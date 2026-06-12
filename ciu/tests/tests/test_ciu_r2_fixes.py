"""Regression tests for the R2 adversarial-review fixes (F1–F6).

Each test names the finding and the spec ID it protects.
"""
from __future__ import annotations

import io
import os
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest

from ciu.composefile import SecretLeakError, leak_scan
from ciu.secrets.directives import SecretSpec, parse_value
from ciu.secrets.materialize import materialize
from ciu.secrets.providers import VaultError, VaultKV2


def _spec(name: str, kind: str, locator: str | None, **kw) -> SecretSpec:
    return SecretSpec(name=name, kind=kind, locator=locator, table_path="app.secrets", **kw)


class _Mat:
    def __init__(self, spec, value, file):
        self.spec, self.value, self.file = spec, value, file


class TestF1VaultWriteErrorBody:
    """F1/S4.23 — write-path HTTP error must not echo the response body."""

    def _http_error(self, url: str, body: bytes) -> urllib.error.HTTPError:
        return urllib.error.HTTPError(url, 400, "Bad Request", {}, io.BytesIO(body))

    def test_write_error_omits_body(self, monkeypatch):
        secret_value = "super-secret-token-value-12345"
        body = f'{{"errors": ["bad payload: {secret_value}"]}}'.encode()

        def fake_urlopen(req, timeout=None):
            raise self._http_error(req.full_url, body)

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        client = VaultKV2("http://vault:8200", "tok")
        with pytest.raises(VaultError) as exc_info:
            client.write("db/pass", secret_value)
        assert secret_value not in str(exc_info.value)
        assert "HTTP 400" in str(exc_info.value)

    def test_read_error_keeps_detail(self, monkeypatch):
        body = b'{"errors": ["permission denied"]}'

        def fake_urlopen(req, timeout=None):
            raise self._http_error(req.full_url, body)

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        client = VaultKV2("http://vault:8200", "tok")
        with pytest.raises(VaultError) as exc_info:
            client.read("db/pass")
        assert "permission denied" in str(exc_info.value)


class TestF2StoreDirTreeMode:
    """F2/S4.10 — every store-dir level is 0700, not only the leaf parent."""

    def test_namespaced_gen_local_dirs_0700(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CONTAINER_UID", raising=False)
        monkeypatch.delenv("DOCKER_GID", raising=False)
        stack = tmp_path / "stack"
        stack.mkdir()
        spec = _spec("pw", "GEN_LOCAL", "group/sub/pw")
        materialize(
            [spec], stack_dir=stack, repo_root=tmp_path,
            vault=None, assume_yes=True,
        )
        store = tmp_path / ".ciu" / "secrets"
        for d in (store, store / "group", store / "group" / "sub"):
            assert (d.stat().st_mode & 0o777) == 0o700, d


class TestF3ReuseReappliesPerms:
    """F3/S4.10 — mode changes take effect on pre-existing store files."""

    def test_gen_local_reuse_reapplies_mode(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CONTAINER_UID", raising=False)
        monkeypatch.delenv("DOCKER_GID", raising=False)
        stack = tmp_path / "stack"
        stack.mkdir()
        spec1 = _spec("pw", "GEN_LOCAL", "pw")
        out1 = materialize([spec1], stack_dir=stack, repo_root=tmp_path,
                           vault=None, assume_yes=True)
        f = out1["pw"].file
        assert (f.stat().st_mode & 0o777) == 0o440

        spec2 = _spec("pw", "GEN_LOCAL", "pw", mode="0400")
        out2 = materialize([spec2], stack_dir=stack, repo_root=tmp_path,
                           vault=None, assume_yes=True)
        assert out2["pw"].value == out1["pw"].value  # idempotent value
        assert (f.stat().st_mode & 0o777) == 0o400   # but mode updated


class TestF5AskFileExposeEnv:
    """F5/S4.19 — expose_env on ASK_FILE is rejected at parse time."""

    def test_rejected(self):
        with pytest.raises(ValueError, match=r"\[S4\.19\]"):
            parse_value(
                "cert",
                {"directive": "ASK_FILE:certs/x.pem", "expose_env": "CERT"},
                "app.secrets",
            )


class TestF6FoldedLeak:
    """F6/S4.22 — value split across a YAML fold is still caught."""

    def test_folded_value_detected(self, tmp_path):
        value = "AAAABBBBCCCCDDDDEEEE"
        folded = f"key: >\n  {value[:10]}\n  {value[10:]}\n"
        spec = _spec("pw", "GEN_LOCAL", "pw")
        with pytest.raises(SecretLeakError) as exc_info:
            leak_scan(folded, {"pw": _Mat(spec, value, tmp_path / "f")})
        assert value not in str(exc_info.value)

    def test_value_with_whitespace_not_collapse_matched(self, tmp_path):
        # A value containing whitespace is only raw-scanned (no false positive
        # from the collapsed haystack).
        value = "pass word with spaces"
        text = "passwordwithspaces and other text"
        spec = _spec("pw", "GEN_LOCAL", "pw")
        leak_scan(text, {"pw": _Mat(spec, value, tmp_path / "f")})  # no raise
