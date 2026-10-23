"""Bounded workspace keys and backward-compatible session lookup."""

import json

from code_agent.storage.models import SessionMeta
from code_agent.storage.session_store import SessionStore, _encode_cwd


async def test_long_workspace_path_can_save_and_resume(tmp_path):
    cwd = str(tmp_path / ("deep-project-" * 40))
    store = SessionStore(tmp_path / "sessions")
    await store.init_session(SessionMeta(session_id="long", cwd=cwd, model_id="test"))
    await store.append_message("long", cwd, {"role": "user", "content": "hello"})
    meta, messages = await store.load_session("long", cwd)
    assert meta.session_id == "long"
    assert messages == [{"role": "user", "content": "hello"}]
    assert len(store._session_dir(cwd, "long").parent.name) == 34
    assert store.find_latest(cwd).session_id == "long"


async def test_legacy_sessions_remain_readable_and_appendable(tmp_path):
    store = SessionStore(tmp_path)
    cwd = "/old-project"
    old = tmp_path / _encode_cwd(cwd) / "legacy"
    old.mkdir(parents=True)
    meta = SessionMeta(session_id="legacy", cwd=cwd, model_id="old")
    (old / "summary.json").write_text(meta.model_dump_json(), encoding="utf-8")
    (old / "chat_history.jsonl").write_text(json.dumps({"role": "user", "content": "old"}) + "\n", encoding="utf-8")
    await store.append_message("legacy", cwd, {"role": "assistant", "content": "new"})
    _, messages = await store.load_session("legacy", cwd)
    assert [m["content"] for m in messages] == ["old", "new"]
    assert store.find_latest(cwd).session_id == "legacy"
    await store.init_session(SessionMeta(session_id="current", cwd=cwd, model_id="new"))
    assert store._session_dir(cwd, "current").parent != old.parent
    assert store.find_latest(cwd).session_id == "current"
