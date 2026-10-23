# Project Progress Summary

> 最后更新: 2026-08-06 | Python AI Coding Agent (evil-code)
> 上一会话完成内容: **Phase B 收敛 deferred import 完成(89→15, -83%, 0 循环依赖, 280 顶层边可见)**
> 更早: Phase A 上帝文件拆分 + file_delete 工具 + Phase 5 补足方案 0-7 + 提示词集中(Evil) + 有效性测试 + 项目规则发现 + 全链路审计修复

---

## 项目总览

一个 Python AI Coding Agent，架构参考 Rust Grok Build，实现 think → act → observe 循环。

- **项目路径**: `D:\Pycharm_py\evil-code`
- **Python 版本**: 3.12.6
- **虚拟环境**: `.venv`
- **包名**: `code_agent`
- **入口**: `code-agent` (通过 `pyproject.toml` console_scripts)
- **设计文档**: `design-docs/DEV-INDEX.md`
- **进度文档**: `progress/`

---

## 当前实现阶段

| Phase | 状态 | 测试数 |
|:---|:---|:---|
| Phase 1: 最小可行 Agent | ✅ 完成 | 157 |
| Phase 2: 会话 + 记忆 + 压缩 | ✅ 完成 | 148 |
| Phase 3: TUI + Skills + Plan Mode | ✅ 完成 | 63 |
| Phase 4: 权限 + MCP + 插件 + 遥测 | ✅ 完成 | 89 (新增) |
| **Phase 5: 补足方案 0-7 + 提示词(Evil) + 有效性测试 + 项目规则** | **✅ 全部完成 (2026-08-05)** | **992 全部通过 (覆盖率 72.4%, 门槛 60%)** |
| **Phase A: 上帝文件拆分** | **✅ 完成 (2026-08-06)** | **1039 passed (main.py -73%, tui.py -46%)** |
| **Phase B: 收敛 deferred import** | **✅ 完成 (2026-08-06)** | **1039 passed (89→15 deferred, -83%)** |
| **总计** | **4/4 Phase + Phase5 + Phase A/B 完成** | **1039 passed** |

> 设计文档: `design-docs/DEV-INDEX.md` ｜ 进度: `progress/phase5.md`（含 Phase A 详细记录 + Phase B-H 路线图）

---

## 完整包结构

```
code_agent/
├── __init__.py                    # version
├── auth/                          # 认证层
│   ├── token_refresh.py           # OAuth2 刷新
│   ├── token_store.py             # ~/.claude/.credentials.json 读写
│   ├── settings_store.py          # ~/.claude/settings.json 读取
│   ├── config_store.py            # ~/.evil-code/config.yaml 读写
│   └── vault.py                   # [Phase 2] 多账户保险库
├── config/
│   ├── __init__.py
│   └── settings_writer.py         # settings.json 写入器 (cc-switch 兼容)
├── api/                           # LLM 通信层
│   ├── client.py                  # ChatClient: httpx + Backend Protocol 分发
│   ├── schemas.py                 # StreamEvent, ProviderConfig, ApiBackend 等
│   ├── sse.py                     # 共享 SSE 解析器
│   └── backends/
│       ├── base.py                # Backend Protocol
│       ├── chat_completions.py    # OpenAI /chat/completions
│       ├── messages.py            # Anthropic /messages (DeepSeek 兼容)
│       └── responses.py           # OpenAI /responses
├── core/                          # Agent 核心
│   ├── agent.py                   # think→act→observe 主循环 + AgentHook Protocol
│   ├── session.py                 # 会话状态 + replace_history + from_dicts
│   ├── messages.py                # System/User/Assistant/ToolResult tagged union
│   ├── exceptions.py              # AgentError, MaxTurnsReached 等
│   └── reminders.py               # [Phase 2] System-reminder 注入器
├── tools/                         # 工具系统
│   ├── registry.py                # 装饰器注册 + finalize + Pydantic 验证
│   ├── definition.py              # ToolDefinition
│   ├── result.py                  # ToolResult
│   └── builtin/
│       ├── file_read.py           # 读文件 (行号 + 截断)
│       ├── file_search.py         # 代码搜索 (rg + Python re fallback)
│       ├── shell_exec.py          # Shell 执行 (timeout + kill)
│       ├── file_edit.py           # search_replace (唯一匹配 + atomic)
│       ├── file_write.py          # 写文件 (atomic + diff)
│       ├── file_delete.py         # 删文件/目录 (宿主侧 + 审批, 修复沙箱只读)
│       └── web_fetch.py           # URL 获取 (SSRF 3层防护 + HTML→MD)
├── hooks/                         # [Phase 2] AgentHook 实现
│   └── phase2_hooks.py            # MemoryHook, CompactionHook, PersistenceHook, EditTrackerHook
├── storage/                       # [Phase 2] 会话持久化
│   ├── session_store.py           # SessionStore + DeltaIterator (JSONL)
│   ├── file_lock.py               # 跨平台文件锁 (msvcrt/fcntl)
│   └── models.py                  # SessionMeta
├── memory/                        # [Phase 2] 跨会话记忆
│   ├── index_db.py                # MemoryIndex (SQLite + FTS5 + LIKE fallback)
│   ├── chunker.py                 # Markdown ## 标题分块
│   ├── search.py                  # 混合搜索管道 (FTS5 → filter → weight → truncate)
│   ├── file_store.py              # MEMORY.md 读写 + 会话日志
│   └── schema.sql                 # DDL 参考
├── compaction/                    # [Phase 2] 对话压缩
│   ├── policy.py                  # CompactionPolicy (token 阈值触发)
│   ├── summarizer.py              # generate_summary (LLM 摘要 + retry)
│   └── assembler.py               # assemble_compacted_history
├── cli/
│   ├── main.py                    # 入口 + 子命令分发 + _async_main + _run_tui (564行,原2099行)
│   ├── args.py                    # [Phase A] argparse parser 定义 (build_parser)
│   ├── auth.py                    # [Phase A] 5级认证链 + OAuth 刷新 + resilient client
│   ├── assembly.py                # [Phase A] 工具/Hook/Agent/phase2 装配 (14个函数)
│   ├── execution.py               # [Phase A] headless/interactive/list_sessions 执行模式
│   ├── headless.py                # [Phase 3] JSON/streaming-json output formats
│   └── subcommands/               # [Phase A] CLI 子命令
│       ├── config.py              # config set/show/clear
│       ├── sandbox.py             # sandbox list/gc/protect/remove
│       └── draft.py               # draft create/list/status/merge/discard
├── helpers/
│   ├── token_counter.py           # bytes/4 token 估算
│   ├── text_truncate.py           # 文本截断工具
│   └── file_io.py                 # atomic_write
├── skills/                        # [Phase 3] Skills system
│   ├── __init__.py
│   ├── models.py                  # SkillInfo Pydantic model
│   ├── parser.py                  # SKILL.md YAML parser
│   ├── discovery.py               # SKILL.md file scanner
│   └── manager.py                 # SkillManager + SkillTool
└── tui/
    ├── __init__.py                 # launcher + exports
    ├── tui.py                     # EvilCodeApp 主类 (2583行,原4812行)
    ├── domain.py                  # [Phase A] 值类型 + 9 Protocol + 数据类 (纯领域,无IO)
    ├── adapters.py                # InstrumentedAgent/TuiAgentRunner/TuiController
    ├── text.py                    # UI 文本本地化 (en/zh-CN)
    ├── stores.py                  # UI 偏好 + provider 设置持久化
    ├── widgets/
    │   ├── __init__.py
    │   ├── conversation.py        # [Phase A] ConversationMessage/ToolFeedbackMessage/AssistantMarkdown
    │   └── suggester.py           # [Phase A] SlashCommandSuggester
    ├── screens/
    │   ├── __init__.py
    │   ├── settings.py            # [Phase A] SettingsScreen/LanguageSettingsScreen
    │   ├── provider.py            # [Phase A] ProviderSettingsScreen/ProviderSetupApp/ProviderSelectionScreen
    │   ├── approval.py            # [Phase A] PermissionApprovalScreen
    │   ├── reasoning.py           # [Phase A] ReasoningEffortScreen
    │   └── session.py             # [Phase A] SessionSelectionScreen
    └── rendering/
        ├── __init__.py
        └── tool_feedback.py       # [Phase A] ToolFeedbackRenderer (工具反馈渲染,524行)
```

---

## Phase 1 成果（已完成）

- **6 个内置工具**: file_read, file_search, shell_exec, file_edit, file_write, web_fetch
  - *(2026-08-06 新增 file_delete → 现 7 个内置工具)*
- **多 Provider 支持**: 3 后端 (ChatCompletions / Messages / Responses)
- **5 级认证链**: CLI → config.yaml → env → settings.json → OAuth
- **TUI 模式**: `--tui` 启动 Textual 全屏界面
- **配置工具**: `code-agent config set/show/clear`
- **157 个测试**

## Phase 2 成果（刚完成）

| 模块 | 关键交付 | 文件数 |
|:---|:---|:---|
| **storage/** | SessionStore (JSONL CRUD + Delta Replay), FileLock (跨平台), SessionMeta | 4 |
| **memory/** | MemoryIndex (FTS5 + LIKE fallback), chunker (H2分块), search_memory, MemoryStore | 6 |
| **compaction/** | CompactionPolicy, generate_summary (LLM摘要), assemble_compacted_history | 4 |
| **core/reminders.py** | ReminderInjector (8种提醒: date/todo/memory/compaction/interrupt) | 1 |
| **hooks/** | MemoryHook, CompactionHook, PersistenceHook, EditTrackerHook | 2 |
| **auth/vault.py** | AccountVault (多账户 CRUD + switch_to + rotation) | 1 |
| **Session 扩展** | replace_history(), from_dicts() | (修改 session.py) |
| **CLI 集成** | --resume, --continue, --no-memory, --no-compaction, --no-persist, /sessions | (修改 main.py) |
| **测试** | 148 个新测试 (storage/memory/compaction/reminders/hooks/vault) | 8 |

---

## Phase 3 成果（刚完成）

| 模块 | 关键交付 | 文件数 |
|:---|:---|:---|
| **skills/** | SkillInfo model, SKILL.md parser (YAML + line-based recovery), discover_skills(), SkillManager + SkillTool | 5 |
| **core/plan_mode.py** | PlanState enum, PlanModeTracker FSM (5-state) → PlanModeHook | 1 |
| **hooks/phase3_hooks.py** | PlanModeHook (on_turn_start inject, on_tool_call DENY write tools, on_turn_end exit) | 1 |
| **cli/headless.py** | run_headless_plain / run_headless_json / run_headless_streaming | 1 |
| **tui/rendering/** | render_markdown (rich.Markdown), highlight_code + detect_language (Pygments) | 2 |
| **tui/theme.py** | Theme manager (light/dark/auto + OS detection) | 1 |
| **tui/widgets/** | ScrollbackWidget, PromptWidget, DiffWidget, ThinkingBlock, TodoPanel, StatusBar | 6 |
| **tui/screens/** | ChatScreen (composes all widgets + agent streaming integration) | 1 |
| **CLI 集成** | --output-format, --no-skills, --plan, SkillTool registration, PlanModeHook in pipeline | (修改 main.py) |
| **测试** | 63 个新测试 (skills models/parser/discovery/manager + plan_mode FSM + TUI rendering) | 4 |

### Phase 3 新增文件（19 个）

| 文件 | 行数 | 模块 |
|:---|:---|:---|
| `skills/__init__.py` | 12 | Skills |
| `skills/models.py` | 34 | Skills |
| `skills/parser.py` | 155 | Skills |
| `skills/discovery.py` | 133 | Skills |
| `skills/manager.py` | 228 | Skills |
| `core/plan_mode.py` | 203 | Plan Mode |
| `hooks/phase3_hooks.py` | 115 | Hook |
| `cli/headless.py` | 140 | CLI |
| `tui/theme.py` | 170 | TUI |
| `tui/rendering/__init__.py` | 5 | TUI |
| `tui/rendering/markdown.py` | 30 | TUI |
| `tui/rendering/syntax_highlight.py` | 128 | TUI |
| `tui/widgets/__init__.py` | 15 | TUI |
| `tui/widgets/scrollback.py` | 140 | TUI |
| `tui/widgets/prompt_input.py` | 127 | TUI |
| `tui/widgets/diff_view.py` | 85 | TUI |
| `tui/widgets/thinking_block.py` | 80 | TUI |
| `tui/widgets/todo_panel.py` | 74 | TUI |
| `tui/widgets/status_bar.py` | 99 | TUI |

## Phase 4 成果（刚完成）

| 模块 | 关键交付 | 文件数 |
|:---|:---|:---|
| **permission/** | SafeCmd whitelist (80+), glob rule matcher, 3-layer pipeline | 4 |
| **hooks/phase4_hooks.py** | PermissionHook, LifecycleHook | 1 |
| **hooks/dispatcher.py** | HookDispatcher (7 events), HookResult | 1 |
| **mcp/** | StdioTransport, HttpTransport, McpClient, register_mcp_tools | 4 |
| **plugins/** | PluginLoader, PluginManifest, load_tools/load_hooks | 3 |
| **telemetry/** | TelemetryLogger (privacy-safe), UsageTracker | 3 |
| **sandbox/** | DockerSandbox (read-only mount, network=none, mem/cpu limits) | 2 |
| **CLI 集成** | --yolo, --allow, --deny, --sandbox, --no-plugins | (修改 2 文件) |
| **测试** | 89 个 Phase 4 测试 | 6 |

---

## 项目完成！4/4 Phase + Phase 5 阶段 0

全部 Phase 已完成。Python AI Coding Agent 具备完整能力:
- 6+1 内置工具 + MCP 外部工具热插拔（`--mcp-server` 已接线）
- 5 级认证链 + 多账户保险库
- 会话持久化 + 跨会话记忆(FTS5) + 对话压缩
- Skills 系统(SKILL.md，可用列表已注入) + Plan Mode 状态机（headless `--plan` 已接线，`exit_plan_mode` 工具）
- 3 层权限管线(YOLO→SafeCmd→Ask) + Plugin 插件（工具/Hook 已接线）
- TUI(Textual) + REPL + Headless(JSON/NDJSON) 三种模式
- Docker 沙箱（`--sandbox` 已接线）+ 结构化遥测日志（TelemetryHook 已接线）
- **真实 usage 记账**（headless JSON 输出真实 token/成本结构；`Session.usage_totals` 按模型拆分）
- **弹性层**（熔断器 + 退避重试 + 401 OAuth 刷新自愈，`api/resilience.py`）
- **glob + web_search 工具**（模型可列举目录、可搜索网络）
- **并行工具执行**（asyncio.gather + 同路径锁串行写）+ **Todo 系统**（todo_write 工具 + TodoHook 提醒）+ **on_session_end**（生命周期完整）
- **防御体系**（Doom Loop 检测、web_fetch 注入标记、压缩 Reduction Guard、dangling 读边界修复）
- **Subagent**（task 工具 + SubagentCoordinator + capability 过滤 + 零父上下文隔离）
- **932 个测试全部通过（覆盖率 71.8%, 门槛 60%）**

### file_delete 工具 — 2026-08-06

**问题**：用户说"删掉脚本"时，agent 只能用 `shell_exec rm`。`rm` 被分类器归入 `clean` 档（fail-safe，reason=`unclassified_risky`），而 clean 档工作区只读挂载（`:ro`），`rm` 静默失败——权限层显示"已允许"但文件没删掉。

**修复**：新增 `file_delete` 工具（`tools/builtin/file_delete.py`），走与 `file_write` 相同的宿主侧审批路径：
- 不加入任何权限预放行集合 → 自动落到 `ASK`（交互审批）/ `DENY`（headless fail-closed），**权限层零改动**。
- 安全守卫：拒绝系统/凭证路径（`~/.ssh`、`/etc/` 等，归一化分隔符跨平台匹配）、拒绝 workspace 根 / 驱动器根。
- 支持文件与目录（`rmtree`），符号链接走 `unlink`（不误删链接目标），断链可删。
- 接线：`cli/main.py` 注册 + 加入 `_READ_WRITE_TOOLS`（read-write/execute 子代理可用，read-only 不可用）。
- 提示词：`prompts.py` 工具集列表 + `<tool_calling>` 明确"删文件用 file_delete，勿用 shell_exec rm"。

测试：`tests/test_tools/test_file_delete.py`（14 个：文件/目录/符号链接/安全守卫/权限接线），更新 `test_cli_discrete.py` 工具数（7→8）。

### Phase A: 上帝文件拆分 — 2026-08-06 ✅

**问题**：`cli/main.py`（2099行、32函数、60个 deferred import）和 `tui/tui.py`（4812行、28类）是上帝文件，职责混杂、不可维护。

**实施**：拆分为 15 个新文件，main.py 564行(-73%)，tui.py 2583行(-46%)。

| 侧 | 新文件 | 内容 |
|:---|:---|:---|
| CLI | `cli/auth.py` (228行) | 5级认证链 + OAuth刷新 + resilient client |
| CLI | `cli/args.py` (135行) | argparse parser 定义 |
| CLI | `cli/assembly.py` (889行) | 14个装配函数(build_tools/hooks/agent/phase2) |
| CLI | `cli/execution.py` (156行) | headless/interactive/list_sessions |
| CLI | `cli/subcommands/{config,sandbox,draft}.py` | 3个子命令 |
| TUI | `tui/domain.py`（扩展） | 4个数据类 + 9个Protocol去重 |
| TUI | `tui/widgets/{conversation,suggester}.py` | 4个UI组件 |
| TUI | `tui/screens/{settings,provider,approval,reasoning,session}.py` | 8个ModalScreen |
| TUI | `tui/rendering/tool_feedback.py` (524行) | ToolFeedbackRenderer(10个渲染方法) |

测试：**1039 passed**（仅2个预先存在的Windows MAX_PATH bug失败，与重构无关）。详见 `progress/phase5.md`。

### Phase B: 收敛 deferred import — 2026-08-06 ✅

**目标**：把函数内 deferred import 提到模块顶部，暴露真实依赖图，消除循环依赖。

**成果**：89→15 deferred import（74 个提前，83% 减少）。0 循环依赖。280 条顶层依赖边可见。

| 步骤 | 内容 | 结果 |
|:---|:---|:---|
| B1 | 审计 deferred import | 89 个,分布在 14 个模块(assembly 44, hooks 17, main 11, execution 5, mcp 3, 其他 9) |
| B2 | 分类 | 77 个可提前(无循环依赖),12 个保留(main.py 启动速度: TUI/MCP/subcommands/assembly) |
| B3 | 提前非循环 import | assembly.py 44个 + hooks 17个 + execution 5个 + mcp 3个 + 小模块 9个 = 78 个提前(含 main.py 调整) |
| B4 | 消除循环依赖 | AST DFS 验证: 0 循环依赖。无需 Protocol/合并/提取 |
| B5 | 依赖图验证 | 89 模块, 280 条顶层边。Top 3 被依赖: tools.result(19), api.schemas(14), core.session(11) |
| B6 | 测试验证 | 1039 passed(与 Phase B 前一致), 2 pre-existing Windows MAX_PATH failures |

**保留的 15 个 deferred import**（均在 main.py，为启动速度）：
- `_run_tui()`: TUI 4 个(adapters/domain/stores/tui) + assembly 4 个 + Session 1 个 + DraftManager 1 个
- `_async_main()`: assembly 1 个 + execution 1 个
- `main()`: subcommands 3 个

**Mock 修复**：8 个测试的 monkeypatch 目标从源模块改为导入模块（hoisting 后标准做法）：
- `test_cli_discrete.py`: `code_agent.cli.assembly.get_data_dir` → `code_agent.cli.execution.get_data_dir`
- `test_phase2_hooks.py`: `code_agent.compaction.summarizer.generate_summary` → `code_agent.hooks.phase2_hooks.generate_summary`
- `test_mcp/test_client.py`: `code_agent.mcp.transport.{Stdio,Http}Transport` → `code_agent.mcp.client.{Stdio,Http}Transport`

---

### 架构优化路线图（Phase B-H）

| Phase | 目标 | 状态 |
|:---|:---|:---|
| **A** | 上帝文件拆分 | ✅ 完成 |
| **B** | 收敛 deferred import，暴露真实依赖图 | ✅ 完成 |
| **C** | 引入 Port 抽象 + 组合根（bootstrap/composition.py） | 📋 规划中 |
| **D** | 分层重组 + AST 护栏（domain/application/infrastructure） | 📋 规划中 |
| **E** | 错误处理治理（83个 bare except → 失败关闭） | 📋 规划中 |
| **F** | 事件流重构（Hook → 不可变事件流） | 📋 规划中 |
| **G** | 安全加固（TOCTOU + SyntheticReason + 沙箱分类器） | 📋 规划中 |
| **H** | SQLite 会话替换 JSONL（修 Windows MAX_PATH bug） | 📋 规划中 |

### 全链路审计修复 — 2026-08-05

针对功能审计发现的 4 个"建好未连/误伤"问题完成修复：

| 项 | 内容 |
|---|---|
| **记忆写入接通** | 新增 `MemoryWriteHook`（`hooks/memory_write_hook.py`）：`on_session_end` 写入 session log 并 reindex 到 SQLite/FTS5（session/workspace/global 三种 source）。此前只有 MemoryHook 搜索，索引为空，记忆实际失效。实现中再修复 reindex 窗口遗漏：每次运行精确索引本次日志，保证所有会话入库 |
| **压缩后持久化** | `Session.replace_history()` 置 `_history_replaced` 标记；`PersistenceHook` 检测后 `save_full_history()` 全量重写 JSONL，不再把压缩历史 append 到旧全量后面 |
| **headless 权限预放行** | `file_glob`/`web_search` 加入读工具预放行；`todo_write`/`exit_plan_mode`/`skill` 加入控制工具预放行（修复 headless `--plan` 无法退出）；`task` 仍默认拒绝 |
| **编辑文件提醒** | `CompactionHook` 接入 `EditTrackerHook`，压缩后 `<system-reminder>` 真实包含已编辑文件（此前恒为 None） |

测试：**992 passed（+30，相对 962），覆盖率 72.41%**（门槛 60%）。验证方案见 `design-docs/测试方案-修复验证.md`，详见 `progress/phase5.md` 追加章节。

### Phase 5（补足方案）阶段 0 — 2026-08-04

针对"基础 Agent 对比"发现的缺口，参考 Grok Build Rust 实现做了第一批补足：

| 项 | 内容 |
|:---|:---|
| **权限 fail-closed** | headless 未授权工具默认 DENY（对齐 Rust `PromptPolicy::Deny`）；读工具预放行；REPL 用控制台确认 |
| **8 项接线** | skills 列表注入、`--plan`、`--sandbox`、`--mcp-server`、插件工具/Hook 注册、TelemetryHook、TUI trace |
| **TUI 高危修复** | `/clear` 真重置（`Session.reset()`）、`/rename` 新会话可用、plan 文件写入放行 + `exit_plan_mode`、模式持久化错位 |

> 详见 `progress/phase5.md` 与 `design-docs/Python-Agent-补足方案-设计.md`。阶段 1-7 规划中。

---

## 快速启动命令

```bash
cd D:\Pycharm_py\evil-code
pip install -e ".[dev,tui]"

# 配置 API key
code-agent config set --api-key sk-xxx --base-url https://api.deepseek.com/anthropic --model deepseek-v4-pro

# 运行模式
code-agent                    # REPL 交互模式
code-agent "prompt"           # 单次 headless
code-agent --tui              # Textual TUI
code-agent --continue         # 恢复上次会话
code-agent --resume <id>      # 恢复指定会话
code-agent --no-memory --no-compaction  # 禁用 Phase 2 特性

# 测试
python -m pytest tests/ -q    # 457 测试
```

---

## 重要设计文件

| 文件 | 用途 |
|:---|:---|
| `design-docs/DEV-INDEX.md` | 设计文档索引 |
| `design-docs/Python-Agent-MVP-实现路线.md` | 4-Phase 总路线图 |
| `design-docs/Python-Agent-MVP-Phase3-详细设计.md` | Phase 3 详细设计 |
| `design-docs/Python-Agent-MVP-Phase4-详细设计.md` | Phase 4 详细设计 |
| `progress/PROGRESS-SUMMARY.md` | 本文件 — 进度总览 |
| `progress/phase1.md` | Phase 1 详细进度 |
| `progress/phase2.md` | Phase 2 详细进度 |
| `progress/modules/module1.md` ~ `module19.md` | 各模块详细进度 |
