"""SSE (Server-Sent Events) stream parser.

Shared by all three LLM API backends. Reads raw text lines from an
httpx streaming response and yields parsed JSON event dicts.

SSE wire format (RFC 8895, simplified):
- "data: {json}" lines carry the payload
- "data: [DONE]" signals stream termination (Chat Completions)
- "event: xxx" lines mark the event type (Messages/Responses)
- Empty lines are event delimiters
- Lines starting with ":" are comments (keepalive)

Each yielded dict is the parsed JSON payload. If an "event:" line
preceded the data, the dict gets an "_event" key with the type string.

Logic reference: Rust version SamplingClient::post() SSE line parsing
(client.rs:550+). Three backends share this parser; each backend's
parse_stream() only interprets the JSON content, not the SSE framing.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import httpx

logger = logging.getLogger(__name__)


async def iter_sse_events(response: httpx.Response) -> AsyncIterator[dict]:
    """Parse an httpx streaming response as SSE events.

    Yields one dict per SSE event. The dict is the parsed JSON payload
    from the "data:" line(s). If an "event:" line preceded the data,
    the dict includes an "_event" key with the event type string.

    Special: "data: [DONE]" yields {"_done": True} (Chat Completions
    termination signal).

    Args:
        response: An httpx.Response obtained via client.stream().

    Yields:
        dict: Parsed SSE event JSON, with optional "_event" key.
    """
    event_type: str | None = None
    data_lines: list[str] = []

    async for line in response.aiter_lines():
        line = line.strip()

        # Empty line = event delimiter
        if not line:
            if data_lines:
                data_str = "\n".join(data_lines)
                data_lines.clear()
                if data_str == "[DONE]":
                    yield {"_done": True}
                else:
                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        logger.debug("Skipping invalid SSE JSON: %s", data_str[:200])
                        event_type = None
                        continue
                    if event_type:
                        event["_event"] = event_type
                        event_type = None
                    yield event
            continue

        # SSE comment (keepalive)
        if line.startswith(":"):
            continue

        # Event type line
        if line.startswith("event:"):
            event_type = line[6:].strip()

        # Data line
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())

    # Flush any remaining data after stream ends
    if data_lines:
        data_str = "\n".join(data_lines)
        if data_str != "[DONE]":
            try:
                event = json.loads(data_str)
                if event_type:
                    event["_event"] = event_type
                yield event
            except json.JSONDecodeError:
                pass
