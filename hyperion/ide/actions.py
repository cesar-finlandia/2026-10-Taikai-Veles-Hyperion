"""The five IDE actions of the Hyperion protocol, as validated immutable values."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from hyperion.ide.paths import PathError, normalize_path
from hyperion.issues import Issue

ActionName = Literal["create_folder", "delete_folder", "create_file", "edit_file", "delete_file"]
ACTION_NAMES: tuple[str, ...] = ("create_folder", "delete_folder", "create_file", "edit_file", "delete_file")

_FILE_WRITE_ACTIONS = ("create_file", "edit_file")
_DELETE_ACTIONS = ("delete_file", "delete_folder")


class ActionError(ValueError):
    """An action violates the protocol; str(e) is a sentence safe to show the user."""


@dataclass(frozen=True)
class IdeAction:
    action: ActionName
    path: str
    content: str | None = None

    def to_payload(self) -> dict[str, str]:
        """{'action','path'} plus 'content' for create_file/edit_file. Key order: action, path, content."""
        payload: dict[str, str] = {"action": self.action, "path": self.path}
        if self.action in _FILE_WRITE_ACTIONS:
            payload["content"] = self.content if self.content is not None else ""
        return payload

    def describe(self) -> str:
        """Plain English: 'create the file `app.yaml`', 'replace the contents of `app.yaml`', 'delete the file `x`',
        'create the folder `d`', 'delete the folder `d` and everything in it'."""
        if self.action == "create_file":
            return f"create the file `{self.path}`"
        if self.action == "edit_file":
            return f"replace the contents of `{self.path}`"
        if self.action == "delete_file":
            return f"delete the file `{self.path}`"
        if self.action == "create_folder":
            return f"create the folder `{self.path}`"
        return f"delete the folder `{self.path}` and everything in it"

    @property
    def is_delete(self) -> bool:
        """True for delete_file and delete_folder."""
        return self.action in _DELETE_ACTIONS

    @property
    def is_overwrite(self) -> bool:
        """True for edit_file."""
        return self.action == "edit_file"


def make_action(action: str, path: str, content: str | None = None, *, max_content_chars: int = 60000) -> IdeAction:
    """Validate and build. Raises ActionError when: action not in ACTION_NAMES; path fails normalize_path (PathError is
    re-raised as ActionError with the same message); create_file/edit_file without str content; content longer than
    max_content_chars; content given for a folder or delete action."""
    if action not in ACTION_NAMES:
        raise ActionError(f"Unknown IDE action '{action}'; expected one of: {', '.join(ACTION_NAMES)}.")
    try:
        clean = normalize_path(path)
    except PathError as e:
        raise ActionError(str(e)) from e
    if action in _FILE_WRITE_ACTIONS:
        if not isinstance(content, str):
            raise ActionError(f"The action '{action}' needs file content as text.")
        if len(content) > max_content_chars:
            raise ActionError(
                f"The content for `{clean}` is too long ({len(content)} characters; the limit is {max_content_chars})."
            )
        return IdeAction(action=action, path=clean, content=content)  # type: ignore[arg-type]
    if content is not None:
        raise ActionError(f"The action '{action}' does not take content.")
    return IdeAction(action=action, path=clean)  # type: ignore[arg-type]


def validate_batch(actions: Sequence[IdeAction], *, max_actions: int) -> list[Issue]:
    """Pre-send validator. Errors (code in parentheses): more than max_actions ('too_many_actions'); the same
    (action, path) twice ('duplicate_action'); create_file and delete_file on the same path in one batch
    ('conflicting_actions'); delete_folder of '.' or '' ('root_delete'). Returns [] when clean."""
    found: list[Issue] = []
    if len(actions) > max_actions:
        found.append(
            Issue(
                severity="error",
                code="too_many_actions",
                message=f"Too many actions ({len(actions)}); at most {max_actions} may be sent together.",
                source="local",
            )
        )
    seen: set[tuple[str, str]] = set()
    for a in actions:
        key = (a.action, a.path)
        if key in seen:
            found.append(
                Issue(
                    severity="error",
                    code="duplicate_action",
                    message=f"The action '{a.action}' on `{a.path}` appears more than once.",
                    path=a.path,
                    source="local",
                )
            )
        else:
            seen.add(key)
    by_path: dict[str, set[str]] = {}
    for a in actions:
        by_path.setdefault(a.path, set()).add(a.action)
    for p, names in by_path.items():
        if "create_file" in names and "delete_file" in names:
            found.append(
                Issue(
                    severity="error",
                    code="conflicting_actions",
                    message=f"`{p}` cannot be created and deleted in the same batch.",
                    path=p,
                    source="local",
                )
            )
    for a in actions:
        if a.action == "delete_folder" and a.path in (".", ""):
            found.append(
                Issue(
                    severity="error",
                    code="root_delete",
                    message="Deleting the workspace root is not allowed.",
                    path=a.path,
                    source="local",
                )
            )
    return found
