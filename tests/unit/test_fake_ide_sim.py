"""Unit tests for FakeWorkspace, IdeSimulator and parse_sse_lines."""
from __future__ import annotations

from hyperion.events import ActionEvent, TextEvent
from hyperion.testing.fake_ide import FakeWorkspace
from hyperion.testing.ide_simulator import IdeSimulator, parse_sse_lines


def test_workspace_create_parents() -> None:
    ws = FakeWorkspace()
    assert ws.apply({"action": "create_file", "path": "x/y.yaml", "content": "hi"}) is True
    assert ws.snapshot() == {"x/y.yaml": "hi"}
    assert "x" in ws.folders
    kind, matches = ws.resolve("y.yaml")
    assert (kind, matches) == ("one", ["x/y.yaml"])


def test_workspace_no_parents_mode_rejects() -> None:
    ws = FakeWorkspace(create_parents=False)
    assert ws.apply({"action": "create_file", "path": "x/y.yaml", "content": "hi"}) is False
    assert ws.snapshot() == {}
    assert ws.apply({"action": "create_folder", "path": "x"}) is True
    assert ws.apply({"action": "create_file", "path": "x/y.yaml", "content": "hi"}) is True


def test_workspace_edit_first_match_and_delete_folder_prefix() -> None:
    ws = FakeWorkspace({"a/app.yaml": "1", "b/app.yaml": "2", "a/other.txt": "o"})
    assert ws.apply({"action": "edit_file", "path": "app.yaml", "content": "new"}) is True
    assert ws.files["a/app.yaml"] == "new"
    assert ws.files["b/app.yaml"] == "2"
    assert ws.apply({"action": "delete_file", "path": "b/app.yaml"}) is True
    assert "b/app.yaml" not in ws.files
    assert ws.apply({"action": "delete_folder", "path": "a"}) is True
    assert ws.snapshot() == {}
    assert ws.apply({"action": "delete_folder", "path": "nope"}) is False


async def test_simulator_applies_actions_after_delay() -> None:
    ws = FakeWorkspace()
    sim = IdeSimulator(ws, apply_delay_s=0.1)

    async def evs():  # type: ignore[no-untyped-def]
        yield TextEvent(text="hello ")
        yield TextEvent(text="world")
        yield ActionEvent(payload={"action": "create_file", "path": "app.yaml", "content": "x"})

    turn = await sim.run_events(evs())
    assert turn.text == "hello world"
    assert turn.actions == [{"action": "create_file", "path": "app.yaml", "content": "x"}]
    assert turn.events[0] == ("text", "hello ")
    assert turn.events[2][0] == "action"
    assert turn.done is True
    assert turn.duration_s >= 0.1
    assert ws.snapshot() == {"app.yaml": "x"}


def test_parse_sse_lines() -> None:
    raw = (
        'data: {"response": "hi"}\n\n'
        'data: {"action": "create_file", "path": "a.yaml", "content": "x"}\n\n'
        "data: [DONE]\n\n"
    )
    text, actions, done = parse_sse_lines(raw)
    assert text == "hi"
    assert actions == [{"action": "create_file", "path": "a.yaml", "content": "x"}]
    assert done is True
    text, actions, done = parse_sse_lines('data: {"response": "x"}\n\n')
    assert (text, actions, done) == ("x", [], False)
