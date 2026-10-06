"""Intents front of the act pipeline (DP-ACTIONS WU-ACT-01)."""
from __future__ import annotations

from hyperion.actions.intents import detect_act_intent, resolve_intent
from hyperion.config import Settings
from hyperion.dsl.render import render_profile
from hyperion.dsl.slots import extract_slots
from hyperion.memory.store import SessionStore


def _detect(text: str):
    return detect_act_intent(text)


def test_create_nginx_request_is_create_profile():
    for text in (
        "Create a deployment YAML for a service using the nginx Docker image",
        "generate a yaml for redis",
        "deploy an nginx service",
        "I want a deployment yaml for grafana",
    ):
        intent = _detect(text)
        assert intent is not None, text
        assert intent.verb == "create", text
        assert intent.is_profile is True, text
        assert intent.target is None, text


def test_create_with_explicit_filename():
    i = _detect("create web.yaml for nginx on port 8080")
    assert i is not None
    assert i.verb == "create"
    assert i.target == "web.yaml"
    assert i.target_source == "explicit"
    i2 = _detect("make a file called notes.txt with content hello")
    assert i2 is not None
    assert i2.verb == "create"
    assert i2.target == "notes.txt"
    assert i2.is_profile is False


def test_create_folder_and_delete_folder():
    i = _detect("create a folder called configs")
    assert i is not None and i.verb == "create_folder" and i.target == "configs"
    i = _detect("make a directory app/config")
    assert i is not None and i.verb == "create_folder" and i.target == "app/config"
    i = _detect("delete the configs folder")
    assert i is not None and i.verb == "delete_folder" and i.target == "configs"
    i = _detect("create a new folder")
    assert i is not None and i.verb == "create_folder" and i.target is None


def test_delete_file_and_pronoun():
    i = _detect("delete app.yaml")
    assert i is not None and i.verb == "delete" and i.target == "app.yaml"
    i = _detect("delete it")
    assert i is not None and i.verb == "delete" and i.target is None
    i = _detect("remove the yaml file")
    assert i is not None and i.verb == "delete"
    i = _detect("Could you please delete app.yaml")
    assert i is not None and i.verb == "delete"


def test_edit_with_field_reference():
    for text in (
        "change the port to 8080",
        "now change its port to 8080",
        "set the image to nginx:1.27",
        "update app.yaml: set cpu to 250m",
    ):
        i = _detect(text)
        assert i is not None and i.verb == "edit", text


def test_remove_field_is_edit_not_delete():
    i = _detect("remove the port")
    assert i is not None and i.verb == "edit"
    i = _detect("delete the owner field")
    assert i is not None and i.verb == "edit"


def test_validate_forms():
    for text in ("validate app.yaml", "check my yaml", "is app.yaml valid?", "lint the profile"):
        i = _detect(text)
        assert i is not None and i.verb == "validate", text


def test_fix_forms():
    for text in ("fix it", "fix the errors in app.yaml", "repair the profile"):
        i = _detect(text)
        assert i is not None and i.verb == "fix", text


def test_read_and_explain_forms():
    assert _detect("show me app.yaml") is not None and _detect("show me app.yaml").verb == "read"  # type: ignore[union-attr]
    assert _detect("open app.yaml").verb == "read"  # type: ignore[union-attr]
    assert _detect("explain app.yaml").verb == "explain_file"  # type: ignore[union-attr]
    assert _detect("what does app.yaml do?").verb == "explain_file"  # type: ignore[union-attr]


def test_questions_are_not_act_intents():
    for text in (
        "What is a native app?",
        "How do I validate a profile?",
        "what is HyperAI?",
        "Why is delete protected?",
        "Can you explain native vs device apps?",
        "tell me about the IDE",
        "hello",
    ):
        assert _detect(text) is None, text


def test_unsafe_paths_set_path_error():
    for text in ("delete ../secrets.yaml", "create /etc/passwd.yaml", "edit C:\\Users\\x\\app.yaml"):
        i = _detect(text)
        assert i is not None, text
        assert isinstance(i.path_error, str) and i.path_error, text
        assert i.target is None, text


def test_polite_prefix_stripped():
    for text in (
        "Could you please delete app.yaml",
        "I want you to create a deployment yaml for nginx",
        "go ahead and validate app.yaml",
    ):
        assert _detect(text) is not None, text


def test_resolve_intent_uses_memory_and_sole_profile():
    settings = Settings(api_key="test-key")
    store = SessionStore(settings)
    content = render_profile(extract_slots("nginx service", "native").slots)
    store.note_file("u", "app.yaml", "native", "create", content)
    session = store.get("u")
    r = resolve_intent(_detect("delete it"), session, "delete it")  # type: ignore[arg-type]
    assert r is not None and r.target == "app.yaml" and r.target_source == "reference"
    r = resolve_intent(_detect("change the port to 9090"), session, "change the port to 9090")  # type: ignore[arg-type]
    assert r is not None and r.target == "app.yaml"
    store.note_file("u", "b.yaml", "native", "create", content)
    session = store.get("u")
    r = resolve_intent(_detect("change the port to 9090"), session, "change the port to 9090")  # type: ignore[arg-type]
    # edit with no pronoun and no field word stays unresolved -> target None
    # "change the port to 9090" has field word 'port' but no pronoun; with two files sole-profile rule needs no pronoun? 
    # Spec §5.2 step 5 applies to edit/fix/validate with exactly one live file; here two files.
    # The FIELD_REF rule needs 'its/their/the port'; 'the port' qualifies via FIELD_REF? 
    # 'the port' matches FIELD_REF (the + port) so it resolves via last_file. To test unresolved, use a phrase
    # without pronoun/field-ref and two files: e.g. "change it"? has pronoun -> resolves. Use "update the config".
    r2 = resolve_intent(_detect("update the config"), session, "update the config")  # type: ignore[arg-type]
    assert r2 is not None and r2.target is None


def test_resolve_intent_none_for_pronoun_without_file():
    settings = Settings(api_key="test-key")
    store = SessionStore(settings)
    session = store.get("u2")
    assert resolve_intent(_detect("explain it"), session, "explain it") is None  # type: ignore[arg-type]
    assert resolve_intent(_detect("show it"), session, "show it") is None  # type: ignore[arg-type]
    r = resolve_intent(_detect("delete it"), session, "delete it")  # type: ignore[arg-type]
    assert r is not None and r.target is None
