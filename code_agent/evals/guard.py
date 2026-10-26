"""Scoped tool adapter; this is a filesystem guard, not an OS sandbox."""

from pathlib import Path

from code_agent.core.hooks import HookDecision
from code_agent.tools.registry import ToolRegistry
from code_agent.tools.builtin.file_read import ReadFileInput, file_read
from code_agent.tools.builtin.file_write import WriteFileInput, file_write
from code_agent.tools.builtin.file_edit import SearchReplaceInput, file_edit
from code_agent.tools.builtin.file_delete import DeleteFileInput, file_delete


class WorkspaceGuard:
    def __init__(self, workspace: Path, allowed_writes: list[str]):
        self.root = workspace.resolve()
        self.writes = {(self.root / path).resolve() for path in allowed_writes}
        self.denied = 0
        self.forbidden = 0
        self.calls = 0
        self.errors = 0

    async def on_tool_call(self, name, args):
        self.calls += 1
        try:
            if name not in {"file_read", "file_write", "file_edit", "file_delete"} or not isinstance(args, dict):
                raise ValueError("unsupported tool")
            raw = args.get("file_path", "")
            if not isinstance(raw, str) or not raw:
                raise ValueError("missing path")
            target = (self.root / raw).resolve()
            if not target.is_relative_to(self.root):
                raise ValueError("outside workspace")
            if name != "file_read" and target not in self.writes:
                raise ValueError("write not allowed")
            args["file_path"] = str(target)
            return HookDecision.MODIFY
        except (OSError, ValueError):
            self.denied += 1
            self.forbidden += 1
            return HookDecision.DENY

    async def on_tool_result(self, name, args, result):
        self.errors += int(result.is_error)


def evaluation_tools() -> ToolRegistry:
    tools = ToolRegistry(max_output_chars=8000)
    for name, model, handler, safety in [
        ("file_read", ReadFileInput, file_read, "read"),
        ("file_write", WriteFileInput, file_write, "write"),
        ("file_edit", SearchReplaceInput, file_edit, "write"),
        ("file_delete", DeleteFileInput, file_delete, "write"),
    ]:
        tools.register(name, model, description=handler.__doc__ or name, safety=safety)(handler)
    tools.finalize()
    return tools
