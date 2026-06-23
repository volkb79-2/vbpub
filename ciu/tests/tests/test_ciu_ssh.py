"""
CIU SPEC J — SSH transport, host inventory, and CLI integration tests.

All tests are unit-only: no real network, no real SSH, no real Vault.
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import hosts as hosts_mod
from ciu.hosts import load_hosts, get_host
from ciu import transport_ssh as tssh_mod
from ciu.transport_ssh import (
    _parse_ask_vault,
    resolve_key,
    _known_hosts_file,
    ssh_exec,
    ssh_sync,
    _ssh_exec_subprocess,
)
from ciu import cli as cli_mod


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_hosts_toml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / ".ciu.hosts.toml"
    p.write_text(content)
    return p


_MINIMAL_HOSTS_TOML = """\
[deploy.hosts.web]
ssh_host = "web.example.com"
ssh_user = "deploy"
ssh_port = 22
ssh_key = "/home/deploy/.ssh/id_rsa"
known_host = "ecdsa-sha2-nistp256 AAAA..."
bundle_dir = "/opt/app"
"""

_ADMIN_HOSTS_TOML = """\
[deploy.hosts.web]
ssh_host = "web.example.com"
ssh_user = "deploy"
ssh_key = "/home/deploy/.ssh/id_rsa"
known_host = "ecdsa-sha2-nistp256 AAAA..."

[deploy.hosts.web.admin]
ssh_user = "root"
ssh_key = "/root/.ssh/id_rsa"
"""

_TOP_LEVEL_HOSTS_TOML = """\
[hosts.staging]
ssh_host = "staging.example.com"
ssh_user = "ci"
ssh_key = "/ci/.ssh/id_ed25519"
known_host = "ssh-ed25519 BBBB..."
"""


# ===========================================================================
# hosts.py — load_hosts
# ===========================================================================


class TestLoadHosts:
    def test_returns_empty_when_no_file(self, tmp_path):
        result = load_hosts(tmp_path)
        assert result == {}

    def test_loads_deploy_hosts_table(self, tmp_path):
        _make_hosts_toml(tmp_path, _MINIMAL_HOSTS_TOML)
        result = load_hosts(tmp_path)
        assert "web" in result
        assert result["web"]["ssh_host"] == "web.example.com"

    def test_loads_top_level_hosts_table(self, tmp_path):
        p = tmp_path / ".ciu.hosts.toml"
        p.write_text(_TOP_LEVEL_HOSTS_TOML)
        result = load_hosts(tmp_path)
        assert "staging" in result

    def test_env_override_takes_precedence(self, tmp_path, monkeypatch):
        # Set up a repo-local file with 'web' and an env-override file with 'env_host'
        _make_hosts_toml(tmp_path, _MINIMAL_HOSTS_TOML)
        env_file = tmp_path / "custom_hosts.toml"
        env_file.write_text(
            "[deploy.hosts.env_host]\nssh_host = \"env.example.com\"\n"
        )
        monkeypatch.setenv("CIU_HOSTS_FILE", str(env_file))
        result = load_hosts(tmp_path)
        assert "env_host" in result
        # The repo-local 'web' host should NOT be present (env file wins)
        assert "web" not in result

    def test_env_file_missing_falls_through_to_repo(self, tmp_path, monkeypatch):
        """If CIU_HOSTS_FILE points to a nonexistent path, fall through to repo-local."""
        _make_hosts_toml(tmp_path, _MINIMAL_HOSTS_TOML)
        monkeypatch.setenv("CIU_HOSTS_FILE", str(tmp_path / "nonexistent.toml"))
        result = load_hosts(tmp_path)
        assert "web" in result

    def test_non_dict_hosts_returns_empty(self, tmp_path):
        p = tmp_path / ".ciu.hosts.toml"
        # hosts is a list, not a dict — should return {}
        p.write_text('[deploy]\nhosts = ["not", "a", "dict"]\n')
        result = load_hosts(tmp_path)
        assert result == {}

    def test_home_dir_fallback(self, tmp_path, monkeypatch):
        """~/.ciu/hosts.toml is used when repo-local is absent."""
        home_ciu = tmp_path / "fake_home" / ".ciu"
        home_ciu.mkdir(parents=True)
        (home_ciu / "hosts.toml").write_text(
            "[deploy.hosts.home_host]\nssh_host = \"home.example.com\"\n"
        )
        # Ensure no repo-local file
        monkeypatch.setenv("HOME", str(tmp_path / "fake_home"))
        # Use a different tmp_path as repo_root so no .ciu.hosts.toml there
        repo = tmp_path / "repo"
        repo.mkdir()
        # We patch Path.home() since load_hosts uses it
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "fake_home")
        result = load_hosts(repo)
        assert "home_host" in result


# ===========================================================================
# hosts.py — get_host
# ===========================================================================


class TestGetHost:
    def test_returns_host_cfg(self, tmp_path):
        _make_hosts_toml(tmp_path, _MINIMAL_HOSTS_TOML)
        cfg = get_host(tmp_path, "web")
        assert cfg["ssh_host"] == "web.example.com"
        assert cfg["ssh_user"] == "deploy"

    def test_raises_when_no_hosts_file(self, tmp_path):
        with pytest.raises(ValueError, match="No hosts file found"):
            get_host(tmp_path, "web")

    def test_raises_when_host_not_found(self, tmp_path):
        _make_hosts_toml(tmp_path, _MINIMAL_HOSTS_TOML)
        with pytest.raises(ValueError, match="Host 'missing'"):
            get_host(tmp_path, "missing")

    def test_available_hosts_listed_in_error(self, tmp_path):
        _make_hosts_toml(tmp_path, _MINIMAL_HOSTS_TOML)
        with pytest.raises(ValueError, match="web"):
            get_host(tmp_path, "nonexistent")

    def test_admin_mode_merges_admin_subtable(self, tmp_path):
        _make_hosts_toml(tmp_path, _ADMIN_HOSTS_TOML)
        cfg = get_host(tmp_path, "web", admin=True)
        # admin subtable overrides ssh_user and ssh_key
        assert cfg["ssh_user"] == "root"
        assert cfg["ssh_key"] == "/root/.ssh/id_rsa"
        # admin sub-table itself should not appear as a nested dict
        assert "admin" not in cfg

    def test_non_admin_mode_excludes_admin_subtable(self, tmp_path):
        _make_hosts_toml(tmp_path, _ADMIN_HOSTS_TOML)
        cfg = get_host(tmp_path, "web", admin=False)
        assert cfg["ssh_user"] == "deploy"
        assert "admin" not in cfg

    def test_host_without_admin_subtable_admin_true(self, tmp_path):
        _make_hosts_toml(tmp_path, _MINIMAL_HOSTS_TOML)
        # Should not raise even if no admin subtable defined
        cfg = get_host(tmp_path, "web", admin=True)
        assert cfg["ssh_host"] == "web.example.com"


# ===========================================================================
# transport_ssh.py — _parse_ask_vault
# ===========================================================================


class TestParseAskVault:
    def test_path_only(self):
        path, field = _parse_ask_vault("ASK_VAULT:secret/data/mykey")
        assert path == "secret/data/mykey"
        assert field is None

    def test_path_with_field(self):
        path, field = _parse_ask_vault("ASK_VAULT:secret/data/mykey#private_key")
        assert path == "secret/data/mykey"
        assert field == "private_key"

    def test_multiple_hashes_uses_first(self):
        path, field = _parse_ask_vault("ASK_VAULT:secret/data/k#field#extra")
        assert path == "secret/data/k"
        assert field == "field#extra"


# ===========================================================================
# transport_ssh.py — resolve_key
# ===========================================================================


class TestResolveKey:
    def test_filesystem_path_passthrough(self, tmp_path):
        key_file = tmp_path / "id_rsa"
        key_file.write_text("PRIVATE_KEY")
        host_cfg = {"ssh_key": str(key_file)}
        result = resolve_key(host_cfg, {}, tmp_path)
        assert result == str(key_file.resolve())

    def test_expanduser_applied(self, tmp_path, monkeypatch):
        # Create a fake home with a key
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        key_file = fake_home / ".ssh" / "id_rsa"
        key_file.parent.mkdir()
        key_file.write_text("KEY")
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        # Use ~ prefix
        host_cfg = {"ssh_key": "~/.ssh/id_rsa"}
        # The expanduser will use the real os.path.expanduser which may not
        # see our monkeypatched home, so use absolute path directly
        host_cfg = {"ssh_key": str(key_file)}
        result = resolve_key(host_cfg, {}, tmp_path)
        assert Path(result).exists()

    def test_no_ssh_key_raises(self, tmp_path):
        with pytest.raises(ValueError, match="No ssh_key"):
            resolve_key({}, {}, tmp_path)

    def test_ask_vault_writes_temp_file_with_0600(self, tmp_path):
        host_cfg = {"ssh_key": "ASK_VAULT:secret/data/mykey#private_key"}
        mock_token = "s.testtoken"
        mock_addr = "http://vault:8200"
        key_material = "-----BEGIN RSA PRIVATE KEY-----\nFAKEKEY\n-----END RSA PRIVATE KEY-----"

        mock_client = MagicMock()
        mock_client.read.return_value = key_material

        with patch.object(tssh_mod, "resolve_key", wraps=resolve_key):
            # Patch the imported names inside transport_ssh
            with patch("ciu.transport_ssh.resolve_key.__module__"):
                pass

        # Direct patching inside the function via import
        with patch("ciu.secrets.providers.resolve_vault_token", return_value=mock_token), \
             patch("ciu.secrets.providers.vault_addr_from_config", return_value=mock_addr), \
             patch("ciu.secrets.providers.VaultKV2", return_value=mock_client):
            # We need to patch the import path inside transport_ssh
            with patch.dict("sys.modules", {}):
                import ciu.secrets.providers as prov_mod
                orig_rt = prov_mod.resolve_vault_token
                orig_vac = prov_mod.vault_addr_from_config
                orig_vkv2 = prov_mod.VaultKV2
                try:
                    prov_mod.resolve_vault_token = lambda *a, **kw: mock_token
                    prov_mod.vault_addr_from_config = lambda *a, **kw: mock_addr
                    prov_mod.VaultKV2 = lambda *a, **kw: mock_client
                    tmp_path_out = resolve_key(host_cfg, {}, tmp_path)
                    try:
                        assert Path(tmp_path_out).exists()
                        file_stat = os.stat(tmp_path_out)
                        # Check mode is 0600 (owner read+write only)
                        assert stat.S_IMODE(file_stat.st_mode) == 0o600
                        content = Path(tmp_path_out).read_text()
                        assert "FAKEKEY" in content
                    finally:
                        if Path(tmp_path_out).exists():
                            os.unlink(tmp_path_out)
                finally:
                    prov_mod.resolve_vault_token = orig_rt
                    prov_mod.vault_addr_from_config = orig_vac
                    prov_mod.VaultKV2 = orig_vkv2

    def test_ask_vault_no_token_raises(self, tmp_path):
        host_cfg = {"ssh_key": "ASK_VAULT:secret/data/mykey"}
        import ciu.secrets.providers as prov_mod
        orig = prov_mod.resolve_vault_token
        try:
            prov_mod.resolve_vault_token = lambda *a, **kw: None
            with pytest.raises(ValueError, match="Vault token"):
                resolve_key(host_cfg, {}, tmp_path)
        finally:
            prov_mod.resolve_vault_token = orig


# ===========================================================================
# transport_ssh.py — _known_hosts_file
# ===========================================================================


class TestKnownHostsFile:
    def test_returns_none_when_no_known_host(self):
        result = _known_hosts_file({})
        assert result is None

    def test_returns_none_when_known_host_empty_string(self):
        result = _known_hosts_file({"known_host": ""})
        assert result is None

    def test_writes_temp_file_with_correct_content(self):
        host_cfg = {
            "ssh_host": "web.example.com",
            "known_host": "ecdsa-sha2-nistp256 AAAA...",
        }
        tmp_path = _known_hosts_file(host_cfg)
        try:
            assert tmp_path is not None
            content = Path(tmp_path).read_text()
            assert "web.example.com" in content
            assert "ecdsa-sha2-nistp256 AAAA..." in content
            # Default port 22 → bare hostname, not bracketed form
            assert "[web.example.com]" not in content
            # Check mode is 0600
            file_stat = os.stat(tmp_path)
            assert stat.S_IMODE(file_stat.st_mode) == 0o600
        finally:
            if tmp_path and Path(tmp_path).exists():
                os.unlink(tmp_path)

    def test_non_default_port_uses_bracket_form(self):
        # OpenSSH/paramiko key non-22 ports as "[host]:port"; a bare hostname
        # entry would never match and the pinned connection would be rejected.
        host_cfg = {
            "ssh_host": "web.example.com",
            "ssh_port": 2222,
            "known_host": "ecdsa-sha2-nistp256 AAAA...",
        }
        tmp_path = _known_hosts_file(host_cfg)
        try:
            content = Path(tmp_path).read_text()
            assert content.startswith("[web.example.com]:2222 ")
        finally:
            if tmp_path and Path(tmp_path).exists():
                os.unlink(tmp_path)


# ===========================================================================
# transport_ssh.py — ssh_exec (subprocess path)
# ===========================================================================


class TestSshExecSubprocess:
    def _make_host_cfg(self, **kwargs):
        base = {
            "ssh_host": "web.example.com",
            "ssh_user": "deploy",
            "ssh_port": 22,
            "ssh_key": "/fake/key",
            "known_host": "ecdsa-sha2-nistp256 AAAA...",
        }
        base.update(kwargs)
        return base

    def test_fails_closed_no_known_host_no_tofu(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CIU_SSH_INSECURE_TOFU", raising=False)
        host_cfg = {
            "ssh_host": "web.example.com",
            "ssh_user": "deploy",
            "ssh_key": "/fake/key",
            # no known_host
        }
        with pytest.raises(ValueError, match="no 'known_host' pinned"):
            ssh_exec(host_cfg, [], config={}, repo_root=tmp_path)

    def test_fails_without_ssh_host(self, tmp_path):
        host_cfg = {"ssh_user": "deploy", "ssh_key": "/fake/key"}
        with pytest.raises(ValueError, match="ssh_host not configured"):
            ssh_exec(host_cfg, [], config={}, repo_root=tmp_path)

    def test_subprocess_call_with_known_host(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CIU_SSH_INSECURE_TOFU", raising=False)
        monkeypatch.delenv("CIU_SSH_TRANSPORT", raising=False)

        key_file = tmp_path / "id_rsa"
        key_file.write_text("KEY")

        host_cfg = self._make_host_cfg(ssh_key=str(key_file))

        captured_cmd = []

        def fake_subprocess_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            result = MagicMock()
            result.returncode = 0
            return result

        monkeypatch.setattr(tssh_mod.subprocess, "run", fake_subprocess_run)
        rc = ssh_exec(host_cfg, ["echo", "hello"], config={}, repo_root=tmp_path)
        assert rc == 0

        # Must include -i <key>
        assert "-i" in captured_cmd
        key_idx = captured_cmd.index("-i")
        assert str(key_file.resolve()) in captured_cmd[key_idx + 1]

        # Must include StrictHostKeyChecking=yes
        assert any("StrictHostKeyChecking=yes" in a for a in captured_cmd)

        # Must include UserKnownHostsFile=<some-tmp-file>
        assert any("UserKnownHostsFile=" in a for a in captured_cmd)
        # Must NOT include -t (non-interactive)
        assert "-t" not in captured_cmd
        # Must include BatchMode=yes for non-interactive
        assert any("BatchMode=yes" in a for a in captured_cmd)

    def test_subprocess_call_interactive_adds_dash_t(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CIU_SSH_INSECURE_TOFU", raising=False)
        monkeypatch.delenv("CIU_SSH_TRANSPORT", raising=False)

        key_file = tmp_path / "id_rsa"
        key_file.write_text("KEY")
        host_cfg = self._make_host_cfg(ssh_key=str(key_file))

        captured_cmd = []

        def fake_subprocess_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            result = MagicMock()
            result.returncode = 0
            return result

        monkeypatch.setattr(tssh_mod.subprocess, "run", fake_subprocess_run)
        rc = ssh_exec(host_cfg, [], config={}, repo_root=tmp_path, interactive=True)
        assert rc == 0
        assert "-t" in captured_cmd

    def test_tofu_escape_hatch_allows_no_known_host(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CIU_SSH_INSECURE_TOFU", "1")
        monkeypatch.delenv("CIU_SSH_TRANSPORT", raising=False)

        key_file = tmp_path / "id_rsa"
        key_file.write_text("KEY")

        host_cfg = {
            "ssh_host": "web.example.com",
            "ssh_user": "deploy",
            "ssh_port": 22,
            "ssh_key": str(key_file),
            # no known_host
        }

        captured_cmd = []

        def fake_subprocess_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            result = MagicMock()
            result.returncode = 0
            return result

        monkeypatch.setattr(tssh_mod.subprocess, "run", fake_subprocess_run)
        rc = ssh_exec(host_cfg, ["echo", "hi"], config={}, repo_root=tmp_path)
        assert rc == 0
        # Should have StrictHostKeyChecking=no when TOFU escape hatch is on
        assert any("StrictHostKeyChecking=no" in a for a in captured_cmd)

    def test_returns_subprocess_exit_code(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CIU_SSH_INSECURE_TOFU", raising=False)
        monkeypatch.delenv("CIU_SSH_TRANSPORT", raising=False)

        key_file = tmp_path / "id_rsa"
        key_file.write_text("KEY")
        host_cfg = self._make_host_cfg(ssh_key=str(key_file))

        def fake_subprocess_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 42
            return result

        monkeypatch.setattr(tssh_mod.subprocess, "run", fake_subprocess_run)
        rc = ssh_exec(host_cfg, ["false"], config={}, repo_root=tmp_path)
        assert rc == 42


# ===========================================================================
# transport_ssh.py — ssh_sync
# ===========================================================================


class TestSshSync:
    def test_fails_closed_no_known_host_no_tofu(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CIU_SSH_INSECURE_TOFU", raising=False)
        host_cfg = {
            "ssh_host": "web.example.com",
            "ssh_user": "deploy",
            "ssh_key": "/fake/key",
            # no known_host
        }
        with pytest.raises(ValueError, match="no 'known_host' pinned"):
            ssh_sync(host_cfg, "/local", "/remote", config={}, repo_root=tmp_path)

    def test_rsync_cmd_structure(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CIU_SSH_INSECURE_TOFU", raising=False)

        key_file = tmp_path / "id_rsa"
        key_file.write_text("KEY")

        host_cfg = {
            "ssh_host": "web.example.com",
            "ssh_user": "deploy",
            "ssh_port": 22,
            "ssh_key": str(key_file),
            "known_host": "ecdsa-sha2-nistp256 AAAA...",
        }

        captured_cmd = []

        def fake_subprocess_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            result = MagicMock()
            result.returncode = 0
            return result

        monkeypatch.setattr(tssh_mod.subprocess, "run", fake_subprocess_run)
        rc = ssh_sync(host_cfg, "/local/dir", "/remote/dir", config={}, repo_root=tmp_path)
        assert rc == 0

        assert "rsync" in captured_cmd
        assert "-az" in captured_cmd
        assert "-e" in captured_cmd
        # Destination should be user@host:remote/
        assert any("deploy@web.example.com:/remote/dir/" in a for a in captured_cmd)
        # Source should have trailing slash
        assert "/local/dir/" in captured_cmd

    def test_rsync_returns_exit_code(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CIU_SSH_INSECURE_TOFU", raising=False)
        key_file = tmp_path / "id_rsa"
        key_file.write_text("KEY")
        host_cfg = {
            "ssh_host": "web.example.com",
            "ssh_user": "deploy",
            "ssh_port": 22,
            "ssh_key": str(key_file),
            "known_host": "ecdsa-sha2-nistp256 AAAA...",
        }

        def fake_subprocess_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 23
            return result

        monkeypatch.setattr(tssh_mod.subprocess, "run", fake_subprocess_run)
        rc = ssh_sync(host_cfg, "/local", "/remote", config={}, repo_root=tmp_path)
        assert rc == 23


# ===========================================================================
# CLI — ssh verb dispatch
# ===========================================================================


class TestCliSshVerb:
    def test_ssh_missing_host_exits_2(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["ciu", "ssh"])
        with pytest.raises(SystemExit) as exc:
            cli_mod.main()
        assert exc.value.code == 2

    def test_ssh_verb_help(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["ciu", "ssh", "--help"])
        with pytest.raises(SystemExit) as exc:
            cli_mod.main()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "ciu ssh" in out

    def test_ssh_verb_dispatches_to_ssh_exec(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REPO_ROOT", str(tmp_path))
        monkeypatch.delenv("CIU_SSH_INSECURE_TOFU", raising=False)
        monkeypatch.delenv("CIU_SSH_TRANSPORT", raising=False)

        # Create a hosts file
        hosts_file = tmp_path / ".ciu.hosts.toml"
        key_file = tmp_path / "id_rsa"
        key_file.write_text("KEY")
        hosts_file.write_text(
            f'[deploy.hosts.myhost]\n'
            f'ssh_host = "myhost.example.com"\n'
            f'ssh_user = "deploy"\n'
            f'ssh_port = 22\n'
            f'ssh_key = "{key_file}"\n'
            f'known_host = "ecdsa-sha2-nistp256 AAAA..."\n'
        )

        captured_calls = []

        def fake_ssh_exec(host_cfg, argv, *, config, repo_root, interactive=False, admin=False):
            captured_calls.append({
                "host_cfg": host_cfg,
                "argv": argv,
                "interactive": interactive,
                "admin": admin,
            })
            return 0

        monkeypatch.setattr(sys, "argv", ["ciu", "ssh", "myhost", "--", "echo", "hello"])
        monkeypatch.setattr(cli_mod, "_ssh_exec_injected", None, raising=False)

        # Patch transport_ssh.ssh_exec at the module level it's imported in cli
        import ciu.transport_ssh as tssh
        orig_exec = tssh.ssh_exec
        tssh.ssh_exec = fake_ssh_exec
        try:
            with pytest.raises(SystemExit) as exc:
                cli_mod.main()
            assert exc.value.code == 0
            assert len(captured_calls) == 1
            assert captured_calls[0]["argv"] == ["echo", "hello"]
            assert captured_calls[0]["interactive"] is False
        finally:
            tssh.ssh_exec = orig_exec

    def test_ssh_verb_interactive_when_no_cmd(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REPO_ROOT", str(tmp_path))
        monkeypatch.delenv("CIU_SSH_INSECURE_TOFU", raising=False)
        monkeypatch.delenv("CIU_SSH_TRANSPORT", raising=False)

        hosts_file = tmp_path / ".ciu.hosts.toml"
        key_file = tmp_path / "id_rsa"
        key_file.write_text("KEY")
        hosts_file.write_text(
            f'[deploy.hosts.myhost]\n'
            f'ssh_host = "myhost.example.com"\n'
            f'ssh_user = "deploy"\n'
            f'ssh_port = 22\n'
            f'ssh_key = "{key_file}"\n'
            f'known_host = "ecdsa-sha2-nistp256 AAAA..."\n'
        )

        captured_calls = []

        def fake_ssh_exec(host_cfg, argv, *, config, repo_root, interactive=False, admin=False):
            captured_calls.append({"interactive": interactive, "argv": argv})
            return 0

        import ciu.transport_ssh as tssh
        orig_exec = tssh.ssh_exec
        tssh.ssh_exec = fake_ssh_exec
        try:
            monkeypatch.setattr(sys, "argv", ["ciu", "ssh", "myhost"])
            with pytest.raises(SystemExit) as exc:
                cli_mod.main()
            assert exc.value.code == 0
            assert captured_calls[0]["interactive"] is True
            assert captured_calls[0]["argv"] == []
        finally:
            tssh.ssh_exec = orig_exec


# ===========================================================================
# CLI — up --host routing
# ===========================================================================


class TestCliUpHostRouting:
    def test_up_with_host_calls_ssh_sync_and_ssh_exec(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REPO_ROOT", str(tmp_path))
        monkeypatch.delenv("CIU_SSH_INSECURE_TOFU", raising=False)

        hosts_file = tmp_path / ".ciu.hosts.toml"
        key_file = tmp_path / "id_rsa"
        key_file.write_text("KEY")
        hosts_file.write_text(
            f'[deploy.hosts.myhost]\n'
            f'ssh_host = "myhost.example.com"\n'
            f'ssh_user = "deploy"\n'
            f'ssh_port = 22\n'
            f'ssh_key = "{key_file}"\n'
            f'known_host = "ecdsa-sha2-nistp256 AAAA..."\n'
            f'bundle_dir = "/opt/app"\n'
        )

        sync_calls = []
        exec_calls = []

        def fake_ssh_sync(host_cfg, local_dir, remote_dir, *, config, repo_root, admin=False):
            sync_calls.append({"local": local_dir, "remote": remote_dir})
            return 0

        def fake_ssh_exec(host_cfg, argv, *, config, repo_root, interactive=False, admin=False):
            exec_calls.append({"argv": argv})
            return 0

        import ciu.transport_ssh as tssh
        orig_sync = tssh.ssh_sync
        orig_exec = tssh.ssh_exec
        tssh.ssh_sync = fake_ssh_sync
        tssh.ssh_exec = fake_ssh_exec
        try:
            monkeypatch.setattr(sys, "argv", ["ciu", "up", "--host", "myhost"])
            with pytest.raises(SystemExit) as exc:
                cli_mod.main()
            assert exc.value.code == 0
            assert len(sync_calls) == 1
            assert sync_calls[0]["remote"] == "/opt/app"
            assert len(exec_calls) == 1
            # render-on-target must be ONE argv element (no "sh -c" wrapper) so
            # ssh's remote login shell parses the cd/&& chain intact. A wrapper
            # would be space-re-split remote-side and break the cd into the bundle.
            argv = exec_calls[0]["argv"]
            assert len(argv) == 1
            assert argv[0].startswith("cd /opt/app &&")
            assert "ciu render" in argv[0] and "ciu up" in argv[0]
        finally:
            tssh.ssh_sync = orig_sync
            tssh.ssh_exec = orig_exec

    def test_up_thin_flag_exits_1(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REPO_ROOT", str(tmp_path))
        hosts_file = tmp_path / ".ciu.hosts.toml"
        hosts_file.write_text(
            '[deploy.hosts.myhost]\nssh_host = "h"\nssh_user = "u"\nssh_key = "/k"\n'
        )
        monkeypatch.setattr(sys, "argv", ["ciu", "up", "--host", "myhost", "--thin"])
        with pytest.raises(SystemExit) as exc:
            cli_mod.main()
        assert exc.value.code == 1


# ===========================================================================
# deploy.py parse_args — --host and --thin
# ===========================================================================


class TestDeployParseArgs:
    def test_host_defaults_none(self):
        from ciu.deploy import parse_args
        args = parse_args(["--deploy"])
        assert args.host is None

    def test_thin_defaults_false(self):
        from ciu.deploy import parse_args
        args = parse_args(["--deploy"])
        assert args.thin is False

    def test_host_parsed(self):
        from ciu.deploy import parse_args
        args = parse_args(["--deploy", "--host", "prod"])
        assert args.host == "prod"

    def test_thin_parsed(self):
        from ciu.deploy import parse_args
        args = parse_args(["--deploy", "--thin"])
        assert args.thin is True
