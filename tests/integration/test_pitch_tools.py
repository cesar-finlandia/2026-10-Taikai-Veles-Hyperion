"""Pitch tools integration tests (DP-PITCH WU-PITCH-01)."""
from __future__ import annotations

import json
import subprocess
import sys


def test_replay_one_dialogue():
    proc = subprocess.run(
        [sys.executable, "scripts/demo_replay.py", "--only", "d03"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0
    assert "=== d03 — delete is confirmed before it happens" in proc.stdout
    assert proc.stdout.strip().splitlines()[-1] == "REPLAY OK dialogues=1 turns=2 unconfirmed_state_changes=0"


def test_replay_all_offline():
    proc = subprocess.run(
        [sys.executable, "scripts/demo_replay.py"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0
    assert proc.stdout.strip().splitlines()[-1] == "REPLAY OK dialogues=16 turns=39 unconfirmed_state_changes=0"


def test_replay_unknown_dialogue():
    proc = subprocess.run(
        [sys.executable, "scripts/demo_replay.py", "--only", "d99"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 2
    assert "ERROR: unknown dialogue: d99" in proc.stdout


def test_pitch_numbers_card_and_missing(tmp_path):
    card = {
        "meta": {"mode": "offline", "git_sha": "abc1234"},
        "gates": {"passed": 7, "total": 7},
        "claims": {
            "C-MEM": {"text": "T1", "ceiling": "X1"},
            "C-HITL": {"text": "T2", "ceiling": "X2"},
        },
    }
    card_path = tmp_path / "scorecard.json"
    card_path.write_text(json.dumps(card), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "scripts/pitch_numbers.py", "--card", str(card_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0
    assert proc.stdout.replace("\r\n", "\n") == (
        "SCORECARD mode=offline git=abc1234 gates=7/7\n"
        "C-MEM: T1\n"
        "    ceiling: X1\n"
        "C-HITL: T2\n"
        "    ceiling: X2\n"
        "PITCH NUMBERS: 2 claims\n"
    )
    missing = tmp_path / "does-not-exist.json"
    proc = subprocess.run(
        [sys.executable, "scripts/pitch_numbers.py", "--card", str(missing)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 2
    assert proc.stdout.startswith("ERROR: scorecard not found:")
