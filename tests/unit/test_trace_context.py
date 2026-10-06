"""Trace, context and issue tests."""
import time
import pytest
from hyperion.config import Settings
from hyperion.context import TurnContext, new_turn
from hyperion.issues import Issue, errors_only
from hyperion.trace import TraceRecorder, TraceStore


def test_trace_add_truncates_and_drops_keys():
    t = TraceRecorder("t1", "u1")
    t.add("stage1", ok=True, ms=1.0, note="x" * 500, api_key="secret", authorization="bearer", key="k")
    rec = t.records[0]
    assert rec.seq == 1
    assert rec.detail["note"] == "x" * 300
    assert "api_key" not in rec.detail
    assert "authorization" not in rec.detail
    assert "key" not in rec.detail


def test_trace_span_records_error():
    t = TraceRecorder("t1", "u1")
    with pytest.raises(ValueError):
        with t.span("work", extra_info="yes") as extra:
            extra["more"] = "1"
            raise ValueError("nope")
    rec = t.records[-1]
    assert rec.ok is False
    assert rec.detail["error"] == "ValueError"
    assert rec.detail["more"] == "1"


def test_trace_store_ring_and_filter():
    store = TraceStore(max_turns=2)
    for i in range(3):
        t = TraceRecorder(f"t{i}", "u-a" if i < 2 else "u-b")
        store.put(t, user_text=f"hello-{i}", summary="s")
    all_recent = store.recent()
    assert len(all_recent) == 2
    assert all_recent[0]["turn_id"] == "t2"  # newest first
    assert [r["turn_id"] for r in store.recent(user_id="u-a")] == ["t1"]


def test_turn_budget():
    s = Settings(turn_llm_budget=2)
    ctx = new_turn(s, "u", "hi")
    assert ctx.llm_budget == 2
    assert ctx.take_llm_call() is True
    assert ctx.take_llm_call() is True
    assert ctx.take_llm_call() is False


def test_turn_deadline():
    s = Settings(turn_deadline_s=0.05)
    ctx = new_turn(s, "u", "hi")
    assert not ctx.expired()
    time.sleep(0.07)
    assert ctx.expired()
    assert ctx.time_left() <= 0


def test_issue_format():
    i = Issue(severity="error", code="E1", message="ports[0].port must be an integer",
              path="specs.network", line=12)
    assert i.format() == "error at line 12 (specs.network): ports[0].port must be an integer"


def test_errors_only():
    issues = [Issue(severity="warning", code="W", message="w"),
              Issue(severity="error", code="E", message="e")]
    assert errors_only(issues) == [issues[1]]
