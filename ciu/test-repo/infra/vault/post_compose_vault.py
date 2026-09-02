#!/usr/bin/env python3
"""infra/vault — post_compose hook (S9.1 / S9.4).

Runs at S8.3 step 17, after the Vault container is up. In a real deployment this
is where you would initialize + unseal a fresh Vault and capture the resulting
root token; in this DEV-mode demo Vault is auto-unsealed and the root token is
simply the GEN_LOCAL value we already fed it.

The return is the v2 STRUCTURED form only (S9.4):

  * ``initialized`` -> apply_to_config (visible to later hooks/templates this
    run) AND persist:'state' (written to [state] in ciu.toml). It is a plain
    boolean fact, not secret-shaped, so [state] is exactly where it belongs.

**This hook deliberately persists NO token (ciu-P46 / F4).** It used to return
``root_token`` with ``persist:'state'`` so S4.16's old source #3 could find it
in ``infra/vault/ciu.toml [state]``. That was a redundant SECOND copy of an
already-safe value: this stack declares ``root_token = "GEN_LOCAL:demo/
vault_root_token"``, so from the moment it is generated the token already lives
in the project secret store at 0440, masked in every log, and covered by the
S4.22 post-render leak scan — while the ``[state]`` copy sat in an ordinarily
rendered, ordinarily readable, unscanned plaintext file.

S4.16's source #3 now reads a hook-persisted secret store file (S9.4a) instead
of ``[state]``, and this fixture correspondingly needs LESS code, not more: it
simply stops writing the unsafe copy. A stack whose token IS directive-backed
publishes it through the ordinary S4 store; ``[vault].token_file`` in
``ciu.global.defaults.toml.j2`` points the token resolver at this demo's
GEN_LOCAL project-store path, which is source #2 of the same S4.16 order.

``persist:'secret'`` exists for the OTHER case — a hook MINTING a value no
directive could have expressed (a real ``vault operator init`` producing a
fresh root token and unseal key). See docs/SPEC.md §B.2a for that worked
example; this DEV-mode fixture is not it.
"""

from __future__ import annotations


def run(config: dict, ctx) -> dict:
    _ = config, ctx
    return {
        "initialized": {
            "value": True,
            "persist": "state",
            "apply_to_config": True,
        },
    }
