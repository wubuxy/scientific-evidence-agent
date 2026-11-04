"""Bounded contracts for data experiments and aligned literature translation."""
from typing import Any, Literal, Optional
from pydantic import Field, model_validator
from .schemas import Contract


class ColumnView(Contract):
    name: str
    kind: Literal["numeric", "categorical"]
    missing: int
    unique: int


class DatasetView(Contract):
    id: str
    workspace_id: str
    created_at: str
    title: str
    filename: str
    row_count: int
    columns: list[ColumnView]
    preview: list[list[Any]]
    data_hash: str
    original_hash: str
    source_id: Optional[str] = None
    page: Optional[int] = None
    note: str = ""
    warnings: list[str] = Field(default_factory=list)


class AnalysisCreate(Contract):
    dataset_id: str
    method: Literal["descriptive", "welch_t", "paired_t", "chi_square", "correlation", "linear_regression", "hotelling_t2", "lstm"]
    columns: list[str] = Field(min_length=1, max_length=20)
    alpha: float = Field(default=0.05, ge=0.001, le=0.2)
    weight_column: Optional[str] = None
    continuity_correction: bool = True
    time_column: Optional[str] = None
    window: int = Field(default=12, ge=3, le=128)
    epochs: int = Field(default=40, ge=5, le=150)
    hidden_size: int = Field(default=24, ge=8, le=64)
    train_fraction: float = Field(default=0.8, ge=0.6, le=0.9)
    horizon: int = Field(default=12, ge=1, le=100)
    seed: int = Field(default=42, ge=0, le=2147483647)

    @model_validator(mode="after")
    def valid_columns(self):
        if len(set(self.columns)) != len(self.columns):
            raise ValueError("分析列不能重复")
        count = 2 if self.method in {"welch_t", "paired_t", "chi_square", "correlation", "linear_regression"} else None
        if count and len(self.columns) != count:
            raise ValueError("该分析方法需要选择两列")
        if self.method == "lstm" and len(self.columns) != 1:
            raise ValueError("首版 LSTM 支持单变量序列，请选择一个目标列")
        if self.weight_column and self.method != "chi_square":
            raise ValueError("频数权重列仅适用于卡方检验")
        return self


class AnalysisView(Contract):
    id: str
    workspace_id: str
    created_at: str
    updated_at: Optional[str] = None
    dataset_id: str
    dataset_hash: str
    method: str
    parameters: dict[str, Any]
    status: Literal["queued", "running", "completed", "failed", "cancelled", "interrupted"]
    progress: dict[str, Any]
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    published_source_id: Optional[str] = None


class TranslationCreate(Contract):
    source_id: Optional[str] = None
    text: Optional[str] = Field(default=None, max_length=80000)
    start_line: int = Field(default=1, ge=1, le=10000)
    end_line: Optional[int] = Field(default=None, ge=1, le=10000)
    target_language: Literal["auto", "zh-CN", "en"] = "auto"
    style: Literal["direct", "academic"] = "direct"
    glossary: dict[str, str] = Field(default_factory=dict, max_length=50)
    max_tokens: int = Field(default=60000, ge=4000, le=200000)

    @model_validator(mode="after")
    def content(self):
        if bool(self.source_id) == bool(self.text and self.text.strip()):
            raise ValueError("请选择一份文献，或输入待翻译文本，不能同时提供")
        if self.end_line is not None and self.end_line < self.start_line:
            raise ValueError("结束行不能早于起始行")
        if any(not k.strip() or not v.strip() or len(k) > 100 or len(v) > 200 for k, v in self.glossary.items()):
            raise ValueError("术语表的原文与译法不能为空，长度分别不得超过 100 / 200 字符")
        if sum(len(k) + len(v) for k, v in self.glossary.items()) > 3000:
            raise ValueError("术语表总长度不得超过 3000 字符")
        return self


class TranslationView(Contract):
    id: str
    workspace_id: str
    created_at: str
    updated_at: Optional[str] = None
    title: str
    source_id: Optional[str]
    source_hash: str
    request_hash: str
    target_language: str
    # Historical records used the original academic prompt.
    style: Literal["direct", "academic"] = "academic"
    glossary: dict[str, str]
    max_tokens: int
    status: Literal["queued", "running", "completed", "failed", "cancelled", "interrupted"]
    progress: dict[str, Any]
    tokens_used: int
    tokens_estimated: bool = False
    model: str
    segments: list[dict[str, Any]]
    error: Optional[str] = None
