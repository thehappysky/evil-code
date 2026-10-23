"""`code-agent draft` subcommand — git worktree working copies (experiment safety net)."""

from __future__ import annotations

import asyncio
import os

from code_agent.sandbox.draft import DraftError, DraftManager


def run_draft_command(args: list[str]) -> None:
    """Handle `code-agent draft create/list/status/merge/discard`."""
    if not args or args[0] in ("-h", "--help"):
        print("evil-code draft -- git worktree 工作副本（实验安全网）\n")
        print("Usage:")
        print("  code-agent draft create [PROJECT_ROOT]")
        print("  code-agent draft list")
        print("  code-agent draft status <draft_id>")
        print("  code-agent draft merge <draft_id>")
        print("  code-agent draft discard <draft_id>")
        return

    sub = args[0]
    mgr = DraftManager()

    async def _async_run() -> None:
        try:
            if sub == "list":
                rows = mgr.list_drafts()
                if not rows:
                    print("No drafts.")
                    return
                for row in rows:
                    print(
                        f"{row['draft_id']}  merged={row['merged']}  "
                        f"branch={row['branch']}  root={row['root']}"
                    )
            elif sub == "create":
                root = args[1] if len(args) > 1 else os.getcwd()
                rec = await mgr.create(root)
                print(f"created {rec.draft_id} -> {rec.root}")
                print(f"  branch : {rec.branch}")
                print(f"  volume : {rec.volume}")
                print(
                    "  实验命令请使用 workdir=<副本路径>；"
                    "完成后再 code-agent draft merge/discard <id>"
                )
            elif sub == "status" and len(args) > 1:
                st = await mgr.status(args[1])
                print(f"draft  : {st['draft_id']}  branch={st['branch']}")
                print(f"root   : {st['root']}")
                print(f"at_base: {st['at_base']}")
                print(f"changed:\n{st['changed']}")
            elif sub == "merge" and len(args) > 1:
                print(await mgr.merge(args[1]))
            elif sub == "discard" and len(args) > 1:
                print(await mgr.discard(args[1]))
            else:
                print(
                    f"Unknown draft subcommand: {sub!r} "
                    "(see 'code-agent draft --help')"
                )
        except DraftError as exc:
            print(f"[draft] {exc}")
            raise SystemExit(1) from None

    asyncio.run(_async_run())
