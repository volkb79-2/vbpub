"""The release config publishes exactly the variants and retained inputs declared."""
from __future__ import annotations

import fnmatch
import tomllib

from cmru.bundle import parse_config as parse_bundle_config
from cmru.config import load_forge_config
from conftest import ROOT

CONFIG = tomllib.loads((ROOT / "cmru.toml").read_text(encoding="utf-8"))
ORCHESTRATION = ROOT.parent / "cmru.orchestration.toml"


def _load_tls_edge(monkeypatch) -> object:
    monkeypatch.setenv("CGROUP_PARENT_DEV_GATES", "test.slice")
    forge = load_forge_config(ORCHESTRATION, require_orchestration=True)
    return forge.projects["tls-edge"]


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


def test_the_installer_is_published_beside_the_bundles_from_the_build_output(monkeypatch):
    argv = _publish_argv()
    assert argv[argv.index("--extra-asset") + 1] == "dist/get.py"
    assert argv[argv.index("--version-file") + 1] == "dist/VERSION"
    project = _load_tls_edge(monkeypatch)
    assert project.artifact_dirs == ["dist"]
    build = project.steps["build"]
    assert build["clean_dirs"] == ["dist"]
    assert build["required_env"] == ["TLS_EDGE_VERSION"]
    assert build["env_command"] == ["python3", "tools/bundle_wheels.py", "version-env"]
    assert build["commands"][0]["argv"][:3] == ["cmru", "handler", "bundle-build"]
    assert build["commands"][1]["argv"] == [
        "python3", "tools/bundle_wheels.py", "retain", "dist",
    ]


def test_bundle_build_names_reproducible_variant_archives_from_one_variant_table(monkeypatch):
    bundle = parse_bundle_config(ROOT / "bundle.toml")
    project = _load_tls_edge(monkeypatch)

    assert bundle.archive_template == "tls-edge-v{version}-{variant}.tar.xz"
    assert bundle.archive_version_env == "TLS_EDGE_VERSION"
    assert bundle.archive_format == "xztar"
    assert bundle.vendor is not None
    assert bundle.vendor.requirements == ["requirements-bundle.txt"]
    assert bundle.vendor.dest == "wheels"
    assert bundle.stage is not None
    assert "{variant}" in " ".join(bundle.stage.argv)
    assert [variant.name for variant in project.variants] == ["py313", "py314"]
    assert [variant.build_arg for variant in project.variants] == [
        "PYTHON_VERSION=3.13", "PYTHON_VERSION=3.14",
    ]


def test_bundle_copy_allowlist_is_operator_only():
    bundle = parse_bundle_config(ROOT / "bundle.toml")

    assert bundle.copy_files == [
        "VERSION",
        "get.py",
        "README.md",
        "ARCHITECTURE.md",
        "CONSUMER_GUIDE.md",
        "KNOWN_ISSUES_TODO_BACKLOG.md",
        "ciu.global.defaults.toml.j2",
        "packaging/instance-root/.gitignore",
    ]
    assert bundle.copy_dirs == [
        "ciu-stack", "consumer-examples", "docs", "installer", "scripts",
    ]
    assert sorted(path.name for path in (ROOT / "scripts").iterdir() if path.is_file()) == [
        "certbot-deploy-hook.sh",
        "dev-certs.sh",
        "gen-guard-secret.sh",
        "verify-external.sh",
        "verify.sh",
    ]


def test_asset_suffix_matches_the_installer_asset_suffix():
    argv = _publish_argv()
    suffix = argv[argv.index("--asset-suffix") + 1] if "--asset-suffix" in argv else ".tar.xz"
    assert suffix == CONFIG["project"]["installer"]["asset_suffix"]


def test_publish_glob_matches_every_variant_but_not_the_retired_single_asset_name():
    glob = _publish_argv()[_publish_argv().index("--glob") + 1]
    variants = [variant["name"] for variant in CONFIG["project"]["variants"]]

    assert all(fnmatch.fnmatchcase(f"tls-edge-v4.0.0-{name}.tar.xz", glob) for name in variants)
    assert not fnmatch.fnmatchcase("tls-edge-v4.0.0.tar.xz", glob)


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
