# Plan — gate adoption & verification (every project has a *meaningful*, verified gate)

Status: planned · authored 2026-07-25 · sibling of `docs/plan-factory-hardening.md`.

## Why

The factory's whole correctness story rests on one assumption: **a project's
declared gate actually decides "shippable" correctly.** nyxloom hardened the
orchestration *around* the gate (F: run-at-commit isolation, auto-revert; G:
parallel + coverage-honest gate), but it still **trusts the gate command blindly**:

- dstdns ships a `[gates.gate-probe]` whose `argv` is `true` — proves the harness
  *runs*, not that it *rejects bad code*.
- A project could register with `argv=["true"]` as its only gate and every merge
  would sail through, orchestration perfectly hardened, verdict meaningless.
- Coverage floors are opt-in and today only nyxloom has one; dstdns/topos/naf run
  a bare `pytest` with no completeness floor and no parallelism.

Canonical framing (the gate's value is a *composition* — infra ⊕ toolkit ⊕ content
⊕ discipline; nyxloom requires the interface, offers the toolkit, mandates no
infra): `nyxloom-trove/LESSONS.md` PL2 and `reference/STANDARD.md` §"What nyxloom
requires of a project (the gate contract)". This plan is the *make-it-true-and-keep-
it-true* workstream for that contract.

## The gate-adoption checklist (per project — the deliverable a human/onboarding works through)

A project is **factory-ready** only when every box is checked:

1. **Separate, runtime-faithful test environment** — a container/venv whose
   dependency closure matches the app runtime, NOT the interactive cockpit (whose
   pins are not a ship signal). dstdns has `test-runner`; nyxloom has
   `tester-unified`. topos/naf: confirm or build one.
2. **A declared `[gates.*]`** with `phase="implementation"` (and optionally a
   distinct `post-merge` gate), `{worktree}`-parameterised `argv`, and a real
   `timeout_seconds`.
3. **Fails closed** — the command's real non-zero exit survives to nyxloom: no
   trailing `echo`, no `| tee` that swallows `$?`; the verdict is read in a step
   separate from the run (canonical `LESSONS.md` L4; use `PIPESTATUS`/logfile,
   never a pipe).
4. **Completeness floor (recommended)** — changed-line coverage via
   `coverage_gate.py` (Python) or the ecosystem equivalent (`cargo llvm-cov`,
   `nyc`, …). Opt-in, but a gate without one cannot catch untested new branches.
5. **Parallel (recommended)** — the suite runs under `pytest-xdist -n auto` (or
   ecosystem `-j`) so the floor stays affordable. **Coverage must remain honest
   under parallelism** — verify per-file executed-line parity vs a serial run
   before trusting it (factory-hardening G's method: `coverage run` serial vs
   `pytest-cov` xdist; a fork-covered line that serial credits but the parallel
   runner drops is a *deterministic-test gap to close*, not a plumbing bug).
6. **Proven to REJECT** — the gate has been shown to fail on a deliberately-broken
   commit (a "known-bad canary"), not just pass on a good one. This is the box
   that distinguishes a gate from a `true`.
7. **Rigor declared** — once GA2 lands, the `[gates.*]` `asserts=[...]` key states
   what the gate enforces, so nyxloom can surface it and route review depth.

The checklist itself is shippable doc today; items 3–7 want the automation below to
stay true over time.

## Packages (automation — carve from this plan)

### GA1 — `nyxloom gate verify <project>` CLI verb · SMALL-MEDIUM · ✅ DONE (merge `49f7ca06`)
**Done.** Verdicts TRUSTWORTHY(0)/LAUNDERS(1)/BROKEN(1)/NO_GATE(1)/INCONCLUSIVE(3). Canary =
subtree-scoped import-break (minimal one-line `raise` insertion, no coverage/reformat
dependence), multi-attempt (≤4 subtree files, any-killed → TRUSTWORTHY, LAUNDERS only if all
survive). Reuses `gate_runner.run_gate_at_commit` + isolation; logic in new `gate_canary.py`.
Review REJECTed v1 (canary scanned the WHOLE repo → false LAUNDERS on nyxloom's own gate);
redesigned + re-review APPROVE. Gate 173/173 diff-cov green.
**What.** A verb that (a) selects the project's verification gate
(`gate_runner.select_verification_gate`), (b) runs it at HEAD and asserts PASS, and
(c) applies a **known-bad canary** — a mechanical, reversible source mutation
(reuse `mutation_gate.generate_mutants` on a changed/first line, or inject a
`raise AssertionError` into a covered function in a scratch worktree) — and asserts
the gate now FAILS. Reports `pass-on-good`, `fail-on-bad`, and the derived verdict
`TRUSTWORTHY | LAUNDERS (does not reject) | BROKEN (does not pass)`.
**Why.** Turns checklist item 6 from a manual belief into a command. This is the
"meta-gate" from PL2/§D — a gate for the gate.
**Scope.** New `cli.cmd_gate_verify` + a small `gate_canary.py` helper (canary
inject/restore in a scratch worktree, mirroring `gate_runner`'s isolation), tests.
Leaf-ish; reuses `gate_runner` + `mutation_gate` primitives.

### GA2 — `asserts=[...]` gate-rigor declaration + review-depth routing · MEDIUM · declaration+cross-check ✅ DONE (merge `7529f807`); review-depth routing → D part 2
**Done (declaration + gate-verify cross-check).** `asserts=[tests-pass|changed-line-coverage|
mutation|canary-verified]` on `[gates.*]` (schema enum + `GateDef.asserts`). `nyxloom gate verify`
cross-checks it against its own verdict (canary-verified + LAUNDERS → MISMATCH; + TRUSTWORTHY →
OK confirmed; INCONCLUSIVE → UNVERIFIED; coverage/mutation → declared-not-verified). Additive
overlay, gate 42/42. **Still pending:** feeding `asserts` (+ complexity band) into review-depth
selection — that's D part 2 (the routing half), deferred with it.
**What.** Add an optional `asserts: [tests-pass, changed-line-coverage, mutation,
canary-verified]` list to `[gates.*]` in the config schema; surface it on the
dashboard; feed it into review-depth selection (a weak-gate project → deeper/stronger
review; a strong-gate project → cheaper). **This is the same axis factory-hardening
D mechanizes** (band + gate-rigor → review depth) — build them together.
**Scope.** `nyxloom-config.schema.json` (+`asserts`), `config.py` GateDef, dashboard
render, routing/review-depth selection, `adapters.py` REVIEW_INDEPENDENT (argv_max-
bounded injection — see §D caveat), tests. Frozen-core-adjacent (routing/adapters).

### GA3 — onboarding offers to build a missing/untrustworthy gate · MEDIUM
**What.** Extend the onboarding engine (F2/F3/F4) so that when a project is
registered/assessed and has no gate — or `nyxloom gate verify` (GA1) returns
`LAUNDERS`/`BROKEN` — onboarding **offers to scaffold a separate test env + a
fail-closed `[gates.*]`** (from per-ecosystem templates: a `test-runner`-style
Dockerfile + a pytest/coverage/xdist gate command for Python), rather than
registering a project whose merges can't be verified.
**Scope.** Onboarding assessment step + gate-scaffold templates + wiring into the
init/questionnaire flow. Depends on GA1 (the trust verdict) + GA2 (rigor vocab).

### GA4 — carver periodically re-verifies each project's gate · SMALL (needs GA1)
**What.** A cadence-driven check (mirror the D-065 `test_health_interval_days`
pattern: a `gate_verify_interval_days` policy knob) where the carver/daemon runs
`nyxloom gate verify` (GA1) per project and, on a `LAUNDERS`/`BROKEN` verdict,
raises an escalation/decision rather than silently continuing to trust it. Keeps
checklist item 6 true as the codebase evolves (a refactor can quietly make a gate
stop rejecting).
**Scope.** A reconcile item + policy knob + escalation wiring. Pure-ish; needs GA1.

## Sequencing
1. **GA1** (the `gate verify` verb + canary) — the primitive everything else needs.
2. **GA2** (rigor declaration + review routing) — build with factory-hardening **D**.
3. **GA4** (periodic re-verify) — cheap once GA1 exists.
4. **GA3** (onboarding gate-scaffold offer) — richest; after GA1/GA2.

All honor **L1** (structural over band-aid) and **PL2** (require interface / offer
toolkit / mandate no infra) as their acceptance bar. Per-project adoption (dstdns
coverage+xdist, topos/naf separate-env+floor) is tracked in each project's own
`4-backlog.md`.
