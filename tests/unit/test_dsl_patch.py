"""Unit tests for hyperion.dsl.patch."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hyperion.dsl.patch import (
    PatchError,
    PatchOp,
    apply_ops,
    deterministic_ops_from_request,
    ops_from_json,
)

FIX = Path(__file__).parent.parent / "fixtures" / "profiles"


def _native() -> str:
    return (FIX / "native_nginx.yaml").read_text()


def _device() -> str:
    return (FIX / "device_docker.yaml").read_text()


def test_set_scalar_preserves_quotes_minimal_diff() -> None:
    text = _native()
    out = apply_ops(text, [PatchOp(op="set", path="applicationProfile.metadata.owner", value="New-Team")])
    assert 'owner: "New-Team"' in out
    before, after = text.splitlines(), out.splitlines()
    assert len(before) == len(after)
    assert sum(1 for a, b in zip(before, after) if a != b) == 1


def test_set_int_plain() -> None:
    out = apply_ops(
        _native(),
        [PatchOp(op="set", path="applicationProfile.specs.network.ports[0].port", value=9000)],
    )
    assert "- port: 9000" in out
    assert len(out.splitlines()) == len(_native().splitlines())


def test_set_missing_key_reserialises() -> None:
    text = _native()
    out = apply_ops(
        text, [PatchOp(op="set", path="applicationProfile.metadata.team", value="core")])
    assert yaml.safe_load(out)["applicationProfile"]["metadata"]["team"] == "core"


def test_set_list_index_path() -> None:
    out = apply_ops(
        _native(),
        [PatchOp(op="set", path="applicationProfile.specs.network.ports[0].protocol", value="UDP")],
    )
    assert yaml.safe_load(out)["applicationProfile"]["specs"]["network"]["ports"][0]["protocol"] == "UDP"


def test_set_bad_index_raises() -> None:
    with pytest.raises(PatchError):
        apply_ops(
            _native(),
            [PatchOp(op="set", path="applicationProfile.specs.network.ports[5].port", value=1)],
        )


def test_delete_key() -> None:
    out = apply_ops(
        _native(), [PatchOp(op="delete", path="applicationProfile.metadata.description")])
    assert "description" not in yaml.safe_load(out)["applicationProfile"]["metadata"]


def test_replace_unique() -> None:
    out = apply_ops(_native(), [PatchOp(op="replace", find="UoA-Team", replace_with="New-Team")])
    assert "New-Team" in out and "UoA-Team" not in out


def test_replace_ambiguous_raises() -> None:
    with pytest.raises(PatchError, match="more than once"):
        apply_ops(_native(), [PatchOp(op="replace", find="port", replace_with="PORT")])


def test_replace_missing_raises() -> None:
    with pytest.raises(PatchError, match="exactly once"):
        apply_ops(_native(), [PatchOp(op="replace", find="no-such-text-xyz", replace_with="x")])


def test_result_must_parse() -> None:
    with pytest.raises(PatchError):
        apply_ops(_native(), [PatchOp(op="replace", find="  specs:", replace_with="  specs: [")])
    out = apply_ops(_native(), [PatchOp(op="replace", find="UoA-Team", replace_with="X")])
    yaml.safe_load(out)


def test_ops_from_json_validates() -> None:
    ops = ops_from_json([
        {"op": "set", "path": "a.b", "value": 1},
        {"op": "delete", "path": "a.c"},
        {"op": "replace", "find": "x", "with": "y"},
    ])
    assert [o.op for o in ops] == ["set", "delete", "replace"]
    assert ops[2].replace_with == "y"
    with pytest.raises(PatchError):
        ops_from_json([{"op": "frobnicate"}])
    with pytest.raises(PatchError):
        ops_from_json([{"op": "set", "path": "a"}])
    with pytest.raises(PatchError):
        ops_from_json(["not-a-dict"])  # type: ignore[list-item]


def test_ops_from_json_limit_eight() -> None:
    with pytest.raises(PatchError):
        ops_from_json([{"op": "delete", "path": f"a.{i}"} for i in range(9)])
    assert len(ops_from_json([{"op": "delete", "path": f"a.{i}"} for i in range(8)])) == 8


def test_deterministic_port() -> None:
    ops = deterministic_ops_from_request("change port to 9000", _native())
    assert ops == [PatchOp(op="set", path="applicationProfile.specs.network.ports[0].port", value=9000)]
    out = apply_ops(_native(), ops)
    assert yaml.safe_load(out)["applicationProfile"]["specs"]["network"]["ports"][0]["port"] == 9000


def test_deterministic_name_and_version() -> None:
    ops = deterministic_ops_from_request("rename to my-web version 1.2.3", _native())
    out = apply_ops(_native(), ops)
    doc = yaml.safe_load(out)
    assert doc["applicationProfile"]["metadata"]["name"] == "my-web"
    assert doc["applicationProfile"]["metadata"]["version"] == "1.2.3"


def test_deterministic_image_with_tag() -> None:
    ops = deterministic_ops_from_request("set image to redis:7", _native())
    out = apply_ops(_native(), ops)
    img = yaml.safe_load(out)["applicationProfile"]["specs"]["runtime"]["containerImage"]
    assert img == {"uri": "redis", "tag": "7"}


def test_deterministic_memory_cpu() -> None:
    ops = deterministic_ops_from_request("set cpu to 2 cores and memory to 1 GB", _native())
    out = apply_ops(_native(), ops)
    res = yaml.safe_load(out)["applicationProfile"]["specs"]["resources"]
    assert res["cpu"] == "2000m"
    assert res["memory"] == "1Gi"


def test_deterministic_device_name_sets_both_paths() -> None:
    ops = deterministic_ops_from_request("rename to my-device", _device())
    assert len(ops) == 2
    out = apply_ops(_device(), ops)
    doc = yaml.safe_load(out)
    assert doc["metadata"]["name"] == "my-device"
    assert doc["spec"]["app"]["name"] == "my-device"


def test_deterministic_unknown_kind_empty() -> None:
    assert deterministic_ops_from_request("change port to 80", "foo: 1\n") == []
    assert deterministic_ops_from_request("hello there", _native()) == []
