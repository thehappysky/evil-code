# Evil Code

Python 编码 Agent，提供命令行、交互式 REPL 和 Textual 终端界面。支持读取和修改文件、执行命令、多轮工具调用、会话恢复、记忆检索、上下文压缩、计划模式、子代理、MCP 和 Docker 沙箱。

当前版本：`0.1.0`，仍处于开发阶段。模型接口支持 Chat Completions、Messages 和 Responses 三种协议，具体模型名称与接口地址由用户配置。

## 安装

需要 Python 3.11 或以上版本。以下命令适用于 Windows PowerShell，在项目根目录执行；无需激活环境，也不会修改系统 Python 的依赖。

```powershell
python -m venv .venv-dev
.\.venv-dev\Scripts\python.exe -m pip install -e ".[dev,tui]"
.\.venv-dev\Scripts\python.exe -m pip check
```

只使用命令行可安装 `pip install -e .`；使用终端界面需要额外安装 `tui`。Docker 沙箱需要另行安装并启动 Docker Desktop，普通文件工具和界面本身不要求 Docker。

本次 Windows / Python 3.13.9 的依赖版本记录在 `constraints-dev-windows.txt`。在相同环境复现验证版本时，安装命令可增加 `-c constraints-dev-windows.txt`；该文件是版本约束快照，其他操作系统需重新验证。

## 启动

启动终端界面，首次使用时配置模型服务、接口地址和密钥：

```powershell
.\.venv-dev\Scripts\code-agent.exe --tui
```

查看命令行选项：

```powershell
.\.venv-dev\Scripts\code-agent.exe --help
```

命令行支持从环境变量读取密钥。下面的服务地址、模型和密钥均为占位值，需按实际服务替换：

```powershell
$env:ANTHROPIC_API_KEY = "你的密钥"
.\.venv-dev\Scripts\code-agent.exe "说明当前项目结构" --base-url "https://你的服务地址/v1" --provider chat_completions --model "你的模型名称"
```

认证优先级：命令行参数 → `~/.evil-code/config.yaml` → 环境变量 → `~/.claude/settings.json` → OAuth。已有本机配置可能优先于环境变量生效。TUI 管理的模型配置位于 `~/.code-agent/`。

`code-agent config set` 会同时更新本机原生配置和 `~/.claude/settings.json`；如果希望保留其他客户端的配置，可优先使用终端界面的服务配置功能。

## 权限与沙箱

- 普通读取工具自动放行；写入、删除和命令执行受权限规则控制。
- 非交互模式下，未获允许的操作会被拒绝。交互模式可请求审批。
- 默认沙箱模式为 `auto`，`--sandbox` 使用 `strict`。环境变量 `CODE_AGENT_SANDBOX=off|auto|strict` 优先于命令行开关。
- 通过 CLI/TUI 执行的沙箱命令在 Docker 不可用时返回错误，不会自动改为本机执行。
- 沙箱负责 shell 命令隔离，文件工具仍在宿主机执行并接受权限检查。
- 子代理继承父代理的权限与计划模式限制；需要审批的操作在子代理中直接拒绝，不会另行打开交互提示。子代理禁止在 Docker 不可用时回退到宿主机。
- 出站域名代理允许白名单域名及其子域名，仅约束经过代理的流量。它不提供对所有容器网络流量的强制隔离。

## 验证

```powershell
.\.venv-dev\Scripts\python.exe -m pytest -q --tb=short
```

测试配置包含 60% 的总行覆盖率门禁。测试使用模拟模型响应，不要求真实 API 密钥；通过自动测试不代表真实模型服务、Docker 和远程 MCP 的完整流程均已验证。GitHub Actions 会在 Windows 的 Python 3.11 和 3.13 上执行安装及测试。

新会话使用固定长度的项目目录哈希，避免 Windows 路径因 Base64 编码而过长。已有 Base64 布局的会话仍可读取、续写及查找。

## 项目结构

| 路径 | 职责 |
| --- | --- |
| `code_agent/cli/`、`tui/` | 命令行入口、依赖装配、终端交互 |
| `code_agent/core/` | 推理循环、消息、会话、计划模式 |
| `code_agent/api/`、`auth/` | 模型协议、流式响应、认证 |
| `code_agent/tools/`、`subagents/` | 工具注册执行、子代理 |
| `code_agent/permission/`、`sandbox/` | 权限判断、Docker 命令隔离 |
| `code_agent/storage/`、`memory/`、`compaction/` | 会话保存、记忆、上下文压缩 |
| `tests/` | 单元测试与集成测试 |
| `design-docs/`、`progress/` | 设计记录与历史进度，部分内容已过时 |

上传与后续提交方法见 [GitHub 上传说明](GITHUB-UPLOAD.md)。
