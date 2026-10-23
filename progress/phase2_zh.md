# Phase 2：会话 + 记忆 + 压缩 - 进度

> 目标: 会话持久化、对话压缩、跨会话记忆
> 预估工时: 5-7 天
> 状态: 已完成 (8/8 模块)
> 依赖: Phase 1 完成

---

## 范围

1. **会话存储**: JSONL 持久化 + Delta Replay + crash-safe 追加写
2. **对话压缩**: Token 阈值触发 + LLM 摘要生成 + 历史替换
3. **跨会话记忆**: SQLite FTS5 索引 + Markdown 分块 + 跨会话召回
4. **认证扩展**: 多账户保险库（可选）

---

## 最终架构（Phase 2 新增）

```
code_agent/
├── storage/                  # 新增: 会话 JSONL 持久化
│   ├── __init__.py           #   包导出
│   ├── session_store.py      #   读/写/追加 + Delta Replay + torn-tail 修复
│   ├── models.py             #   SessionMeta (summary.json 模型)
│   └── file_lock.py          #   跨平台文件锁 (fcntl/msvcrt)
├── memory/                   # 新增: 跨会话记忆
│   ├── __init__.py           #   包导出
│   ├── index_db.py           #   SQLite + FTS5 索引管理 (运行时检测 FTS5, 降级 LIKE)
│   ├── schema.sql            #   DDL (CREATE TABLE chunks, chunks_fts)
│   ├── search.py             #   混合搜索 (FTS5 关键词 -> 排序 -> 过滤)
│   ├── chunker.py            #   Markdown ## 标题分块
│   └── file_store.py         #   MEMORY.md 文件读/写 + session log
├── compaction/               # 新增: 对话压缩
│   ├── __init__.py           #   包导出 (CompactionPolicy, generate_summary, assemble_compacted_history)
│   ├── policy.py             #   触发策略（token 阈值判断）
│   ├── summarizer.py         #   LLM 摘要生成
│   └── assembler.py          #   组装压缩后的对话历史
├── hooks/                    # 新增: AgentHook 实现
│   ├── __init__.py           #   包导出
│   └── phase2_hooks.py       #   MemoryHook, CompactionHook, PersistenceHook, EditTrackerHook
├── core/
│   └── reminders.py          # 新增: system-reminder 注入器 (8 种 reminder 类型)
└── auth/
    └── vault.py              # 新增: 多账户保险库（从 cc-switch 移植）
```

---

## Phase 2 新增的 Hook

| Hook 类 | 方法 | 职责 |
|:---|:---|:---|
| MemoryHook | on_session_start | 注入跨会话记忆（首次 turn，SQLite 查询失败降级） |
| CompactionHook | on_turn_start | 检查 token 阈值，触发压缩 + 历史替换 |
| PersistenceHook | on_turn_end | 持久化会话到磁盘（增量追加，I/O 失败不阻断） |
| EditTrackerHook | on_tool_result | 追踪 file_edit / file_write 编辑的文件 |

---

## 模块进度

### 模块 12: 会话存储 [已完成]

| 项目 | 状态 | 位置 |
|:---|:---|:---|
| SessionStore (init/append/load/find_latest) | 完成 | `storage/session_store.py` |
| DeltaIterator (增量读取) | 完成 | `storage/session_store.py` |
| torn-tail 修复 (崩溃恢复) | 完成 | `storage/session_store.py` |
| SessionMeta (summary.json 模型) | 完成 | `storage/models.py` |
| FileLock (跨进程锁, fcntl/msvcrt) | 完成 | `storage/file_lock.py` |
| encode_cwd / _generate_session_id | 完成 | `storage/session_store.py` |
| 测试: SessionStore CRUD + DeltaIterator (12 测试) | 完成 | `tests/test_storage/test_session_store.py` |
| 测试: FileLock 同步/异步/超时/并发 (7 测试) | 完成 | `tests/test_storage/test_file_lock.py` |

### 模块 13: 跨会话记忆 [已完成]

| 项目 | 状态 | 位置 |
|:---|:---|:---|
| MemoryIndex (SQLite + FTS5 索引) | 完成 | `memory/index_db.py` |
| FTS5 运行时检测 + LIKE 降级 | 完成 | `memory/index_db.py` |
| blake3 hash 去重 (reindex) | 完成 | `memory/index_db.py` |
| schema.sql (chunks + chunks_fts DDL) | 完成 | `memory/schema.sql` |
| search_memory (混合搜索 + 来源权重) | 完成 | `memory/search.py` |
| chunk_markdown (## 标题分块) | 完成 | `memory/chunker.py` |
| MemoryStore (global/workspace/session log) | 完成 | `memory/file_store.py` |
| 测试: chunker (9 测试) | 完成 | `tests/test_memory/test_chunker.py` |
| 测试: index_db (10 测试) | 完成 | `tests/test_memory/test_index_db.py` |
| 测试: search (6 测试) | 完成 | `tests/test_memory/test_search.py` |
| 测试: file_store (7 测试) | 完成 | `tests/test_memory/test_file_store.py` |

### 模块 14: 对话压缩 [已完成]

| 项目 | 状态 | 位置 |
|:---|:---|:---|
| CompactionPolicy (should_compact + target_budget) | 完成 | `compaction/policy.py` |
| generate_summary (LLM 摘要) | 完成 | `compaction/summarizer.py` |
| assemble_compacted_history (历史组装) | 完成 | `compaction/assembler.py` |
| classify_compact_error (错误分类) | 完成 | `compaction/summarizer.py` |
| count_preserved_turns (保留最近 N 轮) | 完成 | `compaction/assembler.py` |
| 测试: policy (7 测试) | 完成 | `tests/test_compaction/test_policy.py` |
| 测试: assembler (5 测试) | 完成 | `tests/test_compaction/test_assembler.py` |
| 测试: summarizer (7 测试) | 完成 | `tests/test_compaction/test_summarizer.py` |

### 模块 15: ReminderInjector [已完成]

| 项目 | 状态 | 位置 |
|:---|:---|:---|
| ReminderInjector (8 种 reminder 类型) | 完成 | `core/reminders.py` |
| compaction_reminder (压缩后注入) | 完成 | `core/reminders.py` |
| memory_reminder (跨会话记忆注入) | 完成 | `core/reminders.py` |
| 测试: 8 种 reminder + 空状态 + 限制 (15 测试) | 完成 | `tests/test_reminders/test_reminders.py` |

### 模块 16: EditTrackerHook [已完成]

| 项目 | 状态 | 位置 |
|:---|:---|:---|
| EditTrackerHook (追踪 file_edit / file_write) | 完成 | `hooks/phase2_hooks.py` |
| get_edited_files / clear | 完成 | `hooks/phase2_hooks.py` |
| 测试: 追踪/去重/忽略错误/清空 (7 测试) | 完成 | `tests/test_hooks/test_phase2_hooks.py` |

### 模块 17: 多账户保险库 [已完成]

| 项目 | 状态 | 位置 |
|:---|:---|:---|
| AccountVault (add/remove/switch_to/list) | 完成 | `auth/vault.py` |
| switch_to (先刷新过期 token, 网络调用在锁外) | 完成 | `auth/vault.py` |
| next_in_rotation (轮换) | 完成 | `auth/vault.py` |
| 测试: CRUD/switch/rotation/edge cases (16 测试) | 完成 | `tests/test_vault/test_vault.py` |

### 模块 18: Session 扩展 [已完成]

| 项目 | 状态 | 位置 |
|:---|:---|:---|
| Session.replace_history (压缩后替换历史) | 完成 | `core/session.py` |
| Session.from_dicts (API 消息恢复为 Message) | 完成 | `core/session.py` |

### 模块 19: CLI 集成 + 测试 [已完成]

| 项目 | 状态 | 位置 |
|:---|:---|:---|
| --resume <id> 恢复会话 | 完成 | `cli/main.py` |
| --continue 恢复最近会话 | 完成 | `cli/main.py` |
| --no-memory / --no-compaction / --no-persist | 完成 | `cli/main.py` |
| _build_agent() Phase 2 hook 组装 | 完成 | `cli/main.py` |
| _resolve_session() (new/resume by ID/resume latest) | 完成 | `cli/main.py` |
| _init_phase2_modules() (懒加载 + 错误韧性) | 完成 | `cli/main.py` |
| /sessions REPL 命令 | 完成 | `cli/main.py` |
| MemoryHook / CompactionHook / PersistenceHook | 完成 | `hooks/phase2_hooks.py` |
| 测试: hooks (PersistenceHook + MemoryHook, 7 测试) | 完成 | `tests/test_hooks/test_phase2_hooks.py` |

---

## 验证标准（来自设计文档）

**P0（无此不可用）**:
- [x] `--resume <id>` 恢复会话
- [x] 长对话自动触发压缩（token 超 85% 阈值）
- [x] SessionStore.append_message() 追加单行 JSON，torn-tail 修复
- [x] SessionStore.load_session() 成功恢复完整对话历史
- [x] DeltaIterator 从指定偏移量只读新增行
- [x] CompactionPolicy.should_compact() token 超阈值返回 True
- [x] assemble_compacted_history() 输出格式: [system, user(summary), ...recent]
- [x] 压缩后 Agent 继续推理不丢失上下文
- [x] Agent(session, client, tools, hooks=hooks) hooks 列表含 Phase 2 四个 Hook 时正常启动
- [x] hooks 列表为空时（Phase 1 兼容）所有持久化/压缩/记忆功能静默跳过

**P1（显著体验提升）**:
- [x] 跨会话记忆召回正常工作
- [x] MemoryIndex.search_fts() 返回排名结果
- [x] FTS5 不可用时自动降级为 LIKE 查询（不崩溃、结果正确）
- [x] 同一文件两次 reindex，blake3 未变则跳过
- [x] AccountVault.add_account() 写入 vault.json
- [x] AccountVault.switch_to() 先刷新过期 token 再切换
- [x] switch_to 中网络刷新在文件锁外进行（不持锁做网络调用）
- [x] MemoryHook.on_session_start → 首次 turn 注入跨会话记忆 context
- [x] CompactionHook.on_turn_start → token 超阈值时触发压缩
- [x] PersistenceHook.on_turn_end → 每 turn 结束自动持久化会话
- [x] EditTrackerHook.on_tool_result → file_edit/file_write 执行后记录编辑文件

**P2（锦上添花）**:
- [x] 多账户切换
- [x] FileLock 跨进程不冲突（两个进程同时写不同 session）
- [x] 崩溃恢复：写入中间 kill 进程，重启后 JSONL 不损坏
- [x] /sessions REPL 命令列出已保存会话

---

## 测试统计

| 测试文件 | 测试数 | 覆盖范围 |
|:---|:---|:---|
| `tests/test_storage/test_session_store.py` | 12 | SessionStore CRUD, DeltaIterator, encode_cwd |
| `tests/test_storage/test_file_lock.py` | 7 | 同步/异步锁, 超时, 并发 |
| `tests/test_memory/test_chunker.py` | 9 | H2 分块, hash, 超大分块, 空输入 |
| `tests/test_memory/test_index_db.py` | 10 | open/close, reindex, search, LIKE 降级 |
| `tests/test_memory/test_search.py` | 6 | 搜索管道, 来源权重, 过滤, 限制 |
| `tests/test_memory/test_file_store.py` | 7 | global/workspace 读写, session log |
| `tests/test_compaction/test_policy.py` | 7 | should_compact, target_budget, 边界 |
| `tests/test_compaction/test_assembler.py` | 5 | 结构, 规则, reminders, XML 标签 |
| `tests/test_compaction/test_summarizer.py` | 7 | 错误分类, 清理摘要, 适配对话 |
| `tests/test_reminders/test_reminders.py` | 15 | 8 种 reminder, 空状态, 限制 |
| `tests/test_hooks/test_phase2_hooks.py` | 14 | EditTracker (7), Persistence (3), Memory (2), Compaction (2) |
| `tests/test_vault/test_vault.py` | 16 | CRUD, switch, rotation, edge cases |
| **Phase 2 新增** | **115** | |
| **Phase 1 已有** | **157** | 无回归 |
| **总计** | **305** | 全部通过 |

---

## 依赖 (Phase 2 新增)

```
pip install aiofiles          # 异步文件 I/O
pip install aiosqlite>=0.20   # 异步 SQLite
```

---

## 关键设计决策

- **JSONL 追加写**: 单行 JSON per message，torn-tail 修复（最后字节非换行则补换行）
- **FTS5 运行时检测**: 取决于 Python 发行版的 sqlite3 编译选项，不可用时降级为 LIKE 查询
- **blake3 hash 去重**: 同一文件未修改则跳过 reindex
- **压缩保留最近 N 轮**: preserve_turns=4，避免丢失最近上下文
- **Hook 异常降级**: 所有 Phase 2 Hook 遵循 catch + log.warning + 继续策略，不阻断 Agent 主循环
- **Agent 构造函数不变**: Phase 2 功能通过 _build_agent() 注入 hooks，Agent 源码零修改
- **EditTrackerHook 追踪 file_edit / file_write**: 实际注册的工具名，shell_exec 不追踪（副作用不可靠解析）
