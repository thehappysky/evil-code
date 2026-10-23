# [INT-09] 2026-08-09: Plugin hook registration helpers.
# Moved from cli/assembly.py. The hook-name convention mapper
# (hook_event_name_for) and the dispatcher registration adapter
# (register_plugin_hook + wrapped closure) are plugin/hook orchestration
# logic and belong with the plugins package, not in the CLI assembly root.
"""Plugin hook registration by name convention.

Wires plugin ``hooks.py`` callables into the ``HookDispatcher`` by a
name convention: a plugin may define functions whose name matches a
``HookEvent`` value (e.g. ``pre_tool_use``, ``post_tool_use``,
``session_start``, ``turn_start``, ``turn_end``), optionally with an
``on_`` prefix. Unknown names are skipped (caller warns).

The plugin handler is called with the event context dict; its return
is normalized to a ``HookResult`` (HookResult passthrough, dict with
``action`` key, or default ``allow``). Handler exceptions degrade to
``allow`` (best-effort, never raises into the dispatch loop).
"""

from __future__ import annotations

import inspect
import logging

from code_agent.hooks.dispatcher import HookEvent, HookResult

logger = logging.getLogger(__name__)


def hook_event_name_for(fn_name: str) -> str | None:
    """Map a plugin hook function name to a HookEvent enum value.

    Convention: a plugin's hooks.py may define functions whose name matches a
    HookEvent value (e.g. ``pre_tool_use``, ``post_tool_use``,
    ``session_start``, ``turn_start``, ``turn_end``). Returns None for
    unknown names so the caller can warn.

    Args:
        fn_name: The plugin handler function's ``__name__``.

    Returns:
        The matched event name string, or ``None`` if the name doesn't
        match any known hook event.
    """
    name = fn_name.lower()
    if name.startswith("on_"):
        name = name[3:]
    known = {
        "pre_tool_use", "post_tool_use", "post_tool_use_failure",
        "session_start", "session_end", "turn_start", "turn_end",
    }
    return name if name in known else None


def register_plugin_hook(dispatcher, handler) -> bool:
    """Register a plugin hook handler into the dispatcher by name convention.

    The plugin handler is called with the event context dict; its return is
    normalized to a HookResult. Returns True if registered.

    Args:
        dispatcher: HookDispatcher instance.
        handler: async/sync callable from the plugin's hooks.py.

    Returns:
        True if a matching event name was found and the handler registered.
    """
    event_name = hook_event_name_for(handler.__name__)
    if event_name is None:
        return False

    event = HookEvent(event_name)

    async def wrapped(event, context):
        try:
            result = handler(context)
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, HookResult):
                return result
            if isinstance(result, dict) and "action" in result:
                return HookResult(
                    action=result.get("action", "allow"),
                    message=result.get("message"),
                    modified_args=result.get("modified_args"),
                )
            return HookResult(action="allow")
        except Exception:
            logger.warning(
                "Plugin hook '%s' failed", handler.__name__, exc_info=True,
            )
            return HookResult(action="allow")

    dispatcher.register(event, wrapped)
    return True


__all__ = ["hook_event_name_for", "register_plugin_hook"]
