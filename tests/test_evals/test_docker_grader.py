import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from code_agent.evals.docker_grader import bounded_read, docker_grade, image_available
from code_agent.evals.schema import load_tasks


@pytest.mark.parametrize("code,output,expected", [(0,b"[5,1,0]\n",True),(0,b"[0,0,0]\n",False),(0,b"extra output\n[5,1,0]\n",False),(1,b"",False),(125,b"",None)])
async def test_container_contract_and_independent_expected_answers(code, output, expected, tmp_path, monkeypatch):
    task, _ = load_tasks(Path(__file__).resolve().parents[2] / "evals/datasets/coding-v1.jsonl")
    calls = []
    proc = Mock()
    proc.returncode = code
    proc.stdin = Mock()
    proc.stdin.drain = AsyncMock()
    proc.stdout = asyncio.StreamReader()
    proc.stdout.feed_data(output)
    proc.stdout.feed_eof()
    proc.stderr = asyncio.StreamReader()
    proc.stderr.feed_eof()
    proc.wait = AsyncMock(return_value=code)
    cleanup = Mock(returncode=0)
    cleanup.wait = AsyncMock(return_value=0)
    async def start(*args, **kwargs):
        calls.append(args)
        return cleanup if args[1] in {"rm", "image"} else proc
    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)
    grade = await docker_grade(task[0].checks[0], tmp_path)
    assert grade.passed is expected
    assert calls[0][1:3] == ("image", "inspect")
    assert "--network=none" in calls[1]
    assert "--read-only" in calls[1]
    assert "--pull=never" in calls[1]
    payload = json.loads(proc.stdin.write.call_args[0][0])
    assert "expected" not in payload
    assert calls[-1][1:3] == ("rm","-f")


async def test_missing_docker_is_infrastructure_error(tmp_path, monkeypatch):
    task, _ = load_tasks(Path(__file__).resolve().parents[2] / "evals/datasets/coding-v1.jsonl")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(side_effect=FileNotFoundError))
    assert (await docker_grade(task[0].checks[0], tmp_path)).passed is None


async def test_candidate_output_is_bounded():
    stream = asyncio.StreamReader()
    stream.feed_data(b"x" * 70000)
    stream.feed_eof()
    with pytest.raises(ValueError):
        await bounded_read(stream)


async def test_unavailable_engine_exit_one_is_not_candidate_failure(tmp_path, monkeypatch):
    task, _ = load_tasks(Path(__file__).resolve().parents[2] / "evals/datasets/coding-v1.jsonl")
    proc = Mock(returncode=1)
    proc.wait = AsyncMock(return_value=1)
    proc.stdin = Mock()
    proc.stdin.drain = AsyncMock()
    proc.stdout = asyncio.StreamReader()
    proc.stdout.feed_eof()
    proc.stderr = asyncio.StreamReader()
    proc.stderr.feed_data(b"error during connect: Docker engine unavailable")
    proc.stderr.feed_eof()
    start = AsyncMock(return_value=proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)
    result = await docker_grade(task[0].checks[0], tmp_path)
    assert result.passed is None
    assert result.reason == "docker_unavailable_or_image_missing"
    assert not any(call.args[1] == "run" for call in start.call_args_list)


async def test_infrastructure_probe_timeout_kills_and_reaps_process(monkeypatch):
    proc = Mock(returncode=None)
    proc.wait = AsyncMock(side_effect=[asyncio.TimeoutError(), -9])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    assert await image_available() is False
    proc.kill.assert_called_once()
    assert proc.wait.await_count == 2
