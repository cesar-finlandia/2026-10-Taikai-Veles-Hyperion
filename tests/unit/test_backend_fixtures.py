"""Calibration fixtures recorded from the real IDE backend (DP-IDE-PROTOCOL WU-IDE-05).

Skipped (2 skipped) until the operator runs scripts/probe_backend.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hyperion.ide.gateway import normalize_report

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "backend"


def _validate_fixtures() -> list[Path]:
    if not FIXTURE_DIR.is_dir():
        return []
    return sorted(FIXTURE_DIR.glob("*.validate.json"))


def test_fixture_reports_normalise() -> None:
    fixtures = _validate_fixtures()
    if not fixtures:
        pytest.skip("calibration not done: no *.validate.json fixtures")
    by_name = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in fixtures}
    bodies = {name: envelope.get("body") for name, envelope in by_name.items()}
    for name, body in bodies.items():
        assert isinstance(body, dict), f"{name} has no JSON object body"
        normalize_report(body)  # must not raise
    broken = next((b for n, b in bodies.items() if n.startswith("broken")), None)
    native = next((b for n, b in bodies.items() if n.startswith("app")), None)
    if broken is not None:
        valid, _, errors, _ = normalize_report(broken)
        assert valid is False
        assert len(errors) >= 1
    if native is not None:
        valid, _, _, _ = normalize_report(native)
        assert valid is True


def test_fixture_ambiguous_has_matches() -> None:
    fixtures = _validate_fixtures()
    if not fixtures:
        pytest.skip("calibration not done: no *.validate.json fixtures")
    reads = sorted(FIXTURE_DIR.glob("*.read.json"))
    app_reads = [p for p in reads if p.name.startswith("app")]
    if not app_reads:
        pytest.skip("calibration not done: no app.yaml read fixture")
    envelope = json.loads(app_reads[0].read_text(encoding="utf-8"))
    assert envelope.get("status") == 409
    assert isinstance(envelope.get("body"), dict)
    assert isinstance(envelope["body"].get("matches"), list)
