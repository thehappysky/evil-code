# Python AI Coding Agent -- Development Index

> Progressive disclosure: start from the roadmap, drill down to implementation details.

2026-10-26 新增：[LLM / Agent 评测建设方案](LLM-Evaluation-Plan.md)、[操作手册与流程图](../docs/evaluation.md)、[真实实验与失败归因](../progress/evaluation-results-2026-10-26.md)。

---

## Document Map

```
DEV-INDEX.md  (this file)
  |
  +-- Python-Agent-MVP-实现路线.md          [ROADMAP] Overall 4-phase plan
  |     |
  |     +-- Phase 1: Minimal Viable Agent
  |     |     +-- Python-Agent-MVP-详细设计.md       [CODE] Per-file class/function design (含编辑工具 + AgentHook)
  |     |
  |     +-- Phase 2: Session + Memory + Compaction
  |     |     +-- Python-Agent-MVP-Phase2-详细设计.md [CODE] storage/, memory/, compaction/ (含 Windows FTS5 降级)
  |     |
  |     +-- Phase 3: TUI + Skills + Plan Mode
  |     |     +-- Python-Agent-MVP-Phase3-详细设计.md [CODE] tui/, skills/, plan_mode.py (含 SkillTool)
  |     |
  |     +-- Phase 4: Complete Ecosystem
  |           +-- Python-Agent-MVP-Phase4-详细设计.md [CODE] permission/, mcp/, plugins/, hooks/ (含 Windows SAFE_COMMANDS)
  |
  +-- Python-Agent-补足方案-设计.md            [DESIGN] 2026-08 补足方案 — 阶段 0 已实施（权限 fail-closed + 接线 + TUI 修复），阶段 1-7 规划（对齐 Grok Build）
  |
  +-- Grok-Build-LLM缺陷防御体系深度分析.md     [REFERENCE] Full architecture analysis of Rust reference
  +-- 架构逻辑.md                                 [REFERENCE] Pure architecture logic analysis
  |
  +-- [ARCHIVED] Python-Agent-MVP-文件架构设计.md  (naming outdated — use Phase 1 详细设计 instead)
  |
  +-- [Phase A] 上帝文件拆分 (2026-08-06)
        main.py 2099→564行, tui.py 4812→2583行
        新增: cli/{auth,args,assembly,execution,subcommands/}, tui/{widgets,screens,rendering/}
        详见: progress/phase5.md "Phase A 上帝文件拆分" 章节
        当前文件结构: progress/PROGRESS-SUMMARY.md §包结构总览
```

---

## How to Use (Progressive Disclosure)

### Step 1: Understand the big picture
Read `Python-Agent-MVP-实现路线.md` for the overall 4-phase plan and time estimates.

### Step 2: Phase 1
- IGNORE `Python-Agent-MVP-文件架构设计.md` (archived — naming outdated)
- Read `Python-Agent-MVP-详细设计.md` for authoritative per-file design
- This includes: AgentHook middleware pipeline (§3.7), async httpx auth (§3.1), 6 built-in tools (§3.12)

### Step 3: Phase 2
Read `Python-Agent-MVP-Phase2-详细设计.md` for storage, memory, compaction, vault, and Windows FTS5 fallback design.

### Step 4: Phase 3
Read `Python-Agent-MVP-Phase3-详细设计.md` for TUI widgets, Skills system (including SkillTool), and Plan Mode.

### Step 5: Phase 4
Read `Python-Agent-MVP-Phase4-详细设计.md` for permission (including Windows SAFE_COMMANDS), MCP, plugins, hooks, and telemetry.

### Reference
When you need to understand WHY: `Grok-Build-LLM缺陷防御体系深度分析.md` or `架构逻辑.md`

---

## Quick Reference: Which Document for What?

| I want to... | Read this |
|:---|:---|
| Know what to build next | `Python-Agent-MVP-实现路线.md` |
| Know where files go | Phase-specific doc: `Python-Agent-MVP-详细设计.md` §2 包结构总览 |
| Know how to implement a specific class | `Python-Agent-MVP-详细设计.md` §3 (Phase 1) or phase-specific doc |
| Understand OAuth token flow | `Python-Agent-MVP-详细设计.md` §3.1 (auth/token_refresh.py) |
| Understand Agent loop + AgentHook | `Python-Agent-MVP-详细设计.md` §3.7 (core/agent.py) |
| Understand tool registry | `Python-Agent-MVP-详细设计.md` §3.11 (tools/registry.py) |
| Understand session storage | `Python-Agent-MVP-Phase2-详细设计.md` §2 |
| Understand memory index + FTS5 | `Python-Agent-MVP-Phase2-详细设计.md` §3 |
| Understand compaction flow | `Python-Agent-MVP-Phase2-详细设计.md` §7-9 |
| Understand TUI architecture | `Python-Agent-MVP-Phase3-详细设计.md` §2 |
| Understand Skills system + SkillTool | `Python-Agent-MVP-Phase3-详细设计.md` §7 |
| Understand Plan Mode | `Python-Agent-MVP-Phase3-详细设计.md` §8 |
| Understand permissions | `Python-Agent-MVP-Phase4-详细设计.md` Section 2 |
| Understand MCP integration | `Python-Agent-MVP-Phase4-详细设计.md` Section 3 |
| Understand plugins/hooks | `Python-Agent-MVP-Phase4-详细设计.md` Sections 4-5 |
| Understand edit tools (search_replace/write/web_fetch) | `Python-Agent-MVP-详细设计.md` §3.12.4-3.12.6 |
| Understand AgentHook extension points | `Python-Agent-MVP-详细设计.md` §3.7 |
| Understand architecture rationale | `Grok-Build-LLM缺陷防御体系深度分析.md` |
| Know current file structure (post-Phase A) | `progress/PROGRESS-SUMMARY.md` §包结构总览 |
| Understand Phase A refactoring (main.py/tui.py split) | `progress/phase5.md` "Phase A 上帝文件拆分" |

---

## Document Status

| Document | Status | Description |
|:---|:---|:---|
| `DEV-INDEX.md` | Complete | This index file |
| `Python-Agent-MVP-实现路线.md` | ✅ Complete 2026-07-19 | 4-phase roadmap (工时已修订为 26-36 天，moto→pytest-httpx)。命名以详细设计为准，见文档顶部对照表 |
| `Python-Agent-MVP-文件架构设计.md` | ⚠️ ARCHIVED — 已被 Phase 1 详细设计取代 | Naming outdated (grok_py vs code_agent, etc.). See mapping table at top of doc. |
| `Python-Agent-MVP-详细设计.md` | ✅ Complete 2026-07-19 | Phase 1: AgentHook middleware (§3.7) + async httpx auth (§3.1) + 6 tools (§3.12) |
| `Python-Agent-MVP-Phase2-详细设计.md` | ✅ Complete 2026-07-19 | Phase 2: storage/memory/compaction/vault + FTS5 fallback (§3) |
| `Python-Agent-MVP-Phase3-详细设计.md` | ✅ Complete 2026-07-19 | Phase 3: TUI/skills(含 SkillTool §7)/plan-mode |
| `Python-Agent-MVP-Phase4-详细设计.md` | ✅ Complete 2026-07-19 | Phase 4: permission(含 Windows SAFE_COMMANDS)/MCP/plugins/hooks(HookResult unified) |
| `Grok-Build-LLM缺陷防御体系深度分析.md` | Complete | Rust reference architecture analysis |
| `架构逻辑.md` | Complete | Pure architecture logic analysis |
| Phase A 上帝文件拆分 | ✅ Complete 2026-08-06 | main.py 2099→564行(-73%), tui.py 4812→2583行(-46%)。新增 15 个文件。详见 `progress/phase5.md` |
