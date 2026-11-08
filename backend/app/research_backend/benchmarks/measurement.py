"""Measured model boundary for evaluations; missing billing is never zero."""
import asyncio
import json
import time

from ..runtime import estimate_prompt_tokens, MeasuredBudgetExceeded, BudgetExceeded
from ..providers import provider_tools


class MeasuredModel:
    def __init__(self, model, ceiling, initial=0, preflight=False, deadline=None):
        self.model, self.ceiling, self.initial = model, ceiling, initial
        self.preflight = preflight
        self.deadline = deadline
        self.calls = []

    @property
    def known_tokens(self):
        return self.initial + sum(c["tokens_used"] or 0 for c in self.calls)

    @property
    def tokens(self):
        return None if any(c["tokens_used"] is None for c in self.calls) else self.known_tokens

    async def complete(self, messages, tools, max_tokens):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise ValueError("Logical policy time budget exhausted")
        if self.tokens is None:
            raise ValueError("Cannot continue after unknown provider usage")
        if self.known_tokens >= self.ceiling:
            raise BudgetExceeded("Measured token budget already exhausted")
        if self.preflight:
            remaining = self.ceiling - self.known_tokens - int(estimate_prompt_tokens(messages, tools) * 1.15)
            if remaining < 256:
                raise ValueError("Insufficient remaining measured budget")
            max_tokens = min(max_tokens, remaining)
        record = dict(tokens_used=None, messages=json.loads(json.dumps(messages)), assistant=None,
            provider_tools=provider_tools(tools), max_output_tokens=max_tokens)
        started = time.monotonic()
        self.calls.append(record)
        try:
            operation = self.model.complete(messages, tools, max_tokens)
            response = await asyncio.wait_for(operation, self.deadline-time.monotonic()) if self.deadline is not None else await operation
            record.update(tokens_used=response.get("tokens"),
                          prompt_tokens=response.get("prompt_tokens"), completion_tokens=response.get("completion_tokens"),
                          response_model=response.get("response_model"),
                          assistant={k:v for k,v in response.get("message", {}).items()
                                     if k in {"role", "content", "tool_calls"}})
            if record["tokens_used"] is None:
                raise ValueError("Evaluation requires measured provider usage")
            value = record["tokens_used"]
            if type(value) is not int or value < 0:
                record["tokens_used"] = None
                raise ValueError("Invalid provider usage")
            if self.known_tokens > self.ceiling:
                raise MeasuredBudgetExceeded(response)
            return response
        finally:
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)
