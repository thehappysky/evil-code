# Phase 3: TUI + Skills + Plan Mode - Progress

> Goal: Enhanced TUI, Skills system, Plan Mode FSM, Headless JSON output
> Estimated: 5-7 days
> Status: ✅ DONE (All 9 modules complete)
> Depends on: Phase 2 complete
> Completed: 2026-07-27

---

## Scope

1. **Skills System**: SKILL.md discovery + YAML parsing + SkillTool registration
2. **Plan Mode**: FSM state machine (INACTIVE→PENDING→ACTIVE→EXIT_PENDING→INACTIVE)
3. **TUI Widgets**: Scrollback, Prompt (@/ command), Diff, ThinkingBlock, TodoPanel, StatusBar
4. **TUI Rendering**: Markdown→Rich, Pygments syntax highlighting, Theme management
5. **Headless Output**: `--output-format json | streaming-json`
6. **ChatScreen**: Widget composition + agent streaming integration

---

## Final Architecture (Phase 3 additions)

```
code_agent/              (Phase 1/2 + 3)
├── skills/              5 files — SKILL.md discovery + parsing + SkillManager + SkillTool
├── core/plan_mode.py    Plan Mode FSM
├── hooks/phase3_hooks.py PlanModeHook
├── cli/headless.py      JSON/streaming-json output
├── tui/
│   ├── theme.py         light/dark/auto
│   ├── rendering/       2 files — Markdown + syntax highlight
│   ├── widgets/         6 files — scrollback, prompt, diff, thinking, todo, status
│   └── screens/         ChatScreen
└── pyproject.toml       deps: rich, pygments, pyyaml
```

---

## Hooks Added in Phase 3

| Hook Class | Methods | Responsibility | Status |
|:---|:---|:---|:---|
| PlanModeHook | on_turn_start, on_tool_call, on_turn_end | Plan Mode FSM: inject instructions, block write tools in ACTIVE, handle exit | ✅ |

---

## Module Progress

| Module | Description | Status |
|:---|:---|:---|
| S1 | skills/ — SKILL.md discovery + parsing + SkillManager + SkillTool | ✅ DONE |
| S2 | core/plan_mode.py — Plan Mode FSM | ✅ DONE |
| S3 | hooks/phase3_hooks.py — PlanModeHook | ✅ DONE |
| S4 | cli/headless.py — JSON/NDJSON output | ✅ DONE |
| S5 | tui/rendering/ — Markdown + syntax highlighting | ✅ DONE |
| S6 | tui/theme.py — Theme management | ✅ DONE |
| S7 | tui/widgets/ — 6 widgets | ✅ DONE |
| S8 | tui/screens/ — ChatScreen | ✅ DONE |
| S9 | CLI integration + tests | ✅ DONE |

---

## New Files Created (19 files, ~2,000 lines)

| File | Lines | Module |
|:---|:---|:---|
| `skills/__init__.py` | 12 | S1 |
| `skills/models.py` | 34 | S1 |
| `skills/parser.py` | 155 | S1 |
| `skills/discovery.py` | 133 | S1 |
| `skills/manager.py` | 228 | S1 |
| `core/plan_mode.py` | 203 | S2 |
| `hooks/phase3_hooks.py` | 115 | S3 |
| `cli/headless.py` | 140 | S4 |
| `tui/theme.py` | 170 | S6 |
| `tui/rendering/__init__.py` | 5 | S5 |
| `tui/rendering/markdown.py` | 30 | S5 |
| `tui/rendering/syntax_highlight.py` | 128 | S5 |
| `tui/widgets/__init__.py` | 15 | S7 |
| `tui/widgets/scrollback.py` | 140 | S7 |
| `tui/widgets/prompt_input.py` | 127 | S7 |
| `tui/widgets/diff_view.py` | 85 | S7 |
| `tui/widgets/thinking_block.py` | 80 | S7 |
| `tui/widgets/todo_panel.py` | 74 | S7 |
| `tui/widgets/status_bar.py` | 99 | S7 |

## Modified Files (5 files)

| File | Changes |
|:---|:---|
| `cli/main.py` | + SkillTool registration in _build_tools(), + PlanModeHook in _build_agent(), + SkillManager in _init_phase2_modules, + --output-format/--no-skills/--plan flags, + headless format dispatch |
| `core/agent.py` | Protocol methods: `...` → `return None` (optional), + hasattr checks before hook calls, log level error→warning |
| `tui/__init__.py` | + ChatScreen, Theme exports |
| `tui/app.py` | (unchanged — Phase 1 TUI preserved as fallback) |
| `pyproject.toml` | + rich>=13.0, + pygments>=2.17, + pyyaml>=6.0 (main); + rich, pygments (tui extras) |

---

## New CLI Flags

| Flag | Purpose |
|:---|:---|
| `--output-format plain\|json\|streaming-json` | Headless output mode |
| `--no-skills` | Disable skill discovery and SkillTool |
| `--plan` | Start in Plan Mode |

---

## Verification Criteria

### Skills
- [x] SkillInfo model validation (required fields)
- [x] SKILL.md parser (standard YAML + line-based recovery)
- [x] Skills discovery (project/user dirs, dedup by name, invalid skip)
- [x] SkillManager (load_all, get, format, reload_if_changed)
- [x] SkillTool (run: found → XML, not found → available list)

### Plan Mode
- [x] PlanModeTracker FSM all 5-state transitions
- [x] Idempotent on_prompt_arrived()
- [x] Tool gating: write tools blocked (file_edit, file_write, shell_exec) in ACTIVE
- [x] Read tools allowed (file_read, file_search, web_fetch) in ACTIVE
- [x] Plan Mode exit reminder injection

### TUI
- [x] render_markdown(), highlight_code(), detect_language()
- [x] Theme (dark/light/auto, validate mode)

### Regression
- [x] All 305 Phase 1/2 tests still passing

---

## Test Summary

| Category | Count |
|:---|:---|
| Phase 1 (existing) | 157 |
| Phase 2 (existing) | 148 |
| Phase 3 new tests | 63 |
| **Total** | **368 (all passing)** |
