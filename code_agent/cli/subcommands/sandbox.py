"""`code-agent sandbox` subcommand — manage Docker sandbox environments."""

from __future__ import annotations

import asyncio
import os

from code_agent.sandbox.manager import SandboxManager


def run_sandbox_command(args: list[str]) -> None:
    """Handle `code-agent sandbox list/gc/protect/remove/...` subcommands."""
    if not args or args[0] in ("-h", "--help"):
        print("evil-code sandbox -- manage sandbox environments\n")
        print("Usage:")
        print("  code-agent sandbox status")
        print("  code-agent sandbox list")
        print("  code-agent sandbox gc")
        print("  code-agent sandbox protect <project_id>")
        print("  code-agent sandbox unprotect <project_id>")
        print("  code-agent sandbox remove <project_id>")
        print("  code-agent sandbox clean-cache")
        print("")
        print("Environments are per-project Docker volumes; source code is never deleted.")
        return

    sub = args[0]
    mgr = SandboxManager(mode="auto", cwd=os.getcwd())

    async def _async_run() -> None:
        if sub == "status":
            print(mgr.status())
        elif sub == "list":
            rows = mgr.list_projects()
            if not rows:
                print("No sandbox environments recorded.")
                return
            print(f"{'project_id':<18} {'lifecycle':<12} {'importance':<10} {'protected':<10} volume")
            for r in rows:
                print(
                    f"{r['project_id']:<18} {r['lifecycle']:<12} "
                    f"{r['importance']:<10.0f} {str(r['protected']):<10} "
                    f"{r['volume'] or '-'}"
                )
        elif sub == "gc":
            stats = await mgr.gc()
            print(stats)
        elif sub == "clean-cache":
            stats = await mgr.clean_cache()
            print(stats)
        elif sub == "protect" and len(args) > 1:
            print("protected" if mgr.protect_project(args[1]) else "not found")
        elif sub == "unprotect" and len(args) > 1:
            print("unprotected" if mgr.unprotect_project(args[1]) else "not found")
        elif sub == "remove" and len(args) > 1:
            print("removed" if mgr.remove_project(args[1]) else "not found")
        else:
            print(f"Unknown sandbox subcommand: {sub!r} (see 'code-agent sandbox --help')")

    asyncio.run(_async_run())
