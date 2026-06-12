"""Secret materialization for CIU v2.

Normative contract: docs/SPEC.md §S4 (resolution & materialization,
S4.8–S4.16, S4.24–S4.26) plus S1.6 (store locations), S2.5 (falsy-safe
numeric env), S6.5 (chown degrade / helper-container fallback hook).

This module turns parsed ``SecretSpec`` objects (from
``ciu.secrets.directives``) into materialized secret files on disk, returning
the file each overlay entry must reference. It NEVER writes a secret value
into ``ciu.toml`` (S4.24) and NEVER logs a value (S4.23).

Public API
----------
MaterializedSecret                      : dataclass (spec, value, file)
stack_store(stack_dir) -> Path          : per-stack store dir (S4.9)
project_store(repo_root) -> Path        : project store dir (S4.9, GEN_LOCAL)
materialize(specs, ...) -> dict[str, MaterializedSecret]
list_secrets(specs, stack_dir, repo_root) -> list[dict]   (S4.25)
reset_secrets(stack_dir, repo_root, specs, names=None) -> list[Path]  (S4.25)
"""

from __future__ import annotations

import contextlib
import fcntl
import getpass
import os
import secrets as _stdlib_secrets
import sys
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping

from ciu.secrets.directives import SecretSpec
from ciu.secrets.providers import VaultError, VaultKV2

# Default secret-file mode when a spec carries the default "0440" (S4.10).
_DEFAULT_MODE = 0o440
_STORE_DIR_MODE = 0o700  # S4.10 store dirs

# Kinds that need Vault to be available (S4.16).
_VAULT_KINDS = frozenset({"ASK_VAULT", "GEN_TO_VAULT"})


# ---------------------------------------------------------------------------
# MaterializedSecret
# ---------------------------------------------------------------------------

@dataclass
class MaterializedSecret:
    """Result of materializing one secret.

    Attributes
    ----------
    spec  : the originating SecretSpec.
    value : the resolved plaintext value, or None for ASK_FILE (whose content
            is referenced in place and never loaded by CIU, S4.14).
    file  : the file the overlay must reference as the compose secret source —
            the per-stack/project store file, or the in-place ASK_FILE path.
    """

    spec: SecretSpec
    value: str | None
    file: Path


# ---------------------------------------------------------------------------
# Store locations (S4.9 / S1.6)
# ---------------------------------------------------------------------------

def stack_store(stack_dir: Path) -> Path:
    """Per-stack secret store directory ``<stack>/.ciu/secrets`` (S4.9)."""
    return Path(stack_dir) / ".ciu" / "secrets"


def project_store(repo_root: Path) -> Path:
    """Project secret store ``<repo-root>/.ciu/secrets`` (S4.9).

    GEN_LOCAL secrets live here so unrelated stacks can share a generated
    value without Vault; their ``<name>`` MAY contain ``/`` namespacing.
    """
    return Path(repo_root) / ".ciu" / "secrets"


def _store_file(spec: SecretSpec, stack_dir: Path, repo_root: Path) -> Path:
    """The store file a secret's overlay references (NOT for ASK_FILE).

    GEN_LOCAL → project store (S4.9, name may contain '/'); everything else →
    per-stack store. ASK_FILE has no store file (handled by the caller).
    """
    if spec.kind == "GEN_LOCAL":
        return project_store(repo_root) / spec.locator
    return stack_store(stack_dir) / spec.name


# ---------------------------------------------------------------------------
# Numeric env (S2.5 — falsy-safe; 0 is valid)
# ---------------------------------------------------------------------------

def _env_int_or_none(env: Mapping[str, str], key: str) -> int | None:
    """Read an integer env var with falsy-safe checks (S2.5).

    ``None``/``""`` mean "unset"; ``"0"`` is a valid value (UID/GID 0).
    A non-integer value is treated as unset (the chown step degrades).
    """
    raw = env.get(key)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# chown (S4.10 degrade / S6.5 helper-container hook)
# ---------------------------------------------------------------------------

def _default_chown(path: Path, uid: int | None, gid: int | None) -> None:
    """Best-effort chown that degrades on PermissionError (S4.10).

    When CIU lacks the privilege to chown, emit exactly ONE clear warning
    naming the file and the required owner, then continue (degraded). The
    S6.5 helper-container fallback is wired by the engine via an injected
    ``chown_fn`` — this default is the in-process attempt only.
    """
    if uid is None and gid is None:
        return
    try:
        os.chown(path, uid if uid is not None else -1, gid if gid is not None else -1)
    except PermissionError:
        want = f"{uid if uid is not None else '<unchanged>'}:" \
               f"{gid if gid is not None else '<unchanged>'}"
        warnings.warn(
            f"[S4.10] insufficient privilege to chown secret file "
            f"'{path}' to {want}; continuing with current ownership "
            f"(materialized file may be unreadable by the container)",
            stacklevel=2,
        )
    except FileNotFoundError:  # pragma: no cover — store file just written
        pass


# ---------------------------------------------------------------------------
# Atomic store write (S4.9 / S4.10)
# ---------------------------------------------------------------------------

def _mode_from_spec(spec: SecretSpec) -> int:
    """Parse the spec's octal mode string into an int, default 0440 (S4.10)."""
    try:
        return int(spec.mode, 8)
    except (TypeError, ValueError):
        return _DEFAULT_MODE


def _write_store_file(
    target: Path,
    value: str,
    spec: SecretSpec,
    *,
    env: Mapping[str, str],
    chown_fn: Callable[[Path, int | None, int | None], None],
) -> None:
    """Atomically write *value* to *target* with spec mode + ownership.

    - Raw value bytes, NO trailing newline (S4.9).
    - tmp file in the same dir + ``os.replace`` (atomic, S4.9/S8.4).
    - chmod to ``spec.mode`` (default 0440); parent dirs mode 0700 (S4.10).
    - chown to ``(spec.uid or CONTAINER_UID) : DOCKER_GID`` (S4.10); the
      CONTAINER_UID/DOCKER_GID env are read falsy-safe (S2.5).
    """
    target = Path(target)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    _ensure_dir_mode(parent, target)

    mode = _mode_from_spec(spec)
    data = value.encode("utf-8")

    # NamedTemporaryFile in the same dir so os.replace is an atomic rename.
    fd, tmp_name = tempfile.mkstemp(dir=str(parent), prefix=".tmp-secret-")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, target)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()
        raise

    # Ownership last (after the file is in place at its final name).
    container_uid = _env_int_or_none(env, "CONTAINER_UID")
    docker_gid = _env_int_or_none(env, "DOCKER_GID")
    owner_uid = spec.uid if spec.uid is not None else container_uid
    chown_fn(target, owner_uid, docker_gid)


def _ensure_dir_mode(parent: Path, leaf_target: Path) -> None:
    """chmod the store dir tree to 0700 (S4.10).

    For GEN_LOCAL names with '/' namespacing the parent may be several levels
    deep under the store root; tighten EVERY level from the leaf parent up to
    and including the '.ciu/secrets' store root (otherwise intermediate dirs
    keep the process umask and disclose secret names to local users).
    Degrades silently on a non-chmod-able level, never crashes.
    """
    current = Path(parent)
    while True:
        with contextlib.suppress(OSError):
            os.chmod(current, _STORE_DIR_MODE)
        at_store_root = current.name == "secrets" and current.parent.name == ".ciu"
        if at_store_root or current.parent == current:
            break
        current = current.parent


# ---------------------------------------------------------------------------
# Locking (S4.26)
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _flock(lock_path: Path):
    """Hold an exclusive ``fcntl.flock`` on *lock_path* (S4.26).

    Parent dir is created 0700. The lock file itself is created if absent.
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(lock_path.parent, _STORE_DIR_MODE)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# ---------------------------------------------------------------------------
# materialize
# ---------------------------------------------------------------------------

def materialize(
    specs: Iterable[SecretSpec],
    *,
    stack_dir: Path,
    repo_root: Path,
    vault: VaultKV2 | None,
    assume_yes: bool,
    env: Mapping[str, str] = os.environ,
    chown_fn: Callable[[Path, int | None, int | None], None] | None = None,
    prompt_fn: Callable[[str], str] | None = None,
) -> dict[str, MaterializedSecret]:
    """Resolve and materialize every secret in *specs* (S4.8–S4.16).

    Parameters
    ----------
    specs       : parsed SecretSpec objects (from directives.discover).
    stack_dir   : the stack directory (per-stack store + lock live under it).
    repo_root   : the workspace root (project store + lock for GEN_LOCAL).
    vault       : a VaultKV2 client, or None when no Vault token/address
                  resolved. A None vault with any vault-backed spec aborts.
    assume_yes  : the ``-y`` flag; disables interactive prompts (S4.13).
    env         : environment mapping (default ``os.environ``); read for
                  ASK_EXTERNAL locators, CIU_SECRET_*, CONTAINER_UID, DOCKER_GID.
    chown_fn    : injected ``(path, uid, gid) -> None`` ownership applier
                  (S6.5 helper-container fallback wired by the engine).
                  Default: in-process os.chown with degrade-warning (S4.10).
    prompt_fn   : injected ``(prompt) -> str`` for ASK_EXTERNAL; default
                  ``getpass.getpass``. Only consulted when interactive
                  (not assume_yes and stdin is a TTY).

    Returns
    -------
    dict[name, MaterializedSecret]

    Raises
    ------
    VaultError : vault-backed spec with no vault, ASK_VAULT absent, etc.
    ValueError : ASK_EXTERNAL non-interactive with no value (S4.13),
                 ASK_FILE missing/unreadable (S4.14).
    """
    specs = list(specs)
    stack_dir = Path(stack_dir)
    repo_root = Path(repo_root)
    chown_fn = chown_fn if chown_fn is not None else _default_chown
    prompt_fn = prompt_fn if prompt_fn is not None else getpass.getpass

    if not specs:
        return {}

    # S4.16: a vault-backed directive with no usable Vault aborts before any
    # container could start.
    if vault is None and any(s.kind in _VAULT_KINDS for s in specs):
        names = sorted(s.name for s in specs if s.kind in _VAULT_KINDS)
        raise VaultError(
            f"[S4.16] no Vault token/address available but vault-backed "
            f"secrets are declared: {names}"
        )

    needs_project_lock = any(s.kind == "GEN_LOCAL" for s in specs)

    # S4.26: always serialize on the stack lock; additionally take the project
    # lock when GEN_LOCAL writes are in play. Order (stack then project) is
    # fixed to avoid deadlocks across concurrent runs.
    stack_lock = stack_dir / ".ciu" / "lock"
    project_lock = repo_root / ".ciu" / "lock"

    with contextlib.ExitStack() as locks:
        locks.enter_context(_flock(stack_lock))
        if needs_project_lock:
            locks.enter_context(_flock(project_lock))

        results: dict[str, MaterializedSecret] = {}
        for spec in specs:
            results[spec.name] = _materialize_one(
                spec,
                stack_dir=stack_dir,
                repo_root=repo_root,
                vault=vault,
                assume_yes=assume_yes,
                env=env,
                chown_fn=chown_fn,
                prompt_fn=prompt_fn,
            )
        return results


def _materialize_one(
    spec: SecretSpec,
    *,
    stack_dir: Path,
    repo_root: Path,
    vault: VaultKV2 | None,
    assume_yes: bool,
    env: Mapping[str, str],
    chown_fn: Callable[[Path, int | None, int | None], None],
    prompt_fn: Callable[[str], str],
) -> MaterializedSecret:
    """Resolve and persist a single secret. See ``materialize`` for contract."""
    kind = spec.kind

    def _persist(value: str) -> MaterializedSecret:
        target = _store_file(spec, stack_dir, repo_root)
        _write_store_file(target, value, spec, env=env, chown_fn=chown_fn)
        return MaterializedSecret(spec=spec, value=value, file=target)

    def _reuse(target: Path) -> MaterializedSecret:
        # Re-apply mode/ownership on reuse so spec mode/uid changes take
        # effect on pre-existing files too (S4.10 must hold idempotently).
        with contextlib.suppress(OSError):
            os.chmod(target, _mode_from_spec(spec))
        container_uid = _env_int_or_none(env, "CONTAINER_UID")
        docker_gid = _env_int_or_none(env, "DOCKER_GID")
        owner_uid = spec.uid if spec.uid is not None else container_uid
        chown_fn(target, owner_uid, docker_gid)
        value = target.read_bytes().decode("utf-8")
        return MaterializedSecret(spec=spec, value=value, file=target)

    # -- GEN_LOCAL (S4.8/S4.9/S4.11) --------------------------------------
    if kind == "GEN_LOCAL":
        target = _store_file(spec, stack_dir, repo_root)
        if target.exists():
            # The file IS the value (no TOML state).
            return _reuse(target)
        value = _stdlib_secrets.token_urlsafe(32)
        return _persist(value)

    # -- GEN_TO_VAULT (S4.11/S4.12) ---------------------------------------
    if kind == "GEN_TO_VAULT":
        assert vault is not None  # guarded in materialize()
        existing = vault.read(spec.locator)
        if existing is None:
            value = _stdlib_secrets.token_urlsafe(32)
            vault.write(spec.locator, value)
        else:
            value = existing
        # Refresh the store file from the provider every run (S4.12).
        return _persist(value)

    # -- ASK_VAULT (S4.2 must exist; S4.12 refresh) -----------------------
    if kind == "ASK_VAULT":
        assert vault is not None  # guarded in materialize()
        value = vault.read(spec.locator, field=spec.field)
        if value is None:
            raise VaultError(
                f"[S4.2] ASK_VAULT secret '{spec.name}' must exist at Vault "
                f"path '{spec.locator}' but it is absent"
            )
        return _persist(value)

    # -- ASK_EXTERNAL (S4.13) ---------------------------------------------
    if kind == "ASK_EXTERNAL":
        store_target = _store_file(spec, stack_dir, repo_root)

        # 1) env[locator]
        value = env.get(spec.locator)
        # 2) env['CIU_SECRET_' + NAME]
        if value is None or value == "":
            value = env.get("CIU_SECRET_" + spec.name.upper())
        if value:
            return _persist(value)

        # 3) existing stack store file (cached from a previous run) — reuse
        #    with no prompt.
        if store_target.exists():
            return _reuse(store_target)

        # 4) interactive prompt (only when not -y and stdin is a TTY).
        if not assume_yes and sys.stdin.isatty():
            entered = prompt_fn(f"Enter value for secret '{spec.name}': ")
            if entered:
                return _persist(entered)

        # 5) non-interactive with no value → abort (S4.13).
        raise ValueError(
            f"[S4.13] ASK_EXTERNAL secret '{spec.name}' has no value: set env "
            f"'{spec.locator}' or 'CIU_SECRET_{spec.name.upper()}', or run "
            f"interactively (cannot prompt non-interactively)"
        )

    # -- ASK_FILE (S4.14 — referenced in place, NO copy) ------------------
    if kind == "ASK_FILE":
        file_path = Path(spec.locator)
        if not file_path.is_absolute():
            file_path = stack_dir / file_path
        try:
            with file_path.open("rb"):
                pass
        except OSError as exc:
            raise ValueError(
                f"[S4.14] ASK_FILE secret '{spec.name}' references "
                f"'{file_path}' which must exist and be readable: {exc}"
            ) from exc
        # value is None — content is never loaded; overlay references in place.
        return MaterializedSecret(spec=spec, value=None, file=file_path)

    # -- GEN_EPHEMERAL (S4.2 — fresh every run) ---------------------------
    if kind == "GEN_EPHEMERAL":
        value = _stdlib_secrets.token_urlsafe(32)
        return _persist(value)

    # Unknown kind — the parser should have rejected this already.
    raise ValueError(
        f"[S4.2] unsupported secret kind '{kind}' for '{spec.name}'"
    )


# ---------------------------------------------------------------------------
# list_secrets (S4.25)
# ---------------------------------------------------------------------------

def list_secrets(
    specs: Iterable[SecretSpec],
    stack_dir: Path,
    repo_root: Path,
) -> list[dict]:
    """Describe each secret without any value (S4.25).

    Each entry: ``name``, ``kind``, ``locator``, ``store`` (path str, or None
    for the in-place ASK_FILE target which has no store file), ``exists``.
    """
    stack_dir = Path(stack_dir)
    repo_root = Path(repo_root)
    rows: list[dict] = []
    for spec in specs:
        if spec.kind == "ASK_FILE":
            file_path = Path(spec.locator)
            if not file_path.is_absolute():
                file_path = stack_dir / file_path
            store = file_path
        else:
            store = _store_file(spec, stack_dir, repo_root)
        rows.append(
            {
                "name": spec.name,
                "kind": spec.kind,
                "locator": spec.locator,
                "store": str(store),
                "exists": store.exists(),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# reset_secrets (S4.25)
# ---------------------------------------------------------------------------

def reset_secrets(
    stack_dir: Path,
    repo_root: Path,
    specs: Iterable[SecretSpec],
    names: Iterable[str] | None = None,
) -> list[Path]:
    """Delete store files for the selected secrets (S4.25).

    - Always targets the per-stack store file.
    - The project store file is targeted ONLY for GEN_LOCAL specs (S4.9).
    - ASK_FILE has no store file and is never deleted (referenced in place).
    - *names* limits the selection; None means all specs.

    The caller handles confirmation (``-y`` skips). Returns the paths actually
    deleted (existing files that were unlinked).
    """
    stack_dir = Path(stack_dir)
    repo_root = Path(repo_root)
    selected = set(names) if names is not None else None
    deleted: list[Path] = []
    for spec in specs:
        if selected is not None and spec.name not in selected:
            continue
        if spec.kind == "ASK_FILE":
            continue
        target = _store_file(spec, stack_dir, repo_root)
        if target.exists():
            with contextlib.suppress(FileNotFoundError):
                target.unlink()
                deleted.append(target)
    return deleted
