# Python Agent MVP 实现路线（整合 cc-switch 认证）

> 基于 Grok Build 架构分析 + Python 生态适配 + cc-switch 源码认证方案
>
> **⚠️ 命名注意：本文件使用初版草稿命名（`grok-py/`、`agent/`、`LLMClient`、`ChatState` 等），
> 实际开发时请以各 Phase 详细设计中的命名为准。**
>
> | 本文件旧名 | 详细设计新名 | 理由 |
> |:---|:---|:---|
> | `grok-py/` / `agent/` | `code_agent/` | 描述功能而非项目代号 |
> | `~/.grok-py/` | `~/.code-agent/` | 同上 |
> | `LLMClient` | `ChatClient` | 描述功能：聊天客户端 |
> | `ChatState` | `Session` | 描述功能：会话状态 |
> | `core/loop.py` | `core/agent.py` | 描述功能：Agent 主循环 |
> | `tools/base.py` | `tools/definition.py` | 描述功能：工具定义 |
> | `read_file / grep / bash` | `file_read / file_search / shell_exec` + `file_edit / file_write / web_fetch` | Phase 1 最终为 6 个工具 |

---

## 总体架构决策

| 决策点 | 选择 | 理由 |
|:---|:---|:---|
| 异步模型 | asyncio 单进程多协程 | Python GIL 在 I/O 密集场景不是瓶颈，单线程天然无竞态 |
| 工具执行 | asyncio.create_subprocess_exec | 简单命令开销低，复杂工具通过 ProcessPoolExecutor |
| 类型安全 | Pydantic v2 | 运行时验证，JSON Schema 自动生成 |
| TUI | Textual | Python 最成熟的 TUI 框架，声明式布局 |
| LLM SDK | ~~openai (官方)~~ → httpx direct + Backend Protocol | 多 provider 支持（OpenAI/Anthropic/DeepSeek 等），三后端协议转换，不绑定单一 SDK |
| 存储 | JSONL + aiosqlite | JSONL 用于会话，SQLite 用于记忆/搜索 |
| **认证** | **五级认证: CLI --api-key > config.yaml > env > settings.json > OAuth** | 原生配置免登录 + cc-switch 兼容 + OAuth |

---

## 分层路线

### Phase 1：最小可行 Agent（3-5 天）

**目标**：在命令行中完成 "用户输入 → LLM 推理 → 工具调用 → 返回结果" 的完整闭环，验证核心架构可行性。

#### 1.1 项目骨架

```
grok-py/
├── pyproject.toml
├── agent/
│   ├── __init__.py
│   ├── auth/                    ← 从 cc-switch 移植
│   │   ├── oauth.py             ← 从 ccswitch-python/ccswitch/oauth.py
│   │   └── creds.py             ← 从 ccswitch-python/ccswitch/creds.py
│   ├── api/
│   │   └── client.py            ← AsyncOpenAI 封装
│   ├── core/
│   │   ├── state.py             ← ChatState（对话历史）
│   │   ├── loop.py              ← Agent 推理循环
│   │   └── schemas.py           ← ConversationItem Pydantic 模型
│   └── tools/
│       ├── registry.py          ← 工具注册表（装饰器模式）
│       ├── builtin/
│       │   ├── read_file.py
│       │   ├── grep.py
│       │   └── bash.py
│       └── base.py              ← Tool Protocol
├── cli.py                       ← 命令行入口
└── tests/
```

#### 1.2 认证模块（从 cc-switch 移植）

需要从 ccswitch-python 移植到 `agent/auth/` 的文件：

| 源文件 | 目标文件 | 修改 |
|:---|:---|:---|
| `oauth.py` | `agent/auth/oauth.py` | 几乎原样复用。常量 TOKEN_URL、CLIENT_ID 不变 |
| `creds.py` | `agent/auth/creds.py` | 移除 ccswitch 特有的备份逻辑，保留核心的 read/write + atomic 写 |

**不需要移植的部分**（MVP 单账户，暂不需要）：
- `vault.py`——多账户保险库，Phase 4 再加
- `crypto.py`——DPAPI 加密，Phase 4 再加
- `daemon.py`——自动轮换守护进程

#### 1.3 LLM 客户端（`agent/api/client.py`）

> **⚠️ 架构补强更新**：原设计使用 `AsyncOpenAI` 封装，实际实现已改为
> `httpx.AsyncClient` 直连 + `Backend Protocol` 多后端分发。
> 新增 `ProviderConfig` 配置类、`api/backends/` 三后端、`api/sse.py` 共享 SSE 解析。

```python
# 架构补强后的核心接口
class ChatClient:
    def __init__(self, config: ProviderConfig):
        self._config = config
        self._http_client = httpx.AsyncClient(timeout=300)
        self._backend = get_backend(config.api_backend)  # 工厂分发

    async def chat_stream(self, messages, tools, **overrides) -> AsyncIterator[StreamEvent]:
        config = replace(self._config, **overrides) if overrides else self._config
        request = self._backend.build_request(messages, tools, config)  # 构建HTTP请求
        async with self._http_client.stream("POST", request.url, ...):
            async for sse in iter_sse_events(response):
                async for event in self._backend.parse_stream(sse):  # 后端解析
                    yield event

# ProviderConfig 定义
@dataclass
class ProviderConfig:
    api_backend: ApiBackend = ApiBackend.CHAT_COMPLETIONS
    base_url: str = "https://api.anthropic.com/v1"
    api_key: str | None = None
    auth_scheme: AuthScheme = AuthScheme.BEARER
    model: str = "claude-sonnet-4-5-20250501"
    max_tokens: int = 4096
    temperature: float = 0.7
```

**三后端协议**（`api/backends/`）：

| 后端 | 端点 | 格式 | 适用 provider |
|:---|:---|:---|:---|
| ChatCompletions | `/chat/completions` | OpenAI 格式 | OpenAI, DeepSeek (compat), 本地 Ollama |
| Messages | `/messages` | Anthropic 原生格式 | Anthropic, DeepSeek /anthropic 端点 |
| Responses | `/responses` | OpenAI 新格式 | OpenAI Responses API |

每个后端实现 `Backend` Protocol：`build_request() -> BackendRequest` + `parse_stream() -> AsyncIterator[StreamEvent]`

#### 1.4 对话状态（`agent/core/state.py`）

```python
@dataclass
class ChatState:
    conversation: list[ConversationItem] = field(default_factory=list)
    prompt_index: int = 0
    total_tokens: int = 0

    def push_user_message(self, content): ...
    def push_assistant_message(self, content, tool_calls): ...
    def push_tool_result(self, call_id, content): ...
    def to_api_messages(self) -> list[dict]: ...

# Pydantic 模型
class ConversationItem(BaseModel):
    type: Literal["system", "user", "assistant", "tool_result"]
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
```

#### 1.5 Agent 循环（`agent/core/loop.py`）

```python
class Agent:
    def __init__(self, state: ChatState, client: LLMClient, registry: ToolRegistry):
        ...

    async def run(self, user_input: str, max_turns: int = 20):
        self.state.push_user_message(user_input)

        for turn in range(max_turns):
            # 1. 构建请求
            messages = self.state.to_api_messages()
            tools = self.registry.get_definitions()

            # 2. 调用 LLM
            response = await self.client.chat(messages, tools)

            # 3. 解析响应
            if response.is_end_turn():
                self.state.push_assistant_message(response.text)
                return response.text

            if response.has_tool_calls():
                self.state.push_assistant_message("", response.tool_calls)
                # 4. 执行工具
                for tc in response.tool_calls:
                    result = await self.registry.call(tc.name, tc.arguments)
                    self.state.push_tool_result(tc.id, result)
                continue  # 下一轮推理

        raise MaxTurnsExceeded(max_turns)
```

#### 1.6 工具注册表（`agent/tools/registry.py`）

```python
class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolEntry] = {}

    def tool(self, name: str, description: str):
        """装饰器：注册工具"""
        def decorator(input_model: type[BaseModel]):
            def wrapper(fn):
                schema = input_model.model_json_schema()
                self._tools[name] = ToolEntry(
                    name=name, description=description,
                    parameters=schema, fn=fn, input_model=input_model
                )
                return fn
            return wrapper
        return decorator

    def get_definitions(self) -> list[dict]:
        """返回 OpenAI tool format"""
        return [t.to_openai_tool() for t in self._tools.values()]

    async def call(self, name: str, arguments: dict) -> str:
        entry = self._tools[name]
        parsed = entry.input_model.model_validate(arguments)
        result = await entry.fn(parsed)
        return result if isinstance(result, str) else result.model_dump_json()
```

#### 1.7 命令行入口（`cli.py`）

```python
async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt", nargs="?", default=None)
    parser.add_argument("--cwd", default=".")
    parser.add_argument("--model", default="claude-sonnet-4-5-20250501")
    args = parser.parse_args()

    client = LLMClient(model=args.model)
    state = ChatState()
    registry = ToolRegistry()
    # 注册内置工具
    registry.tool("read_file", "Read file contents")(ReadFileInput)(read_file)
    registry.tool("grep", "Search codebase")(GrepInput)(grep)
    registry.tool("bash", "Run shell command")(BashInput)(bash)

    agent = Agent(state, client, registry)

    if args.prompt:
        result = await agent.run(args.prompt)
        print(result)
    else:
        # REPL 模式
        while True:
            user_input = input("> ")
            result = await agent.run(user_input)
            print(result)
```

#### Phase 1 验证标准

**P0（无此不可用）**：
- [ ] 能完成单轮 "Read this file" 请求（模型调用 read_file 工具）
- [ ] 能完成多轮 "Find and fix this bug" 请求（模型调用 grep + read_file + bash）

**P1（显著体验提升）**：
- [ ] Token 过期时自动刷新
- [ ] Ctrl+C 能优雅取消当前 turn

#### Phase 1+ 增强（多 Provider 架构，已实现）

- httpx 替代 openai SDK，Backend Protocol + 三后端（ChatCompletions/Messages/Responses）
- 五级认证解析: --api-key > config.yaml (原生) > env > settings.json (cc-switch) > OAuth
- _infer_backend_from_url: 从实际 base_url 推断后端（非 argparse 默认值）
- cc-switch 联动: 读取 ~/.claude/settings.json env 块

#### Phase 1++ 增强（配置工具 + TUI，已实现）

- config/settings_writer.py: 参数→settings.json 写入器（cc-switch 等价），save/show/clear + 原子写入 + 合并保留非 env 键
- tui/app.py: Textual TUI 交互界面，EvilCodeApp — 头部栏 + 流式对话 + 工具标记 + 错误显示
- cli config 子命令: `code-agent config set/show/clear`（在 argparse 之前分发）
- --tui 标志: 路由到 EvilCodeApp
- 用户工作流: `config set` → `config show` → `--tui` → 对话

---

### Phase 2：会话管理 + 持久化（5-7 天）

**目标**：支持会话恢复、对话压缩、跨会话记忆。

#### 2.1 会话存储

```
~/.grok-py/sessions/{cwd_encoded}/{session_id}/
├── chat_history.jsonl    ← ConversationItem 每行一个 JSON
├── updates.jsonl         ← SessionUpdate 流（用于 Delta Replay）
└── summary.json          ← 会话元数据
```

**实现要点**：
- `aiofiles` 异步文件 I/O
- 追加写 + `os.fsync` 保证 crash-safe
- `UpdatesIterator` 支持偏移量定位（和 Grok Build 一样）
- `summary.json` 用 `fcntl.flock`（POSIX）或 `msvcrt.locking`（Windows）防并发写

#### 2.2 对话压缩（Compaction）

```
触发条件：估算 token 数 > context_window * 85%
压缩流程：
  1. 获取对话历史快照
  2. 调用专用小模型生成摘要（保留：用户请求 + 关键文件路径 + 错误信息 + 待办）
  3. 用摘要 + 最近 N 条消息替换原始历史
  4. 注入 <system-reminder>（当前 Todo、后台任务、编辑文件列表）
```

#### 2.3 跨会话记忆

```
~/.grok-py/memory/{project_hash}/
├── MEMORY.md         ← 人类可编辑的项目记忆
└── index.db          ← SQLite + FTS5 索引
```

**实现要点**：
- `aiosqlite` 异步 SQLite
- FTS5 全文搜索（SQL 完全复用 Grok Build 的 schema）
- 按 `##` 标题分块，blake3 去重
- 每次搜索时注入相关记忆到 `<memory-context>` 区块

#### 2.4 认证扩展

此时引入**多账户管理**（从 cc-switch 移植 vault.py）：
- `agent/auth/vault.py`：保险库（可选，单账户用户不需要）
- 支持 `grok-py auth add <name>` 添加新账户
- 支持 `grok-py auth use <name>` 切换账户
- 支持 `grok-py auth list` 列出所有账户

#### Phase 2 验证标准

**P0（无此不可用）**：
- [ ] 能 `grok-py --resume <id>` 恢复上次会话
- [ ] 长对话自动触发压缩

**P1（显著体验提升）**：
- [ ] 跨会话记忆：上一轮告诉 Agent 的信息，新会话能通过 Memory 搜索到

**P2（锦上添花）**：
- [ ] 多账户切换

---

### Phase 3：TUI + 开发体验（8-10 天）

**目标**：Textual 全屏 TUI，流式渲染，插件系统。

#### 3.1 TUI 架构（Textual）

```
Screen 层次：
┌──────────────────────────────────────────┐
│ App (Textual App)                        │
│ ┌──────────────────────────────────────┐ │
│ │ Header (模型名、session ID)          │ │
│ ├──────────────────────────────────────┤ │
│ │ Scrollback (对话历史，流式追加)       │ │
│ │  ├─ UserMessage (用户输入)           │ │
│ │  ├─ AssistantMessage (Markdown 渲染) │ │
│ │  ├─ ToolCall (Diff 展示)            │ │
│ │  └─ Thinking (可折叠思考块)          │ │
│ ├──────────────────────────────────────┤ │
│ │ Prompt (输入区域)                    │ │
│ └──────────────────────────────────────┘ │
└──────────────────────────────────────────┘
```

**关键组件**：
- `Scrollback` widget：`rich.Markdown` + `pygments` 语法高亮
- `Prompt` widget：`@` 文件引用（fuzzy 选择器，用 `iterfzf` 或自实现）
- `Diff` widget：`difflib` 或 `similar`（Python binding）行级 diff
- 流式渲染：Agent 的 `yield text_delta` → TUI 逐字符追加

#### 3.2 异步模型

```
Textual App (asyncio)
  ├─ Agent Task (asyncio.Task)
  │   └─ Agent.run() → yield text_delta → TUI 事件队列
  ├─ TUI 事件循环 (Textual 内置)
  └─ Worker 管理
```

Textual 本身基于 asyncio，和 Agent 用同一个事件循环。Agent 通过 `asyncio.Queue` 或 callback 将流式内容推给 TUI。

#### 3.3 Skills 系统

```python
# SKILL.md 格式（兼容 Grok Build / Claude Code）

# Skill: commit
# Description: Create well-formatted git commits
# When to use: user asks to commit changes

## Instructions
1. Run `git diff --staged` to see changes
2. Write a concise commit message
3. Run `git commit -m "message"`
```

- 发现：扫描 `.grok-py/skills/`、`~/.grok-py/skills/`
- 解析：YAML frontmatter + Markdown body
- 注入：作为 `<skill>` block 注入 System Prompt 或 `<system-reminder>`

#### Phase 3 验证标准

**P0（无此不可用）**：
- [ ] TUI 流式渲染正常工作
- [ ] Plan Mode 状态机完整（/plan → ACTIVE → 写工具被拦截 → exit）

**P1（显著体验提升）**：
- [ ] `@` 文件引用 fuzzy 选择
- [ ] Skills 能被正确发现和调用

**P2（锦上添花）**：
- [ ] Diff 渲染（工具调用前后的代码变化可视化）
- [ ] ThinkingBlock 可折叠

---

### Phase 4：完整生态（10-14 天）

**目标**：权限控制、沙箱、MCP、插件、Headless 模式。

#### 4.1 权限系统

```python
class PermissionManager:
    """简化版 7 层管道 → 3 层管道（MVP 够用）"""

    async def decide(self, tool_call) -> Decision:
        # 1. YOLO 模式？→ Allow
        if self.yolo_mode:
            return Decision.ALLOW

        # 2. 安全命令？（ls, cat, git status...）
        if self.is_safe_command(tool_call):
            return Decision.ALLOW

        # 3. 用户确认（TUI 弹窗 / CLI prompt）
        return await self.ask_user(tool_call)
```

#### 4.2 沙箱（可选）

```python
# 简单方案：subprocess 的 cwd 限制 + 禁止命令白名单
FORBIDDEN_COMMANDS = ["rm -rf /", "sudo", "chmod 777 /", "mkfs", "dd if="]

# 高级方案：Docker 容器（和 Grok Build 的 Landlock 不同但等效）
```

#### 4.3 MCP 集成

```python
class McpClient:
    """MCP 协议客户端（参考 Grok Build 的 rmcp 封装）"""
    async def connect(self, config: McpServerConfig): ...
    async def list_tools(self) -> list[ToolDefinition]: ...
    async def call_tool(self, name: str, args: dict) -> ToolOutput: ...
```

可以用 `mcp` Python SDK（如果社区已有），或者自己实现 JSON-RPC over stdio/HTTP。

#### 4.4 Headless 模式

```bash
# 脚本/CI 使用
grok-py -p "Review this PR for bugs" --output-format json
grok-py -p "Fix all type errors" --yolo --cwd ~/projects/my-app
```

#### 4.5 Plugin 系统

```python
# 简单的 importlib 插件发现
# ~/.grok-py/plugins/my-plugin/
#   ├── plugin.toml
#   ├── tools.py       (额外工具)
#   ├── skills/        (额外 Skills)
#   └── hooks.py       (生命周期钩子)
```

#### Phase 4 验证标准

**P0（无此不可用）**：
- [ ] `--yolo` 跳过所有权限提示
- [ ] MCP server 工具能被发现和调用
- [ ] `grok-py -p "..." --output-format json` 输出可用

**P1（显著体验提升）**：
- [ ] 插件能贡献新工具

**P2（锦上添花）**：
- [ ] Docker 沙箱隔离执行（可选，未安装 Docker 时自动降级）

---

## 技术依赖时间线

```
Phase 1:
  pip install httpx pydantic markdownify  # 不再依赖 openai SDK
  从 cc-switch 移植: oauth.py + token_store.py（纯标准库）
  架构补强新增: settings_store.py (读 settings.json), api/sse.py, api/backends/

Phase 2:
  pip install aiosqlite blake3

Phase 3:
  pip install textual rich pygments PyYAML

Phase 4:
  pip install mcp  # 如果有社区 SDK，否则手写
  # Docker SDK (可选，用于沙箱)
```

---

## 测试策略

| Phase | 测试内容 | 方式 |
|:---|:---|:---|
| 1 | 认证流程（token 读/刷新/写） | pytest + pytest-httpx（mock HTTP） |
| 1 | Agent 循环（mock LLM 响应） | pytest + asyncio |
| 1 | 工具注册/调用 | pytest + tmp_path |
| 2 | 会话持久化 | pytest + tmp_path |
| 2 | Compaction | pytest + mock LLM |
| 2 | Memory 搜索 | pytest + aiosqlite |
| 3 | TUI smoke test（启动 + 流式追加 + 取消） | Textual `async with app.run_test()` |
| 4 | 权限管道 | pytest 单元测试 |
| 4 | MCP 集成（握手 + list_tools + call_tool） | pytest + 本地 echo MCP server（stdio transport） |

---

## 不做的事情（明确边界）

- **不试图移植 Grok Build 的所有功能**：Grok Build 80+ crate、50+ 工具，MVP 只做核心
- **不复制 Rust 的设计模式**：Actor 模式在 Python asyncio 中不需要（单线程无竞态）
- **不引入 langchain/llamaindex**：它们增加了不必要的抽象层，Agent 核心逻辑不超过 200 行
- **不依赖 openai SDK**：架构补强后改用 `httpx` 直连 + `Backend Protocol` 多后端分发，支持任意 provider 的 wire format 转换（OpenAI Chat Completions / Anthropic Messages / Responses API），避免 SDK 锁定
- **不实现 os 级沙箱**：Phase 4 的沙箱用 Docker 或简单命令白名单即可
