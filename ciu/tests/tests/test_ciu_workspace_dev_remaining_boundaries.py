"""Remaining deterministic boundary contracts for workspace setup and ``ciu dev``."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import dev, workspace_env  # noqa: E402
from ciu.dev import parse_dev_profile, resolve_repo_root, run_dev  # noqa: E402
from ciu.workspace_env import WorkspaceEnvError  # noqa: E402


def _result(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class TestWorkspaceDockerBoundaries:
    def test_host_tmp_uses_explicit_override_without_docker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOST_MDT_TMP", "/host/persisted tmp")
        monkeypatch.setattr(workspace_env.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("docker must not run"))

        assert workspace_env._detect_host_mdt_tmp() == "/host/persisted tmp"

    def test_host_tmp_reads_only_the_tmp_bind_source(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HOST_MDT_TMP", raising=False)
        monkeypatch.setenv("DEVCONTAINER_NAME", "ciu-cockpit")
        seen: list[list[str]] = []

        def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            seen.append(argv)
            return _result(stdout="/host/mdt-tmp\n")

        monkeypatch.setattr(workspace_env.subprocess, "run", fake_run)

        assert workspace_env._detect_host_mdt_tmp() == "/host/mdt-tmp"
        assert seen == [[
            "docker", "inspect", "ciu-cockpit", "--format",
            '{{ range .Mounts }}{{ if eq .Destination "/tmp" }}{{ .Source }}{{ end }}{{ end }}',
        ]]

    def test_network_creation_failure_reports_network_and_daemon_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(workspace_env, "_docker_available", lambda: True)
        calls: list[list[str]] = []

        def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(argv)
            if argv[2] == "inspect":
                return _result(returncode=1, stderr="not found")
            return _result(returncode=1, stderr="permission denied")

        monkeypatch.setattr(workspace_env.subprocess, "run", fake_run)

        with pytest.raises(WorkspaceEnvError, match=r"failed-net: permission denied"):
            workspace_env.ensure_workspace_network("failed-net", auto_connect=False)
        assert calls == [
            ["docker", "network", "inspect", "failed-net"],
            ["docker", "network", "create", "failed-net"],
        ]

    def test_native_network_setup_never_attaches_cockpit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ENV_TYPE", "native")
        monkeypatch.setattr(workspace_env, "_ensure_network_exists", lambda name: None)
        monkeypatch.setattr(workspace_env.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("native must not attach"))

        workspace_env.ensure_workspace_network("native-net")

    def test_tls_probe_shell_quotes_path_with_apostrophe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cert = "/etc/letsencrypt/live/acme's/cert.pem"
        key = "/etc/letsencrypt/live/acme's/key.pem"
        monkeypatch.setenv("PUBLIC_TLS_CRT_PEM", cert)
        monkeypatch.setenv("PUBLIC_TLS_KEY_PEM", key)
        monkeypatch.setenv("DOCKER_GID", "998")
        monkeypatch.setenv("USER_UID", "1000")
        monkeypatch.setattr(workspace_env, "_docker_available", lambda: True)
        commands: list[list[str]] = []
        monkeypatch.setattr(
            workspace_env.subprocess,
            "run",
            lambda argv, **_kwargs: commands.append(argv) or _result(),
        )

        workspace_env._check_tls_access()

        assert [cmd[-1] for cmd in commands] == [
            "test -r '/etc/letsencrypt/live/acme'\"'\"'s/cert.pem'",
            "test -r '/etc/letsencrypt/live/acme'\"'\"'s/key.pem'",
        ]


class TestDevProfileAndExecutionBoundaries:
    def test_repo_root_precedence_and_marker_walk(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        marker_root = tmp_path / "repo"
        nested = marker_root / "a" / "b"
        nested.mkdir(parents=True)
        (marker_root / "ciu.global.defaults.toml.j2").write_text("", encoding="utf-8")
        explicit = tmp_path / "explicit"
        explicit.mkdir()

        # cwd is inside a REAL ciu-managed tree (marker_root derives cleanly),
        # but a disagreeing ambient REPO_ROOT is set -- S1.1 REFUSES rather
        # than silently preferring either value (ciu-P32; this line used to
        # assert the ambient value won, which was the exact masked-default
        # defect that fix closes).
        monkeypatch.setenv("REPO_ROOT", str(explicit))
        with pytest.raises(ValueError) as exc_info:
            resolve_repo_root(None, nested)
        message = str(exc_info.value)
        assert "[S1.1]" in message
        assert str(explicit.resolve()) in message
        assert str(marker_root.resolve()) in message

        monkeypatch.delenv("REPO_ROOT", raising=False)
        assert resolve_repo_root(None, nested) == marker_root.resolve()
        assert resolve_repo_root(explicit, nested) == explicit.resolve()

    def test_empty_image_is_a_profile_error_not_a_docker_command(self) -> None:
        with pytest.raises(ValueError, match=r"\[S5a\].*non-empty string"):
            parse_dev_profile({"app": {"dev": {"image": " ", "command": "serve"}}}, "app")

    def test_bad_empty_image_stops_run_before_docker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "repo"
        stack = repo / "apps" / "bad"
        stack.mkdir(parents=True)
        (stack / "ciu.defaults.toml.j2").write_text(
            '[app]\n[app.dev]\nimage = ""\ncommand = "serve"\n', encoding="utf-8"
        )
        launched = False

        def run_fn(_argv: list[str], **_kwargs: object) -> int:
            nonlocal launched
            launched = True
            return 0

        assert run_dev("apps/bad", repo_root=repo, global_loader=lambda _root: {}, run_fn=run_fn) == 2
        assert launched is False

    def test_build_profile_runs_build_then_dev_container_with_selected_network(
        self, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        stack = repo / "apps" / "builder"
        stack.mkdir(parents=True)
        (stack / "ciu.defaults.toml.j2").write_text(
            "[app]\n[app.dev]\n"
            'command = "serve --reload"\n'
            'build = { context = "src", dockerfile = "Dockerfile.dev", target = "dev", tag = "app-dev" }\n'
            'network = "profile-net"\n',
            encoding="utf-8",
        )
        calls: list[tuple[list[str], str]] = []

        def run_fn(argv: list[str], **kwargs: object) -> int:
            calls.append((argv, str(kwargs["cwd"])))
            return 0

        assert run_dev(
            "apps/builder", repo_root=repo,
            global_loader=lambda _root: {"deploy": {"network_name": "default-net"}},
            run_fn=run_fn, interactive=False,
        ) == 0
        assert calls == [
            (["docker", "build", "-t", "app-dev", "-f", "src/Dockerfile.dev", "--target", "dev", "src"], str(stack)),
            (["docker", "run", "--rm", "--network", "profile-net", "-w", "/app", "app-dev", "sh", "-c", "exec serve --reload"], str(stack)),
        ]

    def test_build_failure_does_not_launch_dev_container(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        stack = repo / "apps" / "builder"
        stack.mkdir(parents=True)
        (stack / "ciu.defaults.toml.j2").write_text(
            '[app]\n[app.dev]\ncommand = "serve"\nbuild = { context = "src" }\n',
            encoding="utf-8",
        )
        calls: list[list[str]] = []

        def build_run_fn(argv: list[str], **_kwargs: object) -> int:
            calls.append(argv)
            return 7

        assert run_dev(
            "apps/builder", repo_root=repo, global_loader=lambda _root: {},
            run_fn=lambda *_args, **_kwargs: pytest.fail("dev run must not start"),
            build_run_fn=build_run_fn, interactive=False,
        ) == 1
        assert calls == [["docker", "build", "-t", "ciu-dev-builder", "-f", "src/Dockerfile", "src"]]


def test_container_status_treats_malformed_daemon_state_as_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dev.procutil, "docker", lambda *_args, **_kwargs: _result(stdout="not-json"))

    assert dev._container_status_fn("project", "dev")("api") == "not-found"
