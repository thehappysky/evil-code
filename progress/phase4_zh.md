# Phase 4：完整生态 - 进度

> 目标: 权限控制、沙箱、MCP、插件、Hook、遥测
> 预估工时: 10-14 天
> 状态: 未开始
> 依赖: Phase 3 完成

---

## 范围

1. **权限系统**: 3 层管道（YOLO -> 安全命令 -> 询问用户）
2. **沙箱**: Docker 容器执行（可选，优雅降级）
3. **MCP**: JSON-RPC 客户端（stdio/HTTP 传输）+ 工具注册
4. **插件**: importlib 发现 + plugin.toml 清单 + 工具/技能/hook 贡献
5. **Hook 系统**: 生命周期事件分发器（PreToolUse, PostToolUse 等）
6. **遥测**: 结构化 JSON 日志 + token 使用统计
7. **认证扩展**: DPAPI 加密（从 cc-switch 移植）

---

## 最终架构（Phase 4 新增）

```
code_agent/
├── permission/        # 新增: 权限系统
│   ├── manager.py     #   3 层决策管道
│   ├── safe_commands.py # 安全命令白名单
│   └── rules.py       #   --allow/--deny glob 规则
├── sandbox/           # 新增: 沙箱（可选）
│   └── docker.py      #   Docker 容器执行
├── mcp/               # 新增: MCP 集成
│   ├── client.py      #   JSON-RPC 客户端
│   ├── transport.py   #   stdio / HTTP 传输
│   └── registry.py    #   MCP 工具 -> ToolRegistry 桥接
├── plugins/           # 新增: 插件系统
│   ├── loader.py      #   importlib 发现 + 加载
│   └── types.py       #   PluginManifest 模型
├── hooks/             # 新增: Hook 系统
│   └── dispatcher.py  #   生命周期事件分发
├── telemetry/         # 新增: 遥测（可选）
│   ├── logger.py      #   结构化 JSON 日志
│   └── usage.py       #   Token 使用统计
└── auth/
    └── crypto.py      # 新增: DPAPI 加密
```

---

## Phase 4 新增的 Hook

| Hook 类 | 方法 | 职责 |
|:---|:---|:---|
| PermissionHook | on_tool_call | 权限决策管道 |
| LifecycleHook | on_tool_call, on_tool_result, on_session_start, on_turn_end | 用户定义的 Hook 脚本 |

---

## 模块进度

（Phase 4 启动后填充）

---

## 验证标准

**P0**: --yolo 跳过所有权限提示；MCP 工具可发现和调用；headless JSON 输出可用
**P1**: 插件能贡献新工具
**P2**: Docker 沙箱隔离执行（未安装 Docker 时自动降级）
