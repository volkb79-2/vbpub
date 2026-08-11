---
schema_version: 1
id: ciu-worktree-isolation-primitives
project: ciu
title: "Worktree instances become usable for real (non-mock) test lanes"
tier: implement-2
input_revision: "e414f475e8fc03e260b8dd913f36cfbb631ef1a1"
source: {kind: user, ref: "KNOWN_ISSUES_TODO_BACKLOG.md CIU-20..CIU-24"}
stack: none
depends_on: []
session: fresh
scope:
  touch:
    - "src/ciu/deploy.py"
    - "src/ciu/cli.py"
    - "src/ciu/governance.py"
    - "src/ciu/worktree.py"
    - "src/ciu/composefile.py"
    - "src/ciu/workspace_env.py"
    - "docs/SPEC.md"
    - "docs/DESIGN-NOTES.md"
    - "KNOWN_ISSUES_TODO_BACKLOG.md"
    - "tests/test_deploy.py"
    - "tests/test_governance.py"
    - "tests/test_worktree.py"
    - "tests/test_cli.py"
    - "tests/test_workspace_env.py"
    - "tests/test_composefile.py"
    - "nyxloom-trove/reports/ciu-worktree-isolation-primitives-LOG.md"
  forbid:
    - "docs/SPEC.md#S1-S14"
    - "docs/SPEC.md#S15"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/roadmap.md"
    - "nyxloom-trove/backlog.md"
    - "../assay"
    - "../srdm"
    - "../dstdns"
    - "../nyxloom"
oracles:
  - id: O1
    observable: "CIU-20. `ciu provenance --json [PATH|-]` emits one closed JSON document (`schema_version`, `instance`, `commit_under_test`, `tree_state`, `containers[]` with `{name, image, labelled_revision, status}`, `overall`) ALONGSIDE the existing prose/exit-code behaviour, unchanged. `overall` distinguishes `verified-match` from every non-refusal path (`not-verified-dirty`, `not-verified-unknown`, `refused-no-identity`) so a caller can tell 'checked and matched' from 'nothing was verifiable' without parsing prose. A verified match is RECORDED, not silent — the current success path in `verify_running_provenance` returns without any output; `--json` must not inherit that silence."
    negative: "A silent success path under `--json`, an `overall` vocabulary that collapses verified-match together with any not-verified case, or a shape that requires reading `[S17]`-prefixed prose to interpret, each fail this oracle."
    gate: tester-unified
  - id: O2
    observable: "CIU-21. Every non-exempt service's compose overlay fragment gets `CIU_IMAGE_REVISION=<value>` appended to `frag[\"environment\"]` (never assigned — the existing KSM `LD_PRELOAD=...` entry, S15.11, must survive untouched when both apply to the same service), where `<value>` is read back from THAT SERVICE'S OWN resolved image's `org.opencontainers.image.revision` label at overlay-generation time — never from `engine.get_git_hash()`, which is the host working tree's current view, a different claim. The variable is OMITTED entirely (not set to an empty string or placeholder) when the image has no label or does not exist yet (a plain `ciu up` with no prior `bake`)."
    negative: "A value sourced from `get_git_hash()` instead of the image's own label, an assignment (`frag[\"environment\"] = [...]`) that silently drops the KSM entry, or an injected placeholder when the label is absent, each fail this oracle — the second is CIU-14's exact failure class one call site over."
    gate: tester-unified
  - id: O3
    observable: "CIU-22. `ciu worktree add <name> --shared-infra <ref> [--profile P1,P2]` narrows the new instance to the profile's OWN services (S7.5, unchanged) and additionally wires every service the new instance would otherwise start for the NAMED, already-running REF instance's shared tier onto that ref instance's existing network — so a shared-tier service is never started twice. The new instance still gets its own `INSTANCE_ID`, its own network for its diverging-tier containers, and its own `ciu.env`. `--shared-infra` naming a REF instance that is not currently running is a `WorktreeError` at `add` time, not a silent no-op."
    negative: "Standing up a duplicate copy of a shared-tier service under a different name, joining a network without first confirming the ref instance is live, or silently ignoring an unresolvable `--shared-infra` value, each fail this oracle."
    gate: tester-unified
  - id: O4
    observable: "CIU-23. A new `worktree add --data-isolation <db-profile>` mode (config-declared: a reachable shared database server plus a named init-script set) provisions a database/schema UNIQUELY NAMESPACED by the new instance's own `INSTANCE_ID` on that shared server, applies the declared init scripts against it, and records the resulting connection identity in the new worktree's own `ciu.env` for the project's test command to read. `worktree rm`'s existing clean-before-remove ordering (S16) tears the namespaced database down BEFORE the checkout is removed, mirroring the existing container-volume discipline."
    negative: "A namespace collision between two concurrently-provisioned instances, a namespaced database surviving `worktree rm`, or a silent success when the configured shared server is unreachable at provision time, each fail this oracle."
    gate: tester-unified
  - id: O5
    observable: "CIU-24. A configured maximum concurrent worktree-instance count is resolved the same way `resolve_cgroup_parent` already resolves ITS config (explicit stack config, else an ambient env override, else a hard named error — never a silent hardcoded default), and `worktree add` counts currently DEPLOYED instances (registered per `list_worktrees` AND actually running containers, not merely checked out) against that cap, refusing with a message naming the current count and the limit when it would be exceeded."
    negative: "Counting checked-out-but-undeployed worktrees toward the cap (undercounting nothing, but conflating two different resource costs), or a cap value with no traceable config/env source, each fail this oracle."
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "the shared-infra network join (O3) or the data-isolation provisioning (O4) cannot be implemented without touching a file outside scope.touch — these are the two most architecturally open items in this package; say so rather than improvise past the boundary"
  - "CIU-22's or CIU-23's honest implementation requires a DIFFERENT primitive than the one sketched in the observable (e.g. compose's own network model cannot express a cross-instance join the way this handoff assumes) — a real, working design that diverges from the sketch is preferred over a fragile one that matches it"
  - "O2's per-service image-label lookup requires calling docker at overlay-GENERATION time (before any container is created) and `composefile.py`'s current design assumes no docker dependency at that stage — if this is a real architectural cost, record it as a decision rather than silently accepting it"
mutexes: [merge-lane]
---

# ciu-worktree-isolation-primitives — worktree instances become usable for real test lanes

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

The five items are one package because they are the same subsystem
(`worktree.py`/S16) viewed from five angles, not five unrelated features:
provenance visibility (O1/CIU-20), provenance's in-container half (O2/
CIU-21), the "don't pay for isolation on tiers that never diverge" cost
control (O3/CIU-22), the "don't pay for a full Postgres container when a
namespaced database suffices" cost control (O4/CIU-23), and the safety valve
that makes running several of these at once survivable (O5/CIU-24).

## What's already real (read before touching anything)

- `src/ciu/worktree.py` — the whole S16 module. `add()`, `remove()`,
  `list_worktrees()`, `find_worktree()`. Read the module docstring in full:
  the `rm`-order invariant (clean before remove) is normative and O4 must
  extend it, not work around it.
- `src/ciu/deploy.py` — `verify_running_provenance` (S17.2, the function
  O1 adds a JSON sibling output to), `_running_containers`,
  `_image_revision_label` (the function O2 must reuse the SAME logic as, not
  reimplement).
- `src/ciu/governance.py` — the KSM env-injection site (~line 1025,
  `frag["environment"] = [f"LD_PRELOAD={KSM_PRELOAD_TARGET}"]`) is O2's
  precedent and collision risk in one place. `resolve_cgroup_parent`
  (~line 275) and `CGROUP_PARENT_ENV_VAR` (~line 272) are O5's precedent for
  how a resource cap gets resolved without a hardcoded default.
- `src/ciu/cli.py` — `_provenance` (O1's CLI wiring site), `_worktree`
  (O3/O4/O5's CLI wiring site), the `diagnose --json` implementation
  (~line 700-730) as O1's JSON-output precedent to match in shape, not
  reinvent.
- `src/ciu/workspace_env.py` — `_compute_network_name`,
  `_ensure_network_exists`, `ensure_workspace_network` — where network
  identity and creation currently live; O3's likely site.
- `docs/DESIGN-NOTES.md` D7 and `docs/SPEC.md` S16/S17 — already updated
  this session with the honest state of what exists; read both before
  writing the new S16.1-S16.3/S17.3-S17.4 sections this package adds.
- `dstdns/scripts/schema-gate.sh` (a SIBLING repo, read-only reference, NOT
  in this package's scope to touch) — the hand-rolled throwaway-database
  pattern O4 makes first-class. Read it for the shape, not to copy code
  across repos.

## Dispatch contract

- Contract class: **2b/2d mixed** — O1/O2/O5 are constrained implementation
  against a fully-specified target (2b); O3/O4 have real, named design
  freedom within a falsifiable observable (closer to 2d). Say in the LOG
  which class each oracle actually turned out to be.
- Required roles: **Sonnet xhigh implementer → fresh Opus xhigh independent
  reviewer.**
- Readiness: READY only after this handoff has passed adversarial carve
  review.
- Degrees of freedom: private helper names, the exact `--data-isolation`
  config table shape (O4), and the exact network-join mechanism (O3) are
  yours to design — the observables are falsifiable behaviour, not a
  prescribed implementation. Everything named explicitly in an oracle
  (field names in O1's JSON, the append-not-assign rule in O2, the
  label-not-git-hash rule in O2, the clean-before-remove ordering in O4,
  the cgroup_parent-style resolution in O5) is fixed.

## Worktree and branch

`.worktrees/ciu-worktree-isolation-primitives`, branch
`ciu-worktree-isolation-primitives`, created from `ciu`'s current `main` at
the pinned `input_revision`. `nyxloom.toml`'s `worktree_root = "../.worktrees"`
already points here — this is the same shared `.worktrees/` directory assay's
own packages use, at the monorepo root, not nested inside `ciu/`.

## Gate

`[gates.tester-unified]` in `nyxloom-trove/nyxloom.toml` — real, already
wired, runs `run-ciu-tests.py` inside `tester-unified:local` plus
`nyxloom.coverage_gate` against `src/ciu`. Baseline at `input_revision`:
measure and paste real output in the LOG before writing any code (A-232's
rule: a stated pass/fail count is not evidence without the artifact).

## What must stay out, and why

`docs/SPEC.md`'s S1-S14/S15 sections and the strategic docs (`decisions.md`,
`roadmap.md`, `backlog.md`) are out of scope — this package documents new
capability (new S-numbered subsections), it does not re-litigate existing
spec or re-plan the roadmap. The sibling repos (`assay`, `srdm`, `dstdns`,
`nyxloom`) are out of scope entirely: this package only ships the ciu-side
primitive; a consumer adopting it (e.g. dstdns wiring its own gate to
`--shared-infra`/`--data-isolation`) is separate, unstarted work in that
consumer's own repo, not this package's job.

## On landing

Mark CIU-20 through CIU-24 `FIXED` in `KNOWN_ISSUES_TODO_BACKLOG.md`'s status
board, each row pointing at the SPEC id and the real evidence (per the
file's own house rule: code + tests + spec + docs land together). Leave
CIU-25 alone — explicitly out of scope for this package.
