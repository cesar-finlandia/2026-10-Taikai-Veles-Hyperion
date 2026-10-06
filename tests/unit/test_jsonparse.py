"""JSON extraction tests."""
import pytest
from hyperion.llm.base import LLMBadJson
from hyperion.llm.jsonparse import extract_json


def test_plain_object():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_fenced():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('```\n{"b": 2}\n```') == {"b": 2}


def test_prose_around():
    assert extract_json('Here you go: {"x": 5} hope it helps') == {"x": 5}


def test_trailing_comma():
    assert extract_json('{"a": 1,}') == {"a": 1}
    assert extract_json('{"a": [1, 2,],}') == {"a": [1, 2]}


def test_single_quotes():
    assert extract_json("{'a': 1}") == {"a": 1}


def test_nested_braces_in_strings():
    assert extract_json('{"text": "a { b } c", "n": 1}') == {"text": "a { b } c", "n": 1}


def test_unclosed_object_repaired():
    assert extract_json('{"a": 1') == {"a": 1}


def test_no_json_raises():
    with pytest.raises(LLMBadJson):
        extract_json("no json here at all")
    with pytest.raises(LLMBadJson):
        extract_json("[1, 2, 3]")
