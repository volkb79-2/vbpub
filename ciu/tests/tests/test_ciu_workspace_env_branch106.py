"""Focused branch coverage for workspace environment fallbacks and bootstrap."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import workspace_env  # noqa: E402


def _result(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_physical_root_uses_repo_fallback_when_docker_labels_are_blank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PHYSICAL_REPO_ROOT", raising=False)
    monkeypatch.setattr(workspace_env, "_physical_root_from_mountinfo", lambda _root: None)
    monkeypatch.setattr(
        workspace_env.subprocess,
        "run",
        lambda *_args, **_kwargs: _result(stdout="\n  \n"),
    )

    assert workspace_env._detect_physical_repo_root(tmp_path) == tmp_path.resolve()


def test_physical_root_skips_blank_label_before_accepting_valid_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PHYSICAL_REPO_ROOT", raising=False)
    monkeypatch.setattr(workspace_env, "_physical_root_from_mountinfo", lambda _root: None)
    monkeypatch.setattr(
        workspace_env.subprocess,
        "run",
        lambda *_args, **_kwargs: _result(stdout=f"\n  \n{tmp_path / 'host-repo'}\n"),
    )

    assert workspace_env._detect_physical_repo_root(tmp_path) == (tmp_path / "host-repo").resolve()


def test_host_tmp_inspect_with_blank_mount_source_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HOST_MDT_TMP", raising=False)
    monkeypatch.setenv("DEVCONTAINER_NAME", "ciu-cockpit")
    monkeypatch.setattr(
        workspace_env.subprocess,
        "run",
        lambda *_args, **_kwargs: _result(stdout=" \n\t"),
    )

    assert workspace_env._detect_host_mdt_tmp() == ""


# --- CIU-59: detect_devcontainer_name() -- the factored helper -----------


def test_detect_devcontainer_name_prefers_the_devcontainer_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEVCONTAINER_NAME", "ciu-cockpit")
    monkeypatch.setenv("HOSTNAME", "ignored")
    assert workspace_env.detect_devcontainer_name() == "ciu-cockpit"


def test_detect_devcontainer_name_falls_back_to_hostname_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEVCONTAINER_NAME", raising=False)
    monkeypatch.setenv("HOSTNAME", "runner-7")
    assert workspace_env.detect_devcontainer_name() == "runner-7"


def test_detect_devcontainer_name_treats_an_explicit_empty_value_as_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The semantic fix this factoring makes: before CIU-59, the third call
    site (`generate_ciu_env`'s `ciu env print` report row) used a nested
    ``os.environ.get("DEVCONTAINER_NAME", os.environ.get("HOSTNAME", ""))``
    that returned the PRESENT-but-empty ``DEVCONTAINER_NAME`` verbatim,
    diverging from the other three (`or`-based) call sites, which fall
    through to ``HOSTNAME`` for a falsy value regardless of whether the key
    is present or absent. The shared helper standardizes on the `or` shape:
    an explicitly-empty ``DEVCONTAINER_NAME`` now falls through everywhere.
    """
    monkeypatch.setenv("DEVCONTAINER_NAME", "")
    monkeypatch.setenv("HOSTNAME", "runner-7")
    assert workspace_env.detect_devcontainer_name() == "runner-7"


def test_detect_devcontainer_name_empty_when_neither_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEVCONTAINER_NAME", raising=False)
    monkeypatch.delenv("HOSTNAME", raising=False)
    assert workspace_env.detect_devcontainer_name() == ""


@pytest.mark.usefixtures("real_network_side_effects")  # CIU-87 gate opt-out
def test_network_creation_failure_preserves_daemon_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_env, "_docker_available", lambda: True)
    responses = iter([_result(returncode=1), _result(returncode=1, stderr="network quota exhausted")])
    monkeypatch.setattr(workspace_env.subprocess, "run", lambda *_args, **_kwargs: next(responses))

    with pytest.raises(workspace_env.WorkspaceEnvError, match="network quota exhausted"):
        workspace_env._ensure_network_exists("ciu-network")


@pytest.mark.usefixtures("real_network_side_effects")  # CIU-87 gate opt-out
def test_network_creation_succeeds_after_absent_network_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An absent shared network is created successfully before deployment continues."""
    monkeypatch.setattr(workspace_env, "_docker_available", lambda: True)
    responses = iter([_result(returncode=1), _result(returncode=0)])
    monkeypatch.setattr(workspace_env.subprocess, "run", lambda *_args, **_kwargs: next(responses))

    assert workspace_env._ensure_network_exists("ciu-network") is None


def test_ensure_workspace_network_without_auto_connect_only_ensures_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, str]] = []
    monkeypatch.setattr(
        workspace_env,
        "_ensure_network_exists",
        lambda name, **_kwargs: events.append(("ensure", name)),
    )
    monkeypatch.setattr(
        workspace_env,
        "_connect_devcontainer_to_network",
        lambda name: events.append(("connect", name)),
    )

    workspace_env.ensure_workspace_network("ciu-network", auto_connect=False)

    assert events == [("ensure", "ciu-network")]


def test_managed_startup_verifies_network_before_and_after_ensure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[object, ...]] = []
    monkeypatch.setattr(workspace_env, "_test_suite_gate_active", lambda: False)
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("INSTANCE_ID", "managed-1")

    from ciu import worktree

    def verify(root, *, expected_instance_id, expected_network):
        events.append(("verify", root, expected_instance_id, expected_network))

    monkeypatch.setattr(worktree, "verify_own_runtime_network", verify)
    monkeypatch.setattr(
        workspace_env,
        "_ensure_network_exists",
        lambda name, **_kwargs: events.append(("ensure", name)),
    )
    monkeypatch.setattr(
        workspace_env,
        "_connect_devcontainer_to_network",
        lambda name: events.append(("connect", name)),
    )

    workspace_env.ensure_workspace_network(
        "managed-net", verify_managed_instance=True
    )

    assert events == [
        ("verify", tmp_path.resolve(), "managed-1", "managed-net"),
        ("ensure", "managed-net"),
        ("verify", tmp_path.resolve(), "managed-1", "managed-net"),
        ("connect", "managed-net"),
    ]


def test_managed_startup_refuses_before_network_side_effect_on_unknown_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    monkeypatch.setattr(workspace_env, "_test_suite_gate_active", lambda: False)
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("INSTANCE_ID", "managed-1")

    from ciu import worktree

    def refuse(*_args, **_kwargs):
        events.append("verify")
        raise worktree.RuntimeNetworkOwnershipUnprovenError("unknown owner")

    monkeypatch.setattr(worktree, "verify_own_runtime_network", refuse)
    monkeypatch.setattr(
        workspace_env,
        "_ensure_network_exists",
        lambda *_args, **_kwargs: events.append("ensure"),
    )
    monkeypatch.setattr(
        workspace_env,
        "_connect_devcontainer_to_network",
        lambda *_args, **_kwargs: events.append("connect"),
    )

    with pytest.raises(workspace_env.WorkspaceEnvError, match="unknown owner"):
        workspace_env.ensure_workspace_network(
            "managed-net", verify_managed_instance=True
        )

    assert events == ["verify"]


def test_generated_bootstrap_without_network_still_runs_tls_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / "ciu.env"
    events: list[str] = []

    def generate(root: Path) -> Path:
        assert root == tmp_path
        env_path.write_text('export DOCKER_NETWORK_INTERNAL=""\n', encoding="utf-8")
        return env_path

    monkeypatch.delenv("DOCKER_NETWORK_INTERNAL", raising=False)
    monkeypatch.setattr(workspace_env, "generate_ciu_env", generate)
    monkeypatch.setattr(
        workspace_env,
        "ensure_workspace_network",
        lambda _name, **_kwargs: events.append("network"),
    )
    monkeypatch.setattr(workspace_env, "_check_tls_access", lambda: events.append("tls"))

    assert workspace_env.bootstrap_env_init(tmp_path) == env_path
    assert events == ["tls"]

def test_generated_workspace_bootstrap_without_network_probes_tls_and_validates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, write_instance_facts
) -> None:
    env_path = tmp_path / "ciu.env"
    events: list[str] = []

    def generate(root: Path, **_kw) -> Path:
        # **_kw: CIU-75's bootstrap passes notice_stream=sys.stderr so the
        # deprecation notice cannot land on `ciu check --json`'s stdout.
        assert root == tmp_path
        env_path.write_text(
            'DOCKER_NETWORK_INTERNAL=""\n',
            encoding="utf-8",
        )
        write_instance_facts(
            root,
            repo_root=str(root),
            physical_repo_root=str(root),
            network="",
        )
        return env_path

    monkeypatch.delenv("DOCKER_NETWORK_INTERNAL", raising=False)
    monkeypatch.setattr(workspace_env, "generate_ciu_env", generate)
    monkeypatch.setattr(
        workspace_env,
        "ensure_workspace_network",
        lambda _name, **_kwargs: events.append("network"),
    )
    monkeypatch.setattr(workspace_env, "_check_tls_access", lambda: events.append("tls"))

    assert workspace_env.bootstrap_workspace_env(
        start_dir=tmp_path,
        define_root=tmp_path,
        defaults_filename="ciu.global.defaults.toml.j2",
        generate_env=True,
        update_cert_permission=False,
        required_keys=("REPO_ROOT",),
    ) == tmp_path.resolve()
    assert events == ["tls"]
