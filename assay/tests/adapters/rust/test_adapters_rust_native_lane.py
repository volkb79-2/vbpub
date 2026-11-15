"""A native ``kind = "assay"`` Rust R1 lane reaches the Rust LCOV normalizer.

The baseline unit parses the lane's coverage artifact before ``evaluate_r1``
sees it, so the adapter's ``normalize_coverage_text`` has to be wired into that
path as well as into ``evaluate_r1``'s own fallback parse. These tests drive
the whole CLI so dropping either wire fails here.
"""

from __future__ import annotations

import io
import json

from conftest import GitRepo

from assay.cli import main

# Hand-written marker-only LCOV in the shape stable cargo-llvm-cov writes (the
# measured artifact is docs/rust-r0/evidence/s2-sampler-lcov.info); this test
# targets the wiring, not the producer.
MARKER_ONLY_LCOV = (
    "SF:src/lib.rs\\n"
    "DA:1,1\\nDA:2,1\\nDA:5,1\\nDA:6,1\\n"
    "LF:4\\nLH:4\\nBRF:0\\nBRH:0\\nend_of_record\\n"
)


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def _rust_lane(git_repo: GitRepo, *, require_branch: bool):
    (git_repo.path / "src").mkdir()
    git_repo.write(".gitignore", "lcov.info\n")
    git_repo.write("src/lib.rs", "pub fn f() -> u8 {\n    1\n}\n")
    base_rev = git_repo.commit_all("add lib.rs")
    git_repo.write(
        "src/lib.rs",
        "pub fn f() -> u8 {\n    1\n}\n\npub fn g() -> u8 {\n    2\n}\n",
    )
    git_repo.commit_all("add g")
    command = f"printf '{MARKER_ONLY_LCOV}' > lcov.info"
    lane = f"""\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R1"]
enforcement = "gate"
argv = ["/bin/sh", "-c", {json.dumps(command)}]
env = {{}}
env_passthrough = ["PATH"]
budget = "1m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "rust"
source_roots = ["src"]
fail_under = 100.0
allow_excluded = false
require_branch = {"true" if require_branch else "false"}
coverage = {{ format = "lcov", artifact = "lcov.info" }}
base = "{base_rev}"
"""
    path = git_repo.write("assay.toml", lane)
    git_repo.commit_all("add assay.toml")
    return path


def test_native_rust_lane_normalizes_marker_only_branch_totals(git_repo: GitRepo):
    path = _rust_lane(git_repo, require_branch=False)

    code, out, err = _run(["run", "package", "--file", str(path), "--verdict-json", "-"])

    assert code == 0, err
    document = json.loads(out)
    assert document["outcome"] == "PASS"
    coverage = document["claims"][1]["coverage"]
    assert coverage["pct"] == 100.0
    assert coverage["branch_capability"] == "unavailable"
    assert coverage["branches_total"] == 0


def test_native_rust_lane_refuses_require_branch_on_marker_only_totals(git_repo: GitRepo):
    path = _rust_lane(git_repo, require_branch=True)

    code, out, err = _run(["run", "package", "--file", str(path), "--verdict-json", "-"])

    assert code != 0, err
    document = json.loads(out)
    assert document["outcome"] == "NO_MEASUREMENT"
    r1 = document["claims"][1]
    assert r1["reason_code"] == "BRANCH_UNAVAILABLE"
