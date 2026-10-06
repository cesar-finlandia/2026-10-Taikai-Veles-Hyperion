"""End-to-end act pipeline through the IDE simulator (DP-ACTIONS WU-ACT-04)."""
from __future__ import annotations

from hyperion.actions.intents import detect_act_intent, resolve_intent
from hyperion.actions.messages import MSG
from hyperion.dsl.render import render_profile
from hyperion.dsl.slots import extract_slots
from hyperion.testing.actions_env import make_env, new_ctx


def _nginx() -> str:
    return render_profile(extract_slots("nginx service", "native").slots)


def _intent(env, text: str):
    raw = detect_act_intent(text)
    assert raw is not None, text
    resolved = resolve_intent(raw, env.store.get("u1"), text)
    assert resolved is not None, text
    return resolved


async def test_nginx_request_end_to_end():
    env = make_env()
    text = "Create a deployment YAML for a service using the nginx Docker image"
    turn = await env.sim.run_events(env.flow.handle_request(new_ctx(env, text), _intent(env, text)))
    assert len([a for a in turn.actions if a["action"] == "create_file"]) == 1
    assert "app.yaml" in env.workspace.files and "nginx" in env.workspace.files["app.yaml"]
    assert "passed validation" in turn.text
    assert turn.done is True


async def test_second_identical_request_creates_unique_file():
    env = make_env()
    text = "Create a deployment YAML for a service using the nginx Docker image"
    t1 = await env.sim.run_events(env.flow.handle_request(new_ctx(env, text), _intent(env, text)))
    t2 = await env.sim.run_events(env.flow.handle_request(new_ctx(env, text), _intent(env, text)))
    assert len(env.workspace.files) == 2
    paths = sorted(env.workspace.files.keys())
    assert "app.yaml" in paths and paths[1] != "app.yaml"
    assert all(a["action"] == "create_file" for a in t2.actions)
    assert "I need your OK" not in t2.text


async def test_delete_requires_confirmation_no_action_before_yes():
    env = make_env(files={"app.yaml": _nginx()})
    turn = await env.sim.run_events(env.flow.handle_request(new_ctx(env, "delete app.yaml"), _intent(env, "delete app.yaml")))
    assert turn.actions == []
    assert "I need your OK" in turn.text
    assert "app.yaml" in env.workspace.files
    assert env.store.peek_pending("u1") is not None


async def test_yes_executes_delete_and_forgets_file():
    env = make_env(files={"app.yaml": _nginx()})
    await env.sim.run_events(env.flow.handle_request(new_ctx(env, "delete app.yaml"), _intent(env, "delete app.yaml")))
    turn = await env.sim.run_events(env.flow.handle_confirmation(new_ctx(env, "yes"), "yes"))
    assert len(turn.actions) == 1 and turn.actions[0]["action"] == "delete_file"
    assert "app.yaml" not in env.workspace.files
    assert env.store.peek("u1").files["app.yaml"].deleted is True  # type: ignore[union-attr]
    assert env.store.peek_pending("u1") is None


async def test_no_cancels_records_declined():
    env = make_env(files={"app.yaml": _nginx()})
    await env.sim.run_events(env.flow.handle_request(new_ctx(env, "delete app.yaml"), _intent(env, "delete app.yaml")))
    turn = await env.sim.run_events(env.flow.handle_confirmation(new_ctx(env, "no"), "no"))
    assert turn.actions == []
    declined = env.store.peek("u1").declined  # type: ignore[union-attr]
    assert len(declined) == 1 and "app.yaml" in declined[0]


async def test_edit_confirmation_shows_diff_then_applies():
    content = _nginx()
    env = make_env(files={"app.yaml": content})
    env.store.note_file("u1", "app.yaml", "native", "create", content)
    turn = await env.sim.run_events(env.flow.handle_request(new_ctx(env, "change the port to 8080"), _intent(env, "change the port to 8080")))
    assert turn.actions == []
    assert "```diff" in turn.text
    turn2 = await env.sim.run_events(env.flow.handle_confirmation(new_ctx(env, "yes"), "yes"))
    assert len(turn2.actions) == 1 and turn2.actions[0]["action"] == "edit_file"
    assert "8080" in env.workspace.files["app.yaml"]
    assert "passed validation" in turn2.text


async def test_overwrite_named_existing_asks():
    env = make_env()
    text = "create web.yaml for nginx"
    await env.sim.run_events(env.flow.handle_request(new_ctx(env, text), _intent(env, text)))
    assert "web.yaml" in env.workspace.files
    turn2 = await env.sim.run_events(env.flow.handle_request(new_ctx(env, text), _intent(env, text)))
    assert turn2.actions == []
    assert "I need your OK" in turn2.text
    turn3 = await env.sim.run_events(env.flow.handle_confirmation(new_ctx(env, "yes"), "yes"))
    assert turn3.actions and turn3.actions[0]["action"] == "edit_file"


async def test_stale_file_cancels_confirmation():
    content = _nginx()
    env = make_env(files={"app.yaml": content})
    env.store.note_file("u1", "app.yaml", "native", "create", content)
    await env.sim.run_events(env.flow.handle_request(new_ctx(env, "change the port to 8080"), _intent(env, "change the port to 8080")))
    env.workspace.files["app.yaml"] = env.workspace.files["app.yaml"].replace("owner", "owner-changed")
    turn = await env.sim.run_events(env.flow.handle_confirmation(new_ctx(env, "yes"), "yes"))
    assert turn.actions == []
    assert "changed after I prepared" in turn.text


async def test_pending_expired_gives_no_pending_reply():
    env = make_env(files={"app.yaml": _nginx()})
    await env.sim.run_events(env.flow.handle_request(new_ctx(env, "delete app.yaml"), _intent(env, "delete app.yaml")))
    pending = env.store.peek_pending("u1")
    assert pending is not None
    pending.expires_at = env.store.now() - 1
    turn = await env.sim.run_events(env.flow.handle_confirmation(new_ctx(env, "yes"), "yes"))
    assert turn.text == MSG.NO_PENDING


async def test_other_message_keeps_pending_and_reminder_available():
    env = make_env(files={"app.yaml": _nginx()})
    await env.sim.run_events(env.flow.handle_request(new_ctx(env, "delete app.yaml"), _intent(env, "delete app.yaml")))
    assert "app.yaml" in env.flow.pending_reminder("u1")
    text = "Create a deployment YAML for a service using the nginx Docker image"
    turn = await env.sim.run_events(env.flow.handle_request(new_ctx(env, text), _intent(env, text)))
    assert turn.text.startswith("I dropped the earlier pending request")


async def test_validate_reports_errors_then_fix_flow():
    broken = "\n".join(l for l in _nginx().splitlines() if "owner:" not in l) + "\n"
    env = make_env(files={"app.yaml": broken})
    turn = await env.sim.run_events(env.flow.handle_validate(new_ctx(env, "validate app.yaml"), _intent(env, "validate app.yaml")))
    assert "has 1 problem" in turn.text and "fix it" in turn.text
    assert turn.actions == []
    turn2 = await env.sim.run_events(env.flow.handle_request(new_ctx(env, "fix it"), _intent(env, "fix it")))
    assert turn2.actions == []
    assert "```diff" in turn2.text and "owner" in turn2.text
    turn3 = await env.sim.run_events(env.flow.handle_confirmation(new_ctx(env, "yes"), "yes"))
    assert len(turn3.actions) == 1 and turn3.actions[0]["action"] == "edit_file"
    assert "passed validation" in turn3.text


async def test_read_file_shows_content_clipped():
    env = make_env(files={"big.txt": "y\n" * 1500})
    # make it 3000+ chars
    env.workspace.files["big.txt"] = "z" * 3000
    turn = await env.sim.run_events(env.flow.handle_read(new_ctx(env, "show me big.txt"), _intent(env, "show me big.txt")))
    assert "showing the first 1,500 characters" in turn.text


async def test_explain_file_without_llm_uses_summary():
    env = make_env(files={"app.yaml": _nginx()})
    turn = await env.sim.run_events(env.flow.handle_read(new_ctx(env, "explain app.yaml"), _intent(env, "explain app.yaml")))
    assert turn.text.startswith("Native app ")
    assert "language model is not available" in turn.text


async def test_unsafe_path_refused_no_action():
    env = make_env()
    raw = detect_act_intent("delete ../secrets.yaml")
    assert raw is not None and raw.path_error
    turn = await env.sim.run_events(env.flow.handle_request(new_ctx(env, "delete ../secrets.yaml"), raw))
    assert turn.text == raw.path_error
    assert turn.actions == []


async def test_hitl_destructive_mode_edits_without_confirmation_deletes_with():
    content = _nginx()
    env = make_env(files={"app.yaml": content}, hitl_mode="destructive")
    env.store.note_file("u1", "app.yaml", "native", "create", content)
    turn = await env.sim.run_events(env.flow.handle_request(new_ctx(env, "change the port to 8080"), _intent(env, "change the port to 8080")))
    assert len(turn.actions) == 1 and turn.actions[0]["action"] == "edit_file"
    turn2 = await env.sim.run_events(env.flow.handle_request(new_ctx(env, "delete app.yaml"), _intent(env, "delete app.yaml")))
    assert turn2.actions == []
    assert "I need your OK" in turn2.text


async def test_feature_hitl_off_skips_confirmation_and_trace_shows_it():
    env = make_env(files={"app.yaml": _nginx()}, feature_hitl=False)
    ctx = new_ctx(env, "delete app.yaml")
    turn = await env.sim.run_events(env.flow.handle_request(ctx, _intent(env, "delete app.yaml")))
    assert turn.actions and turn.actions[0]["action"] == "delete_file"
    recs = [r for r in ctx.trace.records if r.stage == "action_emitted"]
    assert recs and recs[0].detail["required"] is True and recs[0].detail["confirmed"] is False
