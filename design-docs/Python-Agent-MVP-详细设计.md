# Python AI Coding Agent -- Phase 1 逐文件详细设计

> 代码逻辑参考 Grok Build（Rust 版 AI 编程代理）的架构思想。
> 命名和注释遵循 Python 社区规范，**按功能描述命名**，不照搬原项目代号。
> 引用格式：`※ 逻辑参考 Rust 版的 xxx 模块`

---

## 1. 项目命名对照

| 原 Rust 项目名 | 本 Python 文件名 | 命名理由 |
|:---|:---|:---|
| `xai-grok-shell` | `code_agent/core/` | 描述功能：Agent 推理循环 |
| `xai-grok-tools` | `code_agent/tools/` | 描述功能：工具注册和执行 |
| `xai-chat-state` | `code_agent/core/session.py` | 描述功能：对话状态管理 |
| `xai-grok-sampler` | `code_agent/api/client.py` | 描述功能：LLM API 通信 |
| `xai-grok-auth` | `code_agent/auth/` | 描述功能：认证 |
| `AuthManager::try_recover_unauthorized` | `auth/token_refresh.py` | 描述功能：token 刷新 |
| `ChatStateActor::ensure_conversation_integrity` | `session.py:_repair_dangling_calls` | 描述功能：修复未配对的工具调用 |
| `ToolRegistryBuilder + FinalizedToolset` | `tools/registry.py:ToolRegistry` | 描述功能：工具注册表 |
| `FinalizedTool` | `tools/definition.py:ToolDefinition` | 描述功能：工具定义 |
| `ToolOutput / ToolRunResult` | `tools/result.py:ToolResult` | 描述功能：工具执行结果 |
| `xai-token-estimation` | `helpers/token_counter.py` | 描述功能：token 估算 |
| `truncate.rs` | `helpers/text_truncate.py` | 描述功能：文本截断 |
| `SessionActor::process_conversation_turn` | `core/agent.py:Agent.run` | 描述功能：Agent 主循环 |
| `dispatch_tool` | `core/agent.py:_execute_one_tool` | 描述功能：执行单个工具 |
| `ApiBackend` enum | `api/schemas.py: ApiBackend` | 枚举名直接复用——跨语言通用概念 |
| `AuthScheme` enum | `api/schemas.py: AuthScheme` | 同上 |
| `SamplerConfig` | `api/schemas.py: ProviderConfig` | 描述功能：provider 配置（非 sampler） |
| `SamplingClient::post()` | `api/client.py: ChatClient.chat_stream()` | 描述功能：LLM HTTP 通信 |
| `stream_chat_completions()` | `api/backends/chat_completions.py: parse_stream()` | 描述功能：解析流 |
| `stream_messages()` | `api/backends/messages.py: parse_stream()` | 同上 |
| `stream_responses()` | `api/backends/responses.py: parse_stream()` | 同上 |
| `BearerResolver` / `HeaderInjector` | `cli/main.py: _resolve_provider_config()` | 简化为函数，不需 trait/Protocol |

---

## 2. 包结构总览

```
code_agent/
├── __init__.py                 # 版本号
├── auth/                       # 认证层（纯函数模块）
│   ├── __init__.py             #   公开: load_token, save_token, refresh_token, is_token_expired, load_settings_credentials, load_config, save_config, generate_config
│   ├── token_refresh.py        #   OAuth2 token 刷新（纯函数，不读文件不写文件）
│   ├── token_store.py          #   token 文件读写（原子写入: temp + fsync + os.replace）
│   ├── settings_store.py       #   ~/.claude/settings.json env 块读取（cc-switch 联动）
│   └── config_store.py         #   evil-code 原生配置读/写/生成（~/.evil-code/config.yaml，YAML/JSON 双格式）
├── api/                        # API 通信层（多 provider，httpx 直连）
│   ├── __init__.py             #   公开: StreamEvent, ToolCall, ApiBackend, AuthScheme, ProviderConfig
│   │                           #   （注: ChatClient 因循环导入不在此 re-export，直接从 api.client 导入）
│   ├── client.py               #   LLM HTTP 客户端（httpx 直连 + 后端分发，无 SDK 依赖）
│   ├── schemas.py              #   请求/响应数据模型 + 多 provider 配置类型（ApiBackend, AuthScheme, ProviderConfig）
│   ├── sse.py                  #   SSE 流解析工具（共用，逐行读 data: 前缀 + JSON 解析）
│   └── backends/               #   三后端实现（各实现 build_request + parse_stream）
│       ├── __init__.py         #   公开: get_backend(ApiBackend) -> Backend
│       ├── base.py             #   Backend Protocol + BackendRequest dataclass
│       ├── chat_completions.py #   OpenAI Chat Completions 后端（/chat/completions）
│       ├── messages.py         #   Anthropic Messages 后端（/messages，含消息格式转换）
│       └── responses.py        #   OpenAI Responses 后端（/responses）
├── core/                       # 核心编排层（Agent 大脑）
│   ├── __init__.py             #   公开: Session, 消息类型, 异常类型（注: Agent 因循环导入不在此 re-export，直接从 core.agent 导入）
│   ├── agent.py                #   Agent 主循环: think -> act -> observe（AsyncIterator 输出）
│   ├── session.py              #   对话状态: 历史管理 + token估算 + dangling repair
│   ├── messages.py             #   消息数据模型: System/User/Assistant/ToolResult（tagged union）
│   └── exceptions.py           #   自定义异常: AgentError, MaxTurnsReached, AuthError, ToolNotFound
├── tools/                      # 工具层（独立模块，每个工具一个文件）
│   ├── __init__.py             #   公开: ToolRegistry, ToolDefinition, ToolResult
│   ├── registry.py             #   工具注册表: 装饰器注册 + finalize锁 + execute调度
│   ├── definition.py           #   工具定义数据模型（name + description + input_model + handler）
│   ├── result.py               #   统一工具返回类型（output + is_error + truncated）
│   └── builtin/
│       ├── __init__.py
│       ├── file_read.py        #   读文件（二进制检测 + 行号标注 + 截断footer）
│       ├── file_search.py      #   代码搜索（优先ripgrep，fallback Python re）
│       ├── shell_exec.py       #   Shell执行（shlex解析 + subprocess + 超时kill）
│       ├── file_edit.py        #   精确字符串替换（原子写入 + difflib + 1MB限制）
│       ├── file_write.py       #   创建/覆盖文件（目录创建 + 权限保留 + 5MB限制）
│       └── web_fetch.py        #   URL获取（SSRF防护 + HTML转Markdown + 5MB截断）
├── config/                     # 配置文件管理层（生成 + 读写）
│   ├── __init__.py             #   公开: generate_config, load_config, save_config, save_settings_config, show_settings_config, clear_settings_config
│   └── settings_writer.py      #   写入 ~/.claude/settings.json env 块（cc-switch 兼容，含 show/clear）
├── cli/                        # 命令行入口
│   ├── __init__.py
│   └── main.py                 #   argparse入口 + 五级认证解析 + config set/show/clear 子命令 + headless/REPL双模式
└── helpers/                    # 工具函数（纯函数，零项目内依赖）
    ├── __init__.py
    ├── token_counter.py        #   bytes/4 token估算 + 阈值判断
    ├── text_truncate.py        #   多种截断策略（硬截断、头尾保留、行边界截断）
    └── file_io.py              #   原子文件写入（mkstemp + fsync + os.replace，file_edit/file_write 共享）
```

---

## 3. 逐个文件详细设计

以下每个文件的描述格式：
- **职责**：这个文件做什么
- **公开 API**：对外暴露的函数/类及其签名
- **设计决策**：为什么这样设计，参考了 Rust 版的什么逻辑
- **封装原则**：依赖什么，不依赖什么


---

## 3.1 `auth/token_refresh.py` — OAuth2 Token 刷新

**职责**：OAuth2 token 的过期检测和刷新。纯函数模块，不读文件、不写文件、不持有全局状态。

**公开 API**：

| 函数 | 签名 | 职责 |
|:---|:---|:---|
| `is_token_expired` | `(token: dict, buffer_seconds: int = 300) -> bool` | 检查 token 是否过期（`expiresAt` 为毫秒时间戳） |
| `refresh_token` | `async (token: dict, timeout: float = 30.0, client: httpx.AsyncClient \| None = None) -> dict` | 用 refresh_token 换新 token，**返回新 dict（不修改入参）**。异步方法，实现细节见本节"内部实现要点" |

**设计决策**：
- ※ 逻辑参考 Rust 版 `AuthManager::try_recover_unauthorized()` 的"检测过期 -> 刷新 -> 重试"模式
- ※ 常量值 (`_TOKEN_URL`, `_CLIENT_ID`) 参考 cc-switch 从 Claude Code 二进制中提取的 OAuth 参数
- **纯函数**：`refresh_token()` 不修改传入的 dict，返回新 dict。调用方决定是否持久化
- **错误分类**：`ValueError`（无 refreshToken）、`RuntimeError`（HTTP/网络错误）。不吞错误
- **依赖**：`httpx`（openai SDK 已依赖，无需额外安装）+ `json` + `time`。`refresh_token` 为 `async def`，实现细节见本节"内部实现要点"。**不依赖项目内任何模块**

**内部实现要点**：
- `refresh_token` 为 **async def**，使用 `httpx.AsyncClient` 发送 POST 请求（openai SDK 已依赖 httpx，无需额外安装）
- POST 请求体：`{"grant_type": "refresh_token", "refresh_token": ..., "client_id": ...}`
- 接受可选 `client: httpx.AsyncClient | None` 参数——若提供则复用连接池，若为 None 则创建临时 client
- 集成说明：openai SDK（AsyncOpenAI）内部使用 httpx 但不暴露其 client 实例。Phase 1 推荐 ChatClient 额外维护一个独立 httpx.AsyncClient 传给 refresh_token()——两个连接池开销可忽略（token 刷新频率极低）
- 响应解析：`access_token` 必选，`refresh_token` 可选（服务端可能轮换），`expires_in` 用于计算新 `expiresAt`
- 错误分类：`ValueError`（无 refreshToken）、`RuntimeError`（HTTP/网络错误）
- `is_token_expired` 保持**同步**（纯 CPU 时间戳比较，不阻塞事件循环）
- 超时默认 30 秒，调用方可覆盖

---

## 3.2 `auth/token_store.py` — Token 文件读写

**职责**：从 Claude Code 兼容的凭证文件 (`~/.claude/.credentials.json`) 读写 OAuth token。

**公开 API**：

| 函数 | 签名 | 职责 |
|:---|:---|:---|
| `load_token` | `() -> dict | None` | 读取 token，文件不存在或无效返回 None |
| `save_token` | `(token: dict) -> None` | **原子写入** token，保留文件中其他键 |

**设计决策**：
- ※ 逻辑参考 Rust 版 JSONL 存储的原子写入模式 (`storage/jsonl/mod.rs:249`)
- **原子写入**：temp file -> `json.dump` -> `f.flush()` -> `os.fsync()` -> `os.replace()` -> `chmod 0o600`
- **保留其他键**：只替换 `claudeAiOauth` 键，不覆盖 `mcpOAuth` 等其他顶层键
- **权限**：文件设为 `0o600`，防止其他用户读取
- **依赖**：仅标准库 `json` + `os` + `pathlib`。**不依赖项目内任何模块**

---

## 3.3 `api/schemas.py` — API 数据模型

**职责**：定义 LLM API 通信中使用的 Pydantic 数据模型。

**公开类**：

| 类 | 字段 | 用途 |
|:---|:---|:---|
| `ToolDefinitionSchema` | `type`, `function: FunctionSchema` | OpenAI function-calling 格式的工具定义（对外 API schema） |
| `FunctionSchema` | `name`, `description`, `parameters` | 工具的 function 描述 |
| `ToolCall` | `id`, `type`, `function: FunctionCall` | 模型返回的工具调用请求 |
| `FunctionCall` | `name`, `arguments` | 工具调用的具体参数（arguments 为原始 JSON 字符串） |
| `StreamEvent` | `kind`, `text`, `tool_call` | 流式响应的单个事件（kind 区分类型） |
| `ApiBackend` | `CHAT_COMPLETIONS` / `MESSAGES` / `RESPONSES` | LLM API 后端协议枚举（决定 wire format + SSE 解析方式） |
| `AuthScheme` | `BEARER` / `X_API_KEY` | HTTP 认证头方案枚举（Bearer → Authorization 头，XApiKey → x-api-key 头） |
| `ProviderConfig` | `api_backend`, `base_url`, `api_key`, `auth_scheme`, `model`, `max_tokens`, `temperature` | 单个 provider 的完整配置（dataclass，由 ChatClient 持有） |

> **命名说明**：`ToolDefinitionSchema`（api/schemas.py）是面向 LLM API 的 OpenAI 格式 schema；`ToolDefinition`（tools/definition.py，见 §3.9）是内部完整工具定义（含 handler、input_model）。两者职责不同，命名区分以避免歧义。

**设计决策**：
- ※ 逻辑参考 Rust 版 `ConversationItem` tagged enum (`sampling-types/src/conversation.rs:28`)
- Python 中用 Pydantic + `Literal` 类型实现 tagged union 效果
- `ToolCall.arguments` 存原始 JSON 字符串——**不在 schemas 层解析**，解析交给 ToolRegistry
- `StreamEvent.kind` 用 `Literal` 区分：`text_delta` | `tool_call_delta` | `thinking_delta` | `turn_end` | `error`
- **`thinking_delta`** 为 Phase 1 预定义但 Phase 1 不生产——API client 遇到推理模型返回的 reasoning/thinking token 时，将其映射为 thinking_delta。Phase 3 TUI 的 ThinkingBlock widget 依赖此事件类型
- **不包含模型特定的字段**（如 Anthropic 的 `anthropic-beta` header），保持通用性
- **多 provider 类型**（架构补强）：`ApiBackend` 用 `Enum` 而非 `Literal`（需要可扩展性）；`AuthScheme` 与 Rust 版一一对应；`ProviderConfig` 用 `dataclass` 而非 Pydantic（内部配置传递，不需 JSON 序列化）
- ※ `ApiBackend` + `AuthScheme` 逻辑参考 Rust 版 `sampling-types/src/types.rs:1013` + `config.rs:20`

---

## 3.4 `api/client.py` — LLM 通信客户端（多 Provider，架构补强）

**职责**：封装 LLM HTTP 通信。上层 Agent 只知道"发消息，收回复"，不关心 HTTP、认证、流式解析。支持三种 API 后端协议（Chat Completions / Messages / Responses），根据 `ProviderConfig.api_backend` 分发到对应后端实现。

**公开类**：

```python
class ChatClient:
    """LLM 对话客户端。多 provider，httpx 直连，后端分发。

    使用方式:
        config = ProviderConfig(
            api_backend=ApiBackend.MESSAGES,
            base_url="https://api.deepseek.com/anthropic",
            api_key="sk-...",
            auth_scheme=AuthScheme.BEARER,
            model="deepseek-v4-pro",
        )
        client = ChatClient(config)
        async for event in client.chat_stream(messages, tools):
            ...
    """

    def __init__(self, config: ProviderConfig): ...
    async def chat_stream(
        self, messages: list[dict], tools: list[dict] | None = None,
        max_tokens: int | None = None, temperature: float | None = None,
    ) -> AsyncIterator[StreamEvent]: ...
    async def close(self) -> None: ...  # 清理 httpx.AsyncClient 资源
```

**设计决策**：
- ※ 逻辑参考 Rust 版 xai-grok-sampler crate 的 `SamplingClient::post()` (`client.rs:550`)——构造请求 → 发 HTTP → 解析 SSE 流
- **httpx 直连**：去掉 `openai` SDK 依赖，改用 `httpx.AsyncClient` 直接发 HTTP 请求。三个后端用同一套 HTTP client + SSE 解析，行为一致；加新后端只需写 `build_request` + `parse_stream`
- **后端分发**：`chat_stream()` 根据 `config.api_backend` 获取后端实例（`get_backend()`），调用 `backend.build_request()` 构造 HTTP 请求（url + headers + body），用 `httpx.stream()` 发流式 POST，再用 `iter_sse_events()` 解析 SSE 流，最后 `backend.parse_stream()` 解释 JSON chunk 为 `StreamEvent`
- **认证预解析**：`ProviderConfig.api_key` 在传入前已由 `cli/main.py` 的五级认证链解析好（CLI > config.yaml > env > settings.json > OAuth）。ChatClient 不读 token 文件、不刷新——认证是调用方的职责
- **认证失败语义**：HTTP 401 → `AuthenticationError`（由 Agent 捕获）。非 200 → `error` StreamEvent。网络超时/连接失败 → `error` 事件。不降级为"未认证模式"
- **per-request 覆盖**：`chat_stream()` 的 `max_tokens`/`temperature` 参数可覆盖 `ProviderConfig` 默认值——用 `dataclasses.replace()` 创建副本
- **流式输出**：`chat_stream()` 是 async generator，逐个 yield `StreamEvent`
- **依赖**：`httpx`, `code_agent.api.backends` (get_backend), `code_agent.api.sse` (iter_sse_events), `code_agent.api.schemas` (ProviderConfig, StreamEvent), `code_agent.core.exceptions` (AuthenticationError)
- **不依赖**：`openai` SDK, Session, ToolRegistry, CLI/TUI, auth/

### 3.4.1 新增文件：`api/sse.py` — SSE 流解析工具

**职责**：共用 SSE 行解析，三个后端共享。从 `httpx.Response.aiter_lines()` 读取原始文本行，解析出 JSON 事件 dict。

**公开 API**：`async def iter_sse_events(response: httpx.Response) -> AsyncIterator[dict]`

**设计决策**：
- ※ 逻辑参考 Rust 版 `SamplingClient::post()` 中的 SSE 行解析
- 解析规则：`data:` 行取 JSON payload；`data: [DONE]` 终止信号；`event:` 行标记事件类型（附加到 `_event` 字段）；空行为事件分隔符；`:` 开头为注释
- 三后端共用此解析器——后端 `parse_stream()` 只负责解释 JSON 内容

### 3.4.2 新增文件：`api/backends/` — 三后端实现

**职责**：每个后端实现 `Backend` Protocol 的两个方法：`build_request()`（构造 HTTP 请求）+ `parse_stream()`（解析 SSE 流为 StreamEvent）。

| 文件 | 后端 | 端点 | 适用场景 |
|:---|:---|:---|:---|
| `base.py` | — | — | Backend Protocol + BackendRequest dataclass |
| `chat_completions.py` | ChatCompletions | `/chat/completions` | OpenAI/xAI/Ollama/兼容端点 |
| `messages.py` | Messages | `/messages` | Anthropic 原生 + DeepSeek 兼容端点 |
| `responses.py` | Responses | `/responses` | OpenAI Responses API |
| `__init__.py` | — | — | 工厂函数 `get_backend(ApiBackend) -> Backend` |

**Messages 后端的关键转换逻辑**（最复杂）：
- system 消息从 messages 数组提取到顶层 `system` 字段
- `role=tool` 消息转换为 `role=user` 的 `tool_result` content block（连续合并）
- `tool_calls` 转换为 `tool_use` content block（`arguments` JSON 字符串 → `input` JSON 对象）
- SSE 用 `event:` 行标记类型（`content_block_start/delta/stop`、`message_delta`）
- `block index ≠ tool index`（block 含 text+thinking+tool_use），需 `block_to_tool` 映射
- 认证头：`x-api-key`（XApiKey）或 `Authorization: Bearer`（Bearer，兼容端点用）
- 必需头：`anthropic-version: 2023-06-01`

**设计决策**：
- ※ 逻辑参考 Rust 版 `stream_chat_completions()` + `stream_messages()` + `stream_responses()`
- 后端实例无状态（纯函数逻辑），可安全地用单例
- `base.py` 用 Python `Protocol`（结构化子类型），后端模块只需实现两个方法即可替换

---

## 3.5 `core/messages.py` — 消息数据模型

**职责**：定义 Agent 内部使用的消息类型。tagged union 设计，每种消息有独立的 role 字段。

**公开类**：

| 类 | role 值 | 关键字段 | 用途 |
|:---|:---|:---|:---|
| `SystemMessage` | `"system"` | `content` | 系统指令（conversation[0]） |
| `UserMessage` | `"user"` | `content` | 用户输入（含 synthetic system-reminder） |
| `AssistantMessage` | `"assistant"` | `content`, `tool_calls: list[ToolCallItem]` | 模型回复 |
| `ToolCallItem` | (嵌套在 AssistantMessage 中) | `id`, `name`, `arguments` | 单个工具调用 |
| `ToolResultMessage` | `"tool"` | `tool_call_id`, `content`, `is_error` | 工具执行结果 |

**设计决策**：
- ※ 逻辑参考 Rust 版 `ConversationItem` enum (`sampling-types/src/conversation.rs:28-58`)
- Python 中用 Pydantic `BaseModel` + `Literal` role 字段区分类型
- `arguments` 存原始 JSON 字符串——**解析推迟到 ToolRegistry.execute()**，使得消息层不依赖工具层
- `is_error` 字段：标记工具执行失败的 ToolResult，模型看到后可选择重试或放弃
- **不包含** Rust 版的 `BackendToolCall` 和 `Reasoning` variant——Phase 1 不需要

---

## 3.6 `core/session.py` — 对话状态管理

**职责**：管理单个 Agent 会话的对话历史、token 估算、API 格式转换、dangling call 自修复。

**公开类**：

```python
class Session:
    """管理单个 Agent 会话的对话状态。"""

    def __init__(self, system_prompt: str | None = None): ...

    # 写入操作（每次写前自动触发 _repair_dangling_calls）
    def add_user_message(self, content: str) -> None: ...
    def add_assistant_message(self, text: str | None, tool_calls: list | None) -> None: ...
    def add_tool_result(self, call_id: str, content: str, is_error: bool = False) -> None: ...

    # 读取操作
    def to_api_messages(self) -> list[dict]: ...   # 转换为 OpenAI API 格式
    def get_system_prompt(self) -> str | None: ...

    # 属性
    messages: list[Message]  # 只读，供 hooks 检查消息历史
    prompt_count: int       # 已处理的用户 prompt 数量
    estimated_tokens: int   # bytes/4 估算的当前 token 数
```

**设计决策**：
- ※ 逻辑参考 Rust 版 `ChatStateActor` (`chat-state/src/actor/`) + `ChatState` 结构体
- **不需要 Actor 模式**：Python asyncio 单线程中，Session 的调用方 (Agent) 在同一协程中，不存在并发 mutation
- **`_repair_dangling_calls()`**：等价于 Rust 版 `ChatStateActor::ensure_conversation_integrity()`。每次写前调用，3 个步骤：
  1. 收集所有 AssistantMessage 中的 tool_call id
  2. 收集所有 ToolResultMessage 中的 tool_call_id
  3. 缺失的注入 `is_error=True` 的占位 ToolResult，重复的去重
- **Token 估算**：使用 `helpers/token_counter.py` 的 bytes/4 启发式。模型返回实际 token 数后由 Agent 更新
- **依赖**：`code_agent.core.messages`, `code_agent.helpers.token_counter`
- **不依赖**：Agent, ChatClient, ToolRegistry, CLI/TUI

---

## 3.7 `core/agent.py` — Agent 主循环 + AgentHook 中间件

**职责**：实现 think -> act -> observe 的完整 Agent 循环。这是整个系统最核心的文件。
Phase 1 定义 AgentHook 协议接口，后续 Phase 只注册新 Hook 类，不修改 Agent 代码。

### 3.7.1 AgentHook 协议（Phase 1 定义，Phase 2-4 实现）

```python
from typing import Protocol, AsyncIterator
from enum import Enum

class HookDecision(Enum):
    ALLOW = "allow"       # 允许执行
    DENY = "deny"         # 拒绝执行
    MODIFY = "modify"     # 修改参数后执行


class AgentHook(Protocol):
    """Agent 生命周期钩子。Phase 1 定义接口，Phase 2-4 实现具体钩子。

    所有方法都有默认空实现（no-op），子类只 override 需要的方法。
    Agent 调用时遍历 hooks 列表，按注册顺序执行。
    """

    async def on_session_start(self, session: "Session") -> None:
        """新一轮用户输入开始前调用。Phase 2 在此注入 memory reminder。"""
        ...

    async def on_turn_start(
        self, session: "Session", turn: int, tools: list[dict]
    ) -> None:
        """每轮推理开始前调用。Phase 2 在此检查 compaction；Phase 3 在此推进 Plan Mode。"""
        ...

    async def on_tool_call(
        self, tool_name: str, arguments: dict
    ) -> "HookDecision":
        """每个工具调用前调用。Phase 4 在此做权限决策和 PreToolUse 分发。"""
        return HookDecision.ALLOW

    async def on_tool_result(
        self, tool_name: str, arguments: dict, result: "ToolResult"
    ) -> None:
        """每个工具调用后调用。Phase 4 在此做 PostToolUse 分发；Phase 2 在此记录编辑文件。"""
        ...

    async def on_turn_end(
        self, session: "Session", turn: int
    ) -> None:
        """每轮推理结束后调用。Phase 2 在此持久化；Phase 3 在此推进 Plan Mode 退出。"""
        ...
```

**HookDecision 与 Phase 4 HookResult 的映射**：Phase 4 的 `HookDispatcher` 使用 `HookResult.action`，
其值（`allow`/`deny`/`modify`）与 `HookDecision` 保持一致。`LifecycleHook` 在内部将
`HookDispatcher.dispatch()` 的返回直接映射为 `HookDecision`（值相同，无需转换）。

### 3.7.2 Agent 构造函数（修订版）

```python
class Agent:
    """AI 编程代理。think -> act -> observe 主循环。

    所有外部依赖通过构造函数注入（依赖反转）：
    - session: 对话状态（同层）
    - client: LLM 通信（注入，延迟初始化 + 自动刷新 token）
    - tools: 工具注册表（注入）
    - max_turns: 单次 prompt 最多推理轮数
    - hooks: AgentHook 列表（注入，可选）——这是唯一的扩展点。Phase 2-4 通过此参数扩展。
    """

    def __init__(self, session: Session, client: ChatClient,
                 tools: ToolRegistry, *,
                 max_turns: int = 20,
                 hooks: list[AgentHook] | None = None):
        self._session = session
        self._client = client
        self._tools = tools
        self._max_turns = max_turns
        self._hooks = hooks or []
```

**关键设计**：Phase 1 只加一个 `hooks` 参数（默认为 None）。以后所有 Phase 的新功能都通过
`AgentHook` 子类实现，在 `cli/main.py` 的 `_build_agent()` 中组装 hook 列表传给 Agent。
`Agent.run()` 和 `_execute_one_tool()` 在固定位置调用 hook 方法——这个调用逻辑 Phase 1 就写好，后续 Phase 不修改。

### 3.7.3 Agent.run()（修订版——Phase 1 一次写好，后续不动）

```python
async def run(self, user_input: str) -> AsyncIterator[StreamEvent]:
    self._session.add_user_message(user_input)

    # Phase 1 定义钩子调用点，后续不修改
    for hook in self._hooks:
        await hook.on_session_start(self._session)

    for turn in range(self._max_turns):
        tools = self._tools.get_definitions()

        for hook in self._hooks:
            await hook.on_turn_start(self._session, turn, tools)

        messages = self._session.to_api_messages()
        pending_calls: dict[int, ToolCallItem] = {}

        async for event in self._client.chat_stream(messages, tools):
            yield event

            # 流式 tool_call 累积（标准 SSE tool_call delta 协议——见 §3.4 StreamEvent 定义）：
            # - tool_call_delta: 按 index 分组，arguments_fragment 追加拼接，累积 id/name/arguments
            # - turn_end: 从 pending_calls 取出完整列表，join args 成 JSON 字符串
            if event.kind == "tool_call_delta":
                idx = event.tool_call.index
                if idx not in pending_calls:
                    pending_calls[idx] = ToolCallItem(
                        id=event.tool_call.id or "",
                        name=event.tool_call.name or "",
                        arguments="",
                    )
                tc = pending_calls[idx]
                if event.tool_call.id:
                    tc.id = event.tool_call.id
                if event.tool_call.name:
                    tc.name = event.tool_call.name
                if event.tool_call.arguments_fragment:
                    tc.arguments += event.tool_call.arguments_fragment

            elif event.kind == "turn_end":
                tool_calls = list(pending_calls.values()) if pending_calls else None
                self._session.add_assistant_message(tool_calls=tool_calls)

                if tool_calls:
                    for tc in tool_calls:
                        result = await self._execute_one_tool(tc)
                        self._session.add_tool_result(
                            call_id=tc.id, content=result.output,
                            is_error=result.is_error,
                        )
                    break  # 跳出 stream 循环，进入下一 turn
                else:
                    for hook in self._hooks:
                        await hook.on_turn_end(self._session, turn)
                    return  # 纯文本回复，结束

    raise MaxTurnsReached(self._max_turns)
```

### 3.7.4 Agent._execute_one_tool()（修订版——Phase 1 一次写好，后续不动）

```python
async def _execute_one_tool(self, tc: ToolCallItem) -> ToolResult:
    try:
        args = json.loads(tc.arguments) if tc.arguments else {}
    except json.JSONDecodeError:
        return ToolResult.error(f"参数不是有效 JSON: {tc.arguments[:200]}")

    # Phase 1 定义钩子调用点，后续不修改
    # 多个 hook 按注册顺序链式执行——每个 MODIFY hook 直接修改 args dict，
    # 下一个 hook 看到的是前一个 hook 修改后的 args。顺序敏感：注册顺序决定修改优先级。
    for hook in self._hooks:
        decision = await hook.on_tool_call(tc.name, args)
        if decision == HookDecision.DENY:
            return ToolResult.error(f"权限拒绝: {tc.name}")
        if decision == HookDecision.MODIFY:
            # hook 通过 args 引用修改了参数。在此之后的 hook 将看到修改后的 args。
            # 如需"各自基于原始 args"的语义，hook 实现者应在修改前自行保存 args.copy()。
            pass

    try:
        result = await self._tools.execute(tc.name, args)
    except ToolNotFoundError:
        return ToolResult.error(f"未知工具 '{tc.name}'")
    except Exception as exc:
        return ToolResult.error(f"执行失败: {exc}")

    for hook in self._hooks:
        await hook.on_tool_result(tc.name, args, result)

    return result
```

### 3.7.5 各 Phase 的 Hook 实现一览

| Phase | Hook 类 | 实现的方法 | 职责 |
|:---|:---|:---|:---|
| 2 | `MemoryHook` | `on_session_start` | 注入跨会话记忆 |
| 2 | `CompactionHook` | `on_turn_start` | 检查是否需要压缩 |
| 2 | `PersistenceHook` | `on_turn_end` | 持久化会话 |
| 2 | `EditTrackerHook` | `on_tool_result` | 记录 Agent 编辑的文件（仅追踪 search_replace/write_file，不追踪 shell_exec） |
| 3 | `PlanModeHook` | `on_turn_start`, `on_tool_call`, `on_turn_end` | Plan Mode 状态机 |
| 4 | `PermissionHook` | `on_tool_call` | 权限决策管线 |
| 4 | `LifecycleHook` | `on_tool_call`, `on_tool_result`, `on_session_start`, `on_turn_end` | 用户定义的 Hook 脚本 |

**Hook 实现要点**：
- `PermissionHook`：将 Phase 4 `PermissionManager.decide()` 的 `ALLOW/DENY/ASK` 映射为 `HookDecision`。`ASK` 需在 hook 内部处理用户交互（弹出确认对话框或检查缓存）
- `PlanModeHook`：将 Phase 3 `PlanModeTracker.is_tool_allowed()` 的 `bool` 返回映射为 `HookDecision.ALLOW/DENY`
- `EditTrackerHook`：在 `on_tool_result` 中提取 `arguments["file_path"]`（仅 search_replace/write_file）。shell_exec 不追踪——sed/tee/重定向的编辑副作用无法可靠解析
- `LifecycleHook`：将 Phase 4 `HookDispatcher` 包装为 `AgentHook`。核心映射逻辑：

```python
# LifecycleHook.on_tool_call 内部：
hook_result = await self._dispatcher.dispatch(HookEvent.PRETOOL_USE, context)
if hook_result.action == "deny":
    return HookDecision.DENY
if hook_result.action == "modify" and hook_result.modified_args:
    arguments.update(hook_result.modified_args)  # Phase 4 返回独立 dict，合并到 Agent 的 args
    return HookDecision.MODIFY
return HookDecision.ALLOW
```

注意 MODIFY 的两层语义差异：Phase 1 `AgentHook` 的 MODIFY 靠 hook **直接修改 args dict in-place**（§3.7.4 注释），
Phase 4 `HookResult.modified_args` 是一个**独立 dict**（不修改原 args）。
LifecycleHook 的职责是桥接这一差异——`arguments.update(hook_result.modified_args)`。

**设计决策**：
- ※ 逻辑参考 Rust 版 `SessionActor::process_conversation_turn()` (`turn.rs:1695-2300`)
- **AgentHook 中间件管道**：Agent 的扩展点不是"在方法里插入 if 语句"，而是注册钩子。符合开放-封闭原则——对扩展开放（新增 Hook），对修改封闭（不改 Agent）
- **AsyncIterator 暴露流式事件**：Agent 不直接 print，而是 yield StreamEvent。CLI 和 TUI 都能消费同一个 Agent
- **工具定义每轮重新获取**：`tools.get_definitions()` 每轮调用而非缓存，确保新注册的 MCP 工具下一轮立即可用
- **max_turns 硬限制**：`range(self._max_turns)`，超出抛 MaxTurnsReached
- **流式 tool_call 累积**：LLM 的 tool_call 在 SSE 流中可能分多次到达，用 dict 按 index 累积 id/name/arguments
- **依赖**：`core.session`, `api.client`, `tools.registry`, `core.exceptions`
- **不依赖**：CLI, TUI, HTTP 细节, 具体工具实现

### 3.7.8 Hook 异常处理契约

Hook 的底层依赖可靠性差距很大——从纯 CPU 计算（CompactionPolicy）到磁盘 I/O（SessionStore.save）、
SQLite 查询（MemoryIndex.search）、用户自定义脚本（LifecycleHook 的 HookDispatcher）。
必须定义统一的异常处理语义，否则实现者不知道异常该自己 catch 还是冒泡。

**两层防御策略**：

#### 第一层——Hook 实现者契约（每个 Hook 自己保证）

| Hook 方法类型 | 失败策略 | 典型 Hook | 理由 |
|:---|:---|:---|:---|
| `on_session_start` / `on_turn_start` / `on_turn_end` | **降级**：catch + log.warning + 返回 None | MemoryHook, CompactionHook, PersistenceHook, EditTrackerHook | 增值功能——记忆/压缩/持久化失败不应阻断 Agent 主循环 |
| `on_tool_call` | **fail-safe 保守拒绝**：catch + log.error + 返回 `HookDecision.DENY` | PermissionHook, PlanModeHook, LifecycleHook | 安全敏感决策——权限/Plan Mode 出错时默认拒绝，不允许放行 |
| `on_tool_result` | **降级**：catch + log.warning + 返回 None | EditTrackerHook, LifecycleHook | post-execution hook——编辑追踪/用户脚本失败不应阻断结果返回给模型 |

**实现示例**（MemoryHook，on_session_start 降级模式）：
```python
async def on_session_start(self, session):
    try:
        results = await self._index.search(session.last_user_query)
        if results:
            session.add_user_message(self._format(results))
    except Exception:
        logger.warning("MemoryHook: 记忆注入失败，跳过", exc_info=True)
        # 降级——Agent 继续运行，只是本轮没有记忆增强
```

**实现示例**（PermissionHook，on_tool_call fail-safe 模式）：
```python
async def on_tool_call(self, tool_name, arguments):
    try:
        return await self._do_decide(tool_name, arguments)
    except Exception:
        logger.error(f"PermissionHook: 权限决策异常，默认拒绝 {tool_name}", exc_info=True)
        return HookDecision.DENY  # fail-safe
```

#### 第二层——Agent 防御性兜底（可选，Phase 1 实现）

Agent 在调用 Hook 时包裹 try/except 作为最后防线——如果 Hook 实现者忘了写 try/except，
Agent 兜底 catch + log.error + 按类型处理（on_tool_call 当作 DENY，其他当作 no-op）。
这层不是 Hook 实现者的替代品，而是防御性编程的保险网：

```python
# _execute_one_tool() 中的防御性包裹：
for hook in self._hooks:
    try:
        decision = await hook.on_tool_call(tc.name, args)
    except Exception:
        logger.error(f"Hook {type(hook).__name__}.on_tool_call 异常，当作 DENY", exc_info=True)
        decision = HookDecision.DENY  # fail-safe 兜底
    if decision == HookDecision.DENY:
        return ToolResult.error(f"权限拒绝: {tc.name}")
    ...
```

**设计决策**：两层防御同时生效。第一层（Hook 自 catch）是正常路径，让 Hook 自己决定降级粒度；
第二层（Agent 兜底）是保险网，防止实现者遗漏 try/except 时 Agent 崩溃。
如果两层都缺失（当前状态），一个 SQLite OperationalError 就能让整个 turn 崩溃。

### 3.7.9 设计决策（续）

---

## 3.8 `core/exceptions.py` — 自定义异常

**职责**：定义 Agent 专用的异常类型，比裸 `RuntimeError` 更精确。

**公开类**：

| 类 | 父类 | 触发场景 |
|:---|:---|:---|
| `AgentError` | `Exception` | 所有 Agent 异常的基类 |
| `MaxTurnsReached` | `AgentError` | 达到 max_turns 上限 |
| `AuthenticationError` | `AgentError` | 未登录或 token 无效 |
| `ToolNotFoundError` | `AgentError` | 模型调用了不存在的工具 |

**设计决策**：
- 继承层次扁平（只有一层），不需要复杂的异常分类
- 每个异常携带上下文数据（如 `MaxTurnsReached.max_turns`、`ToolNotFoundError.tool_name`）

---

## 3.9 `tools/definition.py` — 工具定义

**职责**：定义单个工具的完整描述信息。

**公开类**：

```python
class ToolDefinition:
    """单个工具的完整描述。

    参数：
    - name: 工具名称（模型可见）
    - description: 工具描述（模型可见，用于决策是否调用）
    - input_model: Pydantic 模型（自动生成 JSON Schema + 运行时验证）
    - handler: 异步执行函数 async def fn(input) -> str | ToolResult
    - category: 可选分类标签
    """

    def __init__(self, name: str, description: str,
                 input_model: type[BaseModel], handler: Callable,
                 category: str | None = None): ...

    def to_openai_schema(self) -> dict:
        """转换为 OpenAI function-calling 格式。"""
```

**设计决策**：
- ※ 逻辑参考 Rust 版 `FinalizedTool` (`registry/types.rs:405-439`)
- Rust 版需要 7 个闭包（parse_input, validate_params, output_converter, apply_params, register_params, register_in_local, metadata），Python 版简化为 3 个核心字段：input_model（自动提供验证+schema）、handler（执行逻辑）、description
- **不需要 output_converter**：Rust 版需要把 JSON 转回类型化输出，Python 版工具直接返回 str 或 ToolResult
- **依赖**：仅 Pydantic。**不依赖** ToolRegistry, Agent, CLI

---

## 3.10 `tools/result.py` — 工具执行结果

**职责**：统一的工具返回类型。所有工具通过它返回结果给模型。

**公开类**：

```python
class ToolResult(BaseModel):
    """统一的工具返回类型。

    字段：
    - output: str — 给模型看的文本（可能被截断）
    - is_error: bool = False — 执行是否失败
    - truncated: bool = False — 是否被截断（模型需知道信息不完整）
    """

    @classmethod
    def ok(cls, text: str, truncated: bool = False) -> "ToolResult": ...
    @classmethod
    def error(cls, text: str) -> "ToolResult": ...
```

**设计决策**：
- ※ 逻辑参考 Rust 版 `ToolRunResult` (`types/output.rs`)
- Rust 版需要区分 output（结构化的）和 prompt_text（给模型看的），Python 版统一为 output 字段
- `is_error` 字段让模型知道结果不可用——可能触发模型换一种方式重试
- `truncated` 字段让模型知道信息不完整——可能触发模型用 read_file 获取完整内容

---

## 3.11 `tools/registry.py` — 工具注册表

**职责**：工具的注册、查询和调度中心。

**公开类**：

```python
class ToolRegistry:
    """工具注册和调度中心。

    使用方式：
        registry = ToolRegistry()

        @registry.register("read_file", ReadFileInput, description="...")
        async def read_file(input: ReadFileInput) -> str: ...

        registry.finalize()  # 锁定

        result = await registry.execute("read_file", {"file_path": "..."})
    """

    def __init__(self, max_output_chars: int = 100_000): ...

    def register(self, name: str, input_model: type[BaseModel], *,
                 description: str, category: str | None = None) -> Callable:
        """装饰器：注册工具。必须在 finalize() 前调用。"""

    def finalize(self) -> None:
        """锁定注册表。之后不可再注册。等价于 Rust 版 FinalizedToolset 不可变语义。"""

    def get_definitions(self) -> list[dict]:
        """返回所有工具的 OpenAI 格式定义。每轮调用，确保最新。"""

    async def execute(self, name: str, arguments: dict) -> ToolResult:
        """执行指定工具。

        步骤：
        1. 查找工具 -> 找不到抛 ToolNotFoundError
        2. Pydantic model_validate() 验证参数 -> 失败返回 ToolResult.error()
        3. 调用 handler -> 异常捕获 -> ToolResult.error()
        4. 统一格式 + 截断 -> 返回 ToolResult
        """

    def list_names(self) -> list[str]: ...
```

**设计决策**：
- ※ 逻辑参考 Rust 版 `ToolRegistryBuilder` + `FinalizedToolset` (`registry/types.rs`)
- **装饰器注册**：等价于 Rust 版 `register::<T>()`，每个工具在模块加载时注册
- **finalize() 锁**：等价于 Rust 版 FinalizedToolset 的不可变语义
- **Pydantic 验证**：`model_validate()` 替代 Rust 版的 `serde_path_to_error` + `validate_params_json`
- **统一截断**：所有工具结果通过 `_maybe_truncate()` 统一处理，不依赖每个工具自己截断
- **依赖**：`tools.definition`, `tools.result`, `core.exceptions`, `helpers.text_truncate`
- **不依赖**：Agent, Session, CLI

---

## 3.12 `tools/builtin/` — 内置工具

Phase 1 提供 6 个内置工具，每个都是一个独立文件，只依赖 `tools/result.py`。

#### 3.12.1 `file_read.py`

※ 逻辑参考 Rust 版 ReadFileTool (`implementations/grok_build/read_file/mod.rs`)

- **输入模型** (`ReadFileInput`)：`file_path: str`, `offset: int = 0`, `limit: int = 2000`
- **安全措施**：二进制检测（前 8KB 中 null byte > 10% -> 拒绝）；路径展开 (`expanduser().resolve()`)
- **输出格式**：带行号的文本 (`{line:6d}|{content}`)
- **截断**：超 limit 时追加 footer `[输出已截断 — 共 N 行。使用 offset=X 继续。]`
- **依赖**：仅 `pathlib`, `pydantic`

#### 3.12.2 `file_search.py`

※ 逻辑参考 Rust 版 GrepTool (`implementations/grok_build/grep/mod.rs`)

- **输入模型** (`SearchInput`)：`pattern`, `path`, `include`, `ignore_case`, `max_results`
- **双引擎策略**：优先使用系统安装的 `rg`（结构化 JSON 输出），fallback 到 Python `re` 模块
- **rg 路径**：`subprocess.run(["rg", "--line-number", ...])`，30 秒超时
- **Python re 路径**：`path.rglob(glob)` 配合 `re.compile().search()`
- **截断**：超 max_results 时截断 + footer

#### 3.12.3 `shell_exec.py`

※ 逻辑参考 Rust 版 BashTool (`implementations/grok_build/bash/mod.rs`)

- **输入模型** (`ShellInput`)：`command`, `timeout: int = 120`, `workdir: str | None`
- **命令解析**：`shlex.split()` 安全解析
- **执行**：`asyncio.create_subprocess_exec()` 非阻塞
- **超时处理**：`asyncio.wait_for()` 超时 -> `proc.kill()`
- **输出**：stdout + stderr 合并，非零退出码标记 `is_error=True`
- **依赖**：`asyncio`, `shlex`, `os`, `pydantic`, `tools.result`

#### 3.12.4 `file_edit.py` — search_replace

※ 逻辑参考 Rust 版 SearchReplaceTool (`implementations/grok_build/search_replace/mod.rs`)

- **输入模型** (`SearchReplaceInput`)：`file_path: str`, `old_string: str`, `new_string: str`, `replace_all: bool = False`
- **工具类型**：`Edit`——Agent 最核心的编辑工具。精确字符串替换：读文件 → 找匹配 → 替换 → 原子写入 → 返回 diff
- **唯一性检查**：`replace_all=False` 时 `old_string` 必须恰好出现一次，多于一次返回错误 + 上下文提示
- **原子写入**：`tempfile.NamedTemporaryFile` + `os.replace()`，写入中断不留半截文件
- **Diff 计算**：`difflib.unified_diff()` 返回 human-readable diff 给模型
- **大文件保护**：超过 1MB 拒绝编辑，提示用 shell sed 代替
- **不读文件（prompt 级约定，非工具级强制）**：工具本身无状态，不校验"是否先调过 read_file"。强制力来自 old_string 精确匹配——模型若跳过 read 直接猜 old_string，大概率匹配失败，此时返回专项错误消息（"未找到匹配，建议先 read_file 确认内容"）引导模型自纠正。这比工具层硬拦截更鲁棒：即使模型从上下文记忆中正确复现了 old_string，编辑依然合法
- **错误处理**：文件不存在 / 未找到匹配 / replace_all=False 且多次匹配 / new_string==old_string → 各自有专项错误消息
- **依赖**：`pathlib`, `difflib`, `tempfile`, `pydantic`, `tools.result`

#### 3.12.5 `file_write.py` — write_file

※ 逻辑参考 Rust 版 OpenCodeWriteTool (`implementations/opencode/write/mod.rs`)

- **输入模型** (`WriteFileInput`)：`file_path: str`, `content: str`
- **工具类型**：`Write`——创建新文件或覆盖已有文件。和 search_replace 互补：写新文件用 write_file，改已有文件用 search_replace
- **安全检查**：路径展开 (`expanduser().resolve()`)，拒绝写入 `/etc/`、`/sys/`、`/proc/`、`/boot/`、`~/.ssh/`、`~/.gnupg/` 等系统目录
- **目录创建**：父目录不存在时自动创建（`mkdir -p`）
- **覆盖保护**：文件已存在时先读旧内容用于 diff，保留原文件权限位（`os.chmod(path, old_mode)`）
- **原子写入**：同 search_replace（temp file + os.replace）
- **大文件保护**：超过 5MB 拒绝，提示用 shell 重定向
- **依赖**：`pathlib`, `shutil`, `tempfile`, `difflib`, `pydantic`, `tools.result`

#### 3.12.6 `web_fetch.py` — web_fetch

※ 逻辑参考 Rust 版 WebFetchTool (`implementations/grok_build/web_fetch/`)

- **输入模型** (`WebFetchInput`)：`url: str`, `prompt: str | None = None`
- **工具类型**：`WebFetch`——获取 URL 内容，HTML 转为 Markdown 返回给模型
- **HTTP→HTTPS 自动升级**
- **SSRF 三层防护**：(1) URL 解析阶段拒绝内网 IP/hostname；(2) DNS 解析后检查解析 IP 不指向内网；(3) 手动处理 3xx 重定向，对新 URL 重新执行检查
- **响应截断**：超过 5MB 截断，返回前 256KB + footer 提示
- **内容类型**：非 HTML 返回 "Content-Type: xxx, 无法处理"
- **HTML→Markdown**：使用 `markdownify` 或 `html2text` 库转换
- **依赖**：`httpx`, `markdownify`（或 `html2text`）, `pydantic`, `tools.result`

---

## 3.13 `helpers/` — 工具函数

纯函数模块。零项目内依赖，可脱离整个项目独立使用。

#### 3.13.1 `token_counter.py`

※ 逻辑参考 Rust 版 xai-token-estimation crate

| 函数 | 用途 |
|:---|:---|
| `estimate_tokens(text: str) -> int` | bytes/4 启发式 token 估算 |
| `exceeds_threshold(used: int, window: int, threshold_pct: int) -> bool` | 判断是否超过阈值百分比 |

#### 3.13.2 `text_truncate.py`

※ 逻辑参考 Rust 版 truncate.rs

| 函数 | 截断策略 |
|:---|:---|
| `truncate_with_preview(text, max_chars, preview_chars, hint) -> (str, bool)` | 保留头部 + footer 提示 |
| `truncate_middle(text, max_chars) -> (str, bool)` | 保留头尾 + 中间省略号 |

所有函数返回 `(结果文本, 是否被截断)` 元组，调用方据此决定是否给模型追加提示。

#### 3.13.3 `file_io.py`

※ 由修复 C1 引入（消除 file_edit / file_write 中的重复代码）

| 函数 | 用途 |
|:---|:---|
| `atomic_write(path: Path, content: str, encoding: str = "utf-8") -> None` | 原子文件写入（mkstemp + write + fsync + os.replace） |

被 `tools/builtin/file_edit.py` 和 `tools/builtin/file_write.py` 共享调用。

---

## 3.14 `cli/main.py` — 命令行入口

**职责**：解析 CLI 参数、组装所有依赖、分发到 headless 或 REPL 模式。

※ 逻辑参考 Rust 版 `main.rs` 的 `run_agent_command()`

**函数**：

| 函数 | 用途 |
|:---|:---|
| `_build_tools() -> ToolRegistry` | 注册 6 个内置工具（file_read / file_search / shell_exec / search_replace / write_file / web_fetch）+ finalize |
| `_build_agent(args, session, client, tools) -> Agent` | **核心组装函数**——创建 Agent 实例并注入 hooks 列表。这是整个开放封闭原则的集成点：Phase 1 返回空 hooks 列表，Phase 2-4 只需修改此函数追加 Hook 实例，Agent 源码不动 |
| `_run_headless(agent, prompt)` | 单次 prompt -> 流式输出到 stdout |
| `_run_interactive(agent, args, client, tools)` | REPL 循环：读取输入 -> agent.run() -> 打印结果。`/clear` 重建 Session+Agent 真正清除上下文 |
| `main()` | argparse 入口 |
| `_resolve_provider_config(args) -> ProviderConfig` | **五级认证解析**（架构补强）——按优先级解析 API key：CLI > config.yaml > env > settings.json > OAuth。Level 2-4 用实际 base_url 重新推断后端（非 argparse 默认值） |
| `_infer_backend_from_url(base_url, provider) -> ApiBackend` | 后端协议推断（纯函数，接受 base_url 字符串）——auto: URL 含 anthropic/deepseek → Messages，否则 ChatCompletions |
| `_infer_backend(args) -> ApiBackend` | _infer_backend_from_url 的 CLI 封装（用 args.base_url），用于 Level 1 CLI --api-key |
| `_infer_auth_scheme_from_url(base_url, backend) -> AuthScheme` | 认证方案推断（纯函数）——Messages + api.anthropic.com → XApiKey，否则 Bearer |
| `_infer_auth_scheme(args) -> AuthScheme` | _infer_auth_scheme_from_url 的 CLI 封装，用于 Level 1 CLI --api-key |
| `_resolve_oauth_config(args) -> ProviderConfig` | OAuth fallback：加载 token → 刷新过期 → 构造 config |
| `_run_config_command(args: list[str])` | **config 子命令分发**——处理 `code-agent config set/show/clear`。set 写入两个文件：`~/.evil-code/config.yaml`（主）+ `~/.claude/settings.json`（备）。支持 CLI 参数模式和交互输入模式。show 同时显示两个文件。clear 同时清除两个文件 |

**_build_agent()——开放封闭原则的集成点**：

```python
def _build_agent(args, session: Session, client: ChatClient, tools: ToolRegistry) -> Agent:
    """一站式组装 Agent，注入 hooks 列表。

    Phase 1: hooks 为空列表。Phase 2-4 在此函数内追加 Hook 实例。
    本函数是 Agent 构造函数签名（session, client, tools, hooks）与各 Phase
    新增模块之间的唯一集成点——Agent 源码不需要任何修改。
    """
    hooks: list[AgentHook] = []  # Phase 1: 无 hook

    # Phase 2（示例——实际实现时取消注释）:
    # memory_index = MemoryIndex(db_path=...)
    # store = SessionStore(root_dir=...)
    # policy = CompactionPolicy()
    # hooks.extend([
    #     MemoryHook(memory_index),
    #     CompactionHook(policy),
    #     PersistenceHook(store),
    #     EditTrackerHook(),
    # ])

    # Phase 3（示例）:
    # plan_tracker = PlanModeTracker(plan_file_path=...)
    # hooks.append(PlanModeHook(plan_tracker))

    # Phase 4（示例）:
    # permission_mgr = PermissionManager(yolo_mode=args.yolo)
    # lifecycle_dispatcher = HookDispatcher()
    # hooks.extend([
    #     PermissionHook(permission_mgr),
    #     LifecycleHook(lifecycle_dispatcher),
    # ])

    return Agent(session, client, tools,
                 max_turns=args.max_turns,
                 hooks=hooks)
```

**组装流程**（等价于 Rust 版 AgentBuilder::build()）：
```
_resolve_provider_config(args) -> Session() -> ChatClient(config) -> _build_tools() -> _build_agent(args, session, client, tools)
```

**支持的 CLI 参数**（Phase 1 + 架构补强）：
- `prompt` (positional) — headless 模式的 prompt
- `--cwd` — 工作目录
- `--model` — 模型 ID
- `--max-turns` — 最大推理轮数
- `--base-url` — API 基础 URL（默认 https://api.anthropic.com/v1）
- `--api-key` — API 密钥（跳过所有文件/环境变量查找，架构补强新增）
- `--provider` — 后端协议：auto/chat_completions/messages/responses（默认 auto 推断，架构补强新增）
- `--tui` — 启动 Textual TUI 全屏交互模式（config 子命令后推荐入口）

**config 子命令**（架构补强——参数 → 配置文件）：
```
code-agent config set [--api-key KEY] [--base-url URL] [--model MODEL]
                      [--auth-scheme bearer|x_api_key]
                      [--backend auto|chat_completions|messages|responses]
code-agent config show
code-agent config clear
```
`config set` 缺省参数时交互式提示输入。写入两个文件：
1. `~/.evil-code/config.yaml`（evil-code 原生，YAML，主配置）
2. `~/.claude/settings.json`（cc-switch 兼容，JSON，备选）

`config show` 同时显示两个文件的配置状态。`config clear` 同时清除两个文件。

**五级认证解析流程**（架构补强）：
```
1. CLI --api-key → 直接用，auth_scheme 由 --provider 推断（最高优先级）
2. ~/.evil-code/config.yaml（evil-code 原生，免登录——config set 一次后自动生效）
3. 环境变量 ANTHROPIC_AUTH_TOKEN (Bearer) / ANTHROPIC_API_KEY (XApiKey) + ANTHROPIC_BASE_URL + ANTHROPIC_MODEL
4. ~/.claude/settings.json env 块（cc-switch 管理）→ ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY + BASE_URL + MODEL
5. ~/.claude/.credentials.json OAuth token → 检查过期 → 刷新 → 用 accessToken
```

cc-switch 联动：cc-switch Tauri 版写 settings.json，evil-code 读 settings.json，自动生效。用户无需额外操作。config 子命令让 evil-code 也能写 settings.json，形成双向兼容。

---

## 3.15 `config/settings_writer.py` — 配置文件生成（cc-switch 等价）

**职责**：参数 → settings.json 写入。cc-switch Tauri 版的配置生成等价物，但纯 CLI。

**动机**：用户反馈"正常使用应该有一段代码接受各种参数，然后生成配置文件"。手动编辑 JSON 不应作为正式流程。

**函数**：

| 函数 | 用途 |
|:---|:---|
| `save_settings_config(api_key, base_url, model, *, use_auth_token=True, settings_path=None) -> Path` | 写入 provider 配置到 settings.json env 块。合并保留非 env 键（theme 等），清除冲突的 auth 键后写入。原子写入（temp + fsync + rename） |
| `show_settings_config(settings_path=None) -> ConfigSummary` | 读取 settings.json 返回配置摘要 NamedTuple。不打印——调用方决定显示方式 |
| `clear_settings_config(settings_path=None) -> Path` | 只移除 auth 相关 env 变量（AUTH_TOKEN/API_KEY/BASE_URL/MODEL），保留其他 env 和非 env 键。空 env 块自动清除 |
| `_read_settings(path) -> dict` | 读 JSON，缺失/无效返回空 dict |
| `_atomic_write_json(path, data)` | 原子写入：tempfile → fsync → os.replace（同 token_store.py 模式） |
| `_mask_key(key) -> str` | 掩码 API key 用于显示：`sk-8635e...ef2b` |

**ConfigSummary NamedTuple**：

| 字段 | 类型 | 说明 |
|:---|:---|:---|
| api_key | str \| None | AUTH_TOKEN 或 API_KEY 的值 |
| base_url | str \| None | ANTHROPIC_BASE_URL |
| model | str \| None | ANTHROPIC_MODEL |
| auth_token_present | bool | 是否有 ANTHROPIC_AUTH_TOKEN |
| api_key_present | bool | 是否有 ANTHROPIC_API_KEY |
| is_configured | bool | 是否已配置（有 auth_token 或 api_key） |
| settings_path | Path | settings.json 路径 |

**常量**：
- `_DEFAULT_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"`
- `_MANAGED_KEYS = {ANTHROPIC_AUTH_TOKEN, ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, ANTHROPIC_MODEL}`

**与 auth/settings_store.py 的关系**：settings_writer 写 settings.json，settings_store 读 settings.json。单向数据流：writer → file → reader。两者通过文件格式耦合（同一套 key 名称），但代码互不依赖。

---

## 3.16 `tui/app.py` — Textual TUI 交互界面

**职责**：全屏终端 UI，提供配置 → 启动 → 对话的完整测试体验。

**动机**：用户反馈"我想要直接测试 agent，配置 settings 免登录，然后直接能使用简易的 UI 去操作，可以问问题，有对话框"。单元测试不满足此需求。

**EvilCodeApp(App)**：

| 组件 | 用途 |
|:---|:---|
| header-bar (Static) | 显示 model + backend，height 3 |
| chat-log (RichLog) | 对话区域，流式逐行输出，支持 markup |
| input-bar (Input) | 输入栏，dock bottom，处理时禁用 |
| Footer | 显示快捷键 |

**事件处理**：

| 方法 | 用途 |
|:---|:---|
| `__init__(agent, config)` | 注入 Agent + ProviderConfig，初始化 text_buffer 和 first_line 标志 |
| `compose()` | 生成 header + chat-log + input + footer |
| `on_mount()` | 聚焦输入栏，显示就绪信息（model/backend/endpoint） |
| `on_input_submitted(event)` | 核心：读取输入 → agent.run() → 流式输出 text_delta / tool_call_delta / error |
| `_flush_buffer(chat)` | 刷新累积的 text_buffer 到 RichLog |

**流式输出逻辑**：
- text_delta：累积到 buffer，遇 `\n` 逐行输出（流式效果）
- tool_call_delta：显示 `⟡ tool: {name}` 标记
- error：显示 `✗ {text}`
- MaxTurnsReached / AuthenticationError / AgentError / Exception：分别显示对应错误信息

**CSS 布局**：
- Screen: vertical layout
- header-bar: height 3, $boost background, bottom border
- chat-log: 1fr (填满), solid border
- input-bar: dock bottom, height 3, top border

**BINDINGS**: `ctrl+c → quit`

**入口**：
- `code-agent --tui` → main.py 构造 EvilCodeApp(agent, config) → app.run()
- `python -m code_agent.tui.app` → standalone main() 解析参数 + 认证解析 + 构造 agent

**独立 main()**：tui/app.py 有自己的 main() 入口，复用 cli/main.py 的 _resolve_provider_config / _build_tools / _build_agent 函数。

---

## 4. 依赖关系图

```
cli/main.py
  ├── core/agent.py        ← 编排层（Agent 主循环 + AgentHook 中间件管道）
  │     ├── core/session.py ← 对话状态 (messages.py + helpers/token_counter.py)
  │     ├── api/client.py   ← LLM HTTP 通信 (httpx direct, 不依赖 openai SDK)
  │     └── tools/registry.py ← 工具注册/调度 (definition.py + result.py + helpers/)
  ├── api/client.py
  │     ├── api/sse.py       ← SSE 流解析 (iter_sse_events, 三后端共享)
  │     ├── api/backends/   ← 三后端协议 (base + chat_completions + messages + responses)
  │     └── auth/           ← token 管理 (settings_store + token_refresh + token_store)
  ├── config/settings_writer.py ← 配置生成 (参数→settings.json, cc-switch 等价)
  ├── tui/app.py           ← Textual TUI 交互界面 (--tui 入口)
  └── tools/builtin/*.py   ← 6 个工具 (file_read/file_search/shell_exec/file_edit/file_write/web_fetch)
        └── tools/result.py

依赖方向: cli -> core -> api/tools -> auth/helpers
         config/ 独立写 settings.json (不依赖 auth/settings_store, 但通过文件格式耦合)
         tui/ 依赖 cli (复用 _resolve_provider_config/_build_tools/_build_agent)
         上层知道下层，下层不知道上层
外部依赖: httpx (HTTP), pydantic (校验), markdownify (HTML→MD)
         textual>=0.50.0 (TUI, optional [tui] group)
         不再依赖 openai SDK — httpx 直接通信
```

**核心原则**：
1. 下层不知道上层存在
2. `core/agent.py` 只依赖注入的抽象接口，不依赖具体实现
3. `auth/` 和 `helpers/` 可脱离整个项目独立使用（settings_store.py 仅依赖 api.schemas.AuthScheme 枚举）
4. `api/backends/` 各后端互不依赖，通过 Backend Protocol 解耦
5. `api/client.py` 通过 `get_backend()` 工厂获取具体后端实例，不硬编码后端选择
6. 每个文件职责单一，核心逻辑不超过 200 行

---

## 5. 封装层次总结

```
┌──────────────────────────────────────────────────┐
│ cli/main.py          组装层                       │
│ 职责: 解析参数、认证解析、创建对象、连线依赖      │
│ 依赖: core, api, tools, auth (所有下层)          │
│ 五级认证: --api-key > config.yaml > env > settings.json > OAuth │
├──────────────────────────────────────────────────┤
│ core/agent.py        编排层                       │
│ 职责: think -> act -> observe 主循环             │
│ 依赖: Session (同层), ChatClient (注入),         │
│       ToolRegistry (注入)                        │
├──────────────────┬───────────────────────────────┤
│ api/client.py    │ tools/registry.py  服务层      │
│ 职责: HTTP 通信  │ 职责: 工具注册/调度            │
│ httpx direct     │ 依赖: definition, result,     │
│ 依赖: api/sse.py │       helpers                  │
│      api/backends│                                │
│      auth 模块   │                                │
├──────────────────┤                               │
│ api/sse.py       │                               │
│ 职责: SSE 解析   │                               │
│      iter_sse_   │                               │
│      events()    │                               │
│ 依赖: 无(纯解析) │                               │
├──────────────────┤                               │
│ api/backends/    │                               │
│ 职责: 三后端转换 │                               │
│ base: Protocol   │                               │
│ + BackendRequest │                               │
│ chat_completions │                               │
│ messages         │                               │
│ responses        │                               │
│ 依赖: api/schemas│                               │
├──────────────────┼───────────────────────────────┤
│ auth/            │ helpers/           工具层      │
│ 职责: token 管理 │ 职责: 纯函数工具集             │
│ settings_store   │ 依赖: 无(纯标准库)            │
│ + token_refresh  │                                │
│ + token_store    │                                │
│ 依赖: schemas    │                                │
│ (AuthScheme枚举) │                                │
└──────────────────┴───────────────────────────────┘
```

**与 Rust 版的关键架构差异**：

| Rust 版 | Python 版 | 原因 |
|:---|:---|:---|
| Actor 模式 (ChatStateActor) | 直接方法调用 (Session) | asyncio 单线程无竞态 |
| 7 个闭包的工具注册 | 装饰器 + Pydantic | Python 动态类型更灵活 |
| mpsc channel 通信 | 直接 async 调用 | 不需要跨线程通信 |
| parking_lot::RwLock | 无需锁 | 单线程 asyncio |
| `&mut self` 独占 | 普通方法 | Python 无所有权概念 |
| Arc<FinalizedToolset> | 普通对象引用 | Python GC 自动管理 |
| 单后端 (Anthropic SDK) | 多后端 (httpx + Backend Protocol) | 支持 OpenAI/Anthropic/DeepSeek 等任意 provider |
| OAuth 独占认证 | 五级认证解析 (CLI>config.yaml>env>settings.json>OAuth) | 原生配置免登录 + cc-switch 兼容 |
