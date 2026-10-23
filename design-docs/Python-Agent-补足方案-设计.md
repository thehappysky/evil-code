# Python Agent 补足方案（Remediation Plan）设计文档

> 创建: 2026-08-04
> 状态: **阶段 0-7 全部实施 ✅（补足方案完成）**
> 参考: `Grok-Build-LLM缺陷防御体系深度分析.md` + `D:\Pycharm_py\gork_build\grok-build`（Rust 参考实现）
>
> **⚠️ Phase A 更新（2026-08-06）**: 本文中引用的 `cli/main.py::_xxx` 函数已迁移到独立模块:
> - `_build_hooks`/`_build_tools`/`_build_agent`/`_init_phase2_modules` 等 → `cli/assembly.py`（公开名,去下划线前缀）
> - `_infer_backend`/`_resolve_provider_config`/`_build_resilient_client` 等 → `cli/auth.py`
> - `_run_headless`/`_run_interactive` → `cli/execution.py`
> - argparse 定义 → `cli/args.py`
>
> 详见 `progress/phase5.md` "Phase A 上帝文件拆分" 章节。

---

## 背景

对 code_agent 与"基础/生产级 Agent"做了全面对比（功能盘点 + 数据流分析），确认：
- **主干架构正确**：think→act→observe 循环、多 Provider 协议抽象、AgentHook 开放封闭、上下文完整性自愈，均与主流 Agent 同构。
- **缺口集中在"循环之外的生态"**：① 无子代理；② 工具广度不足；③ 上下文管理精度低；④ 非交互权限默认放行；⑤ 无重试/熔断/401 恢复；⑥ usage 记账缺失；⑦ 一批"建好未接"的半成品；⑧ 工具串行执行。
- **TUI 是照搬适配的**，存在"有 UI 无后端"的隐性缺口。
- **外围功能多为"只做未测"**，需要测试先行再接线。

本文档给出分阶段补足方案，每项标注 Rust 参考依据与 Python 落点。**阶段 0 已实施**，其余为规划。

---

## 阶段 0 · 止血 + 接线（✅ 已完成 2026-08-04）

### 0.1 权限 fail-closed（安全，最高优先）

**问题**：`hooks/phase4_hooks.py` 中 `Decision.ASK` 被静默降级为 `HookDecision.ALLOW`（headless 默认放行未授权工具）。
**Rust 对照**：Grok Build 全链路 fail-closed —— `PromptPolicy::Deny`（dontAsk）在"本应弹窗"的工上前一律 `PolicyDeny`（`xai-grok-workspace/src/permission/manager.rs:1517-1525`）；弹窗投递失败也转 `Reject`（`manager.rs:1597-1601`）。
**实施**：
- `permission/manager.py`：
  - 新增 `prompt_policy: "ask"|"deny"`（默认 `ask`），`set_prompt_policy()`。
  - `decide()` 管道对齐 Rust：deny 规则 → YOLO → allow 规则 → 安全命令白名单 → **读工具预放行**（`file_read/file_search/web_fetch`，对应 Rust layer-7 Read/Grep/WebSearch）→ `prompt_policy=="deny"` 时未授权 → `DENY`（fail-closed）→ 默认 `ASK`。
- `hooks/phase4_hooks.py`：
  - `PermissionHook` 新增 `ask_action: "deny"|"prompt"|"allow"`（默认 `deny`）。
  - `ASK` → `deny`（默认，headless 拒绝）/ `prompt`（REPL 控制台确认，`asyncio.to_thread(input)`）/ `allow`（兼容位）。
- `cli/main.py::_build_hooks`：headless（有 prompt）→ `ask_action="deny"`；REPL（无 prompt）→ `ask_action="prompt"`。TUI 仍排除此 hook（走 `SessionApprovalBroker`）。
- 测试：决策矩阵 + fail-closed 断言（`tests/test_permission/`、`tests/test_hooks_phase4/`）。

### 0.2 接线清单（8 项"建好未连"）

| 功能 | 之前 | 实施 |
|---|---|---|
| Skills 可用列表 | 只注册 skill 工具，`format_available_skills()` 未注入 | `_async_main` 在 `load_all()` 后 `session.add_user_message(format_available_skills())` |
| `--plan` | flag 定义但从未读取 | `_async_main` 中 `if args.plan: plan_tracker.activate()` |
| `--sandbox` | flag 定义但 shell_exec 不引用 | `_build_tools(sandbox=True)` 注册 DockerSandbox 包装的 `shell_exec_sandbox` |
| MCP `--mcp-server` | `register_mcp_tools` 无调用点 | 新增 flag（`NAME=CMD|URL`），`_async_main` 连接 `McpClient` → `register_mcp_tools`（`_build_tools` 支持 `finalize=False` 便于锁前注册）→ 收尾 close |
| 插件工具 | `main.py:363` `_ = load_tools(manifest)` 丢弃 | 收集进 `plugin_tools`，`_build_tools` 注册为 `{plugin}__{fn}`（宽松输入模型 `PluginToolArgs`，支持 `__input_model__` 严格 schema） |
| 插件 Hook | `main.py:371` 裸 `pass` | 按名约定注册：`_register_plugin_hook()` 把 `pre_tool_use` 等命名函数接入 `HookDispatcher` |
| Telemetry | `TelemetryLogger` 无调用点 | 新增 `TelemetryHook`（session_create/turn_start/turn_complete），注册在 PersistenceHook 之后 |
| TUI trace | `InstrumentedAgent(trace=None)` | 传入全局 `trace` |

### 0.3 TUI 高危修复（照搬带来的隐性缺口）

| 问题 | 修复 |
|---|---|
| `/clear` 只清屏不清会话 | `Session.reset()`（保留 system，清空历史/计数）；`action_clear_transcript` 同步重置会话 + `_context_used_tokens` |
| `/rename`/`/status` 新建会话必报错 | `TuiAgentRunner.session_id` 优先读 live `session._session_id`（PersistenceHook 首轮生成），回退绑定时的值 |
| 计划模式死局（模型被告知写 plan.md 但写工具全被拦） | `PlanModeHook.on_tool_call` 放行对 plan 文件的写入；新增 `exit_plan_mode` 工具（`_build_tools(plan_tracker=...)`）让模型可提交计划并退出 |
| 模式持久化错位（重启后 UI 显示 plan/auto 实际不生效） | `InteractionModeController._apply_mode_to_backends()` 抽出，`set_interaction_mode` 对 no-op（`changed=False`）也应用后端状态 |

---

## 阶段 1 · 测试先行（✅ 已完成 2026-08-04）

给每个外围模块补隔离测试，建立门禁：`pytest tests/test_<module>/ -q` 全绿才可接线。解决"AI 发现未测不敢接入"的流程问题。
**实施**：
- 新增 `tests/test_cli/test_plugin_hooks.py`：`_hook_event_name_for`（含 `on_` 前缀剥离、未知名返回 None）+ `_register_plugin_hook`（async/sync handler、dict 归一化、异常降级、未知名不注册）。
- 新增 `tests/test_telemetry/test_hook.py`：`TelemetryHook` 的 session_create / turn_start / turn_complete 事件、日志落盘、失败不崩溃。

## 阶段 2 · 上下文与记账（✅ 已完成 2026-08-04）

### 2.1 真实 usage 记账

三个后端已在 `turn_end.usage` 携带真实 token，但 `agent.py` 忽略。实施：
- `api/backends/base.py`：新增 `normalize_usage()`，把 OpenAI 风格（`prompt/completion_tokens` + `*_details.cached/reasoning`）与 Anthropic 风格（`input/output_tokens` + `cache_*`）统一为 `prompt_tokens / completion_tokens / cached_prompt_tokens / reasoning_tokens`。
- 三后端 `parse_stream` 的 `turn_end.usage` 改用 `normalize_usage`。
- `core/session.py`：新增 `UsageTotals` dataclass + `Session.record_usage(usage, model)` + `usage_totals` 属性（按模型拆分 `by_model`）；`reset()` 清零。与 `estimated_tokens`（bytes/4 启发式，仍做压缩门控）并存。
- `core/agent.py`：`turn_end` 分支 `if event.usage: session.record_usage(event.usage, self._client.model)`。
- `api/client.py`：`ChatClient.model` 属性。
- `cli/headless.py`：JSON / streaming-json 的 `usage` 字段用真实数字（`_session_usage()`），去掉硬编码 0。
- 测试：`Session.record_usage` 矩阵、`Agent` 记录 usage、`normalize_usage` 三形态、headless 输出。

### 2.2 分阶段压缩（snap 安全边界）

对齐 `xai-grok-compaction` 的 `snap_to_safe_boundary`（`select.rs`）：`count_preserved_turns` 的切点若落在**孤儿 tool result** 上（其 assistant 被压缩掉），模型 API 会因未配对的 `tool` 消息返回 400。实施：
- `compaction/assembler.py`：新增 `_snap_to_safe_boundary()`，切点落在 `tool` 消息时向后回退到其 owner assistant；`count_preserved_turns` 在预算不足时也保证窗口不以裸 tool 开头（允许略微超预算，与 Rust 一致）。
- 测试：孤儿 tool result 场景、`nothing fits` 边界（无 crash）。

## 阶段 3 · 弹性层（✅ 已完成 2026-08-04）

新增 `api/resilience.py`（纯标准库，零依赖），对齐 `xai-circuit-breaker` + `xai-grok-auth`：

- **`CircuitBreaker`**（同步类，`check`/`record` 无 await，可在流式循环任意位置调用）：三态 CLOSED/OPEN/HALF_OPEN + `SlidingWindow` 错误率采样 + `BreakerOpen(retry_after)`；`BreakerConfig` 带 `client`/`server` 预设；时钟可注入（`clock` 参数）便于确定性测试。
- **`RetryPolicy`**：HTTP 状态分类 `RETRYABLE / AUTH_REFRESH / TERMINAL`，`server`/`client_storage` 预设（client 多重试 408）。
- **`RetryConfig`**：指数退避 + jitter + `retry-after` header 尊重（`delay_for(attempt, retry_after)`）。
- **`RefreshGate`**（async）：并发 401 刷新去重（在途刷新共享同一 future，`asyncio.shield`），对齐 Rust OnceCell 去重。

**接线**：
- `api/client.py`：`ChatClient` 新增可选注入 `breaker/retry/retry_cfg/refresh_cb/refresh_gate`（默认 None → 完全兼容旧行为）。`chat_stream` 重写为 retry 循环：顶部 breaker 门 → 401 走 RefreshGate 刷新重试一次 → retryable（5xx/429/408）退避重试 → 200 正常流式 → 网络异常退避重试。
- `cli/main.py`：新增 `_build_resilient_client()` + `_oauth_refresh_cb()`（运行时 401 用 OAuth refresh_token 自愈），headless/REPL/TUI 三处 `ChatClient` 都改用 resilient client。
- `compaction/summarizer.py`：瞬时错误重试前加退避（`_RETRY_BACKOFF`）。

**测试**：`tests/test_api/test_resilience.py`（25 个）——熔断状态机（fake clock）、滑动窗口、RetryPolicy 分类、退避、RefreshGate 去重、ChatClient 集成（401 刷新重试/5xx 退避重试/4xx 不重试/breaker fail-fast/成功计分）。

## 阶段 4 · 工具面扩展（✅ 已完成 2026-08-04）

- **`file_glob` 工具**（`tools/builtin/file_glob.py`）：模型探索代码库的第一需求（目录/文件列举）。输入 `{pattern, path}`；首选 `rg --files --hidden --glob=!.git/** --glob <pattern>`（**rg 自动带 gitignore 语义 + 跳过 .git**），回退纯 Python `pathlib.rglob` + fnmatch（`**` 折叠为 `*`）；结果按 mtime 降序、上限 100、超限附截断页脚；路径输出统一正斜杠（跨平台）。
- **`web_search` 工具**（`tools/builtin/web_search.py`）：直连 Responses API `tools:[{type:"web_search"}]`，解析 `output_text` + `url_citation` 去重保序为 Sources。**仅当 provider backend 为 RESPONSES 时注册**（避免默认 DeepSeek 等不支持 Responses 的 provider 暴露一个必然失败的工具）。
- **接线**：`_build_tools` 恒注册 `file_glob`；`web_search_config` 参数在 backend==RESPONSES 时注册 `web_search`。headless/REPL/TUI 两处 `_build_tools` 调用点都传配置。
- **测试**：`test_file_glob.py`（Python 回退路径、跳过 .git、排序、截断、错误路径）+ `test_web_search.py`（成功/域名过滤/citation 去重/无结果/HTTP 错误，httpx mock）+ `test_cli_discrete`（7 内置工具、web_search 仅 RESPONSES 注册）。

## 阶段 5 · 编排层（✅ 已完成 2026-08-04）

### 5.1 并行工具执行

`core/agent.py`：单轮多个 tool_call 从串行 `for` 改为 **`asyncio.gather(return_exceptions=True)`**（对齐 Rust `FuturesUnordered`），结果按模型输出顺序落盘。**写工具（`file_edit`/`file_write`）同路径用 `asyncio.Lock` 串行**（`_tool_lock_key` 归一化目标路径，防同文件 read-modify-write TOCTOU），不同路径完全并发。TUI 的 `InstrumentedAgent._execute_one_tool` 覆盖同步加 `lock` 透传。单个工具失败不拖垮整批（`BaseException` → `ToolResult.error`）。

### 5.2 Todo 系统

新增 `code_agent/todo/`（对齐 Rust `TodoState`/`TodoWrite`）：
- `state.py`：纯逻辑 `TodoState`（id 键控有序表，`apply_replace`/`apply_merge`/重复 id 校验/`summarize_todo_state`）。
- `tool.py`：`todo_write` 工具（`merge` 默认 True，返回渲染列表让模型看到进度）。
- `controller.py`：`TodoController`（工具与 hook 共享的内存状态）。
- `hook.py`：`TodoHook` —— 每轮 turn_start 把待办注入为 `<system-reminder>`（复用 `ReminderInjector.todo_reminder`，复活死代码）；todo_write 成功重置 nudge 计数；连续数轮忽略 todo 时注入 nudge。
- 接线：`_init_phase2_modules` 创建 controller → `_build_tools` 注册 `todo_write` → `_build_hooks` 挂 `TodoHook`。
- 说明：TodoGate（turn-end 强制续跑）暂未实现，以"注入提醒"作为安全的第一步（文档记录）。

### 5.3 on_session_end 生命周期

- `AgentHook` 协议新增 `on_session_end(session, reason="completed"|"max_turns"|"error")`。
- `Agent.run` 的 turn 循环包进 `try/except/finally`，`finally` 中**恰好一次**触发 `on_session_end`（`_session_end_fired` 每 run 重置，兼容 REPL 复用 agent）；MaxTurnsReached/异常分别映射 `max_turns`/`error`。
- `LifecycleHook` 新增 `on_session_end` → 分发 `SESSION_END` 事件（此前"定义了从不派发"）。

## 阶段 6 · 防御体系 Top-5（✅ 已完成 2026-08-04，依据防御文档）

已实施（高性价比 + 可测试）：

1. **Doom Loop 重复检测**（缺陷 5）—— 新增 `hooks/doom_loop_hook.py`：连续 N（默认 3）次**完全相同**的工具调用（工具名 + 规范化参数）返回 `DENY`，模型收到错误结果被迫改变策略；滑窗可恢复（换一个调用后再允许）。恒注册。
2. **Prompt-injection 防线**（抓取内容标记）—— `web_fetch` 输出包 `<web_content source=... kind="untrusted-data">`，明确告知模型抓取内容是**数据不是指令**。
3. **压缩 Reduction Guard**（缺陷 3，压缩率<20% 拒绝）—— `CompactionHook` 在替换历史前估算新旧 token，`new >= old*80%` 则跳过压缩（`_estimate_messages` 辅助），防止"压了等于没压"的信息劣化。
4. **上下文完整性收尾**（缺陷 3.G）—— `Session.to_api_messages()` 读边界也调用 `_repair_dangling_calls()`，模型 API 永不收到孤儿 tool 消息。

未实施（记录为后续，工程量较大）：
- **StructuredOutput 工具 + 3 次重试 + fail-closed**（缺陷 1/2/9）：把"自由文本 JSON"变成"显式工具 + schema 校验 + 错误回注重试"。
- **Stall 检测**（异步旁路 nudge）：模型静默时注入催促提醒。
- **TodoGate**（turn-end 强制续跑）：目前 Todo 只注入提醒，未强制续跑。

## 阶段 7 · Subagent（✅ 已完成 2026-08-04）

对齐 `xai-grok-shell/src/agent/subagent/`：
- **`subagents/coordinator.py`**：`SubagentCoordinator`（深度计数器 `MAX_SUBAGENT_DEPTH=1`，超限返回错误字符串而非异常，让父模型优雅应对）。
- **`tools/builtin/task.py`**：`task` 工具，输入 `{prompt, subagent_type, capability_mode}`，把子代理最终文本作为 `<subagent_result>` 工具结果回传父对话。
- **上下文隔离**：子代理**零父上下文**（新 Session = 子系统提示词 + task prompt 唯一一条 user 消息，对齐 Rust `ContextSource::New`）。
- **双重深度保护**：子代理工具集按 `capability_mode`（read-only/read-write/execute）过滤，**且不含 task 工具**（结构上无法递归）+ 深度计数器兜底。
- **接线**：`_build_tools(subagent_factory=...)` 注册 task 工具；`_build_subagent_factory(args, config)` 构建子代理（resilient client + capability 过滤工具 + DoomLoopHook，子代理结束后关闭 client）。
- 未实现（记录）：后台运行/轮询、`<background_context>` fork 折叠上下文、role/persona 解析 —— 后续可加。

---

## 提示词集中（2026-08-05）

新建 `code_agent/prompts.py`，集中管理全部系统提示词。**提示词正文从"新 agent（Evil）"的独立身份出发撰写，不含任何参考 agent（grok-build/Grok/xAI）字眼** —— 参考内容只用于工程上的措辞参考，绝不出现在模型可见文本或身份命名里。昵称统一 **Evil**，工具名映射 code_agent 内置工具（file_read/file_search/file_glob/file_edit/file_write/shell_exec/web_fetch/task/todo_write）。

| 提示词 | 来源 | 说明 |
|:---|:---|:---|
| `MAIN_SYSTEM_PROMPT` | grok-build `prompt.md` | `<action_safety>`/`<tool_calling>`/`<output_efficiency>`/`<formatting>` 四块逐字保留 + `<task_completion_discipline>` 追加 |
| `COMPACT_SYSTEM_PROMPT` | grok-build `COMPACT_SYSTEM_PROMPT` | 压缩后精简提示词 |
| `SUBAGENT_PROMPTS`（general-purpose/explore/plan） | grok-build `task.rs` | 三角色逐字，`${{tools.by_kind.*}}` → code_agent 工具 |
| `SUMMARIZE_SYSTEM_PROMPT` | grok-build `compaction_developer_prompt.txt` | 摘要器提示词，Evil 身份 |
| `PLAN_MODE_INSTRUCTIONS` / `PLAN_MODE_EXIT_REMINDER` | grok-build `EnterPlanMode` output | 计划模式 5 步指令 |
| `INTERJECTION_TEMPLATE` / `TODO_GATE_REMINDER` | grok-build 运行期提醒 | 中途插话 / 待办门提醒（备用） |

**接线**：`cli/main.py`（主 + 子代理）、`compaction/summarizer.py`、`core/plan_mode.py` 全部改为引用 `prompts.py`。
**测试**：`tests/test_prompts/` 断言各提示词已接线、Evil 身份、关键块存在（语义有效性无法断言，接线可断言）。

## 项目规则自动发现（2026-08-05）

补最初对比标为缺失的"CLAUDE.md/AGENTS.md 项目规则自动发现"（2026 事实标准）：
- **`code_agent/rules.py`**：`discover_project_rules(cwd)` 读 `AGENTS.md`（优先）或 `CLAUDE.md`，返回规则文本；`format_project_context()` 包装成 `<project-context>` 块。
- **`hooks/project_rules_hook.py`**：`ProjectRulesHook` 在 `on_session_start` 注入一次（与 MemoryHook 同通道）。
- **接线**：`_build_hooks` 恒注册。
- **测试**：`tests/test_rules/`（发现/优先级/空文件/hook 注入/真实 Agent 循环注入）。

## 功能有效性测试（2026-08-05）

设计文档 `design-docs/测试方案-功能有效性.md`：每个功能区分"实现性测试"（代码存在）与"有效性测试"（真实循环中可观察行为）。新增 `tests/test_effectiveness/`（9 个）：
- 权限 fail-closed 在真实 Agent 循环拦截写工具（文件未创建 + is_error）
- 并行工具真并发（时序 < 串行阈值）
- Todo 端到端（todo_write → 下一轮提醒注入）
- Subagent 端到端（父 session 出现 subagent_result）
- DoomLoop 端到端（第 4 次相同调用被拒）
- headless JSON 真实 usage
- --sandbox 委托 DockerSandbox
- skills 注入 / --plan 激活（抽函数）

## 追加：全链路审计修复（2026-08-05）

对"设计文档 + 进度文档 + 实际代码"做全链路审计后，发现并修复 4 个真实缺口：

1. **跨会话记忆只有读、没有写**（最严重）——`MemoryHook` 搜索的索引从未被运行时填充。新增 `hooks/memory_write_hook.py`：`MemoryWriteHook` 在 `on_session_end` 把会话写入 per-workspace session log 并 reindex（source=session/workspace/global），`_build_hooks` 接线。
2. **压缩后持久化写错文件**——`replace_history()` 后 `PersistenceHook` 仍按差量 append，产生"旧全量 + 压缩摘要"混合历史。改为 `_history_replaced` 标记 + `save_full_history()` 全量重写（含 on_session_end 异常终止路径）。
3. **headless 权限误伤**——`file_glob`/`web_search` 不在读工具预放行；`exit_plan_mode`/`todo_write`/`skill` 无预放行位，导致 headless `--plan` 无法退出。已补齐；`task` 保持默认拒绝（防子代理成本失控）。
4. **编辑文件提醒恒为空**——`CompactionHook` 读不存在的 `_edited_files` 属性。改为注入 `EditTrackerHook`，压缩后 reminder 真实包含已编辑文件。

测试：新增 18 个（记忆写路径单元 + 跨会话端到端、压缩持久化重写、权限矩阵、装配断言、headless 预放行端到端）。全量 **980 passed，覆盖率 72.29%**。

## 设计要点回顾

- **先测后接**：每个模块独立补测试，全绿才进主干（阶段 1 门禁）。
- **fail-closed 优先**：非交互模式未授权工具一律 DENY（Rust dontAsk 语义），安全是底线。
- **开放封闭为主**：绝大多数能力通过 AgentHook / 工具注册表扩展。阶段 5 是唯一的 `core/agent.py` 主循环改动（并行工具执行 + `on_session_end` finally），已实现并全量测试通过。
