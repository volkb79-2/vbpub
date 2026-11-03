"""B206: the cgprofile observer client, its profile mapping and its run policy.

The wire is pinned by golden fixtures copied byte-for-byte from cgprofile
(``tests/fixtures/observer/README.md``).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from assay import observer_windows as ow
from observer_support import FakeObserver, error_reply, golden, golden_handler

SRC = Path(__file__).parents[2] / "src" / "assay"


def _open(path: Path, **overrides):
    args = {"root_pid": 100, "mode": "cgroup", "interval_ms": 250, "thread_id": 50}
    args.update(overrides)
    return ow.open_window(str(path), **args)


# --------------------------------------------------------------------------
# The wire against the golden fixtures.
# --------------------------------------------------------------------------


def test_window_open_sends_the_golden_request_and_accepts_the_golden_reply(tmp_path: Path):
    fixture = golden("open")
    with FakeObserver(tmp_path / "o.sock", lambda request: fixture["response"]) as observer:
        reply = _open(observer.path)
    assert observer.requests == [fixture["request"]]
    assert reply == fixture["response"]


def test_window_close_sends_the_golden_request_and_accepts_every_golden_reply(tmp_path: Path):
    for name in ("close-cgroup", "close-process-tree", "close-shared"):
        fixture = golden(name)
        with FakeObserver(tmp_path / f"{name}.sock", lambda request, f=fixture: f["response"]) as observer:
            reply = ow.close_window(
                str(observer.path), window=fixture["request"]["args"]["window"], thread_id=50
            )
        assert observer.requests == [fixture["request"]], name
        assert reply == fixture["response"], name


@pytest.mark.parametrize(
    ("name", "status", "mode"),
    [
        ("close-cgroup", ow.STATUS_MEASURED, "cgroup"),
        ("close-process-tree", ow.STATUS_MEASURED, "process-tree"),
        ("close-shared", ow.STATUS_PARTIAL, "cgroup"),
    ],
)
def test_the_profile_carries_the_golden_numbers_byte_for_byte(name: str, status: str, mode: str):
    response = golden(name)["response"]
    result = ow.WindowResult(reply=response, mode=mode, window=response["window"], asked=True)
    profile = ow.build_profile(
        result, subject={"tool": "assay", "id": "c"}, wall_seconds=1.5
    )
    ow.validate_profile(profile)
    assert profile["schema"] == "resource-profile/1"
    assert profile["status"] == status
    assert profile["reason"] is None
    assert profile["wall_seconds"] == 1.5
    # Byte-equal JSON for the metrics, the missing reasons, sources and context.
    assert json.dumps(profile["metrics"]) == json.dumps(response["metrics"])
    assert json.dumps(profile["missing"]) == json.dumps(response["missing"])
    assert json.dumps(profile["sources"]) == json.dumps(response["sources"])
    assert json.dumps(profile["context"]) == json.dumps(response["context"])
    assert profile["observer"]["mode"] == mode
    assert profile["observer"]["window"] == response["window"]
    assert profile["observer"]["sample_interval_seconds"] == 0.25


def test_process_tree_profile_names_requires_cgroup_mode_for_iops_and_psi():
    response = golden("close-process-tree")["response"]
    profile = ow.build_profile(
        ow.WindowResult(reply=response, mode="process-tree", window=response["window"], asked=True),
        subject={}, wall_seconds=2.0,
    )
    for key in ("io_read_ops", "io_write_ops", "psi_memory_some_seconds", "psi_io_full_seconds",
                "memory_peak_bytes", "swap_peak_bytes"):
        assert profile["metrics"][key] is None
        assert profile["missing"][key] == "REQUIRES_CGROUP_MODE"
    assert profile["metrics"]["cpu_seconds"] == 4.6


def test_a_shared_cgroup_keeps_only_the_sampled_rss():
    response = golden("close-shared")["response"]
    profile = ow.build_profile(
        ow.WindowResult(reply=response, mode="cgroup", window=response["window"], asked=True),
        subject={}, wall_seconds=2.0,
    )
    assert profile["status"] == "PARTIAL"
    assert profile["metrics"]["rss_peak_bytes"] == 6144000
    assert profile["metrics"]["cpu_seconds"] is None
    assert set(profile["missing"].values()) == {"SHARED_CGROUP"}


# --------------------------------------------------------------------------
# NO_MEASUREMENT: every failure is an explicit reason, never a fallback.
# --------------------------------------------------------------------------


def test_an_absent_socket_is_no_measurement_with_every_metric_null_and_missing_filled(tmp_path: Path):
    with pytest.raises(ow.ObserverFailure) as caught:
        _open(tmp_path / "absent.sock")
    assert caught.value.code == ow.OBSERVER_UNAVAILABLE
    profile = ow.build_profile(
        ow.WindowResult(reason=ow.OBSERVER_UNAVAILABLE, asked=True),
        subject={"id": "c"}, wall_seconds=0.75,
    )
    ow.validate_profile(profile)
    assert profile["status"] == "NO_MEASUREMENT"
    assert profile["reason"] == "OBSERVER_UNAVAILABLE"
    assert profile["wall_seconds"] == 0.75  # Assay's own clock survives
    assert set(profile["metrics"]) == set(ow.METRIC_KEYS)
    assert all(value is None for value in profile["metrics"].values())
    assert profile["missing"] == {key: "OBSERVER_UNAVAILABLE" for key in ow.METRIC_KEYS}


def test_a_regular_file_at_the_socket_path_is_unavailable_not_a_crash(tmp_path: Path):
    path = tmp_path / "not-a-socket"
    path.write_text("x")
    with pytest.raises(ow.ObserverFailure) as caught:
        _open(path)
    assert caught.value.code in {ow.OBSERVER_UNAVAILABLE, ow.OBSERVER_ERROR}


@pytest.mark.parametrize(
    ("reply", "code"),
    [
        (error_reply("peer-refused"), ow.OBSERVER_REFUSED),
        (error_reply("observer-refused"), ow.OBSERVER_REFUSED),
        (error_reply("bad-argument"), ow.OBSERVER_CONTRACT_UNSUPPORTED),
        (error_reply("window-refused"), ow.OBSERVER_ERROR),
        (error_reply("daemon-error"), ow.OBSERVER_ERROR),
        ({**golden("open")["response"], "contract": 2}, ow.OBSERVER_CONTRACT_UNSUPPORTED),
        (b"not json\n", ow.OBSERVER_ERROR),
        (b"[1]\n", ow.OBSERVER_ERROR),
        (b'{"ok":true,"contract":3}\n{"extra":1}\n', ow.OBSERVER_ERROR),
        ({"ok": True, "contract": 3, "window": "w-short", "mode": "cgroup", "started_at": "x"},
         ow.OBSERVER_ERROR),
    ],
)
def test_every_open_failure_maps_to_one_reason_code(tmp_path: Path, reply, code: str):
    with FakeObserver(tmp_path / "o.sock", lambda request: reply) as observer:
        with pytest.raises(ow.ObserverFailure) as caught:
            _open(observer.path)
    assert caught.value.code == code


@pytest.mark.parametrize(
    ("reply", "code"),
    [
        (error_reply("window-lost"), ow.WINDOW_LOST),
        (error_reply("peer-refused"), ow.OBSERVER_REFUSED),
        (error_reply("daemon-error"), ow.OBSERVER_ERROR),
        (error_reply("bad-argument"), ow.OBSERVER_ERROR),
        (b"{broken\n", ow.OBSERVER_ERROR),
    ],
)
def test_every_close_failure_maps_to_one_reason_code(tmp_path: Path, reply, code: str):
    with FakeObserver(tmp_path / "o.sock", lambda request: reply) as observer:
        with pytest.raises(ow.ObserverFailure) as caught:
            ow.close_window(str(observer.path), window="w-" + "0" * 32, thread_id=1)
    assert caught.value.code == code


def test_a_malformed_close_reply_is_an_error_never_a_partial_profile(tmp_path: Path):
    reply = json.loads(json.dumps(golden("close-cgroup")["response"]))
    reply["metrics"]["cpu_seconds"] = None  # a null with no `missing` reason
    with FakeObserver(tmp_path / "o.sock", lambda request: reply) as observer:
        with pytest.raises(ow.ObserverFailure) as caught:
            ow.close_window(str(observer.path), window=reply["window"], thread_id=1)
    assert caught.value.code == ow.OBSERVER_ERROR
    reply["metrics"]["cpu_seconds"] = -1
    reply["missing"]["cpu_seconds"] = "x"
    with FakeObserver(tmp_path / "o2.sock", lambda request: reply) as observer:
        with pytest.raises(ow.ObserverFailure):
            ow.close_window(str(observer.path), window=reply["window"], thread_id=1)


def test_a_silent_observer_times_out_within_the_bound(tmp_path: Path):
    with FakeObserver(tmp_path / "o.sock", lambda request: None) as observer:
        with pytest.raises(ow.ObserverFailure) as caught:
            ow.open_window(str(observer.path), root_pid=1, mode="cgroup", timeout=0.3)
    assert caught.value.code == ow.OBSERVER_ERROR
    assert "timed out" in caught.value.detail


def test_a_reply_where_every_metric_is_null_is_no_measurement_with_the_daemons_reason():
    reply = json.loads(json.dumps(golden("close-shared")["response"]))
    for key in ow.METRIC_KEYS:
        reply["metrics"][key] = None
        reply["missing"][key] = "CONTROLLER_DISABLED"
    profile = ow.build_profile(
        ow.WindowResult(reply=reply, mode="cgroup", window=reply["window"], asked=True),
        subject={}, wall_seconds=1.0,
    )
    assert profile["status"] == "NO_MEASUREMENT"
    assert profile["reason"] == "CONTROLLER_DISABLED"


def test_a_candidate_that_never_ran_is_not_started_and_a_missing_result_is_window_lost():
    started = ow.build_profile(None, subject={}, wall_seconds=0.0, not_started=True)
    assert (started["status"], started["reason"]) == ("NO_MEASUREMENT", "NOT_STARTED")
    lost = ow.build_profile(None, subject={}, wall_seconds=1.0)
    assert (lost["status"], lost["reason"]) == ("NO_MEASUREMENT", "WINDOW_LOST")


def test_profile_validation_rejects_a_number_without_a_status_and_a_reason_without_no_measurement():
    response = golden("close-cgroup")["response"]
    good = ow.build_profile(
        ow.WindowResult(reply=response, mode="cgroup", window=response["window"], asked=True),
        subject={}, wall_seconds=1.0,
    )
    ow.validate_profile(good)
    bad = {**good, "reason": "OBSERVER_ERROR"}
    with pytest.raises(ValueError, match="reason is set exactly"):
        ow.validate_profile(bad)
    unmeasured = ow.build_profile(None, subject={}, wall_seconds=1.0, not_started=True)
    with pytest.raises(ValueError, match="carries no metric values"):
        ow.validate_profile({**unmeasured, "metrics": {**unmeasured["metrics"], "cpu_seconds": 1}})
    with pytest.raises(ValueError):
        ow.validate_profile({**good, "schema": "resource-profile/2"})


# --------------------------------------------------------------------------
# Run-wide policy: the coordinator's ProfileCollector.
# --------------------------------------------------------------------------


def _failure(code: str) -> ow.WindowResult:
    return ow.WindowResult(reason=code, asked=True)


def _success() -> ow.WindowResult:
    response = golden("close-cgroup")["response"]
    return ow.WindowResult(reply=response, mode="cgroup", window=response["window"], asked=True)


def test_without_a_socket_nothing_is_asked_and_every_candidate_is_unavailable():
    collector = ow.ProfileCollector(socket_path=None, jobs=1)
    config = collector.config()
    assert config.skip_reason == ow.OBSERVER_UNAVAILABLE
    with ow.activate(config) as slot:
        assert slot is not None and not slot.active
        assert slot.result is not None and slot.result.reason == ow.OBSERVER_UNAVAILABLE
        assert slot.result.asked is False
        assert ow.current_slot() is None  # runners see no window to open


def test_contract_support_is_asked_exactly_once_per_run(tmp_path: Path):
    with FakeObserver(tmp_path / "o.sock", lambda request: error_reply("bad-argument")) as observer:
        collector = ow.ProfileCollector(socket_path=str(observer.path), jobs=1)
        for _ in range(5):
            config = collector.config()
            with ow.activate(config) as slot:
                if slot.active:
                    ow.open_for_root(os.getpid())
                collector.record(slot.result)
        assert len(observer.requests) == 1
        assert collector.config().skip_reason == ow.OBSERVER_CONTRACT_UNSUPPORTED


def test_one_refusal_costs_that_candidate_only_and_three_errors_stop_the_asking():
    collector = ow.ProfileCollector(socket_path="/nonexistent/observer.sock", jobs=3)
    collector.record(_failure(ow.OBSERVER_REFUSED))
    assert collector.config().skip_reason is None
    collector.record(_success())  # a success resets the consecutive count
    collector.record(_failure(ow.OBSERVER_ERROR))
    collector.record(_failure(ow.OBSERVER_ERROR))
    assert collector.config().skip_reason is None
    collector.record(_failure(ow.OBSERVER_ERROR))
    assert collector.config().skip_reason == ow.OBSERVER_ERROR


def test_a_window_lost_to_a_daemon_restart_does_not_count_as_a_failure():
    collector = ow.ProfileCollector(socket_path="/x.sock", jobs=1)
    for _ in range(5):
        collector.record(_failure(ow.WINDOW_LOST))
    assert collector.config().skip_reason is None


def test_the_mode_is_cgroup_for_one_worker_and_process_tree_above_one():
    assert ow.ProfileCollector(socket_path="/x", jobs=1).config().mode == "cgroup"
    assert ow.ProfileCollector(socket_path="/x", jobs=2).config().mode == "process-tree"


def test_the_summary_counts_the_final_profile_of_each_candidate_once():
    collector = ow.ProfileCollector(socket_path="/x", jobs=1)
    measured = ow.build_profile(_success(), subject={}, wall_seconds=1.0)
    unmeasured = ow.build_profile(_failure(ow.OBSERVER_ERROR), subject={}, wall_seconds=1.0)
    collector.finish("a", unmeasured)
    collector.finish("a", measured)  # a retried candidate counts once, finally
    collector.finish("b", unmeasured)
    summary = collector.summary()
    assert summary["status_counts"] == {"MEASURED": 1, "PARTIAL": 0, "NO_MEASUREMENT": 1}
    assert summary["reason_counts"] == {"OBSERVER_ERROR": 1}
    assert summary["observer"] == {
        "name": "cgprofile", "version": None, "contracts": [3], "modes": ["cgroup"],
    }


def test_window_config_and_result_round_trip_over_the_host_wire():
    config = ow.WindowConfig("/run/o.sock", "process-tree", 500, None)
    assert ow.WindowConfig.from_wire(json.loads(json.dumps(config.to_wire()))) == config
    for result in (_success(), _failure(ow.OBSERVER_REFUSED)):
        again = ow.WindowResult.from_wire(json.loads(json.dumps(result.to_wire())))
        assert again == result
    with pytest.raises(ValueError):
        ow.WindowResult.from_wire({"reason": "NOPE", "detail": None, "reply": None,
                                   "mode": None, "window": None, "interval_ms": 250, "asked": True})
    with pytest.raises(ValueError):
        ow.WindowConfig.from_wire({"socket_path": "/x", "mode": "bogus", "interval_ms": 250,
                                   "skip_reason": None})


# --------------------------------------------------------------------------
# A real process: the window closes while the root is still a zombie.
# --------------------------------------------------------------------------


def _proc_state(pid: int) -> str:
    stat = Path(f"/proc/{pid}/stat").read_text()
    return stat[stat.rindex(")") + 2]


def test_the_process_tree_runner_closes_the_window_before_reaping_the_root(tmp_path: Path):
    """The exact-CPU rule: at `window-close` the root is a zombie, not reaped."""
    from assay.process_tree import ProcessTreeProcessRunner

    seen: dict[str, object] = {}

    def handle(request):
        if request["verb"] == "window-open":
            seen["root_pid"] = request["args"]["root_pid"]
            seen["thread_id"] = request["args"]["thread_id"]
            seen["mode"] = request["args"]["mode"]
            return {**golden("open")["response"], "mode": request["args"]["mode"]}
        seen["state_at_close"] = _proc_state(seen["root_pid"])  # type: ignore[arg-type]
        return golden("close-cgroup")["response"]

    with FakeObserver(tmp_path / "o.sock", handle) as observer:
        collector = ow.ProfileCollector(socket_path=str(observer.path), jobs=1)
        runner = ProcessTreeProcessRunner()
        with ow.activate(collector.config()) as slot:
            completed = runner(
                [sys.executable, "-c", "import time; time.sleep(0.2)"],
                env=dict(os.environ), cwd=tmp_path, timeout=30,
            )
        assert completed.returncode == 0
        assert seen["state_at_close"] == "Z"
        assert seen["mode"] == "cgroup"
        assert seen["thread_id"] == threading.get_native_id()
        assert slot.result is not None and slot.result.reply is not None
        assert [r["verb"] for r in observer.requests] == ["window-open", "window-close"]


def test_the_runner_closes_the_window_when_the_command_times_out(tmp_path: Path):
    from assay.process_tree import ProcessTreeProcessRunner

    with FakeObserver(tmp_path / "o.sock", golden_handler()) as observer:
        collector = ow.ProfileCollector(socket_path=str(observer.path), jobs=1)
        with ow.activate(collector.config()) as slot:
            with pytest.raises(subprocess.TimeoutExpired):
                ProcessTreeProcessRunner()(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    env=dict(os.environ), cwd=tmp_path, timeout=0.5,
                )
        assert [r["verb"] for r in observer.requests] == ["window-open", "window-close"]
        assert slot.result is not None and slot.result.reply is not None


def test_a_refused_open_leaves_the_command_untouched_and_records_the_reason(tmp_path: Path):
    from assay.process_tree import ProcessTreeProcessRunner

    with FakeObserver(tmp_path / "o.sock", lambda request: error_reply("peer-refused")) as observer:
        collector = ow.ProfileCollector(socket_path=str(observer.path), jobs=1)
        with ow.activate(collector.config()) as slot:
            completed = ProcessTreeProcessRunner()(
                [sys.executable, "-c", "print('ok')"], env=dict(os.environ), cwd=tmp_path, timeout=30,
            )
        assert (completed.returncode, completed.stdout.strip()) == (0, "ok")
        assert slot.result is not None and slot.result.reason == ow.OBSERVER_REFUSED
        assert [r["verb"] for r in observer.requests] == ["window-open"]  # nothing to close


def test_the_candidate_root_is_a_child_subreaper_when_a_window_is_requested(tmp_path: Path):
    """PR_GET_CHILD_SUBREAPER reads 1 in the root: orphans stay in its tree."""
    from assay.process_tree import ProcessTreeProcessRunner

    probe = (
        "import ctypes;v=ctypes.c_int();"
        "ctypes.CDLL(None).prctl(37,ctypes.byref(v),0,0,0);print(v.value)"
    )
    with FakeObserver(tmp_path / "o.sock", golden_handler()) as observer:
        collector = ow.ProfileCollector(socket_path=str(observer.path), jobs=1)
        with ow.activate(collector.config()):
            window = ProcessTreeProcessRunner()(
                [sys.executable, "-c", probe], env=dict(os.environ), cwd=tmp_path, timeout=30
            )
    plain = ProcessTreeProcessRunner()(
        [sys.executable, "-c", probe], env=dict(os.environ), cwd=tmp_path, timeout=30
    )
    assert window.stdout.strip() == "1"
    assert plain.stdout.strip() == "0"


# --------------------------------------------------------------------------
# The one-source rule.
# --------------------------------------------------------------------------

_FORBIDDEN = re.compile(
    r"statm|smaps|memory\.peak|memory\.swap\.peak|io\.stat|/proc/[^\"' ]*/io\b|/ ?\"io\"\)"
)


def test_no_assay_module_except_the_observer_client_reads_rss_or_io_counters():
    """Liveness reads `/proc/<pid>/stat` for CPU progress only; every other
    per-process or cgroup resource number belongs to the cgprofile observer."""
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "observer_windows.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            if _FORBIDDEN.search(line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert offenders == []


def test_the_source_check_would_catch_a_reader(tmp_path: Path):
    for text in ('open("/proc/1/statm")', 'p = "/proc/%d/io" % pid', 'f = "memory.peak"',
                 'g = cgroup / "io.stat"', 'x = proc_root / str(pid) / "smaps_rollup"'):
        assert _FORBIDDEN.search(text), text


def test_the_removed_resource_readers_and_keys_are_gone():
    from assay import liveness, mutation

    for name in ("process_rss_bytes", "tree_sample", "TreeSample", "_pid_io_write_bytes", "_ResourceTally"):
        assert not hasattr(liveness, name), name
    assert not hasattr(mutation, "_measured_resources")
    runner_fields = set(mutation._MutantRun.__dataclass_fields__)
    assert not runner_fields & {"cpu_seconds", "peak_rss_bytes", "write_bytes", "judge_rss_bytes"}
