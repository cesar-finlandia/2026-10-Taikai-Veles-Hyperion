"""Unit tests for hyperion.ide.paths."""
from __future__ import annotations

import pytest

from hyperion.ide.paths import PathError, basename, extension, is_bare_name, normalize_path, parent_dirs


def test_relative_ok() -> None:
    assert normalize_path("app.yaml") == "app.yaml"
    assert normalize_path("demo/app.yaml") == "demo/app.yaml"


def test_backslashes_converted() -> None:
    assert normalize_path("demo\\app.yaml") == "demo/app.yaml"


def test_quotes_stripped() -> None:
    assert normalize_path("`app.yaml`") == "app.yaml"
    assert normalize_path('"demo/app.yaml"') == "demo/app.yaml"
    assert normalize_path("'app.yaml'") == "app.yaml"


def test_dot_segments_removed() -> None:
    assert normalize_path("./app.yaml") == "app.yaml"
    assert normalize_path("demo/./app.yaml") == "demo/app.yaml"
    assert normalize_path("demo//app.yaml") == "demo/app.yaml"


def test_absolute_rejected() -> None:
    with pytest.raises(PathError):
        normalize_path("/app.yaml")


def test_drive_letter_rejected() -> None:
    with pytest.raises(PathError):
        normalize_path("C:/app.yaml")


def test_tilde_rejected() -> None:
    with pytest.raises(PathError):
        normalize_path("~/app.yaml")


def test_dotdot_rejected() -> None:
    with pytest.raises(PathError):
        normalize_path("../app.yaml")
    with pytest.raises(PathError):
        normalize_path("demo/../../app.yaml")


def test_empty_rejected() -> None:
    with pytest.raises(PathError):
        normalize_path("")
    with pytest.raises(PathError):
        normalize_path("   ")
    with pytest.raises(PathError):
        normalize_path(".")


def test_illegal_chars_rejected() -> None:
    for bad in ("a<b.yaml", "a>b.yaml", "a:b.yaml", 'a"b.yaml', "a|b.yaml", "a?b.yaml", "a*b.yaml", "a\x01b.yaml"):
        with pytest.raises(PathError):
            normalize_path(bad)


def test_too_deep_rejected() -> None:
    with pytest.raises(PathError):
        normalize_path("a/b/c/d/e/f/g/h/i.yaml")
    with pytest.raises(PathError):
        normalize_path("a" * 201)


def test_parent_dirs_and_helpers() -> None:
    assert parent_dirs("a/b/c.yaml") == ["a", "a/b"]
    assert parent_dirs("c.yaml") == []
    assert is_bare_name("c.yaml") is True
    assert is_bare_name("a/c.yaml") is False
    assert basename("a/b/c.yaml") == "c.yaml"
    assert extension("App.YAML") == ".yaml"
    assert extension("noext") == ""
