"""Tests for src/ciu/secrets/directives.py — CIU v2 secret-directive parser.

Normative contract: docs/SPEC.md §S4 (S4.1–S4.7).
Each test documents which spec requirement it exercises.
"""
from __future__ import annotations

import pytest

from ciu.secrets import (
    DIRECTIVES,
    SECRET_NAME_RE,
    SecretSpec,
    discover,
    find_misplaced,
    parse_value,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mk_table(name: str, kind: str, locator: str | None = None, **kw: object) -> SecretSpec:
    """Convenience: parse a string directive and check the kind."""
    directive = f"{kind}:{locator}" if locator else kind
    spec = parse_value(name, directive, "mystack.secrets")
    assert spec.kind == kind
    return spec


# ---------------------------------------------------------------------------
# S4.2 — the six directives (string form)
# ---------------------------------------------------------------------------

class TestParseStringDirectives:
    """S4.2 — parse each of the six directives from their string form."""

    def test_ask_vault_s4_2(self) -> None:
        spec = parse_value("db_password", "ASK_VAULT:secret/data/db", "stack.secrets")
        assert spec.name == "db_password"
        assert spec.kind == "ASK_VAULT"
        assert spec.locator == "secret/data/db"
        assert spec.field is None
        assert spec.table_path == "stack.secrets"

    def test_gen_to_vault_s4_2(self) -> None:
        spec = parse_value("redis_password", "GEN_TO_VAULT:shared/redis_password", "stack.secrets")
        assert spec.kind == "GEN_TO_VAULT"
        assert spec.locator == "shared/redis_password"

    def test_gen_local_s4_2(self) -> None:
        spec = parse_value("registry_pw", "GEN_LOCAL:local/registry_password", "stack.secrets")
        assert spec.kind == "GEN_LOCAL"
        assert spec.locator == "local/registry_password"

    def test_ask_external_s4_2(self) -> None:
        spec = parse_value("shared_token", "ASK_EXTERNAL:PWMCP_SHARED_TOKEN", "stack.secrets")
        assert spec.kind == "ASK_EXTERNAL"
        assert spec.locator == "PWMCP_SHARED_TOKEN"

    def test_ask_file_s4_2(self) -> None:
        spec = parse_value("tls_cert", "ASK_FILE:/etc/certs/tls.crt", "stack.secrets")
        assert spec.kind == "ASK_FILE"
        assert spec.locator == "/etc/certs/tls.crt"

    def test_gen_ephemeral_s4_2(self) -> None:
        spec = parse_value("session_key", "GEN_EPHEMERAL", "stack.secrets")
        assert spec.kind == "GEN_EPHEMERAL"
        assert spec.locator is None
        assert spec.field is None


# ---------------------------------------------------------------------------
# S4.2/S4.15 — #field selector on ASK_VAULT
# ---------------------------------------------------------------------------

class TestVaultFieldSelector:
    """S4.2/S4.15 — #field is valid on ASK_VAULT, rejected elsewhere."""

    def test_ask_vault_field_ok_s4_15(self) -> None:
        spec = parse_value("db_pw", "ASK_VAULT:secret/data/db#password", "stack.secrets")
        assert spec.kind == "ASK_VAULT"
        assert spec.locator == "secret/data/db"
        assert spec.field == "password"

    def test_gen_to_vault_field_rejected_s4_2(self) -> None:
        with pytest.raises(ValueError, match=r"\[S4\.2\].*field.*ASK_VAULT"):
            parse_value("x", "GEN_TO_VAULT:path/to/secret#value", "stack.secrets")

    def test_gen_local_field_rejected_s4_2(self) -> None:
        with pytest.raises(ValueError, match=r"\[S4\.2\].*field.*ASK_VAULT"):
            parse_value("x", "GEN_LOCAL:localname#field", "stack.secrets")

    def test_ask_external_field_rejected_s4_2(self) -> None:
        with pytest.raises(ValueError, match=r"\[S4\.2\].*field.*ASK_VAULT"):
            parse_value("x", "ASK_EXTERNAL:KEY#field", "stack.secrets")

    def test_ask_file_field_rejected_s4_2(self) -> None:
        with pytest.raises(ValueError, match=r"\[S4\.2\].*field.*ASK_VAULT"):
            parse_value("x", "ASK_FILE:/path/to/file#field", "stack.secrets")


# ---------------------------------------------------------------------------
# S4.2 — GEN_EPHEMERAL must not accept a payload
# ---------------------------------------------------------------------------

class TestGenEphemeralNoPayload:
    """S4.2 — GEN_EPHEMERAL:<anything> is an error."""

    def test_gen_ephemeral_with_payload_rejected_s4_2(self) -> None:
        with pytest.raises(ValueError, match=r"\[S4\.2\].*GEN_EPHEMERAL.*no payload"):
            parse_value("nonce", "GEN_EPHEMERAL:extra", "stack.secrets")

    def test_gen_ephemeral_with_colon_empty_rejected_s4_2(self) -> None:
        # Even "GEN_EPHEMERAL:" (empty payload after colon) is wrong —
        # the colon signals a payload attempt
        with pytest.raises(ValueError, match=r"\[S4\.2\].*GEN_EPHEMERAL.*no payload"):
            parse_value("nonce", "GEN_EPHEMERAL:", "stack.secrets")


# ---------------------------------------------------------------------------
# S4.2 — directives that require a locator must have one
# ---------------------------------------------------------------------------

class TestMissingLocator:
    """S4.2 — missing locator on directives that require one."""

    @pytest.mark.parametrize("verb", [
        "ASK_VAULT", "GEN_TO_VAULT", "GEN_LOCAL", "ASK_EXTERNAL", "ASK_FILE"
    ])
    def test_missing_locator_s4_2(self, verb: str) -> None:
        with pytest.raises(ValueError, match=r"\[S4\.2\].*requires.*locator"):
            parse_value("x", f"{verb}:", "stack.secrets")


# ---------------------------------------------------------------------------
# S4.4 — inline-table form with options
# ---------------------------------------------------------------------------

class TestInlineTableForm:
    """S4.4 — inline table {directive=..., expose_env=..., mode=..., uid=...}."""

    def test_inline_with_expose_env_s4_4(self) -> None:
        spec = parse_value(
            "redis_pw",
            {"directive": "GEN_TO_VAULT:shared/redis", "expose_env": "REDIS_PASSWORD"},
            "stack.secrets",
        )
        assert spec.kind == "GEN_TO_VAULT"
        assert spec.locator == "shared/redis"
        assert spec.expose_env == "REDIS_PASSWORD"

    def test_inline_with_mode_and_uid_s4_4(self) -> None:
        spec = parse_value(
            "pg_pw",
            {"directive": "ASK_VAULT:secret/pg", "mode": "0400", "uid": 999},
            "stack.secrets",
        )
        assert spec.mode == "0400"
        assert spec.uid == 999

    def test_inline_all_options_s4_4(self) -> None:
        spec = parse_value(
            "consul_tok",
            {
                "directive": "GEN_TO_VAULT:consul/token",
                "expose_env": "CONSUL_TOKEN",
                "mode": "0440",
                "uid": 0,
            },
            "stack.secrets",
        )
        assert spec.expose_env == "CONSUL_TOKEN"
        assert spec.uid == 0

    def test_inline_missing_directive_key_s4_4(self) -> None:
        with pytest.raises(ValueError, match=r"\[S4\.4\].*missing.*directive"):
            parse_value("x", {"expose_env": "FOO"}, "stack.secrets")

    def test_inline_unknown_key_s4_4(self) -> None:
        with pytest.raises(ValueError, match=r"\[S4\.4\].*Unknown.*inline-table key"):
            parse_value("x", {"directive": "GEN_EPHEMERAL", "unknown_key": "val"}, "stack.secrets")

    def test_inline_ask_vault_with_field_s4_4(self) -> None:
        spec = parse_value(
            "db_pw",
            {"directive": "ASK_VAULT:secret/db#password", "mode": "0440"},
            "stack.secrets",
        )
        assert spec.field == "password"
        assert spec.locator == "secret/db"


# ---------------------------------------------------------------------------
# S4.3 — withdrawn directives with targeted error messages
# ---------------------------------------------------------------------------

class TestWithdrawnDirectives:
    """S4.3 — withdrawn v1 names must produce targeted error messages."""

    def test_ask_vault_once_withdrawn_s4_3(self) -> None:
        with pytest.raises(ValueError, match=r"\[S4\.3\].*ASK_VAULT_ONCE.*withdrawn.*GEN_TO_VAULT"):
            parse_value("x", "ASK_VAULT_ONCE:path/secret", "stack.secrets")

    def test_derive_withdrawn_s4_3(self) -> None:
        with pytest.raises(ValueError, match=r"\[S4\.3\].*DERIVE.*withdrawn.*secret\(\)"):
            parse_value("x", "DERIVE:sha256:source", "stack.secrets")

    def test_ask_vault_once_inline_withdrawn_s4_3(self) -> None:
        with pytest.raises(ValueError, match=r"\[S4\.3\].*ASK_VAULT_ONCE.*withdrawn"):
            parse_value("x", {"directive": "ASK_VAULT_ONCE:path"}, "stack.secrets")


# ---------------------------------------------------------------------------
# S4.6 — bad name pattern
# ---------------------------------------------------------------------------

class TestBadNamePattern:
    """S4.6 — secret names must match [a-z][a-z0-9_]*."""

    @pytest.mark.parametrize("bad_name", [
        "MySecret",         # uppercase
        "_secret",          # leading underscore
        "1secret",          # leading digit
        "se-cret",          # hyphen
        "SECRET",           # all caps
        "",                 # empty
        "se cret",          # space
    ])
    def test_bad_name_s4_6(self, bad_name: str) -> None:
        with pytest.raises(ValueError, match=r"\[S4\.6\]"):
            parse_value(bad_name, "GEN_EPHEMERAL", "stack.secrets")

    def test_valid_names_s4_6(self) -> None:
        for good in ("x", "my_secret", "a1b2c3", "abc_def_123"):
            spec = parse_value(good, "GEN_EPHEMERAL", "stack.secrets")
            assert spec.name == good


# ---------------------------------------------------------------------------
# S4.7 / S12 — reserved extension options must be rejected
# ---------------------------------------------------------------------------

class TestReservedOptions:
    """S4.7 / S12 — length/charset/transform are reserved, not yet specified."""

    @pytest.mark.parametrize("reserved_key", ["length", "charset", "transform"])
    def test_reserved_option_rejected_s4_7(self, reserved_key: str) -> None:
        with pytest.raises(ValueError, match=r"\[S4\.7\].*reserved, not yet specified"):
            parse_value(
                "x",
                {"directive": "GEN_EPHEMERAL", reserved_key: "value"},
                "stack.secrets",
            )


# ---------------------------------------------------------------------------
# S4.4 — unknown directive verb
# ---------------------------------------------------------------------------

class TestUnknownDirective:
    """S4.2 — completely unknown directive verb."""

    def test_unknown_verb_s4_2(self) -> None:
        with pytest.raises(ValueError, match=r"\[S4\.2\].*Unknown directive"):
            parse_value("x", "MAKE_IT_UP:foo", "stack.secrets")


# ---------------------------------------------------------------------------
# discover() — S4.1 + S4.6
# ---------------------------------------------------------------------------

class TestDiscover:
    """S4.1/S4.6 — discover() walks secrets tables under the root key."""

    def test_root_level_secrets_table_s4_1(self) -> None:
        """[redis_core.secrets] is found."""
        config = {
            "redis_core": {
                "secrets": {
                    "redis_pw": "GEN_TO_VAULT:shared/redis_password",
                }
            }
        }
        specs = discover("redis_core", config)
        assert len(specs) == 1
        assert specs[0].name == "redis_pw"
        assert specs[0].kind == "GEN_TO_VAULT"
        assert specs[0].table_path == "redis_core.secrets"

    def test_service_level_secrets_table_s4_1(self) -> None:
        """[controller.controller.secrets] is found."""
        config = {
            "controller": {
                "controller": {
                    "secrets": {
                        "api_key": "ASK_EXTERNAL:API_KEY",
                    }
                }
            }
        }
        specs = discover("controller", config)
        assert len(specs) == 1
        assert specs[0].name == "api_key"
        assert specs[0].table_path == "controller.controller.secrets"

    def test_both_root_and_service_level_s4_1(self) -> None:
        """Root-level and service-level secrets tables are both discovered."""
        config = {
            "mystack": {
                "secrets": {
                    "shared_token": "GEN_EPHEMERAL",
                },
                "svc_a": {
                    "secrets": {
                        "svc_token": "ASK_VAULT:secret/svc_a/token",
                    }
                },
            }
        }
        specs = discover("mystack", config)
        names = {s.name for s in specs}
        assert names == {"shared_token", "svc_token"}

    def test_uniqueness_enforced_s4_6(self) -> None:
        """Duplicate name across two secrets tables → ValueError listing both paths."""
        config = {
            "mystack": {
                "secrets": {
                    "db_pw": "GEN_EPHEMERAL",
                },
                "svc_a": {
                    "secrets": {
                        "db_pw": "GEN_TO_VAULT:path/db_pw",  # duplicate!
                    }
                },
            }
        }
        with pytest.raises(ValueError, match=r"\[S4\.6\].*duplicate.*db_pw"):
            discover("mystack", config)

    def test_no_secrets_tables_returns_empty(self) -> None:
        config = {"mystack": {"redis": {"host": "localhost"}}}
        assert discover("mystack", config) == []

    def test_missing_root_key_returns_empty(self) -> None:
        config = {"other_stack": {"secrets": {"x": "GEN_EPHEMERAL"}}}
        assert discover("mystack", config) == []

    def test_secrets_outside_root_key_not_discovered_s4_1(self) -> None:
        """discover() only looks under the stack root — global secrets are not found."""
        config = {
            "global_table": {
                "secrets": {
                    "global_secret": "GEN_EPHEMERAL",
                }
            },
            "mystack": {
                "secrets": {
                    "stack_secret": "GEN_EPHEMERAL",
                }
            },
        }
        specs = discover("mystack", config)
        assert len(specs) == 1
        assert specs[0].name == "stack_secret"


# ---------------------------------------------------------------------------
# find_misplaced() — S4.5 + S4.1
# ---------------------------------------------------------------------------

class TestFindMisplaced:
    """S4.5 — directive strings outside secrets tables are violations."""

    def test_directive_in_non_secrets_table_s4_5(self) -> None:
        """The dstdns consul.token case: GEN_TO_VAULT: in [controller.consul]."""
        config = {
            "controller": {
                "consul": {
                    "token": "GEN_TO_VAULT:consul/controller_token",
                }
            }
        }
        violations = find_misplaced(config)
        assert len(violations) == 1
        path, val = violations[0]
        assert "token" in path
        assert "GEN_TO_VAULT" in val

    def test_no_false_positive_log_level_s4_5(self) -> None:
        """'LOG_LEVEL:INFO' must NOT be flagged (S4.5 exact-prefix check)."""
        config = {
            "myapp": {
                "settings": {
                    "log_level": "LOG_LEVEL:INFO",
                }
            }
        }
        assert find_misplaced(config) == []

    def test_no_false_positive_hkey_s4_5(self) -> None:
        """'HKEY:foo' must NOT be flagged."""
        config = {
            "myapp": {
                "registry": {
                    "hkey": "HKEY:foo",
                }
            }
        }
        assert find_misplaced(config) == []

    def test_directive_in_list_s4_5(self) -> None:
        """Directive string inside a list outside a secrets table is a violation."""
        config = {
            "myapp": {
                "items": ["ASK_VAULT:some/path", "plain_value"],
            }
        }
        violations = find_misplaced(config)
        assert len(violations) == 1
        assert "ASK_VAULT" in violations[0][1]

    def test_directive_inside_secrets_table_is_not_a_violation(self) -> None:
        """Values inside a secrets table are not misplaced."""
        config = {
            "mystack": {
                "secrets": {
                    "db_pw": "ASK_VAULT:secret/db",
                }
            }
        }
        assert find_misplaced(config) == []

    def test_all_six_directives_detected_s4_5(self) -> None:
        """All six directive prefixes trigger a violation when outside secrets."""
        config = {
            "settings": {
                "a": "ASK_VAULT:path",
                "b": "GEN_TO_VAULT:path",
                "c": "GEN_LOCAL:name",
                "d": "ASK_EXTERNAL:KEY",
                "e": "ASK_FILE:/path",
                "f": "GEN_EPHEMERAL",
            }
        }
        violations = find_misplaced(config)
        assert len(violations) == 6

    def test_secrets_table_outside_stack_root_s4_1(self) -> None:
        """S4.1: a secrets table outside the stack root is a violation when root given."""
        config = {
            "global_stuff": {
                "secrets": {
                    "misplaced": "GEN_EPHEMERAL",
                }
            },
            "mystack": {
                "secrets": {
                    "ok_secret": "GEN_EPHEMERAL",
                }
            },
        }
        violations = find_misplaced(config, stack_root_key="mystack")
        # global_stuff.secrets is outside the stack root — violation
        assert any("global_stuff.secrets" in v[0] for v in violations)
        # mystack.secrets is inside the stack root — not a violation
        assert not any("mystack.secrets" in v[0] for v in violations)

    def test_no_stack_root_key_no_secrets_table_violation(self) -> None:
        """Without stack_root_key, misplaced-secrets-table check is skipped."""
        config = {
            "anywhere": {
                "secrets": {
                    "x": "GEN_EPHEMERAL",
                }
            }
        }
        # No stack_root_key → secrets table check not applied, contents are fine
        assert find_misplaced(config) == []

    def test_plain_value_no_violation(self) -> None:
        """Plain non-directive strings anywhere are never flagged."""
        config = {
            "myapp": {
                "host": "localhost",
                "port": 5432,
                "name": "mydb",
            }
        }
        assert find_misplaced(config) == []


# ---------------------------------------------------------------------------
# SecretSpec defaults
# ---------------------------------------------------------------------------

class TestSecretSpecDefaults:
    """Verify default field values on SecretSpec."""

    def test_defaults_s4_10(self) -> None:
        spec = parse_value("session_key", "GEN_EPHEMERAL", "stack.secrets")
        assert spec.mode == "0440"
        assert spec.uid is None
        assert spec.expose_env is None
        assert spec.field is None

    def test_table_path_propagated(self) -> None:
        spec = parse_value("tok", "GEN_EPHEMERAL", "myroot.svc.secrets")
        assert spec.table_path == "myroot.svc.secrets"


# ---------------------------------------------------------------------------
# DIRECTIVES constant and SECRET_NAME_RE
# ---------------------------------------------------------------------------

class TestPublicConstants:
    """Public constants are correct."""

    def test_directives_count_s4_2(self) -> None:
        assert len(DIRECTIVES) == 6
        assert set(DIRECTIVES) == {
            "ASK_VAULT", "GEN_TO_VAULT", "GEN_LOCAL",
            "ASK_EXTERNAL", "ASK_FILE", "GEN_EPHEMERAL",
        }

    def test_secret_name_re_s4_6(self) -> None:
        import re
        pat = re.compile(SECRET_NAME_RE)
        assert pat.match("abc")
        assert pat.match("a1_b2")
        assert not pat.match("ABC")
        assert not pat.match("_abc")
        assert not pat.match("1abc")
