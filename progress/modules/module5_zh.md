# 模块 5：工具基础设施

> 阶段: 1 | 状态: 已完成 | 创建时间: 2026-07-19

---

## 范围

| 功能 | 描述 |
|:---|:---|
| ToolResult | 统一返回类型（output + is_error + truncated），ok()/error() 工厂方法 |
| ToolDefinition | name + description + input_model + handler + to_openai_schema() |
| ToolRegistry | 装饰器注册 + finalize 锁 + execute（4步）+ 统一截断 |

---

## 最终架构

```
code_agent/tools/
├── __init__.py      # 导出: ToolRegistry, ToolDefinition, ToolResult
├── result.py        # ToolResult (Pydantic BaseModel)
├── definition.py    # ToolDefinition (从 Pydantic JSON Schema 生成 to_openai_schema)
└── registry.py      # ToolRegistry (register/finalize/execute/get_definitions)
```

---

## 实现状态

| 项目 | 状态 | 文件 |
|:---|:---|:---|
| ToolResult (output, is_error, truncated) | 完成 | `tools/result.py` |
| ToolResult.ok() / ToolResult.error() 类方法 | 完成 | `tools/result.py` |
| ToolDefinition (name, description, input_model, handler, category) | 完成 | `tools/definition.py` |
| ToolDefinition.to_openai_schema() (去除 title/$defs) | 完成 | `tools/definition.py` |
| ToolRegistry.register() 装饰器 | 完成 | `tools/registry.py` |
| ToolRegistry.finalize() 锁 | 完成 | `tools/registry.py` |
| ToolRegistry.get_definitions() (每轮调用, 不缓存) | 完成 | `tools/registry.py` |
| ToolRegistry.execute() (查找->验证->调用->截断) | 完成 | `tools/registry.py` |
| ToolRegistry._maybe_truncate() (max_output_chars=100K) | 完成 | `tools/registry.py` |
| ToolRegistry.list_names() | 完成 | `tools/registry.py` |

**进度: 10/10 项完成**

---

## 设计决策

- 装饰器注册：等价于 Rust 版 register::<T>()
- finalize() 锁：等价于 Rust 版 FinalizedToolset 不可变语义
- Pydantic model_validate() 替代 Rust 版 serde_path_to_error
- 统一截断在 registry 层（非每个工具自己截断）——单一职责
- get_definitions() 每轮调用（不缓存），支持 MCP 工具热加载
- execute() 捕获所有异常 -> ToolResult.error（永不让 Agent 崩溃）
- max_output_chars 默认 100K（构造函数可配置）
