"""Observable calls, explicit usage gaps, and conservative per-request budgets."""

import json
import re
from decimal import Decimal

from code_agent.telemetry.usage import UsageTotals


class BudgetExceeded(RuntimeError):
    pass


class ModelFailure(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class CostLedger:
    def __init__(self, *, limit_cny, input_per_million, output_per_million, cached_per_million=0, max_calls=40, max_output_tokens=4096):
        self.limit = Decimal(str(limit_cny))
        self.input = Decimal(str(input_per_million))
        self.output = Decimal(str(output_per_million))
        self.cached = Decimal(str(cached_per_million))
        if any(not v.is_finite() or v < 0 for v in (self.limit, self.input, self.output, self.cached)) or self.limit <= 0:
            raise ValueError("Prices and budget must be finite and nonnegative")
        if self.cached > self.input or max_calls < 1 or max_output_tokens < 1:
            raise ValueError("Invalid budget settings")
        self.max_calls, self.max_output_tokens = max_calls, max_output_tokens
        self.calls = 0
        self.charged = Decimal(0)
        self.missing_usage_calls = 0
        self.overrun = False

    def reserve(self, messages, tools):
        # One token per UTF-8 byte plus a deliberately large protocol margin.
        # This is an engineering bound, not the provider's billing hard limit.
        size = len(json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False).encode())
        if size > 100000 or self.calls >= self.max_calls or self.overrun:
            raise BudgetExceeded("request_limit")
        reserve = ((Decimal(size + 8192) * self.input) + Decimal(self.max_output_tokens) * self.output) / 1_000_000
        if self.charged + reserve > self.limit:
            raise BudgetExceeded("cost_limit")
        self.calls += 1
        self.charged += reserve
        return reserve

    def settle(self, reserve, usage):
        if not usage or "prompt_tokens" not in usage or "completion_tokens" not in usage:
            self.missing_usage_calls += 1
            return
        prompt, output = int(usage["prompt_tokens"]), int(usage["completion_tokens"])
        cached = int(usage.get("cached_prompt_tokens") or 0)
        if min(prompt, output, cached) < 0 or cached > prompt:
            self.overrun = True
            raise BudgetExceeded("invalid_usage")
        actual = (Decimal(prompt - cached) * self.input + Decimal(cached) * self.cached + Decimal(output) * self.output) / 1_000_000
        self.charged += actual - reserve
        if actual > reserve:
            self.overrun = True
            raise BudgetExceeded("usage_exceeded_reservation")


class ObservedClient:
    def __init__(self, client, ledger=None):
        self.client, self.ledger = client, ledger
        self.model = client.model
        self.calls = 0
        self.usage_calls = 0
        self.usage = UsageTotals()

    async def chat_stream(self, messages, tools=None, **kwargs):
        reserve = self.ledger.reserve(messages, tools) if self.ledger else None
        self.calls += 1
        usage = None
        settled = False
        try:
            async for event in self.client.chat_stream(messages, tools, **kwargs):
                if event.kind == "error":
                    code = re.search(r"\b[45]\d\d\b", event.text or "")
                    raise ModelFailure("http_" + code[0] if code else "stream_error")
                if event.kind == "turn_end" and event.usage:
                    usage = event.usage
                    self.usage_calls += 1
                    self.usage.accumulate(usage, self.model)
                if event.kind == "turn_end" and self.ledger:
                    settled = True
                    self.ledger.settle(reserve, usage)
                yield event
        finally:
            if self.ledger and not settled:
                self.ledger.settle(reserve, usage)

    async def close(self):
        await self.client.close()

    def complete_usage(self):
        return self.usage.to_dict() if self.calls and self.calls == self.usage_calls else None
