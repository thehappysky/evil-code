# Python AI Coding Agent -- Phase 4 详细设计：完整生态

> 代码逻辑参考 Grok Build 的 permission、mcp、plugins、hooks、sandbox 模块。
> 命名遵循 Python 社区规范，按功能描述命名。格式对齐 Phase 1 详细设计。

---

## Phase 4 Goals

Build on Phase 3 with production-ready features:
- **Permission system**: 3-layer pipeline (YOLO / SafeCmd / UserConfirm)
- **MCP integration**: external tool hot-plug via JSON-RPC
- **Plugin system**: importlib-based discovery and loading
- **Hook lifecycle**: PreToolUse, PostToolUse, Session events
- **Sandbox**: optional Docker container isolation
- **Telemetry**: structured JSON logging and usage tracking

---

## 1. New Directory Structure

Phase 3 directories remain unchanged. Phase 4 only adds:

```
code_agent/
  ... (Phase 1/2/3 files untouched)

  permission/                # Permission system
    __init__.py              #   public: PermissionManager, Decision
    manager.py               #   3-layer decision pipeline
    safe_commands.py         #   Safe command whitelist (80+ entries)
    rules.py                 #   --allow/--deny glob rule parser

  sandbox/                   # Sandbox (optional, disabled by default)
    __init__.py              #   public: DockerSandbox
    docker_exec.py           #   Docker container command execution

  mcp/                       # MCP external tool integration
    __init__.py              #   public: McpClient, register_mcp_tools
    client.py                #   MCP JSON-RPC client (stdio + HTTP transports)
    transport.py             #   Transport abstraction layer
    adapter.py               #   MCP tool to ToolRegistry adapter

  plugins/                   # Plugin system
    __init__.py              #   public: PluginLoader, PluginManifest
    loader.py                #   importlib-based plugin discovery
    models.py                #   PluginManifest Pydantic model

  hooks/                     # Hook lifecycle events
    __init__.py              #   public: HookDispatcher, HookEvent
    dispatcher.py            #   Event dispatch engine

  telemetry/                 # Telemetry (opt-in)
    __init__.py              #   public: TelemetryLogger, UsageTracker
    logger.py                #   Structured JSON lines logging
    usage_stats.py           #   Token usage aggregation
```

---

## 2. permission/manager.py

References: Rust 7-layer pipeline (permission/manager.rs), simplified to 3

### Types

```
from enum import Enum

class Decision(Enum):
    ALLOW = "allow"    # Execute without prompting
    DENY = "deny"      # Block execution entirely
    ASK = "ask"        # Prompt user for confirmation
```

### Class: PermissionManager

```
class PermissionManager:
    """3-layer permission decision pipeline.

    Pipeline checked in strict priority order:
    1. YOLO mode enabled? -> ALLOW (skip all further checks)
    2. Safe command whitelist match? -> ALLOW (static analysis)
    3. User confirmation needed -> ASK (interactive prompt)

    Parameters:
        yolo_mode: if True, all tools auto-approved (--yolo flag)
        allow_rules: glob patterns for unconditional allow
        deny_rules: glob patterns for unconditional deny

    Rule syntax (compatible with Grok Build / Claude Code):
        Bash(npm*)       -- allow npm commands
        Bash(rm*)        -- deny rm commands
        Edit(**/*.py)    -- allow editing Python files
        WebFetch(domain:github.com) -- allow fetching from GitHub
    """

    def __init__(
        self,
        yolo_mode: bool = False,
        allow_rules: list[str] | None = None,
        deny_rules: list[str] | None = None,
    ): ...

    async def decide(self, tool_name: str, arguments: dict) -> Decision:
        """Check if a tool call needs user confirmation.

        Called before every tool execution in Agent._execute_one_tool().
        Returns ALLOW, DENY, or ASK.
        """

    def set_yolo(self, enabled: bool) -> None:
        """Toggle YOLO mode. Ctrl+O in TUI, --yolo in CLI."""

    def add_allow_rule(self, pattern: str) -> None:
        """Add an allow rule at runtime."""

    def add_deny_rule(self, pattern: str) -> None:
        """Add a deny rule at runtime."""
```

### permission/safe_commands.py

```
# Commands that are ALWAYS safe to execute without asking
# References: Rust is_safe_command_words() (permission/manager.rs:167)

SAFE_COMMANDS: list[str] = [
    # Read-only filesystem (Unix)
    "ls", "cat", "pwd", "date", "whoami", "hostname", "uptime",

    # Read-only filesystem (Windows)
    "dir", "type", "where",

    # Git read-only
    "git status", "git branch", "git log", "git diff",
    "git show", "git rev-parse", "git ls-files",

    # Package manager read-only
    "cargo check", "npm ls", "pip list", "pip show",

    # Text processing (Unix)
    "head", "tail", "wc", "sort", "uniq", "tr", "cut",

    # Text processing (Windows)
    "findstr",

    # Search
    "grep", "rg",

    # Kubernetes read-only
    "kubectl get", "kubectl logs", "kubectl describe",

    # Process inspection (Unix + Windows)
    "ps", "tasklist",
]

# EXPLICITLY excluded from safe list:
# - "rg --pre" -- can execute arbitrary programs
# - "tee"      -- can bypass edit permissions (CWE-863)
# - Any destructive kubectl commands (apply, delete, create)


def is_safe_command(command: str) -> bool:
    """Check if a shell command matches a safe prefix.

    Parses command with shlex, checks prefix against SAFE_COMMANDS.
    Returns False for any command containing dangerous flags
    (e.g., rg --pre).
    """
```

### permission/rules.py

```
def match_rule(pattern: str, tool_name: str, arguments: dict) -> bool:
    """Match a permission rule pattern.

    Pattern format: ToolPrefix(match_spec)

    Examples:
        Bash(npm*)     -> matches tool_name="bash" and command starts with "npm"
        Edit(**/*.py)  -> matches tool_name="search_replace" and file_path ends with ".py"
        WebFetch(domain:github.com) -> matches tool_name="web_fetch" and URL domain matches
        Bash           -> matches any bash call (bare prefix, no parentheses)

    Uses fnmatch for glob matching. Rules support * (single-level) and ** (recursive).
    """
```

---

## 3. mcp/client.py

References: Rust McpClient (xai-grok-mcp/src/servers.rs)

### Class: McpClient

```
class McpClient:
    """MCP (Model Context Protocol) JSON-RPC client.

    Supports two transport modes:
    - stdio: spawns the MCP server as a child process,
             communicates via stdin/stdout JSON-RPC 2.0
    - HTTP: connects to a remote MCP server over HTTP + SSE

    Protocol flow:
    1. initialize -> exchange capabilities and server information
    2. tools/list -> discover available tools (with cursor pagination)
    3. tools/call -> invoke a specific tool by name

    Connection lifecycle:
    - connect() establishes transport and runs handshake
    - Auto-reconnect on connection loss with exponential backoff
    - Health check via periodic ping (configurable interval)
    - Graceful shutdown via close()
    """

    def __init__(self, config: McpServerConfig): ...

    async def connect(self) -> None:
        """Establish transport connection and perform handshake.
        Raises McpConnectionError on failure."""

    async def list_tools(self) -> list[ToolDefinition]:
        """List all tools provided by this MCP server.
        Handles cursor-based pagination for servers with many tools (>100)."""

    async def call_tool(self, name: str, arguments: dict) -> dict:
        """Invoke a tool by name. Returns raw JSON-RPC result dict.
        Raises McpToolError if the server returns an error."""

    async def close(self) -> None:
        """Graceful shutdown: close transport, terminate child process."""

    @property
    def is_connected(self) -> bool:
        """True if transport is established and handshake completed."""
```

### mcp/transport.py

```
class StdioTransport:
    """Subprocess stdin/stdout JSON-RPC transport.
    Spawns MCP server as child process, communicates via line-delimited JSON."""


class HttpTransport:
    """HTTP + SSE transport for remote MCP servers.
    Uses httpx for HTTP requests and SSE client for streaming responses."""
```

### mcp/adapter.py

```
def register_mcp_tools(
    registry: ToolRegistry,
    mcp_client: McpClient,
) -> int:
    """Register all MCP server tools into the ToolRegistry.

    Naming convention: {server_name}__{tool_name}
    Double underscore separates server name from tool name.

    Example tools:
        github__search_issues
        github__create_pr
        postgres__query
        slack__send_message

    Each MCP tool is wrapped to:
    1. Forward arguments to MCP client.call_tool()
    2. Convert MCP response format to ToolResult
    3. Report errors with server name context

    Returns the number of tools successfully registered.
    """
```

---

## 4. plugins/loader.py

References: Rust plugin marketplace (xai-grok-plugin-marketplace)

### Class: PluginLoader

```
import importlib

class PluginLoader:
    """importlib-based plugin discovery and loading.

    Plugin directory structure:
    ~/.code-agent/plugins/{plugin_name}/
      plugin.toml       -- metadata (name, version, description, author)
      tools.py          -- extra tool functions (optional)
      skills/           -- extra Skills directory (optional)
      hooks.py          -- lifecycle hook handlers (optional)

    Discovery order (project overrides user — deeper scope wins):
    1. Built-in plugins: bundled with code_agent package
    2. User plugins: ~/.code-agent/plugins/
    3. Project plugins: {cwd}/.code-agent/plugins/
    Later entries override earlier ones for same-name conflicts.
    """

    def __init__(self, plugins_dir: Path | None = None): ...

    def discover(self) -> list[PluginManifest]:
        """Scan plugin directories and return all valid plugin manifests.
        Invalid or malformed plugins are skipped with a warning."""

    def load_tools(self, plugin: PluginManifest) -> list[Callable]:
        """Load tool functions from plugin's tools.py module.
        Uses importlib.import_module() for dynamic loading.
        Each loaded function is a standard async tool handler."""

    def load_hooks(self, plugin: PluginManifest) -> list[HookHandler]:
        """Load hook handlers from plugin's hooks.py module.
        Each handler is registered with HookDispatcher."""
```

### plugins/models.py

```
from pydantic import BaseModel

class PluginManifest(BaseModel):
    """Plugin metadata parsed from plugin.toml."""
    name: str
    version: str
    description: str = ""
    author: str = ""
    tools: list[str] = []        # Module names providing tool functions
    skills: list[str] = []       # Directory names containing SKILL.md files
    hooks: list[str] = []        # Module names providing hook handlers
```

### 安全讨论：插件的信任边界

**边界声明**：Phase 4 的插件系统定位为**可信本地插件**——仅加载用户主动安装的本地插件，
不提供应用商店/社区分发能力。在此信任模型下，插件等同于用户自己写的代码，
与 `~/.bashrc` 或 VS Code 扩展具有相同的信任级别。Phase 4 MVP 不实现安全防护。

Phase 4 的插件系统使用 `importlib.import_module()` 加载 `~/.code-agent/plugins/` 下的 Python 代码。
与 Grok Build 的 Plugin Marketplace（Git-based + 索引校验 + 用户显式安装）不同，当前设计缺少安全性防护。

**MVP 安全基线（Phase 4 实现）**：
- 只加载用户显式 `code-agent plugin install` 安装的插件（来源白名单），不自动扫描目录下所有代码

**Growth Path（未来版本，非 Phase 4 硬性要求）**：

| 约束 | 实现方式 | 触发条件 |
|:---|:---|:---|
| 签名校验 | 插件安装时验证 SHA256 校验和（从 plugin.toml 的 `checksum` 字段读取） | 引入社区分发时 |
| 沙箱执行 | 插件工具默认在 DockerSandbox 中运行（不直接访问宿主机文件系统） | 引入第三方插件时 |
| 权限声明 | plugin.toml 中声明 `permissions: [read, write, network]`，由 PermissionManager 强制执行 | 引入第三方插件时 |
| 审计日志 | 所有插件加载和调用记录到 telemetry，标记 `source="plugin:{name}"` | 遥测功能就绪后 |

参考实现（Grok Build）的 Plugin Marketplace 使用了 Git 索引 + 显式安装流程，
不直接从目录加载未验证的代码。当本项目引入社区分发能力时，应对齐此安全级别。

---

## 5. hooks/dispatcher.py

References: Rust xai-agent-lifecycle + xai-grok-hooks

### Types

```
from enum import Enum

class HookEvent(Enum):
    PRETOOL_USE = "pre_tool_use"                  # Before tool execution
    POST_TOOL_USE = "post_tool_use"               # After successful execution
    POST_TOOL_USE_FAILURE = "post_tool_use_failure"  # After failed execution
    SESSION_START = "session_start"               # Session creation
    SESSION_END = "session_end"                   # Session teardown
    TURN_START = "turn_start"                     # Each turn begins
    TURN_END = "turn_end"                         # Each turn ends


class HookResult:
    """Result from a hook handler.
    
    动作命名与 Phase 1 AgentHook.HookDecision 保持一致（见 `Python-Agent-MVP-详细设计.md` §3.7.1）。
    """
    action: Literal["allow", "deny", "modify"]
    message: str | None = None    # Optional message for the model
    modified_args: dict | None = None  # Modified tool arguments (modify action)
```

### Class: HookDispatcher

```
class HookDispatcher:
    """Lifecycle event dispatcher.

    Design:
    - Handlers registered per-event type
    - Multiple handlers per event (called in registration order)
    - PreToolUse: first "deny" result stops the chain and prevents execution
    - PostToolUse/PostToolUseFailure: all handlers run (best-effort, errors logged)
    - SessionStart/SessionEnd/TurnStart/TurnEnd: all handlers run

    Handler signature: async def handler(event: HookEvent, context: dict) -> HookResult
    """

    def __init__(self): ...

    def register(self, event: HookEvent, handler: Callable) -> None:
        """Register a hook handler for a specific event.
        Handlers are called in registration order."""

    async def dispatch(
        self, event: HookEvent, context: dict
    ) -> HookResult:
        """Dispatch event to all registered handlers.

        For PreToolUse: returns "deny" if any handler blocks execution.
        For non-blocking events: always returns "allow".
        Handler exceptions are caught and logged (never crash the agent).
        """
```

---

## 6. sandbox/docker_exec.py

References: Rust xai-grok-sandbox (nono: Landlock/Seatbelt)

Python alternative: Docker container isolation (cross-platform, no kernel API dependency).

### Class: DockerSandbox

```
class DockerSandbox:
    """Docker-based command execution sandbox.

    Security measures applied to every command execution:
    - Read-only bind mount of working directory
    - Network disabled (--network=none)
    - Memory limit (default 512MB)
    - CPU limit (default 1 core)
    - Auto-remove container after execution (--rm)
    - Non-root user inside container
    - Timeout with SIGKILL fallback

    Usage:
        sandbox = DockerSandbox()
        result = await sandbox.execute("npm test", timeout=60)

    Configured via shell_exec tool: passes sandbox=True to opt in.
    Disabled by default (None sandbox -> direct subprocess execution).
    """

    def __init__(
        self,
        image: str = "python:3.11-slim",
        workdir: str = "/workspace",
        network: str = "none",
        memory: str = "512mb",
        cpus: str = "1",
    ): ...

    async def execute(self, command: str, timeout: int = 120) -> ToolResult:
        """Execute command in isolated Docker container.

        Steps:
        1. docker run -d with security flags
        2. Wait for completion or timeout
        3. Collect stdout+stderr
        4. On timeout: docker kill
        5. Return ToolResult with merged output + exit code
        """

    async def is_available(self) -> bool:
        """Check if Docker is installed and accessible."""
```

---

## 7. telemetry/logger.py

References: Rust xai-grok-telemetry unified_log

### Class: TelemetryLogger

```
class TelemetryLogger:
    """Structured JSON event logging.

    Output: ~/.code-agent/logs/unified.jsonl (JSON lines format)
    Each line is a self-contained JSON object.

    Event types logged:
    - session_create, session_resume, session_end
    - turn_start, turn_complete
    - tool_call, tool_result
    - compaction
    - error

    Privacy guarantees:
    - No prompts, code content, file paths, or shell commands are logged
    - Session IDs and tool names are logged (needed for debugging)
    - Opt-in content logging via CODE_AGENT_LOG_CONTENT=1 env var
    """

    def __init__(self, log_dir: Path | None = None): ...

    def log(self, event: str, **kwargs) -> None:
        """Log a structured event.
        Auto-adds timestamp, session_id, and event type."""


class UsageTracker:
    """Token usage aggregation.

    Dimensions: session, model, date.
    Methods for recording and querying usage statistics.
    Used for cost estimation and model selection guidance.

    Data stored in-memory during session; persisted to
    ~/.code-agent/logs/usage.jsonl on session end.
    """

    def record_usage(
        self,
        session_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None: ...

    def get_session_stats(self, session_id: str) -> dict:
        """Return {total_input_tokens, total_output_tokens, turns, tools_called}."""

    def get_model_stats(self, model: str) -> dict:
        """Return aggregate stats per model across all sessions."""
```

---

## 8. Phase 4 Dependencies

```toml
[project]
dependencies = [
    # Phase 1-3:
    "openai>=1.0",
    "pydantic>=2.0",
    "aiofiles>=23.0",
    "aiosqlite>=0.20",
    "blake3>=0.4",
    "textual>=0.50",
    "rich>=13.0",
    "pygments>=2.17",
    "pyyaml>=6.0",
    # Phase 4 new (all optional):
    "mcp>=1.12",         # MCP Python SDK — https://pypi.org/project/mcp/
                         #   官方 Anthropic SDK (github.com/modelcontextprotocol/python-sdk)
                         #   注意: 版本 < 1.9.4 有已知 CVE (CVE-2025-53366)，需 >= 1.9.4
    "docker>=7.0",       # Docker SDK for sandbox — 仅 DockerSandbox 功能需要
]
```

---

## 10. 补充：之前缺失的 API 签名

### mcp/transport.py

```
class StdioTransport:
    """子进程 stdin/stdout JSON-RPC 传输。

    启动 MCP server 作为子进程，每行一个 JSON-RPC 消息。
    自动处理进程启动失败和意外退出。
    """
    def __init__(self, command: list[str], env: dict | None = None): ...
    async def start(self) -> None: ...
    async def send(self, message: dict) -> None: ...
    async def receive(self) -> dict: ...
    async def close(self) -> None: ...
    @property
    def is_alive(self) -> bool: ...


class HttpTransport:
    """HTTP + SSE 传输——连接远程 MCP server。

    使用 httpx 发送 JSON-RPC 请求，SSE 接收服务器推送。
    """
    def __init__(self, url: str, headers: dict | None = None): ...
    async def connect(self) -> None: ...
    async def send(self, message: dict) -> dict: ...
    async def close(self) -> None: ...
```

---

## 10. 本 Phase 的 hooks 装配

Phase 4 不修改 `core/agent.py`。权限和用户 Hook 通过 `PermissionHook` + `LifecycleHook` 注入：

```python
# cli/main.py 的 _build_agent()——Phase 4 最终版本（在 Phase 3 基础上追加）

def _build_agent(args, session: Session, client: ChatClient, tools: ToolRegistry) -> Agent:
    # Phase 2 hooks（保持不变）
    memory_index = MemoryIndex(db_path=...)
    store = SessionStore(root_dir=...)
    policy = CompactionPolicy()

    # Phase 3 hooks（保持不变）
    plan_tracker = PlanModeTracker(plan_file_path=Path(args.cwd) / "plan.md")

    # Phase 4 新增：权限管理器 + 用户 Hook 分发器
    permission_mgr = PermissionManager(
        yolo_mode=args.yolo,
        allow_rules=args.allow_rules or [],
        deny_rules=args.deny_rules or [],
    )
    lifecycle_dispatcher = HookDispatcher()
    # 用户定义的 hook 脚本通过 PluginLoader 加载后注册到 lifecycle_dispatcher

    hooks: list[AgentHook] = [
        MemoryHook(memory_index),
        CompactionHook(policy),
        PersistenceHook(store),
        EditTrackerHook(),
        PlanModeHook(plan_tracker),
        PermissionHook(permission_mgr),         # ← Phase 4 追加：权限决策（工具调用前）
        LifecycleHook(lifecycle_dispatcher),    # ← Phase 4 追加：用户 Hook 脚本（Pre/PostToolUse）
    ]

    return Agent(session, client, tools,
                 max_turns=args.max_turns,
                 hooks=hooks)
```

Hook 执行顺序决定了权限管线的优先级：
1. `PlanModeHook.on_tool_call` — Plan Mode ACTIVE 时先拦截写工具
2. `PermissionHook.on_tool_call` — 权限决策管线（YOLO → SafeCmd → UserConfirm）决定允许/拒绝
3. `LifecycleHook.on_tool_call` — 用户定义的 PreToolUse hook 脚本（最后执行，可以 MODIFY 参数）

### PermissionHook——适配器实现

```python
from code_agent.permission.manager import PermissionManager, Decision

class PermissionHook:
    """将 PermissionManager 适配为 AgentHook 接口。

    Decision 映射：
    - ALLOW → HookDecision.ALLOW
    - DENY  → HookDecision.DENY
    - ASK   → 弹出用户确认或检查缓存；用户拒绝时返回 DENY
    """

    def __init__(self, permission_manager: PermissionManager):
        self._pm = permission_manager

    async def on_tool_call(self, tool_name: str, arguments: dict) -> HookDecision:
        decision = await self._pm.decide(tool_name, arguments)

        if decision == Decision.ALLOW:
            return HookDecision.ALLOW
        elif decision == Decision.DENY:
            return HookDecision.DENY
        elif decision == Decision.ASK:
            # ASK 需要用户交互。AgentHook 协议不支持直接交互，
            # PermissionHook 在此调用交互层（如 TUI 的 confirm 对话框）。
            # 若在 headless 模式（无交互能力），默认拒绝。
            return await self._handle_ask(tool_name, arguments)
        return HookDecision.ALLOW

    async def _handle_ask(self, tool_name: str, arguments: dict) -> HookDecision:
        """处理 ASK 决策——弹出确认或检查缓存。

        实现依赖运行模式：
        - TUI 模式：弹出 confirm 对话框（同步等待用户输入）
        - Headless 模式：检查 --yes 标志，否则拒绝
        - 可在构造函数注入 _ask_handler: Callable 以解耦
        """
        ...
```

### LifecycleHook——适配器实现（已在 Phase 1 §3.7.5 给出完整 MODIFY 映射代码，此处列出其余方法）

```python
from code_agent.hooks.dispatcher import HookDispatcher, HookEvent

class LifecycleHook:
    """将 Phase 4 HookDispatcher 包装为 AgentHook 接口。"""

    def __init__(self, dispatcher: HookDispatcher):
        self._dispatcher = dispatcher

    async def on_session_start(self, session: "Session") -> None:
        await self._dispatcher.dispatch(HookEvent.SESSION_START, {"session": session})

    async def on_tool_call(self, tool_name: str, arguments: dict) -> HookDecision:
        # 见 Phase 1 §3.7.5 完整实现（含 deny/modify/allow 三态映射）
        ...

    async def on_tool_result(self, tool_name: str, arguments: dict, result) -> None:
        await self._dispatcher.dispatch(HookEvent.POST_TOOL_USE, {
            "tool_name": tool_name, "arguments": arguments, "result": result,
        })

    async def on_turn_end(self, session: "Session", turn: int) -> None:
        await self._dispatcher.dispatch(HookEvent.TURN_END, {
            "session": session, "turn": turn,
        })
```

---

## 11. 验收清单

### 权限
- [ ] `PermissionManager.decide("bash", {"command": "rm -rf /"})` → DENY
- [ ] `PermissionManager.decide("bash", {"command": "ls"})` → ALLOW（SafeCmd 白名单）
- [ ] YOLO 模式 → 所有工具 ALLOW
- [ ] `--deny "Bash(rm*)"` → `decide("bash", {"command":"rm file"})` → DENY
- [ ] 权限拒绝时 Agent 不崩溃，返回 ToolResult.error()

### MCP
- [ ] `McpClient(stdio_config).connect()` → 成功握手
- [ ] `McpClient.list_tools()` → 返回工具列表（含 cursor 分页处理）
- [ ] `McpClient.call_tool(name, args)` → 返回 JSON-RPC 结果
- [ ] `register_mcp_tools(registry, client)` → 工具正确注册为 `server__tool` 格式

### 插件
- [ ] `PluginLoader.discover()` → 扫描到测试插件
- [ ] `PluginLoader.load_tools(plugin)` → 加载的函数可正常注册到 ToolRegistry
- [ ] `PluginLoader.load_hooks(plugin)` → 加载的 handler 可注册到 HookDispatcher
- [ ] 项目插件覆盖同名用户插件（deeper scope wins）

### Hook
- [ ] `PreToolUse` handler 返回 deny → 工具执行被阻止
- [ ] `PreToolUse` handler 返回 modify → 修改后的参数传给工具
- [ ] `PostToolUse` handler 正常执行 → 不影响 Agent 流程
- [ ] handler 抛出异常 → 被捕获并记录，不崩溃 Agent

### 沙箱
- [ ] `DockerSandbox.execute("echo hello")` → 返回 stdout
- [ ] `DockerSandbox.execute("sleep 10", timeout=2)` → 超时返回 ToolResult.error()
- [ ] Docker 未安装时 `is_available()` 返回 False，Agent 降级为直接 subprocess

### 遥测
- [ ] `TelemetryLogger.log("turn_complete", tokens=1234)` → 写入 JSON line
- [ ] 不记录 prompt 内容和文件路径（隐私保护）
- [ ] `UsageTracker` 正确聚合跨 session 的 token 使用量

---

## 9. Growth Summary: Phase 1 -> Phase 4

| Phase | New Dirs | Cumulative | Approx Files | Key Milestone |
|:---|:---|:---|:---|:---|
| 1 | 6 | 6 | ~20 | Agent loop runs in terminal with 6 tools |
| 2 | +4 | 10 | ~35 | Session resume + cross-session memory |
| 3 | +2 | 12 | ~50 | Full TUI + Skills + Plan Mode |
| 4 | +6 | 18 | ~70 | Production-ready coding agent |

Each phase only adds new directories and files. Existing code is never restructured.
Phase 1 `core/agent.py` works identically through all phases.
