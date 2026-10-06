"""SessionStore unit tests with an injected fake clock (DP-MEMORY WU-MEM-02/03)."""
from __future__ import annotations

import hashlib
from pathlib import Path

from hyperion.config import Settings
from hyperion.ide.actions import make_action
from hyperion.llm.fake import FakeLLM
from hyperion.memory.budget import estimate_tokens
from hyperion.memory.models import PendingAction
from hyperion.memory.store import SessionStore


def make_store(**kw) -> tuple[SessionStore, list[float]]:
    now = [1000.0]
    settings = Settings(
        session_max=kw.get("session_max", 100),
        session_ttl_s=kw.get("session_ttl_s", 3600),
        pending_ttl_s=kw.get("pending_ttl_s", 300),
    )
    return SessionStore(settings, clock=lambda: now[0]), now


def make_pending(store: SessionStore, summary: str = "Delete the file `app.yaml`") -> PendingAction:
    now = store.now()
    return PendingAction(
        id="abc123",
        actions=[make_action("delete_file", "app.yaml")],
        summary=summary,
        preview="- app.yaml",
        origin_text="delete it",
        created_at=now,
        expires_at=now + 300,
        validate_paths=["app.yaml"],
    )


def test_get_creates_and_isolates_users() -> None:
    store, _ = make_store()
    a = store.get("alice")
    b = store.get("bob")
    assert a.user_id == "alice"
    assert b.user_id == "bob"
    assert a is not b
    store.record_turn("alice", user="hi", assistant="hello", intent="chat")
    assert len(store.peek("alice").turns) == 1  # type: ignore[union-attr]
    assert store.peek("bob").turns == []  # type: ignore[union-attr]
    assert store.peek("nobody") is None


def test_record_turn_caps_at_forty_and_clips() -> None:
    store, _ = make_store()
    for i in range(45):
        store.record_turn("u", user=f"q{i} " + "x" * 700, assistant="a" * 700, intent="chat")
    s = store.peek("u")
    assert s is not None
    assert len(s.turns) == 40
    assert s.turn_count == 45
    assert all(len(t.user) <= 600 and len(t.assistant) <= 600 for t in s.turns)
    assert s.turns[-1].user.startswith("q44")


def test_record_turn_extracts_name_fact() -> None:
    store, _ = make_store()
    store.record_turn("u", user="my name is Elena", assistant="hi Elena", intent="chat")
    assert store.peek("u").facts["name"] == "Elena"  # type: ignore[union-attr]
    store.record_turn("u", user="remember that the sky is blue", assistant="noted", intent="chat")
    assert store.peek("u").facts["note:1"] == "the sky is blue"  # type: ignore[union-attr]


def test_history_for_prompt_order_and_budget() -> None:
    store, _ = make_store()
    for i in range(10):
        store.record_turn("u", user=f"q{i}", assistant=f"a{i}", intent="chat")
    h = store.history_for_prompt("u", max_tokens=1100)
    assert "q2" in h and "q0" not in h  # only the last 8 turns
    assert h.index("User: q2") < h.index("Assistant: a2") < h.index("User: q3")
    assert estimate_tokens(h) <= 1100 * 1.05
    assert store.history_for_prompt("nobody") == ""


def test_history_includes_summary_pending_last_file() -> None:
    store, _ = make_store()
    store.record_turn("u", user="my name is Elena", assistant="hi", intent="chat")
    store.note_file("u", "app.yaml", "native", "create", "x: 1")
    store.set_pending("u", make_pending(store))
    session = store.peek("u")
    assert session is not None
    session.summary = "Earlier we talked about deploys."
    h = store.history_for_prompt("u")
    assert "Summary of earlier conversation: Earlier we talked about deploys." in h
    assert "Pending confirmation: Delete the file `app.yaml`" in h
    assert "Last file: app.yaml" in h
    assert "User name: Elena" in h


def test_pending_set_take_roundtrip() -> None:
    store, _ = make_store()
    assert store.peek_pending("u") is None
    pending = make_pending(store)
    store.set_pending("u", pending)
    assert store.peek_pending("u") == pending
    taken = store.take_pending("u")
    assert taken == pending
    assert store.peek_pending("u") is None


def test_pending_expires() -> None:
    store, now = make_store()
    store.set_pending("u", make_pending(store))
    now[0] += 301
    assert store.peek_pending("u") is None
    assert store.peek("u") is not None
    assert store.peek("u").pending is None  # type: ignore[union-attr]


def test_clear_pending_records_declined_max_five() -> None:
    store, _ = make_store()
    for i in range(7):
        store.set_pending("u", make_pending(store, summary=f"plan {i}"))
        store.clear_pending("u", declined_summary=f"plan {i}")
    assert store.peek("u").declined == [f"plan {i}" for i in range(2, 7)]  # type: ignore[union-attr]
    store.set_pending("u", make_pending(store))
    store.clear_pending("u")
    assert len(store.peek("u").declined) == 5  # type: ignore[union-attr]


def test_note_file_and_last_file() -> None:
    store, _ = make_store()
    store.note_file("u", "app.yaml", "native", "create", "x: 1")
    s = store.peek("u")
    assert s is not None
    assert s.last_file == "app.yaml"
    ref = s.files["app.yaml"]
    assert ref.kind == "native" and ref.last_op == "create" and not ref.deleted
    assert ref.sha == hashlib.sha256(b"x: 1").hexdigest()[:12]
    store.note_file("u", "docs", "folder", "create", None)
    assert store.peek("u").files["docs"].sha == ""  # type: ignore[union-attr]


def test_forget_file_moves_last_file() -> None:
    store, _ = make_store()
    store.note_file("u", "a.yaml", "native", "create", "a")
    store.note_file("u", "b.yaml", "native", "create", "b")
    assert store.peek("u").last_file == "b.yaml"  # type: ignore[union-attr]
    store.forget_file("u", "b.yaml")
    s = store.peek("u")
    assert s is not None
    assert s.last_file == "a.yaml"
    assert s.files["b.yaml"].deleted
    store.forget_file("u", "a.yaml")
    assert store.peek("u").last_file is None  # type: ignore[union-attr]


def test_resolve_reference_uses_last_file() -> None:
    store, _ = make_store()
    assert store.resolve_reference("u", "delete it") is None
    store.note_file("u", "app.yaml", "native", "create", "x")
    assert store.resolve_reference("u", "delete it") == "app.yaml"
    assert store.resolve_reference("u", "what is it?") is None


def test_lru_eviction() -> None:
    store, _ = make_store(session_max=2)
    store.get("a")
    store.get("b")
    store.get("c")
    assert store.peek("a") is None
    assert store.peek("b") is not None
    store.get("b")
    store.get("d")
    assert store.peek("c") is None
    assert store.peek("b") is not None
    assert store.peek("d") is not None


def test_ttl_expiry_makes_fresh_session() -> None:
    store, now = make_store(session_ttl_s=60)
    store.record_turn("u", user="my name is Elena", assistant="hi", intent="chat")
    now[0] += 61
    assert store.peek("u") is None
    fresh = store.get("u")
    assert fresh.turns == [] and fresh.facts == {} and fresh.turn_count == 0


async def test_compress_with_fake_llm() -> None:
    store, _ = make_store()
    for i in range(6):
        store.record_turn("u", user=f"question {i} " + "x" * 600, assistant="y" * 600, intent="chat")
    llm = FakeLLM(chat={"memory_summary": "Folded summary of six turns."})
    assert await store.compress_if_needed("u", llm) is True
    s = store.peek("u")
    assert s is not None
    assert s.summary == "Folded summary of six turns."
    assert len(s.turns) == 3


async def test_compress_fallback_when_llm_down() -> None:
    store, _ = make_store()
    for i in range(6):
        store.record_turn("u", user=f"question {i} " + "x" * 600, assistant="y" * 600, intent="chat")
    llm = FakeLLM(down=True)
    assert await store.compress_if_needed("u", llm) is True
    s = store.peek("u")
    assert s is not None
    assert "User asked:" in s.summary
    assert len(s.turns) == 3


async def test_compress_noop_when_small() -> None:
    store, _ = make_store()
    store.record_turn("u", user="hi", assistant="hello", intent="chat")
    llm = FakeLLM(chat={"memory_summary": "unused"})
    assert await store.compress_if_needed("u", llm) is False
    assert store.peek("u").summary == ""  # type: ignore[union-attr]
    assert await store.compress_if_needed("nobody", llm) is False


def test_snapshot_restore_roundtrip_with_pending() -> None:
    store, _ = make_store()
    store.record_turn("u", user="my name is Elena", assistant="hi", intent="chat")
    store.note_file("u", "app.yaml", "native", "create", "x: 1")
    store.set_pending("u", make_pending(store))
    data = store.snapshot()
    assert data["version"] == 1
    other, _ = make_store()
    other.restore(data)
    s = other.peek("u")
    assert s is not None
    assert s.facts == {"name": "Elena"}
    assert s.last_file == "app.yaml"
    assert s.turn_count == 1
    pending = other.take_pending("u")
    assert pending is not None
    assert pending.actions[0].action == "delete_file"
    assert pending.summary.startswith("Delete the file")


def test_save_and_load_file(tmp_path: Path) -> None:
    store, _ = make_store()
    store.record_turn("u", user="remember that the sky is blue", assistant="noted", intent="chat")
    path = tmp_path / "mem" / "memory.json"
    store.save(path)
    assert path.exists()
    other, _ = make_store()
    assert other.load(path) is True
    assert other.peek("u").facts["note:1"] == "the sky is blue"  # type: ignore[union-attr]


def test_load_corrupt_returns_false(tmp_path: Path) -> None:
    store, _ = make_store()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert store.load(bad) is False
    assert store.load(tmp_path / "missing.json") is False


def test_stats() -> None:
    store, _ = make_store()
    store.record_turn("a", user="hi", assistant="yo", intent="chat")
    store.record_turn("a", user="again", assistant="yo", intent="chat")
    store.record_turn("b", user="hello", assistant="yo", intent="chat")
    store.set_pending("a", make_pending(store))
    assert store.stats() == {"sessions": 2, "pending": 1, "turns": 3}


def test_pending_holds_real_ide_actions() -> None:
    store, _ = make_store()
    actions = [
        make_action("delete_file", "app.yaml"),
        make_action("edit_file", "app.yaml", content="x: 2"),
    ]
    now = store.now()
    pending = PendingAction(
        id="1a2b3c",
        actions=actions,
        summary="Delete and rewrite `app.yaml`",
        preview="-app.yaml\n+app.yaml",
        origin_text="redo it",
        created_at=now,
        expires_at=now + 300,
        validate_paths=["app.yaml"],
    )
    store.set_pending("u", pending)
    other, _ = make_store()
    other.restore(store.snapshot())
    taken = other.take_pending("u")
    assert taken is not None
    assert taken.actions[0].action == "delete_file"
    assert [a.to_payload() for a in taken.actions] == [a.to_payload() for a in actions]
