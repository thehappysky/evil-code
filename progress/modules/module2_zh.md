# 模块 2：认证层

> 阶段: 1 | 状态: 已完成 | 创建时间: 2026-07-19

---

## 范围

| 功能 | 描述 |
|:---|:---|
| is_token_expired() | 同步检查：毫秒时间戳 + 5分钟安全缓冲 |
| refresh_token() | 异步 httpx POST，返回新 dict（不可变模式） |
| load_token() | 读取 ~/.claude/.credentials.json，任何失败返回 None |
| save_token() | 原子写入: mkstemp -> json.dump -> fsync -> os.replace -> chmod 600 |

---

## 最终架构

```
code_agent/auth/
├── __init__.py          # 导出: load_token, save_token, refresh_token, is_token_expired
├── token_refresh.py     # OAuth2 刷新（异步 httpx，纯函数，不读写文件）
└── token_store.py       # 原子文件读写（~/.claude/.credentials.json）
```

---

## 实现状态

| 项目 | 状态 | 文件 |
|:---|:---|:---|
| is_token_expired() - 同步, buffer_seconds=300 | 完成 | `auth/token_refresh.py` |
| refresh_token() - 异步, 可选 client 复用 | 完成 | `auth/token_refresh.py` |
| _TOKEN_URL / _CLIENT_ID 常量 | 完成 | `auth/token_refresh.py` |
| load_token() - 缺失/无效时优雅返回 None | 完成 | `auth/token_store.py` |
| save_token() - 保留其他键, 原子写入 | 完成 | `auth/token_store.py` |

**进度: 5/5 项完成**

---

## 设计决策

- 纯函数模块：token_refresh.py 不读写文件（调用方决定是否持久化）
- refresh_token() 返回新 dict，永不修改入参
- 错误分类：ValueError（缺 refreshToken）vs RuntimeError（HTTP/网络错误）
- save_token() 只替换 `claudeAiOauth` 键，保留 mcpOAuth 等其他顶层键
- chmod 600 包裹在 try/except 中（Windows 可能不支持）
- 依赖：httpx（openai SDK 已依赖）+ 纯标准库
