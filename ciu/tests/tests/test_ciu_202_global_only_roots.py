"""CIU-202: global-only CIU roots are valid and have nothing to clean."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy, workspace  # noqa: E402


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False,
    )


def _copy_shape_file(source_root: Path, scratch: Path, relative: str) -> None:
    source = source_root / relative
    destination = scratch / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def test_committed_root_discovery_accepts_every_repo_root_shape(tmp_path):
    """Use a scratch Git copy of real markers, including standalone/test roots."""
    source_root = Path(__file__).resolve().parents[3]
    scratch = tmp_path / "fixture"
    scratch.mkdir()
    assert _git(["init", "-b", "main"], scratch).returncode == 0
    assert _git(["config", "user.email", "test@example.invalid"], scratch).returncode == 0
    assert _git(["config", "user.name", "CIU test"], scratch).returncode == 0

    # Copy the distinct discovered-root layouts from the repository inventory:
    # global and stack marker in one directory, global root with child stacks,
    # a global-only root with install override, and the nested test-repo root.
    # Also include stack-only markers to prove they do not create roots.
    shape_files = (
        "pwmcp/ciu.global.defaults.toml.j2",
        "pwmcp/ciu.defaults.toml.j2",
        "pwmcp/standalone/ciu.global.defaults.toml.j2",
        "pwmcp/standalone/ciu.install.override.toml.j2",
        "nyxloom/ciu.global.defaults.toml.j2",
        "nyxloom/ntfy/ciu.defaults.toml.j2",
        "nyxloom/nyxloomd/ciu.defaults.toml.j2",
        "nyxloom/pwmcp-instance/ciu.defaults.toml.j2",
        "tls-edge/ciu.global.defaults.toml.j2",
        "tls-edge/ciu-stack/ciu.defaults.toml.j2",
        "topos/ciu.global.defaults.toml.j2",
        "topos/pwmcp-instance/ciu.defaults.toml.j2",
        "scripts/cgroup-profiler/ciu.global.defaults.toml.j2",
        "scripts/cgroup-profiler/ciu.defaults.toml.j2",
        "voice-screen-share/fluxer-server/ciu.global.defaults.toml.j2",
        "voice-screen-share/fluxer-server/ciu.defaults.toml.j2",
        "voice-screen-share/jitsi-server/ciu.global.defaults.toml.j2",
        "voice-screen-share/jitsi-server/ciu.defaults.toml.j2",
        "voice-screen-share/mattermore/ciu.global.defaults.toml.j2",
        "voice-screen-share/mattermore/ciu.defaults.toml.j2",
        "voice-screen-share/mattermost-server/ciu.global.defaults.toml.j2",
        "voice-screen-share/mattermost-server/ciu.defaults.toml.j2",
        "ciu/test-repo/ciu.global.defaults.toml.j2",
        "ciu/test-repo/applications/app-config/ciu.defaults.toml.j2",
        "ciu/test-repo/applications/workers/ciu.defaults.toml.j2",
        "ciu/test-repo/infra/db-core/ciu.defaults.toml.j2",
        "ciu/test-repo/infra/redis-core/ciu.defaults.toml.j2",
        "ciu/test-repo/infra/vault/ciu.defaults.toml.j2",
        "ciu/test-repo/standalone/project/ciu.global.defaults.toml.j2",
        "ciu/test-repo/standalone/project/app/ciu.defaults.toml.j2",
        "legacy-experiments/voice2text-ai/whisper-live/ciu.defaults.toml.j2",
        "legacy-experiments/voice2text-ai/localai/ciu.defaults.toml.j2",
        "legacy-experiments/voice2text-ai/oobabooga-llm/ciu.defaults.toml.j2",
        "legacy-experiments/voice2text-ai/reverse-proxy/ciu.defaults.toml.j2",
        "legacy-experiments/voice2text-ai/speech-to-copilot/ciu.defaults.toml.j2",
        "legacy-experiments/voice2text-ai/whisper-trans/ciu.defaults.toml.j2",
    )
    for relative in shape_files:
        _copy_shape_file(source_root, scratch, relative)

    # An install override without a committed global marker is not a CIU root.
    install_only = scratch / "pwmcp" / "install-only"
    install_only.mkdir()
    (install_only / "ciu.install.override.toml.j2").write_text(
        "[install]\n", encoding="utf-8",
    )

    assert _git(["add", "-A"], scratch).returncode == 0
    assert _git(["commit", "-m", "root shape fixture"], scratch).returncode == 0

    discovered = workspace.discover_committed_roots(scratch, base="HEAD")
    assert discovered == tuple(sorted(
        (
            (scratch / "pwmcp").resolve(),
            (scratch / "pwmcp" / "standalone").resolve(),
            (scratch / "nyxloom").resolve(),
            (scratch / "tls-edge").resolve(),
            (scratch / "topos").resolve(),
            (scratch / "scripts" / "cgroup-profiler").resolve(),
            (scratch / "voice-screen-share" / "fluxer-server").resolve(),
            (scratch / "voice-screen-share" / "jitsi-server").resolve(),
            (scratch / "voice-screen-share" / "mattermore").resolve(),
            (scratch / "voice-screen-share" / "mattermost-server").resolve(),
            (scratch / "ciu" / "test-repo").resolve(),
            (scratch / "ciu" / "test-repo" / "standalone" / "project").resolve(),
        ),
        key=lambda path: (len(path.parts), str(path)),
    ))


@pytest.mark.parametrize("dry_run", [False, True])
def test_clean_of_global_only_root_is_an_info_noop(
    monkeypatch, tmp_path, capsys, dry_run,
):
    root = tmp_path / "pwmcp" / "standalone"
    root.mkdir(parents=True)
    (root / "ciu.global.defaults.toml.j2").write_text(
        "[ciu]\nstandalone_root = true\n", encoding="utf-8",
    )
    (root / "ciu.install.override.toml.j2").write_text(
        "[install]\n", encoding="utf-8",
    )

    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    docker_calls = tmp_path / "docker.calls"
    docker = fake_bin / "docker"
    docker.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\n"
        f"Path({str(docker_calls)!r}).write_text('called\\n')\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.chdir(root)

    profile = SimpleNamespace(
        name=None,
        phase_keys=None,
        config={"deploy": {"project_name": "pwmcp", "environment_tag": "test"}},
    )
    selection = [{
        "phase_num": 1,
        "phase_key": "phase_1",
        "path": ".",
        "name": "pwmcp",
        "service": {"path": ".", "name": "pwmcp", "enabled": True},
    }]
    monkeypatch.setattr(deploy, "bootstrap_workspace_env", lambda **_kw: None)
    monkeypatch.setattr(deploy, "enforce_standalone_root", lambda _cwd: None)
    monkeypatch.setattr(deploy, "resolve_repo_root", lambda *_a, **_kw: root)
    monkeypatch.setattr(deploy, "load_global_config", lambda _root: profile.config)
    monkeypatch.setattr(deploy, "resolve_profiles", lambda *_a, **_kw: profile)
    monkeypatch.setattr(deploy, "build_selection", lambda *_a, **_kw: selection)

    argv = ["--clean"] + (["--dry-run"] if dry_run else [])
    assert deploy.main(argv) == 0
    output = capsys.readouterr().out
    assert "[INFO] global-only root: nothing to clean" in output
    assert not docker_calls.exists()

