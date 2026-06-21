"""Tests for the cmru installer v2 — spec SPEC A (spec-cmru-installer-v2.md).

Covers:
  - Schema: [installer] accepted; [getsh] rejected (exit 2); missing/unknown keys.
  - Generator: render_get_py output reproducible; no unreplaced [[...]] placeholders.
  - Auth: public request carries no Authorization; token-file security rules.
  - Verify: SHA256 mismatch aborts before extraction; safe-member rejection rules.
  - Extraction: path traversal, absolute paths, symlink escapes, device nodes rejected.
  - Transaction: install/update/rollback round-trip; interrupted update leaves current live.
  - Adapter: stub invoked with correct argv; non-zero adapter exit aborts before swap.
  - Scope: system vs user scope produce correct paths.

Stdlib + tmp files only — no network, no git side effects.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import textwrap
from pathlib import Path
from typing import List, Optional, Tuple
from unittest import mock

import pytest

# ─── helpers ─────────────────────────────────────────────────────────────────

MINIMAL_GITHUB = """
[github]
owner = "octocat"
repo = "demo"
owner_type = "user"

[targets]
host = "github"
registry = []
"""


def _minimal_toml(extra_project: str = "") -> str:
    return (
        MINIMAL_GITHUB
        + """
[project.demo]
prefix    = "demo-v"
artifacts = ["tarball"]
cwd       = "demo"
[project.demo.version]
strategy = "file:VERSION"
"""
        + extra_project
    )


def _write(tmp_path: Path, body: str, name: str = "cmru.toml") -> Path:
    p = tmp_path / name
    p.write_text(body)
    return p


# ─── Schema tests ─────────────────────────────────────────────────────────────

class TestInstallerSchema:
    def test_valid_installer_section_accepted(self, tmp_path):
        toml = _minimal_toml("""
[project.demo.installer]
install_dir_system = "/opt/demo"
install_dir_user   = "demo"
asset_suffix       = ".tar.xz"
required_commands  = ["python3", "docker", "minisign"]
preserve           = ["shared/host.toml"]
manifest_name      = "manifest.json"
signature_name     = "manifest.json.minisig"

[[project.demo.installer.wheels]]
path         = "vendor/cmru-*.whl"
distribution = "cmru"
""")
        cfg_path = _write(tmp_path, toml)
        from cmru.config import load_forge_config
        config = load_forge_config(cfg_path)
        ins = config.projects["demo"].installer
        assert ins is not None
        assert ins.install_dir_system == "/opt/demo"
        assert ins.install_dir_user == "demo"
        assert ins.asset_suffix == ".tar.xz"
        assert ins.required_commands == ["python3", "docker", "minisign"]
        assert ins.preserve == ["shared/host.toml"]
        assert ins.manifest_name == "manifest.json"
        assert ins.signature_name == "manifest.json.minisig"
        assert len(ins.wheels) == 1
        assert ins.wheels[0].path == "vendor/cmru-*.whl"
        assert ins.wheels[0].distribution == "cmru"

    def test_installer_no_wheels_no_entrypoint(self, tmp_path):
        """tls-edge minimal path: no wheels, no entrypoint."""
        toml = _minimal_toml("""
[project.demo.installer]
install_dir_system = "/opt/demo"
install_dir_user   = "demo"
""")
        cfg_path = _write(tmp_path, toml)
        from cmru.config import load_forge_config
        config = load_forge_config(cfg_path)
        ins = config.projects["demo"].installer
        assert ins.wheels == []
        assert ins.entrypoint is None

    def test_getsh_key_rejected_exit_2(self, tmp_path):
        """V09: surviving [getsh] key is exit 2."""
        toml = _minimal_toml("""
[project.demo.getsh]
install_dir = "/opt/demo-src"
preserve    = []
""")
        cfg_path = _write(tmp_path, toml)
        from cmru.config import load_forge_config
        with pytest.raises(SystemExit) as exc:
            load_forge_config(cfg_path)
        assert exc.value.code == 2

    def test_missing_required_field_install_dir_system(self, tmp_path):
        toml = _minimal_toml("""
[project.demo.installer]
install_dir_user = "demo"
""")
        cfg_path = _write(tmp_path, toml)
        from cmru.config import load_forge_config
        with pytest.raises(SystemExit) as exc:
            load_forge_config(cfg_path)
        assert exc.value.code == 2

    def test_missing_required_field_install_dir_user(self, tmp_path):
        toml = _minimal_toml("""
[project.demo.installer]
install_dir_system = "/opt/demo"
""")
        cfg_path = _write(tmp_path, toml)
        from cmru.config import load_forge_config
        with pytest.raises(SystemExit) as exc:
            load_forge_config(cfg_path)
        assert exc.value.code == 2

    def test_unknown_installer_key_rejected(self, tmp_path):
        """V09: unknown key in [installer] is exit 2."""
        toml = _minimal_toml("""
[project.demo.installer]
install_dir_system = "/opt/demo"
install_dir_user   = "demo"
bogus_key          = "should-fail"
""")
        cfg_path = _write(tmp_path, toml)
        from cmru.config import load_forge_config
        with pytest.raises(SystemExit) as exc:
            load_forge_config(cfg_path)
        assert exc.value.code == 2

    def test_unknown_wheel_subkey_rejected(self, tmp_path):
        toml = _minimal_toml("""
[project.demo.installer]
install_dir_system = "/opt/demo"
install_dir_user   = "demo"

[[project.demo.installer.wheels]]
path         = "vendor/cmru-*.whl"
distribution = "cmru"
extra_field  = "bad"
""")
        cfg_path = _write(tmp_path, toml)
        from cmru.config import load_forge_config
        with pytest.raises(SystemExit) as exc:
            load_forge_config(cfg_path)
        assert exc.value.code == 2

    def test_two_wheels(self, tmp_path):
        toml = _minimal_toml("""
[project.demo.installer]
install_dir_system = "/opt/demo"
install_dir_user   = "demo"

[[project.demo.installer.wheels]]
path         = "vendor/cmru-*.whl"
distribution = "cmru"

[[project.demo.installer.wheels]]
path         = "vendor/ciu-*.whl"
distribution = "ciu"
""")
        cfg_path = _write(tmp_path, toml)
        from cmru.config import load_forge_config
        config = load_forge_config(cfg_path)
        ins = config.projects["demo"].installer
        assert len(ins.wheels) == 2
        assert ins.wheels[1].distribution == "ciu"


# ─── Generator tests ─────────────────────────────────────────────────────────

class TestGenerator:
    def _render(self, **kw) -> str:
        from cmru.getpy import render_get_py
        defaults = dict(
            project_name="demo",
            repo_owner="octocat",
            repo_name="my-repo",
            tag_prefix="demo-v",
            install_dir_system="/opt/demo",
            install_dir_user="demo",
        )
        defaults.update(kw)
        return render_get_py(**defaults)

    def test_no_unreplaced_placeholders(self):
        import re
        output = self._render()
        remaining = re.findall(r"\[\[[A-Z_]+\]\]", output)
        assert remaining == [], f"Unreplaced placeholders: {remaining}"

    def test_output_reproducible(self):
        out1 = self._render()
        out2 = self._render()
        assert out1 == out2

    def test_project_name_in_output(self):
        out = self._render(project_name="my-proj")
        assert "my-proj" in out

    def test_required_commands_rendered(self):
        out = self._render(required_commands=["python3", "docker", "minisign"])
        assert '"python3"' in out
        assert '"docker"' in out
        assert '"minisign"' in out

    def test_empty_required_commands(self):
        out = self._render(required_commands=[])
        assert "REQUIRED_COMMANDS: List[str] = []" in out

    def test_preserve_paths_rendered(self):
        out = self._render(preserve_paths=["shared/host.toml", "shared/ciu.env"])
        assert '"shared/host.toml"' in out
        assert '"shared/ciu.env"' in out

    def test_empty_preserve_paths(self):
        out = self._render(preserve_paths=[])
        # template has extra spaces in alignment; just check the list is empty
        assert "PRESERVE_PATHS" in out and "= []" in out

    def test_wheel_specs_rendered(self):
        out = self._render(wheel_specs=[("vendor/cmru-*.whl", "cmru"), ("vendor/ciu-*.whl", "ciu")])
        assert '"vendor/cmru-*.whl"' in out
        assert '"vendor/ciu-*.whl"' in out
        assert '"cmru"' in out
        assert '"ciu"' in out

    def test_empty_wheel_specs(self):
        out = self._render(wheel_specs=[])
        assert "WHEEL_SPECS: List[Tuple[str, str]] = []" in out

    def test_entrypoint_in_output(self):
        out = self._render(entrypoint="scripts/bootstrap.py")
        assert '"scripts/bootstrap.py"' in out or "scripts/bootstrap.py" in out

    def test_empty_entrypoint_no_adapter_call(self):
        out = self._render(entrypoint="")
        # when ENTRYPOINT is "" the template must degrade: no adapter invocation
        # The code checks `if not ENTRYPOINT: return`
        assert 'ENTRYPOINT       = ""' in out

    def test_render_from_config(self, tmp_path):
        toml = _minimal_toml("""
[project.demo.installer]
install_dir_system = "/opt/demo"
install_dir_user   = "demo"
required_commands  = ["python3", "docker"]
preserve           = ["shared/host.toml"]
manifest_name      = "manifest.json"
signature_name     = "manifest.json.minisig"
""")
        cfg_path = _write(tmp_path, toml)
        from cmru.getpy import render_from_config
        import re
        out = render_from_config("demo", cfg_path)
        remaining = re.findall(r"\[\[[A-Z_]+\]\]", out)
        assert remaining == [], f"Unreplaced placeholders: {remaining}"
        assert '"python3"' in out
        assert '"docker"' in out

    def test_render_from_config_no_installer_raises(self, tmp_path):
        toml = _minimal_toml()
        cfg_path = _write(tmp_path, toml)
        from cmru.getpy import render_from_config
        with pytest.raises(ValueError, match="installer"):
            render_from_config("demo", cfg_path)

    def test_scope_dirs_in_output(self):
        out = self._render(install_dir_system="/opt/myapp", install_dir_user="myapp")
        assert '"/opt/myapp"' in out
        assert '"myapp"' in out

    def test_manifest_names_in_output(self):
        out = self._render(manifest_name="m.json", signature_name="m.json.minisig")
        assert '"m.json"' in out
        assert '"m.json.minisig"' in out


# ─── Auth / transport tests ────────────────────────────────────────────────────

class TestAuth:
    """Tests for rendered get.py auth + transport logic (executed in-process)."""

    def _get_check_url(self):
        """Import _check_url from a rendered get.py (or stub equivalent)."""
        # Execute the rendered script in a sandbox to extract _check_url
        from cmru.getpy import render_get_py
        src = render_get_py(
            project_name="demo", repo_owner="o", repo_name="r",
            tag_prefix="demo-v", install_dir_system="/opt/demo",
            install_dir_user="demo",
        )
        ns: dict = {}
        exec(compile(src, "<rendered-get.py>", "exec"), ns)
        return ns["_check_url"]

    def test_check_url_allows_github(self):
        _check_url = self._get_check_url()
        # should not raise
        _check_url("https://github.com/owner/repo/releases/download/v1/file.tar.xz")
        _check_url("https://api.github.com/repos/owner/repo/releases")
        _check_url("https://objects.githubusercontent.com/some-path")

    def test_check_url_rejects_http(self):
        _check_url = self._get_check_url()
        with pytest.raises(SystemExit) as exc:
            _check_url("http://github.com/owner/repo/releases/download/v1/file.tar.xz")
        assert exc.value.code == 1

    def test_check_url_rejects_unknown_host(self):
        _check_url = self._get_check_url()
        with pytest.raises(SystemExit) as exc:
            _check_url("https://evil.example.com/malware.tar.xz")
        assert exc.value.code == 1

    def test_public_request_no_auth_header(self):
        """Public request (no token) must carry no Authorization header."""
        from cmru.getpy import render_get_py
        src = render_get_py(
            project_name="demo", repo_owner="o", repo_name="r",
            tag_prefix="demo-v", install_dir_system="/opt/demo",
            install_dir_user="demo",
        )
        ns: dict = {}
        exec(compile(src, "<rendered-get.py>", "exec"), ns)
        _gh_request = ns["_gh_request"]

        captured_headers: list = []

        def fake_opener_open(req, timeout=30):
            captured_headers.append(dict(req.headers))
            raise ns["urllib"].error.URLError("no network in test")

        fake_opener = mock.MagicMock()
        fake_opener.open.side_effect = fake_opener_open

        with mock.patch.object(ns["urllib"].request, "build_opener", return_value=fake_opener):
            try:
                _gh_request("https://api.github.com/repos/o/r/releases", token=None)
            except SystemExit:
                pass

        # No Authorization header when token is None
        for hdrs in captured_headers:
            # Header dict keys from urllib.request.Request are title-cased
            auth = hdrs.get("Authorization") or hdrs.get("authorization")
            assert auth is None, f"Authorization header sent without token: {auth}"

    def test_token_present_sends_bearer(self):
        """When a token is present, Authorization: Bearer <token> is sent."""
        from cmru.getpy import render_get_py
        src = render_get_py(
            project_name="demo", repo_owner="o", repo_name="r",
            tag_prefix="demo-v", install_dir_system="/opt/demo",
            install_dir_user="demo",
        )
        ns: dict = {}
        exec(compile(src, "<rendered-get.py>", "exec"), ns)
        _gh_request = ns["_gh_request"]

        captured_headers: list = []

        def fake_opener_open(req, timeout=30):
            captured_headers.append(dict(req.headers))
            raise ns["urllib"].error.URLError("no network in test")

        fake_opener = mock.MagicMock()
        fake_opener.open.side_effect = fake_opener_open

        with mock.patch.object(ns["urllib"].request, "build_opener", return_value=fake_opener):
            try:
                _gh_request("https://api.github.com/repos/o/r/releases", token="mytoken")
            except SystemExit:
                pass

        assert any(
            (hdrs.get("Authorization") or hdrs.get("authorization", "")).startswith("Bearer ")
            for hdrs in captured_headers
        ), "Expected Bearer token in Authorization header"

    def test_token_not_in_child_env(self, tmp_path):
        """GitHub token must be stripped from child-process environment (S5)."""
        from cmru.getpy import render_get_py
        src = render_get_py(
            project_name="demo", repo_owner="o", repo_name="r",
            tag_prefix="demo-v", install_dir_system="/opt/demo",
            install_dir_user="demo",
            entrypoint="adapter.py",
        )
        ns: dict = {}
        exec(compile(src, "<rendered-get.py>", "exec"), ns)
        _invoke_adapter = ns["_invoke_adapter"]

        captured_env: list = []

        def fake_run(cmd, env=None, **kw):
            captured_env.append(dict(env or os.environ))
            result = mock.MagicMock()
            result.returncode = 0
            return result

        adapter_dir = tmp_path / "release"
        adapter_dir.mkdir()
        (adapter_dir / "adapter.py").touch()
        venv_dir = tmp_path / "venv"
        (venv_dir / "bin").mkdir(parents=True)
        (venv_dir / "bin" / "python").touch()

        os.environ["GITHUB_TOKEN"] = "secret-token"
        try:
            with mock.patch("subprocess.run", side_effect=fake_run):
                _invoke_adapter("bootstrap", adapter_dir, tmp_path, venv_dir, token="secret-token")
        finally:
            del os.environ["GITHUB_TOKEN"]

        for env in captured_env:
            assert "GITHUB_TOKEN" not in env
            assert "CMRU_GITHUB_TOKEN" not in env
            assert "GITHUB_PUSH_PAT" not in env

    def test_token_file_bad_permissions_rejected(self, tmp_path):
        """Token file with loose permissions must be rejected (exit 2)."""
        from cmru.getpy import render_get_py
        src = render_get_py(
            project_name="demo", repo_owner="o", repo_name="r",
            tag_prefix="demo-v", install_dir_system="/opt/demo",
            install_dir_user="demo",
        )
        ns: dict = {}
        exec(compile(src, "<rendered-get.py>", "exec"), ns)
        _validate_token_file = ns["_validate_token_file"]

        tok_file = tmp_path / "token"
        tok_file.write_text("mytoken")
        tok_file.chmod(0o644)  # world-readable — bad

        with pytest.raises(SystemExit) as exc:
            _validate_token_file(tok_file)
        assert exc.value.code == 2

    def test_token_file_good_permissions_accepted(self, tmp_path):
        from cmru.getpy import render_get_py
        src = render_get_py(
            project_name="demo", repo_owner="o", repo_name="r",
            tag_prefix="demo-v", install_dir_system="/opt/demo",
            install_dir_user="demo",
        )
        ns: dict = {}
        exec(compile(src, "<rendered-get.py>", "exec"), ns)
        _validate_token_file = ns["_validate_token_file"]

        tok_file = tmp_path / "token"
        tok_file.write_text("mytoken")
        tok_file.chmod(0o600)

        # should not raise
        _validate_token_file(tok_file)

    def test_401_handled_with_clear_error(self):
        from cmru.getpy import render_get_py
        src = render_get_py(
            project_name="demo", repo_owner="o", repo_name="r",
            tag_prefix="demo-v", install_dir_system="/opt/demo",
            install_dir_user="demo",
        )
        ns: dict = {}
        exec(compile(src, "<rendered-get.py>", "exec"), ns)

        # Replace _gh_request in the namespace directly then call _gh_json
        original_gh_request = ns["_gh_request"]
        ns["_gh_request"] = lambda url, token=None, **kw: (401, b'{"message": "Unauthorized"}')
        try:
            with pytest.raises(SystemExit) as exc:
                ns["_gh_json"]("https://api.github.com/repos/o/r/releases")
            assert exc.value.code == 1
        finally:
            ns["_gh_request"] = original_gh_request


# ─── SHA256 + signature verification tests ───────────────────────────────────

class TestVerification:
    def _get_ns(self):
        from cmru.getpy import render_get_py
        src = render_get_py(
            project_name="demo", repo_owner="o", repo_name="r",
            tag_prefix="demo-v", install_dir_system="/opt/demo",
            install_dir_user="demo",
            manifest_name="manifest.json",
            signature_name="manifest.json.minisig",
        )
        ns: dict = {}
        exec(compile(src, "<rendered-get.py>", "exec"), ns)
        return ns

    def test_sha256_mismatch_aborts_exit_1(self, tmp_path):
        ns = self._get_ns()
        _verify_sha256 = ns["_verify_sha256"]

        asset = tmp_path / "bundle.tar.xz"
        asset.write_bytes(b"real content")
        sidecar = tmp_path / "bundle.tar.xz.sha256"
        sidecar.write_text("deadbeef0000000000000000000000000000000000000000000000000000dead  bundle.tar.xz\n")

        with pytest.raises(SystemExit) as exc:
            _verify_sha256(asset, sidecar)
        assert exc.value.code == 1

    def test_sha256_match_passes(self, tmp_path):
        ns = self._get_ns()
        _verify_sha256 = ns["_verify_sha256"]
        _sha256 = ns["_sha256"]

        asset = tmp_path / "bundle.tar.xz"
        asset.write_bytes(b"real content")
        digest = _sha256(asset)
        sidecar = tmp_path / "bundle.tar.xz.sha256"
        sidecar.write_text(f"{digest}  bundle.tar.xz\n")

        # should not raise
        _verify_sha256(asset, sidecar)

    def test_minisign_failure_aborts_exit_1(self, tmp_path):
        ns = self._get_ns()
        _verify_minisign = ns["_verify_minisign"]

        manifest = tmp_path / "manifest.json"
        manifest.write_text("{}")
        sig = tmp_path / "manifest.json.minisig"
        sig.write_text("bad sig")

        # Mock subprocess.run to simulate minisign failure
        def fake_run(cmd, **kw):
            r = mock.MagicMock()
            r.returncode = 1
            r.stderr = "signature verification failed"
            return r

        with mock.patch("subprocess.run", side_effect=fake_run):
            with pytest.raises(SystemExit) as exc:
                _verify_minisign(manifest, sig, "RWS...")
            assert exc.value.code == 1

    def test_minisign_success_passes(self, tmp_path):
        ns = self._get_ns()
        _verify_minisign = ns["_verify_minisign"]

        manifest = tmp_path / "manifest.json"
        manifest.write_text("{}")
        sig = tmp_path / "manifest.json.minisig"
        sig.write_text("good sig")

        def fake_run(cmd, **kw):
            r = mock.MagicMock()
            r.returncode = 0
            r.stderr = ""
            return r

        with mock.patch("subprocess.run", side_effect=fake_run):
            _verify_minisign(manifest, sig, "RWS...")  # should not raise


# ─── Extraction safety tests ─────────────────────────────────────────────────

class TestExtractionSafety:
    def _get_safe_member(self):
        from cmru.getpy import render_get_py
        src = render_get_py(
            project_name="demo", repo_owner="o", repo_name="r",
            tag_prefix="demo-v", install_dir_system="/opt/demo",
            install_dir_user="demo",
        )
        ns: dict = {}
        exec(compile(src, "<rendered-get.py>", "exec"), ns)
        return ns["_safe_member"]

    def _make_member(self, name: str, type_=tarfile.REGTYPE, linkname: str = "") -> tarfile.TarInfo:
        m = tarfile.TarInfo(name=name)
        m.type = type_
        m.linkname = linkname
        return m

    def test_normal_member_accepted(self, tmp_path):
        _safe_member = self._get_safe_member()
        m = self._make_member("some/file.txt")
        assert _safe_member(m, tmp_path) is True

    def test_absolute_path_rejected(self, tmp_path):
        _safe_member = self._get_safe_member()
        m = self._make_member("/etc/passwd")
        assert _safe_member(m, tmp_path) is False

    def test_path_traversal_rejected(self, tmp_path):
        _safe_member = self._get_safe_member()
        m = self._make_member("some/../../../etc/passwd")
        assert _safe_member(m, tmp_path) is False

    def test_device_node_rejected(self, tmp_path):
        _safe_member = self._get_safe_member()
        m = self._make_member("dev/null")
        m.type = tarfile.CHRTYPE
        assert _safe_member(m, tmp_path) is False

    def test_fifo_rejected(self, tmp_path):
        _safe_member = self._get_safe_member()
        m = self._make_member("myfifo")
        m.type = tarfile.FIFOTYPE
        assert _safe_member(m, tmp_path) is False

    def test_absolute_symlink_rejected(self, tmp_path):
        _safe_member = self._get_safe_member()
        m = self._make_member("link", type_=tarfile.SYMTYPE, linkname="/etc/passwd")
        assert _safe_member(m, tmp_path) is False

    def test_traversal_symlink_rejected(self, tmp_path):
        _safe_member = self._get_safe_member()
        m = self._make_member("link", type_=tarfile.SYMTYPE, linkname="../../evil")
        assert _safe_member(m, tmp_path) is False

    def test_relative_symlink_accepted(self, tmp_path):
        _safe_member = self._get_safe_member()
        m = self._make_member("link", type_=tarfile.SYMTYPE, linkname="target.txt")
        assert _safe_member(m, tmp_path) is True

    def test_hardlink_traversal_rejected(self, tmp_path):
        _safe_member = self._get_safe_member()
        m = self._make_member("link", type_=tarfile.LNKTYPE, linkname="../../evil")
        assert _safe_member(m, tmp_path) is False


# ─── Transaction tests ────────────────────────────────────────────────────────

class TestTransaction:
    """Integration-style tests for install/update/rollback using in-process helpers."""

    def _build_ns(self, tmp_path: Path, project_name: str = "demo") -> dict:
        """Render get.py with no entrypoint (no adapter) and execute it."""
        from cmru.getpy import render_get_py
        src = render_get_py(
            project_name=project_name,
            repo_owner="o",
            repo_name="r",
            tag_prefix="demo-v",
            install_dir_system=str(tmp_path / "system"),
            install_dir_user=project_name,
            entrypoint="",
            required_commands=[],
            preserve_paths=["shared/host.toml"],
            manifest_name="manifest.json",
            signature_name="manifest.json.minisig",
        )
        ns: dict = {}
        exec(compile(src, "<rendered-get.py>", "exec"), ns)
        # Override XDG_DATA_HOME to use tmp_path for user scope
        os.environ["XDG_DATA_HOME"] = str(tmp_path / "xdg")
        return ns

    def _make_bundle(self, workdir: Path, tag: str, files: dict) -> Tuple[Path, Path]:
        """Create a minimal .tar.xz bundle + .sha256 sidecar."""
        asset_name = f"{tag}.tar.xz"
        asset = workdir / asset_name
        with tarfile.open(asset, "w:xz") as tf:
            for rel_path, content in files.items():
                full = f"{tag}/{rel_path}"
                data = content.encode() if isinstance(content, str) else content
                info = tarfile.TarInfo(name=full)
                info.size = len(data)
                import io
                tf.addfile(info, io.BytesIO(data))
        digest = hashlib.sha256(asset.read_bytes()).hexdigest()
        sidecar = workdir / f"{asset_name}.sha256"
        sidecar.write_text(f"{digest}  {asset_name}\n")
        return asset, sidecar

    def _patch_download(self, ns: dict, workdir: Path, tag: str):
        """Patch _download_asset to copy from workdir instead of hitting network."""
        asset_name = f"{tag}{ns['ASSET_SUFFIX']}"
        sidecar_name = f"{asset_name}.sha256"

        def fake_download(t, name, dest, token):
            src = workdir / name
            shutil.copy2(src, dest)

        ns["_download_asset"] = fake_download

    def test_install_creates_current_symlink(self, tmp_path, monkeypatch):
        ns = self._build_ns(tmp_path)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

        tag = "demo-v0.1.0"
        bundle_dir = tmp_path / "bundles"
        bundle_dir.mkdir()
        self._make_bundle(bundle_dir, tag, {"VERSION": "0.1.0"})
        self._patch_download(ns, bundle_dir, tag)

        # Monkeypatch minisign check (skip)
        ns["MANIFEST_NAME"] = ""
        ns["SIGNATURE_NAME"] = ""

        root = Path(ns["INSTALL_DIR_SYSTEM"])
        root.mkdir(parents=True, exist_ok=True)

        args = mock.MagicMock()
        args.version = tag
        args.scope = "system"
        args.manifest_pubkey = None

        with mock.patch.object(sys.modules.get("os", os), "geteuid", return_value=0):
            ns["do_install"](args, token=None)

        current = root / "current"
        assert current.is_symlink()
        assert current.resolve().name == tag

    def test_update_changes_current(self, tmp_path, monkeypatch):
        ns = self._build_ns(tmp_path)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

        tag1 = "demo-v0.1.0"
        tag2 = "demo-v0.2.0"
        bundle_dir = tmp_path / "bundles"
        bundle_dir.mkdir()
        self._make_bundle(bundle_dir, tag1, {"VERSION": "0.1.0"})
        self._make_bundle(bundle_dir, tag2, {"VERSION": "0.2.0"})
        self._patch_download(ns, bundle_dir, tag1)

        ns["MANIFEST_NAME"] = ""
        ns["SIGNATURE_NAME"] = ""
        root = Path(ns["INSTALL_DIR_SYSTEM"])
        root.mkdir(parents=True, exist_ok=True)

        args1 = mock.MagicMock()
        args1.version = tag1
        args1.scope = "system"
        args1.manifest_pubkey = None

        with mock.patch.object(sys.modules.get("os", os), "geteuid", return_value=0):
            ns["do_install"](args1, token=None)

        # Now update
        self._patch_download(ns, bundle_dir, tag2)
        args2 = mock.MagicMock()
        args2.version = tag2
        args2.scope = "system"
        args2.manifest_pubkey = None

        with mock.patch.object(sys.modules.get("os", os), "geteuid", return_value=0):
            ns["do_update"](args2, token=None)

        current = root / "current"
        assert current.resolve().name == tag2

    def test_rollback_restores_previous(self, tmp_path, monkeypatch):
        ns = self._build_ns(tmp_path)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

        tag1 = "demo-v0.1.0"
        tag2 = "demo-v0.2.0"
        bundle_dir = tmp_path / "bundles"
        bundle_dir.mkdir()
        self._make_bundle(bundle_dir, tag1, {"VERSION": "0.1.0"})
        self._make_bundle(bundle_dir, tag2, {"VERSION": "0.2.0"})

        ns["MANIFEST_NAME"] = ""
        ns["SIGNATURE_NAME"] = ""
        root = Path(ns["INSTALL_DIR_SYSTEM"])
        root.mkdir(parents=True, exist_ok=True)

        self._patch_download(ns, bundle_dir, tag1)
        args1 = mock.MagicMock()
        args1.version = tag1
        args1.scope = "system"
        args1.manifest_pubkey = None
        with mock.patch.object(sys.modules.get("os", os), "geteuid", return_value=0):
            ns["do_install"](args1, token=None)

        self._patch_download(ns, bundle_dir, tag2)
        args2 = mock.MagicMock()
        args2.version = tag2
        args2.scope = "system"
        args2.manifest_pubkey = None
        with mock.patch.object(sys.modules.get("os", os), "geteuid", return_value=0):
            ns["do_update"](args2, token=None)

        args_rb = mock.MagicMock()
        args_rb.version = None
        args_rb.scope = "system"
        with mock.patch.object(sys.modules.get("os", os), "geteuid", return_value=0):
            ns["do_rollback"](args_rb, token=None)

        current = root / "current"
        assert current.resolve().name == tag1

    def test_interrupted_update_leaves_current_live(self, tmp_path, monkeypatch):
        """Staging dir exists but current still points to previous when update is interrupted."""
        ns = self._build_ns(tmp_path)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

        tag1 = "demo-v0.1.0"
        tag2 = "demo-v0.2.0"
        bundle_dir = tmp_path / "bundles"
        bundle_dir.mkdir()
        self._make_bundle(bundle_dir, tag1, {"VERSION": "0.1.0"})
        self._make_bundle(bundle_dir, tag2, {"VERSION": "0.2.0"})

        ns["MANIFEST_NAME"] = ""
        ns["SIGNATURE_NAME"] = ""
        root = Path(ns["INSTALL_DIR_SYSTEM"])
        root.mkdir(parents=True, exist_ok=True)

        # First install tag1
        self._patch_download(ns, bundle_dir, tag1)
        args1 = mock.MagicMock()
        args1.version = tag1
        args1.scope = "system"
        args1.manifest_pubkey = None
        with mock.patch.object(sys.modules.get("os", os), "geteuid", return_value=0):
            ns["do_install"](args1, token=None)

        # Simulate interrupted update: extraction succeeds but _atomic_swap_current raises
        self._patch_download(ns, bundle_dir, tag2)
        original_swap = ns["_atomic_swap_current"]

        def fail_on_swap(root_, new_release):
            raise RuntimeError("simulated interrupt")

        ns["_atomic_swap_current"] = fail_on_swap

        args2 = mock.MagicMock()
        args2.version = tag2
        args2.scope = "system"
        args2.manifest_pubkey = None
        with mock.patch.object(sys.modules.get("os", os), "geteuid", return_value=0):
            try:
                ns["do_update"](args2, token=None)
            except RuntimeError:
                pass

        # current must still point to tag1
        current = root / "current"
        assert current.resolve().name == tag1

    def test_user_scope_path(self, tmp_path, monkeypatch):
        """User scope resolves to XDG_DATA_HOME/<install_dir_user>."""
        from cmru.getpy import render_get_py
        src = render_get_py(
            project_name="demo", repo_owner="o", repo_name="r",
            tag_prefix="demo-v", install_dir_system="/opt/demo",
            install_dir_user="my-demo",
        )
        ns: dict = {}
        exec(compile(src, "<rendered-get.py>", "exec"), ns)
        xdg = tmp_path / "xdg"
        monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
        root = ns["_root_dir"]("user")
        assert root == xdg / "my-demo"

    def test_system_scope_path(self, tmp_path):
        from cmru.getpy import render_get_py
        src = render_get_py(
            project_name="demo", repo_owner="o", repo_name="r",
            tag_prefix="demo-v", install_dir_system="/opt/demo",
            install_dir_user="demo",
        )
        ns: dict = {}
        exec(compile(src, "<rendered-get.py>", "exec"), ns)
        root = ns["_root_dir"]("system")
        assert root == Path("/opt/demo")


# ─── Adapter contract tests ────────────────────────────────────────────────────

class TestAdapter:
    def _build_ns_with_adapter(self, tmp_path: Path) -> dict:
        from cmru.getpy import render_get_py
        src = render_get_py(
            project_name="demo",
            repo_owner="o",
            repo_name="r",
            tag_prefix="demo-v",
            install_dir_system=str(tmp_path / "system"),
            install_dir_user="demo",
            entrypoint="scripts/bootstrap.py",
            required_commands=[],
            manifest_name="manifest.json",
            signature_name="manifest.json.minisig",
        )
        ns: dict = {}
        exec(compile(src, "<rendered-get.py>", "exec"), ns)
        return ns

    def test_adapter_invoked_with_correct_argv(self, tmp_path):
        ns = self._build_ns_with_adapter(tmp_path)
        _invoke_adapter = ns["_invoke_adapter"]

        release_dir = tmp_path / "release"
        release_dir.mkdir(parents=True)
        adapter_script = release_dir / "scripts" / "bootstrap.py"
        adapter_script.parent.mkdir(parents=True, exist_ok=True)
        adapter_script.touch()

        venv_dir = tmp_path / "venv"
        (venv_dir / "bin").mkdir(parents=True)
        (venv_dir / "bin" / "python").touch()

        root = tmp_path
        (root / "shared").mkdir(exist_ok=True)
        (root / "shared" / "host.toml").touch()

        captured_cmd: list = []

        def fake_run(cmd, env=None, **kw):
            captured_cmd.append(cmd)
            r = mock.MagicMock()
            r.returncode = 0
            return r

        with mock.patch("subprocess.run", side_effect=fake_run):
            _invoke_adapter("bootstrap", release_dir, root, venv_dir, token=None)

        assert len(captured_cmd) == 1
        cmd = captured_cmd[0]
        # argv: venv/bin/python adapter.py action --release-root ... --config ... --manifest ...
        assert str(venv_dir / "bin" / "python") == cmd[0]
        assert str(adapter_script) == cmd[1]
        assert "bootstrap" == cmd[2]
        assert "--release-root" in cmd
        assert str(release_dir) == cmd[cmd.index("--release-root") + 1]
        assert "--config" in cmd
        assert "--manifest" in cmd

    def test_nonzero_adapter_aborts_before_swap(self, tmp_path):
        """Non-zero adapter exit aborts before current swap."""
        ns = self._build_ns_with_adapter(tmp_path)
        _invoke_adapter = ns["_invoke_adapter"]

        release_dir = tmp_path / "release"
        release_dir.mkdir(parents=True)
        adapter_script = release_dir / "scripts" / "bootstrap.py"
        adapter_script.parent.mkdir(parents=True, exist_ok=True)
        adapter_script.touch()

        venv_dir = tmp_path / "venv"
        (venv_dir / "bin").mkdir(parents=True)
        (venv_dir / "bin" / "python").touch()
        root = tmp_path
        (root / "shared").mkdir(exist_ok=True)
        (root / "shared" / "host.toml").touch()

        def fake_run_fail(cmd, env=None, **kw):
            r = mock.MagicMock()
            r.returncode = 1
            return r

        with mock.patch("subprocess.run", side_effect=fake_run_fail):
            with pytest.raises(SystemExit) as exc:
                _invoke_adapter("bootstrap", release_dir, root, venv_dir, token=None)
            assert exc.value.code == 1

    def test_no_entrypoint_no_adapter_call(self, tmp_path):
        """When ENTRYPOINT is empty, _invoke_adapter returns immediately."""
        from cmru.getpy import render_get_py
        src = render_get_py(
            project_name="demo", repo_owner="o", repo_name="r",
            tag_prefix="demo-v", install_dir_system="/opt/demo",
            install_dir_user="demo",
            entrypoint="",
        )
        ns: dict = {}
        exec(compile(src, "<rendered-get.py>", "exec"), ns)
        _invoke_adapter = ns["_invoke_adapter"]

        called = []
        with mock.patch("subprocess.run", side_effect=lambda *a, **k: called.append(a)):
            _invoke_adapter("bootstrap",
                            tmp_path / "release",
                            tmp_path,
                            tmp_path / "venv",
                            token=None)
        assert called == [], "subprocess.run should not be called when ENTRYPOINT is empty"


# ─── Scope resolution tests ───────────────────────────────────────────────────

class TestScopeResolution:
    def _get_root_dir(self, install_dir_system="/opt/demo", install_dir_user="demo"):
        from cmru.getpy import render_get_py
        src = render_get_py(
            project_name="demo", repo_owner="o", repo_name="r",
            tag_prefix="demo-v",
            install_dir_system=install_dir_system,
            install_dir_user=install_dir_user,
        )
        ns: dict = {}
        exec(compile(src, "<rendered-get.py>", "exec"), ns)
        return ns["_root_dir"]

    def test_system_scope(self):
        root_dir = self._get_root_dir(install_dir_system="/opt/myapp")
        assert root_dir("system") == Path("/opt/myapp")

    def test_user_scope_with_xdg(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        root_dir = self._get_root_dir(install_dir_user="my-leaf")
        assert root_dir("user") == tmp_path / "my-leaf"

    def test_user_scope_no_xdg(self, monkeypatch):
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        root_dir = self._get_root_dir(install_dir_user="my-leaf")
        expected = Path.home() / ".local" / "share" / "my-leaf"
        assert root_dir("user") == expected


# ─── Prerequisite check tests ─────────────────────────────────────────────────

class TestPrerequisites:
    def test_missing_command_exits_3(self):
        from cmru.getpy import render_get_py
        src = render_get_py(
            project_name="demo", repo_owner="o", repo_name="r",
            tag_prefix="demo-v", install_dir_system="/opt/demo",
            install_dir_user="demo",
            required_commands=["__nonexistent_cmd_xyz__"],
        )
        ns: dict = {}
        exec(compile(src, "<rendered-get.py>", "exec"), ns)
        with pytest.raises(SystemExit) as exc:
            ns["check_prerequisites"]()
        assert exc.value.code == 3

    def test_present_commands_pass(self):
        from cmru.getpy import render_get_py
        src = render_get_py(
            project_name="demo", repo_owner="o", repo_name="r",
            tag_prefix="demo-v", install_dir_system="/opt/demo",
            install_dir_user="demo",
            required_commands=["python3"],
        )
        ns: dict = {}
        exec(compile(src, "<rendered-get.py>", "exec"), ns)
        # Should not raise
        ns["check_prerequisites"]()

    def test_empty_required_commands_passes(self):
        from cmru.getpy import render_get_py
        src = render_get_py(
            project_name="demo", repo_owner="o", repo_name="r",
            tag_prefix="demo-v", install_dir_system="/opt/demo",
            install_dir_user="demo",
            required_commands=[],
        )
        ns: dict = {}
        exec(compile(src, "<rendered-get.py>", "exec"), ns)
        ns["check_prerequisites"]()  # should not raise


# ─── CLI dispatch (extend test_cli_dispatch.py coverage) ─────────────────────

class TestGetPyCLI:
    def test_getpy_main_no_config_exits_2(self, capsys):
        from cmru.getpy import getpy_main
        with pytest.raises(SystemExit) as exc:
            getpy_main(["--project", "demo"])
        assert exc.value.code == 2

    def test_getpy_main_missing_project_exits(self, tmp_path):
        toml = _minimal_toml()
        cfg_path = _write(tmp_path, toml)
        from cmru.getpy import getpy_main
        with pytest.raises((ValueError, SystemExit)):
            getpy_main(["--project", "nonexistent", "--config", str(cfg_path)])

    def test_getpy_main_to_stdout(self, tmp_path):
        toml = _minimal_toml("""
[project.demo.installer]
install_dir_system = "/opt/demo"
install_dir_user   = "demo"
""")
        cfg_path = _write(tmp_path, toml)
        import io, re
        from contextlib import redirect_stdout
        from cmru.getpy import getpy_main
        buf = io.StringIO()
        with redirect_stdout(buf):
            getpy_main(["--project", "demo", "--config", str(cfg_path)])
        output = buf.getvalue()
        remaining = re.findall(r"\[\[[A-Z_]+\]\]", output)
        assert remaining == [], f"Unreplaced placeholders in stdout: {remaining}"

    def test_getpy_main_to_file(self, tmp_path):
        toml = _minimal_toml("""
[project.demo.installer]
install_dir_system = "/opt/demo"
install_dir_user   = "demo"
""")
        cfg_path = _write(tmp_path, toml)
        import re
        from cmru.getpy import getpy_main
        out_file = tmp_path / "get.py"
        getpy_main([
            "--project", "demo",
            "--config", str(cfg_path),
            "--output", str(out_file),
        ])
        assert out_file.exists()
        content = out_file.read_text()
        remaining = re.findall(r"\[\[[A-Z_]+\]\]", content)
        assert remaining == [], f"Unreplaced placeholders in output file: {remaining}"
        # File should be executable
        assert out_file.stat().st_mode & 0o111

    def test_getpy_getsh_rejected_exit_2(self, tmp_path):
        """cmru get-py with [getsh] config exits 2."""
        toml = _minimal_toml("""
[project.demo.getsh]
install_dir = "/opt/demo"
""")
        cfg_path = _write(tmp_path, toml)
        from cmru.getpy import getpy_main
        with pytest.raises(SystemExit) as exc:
            getpy_main(["--project", "demo", "--config", str(cfg_path)])
        assert exc.value.code == 2
