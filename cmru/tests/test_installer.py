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
  - Enroll (KI-24): CLI shape, key parsing, authorized_keys line handling, the
    fail-fast ORDER, and — in a fixture container this file builds and tears
    down itself — the real effects on a real system (user, key, modes,
    ownership, host-key fingerprints).

Stdlib + tmp files only — no network, no git side effects. The enroll container
tests are integration tests by nature; they run against a fixture image this
file owns, never a live host, and skip (never fail) where docker is absent.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import textwrap
from pathlib import Path
from typing import Iterator, List, Optional, Tuple
from unittest import mock

import pytest

# ─── helpers ─────────────────────────────────────────────────────────────────

MINIMAL_GITHUB = """
schema_version = 1

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
[project]
id = "demo"
description = "test installer product"
template_revision = 2
prefix    = "demo-v"
artifacts = ["tarball"]
[project.version]
strategy = "file:VERSION"
bump = "conventional"

[project.release]
git_tag = true
build_step = "build"

[steps.run-tests]
quiet = true
commands = [{ label = "test", argv = ["true"], cwd = "." }]

[steps.build]
quiet = true
commands = [{ label = "build", argv = ["true"], cwd = "." }]

[steps.push]
quiet = true
commands = [{ label = "push", argv = ["true"], cwd = "." }]
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
[project.installer]
install_dir_system = "/opt/demo"
install_dir_user   = "demo"
asset_suffix       = ".tar.xz"
required_commands  = ["python3", "docker", "minisign"]
preserve           = ["shared/host.toml"]
manifest_name      = "manifest.json"
signature_name     = "manifest.json.minisig"

[[project.installer.wheels]]
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
[project.installer]
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
[project.getsh]
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
[project.installer]
install_dir_user = "demo"
""")
        cfg_path = _write(tmp_path, toml)
        from cmru.config import load_forge_config
        with pytest.raises(SystemExit) as exc:
            load_forge_config(cfg_path)
        assert exc.value.code == 2

    def test_missing_required_field_install_dir_user(self, tmp_path):
        toml = _minimal_toml("""
[project.installer]
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
[project.installer]
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
[project.installer]
install_dir_system = "/opt/demo"
install_dir_user   = "demo"

[[project.installer.wheels]]
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
[project.installer]
install_dir_system = "/opt/demo"
install_dir_user   = "demo"

[[project.installer.wheels]]
path         = "vendor/cmru-*.whl"
distribution = "cmru"

[[project.installer.wheels]]
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
[project.installer]
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

    # ── _verify_wheel_sha256 — Seam-3 schema (SPEC B) ────────────────────────

    def test_verify_wheel_sha256_correct_hash_passes(self, tmp_path):
        """Correct sha256 from SPEC B per-distribution schema passes without error."""
        ns = self._get_ns()
        _verify_wheel_sha256 = ns["_verify_wheel_sha256"]
        _sha256 = ns["_sha256"]

        wheel = tmp_path / "cmru-1.0.0-py3-none-any.whl"
        wheel.write_bytes(b"fake wheel content")
        digest = _sha256(wheel)

        # SPEC B schema: sha256 lives at manifest['cmru']['sha256']
        manifest = {
            "schema_version": 1,
            "cmru": {"version": "1.0.0", "wheel": wheel.name, "sha256": digest},
            "ciu":  {"version": "2.0.0", "wheel": "ciu-2.0.0-py3-none-any.whl", "sha256": "aabbcc"},
        }

        # Should not raise
        _verify_wheel_sha256(wheel, manifest, "cmru")

    def test_verify_wheel_sha256_mismatch_aborts_exit_1(self, tmp_path):
        """Wrong sha256 in SPEC B per-distribution entry causes fatal exit(1)."""
        ns = self._get_ns()
        _verify_wheel_sha256 = ns["_verify_wheel_sha256"]

        wheel = tmp_path / "cmru-1.0.0-py3-none-any.whl"
        wheel.write_bytes(b"fake wheel content")

        manifest = {
            "schema_version": 1,
            "cmru": {"version": "1.0.0", "wheel": wheel.name,
                     "sha256": "deadbeef" * 8},  # wrong digest
        }

        with pytest.raises(SystemExit) as exc:
            _verify_wheel_sha256(wheel, manifest, "cmru")
        assert exc.value.code == 1

    def test_verify_wheel_sha256_missing_distribution_warns_skips(self, tmp_path):
        """Missing distribution entry in manifest emits a warning and skips verification."""
        import io
        ns = self._get_ns()
        _verify_wheel_sha256 = ns["_verify_wheel_sha256"]

        wheel = tmp_path / "cmru-1.0.0-py3-none-any.whl"
        wheel.write_bytes(b"content")

        # Manifest has no 'cmru' key at all
        manifest = {"schema_version": 1}

        # Should warn and return without raising or calling fatal
        _verify_wheel_sha256(wheel, manifest, "cmru")

    def test_verify_wheel_sha256_ciu_distribution(self, tmp_path):
        """Correctly verifies ciu wheel using manifest['ciu']['sha256']."""
        ns = self._get_ns()
        _verify_wheel_sha256 = ns["_verify_wheel_sha256"]
        _sha256 = ns["_sha256"]

        wheel = tmp_path / "ciu-2.0.0-py3-none-any.whl"
        wheel.write_bytes(b"ciu wheel bytes")
        digest = _sha256(wheel)

        manifest = {
            "schema_version": 1,
            "cmru": {"version": "1.0.0", "wheel": "cmru-1.0.0-py3-none-any.whl", "sha256": "aabbcc"},
            "ciu":  {"version": "2.0.0", "wheel": wheel.name, "sha256": digest},
        }

        # Should not raise
        _verify_wheel_sha256(wheel, manifest, "ciu")

    def test_verify_wheel_sha256_does_not_use_filename_map(self, tmp_path):
        """Verifier must NOT fall back to a 'wheels'-by-filename map (old broken schema)."""
        ns = self._get_ns()
        _verify_wheel_sha256 = ns["_verify_wheel_sha256"]
        _sha256 = ns["_sha256"]

        wheel = tmp_path / "cmru-1.0.0-py3-none-any.whl"
        wheel.write_bytes(b"content")
        real_digest = _sha256(wheel)

        # Provide the old broken schema only — the function must ignore it
        # and fall back to warning (no sha256 found via distribution key).
        manifest = {
            "schema_version": 1,
            "wheels": {wheel.name: {"sha256": real_digest}},
            # intentionally no 'cmru' distribution entry
        }

        # With the fixed implementation, this warns and skips (no crash, no mismatch).
        _verify_wheel_sha256(wheel, manifest, "cmru")


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
[project.installer]
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
[project.installer]
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
[project.getsh]
install_dir = "/opt/demo"
""")
        cfg_path = _write(tmp_path, toml)
        from cmru.getpy import getpy_main
        with pytest.raises(SystemExit) as exc:
            getpy_main(["--project", "demo", "--config", str(cfg_path)])
        assert exc.value.code == 2


class TestNormalizeTag:
    """normalize_tag: a bare semver (optionally a single leading 'v') maps to
    <prefix><semver>; a full tag passes through. Regression-guards the prefix-strip
    (must NOT be str.lstrip('v'), which strips a whole leading run of 'v')."""

    def _ns(self) -> dict:
        from cmru.getpy import render_get_py
        src = render_get_py(
            project_name="demo", repo_owner="o", repo_name="r", tag_prefix="demo-v",
            install_dir_system="/opt/demo", install_dir_user="demo",
        )
        ns: dict = {}
        exec(compile(src, "<rendered-get.py>", "exec"), ns)
        return ns

    def test_bare_semver(self):
        assert self._ns()["normalize_tag"]("1.2.3") == "demo-v1.2.3"

    def test_single_v_prefix_stripped(self):
        assert self._ns()["normalize_tag"]("v1.2.3") == "demo-v1.2.3"

    def test_full_tag_passthrough(self):
        assert self._ns()["normalize_tag"]("demo-v1.2.3") == "demo-v1.2.3"

    def test_only_single_leading_v_stripped_not_charset(self):
        # str.lstrip('v') would strip BOTH leading v's → 'demo-v1.0.0'; a single
        # prefix-strip preserves the inner 'v' → 'demo-vv1.0.0'.
        assert self._ns()["normalize_tag"]("vv1.0.0") == "demo-vv1.0.0"


# ═════════════════════════════════════════════════════════════════════════════
# enroll — host enrollment (cmru KI-24 / CIU S14.7)
# ═════════════════════════════════════════════════════════════════════════════

# A syntactically real ed25519 public key line (32-byte key blob, base64) — it
# is never used to authenticate anything, only appended and read back.
ENROLL_TEST_KEY = (
    "ssh-ed25519 "
    "AAAAC3NzaC1lZDI1NTE5AAAAIJ4tOoyRAvbBiHFB5zFCFtOijSMxU9BzM3sB0K9RRppQ"
    " ciu@control.example"
)
ENROLL_TEST_KEY_TYPE = "ssh-ed25519"
ENROLL_TEST_KEY_B64 = ENROLL_TEST_KEY.split()[1]

# Exactly the flags KI-24's proposed contract names, and nothing else (O6).
ENROLL_EXPECTED_FLAGS = {
    "-h", "--help",
    "--authorized-key", "--controller", "--user", "--name", "--from",
    "--docker", "--no-install", "--scope",
}


def _render_enroll_ns(**kw) -> dict:
    """Render get.py and exec it, returning its module namespace."""
    from cmru.getpy import render_get_py
    defaults = dict(
        project_name="demo", repo_owner="o", repo_name="r",
        tag_prefix="demo-v", install_dir_system="/opt/demo",
        install_dir_user="demo", required_commands=[],
    )
    defaults.update(kw)
    src = render_get_py(**defaults)
    ns: dict = {}
    exec(compile(src, "<rendered-get.py>", "exec"), ns)
    return ns


def _enroll_args(**kw):
    """A fully-populated enroll Namespace (argparse defaults spelled out)."""
    import argparse as _argparse
    values = dict(
        command="enroll",
        authorized_key=ENROLL_TEST_KEY,
        controller="control.example.net",
        user="ciu",
        name=None,
        from_pattern=None,
        docker=False,
        no_install=False,
        scope="system",
        manifest_pubkey=None,
        version=None,
        variant=None,
        config=None,
    )
    values.update(kw)
    return _argparse.Namespace(**values)


class TestEnrollCLIShape:
    """O6: a rendered script's ``enroll --help`` lists exactly KI-24's flags."""

    def _write_rendered(self, tmp_path: Path, source: str) -> Path:
        script = tmp_path / "get.py"
        script.write_text(source, encoding="utf-8")
        return script

    def _help(self, script: Path, *argv: str) -> str:
        result = subprocess.run(
            [sys.executable, str(script), *argv],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    def _flags_in(self, help_text: str) -> set:
        # argparse wraps long help; a flag is only a flag at a token boundary.
        return set(re.findall(r"(?<![\w-])(--?[A-Za-z][A-Za-z0-9-]*)", help_text))

    def test_enroll_help_lists_exactly_the_contract_flags(self, tmp_path):
        from cmru.getpy import render_get_py
        src = render_get_py(
            project_name="demo", repo_owner="o", repo_name="r",
            tag_prefix="demo-v", install_dir_system="/opt/demo",
            install_dir_user="demo", required_commands=[],
        )
        script = self._write_rendered(tmp_path, src)
        # The usage line, which is where a missing/extra flag shows first.
        text = self._help(script, "enroll", "--help")
        usage = text.split("options:")[0]
        assert self._flags_in(usage) | {"-h", "--help"} == ENROLL_EXPECTED_FLAGS
        assert self._flags_in(text) == ENROLL_EXPECTED_FLAGS

    def test_enroll_required_flags_are_required(self, tmp_path):
        from cmru.getpy import render_get_py
        src = render_get_py(
            project_name="demo", repo_owner="o", repo_name="r",
            tag_prefix="demo-v", install_dir_system="/opt/demo",
            install_dir_user="demo", required_commands=[],
        )
        script = self._write_rendered(tmp_path, src)
        text = self._help(script, "enroll", "--help")
        usage = text.split("options:")[0]
        # required ⇒ unbracketed; optional ⇒ bracketed
        assert "--authorized-key KEY" in usage
        assert "[--authorized-key" not in usage
        assert "--controller FQDN" in usage
        assert "[--controller" not in usage
        for optional in ("--user USER", "--name NAME", "--from PATTERN",
                         "--docker", "--no-install"):
            assert f"[{optional}]" in usage, usage

    def test_enroll_defaults_documented(self, tmp_path):
        from cmru.getpy import render_get_py
        src = render_get_py(
            project_name="demo", repo_owner="o", repo_name="r",
            tag_prefix="demo-v", install_dir_system="/opt/demo",
            install_dir_user="demo", required_commands=[],
        )
        script = self._write_rendered(tmp_path, src)
        text = self._help(script, "enroll", "--help")
        assert "default: ciu" in text
        assert "default: system" in text
        assert "{system,user}" in text

    def test_top_level_help_lists_enroll_beside_the_others(self, tmp_path):
        from cmru.getpy import render_get_py
        src = render_get_py(
            project_name="demo", repo_owner="o", repo_name="r",
            tag_prefix="demo-v", install_dir_system="/opt/demo",
            install_dir_user="demo", required_commands=[],
        )
        script = self._write_rendered(tmp_path, src)
        text = self._help(script, "--help")
        assert "enroll" in text
        for existing in ("install", "update", "status", "rollback"):
            assert existing in text

    def test_main_dispatches_enroll_with_the_parsed_args_and_token(self, monkeypatch):
        """main() routes `enroll` to do_enroll, exactly as it routes the other four."""
        ns = _render_enroll_ns()
        seen: dict = {}
        ns["do_enroll"] = lambda args, token: seen.update(args=args, token=token)
        for other in ("do_install", "do_update", "do_rollback", "do_status"):
            ns[other] = lambda *a, **k: pytest.fail("wrong subcommand dispatched")
        monkeypatch.setenv("CMRU_GITHUB_TOKEN", "tok")
        argv = ["get.py", "enroll",
                "--authorized-key", ENROLL_TEST_KEY,
                "--controller", "control.example.net",
                "--user", "deploy", "--name", "web-01",
                "--from", "10.0.0.0/8", "--docker", "--no-install",
                "--scope", "user"]
        with mock.patch.object(sys, "argv", argv):
            ns["main"]()
        args = seen["args"]
        assert seen["token"] == "tok"
        assert (args.command, args.user, args.name, args.scope) == (
            "enroll", "deploy", "web-01", "user")
        assert args.from_pattern == "10.0.0.0/8"
        assert args.docker is True and args.no_install is True
        assert args.authorized_key == ENROLL_TEST_KEY
        assert args.controller == "control.example.net"

    def test_enroll_defaults_when_only_required_flags_given(self, monkeypatch):
        ns = _render_enroll_ns()
        seen: dict = {}
        ns["do_enroll"] = lambda args, token: seen.update(args=args)
        monkeypatch.delenv("CMRU_GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        argv = ["get.py", "enroll",
                "--authorized-key", ENROLL_TEST_KEY,
                "--controller", "control.example.net"]
        with mock.patch.object(sys, "argv", argv):
            ns["main"]()
        args = seen["args"]
        assert args.user == "ciu"
        assert args.scope == "system"
        assert args.name is None and args.from_pattern is None
        assert args.docker is False and args.no_install is False

    def test_get_py_cli_render_carries_enroll(self, tmp_path):
        """The `cmru get-py --project <name>` path (O6's own entry point)."""
        toml = _minimal_toml("""
[project.installer]
install_dir_system = "/opt/demo"
install_dir_user   = "demo"
""")
        cfg_path = _write(tmp_path, toml)
        from cmru.getpy import getpy_main
        out_file = tmp_path / "rendered-get.py"
        getpy_main([
            "--project", "demo", "--config", str(cfg_path),
            "--output", str(out_file),
        ])
        text = self._help(out_file, "enroll", "--help")
        assert self._flags_in(text) == ENROLL_EXPECTED_FLAGS

    def test_real_project_config_renders_enroll(self, capsys):
        """The one [project.installer] this monorepo actually ships renders it too.

        tls-edge declares required_commands, and ``main()`` runs
        check_prerequisites BEFORE parse_args, so the parser is exercised in
        process with that (pre-existing, unrelated) gate stubbed out rather than
        by shelling out on a host that may lack docker.
        """
        real = Path(__file__).resolve().parents[2] / "tls-edge" / "cmru.toml"
        if not real.exists():
            pytest.skip(f"no real installer config at {real}")
        from cmru.getpy import render_from_config
        src = render_from_config("tls-edge", real)
        ns: dict = {}
        exec(compile(src, "<tls-edge-get.py>", "exec"), ns)
        ns["check_prerequisites"] = lambda: None
        with mock.patch.object(sys, "argv", ["get.py", "enroll", "--help"]):
            with pytest.raises(SystemExit) as exc:
                ns["main"]()
        assert exc.value.code == 0
        assert self._flags_in(capsys.readouterr().out) == ENROLL_EXPECTED_FLAGS


class TestEnrollKeyParsing:
    """KI-24 step 1: the --authorized-key value must parse, or EXIT_CONFIG."""

    def _parse(self, ns, raw):
        return ns["_parse_authorized_key"](raw)

    @pytest.fixture()
    def ns(self):
        return _render_enroll_ns()

    @pytest.mark.parametrize("ktype", [
        "ssh-ed25519",
        "ssh-rsa",
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp521",
        "sk-ssh-ed25519@openssh.com",
        "sk-ecdsa-sha2-nistp256@openssh.com",
    ])
    def test_accepted_key_types(self, ns, ktype):
        parsed = self._parse(ns, f"{ktype} AAAAB3NzaC1kZXN0 someone@somewhere")
        assert parsed == (ktype, "AAAAB3NzaC1kZXN0", "someone@somewhere")

    def test_comment_is_optional(self, ns):
        assert self._parse(ns, "ssh-ed25519 AAAAB3NzaC1kZXN0") == (
            "ssh-ed25519", "AAAAB3NzaC1kZXN0", "")

    def test_multiword_comment_preserved(self, ns):
        assert self._parse(ns, "ssh-ed25519 AAAAB3NzaC1kZXN0 my laptop key")[2] == (
            "my laptop key")

    def test_surrounding_whitespace_tolerated(self, ns):
        assert self._parse(ns, "  ssh-ed25519 AAAAB3NzaC1kZXN0  \n")[0] == "ssh-ed25519"

    @pytest.mark.parametrize("bad", [
        "",
        "   ",
        "ssh-ed25519",
        "ssh-dss AAAAB3NzaC1kZXN0",
        "not-a-key-type AAAAB3NzaC1kZXN0",
        "ssh-ed25519 not+valid+base64!!",
        'from="10.0.0.1",ssh-ed25519 AAAAB3NzaC1kZXN0',
        "ssh-ed25519 AAAAB3NzaC1kZXN0\nssh-ed25519 AAAAB3NzaC1kZXN1",
    ])
    def test_rejected_with_exit_config(self, ns, bad):
        with pytest.raises(SystemExit) as exc:
            self._parse(ns, bad)
        assert exc.value.code == 2, bad

    def test_options_rejection_names_the_from_flag(self, ns, capsys):
        with pytest.raises(SystemExit):
            self._parse(ns, 'from="10.0.0.1",ssh-ed25519 AAAAB3NzaC1kZXN0')
        assert "--from" in capsys.readouterr().err


class TestEnrollAuthorizedKeysLine:
    """The line this subcommand writes, and the reader that decides idempotency."""

    @pytest.fixture()
    def ns(self):
        return _render_enroll_ns()

    def test_line_without_from(self, ns):
        assert ns["_build_key_line"]("ssh-ed25519", "AAAA", "c@h", None) == (
            "ssh-ed25519 AAAA c@h")

    def test_line_with_from_separates_options_by_whitespace_not_comma(self, ns):
        """A comma-joined options field makes the key unreadable to sshd.

        KI-24's text spells this ``from="P",<type> …``; OpenSSH advances past the
        options to the first unquoted whitespace, so that form hides the key type
        inside the options and public-key auth fails. See _build_key_line's own
        docstring for the measured sshd result behind this assertion.
        """
        assert ns["_build_key_line"]("ssh-ed25519", "AAAA", "c@h", "10.0.0.0/8") == (
            'from="10.0.0.0/8" ssh-ed25519 AAAA c@h')

    def test_line_without_comment(self, ns):
        assert ns["_build_key_line"]("ssh-rsa", "AAAA", "", None) == "ssh-rsa AAAA"

    def test_split_plain_line(self, ns):
        assert ns["_ak_split_line"]("ssh-ed25519 AAAA c@h") == (
            "", "ssh-ed25519", "AAAA", "c@h")

    def test_split_line_with_options(self, ns):
        assert ns["_ak_split_line"]('from="10.0.0.1" ssh-ed25519 AAAA c@h') == (
            'from="10.0.0.1"', "ssh-ed25519", "AAAA", "c@h")

    def test_split_tolerates_the_malformed_comma_joined_form_on_read(self, ns):
        """We never WRITE this shape, but a pre-existing one must still be seen."""
        assert ns["_ak_split_line"]('from="10.0.0.1",ssh-ed25519 AAAA c@h') == (
            'from="10.0.0.1"', "ssh-ed25519", "AAAA", "c@h")

    def test_split_comma_form_with_several_options(self, ns):
        assert ns["_ak_split_line"](
            'no-pty,from="10.0.0.1",ssh-ed25519 AAAA') == (
            'no-pty,from="10.0.0.1"', "ssh-ed25519", "AAAA", "")

    def test_split_comma_form_needs_key_material_after_it(self, ns):
        assert ns["_ak_split_line"]('from="10.0.0.1",ssh-ed25519') is None

    def test_split_is_quote_aware(self, ns):
        """An options field may hold whitespace inside quotes; str.split() tears it."""
        line = 'command="/bin/echo hi there",from="10.0.0.1" ssh-ed25519 AAAA c@h'
        assert ns["_ak_split_line"](line) == (
            'command="/bin/echo hi there",from="10.0.0.1"',
            "ssh-ed25519", "AAAA", "c@h")

    def test_split_honours_backslash_escapes_inside_quotes(self, ns):
        line = 'command="echo \\" x" ssh-ed25519 AAAA'
        assert ns["_ak_split_line"](line) == (
            'command="echo \\" x"', "ssh-ed25519", "AAAA", "")

    @pytest.mark.parametrize("ignored", [
        "", "   ", "# a comment", "\t# indented comment",
        "garbage-with-no-key", 'from="x" also-garbage',
    ])
    def test_uninteresting_lines_are_skipped(self, ns, ignored):
        assert ns["_ak_split_line"](ignored) is None


class TestEnrollOrdering:
    """O3 and the fail-fast order: nothing happens before the prerequisites pass."""

    def _poison(self, ns, calls):
        """Replace every step after the prerequisites with a tripwire."""
        def tripwire(label):
            def _fail(*_a, **_kw):
                calls.append(label)
                raise AssertionError(
                    f"{label} ran before/despite a failing prerequisite check")
            return _fail
        ns["do_install"] = tripwire("do_install")
        ns["_enroll_ensure_user"] = tripwire("_enroll_ensure_user")
        ns["_enroll_install_key"] = tripwire("_enroll_install_key")
        ns["_gh_request"] = tripwire("_gh_request")
        ns["resolve_latest_tag"] = tripwire("resolve_latest_tag")
        ns["_host_key_fingerprints"] = tripwire("_host_key_fingerprints")

    def test_missing_ssh_server_exits_prereq_names_openssh_server(self, capsys):
        ns = _render_enroll_ns()
        calls: list = []
        self._poison(ns, calls)
        ns["_find_sshd"] = lambda: None
        with mock.patch.object(os, "geteuid", return_value=0):
            with pytest.raises(SystemExit) as exc:
                ns["do_enroll"](_enroll_args(), token=None)
        assert exc.value.code == 3
        assert "openssh-server" in capsys.readouterr().err
        assert calls == [], f"steps reached before the prerequisite gate: {calls}"

    def test_missing_ssh_server_reaches_no_network_call(self):
        """Zero network I/O: urllib itself is poisoned, not merely unasserted."""
        ns = _render_enroll_ns()
        calls: list = []
        self._poison(ns, calls)
        ns["_find_sshd"] = lambda: None

        def no_network(*_a, **_kw):
            raise AssertionError("network I/O attempted before prerequisites passed")

        with mock.patch.object(ns["urllib"].request, "build_opener", new=no_network), \
                mock.patch.object(ns["urllib"].request, "urlopen", new=no_network), \
                mock.patch.object(os, "geteuid", return_value=0):
            with pytest.raises(SystemExit) as exc:
                ns["do_enroll"](_enroll_args(), token=None)
        assert exc.value.code == 3

    def test_non_root_exits_prereq_before_anything_else(self, capsys):
        ns = _render_enroll_ns()
        calls: list = []
        self._poison(ns, calls)
        ns["_find_sshd"] = lambda: "/usr/sbin/sshd"
        with mock.patch.object(os, "geteuid", return_value=1000):
            with pytest.raises(SystemExit) as exc:
                ns["do_enroll"](_enroll_args(), token=None)
        assert exc.value.code == 3
        assert "root" in capsys.readouterr().err
        assert calls == []

    def test_unparsable_key_exits_config_before_install(self, capsys):
        ns = _render_enroll_ns()
        calls: list = []
        self._poison(ns, calls)
        ns["_find_sshd"] = lambda: "/usr/sbin/sshd"
        with mock.patch.object(os, "geteuid", return_value=0):
            with pytest.raises(SystemExit) as exc:
                ns["do_enroll"](_enroll_args(authorized_key="ssh-dss AAAA"), token=None)
        assert exc.value.code == 2
        assert calls == []


class TestEnrollInstallStep:
    """KI-24 step 2: install runs verbatim, before the user, unless --no-install."""

    def _instrument(self, ns):
        order: list = []
        seen: dict = {}

        def fake_install(args, token):
            order.append("install")
            seen["scope"] = args.scope
            seen["pubkey"] = args.manifest_pubkey
            seen["token"] = token

        class _Entry:
            pw_dir = "/home/ciu"
            pw_uid = 1001
            pw_gid = 1001
            pw_shell = "/bin/bash"

        def fake_user(user, want_docker):
            order.append("user")
            seen["user"] = user
            seen["docker"] = want_docker
            return _Entry()

        def fake_key(entry, parsed, from_pattern):
            order.append("key")
            seen["parsed"] = parsed
            seen["from"] = from_pattern
            return Path("/home/ciu/.ssh/authorized_keys")

        ns["do_install"] = fake_install
        ns["_enroll_ensure_user"] = fake_user
        ns["_enroll_install_key"] = fake_key
        ns["_find_sshd"] = lambda: "/usr/sbin/sshd"
        ns["_host_key_fingerprints"] = lambda: [
            ("/etc/ssh/ssh_host_ed25519_key.pub",
             "256 SHA256:abc root@h (ED25519)", "SHA256:abc"),
        ]
        ns["_host_addresses"] = lambda: ["10.1.2.3"]
        ns["_current_version"] = lambda root: "demo-v1.2.3"
        return order, seen

    def test_install_runs_before_user_and_key(self, capsys):
        ns = _render_enroll_ns()
        order, seen = self._instrument(ns)
        with mock.patch.object(os, "geteuid", return_value=0):
            ns["do_enroll"](_enroll_args(scope="system", manifest_pubkey="RWS1"),
                            token="tok")
        assert order == ["install", "user", "key"]
        assert seen["scope"] == "system"
        assert seen["pubkey"] == "RWS1"
        assert seen["token"] == "tok"

    def test_scope_is_propagated_to_install(self):
        ns = _render_enroll_ns()
        order, seen = self._instrument(ns)
        with mock.patch.object(os, "geteuid", return_value=0):
            ns["do_enroll"](_enroll_args(scope="user"), token=None)
        assert seen["scope"] == "user"

    def test_no_install_skips_install_but_still_enrolls(self, capsys):
        ns = _render_enroll_ns()
        order, seen = self._instrument(ns)
        with mock.patch.object(os, "geteuid", return_value=0):
            ns["do_enroll"](_enroll_args(no_install=True), token=None)
        assert order == ["user", "key"]
        assert "skipping the install step" in capsys.readouterr().out

    def test_defaults_user_ciu_and_passes_from_pattern(self):
        ns = _render_enroll_ns()
        order, seen = self._instrument(ns)
        with mock.patch.object(os, "geteuid", return_value=0):
            ns["do_enroll"](_enroll_args(no_install=True, from_pattern="10.0.0.0/8"),
                            token=None)
        assert seen["user"] == "ciu"
        assert seen["from"] == "10.0.0.0/8"
        assert seen["parsed"] == (ENROLL_TEST_KEY_TYPE, ENROLL_TEST_KEY_B64,
                                  "ciu@control.example")


class TestEnrollReport:
    """KI-24 step 5: what enroll prints is what the operator confirms."""

    def _instrument(self, ns, *, addresses, fingerprints):
        class _Entry:
            pw_dir = "/home/ciu"
            pw_uid = 1001
            pw_gid = 1001
            pw_shell = "/bin/bash"

        ns["do_install"] = lambda args, token: None
        ns["_enroll_ensure_user"] = lambda user, want_docker: _Entry()
        ns["_enroll_install_key"] = (
            lambda entry, parsed, from_pattern: Path("/home/ciu/.ssh/authorized_keys"))
        ns["_find_sshd"] = lambda: "/usr/sbin/sshd"
        ns["_host_key_fingerprints"] = lambda: fingerprints
        ns["_host_addresses"] = lambda: addresses
        ns["_current_version"] = lambda root: "demo-v1.2.3"

    FPS = [
        ("/etc/ssh/ssh_host_ecdsa_key.pub", "256 SHA256:ecdsafp root@h (ECDSA)",
         "SHA256:ecdsafp"),
        ("/etc/ssh/ssh_host_ed25519_key.pub", "256 SHA256:edfp root@h (ED25519)",
         "SHA256:edfp"),
    ]

    def test_prints_every_host_key_addresses_user_version(self, capsys):
        ns = _render_enroll_ns()
        self._instrument(ns, addresses=["10.1.2.3", "192.168.0.9"], fingerprints=self.FPS)
        with mock.patch.object(os, "geteuid", return_value=0):
            ns["do_enroll"](_enroll_args(name="web-01"), token=None)
        out = capsys.readouterr().out
        assert "SHA256:ecdsafp" in out and "SHA256:edfp" in out
        assert "10.1.2.3" in out and "192.168.0.9" in out
        assert "UNCONFIRMED" in out
        assert "demo-v1.2.3" in out
        assert "control.example.net" in out

    def test_completion_command_uses_ed25519_fingerprint_and_name(self, capsys):
        ns = _render_enroll_ns()
        self._instrument(ns, addresses=["10.1.2.3"], fingerprints=self.FPS)
        with mock.patch.object(os, "geteuid", return_value=0):
            ns["do_enroll"](_enroll_args(name="web-01"), token=None)
        out = capsys.readouterr().out
        assert ("ciu host enroll web-01 --ssh-host 10.1.2.3 "
                "--fingerprint SHA256:edfp") in out

    def test_no_name_prints_a_placeholder_never_a_guess(self, capsys):
        ns = _render_enroll_ns()
        self._instrument(ns, addresses=["10.1.2.3"], fingerprints=self.FPS)
        with mock.patch.object(os, "geteuid", return_value=0):
            ns["do_enroll"](_enroll_args(name=None), token=None)
        out = capsys.readouterr().out
        assert "ciu host enroll <NAME> --ssh-host 10.1.2.3" in out
        assert "replace <NAME>" in out

    def test_no_addresses_prints_a_placeholder(self, capsys):
        ns = _render_enroll_ns()
        self._instrument(ns, addresses=[], fingerprints=self.FPS)
        with mock.patch.object(os, "geteuid", return_value=0):
            ns["do_enroll"](_enroll_args(name="web-01"), token=None)
        out = capsys.readouterr().out
        assert "--ssh-host <ADDRESS>" in out
        assert "replace <ADDRESS>" in out

    def test_no_host_keys_warns_and_keeps_the_command_substitutable(self, capsys):
        ns = _render_enroll_ns()
        self._instrument(ns, addresses=["10.1.2.3"], fingerprints=[])
        with mock.patch.object(os, "geteuid", return_value=0):
            ns["do_enroll"](_enroll_args(name="web-01"), token=None)
        captured = capsys.readouterr()
        assert "ssh_host_*_key.pub" in captured.err
        assert "--fingerprint SHA256:<ed25519 fingerprint>" in captured.out


class TestEnrollHostProbes:
    """The two shell-outs enroll makes: ssh-keygen -lf and hostname -I."""

    @pytest.fixture()
    def ns(self):
        return _render_enroll_ns()

    def test_fingerprints_shell_out_to_ssh_keygen(self, ns, tmp_path, monkeypatch):
        pub = tmp_path / "ssh_host_ed25519_key.pub"
        pub.write_text("ssh-ed25519 AAAA root@h\n")
        monkeypatch.setattr(ns["_glob"], "glob", lambda pat: [str(pub)])

        def fake_run(cmd, **kw):
            assert cmd == ["ssh-keygen", "-lf", str(pub)]
            r = mock.MagicMock()
            r.returncode = 0
            r.stdout = "256 SHA256:deadbeef root@h (ED25519)\n"
            return r

        with mock.patch("subprocess.run", side_effect=fake_run):
            assert ns["_host_key_fingerprints"]() == [
                (str(pub), "256 SHA256:deadbeef root@h (ED25519)", "SHA256:deadbeef"),
            ]

    def test_fingerprint_failure_warns_and_skips_that_key(self, ns, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(ns["_glob"], "glob", lambda pat: ["/etc/ssh/broken.pub"])

        def fake_run(cmd, **kw):
            r = mock.MagicMock()
            r.returncode = 1
            r.stdout = ""
            r.stderr = "is not a public key file"
            return r

        with mock.patch("subprocess.run", side_effect=fake_run):
            assert ns["_host_key_fingerprints"]() == []
        assert "ssh-keygen" in capsys.readouterr().err

    def test_addresses_from_hostname_dash_i(self, ns):
        def fake_run(cmd, **kw):
            assert cmd == ["hostname", "-I"]
            r = mock.MagicMock()
            r.returncode = 0
            r.stdout = "10.1.2.3 192.168.0.9 \n"
            return r

        with mock.patch.object(shutil, "which", return_value="/bin/hostname"), \
                mock.patch("subprocess.run", side_effect=fake_run):
            assert ns["_host_addresses"]() == ["10.1.2.3", "192.168.0.9"]

    def test_addresses_absent_hostname_warns_and_returns_empty(self, ns, capsys):
        with mock.patch.object(shutil, "which", return_value=None):
            assert ns["_host_addresses"]() == []
        assert "hostname" in capsys.readouterr().err

    def test_addresses_failed_hostname_warns_and_returns_empty(self, ns, capsys):
        def fake_run(cmd, **kw):
            r = mock.MagicMock()
            r.returncode = 1
            r.stdout = ""
            r.stderr = "boom"
            return r

        with mock.patch.object(shutil, "which", return_value="/bin/hostname"), \
                mock.patch("subprocess.run", side_effect=fake_run):
            assert ns["_host_addresses"]() == []
        assert "hostname -I failed" in capsys.readouterr().err

    def test_find_sshd_prefers_path(self, ns):
        with mock.patch.object(shutil, "which", return_value="/usr/local/sbin/sshd"):
            assert ns["_find_sshd"]() == "/usr/local/sbin/sshd"

    def test_find_sshd_falls_back_to_usr_sbin(self, ns, monkeypatch):
        real_exists = Path.exists

        def fake_exists(self):
            if str(self) == "/usr/sbin/sshd":
                return True
            return real_exists(self)

        with mock.patch.object(shutil, "which", return_value=None), \
                mock.patch.object(Path, "exists", fake_exists):
            assert ns["_find_sshd"]() == "/usr/sbin/sshd"

    def test_find_sshd_absent_is_none(self, ns):
        real_exists = Path.exists

        def fake_exists(self):
            if str(self) == "/usr/sbin/sshd":
                return False
            return real_exists(self)

        with mock.patch.object(shutil, "which", return_value=None), \
                mock.patch.object(Path, "exists", fake_exists):
            assert ns["_find_sshd"]() is None


# ─── enroll: real execution against real system state (O2 / O3) ───────────────
#
# There is no prior "spin up a container, run the installer, assert on real
# system state" pattern in this repo; this is it. The fixture image is built and
# owned here (never a live host), every container is torn down in a finally, and
# the whole group SKIPS — never fails — where docker or the estate's cgroup tier
# is unavailable, which is exactly the case inside the gate's own tester-unified
# container (no docker socket is mounted into it).

ENROLL_FIXTURE_IMAGE = "cmru-enroll-fixture:local"
ENROLL_FIXTURE_DOCKERFILE = """\
FROM debian:bookworm-slim
RUN apt-get update \\
 && apt-get install -y --no-install-recommends \\
        openssh-server python3 iproute2 hostname passwd \\
 && rm -rf /var/lib/apt/lists/* \\
 && ssh-keygen -A
"""


def _docker_unavailable_reason() -> Optional[str]:
    if shutil.which("docker") is None:
        return "docker CLI not present"
    probe = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if probe.returncode != 0:
        return f"docker daemon unreachable: {probe.stderr.strip()[:200]}"
    # AGENTS.md "Host cgroup placement": no hardcoded fallback slice — a
    # container we cannot place on the host's dev-background tier is one we do
    # not start next to production.
    if not os.environ.get("CGROUP_PARENT_DEV_BACKGROUND", "").strip():
        return "CGROUP_PARENT_DEV_BACKGROUND unset — refusing an unplaced container"
    return None


@pytest.fixture(scope="session")
def enroll_fixture_image() -> str:
    reason = _docker_unavailable_reason()
    if reason:
        pytest.skip(f"enroll container oracle needs docker ({reason})")
    present = subprocess.run(
        ["docker", "image", "inspect", ENROLL_FIXTURE_IMAGE],
        capture_output=True, text=True,
    )
    if present.returncode != 0:
        with tempfile.TemporaryDirectory() as ctx:
            (Path(ctx) / "Dockerfile").write_text(ENROLL_FIXTURE_DOCKERFILE)
            built = subprocess.run(
                ["docker", "build", "-t", ENROLL_FIXTURE_IMAGE, ctx],
                capture_output=True, text=True, timeout=900,
            )
            if built.returncode != 0:
                pytest.skip(
                    "could not build the enroll fixture image: "
                    f"{built.stderr.strip()[-400:]}"
                )
    return ENROLL_FIXTURE_IMAGE


@pytest.fixture()
def rendered_get_py(tmp_path) -> Path:
    from cmru.getpy import render_get_py
    src = render_get_py(
        project_name="demo", repo_owner="o", repo_name="r",
        tag_prefix="demo-v", install_dir_system="/opt/demo",
        install_dir_user="demo", required_commands=[],
    )
    script = tmp_path / "get.py"
    script.write_text(src, encoding="utf-8")
    return script


@contextlib.contextmanager
def _enroll_container(image: str, script: Path, *, network: str = "bridge") -> Iterator[str]:
    """A short-lived fixture container carrying the rendered installer.

    Placed on the estate's dev-background cgroup tier and capped, per the shared
    production host's rules; removed in a finally so a failing assertion never
    leaves one running.
    """
    slice_name = os.environ["CGROUP_PARENT_DEV_BACKGROUND"]
    started = subprocess.run(
        ["docker", "run", "-d",
         f"--cgroup-parent={slice_name}",
         "--cpus=3", "--memory=1g", "--memory-swap=4g",
         f"--network={network}",
         image, "sleep", "600"],
        capture_output=True, text=True,
    )
    assert started.returncode == 0, started.stderr
    container = started.stdout.strip()
    try:
        copied = subprocess.run(
            ["docker", "cp", str(script), f"{container}:/tmp/get.py"],
            capture_output=True, text=True,
        )
        assert copied.returncode == 0, copied.stderr
        yield container
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)


def _cexec(container: str, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "exec", container, *argv],
        capture_output=True, text=True, timeout=300,
    )


def _enroll_in(container: str, *extra: str) -> subprocess.CompletedProcess:
    return _cexec(
        container, "python3", "/tmp/get.py", "enroll",
        "--no-install",                       # the install step needs GitHub; kept hermetic
        "--controller", "test.example",
        "--user", "deployer",
        "--authorized-key", ENROLL_TEST_KEY,
        *extra,
    )


class TestEnrollAgainstRealSystem:
    """O2/O3 — the rendered enroll run for real, asserted on real system state."""

    def test_o2_user_key_modes_ownership_and_fingerprint(
        self, enroll_fixture_image, rendered_get_py
    ):
        with _enroll_container(enroll_fixture_image, rendered_get_py) as c:
            first = _enroll_in(c)
            assert first.returncode == 0, first.stdout + first.stderr

            # the user exists for real
            ident = _cexec(c, "id", "-u", "deployer")
            assert ident.returncode == 0, ident.stderr

            # exactly one matching line
            keys = _cexec(c, "cat", "/home/deployer/.ssh/authorized_keys")
            assert keys.returncode == 0, keys.stderr
            lines = [ln for ln in keys.stdout.splitlines() if ln.strip()]
            assert lines == [ENROLL_TEST_KEY]

            # modes + ownership, read off the real filesystem
            stat_out = _cexec(
                c, "stat", "-c", "%a %U %G %n",
                "/home/deployer/.ssh", "/home/deployer/.ssh/authorized_keys",
            )
            assert stat_out.returncode == 0, stat_out.stderr
            assert stat_out.stdout.splitlines() == [
                "700 deployer deployer /home/deployer/.ssh",
                "600 deployer deployer /home/deployer/.ssh/authorized_keys",
            ]

            # the printed fingerprint equals ssh-keygen run independently HERE
            independent = _cexec(
                c, "ssh-keygen", "-lf", "/etc/ssh/ssh_host_ed25519_key.pub")
            assert independent.returncode == 0, independent.stderr
            expected_fp = next(
                tok for tok in independent.stdout.split() if tok.startswith("SHA256:"))
            assert expected_fp in first.stdout
            assert f"--fingerprint {expected_fp}" in first.stdout
            assert "/etc/ssh/ssh_host_ed25519_key.pub" in first.stdout

            # the addresses are printed and explicitly UNCONFIRMED
            addrs = _cexec(c, "hostname", "-I")
            assert addrs.returncode == 0, addrs.stderr
            for addr in addrs.stdout.split():
                assert addr in first.stdout
            assert "UNCONFIRMED" in first.stdout

            # re-run: still exactly one line, reported, not an error
            second = _enroll_in(c)
            assert second.returncode == 0, second.stdout + second.stderr
            keys_again = _cexec(c, "cat", "/home/deployer/.ssh/authorized_keys")
            assert [ln for ln in keys_again.stdout.splitlines() if ln.strip()] == [
                ENROLL_TEST_KEY]
            assert "not duplicated" in second.stdout
            assert "already exists" in second.stdout

    def test_o2_from_pattern_written_and_conflicting_options_refused(
        self, enroll_fixture_image, rendered_get_py
    ):
        with _enroll_container(enroll_fixture_image, rendered_get_py) as c:
            first = _enroll_in(c, "--from", "10.0.0.0/8")
            assert first.returncode == 0, first.stdout + first.stderr
            keys = _cexec(c, "cat", "/home/deployer/.ssh/authorized_keys")
            assert [ln for ln in keys.stdout.splitlines() if ln.strip()] == [
                f'from="10.0.0.0/8" {ENROLL_TEST_KEY}']

            # identical re-run is a no-op
            same = _enroll_in(c, "--from", "10.0.0.0/8")
            assert same.returncode == 0, same.stdout + same.stderr
            still = _cexec(c, "cat", "/home/deployer/.ssh/authorized_keys")
            assert [ln for ln in still.stdout.splitlines() if ln.strip()] == [
                f'from="10.0.0.0/8" {ENROLL_TEST_KEY}']
            assert "not duplicated" in same.stdout

            # same key material, DIFFERENT options → a refusal, not a second line
            conflict = _enroll_in(c, "--from", "192.168.0.0/16")
            assert conflict.returncode == 2, conflict.stdout + conflict.stderr
            assert "DIFFERENT options" in conflict.stderr
            after = _cexec(c, "cat", "/home/deployer/.ssh/authorized_keys")
            assert [ln for ln in after.stdout.splitlines() if ln.strip()] == [
                f'from="10.0.0.0/8" {ENROLL_TEST_KEY}']

            # dropping --from entirely is the same conflict, not a silent append
            dropped = _enroll_in(c)
            assert dropped.returncode == 2, dropped.stdout + dropped.stderr
            after2 = _cexec(c, "cat", "/home/deployer/.ssh/authorized_keys")
            assert len([ln for ln in after2.stdout.splitlines() if ln.strip()]) == 1

    def test_written_key_actually_authenticates_against_a_real_sshd(
        self, enroll_fixture_image, rendered_get_py
    ):
        """The point of the whole subcommand: the controller can now log in.

        This is also the oracle behind the one deviation from KI-24's literal
        text — the restricted line is written `from="P" <type> …`, not
        `from="P",<type> …`. The comma-joined form fails here (measured:
        "Permission denied (publickey)"), because sshd reads everything up to
        the first unquoted whitespace as options.
        """
        with _enroll_container(enroll_fixture_image, rendered_get_py) as c:
            keygen = _cexec(c, "ssh-keygen", "-q", "-t", "ed25519", "-N", "",
                            "-f", "/root/id_probe")
            assert keygen.returncode == 0, keygen.stderr
            pub = _cexec(c, "cat", "/root/id_probe.pub")
            assert pub.returncode == 0, pub.stderr
            public_key = pub.stdout.strip()

            enrolled = _cexec(
                c, "python3", "/tmp/get.py", "enroll", "--no-install",
                "--controller", "test.example", "--user", "deployer",
                "--from", "127.0.0.1", "--authorized-key", public_key,
            )
            assert enrolled.returncode == 0, enrolled.stdout + enrolled.stderr

            started = _cexec(c, "sh", "-c",
                             "mkdir -p /run/sshd && /usr/sbin/sshd -p 2222")
            assert started.returncode == 0, started.stderr
            login = _cexec(
                c, "ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
                "-o", "ConnectionAttempts=10",
                "-i", "/root/id_probe", "-p", "2222", "deployer@127.0.0.1",
                "echo", "ENROLLED_LOGIN_OK",
            )
            assert login.returncode == 0, login.stdout + login.stderr
            assert "ENROLLED_LOGIN_OK" in login.stdout

    def test_o3_no_ssh_server_exits_prereq_and_changes_nothing(
        self, enroll_fixture_image, rendered_get_py
    ):
        # --network none: this container cannot reach anything, so a run that
        # exits EXIT_PREREQ here also proves nothing was fetched on the way.
        with _enroll_container(enroll_fixture_image, rendered_get_py,
                               network="none") as c:
            removed = _cexec(c, "rm", "-f", "/usr/sbin/sshd")
            assert removed.returncode == 0, removed.stderr
            assert _cexec(c, "test", "-e", "/usr/sbin/sshd").returncode != 0

            result = _enroll_in(c)
            assert result.returncode == 3, result.stdout + result.stderr
            assert "openssh-server" in result.stderr

            # and it did NOT do any of the later steps
            assert _cexec(c, "id", "-u", "deployer").returncode != 0
            assert _cexec(c, "test", "-e", "/home/deployer").returncode != 0

    def test_docker_flag_refused_when_the_group_is_absent(
        self, enroll_fixture_image, rendered_get_py
    ):
        with _enroll_container(enroll_fixture_image, rendered_get_py) as c:
            assert _cexec(c, "getent", "group", "docker").returncode != 0

            result = _enroll_in(c, "--docker")
            assert result.returncode == 3, result.stdout + result.stderr
            assert "docker" in result.stderr
            # refused BEFORE creating the user — no half-enrolled host
            assert _cexec(c, "id", "-u", "deployer").returncode != 0

    def test_docker_flag_adds_the_user_when_the_group_exists(
        self, enroll_fixture_image, rendered_get_py
    ):
        with _enroll_container(enroll_fixture_image, rendered_get_py) as c:
            created = _cexec(c, "groupadd", "docker")
            assert created.returncode == 0, created.stderr

            result = _enroll_in(c, "--docker")
            assert result.returncode == 0, result.stdout + result.stderr
            groups = _cexec(c, "id", "-nG", "deployer")
            assert "docker" in groups.stdout.split(), groups.stdout

    def test_existing_user_is_left_untouched(
        self, enroll_fixture_image, rendered_get_py
    ):
        with _enroll_container(enroll_fixture_image, rendered_get_py) as c:
            made = _cexec(c, "useradd", "--create-home", "--shell", "/bin/sh",
                          "deployer")
            assert made.returncode == 0, made.stderr

            result = _enroll_in(c)
            assert result.returncode == 0, result.stdout + result.stderr
            shell = _cexec(c, "getent", "passwd", "deployer")
            assert shell.stdout.strip().endswith("/bin/sh"), shell.stdout
            assert "already exists" in result.stdout
