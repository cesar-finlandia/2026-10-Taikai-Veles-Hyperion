"""Frozen-set loading and integrity (DP-EVAL WU-EVAL-01)."""
from __future__ import annotations

import pytest

from evals.loaders import (
    DataError,
    load_ablation,
    load_dialogues,
    load_golden,
    load_probes,
    verify_frozen,
    write_manifest,
)


def test_golden_counts_and_schema():
    rows = load_golden()
    assert len(rows) == 54
    by_expect = [r.expect for r in rows]
    assert by_expect.count("answer") == 50
    assert by_expect.count("no_answer") == 4
    for row in rows:
        if row.expect == "answer":
            assert row.source_docs, row.id


def test_probe_counts_and_labels():
    frozen = load_probes("frozen")
    assert len(frozen) == 110
    labels = [p.label for p in frozen]
    assert labels.count("in_scope") == 45
    assert labels.count("off_topic") == 35
    assert labels.count("injection") == 20
    assert labels.count("smalltalk") == 10
    tuning = load_probes("tuning")
    assert len(tuning) == 40
    for probe in frozen + tuning:
        assert probe.label in ("in_scope", "off_topic", "injection", "smalltalk")
        assert probe.set in ("frozen", "tuning")


def test_probe_frozen_and_tuning_disjoint():
    frozen = {p.text.strip().lower() for p in load_probes("frozen")}
    tuning = [p.text.strip().lower() for p in load_probes("tuning")]
    assert len(tuning) == 40
    for text in tuning:
        assert text not in frozen


def test_dialogues_schema_and_counts():
    dialogues = load_dialogues()
    assert len(dialogues) == 16
    turns = sum(len(d.turns) for d in dialogues)
    assert turns == 39
    allowed = {
        "actions",
        "action_paths",
        "text_contains",
        "text_not_contains",
        "workspace_has",
        "workspace_missing",
    }
    for dialogue in dialogues:
        assert dialogue.turns
        for turn in dialogue.turns:
            assert turn.user and turn.say
            assert set(turn.expect) <= allowed


def test_ablation_counts_and_ports():
    rows = load_ablation()
    assert len(rows) == 24
    kinds = [r.kind for r in rows]
    assert kinds.count("native") == 19
    assert kinds.count("device") == 5
    assert sum(1 for r in rows if r.expect_port is not None) == 15


def test_manifest_write_and_verify(tmp_path):
    import json
    import shutil

    for name in ("qa_golden.jsonl", "guard_probe.jsonl", "dialogues.jsonl", "ablation_requests.jsonl"):
        shutil.copy(f"evals/data/{name}", tmp_path / name)
    manifest = write_manifest(tmp_path)
    assert set(manifest) == {
        "qa_golden.jsonl",
        "guard_probe.jsonl",
        "dialogues.jsonl",
        "ablation_requests.jsonl",
    }
    assert verify_frozen(tmp_path) == []
    (tmp_path / "qa_golden.jsonl").write_text(
        (tmp_path / "qa_golden.jsonl").read_text(encoding="utf-8") + "\n", encoding="utf-8"
    )
    assert verify_frozen(tmp_path) == ["qa_golden.jsonl"]
    manifest_path = tmp_path / "MANIFEST.json"
    stored = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert stored == manifest


def test_loaders_reject_bad_rows(tmp_path):
    import shutil

    for name in ("qa_golden.jsonl", "guard_probe.jsonl", "dialogues.jsonl", "ablation_requests.jsonl"):
        shutil.copy(f"evals/data/{name}", tmp_path / name)
    golden_path = tmp_path / "qa_golden.jsonl"
    lines = golden_path.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace('"question"', '"TOPIC"')
    golden_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(DataError):
        load_golden(tmp_path)
    probe_path = tmp_path / "guard_probe.jsonl"
    first = probe_path.read_text(encoding="utf-8").splitlines()[0]
    probe_path.write_text(first + "\n" + first + "\n", encoding="utf-8")
    with pytest.raises(DataError):
        load_probes("frozen", tmp_path)
