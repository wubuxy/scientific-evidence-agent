"""Public contracts; source locations survive changes of retrieval backend."""
from typing import Any, Generic, Literal, Optional, TypeVar
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class WorkspaceCreate(Contract):
    title: str = Field(min_length=1, max_length=200)
    research_question: str = Field(default="", max_length=4000)
    field: str = Field(default="AI/NLP", max_length=100)


class SessionCreate(Contract):
    title: str = Field(default="科研调研", min_length=1, max_length=200)


class MessageCreate(Contract):
    content: str = Field(min_length=1, max_length=20000)


class SourceBlock(Contract):
    model_config = ConfigDict(str_strip_whitespace=False)
    text: str = Field(min_length=1, max_length=50000)
    page: Optional[int] = Field(default=None, ge=1)
    start_seconds: Optional[float] = Field(default=None, ge=0)
    end_seconds: Optional[float] = Field(default=None, ge=0)
    block_type: Literal["text", "table", "transcript", "ocr", "visual_description"] = "text"

    @model_validator(mode="after")
    def valid_time(self):
        if not self.text.strip():
            raise ValueError("资料文本不能为空白")
        if (self.start_seconds is None) != (self.end_seconds is None):
            raise ValueError("时间定位必须同时提供 start_seconds 和 end_seconds")
        if self.start_seconds is not None and self.end_seconds < self.start_seconds:
            raise ValueError("end_seconds 不得早于 start_seconds")
        return self


class SourceCreate(Contract):
    title: str = Field(min_length=1, max_length=200)
    kind: Literal["paper", "document", "slides", "transcript", "web", "abstract"] = "document"
    uri: Optional[str] = Field(default=None, max_length=2000)
    version: str = Field(default="1", max_length=100)
    blocks: list[SourceBlock] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def bounded_source(self):
        if sum(len(b.text) for b in self.blocks) > 500000:
            raise ValueError("单份资料的文本总长度不得超过 500000 字符")
        lines = [line for block in self.blocks for line in block.text.splitlines()]
        if len(lines) > 10000 or any(len(line) > 16000 for line in lines):
            raise ValueError("单份资料最多 10000 行，单行最多 16000 字符；请先整理段落")
        return self


class RetrievalRequest(Contract):
    query: str = Field(min_length=1, max_length=500)
    strategy: Literal["grep", "rag", "combined"] = "grep"
    limit: int = Field(default=10, ge=1, le=50)
    source_ids: list[str] = Field(default_factory=list, max_length=100)
    ignore_case: bool = True
    regex: bool = False
    start_line: int = Field(default=1, ge=1, le=10000, description="Grep 搜索范围起始行，按每份资料计")
    end_line: Optional[int] = Field(default=None, ge=1, le=10000, description="Grep 搜索范围结束行")
    offset: int = Field(default=0, ge=0, le=1000000, description="Grep 匹配窗口偏移；使用返回的 next_offset 继续搜索")
    context_lines: int = Field(default=0, ge=0, le=15)

    @model_validator(mode="after")
    def bounded_lines(self):
        if self.end_line is not None and self.end_line < self.start_line:
            raise ValueError("end_line 不得小于 start_line")
        if self.strategy == "rag" and (self.start_line != 1 or self.end_line is not None or self.offset):
            raise ValueError("行范围和 offset 仅用于 Grep；RAG 定位后请用 sources_read 阅读")
        return self


class GrepToolRequest(RetrievalRequest):
    # Agents are trained to issue grep patterns; the public search API stays literal by default.
    regex: bool = True


class SearchRequest(RetrievalRequest):
    grep_query: Optional[str] = Field(default=None, min_length=1, max_length=500,
        description="关键词正则，如 train|fine.tun；语义查询仍使用 query")
    limit: int = Field(default=5, ge=1, le=20)
    balance_sources: bool = Field(default=False, description="显式跨多篇广泛探索时启用文档均分；默认按相关性排序")


class ResearchPlanRequest(Contract):
    source_ids: list[str] = Field(min_length=1, max_length=12, description="本次用户要求必读的全部资料 ID")
    fields: list[str] = Field(min_length=1, max_length=8, description="用户要求核实的字段，简短列出")

    @model_validator(mode="after")
    def distinct_plan(self):
        self.source_ids = list(dict.fromkeys(self.source_ids))
        self.fields = list(dict.fromkeys(f.strip() for f in self.fields if f.strip()))
        if not self.fields or any(len(f) > 100 for f in self.fields):
            raise ValueError("计划字段需为 1–100 字符的非空文本")
        return self


class CollectRequest(Contract):
    query: str = Field(min_length=1, max_length=500)
    english_queries: list[str] = Field(default_factory=list, max_length=3)
    limit: int = Field(default=3, ge=1, le=5)
    index_rag: bool = False

    @model_validator(mode="after")
    def bounded_queries(self):
        if any(not q.strip() or len(q) > 500 for q in self.english_queries):
            raise ValueError("每个英文查询需为 1–500 字符")
        return self


class ReadRequest(Contract):
    source_id: str = Field(min_length=1, max_length=64)
    start_line: int = Field(default=1, ge=1)
    line_count: int = Field(default=30, ge=1, le=100)


class DiscoveryRequest(Contract):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=20)


class DatabaseRequest(Contract):
    sql: str = Field(min_length=1, max_length=8000)
    limit: int = Field(default=100, ge=1, le=500)


class ToolRequest(Contract):
    name: str = Field(min_length=1, max_length=80)
    arguments: dict[str, Any] = Field(default_factory=dict)


class Claim(Contract):
    text: str = Field(min_length=1, max_length=4000)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    qualification: str = Field(default="", max_length=2000)


class ComparisonRow(Contract):
    source_id: str
    method: str
    dataset: str = "本次未核实"
    model: str = "本次未核实"
    metric: str = "本次未核实"
    result: str = "本次未核实"
    conditions: str = "本次未核实"
    comparability: Literal["comparable", "qualified", "unknown"] = "unknown"
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    attributes: dict[str, str] = Field(default_factory=dict, max_length=12,
        description="用户要求的自定义对照字段，如论文数量、训练要求、检索时机；使用问题语言的列名，值应有本行证据支持")

    @model_validator(mode="after")
    def bounded_attributes(self):
        if any(not k.strip() or len(k) > 100 or "\n" in k or "\r" in k or len(v) > 2000 for k, v in self.attributes.items()):
            raise ValueError("对照字段名需 1–100 字符，单元格最多 2000 字符")
        return self


class Report(Contract):
    title: str = Field(min_length=1, max_length=200)
    claims: list[Claim] = Field(default_factory=list, max_length=100)
    comparison_rows: list[ComparisonRow] = Field(default_factory=list, max_length=100)
    limitations: list[str] = Field(min_length=1, max_length=50)
    verification_status: Literal["needs_human_review"] = "needs_human_review"


class RunCreate(Contract):
    session_id: str
    question: str = Field(min_length=1, max_length=10000)
    retrieval_strategy: Literal["grep", "rag", "combined"] = "grep"
    allow_network: bool = False
    memory_mode: Literal["evidence", "legacy"] = "legacy"
    max_steps: int = Field(default=10, ge=1, le=30)
    max_tokens: int = Field(default=20000, ge=2000, le=100000)
    max_seconds: int = Field(default=180, ge=10, le=900)


# Response models make the contract useful before the frontend is rebuilt.
class WorkspaceView(WorkspaceCreate):
    id: str
    created_at: str


class SessionView(SessionCreate):
    id: str
    workspace_id: str
    created_at: str


class MessageView(Contract):
    id: str
    workspace_id: str
    created_at: str
    role: Literal["user", "assistant"]
    content: str
    run_id: Optional[str] = None


class SourceView(Contract):
    id: str
    workspace_id: str
    title: str
    kind: str
    uri: Optional[str]
    version: str
    source_hash: str
    line_count: int
    rag_indexed: bool
    created_at: str


class Location(Contract):
    block_index: int
    page: Optional[int]
    start_seconds: Optional[float]
    end_seconds: Optional[float]
    block_type: str


class EvidenceView(Contract):
    model_config = ConfigDict(str_strip_whitespace=False)
    id: str
    workspace_id: str
    created_at: str
    source_id: str
    source_hash: str
    title: str
    uri: Optional[str]
    quote: str
    start_line: int
    end_line: int
    locations: list[Location]


class RetrievalHit(EvidenceView):
    method: Literal["grep", "rag"]
    score: Optional[float]
    backend_ranks: dict[str, int] = Field(default_factory=dict)
    match_lines: list[int] = Field(default_factory=list)


class RetrievalView(Contract):
    hits: list[RetrievalHit]
    warnings: list[str]
    partial: bool
    truncated: bool
    backends: list[str]
    merge: str
    backend_runs: list[dict[str, Any]] = Field(default_factory=list)
    strategy: str = "grep"
    coverage: str = ""
    grep_next_offset: Optional[int] = None
    grep_total_windows: Optional[int] = None


class ToolCallView(Contract):
    id: str
    workspace_id: str
    created_at: str
    name: str
    arguments: dict[str, Any]
    run_id: Optional[str]
    status: Literal["succeeded", "failed"]
    data: Optional[dict[str, Any]]
    error: Optional[str]
    elapsed_ms: int


class RunView(RunCreate):
    id: str
    workspace_id: str
    created_at: str
    updated_at: Optional[str] = None
    status: Literal["queued", "running", "completed", "failed", "cancelled", "interrupted"]
    model: str
    tokens_used: int
    tokens_estimated: bool
    step: int
    report: Optional[Report]
    error: Optional[str]
    markdown: Optional[str] = None
    source_coverage: dict[str, Any] = Field(default_factory=dict)


class ReportView(Contract):
    report: Report
    markdown: str


T = TypeVar("T")


class Items(Contract, Generic[T]):
    items: list[T]
