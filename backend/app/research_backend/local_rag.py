"""Small-corpus BM25 + dense RRF, without legacy services or a vector database.

Canonical source text and line addresses are shared with Grep. This is a new
baseline, not a reproduction of the historical Milvus implementation.
"""
import hashlib
import json
import math
import re
import threading
from collections import Counter
from pathlib import Path
import numpy as np
from .store import Unavailable


def tokenize(text):
    return re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text.casefold())


def bm25_scores(query, documents):
    counts = [Counter(tokenize(text)) for text in documents]
    lengths = [sum(c.values()) for c in counts]
    average = sum(lengths) / max(1, len(lengths)) or 1
    scores = [0.0] * len(counts)
    for term in set(tokenize(query)):
        df = sum(term in count for count in counts)
        idf = math.log(1 + (len(counts) - df + 0.5) / (df + 0.5))
        for i, count in enumerate(counts):
            tf = count[term]
            scores[i] += idf * tf * 2.5 / (tf + 1.5 * (0.25 + 0.75 * lengths[i] / average))
    return scores


class CachedEmbedder:
    def __init__(self, settings, cache_dir):
        self.settings = settings
        self.cache_dir = Path(cache_dir)
        self.model = settings.embedding_model
        self.calls = 0
        self.tokens = 0
        self._lock = threading.Lock()

    def __call__(self, texts):
        with self._lock:
            return self._embed(texts)

    def _embed(self, texts):
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        namespace = self.settings.llm_base_url + ":" + self.model
        paths = [self.cache_dir / (hashlib.sha256((namespace + "\0" + text).encode()).hexdigest() + ".json") for text in texts]
        vectors = [json.loads(p.read_text("utf-8")) if p.exists() else None for p in paths]
        missing = [i for i, vector in enumerate(vectors) if vector is None]
        if missing:
            from openai import OpenAI
            if not self.settings.llm_api_key:
                raise Unavailable("Dense retrieval requires the configured embedding API key")
            try:
                with OpenAI(api_key=self.settings.llm_api_key, base_url=self.settings.llm_base_url,
                            timeout=self.settings.llm_timeout, max_retries=1) as client:
                    for start in range(0, len(missing), 10):
                        indices = missing[start:start + 10]
                        response = client.embeddings.create(model=self.model, input=[texts[i] for i in indices])
                        ordered = sorted(response.data, key=lambda item: item.index)
                        if [item.index for item in ordered] != list(range(len(indices))):
                            raise ValueError("Incomplete embedding response")
                        self.calls += 1
                        self.tokens += getattr(response.usage, "total_tokens", 0) or 0
                        for i, item in zip(indices, ordered):
                            vector = item.embedding
                            if not vector or not all(math.isfinite(v) for v in vector):
                                raise ValueError("Invalid embedding")
                            vectors[i] = vector
                            paths[i].write_text(json.dumps(vector), encoding="utf-8")
            except Exception as exc:
                raise Unavailable(f"Embedding request failed ({type(exc).__name__}); no lexical-only fallback") from None
        return vectors


class LocalHybridRag:
    def __init__(self, settings, store, embedder=None):
        self.settings, self.store = settings, store
        self.embedder = embedder or CachedEmbedder(settings, store.root / "embedding-cache")
        self._indexes = {}

    def index(self, owner, workspace_id, source_id):
        if not self.settings.rag_enabled:
            raise Unavailable("RAG is disabled")
        source = self.store.get(owner, workspace_id, "source", source_id)
        lines = self.store.source_text(owner, workspace_id, source_id).splitlines()
        path = self.store.root / workspace_id / source_id / "hybrid-index.json"
        if path.exists():
            data = json.loads(path.read_text("utf-8"))
            if (data.get("source_hash") == source["source_hash"] and data.get("model") == self.settings.embedding_model
                    and data.get("provider") == self.settings.llm_base_url):
                self._indexes[source_id] = data["chunks"]
                self.store.update(owner, workspace_id, "source", source_id, rag_indexed=True)
                return dict(indexed=True, already_indexed=True, chunk_count=len(data["chunks"]))
        spans, start, size = [], 1, 0
        for number, line in enumerate(lines, 1):
            if number > start and (size + len(line) > 1500 or number - start >= 30):
                spans.append((start, number - 1))
                start, size = number, 0
            size += len(line) + 1
        spans.append((start, len(lines)))
        quotes = ["\n".join(lines[a-1:b]) for a, b in spans]
        vectors = self.embedder(quotes)
        if len(vectors) != len(quotes):
            raise Unavailable("Incomplete embedding index")
        chunks = [dict(source_id=source_id, start_line=a, end_line=b, quote=quote, vector=vector)
                  for (a, b), quote, vector in zip(spans, quotes, vectors)]
        path.write_text(json.dumps(dict(source_hash=source["source_hash"], model=self.settings.embedding_model, provider=self.settings.llm_base_url,
                                        chunks=chunks)), encoding="utf-8")
        self._indexes[source_id] = chunks
        self.store.update(owner, workspace_id, "source", source_id, rag_indexed=True)
        return dict(source_id=source_id, indexed=True, chunk_count=len(chunks))

    def search(self, owner, workspace_id, request):
        if not self.settings.rag_enabled:
            raise Unavailable("RAG is disabled")
        sources = ([self.store.get(owner, workspace_id, "source", sid) for sid in request.source_ids]
                   if request.source_ids else self.store.list(owner, workspace_id, "source", limit=1001))
        if len(sources) > 1000:
            raise ValueError("Specify source_ids for workspaces larger than 1000 sources")
        chunks = []
        for source in sources:
            sid = source["id"]
            if source["rag_indexed"]:
                if sid not in self._indexes:
                    path = self.store.root / workspace_id / sid / "hybrid-index.json"
                    if not path.exists():
                        raise Unavailable("Source has a legacy index; build its local hybrid index first")
                    data = json.loads(path.read_text("utf-8"))
                    if (data["source_hash"] != source["source_hash"] or data["model"] != self.settings.embedding_model
                            or data.get("provider") != self.settings.llm_base_url):
                        raise Unavailable("Source index configuration changed; rebuild it")
                    self._indexes[sid] = data["chunks"]
                chunks.extend(self._indexes[sid])
        if not chunks:
            raise Unavailable("No local hybrid index in the requested sources")
        vector = np.asarray(self.embedder([request.query])[0], dtype=np.float32)
        matrix = np.asarray([c["vector"] for c in chunks], dtype=np.float32)
        dense = matrix @ vector / np.maximum(np.linalg.norm(matrix, axis=1) * np.linalg.norm(vector), 1e-12)
        lexical = bm25_scores(request.query, [c["quote"] for c in chunks])
        ranks = {}
        depth = min(len(chunks), max(50, request.limit))
        for name, scores in (("bm25", lexical), ("dense", dense)):
            order = sorted(range(len(chunks)), key=lambda i: (-float(scores[i]), i))[:depth]
            for rank, index in enumerate(order, 1):
                if name == "bm25" and scores[index] <= 0:
                    continue
                ranks.setdefault(index, {})[name] = rank
        scores = {i: sum(1 / (60 + rank) for rank in methods.values()) for i, methods in ranks.items()}
        order = sorted(scores, key=lambda i: (-scores[i], i))[:request.limit]
        hits = []
        for index in order:
            chunk = chunks[index]
            evidence = self.store.evidence(owner, workspace_id, chunk["source_id"], chunk["start_line"], chunk["end_line"])
            hits.append(dict(evidence, method="rag", score=scores[index]))
        return dict(hits=hits, truncated=len(chunks) > len(hits), backend="local_bm25_dense_rrf", warnings=[])

    def remove_evaluation_indexes(self, source_ids):
        # Index files belong to the disposable evaluation Store, not a shared DB.
        for source_id in source_ids:
            self._indexes.pop(source_id, None)
