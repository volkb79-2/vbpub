"""Independent locked acceptance for P25's qualification contract.

The quick suite checks the immutable packet, complete-artifact comparator, and
production promotion/wiring.  The registered tester-unified gate owns the live
2,923-test Topos execution; this suite never substitutes a cockpit run for it.
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ASSET_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = Path(os.environ.get("ASSAY_P25_PROJECT_ROOT", ASSET_ROOT.parents[2])).resolve()
REPO_ROOT = PROJECT_ROOT.parent
PRODUCTION_HARNESS = PROJECT_ROOT / "gate/python/qualify_topos.py"
PRODUCTION_FIXTURES = PROJECT_ROOT / "gate/python/fixtures/P25"
PRODUCTION_RELEASE = PROJECT_ROOT / "gate/python/release/P25"
SKELETON = ASSET_ROOT / "skeleton/qualify_topos.py"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_skeleton():
    spec = importlib.util.spec_from_file_location("p25_locked_skeleton", SKELETON)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.FIXTURE_ROOT = ASSET_ROOT / "fixtures"
    module.RELEASE_ROOT = ASSET_ROOT / "release"
    return module


def test_locked_asset_hashes() -> None:
    document = json.loads((ASSET_ROOT / "fixture-manifest.json").read_text())
    assert document["schema_version"] == 1
    for relative, expected in document["files"].items():
        assert sha256(ASSET_ROOT / relative) == expected, relative


def test_skeleton_compiles_and_freezes_the_public_shapes() -> None:
    subprocess.run([sys.executable, "-m", "py_compile", str(SKELETON)], check=True)
    module = load_skeleton()
    assert module.INPUT_REVISION == "9f522a72d37b9cb5beb1939ceca1978c9fc4ef23"
    assert module.TOPOS_TREE == "1bc8a51296b74e536bf60b534efb2fc938dcc389"
    assert module.RELEASE_SHA256 == "a0f8d28e4f6359e90616343badcf3c663eb7e2075c1a521bf9da8afd7002dc86"
    assert tuple(spec.name for spec in module.SCENARIOS) == (
        "current-full-pass",
        "release-targeted-pass",
        "missing-line",
        "excluded-forbidden",
        "comment-only",
    )


def test_the_pinned_topos_and_release_inputs_are_reachable_and_exact() -> None:
    module = load_skeleton()
    module.verify_pinned_inputs(REPO_ROOT)
    input_manifest = json.loads((ASSET_ROOT / "topos-input-manifest.json").read_text())
    names = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-tree", "-r", "--name-only", module.INPUT_REVISION, "--", "topos"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.splitlines()
    assert len(names) == input_manifest["topos_tracked_entries"] == 966


def _actual_from_template(template_name: str) -> tuple[dict, dict[str, object]]:
    template = json.loads((ASSET_ROOT / "expected" / template_name).read_text())
    actual = json.loads(json.dumps(template))
    substitutions: dict[str, object] = {
        "@ASSAY_VERSION@": "1.2.5",
        "@HEAD_OID@": "1" * 40,
        "@BASE_OID@": "2" * 40,
        "@STARTED@": "2026-08-10T00:00:00+00:00",
        "@ENDED@": "2026-08-10T00:01:00+00:00",
        "@WITNESS_PATH@": "/tmp/p25/witness.json",
        "@PYTEST_LOG@": "/tmp/p25/pytest.log",
    }

    def replace(value):
        if isinstance(value, str) and value in substitutions:
            return substitutions[value]
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        return value

    actual = replace(actual)
    return actual, substitutions


def test_complete_pass_artifact_template_is_independently_comparable() -> None:
    module = load_skeleton()
    actual, values = _actual_from_template("pass-v4-template.json")
    module.compare_complete_artifact(
        actual=actual,
        template=ASSET_ROOT / "expected/pass-v4-template.json",
        assay_version=values["@ASSAY_VERSION@"],
        base_oid=values["@BASE_OID@"],
        head_oid=values["@HEAD_OID@"],
        witness=Path(values["@WITNESS_PATH@"]),
        pytest_log=Path(values["@PYTEST_LOG@"]),
    )


@pytest.mark.parametrize(
    ("field", "wrong", "message"),
    [
        ("assay_version", "wrong", "assay_version"),
        ("commit", "0" * 40, "commit"),
        ("started", "", "started"),
    ],
)
def test_complete_artifact_comparison_rejects_identity_corruption(
    field: str, wrong: str, message: str
) -> None:
    module = load_skeleton()
    actual, values = _actual_from_template("pass-v4-template.json")
    actual[field] = wrong
    with pytest.raises(module.QualificationError, match=message):
        module.normalize_artifact(
            actual,
            assay_version=values["@ASSAY_VERSION@"],
            base_oid=values["@BASE_OID@"],
            head_oid=values["@HEAD_OID@"],
            witness=Path(values["@WITNESS_PATH@"]),
            pytest_log=Path(values["@PYTEST_LOG@"]),
        )


def test_line_manifests_are_literal_and_distinguish_pass_fail_and_zero_over_zero() -> None:
    document = json.loads((ASSET_ROOT / "qualification-manifest.json").read_text())
    assert document["pass_probe"]["executed_lines"] == [4, 5, 6, 7, 10]
    assert document["missing_probe"]["missing_lines"] == [7]
    assert document["comment_only_probe"] == {
        "path": "topos/src/topos/_assay_comment.py",
        "changed_executable": 0,
        "covered": 0,
        "terminal": "PASS",
    }


def test_reference_probe_records_real_full_suite_and_three_way_results() -> None:
    document = json.loads((ASSET_ROOT / "probe-results.json").read_text())
    assert document["full_suite"] is True
    scenarios = {item["scenario"]: item for item in document["scenarios"]}
    assert scenarios["pass"]["exit_code"] == 0
    assert scenarios["pass"]["comparator"]["passed"] is True
    assert scenarios["missing"]["reason_code"] == "UNCOVERED_LINES"
    assert scenarios["missing"]["comparator"]["uncovered"] == {
        "topos/src/topos/_assay_probe.py": [7]
    }
    assert scenarios["excluded-forbidden"]["reason_code"] == "EXCLUDED_LINES"
    assert scenarios["excluded-forbidden"]["comparator"]["passed"] is True
    assert scenarios["comment-only"]["comparator"]["changed_executable"] == 0


def test_production_harness_promotes_the_skeleton_without_todos() -> None:
    assert PRODUCTION_HARNESS.is_file()
    source = PRODUCTION_HARNESS.read_text(encoding="utf-8")
    assert "NotImplementedError" not in source
    for name in (
        "install_locked_release",
        "materialize_scenario",
        "run_scenario",
        "normalize_artifact",
        "compare_complete_artifact",
        "qualify",
    ):
        assert f"def {name}(" in source


def test_production_fixture_and_release_promotions_are_byte_exact() -> None:
    for relative in (
        "comment-only.py",
        "coverage-witness.py",
        "probe-missing.py",
        "probe-pass.py",
        "test-comment-only.py",
        "test-probe-missing.py",
        "test-probe-pass.py",
    ):
        assert (PRODUCTION_FIXTURES / relative).read_bytes() == (
            ASSET_ROOT / "fixtures" / relative
        ).read_bytes()
    for relative in ("assay-1.2.5-py3-none-any.whl", "release-manifest.json"):
        assert (PRODUCTION_RELEASE / relative).read_bytes() == (
            ASSET_ROOT / "release" / relative
        ).read_bytes()


def test_registered_gate_uses_current_wheel_and_emits_the_p25_marker() -> None:
    source = (PROJECT_ROOT / "tools/tester-unified-gate.sh").read_text(encoding="utf-8")
    assert 'gate/python/qualify_topos.py' in source
    assert '--current-assay' in source and '$scratch/run-venv/bin/assay' in source
    assert "ASSAY_GATE_PHASE=topos-qualified" in source
    inner = source.split("run_inner() {", 1)[1].split("# --- entry points", 1)[0]
    assert inner.index("run_self_hosted_lane") < inner.index("gate/python/qualify_topos.py")
    assert inner.index("gate/python/qualify_topos.py") < inner.index("run_independent_witness")


def test_no_production_route_selects_an_alternate_wheel_or_topos_outer_docker() -> None:
    if not PRODUCTION_HARNESS.exists():
        pytest.fail("production harness is absent")
    source = PRODUCTION_HARNESS.read_text(encoding="utf-8")
    assert ".glob(" not in source
    assert "nyxloom-gates.slice" not in source
    assert "/home/vb/volkb79-2/vbpub" not in source
    assert "docker run" not in source
    assert "topos-v" not in source
