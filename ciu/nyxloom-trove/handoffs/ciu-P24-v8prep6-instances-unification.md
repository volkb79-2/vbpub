---
schema_version: 1
id: ciu-P24-v8prep6-instances-unification
project: ciu
component: composefile
title: "V8-PREP-6: optional service-level instances = N default for configfile fan-out, plus a {{ ciu.instances }} compose-template context and a loud refusal on the duplicate-mount condition instead of today's silent WARN"
tier: implement-2
input_revision: "13c039ac"
source: {kind: research, ref: "V8-PREP-6 additive-safety research pass, controller session 2026-08-25, grounded in docs/CIU-V8-TESTING-GATE-PROPOSAL.md §1.19 and live source at src/ciu/composefile.py"}
stack: none
depends_on: [P23]
session: fresh
scope:
  touch:
    - "src/ciu/composefile.py"
    - "tests/tests/test_ciu_composefile.py"
    - "docs/SPEC.md"
    - "docs/CONFIG.md"
    - "docs/CONSUMERS.md"
    - "CHANGES.md"
    - "docs/BACKLOG-2026-08-24.md"
    - "nyxloom-trove/reports/ciu-P24-v8prep6-instances-unification-LOG.md"
    # --- controller-authorized widening, 2026-08-25 (same pattern as this
    # wave's ciu-P15/P17/P32 scope-widening episodes): implementing O1
    # surfaced a live blast-radius issue (a pre-existing test pinning the
    # OLD "single shared render, S5.3 base-selector fan-out" behavior for
    # a stack that already carries a service-level `instances` key used
    # only by its own compose loop) PLUS a real silent-upgrade hazard for
    # any external consumer shaped the same way. The controller decided:
    # migrate this demo fixture to the new unified pattern, update its
    # pinning test, and add a dedicated refusal (S7.5e) closing the hazard
    # for everyone else. See the LOG's dedicated section for the full story.
    - "test-repo/applications/workers/ciu.defaults.toml.j2"
    - "test-repo/applications/workers/ciu.compose.yml.j2"
    - "test-repo/applications/workers/config.toml.j2"
    - "tests/tests/test_ciu_test_repo.py"
  forbid:
    - "src/ciu/engine.py"
    - "src/ciu/deploy.py"
    - "src/ciu/provisioning.py"
    - "src/ciu/config_model.py"
    - "nyxloom-trove/backlog.md"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/roadmap.md"
oracles:
  - id: O1-service-level-default
    observable: "A new optional key `[<root>.<service>] instances = N` (positive int; validated identically to the existing per-configfile `instances` check at ~composefile.py:632-643, do not reimplement the validation differently). When present, it is the DEFAULT for every configfile under that service that OMITS its own `instances` key — an explicit configfile-level `instances` still wins (existing behavior, byte-identical). Declaring BOTH the service-level and a configfile-level `instances` with DIFFERENT values is a REFUSAL naming the service, the configfile, and both conflicting values (this is the exact duplicate-mount trap named in the proposal — it must fail loudly at config-validation time, not produce a silent partial mount later)."
    negative: "silently preferring one value over the other when they disagree; changing the resolved instances count for any configfile that already declares its own instances value (must stay byte-identical when no service-level default is declared, or when it agrees with the configfile-level value)"
    gate: "tester-unified"
  - id: O2-compose-context
    observable: "`render_compose` (~composefile.py:229-271) gains `ciu.instances` in the template context it already builds: a `{service_name: N}` mapping built the SAME way `selected_profiles`/`deployed_stacks` are already merged into the `ciu` table (mirror ~composefile.py:252-258 / config_model.py:332-347's channel exactly — do not invent a second injection mechanism). A service with no declared `instances` (service-level or any configfile-level) is simply ABSENT from the mapping (not present with a value of 1) -- a template checking `'api' in ciu.instances` is the intended pattern for 'does this service fan out'."
    negative: "including every service in ciu.instances with a default of 1 (changes what 'is this a multi-instance service' means for a template author, and generates a churn diff for every existing compose template that doesn't yet check this)"
    gate: "tester-unified"
  - id: O3-duplicate-mount-refusal
    observable: "New post-render assertion: when ANY service has a resolved instances value N > 1 (from either level), the rendered compose dict must contain compose service keys `<svc>-1` through `<svc>-N` and must NOT contain a bare `<svc>` key -- violating this is a NAMED ValueError (naming the service and what was found instead), not the existing silent `[WARN]` at `_configfile_mount_services` (~composefile.py:815-834) which only warns on the OPPOSITE case today. This assertion runs only for services where instances > 1 was actually declared (via O1) -- it must not fire for ordinary single-instance services that happen to use numeric-looking naming for unrelated reasons."
    negative: "extending the existing WARN into a refusal for EVERY case _configfile_mount_services already covers (scope creep -- this oracle only covers the NEW instances>1-declared-but-bare-key-rendered direction, the existing WARN behavior for its own original cases is untouched)"
    gate: "tester-unified"
  - id: O4-tests
    observable: "No service declares instances -> zero behavior change (regression bar, a spy/counroun fixture proving ciu.instances is absent from context and no new refusal path is entered). Service-level instances=3, configfile omits its own -> configfile fans out 3x (existing fan-out mechanism, just service-level-sourced). Service-level=3, configfile-level=3 (agreeing) -> passes, uses 3. Service-level=3, configfile-level=5 (disagreeing) -> refusal naming both values. ciu.instances context contains exactly the services that declared instances>1, nothing else. A compose render that declares instances=3 but emits a bare `api` key (the duplicate-mount condition) -> refusal naming it; a compose render that correctly emits `api-1/api-2/api-3` -> passes."
    negative: "a test suite that never actually constructs the duplicate-mount failure case end-to-end (this is the one oracle proving the fix does anything real)"
    gate: "tester-unified"
  - id: O5-docs
    observable: "docs/SPEC.md documents the service-level instances default, the disagreement refusal, and `{{ ciu.instances }}`'s exact shape (extend whatever section documents today's configfile-level instances, state which in the LOG). docs/CONSUMERS.md gets a worked example: a service declaring instances=3 with a compose template looping `{% for i in range(1, ciu.instances.api + 1) %}` naming `api-{{ i }}` (per the research, this naming convention is ALREADY what `_configfile_mount_services` understands -- do not invent a different convention). docs/CONFIG.md documents the key. CHANGES.md Unreleased entry. docs/BACKLOG-2026-08-24.md's V8-PREP-6 row updated with the additive subset shipped -- state plainly that CIU does NOT auto-generate compose service blocks (the template author still writes the loop); this package unifies the FAN-OUT COUNT and makes disagreement loud, it does not generate compose YAML."
    negative: "documenting this as if CIU now auto-generates compose services from instances (it does not -- the template author's own {% for %} loop does that, using the new count)"
    gate: "tester-unified"
gates: ["tester-unified"]
escalate_if:
  - "the existing selected_profiles/deployed_stacks injection channel (config_model.py ~332-347 / composefile.py ~252-258) cannot accept a third key without a change to a FORBIDDEN file (config_model.py) -- BLOCKED naming the exact incompatibility; do not invent a parallel injection mechanism in composefile.py alone if the real channel lives in the forbidden file"
mutexes: [merge-lane]
review_focus:
  - "a config that declares no instances anywhere renders byte-identical compose output to before this package"
  - "the disagreement refusal actually fires on service-level vs configfile-level MISMATCH, not on mere co-presence with equal values"
  - "the duplicate-mount refusal is scoped to instances-declared services only, doesn't spuriously fire on unrelated numeric-looking service names"
---

# ciu-P24 — V8-PREP-6: unified `instances = N` fan-out

## Context to read first

1. `docs/BACKLOG-2026-08-24.md#V8-PREP-6` and `docs/CIU-V8-TESTING-GATE-PROPOSAL.md` §1.19.
2. `src/ciu/composefile.py` in full for the `instances` machinery: the per-configfile `instances` validation (~618-643), `instance_index`/`instance_id` context injection (~683-690), the per-instance mount naming (~697-703), and `_configfile_mount_services` (~796-835, especially the exact-key-wins-then-`<base>-<N>`-fallback-then-WARN logic at ~810-822 and the WARN at ~824-834 — this is the OPPOSITE-direction existing check O3 must not duplicate or weaken).
3. `render_compose` (~229-271) and the `ciu` context table's construction — trace exactly how `selected_profiles`/`deployed_stacks` get into it (likely threaded from `config_model.py` — read that side too even though it's forbidden for edits, to understand the channel you're extending).
4. `docs/CONSUMERS.md` — check whether any existing worked example already shows a multi-instance compose template, to match its naming convention exactly.

## Work

1. Service-level `instances` default + disagreement refusal (O1).
2. `{{ ciu.instances }}` context injection (O2).
3. Duplicate-mount post-render refusal (O3).
4. Tests (O4).
5. Docs (O5).

## Environment setup

```bash
cd /workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu
.venv/bin/python run-ciu-tests.py
```

## BLOCKED rule

Per `escalate_if`. Write `BLOCKED: <reason>` to
`nyxloom-trove/reports/ciu-P24-v8prep6-instances-unification-LOG.md`, commit, exit.
