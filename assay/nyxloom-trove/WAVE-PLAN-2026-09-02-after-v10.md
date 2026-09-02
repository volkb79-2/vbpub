# Wave plan — after v10 (assay 5.0.0): resumable, observable, re-attachable long runs

**Status: PLAN, not a dispatch.** Written 2026-09-02 by the vbpub controller
session on the operator's instruction to "fold the new todos/backlog in with
future wave plans" and to lay out the open decisions with context, pros/cons
and a recommendation. Nothing here is carved; Wave D (`WAVE-PROMPT-2026-09-02-
wave-d-v10-integrity.md`, target assay 5.0.0 / verdict v10) is still in
flight on `feature/assay-wave-d-v10` and ships first. A backlog item enters
`3-roadmap.md` only by becoming a feature with checkable acceptance criteria
(that file's own rule), so this note is the staging area between the backlogs
and the next wave prompt.

Where the items live: assay `4-backlog.md` (B0xx), run-gate
`run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md` (RG-xx), cmru's release
checklist (memory `cmru-self-release-workflow`, `assay-dstdns-release-notify`).

---

## 1. The pattern being adopted, and how it maps

dstdns adopted, for its own long drivers on 2026-09-02:

1. a **progress artifact** the caller polls (JSONL: elapsed, counts, ETA),
   not the process;
2. **resumability** — created run ids persisted on creation, a restarted
   invocation re-attaches instead of creating new ones;
3. an **unbounded budget by convention** once 1 and 2 exist — health is
   judged from the progress file, not from whether the process is alive.

Measured state of the judge and the gate after today (run-gate 23.3.0,
assay 4.1.0; the Wave D branch changes none of this):

| leg | assay (judge) | run-gate (gate) | gap → item |
|---|---|---|---|
| progress artifact | `--progress` writes `run`/`baseline`/`shard`/`resume` + one event per candidate with index/total; **no timestamp, no outcome** (`mutation.py:755`). R0/R1: no events (one command). R3: no events. | rev 33 passes `--progress .assay/progress-<lane>.jsonl` on every assay lane; reads none of it. | **B065** timestamps/outcome/end event; **B064** (branch) phase events for R0/R1, per-attempt for canary; **RG-36** ETA disclosure |
| resumability | `--resume` + `.assay/mutation-state/<id>.json` under the judged project root; ids fold in the file's exact bytes, so a shared store is safe. R3/R4: no state. | rev 33 passes `--resume` always. The lane CONTAINER survives a dead client (`docker run -d` … `rm -f` in a `finally` the client never reaches) but nothing re-attaches; a restart starts a duplicate. Fresh worktrees (cmru release, Mode-B) lose the state. | **RG-35** re-attach; **B066** `--state-dir`; **RG-37** durable per-repo state dir; B064/B066 for canary and R4 records |
| unbounded budget | `budget` required, enforced lane-wide (`LANE_TIMEOUT`); `budget_per_candidate` optional (R2). | `budget` advisory (printed, never enforced). | **B067** `budget = "unbounded"` iff every unit is bounded; **RG-36** `stall_timeout` |

Two facts bound what is possible:

- **R0 and R1 are one command each.** assay cannot checkpoint inside a
  foreign test runner, and a coverage judgment over a partial rerun is
  unsound. Resume below the whole command is a rerun by construction; the
  container-level re-attach (RG-35) IS their resumability. Progress there is
  a phase stream (snapshot materialised, command started/finished, coverage
  parsed, verdict written) — cheap, and it makes every lane's file look the
  same to a monitor.
- **Canary (R3) and red-first (R4, Wave D's F015) have mutation's per-unit
  shape** — one command per target/attempt, few units (B007 bounds targets at
  8, measured 2.76 s of materialisation per target). Per-attempt progress and
  per-target resume reuse the mutation-state mechanism through B066; B007's
  per-attempt payload is exactly what a progress event would carry (A-432's
  coupling note).

## 2. The candidate pool (everything not yet carved, grouped)

**G1 — the pattern (this plan's core).**
assay B064 (branch; phase/attempt events), B065 (timestamped, outcome-bearing
events + `end`), B066 (`--state-dir`), B067 (`budget = "unbounded"`, decision
D1). run-gate RG-35 (re-attach / inflight record / `--fresh`), RG-36
(progress tail: rate + ETA disclosure; optional `stall_timeout`), RG-37
(durable `.run-gate/assay-state/<project>/` bind-mounted at the state path).

Dependencies: B065 → RG-36's exact timing (RG-36 can ship with mtime-based
stall and clock-based ETA first, disclosed as coarse). B066 → RG-37 (copy-in/
copy-out is the disclosed fallback). RG-35 depends on nothing. B067 depends on
B065 + RG-36 being real, and on decision D1.

**G2 — hygiene carried from Wave D and the peer's filings.**
assay B062 (`tests/` pyflakes sweep, 31 findings in 19 modules), B063 (three
test modules `git -C PROJECT_ROOT.parent`, so the suite cannot run from a copy).
run-gate RG-32 (`pins.assay.budget` silently inert), RG-34 (a consumer's
`schema` lane argv not `{worktree}`-templated — dstdns config; run-gate's
share is a validation/doc rule), RG-18 (dstdns-side). A further peer filing
is pending as RG-38 (exec-mode container derivation reads `deploy.*` from the
consumer's ciu.global.toml).

**G3 — Wave D's explicit exclusions, carried forward unchanged** (see the
wave prompt's exclusion list): B020, B023, B001 residual, B010's orchestration
half, B026, B048's verb, Go at R2/R3, `assay canary qualify`. Not
re-argued here.

**G4 — process, not code.** After every assay release: `cmru tool-deps`
freshness sweep + `cmru tool-deps --refresh assay` for vbpub's own consumers
(ciu 3.2.0, nyxloom 4.0.0, cmru 4.1.0 today), next to the dstdns
`release.json` notify. Decision D2 (pin policy) governs whether this is the
mechanism or tree-tracking replaces it.

## 3. Proposed sequencing (after Wave D ships 5.0.0)

| step | scope | why this order |
|---|---|---|
| **E-1 run-gate 23.4.0** | RG-35 re-attach; RG-32; RG-34's run-gate share; RG-38 if filed | independent of assay; RG-35 removes the duplicate-container hazard the host rule exists for; small, one reviewer round |
| **E-2 assay 5.1.0** | B065, B066, B064 (phase + per-attempt events); B062, B063 hygiene | all additive to the progress stream / CLI — no verdict schema change, so a minor release; unblocks E-3 exactly |
| **E-3 run-gate 23.5.0** | RG-36 (exact ETA/stall on B065's timestamps), RG-37 (on B066) | the consumer-facing payoff; dstdns `sql-mutation` is the acceptance lane |
| **E-4 assay 5.2.0** | B067 `budget = "unbounded"` (needs D1); F015/R4 implementation (phase 3 of Wave D if it does not fit there; its wire shape ships in 5.0.0); canary per-target resume | only after E-2/E-3 make "health from progress" true, so an unbounded budget is honest |

Sizes are not estimated in hours: the analogous shipped items are RG-33
(one day incl. release, this session), B031/B032 (the progress artifact,
one review round) and Wave D's own B028 (lane timeouts, two generations).

## 4. Open decisions — context, pros/cons, recommendation

**D1 — `budget = "unbounded"` (B067).**
Context: `budget` is required and hard; the failure mode is guessing a total
(dstdns 90m → 120m, still short). Options: (a) keep a required numeric budget,
tell consumers to set it generously; (b) allow `unbounded` only when every
unit of work carries its own bound (R2 `budget_per_candidate`; R3/R4
per-attempt bound from B007's design); refuse it for R0/R1, whose only unit is
the command. Pros of (b): honest — the lane cannot hang, because every unit is
bounded and the caller judges liveness from the stream; no invented number.
Cons: two more config states to validate; a lane with a hung *substrate*
(materialisation, not a unit) still needs the caller's stall detection, so (b)
is only safe together with RG-36. (a) is simpler but keeps the guess.
**Recommendation: (b), landed in E-4 after RG-36, with the refusal for R0/R1.**

**D2 — judge pins in vbpub's own consumers.**
Context: ciu/cmru/nyxloom vendor a digest-verified assay zipapp; the operator
asked why not track the repo's latest. Options: (a) keep immutable pins +
the G4 release-checklist sweep; (b) in-repo consumers judge with the
current assay tree/wheel. Pros of (a): a verdict names the judge that produced
it (`--require-judge-provenance`), the gate has no network and no source
install, the judge under test never changes mid-wave under a consumer, one
path shared with dstdns. Cons: pins go stale unless swept (cmru sat on 2.3.0
ten days). Pros of (b): no staleness, no chicken-and-egg if a release did not
exist. Cons: evidence loses its binding to a reviewed release; a mid-wave
assay tree (like today's branch) would judge consumer gates; assay's own gate
already builds from the tree, so the egg problem is confined to consumers,
where a release always exists.
**Recommendation: (a), with the sweep made a checklist step now (a
`cmru release --project assay` post-step is the natural home).**

**D3 — where liveness lives: judge or gate.**
Context: the judge could time-stamp and self-detect stalls; the gate can tail
the file. Options: (a) assay grows a `stall_timeout` of its own; (b) assay
emits timestamps (B065), run-gate judges (RG-36). Pros of (b): the judge
cannot observe its own stall from inside a stuck unit; the gate already owns
the container lifecycle and the kill; one implementation serves every
consumer, including ones not using run-gate (they get the file). Cons: a
consumer without run-gate has no stall detection until it writes one (the
file makes that trivial).
**Recommendation: (b).**

**D4 — re-attach by default or by flag (RG-35).**
Options: (a) auto re-attach whenever an inflight record names a live
container for the same lane+worktree, `--fresh` to override; (b) refuse and
name the container, require `--attach`. Pros of (a): a restart is the common
case after a client death and it removes the duplicate-container hazard
without the operator knowing the flag. Cons: a stale record could attach to
the wrong run — mitigated by matching commit + worktree + container id and
disclosing the attach line. (b) is safer in the abstract but reintroduces the
manual step the host rule was written to prevent.
**Recommendation: (a), always disclosed, with commit and worktree matched.**

**D5 — durable state placement (RG-37/B066).**
Options: (a) bind-mount a per-repo `.run-gate/assay-state/<project>/` at the
state path; (b) copy `.assay/mutation-state/` in before and out after the
lane. Pros of (a): zero copy cost, state visible to a re-attached run, works
for a killed client; needs assay B066 first. Cons: one more mount to get
right (dual-mount lessons apply). (b) works today but loses the state of a
lane that dies before copy-out.
**Recommendation: (a) once B066 ships; (b) only as the disclosed interim,
if E-3 must precede E-2 for dstdns.**

**D6 — progress events for R0/R1 (B064's phase stream).**
Options: (a) build the phase stream; (b) leave R0/R1 silent. Pros of (a):
every lane's file has the same shape, a monitor can tell "command running
20 min" from "hung before materialisation", and a run header exists for
re-attach. Cons: a few events nobody strictly needs today. It is small (the
events sit at seams that already log).
**Recommendation: (a), inside E-2, kept to a handful of named phases.**

**D7 — F015/R4 implementation: Wave D phase 3 or E-4.**
Context: Wave D ships R4's wire shape in the v10 cut; the mechanism (materialise
the pre-fix commit with HEAD's test files overlaid, run both) is phase 3 by
the wave prompt. Pros of keeping it in Wave D: the schema and the mechanism
ship together, M7 closes, one reviewer (R-2) sees both. Cons: Wave D is
already eight generations in; phase 3 adds at least two more plus R-2 rounds
before 5.0.0. Pros of moving it to E-4: 5.0.0 ships sooner with the wire
shape reserved (a claim kind nobody produces yet is allowed by A-138's hard-cut
rule); the implementation lands as a minor release. Cons: an R4 enum value in
the wild with no producer for a while.
**Recommendation: decide at the phase-2 gate. If R-2's review of phase 2
needs more than one round, move F015's implementation to E-4 and ship 5.0.0;
otherwise keep phase 3 in Wave D as planned.**

## 5. What this plan does NOT decide

The Wave D rulings (DA-D1..D16, DA-R1..R20) stand. Nothing in G1 changes the
v10 wire format; B065/B066/B067 are CLI, stream and config, which is why E-2
is a minor release. G3 stays excluded until argued item by item.
