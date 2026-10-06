"""Emit → await landing → validate → repair (DP-ACTIONS §5.11–§5.12)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Literal, Sequence

from hyperion.actions.gate import PlannedItem, UnauthorizedAction
from hyperion.actions.messages import MSG
from hyperion.actions.prompts import repair_messages
from hyperion.config import Settings
from hyperion.context import TurnContext
from hyperion.dsl.check import check_profile
from hyperion.dsl.patch import PatchError, apply_ops, ops_from_json
from hyperion.dsl.repair import deterministic_repair
from hyperion.events import ActionEvent, Event, TextEvent
from hyperion.ide.actions import make_action
from hyperion.ide.gateway import IdeGateway
from hyperion.ide.paths import extension
from hyperion.issues import Issue, errors_only
from hyperion.llm.base import LLMBadJson, LLMLike, LLMUnavailable
from hyperion.memory.store import SessionStore


@dataclass
class ExecutionReport:
    emitted: list[dict[str, str]]  # {'action','path'} per emitted action, in order
    landed: dict[str, bool | None]  # path -> True / False / None (not checked: folders)
    validated: dict[str, bool | None]  # path -> True valid / False invalid / None not validated
    repair_rounds: int = 0
    errors_left: list[str] = field(default_factory=list)
    stopped_early: bool = False


@dataclass
class Collected:
    errors: list[Issue]
    warnings: list[Issue]
    remote_state: str  # 'ok' | 'not_found' | 'ambiguous' | 'unreachable' | 'error'
    valid: bool  # no errors (authoritative remote verdict when remote_state == 'ok')


class Executor:
    def __init__(self, settings: Settings, llm: LLMLike, gateway: IdeGateway, store: SessionStore) -> None:
        self._settings = settings
        self._llm = llm
        self._gateway = gateway
        self._store = store

    def _kind_of(self, content: str) -> str:
        from hyperion.dsl.detect import detect_kind_from_text

        k = detect_kind_from_text(content)
        return k if k in ("native", "device") else "other"

    async def collect(self, turn: TurnContext, path: str, content: str) -> Collected:
        local = check_profile(content)
        v = await self._gateway.validate(path)
        if v.ok:
            errors = list(v.errors)
            warnings = list(v.warnings)
            valid = (v.valid is not False) and not errors
            if v.valid is False and not errors:
                errors = [Issue(severity="error", code="remote_invalid", message="The IDE validator reports this file as invalid.", source="remote")]
                valid = False
            if not valid:
                seen = {e.message.lower() for e in errors} | {w.message.lower() for w in warnings}
                for li in local:
                    if li.message.lower() not in seen:
                        if li.severity == "error":
                            errors.append(li)
                        else:
                            warnings.append(li)
            turn.trace.add("validate", path=path, remote_state="ok", n_errors=len(errors), n_warnings=len(warnings), valid=valid)
            return Collected(errors=errors, warnings=warnings, remote_state="ok", valid=valid)
        errors = errors_only(local)
        warnings = [i for i in local if i.severity == "warning"]
        valid = not errors
        turn.trace.add("validate", path=path, remote_state=v.status, n_errors=len(errors), n_warnings=len(warnings), valid=valid)
        return Collected(errors=errors, warnings=warnings, remote_state=v.status, valid=valid)

    async def _repair(self, turn: TurnContext, path: str, content: str, issues: list[Issue]) -> tuple[str | None, list[str]]:
        from hyperion.dsl.detect import detect_kind_from_text

        kind = detect_kind_from_text(content)
        hint = kind if kind in ("native", "device") else None
        r = deterministic_repair(content, issues, kind_hint=hint)
        if r is not None and r.text != content:
            return (r.text, list(r.applied))
        # Remote issues carry code "remote" (no actionable code); retry with the
        # local checker's issues (same findings, actionable codes) merged in.
        try:
            local = check_profile(content)
            if local:
                local_msgs = {i.message.lower() for i in local}
                merged = list(local) + [i for i in issues if i.message.lower() not in local_msgs]
                r2 = deterministic_repair(content, merged, kind_hint=hint)
                if r2 is not None and r2.text != content:
                    return (r2.text, list(r2.applied))
        except Exception:
            pass
        try:
            data = await self._llm.chat_json(repair_messages(path, content, issues), name="repair_ops", turn=turn, required_keys=("ops",), max_tokens=300)
            ops = ops_from_json(data["ops"])
            new = apply_ops(content, ops)
            return (new, ["Applied the model's suggested correction."])
        except (PatchError, LLMUnavailable, LLMBadJson):
            return (None, [])

    async def run(
        self,
        turn: TurnContext,
        items: Sequence[PlannedItem],
        validate_paths: Sequence[str],
        *,
        authorization: Literal["not_required", "confirmed"],
        report: ExecutionReport,
    ) -> AsyncIterator[Event]:
        if self._settings.feature_hitl and any(i.reason is not None for i in items) and authorization != "confirmed":
            raise UnauthorizedAction("An action requires confirmation before it can be emitted.")
        known: dict[str, str] = {}
        confirmed = authorization == "confirmed"
        for item in items:
            if turn.expired():
                done = ", ".join(f"{e['action']} {e['path']}" for e in report.emitted) or "nothing"
                yield TextEvent(MSG.TIME_OUT.format(done=done))
                report.stopped_early = True
                break
            payload = item.action.to_payload()
            turn.trace.add(
                "action_emitted",
                action=item.action.action,
                path=item.action.path,
                required=item.reason is not None,
                confirmed=confirmed,
                reason=item.reason or "none",
            )
            report.emitted.append({"action": item.action.action, "path": item.action.path})
            yield ActionEvent(payload)
            # Landing + session
            path = item.action.path
            act = item.action.action
            if act in ("create_file", "edit_file"):
                content = item.action.content or ""
                lr = await self._gateway.await_landing(path, expect_content=content)
                report.landed[path] = lr.landed
                turn.trace.add("landing", path=path, landed=lr.landed, waited_s=lr.waited_s)
                if not lr.landed:
                    if lr.result.status == "unreachable":
                        yield TextEvent(MSG.IDE_UNREACHABLE_AFTER.format(path=path))
                    else:
                        yield TextEvent(MSG.LANDING_TIMEOUT.format(path=path))
                    report.validated[path] = None
                else:
                    op = "create" if act == "create_file" else "edit"
                    self._store.note_file(turn.user_id, path, self._kind_of(content), op, content)
                    known[path] = content
            elif act == "delete_file":
                lr = await self._gateway.await_landing(path, expect_absent=True)
                report.landed[path] = lr.landed
                turn.trace.add("landing", path=path, landed=lr.landed, waited_s=lr.waited_s)
                if lr.landed:
                    self._store.forget_file(turn.user_id, path)
                else:
                    if lr.result.status == "unreachable":
                        yield TextEvent(MSG.IDE_UNREACHABLE_AFTER.format(path=path))
                    else:
                        yield TextEvent(MSG.LANDING_TIMEOUT.format(path=path))
                    report.validated[path] = None
                    yield TextEvent(MSG.DELETE_UNCONFIRMED.format(path=path))
            elif act == "delete_folder":
                lr = await self._gateway.await_landing(path, expect_absent=True)
                # folders: no landing check per spec -> landed None? But need forget + landing trace?
                # Spec §5.11 step 1.3: folders → no check, landed None. Step 1.4 delete: landed → forget.
                # For delete_folder, treat as landed check skipped: forget known files under prefix.
                report.landed[path] = None
                # best-effort: if folder gone or still there, forget session refs under it
                session = self._store.peek(turn.user_id)
                if session is not None:
                    for fp in [f for f in list(session.files.keys()) if f == path or f.startswith(path + "/")]:
                        self._store.forget_file(turn.user_id, fp)
                _ = lr
            else:  # create_folder
                report.landed[path] = None
                self._store.note_file(turn.user_id, path, "folder", "create", None)
        # Validation
        for path in validate_paths:
            if path not in known:
                continue
            if report.landed.get(path) is not True:
                continue
            if extension(path) not in (".yaml", ".yml"):
                continue
            content = known[path]
            round = 0
            while True:
                if turn.expired():
                    done = ", ".join(f"{e['action']} {e['path']}" for e in report.emitted) or "nothing"
                    yield TextEvent(MSG.TIME_OUT.format(done=done))
                    report.stopped_early = True
                    break
                col = await self.collect(turn, path, content)
                if col.valid:
                    report.validated[path] = True
                    if col.remote_state == "ok":
                        n = len(col.warnings)
                        warn = f" (with {n} warning(s))" if n else ""
                        yield TextEvent(MSG.VALID_IDE.format(path=path, warn=warn))
                    else:
                        yield TextEvent(MSG.VALID_LOCAL.format(path=path))
                    if col.remote_state not in ("ok", "not_found"):
                        yield TextEvent(MSG.VALIDATOR_UNREACHABLE)
                    break
                # not valid
                if not self._settings.feature_repair or round >= self._settings.repair_rounds or turn.expired():
                    report.validated[path] = False
                    report.errors_left = [i.format() for i in col.errors[:5]]
                    yield TextEvent(MSG.STILL_INVALID.format(path=path, issues="\n".join("- " + e for e in report.errors_left)))
                    break
                round += 1
                report.repair_rounds = max(report.repair_rounds, round)
                new, applied = await self._repair(turn, path, content, col.errors + col.warnings)
                if new is None or new == content:
                    report.validated[path] = False
                    # recompute errors_left from last collect
                    report.errors_left = [i.format() for i in col.errors[:5]]
                    yield TextEvent(MSG.STILL_INVALID.format(path=path, issues="\n".join("- " + e for e in report.errors_left)))
                    break
                turn.trace.add("repair", round=round, applied=" ".join(applied))
                yield TextEvent(MSG.REPAIRING.format(applied=" ".join(applied), n=round, max=self._settings.repair_rounds))
                # emit repair edit
                try:
                    repair_action = make_action("edit_file", path, new, max_content_chars=self._settings.max_content_chars)
                except Exception as e:
                    report.validated[path] = False
                    report.errors_left = [i.format() for i in col.errors[:5]]
                    yield TextEvent(MSG.STILL_INVALID.format(path=path, issues="\n".join("- " + e for e in report.errors_left)))
                    break
                turn.trace.add("action_emitted", action="edit_file", path=path, required=False, confirmed=confirmed, reason="repair")
                report.emitted.append({"action": "edit_file", "path": path})
                yield ActionEvent(repair_action.to_payload())
                lr = await self._gateway.await_landing(path, expect_content=new)
                report.landed[path] = lr.landed
                turn.trace.add("landing", path=path, landed=lr.landed, waited_s=lr.waited_s)
                if not lr.landed:
                    if lr.result.status == "unreachable":
                        yield TextEvent(MSG.IDE_UNREACHABLE_AFTER.format(path=path))
                    else:
                        yield TextEvent(MSG.LANDING_TIMEOUT.format(path=path))
                    report.validated[path] = None
                    break
                self._store.note_file(turn.user_id, path, self._kind_of(new), "edit", new)
                known[path] = new
                content = new
                continue
