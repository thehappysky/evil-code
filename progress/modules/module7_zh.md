# 模块 7：Agent 核心

> 阶段: 1 | 状态: 已完成 | 创建时间: 2026-07-19

---

## 范围

| 功能 | 描述 |
|:---|:---|
| Session | 对话状态：添加消息、to_api_messages、dangling修复、token估算 |
| _repair_dangling_calls() | 为未配对的工具调用注入占位 ToolResult |
| AgentHook 协议 | 5 个生命周期方法 (on_session_start/turn_start/tool_call/tool_result/turn_end) |
| HookDecision 枚举 | ALLOW / DENY / MODIFY |
| Agent.run() | 异步生成器，按 index 累积流式 tool_call，hook 调用点 |
| Agent._execute_one_tool() | JSON解析 -> hook链 -> 执行 -> on_tool_result，两层异常防御 |

---

## 最终架构

```
code_agent/core/
├── session.py     # Session 类（对话状态管理）
└── agent.py       # AgentHook 协议 + HookDecision + Agent 类
```

---

## 实现状态

| 项目 | 状态 | 文件 |
|:---|:---|:---|
| Session.__init__ (system_prompt 可选) | 完成 | `core/session.py` |
| Session.add_user_message (触发 repair) | 完成 | `core/session.py` |
| Session.add_assistant_message (text + tool_calls) | 完成 | `core/session.py` |
| Session.add_tool_result (call_id, content, is_error) | 完成 | `core/session.py` |
| Session.to_api_messages() (OpenAI 格式) | 完成 | `core/session.py` |
| Session._repair_dangling_calls() (4步) | 完成 | `core/session.py` |
| Session.prompt_count / estimated_tokens 属性 | 完成 | `core/session.py` |
| HookDecision 枚举 (ALLOW/DENY/MODIFY) | 完成 | `core/agent.py` |
| AgentHook 协议 (5 方法, runtime_checkable) | 完成 | `core/agent.py` |
| Agent.__init__ (session, client, tools, max_turns, hooks) | 完成 | `core/agent.py` |
| Agent.run() - 异步生成器 yield StreamEvent | 完成 | `core/agent.py` |
| Agent.run() - 按 index 累积 tool_call_delta | 完成 | `core/agent.py` |
| Agent.run() - hook 调用点 (session_start/turn_start/turn_end) | 完成 | `core/agent.py` |
| Agent._execute_one_tool() - JSON 解析 + hook 链 | 完成 | `core/agent.py` |
| Agent._execute_one_tool() - 防御性异常处理（第二层） | 完成 | `core/agent.py` |
| max_turns 耗尽后抛 MaxTurnsReached | 完成 | `core/agent.py` |

**进度: 16/16 项完成**

---

## 设计决策

- 不需要 Actor 模式：Python asyncio 单线程，无并发 mutation
- _repair_dangling_calls：等价于 Rust 版 ensure_conversation_integrity()
- AgentHook 是唯一扩展点（开放-封闭原则）
- 两层 hook 异常防御：第一层（hook 自 catch）+ 第二层（Agent 兜底）
- on_tool_call 异常 -> DENY（fail-safe）；其他 hook -> no-op（降级）
- 工具定义每轮重新获取（不缓存），支持 MCP 热加载
- MODIFY hook 通过引用修改 args；注册顺序 = 修改优先级
