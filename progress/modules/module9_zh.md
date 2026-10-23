# 模块 9：单元测试

> 阶段: 1 | 状态: 已完成 | 创建时间: 2026-07-19

---

## 范围

| 功能 | 描述 |
|:---|:---|
| conftest.py | 共享 fixtures：MockChatClient、临时目录、示例 token、事件工厂 |
| test_auth/ | Token 读/写/过期/刷新（pytest-httpx mock HTTP） |
| test_api/ | ChatClient 认证流程（_ensure_client 路径、close） |
| test_core/ | Agent 主循环 mock LLM、Session dangling 修复、消息格式 |
| test_tools/ | Registry 注册/finalize/执行、各内置工具 |

---

## 最终架构

```
tests/
├── conftest.py            # Fixtures: MockChatClient, tmp_workspace, sample_token, 事件工厂
├── test_auth/
│   ├── __init__.py
│   └── test_oauth.py      # is_token_expired, refresh_token, load/save_token (24 测试)
├── test_api/
│   ├── __init__.py
│   └── test_client.py     # _ensure_client 认证路径, close() (7 测试)
├── test_core/
│   ├── __init__.py
│   ├── test_agent.py      # Agent.run() 文本/工具调用/max_turns/hooks (9 测试)
│   └── test_session.py    # Session add/to_api/repair (14 测试)
└── test_tools/
    ├── __init__.py
    ├── test_registry.py   # register/finalize/execute/truncate (12 测试)
    └── test_builtin.py    # file_read/file_edit/file_write/shell_exec/file_search (15 测试)
```

总计: 81 个测试

---

## 实现状态

| 项目 | 状态 | 文件 |
|:---|:---|:---|
| conftest.py (MockChatClient, 临时目录, sample token, 工厂) | 完成 | `tests/conftest.py` |
| test_auth: is_token_expired (过期/有效/buffer/无expiry) | 完成 | `tests/test_auth/test_oauth.py` |
| test_auth: refresh_token (成功/无轮转/缺字段/HTTP错误/非法JSON) | 完成 | `tests/test_auth/test_oauth.py` |
| test_auth: load_token/save_token (缺失/非法/保留keys/创建目录) | 完成 | `tests/test_auth/test_oauth.py` |
| test_api: _ensure_client (无凭证/有效/过期+刷新/失败/缓存) | 完成 | `tests/test_api/test_client.py` |
| test_core: Agent.run() 单轮文本响应 | 完成 | `tests/test_core/test_agent.py` |
| test_core: Agent.run() 多轮工具调用链 | 完成 | `tests/test_core/test_agent.py` |
| test_core: Agent.run() MaxTurnsReached | 完成 | `tests/test_core/test_agent.py` |
| test_core: Hook 管线 (DENY / 爆炸hook第二层防御) | 完成 | `tests/test_core/test_agent.py` |
| test_core: Session dangling 修复 (4步: 配对/悬空/多个/去重) | 完成 | `tests/test_core/test_session.py` |
| test_core: Session to_api_messages 格式 (所有消息类型) | 完成 | `tests/test_core/test_session.py` |
| test_tools: Registry 注册 + finalize 锁 + definitions 格式 | 完成 | `tests/test_tools/test_registry.py` |
| test_tools: Registry 执行 (有效/无效/缺失/异常/str归一化) | 完成 | `tests/test_tools/test_registry.py` |
| test_tools: Registry 截断 (未超/超限/error豁免) | 完成 | `tests/test_tools/test_registry.py` |
| test_tools: file_read (正常/二进制/offset+limit/缺失/空文件) | 完成 | `tests/test_tools/test_builtin.py` |
| test_tools: file_edit (唯一/多处/replace_all/未找到/缺文件) | 完成 | `tests/test_tools/test_builtin.py` |
| test_tools: file_write (创建/覆盖/mkdir) | 完成 | `tests/test_tools/test_builtin.py` |
| test_tools: shell_exec (成功/错误退出/超时/workdir) | 完成 | `tests/test_tools/test_builtin.py` |
| test_tools: file_search (匹配/无匹配/忽略大小写/glob过滤) | 完成 | `tests/test_tools/test_builtin.py` |

**进度: 19/19 项完成**

---

## 设计决策

- pytest + pytest-asyncio (asyncio_mode = "auto")
- pytest-httpx 用于 mock HTTP（token 刷新）
- MockChatClient 产出预定义 StreamEvent 序列（无真实 LLM 调用）
- tmp_path fixture 用于文件类工具测试
- web_fetch 不纳入单元测试（DNS/重定向 SSRF 逻辑需集成级 mock）
- 循环导入修复：core/__init__.py 和 api/__init__.py 不再 re-export Agent/ChatClient（打断 api↔core 循环）
