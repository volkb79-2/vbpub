"""Minimal v2 pre_compose hook example (SPEC S9).

Hook point: pre_compose — runs after secrets are materialized, before
docker-compose.yml is rendered (S8.3 step 11).

Signature (S9.1):  run(config: dict, ctx) -> dict

Return value (S9.4): a dict where every value is a dict containing
at least 'value'.  Two optional keys control side-effects:

  apply_to_config: True   → merge the value into the in-memory config
                             dict at the dotted path (the key).
  persist: 'state'        → write the value under [state].<key> in
                             the stack's ciu.toml (atomic tmp + replace).

Return None or {} for a no-op hook.

ctx.secret_file(name) returns the Path of a secret's store file (S9.3).

Live examples in the test-repo:
  test-repo/applications/app-config/pre_compose_app.py
"""
from __future__ import annotations


def run(config: dict, ctx) -> dict:
    """Example: inject a computed value into the in-memory config."""
    # Read something from config to illustrate the pattern.
    project = config.get("deploy", {}).get("project_name", "unknown")

    return {
        # Dotted key path → applied to config["deploy"]["computed_tag"]
        "deploy.computed_tag": {
            "value": f"{project}-ready",
            "apply_to_config": True,
        },
    }
