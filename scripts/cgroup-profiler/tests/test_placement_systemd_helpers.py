"""Behavioral tests for placement's host-systemd and /proc trust boundary."""

from __future__ import annotations

import subprocess
import shutil
import sys

import pytest

from lib import placement


def _completed(stdout: str = "", *, code: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["busctl"], code, stdout, "")


@pytest.mark.parametrize(
    ("stdout", "code", "raises", "expected"),
    [
        ('o "/unit/path"', 0, None, "/unit/path"),
        ('s "not-an-object-path"', 0, None, None),
        ('o "/unit/path" extra', 0, None, None),
        ('o "unterminated', 0, None, None),
        ("", 1, None, None),
        ("", 0, OSError("busctl unavailable"), None),
        ("", 0, subprocess.TimeoutExpired("busctl", 5), None),
    ],
)
def test_unit_path_accepts_only_a_successful_object_path_reply(
    monkeypatch, stdout, code, raises, expected,
):
    def run(*_args, **_kwargs):
        if raises is not None:
            raise raises
        return _completed(stdout, code=code)

    monkeypatch.setattr(placement.subprocess, "run", run)
    assert placement._systemd_unit_path("rg-profile-test.scope") == expected


def test_unit_path_parses_stdout_from_a_real_successful_command(monkeypatch, tmp_path):
    fake_busctl = tmp_path / "busctl"
    fake_busctl.write_text(
        f"#!{sys.executable}\n"
        "print('o \"/unit/path\"')\n",
        encoding="utf-8",
    )
    fake_busctl.chmod(0o755)
    monkeypatch.setenv("CGPROFILE_BUSCTL", str(fake_busctl))

    assert placement._systemd_unit_path("rg-profile-test.scope") == "/unit/path"


@pytest.mark.parametrize(
    ("stdout", "stderr", "code", "raises", "expected"),
    [
        ('o "/unit/path"', "", 0, None, False),
        (
            "",
            "Call failed: org.freedesktop.systemd1.NoSuchUnit: "
            "Unit rg-profile-test.scope not loaded.\n",
            1, None, True,
        ),
        ("", "Call failed: Unit rg-profile-test.scope not loaded.\n", 1, None, True),
        ("", "Call failed: Unit another.scope not loaded.\n", 1, None, None),
        ("", "Call failed: Unit rg-profile-test.scope not loaded.\nextra", 1, None, None),
        (
            "",
            "Call failed: org.freedesktop.systemd1.NoSuchUnit: "
            "Unit another.scope not loaded.\n",
            1, None, None,
        ),
        ("", "Call failed: org.freedesktop.systemd1.NoReply: timed out", 1, None, None),
        ("", "", 1, None, None),
        ("", "", 0, OSError("busctl unavailable"), None),
        ("", "", 0, subprocess.TimeoutExpired("busctl", 5), None),
    ],
)
def test_unit_absence_requires_exact_systemd_response(
    stdout, stderr, code, raises, expected,
):
    def run(*_args, **_kwargs):
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(["busctl"], code, stdout, stderr)

    assert placement._systemd_unit_is_absent(
        "rg-profile-test.scope", run=run,
    ) is expected


@pytest.mark.parametrize(
    ("stdout", "code", "raises", "expected"),
    [
        ('s "loaded"', 0, None, "loaded"),
        ('b true', 0, None, None),
        ('s "loaded" extra', 0, None, None),
        ('s "unterminated', 0, None, None),
        ("", 1, None, None),
        ("", 0, OSError("busctl unavailable"), None),
        ("", 0, subprocess.TimeoutExpired("busctl", 5), None),
    ],
)
def test_string_property_requires_exactly_one_string_reply(
    monkeypatch, stdout, code, raises, expected,
):
    def run(*_args, **_kwargs):
        if raises is not None:
            raise raises
        return _completed(stdout, code=code)

    monkeypatch.setattr(placement.subprocess, "run", run)
    assert placement._systemd_property("/unit", "iface", "Name") == expected


@pytest.mark.parametrize(
    ("stdout", "code", "expected"),
    [
        ("b true", 0, True),
        ("b false", 0, False),
        ("s true", 0, None),
        ("b yes", 0, None),
        ("b true extra", 0, None),
        ("b true", 1, None),
        ('b "unterminated', 0, None),
    ],
)
def test_boolean_property_rejects_unknown_or_malformed_values(
    monkeypatch, stdout, code, expected,
):
    monkeypatch.setattr(
        placement.subprocess, "run",
        lambda *_args, **_kwargs: _completed(stdout, code=code),
    )
    assert placement._systemd_bool_property("/unit", "iface", "Delegate") is expected


@pytest.mark.parametrize("raises", [OSError("bus unavailable"), subprocess.TimeoutExpired("busctl", 5)])
def test_boolean_property_treats_transport_failure_as_unknown(monkeypatch, raises):
    def run(*_args, **_kwargs):
        raise raises

    monkeypatch.setattr(placement.subprocess, "run", run)
    assert placement._systemd_bool_property("/unit", "iface", "Delegate") is None


@pytest.mark.parametrize(
    ("stdout", "code", "expected"),
    [
        ("as 2 memory cpu", 0, ["memory", "cpu"]),
        ("as 0", 0, []),
        ("s memory", 0, None),
        ("as 2 memory", 0, None),
        ("as nope memory", 0, None),
        ('as 1 "unterminated', 0, None),
        ("as 1 memory", 1, None),
    ],
)
def test_string_array_property_requires_a_well_formed_counted_reply(
    monkeypatch, stdout, code, expected,
):
    monkeypatch.setattr(
        placement.subprocess, "run",
        lambda *_args, **_kwargs: _completed(stdout, code=code),
    )
    assert placement._systemd_string_array_property("/unit", "iface", "Controllers") == expected


@pytest.mark.parametrize("raises", [OSError("bus unavailable"), subprocess.TimeoutExpired("busctl", 5)])
def test_string_array_property_treats_transport_failure_as_unknown(monkeypatch, raises):
    def run(*_args, **_kwargs):
        raise raises

    monkeypatch.setattr(placement.subprocess, "run", run)
    assert placement._systemd_string_array_property("/unit", "iface", "Controllers") is None


@pytest.mark.parametrize(
    ("unit", "slice_unit", "controllers"),
    [
        ("not-a-scope", "dev-gates.slice", placement.REQUIRED_CONTROLLERS),
        ("rg-profile-a.scope", "not-a-slice", placement.REQUIRED_CONTROLLERS),
        ("rg-profile-a.scope", "dev-gates.slice", []),
        ("rg-profile-a.scope", "dev-gates.slice", ["memory;bad"]),
    ],
)
def test_scope_creation_rejects_invalid_unit_or_controller_names_before_bus_call(
    unit, slice_unit, controllers,
):
    assert placement._systemd_create_scope(
        unit, slice_unit, [123], controllers,
        run=lambda *_args, **_kwargs: pytest.fail("invalid request reached systemd"),
    ) is None


@pytest.mark.parametrize("failure", ["exception", "timeout", "nonzero", "not-visible"])
def test_scope_creation_refuses_failed_or_unresolved_manager_transactions(
    monkeypatch, failure,
):
    monkeypatch.setattr(placement.time, "sleep", lambda _seconds: None)
    if failure == "not-visible":
        monkeypatch.setattr(placement, "_systemd_unit_path", lambda _unit: None)

        def run(*_args, **_kwargs):
            return _completed()
    else:
        def run(*_args, **_kwargs):
            if failure == "exception":
                raise OSError("bus unavailable")
            if failure == "timeout":
                raise subprocess.TimeoutExpired("busctl", 10)
            return _completed(code=1)

    assert placement._systemd_create_scope(
        "rg-profile-test.scope", "dev-gates.slice", [123],
        placement.REQUIRED_CONTROLLERS, run=run,
    ) is None


@pytest.mark.parametrize(
    "failure", ["exception", "timeout", "nonzero", "missing", "unknown", "active", "dead"]
)
def test_scope_stop_reports_only_a_confirmed_retirement(monkeypatch, failure):
    monkeypatch.setattr(placement.time, "sleep", lambda _seconds: None)
    if failure in {"active", "dead"}:
        monkeypatch.setattr(placement, "_systemd_unit_path", lambda _unit: "/unit")
        monkeypatch.setattr(
            placement, "_systemd_property",
            lambda _path, _iface, name: (
                {"ActiveState": "active", "SubState": "running"}
                if failure == "active" else
                {"ActiveState": "inactive", "SubState": "dead"}
            ).get(name),
        )
    else:
        monkeypatch.setattr(placement, "_systemd_unit_path", lambda _unit: None)
        monkeypatch.setattr(
            placement, "_systemd_unit_is_absent",
            lambda _unit, **_kwargs: True if failure == "missing" else None,
        )

    def run(*_args, **_kwargs):
        if failure == "exception":
            raise OSError("bus unavailable")
        if failure == "timeout":
            raise subprocess.TimeoutExpired("busctl", 10)
        return _completed(code=1 if failure == "nonzero" else 0)

    result = placement._systemd_stop_unit("rg-profile-test.scope", run=run)
    assert result is (failure in {"dead", "missing"})


@pytest.mark.parametrize(
    ("cgroup", "expected"),
    [
        ("relative/path", None),
        ("/", None),
        ("/dev.slice//service.scope", None),
        ("/dev.slice/../escape.scope", None),
        ("/no-unit", None),
        ("/dev.slice/no-unit", ("dev.slice", "no-unit")),
        ("/dev.slice/job.service/child", ("job.service", "child")),
    ],
)
def test_systemd_destination_selects_a_real_unit_boundary(cgroup, expected):
    assert placement._systemd_destination(cgroup) == expected


@pytest.mark.parametrize(
    ("unit", "path", "loaded", "actual", "expected"),
    [
        ("job.scope", "/unit", "loaded", "/dev.slice/job.scope", True),
        ("job.scope", None, "loaded", "/dev.slice/job.scope", False),
        ("job.invalid", "/unit", "loaded", "/dev.slice/job.invalid", False),
        ("job.scope", "/unit", "not-found", "/dev.slice/job.scope", False),
        ("job.scope", "/unit", "loaded", "/dev.slice/other.scope", False),
        ("job.scope", "/unit", "loaded", None, False),
    ],
)
def test_systemd_unit_identity_is_verified_against_control_group(
    monkeypatch, unit, path, loaded, actual, expected,
):
    monkeypatch.setattr(placement, "_systemd_unit_path", lambda _unit: path)
    monkeypatch.setattr(
        placement, "_systemd_property",
        lambda _path, _interface, name: {
            "LoadState": loaded, "ControlGroup": actual,
        }.get(name),
    )
    assert placement._systemd_unit_cgroup_matches(unit, "/dev.slice/job.scope") is expected


def test_proc_identity_readers_parse_fields_after_parenthesized_comm(tmp_path):
    proc = tmp_path / "proc"
    process = proc / "42"
    process.mkdir(parents=True)
    suffix = ["S", "7"] + ["0"] * 17 + ["98765"]
    (process / "stat").write_text("42 (comm with ) parens) " + " ".join(suffix))
    assert placement._process_start_time_ticks(str(proc), 42) == "98765"
    assert placement._process_parent_pid(str(proc), 42) == 7


@pytest.mark.parametrize("contents", ["", "not a stat line", "42 (short) S", "42 ("])
def test_proc_stat_identity_readers_reject_missing_or_short_records(tmp_path, contents):
    proc = tmp_path / "proc"
    process = proc / "42"
    process.mkdir(parents=True)
    (process / "stat").write_text(contents)
    assert placement._process_start_time_ticks(str(proc), 42) is None
    assert placement._process_parent_pid(str(proc), 42) is None


@pytest.mark.parametrize(
    ("contents", "expected"),
    [
        ("Name: daemon\nNSpid:\t7123 12\n", 7123),
        ("Name: daemon\n", None),
        ("NSpid:\t0 12\n", None),
        ("NSpid: invalid\n", None),
    ],
)
def test_host_pid_reader_uses_the_outermost_positive_namespace_pid(tmp_path, contents, expected):
    proc = tmp_path / "proc"
    (proc / "self").mkdir(parents=True)
    (proc / "self" / "status").write_text(contents)
    assert placement._host_pid_of_self(str(proc)) == expected


def test_host_pid_reader_treats_a_missing_status_file_as_unknown(tmp_path):
    assert placement._host_pid_of_self(str(tmp_path / "absent-proc")) is None


@pytest.mark.parametrize(
    ("path", "parent", "expected"),
    [
        ("/dev.slice/gates", "/dev.slice/gates", True),
        ("/dev.slice/gates/lane", "/dev.slice/gates", True),
        ("/dev.slice/gates-extra/lane", "/dev.slice/gates", False),
        ("relative/path", "/dev.slice", False),
        ("/dev.slice/lane", "relative", False),
        (None, "/dev.slice", False),
    ],
)
def test_cgroup_containment_uses_component_boundaries(path, parent, expected):
    assert placement._within_cgroup(path, parent) is expected


@pytest.mark.parametrize(
    ("contents", "expected"),
    [("12\nnot-a-pid\n34\n", [12, 34]), ("not-a-pid\n", []), ("", [])],
)
def test_cgroup_pid_reader_ignores_unparseable_rows(tmp_path, contents, expected):
    path = tmp_path / "cgroup.procs"
    path.write_text(contents)
    assert placement._read_pids(str(path)) == expected


@pytest.mark.parametrize(
    ("contents", "expected"),
    [
        ("populated 1\nfrozen 0\n", True),
        ("populated 0\nfrozen 0\n", False),
        ("frozen 1\n", False),
        ("populated 1 extra\n", False),
        ("populated unknown\n", False),
    ],
)
def test_cgroup_populated_requires_one_exact_positive_kernel_fact(
    tmp_path, contents, expected,
):
    path = tmp_path / "lane"
    path.mkdir()
    (path / "cgroup.events").write_text(contents)
    assert placement._cgroup_is_populated(str(path)) is expected


def _placement_with_identity_callbacks(tmp_path, **overrides):
    origin_cgroup = overrides.pop(
        "origin_cgroup", "/dev.slice/dev-background.slice",
    )
    defaults = {
        "host_proc_view": lambda _proc: True,
        "host_pid": lambda: 9999,
        "pid_exists": lambda _pid: True,
        "pid_start_time": lambda _pid: "42",
        "pid_cgroup": lambda _pid: "/dev.slice/dev-background.slice/docker-a.scope",
        "unit_cgroup_verifier": lambda _unit, _path: True,
        "pid_parent": lambda _pid: None,
    }
    defaults.update(overrides)
    return placement.LanePlacement(
        cgroup_root=str(tmp_path),
        gates_cgroup="/dev.slice/dev-gates.slice",
        token="identity-test-token",
        origin_cgroup=origin_cgroup,
        request=placement.PlacementRequest(),
        **defaults,
    )


@pytest.mark.parametrize("pid", [True, 0, -1, "42"])
def test_capture_pid_rejects_nonpositive_or_noninteger_identity(tmp_path, pid):
    lane = _placement_with_identity_callbacks(tmp_path)
    assert lane._capture_pid(pid) is None
    assert lane.error == placement.REFUSED_IDENTITY_UNAVAILABLE


def test_capture_pid_refuses_when_host_proc_view_or_host_pid_is_unavailable(tmp_path):
    hidden = _placement_with_identity_callbacks(tmp_path / "hidden", host_proc_view=lambda _proc: False)
    assert hidden._capture_pid(101) is None
    assert hidden.error == placement.REFUSED_NO_HOST_PROC

    unknown = _placement_with_identity_callbacks(tmp_path / "unknown", host_pid=lambda: None)
    assert unknown._capture_pid(101) is None
    assert unknown.error == placement.REFUSED_IDENTITY_UNAVAILABLE


def test_capture_pid_refuses_daemon_pid_but_skips_a_pid_that_already_exited(tmp_path):
    own = _placement_with_identity_callbacks(tmp_path / "own", host_pid=lambda: 101)
    assert own._capture_pid(101) is None
    assert own.error == placement.REFUSED_IDENTITY_UNAVAILABLE

    vanished = _placement_with_identity_callbacks(
        tmp_path / "vanished", pid_exists=lambda _pid: False,
    )
    assert vanished._capture_pid(101) is None
    assert vanished.error is None


@pytest.mark.parametrize(
    ("start_time", "cgroup"),
    [(None, "/dev.slice/dev-background.slice/docker-a.scope"),
     ("42", None)],
)
def test_capture_pid_requires_start_time_and_current_cgroup(tmp_path, start_time, cgroup):
    lane = _placement_with_identity_callbacks(
        tmp_path, pid_start_time=lambda _pid: start_time,
        pid_cgroup=lambda _pid: cgroup,
    )
    assert lane._capture_pid(101) is None
    assert lane.error == placement.REFUSED_IDENTITY_UNAVAILABLE


@pytest.mark.parametrize(
    ("cgroup", "unit_ok"),
    [("/outside.slice/job.scope", True), ("/origin/no-systemd-boundary", True),
     ("/dev.slice/dev-background.slice/docker-a.scope", False)],
)
def test_capture_pid_rejects_ambiguous_origin_or_unverified_unit(tmp_path, cgroup, unit_ok):
    lane = _placement_with_identity_callbacks(
        tmp_path, pid_cgroup=lambda _pid: cgroup,
        unit_cgroup_verifier=lambda _unit, _path: unit_ok,
    )
    assert lane._capture_pid(101) is None
    assert lane.error == placement.REFUSED_IDENTITY_UNAVAILABLE


@pytest.mark.parametrize("race", ["exit", "reuse", "move"])
def test_capture_pid_rechecks_process_identity_and_membership_before_admitting(tmp_path, race):
    origin = "/dev.slice/dev-background.slice/docker-a.scope"
    exists_reads = iter([True, False, False]) if race == "exit" else iter([True, True, True])
    start_reads = iter(["42", "43"]) if race == "reuse" else iter(["42", "42"])
    cgroup_reads = iter([origin, "/dev.slice/escaped.scope"]) if race == "move" else iter([origin, origin])
    lane = _placement_with_identity_callbacks(
        tmp_path / race,
        pid_exists=lambda _pid: next(exists_reads),
        pid_start_time=lambda _pid: next(start_reads),
        pid_cgroup=lambda _pid: next(cgroup_reads),
    )
    assert lane._capture_pid(101) is None
    if race == "exit":
        assert lane.error is None
    else:
        assert lane.error == placement.REFUSED_IDENTITY_UNAVAILABLE


def test_capture_pid_inherits_origin_only_from_a_live_journaled_ancestor(tmp_path):
    lane = _placement_with_identity_callbacks(
        tmp_path,
        pid_cgroup=lambda _pid: "/dev.slice/dev-gates.slice/rg-profile-x.scope/rg-x",
        pid_parent=lambda pid: 101 if pid == 202 else None,
    )
    lane.scope_cgroup = "/dev.slice/dev-gates.slice/rg-profile-x.scope"
    lane.pid_records[101] = {
        "pid": 101,
        "start_time_ticks": "42",
        "origin_cgroup": "/dev.slice/dev-background.slice/docker-a.scope",
        "origin_unit": "docker-a.scope",
        "origin_unit_cgroup": "/dev.slice/dev-background.slice/docker-a.scope",
        "origin_subcgroup": "",
    }
    result = lane._capture_pid(202)
    assert result is not None
    assert result["origin_cgroup"] == lane.pid_records[101]["origin_cgroup"]


def test_capture_pid_refuses_scope_descendant_without_live_ancestor_proof(tmp_path):
    lane = _placement_with_identity_callbacks(
        tmp_path,
        pid_cgroup=lambda _pid: "/dev.slice/dev-gates.slice/rg-profile-x.scope/rg-x",
    )
    lane.scope_cgroup = "/dev.slice/dev-gates.slice/rg-profile-x.scope"
    assert lane._capture_pid(202) is None
    assert lane.error == placement.REFUSED_IDENTITY_UNAVAILABLE


def test_capture_pid_refuses_origin_paths_without_a_systemd_unit_boundary(tmp_path):
    lane = _placement_with_identity_callbacks(
        tmp_path,
        origin_cgroup="/origin",
        pid_cgroup=lambda _pid: "/origin/task",
    )
    assert lane._capture_pid(101) is None
    assert lane.error == placement.REFUSED_IDENTITY_UNAVAILABLE


def test_ancestor_lookup_skips_reused_parent_and_finds_nearest_live_record(tmp_path):
    lane = _placement_with_identity_callbacks(
        tmp_path,
        pid_parent=lambda pid: {303: 202, 202: 101}.get(pid),
        pid_start_time=lambda pid: {202: "new", 101: "old"}.get(pid, str(pid)),
    )
    stale = {"start_time_ticks": "old"}
    nearest = {"start_time_ticks": "old"}
    lane.pid_records.update({202: stale, 101: nearest})
    assert lane._ancestor_record(303) is nearest


def test_ancestor_lookup_stops_on_root_or_a_parent_cycle(tmp_path):
    no_parent = _placement_with_identity_callbacks(tmp_path / "root", pid_parent=lambda _pid: 0)
    assert no_parent._ancestor_record(303) is None

    cycle = _placement_with_identity_callbacks(
        tmp_path / "cycle", pid_parent=lambda pid: 202 if pid == 303 else 303,
    )
    assert cycle._ancestor_record(303) is None


def test_ancestor_lookup_has_a_finite_bound_for_an_unbroken_parent_chain(tmp_path):
    lane = _placement_with_identity_callbacks(
        tmp_path, pid_parent=lambda pid: pid + 1,
    )
    assert lane._ancestor_record(1) is None


def test_persist_is_a_noop_without_a_journal_and_serializes_sorted_identity_records(tmp_path):
    lane = _placement_with_identity_callbacks(tmp_path)
    assert lane._persist(state="ignored") is True

    snapshots = []
    lane._state_write = lambda state: snapshots.append(dict(state))
    lane._journal = {"state": "prepared"}
    lane.pid_records = {20: {"pid": 20}, 3: {"pid": 3}}
    lane.leaf_created = True
    lane.successfully_placed = True
    assert lane._persist() is True
    assert snapshots[-1]["state"] == "prepared"
    assert list(snapshots[-1]["pids"]) == ["3", "20"]
    assert snapshots[-1]["leaf_created"] is True
    assert snapshots[-1]["was_placed"] is True


@pytest.mark.parametrize("with_logger", [False, True])
def test_persist_failure_is_false_and_optional_logging_is_safe(tmp_path, with_logger):
    logged = []

    def fail(_journal):
        raise OSError("journal device unavailable")

    lane = _placement_with_identity_callbacks(
        tmp_path,
        state_write=fail,
        log=logged.append if with_logger else None,
    )
    lane._journal = {"state": "prepared"}
    assert lane._persist(state="restoring") is False
    assert lane._journal["state"] == "restoring"
    assert bool(logged) is with_logger


def test_default_pid_readers_delegate_to_the_target_resolvers(tmp_path, monkeypatch):
    lane = placement.LanePlacement(
        cgroup_root=str(tmp_path), gates_cgroup="/dev.slice/dev-gates.slice",
        token="default-reader-token", origin_cgroup="/dev.slice/dev-background.slice",
        request=placement.PlacementRequest(), proc_root=str(tmp_path / "proc"),
    )
    calls = []
    monkeypatch.setattr(
        "lib.targets.cgroup_of_pid",
        lambda pid, root, proc: calls.append(("cgroup", pid, root, proc)) or "/found.scope",
    )
    monkeypatch.setattr(
        "lib.targets.pids_in_cgroup",
        lambda cg, root, proc: calls.append(("pids", cg, root, proc)) or [7, 8],
    )
    (tmp_path / "proc" / "71").mkdir(parents=True)
    assert lane._default_pid_cgroup(7) == "/found.scope"
    assert lane._default_pid_exists(71) is True
    assert lane._default_pid_exists(72) is False
    assert lane._default_pids_in_cgroup("/some.scope", "root", "proc") == [7, 8]
    assert calls == [
        ("cgroup", 7, str(tmp_path), str(tmp_path / "proc")),
        ("pids", "/some.scope", "root", "proc"),
    ]


def test_guarded_directory_mutations_need_no_event_sink(tmp_path):
    root = tmp_path / "cgroup"
    scope = root / "dev.slice" / "dev-gates.slice" / "rg-profile-identity-test-token.scope"
    scope.mkdir(parents=True)
    lane = placement.LanePlacement(
        cgroup_root=str(root), gates_cgroup="/dev.slice/dev-gates.slice",
        token="identity-test-token", origin_cgroup="/dev.slice/dev-background.slice",
        request=placement.PlacementRequest(), rmdir=shutil.rmtree,
    )
    lane._set_scope("/dev.slice/dev-gates.slice/rg-profile-identity-test-token.scope")
    leaf = scope / lane.leaf_name
    lane._mkdir(str(leaf))
    lane._record_write(str(leaf / "cgroup.procs"), "12")
    lane._rmdir(str(leaf))
    assert not leaf.exists()
