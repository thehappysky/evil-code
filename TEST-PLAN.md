# evil-code 多 Provider 架构测试方案

> 基于 Module 10 架构补强 + 配置工具 + TUI 后的端到端测试
> 你的环境: DeepSeek via cc-switch settings.json

---

## 准备

```bash
cd D:\Pycharm_py\evil-code
pip install -e ".[tui]"
```

`[tui]` 安装 textual（TUI 依赖）。如果已有 textual 可跳过。

安装后可以用 `code-agent` 命令运行。你的 settings.json 已有 DeepSeek 配置，零配置可跑。

---

## 测试 0: 配置工具 -- config set/show/clear

**验证目标**: 参数生成 settings.json（cc-switch 等价功能）

### 0a. 查看当前配置

```bash
code-agent config show
```

**预期**:
```
Settings file: C:\Users\Administrator\.claude\settings.json
  Configured:  [yes]
  API key:     sk-8635e...ef2b
  Base URL:    https://api.deepseek.com/anthropic
  Model:       deepseek-v4-pro
  Auth scheme: Bearer (ANTHROPIC_AUTH_TOKEN)
```

### 0b. 用 config set 写入新配置（交互模式）

```bash
code-agent config set
```

按提示输入:
```
API key: sk-你的deepseek密钥
Base URL: https://api.deepseek.com/anthropic
Model: deepseek-v4-pro
```

**预期**:
```
[OK] Config saved to C:\Users\Administrator\.claude\settings.json
  ANTHROPIC_AUTH_TOKEN: sk-8635e...ef2b
  ANTHROPIC_BASE_URL: https://api.deepseek.com/anthropic
  ANTHROPIC_MODEL: deepseek-v4-pro

Now run: code-agent --tui
```

### 0c. 用 config set 写入新配置（CLI 参数模式）

```bash
code-agent config set --api-key sk-你的密钥 --base-url https://api.deepseek.com/anthropic --model deepseek-v4-pro
```

### 0d. 清除配置

```bash
code-agent config clear
```

**预期**: 清除 auth 相关 env 变量，保留其他 settings.json 键。

### 0e. 切换 provider（模拟 cc-switch）

```bash
# 切换到 OpenAI 兼容端点
code-agent config set --api-key sk-openai密钥 --base-url https://api.openai.com/v1 --model gpt-4o

# 验证
code-agent config show

# 切回 DeepSeek
code-agent config set --api-key sk-deepseek密钥 --base-url https://api.deepseek.com/anthropic --model deepseek-v4-pro
```

---

## 测试 1: settings.json 零配置启动（cc-switch 联动）

**验证目标**: 四级认证解析第 3 级 -- settings.json 读取 + 后端自动推断

```bash
code-agent "你好，请用一句话介绍你自己"
```

**预期行为**:
- 自动读取 ~/.claude/settings.json -- 获取 DeepSeek API key
- 自动推断: base_url 含 "deepseek" -- Messages 后端
- auth_scheme = Bearer（ANTHROPIC_AUTH_TOKEN）
- 流式输出文字到终端

**如果失败**:
- `[Auth Error]` -- 检查 `code-agent config show` 是否 configured
- `[Error]` + HTTP 错误码 -- 检查 API key 是否有效
- 连接超时 -- 检查网络是否能访问 api.deepseek.com

---

## 测试 2: --api-key 显式指定（第 1 级认证）

**验证目标**: 四级认证解析第 1 级 -- CLI --api-key 最高优先级

```bash
code-agent "1+1等于几" --api-key sk-你的deepseek密钥 --base-url https://api.deepseek.com/anthropic --provider messages --model deepseek-v4-pro
```

**预期行为**:
- 跳过 settings.json 和环境变量，直接用 --api-key
- --provider messages 强制 Messages 后端（不走 auto 推断）
- 流式输出

**对比测试**: 把 --api-key 换成错误的值，应该看到 `[Auth Error]`

---

## 测试 3: 环境变量认证（第 2 级）

**验证目标**: 四级认证解析第 2 级 -- 环境变量优先于 settings.json

```cmd
set ANTHROPIC_AUTH_TOKEN=sk-你的deepseek密钥
set ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
set ANTHROPIC_MODEL=deepseek-v4-pro
code-agent "说一个颜色"
```

**预期行为**:
- 环境变量优先于 settings.json（如果 settings.json 也有配置，env 会覆盖）
- 但 --api-key 仍然优先于 env（可以加 --api-key 测试覆盖关系）

**清理**: 测试后清掉环境变量
```cmd
set ANTHROPIC_AUTH_TOKEN=
set ANTHROPIC_BASE_URL=
set ANTHROPIC_MODEL=
```

---

## 测试 4: TUI 交互模式（推荐测试方式）

**验证目标**: 配置 -- 启动 TUI -- 对话 -- 流式回复 -- 工具调用可见

### 完整流程

```bash
# 1. 先确认配置
code-agent config show

# 2. 启动 TUI
code-agent --tui
```

TUI 启动后:
```
 evil-code | deepseek-v4-pro | messages   <- 头部栏
─────────────────────────────────────────
evil-code 已就绪。输入你的问题开始对话。
模型: deepseek-v4-pro
后端: messages
端点: https://api.deepseek.com/anthropic
─────────────────────────────────────────
> 输入你的问题... (Ctrl+C 退出)           <- 输入栏
```

### 4a. 基本对话
输入: `你好，介绍你自己`
预期: Agent 流式输出回复，每行逐行显示

### 4b. 单轮工具调用
先创建测试文件: `echo hello world > test.txt`
输入: `请读取 test.txt 文件的内容`
预期:
- `You: 请读取 test.txt 文件的内容`
- `⟡ tool: file_read`（工具调用标记）
- `Agent: 文件内容是 hello world`（流式回复）

### 4c. 多轮工具链
输入: `搜索当前目录下包含 hello 的文件，然后读取每个匹配文件的内容`
预期: 多次 `⟡ tool:` 标记 + 最终汇总回复

### 4d. 错误恢复
输入: `读取一个不存在的文件 nonexistent.txt`
预期: 工具返回错误，Agent 基于错误结果回复

### 退出
输入 `exit` 或按 `Ctrl+C`

---

## 测试 5: headless 模式 -- 单轮工具调用

**验证目标**: LLM -- 工具调用 -- 工具结果 -- LLM -- 文字回复的完整闭环

先创建测试文件:
```bash
echo hello world > test.txt
```

然后运行:
```bash
code-agent "请读取 test.txt 文件的内容"
```

**预期行为**:
- 模型决定调用 file_read 工具
- 工具执行后返回文件内容
- 模型基于工具结果生成回复
- 终端输出最终文字回复

**注意**: headless 模式下工具调用过程不显示（只显示最终文字）。想看工具调用活动用 TUI 模式（测试 4）。

---

## 测试 6: REPL 交互模式

**验证目标**: 交互模式 + 流式工具调用显示

```bash
code-agent
```

进入 REPL 后依次输入:
```
> 请读取 test.txt 文件
> 在当前目录搜索包含 "hello" 的文件
> 执行 dir 命令查看当前目录
> exit
```

**预期行为**:
- REPL 提示符 `> `
- 每次请求后流式输出文字
- 工具调用时显示 `[tool: file_read]` 等标记
- `exit` 退出

---

## 测试 7: 错误处理 -- 无效 API key

**验证目标**: HTTP 401 -- AuthenticationError

```bash
code-agent "测试" --api-key sk-invalid-key-12345 --base-url https://api.deepseek.com/anthropic --provider messages
```

**预期行为**:
- 终端输出 `[Auth Error] ...401...`
- 非崩溃退出（exit code 1）

---

## 测试 8: --provider auto 自动推断

**验证目标**: 不同 base_url 的自动后端推断

```bash
# DeepSeek -- 自动推断 Messages（base_url 含 "deepseek"）
code-agent "说一个数字" --api-key sk-你的密钥 --base-url https://api.deepseek.com/anthropic

# 如果有 OpenAI 兼容端点 -- 自动推断 ChatCompletions
# code-agent "说一个数字" --api-key sk-你的密钥 --base-url https://api.openai.com/v1
```

**预期行为**:
- `--provider auto`（默认）时，从 base_url 推断后端
- deepseek -- Messages，其他 -- ChatCompletions

---

## 验证检查表

| # | 测试 | 认证级别 | 验证点 | 通过? |
|:---|:---|:---|:---|:---|
| 0a | config show | -- | 读取+掩码显示 | [ ] |
| 0b | config set（交互） | -- | 参数--配置文件 | [ ] |
| 0c | config set（CLI 参数） | -- | 参数--配置文件 | [ ] |
| 0d | config clear | -- | 清除auth保留其他 | [ ] |
| 0e | config set 切换 provider | -- | 模拟 cc-switch | [ ] |
| 1 | settings.json 零配置 | Level 3 | cc-switch 联动 + auto 推断 | [ ] |
| 2 | --api-key 显式 | Level 1 | CLI 最高优先级 | [ ] |
| 3 | 环境变量 | Level 2 | env > settings.json | [ ] |
| 4 | TUI 对话 + 工具 | -- | 配置--TUI--对话闭环 | [ ] |
| 5 | headless 单轮工具 | -- | file_read 闭环 | [ ] |
| 6 | REPL + 工具可见 | -- | 交互模式 + 工具标记 | [ ] |
| 7 | 无效 key | -- | 401 -- AuthError | [ ] |
| 8 | auto 推断 | -- | base_url -- backend | [ ] |

---

## 你的环境配置参考

你的 settings.json 会产生以下 ProviderConfig（自动推断）:

| 字段 | 值 | 来源 |
|:---|:---|:---|
| api_backend | Messages | settings.json base_url 含 "deepseek"（实际 base_url 推断） |
| base_url | https://api.deepseek.com/anthropic | settings.json ANTHROPIC_BASE_URL |
| api_key | sk-8635... | settings.json ANTHROPIC_AUTH_TOKEN |
| auth_scheme | Bearer | AUTH_TOKEN -- Bearer |
| model | deepseek-v4-pro | settings.json ANTHROPIC_MODEL |

实际请求: POST https://api.deepseek.com/anthropic/messages
认证头: Authorization: Bearer sk-8635...
