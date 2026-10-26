# 文档图片与来源

`tui-demo.svg` 是实际 Textual 应用通过 `run_test()` 导出的界面；`tui-demo.png` 是同一 SVG 的栅格渲染。模型响应与工具内容为明确标注的离线演示数据，不使用用户凭证、不访问模型服务、不修改项目文件。

生成来源：`scripts/capture_readme.py`。在已安装 `.[dev,tui]` 的项目环境下，从根目录运行：

```powershell
.\.venv-dev\Scripts\python.exe scripts/capture_readme.py
```

其他 SVG 流程图来自 `docs/diagrams/*.mmd`，README 内嵌相同 Mermaid 内容。同步检查使用 `scripts/check_readme.py`，浏览器渲染使用 `scripts/render_readme.cjs`。

生成图片仅用于文档，不是运行应用的依赖。改变界面或架构后，应更新源文件、重新生成并检查图片，再提交。

`evaluation-report.png` 是 2026-10-26 真实 DeepSeek V4 Pro 核心集评测生成的 HTML 报告顶部（16 题 × 2 次，30 成功、2 失败），不是模拟数据。对应的可维护图表源为 `code_agent/evals/report.py`，公开 HTML 在 `progress/evidence/2026-10-26/live-core-report.html`。

通过 `scripts/capture_evaluation.cjs 运行目录 输出.png --viewport` 在 Edge 中截图；设置 `PLAYWRIGHT_MODULE` 指向已安装的 Playwright。原始运行目录保留于本机 `.eval-runs/`，无须为重建截图再次调用模型；也可把公开 HTML 复制为某个目录中的 `report.html` 后截图。截图不含 API key、原始轨迹或个人文件。
