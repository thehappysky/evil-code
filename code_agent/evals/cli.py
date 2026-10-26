"""Command-line evaluations: validate, run, report, compare, and safe export."""

import argparse
import asyncio
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from code_agent.evals.clients import CostLedger
from code_agent.evals.fixtures import FixtureClient
from code_agent.evals.report import compare_runs, export_summary, render_reports
from code_agent.evals.runner import run_suite, write_json
from code_agent.evals.schema import StrictModel, load_tasks

DEFAULT_DATASET = "evals/datasets/core-v1.jsonl"


class Pricing(StrictModel):
    model: str
    host: str
    currency: Literal["CNY"] = "CNY"
    input_per_million: Decimal = Field(ge=0)
    output_per_million: Decimal = Field(ge=0)
    cached_per_million: Decimal = Field(ge=0)
    source: str
    as_of: str

    @model_validator(mode="after")
    def valid_cache_price(self):
        if self.cached_per_million > self.input_per_million:
            raise ValueError("Cached price cannot exceed normal input price")
        return self


def parser():
    root = argparse.ArgumentParser(prog="code-agent-eval", description="Evil Code 可复现 Agent 评测")
    commands = root.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="校验任务集并显示类别和哈希")
    validate.add_argument("--dataset", default=DEFAULT_DATASET)
    run = commands.add_parser("run", help="离线参考/反例或真实模型执行")
    run.add_argument("--dataset", default=DEFAULT_DATASET)
    run.add_argument("--mode", choices=["reference","noop","live"], default="reference")
    run.add_argument("--tasks", help="逗号分隔 task ID，不指定时运行整个任务集")
    run.add_argument("--repeats", type=int, default=1)
    run.add_argument("--max-turns", type=int, default=8)
    run.add_argument("--timeout", type=float, default=90)
    run.add_argument("--max-output-tokens", type=int, default=4096)
    run.add_argument("--output", required=True, type=Path)
    run.add_argument("--pricing", type=Path, help="真实调用必须显式提供 CNY 价格快照")
    run.add_argument("--budget-cny", type=float, default=5)
    run.add_argument("--max-calls", type=int, default=24)
    run.add_argument("--model")
    run.add_argument("--profile", help="使用 TUI profiles.json 中指定的模型配置")
    run.add_argument("--fail-under", type=float, default=0)
    report = commands.add_parser("report", help="从本地逐次记录重新生成报告")
    report.add_argument("--run", required=True, type=Path)
    compare = commands.add_parser("compare", help="校验实验可比性并按任务配对比较")
    compare.add_argument("--baseline", required=True, type=Path)
    compare.add_argument("--candidate", required=True, type=Path)
    compare.add_argument("--output", required=True, type=Path)
    export = commands.add_parser("export", help="仅导出白名单汇总，不包含原始轨迹")
    export.add_argument("--run", required=True, type=Path)
    export.add_argument("--output", required=True, type=Path)
    return root


async def execute(args):
    tasks, digest = load_tasks(args.dataset)
    if args.tasks:
        ids = args.tasks.split(",")
        if len(set(ids)) != len(ids) or set(ids) - {t.id for t in tasks}:
            raise ValueError("Unknown or duplicate task selection")
        tasks = [t for t in tasks if t.id in ids]
    if not 1 <= args.max_turns <= 30 or not 0 < args.timeout <= 600 or not 1 <= args.max_output_tokens <= 8192 or not 0 <= args.fail_under <= 1:
        raise ValueError("Invalid resource limits")
    ledger = None
    metadata = {"max_output_tokens":args.max_output_tokens}
    if args.mode == "live":
        if args.pricing is None:
            raise ValueError("Live runs require --pricing")
        from code_agent.api.client import ChatClient
        from code_agent.cli.args import build_parser
        from code_agent.cli.auth import config_from_profile, resolve_provider_config
        if args.profile:
            from code_agent.tui.stores import JsonProviderSettingsStore
            settings = await JsonProviderSettingsStore().load()
            profile = settings.profile(args.profile)
            if profile is None:
                raise ValueError("Unknown provider profile")
            config = config_from_profile(profile)
        else:
            config = resolve_provider_config(build_parser().parse_args([]))
        if not config.api_key:
            raise ValueError("No locally configured credential")
        config = replace(config, model=args.model or config.model, max_tokens=args.max_output_tokens)
        prices = Pricing.model_validate_json(args.pricing.read_text(encoding="utf-8"))
        if prices.model != config.model or prices.host != urlsplit(config.base_url).hostname:
            raise ValueError("Pricing snapshot does not match configured model/host")
        ledger = CostLedger(limit_cny=args.budget_cny, input_per_million=prices.input_per_million,
            output_per_million=prices.output_per_million, cached_per_million=prices.cached_per_million,
            max_calls=args.max_calls, max_output_tokens=args.max_output_tokens)
        # Deliberately no retry policy: each request is reserved separately.
        factory = lambda task: ChatClient(config)
        metadata |= {"model":config.model,"backend":config.api_backend.value,"temperature":config.temperature,
                     "pricing":prices.model_dump(mode="json"),"budget_limit_cny":args.budget_cny}
    else:
        factory = lambda task: FixtureClient(task, reference=args.mode == "reference")
        metadata |= {"variant":args.mode,"model":"fixture-" + args.mode}
    manifest, trials, summary = await run_suite(tasks, digest, args.output, factory,
        mode="live" if args.mode == "live" else "fixture", repeats=args.repeats,
        max_turns=args.max_turns, timeout=args.timeout, ledger=ledger, metadata=metadata)
    if ledger:
        manifest["metadata"]["budget_result"] = {"limit_cny":float(ledger.limit),"charged_estimate_cny":float(ledger.charged),
            "requests":ledger.calls,"missing_usage_requests":ledger.missing_usage_calls,"reservation_overrun":ledger.overrun}
        write_json(args.output / "manifest.json", manifest)
    render_reports(args.output)
    print(json.dumps({"mode":manifest["mode"],"output":str(args.output),"successes":summary["successes"],
        "recorded":summary["recorded_attempts"],"planned":summary["planned_attempts"],"statuses":summary["statuses"],
        "estimated_cost_cny":summary["total_estimated_cost_cny"]}, ensure_ascii=False))
    if summary["excluded_from_capability"] or summary["not_started"]:
        return 2
    return 0 if summary["end_to_end_success_rate"] >= args.fail_under else 3


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "validate":
            tasks, digest = load_tasks(args.dataset)
            print(json.dumps({"tasks":len(tasks),"sha256":digest,"categories":sorted({t.category for t in tasks}),"task_ids":[t.id for t in tasks]},ensure_ascii=False))
        elif args.command == "run":
            return asyncio.run(execute(args))
        elif args.command == "report":
            summary = render_reports(args.run)
            print(json.dumps({"successes":summary["successes"],"statuses":summary["statuses"]},ensure_ascii=False))
        elif args.command == "compare":
            result = compare_runs(args.baseline, args.candidate)
            write_json(args.output, result)
            print(json.dumps(result,ensure_ascii=False))
        elif args.command == "export":
            export_summary(args.run, args.output)
            print(str(args.output))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        # Do not print arbitrary provider responses or credential-bearing URLs.
        print(json.dumps({"error_type":type(exc).__name__,"message":"配置、输入或文件校验失败；未输出敏感错误正文。"},ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
