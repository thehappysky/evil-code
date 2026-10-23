# Phase 3：TUI + Skills + Plan Mode - 进度

> 目标: Textual 全屏 TUI、流式渲染、Skills 系统、Plan Mode
> 预估工时: 8-10 天
> 状态: 未开始
> 依赖: Phase 2 完成

---

## 范围

1. **TUI**: Textual App，Scrollback/Prompt/Diff/Thinking 组件
2. **流式渲染**: Agent yield -> TUI 事件队列 -> 增量渲染
3. **Skills**: SKILL.md 发现 + YAML frontmatter 解析 + 注入
4. **Plan Mode**: 状态机（ACTIVE -> 写工具被拦截 -> 退出）
5. **Headless**: `code-agent -p "..." --output-format json`

---

## 最终架构（Phase 3 新增）

```
code_agent/
├── tui/               # 新增: Textual TUI 层
│   ├── app.py         #   App 入口
│   ├── screens/chat.py #  主聊天界面
│   ├── widgets/       #   scrollback, prompt, diff_view, thinking, status_bar
│   ├── renderer.py    #   Markdown -> Rich Text
│   ├── syntax.py      #   Pygments 封装
│   └── theme.py       #   主题管理
├── skills/            # 新增: Skills 系统
│   ├── discovery.py   #   扫描 SKILL.md 文件
│   ├── parser.py      #   YAML frontmatter 解析
│   ├── manager.py     #   Skill 生命周期
│   └── types.py       #   SkillInfo 模型
├── cli/
│   └── headless.py    # 新增: Headless 入口
└── core/
    └── plan_mode.py   # 新增: Plan Mode 状态机
```

---

## Phase 3 新增的 Hook

| Hook 类 | 方法 | 职责 |
|:---|:---|:---|
| PlanModeHook | on_turn_start, on_tool_call, on_turn_end | Plan Mode 状态机 |

---

## 模块进度

（Phase 3 启动后填充）

---

## 验证标准

**P0**: TUI 流式渲染正常工作；Plan Mode 状态机完整
**P1**: @ 文件引用 fuzzy 选择；Skills 被发现和调用
**P2**: Diff 渲染；ThinkingBlock 可折叠
