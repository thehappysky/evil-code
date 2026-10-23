"""CLI subcommand handlers (config / sandbox / draft).

Each module is self-contained and imported on demand by ``cli.main`` —
subcommands must not import the full CLI assembly to keep startup cheap.
"""

from code_agent.cli.subcommands.config import run_config_command
from code_agent.cli.subcommands.draft import run_draft_command
from code_agent.cli.subcommands.sandbox import run_sandbox_command

__all__ = [
    "run_config_command",
    "run_draft_command",
    "run_sandbox_command",
]
