"""The five ActionsFlow handlers wired to memory, planner and executor (DP-ACTIONS §5.13–§5.17)."""
from __future__ import annotations

import uuid
from typing import AsyncIterator, Literal

from hyperion.actions.executor import Collected, ExecutionReport
from hyperion.actions.gate import PlannedItem, requires_confirmation
from hyperion.actions.intents import ActIntent
from hyperion.actions.messages import MSG
from hyperion.actions.preview import format_confirmation, sha12
from hyperion.actions.prompts import explain_messages
from hyperion.config import Settings
from hyperion.context import TurnContext
from hyperion.dsl.check import check_profile
from hyperion.dsl.summary import summarize_profile
from hyperion.events import Event, TextEvent
from hyperion.ide.gateway import IdeGateway
from hyperion.ide.paths import extension
from hyperion.issues import errors_only
from hyperion.llm.base import LLMBadJson, LLMLike, LLMUnavailable
from hyperion.memory.budget import clip
from hyperion.memory.models import PendingAction
from hyperion.memory.store import SessionStore


def _clip_block(content: str, limit: int = 1500) -> str:
    if len(content) > limit:
        return content[:limit] + "\n… (showing the first 1,500 characters)"
    return content


class ActionsFlow:
    def __init__(self, settings: Settings, llm: LLMLike, gateway: IdeGateway, store: SessionStore) -> None:
        from hyperion.actions.planner import ActionPlanner
        from hyperion.actions.executor import Executor

        self._settings = settings
        self._llm = llm
        self._gateway = gateway
        self._store = store
        self._planner = ActionPlanner(settings, llm, gateway, store)
        self._executor = Executor(settings, llm, gateway, store)

    @property
    def planner(self):  # for actions_env
        return self._planner

    @property
    def executor(self):  # for actions_env
        return self._executor

    async def handle_request(self, turn: TurnContext, intent: ActIntent) -> AsyncIterator[Event]:
        if intent.path_error:
            turn.trace.add("refuse_path", path=intent.target, error=intent.path_error)
            yield TextEvent(intent.path_error)
            return
        note = self.drop_superseded(turn.user_id)
        if note:
            yield TextEvent(note + "\n")
        plan = await self._planner.plan(turn, intent)
        if not plan.ok:
            yield TextEvent(plan.message)
            return
        yield TextEvent(plan.message)
        for n in plan.notes:
            yield TextEvent("\n" + n)
        if requires_confirmation(plan.items, self._settings):
            pending = PendingAction(
                id=uuid.uuid4().hex[:6],
                actions=[i.action for i in plan.items],
                summary=plan.summary,
                preview=plan.preview,
                origin_text=turn.text,
                created_at=self._store.now(),
                expires_at=self._store.now() + self._settings.pending_ttl_s,
                validate_paths=list(plan.validate_paths),
            )
            self._store.set_pending(turn.user_id, pending)
            minutes = max(1, self._settings.pending_ttl_s // 60)
            yield TextEvent("\n\n" + format_confirmation(pending, minutes=minutes))
            turn.trace.add("request_confirmation", summary=plan.summary, n_actions=len(plan.items))
            return
        report = ExecutionReport(emitted=[], landed={}, validated={})
        sent_text = True
        first_action = True
        async for ev in self._executor.run(turn, plan.items, plan.validate_paths, authorization="not_required", report=report):
            from hyperion.events import ActionEvent as _AE

            if isinstance(ev, _AE) and first_action and sent_text:
                yield TextEvent("\n\n")
                first_action = False
            if isinstance(ev, _AE):
                first_action = False
            yield ev
        turn.trace.add("act_done", n_actions=len(report.emitted))

    async def handle_confirmation(self, turn: TurnContext, decision: Literal["yes", "no"]) -> AsyncIterator[Event]:
        pending = self._store.peek_pending(turn.user_id)
        if pending is None:
            yield TextEvent(MSG.NO_PENDING)
            return
        if decision == "no":
            self._store.clear_pending(turn.user_id, declined_summary=pending.summary)
            yield TextEvent(MSG.CANCELLED.format(summary=pending.summary))
            turn.trace.add("resolve_confirmation", decision="no")
            return
        # yes
        pending = self._store.take_pending(turn.user_id)
        assert pending is not None
        session = self._store.get(turn.user_id)
        for a in pending.actions:
            if a.action in ("edit_file", "delete_file"):
                path = a.path
                ref = session.files.get(path)
                r = await self._gateway.read(path)
                if r.status == "not_found":
                    yield TextEvent(MSG.GONE.format(path=path))
                    turn.trace.add("resolve_confirmation", decision="yes", cancelled="gone", path=path)
                    return
                if r.status == "ok" and ref is not None and ref.sha and isinstance(r.content, str) and sha12(r.content) != ref.sha:
                    yield TextEvent(MSG.STALE.format(path=path))
                    turn.trace.add("resolve_confirmation", decision="yes", cancelled="stale", path=path)
                    return
                # other statuses continue silently
        items = [
            PlannedItem(a, reason=("delete" if a.action in ("delete_file", "delete_folder") else ("edit" if a.action == "edit_file" else None)), patch_edit=(a.action == "edit_file"))
            for a in pending.actions
        ]
        yield TextEvent(MSG.CONFIRMED_LEAD.format(summary=pending.summary))
        report = ExecutionReport(emitted=[], landed={}, validated={})
        async for ev in self._executor.run(turn, items, pending.validate_paths, authorization="confirmed", report=report):
            yield ev
        turn.trace.add("resolve_confirmation", decision="yes", n_actions=len(report.emitted))

    async def handle_validate(self, turn: TurnContext, intent: ActIntent) -> AsyncIterator[Event]:
        if intent.target is None:
            yield TextEvent(MSG.NEED_TARGET)
            return
        r = await self._gateway.read(intent.target)
        if r.status == "not_found":
            yield TextEvent(MSG.NOT_FOUND.format(path=intent.target))
            return
        if r.status == "ambiguous":
            yield TextEvent(MSG.AMBIGUOUS.format(path=intent.target, matches=", ".join(r.matches)))
            return
        if r.status == "unreachable":
            yield TextEvent(MSG.IDE_UNREACHABLE_READ.format(path=intent.target))
            return
        if r.status != "ok":
            yield TextEvent(r.message)
            return
        content = r.content or ""
        actual = r.path or intent.target
        from hyperion.dsl.detect import detect_kind_from_text as _k

        k = _k(content)
        kind = k if k in ("native", "device") else "other"
        self._store.note_file(turn.user_id, actual, kind, "validate", content)
        col = await self._executor.collect(turn, actual, content)
        if col.valid:
            if col.remote_state == "ok":
                n = len(col.warnings)
                warn = f" (with {n} warning(s))" if n else ""
                out = MSG.VALID_IDE.format(path=actual, warn=warn)
            else:
                out = MSG.VALID_LOCAL.format(path=actual)
            if col.warnings:
                out += "\nWarnings: " + "; ".join(w.format() for w in col.warnings[:3])
            if col.remote_state not in ("ok", "not_found"):
                out += MSG.VALIDATOR_UNREACHABLE
            yield TextEvent(out)
        else:
            errs = col.errors
            n = len(errs)
            lines = "\n".join("- " + e.format() for e in errs[:6])
            extra = ""
            if n > 6:
                extra = f"\n- … and {n - 6} more"
            yield TextEvent(MSG.INVALID_HEADER.format(path=actual, n=n) + "\n" + lines + extra + MSG.OFFER_FIX)

    async def handle_read(self, turn: TurnContext, intent: ActIntent) -> AsyncIterator[Event]:
        if intent.target is None:
            yield TextEvent(MSG.NEED_TARGET)
            return
        r = await self._gateway.read(intent.target)
        if r.status == "not_found":
            yield TextEvent(MSG.NOT_FOUND.format(path=intent.target))
            return
        if r.status == "ambiguous":
            yield TextEvent(MSG.AMBIGUOUS.format(path=intent.target, matches=", ".join(r.matches)))
            return
        if r.status == "unreachable":
            yield TextEvent(MSG.IDE_UNREACHABLE_READ.format(path=intent.target))
            return
        if r.status != "ok":
            yield TextEvent(r.message)
            return
        content = r.content or ""
        actual = r.path or intent.target
        from hyperion.dsl.detect import detect_kind_from_text as _k

        k = _k(content)
        kind = k if k in ("native", "device") else "other"
        self._store.note_file(turn.user_id, actual, kind, "read", content)
        if intent.verb == "read":
            n = len(content.splitlines())
            yield TextEvent(f"`{actual}` ({n} lines):\n```\n{_clip_block(content, 1500)}\n```")
            return
        # explain_file
        if kind == "other":
            yield TextEvent(MSG.NOT_A_PROFILE.format(path=actual) + f"\n{content[:600]}")
            return
        facts = summarize_profile(content)
        issues = errors_only(check_profile(content))[:3]
        clipped = content[:6000]
        yielded_any = False
        try:
            async for part in self._llm.stream(explain_messages(actual, clipped, facts, issues), name="explain_file", turn=turn):
                yielded_any = True
                yield TextEvent(part)
        except (LLMUnavailable, LLMBadJson):
            if yielded_any:
                return
            if issues:
                probs = " Problems: " + "; ".join(i.format() for i in issues)
            else:
                probs = " No problems found."
            yield TextEvent(facts + probs + MSG.DEGRADED_EXPLAIN)
        except Exception:
            if yielded_any:
                return
            if issues:
                probs = " Problems: " + "; ".join(i.format() for i in issues)
            else:
                probs = " No problems found."
            yield TextEvent(facts + probs + MSG.DEGRADED_EXPLAIN)

    def pending_reminder(self, user_id: str) -> str:
        pending = self._store.peek_pending(user_id)
        if pending is None:
            return ""
        return MSG.REMINDER.format(summary=pending.summary)

    def drop_superseded(self, user_id: str) -> str:
        pending = self._store.peek_pending(user_id)
        if pending is None:
            return ""
        # clear WITHOUT recording a decline
        session = self._store.peek(user_id)
        if session is not None:
            session.pending = None
        return MSG.SUPERSEDED.format(summary=pending.summary)
