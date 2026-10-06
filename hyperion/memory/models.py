"""Session memory data model (DP-MEMORY §3)."""
from __future__ import annotations

from dataclasses import dataclass, field

from hyperion.ide.actions import IdeAction


@dataclass
class Turn:
    ts: float
    user: str
    assistant: str
    intent: str


@dataclass
class FileRef:
    path: str
    kind: str  # 'native' | 'device' | 'other' | 'folder'
    last_op: str  # 'create' | 'edit' | 'delete' | 'read' | 'validate'
    sha: str  # sha256 hex[:12] of the last known content ('' for folders / unknown)
    ts: float
    deleted: bool = False


@dataclass
class PendingAction:
    id: str  # 6 hex chars
    actions: list[IdeAction]
    summary: str  # one sentence: what will happen
    preview: str  # compact diff / content preview shown to the user
    origin_text: str  # the user message that caused it
    created_at: float
    expires_at: float
    validate_paths: list[str]  # paths to validate after execution


@dataclass
class Session:
    user_id: str
    created_at: float = 0.0
    updated_at: float = 0.0
    turns: list[Turn] = field(default_factory=list)
    summary: str = ""
    files: dict[str, FileRef] = field(default_factory=dict)
    last_file: str | None = None
    pending: PendingAction | None = None
    declined: list[str] = field(default_factory=list)  # newest last, max 5
    facts: dict[str, str] = field(default_factory=dict)  # 'name' -> 'Elena'; 'note:1' -> '...'
    turn_count: int = 0
