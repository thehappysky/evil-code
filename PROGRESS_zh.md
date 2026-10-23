# Code Agent - 实现进度

> 最后更新: 2026-10-23
> 状态: 核心功能已实现，进行稳定性修复；当前使用方法以 README.md 为准。
> 下方阶段叙述与架构图保留为历史记录，不代表本次已验证所有真实服务。

---

## Phase 7：沙箱体系 v1（2026-08-05）

风险 shell 命令自动进沙箱（`sandbox/policy.py` + `manager.py`）、出站白名单代理
（`sandbox/proxy.py`）、项目级 work 卷、权限自动放行联动、TUI 沙箱指示、
按重视度驱逐的生命周期 GC、子代理沙箱接入、TUI `/sandbox` 命令、
`sandbox.toml` 配置（镜像/白名单/上限）、真实卷大小驱逐、
git worktree 的 Draft 工作副本（CLI + TUI）、
自定义 sandbox profile（extends/挂载/deny/网络）、net 容器复用、
精细化挂载（/scratch tmpfs）、
`code-agent sandbox list/gc/protect/remove` 命令。已在 Docker 28.5.2 上通过
真机冒烟（clean 只读、work 卷状态保留、出站拦截、Draft 创建/合并/丢弃、
profile deny/挂载、net 复用）。详见
`design-docs/沙箱方案-v1.md` 与 `progress/phase7_zh.md`。

---

## 总体状态

| 阶段 | 名称 | 状态 | 进度 |
|:---|:---|:---|:---|
| Phase 1 | 最小可行 Agent | 完成 | 10/10 模块 |
| Phase 1+ | 多 Provider 架构补强 | 完成 | 架构补强 |
| Phase 1++ | 配置工具 + TUI | 完成 | config/ + tui/ + 17 测试 |
| Phase 2 | 会话 + 记忆 + 压缩 | 已实现 | 自动测试验证 |
| Phase 3 | TUI + Skills + Plan Mode | 已实现 | 自动测试验证 |
| Phase 4 | 完整生态 | 已实现 | 真实外部服务需单独验证 |

---

## 架构总览（当前）

```
code_agent/
├── auth/          Token 管理 + 原生配置读写 + settings.json 读取（cc-switch 联动）
├── api/           LLM HTTP 通信（httpx 直连 + Backend Protocol）
│   ├── sse.py     共享 SSE 流解析器 (iter_sse_events)
│   ├── backends/  三后端协议 (chat_completions/messages/responses)
│   ├── client.py  ChatClient (httpx 直连，移除 openai SDK)
│   └── schemas.py  StreamEvent + ProviderConfig + ApiBackend + AuthScheme
├── core/          Agent 大脑（主循环 + 会话 + 消息）
├── tools/         工具注册、调度、6 个内置工具
├── cli/           命令行入口（headless + REPL + config 子命令 + 五级认证）
├── config/        配置文件管理（config.yaml 原生 + settings.json 备选，参数→文件）
├── tui/            Textual TUI 交互界面（全屏模式，--tui 入口）
└── helpers/       纯工具函数（token 估算、截断、原子写入）
```

依赖方向: `cli -> core -> api/tools -> auth/helpers`
外部依赖: httpx, pydantic, markdownify（已移除 openai SDK）

---

## Phase 1 模块追踪

| # | 模块 | 文件 | 状态 | 验证 |
|:---|:---|:---|:---|:---|
| 1 | 项目骨架 + helpers | pyproject.toml, helpers/*.py | 完成 | 单元测试 |
| 2 | 认证层 | auth/token_refresh.py, auth/token_store.py | 完成 | 单元测试 |
| 3 | 数据模型 | core/exceptions.py, core/messages.py | 完成 | 单元测试 |
| 4 | LLM 通信 | api/schemas.py, api/client.py | 完成 | 单元测试 |
| 5 | 工具基础设施 | tools/result.py, definition.py, registry.py | 完成 | 单元测试 |
| 6 | 内置工具 (6个) | tools/builtin/*.py | 完成 | 单元测试 |
| 7 | Agent 核心 | core/session.py, core/agent.py | 完成 | 单元测试 |
| 8 | CLI 入口 | cli/main.py | 完成 | 单元测试 |
| 9 | 单元测试 | tests/ | 完成 | 81 → 140 通过 |
| 10 | 多 Provider 架构补强 | api/sse.py, api/backends/*, auth/settings_store.py | 完成 | 单元测试 |
| 11 | 配置工具 + TUI | config/settings_writer.py, tui/app.py, cli config 子命令 | 完成 | 17 测试全部通过 |

---

## 质量门禁

| 门禁 | 标准 | 状态 |
|:---|:---|:---|
| P0: 单轮工具调用 | "Read this file" 触发 file_read | 单元测试通过 (mock LLM) |
| P0: 多轮工具链 | "Find and fix bug" 触发 grep + read + bash | 单元测试通过 (mock LLM) |
| P1: Token 自动刷新 | 过期 token 触发刷新不崩溃 | 单元测试通过 (pytest-httpx) |
| P1: 优雅取消 | Ctrl+C 干净取消当前 turn | 待验证（需集成测试） |
| P1: 多 Provider SSE 解析 | iter_sse_events 处理 data/event/DONE/多行 | 单元测试通过 (6 测试) |
| P1: settings.json 认证 (cc-switch) | load_settings_credentials 读取 AUTH_TOKEN/API_KEY | 单元测试通过 (7 测试) |
| P1: 三后端请求构建 | chat_completions/messages/responses build_request | 单元测试通过 (15 测试) |
| P1: 三后端流解析 | 各后端 parse_stream 产出 StreamEvents | 单元测试通过 (7 测试) |
| P1: ChatClient httpx 直连 | chat_stream 产出事件, 401→AuthError | 单元测试通过 (6 测试) |
| P1: 认证解析后端推断 | _infer_backend_from_url: settings/env base_url → 正确后端 | 单元测试通过 (25 测试) |
| P1: 配置工具 (settings_writer) | save/show/clear settings.json — 合并、保留非 env 键、原子写入 | 单元测试通过 (17 测试) |
| P1: TUI 交互模式 | EvilCodeApp 构造 + 流式输出 + 工具调用显示 | 已验证 (导入 + 构造) |

---

## 变更日志

| 日期 | 变更 |
|:---|:---|
| 2026-07-19 | 模块 1: 项目骨架 + helpers 创建 |
| 2026-07-19 | 模块 2: 认证层 (token_refresh + token_store) 创建 |
| 2026-07-19 | 模块 3: core/exceptions.py + core/messages.py 创建 |
| 2026-07-19 | 模块 4: api/schemas.py + api/client.py 创建 |
| 2026-07-19 | 模块 5: tools/result.py + definition.py + registry.py 创建 |
| 2026-07-19 | 模块 6: 6 个内置工具 (file_read/file_search/shell_exec/file_edit/file_write/web_fetch) |
| 2026-07-19 | 模块 7: core/session.py + core/agent.py (AgentHook + 主循环) 创建 |
| 2026-07-19 | 修复 C1: 抽取 _atomic_write 到 helpers/file_io.py（file_edit + file_write 共享） |
| 2026-07-19 | 修复 C2: _repair_dangling_calls 补充第4步去重（移除重复 ToolResult） |
| 2026-07-19 | 修复 C3: web_fetch _resolve_hostname 改用 get_running_loop()（Python 3.10+） |
| 2026-07-19 | 模块 8: cli/main.py (_build_tools + _build_agent + headless/REPL) 创建 |
| 2026-07-19 | 审计修复: phase1.md 模块计数 7/9→8/9，模块 8 状态 待实现→完成 |
| 2026-07-19 | 审计修复: module1.md 补充 helpers/file_io.py (atomic_write) 到范围/架构/状态 |
| 2026-07-19 | 审计修复: module7.md _repair_dangling_calls 3步→4步 |
| 2026-07-19 | 审计修复: 记录 truncate_middle 返回值与设计文档 §3.13.2 的差异 |
| 2026-07-19 | 审计修复: web_fetch prompt 参数已使用（提取目标头部） |
| 2026-07-19 | 模块 9: tests/ 创建（81 个测试：auth/api/core/tools，全部通过） |
| 2026-07-19 | 修复: 循环导入已打断（core/__init__ + api/__init__ 不再 re-export Agent/ChatClient） |
| 2026-07-20 | 设计文档同步: §3.3 ToolDefinitionSchema 命名, §3.13.2 返回类型, §2 re-export 标注, §3.13.3 file_io.py |
| 2026-07-20 | 审计R2: C1 web_fetch 5MB 守卫, C2 auth 匹配收紧, D1-D5 设计文档同步, P1-P6 进度文档补全 |
| 2026-07-24 | 模块 10: 多 Provider 架构补强 — httpx 替代 openai SDK，三后端 Protocol，四级认证，cc-switch 联动 |
| 2026-07-24 | 新增文件: api/sse.py, api/backends/{base,chat_completions,messages,responses,__init__}.py, auth/settings_store.py |
| 2026-07-24 | 重写: api/client.py (httpx 直连), cli/main.py (四级认证 + --api-key/--provider 参数), tests/test_api/test_client.py |
| 2026-07-24 | 新增测试: test_sse.py (6), test_settings_store.py (7), test_backends.py (22) — 共计 115 测试 |
| 2026-07-24 | 设计文档同步: §1 命名, §2 结构, §3.3 schemas, §3.4 ChatClient 重写 + §3.4.1/§3.4.2, §3.14 CLI, §4 依赖, §5 封装 |
| 2026-07-24 | 实现路线同步: LLM SDK 决策 (openai→httpx), 认证决策 (四级), §1.3 代码示例, 依赖, 边界 |
| 2026-07-24 | Bug 修复: _infer_backend 对 levels 2/3 用 argparse 默认 base_url 而非实际 env/settings base_url — 提取 _infer_backend_from_url() + _infer_auth_scheme_from_url() 纯函数，每级重新推断 |
| 2026-07-24 | 新增测试: test_auth_resolution.py (25 测试) — 后端推断, 认证方案, 四级优先级链, bug 修复验证 |
| 2026-07-24 | 审计R4 修复: phase1.md Module 2 表补充 settings_store.py 行 |
| 2026-07-24 | 设计文档同步: §3.14 函数表补充 _infer_backend_from_url, _infer_auth_scheme_from_url |
| 2026-07-25 | 模块 11: 配置工具 + TUI — config/settings_writer.py (save/show/clear settings.json), tui/app.py (Textual TUI), cli config 子命令 |
| 2026-07-25 | 新增文件: config/__init__.py, config/settings_writer.py, tui/__init__.py, tui/app.py, tests/test_config/test_settings_writer.py |
| 2026-07-25 | CLI 增强: _run_config_command (config set/show/clear), --tui 标志路由到 EvilCodeApp |
| 2026-07-25 | 新增测试: test_settings_writer.py (17 测试 — save 5, show 4, clear 4, mask 4) — 共计 157 测试 |
| 2026-07-25 | 修复: cli/main.py config 输出中 Unicode ✓/✗/— → ASCII（Windows GBK 控制台兼容） |
| 2026-07-25 | 设计文档同步: §3.14 config 子命令 + --tui, §3.15 config/settings_writer.py, §3.16 tui/app.py, §4 依赖图 |
| 2026-07-25 | TEST-PLAN.md 重写: 测试 0 (config set/show/clear), 测试 4 (TUI 交互), 移除手动 JSON 编辑 |
| 2026-07-25 | 原生配置: auth/config_store.py (load_config/save_config/generate_config — ~/.evil-code/config.yaml) — 免 CLI 参数自动登录 |
| 2026-07-25 | 认证链: 四级→五级（插入 config.yaml 第2层: CLI > config.yaml > env > settings.json > OAuth） |
| 2026-07-25 | config set: 写入双文件 ~/.evil-code/config.yaml（主）+ ~/.claude/settings.json（备）；show/clear 同时处理双文件 |
| 2026-07-25 | 设计文档+进度文档同步: §2 追加 config_store.py + config/, §3.14 五级认证 + config 子命令, Module 2/8/10 表 |
| 2026-07-25 | 修复: REPL /clear 现在重建 Agent+Session 真正清除上下文（之前只打印文字） |
| 2026-07-25 | 测试修复: conftest.py autouse fixture patch load_config -> None（防止真实 config.yaml 泄漏入测试） |
| 2026-07-25 | 默认值统一改为 DeepSeek: base_url=https://api.deepseek.com/anthropic, model=deepseek-v4-pro |
| 2026-07-25 | Config set: 只有 api_key 必填, base_url 和 model 可选（有默认值） |
