"""Per-user session store: turns, summary, file refs, facts, pending confirmation
(DP-MEMORY §5.5–§5.8)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

from hyperion.config import Settings
from hyperion.ide.actions import make_action
from hyperion.llm.base import LLMLike, LLMUnavailable
from hyperion.memory.budget import clip, estimate_tokens, fit_to_budget
from hyperion.memory.facts import extract_facts
from hyperion.memory.models import FileRef, PendingAction, Session, Turn
from hyperion.memory.recall import pronoun_target

logger = logging.getLogger(__name__)

MEMORY_SUMMARY_SYSTEM = (
    "You compress a chat between a user and Hyperion, the assistant inside the HyperAI IDE. "
    "Write a summary of at most 80 words in plain sentences. "
    "Keep: the user's name if given, file paths, what was created, edited or deleted, open questions, and anything the user declined. "
    "Drop greetings and small talk. Output the summary only."
)

_MAX_TURNS = 40
_MAX_DECLINED = 5


class SessionStore:
    def __init__(self, settings: Settings, *, clock: Callable[[], float] = time.time) -> None:
        self._settings = settings
        self._clock = clock
        self._sessions: OrderedDict[str, Session] = OrderedDict()

    def now(self) -> float:
        return self._clock()

    def _expired(self, session: Session) -> bool:
        return self.now() - session.updated_at > self._settings.session_ttl_s

    def _new_session(self, user_id: str) -> Session:
        now = self.now()
        return Session(user_id=user_id, created_at=now, updated_at=now)

    def get(self, user_id: str) -> Session:
        """Return the session (create it if missing or expired); refresh updated_at;
        mark as most recently used; evict the least recently used when over capacity."""
        existing = self._sessions.get(user_id)
        if existing is not None and not self._expired(existing):
            existing.updated_at = self.now()
            self._sessions.move_to_end(user_id)
            return existing
        if existing is not None:
            del self._sessions[user_id]
        session = self._new_session(user_id)
        self._sessions[user_id] = session
        while len(self._sessions) > self._settings.session_max:
            self._sessions.popitem(last=False)
        return session

    def peek(self, user_id: str) -> Session | None:
        """Return the live session without creating or refreshing it
        (None when missing/expired)."""
        session = self._sessions.get(user_id)
        if session is None:
            return None
        if self._expired(session):
            del self._sessions[user_id]
            return None
        return session

    def record_turn(self, user_id: str, *, user: str, assistant: str, intent: str) -> None:
        """Append a Turn (user and assistant clipped to 600 chars), increment
        turn_count, merge extract_facts(user), keep at most 40 turns."""
        session = self.get(user_id)
        session.turns.append(
            Turn(ts=self.now(), user=clip(user, 600), assistant=clip(assistant, 600), intent=intent)
        )
        session.turn_count += 1
        found = extract_facts(user)
        if "name" in found:
            session.facts["name"] = found["name"]
        notes = [v for k, v in found.items() if k == "note" or k.startswith("note:")]
        for note in notes:
            existing = sum(1 for k in session.facts if k == "note" or k.startswith("note:"))
            session.facts[f"note:{existing + 1}"] = note
        if len(session.turns) > _MAX_TURNS:
            session.turns = session.turns[-_MAX_TURNS:]

    def set_pending(self, user_id: str, pending: PendingAction) -> None:
        self.get(user_id).pending = pending

    def peek_pending(self, user_id: str) -> PendingAction | None:
        """The pending action if it exists and has not expired
        (an expired one is cleared and None returned)."""
        session = self.peek(user_id)
        if session is None or session.pending is None:
            return None
        if session.pending.expires_at <= self.now():
            session.pending = None
            return None
        return session.pending

    def take_pending(self, user_id: str) -> PendingAction | None:
        """peek_pending + clear."""
        pending = self.peek_pending(user_id)
        if pending is None:
            return None
        session = self.peek(user_id)
        if session is not None:
            session.pending = None
        return pending

    def clear_pending(self, user_id: str, *, declined_summary: str | None = None) -> None:
        """Clear the pending action; when declined_summary is given append it to
        Session.declined (max 5, oldest dropped)."""
        session = self.peek(user_id)
        if session is None:
            return
        session.pending = None
        if declined_summary is not None:
            session.declined.append(declined_summary)
            session.declined = session.declined[-_MAX_DECLINED:]

    def _most_recent_live_file(self, session: Session) -> str | None:
        best: str | None = None
        best_ts = float("-inf")
        for ref in session.files.values():
            if ref.deleted:
                continue
            if ref.ts >= best_ts:
                best_ts = ref.ts
                best = ref.path
        return best

    def note_file(self, user_id: str, path: str, kind: str, op: str, content: str | None) -> None:
        """Record/refresh a FileRef, set last_file = path (unless op == 'delete'),
        clear 'deleted' flag unless op == 'delete'."""
        session = self.get(user_id)
        sha = hashlib.sha256(content.encode()).hexdigest()[:12] if content is not None else ""
        session.files[path] = FileRef(
            path=path, kind=kind, last_op=op, sha=sha, ts=self.now(), deleted=(op == "delete")
        )
        if op != "delete":
            session.last_file = path
        elif session.last_file == path:
            session.last_file = self._most_recent_live_file(session)

    def forget_file(self, user_id: str, path: str) -> None:
        """Mark deleted=True, keep the ref for recall, and if last_file == path set
        last_file to the most recent non-deleted file or None."""
        session = self.get(user_id)
        ref = session.files.get(path)
        if ref is not None:
            ref.deleted = True
        if session.last_file == path:
            session.last_file = self._most_recent_live_file(session)

    def resolve_reference(self, user_id: str, text: str) -> str | None:
        """pronoun_target(session, text) for the live session."""
        session = self.peek(user_id)
        if session is None:
            return None
        return pronoun_target(session, text)

    def history_for_prompt(self, user_id: str, *, max_tokens: int = 1100) -> str:
        """Prompt-ready history text; '' when there is nothing (§5.5)."""
        session = self.peek(user_id)
        if session is None or (not session.summary and not session.turns):
            return ""
        parts: list[str] = []
        summary_cost = 0
        if session.summary:
            parts.append(f"Summary of earlier conversation: {clip(session.summary, 1400)}")
            summary_cost = estimate_tokens(parts[0])
        lines: list[str] = []
        for turn in session.turns[-8:]:
            lines.append(f"User: {clip(turn.user, 300)}")
            lines.append(f"Assistant: {clip(turn.assistant, 300)}")
        budget = max_tokens - summary_cost
        parts.extend(fit_to_budget(lines, budget) if budget > 0 else [])
        if session.pending is not None:
            parts.append(f"Pending confirmation: {session.pending.summary}")
        if session.last_file:
            parts.append(f"Last file: {session.last_file}")
        name = session.facts.get("name")
        if name:
            parts.append(f"User name: {name}")
        return "\n".join(parts)

    async def compress_if_needed(self, user_id: str, llm: LLMLike) -> bool:
        """Fold old turns into Session.summary when the retained turns exceed 1800
        estimated tokens (§5.6). Returns True when it compressed."""
        session = self.peek(user_id)
        if session is None:
            return False
        total = sum(estimate_tokens(t.user) + estimate_tokens(t.assistant) for t in session.turns)
        if total <= 1800 or len(session.turns) <= 4:
            return False
        old = session.turns[:-3]
        body = "\n".join(
            f"User: {clip(t.user, 200)}\nAssistant: {clip(t.assistant, 200)}" for t in old
        )
        msg = (
            f"Existing summary:\n{session.summary or '(none)'}\n\nTurns to fold in:\n"
            + body
            + "\n\nNew summary:"
        )
        try:
            text = await llm.chat(
                [
                    {"role": "system", "content": MEMORY_SUMMARY_SYSTEM},
                    {"role": "user", "content": msg},
                ],
                name="memory_summary",
                turn=None,
                temperature=0.0,
                max_tokens=160,
            )
        except Exception:
            text = (
                session.summary
                + " "
                + " ".join(f"User asked: {clip(t.user, 80)}." for t in old)
            ).strip()[-700:]
        session.summary = clip(text, 700)
        session.turns = session.turns[-3:]
        return True

    def stats(self) -> dict[str, int]:
        """{'sessions': n, 'pending': n, 'turns': total_turns}"""
        return {
            "sessions": len(self._sessions),
            "pending": sum(1 for s in self._sessions.values() if s.pending is not None),
            "turns": sum(len(s.turns) for s in self._sessions.values()),
        }

    def _serialise_session(self, session: Session) -> dict[str, Any]:
        pending: dict[str, Any] | None = None
        if session.pending is not None:
            p = session.pending
            pending = {
                "id": p.id,
                "actions": [a.to_payload() for a in p.actions],
                "summary": p.summary,
                "preview": p.preview,
                "origin_text": p.origin_text,
                "created_at": p.created_at,
                "expires_at": p.expires_at,
                "validate_paths": list(p.validate_paths),
            }
        return {
            "user_id": session.user_id,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "turns": [
                {"ts": t.ts, "user": t.user, "assistant": t.assistant, "intent": t.intent}
                for t in session.turns
            ],
            "summary": session.summary,
            "files": {
                path: {
                    "path": r.path,
                    "kind": r.kind,
                    "last_op": r.last_op,
                    "sha": r.sha,
                    "ts": r.ts,
                    "deleted": r.deleted,
                }
                for path, r in session.files.items()
            },
            "last_file": session.last_file,
            "pending": pending,
            "declined": list(session.declined),
            "facts": dict(session.facts),
            "turn_count": session.turn_count,
        }

    def _deserialise_session(self, data: Any) -> Session:
        if not isinstance(data, dict):
            raise ValueError("session is not an object")
        user_id = data["user_id"]
        if not isinstance(user_id, str):
            raise ValueError("bad user_id")
        turns = [
            Turn(ts=float(t["ts"]), user=str(t["user"]), assistant=str(t["assistant"]), intent=str(t["intent"]))
            for t in data.get("turns", [])
        ]
        files: dict[str, FileRef] = {}
        raw_files = data.get("files", {})
        if not isinstance(raw_files, dict):
            raise ValueError("bad files")
        for path, r in raw_files.items():
            files[str(path)] = FileRef(
                path=str(r["path"]),
                kind=str(r["kind"]),
                last_op=str(r["last_op"]),
                sha=str(r.get("sha", "")),
                ts=float(r["ts"]),
                deleted=bool(r.get("deleted", False)),
            )
        pending = None
        raw_pending = data.get("pending")
        if raw_pending is not None:
            if not isinstance(raw_pending, dict):
                raise ValueError("bad pending")
            actions = [make_action(**a) for a in raw_pending["actions"]]
            pending = PendingAction(
                id=str(raw_pending["id"]),
                actions=actions,
                summary=str(raw_pending["summary"]),
                preview=str(raw_pending.get("preview", "")),
                origin_text=str(raw_pending.get("origin_text", "")),
                created_at=float(raw_pending["created_at"]),
                expires_at=float(raw_pending["expires_at"]),
                validate_paths=[str(p) for p in raw_pending.get("validate_paths", [])],
            )
        last_file = data.get("last_file")
        return Session(
            user_id=user_id,
            created_at=float(data.get("created_at", 0.0)),
            updated_at=float(data.get("updated_at", 0.0)),
            turns=turns,
            summary=str(data.get("summary", "")),
            files=files,
            last_file=str(last_file) if last_file is not None else None,
            pending=pending,
            declined=[str(d) for d in data.get("declined", [])],
            facts={str(k): str(v) for k, v in data.get("facts", {}).items()},
            turn_count=int(data.get("turn_count", len(turns))),
        )

    def snapshot(self) -> dict[str, Any]:
        return {"version": 1, "sessions": [self._serialise_session(s) for s in self._sessions.values()]}

    def restore(self, data: dict[str, Any]) -> None:
        if not isinstance(data, dict) or not isinstance(data.get("sessions"), list):
            return
        rebuilt: OrderedDict[str, Session] = OrderedDict()
        for raw in data["sessions"]:
            try:
                session = self._deserialise_session(raw)
            except Exception:
                continue
            rebuilt[session.user_id] = session
        self._sessions = rebuilt

    def save(self, path: Path) -> None:
        """Atomic JSON write (tmp file + os.replace); never raises (logs a warning)."""
        try:
            path = Path(path)
            if path.parent != Path("") and str(path.parent) not in ("", "."):
                path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(self.snapshot()), encoding="utf-8")
            os.replace(tmp, path)
        except Exception as e:
            logger.warning("memory save failed for %s: %s", path, e)

    def load(self, path: Path) -> bool:
        """Restore from a file; False when missing or unreadable; never raises."""
        try:
            raw = Path(path).read_text(encoding="utf-8")
            self.restore(json.loads(raw))
            return True
        except Exception as e:
            logger.warning("memory load failed for %s: %s", path, e)
            return False
