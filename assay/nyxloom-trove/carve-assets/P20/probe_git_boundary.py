"""P20 tracer bullet for the proposed sanitized Git process boundary."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

ENV = {
    "LC_ALL": "C.UTF-8",
    "LANG": "C.UTF-8",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_ATTR_NOSYSTEM": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_PAGER": "",
    "PAGER": "",
}


def _git_executable() -> Path:
    declared_path = os.environ.get("PATH")
    if declared_path is None:
        raise RuntimeError("probe caller did not declare PATH")
    found = shutil.which("git", path=declared_path)
    if found is None:
        raise RuntimeError("git is absent from the caller-declared PATH")
    resolved = Path(found).resolve(strict=True)
    mode = resolved.stat().st_mode
    if not resolved.is_absolute() or not stat.S_ISREG(mode) or not os.access(resolved, os.X_OK):
        raise RuntimeError(f"resolved git is not an absolute regular executable: {resolved}")
    return resolved


def _boundary(git: Path, repo: Path, *args: str) -> bytes:
    repo_top = repo.resolve()
    git_dir = (repo_top / ".git").resolve(strict=True)
    argv = [
        str(git),
        "--no-pager",
        "--no-optional-locks",
        "--literal-pathspecs",
        f"--git-dir={git_dir}",
        f"--work-tree={repo_top}",
        "-c",
        "core.quotePath=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=",
        "-c",
        "commit.gpgSign=false",
        "-C",
        str(repo_top),
        *args,
    ]
    proc = subprocess.run(argv, env=ENV, capture_output=True)
    if proc.returncode:
        raise RuntimeError(
            f"boundary command failed {proc.returncode}: {proc.stderr[:400]!r}"
        )
    return proc.stdout


def _seed(git: Path, root: Path, name: str) -> tuple[str, str]:
    subprocess.run([str(git), "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(
        [str(git), "-C", str(root), "config", "user.name", "P20 probe"], check=True
    )
    subprocess.run(
        [str(git), "-C", str(root), "config", "user.email", "p20@example.invalid"],
        check=True,
    )
    (root / f"{name}.txt").write_text(f"{name}-base\n", encoding="utf-8")
    subprocess.run([str(git), "-C", str(root), "add", "-A"], check=True)
    subprocess.run([str(git), "-C", str(root), "commit", "-q", "-m", "base"], check=True)
    base = subprocess.check_output([str(git), "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    (root / f"{name}.txt").write_text(f"{name}-head\n", encoding="utf-8")
    subprocess.run([str(git), "-C", str(root), "add", "-A"], check=True)
    subprocess.run([str(git), "-C", str(root), "commit", "-q", "-m", "head"], check=True)
    head = subprocess.check_output([str(git), "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    return base, head


def main() -> int:
    git = _git_executable()
    with tempfile.TemporaryDirectory(prefix="assay-p20-git-probe-") as raw:
        root = Path(raw)
        repo_a = root / "repo-a"
        repo_b = root / "repo-b"
        base_a, head_a = _seed(git, repo_a, "alpha")
        _, head_b = _seed(git, repo_b, "beta")

        sentinel = root / "external-diff-ran"
        external = root / "external-diff"
        external.write_text(f"#!/bin/sh\ntouch '{sentinel}'\nexit 99\n", encoding="utf-8")
        external.chmod(0o755)
        subprocess.run(
            [str(git), "-C", str(repo_a), "config", "diff.external", str(external)],
            check=True,
        )
        subprocess.run(
            [str(git), "-C", str(repo_a), "config", "core.worktree", str(repo_b)],
            check=True,
        )
        subprocess.run(
            [str(git), "-C", str(repo_a), "config", "commit.gpgSign", "true"],
            check=True,
        )
        subprocess.run(
            [str(git), "-C", str(repo_a), "config", "gpg.program", str(external)],
            check=True,
        )
        subprocess.run(
            [str(git), "-C", str(repo_a), "replace", head_a, base_a],
            check=True,
        )

        hostile_parent = dict(os.environ)
        hostile_parent.update(
            {
                "GIT_DIR": str(repo_b / ".git"),
                "GIT_WORK_TREE": str(repo_b),
                "GIT_EXTERNAL_DIFF": str(external),
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "core.fsmonitor",
                "GIT_CONFIG_VALUE_0": str(external),
            }
        )
        os.environ.clear()
        os.environ.update(hostile_parent)

        observed_head = _boundary(git, repo_a, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
        diff = _boundary(
            git,
            repo_a,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--unified=0",
            base_a,
            head_a,
        ).decode("utf-8")
        status = _boundary(git, repo_a, "status", "--porcelain=v1", "-z")

        assert observed_head == head_a and observed_head != head_b
        assert "alpha-head" in diff and "beta" not in diff
        assert status == b""
        _boundary(git, repo_a, "commit", "--allow-empty", "-q", "-m", "boundary probe")
        assert not sentinel.exists()
        print(
            json.dumps(
                {
                    "locale": ENV["LC_ALL"],
                    "git": str(git),
                    "repo_a_head": head_a,
                    "repo_b_head": head_b,
                    "diff_contains": "alpha-head",
                    "external_diff_ran": sentinel.exists(),
                    "local_core_worktree": str(repo_b),
                    "replace_ref_ignored": True,
                    "signing_program_ran": sentinel.exists(),
                    "status_bytes": len(status),
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
