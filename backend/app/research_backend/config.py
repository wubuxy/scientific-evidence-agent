"""Configuration is read at application creation; importing this module has no I/O."""
import os
import json
from dataclasses import dataclass, field
from pathlib import Path


def load_environment():
    """Use one config precedence for ASGI, CLI and evaluation, from any cwd."""
    from dotenv import load_dotenv
    backend = Path(__file__).resolve().parents[2]
    load_dotenv(backend / ".env.research")
    load_dotenv(backend / ".env")


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parents[2] / ".research-data")
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    llm_api_key: str = ""
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen-plus"
    llm_extra_body: dict = field(default_factory=dict)
    serper_api_key: str = ""
    bocha_api_key: str = ""
    web_provider: str = "auto"
    rag_enabled: bool = False
    rag_collection: str = "research_sources_v1"
    rag_backend: str = "local"
    embedding_model: str = "text-embedding-v4"
    rg_binary: str = ""
    request_timeout: float = 30.0
    llm_timeout: float = 90.0

    @classmethod
    def from_env(cls):
        try:
            extra_body = json.loads(os.getenv("RESEARCH_LLM_EXTRA_BODY", "{}"))
        except json.JSONDecodeError:
            raise ValueError("RESEARCH_LLM_EXTRA_BODY 必须为 JSON 对象") from None
        if not isinstance(extra_body, dict):
            raise ValueError("RESEARCH_LLM_EXTRA_BODY 必须为 JSON 对象")
        kwargs = dict(
            jwt_secret=os.getenv("JWT_SECRET_KEY", ""),
            llm_api_key=os.getenv("RESEARCH_LLM_API_KEY") or os.getenv("DASHSCOPE_API_KEY", ""),
            llm_base_url=os.getenv("RESEARCH_LLM_BASE_URL") or os.getenv("DASHSCOPE_BASE_URL")
            or "https://dashscope.aliyuncs.com/compatible-mode/v1",
            llm_model=os.getenv("RESEARCH_LLM_MODEL", "qwen-plus"),
            llm_extra_body=extra_body,
            serper_api_key=os.getenv("SERPER_API_KEY", ""),
            bocha_api_key=os.getenv("BOCHA_API_KEY", ""),
            web_provider=os.getenv("RESEARCH_WEB_PROVIDER", "auto"),
            rag_enabled=os.getenv("RESEARCH_RAG_ENABLED", "false").lower() == "true",
            rag_collection=os.getenv("RESEARCH_RAG_COLLECTION", "research_sources_v1"),
            rag_backend=os.getenv("RESEARCH_RAG_BACKEND", "local"),
            embedding_model=os.getenv("RESEARCH_EMBEDDING_MODEL", "text-embedding-v4"),
            rg_binary=os.getenv("RESEARCH_RG_BINARY", ""),
            llm_timeout=float(os.getenv("RESEARCH_LLM_TIMEOUT_SECONDS", "90")),
        )
        if os.getenv("RESEARCH_DATA_DIR"):
            path = Path(os.environ["RESEARCH_DATA_DIR"])
            kwargs["data_dir"] = path if path.is_absolute() else Path(__file__).resolve().parents[2] / path
        return cls(**kwargs)
