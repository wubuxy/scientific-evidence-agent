import json
import pytest
import httpx
from research_backend.store import Store, Unavailable
from research_backend.schemas import SourceCreate, SearchRequest, CollectRequest, RetrievalRequest
from research_backend.retrieval import GrepSearch, retrieve
from research_backend.memory import EvidenceMemory
from research_backend.discovery import LiteratureCollector, arxiv_id, query_variants


@pytest.fixture
def corpus(tmp_path):
    store = Store(tmp_path); store.initialize()
    wid = store.create_workspace("alice", {"title": "test"})["id"]
    def add(text, uri=None):
        return store.add_source("alice", wid, SourceCreate(title="test", uri=uri, blocks=[{"text": text}]))
    return store, wid, add


def test_grep_balances_sources_instead_of_starving_second_paper(corpus):
    store, wid, add = corpus
    a, b = add("hit\n" * 30), add("hit\nother")
    result = GrepSearch(store).search("alice", wid, RetrievalRequest(query="hit", limit=2, source_ids=[a['id'], b['id']]))
    assert {h['source_id'] for h in result['hits']} == {a['id'], b['id']}
    assert result['truncated']


def test_combined_runs_both_and_fuses_located_context(corpus):
    store, wid, add = corpus
    sid = add("intro\nmodel\ntrained evaluator\noracle conditions\nend")['id']
    class Route:
        def __init__(self, method): self.method, self.calls = method, 0
        def search(self, owner, wid, request):
            self.calls += 1
            ev = store.evidence(owner, wid, sid, 2 if self.method == 'grep' else 3, 3)
            return {'hits': [dict(ev, method=self.method, score=1)], 'backend': self.method}
    grep, rag = Route('grep'), Route('rag'); grep.binary = True
    result = retrieve(store, grep, rag, 'alice', wid, SearchRequest(query='model training', strategy='combined', context_lines=1))
    assert grep.calls == rag.calls == 1
    assert len(result['hits']) == 1
    assert result['hits'][0]['backend_ranks'] == {'grep': 1, 'rag': 1}
    assert 'oracle conditions' in result['hits'][0]['quote']
    assert len(result['backend_runs']) == 2


def test_combined_degradation_is_visible_and_single_route_failure_raises(corpus):
    store, wid, add = corpus; add('hit')
    class Down:
        def search(self, *args): raise Unavailable('offline')
    grep = GrepSearch(store)
    mixed = retrieve(store, grep, Down(), 'alice', wid, SearchRequest(query='hit', strategy='combined'))
    assert mixed['partial'] and mixed['warnings'] and mixed['hits']
    assert mixed['backend_runs'][1]['status'] == 'failed'
    with pytest.raises(Unavailable):
        retrieve(store, grep, Down(), 'alice', wid, SearchRequest(query='hit', strategy='rag'))


def test_memory_deduplicates_and_keeps_verbatim_excerpts_without_mutation():
    memory = EvidenceMemory('Which model uses gold evidence?')
    text = 'irrelevant text\n' * 100 + 'T5 uses gold evidence in oracle experiments.\nNot full document input.\n'
    original = dict(id='ev_test', source_id='paper', start_line=1, end_line=102, quote=text)
    for _ in range(10): memory.observe('sources_read', {}, {'data': original})
    state = memory.state(2000)
    assert len(state['evidence']) == 1
    excerpts = state['evidence'][0]['excerpts']
    assert all(excerpt in text for excerpt in excerpts)
    assert any('oracle experiments' in excerpt for excerpt in excerpts)
    assert original['quote'] == text
    assert len(json.dumps(state['evidence'])) < 2400


@pytest.mark.parametrize('value', ['http://127.0.0.1/pdf/2310.11511', 'https://arxiv.org.evil/pdf/2310.11511',
    'file:///2310.11511', 'https://user:pass@arxiv.org/pdf/2310.11511', 'https://arxiv.org/pdf/../../secret'])
def test_collector_rejects_non_arxiv_originals(value):
    assert arxiv_id(value) is None


async def test_collection_reuses_existing_source_and_records_index_failure(corpus):
    store, wid, add = corpus
    store.add_source('alice', wid, SourceCreate(title='paper', kind='paper', uri='https://arxiv.org/abs/2310.11511v1',
        blocks=[{'text':'native text'}]), b'%PDF-fixture', '.pdf')
    class Search:
        async def papers(self, query, limit):
            return {'results': [{'uri': 'http://arxiv.org/abs/2310.11511v1', 'title': 'paper'}]}
    class Down:
        def index(self, *args): raise Unavailable('index offline')
    collector = LiteratureCollector(store, Search(), Down())
    result = await collector.collect('alice', wid, CollectRequest(query='self rag', limit=1, index_rag=True))
    assert result['status'] == 'partial'
    assert result['papers'][0]['status'] == 'already_present'
    assert result['papers'][0]['index_error'] == 'index offline'
    assert store.list('alice', wid, 'discovery_job')[0]['status'] == 'partial'
    assert len(store.list('alice', wid, 'source')) == 1


async def test_collection_imports_pdf_and_second_run_is_idempotent(corpus, monkeypatch):
    from research_backend import discovery
    store, wid, add = corpus
    class Search:
        async def papers(self, query, limit):
            return {'results': [{'uri': 'https://arxiv.org/abs/2401.15884v1', 'title': 'paper'}]}
    collector = LiteratureCollector(store, Search(), None); downloads = []
    async def download(pid): downloads.append(pid); return b'%PDF-test'
    monkeypatch.setattr(collector, 'download', download)
    monkeypatch.setattr(discovery, 'parse_upload', lambda *args: (SourceCreate(title='paper', kind='paper', blocks=[{'text': 'verified native text', 'page': 1}]), '.pdf'))
    for _ in range(2):
        result = await collector.collect('alice', wid, CollectRequest(query='corrective retrieval', limit=1))
        assert result['status'] == 'completed'
    assert downloads == ['2401.15884v1']
    assert len(store.list('alice', wid, 'source')) == 1


async def test_pdf_redirect_is_validated_before_following(corpus, monkeypatch):
    from research_backend import discovery
    store, wid, _ = corpus; original = httpx.AsyncClient; urls = []
    def handler(request):
        urls.append(str(request.url))
        return httpx.Response(302, headers={'Location': 'http://127.0.0.1/private'})
    monkeypatch.setattr(discovery.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(ValueError, match='重定向'):
        await LiteratureCollector(store, None, None).download('2310.11511v1')
    assert urls == ['https://arxiv.org/pdf/2310.11511v1']


def test_chinese_query_rewrite_is_transparent():
    variants = query_variants('自反思 检索增强生成')
    assert variants[0] == '"self-reflective" "retrieval augmented generation"'
    assert variants[-1] == '自反思 检索增强生成'


def test_arxiv_query_explicitly_conjoins_concepts_and_preserves_phrases():
    from research_backend.providers import arxiv_expression
    assert arxiv_expression('Self-RAG') == 'all:"Self-RAG"'
    assert arxiv_expression('"self-reflective" "retrieval augmented generation"') == 'all:"self-reflective" AND all:"retrieval augmented generation"'
    assert arxiv_expression('ti:retrieval AND au:Asai') == 'ti:retrieval AND au:Asai'


def test_memory_retains_counts_instead_of_repeating_author_headers():
    memory = EvidenceMemory('Compare the paper dataset tasks and data composition')
    quote = ('A Dataset of Information-Seeking Questions\nand Answers Anchored in Research Papers\n'
        'Research Author\nResearch Author\nResearch Author\nInstitution\nname@example.org\nAbstract\n' +
        'Readers of research papers ask questions about the research task.\n' * 15 +
        'We present a dataset of 5,049 questions over 1,585 NLP papers.\n' +
        'This research task helps readers answer questions.\n' * 20)
    memory.observe('sources_search', {'query':'dataset task research papers questions'}, {'data': {'hits':[
        dict(id='ev_count', source_id='paper', start_line=1, end_line=44, quote=quote)]}})
    state = memory.state(1400)
    assert '5,049' in json.dumps(state) and '1,585' in json.dumps(state)


def test_wrong_large_dataset_count_is_rejected_but_approximation_is_allowed(corpus):
    from research_backend.runtime import Runtime
    from research_backend.schemas import Report
    store, wid, add = corpus; sid=add('A dataset of 5,049 questions over 1,585 papers.')['id']
    ev=store.evidence('alice', wid, sid, 1, 1)
    runtime=Runtime(store, None, None)
    for text in ['数据集包含 5,447 个问题。', 'The dataset contains 5,447 questions.']:
        report=Report(title='test', claims=[dict(text=text,evidence_ids=[ev['id']])],limitations=['test']).model_dump()
        with pytest.raises(ValueError, match='5,447'):runtime.validate_report('alice', wid, report)
    for text in ['数据集包含 5,049 个问题。', 'The dataset contains over 5,000 questions.']:
        report=Report(title='test', claims=[dict(text=text,evidence_ids=[ev['id']])],limitations=['test']).model_dump()
        runtime.validate_report('alice', wid, report)


async def test_saved_abstract_is_not_relabelled_as_downloaded_full_text(corpus, monkeypatch):
    from research_backend import discovery
    store,wid,add=corpus
    add('abstract only', 'https://arxiv.org/abs/2401.15884v1')
    class Search:
        async def papers(self,*args):return {'results':[{'uri':'https://arxiv.org/abs/2401.15884v1','title':'paper'}]}
    collector=LiteratureCollector(store,Search(),None); downloads=[]
    async def download(pid):downloads.append(pid);return b'%PDF-test'
    monkeypatch.setattr(collector,'download',download)
    monkeypatch.setattr(discovery,'parse_upload',lambda *a:(SourceCreate(title='paper',kind='paper',blocks=[{'text':'full text'}]),'.pdf'))
    result=await collector.collect('alice',wid,CollectRequest(query='CRAG',limit=1))
    assert downloads==['2401.15884v1'] and result['papers'][0]['status']=='imported'
    assert len(store.list('alice',wid,'source'))==2
