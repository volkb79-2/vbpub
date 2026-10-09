"""B118/P7a contract tests for non-qualifying native-R2 pilot runs."""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from assay import cli, mutation
from assay.adapters.python import PythonAdapter
from assay.cli import main
from assay.errors import LaneConfigError, Outcome, ReasonCode
from conftest import GitRepo, PROJECT_ROOT


def _seed_pilot_repo(repo: GitRepo) -> tuple[Path, tuple[int, ...]]:
    repo.write("src/mod.py", "def base():\n    return 0\n")
    base = repo.commit_all("add base source")
    source = (
        "def killed_a():\n    return a > 0\n\n"
        "def killed_b():\n    return b > 0\n\n"
        "def killed_c():\n    return c > 0\n\n"
        "def survivor():\n    return untouched > 0\n"
    )
    repo.write("src/mod.py", source)
    line_numbers = tuple(
        index
        for index, line in enumerate(source.splitlines(), start=1)
        if "return " in line
    )
    command = " && ".join(
        f"grep -Fqx {json.dumps(f'    return {name} > 0')} src/mod.py"
        for name in ("a", "b", "c")
    )
    argv = json.dumps(["/bin/sh", "-c", command])
    common = f'''\
scope = "S1"
enforcement = "gate"
argv = {argv}
env = {{}}
env_passthrough = ["PATH"]
budget = "2m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "python"
source_roots = ["src"]
base = "{base}"

[lanes.package.judge.mutation]
jobs = 1
max_mutants = 32
operators = ["python:compare-swap"]
budget_per_candidate = "30s"
'''
    config = (
        "schema_version = 2\n\n"
        "[lanes.package]\nrigor = [\"R0\", \"R2\"]\n"
        + common
        + "\n[lanes.pilot-r3]\nrigor = [\"R0\", \"R2\", \"R3\"]\n"
        + common.replace("[lanes.package.", "[lanes.pilot-r3.")
        + '\n[lanes.pilot-r3.judge.canary]\nmechanism = "import-break"\ntarget = "src/mod.py"\n'
        + '\n[lanes.r0only]\nscope = "S1"\nrigor = ["R0"]\nenforcement = "gate"\n'
        + f"argv = {argv}\nenv = {{}}\nenv_passthrough = [\"PATH\"]\n"
        + 'budget = "2m"\nallow_argv_append = false\n'
    )
    lane_file = repo.write("assay.toml", config)
    repo.commit_all("add pilot lanes")
    return lane_file, line_numbers


def _run(argv: list[str]) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    code = main(argv, stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def _plan(lane_file: Path, lane: str = "package") -> list[dict[str, object]]:
    code, stdout, stderr = _run(["plan", lane, "--file", str(lane_file)])
    assert code == 0, stderr
    document = json.loads(stdout)
    assert document["status"] == "ok"
    return document["candidates"]


def _candidate_file(path: Path, candidate_ids: list[str]) -> Path:
    path.write_text("# pilot test\n" + "\n".join(candidate_ids) + "\n", encoding="utf-8")
    return path


def _assert_state_lock_refuses_promptly(state_dir: Path) -> LaneConfigError:
    completed = threading.Event()
    result: list[BaseException | None] = []

    def attempt():
        try:
            root_fd, lock_fds, _root = cli._acquire_state_directory_lock(state_dir)
        except BaseException as exc:
            result.append(exc)
        else:
            try:
                result.append(None)
            finally:
                cli._release_state_directory_lock(root_fd, lock_fds)
        finally:
            completed.set()

    contender = threading.Thread(target=attempt, daemon=True)
    contender.start()
    assert completed.wait(timeout=1), "contending state lock acquisition blocked"
    contender.join(timeout=1)
    assert not contender.is_alive()
    assert len(result) == 1
    assert isinstance(result[0], LaneConfigError)
    assert "already in use" in str(result[0])
    return result[0]


def _run_pilot(
    lane_file: Path,
    candidates: Path,
    state_dir: Path,
    progress: Path,
    *,
    lane: str = "package",
    extra: tuple[str, ...] = (),
) -> tuple[int, str, str]:
    return _run(
        [
            "run",
            lane,
            "--file",
            str(lane_file),
            "--candidates-file",
            str(candidates),
            "--state-dir",
            str(state_dir),
            "--progress",
            str(progress),
            *extra,
        ]
    )


@pytest.mark.parametrize("record_name", ["PILOT-STATE", "a" * 64 + ".json"])
def test_pilot_progress_cannot_overwrite_state_metadata_or_records(
    git_repo: GitRepo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    record_name: str,
):
    lane_file, _lines = _seed_pilot_repo(git_repo)
    candidate = str(_plan(lane_file)[0]["id"])
    candidates = _candidate_file(tmp_path / "one.txt", [candidate])
    state_dir = tmp_path / "state"
    progress = state_dir / record_name
    monkeypatch.setattr(
        cli,
        "_run_reserved",
        lambda *_args, **_kwargs: pytest.fail("pilot execution started before collision refusal"),
    )

    code, _stdout, stderr = _run_pilot(lane_file, candidates, state_dir, progress)

    assert code == 2
    assert "--progress" in stderr and "mutation state directory" in stderr
    assert not progress.exists()


def test_pilot_progress_open_rechecks_state_alias_after_preflight(
    git_repo: GitRepo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    lane_file, _lines = _seed_pilot_repo(git_repo)
    candidate = str(_plan(lane_file)[0]["id"])
    candidates = _candidate_file(tmp_path / "one.txt", [candidate])
    state_dir = tmp_path / "state"
    progress = tmp_path / "progress.jsonl"
    snapshots: dict[str, dict[str, bytes]] = {}
    original_enable = cli._DeferredProgressWriter.enable

    def redirect_progress_to_sentinel(writer):
        sentinel = state_dir / "PILOT-STATE"
        assert sentinel.is_file()
        snapshots["before"] = {
            path.name: path.read_bytes() for path in state_dir.iterdir() if path.is_file()
        }
        writer._path.unlink(missing_ok=True)
        os.link(sentinel, writer._path)
        try:
            original_enable(writer)
        finally:
            writer._path.unlink(missing_ok=True)
            snapshots["after"] = {
                path.name: path.read_bytes()
                for path in state_dir.iterdir()
                if path.is_file()
            }

    monkeypatch.setattr(
        cli._DeferredProgressWriter,
        "enable",
        redirect_progress_to_sentinel,
    )
    code, stdout, stderr = _run_pilot(
        lane_file, candidates, state_dir, progress
    )

    assert code == 2
    assert stdout == ""
    assert "another link to mutation-state file 'PILOT-STATE'" in stderr
    assert snapshots["after"] == snapshots["before"]
    assert not progress.exists()


def test_state_parent_symlink_redirect_is_rejected(
    git_repo: GitRepo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    lane_file, _lines = _seed_pilot_repo(git_repo)
    candidate = str(_plan(lane_file)[0]["id"])
    candidates = _candidate_file(tmp_path / "one.txt", [candidate])
    first_parent = tmp_path / "first-parent"
    replacement_parent = tmp_path / "replacement-parent"
    first_parent.mkdir()
    replacement_parent.mkdir()
    parent_alias = tmp_path / "state-parent"
    parent_alias.symlink_to(first_parent, target_is_directory=True)
    state_dir = parent_alias / "state"
    original_preflight = cli._preflight_state_directory

    def redirect_after_preflight(*args, **kwargs):
        result = original_preflight(*args, **kwargs)
        parent_alias.unlink()
        parent_alias.symlink_to(replacement_parent, target_is_directory=True)
        return result

    monkeypatch.setattr(cli, "_preflight_state_directory", redirect_after_preflight)
    code, stdout, stderr = _run_pilot(
        lane_file, candidates, state_dir, tmp_path / "progress.jsonl"
    )

    assert code == 2
    assert stdout == ""
    assert "mutation state directory" in stderr and "changed while locked" in stderr
    assert not (first_parent / "state" / "PILOT-STATE").exists()


def test_pilot_rechecks_sentinel_after_summary_state_reads(
    git_repo: GitRepo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    lane_file, _lines = _seed_pilot_repo(git_repo)
    candidate = str(_plan(lane_file)[0]["id"])
    candidates = _candidate_file(tmp_path / "one.txt", [candidate])
    state_dir = tmp_path / "state"
    original = cli._build_pilot_summary

    def change_sentinel_after_reads(*args, **kwargs):
        summary = original(*args, **kwargs)
        (state_dir / "PILOT-STATE").write_text(
            json.dumps(
                {
                    "schema": "assay-pilot-state/1",
                    "selection_sha256": "f" * 64,
                    "lane": "package",
                }
            ),
            encoding="utf-8",
        )
        return summary

    monkeypatch.setattr(cli, "_build_pilot_summary", change_sentinel_after_reads)
    code, stdout, stderr = _run_pilot(
        lane_file, candidates, state_dir, tmp_path / "progress.jsonl"
    )
    assert code == 2
    assert stdout == ""
    assert "PILOT-STATE changed while the pilot summary was being built" in stderr


def _events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _ids_by_line(rows: list[dict[str, object]]) -> dict[int, str]:
    return {int(row["lineno"]): str(row["id"]) for row in rows}


def _job_for_candidate(lane_file: Path, candidate_id: str) -> mutation.MutantJob:
    rows = _plan(lane_file)
    selected = next(row for row in rows if row["id"] == candidate_id)
    path = str(selected["path"])
    source = (lane_file.parent / path).read_text(encoding="utf-8")
    lane = cli._resolve_lane_file(lane_file).lane("package")
    assert lane.judge is not None and lane.judge.mutation is not None
    jobs = mutation.collect_mutation_sites(
        (
            mutation.MutationTarget(
                path=path,
                text=source,
                lines=frozenset(
                    int(row["lineno"])
                    for row in rows
                    if row["path"] == path
                ),
            ),
        ),
        adapter=PythonAdapter(),
        operators=lane.judge.mutation.operators,
        limit=lane.judge.mutation.max_mutants,
    )
    assert jobs != mutation.UNSUPPORTED
    return next(job for job in jobs if mutation.candidate_id(job) == candidate_id)


def test_T1_candidate_file_grammar_refuses_before_progress_header(git_repo: GitRepo, tmp_path: Path):
    lane_file, _lines = _seed_pilot_repo(git_repo)
    valid_id = str(_plan(lane_file)[0]["id"])
    cases = (
        ("uppercase", "A" * 64 + "\n", "64-hex"),
        ("trailing-space", valid_id + " \n", "64-hex"),
        ("duplicate", valid_id + "\n" + valid_id + "\n", "appears more than once"),
        ("empty", "# no candidates\n\n", "selects no candidates"),
        ("invalid-utf8", b"\xff", "valid UTF-8"),
        (
            "over-cap",
            "\n".join(f"{index:064x}" for index in range(33)) + "\n",
            "more than judge.mutation.max_mutants",
        ),
    )
    for name, contents, message in cases:
        candidates = tmp_path / f"{name}.txt"
        if isinstance(contents, bytes):
            candidates.write_bytes(contents)
        else:
            candidates.write_text(contents, encoding="utf-8")
        progress = tmp_path / f"{name}.jsonl"
        code, _out, err = _run_pilot(
            lane_file,
            candidates,
            tmp_path / f"state-{name}",
            progress,
        )
        assert code == 2
        assert message in err
        assert not progress.exists()

    missing_progress = tmp_path / "missing.jsonl"
    code, _out, err = _run_pilot(
        lane_file,
        tmp_path / "does-not-exist.txt",
        tmp_path / "missing-state",
        missing_progress,
    )
    assert code == 2 and "does not exist" in err
    assert not missing_progress.exists()


def test_T2_pilot_flag_conflicts_refuse_before_execution(git_repo: GitRepo, tmp_path: Path):
    lane_file, _lines = _seed_pilot_repo(git_repo)
    candidate = str(_plan(lane_file)[0]["id"])
    candidates = _candidate_file(tmp_path / "one.txt", [candidate])
    verdict = tmp_path / "forbidden-verdict.json"
    cases = (
        ("--verdict-json", str(verdict)),
        ("--shard", "0/2"),
        ("--reuse-from", str(tmp_path / "old.json")),
        ("--rejudge", "f" * 64),
        ("--rejudge-outcome", "killed"),
        ("--pilot-jobs", "0"),
        ("--pilot-jobs", "9"),
        ("--pilot-jobs", "abc"),
        ("--pilot-jobs", "9" * 5000),
    )
    for index, pair in enumerate(cases):
        code, _out, err = _run(
            [
                "run",
                "package",
                "--file",
                str(lane_file),
                "--candidates-file",
                str(candidates),
                "--state-dir",
                str(tmp_path / f"state-{index}"),
                *pair,
            ]
        )
        assert code == 2
        assert "--candidates-file" in err or "--pilot-jobs" in err
    assert not verdict.exists()

    code, _out, err = _run(
        ["run", "package", "--file", str(lane_file), "--candidates-file", str(candidates)]
    )
    assert code == 2 and "--state-dir" in err
    code, _out, err = _run(
        ["run", "package", "--file", str(lane_file), "--pilot-jobs", "2"]
    )
    assert code == 2 and "requires --candidates-file" in err
    code, _out, err = _run(
        [
            "run",
            "r0only",
            "--file",
            str(lane_file),
            "--candidates-file",
            str(candidates),
            "--state-dir",
            str(tmp_path / "r0-state"),
        ]
    )
    assert code == 2 and "native R2" in err


def test_T3_T5_single_killed_candidate_is_completed_and_persisted(git_repo: GitRepo, tmp_path: Path):
    lane_file, lines = _seed_pilot_repo(git_repo)
    rows = _plan(lane_file)
    selected = _ids_by_line(rows)[lines[0]]
    candidates = _candidate_file(tmp_path / "one.txt", [selected])
    state_dir, progress = tmp_path / "state", tmp_path / "progress.jsonl"
    code, stdout, stderr = _run_pilot(lane_file, candidates, state_dir, progress)
    assert code == 6, stderr
    summary = json.loads(stdout)
    assert summary["qualifying"] is False and summary["completed"] is True
    current_judge = next(
        event["judge_sha256"]
        for event in _events(progress)
        if event["event"] == "candidates"
    )
    assert summary["judge_sha256"] == current_judge
    assert summary["requested"] == 1
    assert [row["id"] for row in summary["candidates"]] == [selected]
    assert summary["buckets"]["killed"] == 1
    assert summary["r2"]["status"] == "PASS"
    assert (state_dir / "PILOT-STATE").is_file()
    records = sorted(state_dir.glob("*.json"))
    assert len(records) == 1
    assert json.loads(records[0].read_text(encoding="utf-8"))["candidate_id"] == selected
    assert not list(git_repo.path.rglob("verdict*.json"))
    events = _events(progress)
    candidate_events = [event for event in events if event["event"] == "candidate"]
    assert [event["candidate_id"] for event in candidate_events] == [selected]
    assert events[-1]["event"] == "verdict_written"
    assert events[-1]["destination"] is None


def test_T4_unknown_id_refuses_after_baseline_without_a_candidate_event(git_repo: GitRepo, tmp_path: Path):
    lane_file, _lines = _seed_pilot_repo(git_repo)
    unknown = "f" * 64
    assert unknown not in {str(row["id"]) for row in _plan(lane_file)}
    candidates = _candidate_file(tmp_path / "unknown.txt", [unknown])
    progress = tmp_path / "progress.jsonl"
    code, stdout, _err = _run_pilot(
        lane_file, candidates, tmp_path / "state", progress
    )
    summary = json.loads(stdout)
    assert code == 2 and summary["completed"] is False
    assert summary["refusal"]["reason_code"] == "BAD_LANE_CONFIG"
    events = _events(progress)
    assert any(
        event.get("event") == "command_started" and event.get("phase") == "baseline"
        for event in events
    )
    assert not [event for event in events if event["event"] == "candidate"]
    assert not (tmp_path / "state" / "PILOT-STATE").exists()


def test_T6_pilot_skips_r3_canary(git_repo: GitRepo, tmp_path: Path):
    lane_file, lines = _seed_pilot_repo(git_repo)
    rows = _plan(lane_file, "pilot-r3")
    selected = _ids_by_line(rows)[lines[0]]
    candidates = _candidate_file(tmp_path / "one.txt", [selected])
    progress = tmp_path / "progress.jsonl"
    code, stdout, stderr = _run_pilot(
        lane_file,
        candidates,
        tmp_path / "state",
        progress,
        lane="pilot-r3",
    )
    assert code == 6, stderr
    summary = json.loads(stdout)
    assert summary["r3"] == "not-run: pilot"
    events = _events(progress)
    assert not any(
        "canary" in str(event.get("event", ""))
        or "canary" in str(event.get("phase", ""))
        for event in events
    )
    assert [event["candidate_id"] for event in events if event["event"] == "candidate"] == [selected]


def test_T7_selection_digest_uses_plan_order(git_repo: GitRepo, tmp_path: Path):
    lane_file, lines = _seed_pilot_repo(git_repo)
    rows = _plan(lane_file)
    by_line = _ids_by_line(rows)
    selected = [by_line[lines[1]], by_line[lines[0]]]
    candidates = _candidate_file(tmp_path / "reverse.txt", selected)
    progress = tmp_path / "progress.jsonl"
    code, stdout, stderr = _run_pilot(
        lane_file, candidates, tmp_path / "state", progress
    )
    assert code == 6, stderr
    summary = json.loads(stdout)
    ordered = [str(row["id"]) for row in rows if row["id"] in selected]
    digest = hashlib.sha256(
        "".join(f"{len(item)}:{item}," for item in ordered).encode("ascii")
    ).hexdigest()
    event = next(event for event in _events(progress) if event["event"] == "candidates")
    assert event["selection_sha256"] == summary["selection_sha256"] == digest
    assert [row["id"] for row in summary["candidates"]] == ordered


def test_T8_killed_and_surviving_buckets_still_exit_six(git_repo: GitRepo, tmp_path: Path):
    lane_file, lines = _seed_pilot_repo(git_repo)
    by_line = _ids_by_line(_plan(lane_file))
    candidates = _candidate_file(
        tmp_path / "mix.txt", [by_line[lines[0]], by_line[lines[-1]]]
    )
    code, stdout, stderr = _run_pilot(
        lane_file, candidates, tmp_path / "state", tmp_path / "progress.jsonl"
    )
    assert code == 6, stderr
    summary = json.loads(stdout)
    assert summary["completed"] is True
    assert summary["r2"] == {"status": "FAIL", "reason_code": "MUTANTS_SURVIVED"}
    assert summary["buckets"]["killed"] == 1
    assert summary["buckets"]["survived"] == 1


def test_T9_resume_reuses_the_selected_state_record(git_repo: GitRepo, tmp_path: Path):
    lane_file, lines = _seed_pilot_repo(git_repo)
    selected = _ids_by_line(_plan(lane_file))[lines[0]]
    candidates = _candidate_file(tmp_path / "one.txt", [selected])
    state_dir, progress = tmp_path / "state", tmp_path / "progress.jsonl"
    first, _out, err = _run_pilot(lane_file, candidates, state_dir, progress)
    assert first == 6, err
    second, stdout, err = _run_pilot(
        lane_file,
        candidates,
        state_dir,
        progress,
        extra=("--resume",),
    )
    assert second == 6, err
    assert json.loads(stdout)["completed"] is True
    events = _events(progress)
    headers = [index for index, event in enumerate(events) if event["event"] == "run"]
    assert len(headers) == 2
    second_events = events[headers[1] :]
    assert not [event for event in second_events if event["event"] == "candidate"]
    resume = next(event for event in second_events if event["event"] == "resume")
    assert resume["resumed_total"] == 1


def test_T10_sentinel_rejects_a_different_selection_without_running(git_repo: GitRepo, tmp_path: Path):
    lane_file, lines = _seed_pilot_repo(git_repo)
    by_line = _ids_by_line(_plan(lane_file))
    state_dir = tmp_path / "state"
    first_ids = [by_line[lines[0]]]
    candidates = _candidate_file(tmp_path / "first.txt", first_ids)
    first, _out, err = _run_pilot(
        lane_file, candidates, state_dir, tmp_path / "first.jsonl"
    )
    assert first == 6, err
    original = json.loads((state_dir / "PILOT-STATE").read_text(encoding="utf-8"))
    second_file = _candidate_file(tmp_path / "second.txt", [by_line[lines[1]]])
    second_progress = tmp_path / "second.jsonl"
    code, stdout, stderr = _run_pilot(
        lane_file, second_file, state_dir, second_progress
    )
    assert code == 2 and "PILOT-STATE" in stderr
    assert stdout == ""
    assert json.loads((state_dir / "PILOT-STATE").read_text(encoding="utf-8")) == original
    assert not second_progress.exists()


def test_T10_malformed_sentinel_is_refused_without_progress(
    git_repo: GitRepo, tmp_path: Path
):
    lane_file, _lines = _seed_pilot_repo(git_repo)
    selected = str(_plan(lane_file)[0]["id"])
    candidates = _candidate_file(tmp_path / "one.txt", [selected])
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "PILOT-STATE").write_text("{malformed", encoding="utf-8")
    progress = tmp_path / "progress.jsonl"

    code, stdout, stderr = _run_pilot(lane_file, candidates, state_dir, progress)

    assert code == 2
    assert stdout == ""
    assert "PILOT-STATE" in stderr
    assert not progress.exists()


def test_T10_mutation_discovery_refusal_keeps_r2_reason_and_pilot_terminal(
    git_repo: GitRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    lane_file, _lines = _seed_pilot_repo(git_repo)
    selected = str(_plan(lane_file)[0]["id"])
    candidates = _candidate_file(tmp_path / "one.txt", [selected])
    progress = tmp_path / "progress.jsonl"

    def fail_discovery(*_args, **_kwargs):
        raise mutation.MutationDiscoveryError("synthetic mutation discovery failure")

    monkeypatch.setattr(cli, "_discover_plan_jobs", fail_discovery)
    code, stdout, stderr = _run_pilot(
        lane_file, candidates, tmp_path / "state", progress
    )

    assert code == 2
    assert "synthetic mutation discovery failure" in stderr
    summary = json.loads(stdout)
    assert summary["completed"] is False
    assert summary["r0"] is None
    assert summary["r1"] is None
    assert summary["r2"] is None
    assert summary["refusal"]["reason_code"] == "MUTATION_DISCOVERY_FAILED"
    assert summary["unresolved"] == [selected]
    events = _events(progress)
    assert events[-1]["event"] == "verdict_written"
    assert events[-1]["reason_code"] == "MUTATION_DISCOVERY_FAILED"


def test_T10d_nonpilot_records_cannot_seed_a_pilot_state_dir(git_repo: GitRepo, tmp_path: Path):
    lane_file, _lines = _seed_pilot_repo(git_repo)
    state_dir = tmp_path / "state"
    ordinary, _stdout, stderr = _run(
        ["run", "package", "--file", str(lane_file), "--resume", "--state-dir", str(state_dir)]
    )
    assert ordinary == 1, stderr  # the ordinary full lane has a surviving mutant
    assert list(state_dir.glob("*.json"))
    selected = str(_plan(lane_file)[0]["id"])
    candidates = _candidate_file(tmp_path / "one.txt", [selected])
    progress = tmp_path / "pilot.jsonl"
    code, _out, err = _run_pilot(lane_file, candidates, state_dir, progress)
    assert code == 2 and "PILOT-STATE" in err
    assert not (state_dir / "PILOT-STATE").exists()
    assert not progress.exists()


def test_T10e_qualifying_run_cannot_reuse_pilot_state(
    git_repo: GitRepo, tmp_path: Path
):
    lane_file, _lines = _seed_pilot_repo(git_repo)
    selected = str(_plan(lane_file)[0]["id"])
    candidates = _candidate_file(tmp_path / "one.txt", [selected])
    state_dir = tmp_path / "state"
    pilot_progress = tmp_path / "pilot.jsonl"
    pilot_code, _pilot_out, pilot_err = _run_pilot(
        lane_file, candidates, state_dir, pilot_progress
    )
    assert pilot_code == 6, pilot_err

    qualifying_progress = tmp_path / "qualifying.jsonl"
    code, stdout, stderr = _run(
        [
            "run",
            "package",
            "--file",
            str(lane_file),
            "--resume",
            "--state-dir",
            str(state_dir),
            "--progress",
            str(qualifying_progress),
        ]
    )

    assert code == 2
    assert stdout == ""
    assert "PILOT-STATE" in stderr
    assert not qualifying_progress.exists()


def test_T10f_qualifying_resume_checks_the_implicit_default_state_root(
    git_repo: GitRepo, tmp_path: Path
):
    lane_file, _lines = _seed_pilot_repo(git_repo)
    git_repo.write(".gitignore", ".assay/\n")
    git_repo.commit_all("ignore default Assay state directory")
    selected = str(_plan(lane_file)[0]["id"])
    candidates = _candidate_file(tmp_path / "one.txt", [selected])
    default_state = mutation.default_state_root(git_repo.path)
    pilot_code, _pilot_out, pilot_err = _run_pilot(
        lane_file,
        candidates,
        default_state,
        tmp_path / "pilot.jsonl",
    )
    assert pilot_code == 6, pilot_err
    assert (default_state / "PILOT-STATE").is_file()

    progress = tmp_path / "qualifying.jsonl"
    code, stdout, stderr = _run(
        [
            "run",
            "package",
            "--file",
            str(lane_file),
            "--resume",
            "--progress",
            str(progress),
        ]
    )

    assert code == 2
    assert stdout == ""
    assert "PILOT-STATE" in stderr
    assert not progress.exists()


def test_T10_malformed_or_wrong_lane_sentinels_are_refused(tmp_path: Path):
    digest = "a" * 64
    cases = (
        (b'{"schema":"assay-pilot-state/1","selection_sha256":"' + digest.encode() + b'","lane":"x","lane":"x"}', "malformed"),
        (b'{"schema":"assay-pilot-state/0","selection_sha256":"' + digest.encode() + b'","lane":"x"}', "malformed"),
        (json.dumps({"schema": "assay-pilot-state/1", "selection_sha256": digest, "lane": "other"}).encode(), "different"),
    )
    for index, (raw, message) in enumerate(cases):
        root = tmp_path / f"state-{index}"
        root.mkdir()
        (root / "PILOT-STATE").write_bytes(raw)
        with pytest.raises(LaneConfigError, match="PILOT-STATE"):
            cli._ensure_pilot_state(root, lane="package", selection_sha256=digest)
        if message == "different":
            with pytest.raises(LaneConfigError, match="belongs to lane"):
                cli._ensure_pilot_state(root, lane="package", selection_sha256=digest)


def test_T13_campaign_digest_binds_full_plan_before_pilot_selection(
    git_repo: GitRepo, tmp_path: Path
):
    lane_file, lines = _seed_pilot_repo(git_repo)
    rows = _plan(lane_file)
    selected = _ids_by_line(rows)[lines[0]]
    candidates = _candidate_file(tmp_path / "one.txt", [selected])
    state_dir = tmp_path / "valid-state"
    deadline = tmp_path / "full-plan-deadline.json"
    init_code, _init_out, init_err = _run(
        [
            "campaign",
            "init",
            "--campaign",
            "t13-full",
            "--lane",
            "package",
            "--hours",
            "1",
            "--file",
            str(lane_file),
            "--state-dir",
            str(state_dir),
            "--out",
            str(deadline),
        ]
    )
    assert init_code == 0, init_err
    deadline_bytes = deadline.read_bytes()
    code, stdout, stderr = _run_pilot(
        lane_file,
        candidates,
        state_dir,
        tmp_path / "valid-progress.jsonl",
        extra=("--campaign-deadline", str(deadline)),
    )
    assert code == 6, stderr
    assert json.loads(stdout)["completed"] is True
    record = next(state_dir.glob("*.json"))
    document = json.loads(record.read_text(encoding="utf-8"))
    assert document["campaign_deadline_sha256"] == hashlib.sha256(deadline_bytes).hexdigest()

    bad_state = tmp_path / "selected-only-state"
    selected_deadline = tmp_path / "selected-only-deadline.json"
    init_code, _init_out, init_err = _run(
        [
            "campaign",
            "init",
            "--campaign",
            "t13-selected-only",
            "--lane",
            "package",
            "--hours",
            "1",
            "--file",
            str(lane_file),
            "--state-dir",
            str(bad_state),
            "--out",
            str(selected_deadline),
        ]
    )
    assert init_code == 0, init_err
    invalid = json.loads(selected_deadline.read_text(encoding="utf-8"))
    invalid["plan_sha256"]["package"] = mutation.plan_sha256([selected])
    selected_deadline.write_text(
        json.dumps(invalid, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    bad_progress = tmp_path / "selected-only-progress.jsonl"
    code, stdout, stderr = _run_pilot(
        lane_file,
        candidates,
        bad_state,
        bad_progress,
        extra=("--campaign-deadline", str(selected_deadline)),
    )
    assert code == 2, stderr
    summary = json.loads(stdout)
    assert summary["completed"] is False
    assert summary["refusal"]["reason_code"] == "BAD_LANE_CONFIG"
    assert not [event for event in _events(bad_progress) if event["event"] == "candidate"]


def test_T11_head_timeout_still_prints_pilot_summary_and_terminal_event(git_repo: GitRepo, tmp_path: Path):
    lane_file, _lines = _seed_pilot_repo(git_repo)
    candidate = str(_plan(lane_file)[0]["id"])
    text = lane_file.read_text(encoding="utf-8").replace('budget = "2m"', 'budget = "0.001s"')
    git_repo.write("assay.toml", text)
    git_repo.commit_all("make pilot deadline exhausted before HEAD")
    candidates = _candidate_file(tmp_path / "one.txt", [candidate])
    progress = tmp_path / "progress.jsonl"
    code, stdout, stderr = _run_pilot(
        lane_file, candidates, tmp_path / "state", progress
    )
    assert code == 4, stderr
    summary = json.loads(stdout)
    assert summary["completed"] is False
    assert summary["refusal"]["reason_code"] == "LANE_TIMEOUT"
    events = _events(progress)
    assert events[-1]["event"] == "verdict_written"
    assert events[-1]["destination"] is None
    assert "package: BUDGET_EXCEEDED" not in stdout


def test_T12_one_verdict_write_site_remains_inside_finish():
    source = Path(cli.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    reserved = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_run_reserved"
    )
    calls = [
        node
        for node in ast.walk(reserved)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "runner"
        and node.func.attr == "write_verdict"
    ]
    finish = next(
        node
        for node in reserved.body
        if isinstance(node, ast.FunctionDef) and node.name == "_finish"
    )
    assert len(calls) == 1
    assert calls[0] in list(ast.walk(finish))


def test_T14_noncompleted_pass_is_explicitly_reported_as_error(
    git_repo: GitRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    lane_file, lines = _seed_pilot_repo(git_repo)
    selected = _ids_by_line(_plan(lane_file))[lines[0]]
    candidates = _candidate_file(tmp_path / "one.txt", [selected])
    monkeypatch.setattr(
        cli,
        "_pilot_completed",
        lambda _verdict, _ids, *, unresolved: False,
    )
    code, _stdout, stderr = _run_pilot(
        lane_file, candidates, tmp_path / "state", tmp_path / "progress.jsonl"
    )
    assert code == 2
    assert "a non-completed selection produced a PASS verdict; reporting ERROR" in stderr


def test_T15_invalid_budget_record_keeps_pilot_incomplete(
    git_repo: GitRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    lane_file, lines = _seed_pilot_repo(git_repo)
    by_line = _ids_by_line(_plan(lane_file))
    budget_id, survivor_id = by_line[lines[0]], by_line[lines[-1]]
    candidates = _candidate_file(
        tmp_path / "two.txt", [budget_id, survivor_id]
    )
    state_dir = tmp_path / "state"
    original_run_lane = cli.runner.run_lane

    def run_with_invalid_budget_record(*args, **kwargs):
        verdict = original_run_lane(*args, **kwargs)
        record_path = state_dir / f"{budget_id}.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["outcome_bucket"] = "budget_exceeded"
        record.pop("source_sha256", None)
        record_path.write_text(json.dumps(record), encoding="utf-8")

        r2 = next(claim for claim in verdict.claims if claim.rigor == "R2")
        assert r2.mutation is not None
        budget_outcome = next(
            item for item in r2.mutation.killed if item.candidate_id == budget_id
        )
        changed_mutation = replace(
            r2.mutation,
            killed=tuple(
                item for item in r2.mutation.killed if item.candidate_id != budget_id
            ),
            budget_exceeded=(*r2.mutation.budget_exceeded, budget_outcome),
        )
        changed_r2 = replace(r2, mutation=changed_mutation)
        changed_claims = tuple(
            changed_r2 if claim is r2 else claim for claim in verdict.claims
        )
        return replace(verdict, claims=changed_claims)

    monkeypatch.setattr(cli.runner, "run_lane", run_with_invalid_budget_record)
    code, stdout, _stderr = _run_pilot(
        lane_file,
        candidates,
        state_dir,
        tmp_path / "progress.jsonl",
    )

    summary = json.loads(stdout)
    assert budget_id in summary["unresolved"]
    assert summary["completed"] is False
    assert code != 6


def test_pilot_state_sentinel_creation_and_same_selection_resume(tmp_path: Path):
    state_dir = tmp_path / "state"
    digest = "c" * 64
    cli._ensure_pilot_state(state_dir, lane="package", selection_sha256=digest)
    cli._ensure_pilot_state(state_dir, lane="package", selection_sha256=digest)
    assert json.loads((state_dir / "PILOT-STATE").read_text(encoding="utf-8")) == {
        "schema": "assay-pilot-state/1",
        "selection_sha256": digest,
        "lane": "package",
    }


def test_pilot_state_refuses_existing_record_without_sentinel(tmp_path: Path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / ("d" * 64 + ".json")).write_text("{}", encoding="utf-8")
    with pytest.raises(LaneConfigError, match="PILOT-STATE"):
        cli._ensure_pilot_state(state_dir, lane="package", selection_sha256="e" * 64)


@pytest.mark.parametrize("pilot", [False, True], ids=["qualifying-resume", "pilot"])
def test_state_store_lock_is_held_through_reserved_run(
    git_repo: GitRepo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pilot: bool,
):
    lane_file, _lines = _seed_pilot_repo(git_repo)
    state_dir = tmp_path / "state"
    def inspect_lock(*_args, **_kwargs):
        _assert_state_lock_refuses_promptly(state_dir)
        return 0

    monkeypatch.setattr(cli, "_run_reserved", inspect_lock)
    argv = ["run", "package", "--file", str(lane_file), "--state-dir", str(state_dir)]
    if pilot:
        candidate = str(_plan(lane_file)[0]["id"])
        candidates = _candidate_file(tmp_path / "one.txt", [candidate])
        argv.extend(["--candidates-file", str(candidates)])
    else:
        argv.append("--resume")
    code, _stdout, stderr = _run(argv)
    assert code == 0, stderr

    root_fd, lock_fds, _root = cli._acquire_state_directory_lock(state_dir)
    cli._release_state_directory_lock(root_fd, lock_fds)


def test_state_store_lock_is_nonblocking_and_survives_directory_replacement(
    tmp_path: Path,
):
    state_dir = tmp_path / ".assay" / "state"
    state_dir.mkdir(parents=True)
    root_fd, lock_fds, canonical_root = cli._acquire_state_directory_lock(state_dir)
    try:
        moved_parent = tmp_path / ".assay-moved"
        state_dir.parent.rename(moved_parent)
        state_dir.parent.mkdir()
        state_dir.mkdir()
        _assert_state_lock_refuses_promptly(moved_parent / "state")

        with pytest.raises(LaneConfigError, match="changed while locked"):
            cli._verify_state_directory_identity(canonical_root, root_fd)
    finally:
        cli._release_state_directory_lock(root_fd, lock_fds)

    replacement_root_fd, replacement_lock_fds, replacement_root = cli._acquire_state_directory_lock(state_dir)
    try:
        cli._verify_state_directory_identity(replacement_root, replacement_root_fd)
    finally:
        cli._release_state_directory_lock(replacement_root_fd, replacement_lock_fds)


def test_state_path_lock_survives_parent_replacement_at_same_spelling(
    tmp_path: Path,
):
    state_dir = tmp_path / ".assay" / "state"
    state_dir.mkdir(parents=True)
    root_fd, lock_fds, _canonical_root = cli._acquire_state_directory_lock(state_dir)
    moved_parent = tmp_path / ".assay-moved"
    try:
        state_dir.parent.rename(moved_parent)
        state_dir.parent.mkdir()
        state_dir.mkdir()
        _assert_state_lock_refuses_promptly(state_dir)
    finally:
        cli._release_state_directory_lock(root_fd, lock_fds)


def test_state_path_lock_parent_replacement_is_shared_across_tmpdirs(
    tmp_path: Path,
):
    import time

    state_parent = tmp_path / "state-parent"
    state_parent.mkdir()
    state_dir = state_parent / "store"
    state_dir.mkdir()
    moved_parent = tmp_path / "state-parent-moved"
    first_tmp = tmp_path / "first-tmp"
    second_tmp = tmp_path / "second-tmp"
    first_tmp.mkdir()
    second_tmp.mkdir()
    ready = tmp_path / "lock-held"
    release = tmp_path / "release-lock"
    child = r"""
from pathlib import Path
import sys
import time
from assay import cli

state_dir, ready, release = map(Path, sys.argv[1:])
root_fd, lock_fds, _root = cli._acquire_state_directory_lock(state_dir)
try:
    ready.write_text("held", encoding="utf-8")
    deadline = time.monotonic() + 20
    while not release.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("timed out waiting for lock release")
        time.sleep(0.01)
finally:
    cli._release_state_directory_lock(root_fd, lock_fds)
"""
    first_env = os.environ.copy()
    first_env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    first_env["TMPDIR"] = str(first_tmp)
    second_result: subprocess.CompletedProcess[str] | None = None
    first_stdout = first_stderr = ""
    ready_seen = False
    first_process = subprocess.Popen(
        [sys.executable, "-c", child, str(state_dir), str(ready), str(release)],
        cwd=PROJECT_ROOT,
        env=first_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and first_process.poll() is None:
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        ready_seen = ready.exists()
        if ready_seen:
            state_parent.rename(moved_parent)
            state_parent.mkdir()
            state_dir.mkdir()
            second_env = os.environ.copy()
            second_env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
            second_env["TMPDIR"] = str(second_tmp)
            second_source = r"""
from pathlib import Path
import sys
from assay import cli
from assay.errors import LaneConfigError

try:
    root_fd, lock_fds, _root = cli._acquire_state_directory_lock(Path(sys.argv[1]))
except LaneConfigError as exc:
    if "already in use" not in str(exc):
        raise
    print("REFUSED")
else:
    cli._release_state_directory_lock(root_fd, lock_fds)
    print("ACQUIRED")
    raise SystemExit(1)
"""
            second_result = subprocess.run(
                [sys.executable, "-c", second_source, str(state_dir)],
                cwd=PROJECT_ROOT,
                env=second_env,
                capture_output=True,
                text=True,
                timeout=10,
            )
    finally:
        try:
            if first_process.poll() is None:
                release.write_text("continue", encoding="utf-8")
        except OSError:
            try:
                first_process.kill()
            except OSError:
                pass
        try:
            first_stdout, first_stderr = first_process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                first_process.kill()
            except OSError:
                pass
            first_stdout, first_stderr = first_process.communicate(timeout=10)

    assert ready_seen, f"first process never acquired the lock: {first_stdout}\n{first_stderr}"
    assert first_process.returncode == 0, first_stdout + first_stderr
    assert second_result is not None
    assert second_result.returncode == 0, second_result.stdout + second_result.stderr
    assert second_result.stdout.strip() == "REFUSED"


def test_external_state_lock_is_shared_across_independent_git_clones(
    git_repo: GitRepo, tmp_path: Path
):
    lane_file, _lines = _seed_pilot_repo(git_repo)
    second_clone = tmp_path / "second-clone"
    import subprocess

    subprocess.run(
        ["git", "clone", "--quiet", str(git_repo.path), str(second_clone)],
        check=True,
        capture_output=True,
        text=True,
    )
    external_state = tmp_path / "shared" / "mutation-state"
    root_fd, lock_fds, _root = cli._acquire_state_directory_lock(
        external_state
    )
    try:
        _assert_state_lock_refuses_promptly(external_state)
    finally:
        cli._release_state_directory_lock(root_fd, lock_fds)


def test_mutation_record_writer_stays_in_admitted_directory_after_path_replacement(
    tmp_path: Path,
):
    state_dir = tmp_path / ".assay" / "state"
    state_dir.mkdir(parents=True)
    root_fd = os.open(
        state_dir,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    moved_parent = tmp_path / ".assay-moved"
    try:
        state_dir.parent.rename(moved_parent)
        state_dir.parent.mkdir()
        state_dir.mkdir()
        mutation._write_mutation_state_record(
            state_dir,
            {"candidate_id": "a" * 64},
            state_root_fd=root_fd,
        )
        record_name = mutation.mutation_state_record_name("a" * 64)
        assert not (state_dir / record_name).exists()
        assert (moved_parent / "state" / record_name).is_file()
    finally:
        os.close(root_fd)


def test_replaced_state_parent_refuses_run_certification(
    git_repo: GitRepo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    lane_file, _lines = _seed_pilot_repo(git_repo)
    state_dir = tmp_path / ".assay" / "state"
    state_dir.mkdir(parents=True)
    moved_parent = tmp_path / ".assay-moved"

    def replace_before_finish(*_args, state_lock_guard=None, **_kwargs):
        state_dir.parent.rename(moved_parent)
        state_dir.parent.mkdir()
        state_dir.mkdir()
        assert state_lock_guard is not None
        state_lock_guard()
        return 0

    monkeypatch.setattr(cli, "_run_reserved", replace_before_finish)
    code, stdout, stderr = _run(
        ["run", "package", "--file", str(lane_file), "--state-dir", str(state_dir), "--resume"]
    )
    assert code == 2
    assert stdout == ""
    assert "changed while locked" in stderr


def test_replaced_state_directory_refuses_run_certification(
    git_repo: GitRepo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    lane_file, _lines = _seed_pilot_repo(git_repo)
    state_dir = tmp_path / "state"
    moved_dir = tmp_path / "moved-state"

    def replace_before_finish(*_args, state_lock_guard=None, **_kwargs):
        state_dir.rename(moved_dir)
        state_dir.mkdir()
        assert state_lock_guard is not None
        state_lock_guard()
        return 0

    monkeypatch.setattr(cli, "_run_reserved", replace_before_finish)
    code, stdout, stderr = _run(
        ["run", "package", "--file", str(lane_file), "--state-dir", str(state_dir), "--resume"]
    )
    assert code == 2
    assert stdout == ""
    assert "changed while locked" in stderr


def test_consumer_pilot_example_binds_the_full_plan_and_uses_cold_witness():
    project_root = Path(__file__).resolve().parents[2]
    document = (project_root / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    section = document.split("## Run a non-qualifying native-R2 pilot (B118)\n", 1)[1]
    example = section.split("## Resume and shard a long mutation lane", 1)[0]
    manual_example = example.split("The local command sequence below", 1)[1]
    assert "assay campaign init" in example
    assert 'manual_dir=".assay/manual-b110-pilot-${commit12}"' in example
    assert 'campaign="manual-b110-pilot-${commit12}"' in example
    assert 'deadline=".assay/campaign-deadline-${campaign}.json"' in example
    assert '--state-dir "$pilot_state"' in example
    assert '--progress "$pilot_progress"' in example
    assert '> "$pilot_summary" 2> "$pilot_log"' in example
    assert ".assay/b110-pilot-state" not in manual_example
    assert ".assay/progress-b110-pilot.jsonl" not in manual_example
    assert "--cold-witness --resume --campaign-deadline \"$deadline\"" in example
    assert 'timeout --verbose --signal=TERM --kill-after=30s "${pilot_run_s}s"' in example
    assert 'pilot_after_run_s="$(pilot_remaining_s "$SECONDS")"' in example
    assert "pilot_status=124" in example
    assert "source_sha256" in section
    assert "current Git HEAD and tree" in section
    assert "dirty checkout" in section
    assert "raw plan digest, and selected IDs" in section
    assert "measurement only" in example


def test_pilot_budget_record_rejects_a_relabelled_killed_record(
    git_repo: GitRepo, tmp_path: Path
):
    lane_file, _lines = _seed_pilot_repo(git_repo)
    selected = str(_plan(lane_file)[0]["id"])
    candidates = _candidate_file(tmp_path / "one.txt", [selected])
    state_dir, progress = tmp_path / "state", tmp_path / "progress.jsonl"

    code, _stdout, stderr = _run_pilot(lane_file, candidates, state_dir, progress)
    assert code == 6, stderr
    judge_sha256 = next(
        event["judge_sha256"]
        for event in _events(progress)
        if event["event"] == "candidates"
    )
    job = _job_for_candidate(lane_file, selected)
    record_path = state_dir / f"{selected}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))

    assert record["outcome_bucket"] == "killed"
    record["outcome_bucket"] = "budget_exceeded"
    record_path.write_text(json.dumps(record), encoding="utf-8")
    assert not cli._pilot_has_budget_record(
        state_dir,
        job,
        judge_sha256=judge_sha256,
        campaign_deadline_sha256=None,
        cold_witness=False,
    )


def test_validated_resume_reads_state_records_from_admitted_directory_fd(
    git_repo: GitRepo, tmp_path: Path
):
    lane_file, _lines = _seed_pilot_repo(git_repo)
    selected = str(_plan(lane_file)[0]["id"])
    candidates = _candidate_file(tmp_path / "one.txt", [selected])
    state_dir, progress = tmp_path / "state", tmp_path / "progress.jsonl"
    code, _stdout, stderr = _run_pilot(lane_file, candidates, state_dir, progress)
    assert code == 6, stderr
    judge_sha256 = next(
        event["judge_sha256"]
        for event in _events(progress)
        if event["event"] == "candidates"
    )
    job = _job_for_candidate(lane_file, selected)
    root_fd = os.open(
        state_dir,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    moved = tmp_path / "moved-state"
    try:
        state_dir.rename(moved)
        state_dir.mkdir()
        (state_dir / mutation.mutation_state_record_name(selected)).write_text(
            "{}", encoding="utf-8"
        )
        record = mutation._load_validated_state_record(
            state_dir,
            job,
            judge=judge_sha256,
            state_root_fd=root_fd,
        )
        assert isinstance(record, dict)
        assert record["candidate_id"] == selected
    finally:
        os.close(root_fd)


def test_real_candidate_timeout_record_is_valid_but_not_a_completed_pilot(
    git_repo: GitRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    lane_file, lines = _seed_pilot_repo(git_repo)
    selected = _ids_by_line(_plan(lane_file))[lines[0]]
    candidates = _candidate_file(tmp_path / "one.txt", [selected])
    state_dir, progress = tmp_path / "state", tmp_path / "progress.jsonl"
    baseline_source = (lane_file.parent / "src/mod.py").read_bytes()
    original_execute_plan = cli.runner.execute_plan
    fixed_time = "2026-10-08T00:00:00+00:00"

    def timeout_mutant(plan, *, cwd, **kwargs):
        if (cwd / "src/mod.py").read_bytes() != baseline_source:
            return cli.runner.CommandResult(
                plan=plan,
                outcome=Outcome.BUDGET_EXCEEDED,
                reason_code=ReasonCode.LANE_TIMEOUT,
                returncode=None,
                started=fixed_time,
                ended=fixed_time,
            )
        return original_execute_plan(plan, cwd=cwd, **kwargs)

    monkeypatch.setattr(cli.runner, "execute_plan", timeout_mutant)
    code, stdout, stderr = _run_pilot(
        lane_file, candidates, state_dir, progress
    )

    summary = json.loads(stdout)
    assert code == 4, stderr
    assert summary["completed"] is False
    assert summary["r2"] == {
        "status": "BUDGET_EXCEEDED",
        "reason_code": "LANE_TIMEOUT",
    }
    assert summary["unresolved"] == []
    record_path = state_dir / f"{selected}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["terminal_result"] == {
        "outcome": "BUDGET_EXCEEDED",
        "reason_code": "LANE_TIMEOUT",
        "returncode": None,
    }
    judge_sha256 = next(
        event["judge_sha256"]
        for event in _events(progress)
        if event["event"] == "candidates"
    )
    job = _job_for_candidate(lane_file, selected)
    assert cli._pilot_has_budget_record(
        state_dir,
        job,
        judge_sha256=judge_sha256,
        campaign_deadline_sha256=None,
        cold_witness=False,
    )

    valid_record = json.loads(record_path.read_text(encoding="utf-8"))

    relabelled_timeout = json.loads(json.dumps(valid_record))
    relabelled_timeout["outcome_bucket"] = "killed"
    record_path.write_text(json.dumps(relabelled_timeout), encoding="utf-8")
    assert mutation._load_validated_state_record(
        state_dir,
        job,
        judge=judge_sha256,
        campaign_deadline_sha256=None,
        cold_witness=False,
    ) is mutation._RECORD_REJECTED

    def assert_rejected(change) -> None:
        changed = json.loads(json.dumps(valid_record))
        change(changed)
        record_path.write_text(json.dumps(changed), encoding="utf-8")
        assert not cli._pilot_has_budget_record(
            state_dir,
            job,
            judge_sha256=judge_sha256,
            campaign_deadline_sha256=None,
            cold_witness=False,
        )

    assert_rejected(lambda changed: changed.__setitem__("campaign_deadline_sha256", "f" * 64))
    assert_rejected(lambda changed: changed.pop("execution"))
    assert_rejected(lambda changed: changed.pop("evidence"))
    assert_rejected(lambda changed: changed.pop("source_sha256"))
    assert_rejected(lambda changed: changed.pop("mutated_file_sha256"))
    assert_rejected(lambda changed: changed.__setitem__("mutated_file_sha256", "f" * 64))


def test_malformed_terminal_evidence_refuses_before_pilot_summary(
    git_repo: GitRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    lane_file, lines = _seed_pilot_repo(git_repo)
    selected = _ids_by_line(_plan(lane_file))[lines[0]]
    candidates = _candidate_file(tmp_path / "one.txt", [selected])
    state_dir, progress = tmp_path / "state", tmp_path / "progress.jsonl"
    baseline_source = (lane_file.parent / "src/mod.py").read_bytes()
    original_execute_plan = cli.runner.execute_plan
    fixed_time = "2026-10-08T00:00:00+00:00"

    def timeout_mutant(plan, *, cwd, **kwargs):
        if (cwd / "src/mod.py").read_bytes() != baseline_source:
            return cli.runner.CommandResult(
                plan=plan,
                outcome=Outcome.BUDGET_EXCEEDED,
                reason_code=ReasonCode.LANE_TIMEOUT,
                returncode=None,
                started=fixed_time,
                ended=fixed_time,
            )
        return original_execute_plan(plan, cwd=cwd, **kwargs)

    monkeypatch.setattr(cli.runner, "execute_plan", timeout_mutant)
    original_build_summary = cli._build_pilot_summary

    def corrupt_terminal_before_summary(*args, **kwargs):
        record_path = state_dir / f"{selected}.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["terminal_result"] = {"outcome": "BUDGET_EXCEEDED"}
        record.pop("execution")
        record_path.write_text(json.dumps(record), encoding="utf-8")
        return original_build_summary(*args, **kwargs)

    monkeypatch.setattr(cli, "_build_pilot_summary", corrupt_terminal_before_summary)
    code, stdout, stderr = _run_pilot(
        lane_file, candidates, state_dir, progress
    )

    assert code != 6
    assert stdout == ""
    assert "invalid terminal evidence" in stderr
    assert "unresolved" not in stderr


@pytest.mark.parametrize(
    "missing_field", ["execution", "evidence", "mutated_file_sha256"]
)
def test_resume_reexecutes_incomplete_budget_record(
    git_repo: GitRepo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_field: str,
):
    lane_file, lines = _seed_pilot_repo(git_repo)
    selected = _ids_by_line(_plan(lane_file))[lines[0]]
    candidates = _candidate_file(tmp_path / "one.txt", [selected])
    state_dir, progress = tmp_path / "state", tmp_path / "progress.jsonl"
    baseline_source = (lane_file.parent / "src/mod.py").read_bytes()
    original_execute_plan = cli.runner.execute_plan
    fixed_time = "2026-10-08T00:00:00+00:00"
    mutant_runs = 0

    def timeout_mutant(plan, *, cwd, **kwargs):
        nonlocal mutant_runs
        if (cwd / "src/mod.py").read_bytes() != baseline_source:
            mutant_runs += 1
            return cli.runner.CommandResult(
                plan=plan,
                outcome=Outcome.BUDGET_EXCEEDED,
                reason_code=ReasonCode.LANE_TIMEOUT,
                returncode=None,
                started=fixed_time,
                ended=fixed_time,
            )
        return original_execute_plan(plan, cwd=cwd, **kwargs)

    monkeypatch.setattr(cli.runner, "execute_plan", timeout_mutant)
    first_code, _first_stdout, first_stderr = _run_pilot(
        lane_file, candidates, state_dir, progress
    )
    assert first_code == 4, first_stderr
    assert mutant_runs == 1

    record_path = state_dir / f"{selected}.json"
    broken_record = json.loads(record_path.read_text(encoding="utf-8"))
    broken_record.pop(missing_field)
    record_path.write_text(json.dumps(broken_record), encoding="utf-8")

    resumed_code, resumed_stdout, resumed_stderr = _run_pilot(
        lane_file,
        candidates,
        state_dir,
        progress,
        extra=("--resume",),
    )
    assert resumed_code == 4, resumed_stderr
    assert mutant_runs == 2, "an incomplete timeout cache must execute the candidate again"
    assert json.loads(resumed_stdout)["unresolved"] == []
    assert any(
        event.get("event") == "resume" and event.get("rejected_total") == 1
        for event in _events(progress)
    )
    repaired_record = json.loads(record_path.read_text(encoding="utf-8"))
    assert missing_field in repaired_record
