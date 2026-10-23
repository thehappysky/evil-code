# Phase 2 Module 3: compaction/ — Dialog Compression

> Phase: 2 | Status: DONE | Created: 2026-07-25

---

## Scope

| Feature | Description |
|:---|:---|
| CompactionPolicy | Token threshold trigger strategy: min_tokens + percentage of context_window |
| generate_summary | LLM-based conversation summarization with input fitting + retry logic |
| assemble_compacted_history | Post-compaction history assembly: [system, project_rules?, summary, ...recent, reminders?] |
| classify_compact_error | Error classifier for retry logic: deterministic vs transient |
| count_preserved_turns | Split conversation into to-summarize and to-preserve by token budget |

---

## Final Architecture

```
code_agent/compaction/
├── __init__.py          # exports: CompactionPolicy, generate_summary, assemble_compacted_history, classify_compact_error
├── policy.py            # CompactionPolicy: should_compact(), target_budget(), update_context_window() (65 lines)
├── summarizer.py        # generate_summary(), classify_compact_error(), _fit_conversation() (183 lines)
└── assembler.py         # assemble_compacted_history(), count_preserved_turns() (127 lines)
```

---

## Implementation Status

| Item | Status | File |
|:---|:---|:---|
| CompactionPolicy — context_window, threshold_percent, min_tokens | Done | `compaction/policy.py` |
| should_compact() — min_tokens gate + percentage threshold | Done | `compaction/policy.py` |
| target_budget() — 50% of context_window for post-compaction room | Done | `compaction/policy.py` |
| update_context_window() — dynamic window from model metadata | Done | `compaction/policy.py` |
| SummaryClient Protocol — minimal async chat() interface | Done | `compaction/summarizer.py` |
| generate_summary() — input fitting + LLM call + retry + clean | Done | `compaction/summarizer.py` |
| _fit_conversation() — keep system + last user + as many prefix as fit | Done | `compaction/summarizer.py` |
| classify_compact_error() — httpx errors + value/type errors | Done | `compaction/summarizer.py` |
| _clean_summary() — strip prefixes, code fences, meta-commentary | Done | `compaction/summarizer.py` |
| assemble_compacted_history() — 5-layer structure | Done | `compaction/assembler.py` |
| count_preserved_turns() — walk backwards, keep complete turns | Done | `compaction/assembler.py` |

**Progress: 11/11 items complete**

---

## Design Decisions

- **SummaryClient Protocol**: Minimal interface (only `chat()`), no dependency on full ChatClient
- **Input fitting**: 70% of context_window reserved for conversation, minus system + last message
- **Retry logic**: 2 attempts, only on transient errors (network/server 500+)
- **Summary injection as user message**: Model sees it as context, not instruction
- **Preserved turns**: Walk backwards through messages, keep within token budget
