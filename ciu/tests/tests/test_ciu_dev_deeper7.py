"""Additional deterministic public ``ciu dev`` profile and execution contracts."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import config_model, dev  # noqa: E402


def _profile(**overrides: object) -> dict:
    profile: dict[str, object] = {"image": "node:22", "command": "npm run dev"}
    profile.update(overrides)
    return {"web": {"dev": profile}}


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("build", [], "build must be a table"),
        ("prebuild", "npm run generate", "prebuild must be a list"),
        ("mount", {"source": "/repo"}, "mount must be a list"),
        ("depends_on", {"api": "healthy"}, "depends_on must be a list"),
        ("workdir", 42, "workdir must be a non-empty string"),
        ("env", ["NODE_ENV=development"], "env must be a table"),
        ("network", 42, "network must be a string"),
    ],
)
def test_public_dev_profile_rejects_invalid_optional_shape(
    field: str, value: object, message: str
) -> None:
    """A malformed optional dev field cannot become a Docker argv value."""
    with pytest.raises(ValueError, match=message):
        dev.parse_dev_profile(_profile(**{field: value}), "web")


def test_public_dev_profile_rejects_non_numeric_port() -> None:
    """A port which Docker cannot publish is rejected during profile parsing."""
    with pytest.raises(ValueError, match=r"\[S5a\].*port"):
        dev.parse_dev_profile(_profile(port="hmr"), "web")


def test_repo_root_falls_back_to_start_directory_without_global_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Running from an unrelated tree must not select an arbitrary parent as CIU root."""
    start = tmp_path / "unrelated" / "nested"
    start.mkdir(parents=True)
    monkeypatch.delenv("REPO_ROOT", raising=False)

    assert dev.resolve_repo_root(None, start) == start.resolve()


def test_run_dev_uses_default_loader_and_runner_without_docker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The public runner's defaults render once and pass its complete argv to subprocess."""
    repo = tmp_path / "repo"
    stack = repo / "apps" / "web"
    stack.mkdir(parents=True)
    rendered = _profile(port=5173, mount=["./:/app"], env={"NODE_ENV": "development"})
    loaded: list[tuple[Path, Path]] = []
    calls: list[tuple[list[str], str]] = []

    def render_global(root: Path, define_root: Path) -> dict:
        loaded.append((root, define_root))
        return {"deploy": {"network_name": "dev-net"}}

    def fake_call(argv: list[str], **kwargs: object) -> int:
        calls.append((argv, str(kwargs["cwd"])))
        return 17

    monkeypatch.setattr(config_model, "render_global_chain", render_global)
    monkeypatch.setattr(config_model, "render_stack", lambda *_args, **_kwargs: rendered)
    monkeypatch.setattr(dev.subprocess, "call", fake_call)

    assert dev.run_dev("apps/web", repo_root=repo, interactive=False) == 17
    assert loaded == [(repo.resolve(), repo.resolve())]
    assert calls == [(
        [
            "docker", "run", "--rm", "--network", "dev-net", "-p", "5173:5173",
            "-v", "./:/app", "-e", "NODE_ENV=development", "-w", "/app", "node:22",
            "sh", "-c", "exec npm run dev",
        ],
        str(stack.resolve()),
    )]


def test_dev_dependency_status_fails_closed_when_docker_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing Docker binary is a non-ready dependency, never an implicit success."""
    def missing_docker(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("docker")

    monkeypatch.setattr(dev.procutil, "docker", missing_docker)

    assert dev._container_status_fn("demo", "dev")("api") == "not-found"


def test_dev_dependency_status_rejects_failed_inspect_with_stale_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed daemon inspection cannot be made ready by leftover stdout text."""
    monkeypatch.setattr(
        dev.procutil,
        "docker",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, '{"Status":"running"}', "denied"),
    )

    assert dev._container_status_fn("demo", "dev")("api") == "not-found"
