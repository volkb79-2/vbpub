"""Minimal v2 post_compose hook example (SPEC S9).

Hook point: post_compose — runs after docker compose up succeeds (S8.3 step 17).

Signature (S9.1):  run(config: dict, ctx) -> dict

Return value (S9.4): a dict where every value is a dict containing
at least 'value'.  Two persist destinations exist, and choosing correctly
between them is the whole point of this example:

  * ``persist:'state'`` writes under ``[state]`` in the stack's ciu.toml. That
    table is ordinarily rendered and ordinarily readable, so it is for
    NON-SECRET facts only — booleans, counters, URIs, timestamps. A
    secret-shaped key there is refused outright by `ciu check` (S3.4a).
  * ``persist:'secret'`` (S9.4a) writes into the stack's secret store,
    ``<stack>/.ciu/secrets/<name>``, at mode 0440 — the same machinery a
    directive-declared secret uses. Use it for a credential a hook MINTS,
    which no directive could have expressed in advance (the canonical case is
    a real Vault's ``operator init`` output).

This example shows the second, because a runtime token is exactly that case.
Note what it may NOT do: ``apply_to_config`` alongside ``persist:'secret'`` is
a contract violation (S9.4a) — it would put the raw value in front of every
later template and hook. Read it back with ``ctx.secret_file(name)`` instead.

ctx.secret_file(name) returns the Path of a secret's store file (S9.3).

Live example in the test-repo:
  test-repo/infra/vault/post_compose_vault.py
"""
from __future__ import annotations


def run(config: dict, ctx) -> dict:
    """Example: persist a minted runtime token into the stack's secret store."""
    project = config.get("deploy", {}).get("project_name", "unknown")
    env_tag = config.get("deploy", {}).get("environment_tag", "dev")

    return {
        # 'root_token' → written to <stack>/.ciu/secrets/root_token, mode 0440
        "root_token": {
            "value": f"placeholder-{project}-{env_tag}",
            "persist": "secret",
        },
    }
