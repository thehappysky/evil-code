# Module 8: CLI Entry

> Phase: 1 | Status: DONE | Created: 2026-07-19

---

## Scope

| Feature | Description |
|:---|:---|
| _build_tools() | Register 6 built-in tools + finalize |
| _build_agent() | Core assembly function — inject hooks list (open-closed integration point) |
| _run_headless() | Single prompt -> stream to stdout |
| _run_interactive() | REPL loop: input -> agent.run() -> print |
| main() | argparse entry (--cwd, --model, --max-turns, positional prompt) |

---

## Final Architecture

```
code_agent/cli/
├── __init__.py
└── main.py        # argparse + _build_tools + _build_agent + headless/REPL
```

---

## Implementation Status

| Item | Status | File |
|:---|:---|:---|
| _build_tools() - register 6 tools + finalize | Done | `cli/main.py` |
| _build_agent() - hook assembly (Phase 1: empty hooks) | Done | `cli/main.py` |
| _run_headless() - stream text_delta to stdout | Done | `cli/main.py` |
| _run_interactive() - REPL with graceful exit | Done | `cli/main.py` |
| main() - argparse (prompt, --cwd, --model, --max-turns, --base-url) | Done | `cli/main.py` |

**Progress: 5/5 items complete**

---

## Design Decisions (from design doc)

- _build_agent() is THE integration point for open-closed principle:
  Phase 1 returns empty hooks; Phase 2-4 only modify this function to add Hook instances
- Assembly flow: Session() -> ChatClient(model) -> _build_tools() -> _build_agent()
- Headless mode: consume async generator, print text_delta, exit
- REPL mode: while True loop with input(), handle KeyboardInterrupt/EOFError
- CLI args Phase 1: prompt (positional), --cwd, --model, --max-turns
