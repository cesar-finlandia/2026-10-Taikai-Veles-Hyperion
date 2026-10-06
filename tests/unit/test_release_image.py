"""Unit tests for release.image (DP-RELEASE WU-RELEASE-02)."""
import pytest

from release.image import build_commands, forbidden_in_listing, parse_dotenv_key, wait_healthy


def test_build_commands_local_and_tags_no_push():
    assert build_commands("demo") == [
        ["docker", "build", "--platform", "linux/amd64", "-t", "hyperion:local", "."],
        ["docker", "tag", "hyperion:local", "demo/hyperion:v2"],
        ["docker", "tag", "hyperion:local", "demo/hyperion:latest"],
    ]


def test_build_commands_push_adds_push_per_tag():
    commands = build_commands("demo", push=True)
    assert commands[-2:] == [
        ["docker", "push", "demo/hyperion:v2"],
        ["docker", "push", "demo/hyperion:latest"],
    ]


def test_build_commands_rejects_bad_user():
    for bad in ("Demo", "d", "a b"):
        with pytest.raises(ValueError, match="bad user"):
            build_commands(bad)


def test_build_commands_rejects_bad_tag():
    for bad in ("-x", "a b"):
        with pytest.raises(ValueError, match="bad tag"):
            build_commands("demo", (bad,))
    with pytest.raises(ValueError, match="no tags"):
        build_commands("demo", ())


def test_parse_dotenv_key_plain_quoted_comment_missing():
    assert parse_dotenv_key("API_KEY=abc") == "abc"
    assert parse_dotenv_key('API_KEY="abc def"') == "abc def"
    assert parse_dotenv_key("# API_KEY=zzz\nAPI_KEY='k1'\nAPI_KEY=k2") == "k2"
    assert parse_dotenv_key("OTHER=1") == ""


def test_forbidden_in_listing_flags_env_hee_design():
    listing = "/app\n/app/.env\n/app/hee\n/app/design_documents/x\n/app/main.py"
    assert forbidden_in_listing(listing) == ["/app/.env", "/app/design_documents/x", "/app/hee"]


def test_forbidden_in_listing_clean_listing_is_empty():
    listing = "/app\n/app/main.py\n/app/hyperion\n/app/data/index.json\n/app/.venv/bin"
    assert forbidden_in_listing(listing) == []


def test_wait_healthy_succeeds_after_retries():
    statuses = iter([0, 0, 200])
    ticks = iter([0, 1, 2, 3, 4, 5])
    sleeps: list[float] = []
    assert wait_healthy(lambda: next(statuses), 60, 1.0, sleep=sleeps.append, clock=lambda: next(ticks)) is True
    assert len(sleeps) == 2


def test_wait_healthy_times_out():
    ticks = iter(range(100))
    sleeps: list[float] = []
    assert wait_healthy(lambda: 0, 3, 1.0, sleep=sleeps.append, clock=lambda: next(ticks)) is False
