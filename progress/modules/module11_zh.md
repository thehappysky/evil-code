# 模块 11: 配置工具 + TUI

> 阶段: 1++ | 状态: 完成 | 创建: 2026-07-25

---

## 范围

| 功能 | 描述 |
|:---|:---|
| config/settings_writer.py | 参数→settings.json 写入器（cc-switch 等价）。save_settings_config（合并+原子写入）, show_settings_config（ConfigSummary）, clear_settings_config（保留非 env 键） |
| config/__init__.py | 包导出: save/show/clear 函数 |
| tui/app.py | Textual TUI: EvilCodeApp — 头部栏（model+backend）, RichLog 对话（流式）, 输入栏, 工具调用标记, 错误显示 |
| tui/__init__.py | 包导出: EvilCodeApp |
| cli/main.py（修改） | _run_config_command（config set/show/clear 子命令）, --tui 标志路由到 EvilCodeApp |
| tests/test_config/test_settings_writer.py | 17 测试: save (5), show (4), clear (4), _mask_key (4) |
| pyproject.toml（修改） | 新增 tui 可选依赖组（textual>=0.50.0） |

---

## 最终架构

```
code_agent/
├── config/                    # 新增 — settings.json 写入器（cc-switch 等价）
│   ├── __init__.py            # 导出: save_settings_config, show_settings_config, clear_settings_config
│   └── settings_writer.py    # save/show/clear + ConfigSummary + _atomic_write_json + _mask_key
├── tui/                       # 新增 — Textual TUI 交互界面
│   ├── __init__.py            # 导出: EvilCodeApp
│   └── app.py                 # EvilCodeApp(App) — compose, on_input_submitted, 流式, 工具标记
├── cli/
│   └── main.py                # + _run_config_command, + --tui 标志
└── tests/
    └── test_config/
        ├── __init__.py
        └── test_settings_writer.py  # 17 测试
```

---

## 实现状态

| 项目 | 状态 | 文件 |
|:---|:---|:---|
| save_settings_config: bearer 认证 (ANTHROPIC_AUTH_TOKEN) | 完成 | `config/settings_writer.py` |
| save_settings_config: x_api_key 认证 (ANTHROPIC_API_KEY) | 完成 | `config/settings_writer.py` |
| save_settings_config: 合并保留非 env 键（theme 等） | 完成 | `config/settings_writer.py` |
| save_settings_config: 覆盖先前认证（清除冲突键） | 完成 | `config/settings_writer.py` |
| save_settings_config: 父目录创建 | 完成 | `config/settings_writer.py` |
| ConfigSummary NamedTuple（7 字段） | 完成 | `config/settings_writer.py` |
| show_settings_config: bearer 配置读取 | 完成 | `config/settings_writer.py` |
| show_settings_config: x_api_key 配置读取 | 完成 | `config/settings_writer.py` |
| show_settings_config: 空配置（is_configured=False） | 完成 | `config/settings_writer.py` |
| show_settings_config: 文件缺失（优雅处理） | 完成 | `config/settings_writer.py` |
| clear_settings_config: 只移除 auth 键 | 完成 | `config/settings_writer.py` |
| clear_settings_config: 保留非 env 键 | 完成 | `config/settings_writer.py` |
| clear_settings_config: 移除空 env 块 | 完成 | `config/settings_writer.py` |
| clear_settings_config: 文件缺失时（创建空文件） | 完成 | `config/settings_writer.py` |
| _mask_key: 长密钥（8 前缀 + 4 后缀） | 完成 | `config/settings_writer.py` |
| _mask_key: 短密钥（***） | 完成 | `config/settings_writer.py` |
| _mask_key: 空字符串 | 完成 | `config/settings_writer.py` |
| _mask_key: 正好 12 字符 | 完成 | `config/settings_writer.py` |
| _atomic_write_json: temp + fsync + os.replace | 完成 | `config/settings_writer.py` |
| _run_config_command: config set（CLI 参数模式） | 完成 | `cli/main.py` |
| _run_config_command: config set（交互输入模式） | 完成 | `cli/main.py` |
| _run_config_command: config show（掩码显示） | 完成 | `cli/main.py` |
| _run_config_command: config clear（保留提示） | 完成 | `cli/main.py` |
| _run_config_command: --help / 无参数用法 | 完成 | `cli/main.py` |
| main(): config 子命令分发（在 argparse 之前） | 完成 | `cli/main.py` |
| --tui 标志: 路由到 EvilCodeApp(agent, config).run() | 完成 | `cli/main.py` |
| EvilCodeApp: compose（header + chat-log + input + footer） | 完成 | `tui/app.py` |
| EvilCodeApp: on_mount（聚焦输入，显示就绪信息） | 完成 | `tui/app.py` |
| EvilCodeApp: on_input_submitted（流式 text_delta） | 完成 | `tui/app.py` |
| EvilCodeApp: tool_call_delta 显示（工具标记） | 完成 | `tui/app.py` |
| EvilCodeApp: 错误显示（MaxTurns/Auth/Agent/Exception） | 完成 | `tui/app.py` |
| EvilCodeApp: _flush_buffer（逐行流式） | 完成 | `tui/app.py` |
| EvilCodeApp: CSS 布局（header 3, chat 1fr, input dock bottom） | 完成 | `tui/app.py` |
| EvilCodeApp: BINDINGS（ctrl+c 退出） | 完成 | `tui/app.py` |
| tui/app.py 独立 main() 入口 | 完成 | `tui/app.py` |
| Unicode 修复: ✓/✗/— → ASCII（config 输出, Windows GBK 兼容） | 完成 | `cli/main.py` |
| pyproject.toml: tui 可选依赖组 | 完成 | `pyproject.toml` |

**进度: 38/38 项完成**

---

## 设计决策

- **配置工具作为 cc-switch 等价物**: 用户反馈"正常使用应该是有一段代码是用于接受各种参数，然后生成配置文件的"。手动编辑 JSON 不应作为正式流程。`code-agent config set/show/clear` 命令提供了与 cc-switch Tauri 相同的参数→配置文件流程，但从 CLI 操作。两者写入同一个 ~/.claude/settings.json，使用相同的键名。

- **合并非覆盖**: save_settings_config 先读取现有 settings.json 再合并。非 env 键（theme、model 偏好等）被保留。只有 auth 相关 env 变量（ANTHROPIC_AUTH_TOKEN/API_KEY/BASE_URL/MODEL）被修改。确保 config set 不会破坏用户自定义配置。

- **原子写入**: 使用 tempfile + fsync + os.replace 模式（同 token_store.py）。如果进程在写入中途崩溃，原文件完好无损——rename 在 POSIX 和 Windows 上都是原子操作。

- **clear 保留非 auth 键**: clear_settings_config 只移除 4 个 auth 相关 env 变量。其他 env 变量和所有非 env 键都被保留。空 env 块会被自动移除以保持整洁。

- **ConfigSummary NamedTuple**: show_settings_config 返回 NamedTuple 而非打印字符串。调用方（CLI）决定如何显示。这种分离允许未来的 TUI 或程序化消费者。

- **_mask_key 显示**: API 密钥掩码显示: `sk-8635e...ef2b`。前 8 字符 + 后 4 字符。短于 12 字符的密钥显示为 `***`。防止密钥在终端回滚中意外暴露。

- **Textual TUI 作为测试界面**: 用户反馈"我想要的是直接测试 agent，配置 settings 免登录，然后直接能使用简易的 UI 去操作，可以问问题，有对话框"。TUI 提供全屏交互界面，支持流式输出、工具调用标记和错误显示——比 headless 或 REPL 模式更适合测试。

- **TUI 在 argparse 之前**: config 子命令在 argparse.parse_args() 之前检查，避免干扰 `code-agent "prompt"` 语法。`config` 是保留的第一个位置参数。

- **Unicode 转 ASCII**: Windows GBK 控制台无法编码 ✓（U+2713）、✗（U+2717）或 —（U+2014）。全部替换为 ASCII 等价物（[yes]/[no]、[OK]、--）。

- **TUI 作为可选依赖**: textual 在 `tui` 可选依赖组中（`pip install -e .[tui]`）。核心 agent 不依赖 textual——只有交互式 UI 需要它。

---

## 测试覆盖

| 测试文件 | 测试数 | 关键用例 |
|:---|:---|:---|
| `tests/test_config/test_settings_writer.py` | 17 | save bearer/x_api_key/保留/覆盖/父目录 (5), show bearer/x_api_key/空/缺失 (4), clear 键/保留/空块/缺失 (4), mask 长/短/空/12字符 (4) |
| **新增测试总计** | **17** | |

全部 157 测试通过（140 原有 + 17 新增配置测试 = 157）。

---

## 用户工作流

```
1. code-agent config set --api-key sk-xxx --base-url URL --model MODEL
2. code-agent config show                          # 验证
3. code-agent --tui                                # 启动交互 UI
4. 输入问题，查看流式回复 + 工具调用
5. Ctrl+C 或输入 'exit' 退出
```

这将旧的手动 JSON 编辑流程替换为正式的参数→配置工具。
