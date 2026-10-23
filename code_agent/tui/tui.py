from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, ClassVar
from rich.console import RenderableType
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text
from rich.theme import Theme as RichTheme
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.geometry import Size
from textual.message import Message as TextualMessage
from textual.screen import ModalScreen
from textual.suggester import Suggester
from textual.theme import Theme
from textual.widgets import Button, Header, Input, Label, Static
from textual.worker import Worker

from code_agent.tui.commands import SlashCompletion, slash_completions
from code_agent.tui.domain import (
    AgentEvent,
    AgentEventKind,
    AgentRunResult,
    ApprovalController,
    ApprovalHandler,
    BackgroundTaskSnapshot,
    BackgroundTaskStatus,
    ConversationRunner,
    EventSink,
    HttpClientPolicy,
    InteractionMode,
    InteractionModeController,
    InteractionModeSelectionResult,
    ManagedProviderProfile,
    ManagedProviderSettings,
    Message,
    PermissionApproval,
    PermissionRequest,
    PlanComment,
    PlanController,
    ProviderCatalog,
    ProviderCatalogError,
    ProviderCatalogResult,
    ProviderConnectionSpec,
    ProviderController,
    ProviderOption,
    ProviderPreset,
    ProviderSelectionResult,
    ProviderSettingsStore,
    ProviderSettingsSubmission,
    ReasoningController,
    ReasoningEffort,
    ReasoningEffortSelectionResult,
    Role,
    SessionController,
    SessionItem,
    SessionOption,
    SessionSelectionResult,
    SessionSummary,
    SessionTask,
    SessionTaskController,
    TaskController,
    ToolFeedbackState,
    TranscriptEntry,
    UiLanguage,
    UiPreferencesStore,
    _PROVIDER_PRESETS,
    _is_deepseek_base_url,
    estimate_context_tokens,
    estimate_text_tokens,
    resolve_http_client_policy,
)
from code_agent.tui.widgets import (
    AssistantMarkdown,
    ConversationMessage,
    SlashCommandSuggester,
    ToolFeedbackMessage,
)
from code_agent.tui.text import language_name, ui_text
from code_agent.tui.rendering import ToolFeedbackRenderer
from code_agent.tui.screens import (
    LanguageSettingsScreen,
    PermissionApprovalScreen,
    ProviderSelectionScreen,
    ProviderSettingsScreen,
    ProviderSetupApp,
    ReasoningEffortScreen,
    SessionSelectionScreen,
    SettingsScreen,
)
from code_agent.tui.screens.provider import _EVIL_CODE_THEME
from code_agent.tui.screens.reasoning import _EFFORT_COLORS

logger = logging.getLogger(__name__)

_RESTORED_MESSAGE_LIMIT = 20_000
_TASK_LIST_LIMIT = 20
_TASK_POLL_SECONDS = 0.5
_TERMINAL_SIZE_POLL_SECONDS = 0.25
_LOADING_ANIMATION_TICK_SECONDS = 0.05
_COMMAND_HINT_LIMIT = 5
TUI_RELOAD_PROVIDER_SETTINGS = 75

_MODE_COLORS = {
    InteractionMode.NORMAL: "#9aa3b2",
    InteractionMode.ACCEPT_EDITS: "#78c2a4",
    InteractionMode.PLAN: "#8b9cff",
    InteractionMode.AUTO: "#d58cc8",
}


@dataclass(slots=True)
class CollapsingPulseAnimation:
    """Textual-friendly port of the user-supplied collapsing pulse demo."""

    width: int = 7
    level_by_distance: tuple[int, ...] = (7, 5, 3, 1)
    peak_position: int = 0
    direction: int = 1
    merged_trail_count: int = 0
    phase: str = "moving"

    @property
    def delay_seconds(self) -> float:
        return {
            "moving": 0.09,
            "edge-hold": 0.14,
            "collapsing": 0.10,
            "merged-hold": 0.22,
        }.get(self.phase, 0.09)

    def reset(self) -> None:
        self.peak_position = 0
        self.direction = 1
        self.merged_trail_count = 0
        self.phase = "moving"

    def advance(self) -> None:
        trail_length = max(0, len(self.level_by_distance) - 1)
        if self.phase == "moving":
            self.peak_position += self.direction
            self.merged_trail_count = 0
            reached_edge = (self.direction == 1 and self.peak_position >= self.width - 1) or (
                self.direction == -1 and self.peak_position <= 0
            )
            if reached_edge:
                self.peak_position = max(0, min(self.width - 1, self.peak_position))
                self.phase = "edge-hold"
            return
        if self.phase == "edge-hold":
            if trail_length == 0:
                self.phase = "merged-hold"
                return
            self.merged_trail_count = 1
            self.phase = "merged-hold" if self.merged_trail_count >= trail_length else "collapsing"
            return
        if self.phase == "collapsing":
            self.merged_trail_count = min(
                trail_length,
                self.merged_trail_count + 1,
            )
            if self.merged_trail_count >= trail_length:
                self.phase = "merged-hold"
            return
        if self.phase == "merged-hold":
            self.direction *= -1
            self.peak_position += self.direction
            self.merged_trail_count = 0
            self.phase = "moving"
            return
        self.reset()

    def levels(self) -> tuple[int, ...]:
        trail = self.level_by_distance[1 + self.merged_trail_count :]
        rendered: list[int] = []
        for position in range(self.width):
            if position == self.peak_position:
                rendered.append(self.level_by_distance[0])
                continue
            if self.direction == 1:
                distance = self.peak_position - position
            else:
                distance = position - self.peak_position
            trail_index = distance - 1
            rendered.append(trail[trail_index] if 0 <= trail_index < len(trail) else 0)
        return tuple(rendered)


_MARKDOWN_THEME = RichTheme(
    {
        "markdown.paragraph": "#d8dadd",
        "markdown.text": "#d8dadd",
        "markdown.em": "italic #b8bcc2",
        "markdown.strong": "bold #aebcff",
        "markdown.code": "bold #9cc4cc on #1a1c1f",
        "markdown.code_block": "#d8dadd on #16181b",
        "markdown.block_quote": "italic #9aa2ad",
        "markdown.list": "#d8dadd",
        "markdown.item": "#d8dadd",
        "markdown.item.bullet": "bold #6fc3df",
        "markdown.item.number": "bold #6fc3df",
        "markdown.hr": "#3b3f44",
        "markdown.h1": "bold #b6c2ff",
        "markdown.h2": "bold #9eafff",
        "markdown.h3": "bold #8b9cff",
        "markdown.h4": "bold #8ed1e6",
        "markdown.h5": "bold #9db4c8",
        "markdown.h6": "bold #989da4",
        "markdown.link": "underline #7da7d9",
        "markdown.link_url": "underline #7da7d9",
        "markdown.table.border": "#50555b",
        "markdown.table.header": "bold #9eafff",
        "markdown.kbd": "bold #aebcff on #292c30",
    }
)


def _read_terminal_size() -> Size | None:
    """Read the real TTY viewport without trusting possibly stale shell variables."""

    for stream in (sys.__stdin__, sys.__stderr__, sys.__stdout__):
        if stream is None:
            continue
        try:
            terminal_size = os.get_terminal_size(stream.fileno())
        except (AttributeError, OSError, ValueError):
            continue
        if terminal_size.columns > 0 and terminal_size.lines > 0:
            return Size(terminal_size.columns, terminal_size.lines)
    return None


class EvilCodeApp(App[None]):
    """Minimal Textual interface over the normalized agent event stream."""

    TITLE = "Evil Code"
    SUB_TITLE = "Terminal coding agent"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen {
        layout: vertical;
        width: 100%;
        height: 100%;
        background: #101214;
        color: #d8dadd;
    }

    Header {
        background: #16181b;
        color: #d8dadd;
    }

    HeaderIcon {
        display: none;
    }

    HeaderTitle {
        padding-left: 2;
        content-align: left middle;
    }

    HeaderClock {
        background: #1a1c1f;
        color: #989da4;
    }

    #transcript {
        width: 100%;
        height: 1fr;
        padding: 1 2;
        background: #101214;
        color: #d8dadd;
        border-bottom: solid #303338;
    }

    .conversation-message {
        width: 100%;
        height: auto;
        min-height: 1;
        margin-bottom: 1;
        padding: 0;
        color: #d8dadd;
    }

    .message-user {
        padding: 1 2;
        background: #292c30;
        color: #e3e5e8;
        border-left: solid #5866a3;
    }

    .message-assistant {
        padding: 0 2;
        background: #101214;
        color: #e1e3e6;
    }

    .message-pending {
        color: #8e939a;
        text-style: italic;
    }

    .message-system {
        padding: 0 1;
        color: #aebcff;
    }

    .message-tool {
        padding: 0 1;
        color: #78c2a4;
    }

    .message-tool.tool-interactive:hover,
    .message-tool.tool-interactive:focus {
        background: #161a1c;
        border-left: solid #5866a3;
    }

    .message-status {
        padding: 0 1;
        color: #8e939a;
    }

    .message-error {
        padding: 0 1;
        color: #c76d6d;
    }

    #runtime-bar {
        width: 100%;
        height: 1;
        padding: 0 2;
        background: #16181b;
        color: #a9adb3;
    }

    #runtime-model {
        width: 1fr;
        height: 1;
        overflow: hidden hidden;
    }

    #runtime-workspace {
        width: 1fr;
        height: 1;
        padding-left: 2;
        text-align: right;
        overflow: hidden hidden;
    }

    #runtime-context {
        width: auto;
        max-width: 20;
        height: 1;
        padding-left: 2;
        overflow: hidden hidden;
    }

    #runtime-effort {
        width: auto;
        max-width: 34;
        height: 1;
        padding-left: 2;
        overflow: hidden hidden;
    }

    #runtime-mode {
        width: auto;
        max-width: 30;
        height: 1;
        padding-left: 2;
        overflow: hidden hidden;
    }

    #runtime-sandbox {
        width: auto;
        max-width: 18;
        height: 1;
        padding-left: 2;
        overflow: hidden hidden;
    }

    #prompt {
        height: 3;
        margin: 0 1;
        background: #1a1c1f;
        color: #d8dadd;
        border: tall #3b3f44;
    }

    #prompt:focus {
        background: #1d1f22;
        background-tint: #ffffff 2%;
        border: tall #5866a3;
    }

    #command-hints {
        display: none;
        width: 100%;
        height: auto;
        max-height: 3;
        padding: 0 2;
        background: #141619;
        color: #a9adb3;
        overflow: hidden hidden;
    }

    #shortcut-bar {
        height: 1;
        padding: 0 1;
        background: #141619;
        color: #a9adb3;
    }

    #provider-options Button:focus,
    #session-options Button:focus,
    #effort-options Button:focus,
    #settings-languages Button:focus {
        background: #292c30;
        color: #e1e3e6;
        border-left: solid #5866a3;
    }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel_turn", "Cancel", show=False),
        Binding("ctrl+c", "cancel_turn", "Cancel", priority=True, show=False),
        Binding("ctrl+p", "select_provider", "Provider", priority=True, show=False),
        Binding("ctrl+r", "select_session", "Sessions", priority=True, show=False),
        Binding("ctrl+e", "select_reasoning_effort", "Effort", priority=True, show=False),
        Binding("ctrl+comma", "open_settings", "Settings", priority=True, show=False),
        Binding("ctrl+q", "quit", "Quit", show=False),
        Binding("ctrl+l", "clear_transcript", "Clear", show=False),
        Binding(
            "shift+tab",
            "cycle_interaction_mode",
            "Mode",
            priority=True,
            show=False,
        ),
        Binding("tab", "complete_slash_command", "Complete", priority=True, show=False),
    ]

    def __init__(
        self,
        runner: ConversationRunner,
        *,
        approval_controller: ApprovalController | None = None,
        provider_controller: ProviderController | None = None,
        reasoning_controller: ReasoningController | None = None,
        interaction_mode_controller: InteractionModeController | None = None,
        session_controller: SessionController | None = None,
        task_controller: TaskController | None = None,
        session_task_controller: SessionTaskController | None = None,
        plan_controller: PlanController | None = None,
        ui_preferences: UiPreferencesStore | None = None,
        provider_settings_store: ProviderSettingsStore | None = None,
        provider_catalog: ProviderCatalog | None = None,
        managed_provider_settings: ManagedProviderSettings | None = None,
        language: UiLanguage = UiLanguage.SIMPLIFIED_CHINESE,
        initial_items: Sequence[SessionItem] = (),
        provider_name: str,
        model_name: str,
        cwd: Path,
        reasoning_effort: ReasoningEffort = ReasoningEffort.HIGH,
        interaction_mode: InteractionMode = InteractionMode.NORMAL,
        context_window_tokens: int | None = None,
        sandbox_label: str | None = None,
        sandbox_manager=None,
        draft_manager=None,
    ) -> None:
        if context_window_tokens is not None and context_window_tokens <= 0:
            raise ValueError("context window tokens must be positive")
        super().__init__()
        self.register_theme(_EVIL_CODE_THEME)
        self.theme = _EVIL_CODE_THEME.name
        self._runner = runner
        self._approval_controller = approval_controller
        self._provider_controller = provider_controller
        if context_window_tokens is None and provider_controller is not None:
            selected_profile = provider_controller.selected_profile
            selected_option = next(
                (
                    option
                    for option in provider_controller.profiles
                    if option.name == selected_profile
                ),
                None,
            )
            if selected_option is not None:
                context_window_tokens = selected_option.context_window_tokens
        if context_window_tokens is not None and context_window_tokens <= 0:
            raise ValueError("context window tokens must be positive")
        if reasoning_controller is None and all(
            hasattr(provider_controller, name)
            for name in (
                "reasoning_effort",
                "effective_reasoning_effort",
                "set_reasoning_effort",
            )
        ):
            reasoning_controller = provider_controller  # type: ignore[assignment]
        self._reasoning_controller = reasoning_controller
        if interaction_mode_controller is None and all(
            hasattr(provider_controller, name)
            for name in (
                "interaction_mode",
                "auto_mode_unrestricted",
                "set_interaction_mode",
            )
        ):
            interaction_mode_controller = provider_controller  # type: ignore[assignment]
        self._interaction_mode_controller = interaction_mode_controller
        self._session_controller = session_controller
        self._task_controller = task_controller
        self._session_task_controller = session_task_controller
        self._plan_controller = plan_controller
        self._ui_preferences = ui_preferences
        self._provider_settings_store = provider_settings_store
        self._provider_catalog = provider_catalog
        self._managed_provider_settings = managed_provider_settings
        self._language = language
        self._initial_items = tuple(initial_items)
        self._provider_name = provider_name
        self._model_name = model_name
        self._reasoning_effort = (
            reasoning_controller.reasoning_effort
            if reasoning_controller is not None
            else reasoning_effort
        )
        self._sandbox_label = sandbox_label
        self._sandbox_manager = sandbox_manager
        self._draft_manager = draft_manager
        self._effective_reasoning_effort = (
            reasoning_controller.effective_reasoning_effort
            if reasoning_controller is not None
            else self._reasoning_effort.effective
        )
        self._interaction_mode = (
            interaction_mode_controller.interaction_mode
            if interaction_mode_controller is not None
            else interaction_mode
        )
        self._auto_mode_unrestricted = (
            interaction_mode_controller.auto_mode_unrestricted
            if interaction_mode_controller is not None
            else False
        )
        self._cwd = cwd
        self._context_window_tokens = context_window_tokens
        self._context_used_tokens = estimate_context_tokens(self._initial_items)
        self._context_usage_estimated = True
        self._plan = plan_controller.plan if plan_controller is not None else None
        self._entries: list[TranscriptEntry] = []
        self._entry_widgets: list[ConversationMessage] = []
        self._tool_feedback_by_call: dict[tuple[bool, str], ToolFeedbackState] = {}
        self._tool_feedback_by_entry: dict[int, ToolFeedbackState] = {}
        self._tool_feedback_renderer = ToolFeedbackRenderer(lambda: self._language)
        self._assistant_parts: list[str] = []
        self._pending_assistant: ConversationMessage | None = None
        self._reasoning_announced = False
        self._turn_completion: tuple[str, int] | None = None
        self._turn_usage_reported = False
        self._turn_worker: Worker[None] | None = None
        self._model_loading = False
        self._loading_animation = CollapsingPulseAnimation()
        self._loading_animation_elapsed = 0.0
        self._announced_terminal_tasks: set[str] = set()
        self._task_polling = False

    @property
    def entries(self) -> tuple[TranscriptEntry, ...]:
        return tuple(self._entries)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True, icon="")
        yield VerticalScroll(id="transcript")
        yield Horizontal(
            Static(id="runtime-model"),
            Static(id="runtime-workspace"),
            Static(id="runtime-context"),
            Static(id="runtime-effort"),
            Static(id="runtime-mode"),
            Static(id="runtime-sandbox"),
            id="runtime-bar",
        )
        yield Input(
            placeholder=ui_text(self._language, "prompt.placeholder"),
            suggester=SlashCommandSuggester(self._slash_completions),
            id="prompt",
        )
        yield Static(id="command-hints")
        yield Static(ui_text(self._language, "shortcuts"), id="shortcut-bar")

    def on_mount(self) -> None:
        self.console.push_theme(_MARKDOWN_THEME)
        self._apply_language_to_chrome()
        if self._approval_controller is not None:
            self._approval_controller.set_handler(self._request_approval)
        if self._runner.session_id is not None:
            self._replace_transcript(self._initial_items)
            self._write_ui_entry(
                "system",
                "startup.resumed",
                session_id=self._runner.session_id or ui_text(self._language, "value.unknown"),
                provider=self._provider_name,
                model=self._model_name,
                cwd=self._cwd,
            )
        else:
            self._write_ui_entry(
                "system",
                "startup.ready",
                provider=self._provider_name,
                model=self._model_name,
                cwd=self._cwd,
            )
        if self._task_controller is not None:
            self.set_interval(_TASK_POLL_SECONDS, self._poll_background_tasks)
        self.set_interval(
            _LOADING_ANIMATION_TICK_SECONDS,
            self._advance_model_loading_animation,
        )
        if not self.is_headless and not self.is_inline and not self.is_web:
            self.set_interval(_TERMINAL_SIZE_POLL_SECONDS, self._synchronize_terminal_size)
        self.query_one("#prompt", Input).focus()

    def _synchronize_terminal_size(self) -> None:
        """Recover when a terminal drops its normal resize notification."""

        terminal_size = _read_terminal_size()
        if terminal_size is None or terminal_size == self.screen.size:
            return
        self.post_message(events.Resize(terminal_size, terminal_size, terminal_size))

    def on_unmount(self) -> None:
        self._model_loading = False
        if self._approval_controller is not None:
            self._approval_controller.set_handler(None)
        self.console.pop_theme()

    async def _request_approval(self, request: PermissionRequest) -> PermissionApproval:
        return await self.push_screen_wait(
            PermissionApprovalScreen(request, language=self._language)
        )

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        event.input.value = ""
        if not prompt:
            return
        if prompt.startswith("/"):
            await self._dispatch_slash_command(prompt)
            return
        if self._turn_worker is not None and self._turn_worker.is_running:
            self._write_ui_entry("error", "turn.running")
            return

        self._submit_prompt(prompt)

    def _submit_prompt(self, prompt: str) -> None:
        if self._turn_worker is not None and self._turn_worker.is_running:
            self._write_ui_entry("error", "turn.running")
            return
        self._write_entry("user", prompt)
        self._context_used_tokens += 4 + estimate_text_tokens(prompt)
        self._context_usage_estimated = True
        self._refresh_runtime_bar()
        self._assistant_parts.clear()
        self._reasoning_announced = False
        self._turn_completion = None
        self._turn_usage_reported = False
        self._begin_pending_assistant()
        self._turn_worker = self.run_worker(
            self._run_prompt(prompt),
            name="agent-turn",
            group="agent",
            exclusive=True,
            exit_on_error=False,
        )

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "prompt" and event.input.screen is self.screen:
            self._refresh_command_hints(event.value)

    def on_tool_feedback_message_toggle_requested(
        self,
        event: ToolFeedbackMessage.ToggleRequested,
    ) -> None:
        state = self._tool_feedback_by_entry.get(event.card.entry_index)
        if state is None or not self._tool_feedback_renderer.tool_details_available(
            state,
            self._tool_feedback_renderer.tool_change_report(state),
        ):
            return
        state.expanded = not state.expanded
        self._refresh_tool_feedback(state)
        event.card.focus()

    def action_complete_slash_command(self) -> None:
        if isinstance(self.screen, ModalScreen):
            self.screen.focus_next()
            return
        prompt = self.query_one("#prompt", Input)
        if not prompt.has_focus:
            self.screen.focus_next()
            return
        if not prompt.value.startswith("/"):
            self.screen.focus_next()
            return
        completions = self._slash_completions(prompt.value)
        if not completions:
            return
        completed = completions[0].value
        if completed == prompt.value:
            return
        prompt.value = completed
        prompt.cursor_position = len(completed)

    async def _run_prompt(self, prompt: str) -> None:
        await self._run_agent_turn(lambda: self._runner.run(prompt, sink=self._handle_event))

    async def _run_plan_execution(self) -> None:
        controller = self._plan_controller
        if controller is None:
            self._write_ui_entry("error", "plan.execution_unavailable")
            return
        await self._run_agent_turn(lambda: controller.execute_plan(sink=self._handle_event))

    async def _run_agent_turn(
        self,
        run: Callable[[], Awaitable[AgentRunResult]],
    ) -> None:
        prompt_input = self.query_one("#prompt", Input)
        try:
            result = await run()
            response = result.response or ui_text(self._language, "turn.no_response")
            if not self._turn_usage_reported:
                self._context_used_tokens = (
                    estimate_context_tokens(result.items)
                    if result.items
                    else self._context_used_tokens + 4 + estimate_text_tokens(response)
                )
                self._context_usage_estimated = True
                self._refresh_runtime_bar()
            self._finish_pending_assistant(response)
            if self._turn_completion is not None:
                duration, steps = self._turn_completion
                self._write_ui_entry(
                    "status",
                    "turn.completed",
                    duration=duration,
                    steps=steps,
                )
        except asyncio.CancelledError:
            await self._discard_pending_assistant()
            self._write_ui_entry("status", "turn.cancelled")
            raise
        except Exception as error:
            await self._discard_pending_assistant()
            self._write_entry("error", f"{type(error).__name__}: {error}")
        finally:
            self._stop_model_loading()
            prompt_input.focus()

    async def _handle_event(self, event: AgentEvent) -> None:
        data = event.data
        if event.kind is AgentEventKind.TEXT_DELTA:
            text = data.get("text")
            if isinstance(text, str):
                self._assistant_parts.append(text)
                self._update_pending_assistant("".join(self._assistant_parts))
        elif event.kind is AgentEventKind.REASONING_DELTA and not self._reasoning_announced:
            self._reasoning_announced = True
            self._write_ui_entry("status", "turn.reasoning")
        elif event.kind is AgentEventKind.MODEL_THINKING_COMPLETED:
            self._write_ui_entry(
                "status",
                "turn.thinking_completed",
                duration=self._event_duration(data),
                step=self._positive_int(data.get("step"), fallback=1),
            )
        elif event.kind is AgentEventKind.CONTEXT_USAGE_UPDATED:
            used_tokens = data.get("used_tokens")
            if isinstance(used_tokens, int) and not isinstance(used_tokens, bool):
                self._context_used_tokens = max(0, used_tokens)
                self._context_usage_estimated = data.get("estimated") is not False
                self._turn_usage_reported = not self._context_usage_estimated
                self._refresh_runtime_bar()
        elif event.kind is AgentEventKind.PROVIDER_ATTEMPT_FAILED:
            provider = self._field(data, "provider")
            message = self._field(data, "message")
            self._write_ui_entry(
                "error",
                "provider.failed",
                provider=provider,
                message=message,
            )
        elif event.kind is AgentEventKind.PROVIDER_SELECTED:
            provider = self._field(data, "provider")
            model = self._field(data, "model")
            self._provider_name = provider
            self._model_name = model
            context_window_tokens = data.get("context_window_tokens")
            self._context_window_tokens = (
                context_window_tokens
                if isinstance(context_window_tokens, int)
                and not isinstance(context_window_tokens, bool)
                and context_window_tokens > 0
                else None
            )
            self._refresh_runtime_bar()
            key = (
                "provider.fallback_selected"
                if data.get("failover") is True
                else "provider.selected"
            )
            self._write_ui_entry("status", key, provider=provider, model=model)
        elif event.kind in {
            AgentEventKind.BACKEND_TOOL_STARTED,
            AgentEventKind.BACKEND_TOOL_COMPLETED,
            AgentEventKind.TOOL_REQUESTED,
            AgentEventKind.TOOL_PERMISSION,
            AgentEventKind.TOOL_APPROVAL_REQUESTED,
            AgentEventKind.TOOL_APPROVAL_RESOLVED,
            AgentEventKind.TOOL_STARTED,
            AgentEventKind.TOOL_COMPLETED,
            AgentEventKind.TOOL_FAILED,
        }:
            self._handle_tool_feedback_event(event)
        elif event.kind is AgentEventKind.PLAN_UPDATED:
            try:
                self._plan = SessionPlan.from_dict(data)
            except ValueError:
                return
        elif event.kind is AgentEventKind.PLAN_EXECUTION_REQUESTED:
            self._write_ui_entry("status", "plan.execution_requested")
        elif event.kind is AgentEventKind.TURN_COMPLETED:
            self._turn_completion = (
                self._event_duration(data),
                self._positive_int(data.get("step"), fallback=1),
            )

    def _handle_tool_feedback_event(self, event: AgentEvent) -> None:
        hosted = event.kind in {
            AgentEventKind.BACKEND_TOOL_STARTED,
            AgentEventKind.BACKEND_TOOL_COMPLETED,
        }
        starts_card = event.kind in {
            AgentEventKind.BACKEND_TOOL_STARTED,
            AgentEventKind.TOOL_REQUESTED,
        }
        state = (
            self._start_tool_feedback(event, hosted=hosted)
            if starts_card
            else self._find_or_start_tool_feedback(event, hosted=hosted)
        )
        data = event.data
        if event.kind is AgentEventKind.BACKEND_TOOL_STARTED:
            state.phase = "running"
        elif event.kind is AgentEventKind.BACKEND_TOOL_COMPLETED:
            state.phase = "completed"
            state.duration = self._event_duration(data)
        elif event.kind is AgentEventKind.TOOL_PERMISSION:
            state.permission_effect = self._optional_text(data.get("effect"))
            state.permission_reason = self._optional_text(data.get("reason"))
            if state.permission_effect == "deny":
                state.phase = "permission_denied"
            elif state.permission_effect == "ask":
                state.phase = "approval_required"
            else:
                state.phase = "permitted"
        elif event.kind is AgentEventKind.TOOL_APPROVAL_REQUESTED:
            state.phase = "awaiting_approval"
        elif event.kind is AgentEventKind.TOOL_APPROVAL_RESOLVED:
            state.approval_effect = self._optional_text(data.get("effect"))
            state.approval_outcome = self._optional_text(data.get("outcome"))
            state.approval_reason = self._optional_text(data.get("reason"))
            state.phase = (
                "approval_denied" if state.approval_effect == "deny" else "approval_resolved"
            )
        elif event.kind is AgentEventKind.TOOL_STARTED:
            state.phase = "running"
        elif event.kind in {AgentEventKind.TOOL_COMPLETED, AgentEventKind.TOOL_FAILED}:
            state.phase = "failed" if event.kind is AgentEventKind.TOOL_FAILED else "completed"
            state.duration = self._event_duration(data)
            state.content = self._optional_text(data.get("content"), allow_empty=True)
            state.is_error = (
                event.kind is AgentEventKind.TOOL_FAILED or data.get("is_error") is True
            )
            raw_metadata = data.get("metadata")
            state.metadata = dict(raw_metadata) if isinstance(raw_metadata, Mapping) else None
            raw_changes = data.get("workspace_changes")
            state.workspace_changes = (
                dict(raw_changes) if isinstance(raw_changes, Mapping) else None
            )
            state.expanded = (
                state.is_error
                or state.workspace_changes is not None
                or state.name in {"apply_patch", "search_replace"}
            )
        self._refresh_tool_feedback(state)

    def _start_tool_feedback(
        self,
        event: AgentEvent,
        *,
        hosted: bool,
    ) -> ToolFeedbackState:
        data = event.data
        call_id = self._tool_event_id(event)
        raw_arguments = data.get("arguments")
        arguments = dict(raw_arguments) if isinstance(raw_arguments, Mapping) else {}
        state = ToolFeedbackState(
            call_id=call_id,
            name=self._field(data, "name"),
            arguments=arguments,
            entry_index=len(self._entries),
            hosted=hosted,
        )
        self._tool_feedback_by_call[(hosted, call_id)] = state
        self._tool_feedback_by_entry[state.entry_index] = state
        content = self._tool_feedback_renderer.render_body(state).plain
        self._write_entry("tool", content, tool_state=state)
        return state

    def _find_or_start_tool_feedback(
        self,
        event: AgentEvent,
        *,
        hosted: bool,
    ) -> ToolFeedbackState:
        raw_id = event.data.get("id")
        if isinstance(raw_id, str) and raw_id:
            state = self._tool_feedback_by_call.get((hosted, raw_id))
            if state is not None:
                return state
        name = self._optional_text(event.data.get("name"))
        candidates = (
            state
            for state in self._tool_feedback_by_entry.values()
            if state.hosted is hosted
            and (name is None or state.name == name)
            and state.phase not in {"completed", "failed", "permission_denied", "approval_denied"}
        )
        latest = max(candidates, key=lambda state: state.entry_index, default=None)
        return latest if latest is not None else self._start_tool_feedback(event, hosted=hosted)

    @staticmethod
    def _tool_event_id(event: AgentEvent) -> str:
        raw_id = event.data.get("id")
        return raw_id if isinstance(raw_id, str) and raw_id else f"event-{event.sequence}"

    @staticmethod
    def _optional_text(value: object, *, allow_empty: bool = False) -> str | None:
        if not isinstance(value, str):
            return None
        if value or allow_empty:
            return value
        return None

    def _refresh_tool_feedback(self, state: ToolFeedbackState) -> None:
        if state.entry_index >= len(self._entries):
            return
        transcript = self.query_one("#transcript", VerticalScroll)
        follow = transcript.is_vertical_scroll_end
        body = self._tool_feedback_renderer.render_body(state)
        self._entries[state.entry_index] = TranscriptEntry("tool", body.plain)
        widget = self._entry_widgets[state.entry_index]
        widget.update(self._render_tool_feedback(state, body=body))
        if isinstance(widget, ToolFeedbackMessage):
            self._tool_feedback_renderer.configure_widget(widget, state)
        if follow:
            transcript.scroll_end(animate=False)

    def _field(self, data: Mapping[str, Any], name: str) -> str:
        value = data.get(name)
        if isinstance(value, str) and value:
            return value
        return ui_text(self._language, "value.unknown")

    @staticmethod
    def _positive_int(value: object, *, fallback: int) -> int:
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
        return fallback

    @classmethod
    def _event_duration(cls, data: Mapping[str, Any]) -> str:
        value = data.get("duration_seconds")
        if not isinstance(value, int | float) or isinstance(value, bool) or value < 0:
            return "—"
        seconds = float(value)
        if seconds < 0.001:
            return "<1ms"
        if seconds < 1:
            return f"{seconds * 1000:.0f}ms"
        if seconds < 60:
            return f"{seconds:.1f}s"
        minutes, remainder = divmod(round(seconds), 60)
        return f"{minutes}m {remainder:02d}s"

    async def _dispatch_slash_command(self, raw: str) -> None:
        command, _, arguments = raw[1:].partition(" ")
        command = command.casefold()
        if command == "plan":
            description = arguments.strip()
            await self._apply_interaction_mode(InteractionMode.PLAN)
            if description and self._interaction_mode is InteractionMode.PLAN:
                self._submit_prompt(description)
            return
        if command in {"view-plan", "show-plan"}:
            if arguments.strip():
                self._write_ui_entry("error", "command.arguments", command=command)
                return
            await self._show_plan()
            return
        if command in {"comment-plan", "plan-comment"}:
            await self._add_plan_comment(arguments)
            return
        if command in {"execute-plan", "run-plan"}:
            if arguments.strip():
                self._write_ui_entry("error", "command.arguments", command=command)
                return
            await self._execute_plan()
            return
        if command == "mode":
            mode_value = arguments.strip()
            if not mode_value:
                self._write_ui_entry(
                    "system",
                    "mode.current",
                    mode=self._interaction_mode.value,
                    modes=", ".join(mode.value for mode in InteractionMode),
                )
                return
            try:
                mode = InteractionMode(mode_value.casefold())
            except ValueError:
                self._write_ui_entry(
                    "error",
                    "mode.invalid",
                    value=mode_value,
                    modes=", ".join(mode.value for mode in InteractionMode),
                )
                return
            await self._apply_interaction_mode(mode)
            return
        if command in {"effort", "reasoning"}:
            effort_value = arguments.strip()
            if not effort_value:
                await self._select_reasoning_effort(None)
                return
            try:
                effort = ReasoningEffort(effort_value.casefold())
            except ValueError:
                self._write_ui_entry(
                    "error",
                    "effort.invalid",
                    value=effort_value,
                    levels=", ".join(effort.value for effort in ReasoningEffort),
                )
                return
            await self._select_reasoning_effort(effort)
            return
        if command in {"model", "provider"}:
            await self._select_provider(arguments.strip() or None)
            return
        if command in {"resume", "sessions"}:
            requested_session = arguments.strip() or None
            if command == "sessions":
                await self._select_session(None, query=requested_session)
            else:
                await self._select_session(requested_session)
            return
        if command in {"rename", "title"}:
            await self._rename_session(arguments)
            return
        if command == "tasks":
            if arguments.strip():
                self._write_ui_entry("error", "command.tasks_arguments")
                return
            await self._show_tasks()
            return
        if command == "view-task":
            await self._show_session_task(arguments.strip())
            return
        if command in {"setting", "settings"}:
            if arguments.strip():
                self._write_ui_entry("error", "command.arguments", command=command)
                return
            await self.action_open_settings()
            return
        if command == "sandbox":
            await self._run_sandbox_command(arguments.strip())
            return
        if command == "draft":
            await self._run_draft_command(arguments.strip())
            return
        if arguments.strip():
            self._write_ui_entry("error", "command.arguments", command=command)
            return
        if command in {"quit", "exit"}:
            self.exit()
        elif command == "cancel":
            self.action_cancel_turn()
        elif command == "clear":
            self.action_clear_transcript()
        elif command == "help":
            self._write_ui_entry("system", "command.help")
        elif command == "status":
            session_id = self._runner.session_id or ui_text(self._language, "command.not_created")
            profile = (
                ui_text(
                    self._language,
                    "command.profile",
                    profile=self._provider_controller.selected_profile,
                )
                if self._provider_controller is not None
                else ""
            )
            self._write_ui_entry(
                "system",
                "command.status",
                provider=self._provider_name,
                model=self._model_name,
                effort=self._reasoning_effort_summary(),
                context=self._context_usage_summary(),
                mode=self._interaction_mode_summary(),
                session=session_id,
                profile=profile,
                cwd=self._cwd,
            )
        else:
            self._write_ui_entry("error", "command.unknown", command=command)

    def action_clear_transcript(self) -> None:
        # [2026-08-04] /clear now resets the underlying conversation too,
        # not just the rendered transcript (previously the agent kept the
        # full history and the context counter kept climbing). Matches the
        # REPL /clear semantics of starting a fresh conversation.
        try:
            session = self._runner.session
            reset = getattr(session, "reset", None)
            if callable(reset):
                reset()
        except Exception:
            logger.warning("Failed to reset session on /clear", exc_info=True)
        self._context_used_tokens = 0
        transcript = self.query_one("#transcript", VerticalScroll)
        transcript.remove_children(tuple(self._entry_widgets))
        self._entries.clear()
        self._entry_widgets.clear()
        self._tool_feedback_by_call.clear()
        self._tool_feedback_by_entry.clear()
        self._write_ui_entry("system", "transcript.cleared")

    def action_cancel_turn(self) -> None:
        if isinstance(self.screen, PermissionApprovalScreen):
            self.screen.action_deny()
            return
        if isinstance(self.screen, ProviderSelectionScreen):
            self.screen.action_cancel()
            return
        if isinstance(self.screen, ReasoningEffortScreen):
            self.screen.action_cancel()
            return
        if isinstance(self.screen, SessionSelectionScreen):
            self.screen.action_cancel()
            return
        if isinstance(
            self.screen,
            (SettingsScreen, LanguageSettingsScreen, ProviderSettingsScreen),
        ):
            self.screen.action_cancel()
            return
        if self._turn_worker is not None and self._turn_worker.is_running:
            self._write_ui_entry("status", "turn.cancel_requested")
            self._turn_worker.cancel()
            return
        prompt = self.query_one("#prompt", Input)
        if prompt.value:
            prompt.value = ""
            self._write_ui_entry("status", "turn.draft_cleared")
        else:
            self._write_ui_entry("status", "turn.none_running")

    async def _run_sandbox_command(self, args: str) -> None:
        """处理 /sandbox SUBCOMMAND（TUI 内沙箱管理）。"""
        mgr = self._sandbox_manager
        if mgr is None:
            self._write_ui_entry("error", "sandbox.unavailable")
            return
        parts = args.split()
        sub = parts[0].lower() if parts else "status"

        if sub == "status":
            st = mgr.status()
            self._write_entry(
                "system",
                ui_text(
                    self._language,
                    "sandbox.status",
                    mode=st["mode"],
                    egress=st["egress"],
                    projects=st["projects"],
                    max_projects=st["max_projects"],
                ),
            )
            return
        if sub == "list":
            rows = mgr.list_projects()
            if not rows:
                self._write_ui_entry("system", "sandbox.empty")
                return
            self._write_entry(
                "system", ui_text(self._language, "sandbox.list_header"),
            )
            for row in rows:
                self._write_entry(
                    "system",
                    ui_text(
                        self._language,
                        "sandbox.project_row",
                        pid=row["project_id"],
                        lifecycle=row["lifecycle"],
                        importance=f"{row['importance']:.0f}",
                        protected=str(row["protected"]),
                    ),
                )
            return
        if sub == "gc":
            stats = await mgr.gc()
            self._write_entry(
                "system",
                ui_text(
                    self._language,
                    "sandbox.gc_done",
                    removed=stats["projects_evicted"],
                ),
            )
            return
        if sub == "clean-cache":
            await mgr.clean_cache()
            self._write_ui_entry("system", "sandbox.clean_cache_done")
            return
        if sub in {"protect", "unprotect"} and len(parts) > 1:
            ok = (
                mgr.protect_project(parts[1])
                if sub == "protect"
                else mgr.unprotect_project(parts[1])
            )
            self._write_ui_entry(
                "system",
                "sandbox.protected" if ok else "sandbox.not_found",
            )
            return
        if sub == "remove" and len(parts) > 1:
            ok = mgr.remove_project(parts[1])
            self._write_ui_entry(
                "system",
                "sandbox.removed" if ok else "sandbox.not_found",
            )
            return
        self._write_ui_entry(
            "error", "command.arguments", command=f"/sandbox {args}",
        )

    async def _run_draft_command(self, args: str) -> None:
        """处理 /draft SUBCOMMAND（git worktree 工作副本）。"""
        mgr = self._draft_manager
        if mgr is None:
            self._write_ui_entry("error", "draft.unavailable")
            return
        parts = args.split()
        sub = parts[0].lower() if parts else "list"

        try:
            if sub == "list":
                rows = mgr.list_drafts()
                if not rows:
                    self._write_ui_entry("system", "draft.empty")
                    return
                for row in rows:
                    self._write_entry(
                        "system",
                        ui_text(
                            self._language,
                            "draft.row",
                            did=row["draft_id"],
                            merged=str(row["merged"]),
                            branch=row["branch"],
                        ),
                    )
                return
            if sub == "create":
                root = parts[1] if len(parts) > 1 else str(self._cwd)
                record = await mgr.create(root)
                self._write_entry(
                    "system",
                    ui_text(
                        self._language,
                        "draft.created",
                        did=record.draft_id,
                        root=record.root,
                        branch=record.branch,
                        volume=record.volume,
                    ),
                )
                self._write_entry(
                    "system", ui_text(self._language, "draft.hint"),
                )
                return
            if sub == "status" and len(parts) > 1:
                st = await mgr.status(parts[1])
                self._write_entry(
                    "system",
                    ui_text(
                        self._language,
                        "draft.status",
                        did=st["draft_id"],
                        at_base=str(st["at_base"]),
                        root=st["root"],
                    ),
                )
                self._write_entry("system", st["changed"])
                return
            if sub == "merge" and len(parts) > 1:
                result = await mgr.merge(parts[1])
                self._write_entry(
                    "system",
                    ui_text(self._language, "draft.merged", result=result),
                )
                return
            if sub == "discard" and len(parts) > 1:
                result = await mgr.discard(parts[1])
                self._write_entry(
                    "system",
                    ui_text(self._language, "draft.discarded", result=result),
                )
                return
        except Exception as exc:  # DraftError 等业务错误展示给用户
            self._write_entry("error", f"[draft] {exc}")
            return
        self._write_ui_entry(
            "error", "command.arguments", command=f"/draft {args}",
        )

    async def action_select_provider(self) -> None:
        await self._select_provider(None)

    async def action_select_reasoning_effort(self) -> None:
        await self._select_reasoning_effort(None)

    async def action_cycle_interaction_mode(self) -> None:
        if isinstance(self.screen, ModalScreen):
            self.screen.focus_previous()
            return
        await self._apply_interaction_mode(self._interaction_mode.next)

    async def action_select_session(self) -> None:
        await self._select_session(None)

    async def action_open_settings(self) -> None:
        self.push_screen(
            SettingsScreen(
                self._language,
                language=self._language,
                provider_settings_available=(
                    self._managed_provider_settings is not None
                    and self._provider_settings_store is not None
                ),
            ),
            self._settings_category_selected,
        )

    async def _settings_category_selected(self, category: str | None) -> None:
        if category == "language":
            self.push_screen(
                LanguageSettingsScreen(self._language, language=self._language),
                self._language_settings_selected,
            )
            return
        if category == "providers":
            if self._managed_provider_settings is None or self._provider_settings_store is None:
                return
            self.push_screen(
                ProviderSettingsScreen(
                    language=self._language,
                    provider_settings=self._managed_provider_settings,
                    provider_settings_store=self._provider_settings_store,
                    provider_catalog=self._provider_catalog,
                ),
                self._provider_settings_selected,
            )

    async def _provider_settings_selected(
        self,
        result: ProviderSettingsSubmission | None,
    ) -> None:
        if result is not None:
            self.exit(return_code=TUI_RELOAD_PROVIDER_SETTINGS)
            return
        await self.action_open_settings()

    async def _select_reasoning_effort(
        self,
        requested: ReasoningEffort | None,
    ) -> None:
        if self._reasoning_controller is None:
            self._write_ui_entry("error", "effort.unavailable")
            return
        if self._turn_worker is not None and self._turn_worker.is_running:
            self._write_ui_entry("error", "effort.switch_running")
            return
        if requested is None:
            self.push_screen(
                ReasoningEffortScreen(
                    self._reasoning_effort,
                    language=self._language,
                ),
                self._reasoning_effort_selected,
            )
            return
        await self._apply_reasoning_effort(requested)

    async def _reasoning_effort_selected(
        self,
        effort: ReasoningEffort | None,
    ) -> None:
        if effort is not None:
            await self._apply_reasoning_effort(effort)

    async def _apply_reasoning_effort(self, effort: ReasoningEffort) -> None:
        assert self._reasoning_controller is not None
        try:
            result = await self._reasoning_controller.set_reasoning_effort(effort)
        except Exception as error:
            self._write_entry("error", f"{type(error).__name__}: {error}")
            return

        self._reasoning_effort = result.requested
        self._effective_reasoning_effort = result.effective
        self._refresh_runtime_bar()
        if not result.changed:
            self._write_ui_entry(
                "status",
                "effort.already_selected",
                glyph=result.requested.glyph,
                effort=result.requested.value,
            )
            return
        if result.requested is ReasoningEffort.ULTRACODE:
            self._write_ui_entry(
                "status",
                "effort.changed_fallback",
                requested=result.requested.value,
                effective=result.effective.value,
            )
        else:
            self._write_ui_entry(
                "status",
                "effort.changed",
                glyph=result.requested.glyph,
                effort=result.requested.value,
            )
        if self._ui_preferences is not None:
            try:
                await self._ui_preferences.save_reasoning_effort(result.requested)
            except Exception as error:
                self._write_ui_entry(
                    "error",
                    "effort.save_failed",
                    error=f"{type(error).__name__}: {error}",
                )

    async def _apply_interaction_mode(self, mode: InteractionMode) -> None:
        if self._interaction_mode_controller is None:
            self._write_ui_entry("error", "mode.unavailable")
            return
        if self._turn_worker is not None and self._turn_worker.is_running:
            self._write_ui_entry("error", "mode.switch_running")
            return
        try:
            result = await self._interaction_mode_controller.set_interaction_mode(mode)
        except Exception as error:
            self._write_entry("error", f"{type(error).__name__}: {error}")
            return

        self._interaction_mode = result.requested
        self._auto_mode_unrestricted = result.auto_unrestricted
        self._refresh_runtime_bar()
        if not result.changed:
            self._write_ui_entry(
                "status",
                "mode.already_selected",
                glyph=result.requested.glyph,
                mode=result.requested.value,
            )
            return
        key = "mode.changed_auto_limited" if result.limited_auto else "mode.changed"
        self._write_ui_entry(
            "status",
            key,
            glyph=result.requested.glyph,
            mode=result.requested.value,
        )
        if self._ui_preferences is not None:
            try:
                await self._ui_preferences.save_interaction_mode(result.requested)
            except Exception as error:
                self._write_ui_entry(
                    "error",
                    "mode.save_failed",
                    error=f"{type(error).__name__}: {error}",
                )

    async def _execute_plan(self) -> None:
        controller = self._plan_controller
        if controller is None:
            self._write_ui_entry("error", "plan.execution_unavailable")
            return
        plan = controller.plan
        self._plan = plan
        if plan is None:
            self._write_ui_entry("status", "plan.none")
            return
        if self._turn_worker is not None and self._turn_worker.is_running:
            self._write_ui_entry("error", "turn.running")
            return
        await self._apply_interaction_mode(InteractionMode.ACCEPT_EDITS)
        if self._interaction_mode is not InteractionMode.ACCEPT_EDITS:
            return
        self._write_ui_entry("user", "plan.execution_user")
        self._assistant_parts.clear()
        self._reasoning_announced = False
        self._turn_completion = None
        self._turn_usage_reported = False
        self._begin_pending_assistant()
        self._turn_worker = self.run_worker(
            self._run_plan_execution(),
            name="agent-plan-execution",
            group="agent",
            exclusive=True,
            exit_on_error=False,
        )

    async def _language_settings_selected(
        self,
        result: UiLanguage | None,
    ) -> None:
        language = result
        if language is None:
            await self.action_open_settings()
            return
        if language is self._language:
            return
        self._language = language
        self._refresh_localized_interface()
        if self._ui_preferences is not None:
            try:
                await self._ui_preferences.save_language(language)
            except Exception as error:
                self._write_ui_entry(
                    "error",
                    "settings.save_failed",
                    error=f"{type(error).__name__}: {error}",
                )
                return
        self._write_ui_entry(
            "system",
            "settings.changed",
            language=language_name(language, in_language=language),
        )

    async def _select_provider(self, requested: str | None) -> None:
        if self._provider_controller is None:
            self._write_ui_entry("error", "provider.switch_unavailable")
            return
        if self._turn_worker is not None and self._turn_worker.is_running:
            self._write_ui_entry("error", "provider.switch_running")
            return
        profile_name = requested
        if profile_name is None:
            self.push_screen(
                ProviderSelectionScreen(
                    self._provider_controller.profiles,
                    language=self._language,
                ),
                self._provider_selected,
            )
            return
        await self._apply_provider_selection(profile_name)

    async def _provider_selected(self, profile_name: str | None) -> None:
        if profile_name is not None:
            await self._apply_provider_selection(profile_name)

    async def _apply_provider_selection(self, profile_name: str) -> None:
        assert self._provider_controller is not None
        try:
            result = await self._provider_controller.select_profile(profile_name)
        except Exception as error:
            self._write_entry("error", f"{type(error).__name__}: {error}")
            return

        if (
            self._provider_settings_store is not None
            and self._managed_provider_settings is not None
            and self._managed_provider_settings.profile(profile_name) is not None
        ):
            try:
                self._managed_provider_settings = await self._provider_settings_store.set_default(
                    profile_name
                )
            except Exception as error:
                self._write_ui_entry(
                    "error",
                    "provider.default_save_failed",
                    error=f"{type(error).__name__}: {error}",
                )

        self._provider_name = result.provider_name
        self._model_name = result.model_name
        if self._plan_controller is not None:
            self._plan = self._plan_controller.plan
        self._context_window_tokens = result.context_window_tokens
        if result.changed:
            self._context_used_tokens = 0
            self._context_usage_estimated = True
        self._refresh_runtime_bar()
        if result.changed:
            self._reset_background_task_tracking()
        if not result.changed:
            self._write_ui_entry(
                "status",
                "provider.already_selected",
                profile=result.profile_name,
            )
        elif result.previous_session_id is None:
            self._write_ui_entry(
                "status",
                "provider.switched",
                profile=result.profile_name,
                provider=result.provider_name,
                model=result.model_name,
                stopped=self._stopped_task_note(result.stopped_background_tasks),
            )
        else:
            self._write_ui_entry(
                "status",
                "provider.switched_saved",
                profile=result.profile_name,
                provider=result.provider_name,
                model=result.model_name,
                session_id=result.previous_session_id,
                stopped=self._stopped_task_note(result.stopped_background_tasks),
            )

    async def _select_session(
        self,
        requested: str | None,
        *,
        query: str | None = None,
    ) -> None:
        if self._session_controller is None:
            self._write_ui_entry("error", "session.resume_unavailable")
            return
        if self._turn_worker is not None and self._turn_worker.is_running:
            self._write_ui_entry("error", "session.resume_running")
            return
        if requested is not None:
            await self._apply_session_selection(requested)
            return
        try:
            options = await self._session_controller.list_sessions(query)
        except Exception as error:
            self._write_entry("error", f"{type(error).__name__}: {error}")
            return
        if not options:
            if query is None:
                self._write_ui_entry("status", "session.none")
            else:
                self._write_ui_entry(
                    "status",
                    "session.none_matching",
                    query=query,
                )
            return
        self.push_screen(
            SessionSelectionScreen(options, query=query, language=self._language),
            self._session_selected,
        )

    async def _rename_session(self, title: str) -> None:
        if self._session_controller is None:
            self._write_ui_entry("error", "session.rename_unavailable")
            return
        if self._turn_worker is not None and self._turn_worker.is_running:
            self._write_ui_entry("error", "session.rename_running")
            return
        try:
            summary = await self._session_controller.rename_session(title)
        except Exception as error:
            self._write_entry("error", f"{type(error).__name__}: {error}")
            return
        self._write_ui_entry(
            "status",
            "session.renamed",
            session_id=summary.id,
            title=summary.title,
        )

    async def _session_selected(self, session_id: str | None) -> None:
        if session_id is not None:
            await self._apply_session_selection(session_id)

    async def _apply_session_selection(self, session_id: str) -> None:
        assert self._session_controller is not None
        try:
            result = await self._session_controller.select_session(session_id)
        except Exception as error:
            self._write_entry("error", f"{type(error).__name__}: {error}")
            return

        self._provider_name = result.provider_name
        self._model_name = result.model_name
        if self._plan_controller is not None:
            self._plan = self._plan_controller.plan
        self._context_window_tokens = result.context_window_tokens
        if result.changed:
            self._context_used_tokens = estimate_context_tokens(result.items)
            self._context_usage_estimated = True
        self._refresh_runtime_bar()
        if not result.changed:
            self._write_ui_entry(
                "status",
                "session.already_open",
                session_id=result.session_id,
            )
            return

        self._reset_background_task_tracking()
        self._replace_transcript(result.items)
        profile_note = (
            ui_text(
                self._language,
                "session.profile",
                profile=result.profile_name,
            )
            if result.source_profile_match
            else ui_text(
                self._language,
                "session.profile_unavailable",
                profile=result.profile_name,
                source=result.source_provider,
            )
        )
        previous_note = (
            ui_text(
                self._language,
                "session.previous_saved",
                session_id=result.previous_session_id,
            )
            if result.previous_session_id is not None
            else ""
        )
        self._write_ui_entry(
            "system",
            "session.resumed",
            session_id=result.session_id,
            profile_note=profile_note,
            provider=result.provider_name,
            model=result.model_name,
            previous=previous_note,
            stopped=self._stopped_task_note(result.stopped_background_tasks),
        )

    async def _add_plan_comment(self, arguments: str) -> None:
        controller = self._plan_controller
        if controller is None:
            self._write_ui_entry("error", "plan.comment_unavailable")
            return
        plan = controller.plan
        self._plan = plan
        if plan is None:
            self._write_ui_entry("status", "plan.none")
            return
        if self._turn_worker is not None and self._turn_worker.is_running:
            self._write_ui_entry("error", "turn.running")
            return
        raw_index, separator, content = arguments.strip().partition(" ")
        if not separator or not raw_index or not content.strip():
            self._write_ui_entry("error", "plan.comment_usage")
            return
        try:
            step_index = int(raw_index)
        except ValueError:
            self._write_ui_entry("error", "plan.comment_step_invalid", index=raw_index)
            return
        if not 1 <= step_index <= len(plan.steps):
            self._write_ui_entry("error", "plan.comment_step_invalid", index=raw_index)
            return
        try:
            await controller.add_plan_comment(step_index, content)
        except Exception as error:
            self._write_entry("error", f"{type(error).__name__}: {error}")
            return
        self._write_ui_entry("status", "plan.comment_added", index=step_index)
        await self._show_plan()

    async def _show_plan(self) -> None:
        controller = self._plan_controller
        plan = controller.plan if controller is not None else self._plan
        self._plan = plan
        if plan is None:
            self._write_ui_entry("status", "plan.none")
            return
        comments: tuple[PlanComment, ...] = ()
        if controller is not None:
            try:
                comments = await controller.list_plan_comments()
            except Exception as error:
                self._write_entry("error", f"{type(error).__name__}: {error}")
                return
        lines = [ui_text(self._language, "plan.heading")]
        if plan.explanation is not None:
            lines.append(ui_text(self._language, "plan.purpose", explanation=plan.explanation))
        for index, step in enumerate(plan.steps, start=1):
            lines.append(
                ui_text(
                    self._language,
                    "plan.step",
                    index=index,
                    status=ui_text(self._language, f"plan.status.{step.status.value}"),
                    step=step.step,
                )
            )
            lines.extend(
                ui_text(self._language, "plan.comment", content=comment.content)
                for comment in comments
                if comment.step_index == index
            )
        self._write_entry("system", "\n".join(lines))

    async def _show_tasks(self) -> None:
        if self._task_controller is None and self._session_task_controller is None:
            self._write_ui_entry("error", "tasks.unavailable")
            return
        try:
            snapshots = (
                await self._task_controller.list_background_tasks()
                if self._task_controller is not None
                else ()
            )
            session_tasks = (
                await self._session_task_controller.list_session_tasks()
                if self._session_task_controller is not None
                else ()
            )
        except Exception as error:
            self._write_entry("error", f"{type(error).__name__}: {error}")
            return
        if not snapshots and not session_tasks:
            self._write_ui_entry("status", "tasks.none")
            return

        visible = snapshots[-_TASK_LIST_LIMIT:]
        omitted = len(snapshots) - len(visible)
        lines = [self._task_summary(snapshot) for snapshot in visible]
        lines.extend(self._session_task_summary(task) for task in session_tasks[:_TASK_LIST_LIMIT])
        if omitted:
            lines.insert(0, ui_text(self._language, "tasks.omitted", count=omitted))
        self._write_ui_entry(
            "system",
            "tasks.heading",
            lines="\n".join(lines),
        )

    async def _show_session_task(self, task_id: str) -> None:
        if not task_id:
            self._write_ui_entry("error", "tasks.view.usage")
            return
        controller = self._session_task_controller
        if controller is None:
            self._write_ui_entry("error", "tasks.unavailable")
            return
        try:
            task = await controller.get_session_task(task_id)
        except Exception as error:
            self._write_entry("error", f"{type(error).__name__}: {error}")
            return
        if task is None:
            self._write_ui_entry("status", "tasks.view.not_found", task_id=task_id)
            return
        if task.plan_snapshot is None:
            self._write_ui_entry("status", "tasks.view.no_plan", task_id=task.task_id)
            return

        plan = task.plan_snapshot
        finished = (
            ui_text(
                self._language,
                "tasks.session.finished",
                finished=task.finished_at.astimezone().strftime("%H:%M:%S"),
            )
            if task.finished_at is not None
            else ""
        )
        lines = [
            ui_text(self._language, "tasks.view.heading", task_id=task.task_id),
            ui_text(
                self._language,
                "tasks.view.lifecycle",
                kind=ui_text(self._language, f"tasks.kind.{task.kind.value}"),
                status=ui_text(self._language, f"tasks.status.{task.status.value}"),
                started=task.started_at.astimezone().strftime("%H:%M:%S"),
                finished=finished,
            ),
            ui_text(
                self._language,
                "tasks.view.revision",
                fingerprint=plan.fingerprint,
            ),
            ui_text(self._language, "tasks.view.snapshot"),
        ]
        if plan.explanation is not None:
            lines.append(
                ui_text(self._language, "tasks.view.purpose", explanation=plan.explanation)
            )
        lines.extend(
            ui_text(
                self._language,
                "tasks.view.step",
                index=index,
                status=ui_text(self._language, f"plan.status.{step.status.value}"),
                step=step.step,
            )
            for index, step in enumerate(plan.steps, start=1)
        )
        lines.append(ui_text(self._language, "tasks.view.reference"))
        self._write_entry("system", "\n".join(lines))

    async def _poll_background_tasks(self) -> None:
        if self._task_controller is None or self._task_polling:
            return
        self._task_polling = True
        try:
            snapshots = await self._task_controller.list_background_tasks()
        except Exception:
            return
        finally:
            self._task_polling = False

        for snapshot in snapshots:
            if not snapshot.status.terminal:
                continue
            if snapshot.task_id in self._announced_terminal_tasks:
                continue
            self._announced_terminal_tasks.add(snapshot.task_id)
            category = (
                "status"
                if snapshot.status
                in {BackgroundTaskStatus.COMPLETED, BackgroundTaskStatus.CANCELLED}
                else "error"
            )
            self._write_entry(category, self._task_completion_message(snapshot))

    def _reset_background_task_tracking(self) -> None:
        self._announced_terminal_tasks.clear()

    def _task_summary(self, snapshot: BackgroundTaskSnapshot) -> str:
        exit_note = (
            ui_text(self._language, "tasks.exit", code=snapshot.exit_code)
            if snapshot.exit_code is not None
            else ""
        )
        truncation_note = ui_text(self._language, "tasks.truncated") if snapshot.truncated else ""
        started = snapshot.started_at.astimezone().strftime("%H:%M:%S")
        return ui_text(
            self._language,
            "tasks.summary",
            task_id=snapshot.task_id,
            status=ui_text(self._language, f"tasks.status.{snapshot.status.value}"),
            exit_note=exit_note,
            bytes=snapshot.total_output_bytes,
            truncated=truncation_note,
            started=started,
        )

    def _task_completion_message(self, snapshot: BackgroundTaskSnapshot) -> str:
        exit_note = (
            ui_text(self._language, "tasks.completion.exit", code=snapshot.exit_code)
            if snapshot.exit_code is not None
            else ""
        )
        return ui_text(
            self._language,
            f"tasks.completion.{snapshot.status.value}",
            task_id=snapshot.task_id,
            exit_note=exit_note,
        )

    def _session_task_summary(self, task: SessionTask) -> str:
        started = task.started_at.astimezone().strftime("%H:%M:%S")
        finished = (
            ui_text(
                self._language,
                "tasks.session.finished",
                finished=task.finished_at.astimezone().strftime("%H:%M:%S"),
            )
            if task.finished_at is not None
            else ""
        )
        plan_note = ""
        if task.plan_snapshot is not None:
            completed = sum(step.status.value == "completed" for step in task.plan_snapshot.steps)
            plan_note = ui_text(
                self._language,
                "tasks.session.plan_revision",
                fingerprint=task.plan_snapshot.fingerprint[:12],
                completed=completed,
                total=len(task.plan_snapshot.steps),
            )
        return ui_text(
            self._language,
            "tasks.session.summary",
            task_id=task.task_id,
            kind=ui_text(self._language, f"tasks.kind.{task.kind.value}"),
            status=ui_text(self._language, f"tasks.status.{task.status.value}"),
            started=started,
            finished=finished,
            plan=plan_note,
        )

    def _stopped_task_note(self, count: int) -> str:
        if count == 0:
            return ""
        if count == 1:
            return ui_text(self._language, "tasks.stopped_one")
        return ui_text(self._language, "tasks.stopped_many", count=count)

    def _replace_transcript(self, items: Sequence[SessionItem]) -> None:
        transcript = self.query_one("#transcript", VerticalScroll)
        transcript.remove_children()
        self._entries.clear()
        self._entry_widgets.clear()
        self._tool_feedback_by_call.clear()
        self._tool_feedback_by_entry.clear()
        self._pending_assistant = None
        self._assistant_parts.clear()
        for item in items:
            if not isinstance(item, Message) or item.role is Role.SYSTEM:
                continue
            if item.role is Role.TOOL:
                self._write_ui_entry(
                    "tool",
                    "restore.result",
                    name=item.name or ui_text(self._language, "value.unknown"),
                )
                continue
            content = self._bounded_restored_text(item.model_content())
            if content:
                category = "user" if item.role is Role.USER else "assistant"
                self._write_entry(category, content)
            if item.role is Role.ASSISTANT and item.tool_calls:
                names = ", ".join(call.name for call in item.tool_calls)
                self._write_ui_entry("tool", "restore.request", names=names)

    def _bounded_restored_text(self, content: str) -> str:
        if len(content) <= _RESTORED_MESSAGE_LIMIT:
            return content
        return (
            f"{content[:_RESTORED_MESSAGE_LIMIT]}\n{ui_text(self._language, 'restore.truncated')}"
        )

    @staticmethod
    def _semantic_value_style(name: str, value: object) -> str | None:
        if name in {"provider", "model", "profile", "source"}:
            return "bold #7da7d9"
        if name in {"name", "task_id", "session_id", "title"}:
            return "bold #8ed1e6"
        if name in {"cwd", "path"}:
            return "#8ab3ad"
        if name in {"effect", "outcome", "status"}:
            return "bold #78c2a4"
        if name in {"duration", "steps", "step"}:
            return "bold #78c2a4"
        if name == "context":
            return "bold #8ab3ad"
        if name in {"effort", "requested", "effective"}:
            try:
                effort = ReasoningEffort(str(value))
            except ValueError:
                return "bold #a9a1e8"
            return f"bold {_EFFORT_COLORS[effort]}"
        if name == "mode":
            try:
                mode = InteractionMode(str(value))
            except ValueError:
                return "bold #8b9cff"
            return f"bold {_MODE_COLORS[mode]}"
        if name == "policy":
            return "#9aa3b2"
        if name in {"message", "reason", "error"}:
            return "#d88a8a"
        return None

    def _render_entry(
        self,
        category: str,
        content: str,
        *,
        ui_key: str | None = None,
        ui_values: tuple[tuple[str, object], ...] = (),
    ) -> RenderableType:
        if category == "user":
            return Text(content, style="#e3e5e8", overflow="fold")
        if category == "assistant":
            return AssistantMarkdown(
                content,
                code_theme="github-dark",
                style="#e1e3e6",
                hyperlinks=False,
            )

        labels = {
            "error": (ui_text(self._language, "label.error"), "bold #c76d6d"),
            "status": (ui_text(self._language, "label.status"), "bold #8e939a"),
            "system": ("Evil Code", "bold #8b9cff"),
            "tool": (ui_text(self._language, "label.tool"), "bold #78c2a4"),
        }
        body_styles = {
            "error": "#d58b8b",
            "status": "#a9adb3",
            "system": "#c8cbd0",
            "tool": "#b8c7c1",
        }
        label, label_style = labels.get(category, (category.title(), "bold"))
        body = Text(content, style=body_styles.get(category, "#d8dadd"), overflow="fold")
        for name, value in ui_values:
            style = self._semantic_value_style(name, value)
            rendered_value = str(value)
            if style is not None and rendered_value:
                body.highlight_words((rendered_value,), style=style, case_sensitive=True)

        table = Table.grid(expand=True, padding=(0, 1))
        table.add_column(width=10, justify="right", no_wrap=True)
        table.add_column(ratio=1, overflow="fold")
        table.add_row(Text(label, style=label_style), body)
        return table

    def _render_tool_feedback(
        self,
        state: ToolFeedbackState,
        *,
        body: Text | None = None,
    ) -> RenderableType:
        table = Table.grid(expand=True, padding=(0, 1))
        table.add_column(width=10, justify="right", no_wrap=True)
        table.add_column(ratio=1, overflow="fold")
        label_style = "bold #d07878" if state.is_error else "bold #78c2a4"
        table.add_row(
            Text(ui_text(self._language, "label.tool"), style=label_style),
            body if body is not None else self._tool_feedback_renderer.render_body(state),
        )
        return table

    def _write_ui_entry(self, category: str, key: str, **values: object) -> None:
        self._write_entry(
            category,
            ui_text(self._language, key, **values),
            ui_key=key,
            ui_values=tuple(values.items()),
        )

    def _write_entry(
        self,
        category: str,
        content: str,
        *,
        ui_key: str | None = None,
        ui_values: tuple[tuple[str, object], ...] = (),
        tool_state: ToolFeedbackState | None = None,
    ) -> None:
        entry = TranscriptEntry(category, content, ui_key, ui_values)
        if tool_state is not None:
            tool_widget = ToolFeedbackMessage(
                self._render_tool_feedback(tool_state),
                entry_index=tool_state.entry_index,
            )
            self._tool_feedback_renderer.configure_widget(tool_widget, tool_state)
            widget: ConversationMessage = tool_widget
        else:
            widget = ConversationMessage(
                category,
                self._render_entry(
                    category,
                    content,
                    ui_key=ui_key,
                    ui_values=ui_values,
                ),
            )
        transcript = self.query_one("#transcript", VerticalScroll)
        follow = transcript.is_vertical_scroll_end
        pending = self._pending_assistant
        if pending is not None and pending.parent is transcript:
            transcript.mount(widget, before=pending)
        else:
            transcript.mount(widget)
        self._entries.append(entry)
        self._entry_widgets.append(widget)
        if follow:
            transcript.scroll_end(animate=False)

    def _begin_pending_assistant(self) -> None:
        if self._pending_assistant is not None:
            return
        self._start_model_loading()
        pending = ConversationMessage(
            "assistant",
            self._render_model_loading(),
            pending=True,
        )
        self._pending_assistant = pending
        transcript = self.query_one("#transcript", VerticalScroll)
        transcript.mount(pending)
        transcript.scroll_end(animate=False)

    def _update_pending_assistant(self, content: str) -> None:
        if self._pending_assistant is None:
            self._begin_pending_assistant()
        pending = self._pending_assistant
        assert pending is not None
        transcript = self.query_one("#transcript", VerticalScroll)
        follow = transcript.is_vertical_scroll_end
        pending.set_pending(False)
        pending.update(self._render_entry("assistant", content))
        if follow:
            transcript.scroll_end(animate=False)

    def _finish_pending_assistant(self, content: str) -> None:
        if self._pending_assistant is None:
            self._begin_pending_assistant()
        pending = self._pending_assistant
        assert pending is not None
        transcript = self.query_one("#transcript", VerticalScroll)
        follow = transcript.is_vertical_scroll_end
        pending.set_pending(False)
        pending.update(self._render_entry("assistant", content))
        self._entries.append(TranscriptEntry("assistant", content))
        self._entry_widgets.append(pending)
        self._pending_assistant = None
        self._stop_model_loading()
        if follow:
            transcript.scroll_end(animate=False)

    async def _discard_pending_assistant(self) -> None:
        pending = self._pending_assistant
        self._pending_assistant = None
        self._assistant_parts.clear()
        self._stop_model_loading()
        if pending is not None and pending.parent is not None:
            await pending.remove()

    def _start_model_loading(self) -> None:
        self._model_loading = True
        self._loading_animation.reset()
        self._loading_animation_elapsed = 0.0

    def _stop_model_loading(self) -> None:
        if not self._model_loading:
            return
        self._model_loading = False
        self._loading_animation_elapsed = 0.0

    def _advance_model_loading_animation(self) -> None:
        if not self._model_loading:
            return
        self._loading_animation_elapsed += _LOADING_ANIMATION_TICK_SECONDS
        if self._loading_animation_elapsed + 1e-9 < self._loading_animation.delay_seconds:
            return
        self._loading_animation_elapsed = 0.0
        self._loading_animation.advance()
        pending = self._pending_assistant
        if pending is not None and not self._assistant_parts and pending.parent is not None:
            pending.update(self._render_model_loading())

    def _apply_language_to_chrome(self) -> None:
        self.sub_title = ui_text(self._language, "subtitle")
        self.query_one("#prompt", Input).placeholder = ui_text(
            self._language,
            "prompt.placeholder",
        )
        shortcuts = Text(ui_text(self._language, "shortcuts"), style="#a9adb3")
        shortcuts.highlight_regex(r"\^(?:[A-Z]|,)|Shift\+Tab", style="bold #8b9cff")
        self.query_one("#shortcut-bar", Static).update(shortcuts)
        prompt = self.query_one("#prompt", Input)
        self._refresh_command_hints(prompt.value)
        self._refresh_runtime_bar()

    def _slash_completions(self, value: str) -> tuple[SlashCompletion, ...]:
        provider_names = (
            tuple(option.name for option in self._provider_controller.profiles if option.selectable)
            if self._provider_controller is not None
            else ()
        )
        return slash_completions(value, provider_names=provider_names)

    def _refresh_command_hints(self, value: str) -> None:
        widget = self.query_one("#command-hints", Static)
        completions = self._slash_completions(value)
        if not completions:
            widget.update("")
            widget.display = False
            return

        hints = Text()
        hints.append(ui_text(self._language, "command_hint.tab"), style="bold #8b9cff")
        hints.append("  ", style="#555b62")
        for index, completion in enumerate(completions[:_COMMAND_HINT_LIMIT]):
            if index:
                hints.append("  ·  ", style="#555b62")
            hints.append(completion.display, style="#7da7d9")
        if len(completions) > _COMMAND_HINT_LIMIT:
            hints.append("  ·  …", style="#777c83")
        widget.update(hints)
        widget.display = True

    def _context_percentage(self) -> str:
        window = self._context_window_tokens
        if window is None:
            return ui_text(self._language, "runtime.context_unknown")
        percentage = self._context_used_tokens / window * 100
        rendered = "<0.1%" if 0 < percentage < 0.1 else f"{percentage:.1f}%"
        return f"~{rendered}" if self._context_usage_estimated else rendered

    def _context_color(self) -> str:
        window = self._context_window_tokens
        if window is None:
            return "#8e939a"
        ratio = self._context_used_tokens / window
        if ratio >= 0.8:
            return "#c76d6d"
        if ratio >= 0.5:
            return "#e59c74"
        return "#78c2a4"

    def _context_usage_summary(self) -> str:
        window = self._context_window_tokens
        if window is None:
            return ui_text(self._language, "runtime.context_unknown")
        approximation = "≈" if self._context_usage_estimated else ""
        return (
            f"{self._context_percentage()} "
            f"({approximation}{self._context_used_tokens:,}/{window:,})"
        )

    @staticmethod
    def _loading_color(level: int) -> str:
        inactive = (72, 72, 72)
        active = (230, 230, 230)
        peak = (255, 255, 255)
        intensity = max(0.0, min(1.0, level / 7))
        color = tuple(
            round(background + (foreground - background) * intensity)
            for foreground, background in zip(active, inactive, strict=True)
        )
        if level >= 6:
            peak_amount = min(1.0, (level - 5) / 2)
            color = tuple(
                round(background + (foreground - background) * peak_amount)
                for foreground, background in zip(peak, color, strict=True)
            )
        return f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}"

    def _loading_wave(self) -> Text:
        symbols = ("▁", "▂", "▃", "▄", "▅", "▆", "▇", "█")
        wave = Text()
        for level in self._loading_animation.levels():
            safe_level = max(0, min(7, level))
            style = self._loading_color(safe_level)
            if safe_level == 7:
                style = f"bold {style}"
            wave.append(symbols[safe_level], style=style)
        return wave

    def _render_model_loading(self) -> Text:
        loading = self._loading_wave()
        loading.append("  ")
        loading.append(ui_text(self._language, "turn.waiting"), style="#a9adb3")
        return loading

    def _refresh_runtime_bar(self) -> None:
        model = Text()
        model.append(
            f"{ui_text(self._language, 'runtime.model')}  ",
            style="bold #777c83",
        )
        model.append(self._provider_name, style="bold #8ab3ad")
        model.append(" / ", style="#555b62")
        model.append(self._model_name, style="bold #7da7d9")
        self.query_one("#runtime-model", Static).update(model)

        workspace = Text(justify="right", overflow="ellipsis", no_wrap=True)
        workspace.append(
            f"{ui_text(self._language, 'runtime.workspace')}  ",
            style="bold #777c83",
        )
        workspace.append(self._display_cwd(), style="#6fb7d6")
        workspace_widget = self.query_one("#runtime-workspace", Static)
        workspace_widget.update(workspace)
        workspace_widget.tooltip = str(self._cwd)

        context = Text()
        context.append(
            f"{ui_text(self._language, 'runtime.context')}  ",
            style="bold #777c83",
        )
        context.append(
            self._context_percentage(),
            style=f"bold {self._context_color()}",
        )
        context_widget = self.query_one("#runtime-context", Static)
        context_widget.update(context)
        if self._context_window_tokens is None:
            context_widget.tooltip = ui_text(
                self._language,
                "runtime.context_help_unknown",
            )
        else:
            context_widget.tooltip = ui_text(
                self._language,
                (
                    "runtime.context_help_estimated"
                    if self._context_usage_estimated
                    else "runtime.context_help_reported"
                ),
                used=f"{self._context_used_tokens:,}",
                window=f"{self._context_window_tokens:,}",
            )

        requested = self._reasoning_effort
        effective = self._effective_reasoning_effort
        effort = Text()
        effort.append(
            f"{ui_text(self._language, 'runtime.effort')}  ",
            style="bold #777c83",
        )
        effort.append(
            f"{requested.glyph} {requested.value}",
            style=f"bold {_EFFORT_COLORS[requested]}",
        )
        if effective is not requested:
            effort.append(" → ", style="#777c83")
            effort.append(
                f"{effective.glyph} {effective.value}",
                style=f"bold {_EFFORT_COLORS[effective]}",
            )
        effort_widget = self.query_one("#runtime-effort", Static)
        effort_widget.update(effort)
        effort_widget.tooltip = ui_text(self._language, "runtime.effort_help")

        mode = Text()
        mode.append(
            f"{ui_text(self._language, 'runtime.mode')}  ",
            style="bold #777c83",
        )
        mode.append(
            f"{self._interaction_mode.glyph} {self._interaction_mode.value}",
            style=f"bold {_MODE_COLORS[self._interaction_mode]}",
        )
        mode_widget = self.query_one("#runtime-mode", Static)
        mode_widget.update(mode)
        mode_widget.tooltip = ui_text(
            self._language,
            (
                "runtime.mode_help_auto_unrestricted"
                if self._interaction_mode is InteractionMode.AUTO and self._auto_mode_unrestricted
                else f"runtime.mode_help.{self._interaction_mode.value}"
            ),
        )

        sandbox = Text()
        if self._sandbox_label:
            sandbox.append(self._sandbox_label, style="bold #b08a3e")
        else:
            sandbox.append("sandbox:off", style="bold #555b62")
        sandbox_widget = self.query_one("#runtime-sandbox", Static)
        sandbox_widget.update(sandbox)
        sandbox_widget.tooltip = ui_text(self._language, "runtime.sandbox_help")

    def _display_cwd(self) -> str:
        try:
            relative = self._cwd.resolve().relative_to(Path.home().resolve())
        except (OSError, ValueError):
            return str(self._cwd)
        return "~" if str(relative) == "." else f"~/{relative}"

    def _reasoning_effort_summary(self) -> str:
        requested = self._reasoning_effort
        effective = self._effective_reasoning_effort
        summary = f"{requested.glyph} {requested.value}"
        if effective is not requested:
            summary += f" → {effective.glyph} {effective.value}"
        return summary

    def _interaction_mode_summary(self) -> str:
        summary = f"{self._interaction_mode.glyph} {self._interaction_mode.value}"
        if self._interaction_mode is InteractionMode.AUTO and not self._auto_mode_unrestricted:
            summary += f" ({ui_text(self._language, 'mode.limited')})"
        return summary

    def _refresh_localized_interface(self) -> None:
        self._apply_language_to_chrome()
        for index, (entry, widget) in enumerate(
            zip(self._entries, self._entry_widgets, strict=True)
        ):
            tool_state = self._tool_feedback_by_entry.get(index)
            if tool_state is not None:
                body = self._tool_feedback_renderer.render_body(tool_state)
                self._entries[index] = TranscriptEntry("tool", body.plain)
                widget.update(self._render_tool_feedback(tool_state, body=body))
                if isinstance(widget, ToolFeedbackMessage):
                    self._tool_feedback_renderer.configure_widget(widget, tool_state)
                continue
            if entry.ui_key is not None:
                content = ui_text(
                    self._language,
                    entry.ui_key,
                    **dict(entry.ui_values),
                )
                entry = replace(entry, text=content)
                self._entries[index] = entry
            widget.update(
                self._render_entry(
                    entry.category,
                    entry.text,
                    ui_key=entry.ui_key,
                    ui_values=entry.ui_values,
                )
            )
        if self._pending_assistant is not None:
            if self._assistant_parts:
                self._pending_assistant.update(
                    self._render_entry("assistant", "".join(self._assistant_parts))
                )
            else:
                self._pending_assistant.update(self._render_model_loading())


__all__ = [
    "TUI_RELOAD_PROVIDER_SETTINGS",
    "ApprovalController",
    "AssistantMarkdown",
    "ConversationMessage",
    "ConversationRunner",
    "LanguageSettingsScreen",
    "EvilCodeApp",
    "PermissionApprovalScreen",
    "ProviderController",
    "ProviderSelectionScreen",
    "ProviderSettingsScreen",
    "ProviderSettingsSubmission",
    "ProviderSetupApp",
    "ReasoningController",
    "ReasoningEffortScreen",
    "SessionController",
    "SessionSelectionScreen",
    "SessionTaskController",
    "SettingsScreen",
    "TaskController",
    "ToolFeedbackMessage",
    "TranscriptEntry",
]
