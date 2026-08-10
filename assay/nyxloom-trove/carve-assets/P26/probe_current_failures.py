"""Carver-owned P26 premise probe against the pre-P26 implementation.

This is evidence about the input revision, not production code.  It proves the
two known false-PASS shapes, the missing-parent safe-I/O mismatch, the generic
Git child's missing process-group ownership, and the exact real-Git pathspec
semantics the repaired implementation may rely on.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from assay.attestation import (
    AttestationRecord,
    evaluate_attestation,
    load_attested_evidence,
)
from assay.errors import AssayError
from assay.safeio import read_bounded_file
from assay.verdict import EvidenceDeclaration


def _git_env() -> dict[str, str]:
    return {
        "PATH": os.environ["PATH"],
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "P26 probe",
        "GIT_AUTHOR_EMAIL": "p26@assay.invalid",
        "GIT_COMMITTER_NAME": "P26 probe",
        "GIT_COMMITTER_EMAIL": "p26@assay.invalid",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
    }


def _git(repo: Path, *args: str, raw: bool = False) -> bytes | str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        env=_git_env(),
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.decode("utf-8", errors="replace"))
    return completed.stdout if raw else completed.stdout.decode("utf-8").strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return str(_git(repo, "rev-parse", "HEAD"))


def _probe_attestation(root: Path) -> dict[str, bool]:
    repo = root / "consumer"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    directory_file = repo / "reviewed/child.py"
    directory_file.parent.mkdir()
    directory_file.write_text("old\n", encoding="utf-8")
    hostile = repo / "literal/*?[x]\n.py"
    hostile.parent.mkdir()
    hostile.write_text("old\n", encoding="utf-8")
    base = _commit(repo, "base")
    directory_file.write_text("new\n", encoding="utf-8")
    hostile.write_text("new\n", encoding="utf-8")
    head = _commit(repo, "head")

    directory = evaluate_attestation(
        repo,
        key="directory",
        head=head,
        record=AttestationRecord(
            producer="probe", attested_commit=base, reviewed_paths=("reviewed",)
        ),
    )

    attestations = root / "attestations"
    attestations.mkdir()
    outside = root / "outside.json"
    outside.write_text(
        json.dumps(
            {
                "producer": "probe",
                "attested_commit": head,
                "reviewed_paths": ["reviewed/child.py"],
            }
        ),
        encoding="utf-8",
    )
    escaped = load_attested_evidence(
        repo,
        head=head,
        declared=(EvidenceDeclaration(source="attested", key="../outside"),),
        attestations_dir=attestations,
    )[0]

    directory_ls = _git(
        repo, "--literal-pathspecs", "ls-tree", "-z", base, "--", "reviewed", raw=True
    )
    hostile_ls = _git(
        repo,
        "--literal-pathspecs",
        "ls-tree",
        "-z",
        base,
        "--",
        "literal/*?[x]\n.py",
        raw=True,
    )
    directory_diff = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "--literal-pathspecs",
            "diff",
            "--quiet",
            "--exit-code",
            "--no-ext-diff",
            "--no-textconv",
            base,
            head,
            "--",
            "reviewed",
        ],
        env=_git_env(),
        check=False,
    ).returncode
    hostile_diff = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "--literal-pathspecs",
            "diff",
            "--quiet",
            "--exit-code",
            "--no-ext-diff",
            "--no-textconv",
            base,
            head,
            "--",
            "literal/*?[x]\n.py",
        ],
        env=_git_env(),
        check=False,
    ).returncode
    return {
        "current_directory_descendant_change_false_pass": directory.status.value
        == "PASS",
        "current_parent_key_escape_false_pass": escaped.status.value == "PASS",
        "literal_directory_ls_tree_is_exact_tree": b" tree " in directory_ls
        and directory_ls.endswith(b"\treviewed\x00"),
        "literal_hostile_name_ls_tree_is_exact_blob": b" blob " in hostile_ls
        and hostile_ls.endswith(b"\tliteral/*?[x]\n.py\x00"),
        "literal_directory_diff_reports_changed": directory_diff == 1,
        "literal_hostile_name_diff_reports_changed": hostile_diff == 1,
    }


def _probe_missing_parent(root: Path) -> bool:
    try:
        read_bounded_file(root, "missing/review.json", limit=1024)
    except AssayError as exc:
        return (
            exc.outcome.value == "ERROR"
            and exc.reason_code.value == "UNREADABLE_ARTIFACT"
        )
    return False


def _probe_process_group(root: Path) -> bool:
    """Kill only the boundary process and observe its escaped descendant.

    The 60-second loop is a hang failsafe only.  The witness is the atomically published pidfile
    written by the descendant before the boundary is killed.
    """
    pidfile = root / "descendant.pid"
    source_root = Path(__file__).resolve().parents[3] / "src"
    boundary = r'''
import sys
from assay.git import _run_bounded
helper = r"""
import os, sys, time
child = os.fork()
if child == 0:
    temporary = sys.argv[1] + ".tmp"
    with open(temporary, "w", encoding="ascii") as stream:
        stream.write(str(os.getpid()))
    os.replace(temporary, sys.argv[1])
    os.write(1, b"ready")
    time.sleep(60)
    raise SystemExit(0)
raise SystemExit(0)
"""
_run_bounded([sys.executable, "-c", helper, sys.argv[1]])
'''
    env = dict(os.environ)
    env["PYTHONPATH"] = str(source_root)
    boundary_process = subprocess.Popen(
        [sys.executable, "-c", boundary, str(pidfile)],
        env=env,
        start_new_session=True,
    )
    failsafe = time.monotonic() + 60
    while not pidfile.exists() and time.monotonic() < failsafe:
        time.sleep(0.01)
    if not pidfile.exists():
        os.killpg(boundary_process.pid, signal.SIGKILL)
        boundary_process.wait()
        raise RuntimeError("the synchronized descendant never wrote its pid")
    descendant = int(pidfile.read_text(encoding="ascii"))
    os.kill(boundary_process.pid, signal.SIGTERM)
    boundary_process.wait(timeout=60)
    try:
        os.kill(descendant, 0)
    except ProcessLookupError:
        survived = False
    else:
        survived = True
    try:
        os.killpg(boundary_process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return survived


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="assay-p26-probe-") as raw:
        root = Path(raw)
        result = {
            **_probe_attestation(root),
            "current_missing_parent_maps_unreadable_not_missing": _probe_missing_parent(
                root
            ),
            "current_git_boundary_leaves_descendant_after_pid_only_kill": _probe_process_group(
                root
            ),
        }
    result["status"] = "PASS" if all(result.values()) else "FAIL"
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
