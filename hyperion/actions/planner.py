"""Turn an ActIntent into a reviewable Plan without emitting anything (DP-ACTIONS §5.6–§5.10)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from hyperion.actions.confirm import Decision  # noqa: F401 (re-export convenience)
from hyperion.actions.gate import PlannedItem, classify
from hyperion.actions.messages import MSG
from hyperion.actions.preview import diff_preview, head_preview
from hyperion.actions.prompts import patch_messages, repair_messages, slots_messages
from hyperion.config import Settings
from hyperion.context import TurnContext
from hyperion.dsl.check import check_profile
from hyperion.dsl.detect import detect_kind_from_request, detect_kind_from_text, looks_like_profile_request  # noqa: F401
from hyperion.dsl.patch import PatchError, PatchOp, apply_ops, deterministic_ops_from_request, ops_from_json
from hyperion.dsl.repair import deterministic_repair
from hyperion.dsl.render import default_filename, render_profile
from hyperion.dsl.slots import extract_slots, merge_llm_slots
from hyperion.dsl.summary import summarize_profile
from hyperion.ide.actions import ActionError, make_action, validate_batch
from hyperion.ide.gateway import IdeGateway
from hyperion.ide.paths import extension, parent_dirs
from hyperion.issues import errors_only
from hyperion.llm.base import LLMBadJson, LLMLike, LLMUnavailable
from hyperion.memory.store import SessionStore

from hyperion.actions.intents import ActIntent

EDIT_MAX_CHARS: int = 6000

CONTENT_RE = re.compile(
    r"""(?:with\s+(?:the\s+)?(?:content|contents|text)\s*[:\-]?\s*|containing\s*[:\-]?\s*|that\s+(?:says|contains)\s*[:\-]?\s*|saying\s*[:\-]?\s*|content(?:s)?\s*[:\-]\s*)(?P<q>["'“`]?)(?P<c>.+?)(?P=q)\s*$""",
    re.I | re.S,
)


def extract_plain_content(text: str) -> str | None:
    m = CONTENT_RE.search(text)
    if not m:
        return None
    c = m.group("c").strip()
    return c if c else None


def _describe_op(o: PatchOp) -> str:
    if o.op == "set":
        return f"set `{o.path}` to {o.value!r}"
    if o.op == "delete":
        return f"delete `{o.path}`"
    return "replace one piece of text"


@dataclass
class Plan:
    ok: bool
    message: str  # ok=False: the sentence to show (an error or a question). ok=True: the lead-in narration.
    items: list[PlannedItem]
    summary: str  # one sentence for PendingAction.summary
    preview: str  # diff / head preview; '' when none
    notes: list[str]  # assumptions and degraded-mode sentences, shown after the lead-in
    validate_paths: list[str]
    clarify: bool = False  # True when message is a question the user must answer


class ActionPlanner:
    def __init__(self, settings: Settings, llm: LLMLike, gateway: IdeGateway, store: SessionStore) -> None:
        self._settings = settings
        self._llm = llm
        self._gateway = gateway
        self._store = store

    async def _exists_info(self, turn: TurnContext, path: str) -> tuple[bool | None, str, tuple[str, ...]]:
        r = await self._gateway.read(path)
        if r.status == "ok":
            return (True, r.path or path, ())
        if r.status == "ambiguous":
            return (True, path, r.matches)
        if r.status == "not_found":
            return (False, path, ())
        session = self._store.get(turn.user_id)
        ref = session.files.get(path)
        if ref is not None and not ref.deleted:
            return (True, path, ())
        return (None, path, ())

    def _kind_of(self, content: str) -> str:
        k = detect_kind_from_text(content)
        if k in ("native", "device"):
            return k
        return "other"

    async def plan(self, turn: TurnContext, intent: ActIntent) -> Plan:
        turn.trace.add("plan_start", verb=intent.verb, target=intent.target)
        try:
            if intent.verb == "create":
                if intent.is_profile:
                    plan = await self._plan_create_profile(turn, intent)
                else:
                    plan = await self._plan_create_plain(turn, intent)
            elif intent.verb == "create_folder":
                plan = await self._plan_create_folder(turn, intent)
            elif intent.verb == "edit":
                plan = await self._plan_edit(turn, intent)
            elif intent.verb == "fix":
                plan = await self._plan_fix(turn, intent)
            elif intent.verb == "delete":
                plan = await self._plan_delete(turn, intent)
            elif intent.verb == "delete_folder":
                plan = await self._plan_delete_folder(turn, intent)
            else:
                raise ValueError(f"ActionPlanner cannot plan verb {intent.verb!r}")
        except ValueError:
            raise
        except Exception as e:
            plan = Plan(ok=False, message=str(e) or "I could not prepare that request.", items=[], summary="", preview="", notes=[], validate_paths=[])
        reasons = [i.reason or "none" for i in plan.items]
        turn.trace.add("plan", ok=plan.ok, n_actions=len(plan.items), reasons=reasons)
        return plan

    def _parent_items(self, turn: TurnContext, path: str) -> list[PlannedItem]:
        if not self._settings.auto_create_parents:
            return []
        session = self._store.get(turn.user_id)
        out: list[PlannedItem] = []
        for d in parent_dirs(path):
            ref = session.files.get(d)
            if ref is not None and not ref.deleted and ref.kind == "folder":
                continue
            try:
                out.append(PlannedItem(make_action("create_folder", d), None))
            except ActionError:
                continue
        return out

    def _check_batch(self, items: list[PlannedItem]) -> Plan | None:
        issues = validate_batch([i.action for i in items], max_actions=self._settings.max_actions_per_turn)
        if errors_only(issues) or issues:
            # validate_batch only returns errors; any issue fails
            if issues:
                return Plan(ok=False, message=f"I cannot do that safely: {issues[0].message}", items=[], summary="", preview="", notes=[], validate_paths=[])
        return None

    async def _plan_create_profile(self, turn: TurnContext, intent: ActIntent) -> Plan:
        notes: list[str] = []
        kind = detect_kind_from_request(turn.text)
        ext = extract_slots(turn.text, kind)
        need_llm = (("image_uri" not in ext.from_user and "image" not in ext.from_user) or len(turn.text.split()) > 30)
        if need_llm:
            try:
                data = await self._llm.chat_json(slots_messages(kind, turn.text), name="slots", turn=turn, max_tokens=200)
                ext = merge_llm_slots(ext, data)
                turn.trace.add("slots", used_llm=True, from_user=sorted(ext.from_user))
            except (LLMUnavailable, LLMBadJson):
                notes.append(MSG.DEGRADED_SLOTS)
                turn.trace.add("slots", used_llm=False, from_user=sorted(ext.from_user))
        else:
            turn.trace.add("slots", used_llm=False, from_user=sorted(ext.from_user))
        text = render_profile(ext.slots)
        issues = check_profile(text, kind_hint=kind)
        if errors_only(issues):
            r = deterministic_repair(text, issues, kind_hint=kind)
            if r is not None:
                text = r.text
                notes.extend(list(r.applied))
            issues = check_profile(text, kind_hint=kind)
            if errors_only(issues):
                return Plan(ok=False, message=MSG.INTERNAL_RENDER, items=[], summary="", preview="", notes=notes, validate_paths=[])
        path = intent.target or default_filename(ext.slots)
        existed, actual, matches = await self._exists_info(turn, path)
        if matches:
            return Plan(ok=False, clarify=True, message=MSG.AMBIGUOUS.format(path=path, matches=", ".join(matches)), items=[], summary="", preview="", notes=notes, validate_paths=[])
        path = actual
        if existed:
            if intent.target is None:
                slug = ext.slots.name  # type: ignore[attr-defined]
                found: str | None = None
                for cand in [f"{slug}.yaml"] + [f"{slug}-{i}.yaml" for i in range(2, 10)]:
                    e2, _, m2 = await self._exists_info(turn, cand)
                    if m2:
                        return Plan(ok=False, clarify=True, message=MSG.AMBIGUOUS.format(path=cand, matches=", ".join(m2)), items=[], summary="", preview="", notes=notes, validate_paths=[])
                    if e2 is False or e2 is None:
                        found = cand
                        break
                if found is None:
                    return Plan(ok=False, message=MSG.NO_FREE_NAME, items=[], summary="", preview="", notes=notes, validate_paths=[])
                notes.append(MSG.USED_UNIQUE_NAME.format(path=found))
                path = found
                existed = False
        items = self._parent_items(turn, path)
        try:
            if existed:
                action = make_action("edit_file", path, text, max_content_chars=self._settings.max_content_chars)
                reason = classify(action, mode=self._settings.hitl_mode, existed=True, patch_edit=False)
            else:
                action = make_action("create_file", path, text, max_content_chars=self._settings.max_content_chars)
                reason = classify(action, mode=self._settings.hitl_mode, existed=False, patch_edit=False)
        except ActionError as e:
            return Plan(ok=False, message=str(e), items=[], summary="", preview="", notes=notes, validate_paths=[])
        items.append(PlannedItem(action, reason))
        bad = self._check_batch(items)
        if bad is not None:
            bad.notes = notes
            return bad
        summary = f"{'overwrite' if existed else 'create'} the file `{path}`"
        if existed:
            r2 = await self._gateway.read(path)
            old_content = r2.content if r2.status == "ok" and isinstance(r2.content, str) else ""
            preview = diff_preview(old_content, text)
        else:
            preview = head_preview(text, max_lines=10)
        if existed:
            message = f"I will replace the contents of `{path}`. {summarize_profile(text)}"
        else:
            message = f"I will create `{path}`. {summarize_profile(text)}"
        notes.extend(list(ext.notes))
        return Plan(ok=True, message=message, items=items, summary=summary, preview=preview, notes=notes, validate_paths=[path])

    async def _plan_create_plain(self, turn: TurnContext, intent: ActIntent) -> Plan:
        notes: list[str] = []
        if intent.target is None:
            return Plan(ok=False, clarify=True, message=MSG.NEED_NAME, items=[], summary="", preview="", notes=[], validate_paths=[])
        path = intent.target
        content = extract_plain_content(turn.text)
        if content is None:
            content = ""
            notes.append(MSG.CREATED_EMPTY)
        existed, actual, matches = await self._exists_info(turn, path)
        if matches:
            return Plan(ok=False, clarify=True, message=MSG.AMBIGUOUS.format(path=path, matches=", ".join(matches)), items=[], summary="", preview="", notes=notes, validate_paths=[])
        path = actual
        items = self._parent_items(turn, path)
        try:
            if existed:
                action = make_action("edit_file", path, content, max_content_chars=self._settings.max_content_chars)
                reason = classify(action, mode=self._settings.hitl_mode, existed=True, patch_edit=False)
            else:
                action = make_action("create_file", path, content, max_content_chars=self._settings.max_content_chars)
                reason = classify(action, mode=self._settings.hitl_mode, existed=False, patch_edit=False)
        except ActionError as e:
            return Plan(ok=False, message=str(e), items=[], summary="", preview="", notes=notes, validate_paths=[])
        items.append(PlannedItem(action, reason))
        bad = self._check_batch(items)
        if bad is not None:
            bad.notes = notes
            return bad
        summary = f"{'overwrite' if existed else 'create'} the file `{path}`"
        if existed:
            r2 = await self._gateway.read(path)
            old_content = r2.content if r2.status == "ok" and isinstance(r2.content, str) else ""
            preview = diff_preview(old_content, content)
        else:
            preview = head_preview(content, max_lines=10)
        if existed:
            message = f"I will replace the contents of `{path}`."
        else:
            message = f"I will create `{path}`."
        vpaths = [path] if extension(path) in (".yaml", ".yml") else []
        return Plan(ok=True, message=message, items=items, summary=summary, preview=preview, notes=notes, validate_paths=vpaths)

    async def _plan_create_folder(self, turn: TurnContext, intent: ActIntent) -> Plan:
        if intent.target is None:
            return Plan(ok=False, clarify=True, message=MSG.NEED_FOLDER_NAME, items=[], summary="", preview="", notes=[], validate_paths=[])
        p = intent.target
        session = self._store.get(turn.user_id)
        ref = session.files.get(p)
        if ref is not None and not ref.deleted and ref.kind == "folder":
            return Plan(ok=False, message=MSG.FOLDER_EXISTS.format(path=p), items=[], summary="", preview="", notes=[], validate_paths=[])
        try:
            action = make_action("create_folder", p)
        except ActionError as e:
            return Plan(ok=False, message=str(e), items=[], summary="", preview="", notes=[], validate_paths=[])
        items = [PlannedItem(action, None)]
        bad = self._check_batch(items)
        if bad is not None:
            return bad
        return Plan(ok=True, message=f"I will create the folder `{p}`.", items=items, summary=f"create the folder `{p}`", preview="", notes=[], validate_paths=[])

    async def _plan_edit(self, turn: TurnContext, intent: ActIntent) -> Plan:
        notes: list[str] = []
        if intent.target is None:
            return Plan(ok=False, clarify=True, message=MSG.NEED_TARGET, items=[], summary="", preview="", notes=[], validate_paths=[])
        r = await self._gateway.read(intent.target)
        if r.status == "not_found":
            return Plan(ok=False, message=MSG.NOT_FOUND.format(path=intent.target), items=[], summary="", preview="", notes=[], validate_paths=[])
        if r.status == "ambiguous":
            return Plan(ok=False, clarify=True, message=MSG.AMBIGUOUS.format(path=intent.target, matches=", ".join(r.matches)), items=[], summary="", preview="", notes=[], validate_paths=[])
        if r.status == "unreachable":
            return Plan(ok=False, message=MSG.IDE_UNREACHABLE_READ.format(path=intent.target), items=[], summary="", preview="", notes=[], validate_paths=[])
        if r.status != "ok":
            return Plan(ok=False, message=r.message, items=[], summary="", preview="", notes=[], validate_paths=[])
        content = r.content or ""
        actual = r.path or intent.target
        if len(content) > EDIT_MAX_CHARS:
            return Plan(ok=False, message=MSG.TOO_LARGE.format(path=actual, n=len(content), limit=EDIT_MAX_CHARS), items=[], summary="", preview="", notes=[], validate_paths=[])
        kind = self._kind_of(content)
        self._store.note_file(turn.user_id, actual, kind, "read", content)
        ops = deterministic_ops_from_request(turn.text, content)
        if not ops:
            try:
                data = await self._llm.chat_json(patch_messages(actual, content, turn.text), name="patch_ops", turn=turn, required_keys=("ops",), max_tokens=300)
                try:
                    ops = ops_from_json(data["ops"])
                except PatchError as e:
                    # one retry
                    retry_msgs = patch_messages(actual, content, turn.text) + [
                        {"role": "user", "content": f"Your operations were rejected: {e}. Reply again with a corrected JSON object."}
                    ]
                    try:
                        data2 = await self._llm.chat_json(retry_msgs, name="patch_ops", turn=turn, required_keys=("ops",), max_tokens=300)
                        ops = ops_from_json(data2["ops"])
                    except (PatchError, LLMUnavailable, LLMBadJson):
                        return Plan(ok=False, message=MSG.CANNOT_EDIT, items=[], summary="", preview="", notes=notes, validate_paths=[])
            except (LLMUnavailable, LLMBadJson):
                notes.append(MSG.DEGRADED_EDIT)
                return Plan(ok=False, message=MSG.CANNOT_EDIT + " " + MSG.DEGRADED_EDIT, items=[], summary="", preview="", notes=notes, validate_paths=[])
            if not ops:
                return Plan(ok=False, message=MSG.CANNOT_EDIT, items=[], summary="", preview="", notes=notes, validate_paths=[])
        try:
            new = apply_ops(content, ops)
        except PatchError as e:
            return Plan(ok=False, message=str(e), items=[], summary="", preview="", notes=notes, validate_paths=[])
        if new == content:
            return Plan(ok=False, message=MSG.NO_CHANGE.format(path=actual), items=[], summary="", preview="", notes=notes, validate_paths=[])
        if kind in ("native", "device"):
            before = len(errors_only(check_profile(content)))
            after_issues = check_profile(new)
            if len(errors_only(after_issues)) > before:
                r2 = deterministic_repair(new, after_issues, kind_hint=kind)
                if r2 is not None:
                    new = r2.text
                    notes.extend(list(r2.applied))
                after_issues = check_profile(new)
                if len(errors_only(after_issues)) > before:
                    notes.append(MSG.CHECKER_WARNING.format(issues="; ".join(i.format() for i in errors_only(after_issues)[:3])))
        try:
            action = make_action("edit_file", actual, new, max_content_chars=self._settings.max_content_chars)
        except ActionError as e:
            return Plan(ok=False, message=str(e), items=[], summary="", preview="", notes=notes, validate_paths=[])
        reason = classify(action, mode=self._settings.hitl_mode, existed=True, patch_edit=True)
        items = [PlannedItem(action, reason, patch_edit=True)]
        bad = self._check_batch(items)
        if bad is not None:
            bad.notes = notes
            return bad
        summary = f"edit `{actual}`: " + "; ".join(_describe_op(o) for o in ops)
        preview = diff_preview(content, new)
        vpaths = [actual] if extension(actual) in (".yaml", ".yml") else []
        return Plan(ok=True, message=f"I will change `{actual}`.", items=items, summary=summary, preview=preview, notes=notes, validate_paths=vpaths)

    async def _plan_fix(self, turn: TurnContext, intent: ActIntent) -> Plan:
        notes: list[str] = []
        if intent.target is None:
            # try sole-profile fallback? resolve_intent already handles; planner asks
            return Plan(ok=False, clarify=True, message=MSG.NEED_TARGET, items=[], summary="", preview="", notes=[], validate_paths=[])
        r = await self._gateway.read(intent.target)
        if r.status == "not_found":
            return Plan(ok=False, message=MSG.NOT_FOUND.format(path=intent.target), items=[], summary="", preview="", notes=[], validate_paths=[])
        if r.status == "ambiguous":
            return Plan(ok=False, clarify=True, message=MSG.AMBIGUOUS.format(path=intent.target, matches=", ".join(r.matches)), items=[], summary="", preview="", notes=[], validate_paths=[])
        if r.status == "unreachable":
            return Plan(ok=False, message=MSG.IDE_UNREACHABLE_READ.format(path=intent.target), items=[], summary="", preview="", notes=[], validate_paths=[])
        if r.status != "ok":
            return Plan(ok=False, message=r.message, items=[], summary="", preview="", notes=[], validate_paths=[])
        content = r.content or ""
        actual = r.path or intent.target
        if len(content) > EDIT_MAX_CHARS:
            return Plan(ok=False, message=MSG.TOO_LARGE.format(path=actual, n=len(content), limit=EDIT_MAX_CHARS), items=[], summary="", preview="", notes=[], validate_paths=[])
        kind = self._kind_of(content)
        self._store.note_file(turn.user_id, actual, kind, "read", content)
        local_issues = check_profile(content)
        v = await self._gateway.validate(actual)
        issues = list(local_issues)
        if v.status == "ok":
            seen = {i.message.lower() for i in issues}
            for ri in list(v.errors) + list(v.warnings):
                if ri.message.lower() not in seen:
                    issues.append(ri)
        if not errors_only(issues):
            return Plan(ok=False, message=MSG.NOTHING_TO_FIX.format(path=actual), items=[], summary="", preview="", notes=notes, validate_paths=[])
        rep = deterministic_repair(content, issues, kind_hint=kind)
        if rep is None:
            try:
                from hyperion.actions.prompts import repair_messages as _rm

                data = await self._llm.chat_json(_rm(actual, content, issues), name="repair_ops", turn=turn, required_keys=("ops",), max_tokens=300)
                try:
                    ops = ops_from_json(data["ops"])
                except PatchError:
                    retry_msgs = _rm(actual, content, issues) + [
                        {"role": "user", "content": "Your operations were rejected. Reply again with a corrected JSON object."}
                    ]
                    try:
                        data2 = await self._llm.chat_json(retry_msgs, name="repair_ops", turn=turn, required_keys=("ops",), max_tokens=300)
                        ops = ops_from_json(data2["ops"])
                    except (PatchError, LLMUnavailable, LLMBadJson):
                        return Plan(ok=False, message=MSG.CANNOT_FIX, items=[], summary="", preview="", notes=notes, validate_paths=[])
                try:
                    new = apply_ops(content, ops)
                except PatchError as e:
                    return Plan(ok=False, message=str(e), items=[], summary="", preview="", notes=notes, validate_paths=[])
                applied: list[str] = ["Applied the model's suggested correction."]
            except (LLMUnavailable, LLMBadJson):
                notes.append(MSG.DEGRADED_EDIT)
                return Plan(ok=False, message=MSG.CANNOT_FIX + " " + MSG.DEGRADED_EDIT, items=[], summary="", preview="", notes=notes, validate_paths=[])
            if not ops:
                return Plan(ok=False, message=MSG.CANNOT_FIX, items=[], summary="", preview="", notes=notes, validate_paths=[])
        else:
            new = rep.text
            applied = list(rep.applied)
            notes.extend(applied)
        if new == content:
            return Plan(ok=False, message=MSG.CANNOT_FIX, items=[], summary="", preview="", notes=notes, validate_paths=[])
        try:
            action = make_action("edit_file", actual, new, max_content_chars=self._settings.max_content_chars)
        except ActionError as e:
            return Plan(ok=False, message=str(e), items=[], summary="", preview="", notes=notes, validate_paths=[])
        reason = classify(action, mode=self._settings.hitl_mode, existed=True, patch_edit=True)
        items = [PlannedItem(action, reason, patch_edit=True)]
        bad = self._check_batch(items)
        if bad is not None:
            bad.notes = notes
            return bad
        summary = f"fix `{actual}`: " + "; ".join(applied or ["apply the suggested corrections"])
        preview = diff_preview(content, new)
        vpaths = [actual] if extension(actual) in (".yaml", ".yml") else []
        return Plan(ok=True, message=f"I will change `{actual}`.", items=items, summary=summary, preview=preview, notes=notes, validate_paths=vpaths)

    async def _plan_delete(self, turn: TurnContext, intent: ActIntent) -> Plan:
        if intent.target is None:
            return Plan(ok=False, clarify=True, message=MSG.NEED_TARGET, items=[], summary="", preview="", notes=[], validate_paths=[])
        target = intent.target
        r = await self._gateway.read(target)
        content: str | None = None
        actual = target
        notes: list[str] = []
        if r.status == "not_found":
            return Plan(ok=False, message=MSG.NOT_FOUND.format(path=target), items=[], summary="", preview="", notes=[], validate_paths=[])
        if r.status == "ambiguous":
            return Plan(ok=False, clarify=True, message=MSG.AMBIGUOUS.format(path=target, matches=", ".join(r.matches)), items=[], summary="", preview="", notes=[], validate_paths=[])
        if r.status == "error":
            return Plan(ok=False, message=r.message, items=[], summary="", preview="", notes=[], validate_paths=[])
        if r.status == "unreachable":
            session = self._store.get(turn.user_id)
            ref = session.files.get(target)
            if ref is not None and not ref.deleted:
                notes.append(MSG.CANNOT_VERIFY)
            else:
                return Plan(ok=False, message=MSG.IDE_UNREACHABLE_READ.format(path=target), items=[], summary="", preview="", notes=[], validate_paths=[])
        else:
            content = r.content or ""
            actual = r.path or target
            kind = self._kind_of(content)
            self._store.note_file(turn.user_id, actual, kind, "read", content)
        try:
            action = make_action("delete_file", actual)
        except ActionError as e:
            return Plan(ok=False, message=str(e), items=[], summary="", preview="", notes=notes, validate_paths=[])
        reason = classify(action, mode=self._settings.hitl_mode, existed=True, patch_edit=False)
        assert reason == "delete"
        items = [PlannedItem(action, reason)]
        bad = self._check_batch(items)
        if bad is not None:
            bad.notes = notes
            return bad
        summary = f"delete the file `{actual}`"
        if content is not None:
            preview = head_preview(content, max_lines=6) + "\nThis cannot be undone from here."
        else:
            preview = "\nThis cannot be undone from here.".lstrip("\n")
            # spec: omit head part when unknown -> just the sentence? keep exact:
            preview = "This cannot be undone from here."
            # Actually for unknown content the preview should still mention undo; use head-less:
        return Plan(ok=True, message=f"I will delete `{actual}`.", items=items, summary=summary, preview=preview, notes=notes, validate_paths=[])

    async def _plan_delete_folder(self, turn: TurnContext, intent: ActIntent) -> Plan:
        if intent.target is None:
            return Plan(ok=False, clarify=True, message=MSG.NEED_TARGET, items=[], summary="", preview="", notes=[], validate_paths=[])
        p = intent.target
        try:
            action = make_action("delete_folder", p)
        except ActionError as e:
            return Plan(ok=False, message=str(e), items=[], summary="", preview="", notes=[], validate_paths=[])
        reason = classify(action, mode=self._settings.hitl_mode, existed=None, patch_edit=False)
        items = [PlannedItem(action, reason)]
        bad = self._check_batch(items)
        if bad is not None:
            return bad
        session = self._store.get(turn.user_id)
        known = [f.path for f in session.files.values() if not f.deleted and f.path.startswith(p + "/")]
        if known:
            shown = ", ".join(known[:5])
            if len(known) > 5:
                shown += f" and {len(known) - 5} more"
            preview = "Files I know about inside: " + shown
        else:
            preview = MSG.FOLDER_UNKNOWN_CONTENT
        return Plan(ok=True, message=f"I will delete `{p}`.", items=items, summary=f"delete the folder `{p}` and everything in it", preview=preview, notes=[], validate_paths=[])
