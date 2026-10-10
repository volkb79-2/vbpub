"""Host-side B131 evidence boundary checks after the judge container exits."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gate.tests.support import PROJECT_ROOT

TOOLS = PROJECT_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
import analysis_r2_pilot_host_check as host_check  # noqa: E402
import pilot_r2_snapshot  # noqa: E402

if sys.path[0] == str(TOOLS):
    sys.path.pop(0)


def _attested_pilot(tmp_path: Path) -> tuple[list[str], dict[str, Path]]:
    repository = tmp_path / "repo"
    project = repository / "assay"
    project.mkdir(parents=True)
    (repository / ".gitignore").write_text("assay/.assay/\n", encoding="utf-8")
    (project / "README.md").write_text("fixture source\n", encoding="utf-8")
    git_env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "Assay host checker test",
        "GIT_AUTHOR_EMAIL": "assay-host-check@example.invalid",
        "GIT_COMMITTER_NAME": "Assay host checker test",
        "GIT_COMMITTER_EMAIL": "assay-host-check@example.invalid",
    }
    subprocess.run(["git", "-C", str(repository), "init", "-q"], env=git_env, check=True)
    subprocess.run(["git", "-C", str(repository), "add", ".gitignore", "assay/README.md"], env=git_env, check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "fixture"], env=git_env, check=True)
    commit = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"], env=git_env, text=True
    ).strip()
    tree = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD^{tree}"], env=git_env, text=True
    ).strip()
    state_root = project / ".assay"
    state_root.mkdir(parents=True)
    deadline = state_root / f"campaign-deadline-analysis-r2-pilot-{commit[:12]}.json"
    paths = {
        "plan": state_root / "analysis-r2-pilot-plan.json",
        "selection": state_root / "analysis-r2-pilot-selection.json",
        "candidate file": state_root / "analysis-r2-pilot-candidates.txt",
        "summary": state_root / "analysis-r2-pilot-summary.json",
        "run log": state_root / "analysis-r2-pilot-run.log",
        "progress": state_root / "progress-analysis-r2-pilot.jsonl",
        "deadline": deadline,
        "manifest": state_root / "r2-manifest-analysis-r2-pilot.txt",
    }
    created = datetime.now(timezone.utc).replace(microsecond=0)
    deadline_document = {
        "schema": "assay-campaign-deadline/1",
        "campaign": f"analysis-r2-pilot-{commit[:12]}",
        "commit": commit,
        "git_tree": tree,
        "lanes": ["analysis-r2"],
        "assay_version": "8.1.0",
        "wheel_sha256": "c" * 64,
        "plan_sha256": {"analysis-r2": "d" * 64},
        "created_at_utc": created.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at_utc": (created + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    for index, (label, path) in enumerate(paths.items()):
        if label == "deadline":
            path.write_text(json.dumps(deadline_document, sort_keys=True) + "\n", encoding="utf-8")
        else:
            path.write_text(f"fixture artifact {index}\n", encoding="utf-8")
    state_dir = state_root / "analysis-r2-pilot-state"
    state_dir.mkdir()
    state_names = ["PILOT-STATE", f"{'b' * 64}.json"]
    for name in state_names:
        (state_dir / name).write_text(f"state {name}\n", encoding="utf-8")

    files: dict[str, dict[str, object]] = {}
    for label, path in paths.items():
        raw, identity = pilot_r2_snapshot.read_file(path, maximum=1024 * 1024)
        files[label] = {"identity": identity, "sha256": hashlib.sha256(raw).hexdigest()}
    for name in state_names:
        path = state_dir / name
        raw, identity = pilot_r2_snapshot.read_file(path, maximum=1024 * 1024)
        files[f"state/{name}"] = {
            "identity": identity,
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    attestation = state_root / "analysis-r2-pilot-attestation.json"
    document = {
        "schema": "assay-analysis-r2-pilot-evidence-attestation/2",
        "commit": commit,
        "files": files,
        "state_directory": pilot_r2_snapshot.directory_snapshot(state_dir),
        "verdict": pilot_r2_snapshot.path_is_absent(state_root / "verdict-analysis-r2.json"),
    }
    raw = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    attestation.write_bytes(raw)
    argv = [
        "--project-root", str(project),
        "--attestation", str(attestation),
        "--deadline", str(deadline),
        "--expected-commit", commit,
        "--expected-tree", tree,
        "--expected-sha256", hashlib.sha256(raw).hexdigest(),
    ]
    return argv, paths


def _run(argv: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        status = host_check.main(argv)
    return status, stdout.getvalue(), stderr.getvalue()


def test_host_checker_accepts_exact_attested_inventory_after_container_exit(tmp_path: Path):
    argv, _paths = _attested_pilot(tmp_path)

    status, stdout, stderr = _run(argv)

    assert status == 0, stderr
    assert stdout.startswith("ANALYSIS_R2_PILOT_HOST_EVIDENCE_VERIFIED=")


@pytest.mark.parametrize(
    ("label", "change"),
    [
        ("plan", "identical-replacement"),
        ("plan", "hardlink"),
        ("run log", "identical-replacement"),
        ("run log", "hardlink"),
    ],
)
def test_host_checker_rejects_replaced_or_hardlinked_evidence(
    tmp_path: Path, label: str, change: str
):
    argv, paths = _attested_pilot(tmp_path)
    target = paths[label]
    if change == "identical-replacement":
        raw = target.read_bytes()
        target.unlink()
        target.write_bytes(raw)
    else:
        target.with_name("plan-aliased.json").hardlink_to(target)

    status, stdout, stderr = _run(argv)

    assert status == 2
    assert stdout == ""
    assert "host evidence differs" in stderr or "hard links" in stderr


def test_host_checker_rechecks_run_log_after_source_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    argv, paths = _attested_pilot(tmp_path)
    original_check = host_check._check_worktree_source

    def change_run_log_then_check(
        project: Path, *, expected_commit: str, expected_tree: str
    ) -> None:
        paths["run log"].write_text("changed after artifact scan\n", encoding="utf-8")
        original_check(
            project, expected_commit=expected_commit, expected_tree=expected_tree
        )

    monkeypatch.setattr(host_check, "_check_worktree_source", change_run_log_then_check)

    status, stdout, stderr = _run(argv)

    assert status == 2
    assert stdout == ""
    assert "host evidence changed during final source verification: run log" in stderr


def test_host_checker_rechecks_earlier_files_after_later_artifact_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    argv, paths = _attested_pilot(tmp_path)
    original_read = host_check._read_regular
    summary_reads = 0

    def replace_plan_after_summary(path: Path, *, maximum: int):
        nonlocal summary_reads
        result = original_read(path, maximum=maximum)
        if path == paths["summary"]:
            summary_reads += 1
            if summary_reads == 1:
                plan = paths["plan"]
                raw = plan.read_bytes()
                plan.unlink()
                plan.write_bytes(raw)
        return result

    monkeypatch.setattr(host_check, "_read_regular", replace_plan_after_summary)

    status, stdout, stderr = _run(argv)

    assert status == 2
    assert stdout == ""
    assert "changed before final verification" in stderr


def test_host_checker_rechecks_worktree_after_the_artifact_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    argv, _paths = _attested_pilot(tmp_path)
    original_check = host_check._check_worktree_source

    def change_source_then_check(project: Path, *, expected_commit: str, expected_tree: str):
        (project / "README.md").write_text("changed during host verification\n", encoding="utf-8")
        return original_check(
            project, expected_commit=expected_commit, expected_tree=expected_tree
        )

    monkeypatch.setattr(host_check, "_check_worktree_source", change_source_then_check)

    status, stdout, stderr = _run(argv)

    assert status == 2
    assert stdout == ""
    assert "judged worktree became dirty during host evidence verification" in stderr


def test_host_checker_rehashes_evidence_changed_during_final_source_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    argv, paths = _attested_pilot(tmp_path)
    original_check = host_check._check_worktree_source
    summary = paths["summary"]
    original_identity = summary.stat()

    def change_summary_in_place_then_check(
        project: Path, *, expected_commit: str, expected_tree: str
    ):
        with summary.open("ab") as stream:
            stream.write(b" ")
            stream.flush()
        changed_identity = summary.stat()
        assert changed_identity.st_ino == original_identity.st_ino
        return original_check(
            project, expected_commit=expected_commit, expected_tree=expected_tree
        )

    monkeypatch.setattr(host_check, "_check_worktree_source", change_summary_in_place_then_check)

    status, stdout, stderr = _run(argv)

    assert status == 2
    assert stdout == ""
    assert "host evidence changed during final source verification: summary" in stderr


def test_host_checker_checks_deadline_after_artifacts_and_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    argv, paths = _attested_pilot(tmp_path)
    deadline = json.loads(paths["deadline"].read_text(encoding="utf-8"))
    expires = datetime.strptime(
        deadline["expires_at_utc"], "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=timezone.utc)
    source_checked = False
    original_check = host_check._check_worktree_source

    def record_source_check(project: Path, *, expected_commit: str, expected_tree: str):
        nonlocal source_checked
        source_checked = True
        return original_check(
            project, expected_commit=expected_commit, expected_tree=expected_tree
        )

    monkeypatch.setattr(host_check, "_check_worktree_source", record_source_check)
    monkeypatch.setattr(host_check, "_utc_now", lambda: expires + timedelta(seconds=1))

    status, stdout, stderr = _run(argv)

    assert source_checked
    assert status == 2
    assert stdout == ""
    assert "campaign deadline expired during final host verification" in stderr
