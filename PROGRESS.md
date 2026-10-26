# Code Agent - Implementation Progress

> 当前增量（2026-10-26）：Agent 评测软件 1137 项通过、覆盖率 74.59%；真实核心开发集 30/32，两次迁移丢失末尾换行保留失败；真实调用合计估算 0.352254 元。Docker 代码任务尚无可判定结果；旧 Docker 测试的环境依赖已另行修正留痕。
> [建设进度与前后对比](progress/evaluation-2026-10-26.md) / [真实数据与失败分析](progress/evaluation-results-2026-10-26.md)。旧运行时批次见 [历史记录](progress/runtime-docs-2026-10-23.md)。

> Last updated: 2026-10-26
> Status: Core features implemented; stabilization in progress. See README.md for current usage.
> The phase narratives and architecture diagram below are historical records, not evidence of current live-service verification.

---

## Phase 7: Sandbox v1 (2026-08-05)

Auto sandbox routing for risky shell commands (`sandbox/policy.py` + `manager.py`),
egress allowlist proxy (`sandbox/proxy.py`), project-bound work volumes,
permission auto-allow integration, TUI sandbox indicator, lifecycle GC with
importance-based eviction, subagent sandbox wiring, TUI `/sandbox` commands,
`sandbox.toml` configuration (image / egress extras / limits), real-volume-size
eviction, git-worktree Draft working copies (CLI + TUI), and
custom sandbox profiles (extends / mounts / deny / network), net container
reuse, and fine-grained mounts (`/scratch` tmpfs), plus
`code-agent sandbox list/gc/protect/remove` CLI. Real-machine smoke passed on
Docker 28.5.2 (clean read-only, work volume persistence, egress blocking,
draft create/merge/discard, profile deny/mounts, net reuse). See
`design-docs/沙箱方案-v1.md` and `progress/phase7.md`.

---

## Overall Status

| Phase | Name | Status | Progress |
|:---|:---|:---|:---|
| Phase 1 | Minimal Viable Agent | Done | 10/10 modules |
| Phase 1+ | Multi-Provider Enhancement | Done | Architecture补强 |
| Phase 1++ | Config Tool + TUI | Done | config/ + tui/ + 17 tests |
| Phase 2 | Session + Memory + Compaction | Implemented | Automated verification |
| Phase 3 | TUI + Skills + Plan Mode | Implemented | Automated verification |
| Phase 4 | Complete Ecosystem | Implemented | Live external services require separate verification |

---

## Architecture Overview (Current)

```
code_agent/
├── auth/          Token management + native config + settings.json reader (cc-switch integration)
├── api/           LLM HTTP communication (httpx direct + Backend Protocol)
│   ├── sse.py     Shared SSE stream parser (iter_sse_events)
│   ├── backends/  Three wire-format backends (chat_completions/messages/responses)
│   ├── client.py  ChatClient (httpx direct, no openai SDK)
│   └── schemas.py  StreamEvent + ProviderConfig + ApiBackend + AuthScheme
├── core/          Agent brain (main loop + session + messages)
├── tools/         Tool registration, dispatch, 6 built-in tools
├── cli/           Command-line entry (headless + REPL + config subcommand + 5-level auth)
├── config/        Config file management (config.yaml native + settings.json fallback)
├── tui/            Textual TUI (full-screen interactive mode, --tui flag)
└── helpers/       Pure utility functions (token estimation, truncation, atomic write)
```

Dependency direction: `cli -> core -> api/tools -> auth/helpers`
External deps: httpx, pydantic, markdownify (openai SDK removed)

---

## Phase 1 Module Tracker

| # | Module | Files | Status | Verified |
|:---|:---|:---|:---|:---|
| 1 | Project skeleton + helpers | pyproject.toml, helpers/*.py | Done | Unit tested |
| 2 | Auth layer | auth/token_refresh.py, auth/token_store.py | Done | Unit tested |
| 3 | Data models | core/exceptions.py, core/messages.py | Done | Unit tested |
| 4 | LLM communication | api/schemas.py, api/client.py | Done | Unit tested |
| 5 | Tool infrastructure | tools/result.py, definition.py, registry.py | Done | Unit tested |
| 6 | Built-in tools (6) | tools/builtin/*.py | Done | Unit tested |
| 7 | Agent core | core/session.py, core/agent.py | Done | Unit tested |
| 8 | CLI entry | cli/main.py | Done | Unit tested |
| 9 | Unit tests | tests/ | Done | 81 → 140 passed |
| 10 | Multi-provider enhancement | api/sse.py, api/backends/*, auth/settings_store.py | Done | Unit tested |
| 11 | Config tool + TUI | config/settings_writer.py, tui/app.py, cli config subcommand | Done | 17 tests, all passing |

---

## Quality Gates

| Gate | Criteria | Status |
|:---|:---|:---|
| P0: Single-turn tool call | "Read this file" triggers file_read | Unit tested (mock LLM) |
| P0: Multi-turn tool chain | "Find and fix bug" triggers grep + read + bash | Unit tested (mock LLM) |
| P1: Token auto-refresh | Expired token triggers refresh without crash | Unit tested (pytest-httpx) |
| P1: Graceful cancel | Ctrl+C cancels current turn cleanly | Pending (requires integration test) |
| P1: Multi-provider SSE parsing | iter_sse_events handles data/event/DONE/multiline | Unit tested (6 tests) |
| P1: Settings.json auth (cc-switch) | load_settings_credentials reads AUTH_TOKEN/API_KEY | Unit tested (7 tests) |
| P1: Three-backend request building | chat_completions/messages/responses build_request | Unit tested (15 tests) |
| P1: Three-backend stream parsing | Each backend parse_stream yields StreamEvents | Unit tested (7 tests) |
| P1: ChatClient httpx direct | chat_stream yields events, 401→AuthError | Unit tested (6 tests) |
| P1: Auth resolution backend inference | _infer_backend_from_url: settings/env base_url → correct backend | Unit tested (25 tests) |
| P1: Config tool (settings_writer) | save/show/clear settings.json — merge, preserve non-env keys, atomic write | Unit tested (17 tests) |
| P1: TUI interactive mode | EvilCodeApp construction + streaming + tool call display | Verified (imports + construction) |

---

## Change Log

| Date | Change |
|:---|:---|
| 2026-07-19 | Module 1: project skeleton + helpers created |
| 2026-07-19 | Module 2: auth layer (token_refresh + token_store) created |
| 2026-07-19 | Module 3: core/exceptions.py + core/messages.py created |
| 2026-07-19 | Module 4: api/schemas.py + api/client.py created |
| 2026-07-19 | Module 5: tools/result.py + definition.py + registry.py created |
| 2026-07-19 | Module 6: 6 built-in tools (file_read/file_search/shell_exec/file_edit/file_write/web_fetch) |
| 2026-07-19 | Module 7: core/session.py + core/agent.py (AgentHook + main loop) created |
| 2026-07-19 | Fix C1: extracted _atomic_write to helpers/file_io.py (shared by file_edit + file_write) |
| 2026-07-19 | Fix C2: _repair_dangling_calls added step 4 dedup (remove duplicate ToolResults) |
| 2026-07-19 | Fix C3: web_fetch _resolve_hostname uses get_running_loop() (Python 3.10+) |
| 2026-07-19 | Module 8: cli/main.py (_build_tools + _build_agent + headless/REPL) created |
| 2026-07-19 | Audit fix: phase1.md module count 7/9→8/9, Module 8 status PENDING→DONE |
| 2026-07-19 | Audit fix: module1.md added helpers/file_io.py (atomic_write) to scope/arch/status |
| 2026-07-19 | Audit fix: module7.md _repair_dangling_calls 3-step→4-step |
| 2026-07-19 | Audit fix: recorded truncate_middle return type discrepancy (design doc §3.13.2) |
| 2026-07-19 | Audit fix: web_fetch prompt parameter now used (extraction goal header) |
| 2026-07-19 | Module 9: tests/ created (81 tests: auth/api/core/tools, all passing) |
| 2026-07-19 | Fix: circular import broken (core/__init__ + api/__init__ no longer re-export Agent/ChatClient) |
| 2026-07-20 | Design doc sync: §3.3 ToolDefinitionSchema naming, §3.13.2 return type, §2 re-export notes, §3.13.3 file_io.py |
| 2026-07-20 | Audit R2: C1 web_fetch 5MB guard, C2 auth error matching tightened, D1-D5 design doc sync, P1-P6 progress docs |
| 2026-07-24 | Module 10: Multi-provider architecture enhancement — httpx replaces openai SDK, 3-backend Protocol, 4-level auth, cc-switch integration |
| 2026-07-24 | New files: api/sse.py, api/backends/{base,chat_completions,messages,responses,__init__}.py, auth/settings_store.py |
| 2026-07-24 | Rewritten: api/client.py (httpx direct), cli/main.py (4-level auth resolution + --api-key/--provider args), tests/test_api/test_client.py |
| 2026-07-24 | New tests: test_sse.py (6), test_settings_store.py (7), test_backends.py (22) — total 115 tests |
| 2026-07-24 | Design doc sync: §1 naming, §2 structure, §3.3 schemas, §3.4 ChatClient rewrite + §3.4.1/§3.4.2, §3.14 CLI, §4 deps, §5 encapsulation |
| 2026-07-24 | Implementation roadmap sync: LLM SDK decision (openai→httpx), auth decision (4-level), §1.3 code sample, deps, boundaries |
| 2026-07-24 | Bug fix: _infer_backend used argparse default base_url for levels 2/3 instead of actual env/settings base_url — extracted _infer_backend_from_url() + _infer_auth_scheme_from_url() pure functions, re-infer at each level |
| 2026-07-24 | New test: test_auth_resolution.py (25 tests) — backend inference, auth scheme, 4-level priority chain, bug fix verification |
| 2026-07-24 | Audit R4 fix: phase1.md Module 2 table added settings_store.py row |
| 2026-07-24 | Design doc sync: §3.14 function table added _infer_backend_from_url, _infer_auth_scheme_from_url |
| 2026-07-25 | Module 11: Config tool + TUI — config/settings_writer.py (save/show/clear settings.json), tui/app.py (Textual TUI), cli config subcommand |
| 2026-07-25 | New files: config/__init__.py, config/settings_writer.py, tui/__init__.py, tui/app.py, tests/test_config/test_settings_writer.py |
| 2026-07-25 | CLI enhancement: _run_config_command (config set/show/clear), --tui flag routing to EvilCodeApp |
| 2026-07-25 | New tests: test_settings_writer.py (17 tests — save 5, show 4, clear 4, mask 4) — total 157 tests |
| 2026-07-25 | Fix: Unicode ✓/✗/— → ASCII in cli/main.py config output (Windows GBK console) |
| 2026-07-25 | Design doc sync: §3.14 config subcommand + --tui, §3.15 config/settings_writer.py, §3.16 tui/app.py, §4 deps graph |
| 2026-07-25 | TEST-PLAN.md rewrite: Test 0 (config set/show/clear), Test 4 (TUI interactive), removed manual JSON editing |
| 2026-07-25 | Native config: auth/config_store.py (load_config/save_config/generate_config — ~/.evil-code/config.yaml) — auto-login without CLI args |
| 2026-07-25 | Auth chain: 4-level -> 5-level (insert config.yaml at level 2: CLI > config.yaml > env > settings.json > OAuth) |
| 2026-07-25 | Config set: writes to both ~/.evil-code/config.yaml (primary) + ~/.claude/settings.json (fallback); show/clear handle both |
| 2026-07-25 | Design doc + progress doc sync: §2 added config_store.py + config/, §3.14 5-level auth + config subcommand, Module 2/8/10 tables |
| 2026-07-25 | Fix: /clear in REPL now rebuilds Agent with fresh Session (was only cosmetic before) |
| 2026-07-25 | Test fix: conftest.py autouse fixture patches load_config -> None (prevents real config.yaml from leaking into tests) |
| 2026-07-25 | Defaults changed to DeepSeek: base_url=https://api.deepseek.com/anthropic, model=deepseek-v4-pro across schemas/config_store/cli |
| 2026-07-25 | Config set: only api_key required; base_url and model now optional with DeepSeek defaults |
