"""重建公开统计及失败归因；只读本地运行，不调用模型服务。"""
import asyncio
import json
import tempfile
from decimal import Decimal
from pathlib import Path

from code_agent.tools.builtin.file_read import ReadFileInput, file_read

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
LIVE = ROOT / ".eval-runs/live-core-corrected-20261026"
INITIAL = ROOT / ".eval-runs/live-deepseek-20261026"


async def main():
    manifests = [json.loads((p / "manifest.json").read_text(encoding="utf-8")) for p in (INITIAL, LIVE)]
    costs = [Decimal(str(m["metadata"]["budget_result"]["charged_estimate_cny"])) for m in manifests]
    assert costs[0] + Decimal("4.90") <= Decimal("5")
    assert sum(costs) <= Decimal("5")
    assert all(not m["metadata"]["budget_result"]["reservation_overrun"] for m in manifests)
    assert all(m["metadata"]["budget_result"]["missing_usage_requests"] == 0 for m in manifests)
    trials = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((LIVE / "trials").glob("*/*/trial.json"))]
    failed = [t for t in trials if t["status"] != "success"]
    assert len(failed) == 2 and all(t["task_id"] == "move-file" for t in failed)
    cases = []
    for t in failed:
        trace = json.loads((LIVE / "trials" / t["task_id"] / str(t["attempt"]) / "trace.json").read_text(encoding="utf-8"))
        writes = [json.loads(c["function"]["arguments"]) for m in trace for c in m.get("tool_calls", []) if c["function"]["name"] == "file_write"]
        written = next(w["content"] for w in writes if w["file_path"] == "new.txt")
        assert len(written) == 17 and not written.endswith("\n")
        cases.append({"task_id":t["task_id"],"attempt":t["attempt"],"status":t["status"],
                      "expected_characters":18,"actual_characters":len(written),"expected_final_newline":True,
                      "actual_final_newline":written.endswith("\n"),"grades":t["grades"]})
    with tempfile.TemporaryDirectory(prefix="evil-eval-observation-") as tmp:
        with_newline, without_newline = Path(tmp)/"with.txt", Path(tmp)/"without.txt"
        with_newline.write_text("migration payload\n", encoding="utf-8")
        without_newline.write_text("migration payload", encoding="utf-8")
        first = await file_read(ReadFileInput(file_path=str(with_newline)))
        second = await file_read(ReadFileInput(file_path=str(without_newline)))
        assert first.output == second.output
    console = (OUT / "live-core-console.txt").read_text(encoding="utf-8")
    result = {
        "software_commit":manifests[1]["agent_commit"],"working_tree_dirty_at_run_start":manifests[1]["working_tree_dirty"],
        "authorized_total_cny":5,"initial_estimated_cny":float(costs[0]),"second_run_limit_cny":4.90,
        "core_estimated_cny":float(costs[1]),"total_estimated_cny":float(sum(costs)),
        "requests":sum(m["metadata"]["budget_result"]["requests"] for m in manifests),
        "unsettled_usage_requests":0,"core_close_warning_observed":"aclose():" in console,
        "failed_cases":cases,"file_read_hides_final_newline":first.output == second.output,
        "interpretation":"迁移结果确实不一致，保留任务失败；读取工具丢失末尾换行信息，不能把端到端失败全部归因于模型。未放宽判题或改题。",
        "tool_error_trials":[{"task_id":t["task_id"],"attempt":t["attempt"],"tool_errors":t["tool_errors"]} for t in trials if t["tool_errors"]],
    }
    (OUT / "live-analysis.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
