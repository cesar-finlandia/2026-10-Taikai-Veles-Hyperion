"""Pure eval metric functions (DP-EVAL WU-EVAL-01)."""
from __future__ import annotations

from hyperion.dsl.check import check_profile
from hyperion.dsl.render import render_profile
from hyperion.dsl.slots import extract_slots
from hyperion.issues import errors_only
from hyperion.rag.answer import ABSTAIN_PHRASE

from evals.faults import FAULTS, FAULT_BASE_REQUESTS
from evals.freewrite import extract_yaml
from evals.metrics import (
    confusion,
    is_abstention,
    keyword_hit,
    macro_f1,
    per_class_prf,
    percentile,
    prf,
    source_hit,
    wilson,
)


def test_prf_basic():
    assert prf(8, 2, 2) == (0.8, 0.8, 0.8)


def test_prf_zero_division():
    assert prf(0, 0, 0) == (0.0, 0.0, 0.0)


def test_confusion_and_macro_f1():
    labels = ["a", "b", "c"]
    pairs = [
        ("a", "a"),
        ("a", "a"),
        ("a", "b"),
        ("b", "b"),
        ("b", "c"),
        ("c", "c"),
    ]
    conf = confusion(pairs, labels)
    assert conf["a"] == {"a": 2, "b": 1, "c": 0}
    assert conf["b"] == {"a": 0, "b": 1, "c": 1}
    assert conf["c"] == {"a": 0, "b": 0, "c": 1}
    per = per_class_prf(conf, labels)
    # a: tp=2 fp=0 fn=1 -> p=1.0 r=2/3 f1=0.8
    assert per["a"]["precision"] == 1.0
    assert per["a"]["recall"] == 2 / 3
    assert per["a"]["f1"] == 0.8
    assert per["a"]["support"] == 3
    # b: tp=1 fp=1 fn=1 -> p=r=f1=0.5
    assert per["b"]["precision"] == 0.5
    assert per["b"]["recall"] == 0.5
    assert per["b"]["f1"] == 0.5
    # c: tp=1 fp=1 fn=0 -> p=0.5 r=1.0 f1=2/3
    assert per["c"]["precision"] == 0.5
    assert per["c"]["recall"] == 1.0
    assert per["c"]["f1"] == 2 / 3
    assert macro_f1(per) == (0.8 + 0.5 + 2 / 3) / 3
    # a label with support 0 is ignored by macro_f1
    per["z"] = {"precision": 0.0, "recall": 0.0, "f1": 0.0, "support": 0.0}
    assert macro_f1(per) == (0.8 + 0.5 + 2 / 3) / 3


def test_keyword_hit_rules():
    assert keyword_hit("Nginx Research Project on the Edge", ["research project"], ["edge"]) is True
    assert keyword_hit("Nginx Research Project", ["research project"], ["edge"]) is False
    assert keyword_hit("nothing here", ["research project"], []) is False
    assert keyword_hit("anything at all", [], []) is True
    assert keyword_hit("EDGE computing", [], ["edge"]) is True
    assert keyword_hit("no match", [], ["edge"]) is False


def test_is_abstention():
    assert is_abstention(ABSTAIN_PHRASE + " Try asking about native apps.") is True
    assert is_abstention("This is not covered in the docs, but in general: ...") is True
    assert is_abstention("THIS IS NOT COVERED HERE") is True
    assert is_abstention("Here is what the documentation says:\n- nginx [1]") is False


def test_source_hit_and_percentile():
    assert source_hit([], ["a"]) is False
    assert source_hit(["a"], []) is False
    assert source_hit(["a", "b"], ["b"]) is True
    assert percentile([1, 2, 3, 4], 50) == 2
    assert percentile([1, 2, 3, 4], 95) == 4
    assert percentile([], 50) == 0.0


def test_wilson_interval():
    assert wilson(0, 0) == (0.0, 0.0)
    low, high = wilson(50, 100)
    assert round(low, 3) == 0.404
    assert round(high, 3) == 0.596
    assert wilson(10, 10)[1] == 1.0


def _nginx_profile() -> str:
    return render_profile(
        extract_slots(
            "Create a deployment YAML for a service using the nginx Docker image",
            "native",
        ).slots
    )


def test_faults_each_raise_a_checker_error():
    text = _nginx_profile()
    assert not errors_only(check_profile(text))
    for fault in FAULTS:
        bad = fault.apply(text)
        assert bad != text, fault.name
        assert errors_only(check_profile(bad)), fault.name
    drop_owner = next(f for f in FAULTS if f.name == "drop_owner")
    once = drop_owner.apply(text)
    try:
        drop_owner.apply(once)
    except ValueError:
        pass
    else:
        raise AssertionError("applying drop_owner twice must raise ValueError")


def test_freewrite_extract_yaml():
    fenced = "Some intro\n```yaml\nkey: value\n```\nTrailing"
    assert extract_yaml(fenced) == "key: value"
    plain = "Intro\n```\njust: text\n```"
    assert extract_yaml(plain) == "just: text"
    unfenced = "  plain answer  "
    assert extract_yaml(unfenced) == "plain answer"


def test_fault_counts():
    assert len(FAULTS) == 8
    assert len(FAULT_BASE_REQUESTS) == 3
