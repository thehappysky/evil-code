# evil-code Agent 功能测试流程

> 测试日期: 2026-07-25
> 测试目标: 验证配置免登录 → TUI 交互 → 6 个工具全链路可用

---

## 前置条件

```bash
# 确认在项目目录
cd D:\Pycharm_py\evil-code

# 确认依赖安装完整
.venv\Scripts\pip install -e ".[tui]"

# 确认测试通过
.venv\Scripts\python -m pytest tests/ -q
# 预期输出: 157 passed

# 测试素材已创建在 test_workspace/
dir test_workspace\
# 预期: demo.txt  math_utils.py
```

---

## Step 1: 配置文件免登录

### 1.1 生成配置文件

```bash
# 方式 A: 交互式（推荐用于首次测试）
.venv\Scripts\python -m code_agent.cli.main config set

# 按提示输入:
#   API key: sk-xxxxxxxxxxxxx        <-- 你的真实 API key
#   Base URL: https://api.anthropic.com/v1
#   Model: claude-sonnet-4-5-20250501

# 方式 B: 命令行一次性（CI/脚本用）
.venv\Scripts\python -m code_agent.cli.main config set \
    --api-key sk-xxxxxxxxxxxxx \
    --base-url https://api.anthropic.com/v1 \
    --model claude-sonnet-4-5-20250501 \
    --auth-scheme x_api_key \
    --backend messages
```

**验证点**:
- [ ] 输出 `[OK] Config saved`
- [ ] Primary 路径: `~/.evil-code/config.yaml`
- [ ] Fallback 路径: `~/.claude/settings.json`

### 1.2 验证配置文件

```bash
.venv\Scripts\python -m code_agent.cli.main config show
```

**验证点**:
- [ ] Primary `config.yaml` 显示 `[configured]`
- [ ] 显示正确的 Model / Base URL / 掩码后的 API key
- [ ] Fallback `settings.json` 同步显示相同信息

### 1.3 验证免登录（headless 模式快速测试）

```bash
.venv\Scripts\python -m code_agent.cli.main "回复 'OK' 即可，不要调用任何工具"
```

**验证点**:
- [ ] 不传 `--api-key` 也能正常运行（说明 config.yaml 生效）
- [ ] 输出 `OK`（或类似简短回复）
- [ ] 无认证错误

---

## Step 2: 启动 TUI

```bash
.venv\Scripts\python -m code_agent.cli.main --tui

# 或使用可直接运行的入口:
# .venv\Scripts\code-agent --tui
```

**验证点**:
- [ ] 终端全屏切换到 Textual 界面
- [ ] Header 显示 evil-code | 模型名 | 后端类型
- [ ] 启动信息显示 "Ready. Type a question to start."
- [ ] 输入框在底部，有 placeholder 文字
- [ ] Footer 显示快捷键 F1 Exit / Esc Cancel

---

## Step 3: 六工具全链路测试

> **重要: 每个测试用 `/clear` 清屏后再做下一个，避免上下文污染**

### Test 1: 纯文本回复（无工具调用）

```
输入: 你好！请用一句话介绍你自己。
```

**预期**:
- [ ] "Agent is thinking..." 状态指示器出现
- [ ] `You:` 青色显示用户消息
- [ ] `Agent:` 蓝色显示 agent 回复（流式逐行）
- [ ] 无工具调用
- [ ] 输入栏恢复，显示 `Turn #1`

### Test 2: file_read — 读取文件

```
输入: 读取 test_workspace/demo.txt 这个文件的内容，告诉我里面写了什么
```

**预期**:
- [ ] Agent 调用 `<> file_read` 工具（橙色）
- [ ] 返回文件内容（带行号）
- [ ] Agent 用中文总结文件内容
- [ ] 输入栏显示 `Turn #1 | 1 tool(s)`

### Test 3: file_search — 搜索代码

```
输入: 在 test_workspace/ 目录下搜索所有包含 'hello' 的文件（不区分大小写），告诉我在哪些文件的哪些行找到了
```

**预期**:
- [ ] Agent 调用 `<> file_search` 工具
- [ ] 返回 demo.txt:1, demo.txt:3, demo.txt:4（三处匹配）
- [ ] Agent 正确总结搜索结果
- [ ] 输入栏显示 `Turn #1 | 1 tool(s)`

### Test 4: shell_exec — 执行命令

```
输入: 用 shell 命令运行 test_workspace/math_utils.py，把输出展示给我
```

**预期**:
- [ ] Agent 调用 `<> shell_exec` 工具
- [ ] 命令类似 `python test_workspace/math_utils.py`
- [ ] 返回: `Hello, World!` 和 `2 + 3 = 5`
- [ ] Agent 确认执行结果

### Test 5: file_edit — 编辑文件

```
输入: 把 test_workspace/demo.txt 中 'This is the last line.' 改成 'This is the last line — modified by evil-code agent on 2026-07-25.'
```

**预期**:
- [ ] Agent 先调用 `<> file_read` 确认当前内容
- [ ] 再调用 `<> file_edit` 执行精确替换
- [ ] 返回 unified diff（原行 vs 新行）
- [ ] Agent 确认替换成功

**验证**（退出 TUI 后在终端检查）:
```bash
type test_workspace\demo.txt
```
- [ ] 最后一行已变更为 `This is the last line — modified by evil-code agent on 2026-07-25.`

### Test 6: file_write — 创建新文件

```
输入: 创建一个新的 Python 文件 test_workspace/hello_agent.py，内容是打印 'evil-code agent says hello!' 的函数
```

**预期**:
- [ ] Agent 调用 `<> file_write` 工具
- [ ] 返回 "Created new file: .../hello_agent.py (N lines, M characters)"
- [ ] Agent 确认创建成功

**验证**:
```bash
type test_workspace\hello_agent.py
.venv\Scripts\python test_workspace\hello_agent.py
```
- [ ] 文件存在且内容正确
- [ ] 执行输出 "evil-code agent says hello!"

### Test 7: web_fetch — 获取网页

```
输入: 获取 https://example.com 的网页内容，告诉我网页标题是什么
```

**预期**:
- [ ] Agent 调用 `<> web_fetch` 工具
- [ ] 返回 Markdown 格式的网页内容
- [ ] Agent 指出标题是 "Example Domain"
- [ ] 输入栏显示 `Turn #1 | 1 tool(s)`

### Test 8: 多轮工具链（核心竞争力）

```
输入: 在 test_workspace/ 目录下找到所有包含 'hello' 的文件，读取这些文件的内容，然后把其中所有的 'hello' 替换为 'hi'（不区分大小写）
```

**预期**:
- [ ] Agent 先调用 `<> file_search` 找到包含 'hello' 的文件
- [ ] 再调用 `<> file_read` 读取每个匹配文件
- [ ] 再调用 `<> file_edit` 逐一替换
- [ ] 整个过程在一次对话中自动完成
- [ ] 输入栏显示多个 tool(s) 计数

### Test 9: 连续多轮对话

```
第一轮: 你好，我叫小明
第二轮: 我叫什么名字？
第三轮: 请用 python 打印我的名字，直接执行
```

**预期**:
- [ ] 第二轮 agent 能正确回答"小明"（上下文记忆）
- [ ] 第三轮 agent 调用 shell_exec 执行 python 代码
- [ ] 代码中使用了 "小明" 这个名字

### Test 10: Esc 取消测试

```
输入: 写一个完整的 web 爬虫，爬取 100 个页面
(在 agent 思考/执行中按 Esc)
```

**预期**:
- [ ] Agent 开始思考或执行
- [ ] 按 Esc 后显示 `(cancelled)`
- [ ] 输入栏恢复，可以继续输入新问题
- [ ] 不会崩溃或退出

---

## Step 4: 退出后验证

```bash
# 1. 确认文件修改生效
type test_workspace\demo.txt
# 预期: 最后一行已被修改

# 2. 确认新文件创建
type test_workspace\hello_agent.py
# 预期: 文件内容正确

# 3. 确认配置仍在
.venv\Scripts\python -m code_agent.cli.main config show
# 预期: 两个文件都显示 [configured]

# 4. 运行完整测试套件确认无副作用
.venv\Scripts\python -m pytest tests/ -q
# 预期: 157 passed
```

---

## 测试总结 Checklist

| # | 测试项 | 工具 | 通过 |
|:---|:---|:---|:---|
| 1 | 纯文本回复 | — | [ ] |
| 2 | 读文件 | file_read | [ ] |
| 3 | 搜索代码 | file_search | [ ] |
| 4 | 执行命令 | shell_exec | [ ] |
| 5 | 编辑文件 | file_edit | [ ] |
| 6 | 创建文件 | file_write | [ ] |
| 7 | 获取网页 | web_fetch | [ ] |
| 8 | 多轮工具链 | file_search+file_read+file_edit | [ ] |
| 9 | 连续多轮上下文 | shell_exec | [ ] |
| 10 | Esc 取消 | — | [ ] |
| C | 配置免登录 | — | [ ] |
| C | headless 免登录 | — | [ ] |
| C | TUI 正常启动 | — | [ ] |
| C | 退出后文件正确 | — | [ ] |

---

## 快速一键测试（headless，无需 TUI）

如果 TUI 有显示问题，先用 headless 模式跑核心流程：

```bash
# 1. 创建测试文件
echo "Hello World" > test_workspace\quick_test.txt

# 2. 免登录测试（不传 --api-key，靠 config.yaml）
.venv\Scripts\python -m code_agent.cli.main "读取 test_workspace/quick_test.txt，告诉我里面是什么"

# 3. 搜索测试
.venv\Scripts\python -m code_agent.cli.main "在 test_workspace/ 搜索包含 'hello' 的文件"

# 4. 执行测试
.venv\Scripts\python -m code_agent.cli.main "运行 python -c \"print('evil-code works!')\""

# 5. 多工具链测试
.venv\Scripts\python -m code_agent.cli.main "读取 test_workspace/demo.txt，把其中 'Hello' 改成 'Hi'，然后验证修改结果"
```
