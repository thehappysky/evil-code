# 模块 1：项目骨架 + 工具函数

> 阶段: 1 | 状态: 已完成 | 创建时间: 2026-07-19

---

## 范围

| 功能 | 描述 |
|:---|:---|
| pyproject.toml | 构建配置 (hatchling)、依赖声明、CLI 入口点 |
| 包结构 | 6 个子包: auth/, api/, core/, tools/, cli/, helpers/ + tests/ |
| estimate_tokens() | bytes/4 启发式 token 估算 |
| exceeds_threshold() | 判断是否超过上下文窗口百分比阈值 |
| truncate_with_preview() | 保留头部 + footer 提示（行数/字符数统计） |
| truncate_middle() | 保留头尾，中间用省略号替代 |
| atomic_write() | 共享原子写入（临时文件 + fsync + os.replace） |

---

## 最终架构

```
code_agent/
├── __init__.py              # __version__ = "0.1.0"
├── helpers/
│   ├── __init__.py          # 重导出所有公开函数
│   ├── token_counter.py     # estimate_tokens, exceeds_threshold
│   ├── text_truncate.py     # truncate_with_preview, truncate_middle
│   └── file_io.py           # atomic_write（file_edit + file_write 共享）
pyproject.toml               # hatchling 构建, 依赖: openai/pydantic/httpx/markdownify
```

---

## 实现状态

| 项目 | 状态 | 文件 |
|:---|:---|:---|
| pyproject.toml | 完成 | `pyproject.toml` |
| 包 __init__.py (全部 6 个 + builtin) | 完成 | `code_agent/*/__init__.py` |
| estimate_tokens() | 完成 | `code_agent/helpers/token_counter.py` |
| exceeds_threshold() | 完成 | `code_agent/helpers/token_counter.py` |
| truncate_with_preview() | 完成 | `code_agent/helpers/text_truncate.py` |
| truncate_middle() | 完成 | `code_agent/helpers/text_truncate.py` |
| atomic_write() | 完成 | `code_agent/helpers/file_io.py` |

**进度: 7/7 项完成**

---

## 设计决策

- helpers/ 是纯函数模块，零项目内依赖（可脱离项目独立使用）
- 所有截断函数返回 `(text, was_truncated)` 元组，让调用方感知截断
- truncate_middle 尝试在行边界断开以提高可读性
- pyproject.toml 使用 hatchling（现代、配置精简）

### 设计文档差异记录

| 项目 | 设计文档 (§3.13.2) | 实际实现 | 原因 |
|:---|:---|:---|:---|
| truncate_middle 返回值 | `-> str` | `-> tuple[str, bool]` | 与 truncate_with_preview 保持一致；调用方需要截断感知 |
