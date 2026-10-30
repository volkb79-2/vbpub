"""Every live in-repo Assay consumer config must load with this checkout's loader.

A loader change (for example the native-R2 ``jobs`` rule) must not silently break another
vbpub project's lane. "Live" means the config sits next to a tracked ``run-gate.toml``: demo,
documentation and legacy ``assay.toml`` files name source roots of other repositories and can
never load here, so including them would make the guard fail on a healthy estate.
"""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath

from assay.config import load_lane_file
from assay.errors import LaneConfigError

REPO_ROOT = Path(__file__).resolve().parents[3]


def live_consumer_configs(tracked: list[str]) -> list[str]:
    """Tracked ``assay.toml`` paths whose directory also tracks a ``run-gate.toml``."""
    gate_dirs = {
        PurePosixPath(path).parent for path in tracked if PurePosixPath(path).name == "run-gate.toml"
    }
    return sorted(
        path
        for path in tracked
        if PurePosixPath(path).name == "assay.toml" and PurePosixPath(path).parent in gate_dirs
    )


def _tracked_files() -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "-z"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    ).stdout
    return [raw.decode("utf-8") for raw in listed.split(b"\0") if raw]


def test_live_consumer_filter_keeps_gated_projects_and_drops_demo_and_legacy_configs():
    tracked = [
        "scripts/debian-install-v2/assay.toml",
        "scripts/debian-install-v2/run-gate.toml",
        "ciu/docs/v8-dstdns-demo/assay.toml",
        "wings-cgroups/v1-legacy/patchstack/assay/assay.toml",
        "assay/README.md",
    ]
    assert live_consumer_configs(tracked) == ["scripts/debian-install-v2/assay.toml"]


def test_every_live_in_repo_assay_toml_loads_with_this_source_tree():
    configs = live_consumer_configs(_tracked_files())
    # The scan must not pass vacuously: this repository has well over a handful of gated consumers.
    assert len(configs) >= 8, configs
    assert "scripts/debian-install-v2/assay.toml" in configs

    failures: list[str] = []
    for relative in configs:
        try:
            load_lane_file(REPO_ROOT / relative)
        except LaneConfigError as exc:
            failures.append(f"{relative}: {exc}")

    assert not failures, "in-repo Assay consumer configs no longer load:\n" + "\n".join(failures)
