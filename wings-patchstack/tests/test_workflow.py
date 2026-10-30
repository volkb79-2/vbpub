from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WINGS = ROOT / "wings-patchstack"
sys.path.insert(0, str(ROOT / "libraries" / "patchseries" / "src"))

from patchseries import parse, series_digest


def test_wings_gate_uses_tester_unified_and_gate_slice() -> None:
    config = tomllib.loads((WINGS / "run-gate.toml").read_text(encoding="utf-8"))
    estate_config = tomllib.loads(
        (ROOT / "run-gate.root.toml").read_text(encoding="utf-8")
    )
    environment = estate_config["environments"]["tester-unified"]
    lane = config["lanes"]["wings"]

    assert "environments" not in config
    assert environment["mode"] == "ephemeral"
    assert environment["image"] == "tester-unified:local"
    assert environment["image_env"] == "RUN_GATE_TESTER_UNIFIED_IMAGE"
    assert environment["cgroup_slice_env"] == "CGROUP_PARENT_DEV_GATES"
    assert "CGROUP_PARENT_DEV_BACKGROUND" in environment["forward_env"]
    assert environment["resources"]["cpus"] == "3"
    assert lane["environment"] == "tester-unified"
    assert lane["kind"] == "command"

    test_script = (WINGS / "scripts" / "test.sh").read_text(encoding="utf-8")
    common_script = (WINGS / "scripts" / "common.sh").read_text(encoding="utf-8")
    worker_script = (WINGS / "scripts" / "run-tests-in-gate.sh").read_text(
        encoding="utf-8"
    )
    assert "run-gate" in test_script
    assert "docker run" not in test_script
    assert "go_in_container" not in common_script
    assert "docker run" not in common_script
    assert "INTEGRATION=1" in test_script
    assert "RUN_GATE_EXTRA_MOUNTS" in test_script
    assert "DOCKER_HOST" in test_script
    assert "! -S /var/run/docker.sock" in worker_script


def test_assay_coverage_base_matches_shallow_clone_tip() -> None:
    assay = tomllib.loads(
        (WINGS / "assay" / "assay.toml").read_text(encoding="utf-8")
    )
    stack = (WINGS / "stack.conf").read_text(encoding="utf-8")
    clone_script = (WINGS / "scripts" / "clone.sh").read_text(encoding="utf-8")
    ref_match = re.search(r'^PTERODACTYL_REF="([^"]+)"$', stack, re.MULTILINE)

    assert ref_match is not None
    assert assay["lanes"]["coverage"]["judge"]["base"] == ref_match.group(1)
    assert 'git clone --branch "$REF" --depth 50' in clone_script


def test_ledger_current_series_identity_matches_active_patch_bytes() -> None:
    ledger = json.loads((WINGS / "image-ledger.json").read_text(encoding="utf-8"))
    current = ledger["current_series"]
    active = parse(WINGS / "patches" / "pterodactyl-v1.13.4")

    assert current["upstream_version"] == "1.13.4"
    assert current["upstream_commit"] == "747bc4393daf5d95fdf61d76382f5e23749cffe9"
    assert current["series_digest"] == series_digest(
        active, current["upstream_commit"]
    )

    expected_builds = {
        ("1.13.3", "cgroup.7"): (
            "3f9df8045b72c0346a0a2bc9aeac7fa1af1fb033e3e395d7c568ab22fae2558b",
            "358e0116e1ceca1296daa156ea2b4e82ebf0719197d9731c6ee950523d028d66",
        ),
        ("1.13.4", "cgroup.1"): (
            "fbe1fcddd19c59127f45d3afb923c532a15d054dfdd23f7d3f6c398c48838dec",
            "923d9296e08ab5dcbaaec6a722625c99f0e14a968f424685e8c48dfc452257c7",
        ),
    }
    actual_builds = {
        (item["upstream"], item["suffix"]): (
            item["series_digest"],
            item["image_label_series_digest"],
        )
        for item in ledger["builds"]
    }
    assert actual_builds == expected_builds
    upstream_commits = {
        "1.13.3": "6987d5e6f0612133e031255a99655e822d30579a",
        "1.13.4": "747bc4393daf5d95fdf61d76382f5e23749cffe9",
    }
    for item in ledger["builds"]:
        series = parse(WINGS / "patches" / f"pterodactyl-v{item['upstream']}")
        assert item["series_digest"] == series_digest(
            series, upstream_commits[item["upstream"]]
        )
    assert ledger["reservations"] == []


def test_ledger_has_one_build_record_per_upstream_suffix() -> None:
    ledger = json.loads((WINGS / "image-ledger.json").read_text(encoding="utf-8"))
    keys = [(item["upstream"], item["suffix"]) for item in ledger["builds"]]

    assert len(keys) == len(set(keys))


def _heading_slug(heading: str) -> str:
    text = heading.strip().lower()
    text = re.sub(r"[`*]", "", text)
    text = re.sub(r"[^a-z0-9 -]", "", text)
    return re.sub(r"\s+", "-", text)


def test_wings_user_docs_cover_closed_vocabulary_and_current_config_schema() -> None:
    docs = [WINGS / name for name in ("README.md", "DESIGN-GUIDE.md", "CONSUMERS.md")]
    content = "\n".join(path.read_text(encoding="utf-8") for path in docs)
    vocabulary = tomllib.loads((WINGS / "vocabulary.toml").read_text(encoding="utf-8"))
    series = tomllib.loads((WINGS / "run-gate.toml").read_text(encoding="utf-8"))

    for values in (vocabulary["capabilities"], vocabulary["roles"], vocabulary["risks"]):
        for value in values:
            assert f"`{value}`" in content
    for value in ("active", "proposal", "archived", "pterodactyl", "pelican"):
        assert f"`{value}`" in content

    assert series["schema_version"] == 1
    assert set(series["lanes"]) == {"wings", "coverage"}

    for doc in docs:
        text = doc.read_text(encoding="utf-8")
        for match in re.finditer(r"```toml\s*\n(.*?)\n```", text, re.DOTALL):
            example = tomllib.loads(match.group(1))
            assert example["schema_version"] == series["schema_version"]


def test_wings_user_doc_links_and_anchors_resolve() -> None:
    docs = [WINGS / name for name in ("README.md", "DESIGN-GUIDE.md", "CONSUMERS.md")]
    for doc in docs:
        content = doc.read_text(encoding="utf-8")
        links = re.findall(r"\[[^]]+\]\(([^)]+)\)", content)
        for destination in links:
            target, separator, anchor = destination.partition("#")
            target = target.split(maxsplit=1)[0]
            if target.startswith(("https://", "http://", "mailto:")):
                continue
            target_path = (doc.parent / target).resolve()
            assert target_path.exists(), f"{doc.name}: missing linked target {target}"
            if separator:
                assert target_path.is_file(), (
                    f"{doc.name}: anchor target is not a file {target}"
                )
                headings = {
                    _heading_slug(match.group(1))
                    for match in re.finditer(
                        r"^#{1,6}\s+(.+?)\s*#*\s*$",
                        target_path.read_text(encoding="utf-8"),
                        re.MULTILINE,
                    )
                }
                assert anchor in headings, (
                    f"{doc.name}: missing anchor {target}#{anchor}"
                )


def _fake_git(directory: Path) -> Path:
    executable = directory / "git"
    executable.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "[ \"$1\" = format-patch ] || exit 90\n"
        "shift\n"
        "[ \"$1\" = -o ] || exit 91\n"
        "output=\"$2\"\n"
        "mkdir -p \"$output\"\n"
        "cat > \"$output/0001-new.patch\" <<'PATCH'\n"
        "From 0123456789abcdef0123456789abcdef01234567 Mon Sep 17 00:00:00 2001\n"
        "From: Author <author@example.test>\n"
        "Subject: [PATCH] New patch\n\n"
        "New patch body.\n\n"
        "Change-Id: new-patch\n"
        "Capability: node-placement\n"
        "Role: enable\n"
        "Risk: low\n"
        "Requires: none\n\n"
        "---\n"
        "diff --git a/pkg/file.go b/pkg/file.go\n"
        "--- a/pkg/file.go\n"
        "+++ b/pkg/file.go\n"
        "@@ -1 +1 @@\n"
        "-before\n"
        "+after\n"
        "PATCH\n"
        "if [ \"${FAIL_FORMAT_PATCH:-0}\" = 1 ]; then\n"
        "  printf 'partial output\\n' > \"$output/0002-partial.patch\"\n"
        "  exit 9\n"
        "fi\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def _export_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    wings = repo / "wings-patchstack"
    scripts = wings / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(WINGS / "scripts" / "common.sh", scripts / "common.sh")
    shutil.copy2(WINGS / "scripts" / "export-patches.sh", scripts / "export-patches.sh")
    shutil.copy2(WINGS / "stack.conf", wings / "stack.conf")
    shutil.copy2(WINGS / "vocabulary.toml", wings / "vocabulary.toml")
    library_source = repo / "libraries" / "patchseries" / "src"
    shutil.copytree(ROOT / "libraries" / "patchseries" / "src", library_source)

    source = repo / "wings-cgroups" / "build" / "wings-pterodactyl"
    source.mkdir(parents=True)
    destination = wings / "patches" / "pterodactyl-v1.13.4"
    destination.mkdir(parents=True)
    (destination / "0001-old.patch").write_text("old committed series\n", encoding="utf-8")
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    _fake_git(fake_bin)
    return repo, destination, fake_bin


def _run_export(repo: Path, fake_bin: Path, *, fail: bool) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "PYTHONPATH": str(repo / "libraries" / "patchseries" / "src"),
        "FAIL_FORMAT_PATCH": "1" if fail else "0",
    }
    return subprocess.run(
        ["bash", str(repo / "wings-patchstack" / "scripts" / "export-patches.sh")],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )


def test_export_failure_preserves_existing_series_and_discards_partial_output(
    tmp_path: Path,
) -> None:
    repo, destination, fake_bin = _export_fixture(tmp_path)

    result = _run_export(repo, fake_bin, fail=True)

    assert result.returncode != 0
    assert (destination / "0001-old.patch").read_text(encoding="utf-8") == (
        "old committed series\n"
    )
    assert not (destination / "0001-new.patch").exists()
    assert not (destination / "0002-partial.patch").exists()
    assert list(destination.parent.glob(f".{destination.name}.*")) == []


def test_export_replaces_series_only_after_validated_generation(tmp_path: Path) -> None:
    repo, destination, fake_bin = _export_fixture(tmp_path)

    result = _run_export(repo, fake_bin, fail=False)

    assert result.returncode == 0, result.stderr
    assert not (destination / "0001-old.patch").exists()
    assert (destination / "0001-new.patch").is_file()
    assert list(destination.parent.glob(f".{destination.name}.*")) == []
