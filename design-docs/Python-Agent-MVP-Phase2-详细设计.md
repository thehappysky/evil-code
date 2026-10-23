# Python AI Coding Agent -- Phase 2 详细设计：会话管理 + 持久化 + 记忆

> 代码逻辑参考 Grok Build 的 storage、memory、compaction 模块。
> 命名遵循 Python 社区规范，按功能描述命名。格式对齐 Phase 1 详细设计（中文叙述 + 表格 + 英文代码）。

---

## Phase 2 Goals

Build on Phase 1 (command-line Agent loop) with four new capabilities:
- **Session persistence**: JSONL storage, resume and continue support
- **Dialog compaction**: Auto-triggered transparent compression
- **Cross-session memory**: SQLite + FTS5 keyword search
- **Multi-account vault**: Ported and adapted from cc-switch

---

## 1. New Directory Structure

Phase 1 directories (auth/ api/ core/ tools/ cli/ helpers/) remain completely unchanged.
Phase 2 only adds these new directories and files:

```
code_agent/
  ... (Phase 1 files untouched)

  storage/                    # Session persistence layer
    __init__.py               #   public: SessionStore, DeltaIterator
    session_store.py          #   JSONL read/write with append + delta replay
    file_lock.py              #   Cross-platform file locking (POSIX fcntl / Windows msvcrt)
    models.py                 #   SessionMeta Pydantic model

  memory/                     # Cross-session memory layer
    __init__.py               #   public: MemoryIndex, MemoryStore, search_memory
    index_db.py               #   SQLite + FTS5 index management
    search.py                 #   Hybrid search pipeline
    chunker.py                #   Markdown chunking by H2 headers
    file_store.py             #   MEMORY.md file I/O + session log writing

  compaction/                 # Dialog compression layer
    __init__.py               #   public: CompactionPolicy, compact
    policy.py                 #   Trigger policy (token threshold + context window)
    summarizer.py             #   LLM summary generation with input fitting
    assembler.py              #   Compacted history assembly

  core/                       # New file in existing directory
    reminders.py              #   System-reminder injection for todo/date/memory context

  auth/                       # New file in existing directory
    vault.py                  #   Multi-account credential vault (from cc-switch)
```

---

## 2. storage/session_store.py

References: Rust JsonlStorageAdapter (storage/jsonl/mod.rs)

### Class: SessionStore

```
class SessionStore:
    """JSONL session persistence.

    Directory layout:
    ~/.code-agent/sessions/{url_encoded_cwd}/{session_id}/
      chat_history.jsonl   -- One ConversationItem JSON per line
      updates.jsonl        -- SessionUpdate stream (for delta replay)
      summary.json         -- SessionMeta metadata
    """

    def __init__(self, root_dir: str | Path | None = None): ...

    # Write operations
    async def init_session(self, meta: SessionMeta) -> None:
        """Create session directory and write initial summary.json."""

    async def append_message(self, session_id: str, message: dict) -> None:
        """Append a single message to chat_history.jsonl.
        Checks last byte is newline before append (torn-tail repair).
        Uses aiofiles for async I/O."""

    async def update_meta(self, session_id: str, patch: dict) -> None:
        """Atomic update of summary.json using file lock."""

    # Read operations
    async def load_session(self, session_id: str) -> tuple[SessionMeta, list[dict]]:
        """Load full session: summary.json + chat_history.jsonl."""

    async def load_delta(self, session_id: str, offset: int) -> list[dict]:
        """Load only messages appended after a known byte offset."""

    def list_sessions(self) -> list[SessionMeta]:
        """List all saved sessions sorted by last update time."""

    def find_latest(self, cwd: str) -> SessionMeta | None:
        """Find most recent session for a given working directory."""
```

### Class: DeltaIterator

References: Rust UpdatesIterator (storage/mod.rs:26)

```
class DeltaIterator:
    """Line-by-line JSONL reader with byte offset tracking.

    Uses BufReader + seek(offset) for efficient partial reads.
    Each call to __anext__() reads and parses one JSON line.
    """

    def __init__(self, path: Path, offset: int = 0): ...
    def __aiter__(self): ...
    async def __anext__(self) -> dict: ...
    @property
    def current_offset(self) -> int:
        """Current byte position. Used to save checkpoint for next delta read."""
```

### Design Decisions

- **Append-first architecture**: O(1) writes with no transactions needed. Equivalent to Rust JSONL strategy.
- **Atomic writes**: temp file + os.fsync + os.replace pattern for summary.json. For chat_history.jsonl, append with torn-tail check.
- **Delta Replay**: SeekFrom::Start(offset) enables efficient resume without re-reading entire history.
- **Why not SQLite?** Session data is a linear append-only stream. JSONL is simpler to debug (tail -f), backup (cp), and migrate.

---

## 3. memory/index_db.py

References: Rust MemoryIndex (memory/src/index.rs)

### Class: MemoryIndex

```
class MemoryIndex:
    """SQLite + FTS5 memory chunk index.

    Database: ~/.code-agent/memory/{project_hash}/index.db

    Tables:
      chunks       -- text chunks with metadata (id, path, text, hash, source, etc.)
      chunks_fts   -- FTS5 virtual table for BM25 keyword search (content='')

    FTS5 兼容性：运行时检测 FTS5 是否可用（取决于 Python 发行版的 sqlite3 编译选项）。
    若不可用，自动降级为 LIKE 查询——功能不变，全文搜索性能下降但不影响正确性。
    """

    def __init__(self, db_path: Path):
        self._fts5_available: bool = False  # 运行时检测

    async def open(self) -> None:
        # 检测 FTS5 是否可用
        cursor = await self._db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='fts5'"
        )
        self._fts5_available = (await cursor.fetchone()) is not None

        if not self._fts5_available:
            logger.warning(
                "FTS5 不可用，降级为 LIKE 查询。"
                "全文搜索性能将下降，但不影响功能。"
            )

    async def close(self) -> None: ...

    async def reindex_file(self, path: Path) -> int:
        """Index a MEMORY.md file by chunking it and upserting chunks.
        Uses blake3 hash for change detection -- only updates changed chunks.
        Returns number of chunks added/updated."""

    async def search_fts(self, query: str, limit: int = 20) -> list[dict]:
        """FTS5 BM25 keyword search，FTS5 不可用时自动降级为 LIKE。"""
        if self._fts5_available:
            return await self._search_fts5(query, limit)
        else:
            return await self._search_like(query, limit)

    async def _search_fts5(self, query: str, limit: int) -> list[dict]:
        """FTS5 BM25 全文搜索。"""
        ...

    async def _search_like(self, query: str, limit: int) -> list[dict]:
        """LIKE fallback——功能正确但比 FTS5 慢。"""
        cursor = await self._db.execute(
            "SELECT rowid, path, text, source FROM chunks "
            "WHERE text LIKE ? ORDER BY rowid DESC LIMIT ?",
            (f"%{query}%", limit),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def delete_chunks_for_file(self, path: Path) -> int:
        """Remove all chunks belonging to a deleted/modified file."""

    async def get_chunk_count(self) -> int:
        """Total number of indexed chunks."""
```

### Schema (memory/schema.sql)

```sql
CREATE TABLE IF NOT EXISTS chunks (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT UNIQUE NOT NULL,          -- "{path}:{chunk_index}"
    path TEXT NOT NULL,               -- source file path
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    text TEXT NOT NULL,               -- chunk content
    hash TEXT NOT NULL,               -- blake3 hex digest
    source TEXT NOT NULL,             -- "global" | "workspace" | "session"
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    access_count INTEGER DEFAULT 0,
    last_accessed INTEGER
);

CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);
CREATE INDEX IF NOT EXISTS idx_chunks_hash ON chunks(hash);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='');
```

### Design Decisions

- **aiosqlite** for async database access -- non-blocking event loop
- **blake3 hash** for change detection -- only expensive chunking when content actually changes
- **FTS5 content='' (contentless table)**: data lives in chunks table, FTS only stores index tokens. Reduces storage by ~50%
- **Why not sqlite-vec in Phase 2?** Vector search requires external embedding API calls. FTS5 keyword search is sufficient for Phase 2. Upgrade path exists.

---

## 4. memory/search.py

References: Rust search.rs hybrid search pipeline

### Types

```
@dataclass
class SearchResult:
    chunk_id: str
    path: str
    text: str
    source: str       # "global" | "workspace" | "session"
    score: float
```

### Function: search_memory

```
async def search_memory(
    index: MemoryIndex,
    query: str,
    source_filter: str | None = None,
    limit: int = 10,
) -> list[SearchResult]:
    """Hybrid search pipeline:

    1. FTS5 keyword search (always available)
    2. Filter empty/template chunks (is_content_free check)
    3. Rank by source weight: global(1.0) > workspace(0.8) > session(0.5)
    4. Truncate to limit
    """
```

---

## 5. memory/chunker.py

References: Rust chunker.rs

### Types

```
@dataclass
class Chunk:
    id: str              # auto-generated from path + index
    text: str            # chunk content
    start_line: int      # line in source file
    end_line: int        # line in source file
    hash: str            # blake3 hex digest
```

### Function: chunk_markdown

```
def chunk_markdown(
    text: str,
    source_path: str,
    max_chars: int = 16000,
) -> list[Chunk]:
    """Split Markdown text into indexed chunks.

    Strategy:
    1. Primary split: on ## (H2) header boundaries
    2. Secondary split: oversized chunks (> max_chars) split on paragraph
       boundaries (double newline)
    3. Each chunk records its line range in the source file
    4. Chunk IDs are deterministically generated from path + position
    """
```

---

## 6. memory/file_store.py

### Class: MemoryStore

```
class MemoryStore:
    """MEMORY.md file I/O and session log management.

    Directory layout:
    ~/.code-agent/memory/
      MEMORY.md                  -- global memory (user preferences/habits)
      {project_hash}/            -- per-workspace memory directory
        MEMORY.md                -- project-level memory
        index.db                 -- SQLite search index
        sessions/                -- auto-generated session logs
          YYYY-MM-DD-{slug}-{session_id_prefix}.md
    """

    def __init__(self, root_dir: Path | None = None): ...

    async def read_global(self) -> str:
        """Read global MEMORY.md content."""

    async def read_workspace(self, cwd: Path) -> str:
        """Read per-project MEMORY.md content.
        Project identity: git remote origin > canonicalized path."""

    async def write_workspace(self, cwd: Path, content: str) -> None:
        """Write per-project MEMORY.md (atomic)."""

    async def append_session_log(
        self, cwd: Path, session_id: str, first_message: str, content: str
    ) -> None:
        """Append to daily session log file.
        Format: {date}-{slug}-{sid_prefix}.md
        Each append is prefixed with <!-- flush HH:MM:SS UTC --> timestamp."""
```

---

## 7. compaction/policy.py

References: Rust auto-compact threshold logic

### Class: CompactionPolicy

```
class CompactionPolicy:
    """Trigger policy for automatic dialog compaction.

    Parameters:
        context_window: LLM context window size in tokens (default 200k)
        threshold_percent: trigger percentage (default 85)
        min_tokens: minimum tokens before even considering compaction (default 5000)
    """

    def __init__(
        self,
        context_window: int = 200_000,
        threshold_percent: int = 85,
        min_tokens: int = 5000,
    ): ...

    def should_compact(self, estimated_tokens: int) -> bool:
        """Check if compaction should be triggered.
        Returns True when estimated_tokens >= (context_window * threshold_percent / 100)."""

    def target_budget(self) -> int:
        """Post-compaction target token count.
        Returns context_window * 50% -- leaves room for new messages."""
```

**设计决策**：
- `context_window` 默认 200_000 仅为 fallback 值。实际组装时（`_build_agent()`），应从模型元数据动态获取：
  openai SDK 的 `client.models.retrieve(model_id)` 或维护一份 `MODEL_CONTEXT_WINDOWS` 映射表。
  不同模型差异极大（GPT-4o 128k、Claude 200k、Gemini 1M），硬编码会导致小窗口模型过早/过晚触发压缩。
- 若动态获取失败（网络不可达、未知模型），降级为默认 200k 并 log.warning

---

## 8. compaction/summarizer.py

References: Rust generate_session_compact() (helpers/session_compact.rs)

### Functions

```
async def generate_summary(
    client: ChatClient,
    conversation: list[dict],
    system_prompt: str,
) -> str:
    """Summarize conversation using a dedicated LLM call.

    Simplified input ladder (Phase 2):
    1. Fit: trim conversation to context_window * 70% - tool_tokens
       (keeps system prompt, last user query, and as much prefix as fits)
    2. Send to LLM with summarization prompt:
       "Summarize this conversation for another AI assistant
        to continue working. Include: user's request, work done,
        file paths and code details, errors and solutions,
        remaining tasks. Return only the summary."
    3. Return cleaned summary text (strip scratchpad/analysis blocks)
    """


async def classify_compact_error(error: Exception) -> str:
    """Classify compaction failure for retry logic.

    Returns "deterministic" (no retry) or "transient" (exponential backoff).

    Deterministic: auth error, invalid config, schema violation
    Transient: network error, server 500-range errors, stream interruption
    """
```

---

## 9. compaction/assembler.py

References: Rust build_compacted_history() (compaction_utils.rs:828)

### Function: assemble_compacted_history

```
def assemble_compacted_history(
    system_prompt: str,
    summary: str,
    recent_messages: list[dict],
    reminders: str | None = None,
) -> list[dict]:
    """Assemble post-compaction conversation history.

    Result structure:
    [system_message, user(project_rules), user(summary), ...recent_messages, user(reminders)]

    The summary is injected as a synthetic user message so the model sees it
    as context rather than instruction. Recent messages (last N turns) are
    preserved verbatim so the model has immediate context continuity.
    """
```

---

## 10. core/reminders.py

References: Rust reminders.rs (session/acp_session_impl/reminders.rs)

### Class: ReminderInjector

```
class ReminderInjector:
    """System-reminder injection into conversation.

    Injects <system-reminder> blocks as synthetic user messages.
    Each reminder is wrapped in <system-reminder>...</system-reminder> tags.
    """

    def __init__(self): ...

    def todo_reminder(self, todos: list[dict]) -> str | None:
        """Generate reminder if there are unfinished todos.
        Format: "You have N pending tasks: ..." """

    def date_rollover(self, last_announced_date: str) -> str | None:
        """Generate date reminder if local date changed since last announcement.
        One-shot per calendar day."""

    def compaction_reminder(
        self,
        edited_files: list[str],
        running_tasks: list[str],
    ) -> str:
        """Generate post-compaction state summary.
        Includes: edited files, running background tasks, connected MCP servers."""

    def memory_reminder(self, results: list[SearchResult]) -> str | None:
        """Format memory search results as <memory-context> block.
        Injected once per session at start of first turn."""

    def interrupt_reminder(self) -> str:
        """Generate reminder after user cancelled mid-turn.
        "[Request interrupted by user]" -- injected on next real user turn."""
```

---

## 11. auth/vault.py

Ported from cc-switch vault.py, adapted for code_agent package naming.

### Class: AccountVault

```
class AccountVault:
    """Multi-account credential vault.

    Storage: ~/.code-agent/vault.json

    Format:
    {
      "accounts": {"name": {"token": {...OAuth blob...}, "label": "work"}},
      "active": "name",
      "rotation": ["name1", "name2"]
    }

    Security:
    - File permissions: 0600 (user-only read/write)
    - Atomic writes: temp file + os.replace + os.fsync
    - Cross-process file locking (same as storage/file_lock.py)
    """

    def __init__(self, vault_dir: Path | None = None): ...

    def add_account(self, name: str, token: dict, label: str = "") -> None:
        """添加账户到保险库。

        当前依赖已有的 live token（从 Claude Code 凭证文件读取或用户手动提供）。
        Growth Path：未来可在此扩展独立 OAuth device-flow 登录流程，
        解除对 Claude Code 凭证文件的首次依赖。"""
        ...
    def remove_account(self, name: str) -> bool: ...
    def list_accounts(self) -> list[dict]: ...
    def get_active(self) -> dict | None: ...

    def switch_to(self, name: str) -> dict:
        """Switch active account.

        Steps (matching cc-switch switch_to() exactly):
        1. Pre-lock: check target token expiry -> refresh if needed
           (network call happens OUTSIDE file lock)
        2. Lock: checkpoint active account (sync live token back to vault)
        3. Lock: apply refreshed token to vault entry
        4. Lock: set active = target name
        5. Lock: save vault
        6. Write live credentials file (via auth/token_store.py)
        """

    def next_in_rotation(self, skip: set[str] | None = None) -> str | None:
        """Find next account in rotation order.
        Starts from active+1, optionally skipping a set of names."""
```

### vault 与 token_store 的共存规则

Phase 1 的 `auth/token_store.py` 读写 `~/.claude/.credentials.json`（Claude Code 兼容格式）。
Phase 2 的 `AccountVault` 管理 `~/.code-agent/vault.json`（多账户保险库）。
两者同时存在，关系如下：

```
~/.code-agent/vault.json        ← Source of Truth（所有账户的 token）
~/.claude/.credentials.json     ← Live File（Claude Code 当前使用的 token）

vault 是"数据库"——存了多个账户的 token。
token_store 是"输出端"——只写当前活跃账户的 token 到 live file。

共存规则：
1. 单账户模式（Phase 1）：只使用 token_store。没有 vault。
   ChatClient._ensure_client() 直接从 live file 读 token。

2. 多账户模式（Phase 2，vault 存在时）：
   - vault.switch_to(name) 的第 6 步调用 token_store.save_token(token)
     将目标账户的 token 写入 live file
   - ChatClient 仍然从 live file 读 token（行为不变）
   - vault.load() 从 VAULT 读，token_store.load_token() 从 LIVE FILE 读——两者不混淆

3. 冲突处理：
   - 如果 vault.json 和 .credentials.json 都有 token 但内容不同：
     LIVE FILE 的 token 优先（因为它被 Claude Code 实时刷新，
     vault 里的可能是过期的）
   - switch_to() 时的 checkpoint 步骤（第 2 步）正是解决这个冲突：
     先将 live file 的最新 token 同步回 vault，再切换

4. 数据流：
   vault.add_account(name, live_oauth)  ← Phase 1 的 token_store.load_token()
   vault.switch_to(name)
     → 步骤 6: token_store.save_token(target_oauth)  ← 写回到 live file
   ChatClient._ensure_client()
     → token_store.load_token()  ← 始终从 live file 读
```

---

## 12. Key Data Flows

### Session Resume (--resume or --continue)

```
1. storage/session_store.load_session(session_id)
   -> read chat_history.jsonl line by line -> list of ConversationItem dicts
   -> read summary.json -> SessionMeta

2. Session._history = restored message list
   (idempotent: AGENTS.md already present -> skip duplicate injection)

3. Agent.run() starts with full context
   -> first build_request() includes complete history
   -> model sees previous conversation naturally
```

### Auto-Compaction Trigger

```
1. After each turn: session.estimated_tokens vs policy.should_compact()
   -> True if tokens >= context_window * 85%

2. compaction/summarizer.generate_summary()
   -> trim input to context_window * 70% - tool_tokens
   -> call LLM with dedicated summarization prompt
   -> return cleaned summary text

3. compaction/assembler.assemble_compacted_history()
   -> [system, user(project_info), user(summary), recent_N_messages, user(reminders)]

4. Session.replace_history(compacted_items)
   -> 批量替换整个对话历史（等价于先清空再重建）
   -> 重置 token 估算计数器
   -> storage/session_store writes updated chat_history.jsonl
   -> next turn sees compacted (but coherent) context
```

### Session.replace_history — 新增方法

```
class Session:
    def replace_history(self, items: list[ConversationItem]) -> None:
        """批量替换整个对话历史。

        用于 compaction 后的历史重建。
        等价于先清空 _history 再用 add_* 逐个填充，但避免了 O(n) 次持久化写入。

        实现：
        1. 验证 items 不为空且以 SystemMessage 开头
        2. _history = list(items)  # 浅拷贝
        3. 重新计算 _token_estimate（遍历估算）
        4. 调用 _repair_dangling_calls() 检查完整性
        5. 触发 store.save() 全量覆盖 JSONL（如果 store 已注入）
        """
```

**为什么不用 `_history = compacted` 直接赋值？**
- 直接赋值绕过了 token 估算更新、dangling 检查、持久化同步
- `replace_history()` 是这些步骤的统一入口"""


### Cross-Session Memory Injection

```
1. memory/index_db.search_fts(user_query) -> FTS5 BM25 ranked results

2. memory/search.search_memory(index, query)
   -> filter empty template chunks
   -> rank by source weight
   -> truncate to max_results

3. core/reminders.memory_reminder(search_results)
   -> format <memory-context> XML block

4. Agent.run() first turn: inject memory_reminder as synthetic user message
   -> model sees past session context without explicit recall
```

---

## 13. Phase 2 Dependencies

```toml
[project]
dependencies = [
    # Phase 1:
    "openai>=1.0",
    "pydantic>=2.0",
    "aiofiles>=23.0",
    # Phase 2 new:
    "aiosqlite>=0.20",
    "blake3>=0.4",
]
```

---

## 14. 补充：之前缺失的 API 签名

### storage/file_lock.py

```
class FileLock:
    """跨平台文件锁。Windows: msvcrt.locking, POSIX: fcntl.flock."""
    def __init__(self, path: Path, timeout: float = 30.0): ...
    def __enter__(self) -> "FileLock": ...
    def __exit__(self, *args) -> None: ...
    async def __aenter__(self) -> "FileLock": ...
    async def __aexit__(self, *args) -> None: ...
```

### storage/models.py

```
class SessionMeta(BaseModel):
    session_id: str
    cwd: str
    model_id: str
    title: str = ""
    created_at: datetime
    updated_at: datetime
    prompt_count: int = 0
    total_tokens: int = 0
```

---

## 15. 本 Phase 的 hooks 装配

Phase 2 不修改 `core/agent.py`。所有新功能通过 `cli/main.py` 的 `_build_agent()` 注入：

```python
# cli/main.py 的 _build_agent()——Phase 2 版本（在 Phase 1 基础上扩展）

def _build_agent(args, session: Session, client: ChatClient, tools: ToolRegistry) -> Agent:
    # Phase 2 新增：创建存储/记忆/压缩模块
    memory_index = MemoryIndex(db_path=Path(args.data_dir) / "memory" / _project_hash(args.cwd) / "index.db")
    store = SessionStore(root_dir=Path(args.data_dir) / "sessions")
    policy = CompactionPolicy()

    hooks: list[AgentHook] = [
        MemoryHook(memory_index),
        CompactionHook(policy),
        PersistenceHook(store),
        EditTrackerHook(),
    ]
    # Phase 3/4 在此列表追加 hook

    return Agent(session, client, tools,
                 max_turns=args.max_turns,
                 hooks=hooks)
```

关键点：
- Agent 构造函数签名不变（`session, client, tools, hooks`）——Phase 1 定义，所有 Phase 不变
- 新功能模块（MemoryIndex/SessionStore/CompactionPolicy）作为 Hook 的**构造依赖**注入，Hook 内部持有引用
- Hook 列表顺序 = 执行顺序：MemoryHook 先于 CompactionHook 注入记忆，CompactionHook 先于 PersistenceHook 压缩后再持久化

### EditTrackerHook——适配器实现

```python
from pathlib import Path

class EditTrackerHook:
    """追踪 Agent 编辑的文件列表。

    追踪策略：
    - search_replace / write_file → 提取 arguments["file_path"]
    - shell_exec → 不追踪（sed/tee/重定向等副作用无法可靠解析）

    设计决策：误追踪比漏追踪更危险（如 ls > /dev/null 被识别为编辑）。
    Agent 通过 search_replace/write_file 完成的编辑占绝大多数。
    """

    def __init__(self):
        self._edited_files: set[str] = set()

    async def on_tool_result(
        self, tool_name: str, arguments: dict, result: "ToolResult"
    ) -> None:
        if result.is_error:
            return  # 失败的编辑不记录
        if tool_name in ("search_replace", "write_file"):
            file_path = arguments.get("file_path")
            if file_path:
                self._edited_files.add(str(Path(file_path).resolve()))

    def get_edited_files(self) -> list[str]:
        """返回已编辑文件的排序列表。Phase 2 ReminderInjector 调用此方法。"""
        return sorted(self._edited_files)

    def clear(self) -> None:
        """清空追踪（新 session 或 compaction 后调用）。"""
        self._edited_files.clear()
```

### MemoryHook / CompactionHook / PersistenceHook——适配要点

这三个 Hook 的适配模式与 EditTrackerHook 类似——将底层模块（MemoryIndex /
CompactionPolicy / SessionStore）包装为 AgentHook 接口。要点：

| Hook 类 | 构造依赖 | 核心方法 | 适配逻辑 |
|:---|:---|:---|:---|
| `MemoryHook` | `MemoryIndex` | `on_session_start` | 调用 `MemoryIndex.search()`，将结果注入 `session.add_user_message()` 作为 synthetic system-reminder |
| `CompactionHook` | `CompactionPolicy` | `on_turn_start` | 调用 `policy.should_compact()`（同步），若需要则触发 `session.replace_history()` 并注入压缩摘要 |
| `PersistenceHook` | `SessionStore` | `on_turn_end` | 调用 `store.save(session)`，异常静默记录（持久化失败不阻断 Agent） |

异常处理：所有 Phase 2 Hook 均为 `on_session_start` / `on_turn_start` / `on_turn_end` 类型——
遵循 Phase 1 §3.7.8 Hook 异常处理契约中的**降级**策略：catch + log.warning + 继续。
MemoryHook 的 SQLite 异常、CompactionHook 的 LLM 调用失败、PersistenceHook 的磁盘 I/O 错误——均不应阻断 Agent 主循环。

---

## 16. 验收清单

对照 `Python-Agent-MVP-实现路线.md` Phase 2 验证标准：

### 会话持久化
- [ ] `SessionStore.init_session()` 创建目录 + 写入 summary.json
- [ ] `SessionStore.append_message()` 追加单行 JSON，两次写入间插入换行
- [ ] `SessionStore.load_session()` 成功恢复完整对话历史
- [ ] `DeltaIterator` 从指定偏移量只读新增行
- [ ] `SessionStore.find_latest(cwd)` 找到最近会话
- [ ] `FileLock` 跨进程不冲突（两个进程同时写不同 session）
- [ ] 崩溃恢复：写入中间 kill 进程，重启后 JSONL 不损坏

### 跨会话记忆
- [ ] `MemoryIndex.reindex_file()` 创建 FTS5 索引
- [ ] `MemoryIndex.search_fts("query")` 返回排名结果
- [ ] FTS5 不可用时自动降级为 LIKE 查询（不崩溃、结果正确）
- [ ] 同一文件两次 reindex，blake3 未变则跳过
- [ ] `MemoryStore.append_session_log()` 追加到按日期的日志文件
- [ ] `search_memory()` 过滤空模板块，按来源权重排序

### 对话压缩
- [ ] `CompactionPolicy.should_compact()` token 超 85% 阈值返回 True
- [ ] `generate_summary()` 返回有意义的中文/英文摘要
- [ ] `assemble_compacted_history()` 输出格式: [system, user(summary), ...recent]
- [ ] 压缩后 Agent 继续推理不丢失上下文

### 多账户
- [ ] `AccountVault.add_account()` 写入 vault.json
- [ ] `AccountVault.switch_to()` 先刷新过期 token 再切换
- [ ] swith_to 中网络刷新在文件锁外进行（不持锁做网络调用）

### 集成（通过 AgentHook 注入——不修改 Agent 构造函数）

Phase 2 的 store/compaction/memory 以 Hook 实例形式注入 Agent，而非作为 Agent 构造函数的直接参数：

```python
# cli/main.py 的 _build_agent() 中（Phase 2 版本）:
hooks=[
    MemoryHook(memory_index),
    CompactionHook(policy),
    PersistenceHook(store),
    EditTrackerHook(),
]
```

验收项：
- [ ] `Agent(session, client, tools, hooks=hooks)` hooks 列表含 Phase 2 四个 Hook 时，Agent 正常启动
- [ ] `MemoryHook.on_session_start` → 首次 turn 注入跨会话记忆 context
- [ ] `CompactionHook.on_turn_start` → token 超阈值时触发压缩
- [ ] `PersistenceHook.on_turn_end` → 每 turn 结束自动持久化会话
- [ ] `EditTrackerHook.on_tool_result` → search_replace/write_file 执行后记录编辑文件
- [ ] hooks 列表为空时（Phase 1 兼容）所有持久化/压缩/记忆功能静默跳过
```
