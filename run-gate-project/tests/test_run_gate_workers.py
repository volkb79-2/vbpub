"""RG-146 / RG-147: pytest-xdist workers for command lanes, scaling mode.

Pure-function tests pin the sizing formula term by term; the end-to-end tests
run real host-mode lanes whose argv is a tiny fake pytest, so the substituted
argv, the exported env, the junit evidence, the serial recheck and the
scaling tables are exercised through `run_gate.main`.
"""

import json
import os
import re
import socketserver
import subprocess
import sys
import tempfile
import textwrap
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import _testdeps
from test_run_gate import (RUN_GATE_DIR, commit_all, docker_execs,
                           docker_runs, EXEC_LANE, fake_docker, make_project, make_repo,
                           read_store, run_gate, run_tool, shim_dir_of)

# The suite's own autouse environment fixtures (ambient slice, profiling off,
# no host profiler socket) apply to this module's lane runs too.
from test_run_gate import (  # noqa: E402,F401
    ambient_cgroup, isolate_host_profiler_socket, profiling_off_by_default)

sys.path.insert(0, str(RUN_GATE_DIR))
import run_gate_profile  # noqa: E402

MIB = 1024 * 1024


def lane(**extra):
    table = {"kind": "command", "environment": "runner",
             "argv": ["pytest", "-n", "{workers}"], "workers": "auto",
             "xdist_safe": True}
    table.update(extra)
    return table


def validate(table, name="unit"):
    run_gate._validate_lane(name, table, "fixture")


def profile(kind, subject_id, rss, cpu, wall, write=0, tests_collected=None):
    subject = {"kind": kind, "id": subject_id}
    if tests_collected is not None:
        subject["tests_collected"] = tests_collected
    return {"schema": "resource-profile/1", "subject": subject,
            "wall_seconds": wall, "status": "MEASURED",
            "metrics": {"rss_peak_bytes": rss, "cpu_seconds": cpu,
                        "io_read_bytes": 0, "io_write_bytes": write}}


def entry(rss_p95=400 * MIB, cpu=8.0, wall=10.0, write=0, controller=100 * MIB,
          tests=200, scaling=False, outcome="pass", contaminated=False):
    return {
        "history_eligible": True, "outcome": outcome,
        "workers": {"scaling": scaling},
        "per_worker": {
            "rss_peak_bytes": {"p50": rss_p95, "p95": rss_p95, "max": rss_p95,
                               "n": 2},
            "cpu_seconds": {"p50": cpu, "p95": cpu, "max": cpu, "n": 2},
            "wall_seconds": {"p50": wall, "p95": wall, "max": wall, "n": 2},
            "io_write_bytes": {"p50": write, "p95": write, "max": write,
                               "n": 2}},
        "controller": {"rss_peak_bytes": controller},
        "tests_collected": tests,
        **({"contamination": {"flagged": True}} if contaminated else {}),
    }


class TestWorkersLaneKeys:
    def test_valid_declarations_load(self):
        validate(lane())
        validate(lane(workers=1, xdist_safe=False))
        validate(lane(workers=3, workers_max=4))

    @pytest.mark.parametrize("table,match", [
        ({"kind": "assay", "workers": 1}, "only for command lanes"),
        ({"workers": 0}, "positive integer or 'auto'"),
        ({"workers": True}, "positive integer or 'auto'"),
        ({"workers_max": 0}, "'workers_max' must be a positive integer"),
        ({"xdist_safe": "yes"}, "'xdist_safe' must be a boolean"),
        ({"argv": ["pytest"]}, "must carry the declared"),
        ({"xdist_safe": False}, "requires xdist_safe = true"),
        ({"workers": 9}, "exceeds workers_max"),
        ({"workers": 5, "workers_max": 4}, "exceeds workers_max"),
    ])
    def test_invalid_declarations_refuse(self, table, match):
        base = lane()
        if table.get("kind") == "assay":
            base = {"kind": "assay", "assay_lane": "r1"}
        with pytest.raises(run_gate.GateError, match=match):
            validate({**base, **table})

    def test_companion_keys_require_workers(self):
        with pytest.raises(run_gate.GateError, match="require 'workers'"):
            validate({"kind": "command", "environment": "runner",
                      "argv": ["pytest"], "workers_max": 2})

    def test_token_without_workers_refuses(self):
        with pytest.raises(run_gate.GateError, match="declares no 'workers'"):
            validate({"kind": "command", "environment": "runner",
                      "argv": ["pytest", "-n", "{workers}"]})

    def test_lane_without_workers_is_unchanged(self):
        validate({"kind": "command", "environment": "runner",
                  "argv": ["pytest"]})

    def test_worker_ram_is_lane_only_and_positive(self):
        run_gate._validate_resource_table(
            {"worker_ram": "512m"}, "fixture", "lane")
        with pytest.raises(run_gate.GateError, match="lane-only"):
            run_gate._validate_resource_table(
                {"worker_ram": "512m"}, "fixture", "project")
        for bad in ("0m", "lots", 5):
            with pytest.raises(run_gate.GateError, match="worker_ram"):
                run_gate._validate_resource_table(
                    {"worker_ram": bad}, "fixture", "lane")


class TestWorkersCli:
    def test_workers_request(self):
        assert run_gate.parse_workers_cli(None) is None
        assert run_gate.parse_workers_cli("auto") == "auto"
        assert run_gate.parse_workers_cli("3") == 3
        for bad in ("0", "-1", "x", "1.5"):
            with pytest.raises(run_gate.GateError, match="--workers"):
                run_gate.parse_workers_cli(bad)

    def test_scaling_counts(self):
        assert run_gate.parse_scaling_cli(None) is None
        assert run_gate.parse_scaling_cli("1,2,4") == [1, 2, 4]
        for bad in ("1,x", "2,3", "1,3,2", "1,1", ""):
            with pytest.raises(run_gate.GateError, match="--scaling"):
                run_gate.parse_scaling_cli(bad)

    def test_repeat_defaults_explicit_and_star(self):
        counts = [1, 2, 3]
        assert run_gate.parse_repeat_cli(None, counts) == {1: 1, 2: 1, 3: 1}
        assert run_gate.parse_repeat_cli("1:3,*:2", counts) == {
            1: 3, 2: 2, 3: 2}
        assert run_gate.parse_repeat_cli("2:4", counts) == {1: 1, 2: 4, 3: 1}
        with pytest.raises(run_gate.GateError, match="1:3,"):
            run_gate.parse_repeat_cli("x", counts)
        with pytest.raises(run_gate.GateError, match="does not run"):
            run_gate.parse_repeat_cli("9:2", counts)

    def test_strip_cli_options_drops_only_worker_modes(self):
        assert run_gate.strip_cli_options(
            ["unit", "--scaling", "1,2", "--repeat=1:2", "--xdist-parity",
             "--workers", "3", "--worktree", "w", "--", "--workers", "9"]
        ) == ["unit", "--worktree", "w", "--", "--workers", "9"]


class TestStatistics:
    def test_nearest_rank_and_distribution(self):
        assert run_gate.worker_nearest_rank([5, 1, 3], 0.5) == 3
        assert run_gate.worker_nearest_rank([1, 2, 3, 4], 0.95) == 4
        assert run_gate.worker_distribution([]) is None
        assert run_gate.worker_distribution([True, None]) is None
        assert run_gate.worker_distribution([1, 3, None, 2]) == {
            "p50": 2, "p95": 3, "max": 3, "n": 3}

    def test_aggregate_folds_workers_and_controller(self):
        folded = run_gate.aggregate_worker_profiles([
            profile("xdist-worker", "gw0", 400, 8.0, 10.0, write=10),
            profile("xdist-worker", "gw1", 500, 9.0, 11.0, write=30),
            profile("controller", "controller", 100, 1.0, 12.0,
                    tests_collected=240),
            {**profile("xdist-worker", "gw2", 9999, 1, 1),
             "status": "NO_MEASUREMENT"},
            {"subject": "bad"},
            {"subject": {"kind": "xdist-worker"}, "status": "MEASURED",
             "metrics": "bad"},
        ])
        assert folded["per_worker"]["rss_peak_bytes"] == {
            "p50": 400, "p95": 500, "max": 500, "n": 2}
        assert folded["per_worker"]["io_write_bytes"]["p95"] == 30
        assert folded["controller"] == {"rss_peak_bytes": 100,
                                        "cpu_seconds": 1.0}
        assert folded["tests_collected"] == 240

    def test_aggregate_ignores_unknown_kinds_and_partial_numbers(self):
        partial = profile("xdist-worker", "gw0", 4, 1.0, 2.0)
        del partial["metrics"]["io_read_bytes"]
        folded = run_gate.aggregate_worker_profiles([
            partial, profile("other", "x", 9, 9, 9),
            profile("controller", "controller", 1, 1, 1,
                    tests_collected=None)])
        assert "io_read_bytes" not in folded["per_worker"]
        assert folded["per_worker"]["rss_peak_bytes"]["max"] == 4
        assert folded["tests_collected"] is None

    def test_step_without_junit_has_no_outcomes(self, tmp_path):
        result = run_gate.LaneResult("ERROR", None, "boom")
        step = run_gate.run_workers_step(
            None, [], 2, lambda *_a, **_k: result, scaling=True)
        assert step["outcomes"] is None and not step["contaminated"]

    def test_aggregate_without_workers_or_controller(self):
        assert run_gate.aggregate_worker_profiles([]) is None
        only = run_gate.aggregate_worker_profiles(
            [profile("xdist-worker", "gw0", 4, 1.0, 2.0)])
        assert only["controller"] is None and only["tests_collected"] is None

    def test_read_worker_profiles_skips_foreign_and_broken_files(self, tmp_path):
        (tmp_path / "gw0.json").write_text(json.dumps(
            profile("xdist-worker", "gw0", 1, 1, 1)))
        (tmp_path / "broken.json").write_text("{")
        (tmp_path / "other.json").write_text(json.dumps({"schema": "x"}))
        (tmp_path / "list.json").write_text("[]")
        found = run_gate.read_worker_profiles(tmp_path)
        assert [item["subject"]["id"] for item in found] == ["gw0"]

    def test_junit_outcomes(self, tmp_path):
        path = tmp_path / "junit.xml"
        path.write_text(textwrap.dedent("""\
            <testsuites><testsuite>
              <testcase classname="a" name="ok"/>
              <testcase classname="a" name="bad"><failure/></testcase>
              <testcase classname="a" name="err"><error/></testcase>
              <testcase classname="a" name="skip"><skipped/></testcase>
              <testcase classname="a" name="noise"><system-out/></testcase>
            </testsuite></testsuites>"""))
        assert run_gate.junit_outcomes(path) == {
            "a::ok": "passed", "a::bad": "failed", "a::err": "error",
            "a::skip": "skipped", "a::noise": "passed"}
        assert run_gate.junit_outcomes(tmp_path / "missing.xml") is None
        path.write_text("<not-xml")
        assert run_gate.junit_outcomes(path) is None


class TestWorkersBasis:
    def test_clean_seam(self):
        assert run_gate.workers_entry_is_clean({})
        assert run_gate.workers_entry_is_clean({"contamination": "x"})
        assert run_gate.workers_entry_is_clean(
            {"contamination": {"flagged": False}})
        assert not run_gate.workers_entry_is_clean(
            {"contamination": {"flagged": True, "reasons": ["host"]}})

    def test_no_basis_below_two_clean_passing_runs(self):
        assert run_gate.workers_basis_from_history([]) is None
        assert run_gate.workers_basis_from_history([entry()]) is None
        assert run_gate.workers_basis_from_history(
            [entry(), entry(contaminated=True), entry(outcome="fail"),
             {**entry(), "history_eligible": False}, {"outcome": "pass"}]
        ) is None

    def test_history_basis_is_conservative_over_recent_runs(self):
        basis = run_gate.workers_basis_from_history([
            entry(rss_p95=300 * MIB, cpu=5, wall=10, write=100, controller=50,
                  tests=100),
            entry(rss_p95=500 * MIB, cpu=9, wall=10, write=300, controller=90,
                  tests=240),
        ])
        assert basis["source"] == "history" and basis["runs"] == 2
        assert basis["worker_rss_p95_bytes"] == 500 * MIB
        assert basis["controller_rss_p95_bytes"] == 90
        assert basis["worker_core_fraction_p50"] == 0.5
        assert basis["worker_write_bps_p95"] == 30.0
        assert basis["tests_collected"] == 240

    def test_latest_clean_scaling_run_stands_alone(self):
        basis = run_gate.workers_basis_from_history([
            entry(rss_p95=900 * MIB), entry(rss_p95=800 * MIB),
            entry(rss_p95=200 * MIB, scaling=True),
            entry(rss_p95=700 * MIB, scaling=True, contaminated=True)])
        assert basis["source"] == "scaling" and basis["runs"] == 1
        assert basis["worker_rss_p95_bytes"] == 200 * MIB

    def test_sparse_entries_leave_optional_terms_unknown(self):
        sparse = entry()
        del sparse["per_worker"]["wall_seconds"]
        del sparse["controller"]
        del sparse["tests_collected"]
        basis = run_gate.workers_basis_from_history([sparse, sparse])
        assert basis["worker_core_fraction_p50"] is None
        assert basis["worker_write_bps_p95"] is None
        assert basis["controller_rss_p95_bytes"] == 0
        assert basis["tests_collected"] is None
        no_cpu = entry()
        del no_cpu["per_worker"]["cpu_seconds"]
        del no_cpu["per_worker"]["io_write_bytes"]
        basis = run_gate.workers_basis_from_history([no_cpu, no_cpu])
        assert basis["worker_core_fraction_p50"] is None
        assert basis["worker_write_bps_p95"] is None


class TestSelectWorkers:
    BASIS = {"worker_rss_p95_bytes": 400 * MIB,
             "controller_rss_p95_bytes": 100 * MIB,
             "worker_core_fraction_p50": 0.8,
             "worker_write_bps_p95": 10 * MIB, "tests_collected": 1000}

    def select(self, **kwargs):
        arguments = dict(workers_max=16, cpu_cap=8.0,
                         memory_cap_bytes=16 * 1024 * MIB, basis=self.BASIS)
        arguments.update(kwargs)
        return run_gate.select_workers("auto", **arguments)

    def test_each_term_can_be_the_limit(self):
        assert self.select(workers_max=3)["limit"] == "workers_max"
        assert self.select(workers_max=3)["workers"] == 3
        picked = self.select(cpu_cap=2.0)
        assert (picked["workers"], picked["limit"]) == (2, "cpu")
        picked = self.select(memory_cap_bytes=1200 * MIB)
        # (1200 * .75 - 100) / 400 = 2.0
        assert (picked["workers"], picked["limit"]) == (2, "memory")
        picked = self.select(io_write_cap_bps=25 * MIB)
        assert (picked["workers"], picked["limit"]) == (2, "io")
        picked = self.select(basis={**self.BASIS, "tests_collected": 59})
        assert (picked["workers"], picked["limit"]) == (2, "tests")

    def test_cores_are_floored_at_a_quarter_core_per_worker(self):
        picked = self.select(basis={**self.BASIS,
                                    "worker_core_fraction_p50": 0.01},
                             workers_max=99, cpu_cap=2.0)
        assert picked["terms"]["cpu"] == 8

    def test_never_below_one(self):
        picked = self.select(memory_cap_bytes=10 * MIB)
        assert picked["workers"] == 1 and picked["limit"] == "memory"

    def test_no_clean_profile_or_no_caps_select_one_and_say_why(self):
        none = self.select(basis=None)
        assert none["workers"] == 1 and none["limit"] == "no-clean-profile"
        assert "no clean per-worker profile" in none["reason"]
        for kwargs in ({"cpu_cap": None}, {"memory_cap_bytes": None}):
            uncapped = self.select(**kwargs)
            assert uncapped["workers"] == 1 and uncapped["limit"] == "no-caps"

    def test_unknown_optional_terms_do_not_limit(self):
        picked = self.select(basis={"worker_rss_p95_bytes": 400 * MIB})
        assert picked["terms"]["io"] is None
        assert picked["terms"]["tests"] is None

    def test_ram_default_is_used_without_a_measured_p95(self):
        picked = self.select(basis={"tests_collected": 1000},
                             ram_default_bytes=2048 * MIB,
                             memory_cap_bytes=8192 * MIB)
        assert picked["worker_rss_bytes"] == 2048 * MIB
        assert picked["worker_rss_source"] == "declared or default"
        assert picked["terms"]["memory"] == 3

    def test_explicit_is_honoured_and_over_capacity_is_listed(self):
        picked = run_gate.select_workers(
            3, workers_max=4, cpu_cap=0.5, memory_cap_bytes=16 * 1024 * MIB)
        assert picked["workers"] == 3 and picked["mode"] == "explicit"
        assert picked["over_capacity"] == ["cpu"]
        with pytest.raises(run_gate.GateError, match="exceeds workers_max"):
            run_gate.select_workers(5, workers_max=4)
        with pytest.raises(run_gate.GateError, match="invalid workers_max"):
            run_gate.select_workers(1, workers_max=0)


class TestParityProof:
    def test_digest_ignores_tuning_keys_only(self):
        base = lane()
        assert run_gate.lane_config_digest(base) == run_gate.lane_config_digest(
            {**base, "workers": 2, "workers_max": 3, "budget": "5m",
             "_workers_selection": {}})
        assert run_gate.lane_config_digest(base) != run_gate.lane_config_digest(
            {**base, "argv": ["pytest", "{workers}", "-x"]})

    def test_store_load_and_staleness(self, tmp_path):
        digest = run_gate.lane_config_digest(lane())
        assert run_gate.load_xdist_parity(tmp_path, "unit", digest) == "absent"
        run_gate.store_xdist_parity(tmp_path, "unit", digest, 3, 40, "abc")
        assert run_gate.load_xdist_parity(tmp_path, "unit", digest) == "proven"
        assert run_gate.load_xdist_parity(tmp_path, "unit", "other") == "stale"
        path = run_gate.xdist_parity_path(tmp_path, "unit")
        path.write_text("[]")
        assert run_gate.load_xdist_parity(tmp_path, "unit", digest) == "absent"
        path.write_text(json.dumps({"identical": False}))
        assert run_gate.load_xdist_parity(tmp_path, "unit", digest) == "absent"


EPHEMERAL = {"mode": "ephemeral"}


class TestWorkersCapsAndConfigure:
    def test_ephemeral_lane_needs_memory_and_cpus(self):
        resources = {"memory": "4g", "cpus": "3"}
        assert run_gate.workers_caps(
            lane(resources=resources), EPHEMERAL, "unit") == (
                3.0, 4 * 1024 * MIB, "enforced lane caps")
        # cpus may come from the environment, memory from the legacy key
        assert run_gate.workers_caps(
            lane(memory="1g"), {"mode": "ephemeral",
                                "resources": {"cpus": "2"}}, "unit")[:2] == (
                2.0, 1024 * MIB)
        for bad in (lane(), lane(resources={"memory": "1g"}),
                    lane(resources={"cpus": "1"})):
            with pytest.raises(run_gate.GateError,
                               match="requires enforced"):
                run_gate.workers_caps(bad, EPHEMERAL, "unit")

    def test_bare_host_needs_cgprofile_placement(self):
        with pytest.raises(run_gate.GateError, match="cgprofile"):
            run_gate.workers_caps(lane(), {"mode": "host"}, "unit")
        cpu, memory, source = run_gate.workers_caps(
            lane(resource_observer="cgprofile"), {}, "unit")
        assert (cpu, memory) == (None, None) and "cgprofile" in source

    def test_absent_workers_key(self, tmp_path):
        plain = {"kind": "command", "argv": ["pytest"]}
        assert run_gate.configure_workers(
            plain, "unit", None, EPHEMERAL, tmp_path) is None
        assert "_workers_selection" not in plain
        with pytest.raises(run_gate.GateError, match="does not"):
            run_gate.configure_workers(plain, "unit", 2, EPHEMERAL, tmp_path)

    def test_parallel_request_requires_xdist_safe(self, tmp_path):
        unsafe = lane(workers=1, xdist_safe=False,
                      resources={"memory": "1g", "cpus": "2"})
        with pytest.raises(run_gate.GateError, match="xdist_safe"):
            run_gate.configure_workers(unsafe, "unit", 2, EPHEMERAL, tmp_path)

    def test_baseline_needs_no_caps_and_sets_env(self, tmp_path):
        table = lane(workers=1, xdist_safe=False)
        selection = run_gate.configure_workers(
            table, "unit", None, EPHEMERAL, tmp_path)
        assert selection["workers"] == 1 and selection["parity"] == "not-needed"
        assert table["_workers_env"] == {"RUN_GATE_WORKERS": "1"}

    def test_auto_without_history_stays_at_one(self, tmp_path):
        table = lane(resources={"memory": "4g", "cpus": "3"})
        selection = run_gate.configure_workers(
            table, "unit", None, EPHEMERAL, tmp_path)
        assert selection["workers"] == 1
        assert selection["limit"] == "no-clean-profile"
        assert selection["parity"] == "not-needed"

    def test_auto_sizes_from_history_and_reads_parity_and_worker_ram(
            self, tmp_path):
        store = {"schema": 2, "lanes": {"unit": {"history": [
            entry(), entry()]}}}
        path = run_gate.history_store_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(store))
        table = lane(resources={"memory": "4g", "cpus": "3",
                                "worker_ram": "512m"}, workers_max=6)
        run_gate.store_xdist_parity(
            tmp_path, "unit", run_gate.lane_config_digest(table), 3, 9, "c")
        selection = run_gate.configure_workers(
            table, "unit", None, EPHEMERAL, tmp_path)
        assert selection["workers"] > 1
        assert selection["parity"] == "proven"
        assert selection["source"] == "toml"
        assert selection["caps_source"] == "enforced lane caps"

    def test_declared_ram_feeds_explicit_over_capacity(self, tmp_path):
        table = lane(workers=2, resources={"memory": "1g", "cpus": "1",
                                           "worker_ram": "2g"})
        selection = run_gate.configure_workers(
            table, "unit", None, EPHEMERAL, tmp_path)
        assert selection["over_capacity"] == ["memory"]

    def test_cli_and_internal_requests_override_the_lane(self, tmp_path):
        resources = {"memory": "8g", "cpus": "4"}
        table = lane(workers=1, resources=resources)
        assert run_gate.configure_workers(
            table, "unit", 3, EPHEMERAL, tmp_path)["source"] == "cli"
        table = lane(workers=1, resources=resources)
        internal = run_gate.configure_workers(
            table, "unit", 3, EPHEMERAL, tmp_path,
            {"workers": 2, "scaling": True, "lf": True})
        assert internal["workers"] == 2 and internal["source"] == "internal"
        assert internal["scaling"] and internal["rerun"]

    def test_disclosure_lines(self, capsys):
        run_gate.disclose_workers("unit", None)
        assert capsys.readouterr().out == ""
        run_gate.disclose_workers("unit", {
            "workers": 3, "mode": "explicit", "limit": None, "reason": None,
            "parity": "absent", "over_capacity": ["cpu"]})
        out = capsys.readouterr().out
        assert "N=3 mode=explicit limit=requested parity=absent" in out
        assert "xdist-parity" in out and "exceeds the sizing term(s): cpu" in out
        run_gate.disclose_workers("unit", {
            "workers": 1, "mode": "auto", "limit": "no-clean-profile",
            "reason": "no clean per-worker profile yet", "parity": "not-needed",
            "over_capacity": []})
        out = capsys.readouterr().out
        assert "(no clean per-worker profile yet)" in out
        assert "WARNING" not in out


class TestWorkersRunPlumbing:
    SELECTION = {"workers": 2, "mode": "explicit", "requested": 2,
                 "source": "cli", "limit": None, "reason": None,
                 "workers_max": 4, "terms": {}, "worker_rss_bytes": 1,
                 "worker_rss_source": "x", "cpu_cap": 2.0,
                 "memory_cap_bytes": 3, "caps_source": "y", "parity": "proven",
                 "scaling": False, "rerun": False, "over_capacity": [],
                 "basis": None}

    def table(self):
        return {"argv": ["pytest", "-n", "{workers}", "{worktree}"],
                "_workers_selection": dict(self.SELECTION),
                "_workers_env": {"RUN_GATE_WORKERS": "2"}}

    def test_argv_substitution_and_env(self):
        table = self.table()
        assert run_gate.lane_command_argv(table, Path("/t")) == [
            "pytest", "-n", "2", "/t"]
        assert run_gate.lane_workers_env(table) == {"RUN_GATE_WORKERS": "2"}
        assert run_gate.lane_workers_env({}) == {}
        assert run_gate.lane_command_argv(
            {"argv": ["a", "{workers}"]}, Path("/t")) == ["a", "{workers}"]

    def test_prepare_without_selection_is_a_no_op(self, tmp_path):
        record = {"run_id": "r1"}
        run_gate.prepare_workers_run({}, record, tmp_path)
        assert record == {"run_id": "r1"}

    def test_prepare_builds_profile_dir_junit_and_lf(self, tmp_path,
                                                     monkeypatch):
        monkeypatch.setenv("PYTEST_ADDOPTS", "-q")
        table, record = self.table(), {"run_id": "r1"}
        run_gate.prepare_workers_run(
            table, record, tmp_path, {"junit": True, "lf": True})
        env = table["_workers_env"]
        directory = Path(env["RUN_GATE_PROFILE_DIR"])
        assert directory.is_dir() and directory.name == "r1"
        assert env["PYTEST_ADDOPTS"] == f"-q --junitxml={directory}/junit.xml --lf"
        assert record["_workers_junit"] == str(directory / "junit.xml")
        assert record["_workers_profile_dir"] == str(directory)
        assert record["workers"]["workers"] == 2
        assert directory.stat().st_mode & 0o777 == 0o777   # H1: lane uid

    def test_prepare_survives_a_failed_chmod(self, tmp_path, monkeypatch):
        def refuse(*_args, **_kwargs):
            raise PermissionError("not ours")
        monkeypatch.setattr(run_gate.os, "chmod", refuse)
        table, record = self.table(), {"run_id": "r3"}
        run_gate.prepare_workers_run(table, record, tmp_path)
        assert Path(table["_workers_env"]["RUN_GATE_PROFILE_DIR"]).is_dir()

    def test_prepare_leaves_pytest_addopts_alone_when_nothing_to_add(
            self, tmp_path, monkeypatch):
        monkeypatch.setenv("PYTEST_ADDOPTS", "-q")
        table, record = self.table(), {"run_id": "r2"}
        run_gate.prepare_workers_run(table, record, tmp_path)
        assert "PYTEST_ADDOPTS" not in table["_workers_env"]
        assert "_workers_junit" not in record

    def test_attach_folds_profiles_and_drops_the_directory(self, tmp_path):
        directory = tmp_path / "run"
        directory.mkdir()
        (directory / "gw0.json").write_text(json.dumps(
            profile("xdist-worker", "gw0", 4, 1.0, 2.0)))
        (directory / "controller.json").write_text(json.dumps(
            profile("controller", "controller", 2, 0.5, 3.0,
                    tests_collected=7)))
        record = {"_workers_profile_dir": str(directory), "workers": {}}
        run_gate.attach_workers_profiles(record)
        assert record["workers_profile"] == {"status": "MEASURED"}
        assert record["per_worker"]["rss_peak_bytes"]["p95"] == 4
        assert record["tests_collected"] == 7
        assert "_workers_profile_dir" not in record
        assert not directory.exists()

    def test_attach_without_profiles_is_no_measurement_and_keeps_junit(
            self, tmp_path):
        directory = tmp_path / "run"
        directory.mkdir()
        junit = directory / "junit.xml"
        junit.write_text('<testsuite><testcase classname="a" name="b"/>'
                         "</testsuite>")
        record = {"_workers_profile_dir": str(directory),
                  "_workers_junit": str(junit), "workers": {}}
        run_gate.attach_workers_profiles(record)
        assert record["workers_profile"]["status"] == "NO_MEASUREMENT"
        assert "per_worker" not in record
        assert record["tests_collected"] == 1
        assert record["workers"]["junit"] == str(junit)
        assert junit.exists()

    def test_attach_without_workers_run_is_a_no_op(self):
        record = {}
        run_gate.attach_workers_profiles(record)
        assert record == {}

    def test_failed_tests_come_from_the_summary_lines(self, tmp_path):
        log = tmp_path / "lane.log"
        log.write_text("FAILED tests/a.py::t1 - x\nok\nFAILED tests/a.py::t1\n"
                       "FAILED tests/b.py::t2[p] - y\n")
        assert run_gate.failed_tests_from_log(str(log)) == [
            "tests/a.py::t1", "tests/b.py::t2[p]"]
        assert run_gate.failed_tests_from_log(None) == []
        assert run_gate.failed_tests_from_log(str(tmp_path / "none")) == []


class TestIsolationRecheck:
    def recheck(self, tmp_path, *, workers=2, verdict="FAIL", exit_code=1,
                serial="PASS", options=None, record=True):
        log = tmp_path / "lane.log"
        log.write_text("FAILED tests/t.py::test_shared - boom\n")
        discarded = tmp_path / "discarded"
        discarded.mkdir()
        calls = []

        def rerun():
            calls.append(1)
            return run_gate.LaneResult(
                serial, 0, _record={"_workers_profile_dir": str(discarded)})

        result = run_gate.xdist_isolation_recheck(
            run_gate.LaneResult(verdict, exit_code, None, str(log)),
            {"_workers_selection": {"workers": workers}},
            {"log_path": str(log)} if record else None, options or {}, rerun)
        return result, calls, discarded

    def test_serial_pass_is_inconclusive_and_names_the_tests(
            self, tmp_path, capsys):
        result, calls, discarded = self.recheck(tmp_path)
        assert (result.verdict, result.reason) == ("FAIL", "XDIST_ISOLATION")
        assert result.gate_exit_code == 1 and calls
        assert not discarded.exists()
        assert "tests/t.py::test_shared" in capsys.readouterr().out

    def test_a_failure_that_also_fails_serially_is_a_real_fail(self, tmp_path):
        result, calls, _ = self.recheck(tmp_path, serial="FAIL")
        assert result.verdict == "FAIL" and calls

    def test_unidentifiable_failures_are_still_named(self, tmp_path, capsys):
        log = tmp_path / "empty.log"
        log.write_text("")
        record = {"log_path": str(log)}
        result = run_gate.xdist_isolation_recheck(
            run_gate.LaneResult("FAIL", 1, None, str(log)),
            {"_workers_selection": {"workers": 3}}, record, {},
            lambda: run_gate.LaneResult("PASS", 0))
        assert result.reason == "XDIST_ISOLATION"
        assert "not identifiable" in capsys.readouterr().out
        assert record["xdist_recheck"]["failed_tests"] == []

    @pytest.mark.parametrize("kwargs", [
        {"workers": 1}, {"verdict": "PASS", "exit_code": 0},
        {"exit_code": 2}, {"options": {"no_recheck": True}},
        {"record": False}])
    def test_no_recheck_outside_its_conditions(self, tmp_path, kwargs):
        result, calls, _ = self.recheck(tmp_path, **kwargs)
        assert not calls and result.reason is None

    def test_no_selection_means_no_recheck(self):
        original = run_gate.LaneResult("FAIL", 1)
        assert run_gate.xdist_isolation_recheck(
            original, {}, {}, {}, lambda: pytest.fail("rerun")) is original


class TestScalingTables:
    def step(self, wall, rss, *, outcomes=None, contaminated=False, peak=None,
             stall=None):
        record = {"duration_seconds": wall,
                  "per_worker": {"rss_peak_bytes": {
                      "p50": rss, "p95": rss * 2, "max": rss * 3, "n": 2},
                      "cpu_seconds": {"p50": 4.0}, "io_write_bytes": {
                          "p95": 2 * MIB}},
                  "resources": {"memory": {"peak_bytes": peak},
                                "host": {"memory_full_stall_seconds": stall}}}
        return {"record": record, "outcomes": outcomes,
                "contaminated": contaminated}

    def test_record_contaminated_seam(self):
        assert not run_gate.record_contaminated(None)
        assert not run_gate.record_contaminated({})
        assert run_gate.record_contaminated(
            {"contamination": {"flagged": True}})

    def test_rows_equal_the_reference_computed_from_the_records(self):
        same = {"a": "passed", "b": "failed"}
        baseline = [self.step(100.0, 400 * MIB, outcomes=same,
                              peak=900 * MIB, stall=0.0)]
        base_row = run_gate.scaling_row(1, baseline, None, None)
        assert base_row["speedup"] is None and base_row["identical"]
        steps = [self.step(40.0, 420 * MIB, outcomes=same, peak=1200 * MIB),
                 self.step(60.0, 500 * MIB, outcomes=same, peak=1100 * MIB,
                           contaminated=True)]
        row = run_gate.scaling_row(2, steps, 100.0, same)
        assert row["wall_p50"] == 40.0
        assert row["speedup"] == 2.5 and row["efficiency"] == 1.25
        assert row["lane_memory_peak_bytes"] == 1200 * MIB
        assert row["rss_p50"] == 420 * MIB and row["rss_p95"] == 1000 * MIB
        assert row["rss_max"] == 1500 * MIB
        assert row["cpu_p50"] == 4.0 and row["write_p95"] == 2 * MIB
        assert row["stall_seconds"] is None
        assert row["contaminated"] and row["identical"]
        lines = run_gate.format_scaling_table([base_row, row])
        assert lines[0].startswith("N | reps | wall_p50_s | speedup | eff")
        assert lines[1].split(" | ")[:5] == ["1", "1", "100.0", "-", "-"]
        assert lines[2].split(" | ")[:5] == ["2", "2", "40.0", "2.50", "1.25"]
        assert lines[2].endswith("| yes | yes")

    def test_outcome_mismatch_is_reported_per_test(self):
        baseline = {"a": "passed", "b": "passed"}
        row = run_gate.scaling_row(
            2, [self.step(50.0, MIB, outcomes={"a": "passed", "b": "failed",
                                                "c": "passed"})],
            100.0, baseline)
        assert not row["identical"] and row["mismatches"] == ["b", "c"]
        assert run_gate.format_scaling_table([row])[1].endswith("| NO")

    def test_missing_numbers_render_as_dashes(self):
        row = run_gate.scaling_row(
            2, [{"record": {}, "outcomes": None, "contaminated": False}],
            None, {})
        assert row["wall_p50"] is None and row["efficiency"] is None
        cells = run_gate.format_scaling_table([row])[1].split(" | ")
        assert cells[2] == "-" and cells[5] == "-"


FAKE_SUITE = '''\
import os, shlex, sys
from pathlib import Path

workers = int(sys.argv[1])
assert os.environ["RUN_GATE_WORKERS"] == str(workers)
addopts = shlex.split(os.environ.get("PYTEST_ADDOPTS", ""))
junit = next((a.split("=", 1)[1] for a in addopts
              if a.startswith("--junitxml=")), None)
rerun = "--lf" in addopts
mode = os.environ.get("FAKE_MODE", "ok")
outcomes = {"a": "passed", "b": "passed"}
if mode == "isolation" and workers > 1 and not rerun:
    outcomes["b"] = "failed"
if mode == "broken":
    outcomes["b"] = "failed"
if mode == "mismatch" and workers > 1:
    outcomes["b"] = "skipped"
if junit:
    tag = {"failed": "<failure/>", "skipped": "<skipped/>", "passed": ""}
    cases = "".join(
        '<testcase classname="t" name="%s">%s</testcase>' % (n, tag[o])
        for n, o in outcomes.items())
    Path(junit).write_text("<testsuite>%s</testsuite>" % cases)
if "failed" in outcomes.values():
    print("FAILED t.py::b - boom")
    sys.exit(1)
'''


class TestWorkersEndToEnd:
    """Host-mode lanes run the fake suite for real through `main`."""

    CONFIG = """\
        schema_version = 1
        [environments.local]
        mode = "host"
        [lanes.suite]
        kind = "command"
        environment = "local"
        argv = ["python3", "{worktree}/proj/fake_suite.py", "{workers}"]
        workers = 1
        workers_max = 4
        xdist_safe = true
        clean_tree = false
        profile = false
    """

    def project(self, tmp_path, monkeypatch, mode="ok"):
        repo = make_repo(tmp_path)
        proj = make_project(repo, self.CONFIG)
        (proj / "fake_suite.py").write_text(FAKE_SUITE)
        commit_all(repo, "suite")
        monkeypatch.chdir(proj)
        monkeypatch.setattr(sys, "argv", ["run-gate"])
        monkeypatch.setenv("FAKE_MODE", mode)
        monkeypatch.setattr(run_gate, "workers_caps",
                            lambda *_a: (4.0, 8 * 1024 * MIB, "test caps"))
        return proj

    def test_baseline_run_records_the_selection(self, tmp_path, monkeypatch,
                                                capsys):
        proj = self.project(tmp_path, monkeypatch)
        assert run_gate.main(["suite"]) == 0
        latest = read_store(proj)["lanes"]["suite"]["latest"]
        assert latest["workers"]["workers"] == 1
        assert latest["workers_profile"]["status"] == "NO_MEASUREMENT"
        assert "N=1 mode=explicit" in capsys.readouterr().out
        assert not list((proj / ".run-gate" / "workers").glob("*"))

    def test_dry_run_substitutes_without_creating_state(self, tmp_path,
                                                        monkeypatch, capsys):
        proj = self.project(tmp_path, monkeypatch)
        assert run_gate.main(["suite", "--dry-run", "--workers", "3"]) == 0
        assert "/proj/fake_suite.py 3" in capsys.readouterr().out
        assert not (proj / ".run-gate" / "workers").exists()

    def test_isolation_failure_is_inconclusive_never_pass(self, tmp_path,
                                                          monkeypatch, capsys):
        proj = self.project(tmp_path, monkeypatch, mode="isolation")
        assert run_gate.main(["suite", "--workers", "2"]) == 1
        out = capsys.readouterr().out
        assert "INCONCLUSIVE/XDIST_ISOLATION" in out and "t.py::b" in out
        latest = read_store(proj)["lanes"]["suite"]["latest"]
        assert latest["verdict"] == "FAIL"
        assert latest["reason"] == "XDIST_ISOLATION"
        assert latest["outcome"] == "inconclusive"
        assert latest["history_eligible"] is False
        assert latest["xdist_recheck"]["serial_verdict"] == "PASS"
        assert not list((proj / ".run-gate" / "workers").glob("*"))

    def test_a_failure_that_fails_serially_too_is_a_real_fail(
            self, tmp_path, monkeypatch):
        proj = self.project(tmp_path, monkeypatch, mode="broken")
        assert run_gate.main(["suite", "--workers", "2"]) == 1
        latest = read_store(proj)["lanes"]["suite"]["latest"]
        assert latest["verdict"] == "FAIL"
        assert latest["xdist_recheck"]["serial_verdict"] == "FAIL"

    def test_scaling_prints_the_tables_and_records_every_step(
            self, tmp_path, monkeypatch, capsys):
        proj = self.project(tmp_path, monkeypatch)
        assert run_gate.main(
            ["suite", "--scaling", "1,2", "--repeat", "2:2"]) == 0
        out = capsys.readouterr().out
        assert "scaling results for lane 'suite'" in out
        table = [line for line in out.splitlines()
                 if re.match(r"\d+ \| ", line)]
        assert [line.split(" | ")[:2] for line in table] == [
            ["1", "1"], ["2", "2"]]
        history = read_store(proj)["lanes"]["suite"]["history"]
        # one measurement per (commit, worker count): the two N=2 reps
        # collapse onto the later one
        assert [item["workers"]["workers"] for item in history] == [1, 2]
        assert all(item["workers"]["scaling"] for item in history)
        assert not list((proj / ".run-gate" / "workers").glob("*"))

    def test_contaminated_step_is_repeated_then_labelled(
            self, tmp_path, monkeypatch, capsys):
        self.project(tmp_path, monkeypatch)
        monkeypatch.setattr(run_gate, "record_contaminated", lambda _r: True)
        assert run_gate.main(["suite", "--scaling", "1,2"]) == 1
        out = capsys.readouterr().out
        assert out.count("lane 'suite' verdict PASS") == 4
        assert "| yes | yes" in out

    def test_outcome_mismatch_is_inconclusive_and_named(
            self, tmp_path, monkeypatch, capsys):
        self.project(tmp_path, monkeypatch, mode="mismatch")
        assert run_gate.main(["suite", "--scaling", "1,2"]) == 1
        assert "differs from N=1 in: t::b" in capsys.readouterr().out

    def test_a_failing_step_stops_the_measurement(self, tmp_path, monkeypatch,
                                                  capsys):
        self.project(tmp_path, monkeypatch, mode="broken")
        assert run_gate.main(["suite", "--scaling", "1,2"]) == 1
        assert "lane 'suite' verdict FAIL" in capsys.readouterr().out

    def test_parity_proof_is_stored_and_read_back(self, tmp_path, monkeypatch,
                                                  capsys):
        proj = self.project(tmp_path, monkeypatch)
        assert run_gate.main(["suite", "--xdist-parity", "--workers", "3"]) == 0
        assert "xdist parity proven for N=1 vs N=3" in capsys.readouterr().out
        assert (proj / ".run-gate" / "xdist-parity" / "suite.json").is_file()
        assert run_gate.main(["suite", "--workers", "2", "--dry-run"]) == 0
        assert "parity=proven" in capsys.readouterr().out

    def test_parity_defaults_to_two_workers(self, tmp_path, monkeypatch,
                                            capsys):
        self.project(tmp_path, monkeypatch)
        assert run_gate.main(["suite", "--xdist-parity"]) == 0
        assert "N=1 vs N=2" in capsys.readouterr().out

    def test_parity_mismatch_stores_no_proof(self, tmp_path, monkeypatch):
        proj = self.project(tmp_path, monkeypatch, mode="mismatch")
        assert run_gate.main(["suite", "--xdist-parity"]) == 1
        assert not (proj / ".run-gate" / "xdist-parity").exists()

    def test_parity_without_junit_evidence_is_inconclusive(
            self, tmp_path, monkeypatch, capsys):
        proj = self.project(tmp_path, monkeypatch)
        (proj / "fake_suite.py").write_text("import sys\n")
        commit_all(proj.parent, "no junit")
        assert run_gate.main(["suite", "--xdist-parity"]) == 1
        assert "no parity proof stored" in capsys.readouterr().out

    def test_parity_of_one_worker_against_itself_proves_nothing(
            self, tmp_path, monkeypatch, capsys):
        proj = self.project(tmp_path, monkeypatch)
        assert run_gate.main(["suite", "--xdist-parity", "--workers", "1"]) == 1
        assert not (proj / ".run-gate" / "xdist-parity").exists()

    def test_measuring_parallel_workers_requires_xdist_safe(
            self, tmp_path, monkeypatch, capsys):
        proj = self.project(tmp_path, monkeypatch)
        config = (proj / "run-gate.toml")
        config.write_text(config.read_text().replace(
            "xdist_safe = true", "xdist_safe = false"))
        commit_all(proj.parent, "unsafe")
        assert run_gate.main(["suite", "--scaling", "1,2"]) == 2
        assert "xdist_safe" in capsys.readouterr().err

    @pytest.mark.parametrize("arguments,message", [
        (["--scaling", "1,2", "--xdist-parity"], "separate modes"),
        (["--scaling", "1,2", "--workers", "2"], "names its own"),
        (["--scaling", "1,2", "--dry-run"], "accept no --json"),
        (["--repeat", "1:2"], "applies to --scaling only"),
        (["--workers", "x"], "--workers must be"),
    ])
    def test_flag_misuse_is_refused(self, tmp_path, monkeypatch, capsys,
                                    arguments, message):
        self.project(tmp_path, monkeypatch)
        assert run_gate.main(["suite", *arguments]) == 2
        assert message in capsys.readouterr().err

    def test_flags_are_for_lane_runs_only(self, tmp_path, monkeypatch, capsys):
        self.project(tmp_path, monkeypatch)
        assert run_gate.main(["history", "--workers", "2"]) == 2
        assert "apply to a lane run only" in capsys.readouterr().err

    def test_workers_flag_on_a_lane_without_workers_is_refused(
            self, tmp_path, monkeypatch, capsys):
        repo = make_repo(tmp_path)
        proj = make_project(repo, """\
            schema_version = 1
            [environments.local]
            mode = "host"
            [lanes.plain]
            kind = "command"
            environment = "local"
            argv = ["true"]
            clean_tree = false
            profile = false
        """)
        monkeypatch.chdir(proj)
        monkeypatch.setattr(sys, "argv", ["run-gate"])
        assert run_gate.main(["plain", "--workers", "2"]) == 2
        assert "does not" in capsys.readouterr().err


class TestWorkersContainerRunners:
    """The worker count reaches the real runners' argv and environment."""

    CONTAINER = """\
        schema_version = 1
        [environments.tester]
        image = "tester-unified:local"
        mode = "ephemeral"
        [lanes.suite]
        kind = "command"
        environment = "tester"
        argv = ["bash", "-c", "cd {worktree}/proj && pytest -n {workers}"]
        workers = 2
        xdist_safe = true
        clean_tree = false
        [lanes.suite.resources]
        memory = "1g"
        cpus = "2"
    """

    def test_ephemeral_lane_gets_token_env_and_profile_dir(
            self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        proj = make_project(repo, self.CONTAINER)
        monkeypatch.setenv(run_gate.CGROUP_ENV_VAR, "dev-gates.slice")
        log = fake_docker(tmp_path, monkeypatch)
        proc = run_tool(proj, "suite")
        assert proc.returncode == 0, proc.stderr
        argv = docker_runs(log)[0]
        assert "RUN_GATE_WORKERS=2" not in argv     # name only on argv
        assert any(left == "-e" and right == "RUN_GATE_WORKERS"
                   for left, right in zip(argv, argv[1:]))
        assert any(left == "-e" and right == "RUN_GATE_PROFILE_DIR"
                   for left, right in zip(argv, argv[1:]))
        assert any("pytest -n 2" in part for part in argv)
        assert "parity=absent" in proc.stdout

    def test_cap_free_ephemeral_lane_is_refused_for_parallel_workers(
            self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        proj = make_project(repo, self.CONTAINER.replace(
            '        cpus = "2"\n', ""))
        monkeypatch.setenv(run_gate.CGROUP_ENV_VAR, "dev-gates.slice")
        fake_docker(tmp_path, monkeypatch)
        proc = run_tool(proj, "suite")
        assert proc.returncode == 2
        assert "requires enforced" in proc.stderr

    def test_exec_lane_baseline_exports_the_worker_count(
            self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        proj = make_project(repo, EXEC_LANE.replace(
            'mode = "exec"', 'mode = "exec"\ncontainer_name = "custom-name"'
        ).replace('echo hello"]', 'pytest -n {workers}"]\nworkers = 1'))
        log = fake_docker(tmp_path, monkeypatch)
        shim = shim_dir_of(monkeypatch) / "docker"
        seen = tmp_path / "carried-env"
        shim.write_text(shim.read_text().replace(
            'case "$1" in',
            'case "$1" in\n  ps) echo "custom-name" ;;\n'
            f'  exec) printf "%s" "$RUN_GATE_WORKERS" > {seen} ;;'))
        proc = run_tool(proj, "suite", "--worktree", str(repo))
        assert proc.returncode == 0, proc.stderr
        # M1: the value travelled in the docker client's environment.
        assert seen.read_text() == "1"
        call = docker_execs(log)[0]
        assert "RUN_GATE_WORKERS=1" not in call
        assert any(left == "-e" and right == "RUN_GATE_WORKERS"
                   for left, right in zip(call, call[1:]))
        assert any("pytest -n 1" in part for part in call)


class TestInconclusiveRecord:
    def test_finish_record_excludes_an_inconclusive_run(self):
        record = run_gate.finish_run_record(
            {"verdict": "FAIL", "reason": "XDIST_ISOLATION",
             "commit": "abc", "dirty": False, "git_operation": None,
             "_started_monotonic": 0.0}, exit_code=1)
        assert record["outcome"] == "inconclusive"
        assert record["history_eligible"] is False
        assert "XDIST_ISOLATION" in record["excluded_reason"]


class FakeObserver(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def serve_observer(directory, replies):
    seen = []

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            request = json.loads(self.rfile.readline())
            seen.append(request)
            self.wfile.write((json.dumps(replies[request["verb"]]) + "\n")
                             .encode())

    path = os.path.join(directory, "observer.sock")
    server = FakeObserver(path, Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, path, seen


class TestProfilePlugin:
    def config(self, worker=None):
        return SimpleNamespace(**({"workerinput": {"workerid": worker}}
                                  if worker else {}))

    @pytest.fixture(autouse=True)
    def reset(self, monkeypatch):
        monkeypatch.setattr(run_gate_profile, "_PROFILE", None)

    def test_subject_identity(self):
        assert run_gate_profile.subject_for(self.config()) == (
            "controller", "controller")
        assert run_gate_profile.subject_for(self.config("gw2")) == (
            "gw2", "xdist-worker")

    def test_inert_without_a_profile_dir(self, monkeypatch, tmp_path):
        monkeypatch.delenv(run_gate_profile.PROFILE_DIR_ENV, raising=False)
        run_gate_profile.pytest_configure(self.config())
        run_gate_profile.pytest_collection_finish(SimpleNamespace(items=[]))
        run_gate_profile.pytest_runtest_logreport(SimpleNamespace(when="call"))
        run_gate_profile.pytest_unconfigure(self.config())
        assert run_gate_profile._PROFILE is None

    def test_without_an_observer_the_profile_says_no_measurement(
            self, monkeypatch, tmp_path):
        monkeypatch.setenv(run_gate_profile.PROFILE_DIR_ENV, str(tmp_path))
        monkeypatch.delenv(run_gate_profile.OBSERVER_SOCKET_ENV,
                           raising=False)
        run_gate_profile.pytest_configure(self.config("gw0"))
        run_gate_profile.pytest_runtest_logreport(SimpleNamespace(when="setup"))
        run_gate_profile.pytest_runtest_logreport(SimpleNamespace(when="call"))
        run_gate_profile.pytest_collection_finish(SimpleNamespace(items=[1]))
        run_gate_profile.pytest_xdist_node_collection_finished(None, ["a"])
        run_gate_profile.pytest_unconfigure(self.config("gw0"))
        written = json.loads((tmp_path / "gw0.json").read_text())
        assert written["status"] == "NO_MEASUREMENT"
        assert "is not set" in written["reason"]
        assert written["subject"] == {"tool": "run-gate",
                                      "kind": "xdist-worker", "id": "gw0",
                                      "tests": 1}
        assert run_gate.aggregate_worker_profiles([written]) is None

    def test_unwritable_profile_dir_never_raises(
            self, monkeypatch, tmp_path, capsys):
        """H1: a profile that cannot be written is a warning, not a failure
        of pytest's unconfigure (which would turn a PASS lane into FAIL)."""
        blocker = tmp_path / "blocker"
        blocker.write_text("a file where the directory should be")
        monkeypatch.setenv(run_gate_profile.PROFILE_DIR_ENV,
                           str(blocker / "profiles"))
        monkeypatch.delenv(run_gate_profile.OBSERVER_SOCKET_ENV,
                           raising=False)
        run_gate_profile.pytest_configure(self.config())
        run_gate_profile.pytest_unconfigure(self.config())
        assert run_gate_profile._PROFILE is None
        err = capsys.readouterr().err
        assert "NO_MEASUREMENT" in err and "controller" in err

    def test_unreachable_observer_is_a_reasoned_no_measurement(
            self, monkeypatch, tmp_path):
        monkeypatch.setenv(run_gate_profile.PROFILE_DIR_ENV, str(tmp_path))
        monkeypatch.setenv(run_gate_profile.OBSERVER_SOCKET_ENV,
                           str(tmp_path / "missing.sock"))
        run_gate_profile.pytest_configure(self.config())
        run_gate_profile.pytest_unconfigure(self.config())
        written = json.loads((tmp_path / "controller.json").read_text())
        assert written["status"] == "NO_MEASUREMENT"
        assert "window-open failed" in written["reason"]

    def test_window_round_trip_against_a_fake_observer(self, monkeypatch):
        short = tempfile.mkdtemp(prefix="rgp", dir="/tmp")
        out = Path(short) / "out"
        replies = {
            "window-open": {"ok": True, "window": "w-1"},
            "window-close": {"ok": True, "metrics": {
                "rss_peak_bytes": 321, "cpu_seconds": 2.5,
                "io_read_bytes": 0, "io_write_bytes": 9},
                "missing": {}, "sources": {"rss_peak_bytes": "x"},
                "context": {"a": 1}}}
        server, socket_path, seen = serve_observer(short, replies)
        try:
            monkeypatch.setenv(run_gate_profile.PROFILE_DIR_ENV, str(out))
            monkeypatch.setenv(run_gate_profile.OBSERVER_SOCKET_ENV,
                               socket_path)
            run_gate_profile.pytest_configure(self.config())
            run_gate_profile.pytest_xdist_node_collection_finished(
                None, ["a", "b", "c"])
            run_gate_profile.pytest_unconfigure(self.config())
        finally:
            server.shutdown()
            server.server_close()
        written = json.loads((out / "controller.json").read_text())
        assert written["status"] == "MEASURED"
        assert written["metrics"]["rss_peak_bytes"] == 321
        assert written["observer"]["window"] == "w-1"
        assert written["subject"]["tests_collected"] == 3
        assert [item["verb"] for item in seen] == ["window-open",
                                                   "window-close"]
        assert seen[0]["args"]["mode"] == "process-tree"
        assert seen[0]["args"]["root_pid"] == os.getpid()
        assert seen[1]["args"]["window"] == "w-1"
        assert run_gate.aggregate_worker_profiles([written]) is None

    def test_refused_close_is_no_measurement(self, monkeypatch):
        short = tempfile.mkdtemp(prefix="rgp", dir="/tmp")
        replies = {"window-open": {"ok": True, "window": "w-9"},
                   "window-close": {"ok": False, "error": "WINDOW_LOST"}}
        server, socket_path, _ = serve_observer(short, replies)
        try:
            monkeypatch.setenv(run_gate_profile.PROFILE_DIR_ENV, short + "/o")
            monkeypatch.setenv(run_gate_profile.OBSERVER_SOCKET_ENV,
                               socket_path)
            run_gate_profile.pytest_configure(self.config("gw1"))
            run_gate_profile.pytest_unconfigure(self.config("gw1"))
        finally:
            server.shutdown()
            server.server_close()
        written = json.loads(Path(short, "o", "gw1.json").read_text())
        assert written["status"] == "NO_MEASUREMENT"
        assert "window-close failed" in written["reason"]
        assert written["observer"]["window"] == "w-9"

    def test_an_observer_that_hangs_up_is_a_refusal(self):
        short = tempfile.mkdtemp(prefix="rgp", dir="/tmp")
        path = os.path.join(short, "o.sock")

        class Silent(socketserver.StreamRequestHandler):
            def handle(self):
                self.rfile.readline()

        server = FakeObserver(path, Silent)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with pytest.raises(ValueError):
                run_gate_profile.observer_call(path, "x", {})
        finally:
            server.shutdown()
            server.server_close()

    def test_controller_collection_counts_the_suite(self, monkeypatch,
                                                    tmp_path):
        monkeypatch.setenv(run_gate_profile.PROFILE_DIR_ENV, str(tmp_path))
        monkeypatch.delenv(run_gate_profile.OBSERVER_SOCKET_ENV,
                           raising=False)
        run_gate_profile.pytest_configure(self.config())
        run_gate_profile.pytest_collection_finish(
            SimpleNamespace(items=[1, 2]))
        run_gate_profile.pytest_unconfigure(self.config())
        written = json.loads((tmp_path / "controller.json").read_text())
        assert written["subject"]["tests_collected"] == 2

    def test_observer_replies_must_be_objects_with_ok(self, tmp_path):
        short = tempfile.mkdtemp(prefix="rgp", dir="/tmp")
        server, socket_path, _ = serve_observer(short, {"x": []})
        try:
            with pytest.raises(RuntimeError, match="refused x"):
                run_gate_profile.observer_call(socket_path, "x", {})
        finally:
            server.shutdown()
            server.server_close()

    def test_real_xdist_suite_writes_one_profile_per_process(self, tmp_path):
        _testdeps.optional_module("xdist", "pytest-xdist drives this test")
        suite = tmp_path / "suite"
        suite.mkdir()
        (suite / "test_tiny.py").write_text(
            "import pytest\n\n@pytest.mark.parametrize('n', range(8))\n"
            "def test_n(n):\n    assert n >= 0\n")
        profiles = tmp_path / "profiles"
        environment = {**os.environ, "RUN_GATE_PROFILE_DIR": str(profiles),
                       "PYTHONPATH": str(RUN_GATE_DIR),
                       "PYTHONDONTWRITEBYTECODE": "1"}
        environment.pop("ASSAY_RESOURCE_OBSERVER_SOCKET", None)
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "run_gate_profile",
             "-p", "no:cacheprovider", "-n", "2", "-q", str(suite)],
            capture_output=True, text=True, env=environment, cwd=suite)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        names = sorted(path.name for path in profiles.glob("*.json"))
        assert names == ["controller.json", "gw0.json", "gw1.json"]
        controller = json.loads((profiles / "controller.json").read_text())
        assert controller["subject"]["tests_collected"] == 8
        ran = sum(json.loads((profiles / f"gw{i}.json").read_text())
                  ["subject"]["tests"] for i in (0, 1))
        assert ran == 8
