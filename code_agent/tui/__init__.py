"""Evil Code TUI — production terminal interface adapted from the reference repo.

Public surface:
    - ``EvilCodeApp``: the main interactive TUI application.
    - ``ProviderSetupApp``: first-run provider configuration wizard.
    - ``TUI_RELOAD_PROVIDER_SETTINGS``: exit code signalling the CLI to reload
      provider settings and relaunch (used after "Save and use").
"""

from code_agent.tui.tui import (
    TUI_RELOAD_PROVIDER_SETTINGS,
    EvilCodeApp,
    ProviderSetupApp,
)

__all__ = [
    "EvilCodeApp",
    "ProviderSetupApp",
    "TUI_RELOAD_PROVIDER_SETTINGS",
]
