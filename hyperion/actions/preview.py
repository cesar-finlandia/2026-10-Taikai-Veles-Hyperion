"""Previews and the confirmation message (DP-ACTIONS §5.5)."""
from __future__ import annotations

import difflib
import hashlib

from hyperion.memory.models import PendingAction


def sha12(text: str) -> str:
    """hashlib.sha256(text.encode()).hexdigest()[:12] (the same formula SessionStore.note_file uses)."""
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def diff_preview(old: str, new: str, *, max_lines: int = 24, context: int = 1) -> str:
    lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=context))
    # drop the first two lines (---, +++)
    body = lines[2:] if len(lines) >= 2 else []
    if not body:
        return "(no visible difference)"
    # Keep @@ lines out
    kept_all = [ln for ln in body if not ln.startswith("@@")]
    kept = kept_all[:max_lines]
    extra = len(kept_all) - len(kept)
    if extra > 0:
        kept.append(f"… {extra} more changed lines")
    return "```diff\n" + "\n".join(kept) + "\n```"


def head_preview(text: str, *, max_lines: int = 6) -> str:
    all_lines = text.splitlines()
    head = all_lines[:max_lines]
    clipped = [ln[:120] for ln in head]
    n_lines = len(all_lines)
    n_chars = len(text)
    return "```\n" + "\n".join(clipped) + "\n```" + f"\n({n_lines} lines, {n_chars} characters)"


def format_confirmation(pending: PendingAction, *, minutes: int) -> str:
    lines: list[str] = ["I need your OK before I change anything:"]
    for a in pending.actions:
        desc = a.describe()
        if desc:
            desc = desc[0].upper() + desc[1:]
        lines.append(f"- {desc}")
    lines.append("")
    if pending.preview:
        lines.append(pending.preview)
        lines.append("")
    lines.append(f"Reply \"yes\" to go ahead or \"no\" to cancel. This request expires in {minutes} minutes.")
    return "\n".join(lines)
