"""The release config publishes exactly the variants the project declares (cmru KI-115)."""
from __future__ import annotations

import tomllib

from conftest import ROOT

CONFIG = tomllib.loads((ROOT / "cmru.toml").read_text(encoding="utf-8"))


def _publish_argv() -> list[str]:
    commands = CONFIG["steps"]["push"]["commands"]
    assert len(commands) == 1
    return commands[0]["argv"]


def test_push_step_is_the_cmru_variants_handler_not_a_project_script():
    argv = _publish_argv()
    assert argv[:3] == ["cmru", "handler", "tarball-publish"]
    assert "scripts/publish-variants.py" not in " ".join(argv)
    assert not (ROOT / "scripts" / "publish-variants.py").exists()


def test_handler_variants_repeat_the_declared_variants_with_their_labels():
    argv = _publish_argv()
    specs = [argv[i + 1] for i, item in enumerate(argv) if item == "--variant"]
    declared = [f"{v['name']}={v['label']}" for v in CONFIG["project"]["variants"]]
    assert specs == declared
    assert argv[argv.index("--prefix") + 1] == CONFIG["project"]["prefix"].removesuffix("-v")


def test_the_installer_is_published_beside_the_bundles_from_the_build_output():
    argv = _publish_argv()
    assert argv[argv.index("--extra-asset") + 1] == "dist/get.py"
    script = (ROOT / "scripts" / "build-artifact.sh").read_text(encoding="utf-8")
    assert 'cp -p "$TLS_EDGE_ROOT/get.py" "$DIST_DIR/get.py"' in script
    assert CONFIG["project"]["release"]["artifact_dirs"] == ["dist"]


def test_asset_suffix_matches_the_installer_asset_suffix():
    argv = _publish_argv()
    suffix = argv[argv.index("--asset-suffix") + 1] if "--asset-suffix" in argv else ".tar.xz"
    assert suffix == CONFIG["project"]["installer"]["asset_suffix"]


def test_cmru_release_runs_the_registered_r0_r1_r3_gate():
    commands = CONFIG["steps"]["run-tests"]["commands"]
    assert commands == [{
        "label": "tls-edge: R0/R1/R3 release gate",
        "argv": ["run-gate", "release-gate"],
        "cwd": ".",
    }]

    gate = tomllib.loads((ROOT / "run-gate.toml").read_text(encoding="utf-8"))
    assert gate["lanes"]["release-gate"]["kind"] == "assay"
    assert gate["lanes"]["release-gate"]["assay_lane"] == "release-gate"

    assay = tomllib.loads((ROOT / "assay.toml").read_text(encoding="utf-8"))
    lane = assay["lanes"]["release-gate"]
    assert lane["rigor"] == ["R0", "R1", "R3"]
    assert lane["judge"]["mode"] == "whole_target"
    assert lane["judge"]["fail_under"] == 100.0
    assert lane["judge"]["require_branch"] is True
    assert lane["judge"]["canary"]["mechanism"] == "import-break"
