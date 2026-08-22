"""CIU-41 — `env generate` must not inherit ambient identity values (S2.7).

The derived identity tuple (REPO_NAME / INSTANCE_ID / DOCKER_NETWORK_INTERNAL)
is computed from THIS physical root alone. A pre-set ambient value wins only
when consistent with the derived one; on mismatch the derived value is used
and a warning names the ignored ambient value.

Controlled wrong implementation: restoring the bare
``os.environ.get("DOCKER_NETWORK_INTERNAL", derived)`` fallback in
``_compute_network_name`` fails ``test_mismatched_ambient_network_*``.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import workspace_env  # noqa: E402

_IDENTITY_KEYS = ("REPO_ROOT", "REPO_NAME", "INSTANCE_ID", "DOCKER_NETWORK_INTERNAL")


@pytest.fixture(autouse=True)
def _clean_ambient_identity(monkeypatch):
    """This devcontainer's shell carries a sourced ciu.env (REPO_NAME=dstdns,
    INSTANCE_ID=98535c, ...) — the exact contamination CIU-41 addresses.
    Scrub it so assertions see only what each test sets."""
    for key in _IDENTITY_KEYS:
        monkeypatch.delenv(key, raising=False)


def _derived(root: Path) -> dict:
    return workspace_env._compute_network_name(root)


def _expected(root: Path) -> tuple[str, str, str]:
    repo_name = root.name.lower()
    instance_id = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:6]
    return repo_name, instance_id, f"{repo_name}-{instance_id}-network"


def test_mismatched_ambient_network_ignored_derived_wins(monkeypatch, tmp_path, capsys):
    """A fresh-worktree generate with main's network exported stays Mode-B."""
    repo_name, instance_id, network = _expected(tmp_path)
    monkeypatch.setenv("DOCKER_NETWORK_INTERNAL", "dstdns-98535c-network")

    values = _derived(tmp_path)

    assert values == {
        "REPO_NAME": repo_name,
        "INSTANCE_ID": instance_id,
        "DOCKER_NETWORK_INTERNAL": network,
    }
    err = capsys.readouterr().err
    assert "ignoring pre-set DOCKER_NETWORK_INTERNAL='dstdns-98535c-network'" in err
    assert network in err
    assert "--shared-infra" in err


def test_consistent_ambient_network_is_silent_and_kept(monkeypatch, tmp_path, capsys):
    _, _, network = _expected(tmp_path)
    monkeypatch.setenv("DOCKER_NETWORK_INTERNAL", network)

    values = _derived(tmp_path)

    assert values["DOCKER_NETWORK_INTERNAL"] == network
    assert capsys.readouterr().err == ""


def test_unset_ambient_network_plain_derivation(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("DOCKER_NETWORK_INTERNAL", raising=False)

    _, _, network = _expected(tmp_path)

    assert _derived(tmp_path)["DOCKER_NETWORK_INTERNAL"] == network
    assert capsys.readouterr().err == ""


def test_mismatched_ambient_repo_name_and_instance_warn_never_adopted(
    monkeypatch, tmp_path, capsys
):
    """Contamination signal for the whole tuple; values are never adopted."""
    repo_name, instance_id, _ = _expected(tmp_path)
    monkeypatch.setenv("REPO_NAME", "dstdns")
    monkeypatch.setenv("INSTANCE_ID", "98535c")

    values = _derived(tmp_path)

    assert values["REPO_NAME"] == repo_name
    assert values["INSTANCE_ID"] == instance_id
    err = capsys.readouterr().err
    assert "ignoring pre-set REPO_NAME='dstdns'" in err
    assert "ignoring pre-set INSTANCE_ID='98535c'" in err


def test_empty_ambient_value_is_not_a_warning(monkeypatch, tmp_path, capsys):
    """An empty ambient value is absence, not contamination — stay silent."""
    monkeypatch.setenv("DOCKER_NETWORK_INTERNAL", "")

    _, _, network = _expected(tmp_path)

    assert _derived(tmp_path)["DOCKER_NETWORK_INTERNAL"] == network
    assert capsys.readouterr().err == ""


def test_generate_ciu_env_writes_derived_network_under_contaminated_shell(
    monkeypatch, tmp_path
):
    """End to end: generated ciu.env carries the DERIVED identity, never ambient."""
    monkeypatch.setenv("DOCKER_NETWORK_INTERNAL", "main-stack-network")
    monkeypatch.setattr(
        workspace_env, "_detect_physical_repo_root", lambda repo_root: tmp_path
    )
    # Keep the detector surface hermetic: no host TLS/FQDN facts in play.
    monkeypatch.delenv("PUBLIC_FQDN", raising=False)
    monkeypatch.delenv("PUBLIC_TLS_CRT", raising=False)
    monkeypatch.delenv("PUBLIC_TLS_KEY", raising=False)

    env_path = workspace_env.generate_ciu_env(tmp_path)

    body = env_path.read_text(encoding="utf-8")
    _, instance_id, network = _expected(tmp_path)
    assert f'export DOCKER_NETWORK_INTERNAL="{network}"' in body
    assert "main-stack-network" not in body
    assert instance_id in body


def test_bootstrap_after_generate_prefers_file_identity_over_ambient(
    monkeypatch, tmp_path
):
    """Post-generate steps act on the FILE's identity, not stale ambient state.

    The shell's contaminated DOCKER_NETWORK_INTERNAL must not choose the
    network the bootstrap creates/attaches: the ciu.env written moments ago
    for THIS root names the derived network, and that is what is used.
    """
    monkeypatch.setenv("DOCKER_NETWORK_INTERNAL", "main-stack-network")
    monkeypatch.setattr(
        workspace_env, "_detect_physical_repo_root", lambda repo_root: tmp_path
    )
    attached: list[str] = []
    monkeypatch.setattr(
        workspace_env,
        "ensure_workspace_network",
        lambda name, auto_connect=True: attached.append(name),
    )
    monkeypatch.setattr(workspace_env, "_check_tls_access", lambda: None)

    workspace_env.bootstrap_workspace_env(
        start_dir=tmp_path,
        define_root=None,
        defaults_filename="ciu.global.defaults.toml.j2",
        generate_env=True,
        update_cert_permission=False,
        required_keys=(),
    )

    _, _, network = _expected(tmp_path)
    assert attached == [network]
    assert os.environ.get("DOCKER_NETWORK_INTERNAL") == network
