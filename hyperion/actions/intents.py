"""Detect whether a message asks the agent to ACT on the workspace, and on what."""
from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from typing import Literal

from hyperion.dsl.detect import IMAGE_CUES, looks_like_profile_request
from hyperion.ide.paths import PathError, extension, normalize_path
from hyperion.memory.models import Session
from hyperion.memory.recall import pronoun_target

# Any action verb, anywhere in the text (not just at the head). A message that
# names an action mid-sentence ("... you to create ...") keeps the historical
# guard routing; only fully verbless profile requests fall through to create.
_ANY_ACT_VERB = re.compile(
    r"\b(?:create|generate|write|build|draft|produce|scaffold|prepare|deploy|launch|spin\s+up"
    r"|make|add|set\s+up|use|put|give\s+me|get\s+me"
    r"|change|set|update|modify|edit|rename|switch|bump|increase|decrease|raise|lower"
    r"|replace|adjust|tweak|append|remove|delete|configure|rewrite|erase|trash|destroy|drop"
    r"|validate|lint|verify|check|fix|repair|correct|resolve"
    r"|show|open|read|display|print|cat|view|see"
    r"|explain|describe|summariz?e|walk\s+me\s+through)\b",
    re.I,
)

ActVerb = Literal["create", "edit", "fix", "delete", "create_folder", "delete_folder", "validate", "read", "explain_file"]
TargetSource = Literal["explicit", "reference", "none"]


@dataclass(frozen=True)
class ActIntent:
    verb: ActVerb
    target: str | None  # normalised workspace path (explicit or resolved); None when absent
    target_source: TargetSource
    is_profile: bool  # True when the content to create/edit is a HyperAI application profile
    path_error: str | None = None  # a sentence safe to show the user when the explicit path is unusable (target is then None)


POLITE_PREFIX = re.compile(
    r"^(?:(?:please|pls|kindly|hey|hi|ok(?:ay)?|now|then|and|also|next|so)\b[ ,]*"
    r"|(?:can|could|would|will)\s+you\b[ ,]*"
    r"|i\s+(?:want|need|would\s+like)\s+(?:you\s+)?to\b[ ,]*"
    r"|i['’]d\s+like\s+(?:you\s+)?to\b[ ,]*"
    r"|let['’]?s\b[ ,]*"
    r"|go\s+ahead\s+and\b[ ,]*)+", re.I)
QUESTION_HEAD = re.compile(r"^(?:what|what['’]?s|whats|why|how|when|where|who|whom|which|does|do|did|is|are|was|were|should|shall|may|might|tell\s+me)\b", re.I)
_TOKEN = r"(?:[A-Za-z]:)?[\w.\-/\\]*[\w\-]\.(?:ya?ml|json|md|txt|toml|cfg|ini|conf|env|csv|xml|html|py|js|ts|sh)"
FILE_TOKEN = re.compile(r"(?<![\w@])(" + _TOKEN + r")(?![\w])", re.I)
FOLDER_AFTER = re.compile(r"\b(?:folder|directory|dir)\s+(?:named\s+|called\s+)?([\w][\w.\-/]*)", re.I)
FOLDER_BEFORE = re.compile(r"\b([\w][\w.\-/]*)\s+(?:folder|directory|dir)\b", re.I)
FOLDER_STOP = frozenset({"a", "an", "the", "new", "this", "that", "my", "same", "empty", "named", "called", "it", "of"})
FOLDER_WORD = re.compile(r"\b(?:folder|directory|dir)\b", re.I)
PRONOUN = re.compile(r"\b(?:it|its|that\s+file|this\s+file|the\s+file|that\s+one|the\s+same\s+file|same\s+file|that\s+folder|the\s+folder|that\s+directory"
                     r"|the\s+(?:yaml|yml|profile|manifest|app(?:lication)?(?:\s+profile)?)(?:\s+file)?)\b", re.I)
FIELD_WORD = re.compile(r"\b(?:port|image|tag|cpu|memory|ram|storage|name|version|owner|description|lifecycle|replicas?|env|args?|entry\s*point|protocol|label|domain|intent)\b", re.I)
FIELD_REF = re.compile(r"\b(?:its|their|the)\s+(?:port|image|tag|cpu|memory|ram|storage|name|version|owner|description|lifecycle|env|args?)\b", re.I)
PROFILE_NOUN = re.compile(r"\b(?:ya?ml|profile|manifest|deployment|config(?:uration)?|descriptor|spec)\b", re.I)
ARTIFACT_NOUN = re.compile(r"\b(?:ya?ml|profile|manifest|deployment|config(?:uration)?|descriptor|spec|app(?:lication)?|service|file)\b", re.I)
DELETE_HEAD = re.compile(r"^(?:delete|remove|erase|trash|destroy|drop|get\s+rid\s+of|wipe)\b", re.I)
CREATE_HEAD_UNAMBIGUOUS = re.compile(r"^(?:create|generate|write|build|draft|produce|scaffold|prepare|deploy|launch|spin\s+up|give\s+me|get\s+me|new|i\s+(?:want|need)\s+(?:a|an|the|some))\b", re.I)
CREATE_HEAD_AMBIGUOUS = re.compile(r"^(?:make|add|set\s+up|use|put)\b", re.I)
EDIT_HEAD = re.compile(r"^(?:change|set|update|modify|edit|rename|switch|bump|increase|decrease|raise|lower|replace|adjust|tweak|use|make|put|add|append|remove|delete|configure|rewrite)\b", re.I)
VALIDATE_HEAD = re.compile(r"^(?:validate|lint|verify|check)\b", re.I)
FIX_HEAD = re.compile(r"^(?:fix|repair|correct|resolve)\b", re.I)
READ_HEAD = re.compile(r"^(?:show|open|read|display|print|cat|view|see)\b", re.I)
EXPLAIN_HEAD = re.compile(r"^(?:explain|describe|walk\s+me\s+through|summari[sz]e)\b", re.I)
VALID_Q = re.compile(r"^(?:is|are|does|did)\s+(?:the\s+|my\s+)?(?:file\s+)?(?P<t>" + _TOKEN + r"|it|the\s+(?:yaml|yml|profile))\s+(?:look\s+)?(?:valid|correct|ok(?:ay)?|right|fine)\b", re.I)
WHAT_DOES = re.compile(r"^what\s+(?:does|is\s+in)\s+(?:the\s+file\s+)?(?P<t>" + _TOKEN + r")\b", re.I)

_FILE_WORD_RE = re.compile(r"\bfile\b", re.I)
_ERROR_WORD_RE = re.compile(r"\b(?:errors?|issues?|problems?|mistakes?)\b", re.I)
_IMAGE_WORD_RE = re.compile(r"\b(?:nginx|apache|httpd|redis|postgres|mysql|mongo|node|python|grafana|traefik)\b", re.I)
_FOLDER_CREATE_RE = re.compile(r"^(?:create|make|add|new|mkdir)\b", re.I)


def _folder_token(clean: str) -> str | None:
    m = FOLDER_AFTER.search(clean)
    if m:
        cand = m.group(1).strip().rstrip("./,")
        if cand and cand.lower() not in FOLDER_STOP:
            return cand
        return None
    m = FOLDER_BEFORE.search(clean)
    if m:
        cand = m.group(1).strip().rstrip("./,")
        if cand and cand.lower() not in FOLDER_STOP:
            return cand
        return None
    return None


def _compute_is_profile(verb: str, explicit_target: str | None, s: str) -> bool:
    if verb in ("create", "edit", "fix", "validate"):
        if explicit_target is not None:
            try:
                ext = extension(explicit_target)
            except Exception:
                ext = ""
            if ext in (".yaml", ".yml"):
                return True
            return False
        if looks_like_profile_request(s) or PROFILE_NOUN.search(s):
            return True
        if verb == "create" and _IMAGE_WORD_RE.search(s):
            return True
        return False
    return False


def detect_act_intent(text: str) -> ActIntent | None:
    """None when the message is not an instruction to act (questions, chat, anything else). Never raises."""
    try:
        raw = " ".join(text.split())
        clean = raw
        for ch in ("`", '"', "'", "“", "”"):
            clean = clean.replace(ch, " ")
        s = POLITE_PREFIX.sub("", clean).strip()
        if not s:
            return None
        # 2. Special question forms first
        m = VALID_Q.match(s)
        if m:
            t = m.group("t")
            low = t.strip().lower()
            if low in ("it",) or low.startswith("the "):
                return ActIntent(verb="validate", target=None, target_source="none",
                                is_profile=_compute_is_profile("validate", None, s))
            try:
                norm = normalize_path(t)
            except PathError as e:
                return ActIntent(verb="validate", target=None, target_source="none",
                                is_profile=_compute_is_profile("validate", None, s), path_error=str(e))
            return ActIntent(verb="validate", target=norm, target_source="explicit",
                            is_profile=_compute_is_profile("validate", norm, s))
        m = WHAT_DOES.match(s)
        if m:
            t = m.group("t")
            try:
                norm = normalize_path(t)
            except PathError as e:
                return ActIntent(verb="explain_file", target=None, target_source="none",
                                is_profile=False, path_error=str(e))
            return ActIntent(verb="explain_file", target=norm, target_source="explicit", is_profile=False)
        # 3. question head
        if QUESTION_HEAD.match(s):
            return None
        # 4. tokens
        tok = FILE_TOKEN.search(clean)
        pron = PRONOUN.search(s)
        field = FIELD_WORD.search(s)
        fieldref = FIELD_REF.search(s)
        folder = _folder_token(clean)
        verb: str | None = None
        # 5. verb decision in order
        if FIX_HEAD.match(s) and (tok or pron or PROFILE_NOUN.search(s) or _ERROR_WORD_RE.search(s)):
            verb = "fix"
        elif VALIDATE_HEAD.match(s) and (tok or pron or PROFILE_NOUN.search(s)):
            verb = "validate"
        elif DELETE_HEAD.match(s) and FOLDER_WORD.search(s):
            verb = "delete_folder"
        elif (DELETE_HEAD.match(s) and (tok or pron or _FILE_WORD_RE.search(s) or PROFILE_NOUN.search(s))
              and not (field and not tok and not _FILE_WORD_RE.search(s))):
            verb = "delete"
        elif _FOLDER_CREATE_RE.match(s) and FOLDER_WORD.search(s):
            verb = "create_folder"
        elif READ_HEAD.match(s) and (tok or pron or _FILE_WORD_RE.search(s)):
            verb = "read"
        elif EXPLAIN_HEAD.match(s) and (tok or pron):
            verb = "explain_file"
        elif CREATE_HEAD_UNAMBIGUOUS.match(s) and (tok or ARTIFACT_NOUN.search(s) or looks_like_profile_request(s) or _IMAGE_WORD_RE.search(s)):
            verb = "create"
        elif (CREATE_HEAD_AMBIGUOUS.match(s) and (PROFILE_NOUN.search(s) or tok)
              and not fieldref and not pron):
            verb = "create"
        elif EDIT_HEAD.match(s) and (tok or pron or fieldref or field or PROFILE_NOUN.search(s)):
            verb = "edit"
        elif (
            "?" not in raw
            and _ANY_ACT_VERB.search(s) is None
            and looks_like_profile_request(s)
            and IMAGE_CUES.search(s)
        ):
            # Verbless profile request ("yaml for a minio object store, native app"):
            # names a deployment artifact and an image but no action. Treat as a
            # create so terse requests still build instead of falling to ask.
            verb = "create"
        else:
            return None
        assert verb is not None
        # 6. target extraction
        if verb in ("create_folder", "delete_folder"):
            if folder is None:
                return ActIntent(verb=verb, target=None, target_source="none", is_profile=False)  # type: ignore[arg-type]
            try:
                norm = normalize_path(folder)
            except PathError as e:
                return ActIntent(verb=verb, target=None, target_source="none", is_profile=False, path_error=str(e))  # type: ignore[arg-type]
            return ActIntent(verb=verb, target=norm, target_source="explicit", is_profile=False)  # type: ignore[arg-type]
        # file verbs
        if tok is not None:
            raw_tok = tok.group(1)
            try:
                norm = normalize_path(raw_tok)
            except PathError as e:
                return ActIntent(verb=verb, target=None, target_source="none",  # type: ignore[arg-type]
                                is_profile=_compute_is_profile(verb, None, s), path_error=str(e))
            return ActIntent(verb=verb, target=norm, target_source="explicit",  # type: ignore[arg-type]
                            is_profile=_compute_is_profile(verb, norm, s))
        return ActIntent(verb=verb, target=None, target_source="none",  # type: ignore[arg-type]
                        is_profile=_compute_is_profile(verb, None, s))
    except Exception:
        return None


def resolve_intent(intent: ActIntent, session: Session, text: str) -> ActIntent | None:
    """Fill intent.target from memory when the text uses a pronoun / field reference; None when the verb is
    read or explain_file and nothing can be resolved. Never raises (defensive)."""
    try:
        if intent.target or intent.path_error:
            return intent
        if intent.verb in ("create", "create_folder"):
            return intent
        ref = pronoun_target(session, text)
        if ref is None and FIELD_REF.search(text) and session.last_file is not None:
            cand = session.files.get(session.last_file)
            if cand is not None and not cand.deleted:
                ref = session.last_file
        if ref is None and intent.verb in ("edit", "fix", "validate"):
            live = [f for f in session.files.values() if not f.deleted and f.kind in ("native", "device")]
            if len(live) == 1:
                ref = live[0].path
        if ref is not None:
            return dataclasses.replace(intent, target=ref, target_source="reference")
        if intent.verb in ("read", "explain_file"):
            return None
        return intent
    except Exception:
        return intent
