"""Budget, models, facts and recall unit tests (DP-MEMORY WU-MEM-01)."""
from __future__ import annotations

from hyperion.memory.budget import clip, estimate_tokens, fit_to_budget
from hyperion.memory.facts import extract_facts
from hyperion.memory.models import FileRef, Session, Turn
from hyperion.memory.recall import answer_recall, pronoun_target


def _session(**kw) -> Session:
    base = Session(user_id="u1")
    for k, v in kw.items():
        setattr(base, k, v)
    return base


def _turn(user: str, assistant: str = "ok", intent: str = "chat") -> Turn:
    return Turn(ts=1.0, user=user, assistant=assistant, intent=intent)


def _ref(path: str, kind: str = "native", op: str = "create", ts: float = 1.0) -> FileRef:
    return FileRef(path=path, kind=kind, last_op=op, sha="abc", ts=ts)


def test_estimate_tokens() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("a") == 1
    assert estimate_tokens("abcd") == 2
    assert estimate_tokens("x" * 35) == 10
    assert estimate_tokens("x" * 36) == 11


def test_fit_to_budget_keeps_newest() -> None:
    items = ["a" * 35, "b" * 35, "c" * 35]  # 10 tokens each
    assert fit_to_budget(items, 100) == items
    assert fit_to_budget(items, 20) == items[1:]
    assert fit_to_budget(items, 10) == items[2:]
    assert fit_to_budget(items, 0) == []


def test_fit_to_budget_truncates_oversized() -> None:
    big = "z" * 350  # 100 tokens
    out = fit_to_budget([big], 10)
    assert len(out) == 1
    assert out[0].endswith("…")
    assert estimate_tokens(out[0]) <= 10


def test_clip() -> None:
    assert clip("hello   world", 50) == "hello world"
    assert clip("  a\nb\tc ", 50) == "a b c"
    assert clip("short", 10) == "short"
    cut = clip("x" * 20, 10)
    assert cut == "x" * 9 + "…"
    assert len(cut) == 10


def test_facts_name_variants() -> None:
    assert extract_facts("my name is Elena") == {"name": "Elena"}
    assert extract_facts("My name's Bob Jones") == {"name": "Bob Jones"}
    assert extract_facts("please call me Zoe") == {"name": "Zoe"}
    assert extract_facts("I'm called Ana.") == {"name": "Ana"}
    assert extract_facts("I am named Max!") == {"name": "Max"}


def test_facts_lowercase_not_a_name() -> None:
    assert extract_facts("my name is elena") == {}
    assert extract_facts("hello there") == {}


def test_facts_note() -> None:
    assert extract_facts("remember that the sky is blue") == {"note": "the sky is blue"}
    assert extract_facts("Remember my dog likes rain!") == {"note": "my dog likes rain"}
    assert extract_facts("hello world") == {}


def test_recall_name_known_and_unknown() -> None:
    assert answer_recall(_session(facts={"name": "Elena"}), "What's my name?") == "Your name is Elena."
    assert answer_recall(_session(), "who am i") == "You have not told me your name yet."
    assert answer_recall(_session(), "do you remember my name") == "You have not told me your name yet."


def test_recall_last_question() -> None:
    s = _session(turns=[_turn("how do I deploy?"), _turn("what port?")])
    assert answer_recall(s, "what did I just ask?") == "You asked: “what port?”"
    assert (
        answer_recall(_session(), "what did i just say")
        == "We have only just started - there is nothing earlier yet."
    )


def test_recall_last_file() -> None:
    s = _session(last_file="app.yaml", files={"app.yaml": _ref("app.yaml")})
    assert answer_recall(s, "which file did we touch?") == "The last file we worked on is `app.yaml`."
    assert (
        answer_recall(_session(), "what file are we working on")
        == "We have not touched any file in this conversation yet."
    )


def test_recall_files_list() -> None:
    s = _session(
        files={"a.yaml": _ref("a.yaml", ts=1.0), "b.yaml": _ref("b.yaml", op="edit", ts=2.0)},
        last_file="b.yaml",
    )
    out = answer_recall(s, "which files did we create?")
    assert out is not None
    assert "`b.yaml` (native, edit)" in out
    assert "`a.yaml` (native, create)" in out
    assert out.index("b.yaml") < out.index("a.yaml")
    assert answer_recall(_session(), "list the files we made") == (
        "We have not created or changed any files yet."
    )


def test_recall_notes_and_ack() -> None:
    s = _session(facts={"name": "Elena", "note:1": "the sky is blue"})
    out = answer_recall(s, "what do you remember?")
    assert out is not None
    assert "Elena" in out
    assert "the sky is blue" in out
    assert answer_recall(_session(), "what did I tell you to remember") == (
        "You have not asked me to remember anything yet."
    )
    assert answer_recall(_session(), "remember that the sky is blue") == "Noted: the sky is blue."


def test_recall_recap() -> None:
    s = _session(
        turns=[_turn("first question"), _turn("second question")],
        files={"app.yaml": _ref("app.yaml")},
        last_file="app.yaml",
    )
    out = answer_recall(s, "summarise the conversation so far")
    assert out is not None
    assert out.startswith("Here is a recap of what you asked:")
    assert "first question" in out
    assert "Files touched: app.yaml." in out


def test_recall_declined() -> None:
    s = _session(declined=["delete app.yaml"])
    out = answer_recall(s, "what did i decline?")
    assert out is not None
    assert "delete app.yaml" in out
    assert answer_recall(_session(), "what did i reject") == "You have not declined anything."


def test_recall_none_for_ordinary_text() -> None:
    assert answer_recall(_session(), "hello, how are you?") is None
    assert answer_recall(_session(), "create a file please") is None
    assert answer_recall(_session(), "what is it?") is None


def test_pronoun_it_with_verb() -> None:
    s = _session(last_file="app.yaml", files={"app.yaml": _ref("app.yaml")})
    assert pronoun_target(s, "delete it") == "app.yaml"
    assert pronoun_target(s, "please fix that file") == "app.yaml"


def test_pronoun_ignores_non_file_it() -> None:
    s = _session(last_file="app.yaml", files={"app.yaml": _ref("app.yaml")})
    assert pronoun_target(s, "what is it?") is None
    assert pronoun_target(_session(), "delete it") is None


def test_pronoun_explicit_path_wins() -> None:
    s = _session(last_file="app.yaml", files={"app.yaml": _ref("app.yaml")})
    assert pronoun_target(s, "delete it in other.yaml") is None
    assert pronoun_target(s, "delete the file named other.yaml") is None


def test_pronoun_yaml_reference() -> None:
    s = _session(
        files={
            "notes.txt": _ref("notes.txt", kind="other", ts=1.0),
            "app.yaml": _ref("app.yaml", kind="native", ts=2.0),
        }
    )
    assert pronoun_target(s, "validate the yaml file") == "app.yaml"
    assert pronoun_target(_session(), "check the yaml") is None
