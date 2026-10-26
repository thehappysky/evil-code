"""Strict public task contracts and private per-trial evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def relative_path(value: str) -> str:
    value = value.replace("\\", "/")
    path = PurePosixPath(value)
    if not value or not path.parts or path.is_absolute() or any(p in {"..", ".git"} or ":" in p for p in path.parts):
        raise ValueError("Paths must stay inside a relative workspace")
    return str(path)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Check(StrictModel):
    kind: Literal["text_contains", "file_equals", "file_contains", "file_absent", "json_file", "json_response", "python_function"]
    path: str | None = None
    expected: Any
    function: str | None = None
    inputs: list[list[Any]] = Field(default_factory=list)

    @field_validator("path")
    @classmethod
    def valid_path(cls, value):
        return relative_path(value) if value is not None else None

    @model_validator(mode="after")
    def complete(self):
        if self.kind in {"file_equals", "file_contains", "file_absent", "json_file", "python_function"} and not self.path:
            raise ValueError("File checks require a path")
        if self.kind == "python_function" and (not self.function or not self.function.isidentifier() or not self.inputs):
            raise ValueError("Python check requires function and inputs")
        return self


class Action(StrictModel):
    name: str
    arguments: dict = Field(default_factory=dict)


class Task(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{1,79}$")
    version: str = "1"
    category: str
    difficulty: Literal["easy", "medium", "hard"] = "easy"
    prompt: str = Field(min_length=5, max_length=20000)
    files: dict[str, str] = Field(default_factory=dict)
    allowed_writes: list[str] = Field(default_factory=list)
    checks: list[Check] = Field(min_length=1)
    reference_actions: list[Action] = Field(default_factory=list)
    reference_text: str = ""

    @field_validator("files")
    @classmethod
    def valid_files(cls, value):
        if len(value) > 100 or sum(len(v.encode()) for v in value.values()) > 128000:
            raise ValueError("Task fixture exceeds size limits")
        return {relative_path(k): v for k, v in value.items()}

    @field_validator("allowed_writes")
    @classmethod
    def valid_writes(cls, value):
        return [relative_path(v) for v in value]


class Grade(StrictModel):
    kind: str
    passed: bool | None
    reason: str


class Trial(StrictModel):
    task_id: str
    attempt: int
    category: str
    difficulty: str
    status: Literal["success", "task_failure", "model_error", "timeout", "harness_error", "budget_exceeded", "cancelled"]
    duration_seconds: float = Field(ge=0)
    ttft_seconds: float | None = None
    model_calls: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    denied_calls: int = 0
    forbidden_attempts: int = 0
    unexpected_changes: list[str] = Field(default_factory=list)
    usage: dict | None = None
    cost_cny: float | None = None
    grades: list[Grade] = Field(default_factory=list)
    error_type: str | None = None


def load_tasks(path: str | Path) -> tuple[list[Task], str]:
    raw = Path(path).read_bytes()
    if len(raw) > 10_000_000:
        raise ValueError("Dataset exceeds 10 MB")
    tasks = [Task.model_validate(json.loads(line)) for line in raw.decode("utf-8-sig").splitlines() if line.strip()]
    if not tasks or len({t.id for t in tasks}) != len(tasks):
        raise ValueError("Dataset must be nonempty with unique task IDs")
    return tasks, hashlib.sha256(raw).hexdigest()
