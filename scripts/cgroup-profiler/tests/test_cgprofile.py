"""Tests for cgprofile.py: argument parsing, target predigestion, the
collector's own orchestration (cmd_collect), the driver/collector handshake
(_start_run/_wait_for/_stop_run), and the top-level commands.

Nothing here starts a real container, calls the real docker binary, touches
the real /sys/fs/cgroup or /proc, or sleeps in real wall-clock time:

* the fake cgroup tree comes from conftest.py's ``cgroup_root`` fixture, and
  ``access.CGROUP_ROOT`` is monkeypatched to point at it — cmd_collect
  hardcodes that constant with no CLI override, so there is no other way to
  run it against synthetic data;
* ``lib.metrics.sample_host`` / ``lib.limits.mount_flags`` default to the
  real ``/proc`` with no override plumbed through cmd_collect either, so
  they are monkeypatched to fixed fakes for every cmd_collect test;
* the real adaptive-interval ``lib.sampler.Sampler`` sleeps in real time by
  construction (DESIGN.md's fake clock/sleep injection is not wired through
  cmd_collect); it is replaced with ``FakeSampler`` below, which drives
  cmd_collect's own ``on_sample``/``on_topology``/``sample_fn`` closures
  directly from a scripted list of actions — deterministic, and exercises
  exactly the code this suite owns;
* every subprocess/Popen boundary (the collector child process, the
  wrapped command, the helper container) is monkeypatched to a fake that
  never shells out.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cgprofile as cg
from lib import access, targets as targets_mod
from lib import analyze as analyze_lib
from lib import caps as caps_lib
from lib import damon as damon_lib
from lib import limits as limits_lib
from lib import logtail as logtail_lib
from lib import metrics as metrics_lib
from lib import phases as phases_lib
from lib import report_html as report_html_lib
from lib import report_md as report_md_lib
from lib import sampler as sampler_lib
from lib import serve as serve_lib
from lib import store as store_lib


# ── small process/clock doubles used across many tests ──────────────────────

class FakePopen:
    """Stands in for subprocess.Popen for the collector/helper child.

    Defaults to "already exited cleanly" (poll()/wait() both return 0
    immediately) so _stop_run's wait loop never has to actually spin.
    """

    def __init__(self, args=None, exit_code: Optional[int] = 0, **kwargs):
        self.args = args
        self._exit_code = exit_code
        self.returncode = exit_code
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self.returncode = -9


def make_fake_sampler(script: List[tuple]):
    """A lib.sampler.Sampler replacement driven by a scripted action list.

    Each action is ``("sample", seq, mono)`` — calls ``sample_fn`` for real
    against whatever membership/root the test set up, then feeds the record
    to ``on_sample`` — or ``("topology", appeared, disappeared)`` — calls
    ``on_topology`` directly. Nothing here sleeps or reads a clock; the test
    controls every tick explicitly.
    """

    class _FakeSampler:
        instances: List["_FakeSampler"] = []

        def __init__(self, membership, config, sample_fn, clock=None, sleep=None):
            self.membership = membership
            self.config = config
            self.sample_fn = sample_fn
            self.force_hot_calls = 0
            _FakeSampler.instances.append(self)

        def force_hot(self):
            self.force_hot_calls += 1

        def run(self, should_stop, on_sample, on_topology):
            for action in script:
                if should_stop():
                    return
                if action[0] == "sample":
                    _, seq, mono = action
                    raw = self.sample_fn(self.membership) or {}
                    record: Dict[str, Any] = {
                        "seq": seq, "t": 1_754_325_600.0 + mono, "mono": mono,
                        "cg": raw.get("cg", {}),
                    }
                    if "proc" in raw:
                        record["proc"] = raw["proc"]
                    if "host" in raw:
                        record["host"] = raw["host"]
                    on_sample(record)
                elif action[0] == "topology":
                    _, appeared, disappeared = action
                    on_topology(appeared, disappeared)
                else:
                    raise AssertionError(f"unknown fake-sampler action {action!r}")

    return _FakeSampler


class FakeSignalRegistry:
    def __init__(self):
        self.handlers: Dict[int, Any] = {}

    def set_signal(self, sig, handler):
        prev = self.handlers.get(sig, "DEFAULT")
        self.handlers[sig] = handler
        return prev


# ── _err / _note ──────────────────────────────────────────────────────────

class TestSmallHelpers:
    def test_err_prints_to_stderr_and_exits_with_code(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            cg._err("boom")
        assert exc_info.value.code == 2
        assert "cgprofile: boom" in capsys.readouterr().err

    def test_err_accepts_a_custom_code(self):
        with pytest.raises(SystemExit) as exc_info:
            cg._err("boom", code=5)
        assert exc_info.value.code == 5

    def test_note_prints_to_stderr_with_prefix(self, capsys):
        cg._note("hello")
        assert "[cgprofile] hello" in capsys.readouterr().err

    def test_venv_python_present_when_executable(self, monkeypatch):
        monkeypatch.setattr(cg.os, "access", lambda path, mode: True)
        assert cg._venv_python() == os.path.join(cg.HERE, "venv", "bin", "python")

    def test_venv_python_absent_when_not_executable(self, monkeypatch):
        monkeypatch.setattr(cg.os, "access", lambda path, mode: False)
        assert cg._venv_python() is None

    def test_require_reporting_deps_succeeds_when_libraries_are_importable(self):
        cg._require_reporting_deps()  # must not raise in this venv

    def test_require_reporting_deps_fails_with_the_fix_not_a_traceback(self, monkeypatch, capsys):
        monkeypatch.setitem(sys.modules, "pandas", None)
        with pytest.raises(SystemExit) as exc_info:
            cg._require_reporting_deps()
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "./setup.sh" in err
        assert "data is safe" in err

    def test_require_reporting_deps_fails_when_only_plotly_is_missing(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "plotly", None)
        with pytest.raises(SystemExit):
            cg._require_reporting_deps()

    def test_load_store_returns_the_store_module(self):
        assert cg._load_store() is store_lib


# ── _predigest_specs ─────────────────────────────────────────────────────

class TestPredigestSpecs:
    def _configure_helper_pid(
        self, monkeypatch, tmp_path: Path, *,
        cgroup_text: Optional[str] = "0::/worker.scope\n",
        same_namespace: bool = True, host_view: bool = False, process_exists: bool = True,
    ):
        proc_root = tmp_path / "proc"
        pid_namespace = tmp_path / "pid-ns"
        cgroup_namespace = tmp_path / "cgroup-ns"
        pid_namespace.touch()
        cgroup_namespace.touch()
        process_dir = proc_root / "4242"
        if process_exists:
            process_dir.mkdir(parents=True)
            namespace_dir = process_dir / "ns"
            namespace_dir.mkdir()
            (namespace_dir / "pid").symlink_to(pid_namespace)
            (namespace_dir / "cgroup").symlink_to(cgroup_namespace)
            (process_dir / "status").write_text("Name:\tworker\nNSpid:\t4242\n")
            (process_dir / "stat").write_text(_fake_proc_stat(98765))
            if cgroup_text is not None:
                (process_dir / "cgroup").write_text(cgroup_text)
        monkeypatch.setattr(access, "PROC_ROOT", str(proc_root))
        monkeypatch.setattr(access, "have_host_cgroup_view", lambda: host_view)
        monkeypatch.setattr(
            access, "same_cgroup_namespace", lambda pid, root: same_namespace,
        )
        cgroup_namespace_inode = cgroup_namespace.stat().st_ino
        monkeypatch.setattr(
            access, "local_namespace_inode",
            lambda name: cgroup_namespace_inode if name == "cgroup" else None,
        )
        monkeypatch.setattr(access, "self_container_id", lambda: "a" * 64)

    def _helper_identity_option(self, tmp_path: Path) -> str:
        pid_namespace_inode = (tmp_path / "pid-ns").stat().st_ino
        cgroup_namespace_inode = (tmp_path / "cgroup-ns").stat().st_ino
        return f"_cgprofile_pid={pid_namespace_inode}:4242:98765:{cgroup_namespace_inode}"

    def test_helper_self_resolves_to_the_invoking_container_id(self, monkeypatch):
        monkeypatch.setattr(access, "self_container_id", lambda: "a" * 64)

        out = cg._predigest_specs(["self@follow"], resolve_self=True)

        assert out == [f"containerid:{'a' * 64}@follow=1,as=self"]

    def test_helper_self_preserves_an_explicit_label(self, monkeypatch):
        monkeypatch.setattr(access, "self_container_id", lambda: "a" * 64)

        out = cg._predigest_specs(["self@as=caller"], resolve_self=True)

        assert out == [f"containerid:{'a' * 64}@as=caller"]

    def test_helper_self_refuses_when_the_callers_container_id_is_unknown(
        self, monkeypatch, capsys,
    ):
        monkeypatch.setattr(access, "self_container_id", lambda: None)

        with pytest.raises(SystemExit) as exc_info:
            cg._predigest_specs(["self"], resolve_self=True)

        assert exc_info.value.code == 2
        assert "Docker inspect could not establish it" in capsys.readouterr().err

    def test_helper_pid_resolves_through_caller_container_and_relative_cgroup(
        self, monkeypatch, tmp_path: Path,
    ):
        self._configure_helper_pid(monkeypatch, tmp_path)

        out = cg._predigest_specs(["pid:4242@follow"], resolve_self=True)

        assert out == [
            f"containerid:{'a' * 64}@subpath=/worker.scope,{self._helper_identity_option(tmp_path)},"
            "follow=1,as=pid-4242"
        ]

    def test_helper_pid_preserves_an_explicit_label(self, monkeypatch, tmp_path: Path):
        self._configure_helper_pid(monkeypatch, tmp_path)

        out = cg._predigest_specs(["pid:4242@as=worker"], resolve_self=True)

        assert out == [
            f"containerid:{'a' * 64}@subpath=/worker.scope,{self._helper_identity_option(tmp_path)},as=worker"
        ]

    def test_helper_pid_can_target_the_callers_container_root(self, monkeypatch, tmp_path: Path):
        self._configure_helper_pid(monkeypatch, tmp_path, cgroup_text="0::/\n")

        out = cg._predigest_specs(["pid:4242"], resolve_self=True)

        assert out == [
            f"containerid:{'a' * 64}@subpath=/,{self._helper_identity_option(tmp_path)},as=pid-4242"
        ]

    @pytest.mark.parametrize(
        ("spec", "config", "message"),
        [
            ("pid:bad", {}, "needs a number"),
            ("pid:0", {}, "positive process id"),
            ("pid:4242", {"host_view": True}, "private cgroup view"),
            ("pid:4242", {"process_exists": False}, "not visible"),
            ("pid:4242", {"same_namespace": False}, "different cgroup namespace"),
            ("pid:4242", {"cgroup_text": "1:name=systemd:/x\\n"}, "no absolute unified"),
            ("pid:4242", {"cgroup_text": "0::relative/path\\n"}, "no absolute unified"),
            ("pid:4242", {"cgroup_text": None}, "no absolute unified"),
            ("pid:4242", {"cgroup_text": "0::/../outside\\n"}, "unsafe cgroup path"),
            ("pid:4242@subpath=/override", {}, "reserved for the helper"),
        ],
    )
    def test_helper_pid_refuses_unverifiable_mappings(
        self, monkeypatch, tmp_path: Path, capsys, spec, config, message,
    ):
        self._configure_helper_pid(monkeypatch, tmp_path, **config)

        with pytest.raises(SystemExit) as exc_info:
            cg._predigest_specs([spec], resolve_self=True)

        assert exc_info.value.code == 2
        assert message in capsys.readouterr().err

    def test_container_spec_resolves_to_containerid(self, monkeypatch):
        monkeypatch.setattr(targets_mod, "resolve_container", lambda ref: ("a" * 64, "my-app"))
        out = cg._predigest_specs(["container:my-app"])
        assert out == [f"containerid:{'a' * 64}@as=my-app"]

    def test_container_spec_preserves_existing_options_and_as_override(self, monkeypatch):
        monkeypatch.setattr(targets_mod, "resolve_container", lambda ref: ("a" * 64, "my-app"))
        out = cg._predigest_specs(["container:my-app@as=custom,follow"])
        # Options round-trip through _parse_options' dict, which expands a
        # bare flag ("follow") to its stored form ("follow=1").
        assert out == [f"containerid:{'a' * 64}@as=custom,follow=1"]

    def test_container_spec_that_does_not_resolve_raises(self, monkeypatch):
        def raise_target_error(ref):
            raise targets_mod.TargetError("no such container")

        monkeypatch.setattr(targets_mod, "resolve_container", raise_target_error)
        with pytest.raises(SystemExit):
            cg._predigest_specs(["container:nope"])

    def test_label_spec_expands_to_one_entry_per_match(self, monkeypatch):
        monkeypatch.setattr(
            targets_mod, "resolve_label",
            lambda selector: [("a" * 64, "app-1"), ("b" * 64, "app-2")],
        )
        out = cg._predigest_specs(["label:app=gate"])
        assert out == [
            f"containerid:{'a' * 64}@as=app-1",
            f"containerid:{'b' * 64}@as=app-2",
        ]

    def test_label_spec_with_an_explicit_as_reuses_it_for_every_match(self, monkeypatch):
        # Every matched container gets the same override label rather than
        # its discovered name — that's the caller's explicit request, so the
        # per-match "as=<name>" synthesis must be skipped entirely.
        monkeypatch.setattr(
            targets_mod, "resolve_label",
            lambda selector: [("a" * 64, "app-1"), ("b" * 64, "app-2")],
        )
        out = cg._predigest_specs(["label:app=gate@as=custom"])
        assert out == [
            f"containerid:{'a' * 64}@as=custom",
            f"containerid:{'b' * 64}@as=custom",
        ]

    def test_label_spec_that_matches_nothing_raises(self, monkeypatch):
        def raise_target_error(selector):
            raise targets_mod.TargetError("no running container carries label")

        monkeypatch.setattr(targets_mod, "resolve_label", raise_target_error)
        with pytest.raises(SystemExit):
            cg._predigest_specs(["label:app=nope"])

    def test_bare_name_that_resolves_as_a_container(self, monkeypatch):
        monkeypatch.setattr(targets_mod, "docker_bin", lambda: "/usr/bin/docker")
        monkeypatch.setattr(targets_mod, "resolve_container", lambda ref: ("c" * 64, "gate"))
        out = cg._predigest_specs(["gate"])
        assert out == [f"containerid:{'c' * 64}@as=gate"]

    def test_bare_name_that_is_neither_a_container_nor_translatable_passes_through(self, monkeypatch):
        # docker present, but this name matches no running container — the
        # collector side (which has no docker socket) must still get to try
        # resolving it as a slice/cgroup, so the original spec passes through.
        monkeypatch.setattr(targets_mod, "docker_bin", lambda: "/usr/bin/docker")

        def raise_target_error(ref):
            raise targets_mod.TargetError("no container matches")

        monkeypatch.setattr(targets_mod, "resolve_container", raise_target_error)
        out = cg._predigest_specs(["dev-background.slice"])
        assert out == ["dev-background.slice"]

    def test_bare_name_with_an_explicit_as_keeps_it_rather_than_the_discovered_name(self, monkeypatch):
        monkeypatch.setattr(targets_mod, "docker_bin", lambda: "/usr/bin/docker")
        monkeypatch.setattr(targets_mod, "resolve_container", lambda ref: ("c" * 64, "discovered-name"))
        out = cg._predigest_specs(["gate@as=custom"])
        assert out == [f"containerid:{'c' * 64}@as=custom"]

    def test_bare_name_without_docker_passes_through_untouched(self, monkeypatch):
        monkeypatch.setattr(targets_mod, "docker_bin", lambda: None)
        out = cg._predigest_specs(["dev-background.slice"])
        assert out == ["dev-background.slice"]

    def test_explicit_scheme_other_than_container_or_label_passes_through(self, monkeypatch):
        monkeypatch.setattr(targets_mod, "docker_bin", lambda: None)
        out = cg._predigest_specs(["slice:dev-background.slice", "cgroup:/wings.slice", "self", "pid:99"])
        assert out == ["slice:dev-background.slice", "cgroup:/wings.slice", "self", "pid:99"]

    def test_already_resolved_containerid_spec_passes_through(self, monkeypatch):
        monkeypatch.setattr(targets_mod, "docker_bin", lambda: None)
        spec = "containerid:" + "a" * 64
        assert cg._predigest_specs([spec]) == [spec]


class TestTagRole:
    def test_adds_role_when_absent_and_no_other_options(self):
        assert cg._tag_role(["cgroup:/dev.slice"], "subject") == ["cgroup:/dev.slice@role=subject"]

    def test_appends_role_to_existing_options(self):
        assert cg._tag_role(["cgroup:/dev.slice@follow"], "observer") == [
            "cgroup:/dev.slice@follow,role=observer"
        ]

    def test_leaves_a_spec_that_already_names_a_role_untouched(self):
        assert cg._tag_role(["cgroup:/dev.slice@role=observer"], "subject") == [
            "cgroup:/dev.slice@role=observer"
        ]


class TestCapsFrom:
    def _args(self, cap):
        return argparse.Namespace(cap=cap)

    def test_no_cap_flags_is_empty(self):
        assert cg._caps_from(self._args([])) == {}

    def test_no_cap_attribute_at_all_is_empty(self):
        assert cg._caps_from(argparse.Namespace()) == {}

    def test_single_cap_parses_into_the_nested_map(self):
        caps = cg._caps_from(self._args(["/dev.slice/dev-background.slice:memory.max=2G"]))
        assert caps == {"/dev.slice/dev-background.slice": {"memory.max": "2G"}}

    def test_two_caps_on_the_same_cgroup_merge(self):
        caps = cg._caps_from(self._args([
            "/dev.slice:memory.max=2G",
            "/dev.slice:memory.high=1G",
        ]))
        assert caps == {"/dev.slice": {"memory.max": "2G", "memory.high": "1G"}}

    def test_missing_equals_sign_is_rejected(self):
        with pytest.raises(SystemExit):
            cg._caps_from(self._args(["/dev.slice:memory.max"]))

    def test_missing_colon_is_rejected(self):
        with pytest.raises(SystemExit):
            cg._caps_from(self._args(["memory.max=2G"]))

    def test_empty_cgroup_is_rejected(self):
        with pytest.raises(SystemExit):
            cg._caps_from(self._args([":memory.max=2G"]))

    def test_empty_filename_is_rejected(self):
        with pytest.raises(SystemExit):
            cg._caps_from(self._args(["/dev.slice:=2G"]))

    def test_value_may_itself_contain_an_equals_sign(self):
        # partition("=") on the whole raw string, not the value half, so a
        # value that legitimately contains "=" (unlikely for a cgroup file,
        # but nothing here should assume otherwise) still round-trips.
        caps = cg._caps_from(self._args(["/dev.slice:io.max=254:0 rbps=max"]))
        assert caps == {"/dev.slice": {"io.max": "254:0 rbps=max"}}

    def test_cgroup_may_itself_contain_a_colon_free_of_ambiguity(self):
        # rpartition(":") on the left half splits at the LAST colon, so a
        # cgroup path (which never contains ":") and a filename are
        # unambiguous even though the cgroup itself has slashes.
        caps = cg._caps_from(self._args(["/dev.slice/dev-background.slice:memory.high=max"]))
        assert caps == {"/dev.slice/dev-background.slice": {"memory.high": "max"}}


# ── log tailing wiring ───────────────────────────────────────────────────

class TestParseLogTailSpec:
    def test_bare_container_ref(self):
        assert cg._parse_log_tail_spec("container:my-app") == ("my-app", None)

    def test_container_ref_with_label(self):
        assert cg._parse_log_tail_spec("container:my-app@as=soulmask") == ("my-app", "soulmask")

    def test_missing_container_prefix_is_rejected(self):
        with pytest.raises(SystemExit):
            cg._parse_log_tail_spec("my-app")

    def test_empty_container_ref_is_rejected(self):
        with pytest.raises(SystemExit):
            cg._parse_log_tail_spec("container:@as=x")

    def test_unknown_option_is_rejected(self):
        with pytest.raises(SystemExit):
            cg._parse_log_tail_spec("container:my-app@bogus=1")


class TestCollectLogPatterns:
    def _args(self, **overrides):
        ns = argparse.Namespace(log_match=[], log_match_file=[])
        for key, value in overrides.items():
            setattr(ns, key, value)
        return ns

    def test_no_attributes_at_all_is_empty(self):
        assert cg._collect_log_patterns(argparse.Namespace()) == []

    def test_log_match_values_are_parsed(self):
        patterns = cg._collect_log_patterns(self._args(log_match=["a=hello", "b=regex:wor.d"]))
        assert [p.name for p in patterns] == ["a", "b"]

    def test_a_malformed_log_match_value_is_a_clean_cli_error(self):
        with pytest.raises(SystemExit):
            cg._collect_log_patterns(self._args(log_match=["no-equals-sign"]))

    def test_log_match_file_is_loaded_and_merged_with_log_match(self, tmp_path: Path):
        path = tmp_path / "patterns.txt"
        path.write_text("from-file=hello\n")
        patterns = cg._collect_log_patterns(
            self._args(log_match_file=[str(path)], log_match=["from-cli=world"])
        )
        assert [p.name for p in patterns] == ["from-file", "from-cli"]

    def test_missing_log_match_file_is_a_clean_cli_error(self, tmp_path: Path):
        with pytest.raises(SystemExit):
            cg._collect_log_patterns(self._args(log_match_file=[str(tmp_path / "nope.txt")]))

    def test_malformed_line_inside_log_match_file_is_a_clean_cli_error(self, tmp_path: Path):
        path = tmp_path / "bad.txt"
        path.write_text("no-equals-sign\n")
        with pytest.raises(SystemExit):
            cg._collect_log_patterns(self._args(log_match_file=[str(path)]))


class FakeLogTailer:
    instances: List["FakeLogTailer"] = []

    def __init__(self, container, patterns, run_path, label=None, docker=None, kind="phase"):
        self.container = container
        self.patterns = list(patterns)
        self.run_path = run_path
        self.label = label
        self.started = False
        self.stopped = False
        FakeLogTailer.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class TestStartStopLogTailers:
    def setup_method(self):
        FakeLogTailer.instances = []

    def _args(self, **overrides):
        ns = argparse.Namespace(log_tail=[], log_match=[], log_match_file=[])
        for key, value in overrides.items():
            setattr(ns, key, value)
        return ns

    def test_no_log_tail_specs_starts_nothing(self, tmp_path: Path):
        assert cg._start_log_tailers(self._args(), str(tmp_path)) == []

    def test_no_attributes_at_all_starts_nothing(self, tmp_path: Path):
        assert cg._start_log_tailers(argparse.Namespace(), str(tmp_path)) == []

    def test_log_tail_with_no_patterns_is_a_clean_cli_error(self, tmp_path: Path):
        with pytest.raises(SystemExit):
            cg._start_log_tailers(self._args(log_tail=["container:c1"]), str(tmp_path))

    def test_starts_one_tailer_per_spec_with_the_shared_pattern_set(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(logtail_lib, "LogTailer", FakeLogTailer)
        args = self._args(
            log_tail=["container:c1", "container:c2@as=other"],
            log_match=["a=hello"],
        )
        tailers = cg._start_log_tailers(args, str(tmp_path))
        assert len(tailers) == 2
        assert all(t.started for t in tailers)
        assert [t.container for t in tailers] == ["c1", "c2"]
        assert tailers[1].label == "other"
        assert [p.name for p in tailers[0].patterns] == ["a"]

    def test_stop_calls_stop_on_every_tailer(self):
        tailers = [FakeLogTailer("c1", [], "/run"), FakeLogTailer("c2", [], "/run")]
        cg._stop_log_tailers(tailers)
        assert all(t.stopped for t in tailers)

    def test_stop_of_an_empty_list_is_a_noop(self):
        cg._stop_log_tailers([])


# ── argument parsing ─────────────────────────────────────────────────────

class TestArgumentParsing:
    def test_run_requires_a_subcommand(self):
        parser = cg.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_run_with_double_dash_separator(self):
        args = cg.build_parser().parse_args(["run", "--", "echo", "hi"])
        assert args.command == ["--", "echo", "hi"]
        assert args.func is cg.cmd_run

    def test_run_without_double_dash_separator(self):
        args = cg.build_parser().parse_args(["run", "echo", "hi"])
        assert args.command == ["echo", "hi"]

    def test_run_with_no_command_at_all(self):
        args = cg.build_parser().parse_args(["run"])
        assert args.command == []

    def test_main_strips_a_leading_double_dash_from_the_command(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(cg, "cmd_run", lambda args: captured.setdefault("command", args.command) or 0)
        cg.main(["run", "--", "echo", "hi"])
        assert captured["command"] == ["echo", "hi"]

    def test_main_leaves_a_no_dash_command_untouched(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(cg, "cmd_run", lambda args: captured.setdefault("command", args.command) or 0)
        cg.main(["run", "echo", "hi"])
        assert captured["command"] == ["echo", "hi"]

    def test_targets_and_mark_have_no_command_attribute_at_all(self, monkeypatch):
        # main()'s getattr(args, "command", None) guard must not blow up on
        # subcommands that never define a "command" positional.
        monkeypatch.setattr(cg, "cmd_mark", lambda args: 0)
        assert cg.main(["mark", "x", "--quiet"]) == 0

    def test_mark_defaults(self):
        args = cg.build_parser().parse_args(["mark", "job-a"])
        assert args.kind == "phase"
        assert args.run_dir is None
        assert args.meta is None
        assert args.quiet is False

    def test_repeated_target_and_observe_accumulate(self):
        args = cg.build_parser().parse_args([
            "attach", "--target", "a", "--target", "b", "--observe", "c",
        ])
        assert args.target == ["a", "b"]
        assert args.observe == ["c"]

    def test_run_and_attach_accept_log_tail_flags(self):
        for sub, tail in (("run", ["--"]), ("attach", [])):
            args = cg.build_parser().parse_args([
                sub, "--log-tail", "container:c1", "--log-match", "a=b",
                "--log-match-file", "/tmp/x", *tail,
            ])
            assert args.log_tail == ["container:c1"]
            assert args.log_match == ["a=b"]
            assert args.log_match_file == ["/tmp/x"]

    def test_targets_and_doctor_have_no_log_tail_flags(self):
        targets_args_ns = cg.build_parser().parse_args(["targets"])
        doctor_args_ns = cg.build_parser().parse_args(["doctor"])
        assert not hasattr(targets_args_ns, "log_tail")
        assert not hasattr(doctor_args_ns, "log_tail")

    def test_doctor_has_only_helper_image(self):
        args = cg.build_parser().parse_args(["doctor"])
        assert args.func is cg.cmd_doctor
        assert args.helper_image is None

    def test_collect_subcommand_is_hidden_but_parseable(self):
        args = cg.build_parser().parse_args(["_collect", "--run-dir", "/tmp/x"])
        assert args.func is cg.cmd_collect
        assert args.run_dir == "/tmp/x"

    def test_main_propagates_access_error_as_a_clean_exit(self, monkeypatch, capsys):
        def raise_access_error(args):
            raise access.AccessError("no helper image")

        monkeypatch.setattr(cg, "cmd_doctor", raise_access_error)
        with pytest.raises(SystemExit) as exc_info:
            cg.main(["doctor"])
        assert exc_info.value.code == 2
        assert "no helper image" in capsys.readouterr().err

    def test_main_propagates_target_error_as_a_clean_exit(self, monkeypatch, capsys):
        def raise_target_error(args):
            raise targets_mod.TargetError("bad spec")

        monkeypatch.setattr(cg, "cmd_targets", raise_target_error)
        with pytest.raises(SystemExit):
            cg.main(["targets"])
        assert "bad spec" in capsys.readouterr().err

    def test_main_turns_keyboard_interrupt_into_exit_130(self, monkeypatch):
        def raise_keyboard_interrupt(args):
            raise KeyboardInterrupt()

        monkeypatch.setattr(cg, "cmd_run", raise_keyboard_interrupt)
        assert cg.main(["run", "--", "sleep", "1"]) == 130

    def test_main_propagates_the_command_exit_code(self, monkeypatch):
        monkeypatch.setattr(cg, "cmd_run", lambda args: 17)
        assert cg.main(["run", "--", "false"]) == 17


# ── cmd_mark ──────────────────────────────────────────────────────────────

class TestCmdMark:
    def test_no_run_dir_anywhere_is_an_error(self, monkeypatch, capsys):
        monkeypatch.delenv("CGPROFILE_RUN_DIR", raising=False)
        args = argparse.Namespace(name="job-a", kind="phase", run_dir=None, meta=None, quiet=False)
        with pytest.raises(SystemExit):
            cg.cmd_mark(args)
        assert "CGPROFILE_RUN_DIR" in capsys.readouterr().err

    def test_run_dir_from_the_environment(self, tmp_path: Path, monkeypatch):
        run_dir = tmp_path / "run-1"
        run_dir.mkdir()
        monkeypatch.setenv("CGPROFILE_RUN_DIR", str(run_dir))
        args = argparse.Namespace(name="job-a", kind="phase", run_dir=None, meta=None, quiet=True)
        assert cg.cmd_mark(args) == 0
        marks = (run_dir / "marks.jsonl").read_text()
        assert '"name": "job-a"' in marks

    def test_nonexistent_run_dir_is_an_error(self, tmp_path: Path, capsys):
        args = argparse.Namespace(
            name="job-a", kind="phase", run_dir=str(tmp_path / "nope"), meta=None, quiet=False,
        )
        with pytest.raises(SystemExit):
            cg.cmd_mark(args)
        assert "does not exist" in capsys.readouterr().err

    def test_malformed_meta_json_raises(self, tmp_path: Path):
        run_dir = tmp_path / "run-1"
        run_dir.mkdir()
        args = argparse.Namespace(
            name="job-a", kind="phase", run_dir=str(run_dir), meta="{not json", quiet=False,
        )
        with pytest.raises(json.JSONDecodeError):
            cg.cmd_mark(args)

    def test_valid_meta_json_is_stored(self, tmp_path: Path):
        run_dir = tmp_path / "run-1"
        run_dir.mkdir()
        args = argparse.Namespace(
            name="job-a", kind="event", run_dir=str(run_dir), meta='{"n": 3}', quiet=True,
        )
        cg.cmd_mark(args)
        line = json.loads((run_dir / "marks.jsonl").read_text().strip())
        assert line["meta"] == {"n": 3}
        assert line["kind"] == "event"

    def test_not_quiet_prints_a_note(self, tmp_path: Path, capsys):
        run_dir = tmp_path / "run-1"
        run_dir.mkdir()
        args = argparse.Namespace(name="job-a", kind="phase", run_dir=str(run_dir), meta=None, quiet=False)
        cg.cmd_mark(args)
        assert "mark phase:job-a" in capsys.readouterr().err

    def test_explicit_run_dir_wins_over_the_environment(self, tmp_path: Path, monkeypatch):
        env_dir = tmp_path / "env-run"
        env_dir.mkdir()
        explicit_dir = tmp_path / "explicit-run"
        explicit_dir.mkdir()
        monkeypatch.setenv("CGPROFILE_RUN_DIR", str(env_dir))
        args = argparse.Namespace(
            name="x", kind="phase", run_dir=str(explicit_dir), meta=None, quiet=True,
        )
        cg.cmd_mark(args)
        assert (explicit_dir / "marks.jsonl").exists()
        assert not (env_dir / "marks.jsonl").exists()


# ── _wait_for ─────────────────────────────────────────────────────────────

class TestWaitFor:
    def test_returns_as_soon_as_the_path_exists(self, tmp_path: Path):
        target = tmp_path / "ready"
        target.write_text("x")
        cg._wait_for(str(target), timeout=5.0, what="the collector")  # must not raise

    def test_times_out_without_ever_sleeping_in_real_time(self, tmp_path: Path, monkeypatch):
        target = tmp_path / "never-appears"
        clock = {"t": 0.0}
        sleeps = []

        def fake_monotonic():
            return clock["t"]

        def fake_sleep(dt):
            sleeps.append(dt)
            clock["t"] += dt

        monkeypatch.setattr(cg.time, "monotonic", fake_monotonic)
        monkeypatch.setattr(cg.time, "sleep", fake_sleep)
        with pytest.raises(SystemExit) as exc_info:
            cg._wait_for(str(target), timeout=1.0, what="the widget")
        assert exc_info.value.code == 2
        assert sleeps  # it did poll, just never in real wall-clock time
        assert sum(sleeps) >= 1.0

    def test_timeout_message_names_what_it_was_waiting_for(self, tmp_path: Path, monkeypatch, capsys):
        clock = {"t": 0.0}
        monkeypatch.setattr(cg.time, "monotonic", lambda: clock["t"])
        monkeypatch.setattr(cg.time, "sleep", lambda dt: clock.__setitem__("t", clock["t"] + dt))
        with pytest.raises(SystemExit):
            cg._wait_for(str(tmp_path / "nope"), timeout=1.0, what="the sentinel")
        assert "the sentinel" in capsys.readouterr().err


# ── _stop_run ─────────────────────────────────────────────────────────────

class TestStopRun:
    def test_collector_writes_the_done_sentinel_promptly(self, tmp_path: Path):
        run = store_lib.RunDir(str(tmp_path), run_id="run-a")
        open(os.path.join(run.path, cg.DONE_FILE), "w").close()
        child = FakePopen(exit_code=None)  # still "running" by poll(), done file wins
        cg._stop_run(run, child, timeout=5.0)
        assert os.path.exists(os.path.join(run.path, cg.STOP_FILE))
        assert not child.terminated

    def test_child_exiting_on_its_own_is_also_enough(self, tmp_path: Path):
        run = store_lib.RunDir(str(tmp_path), run_id="run-b")
        child = FakePopen(exit_code=0)  # already exited; no done file needed
        cg._stop_run(run, child, timeout=5.0)
        assert not child.terminated

    def test_collector_that_never_stops_is_terminated(self, tmp_path: Path, monkeypatch):
        run = store_lib.RunDir(str(tmp_path), run_id="run-c")
        child = FakePopen(exit_code=None)  # never exits, never writes done

        clock = {"t": 0.0}
        monkeypatch.setattr(cg.time, "monotonic", lambda: clock["t"])

        def fake_sleep(dt):
            clock["t"] += dt

        monkeypatch.setattr(cg.time, "sleep", fake_sleep)
        cg._stop_run(run, child, timeout=1.0)
        assert child.terminated
        assert child.returncode == 0  # wait() after terminate() still succeeds

    def test_child_that_wont_even_terminate_is_killed(self, tmp_path: Path, monkeypatch):
        run = store_lib.RunDir(str(tmp_path), run_id="run-d")

        class StubbornPopen(FakePopen):
            def wait(self, timeout=None):
                raise subprocess.TimeoutExpired(cmd="collector", timeout=timeout)

        child = StubbornPopen(exit_code=None)
        clock = {"t": 0.0}
        monkeypatch.setattr(cg.time, "monotonic", lambda: clock["t"])
        monkeypatch.setattr(cg.time, "sleep", lambda dt: clock.__setitem__("t", clock["t"] + dt))
        cg._stop_run(run, child, timeout=0.5)
        assert child.terminated
        assert child.killed


# ── cmd_collect ───────────────────────────────────────────────────────────

@pytest.fixture
def collect_env(monkeypatch, cgroup_root: Path):
    """Point the collector at the fake tree, and stub the two readers that
    have no root override plumbed through cmd_collect at all."""
    monkeypatch.setattr(access, "CGROUP_ROOT", str(cgroup_root))
    monkeypatch.setattr(metrics_lib, "sample_host", lambda *a, **k: {"meminfo": {"MemTotal": 1}})
    monkeypatch.setattr(limits_lib, "mount_flags", lambda *a, **k: set())
    return cgroup_root


def make_collect_args(run_dir: Path, **overrides) -> argparse.Namespace:
    ns = argparse.Namespace(
        run_dir=str(run_dir),
        target=["cgroup:/dev.slice/dev-background.slice"],
        observe=[],
        follow_children=False,
        max_depth=4,
        hot_interval=0.25,
        idle_interval=2.0,
        discovery_interval=2.0,
        duration=None,
        damon=False,
        caps=None,
    )
    for key, value in overrides.items():
        setattr(ns, key, value)
    return ns


def _fake_proc_stat(start_ticks: int, pid: int = 4242) -> str:
    fields = ["S", *(["0"] * 18), str(start_ticks)]
    return f"{pid} (worker (fixture)) {' '.join(fields)}\n"


class TestCmdCollect:
    def test_no_target_resolved_is_an_error(self, collect_env, tmp_path: Path):
        args = make_collect_args(tmp_path / "run-empty", target=[], observe=[])
        with pytest.raises(SystemExit):
            cg.cmd_collect(args)

    def test_happy_path_writes_manifest_ready_and_done(self, collect_env, tmp_path: Path, monkeypatch):
        run_dir = tmp_path / "run-1"
        args = make_collect_args(run_dir)
        monkeypatch.setattr(sampler_lib, "Sampler", make_fake_sampler([("sample", 0, 0.0)]))
        assert cg.cmd_collect(args) == 0

        assert os.path.exists(os.path.join(str(run_dir), cg.READY_FILE))
        assert os.path.exists(os.path.join(str(run_dir), cg.DONE_FILE))
        run = store_lib.RunDir(str(tmp_path), run_id="run-1", create=False)
        manifest = run.read_manifest()
        assert manifest["mode"] == "collector"
        assert manifest["cgroup_root"] == str(collect_env)
        assert "duration" in manifest and manifest["duration"] >= 0
        samples = list(run.read("samples"))
        assert len(samples) == 1
        assert samples[0]["seq"] == 0

    def test_observer_targets_are_tagged_and_sampled_alongside_subjects(self, collect_env, tmp_path: Path, monkeypatch):
        run_dir = tmp_path / "run-obs"
        args = make_collect_args(
            run_dir,
            target=["cgroup:/dev.slice/dev-background.slice"],
            observe=["cgroup:/wings.slice/wings-prod.slice"],
        )
        monkeypatch.setattr(sampler_lib, "Sampler", make_fake_sampler([("sample", 0, 0.0)]))
        cg.cmd_collect(args)
        run = store_lib.RunDir(str(tmp_path), run_id="run-obs", create=False)
        manifest = run.read_manifest()
        roles = {t["cgroup"]: t["role"] for t in manifest["targets"]}
        assert roles["/dev.slice/dev-background.slice"] == "subject"
        assert roles["/wings.slice/wings-prod.slice"] == "observer"
        sample = next(iter(run.read("samples")))
        assert "/wings.slice/wings-prod.slice" in sample["cg"]

    def test_a_second_sample_hits_the_event_detection_path(self, collect_env, tmp_path: Path, monkeypatch):
        # Only from a second sample onward does cmd_collect's on_sample
        # closure have a `prev` to diff against.
        run_dir = tmp_path / "run-2"
        args = make_collect_args(run_dir)
        monkeypatch.setattr(
            sampler_lib, "Sampler",
            make_fake_sampler([("sample", 0, 0.0), ("sample", 1, 0.25)]),
        )
        cg.cmd_collect(args)
        run = store_lib.RunDir(str(tmp_path), run_id="run-2", create=False)
        samples = list(run.read("samples"))
        assert len(samples) == 2
        # events.jsonl may legitimately be empty (nothing crossed a
        # threshold in the fake tree's static data) — what matters is that
        # writing it didn't raise, i.e. the detector.observe() path ran.
        list(run.read("events"))

    def test_event_detection_actually_fires_when_a_counter_moves(self, collect_env, tmp_path: Path, monkeypatch):
        # The two prior samples used static fixture files, so every delta was
        # zero and detector.observe() never actually returned an event; this
        # one mutates memory.events.local between ticks so run.append(
        # "events", ...) genuinely executes.
        run_dir = tmp_path / "run-event"
        events_file = collect_env / "dev.slice/dev-background.slice/memory.events.local"

        class MutatingSampler(make_fake_sampler([("sample", 0, 0.0)])):
            def run(self, should_stop, on_sample, on_topology):
                super().run(should_stop, on_sample, on_topology)
                events_file.write_text(
                    "low 0\nhigh 5\nmax 0\noom 0\noom_kill 0\noom_group_kill 0\n"
                )
                raw = self.sample_fn(self.membership) or {}
                record = {"seq": 1, "t": 1_754_325_601.0, "mono": 1.0, "cg": raw.get("cg", {})}
                on_sample(record)

        args = make_collect_args(run_dir)
        monkeypatch.setattr(sampler_lib, "Sampler", MutatingSampler)
        cg.cmd_collect(args)
        run = store_lib.RunDir(str(tmp_path), run_id="run-event", create=False)
        events = list(run.read("events"))
        assert any(e["kind"] == "memory_high_breach" for e in events)

    def test_a_new_mark_snaps_the_sampler_to_hot_on_the_next_tick(self, collect_env, tmp_path: Path, monkeypatch):
        # DESIGN.md §4.4: "any... phase mark ⇒ snap straight to hot_interval
        # on the next tick." A mark is written by a different process sharing
        # this run directory (cgprofile mark, a wrapper boundary, a log-tail
        # match — DESIGN.md §3) — the sampler has no way to notice one on its
        # own, so cmd_collect's on_sample must poll marks.jsonl each tick and
        # call sampler.force_hot() when it grows. No mark exists for the
        # first tick; one is written between the first and second, mirroring
        # test_event_detection_actually_fires_when_a_counter_moves' mid-run
        # mutation pattern above.
        run_dir = tmp_path / "run-mark-forces-hot"

        class MarkWritingSampler(make_fake_sampler([("sample", 0, 0.0)])):
            def run(self, should_stop, on_sample, on_topology):
                super().run(should_stop, on_sample, on_topology)
                phases_lib.emit_mark(str(run_dir), "loading-world", kind="phase")
                raw = self.sample_fn(self.membership) or {}
                record = {"seq": 1, "t": 1_754_325_600.25, "mono": 0.25, "cg": raw.get("cg", {})}
                on_sample(record)

        monkeypatch.setattr(sampler_lib, "Sampler", MarkWritingSampler)
        cg.cmd_collect(make_collect_args(run_dir))

        instance = MarkWritingSampler.instances[-1]
        assert instance.force_hot_calls == 1

    def test_no_new_marks_never_calls_force_hot(self, collect_env, tmp_path: Path, monkeypatch):
        run_dir = tmp_path / "run-no-marks"
        monkeypatch.setattr(
            sampler_lib, "Sampler",
            make_fake_sampler([("sample", 0, 0.0), ("sample", 1, 0.25)]),
        )
        cg.cmd_collect(make_collect_args(run_dir))
        # make_fake_sampler's _FakeSampler is a fresh class per call, so its
        # own instances list holds exactly this run's construction.
        sampler_cls = sampler_lib.Sampler
        assert sampler_cls.instances[-1].force_hot_calls == 0

    def test_topology_change_before_any_sample_anchors_to_mono_zero(self, collect_env, tmp_path: Path, monkeypatch):
        run_dir = tmp_path / "run-topo-first"
        args = make_collect_args(run_dir, follow_children=True)
        monkeypatch.setattr(
            sampler_lib, "Sampler",
            make_fake_sampler([("topology", ["/dev.slice/dev-background.slice/x"], [])]),
        )
        cg.cmd_collect(args)
        run = store_lib.RunDir(str(tmp_path), run_id="run-topo-first", create=False)
        events = list(run.read("events"))
        appeared = [e for e in events if e["kind"] == "cgroup_appeared"]
        assert appeared and appeared[0]["mono"] == 0.0

    def test_topology_change_after_a_sample_anchors_to_the_last_sample(self, collect_env, tmp_path: Path, monkeypatch):
        run_dir = tmp_path / "run-topo-after"
        args = make_collect_args(run_dir, follow_children=True)
        monkeypatch.setattr(
            sampler_lib, "Sampler",
            make_fake_sampler([
                ("sample", 0, 3.5),
                ("topology", [], ["/dev.slice/dev-background.slice/x"]),
            ]),
        )
        cg.cmd_collect(args)
        run = store_lib.RunDir(str(tmp_path), run_id="run-topo-after", create=False)
        events = list(run.read("events"))
        gone = [e for e in events if e["kind"] == "cgroup_disappeared"]
        assert gone and gone[0]["mono"] == 3.5

    def test_caps_are_applied_during_the_run_and_restored_after(self, collect_env, tmp_path: Path, monkeypatch):
        run_dir = tmp_path / "run-caps"
        target_file = collect_env / "dev.slice/dev-background.slice/memory.high"
        before = target_file.read_text().strip()
        seen_during = {}

        script = [("sample", 0, 0.0)]

        class ObservingSampler(make_fake_sampler(script)):
            def run(self, should_stop, on_sample, on_topology):
                seen_during["value"] = target_file.read_text().strip()
                super().run(should_stop, on_sample, on_topology)

        args = make_collect_args(
            run_dir,
            caps=json.dumps({"/dev.slice/dev-background.slice": {"memory.high": "1073741824"}}),
        )
        monkeypatch.setattr(sampler_lib, "Sampler", ObservingSampler)
        cg.cmd_collect(args)

        assert seen_during["value"] == "1073741824"
        assert target_file.read_text().strip() == before  # restored

    def test_pid_targets_are_sampled_and_empty_proc_data_is_filtered(self, collect_env, tmp_path: Path, monkeypatch):
        run_dir = tmp_path / "run-pid"

        def fake_cgroup_of_pid(pid, root=access.CGROUP_ROOT, proc_root=access.PROC_ROOT):
            return "/dev.slice/dev-background.slice"

        monkeypatch.setattr(targets_mod, "cgroup_of_pid", fake_cgroup_of_pid)
        monkeypatch.setattr(targets_mod.util, "read_text", lambda p: "fakeproc")

        def fake_sample_proc(pid, proc_root="/proc"):
            return {"rss_anon": 100} if pid == 4242 else {}

        monkeypatch.setattr(metrics_lib, "sample_proc", fake_sample_proc)
        args = make_collect_args(run_dir, target=["pid:4242", "pid:5555"])
        monkeypatch.setattr(sampler_lib, "Sampler", make_fake_sampler([("sample", 0, 0.0)]))
        cg.cmd_collect(args)

        run = store_lib.RunDir(str(tmp_path), run_id="run-pid", create=False)
        sample = next(iter(run.read("samples")))
        assert sample["proc"] == {"4242": {"rss_anon": 100}}  # 5555 dropped: {} is falsy

    def test_host_snapshot_is_taken_on_the_configured_cadence(self, collect_env, tmp_path: Path, monkeypatch):
        run_dir = tmp_path / "run-host-cadence"
        calls = []
        monkeypatch.setattr(metrics_lib, "sample_host", lambda *a, **k: calls.append(1) or {"MemTotal": 1})
        # idle/hot chosen so host_every == 2: every other sample carries "host".
        args = make_collect_args(run_dir, hot_interval=1.0, idle_interval=2.0)
        monkeypatch.setattr(
            sampler_lib, "Sampler",
            make_fake_sampler([("sample", 0, 0.0), ("sample", 1, 1.0), ("sample", 2, 2.0)]),
        )
        cg.cmd_collect(args)
        run = store_lib.RunDir(str(tmp_path), run_id="run-host-cadence", create=False)
        samples = list(run.read("samples"))
        assert "host" in samples[0]
        assert "host" not in samples[1]
        assert "host" in samples[2]

    def test_damon_requested_but_unavailable_notes_and_continues(self, collect_env, tmp_path: Path, monkeypatch, capsys):
        run_dir = tmp_path / "run-damon-unavail"
        monkeypatch.setattr(damon_lib, "available", lambda: False)
        args = make_collect_args(run_dir, damon=True)
        monkeypatch.setattr(sampler_lib, "Sampler", make_fake_sampler([("sample", 0, 0.0)]))
        assert cg.cmd_collect(args) == 0
        assert "unavailable" in capsys.readouterr().err

    def test_damon_available_collects_on_every_twentieth_sample(self, collect_env, tmp_path: Path, monkeypatch):
        run_dir = tmp_path / "run-damon-ok"

        class FakeDamonSession:
            instances: List["FakeDamonSession"] = []

            def __init__(self, targets, **kwargs):
                self.targets = targets
                self.collect_calls = 0
                FakeDamonSession.instances.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def collect(self):
                self.collect_calls += 1
                return [{"region": "hot"}]

        monkeypatch.setattr(damon_lib, "available", lambda: True)
        monkeypatch.setattr(damon_lib, "DamonSession", FakeDamonSession)
        args = make_collect_args(run_dir, damon=True)
        # seq=0 -> 0 % 20 == 0 (collect); seq=1 -> skipped.
        monkeypatch.setattr(
            sampler_lib, "Sampler",
            make_fake_sampler([("sample", 0, 0.0), ("sample", 1, 0.25)]),
        )
        cg.cmd_collect(args)
        assert FakeDamonSession.instances[0].collect_calls == 1
        # No pid targets resolved => the paddr fallback target is used.
        assert FakeDamonSession.instances[0].targets[0].kind == "paddr"
        run = store_lib.RunDir(str(tmp_path), run_id="run-damon-ok", create=False)
        damon_records = list(run.read("damon"))
        assert len(damon_records) == 1

    def test_damon_available_with_pid_targets_uses_vaddr_targets(self, collect_env, tmp_path: Path, monkeypatch):
        run_dir = tmp_path / "run-damon-pid"

        class FakeDamonSession:
            instances: List["FakeDamonSession"] = []

            def __init__(self, targets, **kwargs):
                self.targets = targets
                FakeDamonSession.instances.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def collect(self):
                return []

        monkeypatch.setattr(damon_lib, "available", lambda: True)
        monkeypatch.setattr(damon_lib, "DamonSession", FakeDamonSession)
        monkeypatch.setattr(
            targets_mod, "cgroup_of_pid",
            lambda pid, root=access.CGROUP_ROOT, proc_root=access.PROC_ROOT: "/dev.slice/dev-background.slice",
        )
        monkeypatch.setattr(targets_mod.util, "read_text", lambda p: "fakeproc")
        monkeypatch.setattr(metrics_lib, "sample_proc", lambda pid, proc_root="/proc": {})
        args = make_collect_args(run_dir, target=["pid:4242"], damon=True)
        monkeypatch.setattr(sampler_lib, "Sampler", make_fake_sampler([("sample", 0, 0.0)]))
        cg.cmd_collect(args)

        # A resolved pid target must steer DamonSession toward per-process
        # (vaddr) monitoring instead of the whole-system (paddr) fallback.
        targets_used = FakeDamonSession.instances[0].targets
        assert len(targets_used) == 1
        assert targets_used[0].kind == "vaddr"
        assert targets_used[0].pid == 4242

    def test_sigterm_during_the_run_sets_stopping_and_is_observable(self, collect_env, tmp_path: Path, monkeypatch):
        run_dir = tmp_path / "run-sigterm"
        registry = FakeSignalRegistry()
        monkeypatch.setattr(cg.signal, "signal", registry.set_signal)

        observed = {}

        class SignalProbingSampler(make_fake_sampler([("sample", 0, 0.0)])):
            def run(self, should_stop, on_sample, on_topology):
                observed["before"] = should_stop()
                handler = registry.handlers[signal.SIGTERM]
                handler(signal.SIGTERM, None)
                observed["after"] = should_stop()
                super().run(should_stop, on_sample, on_topology)

        args = make_collect_args(run_dir)
        monkeypatch.setattr(sampler_lib, "Sampler", SignalProbingSampler)
        cg.cmd_collect(args)
        assert observed["before"] is False
        assert observed["after"] is True

    def test_should_stop_also_sees_the_stop_sentinel_file(self, collect_env, tmp_path: Path, monkeypatch):
        run_dir = tmp_path / "run-stopfile"
        observed = {}

        class StopFileProbingSampler(make_fake_sampler([])):
            def run(self, should_stop, on_sample, on_topology):
                observed["before"] = should_stop()
                open(os.path.join(run_dir, cg.STOP_FILE), "w").close()
                observed["after"] = should_stop()

        args = make_collect_args(run_dir)
        monkeypatch.setattr(sampler_lib, "Sampler", StopFileProbingSampler)
        cg.cmd_collect(args)
        assert observed["before"] is False
        assert observed["after"] is True


# ── _launch_helper / _start_run ─────────────────────────────────────────

class TestLaunchHelper:
    def test_builds_the_docker_command_and_notes_the_image(self, monkeypatch, tmp_path: Path, capsys):
        spec = access.HelperSpec(
            image="cgprofile-self:local", repo_host_path="/host/repo", repo_mount_path=cg.HERE,
            out_host_path="/host/out", out_mount_path=str(tmp_path),
            cgroup_parent="dev-interactive.slice",
        )
        monkeypatch.setattr(access, "build_helper_spec", lambda repo, out, image, cgroup_parent=None: spec)
        monkeypatch.setattr(access, "docker_bin", lambda: "/usr/bin/docker")
        captured = {}

        def fake_popen(command, **kwargs):
            captured["command"] = command
            return FakePopen(command)

        monkeypatch.setattr(cg.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(
            access, "_docker",
            lambda *a, **k: subprocess.CompletedProcess(a, returncode=1),
        )
        run_path = str(tmp_path / "run-1")
        child = cg._launch_helper(run_path, ["--run-dir", run_path], None)
        assert isinstance(child, FakePopen)
        command = captured["command"]
        assert command[0] == "/usr/bin/docker"
        assert command[-5:] == [
            "python3", os.path.join(cg.HERE, "cgprofile.py"), "_collect", "--run-dir", run_path,
        ]
        # docker_args() (--privileged, the bind mounts, etc.) sit between the
        # binary and the plain-python3 collector invocation.
        assert "--privileged" in command
        assert "--cpus=3" in command
        assert "--cgroupns=host" not in command
        assert "--pid=host" not in command
        assert "--cgroupns=private" in command
        assert "--cgroup-parent=dev-interactive.slice" in command
        assert "starting helper container" in capsys.readouterr().err

    def test_helper_is_named_and_updated_to_the_cpu_cap(self, monkeypatch, tmp_path: Path):
        spec = access.HelperSpec(
            image="cgprofile-self:local", repo_host_path="/host/repo", repo_mount_path=cg.HERE,
            out_host_path="/host/out", out_mount_path=str(tmp_path),
            cgroup_parent="dev-interactive.slice",
        )
        monkeypatch.setattr(access, "build_helper_spec", lambda *a, **k: spec)
        monkeypatch.setattr(access, "docker_bin", lambda: "/usr/bin/docker")
        captured = {}
        child = FakePopen(exit_code=None)
        monkeypatch.setattr(cg.subprocess, "Popen", lambda command, **kwargs: captured.update(command=command) or child)

        def fake_docker(*args, **kwargs):
            captured["update"] = args
            captured["timeout"] = kwargs["timeout"]
            return access.subprocess.CompletedProcess(args=["docker", *args], returncode=0, stdout="", stderr="")

        monkeypatch.setattr(access, "_docker", fake_docker)
        run_path = str(tmp_path / "run-named-helper")
        result = cg._launch_helper(run_path, ["--run-dir", run_path], None)

        assert result is child
        command = captured["command"]
        assert "--name" in command
        assert command[command.index("--name") + 1].startswith("cgprofile-helper-")
        assert captured["update"] == ("update", "--cpus=3", command[command.index("--name") + 1])
        assert captured["timeout"] == 2

    def test_helper_that_exits_after_failed_update_is_not_treated_as_cap_failure(
        self, monkeypatch, tmp_path: Path,
    ):
        spec = access.HelperSpec(
            image="cgprofile-self:local", repo_host_path="/host/repo", repo_mount_path=cg.HERE,
            out_host_path="/host/out", out_mount_path=str(tmp_path),
            cgroup_parent="dev-interactive.slice",
        )
        monkeypatch.setattr(access, "build_helper_spec", lambda *a, **k: spec)
        monkeypatch.setattr(access, "docker_bin", lambda: "/usr/bin/docker")
        child = FakePopen(exit_code=None)
        monkeypatch.setattr(cg.subprocess, "Popen", lambda *a, **k: child)

        def failed_update(*args, **kwargs):
            child.returncode = 0
            return access.subprocess.CompletedProcess(
                args=["docker", *args], returncode=1, stdout="", stderr="already exited",
            )

        monkeypatch.setattr(access, "_docker", failed_update)
        result = cg._launch_helper(str(tmp_path / "run-exits-during-update"), ["--run-dir", "x"], None)

        assert result is child
        assert child.terminated is False

    def test_helper_returned_when_it_finishes_at_retry_deadline(self, monkeypatch, tmp_path: Path):
        spec = access.HelperSpec(
            image="cgprofile-self:local", repo_host_path="/host/repo", repo_mount_path=cg.HERE,
            out_host_path="/host/out", out_mount_path=str(tmp_path),
            cgroup_parent="dev-interactive.slice",
        )
        monkeypatch.setattr(access, "build_helper_spec", lambda *a, **k: spec)
        monkeypatch.setattr(access, "docker_bin", lambda: "/usr/bin/docker")
        child = FakePopen(exit_code=None)
        monkeypatch.setattr(cg.subprocess, "Popen", lambda *a, **k: child)
        monkeypatch.setattr(
            access, "_docker",
            lambda *a, **k: access.subprocess.CompletedProcess(
                args=["docker", *a], returncode=1, stdout="", stderr="not found",
            ),
        )
        sleeps = []

        def finish_on_final_retry(_seconds):
            sleeps.append(None)
            if len(sleeps) == 5:
                child.returncode = 0

        monkeypatch.setattr(cg.time, "sleep", finish_on_final_retry)
        result = cg._launch_helper(str(tmp_path / "run-finishes-at-deadline"), ["--run-dir", "x"], None)

        assert result is child
        assert child.terminated is False

    def test_helper_cap_refusal_kills_child_if_termination_times_out(self, monkeypatch, tmp_path: Path):
        spec = access.HelperSpec(
            image="cgprofile-self:local", repo_host_path="/host/repo", repo_mount_path=cg.HERE,
            out_host_path="/host/out", out_mount_path=str(tmp_path),
            cgroup_parent="dev-interactive.slice",
        )
        monkeypatch.setattr(access, "build_helper_spec", lambda *a, **k: spec)
        monkeypatch.setattr(access, "docker_bin", lambda: "/usr/bin/docker")

        class ChildThatIgnoresTerminate(FakePopen):
            def wait(self, timeout=None):
                if timeout is not None:
                    raise subprocess.TimeoutExpired("cgprofile-helper", timeout)
                return 0

        child = ChildThatIgnoresTerminate(exit_code=None)
        monkeypatch.setattr(cg.subprocess, "Popen", lambda *a, **k: child)
        monkeypatch.setattr(
            access, "_docker",
            lambda *a, **k: access.subprocess.CompletedProcess(
                args=["docker", *a], returncode=1, stdout="", stderr="not found",
            ),
        )
        sleeps = []

        def keep_running(_seconds):
            sleeps.append(None)

        monkeypatch.setattr(cg.time, "sleep", keep_running)
        with pytest.raises(SystemExit) as exc_info:
            cg._launch_helper(str(tmp_path / "run-cap-kill"), ["--run-dir", "x"], None)

        assert exc_info.value.code == 2
        assert child.terminated is True
        assert child.killed is True

    def test_helper_refuses_if_running_container_cannot_be_capped(self, monkeypatch, tmp_path: Path):
        spec = access.HelperSpec(
            image="cgprofile-self:local", repo_host_path="/host/repo", repo_mount_path=cg.HERE,
            out_host_path="/host/out", out_mount_path=str(tmp_path),
            cgroup_parent="dev-interactive.slice",
        )
        monkeypatch.setattr(access, "build_helper_spec", lambda *a, **k: spec)
        monkeypatch.setattr(access, "docker_bin", lambda: "/usr/bin/docker")
        child = FakePopen(exit_code=None)
        monkeypatch.setattr(cg.subprocess, "Popen", lambda *a, **k: child)
        calls = []

        def failed_update(*args, **kwargs):
            calls.append(args)
            return access.subprocess.CompletedProcess(
                args=["docker", *args], returncode=1, stdout="", stderr="container not found",
            )

        monkeypatch.setattr(access, "_docker", failed_update)

        with pytest.raises(SystemExit) as exc_info:
            cg._launch_helper(str(tmp_path / "run-cap-refusal"), ["--run-dir", "x"], None)

        assert exc_info.value.code == 2
        assert child.terminated is True
        assert calls[-1][0:2] == ("rm", "--force")
        assert calls[-1][2].startswith("cgprofile-helper-")

    def test_helper_refuses_when_cap_update_transport_raises(self, monkeypatch, tmp_path: Path):
        spec = access.HelperSpec(
            image="cgprofile-self:local", repo_host_path="/host/repo", repo_mount_path=cg.HERE,
            out_host_path="/host/out", out_mount_path=str(tmp_path),
            cgroup_parent="dev-interactive.slice",
        )
        monkeypatch.setattr(access, "build_helper_spec", lambda *a, **k: spec)
        monkeypatch.setattr(access, "docker_bin", lambda: "/usr/bin/docker")
        child = FakePopen(exit_code=None)
        monkeypatch.setattr(cg.subprocess, "Popen", lambda *a, **k: child)

        def fail_update(*args, **kwargs):
            raise OSError("docker unavailable")

        monkeypatch.setattr(access, "_docker", fail_update)
        with pytest.raises(SystemExit) as exc_info:
            cg._launch_helper(str(tmp_path / "run-cap-transport"), ["--run-dir", "x"], None)

        assert exc_info.value.code == 2
        assert child.terminated is True

    def test_cgroup_parent_argument_is_threaded_to_build_helper_spec(self, monkeypatch, tmp_path: Path):
        spec = access.HelperSpec(
            image="img:local", repo_host_path="/h/repo", repo_mount_path=cg.HERE,
            out_host_path="/h/out", out_mount_path=str(tmp_path), cgroup_parent="whatever.slice",
        )
        captured = {}

        def fake_build_helper_spec(repo, out, image, cgroup_parent=None):
            captured["cgroup_parent"] = cgroup_parent
            return spec

        monkeypatch.setattr(access, "build_helper_spec", fake_build_helper_spec)
        monkeypatch.setattr(access, "docker_bin", lambda: "/usr/bin/docker")
        monkeypatch.setattr(cg.subprocess, "Popen", lambda command, **k: FakePopen(command))
        monkeypatch.setattr(
            access,
            "_docker",
            lambda *args, **kwargs: subprocess.CompletedProcess(args, 0),
        )
        run_path = str(tmp_path / "run-2")
        cg._launch_helper(run_path, ["--run-dir", run_path], None, cgroup_parent="my-explicit.slice")
        assert captured["cgroup_parent"] == "my-explicit.slice"


def fake_ready_popen(run_dir_holder: Dict[str, str], exit_code: Optional[int] = 0):
    """A subprocess.Popen replacement that creates READY_FILE (simulating an
    instant collector) as soon as it is "started", so _wait_for never has to
    poll more than once."""

    def _popen(args, **kwargs):
        if "--run-dir" in args:
            run_dir = args[args.index("--run-dir") + 1]
            run_dir_holder["path"] = run_dir
            open(os.path.join(run_dir, cg.READY_FILE), "w").close()
        return FakePopen(args, exit_code=exit_code)

    return _popen


class TestCollectArgsFrom:
    def _args(self, **overrides):
        ns = argparse.Namespace(
            hot_interval=0.25, idle_interval=2.0, discovery_interval=2.0, max_depth=4,
            duration=None, follow_children=False, damon=False, cap=[],
        )
        for key, value in overrides.items():
            setattr(ns, key, value)
        return ns

    def test_minimal_case_has_no_optional_flags(self):
        out = cg._collect_args_from(self._args(), "/run/x", ["cgroup:/a"], [])
        assert "--observe" not in out
        assert "--duration" not in out
        assert "--follow-children" not in out
        assert "--damon" not in out
        assert "--caps" not in out

    def test_observers_are_appended(self):
        out = cg._collect_args_from(self._args(), "/run/x", ["cgroup:/a"], ["cgroup:/b"])
        assert out.count("--observe") == 1
        assert "cgroup:/b" in out

    def test_duration_is_included_when_truthy(self):
        out = cg._collect_args_from(self._args(duration=30.0), "/run/x", ["cgroup:/a"], [])
        assert "--duration" in out
        assert "30.0" in out

    def test_explicit_zero_duration_is_still_passed_through(self):
        # A bare truthiness check on args.duration would treat 0 the same as
        # unset — but SamplerConfig(max_duration=0) is meaningful (Sampler.run
        # breaks after its very first tick), so `attach --duration 0` (take
        # exactly one sample, then stop) must not silently become unbounded.
        out = cg._collect_args_from(self._args(duration=0), "/run/x", ["cgroup:/a"], [])
        assert "--duration" in out
        assert out[out.index("--duration") + 1] == "0"

    def test_unset_duration_is_omitted(self):
        out = cg._collect_args_from(self._args(duration=None), "/run/x", ["cgroup:/a"], [])
        assert "--duration" not in out

    def test_follow_children_flag(self):
        out = cg._collect_args_from(self._args(follow_children=True), "/run/x", ["cgroup:/a"], [])
        assert "--follow-children" in out

    def test_damon_flag(self):
        out = cg._collect_args_from(self._args(damon=True), "/run/x", ["cgroup:/a"], [])
        assert "--damon" in out

    def test_caps_are_serialized_as_json(self):
        args = self._args(cap=["/dev.slice:memory.max=2G"])
        out = cg._collect_args_from(args, "/run/x", ["cgroup:/a"], [])
        assert "--caps" in out
        payload = json.loads(out[out.index("--caps") + 1])
        assert payload == {"/dev.slice": {"memory.max": "2G"}}


class TestStartRun:
    def test_no_target_is_an_error(self, tmp_path: Path):
        args = argparse.Namespace(
            target=[], observe=[], out_dir=str(tmp_path), run_id=None, mode="direct",
            hot_interval=0.25, idle_interval=2.0, discovery_interval=2.0, max_depth=4,
            duration=None, follow_children=False, damon=False, cap=[], helper_image=None,
            start_timeout=5.0,
        )
        with pytest.raises(SystemExit):
            cg._start_run(args)

    def test_empty_resolved_label_is_an_error_before_creating_the_run_dir(
        self, monkeypatch, tmp_path: Path,
    ):
        monkeypatch.setattr(access, "choose_mode", lambda requested: "direct")
        monkeypatch.setattr(targets_mod, "resolve_label", lambda selector: [])
        args = argparse.Namespace(
            target=["label:app=missing"], observe=[], out_dir=str(tmp_path / "runs"),
            run_id=None, mode="auto", hot_interval=0.25, idle_interval=2.0,
            discovery_interval=2.0, max_depth=4, duration=None,
            follow_children=False, damon=False, cap=[], helper_image=None,
            start_timeout=5.0,
        )

        with pytest.raises(SystemExit):
            cg._start_run(args)

        assert not (tmp_path / "runs").exists()

    def test_direct_mode_spawns_a_local_subprocess(self, monkeypatch, tmp_path: Path):
        holder: Dict[str, str] = {}
        monkeypatch.setattr(cg.subprocess, "Popen", fake_ready_popen(holder))
        monkeypatch.setattr(access, "choose_mode", lambda requested: "direct")
        args = argparse.Namespace(
            target=["cgroup:/dev.slice"], observe=[], out_dir=str(tmp_path), run_id="run-direct",
            mode="auto", hot_interval=0.25, idle_interval=2.0, discovery_interval=2.0, max_depth=4,
            duration=None, follow_children=False, damon=False, cap=[], helper_image=None,
            start_timeout=5.0,
        )
        run, child = cg._start_run(args)
        assert run.run_id == "run-direct"
        assert isinstance(child, FakePopen)
        assert os.path.exists(os.path.join(run.path, cg.READY_FILE))

    def test_helper_mode_delegates_to_launch_helper(self, monkeypatch, tmp_path: Path):
        holder: Dict[str, str] = {}
        called = {}

        def fake_launch(run_path, collect_args, image, cgroup_parent=None):
            called["run_path"] = run_path
            called["image"] = image
            called["cgroup_parent"] = cgroup_parent
            open(os.path.join(run_path, cg.READY_FILE), "w").close()
            return FakePopen(collect_args)

        monkeypatch.setattr(cg, "_launch_helper", fake_launch)
        monkeypatch.setattr(access, "choose_mode", lambda requested: "helper")
        args = argparse.Namespace(
            target=["cgroup:/dev.slice"], observe=[], out_dir=str(tmp_path), run_id="run-helper",
            mode="helper", hot_interval=0.25, idle_interval=2.0, discovery_interval=2.0, max_depth=4,
            duration=None, follow_children=False, damon=False, cap=[], helper_image="img:local",
            helper_cgroup_parent="dev-interactive.slice", start_timeout=5.0,
        )
        run, child = cg._start_run(args)
        assert called["run_path"] == run.path
        assert called["image"] == "img:local"
        assert called["cgroup_parent"] == "dev-interactive.slice"

    def test_ready_wait_timeout_terminates_the_child(self, monkeypatch, tmp_path: Path):
        # Regression test: if the collector never signals ready within
        # start_timeout, _wait_for raises SystemExit before _start_run ever
        # returns a `child` to the caller — without cleanup here, the
        # spawned collector process (or, in helper mode, a whole privileged
        # container) would be left running forever with nothing left
        # holding a reference to stop it.
        spawned = {}

        def never_ready_popen(*args, **kwargs):
            child = FakePopen()
            spawned["child"] = child
            return child

        monkeypatch.setattr(cg.subprocess, "Popen", never_ready_popen)
        monkeypatch.setattr(access, "choose_mode", lambda requested: "direct")
        clock = {"t": 0.0}
        monkeypatch.setattr(cg.time, "monotonic", lambda: clock["t"])
        monkeypatch.setattr(cg.time, "sleep", lambda dt: clock.__setitem__("t", clock["t"] + dt))
        args = argparse.Namespace(
            target=["cgroup:/dev.slice"], observe=[], out_dir=str(tmp_path), run_id="run-never-ready",
            mode="auto", hot_interval=0.25, idle_interval=2.0, discovery_interval=2.0, max_depth=4,
            duration=None, follow_children=False, damon=False, cap=[], helper_image=None,
            start_timeout=1.0,
        )
        with pytest.raises(SystemExit):
            cg._start_run(args)

        assert spawned["child"].terminated is True

    def test_ready_wait_timeout_kills_child_when_terminate_does_not_land_in_time(
        self, monkeypatch, tmp_path: Path
    ):
        # Same leak as above, one step further: terminate() alone is not
        # always enough (a wedged process can ignore SIGTERM), so the
        # cleanup's own wait(timeout=10) must fall back to kill() rather
        # than leaving the process running because ITS wait blocked too.
        class StubbornPopen(FakePopen):
            def wait(self, timeout=None):
                if timeout is not None and not self.killed:
                    raise cg.subprocess.TimeoutExpired(cmd="collector", timeout=timeout)
                return super().wait(timeout)

        spawned = {}

        def never_ready_popen(*args, **kwargs):
            child = StubbornPopen()
            spawned["child"] = child
            return child

        monkeypatch.setattr(cg.subprocess, "Popen", never_ready_popen)
        monkeypatch.setattr(access, "choose_mode", lambda requested: "direct")
        clock = {"t": 0.0}
        monkeypatch.setattr(cg.time, "monotonic", lambda: clock["t"])
        monkeypatch.setattr(cg.time, "sleep", lambda dt: clock.__setitem__("t", clock["t"] + dt))
        args = argparse.Namespace(
            target=["cgroup:/dev.slice"], observe=[], out_dir=str(tmp_path), run_id="run-stubborn",
            mode="auto", hot_interval=0.25, idle_interval=2.0, discovery_interval=2.0, max_depth=4,
            duration=None, follow_children=False, damon=False, cap=[], helper_image=None,
            start_timeout=1.0,
        )
        with pytest.raises(SystemExit):
            cg._start_run(args)

        assert spawned["child"].terminated is True
        assert spawned["child"].killed is True

    def test_helper_mode_with_no_cgroup_parent_attribute_passes_none(self, monkeypatch, tmp_path: Path):
        # _add_common always defines --helper-cgroup-parent, but _start_run
        # reads it via getattr(..., None) so it degrades gracefully if a
        # caller ever builds args without it.
        called = {}

        def fake_launch(run_path, collect_args, image, cgroup_parent=None):
            called["cgroup_parent"] = cgroup_parent
            open(os.path.join(run_path, cg.READY_FILE), "w").close()
            return FakePopen(collect_args)

        monkeypatch.setattr(cg, "_launch_helper", fake_launch)
        monkeypatch.setattr(access, "choose_mode", lambda requested: "helper")
        args = argparse.Namespace(
            target=["cgroup:/dev.slice"], observe=[], out_dir=str(tmp_path), run_id="run-helper-2",
            mode="helper", hot_interval=0.25, idle_interval=2.0, discovery_interval=2.0, max_depth=4,
            duration=None, follow_children=False, damon=False, cap=[], helper_image=None,
            start_timeout=5.0,
        )
        cg._start_run(args)
        assert called["cgroup_parent"] is None


# ── cmd_run ───────────────────────────────────────────────────────────────

def run_args(command, **overrides) -> argparse.Namespace:
    ns = argparse.Namespace(
        command=command, target=["cgroup:/dev.slice"], observe=[], out_dir="/tmp/x",
        run_id=None, mode="auto", hot_interval=0.25, idle_interval=2.0, discovery_interval=2.0,
        max_depth=4, duration=None, follow_children=False, damon=False, cap=[],
        helper_image=None, start_timeout=5.0, phase=None, settle=0.0, no_report=True,
        html_only=False, md_only=False, plotlyjs="inline",
    )
    for key, value in overrides.items():
        setattr(ns, key, value)
    return ns


class TestCmdRun:
    def test_no_command_is_an_error(self):
        with pytest.raises(SystemExit):
            cg.cmd_run(run_args([]))

    def test_wraps_the_command_and_propagates_its_exit_code(self, monkeypatch, tmp_path: Path):
        run = store_lib.RunDir(str(tmp_path), run_id="run-wrap")
        child = FakePopen()
        monkeypatch.setattr(cg, "_start_run", lambda args: (run, child))
        monkeypatch.setattr(cg, "_stop_run", lambda run_, child_, timeout=30.0: None)

        captured = {}

        def fake_subprocess_run(command, env=None, check=False):
            captured["command"] = command
            captured["CGPROFILE_RUN_DIR"] = env.get("CGPROFILE_RUN_DIR")
            return subprocess.CompletedProcess(command, returncode=17)

        monkeypatch.setattr(cg.subprocess, "run", fake_subprocess_run)
        args = run_args(["echo", "hi"])
        assert cg.cmd_run(args) == 17
        assert captured["command"] == ["echo", "hi"]
        assert captured["CGPROFILE_RUN_DIR"] == run.path
        marks = list(run.read("marks"))
        assert marks[0]["name"] == "echo"  # default phase label from argv[0]
        assert marks[0]["meta"]["argv"] == ["echo", "hi"]
        assert marks[1]["name"] == "echo:done"
        assert marks[1]["meta"]["exit_code"] == 17

    def test_command_launch_failure_still_stops_the_collector(self, monkeypatch, tmp_path: Path):
        # Regression test: subprocess.run's check=False only governs a
        # nonzero exit code, not a failed exec (bad command, no permission)
        # — that raises OSError, which used to propagate straight out of
        # cmd_run and skip _stop_run entirely, leaking the collector process
        # (or, in helper mode, a whole privileged container) forever.
        run = store_lib.RunDir(str(tmp_path), run_id="run-launch-fail")
        child = FakePopen()
        monkeypatch.setattr(cg, "_start_run", lambda args: (run, child))
        stopped = {"run": False, "tailers": False}
        monkeypatch.setattr(
            cg, "_stop_run",
            lambda run_, child_, timeout=30.0: stopped.__setitem__("run", True),
        )
        monkeypatch.setattr(
            cg, "_stop_log_tailers",
            lambda tailers: stopped.__setitem__("tailers", True),
        )

        def raising_subprocess_run(command, env=None, check=False):
            raise FileNotFoundError(2, "No such file or directory", command[0])

        monkeypatch.setattr(cg.subprocess, "run", raising_subprocess_run)
        args = run_args(["no-such-binary"])
        with pytest.raises(SystemExit):
            cg.cmd_run(args)

        assert stopped["run"] is True
        assert stopped["tailers"] is True

    def test_log_tailers_start_after_the_collector_and_stop_before_stop_run(self, monkeypatch, tmp_path: Path):
        FakeLogTailer.instances = []
        monkeypatch.setattr(logtail_lib, "LogTailer", FakeLogTailer)
        run = store_lib.RunDir(str(tmp_path), run_id="run-logtail")
        order = []
        monkeypatch.setattr(cg, "_start_run", lambda args: order.append("start_run") or (run, FakePopen()))
        monkeypatch.setattr(
            cg, "_stop_run", lambda run_, child_, timeout=30.0: order.append("stop_run"),
        )
        real_start_tailers, real_stop_tailers = cg._start_log_tailers, cg._stop_log_tailers
        monkeypatch.setattr(
            cg, "_start_log_tailers",
            lambda args_, path: order.append("start_tailers") or real_start_tailers(args_, path),
        )
        monkeypatch.setattr(
            cg, "_stop_log_tailers",
            lambda tailers: order.append("stop_tailers") or real_stop_tailers(tailers),
        )

        def fake_subprocess_run(command, env=None, check=False):
            order.append("wrapped_command")
            return subprocess.CompletedProcess(command, returncode=0)

        monkeypatch.setattr(cg.subprocess, "run", fake_subprocess_run)
        args = run_args(["echo", "hi"], log_tail=["container:c1"], log_match=["a=hello"])
        assert cg.cmd_run(args) == 0
        assert order == ["start_run", "start_tailers", "wrapped_command", "stop_tailers", "stop_run"]
        assert len(FakeLogTailer.instances) == 1
        tailer = FakeLogTailer.instances[0]
        assert tailer.started is True
        assert tailer.stopped is True

    def test_explicit_phase_name_overrides_the_default(self, monkeypatch, tmp_path: Path):
        run = store_lib.RunDir(str(tmp_path), run_id="run-phase")
        monkeypatch.setattr(cg, "_start_run", lambda args: (run, FakePopen()))
        monkeypatch.setattr(cg, "_stop_run", lambda run_, child_, timeout=30.0: None)
        monkeypatch.setattr(cg.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 0))
        args = run_args(["echo", "hi"], phase="custom-phase")
        cg.cmd_run(args)
        marks = list(run.read("marks"))
        assert marks[0]["name"] == "custom-phase"

    def test_settle_emits_a_settle_mark_and_never_sleeps_in_real_time(self, monkeypatch, tmp_path: Path):
        run = store_lib.RunDir(str(tmp_path), run_id="run-settle")
        monkeypatch.setattr(cg, "_start_run", lambda args: (run, FakePopen()))
        monkeypatch.setattr(cg, "_stop_run", lambda run_, child_, timeout=30.0: None)
        monkeypatch.setattr(cg.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 0))
        sleeps = []
        monkeypatch.setattr(cg.time, "sleep", lambda dt: sleeps.append(dt))
        args = run_args(["echo", "hi"], settle=3.0)
        cg.cmd_run(args)
        assert sleeps == [3.0]
        marks = list(run.read("marks"))
        assert any(m["name"] == "settle" for m in marks)

    def test_zero_settle_skips_the_settle_mark(self, monkeypatch, tmp_path: Path):
        run = store_lib.RunDir(str(tmp_path), run_id="run-nosettle")
        monkeypatch.setattr(cg, "_start_run", lambda args: (run, FakePopen()))
        monkeypatch.setattr(cg, "_stop_run", lambda run_, child_, timeout=30.0: None)
        monkeypatch.setattr(cg.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 0))
        args = run_args(["echo", "hi"], settle=0.0)
        cg.cmd_run(args)
        marks = list(run.read("marks"))
        assert not any(m["name"] == "settle" for m in marks)

    def test_report_is_generated_unless_no_report(self, monkeypatch, tmp_path: Path):
        run = store_lib.RunDir(str(tmp_path), run_id="run-report")
        monkeypatch.setattr(cg, "_start_run", lambda args: (run, FakePopen()))
        monkeypatch.setattr(cg, "_stop_run", lambda run_, child_, timeout=30.0: None)
        monkeypatch.setattr(cg.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 0))
        calls = []
        monkeypatch.setattr(cg, "_report", lambda run_path, args_: calls.append(run_path))
        args = run_args(["echo", "hi"], no_report=False)
        cg.cmd_run(args)
        assert calls == [run.path]

    def test_no_report_flag_skips_reporting(self, monkeypatch, tmp_path: Path):
        run = store_lib.RunDir(str(tmp_path), run_id="run-noreport")
        monkeypatch.setattr(cg, "_start_run", lambda args: (run, FakePopen()))
        monkeypatch.setattr(cg, "_stop_run", lambda run_, child_, timeout=30.0: None)
        monkeypatch.setattr(cg.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 0))
        calls = []
        monkeypatch.setattr(cg, "_report", lambda run_path, args_: calls.append(run_path))
        args = run_args(["echo", "hi"], no_report=True)
        cg.cmd_run(args)
        assert calls == []


# ── cmd_attach ────────────────────────────────────────────────────────────

def attach_args(**overrides) -> argparse.Namespace:
    ns = argparse.Namespace(
        target=["cgroup:/dev.slice"], observe=[], out_dir="/tmp/x", run_id=None, mode="auto",
        hot_interval=0.25, idle_interval=2.0, discovery_interval=2.0, max_depth=4, duration=None,
        follow_children=False, damon=False, cap=[], helper_image=None, start_timeout=5.0,
        until_file=None, no_report=True, html_only=False, md_only=False, plotlyjs="inline",
    )
    for key, value in overrides.items():
        setattr(ns, key, value)
    return ns


class TestCmdAttach:
    def test_stops_when_the_child_exits_on_its_own(self, monkeypatch, tmp_path: Path):
        run = store_lib.RunDir(str(tmp_path), run_id="run-attach-exit")
        child = FakePopen(exit_code=0)
        monkeypatch.setattr(cg, "_start_run", lambda args: (run, child))
        stop_calls = []
        monkeypatch.setattr(cg, "_stop_run", lambda run_, child_, timeout=30.0: stop_calls.append(1))
        assert cg.cmd_attach(attach_args()) == 0
        assert stop_calls == [1]

    def test_log_tailers_are_started_and_stopped_around_the_attach_loop(self, monkeypatch, tmp_path: Path):
        FakeLogTailer.instances = []
        monkeypatch.setattr(logtail_lib, "LogTailer", FakeLogTailer)
        run = store_lib.RunDir(str(tmp_path), run_id="run-attach-logtail")
        child = FakePopen(exit_code=0)
        monkeypatch.setattr(cg, "_start_run", lambda args: (run, child))
        monkeypatch.setattr(cg, "_stop_run", lambda run_, child_, timeout=30.0: None)
        args = attach_args(log_tail=["container:c1"], log_match=["a=hello"])
        assert cg.cmd_attach(args) == 0
        assert len(FakeLogTailer.instances) == 1
        tailer = FakeLogTailer.instances[0]
        assert tailer.started is True
        assert tailer.stopped is True

    def test_stops_when_the_duration_deadline_is_reached_without_real_sleeping(self, monkeypatch, tmp_path: Path):
        run = store_lib.RunDir(str(tmp_path), run_id="run-attach-duration")
        child = FakePopen(exit_code=None)  # never exits on its own
        monkeypatch.setattr(cg, "_start_run", lambda args: (run, child))
        monkeypatch.setattr(cg, "_stop_run", lambda run_, child_, timeout=30.0: None)
        clock = {"t": 0.0}
        monkeypatch.setattr(cg.time, "monotonic", lambda: clock["t"])
        monkeypatch.setattr(cg.time, "sleep", lambda dt: clock.__setitem__("t", clock["t"] + dt))
        assert cg.cmd_attach(attach_args(duration=2.0)) == 0
        assert clock["t"] >= 2.0

    def test_stops_when_the_until_file_appears(self, monkeypatch, tmp_path: Path):
        run = store_lib.RunDir(str(tmp_path), run_id="run-attach-untilfile")
        child = FakePopen(exit_code=None)
        monkeypatch.setattr(cg, "_start_run", lambda args: (run, child))
        monkeypatch.setattr(cg, "_stop_run", lambda run_, child_, timeout=30.0: None)
        until_file = tmp_path / "stop-here"

        calls = {"n": 0}

        def fake_sleep(dt):
            calls["n"] += 1
            if calls["n"] >= 2:
                until_file.write_text("x")

        monkeypatch.setattr(cg.time, "sleep", fake_sleep)
        assert cg.cmd_attach(attach_args(until_file=str(until_file))) == 0

    def test_keyboard_interrupt_is_caught_and_still_stops_the_collector(self, monkeypatch, tmp_path: Path, capsys):
        run = store_lib.RunDir(str(tmp_path), run_id="run-attach-interrupt")
        child = FakePopen(exit_code=None)
        monkeypatch.setattr(cg, "_start_run", lambda args: (run, child))
        stop_calls = []
        monkeypatch.setattr(cg, "_stop_run", lambda run_, child_, timeout=30.0: stop_calls.append(1))

        def raise_keyboard_interrupt(dt):
            raise KeyboardInterrupt()

        monkeypatch.setattr(cg.time, "sleep", raise_keyboard_interrupt)
        assert cg.cmd_attach(attach_args()) == 0
        assert stop_calls == [1]
        assert "interrupted" in capsys.readouterr().err

    def test_report_is_generated_unless_no_report(self, monkeypatch, tmp_path: Path):
        run = store_lib.RunDir(str(tmp_path), run_id="run-attach-report")
        child = FakePopen(exit_code=0)
        monkeypatch.setattr(cg, "_start_run", lambda args: (run, child))
        monkeypatch.setattr(cg, "_stop_run", lambda run_, child_, timeout=30.0: None)
        calls = []
        monkeypatch.setattr(cg, "_report", lambda run_path, args_: calls.append(run_path))
        cg.cmd_attach(attach_args(no_report=False))
        assert calls == [run.path]


# ── _report / cmd_report ───────────────────────────────────────────────────

class TestReport:
    """_report does a local ``from lib import analyze, report_html, report_md``.

    Once any other test module in the session has really imported
    ``lib.analyze`` (etc.), the ``lib`` package caches it as an attribute,
    and a ``from lib import analyze`` elsewhere resolves via that cached
    attribute rather than re-consulting ``sys.modules`` — so patching only
    ``sys.modules["lib.analyze"]`` is invisible to ``_report`` whenever the
    full suite (not just this file) has run first. Patching the attribute
    on the real module object works either way.
    """

    def test_renders_both_by_default(self, monkeypatch, tmp_path: Path):
        run_path = str(tmp_path / "run-both")
        os.makedirs(run_path)
        monkeypatch.setattr(cg, "_require_reporting_deps", lambda: None)
        calls = []
        monkeypatch.setattr(analyze_lib, "build", lambda run: "ANALYSIS")
        monkeypatch.setattr(
            report_html_lib, "render",
            lambda analysis, out_path, plotlyjs="inline": calls.append(("html", out_path)) or out_path,
        )
        monkeypatch.setattr(
            report_md_lib, "render",
            lambda analysis, out_dir: calls.append(("md", out_dir)) or (out_dir + "/report.md"),
        )
        args = argparse.Namespace(md_only=False, html_only=False, plotlyjs="directory")
        cg._report(run_path, args)
        assert ("html", os.path.join(run_path, "report.html")) in calls
        assert ("md", run_path) in calls

    def test_md_only_skips_html(self, monkeypatch, tmp_path: Path):
        run_path = str(tmp_path / "run-md")
        os.makedirs(run_path)
        monkeypatch.setattr(cg, "_require_reporting_deps", lambda: None)
        calls = []
        monkeypatch.setattr(analyze_lib, "build", lambda run: "A")
        monkeypatch.setattr(report_html_lib, "render", lambda *a, **k: calls.append("html") or "x")
        monkeypatch.setattr(report_md_lib, "render", lambda *a, **k: calls.append("md") or "x")
        args = argparse.Namespace(md_only=True, html_only=False)
        cg._report(run_path, args)
        assert calls == ["md"]

    def test_html_only_skips_md(self, monkeypatch, tmp_path: Path):
        run_path = str(tmp_path / "run-html")
        os.makedirs(run_path)
        monkeypatch.setattr(cg, "_require_reporting_deps", lambda: None)
        calls = []
        monkeypatch.setattr(analyze_lib, "build", lambda run: "A")
        monkeypatch.setattr(report_html_lib, "render", lambda *a, **k: calls.append("html") or "x")
        monkeypatch.setattr(report_md_lib, "render", lambda *a, **k: calls.append("md") or "x")
        args = argparse.Namespace(md_only=False, html_only=True)
        cg._report(run_path, args)
        assert calls == ["html"]


class TestCmdReport:
    def test_explicit_run_dir_is_used_directly(self, monkeypatch, tmp_path: Path):
        run_path = str(tmp_path / "run-explicit")
        os.makedirs(run_path)
        calls = []
        monkeypatch.setattr(cg, "_report", lambda path, args_: calls.append(path))
        args = argparse.Namespace(run_dir=run_path, out_dir=str(tmp_path))
        assert cg.cmd_report(args) == 0
        assert calls == [run_path]

    def test_no_run_dir_uses_the_latest_run(self, monkeypatch, tmp_path: Path):
        base = tmp_path / "runs"
        base.mkdir()
        store_lib.RunDir(str(base), run_id="run-20260101-000000-aaaa")
        latest_dir = store_lib.RunDir(str(base), run_id="run-20260804-120000-bbbb").path
        calls = []
        monkeypatch.setattr(cg, "_report", lambda path, args_: calls.append(path))
        args = argparse.Namespace(run_dir=None, out_dir=str(base))
        cg.cmd_report(args)
        assert calls == [latest_dir]

    def test_no_run_dir_and_no_runs_at_all_is_an_error(self, tmp_path: Path):
        args = argparse.Namespace(run_dir=None, out_dir=str(tmp_path / "empty"))
        with pytest.raises(SystemExit):
            cg.cmd_report(args)


# ── cmd_targets ──────────────────────────────────────────────────────────

def targets_args(**overrides) -> argparse.Namespace:
    ns = argparse.Namespace(
        target=["cgroup:/dev.slice/dev-background.slice"], mode="direct", follow_children=False,
        max_depth=4, helper_image=None, helper_cgroup_parent=None,
    )
    for key, value in overrides.items():
        setattr(ns, key, value)
    return ns


class TestCmdTargets:
    def test_nothing_resolved_is_an_error(self, monkeypatch, cgroup_root: Path):
        monkeypatch.setattr(access, "CGROUP_ROOT", str(cgroup_root))
        monkeypatch.setattr(access, "choose_mode", lambda requested: "direct")
        with pytest.raises(SystemExit):
            cg.cmd_targets(targets_args(target=[]))

    def test_direct_mode_prints_resolved_targets_and_their_limits(self, monkeypatch, cgroup_root: Path, capsys):
        monkeypatch.setattr(access, "CGROUP_ROOT", str(cgroup_root))
        monkeypatch.setattr(access, "choose_mode", lambda requested: "direct")
        monkeypatch.setattr(limits_lib, "mount_flags", lambda *a, **k: set())
        assert cg.cmd_targets(targets_args()) == 0
        out = capsys.readouterr().out
        assert "dev.slice/dev-background.slice" in out or "dev-background.slice" in out
        assert "cgroup:" in out

    def test_direct_mode_lists_up_to_twenty_extra_descendants(self, monkeypatch, cgroup_root: Path, capsys):
        from tests.conftest import cgroup_files, write_cgroup

        for i in range(5):
            write_cgroup(cgroup_root, f"dev.slice/dev-background.slice/extra-{i}", cgroup_files())
        monkeypatch.setattr(access, "CGROUP_ROOT", str(cgroup_root))
        monkeypatch.setattr(access, "choose_mode", lambda requested: "direct")
        monkeypatch.setattr(limits_lib, "mount_flags", lambda *a, **k: set())
        cg.cmd_targets(targets_args(follow_children=True))
        out = capsys.readouterr().out
        assert "descendant cgroups followed" in out
        assert "more" not in out  # 5 + the pre-existing docker scope < 20

    def test_direct_mode_truncates_past_twenty_extra_descendants(self, monkeypatch, cgroup_root: Path, capsys):
        from tests.conftest import cgroup_files, write_cgroup

        for i in range(25):
            write_cgroup(cgroup_root, f"dev.slice/dev-background.slice/extra-{i}", cgroup_files())
        monkeypatch.setattr(access, "CGROUP_ROOT", str(cgroup_root))
        monkeypatch.setattr(access, "choose_mode", lambda requested: "direct")
        monkeypatch.setattr(limits_lib, "mount_flags", lambda *a, **k: set())
        cg.cmd_targets(targets_args(follow_children=True))
        out = capsys.readouterr().out
        assert "more" in out

    def test_helper_mode_delegates_through_a_docker_run_of_itself(self, monkeypatch, tmp_path: Path):
        monkeypatch.setattr(access, "choose_mode", lambda requested: "helper")
        monkeypatch.setattr(targets_mod, "docker_bin", lambda: None)  # keep _predigest_specs hermetic
        spec = access.HelperSpec(
            image="img:local", repo_host_path="/h/repo", repo_mount_path=cg.HERE,
            out_host_path="/h/out", out_mount_path=cg.HERE, cgroup_parent="dev-interactive.slice",
        )
        captured_spec_call = {}

        def fake_build_helper_spec(repo, out, image, cgroup_parent=None):
            captured_spec_call["repo"] = repo
            captured_spec_call["out"] = out
            captured_spec_call["cgroup_parent"] = cgroup_parent
            return spec

        monkeypatch.setattr(access, "build_helper_spec", fake_build_helper_spec)
        monkeypatch.setattr(access, "docker_bin", lambda: "/usr/bin/docker")
        captured = {}
        child = FakePopen(exit_code=3)

        def fake_popen(command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            return child

        monkeypatch.setattr(cg.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(
            access, "_docker",
            lambda *a, **k: subprocess.CompletedProcess(a, returncode=1),
        )
        result = cg.cmd_targets(targets_args(
            mode="helper", target=["cgroup:/dev.slice"], helper_cgroup_parent="dev-interactive.slice",
        ))
        assert result == 3
        assert captured["kwargs"] == {"stdout": None, "stderr": None}
        assert "--target" in captured["command"]
        assert "targets" in captured["command"]
        assert "--cgroup-parent=dev-interactive.slice" in captured["command"]
        assert "--name" in captured["command"]
        assert captured["command"][captured["command"].index("--name") + 1].startswith(
            "cgprofile-targets-"
        )
        assert captured_spec_call["cgroup_parent"] == "dev-interactive.slice"
        # RW-3 regression guard: the helper spec's *output* mount must be
        # DEFAULT_OUT (cgprofile.py's runs/ dir), not HERE (the repo dir
        # itself) — `cmd_targets` never sampled anything before, but a wrong
        # `out` mount here would corrupt the repo checkout the moment this
        # verb grows an on-disk artifact. `repo` is still HERE (the code the
        # helper needs to run `cgprofile.py targets --mode direct`).
        assert captured_spec_call["repo"] == cg.HERE
        assert captured_spec_call["out"] == cg.DEFAULT_OUT
        assert captured_spec_call["out"] != cg.HERE

    def test_helper_mode_applies_live_cpu_cap_to_named_probe(self, monkeypatch):
        monkeypatch.setattr(access, "choose_mode", lambda requested: "helper")
        monkeypatch.setattr(targets_mod, "docker_bin", lambda: None)
        spec = access.HelperSpec(
            image="img:local", repo_host_path="/h/repo", repo_mount_path=cg.HERE,
            out_host_path="/h/out", out_mount_path=cg.HERE,
            cgroup_parent="dev-interactive.slice",
        )
        monkeypatch.setattr(access, "build_helper_spec", lambda *a, **k: spec)
        child = FakePopen(exit_code=None)
        captured = {}

        def fake_popen(command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            return child

        monkeypatch.setattr(cg.subprocess, "Popen", fake_popen)

        def fake_docker(*args, timeout=30):
            captured["docker"] = args
            captured["timeout"] = timeout
            return subprocess.CompletedProcess(args, returncode=0)

        monkeypatch.setattr(access, "_docker", fake_docker)
        assert cg.cmd_targets(targets_args(mode="helper")) == 0
        name = captured["command"][captured["command"].index("--name") + 1]
        assert captured["docker"] == ("update", "--cpus=3", name)
        assert captured["timeout"] == 2


# ── cmd_doctor ───────────────────────────────────────────────────────────

class TestCmdDoctor:
    def _base_access_info(self, host_cgroup_view: bool) -> Dict[str, Any]:
        return {
            "in_helper": False, "in_container": True, "host_cgroup_view": host_cgroup_view,
            "cgroup_writable": False, "damon_sysfs": False, "damon_writable": False,
            "docker": "/usr/bin/docker", "uid": 0,
        }

    def test_direct_mode_never_touches_the_helper_spec(self, monkeypatch, capsys):
        monkeypatch.setattr(access, "describe_access", lambda: self._base_access_info(True))
        # _venv_python left real: this venv genuinely exists in the repo.
        assert cg.cmd_doctor(argparse.Namespace(helper_image=None)) == 0
        out = capsys.readouterr().out
        assert "resolved mode          direct" in out
        assert "helper image" not in out

    def test_reporting_venv_ok_when_libraries_import_in_this_interpreter(self, monkeypatch, capsys):
        monkeypatch.setattr(access, "describe_access", lambda: self._base_access_info(True))
        monkeypatch.setattr(cg, "_venv_python", lambda: "/x/venv/bin/python")
        cg.cmd_doctor(argparse.Namespace(helper_image=None))
        assert "ok" in capsys.readouterr().out

    def test_reporting_venv_present_but_this_interpreter_lacks_the_libraries(self, monkeypatch, capsys):
        monkeypatch.setattr(access, "describe_access", lambda: self._base_access_info(True))
        monkeypatch.setattr(cg, "_venv_python", lambda: "/x/venv/bin/python")
        monkeypatch.setitem(sys.modules, "pandas", None)
        cg.cmd_doctor(argparse.Namespace(helper_image=None))
        out = capsys.readouterr().out
        assert "present but this interpreter lacks the libraries" in out
        assert "/x/venv/bin/python" in out

    def test_reporting_venv_missing_tells_the_user_to_run_setup(self, monkeypatch, capsys):
        monkeypatch.setattr(access, "describe_access", lambda: self._base_access_info(True))
        monkeypatch.setattr(cg, "_venv_python", lambda: None)
        cg.cmd_doctor(argparse.Namespace(helper_image=None))
        assert "missing" in capsys.readouterr().out

    def test_helper_mode_prints_the_resolved_spec(self, monkeypatch, capsys):
        monkeypatch.setattr(access, "describe_access", lambda: self._base_access_info(False))
        monkeypatch.setattr(cg, "_venv_python", lambda: None)
        spec = access.HelperSpec(
            image="img:local", repo_host_path="/h/repo", repo_mount_path=cg.HERE,
            out_host_path="/h/out", out_mount_path=cg.DEFAULT_OUT,
            cgroup_parent="dev-interactive.slice",
        )
        captured = {}

        def fake_build_helper_spec(repo, out, image, cgroup_parent=None):
            captured["cgroup_parent"] = cgroup_parent
            return spec

        monkeypatch.setattr(access, "build_helper_spec", fake_build_helper_spec)
        args = argparse.Namespace(helper_image=None, helper_cgroup_parent="explicit.slice")
        assert cg.cmd_doctor(args) == 0
        out = capsys.readouterr().out
        assert "resolved mode          helper" in out
        assert "img:local" in out
        assert "placed in            dev-interactive.slice" in out
        assert captured["cgroup_parent"] == "explicit.slice"

    def test_helper_mode_with_no_cgroup_parent_attribute_passes_none(self, monkeypatch, capsys):
        # doctor_parser always defines --helper-cgroup-parent, but the read
        # goes through getattr(..., None) the same way _start_run's does.
        monkeypatch.setattr(access, "describe_access", lambda: self._base_access_info(False))
        monkeypatch.setattr(cg, "_venv_python", lambda: None)
        spec = access.HelperSpec(
            image="img:local", repo_host_path="/h/repo", repo_mount_path=cg.HERE,
            out_host_path="/h/out", out_mount_path=cg.DEFAULT_OUT,
            cgroup_parent="dev-interactive.slice",
        )
        captured = {}

        def fake_build_helper_spec(repo, out, image, cgroup_parent=None):
            captured["cgroup_parent"] = cgroup_parent
            return spec

        monkeypatch.setattr(access, "build_helper_spec", fake_build_helper_spec)
        cg.cmd_doctor(argparse.Namespace(helper_image=None))
        assert captured["cgroup_parent"] is None

    def test_helper_mode_unavailable_reports_why_and_returns_one(self, monkeypatch, capsys):
        monkeypatch.setattr(access, "describe_access", lambda: self._base_access_info(False))
        monkeypatch.setattr(cg, "_venv_python", lambda: None)

        def raise_access_error(repo, out, image, cgroup_parent=None):
            raise access.AccessError("no helper image found")

        monkeypatch.setattr(access, "build_helper_spec", raise_access_error)
        assert cg.cmd_doctor(argparse.Namespace(helper_image=None)) == 1
        assert "helper unavailable" in capsys.readouterr().out


# ── manifest limit snapshots (added during integration) ─────────────────────

class TestLimitsSnapshot:
    """The manifest is the only channel between the collector and the
    analyser, so what it records about limits is a contract, not a detail.
    Writing only the prose form left every real run producing no proposals.
    """

    @staticmethod
    def _eff():
        from lib import limits

        return limits.Effective(
            cgroup="/dev.slice/dev-background.slice",
            chain=[],
            memory_max=8 * 1024**3, memory_max_by="/dev.slice/dev-background.slice",
            memory_high=6 * 1024**3, memory_high_by="/dev.slice/dev-background.slice",
            memory_swap_max=48 * 1024**3, memory_swap_max_by="/dev.slice/dev-background.slice",
            strict_min=0, recursive_min=0, strict_low=0, recursive_low=0,
            protection_mode="strict",
            cpu_cores=None, cpu_cores_by=None,
            io_max={}, io_max_by={},
            pids_max=None,
            warnings=["something was discarded"],
        )

    def test_snapshot_has_all_three_forms(self):
        from lib import limits
        import cgprofile

        snap = cgprofile._limits_snapshot(limits, self._eff())
        assert set(snap) == {"resolved", "described", "fingerprint"}
        assert isinstance(snap["described"], list)
        assert isinstance(snap["fingerprint"], str)

    def test_resolved_carries_the_scalars_a_proposal_needs(self):
        from lib import limits
        import cgprofile

        resolved = cgprofile._limits_snapshot(limits, self._eff())["resolved"]
        assert resolved["memory_max"] == 8 * 1024**3
        assert resolved["memory_high_by"] == "/dev.slice/dev-background.slice"
        assert resolved["protection_mode"] == "strict"
        assert resolved["warnings"] == ["something was discarded"]

    def test_chain_is_excluded_because_it_dominates_the_manifest(self):
        from lib import limits
        import cgprofile

        assert "chain" not in cgprofile._limits_snapshot(limits, self._eff())["resolved"]

    def test_snapshot_is_json_serialisable(self):
        # It goes straight into manifest.json; a non-serialisable value here
        # loses the whole manifest, and with it the run.
        import json
        from lib import limits
        import cgprofile

        json.dumps(cgprofile._limits_snapshot(limits, self._eff()))

    def test_analyze_can_actually_read_what_we_write(self):
        """The end-to-end point: the shape the writer emits is the shape the
        proposal generator consumes. These two were mismatched, and nothing
        failed — proposals just silently never appeared."""
        from lib import analyze, limits
        from lib.model import Analysis
        import cgprofile

        snap = cgprofile._limits_snapshot(limits, self._eff())
        analysis = Analysis(limits={"/dev.slice/dev-background.slice": snap})
        got = analyze._effective_limits(analysis)
        assert got, "analyze could not read the writer's own output"
        assert got["/dev.slice/dev-background.slice"]["memory_max"] == 8 * 1024**3


# ── serve / ctl: parser, defaults, the socket client (RG-55 C5) ──────────

class TestDefaultsDriftGuard:
    """`cgprofile.py`'s own DEFAULT_CTL_SOCKET/DEFAULT_CGPROFILE_SESSIONS are
    literals, not imports from lib.serve (see that constant's own comment:
    build_parser() should not have to import lib.serve just to read a
    default) — this is the guard against the two drifting apart, the same
    "lock file, not a second source of truth" shape pyproject.toml already
    uses for requirements.txt."""

    def test_ctl_socket_matches_lib_serve(self):
        assert cg.DEFAULT_CTL_SOCKET == serve_lib.DEFAULT_SOCKET_PATH

    def test_sessions_dir_matches_lib_serve(self):
        assert cg.DEFAULT_CGPROFILE_SESSIONS == serve_lib.DEFAULT_SESSIONS_DIR


class TestBuildParserServeCtl:
    def test_serve_defaults(self):
        args = cg.build_parser().parse_args(["serve"])
        assert args.func is cg.cmd_serve
        assert args.sessions == cg.DEFAULT_CGPROFILE_SESSIONS
        assert args.socket == cg.DEFAULT_CTL_SOCKET
        assert args.damon_default == "on"
        assert args.interval == 1.0
        assert args.keep_sessions == 200
        assert args.keep_days == 14
        assert args.observe_slices == ""
        assert args.max_sessions == 16

    def test_serve_refuses_cap_structurally(self):
        # No --cap flag exists on this parser at all — argparse itself
        # rejects it (exit 2), never a runtime check inside cmd_serve.
        with pytest.raises(SystemExit):
            cg.build_parser().parse_args(["serve", "--cap", "x:memory.max=1G"])

    def test_serve_has_no_common_target_observe_flags(self):
        # `_add_common` (the run/attach/targets flags, incl. --cap) was
        # deliberately never called for `serve` — this is the structural
        # half of "refuses --cap" the handoff describes.
        args = cg.build_parser().parse_args(["serve"])
        assert not hasattr(args, "target")
        assert not hasattr(args, "cap")

    def test_ctl_requires_a_verb(self):
        with pytest.raises(SystemExit):
            cg.build_parser().parse_args(["ctl"])

    def test_ctl_json_flag_and_socket_default(self):
        args = cg.build_parser().parse_args(["ctl", "version"])
        assert args.func is cg.cmd_ctl
        assert args.socket == cg.DEFAULT_CTL_SOCKET
        assert args.json is False
        args2 = cg.build_parser().parse_args(["ctl", "--json", "host"])
        assert args2.json is True

    def test_ctl_json_and_socket_work_in_every_position(self):
        # RG55-INTERFACE-CONTRACT.md's own examples always place --json
        # AFTER the verb (`ctl version --json`) — this is the exact bug a
        # naive `parents=[_ctl_common]` mixin introduced (the child verb
        # subparser's own default silently clobbered a LEADING flag the
        # parent `ctl` parser had already set) and `default=SUPPRESS`
        # fixed; pin every position so it cannot regress silently.
        parser = cg.build_parser()
        assert parser.parse_args(["ctl", "version", "--json"]).json is True
        assert parser.parse_args(["ctl", "--json", "version"]).json is True
        leading = parser.parse_args(["ctl", "--socket", "/tmp/leading.sock", "version"])
        assert leading.socket == "/tmp/leading.sock"
        trailing = parser.parse_args(["ctl", "version", "--socket", "/tmp/trailing.sock"])
        assert trailing.socket == "/tmp/trailing.sock"
        # Given at both positions, the more specific (trailing/child) one
        # wins — an arbitrary but documented tie-break, not an accident.
        both = parser.parse_args([
            "ctl", "--socket", "/tmp/leading.sock", "version",
            "--socket", "/tmp/trailing.sock",
        ])
        assert both.socket == "/tmp/trailing.sock"

    def test_ctl_status_session_is_optional(self):
        args = cg.build_parser().parse_args(["ctl", "status"])
        assert args.session is None
        args2 = cg.build_parser().parse_args(["ctl", "status", "s-20260101T000000Z-abcd"])
        assert args2.session == "s-20260101T000000Z-abcd"

    def test_ctl_stop_and_report_require_a_session_positional(self):
        with pytest.raises(SystemExit):
            cg.build_parser().parse_args(["ctl", "stop"])
        with pytest.raises(SystemExit):
            cg.build_parser().parse_args(["ctl", "report"])
        stop_args = cg.build_parser().parse_args(["ctl", "stop", "s-1"])
        assert stop_args.session == "s-1"
        report_args = cg.build_parser().parse_args(["ctl", "report", "s-1"])
        assert report_args.session == "s-1"

    def test_ctl_start_required_and_optional_flags(self):
        args = cg.build_parser().parse_args([
            "ctl", "start", "--target", "containerid:" + "a" * 64,
            "--scope", "container", "--meta", "{}",
        ])
        assert args.token is None
        assert args.damon is None
        assert args.interval is None
        for missing in (
            ["ctl", "start", "--scope", "container", "--meta", "{}"],
            ["ctl", "start", "--target", "containerid:" + "a" * 64, "--meta", "{}"],
            ["ctl", "start", "--target", "containerid:" + "a" * 64, "--scope", "container"],
        ):
            with pytest.raises(SystemExit):
                cg.build_parser().parse_args(missing)

    def test_ctl_start_scope_and_damon_choices_are_enforced(self):
        with pytest.raises(SystemExit):
            cg.build_parser().parse_args([
                "ctl", "start", "--target", "containerid:" + "a" * 64,
                "--scope", "bogus", "--meta", "{}",
            ])
        with pytest.raises(SystemExit):
            cg.build_parser().parse_args([
                "ctl", "start", "--target", "containerid:" + "a" * 64,
                "--scope", "container", "--damon", "bogus", "--meta", "{}",
            ])

    def test_ctl_gc_and_host_take_no_arguments(self):
        assert cg.build_parser().parse_args(["ctl", "gc"]).verb == "gc"
        assert cg.build_parser().parse_args(["ctl", "host"]).verb == "host"


class TestCmdServe:
    def test_refuses_without_a_host_cgroup_view(self, monkeypatch, capsys):
        monkeypatch.setattr(access, "have_host_cgroup_view", lambda root: False)
        args = cg.build_parser().parse_args(["serve"])
        with pytest.raises(SystemExit) as exc_info:
            cg.cmd_serve(args)
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "bind-mounted" in err and "keep the cgroup namespace private" in err

    def test_refuses_without_a_host_proc_view(self, monkeypatch, capsys):
        monkeypatch.setattr(access, "have_host_cgroup_view", lambda root: True)
        monkeypatch.setattr(access, "have_host_proc_view", lambda proc: False)
        args = cg.build_parser().parse_args(["serve"])
        with pytest.raises(SystemExit) as exc_info:
            cg.cmd_serve(args)
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "host /proc tree explicitly bind-mounted read-only" in err
        assert "Keep the PID namespace private" in err

    def test_constructs_the_server_and_serves_forever(self, monkeypatch, tmp_path):
        monkeypatch.setattr(access, "have_host_cgroup_view", lambda root: True)
        monkeypatch.setattr(access, "have_host_proc_view", lambda proc: True)
        captured: Dict[str, Any] = {}

        class FakeServer:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def serve_forever(self):
                captured["served"] = True

        monkeypatch.setattr(serve_lib, "SessionServer", FakeServer)
        args = cg.build_parser().parse_args([
            "serve", "--sessions", str(tmp_path), "--socket", str(tmp_path / "x.sock"),
            "--damon-default", "off", "--interval", "2.5", "--keep-sessions", "5",
            "--keep-days", "1", "--observe-slices", "a.slice, ,b.slice,", "--max-sessions", "3",
        ])
        assert cg.cmd_serve(args) == 0
        assert captured["served"] is True
        assert captured["sessions_dir"] == str(tmp_path)
        assert captured["socket_path"] == str(tmp_path / "x.sock")
        assert captured["damon_default"] == "off"
        assert captured["interval"] == 2.5
        assert captured["keep_sessions"] == 5
        assert captured["keep_days"] == 1
        # Blank/whitespace-only entries (a trailing comma, a lone space) are
        # dropped rather than becoming a bogus "" or " " slice name.
        assert captured["observe_slices"] == ("a.slice", "b.slice")
        assert captured["max_sessions"] == 3

    def test_empty_observe_slices_is_the_empty_tuple(self, monkeypatch, tmp_path):
        monkeypatch.setattr(access, "have_host_cgroup_view", lambda root: True)
        monkeypatch.setattr(access, "have_host_proc_view", lambda proc: True)
        captured: Dict[str, Any] = {}

        class FakeServer:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def serve_forever(self):
                pass

        monkeypatch.setattr(serve_lib, "SessionServer", FakeServer)
        args = cg.build_parser().parse_args(["serve"])
        cg.cmd_serve(args)
        assert captured["observe_slices"] == ()


class TestCtlRequest:
    def _args(self, **overrides):
        ns = argparse.Namespace(
            verb="version", target=None, scope=None, token=None, damon=None,
            interval=None, meta=None, session=None,
        )
        for key, value in overrides.items():
            setattr(ns, key, value)
        return ns

    def test_version_host_gc_take_no_fields(self):
        assert cg._ctl_request(self._args(verb="version")) == {"verb": "version"}
        assert cg._ctl_request(self._args(verb="host")) == {"verb": "host"}
        assert cg._ctl_request(self._args(verb="gc")) == {"verb": "gc"}

    def test_status_stop_report_carry_session(self):
        assert cg._ctl_request(self._args(verb="status", session=None)) == {
            "verb": "status", "session": None,
        }
        assert cg._ctl_request(self._args(verb="status", session="s-1")) == {
            "verb": "status", "session": "s-1",
        }
        assert cg._ctl_request(self._args(verb="stop", session="s-1")) == {
            "verb": "stop", "session": "s-1",
        }
        assert cg._ctl_request(self._args(verb="report", session="s-1")) == {
            "verb": "report", "session": "s-1",
        }

    def test_start_builds_the_full_request(self):
        args = self._args(
            verb="start", target="containerid:" + "a" * 64, scope="container",
            token="a-real-token-99", damon="on", interval=2.0,
            meta='{"lane": "l", "project": "p", "worktree": "w", "commit": null, '
                 '"run_gate_revision": 1, "kind": "command", "expected": null}',
        )
        req = cg._ctl_request(args)
        assert req["verb"] == "start"
        assert req["target"] == "containerid:" + "a" * 64
        assert req["scope"] == "container"
        assert req["token"] == "a-real-token-99"
        assert req["damon"] == "on"
        assert req["interval"] == 2.0
        assert req["meta"]["lane"] == "l"

    def test_start_with_malformed_meta_json_is_a_clean_exit_2(self, capsys):
        args = self._args(
            verb="start", target="containerid:" + "a" * 64, scope="container", meta="{not json",
        )
        with pytest.raises(SystemExit) as exc_info:
            cg._ctl_request(args)
        assert exc_info.value.code == 2
        assert "--meta must be valid JSON" in capsys.readouterr().err

    def test_start_with_a_non_object_meta_is_a_clean_exit_2(self, capsys):
        args = self._args(
            verb="start", target="containerid:" + "a" * 64, scope="container", meta="[1, 2]",
        )
        with pytest.raises(SystemExit) as exc_info:
            cg._ctl_request(args)
        assert exc_info.value.code == 2
        assert "--meta must be a JSON object" in capsys.readouterr().err


class TestCtlRoundtrip:
    def test_connect_failure_raises_oserror(self, tmp_path):
        with pytest.raises(OSError):
            cg._ctl_roundtrip(str(tmp_path / "nothing-listens-here.sock"), {"verb": "version"})

    def _serve_once(self, socket_path: str, reply: bytes) -> threading.Thread:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(socket_path)
        sock.listen(1)
        sock.settimeout(5.0)

        def run():
            try:
                conn, _addr = sock.accept()
            except OSError:
                return
            try:
                conn.settimeout(5.0)
                conn.recv(65536)
                if reply:
                    conn.sendall(reply)
            finally:
                conn.close()
                sock.close()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 5.0
        while not os.path.exists(socket_path) and time.monotonic() < deadline:
            time.sleep(0.01)
        return thread

    def test_empty_response_raises_valueerror(self, tmp_path):
        socket_path = str(tmp_path / "ctl.sock")
        thread = self._serve_once(socket_path, b"\n")
        try:
            with pytest.raises(ValueError, match="empty response"):
                cg._ctl_roundtrip(socket_path, {"verb": "version"})
        finally:
            thread.join(timeout=5.0)

    def test_connection_closed_with_no_reply_at_all_also_raises_valueerror(self, tmp_path):
        # The peer closes without sending a single byte (not even one that
        # already ends in "\n") — recv() returns b"" and the read loop's
        # `if not chunk: break` is what stops it, distinct from the
        # loop-condition exit the "\n"-terminated case above takes.
        socket_path = str(tmp_path / "ctl.sock")
        thread = self._serve_once(socket_path, b"")
        try:
            with pytest.raises(ValueError, match="empty response"):
                cg._ctl_roundtrip(socket_path, {"verb": "version"})
        finally:
            thread.join(timeout=5.0)

    def test_malformed_json_response_raises_valueerror(self, tmp_path):
        socket_path = str(tmp_path / "ctl.sock")
        thread = self._serve_once(socket_path, b"not json at all\n")
        try:
            with pytest.raises(ValueError, match="malformed response"):
                cg._ctl_roundtrip(socket_path, {"verb": "version"})
        finally:
            thread.join(timeout=5.0)

    def test_well_formed_response_round_trips(self, tmp_path):
        socket_path = str(tmp_path / "ctl.sock")
        thread = self._serve_once(socket_path, b'{"ok": true, "contract": 1}\n')
        try:
            resp = cg._ctl_roundtrip(socket_path, {"verb": "version"})
            assert resp == {"ok": True, "contract": 1}
        finally:
            thread.join(timeout=5.0)


class TestCmdCtl:
    def test_daemon_unreachable_prints_one_stderr_line_and_exits_3(self, monkeypatch, capsys):
        def boom(socket_path, req, timeout=25.0):
            raise OSError("connection refused")

        monkeypatch.setattr(cg, "_ctl_roundtrip", boom)
        args = cg.build_parser().parse_args(["ctl", "version"])
        assert cg.cmd_ctl(args) == 3
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "connection refused" in captured.err

    def test_malformed_response_also_exits_3(self, monkeypatch, capsys):
        def boom(socket_path, req, timeout=25.0):
            raise ValueError("empty response from daemon")

        monkeypatch.setattr(cg, "_ctl_roundtrip", boom)
        args = cg.build_parser().parse_args(["ctl", "host"])
        assert cg.cmd_ctl(args) == 3
        assert capsys.readouterr().out == ""

    def test_ok_response_prints_json_and_exits_0(self, monkeypatch, capsys):
        monkeypatch.setattr(cg, "_ctl_roundtrip", lambda *a, **k: {
            "ok": True, "contract": 1, "cgprofile": "1.0.0",
            "daemon": {
                "name": "cgprofile-host-daemon", "started_at": "2026-09-12T10:15:00Z",
                "damon": "unavailable:not available on this host", "damon_default": "on",
                "sessions_live": 0, "max_sessions": 16,
            },
        })
        args = cg.build_parser().parse_args(["ctl", "version"])
        assert cg.cmd_ctl(args) == 0
        out = capsys.readouterr().out
        assert json.loads(out)["ok"] is True
        assert out.count("\n") == 1  # exactly one JSON document, nothing else

    def test_not_ok_response_prints_json_and_exits_2(self, monkeypatch, capsys):
        monkeypatch.setattr(
            cg, "_ctl_roundtrip",
            lambda *a, **k: {"ok": False, "contract": 1, "error": {"code": "x", "message": "y"}},
        )
        args = cg.build_parser().parse_args(["ctl", "stop", "s-1"])
        assert cg.cmd_ctl(args) == 2
        out = capsys.readouterr().out
        assert json.loads(out)["ok"] is False

    @pytest.mark.parametrize("reply", [
        b'{"ok": true, "contract": 2, "cgprofile": "1.0.0", "daemon": {}}\n',
        b'[]\n',
        b'{"contract": 1, "cgprofile": "1.0.0", "daemon": {}}\n',
    ])
    def test_incompatible_response_is_a_daemon_fault_without_printing(
        self, tmp_path, reply, capsys
    ):
        socket_path = str(tmp_path / "ctl.sock")
        thread = TestCtlRoundtrip()._serve_once(socket_path, reply)
        try:
            args = cg.build_parser().parse_args(["ctl", "--socket", socket_path, "version"])
            assert cg.cmd_ctl(args) == 3
            captured = capsys.readouterr()
            assert captured.out == ""
            assert "invalid response" in captured.err
        finally:
            thread.join(timeout=5.0)
