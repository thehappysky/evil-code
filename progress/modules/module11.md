# Module 11: Config Tool + TUI

> Phase: 1++ | Status: DONE | Created: 2026-07-25

---

## Scope

| Feature | Description |
|:---|:---|
| config/settings_writer.py | Parameter to settings.json writer (cc-switch equivalent). save_settings_config (merge + atomic write), show_settings_config (ConfigSummary), clear_settings_config (preserve non-env keys) |
| config/__init__.py | Package exports: save/show/clear functions |
| tui/app.py | Textual TUI: EvilCodeApp — header bar (model+backend), RichLog chat (streaming), Input bar, tool call markers, error display |
| tui/__init__.py | Package export: EvilCodeApp |
| cli/main.py (modified) | _run_config_command (config set/show/clear subcommand), --tui flag routing to EvilCodeApp |
| tests/test_config/test_settings_writer.py | 17 tests: save (5), show (4), clear (4), _mask_key (4) |
| pyproject.toml (modified) | Added tui optional dependency group (textual>=0.50.0) |

---

## Final Architecture

```
code_agent/
├── config/                    # NEW — settings.json writer (cc-switch equivalent)
│   ├── __init__.py            # exports: save_settings_config, show_settings_config, clear_settings_config
│   └── settings_writer.py    # save/show/clear + ConfigSummary + _atomic_write_json + _mask_key
├── tui/                       # NEW — Textual TUI interactive interface
│   ├── __init__.py            # exports: EvilCodeApp
│   └── app.py                 # EvilCodeApp(App) — compose, on_input_submitted, streaming, tool markers
├── cli/
│   └── main.py                # + _run_config_command, + --tui flag
└── tests/
    └── test_config/
        ├── __init__.py
        └── test_settings_writer.py  # 17 tests
```

---

## Implementation Status

| Item | Status | File |
|:---|:---|:---|
| save_settings_config: bearer auth (ANTHROPIC_AUTH_TOKEN) | Done | `config/settings_writer.py` |
| save_settings_config: x_api_key auth (ANTHROPIC_API_KEY) | Done | `config/settings_writer.py` |
| save_settings_config: merge preserves non-env keys (theme, etc.) | Done | `config/settings_writer.py` |
| save_settings_config: overwrite previous auth (clear conflicting key) | Done | `config/settings_writer.py` |
| save_settings_config: parent dir creation | Done | `config/settings_writer.py` |
| ConfigSummary NamedTuple (7 fields) | Done | `config/settings_writer.py` |
| show_settings_config: bearer config read | Done | `config/settings_writer.py` |
| show_settings_config: x_api_key config read | Done | `config/settings_writer.py` |
| show_settings_config: empty settings (is_configured=False) | Done | `config/settings_writer.py` |
| show_settings_config: missing file (graceful) | Done | `config/settings_writer.py` |
| clear_settings_config: removes auth keys only | Done | `config/settings_writer.py` |
| clear_settings_config: preserves non-env keys | Done | `config/settings_writer.py` |
| clear_settings_config: removes empty env block | Done | `config/settings_writer.py` |
| clear_settings_config: on missing file (creates empty) | Done | `config/settings_writer.py` |
| _mask_key: long key (8 prefix + 4 suffix) | Done | `config/settings_writer.py` |
| _mask_key: short key (***) | Done | `config/settings_writer.py` |
| _mask_key: empty string | Done | `config/settings_writer.py` |
| _mask_key: exact 12 chars | Done | `config/settings_writer.py` |
| _atomic_write_json: temp + fsync + os.replace | Done | `config/settings_writer.py` |
| _run_config_command: config set (CLI args mode) | Done | `cli/main.py` |
| _run_config_command: config set (interactive prompt mode) | Done | `cli/main.py` |
| _run_config_command: config show (masked display) | Done | `cli/main.py` |
| _run_config_command: config clear (preserve message) | Done | `cli/main.py` |
| _run_config_command: --help / no args usage | Done | `cli/main.py` |
| main(): config subcommand dispatch (before argparse) | Done | `cli/main.py` |
| --tui flag: route to EvilCodeApp(agent, config).run() | Done | `cli/main.py` |
| EvilCodeApp: compose (header + chat-log + input + footer) | Done | `tui/app.py` |
| EvilCodeApp: on_mount (focus input, show ready info) | Done | `tui/app.py` |
| EvilCodeApp: on_input_submitted (stream text_delta) | Done | `tui/app.py` |
| EvilCodeApp: tool_call_delta display (tool marker) | Done | `tui/app.py` |
| EvilCodeApp: error display (MaxTurns/Auth/Agent/Exception) | Done | `tui/app.py` |
| EvilCodeApp: _flush_buffer (line-by-line streaming) | Done | `tui/app.py` |
| EvilCodeApp: CSS layout (header 3, chat 1fr, input dock bottom) | Done | `tui/app.py` |
| EvilCodeApp: BINDINGS (ctrl+c quit) | Done | `tui/app.py` |
| tui/app.py standalone main() entry | Done | `tui/app.py` |
| Unicode fix: ✓/✗/— → ASCII in config output (Windows GBK) | Done | `cli/main.py` |
| pyproject.toml: tui optional dependency group | Done | `pyproject.toml` |

**Progress: 38/38 items complete**

---

## Design Decisions

- **Config tool as cc-switch equivalent**: User feedback: "正常使用应该是有一段代码是用于接受各种参数，然后生成配置文件的". Manual JSON editing is not a proper workflow. The `code-agent config set/show/clear` commands provide the same parameter-to-config-file flow as cc-switch Tauri, but from the CLI. Both write to the same ~/.claude/settings.json with the same key names.

- **Merge not overwrite**: save_settings_config reads the existing settings.json first and merges. Non-env keys (theme, model preferences, etc.) are preserved. Only auth-related env vars (ANTHROPIC_AUTH_TOKEN/API_KEY/BASE_URL/MODEL) are modified. This ensures config set doesn't destroy user customizations.

- **Atomic write**: Uses tempfile + fsync + os.replace pattern (same as token_store.py). If the process crashes mid-write, the original file is intact — the rename is atomic on POSIX and Windows.

- **clear preserves non-auth keys**: clear_settings_config only removes the 4 auth-related env vars. Other env vars and all non-env keys are preserved. An empty env block is removed entirely for cleanliness.

- **ConfigSummary NamedTuple**: show_settings_config returns a NamedTuple, not a printed string. The caller (CLI) decides how to display. This separation allows future TUI or programmatic consumers.

- **_mask_key display**: API keys are masked for display: `sk-8635e...ef2b`. First 8 chars + last 4 chars. Keys shorter than 12 chars show as `***`. This prevents accidental key exposure in terminal scrollback.

- **Textual TUI as test interface**: User feedback: "我想要的是直接测试agent，配置 settings 免登录，然后直接能使用简易的 UI 去操作，可以问问题，有对话框". The TUI provides a full-screen interactive interface with streaming output, tool call markers, and error display — much better for testing than headless or REPL modes.

- **TUI before argparse**: The config subcommand is checked before argparse.parse_args() to avoid interfering with `code-agent "prompt"` syntax. `config` is a reserved first positional arg.

- **Unicode to ASCII**: Windows GBK console can't encode ✓ (U+2713), ✗ (U+2717), or — (U+2014). All were replaced with ASCII equivalents ([yes]/[no], [OK], --).

- **TUI as optional dependency**: textual is in the `tui` optional group (`pip install -e .[tui]`). Core agent doesn't depend on textual — it's only needed for the interactive UI.

---

## Test Coverage

| Test File | Tests | Key Cases |
|:---|:---|:---|
| `tests/test_config/test_settings_writer.py` | 17 | save bearer/x_api_key/preserve/overwrite/parent_dir (5), show bearer/x_api_key/empty/missing (4), clear keys/preserve/empty_block/missing (4), mask long/short/empty/12chars (4) |
| **Total new tests** | **17** | |

All 157 tests pass (140 original + 17 new config tests = 157).

---

## User Workflow

```
1. code-agent config set --api-key sk-xxx --base-url URL --model MODEL
2. code-agent config show                          # verify
3. code-agent --tui                                # launch interactive UI
4. type questions, see streaming responses + tool calls
5. Ctrl+C or type 'exit' to quit
```

This replaces the old manual JSON editing flow with a proper parameter-to-config tool.
