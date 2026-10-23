# Phase 1：最小可行 Agent - 进度

> 目标: 在命令行中完成 "用户输入 → LLM 推理 → 工具调用 → 返回结果" 的完整闭环
> 预估工时: 3-5 天
> 状态: 已完成 (10/10 模块 + 多 Provider 架构补强)

---

## 范围：Phase 1 必须交付什么

1. **认证**: OAuth token 读取/刷新 ~/.claude/.credentials.json + settings.json 读取（cc-switch 联动）
2. **LLM 客户端**: httpx 直连 + Backend Protocol（多 provider: OpenAI/Anthropic/DeepSeek）+ 流式 SSE
3. **Agent 循环**: think -> act -> observe 循环 + AgentHook 中间件
4. **Session**: 对话历史管理 + dangling call 修复 + token 估算
5. **工具注册表**: 装饰器注册 + finalize 锁 + Pydantic 验证 + 调度
6. **6 个内置工具**: file_read, file_search, shell_exec, file_edit, file_write, web_fetch
7. **CLI**: argparse 入口，headless + REPL 双模式 + 五级认证解析
8. **测试**: pytest 覆盖 auth、agent 循环、tools、session、SSE、backends、settings
9. **多 Provider 架构补强**: api/sse.py + api/backends/（三后端）+ auth/settings_store.py + ProviderConfig + ApiBackend/AuthScheme 枚举

---

## 最终架构 (Phase 1)

```
code_agent/
├── __init__.py                    # 版本号
├── auth/
│   ├── __init__.py                # 导出: load_token, save_token, refresh_token, is_token_expired, load_settings_credentials, load_config, save_config, generate_config
│   ├── token_refresh.py           # OAuth2 刷新（异步 httpx，纯函数）
│   ├── token_store.py             # 原子文件读写（~/.claude/.credentials.json）
│   ├── settings_store.py          # 读取 ~/.claude/settings.json env 块（cc-switch 联动）[新增]
│   └── config_store.py            # 原生 config 读/写/生成（~/.evil-code/config.yaml）[新增]
├── api/
│   ├── __init__.py                # 导出: StreamEvent, ToolCall, ApiBackend, AuthScheme, ProviderConfig
│   ├── client.py                  # ChatClient（httpx 直连 + Backend Protocol 分发，移除 openai SDK）[重写]
│   ├── schemas.py                 # Pydantic 模型 + ApiBackend/AuthScheme 枚举 + ProviderConfig 数据类
│   ├── sse.py                     # 共享 SSE 流解析器: iter_sse_events() [新增]
│   └── backends/                  # 三后端协议 [新增]
│       ├── __init__.py            # 工厂: get_backend(ApiBackend) -> Backend
│       ├── base.py                # Backend Protocol + BackendRequest 数据类
│       ├── chat_completions.py    # OpenAI /chat/completions 后端
│       ├── messages.py            # Anthropic /messages 后端（DeepSeek /anthropic 兼容）
│       └── responses.py           # OpenAI /responses 后端
├── core/
│   ├── __init__.py                # 导出: Session, 异常类型（Agent 因循环导入不 re-export）
│   ├── agent.py                   # 主循环 + AgentHook 协议 + HookDecision
│   ├── session.py                 # 对话状态 + dangling 修复 + token 计数
│   ├── messages.py                # Tagged union: System/User/Assistant/ToolResult
│   └── exceptions.py             # AgentError, MaxTurnsReached, AuthenticationError, ToolNotFoundError
├── config/                        # 新增: 配置写入工具
│   ├── __init__.py                # 包导出
│   └── settings_writer.py         # 写入 ~/.claude/settings.json env 块（cc-switch 兼容）[新增]
├── tools/
│   ├── __init__.py                # 导出: ToolRegistry, ToolDefinition, ToolResult
│   ├── registry.py                # 注册 + finalize + 执行 + 截断
│   ├── definition.py              # ToolDefinition (name + description + input_model + handler)
│   ├── result.py                  # ToolResult (output + is_error + truncated)
│   └── builtin/
│       ├── file_read.py           # 读文件（二进制检测 + 行号 + 截断）
│       ├── file_search.py         # 代码搜索（rg 优先，Python re 回退）
│       ├── shell_exec.py          # Shell 执行（shlex + subprocess + 超时 kill）
│       ├── file_edit.py           # search_replace（唯一匹配 + 原子写入 + diff）
│       ├── file_write.py          # write_file（创建/覆盖 + 安全检查 + 原子写入）
│       └── web_fetch.py           # URL 获取（SSRF 防护 + HTML->Markdown）
├── cli/
│   └── main.py                    # argparse + _build_tools + _build_agent + 五级认证 + config set/show/clear + headless/REPL [重写]
└── helpers/
    ├── token_counter.py           # bytes/4 估算 + 阈值判断
    ├── text_truncate.py           # truncate_with_preview + truncate_middle
    ├── file_io.py                 # atomic_write（file_edit + file_write 共享）
    └── tracer.py                  # TraceWriter 结构化 JSONL 追踪日志 [新增]
```

---

## 模块进度

### 模块 1: 项目骨架 + Helpers [已完成]

| 项目 | 状态 | 位置 |
|:---|:---|:---|
| pyproject.toml (hatchling, 依赖, 入口点) | 完成 | `pyproject.toml` |
| 包结构 (6 个子包 + tests/) | 完成 | `code_agent/` |
| estimate_tokens() - bytes/4 启发式 | 完成 | `helpers/token_counter.py` |
| exceeds_threshold() - 百分比检查 | 完成 | `helpers/token_counter.py` |
| truncate_with_preview() - 头部 + footer | 完成 | `helpers/text_truncate.py` |
| truncate_middle() - 头尾 + 省略号 | 完成 | `helpers/text_truncate.py` |

### 模块 2: 认证层 [已完成]

| 项目 | 状态 | 位置 |
|:---|:---|:---|
| is_token_expired() - 同步, 毫秒时间戳 + 缓冲 | 完成 | `auth/token_refresh.py` |
| refresh_token() - 异步 httpx POST, 不可变 | 完成 | `auth/token_refresh.py` |
| load_token() - 读取 ~/.claude/.credentials.json | 完成 | `auth/token_store.py` |
| save_token() - 原子写入 (mkstemp+fsync+replace) | 完成 | `auth/token_store.py` |
| load_settings_credentials() - 读取 settings.json env 块 | 完成 | `auth/settings_store.py` |
| load_config() - 读取 ~/.evil-code/config.yaml (原生配置, 自动登录) | 完成 | `auth/config_store.py` |
| save_config() - 原子写入 ~/.evil-code/config.yaml | 完成 | `auth/config_store.py` |
| generate_config() - 接受参数, 生成 EvilCodeConfig | 完成 | `auth/config_store.py` |

### 模块 3: 数据模型 [已完成]

| 项目 | 状态 | 位置 |
|:---|:---|:---|
| AgentError / MaxTurnsReached / AuthenticationError / ToolNotFoundError | 完成 | `core/exceptions.py` |
| SystemMessage / UserMessage / AssistantMessage / ToolResultMessage | 完成 | `core/messages.py` |
| Message 类型别名（4 种消息类型的 union） | 完成 | `core/messages.py` |
| ToolCallItem (嵌套在 AssistantMessage 中) | 完成 | `core/messages.py` |

### 模块 4: LLM 通信 [已完成]

| 项目 | 状态 | 位置 |
|:---|:---|:---|
| StreamEvent / ToolCall / FunctionCall / ToolDefinitionSchema / FunctionSchema | 完成 | `api/schemas.py` |
| ToolCallDelta (流式累积模型) | 完成 | `api/schemas.py` |
| ApiBackend 枚举 (chat_completions / messages / responses) | 完成 | `api/schemas.py` |
| AuthScheme 枚举 (bearer / x_api_key) | 完成 | `api/schemas.py` |
| ProviderConfig 数据类 (api_backend, base_url, api_key, auth_scheme, model, max_tokens, temperature) | 完成 | `api/schemas.py` |
| ChatClient (httpx 直连, 后端分发, chat_stream, 每请求覆盖) | 完成 | `api/client.py` |
| iter_sse_events (共享 SSE 解析: data/event/DONE/多行) | 完成 | `api/sse.py` |
| Backend Protocol + BackendRequest (url, headers, body) | 完成 | `api/backends/base.py` |
| ChatCompletions 后端 (build_request + parse_stream) | 完成 | `api/backends/chat_completions.py` |
| Messages 后端 (消息转换 + 工具转换 + parse_stream) | 完成 | `api/backends/messages.py` |
| Responses 后端 (input items + store=false + parse_stream) | 完成 | `api/backends/responses.py` |
| get_backend 工厂 (单例分发) | 完成 | `api/backends/__init__.py` |

### 模块 5: 工具基础设施 [已完成]

| 项目 | 状态 | 位置 |
|:---|:---|:---|
| ToolResult (ok/error 类方法) | 完成 | `tools/result.py` |
| ToolDefinition (to_openai_schema) | 完成 | `tools/definition.py` |
| ToolRegistry (register/finalize/execute/get_definitions/list_names/_maybe_truncate) | 完成 | `tools/registry.py` |

### 模块 6: 内置工具 [已完成]

| 项目 | 状态 | 位置 |
|:---|:---|:---|
| file_read (二进制检测 + 行号 + offset/limit) | 完成 | `tools/builtin/file_read.py` |
| file_search (rg + Python re 回退) | 完成 | `tools/builtin/file_search.py` |
| shell_exec (shlex + asyncio subprocess + 超时) | 完成 | `tools/builtin/shell_exec.py` |
| file_edit / search_replace (唯一匹配 + 原子 + diff) | 完成 | `tools/builtin/file_edit.py` |
| file_write (安全检查 + mkdir + 原子 + diff) | 完成 | `tools/builtin/file_write.py` |
| web_fetch (SSRF 三层 + HTML->Markdown + 重定向重检) | 完成 | `tools/builtin/web_fetch.py` |

### 模块 7: Agent 核心 [已完成]

| 项目 | 状态 | 位置 |
|:---|:---|:---|
| Session (add_*/to_api_messages/get_system_prompt/_repair_dangling_calls) | 完成 | `core/session.py` |
| Session 属性 (messages/prompt_count/estimated_tokens) | 完成 | `core/session.py` |
| AgentHook 协议 + HookDecision 枚举 | 完成 | `core/agent.py` |
| Agent.run() - 异步生成器 + hook 调用点 | 完成 | `core/agent.py` |
| Agent._execute_one_tool() - hook 链 + 防御性异常处理 | 完成 | `core/agent.py` |

### 模块 8: CLI 入口 [已完成]

| 项目 | 状态 | 位置 |
|:---|:---|:---|
| _build_tools() - 注册 6 个工具 + finalize | 完成 | `cli/main.py` |
| _build_agent() - hook 组装（开放-封闭原则） | 完成 | `cli/main.py` |
| _run_headless() / _run_interactive() | 完成 | `cli/main.py` |
| main() - argparse 入口 (prompt, --cwd, --model, --max-turns, --base-url, --api-key, --provider) | 完成 | `cli/main.py` |
| _resolve_provider_config() - 五级认证解析 (CLI > config.yaml > env > settings.json > OAuth) | 完成 | `cli/main.py` |
| _infer_backend() - 从 base_url 自动检测 wire format | 完成 | `cli/main.py` |
| _infer_auth_scheme() - Messages + anthropic.com → XApiKey, 否则 Bearer | 完成 | `cli/main.py` |
| _resolve_oauth_config() - OAuth 回退 (load_token → is_token_expired → refresh → save) | 完成 | `cli/main.py` |
| _run_config_command() - config set/show/clear 子命令 (参数 → 配置文件) | 完成 | `cli/main.py` |
| config set: 写入 ~/.evil-code/config.yaml (主) + ~/.claude/settings.json (备) | 完成 | `cli/main.py` |

### 模块 9: 测试 [完成]

| 项目 | 状态 | 位置 |
|:---|:---|:---|
| test_auth (token 读/刷新/过期) | 完成 | `tests/test_auth/` |
| test_api (ChatClient httpx 直连: 认证, 流式, 关闭) | 完成 | `tests/test_api/` |
| test_api (SSE 解析: data/event/DONE/多行/注释) | 完成 | `tests/test_api/test_sse.py` |
| test_api (settings_store: cc-switch settings.json 读取) | 完成 | `tests/test_auth/test_settings_store.py` |
| test_api (backends: 22 测试覆盖三后端) | 完成 | `tests/test_api/test_backends.py` |
| test_api (认证解析: 后端推断 + 五级优先级链, 25 测试) | 完成 | `tests/test_api/test_auth_resolution.py` |
| test_core (agent 循环 + mock LLM, session) | 完成 | `tests/test_core/` |
| test_tools (registry, 内置工具) | 完成 | `tests/test_tools/` |
| conftest.py (fixtures: MockChatClient, 临时目录, 工厂) | 完成 | `tests/conftest.py` |

---

## 验证标准（来自设计文档）

**P0（无此不可用）**:
- [x] 单轮 "Read this file" 请求正常工作（模型调用 file_read）
- [x] 多轮 "Find and fix this bug" 正常工作（模型调用 grep + read + bash）

**P1（显著体验提升）**:
- [x] Token 过期时自动刷新
- [x] 多 Provider SSE 解析 (iter_sse_events)
- [x] settings.json 认证 (cc-switch 联动)
- [x] 三后端请求构建 + 流解析
- [x] ChatClient httpx 直连 (401 → AuthError)
- [ ] Ctrl+C 优雅取消当前 turn

---

## 模块 10: 多 Provider 架构补强 [已完成]

> 架构补强 — 不是新的 Phase，而是 Phase 1 的结构性增强
> 支持任意 LLM provider（OpenAI, Anthropic, DeepSeek, xAI, Ollama 等）

### 动机

原始 Phase 1 代码硬编码了 openai SDK + Claude OAuth 路径。设计文档的 LLMClient Protocol 本应定义但从未实现。使用 DeepSeek（通过 cc-switch settings.json）或其他 provider 的用户根本无法使用 agent — 代码会在没有 ~/.claude/.credentials.json 的机器上报 "No credentials found"。

### 变更对比

| 领域 | 变更前 | 变更后 |
|:---|:---|:---|
| HTTP 客户端 | AsyncOpenAI SDK | httpx.AsyncClient 直连 |
| Provider 支持 | 仅 Claude OAuth | 任意 provider，三后端协议 |
| 认证 | 仅 OAuth token | 五级: CLI --api-key > config.yaml > env > settings.json > OAuth |
| 配置 | model + base_url 参数 | ProviderConfig 数据类 (backend + auth + model + temp) |
| SSE 解析 | openai SDK 内部 | iter_sse_events() 共享解析器 |
| Wire 格式 | 仅 OpenAI Chat Completions | 三后端: Chat Completions, Messages, Responses |

### 新增文件

| 文件 | 用途 |
|:---|:---|
| `api/sse.py` | 共享 SSE 流解析器 (iter_sse_events) |
| `api/backends/base.py` | Backend Protocol + BackendRequest 数据类 |
| `api/backends/chat_completions.py` | OpenAI /chat/completions 后端 |
| `api/backends/messages.py` | Anthropic /messages 后端（DeepSeek 兼容） |
| `api/backends/responses.py` | OpenAI /responses 后端 |
| `api/backends/__init__.py` | 工厂: get_backend(ApiBackend) -> Backend |
| `auth/settings_store.py` | 读取 ~/.claude/settings.json env 块（cc-switch） |
| `auth/config_store.py` | 原生 config 读/写/生成（~/.evil-code/config.yaml）[新增] |
| `config/__init__.py` | 包导出 (generate/save/load + settings writer) [新增] |
| `config/settings_writer.py` | 写入 ~/.claude/settings.json env 块（cc-switch 兼容）[新增] |

### 修改文件

| 文件 | 变更 |
|:---|:---|
| `api/schemas.py` | 新增 ApiBackend, AuthScheme 枚举 + ProviderConfig 数据类 |
| `api/client.py` | 完全重写: httpx 直连 + 后端分发 + 每请求覆盖 |
| `api/__init__.py` | 新增 ApiBackend, AuthScheme, ProviderConfig 导出 |
| `auth/__init__.py` | 新增 load_settings_credentials, load_config, save_config, generate_config 导出 |
| `cli/main.py` | 完全重写: 五级认证解析 + --api-key/--provider 参数 + config set/show/clear 子命令 |
| `tests/conftest.py` | 新增工厂 + evil-code config 文件 fixtures |
| `pyproject.toml` | 移除 openai 依赖 |

### 新增测试（共 83 个，含认证解析 + config store）

| 文件 | 测试数 | 覆盖范围 |
|:---|:---|:---|
| `tests/test_api/test_sse.py` | 6 | SSE 解析: data, DONE, event, 多行, 注释, 无效 JSON |
| `tests/test_auth/test_settings_store.py` | 7 | settings.json: 未找到, 无效, AUTH_TOKEN, API_KEY, 优先级, 无, 额外键 |
| `tests/test_api/test_backends.py` | 22 | ChatCompletions (7), Messages (8), Responses (7) |
| `tests/test_api/test_client.py` | 6（重写） | ChatClient: 缺少密钥, 401, 非200, 流式, 覆盖, 关闭 |
| `tests/test_api/test_auth_resolution.py` | 25 | 认证解析: 后端推断 + 五级优先级链 |
| `tests/test_auth/test_config_store.py` | 17 | config store: 读/写/生成/roundtrip/tempfile + clear + 冲突 |

### 设计决策

- httpx 替代 openai SDK: 不锁定 SDK，完全控制 wire format
- Backend Protocol（结构化类型）: 各后端实现 build_request + parse_stream
- 共享 SSE 解析器: 后端只解析 JSON 内容，不处理原始字节
- 五级认证解析: CLI --api-key > config.yaml > env > settings.json > OAuth
- evil-code 原生配置: ~/.evil-code/config.yaml — 自动登录，无需 CLI 参数
- cc-switch 联动: 读取 settings.json env 块 = cc-switch 用户零配置
- 自动后端检测: base_url 含 "anthropic" 或 "deepseek" → Messages 后端
- DeepSeek /anthropic 端点说 Anthropic Messages API，非 OpenAI Chat Completions

---

## 模块 11: 配置工具 + TUI [完成]

> 创建: 2026-07-25 | 详见: progress/modules/module11_zh.md

### 动机

用户反馈: "正常使用应该有一段代码接受各种参数，然后生成配置文件"和"我想要直接测试 agent，配置 settings 免登录，然后直接能使用简易的 UI 去操作，可以问问题，有对话框"。测试方案过于简陋——手动编辑 JSON 不是正式流程。需要: 配置工具 + 交互界面。

### 变更内容

- **config/settings_writer.py**: 参数→settings.json 写入器（cc-switch 等价）。save/show/clear 原子写入，合并保留非 env 键，ConfigSummary NamedTuple。
- **tui/app.py**: Textual TUI — EvilCodeApp 头部栏（model+backend）, RichLog 流式对话, 输入栏, 工具调用标记, 错误显示。
- **cli/main.py**: _run_config_command（config set/show/clear）, --tui 标志路由。
- **Unicode 修复**: ✓/✗/— → ASCII（config 输出, Windows GBK 控制台兼容）。

### 新增文件

| 文件 | 用途 |
|:---|:---|
| `config/__init__.py` | 包导出 |
| `config/settings_writer.py` | save/show/clear settings.json（cc-switch 等价） |
| `tui/__init__.py` | 包导出 |
| `tui/app.py` | EvilCodeApp Textual TUI |
| `tests/test_config/__init__.py` | 测试包 |
| `tests/test_config/test_settings_writer.py` | 17 测试 |

### 新增测试（共 17 个）

| 测试文件 | 测试数 | 关键用例 |
|:---|:---|:---|
| `tests/test_config/test_settings_writer.py` | 17 | save (5), show (4), clear (4), _mask_key (4) |

总测试数: 140 → 157（全部通过）。

---

## 依赖 (Phase 1)

```
pip install httpx pydantic markdownify  # 已移除 openai SDK
pip install -e ".[tui]"  # textual 用于 TUI 模式
# 开发:
pip install pytest pytest-asyncio pytest-httpx
```
