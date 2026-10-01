"""W7 (B128): a ``snapshot_history = "shallow"`` snapshot really is shallow.

The two B105 lanes moved from ``"full"`` to ``"shallow"``; this is the negative
proof that the shallow mode hides ancestry.  A two-commit repository is
snapshotted through the isolation API and ``git rev-parse HEAD~1`` must fail
inside the snapshot.  It would pass (and this test go red) were the snapshot
full, so it cannot be satisfied by a snapshot that silently kept its history.
"""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath

from assay.config import IsolationConfig
from assay.isolation import DEFAULT_SNAPSHOT_LIMITS, SnapshotSpec, prepare_snapshot

TIMEOUT = 600.0

#: The mode under test, named once so the deliberate "force full" break is a
#: one-word change.
HISTORY = "shallow"


def test_shallow_snapshot_has_no_parent_of_head(git_repo, tmp_path: Path) -> None:
    git_repo.write("second.txt", "second\n")
    head = git_repo.commit_all("second")
    # The source repository itself has the parent: the refusal below is the
    # snapshot's doing, not a repository without history.
    git_repo.git("rev-parse", "--verify", "HEAD~1")

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    policy = IsolationConfig(
        snapshot_selection="repository",
        unsafe_symlink_omissions=(),
        snapshot_history=HISTORY,
    )
    spec = SnapshotSpec(
        repo_top=git_repo.path.resolve(),
        commit=head,
        project_prefix=PurePosixPath("."),
        scratch_root=scratch.resolve(),
        snapshot_policy=policy,
        limits=DEFAULT_SNAPSHOT_LIMITS,
    )

    with prepare_snapshot(spec, timeout=TIMEOUT) as prepared:
        with prepared.materialize(timeout=TIMEOUT) as snapshot:
            resolved_head = subprocess.run(
                ["git", "-C", str(snapshot.root), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
            )
            assert resolved_head.stdout.strip() == head
            parent = subprocess.run(
                ["git", "-C", str(snapshot.root), "rev-parse", "HEAD~1"],
                capture_output=True,
                text=True,
            )
            assert parent.returncode != 0, (
                "a shallow snapshot must not resolve HEAD~1, got "
                f"{parent.stdout.strip()!r}"
            )
