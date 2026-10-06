"""Workspace path normalisation. Every path that reaches an IDE action passes through normalize_path."""
from __future__ import annotations

import os
import re

_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_ILLEGAL_CHARS = frozenset('<>:"|?*')


class PathError(ValueError):
    """The path cannot be used; str(e) is a sentence safe to show the user."""


def normalize_path(raw: str) -> str:
    """Return a clean workspace-relative path using '/' separators, or raise PathError."""
    if not isinstance(raw, str):
        raise PathError("The path must be text.")
    p = raw.strip().strip("`'\"")
    p = p.replace("\\", "/")
    if not p:
        raise PathError("The path is empty.")
    if p.startswith("/") or _DRIVE_RE.match(p) or p.startswith("~"):
        raise PathError("Paths must be relative to the workspace root (no leading '/', drive letter or '~').")
    segments = [s for s in p.split("/") if s not in ("", ".")]
    if not segments:
        raise PathError("The path is empty.")
    for s in segments:
        if s == "..":
            raise PathError("Paths may not contain '..'.")
    for s in segments:
        for ch in s:
            if ord(ch) < 32 or ch in _ILLEGAL_CHARS:
                raise PathError("The path contains characters the IDE does not allow.")
    joined = "/".join(segments)
    if len(segments) > 8 or len(joined) > 200:
        raise PathError("The path is too long.")
    return joined


def parent_dirs(path: str) -> list[str]:
    """'a/b/c.yaml' -> ['a', 'a/b'];  'c.yaml' -> []."""
    segments = [s for s in path.split("/") if s not in ("", ".")]
    out: list[str] = []
    for i in range(1, len(segments)):
        out.append("/".join(segments[:i]))
    return out


def is_bare_name(path: str) -> bool:
    """True when the path has no '/' (the IDE then searches the workspace for the first match)."""
    return "/" not in path


def basename(path: str) -> str:
    """Return the final segment of a workspace-relative path."""
    return path.split("/")[-1] if path else ""


def extension(path: str) -> str:
    """Lower-case extension including the dot, '' when none: 'App.YAML' -> '.yaml'."""
    return os.path.splitext(basename(path))[1].lower()
