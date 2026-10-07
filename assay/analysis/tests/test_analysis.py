"""Behavioral evidence oracles: preserve adverse results and refuse false bindings."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from analysis.tests.analysis_support import JUDGE_VERDICT_FIXTURES, PROJECT_ROOT
from assay.cli import main
from assay.verdict import VERDICT_SCHEMA_VERSION
from assay_analysis import cli as analysis_cli
from assay_analysis import evidence as analysis


def cli(*args):
    out, err = io.StringIO(), io.StringIO()
    code = main(["analyze", *map(str, args)], stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    for args in (("init", "-q"), ("config", "user.name", "Evidence Test"),
                 ("config", "user.email", "evidence@example.invalid")):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    (root / ".gitignore").write_text(".evidence/\n")
    (root / "source.txt").write_text("original\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)
    head, tree = analysis._git(root, "rev-parse", "HEAD", "HEAD^{tree}").splitlines()
    return root, head, tree


def recorded(tmp_path, head, tree, exit_code=0):
    prefix = tmp_path / "job.with.dots"
    for suffix in (".identity", ".final-identity"):
        Path(str(prefix) + suffix).write_text(head + "\n" + tree + "\n")
    for suffix in (".status", ".final-status"):
        Path(str(prefix) + suffix).write_bytes(b"")
    Path(str(prefix) + ".log").write_text(
        'COMMAND=["pytest", "tests", "-q"]\njob output\nJOB_EXIT=' + str(exit_code) + "\n")
    return prefix


def verdict(tmp_path, head, name="r0_pass"):
    fixture = JUDGE_VERDICT_FIXTURES / (name + ".json")
    document = json.loads(fixture.read_text())
    document["commit"] = head
    path = tmp_path / (name + ".json")
    path.write_bytes((json.dumps(document, indent=2) + "\n").replace("\n", "\r\n").encode())
    return path


def _report_pair(head, args, expected_exit, *, contains=()):
    outputs = {}
    for output_format in ("json", "text"):
        code, out, err = cli("report", "--expected-commit", head, *args,
                             "--format", output_format)
        assert code == expected_exit
        assert err == ""
        for fragment in contains:
            assert fragment in out
        outputs[output_format] = out
    result = json.loads(outputs["json"])
    schema = json.loads(files("assay").joinpath("schemas/analysis-report.schema.json").read_text())
    Draft202012Validator(schema).validate(result)
    return result, outputs["text"]


def _write_progress(path, head, *events, torn=b"", lane="package"):
    records = [{"event": "run", "commit": head, "lane": lane}, *events]
    path.write_bytes(b"".join(json.dumps(record).encode() + b"\n" for record in records) + torn)


def _worktree_snapshot(root):
    """Capture tracked, untracked, and ignored worktree content, excluding Git internals."""
    snapshot = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if ".git" in relative.parts:
            continue
        if path.is_symlink():
            snapshot[relative.as_posix()] = ("symlink", os.readlink(path))
        elif path.is_dir():
            snapshot[relative.as_posix()] = ("directory", None)
        elif path.is_file():
            snapshot[relative.as_posix()] = ("file", path.read_bytes())
    return snapshot


def _launcher(tmp_path, head, exit_code=0):
    directory = tmp_path / "tester"
    directory.mkdir()
    launch = {"git_head": head, "container_id": "012345", "container_name": "evidence-test",
              "user": "1003", "workdir": "/workspace", "cgroup_parent": "fixture.slice",
              "nano_cpus": "3000000000"}
    (directory / "launch.txt").write_text("".join(f"{key}={value}\n" for key, value in launch.items()))
    container = {"Id": "012345", "Name": "/evidence-test", "Config": {
        "User": "1003", "WorkingDir": "/workspace"}, "HostConfig": {
        "CgroupParent": "fixture.slice", "NanoCpus": 3000000000}}
    (directory / "container.inspect.json").write_text(json.dumps([container]))
    (directory / "docker-wait.exit").write_text(str(exit_code) + "\n")
    (directory / "container.log").write_text(f"output\nTESTER_UNIFIED_JOB_EXIT={exit_code}\n")
    (directory / "launch-memory-pressure.txt").write_text("full avg10=0.42\n")
    return directory


def test_archive_relocates_and_checks_copied_binary_bytes(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"binary\x00\xff\r\n")
    output = tmp_path / "archive"
    code, out, err = cli("collect", "--output", output, "--artifact", "one/log", source,
                         "--artifact", "two/manifest.json", source)
    assert (code, err) == (0, "")
    manifest = json.loads(out)
    assert manifest["artifacts"]["one/log"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert manifest == json.loads((output / "manifest.json").read_text())
    source.unlink()  # Archive consumption must not reopen the old source paths.
    moved = tmp_path / "moved"
    output.rename(moved)
    assert analysis.check_archive(moved)["artifact_count"] == 2
    (moved / "one/log").write_bytes(b"changed")
    assert cli("check", moved)[0] == 1


@pytest.mark.parametrize("name", ["", ".", "../outside", "/absolute", "x/../y", "x//y",
                                   "manifest.json", "manifest.json/file"])
def test_unsafe_archive_names_never_create_output(tmp_path, name):
    source = tmp_path / "file"
    source.write_text("data")
    output = tmp_path / "archive"
    assert cli("collect", "--output", output, "--artifact", name, source)[0] == 1
    assert not output.exists()


@pytest.mark.parametrize("problem", ["missing", "directory", "duplicate", "ancestor", "existing"])
def test_archive_refuses_missing_collision_and_overwrite(tmp_path, problem):
    source = tmp_path / "source"
    source.write_text("data")
    output = tmp_path / "archive"
    args = ["collect", "--output", output, "--artifact", "file", source]
    if problem == "missing":
        source.unlink()
    elif problem == "directory":
        source.unlink()
        source.mkdir()
    elif problem in ("duplicate", "ancestor"):
        args += ["--artifact", "file" if problem == "duplicate" else "file/child", source]
    else:
        output.mkdir()
        (output / "old").write_text("preserve")
    assert cli(*args)[0] == 1
    assert output.exists() == (problem == "existing")
    if problem == "existing":
        assert (output / "old").read_text() == "preserve"


@pytest.mark.parametrize("problem", ["extra", "missing", "duplicate-json", "bool-size", "symlink"])
def test_archive_checker_refuses_ambiguous_or_incomplete_manifests(tmp_path, problem):
    source = tmp_path / "source"
    source.write_text("data")
    archive = tmp_path / "archive"
    analysis.collect(archive, [("file", source)])
    manifest = archive / "manifest.json"
    if problem == "extra":
        (archive / "extra").write_text("unmanifested")
    elif problem == "missing":
        (archive / "file").unlink()
    elif problem == "duplicate-json":
        manifest.write_text(manifest.read_text().replace('"schema_version": 1',
                                                        '"schema_version": 1, "schema_version": 1'))
    elif problem == "bool-size":
        data = json.loads(manifest.read_text())
        data["artifacts"]["file"]["bytes"] = True
        manifest.write_text(json.dumps(data))
    else:
        (archive / "file").unlink()
        (archive / "file").symlink_to(source)
    assert cli("check", archive)[0] == 1


@pytest.mark.parametrize("exit_code", [0, 1, 2, -9])
def test_receipt_preserves_job_exit_without_review_verdict(repository, tmp_path, exit_code):
    root, head, tree = repository
    prefix = recorded(tmp_path, head, tree, exit_code)
    output = root / ".evidence/receipt.json"
    code, out, err = cli("receipt", "--worktree", root, "--expected-head", head,
                         "--recorded", "gate", prefix, "JOB_EXIT", "--output", output)
    assert (code, err) == (0, "")
    document = json.loads(out)
    assert document["head"] == head and document["tree"] == tree
    assert document["jobs"]["gate"]["job_exit"] == exit_code
    assert "outcome" not in document and "review_verdict" not in document
    assert json.loads(output.read_text()) == document
    original = output.read_bytes()
    assert cli("receipt", "--worktree", root, "--expected-head", head,
               "--recorded", "gate", prefix, "JOB_EXIT", "--output", output)[0] == 1
    assert output.read_bytes() == original


@pytest.mark.parametrize("problem", ["stale-before", "stale-after", "dirty-before", "dirty-after",
                                     "missing", "duplicate-marker", "trailing-output", "bad-command"])
def test_recorded_refuses_false_identity_or_wrapper_success(repository, tmp_path, problem):
    root, head, tree = repository
    prefix = recorded(tmp_path, head, tree)
    if problem.startswith("stale"):
        suffix = ".identity" if problem.endswith("before") else ".final-identity"
        Path(str(prefix) + suffix).write_text("b" * 40 + "\n" + tree + "\n")
    elif problem.startswith("dirty"):
        suffix = ".status" if problem.endswith("before") else ".final-status"
        Path(str(prefix) + suffix).write_text(" M source.txt\n")
    elif problem == "missing":
        Path(str(prefix) + ".final-identity").unlink()
    else:
        log = Path(str(prefix) + ".log")
        text = log.read_text()
        if problem == "duplicate-marker":
            text += "JOB_EXIT=0\n"
        elif problem == "trailing-output":
            text += "wrapper claims success\n"
        else:
            text = text.replace('["pytest", "tests", "-q"]', "null")
        log.write_text(text)
    output = tmp_path / "receipt.json"
    assert cli("receipt", "--worktree", root, "--expected-head", head,
               "--recorded", "gate", prefix, "JOB_EXIT", "--output", output)[0] == 1
    assert not output.exists()


def test_verdict_error_is_valid_data_and_binary_hash_is_exact(repository, tmp_path):
    root, head, tree = repository
    path = verdict(tmp_path, head, "evidence_unreadable_artifact")
    code, out, err = cli("verdict", path, "--expected-commit", head)
    assert (code, err) == (0, "")
    result = json.loads(out)
    assert result["verdict"]["outcome"] == "ERROR"
    assert result["artifact"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result["artifact"]["bytes"] == len(path.read_bytes())
    prefix = recorded(tmp_path, head, tree, 0)
    facts = analysis.receipt(root, head, [("wrapper", prefix, "JOB_EXIT")], [], [("judge", path)])
    assert facts["jobs"]["wrapper"]["job_exit"] == 0
    assert facts["assay_verdicts"]["judge"]["summary"]["exit_code"] == 2
    code, text, err = cli("verdict", path, "--expected-commit", head, "--format", "text")
    assert (code, err) == (0, "") and "ERROR" in text and "recorded exit 2" in text


def test_release_receipt_refuses_a_verdict_with_an_allow_dirty_override(
    repository, tmp_path
):
    _root, head, _tree = repository
    path = verdict(tmp_path, head)
    document = json.loads(path.read_text())
    document["worktree_integrity"] = {
        "ignored_dirty_paths": ["ledger.md"],
        "overridden_dirty_paths": ["src/uncommitted.py"],
    }
    path.write_text(json.dumps(document))

    with pytest.raises(ValueError, match=r"--allow-dirty overrides"):
        analysis.inspect_verdict(path, head)


def test_report_pass_is_commit_bound_schema_valid_and_read_only(repository, tmp_path):
    root, head, _tree = repository
    path = verdict(tmp_path, head)
    before = subprocess.check_output(["git", "-C", str(root), "status", "--porcelain"])
    worktree_before = _worktree_snapshot(root)

    result, text = _report_pair(head, ["--verdict", "package", str(path)], 0,
                                contains=("pass", "package"))

    assert result["schema_version"] == 1
    assert result["expected_commit"] == result["lanes"][0]["actual_commit"] == head
    assert result["lanes"][0]["status"] == "pass"
    assert result["lanes"][0]["outcome"] == "PASS"
    assert result["lanes"][0]["exit_code"] == 0
    fingerprint = result["lanes"][0]["inputs"]["verdict"]
    assert fingerprint["path"] == str(path.resolve())
    assert fingerprint["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert fingerprint["bytes"] == path.stat().st_size
    schema = json.loads(files("assay").joinpath("schemas/analysis-report.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    validator.validate(result)
    invalid = dict(result, exit_code=9)
    with pytest.raises(ValidationError):
        validator.validate(invalid)
    assert "diagnostics:" in text

    referenced = verdict(tmp_path, head, "r1_pass")
    referenced_result, _ = _report_pair(head, ["--verdict", "package", str(referenced)], 0)
    evidence = referenced_result["lanes"][0]["evidence_artifacts"]
    assert {item["path"] for item in evidence} == {"cov.json"}
    assert not (tmp_path / "cov.json").exists()  # References are labels, never implicit reads.
    assert subprocess.check_output(["git", "-C", str(root), "status", "--porcelain"]) == before
    assert _worktree_snapshot(root) == worktree_before


def test_report_checks_freshness_after_prior_lane_log_snapshot(repository, tmp_path, monkeypatch):
    _root, head, _tree = repository
    alpha_log = tmp_path / "alpha.log"
    alpha_log.write_text("finished log\n")
    alpha_verdict = verdict(tmp_path, head)
    alpha_document = json.loads(alpha_verdict.read_text())
    alpha_document["lane"] = "alpha"
    alpha_verdict.write_text(json.dumps(alpha_document))
    beta_progress = tmp_path / "beta.jsonl"
    observed = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
    _write_progress(beta_progress, head, {"event": "command_running", "commit": head,
                                           "emitted_at": observed.isoformat()}, lane="beta")
    logs_read = []
    report_log = analysis._report_log

    def read_log(path, artifact, lane, limit):
        report_log(path, artifact, lane, limit)
        logs_read.append(lane["name"])

    def report_now():
        assert logs_read == ["alpha"]
        return observed

    monkeypatch.setattr(analysis, "_report_log", read_log)
    monkeypatch.setattr(analysis, "_report_now", report_now)

    result = analysis.report(head, [("alpha", alpha_verdict)], [("beta", beta_progress)],
                             [("alpha", alpha_log)])

    assert result["exit_code"] == 3
    assert [lane["status"] for lane in result["lanes"]] == ["pass", "running"]
    assert result["lanes"][1]["progress"]["freshness"] == "fresh"


def test_report_accepts_dirty_override_verdict_while_receipt_policy_stays_separate(repository, tmp_path):
    _root, head, _tree = repository
    path = verdict(tmp_path, head)
    document = json.loads(path.read_text())
    document["worktree_integrity"] = {
        "ignored_dirty_paths": ["ledger.md"],
        "overridden_dirty_paths": ["src/uncommitted.py"],
    }
    path.write_text(json.dumps(document))

    result, _text = _report_pair(head, ["--verdict", "package", str(path)], 0)

    assert result["lanes"][0]["status"] == "pass"
    assert result["lanes"][0]["overridden_dirty_paths"] == ["src/uncommitted.py"]


def test_report_valid_nonpass_verdict_is_fail(repository, tmp_path):
    _root, head, _tree = repository
    path = verdict(tmp_path, head, "r0_fail_command_failed")

    result, text = _report_pair(head, ["--verdict", "package", str(path)], 1,
                                contains=("fail", "COMMAND_FAILED"))

    assert result["lanes"][0]["status"] == "fail"
    assert result["lanes"][0]["outcome"] == "FAIL"
    assert result["lanes"][0]["exit_code"] == 1
    assert "package" in text


def test_report_current_progress_with_torn_append_is_running(repository, tmp_path):
    _root, head, _tree = repository
    path = tmp_path / "progress.jsonl"
    stamp = datetime.now(UTC).isoformat()
    _write_progress(path, head, {"event": "command_running", "commit": head, "lane": "package",
                                 "phase": "pytest", "candidate_total": 12, "emitted_at": stamp},
                    torn=b'{"event":"candidate","commit":"')

    result, text = _report_pair(head, ["--progress", "package", str(path)], 3,
                                contains=("running", "pytest"))

    progress = result["lanes"][0]["progress"]
    assert result["lanes"][0]["status"] == "running"
    assert progress["freshness"] == "fresh"
    assert progress["latest_event"] == "command_running"
    assert progress["candidate_counts"]["candidate_total"] == 12
    assert progress["torn_final_record"] is True
    assert "running" in text


def test_report_preserves_fresh_progress_before_oversized_torn_append(repository, tmp_path):
    _root, head, _tree = repository
    path = tmp_path / "oversized-torn.jsonl"
    stamp = datetime.now(UTC).isoformat()
    torn = b'{"event":"unfinished","padding":"' + b"x" * (1024 * 1024 + 1)
    _write_progress(path, head, {"event": "command_running", "commit": head,
                                 "emitted_at": stamp}, torn=torn)

    result, _text = _report_pair(head, ["--progress", "package", str(path)], 3)

    progress = result["lanes"][0]["progress"]
    assert result["lanes"][0]["status"] == "running"
    assert progress["freshness"] == "fresh"
    assert progress["torn_final_record"] is True
    assert progress["latest_event"] == "command_running"


def test_report_refuses_oversized_complete_progress_record(repository, tmp_path):
    _root, head, _tree = repository
    path = tmp_path / "oversized-complete.jsonl"
    stamp = datetime.now(UTC).isoformat()
    complete = b'{"event":"command_running","padding":"' + b"x" * (1024 * 1024 + 1) + b'"}\n'
    _write_progress(path, head, {"event": "command_running", "commit": head,
                                 "emitted_at": stamp}, torn=complete)

    result, _text = _report_pair(head, ["--progress", "package", str(path)], 2,
                                 contains=("evidence_error",))

    assert result["lanes"][0]["status"] == "evidence_error"
    assert result["lanes"][0]["progress"]["torn_final_record"] is False


@pytest.mark.parametrize("event", [
    {"event": "end", "padding": "x" * (1024 * 1024 + 1)},
    {"event": "run", "commit": "b" * 40, "lane": "package",
     "padding": "x" * (1024 * 1024 + 1)},
])
def test_report_refuses_oversized_complete_unterminated_progress_record(repository, tmp_path, event):
    _root, head, _tree = repository
    path = tmp_path / "oversized-complete-no-newline.jsonl"
    stamp = datetime.now(UTC).isoformat()
    _write_progress(path, head, {"event": "command_running", "commit": head,
                                 "emitted_at": stamp}, torn=json.dumps(event).encode())

    result, _text = _report_pair(head, ["--progress", "package", str(path)], 2,
                                 contains=("evidence_error",))

    assert result["lanes"][0]["status"] == "evidence_error"
    assert result["lanes"][0]["progress"]["torn_final_record"] is False


def test_report_exit_precedence_for_mixed_lanes(repository, tmp_path):
    _root, head, _tree = repository
    pass_path = verdict(tmp_path, head)
    pass_document = json.loads(pass_path.read_text())
    pass_document["lane"] = "done"
    pass_path.write_text(json.dumps(pass_document))
    fail_path = verdict(tmp_path, head, "r0_fail_command_failed")
    fail_document = json.loads(fail_path.read_text())
    fail_document["lane"] = "failed"
    fail_path.write_text(json.dumps(fail_document))
    running_path = tmp_path / "running.jsonl"
    _write_progress(running_path, head, {"event": "command_running", "commit": head,
                                         "emitted_at": datetime.now(UTC).isoformat()}, lane="live")

    result, _ = _report_pair(head, ["--verdict", "done", str(pass_path),
                                    "--progress", "live", str(running_path)], 3)
    assert result["exit_code"] == 3  # running outranks pass in a mixed result.
    assert [lane["status"] for lane in result["lanes"]] == ["pass", "running"]

    result, _ = _report_pair(head, ["--verdict", "failed", str(fail_path),
                                    "--progress", "live", str(running_path)], 1)
    assert result["exit_code"] == 1  # fail outranks running.
    assert [lane["status"] for lane in result["lanes"]] == ["fail", "running"]

    missing = tmp_path / "absent.json"
    result, _ = _report_pair(head, ["--verdict", "failed", str(fail_path),
                                    "--verdict", "missing", str(missing)], 2)
    assert result["exit_code"] == 2  # evidence_error outranks fail.
    assert [lane["status"] for lane in result["lanes"]] == ["fail", "evidence_error"]


def test_report_stale_and_terminal_progress_without_verdict_are_evidence_errors(repository, tmp_path):
    _root, head, _tree = repository
    stale = tmp_path / "stale.jsonl"
    old = (datetime.now(UTC) - timedelta(seconds=121)).isoformat()
    _write_progress(stale, head, {"event": "command_running", "commit": head,
                                  "phase": "pytest", "emitted_at": old})
    stale_result, _ = _report_pair(head, ["--progress", "package", str(stale)], 2,
                                   contains=("evidence_error", "stale"))
    assert stale_result["lanes"][0]["status"] == "evidence_error"
    assert stale_result["lanes"][0]["progress"]["terminal"] is False

    terminal = tmp_path / "terminal.jsonl"
    _write_progress(terminal, head, {"event": "end", "commit": head, "phase": "finished",
                                     "emitted_at": datetime.now(UTC).isoformat()})
    terminal_result, _ = _report_pair(head, ["--progress", "package", str(terminal)], 2,
                                      contains=("evidence_error", "terminal progress"))
    assert terminal_result["lanes"][0]["progress"]["terminal"] is True

    with_verdict, _ = _report_pair(head, ["--verdict", "package", str(verdict(tmp_path, head)),
                                         "--progress", "package", str(stale)], 0)
    assert with_verdict["lanes"][0]["status"] == "pass"
    assert with_verdict["lanes"][0]["progress"]["freshness"] == "stale"


@pytest.mark.parametrize("problem", ["bad-complete-record", "wrong-lane", "wrong-commit",
                                     "missing-lane", "wrong-run-lane", "wrong-verdict-lane", "directory-log"])
def test_report_refuses_malformed_or_misbound_optional_inputs(repository, tmp_path, problem):
    _root, head, _tree = repository
    progress = tmp_path / "progress.jsonl"
    log = tmp_path / "log"
    log.write_text("ordinary log\n")
    if problem == "bad-complete-record":
        _write_progress(progress, head, {"event": "command_running", "commit": head,
                                         "emitted_at": datetime.now(UTC).isoformat()},
                        torn=b"not json\n")
    elif problem == "wrong-lane":
        _write_progress(progress, head, {"event": "command_running", "lane": "other",
                                         "emitted_at": datetime.now(UTC).isoformat()})
    elif problem == "wrong-commit":
        _write_progress(progress, "b" * 40, {"event": "command_running", "commit": "b" * 40,
                                             "emitted_at": datetime.now(UTC).isoformat()})
    elif problem == "missing-lane":
        progress.write_text(json.dumps({"event": "run", "commit": head}) + "\n"
                            + json.dumps({"event": "command_running", "commit": head,
                                          "emitted_at": datetime.now(UTC).isoformat()}) + "\n")
    elif problem == "wrong-run-lane":
        _write_progress(progress, head, {"event": "command_running", "commit": head,
                                         "emitted_at": datetime.now(UTC).isoformat()}, lane="other")
    elif problem == "wrong-verdict-lane":
        log = verdict(tmp_path, head)
        document = json.loads(log.read_text())
        document["lane"] = "other"
        log.write_text(json.dumps(document))
    else:
        log.unlink()
        log.mkdir()

    if problem == "directory-log":
        option = ["--log", "package", str(log)]
    elif problem == "wrong-verdict-lane":
        option = ["--verdict", "package", str(log)]
    else:
        option = ["--progress", "package", str(progress)]
    result, text = _report_pair(head, option, 2, contains=("evidence_error",))
    assert result["lanes"][0]["status"] == "evidence_error"
    assert result["lanes"][0]["errors"]["count"] >= 1
    assert "diagnostic" in text


@pytest.mark.parametrize("problem", ["missing", "malformed", "commit-mismatch", "invalid-commit"])
def test_report_bad_verdict_evidence_is_evidence_error(repository, tmp_path, problem):
    _root, head, _tree = repository
    if problem == "missing":
        path = tmp_path / "missing.json"
    elif problem == "malformed":
        path = tmp_path / "malformed.json"
        path.write_text('{"outcome":')
    elif problem == "invalid-commit":
        path = verdict(tmp_path, head)
        document = json.loads(path.read_text())
        document["commit"] = "not-a-sha"
        path.write_text(json.dumps(document))
    else:
        path = verdict(tmp_path, "b" * 40)

    result, text = _report_pair(head, ["--verdict", "package", str(path)], 2,
                                contains=("evidence_error",))

    assert result["lanes"][0]["status"] == "evidence_error"
    assert result["lanes"][0]["inputs"]["verdict"]["path"] == str(path.resolve())
    assert result["lanes"][0]["errors"]["count"] >= 1
    assert "diagnostic" in text
    if problem == "commit-mismatch":
        assert result["lanes"][0]["actual_commit"] == "b" * 40
    if problem == "invalid-commit":
        assert result["lanes"][0]["actual_commit"] is None


def test_report_multiple_lanes_sort_and_ignore_child_log_status_words(repository, tmp_path):
    _root, head, _tree = repository
    alpha = verdict(tmp_path, head)
    alpha_document = json.loads(alpha.read_text())
    alpha_document["lane"] = "alpha"
    alpha.write_text(json.dumps(alpha_document))
    zeta = verdict(tmp_path, head, "r0_fail_command_failed")
    zeta_document = json.loads(zeta.read_text())
    zeta_document["lane"] = "zeta"
    zeta.write_text(json.dumps(zeta_document))
    log = tmp_path / "child.log"
    log.write_text("PASS is just a child word\nERROR is also just a child word\n")

    result, text = _report_pair(head, ["--verdict", "zeta", str(zeta), "--verdict", "alpha", str(alpha),
                                       "--log", "alpha", str(log)], 1,
                                contains=("alpha", "zeta"))

    assert [lane["name"] for lane in result["lanes"]] == ["alpha", "zeta"]
    assert [lane["status"] for lane in result["lanes"]] == ["pass", "fail"]
    assert result["exit_code"] == 1
    assert text.index('"alpha"') < text.index('"zeta"')
    assert "PASS is just a child word" not in text
    assert result["lanes"][0]["status"] == "pass"  # The ERROR log line is diagnostic only.


def test_report_bounds_error_records_and_log_window(repository, tmp_path):
    _root, head, _tree = repository
    log = tmp_path / "many-errors.log"
    log.write_text("".join(f"ERROR {number}: " + "x" * 700 + "\n" for number in range(10)))

    result, text = _report_pair(head, ["--log", "package", str(log), "--max-errors", "2"], 2,
                                contains=("evidence_error",))

    errors = result["lanes"][0]["errors"]
    assert result["lanes"][0]["status"] == "evidence_error"
    assert errors["count"] >= 10
    assert len(errors["records"]) == 2
    assert all(len(record["message"]) <= 512 for record in errors["records"])
    assert errors["truncated"] is True
    assert len(text) < 3000
    assert "diagnostics:" in text and "truncated=true" in text

    passing = verdict(tmp_path, head)
    oversized = tmp_path / "oversized.log"
    oversized.write_bytes(b"preamble\n" + b"filler\n" * 20000
                          + b"ASSAY_GATE_PHASE=pytest\nERROR retained tail\n")
    expected_hash = hashlib.sha256(oversized.read_bytes()).hexdigest()
    result, text = _report_pair(head, ["--verdict", "package", str(passing),
                                       "--log", "package", str(oversized)], 0,
                                contains=("pass", "ERROR retained tail"))
    lane = result["lanes"][0]
    assert lane["status"] == "pass"
    assert lane["inputs"]["log"]["bytes"] == oversized.stat().st_size
    assert lane["inputs"]["log"]["sha256"] == expected_hash
    assert lane["errors"]["truncated"] is True
    assert lane["latest_phase"] == "pytest" and lane["phase_source"] == "log"
    assert len(text) < 10000


def test_report_keeps_complete_diagnostic_at_exact_log_window_boundary(repository, tmp_path):
    _root, head, _tree = repository
    passing = verdict(tmp_path, head)
    error_line = b"ERROR within retained window\n"
    tail = error_line + b"x" * (analysis._REPORT_WINDOW - len(error_line) - 1) + b"\n"
    assert len(tail) == analysis._REPORT_WINDOW
    log = tmp_path / "boundary.log"
    log.write_bytes(b"prefix\n" + tail)

    result, text = _report_pair(head, ["--verdict", "package", str(passing),
                                       "--log", "package", str(log)], 0,
                                contains=("ERROR within retained window",))

    lane = result["lanes"][0]
    assert lane["status"] == "pass"
    assert lane["errors"]["count"] == 1
    assert lane["errors"]["records"][0]["message"] == "ERROR within retained window"
    assert lane["errors"]["truncated"] is True
    assert "ERROR within retained window" in text


def test_report_keeps_existing_verdict_progress_and_receipt_commands(repository, tmp_path):
    root, head, _tree = repository
    path = verdict(tmp_path, head)
    progress = tmp_path / "progress.jsonl"
    _write_progress(progress, head)

    verdict_code, verdict_text, verdict_err = cli("verdict", path, "--expected-commit", head)
    progress_code, progress_text, progress_err = cli("progress", progress, "--expected-commit", head)
    receipt_path = tmp_path / "receipt.json"
    receipt_code, receipt_text, receipt_err = cli("receipt", "--worktree", root, "--expected-head", head,
                                                 "--verdict", "package", path,
                                                 "--progress", "package", progress,
                                                 "--output", receipt_path)

    assert (verdict_code, progress_code, receipt_code) == (0, 0, 0)
    assert not verdict_err and not progress_err and not receipt_err
    assert json.loads(verdict_text)["summary"]["outcome"] == "PASS"
    assert json.loads(progress_text)["runs"][0]["run"]["commit"] == head
    assert json.loads(receipt_text)["assay_verdicts"]["package"]["summary"]["commit"] == head


@pytest.mark.parametrize("name", ["r1_pass", "r1_fail_uncovered_branches", "r2_pass_with_judgment"])
def test_human_summary_retains_claim_and_measurements(tmp_path, name):
    head = "a" * 40
    path = verdict(tmp_path, head, name)
    code, out, err = cli("verdict", path, "--expected-commit", head, "--format", "text")
    assert (code, err) == (0, "")
    assert ("lines " if name.startswith("r1") else "mutants ") in out


@pytest.mark.parametrize("captured", [False, True])
def test_human_failure_diagnosis_uses_existing_capture_without_inventing_counts(tmp_path, captured):
    head = "a" * 40
    path = verdict(tmp_path, head, "r0_fail_command_failed")
    document = json.loads(path.read_text())
    for stream in ("stdout", "stderr"):
        document.pop(f"result_{stream}_tail", None)
        document.pop(f"result_{stream}_dropped_bytes", None)
    if captured:
        document.update(result_stdout_tail="FAILED tests/test_contract.py: real assertion",
                        result_stderr_tail="diagnostic stderr", result_stdout_dropped_bytes=17,
                        result_stderr_dropped_bytes=0)
    path.write_text(json.dumps(document))
    code, out, err = cli("verdict", path, "--expected-commit", head, "--format", "text")
    assert (code, err) == (0, "") and "FAIL" in out
    assert ("real assertion" in out) == captured
    assert ("diagnostic stderr" in out) == captured
    assert ("stdout dropped bytes: 17" in out) == captured
    assert ("stderr dropped bytes: 0" in out) == captured
    assert ("captured stdout tail:" in out) == captured


def test_record_and_receipt_preserve_a_legitimate_empty_argument(repository):
    root, head, _ = repository
    output = root / ".evidence" / "empty-argument"
    command = [sys.executable, "-c", "import sys; assert sys.argv[1] == ''", ""]
    code, out, err = cli("record", "--worktree", root, "--expected-head", head,
                         "--output", output, "--", *command)
    assert (code, err) == (0, "")
    result = analysis.receipt(root, head, [("job", output / "job", analysis.JOB_MARKER)], [], [])
    assert result["jobs"]["job"]["command"] == command
    assert json.loads(out)["command"] == command
    schema = json.loads(files("assay").joinpath("schemas/analysis-receipt.schema.json").read_text())
    Draft202012Validator(schema).validate(result)


@pytest.mark.parametrize("kind", ["verdict", "progress", "manifest", "archive-artifact"])
def test_nonregular_inputs_refuse_without_waiting_for_a_fifo_writer(tmp_path, kind):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    if kind in ("verdict", "progress"):
        arguments = [kind, str(fifo), "--expected-commit", "a" * 40]
    else:
        archive = tmp_path / "archive"
        archive.mkdir()
        if kind == "manifest":
            fifo.rename(archive / "manifest.json")
        else:
            fifo.rename(archive / "artifact")
            (archive / "manifest.json").write_text(json.dumps({"schema_version": 1, "artifacts": {
                "artifact": {"source": "historical", "sha256": "0" * 64, "bytes": 0}}}))
        arguments = ["check", str(archive)]
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; from assay.cli import main; raise SystemExit(main(sys.argv[1:]))",
         "analyze", *arguments],
        env={**os.environ, "PYTHONPATH": os.pathsep.join(
            [str(PROJECT_ROOT / "src"), str(PROJECT_ROOT / "analysis" / "src")])},
        capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 1 and result.stdout == ""
    assert "not a regular file" in result.stderr and "Traceback" not in result.stderr


@pytest.mark.parametrize("command", ["verdict", "progress", "check", "collect", "receipt"])
def test_deep_untrusted_json_refuses_without_publishing_success(repository, tmp_path, command):
    root, head, tree = repository
    path = tmp_path / "deep.json"
    path.write_text("[" * 10000 + "0" + "]" * 10000)
    output = tmp_path / "unpublished"
    if command in ("verdict", "progress"):
        args = (command, path, "--expected-commit", head)
    elif command == "check":
        directory = tmp_path / "archive"
        directory.mkdir()
        path.rename(directory / "manifest.json")
        args = (command, directory)
    elif command == "collect":
        args = (command, "--receipt", path, "--output", output)
    else:
        prefix = recorded(tmp_path, head, tree)
        Path(str(prefix) + ".log").write_text("COMMAND=" + path.read_text() + "\nJOB_EXIT=0\n")
        args = (command, "--worktree", root, "--expected-head", head,
                "--recorded", "gate", prefix, "JOB_EXIT", "--output", output)
    code, out, err = cli(*args)
    assert code == 1 and out == "" and err.startswith("assay analyze:")
    assert "Traceback" not in err and not output.exists()


def test_json_parser_recursion_failure_is_translated_at_the_parse_site(monkeypatch):
    def decoder_exhausted(*args, **kwargs):
        raise RecursionError("decoder exhausted its stack")
    monkeypatch.setattr(analysis.json, "loads", decoder_exhausted)
    with pytest.raises(ValueError, match="JSON nesting exceeds decoder limit") as refusal:
        analysis._json("[]")
    assert isinstance(refusal.value.__cause__, RecursionError)


@pytest.mark.parametrize("directory", ["directory\tname", "directory\nname", 'directory"name', "directory\\name",
                                      "directory:1:name", "directory:2:.gitignore:5:name"])
def test_repository_ignore_origin_uses_real_path_instead_of_git_display_spelling(repository, tmp_path, directory):
    root, _, _ = repository
    nested = root / directory
    nested.mkdir()
    (nested / ".gitignore").write_text(".evidence:9:name/\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "nested ignore policy"], check=True)
    head, tree = analysis._git(root, "rev-parse", "HEAD", "HEAD^{tree}").splitlines()
    relative = directory + "/.evidence:9:name/receipt.json"
    assert analysis.git.ignore_rule_source(root, relative) == directory + "/.gitignore"
    prefix = recorded(tmp_path, head, tree)
    code, out, err = cli("receipt", "--worktree", root, "--expected-head", head,
                         "--recorded", "job", prefix, "JOB_EXIT", "--output", root / relative)
    assert (code, err) == (0, "") and json.loads(out)["head"] == head


def test_receipt_collection_uses_one_file_graph_and_detects_stale_bytes(repository, tmp_path):
    root, head, tree = repository
    prefix = recorded(tmp_path, head, tree, 2)
    path = verdict(tmp_path, head)
    result = analysis.receipt(root, head, [("gate", prefix, "JOB_EXIT")], [], [("judge", path)])
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps(result))
    bundle = tmp_path / "bundle"
    code, _, err = cli("collect", "--receipt", receipt, "--output", bundle)
    assert (code, err) == (0, "")
    assert analysis.check_archive(bundle)["artifact_count"] == 7
    assert (bundle / "assay_verdicts/judge").read_bytes() == path.read_bytes()
    Path(str(prefix) + ".log").write_text("changed")
    output = tmp_path / "stale-bundle"
    assert cli("collect", "--receipt", receipt, "--output", output)[0] == 1
    assert not output.exists()


@pytest.mark.parametrize("problem", ["null", "duplicate", "schema", "rollup", "commit"])
def test_verdict_inspection_reuses_real_verifier_and_refuses_false_certification(tmp_path, problem):
    head = "a" * 40
    path = verdict(tmp_path, head)
    text = path.read_text()
    if problem == "null":
        text = "null"
    elif problem == "duplicate":
        text = text.replace('"exit_code": 0', '"exit_code": 0, "exit_code": 0')
    elif problem == "schema":
        current_schema = f'"schema_version": {VERDICT_SCHEMA_VERSION}'
        assert current_schema in text
        text = text.replace(
            current_schema,
            f'"schema_version": {VERDICT_SCHEMA_VERSION - 1}',
            1,
        )
    elif problem == "rollup":
        text = text.replace('"outcome": "PASS"', '"outcome": "FAIL"')
    else:
        text = text.replace(head, "b" * 40)
    path.write_text(text)
    code, out, err = cli("verdict", path, "--expected-commit", head)
    assert code == 1 and out == "" and err.startswith("assay analyze:")


@pytest.mark.parametrize("exit_code", [0, 2])
def test_p4_launcher_adapter_preserves_matching_job_and_wait(repository, tmp_path, exit_code):
    root, head, _ = repository
    directory = _launcher(tmp_path, head, exit_code)
    result = analysis.receipt(root, head, [], [("p4", directory)], [])
    assert result["jobs"]["p4"]["job_exit"] == exit_code
    assert result["jobs"]["p4"]["inspect_phase"] == "launcher-pre-wait"
    code, out, err = cli("launcher", directory, "--expected-commit", head)
    assert (code, err) == (0, "") and json.loads(out)["job_exit"] == exit_code


@pytest.mark.parametrize("problem", ["wait", "identity", "inspect", "duplicate", "missing"])
def test_p4_adapter_refuses_wrong_container_and_wrapper_status(repository, tmp_path, problem):
    root, head, _ = repository
    directory = _launcher(tmp_path, head)
    if problem == "wait":
        (directory / "docker-wait.exit").write_text("2\n")
    elif problem == "identity":
        path = directory / "launch.txt"
        path.write_text(path.read_text().replace(head, "b" * 40))
    elif problem == "inspect":
        path = directory / "container.inspect.json"
        path.write_text(path.read_text().replace("012345", "wrong-container"))
    elif problem == "duplicate":
        path = directory / "launch.txt"
        path.write_text(path.read_text() + "user=1003\n")
    else:
        (directory / "launch-memory-pressure.txt").unlink()
    assert cli("receipt", "--worktree", root, "--expected-head", head,
               "--tester-run", "p4", directory, "--output", tmp_path / "result")[0] == 1


def test_progress_segments_same_commit_retries_without_inventing_freshness(tmp_path):
    path = tmp_path / "progress.jsonl"
    events = [{"event": "run", "commit": "old"}, {"event": "resume", "resumed_total": 99},
              {"event": "run", "commit": "current"},
              {"event": "resume", "resumed_total": 0, "rejected_total": 2},
              {"event": "candidates", "pending_total": 2}, {"event": "end", "outcome": "PASS"},
              {"event": "run", "commit": "current"}, {"event": "resume", "resumed_total": 2}]
    path.write_text("".join(json.dumps(row) + "\n" for row in events))
    result = analysis.inspect_progress(path, "current")
    assert len(result["runs"]) == 2
    assert result["runs"][0]["milestones"][0]["event"]["resumed_total"] == 0
    assert result["runs"][1]["milestones"][0]["event"]["resumed_total"] == 2
    assert "end" not in result["runs"][1]["event_counts"]  # Partial retry stays partial.
    assert "outcome" not in result


@pytest.mark.parametrize("text", ['null\n', '{"event":"resume"}\n',
                                  '{"event":"run"}\n', '{"event":"run","commit":"old"}\n',
                                  '{"event":"run","commit":"a"}\n{"event":"test","commit":"b"}\n',
                                  '{"event":"run","commit":"a"}\n{"event":'])
def test_progress_refuses_malformed_unbound_or_absent_runs(tmp_path, text):
    path = tmp_path / "progress.jsonl"
    path.write_text(text)
    assert cli("progress", path, "--expected-commit", "a")[0] == 1


def test_record_produces_consumable_evidence_and_preserves_exit(repository):
    root, head, _ = repository
    output = root / ".evidence/run"
    code, out, err = cli("record", "--worktree", root, "--expected-head", head,
                         "--output", output, "--", sys.executable, "-c",
                         'import sys; print("job bytes"); sys.exit(7)')
    assert code == 7 and err == "" and json.loads(out)["job_exit"] == 7
    result = analysis.receipt(root, head, [("gate", output / "job", analysis.JOB_MARKER)], [], [])
    assert result["jobs"]["gate"]["job_exit"] == 7
    assert "job bytes" in (output / "job.log").read_text()
    assert not analysis._git(root, "status", "--porcelain")


def test_record_dirty_final_state_is_captured_and_cannot_be_certified(repository):
    root, head, _ = repository
    output = root / ".evidence/run"
    assert cli("record", "--worktree", root, "--expected-head", head, "--output", output,
               "--", sys.executable, "-c", 'open("source.txt","w").write("dirty")')[0] == 0
    assert "source.txt" in (output / "job.final-status").read_text()
    assert cli("receipt", "--worktree", root, "--expected-head", head,
               "--recorded", "gate", output / "job", analysis.JOB_MARKER,
               "--output", output / "receipt.json")[0] == 1
    assert not (output / "receipt.json").exists()


def test_record_preserves_binary_output_and_signal_status(repository):
    root, head, _ = repository
    output = root / ".evidence/run"
    code, out, err = cli("record", "--worktree", root, "--expected-head", head, "--output", output,
                         "--", sys.executable, "-c",
                         'import os, signal, sys; sys.stdout.buffer.write(b"\\xff"); '
                         'sys.stdout.flush(); os.kill(os.getpid(), signal.SIGTERM)')
    assert code == 143 and err == "" and json.loads(out)["job_exit"] == -15
    result = analysis.receipt(root, head, [("gate", output / "job", analysis.JOB_MARKER)], [], [])
    assert result["jobs"]["gate"]["job_exit"] == -15
    assert b"\xff" in (output / "job.log").read_bytes()


def test_record_cannot_start_preserves_diagnostic_partial_files(repository):
    root, head, _ = repository
    output = root / ".evidence/run"
    assert cli("record", "--worktree", root, "--expected-head", head, "--output", output,
               "--", str(root / "not-a-command"))[0] == 1
    assert (output / "job.log").exists()
    assert not (output / "record.json").exists()
    assert cli("receipt", "--worktree", root, "--expected-head", head,
               "--recorded", "partial", output / "job", analysis.JOB_MARKER,
               "--output", output / "receipt.json")[0] == 1


@pytest.mark.parametrize("problem", ["head", "dirty", "not-ignored", "empty-selection"])
def test_receipt_fails_before_publishing_invalid_current_tree(repository, tmp_path, problem):
    root, head, tree = repository
    prefix = recorded(tmp_path, head, tree)
    output = tmp_path / "receipt.json"
    args = ["receipt", "--worktree", root, "--expected-head", head,
            "--recorded", "gate", prefix, "JOB_EXIT"]
    if problem == "head":
        args[4] = "b" * 40
    elif problem == "dirty":
        (root / "source.txt").write_text("dirty")
    elif problem == "not-ignored":
        output = root / "receipt.json"
    else:
        args = args[:5]
    assert cli(*args, "--output", output)[0] == 1
    assert not output.exists()


def test_git_environment_cannot_redirect_receipt(repository, tmp_path, monkeypatch):
    root, head, tree = repository
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "nonexistent"))
    prefix = recorded(tmp_path, head, tree)
    assert cli("receipt", "--worktree", root, "--expected-head", head,
               "--recorded", "gate", prefix, "JOB_EXIT", "--output", root / ".evidence/receipt.json")[0] == 0


def test_local_core_worktree_cannot_redirect_receipt(repository, tmp_path):
    root, head, tree = repository
    other = tmp_path / "wrong-root"
    other.mkdir()
    subprocess.run(["git", "-C", str(root), "config", "core.worktree", str(other)], check=True)
    prefix = recorded(tmp_path, head, tree)
    result = analysis.receipt(root, head, [("gate", prefix, "JOB_EXIT")], [], [])
    assert result["worktree"] == str(root)


def test_personal_excludes_cannot_hide_dirty_sources_or_justify_output(repository, tmp_path):
    root, head, tree = repository
    (root / ".git/info/exclude").write_text("hidden.txt\npersonal-output/\n")
    (root / "hidden.txt").write_text("hidden source")
    prefix = recorded(tmp_path, head, tree)
    assert cli("receipt", "--worktree", root, "--expected-head", head,
               "--recorded", "gate", prefix, "JOB_EXIT", "--output", tmp_path / "receipt.json")[0] == 1
    (root / "hidden.txt").unlink()
    output = root / "personal-output"
    code, _, err = cli("record", "--worktree", root, "--expected-head", head,
                        "--output", output, "--", sys.executable, "-c", "pass")
    assert code == 1 and "personal excludes" in err and not output.exists()


def test_new_cross_document_analysis_anchors_resolve():
    root = PROJECT_ROOT
    docs = [root / "README.md", root / "docs/DESIGN-GUIDE.md", root / "docs/CONSUMERS.md"]
    count = 0
    for path in docs:
        for target in re.findall(r"\]\(([^)]+#review-evidence-analysis)\)", path.read_text()):
            filename, anchor = target.split("#")
            text = (path.parent / filename).read_text()
            assert "## Review evidence analysis" in text and anchor == "review-evidence-analysis"
            count += 1
    assert count >= 3


def test_report_design_anchor_is_linked_from_all_adopter_documents():
    root = PROJECT_ROOT
    design = root / "docs/DESIGN-GUIDE.md"
    assert "### Bounded live gate snapshot (B100)" in design.read_text()
    expected = "bounded-live-gate-snapshot-b100"
    for path in (root / "README.md", root / "docs/CONSUMERS.md", root / "docs/INTERNAL-CONSUMERS.md"):
        targets = re.findall(r"\]\((?:docs/)?DESIGN-GUIDE\.md#([^)]*)\)", path.read_text())
        assert expected in targets, f"{path.name} must link to the B100 design section"


def test_effective_ignore_source_handles_negation_and_colons(repository):
    root, _, _ = repository
    (root / ".gitignore").write_text("output/*\n!output/keep.json\noutput:colon/*\n")
    assert analysis.git.ignore_rule_source(root, "output/keep.json") is None
    assert analysis.git.ignore_rule_source(root, "output/ignored.json") == ".gitignore"
    assert analysis.git.ignore_rule_source(root, "output:colon/log") == ".gitignore"


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_progress_refuses_non_json_numeric_literals(tmp_path, value):
    path = tmp_path / "progress.jsonl"
    path.write_text('{"event":"run","commit":"a","elapsed_s":' + value + '}\n')
    code, out, err = cli("progress", path, "--expected-commit", "a")
    assert code == 1 and out == "" and "non-finite JSON literal" in err


def test_packaged_analysis_schemas_validate_real_outputs_and_reject_false_shapes(repository, tmp_path):
    root, head, tree = repository
    prefix = recorded(tmp_path, head, tree, -9)
    launcher = _launcher(tmp_path, head, 2)
    path = verdict(tmp_path, head, "evidence_unreadable_artifact")
    progress = tmp_path / "progress.jsonl"
    progress.write_text(json.dumps({"event": "run", "commit": head}) + "\n")
    result = analysis.receipt(root, head, [("gate", prefix, "JOB_EXIT")], [("tester", launcher)],
                              [("judge", path)], [("r2", progress)])
    receipt_schema = json.loads(files("assay").joinpath("schemas/analysis-receipt.schema.json").read_text())
    archive_schema = json.loads(files("assay").joinpath("schemas/analysis-archive.schema.json").read_text())
    for schema in (receipt_schema, archive_schema):
        Draft202012Validator.check_schema(schema)
    receipt_validator = Draft202012Validator(receipt_schema)
    receipt_validator.validate(result)
    result["review_verdict"] = "ACCEPT"
    with pytest.raises(ValidationError):
        receipt_validator.validate(result)
    del result["review_verdict"]
    result["head"] = "invented"
    with pytest.raises(ValidationError):
        receipt_validator.validate(result)
    manifest = analysis.collect(tmp_path / "bundle", [("verdict.json", path)])
    archive_validator = Draft202012Validator(archive_schema)
    archive_validator.validate(manifest)
    manifest["artifacts"]["verdict.json"]["bytes"] = True
    with pytest.raises(ValidationError):
        archive_validator.validate(manifest)


def test_documented_analysis_commands_parse_with_shipped_cli():
    root = PROJECT_ROOT
    paths = [root / "README.md", root / "docs/DESIGN-GUIDE.md", root / "docs/CONSUMERS.md",
             root / "docs/INTERNAL-CONSUMERS.md"]
    parser = analysis_cli.build_parser()
    seen = set()
    for path in paths:
        text = path.read_text()
        blocks = re.findall(r"<!-- assay-analysis-example -->\n```bash\n(.*?)\n```", text, re.DOTALL)
        assert blocks, f"{path.name} needs an executable analysis example"
        file_commands = set()
        for block in blocks:
            for line in block.replace("\\\n", " ").splitlines():
                if not line.startswith("assay analyze "):
                    continue
                line = line.replace("$REVIEW_HEAD", "a" * 40)
                args = parser.parse_args(shlex.split(line)[2:])
                seen.add(args.analysis_command)
                file_commands.add(args.analysis_command)
        assert "report" in file_commands, f"{path.name} needs a checked report example"
    subcommands = parser._subparsers._group_actions[0].choices
    assert set(subcommands) == seen
    with pytest.raises(SystemExit):
        parser.parse_args(["verdict", "file.json"])  # No invented commit.


def _valid_receipt_for_collection(repository, tmp_path):
    root, head, tree = repository
    prefix = recorded(tmp_path, head, tree)
    return analysis.receipt(
        root, head, [("gate", prefix, "JOB_EXIT")], [], []
    )


def test_digest_refuses_a_directory_as_an_artifact(tmp_path):
    directory = tmp_path / "directory"
    directory.mkdir()

    with pytest.raises(ValueError, match="not a regular file"):
        analysis._digest(directory)


@pytest.mark.parametrize("problem", ["fields", "identity", "worktree", "fingerprint"])
def test_collect_refuses_malformed_receipt_facts_before_creating_archive(
    repository, tmp_path, problem
):
    document = _valid_receipt_for_collection(repository, tmp_path)
    if problem == "fields":
        del document["progress"]
    elif problem == "identity":
        document["head"] = "g" * 40
    elif problem == "worktree":
        document["current_worktree_status"] = "dirty"
    else:
        document["jobs"]["gate"]["files"][".log"]["bytes"] = -1
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(document), encoding="utf-8")
    output = tmp_path / "archive"

    with pytest.raises(ValueError):
        analysis.collect(output, [], receipt_path)

    assert not output.exists()


def test_collect_requires_at_least_one_explicit_artifact(tmp_path):
    with pytest.raises(ValueError, match="at least one artifact"):
        analysis.collect(tmp_path / "archive", [])


@pytest.mark.parametrize(
    ("manifest", "artifact"),
    [
        ({"schema_version": 2, "artifacts": {"file": {}}}, None),
        ({"schema_version": 1, "artifacts": []}, None),
        ({"schema_version": 1, "artifacts": {"file": {"sha256": "0" * 64}}}, b"content"),
    ],
)
def test_check_archive_refuses_unsupported_empty_or_malformed_manifests(
    tmp_path, manifest, artifact
):
    directory = tmp_path / "archive"
    directory.mkdir()
    if artifact is not None:
        (directory / "file").write_bytes(artifact)
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError):
        analysis.check_archive(directory)


def test_check_archive_refuses_a_symlink_even_when_its_bytes_match(tmp_path):
    directory = tmp_path / "archive"
    directory.mkdir()
    target = directory / "target"
    target.write_bytes(b"same bytes")
    link = directory / "artifact"
    link.symlink_to(target.name)
    manifest = {
        "schema_version": 1,
        "artifacts": {
            "artifact": {
                "source": "historical source",
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "bytes": target.stat().st_size,
            }
        },
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="symlinks"):
        analysis.check_archive(directory)


def test_analysis_marker_rejects_delimiter_in_its_name():
    with pytest.raises(ValueError, match="job marker"):
        analysis._marker("EXIT=0\n", "BAD=MARKER")


def test_recorded_job_refuses_a_log_without_the_command_header(repository, tmp_path):
    _root, head, tree = repository
    prefix = recorded(tmp_path, head, tree)
    Path(str(prefix) + ".log").write_text("ordinary output\nJOB_EXIT=0\n", encoding="utf-8")

    with pytest.raises(ValueError, match="COMMAND"):
        analysis._recorded(prefix, "JOB_EXIT", head, tree)


@pytest.mark.parametrize("problem", ["inspect-shape", "network", "cpu", "wait"])
def test_tester_run_refuses_malformed_docker_acceptance_facts(
    repository, tmp_path, problem
):
    _root, head, _tree = repository
    directory = _launcher(tmp_path, head)
    inspect_path = directory / "container.inspect.json"
    document = json.loads(inspect_path.read_text(encoding="utf-8"))
    if problem == "inspect-shape":
        inspect_path.write_text("{}", encoding="utf-8")
    elif problem == "network":
        launch_path = directory / "launch.txt"
        launch_path.write_text(launch_path.read_text() + "network_mode=bridge\n", encoding="utf-8")
        document[0]["HostConfig"]["NetworkMode"] = "host"
        inspect_path.write_text(json.dumps(document), encoding="utf-8")
    elif problem == "cpu":
        document[0]["HostConfig"]["NanoCpus"] = True
        inspect_path.write_text(json.dumps(document), encoding="utf-8")
    else:
        (directory / "docker-wait.exit").write_text("not-a-status\n", encoding="utf-8")

    with pytest.raises(ValueError):
        analysis._tester_run(directory, head)


def test_report_argument_parsers_refuse_unbound_values():
    with pytest.raises(argparse.ArgumentTypeError, match="expected commit"):
        analysis._report_commit("not-a-full-commit")
    with pytest.raises(argparse.ArgumentTypeError, match="max-errors"):
        analysis._report_max_errors("11")
    with pytest.raises(argparse.ArgumentTypeError, match="max-errors"):
        analysis._report_max_errors("not-an-integer")


def test_report_blocks_refuses_a_fifo_without_opening_it_for_blocking(tmp_path):
    fifo = tmp_path / "progress"
    os.mkfifo(fifo)

    with pytest.raises(ValueError, match="not a regular file"):
        list(analysis._report_blocks(fifo, {}))


def test_report_blocks_accepts_an_empty_regular_file(tmp_path):
    path = tmp_path / "empty"
    path.write_bytes(b"")
    artifact = {}

    assert list(analysis._report_blocks(path, artifact)) == []
    assert artifact["bytes"] == 0
    assert artifact["sha256"] == hashlib.sha256(b"").hexdigest()


def test_report_blocks_detects_a_file_truncated_after_its_size_was_read(
    tmp_path, monkeypatch
):
    path = tmp_path / "changing"
    path.write_bytes(b"initial bytes")
    real_fstat = os.fstat

    def truncate_after_stat(fd):
        info = real_fstat(fd)
        path.write_bytes(b"")
        return info

    monkeypatch.setattr(analysis.os, "fstat", truncate_after_stat)

    with pytest.raises(ValueError, match="truncated during report snapshot"):
        list(analysis._report_blocks(path, {}))


@pytest.mark.parametrize("document", ["[]", "{}"])
def test_report_verdict_refuses_nonobjects_and_schema_invalid_objects(
    tmp_path, document
):
    path = tmp_path / "verdict.json"
    path.write_text(document, encoding="utf-8")
    lane = {"name": "package", "expected_commit": "a" * 40, "evidence_artifacts": []}

    with pytest.raises((TypeError, ValueError)):
        analysis._report_verdict(path, {}, lane)


def _report_progress_direct(path, head, events):
    path.write_bytes(
        b"".join(json.dumps(event).encode("utf-8") + b"\n" for event in events)
    )
    lane = {"name": "package", "expected_commit": head, "actual_commit": None}
    analysis._report_progress(path, {}, lane)
    return lane


@pytest.mark.parametrize(
    ("events", "message"),
    [
        ([{"event": "run", "commit": "short", "lane": "package"}], "full commit"),
        ([{"event": "run", "commit": "a" * 40, "lane": "wrong"}], "differs"),
        ([{"event": "run", "commit": "a" * 40, "lane": "package"},
          {"event": "unknown"}], "malformed progress event"),
        ([{"event": "command_running"}], "precedes first run identity"),
        ([{"event": "run", "commit": "a" * 40, "lane": "package"},
          {"event": "command_running", "commit": "b" * 40}], "conflicting commit"),
        ([{"event": "run", "commit": "a" * 40, "lane": "package"},
          {"event": "command_running", "phase": 4}], "malformed progress phase"),
        ([{"event": "run", "commit": "a" * 40, "lane": "package"},
          {"event": "command_running", "candidate_total": -1}], "malformed progress count"),
        ([{"event": "run", "commit": "a" * 40, "lane": "package"},
          {"event": "command_running", "lane": "wrong"}], "differs"),
    ],
)
def test_report_progress_refuses_unbound_or_malformed_events(
    tmp_path, events, message
):
    path = tmp_path / "progress.jsonl"

    with pytest.raises(ValueError, match=message):
        _report_progress_direct(path, "a" * 40, events)


def test_report_progress_refuses_a_file_with_no_complete_run_header(tmp_path):
    path = tmp_path / "empty-progress.jsonl"
    path.write_bytes(b"")
    lane = {"name": "package", "expected_commit": "a" * 40, "actual_commit": None}

    with pytest.raises(ValueError, match="no complete run header"):
        analysis._report_progress(path, {}, lane)


def test_report_progress_refuses_a_semantically_bad_unterminated_event(tmp_path):
    head = "a" * 40
    path = tmp_path / "bad-torn-event.jsonl"
    path.write_bytes(
        json.dumps({"event": "run", "commit": head, "lane": "package"}).encode()
        + b"\n"
        + json.dumps({"event": "command_running", "commit": "b" * 40}).encode()
    )
    lane = {"name": "package", "expected_commit": head, "actual_commit": None}

    with pytest.raises(ValueError, match="conflicting commit"):
        analysis._report_progress(path, {}, lane)


def test_report_progress_truncates_long_details_but_retains_the_run(tmp_path):
    head = "a" * 40
    now = datetime.now(UTC).replace(tzinfo=None).isoformat()
    lane = _report_progress_direct(
        tmp_path / "progress.jsonl",
        head,
        [
            {"event": "run", "commit": head, "lane": "package"},
            {
                "event": "command_running",
                "phase": "p" * (analysis._REPORT_LINE_LIMIT + 10),
                "emitted_at": "t" * (analysis._REPORT_LINE_LIMIT + 10),
            },
        ],
    )

    assert lane["progress"]["details_truncated"] is True
    assert lane["progress"]["latest_phase"].endswith("...")
    assert len(lane["progress"]["latest_phase"]) == analysis._REPORT_LINE_LIMIT
    assert lane["progress"]["freshness"] == "unknown"

    naive_lane = _report_progress_direct(
        tmp_path / "naive-progress.jsonl",
        head,
        [
            {"event": "run", "commit": head, "lane": "package"},
            {"event": "command_running", "emitted_at": now},
        ],
    )
    assert naive_lane["progress"]["freshness"] == "unknown"


def test_report_progress_keeps_hashing_after_a_complete_bad_record(tmp_path):
    path = tmp_path / "bad-then-large.jsonl"
    path.write_bytes(b"null\n" + b"x" * (analysis._REPORT_WINDOW * 2))
    lane = {"name": "package", "expected_commit": "a" * 40, "actual_commit": None}

    with pytest.raises(ValueError, match="malformed progress event"):
        analysis._report_progress(path, {}, lane)


def test_report_progress_classifies_oversized_records_after_the_limit_block(
    tmp_path,
):
    head = "a" * 40
    run = json.dumps({"event": "run", "commit": head, "lane": "package"}).encode() + b"\n"
    padding = b"x" * (
        analysis._REPORT_RECORD_LIMIT + analysis._REPORT_WINDOW + 100
    )
    complete = (
        b'{"event":"command_running","padding":"' + padding + b'"}\n'
    )
    complete_path = tmp_path / "oversized-complete.jsonl"
    complete_path.write_bytes(run + complete)
    complete_lane = {
        "name": "package",
        "expected_commit": head,
        "actual_commit": None,
    }

    with pytest.raises(ValueError, match="progress record exceeds 1 MiB"):
        analysis._report_progress(complete_path, {}, complete_lane)

    torn = b'{"event":"unfinished","padding":"' + padding
    torn_path = tmp_path / "oversized-torn.jsonl"
    torn_path.write_bytes(run + torn)
    torn_lane = {
        "name": "package",
        "expected_commit": head,
        "actual_commit": None,
    }

    analysis._report_progress(torn_path, {}, torn_lane)

    assert torn_lane["progress"]["torn_final_record"] is True


def test_report_log_trims_crlf_and_recovers_a_complete_error_line(tmp_path):
    window = analysis._REPORT_WINDOW

    cases = [
        # The retained window begins just after CR in a CRLF pair.
        b"old\r" + b"\nerror: boundary\r\n" + b"x" * (
            window - len(b"\nerror: boundary\r\n")
        ),
        # A partial prefix is discarded through an LF separator.
        b"old" + b"x\nerror: lf separator\n" + b"x" * (
            window - len(b"x\nerror: lf separator\n")
        ),
        # A partial prefix is discarded through CRLF as one separator.
        b"old" + b"x\r\nerror: crlf separator\n" + b"x" * (
            window - len(b"x\r\nerror: crlf separator\n")
        ),
        # A window with no line boundary is discarded in full.
        b"old" + b"x" * window,
    ]
    for index, data in enumerate(cases):
        path = tmp_path / f"log-{index}"
        path.write_bytes(data)
        lane = {
            "errors": {"records": [], "count": 0, "truncated": False},
            "latest_phase": None,
            "phase_source": None,
        }

        analysis._report_log(path, {}, lane, 5)

        if index == 3:
            assert lane["errors"]["records"] == []
        else:
            assert len(lane["errors"]["records"]) == 1
            assert "error:" in lane["errors"]["records"][0]["message"]
        assert lane["errors"]["truncated"] is True


def test_report_refuses_invalid_policy_and_empty_input_sets():
    head = "a" * 40
    with pytest.raises(ValueError, match="max-errors"):
        analysis.report(head, [], [], [], max_errors=True)
    with pytest.raises(ValueError, match="at least one explicit input"):
        analysis.report(head, [], [], [])


def test_record_refuses_an_empty_command_before_creating_evidence(repository, tmp_path):
    root, head, _tree = repository
    output = tmp_path / "evidence"

    with pytest.raises(ValueError, match="requires a command"):
        analysis.record(root, head, output, [])

    assert not output.exists()


def test_receipt_refuses_a_name_shared_by_recorded_and_tester_jobs(
    repository, tmp_path
):
    root, head, tree = repository
    prefix = recorded(tmp_path, head, tree)

    with pytest.raises(ValueError, match="overlap"):
        analysis.receipt(
            root,
            head,
            [("same", prefix, "JOB_EXIT")],
            [("same", tmp_path / "unused")],
            [],
        )


def test_receipt_refuses_git_identity_that_changes_during_collection(
    repository, tmp_path, monkeypatch
):
    root, head, tree = repository
    prefix = recorded(tmp_path, head, tree)
    values = iter(((head, tree), (head, "b" * 40)))
    monkeypatch.setattr(analysis, "_identity", lambda *_args: next(values))

    with pytest.raises(ValueError, match="changed while inspecting"):
        analysis.receipt(root, head, [("gate", prefix, "JOB_EXIT")], [], [])


def test_record_cli_requires_the_command_separator(repository, tmp_path):
    root, head, _tree = repository
    code, out, err = cli(
        "record",
        "--worktree",
        root,
        "--expected-head",
        head,
        "--output",
        tmp_path / "evidence",
        "echo",
    )

    assert code == 1 and out == ""
    assert "requires a command after --" in err


def test_new_artifact_writer_removes_partial_file_when_serialization_fails(
    tmp_path, monkeypatch
):
    output = tmp_path / "artifact.json"

    def fail_dump(*_args, **_kwargs):
        raise RuntimeError("serialization failed")

    monkeypatch.setattr(analysis.json, "dump", fail_dump)

    with pytest.raises(RuntimeError, match="serialization failed"):
        analysis._write_new(output, {"evidence": "fact"})

    assert not output.exists()
    assert list(tmp_path.iterdir()) == []
