"""Previews and planner (DP-ACTIONS WU-ACT-02)."""
from __future__ import annotations

from hyperion.actions.intents import ActIntent
from hyperion.actions.messages import MSG
from hyperion.actions.preview import diff_preview, format_confirmation, head_preview, sha12
from hyperion.dsl.check import check_profile
from hyperion.dsl.render import render_profile
from hyperion.dsl.slots import extract_slots
from hyperion.llm.fake import FakeLLM
from hyperion.memory.models import PendingAction
from hyperion.testing.actions_env import make_env, new_ctx
from hyperion.ide.actions import make_action


def _nginx_profile_text() -> str:
    return render_profile(extract_slots("nginx service", "native").slots)


def test_sha12_matches_store_formula():
    env = make_env()
    env.store.note_file("u1", "app.yaml", "native", "create", "x")
    ref = env.store.peek("u1").files["app.yaml"]  # type: ignore[union-attr]
    assert sha12("x") == ref.sha


def test_diff_preview_small():
    out = diff_preview("a\nb\n", "a\nc\n")
    assert out.startswith("```diff")
    assert any(l.startswith("-") and "b" in l for l in out.splitlines())
    assert any(l.startswith("+") and "c" in l for l in out.splitlines())


def test_diff_preview_truncates():
    old = "\n".join(f"line {i}" for i in range(60))
    new = "\n".join(f"changed {i}" for i in range(60))
    out = diff_preview(old, new)
    assert out.startswith("```diff")
    extra = [l for l in out.splitlines() if l.startswith("… ")]
    assert extra and extra[0].endswith("more changed lines")


def test_format_confirmation_contains_diff_and_instructions():
    pending = PendingAction(
        id="abc123", actions=[make_action("delete_file", "app.yaml")],
        summary="delete the file `app.yaml`", preview="```diff\n- a\n+ b\n```",
        origin_text="delete app.yaml", created_at=0.0, expires_at=900.0, validate_paths=[],
    )
    out = format_confirmation(pending, minutes=15)
    assert "I need your OK" in out
    assert "```diff" in out
    assert 'Reply "yes"' in out
    assert "15 minutes" in out


async def test_create_nginx_native_valid():
    env = make_env()
    ctx = new_ctx(env, "Create a deployment YAML for a service using the nginx Docker image")
    plan = await env.planner.plan(ctx, ActIntent(verb="create", target=None, target_source="none", is_profile=True))
    assert plan.ok
    assert len(plan.items) == 1
    assert plan.items[0].action.action == "create_file"
    assert plan.items[0].action.path == "app.yaml"
    assert plan.items[0].reason is None
    assert not check_profile(plan.items[0].action.content or "")
    assert "nginx" in (plan.items[0].action.content or "")
    assert env.llm.calls == []


async def test_create_named_path_with_parent_emits_folder_first():
    env = make_env()
    ctx = new_ctx(env, "create configs/web.yaml for nginx")
    plan = await env.planner.plan(ctx, ActIntent(verb="create", target="configs/web.yaml", target_source="explicit", is_profile=True))
    assert plan.ok
    assert [i.action.action for i in plan.items] == ["create_folder", "create_file"]
    assert plan.items[1].action.path == "configs/web.yaml"


async def test_create_existing_uses_edit_file_with_overwrite_reason():
    from hyperion.dsl.slots import extract_slots as _ex

    old = render_profile(_ex("redis service", "native").slots)
    env = make_env(files={"web.yaml": old})
    ctx = new_ctx(env, "create web.yaml for nginx")
    plan = await env.planner.plan(ctx, ActIntent(verb="create", target="web.yaml", target_source="explicit", is_profile=True))
    assert plan.ok
    assert plan.items[-1].action.action == "edit_file"
    assert plan.items[-1].reason == "overwrite"
    assert plan.preview.startswith("```diff")


async def test_create_unnamed_when_app_yaml_exists_picks_unique_name():
    env = make_env(files={"app.yaml": _nginx_profile_text()})
    ctx = new_ctx(env, "create a deployment yaml for nginx")
    plan = await env.planner.plan(ctx, ActIntent(verb="create", target=None, target_source="none", is_profile=True))
    assert plan.ok
    path = plan.items[-1].action.path
    assert path != "app.yaml"
    assert path.endswith(".yaml")
    assert plan.items[-1].action.action == "create_file"
    assert plan.items[-1].reason is None
    assert any("app.yaml" in n for n in plan.notes)


async def test_create_device_profile():
    env = make_env()
    ctx = new_ctx(env, "create a device app profile for grafana on my raspberry pi")
    plan = await env.planner.plan(ctx, ActIntent(verb="create", target=None, target_source="none", is_profile=True))
    assert plan.ok
    assert "apiVersion: hyper.ai/v1" in (plan.items[-1].action.content or "")


async def test_create_llm_slots_used_when_image_missing():
    llm = FakeLLM(json={"slots": {"image": "redis", "port": 6379}})
    env = make_env(llm=llm)
    ctx = new_ctx(env, "create a deployment yaml for my cache service")
    plan = await env.planner.plan(ctx, ActIntent(verb="create", target=None, target_source="none", is_profile=True))
    assert plan.ok
    content = plan.items[-1].action.content or ""
    assert "redis" in content and "6379" in content
    assert len(llm.calls) == 1 and llm.calls[0].name == "slots"


async def test_create_llm_down_falls_back_to_rules():
    env = make_env()
    ctx = new_ctx(env, "create a deployment yaml for my cache service")
    plan = await env.planner.plan(ctx, ActIntent(verb="create", target=None, target_source="none", is_profile=True))
    assert plan.ok
    assert MSG.DEGRADED_SLOTS in plan.notes


async def test_create_plain_file_with_content():
    env = make_env()
    ctx = new_ctx(env, "make a file called notes.txt with content hello world")
    plan = await env.planner.plan(ctx, ActIntent(verb="create", target="notes.txt", target_source="explicit", is_profile=False))
    assert plan.ok
    assert plan.items[-1].action.path == "notes.txt"
    assert (plan.items[-1].action.content or "") == "hello world"
    assert plan.validate_paths == []


async def test_create_plain_without_name_clarifies():
    env = make_env()
    ctx = new_ctx(env, "create a file")
    plan = await env.planner.plan(ctx, ActIntent(verb="create", target=None, target_source="none", is_profile=False))
    assert not plan.ok and plan.clarify and plan.message == MSG.NEED_NAME


async def test_edit_port_deterministic_ops_diff():
    env = make_env(files={"app.yaml": _nginx_profile_text()})
    ctx = new_ctx(env, "change the port to 8080")
    plan = await env.planner.plan(ctx, ActIntent(verb="edit", target="app.yaml", target_source="explicit", is_profile=False))
    assert plan.ok
    assert len(plan.items) == 1 and plan.items[0].action.action == "edit_file"
    assert plan.items[0].reason == "edit"
    assert "8080" in (plan.items[0].action.content or "")
    assert "+" in plan.preview and "8080" in plan.preview
    assert env.llm.calls == []


async def test_edit_llm_ops_when_no_deterministic():
    llm = FakeLLM(json={"patch_ops": {"ops": [{"op": "set", "path": "applicationProfile.metadata.description", "value": "edge web server"}]}})
    env = make_env(files={"app.yaml": _nginx_profile_text()}, llm=llm)
    ctx = new_ctx(env, "give it a better description")
    plan = await env.planner.plan(ctx, ActIntent(verb="edit", target="app.yaml", target_source="explicit", is_profile=False))
    assert plan.ok
    assert "edge web server" in (plan.items[0].action.content or "")
    assert any(c.name == "patch_ops" for c in llm.calls)


async def test_edit_missing_file_not_found_message():
    env = make_env()
    ctx = new_ctx(env, "change the port to 80 in nope.yaml")
    plan = await env.planner.plan(ctx, ActIntent(verb="edit", target="nope.yaml", target_source="explicit", is_profile=False))
    assert not plan.ok
    assert plan.message.startswith("I cannot find `nope.yaml` ")


async def test_edit_ambiguous_lists_matches():
    env = make_env(files={"a/app.yaml": _nginx_profile_text(), "b/app.yaml": _nginx_profile_text()})
    ctx = new_ctx(env, "set the port to 81 in app.yaml")
    plan = await env.planner.plan(ctx, ActIntent(verb="edit", target="app.yaml", target_source="explicit", is_profile=False))
    assert not plan.ok and plan.clarify
    assert "a/app.yaml" in plan.message and "b/app.yaml" in plan.message


async def test_edit_no_change_message():
    import re

    text = _nginx_profile_text()
    m = re.search(r"port:\s*(\d+)", text)
    assert m
    port = m.group(1)
    env = make_env(files={"app.yaml": text})
    ctx = new_ctx(env, f"change the port to {port}")
    plan = await env.planner.plan(ctx, ActIntent(verb="edit", target="app.yaml", target_source="explicit", is_profile=False))
    assert not plan.ok
    assert plan.message == MSG.NO_CHANGE.format(path="app.yaml")


async def test_fix_uses_deterministic_repair():
    text = _nginx_profile_text()
    broken = "\n".join(l for l in text.splitlines() if "owner:" not in l) + "\n"
    env = make_env(files={"app.yaml": broken})
    ctx = new_ctx(env, "fix app.yaml")
    plan = await env.planner.plan(ctx, ActIntent(verb="fix", target="app.yaml", target_source="explicit", is_profile=True))
    assert plan.ok
    assert "owner" in (plan.items[0].action.content or "")
    assert plan.items[0].reason == "edit"
    assert env.llm.calls == []


async def test_delete_confirmation_reason_and_resolved_path():
    env = make_env(files={"conf/app.yaml": _nginx_profile_text()})
    ctx = new_ctx(env, "delete app.yaml")
    plan = await env.planner.plan(ctx, ActIntent(verb="delete", target="app.yaml", target_source="explicit", is_profile=False))
    assert plan.ok
    assert plan.items[0].action.action == "delete_file"
    assert plan.items[0].action.path == "conf/app.yaml"
    assert plan.items[0].reason == "delete"
    assert "cannot be undone" in plan.preview


async def test_delete_missing_file_message():
    env = make_env()
    ctx = new_ctx(env, "delete nope.yaml")
    plan = await env.planner.plan(ctx, ActIntent(verb="delete", target="nope.yaml", target_source="explicit", is_profile=False))
    assert not plan.ok


async def test_delete_folder_reason():
    env = make_env()
    ctx = new_ctx(env, "delete the folder old")
    plan = await env.planner.plan(ctx, ActIntent(verb="delete_folder", target="old", target_source="explicit", is_profile=False))
    assert plan.ok
    assert plan.items[0].action.action == "delete_folder"
    assert plan.items[0].reason == "delete"
    assert plan.preview == MSG.FOLDER_UNKNOWN_CONTENT


async def test_too_large_file_refused():
    env = make_env(files={"big.yaml": "x" * 7000})
    ctx = new_ctx(env, "change the port to 80 in big.yaml")
    plan = await env.planner.plan(ctx, ActIntent(verb="edit", target="big.yaml", target_source="explicit", is_profile=False))
    assert not plan.ok
    assert plan.message.startswith("`big.yaml` is too large")


async def test_too_many_parent_folders_refused():
    env = make_env()
    ctx = new_ctx(env, "create a/b/c/d/e/f/x.yaml for nginx")
    plan = await env.planner.plan(ctx, ActIntent(verb="create", target="a/b/c/d/e/f/x.yaml", target_source="explicit", is_profile=True))
    assert not plan.ok
    assert plan.message.startswith("I cannot do that safely")
