---
schema_version: 1
id: ciu-P11-host-scoped-secrets
project: ciu
component: secrets
title: "Host-scoped local secrets: [deploy.hosts.<h>.secrets] entries (ASK_EXTERNAL/GEN_LOCAL) materialized under the project store's hosts/<h>/ namespace, resolvable before any Vault exists on the target"
tier: implement-2
input_revision: "3639b18c7500c1e5e09ea5bb2bf88dc6bfe8c6de"   # re-frozen at the main-merge into this branch (rev-2 brief); file:line anchors measured on pre-merge main — RE-VERIFY each (P08: _make_render_context :317, render_global_chain :392 on this branch)
source: {kind: backlog, ref: "KNOWN_ISSUES_TODO_BACKLOG.md#CIU-35"}
stack: none
depends_on: []
session: fresh
scope:
  touch:
    - "src/ciu/hosts.py"
    - "src/ciu/secrets/materialize.py"
    - "src/ciu/cli.py"
    - "tests/tests/test_ciu_host_secrets.py"
    - "docs/CONFIG.md"
    - "docs/SPEC.md"
    - "CHANGES.md"
    - "KNOWN_ISSUES_TODO_BACKLOG.md"
    - "nyxloom-trove/reports/ciu-P11-host-scoped-secrets-LOG.md"
  forbid:
    - "src/ciu/secrets/directives.py"
    - "src/ciu/secrets/providers.py"
    - "src/ciu/transport_ssh.py"
    - "src/ciu/engine.py"
    - "nyxloom-trove/backlog.md"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/roadmap.md"
oracles:
  - id: O1-declaration
    observable: "hosts.py: get_host / load_hosts recognise an optional [deploy.hosts.<h>.secrets] subtable; each entry is parsed with the EXISTING secrets.directives.parse_value (imported, not reimplemented) and only kinds ASK_EXTERNAL and GEN_LOCAL are accepted for host scope — any other directive → tagged error naming host+entry+the reason (Vault-dependent and ephemeral kinds are meaningless before a host is adopted). The secrets subtable is POPPED from the mapping get_host returns for transport use (a caller asking for connection facts never receives secret directives)."
    negative: "a reimplemented parser; ASK_VAULT accepted at host scope; secrets leaking into the transport host dict"
    gate: "tester-unified"
  - id: O2-store-scope
    observable: "New materialize_host_secrets(repo_root, host_name, entries, *, env, assume_yes) in secrets/materialize.py: store path is project_store(repo_root)/hosts/<host_name>/<entry_name> (0700 dirs, atomic write, flock — reusing _write_store_file/_ensure_dir_mode/_flock); resolution order for ASK_EXTERNAL and reuse-if-present for GEN_LOCAL are the EXISTING behaviours (delegating to _materialize_one or its extracted core); two hosts may declare the SAME entry name without collision (the per-stack global-uniqueness rule S4.6 explicitly does NOT apply across host namespaces — documented)."
    negative: "a new store layout invented; uniqueness enforced across hosts; a resolution-order divergence from stack secrets"
    gate: "tester-unified"
  - id: O3-cli
    observable: "ciu host-secrets <host> [--materialize|--list|--path <name>] (cli.py inline-argparse pattern; exact verb spelling implementer's choice, documented): --materialize resolves all declared entries (interactive prompt rules identical to stack ASK_EXTERNAL: TTY + not -y); --list prints names + store-file existence WITHOUT values; --path prints the store file path for one entry (for use in scripts feeding e.g. a Tailscale bootstrap over ciu ssh). Values are NEVER printed."
    negative: "a value reaching stdout; materialization happening implicitly inside ciu ssh/up --host (v1 is explicit-only — the consumer script decides when)"
    gate: "tester-unified"
  - id: O4-docs
    observable: "docs/CONFIG.md documents the subtable, the two allowed kinds, the store namespace, the non-uniqueness rule, and the worked consumer example FROM THE ASK (a Tailscale single-use authkey + an SSH bootstrap key declared per host, materialized, then consumed by a bootstrap command over ciu ssh); docs/SPEC.md normative clause (S14-adjacent); CHANGES.md; KNOWN_ISSUES CIU-35 row → FIXED with evidence."
    negative: "docs without the pre-Vault rationale (the entire point of the ask)"
    gate: "tester-unified"
gates: ["tester-unified"]
escalate_if:
  - "parse_value cannot be reused without editing directives.py (forbidden) — BLOCKED naming the exact incompatibility"
  - "the worktree-automation branch's documentation contract test rejects the docs shape post-merge — BLOCKED with the failing assertion"
mutexes: [merge-lane]
review_focus:
  - "the pop-before-return in get_host (no secret directives in transport dicts)"
  - "no value ever printed; no implicit materialization"
  - "cli.py textual conflicts with the ciu-worktree-automation-backlog branch — coordinate merge order"
---

# ciu-P11 — host-scoped local secrets (CIU-35)

## Context to read first
1. `KNOWN_ISSUES_TODO_BACKLOG.md#CIU-35` — the ask: SSH bootstrap key / Tailscale single-use
   authkey per host, resolvable BEFORE any Vault exists on the target, later movable to Vault
   by the existing directives.
2. `src/ciu/hosts.py` (whole, 68 lines) — where the subtable joins; note the render-safe
   design promise at :2-4 (`ciu render`/`clean` must STILL never touch the hosts file).
3. `src/ciu/secrets/directives.py:116-273` (`parse_value`, READ-ONLY import) and
   `src/ciu/secrets/materialize.py` — `_store_file` :88-96, `_write_store_file` :159-197,
   `_ensure_dir_mode` :205-227, `_flock` :229-249, `_materialize_one` :338-455 (the
   ASK_EXTERNAL order :403-428 and GEN_LOCAL reuse :369-376 you must preserve exactly).
4. `src/ciu/cli.py` — inline-argparse verb pattern + `_USAGE`/`_VERB_HELP` (both must gain the
   new verb or it is undiscoverable).

## Dispatch contract
Host secrets are the EXISTING secret machinery pointed at a new namespace — not a new secret
system. Two kinds only; explicit materialization only; values never printed; the transport
layer never sees directives. Out-of-scope / forbid: `directives.py` and `providers.py` are
consumed read-only; `transport_ssh.py` unchanged (consumers wire materialized files into their
own bootstrap commands); sibling repos are read-only context.

## Work
1. hosts.py subtable parse + pop (O1).
2. `materialize_host_secrets` on the hosts/<h>/ namespace (O2).
3. CLI verb (O3) with prompts faked via monkeypatched stdin/TTY in tests.
4. Docs per O4; CIU-35 → FIXED with evidence in tracker and LOG.

## Environment setup
Implement in the dispatched worktree at `../.worktrees/<branch>/ciu` (trove `worktree_root`).
Standard ciu gate (`run-ciu-tests.py`, 100% line+branch, tester-unified; tmp_path stores; no
live Vault/SSH).

## BLOCKED rule
Impossible within scope.touch → `BLOCKED: <mechanical reason>` in the LOG, commit, exit.
Forbidden workarounds: editing directives.py; printing a value "for debugging"; implicit
materialization inside transport verbs.
