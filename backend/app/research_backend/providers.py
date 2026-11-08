"""External calls are lazy, bounded and replaceable in offline tests."""
import asyncio
import time
import re
import shlex
import copy
import json
from xml.etree import ElementTree as ET
import httpx
from .store import Unavailable


def arxiv_expression(query):
    """Explicit conjunction; unfielded natural text has ambiguous API semantics."""
    if re.search(r"\b(?:all|ti|au|abs|cat|id):|\b(?:AND|OR|ANDNOT)\b", query):
        return query
    try:
        terms = shlex.split(query)
    except ValueError as exc:
        raise ValueError("arXiv 查询引号不完整") from exc
    return " AND ".join('all:"' + term.replace('"', '') + '"' for term in terms)


class Discovery:
    def __init__(self, settings):
        self.settings = settings
        self._arxiv_lock = asyncio.Lock()
        self._last_arxiv = 0.0
        self._cache = {}

    async def papers(self, query, limit):
        cache_key = (query, limit)
        async with self._arxiv_lock:
            cached = self._cache.get(cache_key)
            if cached and time.monotonic() - cached[0] < 300:
                return cached[1]
            await asyncio.sleep(max(0, 3 - (time.monotonic() - self._last_arxiv)))
            try:
                async with httpx.AsyncClient(timeout=self.settings.request_timeout) as client:
                    self._last_arxiv = time.monotonic()
                    response = await client.get("https://export.arxiv.org/api/query", params={
                        "search_query": arxiv_expression(query), "start": 0, "max_results": limit,
                        "sortBy": "relevance"})
                    response.raise_for_status()
                if len(response.content) > 2_000_000:
                    raise Unavailable("文献搜索返回过大")
                root = ET.fromstring(response.content)
            except (httpx.HTTPError, ET.ParseError) as exc:
                raise Unavailable("arXiv 查询失败，请稍后重试") from exc
            ns = {"a": "http://www.w3.org/2005/Atom"}
            papers = []
            for entry in root.findall("a:entry", ns):
                papers.append(dict(title=" ".join(entry.findtext("a:title", "", ns).split()),
                    uri=entry.findtext("a:id", "", ns),
                    abstract=" ".join(entry.findtext("a:summary", "", ns).split()),
                    authors=[a.findtext("a:name", "", ns) for a in entry.findall("a:author", ns)],
                    published_at=entry.findtext("a:published", "", ns),
                    updated_at=entry.findtext("a:updated", "", ns)))
            result = dict(provider="arxiv", results=papers, evidence_level="metadata_and_abstract",
                          note="搜索结果不是全文证据；使用全文结论前请导入并阅读论文")
            if len(self._cache) >= 128:
                self._cache.pop(next(iter(self._cache)))
            self._cache[cache_key] = (time.monotonic(), result)
            return result

    async def web(self, query, limit):
        configured = {"serper": self.settings.serper_api_key, "bocha": self.settings.bocha_api_key}
        provider = self.settings.web_provider
        if provider not in {"auto", "serper", "bocha"}:
            raise Unavailable("RESEARCH_WEB_PROVIDER 必须为 auto/serper/bocha")
        candidates = [name for name, key in configured.items() if key and (provider == "auto" or provider == name)]
        if not candidates:
            raise Unavailable("Web 搜索尚未配置可用的 SERPER_API_KEY 或 BOCHA_API_KEY")
        failures = []
        for name in candidates:
            try:
                async with httpx.AsyncClient(timeout=self.settings.request_timeout) as client:
                    if name == "serper":
                        response = await client.post("https://google.serper.dev/search",
                            headers={"X-API-KEY": configured[name]}, json={"q": query, "num": limit})
                    else:
                        response = await client.post("https://api.bochaai.com/v1/web-search",
                            headers={"Authorization": "Bearer " + configured[name]},
                            json={"query": query, "count": limit, "summary": True})
                    response.raise_for_status()
                    payload = response.json()
                if name == "serper":
                    records = payload.get("organic", [])
                    results = [dict(title=r.get("title", ""), uri=r.get("link", ""),
                                    snippet=r.get("snippet", "")) for r in records[:limit]]
                else:
                    if payload.get("code", 200) != 200:
                        raise ValueError("provider rejected request")
                    records = payload.get("data", {}).get("webPages", {}).get("value", [])
                    results = [dict(title=r.get("name", ""), uri=r.get("url", ""),
                                    snippet=r.get("summary") or r.get("snippet", "")) for r in records[:limit]]
                return dict(provider=name, results=results, evidence_level="search_snippet", warnings=failures)
            except httpx.HTTPStatusError as exc:
                failures.append(f"{name}: HTTP {exc.response.status_code}")
            except (httpx.HTTPError, ValueError, TypeError):
                failures.append(f"{name}: 查询失败")
        raise Unavailable("Web 搜索不可用（" + "; ".join(failures) + "）")


def inline_tool_schemas(definitions):
    """Inline Pydantic's local $refs for providers with limited schema support."""
    result = copy.deepcopy(definitions)
    for tool in result:
        if "parameters" not in tool["function"]:
            continue
        schema = tool["function"]["parameters"]
        definitions_by_name = schema.get("$defs", {})

        def expand(node, ancestors=()):
            if isinstance(node, list):
                return [expand(value, ancestors) for value in node]
            if not isinstance(node, dict):
                return node
            ref = node.get("$ref")
            if ref and ref.startswith("#/$defs/"):
                name = ref.rsplit("/", 1)[-1]
                if name in ancestors:
                    raise ValueError("Recursive tool schemas are not supported")
                return expand(dict(definitions_by_name[name], **{k: v for k, v in node.items() if k != "$ref"}), ancestors + (name,))
            return {key: expand(value, ancestors) for key, value in node.items() if key != "$defs"}

        tool["function"]["parameters"] = expand(schema)
    return result


def provider_tools(definitions):
    """The exact public schema projection used by both budgeting and transport.

    Pydantic's display titles add no validation constraints. Strip annotations,
    never property names (a report still has its required `title` property).
    Keep descriptions, defaults and every validation keyword.
    """
    def compact(node):
        if isinstance(node, list):
            return [compact(value) for value in node]
        if not isinstance(node, dict):
            return node
        result = {}
        for key, value in node.items():
            if key in {"title", "examples"}:
                continue
            if key in {"properties", "$defs", "patternProperties", "dependentSchemas"}:
                result[key] = {name: compact(schema) for name, schema in value.items()}
            elif key in {"items", "prefixItems", "additionalProperties", "allOf", "anyOf", "oneOf",
                         "not", "contains", "if", "then", "else", "unevaluatedProperties", "unevaluatedItems"}:
                result[key] = compact(value)
            else:
                # Defaults/enums/consts are data, not subschemas. A literal
                # object's `title` key must not disappear from their values.
                result[key] = value
        return result

    result = inline_tool_schemas(definitions)
    for tool in result:
        tool["function"]["parameters"] = compact(tool["function"].get("parameters", {}))
    return result


def decode_tool_arguments(call, definitions):
    """Decode valid JSON containers emitted as strings, then let contracts validate.

    Some compatible providers serialize nested arrays/objects twice. This only
    unwraps schema-declared containers; strings, numbers and malformed JSON are
    never repaired or inferred. Callers retain the original arguments for audit.
    """
    arguments = json.loads(call["function"]["arguments"])
    schema = next((tool["function"].get("parameters", {}) for tool in inline_tool_schemas(definitions)
                   if tool["function"]["name"] == call["function"]["name"]), {})
    decoded = []

    def visit(value, contract, path):
        kind = contract.get("type")
        expected = {"array": list, "object": dict}.get(kind)
        if expected and isinstance(value, str):
            try:
                candidate = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                candidate = None
            if isinstance(candidate, expected):
                value = candidate
                decoded.append(path)
            elif kind == "array" and contract.get("items", {}).get("type") == "object" and isinstance(candidate, dict):
                value = [candidate]
                decoded.append(path + ":singleton_object")
        if isinstance(value, dict) and kind == "object":
            return {key: visit(item, contract.get("properties", {}).get(key, {}), path + "." + key)
                    for key, item in value.items()}
        if isinstance(value, list) and kind == "array":
            return [visit(item, contract.get("items", {}), path + f"[{index}]") for index, item in enumerate(value)]
        return value

    return visit(arguments, schema, "$"), decoded


class ChatModel:
    def __init__(self, settings):
        self.settings = settings

    async def complete(self, messages, tools, max_tokens):
        if not self.settings.llm_api_key:
            raise Unavailable("尚未配置 RESEARCH_LLM_API_KEY 或 DASHSCOPE_API_KEY")
        from openai import AsyncOpenAI
        async with AsyncOpenAI(api_key=self.settings.llm_api_key, base_url=self.settings.llm_base_url,
                               timeout=self.settings.llm_timeout, max_retries=1) as client:
            forced_name = tools[0]["function"]["name"] if len(tools) == 1 else None
            response = await client.chat.completions.create(model=self.settings.llm_model,
                messages=messages, tools=provider_tools(tools), max_tokens=max_tokens, temperature=0.1,
                **({"extra_body": self.settings.llm_extra_body} if self.settings.llm_extra_body else {}),
                tool_choice={"type": "function", "function": {"name": forced_name}} if forced_name else "auto")
        if not response.choices:
            raise Unavailable("模型没有返回结果")
        message = response.choices[0].message
        return dict(message=message.model_dump(exclude_none=True),
                    response_model=getattr(response, "model", None),
                    tokens=response.usage.total_tokens if response.usage else None,
                    prompt_tokens=response.usage.prompt_tokens if response.usage else None,
                    completion_tokens=getattr(response.usage, "completion_tokens", None),
                    finish_reason=getattr(response.choices[0], "finish_reason", None))
