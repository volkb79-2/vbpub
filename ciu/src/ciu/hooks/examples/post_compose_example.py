"""Minimal v2 post_compose hook example (SPEC S9).

Hook point: post_compose — runs after docker compose up succeeds (S8.3 step 17).

Signature (S9.1):  run(config: dict, ctx) -> dict

Return value (S9.4): a dict where every value is a dict containing
at least 'value'.  Use persist:'state' to write the value under [state]
in the stack's ciu.toml (useful for tokens, URIs produced by compose).

ctx.secret_file(name) returns the Path of a secret's store file (S9.3).

Live example in the test-repo:
  test-repo/infra/vault/post_compose_vault.py
"""
from __future__ import annotations


def run(config: dict, ctx) -> dict:
    """Example: persist a computed runtime value into [state] of ciu.toml."""
    project = config.get("deploy", {}).get("project_name", "unknown")
    env_tag = config.get("deploy", {}).get("environment_tag", "dev")

    return {
        # 'root_token' → written to ciu.toml [state].root_token
        "root_token": {
            "value": f"placeholder-{project}-{env_tag}",
            "persist": "state",
        },
    }
