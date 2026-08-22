"""CIU-47 — `env generate` must not inherit an ambient PUBLIC_FQDN (S2.7).

The same masked-default family CIU-41 fixed for the identity tuple: a shell
that sourced another checkout's ciu.env carries that checkout's PUBLIC_FQDN,
and generate adopted it bare, making it the fresh worktree's recorded public
name. Refined precedence: derive from THIS workspace's own inputs first
(rendered global config `infrastructure.public_fqdn`, else reverse DNS of the
detected public IP); adopt the ambient value only when consistent, or when
detection yielded no independently sourced value to compare against (offline
host — the legitimate manual-override case).

Controlled wrong implementation: restoring the bare
``os.environ.get("PUBLIC_FQDN", ...)`` fallback fails
``test_mismatched_ambient_fqdn_*``.
"""

from __future__ import annotations

import os
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import workspace_env  # noqa: E402

_AMBIENT_KEYS = (
    "REPO_ROOT",
    "REPO_NAME",
    "INSTANCE_ID",
    "DOCKER_NETWORK_INTERNAL",
    "PUBLIC_FQDN",
    "PUBLIC_IP",
    "PUBLIC_TLS_CRT_PEM",
    "PUBLIC_TLS_KEY_PEM",
)


@pytest.fixture(autouse=True)
def _clean_ambient(monkeypatch):
    """This devcontainer's shell carries a sourced ciu.env — scrub it so
    assertions see only what each test sets. The ipify lookup is denied so
    every derivation below is deterministic regardless of host connectivity;
    tests needing a detected IP set PUBLIC_IP instead."""
    for key in _AMBIENT_KEYS:
        monkeypatch.delenv(key, raising=False)

    def _no_network(*_a, **_kw):
        raise urllib.error.URLError("tests are offline")

    monkeypatch.setattr(workspace_env.urllib.request, "urlopen", _no_network)


def _write_global_config(repo_root: Path, fqdn: str) -> None:
    """Rendered global config naming THIS workspace's own public FQDN."""
    ciu_global = repo_root / workspace_env.GLOBAL_CONFIG_RENDERED
    ciu_global.write_text(
        f'[infrastructure]\npublic_fqdn = "{fqdn}"\n', encoding="utf-8"
    )


def test_mismatched_ambient_fqdn_config_derived_wins(monkeypatch, tmp_path, capsys):
    """Oracle 1: main's FQDN exported in a worktree whose config names this
    workspace's own FQDN — the derived value is written and the ambient one
    is named in a stderr warning."""
    _write_global_config(tmp_path, "box-new.example.com")
    monkeypatch.setenv("PUBLIC_FQDN", "main-stack.example.com")

    values = workspace_env._detect_public_fqdn(tmp_path, require_fqdn=False)

    assert values["PUBLIC_FQDN"] == "box-new.example.com"
    err = capsys.readouterr().err
    assert "ignoring pre-set PUBLIC_FQDN='main-stack.example.com'" in err
    assert "box-new.example.com" in err


def test_consistent_ambient_fqdn_is_silent_and_kept(monkeypatch, tmp_path, capsys):
    """Oracle 2: a pre-set value equal to the derived one changes nothing."""
    _write_global_config(tmp_path, "box.example.com")
    monkeypatch.setenv("PUBLIC_FQDN", "box.example.com")

    values = workspace_env._detect_public_fqdn(tmp_path, require_fqdn=False)

    assert values["PUBLIC_FQDN"] == "box.example.com"
    assert capsys.readouterr().err == ""


def test_unset_ambient_plain_derivation_from_config(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("PUBLIC_FQDN", raising=False)
    _write_global_config(tmp_path, "box.example.com")

    values = workspace_env._detect_public_fqdn(tmp_path, require_fqdn=False)

    assert values["PUBLIC_FQDN"] == "box.example.com"
    assert capsys.readouterr().err == ""


def test_reverse_dns_derivation_beats_stale_ambient(monkeypatch, tmp_path, capsys):
    """No config entry: re-detection via the same detection path (reverse DNS
    of the detected IP) outranks a mismatching ambient value."""
    monkeypatch.setenv("PUBLIC_IP", "203.0.113.5")
    monkeypatch.setenv("PUBLIC_FQDN", "main-stack.example.com")
    monkeypatch.setattr(
        workspace_env.socket,
        "gethostbyaddr",
        lambda ip: ("box.example.com", [], [ip]),
    )

    values = workspace_env._detect_public_fqdn(tmp_path, require_fqdn=False)

    assert values["PUBLIC_FQDN"] == "box.example.com"
    err = capsys.readouterr().err
    assert "ignoring pre-set PUBLIC_FQDN='main-stack.example.com'" in err


def test_offline_detection_keeps_ambient_override_silently(
    monkeypatch, tmp_path, capsys
):
    """No config entry AND no detection result at all (offline host): there is
    no independent signal, so the explicit pre-set FQDN stands — silently."""
    monkeypatch.setenv("PUBLIC_FQDN", "operator-set.example.com")

    values = workspace_env._detect_public_fqdn(tmp_path, require_fqdn=False)

    assert values["PUBLIC_FQDN"] == "operator-set.example.com"
    assert capsys.readouterr().err == ""


def test_failed_reverse_dns_with_ambient_kept(monkeypatch, tmp_path, capsys):
    """Detection RAN but produced nothing (herror): ambient kept, no warning —
    a failed lookup is not a derivation, never a basis to override."""
    monkeypatch.setenv("PUBLIC_IP", "203.0.113.5")
    monkeypatch.setenv("PUBLIC_FQDN", "operator-set.example.com")

    def _fail(ip):
        raise workspace_env.socket.herror(1, "Unknown host")

    monkeypatch.setattr(workspace_env.socket, "gethostbyaddr", _fail)

    values = workspace_env._detect_public_fqdn(tmp_path, require_fqdn=False)

    assert values["PUBLIC_FQDN"] == "operator-set.example.com"
    assert capsys.readouterr().err == ""


def test_empty_ambient_value_is_absence_not_contamination(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("PUBLIC_FQDN", "")
    _write_global_config(tmp_path, "box.example.com")

    values = workspace_env._detect_public_fqdn(tmp_path, require_fqdn=False)

    assert values["PUBLIC_FQDN"] == "box.example.com"
    assert capsys.readouterr().err == ""


def test_require_fqdn_without_ambient_or_derivation_raises(monkeypatch, tmp_path):
    with pytest.raises(workspace_env.WorkspaceEnvError, match="PUBLIC_FQDN"):
        workspace_env._detect_public_fqdn(tmp_path, require_fqdn=True)


def test_generate_ciu_env_writes_derived_fqdn_under_contaminated_shell(
    monkeypatch, tmp_path
):
    """End to end: generated ciu.env carries the DERIVED value, never ambient."""
    _write_global_config(tmp_path, "box.example.com")
    monkeypatch.setenv("PUBLIC_FQDN", "main-stack.example.com")
    monkeypatch.setattr(
        workspace_env, "_detect_physical_repo_root", lambda repo_root: tmp_path
    )

    env_path = workspace_env.generate_ciu_env(tmp_path)

    body = env_path.read_text(encoding="utf-8")
    assert 'export PUBLIC_FQDN="box.example.com"' in body
    assert "main-stack.example.com" not in body


def test_bootstrap_after_generate_adopts_file_fqdn_over_ambient(
    monkeypatch, tmp_path
):
    """CIU-47 enforcement half: post-generate steps act on the FILE's FQDN,
    not stale ambient state (mirrors CIU-41's adopt_file_identity)."""
    _write_global_config(tmp_path, "box.example.com")
    monkeypatch.setenv("PUBLIC_FQDN", "main-stack.example.com")
    monkeypatch.setattr(
        workspace_env, "_detect_physical_repo_root", lambda repo_root: tmp_path
    )
    monkeypatch.setattr(
        workspace_env, "ensure_workspace_network", lambda name, auto_connect=True: None
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

    assert os.environ.get("PUBLIC_FQDN") == "box.example.com"
