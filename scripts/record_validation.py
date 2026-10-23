"""Save comparable pytest/JUnit/coverage results without extra dependencies."""

import argparse
import json
import platform
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--coverage", type=Path)
    parser.add_argument("--console", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = ET.parse(args.junit).getroot()
    suites = [report] if report.tag == "testsuite" else list(report.findall("testsuite"))
    if not suites:
        raise ValueError("No test suite in JUnit report")
    counts = {key: sum(int(s.get(key, 0)) for s in suites) for key in ("tests", "failures", "errors", "skipped")}
    output = {
        "label": args.label,
        "base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip(),
        "python": platform.python_version(),
        "platform": platform.system(),
        **counts,
        "passed": counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"],
        "junit_seconds": sum(float(s.get("time", 0)) for s in suites),
    }
    console = args.console.read_text(encoding="utf-8", errors="replace")
    duration = re.findall(r" in ([0-9.]+)s", console)
    if duration:
        output["pytest_seconds"] = float(duration[-1])
    if args.coverage:
        totals = json.loads(args.coverage.read_text(encoding="utf-8"))["totals"]
        output["coverage"] = {key: totals[key] for key in ("covered_lines", "num_statements", "percent_covered")}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
