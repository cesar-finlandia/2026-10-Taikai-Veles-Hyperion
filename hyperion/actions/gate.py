"""HITL gate: which actions need a recorded confirmation (DP-ACTIONS §5.4)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from hyperion.config import Settings
from hyperion.ide.actions import IdeAction

Reason = Literal["delete", "edit", "overwrite"]


class UnauthorizedAction(RuntimeError):
    """The executor was asked to emit an action that requires confirmation without one."""


@dataclass(frozen=True)
class PlannedItem:
    action: IdeAction
    reason: Reason | None  # why a confirmation is required; None when none is
    patch_edit: bool = False  # True when action is an edit_file produced by patch ops on a file that was read


def classify(action: IdeAction, *, mode: str, existed: bool | None, patch_edit: bool) -> Reason | None:
    if action.action in ("delete_file", "delete_folder"):
        return "delete"
    if action.action == "edit_file":
        if patch_edit:
            if mode == "strict":
                return "edit"
            return None
        return "overwrite"
    if action.action == "create_file":
        if existed:
            return "overwrite"
        return None
    if action.action == "create_folder":
        return None
    return None


def requires_confirmation(items: Sequence[PlannedItem], settings: Settings) -> bool:
    if not settings.feature_hitl:
        return False
    return any(i.reason is not None for i in items)
