---
schema_version: 1
id: ciu-P01-worktree-isolation-primitives
project: ciu
title: "Worktree instances become usable for real (non-mock) test lanes"
tier: implement-2
input_revision: "715b85d78b23baa8079b1f6b1c8c8f0f1b6e0a1d"
source: {kind: user, ref: "KNOWN_ISSUES_TODO_BACKLOG.md CIU-20, CIU-21, CIU-22, CIU-23, CIU-24"}
stack: none
depends_on: []
session: fresh
scope:
  touch:
    - "src/ciu/deploy.py"
    - "src/ciu/cli.py"
    - "src/ciu/governance.py"
    - "src/ciu/worktree.py"
    - "src/ciu/engine.py"
    - "src/ciu/workspace_env.py"
    - "docs/SPEC.md"
    - "docs/DESIGN-NOTES.md"
    - "KNOWN_ISSUES_TODO_BACKLOG.md"
    - "tests/tests/test_ciu_provenance_json.py"
    - "tests/tests/test_ciu_provenance_env_injection.py"
    - "tests/tests/test_ciu_worktree.py"
    - "tests/tests/test_ciu_cli_worktree.py"
    - "nyxloom-trove/carve-assets/ciu-P01-worktree-isolation-primitives/**"
    - "nyxloom-trove/reports/ciu-P01-worktree-isolation-primitives-LOG.md"
  forbid:
    - "src/ciu/composefile.py"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/roadmap.md"
    - "nyxloom-trove/backlog.md"
    - "../assay"
    - "../srdm"
    - "../dstdns"
    - "../nyxloom"
oracles:
  - id: O1
    observable: "CIU-20. `verify_running_provenance` is refactored to ALWAYS build and return a result object (never bare `None`, never raising internally) with fields `overall`, `instance`, `commit_under_test`, `tree_state`, `containers: list[{name, image, labelled_revision, status}]` — this is a real, intentional signature change, not 'unchanged'. `_provenance` (cli.py) is the ONLY place that decides prose/raise/warn behaviour from that result, and is BYTE-IDENTICAL in that behaviour when `--json` is absent. `--json` (store_true, matching `diagnose --json`'s existing shape exactly) prints ONLY the JSON document to stdout, no prose mixed in. Grammar, fixed: `labelled_revision` is JSON `null` when unknown, NEVER `\"\"`; `commit_under_test` is `get_git_hash()`'s return value VERBATIM (the `-dirty` suffix, if any, lives ONLY here); `tree_state` is DERIVED from that same string (`.endswith(\"-dirty\")` -> `dirty`, `== \"dev\"` -> `not-a-checkout`, else `clean`) — never set independently, so the two fields cannot contradict; `containers[]` sorted by `name` ascending for a deterministic document. `refused-no-identity` is emitted directly by `_provenance` when `project`/`env_tag` cannot be resolved, BEFORE `verify_running_provenance` is called at all — for that case `instance`/`commit_under_test`/`tree_state` are `null` and `containers` is `[]`. The five committed fixtures in `nyxloom-trove/carve-assets/ciu-P01-worktree-isolation-primitives/` are the frozen grammar — one real document per `overall` value, byte-comparable after normalising `instance`/`commit_under_test`/image names to the fixture's own values."
    negative: "Emitting a document ONLY on the clean-success path (so `mismatch`/`refused-no-identity` never produce one — CIU-20's own defect re-created inverted), mixing prose onto the same stream as `--json`'s JSON, a `labelled_revision` of `\"\"` instead of `null`, `tree_state` set independently of `commit_under_test` (able to disagree), or `containers[]` in non-deterministic (docker-returned) order, each fail this oracle."
    gate: tester-unified
  - id: O2
    observable: "CIU-21. A `Mapping[str, str]` of service-name -> image-revision (omitting a service with no label, or with `build:` and no baked `image:` yet) is built ONCE per render/up pass in `engine.py` (already docker-aware — the lookup does NOT belong in `composefile.py`, which stays docker-free by design) immediately before the existing Step-15 `generate_overlay` call, and passed through to the injection site. `CIU_IMAGE_REVISION=<value>` is APPENDED (never assigned — `frag[\"environment\"] = [...]` would silently drop the existing KSM `LD_PRELOAD` entry, S15.11, CIU-14's exact failure class one call site over) to each service's environment fragment, using THAT SERVICE'S OWN revision from the map — never `engine.get_git_hash()`, a different claim (the host tree's current view, not the running image's baked truth). Injection is UNCONDITIONAL: it happens regardless of `governance.enabled` (`composefile.py:777`'s gate governs resource governance, not provenance, and must not gate this too), and `governance.exempt_services` does NOT exempt a service from it — that key's whole meaning is 'no resource governance for this service', unrelated to provenance identity. The value must be independently confirmed READABLE INSIDE A RUNNING CONTAINER for at least one real integration-style test — reading it back out of the rendered compose overlay's `environment` list for the owning service is the gate-verifiable proxy for that."
    negative: "Injection suppressed when governance is disabled or a service is in `exempt_services`, a value sourced from `get_git_hash()`, an assignment that drops the KSM entry, an injected placeholder for a service with no baked label (must be OMITTED, not empty), or a test that only inspects the overlay fragment in the abstract without confirming it belongs to the SPECIFIC service whose image it names, each fail this oracle."
    gate: tester-unified
  - id: O3
    observable: "CIU-22. `ciu worktree add <name> --shared-infra <ref> [--profile P1,P2]` gives the new instance a SECOND network membership (`ciu.env` gains a list of additional networks to join, alongside the existing single `DOCKER_NETWORK_INTERNAL` scalar which continues to name the instance's OWN network) — the REF instance's network, resolved and confirmed RUNNING at `add` time (a `WorktreeError` if not). The falsifiable claim is CONNECTIVITY, not non-duplication (non-duplication is already true from S7.5 profile narrowing alone, which this oracle leaves unchanged, and proves nothing new by itself): a diverging-tier container belonging to the NEW instance must be able to resolve and reach a shared-tier service belonging to the REF instance, over the joined network — AND the new instance's own diverging-tier containers must still be reachable from each other on the new instance's OWN network, not merged into one shared network for everything."
    negative: "Setting `DOCKER_NETWORK_INTERNAL` to the ref's network name (moving the WHOLE new instance onto one shared network, destroying S16's own cross-instance isolation — the attack that satisfies non-duplication while shipping no real join), joining a network without confirming the ref instance is live, or an unresolvable `--shared-infra` value treated as a silent no-op, each fail this oracle."
    gate: tester-unified
  - id: O4
    observable: "CIU-23. `worktree add --data-isolation <db-profile>` provisions a database/schema namespaced by the new instance's own `INSTANCE_ID` via an INJECTABLE provisioner interface you define (a `Protocol`/callable your implementation calls, with the real Postgres-backed implementation as the shipped default) — this makes the naming/ordering/force-semantics mechanism independently testable in-gate WITHOUT a live Postgres server, which `tester-unified:local` cannot supply (name this seam explicitly in the LOG; a real-server proof is deliberately OUT OF SCOPE for this package's gate and belongs to a follow-up integration verification, not invented here). `remove()` gains a new step BEFORE `_clean_in` that drops the namespaced entity; a FAILED drop aborts removal unless `force=True` is also passed (mirroring `_clean_in`'s own existing contract exactly), and when `force=True` masks a failed drop the warning explicitly says the entity was not dropped and is now the operator's problem. The connection identity is written to the new worktree's own `ciu.env`; `docs/SPEC.md` and the LOG must both state plainly that this value MAY be credential-bearing and must never be recommended as an `env_passthrough` candidate for a consumer's own assay lane (a passthrough value lands in every verdict artifact in cleartext, established last session)."
    negative: "A collision test using two worktrees WITHIN one repo (untestable — `add` already refuses a duplicate name, so no practical collision exists there); the real attack is two CLONES of the repo choosing the same worktree name, which the test must reproduce. A DB drop happening AFTER `_clean_in` rather than before, or `--force` silently succeeding with no warning when the drop failed, each also fail this oracle."
    gate: tester-unified
  - id: O5
    observable: "CIU-24. New stack-config key `governance.max_concurrent_worktrees` (integer) and ambient override `CIU_MAX_CONCURRENT_WORKTREES`, resolved in that precedence order. UNLIKE `resolve_cgroup_parent`, unset at BOTH levels means NO CAP, not a hard error — a concurrency cap is an opt-in safety net whose absence is today's actual behaviour for every existing installation; a hard error here would regress every current user, which `resolve_cgroup_parent`'s own correctness-gap reasoning does not apply to (that function guards resource governance itself; this guards an unrelated, separately-adopted feature). The PRIMARY checkout COUNTS toward the cap (it is a real, resource-consuming instance): a cap of N permits at most N-1 additional worktree instances. `worktree add` counts only instances that are BOTH registered (`list_worktrees`) AND actually DEPLOYED (have running containers) — independently testable via one fixture with a checked-out-but-undeployed worktree (must NOT count) and one with a deployed worktree (MUST count)."
    negative: "A cap that also fires with nothing configured (regressing every current user), counting the primary as free, counting a checked-out-but-undeployed worktree as consuming the budget, or a docker-running-check that the test suite never independently exercises (the count could be `len(list_worktrees()) - 1` with the running-check silently absent and still pass a test that only ever uses deployed fixtures), each fail this oracle."
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "CIU-22's connectivity requirement (O3) cannot be met without a THIRD network model beyond 'own network' + 'joined ref network' — say what the real shape needs to be rather than force a design that doesn't fit compose's model"
  - "O4's injectable-provisioner seam cannot honestly stand in for a real database without also faking away the exact behaviour (naming collisions, drop failures) the oracle exists to prove"
  - "a correct O2 implementation requires touching `composefile.py` after all — re-verify against the real `generate_overlay` signature before escalating, since the prior round's carve reviewer found this NOT to be the case, but a real second look is not something to skip"
mutexes: [merge-lane]
---

# ciu-P01-worktree-isolation-primitives — worktree instances become usable for real test lanes

## Round 2 — what changed from the first carve, and why

The first carve of this handoff was reviewed by C-sol-1 (assay's carver,
reused here at the operator's direction rather than a fresh CR-opus-0 fork)
and returned **NOT READY** on eleven blocking ambiguities, five false-PASS
attacks, and several scope defects. Every finding was real and is fixed
below — nothing in the verdict was disputed. The full review is in this
package's dispatch record; the changes it produced:

- **`id` corrected** to match `nyxloom`'s own frontmatter schema
  (`^[a-z][a-z0-9]*-P[0-9]{2,4}(-[a-z0-9-]+)?$`) — the first draft would have
  been rejected by `nyxloom lint` outright.
- **`scope.touch`'s test paths corrected** to real, new files under
  `tests/tests/` (ciu's actual test layout — the first draft guessed at a
  layout that doesn't exist) — and `src/ciu/engine.py` ADDED, because O2
  cannot be implemented correctly without it (see O2's own text for why
  `composefile.py` was the wrong site).
- **Five frozen JSON fixtures added** as this package's own carve-assets —
  O1's data grammar is now a byte-comparable artifact, not prose.
- **O1's seam named explicitly**: `verify_running_provenance`'s signature
  change is now stated as intentional, not glossed as "unchanged".
- **O2's governance-gating question ruled**: injection is unconditional,
  `exempt_services` does not apply to it.
- **O3 rewritten around connectivity**, not non-duplication — the original
  observable was satisfiable by S7.5 alone, proving nothing new.
- **O4's gate-verifiability gap named and resolved** via an injectable
  provisioner seam, with the real-Postgres proof explicitly deferred rather
  than silently assumed; `--force` semantics and drop ordering both ruled.
- **O5's config key and env var named**, unset-semantics ruled (opposite of
  `resolve_cgroup_parent`'s hard-error, with the reasoning for why that's
  correct here and not there), and primary-counting ruled.

## Why this package exists, and why it's five items in one

`ciu worktree` (S16) already gives every worktree its own container/network/
volume isolation, automatically, from the physical checkout path — nothing
about that is broken. What's missing is what makes it *practical* for the
case it was actually built for: a program running real (non-mock)
integration/schema/E2E test lanes concurrently across several packages, where
per-package schema genuinely diverges and a shared stack is architecturally
wrong, not just inconvenient.

Two consumers hit this from different directions the same day and converged
on the same five gaps: assay's own cross-project review (filed CIU-20
through CIU-25 in `KNOWN_ISSUES_TODO_BACKLOG.md`), and dstdns's own
reconciliation program, independently designing real-lane isolation for its
Postgres-backed integration suite and needing exactly this primitive set.
CIU-25 (a leak detector for orphaned instances) is deliberately EXCLUDED from
this package — it's real but lower-priority ops hygiene, not something
blocking real-lane adoption today.

## What's already real (read before touching anything)

- `src/ciu/worktree.py` — the whole S16 module. `add()`, `remove()`,
  `list_worktrees()`, `find_worktree()`. Read the module docstring in full:
  the `rm`-order invariant (clean before remove) is normative and O4 extends
  it (drop-before-clean-before-remove), does not work around it. **Note:**
  this file currently has ZERO test coverage (verified — no
  `tests/tests/test_ciu_worktree*.py` exists). `tests/tests/test_ciu_worktree.py`
  is this package's first-ever test module for it; the coverage gate is
  changed-line-scoped, so pre-existing untested lines you don't touch are not
  your problem, but any line you DO touch in `add`/`remove` needs real
  coverage, existing behaviour included where your diff passes through it.
- `src/ciu/deploy.py` — `verify_running_provenance` (S17.2, the function O1
  changes), `_running_containers`, `_image_revision_label` (O2 must call
  this exact function per-service, never reimplement its label-reading
  logic).
- `src/ciu/governance.py` — the KSM env-injection site (~line 1025,
  `frag["environment"] = [f"LD_PRELOAD={KSM_PRELOAD_TARGET}"]`, gated by
  `if gov_cfg["enabled"]:` at `composefile.py:777`) is O2's precedent AND
  its collision risk — O2's injection must NOT be nested inside that gate.
  `resolve_cgroup_parent` (~line 275) and `CGROUP_PARENT_ENV_VAR` (~line 272)
  are O5's naming precedent, not its semantics — read O5's own text for
  where the two deliberately diverge.
- `src/ciu/engine.py` — Step 15 (~line 1368), immediately before the
  existing `generate_overlay` call. `materialized`, `configfile_mounts`,
  `governance` are already resolved here and passed in as data; O2's
  service->revision map is the same shape and belongs here, not in
  `composefile.py` (which stays docker-free — confirmed zero
  `procutil`/`docker`/`subprocess` references, and should stay that way).
- `src/ciu/cli.py` — `_provenance` (O1's CLI wiring site — the ONLY place
  behaviour forks on `--json`), `_worktree` (O3/O4/O5's CLI wiring site),
  the `diagnose --json` implementation (~line 700-730) as O1's PRECEDENT TO
  MATCH EXACTLY (`store_true`, not `[PATH|-]` — the first carve round
  proposed a shape that conflicted with this precedent; this round drops it).
- `src/ciu/workspace_env.py` — `_compute_network_name`,
  `_ensure_network_exists`, `ensure_workspace_network` — O3's likely site
  for the second network-join facility.
- `docs/DESIGN-NOTES.md` D7 and `docs/SPEC.md` S16/S17 — read both before
  writing the new S16.1-S16.3/S17.3-S17.4 sections this package adds.
- `dstdns/scripts/schema-gate.sh` (a SIBLING repo, read-only reference, NOT
  in this package's scope to touch) — the hand-rolled throwaway-database
  pattern O4 makes first-class.

## Dispatch contract

- Contract class: **2b/2d mixed** — O1/O2/O5 are constrained implementation
  against a fully-specified target (2b, all data grammar and semantics now
  ruled); O3/O4 retain real, named design freedom within a falsifiable
  observable (2d) — O4's config-table shape and provisioner-interface shape
  are yours; O3's exact network-join mechanism within compose's model is
  yours. Say in the LOG which class each oracle actually turned out to be.
- Required roles: **Sonnet xhigh implementer -> fresh Opus xhigh independent
  reviewer.**
- Readiness: READY only after this handoff passes adversarial carve review.
- Baseline: measure the real gate at `input_revision` and paste the actual
  output as the LOG's first entry, before any code change — a stated
  pass/fail count with no pasted artifact is not evidence.

## Worktree and branch

`.worktrees/ciu-P01-worktree-isolation-primitives`, branch
`ciu-P01-worktree-isolation-primitives`, created from `ciu`'s current `main`
at the pinned `input_revision`. `nyxloom.toml`'s
`worktree_root = "../.worktrees"` already points here — the same shared
`.worktrees/` directory assay's own packages use, at the monorepo root, not
nested inside `ciu/`.

## Gate

`[gates.tester-unified]` in `nyxloom-trove/nyxloom.toml` — real, already
wired, runs `run-ciu-tests.py` inside `tester-unified:local` plus
`nyxloom.coverage_gate` against `src/ciu`.

## What must stay out, and why

`src/ciu/composefile.py` is explicitly forbidden — O2's docker lookup
belongs in `engine.py`, not here, per the resolved S1 finding; if you find
yourself needing to touch it, that is exactly the case `escalate_if`'s third
entry names, and you should re-verify against the real `generate_overlay`
signature before concluding you need to. The strategic docs (`decisions.md`,
`roadmap.md`, `backlog.md`) are out of scope — this package documents new
capability (new S-numbered subsections in `docs/SPEC.md`), it does not
re-plan the roadmap. The sibling repos (`assay`, `srdm`, `dstdns`, `nyxloom`)
are out of scope entirely: this package only ships the ciu-side primitive; a
consumer adopting it is separate, unstarted work in that consumer's own
repo.

## On landing

Mark CIU-20 through CIU-24 `FIXED` in `KNOWN_ISSUES_TODO_BACKLOG.md`'s status
board, each row pointing at the real SPEC id and evidence (code + tests +
spec + docs land together, per this file's own house rule). Leave CIU-25
alone — explicitly out of scope for this package.
