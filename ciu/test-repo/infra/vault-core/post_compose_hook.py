#!/usr/bin/env python3
"""Demo post_compose hook (v2): simulate Vault initialization.

S9.1 — ``run(config, ctx) -> dict``. S9.4 — structured return; the value is
applied to the in-memory config and persisted under the stack's ``[state]``
(the only persistable destination in v2).
"""

from __future__ import annotations


def run(config: dict, ctx) -> dict:
    _ = config
    _ = ctx
    return {
        "vault_initialized": {
            "value": True,
            "apply_to_config": True,
            "persist": "state",
        }
    }
