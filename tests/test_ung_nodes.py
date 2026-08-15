"""UNG adapter conformance tests for diffapply.ung_nodes.

For every NODES entry: run every fixture case and assert the expected output
(floats compared approximately), double-run determinism, JSON round-trip of
inputs and outputs, and metadata sanity (unique prefixed ids, declared names
present in the fn signature, importable entrypoint).
"""

import importlib
import inspect
import json
import math
from pathlib import Path

import pytest

PKG = "diffapply"
REPOWORD = PKG.replace("-", "").replace("_", "").lower()

ung = importlib.import_module(PKG + ".ung_nodes")
NODES = ung.NODES
NODE_IDS = [n["id"] for n in NODES]
FIXTURE_DIR = Path(ung.__file__).resolve().parent / "ung_fixtures"

REQUIRED_KEYS = {
    "fn", "id", "capabilities", "summary", "inputs", "outputs",
    "parameters", "effects", "determinism", "idempotency", "tags",
}


def approx_equal(a, b, rel=1e-9, abs_tol=1e-12):
    """Deep equality with float tolerance; strict about types otherwise."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b if isinstance(a, bool) and isinstance(b, bool) else False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=rel, abs_tol=abs_tol)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(approx_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(approx_equal(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b if a is not None and b is not None else a is b


def load_cases(node):
    path = FIXTURE_DIR / (node["id"] + ".json")
    assert path.is_file(), "missing fixture file %s" % path
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_nodes_registry_nonempty():
    assert isinstance(NODES, list) and NODES


def test_node_ids_unique_and_prefixed():
    assert len(NODE_IDS) == len(set(NODE_IDS))
    for nid in NODE_IDS:
        assert nid.startswith("amarel." + REPOWORD + "."), nid
        action = nid.split(".", 2)[2]
        assert action and action == action.lower(), nid


def test_metadata_shape():
    for node in NODES:
        assert REQUIRED_KEYS <= set(node), node["id"]
        assert callable(node["fn"])
        assert isinstance(node["summary"], str) and node["summary"]
        assert node["effects"] == []
        assert node["determinism"] == "deterministic"
        assert node["idempotency"] == "idempotent"
        for tag in ("license.mit", "runtime.python", "dependency-free"):
            assert tag in node["tags"], node["id"]
        assert node["capabilities"], node["id"]
        for cap in node["capabilities"]:
            assert "." in cap, (node["id"], cap)
        for port in list(node["inputs"]) + list(node["outputs"]):
            assert {"name", "type_id", "description"} <= set(port), node["id"]
            assert port["type_id"].startswith("amarel.types."), node["id"]
        for param in node["parameters"]:
            assert {"name", "value_type", "default", "required"} <= set(param), node["id"]


def test_entrypoint_importable():
    mod = importlib.import_module(PKG + ".ung_nodes")
    for node in NODES:
        fn = node["fn"]
        assert getattr(mod, fn.__name__) is fn, node["id"]


def test_declared_names_in_signature():
    for node in NODES:
        sig = inspect.signature(node["fn"])
        for port in node["inputs"]:
            assert port["name"] in sig.parameters, (node["id"], port["name"])
        for param in node["parameters"]:
            assert param["name"] in sig.parameters, (node["id"], param["name"])


@pytest.mark.parametrize("node", NODES, ids=NODE_IDS)
def test_fixture_cases(node):
    cases = load_cases(node)
    assert len(cases) >= 2, "need at least 2 fixture cases for %s" % node["id"]
    for case in cases:
        inputs = case.get("inputs", {})
        params = case.get("parameters", {})
        assert json.loads(json.dumps(inputs)) == inputs
        assert json.loads(json.dumps(params)) == params
        out1 = node["fn"](**inputs, **params)
        out2 = node["fn"](**inputs, **params)
        assert approx_equal(out1, out2), "non-deterministic: %s" % node["id"]
        assert approx_equal(json.loads(json.dumps(out1)), out1), (
            "output of %s does not survive a JSON round-trip" % node["id"])
        assert approx_equal(out1, case["expect"]), (node["id"], out1, case["expect"])
