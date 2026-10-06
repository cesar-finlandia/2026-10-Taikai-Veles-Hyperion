"""Unit tests for hyperion.ide.actions."""
from __future__ import annotations

import pytest

from hyperion.ide.actions import ACTION_NAMES, ActionError, IdeAction, make_action, validate_batch


def test_create_file_requires_content() -> None:
    with pytest.raises(ActionError):
        make_action("create_file", "app.yaml")
    with pytest.raises(ActionError):
        make_action("edit_file", "app.yaml", None)
    a = make_action("create_file", "app.yaml", "x: 1")
    assert a.content == "x: 1"


def test_delete_forbids_content() -> None:
    with pytest.raises(ActionError):
        make_action("delete_file", "app.yaml", "x")
    with pytest.raises(ActionError):
        make_action("create_folder", "demo", "x")
    with pytest.raises(ActionError):
        make_action("delete_folder", "demo", "x")


def test_payload_key_order_and_shape() -> None:
    payload = make_action("create_file", "app.yaml", "x").to_payload()
    assert list(payload.keys()) == ["action", "path", "content"]
    assert payload == {"action": "create_file", "path": "app.yaml", "content": "x"}
    assert make_action("delete_file", "app.yaml").to_payload() == {"action": "delete_file", "path": "app.yaml"}
    assert make_action("create_folder", "demo").to_payload() == {"action": "create_folder", "path": "demo"}


def test_describe_texts() -> None:
    assert make_action("create_file", "app.yaml", "x").describe() == "create the file `app.yaml`"
    assert make_action("edit_file", "app.yaml", "x").describe() == "replace the contents of `app.yaml`"
    assert make_action("delete_file", "app.yaml").describe() == "delete the file `app.yaml`"
    assert make_action("create_folder", "d").describe() == "create the folder `d`"
    assert make_action("delete_folder", "d").describe() == "delete the folder `d` and everything in it"


def test_unknown_action_rejected() -> None:
    with pytest.raises(ActionError):
        make_action("write_file", "app.yaml", "x")


def test_content_too_large() -> None:
    with pytest.raises(ActionError):
        make_action("create_file", "app.yaml", "x" * 11, max_content_chars=10)


def test_path_error_becomes_action_error() -> None:
    with pytest.raises(ActionError):
        make_action("create_file", "../app.yaml", "x")
    with pytest.raises(ActionError):
        make_action("create_file", "/app.yaml", "x")


def test_edit_is_overwrite_delete_is_delete() -> None:
    assert make_action("edit_file", "a.yaml", "x").is_overwrite is True
    assert make_action("create_file", "a.yaml", "x").is_overwrite is False
    assert make_action("delete_file", "a.yaml").is_delete is True
    assert make_action("delete_folder", "d").is_delete is True
    assert make_action("create_file", "a.yaml", "x").is_delete is False
    assert ACTION_NAMES == ("create_folder", "delete_folder", "create_file", "edit_file", "delete_file")


def test_batch_too_many() -> None:
    actions = [make_action("create_file", f"f{i}.yaml", "x") for i in range(3)]
    issues = validate_batch(actions, max_actions=2)
    assert [i.code for i in issues] == ["too_many_actions"]


def test_batch_duplicates_and_conflicts() -> None:
    dup = [make_action("create_file", "a.yaml", "x"), make_action("create_file", "a.yaml", "y")]
    assert [i.code for i in validate_batch(dup, max_actions=5)] == ["duplicate_action"]
    conflict = [make_action("create_file", "a.yaml", "x"), make_action("delete_file", "a.yaml")]
    assert [i.code for i in validate_batch(conflict, max_actions=5)] == ["conflicting_actions"]
    clean = [make_action("create_file", "a.yaml", "x"), make_action("delete_file", "b.yaml")]
    assert validate_batch(clean, max_actions=5) == []


def test_batch_root_delete() -> None:
    raw = [IdeAction(action="delete_folder", path=""), IdeAction(action="delete_folder", path=".")]
    codes = [i.code for i in validate_batch(raw, max_actions=5)]
    assert codes == ["root_delete", "root_delete"]
