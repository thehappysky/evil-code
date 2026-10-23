"""Import order must not determine whether sandbox policy works."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize("module", ["code_agent.sandbox.policy", "code_agent.permission.manager", "code_agent.sandbox.manager"])
def test_independent_import(module):
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}; from code_agent.permission import PermissionManager, Decision"],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
