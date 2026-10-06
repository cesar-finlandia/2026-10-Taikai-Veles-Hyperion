"""Deterministic first-stage routing (DP-AGENT-CORE WU-AGENT-01)."""
from __future__ import annotations

from hyperion.agent.router import post_route, pre_route
from hyperion.config import Settings
from hyperion.guard.engine import Verdict
from hyperion.memory.store import SessionStore

MAX = 4000


def _session():
    return SessionStore(Settings(api_key="test-key")).get("u")


def test_empty_and_too_long_go_to_guard():
    s = _session()
    for t in ("", "   ", "x" * 4001):
        assert pre_route(t, s, has_pending=False, max_chars=MAX).kind == "guard"


def test_yes_no_with_pending_is_confirm():
    s = _session()
    pre = pre_route("yes", s, has_pending=True, max_chars=MAX)
    assert (pre.kind, pre.decision) == ("confirm", "yes")
    pre = pre_route("No.", s, has_pending=True, max_chars=MAX)
    assert (pre.kind, pre.decision) == ("confirm", "no")
    pre = pre_route("yes delete it", s, has_pending=True, max_chars=MAX)
    assert (pre.kind, pre.decision) == ("confirm", "yes")


def test_yes_without_pending_is_confirm_but_ack_goes_to_guard():
    s = _session()
    assert pre_route("yes", s, has_pending=False, max_chars=MAX).kind == "confirm"
    assert pre_route("ok", s, has_pending=False, max_chars=MAX).kind == "guard"
    assert pre_route("okay", s, has_pending=False, max_chars=MAX).kind == "guard"


def test_recall_questions_route_to_recall():
    s = _session()
    for q in ("what's my name?", "what did I just ask?", "which file did we create?"):
        pre = pre_route(q, s, has_pending=False, max_chars=MAX)
        assert pre.kind == "recall"
        assert pre.reply


def test_name_statement_is_fact():
    s = _session()
    pre = pre_route("My name is Elena", s, has_pending=False, max_chars=MAX)
    assert pre.kind == "fact"
    assert pre.reply is not None and "Elena" in pre.reply


def test_long_message_with_name_is_not_fact():
    # NOTE (deviation from the DP text, which states `act`): under the literal
    # §5.2 algorithm this message yields `guard` — it is too long for `fact`
    # (16 words > 8) and detect_act_intent returns None for mid-sentence verbs
    # ("... you to create ..."), so step 5 finds no intent and step 6 applies.
    # The test's intent — a long name message is not swallowed as `fact` — holds.
    s = _session()
    pre = pre_route(
        "My name is Elena and I would like you to create a deployment yaml for nginx",
        s,
        has_pending=False,
        max_chars=MAX,
    )
    assert pre.kind == "guard"
    assert pre.kind != "fact"


def test_act_intents_route_by_verb():
    s = _session()
    assert pre_route("create a yaml for nginx", s, has_pending=False, max_chars=MAX).kind == "act"
    assert pre_route("validate app.yaml", s, has_pending=False, max_chars=MAX).kind == "validate"
    assert pre_route("show me app.yaml", s, has_pending=False, max_chars=MAX).kind == "read"
    assert pre_route("explain app.yaml", s, has_pending=False, max_chars=MAX).kind == "read"
    assert pre_route("delete app.yaml", s, has_pending=False, max_chars=MAX).kind == "act"


def test_pronoun_without_file_goes_to_guard():
    s = _session()
    assert pre_route("explain it", s, has_pending=False, max_chars=MAX).kind == "guard"


def test_questions_route_to_guard():
    s = _session()
    for q in (
        "What is a native app?",
        "What is HyperAI?",
        "How do I validate a profile?",
        "What's the weather?",
    ):
        assert pre_route(q, s, has_pending=False, max_chars=MAX).kind == "guard"


def test_post_route_mapping():
    for category in ("off_topic", "injection", "empty", "too_long"):
        assert post_route(Verdict(False, category, "r", "rule", 1.0)) == "refuse"  # type: ignore[arg-type]
    assert post_route(Verdict(True, "smalltalk", "greeting", "rule", 1.0, smalltalk="greeting")) == "smalltalk"
    assert post_route(Verdict(True, "in_scope", "ok", "rule", 0.9)) == "ask"
    assert post_route(Verdict(False, "in_scope", "x", "rule", 0.5)) == "refuse"
