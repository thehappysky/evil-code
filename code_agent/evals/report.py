"""Private run reports and deliberately content-free public result exports."""

import csv
import html
import json
import random
from pathlib import Path

from code_agent.evals.metrics import aggregate, percentile
from code_agent.evals.runner import redact, write_json
from code_agent.evals.schema import Trial


def read_run(directory):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    trials = [Trial.model_validate(json.loads(p.read_text(encoding="utf-8"))) for p in sorted((directory / "trials").glob("*/*/trial.json"))]
    return manifest, trials, aggregate(trials, manifest["task_ids"], manifest["repeats"])


def fmt(value):
    return "未知 / 不适用" if value is None else f"{value:.6g}" if isinstance(value, float) else str(value)


def render_reports(directory):
    directory = Path(directory)
    manifest, trials, summary = read_run(directory)
    write_json(directory / "summary.json", summary)
    rows = [
        ("模式", manifest["mode"]), ("模型", manifest["metadata"].get("model", "fixture")),
        ("计划 / 已记录尝试", f"{summary['planned_attempts']} / {summary['recorded_attempts']}"),
        ("成功次数", summary["successes"]), ("端到端完成率", summary["end_to_end_success_rate"]),
        ("能力完成率（仅可判定任务）", summary["capability_success_rate"]),
        ("排除的基础设施/服务问题", summary["excluded_from_capability"]),
        ("pass@1", summary["pass@1"]), ("pass@2", summary["pass@2"]),
        ("全部重复均成功的任务比例", summary["all_repeats_success_rate"]),
        ("任务聚类 95% 区间", summary["task_cluster_ci95"]),
        ("完整耗时 P50 / 秒", summary["duration_p50_seconds"]),
        ("完整耗时 P95 / 秒", summary["duration_p95_seconds"]),
        ("首个可见文本 P50 / 秒", summary["ttft_p50_seconds"]),
        ("模型调用次数", summary["model_calls"]), ("工具调用次数", summary["tool_calls"]),
        ("工具错误率", summary["tool_error_rate"]), ("禁止访问尝试", summary["forbidden_attempts"]),
        ("实际非预期文件变更次数", summary["actual_unexpected_change_trials"]),
        ("API 用量完整率", summary["usage_coverage"]), ("已知 Token（非完整时不得视作总量）", summary["known_tokens"]),
        ("估算费用 / 元", summary["total_estimated_cost_cny"]),
        ("每次成功的全部投入 / 元", summary["cost_per_success_cny"]),
    ]
    notice = "离线参考/反例仅验证评测系统，不是大模型能力分数。" if manifest["mode"] == "fixture" else "真实模型、小样本开发集结果；不能代表总体能力或模型排名。费用为显式价格表估算，不是账单。"
    md = ["# Agent 评测报告", "", notice, "", "| 指标 | 值 |", "| --- | --- |"]
    md += [f"| {key} | {fmt(value)} |" for key, value in rows]
    md += ["", "## 状态分类", "", "```json", json.dumps(summary["statuses"], ensure_ascii=False, indent=2), "```", "", "## 分类别结果", "", "| 类别 | 尝试 | 成功 | 完成率 |", "| --- | ---: | ---: | ---: |"]
    md += [f"| {key} | {value['attempts']} | {value['successes']} | {fmt(value['success_rate'])} |" for key, value in summary["by_category"].items()]
    md += ["", "## 验证边界", "", "公开题集为开发集；小样本区间可能退化。工作区钩子不是完整 OS 沙箱。原始轨迹不应直接上传公开仓库。", ""]
    (directory / "report.md").write_text("\n".join(md), encoding="utf-8")
    table = "".join(f"<tr><th>{html.escape(key)}</th><td>{html.escape(fmt(value))}</td></tr>" for key, value in rows)
    bars = "".join(f'<div class="category">{html.escape(key)}<div class="track"><div class="bar" style="width:{100 * value["success_rate"]:.2f}%"></div></div>{value["successes"]}/{value["attempts"]}</div>' for key, value in summary["by_category"].items())
    document = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Evil Code 评测报告</title>
<style>body{{background:#f1f5f9;color:#172b4d;font:16px/1.65 "Segoe UI","Microsoft YaHei",sans-serif;margin:0}}main{{max-width:1050px;background:white;padding:38px;margin:28px auto;border-radius:16px}}h1{{color:#173e70}}.notice{{background:#fff4d7;padding:18px;border-radius:10px}}table{{width:100%;border-collapse:collapse}}th,td{{border-bottom:1px solid #dce4ef;padding:12px;text-align:left;overflow-wrap:anywhere}}th{{width:45%}}.track{{height:12px;background:#e9eff7;border-radius:8px}}.bar{{height:12px;background:#3b82f6;border-radius:8px}}.category{{margin:16px 0}}</style>
<main><h1>Evil Code · Agent 评测报告</h1><p class="notice">{html.escape(notice)}</p><h2>分类完成情况</h2>{bars}<h2>指标与口径</h2><table>{table}</table><h2>状态</h2><pre>{html.escape(json.dumps(summary['statuses'],ensure_ascii=False,indent=2))}</pre><p>公开开发集、小样本；未知值保留，不用零替代。详细可见轨迹留在本地。</p></main></html>'''
    (directory / "report.html").write_text(document, encoding="utf-8")
    with (directory / "trials.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["task_id","attempt","category","status","seconds","ttft_seconds","model_calls","tool_calls","forbidden_attempts","cost_cny"])
        for t in trials:
            writer.writerow([t.task_id,t.attempt,t.category,t.status,t.duration_seconds,t.ttft_seconds,t.model_calls,t.tool_calls,t.forbidden_attempts,t.cost_cny])
    with (directory / "human-review.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["task_id","attempt","status","reviewer","facts_0_2","completeness_0_2","code_quality_0_2","evidence_consistency_0_2","disagreement_notes"])
        for t in trials:
            writer.writerow([t.task_id,t.attempt,t.status,"","","","","",""])
    return summary


def compare_runs(baseline, candidate):
    left, lt, ls = read_run(baseline)
    right, rt, rs = read_run(candidate)
    keys = ("mode","dataset_sha256","task_ids","task_versions","repeats","max_turns","timeout_seconds","tool_names","system_prompt_sha256","grader_version")
    differences = [key for key in keys if left.get(key) != right.get(key)]
    if left["metadata"].get("max_output_tokens") != right["metadata"].get("max_output_tokens"):
        differences.append("max_output_tokens")
    if differences:
        raise ValueError("Incompatible experiments: " + ", ".join(differences))
    deltas = [(rs["by_task"][key]["successes"] - ls["by_task"][key]["successes"]) / left["repeats"] for key in left["task_ids"]]
    rng = random.Random(1729)
    boot = [sum(rng.choices(deltas,k=len(deltas))) / len(deltas) for _ in range(2000)] if len(deltas) > 1 else []
    complete = all(s["recorded_attempts"] == s["planned_attempts"] and s["excluded_from_capability"] == 0 for s in (ls, rs))
    return {"comparable":True,"complete":complete,"conclusion":"descriptive_only" if complete else "inconclusive_incomplete_trials", "mode":left["mode"],
            "success_rate_delta":rs["end_to_end_success_rate"]-ls["end_to_end_success_rate"],
            "paired_task_ci95":[percentile(boot,.025),percentile(boot,.975)] if boot else None,
            "small_sample_warning":len(deltas)<30,
            "task_success_deltas":dict(zip(left["task_ids"],deltas)),
            "baseline_cost_cny":ls["total_estimated_cost_cny"],"candidate_cost_cny":rs["total_estimated_cost_cny"]}


def export_summary(directory, target):
    manifest, _, summary = read_run(directory)
    # Deliberate whitelist: never export raw prompts, files, errors or traces.
    safe_metadata = {key:manifest["metadata"][key] for key in ("model","backend","max_output_tokens","temperature","pricing","variant","budget_result") if key in manifest["metadata"]}
    if "pricing" in safe_metadata:
        safe_metadata["pricing"] = {key:value for key,value in safe_metadata["pricing"].items() if key in {"model","currency","input_per_million","output_per_million","cached_per_million","source","as_of"}}
    if "budget_result" in safe_metadata:
        safe_metadata["budget_result"] = {key:value for key,value in safe_metadata["budget_result"].items() if key in {"limit_cny","charged_estimate_cny","requests","missing_usage_requests","reservation_overrun"}}
    safe_manifest = {key:manifest[key] for key in ("schema_version","grader_version","mode","dataset_sha256","task_ids","task_versions","repeats","max_turns","timeout_seconds","system_prompt_sha256","tool_names","agent_commit","working_tree_dirty","created_at")}
    safe_manifest["metadata"] = safe_metadata
    write_json(target, redact({"manifest":safe_manifest,"summary":summary}))
