#!/usr/bin/env python3
"""Carver tracer for P25's pinned, disposable Topos qualification.

This is independent proof material, not the production harness.  It consumes
the locked P25 release wheel, materializes the pinned tracked Topos tree, makes
the explicit three-symlink compatibility patch, and compares Assay with both a
hand manifest and the unmodified copied Topos evaluator.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

INPUT_REVISION = "9f522a72d37b9cb5beb1939ceca1978c9fc4ef23"
TOPOS_TREE = "1bc8a51296b74e536bf60b534efb2fc938dcc389"
TOPOS_TRACKED_COUNT = 966
ROOT_GITIGNORE_BLOB = "b0a3fe4a8cabd6ebdebc4fc4aa4a4a8623bd8dbe"
ROOT_GITIGNORE_SHA256 = "630b36ce354df85ea22ab3cdf44ebb78996f1ca1aec96f2a7f3ae967b231b707"
COVERAGE_GATE_BLOB = "6b96e8711b6c0a2e20456e74626804cd502cf9b7"
COVERAGE_GATE_SHA256 = "64d7d3855abb8b822ff5e49a9a31e79c92bb3857bfb4b9d6fbd57168ff1472f5"

ABSOLUTE_SYMLINKS = {
    "topos/tests/fixtures/inspect_files/_danger/passwd_link": "/etc/passwd",
    "topos/tests/fixtures/inspect_files/cgroup_escape/system.slice/ssh.service/dangerous_link/passwd_escape": "/etc/passwd",
    "topos/tests/fixtures/inspect_files/cgroup_nonreg/system.slice/ssh.service/memory.current": "/etc/passwd",
}

PASS_MANIFEST = {
    "path": "topos/src/topos/_assay_probe.py",
    "executed_lines": [4, 5, 6, 7, 10],
    "missing_lines": [],
    "excluded_lines": [11],
    "comment_lines": [1, 13],
    "changed_executable": 5,
    "covered": 5,
}
MISSING_MANIFEST = {
    "path": "topos/src/topos/_assay_probe.py",
    "executed_lines": [4, 5, 6, 10],
    "missing_lines": [7],
    "excluded_lines": [11],
    "comment_lines": [1, 13],
    "changed_executable": 5,
    "covered": 4,
}


@dataclass(frozen=True)
class RunResult:
    scenario: str
    base: str
    head: str
    exit_code: int
    artifact: dict[str, Any]
    witness: dict[str, Any] | None
    comparator: dict[str, Any] | None
    clean_after: bool


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
    timeout: int = 900,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,  # hang failsafe only; elapsed time never decides a verdict
        check=False,
    )
    if check and proc.returncode:
        raise AssertionError(
            f"command failed ({proc.returncode}): {argv!r}\n"
            f"stdout:\n{proc.stdout[-8000:]}\nstderr:\n{proc.stderr[-8000:]}"
        )
    return proc


def git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return run(["git", "-C", str(repo), *args], env=env).stdout.strip()


def extract_pin(source_repo: Path, destination: Path) -> None:
    destination.mkdir(parents=True)
    producer = subprocess.Popen(
        [
            "git",
            "-C",
            str(source_repo),
            "archive",
            "--format=tar",
            INPUT_REVISION,
            ".gitignore",
            "topos",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert producer.stdout is not None
    consumer = subprocess.Popen(
        ["tar", "-xf", "-", "-C", str(destination)],
        stdin=producer.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    producer.stdout.close()
    _, tar_error = consumer.communicate(timeout=120)
    _, git_error = producer.communicate(timeout=120)
    if producer.returncode or consumer.returncode:
        raise AssertionError(
            f"archive failed: git={producer.returncode} {git_error!r}; "
            f"tar={consumer.returncode} {tar_error!r}"
        )

    assert git(source_repo, "rev-parse", f"{INPUT_REVISION}:topos") == TOPOS_TREE
    assert git(source_repo, "rev-parse", f"{INPUT_REVISION}:.gitignore") == ROOT_GITIGNORE_BLOB
    assert sha256(destination / ".gitignore") == ROOT_GITIGNORE_SHA256
    tracked = git(
        source_repo, "ls-tree", "-r", "--name-only", INPUT_REVISION, "--", "topos"
    ).splitlines()
    assert len(tracked) == TOPOS_TRACKED_COUNT
    comparator = destination / "topos/tools/coverage_gate.py"
    assert git(source_repo, "rev-parse", f"{INPUT_REVISION}:topos/tools/coverage_gate.py") == COVERAGE_GATE_BLOB
    assert sha256(comparator) == COVERAGE_GATE_SHA256

    observed_absolute: dict[str, str] = {}
    for relative in tracked:
        path = destination / relative
        if path.is_symlink() and os.path.isabs(os.readlink(path)):
            observed_absolute[relative] = os.readlink(path)
    assert observed_absolute == ABSOLUTE_SYMLINKS
    for relative, target in ABSOLUTE_SYMLINKS.items():
        path = destination / relative
        assert path.is_symlink() and os.readlink(path) == target
        path.unlink()


def fixed_identity() -> dict[str, str]:
    return {
        **os.environ,
        "GIT_AUTHOR_NAME": "Assay P25",
        "GIT_AUTHOR_EMAIL": "assay-p25@example.invalid",
        "GIT_COMMITTER_NAME": "Assay P25",
        "GIT_COMMITTER_EMAIL": "assay-p25@example.invalid",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
    }


def seed_baseline(source_repo: Path, repository: Path) -> str:
    extract_pin(source_repo, repository)
    run(["git", "init", "-q", "-b", "main"], cwd=repository)
    (repository / ".assay").mkdir()
    (repository / ".assay/.gitignore").write_text("*\n!.gitignore\n", encoding="utf-8")
    # Force-add the exact exported tracked set.  Re-initialising a repository
    # and using ordinary `git add` would re-interpret vbpub's own .gitignore
    # and silently drop four tracked Topos Docker-log fixtures; the targeted
    # tests stay green while the full suite loses 13 oracles.
    run(["git", "add", "-f", ".gitignore", ".assay/.gitignore", "topos"], cwd=repository)
    indexed = git(repository, "ls-files").splitlines()
    assert len(indexed) == TOPOS_TRACKED_COUNT - len(ABSOLUTE_SYMLINKS) + 2
    run(["git", "commit", "-q", "-m", "pinned Topos compatibility baseline"], cwd=repository, env=fixed_identity())
    return git(repository, "rev-parse", "HEAD")


def write_lane(
    repository: Path,
    *,
    base: str,
    witness: Path,
    log: Path,
    full_suite: bool,
    allow_excluded: bool,
) -> None:
    tests = '["topos/tests", "-q", "-n", "auto"]' if full_suite else (
        '["topos/tests/test_config.py", "topos/tests/test_assay_probe.py", "-q", "-n", "2"]'
    )
    argv = json.loads(tests)
    argv = ["/opt/tester-venv/bin/python", "topos/tools/assay_p25_coverage.py", *argv]
    quoted_argv = ", ".join(json.dumps(item) for item in argv)
    text = f'''schema_version = 1
[lanes.topos-qualification]
scope = "S1"
rigor = ["R0", "R1"]
enforcement = "gate"
argv = [{quoted_argv}]
env = {{PYTHONPATH = "topos/src:topos", ASSAY_P25_WITNESS = {json.dumps(str(witness))}, ASSAY_P25_LOG = {json.dumps(str(log))}, PATH = "/opt/tester-venv/bin:/usr/local/bin:/usr/bin:/bin", HOME = "/home/tester", XDG_CACHE_HOME = "/home/tester/.cache", XDG_CONFIG_HOME = "/home/tester/.config", XDG_STATE_HOME = "/home/tester/.local/state", LANG = "C.UTF-8", LC_ALL = "C.UTF-8"}}
env_passthrough = []
budget = "15m"
allow_argv_append = false
[lanes.topos-qualification.judge]
language = "python"
source_roots = ["topos/src/topos"]
base = {json.dumps(base)}
fail_under = 100.0
allow_excluded = {str(allow_excluded).lower()}
[lanes.topos-qualification.judge.coverage]
format = "coverage-py-json"
artifact = ".assay/topos-coverage.json"
'''
    (repository / "assay.toml").write_text(text, encoding="utf-8")


def copy_fixture(asset_root: Path, name: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(asset_root / "fixtures" / name, target)


def load_topos_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("p25_topos_coverage_gate", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def comparator_result(repository: Path, base: str, witness: Path) -> dict[str, Any]:
    module = load_topos_module(repository / "topos/tools/coverage_gate.py")
    diff = git(
        repository,
        "diff",
        "--relative",
        "--unified=0",
        base,
        "HEAD",
        "--",
        "topos/src/topos",
    )
    added = module.parse_added_lines(diff)
    profile = json.loads(witness.read_text(encoding="utf-8"))["files"]
    verdict = module.evaluate(added, profile, "topos/src/topos", 100.0)
    return {
        "passed": verdict.passed,
        "changed_executable": verdict.changed_executable,
        "covered": verdict.covered,
        "pct": verdict.pct,
        "uncovered": {path: sorted(lines) for path, lines in sorted(verdict.uncovered.items())},
        "files_missing_coverage": verdict.files_missing_coverage,
    }


def create_scenario(
    source_repo: Path,
    asset_root: Path,
    root: Path,
    *,
    name: str,
    probe: str,
    test: str,
    full_suite: bool,
    allow_excluded: bool,
    source_target: str = "_assay_probe.py",
) -> tuple[Path, Path, str, str]:
    repository = root / name / "repo"
    base = seed_baseline(source_repo, repository)
    witness = root / name / "witness.json"
    log = root / name / "pytest.log"
    copy_fixture(asset_root, probe, repository / "topos/src/topos" / source_target)
    copy_fixture(asset_root, test, repository / "topos/tests/test_assay_probe.py")
    copy_fixture(asset_root, "coverage-witness.py", repository / "topos/tools/assay_p25_coverage.py")
    write_lane(
        repository,
        base=base,
        witness=witness,
        log=log,
        full_suite=full_suite,
        allow_excluded=allow_excluded,
    )
    run(
        [
            "git",
            "add",
            "assay.toml",
            f"topos/src/topos/{source_target}",
            "topos/tests/test_assay_probe.py",
            "topos/tools/assay_p25_coverage.py",
        ],
        cwd=repository,
    )
    run(["git", "commit", "-q", "-m", f"P25 {name}"], cwd=repository, env=fixed_identity())
    return repository, witness, base, git(repository, "rev-parse", "HEAD")


def run_scenario(
    source_repo: Path,
    asset_root: Path,
    scratch: Path,
    assay_executable: Path,
    *,
    name: str,
    probe: str,
    test: str,
    full_suite: bool,
    allow_excluded: bool,
    source_target: str = "_assay_probe.py",
) -> RunResult:
    repository, witness, base, head = create_scenario(
        source_repo,
        asset_root,
        scratch,
        name=name,
        probe=probe,
        test=test,
        full_suite=full_suite,
        allow_excluded=allow_excluded,
        source_target=source_target,
    )
    artifact_path = scratch / name / "verdict.json"
    proc = run(
        [str(assay_executable), "run", "topos-qualification", "--verdict-json", str(artifact_path)],
        cwd=repository,
        check=False,
    )
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    profile = json.loads(witness.read_text(encoding="utf-8")) if witness.exists() else None
    comparator = comparator_result(repository, base, witness) if witness.exists() else None
    clean = git(repository, "status", "--porcelain=v1") == ""
    assert artifact["commit"] == head
    assert proc.returncode == artifact["exit_code"]
    return RunResult(name, base, head, proc.returncode, artifact, profile, comparator, clean)


def install_locked_release(source_repo: Path, asset_root: Path, scratch: Path) -> tuple[Path, str]:
    release = asset_root / "release"
    wheel = release / "assay-1.2.5-py3-none-any.whl"
    manifest = release / "release-manifest.json"
    base_python = Path(
        run(["/opt/tester-venv/bin/python", "-c", "import sys; print(sys.base_prefix + '/bin/python3')"]).stdout.strip()
    )
    venv = scratch / "release-venv"
    run([str(base_python), "-m", "venv", str(venv)])
    helper = source_repo / "assay/gate/distribution/release_wheel.py"
    verified = run(
        [str(venv / "bin/python"), str(helper), "verify", "--wheel", str(wheel), "--manifest", str(manifest)]
    )
    requirement = scratch / "release-requirement.txt"
    requirement.write_text(verified.stdout, encoding="utf-8")
    run(
        [
            str(venv / "bin/python"),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--require-hashes",
            "-r",
            str(requirement),
        ]
    )
    tester_site = run(
        ["/opt/tester-venv/bin/python", "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"]
    ).stdout.strip()
    run_site = run(
        [str(venv / "bin/python"), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"]
    ).stdout.strip()
    (Path(run_site) / "tester_unified_site.pth").write_text(tester_site + "\n", encoding="utf-8")
    purity = run(
        [
            str(venv / "bin/python"),
            "-c",
            "import json, assay; from importlib.metadata import version; "
            "print(json.dumps([version('assay'), assay.__version__, assay.__file__]))",
        ]
    )
    installed, runtime, location = json.loads(purity.stdout)
    assert installed == runtime == "1.2.5"
    assert location.startswith(str(venv) + "/")
    assert not location.startswith(str(source_repo / "assay/src") + "/")
    return venv / "bin/assay", installed


def compact(result: RunResult) -> dict[str, Any]:
    r1 = result.artifact["claims"][1]
    return {
        "scenario": result.scenario,
        "base": result.base,
        "head": result.head,
        "exit_code": result.exit_code,
        "outcome": result.artifact["outcome"],
        "reason_code": result.artifact.get("reason_code"),
        "r1": r1,
        "comparator": result.comparator,
        "clean_after": result.clean_after,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--full-suite", action="store_true")
    parser.add_argument("--only-pass", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source_repo = args.repo.resolve()
    scratch = args.scratch.resolve()
    if scratch.exists():
        raise SystemExit("scratch must be absent")
    scratch.mkdir(parents=True)
    asset_root = Path(__file__).resolve().parent
    assay_executable, version = install_locked_release(source_repo, asset_root, scratch)

    passing = run_scenario(
        source_repo,
        asset_root,
        scratch,
        assay_executable,
        name="pass",
        probe="probe-pass.py",
        test="test-probe-pass.py",
        full_suite=args.full_suite,
        allow_excluded=True,
    )
    if args.only_pass:
        print(json.dumps(compact(passing), indent=2, sort_keys=True))
        return passing.exit_code
    missing = run_scenario(
        source_repo,
        asset_root,
        scratch,
        assay_executable,
        name="missing",
        probe="probe-missing.py",
        test="test-probe-missing.py",
        full_suite=False,
        allow_excluded=True,
    )
    excluded = run_scenario(
        source_repo,
        asset_root,
        scratch,
        assay_executable,
        name="excluded-forbidden",
        probe="probe-pass.py",
        test="test-probe-pass.py",
        full_suite=False,
        allow_excluded=False,
    )
    comment_only = run_scenario(
        source_repo,
        asset_root,
        scratch,
        assay_executable,
        name="comment-only",
        probe="comment-only.py",
        test="test-comment-only.py",
        full_suite=False,
        allow_excluded=True,
        source_target="_assay_comment.py",
    )

    if passing.exit_code != 0:
        print(json.dumps(compact(passing), indent=2, sort_keys=True), file=sys.stderr)

    assert passing.exit_code == 0 and passing.artifact["outcome"] == "PASS"
    assert passing.artifact["claims"][1]["coverage"] == {
        "covered": 5,
        "changed_executable": 5,
        "pct": 100.0,
        "considered": 1,
        "exclusion_capability": "reported",
        "missing_lines": {},
        "files_missing_coverage": [],
        "unclassified_lines": {},
        "files_with_unclassified_lines": [],
        "excluded_lines": {PASS_MANIFEST["path"]: [11]},
        "files_with_excluded_lines": [PASS_MANIFEST["path"]],
    }
    assert passing.comparator == {
        "passed": True,
        "changed_executable": 5,
        "covered": 5,
        "pct": 100.0,
        "uncovered": {},
        "files_missing_coverage": [],
    }
    assert missing.exit_code == 1 and missing.artifact["reason_code"] == "UNCOVERED_LINES"
    assert missing.comparator is not None and missing.comparator["passed"] is False
    assert missing.comparator["uncovered"] == {MISSING_MANIFEST["path"]: [7]}
    assert excluded.exit_code == 1 and excluded.artifact["reason_code"] == "EXCLUDED_LINES"
    assert excluded.comparator is not None and excluded.comparator["passed"] is True
    assert comment_only.exit_code == 0 and comment_only.artifact["outcome"] == "PASS"
    assert comment_only.artifact["claims"][1]["coverage"]["changed_executable"] == 0
    assert comment_only.artifact["claims"][1]["coverage"]["covered"] == 0
    assert comment_only.comparator is not None and comment_only.comparator["passed"] is True
    assert comment_only.comparator["changed_executable"] == 0
    assert all(item.clean_after for item in (passing, missing, excluded, comment_only))

    document = {
        "schema_version": 1,
        "input_revision": INPUT_REVISION,
        "topos_tree": TOPOS_TREE,
        "assay_version": version,
        "wheel_sha256": sha256(asset_root / "release/assay-1.2.5-py3-none-any.whl"),
        "full_suite": args.full_suite,
        "compatibility_deletions": ABSOLUTE_SYMLINKS,
        "scenarios": [
            compact(passing),
            compact(missing),
            compact(excluded),
            compact(comment_only),
        ],
    }
    rendered = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(rendered)
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
