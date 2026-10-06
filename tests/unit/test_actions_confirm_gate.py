"""Confirmation parser and HITL gate (DP-ACTIONS WU-ACT-01)."""
from __future__ import annotations

import pytest

from hyperion.actions.confirm import parse_confirmation
from hyperion.actions.gate import PlannedItem, classify, requires_confirmation
from hyperion.config import Settings
from hyperion.ide.actions import make_action


def test_yes_words():
    for text in ("yes", "y", "yeah", "sure", "ok", "go ahead", "do it", "approved", "sí", "oui"):
        assert parse_confirmation(text) == "yes", text


def test_no_words():
    for text in ("no", "n", "nope", "cancel", "stop", "never mind", "decline", "nein", "não"):
        assert parse_confirmation(text) == "no", text


def test_filler_after_head():
    assert parse_confirmation("yes delete it") == "yes"
    assert parse_confirmation("no keep it") == "no"
    assert parse_confirmation("yes please go ahead") == "yes"


def test_yes_but_other_is_other():
    assert parse_confirmation("yes but change the port to 9090") == "other"
    assert parse_confirmation("sure, and also add an owner") == "other"


def test_empty_and_punctuation():
    assert parse_confirmation("") == "other"
    assert parse_confirmation("!!!") == "other"
    assert parse_confirmation("YES!!!") == "yes"
    assert parse_confirmation("  No.  ") == "no"


def test_non_english_words():
    assert parse_confirmation("sí") == "yes"
    assert parse_confirmation("oui") == "yes"
    assert parse_confirmation("nein") == "no"
    assert parse_confirmation("não") == "no"


def test_long_sentence_is_other():
    assert parse_confirmation("yes this is a very long sentence with many words here") == "other"


def test_no_wins_when_both_present():
    assert parse_confirmation("yes no") == "no"
    assert parse_confirmation("yes cancel") == "no"


def test_unrelated_text_is_other():
    assert parse_confirmation("what is a native app") == "other"
    assert parse_confirmation("ok so what is hyperai") == "other"


def test_classify_strict_edit():
    a = make_action("edit_file", "app.yaml", "x")
    assert classify(a, mode="strict", existed=True, patch_edit=True) == "edit"
    assert classify(a, mode="strict", existed=True, patch_edit=False) == "overwrite"


def test_classify_destructive_edit():
    a = make_action("edit_file", "app.yaml", "x")
    assert classify(a, mode="destructive", existed=True, patch_edit=True) is None
    assert classify(a, mode="destructive", existed=True, patch_edit=False) == "overwrite"


def test_classify_delete():
    for mode in ("strict", "destructive"):
        assert classify(make_action("delete_file", "a.yaml"), mode=mode, existed=True, patch_edit=False) == "delete"
        assert classify(make_action("delete_folder", "d"), mode=mode, existed=None, patch_edit=False) == "delete"


def test_classify_overwrite_create_existing():
    a = make_action("create_file", "app.yaml", "x")
    assert classify(a, mode="strict", existed=True, patch_edit=False) == "overwrite"
    assert classify(a, mode="destructive", existed=True, patch_edit=False) == "overwrite"


def test_classify_new_create_and_folder_none():
    a = make_action("create_file", "app.yaml", "x")
    assert classify(a, mode="strict", existed=False, patch_edit=False) is None
    assert classify(a, mode="strict", existed=None, patch_edit=False) is None
    assert classify(make_action("create_folder", "d"), mode="strict", existed=None, patch_edit=False) is None


def test_requires_confirmation_flag_off():
    s = Settings(api_key="k", feature_hitl=False)
    items = [PlannedItem(make_action("delete_file", "a.yaml"), "delete")]
    assert requires_confirmation(items, s) is False


def test_requires_confirmation_true_when_any():
    s = Settings(api_key="k")
    yes = [PlannedItem(make_action("delete_file", "a.yaml"), "delete")]
    assert requires_confirmation(yes, s) is True
    no = [PlannedItem(make_action("create_file", "n.yaml", "x"), None)]
    assert requires_confirmation(no, s) is False
