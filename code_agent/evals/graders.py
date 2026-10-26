"""Outcome grading independent of what the agent claims to have done."""

import json
from pathlib import Path

from code_agent.evals.schema import Grade, Task


def grade(task: Task, workspace: Path, response: str) -> list[Grade]:
    results = []
    for check in task.checks:
        if check.kind == "file_absent":
            results.append(Grade(kind=check.kind, passed=not (workspace / check.path).exists(), reason="absence_checked"))
            continue
        if check.kind == "python_function":
            results.append(Grade(kind=check.kind, passed=None, reason="docker_grader_required"))
            continue
        try:
            content = response if check.path is None else (workspace / check.path).read_text(encoding="utf-8")
            if check.kind in {"json_file", "json_response"}:
                # Canonical JSON avoids Python equating true with 1.
                passed = json.dumps(json.loads(content), sort_keys=True) == json.dumps(check.expected, sort_keys=True)
            elif check.kind in {"file_contains", "text_contains"}:
                needles = check.expected if isinstance(check.expected, list) else [check.expected]
                passed = all(str(value) in content for value in needles)
            else:
                passed = content == check.expected
            results.append(Grade(kind=check.kind, passed=passed, reason="matched" if passed else "outcome_mismatch"))
        except (OSError, UnicodeError, ValueError):
            results.append(Grade(kind=check.kind, passed=False, reason="missing_or_invalid_output"))
    return results
