# Phase 5：补足方案（Remediation）

> 最后更新: 2026-08-04
> 设计文档: `design-docs/Python-Agent-补足方案-设计.md`
> 参考实现: `D:\Pycharm_py\gork_build\grok-build`（Rust）

---

## 阶段总览

| 阶段 | 内容 | 状态 |
|:---|:---|:---|
| 0 | 权限 fail-closed + 8 项接线 + TUI 高危修复 | ✅ 完成 (2026-08-04) |
| 1 | 外围模块补测试（门禁） | ✅ 完成 (2026-08-04) |
| 2 | 真实 usage 记账 + 压缩安全边界 | ✅ 完成 (2026-08-04) |
| 3 | 熔断/重试/401 恢复 | ✅ 完成 (2026-08-04) |
| 4 | glob + web_search 工具 | ✅ 完成 (2026-08-04) |
| 5 | 并行工具 + Todo + on_session_end | ✅ 完成 (2026-08-04) |
| 6 | 防御体系（Doom Loop / 注入标记 / Reduction Guard / dangling 读修复） | ✅ 完成 (2026-08-04) |
| 7 | Subagent（task 工具 + 协调器 + capability 过滤） | ✅ 完成 (2026-08-04) |

> **补足方案 0-7 全部完成。**

---

## 阶段 0 已实施内容（2026-08-04）

### 0.1 权限 fail-closed

- `permission/manager.py`：
  - 新增 `prompt_policy`（`ask`/`deny`）+ `set_prompt_policy()`。
  - `decide()` 增加"读工具预放行"（`file_read/file_search/web_fetch`）与"`prompt_policy=deny` → DENY"（fail-closed），对齐 Rust `PromptPolicy::Deny`。
- `hooks/phase4_hooks.py`：`PermissionHook` 新增 `ask_action`（`deny` 默认 / `prompt` REPL 确认 / `allow` 兼容），ASK 不再默认放行。
- `cli/main.py::_build_hooks`：headless → `ask_action="deny"`；REPL → `ask_action="prompt"`。
- 测试：`tests/test_permission/test_manager.py`（prompt_policy 矩阵、读工具预放行）、`tests/test_hooks_phase4/test_phase4_hooks.py`（fail-closed、prompt、allow 兼容）。

### 0.2 接线清单（8 项）

| 项 | 落点 |
|:---|:---|
| Skills 列表注入 | `cli/main.py::_async_main` `session.add_user_message(format_available_skills())` |
| `--plan` | `_async_main` `if args.plan: plan_tracker.activate()` |
| `--sandbox` | `_build_tools(sandbox=True)` → `shell_exec_sandbox`（DockerSandbox） |
| MCP `--mcp-server` | 新增 flag（`NAME=CMD|URL`）+ `_async_main` 连接 + `register_mcp_tools`（`_build_tools(finalize=False)` 支持） |
| 插件工具 | `plugin_tools` 收集 → `_build_tools` 注册 `{plugin}__{fn}`（`PluginToolArgs` 宽松模型，支持 `__input_model__`） |
| 插件 Hook | `_register_plugin_hook()` 按名约定接入 `HookDispatcher` |
| Telemetry | 新增 `TelemetryHook`（session_create/turn_start/turn_complete） |
| TUI trace | `InstrumentedAgent(trace=trace)` |

### 0.3 TUI 高危修复

- `core/session.py`：新增 `Session.reset()`。
- `tui/tui.py`：`action_clear_transcript` 同步重置会话 + 上下文计数。
- `tui/adapters.py`：
  - `TuiAgentRunner.session_id` 读 live `_session_id`（修 `/rename`、`/status`）。
  - `InteractionModeController._apply_mode_to_backends()`（修模式持久化错位）。
- `hooks/phase3_hooks.py`：`PlanModeHook` 放行 plan 文件写入（修计划模式死局）。
- `cli/main.py`：新增 `exit_plan_mode` 工具（`_build_tools(plan_tracker=...)`）。

### 阶段 0 测试

- 新增测试：权限矩阵、插件工具注册、sandbox 包装、`finalize=False`、plan 文件写入、`Session.reset`、`InteractionModeController` no-op。
- 全量：**835 passed，覆盖率 69.7%**（>60% 门槛）。

---

## 阶段 1 已实施内容（2026-08-04）

- 新增 `tests/test_cli/test_plugin_hooks.py`：插件 hook 命名注册助手（`_hook_event_name_for` / `_register_plugin_hook`）。
- 新增 `tests/test_telemetry/test_hook.py`：`TelemetryHook` 生命周期事件落盘。

## 阶段 2 已实施内容（2026-08-04）

### 2.1 真实 usage 记账

| 文件 | 改动 |
|:---|:---|
| `api/backends/base.py` | 新增 `normalize_usage()`（OpenAI/Anthropic 两种 wire 形态 → 统一字段） |
| `api/backends/{messages,chat_completions,responses}.py` | `turn_end.usage` 改用 `normalize_usage` |
| `core/session.py` | 新增 `UsageTotals` + `Session.record_usage(usage, model)` + `usage_totals`；`reset()` 清零 |
| `core/agent.py` | `turn_end` 分支记录 `event.usage` → session ledger |
| `api/client.py` | `ChatClient.model` 属性 |
| `cli/headless.py` | JSON / streaming-json 用真实 usage（`_session_usage()`） |

### 2.2 压缩安全边界（snap）

- `compaction/assembler.py`：新增 `_snap_to_safe_boundary()`，切点落在 `tool` 消息时回退到 owner assistant（防孤儿 tool result / API 400，对齐 Rust `snap_to_safe_boundary`）。

### 阶段 1-2 测试

- 新增：插件 hook 助手、TelemetryHook、usage 记账矩阵、Agent 记录 usage、`normalize_usage` 三形态、压缩 snap。
- 全量：**859 passed，覆盖率 70.3%**（>60% 门槛）。

---

## 阶段 3 已实施内容（2026-08-04）

| 文件 | 改动 |
|:---|:---|
| `api/resilience.py` | 新增：`CircuitBreaker`（三态+滑动窗口+注入时钟）、`RetryPolicy`（状态分类）、`RetryConfig`（退避+jitter+retry-after）、`RefreshGate`（并发刷新去重） |
| `api/client.py` | `ChatClient` 可选注入弹性；`chat_stream` 重写为 retry 循环（breaker 门/401 刷新重试/退避重试/网络异常重试），默认 None 兼容旧行为 |
| `cli/main.py` | 新增 `_build_resilient_client()` + `_oauth_refresh_cb()`；headless/REPL/TUI 三处 ChatClient 均启用 |
| `compaction/summarizer.py` | 瞬时错误重试前加退避 |

### 阶段 3 测试

- 新增 `tests/test_api/test_resilience.py`（25 个）：熔断状态机、滑动窗口、RetryPolicy、退避、RefreshGate 去重、ChatClient 集成（401 刷新/5xx 退避/4xx 不重试/breaker fail-fast）。
- 全量：**884 passed，覆盖率 70.8%**（>60% 门槛）。

---

## 阶段 4 已实施内容（2026-08-04）

| 文件 | 改动 |
|:---|:---|
| `tools/builtin/file_glob.py` | 新增：glob 列举工具（rg --files + gitignore 语义 / Python fnmatch 回退；mtime 排序；上限 100；路径统一正斜杠） |
| `tools/builtin/web_search.py` | 新增：web_search（Responses API，url_citation 去重；仅 RESPONSES provider 注册） |
| `cli/main.py` | `_build_tools` 恒注册 `file_glob`；`web_search_config` 参数（backend==RESPONSES 时注册 `web_search`）；两处调用点传配置 |

### 阶段 4 测试

- 新增 `tests/test_tools/test_file_glob.py` + `test_web_search.py`；`test_cli_discrete` 更新为 7 内置工具 + web_search 条件注册断言。
- 全量：**898 passed，覆盖率 71.0%**（>60% 门槛）。

---

## 阶段 5 已实施内容（2026-08-04）

| 文件 | 改动 |
|:---|:---|
| `core/agent.py` | 并行工具执行（`asyncio.gather` + per-path `asyncio.Lock`，写工具同路径串行，结果保序）；`_tool_lock_key` 辅助；`on_session_end` finally（reason=completed/max_turns/error，每次 run 恰好一次） |
| `todo/`（新增 5 文件） | `TodoState`（state.py）/ `todo_write` 工具（tool.py）/ `TodoController`（controller.py）/ `TodoHook`（hook.py）/ 包导出 |
| `hooks/phase4_hooks.py` | `LifecycleHook.on_session_end` → 分发 `SESSION_END`（此前从不派发） |
| `tui/adapters.py` | `InstrumentedAgent._execute_one_tool` 加 `lock` 透传 |
| `cli/main.py` | `_init_phase2_modules` 建 TodoController；`_build_tools` 注册 `todo_write`；`_build_hooks` 挂 `TodoHook` |

### 阶段 5 测试

- 新增 `tests/test_todo/test_todo.py`（state/tool/hook 11 个）+ `test_agent.py` 并行工具（顺序、同路径串行、锁映射）+ on_session_end（completed/max_turns/每次 run 一次）+ `test_phase4_hooks` SESSION_END 分发。
- 全量：**916 passed，覆盖率 71.3%**（>60% 门槛）。

---

## 阶段 6 已实施内容（2026-08-04）

| 文件 | 改动 |
|:---|:---|
| `hooks/doom_loop_hook.py` | 新增：DoomLoopHook（连续 N 次相同工具调用 → DENY，滑窗可恢复） |
| `tools/builtin/web_fetch.py` | 输出包 `<web_content ... untrusted-data>` 标记（注入防线） |
| `hooks/phase2_hooks.py` | CompactionHook 加 Reduction Guard（<20% 拒绝）+ `_estimate_messages` 辅助 |
| `core/session.py` | `to_api_messages()` 读边界 `_repair_dangling_calls()` |
| `cli/main.py` | `_build_hooks` 恒注册 DoomLoopHook |

### 阶段 6 测试

- 新增 `tests/test_hooks/test_doom_loop.py`（重复/不同/可恢复/参数差异）+ web_fetch untrusted 标记 + Reduction Guard 两场景 + to_api_messages 读边界修复。
- 全量：**925 passed，覆盖率 71.8%**（>60% 门槛）。

---

## 阶段 7 已实施内容（2026-08-04）

| 文件 | 改动 |
|:---|:---|
| `subagents/coordinator.py` | 新增：SubagentCoordinator（深度限制 + 结果收集） |
| `subagents/__init__.py` | 新增：包导出 |
| `tools/builtin/task.py` | 新增：task 工具（TaskInput + TaskTool） |
| `cli/main.py` | `_build_tools` 加 `capability_mode`（read-only/read-write/execute 过滤）+ `subagent_factory`（注册 task 工具）；`_build_subagent_factory` 构建子代理（零父上下文 + resilient client + DoomLoopHook） |

### 阶段 7 测试

- 新增 `tests/test_subagents/test_coordinator.py`（spawn 调用/深度限制/异常/capability 过滤/task 注册/真实 Agent 集成）。
- 全量：**932 passed，覆盖率 71.8%**（>60% 门槛）。

---

## 追加：提示词集中 + 有效性测试 + 项目规则（2026-08-05）

- **`code_agent/prompts.py`**：集中全部系统提示词。**正文从新 agent（Evil）独立身份出发，不含任何参考 agent 字眼**（grok-build/xAI 仅作工程措辞参考，不出现在提示词正文/命名）。接线到 `cli/main.py`（主+子代理）、`compaction/summarizer.py`、`core/plan_mode.py`。旧身份变量 `_NEURO_SYSTEM_PROMPT` 改名 `_MAIN_SYSTEM_PROMPT`。
- **`design-docs/测试方案-功能有效性.md`**：区分实现性/有效性测试，自我审视后新增 `tests/test_effectiveness/`（9 个真实 Agent 循环测试）+ `tests/test_prompts/`（接线断言）。
- **`code_agent/rules.py` + `hooks/project_rules_hook.py`**：项目规则自动发现（AGENTS.md/CLAUDE.md），`on_session_start` 注入 `<project-context>`。
- 测试 932 → **962**，覆盖率 → **72.1%**。

## 补足方案总结

Phase 5（补足方案）0-7 全部完成：权限 fail-closed、8 项接线、TUI 修复、测试门禁、真实 usage、压缩 snap + Reduction Guard、弹性层、glob/web_search、并行工具、Todo、on_session_end、防御体系、Subagent，以及提示词集中（Evil）+ 有效性测试。测试从 835 → **952**，覆盖率 69.7% → **72.0%**。

见 `design-docs/Python-Agent-补足方案-设计.md` 对应章节。核心原则：
- 先测后接（每个模块全绿再接线）。
- fail-closed 优先（非交互未授权一律 DENY）。
- 开放封闭不变（除 `on_session_end` 外不动主循环）。

---

## 追加：全链路审计修复（2026-08-05）

基于功能有效性审计（设计文档/进度文档 + 实际代码 import/行为核对）发现并修复 4 个"建好未连 / 误伤"问题：

### 1. 跨会话记忆写入接通（此前只有读）

**问题**：`MemoryHook` 会搜索 SQLite/FTS5 索引并注入结果，但运行时没有任何地方调用 `reindex_file()` 或 `MemoryStore` 写入方法，索引始终为空，记忆功能实际失效。

**修复**：
- 新增 `hooks/memory_write_hook.py`：`MemoryWriteHook` 在 `on_session_end` 把会话渲染成 Markdown（H2 分节，符合 chunker 切分），append 到 `~/.code-agent/memory/{project_hash}/sessions/*.md`，然后 reindex 该日志（source=session）以及手工维护的 workspace/global `MEMORY.md`（source=workspace/global）。
- `cli/main.py::_build_hooks` 接线：`memory_index` + `memory_store` 同时存在时注册（`--no-memory` 自动跳过）。

### 2. 压缩后持久化全量重写

**问题**：`CompactionHook` 用 `session.replace_history()` 替换历史后，`PersistenceHook.on_turn_end` 仍按"已有消息数差量 append"，把压缩历史追加到旧全量 JSONL 后面，恢复时会读到"旧全量 + 压缩摘要"混合历史。

**修复**：
- `Session.replace_history()` 置 `_history_replaced = True`（`reset()` 清零）。
- `PersistenceHook.on_turn_end` 检测到该标记时调用 `SessionStore.save_full_history()` 全量重写；`on_session_end` 也处理该标记，保证 max_turns/error 异常终止时不丢压缩历史。

### 3. headless 权限预放行补齐

**问题**：读工具预放行名单只有 `file_read/file_search/web_fetch`，`file_glob`、`web_search` 被漏掉；`exit_plan_mode`、`todo_write`、`skill` 也未被预放行。headless fail-closed 下：`file_glob` 必然被拒，`--plan` 模式下 `exit_plan_mode` 被拒 → 计划模式再次死锁。

**修复**（`permission/manager.py`）：
- `_READ_TOOL_NAMES` 增加 `file_glob`、`web_search`。
- 新增 `_CONTROL_TOOL_NAMES = {todo_write, exit_plan_mode, skill}` 预放行（只改 Agent 自身状态或返回用户安装的技能指令，不碰工作区/外部进程）。
- `task` 仍保持默认拒绝（子代理会发起新的 LLM 调用，需要显式授权/`--yolo`）。

### 4. 编辑文件提醒接线

**问题**：`CompactionHook` 压缩后 reminder 读 `getattr(self, '_edited_files', None)`，该属性从未赋值 → 恒为 None。

**修复**：`CompactionHook` 新增 `edit_tracker` 可选参数，`cli/main.py::_build_hooks` 把 `EditTrackerHook` 传入，压缩后的 `<system-reminder>` 现在真实包含已编辑文件列表。

### 测试

- 新增 `tests/test_memory/test_memory_write_hook.py`（日志写入、索引可检索、MEMORY.md 重索引、失败降级、空会话跳过）。
- `tests/test_effectiveness/test_effectiveness.py`：跨两个真实 Agent 的"会话 1 写入 → 会话 2 MemoryHook 注入"端到端；headless 下 file_glob/exit_plan_mode 放行、task 仍拒绝。
- `tests/test_hooks/test_phase2_hooks.py`：压缩后 JSONL 全量重写（含 on_session_end 路径）、编辑文件进 reminder。
- `tests/test_permission/test_manager.py`：读工具/控制工具预放行矩阵、fail-closed 下 task 仍 DENY。
- `tests/test_core/test_session.py`：`_history_replaced` 标记语义。
- `tests/test_cli/test_build_hooks.py`：MemoryWriteHook 装配断言。

全量：**980 passed（+18），覆盖率 72.29%（门槛 60%）**。

### 测试方案落地（2026-08-05 续）

按 `design-docs/测试方案-修复验证.md` 补齐 12 个验证用例（故障注入 2、Agent 循环级 3、压力 5、编辑提醒去重 1、记忆单测 1），全量 **992 passed，覆盖率 72.41%**。

期间发现并修复**第 5 个缺陷**：`MemoryWriteHook` 原来按"文件名排序取最后 5 个日志"reindex，文件名是字典序而非时间序，新会话一旦排不进最后 5 个就永远不入库（50 个会话只入库 10 个）。修复：`append_session_log()` 返回本次日志路径，每次运行精确 reindex 当前文件（O(1) 且必然命中）；`test_stress/test_memory_scale.py` 以 `chunk 数 == 会话数` 作回归护栏。

剩余唯一步骤：§6 真机手动冒烟（记忆两轮、headless 权限、resume 核对）。

---

## 追加：项目更名 neuro-code → evil-code（2026-08-05）

项目目录名与全部品牌引用从 `neuro-code` 改为 `evil-code`：

- 目录 `D:\Pycharm_py\neuro-code` → `D:\Pycharm_py\evil-code`。
- 配置路径 `~/.neuro-code/` → `~/.evil-code/`（`config_store.py`、`helpers/tracer.py`、`cli/main.py`）。
- 类名 `NeuroCodeApp` → `EvilCodeApp`、`NeuroCodeConfig` → `EvilCodeConfig`、`_NEURO_CODE_THEME` → `_EVIL_CODE_THEME`、主题名 `neuro-code-dark` → `evil-code-dark`、MCP 默认名 `neuro-code` → `evil-code`。
- TUI 显示名 `Neuro Code` → `Evil Code`（`tui/tui.py`、`tui/text.py` 中英文）。
- `tests/`、`design-docs/`、`progress/`、`TEST-PLAN.md`、`TEST-FLOW.md`、`verify_session.py`、`.claude/settings.local.json`、`.idea/`、`.venv/Scripts/activate*` 中的路径与品牌引用同步更新。
- 保留两处有意的 `neuro` 字样：`progress/phase5.md` 中"旧身份变量 `_NEURO_SYSTEM_PROMPT`"的历史改名记录；`tests/test_prompts/test_prompts.py` 的回归守卫（断言系统提示词不含旧品牌名）。
- 包名 `code_agent` 与 CLI 命令 `code-agent` 不含 neuro，保持不变。

验证：全量 **992 passed，覆盖率 72.46%**。

---

## 追加：file_delete 工具（2026-08-06）

### 背景

真机使用中发现：用户说"帮我删掉那个脚本吧"时，agent 调 `shell_exec` 执行 `rm`，权限层显示 `sandbox_auto:clean:unclassified_`（已允许），但脚本并未被删除。

根因链路：
1. `policy.py` 分类器对 `rm` 无专门规则 → 落到规则 6 `unclassified_risky` → `clean` 档。
2. 权限层 `sandbox_auto:ALLOW` 自动放行（隔离换免审批，无审批弹窗）。
3. `manager.py` 以 `-v /workspace:ro` 只读挂载执行 `rm` → `Read-only file system`，**静默失败**。
4. 工具集里**没有 file_delete**，agent 想删文件只能走 `shell_exec rm` 这一条死路。

设计原则冲突：原则 3"绝不静默降级"被破坏——权限层放行了一个在只读沙箱里必然失败的写命令，造成"假成功"。

### 实施

新增 `code_agent/tools/builtin/file_delete.py`：

- **宿主侧审批路径**：不加入 `_READ_TOOL_NAMES` / `_CONTROL_TOOL_NAMES` 任何预放行集合 → 自动落到权限管线步骤 7 `default: ASK`（交互审批）/ 步骤 6 `DENY`（headless fail-closed）。**权限层零改动**，与 `file_write` 完全一致。
- **安全守卫**：
  - 拒绝系统/凭证路径（`~/.ssh`、`~/.gnupg`、`~/.aws`、`/etc/`、`/sys/`、`/proc/`、`/boot/`、`/dev/`、`~/.config/`）。归一化路径分隔符（`\` → `/`）+ 小写化 + 去尾斜杠，跨平台匹配（修复了 file_write 同类检查在 Windows 下因分隔符不匹配而失效的隐患）。
  - 拒绝删除 workspace 根（`path == cwd`）与驱动器/文件系统根（`path == path.parent`）。
- **文件/目录双支持**：目录走 `shutil.rmtree`，文件/符号链接走 `os.unlink`。符号链接即使指向目录也走 unlink（不误删链接目标），断链（dangling symlink）可删。
- **接线**：
  - `cli/main.py`：import + `registry.register("file_delete", ...)` + 加入 `_READ_WRITE_TOOLS`（read-write/execute 子代理可用，read-only 子代理不可用）。
  - `prompts.py`：工具集列表加 `file_delete`；`<tool_calling>` 块新增"删文件用 file_delete，勿用 shell_exec rm（rm 进只读沙箱静默失败）"。

### 测试

`tests/test_tools/test_file_delete.py`（14 个）：
- 文件删除 / 不存在报错 / 相对路径解析（3）
- 空目录 / 非空目录 rmtree / 符号链接 unlink 不误删目标 / 断链可删（4，符号链接 2 个在 Windows 无 admin 时 skip）
- 拒绝 workspace 根 / 驱动器根 / 系统/凭证路径（3）
- 权限接线：不在读工具预放行 / 不在控制工具预放行 / 在 read-write+execute 能力集 / 不在 read-only 能力集（4）

更新 `tests/test_cli/test_cli_discrete.py`：内置工具数 7→8（`test_registers_seven_builtin_tools` 集合、`test_definitions_are_exportable` 计数、`test_skill_tool_added_when_skill_manager_provided` 计数 8→9、`test_tool_categories` 加 file_delete）。

### 设计文档同步

- `design-docs/沙箱方案-v1.md` §3：宿主侧审批工具列表加 `file_delete`，并加注"删文件必须用 file_delete，不可用 shell_exec rm"。
- `design-docs/沙箱方案-v1.md` §9：已知边界新增"工作区只读 vs. 删除操作"条目。

### 未覆盖（记录）

`rm` 之外的写操作（`mv`/`cp`/`sed -i`/`mkdir`/`touch`）仍会被分类器归入 `clean` 档并在只读沙箱静默失败。当前依赖提示词引导 agent 用 file_edit/file_write/file_delete；若后续 agent 仍频繁误用 shell_exec 做写操作，可考虑在 `policy.py` 增加"工作区写命令"识别规则（方案 B/C）。

---

## 追加：Phase A 上帝文件拆分（2026-08-06）

### 背景

代码审计发现 `cli/main.py`（2099行、32函数、60个deferred import）和 `tui/tui.py`（4812行、28类）是"上帝文件"，职责混杂、不可维护。参考 neuro-code 项目的架构纪律，启动 Phase A 拆分。

### main.py 拆分（2099 → 564 行，减 73%）

| 新文件 | 行数 | 内容 |
|:---|:---|:---|
| `cli/auth.py` | 228 | 5级认证链 + OAuth刷新 + build_resilient_client + config_from_profile |
| `cli/args.py` | 135 | build_parser() argparse 定义 |
| `cli/assembly.py` | 889 | 14个装配函数：build_tools/build_hooks/build_agent/init_phase2_modules/resolve_session 等 |
| `cli/execution.py` | 156 | run_headless/run_interactive/list_sessions |
| `cli/subcommands/config.py` | ~180 | config set/show/clear |
| `cli/subcommands/sandbox.py` | ~55 | sandbox list/gc/protect/remove |
| `cli/subcommands/draft.py` | ~60 | draft create/list/status/merge/discard |
| `cli/main.py` | 564 | 入口 + 子命令分发 + _async_main + _run_tui（仅保留编排逻辑） |

**关键改进**：
- 函数公开名（去下划线前缀），测试直接从新模块导入
- 60个 deferred import 随函数迁移到各自模块
- 全局变量 `config`/`trace` 确认未被装配函数引用（已通过参数传入），仅存在于入口函数
- conftest.py mock patch 目标从 `cli.main.load_config` 迁移到 `cli.auth.load_config`
- 测试 monkeypatch 目标从 `cli.main._get_data_dir` 迁移到 `cli.assembly.get_data_dir`

### tui.py 拆分（4812 → 2583 行，减 46%）

| 新文件 | 内容 |
|:---|:---|
| `tui/domain.py`（扩展） | 吸收 4 个数据类（ProviderPreset/TranscriptEntry/ToolFeedbackState/ProviderSettingsSubmission），9 个 Protocol 去重（已在 domain.py） |
| `tui/widgets/conversation.py` | ConversationMessage/ToolFeedbackMessage/AssistantMarkdown |
| `tui/widgets/suggester.py` | SlashCommandSuggester |
| `tui/screens/settings.py` | SettingsScreen/LanguageSettingsScreen |
| `tui/screens/provider.py` | ProviderSettingsScreen/ProviderSetupApp/ProviderSelectionScreen + _EVIL_CODE_THEME |
| `tui/screens/approval.py` | PermissionApprovalScreen |
| `tui/screens/reasoning.py` | ReasoningEffortScreen + _EFFORT_COLORS |
| `tui/screens/session.py` | SessionSelectionScreen |
| `tui/rendering/tool_feedback.py`（524行） | ToolFeedbackRenderer 类（10个渲染方法从 EvilCodeApp 抽出） |

**关键改进**：
- Protocol 去重：tui.py 和 domain.py 原各定义 9 个相同 Protocol，现统一在 domain.py
- ToolFeedbackRenderer：EvilCodeApp 委托渲染，4 个深度耦合状态的编排方法保留在 App 中
- `_EVIL_CODE_THEME`/`_EFFORT_COLORS` 移到 screens/ 子模块避免循环导入，tui.py re-import

### 测试迁移

- `test_cli_discrete.py`：import 从 `cli.main` 改为 `cli.auth`/`cli.execution`/`cli.assembly`，调用点去下划线前缀
- `test_auth_resolution.py`：import 从 `cli.main` 改为 `cli.auth`
- `test_build_hooks.py`/`test_plugin_hooks.py`/`test_effectiveness.py`/`test_file_delete.py`/`test_agent.py`：import 从 `cli.main` 改为 `cli.assembly`
- `test_tui_state.py`：ProviderSettingsSubmission/ToolFeedbackState 从 `tui.tui` 改为 `tui.domain` 导入；ToolFeedbackRenderer 调用更新
- `conftest.py`：mock patch 目标迁移

### 验证

- `pytest tests/ --ignore=tests/test_effectiveness.py`：**318 passed**
- `test_effectiveness.py` 4 个失败为预先存在的 Windows MAX_PATH bug（SessionStore base64 路径超 260 字符），与本次重构无关

### Phase A 状态：✅ 完成（2026-08-06）

- main.py 2099→564行(-73%), tui.py 4812→2583行(-46%)
- 15 个新文件创建, 1039 tests passed
- 设计文档已更新: `design-docs/DEV-INDEX.md` + `design-docs/Python-Agent-补足方案-设计.md`
- 进度文档已更新: `progress/PROGRESS-SUMMARY.md`

---

## Phase B: 收敛 deferred import — ✅ 完成（2026-08-06）

### 目标

把函数内 deferred import（`def` 内部的 `from ... import`）提到模块顶部，暴露真实依赖图，消除循环依赖。

### 成果

- **89→15 deferred import**（74 个提前，83% 减少）
- **0 循环依赖**（AST DFS 验证）
- **280 条顶层依赖边**可见（之前隐藏在函数体内）
- **1039 tests passed**（与 Phase B 前一致，无新 failure）

### 实施详情

| 步骤 | 内容 | 结果 |
|:---|:---|:---|
| B1 | 审计 | 89 个 deferred import, 14 个模块。assembly.py 最多(44), hooks 次之(17) |
| B2 | 分类 | 77 个可提前(无循环依赖), 12 个保留(main.py 启动速度) |
| B3 | 提前 | assembly.py 44 + hooks 17 + execution 5 + mcp 3 + 小模块 9 = 78 个提前 |
| B4 | 循环依赖 | AST DFS: 0 循环。无需 Protocol/合并/提取 |
| B5 | 依赖图 | 89 模块, 280 边。Top 3: tools.result(19), api.schemas(14), core.session(11) |
| B6 | 测试 | 1039 passed, 2 pre-existing Windows MAX_PATH failures |

### 保留的 15 个 deferred import

均在 `main.py`，为启动速度（`code-agent --help` 不加载重模块）：
- `_run_tui()`: TUI 4 + assembly 4 + Session 1 + DraftManager 1 = 10
- `_async_main()`: assembly 1 + execution 1 = 2
- `main()`: subcommands 3

### Mock 修复（8 个测试）

Hoisting 后 monkeypatch 需指向导入模块（importing module），不是源模块（source module）：

| 测试文件 | 旧 patch 目标 | 新 patch 目标 |
|:---|:---|:---|
| test_cli_discrete.py | `code_agent.cli.assembly.get_data_dir` | `code_agent.cli.execution.get_data_dir` |
| test_phase2_hooks.py | `code_agent.compaction.summarizer.generate_summary` | `code_agent.hooks.phase2_hooks.generate_summary` |
| test_mcp/test_client.py | `code_agent.mcp.transport.StdioTransport` | `code_agent.mcp.client.StdioTransport` |
| test_mcp/test_client.py | `code_agent.mcp.transport.HttpTransport` | `code_agent.mcp.client.HttpTransport` |

---

### 架构优化路线图（Phase B-H）

| Phase | 目标 | 状态 | 前置 |
|:---|:---|:---|:---|
| **A** | 上帝文件拆分 | ✅ 完成 (2026-08-06) | — |
| **B** | 收敛 deferred import，暴露真实依赖图 | ✅ 完成 (2026-08-06) | A |
| **C** | 引入 Port 抽象 + 组合根（bootstrap/composition.py） | 📋 规划中 | B |
| **D** | 分层重组 + AST 护栏（domain/application/infrastructure） | 📋 规划中 | C |
| **E** | 错误处理治理（83个 bare except → 失败关闭） | 📋 规划中 | 独立 |
| **F** | 事件流重构（Hook → 不可变事件流） | 📋 规划中 | D |
| **G** | 安全加固（TOCTOU + SyntheticReason + 沙箱分类器） | 📋 规划中 | 独立 |
| **H** | SQLite 会话替换 JSONL（修 Windows MAX_PATH bug） | 📋 规划中 | 独立 |

