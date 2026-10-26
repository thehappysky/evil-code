"""Usage must include the start event before it can drive billing estimates."""

from code_agent.api.backends.messages import MessagesBackend


async def parse(*events):
    async def source():
        for event in events:
            yield event
    return [event async for event in MessagesBackend().parse_stream(source()) if event.kind == "turn_end"]


async def test_initial_input_and_cache_usage_survive_output_delta():
    result = await parse(
        {"_event":"message_start","message":{"usage":{"input_tokens":100,"cache_read_input_tokens":40,"output_tokens":0}}},
        {"_event":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":12}},
        {"_event":"message_stop"},
    )
    assert len(result) == 1
    assert result[0].usage["prompt_tokens"] == 140
    assert result[0].usage["cached_prompt_tokens"] == 40
    assert result[0].usage["completion_tokens"] == 12


async def test_missing_input_usage_is_unknown_instead_of_zero():
    result = await parse({"_event":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":12}})
    assert result[0].usage is None


async def test_cumulative_output_updates_are_not_summed():
    result = await parse(
        {"_event":"message_start","message":{"usage":{"input_tokens":15,"output_tokens":0}}},
        {"_event":"message_delta","delta":{},"usage":{"output_tokens":4}},
        {"_event":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":9}},
    )
    assert result[0].usage["prompt_tokens"] == 15
    assert result[0].usage["completion_tokens"] == 9
