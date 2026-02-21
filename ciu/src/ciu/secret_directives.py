"""Secret directive classification helpers.

These helpers define which directives are Vault-backed versus local/external.
"""
from __future__ import annotations

from typing import Literal

DirectiveClass = Literal["vault", "local", "external", "ephemeral", "derived", "unknown"]

VAULT_DIRECTIVE_PREFIXES = (
    "GEN_TO_VAULT:",
    "ASK_VAULT:",
    "ASK_VAULT_ONCE:",
)

LOCAL_DIRECTIVE_PREFIXES = (
    "GEN_LOCAL:",
)

EXTERNAL_DIRECTIVE_PREFIXES = (
    "ASK_EXTERNAL:",
)

DERIVED_DIRECTIVE_PREFIXES = (
    "DERIVE:",
)

EPHEMERAL_DIRECTIVES = (
    "GEN_EPHEMERAL",
)


def classify_directive(value: str) -> DirectiveClass:
    """Classify a directive value into a directive category.

    Args:
        value: Raw directive string (e.g., "GEN_TO_VAULT:shared/redis_password").

    Returns:
        DirectiveClass: One of "vault", "local", "external", "ephemeral", "derived", "unknown".
    """
    value = (value or "").strip()
    if not value:
        return "unknown"

    if value in EPHEMERAL_DIRECTIVES:
        return "ephemeral"

    for prefix in VAULT_DIRECTIVE_PREFIXES:
        if value.startswith(prefix):
            return "vault"

    for prefix in LOCAL_DIRECTIVE_PREFIXES:
        if value.startswith(prefix):
            return "local"

    for prefix in EXTERNAL_DIRECTIVE_PREFIXES:
        if value.startswith(prefix):
            return "external"

    for prefix in DERIVED_DIRECTIVE_PREFIXES:
        if value.startswith(prefix):
            return "derived"

    return "unknown"


def is_vault_directive(value: str) -> bool:
    """Return True if the directive is Vault-backed."""
    return classify_directive(value) == "vault"
