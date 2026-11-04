"""Provider request compatibility for an external Qwen3 inference server."""
from types import SimpleNamespace

import pytest

from research_backend.config import Settings
from research_backend.providers import ChatModel
from research_backend.translation import Translator


def test_model_request_extensions_are_loaded_without_modifying_model_id(monkeypatch):
    monkeypatch.setenv("RESEARCH_LLM_MODEL", "Qwen/Qwen3-8B")
    monkeypatch.setenv("RESEARCH_LLM_EXTRA_BODY", '{"chat_template_kwargs":{"enable_thinking":false}}')
    settings = Settings.from_env()
    assert settings.llm_model == "Qwen/Qwen3-8B"
    assert settings.llm_extra_body == {"chat_template_kwargs": {"enable_thinking": False}}


@pytest.mark.parametrize("value", ["not-json", "[]", "null", "false"])
def test_model_request_extensions_reject_non_objects(monkeypatch, value):
    monkeypatch.setenv("RESEARCH_LLM_EXTRA_BODY", value)
    with pytest.raises(ValueError, match="RESEARCH_LLM_EXTRA_BODY"):
        Settings.from_env()


@pytest.mark.parametrize("extensions", [{}, {"chat_template_kwargs": {"enable_thinking": False}}])
async def test_research_and_translation_share_model_transport(monkeypatch, extensions):
    calls = []

    class Client:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def create(self, **kwargs):
            calls.append(kwargs)
            message = SimpleNamespace(content="translated", model_dump=lambda **kw: {"role": "assistant", "tool_calls": []})
            return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")],
                usage=SimpleNamespace(total_tokens=12, prompt_tokens=8, completion_tokens=4))

    monkeypatch.setattr("openai.AsyncOpenAI", Client)
    settings = Settings(llm_api_key="test-fixture", llm_model="Qwen/Qwen3-8B", llm_extra_body=extensions)
    tools = [{"type": "function", "function": {"name": "finish_report"}}]
    result = await ChatModel(settings).complete([], tools, 1000)
    translation = await Translator(settings).translate("text", "en", {}, 1000)
    assert result["tokens"] == translation["tokens"] == 12
    assert calls[0]["tool_choice"] == {"type": "function", "function": {"name": "finish_report"}}
    for request in calls:
        assert request["model"] == "Qwen/Qwen3-8B"
        assert request.get("extra_body", {}) == extensions
        assert ("extra_body" in request) == bool(extensions)
