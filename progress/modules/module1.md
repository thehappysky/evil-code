# Module 1: Project Skeleton + Helpers

> Phase: 1 | Status: DONE | Created: 2026-07-19

---

## Scope

| Feature | Description |
|:---|:---|
| pyproject.toml | Build config (hatchling), dependencies, CLI entry point |
| Package structure | 6 sub-packages: auth/, api/, core/, tools/, cli/, helpers/ + tests/ |
| estimate_tokens() | bytes/4 heuristic token estimation |
| exceeds_threshold() | Check if usage exceeds percentage of context window |
| truncate_with_preview() | Keep head + footer hint (line/char stats) |
| truncate_middle() | Keep head + tail, replace middle with ellipsis |
| atomic_write() | Shared atomic file write (temp + fsync + os.replace) |

---

## Final Architecture

```
code_agent/
├── __init__.py              # __version__ = "0.1.0"
├── helpers/
│   ├── __init__.py          # re-exports all public functions
│   ├── token_counter.py     # estimate_tokens, exceeds_threshold
│   ├── text_truncate.py     # truncate_with_preview, truncate_middle
│   └── file_io.py           # atomic_write (shared by file_edit + file_write)
pyproject.toml               # hatchling build, deps: openai/pydantic/httpx/markdownify
```

---

## Implementation Status

| Item | Status | File |
|:---|:---|:---|
| pyproject.toml | Done | `pyproject.toml` |
| Package __init__.py (all 6 + builtin) | Done | `code_agent/*/__init__.py` |
| estimate_tokens() | Done | `code_agent/helpers/token_counter.py` |
| exceeds_threshold() | Done | `code_agent/helpers/token_counter.py` |
| truncate_with_preview() | Done | `code_agent/helpers/text_truncate.py` |
| truncate_middle() | Done | `code_agent/helpers/text_truncate.py` |
| atomic_write() | Done | `code_agent/helpers/file_io.py` |

**Progress: 7/7 items complete**

---

## Design Decisions

- helpers/ is pure functions with zero project-internal dependencies (can be used standalone)
- All truncation functions return `(text, was_truncated)` tuple for caller awareness
- truncate_middle tries line-boundary breaks for readability
- pyproject.toml uses hatchling (modern, minimal config)

### Design Doc Discrepancy

| Item | Design Doc (§3.13.2) | Implementation | Reason |
|:---|:---|:---|:---|
| truncate_middle return | `-> str` | `-> tuple[str, bool]` | Consistent with truncate_with_preview; caller needs truncation awareness |
