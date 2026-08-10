#!/usr/bin/env python3
"""P23 tracer: prove the landed P22 calls compose into repeated units.

This is premise evidence, not production code.  It deliberately contrasts a
project-only copy with one prepared base and one whole-blob replacement from
the same P22 seed, then reports only independently observed facts.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from assay.isolation import DEFAULT_SNAPSHOT_LIMITS, SnapshotSpec, prepare_snapshot

GIT = shutil.which("git")
assert GIT is not None


def git_env() -> dict[str, str]:
    return {
        "PATH": os.environ["PATH"],
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "P23 Tracer",
        "GIT_AUTHOR_EMAIL": "p23@assay.invalid",
        "GIT_COMMITTER_NAME": "P23 Tracer",
        "GIT_COMMITTER_EMAIL": "p23@assay.invalid",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
    }


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        [GIT, "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=git_env(),
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr)
    return completed.stdout.strip()


def write(repo: Path, relative: str, data: bytes) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def digest_tree(repo: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(repo.rglob("*")):
        if path.is_file() and ".git" not in path.relative_to(repo).parts:
            digest.update(path.relative_to(repo).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="assay-p23-tracer-") as temp:
        root = Path(temp).resolve()
        repo = root / "consumer"
        scratch = root / "scratch"
        naive = root / "naive-project-copy"
        repo.mkdir()
        scratch.mkdir()
        git(repo, "init", "-q", "-b", "main")
        write(repo, ".gitignore", b"cov.json\ncache/\n")
        write(repo, "apps/p/src/mod.py", b"def check(x):\n    return x > 0\n")
        write(repo, "shared/input.txt", b"tracked sibling\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "fixture")
        commit = git(repo, "rev-parse", "HEAD")
        write(repo, "apps/p/cov.json", b'{"stale":true}\n')
        write(repo, "cache/ignored.txt", b"consumer-only\n")
        before = digest_tree(repo)

        shutil.copytree(repo / "apps/p", naive)
        naive_missing_sibling = not (naive / "../../shared/input.txt").exists()
        naive_has_stale_coverage = (naive / "cov.json").exists()

        spec = SnapshotSpec(
            repo_top=repo,
            commit=commit,
            project_prefix=PurePosixPath("apps/p"),
            scratch_root=scratch,
            limits=DEFAULT_SNAPSHOT_LIMITS,
        )
        original = b"def check(x):\n    return x > 0\n"
        replacement = b"def check(x):\n    return x <= 0\n"
        with prepare_snapshot(spec, timeout=60.0) as prepared:
            seed_bytes = prepared.read_regular_file(
                PurePosixPath("apps/p/src/mod.py"), timeout=60.0
            )
            with prepared.materialize(timeout=60.0) as base:
                base_observed = (base.project_root / "src/mod.py").read_bytes()
                base_sibling = (base.root / "shared/input.txt").read_bytes()
                base_stale_absent = not (base.project_root / "cov.json").exists()
                with prepared.materialize_replacement(
                    path=PurePosixPath("apps/p/src/mod.py"),
                    expected=original,
                    replacement=replacement,
                    timeout=60.0,
                ) as changed:
                    changed_observed = (changed.project_root / "src/mod.py").read_bytes()
                    changed_sibling = (changed.root / "shared/input.txt").read_bytes()
                    independent = (base.project_root / "src/mod.py").read_bytes() == original
                    child_parent = git(changed.root, "rev-parse", "HEAD^")

        evidence = {
            "status": "PASS",
            "commit": commit,
            "seed_read_exact": seed_bytes == original,
            "base_exact": base_observed == original,
            "replacement_exact": changed_observed == replacement,
            "base_unchanged_while_child_live": independent,
            "base_and_child_see_tracked_sibling": base_sibling
            == changed_sibling
            == b"tracked sibling\n",
            "base_starts_without_ignored_coverage": base_stale_absent,
            "child_parent_is_source_commit": child_parent == commit,
            "naive_project_copy_missing_sibling": naive_missing_sibling,
            "naive_project_copy_contains_stale_coverage": naive_has_stale_coverage,
            "consumer_tree_unchanged": digest_tree(repo) == before,
            "scratch_empty_after_contexts": list(scratch.iterdir()) == [],
        }
        if not all(value for key, value in evidence.items() if key not in {"commit", "status"}):
            evidence["status"] = "FAIL"
        print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
        return 0 if evidence["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
