"""CIU-161 / CIU-172: preparing a worktree must not require Docker, and every
refusal on that path names a concrete remedy.

Oracles (each fails on the pre-fix behaviour):

- the Docker group is read from the socket the client uses (``DOCKER_HOST``),
  not only from two fixed ``/var/run`` paths, so a relayed socket works;
- ``worktree create`` in an environment with no Docker socket and no
  ``DOCKER_GID`` succeeds, honours the requested ``--branch`` exactly, and
  leaves ``DOCKER_GID`` empty instead of aborting;
- Docker-using verbs keep a hard refusal, but its text names the remedy;
- the three generated-vs-recorded identity refusals name ``worktree ensure``
  and ``--adopt-existing-network``.
"""
from __future__ import annotations

import grp
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import worktree, workspace_env  # noqa: E402
from test_ciu_worktree import fake_generate_env, tmp_repo  # noqa: E402,F401


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


@pytest.fixture
def no_docker_group(monkeypatch):
    monkeypatch.delenv("DOCKER_GID", raising=False)
    monkeypatch.delenv(workspace_env.DOCKER_GID_OPTIONAL_ENV, raising=False)

    def missing(_name):
        raise KeyError("docker")

    monkeypatch.setattr(grp, "getgrnam", missing)


class TestSocketDerivation:
    def test_docker_host_unix_socket_is_probed_first(self, monkeypatch):
        monkeypatch.delenv("DOCKER_GID", raising=False)
        monkeypatch.setenv("DOCKER_HOST", "unix:///run/docker-api/docker.sock")
        relay = Path("/run/docker-api/docker.sock")
        real_exists = Path.exists

        def exists(path):
            return True if path == relay else (
                False if str(path).startswith("/var/run/docker") else real_exists(path)
            )

        def stat(path, *a, **k):
            assert path == relay
            return SimpleNamespace(st_gid=2751)

        monkeypatch.setattr(Path, "exists", exists)
        monkeypatch.setattr(Path, "stat", stat)
        assert workspace_env._detect_docker_gid() == "2751"

    def test_candidates_without_docker_host_are_the_classic_paths(self, monkeypatch):
        monkeypatch.delenv("DOCKER_HOST", raising=False)
        assert workspace_env._docker_socket_candidates() == [
            "/var/run/docker-host.sock", "/var/run/docker.sock",
        ]

    @pytest.mark.parametrize("host", ["tcp://10.0.0.1:2375", "unix://", "ssh://h"])
    def test_non_unix_or_empty_docker_host_adds_no_candidate(self, monkeypatch, host):
        monkeypatch.setenv("DOCKER_HOST", host)
        assert workspace_env._docker_socket_candidates() == [
            "/var/run/docker-host.sock", "/var/run/docker.sock",
        ]

    def test_unavailable_error_names_paths_and_remedy(self, monkeypatch, no_docker_group):
        monkeypatch.setenv("DOCKER_HOST", "unix:///nonexistent/d.sock")
        monkeypatch.setattr(Path, "exists", lambda _p: False)
        with pytest.raises(workspace_env.DockerGidUnavailableError) as err:
            workspace_env._detect_docker_gid()
        text = str(err.value)
        assert "/nonexistent/d.sock" in text
        assert "DOCKER_HOST=unix://" in text and "DOCKER_GID=<gid>" in text
        assert isinstance(err.value, workspace_env.WorkspaceEnvError)


class TestOptionalGid:
    def _stub(self, monkeypatch):
        monkeypatch.setenv("ENV_TYPE", "native")
        monkeypatch.setenv("PUBLIC_FQDN", "localhost")
        monkeypatch.setattr(
            workspace_env, "_detect_physical_repo_root", lambda root: root
        )
        monkeypatch.setattr(Path, "exists", lambda p, _real=Path.exists: (
            False if str(p).startswith(("/var/run/docker", "/nonexistent")) else _real(p)
        ))

    def test_hard_failure_by_default(self, tmp_path, monkeypatch, no_docker_group):
        self._stub(monkeypatch)
        monkeypatch.setenv("DOCKER_HOST", "unix:///nonexistent/d.sock")
        with pytest.raises(workspace_env.DockerGidUnavailableError):
            workspace_env.generate_ciu_env(tmp_path)

    @pytest.mark.parametrize("via", ["argument", "env"])
    def test_optional_writes_empty_key_and_one_info(
        self, tmp_path, monkeypatch, no_docker_group, capsys, via
    ):
        self._stub(monkeypatch)
        monkeypatch.setenv("DOCKER_HOST", "unix:///nonexistent/d.sock")
        kwargs = {}
        if via == "argument":
            kwargs["docker_gid_optional"] = True
        else:
            monkeypatch.setenv(workspace_env.DOCKER_GID_OPTIONAL_ENV, "1")
        out = workspace_env.generate_ciu_env(tmp_path, **kwargs)
        text = out.read_text(encoding="utf-8")
        assert 'export DOCKER_GID=""' in text
        info = [l for l in capsys.readouterr().out.splitlines()
                if "No Docker group id derivable" in l]
        assert len(info) == 1 and info[0].startswith("[INFO]")

    def test_empty_gid_refusal_names_docker_host_remedy(self, monkeypatch):
        monkeypatch.setenv("DOCKER_GID", "")
        with pytest.raises(workspace_env.WorkspaceEnvError) as err:
            workspace_env.ensure_workspace_env(["DOCKER_GID"])
        assert "DOCKER_HOST=unix://" in str(err.value)

    def test_other_missing_keys_get_no_docker_hint(self, monkeypatch):
        monkeypatch.delenv("SOME_OTHER_KEY", raising=False)
        with pytest.raises(workspace_env.WorkspaceEnvError) as err:
            workspace_env.ensure_workspace_env(["SOME_OTHER_KEY"])
        assert "DOCKER_HOST" not in str(err.value)


class TestPreparationChild:
    def test_generate_env_child_gets_the_optional_marker(self, tmp_path, monkeypatch):
        seen = {}

        def fake_run(argv, cwd=None, env=None, check=False):
            seen["env"] = env
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr(worktree.subprocess, "run", fake_run)
        assert worktree._generate_env_in(tmp_path) == 0
        assert seen["env"][workspace_env.DOCKER_GID_OPTIONAL_ENV] == "1"


class TestDockerAbsence:
    def _probe_fails(self, monkeypatch, stderr="Cannot connect"):
        monkeypatch.setattr(
            worktree.procutil, "docker",
            lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr=stderr),
        )

    def test_absent_unix_socket_is_local_only_with_one_info(self, monkeypatch, capsys):
        monkeypatch.setenv("DOCKER_HOST", "unix:///nonexistent/d.sock")
        monkeypatch.setattr(Path, "exists", lambda _p: False)
        self._probe_fails(monkeypatch)
        assert worktree._docker_network_exists("n-1") is False
        out = capsys.readouterr().out
        assert out.count("[INFO]") == 1 and "no Docker socket" in out

    def test_absent_socket_info_is_flushed(self, monkeypatch):
        # CIU-195: the INFO line precedes a long silent preparation; it must be flushed.
        monkeypatch.setenv("DOCKER_HOST", "unix:///nonexistent/d.sock")
        monkeypatch.setattr(Path, "exists", lambda _p: False)
        self._probe_fails(monkeypatch)
        calls = []
        monkeypatch.setattr(
            "builtins.print", lambda *a, **k: calls.append((a, k)),
        )
        assert worktree._docker_network_exists("n-1") is False
        assert len(calls) == 1 and calls[0][1].get("flush") is True

    def test_failing_probe_with_empty_stderr_reports_stdout(self, monkeypatch):
        monkeypatch.setenv("DOCKER_HOST", "unix:///run/x.sock")
        monkeypatch.setattr(Path, "exists", lambda _p: True)
        monkeypatch.setattr(
            worktree.procutil, "docker",
            lambda *a, **k: SimpleNamespace(
                returncode=1, stdout="daemon says no\n", stderr=""),
        )
        with pytest.raises(worktree.RuntimeNetworkProbeError, match="daemon says no"):
            worktree._docker_network_exists("n-1")

    def test_present_socket_but_failing_probe_stays_a_refusal_with_remedy(
        self, monkeypatch
    ):
        monkeypatch.setenv("DOCKER_HOST", "unix:///run/x.sock")
        monkeypatch.setattr(Path, "exists", lambda _p: True)
        self._probe_fails(monkeypatch, "permission denied")
        with pytest.raises(worktree.RuntimeNetworkProbeError) as err:
            worktree._docker_network_exists("n-1")
        assert "permission denied" in str(err.value)
        assert "ciu worktree ensure" in str(err.value)

    def test_remote_endpoint_is_never_absent(self, monkeypatch):
        monkeypatch.setenv("DOCKER_HOST", "tcp://10.0.0.9:2375")
        monkeypatch.setattr(Path, "exists", lambda _p: False)
        assert worktree._docker_socket_absent() is False
        self._probe_fails(monkeypatch, "timeout")
        with pytest.raises(worktree.RuntimeNetworkProbeError):
            worktree._docker_network_exists("n-1")


def test_create_honours_the_requested_branch_name_exactly(tmp_repo, fake_generate_env):
    record = worktree.create(tmp_repo, "branch-exact", branch="exact/name")
    assert record.branch == "exact/name"
    branches = _git(["branch", "--format=%(refname:short)"], tmp_repo).stdout.split()
    assert "exact/name" in branches
    head = _git(["rev-parse", "--abbrev-ref", "HEAD"], record.git_worktree_path)
    assert head.stdout.strip() == "exact/name"


class TestIdentityMismatchRemedy:
    def _expect(self, text, name):
        assert f"`ciu worktree ensure {name}`" in text
        assert f"`ciu worktree ensure {name} --adopt-existing-network`" in text

    def test_remedy_text_names_both_commands(self):
        self._expect(worktree._IDENTITY_MISMATCH_REMEDY.format(name="p220"), "p220")


def test_adopt_prepares_without_a_docker_gid(
    tmp_repo, monkeypatch, no_docker_group, write_instance_facts,
):
    """CIU-184 / F7: `adopt` runs its env generation with the DOCKER_GID-optional
    switch, so a caller without a Docker group id no longer ends `recovery-required`."""
    target = tmp_repo / ".worktrees" / "existing"
    assert _git(["worktree", "add", "-b", "existing", str(target), "main"], tmp_repo).returncode == 0
    children: list[dict] = []
    real_run = subprocess.run

    def run(argv, *args, **kwargs):
        if argv and argv[0] == sys.executable:
            children.append(dict(kwargs["env"]))
            write_instance_facts(
                Path(kwargs["cwd"]), instance_id="abc123", network="repo-abc123-network",
                repo_root=str(kwargs["cwd"]), physical_repo_root=str(kwargs["cwd"]),
                repo_name="repo",
            )
            return subprocess.CompletedProcess(argv, 0)
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(worktree.subprocess, "run", run)
    monkeypatch.setattr(worktree, "_docker_network_exists", lambda _network: False)
    record = worktree.adopt(tmp_repo, "adopted", str(target))
    assert record.state == "ready"
    assert children, "adopt must run an env-generation child"
    assert all(env.get(workspace_env.DOCKER_GID_OPTIONAL_ENV) == "1" for env in children)
    assert all("DOCKER_GID" not in env for env in children)
