# Grok Build 对 LLM 固有缺陷的全面防御体系

> 本文档是对 Grok Build（SpaceXAI 终端 AI 编程代理）代码仓库的深度架构分析。
> 核心论点：**Grok Build 不是简单的工具调度胶水，而是围绕 Transformer 概率本质构建的完整防御工事。**

---

## 目录

1. [项目概览（Phase 1）](#一项目概览)
2. [技术选型映射（Phase 2）](#二技术选型映射)
3. [LLM 12 大固有缺陷 × 防御机制（Phase 3 核心）](#三llm-12-大固有缺陷--防御机制)
4. [核心链路代码走读](#四核心链路代码走读)
5. [防御深度全景图](#五防御深度全景图)
6. [设计哲学总结](#六设计哲学总结)
7. [工具粘合机制：从注册到精准调用](#七工具粘合机制从注册到精准调用的完整链路)
8. [数据存储全景：Everything in Its Right Place](#八数据存储全景everything-in-its-right-place)
9. [异步架构：并发、流控、取消的完整设计](#九异步架构并发流控取消的完整设计)
10. [三大维度设计原则汇总](#十新增汇总三大维度的设计原则)
11. [未覆盖功能全景：Grok Build 的完整能力图谱](#十一未覆盖功能全景grok-build-的完整能力图谱)
   - [缺陷 1：模型不听话——忽略精细 Prompt 指令](#缺陷-1模型不听话忽略精细-prompt-指令)
   - [缺陷 2：模型输出格式不对——JSON 格式错误参数类型错误](#缺陷-2模型输出格式不对json-格式错误参数类型错误)
   - [缺陷 3：模型忘记上下文——Lost-in-MiddleContext-Amnesia](#缺陷-3模型忘记上下文lost-in-middle--context-amnesia)
   - [缺陷 4：模型幻觉工具名参数——调用不存在的工具或编造参数](#缺陷-4模型幻觉工具名参数调用不存在的工具或编造参数)
   - [缺陷 5：模型陷入死循环——反复调用同一工具或来回犹豫](#缺陷-5模型陷入死循环反复调用同一工具或来回犹豫)
   - [缺陷 6：模型生成危险命令——rm--rf越权访问](#缺陷-6模型生成危险命令rm--rf--越权访问)
   - [缺陷 7：模型丢失任务状态——我刚才让你做什么来着](#缺陷-7模型丢失任务状态我刚才让你做什么来着)
   - [缺陷 8：模型输出过长——撑爆-Context-Window](#缺陷-8模型输出过长撑爆-context-window)
   - [缺陷 9：模型在-tool-call-和-text-之间切换混乱](#缺陷-9模型在-tool-call-和-text-之间切换混乱)
   - [缺陷 10：多-Subagent-并发时的状态不一致](#缺陷-10多-subagent-并发时的状态不一致)
   - [缺陷 11：认证过期--网络临时故障导致模型调用失败](#缺陷-11认证过期--网络临时故障导致模型调用失败)
   - [缺陷 12：System-Prompt-指令在长对话中逐渐被遗忘](#缺陷-12system-prompt-指令在长对话中逐渐被遗忘)

---

## 一、项目概览

### 业务领域

**AI 辅助软件工程（AI-Assisted Software Engineering）**——终端原生 AI 编程代理。

- **项目名**：Grok Build（代号 `grok`）
- **开发者**：SpaceXAI（xAI 旗下）
- **语言**：Rust（Edition 2024）
- **规模**：80+ Cargo workspace 成员

### 用户画像

| 维度 | 描述 |
|:---|:---|
| 主要用户 | 专业软件开发者、DevOps 工程师、SRE，习惯命令行工作流 |
| 次要用户 | CI/CD Pipeline（headless 模式）、IDE 插件用户（通过 ACP 协议嵌入） |
| 核心痛点 | 终端中频繁切换上下文：查文档 → 搜索代码 → 写代码 → 跑测试 → 修 bug，流程碎片化 |
| 价值主张 | 单一终端界面内完成"理解代码 → 编辑文件 → 执行命令 → 搜索 Web → 管理任务"全流程 |

### 顶层业务流程

```
用户输入（自然语言 / @文件引用 / /命令）
  → 会话初始化（认证 → 加载项目上下文 → 注入规则）
  → Agent 运行时（LLM 推理 + 工具调用循环）
    ├─ Read/Grep/ListDir（理解代码）
    ├─ SearchReplace/Write（编辑文件）
    ├─ TerminalCommand（执行 Shell）
    ├─ WebSearch/WebFetch（搜索 Web）
    ├─ TodoWrite（任务管理）
    └─ SpawnSubagent（并行子代理）
  → 结果渲染（Markdown + 语法高亮 + Diff + 思考块）
  → 会话持久化（SQLite 存储 → 可恢复）
  → TUI 渲染输出（Scrollback + 可折叠卡片）
```

### 核心 Crate 分层

| Crate | 职责 |
|:---|:---|
| `xai-grok-pager-bin` | 二进制入口（Composition Root） |
| `xai-grok-pager` | TUI 渲染层（Scrollback、Prompt、Diff、Markdown） |
| `xai-grok-shell` | Agent 运行时 + 会话管理（核心编排层） |
| `xai-grok-tools` | 所有内置工具实现 |
| `xai-grok-workspace` | 文件系统/VCS/执行/Checkpoint/Permission |
| `xai-chat-state` | Actor 模式对话状态机 |
| `xai-grok-memory` | 跨会话 Memory 系统 |
| `xai-grok-mcp` | MCP 协议集成（隔离 rmcp + reqwest 0.13） |
| `xai-grok-sandbox` | OS 级沙箱（Landlock/Seatbelt） |

---

## 二、技术选型映射

| 核心功能需求 | 使用的技术/中间件 | 引入该技术的业务原因 |
|:---|:---|:---|
| 终端全屏渲染 | ratatui + crossterm | Rust 生态最成熟 TUI 框架，跨平台全屏交互 |
| Agent 推理循环 | async-openai（兼容协议） | 通过 OpenAI 兼容 API 对接 xAI Grok 系列模型 |
| 对话状态管理 | Actor 模式 (tokio::mpsc) + ChatStateActor | `&mut self` 消除锁竞争，天生匹配"单写多读"场景 |
| 会话持久化 | SQLite (rusqlite + bundled + FTS5) | 嵌入式数据库，零部署依赖，FTS5 全文搜索 |
| 代码库搜索 | ripgrep (ignore crate) + nucleo 模糊匹配 | ripgrep 业界最快代码搜索引擎；nucleo 类 fzf 模糊匹配 |
| 文件 Diff 渲染 | similar crate | 纯 Rust diff 算法，无需外部工具 |
| Git 操作 | git2 (libgit2 + vendored) | 避免系统 libgit2 版本冲突 |
| MCP 协议集成 | rmcp 2.1（隔离在 xai-grok-mcp） | reqwest 0.13 与主 workspace 0.12 冲突，隔离解决 |
| 编辑器协议 (ACP) | agent-client-protocol 0.10 | IDE 插件通过标准协议嵌入 |
| OS 级沙箱 | nono (Landlock/Seatbelt) | Linux Landlock + macOS Seatbelt 内核原语 |
| 认证系统 | OAuth2 + OIDC + JWT (jsonwebtoken) | 浏览器 OAuth（交互式）+ API Key（CI/CD）+ Device Code |
| Web 搜索 | reqwest + scraper + htmd | HTTP → HTML→Markdown 转换 + CSS 选择器解析 |
| 图片/文件处理 | image crate + pdf_oxide + zip | 多模态输入 + PDF/PPTX 提取 |
| 异步运行时 | tokio (multi-thread) | 并发 Agent + Subagent + 文件监听 |
| 日志/追踪 | tracing + tracing-subscriber | 结构化日志 + 分布式 tracing |
| Markdown 渲染 | pulldown-cmark + syntect | TUI 内富文本 + Sublime 语法高亮 |
| Mermaid 图表 | vendored dagre_rust + graphlib_rust | 自研 Mermaid 引擎，无 JS 依赖 |
| 内存缓存 | moka (future-aware) | 高频数据异步缓存 |
| Hook 扩展 | xai-agent-lifecycle + xai-grok-hooks | 生命周期事件钩子（类 Git hooks） |
| 崩溃诊断 | xai-crash-handler | Panic 时捕获堆栈 → `~/.grok/crash/` |
| 熔断器 | xai-circuit-breaker | 滑动窗口算法，Server/Client 双预设 |
| 模板渲染 | minijinja (custom_syntax) | System Prompt 和工具输出的动态模板 |
| Memory 向量搜索 | sqlite-vec | SQLite 原生向量扩展，零额外服务依赖 |

---

## 三、LLM 12 大固有缺陷 × 防御机制

> **核心前提**：Transformer 只是概率性地预测下一个 token。它没有真正的"理解"、"记忆"或"意图"。以下每个缺陷都是这一本质的必然表现，而 Grok Build 的每一层防御都是对这一本质的工程回应。

---

### 缺陷 1：模型不听话——忽略精细 Prompt 指令

**根因**：长 prompt 中后部指令容易被模型"遗忘"（Lost in Middle）；模型对"应该做什么"和"不应该做什么"的注意力分配严重不均衡。

#### 防御机制

**A. System Reminder 注入管道**

系统不是只在对话开始时发一次 prompt。它在对话过程中**反复注入**提醒：

- 每次构建 conversation request 时，`reminders.rs` 收集当前需要的所有提醒（TodoGate、日期、Plan Mode 状态、Goal 指令等）
- 通过 `push_system_reminder()` 注入为 `<system-reminder>` 标签包裹的 user message
- Reminder 有明确的 policy 控制（cap 上限、频率限制），避免刷屏适得其反

**B. TodoGate——外部状态机监控模型行为**

```
检测逻辑：pending/in_progress_unbacked 非空 → 注入提醒
格式："You have N pending tasks and M unbacked in-progress tasks..."
```

这不是依赖模型自己记得——是**外部状态机监控模型行为**，发现停滞就推一把。

**C. StructuredOutput 强制工具**

最直接的反"不听话"机制：

- 需要结构化输出时，注入 `StructuredOutput` 合成工具（**不是**让模型自由文本输出 JSON）
- 模型必须显式调用这个工具，传入 JSON 参数
- 如果同时调用其他工具 → 注入纠正反馈，拒绝执行
- 如果参数不符合 JSON Schema → 返回验证错误，要求重试
- 最多 3 次重试，穷尽后 **fail closed**（不产出不合规数据）

**D. Plan Mode 写门控——不依赖模型听话**

- 在 `Active` 状态下，**内核层阻止所有非 plan file 的写工具**——模型"忘了规则"也没用
- 状态机持久化到磁盘——session 重启后依然有效
- ExitPlanMode 需要用户显式审批，模型不能自行退出

---

### 缺陷 2：模型输出格式不对——JSON 格式错误 / 参数类型错误

**根因**：Transformer 生成 token 时没有类型系统的约束。`"true"` vs `true`、`{` 配对错误是统计本质上不可避免的。

#### 防御机制

**A. `serde_path_to_error` 精确反序列化**

```
模型输出 JSON 字符串
  → serde_path_to_error::deserialize()    // 不是 serde_json::from_value()
  → 失败时返回精确字段路径: "tool_call.args.command[0]" 而不是 "invalid JSON"
  → 错误分类: classify_serde_error() 区分类型错误/缺失字段/枚举不匹配
  → 构造 ParamValidationError { field_path, expected, bad_value, category }
  → 精准信息注入回模型 → 模型知道"具体哪里错了"
```

**B. StructuredOutput 3 次重试循环**

每次失败后，错误信息作为 `tool_result` 推回对话，让模型在上下文中看到自己的错误——利用 in-context learning 修正。

**C. 拼接 JSON 对象自动恢复**

`try_extract_concatenated_json_objects()` 使用 `serde_json::StreamDeserializer` 检测并拆分模型在一个 tool call 中拼接的多个 JSON 对象，选取第一个匹配目标 schema 的执行。

**D. 空参数规范化**

`normalize_empty_arguments()` 将空字符串/纯空格参数转换为 `"{}"`——处理模型对零参数工具输出 `""` 而非 `"{}"` 的常见错误。

---

### 缺陷 3：模型忘记上下文——Lost-in-Middle / Context Amnesia

**根因**：Transformer 注意力在长上下文中不均匀分布；中间部分被挤压；跨 session 完全失忆。

#### 防御机制

**A. 自动 Compaction（三层策略）**

触发条件：token 数超过上下文窗口的 85%（可通过 env/TOML/remote settings 逐级覆盖）

三种 compaction 策略：

| 策略 | 所在模块 | 适用场景 |
|:---|:---|:---|
| FullReplace | `code_compaction` | grok-build：总结整个对话，重建全新 history |
| Intra-Compaction | `intra_compaction` | Grok Chat per-step：4 种模式（FullReplace/StepsOnly/HistoryOnly/HistoryThenSteps） |
| Inter-Compaction | `inter_compaction` | Grok Chat between-turn：分块摘要管道 |

**Compaction 输入阶梯（Input Ladder）**：

```
Stage 1: Verbatim — 完整对话给摘要模型
  失败（context_overflow）→
Stage 2: VerbatimFitted — 裁剪到 context_window - 32K reserve
  失败 →
Stage 3: Lossy — 剥离 tool results/flatten calls，预算 = context_window * 0.7
  失败 → 放弃，抑制 auto-compact
```

**B. 双通道预压缩（Two-Pass Prefire）**

pass-1 在阈值前 10%（即 75%）时后台启动，提前摘要对话前缀，压缩时只需合并 NOTE1 + 最近尾部。显著减少用户感知延迟。

**C. 退化摘要检测**

`is_degenerate_summary()` —— 清洁后的摘要 seed 低于 500 字符时判定为退化，触发重试。

**D. 摘要质量门控**

`Reduction Guard` —— 如果 `tokens_after > tokens_before * 0.8`（压缩率不到 20%），拒绝此次压缩。

**E. Session Recap**

比 compaction 更轻量——用户 idle 回来后用 ~25-40 词的句子快速"回忆"当前状态。

**F. 跨 Session Memory 系统**

```
存储布局：
~/.grok/memory/
  ├── MEMORY.md           # 全局记忆（用户偏好、习惯）
  ├── {workspace}/MEMORY.md  # 项目级记忆
  └── {workspace}/sessions/  # 自动生成的 session 日志

混合检索管道：
FTS5 BM25 关键词搜索
  + sqlite-vec KNN 向量搜索（语义相似度）
  + 时间衰减（session chunks 指数衰减，evergreen 不受影响）
  + 来源权重（global > workspace > session）
  + MMR 多样性重排（去除冗余结果）
  + 空内容过滤（is_content_free 滤除自动生成的模板）
```

**G. 对话完整性自修复**

- Ctrl+C → Dangling Tool Call 自动标记为 `UserCancelled`
- 进程崩溃 → 重启后自动修复未配对的 tool call
- 重复 Tool Result → 自动去重
- 每次写边界（push_user_message, build_request）自动检测并修复

---

### 缺陷 4：模型幻觉工具名/参数——调用不存在的工具或编造参数

**根因**：模型训练数据中的工具名和实际可用工具有差异；模型倾向于"补全"看起来合理的参数。

#### 防御机制

**A. 每轮动态注入工具定义**

工具列表不是静态缓存——每轮 `prepare_tool_definitions_timed()` 重新收集：
- 内置工具（read_file, search_replace, grep, bash, ...）
- MCP 工具（动态发现，可能有 server 重启/新增）
- Plugin 工具
- 条件工具（Plan Mode 下只暴露 plan file 相关工具）

**B. BM25 工具搜索引擎**

`Bm25ToolSearchIndex` 让模型通过自然语言查找 MCP 工具：
- **精确匹配快速路径**：大小写不敏感匹配 `server__tool` 或裸 tool 名
- **BM25 排序**：基于工具名 + 描述 + 参数名的分词文档
- **标识符拆分**：处理 `__`、`_`、`-` 和驼峰命名边界

**C. 严格的工具名匹配**

`FinalizedToolset` 使用严格字符串相等匹配。如果模型编造了不存在的工具名，返回明确的 `ToolError::not_found()` 错误，模型可以在下一轮修正。

---

### 缺陷 5：模型陷入死循环——反复调用同一工具或来回犹豫

**根因**：模型在 tool calling 循环中可能陷入"执行→不满意→再执行→再不满意"的无限循环。

#### 防御机制

**A. max_turns 硬限制**

每次模型调用（含 tool call 往返）计数一次 turn。达到 `max_turns` 上限后强制终止。

**B. Doom Loop 检测与自动重采样**

Sampler 层检测模型输出中的循环模式：
- 结构化信号：`tail_repetition:4@response`、`tail_repetition:8@thinking`
- 超过阈值 → 自动中止当前流 → 重新发送请求 → 重试
- 所有重试耗尽 → 接受最终响应（带 doom_loop 标记）

**C. Stall Detector（旁路分类器）**

异步定时器触发，不阻塞主推理循环：
```
收集状态快照 → 小型分类器模型判断 Agent 是否"卡住"
  ├─ Nudge → 注入催促提醒
  ├─ NoNudgeLowConfidence → 不干预（防止误判）
  └─ NoNudgeNotStalled → 不干预
```

关键安全设计：分类器输出解析失败 → 不注入任何东西（fail safe，不会因为解析 bug 打断正常模型工作）。

**D. Goal 退避**

连续 3 次非完成 turn → 自动暂停 Goal（防止模型无限尝试）。

---

### 缺陷 6：模型生成危险命令——rm -rf / 越权访问

**根因**：模型不理解 OS 级安全边界；可能被 prompt injection 诱导执行恶意操作。

#### 防御机制

**A. 七层权限决策管道**

```
工具调用请求
  ├─ ① YOLO 模式？              → Allow（跳过所有检查）
  ├─ ② 安全命令白名单？          → Allow（静态列表，tree-sitter 解析）
  ├─ ③ 持久化授权？              → Allow（用户之前点了 "Always allow"）
  ├─ ④ 会话级授权？              → Allow/Deny（本次会话内记住的选择）
  ├─ ⑤ --allow/--deny 规则？     → Allow/Deny（CLI 参数或配置的 glob 规则）
  ├─ ⑥ Auto 模式分类器？         → Allow/Deny（LLM side-query 分类）
  └─ ⑦ 弹出用户确认框            → Ask（阻塞等待用户决策）
```

**B. 安全命令白名单 + 树语法解析**

```
tree-sitter-bash 解析命令 → 提取 command words
  → 匹配安全前缀列表
  → 特殊情况排除:
    - rg --pre → 拒绝（可以执行任意程序）
    - tee → 拒绝（可绕过编辑权限写文件，CWE-863）
```

**C. OS 级沙箱**

- Linux: Landlock（内核 5.13+）文件系统访问控制
- macOS: Seatbelt 系统级沙箱
- deny 路径 glob 在启动时编译为内核规则

即使权限管道被绕过，沙箱在**操作系统内核层**做最后一道防线。

---

### 缺陷 7：模型丢失任务状态——"我刚才让你做什么来着？"

**根因**：对话历史只是文本流，没有结构化的任务状态跟踪。

#### 防御机制

**A. TodoState 结构化资源**

Todo 不是只存在于对话文本中——是一个结构化的 Resource：
```
TodoWrite 工具 → 写入 TodoState（内存 + 持久化）
  ├─ 每个 Todo: { id, content, status, priority }
  ├─ 模型可修改 → 系统跟踪状态变化
  └─ Turn-end 自动清理: in_progress → completed（显示层）
```

**B. GoalTracker 状态机**

```
Goal 生命周期: Active
  → 等待 completed: true
  → Classifier 验证
  → Achieved / NotAchieved
```

Goal 状态不依赖模型"记住"——是外部状态机在跟踪。

**C. 恢复任务的 Manifest 注入**

Session 恢复时，上次 session 遗留的后台任务被加载并通过 system-reminder 注入，模型立刻知道"还有什么没完成"。

---

### 缺陷 8：模型输出过长——撑爆 Context Window

**根因**：工具输出（如读取大文件、grep 大量结果）不加限制地返回会导致 token 爆表。

#### 防御机制

**A. 多层截断策略**

| 函数 | 策略 | 适用场景 |
|:---|:---|:---|
| `truncate_line()` | 硬截断 + marker `"[... truncated (N chars total)]"` | grep 匹配行 |
| `soft_wrap_line()` | 软换行（**保留全部内容**） | bash/task_output |
| `truncate_with_preview()` | 保留头 N 字节 + footer | 大文件（footer 提示"用 read_file 查看更多"） |
| `truncate_front_and_back()` | 保留头尾各 50% | 需要看开头和结尾的场景 |
| `truncate_middle()` | 保留头尾 + `"..."` 中间 | 中等长度输出 |
| `truncate_lines_to_char_budget()` | 在换行边界截断 | 保持可读性 |
| `truncate_str_with_marker()` | 硬截断 + `"…"` | 通用策略 |

**B. Token 预估算**

每个工具实现内部调用 `estimate_tokens()`（bytes/4 启发式），在返回前预判是否超限。

**C. Base64 图片提取**

`extract_base64_images()` 将内联 base64 图片从文本中剥离，转为 deferred `ConversationItem::user` 跟进，防止巨型 blob 污染上下文。

---

### 缺陷 9：模型在 tool call 和 text 之间切换混乱

**根因**：模型可能在一个 response 中混合 text 和 tool call，或该调用工具时却输出文本。

#### 防御机制

**A. 原生 Structured Output 优先**

```
if backend supports native json_schema:
  → request.json_schema = user_schema  // 直接使用 API response_format
else:
  → 使用 StructuredOutput 合成工具  // fallback
```

**B. Content Filter 检测**

```
stop_reason == ContentFilter → 模型输出被安全过滤器拦截
  → refusal_explanation 返回给客户端
  → 不会静默丢弃
```

---

### 缺陷 10：多 Subagent 并发时的状态不一致

**根因**：模型可能同时生成多个 tool call，如果两个 tool call 编辑同一文件就冲突了。

#### 防御机制

**A. 同文件编辑串行化**

```
lock_path_for_args(args) → 提取 file_path/path/target_file
  → 同一 path 的多个 tool call 共享 Mutex
  → 按模型发出的顺序串行执行
  → 不同 path 的 tool call 完全并发
```

**B. 提前终止级联**

如果一个 tool call 导致 `PermissionReject`、`Cancelled` 或 `FollowupMessage`，同一批次所有剩余调用被短路——用合成 "Tool execution cancelled" 结果替代。

**C. 等待工具可中断**

`get_task_output`、`wait_tasks` 等阻塞等待工具收到用户 mid-turn 消息时自动 abort，不阻塞交互。

---

### 缺陷 11：认证过期 / 网络临时故障导致模型调用失败

**根因**：Token 过期、网络波动、服务端临时不可用。

#### 防御机制

**A. Auth 401 自动恢复 + 重试**

```
工具调用返回 401
  → is_auth_tool_error() 检测（结构化 status code + string fallback）
  → AuthManager::try_recover_unauthorized()
  → 恢复成功 → 重试一次
  → 恢复失败 → 返回原始错误
  → 同一批次并发 401 → OnceCell 去重恢复
```

**B. Circuit Breaker 熔断器**

```
滑动窗口算法:
  error_rate >= threshold AND sample_count >= min_samples → 熔断
  BreakerConfig::server / BreakerConfig::client 双预设
  RetryPolicy 控制退避策略
```

**C. Compaction 失败智能分类**

```
Deterministic: Auth / InvalidConfig / Serialization / 4xx(非408/429) → 不重试
Transient: 网络错误 / 5xx / 流错误 → 退避重试
```

---

### 缺陷 12：System Prompt 指令在长对话中逐渐被遗忘

**根因**：System prompt 在 conversation[0]，随着对话增长，注意力分布越来越稀释。

#### 防御机制

**A. 项目规则以 User Message 形式注入**

AGENTS.md / CLAUDE.md 被构造为 `<always_applied_workspace_rules>` 和 `<user_rules>` 独立区块，以 User message 形式注入（比 System message 更容易被注意力机制关注）。

**B. 幂等注入**

`conversation_has_project_instructions()` 在 resume/fork 时检测已有 AGENTS.md 提醒，跳过重复注入。

**C. Compaction 保留 System Prompt**

压缩对话历史但**保留 System prompt**（conversation[0]），模型始终知道自己是谁。

**D. Compaction 后状态恢复**

压缩后立即注入 post-compaction reminder：后台任务 + Todo + Subagent + 编辑文件 + 新发现规则 + MCP 服务器 + Memory 搜索结果。

---

## 四、核心链路代码走读

### Agent 推理主循环

```
TUI Input → ACP Gateway → MvpAgent → SessionActor
                                        │
   ┌────────────────────────────────────┘
   │
   ▼
SessionActor::run_turn_via_sampler()     ← 核心推理循环入口
   │
   ├─ ① prepare_tool_definitions()      ← 收集所有可用工具
   ├─ ② build_conversation_request()    ← 从 ChatStateActor 构建 messages
   ├─ ③ sampler.sample(request)         ← 调用 LLM，streaming 响应
   ├─ ④ 解析响应：
   │     ├─ text_delta → 实时推送 TUI
   │     ├─ tool_use → ToolBridge 执行
   │     └─ stop_reason → 退出循环
   ├─ ⑤ 工具结果写入 ChatStateActor
   └─ ⑥ 检查 max_turns / cancellation
```

### 关键设计模式

| 模式 | 应用位置 | 为什么用 |
|:---|:---|:---|
| **Actor 模式** | `ChatStateActor` | `&mut self` 消除锁开销，天然匹配 tokio 任务模型 |
| **管道 (Pipeline)** | `SessionActor::run_turn_via_sampler()` | 推理→工具执行→反馈 循环本质是管道 |
| **策略模式** | 认证 (`AuthManager`)、MCP 初始化 | 不同环境（交互式/headless/CI）需要不同策略 |
| **桥接模式** | `ToolBridge` | 工具实现与调用方解耦 |
| **观察者模式** | Hook 生命周期事件 | 用户插件在不修改核心代码的情况下扩展行为 |
| **CQRS 分离** | `ChatStateActor` 分 `mutations.rs` / `queries.rs` | 读写分离，避免读操作产生副作用 |

### 异常处理机制

1. **Dangling Tool Call 修复**：每次写边界自动检测并修复未完成 tool call
2. **Auth 401 恢复 + 重试**：`call_with_auth_retry()` 自动刷新 token 后重试一次
3. **MCP Server 崩溃隔离**：失败不阻塞内置工具，自动重启
4. **Panic 兜底**：`xai-crash-handler` 捕获 + 写入 crash report
5. **取消信号传播**：`CancellationToken` 贯穿全链，Ctrl+C 优雅中断

---

## 五、防御深度全景图

```
┌──────────────────────────────────────────────────────────────┐
│                    LLM 输出（不可靠的概率采样）                 │
└──────────────────────┬───────────────────────────────────────┘
                       │
       ┌───────────────┼───────────────┐
       ▼               ▼               ▼
  ┌──────────┐  ┌────────────┐  ┌──────────────┐
  │ 格式兜底  │  │  行为兜底   │  │  状态兜底     │
  ├──────────┤  ├────────────┤  ├──────────────┤
  │JSON Schema│  │7层权限管道  │  │TodoState资源  │
  │3次重试    │  │Plan Mode门控│  │GoalTracker   │
  │serde_path │  │安全命令白名单│  │Compaction    │
  │参数验证   │  │OS级沙箱    │  │Dangling修复   │
  │Structured │  │CircuitBrkr │  │Session持久化  │
  │Output工具 │  │DoomLoop检测│  │Memory系统    │
  │拼接JSON恢复│  │Goal退避    │  │Recap生成     │
  └──────────┘  └────────────┘  └──────────────┘
                       │
       ┌───────────────┼───────────────┐
       ▼               ▼               ▼
  ┌──────────┐  ┌────────────┐  ┌──────────────┐
  │ 上下文兜底 │  │  提醒兜底   │  │  并发兜底     │
  ├──────────┤  ├────────────┤  ├──────────────┤
  │Rules注入  │  │System      │  │同文件串行化  │
  │Memory注入 │  │  Reminder  │  │Subagent上限  │
  │Tool定义   │  │TodoGate    │  │401去重恢复   │
  │  每轮刷新 │  │Laziness    │  │Compaction    │
  │Token预估  │  │  分类器    │  │  重试分类    │
  │输出截断   │  │Recap生成   │  │提前终止级联  │
  │Base64剥离 │  │日期提醒    │  │等待可中断    │
  └──────────┘  └────────────┘  └──────────────┘
```

---

## 六、设计哲学总结

### 核心原则

1. **不信任模型** — 每一项关键决策都有独立于模型的验证；模型只是"建议者"，系统是"裁决者"
2. **Fail Closed** — 不确定时宁可拒绝执行，不假设模型"大概是对的"
3. **多层防御** — 没有单点能击穿整个系统（即使绕过权限，还有沙箱；即使输出格式错，还有重试纠正）
4. **外部状态机** — Todo、Goal、Plan Mode 都是外部状态，不依赖模型记住
5. **信息不丢失** — 截断时给 footer 提示如何获取完整内容；Session 崩溃后可恢复；Compaction 保留核心上下文
6. **渐进降级** — Compaction 的输入阶梯（Verbatim → Fitted → Lossy）、StructuredOutput 3 次重试、Auth 恢复→重试→放弃
7. **可观测性优先** — 每个决策点都有 telemetry + debug log，出了问题可以追溯

### 与"纯胶水"架构的本质区别

| 维度 | 纯胶水架构 | Grok Build |
|:---|:---|:---|
| 模型输出 | 直接执行 | 解析→验证→重试→门控→执行 |
| 任务状态 | 依赖模型记忆 | 外部结构化状态机 |
| 安全边界 | 依赖 prompt 指令 | OS 内核级沙箱 |
| 上下文管理 | 依赖模型注意力 | Compaction + Memory + Recap |
| 失败处理 | 静默丢弃或报错 | 分类→降级→恢复→抑制 |
| 模型停滞 | 无感知 | 旁路分类器异步检测 |

---

> **结论**：Grok Build 不是工具的调度胶水，而是一座围绕 Transformer 概率本质系统性构建的防御工事。它的每一层设计都可以追溯到 LLM 的一个具体失败模式，而它的多层防御架构确保没有任何单一故障点能穿透整个系统。

---

## 七、工具粘合机制：从注册到精准调用的完整链路

### 7.1 三层工具注册架构

```
Layer 1: 编译时 — ToolRegistryBuilder::new()
  ├─ register::<BashTool>()              // 每个工具类型注册一次
  ├─ register::<ReadFileTool>()          // 包含：解析器、验证器、输出转换器、JSON Schema
  ├─ register::<GrepTool>()
  └─ ... 50+ 内置工具 + Tool Packs 外部扩展

Layer 2: 运行时 — ToolRegistryBuilder::finalize(config, ctx)
  ├─ 根据 ToolServerConfig 筛选启用哪些工具
  ├─ 注入运行时资源（Terminal, FileSystem, Cwd, Skills, MemoryBackend...）
  ├─ 构建 TemplateRenderer（kind→name 映射 + 参数名映射）
  └─ 产出 FinalizedToolset（不可变，Arc 共享）

Layer 3: 每轮 — prepare_tool_definitions_timed()
  ├─ 从 FinalizedToolset 获取所有 tool_definitions
  ├─ 过滤 Plan Mode 下不可用的工具
  ├─ 添加 MCP 动态工具（server__tool_name 格式）
  └─ 注入到 LLM request 的 tools 数组
```

### 7.2 ToolEntry：类型安全的 7 闭包管道

每个注册的工具被包装为一个 `ToolEntry`（`registry/types.rs:587-620`），携带 7 个编译时绑定的闭包：

| 闭包 | 签名 | 职责 |
|:---|:---|:---|
| `parse_input` | `JSON → ToolInput` | 将模型输出的 JSON 参数反序列化为强类型输入 |
| `validate_params` | `JSON → Result` | 业务逻辑级参数验证（如文件路径是否存在） |
| `apply_params` | `JSON → Resources` | 工具配置参数注入到运行时状态 |
| `output_converter` | `JSON → ToolOutput` | 将工具执行结果 JSON 转为类型化输出 |
| `register_params` | `Resources → ()` | 声明工具需要的 Resource 类型 |
| `register_in_local` | `LocalRegistry → ()` | 将工具注册到进程内调度中心 |
| `metadata` | `ToolMetadata` | 工具的 kind、namespace、描述模板、JSON Schema |

这不是简单的字符串→函数映射，而是一个**完整的类型安全管道**。

### 7.3 TemplateRenderer：让工具描述跨 harness 统一

**原理**：MiniJinja 模板引擎 + 自定义 `${{ }}` 和 `${% %}` 分隔符。

```
构建输入：
  kind_to_name: { Read → "read_file", Bash → "run_terminal_cmd", Grep → "grep", ... }
  kind_params:  { Edit → { "file_path"→"file_path", "old_string"→"old_string", ... } }

模板变量：
  ${{ tools.by_kind.read }}          → "read_file"
  ${{ params.edit.old_string }}      → "old_string"
  ${%- if has_unix_utilities %}      → 根据运行时 OS/Shell 分支
  ${%- if tools.by_kind.search %}    → 条件渲染
```

**为什么需要？** 同一功能在不同 harness 下名称不同——Grok Build 中叫 `grep`，OpenCode harness 中叫 `open_code_grep`，Concise harness 中叫 `search_content`。模板让描述写一次，名称自动填入。

### 7.4 Skills 系统：触发判断机制

Skills 的 `when_to_use` 字段是**模型面指导**，不是程序化匹配规则。触发方式：

1. **用户显式调用**：`/commit` 斜杠命令 → 展开 skill body → 注入到 user message
2. **模型主动调用**：模型在 tool calling 中选择 `skill` 工具，传入 skill 名和参数
3. **预加载注入**：Agent definition 中 `skills: [commit, review-pr]` → 启动时注入 system prompt

**动态发现流程**（`skills/discovery.rs`）：
```
每次工具调用后触发 SkillDiscoveryReminder：
  → 提取工具输出中的文件路径
  → 向上遍历目录查找 .grok/skills/、.agents/skills/
  → 发现新 SKILL.md → 解析 YAML frontmatter
  → 通过 <system-reminder> 注入模型
  → paths: 条件 skill 匹配到被访问文件时激活
```

### 7.5 MCP 工具集成：隔离的热插拔层

```
xai-grok-mcp crate（隔离 rmcp 2.1 + reqwest 0.13）
  │
  ├─ McpClient: 管理 MCP server 进程生命周期
  │    ├─ 启动/停止 server 进程（HTTP+OAuth 和 stdio 两种传输）
  │    ├─ 握手（initialize → list_tools，带 cursor 分页）
  │    └─ 健康检查 + 自动重连
  │
  ├─ 工具发现: list_tools → 转换为 McpErasedTool
  │    命名: {server_name}__{tool_name}（双下划线分隔）
  │    McpErasedTool 实现 xai_tool_runtime::Tool + ToolMetadata
  │    注册到 LocalRegistry 和 FinalizedToolset
  │
  └─ 工具调用: call_tool() → rmcp JSON-RPC → server 执行 → ToolOutput
```

**隔离理由**：rmcp 2.1 依赖 reqwest 0.13，主 workspace 使用 reqwest 0.12。`xai-grok-mcp` crate 作为隔离层——主代码通过 trait 接口使用 MCP，不直接依赖 rmcp。

### 7.6 Computer Hub SDK：进程内外统一的工具调度

```
xai-computer-hub-sdk (harness 侧 + server 侧调度面)
  ├─ ToolHarness: 客户端调度器
  │    local-first: LocalRegistry.find() → 进程内执行
  │    remote fallback: WebSocket JSON-RPC tool.call
  ├─ ToolServer: 服务端入口
  └─ HubConnectionPool: WebSocket 连接复用

xai-computer-hub-core (传输无关抽象)
  ├─ ErasedTool<T>: 类型擦除包装器
  │    解码 JSON → T::Args → T::run() → 编码输出为 JSON
  ├─ CompoundResolver: 本地+远程组合查找
  └─ LocalRegistry: 进程内工具注册表（FinalizedToolset 的内部调度中心）
```

---

## 八、数据存储全景：Everything in Its Right Place

### 8.1 存储总览

| 数据 | 存储位置 | 格式 | 关键机制 |
|:---|:---|:---|:---|
| **会话对话** | `~/.grok/sessions/{cwd_encoded}/{sid}/chat_history.jsonl` | JSONL (ConversationItem) | 追加写、流式读、format version 兼容、损坏行跳过 |
| **会话事件** | `~/.grok/sessions/{cwd_encoded}/{sid}/updates.jsonl` | JSONL (SessionUpdateEnvelope) | 追加写、Delta Replay (SeekFrom offset)、`\n` 撕裂修复 |
| **会话元数据** | `summary.json` 等 | JSON + fs2 文件锁 | temp-file + rename 原子写、互斥锁防竞态 |
| **Memory 索引** | `~/.grok/memory/{project}/index.sqlite` | SQLite + FTS5 + sqlite-vec | 混合搜索、WAL/Truncate 自适应、blake3 去重 |
| **Memory 内容** | `~/.grok/memory/{project}/MEMORY.md` | Markdown | 人类可编辑、按 H2 标题分块索引 |
| **Memory 会话日志** | `~/.grok/memory/{project}/sessions/YYYY-MM-DD-{slug}.md` | Markdown | `<!-- flush HH:MM:SS UTC -->` 追加戳 |
| **会话搜索索引** | `~/.grok/sessions/session_search.sqlite` | SQLite + FTS5 | BM25 搜索（标题权重 10:1）、cwd 过滤、远程同步到 GCS |
| **认证凭据** | `~/.grok/auth.json` | JSON (BTreeMap) | OIDC scope 键控、Token 刷新时写入 |
| **用户配置** | `~/.grok/config.toml` | TOML (toml_edit, preserve_order) | 保持注释和键序 |
| **资源状态** | `{session_dir}/resources_state.json` | JSON (ResourcesPersistence) | 崩溃恢复 TodoState/SchedulerState |
| **崩溃报告** | `~/.grok/crash/` | 二进制 (minidump) | Panic 紧急写入 |
| **检查点** | `{cwd}/.grok/rewind-checkpoints/{sid}/` | JSON (RewindCheckpoint) | temp-file + rename + fsync、64 个 cap、env gated |
| **上传队列** | `~/.grok/upload_queue/*.meta.json` | JSON sidecar + temp file | sha256 校验、崩溃恢复、2h TTL 清理 |

### 8.2 SQLite 的精细配置

**Memory Index 表结构**（`schema.rs`）：

```sql
-- 元数据（含 reindex_claim 多进程协调锁）
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- 文本块（按 Markdown H2 标题分块，blake3 去重）
CREATE TABLE chunks (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT UNIQUE NOT NULL,          -- "{path}:{chunk_index}"
    path TEXT NOT NULL,               -- 来源文件路径
    start_line, end_line INTEGER,     -- 在源文件中的位置
    text TEXT NOT NULL,               -- 块内容
    hash TEXT NOT NULL,               -- blake3（去重用）
    source TEXT NOT NULL,             -- "global" | "workspace" | "session"
    created_at, updated_at INTEGER,   -- Unix 时间戳
    access_count INTEGER DEFAULT 0,   -- 访问频率（影响搜索排序）
    last_accessed INTEGER
);

-- FTS5 全文索引（content='' = 无内容表，数据在 chunks 中）
CREATE VIRTUAL TABLE chunks_fts USING fts5(text, content='');

-- sqlite-vec 向量索引（可选，维度可配置 384/768/1536）
CREATE VIRTUAL TABLE chunks_vec USING vec0(
    chunk_id TEXT PRIMARY KEY,
    embedding FLOAT[{dimensions}]
);
```

**WAL vs Truncate 日志模式**（`xai-sqlite-journal`）：

```
检测逻辑：
  statfs() → 判断文件系统类型：
    Linux: f_type 匹配 NFS/SMB/CIFS/Ceph/Lustre/FUSE/...
    macOS: MNT_LOCAL 缺失 + fstypename 白名单
    Windows: UNC 路径 + GetDriveTypeW

NFS/网络文件系统 → Truncate 模式
  ├─ 每个 host 独立 DB 文件（{name}.h-{hostname}.db）
  └─ 防止跨 host WAL -shm 映射不一致 → SIGBUS

本地文件系统 → WAL 模式
  ├─ 并发读写性能更好
  └─ busy_timeout = 5s

可通过 GROK_SQLITE_JOURNAL_MODE=wal|truncate 覆盖
```

### 8.3 JSONL 追加写 + 偏移量 Delta Replay

**写入**（`jsonl/mod.rs`）：
```rust
// 异步追加
tokio::fs::OpenOptions::new().append(true).open(path)
tokio::io::AsyncWriteExt::write_all(file, json_line.as_bytes()).await;
file.flush().await;

// 崩溃保护：追加前检查最后一个字节是否为 \n
// 如果不是（上次撕裂写）→ 先补 \n，损坏记录作为独立行存在
```

**读取**（`storage/mod.rs:26-68`）：
```rust
// 完整读取
let iter = UpdatesIterator::open(path)?;

// Delta 读取——从已知偏移量恢复
let iter = UpdatesIterator::open_at(path, last_known_offset)?;
// 内部: BufReader::seek(SeekFrom::Start(offset)) → 只读新增行
// 零拷贝解析: serde_json::from_str::<&RawValue>() + 延迟具体类型反序列化

// 处理后记录新偏移
let new_offset = iter.stream_position()?;
```

**为什么是 JSONL 而不是 SQLite？**
- 追加写 O(1)，不需要事务
- 人类可直接 `tail -f` 查看
- Delta replay 只需文件偏移量
- 崩溃后无需修复——不完整末行成为独立损坏行
- 单线程访问，无并发写入

### 8.4 Memory 混合搜索管道

**完整流水线**（`memory/search.rs`）：

```
1. FTS5 关键词搜索（始终可用，BM25 排序）
2. 补充 evergreen 查询（global/workspace 源不被 session 量淹没）
3. sqlite-vec KNN 向量搜索（当 sqlite-vec + embeddings 可用）
4. 分数归一化：FTS BM25 → [0,1]；向量 L2 → similarity = 1.0 - distance/2.0
5. 分数合并：max(fts_only, hybrid)（FTS-only chunk 不被惩罚）
6. 空内容过滤：is_content_free() 滤除自动生成的 MEMORY.md 模板
7. 时间衰减：session chunks 指数衰减 e^(-λ × age_days)，half-life 30 天
8. 来源权重 + 访问频率加成：ln(1 + access_count) × 0.05
9. 最小分数阈值过滤
10. MMR 多样性重排（可选，config.mmr.enabled）
11. 截断到 max_results
```

### 8.5 GCS 远程存储

**会话 Traces 上传**（`upload/` 模块）：
```
每 turn 上传：
  {sid}/turn_{N}/
    metadata.json           -- 模型、token、沙箱信息
    turn_result.json        -- stop_reason、signals、tokens、subagents
    permission_decisions.json -- 权限决策审计
    unified_log.jsonl       -- 遥测快照
    tool_definitions.json   -- 工具定义快照
    upload_manifest.json    -- 权威 "done" 信号

上传方式：
  Proxy (默认): POST /v1/storage
  Direct GCS: 服务账号密钥
  S3: S3 兼容存储

崩溃恢复：
  upload_queue/*.meta.json + temp file
  sha256 校验 → 重新入队或清理（>2h TTL）
```

---

## 九、异步架构：并发、流控、取消的完整设计

### 9.1 三层运行时架构

```
进程级（Multi-Threaded）:  main.rs
  tokio::runtime::Builder::new_multi_thread().enable_all().build()
  职责: HTTP I/O, TUI 渲染, WebSocket, Leader 协调

Agent 级（CurrentThread + LocalSet）:  app.rs
  tokio::runtime::Builder::new_current_thread() + LocalSet
  职责: MvpAgent 管理 ACP 连接、会话生命周期、!Send 状态（RefCell, Rc）

Session 级（CurrentThread + LocalSet + 专用 OS 线程）:  spawn.rs
  8 MB 栈 + 专用 OS 线程 + 单线程 runtime + LocalSet
  职责: SessionActor 独占执行、spawn_local 任务、非 Send 状态
```

**为什么三层？**
- Session Actor 包含大量 `!Send` 状态（`RefCell`, `Rc`, `Cell`, `LocalRef`）
- 每个 session 的 OS 线程隔离防止 session 崩溃互相影响
- 进程级多线程 runtime 处理网络 I/O，不阻塞 TUI
- Agent 级 LocalSet 避免数百个并发 ACP handler 的同步开销

### 9.2 Actor 模式：零锁状态管理

```rust
// ChatStateActor（chat-state/src/actor/mod.rs）
pub struct ChatStateActor {
    state: ChatState,           // 独占 &mut self → 零锁开销
    cmd_rx: UnboundedReceiver,  // 接收命令
    event_tx: UnboundedSender,  // 发送事件给外部
}

// 运行循环
loop {
    tokio::select! {
        biased;                                    // biased: 取消优先
        _ = cancellation_token.cancelled() => break;
        cmd = cmd_rx.recv() => match cmd {
            PushUserMessage(msg) => state.push(msg),  // 串行化保证一致性
            GetSnapshot(reply) => reply.send(state.snapshot()),
        }
    }
}
```

**为什么不用 `Arc<Mutex<ChatState>>`？**
- Actor 的 `&mut self` 是零成本——无锁竞争，无死锁风险
- 命令串行化天然保证了一致性
- 事件通过 channel 异步发出——不阻塞 Actor 循环

### 9.3 FuturesUnordered + 结果保序并发

```rust
// tool_calls.rs
// 1. 构建并发 futures
let dispatch_futures: Vec<_> = approved.iter().enumerate().map(|(idx, prepared)| {
    async move {
        // 同文件编辑获取 Mutex 锁（串行化）
        let _guard = lock.map(|l| l.lock().await);
        // 执行工具（带 auth 恢复 + 中断检测）
        let result = call_with_auth_retry(am, &shared_recovery, &tool_name, run_tool).await;
        (idx, result)  // 返回原始索引
    }
}).collect();

// 2. FuturesUnordered：哪个先完成就先产出
let mut dispatch_stream = futures::stream::FuturesUnordered::new();

// 3. 按原始索引恢复顺序
let mut approved_slots: Vec<Option<PreparedToolCall>> = approved.into_iter().map(Some).collect();
while let Some((idx, result)) = dispatch_rx.recv().await {
    let prepared = approved_slots[idx].take().unwrap();
    // 按 model 发出的顺序处理结果
}
```

### 9.4 流式响应处理

```
LLM API (SSE)
  → eventsource_stream::Eventsource（HTTP SSE 解析）
  → async_stream::stream!（Stream<Item = SamplingEvent>）
  → tokio::time::timeout（空闲超时检测）
  → sampler actor JoinSet（并发请求管理）
  → unbounded_channel → SessionActor
  → tokio::select! {
        event,
        cancellation_token,
        interjection
    }
```

### 9.5 分级取消机制

```
Ctrl+C
  → Pager: CancellationToken::cancel()
  → Sampler: 中止当前 SSE 流
  → SessionActor: tokio::select! biased 优先取消
    ├─ 已完成 tool call → drain 结果
    ├─ 未开始 tool call → 合成取消结果
    └─ ChatStateActor: repair_dangling_tool_calls()
  → MvpAgent: 返回部分结果给客户端
```

**等待类工具的中断**（`tool_calls.rs:440-449`）：
```rust
tokio::select! {
    biased;                              // biased: 用户优先
    result = call_with_auth_retry(...),  // 工具执行完成
    _ = wait_for_pending_interjection(), // 用户发了消息
    => Ok(interrupted_wait_tool_result(...))  // 合成中断结果
}
```

### 9.6 信号量限流与锁策略

| 机制 | 应用场景 | 实现 |
|:---|:---|:---|
| `tokio::sync::Semaphore` | Subagent 并发上限 | `acquire().await` / `drop(permit)` |
| `tokio::sync::Mutex` (按 file_path) | 同文件编辑串行化 | `HashMap<String, Arc<Mutex<()>>>` |
| `parking_lot::Mutex` | 高频低争用状态 | 无中毒、用户态自旋 |
| `std::sync::atomic::Atomic*` | Flag/计数器 | 零锁开销 |
| `OnceLock<Mutex<HashMap>>` | 懒初始化全局注册表 | 双检查模式 |

### 9.7 LocalRef：绕过 'static 的模式

```rust
// mvp_agent/mod.rs
pub(crate) struct LocalRef<T> {
    ptr: *const T,  // 裸指针 → !Send + !Sync
}

// 安全契约（由调用方保证，不由类型系统保证）：
// 1. T 是堆分配的（Box/Rc/Arc）
// 2. 所有访问在同一 LocalSet 线程
// 3. LocalRef 不超出 LocalSet 生命周期
// 4. 不 move/deallocate 原值
```

**为什么需要？** 数百个 `spawn_local` 闭包需要 `&MvpAgent`，但 borrow checker 要求 `'static`。`LocalRef` 提供安全的裸指针包装。

### 9.8 文件监听的异步架构

```
OS 级文件事件 (inotify/FSEvents/ReadDirectoryChangesW)
  → notify crate
  → notify-debouncer-mini (100ms debounce)
  → mpsc::unbounded_channel
  → LockStateMachine: Idle → Locked → Settling(500ms) → Cooldown(500ms) → Idle
    ├─ 合并 git rebase 的快速锁周期
    └─ 抑制 VCS lock 期间的 GitMetaChanged 事件
  → broadcast::channel(256)（lossy: 慢消费者被跳过）
  → SessionActor 处理:
    ├─ skill 文件变更 → SkillManager 重新发现
    ├─ AGENTS.md 变更 → 重新注入规则
    └─ memory 文件变更 → MemoryIndex 重新索引

Watcher 共享: OnceLock<Mutex<HashMap<PathBuf, Weak<FsEventSource>>>>
  同目录多 session 共享一个 OS watcher
  WATCHERS_CREATED / WATCHERS_REUSED 原子计数器
```

### 9.9 Channel 策略总结

| Channel 类型 | 使用场景 | 理由 |
|:---|:---|:---|
| `mpsc::unbounded_channel` | Actor 命令/事件 | 有限消息量，无背压需求 |
| `broadcast::channel(N)` | FS 事件 | Lossy 投递，最新快照足够 |
| `oneshot::channel` | 请求-响应 | 精确的一对一回复 |
| `FuturesUnordered` | 并发工具执行 | 完成顺序 ∈ 非确定性 |
| `OnceCell` | 懒初始化/去重 | 单次写入、多消费者 |

---

## 十、新增汇总：三大维度的设计原则

### 工具粘合

| 原则 | 实现 |
|:---|:---|
| 类型安全管道 | 7 闭包 ToolEntry（parse→validate→apply→convert） |
| 模板分离关注点 | TemplateRenderer 让工具描述与 harness 解耦 |
| 编译时注册 + 运行时配置 | ToolRegistryBuilder::register() + ToolServerConfig::finalize() |
| Skill 是 prompt 注入 | `<skill>` XML 块，when_to_use 是模型面指导 |
| MCP 版本隔离 | 独立 crate 隔离 reqwest 0.13 冲突 |
| 本地优先 + 远程兜底 | LocalRegistry.find() → WebSocket JSON-RPC fallback |

### 数据存储

| 原则 | 实现 |
|:---|:---|
| 追加写优先 | JSONL 追加，O(1) 写入，Delta Replay |
| 结构化搜索用 SQL | SQLite + FTS5 + sqlite-vec 混合搜索 |
| 防 NFS 崩溃 | Journal 模式自适应（WAL on local, Truncate on NFS）+ 每 host 独立 DB |
| 原子写 | temp-file + rename + fsync |
| 去重 | blake3 hash（Memory chunks + Session search content） |
| 人类可读兜底 | Memory MARKDOWN + JSONL `tail -f` |

### 异步架构

| 原则 | 实现 |
|:---|:---|
| 三层运行时 | 进程(multi-thread) + Agent(current-thread) + Session(专用OS线程) |
| Actor 替代 Mutex | ChatStateActor 串行处理，零锁开销 |
| FuturesUnordered 最大化并行 | 工具执行不互相等待 |
| 结果保序 | 带索引的结果槽恢复 model 原始顺序 |
| biased select 优先用户 | 用户消息 > 工具等待 |
| 分级取消 + 自修复 | CancellationToken 级联 + dangling repair |
| parking_lot 高频锁 | 减少系统调用，不中毒 |
| LocalRef 绕过 'static | 裸指针 + LocalSet 安全契约 |

---

## 十一、未覆盖功能全景：Grok Build 的完整能力图谱

> 以下是在前文 Agent 运行时、工具系统、权限安全、上下文管理、存储、异步架构之外，
> 尚未深入覆盖的功能模块。这些模块同样体现了围绕 LLM 不可靠性的防御性设计。

### 🎤 11.1 语音与多媒体

#### 语音输入 (`xai-grok-voice`)

流式语音转文本（STT）听写，将麦克风输入实时转录到 prompt 框：

```
麦克风 → cpal (macOS CoreAudio / Windows WASAPI)
  → Linux: 系统录音器 (pw-record / parec / arecord)
  → 流式 STT API（xAI 端点）
  → VoiceEvent → Pager → prompt 框文本
```

关键设计：
- `audio` feature gate：Linux 版不链接任何音频库（shell out 到系统工具），musl 静态二进制友好
- 28 种语言自动检测（`language` 模块）
- `VoiceProbe`：启动前验证麦克风链路健康
- `VoiceAuthProvider` trait：可替换的认证后端

#### 图片生成/编辑 (`ImageGenTool`, `ImageEditTool`)

模型可通过工具调用生成和编辑图片：
- `ImageGenClient` / `ImageEditClient` 对接 xAI 图片 API
- 客户端在 `finalize()` 时检查凭证可用性才注入 Resources
- 带 `attribution_callback` 的归因追踪

#### 视频生成 (`VideoGenTool`)

模型可调用视频生成工具：
- `VideoGenClient` 对接 xAI 视频生成 API
- 通过 `is_enabled()` 配置门控

---

### 🎨 11.2 TUI/UX 增强

#### Mermaid 图表渲染 (`xai-grok-mermaid`)

纯 Rust 实现的 Mermaid→PNG 渲染，**零 Node/无头浏览器/无网络**：

```
Pipeline:
  1. MermaidEngine trait（默认为 PureRustEngine）
     → vendored dagre_rust + graphlib_rust 布局
     → mermaid-to-svg 生成 SVG
  2. rasterize（resvg + usvg + tiny-skia）
     → SVG → PNG
```

安全设计——因为 Mermaid 源码是**不可信模型输出**：

| 层级 | 机制 | 目的 |
|:---|:---|:---|
| `render_checked()` | 源码大小限制 + `catch_unwind` | Panic → 错误而非崩溃 |
| 子进程隔离 | `mermaid_worker` 短命子进程 + `run_with_timeout` | 生产构建 `panic=abort` 时子进程崩溃不拖累主进程 |
| 无远程解析器 | resvg 配置 `no remote/file resolvers` | 防 SVG XXE/SSRF 攻击 |
| 内置字体 | Bundled font | 确定性渲染，跨机器一致 |

支持亮/暗主题（`MermaidTheme`）、最大百万像素限制（`MAX_OUTPUT_MEGAPIXELS`）。

#### Agent Dashboard

`grok dashboard` 或 `Ctrl+\` 或 `/dashboard` 打开集中会话总览：

```
 Grok Build · Dashboard — 4 agents · 2 awaiting
▌● reviewer · audit token flow    Awaiting your input            2m
 ● implementer · fix login bug    Running: cargo test           12m
 ⋅ refactor · feat/login          Responding…                   24m
 ○ housekeeping                   idle                           1h
```

特性：
- 按状态分组排序（Needs input → Working → Idle → Completed → Failed）
- 空闲分组折叠（最近 8 个 + 1h 内活跃），其余折叠到 "+N more"
- Peek 面板：选中 agent → 最后响应预览 + 可排队回复
- Dispatch 输入：从 Dashboard 直接启动新 agent
- `Ctrl+/` 搜索过滤（前缀 `a:` agent 名、`s:` 状态、`#` 文本）
- Pin + 拖拽排序 + 重命名
- 不显示 Subagent（只显示顶级 session）

#### 主题系统

- 5 个内置主题：GrokNight, GrokDay, TokyoNight, RosePineMoon, OscuraMidnight
- `auto` 模式跟随 OS 明暗（macOS `AppleInterfaceStyle` / Linux XDG Desktop Portal / Windows Registry，5 秒轮询）
- 自动量化 RGB 到终端颜色级别（truecolor / 256-color / 16-color）
- `pager.toml` 深度自定义：布局、滚动条、动画 FPS、每种 block 类型的颜色

#### Vim 模式 + Minimal TUI

- **Vim 模式**：`j`/`k` 导航，`h`/`l` 折叠，`y`/`Y` 复制，`e` 展开，`i` 聚焦输入
- **Minimal 模式**（`xai-grok-pager-minimal`）：scrollback 原生渲染（不 alternate screen），完成块直接 `insert_before` 写入终端历史，仅底部保留 live region
- 完整键盘快捷键自定义（`[keybindings]` in config.toml）

---

### 🛡 11.3 基础设施

#### 崩溃处理 (`xai-crash-handler`)

```
启动时的恢复流程：
  check_previous_crash(crash_dir)
    → 读取 last-crash.bin
    → 符号化 backtrace（macOS/Linux 符号表解析）
    → 写入人类可读 last-crash-report.txt
    → 归档到 history/ 目录（保留最近 5 个）

运行时的信号处理：
  Unix: sigaction(SIGBUS, SIGSEGV)
  Windows: SetUnhandledExceptionFilter
  → 信号安全地记录 crash info → 写入 last-crash.bin

终端恢复：install_terminal_restore_only()
  → 崩溃时恢复 termios/转义序列 → 终端不会乱码
```

#### 自动更新 (`xai-grok-update`)

- 轮询版本端点 → 比较本地版本 → 下载新二进制
- `indicatif` 进度条显示
- 阻塞/非阻塞两种模式
- Channel 标签（alpha/stable）动态追加
- `enforce_minimum_version_or_exit()`：低于最低版本时强制升级

#### 休眠阻止 (`xai-system-power`)

防止长任务期间系统睡眠：

| OS | 机制 |
|:---|:---|
| macOS | IOKit `IOPMAssertionCreateWithName` |
| Linux | systemd-inhibit |
| Windows | `SetThreadExecutionState` |

额外功能：`SystemPowerListener` 监听 WillSleep/DidWake 事件——在睡眠前保护 OIDC token 刷新（防止睡眠前旋转了 token 但未持久化，醒来后死 token 强制重新登录）。

#### 密钥脱敏 (`xai-grok-secrets`)

自动扫描和脱敏，覆盖 10+ 种模式：
- API key 前缀：`sk-`、`xai-`、`sk_`
- AWS 密钥：`AKIA`/`ASIA`
- GitHub/GitLab/Slack tokens
- Google API keys (`AIza`)
- PEM 私钥块、Bearer tokens、裸 JWT (`eyJ...`)
- Secret 赋值模式：`api_key=...`、`password=...`

消费位置：telemetry、Mixpanel、debug logging、clipboard。URL 中的敏感参数值替换为 `[REDACTED_SECRET]`。

#### 公告系统 (`xai-grok-announcements`)

- 远程设置或 ACP notification 推送
- `RemoteAnnouncement`：标题、消息、严重性、CTA（带 label/URL/caption）、可关闭、持久、过期
- `~/.grok/announcements.json` 持久化隐藏状态
- `GROK_ANNOUNCEMENTS_OVERRIDE` 环境变量调试覆盖

---

### 🔧 11.4 开发与扩展生态

#### Plugin Marketplace (`xai-grok-plugin-marketplace`)

Git-based 插件分发系统：

```
官方便利源: github.com/xai-org/plugin-marketplace
  ├─ index 模块: 解析插件索引
  ├─ scanner: 文件系统扫描已安装插件
  ├─ installer: 通过 InstallRegistry 安装
  ├─ matcher: 模糊名称匹配
  └─ config: 多源配置（官方 + 社区源）
```

插件可包含：skills、subagents、agents、hooks、MCP servers。

#### Hooks 系统 (`xai-grok-hooks` + `xai-agent-lifecycle`)

完整生命周期钩子，shell 脚本或 HTTP 回调：

| 钩子 | 触发时机 | 可阻止？ |
|:---|:---|:---|
| `PreToolUse` | 工具执行前 | ✅ 可阻止或修改 |
| `PostToolUse` | 工具执行后 | ❌ |
| `PostToolUseFailure` | 工具执行失败 | ❌ |
| `SessionStart` | 会话启动 | ❌ |
| `SessionEnd` | 会话结束 | ❌ |
| `TurnStart` | 每轮开始 | ❌ |
| `Notification` | 通知事件 | ❌ |
| `Stop` | Agent 停止 | ❌ |
| `PreCompact` | Compaction 前 | ✅ 可阻止 |

Hook payload 有 `MAX_PAYLOAD_SIZE` 截断保护。

#### Session Fork / Rewind / 导入

- **Fork**（`fork.rs`）：从已有 session 复制到新目录 + 新模型。可选截断到 `target_prompt_index`
- **Rewind**（`rewind.rs`）：基于 checkpoint 回滚对话 + 文件状态
- **Claude/Cursor 导入**（`claude_import.rs`）：迁移 CLAUDE.md、skills、rules、sessions

#### ACP 协议 (`xai-acp-lib` + `agent-client-protocol`)

Agent Client Protocol — IDE 嵌入标准：
- stdio 传输（`grok agent stdio`）
- WebSocket relay（`grok agent --relay`）
- Session 级别双向 RPC
- 类型化消息：`session/new`、`session/prompt`、`session/load` 等

---

### 🧠 11.5 代码智能

#### 代码图谱 (`xai-codebase-graph`)

tree-sitter 驱动的代码符号索引：

```
IndexBuilder
  ├─ tree-sitter 查询: go-to-def, go-to-refs
  ├─ rayon 并行解析（可配置线程数）
  ├─ mmap 零拷贝文件读取
  └─ mmap 二进制缓存（快速重载）

IndexManager::spawn()
  ├─ 后台 Actor 模式
  ├─ 接收 FileEvent (create/modify/delete)
  ├─ 增量重索引变更文件
  └─ IndexManagerHandle: 无克隆的查询 API

Navigator: 位置查询
  goto_definition(file, row, col) → Vec<Location>
```

`StringInterner` 减少符号字符串内存占用。`ScopeGraph` 支持局部定义、imports、引用等更丰富的符号解析。

#### Hunk Tracker (`xai-hunk-tracker`)

Actor 模式的 diff 追踪，区分来源：

```
HunkTrackerActor
  ├─ TrackingMode::AllDirty / OnlyAgent
  ├─ record_agent_write() ← Agent 工具调用
  ├─ fs_notify events → 标记外部修改
  ├─ get_all_hunks() → 查询当前所有 hunks
  ├─ hunk_action(Accept/Reject/Undo)
  └─ HunkEvent 流 → SessionActor 消费
```

`LocSink` 系统聚合代码行数变化指标，带丰富的归因（作者类型、来源类型、事件类型）。

#### LSP 集成 (`LspTool`)

`async-lsp` 对接 Language Server Protocol：
- Go-to-definition、hover、diagnostics
- 模型通过 LSP 工具获得 IDE 级代码理解能力

---

### ⏳ 11.6 后台与自动化

#### 后台任务

```
模型调用 run_terminal_cmd(background: true)
  → 命令在后台启动
  → 立即返回 task_id
  → 完成后通知注入 conversation

Ctrl+G: 将前台命令降级到后台

get_command_or_subagent_output(task_id, timeout_ms?)
  → 非阻塞查询当前输出
  → 或阻塞等待完成（可中断）

wait_commands_or_subagents(task_ids, mode=wait_any|wait_all, timeout_ms)
  → 最多 20 个任务并发等待

kill_command_or_subagent(task_id)
  → SIGTERM → 等待 → SIGKILL
```

#### /loop 命令 + Scheduler

- **`/loop`**：重复执行提示词，支持间隔配置（如 `/loop 5m "check deploy status"`）
- **Scheduler**（`SchedulerCreateTool` + `SchedulerState`）：类似 cron 的定时任务
  - create/delete/list 完整 CRUD
  - `SchedulerActor` 在 FinalizedToolset 中运行
  - 触发时注入 `<system-reminder>`

#### Monitor 工具

观察文件变化并触发动作：
- `MonitorState` 管理活跃监控器
- 文件变化 → 触发配置的动作

---

### 📊 11.7 遥测与可观测性

#### 三层遥测架构

| 层级 | 目标 | 机制 |
|:---|:---|:---|
| **内部日志** | 调试/审计 | `unified_log` → `~/.grok/logs/unified.jsonl`（结构化 JSON） |
| **产品分析** | 功能使用统计 | `xai-mixpanel`（自建 HTTP client，base64 编码 JSON，密钥脱敏在 token 注入前完成） |
| **外部 OTel** | 企业监控 | OTLP HTTP exporter（双重选择加入 + 三层隐私守卫） |

#### 外部 OpenTelemetry 导出

**双重选择加入**（防止无意泄露）：
```
GROK_EXTERNAL_OTEL=1         ← 主开关
+ OTEL_METRICS_EXPORTER=otlp  ← 指标导出
+ OTEL_LOGS_EXPORTER=otlp     ← 日志导出
```

**三层隐私守卫**：

```
1. 内容默认脱敏：prompts/code/file paths/shell cmds/MCP names → 分类标签
2. 可选放行：OTEL_LOG_USER_PROMPTS / OTEL_LOG_TOOL_DETAILS
3. 导出前验证：任何含非 schema key 或未脱敏字段的记录 → 丢弃，不离开进程
```

**指标**：session counts、token usage（by type/model）、turn counts（by outcome）、tool decisions/usage、error counts。

**日志事件**：session lifecycle、user prompts、turn completion、API requests/errors、tool results、MCP connections、permission changes、skill activation、plugin loading、compaction、subagent lifecycle、auth。

#### 统一日志 (`xai-grok-telemetry`)

- `unified_log`：结构化 JSON 日志追加器
- `session_ctx`：per-session 遥测上下文，收集 token counts、tool calls、duration
- `instrumentation`：Chrome-trace 兼容的 span timing（启动性能分析）
- `sentry`：错误报告集成（breadcrumbs + session tracing）
- `memory_telemetry`：进程内存使用追踪
- `tracing-macros`：`#[timed]`、`tprintln!` 等过程宏

---

### 🤖 11.8 模型管理

#### 模型目录 (`xai-grok-models`)

`default_models.json` 编译时嵌入（`include_str!`）→ `LazyLock` 单例：

```json
{
  "default": "grok-build",
  "web_search": "grok-build",
  "image_description": "grok-4.1",
  "session_summary": "grok-4.20",
  "models": [...]
}
```

启动时断言验证 default model 在 models 数组中存在。

#### 自定义模型

三种第三方后端支持，`[model.*]` TOML 配置：
- **BYOK**（Bring Your Own Key）：自定义 API key + endpoint
- **Ollama**：本地模型（`http://localhost:11434`）
- **OpenAI 兼容**：任意兼容 `/v1/chat/completions` 的后端

选择方式：CLI `-m`、`/model` 命令、Ctrl+M 选择器、config default。

#### 推理努力等级

`/effort` 命令：`none` → `minimal` → `low` → `medium` → `high` → `xhigh` → `max`

对推理模型控制 thinking token 预算，对应 API 的 `reasoning_effort` 参数。

#### 中会话模型切换 (`model_switch.rs`)

```
切换流程：
  1. 更新 sampling config（model, base_url, context_window, max_tokens, temperature 等）
  2. 更新认证凭据（api_key, auth_type）
  3. 清除 model_auth_facts 缓存（新模型的 BYOK 状态重新解析）
  4. 可选替换 system prompt（使用 concise 模型时用 COMPACT_SYSTEM_PROMPT）
  5. 持久化 model ID
  6. 更新 compaction 阈值 + 计数器
  7. 中止进行中的 laziness 分类器（在旧模型下触发）
```

---

### 🔐 11.9 杂项

#### 终端兼容性

`xai-tty-utils` + `ptyctl` + `portable-pty`：
- 检测终端模拟器（Apple Terminal / Ghostty / iTerm2 / Warp / WezTerm / Kitty / Alacritty / Rio / foot）
- 根据终端适配行为
- `/terminal-setup` 诊断：颜色级别、可用主题、剪贴板路由
- tmux 推荐配置（clipboard + passthrough + `terminal-features`）
- Bracketed paste + OSC 52 clipboard

#### Git Worktree (`xai-fast-worktree`)

```
--worktree / -w 标志：
  git worktree add --no-checkout → 元数据秒建
  ├─ CoW (copy-on-write) 文件克隆（hash-based sharding 并行）
  ├─ Linux BTRFS snapshot（O(1) 克隆）
  ├─ Overlay 文件系统 snapshot
  └─ 可选复制 dirty / ignored 文件

WorktreeDb（SQLite，metadata feature）：
  ├─ 追踪所有 worktree（状态、类型、元数据）
  ├─ discover_worktrees() 从文件系统重建
  └─ gc_worktrees() 按年龄 + 磁盘占用清理

WorktreeSync：rehydrate、snapshot、transfer、synchronize
```

#### Markdown 渲染 (`xai-grok-markdown`)

- 流式渲染：`StreamingMarkdownRenderer`（只重渲染未冻结的 "tail"）
- `syntect` 语法高亮 + 内置 `.tmTheme` 文件
- 颜色量化到终端级别（256-color / 16-color）
- LaTeX 数学公式 Unicode 近似渲染
- Mermaid 边界检测（inline 渲染触发）
- GFM 扩展（表格、删除线、任务列表、数学公式）

**Markdown 结构检测**（`xai-grok-markdown-core`）：
- 22 种 Markdown 元素计数
- `MalformedTable` 检测：GFM 分隔行列数不匹配 → 表渲染为段落
- `UnterminatedCodeBlock` 检测：代码块无闭合 → 吞掉剩余消息
- 用于渲染保真度监控

#### PTY 测试基础设施

`xai-grok-pager-pty-harness`：
- PTY 驱动的 E2E 测试框架
- `PtyController`：spawn / keystrokes / resize / drain
- `Screen`：virtual terminal state（`alacritty_terminal`）
- `Timing`：per-frame 耗时（`?2026 h/l` markers）
- `Content`：mock inference server（SSE via axum）
- `Scenarios`：命名的参数化测试场景
- `Results`：聚合统计 + baseline 对比
- `write_cast`：导出 asciinema v2 cast 文件（可视化调试）
- `scripted`：声明式场景定义

`ptyctl` / `ptyctl-cli`：独立 PTY 管理工具（HTTP REST API + CLI）

#### HTTP 客户端层 (`xai-grok-http`)

三个 `OnceLock` 缓存的 `reqwest::Client` 实例：
- `shared_client()`：HTTP/2，连接池，30s idle，HTTP/2 keepalive ping（防 LB 静默断连）
- `shared_upload_client()`：HTTP/1.1 only，小连接池（2/host），10s aggressive idle（GCS 上传）
- `shared_blocking_client()`：异步 runtime 启动前的早期模型 prefetch
- `send_with_retry_escaping_pool()`：最终重试用无池 HTTP/1.1 client 逃逸被污染的连接池

---

### 📋 11.10 功能完备性总结

| 类别 | 功能数 | 代表性模块 |
|:---|:---|:---|
| 语音/多媒体 | 4 | Voice dictation, ImageGen, ImageEdit, VideoGen |
| TUI/UX | 5 | Mermaid 纯 Rust 渲染, Dashboard, 主题, Vim 模式, Minimal TUI |
| 基础设施 | 5 | Crash handler, Auto-update, 休眠阻止, 密钥脱敏, 公告 |
| 扩展生态 | 5 | Plugin Marketplace, Hooks, Fork/Rewind, Claude 导入, ACP |
| 代码智能 | 3 | Codebase graph (tree-sitter), Hunk tracker, LSP |
| 后台/自动化 | 4 | 后台任务, /loop, Scheduler cron, Monitor |
| 遥测 | 4 | Mixpanel, OTel 导出, 统一日志, Tracing macros |
| 模型 | 4 | 模型目录, 自定义模型/BYOK, 推理努力, 中会话切换 |
| 终端/基础设施 | 4 | 终端检测, Worktree, Markdown 流式渲染, PTY 测试 |
| HTTP/网络 | 1 | 分场景多 Client 实例 (HTTP/2 vs HTTP/1.1) |

> **结论**：Grok Build 是一个全功能的 AI 编程代理平台。它不仅仅是 "LLM + 工具调用的胶水"——
> 它在语音交互、可视化（Mermaid）、代码智能（tree-sitter + LSP）、插件生态、崩溃恢复、
> 隐私保护（密钥脱敏 + OTel 三层守卫）、终端兼容性等方面都有完整的实现。
> 这些功能同样体现了围绕 LLM 不可靠性的防御性设计哲学——例如 Mermaid 的子进程隔离渲染、
> 崩溃 handler 的启动前检测 + 符号化 + 终端恢复、OTel 的三层隐私守卫等。
