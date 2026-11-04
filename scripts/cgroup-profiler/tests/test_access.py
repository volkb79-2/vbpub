"""Tests for cgprofile.access: host-view detection and slice verification.

Nothing here calls the real ``docker`` binary or ``systemd-run`` (DESIGN.md
hard rule) — they are monkeypatched to fakes.
"""

from __future__ import annotations

import io
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cgprofile import access


def fake_completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["docker"], returncode=returncode, stdout=stdout, stderr=stderr)


class TestVerifySystemdSlice:
    @staticmethod
    def bus_reply(argv, values):
        if "GetUnit" in argv:
            return subprocess.CompletedProcess(
                argv, 0, 'o "/org/freedesktop/systemd1/unit/dev_2dgates_2eslice"\n', ""
            )
        return subprocess.CompletedProcess(argv, 0, f's "{values[argv[-1]]}"\n', "")

    def test_requires_loaded_authored_unit_at_expected_cgroup(self, monkeypatch):
        monkeypatch.setattr(access.shutil, "which", lambda name: "/usr/bin/busctl")
        values = {
            "LoadState": "loaded",
            "FragmentPath": "/etc/systemd/system/dev-gates.slice",
            "ControlGroup": "/dev.slice/dev-gates.slice",
        }
        calls = []

        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            result = self.bus_reply(argv, values)
            if not kwargs["capture_output"]:
                result.stdout = None
                result.stderr = None
            elif not kwargs["text"]:
                result.stdout = result.stdout.encode()
                result.stderr = result.stderr.encode()
            return result

        assert access.verify_systemd_slice(
            "dev-gates.slice", "/dev.slice/dev-gates.slice", run=run
        ) is True
        assert len(calls) == 4
        assert all(kwargs["timeout"] == 5 and kwargs["check"] is False for _, kwargs in calls)
        assert all(kwargs["capture_output"] and kwargs["text"] for _, kwargs in calls)
        assert all(argv[:2] == ["/usr/bin/busctl", "--system"] for argv, _ in calls)
        assert calls[0][0][-3:] == ["GetUnit", "s", "dev-gates.slice"]
        assert calls[-1][0][-2:] == ["org.freedesktop.systemd1.Slice", "ControlGroup"]

    @pytest.mark.parametrize(("property_name", "value"), [
        ("LoadState", "not-found"),
        ("FragmentPath", "/run/systemd/transient/dev-gates.slice"),
        ("FragmentPath", "/run/systemd/generator.late/dev-gates.slice"),
        ("FragmentPath", "/run/systemd/generator.early/dev-gates.slice"),
        ("ControlGroup", "/dev.slice/unrelated.slice"),
    ])
    def test_rejects_unloaded_transient_or_wrong_path(self, monkeypatch, property_name, value):
        monkeypatch.setattr(access.shutil, "which", lambda name: "/usr/bin/busctl")
        values = {
            "LoadState": "loaded",
            "FragmentPath": "/etc/systemd/system/dev-gates.slice",
            "ControlGroup": "/dev.slice/dev-gates.slice",
        }
        values[property_name] = value

        def run(argv, **_kwargs):
            return self.bus_reply(argv, values)

        assert access.verify_systemd_slice(
            "dev-gates.slice", "/dev.slice/dev-gates.slice", run=run
        ) is False

    def test_missing_or_failed_host_bus_is_unverified(self, monkeypatch):
        monkeypatch.setattr(access.shutil, "which", lambda _name: None)
        assert access.verify_systemd_slice("dev-gates.slice", "/dev.slice/dev-gates.slice") is False
        monkeypatch.setattr(access.shutil, "which", lambda _name: "/usr/bin/busctl")
        assert access.verify_systemd_slice(
            "dev-gates.slice", "/dev.slice/dev-gates.slice",
            run=lambda *_args, **_kwargs: fake_completed(returncode=1),
        ) is False

    def test_nonzero_bus_exit_is_not_overridden_by_well_formed_stdout(self, monkeypatch):
        monkeypatch.setattr(access.shutil, "which", lambda _name: "/usr/bin/busctl")
        values = {
            "LoadState": "loaded",
            "FragmentPath": "/etc/systemd/system/dev-gates.slice",
            "ControlGroup": "/dev.slice/dev-gates.slice",
        }

        def run(argv, **_kwargs):
            if "GetUnit" in argv:
                return subprocess.CompletedProcess(
                    argv, 1, 'o "/org/freedesktop/systemd1/unit/dev_2dgates_2eslice"\n',
                    "systemd query failed",
                )
            return self.bus_reply(argv, values)

        assert access.verify_systemd_slice(
            "dev-gates.slice", "/dev.slice/dev-gates.slice", run=run,
        ) is False

    @pytest.mark.parametrize("failure", [
        OSError("host bus unavailable"),
        ValueError("invalid process arguments"),
        subprocess.TimeoutExpired(cmd="busctl", timeout=5),
    ])
    def test_query_errors_fail_closed(self, monkeypatch, failure):
        monkeypatch.setattr(access.shutil, "which", lambda _name: "/usr/bin/busctl")

        def run(*_args, **_kwargs):
            raise failure

        assert access.verify_systemd_slice(
            "dev-gates.slice", "/dev.slice/dev-gates.slice", run=run
        ) is False

    def test_malformed_property_output_fails_closed(self, monkeypatch):
        monkeypatch.setattr(access.shutil, "which", lambda _name: "/usr/bin/busctl")
        assert access.verify_systemd_slice(
            "dev-gates.slice", "/dev.slice/dev-gates.slice",
            run=lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=None),
        ) is False

    @pytest.mark.parametrize("output", [
        'o "/wrong/object"\n',
        'o "/org/freedesktop/systemd1/unit/dev_2dgates_2eslice" extra\n',
        'o "unterminated\n',
        's "/org/freedesktop/systemd1/unit/dev_2dgates_2eslice"\n',
    ])
    def test_malformed_unit_lookup_fails_closed(self, monkeypatch, output):
        monkeypatch.setattr(access.shutil, "which", lambda _name: "/usr/bin/busctl")
        assert access.verify_systemd_slice(
            "dev-gates.slice", "/dev.slice/dev-gates.slice",
            run=lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, output, ""),
        ) is False

    @pytest.mark.parametrize("output", [
        's "/org/freedesktop/systemd1/unit/dev_2dgates_2eslice"',
        'o "/org/freedesktop/systemd1/unit/dev_2dgates_2eslice" extra',
    ])
    def test_invalid_unit_lookup_stops_before_property_queries(
        self, monkeypatch, output,
    ):
        monkeypatch.setattr(access.shutil, "which", lambda _name: "/usr/bin/busctl")
        calls = []

        def run(argv, **_kwargs):
            calls.append(argv)
            if "GetUnit" not in argv:
                pytest.fail("invalid manager reply was used as a property object path")
            return subprocess.CompletedProcess(argv, 0, output, "")

        assert access.verify_systemd_slice(
            "dev-gates.slice", "/dev.slice/dev-gates.slice", run=run,
        ) is False
        assert len(calls) == 1

    def test_successful_bus_exit_with_non_text_stdout_is_unverified(self, monkeypatch):
        monkeypatch.setattr(access.shutil, "which", lambda _name: "/usr/bin/busctl")

        def run(argv, **_kwargs):
            return subprocess.CompletedProcess(argv, 0, None, "")

        assert access.verify_systemd_slice(
            "dev-gates.slice", "/dev.slice/dev-gates.slice", run=run,
        ) is False

    @pytest.mark.parametrize(("unit", "path"), [
        (None, "/dev.slice/dev-gates.slice"),
        (123, "/dev.slice/dev-gates.slice"),
        ("", "/dev.slice/dev-gates.slice"),
        ("dev-gates", "/dev.slice/dev-gates"),
        ("dev-gates.slice", None),
        ("dev-gates.slice", 123),
        ("-bad.slice", "/dev.slice/-bad.slice"),
        ("bad\x00.slice", "/dev.slice/bad.slice"),
        ("bad\x01.slice", "/dev.slice/bad.slice"),
        ("bad\x1f.slice", "/dev.slice/bad.slice"),
        ("bad\x7f.slice", "/dev.slice/bad.slice"),
        ("bad slice.slice", "/dev.slice/bad.slice"),
        ("bad\u2003.slice", "/dev.slice/bad.slice"),
        ("dev-gates.slice", "relative"),
        ("dev-gates.slice", "/dev.slice/bad\x00.slice"),
        ("dev-gates.slice", "/dev.slice/bad\x01.slice"),
        ("dev-gates.slice", "/dev.slice/bad\x1f.slice"),
        ("dev-gates.slice", "/dev.slice/bad\x7f.slice"),
        ("dev-gates.slice", "/dev.slice/bad\n.slice"),
        ("dev-gates.slice", "/dev.slice/bad\u2003.slice"),
        ("dev-gates.slice", "/dev.slice//bad.slice"),
        ("dev-gates.slice", "/dev.slice/../bad.slice"),
        ("dev/child.slice", "/dev.slice/dev-child.slice"),
    ])
    def test_rejects_invalid_names_and_paths_without_querying(self, monkeypatch, unit, path):
        monkeypatch.setattr(access.shutil, "which", lambda _name: pytest.fail("unexpected query"))
        assert access.verify_systemd_slice(unit, path) is False


# ── have_host_cgroup_view ────────────────────────────────────────────────────

class TestHaveHostCgroupView:
    def test_missing_root_is_false(self, tmp_path: Path):
        assert access.have_host_cgroup_view(str(tmp_path / "nope")) is False

    def test_root_without_controllers_file_is_false(self, tmp_path: Path):
        root = tmp_path / "cgroup"
        root.mkdir()
        assert access.have_host_cgroup_view(str(root)) is False

    def test_namespaced_subtree_without_host_markers_is_false(self, tmp_path: Path):
        # This is the devcontainer case: a real cgroup2 mount, but only our
        # own subtree — none of init.scope/system.slice/user.slice exist.
        root = tmp_path / "cgroup"
        root.mkdir()
        (root / "cgroup.controllers").write_text("cpu memory\n")
        (root / "dev.slice").mkdir()
        assert access.have_host_cgroup_view(str(root)) is False

    def test_a_real_host_marker_present_is_true(self, tmp_path: Path):
        root = tmp_path / "cgroup"
        root.mkdir()
        (root / "cgroup.controllers").write_text("cpu memory\n")
        (root / "system.slice").mkdir()
        assert access.have_host_cgroup_view(str(root)) is True


class TestConfiguredProcRoot:
    def test_missing_value_uses_local_procfs(self, monkeypatch):
        monkeypatch.delenv("CGPROFILE_PROC_ROOT", raising=False)
        assert access._configured_proc_root() == "/proc"

    def test_absolute_value_is_normalized(self, monkeypatch):
        monkeypatch.setenv("CGPROFILE_PROC_ROOT", "/hostproc/../hostproc")
        assert access._configured_proc_root() == "/hostproc"

    def test_empty_value_refuses(self, monkeypatch):
        monkeypatch.setenv("CGPROFILE_PROC_ROOT", "  ")
        with pytest.raises(RuntimeError, match="must not be empty"):
            access._configured_proc_root()

    def test_relative_value_refuses(self, monkeypatch):
        monkeypatch.setenv("CGPROFILE_PROC_ROOT", "hostproc")
        with pytest.raises(RuntimeError, match="must be absolute"):
            access._configured_proc_root()


class TestHaveHostProcView:
    def _stat_pair(self, monkeypatch, proc_root: Path, selected: int, local: int):
        # Keep the legacy/private mode explicit unless a case opts into the
        # daemon-only host-PID deployment mode.
        monkeypatch.delenv(access.PID_NAMESPACE_MODE_ENV, raising=False)
        inodes = {
            str(proc_root / "1" / "ns" / "pid"): selected,
            "/proc/1/ns/pid": local,
        }

        def fake_stat(path):
            if str(path) not in inodes:
                raise FileNotFoundError(path)
            return SimpleNamespace(st_ino=inodes[str(path)])

        monkeypatch.setattr(access.os, "stat", fake_stat)

    def test_container_requires_pid1_from_a_distinct_pid_namespace(
        self, monkeypatch, tmp_path: Path,
    ):
        proc = tmp_path / "hostproc"
        self._stat_pair(monkeypatch, proc, selected=202, local=101)
        monkeypatch.setattr(access, "in_container", lambda: True)
        assert access.have_host_proc_view(str(proc)) is True

    def test_same_pid_namespace_is_not_a_host_proc_view(
        self, monkeypatch, tmp_path: Path,
    ):
        proc = tmp_path / "proc"
        self._stat_pair(monkeypatch, proc, selected=101, local=101)
        monkeypatch.setattr(access, "in_container", lambda: True)
        assert access.have_host_proc_view(str(proc)) is False

    def test_host_process_accepts_its_own_pid_namespace(self, monkeypatch, tmp_path: Path):
        proc = tmp_path / "proc"
        self._stat_pair(monkeypatch, proc, selected=101, local=101)
        monkeypatch.setattr(access, "in_container", lambda: False)
        assert access.have_host_proc_view(str(proc)) is True

    def test_host_process_rejects_a_different_pid_namespace(self, monkeypatch, tmp_path: Path):
        proc = tmp_path / "other-proc"
        self._stat_pair(monkeypatch, proc, selected=202, local=101)
        monkeypatch.setattr(access, "in_container", lambda: False)
        assert access.have_host_proc_view(str(proc)) is False

    @pytest.mark.parametrize(("selected", "local", "expected"), [
        (101, 101, True),
        (202, 101, False),
    ])
    def test_host_pid_mode_requires_same_namespace_as_selected_proc_view(
        self, monkeypatch, tmp_path: Path, selected: int, local: int, expected: bool,
    ):
        proc = tmp_path / "hostproc"
        self._stat_pair(monkeypatch, proc, selected=selected, local=local)
        monkeypatch.setenv(access.PID_NAMESPACE_MODE_ENV, "host")
        assert access.have_host_proc_view(str(proc)) is expected

    def test_unknown_pid_namespace_mode_fails_closed(self, monkeypatch, tmp_path: Path):
        proc = tmp_path / "hostproc"
        self._stat_pair(monkeypatch, proc, selected=101, local=101)
        monkeypatch.setenv(access.PID_NAMESPACE_MODE_ENV, "surprise")
        assert access.have_host_proc_view(str(proc)) is False

    def test_missing_namespace_metadata_is_false(self, monkeypatch, tmp_path: Path):
        monkeypatch.setattr(access.os, "stat", lambda path: (_ for _ in ()).throw(OSError(path)))
        assert access.have_host_proc_view(str(tmp_path / "missing-proc")) is False


class TestSameCgroupNamespace:
    def test_same_namespace_inode_is_required(self, monkeypatch, tmp_path: Path):
        proc = tmp_path / "proc"
        target = proc / "4242" / "ns" / "cgroup"
        inodes = {str(target): 77, "/proc/self/ns/cgroup": 77}
        monkeypatch.setattr(
            access.os, "stat",
            lambda path: SimpleNamespace(st_ino=inodes[str(path)]),
        )

        assert access.same_cgroup_namespace(4242, str(proc)) is True

    def test_different_namespace_inode_refuses_translation(self, monkeypatch, tmp_path: Path):
        proc = tmp_path / "proc"
        target = proc / "4242" / "ns" / "cgroup"
        inodes = {str(target): 78, "/proc/self/ns/cgroup": 77}
        monkeypatch.setattr(
            access.os, "stat",
            lambda path: SimpleNamespace(st_ino=inodes[str(path)]),
        )

        assert access.same_cgroup_namespace(4242, str(proc)) is False

    def test_missing_namespace_metadata_refuses_translation(self, monkeypatch):
        monkeypatch.setattr(
            access.os, "stat",
            lambda path: (_ for _ in ()).throw(FileNotFoundError(path)),
        )

        assert access.same_cgroup_namespace(4242, "/missing/proc") is False


class TestNamespaceInodes:
    def test_namespace_inode_reads_only_supported_namespaces(self, monkeypatch, tmp_path: Path):
        namespace = tmp_path / "proc" / "4242" / "ns" / "pid"
        monkeypatch.setattr(
            access.os, "stat", lambda path: SimpleNamespace(st_ino=77 if path == str(namespace) else 0),
        )

        assert access.namespace_inode(4242, "pid", str(tmp_path / "proc")) == 77
        assert access.namespace_inode(4242, "net", str(tmp_path / "proc")) is None
        assert access.namespace_inode(0, "pid", str(tmp_path / "proc")) is None

    def test_namespace_inode_refuses_missing_proc_metadata(self, monkeypatch, tmp_path: Path):
        monkeypatch.setattr(
            access.os, "stat", lambda path: (_ for _ in ()).throw(FileNotFoundError(path)),
        )

        assert access.namespace_inode(4242, "cgroup", str(tmp_path / "proc")) is None

    def test_local_namespace_inode_reads_current_namespace(self, monkeypatch):
        monkeypatch.setattr(
            access.os, "stat", lambda path: SimpleNamespace(st_ino=88 if path == "/proc/self/ns/pid" else 0),
        )

        assert access.local_namespace_inode("pid") == 88
        assert access.local_namespace_inode("net") is None

    def test_local_namespace_inode_refuses_missing_metadata(self, monkeypatch):
        monkeypatch.setattr(
            access.os, "stat", lambda path: (_ for _ in ()).throw(PermissionError(path)),
        )

        assert access.local_namespace_inode("cgroup") is None


# ── cgroup_root_is_writable ──────────────────────────────────────────────────

class TestCgroupRootIsWritable:
    def test_true_when_the_probe_directory_can_be_made_and_removed(self, tmp_path: Path):
        assert access.cgroup_root_is_writable(str(tmp_path)) is True
        assert not (tmp_path / ".cgprofile-probe").exists()

    def test_false_when_mkdir_fails(self, tmp_path: Path):
        # A read-only mount reports "rw" in its own metadata but denies the
        # write — that's exactly the gap a mount-flag check would miss.
        assert access.cgroup_root_is_writable(str(tmp_path / "does-not-exist")) is False

    def test_still_true_when_the_probe_cannot_be_cleaned_up(self, tmp_path: Path, monkeypatch):
        real_rmdir = access.os.rmdir

        def flaky_rmdir(path):
            raise OSError("simulated: something raced into the probe dir")

        monkeypatch.setattr(access.os, "rmdir", flaky_rmdir)
        assert access.cgroup_root_is_writable(str(tmp_path)) is True
        monkeypatch.setattr(access.os, "rmdir", real_rmdir)
        real_rmdir(str(tmp_path / ".cgprofile-probe"))


# ── in_container ─────────────────────────────────────────────────────────────

class TestInContainer:
    def test_dockerenv_file_short_circuits_true(self, monkeypatch):
        monkeypatch.setattr(access.os.path, "exists", lambda p: p == "/.dockerenv")
        assert access.in_container() is True

    def test_proc_1_cgroup_read_failure_is_false(self, monkeypatch):
        monkeypatch.setattr(access.os.path, "exists", lambda p: False)

        def raise_oserror(path, *a, **k):
            raise OSError("no such file")

        monkeypatch.setattr("builtins.open", raise_oserror)
        assert access.in_container() is False

    def test_detects_docker_from_the_cgroup_file(self, monkeypatch):
        monkeypatch.setattr(access.os.path, "exists", lambda p: False)
        monkeypatch.setattr(
            "builtins.open",
            lambda path, *a, **k: io.StringIO("0::/docker/abc123\n"),
        )
        assert access.in_container() is True

    def test_detects_containerd_only_content(self, monkeypatch):
        # Regression: the function used to call fh.read() twice, and the
        # second call on an already-exhausted handle always returns "" — so
        # a cgroup file containing "containerd" but never the literal
        # substring "docker" was silently misread as "not a container".
        monkeypatch.setattr(access.os.path, "exists", lambda p: False)
        monkeypatch.setattr(
            "builtins.open",
            lambda path, *a, **k: io.StringIO("0::/machine.slice/containerd-abc.scope\n"),
        )
        assert access.in_container() is True

    def test_neither_marker_present_is_false(self, monkeypatch):
        monkeypatch.setattr(access.os.path, "exists", lambda p: False)
        monkeypatch.setattr(
            "builtins.open",
            lambda path, *a, **k: io.StringIO("0::/user.slice/user-1000.slice\n"),
        )
        assert access.in_container() is False


# ── docker_bin / _docker ─────────────────────────────────────────────────────

class TestDockerBin:
    def test_absent_binary_is_none(self, monkeypatch):
        monkeypatch.setattr(access.shutil, "which", lambda name: None)
        assert access.docker_bin() is None

    def test_present_binary_is_returned(self, monkeypatch):
        monkeypatch.setattr(access.shutil, "which", lambda name: "/usr/bin/docker")
        assert access.docker_bin() == "/usr/bin/docker"

