"""Engine execution boundary contracts (S8.1, S8.3, S10.3).

These tests exercise the user-visible outcomes around the one real process
boundary in CIU's single-stack pipeline.  Docker itself is deliberately not
started: ``subprocess.Popen`` / the compose runner are replaced at that edge.
The rest of the interrupted pipeline uses a tiny on-disk repository, so the
test proves the ordering rule that post-compose hooks never run after an
interrupted ``compose up``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine  # noqa: E402
from ciu.secrets.providers import VaultError  # noqa: E402


GLOBAL_DEFAULTS = """\
[ciu]
require_fqdn = false
require_certs = false
auto_connect_network = false

[deploy]
project_name = "engine-boundary"
environment_tag = "test"
log_level = "INFO"

[deploy.env.shared]
CONTAINER_UID = "$CONTAINER_UID"
CONTAINER_GID = "$CONTAINER_GID"
DOCKER_GID = "$DOCKER_GID"
"""

STACK_DEFAULTS = """\
[demo]
name = "demo"
image = "alpine:3"

[demo.hooks]
post_compose = ["./post.py"]
"""

COMPOSE = """\
services:
  {{ demo.name }}:
    image: {{ demo.image }}
"""


def _write_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create the smallest native CIU stack needed to reach compose up."""
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("PHYSICAL_REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("DOCKER_NETWORK_INTERNAL", "engine-boundary-net")
    monkeypatch.setenv("CONTAINER_UID", str(os.getuid()))
    monkeypatch.setenv("CONTAINER_GID", str(os.getgid()))
    monkeypatch.setenv("DOCKER_GID", str(os.getgid()))
    monkeypatch.setenv("SKIP_DEPENDENCY_CHECK", "1")
    monkeypatch.setenv("CIU_SKIP_DOOD_PREFLIGHT", "1")

    (tmp_path / "ciu.global.defaults.toml.j2").write_text(GLOBAL_DEFAULTS)
    (tmp_path / "ciu.env").write_text(
        "\n".join(
            f'export {key}="{os.environ[key]}"'
            for key in (
                "REPO_ROOT", "PHYSICAL_REPO_ROOT", "DOCKER_NETWORK_INTERNAL",
                "CONTAINER_UID", "CONTAINER_GID", "DOCKER_GID",
            )
        )
        + "\n"
    )
    (tmp_path / ".gitignore").write_text("**/.ciu/\n")
    stack = tmp_path / "applications" / "demo"
    stack.mkdir(parents=True)
    (stack / "ciu.defaults.toml.j2").write_text(STACK_DEFAULTS)
    (stack / "ciu.compose.yml.j2").write_text(COMPOSE)
    # If CIU incorrectly runs this after a compose interruption, it makes the
    # pipeline visibly fail instead of silently doing forbidden post-work.
    (stack / "post.py").write_text(
        "def run(config, ctx):\n"
        "    raise AssertionError('post_compose must not run after interruption')\n"
    )
    return stack


def test_interrupted_compose_skips_post_hook_and_preserves_interrupted_status(
    tmp_path, monkeypatch
):
    """S8.3: user abort is reported and does not run post-compose side effects."""
    stack = _write_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        engine,
        "execute_docker_compose_with_logs",
        lambda *args, **kwargs: {
            "status": "interrupted",
            "message": "User interrupted execution",
            "stdout": "partial compose output\n",
        },
    )

    result = engine.main_execution(
        working_dir=stack,
        define_root=tmp_path,
        skip_hostdir_check=True,
    )

    assert result["status"] == "interrupted"
    assert result["message"] == "User aborted deployment"
    assert result["stdout"] == "partial compose output\n"


def test_compose_runner_returns_nonzero_status_and_streamed_output(tmp_path, monkeypatch):
    """S8.1: compose failure is a structured failure, retaining visible output."""
    class FailedProcess:
        returncode = 17
        stdout = iter(("creating api\n", "api failed\n"))

        def wait(self):
            return self.returncode

    monkeypatch.setattr(engine.subprocess, "Popen", lambda *args, **kwargs: FailedProcess())

    result = engine.execute_docker_compose_with_logs(["-f", "ciu.compose.yml"], cwd=tmp_path)

    assert result == {
        "status": "error",
        "message": "Docker compose failed with exit code 17",
        "stdout": "creating api\napi failed\n",
    }


def test_compose_runner_missing_docker_is_structured_failure(tmp_path, monkeypatch):
    """S8.1: an absent docker executable does not leak an implementation exception."""
    def _missing(*args, **kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(engine.subprocess, "Popen", _missing)

    result = engine.execute_docker_compose_with_logs(["-f", "ciu.compose.yml"], cwd=tmp_path)

    assert result["status"] == "error"
    assert result["stdout"] == ""
    assert "docker not available" in result["message"]


def test_keyboard_interrupt_terminates_then_kills_unresponsive_compose(tmp_path, monkeypatch):
    """S8.1: Ctrl-C first terminates compose and force-kills only if it hangs."""
    calls: list[str] = []

    class InterruptingStdout:
        def __iter__(self):
            return self

        def __next__(self):
            raise KeyboardInterrupt

    class InterruptedProcess:
        returncode = None

        @property
        def stdout(self):
            return InterruptingStdout()

        def wait(self, **kwargs):
            calls.append("wait")
            raise TimeoutError("still stopping")

        def terminate(self):
            calls.append("terminate")

        def kill(self):
            calls.append("kill")

    monkeypatch.setattr(engine.subprocess, "Popen", lambda *args, **kwargs: InterruptedProcess())

    result = engine.execute_docker_compose_with_logs(["-f", "ciu.compose.yml"], cwd=tmp_path)

    assert result["status"] == "interrupted"
    assert result["message"] == "User interrupted execution"
    assert calls == ["terminate", "wait", "kill"]


@pytest.mark.parametrize(
    ("failure", "expected_exit"),
    [
        (engine.ComposeError("compose failed"), 1),
        (VaultError("vault unavailable"), 1),
        (engine.DooDPreflightError("daemon cannot bind mount"), 3),
    ],
)
def test_main_maps_runtime_and_environment_pipeline_failures_to_public_exit_codes(
    monkeypatch, failure, expected_exit
):
    """S10.3: callers see the documented status, never an uncaught pipeline error."""
    monkeypatch.setattr(engine, "main_execution", lambda **kwargs: (_ for _ in ()).throw(failure))

    assert engine.main(["--dry-run"]) == expected_exit
