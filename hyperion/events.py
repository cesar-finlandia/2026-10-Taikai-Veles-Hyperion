"""Events a turn yields. The transport (hyperion.sse) turns them into SSE lines."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class TextEvent:
    """An increment of assistant text (never accumulated text)."""
    text: str


@dataclass(frozen=True)
class ActionEvent:
    """An IDE action; payload is IdeAction.to_payload(), e.g. {'action': 'create_file', 'path': 'app.yaml', 'content': '...'}."""
    payload: dict[str, str]


Event = Union[TextEvent, ActionEvent]
