# 模块 3：数据模型层

> 阶段: 1 | 状态: 已完成 | 创建时间: 2026-07-19

---

## 范围

| 功能 | 描述 |
|:---|:---|
| AgentError | 所有 Agent 专用异常的基类 |
| MaxTurnsReached | 携带 max_turns 上下文数据 |
| AuthenticationError | 未登录 / token 无效 / 刷新被拒 |
| ToolNotFoundError | 携带 tool_name 用于精确报错 |
| SystemMessage / UserMessage / AssistantMessage / ToolResultMessage | 通过 Literal role 实现 tagged union |
| ToolCallItem | 嵌套在 AssistantMessage 中，存原始 JSON arguments |

---

## 最终架构

```
code_agent/core/
├── exceptions.py    # AgentError -> MaxTurnsReached, AuthenticationError, ToolNotFoundError
└── messages.py      # Tagged union: System/User/Assistant/ToolResult + ToolCallItem
```

---

## 实现状态

| 项目 | 状态 | 文件 |
|:---|:---|:---|
| AgentError (基类) | 完成 | `core/exceptions.py` |
| MaxTurnsReached (max_turns 属性) | 完成 | `core/exceptions.py` |
| AuthenticationError | 完成 | `core/exceptions.py` |
| ToolNotFoundError (tool_name 属性) | 完成 | `core/exceptions.py` |
| ToolCallItem (id, name, arguments 原始 JSON) | 完成 | `core/messages.py` |
| SystemMessage (role="system") | 完成 | `core/messages.py` |
| UserMessage (role="user") | 完成 | `core/messages.py` |
| AssistantMessage (content + tool_calls) | 完成 | `core/messages.py` |
| ToolResultMessage (tool_call_id, is_error) | 完成 | `core/messages.py` |
| Message 类型别名 (union) | 完成 | `core/messages.py` |

**进度: 10/10 项完成**

---

## 设计决策

- 扁平异常层次（只有一层），每个异常携带上下文数据
- Pydantic BaseModel + Literal role 实现 tagged union（替代 Rust enum）
- arguments 存原始 JSON 字符串——解析推迟到 ToolRegistry.execute()
- ToolResultMessage 的 is_error 让模型决定重试还是放弃
- 不包含 Rust 版的 BackendToolCall 和 Reasoning variant（Phase 1 范围外）
