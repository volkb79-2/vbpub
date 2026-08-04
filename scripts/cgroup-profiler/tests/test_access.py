"""Tests for lib.access: the privilege model that decides whether the
collector runs directly or must re-exec inside a privileged helper
container.

Nothing here starts a real container or calls the real ``docker`` binary
(DESIGN.md hard rule; this host happens to have a real docker CLI on PATH,
which makes that an easy rule to break by accident) — every docker-touching
function is exercised through ``access._docker``, ``access.docker_bin``, or
``subprocess.run`` monkeypatched to a fake.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from lib import access


def fake_completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["docker"], returncode=returncode, stdout=stdout, stderr=stderr)


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


class TestLowLevelDocker:
    def test_raises_when_docker_is_entirely_absent(self, monkeypatch):
        monkeypatch.setattr(access, "docker_bin", lambda: None)
        with pytest.raises(access.AccessError, match="docker CLI not found"):
            access._docker("inspect", "x")

    def test_calls_subprocess_run_with_the_binary_prefixed(self, monkeypatch):
        monkeypatch.setattr(access, "docker_bin", lambda: "/usr/bin/docker")
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return fake_completed(0, "ok")

        monkeypatch.setattr(access.subprocess, "run", fake_run)
        result = access._docker("inspect", "x", timeout=5)
        assert captured["args"] == ["/usr/bin/docker", "inspect", "x"]
        assert captured["kwargs"]["timeout"] == 5
        assert result.stdout == "ok"


# ── self_container_id ────────────────────────────────────────────────────────

class TestSelfContainerId:
    def test_hostname_resolves_via_docker_inspect(self, monkeypatch):
        monkeypatch.setattr(access.socket, "gethostname", lambda: "abc123host")
        monkeypatch.setattr(
            access, "_docker",
            lambda *a, **k: fake_completed(0, "fullid-from-hostname\n"),
        )
        assert access.self_container_id() == "fullid-from-hostname"

    def test_empty_hostname_skips_docker_and_falls_back_to_proc(self, monkeypatch):
        monkeypatch.setattr(access.socket, "gethostname", lambda: "")
        calls = []
        monkeypatch.setattr(access, "_docker", lambda *a, **k: calls.append(1) or fake_completed(0, "x"))
        monkeypatch.setattr(
            "builtins.open",
            lambda path, *a, **k: io.StringIO("0::/docker/" + "c" * 64 + "\n"),
        )
        result = access.self_container_id()
        assert calls == []
        assert result == "c" * 64

    def test_docker_inspect_nonzero_falls_back_to_proc(self, monkeypatch):
        monkeypatch.setattr(access.socket, "gethostname", lambda: "myhost")
        monkeypatch.setattr(access, "_docker", lambda *a, **k: fake_completed(1, ""))
        monkeypatch.setattr(
            "builtins.open",
            lambda path, *a, **k: io.StringIO("0::/docker/" + "d" * 64 + "\n"),
        )
        assert access.self_container_id() == "d" * 64

    def test_docker_inspect_empty_stdout_falls_back_to_proc(self, monkeypatch):
        monkeypatch.setattr(access.socket, "gethostname", lambda: "myhost")
        monkeypatch.setattr(access, "_docker", lambda *a, **k: fake_completed(0, "   "))
        monkeypatch.setattr(
            "builtins.open",
            lambda path, *a, **k: io.StringIO("0::/docker/" + "e" * 64 + "\n"),
        )
        assert access.self_container_id() == "e" * 64

    def test_proc_self_cgroup_extracts_a_64_hex_token_among_separators(self, monkeypatch):
        monkeypatch.setattr(access.socket, "gethostname", lambda: "")
        monkeypatch.setattr(
            "builtins.open",
            lambda path, *a, **k: io.StringIO(
                "12:pids:/docker-" + "f" * 64 + ".scope\n"
            ),
        )
        assert access.self_container_id() == "f" * 64

    def test_no_64_hex_token_is_none(self, monkeypatch):
        monkeypatch.setattr(access.socket, "gethostname", lambda: "")
        monkeypatch.setattr(
            "builtins.open",
            lambda path, *a, **k: io.StringIO("0::/user.slice/user-1000.slice\n"),
        )
        assert access.self_container_id() is None

    def test_proc_self_cgroup_missing_is_none(self, monkeypatch):
        monkeypatch.setattr(access.socket, "gethostname", lambda: "")

        def raise_oserror(path, *a, **k):
            raise OSError("no such file")

        monkeypatch.setattr("builtins.open", raise_oserror)
        assert access.self_container_id() is None


# ── self_inspect / self_image ────────────────────────────────────────────────

class TestSelfInspect:
    def test_none_when_no_container_id(self, monkeypatch):
        monkeypatch.setattr(access, "self_container_id", lambda: None)
        assert access.self_inspect() is None

    def test_none_when_docker_inspect_fails(self, monkeypatch):
        monkeypatch.setattr(access, "self_container_id", lambda: "a" * 64)
        monkeypatch.setattr(access, "_docker", lambda *a, **k: fake_completed(1, ""))
        assert access.self_inspect() is None

    def test_none_on_invalid_json(self, monkeypatch):
        monkeypatch.setattr(access, "self_container_id", lambda: "a" * 64)
        monkeypatch.setattr(access, "_docker", lambda *a, **k: fake_completed(0, "not json"))
        assert access.self_inspect() is None

    def test_none_when_inspect_array_is_empty(self, monkeypatch):
        monkeypatch.setattr(access, "self_container_id", lambda: "a" * 64)
        monkeypatch.setattr(access, "_docker", lambda *a, **k: fake_completed(0, "[]"))
        assert access.self_inspect() is None

    def test_returns_the_first_element(self, monkeypatch):
        monkeypatch.setattr(access, "self_container_id", lambda: "a" * 64)
        monkeypatch.setattr(
            access, "_docker",
            lambda *a, **k: fake_completed(0, json.dumps([{"Id": "a" * 64}])),
        )
        assert access.self_inspect() == {"Id": "a" * 64}


class TestSelfImage:
    def test_none_when_self_inspect_is_none(self, monkeypatch):
        monkeypatch.setattr(access, "self_inspect", lambda: None)
        assert access.self_image() is None

    def test_none_when_config_is_missing(self, monkeypatch):
        monkeypatch.setattr(access, "self_inspect", lambda: {"Id": "x"})
        assert access.self_image() is None

    def test_returns_config_image(self, monkeypatch):
        monkeypatch.setattr(access, "self_inspect", lambda: {"Config": {"Image": "myimage:local"}})
        assert access.self_image() == "myimage:local"


class TestImageExists:
    def test_true_on_zero_exit(self, monkeypatch):
        monkeypatch.setattr(access, "_docker", lambda *a, **k: fake_completed(0))
        assert access.image_exists("debian:trixie-slim") is True

    def test_false_on_nonzero_exit(self, monkeypatch):
        monkeypatch.setattr(access, "_docker", lambda *a, **k: fake_completed(1))
        assert access.image_exists("nope:local") is False


# ── resolve_helper_image ─────────────────────────────────────────────────────

class TestResolveHelperImage:
    def test_explicit_image_present_wins(self, monkeypatch):
        monkeypatch.setattr(access, "image_exists", lambda image: image == "explicit:local")
        assert access.resolve_helper_image("explicit:local") == "explicit:local"

    def test_explicit_image_absent_raises_and_never_pulls(self, monkeypatch):
        calls = []
        monkeypatch.setattr(access, "image_exists", lambda image: calls.append(image) or False)
        with pytest.raises(access.AccessError, match="not present locally"):
            access.resolve_helper_image("explicit:local")
        assert calls == ["explicit:local"]  # only ever checked, never invoked docker pull

    def test_env_var_present_wins_over_own_image(self, monkeypatch):
        monkeypatch.delenv("CGPROFILE_HELPER_IMAGE", raising=False)
        monkeypatch.setenv("CGPROFILE_HELPER_IMAGE", "env:local")
        monkeypatch.setattr(access, "image_exists", lambda image: image == "env:local")
        monkeypatch.setattr(access, "self_image", lambda: "should-not-be-used:local")
        assert access.resolve_helper_image() == "env:local"

    def test_env_var_absent_locally_raises(self, monkeypatch):
        monkeypatch.setenv("CGPROFILE_HELPER_IMAGE", "env:local")
        monkeypatch.setattr(access, "image_exists", lambda image: False)
        with pytest.raises(access.AccessError, match="CGPROFILE_HELPER_IMAGE=env:local"):
            access.resolve_helper_image()

    def test_own_image_used_when_present(self, monkeypatch):
        monkeypatch.delenv("CGPROFILE_HELPER_IMAGE", raising=False)
        monkeypatch.setattr(access, "self_image", lambda: "self:local")
        monkeypatch.setattr(access, "image_exists", lambda image: image == "self:local")
        assert access.resolve_helper_image() == "self:local"

    def test_falls_back_to_a_known_candidate_when_own_image_is_absent(self, monkeypatch):
        monkeypatch.delenv("CGPROFILE_HELPER_IMAGE", raising=False)
        monkeypatch.setattr(access, "self_image", lambda: None)
        monkeypatch.setattr(access, "image_exists", lambda image: image == "debian:trixie-slim")
        assert access.resolve_helper_image() == "debian:trixie-slim"

    def test_raises_when_every_fallback_is_absent(self, monkeypatch):
        monkeypatch.delenv("CGPROFILE_HELPER_IMAGE", raising=False)
        monkeypatch.setattr(access, "self_image", lambda: None)
        monkeypatch.setattr(access, "image_exists", lambda image: False)
        with pytest.raises(access.AccessError, match="no helper image found"):
            access.resolve_helper_image()

    def test_own_image_present_but_own_image_lookup_returns_none_is_the_same_as_absent(self, monkeypatch):
        # self_image() itself returning falsy (no self-inspect data at all,
        # e.g. running outside any container) must fall through to the
        # fixed candidate list rather than calling image_exists(None).
        monkeypatch.delenv("CGPROFILE_HELPER_IMAGE", raising=False)
        monkeypatch.setattr(access, "self_image", lambda: None)
        calls = []
        monkeypatch.setattr(access, "image_exists", lambda image: calls.append(image) or False)
        with pytest.raises(access.AccessError):
            access.resolve_helper_image()
        assert None not in calls


# ── HostPathMap / host_path_map / build_helper_spec ──────────────────────────

class TestHostPathMap:
    def test_to_host_exact_destination_match(self):
        m = access.HostPathMap(entries=[("/workspaces/vbpub", "/home/vb/vbpub")])
        assert m.to_host("/workspaces/vbpub") == "/home/vb/vbpub"

    def test_to_host_nested_path_under_a_mount(self):
        m = access.HostPathMap(entries=[("/workspaces/vbpub", "/home/vb/vbpub")])
        assert m.to_host("/workspaces/vbpub/scripts/x") == "/home/vb/vbpub/scripts/x"

    def test_to_host_path_under_no_mount_is_none(self):
        m = access.HostPathMap(entries=[("/workspaces/vbpub", "/home/vb/vbpub")])
        assert m.to_host("/etc/passwd") is None

    def test_to_host_of_an_empty_map_is_none(self):
        assert access.HostPathMap().to_host("/anything") is None

    def test_overlapping_mounts_the_longest_destination_wins(self):
        # host_path_map() is responsible for the longest-first ordering;
        # to_host() just returns the first entry that matches, so this pins
        # down that resolve_all callers must rely on that ordering, not on
        # to_host() doing its own "most specific" search.
        m = access.HostPathMap(entries=[
            ("/workspaces/vbpub", "/host/vbpub"),
            ("/workspaces", "/host/workspaces"),
        ])
        assert m.to_host("/workspaces/vbpub/scripts") == "/host/vbpub/scripts"


class TestBuildHostPathMap:
    def test_no_self_inspect_data_yields_no_entries(self, monkeypatch):
        monkeypatch.setattr(access, "self_inspect", lambda: None)
        mapping = access.host_path_map()
        assert mapping.entries == []

    def test_non_bind_and_incomplete_mounts_are_skipped(self, monkeypatch):
        monkeypatch.setattr(access, "self_inspect", lambda: {"Mounts": [
            {"Type": "volume", "Destination": "/data", "Source": "/var/lib/docker/volumes/x"},
            {"Type": "bind", "Destination": "/workspaces/vbpub"},  # no Source
            {"Type": "bind", "Source": "/host/only"},              # no Destination
        ]})
        mapping = access.host_path_map()
        assert mapping.entries == []

    def test_overlapping_bind_mounts_are_sorted_longest_destination_first(self, monkeypatch):
        monkeypatch.setattr(access, "self_inspect", lambda: {"Mounts": [
            {"Type": "bind", "Destination": "/workspaces", "Source": "/host/workspaces"},
            {"Type": "bind", "Destination": "/workspaces/vbpub", "Source": "/host/vbpub"},
        ]})
        mapping = access.host_path_map()
        assert mapping.entries[0] == ("/workspaces/vbpub", "/host/vbpub")
        assert mapping.entries[1] == ("/workspaces", "/host/workspaces")


class TestBuildHelperSpec:
    def test_repo_path_not_translatable_raises(self, monkeypatch, tmp_path: Path):
        monkeypatch.setattr(access, "host_path_map", lambda: access.HostPathMap(entries=[]))
        with pytest.raises(access.AccessError, match="cannot translate"):
            access.build_helper_spec(str(tmp_path / "repo"), str(tmp_path / "out"))

    def test_out_path_not_translatable_raises(self, monkeypatch, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        out = tmp_path / "out"
        out.mkdir()
        monkeypatch.setattr(
            access, "host_path_map",
            lambda: access.HostPathMap(entries=[(str(repo), "/host/repo")]),
        )
        with pytest.raises(access.AccessError, match="output directory"):
            access.build_helper_spec(str(repo), str(out))

    def test_success_resolves_both_paths_and_the_image(self, monkeypatch, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        out = tmp_path / "out"
        out.mkdir()
        monkeypatch.setattr(
            access, "host_path_map",
            lambda: access.HostPathMap(entries=[
                (str(repo), "/host/repo"), (str(out), "/host/out"),
            ]),
        )
        monkeypatch.setattr(access, "resolve_helper_image", lambda image: "resolved:local")
        monkeypatch.setattr(access, "resolve_helper_cgroup_parent", lambda explicit: "dev-interactive.slice")
        spec = access.build_helper_spec(str(repo), str(out), image="whatever")
        assert spec.image == "resolved:local"
        assert spec.repo_host_path == "/host/repo"
        assert spec.repo_mount_path == str(repo)
        assert spec.out_host_path == "/host/out"
        assert spec.out_mount_path == str(out)
        assert spec.cgroup_parent == "dev-interactive.slice"

    def test_cgroup_parent_argument_is_threaded_through(self, monkeypatch, tmp_path: Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        out = tmp_path / "out"
        out.mkdir()
        monkeypatch.setattr(
            access, "host_path_map",
            lambda: access.HostPathMap(entries=[
                (str(repo), "/host/repo"), (str(out), "/host/out"),
            ]),
        )
        monkeypatch.setattr(access, "resolve_helper_image", lambda image: "img:local")
        captured = {}

        def fake_resolve_cgroup_parent(explicit):
            captured["explicit"] = explicit
            return "resolved.slice"

        monkeypatch.setattr(access, "resolve_helper_cgroup_parent", fake_resolve_cgroup_parent)
        spec = access.build_helper_spec(str(repo), str(out), cgroup_parent="my.slice")
        assert captured["explicit"] == "my.slice"
        assert spec.cgroup_parent == "resolved.slice"


class TestResolveHelperCgroupParent:
    def test_explicit_wins_over_everything(self, monkeypatch):
        monkeypatch.setenv("CGPROFILE_HELPER_CGROUP_PARENT", "env-value.slice")
        monkeypatch.setenv("CGROUP_PARENT_DEV_INTERACTIVE", "devcontainer-value.slice")
        assert access.resolve_helper_cgroup_parent("explicit.slice") == "explicit.slice"

    def test_cgprofile_env_var_wins_over_devcontainer_one(self, monkeypatch):
        monkeypatch.delenv("CGPROFILE_HELPER_CGROUP_PARENT", raising=False)
        monkeypatch.setenv("CGPROFILE_HELPER_CGROUP_PARENT", "env-value.slice")
        monkeypatch.setenv("CGROUP_PARENT_DEV_INTERACTIVE", "devcontainer-value.slice")
        assert access.resolve_helper_cgroup_parent() == "env-value.slice"

    def test_falls_back_to_the_devcontainer_env_var(self, monkeypatch):
        monkeypatch.delenv("CGPROFILE_HELPER_CGROUP_PARENT", raising=False)
        monkeypatch.setenv("CGROUP_PARENT_DEV_INTERACTIVE", "devcontainer-value.slice")
        assert access.resolve_helper_cgroup_parent() == "devcontainer-value.slice"

    def test_empty_string_env_values_are_treated_as_unset(self, monkeypatch):
        # os.environ.get returns "" for a var explicitly set to empty, not
        # None — the precedence chain must not accept "" as a real placement.
        monkeypatch.setenv("CGPROFILE_HELPER_CGROUP_PARENT", "")
        monkeypatch.setenv("CGROUP_PARENT_DEV_INTERACTIVE", "")
        with pytest.raises(access.AccessError):
            access.resolve_helper_cgroup_parent()

    def test_raises_rather_than_falling_back_to_the_daemon_default(self, monkeypatch, capsys):
        # This is the whole point of the function: no environment variable at
        # all must refuse, not silently let Docker place the helper in its
        # `cgroup-parent` daemon default (dev-background.slice on this
        # estate) — exactly the tier a gate run profiles.
        monkeypatch.delenv("CGPROFILE_HELPER_CGROUP_PARENT", raising=False)
        monkeypatch.delenv("CGROUP_PARENT_DEV_INTERACTIVE", raising=False)
        with pytest.raises(access.AccessError) as exc_info:
            access.resolve_helper_cgroup_parent()
        message = str(exc_info.value)
        assert "CGPROFILE_HELPER_CGROUP_PARENT" in message
        assert "daemon" in message  # explains *why* there is no fallback

    def test_explicit_empty_string_is_not_treated_as_given(self, monkeypatch):
        # An explicit empty string ("--helper-cgroup-parent ''") is not a
        # real placement either; the env chain still applies.
        monkeypatch.delenv("CGPROFILE_HELPER_CGROUP_PARENT", raising=False)
        monkeypatch.setenv("CGROUP_PARENT_DEV_INTERACTIVE", "devcontainer-value.slice")
        assert access.resolve_helper_cgroup_parent("") == "devcontainer-value.slice"


class TestHelperSpecDockerArgs:
    def _spec(self, cgroup_parent="dev-interactive.slice"):
        return access.HelperSpec(
            image="img:local", repo_host_path="/h/repo", repo_mount_path="/repo",
            out_host_path="/h/out", out_mount_path="/out", cgroup_parent=cgroup_parent,
        )

    def test_args_without_a_name(self):
        spec = self._spec()
        args = spec.docker_args()
        assert "--name" not in args
        assert args[-1] == "img:local"
        assert "--privileged" in args
        assert "--network=none" in args
        assert f"/h/repo:/repo:ro" in args
        assert f"/h/out:/out:rw" in args

    def test_args_with_a_name(self):
        spec = self._spec()
        args = spec.docker_args(name="cgprofile-helper-1")
        assert "--name" in args
        assert args[args.index("--name") + 1] == "cgprofile-helper-1"

    def test_cgroup_parent_is_always_passed_explicitly(self):
        # The bug this exists to prevent: no --cgroup-parent means Docker's
        # daemon default wins, and on this estate that default is the exact
        # tier a gate run is profiled in.
        spec = self._spec(cgroup_parent="dev-interactive.slice")
        assert "--cgroup-parent=dev-interactive.slice" in spec.docker_args()

    def test_cgroup_parent_reflects_whatever_was_resolved(self):
        spec = self._spec(cgroup_parent="some-other.slice")
        assert "--cgroup-parent=some-other.slice" in spec.docker_args()
        args_named = spec.docker_args(name="x")
        assert "--cgroup-parent=some-other.slice" in args_named


# ── choose_mode ───────────────────────────────────────────────────────────────

class TestChooseMode:
    def test_unknown_mode_raises(self):
        with pytest.raises(access.AccessError, match="unknown access mode"):
            access.choose_mode("sideways")

    def test_direct_without_a_host_view_raises_rather_than_degrading(self, monkeypatch):
        monkeypatch.setattr(access, "have_host_cgroup_view", lambda root: False)
        with pytest.raises(access.AccessError, match="cannot see the host"):
            access.choose_mode("direct")

    def test_direct_with_a_host_view_is_accepted(self, monkeypatch):
        monkeypatch.setattr(access, "have_host_cgroup_view", lambda root: True)
        assert access.choose_mode("direct") == "direct"

    def test_helper_is_always_accepted_regardless_of_view(self, monkeypatch):
        monkeypatch.setattr(access, "have_host_cgroup_view", lambda root: True)
        assert access.choose_mode("helper") == "helper"

    def test_auto_picks_direct_when_the_host_view_is_available(self, monkeypatch):
        monkeypatch.setattr(access, "have_host_cgroup_view", lambda root: True)
        assert access.choose_mode("auto") == "direct"

    def test_auto_picks_helper_when_it_is_not(self, monkeypatch):
        monkeypatch.setattr(access, "have_host_cgroup_view", lambda root: False)
        assert access.choose_mode("auto") == "helper"


# ── describe_access ───────────────────────────────────────────────────────────

class TestDescribeAccess:
    def test_direct_view_reports_cgroup_writable(self, monkeypatch):
        monkeypatch.setattr(access, "have_host_cgroup_view", lambda root: True)
        monkeypatch.setattr(access, "cgroup_root_is_writable", lambda root: True)
        monkeypatch.setattr(access, "in_container", lambda: True)
        monkeypatch.setattr(access.os.path, "isdir", lambda p: True)
        monkeypatch.setattr(access.os, "access", lambda p, mode: True)
        monkeypatch.setattr(access, "docker_bin", lambda: "/usr/bin/docker")
        monkeypatch.setenv("CGPROFILE_IN_HELPER", "1")
        info = access.describe_access()
        assert info["host_cgroup_view"] is True
        assert info["cgroup_writable"] is True
        assert info["in_helper"] is True
        assert info["docker"] == "/usr/bin/docker"

    def test_namespaced_view_never_probes_writability(self, monkeypatch):
        # cgroup_root_is_writable actually creates+removes a directory; when
        # there is no host view it must be skipped, not merely expected to
        # return False.
        monkeypatch.setattr(access, "have_host_cgroup_view", lambda root: False)
        probe_calls = []
        monkeypatch.setattr(
            access, "cgroup_root_is_writable",
            lambda root: probe_calls.append(1) or True,
        )
        monkeypatch.setattr(access, "in_container", lambda: False)
        monkeypatch.setattr(access.os.path, "isdir", lambda p: False)
        monkeypatch.setattr(access.os, "access", lambda p, mode: False)
        monkeypatch.setattr(access, "docker_bin", lambda: None)
        monkeypatch.delenv("CGPROFILE_IN_HELPER", raising=False)
        info = access.describe_access()
        assert info["cgroup_writable"] is False
        assert probe_calls == []
        assert info["in_helper"] is False
