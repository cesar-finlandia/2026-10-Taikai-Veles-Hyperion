"""TurnContext: the per-turn budget, deadline and trace handed to every stage."""
from __future__ import annotations
import time
import uuid
from dataclasses import dataclass
from hyperion.config import Settings
from hyperion.trace import TraceRecorder


@dataclass
class TurnContext:
    user_id: str
    turn_id: str
    text: str
    started_at: float
    deadline: float            # time.monotonic() value
    llm_budget: int
    llm_calls: int
    trace: TraceRecorder

    def take_llm_call(self) -> bool:
        """If llm_calls < llm_budget increment and return True, else return False."""
        if self.llm_calls < self.llm_budget:
            self.llm_calls += 1
            return True
        return False

    def time_left(self) -> float:
        """Seconds until deadline (may be negative)."""
        return self.deadline - time.monotonic()

    def expired(self) -> bool:
        """True when time_left() <= 0."""
        return self.time_left() <= 0


def new_turn(settings: Settings, user_id: str, text: str) -> TurnContext:
    """turn_id = 8 hex chars of uuid4; deadline = monotonic()+settings.turn_deadline_s; llm_budget = settings.turn_llm_budget."""
    turn_id = uuid.uuid4().hex[:8]
    now = time.monotonic()
    return TurnContext(
        user_id=user_id,
        turn_id=turn_id,
        text=text,
        started_at=now,
        deadline=now + settings.turn_deadline_s,
        llm_budget=settings.turn_llm_budget,
        llm_calls=0,
        trace=TraceRecorder(turn_id=turn_id, user_id=user_id),
    )
