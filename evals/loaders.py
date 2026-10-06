"""Frozen-set loading and integrity manifest (DP-EVAL section 3)."""
from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

DATA_DIR: Path = Path("evals/data")
FROZEN_FILES: tuple[str, ...] = (
    "qa_golden.jsonl",
    "guard_probe.jsonl",
    "dialogues.jsonl",
    "ablation_requests.jsonl",
)

_EXPECT_KEYS: tuple[str, ...] = (
    "actions",
    "action_paths",
    "text_contains",
    "text_not_contains",
    "workspace_has",
    "workspace_missing",
)


class DataError(ValueError):
    """A data row violates its schema; str(e) names the file, the row id and the problem."""


@dataclass(frozen=True)
class GoldenQ:
    id: str
    category: str
    question: str
    keywords_all: tuple[str, ...]
    keywords_any: tuple[str, ...]
    source_docs: tuple[str, ...]
    expect: Literal["answer", "no_answer"]


@dataclass(frozen=True)
class Probe:
    id: str
    set: Literal["frozen", "tuning"]
    label: Literal["in_scope", "off_topic", "injection", "smalltalk"]
    text: str


@dataclass(frozen=True)
class DialogueTurn:
    user: str
    say: str
    expect: dict[str, Any]


@dataclass(frozen=True)
class Dialogue:
    id: str
    title: str
    workspace: dict[str, str]
    turns: tuple[DialogueTurn, ...]


@dataclass(frozen=True)
class AblationReq:
    id: str
    text: str
    kind: Literal["native", "device"]
    expect_image: str
    expect_port: int | None


def _rows(data_dir: Path, name: str) -> list[dict[str, Any]]:
    path = Path(data_dir) / name
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DataError(f"{name} <missing>: cannot read file ({exc})") from exc
    rows: list[dict[str, Any]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise DataError(f"{name} <line {lineno}>: not valid JSON ({exc})") from exc
        if not isinstance(row, dict):
            raise DataError(f"{name} <line {lineno}>: row is not an object")
        rows.append(row)
    return rows


def _need_str(row: dict[str, Any], name: str, rid: str, field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str):
        raise DataError(f"{name} {rid}: field {field!r} must be a string")
    return value


def _need_str_list(row: dict[str, Any], name: str, rid: str, field: str) -> tuple[str, ...]:
    value = row.get(field)
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise DataError(f"{name} {rid}: field {field!r} must be a list of strings")
    return tuple(value)


def _check_unique(name: str, ids: list[str]) -> None:
    seen: set[str] = set()
    for rid in ids:
        if rid in seen:
            raise DataError(f"{name} {rid}: duplicate id")
        seen.add(rid)


def load_golden(data_dir: Path = DATA_DIR) -> list[GoldenQ]:
    """Rows of qa_golden.jsonl."""
    name = "qa_golden.jsonl"
    out: list[GoldenQ] = []
    ids: list[str] = []
    for row in _rows(data_dir, name):
        rid = str(row.get("id", "<missing id>"))
        ids.append(rid)
        expect = row.get("expect")
        if expect not in ("answer", "no_answer"):
            raise DataError(f"{name} {rid}: field 'expect' must be 'answer' or 'no_answer'")
        out.append(
            GoldenQ(
                id=_need_str(row, name, rid, "id"),
                category=_need_str(row, name, rid, "category"),
                question=_need_str(row, name, rid, "question"),
                keywords_all=_need_str_list(row, name, rid, "keywords_all"),
                keywords_any=_need_str_list(row, name, rid, "keywords_any"),
                source_docs=_need_str_list(row, name, rid, "source_docs"),
                expect=expect,  # type: ignore[arg-type]
            )
        )
    _check_unique(name, ids)
    return out


def load_probes(
    which: Literal["frozen", "tuning"] | None = None,
    data_dir: Path = DATA_DIR,
) -> list[Probe]:
    """Rows of guard_probe.jsonl filtered by `set`; for 'tuning' also the rows of guard_tuning_extra.jsonl when that file exists."""
    name = "guard_probe.jsonl"
    rows = _rows(data_dir, name)
    extra_path = Path(data_dir) / "guard_tuning_extra.jsonl"
    if extra_path.exists():
        rows = rows + _rows(data_dir, "guard_tuning_extra.jsonl")
    out: list[Probe] = []
    ids: list[str] = []
    for row in rows:
        rid = str(row.get("id", "<missing id>"))
        if which is not None and row.get("set") != which:
            continue
        ids.append(rid)
        if row.get("set") not in ("frozen", "tuning"):
            raise DataError(f"{name} {rid}: field 'set' must be 'frozen' or 'tuning'")
        if row.get("label") not in ("in_scope", "off_topic", "injection", "smalltalk"):
            raise DataError(f"{name} {rid}: field 'label' is not a known probe label")
        out.append(
            Probe(
                id=_need_str(row, name, rid, "id"),
                set=row["set"],
                label=row["label"],
                text=_need_str(row, name, rid, "text"),
            )
        )
    _check_unique(name, ids)
    return out


def _coerce_turn(name: str, rid: str, index: int, raw: Any) -> DialogueTurn:
    if not isinstance(raw, dict):
        raise DataError(f"{name} {rid}: turn {index} must be an object")
    user = raw.get("user")
    say = raw.get("say")
    expect = raw.get("expect", {})
    if not isinstance(user, str):
        raise DataError(f"{name} {rid}: turn {index} field 'user' must be a string")
    if not isinstance(say, str):
        raise DataError(f"{name} {rid}: turn {index} field 'say' must be a string")
    if not isinstance(expect, dict):
        raise DataError(f"{name} {rid}: turn {index} field 'expect' must be an object")
    for key in expect:
        if key not in _EXPECT_KEYS:
            raise DataError(f"{name} {rid}: turn {index} has unknown expect key {key!r}")
    return DialogueTurn(user=user, say=say, expect=dict(expect))


def load_dialogues(data_dir: Path = DATA_DIR) -> list[Dialogue]:
    """Rows of dialogues.jsonl."""
    name = "dialogues.jsonl"
    out: list[Dialogue] = []
    ids: list[str] = []
    for row in _rows(data_dir, name):
        rid = str(row.get("id", "<missing id>"))
        ids.append(rid)
        workspace = row.get("workspace")
        if not isinstance(workspace, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in workspace.items()
        ):
            raise DataError(f"{name} {rid}: field 'workspace' must be an object of strings")
        turns = row.get("turns")
        if not isinstance(turns, list) or not turns:
            raise DataError(f"{name} {rid}: field 'turns' must be a non-empty list")
        out.append(
            Dialogue(
                id=_need_str(row, name, rid, "id"),
                title=_need_str(row, name, rid, "title"),
                workspace=dict(workspace),
                turns=tuple(_coerce_turn(name, rid, i, t) for i, t in enumerate(turns)),
            )
        )
    _check_unique(name, ids)
    return out


def load_ablation(data_dir: Path = DATA_DIR) -> list[AblationReq]:
    """Rows of ablation_requests.jsonl."""
    name = "ablation_requests.jsonl"
    out: list[AblationReq] = []
    ids: list[str] = []
    for row in _rows(data_dir, name):
        rid = str(row.get("id", "<missing id>"))
        ids.append(rid)
        kind = row.get("kind")
        if kind not in ("native", "device"):
            raise DataError(f"{name} {rid}: field 'kind' must be 'native' or 'device'")
        port = row.get("expect_port")
        if port is not None and (not isinstance(port, int) or isinstance(port, bool)):
            raise DataError(f"{name} {rid}: field 'expect_port' must be an integer or null")
        out.append(
            AblationReq(
                id=_need_str(row, name, rid, "id"),
                text=_need_str(row, name, rid, "text"),
                kind=kind,  # type: ignore[arg-type]
                expect_image=_need_str(row, name, rid, "expect_image"),
                expect_port=port,
            )
        )
    _check_unique(name, ids)
    return out


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_manifest(data_dir: Path = DATA_DIR) -> dict[str, str]:
    """Write MANIFEST.json = {file name: sha256 hex} for FROZEN_FILES; return it."""
    manifest = {name: _sha256(Path(data_dir) / name) for name in FROZEN_FILES}
    (Path(data_dir) / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def verify_frozen(data_dir: Path = DATA_DIR) -> list[str]:
    """Names of FROZEN_FILES whose sha256 differs from MANIFEST.json (or that are missing); [] means untouched."""
    manifest_path = Path(data_dir) / "MANIFEST.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return list(FROZEN_FILES)
    bad: list[str] = []
    for name in FROZEN_FILES:
        path = Path(data_dir) / name
        try:
            digest = _sha256(path)
        except OSError:
            bad.append(name)
            continue
        if manifest.get(name) != digest:
            bad.append(name)
    return bad


def _cli() -> int:
    if "--write-manifest" in sys.argv[1:]:
        manifest = write_manifest()
        for name in FROZEN_FILES:
            print(f"{name} {manifest[name]}")
        return 0
    print("usage: python -m evals.loaders --write-manifest")
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
