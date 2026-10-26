"""Hand-computed scores and behavioral counterexamples for the evaluation harness."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from code_agent.api.schemas import StreamEvent
from code_agent.evals.clients import BudgetExceeded, CostLedger, ObservedClient
from code_agent.evals.fixtures import FixtureClient
from code_agent.evals.graders import grade
from code_agent.evals.metrics import aggregate, pass_at_k, percentile
from code_agent.evals.runner import redact, run_suite, run_trial
from code_agent.evals.schema import Task, Trial, load_tasks

ROOT = Path(__file__).resolve().parents[2]


def sample(**kwargs):
    fields = dict(task_id="aa", attempt=1, category="test", difficulty="easy", status="success", duration_seconds=1)
    return Trial(**(fields | kwargs))


def test_hand_computed_pass_k_and_percentiles():
    assert pass_at_k(4, 2, 2) == pytest.approx(5 / 6)
    assert pass_at_k(4, 0, 3) == 0
    assert pass_at_k(2, 1, 3) is None
    assert percentile([1, 2, 3, 4], .95) == pytest.approx(3.85)
    assert percentile([], .5) is None


def test_denominators_and_unknowns_are_explicit():
    trials = [sample(), sample(task_id="bb", status="model_error")]
    metrics = aggregate(trials, ["aa", "bb", "cc"], 1)
    assert metrics["end_to_end_success_rate"] == pytest.approx(1 / 3)
    assert metrics["capability_success_rate"] == 1
    assert metrics["excluded_from_capability"] == 1
    assert metrics["not_started"] == 1
    assert metrics["all_repeats_success_rate"] == pytest.approx(1 / 3)
    assert metrics["cost_per_success_cny"] is None
    assert metrics["usage_coverage"] == 0
    assert metrics["small_sample_warning"]


def test_cost_of_failed_attempts_is_included():
    metrics = aggregate([sample(cost_cny=1), sample(task_id="bb", status="task_failure", cost_cny=2)], ["aa", "bb"], 1)
    assert metrics["cost_per_success_cny"] == 3
    assert metrics["task_cluster_ci95"] is not None


def test_missing_repeat_cannot_claim_stability():
    metrics = aggregate([sample()], ["aa"], 2)
    assert metrics["all_repeats_success_rate"] == 0
    assert metrics["pass@1"] is None
    assert metrics["task_cluster_ci95"] is None


@pytest.mark.parametrize("trials", [[sample(), sample()], [sample(task_id="other")], [sample(attempt=3)]])
def test_duplicate_and_unplanned_attempts_are_rejected(trials):
    with pytest.raises(ValueError):
        aggregate(trials, ["aa"], 1)


@pytest.mark.parametrize("path", ["../escape", "C:/secrets", "/etc/passwd", "dir/file:stream", ".git/config"])
def test_dataset_cannot_create_external_files(path):
    with pytest.raises(ValidationError):
        Task(id="aa", category="x", prompt="create file", files={path: "bad"}, checks=[{"kind": "text_contains", "expected": "ok"}])


def test_duplicate_task_ids_rejected(tmp_path):
    tasks, _ = load_tasks(ROOT / "evals/datasets/core-v1.jsonl")
    path = tmp_path / "bad.jsonl"
    row = tasks[0].model_dump_json()
    path.write_text(row + "\n" + row, encoding="utf-8")
    with pytest.raises(ValueError):
        load_tasks(path)


async def test_reference_fixture_passes_all_core_tasks_and_noop_does_not(tmp_path):
    tasks, digest = load_tasks(ROOT / "evals/datasets/core-v1.jsonl")
    for reference in (True, False):
        _, trials, summary = await run_suite(tasks, digest, tmp_path / str(reference), lambda task: FixtureClient(task, reference=reference), mode="fixture", repeats=2)
        assert len(trials) == 32
        assert summary["successes"] == (32 if reference else 0)
        assert summary["pass@2"] == (1 if reference else 0)
        assert summary["actual_unexpected_change_trials"] == 0
        assert all(t.usage is None for t in trials)
        assert (tmp_path / str(reference) / "manifest.json").exists()


async def test_outside_read_and_unapproved_write_are_blocked(tmp_path):
    tasks, _ = load_tasks(ROOT / "evals/datasets/core-v1.jsonl")
    task = tasks[0].model_copy(deep=True)
    from code_agent.evals.schema import Action
    outside = tmp_path / "untouched.txt"
    outside.write_text("original", encoding="utf-8")
    task.reference_actions = [Action(name="file_write", arguments={"file_path": str(outside), "content": "bad"})]
    trial = await run_trial(task, 1, lambda task: FixtureClient(task))
    assert trial.status == "task_failure"
    assert trial.forbidden_attempts == 1
    assert outside.read_text(encoding="utf-8") == "original"


async def test_wrong_file_content_fails_despite_claim_of_completion():
    tasks, _ = load_tasks(ROOT / "evals/datasets/core-v1.jsonl")
    task = next(t for t in tasks if t.id == "write-config").model_copy(deep=True)
    task.reference_actions[0].arguments["content"] = '{"enabled":false,"retries":3}'
    task.reference_text = "All tasks completed and verified."
    trial = await run_trial(task, 1, lambda task: FixtureClient(task))
    assert trial.status == "task_failure"


async def test_timeout_and_service_errors_do_not_become_task_success():
    tasks, _ = load_tasks(ROOT / "evals/datasets/core-v1.jsonl")
    class Slow(FixtureClient):
        async def chat_stream(self, *a, **kw):
            await asyncio.sleep(10)
            yield StreamEvent(kind="turn_end")
    slow = Slow(tasks[0])
    slow.close = AsyncMock()
    trial = await run_trial(tasks[0], 1, lambda t: slow, timeout=.01)
    assert trial.status == "timeout"
    slow.close.assert_awaited_once()
    class Error(FixtureClient):
        async def chat_stream(self, *a, **kw):
            yield StreamEvent(kind="error", text="HTTP 503 sk-abcdefghijklmnopqrstuv")
    trial = await run_trial(tasks[0], 1, lambda t: Error(t))
    assert trial.status == "model_error"
    assert trial.error_type == "ModelFailure"


def test_json_bool_is_not_integer_and_missing_file_fails(tmp_path):
    task = Task(id="aa", category="json", prompt="output JSON", checks=[{"kind":"json_response","expected":{"value":1}}])
    assert not grade(task, tmp_path, '{"value":true}')[0].passed
    task.checks[0].kind = "json_file"
    task.checks[0].path = "missing.json"
    assert not grade(task, tmp_path, "")[0].passed


def test_code_grader_never_executes_candidate_on_host(tmp_path):
    task, _ = load_tasks(ROOT / "evals/datasets/coding-v1.jsonl")
    marker = tmp_path / "executed"
    (tmp_path / "calculator.py").write_text(f"open({str(marker)!r},'w').write('bad')", encoding="utf-8")
    assert grade(task[0], tmp_path, "")[0].passed is None
    assert not marker.exists()


def test_budget_reserves_before_calls_and_retains_unknown_usage():
    ledger = CostLedger(limit_cny=1, input_per_million=9, output_per_million=27, max_calls=1)
    reserved = ledger.reserve([], [])
    ledger.settle(reserved, None)
    assert ledger.charged == reserved
    assert ledger.missing_usage_calls == 1
    with pytest.raises(BudgetExceeded):
        ledger.reserve([], [])
    with pytest.raises(ValueError):
        CostLedger(limit_cny="NaN", input_per_million=9, output_per_million=27)


def test_cached_usage_settlement_and_zero_success_cost():
    ledger = CostLedger(limit_cny=5, input_per_million=9, output_per_million=27, cached_per_million=.3)
    reserved = ledger.reserve([], [])
    ledger.settle(reserved, {"prompt_tokens":1000,"completion_tokens":100,"cached_prompt_tokens":500})
    assert float(ledger.charged) == pytest.approx(.00735)
    metrics = aggregate([sample(status="task_failure", cost_cny=.2)], ["aa"], 1)
    assert metrics["total_estimated_cost_cny"] == .2
    assert metrics["cost_per_success_cny"] is None


async def test_usage_overrun_stops_budget_without_double_settlement():
    tasks, _ = load_tasks(ROOT / "evals/datasets/core-v1.jsonl")
    class LargeUsage(FixtureClient):
        async def chat_stream(self, *a, **kw):
            yield StreamEvent(kind="turn_end", usage={"prompt_tokens":100000,"completion_tokens":100000})
    ledger = CostLedger(limit_cny=5, input_per_million=9, output_per_million=27)
    observed = ObservedClient(LargeUsage(tasks[0]), ledger)
    with pytest.raises(BudgetExceeded):
        async for _ in observed.chat_stream([], []):
            pass
    assert ledger.overrun
    assert float(ledger.charged) == pytest.approx(3.6)


async def test_budget_rejection_happens_before_underlying_request():
    tasks, _ = load_tasks(ROOT / "evals/datasets/core-v1.jsonl")
    client = FixtureClient(tasks[0])
    ledger = CostLedger(limit_cny=.00001, input_per_million=9, output_per_million=27)
    observed = ObservedClient(client, ledger)
    with pytest.raises(BudgetExceeded):
        async for _ in observed.chat_stream([], []):
            pass
    assert client.calls == 0


def test_redaction_removes_credentials():
    result = redact({"api_key":"secret", "body":"sk-abcdefghijklmnopqrstuv", "path":"/tmp/work/file"}, "/tmp/work")
    assert result == {"api_key":"[REDACTED]", "body":"[REDACTED]", "path":"<workspace>/file"}
