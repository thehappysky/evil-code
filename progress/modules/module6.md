# Module 6: Built-in Tools (6 tools)

> Phase: 1 | Status: DONE | Created: 2026-07-19

---

## Scope

| Tool | File | Key Features |
|:---|:---|:---|
| file_read | `builtin/file_read.py` | Binary detect (null>10%), line numbers, offset/limit, truncation footer |
| file_search | `builtin/file_search.py` | rg preferred (30s timeout), Python re fallback, glob filter, max_results |
| shell_exec | `builtin/shell_exec.py` | shlex parse (POSIX) / shell=True (Win), asyncio subprocess, timeout kill |
| file_edit | `builtin/file_edit.py` | Unique match check, atomic write, difflib diff, 1MB limit, stateless |
| file_write | `builtin/file_write.py` | Forbidden dirs, auto mkdir, preserve permissions, atomic, 5MB limit |
| web_fetch | `builtin/web_fetch.py` | SSRF 3-layer, redirect re-check, HTML->Markdown, 256KB truncation |

---

## Final Architecture

```
code_agent/tools/builtin/
├── __init__.py
├── file_read.py       # ReadFileInput -> ToolResult
├── file_search.py     # SearchInput -> ToolResult (rg or Python re)
├── shell_exec.py      # ShellInput -> ToolResult (asyncio subprocess)
├── file_edit.py       # SearchReplaceInput -> ToolResult (search_replace)
├── file_write.py      # WriteFileInput -> ToolResult (create/overwrite)
└── web_fetch.py       # WebFetchInput -> ToolResult (HTML->Markdown)
```

---

## Implementation Status

| Item | Status | File |
|:---|:---|:---|
| file_read: binary detection (8KB null check) | Done | `builtin/file_read.py` |
| file_read: line number format ({line:6d}\|{content}) | Done | `builtin/file_read.py` |
| file_read: offset/limit + truncation footer | Done | `builtin/file_read.py` |
| file_search: rg subprocess (30s timeout) | Done | `builtin/file_search.py` |
| file_search: Python re fallback (rglob + compile) | Done | `builtin/file_search.py` |
| file_search: include glob + ignore_case + max_results | Done | `builtin/file_search.py` |
| shell_exec: Windows shell=True / POSIX shlex | Done | `builtin/shell_exec.py` |
| shell_exec: asyncio.wait_for + proc.kill on timeout | Done | `builtin/shell_exec.py` |
| shell_exec: stdout+stderr merge, exit code reporting | Done | `builtin/shell_exec.py` |
| file_edit: unique match (count==1 or replace_all) | Done | `builtin/file_edit.py` |
| file_edit: atomic write (mkstemp+fsync+replace) | Done | `builtin/file_edit.py` |
| file_edit: difflib.unified_diff output | Done | `builtin/file_edit.py` |
| file_edit: 1MB limit + specific error messages | Done | `builtin/file_edit.py` |
| file_write: forbidden directory check | Done | `builtin/file_write.py` |
| file_write: auto mkdir -p + preserve permissions | Done | `builtin/file_write.py` |
| file_write: atomic write + diff for overwrites | Done | `builtin/file_write.py` |
| web_fetch: HTTP->HTTPS upgrade | Done | `builtin/web_fetch.py` |
| web_fetch: SSRF layer 1 (URL parse) | Done | `builtin/web_fetch.py` |
| web_fetch: SSRF layer 2 (DNS resolve check) | Done | `builtin/web_fetch.py` |
| web_fetch: SSRF layer 3 (redirect re-check) | Done | `builtin/web_fetch.py` |
| web_fetch: markdownify HTML->Markdown | Done | `builtin/web_fetch.py` |
| web_fetch: 256KB truncation | Done | `builtin/web_fetch.py` |
| web_fetch: prompt parameter (extraction goal header) | Done | `builtin/web_fetch.py` |

**Progress: 23/23 items complete**

---

## Design Decisions

- Each tool is a standalone file, only depends on tools/result.py
- file_edit is stateless: no "read first" enforcement at tool level; match failure naturally guides model
- shell_exec uses shell=True on Windows (cmd.exe compatibility), shlex on POSIX
- web_fetch manually handles redirects for SSRF re-check (follow_redirects=False)
- All tools return ToolResult (never raise to Agent)
