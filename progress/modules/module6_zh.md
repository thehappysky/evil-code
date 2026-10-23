# 模块 6：内置工具（6 个）

> 阶段: 1 | 状态: 已完成 | 创建时间: 2026-07-19

---

## 范围

| 工具 | 文件 | 核心特性 |
|:---|:---|:---|
| file_read | `builtin/file_read.py` | 二进制检测(null>10%)、行号标注、offset/limit、截断footer |
| file_search | `builtin/file_search.py` | rg优先(30s超时)、Python re回退、glob过滤、max_results |
| shell_exec | `builtin/shell_exec.py` | shlex解析(POSIX)/shell=True(Win)、asyncio子进程、超时kill |
| file_edit | `builtin/file_edit.py` | 唯一性检查、原子写入、difflib diff、1MB限制、无状态 |
| file_write | `builtin/file_write.py` | 禁止系统目录、自动mkdir、保留权限、原子写入、5MB限制 |
| web_fetch | `builtin/web_fetch.py` | SSRF三层防护、重定向重检、HTML->Markdown、256KB截断 |

---

## 最终架构

```
code_agent/tools/builtin/
├── __init__.py
├── file_read.py       # ReadFileInput -> ToolResult
├── file_search.py     # SearchInput -> ToolResult (rg 或 Python re)
├── shell_exec.py      # ShellInput -> ToolResult (asyncio 子进程)
├── file_edit.py       # SearchReplaceInput -> ToolResult (search_replace)
├── file_write.py      # WriteFileInput -> ToolResult (创建/覆盖)
└── web_fetch.py       # WebFetchInput -> ToolResult (HTML->Markdown)
```

---

## 实现状态

| 项目 | 状态 | 文件 |
|:---|:---|:---|
| file_read: 二进制检测 (8KB null 检查) | 完成 | `builtin/file_read.py` |
| file_read: 行号格式 ({line:6d}\|{content}) | 完成 | `builtin/file_read.py` |
| file_read: offset/limit + 截断 footer | 完成 | `builtin/file_read.py` |
| file_search: rg 子进程 (30s 超时) | 完成 | `builtin/file_search.py` |
| file_search: Python re 回退 (rglob + compile) | 完成 | `builtin/file_search.py` |
| file_search: include glob + ignore_case + max_results | 完成 | `builtin/file_search.py` |
| shell_exec: Windows shell=True / POSIX shlex | 完成 | `builtin/shell_exec.py` |
| shell_exec: asyncio.wait_for + 超时 proc.kill | 完成 | `builtin/shell_exec.py` |
| shell_exec: stdout+stderr 合并, 退出码报告 | 完成 | `builtin/shell_exec.py` |
| file_edit: 唯一匹配 (count==1 或 replace_all) | 完成 | `builtin/file_edit.py` |
| file_edit: 原子写入 (mkstemp+fsync+replace) | 完成 | `builtin/file_edit.py` |
| file_edit: difflib.unified_diff 输出 | 完成 | `builtin/file_edit.py` |
| file_edit: 1MB 限制 + 专项错误消息 | 完成 | `builtin/file_edit.py` |
| file_write: 禁止目录检查 | 完成 | `builtin/file_write.py` |
| file_write: 自动 mkdir -p + 保留权限 | 完成 | `builtin/file_write.py` |
| file_write: 原子写入 + 覆盖时输出 diff | 完成 | `builtin/file_write.py` |
| web_fetch: HTTP->HTTPS 自动升级 | 完成 | `builtin/web_fetch.py` |
| web_fetch: SSRF 第1层 (URL 解析) | 完成 | `builtin/web_fetch.py` |
| web_fetch: SSRF 第2层 (DNS 解析检查) | 完成 | `builtin/web_fetch.py` |
| web_fetch: SSRF 第3层 (重定向重检) | 完成 | `builtin/web_fetch.py` |
| web_fetch: markdownify HTML->Markdown | 完成 | `builtin/web_fetch.py` |
| web_fetch: 256KB 截断 | 完成 | `builtin/web_fetch.py` |
| web_fetch: prompt 参数（提取目标头部） | 完成 | `builtin/web_fetch.py` |

**进度: 23/23 项完成**

---

## 设计决策

- 每个工具是独立文件，只依赖 tools/result.py
- file_edit 无状态：工具层不强制"先读后改"；匹配失败自然引导模型自纠正
- shell_exec 在 Windows 用 shell=True（cmd.exe 兼容），POSIX 用 shlex
- web_fetch 手动处理重定向以做 SSRF 重检（follow_redirects=False）
- 所有工具返回 ToolResult（永不向 Agent 抛异常）
