"""Unit tests for hyperion.ide.gateway (against FakeIdeBackend)."""
from __future__ import annotations

import asyncio
from typing import Any

from hyperion.events import ActionEvent, TextEvent
from hyperion.ide.gateway import IdeGateway, normalize_report
from hyperion.testing.fake_ide import FakeIdeBackend, FakeWorkspace
from hyperion.testing.ide_simulator import IdeSimulator


def _gateway(ide_settings: Any, backend: FakeIdeBackend) -> IdeGateway:
    return IdeGateway(ide_settings, transport=backend.transport())


async def test_read_ok_full_path(ide_settings: Any) -> None:
    backend = FakeIdeBackend(FakeWorkspace({"demo/app.yaml": "x: 1"}))
    gw = _gateway(ide_settings, backend)
    try:
        result = await gw.read("demo/app.yaml")
        assert result.status == "ok"
        assert result.ok is True
        assert result.path == "demo/app.yaml"
        assert result.content == "x: 1"
    finally:
        await gw.aclose()


async def test_read_bare_name_returns_actual_path(ide_settings: Any) -> None:
    backend = FakeIdeBackend(FakeWorkspace({"demo/app.yaml": "hello"}))
    gw = _gateway(ide_settings, backend)
    try:
        result = await gw.read("app.yaml")
        assert result.status == "ok"
        assert result.path == "demo/app.yaml"
        assert result.content == "hello"
    finally:
        await gw.aclose()


async def test_read_not_found(ide_settings: Any) -> None:
    backend = FakeIdeBackend(FakeWorkspace({}))
    gw = _gateway(ide_settings, backend)
    try:
        result = await gw.read("missing.yaml")
        assert result.status == "not_found"
        assert result.ok is False
        assert result.message == "`missing.yaml` is not in the workspace."
    finally:
        await gw.aclose()


async def test_read_ambiguous_lists_matches(ide_settings: Any) -> None:
    backend = FakeIdeBackend(FakeWorkspace({"a/app.yaml": "1", "b/app.yaml": "2"}))
    gw = _gateway(ide_settings, backend)
    try:
        result = await gw.read("app.yaml")
        assert result.status == "ambiguous"
        assert result.matches == ("a/app.yaml", "b/app.yaml")
        assert "a/app.yaml" in result.message
    finally:
        await gw.aclose()


async def test_read_unreachable(ide_settings: Any) -> None:
    backend = FakeIdeBackend(FakeWorkspace({}))
    backend.down = True
    gw = _gateway(ide_settings, backend)
    try:
        result = await gw.read("app.yaml")
        assert result.status == "unreachable"
        assert "http://ide.test/api" in result.message
    finally:
        await gw.aclose()


async def test_exists_tristate(ide_settings: Any) -> None:
    backend = FakeIdeBackend(FakeWorkspace({"demo/app.yaml": "x", "a/dup.yaml": "1", "b/dup.yaml": "2"}))
    gw = _gateway(ide_settings, backend)
    try:
        assert await gw.exists("demo/app.yaml") is True
        assert await gw.exists("dup.yaml") is True
        assert await gw.exists("missing.yaml") is False
        backend.down = True
        assert await gw.exists("demo/app.yaml") is None
    finally:
        await gw.aclose()


async def test_validate_ok_report(ide_settings: Any) -> None:
    backend = FakeIdeBackend(FakeWorkspace({"app.yaml": "x: 1"}))
    gw = _gateway(ide_settings, backend)
    try:
        result = await gw.validate("app.yaml")
        assert result.ok is True
        assert result.valid is True
        assert result.type == "unknown"
        assert result.errors == ()
    finally:
        await gw.aclose()


async def test_validate_errors_normalised_string_and_dict_items(ide_settings: Any) -> None:
    def validator(path: str, content: str) -> dict[str, Any]:
        _ = (path, content)
        return {
            "path": path,
            "type": "native",
            "valid": False,
            "errors": ["owner is required", {"message": "bad port", "line": 3, "path": "/specs/network"}],
            "warnings": [{"msg": "old field", "field": "meta"}],
        }

    backend = FakeIdeBackend(FakeWorkspace({"app.yaml": "x"}), validator=validator)
    gw = _gateway(ide_settings, backend)
    try:
        result = await gw.validate("app.yaml")
        assert result.valid is False
        assert result.type == "native"
        assert [i.message for i in result.errors] == ["owner is required", "bad port"]
        assert result.errors[1].line == 3
        assert result.errors[1].path == "specs.network"
        assert all(i.source == "remote" for i in result.errors)
        assert result.warnings[0].message == "old field"
    finally:
        await gw.aclose()


async def test_validate_not_found(ide_settings: Any) -> None:
    backend = FakeIdeBackend(FakeWorkspace({}))
    gw = _gateway(ide_settings, backend)
    try:
        result = await gw.validate("missing.yaml")
        assert result.status == "not_found"
        assert result.valid is None
    finally:
        await gw.aclose()


def test_normalize_report_variants() -> None:
    valid, vtype, errors, warnings = normalize_report({"type": "native", "valid": True, "errors": [], "warnings": []})
    assert (valid, vtype, errors, warnings) == (True, "native", (), ())
    valid, _, errors, _ = normalize_report({"errors": ["boom"]})
    assert valid is False
    assert errors[0].message == "boom"
    valid, _, errors, _ = normalize_report({"valid": True, "errors": "not-a-list"})
    assert valid is True and errors == ()
    _, _, errors, _ = normalize_report({"errors": [{"text": "t", "row": 2, "column": 4, "instancePath": "/a/b"}]})
    assert (errors[0].line, errors[0].col, errors[0].path) == (2, 4, "a.b")
    _, _, errors, _ = normalize_report({"errors": [42]})
    assert errors[0].message == "42"


async def test_await_landing_succeeds_after_delay(ide_settings: Any) -> None:
    workspace = FakeWorkspace()
    backend = FakeIdeBackend(workspace)
    gw = _gateway(ide_settings, backend)
    sim = IdeSimulator(workspace, apply_delay_s=0.1)

    async def evs():  # type: ignore[no-untyped-def]
        yield TextEvent(text="creating")
        yield ActionEvent(payload={"action": "create_file", "path": "app.yaml", "content": "x: 1"})

    try:
        task = asyncio.create_task(sim.run_events(evs()))
        result = await gw.await_landing("app.yaml", expect_content="x: 1", timeout_s=1.0)
        await task
        assert result.landed is True
        assert result.result.content == "x: 1"
    finally:
        await gw.aclose()


async def test_await_landing_times_out(ide_settings: Any) -> None:
    backend = FakeIdeBackend(FakeWorkspace({}))
    gw = _gateway(ide_settings, backend)
    try:
        result = await gw.await_landing("nope.yaml", timeout_s=0.2)
        assert result.landed is False
        assert result.result.status == "not_found"
        assert result.waited_s >= 0.2
    finally:
        await gw.aclose()


async def test_await_landing_expect_absent(ide_settings: Any) -> None:
    workspace = FakeWorkspace({"gone.yaml": "x"})
    backend = FakeIdeBackend(workspace)
    gw = _gateway(ide_settings, backend)
    try:
        present = await gw.await_landing("gone.yaml", expect_absent=True, timeout_s=0.15)
        assert present.landed is False
        assert workspace.apply({"action": "delete_file", "path": "gone.yaml"}) is True
        absent = await gw.await_landing("gone.yaml", expect_absent=True, timeout_s=1.0)
        assert absent.landed is True
    finally:
        await gw.aclose()


async def test_await_landing_stops_when_unreachable(ide_settings: Any) -> None:
    backend = FakeIdeBackend(FakeWorkspace({}))
    backend.down = True
    gw = _gateway(ide_settings, backend)
    try:
        result = await gw.await_landing("app.yaml", timeout_s=5.0)
        assert result.landed is False
        assert result.waited_s < 2.0
        assert len(backend.calls) == 2
    finally:
        await gw.aclose()


async def test_probe_reports_reachable(ide_settings: Any) -> None:
    backend = FakeIdeBackend(FakeWorkspace({}))
    gw = _gateway(ide_settings, backend)
    try:
        probe = await gw.probe()
        assert probe["reachable"] is True
        assert probe["url"] == "http://ide.test/api"
        assert isinstance(probe["status"], int)
        backend.down = True
        probe = await gw.probe()
        assert probe == {"reachable": False, "url": "http://ide.test/api", "status": None}
    finally:
        await gw.aclose()
