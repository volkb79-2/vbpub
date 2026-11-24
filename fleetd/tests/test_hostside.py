from __future__ import annotations

import json
import math
import os
import runpy
import subprocess
import sys
from dataclasses import replace

import pytest

from fleetd import hostside
from fleetd.runner import probe_payload, probe_script
from fleetd.runner_layout import progress_home_path

from test_campaign import _real_runner_home, _spec_for_project


def _groups(*specs):
    return hostside.decode_payload(probe_payload({spec.name: spec for spec in specs}))


def _pressure_tree(root, memory="some avg10=2.50\n", io="some avg10=3.00\n"):
    (root / "pressure").mkdir(parents=True)
    for resource, content in {"memory": memory, "io": io}.items():
        if content is not None:
            (root / "pressure" / resource).write_text(content, encoding="ascii")
    return root


def test_the_shipped_probe_equals_the_direct_hostside_answer(tmp_path, monkeypatch):
    spec = _spec_for_project(".")
    missing = replace(spec, batch=1, candidate_ids=("c" * 64,))
    home, environment = _real_runner_home(tmp_path, spec)
    job_dir = home / "jobs" / spec.name
    job_dir.mkdir()
    (job_dir / "EXIT").write_text("0\n", encoding="ascii")
    (job_dir / "list.sha256").write_text(f"{spec.list_digest}\n", encoding="ascii")
    (job_dir / "progress-offset").write_text("0\n", encoding="ascii")
    progress = home / progress_home_path(spec.commit, spec.project, "contract-assay")
    progress.parent.mkdir(parents=True, exist_ok=True)
    progress.write_text(json.dumps({
        "event": "candidate",
        "candidate_id": spec.candidate_ids[0],
        "outcome_bucket": "killed",
        "elapsed_seconds": 4.0,
        "emitted_at": "2030-01-01T00:00:00Z",
        "profile": {"metrics": {"memory_peak_bytes": 8192}},
    }) + "\n", encoding="utf-8")
    (job_dir / "progress-end-offset").write_text(f"{len(progress.read_bytes())}\n", encoding="ascii")
    verdict = {
        "claims": [{"rigor": "R2", "mutation": {
            "survived": [{"candidate_id": spec.candidate_ids[0]}],
        }}],
        "judgment": {"r2": {"r2_command": {"r2_baseline": {"wall_s": 12.5}}}},
    }
    verdict_path = home / "campaigns" / f"{spec.name}-s1" / "verdict.json"
    verdict_path.parent.mkdir(parents=True)
    verdict_path.write_text(json.dumps(verdict), encoding="utf-8")
    proc = _pressure_tree(tmp_path / "proc")
    jobs = {spec.name: spec, missing.name: missing}
    script = probe_script(jobs, proc_root=str(proc))
    direct = hostside.probe_document(home, _groups(spec, missing), proc, home=home)
    monkeypatch.setenv("HOME", str(home))
    shipped = subprocess.run(["bash", "-c", script], cwd=home, env=environment, capture_output=True, text=True, timeout=10)
    assert shipped.returncode == 0, shipped.stderr
    assert json.loads(shipped.stdout) == direct
    rows = {row["name"]: row for row in direct["jobs"]}
    assert rows[spec.name]["state"] == "EXIT"
    assert rows[spec.name]["list_digest"] == spec.list_digest
    assert rows[spec.name]["progress_offset"] == 0
    assert rows[spec.name]["progress_end_offset"] == len(progress.read_bytes())
    assert rows[spec.name]["records"][0]["memory_peak_bytes"] == 8192
    assert rows[spec.name]["verdict_records"][0]["outcome_bucket"] == "survived"
    assert rows[spec.name]["baseline_seconds"] == 12.5
    assert rows[missing.name]["state"] == "MISSING"
    assert rows[missing.name]["checkout_ready"] is True
    assert direct["pressure"] == {"memory_some_avg10": 2.5, "io_some_avg10": 3.0}


def test_probe_reads_a_running_job_progress_without_a_stored_digest(tmp_path):
    spec = _spec_for_project("fleetd")
    home, _environment = _real_runner_home(tmp_path, spec)
    candidate = spec.candidate_ids[0]
    job_dir = home / "jobs" / spec.name
    job_dir.mkdir()
    (job_dir / "pid").write_text(f"{os.getpid()}\n", encoding="ascii")
    (job_dir / "progress-offset").write_text("0\n", encoding="ascii")
    progress = home / progress_home_path(spec.commit, spec.project, "contract-assay")
    progress.parent.mkdir(exist_ok=True)
    progress.write_text(
        "not-json\n"
        + json.dumps({"event": "heartbeat"}) + "\n"
        + json.dumps({"event": "candidate", "candidate_id": "f" * 64, "outcome_bucket": "killed"}) + "\n"
        + json.dumps({
            "event": "candidate", "candidate_id": candidate, "outcome_bucket": "killed",
            "elapsed_seconds": 1.0, "emitted_at": "2030-01-01T00:00:00Z",
            "profile": {"metrics": {"memory_peak_bytes": 4096}},
        }) + "\n",
        encoding="utf-8",
    )
    proc = _pressure_tree(tmp_path / "proc")
    document = hostside.probe_document(home, _groups(spec), proc, home=home)
    row = document["jobs"][0]
    assert row["state"] == "RUNNING"
    assert row["list_digest"] == spec.list_digest
    assert row["progress_count"] == 1
    assert row["progress_path"] == progress_home_path(spec.commit, spec.project, "contract-assay")
    assert row["records"] == [{"candidate_id": candidate, "outcome_bucket": "killed",
        "elapsed_seconds": 1.0, "emitted_at": "2030-01-01T00:00:00Z", "memory_peak_bytes": 4096}]
    missing_ids = replace(spec, batch=1, candidate_ids=("c" * 64,))
    (home / "jobs" / missing_ids.name).mkdir()
    missing_rows = hostside.probe_document(home, _groups(spec, missing_ids), proc, home=home)["jobs"]
    missing_row = next(row for row in missing_rows if row["name"] == missing_ids.name)
    assert missing_row["name"] == missing_ids.name
    assert missing_row["state"] == "UNKNOWN"
    assert missing_row["list_digest"] is None
    assert missing_row["progress_offset"] is None


def test_probe_reads_exit_verdicts_baseline_offsets_and_worktree_ready(tmp_path):
    first = _spec_for_project(".")
    second = _spec_for_project("ciu")
    second = replace(second, batch=1, commit="c" * 40)
    home, _environment = _real_runner_home(tmp_path, first)
    (home / "checkouts" / second.commit / "ciu").mkdir(parents=True)
    (home / "checkouts" / second.commit / ".git").mkdir()
    job_dir = home / "jobs" / first.name
    job_dir.mkdir()
    (job_dir / "EXIT").write_text("0\n", encoding="ascii")
    (job_dir / "list.sha256").write_text(f"{first.list_digest}\n", encoding="ascii")
    (job_dir / "progress-offset").write_text("0\n", encoding="ascii")
    (job_dir / "progress-end-offset").write_text("0\n", encoding="ascii")
    progress = home / progress_home_path(first.commit, first.project, "contract-assay")
    progress.parent.mkdir(exist_ok=True)
    progress.write_text("", encoding="utf-8")
    verdict = {"claims": [{"rigor": "R2", "mutation": {"budget_exceeded": [{
        "candidate_id": first.candidate_ids[0],
        "process_tree_evidence": {"leader_status": "not-started"},
    }]}}], "judgment": {"r2": {
        "mutation": {"survived": [{"candidate_id": first.candidate_ids[0]}]},
        "r2_command": {"r2_baseline": {"wall_s": 12.5}},
    }}}
    verdict_path = home / "campaigns" / f"{first.name}-s1" / "verdict.json"
    verdict_path.parent.mkdir(parents=True)
    verdict_path.write_text(json.dumps(verdict), encoding="utf-8")
    linked = replace(first, batch=3, commit="d" * 40)
    (home / "checkouts" / linked.commit).symlink_to(home / "checkouts" / first.commit)
    proc = _pressure_tree(tmp_path / "proc", memory="some avg10=101\n", io=None)
    document = hostside.probe_document(home, _groups(first, second, linked), proc, home=home)
    rows = {row["name"]: row for row in document["jobs"]}
    assert rows[first.name]["state"] == "EXIT"
    assert rows[first.name]["baseline_seconds"] == 12.5
    assert rows[first.name]["verdict_records"] == [
        {"candidate_id": first.candidate_ids[0], "outcome_bucket": "budget_exceeded",
         "elapsed_seconds": None, "emitted_at": None, "not_started": True},
        {"candidate_id": first.candidate_ids[0], "outcome_bucket": "survived",
         "elapsed_seconds": None, "emitted_at": None, "not_started": False},
    ]
    assert rows[second.name]["checkout_ready"] is True
    assert rows[linked.name]["checkout_ready"] is False
    assert document["pressure"] == {"memory_some_avg10": None, "io_some_avg10": None}


def test_probe_clamps_bad_offsets_and_turns_bad_artifacts_unknown(tmp_path):
    spec = _spec_for_project(".")
    undecodable = replace(spec, batch=1, candidate_ids=("c" * 64,))
    home, _environment = _real_runner_home(tmp_path, spec)
    job_dir = home / "jobs" / spec.name
    job_dir.mkdir()
    (job_dir / "EXIT").write_text("not-an-int\n", encoding="ascii")
    (job_dir / "progress-offset").write_text("7\n", encoding="ascii")
    (job_dir / "progress-end-offset").write_text("6\n", encoding="ascii")
    progress = home / progress_home_path(spec.commit, spec.project, "contract-assay")
    progress.parent.mkdir(exist_ok=True)
    progress.write_bytes(b"\xff\n")
    verdict = home / "campaigns" / f"{spec.name}-s1" / "verdict.json"
    verdict.parent.mkdir(parents=True)
    verdict.write_bytes(b"not-json")
    other_job = home / "jobs" / undecodable.name
    other_job.mkdir()
    (other_job / "EXIT").write_text("0\n", encoding="ascii")
    (other_job / "progress-offset").write_text("0\n", encoding="ascii")
    (other_job / "progress-end-offset").write_text("2\n", encoding="ascii")
    rows = hostside.probe_document(home, _groups(spec, undecodable), _pressure_tree(tmp_path / "proc"), home=home)["jobs"]
    row = next(row for row in rows if row["name"] == spec.name)
    assert row["exit_code"] is None
    assert row["progress_end_offset"] is None
    assert row["progress_mtime"] is not None
    assert row["records"] == []
    assert row["verdict_records"] == []
    assert row["baseline_seconds"] is None
    other = next(row for row in rows if row["name"] == undecodable.name)
    assert other["progress_mtime"] is None
    assert other["records"] == []


def test_probe_reports_null_path_and_unknown_state_for_bad_pid_and_offsets(tmp_path):
    spec = _spec_for_project(".")
    runner_home, _environment = _real_runner_home(tmp_path, spec)
    home = runner_home.parent / "other-home"
    home.mkdir()
    job_dir = home / "jobs" / spec.name
    job_dir.mkdir(parents=True)
    (job_dir / "pid").write_text("not-a-pid\n", encoding="ascii")
    (job_dir / "progress-offset").write_text("bad\n", encoding="ascii")
    (job_dir / "progress-end-offset").write_text("-1\n", encoding="ascii")
    row = hostside.probe_document(runner_home, _groups(spec), _pressure_tree(tmp_path / "proc"), home=home)["jobs"][0]
    assert row["state"] == "UNKNOWN"
    assert row["progress_offset"] is None
    assert row["progress_end_offset"] is None
    assert row["progress_path"] is None


def test_resolve_assay_lane_reads_project_then_root_and_refuses_unsafe_names(tmp_path):
    checkout = tmp_path / "checkout"
    project = checkout / "project"
    project.mkdir(parents=True)
    assert hostside.resolve_assay_lane(checkout, "project", "lane") == "lane"
    (project / "run-gate.toml").write_text('[lanes.other]\nkind = "command"\n', encoding="utf-8")
    (checkout / "run-gate.root.toml").write_text(
        '[lanes.lane]\nkind = "assay"\nassay_lane = "root-lane"\n',
        encoding="utf-8",
    )
    assert hostside.resolve_assay_lane(checkout, "project", "lane") == "root-lane"
    (project / "run-gate.toml").write_text("[broken\n", encoding="utf-8")
    (checkout / "run-gate.root.toml").write_text(
        '[lanes.lane]\nkind = "assay"\nassay_lane = "root-lane"\n',
        encoding="utf-8",
    )
    assert hostside.resolve_assay_lane(checkout, "project", "lane") == "root-lane"
    (project / "run-gate.toml").write_text(
        '[lanes.lane]\nkind = "assay"\nassay_lane = "../bad"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="safe artifact name"):
        hostside.resolve_assay_lane(checkout, "project", "lane")


@pytest.mark.parametrize(("memory", "io", "expected"), [
    ("some avg10=2.5\n", "some avg10=3.0\n", (2.5, 3.0)),
    ("some avg10=101\n", "some avg10=oops\n", (None, None)),
    ("", None, (None, None)), ("full avg10=2.5\n", "some avg60=3.0\n", (None, None)),
])
def test_pressure_reads_only_valid_some_avg10(tmp_path, memory, io, expected):
    proc = tmp_path / "proc"
    (proc / "pressure").mkdir(parents=True)
    if memory is not None:
        (proc / "pressure" / "memory").write_text(memory, encoding="ascii")
    if io is not None:
        (proc / "pressure" / "io").write_text(io, encoding="ascii")
    assert (hostside.read_pressure(proc, "memory"), hostside.read_pressure(proc, "io")) == expected


def test_progress_records_reject_ranges_and_utf8_and_filter_lines():
    candidate = "a" * 64
    with pytest.raises(ValueError, match="start offset"):
        hostside.progress_records(b"", set(), start_offset=True)
    with pytest.raises(ValueError, match="end offset"):
        hostside.progress_records(b"", set(), start_offset=2, end_offset=1)
    with pytest.raises(ValueError, match="not UTF-8"):
        hostside.progress_records(b"\xff", set())
    content = "\n".join(["not-json", "[]", json.dumps({"event": "heartbeat"}), json.dumps(
        {"event": "candidate", "candidate_id": "b" * 64, "outcome_bucket": "killed"}), json.dumps(
        {"event": "candidate", "candidate_id": candidate,
         "profile": {"metrics": {"memory_peak_bytes": 42}}})])
    assert hostside.progress_records(content.encode(), {candidate}) == [
        {"candidate_id": candidate, "memory_peak_bytes": 42}]


@pytest.mark.parametrize("candidate_id", [["a" * 64], {"candidate_id": "a" * 64}])
def test_progress_records_skip_non_string_candidate_ids(candidate_id):
    content = json.dumps({
        "event": "candidate",
        "candidate_id": candidate_id,
        "outcome_bucket": "killed",
    }).encode()
    assert hostside.progress_records(content, {"a" * 64}, strict=True) == []


@pytest.mark.parametrize(("baseline", "expected"), [
    ({"wall_s": 12.5}, 12.5), ({"wall_s": True}, None), ({"wall_s": -1}, None),
    ({"wall_s": math.inf}, None), ({"wall_s": "bad"}, None), ("bad", None),
])
def test_verdict_reading_accepts_shapes_and_validates_baseline(baseline, expected):
    candidate = "a" * 64
    document = {"claims": [None, {"rigor": "R1", "mutation": {}},
        {"rigor": "R2", "mutation": "bad"}, {"rigor": "R2", "mutation": {"killed": "bad",
        "survived": [None, {}, {"candidate_id": "f" * 64}, {"candidate_id": candidate}]}}],
        "judgment": {"r2": {"mutation": "bad", "r2_command": {"r2_baseline": baseline}}}}
    if not isinstance(baseline, dict):
        document["judgment"] = "bad"
    records, seconds = hostside.verdict_reading(json.dumps(document).encode(), {candidate})
    assert records == [{"candidate_id": candidate, "outcome_bucket": "survived",
        "elapsed_seconds": None, "emitted_at": None, "not_started": False}]
    assert seconds == expected
    with pytest.raises(ValueError, match="not UTF-8 JSON"):
        hostside.verdict_reading(b"\xff", set())
    with pytest.raises(ValueError, match="not UTF-8 JSON"):
        hostside.verdict_reading(b"not-json", set())
    for content in (b"[]", b'{"claims":"bad","judgment":{"r2":"bad"}}'):
        assert hostside.verdict_reading(content, set()) == ([], None)


@pytest.mark.parametrize("candidate_id", [["a" * 64], {"candidate_id": "a" * 64}])
def test_verdict_reading_skips_non_string_candidate_ids(candidate_id):
    document = {"claims": [{
        "rigor": "R2",
        "mutation": {"survived": [{"candidate_id": candidate_id}]},
    }]}
    assert hostside.verdict_reading(json.dumps(document).encode(), {"a" * 64}) == ([], None)


def test_payload_and_main_entry_shapes(tmp_path, monkeypatch, capsys):
    with pytest.raises(ValueError, match="group table"):
        hostside.decode_payload(hostside.base64.b64encode(hostside.zlib.compress(b"{}")).decode())
    spec = _spec_for_project(".")
    home, environment = _real_runner_home(tmp_path, spec)
    proc = _pressure_tree(tmp_path / "proc")
    monkeypatch.setenv("HOME", str(home))
    checkout = home / "checkouts" / spec.commit
    assert hostside.main(["resolve-assay", str(checkout), spec.project, spec.lane]) == 0
    assert capsys.readouterr().out.strip() == "contract-assay"
    assert hostside.main(["probe", str(home), probe_payload({spec.name: spec}), str(proc)]) == 0
    assert json.loads(capsys.readouterr().out)["jobs"][0]["name"] == spec.name
    monkeypatch.setattr(sys, "argv", ["hostside.py", "resolve-assay", str(checkout), spec.project, spec.lane])
    runpy.run_path(hostside.__file__, run_name="__main__")
    assert capsys.readouterr().out.strip() == "contract-assay"
    with pytest.raises(SystemExit, match="usage"):
        hostside.main([])
