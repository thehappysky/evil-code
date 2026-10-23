# Python AI Coding Agent -- Phase 3 详细设计：TUI + Skills + Plan Mode

> 代码逻辑参考 Grok Build 的 pager、skills、plan_mode 模块。
> 命名遵循 Python 社区规范，按功能描述命名。格式对齐 Phase 1 详细设计。

---

## Phase 3 Goals

Build on Phase 2 with:
- **Textual full-screen TUI**: streaming rendering, scrollback navigation, diff display
- **Skills system**: SKILL.md file discovery, parsing, and model invocation
- **Plan Mode**: plan-first-then-code state machine with tool gating
- **Headless mode**: JSON and streaming-json output formats

---

## 1. New Directory Structure

Phase 2 directories remain unchanged. Phase 3 only adds:

```
code_agent/
  ... (Phase 1/2 files untouched)

  tui/                       # Textual TUI layer
    __init__.py              #   public: launch_tui
    app.py                   #   Textual App subclass with CSS layout
    screens/
      __init__.py
      chat.py                #   Main chat screen (scrollback + prompt)
    widgets/
      __init__.py
      scrollback.py          #   Streaming conversation history display
      prompt_input.py        #   Input area with @ file reference picker
      diff_view.py           #   Side-by-side diff rendering widget
      thinking_block.py      #   Collapsible thinking block widget
      todo_panel.py          #   Todo sidebar widget
      status_bar.py          #   Status bar (model, token count, elapsed time)
    rendering/
      __init__.py
      markdown.py            #   Markdown to Rich Text converter
      syntax_highlight.py    #   Pygments-based syntax highlighting
    theme.py                 #   Theme management (light/dark/auto)

  skills/                    # Skills system
    __init__.py              #   public: SkillManager, discover_skills
    models.py                #   SkillInfo Pydantic data model
    discovery.py             #   SKILL.md file scanner
    parser.py                #   YAML frontmatter + Markdown body parser
    manager.py               #   Skill lifecycle management

  core/                      # New files in existing directory
    plan_mode.py             #   Plan Mode finite state machine

  cli/                       # New files in existing directory
    headless.py              #   Headless mode with JSON output formats
```

---

## 2. tui/app.py

References: Rust xai-grok-pager (ratatui TUI)

### Class: CodeAgentApp

```
from textual.app import App, ComposeResult

class CodeAgentApp(App):
    """Textual TUI main application.

    CSS layout (app.tcss):
    +----------------------------------+
    | Header (model, session ID, mode)  |
    +----------------------------------+
    | Scrollback (conversation)         |
    |  - UserMessage bubble             |
    |  - AssistantMessage (Markdown)    |
    |  - ToolCall card (Diff view)      |
    |  - Thinking block (collapsible)   |
    +----------------------------------+
    | TodoPanel (toggle: Ctrl+T)       |
    +----------------------------------+
    | Prompt (input area)              |
    |  - @ file references              |
    |  - / command completion           |
    +----------------------------------+
    | StatusBar (tokens, model, time)  |
    +----------------------------------+
    """

    CSS_PATH = "app.tcss"

    def __init__(self, agent: Agent, session: Session): ...
    def compose(self) -> ComposeResult: ...
    async def on_mount(self) -> None:
        """Startup: display welcome message and load initial state."""

    async def action_submit_prompt(self, text: str) -> None:
        """Submit user prompt for processing.

        Creates an asyncio.Task that runs Agent.run().
        Streaming events are consumed and pushed to UI widgets
        via message queue (Textual's post_message mechanism).
        No threading needed -- Agent and TUI share same event loop.
        """
```

---

## 3. tui/widgets/scrollback.py

References: Rust Scrollback pane

### Class: ScrollbackWidget

```
from textual.widget import Widget

class ScrollbackWidget(Widget):
    """Streaming conversation history display.

    Renders each conversation item as a distinct block:
    - User messages: left-aligned bubbles with dimmed border
    - Assistant messages: Markdown rendered via rich.Markdown
    - Tool calls: expandable cards showing tool name + arguments
    - Tool results: collapsible sections with diff view
    - Thinking blocks: collapsible, dimmed text with brain icon

    Keyboard navigation (Vim-compatible):
    - j/k or Down/Up: move selection
    - h/l or Left/Right: collapse/expand selected block
    - y: copy selected block content
    - Enter: open selected block in fullscreen viewer
    """

    def append_text(self, text: str) -> None:
        """Append streaming text character by character."""

    def append_tool_call(self, name: str, args: dict) -> None:
        """Show tool call card while tool is executing."""

    def append_tool_result(self, call_id: str, result: str) -> None:
        """Replace tool call card with completed result."""

    def append_thinking(self, text: str) -> None:
        """Add collapsible thinking block."""

    def mark_turn_complete(self) -> None:
        """Finalize current turn display. Update status bar."""

    def clear(self) -> None:
        """Clear all content (new session)."""
```

---

## 4. tui/widgets/prompt_input.py

References: Rust Prompt widget

### Class: PromptWidget

```
from textual.widgets import TextArea

class PromptWidget(TextArea):
    """Input area with IDE-style features.

    Features:
    - @ character triggers fuzzy file picker (using system fzf if available)
    - / character triggers command completion menu
    - Multi-line editing with Alt+Enter or Shift+Enter
    - Command history via up/down arrows
    - Real-time token count in status bar
    """

    async def action_open_file_picker(self) -> None:
        """@ triggered: open fuzzy file selector.
        Respects .gitignore patterns by default."""

    async def action_show_command_palette(self) -> None:
        """/ triggered: show available slash commands.
        Commands: /model, /compact, /new, /resume, /plan, /always-approve, etc."""

    async def action_submit(self) -> None:
        """Submit current input to Agent.
        Trims whitespace. No-op if input is empty."""

    def get_history(self) -> list[str]: ...
    def append_history(self, text: str) -> None: ...
```

---

## 5. tui/widgets/diff_view.py

References: Rust SearchReplace diff rendering (similar crate)

### Class: DiffWidget

```
class DiffWidget(Widget):
    """Unified or side-by-side diff display.

    Uses Python difflib.SequenceMatcher for diff computation.
    Color scheme:
    - Green background: added lines
    - Red background: removed lines
    - Dim text: context lines
    """

    def render_diff(
        self,
        old_text: str,
        new_text: str,
        file_path: str = "",
    ) -> None:
        """Compute and display diff between old and new text.

        Unified format (default): shows old and new side by side
        with line numbers and color coding.
        """
```

---

## 6. tui/rendering/markdown.py

References: Rust xai-grok-markdown (pulldown-cmark + syntect)

### Function: render_markdown

```
from rich.markdown import Markdown

def render_markdown(text: str) -> Markdown:
    """Convert Markdown text to Rich renderable.

    Phase 3 uses rich.Markdown (batteries included).
    Features: code blocks, tables, lists, links, images.

    Phase 4 upgrade path: pulldown-cmark Python bindings for
    better GFM compliance and streaming rendering.
    """


def render_code_block(code: str, language: str) -> str:
    """Highlight a code block using pygments.

    Auto-detects language from shebang or file extension if not specified.
    Falls back to plain text if pygments doesn't support the language.
    """
```

---

## 7. skills/manager.py

References: Rust SkillManager (skill_discovery_tracker/mod.rs)

### Skill 调用工具——SkillTool

模型通过 `skill` 工具调用用户定义的 Skill。这是一个独立的工具（注册名 `"skill"`），
而非 SkillManager 的方法——因为模型需要像调用 read_file/bash 一样调用它。

```python
from pydantic import BaseModel, Field

class SkillToolInput(BaseModel):
    """Skill 工具输入。"""
    skill: str = Field(description="要调用的 skill 名称（如 'commit'）")
    args: str | None = Field(default=None, description="传递给 skill 的可选参数")


class SkillTool:
    """Skill 工具——模型通过它调用用户定义的 Skill。

    注册名: "skill"
    ToolKind: Other

    行为:
    1. 从 SkillManager 查找指定名称的 skill
    2. 找到 -> 返回 <skill name="..." description="...">body</skill> 包裹的 skill body
    3. 未找到 -> 返回 "Skill 'xxx' not found. Available: [...]"

    注册方式——将类实例的方法适配为 ToolRegistry 装饰器格式：

        skill_tool = SkillTool(manager=skill_manager)
        registry.register(
            "skill",
            SkillToolInput,
            description="调用用户定义的 Skill。输入 skill 名称和可选参数。",
            category="skill",
        )(skill_tool.run)
        # ToolRegistry.register 是装饰器（返回 Callable[[Callable], Callable]），
        # 可以当作普通函数调用，直接传入 handler。

    为什么是类而非函数：SkillTool 需要持有 SkillManager 引用（构造时注入），
    而 ToolRegistry 的 handler 是无状态的 async function。
    类封装状态 + run 方法暴露为 handler，是最小摩擦的适配方式。
    """

    def __init__(self, manager: "SkillManager"): ...
    async def run(self, input: SkillToolInput) -> str: ...
```

**注册时机**：Phase 3 的 `_build_tools()` 中，在 SkillManager 创建后注册此工具。

### Class: SkillManager

```
class SkillManager:
    """Skill lifecycle management.

    Responsibilities:
    1. Discover all SKILL.md files at startup (project + user dirs)
    2. Watch filesystem for changes -> automatic rediscovery
    3. Format <available_skills> block for system prompt injection
    4. Format individual <skill> block when model invokes a skill

    Discovery paths (in order, first found takes precedence):
    - {cwd}/.code-agent/skills/** /SKILL.md
    - ~/.code-agent/skills/** /SKILL.md
    - Max recursion depth: 5 directory levels
    """

    def __init__(self, cwd: Path): ...

    async def load_all(self) -> list[SkillInfo]:
        """Scan and load all SKILL.md files.
        Deduplicates by skill name (first found wins per directory depth)."""

    async def reload_if_changed(self) -> list[SkillInfo]:
        """Check for new/modified/deleted SKILL.md files.
        Only reloads files whose mtime has changed since last load."""

    def format_available_skills(self, skills: list[SkillInfo]) -> str:
        """Generate <available_skills> block for system prompt.

        Format:
        <available_skills>
        Each skill is listed with name, description, and trigger hint.
        </available_skills>
        """

    def format_skill_invocation(self, skill: SkillInfo) -> str:
        """Format skill for model execution.

        Format:
        <skill name="name" description="desc" path="path">
        {Markdown body content}
        </skill>

        The model follows the skill's instructions as if they were
        additional system instructions scoped to this turn.
        """
```

### skills/models.py

```
from pydantic import BaseModel

class SkillInfo(BaseModel):
    """Parsed SKILL.md file information."""
    name: str              # Skill name (e.g., "commit", "review-pr")
    description: str       # Short description for available_skills listing
    body: str              # Markdown body (instructions for the model)
    path: str              # Absolute file path
    scope: str             # "project" | "user" | "global"
    when_to_use: str = ""  # Trigger hint for model decision-making
    allowed_tools: list[str] = []  # Tool whitelist for skill execution
```

### skills/discovery.py

```
async def discover_skills(
    cwd: Path,
    user_skills_dir: Path | None = None,
) -> list[SkillInfo]:
    """Scan filesystem for SKILL.md files.

    Strategy:
    1. Walk project directory: {cwd}/.code-agent/skills/
    2. Walk user directory: ~/.code-agent/skills/
    3. Each directory: find SKILL.md files, parse frontmatter + body
    4. Deduplicate by name (first found wins, sorted by path depth)
    """
```

### skills/parser.py

```
import yaml

def parse_skill_md(filepath: Path) -> SkillInfo:
    """Parse SKILL.md file.

    Expected format:
    ---
    name: commit
    description: Create well-formatted git commits
    when_to_use: user asks to commit changes
    allowed_tools: [bash, read_file]
    ---
    # Instructions
    1. Run 'git diff --staged' to see changes
    2. Write a concise commit message
    3. Run 'git commit -m "message"'

    Frontmatter parsing is resilient: falls back to quoting retry
    for edge cases, then line-based recovery for top-level scalars.
    """
```

---

## 8. core/plan_mode.py

References: Rust PlanModeState (plan_mode.rs:17-43)

### Types

```
from enum import Enum

class PlanState(Enum):
    INACTIVE = "inactive"
    PENDING = "pending"            # User toggled ON via /plan, waiting for prompt
    ACTIVE = "active"              # Plan instructions injected, write tools blocked
    EXIT_PENDING = "exit_pending"  # User toggled OFF, waiting for turn completion
```

### Class: PlanModeTracker

```
class PlanModeTracker:
    """Plan Mode finite state machine.

    State transitions:
    INACTIVE -> PENDING     (user runs /plan command)
    PENDING  -> ACTIVE      (next prompt arrives, plan instructions injected)
    PENDING  -> INACTIVE    (user cancels before prompt)
    ACTIVE   -> EXIT_PENDING (user runs /plan while turn in flight)
    EXIT_PENDING -> INACTIVE (turn completes, exit reminder injected)
    ACTIVE   -> INACTIVE    (ExitPlanMode tool called + user approved)

    While ACTIVE:
    - Write tools are blocked: search_replace, write_file, bash writes
    - Read tools remain available: read_file, grep, list_dir
    - Plan-mode tools available: enter_plan_mode, exit_plan_mode
    - System prompt includes plan mode instructions
    """

    def __init__(self, plan_file_path: Path): ...

    def activate(self) -> None:
        """Transition to PENDING state."""

    def on_prompt_arrived(self) -> str:
        """Handle prompt arrival. **幂等**——非 PENDING 状态返回 ""，不会误触发。

        PENDING 状态时：转换为 ACTIVE，返回 plan mode 指令文本（用于注入对话）。
        """

    def deactivate(self) -> None:
        """Request exit. Transitions to EXIT_PENDING if turn in flight,
        otherwise directly to INACTIVE."""

    def on_turn_complete(self) -> str | None:
        """Handle turn completion. If EXIT_PENDING, transitions to INACTIVE
        and returns exit reminder."""

    def is_active(self) -> bool:
        """True if currently ACTIVE."""

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Check if a tool call is permitted in the current state.
        ACTIVE: only read tools + plan-mode tools allowed."""

    def get_plan_file_path(self) -> Path:
        """Return path to the plan file for model reference."""
```

---

## 9. cli/headless.py

References: Rust headless mode (--output-format)

### Functions

```
async def run_headless_plain(agent: Agent, prompt: str) -> str:
    """Headless plain-text output. Returns agent response as string."""


async def run_headless_json(agent: Agent, prompt: str) -> None:
    """Headless JSON output to stdout.

    Output format:
    {
      "text": "Agent full response text",
      "stopReason": "EndTurn",
      "sessionId": "abc123",
      "num_turns": 7,
      "usage": {
        "input_tokens": 7210,
        "output_tokens": 1893
      }
    }
    """


async def run_headless_streaming(agent: Agent, prompt: str) -> None:
    """Headless streaming-json output (NDJSON).

    Each line is a self-contained JSON object:
    {"type":"text","data":"Here is"}
    {"type":"text","data":" the answer"}
    {"type":"end","stopReason":"EndTurn","sessionId":"abc123","usage":{...}}
    """
```

---

## 10. TUI-Agent Data Flow

```
User keystrokes
  |
  v
tui/widgets/prompt_input.py   (Textual TextArea widget)
  |
  +-- @ character -> action_open_file_picker()
  |     -> fuzzy file search (fzf or built-in)
  |     -> insert selected file path as reference
  |
  +-- / character -> action_show_command_palette()
  |     -> display available slash commands
  |     -> insert selected command + args
  |
  +-- Enter -> action_submit()
        |
        v
tui/screens/chat.py
  -> assemble complete prompt (text + @refs + expanded skills)
  -> asyncio.create_task(agent.run(prompt_text))
     |
     v
  Agent yields StreamEvent:
     - text_delta      -> scrollback.append_text()
     - tool_call_delta -> scrollback.append_tool_call()
     - thinking_delta  -> scrollback.append_thinking()
     - turn_end        -> scrollback.mark_turn_complete()
                          -> status_bar.update()
                          -> check plan mode state
```

Key design: Agent and TUI share same asyncio event loop. Agent runs as a Task. TUI receives events via Textual message queue. Zero threading overhead.

### 取消传播链（Ctrl+C）

用户按 Ctrl+C 时，取消信号沿以下链路传播：

```
Ctrl+C (TUI key binding)
  → CodeAgentApp.action_cancel()
  → self._agent_task.cancel()          # asyncio.Task.cancel()
  → Agent.run() 内部：CancelledError 在最近的 await 点抛出
     （可能是 chat_stream 的 SSE 读取、_execute_one_tool 的 subprocess wait、
      或 hook 的 await 调用）
  → finally 块：Session._repair_dangling_calls()
     （清理未完成的 tool call——注入 is_error=True 的占位 ToolResult）
  → yield StreamEvent(kind="interrupted")
  → TUI ScrollbackWidget 显示 "[Request interrupted by user]"
  → PromptWidget 重新获得焦点，等待下一次输入
```

**实现要点**：
- `action_submit()` 中保存 `self._agent_task = asyncio.create_task(...)`，`action_cancel()` 对其调用 `.cancel()`
- Agent.run() 的 async generator 需要在 `try/finally` 中确保 dangling repair 执行——即使被 cancel 也保证 Session 状态一致
- 若 CancelledError 发生在 `shell_exec` 的 `await proc.wait()` 期间，需在 except 中 `proc.kill()` 防止子进程泄漏
- Phase 1 CLI 模式的 Ctrl+C 走同一路径（KeyboardInterrupt → task cancel），无需额外处理

---

## 11. Phase 3 Dependencies

```toml
[project]
dependencies = [
    # Phase 1-2:
    "openai>=1.0",
    "pydantic>=2.0",
    "aiofiles>=23.0",
    "aiosqlite>=0.20",
    "blake3>=0.4",
    # Phase 3 new:
    "textual>=0.50",
    "rich>=13.0",
    "pygments>=2.17",
    "pyyaml>=6.0",
]
```

---

## 12. 补充：之前缺失的 API 签名

这些模块在目录结构中列出但未给类签名，实施前需补设计。

### tui/widgets/todo_panel.py

```
class TodoPanel(Widget):
    """侧边栏 Todo 列表。快捷键 Ctrl+T 切换显隐。"""
    def update_todos(self, todos: list[dict]) -> None: ...
    def toggle_visibility(self) -> None: ...
```

### tui/widgets/status_bar.py

```
class StatusBar(Widget):
    """底部状态栏。显示: 模型名 | token数 | 耗时 | 权限模式。"""
    def update(self, model: str, tokens: int, elapsed: float, yolo: bool) -> None: ...
```

### tui/widgets/thinking_block.py

```
class ThinkingBlock(Widget):
    """可折叠的思考过程展示。默认折叠，按 Enter 或点击展开。"""
    def __init__(self, text: str): ...
    def append(self, chunk: str) -> None: ...
    def toggle(self) -> None: ...
```

### tui/theme.py

```
class Theme:
    """主题管理。支持 light/dark/auto 三种模式。"""
    def __init__(self, mode: str = "auto"): ...
    def resolve(self) -> dict[str, str]:
        """返回当前主题的 CSS 变量字典。auto 模式检测 OS 外观。"""
    def to_textual_theme(self) -> textual.theme.Theme: ...
```

### tui/rendering/syntax_highlight.py

```
def highlight_code(code: str, language: str, theme: str = "monokai") -> str:
    """Pygments 语法高亮。输出 ANSI 转义序列字符串。"""

def detect_language(filepath: str) -> str:
    """从文件扩展名推断编程语言。"""
```

### tui/screens/chat.py

```
class ChatScreen(Screen):
    """主聊天界面。组合 Scrollback + Prompt + StatusBar + TodoPanel。"""
    def __init__(self, agent: Agent, session: Session): ...
    async def on_mount(self) -> None: ...
    async def action_submit(self, text: str) -> None: ...
    def compose(self) -> ComposeResult: ...
```

---

## 12. 本 Phase 的 hooks 装配

Phase 3 不修改 `core/agent.py`。Plan Mode 通过 `PlanModeHook` 注入，Skill 工具通过 `ToolRegistry` 注册（非 Hook）：

```python
# cli/main.py 的 _build_agent()——Phase 3 版本（在 Phase 2 基础上追加）

def _build_agent(args, session: Session, client: ChatClient, tools: ToolRegistry) -> Agent:
    # Phase 2 hooks（保持不变）
    memory_index = MemoryIndex(db_path=...)
    store = SessionStore(root_dir=...)
    policy = CompactionPolicy()

    # Phase 3 新增：Plan Mode 状态机
    plan_tracker = PlanModeTracker(plan_file_path=Path(args.cwd) / "plan.md")

    hooks: list[AgentHook] = [
        MemoryHook(memory_index),
        CompactionHook(policy),
        PersistenceHook(store),
        EditTrackerHook(),
        PlanModeHook(plan_tracker),  # ← Phase 3 追加
    ]
    # Phase 4 在此列表继续追加

    return Agent(session, client, tools,
                 max_turns=args.max_turns,
                 hooks=hooks)
```

Skill 工具（Phase 3 的另一功能）不走 AgentHook 管道——它注册到 ToolRegistry：
```python
# _build_tools() 中（Phase 3 版本）:
skill_manager = SkillManager(cwd=args.cwd)
skill_tool = SkillTool(manager=skill_manager)
registry.register("skill", SkillToolInput,
    description="调用用户定义的 Skill。输入 skill 名称和可选参数。",
    category="skill")(skill_tool.run)
# SkillTool 是 ToolRegistry 注册的工具，不是 AgentHook。
# 模型通过 tool_call 调用它，PlanModeHook 在 ACTIVE 状态下可以拦截 skill 工具。
```

关键点：
- `PlanModeHook` 追加到 hooks 列表末尾——权限相关 hook（Phase 4 PermissionHook）将在其后执行
- Skill 工具与 Plan Mode 的交互：`PlanModeHook.is_tool_allowed("skill")` → 取决于具体 Skill 的 `allowed_tools` 白名单

### PlanModeHook——适配器实现

```python
from code_agent.core.plan_mode import PlanModeTracker

class PlanModeHook:
    """将 PlanModeTracker 适配为 AgentHook 接口。

    Plan Mode 的工具拦截逻辑：
    - ACTIVE 状态 + 写工具（search_replace/write_file）→ DENY
    - ACTIVE 状态 + 读工具（read_file/grep/shell_exec 中的只读命令）→ ALLOW
    - 非 ACTIVE 状态 → ALLOW（不拦截）
    """

    def __init__(self, tracker: PlanModeTracker):
        self._tracker = tracker

    async def on_turn_start(
        self, session: "Session", turn: int, tools: list[dict]
    ) -> None:
        """若 Plan Mode 刚激活，注入 plan 指令。

        注入通道：session.add_user_message()——与 MemoryHook 的 synthetic system-reminder
        走同一通道（user message），而非直接修改 system prompt。原因：
        (1) 保证原始 system prompt 不被 phase 扩展污染
        (2) 模型看到 user 角色消息会将其视为上下文而非指令，更符合 Plan Mode 的"提醒"语义

        on_prompt_arrived() 是幂等的——非 PENDING 状态时返回空字符串，不会误触发状态转换。
        """
        instructions = self._tracker.on_prompt_arrived()
        if instructions:
            session.add_user_message(instructions)

    async def on_tool_call(self, tool_name: str, arguments: dict) -> HookDecision:
        if self._tracker.is_active():
            if not self._tracker.is_tool_allowed(tool_name):
                return HookDecision.DENY
        return HookDecision.ALLOW

    async def on_turn_end(self, session: "Session", turn: int) -> None:
        """Plan Mode 退出处理：EXIT_PENDING → INACTIVE + 注入退出提醒。"""
        reminder = self._tracker.on_turn_complete()
        if reminder:
            # 注入 synthetic user message 让模型在下一轮看到退出提醒
            session.add_user_message(reminder)
```

---

## 13. 验收清单

### TUI
- [ ] `CodeAgentApp` 启动显示欢迎界面
- [ ] 流式文本逐字追加到 Scrollback（不闪烁）
- [ ] Markdown 正确渲染（标题、列表、代码块、行内代码）
- [ ] 代码块语法高亮正确
- [ ] `@` 字符触发 fuzzy 文件选择器
- [ ] `/` 字符触发命令补全
- [ ] Ctrl+C 取消当前 turn，Ctrl+T 切换 Todo 面板
- [ ] 状态栏实时更新 token 数和耗时

### Skills
- [ ] `discover_skills()` 扫描到测试目录中的所有 SKILL.md
- [ ] `parse_skill_md()` 正确提取 YAML frontmatter + Markdown body
- [ ] `SkillManager.format_available_skills()` 输出正确格式
- [ ] `SkillManager.format_skill_invocation()` 输出 `<skill>` XML 块
- [ ] 模型调用 skill 工具后正确展开 skill body 到对话

### Plan Mode
- [ ] `/plan` 命令切换 PENDING → ACTIVE
- [ ] ACTIVE 状态下 search_replace 和 write_file 被拦截
- [ ] ACTIVE 状态下 read_file 和 grep 正常可用
- [ ] `/plan` 再次切换 → EXIT_PENDING → turn 完成后退出

### Headless
- [ ] `--output-format json` 输出单行 JSON（含 text/stopReason/sessionId/usage）
- [ ] `--output-format streaming-json` 输出 NDJSON 流
