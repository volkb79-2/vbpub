"""CIU-42 / S13.6 — cross-profile ASK_VAULT producers are declarable and enforced.

A stack's ``ASK_VAULT`` directive may declare ``produced_by = "<profile>"``:
the value at its Vault path is provisioned by ANOTHER profile's deployment.
A partial selection excluding that producer used to sail through every
preflight and fail at the consuming stack's materialization with only the
bare path name ([S4.2]); it now refuses UPFRONT, naming producer profile,
path, and both remedies.

Oracles (from the filing):
- A partial selection missing a declared producer refuses pre-deploy, naming
  producer profile + path + the seeding alternative.
- A selection including the producer profile is unaffected.
- An undeclared ASK_VAULT keeps today's behavior exactly.
- Controlled wrong implementation: dropping the declaration lookup regresses
  to the bare-path refusal — ``test_controlled_wrong_...`` proves the refusal
  comes from the declaration, not from the path itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402
from ciu.deploy_pkg import profiles as profiles_pkg  # noqa: E402
from ciu.secrets import directives  # noqa: E402


# ---------------------------------------------------------------------------
# Grammar (S4.4 inline key, S13.6 kind restriction)
# ---------------------------------------------------------------------------


def test_produced_by_parses_on_ask_vault():
    spec = directives.parse_value(
        "bootstrap_token",
        {"directive": "ASK_VAULT:authentik/bootstrap_token", "produced_by": "identity"},
        "controller.secrets",
    )
    assert spec.kind == "ASK_VAULT"
    assert spec.locator == "authentik/bootstrap_token"
    assert spec.produced_by == "identity"


def test_bare_string_directive_has_no_producer():
    spec = directives.parse_value(
        "bootstrap_token", "ASK_VAULT:authentik/bootstrap_token", "controller.secrets"
    )
    assert spec.produced_by is None


def test_produced_by_rejected_on_non_vault_kind():
    with pytest.raises(ValueError, match=r"\[S13.6\].*only valid for ASK_VAULT"):
        directives.parse_value(
            "api_key",
            {"directive": "GEN_TO_VAULT:vault/api/key", "produced_by": "identity"},
            "app.secrets",
        )


@pytest.mark.parametrize("bad", ["", "   ", 42])
def test_produced_by_must_be_non_empty_string(bad):
    with pytest.raises(ValueError, match=r"\[S13.6\].*non-empty"):
        directives.parse_value(
            "bootstrap_token",
            {"directive": "ASK_VAULT:authentik/bootstrap_token", "produced_by": bad},
            "controller.secrets",
        )


# ---------------------------------------------------------------------------
# Preflight (S13.6 refusal contract)
# ---------------------------------------------------------------------------


def _profile_config() -> dict:
    return {
        "deploy": {
            "profiles": {
                "core": {"phases": ["phase_1"]},
                "db": {"phases": ["phase_2"]},
                "identity": {"phases": ["phase_3"]},
            }
        }
    }


def _rendered(*, produced_by: str | None = None) -> dict:
    secret = (
        {"directive": "ASK_VAULT:authentik/bootstrap_token", "produced_by": produced_by}
        if produced_by
        else "ASK_VAULT:authentik/bootstrap_token"
    )
    return {
        "apps/controller": {
            "controller": {
                "secrets": {"bootstrap_token": secret},
            }
        }
    }


_SELECTION = [{"path": "apps/controller", "phase_num": 1}]


def test_partial_selection_missing_producer_refuses_naming_everything():
    """Oracle 1: the refusal names producer profile, path, stack, selection,
    and both remedies — never just the bare path."""
    profile = profiles_pkg.Profile(name="core,db", config=_profile_config())

    with pytest.raises(ValueError) as excinfo:
        deploy.producer_preflight(profile, _SELECTION, _rendered(produced_by="identity"))

    message = str(excinfo.value)
    assert "S13.6" in message
    assert "'identity'" in message
    assert "authentik/bootstrap_token" in message
    assert "apps/controller" in message
    assert "(core,db)" in message
    assert "--profile core,db,identity" in message
    assert "seed the path" in message


def test_selection_including_producer_passes():
    """Oracle 2: selecting the producer changes nothing."""
    profile = profiles_pkg.Profile(name="core,identity", config=_profile_config())
    deploy.producer_preflight(profile, _SELECTION, _rendered(produced_by="identity"))


def test_undeclared_ask_vault_keeps_today_behavior():
    """Oracle 3: no declaration → no refusal under a partial selection."""
    profile = profiles_pkg.Profile(name="core,db", config=_profile_config())
    deploy.producer_preflight(profile, _SELECTION, _rendered())


def test_default_all_phases_selection_never_refuses():
    profile = profiles_pkg.Profile(name=None, config=_profile_config())
    deploy.producer_preflight(profile, _SELECTION, _rendered(produced_by="identity"))


def test_unknown_producer_profile_is_a_configuration_error():
    """A typo'd declaration must fail loudly even when it would 'pass' the
    selection check (default selection deploys everything)."""
    profile = profiles_pkg.Profile(name=None, config=_profile_config())
    rendered = _rendered(produced_by="identiy")  # typo

    with pytest.raises(ValueError, match=r"produced_by = 'identiy'.*not a defined profile"):
        deploy.producer_preflight(profile, _SELECTION, rendered)


def test_shipped_stack_is_skipped():
    class FakePhases:
        @staticmethod
        def service_shipped(service):
            return service.get("shipped", False)

    import importlib

    original = deploy.phases_pkg
    try:
        deploy.phases_pkg = FakePhases
        profile = profiles_pkg.Profile(name="core,db", config=_profile_config())
        rendered = {
            "vendor/vault": {
                "vault": {
                    "secrets": {
                        "token": {
                            "directive": "ASK_VAULT:vault/token",
                            "produced_by": "identity",
                        }
                    }
                }
            }
        }
        selection = [{"path": "vendor/vault", "service": {"shipped": True}}]
        deploy.producer_preflight(profile, selection, rendered)
    finally:
        deploy.phases_pkg = original


def test_multiple_missing_producers_are_listed_together():
    """One run names EVERY unmet producer — not just the first."""
    rendered = {
        "apps/controller": {
            "controller": {
                "secrets": {
                    "bootstrap_token": {
                        "directive": "ASK_VAULT:authentik/bootstrap_token",
                        "produced_by": "identity",
                    },
                    "webhook": {
                        "directive": "ASK_VAULT:integrations/webhook",
                        "produced_by": "identity",
                    },
                }
            }
        },
        "apps/web": {
            "web": {
                "secrets": {
                    "session": {
                        "directive": "ASK_VAULT:consul/session",
                        "produced_by": "db",
                    }
                }
            }
        },
    }
    profile = profiles_pkg.Profile(name="core", config=_profile_config())
    selection = [{"path": rel, "phase_num": 1} for rel in rendered]

    with pytest.raises(ValueError) as excinfo:
        deploy.producer_preflight(profile, selection, rendered)

    message = str(excinfo.value)
    assert message.count("provisioned by profile") == 3
    assert "'apps/web'" in message


def test_controlled_wrong_declaration_lookup_regresses_to_pass(monkeypatch):
    """Controlled wrong implementation: dropping the declaration lookup (the
    pre-CIU-42 behavior) must NOT refuse here — proving the refusal comes
    from the produced_by declaration itself and would regress to the bare-
    path [S4.2] failure at materialization time without it."""
    import ciu.secrets.directives as directives_mod

    real_parse = directives_mod.parse_value

    def producer_blind_parse(name, value, table_path):
        spec = real_parse(name, value, table_path)
        return (
            type(spec)(
                name=spec.name, kind=spec.kind, locator=spec.locator,
                field=spec.field, expose_env=spec.expose_env,
                consumed_by=spec.consumed_by, mode=spec.mode, uid=spec.uid,
                table_path=spec.table_path,
            )  # produced_by deliberately dropped — the regression
        )

    monkeypatch.setattr(
        deploy.secret_directives, "parse_value", producer_blind_parse
    )
    profile = profiles_pkg.Profile(name="core,db", config=_profile_config())

    # The regression passes the preflight — which is exactly the defect: the
    # stack then fails at materialization with only "[S4.2] ... absent".
    deploy.producer_preflight(profile, _SELECTION, _rendered(produced_by="identity"))


def test_selection_entry_missing_from_rendered_is_skipped():
    """A selection entry whose render is absent contributes nothing — no
    crash, no refusal (mirrors vault_preflight's tolerance)."""
    profile = profiles_pkg.Profile(name="core,db", config=_profile_config())
    deploy.producer_preflight(
        profile,
        [{"path": "apps/ghost", "phase_num": 1}],
        rendered={},  # nothing rendered for it
    )
