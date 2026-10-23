# Python Agent MVP —— 文件架构设计

> 设计原则：Phase 1 就搭好骨架，Phase 2/3/4 只在对应目录内增量添加，不动已有结构。
>
> **⚠️ 重要注意：本文件中的命名（`grok_py/`、`auth/oauth.py`、`ChatState` 等）为初版草稿命名，
> 已被 `Python-Agent-MVP-详细设计.md`（Phase 1 逐文件设计）全面修正。**
> **本文件仅作架构结构参考——实际开发时请以 Phase 1 详细设计中的命名为准。**
>
> 命名对照表（本文件 → 详细设计）：
>
> | 本文件旧名 | 详细设计新名 | 理由 |
> |:---|:---|:---|
> | `grok_py/` | `code_agent/` | 描述功能而非项目代号 |
> | `auth/oauth.py` | `auth/token_refresh.py` | 描述功能：token 刷新 |
> | `auth/creds.py` | `auth/token_store.py` | 描述功能：token 存储 |
> | `LLMClient` | `ChatClient` | 描述功能：聊天客户端 |
> | `ChatState` | `Session` | 描述功能：会话状态 |
> | `core/schemas.py` | `core/messages.py` | 描述功能：消息模型 |
> | `core/agent.py` | `core/agent.py` | 不变 |
> | `tools/base.py` | `tools/definition.py` | 描述功能：工具定义 |
> | `tools/builtin/read_file.py` | `tools/builtin/file_read.py` | 描述功能 |
> | `tools/builtin/grep.py` | `tools/builtin/file_search.py` | 描述功能 |
> | `tools/builtin/bash.py` | `tools/builtin/shell_exec.py` | 描述功能 |
> | `utils/` | `helpers/` | Python 社区惯例 |

---

## Phase 1：最小可行 Agent

（以下为初版架构草案，命名已在 Phase 1 详细设计中修正。目录层级结构仍然准确。）

```
grok-py/
├── pyproject.toml                 # 项目元数据 + 依赖声明
├── grok_py/                       # 主包（不是 agent/，名字和项目统一）
│   ├── __init__.py                # 版本号 + 顶层 re-export（空）
│   │
│   ├── auth/                      # ────── 认证层（从 cc-switch 移植）──────
│   │   ├── __init__.py            #   公开 API：load_token(), refresh_if_stale()
│   │   ├── oauth.py               #   OAuth2 token 刷新（POST /v1/oauth/token）
│   │   └── creds.py               #   读写 ~/.claude/.credentials.json（atomic 写）
│   │
│   ├── api/                       # ────── API 层（LLM 通信）──────
│   │   ├── __init__.py            #   公开 API：LLMClient
│   │   ├── client.py              #   AsyncOpenAI 封装（chat + stream）
│   │   └── types.py               #   与 LLM 交互的 Pydantic 模型
│   │                               #   （ToolCall, ToolDefinition, ChatResponse...）
│   │
│   ├── core/                      # ────── 核心层（Agent 大脑）──────
│   │   ├── __init__.py            #   公开 API：Agent
│   │   ├── agent.py               #   Agent 主循环（思考→行动→观察）
│   │   ├── state.py               #   ChatState：对话历史 + token 计数
│   │   ├── schemas.py             #   ConversationItem, Message 等 Pydantic 模型
│   │   └── errors.py              #   自定义异常（MaxTurnsExceeded, AuthError...）
│   │
│   ├── tools/                     # ────── 工具层 ──────
│   │   ├── __init__.py            #   公开 API：ToolRegistry, tool 装饰器
│   │   ├── registry.py            #   ToolRegistry：注册/查找/调度
│   │   ├── base.py                #   Tool Protocol + ToolEntry dataclass
│   │   ├── result.py              #   ToolResult：统一的工具返回类型
│   │   └── builtin/               #   内置工具（每个工具一个文件）
│   │       ├── __init__.py
│   │       ├── read_file.py       #   read_file：读文件（含截断逻辑）
│   │       ├── grep.py            #   grep：正则搜索（用 ripgrep 或 Python re）
│   │       └── bash.py            #   bash：执行 shell 命令
│   │
│   ├── cli/                       # ────── 命令行层 ──────
│   │   ├── __init__.py
│   │   └── main.py                #   argparse 入口 + REPL 模式
│   │
│   └── utils/                     # ────── 工具函数 ──────
│       ├── __init__.py
│       ├── token_counter.py       #   bytes/4 token 估算
│       └── truncate.py            #   输出截断（truncate_line, truncate_middle...）
│
└── tests/                         # ────── 测试 ──────
    ├── conftest.py                #   pytest fixtures（mock LLM server, tmp dirs）
    ├── test_auth/
    │   └── test_oauth.py
    ├── test_api/
    │   └── test_client.py
    ├── test_core/
    │   ├── test_agent.py
    │   └── test_state.py
    └── test_tools/
        ├── test_registry.py
        └── test_builtin.py
```

### 各模块职责边界（Phase 1）

```
┌──────────────────────────────────────────────────────────┐
│ cli/main.py       ← 用户交互入口，组装依赖，启动 Agent     │
├──────────────────────────────────────────────────────────┤
│ core/agent.py     ← 唯一主循环，不关心认证/工具实现细节    │
│ core/state.py     ← 对话历史 CRUD，token 计数，API 格式转换│
├───────────────┬──────────────────┬───────────────────────┤
│ auth/         │ api/client.py    │ tools/                │
│ token 读/刷新 │ LLM HTTP 通信     │ 工具注册/调度/执行     │
│ 完全不依赖    │ 不关心 token 来源 │ 不关心是谁调用的       │
│ 其他模块      │ 只接收 api_key   │ 只接收 ToolInput       │
└───────────────┴──────────────────┴───────────────────────┘
```

**关键原则**：
- `core/` 不直接 import `cli/`（cli 是调用方，core 是被调用方）
- `core/agent.py` 只依赖抽象（`LLMClient` 是 Protocol，`ToolRegistry` 是 class），不依赖具体工具
- `tools/` 不知道 `auth/` 的存在
- `auth/` 是纯函数模块，零副作用（除了 HTTP 调用和文件 I/O）

### Phase 1 各文件的 import 依赖关系

```
cli/main.py
  ├── core/agent.py          ← Agent
  │   ├── core/state.py      ← ChatState
  │   ├── core/schemas.py    ← ConversationItem
  │   └── core/errors.py     ← MaxTurnsExceeded
  ├── api/client.py          ← LLMClient
  │   └── api/types.py
  ├── tools/registry.py      ← ToolRegistry
  │   ├── tools/base.py      ← Tool Protocol
  │   └── tools/result.py
  └── auth/                  ← load_token()

工具注册（在 cli/main.py 中完成）：
  tools/builtin/read_file.py
  tools/builtin/grep.py
  tools/builtin/bash.py
```

### Phase 1 关键接口设计

**`tools/base.py`**：
```python
# 工具可以是 async 函数或实现了 Tool Protocol 的类
class Tool(Protocol):
    name: ClassVar[str]
    description: ClassVar[str]
    parameters: ClassVar[dict]  # JSON Schema
    async def run(self, input: BaseModel) -> str: ...

# 或者用装饰器（更 Pythonic）：
# @registry.register("read_file", ReadFileInput, description="...")
# async def read_file(input: ReadFileInput) -> str: ...
```

**`tools/result.py`**：
```python
class ToolResult(BaseModel):
    output: str              # 给模型看的文本
    is_error: bool = False   # 是否是错误
    truncated: bool = False  # 是否被截断
```

**`core/agent.py`**：
```python
class Agent:
    def __init__(
        self,
        state: ChatState,              # 对话历史
        client: LLMClient,             # LLM 通信（唯一外部依赖）
        registry: ToolRegistry,        # 工具注册表
        max_turns: int = 20,
    ): ...
    async def run(self, user_input: str) -> str: ...
```

---

## Phase 2：会话管理 + 持久化（增量添加）

```
grok-py/
├── grok_py/
│   ├── ...（Phase 1 所有文件保留，不动）
│   │
│   ├── storage/                   # ────── 新增：存储层 ──────
│   │   ├── __init__.py            #   公开 API：SessionStore, MemoryStore
│   │   ├── session.py             #   会话 JSONL 存储（读/写/追加/Delta Replay）
│   │   ├── models.py              #   SessionMeta（summary.json 的模型）
│   │   └── lock.py                #   跨平台文件锁（POSIX fcntl / Windows msvcrt）
│   │
│   ├── memory/                    # ────── 新增：跨会话记忆 ──────
│   │   ├── __init__.py            #   公开 API：MemoryIndex, search(), inject()
│   │   ├── index.py               #   SQLite + FTS5 索引管理
│   │   ├── schema.py              #   DDL（CREATE TABLE chunks, chunks_fts）
│   │   ├── search.py              #   混合搜索（FTS5 关键词 → 排序 → 过滤）
│   │   ├── chunker.py             #   Markdown 按 ## 标题分块
│   │   └── storage.py             #   MEMORY.md 文件读/写
│   │
│   ├── compaction/                # ────── 新增：对话压缩 ──────
│   │   ├── __init__.py
│   │   ├── policy.py              #   触发策略（token 阈值判断）
│   │   ├── summarizer.py          #   调用 LLM 生成摘要
│   │   └── assembler.py           #   组装压缩后的对话历史
│   │
│   ├── core/
│   │   ├── ...（Phase 1 文件不动）
│   │   └── reminders.py           #   新增：system-reminder 注入器
│   │
│   └── auth/
│       ├── ...（Phase 1 文件不动）
│       └── vault.py               #   新增：多账户保险库（从 cc-switch 移植）
│
└── tests/
    ├── ...（Phase 1 测试不动）
    ├── test_storage/
    │   └── test_session.py
    ├── test_memory/
    │   ├── test_index.py
    │   └── test_search.py
    └── test_compaction/
        └── test_summarizer.py
```

### Phase 2 新增的 import 依赖

```
core/agent.py  ← 新增依赖（Phase 2）
  ├── compaction/policy.py     ← 每轮前检查是否需要压缩
  ├── core/reminders.py        ← 每轮前注入 system-reminder
  └── storage/session.py       ← turn 结束时持久化

core/reminders.py  ← 新增
  ├── compaction/assembler.py  ← 压缩后注入状态摘要
  └── memory/search.py         ← 搜索相关记忆注入 <memory-context>

cli/main.py  ← 新增子命令（Phase 2）
  ├── grok-py auth add/list/use  ← 多账户管理
  ├── grok-py --resume <id>      ← 恢复会话
  └── grok-py --continue         ← 继续最近会话
```

---

## Phase 3：TUI + 开发体验（增量添加）

```
grok-py/
├── grok_py/
│   ├── ...（Phase 1/2 文件保留，不动）
│   │
│   ├── tui/                       # ────── 新增：TUI 层 ──────
│   │   ├── __init__.py
│   │   ├── app.py                 #   Textual App 入口
│   │   ├── screens/
│   │   │   ├── __init__.py
│   │   │   └── chat.py            #   主聊天界面（Scrollback + Prompt）
│   │   ├── widgets/
│   │   │   ├── __init__.py
│   │   │   ├── scrollback.py      #   对话历史滚动区
│   │   │   ├── prompt.py          #   输入区域（含 @ 文件引用）
│   │   │   ├── diff_view.py       #   Diff 渲染 widget
│   │   │   ├── thinking.py        #   可折叠思考块
│   │   │   ├── todo_panel.py      #   Todo 侧边栏
│   │   │   └── status_bar.py      #   状态栏（模型名、token 数）
│   │   ├── renderer.py            #   Markdown→Rich Text 渲染
│   │   ├── syntax.py              #   语法高亮（pygments 封装）
│   │   └── theme.py               #   主题管理
│   │
│   ├── skills/                    # ────── 新增：Skills 系统 ──────
│   │   ├── __init__.py
│   │   ├── discovery.py           #   扫描 SKILL.md 文件
│   │   ├── parser.py              #   解析 YAML frontmatter
│   │   ├── manager.py             #   Skill 生命周期管理
│   │   └── types.py               #   SkillInfo 数据模型
│   │
│   ├── cli/
│   │   ├── ...（Phase 1/2 文件不动）
│   │   └── headless.py            #   新增：Headless 入口（grok-py -p "..."）
│   │
│   └── core/
│       ├── ...（Phase 1/2 文件不动）
│       └── plan_mode.py           #   新增：Plan Mode 状态机
│
└── tests/
    ├── ...（Phase 1/2 测试不动）
    ├── test_tui/                   #   Textual 的 run_test() 测试
    └── test_skills/
        └── test_discovery.py
```

### TUI 与 Agent 的数据流

```
User keystrokes
  ↓
tui/widgets/prompt.py          ← Textual Input widget
  ↓ （解析 @引用、/命令）
tui/screens/chat.py            ← 组装完整 prompt
  ↓ （通过 asyncio.Queue 发送）
core/agent.py                  ← Agent.run()（作为 asyncio.Task 运行）
  ↓ （yield 流式事件）
tui/screens/chat.py            ← 接收事件，更新 widget
  ├─ TextDelta → scrollback 追加文本
  ├─ ToolCallStart → 显示工具调用卡片
  ├─ ToolCallEnd → 渲染 Diff
  └─ TurnEnd → 更新状态栏
```

---

## Phase 4：完整生态（增量添加）

```
grok-py/
├── grok_py/
│   ├── ...（Phase 1/2/3 文件保留，不动）
│   │
│   ├── permission/                # ────── 新增：权限系统 ──────
│   │   ├── __init__.py
│   │   ├── manager.py             #   3 层决策管道（YOLO → SafeCmd → Ask）
│   │   ├── safe_commands.py       #   安全命令白名单
│   │   └── rules.py               #   --allow/--deny glob 规则
│   │
│   ├── sandbox/                   # ────── 新增：沙箱（可选）──────
│   │   ├── __init__.py
│   │   └── docker.py              #   Docker 容器执行
│   │
│   ├── mcp/                       # ────── 新增：MCP 集成 ──────
│   │   ├── __init__.py
│   │   ├── client.py              #   MCP JSON-RPC 客户端
│   │   ├── transport.py           #   stdio / HTTP 传输
│   │   └── registry.py            #   MCP 工具 → ToolRegistry 注册
│   │
│   ├── plugins/                   # ────── 新增：插件系统 ──────
│   │   ├── __init__.py
│   │   ├── loader.py              #   importlib 插件发现和加载
│   │   └── types.py               #   PluginManifest 数据模型
│   │
│   ├── hooks/                     # ────── 新增：Hook 系统 ──────
│   │   ├── __init__.py
│   │   └── dispatcher.py          #   生命周期事件分发（PreToolUse 等）
│   │
│   ├── telemetry/                 # ────── 新增：遥测（可选）──────
│   │   ├── __init__.py
│   │   ├── logger.py              #   结构化日志（JSON lines）
│   │   └── usage.py               #   Token 使用统计
│   │
│   └── auth/
│       ├── ...（Phase 1/2 文件不动）
│       └── crypto.py              #   新增：DPAPI 加密（从 cc-switch 移植）
│
└── tests/
    ├── ...（Phase 1/2/3 测试不动）
    ├── test_permission/
    ├── test_mcp/
    └── test_plugins/
```

---

## 总览：Phase 1 → Phase 4 的目录演化

```
grok_py/                         # Phase 1   Phase 2      Phase 3      Phase 4
├── __init__.py                  #    ✓
├── auth/                        #    ✓
│   ├── oauth.py                 #    ✓
│   ├── creds.py                 #    ✓
│   ├── vault.py                 #              ✓
│   └── crypto.py                #                                     ✓
├── api/                         #    ✓
│   ├── client.py                #    ✓
│   └── types.py                 #    ✓
├── core/                        #    ✓
│   ├── agent.py                 #    ✓
│   ├── state.py                 #    ✓
│   ├── schemas.py               #    ✓
│   ├── errors.py                #    ✓
│   ├── reminders.py             #              ✓
│   └── plan_mode.py             #                         ✓
├── tools/                       #    ✓
│   ├── registry.py              #    ✓
│   ├── base.py                  #    ✓
│   ├── result.py                #    ✓
│   └── builtin/                 #    ✓
│       ├── read_file.py         #    ✓
│       ├── grep.py              #    ✓
│       └── bash.py              #    ✓
├── cli/                         #    ✓
│   ├── main.py                  #    ✓
│   └── headless.py              #                         ✓
├── utils/                       #    ✓
│   ├── token_counter.py         #    ✓
│   └── truncate.py              #    ✓
├── storage/                     #              ✓
│   ├── session.py               #              ✓
│   ├── models.py                #              ✓
│   └── lock.py                  #              ✓
├── memory/                      #              ✓
│   ├── index.py                 #              ✓
│   ├── schema.py                #              ✓
│   ├── search.py                #              ✓
│   ├── chunker.py               #              ✓
│   └── storage.py               #              ✓
├── compaction/                  #              ✓
│   ├── policy.py                #              ✓
│   ├── summarizer.py            #              ✓
│   └── assembler.py             #              ✓
├── tui/                         #                         ✓
│   ├── app.py                   #                         ✓
│   ├── screens/chat.py          #                         ✓
│   ├── widgets/*.py             #                         ✓
│   ├── renderer.py              #                         ✓
│   ├── syntax.py                #                         ✓
│   └── theme.py                 #                         ✓
├── skills/                      #                         ✓
│   ├── discovery.py             #                         ✓
│   ├── parser.py                #                         ✓
│   ├── manager.py               #                         ✓
│   └── types.py                 #                         ✓
├── permission/                  #                                     ✓
├── sandbox/                     #                                     ✓
├── mcp/                         #                                     ✓
├── plugins/                     #                                     ✓
├── hooks/                       #                                     ✓
└── telemetry/                   #                                     ✓
```

**Phase 1 只有 6 个顶层目录**，Phase 4 增加到 **15 个**。但每次增量只添加新目录，不动已有目录的结构。Phase 1 写的 `core/agent.py` 到 Phase 4 依然有效——它只是多了一些可选依赖（compaction、reminders、plan_mode）。

---

## 关键架构约束

1. **`core/` 永远是核心，不依赖 `cli/`、`tui/`、`storage/`**
   - `core/agent.py` 只依赖 `api/client.py` 和 `tools/registry.py`——它们通过构造函数注入
   - Compaction、Memory 等功能通过**可选依赖注入**（`agent.compaction = Some(compactor)`），不是硬 import

2. **`tools/` 是独立王国**
   - 每个工具是一个文件，只依赖 `tools/base.py`（Tool Protocol）和 `tools/result.py`
   - 工具不知道 LLM、不知道 Agent、不知道 TUI
   - 新增工具 = 新增一个文件 + 在 cli/main.py 中注册一行

3. **`auth/` 是纯函数模块**
   - `oauth.py` 和 `creds.py` 可以脱离整个项目独立使用（这就是 cc-switch 的做法）
   - 不 import 项目内其他模块

4. **`storage/`、`memory/`、`compaction/` 是平等的服务层**
   - 每个都有自己清晰的公开 API（`__init__.py` 中暴露）
   - 它们之间可以互相依赖（如 compaction 后写 storage），但都不依赖 core/

5. **`cli/` 和 `tui/` 是平行入口**
   - `cli/main.py` 用于 headless/REPL
   - `tui/app.py` 用于全屏 TUI
   - 两者共享同一个 `core/agent.py`，只是输入输出的方式不同
