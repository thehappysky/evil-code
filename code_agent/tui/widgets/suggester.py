from __future__ import annotations

from collections.abc import Callable

from textual.suggester import Suggester

from code_agent.tui.commands import SlashCompletion


class SlashCommandSuggester(Suggester):
    """Show the same first completion that Tab will apply."""

    def __init__(
        self,
        completions: Callable[[str], tuple[SlashCompletion, ...]],
    ) -> None:
        super().__init__(use_cache=False, case_sensitive=False)
        self._completions = completions

    async def get_suggestion(self, value: str) -> str | None:
        completions = self._completions(value)
        if not completions or completions[0].value == value:
            return None
        return completions[0].value
