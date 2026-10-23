# [MEM-04] 2026-08-09: Moved from code_agent/core/session.py → telemetry/usage.py.
# UsageTotals is observability data (real-token accounting), so it belongs with
# the rest of the telemetry/ package (TelemetryLogger, UsageTracker, TraceWriter).
"""Real-token usage accounting (observability).

``UsageTotals`` accumulates API-reported token usage across turns, independent
of the bytes/4 estimate used for compaction gating. The accounting logic lives
here (``accumulate``) so ``Session`` just holds an instance and delegates.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class UsageTotals:
    """Cumulative real token usage reported by the API (not the estimate).

    Fields:
        input_tokens: Total prompt tokens (incl. cached reads/writes).
        output_tokens: Total completion tokens.
        cached_input_tokens: Prompt tokens served from cache.
        reasoning_tokens: Reasoning/completion tokens (model-specific).
        total_tokens: input + output.
        model_calls: Number of API calls that reported usage.
        by_model: Per-model subtotals for multi-provider sessions.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    model_calls: int = 0
    by_model: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serializable summary (model_calls + top-level totals)."""
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": self.total_tokens,
            "model_calls": self.model_calls,
        }

    def accumulate(self, usage: dict | None, model: str = "") -> None:
        """Accumulate a turn's real API usage into this ledger.

        ``usage`` uses the unified shape produced by
        ``code_agent.api.backends.base.normalize_usage``:
        ``prompt_tokens`` / ``completion_tokens`` /
        ``cached_prompt_tokens`` / ``reasoning_tokens``.
        """
        if not usage:
            return
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        cached = int(usage.get("cached_prompt_tokens") or 0)
        reasoning = int(usage.get("reasoning_tokens") or 0)

        self.input_tokens += prompt
        self.output_tokens += completion
        self.cached_input_tokens += cached
        self.reasoning_tokens += reasoning
        self.total_tokens += prompt + completion
        self.model_calls += 1

        if model:
            m = self.by_model.setdefault(model, {
                "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
            })
            m["input_tokens"] += prompt
            m["output_tokens"] += completion
            m["total_tokens"] += prompt + completion
