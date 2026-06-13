#!/usr/bin/env python3
"""app-config — pre_compose hook (S9.1 / S9.4).

Runs at S8.3 step 11 (after secrets are materialized, before configfiles render).
Demonstrates the apply_to_config path: the returned value is merged into the
in-memory config at the dotted key ``app_config.runtime_note`` and is therefore
visible to config.toml.j2 (rendered at step 12) as
``{{ app_config.runtime_note }}``.

This is a PLAIN (non-secret) value, so surfacing it through config is correct;
secret values must never be returned this way — they flow through the secret
store and ``secret()`` (S4.21 / S5.4).
"""

from __future__ import annotations


def run(config: dict, ctx) -> dict:
    _ = config
    _ = ctx
    return {
        "app_config.runtime_note": {
            "value": "set-by-hook",
            "apply_to_config": True,
        }
    }
