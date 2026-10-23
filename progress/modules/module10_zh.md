# 模块 10: 多 Provider 架构补强

> 阶段: 1+ | 状态: 已完成 | 创建: 2026-07-24

---

## 范围

| 功能 | 描述 |
|:---|:---|
| api/sse.py | 共享 SSE 流解析器: iter_sse_events() — data 行, event 标记, [DONE] 终止符, 多行 data, 注释跳过 |
| api/backends/base.py | Backend Protocol（结构化类型）+ BackendRequest 数据类 (url, headers, body) |
| api/backends/chat_completions.py | OpenAI /chat/completions 后端: build_request (消息透传, Bearer/XApiKey 认证) + parse_stream (delta.content, delta.tool_calls, finish_reason) |
| api/backends/messages.py | Anthropic /messages 后端: _convert_messages (system 提取, tool→user tool_result, tool_calls→tool_use) + _convert_tools (function→input_schema) + parse_stream (content_block_start/delta, message_delta) |
| api/backends/responses.py | OpenAI /responses 后端: _convert_messages (input items) + store=false (ZDR) + max_output_tokens + parse_stream (output_text.delta, function_call_arguments.delta) |
| api/backends/__init__.py | 工厂: get_backend(ApiBackend) -> Backend（单例分发） |
| auth/settings_store.py | 读取 ~/.claude/settings.json env 块: ANTHROPIC_AUTH_TOKEN (Bearer) / ANTHROPIC_API_KEY (XApiKey) + BASE_URL + MODEL |
| api/schemas.py (修改) | 新增 ApiBackend, AuthScheme 枚举 + ProviderConfig 数据类 |
| api/client.py (重写) | httpx.AsyncClient 直连, 通过 get_backend() 后端分发, 通过 dataclasses.replace() 每请求覆盖 |
| cli/main.py (重写) | 五级认证解析: --api-key > config.yaml > env > settings.json > OAuth; config set/show/clear 子命令; --provider 参数 |
| pyproject.toml (修改) | 移除 openai>=1.30.0 依赖 |

---

## 最终架构

```
code_agent/api/
├── __init__.py                # 导出: StreamEvent, ToolCall, ApiBackend, AuthScheme, ProviderConfig
├── client.py                  # ChatClient: httpx 直连, 无 openai SDK
├── schemas.py                 # + ApiBackend 枚举, AuthScheme 枚举, ProviderConfig 数据类
├── sse.py                     # iter_sse_events(response) -> AsyncIterator[dict]
└── backends/
    ├── __init__.py            # get_backend(ApiBackend) -> Backend（单例）
    ├── base.py                # Backend Protocol + BackendRequest(url, headers, body)
    ├── chat_completions.py    # /chat/completions — OpenAI 格式
    ├── messages.py            # /messages — Anthropic 格式（DeepSeek 兼容）
    └── responses.py           # /responses — OpenAI Responses API

code_agent/auth/
├── __init__.py                # + load_settings_credentials 导出
├── token_refresh.py           # 未变更
├── token_store.py             # 未变更
└── settings_store.py          # load_settings_credentials() -> SettingsCredentials
```

---

## 实现状态

| 项目 | 状态 | 文件 |
|:---|:---|:---|
| iter_sse_events: data 行解析 + JSON 解码 | 完成 | `api/sse.py` |
| iter_sse_events: [DONE] 终止符 | 完成 | `api/sse.py` |
| iter_sse_events: event: 类型标记 → _event 键 | 完成 | `api/sse.py` |
| iter_sse_events: 多行 data 拼接 | 完成 | `api/sse.py` |
| iter_sse_events: 注释 (:) 跳过 | 完成 | `api/sse.py` |
| iter_sse_events: 无效 JSON 跳过（不崩溃） | 完成 | `api/sse.py` |
| Backend Protocol: build_request + parse_stream | 完成 | `api/backends/base.py` |
| BackendRequest 数据类 (url, headers, body) | 完成 | `api/backends/base.py` |
| ChatCompletions: build_request (url, Bearer/XApiKey 头) | 完成 | `api/backends/chat_completions.py` |
| ChatCompletions: build_request (messages, tools, stream_options) | 完成 | `api/backends/chat_completions.py` |
| ChatCompletions: parse_stream (text_delta, tool_call_delta, turn_end) | 完成 | `api/backends/chat_completions.py` |
| Messages: _convert_messages (system 提取, tool→user tool_result) | 完成 | `api/backends/messages.py` |
| Messages: _convert_messages (tool_calls → tool_use 块, arguments JSON→input) | 完成 | `api/backends/messages.py` |
| Messages: _flush_tool_results (clear 前 list 拷贝 — bug 修复) | 完成 | `api/backends/messages.py` |
| Messages: _convert_tools (function → input_schema) | 完成 | `api/backends/messages.py` |
| Messages: parse_stream (content_block_start, input_json_delta, message_delta) | 完成 | `api/backends/messages.py` |
| Messages: block_to_tool 映射 (block index ≠ tool index) | 完成 | `api/backends/messages.py` |
| Responses: _convert_messages (input items 格式) | 完成 | `api/backends/responses.py` |
| Responses: store=false (ZDR 合规) | 完成 | `api/backends/responses.py` |
| Responses: max_output_tokens (非 max_tokens) | 完成 | `api/backends/responses.py` |
| Responses: parse_stream (output_text.delta, function_call_arguments.delta) | 完成 | `api/backends/responses.py` |
| Responses: output_index → tool_index 映射 | 完成 | `api/backends/responses.py` |
| get_backend 工厂 (单例字典) | 完成 | `api/backends/__init__.py` |
| load_settings_credentials (AUTH_TOKEN Bearer 优先) | 完成 | `auth/settings_store.py` |
| load_settings_credentials (API_KEY XApiKey 回退) | 完成 | `auth/settings_store.py` |
| load_settings_credentials (文件未找到 → is_present=False) | 完成 | `auth/settings_store.py` |
| ProviderConfig 数据类 (7 字段带默认值) | 完成 | `api/schemas.py` |
| ApiBackend 枚举 (3 值) | 完成 | `api/schemas.py` |
| AuthScheme 枚举 (2 值) | 完成 | `api/schemas.py` |
| ChatClient.__init__ (config: ProviderConfig) | 完成 | `api/client.py` |
| ChatClient.chat_stream (backend.build_request → httpx.stream → iter_sse → backend.parse_stream) | 完成 | `api/client.py` |
| ChatClient.chat_stream (每请求覆盖 via dataclasses.replace) | 完成 | `api/client.py` |
| ChatClient: HTTP 401 → AuthenticationError | 完成 | `api/client.py` |
| ChatClient: 非200 → error StreamEvent | 完成 | `api/client.py` |
| ChatClient.close (http_client.aclose) | 完成 | `api/client.py` |
| cli: _resolve_provider_config (四级链) | 完成 | `cli/main.py` |
| cli: _infer_backend (auto: anthropic/deepseek → Messages, 否则 ChatCompletions) | 完成 | `cli/main.py` |
| cli: _infer_auth_scheme (Messages + api.anthropic.com → XApiKey) | 完成 | `cli/main.py` |
| cli: _resolve_oauth_config (异步: load → 过期检查 → refresh → save) | 完成 | `cli/main.py` |
| cli: --api-key 参数（最高优先级认证） | 完成 | `cli/main.py` |
| cli: --provider 参数 (auto/chat_completions/messages/responses) | 完成 | `cli/main.py` |
| pyproject.toml: 移除 openai 依赖 | 完成 | `pyproject.toml` |

**进度: 38/38 项完成**

---

## 设计决策

- **httpx 替代 openai SDK**: openai SDK 锁定 OpenAI 的 wire format。用 httpx 直连 + Backend Protocol，每个 provider 的格式由专用后端模块处理。新增 provider = 新增后端文件，不修改核心代码。
- **结构化类型 (Protocol)**: Python 的 `Protocol` 提供结构化子类型 — 后端不需要继承基类，只需实现正确的方法。比 ABC 更轻量、更灵活。
- **共享 SSE 解析器**: `iter_sse_events()` 一次处理 SSE wire format（data 行, event 标记, [DONE]）。后端只解析 JSON payload — 不处理原始字节。消除重复的 SSE 解析逻辑。
- **五级认证解析**: CLI --api-key（一级）显式覆盖。config.yaml（二级）evil-code 原生免登录。环境变量（三级）用于 CI/CD。settings.json（四级）用于 cc-switch 桌面用户。OAuth（五级）用于 Claude OAuth 用户。
- **cc-switch 联动**: cc-switch Tauri 写 ~/.claude/settings.json（ANTHROPIC_AUTH_TOKEN/API_KEY + BASE_URL + MODEL）。evil-code 通过 `load_settings_credentials()` 读同一文件 — 零配置集成，无需额外设置。
- **自动后端检测**: 当 --provider 为 "auto"（默认），CLI 检查 base_url。URL 含 "anthropic" 或 "deepseek"（带 /anthropic 路径）→ Messages 后端。其他 → ChatCompletions。这正确处理了 DeepSeek /anthropic 端点 — 它说 Anthropic Messages API，非 OpenAI Chat Completions。
- **Messages 后端复杂度**: Anthropic Messages API 的消息格式与 OpenAI 根本不同。system 消息变成顶层字段。tool 结果变成 user 消息的 tool_result 内容块。assistant 消息中的 tool_calls 变成 tool_use 内容块，arguments 是 JSON 字符串（流式传输为 input_json_delta 片段）。流式传输中的 block index ≠ tool index（text 块和 tool_use 块交错），需要 block_to_tool 映射。
- **Responses 后端 store=false**: Responses API 有 `store` 参数。设为 false 确保 ZDR（零数据留存）合规 — 服务器不持久化对话数据。
- **list(pending) bug 修复**: Messages 后端的 `_flush_tool_results()` 中，直接将 `pending` 作为 content 追加后调用 `pending.clear()` 会清空同一引用。通过 `list(pending)` 拷贝后再 clear 修复。
- **_infer_backend_from_url bug 修复**: 原始 `_infer_backend(args)` 对所有认证级别都用 `args.base_url`（argparse 默认: `https://api.anthropic.com/v1`）。对于二级（环境变量）和三级（settings.json），实际 base_url 来自 env/settings，不是 argparse。这意味着 settings.json 里写 `ANTHROPIC_BASE_URL=https://api.openai.com/v1` 仍然会推断成 Messages（因为默认 URL 含 "anthropic"）。修复方式：提取 `_infer_backend_from_url(base_url, provider)` 纯函数，在二级和三级用实际解析出的 base_url 重新推断。`_infer_auth_scheme_from_url()` 同理。

---

## 测试覆盖

| 测试文件 | 测试数 | 关键用例 |
|:---|:---|:---|
| `tests/test_api/test_sse.py` | 6 | 简单 data, [DONE], event 标记, 多行, 注释跳过, 无效 JSON 跳过 |
| `tests/test_auth/test_settings_store.py` | 7 | 未找到, 无效 JSON, AUTH_TOKEN Bearer, API_KEY XApiKey, 两者都有(优先级), 无, 额外键 |
| `tests/test_api/test_backends.py` | 22 | ChatCompletions 构建 (4) + 解析 (3), Messages 构建 (6) + 解析 (2), Responses 构建 (5) + 解析 (2) |
| `tests/test_api/test_client.py` | 6 | 缺少密钥, 401→AuthError, 非200→错误事件, 成功流式, 每请求覆盖, 关闭清理 |
| `tests/test_api/test_auth_resolution.py` | 25 | 后端推断 (8), 认证方案 (4), 一级 CLI (4), 二级 env (3), 三级 settings (3), 优先级链 (3) |
| **新增测试总计** | **66** | (34 净新增 + 7 重写客户端 + 25 认证解析) |

全部 140 测试通过 (81 原始 - 7 旧客户端 + 6 新客户端 + 6 SSE + 7 settings + 22 backends + 25 认证解析 = 140)。
