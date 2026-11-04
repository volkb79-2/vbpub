from __future__ import annotations

import json
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys


PATCH_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PATCH_ROOT.parents[2]
sys.path.insert(0, str(REPO_ROOT / "libraries" / "patchseries" / "src"))
from patchseries import check, load_vocabulary, parse


def registry() -> dict[str, dict[str, object]]:
    return json.loads((PATCH_ROOT / "supported-builds.json").read_text())["supported_builds"]


def load_equivalence_proof():
    script = PATCH_ROOT / "scripts" / "prove_series_equivalence.py"
    spec = importlib.util.spec_from_file_location("truenas_series_equivalence_proof", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_registered_adapters_are_valid_ordered_patchseries() -> None:
    vocabulary = load_vocabulary(PATCH_ROOT / "vocabulary.toml")
    for version, row in registry().items():
        series = parse(PATCH_ROOT / row["directory"] / row["series_dir"])
        check(series, vocabulary)
        assert [change.path.name for change in series] == row["patches"], version
        assert all(change.change_id and change.capability and change.role and change.risk for change in series)
        assert len({change.change_id for change in series}) == len(series)


def test_recorded_series_equivalence_covers_every_patched_file() -> None:
    report = json.loads((PATCH_ROOT / "reports/series-equivalence-2026-10-30.json").read_text())
    registered = registry()
    assert report["all_builds_identical"] is True
    assert {row["build"] for row in report["builds"]} == set(registered)
    for proof in report["builds"]:
        row = registered[proof["build"]]
        changes = parse(PATCH_ROOT / row["directory"] / row["series_dir"])
        expected_files = {name for change in changes for name in change.files}
        proof_files = {item["file"] for item in proof["files"]}
        assert proof_files == expected_files
        assert proof_files
        assert proof["patched_file_count"] == len(proof_files)
        unpatched = {item["file"] for item in proof["unpatched_targets"]}
        expected_unpatched = (
            {"middlewared/plugins/zfs/encryption_info.py"}
            if proof["build"] == "28.0.0-MASTER+20261029-175210" else set()
        )
        assert unpatched == expected_unpatched
        assert proof["target_count"] == len(proof_files) + len(unpatched)
        assert proof_files.isdisjoint(unpatched)
        assert all(item["reason"] for item in proof["unpatched_targets"])
        target_files = {
            line.split(maxsplit=1)[1]
            for line in (PATCH_ROOT / row["directory"] / "targets.sha256").read_text().splitlines()
        }
        assert proof_files | unpatched == target_files
        assert proof["all_identical"] is True
        assert all(item["identical"] for item in proof["files"])
        assert all(item["legacy_sha256"] == item["series_sha256"] for item in proof["files"])


def test_equivalence_proof_rejects_two_empty_patch_lists(tmp_path: Path) -> None:
    proof = load_equivalence_proof()
    patch_root = tmp_path / "patch-root"
    adapter_root = patch_root / "empty-build"
    (adapter_root / "series").mkdir(parents=True)
    (adapter_root / "legacy").mkdir()
    stock_root = tmp_path / "stock"
    target = stock_root / "middlewared" / "plugins" / "example.py"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"stock preimage\n")
    manifest = adapter_root / "targets.sha256"
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest.write_text(f"{digest}  middlewared/plugins/example.py\n")
    proof.PATCH_ROOT = patch_root
    row = {
        "directory": "empty-build",
        "target_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "target_count": 1,
        "series_dir": "series",
        "patches": [],
        "legacy_patches": [],
    }

    try:
        proof.build_proof("empty-build", row, stock_root, {})
    except proof.ProofError as exc:
        assert "patch lists must both be non-empty" in str(exc)
    else:
        raise AssertionError("an equivalence row with empty legacy and series patch lists passed")


def test_registry_legacy_patch_order_matches_frozen_pre_series_installers() -> None:
    frozen = json.loads((PATCH_ROOT / "manifest/legacy-installer-order.json").read_text())
    assert frozen["schema_version"] == 1
    assert frozen["source_revision"] == "fe4adad7185129506738f7bf280418739209a679"
    assert frozen["source_note"]
    assert frozen["build_families"]["v27"]["loop_lines"] == [144, 155, 163]
    assert frozen["build_families"]["v28"]["loop_lines"] == [306, 321, 327]
    for version, row in registry().items():
        family = "v27" if version.startswith("27.") else "v28"
        assert row["legacy_patches"] == frozen["build_families"][family]["patch_order"], version


def test_v27_nightly_reapply_allowlist_includes_registered_rc1_bundle_digest() -> None:
    registered = registry()
    nightly = json.loads(
        (PATCH_ROOT / "v27-nightly-20261029" / "adapter.json").read_text()
    )
    rc1_bundle = registered["27.0.0-RC.1"]["install_bundle_sha256"]
    nightly_bundle = registered["27.0.0-MASTER+20261029-173417"]["install_bundle_sha256"]
    assert rc1_bundle in nightly["reapply_from_bundle_sha256s"]
    assert nightly_bundle != rc1_bundle


def test_registered_install_bundle_digests_are_recomputed_from_archives() -> None:
    script = PATCH_ROOT / "scripts" / "check_install_bundle_digests.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=PATCH_ROOT.parents[2],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert sum(line.startswith("PASS ") for line in result.stdout.splitlines()) == len(registry())


def test_generated_series_outline_and_legacy_hunk_bridge_are_current() -> None:
    script = PATCH_ROOT / "scripts" / "generate_series_views.py"
    result = subprocess.run(
        [sys.executable, str(script), "--check"],
        cwd=PATCH_ROOT.parents[2],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    outline = (PATCH_ROOT / "PATCH-SERIES.md").read_text()
    assert "## Capability and role inventory" not in outline
    assert "## Capabilities\n" in outline

    bridge = json.loads((PATCH_ROOT / "manifest/hunk-map.json").read_text())
    assert bridge["runtime_input"] is False
    assert {item["build"] for item in bridge["hunks"]} == set(registry())
    assert all(item["series_change_ids"] for item in bridge["hunks"])
