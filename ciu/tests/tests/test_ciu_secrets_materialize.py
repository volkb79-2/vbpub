"""Tests for ciu.secrets.materialize and ciu.secrets.providers (CIU v2 P4).

Normative contract: docs/SPEC.md §S4 (S4.8–S4.16, S4.24–S4.26), S1.6, S2.5,
S6.5. Each test names the spec ID it pins.

All filesystem fixtures use tmp_path; env vars use monkeypatch.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.secrets.directives import parse_value  # noqa: E402
from ciu.secrets.materialize import (  # noqa: E402
    MaterializedSecret,
    list_secrets,
    materialize,
    project_store,
    reset_secrets,
    stack_store,
)
from ciu.secrets.providers import (  # noqa: E402
    VaultError,
    VaultKV2,
    resolve_vault_token,
    vault_addr_from_config,
)


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------

def _spec(name: str, directive: str):
    """Parse one directive string into a SecretSpec (table path is cosmetic)."""
    return parse_value(name, directive, "mystack.secrets")


class FakeVault(VaultKV2):
    """In-memory VaultKV2 that exercises the REAL read/write extraction logic.

    We override only the low-level ``_request`` transport so that
    ``VaultKV2.read`` / ``VaultKV2.write`` (the S4.15 logic under test) run
    unmodified against an in-memory KV2 store.
    """

    def __init__(self):  # noqa: D107 — no real addr/token needed
        super().__init__(addr="http://vault.test:8200", token="t", timeout=1)
        # path -> data map (the inner KV2 'data.data' dict)
        self.store: dict[str, dict] = {}

    def _request(self, method, path, payload=None):  # type: ignore[override]
        key = path.lstrip("/")
        if method == "GET":
            if key not in self.store:
                return 404, ""
            body = json.dumps({"data": {"data": self.store[key]}})
            return 200, body
        if method == "POST":
            # KV2 write payload is {"data": {...}}; store the inner map.
            self.store[key] = dict(payload["data"])
            return 200, json.dumps({"data": {"version": 1}})
        raise AssertionError(f"unexpected method {method}")

    def seed(self, path: str, data: dict) -> None:
        self.store[path.lstrip("/")] = dict(data)


def _materialize(specs, *, stack_dir, repo_root, **kw):
    """materialize() with a no-op chown so tests never touch real ownership."""
    kw.setdefault("vault", None)
    kw.setdefault("assume_yes", True)
    kw.setdefault("chown_fn", lambda *a, **k: None)
    return materialize(specs, stack_dir=stack_dir, repo_root=repo_root, **kw)


@pytest.fixture()
def dirs(tmp_path: Path):
    """A stack dir and repo root under tmp_path."""
    repo_root = tmp_path / "repo"
    stack_dir = repo_root / "infra" / "thing"
    stack_dir.mkdir(parents=True)
    return stack_dir, repo_root


# ---------------------------------------------------------------------------
# GEN_LOCAL — S4.8 / S4.9 / S4.11
# ---------------------------------------------------------------------------

class TestGenLocal:
    def test_byte_identical_across_runs_s4_11(self, dirs):
        """S4.11 — two materialize() runs are byte-identical for GEN_LOCAL."""
        stack_dir, repo_root = dirs
        specs = [_spec("token", "GEN_LOCAL:shared/token")]

        first = _materialize(specs, stack_dir=stack_dir, repo_root=repo_root)
        v1 = first["token"].value
        second = _materialize(specs, stack_dir=stack_dir, repo_root=repo_root)
        v2 = second["token"].value

        assert v1 == v2
        assert v1 and len(v1) > 10

    def test_file_in_project_store_s4_9(self, dirs):
        """S4.9 — GEN_LOCAL writes to the PROJECT store, not the stack store."""
        stack_dir, repo_root = dirs
        specs = [_spec("token", "GEN_LOCAL:registry_password")]

        res = _materialize(specs, stack_dir=stack_dir, repo_root=repo_root)
        f = res["token"].file

        assert f == project_store(repo_root) / "registry_password"
        assert f.exists()
        # NOT under the per-stack store
        assert stack_store(stack_dir) not in f.parents

    def test_name_with_slash_nests_dirs_s4_9(self, dirs):
        """S4.9 — a GEN_LOCAL name containing '/' nests subdirectories."""
        stack_dir, repo_root = dirs
        specs = [_spec("nested", "GEN_LOCAL:group/sub/secret_name")]

        res = _materialize(specs, stack_dir=stack_dir, repo_root=repo_root)
        f = res["nested"].file

        assert f == project_store(repo_root) / "group" / "sub" / "secret_name"
        assert f.is_file()


# ---------------------------------------------------------------------------
# GEN_TO_VAULT — S4.11 / S4.12
# ---------------------------------------------------------------------------

class TestGenToVault:
    def test_first_run_generates_and_writes_both_s4_11(self, dirs):
        """S4.11 — first run generates, writes to Vault AND the store file."""
        stack_dir, repo_root = dirs
        vault = FakeVault()
        specs = [_spec("api_key", "GEN_TO_VAULT:apps/api_key")]

        res = _materialize(specs, stack_dir=stack_dir, repo_root=repo_root, vault=vault)
        value = res["api_key"].value

        # Written to fake vault under the canonical 'value' key.
        assert vault.store["apps/api_key"] == {"value": value}
        # Written to the per-stack store file with no trailing newline.
        f = res["api_key"].file
        assert f == stack_store(stack_dir) / "api_key"
        assert f.read_bytes() == value.encode("utf-8")

    def test_second_run_idempotent_s4_11(self, dirs):
        """S4.11 — second run returns the Vault value unchanged."""
        stack_dir, repo_root = dirs
        vault = FakeVault()
        specs = [_spec("api_key", "GEN_TO_VAULT:apps/api_key")]

        first = _materialize(specs, stack_dir=stack_dir, repo_root=repo_root, vault=vault)
        second = _materialize(specs, stack_dir=stack_dir, repo_root=repo_root, vault=vault)

        assert first["api_key"].value == second["api_key"].value

    def test_store_refreshed_when_vault_changed_externally_s4_12(self, dirs):
        """S4.12 — store file is refreshed from the provider each run."""
        stack_dir, repo_root = dirs
        vault = FakeVault()
        specs = [_spec("api_key", "GEN_TO_VAULT:apps/api_key")]

        res1 = _materialize(specs, stack_dir=stack_dir, repo_root=repo_root, vault=vault)
        store_file = res1["api_key"].file

        # Rotate in the provider out-of-band.
        vault.store["apps/api_key"] = {"value": "rotated-value-xyz"}

        res2 = _materialize(specs, stack_dir=stack_dir, repo_root=repo_root, vault=vault)
        assert res2["api_key"].value == "rotated-value-xyz"
        assert store_file.read_bytes() == b"rotated-value-xyz"

    def test_vault_none_with_vault_spec_aborts_s4_16(self, dirs):
        """S4.16 — vault-backed spec but no Vault → abort before any work."""
        stack_dir, repo_root = dirs
        specs = [_spec("api_key", "GEN_TO_VAULT:apps/api_key")]

        with pytest.raises(VaultError, match=r"\[S4.16\]"):
            _materialize(specs, stack_dir=stack_dir, repo_root=repo_root, vault=None)


# ---------------------------------------------------------------------------
# ASK_VAULT — S4.2 / S4.15
# ---------------------------------------------------------------------------

class TestAskVault:
    def test_missing_aborts_s4_2(self, dirs):
        """S4.2 — ASK_VAULT must exist; absent path aborts."""
        stack_dir, repo_root = dirs
        vault = FakeVault()  # empty store
        specs = [_spec("db_pw", "ASK_VAULT:secret/db")]

        with pytest.raises(VaultError, match=r"\[S4.2\].*must exist"):
            _materialize(specs, stack_dir=stack_dir, repo_root=repo_root, vault=vault)

    def test_field_extraction_s4_15(self, dirs):
        """S4.15 — '#field' selects the named key from a multi-key payload."""
        stack_dir, repo_root = dirs
        vault = FakeVault()
        vault.seed("secret/db", {"username": "u", "password": "p"})
        specs = [_spec("db_pw", "ASK_VAULT:secret/db#password")]

        res = _materialize(specs, stack_dir=stack_dir, repo_root=repo_root, vault=vault)
        assert res["db_pw"].value == "p"

    def test_value_key_wins_s4_15(self, dirs):
        """S4.15 — the 'value' key wins over other keys with no selector."""
        stack_dir, repo_root = dirs
        vault = FakeVault()
        vault.seed("secret/x", {"value": "canonical", "other": "ignored"})
        specs = [_spec("x", "ASK_VAULT:secret/x")]

        res = _materialize(specs, stack_dir=stack_dir, repo_root=repo_root, vault=vault)
        assert res["x"].value == "canonical"

    def test_single_key_payload_s4_15(self, dirs):
        """S4.15 — a single-key payload's sole value is used (no 'value' key)."""
        stack_dir, repo_root = dirs
        vault = FakeVault()
        vault.seed("secret/solo", {"password": "only-one"})
        specs = [_spec("solo", "ASK_VAULT:secret/solo")]

        res = _materialize(specs, stack_dir=stack_dir, repo_root=repo_root, vault=vault)
        assert res["solo"].value == "only-one"

    def test_multi_key_no_value_no_field_errors_listing_keys_s4_15(self, dirs):
        """S4.15 — ambiguous multi-key payload aborts listing the keys."""
        stack_dir, repo_root = dirs
        vault = FakeVault()
        vault.seed("secret/multi", {"alpha": "1", "beta": "2"})
        specs = [_spec("multi", "ASK_VAULT:secret/multi")]

        with pytest.raises(VaultError) as exc:
            _materialize(specs, stack_dir=stack_dir, repo_root=repo_root, vault=vault)
        msg = str(exc.value)
        assert "[S4.15]" in msg
        assert "alpha" in msg and "beta" in msg
        # suggests #<field>
        assert "#" in msg

    def test_field_absent_in_payload_errors_s4_15(self, dirs):
        """S4.15 — a given field absent from the payload aborts."""
        stack_dir, repo_root = dirs
        vault = FakeVault()
        vault.seed("secret/db", {"username": "u", "password": "p"})
        specs = [_spec("db_pw", "ASK_VAULT:secret/db#nope")]

        with pytest.raises(VaultError, match=r"\[S4.15\].*no field 'nope'"):
            _materialize(specs, stack_dir=stack_dir, repo_root=repo_root, vault=vault)


# ---------------------------------------------------------------------------
# VaultKV2.read non-JSON body — S4.15
# ---------------------------------------------------------------------------

class TestVaultReadNonJson:
    def test_non_json_body_raises_clean_error_s4_15(self):
        """S4.15 — an HTML/non-JSON body yields a clean VaultError, not a traceback."""
        v = VaultKV2(addr="http://vault.test:8200", token="t")
        # Stub _request to return a 200 with an HTML body.
        v._request = lambda method, path, payload=None: (200, "<html>oops</html>")  # type: ignore
        with pytest.raises(VaultError, match="non-JSON response"):
            v.read("secret/whatever")


# ---------------------------------------------------------------------------
# VaultKV2.write payload — S4.15
# ---------------------------------------------------------------------------

class TestVaultWritePayload:
    def test_write_payload_is_exactly_value_s4_15(self):
        """S4.15 — write POSTs exactly {"data": {"value": v}}, no alias keys."""
        v = VaultKV2(addr="http://vault.test:8200", token="t")
        captured = {}

        def _capture(method, path, payload=None):
            captured["method"] = method
            captured["path"] = path
            captured["payload"] = payload
            return 200, json.dumps({"data": {"version": 1}})

        v._request = _capture  # type: ignore
        v.write("apps/key", "s3cr3t")

        assert captured["method"] == "POST"
        assert captured["payload"] == {"data": {"value": "s3cr3t"}}


# ---------------------------------------------------------------------------
# ASK_EXTERNAL — S4.13
# ---------------------------------------------------------------------------

class TestAskExternal:
    def test_env_locator_wins_over_store_file_s4_13(self, dirs, monkeypatch):
        """S4.13 — env[<key>] wins even when a cached store file exists."""
        stack_dir, repo_root = dirs
        spec = _spec("ext", "ASK_EXTERNAL:MY_TOKEN")
        # Pre-seed a stale store file.
        store_file = stack_store(stack_dir) / "ext"
        store_file.parent.mkdir(parents=True, exist_ok=True)
        store_file.write_bytes(b"stale-cached")

        monkeypatch.setenv("MY_TOKEN", "fresh-from-env")
        res = _materialize([spec], stack_dir=stack_dir, repo_root=repo_root,
                           env=os.environ)
        assert res["ext"].value == "fresh-from-env"
        # store file refreshed to the env value
        assert store_file.read_bytes() == b"fresh-from-env"

    def test_ciu_secret_name_fallback_s4_13(self, dirs, monkeypatch):
        """S4.13 — CIU_SECRET_<NAME> is used when the locator env is unset."""
        stack_dir, repo_root = dirs
        spec = _spec("shared_token", "ASK_EXTERNAL:SOME_KEY")
        monkeypatch.delenv("SOME_KEY", raising=False)
        monkeypatch.setenv("CIU_SECRET_SHARED_TOKEN", "via-ciu-secret")

        res = _materialize([spec], stack_dir=stack_dir, repo_root=repo_root,
                           env=os.environ)
        assert res["shared_token"].value == "via-ciu-secret"

    def test_cached_store_file_reused_no_prompt_s4_13(self, dirs, monkeypatch):
        """S4.13 — a cached store file is reused with no prompt on later runs."""
        stack_dir, repo_root = dirs
        spec = _spec("ext", "ASK_EXTERNAL:MISSING_KEY")
        monkeypatch.delenv("MISSING_KEY", raising=False)
        monkeypatch.delenv("CIU_SECRET_EXT", raising=False)
        store_file = stack_store(stack_dir) / "ext"
        store_file.parent.mkdir(parents=True, exist_ok=True)
        store_file.write_bytes(b"cached-value")

        def _boom(_prompt):
            raise AssertionError("prompt must not be called when cached")

        res = _materialize([spec], stack_dir=stack_dir, repo_root=repo_root,
                           env=os.environ, prompt_fn=_boom)
        assert res["ext"].value == "cached-value"

    def test_non_interactive_missing_aborts_s4_13(self, dirs, monkeypatch):
        """S4.13 — non-interactive (-y) with no value aborts."""
        stack_dir, repo_root = dirs
        spec = _spec("ext", "ASK_EXTERNAL:NOPE_KEY")
        monkeypatch.delenv("NOPE_KEY", raising=False)
        monkeypatch.delenv("CIU_SECRET_EXT", raising=False)

        with pytest.raises(ValueError, match=r"\[S4.13\]"):
            _materialize([spec], stack_dir=stack_dir, repo_root=repo_root,
                         assume_yes=True, env=os.environ)

    def test_prompt_path_persists_s4_13(self, dirs, monkeypatch):
        """S4.13 — a prompted value persists to the store file."""
        stack_dir, repo_root = dirs
        spec = _spec("ext", "ASK_EXTERNAL:PROMPT_KEY")
        monkeypatch.delenv("PROMPT_KEY", raising=False)
        monkeypatch.delenv("CIU_SECRET_EXT", raising=False)
        # Force interactive: assume_yes=False and stdin.isatty() -> True.
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

        res = _materialize([spec], stack_dir=stack_dir, repo_root=repo_root,
                           assume_yes=False, env=os.environ,
                           prompt_fn=lambda _p: "typed-secret")
        assert res["ext"].value == "typed-secret"
        store_file = stack_store(stack_dir) / "ext"
        assert store_file.read_bytes() == b"typed-secret"


# ---------------------------------------------------------------------------
# ASK_FILE — S4.14
# ---------------------------------------------------------------------------

class TestAskFile:
    def test_in_place_no_copy_s4_14(self, dirs):
        """S4.14 — ASK_FILE references the file in place; no copy into .ciu, value None."""
        stack_dir, repo_root = dirs
        src = stack_dir / "tls" / "cert.pem"
        src.parent.mkdir(parents=True)
        src.write_text("CERTDATA")
        spec = _spec("cert", "ASK_FILE:tls/cert.pem")

        res = _materialize([spec], stack_dir=stack_dir, repo_root=repo_root)
        m = res["cert"]
        assert m.file == src
        assert m.value is None
        # No copy into the store.
        assert not (stack_store(stack_dir) / "cert").exists()

    def test_missing_aborts_s4_14(self, dirs):
        """S4.14 — a missing/unreadable ASK_FILE path aborts."""
        stack_dir, repo_root = dirs
        spec = _spec("cert", "ASK_FILE:tls/does_not_exist.pem")

        with pytest.raises(ValueError, match=r"\[S4.14\]"):
            _materialize([spec], stack_dir=stack_dir, repo_root=repo_root)


# ---------------------------------------------------------------------------
# GEN_EPHEMERAL — S4.2
# ---------------------------------------------------------------------------

class TestGenEphemeral:
    def test_differs_across_runs_s4_2(self, dirs):
        """S4.2 — GEN_EPHEMERAL produces a fresh value every run."""
        stack_dir, repo_root = dirs
        spec = _spec("session", "GEN_EPHEMERAL")

        v1 = _materialize([spec], stack_dir=stack_dir, repo_root=repo_root)["session"].value
        v2 = _materialize([spec], stack_dir=stack_dir, repo_root=repo_root)["session"].value
        assert v1 != v2
        # store file written with the latest value
        store_file = stack_store(stack_dir) / "session"
        assert store_file.read_bytes() == v2.encode("utf-8")

    def test_ephemeral_rejects_payload_upstream_s4_2(self):
        """S4.2 — the parser rejects GEN_EPHEMERAL with a payload (covered upstream)."""
        with pytest.raises(ValueError):
            parse_value("session", "GEN_EPHEMERAL:oops", "mystack.secrets")


# ---------------------------------------------------------------------------
# File modes / dir modes / atomicity — S4.9 / S4.10
# ---------------------------------------------------------------------------

class TestFileModesAndAtomicity:
    def test_default_file_mode_0440_s4_10(self, dirs):
        """S4.10 — secret files default to mode 0440."""
        stack_dir, repo_root = dirs
        spec = _spec("session", "GEN_EPHEMERAL")
        res = _materialize([spec], stack_dir=stack_dir, repo_root=repo_root)
        mode = stat.S_IMODE(res["session"].file.stat().st_mode)
        assert mode == 0o440

    def test_custom_mode_honoured_s4_10(self, dirs):
        """S4.10 — per-secret mode override is honoured."""
        stack_dir, repo_root = dirs
        spec = parse_value(
            "session", {"directive": "GEN_EPHEMERAL", "mode": "0400"}, "mystack.secrets"
        )
        res = _materialize([spec], stack_dir=stack_dir, repo_root=repo_root)
        mode = stat.S_IMODE(res["session"].file.stat().st_mode)
        assert mode == 0o400

    def test_store_dir_mode_0700_s4_10(self, dirs):
        """S4.10 — the store directory is mode 0700."""
        stack_dir, repo_root = dirs
        spec = _spec("session", "GEN_EPHEMERAL")
        _materialize([spec], stack_dir=stack_dir, repo_root=repo_root)
        d = stack_store(stack_dir)
        assert stat.S_IMODE(d.stat().st_mode) == 0o700

    def test_no_trailing_newline_s4_9(self, dirs):
        """S4.9 — the store file holds raw bytes with no trailing newline."""
        stack_dir, repo_root = dirs
        spec = _spec("session", "GEN_EPHEMERAL")
        res = _materialize([spec], stack_dir=stack_dir, repo_root=repo_root)
        raw = res["session"].file.read_bytes()
        assert not raw.endswith(b"\n")
        assert raw == res["session"].value.encode("utf-8")

    def test_atomic_via_os_replace_s4_9(self, dirs, monkeypatch):
        """S4.9 — store writes go through os.replace (atomic rename)."""
        import ciu.secrets.materialize as mat

        stack_dir, repo_root = dirs
        spec = _spec("session", "GEN_EPHEMERAL")

        calls = {"n": 0}
        real_replace = os.replace

        def _spy(src, dst):
            calls["n"] += 1
            return real_replace(src, dst)

        monkeypatch.setattr(mat.os, "replace", _spy)
        _materialize([spec], stack_dir=stack_dir, repo_root=repo_root)
        assert calls["n"] == 1


# ---------------------------------------------------------------------------
# chown degrade — S4.10 / S6.5
# ---------------------------------------------------------------------------

class TestChownDegrade:
    def test_permission_error_warns_once_and_continues_s4_10(self, dirs, monkeypatch):
        """S4.10 — chown PermissionError → one warning, run continues."""
        import ciu.secrets.materialize as mat

        stack_dir, repo_root = dirs
        # Two secrets so we can confirm exactly one warning per failing chown.
        specs = [_spec("a", "GEN_EPHEMERAL"), _spec("b", "GEN_EPHEMERAL")]

        # Provide UID/GID env so the default chown_fn actually attempts chown.
        monkeypatch.setenv("CONTAINER_UID", "0")
        monkeypatch.setenv("DOCKER_GID", "0")

        def _deny(*_a, **_k):
            raise PermissionError("not allowed")

        monkeypatch.setattr(mat.os, "chown", _deny)

        with pytest.warns(UserWarning, match=r"\[S4.10\]"):
            # Use the DEFAULT chown_fn (degrade path), not the no-op.
            res = materialize(
                specs,
                stack_dir=stack_dir,
                repo_root=repo_root,
                vault=None,
                assume_yes=True,
            )
        # Run completed: both files exist.
        assert res["a"].file.exists()
        assert res["b"].file.exists()

    def test_uid_gid_zero_is_valid_s2_5(self, dirs, monkeypatch):
        """S2.5 — CONTAINER_UID/DOCKER_GID of 0 are valid (not treated as unset)."""
        import ciu.secrets.materialize as mat

        stack_dir, repo_root = dirs
        spec = _spec("a", "GEN_EPHEMERAL")
        monkeypatch.setenv("CONTAINER_UID", "0")
        monkeypatch.setenv("DOCKER_GID", "0")

        seen = {}

        def _record_chown(path, uid, gid):
            seen["uid"] = uid
            seen["gid"] = gid

        materialize(
            [spec], stack_dir=stack_dir, repo_root=repo_root, vault=None,
            assume_yes=True, chown_fn=_record_chown,
        )
        assert seen["uid"] == 0
        assert seen["gid"] == 0

    def test_spec_uid_overrides_container_uid_s4_10(self, dirs, monkeypatch):
        """S4.10 — per-secret uid override beats CONTAINER_UID for ownership."""
        stack_dir, repo_root = dirs
        spec = parse_value(
            "a", {"directive": "GEN_EPHEMERAL", "uid": 999}, "mystack.secrets"
        )
        monkeypatch.setenv("CONTAINER_UID", "1000")
        monkeypatch.setenv("DOCKER_GID", "1000")

        seen = {}
        materialize(
            [spec], stack_dir=stack_dir, repo_root=repo_root, vault=None,
            assume_yes=True, chown_fn=lambda p, u, g: seen.update(uid=u, gid=g),
        )
        assert seen["uid"] == 999
        assert seen["gid"] == 1000


# ---------------------------------------------------------------------------
# resolve_vault_token order — S4.16
# ---------------------------------------------------------------------------

class TestResolveVaultToken:
    def test_env_beats_token_file_beats_state_s4_16(self, tmp_path, monkeypatch):
        """S4.16 — VAULT_TOKEN env wins over token_file and stack state."""
        repo_root = tmp_path
        # token_file present
        (repo_root / "tok").write_text("from-file\n")
        # vault stack state present
        vault_stack = repo_root / "infra" / "vault"
        vault_stack.mkdir(parents=True)
        (vault_stack / "ciu.toml").write_text(
            '[state]\nroot_token = "from-state"\n'
        )
        config = {"vault": {"token_file": "tok", "stack_path": "infra/vault"}}

        monkeypatch.setenv("VAULT_TOKEN", "from-env")
        assert resolve_vault_token(config, repo_root) == "from-env"

    def test_token_file_beats_state_s4_16(self, tmp_path, monkeypatch):
        """S4.16 — token_file wins over stack state when env is unset."""
        repo_root = tmp_path
        (repo_root / "tok").write_text("from-file\n")
        vault_stack = repo_root / "infra" / "vault"
        vault_stack.mkdir(parents=True)
        (vault_stack / "ciu.toml").write_text('[state]\nroot_token = "from-state"\n')
        config = {"vault": {"token_file": "tok"}}

        monkeypatch.delenv("VAULT_TOKEN", raising=False)
        assert resolve_vault_token(config, repo_root) == "from-file"

    def test_stack_state_used_when_only_source_s4_16(self, tmp_path, monkeypatch):
        """S4.16 — falls back to the local vault stack [state].root_token."""
        repo_root = tmp_path
        vault_stack = repo_root / "infra" / "vault"
        vault_stack.mkdir(parents=True)
        (vault_stack / "ciu.toml").write_text('[state]\nroot_token = "from-state"\n')
        config = {"vault": {}}

        monkeypatch.delenv("VAULT_TOKEN", raising=False)
        assert resolve_vault_token(config, repo_root) == "from-state"

    def test_default_stack_path_infra_vault_s4_16(self, tmp_path, monkeypatch):
        """S4.16 — stack_path defaults to 'infra/vault'."""
        repo_root = tmp_path
        vault_stack = repo_root / "infra" / "vault"
        vault_stack.mkdir(parents=True)
        (vault_stack / "ciu.toml").write_text('[state]\nroot_token = "defaulted"\n')

        monkeypatch.delenv("VAULT_TOKEN", raising=False)
        assert resolve_vault_token({}, repo_root) == "defaulted"

    def test_none_when_no_source_s4_16(self, tmp_path, monkeypatch):
        """S4.16 — None when no source yields a token."""
        repo_root = tmp_path
        monkeypatch.delenv("VAULT_TOKEN", raising=False)
        assert resolve_vault_token({}, repo_root) is None

    def test_unreadable_token_file_raises_s4_16(self, tmp_path, monkeypatch):
        """S4.16 — a token_file that exists but is unreadable aborts."""
        repo_root = tmp_path
        tf = repo_root / "tok"
        tf.write_text("x")
        tf.chmod(0o000)
        config = {"vault": {"token_file": "tok"}}
        monkeypatch.delenv("VAULT_TOKEN", raising=False)

        # Skip when running as root (chmod 000 is bypassed by root).
        if os.getuid() == 0:
            pytest.skip("root bypasses file permission bits")

        with pytest.raises(VaultError, match=r"\[S4.16\]"):
            resolve_vault_token(config, repo_root)


# ---------------------------------------------------------------------------
# vault_addr_from_config — S4.16
# ---------------------------------------------------------------------------

class TestVaultAddr:
    def test_builds_addr_from_topology_s4_16(self):
        """S4.16 — address is http://<internal_host>:<internal_port>."""
        config = {
            "topology": {
                "services": {"vault": {"internal_host": "vault", "internal_port": 8200}}
            }
        }
        assert vault_addr_from_config(config) == "http://vault:8200"

    def test_missing_aborts_s4_16(self):
        """S4.16 — missing host/port aborts with [S4.16]."""
        with pytest.raises(VaultError, match=r"\[S4.16\]"):
            vault_addr_from_config({"topology": {"services": {"vault": {}}}})


# ---------------------------------------------------------------------------
# reset_secrets — S4.25
# ---------------------------------------------------------------------------

class TestResetSecrets:
    def test_deletes_only_selected_names_s4_25(self, dirs):
        """S4.25 — reset deletes only the selected names' store files."""
        stack_dir, repo_root = dirs
        specs = [
            _spec("keep", "GEN_EPHEMERAL"),
            _spec("drop", "GEN_EPHEMERAL"),
        ]
        res = _materialize(specs, stack_dir=stack_dir, repo_root=repo_root)
        keep_file = res["keep"].file
        drop_file = res["drop"].file
        assert keep_file.exists() and drop_file.exists()

        deleted = reset_secrets(stack_dir, repo_root, specs, names=["drop"])

        assert deleted == [drop_file]
        assert not drop_file.exists()
        assert keep_file.exists()

    def test_reset_all_when_names_none_s4_25(self, dirs):
        """S4.25 — names=None resets every (deletable) store file."""
        stack_dir, repo_root = dirs
        specs = [_spec("a", "GEN_EPHEMERAL"), _spec("b", "GEN_LOCAL:loc/b")]
        res = _materialize(specs, stack_dir=stack_dir, repo_root=repo_root)

        deleted = set(reset_secrets(stack_dir, repo_root, specs))
        assert res["a"].file in deleted
        assert res["b"].file in deleted  # GEN_LOCAL project-store file selected
        assert not res["a"].file.exists()
        assert not res["b"].file.exists()

    def test_ask_file_never_deleted_s4_25(self, dirs):
        """S4.25 — ASK_FILE has no store file and is never deleted."""
        stack_dir, repo_root = dirs
        src = stack_dir / "f.pem"
        src.write_text("data")
        specs = [_spec("cert", "ASK_FILE:f.pem")]

        deleted = reset_secrets(stack_dir, repo_root, specs)
        assert deleted == []
        assert src.exists()  # in-place file untouched


# ---------------------------------------------------------------------------
# list_secrets — S4.25 (no values)
# ---------------------------------------------------------------------------

class TestListSecrets:
    def test_lists_metadata_no_values_s4_25(self, dirs):
        """S4.25 — list reports name/kind/locator/store/exists, never a value."""
        stack_dir, repo_root = dirs
        specs = [_spec("session", "GEN_EPHEMERAL")]
        _materialize(specs, stack_dir=stack_dir, repo_root=repo_root)

        rows = list_secrets(specs, stack_dir, repo_root)
        assert len(rows) == 1
        row = rows[0]
        assert row["name"] == "session"
        assert row["kind"] == "GEN_EPHEMERAL"
        assert row["exists"] is True
        assert "value" not in row
        assert str(stack_store(stack_dir) / "session") == row["store"]

    def test_gen_local_store_is_project_path_s4_25(self, dirs):
        """S4.25 — GEN_LOCAL's reported store path is the project store."""
        stack_dir, repo_root = dirs
        specs = [_spec("token", "GEN_LOCAL:shared/token")]
        rows = list_secrets(specs, stack_dir, repo_root)
        assert rows[0]["store"] == str(project_store(repo_root) / "shared" / "token")
        assert rows[0]["exists"] is False  # not materialized yet
