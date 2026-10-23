# 模块 8：命令行入口

> 阶段: 1 | 状态: 已完成 | 创建时间: 2026-07-19

---

## 范围

| 功能 | 描述 |
|:---|:---|
| _build_tools() | 注册 6 个内置工具 + finalize |
| _build_agent() | 核心组装函数——注入 hooks 列表（开放-封闭原则集成点） |
| _run_headless() | 单次 prompt -> 流式输出到 stdout |
| _run_interactive() | REPL 循环：输入 -> agent.run() -> 打印 |
| main() | argparse 入口 (--cwd, --model, --max-turns, 位置参数 prompt) |

---

## 最终架构

```
code_agent/cli/
├── __init__.py
└── main.py        # argparse + _build_tools + _build_agent + headless/REPL
```

---

## 实现状态

| 项目 | 状态 | 文件 |
|:---|:---|:---|
| _build_tools() - 注册 6 个工具 + finalize | 完成 | `cli/main.py` |
| _build_agent() - hook 组装（Phase 1: 空 hooks） | 完成 | `cli/main.py` |
| _run_headless() - 流式输出 text_delta 到 stdout | 完成 | `cli/main.py` |
| _run_interactive() - REPL + 优雅退出 | 完成 | `cli/main.py` |
| main() - argparse (prompt, --cwd, --model, --max-turns, --base-url) | 完成 | `cli/main.py` |

**进度: 5/5 项完成**

---

## 设计决策（来自设计文档）

- _build_agent() 是开放-封闭原则的集成点：
  Phase 1 返回空 hooks；Phase 2-4 只修改此函数追加 Hook 实例
- 组装流程: Session() -> ChatClient(model) -> _build_tools() -> _build_agent()
- Headless 模式：消费异步生成器，打印 text_delta，退出
- REPL 模式：while True 循环 + input()，处理 KeyboardInterrupt/EOFError
- CLI 参数 Phase 1: prompt (位置参数), --cwd, --model, --max-turns
