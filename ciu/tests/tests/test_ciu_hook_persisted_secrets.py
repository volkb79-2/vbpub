"""S9.4a — `persist: 'secret'` hook returns (ciu-P46 / F4).

Covers the new hook-return kind end to end: the store write reusing S4.9/S4.10/
S4.26's existing primitives, every contract refusal (each of which must name the
KEY and never the VALUE, S4.23), the provenance sidecar that makes a
hook-persisted secret visible to `ciu secrets list`/`reset`, and S4.16's
migrated source #3.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.hooks_runner import HookContext, run_hooks  # noqa: E402
from ciu.secrets import materialize as mz  # noqa: E402
from ciu.secrets.providers import resolve_vault_token  # noqa: E402

SECRET_VALUE = "hvs.CAESIJ-freshly-minted-root-token"


def _unknown_secret(name: str) -> Path:
    raise KeyError(name)


def _ctx(stack_dir: Path, repo_root: Path | None = None) -> HookContext:
    return HookContext(
        point="post_compose",
        stack_dir=stack_dir,
        repo_root=repo_root or stack_dir.parent,
        secret_file=_unknown_secret,
    )


def _hook(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "mint_hook.py"
    path.write_text(body, encoding="utf-8")
    return path


def _minting_hook(tmp_path: Path, name: str = "root_token", value: str | None = None,
                  extra: str = "") -> Path:
    literal = repr(SECRET_VALUE if value is None else value)
    return _hook(
        tmp_path,
        "def run(config, ctx):\n"
        f"    return {{{name!r}: {{'value': {literal}, "
        f"'persist': 'secret'{extra}}}}}\n",
    )


# ---------------------------------------------------------------------------
# The happy path: a hook MINTING a value no directive could express
# ---------------------------------------------------------------------------

def test_persist_secret_writes_the_store_file_with_S4_10_mode(tmp_path: Path) -> None:
    """S9.4a — same store path, same 0440 mode, same raw bytes as a directive."""
    stack = tmp_path / "infra" / "vault"
    stack.mkdir(parents=True)
    hook = _minting_hook(tmp_path)

    run_hooks([str(hook)], "post_compose", {}, _ctx(stack), stack / "ciu.toml")

    store = mz.hook_secret_store(stack, "root_token")
    assert store == stack / ".ciu" / "secrets" / "root_token"
    # Raw value, NO trailing newline (S4.9) — byte-identical to a directive's.
    assert store.read_text(encoding="utf-8") == SECRET_VALUE
    assert stat.S_IMODE(store.stat().st_mode) == 0o440
    # The store dir itself is tightened to 0700 (S4.10), exactly as materialize
    # does for a directive-declared secret.
    assert stat.S_IMODE(store.parent.stat().st_mode) == 0o700


def test_persist_secret_never_touches_state(tmp_path: Path) -> None:
    """S9.4a — the whole point of F4: nothing secret-shaped reaches [state]."""
    stack = tmp_path / "infra" / "vault"
    stack.mkdir(parents=True)
    toml_path = stack / "ciu.toml"
    hook = _minting_hook(tmp_path)

    run_hooks([str(hook)], "post_compose", {}, _ctx(stack), toml_path)

    assert not toml_path.exists()


def test_persist_secret_never_reaches_the_in_memory_config(tmp_path: Path) -> None:
    """S9.4a — no config mutation without apply_to_config, which is forbidden."""
    stack = tmp_path / "infra" / "vault"
    stack.mkdir(parents=True)
    config: dict = {}
    run_hooks(
        [str(_minting_hook(tmp_path))], "post_compose", config, _ctx(stack),
        stack / "ciu.toml",
    )
    assert config == {}


def test_persist_secret_records_its_hook_as_the_source(tmp_path: Path) -> None:
    """S9.4a — provenance is the hook FILE, so a listing can attribute it."""
    stack = tmp_path / "infra" / "vault"
    stack.mkdir(parents=True)
    hook = _minting_hook(tmp_path)

    run_hooks([str(hook)], "post_compose", {}, _ctx(stack), stack / "ciu.toml")

    manifest = mz.read_hook_manifest(stack)
    assert manifest == {"root_token": f"hook:{hook}"}
    # The sidecar is provenance, never a value store.
    assert SECRET_VALUE not in mz.hook_secret_manifest(stack).read_text(
        encoding="utf-8"
    )


def test_persist_secret_rewrite_replaces_the_value_atomically(tmp_path: Path) -> None:
    """A re-bootstrap overwrites in place; the sidecar keeps one row, not two."""
    stack = tmp_path / "infra" / "vault"
    stack.mkdir(parents=True)
    run_hooks([str(_minting_hook(tmp_path))], "post_compose", {}, _ctx(stack),
              stack / "ciu.toml")
    (tmp_path / "second").mkdir(exist_ok=True)
    second = _hook(
        tmp_path / "second",
        "def run(config, ctx):\n"
        "    return {'root_token': {'value': 'hvs.rotated-token', "
        "'persist': 'secret'}}\n",
    )

    run_hooks([str(second)], "post_compose", {}, _ctx(stack), stack / "ciu.toml")

    assert mz.hook_secret_store(stack, "root_token").read_text() == "hvs.rotated-token"
    assert list(mz.read_hook_manifest(stack)) == ["root_token"]


# ---------------------------------------------------------------------------
# Contract refusals — every message names the KEY, never the VALUE (S4.23)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "body, pattern",
    [
        pytest.param(
            "def run(config, ctx):\n"
            f"    return {{'root_token': {{'value': {SECRET_VALUE!r}, "
            "'persist': 'secret', 'apply_to_config': True}}\n",
            r"apply_to_config with persist:'secret'",
            id="apply_to_config-combined",
        ),
        pytest.param(
            "def run(config, ctx):\n"
            f"    return {{'vault.root_token': {{'value': {SECRET_VALUE!r}, "
            "'persist': 'secret'}}\n",
            r"dotted path with persist:'secret'",
            id="dotted-path",
        ),
        pytest.param(
            "def run(config, ctx):\n"
            f"    return {{'Root_Token': {{'value': {SECRET_VALUE!r}, "
            "'persist': 'secret'}}\n",
            r"not a valid secret name",
            id="bad-name-grammar",
        ),
        pytest.param(
            "def run(config, ctx):\n"
            "    return {'root_token': {'value': True, 'persist': 'secret'}}\n",
            r"its value is bool, not a string",
            id="non-string-value",
        ),
    ],
)
def test_persist_secret_contract_violations(tmp_path: Path, body: str, pattern: str) -> None:
    stack = tmp_path / "infra" / "vault"
    stack.mkdir(parents=True)
    hook = _hook(tmp_path, body)

    with pytest.raises(ValueError, match=pattern) as excinfo:
        run_hooks([str(hook)], "post_compose", {}, _ctx(stack), stack / "ciu.toml")

    assert "[S9.4a]" in str(excinfo.value)
    # S4.23 — a refusal must never carry the value it refused.
    assert SECRET_VALUE not in str(excinfo.value)
    assert not mz.hook_secret_store(stack, "root_token").exists()


def test_persist_secret_refuses_a_name_a_directive_already_declares(tmp_path: Path) -> None:
    """S4.6/S9.4a — one name, one writer; the collision names the KEY only."""
    stack = tmp_path / "infra" / "vault"
    stack.mkdir(parents=True)
    hook = _minting_hook(tmp_path)

    with pytest.raises(ValueError, match=r"already declared via a directive") as excinfo:
        run_hooks(
            [str(hook)], "post_compose", {}, _ctx(stack), stack / "ciu.toml",
            declared_secret_names=frozenset({"root_token"}),
        )

    assert "'root_token'" in str(excinfo.value)
    assert SECRET_VALUE not in str(excinfo.value)
    assert not mz.hook_secret_store(stack, "root_token").exists()


def test_apply_to_config_is_not_applied_when_the_secret_pairing_is_refused(
    tmp_path: Path,
) -> None:
    """The refusal is raised BEFORE the mutation, so nothing half-applies."""
    stack = tmp_path / "infra" / "vault"
    stack.mkdir(parents=True)
    hook = _hook(
        tmp_path,
        "def run(config, ctx):\n"
        f"    return {{'root_token': {{'value': {SECRET_VALUE!r}, "
        "'persist': 'secret', 'apply_to_config': True}}\n",
    )
    config: dict = {}

    with pytest.raises(ValueError, match=r"\[S9.4a\]"):
        run_hooks([str(hook)], "post_compose", config, _ctx(stack), stack / "ciu.toml")

    assert config == {}


def test_unknown_persist_destination_names_both_valid_ones(tmp_path: Path) -> None:
    """S9.4 — the message must now list 'secret' alongside 'state'."""
    stack = tmp_path / "s"
    stack.mkdir()
    hook = _hook(
        tmp_path,
        "def run(config, ctx):\n"
        "    return {'k': {'value': 'v', 'persist': 'toml'}}\n",
    )
    with pytest.raises(ValueError, match=r"only 'state' and 'secret' are valid"):
        run_hooks([str(hook)], "post_compose", {}, _ctx(stack), stack / "ciu.toml")


# ---------------------------------------------------------------------------
# The provenance sidecar / listing / reset
# ---------------------------------------------------------------------------

def test_hook_secret_rows_report_the_hook_as_the_locator(tmp_path: Path) -> None:
    stack = tmp_path / "s"
    stack.mkdir()
    mz.write_hook_secret(stack, "root_token", SECRET_VALUE, source="hook:/x/y.py")

    rows = mz.hook_secret_rows(stack, [])

    assert rows == [
        {
            "name": "root_token",
            "kind": "HOOK",
            "locator": "hook:/x/y.py",
            "store": str(stack / ".ciu" / "secrets" / "root_token"),
            "exists": True,
        }
    ]


def test_hook_secret_rows_suppress_a_name_a_directive_now_declares(tmp_path: Path) -> None:
    """A stale sidecar row for a since-declared name is not reported as hook-made."""
    stack = tmp_path / "s"
    stack.mkdir()
    mz.write_hook_secret(stack, "root_token", SECRET_VALUE, source="hook:/x/y.py")

    assert mz.hook_secret_rows(stack, ["root_token"]) == []


def test_hook_secret_rows_report_a_deleted_store_file_as_absent(tmp_path: Path) -> None:
    stack = tmp_path / "s"
    stack.mkdir()
    mz.write_hook_secret(stack, "root_token", SECRET_VALUE, source="hook:/x/y.py")
    mz.hook_secret_store(stack, "root_token").unlink()

    assert mz.hook_secret_rows(stack, [])[0]["exists"] is False


def test_read_hook_manifest_is_empty_without_a_sidecar(tmp_path: Path) -> None:
    assert mz.read_hook_manifest(tmp_path / "nothing-here") == {}


def test_read_hook_manifest_degrades_on_a_corrupt_sidecar(tmp_path: Path) -> None:
    """An unreadable sidecar must never fail a deployment — it is metadata."""
    stack = tmp_path / "s"
    stack.mkdir()
    manifest = mz.hook_secret_manifest(stack)
    manifest.parent.mkdir(parents=True)
    manifest.write_text("[secrets\nbroken = ", encoding="utf-8")

    assert mz.read_hook_manifest(stack) == {}


def test_read_hook_manifest_ignores_a_non_table_secrets_key(tmp_path: Path) -> None:
    stack = tmp_path / "s"
    stack.mkdir()
    manifest = mz.hook_secret_manifest(stack)
    manifest.parent.mkdir(parents=True)
    manifest.write_text('secrets = "not-a-table"\n', encoding="utf-8")

    assert mz.read_hook_manifest(stack) == {}


def test_a_failed_sidecar_write_leaves_no_temp_file_behind(
    tmp_path: Path, monkeypatch
) -> None:
    """The sidecar writer honours _write_store_file's own cleanup contract.

    A stray `.tmp-hookman-*` left in the secret store dir would be an
    undeleted artifact inside the one directory S4.9/S4.10 exist to keep
    tidy and tightly-permissioned.
    """
    import tomli_w

    stack = tmp_path / "s"
    stack.mkdir()
    store_dir = mz.stack_store(stack)
    store_dir.mkdir(parents=True)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(tomli_w, "dump", _boom)

    with pytest.raises(RuntimeError, match="disk gone"):
        mz.write_hook_secret(stack, "root_token", SECRET_VALUE, source="hook:/x.py")

    assert [p.name for p in store_dir.iterdir() if p.name.startswith(".tmp-")] == []


def test_reset_hook_secrets_removes_the_file_and_its_row(tmp_path: Path) -> None:
    stack = tmp_path / "s"
    stack.mkdir()
    mz.write_hook_secret(stack, "root_token", SECRET_VALUE, source="hook:/x.py")
    mz.write_hook_secret(stack, "unseal_key", "unseal-value", source="hook:/x.py")

    deleted = mz.reset_hook_secrets(stack)

    assert sorted(p.name for p in deleted) == ["root_token", "unseal_key"]
    assert mz.read_hook_manifest(stack) == {}
    # The sidecar itself is removed once it would be empty.
    assert not mz.hook_secret_manifest(stack).exists()


def test_reset_hook_secrets_honours_a_name_selection(tmp_path: Path) -> None:
    stack = tmp_path / "s"
    stack.mkdir()
    mz.write_hook_secret(stack, "root_token", SECRET_VALUE, source="hook:/x.py")
    mz.write_hook_secret(stack, "unseal_key", "unseal-value", source="hook:/x.py")

    deleted = mz.reset_hook_secrets(stack, names=["unseal_key"])

    assert [p.name for p in deleted] == ["unseal_key"]
    assert list(mz.read_hook_manifest(stack)) == ["root_token"]
    assert mz.hook_secret_store(stack, "root_token").exists()


def test_reset_hook_secrets_on_an_absent_store_is_a_no_op(tmp_path: Path) -> None:
    assert mz.reset_hook_secrets(tmp_path / "nothing") == []


def test_reset_hook_secrets_prunes_a_row_whose_file_is_already_gone(tmp_path: Path) -> None:
    """A half-cleaned store still loses its sidecar row, so the listing is honest."""
    stack = tmp_path / "s"
    stack.mkdir()
    mz.write_hook_secret(stack, "root_token", SECRET_VALUE, source="hook:/x.py")
    mz.hook_secret_store(stack, "root_token").unlink()

    assert mz.reset_hook_secrets(stack) == []
    assert mz.read_hook_manifest(stack) == {}


# ---------------------------------------------------------------------------
# `ciu secrets list` / `ciu secrets reset` (S4.25 + S9.4a)
# ---------------------------------------------------------------------------

def _stub_secrets_command(monkeypatch, specs: list) -> None:
    from ciu import engine

    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **_kw: None)
    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *_a: {})
    monkeypatch.setattr(engine.config_model, "render_stack", lambda *_a, **_kw: {"demo": {}})
    monkeypatch.setattr(engine.config_model, "deep_merge", lambda *_a: {"demo": {}})
    monkeypatch.setattr(engine.config_model, "validate_stack_shape", lambda *_a: "demo")
    monkeypatch.setattr(engine.secret_directives, "discover", lambda *_a: specs)


def test_secrets_list_shows_a_hook_persisted_secret_with_its_source(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """S9.4a — hook-persisted secrets must not be invisible in their own store."""
    from ciu import engine

    _stub_secrets_command(monkeypatch, [])
    mz.write_hook_secret(tmp_path, "root_token", SECRET_VALUE, source="hook:/x/vault.py")

    assert engine.main(["secrets", "list", "-d", str(tmp_path)]) == 0

    out = capsys.readouterr().out
    assert "root_token" in out
    assert "HOOK" in out
    assert "hook:/x/vault.py" in out
    # S4.25 — a listing never prints a value.
    assert SECRET_VALUE not in out


def test_secrets_reset_removes_a_hook_persisted_secret(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from ciu import engine

    _stub_secrets_command(monkeypatch, [])
    store = mz.write_hook_secret(tmp_path, "root_token", SECRET_VALUE, source="hook:/x.py")

    assert engine.main(["secrets", "reset", "-d", str(tmp_path), "-y"]) == 0

    assert not store.exists()
    assert "root_token" in capsys.readouterr().out


def test_secrets_reset_accepts_a_hook_persisted_name(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """`--name` must recognise hook-persisted names, not just declared ones."""
    from ciu import engine

    _stub_secrets_command(monkeypatch, [])
    store = mz.write_hook_secret(tmp_path, "root_token", SECRET_VALUE, source="hook:/x.py")

    assert engine.main(
        ["secrets", "reset", "-d", str(tmp_path), "--name", "root_token", "-y"]
    ) == 0

    assert not store.exists()
    capsys.readouterr()


def test_secrets_reset_still_refuses_a_genuinely_unknown_name(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from ciu import engine

    _stub_secrets_command(monkeypatch, [])

    assert engine.main(["secrets", "reset", "-d", str(tmp_path), "--name", "nope"]) == 2
    assert "no such secret" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# S4.16 source #3 (A2)
# ---------------------------------------------------------------------------

def test_bootstrap_store_is_S4_16_source_3(tmp_path: Path, monkeypatch) -> None:
    """A hook-persisted `root_token` is exactly what source #3 now resolves."""
    monkeypatch.delenv("VAULT_TOKEN", raising=False)
    stack = tmp_path / "infra" / "vault"
    stack.mkdir(parents=True)
    hook = _minting_hook(tmp_path)
    run_hooks([str(hook)], "post_compose", {}, _ctx(stack, tmp_path), stack / "ciu.toml")

    assert resolve_vault_token({"vault": {"stack_path": "infra/vault"}}, tmp_path) == (
        SECRET_VALUE
    )
