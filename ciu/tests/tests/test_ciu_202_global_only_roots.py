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
from ciu.config_constants import (  # noqa: E402
    GLOBAL_CONFIG_DEFAULTS,
    SHIPPED_COMPOSE,
    STACK_CONFIG_DEFAULTS,
)

from ciu.workspace_env import GENERATED_FACTS_KEYS, write_generated_facts  # noqa: E402

IDENTITY_NETWORK = "proj-abc123-network"


def _fake_docker(bin_dir: Path, state: Path, calls: Path) -> None:
    """A stateful fake `docker` on PATH: exact-name network ls/inspect/rm only.

    *state* lists the networks that exist (one per line); every invocation is
    appended to *calls*, so a test can prove docker was (not) touched.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    state.touch()
    script = bin_dir / "docker"
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "from pathlib import Path\n"
        f"state = Path({str(state)!r})\n"
        f"calls = Path({str(calls)!r})\n"
        "args = sys.argv[1:]\n"
        "with calls.open('a') as fh:\n"
        "    fh.write(' '.join(args) + '\\n')\n"
        "nets = [n for n in state.read_text().splitlines() if n]\n"
        "if args[:2] == ['network', 'ls']:\n"
        "    flt = args[args.index('--filter') + 1]\n"
        "    if flt.startswith('name=^'):\n"
        "        name = flt[len('name=^'):-1]\n"
        "        print(name if name in nets else '', end='')\n"
        "elif args[:2] == ['network', 'inspect']:\n"
        "    if args[2] not in nets:\n"
        "        sys.exit(1)\n"
        "    print('[{\"Containers\": {}}]')\n"
        "elif args[:2] == ['network', 'rm']:\n"
        "    state.write_text('\\n'.join(n for n in nets if n != args[2]) + '\\n')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


def _profile() -> SimpleNamespace:
    return SimpleNamespace(
        name=None,
        phase_keys=None,
        config={"deploy": {"project_name": "pwmcp", "environment_tag": "test"}},
    )


def _selection_for(path: str) -> dict:
    return {
        "phase_num": 1,
        "phase_key": "phase_1",
        "path": path,
        "name": "pwmcp",
        "service": {"path": path, "name": "pwmcp", "enabled": True},
    }


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False,
    )


def _copy_shape_file(source_root: Path, scratch: Path, relative: str) -> None:
    source = source_root / relative
    destination = scratch / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def test_committed_root_discovery_accepts_every_repo_root_shape(
    monkeypatch, tmp_path, capsys,
):
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

    # Regression for CIU-202 (the discovery above was never wrong): the shape
    # that used to make `ciu clean` fail is the global-only root, so classify
    # every discovered root and run the real clean entrypoint on the
    # global-only one. On the pre-fix behaviour the clean tries to render
    # `ciu.defaults.toml.j2` there and errors; a root the predicate gets wrong
    # either way (a stack root treated as no-op, or a global-only root
    # treated as a stack) fails the classification.
    global_only = {(scratch / "pwmcp" / "standalone").resolve()}
    for root in discovered:
        assert (root / GLOBAL_CONFIG_DEFAULTS).is_file(), root
        assert deploy._root_has_cleanable_stack(root) is (root not in global_only), root

    fake_bin = tmp_path / "fakebin"
    _fake_docker(fake_bin, tmp_path / "networks.state", tmp_path / "docker.calls")
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}")
    for root in sorted(global_only):
        monkeypatch.chdir(root)
        rc = deploy.action_clean(
            root, _profile(), [_selection_for(".")],
            ignore_errors=False, plan_only=True,
        )
        assert rc == 0, root
        assert "global-only root: nothing to clean" in capsys.readouterr().out
        assert not (tmp_path / "docker.calls").exists()
        # A non-vanilla real clean is the same no-op: exit 0, files untouched.
        assert deploy.action_clean(
            root, _profile(), [_selection_for(".")], ignore_errors=False,
        ) == 0
        assert not (tmp_path / "docker.calls").exists()
        assert (root / "ciu.install.override.toml.j2").is_file()


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


# --- F1: --vanilla and the identity network still run in a global-only root ---

_RESET_BODIES = {
    "ciu.global.toml": "[ciu]\nenv = 'rendered'\n",
    "ciu.env": f'export DOCKER_NETWORK_INTERNAL="{IDENTITY_NETWORK}"\n',
    "ciu.global.instance.toml.j2": "[ciu.instance]\nservice_profiles = ['core']\n",
    "ciu.instance.generated.toml": (
        f'[ciu.instance.generated]\nnetwork = "{IDENTITY_NETWORK}"\n'
    ),
}


def _global_only_workspace(tmp_path: Path, monkeypatch) -> tuple[Path, Path, Path]:
    """A global-only root that already ran `ciu env generate` (identity + network)."""
    assert set(_RESET_BODIES) == set(deploy.VANILLA_RESET_FILES)
    root = tmp_path / "pwmcp" / "standalone"
    root.mkdir(parents=True)
    (root / GLOBAL_CONFIG_DEFAULTS).write_text("[ciu]\n", encoding="utf-8")
    (root / "ciu.install.override.toml.j2").write_text("[install]\n", encoding="utf-8")
    for name, body in _RESET_BODIES.items():
        (root / name).write_text(body, encoding="utf-8")
    # The generated facts file must be COMPLETE (S3.1c) for ciu to read it.
    payload = {key: "" for key in GENERATED_FACTS_KEYS}
    payload.update(network=IDENTITY_NETWORK, instance_id="abc123")
    write_generated_facts(root, payload)
    for key in ("REPO_ROOT", "REPO_NAME", "INSTANCE_ID", "DOCKER_NETWORK_INTERNAL"):
        monkeypatch.delenv(key, raising=False)
    state = tmp_path / "networks.state"
    calls = tmp_path / "docker.calls"
    fake_bin = tmp_path / "fakebin"
    _fake_docker(fake_bin, state, calls)
    state.write_text(f"{IDENTITY_NETWORK}\n", encoding="utf-8")
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.chdir(root)
    monkeypatch.setattr(deploy, "_is_worktree_instance", lambda _root: True)
    return root, state, calls


def _clean(root: Path, **kw) -> int:
    return deploy.action_clean(
        root, _profile(), [_selection_for(".")], ignore_errors=False, **kw,
    )


def test_plain_clean_in_global_only_root_is_a_noop_that_keeps_every_file(
    monkeypatch, tmp_path, capsys,
):
    root, state, calls = _global_only_workspace(tmp_path, monkeypatch)
    assert _clean(root) == 0
    out = capsys.readouterr().out
    assert "[INFO] global-only root: nothing to clean" in out
    for name in _RESET_BODIES:
        assert (root / name).is_file(), name
    assert state.read_text().split() == [IDENTITY_NETWORK]
    assert not calls.exists()


def test_vanilla_clean_in_global_only_root_removes_the_generated_files(
    monkeypatch, tmp_path, capsys,
):
    root, state, calls = _global_only_workspace(tmp_path, monkeypatch)
    assert _clean(root, vanilla=True) == 0
    out = capsys.readouterr().out
    for name in _RESET_BODIES:
        assert not (root / name).exists(), name
    # Committed inputs are never part of the reset.
    assert (root / GLOBAL_CONFIG_DEFAULTS).is_file()
    assert (root / "ciu.install.override.toml.j2").is_file()
    assert "--vanilla: removed" in out
    # The message says what was done, not "nothing to clean".
    assert "nothing to clean" not in out
    assert "global-only root: no stacks to clean" in out


def test_vanilla_clean_in_global_only_root_removes_the_identity_network(
    monkeypatch, tmp_path, capsys,
):
    root, state, calls = _global_only_workspace(tmp_path, monkeypatch)
    assert _clean(root, vanilla=True) == 0
    assert state.read_text().split() == []
    assert f"network rm {IDENTITY_NETWORK}" in calls.read_text()
    assert f"Removed 1 network(s): {IDENTITY_NETWORK}" in capsys.readouterr().out
    # The network went BEFORE the files that name it.
    assert not (root / "ciu.instance.generated.toml").exists()


def test_vanilla_clean_in_global_only_main_workspace_keeps_an_attached_network(
    monkeypatch, tmp_path, capsys,
):
    root, state, calls = _global_only_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(deploy, "_is_worktree_instance", lambda _root: False)
    monkeypatch.setattr(deploy, "_workspace_network_attached", lambda _net: True)
    assert _clean(root, vanilla=True) == 0
    out = capsys.readouterr().out
    assert state.read_text().split() == [IDENTITY_NETWORK]
    assert f"kept: {IDENTITY_NETWORK}" in out
    for name in _RESET_BODIES:
        assert not (root / name).exists(), name


def test_vanilla_clean_in_global_only_root_keeps_files_when_network_removal_fails(
    monkeypatch, tmp_path, capsys,
):
    root, state, calls = _global_only_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        deploy, "_remove_identity_networks",
        lambda nets: ([], [(net, "endpoint 'x' could not be disconnected") for net in nets]),
    )
    assert _clean(root, vanilla=True) == 1
    for name in _RESET_BODIES:
        assert (root / name).is_file(), name
    assert "--vanilla skipped" in capsys.readouterr().out


def test_vanilla_dry_run_in_global_only_root_removes_nothing_and_says_what_it_would(
    monkeypatch, tmp_path, capsys,
):
    root, state, calls = _global_only_workspace(tmp_path, monkeypatch)
    assert _clean(root, vanilla=True, plan_only=True) == 0
    out = capsys.readouterr().out
    assert IDENTITY_NETWORK in out
    assert "ciu.instance.generated.toml" in out
    for name in _RESET_BODIES:
        assert (root / name).is_file(), name
    assert state.read_text().split() == [IDENTITY_NETWORK]
    assert "network rm" not in (calls.read_text() if calls.exists() else "")


def test_vanilla_clean_in_global_only_root_with_nothing_generated_succeeds(
    monkeypatch, tmp_path, capsys,
):
    root, state, calls = _global_only_workspace(tmp_path, monkeypatch)
    for name in _RESET_BODIES:
        (root / name).unlink()
    state.write_text("", encoding="utf-8")
    assert _clean(root, vanilla=True) == 0
    assert "--vanilla: nothing to remove" in capsys.readouterr().out


# --- F2: direct tests of the cleanable-stack predicate ---

def _touch(root: Path, relative: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x\n", encoding="utf-8")


_PREDICATE_CASES = {
    # (files to create, expected predicate)
    "empty-dir": ((), False),
    "global-only": (("ciu.global.defaults.toml.j2",), False),
    "global-only-with-install-override": (
        ("ciu.global.defaults.toml.j2", "ciu.install.override.toml.j2"), False,
    ),
    "install-override-only": (("ciu.install.override.toml.j2",), False),
    "stack-at-root": (("ciu.global.defaults.toml.j2", "ciu.defaults.toml.j2"), True),
    "shipped-compose-at-root": (
        ("ciu.global.defaults.toml.j2", "docker-compose.yml"), True,
    ),
    # nyxloom / tls-edge / topos: global marker at the root, stacks only below.
    "stacks-only-in-subdirectories": (
        ("ciu.global.defaults.toml.j2", "ntfy/ciu.defaults.toml.j2",
         "nyxloomd/ciu.defaults.toml.j2"), True,
    ),
    "stack-two-levels-down": (
        ("ciu.global.defaults.toml.j2", "a/b/ciu.defaults.toml.j2"), True,
    ),
    "shipped-compose-in-subdirectory": (
        ("ciu.global.defaults.toml.j2", "vendor/docker-compose.yml"), True,
    ),
    "similarly-named-non-ignored-dirs": (
        ("ciu.global.defaults.toml.j2", "volumes/s/ciu.defaults.toml.j2"), True,
    ),
    # A nested CIU root is its own owner: its stack is not this root's.
    "stack-only-in-nested-root": (
        ("ciu.global.defaults.toml.j2",
         "standalone/ciu.global.defaults.toml.j2",
         "standalone/app/ciu.defaults.toml.j2"), False,
    ),
    "nested-root-pruned-but-sibling-stack-counts": (
        ("ciu.global.defaults.toml.j2",
         "standalone/ciu.global.defaults.toml.j2",
         "standalone/app/ciu.defaults.toml.j2",
         "real/ciu.defaults.toml.j2"), True,
    ),
    "stack-only-in-.git": (
        ("ciu.global.defaults.toml.j2", ".git/x/ciu.defaults.toml.j2"), False,
    ),
    "stack-only-in-.worktrees": (
        ("ciu.global.defaults.toml.j2", ".worktrees/w/ciu.defaults.toml.j2"), False,
    ),
    "stack-only-in-artifacts": (
        ("ciu.global.defaults.toml.j2", "artifacts/x/ciu.defaults.toml.j2"), False,
    ),
    "stack-only-in-node_modules": (
        ("ciu.global.defaults.toml.j2", "node_modules/x/docker-compose.yml"), False,
    ),
    "stack-only-in-__pycache__": (
        ("ciu.global.defaults.toml.j2", "__pycache__/x/ciu.defaults.toml.j2"), False,
    ),
    "stack-only-in-.assay": (
        ("ciu.global.defaults.toml.j2", ".assay/x/ciu.defaults.toml.j2"), False,
    ),
    "stack-only-in-.run-gate": (
        ("ciu.global.defaults.toml.j2", ".run-gate/x/ciu.defaults.toml.j2"), False,
    ),
    "stack-only-in-vol-dir": (
        ("ciu.global.defaults.toml.j2", "vol-data/x/ciu.defaults.toml.j2"), False,
    ),
}


@pytest.mark.parametrize("case", sorted(_PREDICATE_CASES))
def test_root_has_cleanable_stack_shapes(tmp_path, case):
    files, expected = _PREDICATE_CASES[case]
    root = tmp_path / "root"
    root.mkdir()
    for relative in files:
        _touch(root, relative)
    assert deploy._root_has_cleanable_stack(root) is expected


def test_root_has_cleanable_stack_does_not_follow_symlinked_directories(tmp_path):
    outside = tmp_path / "outside"
    _touch(outside, "s/ciu.defaults.toml.j2")
    root = tmp_path / "root"
    root.mkdir()
    _touch(root, GLOBAL_CONFIG_DEFAULTS)
    (root / "linked").symlink_to(outside, target_is_directory=True)
    assert deploy._root_has_cleanable_stack(root) is False


def test_root_has_cleanable_stack_counts_the_marker_names_from_config_constants(tmp_path):
    # The two stack markers are exactly the CIU-native and shipped filenames.
    for marker in (STACK_CONFIG_DEFAULTS, SHIPPED_COMPOSE):
        root = tmp_path / marker.replace(".", "_")
        _touch(root, GLOBAL_CONFIG_DEFAULTS)
        _touch(root, f"s/{marker}")
        assert deploy._root_has_cleanable_stack(root) is True

