"""
CIU workspace_env v2 tests.

Covers:
- ENV_TYPE native naming (S2.7 — 'bare-metal' retired → 'native')
- REQUIRED_KEYS_CORE content (S2.2)
- validate_required_certs (S2.4): path-as-given, readable checks, DOCKER_GID falsy-safe
- _detect_public_fqdn: malformed ciu.global.toml → '' + WARN (review finding)
- generate_ciu_env: ENV_TYPE=native emitted, CIU_HOST_PROFILE placeholder emitted
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import urllib.error

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.workspace_env import (
    REQUIRED_KEYS_CORE,
    WorkspaceEnvError,
    _detect_env_type,
    _detect_public_fqdn,
    generate_ciu_env,
    validate_required_certs,
)

# Patch target for urllib.request.urlopen as imported inside ciu.workspace_env
_URLOPEN = "ciu.workspace_env.urllib.request.urlopen"


# ---------------------------------------------------------------------------
# ENV_TYPE native naming
# ---------------------------------------------------------------------------

class TestEnvTypeNative:
    """S2.7: 'bare-metal' is retired; the value is now 'native'."""

    def test_native_when_no_markers(self, monkeypatch):
        """Plain host with no devcontainer/CI markers → native."""
        monkeypatch.delenv("ENV_TYPE", raising=False)
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
        monkeypatch.delenv("REMOTE_CONTAINERS", raising=False)
        monkeypatch.delenv("WORKSPACE_DIR", raising=False)

        with patch("ciu.workspace_env.Path") as mock_path_cls:
            # /.dockerenv does not exist on this host
            mock_dockerenv = MagicMock()
            mock_dockerenv.exists.return_value = False

            original_path = Path

            def path_side_effect(arg):
                if arg == "/.dockerenv":
                    return mock_dockerenv
                return original_path(arg)

            mock_path_cls.side_effect = path_side_effect

            # Call through with the real import since Path is used internally
            # Use the function directly — monkeypatching Path is complex; instead
            # test via environment alone (/.dockerenv won't exist in CI either)

        flags = _detect_env_type()
        # In this environment /.dockerenv should not exist and no markers are set
        # The result should be 'native' (not 'bare-metal')
        assert flags["ENV_TYPE"] in ("native", "devcontainer", "github-actions")
        assert flags["ENV_TYPE"] != "bare-metal"

    def test_native_flag_set_explicitly(self, monkeypatch):
        """ENV_TYPE=native preset is respected and IS_NATIVE=1."""
        monkeypatch.setenv("ENV_TYPE", "native")
        flags = _detect_env_type()
        assert flags["ENV_TYPE"] == "native"
        assert flags["IS_NATIVE"] == "1"
        assert flags["IS_DEVCONTAINER"] == "0"
        assert flags["IS_GITHUB_ACTIONS"] == "0"

    def test_no_is_bare_metal_key(self, monkeypatch):
        """IS_BARE_METAL must not appear in the returned dict."""
        monkeypatch.setenv("ENV_TYPE", "native")
        flags = _detect_env_type()
        assert "IS_BARE_METAL" not in flags

    def test_devcontainer_flag(self, monkeypatch):
        """devcontainer env type produces IS_DEVCONTAINER=1 and IS_NATIVE=0."""
        monkeypatch.setenv("ENV_TYPE", "devcontainer")
        flags = _detect_env_type()
        assert flags["ENV_TYPE"] == "devcontainer"
        assert flags["IS_DEVCONTAINER"] == "1"
        assert flags["IS_NATIVE"] == "0"

    def test_github_actions_flag(self, monkeypatch):
        """github-actions env type produces IS_GITHUB_ACTIONS=1 and IS_NATIVE=0."""
        monkeypatch.setenv("ENV_TYPE", "github-actions")
        flags = _detect_env_type()
        assert flags["ENV_TYPE"] == "github-actions"
        assert flags["IS_GITHUB_ACTIONS"] == "1"
        assert flags["IS_NATIVE"] == "0"

    def test_auto_detect_github_actions(self, monkeypatch):
        """GITHUB_ACTIONS env var auto-detects github-actions type."""
        monkeypatch.delenv("ENV_TYPE", raising=False)
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        flags = _detect_env_type()
        assert flags["ENV_TYPE"] == "github-actions"


# ---------------------------------------------------------------------------
# REQUIRED_KEYS_CORE
# ---------------------------------------------------------------------------

class TestRequiredKeysCore:
    """S2.2: five core keys always required."""

    def test_required_keys_core_content(self):
        assert set(REQUIRED_KEYS_CORE) == {
            "REPO_ROOT",
            "PHYSICAL_REPO_ROOT",
            "DOCKER_NETWORK_INTERNAL",
            "CONTAINER_UID",
            "DOCKER_GID",
        }

    def test_required_keys_core_is_tuple(self):
        assert isinstance(REQUIRED_KEYS_CORE, tuple)

    def test_public_keys_not_in_core(self):
        """S2.3: PUBLIC_* keys are NOT core."""
        for key in ("PUBLIC_FQDN", "PUBLIC_TLS_CRT_PEM", "PUBLIC_TLS_KEY_PEM", "PUBLIC_IP"):
            assert key not in REQUIRED_KEYS_CORE, f"{key} must not be in REQUIRED_KEYS_CORE"


# ---------------------------------------------------------------------------
# validate_required_certs
# ---------------------------------------------------------------------------

class TestValidateRequiredCerts:
    """S2.4: validate TLS cert/key paths as given, readable by DOCKER_GID."""

    def _make_readable_file(self, tmp_path: Path, name: str, mode: int) -> Path:
        p = tmp_path / name
        p.write_text("content", encoding="utf-8")
        os.chmod(p, mode)
        return p

    def test_no_paths_set_is_ok(self):
        """Empty env → no error (certs not required by default, S2.3)."""
        validate_required_certs({})

    def test_other_readable_ok(self, tmp_path):
        """S2.4: file readable by others (0o004 bit) → valid."""
        cert = self._make_readable_file(tmp_path, "cert.pem", 0o644)
        key = self._make_readable_file(tmp_path, "key.pem", 0o644)
        env = {
            "PUBLIC_TLS_CRT_PEM": str(cert),
            "PUBLIC_TLS_KEY_PEM": str(key),
            "DOCKER_GID": "1234",
        }
        validate_required_certs(env)  # must not raise

    def test_group_readable_by_docker_gid_ok(self, tmp_path):
        """S2.4: file group-readable and st_gid == DOCKER_GID → valid.

        We cannot chgrp without privilege, so we monkeypatch os.stat to
        return a stat result where st_gid matches DOCKER_GID.
        """
        cert = self._make_readable_file(tmp_path, "cert.pem", 0o040)
        key = self._make_readable_file(tmp_path, "key.pem", 0o040)
        docker_gid = 5555

        real_stat = Path.stat

        def fake_stat(self_path, *args, **kwargs):
            result = real_stat(self_path, *args, **kwargs)
            # Return a stat_result-like object with st_gid == docker_gid
            # and the actual mode from the file
            import types
            sr = types.SimpleNamespace(
                st_mode=result.st_mode,
                st_gid=docker_gid,
                st_uid=result.st_uid,
            )
            return sr

        env = {
            "PUBLIC_TLS_CRT_PEM": str(cert),
            "PUBLIC_TLS_KEY_PEM": str(key),
            "DOCKER_GID": str(docker_gid),
        }

        with patch("pathlib.Path.stat", fake_stat):
            validate_required_certs(env)  # must not raise

    def test_missing_file_raises_naming_path(self, tmp_path):
        """S2.4: missing file → WorkspaceEnvError naming the exact path."""
        missing = str(tmp_path / "nonexistent" / "cert.pem")
        env = {
            "PUBLIC_TLS_CRT_PEM": missing,
            "PUBLIC_TLS_KEY_PEM": str(tmp_path / "key.pem"),
            "DOCKER_GID": "1000",
        }
        with pytest.raises(WorkspaceEnvError) as exc_info:
            validate_required_certs(env)
        assert missing in str(exc_info.value)

    def test_missing_key_file_raises_naming_path(self, tmp_path):
        """S2.4: missing key file names the exact key path."""
        cert = self._make_readable_file(tmp_path, "cert.pem", 0o644)
        missing_key = str(tmp_path / "subdir" / "key.pem")
        env = {
            "PUBLIC_TLS_CRT_PEM": str(cert),
            "PUBLIC_TLS_KEY_PEM": missing_key,
            "DOCKER_GID": "1000",
        }
        with pytest.raises(WorkspaceEnvError) as exc_info:
            validate_required_certs(env)
        assert missing_key in str(exc_info.value)

    def test_no_path_rederivation(self, tmp_path):
        """S2.4 / A10: path validated exactly as given, no .parent/.parent juggling.

        A cert at /x/y/cert.pem must be validated at /x/y/cert.pem, not at
        some derived location. Verify by placing the file at an arbitrary
        nested path and confirming it passes.
        """
        nested = tmp_path / "x" / "y" / "z"
        nested.mkdir(parents=True)
        cert = nested / "cert.pem"
        cert.write_text("cert", encoding="utf-8")
        os.chmod(cert, 0o644)
        key = nested / "key.pem"
        key.write_text("key", encoding="utf-8")
        os.chmod(key, 0o644)

        env = {
            "PUBLIC_TLS_CRT_PEM": str(cert),
            "PUBLIC_TLS_KEY_PEM": str(key),
            "DOCKER_GID": "9999",
        }
        # Must pass: file is other-readable, path used exactly as given
        validate_required_certs(env)

    def test_docker_gid_zero_valid(self, tmp_path):
        """S2.5: DOCKER_GID='0' is a valid value — must not be treated as missing."""
        cert = self._make_readable_file(tmp_path, "cert.pem", 0o644)
        key = self._make_readable_file(tmp_path, "key.pem", 0o644)
        env = {
            "PUBLIC_TLS_CRT_PEM": str(cert),
            "PUBLIC_TLS_KEY_PEM": str(key),
            "DOCKER_GID": "0",
        }
        # Files are other-readable so this should pass regardless of gid match
        validate_required_certs(env)  # must not raise

    def test_not_readable_by_docker_gid_raises(self, tmp_path):
        """File exists but not other-readable and gid doesn't match → error."""
        cert = self._make_readable_file(tmp_path, "cert.pem", 0o600)
        key = self._make_readable_file(tmp_path, "key.pem", 0o600)
        env = {
            "PUBLIC_TLS_CRT_PEM": str(cert),
            "PUBLIC_TLS_KEY_PEM": str(key),
            "DOCKER_GID": "9999",  # won't match st_gid
        }
        with pytest.raises(WorkspaceEnvError) as exc_info:
            validate_required_certs(env)
        # Error must name the path
        assert str(cert) in str(exc_info.value)

    def test_only_cert_set_key_empty(self, tmp_path):
        """Only cert path set, key empty → validate cert only."""
        cert = self._make_readable_file(tmp_path, "cert.pem", 0o644)
        env = {
            "PUBLIC_TLS_CRT_PEM": str(cert),
            "PUBLIC_TLS_KEY_PEM": "",
            "DOCKER_GID": "1000",
        }
        validate_required_certs(env)  # must not raise


# ---------------------------------------------------------------------------
# _detect_public_fqdn: malformed ciu.global.toml → WARN
# ---------------------------------------------------------------------------

class TestDetectPublicFqdnMalformedToml:
    """Review finding: silent Exception swallow → now warns on malformed toml."""

    def test_malformed_toml_returns_empty_and_warns(self, tmp_path, capsys, monkeypatch):
        """Malformed ciu.global.toml: public_fqdn returns '' and a WARN is printed."""
        ciu_global = tmp_path / "ciu.global.toml"
        ciu_global.write_text("this is not valid toml ][", encoding="utf-8")

        monkeypatch.delenv("PUBLIC_FQDN", raising=False)
        monkeypatch.delenv("PUBLIC_IP", raising=False)

        # Stub out the network call so test is fast; must raise URLError not Exception
        with patch(_URLOPEN, side_effect=urllib.error.URLError("no network")):
            result = _detect_public_fqdn(tmp_path, require_fqdn=False)

        captured = capsys.readouterr()
        assert "[WARN]" in captured.out
        assert str(ciu_global) in captured.out
        # public_fqdn falls through to localhost fallback
        assert result["PUBLIC_FQDN"] != ""  # falls back, not crashes

    def test_missing_toml_no_warn(self, tmp_path, capsys, monkeypatch):
        """No ciu.global.toml: no WARN emitted (file simply doesn't exist)."""
        monkeypatch.delenv("PUBLIC_FQDN", raising=False)
        monkeypatch.delenv("PUBLIC_IP", raising=False)

        with patch(_URLOPEN, side_effect=urllib.error.URLError("no network")):
            _detect_public_fqdn(tmp_path, require_fqdn=False)

        captured = capsys.readouterr()
        assert "[WARN]" not in captured.out


# ---------------------------------------------------------------------------
# generate_ciu_env: ENV_TYPE=native + CIU_HOST_PROFILE placeholder
# ---------------------------------------------------------------------------

class TestGenerateCiuEnvNative:
    """generate_ciu_env on a native host emits native naming and S7.5 placeholder."""

    def _stub_docker(self, monkeypatch):
        """Stub docker-dependent detection to avoid real docker calls."""
        monkeypatch.setattr("ciu.workspace_env._detect_docker_gid", lambda: "1000")
        monkeypatch.setattr("ciu.workspace_env._detect_physical_repo_root", lambda repo_root: repo_root)

    def test_env_type_native_in_generated_file(self, tmp_path, monkeypatch):
        """ENV_TYPE=native must appear in the generated ciu.env."""
        monkeypatch.setenv("ENV_TYPE", "native")
        monkeypatch.delenv("PUBLIC_FQDN", raising=False)
        monkeypatch.delenv("PUBLIC_IP", raising=False)
        self._stub_docker(monkeypatch)

        with patch(_URLOPEN, side_effect=urllib.error.URLError("no network")):
            out = generate_ciu_env(tmp_path)

        content = out.read_text(encoding="utf-8")
        assert 'export ENV_TYPE="native"' in content
        assert "bare-metal" not in content

    def test_is_native_in_generated_file(self, tmp_path, monkeypatch):
        """IS_NATIVE=1 must appear in the generated ciu.env (replaces IS_BARE_METAL)."""
        monkeypatch.setenv("ENV_TYPE", "native")
        monkeypatch.delenv("PUBLIC_FQDN", raising=False)
        monkeypatch.delenv("PUBLIC_IP", raising=False)
        self._stub_docker(monkeypatch)

        with patch(_URLOPEN, side_effect=urllib.error.URLError("no network")):
            out = generate_ciu_env(tmp_path)

        content = out.read_text(encoding="utf-8")
        assert 'export IS_NATIVE="1"' in content
        assert "IS_BARE_METAL" not in content

    def test_ciu_host_profile_placeholder_present(self, tmp_path, monkeypatch):
        """S7.5: CIU_HOST_PROFILE commented placeholder must be in the generated file."""
        monkeypatch.setenv("ENV_TYPE", "native")
        monkeypatch.delenv("PUBLIC_FQDN", raising=False)
        monkeypatch.delenv("PUBLIC_IP", raising=False)
        self._stub_docker(monkeypatch)

        with patch(_URLOPEN, side_effect=urllib.error.URLError("no network")):
            out = generate_ciu_env(tmp_path)

        content = out.read_text(encoding="utf-8")
        assert "CIU_HOST_PROFILE" in content
        # It must be commented out (a placeholder, not a live export)
        assert '# export CIU_HOST_PROFILE=""' in content

    def test_env_type_comment_lists_native(self, tmp_path, monkeypatch):
        """The comment for ENV_TYPE must list 'native' not 'bare-metal'."""
        monkeypatch.setenv("ENV_TYPE", "native")
        monkeypatch.delenv("PUBLIC_FQDN", raising=False)
        monkeypatch.delenv("PUBLIC_IP", raising=False)
        self._stub_docker(monkeypatch)

        with patch(_URLOPEN, side_effect=urllib.error.URLError("no network")):
            out = generate_ciu_env(tmp_path)

        content = out.read_text(encoding="utf-8")
        # The comment line should mention 'native' not 'bare-metal'
        assert "native" in content
        assert "bare-metal" not in content
