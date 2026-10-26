"""Run candidate Python only in a constrained, offline Docker container."""

import asyncio
import json
import uuid

from code_agent.evals.schema import Grade

SCRIPT = """import importlib.util,json,sys
data=json.load(sys.stdin)
spec=importlib.util.spec_from_file_location('candidate','/workspace/'+data['path'])
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
fn=getattr(module,data['function'])
print(json.dumps([fn(*args) for args in data['inputs']],ensure_ascii=False))
"""


async def bounded_read(stream):
    data = bytearray()
    while chunk := await stream.read(4096):
        data.extend(chunk)
        if len(data) > 65536:
            raise ValueError("output_limit")
    return bytes(data)


async def image_available():
    proc = None
    try:
        # Windows Docker may return 1 for an absent engine, which is also a
        # candidate's valid failure code. Check infrastructure independently.
        proc = await asyncio.create_subprocess_exec(
            "docker", "image", "inspect", "python:3.12-slim",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        return await asyncio.wait_for(proc.wait(), 5) == 0
    except (OSError, asyncio.TimeoutError):
        return False
    finally:
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()


async def docker_grade(check, workspace, timeout=10):
    if not await image_available():
        return Grade(kind="python_function", passed=None, reason="docker_unavailable_or_image_missing")
    name = "evil-eval-" + uuid.uuid4().hex[:16]
    proc = None
    readers = []
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "run", "--rm", "--pull=never", "--name", name,
            "--network=none", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--pids-limit=64",
            "--memory=256m", "--cpus=1", "--user=65534:65534",
            "--mount", f"type=bind,src={workspace.resolve()},dst=/workspace,readonly",
            "-i", "python:3.12-slim", "python", "-I", "-B", "-c", SCRIPT,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        # Expected answers never enter the container.
        proc.stdin.write(json.dumps({"path":check.path,"function":check.function,"inputs":check.inputs}).encode())
        await proc.stdin.drain()
        proc.stdin.close()
        readers = [asyncio.create_task(bounded_read(proc.stdout)), asyncio.create_task(bounded_read(proc.stderr))]
        stdout, _, code = await asyncio.wait_for(asyncio.gather(*readers, proc.wait()), timeout)
        if code == 125:
            return Grade(kind="python_function", passed=None, reason="docker_unavailable_or_image_missing")
        if code:
            return Grade(kind="python_function", passed=False, reason="candidate_execution_failed")
        try:
            actual = json.loads(stdout)
        except (ValueError, UnicodeError):
            return Grade(kind="python_function", passed=False, reason="invalid_candidate_output")
        matched = json.dumps(actual, sort_keys=True) == json.dumps(check.expected, sort_keys=True)
        return Grade(kind="python_function", passed=matched, reason="behavior_matched" if matched else "behavior_mismatch")
    except (asyncio.TimeoutError, ValueError):
        return Grade(kind="python_function", passed=False, reason="candidate_time_or_output_limit")
    except (OSError, BrokenPipeError, ConnectionResetError):
        return Grade(kind="python_function", passed=None, reason="docker_start_failed")
    finally:
        for reader in readers:
            if not reader.done():
                reader.cancel()
        if readers:
            await asyncio.gather(*readers, return_exceptions=True)
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()
        if proc is not None:
            try:
                cleanup = await asyncio.create_subprocess_exec("docker", "rm", "-f", name, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                try:
                    await asyncio.wait_for(cleanup.wait(), 5)
                except asyncio.TimeoutError:
                    cleanup.kill()
                    await cleanup.wait()
            except OSError:
                pass
