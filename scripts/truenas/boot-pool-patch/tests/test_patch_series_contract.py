from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


PATCH_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PATCH_ROOT.parents[2]
sys.path.insert(0, str(REPO_ROOT / "libraries" / "patchseries" / "src"))
from patchseries import check, load_vocabulary, parse


def registry() -> dict[str, dict[str, object]]:
    return json.loads((PATCH_ROOT / "supported-builds.json").read_text())["supported_builds"]


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
        assert proof["patched_file_count"] == len(proof_files)
        assert proof["all_identical"] is True
        assert all(item["identical"] for item in proof["files"])
        assert all(item["legacy_sha256"] == item["series_sha256"] for item in proof["files"])


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

    bridge = json.loads((PATCH_ROOT / "manifest/hunk-map.json").read_text())
    assert bridge["runtime_input"] is False
    assert {item["build"] for item in bridge["hunks"]} == set(registry())
    assert all(item["series_change_ids"] for item in bridge["hunks"])
