"""Ported domain layer for the Evil Code TUI.

Lightweight, self-contained ports of the reference ``evil_code`` domain types
consolidated into one module so the production TUI can run against the
``code_agent`` package without importing the reference project. Everything here
mirrors the reference shapes so ``code_agent/tui/tui.py`` works unchanged.

There are deliberately no imports from other ``code_agent`` modules: this layer
only defines value types, protocols, and small pure helpers.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol, TypeAlias
from urllib.parse import urlsplit

# ─── UI language ────────────────────────────────────────────────────────────────


class UiLanguage(StrEnum):
    """Languages supported by the application-owned terminal interface."""

    ENGLISH = "en"
    SIMPLIFIED_CHINESE = "zh-CN"


# ─── Reasoning effort ───────────────────────────────────────────────────────────


class ReasoningEffort(StrEnum):
    """Provider-neutral review depth requested from the agent runtime."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    ULTRACODE = "ultracode"

    @property
    def glyph(self) -> str:
        return {
            ReasoningEffort.LOW: "○",
            ReasoningEffort.MEDIUM: "◐",
            ReasoningEffort.HIGH: "●",
            ReasoningEffort.XHIGH: "⬤",
            ReasoningEffort.ULTRACODE: "⚡",
        }[self]

    @property
    def effective(self) -> ReasoningEffort:
        """Return the implemented policy depth for this requested level."""
        if self is ReasoningEffort.ULTRACODE:
            return ReasoningEffort.XHIGH
        return self

    @property
    def requires_workflow_orchestration(self) -> bool:
        return self is ReasoningEffort.ULTRACODE


# ─── Interaction mode ───────────────────────────────────────────────────────────


class InteractionMode(StrEnum):
    """Application-owned operating modes for an interactive coding session."""

    NORMAL = "normal"
    ACCEPT_EDITS = "accept-edits"
    PLAN = "plan"
    AUTO = "auto"

    @property
    def glyph(self) -> str:
        return {
            InteractionMode.NORMAL: "◇",
            InteractionMode.ACCEPT_EDITS: "◆",
            InteractionMode.PLAN: "▣",
            InteractionMode.AUTO: "▶",
        }[self]

    @property
    def next(self) -> InteractionMode:
        modes = tuple(InteractionMode)
        return modes[(modes.index(self) + 1) % len(modes)]


# ─── Messages ───────────────────────────────────────────────────────────────────


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ContentPartKind(StrEnum):
    TEXT = "text"
    IMAGE = "image"


class ContextItemKind(StrEnum):
    REASONING = "reasoning"
    BACKEND_TOOL_CALL = "backend_tool_call"


class SyntheticReason(StrEnum):
    """Marks a message as synthetic (injected context, not a genuine turn)."""

    PROJECT_INSTRUCTIONS = "project-instructions"
    AVAILABLE_SKILLS = "available-skills"


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class ContentPart:
    kind: ContentPartKind
    text: str | None = None
    url: str | None = None

    @classmethod
    def from_text(cls, text: str) -> ContentPart:
        return cls(ContentPartKind.TEXT, text=text)

    @classmethod
    def from_image(cls, url: str) -> ContentPart:
        return cls(ContentPartKind.IMAGE, url=url)

    def to_dict(self) -> dict[str, str]:
        if self.kind is ContentPartKind.TEXT:
            assert self.text is not None
            return {"type": self.kind.value, "text": self.text}
        assert self.url is not None
        return {"type": self.kind.value, "url": self.url}


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: Mapping[str, Any]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "arguments", _freeze_mapping(self.arguments))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "arguments": dict(self.arguments),
        }
        if self.metadata:
            result["metadata"] = dict(self.metadata)
        return result


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str = ""
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)
    content_parts: tuple[ContentPart, ...] = field(default_factory=tuple)
    reasoning_content: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_calls", tuple(self.tool_calls))
        object.__setattr__(self, "content_parts", tuple(self.content_parts))
        if self.content_parts:
            text = "\n".join(
                part.text
                for part in self.content_parts
                if part.kind is ContentPartKind.TEXT and part.text is not None
            )
            if not self.content:
                object.__setattr__(self, "content", text)

    def model_content(self) -> str:
        if not self.content_parts:
            return self.content
        parts: list[str] = []
        for part in self.content_parts:
            if part.kind is ContentPartKind.TEXT:
                assert part.text is not None
                parts.append(part.text)
            else:
                parts.append("[image content preserved in session]")
        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"role": self.role.value, "content": self.content}
        if self.name is not None:
            result["name"] = self.name
        if self.tool_call_id is not None:
            result["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            result["tool_calls"] = [call.to_dict() for call in self.tool_calls]
        if self.content_parts:
            result["content_parts"] = [part.to_dict() for part in self.content_parts]
        if self.reasoning_content is not None:
            result["reasoning_content"] = self.reasoning_content
        return result


@dataclass(frozen=True, slots=True)
class PreservedContextItem:
    kind: ContextItemKind
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", _freeze_mapping(self.payload))


SessionItem: TypeAlias = Message | PreservedContextItem


# ─── Token estimation ───────────────────────────────────────────────────────────


def estimate_text_tokens(text: str) -> int:
    """Approximate token count without a provider-specific tokenizer."""
    if not text:
        return 0
    ascii_characters = sum(character.isascii() for character in text)
    non_ascii_characters = len(text) - ascii_characters
    return max(1, math.ceil(ascii_characters * 0.3 + non_ascii_characters * 0.6))


def _estimate_value_tokens(value: Any) -> int:
    if isinstance(value, str):
        return estimate_text_tokens(value)
    if isinstance(value, Mapping):
        return 2 + sum(
            estimate_text_tokens(str(key)) + _estimate_value_tokens(item)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return 2 + sum(_estimate_value_tokens(item) for item in value)
    if value is None:
        return 1
    return estimate_text_tokens(str(value))


def estimate_context_tokens(items: Sequence[SessionItem]) -> int:
    """Estimate the reusable conversation context represented by session items."""
    total = 0
    for item in items:
        if isinstance(item, Message):
            total += 4
            total += estimate_text_tokens(item.role.value)
            total += estimate_text_tokens(item.model_content())
            total += estimate_text_tokens(item.name or "")
            total += estimate_text_tokens(item.tool_call_id or "")
            total += estimate_text_tokens(item.reasoning_content or "")
            total += _estimate_value_tokens(tuple(call.to_dict() for call in item.tool_calls))
        elif isinstance(item, PreservedContextItem):
            total += 4 + _estimate_value_tokens(item.payload)
    return total


# ─── Agent events ───────────────────────────────────────────────────────────────


class AgentEventKind(StrEnum):
    SESSION_STARTED = "session_started"
    USER_MESSAGE = "user_message"
    MODEL_STEP_STARTED = "model_step_started"
    MODEL_THINKING_COMPLETED = "model_thinking_completed"
    CONTEXT_USAGE_UPDATED = "context_usage_updated"
    BACKGROUND_TASK_COMPLETION_REMINDER = "background_task_completion_reminder"
    PROVIDER_ATTEMPT_FAILED = "provider_attempt_failed"
    PROVIDER_SELECTED = "provider_selected"
    TEXT_DELTA = "text_delta"
    REASONING_DELTA = "reasoning_delta"
    BACKEND_TOOL_STARTED = "backend_tool_started"
    BACKEND_TOOL_COMPLETED = "backend_tool_completed"
    TOOL_REQUESTED = "tool_requested"
    TOOL_PERMISSION = "tool_permission"
    TOOL_APPROVAL_REQUESTED = "tool_approval_requested"
    TOOL_APPROVAL_RESOLVED = "tool_approval_resolved"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    TOOL_FAILED = "tool_failed"
    PLAN_UPDATED = "plan_updated"
    PLAN_EXECUTION_REQUESTED = "plan_execution_requested"
    SESSION_TASK_STARTED = "session_task_started"
    SESSION_TASK_COMPLETED = "session_task_completed"
    SESSION_TASK_FAILED = "session_task_failed"
    SESSION_TASK_CANCELLED = "session_task_cancelled"
    TURN_COMPLETED = "turn_completed"
    TURN_FAILED = "turn_failed"


@dataclass(frozen=True, slots=True)
class AgentEvent:
    sequence: int
    kind: AgentEventKind
    data: Mapping[str, Any]
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")

    @classmethod
    def create(
        cls,
        sequence: int,
        kind: AgentEventKind,
        data: Mapping[str, Any] | None = None,
    ) -> AgentEvent:
        return cls(sequence, kind, data or {}, datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "kind": self.kind.value,
            "data": dict(self.data),
            "created_at": self.created_at.isoformat(),
        }


EventSink: TypeAlias = Callable[[AgentEvent], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    session_id: str | None
    response: str
    messages: tuple[Message, ...]
    items: tuple[SessionItem, ...]
    events: tuple[AgentEvent, ...]
    steps: int
    plan: SessionPlan | None = None


# ─── Permissions / approval ─────────────────────────────────────────────────────


class PermissionApprovalKind(StrEnum):
    ALLOW_ONCE = "allow_once"
    ALLOW_SESSION = "allow_session"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class PermissionApproval:
    kind: PermissionApprovalKind
    reason: str

    @property
    def allowed(self) -> bool:
        return self.kind in {
            PermissionApprovalKind.ALLOW_ONCE,
            PermissionApprovalKind.ALLOW_SESSION,
        }

    @classmethod
    def allow_once(cls, reason: str = "approved once by user") -> PermissionApproval:
        return cls(PermissionApprovalKind.ALLOW_ONCE, reason)

    @classmethod
    def allow_session(
        cls,
        reason: str = "approved for this session by user",
    ) -> PermissionApproval:
        return cls(PermissionApprovalKind.ALLOW_SESSION, reason)

    @classmethod
    def deny(cls, reason: str = "denied by user") -> PermissionApproval:
        return cls(PermissionApprovalKind.DENY, reason)


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    call_id: str
    tool_name: str
    summary: str
    reason: str
    scope_key: str | None


def _bounded_text(value: object, *, limit: int) -> str:
    if not isinstance(value, str) or not value:
        return "(not provided)"
    sanitized = value.replace("\x00", "�")
    if len(sanitized) <= limit:
        return sanitized
    return f"{sanitized[:limit]}\n… [truncated]"


def build_permission_request(
    call_id: str,
    tool_name: str,
    arguments: Mapping[str, Any],
    reason: str,
) -> PermissionRequest:
    """Build a bounded UI description and an opaque exact-action session scope."""
    if tool_name in {"bash", "shell_exec"}:
        command = arguments.get("command")
        summary = f"Run shell command:\n{_bounded_text(command, limit=2_000)}"
    elif tool_name in {"file_edit", "search_replace"}:
        path = _bounded_text(arguments.get("file_path", arguments.get("path")), limit=500)
        qualifier = (
            "all matching occurrences" if arguments.get("replace_all") is True else "one occurrence"
        )
        summary = (
            f"Edit workspace file: {path}\n"
            f"Replace {qualifier}; replacement text is hidden from the approval UI."
        )
    else:
        name = _bounded_text(tool_name, limit=200)
        target = arguments.get("path") or arguments.get("file_path") or arguments.get("url")
        target_note = (
            f"\nTarget: {_bounded_text(target, limit=500)}" if isinstance(target, str) else ""
        )
        summary = f"Run side-effecting tool: {name}{target_note}"

    try:
        scope_payload = json.dumps(
            {"arguments": dict(arguments), "tool": tool_name},
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            default=lambda value: dict(value) if isinstance(value, Mapping) else repr(value),
        )
    except (TypeError, ValueError):
        scope_key = None
    else:
        scope_key = hashlib.sha256(scope_payload.encode("utf-8")).hexdigest()
    return PermissionRequest(call_id, tool_name, summary, reason, scope_key)


ApprovalHandler: TypeAlias = Callable[[PermissionRequest], Awaitable[PermissionApproval]]


class SessionApprovalBroker:
    """Bridge runtime approval requests to one UI and cache exact session scopes."""

    def __init__(self) -> None:
        self._handler: ApprovalHandler | None = None
        self._approved_scopes: set[str] = set()

    def set_handler(self, handler: ApprovalHandler | None) -> None:
        self._handler = handler

    async def request(self, request: PermissionRequest) -> PermissionApproval:
        if request.scope_key is not None and request.scope_key in self._approved_scopes:
            return PermissionApproval.allow_session(
                "matched an identical action approved for this session"
            )
        if self._handler is None:
            return PermissionApproval.deny("interactive approval UI is unavailable")
        approval = await self._handler(request)
        if approval.kind is PermissionApprovalKind.ALLOW_SESSION:
            if request.scope_key is None:
                return PermissionApproval.allow_once(
                    "action arguments could not be scoped safely; approval applied once"
                )
            self._approved_scopes.add(request.scope_key)
        return approval


# ─── Structured plans ───────────────────────────────────────────────────────────

MAX_PLAN_STEP_BYTES = 600
MAX_PLAN_EXPLANATION_BYTES = 2_000
MAX_PLAN_STEPS = 12
MAX_PLAN_COMMENT_BYTES = 2_000
MAX_PLAN_COMMENTS = 48


class PlanStepStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


def _plan_bounded_text(value: object, *, field_name: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"plan {field_name} must be a string")
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError(f"plan {field_name} must not be empty")
    if "\x00" in normalized or any(ord(character) < 32 for character in normalized):
        raise ValueError(f"plan {field_name} contains control characters")
    if len(normalized.encode("utf-8")) > limit:
        raise ValueError(f"plan {field_name} is too large")
    return normalized


@dataclass(frozen=True, slots=True)
class PlanStep:
    step: str
    status: PlanStepStatus = PlanStepStatus.PENDING

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "step", _plan_bounded_text(self.step, field_name="step", limit=MAX_PLAN_STEP_BYTES)
        )

    def to_dict(self) -> dict[str, str]:
        return {"step": self.step, "status": self.status.value}


@dataclass(frozen=True, slots=True)
class PlanComment:
    comment_id: str
    step_index: int
    content: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not 1 <= self.step_index <= MAX_PLAN_STEPS:
            raise ValueError(f"plan comment step index must be between 1 and {MAX_PLAN_STEPS}")
        object.__setattr__(
            self, "content", _plan_bounded_text(self.content, field_name="comment", limit=MAX_PLAN_COMMENT_BYTES)
        )
        if self.created_at.tzinfo is None:
            raise ValueError("plan comment time must be timezone-aware")

    def to_dict(self) -> dict[str, str | int]:
        return {
            "comment_id": self.comment_id,
            "step_index": self.step_index,
            "content": self.content,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class SessionPlan:
    steps: tuple[PlanStep, ...]
    explanation: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "steps", tuple(self.steps))
        if not self.steps:
            raise ValueError("plan must contain at least one step")
        if len(self.steps) > MAX_PLAN_STEPS:
            raise ValueError(f"plan must contain at most {MAX_PLAN_STEPS} steps")
        if self.explanation is not None:
            object.__setattr__(
                self,
                "explanation",
                _plan_bounded_text(
                    self.explanation,
                    field_name="explanation",
                    limit=MAX_PLAN_EXPLANATION_BYTES,
                ),
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "explanation": self.explanation,
            "plan": [step.to_dict() for step in self.steps],
        }

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, value: object) -> SessionPlan:
        if not isinstance(value, Mapping):
            raise ValueError("plan payload must be an object")
        if set(value) != {"explanation", "plan"}:
            raise ValueError("plan payload has unsupported fields")
        raw_explanation = value.get("explanation")
        if raw_explanation is not None and not isinstance(raw_explanation, str):
            raise ValueError("plan explanation must be a string or null")
        raw_steps = value.get("plan")
        if not isinstance(raw_steps, Sequence) or isinstance(raw_steps, str | bytes):
            raise ValueError("plan steps must be an array")
        steps: list[PlanStep] = []
        for raw_step in raw_steps:
            if not isinstance(raw_step, Mapping) or set(raw_step) != {"step", "status"}:
                raise ValueError("plan step has unsupported fields")
            raw_status = raw_step.get("status")
            if not isinstance(raw_status, str):
                raise ValueError("plan step status is invalid")
            try:
                status = PlanStepStatus(raw_status)
            except ValueError:
                raise ValueError("plan step status is invalid") from None
            steps.append(PlanStep(_plan_bounded_text(raw_step.get("step"), field_name="step", limit=MAX_PLAN_STEP_BYTES), status))
        return cls(tuple(steps), raw_explanation)

    def model_guidance(self) -> str:
        lines = ["Current structured plan:"]
        if self.explanation is not None:
            lines.append(f"Purpose: {self.explanation}")
        for index, step in enumerate(self.steps, start=1):
            lines.append(f"{index}. [{step.status.value}] {step.step}")
        return "\n".join(lines)

    def comment_guidance(self, comments: Sequence[PlanComment]) -> str:
        normalized = tuple(comments)
        if not normalized:
            return ""
        lines = ["User comments on the current structured plan:"]
        lines.extend(f"Step {comment.step_index}: {comment.content}" for comment in normalized)
        return "\n".join(lines)


# ─── Sessions / tasks ───────────────────────────────────────────────────────────


class SandboxProfile(StrEnum):
    OFF = "off"
    WORKSPACE = "workspace"
    READ_ONLY = "read-only"
    STRICT = "strict"

    @property
    def enabled(self) -> bool:
        return self is not self.OFF

    @property
    def workspace_writable(self) -> bool:
        return self is not self.READ_ONLY

    @property
    def restricts_child_network(self) -> bool:
        return self in {self.READ_ONLY, self.STRICT}


@dataclass(frozen=True, slots=True)
class SessionSummary:
    id: str
    cwd: str
    provider: str
    model: str
    created_at: datetime
    updated_at: datetime
    context_affinity: str | None = None
    sandbox_profile: SandboxProfile | None = None
    title: str | None = None

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("session timestamps must be timezone-aware")

    def to_dict(self) -> dict[str, str | None]:
        return {
            "id": self.id,
            "cwd": self.cwd,
            "provider": self.provider,
            "model": self.model,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "context_affinity": self.context_affinity,
            "sandbox_profile": (
                self.sandbox_profile.value if self.sandbox_profile is not None else None
            ),
            "title": self.title,
        }


class BackgroundTaskStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self is not self.RUNNING


@dataclass(frozen=True, slots=True)
class BackgroundTaskSnapshot:
    task_id: str
    command: str
    cwd: str
    status: BackgroundTaskStatus
    output: str
    total_output_bytes: int
    truncated: bool
    exit_code: int | None
    started_at: datetime
    finished_at: datetime | None = None

    def to_dict(self, *, include_output: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "task_id": self.task_id,
            "command": self.command,
            "cwd": self.cwd,
            "status": self.status.value,
            "total_output_bytes": self.total_output_bytes,
            "truncated": self.truncated,
            "exit_code": self.exit_code,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }
        if include_output:
            payload["output"] = self.output
        return payload


class SessionTaskKind(StrEnum):
    PLAN_EXECUTION = "plan_execution"
    SUBAGENT = "subagent"


class SessionTaskStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self is not self.RUNNING


@dataclass(frozen=True, slots=True)
class SessionTask:
    task_id: str
    kind: SessionTaskKind
    status: SessionTaskStatus
    started_at: datetime
    finished_at: datetime | None = None
    plan_snapshot: SessionPlan | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "kind": self.kind.value,
            "status": self.status.value,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "plan": self.plan_snapshot.to_dict() if self.plan_snapshot is not None else None,
        }


# ─── Controller option/result types ─────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ProviderOption:
    name: str
    protocol: str
    model: str
    available: bool
    credential_configured: bool
    default: bool = False
    selected: bool = False
    context_window_tokens: int | None = None

    @property
    def selectable(self) -> bool:
        return self.available and self.credential_configured


@dataclass(frozen=True, slots=True)
class ProviderSelectionResult:
    profile_name: str
    provider_name: str
    model_name: str
    previous_session_id: str | None
    changed: bool
    stopped_background_tasks: int = 0
    context_window_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class ReasoningEffortSelectionResult:
    requested: ReasoningEffort
    effective: ReasoningEffort
    changed: bool
    workflow_orchestration_active: bool = False


@dataclass(frozen=True, slots=True)
class InteractionModeSelectionResult:
    requested: InteractionMode
    changed: bool
    auto_unrestricted: bool = False
    safety_classifier_active: bool = False

    @property
    def limited_auto(self) -> bool:
        return self.requested is InteractionMode.AUTO and not self.auto_unrestricted


@dataclass(frozen=True, slots=True)
class SessionOption:
    session_id: str
    source_provider: str
    source_model: str
    updated_at: datetime
    resume_profile: str
    current: bool
    source_profile_match: bool
    selectable: bool
    sandbox_profile: SandboxProfile | None = None
    sandbox_profile_match: bool = True
    title: str | None = None
    matched_fields: tuple[str, ...] = ()
    snippet: str | None = None


@dataclass(frozen=True, slots=True)
class SessionSelectionResult:
    session_id: str
    source_provider: str
    source_model: str
    profile_name: str
    provider_name: str
    model_name: str
    previous_session_id: str | None
    changed: bool
    source_profile_match: bool
    items: tuple[SessionItem, ...]
    stopped_background_tasks: int = 0
    context_window_tokens: int | None = None


# ─── Provider settings ──────────────────────────────────────────────────────────

_PROFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_MAX_PROFILES = 64
_MAX_MODEL_CHARACTERS = 512
_MAX_URL_CHARACTERS = 2_048
_MAX_API_KEY_CHARACTERS = 16_384
_SUPPORTED_PROTOCOLS = frozenset(
    {
        "openai-chat",
        "openai-responses",
        "anthropic-messages",
        "gemini-generate-content",
    }
)
_SUPPORTED_PROXY_MODES = frozenset({"environment", "direct", "explicit"})


class ProviderSettingsError(ValueError):
    """Invalid managed provider configuration or setting."""


@dataclass(frozen=True, slots=True)
class ManagedProviderProfile:
    name: str
    protocol: str
    model: str
    base_url: str
    dialect: str = "standard"
    proxy_mode: str = "environment"
    proxy_url_env: str | None = None
    api_key: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if _PROFILE_NAME.fullmatch(self.name) is None:
            raise ProviderSettingsError(
                "provider profile name must start with a letter or number and contain only "
                "letters, numbers, '.', '_' or '-' (maximum 64 characters)"
            )
        if self.protocol not in _SUPPORTED_PROTOCOLS:
            raise ProviderSettingsError(f"unsupported provider protocol: {self.protocol}")
        if self.dialect not in {"standard", "xai"}:
            raise ProviderSettingsError(f"unsupported provider dialect: {self.dialect}")
        if self.dialect == "xai" and self.protocol != "openai-responses":
            raise ProviderSettingsError("xAI dialect requires protocol 'openai-responses'")
        if not self.model.strip() or len(self.model) > _MAX_MODEL_CHARACTERS:
            raise ProviderSettingsError("provider model is invalid")
        if not self.base_url.strip() or len(self.base_url) > _MAX_URL_CHARACTERS:
            raise ProviderSettingsError("provider base URL is invalid")
        if self.proxy_mode not in _SUPPORTED_PROXY_MODES:
            raise ProviderSettingsError(
                "provider proxy_mode must be 'environment', 'direct', or 'explicit'"
            )
        if self.api_key is not None and not self.api_key.strip():
            object.__setattr__(self, "api_key", None)
        if self.api_key is not None and len(self.api_key) > _MAX_API_KEY_CHARACTERS:
            raise ProviderSettingsError("provider API key is too long")


@dataclass(frozen=True, slots=True)
class ManagedProviderSettings:
    profiles: tuple[ManagedProviderProfile, ...] = ()
    default_provider: str | None = None

    def __post_init__(self) -> None:
        if len(self.profiles) > _MAX_PROFILES:
            raise ProviderSettingsError("too many managed provider profiles")
        names = tuple(profile.name for profile in self.profiles)
        if len(names) != len(set(names)):
            raise ProviderSettingsError("managed provider profile names must be unique")
        if self.default_provider is not None and self.default_provider not in names:
            raise ProviderSettingsError("managed default provider does not exist")

    def profile(self, name: str) -> ManagedProviderProfile | None:
        return next((profile for profile in self.profiles if profile.name == name), None)


@dataclass(frozen=True, slots=True)
class ProviderConnectionSpec:
    """Credential-bearing, ephemeral input for a read-only provider probe."""

    protocol: str
    base_url: str
    api_key: str = field(repr=False)
    dialect: str = "standard"

    def __post_init__(self) -> None:
        if self.protocol not in _SUPPORTED_PROTOCOLS:
            raise ProviderSettingsError(f"unsupported provider protocol: {self.protocol}")
        if self.dialect not in {"standard", "xai"}:
            raise ProviderSettingsError(f"unsupported provider dialect: {self.dialect}")
        if self.dialect == "xai" and self.protocol != "openai-responses":
            raise ProviderSettingsError("xAI dialect requires protocol 'openai-responses'")
        base_url = self.base_url.strip()
        if not base_url or len(base_url) > _MAX_URL_CHARACTERS:
            raise ProviderSettingsError("provider base URL is invalid")
        object.__setattr__(self, "base_url", base_url.rstrip("/"))
        api_key = self.api_key.strip()
        if not api_key:
            raise ProviderSettingsError("provider connection test requires an API key")
        if len(api_key) > _MAX_API_KEY_CHARACTERS:
            raise ProviderSettingsError("provider API key is too long")
        object.__setattr__(self, "api_key", api_key)


@dataclass(frozen=True, slots=True)
class ProviderCatalogResult:
    models: tuple[str, ...]
    truncated: bool = False

    def __post_init__(self) -> None:
        if len(self.models) != len(set(self.models)):
            raise ValueError("provider model catalog contains duplicate identifiers")


class ProviderCatalogError(Exception):
    """Classified, redacted provider discovery failure for local UI recovery."""

    def __init__(
        self,
        kind: str,
        *,
        status_code: int | None = None,
        detail: str | None = None,
    ) -> None:
        self.kind = kind
        self.status_code = status_code
        self.detail = detail
        status = f" (HTTP {status_code})" if status_code is not None else ""
        suffix = f": {detail}" if detail else ""
        super().__init__(f"provider model discovery failed: {kind}{status}{suffix}")


# ─── HTTP client policy ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class HttpClientPolicy:
    """Resolved HTTPX client policy without secret-bearing representation."""

    trust_env: bool = True
    proxy_url: str | None = field(default=None, repr=False)
    redaction_values: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        if self.trust_env and self.proxy_url is not None:
            raise ValueError("an explicit proxy requires trust_env=False")
        values = tuple(dict.fromkeys(value for value in self.redaction_values if value))
        object.__setattr__(self, "redaction_values", values)

    def client_options(
        self,
        *,
        timeout: object,
        transport: object | None = None,
    ) -> dict[str, Any]:
        options: dict[str, Any] = {"timeout": timeout, "trust_env": self.trust_env}
        if self.proxy_url is not None:
            options["proxy"] = self.proxy_url
        if transport is not None:
            options["transport"] = transport
        return options

    def redact(self, detail: str, *additional_values: str, limit: int = 1_000) -> str:
        redacted = detail
        values = (*self.redaction_values, *additional_values)
        for value in sorted({value for value in values if value}, key=len, reverse=True):
            redacted = redacted.replace(value, "[REDACTED]")
        return redacted[:limit]


def resolve_http_client_policy(
    proxy_mode: str,
    proxy_url_env: str | None,
    environ: Mapping[str, str],
) -> HttpClientPolicy:
    """Build an HTTP client policy for a provider proxy setting."""
    if proxy_mode == "direct":
        return HttpClientPolicy(trust_env=False)
    if proxy_mode == "explicit":
        proxy_url = (environ.get(proxy_url_env or "", "") or "").strip() if proxy_url_env else ""
        if proxy_url:
            return HttpClientPolicy(trust_env=False, proxy_url=proxy_url)
        return HttpClientPolicy(trust_env=False)
    return HttpClientPolicy(trust_env=True)  # environment


# ─── Redaction ──────────────────────────────────────────────────────────────────

_SECRET_KEY = (
    r"(?:[a-z0-9.]+[_-])*(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|"
    r"secret|password|passwd|authorization|private[_-]?key)"
)
_QUOTED_SECRET_ASSIGNMENT = re.compile(
    rf"(?i)(?P<prefix>[\"']?{_SECRET_KEY}[\"']?\s*[:=]\s*)"
    r"(?P<quote>[\"'])(?P<value>[^\r\n]*?)(?P=quote)"
)
_UNQUOTED_SECRET_ASSIGNMENT = re.compile(
    rf"(?i)(?P<prefix>[\"']?{_SECRET_KEY}[\"']?\s*[:=]\s*)(?P<value>[^\s,;\"']+)"
)
_BEARER_CREDENTIAL = re.compile(r"(?i)(\bbearer\s+)[a-z0-9._~+/=-]{8,}")
_URL_PASSWORD = re.compile(r"(?i)(https?://[^/\s:@]+:)[^@\s/]+(@)")
_TOKEN_SHAPE = re.compile(r"\b(?:sk|xai|gh[pousr])[-_][a-z0-9_-]{8,}\b", re.IGNORECASE)


def redact_sensitive_text(text: str, *, explicit_values: Sequence[str] = ()) -> str:
    """Redact likely credentials from text intended for logs or the local UI."""
    redacted = text
    for value in dict.fromkeys(value for value in explicit_values if value):
        redacted = redacted.replace(value, "[REDACTED]")
    redacted = _URL_PASSWORD.sub(r"\1[REDACTED]\2", redacted)
    redacted = _BEARER_CREDENTIAL.sub(r"\1[REDACTED]", redacted)
    redacted = _TOKEN_SHAPE.sub("[REDACTED]", redacted)
    redacted = _QUOTED_SECRET_ASSIGNMENT.sub(
        lambda match: (
            f"{match.group('prefix')}{match.group('quote')}[REDACTED]{match.group('quote')}"
        ),
        redacted,
    )
    return _UNQUOTED_SECRET_ASSIGNMENT.sub(r"\g<prefix>[REDACTED]", redacted)


# ─── Controller protocols ───────────────────────────────────────────────────────


class ConversationRunner(Protocol):
    @property
    def session_id(self) -> str | None: ...

    async def run(self, prompt: str, *, sink: EventSink | None = None) -> AgentRunResult: ...


class ApprovalController(Protocol):
    def set_handler(self, handler: ApprovalHandler | None) -> None: ...


class ProviderController(Protocol):
    @property
    def profiles(self) -> tuple[ProviderOption, ...]: ...

    @property
    def selected_profile(self) -> str: ...

    async def select_profile(self, name: str) -> ProviderSelectionResult: ...


class ReasoningController(Protocol):
    @property
    def reasoning_effort(self) -> ReasoningEffort: ...

    @property
    def effective_reasoning_effort(self) -> ReasoningEffort: ...

    async def set_reasoning_effort(
        self,
        effort: ReasoningEffort,
    ) -> ReasoningEffortSelectionResult: ...


class InteractionModeController(Protocol):
    @property
    def interaction_mode(self) -> InteractionMode: ...

    @property
    def auto_mode_unrestricted(self) -> bool: ...

    async def set_interaction_mode(
        self,
        mode: InteractionMode,
    ) -> InteractionModeSelectionResult: ...


class SessionController(Protocol):
    async def list_sessions(self, query: str | None = None) -> tuple[SessionOption, ...]: ...

    async def select_session(self, session_id: str) -> SessionSelectionResult: ...

    async def rename_session(self, title: str) -> SessionSummary: ...


class TaskController(Protocol):
    async def list_background_tasks(self) -> tuple[BackgroundTaskSnapshot, ...]: ...


class SessionTaskController(Protocol):
    async def list_session_tasks(self) -> tuple[SessionTask, ...]: ...

    async def get_session_task(self, task_id: str) -> SessionTask | None: ...


class PlanController(Protocol):
    @property
    def plan(self) -> SessionPlan | None: ...

    async def add_plan_comment(self, step_index: int, content: str) -> PlanComment: ...

    async def list_plan_comments(self) -> tuple[PlanComment, ...]: ...

    async def execute_plan(self, *, sink: EventSink | None = None) -> AgentRunResult: ...


# ─── Persistence ports ──────────────────────────────────────────────────────────


class UiPreferencesStore(Protocol):
    async def load_language(self) -> UiLanguage: ...

    async def save_language(self, language: UiLanguage) -> None: ...

    async def load_reasoning_effort(self) -> ReasoningEffort: ...

    async def save_reasoning_effort(self, effort: ReasoningEffort) -> None: ...

    async def load_interaction_mode(self) -> InteractionMode: ...

    async def save_interaction_mode(self, mode: InteractionMode) -> None: ...


class ProviderSettingsStore(Protocol):
    async def load(self) -> ManagedProviderSettings: ...

    async def save_profile(
        self,
        profile: ManagedProviderProfile,
        *,
        make_default: bool = True,
    ) -> ManagedProviderSettings: ...

    async def set_default(self, name: str) -> ManagedProviderSettings: ...

    async def delete_profile(self, name: str) -> ManagedProviderSettings: ...


class ProviderCatalog(Protocol):
    async def discover_models(
        self,
        spec: ProviderConnectionSpec,
        *,
        http_policy: HttpClientPolicy,
    ) -> ProviderCatalogResult: ...


# ─── TUI value types ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ProviderPreset:
    name: str
    protocol: str
    dialect: str
    base_url: str


_PROVIDER_PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset("openai", "openai-responses", "standard", "https://api.openai.com/v1"),
    ProviderPreset("compatible", "openai-chat", "standard", ""),
    ProviderPreset("deepseek", "openai-chat", "standard", "https://api.deepseek.com"),
    ProviderPreset("anthropic", "anthropic-messages", "standard", "https://api.anthropic.com"),
    ProviderPreset(
        "gemini",
        "gemini-generate-content",
        "standard",
        "https://generativelanguage.googleapis.com/v1beta",
    ),
    ProviderPreset("xai", "openai-responses", "xai", "https://api.x.ai/v1"),
)


def _is_deepseek_base_url(value: str) -> bool:
    try:
        return urlsplit(value.strip()).hostname == "api.deepseek.com"
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class TranscriptEntry:
    category: str
    text: str
    ui_key: str | None = None
    ui_values: tuple[tuple[str, object], ...] = ()


@dataclass(slots=True)
class ToolFeedbackState:
    call_id: str
    name: str
    arguments: dict[str, Any]
    entry_index: int
    hosted: bool = False
    phase: str = "requested"
    permission_effect: str | None = None
    permission_reason: str | None = None
    approval_effect: str | None = None
    approval_outcome: str | None = None
    approval_reason: str | None = None
    duration: str | None = None
    content: str | None = None
    is_error: bool = False
    metadata: dict[str, Any] | None = None
    workspace_changes: dict[str, Any] | None = None
    expanded: bool = False


@dataclass(frozen=True, slots=True)
class ProviderSettingsSubmission:
    profile_name: str | None
    operation: str = "saved"


__all__ = [
    "AgentEvent",
    "AgentEventKind",
    "AgentRunResult",
    "ApprovalController",
    "ApprovalHandler",
    "BackgroundTaskSnapshot",
    "BackgroundTaskStatus",
    "ContentPart",
    "ContentPartKind",
    "ContextItemKind",
    "ConversationRunner",
    "EventSink",
    "HttpClientPolicy",
    "InteractionMode",
    "InteractionModeController",
    "InteractionModeSelectionResult",
    "ManagedProviderProfile",
    "ManagedProviderSettings",
    "Message",
    "PermissionApproval",
    "PermissionApprovalKind",
    "PermissionRequest",
    "PlanComment",
    "PlanController",
    "PlanStep",
    "PlanStepStatus",
    "PreservedContextItem",
    "ProviderCatalog",
    "ProviderCatalogError",
    "ProviderCatalogResult",
    "ProviderConnectionSpec",
    "ProviderController",
    "ProviderOption",
    "ProviderPreset",
    "ProviderSelectionResult",
    "ProviderSettingsError",
    "ProviderSettingsStore",
    "ProviderSettingsSubmission",
    "ReasoningController",
    "ReasoningEffort",
    "ReasoningEffortSelectionResult",
    "Role",
    "SandboxProfile",
    "SessionApprovalBroker",
    "SessionController",
    "SessionItem",
    "SessionOption",
    "SessionPlan",
    "SessionSelectionResult",
    "SessionSummary",
    "SessionTask",
    "SessionTaskController",
    "SessionTaskKind",
    "SessionTaskStatus",
    "SyntheticReason",
    "TaskController",
    "ToolCall",
    "ToolFeedbackState",
    "TranscriptEntry",
    "UiLanguage",
    "UiPreferencesStore",
    "build_permission_request",
    "estimate_context_tokens",
    "estimate_text_tokens",
    "redact_sensitive_text",
    "resolve_http_client_policy",
]
