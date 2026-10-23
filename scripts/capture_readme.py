"""Capture the actual Textual UI with explicit offline demonstration data.

Run from the project root with the project's Python environment. No model
service or credential file is used; the tool response is a canned preview.
The exported SVG can be rendered to PNG by any SVG renderer.
"""

import asyncio
import json
from pathlib import Path

from textual.widgets import Input

from code_agent.api.schemas import StreamEvent, ToolCallDelta
from code_agent.core.session import Session
from code_agent.permission.manager import PermissionManager
from code_agent.tools.builtin.file_read import ReadFileInput
from code_agent.tools.registry import ToolRegistry
from code_agent.tools.result import ToolResult
from code_agent.tui.adapters import InstrumentedAgent, TuiAgentRunner
from code_agent.tui.domain import UiLanguage
from code_agent.tui.tui import EvilCodeApp


class DemoClient:
    model = "offline-demo"

    def __init__(self):
        self.calls = 0

    async def chat_stream(self, messages, tools, **kwargs):
        self.calls += 1
        if self.calls == 1:
            yield StreamEvent(kind="text_delta", text="先查看项目说明，再梳理运行流程。\n\n")
            yield StreamEvent(kind="tool_call_delta", tool_call=ToolCallDelta(
                index=0, id="demo-read", name="file_read",
                arguments_fragment=json.dumps({"file_path": "README.md"}),
            ))
        else:
            yield StreamEvent(kind="text_delta", text=(
                "## 项目入口与运行流程\n\n"
                "- **CLI / TUI**：接收任务与展示结果。\n"
                "- **Runtime**：统一客户端、工具与 Agent 的生命周期。\n"
                "- **Agent**：循环执行模型推理、权限检查与工具调用。\n\n"
                "建议先阅读 `code_agent/runtime/session.py`，再跟踪 `core/agent.py`。\n\n"
                "这是离线演示响应，用于展示真实界面的消息与工具反馈。"
            ))
        yield StreamEvent(kind="turn_end")


async def main():
    tools = ToolRegistry()

    @tools.register("file_read", ReadFileInput, description="Offline documentation preview", safety="read")
    async def demo_read(input):
        return ToolResult.ok("1: # Evil Code\n2: CLI / TUI -> shared runtime -> Agent -> tools")

    tools.finalize()
    agent = InstrumentedAgent(
        session=Session("Offline documentation demonstration"), client=DemoClient(),
        tools=tools, max_turns=3, hooks=[],
        permission_manager=PermissionManager(prompt_policy="deny"),
        provider_name="offline-demo", model_name="offline-demo",
    )
    app = EvilCodeApp(
        TuiAgentRunner(agent), provider_name="offline-demo", model_name="offline-demo",
        cwd=Path("evil-code"), language=UiLanguage.SIMPLIFIED_CHINESE,
    )
    output = Path("docs/assets")
    output.mkdir(parents=True, exist_ok=True)
    async with app.run_test(size=(118, 38)) as pilot:
        app.query_one("#prompt", Input).value = "说明这个项目的入口和运行流程，并给出阅读建议。"
        await pilot.press("enter")
        for _ in range(30):
            if any(entry.category == "assistant" and "离线演示响应" in entry.text for entry in app._entries):
                break
            await pilot.pause(0.1)
        else:
            raise RuntimeError("Demo response did not reach the transcript")
        await pilot.pause(0.2)
        filename = Path(app.save_screenshot("tui-demo.svg", path=str(output)))
        svg = filename.read_text(encoding="utf-8")
        filename.write_text("\n".join(line.rstrip() for line in svg.splitlines()) + "\n", encoding="utf-8")
        print(filename)


if __name__ == "__main__":
    asyncio.run(main())
