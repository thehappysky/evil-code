"""Explicit denominators, task-cluster uncertainty, and unknown cost handling."""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict

from code_agent.evals.schema import Trial


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def percentile(values, q):
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * q
    low = math.floor(position)
    return values[low] + (values[math.ceil(position)] - values[low]) * (position - low)


def pass_at_k(n: int, correct: int, k: int) -> float | None:
    if not 0 <= correct <= n or k < 1:
        raise ValueError("Invalid sampling counts")
    if n < k:
        return None
    return 1.0 - math.comb(n - correct, k) / math.comb(n, k) if n - correct >= k else 1.0


def aggregate(trials: list[Trial], task_ids: list[str], repeats: int) -> dict:
    if repeats < 1 or not task_ids or len(set(task_ids)) != len(task_ids):
        raise ValueError("Invalid experiment plan")
    seen = {(t.task_id, t.attempt) for t in trials}
    if len(seen) != len(trials) or any(t.task_id not in task_ids or not 1 <= t.attempt <= repeats for t in trials):
        raise ValueError("Duplicate or unplanned trial")
    groups = defaultdict(list)
    for trial in trials:
        groups[trial.task_id].append(trial)
    planned = len(task_ids) * repeats
    successes = sum(t.status == "success" for t in trials)
    eligible = [t for t in trials if t.status in {"success", "task_failure"}]
    rates = [sum(t.status == "success" for t in groups[key]) / repeats for key in task_ids]
    rng = random.Random(1729)
    samples = [sum(rng.choices(rates, k=len(rates))) / len(rates) for _ in range(2000)] if len(rates) >= 2 else []
    complete = [groups[key] for key in task_ids if len(groups[key]) == repeats and all(t.status in {"success", "task_failure"} for t in groups[key])]
    pass_scores = {}
    for k in (1, 2, 3, 5):
        values = [pass_at_k(len(group), sum(t.status == "success" for t in group), k) for group in complete]
        valid = [v for v in values if v is not None]
        pass_scores[f"pass@{k}"] = sum(valid) / len(valid) if valid else None
    known_usage = [t.usage for t in trials if t.usage is not None]
    known_cost = [t.cost_cny for t in trials if t.cost_cny is not None]
    total_cost = sum(known_cost) if len(known_cost) == len(trials) and trials else None
    by_category = {}
    for category in sorted({t.category for t in trials}):
        subset = [t for t in trials if t.category == category]
        by_category[category] = {"attempts": len(subset), "successes": sum(t.status == "success" for t in subset), "success_rate": ratio(sum(t.status == "success" for t in subset), len(subset))}
    by_difficulty = {}
    for difficulty in sorted({t.difficulty for t in trials}):
        subset = [t for t in trials if t.difficulty == difficulty]
        by_difficulty[difficulty] = {"attempts": len(subset), "success_rate": ratio(sum(t.status == "success" for t in subset), len(subset))}
    return {
        "planned_attempts": planned, "recorded_attempts": len(trials), "not_started": planned - len(trials),
        "successes": successes, "end_to_end_success_rate": ratio(successes, planned),
        "capability_success_rate": ratio(successes, len(eligible)), "capability_denominator": len(eligible),
        "excluded_from_capability": len(trials) - len(eligible), "statuses": dict(Counter(t.status for t in trials)),
        "model_error_codes": dict(Counter(t.error_code or t.error_type for t in trials if t.status == "model_error")),
        "task_macro_success_rate": sum(rates) / len(rates) if rates else None,
        "task_cluster_ci95": [percentile(samples, 0.025), percentile(samples, 0.975)] if samples else None,
        "small_sample_warning": len(task_ids) < 30,
        "complete_tasks_for_pass_at_k": len(complete), **pass_scores,
        "all_repeats_success_rate": ratio(sum(len(groups[key]) == repeats and all(t.status == "success" for t in groups[key]) for key in task_ids), len(task_ids)),
        "duration_p50_seconds": percentile([t.duration_seconds for t in trials], 0.5),
        "duration_p95_seconds": percentile([t.duration_seconds for t in trials], 0.95),
        "success_duration_p50_seconds": percentile([t.duration_seconds for t in trials if t.status == "success"], 0.5),
        "ttft_p50_seconds": percentile([t.ttft_seconds for t in trials if t.ttft_seconds is not None], 0.5),
        "ttft_p95_seconds": percentile([t.ttft_seconds for t in trials if t.ttft_seconds is not None], 0.95),
        "model_calls": sum(t.model_calls for t in trials), "tool_calls": sum(t.tool_calls for t in trials),
        "tool_error_rate": ratio(sum(t.tool_errors for t in trials), sum(t.tool_calls for t in trials)),
        "denied_calls": sum(t.denied_calls for t in trials), "forbidden_attempts": sum(t.forbidden_attempts for t in trials),
        "actual_unexpected_change_trials": sum(bool(t.unexpected_changes) for t in trials),
        "usage_coverage": ratio(len(known_usage), len(trials)),
        "known_tokens": {key: sum(u.get(key, 0) for u in known_usage) for key in ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens", "total_tokens")},
        "cost_coverage": ratio(len(known_cost), len(trials)), "total_estimated_cost_cny": total_cost,
        "cost_per_success_cny": total_cost / successes if total_cost is not None and successes else None,
        "by_category": by_category, "by_difficulty": by_difficulty,
        "by_task": {key: {"recorded": len(groups[key]), "successes": sum(t.status == "success" for t in groups[key]), "planned": repeats} for key in task_ids},
    }
