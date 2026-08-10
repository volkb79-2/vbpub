#!/usr/bin/env python3
"""Build P25's synthetic clean-tagged wheel from exact tracked Assay bytes.

This is a carver tool, not production code.  It deliberately reproduces the
P24 release recipe at the P25 input revision and refuses any source drift.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

INPUT_REVISION = "9f522a72d37b9cb5beb1939ceca1978c9fc4ef23"
ASSAY_SOURCE_TREE = "e077f25171778bf2e2982996f4466ad9d1259d12"
VERSION = "1.2.5"
SOURCE_DATE_EPOCH = "946684800"


def run(argv: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    proc = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode:
        raise SystemExit(
            f"command failed ({proc.returncode}): {argv!r}\n{proc.stdout}\n{proc.stderr}"
        )
    return proc.stdout


def copy_exact_source(repo: Path, destination: Path) -> None:
    actual = run(["git", "-C", str(repo), "rev-parse", f"{INPUT_REVISION}:assay/src/assay"]).strip()
    if actual != ASSAY_SOURCE_TREE:
        raise SystemExit(f"Assay source tree drifted: expected {ASSAY_SOURCE_TREE}, got {actual}")
    names = run(
        [
            "git",
            "-C",
            str(repo),
            "ls-tree",
            "-r",
            "--name-only",
            INPUT_REVISION,
            "--",
            "assay/pyproject.toml",
            "assay/src",
        ]
    ).splitlines()
    for relative in names:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        blob = subprocess.run(
            ["git", "-C", str(repo), "show", f"{INPUT_REVISION}:{relative}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if blob.returncode:
            raise SystemExit(blob.stderr.decode(errors="replace"))
        target.write_bytes(blob.stdout)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    scratch = args.scratch.resolve()
    output = args.output.resolve()
    if scratch.exists() or output.exists():
        raise SystemExit("scratch and output must both be absent")
    scratch.mkdir(parents=True)
    output.mkdir(parents=True)

    source = scratch / "source"
    copy_exact_source(repo, source)
    run(["git", "init", "-q", "-b", "main"], cwd=source)
    run(["git", "add", "assay"], cwd=source)
    identity = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Assay",
        "GIT_AUTHOR_EMAIL": "assay@example.invalid",
        "GIT_COMMITTER_NAME": "Assay",
        "GIT_COMMITTER_EMAIL": "assay@example.invalid",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
    }
    run(["git", "commit", "-q", "-m", "P25 qualification fixture"], cwd=source, env=identity)
    run(["git", "tag", f"assay-v{VERSION}"], cwd=source)

    base_python = Path(
        run(["/opt/tester-venv/bin/python", "-c", "import sys; print(sys.base_prefix + '/bin/python3')"]).strip()
    )
    build_venv = scratch / "build-venv"
    run([str(base_python), "-m", "venv", str(build_venv)])
    build_python = build_venv / "bin" / "python"
    distribution = repo / "assay" / "gate" / "distribution"
    run(
        [
            str(build_python),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(distribution / "build-wheelhouse"),
            "--require-hashes",
            "-r",
            str(distribution / "build-requirements.txt"),
        ]
    )
    home = scratch / "home"
    home.mkdir()
    build_env = {
        "HOME": str(home),
        "PATH": f"{build_python.parent}:/usr/bin:/bin",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "PYTHONHASHSEED": "0",
        "SOURCE_DATE_EPOCH": SOURCE_DATE_EPOCH,
    }
    run(
        [
            str(build_python),
            "-m",
            "pip",
            "wheel",
            "--no-index",
            "--no-build-isolation",
            "--no-deps",
            "--wheel-dir",
            str(output),
            str(source / "assay"),
        ],
        env=build_env,
    )
    wheels = list(output.glob("assay-*.whl"))
    if len(wheels) != 1 or wheels[0].name != f"assay-{VERSION}-py3-none-any.whl":
        raise SystemExit(f"unexpected wheels: {wheels}")
    manifest = output / "release-manifest.json"
    run(
        [
            str(build_python),
            str(distribution / "release_wheel.py"),
            "manifest",
            "--wheel",
            str(wheels[0]),
            "--version",
            VERSION,
            "--output",
            str(manifest),
        ]
    )
    shutil.copy2(distribution / "release_wheel.py", scratch / "release_wheel.py.witness")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
