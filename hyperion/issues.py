"""One issue type shared by every checker (local profile checker, IDE validator, citation checker)."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, Literal

Severity = Literal["error", "warning"]
IssueSource = Literal["local", "remote"]


@dataclass(frozen=True)
class Issue:
    """severity+code+message, optionally located by dotted `path` and 1-based `line`/`col`."""
    severity: Severity
    code: str
    message: str
    path: str | None = None
    line: int | None = None
    col: int | None = None
    source: IssueSource = "local"

    def format(self) -> str:
        """One human line, e.g. 'error at line 12 (specs.network): ports[0].port must be an integer'."""
        loc = ""
        if self.line is not None:
            loc = f" at line {self.line}"
        if self.path:
            loc += f" ({self.path})"
        return f"{self.severity}{loc}: {self.message}"


def errors_only(issues: Iterable[Issue]) -> list[Issue]:
    """Return only the issues whose severity is 'error', order preserved."""
    return [i for i in issues if i.severity == "error"]
