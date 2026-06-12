#!/usr/bin/env python3
"""infra/vault — post_compose hook (S9.1 / S9.4).

Runs at S8.3 step 17, after the Vault container is up. In a real deployment this
is where you would initialize + unseal a fresh Vault and capture the resulting
root token; in this DEV-mode demo Vault is auto-unsealed and the root token is
simply the GEN_LOCAL value we already fed it, which we read back from its store
file via ``ctx.secret_file`` (S9.3 — the sanctioned way for a post_compose hook
to obtain a secret value).

The return is the v2 STRUCTURED form only (S9.4):

  * ``initialized`` -> apply_to_config (visible to later hooks/templates this
    run) AND persist:'state' (written to [state] in ciu.toml).
  * ``root_token``  -> persist:'state'.

Persisting ``root_token`` into ``infra/vault/ciu.toml [state]`` is the whole
point of the bootstrap: it becomes source #3 of the S4.16 Vault-token order, so
LATER stacks (redis-core, db-core) that declare GEN_TO_VAULT secrets resolve
their token without any env juggling — the v1 ``vault_env_pre_hook`` is gone.
"""

from __future__ import annotations


def run(config: dict, ctx) -> dict:
    _ = config
    # S9.3: a post_compose hook reads a resolved secret from its store file.
    token = ctx.secret_file("root_token").read_text(encoding="utf-8").strip()
    return {
        "initialized": {
            "value": True,
            "persist": "state",
            "apply_to_config": True,
        },
        "root_token": {
            "value": token,
            "persist": "state",
        },
    }
