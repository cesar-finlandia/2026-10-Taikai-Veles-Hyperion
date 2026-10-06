"""Executor: emit → landing → validate → repair (DP-ACTIONS WU-ACT-03)."""
from __future__ import annotations

import time

import pytest

from hyperion.actions.executor import ExecutionReport
from hyperion.actions.gate import PlannedItem, UnauthorizedAction
from hyperion.actions.messages import MSG
from hyperion.dsl.render import render_profile
from hyperion.dsl.slots import extract_slots
from hyperion.events import ActionEvent, TextEvent
from hyperion.ide.actions import make_action
from hyperion.llm.fake import FakeLLM
from hyperion.testing.actions_env import make_env, new_ctx


def _nginx() -> str:
    return render_profile(extract_slots("nginx service", "native").slots)


async def test_create_emits_action_then_validates_ok():
    env = make_env()
    ctx = new_ctx(env, "create")
    items = [PlannedItem(make_action("create_file", "app.yaml", _nginx()), None)]
    report = ExecutionReport(emitted=[], landed={}, validated={})
    turn = await env.sim.run_events(env.executor.run(ctx, items, ["app.yaml"], authorization="not_required", report=report))
    assert turn.actions and turn.actions[0]["action"] == "create_file"
    assert report.validated["app.yaml"] is True
    assert "passed validation" in turn.text


async def test_landing_timeout_says_so_and_skips_validation():
    env = make_env(apply_delay_s=5, landing_timeout_s=0.2, landing_interval_s=0.05)
    ctx = new_ctx(env, "create")
    items = [PlannedItem(make_action("create_file", "app.yaml", _nginx()), None)]
    report = ExecutionReport(emitted=[], landed={}, validated={})
    turn = await env.sim.run_events(env.executor.run(ctx, items, ["app.yaml"], authorization="not_required", report=report))
    assert "has not shown" in turn.text
    assert report.validated["app.yaml"] is None


async def test_validation_error_repaired_deterministically_one_round():
    text = _nginx()
    broken = "\n".join(l for l in text.splitlines() if "owner:" not in l) + "\n"
    env = make_env()
    ctx = new_ctx(env, "create")
    items = [PlannedItem(make_action("create_file", "app.yaml", broken), None)]
    report = ExecutionReport(emitted=[], landed={}, validated={})
    turn = await env.sim.run_events(env.executor.run(ctx, items, ["app.yaml"], authorization="not_required", report=report))
    kinds = [a["action"] for a in turn.actions]
    assert kinds[0] == "create_file" and kinds[1] == "edit_file"
    assert report.repair_rounds == 1
    assert report.validated["app.yaml"] is True
    assert "owner" in env.workspace.files["app.yaml"]


async def test_repair_bounded_to_two_rounds():
    def bad_validator(path: str, content: str):
        return {"path": path, "type": "native", "valid": False, "errors": [{"message": "boom"}], "warnings": []}

    env = make_env(validator=bad_validator)
    ctx = new_ctx(env, "create")
    items = [PlannedItem(make_action("create_file", "app.yaml", _nginx()), None)]
    report = ExecutionReport(emitted=[], landed={}, validated={})
    turn = await env.sim.run_events(env.executor.run(ctx, items, ["app.yaml"], authorization="not_required", report=report))
    assert len(report.emitted) <= 3
    assert report.errors_left
    assert "still has problems" in turn.text


async def test_ide_unreachable_validates_locally():
    env = make_env()
    env.backend.down = True
    ctx = new_ctx(env, "create")
    items = [PlannedItem(make_action("create_file", "app.yaml", _nginx()), None)]
    report = ExecutionReport(emitted=[], landed={}, validated={})
    turn = await env.sim.run_events(env.executor.run(ctx, items, ["app.yaml"], authorization="not_required", report=report))
    assert turn.actions  # the action was still emitted
    assert "not reachable" in turn.text


async def test_unauthorized_item_raises_and_emits_nothing():
    env = make_env()
    ctx = new_ctx(env, "delete")
    items = [PlannedItem(make_action("delete_file", "a.yaml"), "delete")]
    report = ExecutionReport(emitted=[], landed={}, validated={})
    gen = env.executor.run(ctx, items, [], authorization="not_required", report=report)
    with pytest.raises(UnauthorizedAction):
        await gen.__anext__()
    assert report.emitted == []


async def test_delete_waits_for_absence():
    env = make_env(files={"x.yaml": _nginx()})
    env.store.note_file("u1", "x.yaml", "native", "create", _nginx())
    ctx = new_ctx(env, "delete x.yaml")
    items = [PlannedItem(make_action("delete_file", "x.yaml"), "delete")]
    report = ExecutionReport(emitted=[], landed={}, validated={})
    await env.sim.run_events(env.executor.run(ctx, items, [], authorization="confirmed", report=report))
    assert report.landed["x.yaml"] is True
    assert env.store.peek("u1").files["x.yaml"].deleted is True  # type: ignore[union-attr]


async def test_folder_action_no_landing_check():
    env = make_env()
    ctx = new_ctx(env, "mkdir")
    items = [PlannedItem(make_action("create_folder", "configs"), None)]
    report = ExecutionReport(emitted=[], landed={}, validated={})
    await env.sim.run_events(env.executor.run(ctx, items, [], authorization="not_required", report=report))
    assert report.landed["configs"] is None
    assert env.store.peek("u1").files["configs"].kind == "folder"  # type: ignore[union-attr]


async def test_turn_deadline_stops_early():
    env = make_env()
    ctx = new_ctx(env, "create")
    ctx.deadline = time.monotonic() - 1
    items = [PlannedItem(make_action("create_file", "app.yaml", _nginx()), None)]
    report = ExecutionReport(emitted=[], landed={}, validated={})
    turn = await env.sim.run_events(env.executor.run(ctx, items, ["app.yaml"], authorization="not_required", report=report))
    assert report.stopped_early is True
    assert "ran out of time" in turn.text
    assert turn.actions == []


async def test_session_updated_with_created_file():
    env = make_env()
    ctx = new_ctx(env, "create")
    items = [PlannedItem(make_action("create_file", "app.yaml", _nginx()), None)]
    report = ExecutionReport(emitted=[], landed={}, validated={})
    await env.sim.run_events(env.executor.run(ctx, items, ["app.yaml"], authorization="not_required", report=report))
    sess = env.store.peek("u1")
    assert sess is not None and sess.last_file == "app.yaml"
    assert sess.files["app.yaml"].kind == "native"


async def test_repair_edit_traced_not_required():
    text = _nginx()
    broken = "\n".join(l for l in text.splitlines() if "owner:" not in l) + "\n"
    env = make_env()
    ctx = new_ctx(env, "create")
    items = [PlannedItem(make_action("create_file", "app.yaml", broken), None)]
    report = ExecutionReport(emitted=[], landed={}, validated={})
    await env.sim.run_events(env.executor.run(ctx, items, ["app.yaml"], authorization="not_required", report=report))
    recs = [r for r in ctx.trace.records if r.stage == "action_emitted"]
    assert recs[0].detail["required"] is False and recs[0].detail["reason"] == "none"
    assert recs[1].detail["required"] is False and recs[1].detail["reason"] == "repair"


async def test_llm_repair_round_used_when_deterministic_none():
    def needs_8080(path: str, content: str):
        if "8080" in content:
            return {"path": path, "type": "native", "valid": True, "errors": [], "warnings": []}
        return {"path": path, "type": "native", "valid": False, "errors": [{"message": "port must be 8080"}], "warnings": []}

    llm = FakeLLM(json={"repair_ops": {"ops": [{"op": "set", "path": "applicationProfile.specs.network.ports[0].port", "value": 8080}]}})
    env = make_env(llm=llm, validator=needs_8080)
    ctx = new_ctx(env, "create")
    items = [PlannedItem(make_action("create_file", "app.yaml", _nginx()), None)]
    report = ExecutionReport(emitted=[], landed={}, validated={})
    await env.sim.run_events(env.executor.run(ctx, items, ["app.yaml"], authorization="not_required", report=report))
    assert report.validated["app.yaml"] is True
    assert llm.calls and llm.calls[-1].name == "repair_ops"
