# 模块 4：LLM 通信层

> 阶段: 1 | 状态: 已完成 | 创建时间: 2026-07-19

---

## 范围

| 功能 | 描述 |
|:---|:---|
| ToolDefinitionSchema | OpenAI function-calling 格式（发给模型） |
| FunctionSchema / FunctionCall | 工具描述 / 调用详情 |
| ToolCall | 模型的工具调用请求（id + type + function） |
| ToolCallDelta | 流式片段（index + id + name + arguments_fragment） |
| StreamEvent | 5 种类型: text_delta, tool_call_delta, thinking_delta, turn_end, error |
| ChatClient | 延迟初始化 + _ensure_client() 自动刷新 + chat_stream() 异步生成器 |

---

## 最终架构

```
code_agent/api/
├── __init__.py      # 导出: ChatClient, StreamEvent, ToolCall
├── schemas.py       # API 通信的 Pydantic 模型
└── client.py        # AsyncOpenAI 封装 + 认证管理
```

---

## 实现状态

| 项目 | 状态 | 文件 |
|:---|:---|:---|
| FunctionSchema (name, description, parameters) | 完成 | `api/schemas.py` |
| ToolDefinitionSchema (type="function") | 完成 | `api/schemas.py` |
| FunctionCall (name, arguments 原始 JSON) | 完成 | `api/schemas.py` |
| ToolCall (id, type, function) | 完成 | `api/schemas.py` |
| ToolCallDelta (index, id, name, arguments_fragment) | 完成 | `api/schemas.py` |
| StreamEvent (5 种 kind + turn_end 时携带 usage) | 完成 | `api/schemas.py` |
| ChatClient.__init__ (model, base_url, 不读 token) | 完成 | `api/client.py` |
| ChatClient._ensure_client() (读取->检查->刷新->创建) | 完成 | `api/client.py` |
| ChatClient.chat_stream() (异步生成器, SSE 解析) | 完成 | `api/client.py` |
| ChatClient.close() (清理 httpx 资源) | 完成 | `api/client.py` |
| 认证失败 -> AuthenticationError（不静默降级） | 完成 | `api/client.py` |

**进度: 11/11 项完成**

---

## 设计决策

- 延迟初始化：__init__ 不读 token（TUI 可能在认证前启动）
- _ensure_client() 任何认证失败均抛 AuthenticationError——不降级
- chat_stream() yield StreamEvent；Agent 按 index 累积 tool_call_delta
- thinking_delta：Phase 1 定义，模型返回推理 token 时产生
- 独立 httpx.AsyncClient 用于 token 刷新（openai SDK 不暴露其 client）
- API 响应中 401/unauthorized -> 重置 client + 抛 AuthenticationError
