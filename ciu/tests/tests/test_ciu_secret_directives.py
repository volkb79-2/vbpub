"""Tests for secret directive classification."""
from __future__ import annotations

from ciu.secret_directives import classify_directive, is_vault_directive


def test_classify_directive_vault() -> None:
    assert classify_directive("GEN_TO_VAULT:shared/redis_password") == "vault"
    assert classify_directive("ASK_VAULT:shared/redis_password") == "vault"
    assert classify_directive("ASK_VAULT_ONCE:shared/redis_password") == "vault"


def test_classify_directive_local() -> None:
    assert classify_directive("GEN_LOCAL:local/registry_password") == "local"


def test_classify_directive_external() -> None:
    assert classify_directive("ASK_EXTERNAL:PWMCP_SHARED_TOKEN") == "external"


def test_classify_directive_ephemeral() -> None:
    assert classify_directive("GEN_EPHEMERAL") == "ephemeral"


def test_classify_directive_derived() -> None:
    assert classify_directive("DERIVE:sha256:source") == "derived"


def test_classify_directive_unknown() -> None:
    assert classify_directive("") == "unknown"
    assert classify_directive("random") == "unknown"


def test_is_vault_directive() -> None:
    assert is_vault_directive("GEN_TO_VAULT:shared/redis_password") is True
    assert is_vault_directive("GEN_LOCAL:local/registry_password") is False
