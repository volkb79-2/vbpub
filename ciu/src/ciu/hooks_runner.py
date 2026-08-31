"""CIU v2 hooks runner — P6.

Implements S9 (hooks contract) per the v2 specification.

Hook points: pre_secrets · pre_compose · post_compose  (S9.1, S8.3)

Public API
----------
HOOK_POINTS          tuple of the three valid point names
HookContext          dataclass passed to every hook callable
load_hook(path)      load a module and return its callable
load_hook_for_check(path)
                     load a module ONCE and return ``(run, validate_config)``
                     for ``ciu check``'s hook preflight (S9.5) — never runs
                     either callable
run_hooks(...)       validate + run a list of hooks for one point
set_nested(d, dotted, value)  helper shared with the engine
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Callable

import tomli_w

# ---------------------------------------------------------------------------
# S9.1 — hook points
# ---------------------------------------------------------------------------

HOOK_POINTS: tuple[str, ...] = ("pre_secrets", "pre_compose", "post_compose")


class HookExecutionError(RuntimeError):
    """Raised when a hook's body raises an unexpected exception (exit 1).

    Wraps the original exception via exception chaining so the cause is
    preserved.  The runner only wraps body exceptions — [S9.2] FileNotFoundError
    and [S9.4] contract ValueErrors propagate unchanged.
    """

# v1 per-point names that were withdrawn in S9.1, used only for migration hints
_V1_FUNCTION_NAMES = frozenset(
    ("pre_compose_hook", "post_compose_hook", "pre_secrets_hook")
)
_V1_CLASS_NAMES = frozenset(
    ("PreComposeHook", "PostComposeHook", "PreSecretsHook")
)


# ---------------------------------------------------------------------------
# S9.3 — HookContext
# ---------------------------------------------------------------------------


@dataclass
class HookContext:
    """Context object passed to every hook callable (S9.3)."""

    point: str
    """One of HOOK_POINTS."""

    stack_dir: Path
    """Absolute path to the stack directory."""

    repo_root: Path
    """Absolute path to the repository root."""

    secret_file: Callable[[str], Path]
    """Given a secret name, returns its store-file path.
    Raises KeyError for unknown names (wired by the engine, S9.3)."""

    wait_healthy: Callable[..., bool] | None = None
    """Readiness probe (S9.3/CIU-4): ``wait_healthy(service, *, timeout_s=120.0)``
    polls the named service's Docker health until healthy and returns True, or
    False on timeout. Wired by the engine; None only in bare/unit construction."""

    wait_tcp: Callable[..., bool] | None = None
    """Readiness probe (S9.3/CIU-4): ``wait_tcp(host, port, *, timeout_s=30.0)``
    polls a TCP endpoint until it accepts a connection (for images without a
    healthcheck). Wired by the engine; None only in bare/unit construction."""

    extra: dict = field(default_factory=dict)
    """Arbitrary extra data the engine may inject (extensible)."""

    selected_profiles: tuple[str, ...] | None = None
    """S3.12 / CIU-44: the ordered named profiles this invocation resolved
    (empty tuple = the default all-phases profile); None outside a
    deployment render."""

    deployed_stacks: tuple[str, ...] | None = None
    """S3.12 / CIU-44: the full stack set THIS invocation deploys
    (declaration order, deduped); None outside a deployment render."""

    instance_id: str | None = None
    """S3.12 / CIU-44: this workspace's INSTANCE_ID from its own ciu.env
    (exact path), when present — hooks no longer need to re-parse the
    identity record or trust ambient state."""

    network: str | None = None
    """S3.12 / CIU-44: this workspace's DOCKER_NETWORK_INTERNAL from its own
    ciu.env (exact path), when present."""

    identity_unreadable: bool = False
    """S3.12 / CIU-80: True when this workspace's ``ciu.env`` is PRESENT but
    could not be read/parsed (an ``OSError``, ``UnicodeDecodeError`` or
    ``WorkspaceEnvError`` while reading it) — distinct from a genuinely
    ABSENT ``ciu.env`` (no ``ciu env generate`` has run here yet), which
    leaves this ``False`` with ``instance_id``/``network`` also ``None``.
    Both identity readers — ``deploy._workspace_identity`` (the ``ciu
    check`` preflight) and ``engine.main_execution``'s STEP-12 real-run read
    — degrade an unreadable record to the same ``{}`` (so ``instance_id``/
    ``network`` are ``None`` either way, keeping preflight and real run
    answering identically per S3.12/CIU-44) and set this field identically,
    so a hook that legitimately branches on identity can tell "genuinely
    unmanaged" from "record exists, CIU could not parse it" without either
    site turning the degradation into a refusal. Additive: a hook author who
    does not care about the distinction can ignore it."""


# ---------------------------------------------------------------------------
# S9.1 — load_hook
# ---------------------------------------------------------------------------


def _load_hook_module(path: Path) -> ModuleType:
    """Import the hook file at *path* EXACTLY ONCE and return its module.

    The import/exec_module machinery formerly inlined in :func:`load_hook`,
    extracted (ciu-P18) so ``ciu check`` can pull BOTH ``run`` and the
    optional ``validate_config`` (S9.5) out of a SINGLE module load. Importing
    the same file twice in one invocation would re-execute its module-level
    code — a hook author's import-time statements are their own business, but
    CIU must not double-run them behind their back.

    Missing file → ``FileNotFoundError`` with the ``[S9.2]`` marker (the
    contract every caller, including the check path, still relies on).
    """
    if not path.exists():
        raise FileNotFoundError(f"[S9.2] Hook file not found: {path}")

    module_name = f"_ciu_hook_{path.stem}_{id(path)}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create module spec for hook: {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _resolve_hook_callables(
    module: ModuleType, path: Path
) -> tuple[Callable, Callable | None]:
    """Return ``(run, validate_config | None)`` for an ALREADY-loaded *module*.

    ``run`` resolution is exactly S9.1's existing order (module-level ``run``
    first, then a no-arg ``Hook`` instance's ``run``, then the v1
    migration-hint / not-a-hook errors). ``validate_config`` (S9.5) is
    OPTIONAL: a module-level ``validate_config`` wins; otherwise, when ``run``
    came from a ``Hook`` instance, that instance's ``validate_config`` method
    is used, so class-style hooks can carry a preflight the same way
    function-style ones do. A non-callable attribute of that name is treated
    as absent rather than as an error — it is not part of the S9.1 contract a
    hook must satisfy.

    Neither callable is invoked here.
    """
    owner: object | None = None

    # Preferred: module-level `run` function
    if hasattr(module, "run") and callable(module.run):
        run: Callable = module.run
    else:
        # Preferred: `Hook` class with a `run` method
        hook_cls = getattr(module, "Hook", None)
        if hook_cls is not None:
            instance = hook_cls()
            if not hasattr(instance, "run"):
                raise AttributeError(
                    f"Hook class 'Hook' in {path} has no run() method"
                )
            run = instance.run
            owner = instance
        else:
            # Detect v1 withdrawn names and give a migration hint (S9.1)
            found_v1: list[str] = []
            for name in _V1_FUNCTION_NAMES:
                if hasattr(module, name):
                    found_v1.append(name)
            for name in _V1_CLASS_NAMES:
                if hasattr(module, name):
                    found_v1.append(name)

            if found_v1:
                raise AttributeError(
                    f"[S9.1] Hook module {path} defines v1 per-point name(s) "
                    f"{found_v1!r} which are withdrawn in v2. "
                    "Rename to a module-level 'run(config, ctx) -> dict' function "
                    "or a 'Hook' class with a 'run' method (S9.1)."
                )

            raise AttributeError(
                f"Hook module {path} does not define a 'run' function or 'Hook' class"
            )

    validate = getattr(module, "validate_config", None)
    if validate is None and owner is not None:
        validate = getattr(owner, "validate_config", None)
    if not callable(validate):
        validate = None
    return run, validate


def load_hook(path: Path) -> Callable:
    """Load a hook module from *path* and return its callable.

    The module must expose either:
    - a module-level function named ``run``, or
    - a class named ``Hook`` (instantiated no-arg) whose instance has a
      ``run`` method.

    If the module defines one of the v1 per-point names
    (``pre_compose_hook``, ``PostComposeHook``, etc.) but **not** ``run``/
    ``Hook``, raises ``AttributeError`` with a migration hint naming S9.1.

    Missing file → ``FileNotFoundError`` with ``[S9.2]`` marker.
    """
    return _resolve_hook_callables(_load_hook_module(path), path)[0]


def load_hook_for_check(path: Path) -> tuple[Callable, Callable | None]:
    """``(run, validate_config | None)`` from ONE import of *path* — S9.5.

    The ``ciu check`` counterpart of :func:`load_hook` (ciu-P18). It answers
    both of the preflight's questions about a hook file from a single module
    load: does it satisfy the S9.1/S9.2 contract at all (stage 8: a missing
    file still raises the ``[S9.2]`` ``FileNotFoundError``, a module with
    neither ``run`` nor ``Hook`` still raises the same ``AttributeError``),
    and does it opt into the optional preflight (stage 9)?

    ``validate_config`` is ``None`` when the module does not define one — that
    is the normal case and NOT an error; a hook without one is simply skipped
    by the preflight stage.

    NEITHER returned callable is invoked here. In particular ``run`` is only
    *located*, never called: `ciu check` must stay side-effect-free.
    """
    module = _load_hook_module(path)
    return _resolve_hook_callables(module, path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def set_nested(d: dict, dotted: str, value: object) -> None:
    """Set *value* at *dotted* key path inside *d*, creating intermediate dicts.

    Public so the engine can reuse it directly.
    """
    keys = dotted.split(".")
    cursor = d
    for key in keys[:-1]:
        if not isinstance(cursor.get(key), dict):
            cursor[key] = {}
        cursor = cursor[key]
    cursor[keys[-1]] = value


def _persist_state(dotted: str, value: object, stack_toml_path: Path) -> None:
    """Write *value* under ``[state].<normalised-path>`` in *stack_toml_path*.

    Handles both ``root_token`` and ``state.root_token`` dotted forms — both
    land at ``state.root_token`` (S9.4).

    Written atomically: tmp file + ``os.replace``.
    """
    # Strip a leading 'state.' prefix if present
    sub_path = dotted[len("state."):] if dotted.startswith("state.") else dotted

    # Read existing TOML (if any)
    if stack_toml_path.exists():
        with open(stack_toml_path, "rb") as fh:
            data: dict = tomllib.load(fh)
    else:
        data = {}

    # Ensure top-level [state] table exists
    if not isinstance(data.get("state"), dict):
        data["state"] = {}

    # Write the value under state.<sub_path>
    set_nested(data["state"], sub_path, value)

    # Atomic write
    stack_toml_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = stack_toml_path.with_suffix(".toml.tmp")
    with open(tmp, "wb") as fh:
        tomli_w.dump(data, fh)
    os.replace(tmp, stack_toml_path)


# ---------------------------------------------------------------------------
# S9.1/S9.2/S9.4 — run_hooks
# ---------------------------------------------------------------------------


def run_hooks(
    hook_paths: list[str],
    point: str,
    config: dict,
    ctx: HookContext,
    stack_toml_path: Path,
) -> None:
    """Validate and run all hooks for one hook point.

    Parameters
    ----------
    hook_paths:
        Paths as listed in ``[<root>.hooks].<point>``; relative paths are
        resolved against ``ctx.stack_dir``.
    point:
        One of ``HOOK_POINTS`` (informational; stored in *ctx*).
    config:
        Merged in-memory config dict, mutated in-place by ``apply_to_config``
        returns.
    ctx:
        Hook context (S9.3).
    stack_toml_path:
        Path to the stack's ``ciu.toml``; ``persist: 'state'`` writes here.

    Behaviour
    ---------
    - All paths are validated (existence) **before** any hook executes (S9.2).
    - Hooks are called as ``hook(config, ctx) -> dict | None``.
    - Return value contract (S9.4):
      - ``None`` or ``{}`` → ok, no side-effects.
      - ``dict`` → every value MUST be a dict with a ``'value'`` key.
        - ``apply_to_config: True`` → ``set_nested(config, path, value)``.
        - ``persist: 'state'`` → write under ``[state]`` in stack toml.
        - Any other ``persist`` value → ``ValueError [S9.4]``.
        - Plain ``{KEY: scalar}`` (v1 form) → ``ValueError [S9.4]``.
    - Hooks MUST NOT mutate ``os.environ`` (snapshot/compare; S9.4).
    - Runner-contract exceptions (the S9.2 ``FileNotFoundError`` from a missing
      hook file, the S9.4 ``ValueError`` for a malformed return) propagate
      unchanged; any exception raised by the hook *body* is re-wrapped as
      ``HookExecutionError`` (engine maps it to exit 1).
    """
    # --- Phase 1: resolve and validate all paths before running any hook ---
    resolved: list[tuple[str, Path, Callable]] = []
    for raw in hook_paths:
        p = Path(raw)
        if not p.is_absolute():
            p = ctx.stack_dir / p
        # Raises FileNotFoundError if missing — abort before any hook runs (S9.2)
        hook_fn = load_hook(p)
        resolved.append((raw, p, hook_fn))

    # --- Phase 2: execute hooks sequentially ---
    for _raw, _p, hook_fn in resolved:
        # Snapshot process environment before each hook (S9.4)
        env_snapshot = dict(os.environ)

        try:
            result = hook_fn(config, ctx)
        except Exception as exc:
            # [S9.2] / [S9.4] contract violations (FileNotFoundError,
            # ValueError) must propagate unchanged — only re-wrap genuine hook
            # body exceptions that are NOT part of the runner's own contract.
            # In practice: FileNotFoundError is caught before Phase 2 (load_hook
            # raises it) and the [S9.4] ValueErrors are raised AFTER the call,
            # so any exception from the hook body itself lands here.
            raise HookExecutionError(
                f"[hook] {_p}: {exc}"
            ) from exc

        # Detect any environment mutation
        if dict(os.environ) != env_snapshot:
            # Restore before raising
            os.environ.clear()
            os.environ.update(env_snapshot)
            raise ValueError(
                "[S9.4] hook mutated process environment; "
                "hooks MUST NOT modify os.environ"
            )

        if result is None or result == {}:
            continue

        if not isinstance(result, dict):
            raise ValueError(
                f"[S9.4] hook returned {type(result).__name__!r}; "
                "expected dict or None"
            )

        for path_key, meta in result.items():
            # Detect v1 plain {KEY: scalar} form
            if not isinstance(meta, dict):
                raise ValueError(
                    f"[S9.4] hook returned v1 env-update form; "
                    f"use {{'path': {{'value': ..., 'apply_to_config': True}}}} "
                    f"or persist:'state'"
                )

            if "value" not in meta:
                raise ValueError(
                    f"[S9.4] hook return entry {path_key!r} is missing 'value'; "
                    f"every entry must be a dict containing 'value'"
                )

            value = meta["value"]
            apply = meta.get("apply_to_config", False)
            persist = meta.get("persist")

            # apply_to_config: mutate in-memory config (S9.4)
            if apply:
                set_nested(config, path_key, value)

            # persist must be 'state' or absent (S9.4 — no other values)
            if persist is not None:
                if persist != "state":
                    raise ValueError(
                        f"[S9.4] hook returned persist={persist!r}; "
                        "only 'state' is a valid persist destination. "
                        "v1's persist:'toml' and persist:'env' are withdrawn."
                    )
                _persist_state(path_key, value, stack_toml_path)
