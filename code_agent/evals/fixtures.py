"""Deliberately answer-aware fixtures for harness self-tests, never model scores."""

import json

from code_agent.api.schemas import StreamEvent, ToolCallDelta


class FixtureClient:
    def __init__(self, task, *, reference=True):
        self.task = task
        self.reference = reference
        self.model = "fixture-reference" if reference else "fixture-noop"
        self.calls = 0

    async def chat_stream(self, messages, tools, **kwargs):
        self.calls += 1
        actions = self.task.reference_actions if self.reference else []
        if self.calls <= len(actions):
            action = actions[self.calls - 1]
            yield StreamEvent(kind="tool_call_delta", tool_call=ToolCallDelta(
                index=0, id=f"fixture-{self.calls}", name=action.name,
                arguments_fragment=json.dumps(action.arguments),
            ))
        else:
            yield StreamEvent(kind="text_delta", text=self.task.reference_text if self.reference else "未执行任务。")
        yield StreamEvent(kind="turn_end")

    async def close(self):
        pass
