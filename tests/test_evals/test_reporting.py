import json
from pathlib import Path

import pytest

from code_agent.evals.cli import main
from code_agent.evals.report import compare_runs, export_summary, render_reports
from code_agent.evals.runner import write_json

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "evals/datasets/core-v1.jsonl"


def run(tmp_path, name, mode="reference"):
    output = tmp_path / name
    code = main(["run","--dataset",str(DATA),"--mode",mode,"--tasks","read-project,write-config","--repeats","2","--output",str(output)])
    assert code == 0
    return output


def test_cli_round_trip_and_reference_noop_comparison(tmp_path):
    assert main(["validate","--dataset",str(DATA)]) == 0
    reference = run(tmp_path, "reference")
    noop = run(tmp_path, "noop", "noop")
    assert main(["report","--run",str(reference)]) == 0
    summary = json.loads((reference / "summary.json").read_text())
    assert summary["successes"] == 4
    for name in ("report.md","report.html","trials.csv","human-review.csv"):
        assert (reference / name).stat().st_size > 0
    compared = compare_runs(noop, reference)
    assert compared["success_rate_delta"] == 1
    assert compared["mode"] == "fixture"
    assert compared["small_sample_warning"]
    assert main(["compare","--baseline",str(noop),"--candidate",str(reference),"--output",str(tmp_path / "comparison.json")]) == 0


@pytest.mark.parametrize("key,value", [("dataset_sha256","changed"),("mode","live"),("repeats",5),("max_turns",3)])
def test_incompatible_runs_cannot_be_ranked(tmp_path, key, value):
    baseline = run(tmp_path, "baseline")
    candidate = run(tmp_path, "candidate")
    manifest = json.loads((candidate / "manifest.json").read_text())
    manifest[key] = value
    write_json(candidate / "manifest.json", manifest)
    with pytest.raises(ValueError):
        compare_runs(baseline, candidate)


def test_export_does_not_publish_traces_or_credentials(tmp_path):
    directory = run(tmp_path, "reference")
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest["metadata"].update({"api_key":"DO_NOT_EXPORT", "pricing":{"api_key":"DO_NOT_EXPORT", "currency":"CNY"}, "budget_result":{"authorization":"DO_NOT_EXPORT","limit_cny":5}})
    write_json(directory / "manifest.json", manifest)
    output = tmp_path / "public.json"
    export_summary(directory, output)
    assert "DO_NOT_EXPORT" not in output.read_text()
    assert "reference_actions" not in output.read_text()
    assert "trace" not in output.read_text()


def test_html_escapes_model_labels(tmp_path):
    directory = run(tmp_path, "reference")
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest["metadata"]["model"] = "<script>alert(1)</script>"
    write_json(directory / "manifest.json", manifest)
    render_reports(directory)
    text = (directory / "report.html").read_text(encoding="utf-8")
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


def test_existing_output_unknown_tasks_and_live_without_pricing_are_rejected(tmp_path):
    directory = run(tmp_path, "reference")
    assert main(["run","--output",str(directory)]) == 2
    assert main(["run","--tasks","missing-task","--output",str(tmp_path / "unknown")]) == 2
    assert main(["run","--mode","live","--output",str(tmp_path / "live")]) == 2
    assert not (tmp_path / "live").exists()


def test_quality_gate_failure_has_explicit_exit_code(tmp_path):
    assert main(["run","--mode","noop","--tasks","read-project","--output",str(tmp_path / "noop"),"--fail-under","1"]) == 3
