#!/usr/bin/env python3
"""Print CMRU_ASSAY_BASELINE_FACTS from LOCAL cmru-v* tags (no origin access).

The release flow builds these facts from an authenticated `git ls-remote`
(prepare_assay_baseline.py). A mutation campaign started OUTSIDE a release
(e.g. `run-gate mutation` on a host with no publisher credentials) has no such
query; this helper builds the identical fact document from the checkout's own
tags, which must have been fetched from origin first (`git fetch origin
'refs/tags/cmru-v*:refs/tags/cmru-v*'`). It is for out-of-release R2 campaigns
only: the release gate never uses it and keeps the authenticated query.

Usage, from any directory inside the checkout being judged:

    export CMRU_ASSAY_BASELINE_FACTS="$(python3 -I cmru/tools/local_baseline_facts.py)"
    run-gate --worktree <checkout> mutation

The facts bind to the checkout's HEAD, and the lane additionally requires
cmru/assay.toml's `[lanes.cmru.judge] base` to equal the selected tag (the
highest published cmru-v* ancestor of HEAD). Advance that pin in a commit on the
campaign checkout first when it lags (see cmru/docs/CONSUMERS.md).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROJECT_ROOT / "tools"), str(PROJECT_ROOT.parent / "assay" / "src")]

import prepare_assay_baseline  # noqa: E402
from assay import git as assay_git  # noqa: E402


def local_tag_commits(repo_root: Path) -> dict[str, str]:
    """Map every local cmru-v* tag to its peeled commit."""
    listing = subprocess.run(
        ["git", "-C", str(repo_root), "for-each-ref",
         "--format=%(refname:strip=2) %(*objectname) %(objectname)",
         "refs/tags/cmru-v*"],
        capture_output=True, text=True, check=True,
    ).stdout
    tags: dict[str, str] = {}
    for line in listing.splitlines():
        name, peeled, plain = (line.split(" ") + ["", ""])[:3]
        tags[name] = peeled or plain
    return tags


def main() -> int:
    try:
        repo_root = assay_git.repo_top(PROJECT_ROOT)
        facts = prepare_assay_baseline.build_facts(
            repo_root, PROJECT_ROOT, assay_git=assay_git,
            remote_tag_commits=local_tag_commits(repo_root),
        )
    except Exception as exc:  # noqa: BLE001 - one-line operator diagnostic
        print(f"local-baseline-facts: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(facts, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
