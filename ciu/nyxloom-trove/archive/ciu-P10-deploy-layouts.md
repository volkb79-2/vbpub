---
schema_version: 1
id: ciu-P10-deploy-layouts
project: ciu
component: deploy
title: "Deploy layouts: [deploy.layouts.<name>] names a host->bundles plan + its environment; ciu up --layout <name> drives the SPEC-J push per host in declared order"
tier: implement-2
input_revision: "3639b18c7500c1e5e09ea5bb2bf88dc6bfe8c6de"   # re-frozen at the main-merge into this branch (rev-2 brief); file:line anchors measured on pre-merge main — RE-VERIFY each (P08: _make_render_context :317, render_global_chain :392 on this branch)
source: {kind: backlog, ref: "KNOWN_ISSUES_TODO_BACKLOG.md#CIU-34"}
stack: none
depends_on: [ciu-P08-landscape-identity]
session: fresh
scope:
  touch:
    - "src/ciu/deploy_pkg/layouts.py"
    - "src/ciu/deploy_pkg/__init__.py"
    - "src/ciu/cli.py"
    - "tests/tests/test_ciu_deploy_layouts.py"
    - "tests/tests/test_ciu_cli_layouts.py"
    - "docs/CONFIG.md"
    - "docs/SPEC.md"
    - "CHANGES.md"
    - "KNOWN_ISSUES_TODO_BACKLOG.md"
    - "nyxloom-trove/reports/ciu-P10-deploy-layouts-LOG.md"
  forbid:
    - "src/ciu/deploy.py"
    - "src/ciu/engine.py"
    - "src/ciu/transport_ssh.py"
    - "src/ciu/hosts.py"
    - "nyxloom-trove/backlog.md"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/roadmap.md"
oracles:
  - id: O1-model
    observable: "New module src/ciu/deploy_pkg/layouts.py with a Layout dataclass (precedent: deploy_pkg/profiles.py Profile): resolve_layout(global_cfg, hosts_cfg, name) parses [deploy.layouts.<name>] with keys: environment (REQUIRED, enum dev|test|staging|prod, tagged error otherwise), hosts (ordered table: [deploy.layouts.<name>.hosts.<host>] bundles = [<profile names>]), optional description. Validation: every bundle name must resolve via deploy_pkg.profiles (unknown → tagged error naming layout+host+bundle); every host name must exist in the hosts inventory passed in (load_hosts result; unknown → tagged error); an empty hosts table → tagged error. Declaration order of hosts is preserved (dict order) and is the execution order."
    negative: "environment optional or defaulted; validation happening at execution instead of resolution; order lost"
    gate: "tester-unified"
  - id: O2-cli
    observable: "ciu up --layout <name> (cli.py, following the existing inline-argparse pattern): resolves the layout, then FOR EACH host in order runs the existing up --host flow for that host with (a) CIU_SERVICES_PROFILE set to the host's bundles joined by comma and (b) the remote command env carrying CIU_LAYOUT=<name>, CIU_LAYOUT_HOST=<host>, CIU_DEPLOY_ENVIRONMENT=<layout.environment> (prepended as VAR=val exports inside the single remote argv string, same one-argv discipline as cli.py:735-742). A host failure ABORTS the sequence (no continue-on-error in v1) with an error naming the failed host and the not-yet-deployed remainder. --layout and --host are mutually exclusive (tagged error). ciu profiles output (or a new ciu layouts listing, implementer's choice documented in CONFIG.md) shows declared layouts."
    negative: "layout execution reimplementing the push instead of delegating to the existing up --host code path; continue-on-error; the env vars exported into the LOCAL process instead of the remote command"
    gate: "tester-unified"
  - id: O3-no-transport-change
    observable: "transport_ssh.py, hosts.py, deploy.py, engine.py are byte-identical to input_revision (the layout layer is pure orchestration above the existing verbs); test proves the remote argv composition via a fake ssh runner seam, never a live connection"
    negative: "a transport edit sneaking in; a test opening a real socket"
    gate: "tester-unified"
  - id: O4-docs
    observable: "docs/CONFIG.md: [deploy.layouts] section with a worked two-host example (dev-local single-host and a 3-host split) and the CIU_LAYOUT/CIU_LAYOUT_HOST/CIU_DEPLOY_ENVIRONMENT contract (consumers reference them in templates via {{ env.* }} / $VAR expansion); docs/SPEC.md normative clause; CHANGES.md; KNOWN_ISSUES CIU-34 row → FIXED with evidence."
    negative: "docs without the env-contract table (the consumer's whole reason for the ask)"
    gate: "tester-unified"
gates: ["tester-unified"]
escalate_if:
  - "The up --host flow cannot be invoked per-host without editing deploy.py/cli-up internals beyond adding the flag — BLOCKED naming the exact coupling"
  - "The worktree-automation branch (docs/ciu-worktree-automation-backlog) has merged and its documentation contract test rejects the new docs shape — BLOCKED with the failing assertion (do not weaken the contract test)"
mutexes: [merge-lane]
review_focus:
  - "the abort-on-first-failure semantics and its error text (names the remainder)"
  - "mutual exclusion --layout/--host; no local-env leakage of the three CIU_LAYOUT* vars"
  - "textual conflicts with the ciu-worktree-automation-backlog branch in cli.py (_USAGE, _VERB_HELP) — coordinate merge order"
---

# ciu-P10 — deploy layouts (CIU-34)

## Context to read first
1. `KNOWN_ISSUES_TODO_BACKLOG.md#CIU-34` — the filed ask and candidate shape (adopted here as
   the decision, with `environment` added per dstdns D-105 Q2: the layout is the durable home
   of a deployment's environment value).
2. `src/ciu/deploy_pkg/profiles.py` — the `Profile` dataclass + `resolve_profiles` you mirror
   (`_resolve_one` :130-202, conflict-merge :107-123). A layout REFERENCES profiles (bundles);
   it never merges them itself.
3. `src/ciu/hosts.py` (whole, 68 lines) — the host inventory you validate against (read-only).
4. `src/ciu/cli.py:677-742` — the `up --host` flow you orchestrate: bundle_dir, ssh_sync, the
   single remote argv `cd <bundle_dir> && ciu env generate && ciu render && ciu up …`.
5. `docs/SPEC.md:1008-1160` (S14) — the push model this sits on.

## Dispatch contract
A layout is **pure orchestration data + a loop**: resolve, validate, then delegate each host to
the EXISTING `up --host` path with three env vars prepended to the remote command. No transport
change, no render change, no new sync mechanism. Vocabulary (fixed): **bundle** = a
`[deploy.profiles.<name>]` entry (what a host runs); **layout** = the named host→bundles plan
(who runs what, plus the deployment's environment). Out-of-scope / forbid: everything in
scope.forbid — `deploy.py`/`engine.py`/`transport_ssh.py`/`hosts.py` stay byte-identical (O3);
sibling repos are read-only context.

## Work
1. `layouts.py` model + resolution/validation (O1).
2. CLI wiring + the per-host loop (O2) with a fake-ssh seam for tests.
3. Tests: resolution happy + both error paths, order preservation, abort-on-failure remainder text,
   mutual exclusion, env composition in the remote argv.
4. Docs per O4; CIU-34 → FIXED with evidence in tracker and LOG.

## Environment setup
Implement in the dispatched worktree at `../.worktrees/<branch>/ciu` (trove `worktree_root`).
Standard ciu gate (`run-ciu-tests.py`, 100% line+branch, tester-unified; no live Docker/SSH —
fake seams only, precedent `tests/tests/test_ciu_deploy_actions.py:1348-1379`).

## BLOCKED rule
Impossible within scope.touch → `BLOCKED: <mechanical reason>` in the LOG, commit, exit.
Forbidden workarounds: editing forbidden modules; continue-on-error; a second push
implementation.
