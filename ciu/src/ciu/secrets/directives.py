"""Canonical secret-directive parser — CIU v2.

Normative contract: docs/SPEC.md §S4 (S4.1–S4.7).

Public API
----------
DIRECTIVES          : tuple[str, ...]   — the six valid directive names
SECRET_NAME_RE      : str               — pattern for valid secret names
SecretSpec          : dataclass         — parsed representation of one secret
parse_value(name, value, table_path) -> SecretSpec
discover(stack_root_key, stack_config) -> list[SecretSpec]
find_misplaced(config, stack_root_key=None) -> list[tuple[str, object]]
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

# ---------------------------------------------------------------------------
# S4.2 — the six directives
# ---------------------------------------------------------------------------
DIRECTIVES: tuple[str, ...] = (
    "ASK_VAULT",
    "GEN_TO_VAULT",
    "GEN_LOCAL",
    "ASK_EXTERNAL",
    "ASK_FILE",
    "GEN_EPHEMERAL",
)

# S4.3 — withdrawn v1 names with their targeted error messages
_WITHDRAWN: dict[str, str] = {
    "ASK_VAULT_ONCE": (
        "[S4.3] 'ASK_VAULT_ONCE' was withdrawn in v2, use 'GEN_TO_VAULT'"
    ),
    "DERIVE": (
        "[S4.3] 'DERIVE' was withdrawn in v2; use secret() in configfile"
        " templates or a hook"
    ),
}

# S4.6 — secret name pattern
SECRET_NAME_RE: str = r"^[a-z][a-z0-9_]*$"
_NAME_RE = re.compile(SECRET_NAME_RE)

# Directives that take a non-empty locator payload
_REQUIRES_LOCATOR: frozenset[str] = frozenset(
    {"ASK_VAULT", "GEN_TO_VAULT", "GEN_LOCAL", "ASK_EXTERNAL", "ASK_FILE"}
)
# GEN_EPHEMERAL must NOT have a payload

# S4.2 — only ASK_VAULT supports the #field selector
_VAULT_FIELD_DIRECTIVES: frozenset[str] = frozenset({"ASK_VAULT"})

# S4.4 — allowed inline-table keys (beyond 'directive')
_ALLOWED_INLINE_KEYS: frozenset[str] = frozenset({"directive", "expose_env", "mode", "uid"})

# S4.7 / S12 — reserved extension keys (reject with a targeted message)
_RESERVED_EXTENSION_KEYS: frozenset[str] = frozenset({"length", "charset", "transform"})

# Regex to detect a directive string anywhere (used by find_misplaced, S4.5).
# Includes the withdrawn v1 names so stale ASK_VAULT_ONCE/DERIVE values outside
# secrets tables are flagged too instead of flowing through as plain data.
_DIRECTIVE_PREFIX_RE = re.compile(
    r"^(?:ASK_VAULT|ASK_VAULT_ONCE|GEN_TO_VAULT|GEN_LOCAL|ASK_EXTERNAL"
    r"|ASK_FILE|GEN_EPHEMERAL|DERIVE)\b"
)


# ---------------------------------------------------------------------------
# SecretSpec
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SecretSpec:
    """Parsed representation of a single secret declaration.

    Attributes
    ----------
    name        : TOML key; the compose secret name and /run/secrets/<name> (S4.6).
    kind        : one of the six DIRECTIVES.
    locator     : provider path/key; None for GEN_EPHEMERAL.
    field       : Vault #field selector (ASK_VAULT only, S4.15); else None.
    expose_env  : optional ENV_NAME to inject into compose process env (S4.19).
    mode        : secret-file mode string, default "0440" (S4.10).
    uid         : optional owner UID override (S4.10).
    table_path  : dotted path of the secrets table this spec came from (S4.7).
    """

    name: str
    kind: str
    locator: str | None
    field: str | None = dc_field(default=None)
    expose_env: str | None = dc_field(default=None)
    mode: str = dc_field(default="0440")
    uid: int | None = dc_field(default=None)
    table_path: str = dc_field(default="")


# ---------------------------------------------------------------------------
# parse_value
# ---------------------------------------------------------------------------

def parse_value(name: str, value: Any, table_path: str) -> SecretSpec:
    """Parse one secret-table entry into a SecretSpec.

    Parameters
    ----------
    name        : the TOML key (secret name).
    value       : string directive or inline-table dict (S4.4).
    table_path  : dotted path of the enclosing secrets table (for error messages).

    Returns
    -------
    SecretSpec

    Raises
    ------
    ValueError  : any grammar violation; message includes the spec ID and context.
    """
    ctx = f"secret '{name}' in table '{table_path}'"

    # --- S4.6: validate secret name ---
    if not _NAME_RE.match(name):
        raise ValueError(
            f"[S4.6] Invalid secret name {name!r} in table '{table_path}': "
            f"must match {SECRET_NAME_RE}"
        )

    # --- Normalise to directive string + option dict ---
    if isinstance(value, str):
        directive_str = value
        options: dict[str, Any] = {}
    elif isinstance(value, dict):
        # S4.4 inline table
        raw = dict(value)
        directive_str = raw.pop("directive", None)
        if directive_str is None:
            raise ValueError(
                f"[S4.4] Inline table for {ctx} is missing required key 'directive'"
            )
        if not isinstance(directive_str, str):
            raise ValueError(
                f"[S4.4] 'directive' key for {ctx} must be a string, "
                f"got {type(directive_str).__name__!r}"
            )
        # Check for reserved extension keys before unknown-key check
        for k in raw:
            if k in _RESERVED_EXTENSION_KEYS:
                raise ValueError(
                    f"[S4.7] Option '{k}' for {ctx} is reserved, not yet specified"
                )
        # Check for unknown keys
        unknown = set(raw.keys()) - (_ALLOWED_INLINE_KEYS - {"directive"})
        if unknown:
            raise ValueError(
                f"[S4.4] Unknown inline-table key(s) {sorted(unknown)} for {ctx}; "
                f"allowed: {sorted(_ALLOWED_INLINE_KEYS - {'directive'})}"
            )
        options = raw
    else:
        raise ValueError(
            f"[S4.4] Value for {ctx} must be a string or inline table, "
            f"got {type(value).__name__!r}"
        )

    # --- Parse directive string: VERB:payload or VERB ---
    directive_str = directive_str.strip()

    # Check withdrawn names first (before the colon split)
    directive_upper = directive_str.split(":")[0]
    if directive_upper in _WITHDRAWN:
        raise ValueError(_WITHDRAWN[directive_upper] + f" (in {ctx})")

    # Split VERB:payload
    if ":" in directive_str:
        verb, payload = directive_str.split(":", 1)
    else:
        verb = directive_str
        payload = ""

    verb = verb.strip()

    # Validate verb against known directives
    if verb not in DIRECTIVES:
        # Unknown verb — give a helpful message
        raise ValueError(
            f"[S4.2] Unknown directive '{verb}' for {ctx}; "
            f"valid directives: {', '.join(DIRECTIVES)}"
        )

    # --- GEN_EPHEMERAL must have no payload ---
    # Any colon in the directive string signals a payload attempt (even "GEN_EPHEMERAL:")
    if verb == "GEN_EPHEMERAL":
        if ":" in directive_str:
            raise ValueError(
                f"[S4.2] 'GEN_EPHEMERAL' takes no payload for {ctx}; "
                f"got {directive_str!r}"
            )
        locator: str | None = None
        vault_field: str | None = None
    else:
        # --- Directives that require a non-empty locator ---
        if verb in _REQUIRES_LOCATOR:
            # Extract optional #field from payload (ASK_VAULT only)
            vault_field = None
            if "#" in payload:
                locator_part, vault_field = payload.split("#", 1)
                if verb not in _VAULT_FIELD_DIRECTIVES:
                    raise ValueError(
                        f"[S4.2] '#field' selector is only valid for ASK_VAULT, "
                        f"not '{verb}' for {ctx}"
                    )
                if not vault_field:
                    raise ValueError(
                        f"[S4.2] Empty '#field' selector in directive for {ctx}"
                    )
                locator = locator_part
            else:
                locator = payload
                vault_field = None

            if not locator:
                raise ValueError(
                    f"[S4.2] Directive '{verb}' requires a non-empty locator "
                    f"('{verb}:<path>') for {ctx}"
                )
        else:
            locator = None
            vault_field = None

    # --- Extract inline-table options ---
    expose_env: str | None = options.get("expose_env", None)
    mode: str = options.get("mode", "0440")
    uid_raw = options.get("uid", None)
    uid: int | None = None
    if uid_raw is not None:
        if not isinstance(uid_raw, int):
            raise ValueError(
                f"[S4.4] 'uid' for {ctx} must be an integer, "
                f"got {type(uid_raw).__name__!r}"
            )
        uid = uid_raw

    return SecretSpec(
        name=name,
        kind=verb,
        locator=locator,
        field=vault_field,
        expose_env=expose_env,
        mode=mode,
        uid=uid,
        table_path=table_path,
    )


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------

def discover(stack_root_key: str, stack_config: dict[str, Any]) -> list[SecretSpec]:
    """Walk all ``secrets`` tables under *stack_root_key* and parse every entry.

    S4.1 — secrets tables are recognized only inside the stack root key.
    S4.6 — secret names must be unique across the whole stack.

    Parameters
    ----------
    stack_root_key : the single non-reserved top-level TOML key (S3.5).
    stack_config   : the fully-merged stack config dict.

    Returns
    -------
    list[SecretSpec] — ordered by table_path, then key order within each table.

    Raises
    ------
    ValueError : bad directive, bad name pattern, or duplicate name across tables.
    """
    root = stack_config.get(stack_root_key)
    if root is None or not isinstance(root, dict):
        return []

    specs: list[SecretSpec] = []
    seen: dict[str, str] = {}  # name -> table_path where first seen

    def _walk(node: dict[str, Any], dotted_path: str) -> None:
        for key, subtable in node.items():
            if not isinstance(subtable, dict):
                continue
            child_path = f"{dotted_path}.{key}" if dotted_path else key
            if key == "secrets":
                # This is a secrets table — parse all entries
                for entry_name, entry_value in subtable.items():
                    spec = parse_value(entry_name, entry_value, child_path)
                    # S4.6 uniqueness
                    if entry_name in seen:
                        raise ValueError(
                            f"[S4.6] duplicate secret name '{entry_name}': "
                            f"first declared in '{seen[entry_name]}', "
                            f"also in '{child_path}'"
                        )
                    seen[entry_name] = child_path
                    specs.append(spec)
            else:
                # Recurse into non-secrets sub-tables
                _walk(subtable, child_path)

    _walk(root, stack_root_key)
    return specs


# ---------------------------------------------------------------------------
# find_misplaced
# ---------------------------------------------------------------------------

def find_misplaced(
    config: dict[str, Any],
    stack_root_key: str | None = None,
) -> list[tuple[str, Any]]:
    """Find directive strings or secrets tables in disallowed locations.

    S4.5 — a string matching the directive prefix regex outside a secrets
    table must be flagged.
    S4.1 — when *stack_root_key* is given, a ``secrets`` table found outside
    that key is also a violation.

    Parameters
    ----------
    config          : the full config dict (global + stack merged).
    stack_root_key  : when provided, flag any ``secrets`` table outside it.

    Returns
    -------
    list of (dotted_path, value) tuples — each is a violation.
    """
    violations: list[tuple[str, Any]] = []

    def _walk(node: Any, path: str, inside_stack_root: bool, inside_secrets: bool) -> None:
        if isinstance(node, dict):
            for key, val in node.items():
                child_path = f"{path}.{key}" if path else key
                child_inside_root = inside_stack_root or (
                    stack_root_key is not None and key == stack_root_key and not path
                )

                if key == "secrets" and isinstance(val, dict):
                    # S4.1: secrets table outside the stack root key is a violation
                    if stack_root_key is not None and not inside_stack_root:
                        violations.append((child_path, val))
                    else:
                        # Valid location — walk inside but mark as inside secrets
                        _walk(val, child_path, child_inside_root, inside_secrets=True)
                    continue

                # Recurse into non-secrets sub-tables/values
                _walk(val, child_path, child_inside_root, inside_secrets)

        elif isinstance(node, list):
            for i, item in enumerate(node):
                _walk(item, f"{path}[{i}]", inside_stack_root, inside_secrets)

        elif isinstance(node, str):
            # S4.5: directive string outside a secrets table
            if not inside_secrets and _DIRECTIVE_PREFIX_RE.match(node):
                violations.append((path, node))

    _walk(config, "", inside_stack_root=False, inside_secrets=False)
    return violations
