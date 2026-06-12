#!/usr/bin/env python3
"""Demo pre_compose hook (v2): inject a Vault bootstrap token into the config.

S9.1 — provides ``run(config, ctx) -> dict``. S9.4 — structured return only;
``apply_to_config`` makes the value visible to the compose template
(``{{ app_vault.env.VAULT_BOOTSTRAP_TOKEN }}``). This is NOT a secret (plain
demo value), so it is fine to surface via config rather than a secret store.
"""

from __future__ import annotations


def run(config: dict, ctx) -> dict:
    _ = config
    _ = ctx
    return {
        "app_vault.env.VAULT_BOOTSTRAP_TOKEN": {
            "value": "demo-token",
            "apply_to_config": True,
        }
    }
