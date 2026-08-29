# CIU v8 — wholistic integration prompt

**Status:** dispatch prompt for a LIVE INTERACTIVE session — this document is
instructions, not itself a design artifact. **This cannot run as an
unattended/background task.** Its whole method (§0.1, §3) is: read
everything, then interview the operator on every genuine design fork before
writing anything — that requires the operator present and answering, turn by
turn, for the whole session. If you were dispatched as a background/batch
agent, stop and tell whoever dispatched you this needs an interactive
session instead.
**Purpose:** after reading everything in §1 (including dstdns as the example
consumer, §1.6) and interviewing the operator on every genuine fork (§0.1),
rewrite `ciu/docs/CIU-V8-TESTING-GATE-PROPOSAL.md` **in place** as **revision
2.0** — one clean, unified v8 proposal. Revision 2.0 is not a diff or a
patch log on top of 1.10: write it as if authored fresh, incorporating
everything decided in earlier revisions and in the interview, but **without**
the dated "Superseded/Applied/corrected on date X" editorial archaeology
that accumulated across 1.5→1.10 — that convention served an iterative
working draft; it does not belong in the document meant to stand as THE v8
proposal. (§4 below spells out exactly what survives and in what shape.)
**Session origin:** dstdns/vbpub joint design work, 2026-08-22 through 2026-08-29 — several separate design passes that never got reconciled against each other, against what actually shipped, or with the operator present live for every fork
**Audience:** you, a fresh agent with no memory of the discussions that produced the source documents below. Everything you need is named explicitly. Do not guess at context; read it. Where a decision has no technically-forced answer, do not guess at the operator's preference either — ask (§0.1).

---

## 0. What you are doing, and why this prompt exists

Over five days, three tools that must work together — **ciu** (deployment
orchestration), **assay** (coverage/mutation judge), **run-gate** (test-lane
dispatch) — each accumulated its own pile of v8-track design documents,
backlog entries, and decisions, written across multiple sessions by multiple
agents who did not all read each other's work. The result is coherent in
patches and contradictory or duplicated across the seams:

- Two different addressing schemes for the same "logical service" concept
  were drafted in different sections of the *same* proposal document
  (`CIU-V8-TESTING-GATE-PROPOSAL.md` §3.1 vs §1.15/§1.16). **Update
  2026-08-27, revision 1.10:** this specific tension is now resolved — the
  fold-back happened, and the proposal's worked examples throughout §1.15,
  §3.1–3.3, §4.1–4.2, and §8 consistently use `V8-REALIZATION-GRAPH.md`'s
  entity/realization split. Kept here as the motivating example of the
  seam-reconciliation problem this document exists to solve, not because it
  still needs solving — but its resolution has **not** been checked against
  the full scenario walk in §5 (remote deployment, shared-infra join,
  transport), and four more substantial sections landed the same session
  (§10.3 secrets, §10.11 instance mutex, §10.12 concurrency model, §10.13
  realness immutability, §10.14 shared-infra sharpened) that this prompt's
  own §4.3/§6 do not yet account for. The wholistic-integration job has
  shifted, not disappeared: from "reconcile the addressing split" to
  "verify the addressing split holds under every scenario, and integrate
  the newer secrets/locking/realness sections against assay and run-gate,
  neither of which any of this touched."
- Findings that turned out to be **not** ciu bugs (a shipped mechanism that
  nobody had used yet) sit in the same backlog, using the same numbering
  scheme, as findings that **are** real gaps — a reader has to chase each one
  down individually to know which is which (see §6 below for the specific
  list; don't re-derive it).
- Three tools, three independent TOML config surfaces (`ciu.*.toml.j2`,
  `assay.toml`, `run-gate.toml`), and nothing today cross-validates that a
  `run-gate.toml` lane pointing at an `assay.toml` lane pointing at a ciu
  stack is internally consistent. Every cross-tool mismatch found so far
  (§6) was found by a human/agent noticing it by hand, not by any check.
- Several scenarios that real deployment will need — remote/multi-host
  deployment, VPN or sidecar-proxy transport, firewall-scoped traffic — are
  **named** as open questions in `V8-REALIZATION-GRAPH.md` but never actually
  walked through against the proposed schema to see whether it would work.

**Your job is not bounded by any of the sources above.** Every document
named in §1 — including dstdns's own specs like
`spec-ciu-provisioning-model.md`, which reads like a settled design but is
one operator's proposed idea from before most of the rest of this existed —
is **one proposed answer among several, never a constraint**. Nothing in
any of these sources is settled. If the best answer to a scenario is
something none of them proposed — a different split, a different
unification, a mechanism nobody here has named — propose it. Reconciling
existing proposals is the FLOOR, not the ceiling, of what this task wants.
§2 spells out exactly how far this freedom extends (including breaking
changes) and what principles should guide you when you use it.

Treat every existing document as evidence, not authority. Two docs
disagreeing means at least one is wrong, stale, or simply not the best
available answer — your job is to determine which, using the tools' actual
source and SPEC as one input among several (not an automatic tiebreaker:
the source shows what ships TODAY, not what SHOULD ship), and to say so
explicitly rather than picking one silently.

### 0.1 Interview mode — the operating rule for this entire task

Read all of §1 first, in full, before asking anything — an ill-informed
question wastes the operator's time and this prompt's own §7 already says
so ("do not escalate something you could resolve by reading further"). Once
you have read everything, **do not silently resolve a genuine design fork
and move on.** A genuine fork is any choice with no technically-forced
answer: a naming call, an entity-shape decision (does this need its own
entity, or does it ride on an existing one), a simplicity-vs-completeness
tradeoff, anything a reasonable engineer could land on either side of.
Compare this against a forced answer: if the tool's own source/SPEC (§1.3–
1.5) settles it, or a scenario in §5 mechanically breaks one option, that's
not a fork — resolve it yourself and show the reasoning (§4.3), the same as
before.

**When you hit a genuine fork, stop and ask, live, using the
`AskUserQuestion` tool.** Present 2–4 concrete options with a one-line
tradeoff each — never an open-ended "what do you think?" If you have a
recommendation, make it one of the options and say why, but let the
operator choose; do not pick silently and present it as settled even when
you're confident. This is the actual mechanism that resolved secrets
(§10.3), locking (§10.11/§10.12), and realness immutability (§10.13) in
`CIU-V8-TESTING-GATE-PROPOSAL.md`'s own revision history — read a few of
those sections if you want the pattern to imitate, not as content to carry
forward verbatim (see the header above on revision 2.0 dropping their
archaeology style, not their substance).

**Not every fork converges to one answer, and that's a legitimate
outcome, not a failure to decide.** If, after discussion, the operator
doesn't have — or doesn't want to commit to — a single preference (a
question that's genuinely deployment-dependent, or where reasonable
designs trade off differently for different consumers), don't force
convergence. Instead the WRITTEN PROPOSAL itself may present the surviving
alternatives side by side, each with its pros/cons and your recommendation,
rather than picking one — §4.3a spells out exactly how. This is expected to
happen at least once: the topology/transport schema (§6, scenario 3/4) was
already flagged, before this interview even started, as the area most
likely to need this treatment — see §6's own topology bullet for why (only
one of three `transport` values has ever been worked through, and the
`direct` value is itself ambiguous between two genuinely different
guarantees). Treat that flag as a hint of where this might land, not a
predetermined conclusion — the interview may resolve it cleanly, or may
not; find out rather than assuming either way.

§7's "Open-decision rule" still applies, narrowed: use §4.9 only for a fork
you could not put to the operator at all (they were unavailable, or
explicitly deferred a specific question to a later session) — not as a
substitute for asking. If the operator is present, ask; §4.9 is the
fallback for when asking wasn't possible, not the default resolution path.

---

## 1. Context to read first — in full, before you write anything

This list is deliberately exhaustive. Budget for it; do not skim. Where a
size is noted, that is the file's current line count, not a suggestion to
read less than the whole thing.

### 1.1 The two v8 design docs — read completely, they are the primary subject

- **`ciu/docs/CIU-V8-TESTING-GATE-PROPOSAL.md`** (2868 lines) — the original
  v8 proposal: native testing gate, logical services, environment instances.
  **Proposal revision 1.10, dated 2026-08-27 — updated since this prompt was
  first written (2026-08-26); read the current file, not the description
  that follows as a substitute for it.** The blockquote note at §1.15
  (~line 400, not line 381 — the file has grown) still flags the old
  `<stack>.<service>` compound-key addressing as flawed, but now carries its
  own "**Applied 2026-08-27:**" follow-up confirming the fix was actually
  folded back in: §1.15, §3.1–3.3, §4.1–4.2, and §8 all now use
  `V8-REALIZATION-GRAPH.md`'s entity/realization split
  (`[service.<name>]`/`[ciu_stack.<name>]`/`[compose_stack.<name>]`/
  `[external.<name>]`, `realized_by` kind-qualified) in their worked
  examples, not the compound-key form. **Do not take this as settled** —
  it means the fold-back this prompt originally called for is done, not that
  it has survived contact with every scenario in §5 (nobody has walked it
  end-to-end against remote deployment, shared-infra join, or the
  access/transport question yet — that is still this integration's job).
  Four more sections landed in the same 2026-08-27 session, none of which
  existed when this prompt was first drafted and none of which are reflected
  in §4.3/§6 below yet — read them directly:
  - **§10.3** "Secrets architecture — RESOLVED" — `ciu.secrets.toml` as a
    second SSoT coexisting with Vault (not a vaultless-only fallback),
    realization-kind-qualified addressing for secrets, a materialized-copy
    naming scheme fixed twice for real collision risks, and a delivery-mode
    axis (file default / env opt-in / fixed-path / native-Vault-SDK) with an
    explicit weaker-option warning on env delivery — bears directly on
    scenario 8 below.
  - **§10.11** "Instance-level run mutex" (new) — a whole-run mutex per
    `INSTANCE_ID`, targeting `ciu.global.defaults.toml.j2` as the lock file
    (verified never runtime-written).
  - **§10.12** "Concurrency model — how the locking mechanisms compose"
    (new) — reconciles this new mutex against S4.26 (secrets lock), the
    worktree allocation lock, S16.3's budget lock, and §5.7's gate-admission
    logic; directly extends this prompt's own M8 concern (§6 below).
  - **§10.13** "Realness is immutable per environment instance" (new) — a
    firm design stance that realness selection is fixed at `ciu up` time for
    an instance's whole life (DB-state-leakage risk on live swap) and can
    only change via `ciu down`/`ciu clean` + `ciu up`; bears directly on
    scenario 5 below.
  - **§10.14** "Shared infrastructure (was M7) — problem sharpened, NOT
    resolved" (new) — a worked monorepo example (this very checkout:
    `vbpub/ciu`, `vbpub/cmru`, `vbpub/assay` as independent `ciu.global`
    roots) showing S16.1's shared-infra join is welded to `ciu worktree add`
    and has no path to apply outside it; left OPEN, two-way fork undecided.
    Bears directly on scenario 6 below — read this before re-deriving the
    same finding from scratch.
  Also note a structural quirk while navigating: §11 ("Open issues from
  adversarial review") is interleaved INSIDE §10 in the file's actual
  section order — §10.1–10.3, then all of §11, then §10.4–10.14 resume. Not
  a content problem, just don't assume `wc`/ToC order matches heading-number
  order when searching.
- **`ciu/docs/V8-REALIZATION-GRAPH.md`** (381 lines) — the corrective design
  note: splits LogicalService from Realization from RealnessVariant to fix
  the identity-coupled-to-location problem in the proposal above. Contains a
  fully-worked dstdns example, a corrected multi-wave dependency trace with
  its own three-round correction history kept intact (read that history —
  it's the model for how you should write your own reasoning in the output
  document, see §4), and a "Resolved / open" list you must treat as a
  starting inventory, not a finished one.

### 1.2 Supporting v8-track documents in `ciu/docs/` — read completely

- **`ciu/docs/SPEC-RECONCILIATION-2026-08-24.md`** (208 lines) — the audit
  that motivated v8 in the first place: SPEC-vs-implementation mismatches,
  missing SPEC coverage, dstdns schema gaps. Foundational context for why
  any of this started.
- **`ciu/docs/BACKLOG-2026-08-24.md`** (671 lines) — backlog items derived
  from that audit. **Uses a different numbering scheme** (`CIU-QOL-N`) from
  `KNOWN_ISSUES_TODO_BACKLOG.md`'s `CIU-N` — do not conflate the two; check
  both when you look for whether something is already tracked.
- **`ciu/docs/DSTDNS-ACTIONS-2026-08-24.md`** (158 lines) — items dstdns can
  do with the *existing* (pre-v8) schema. Useful negative space: anything
  here is evidence that a v8 schema change is not actually required to solve
  that particular problem, and the wholistic proposal should not claim
  otherwise.
- **`ciu/docs/CIU-V8-TESTING-GATE-ADVERSARIAL-REVIEW.md`** (129 lines) —
  marked `STATUS: STALE` in its own header (reviewed the *initial
  unversioned* proposal, 2026-08-23), but its blockers (B1: logical services
  don't model stacks containing multiple containers/init-jobs/hooks) are
  exactly the tension `V8-REALIZATION-GRAPH.md` was later built to resolve.
  Check each of its blockers against the current state: resolved, still
  open, or resolved differently than this review anticipated. **As of
  2026-08-27 this file itself has one dated correction** — B3's entry has a
  "Superseded 2026-08-27" note pointing at proposal §10.3 and correcting
  the original ruling's claim that `ciu.secrets.toml` "replaces"
  `.ciu/secrets/*` storage (it relocates, doesn't eliminate). **B1's entry
  was NOT similarly updated**, even though it's now resolved too (proposal
  §1.15/§3.1–3.3/§4.1–4.2/§8) — its "Status: RESOLVED by clarification,
  proposal needs revision" line is itself now stale (the proposal HAS been
  revised); don't take that line's wording at face value, check the
  proposal directly the way this bullet already tells you to.
- **`ciu/nyxloom-trove/roadmap.md`** (94 lines) — ciu's live nyxloom roadmap.
  **Not v8-specific** — its current milestone is "automation-safe worktree
  lifecycle," a different track from the testing-gate/logical-services work.
  Read it anyway, for two reasons: cross-cutting awareness (does anything in
  worktree-lifecycle automation touch the same identity/naming layer as
  CIU-66/CIU-51?), and because it's the canonical place to notice if v8
  work is scheduled to collide with it.
- **`ciu/nyxloom-trove/decisions.md`** (122 lines) — ciu's own decision log
  (distinct from `dstdns/nyxloom-trove/decisions.md`, §1.6 below — don't
  conflate the two repos' logs). Same caveat as the roadmap: D-001 onward is
  about the worktree-lifecycle milestone, not v8, but small enough to read
  in full and worth it for the same cross-cutting reason.
- **`ciu/nyxloom-trove/backlog.md`** (8 lines) — **do not chase this as a
  second backlog.** It is a deliberate stub: "intentionally unused for
  active product work until nyxloom's per-entry backlog schema is
  implemented... CIU's temporary canonical tracker is
  `../KNOWN_ISSUES_TODO_BACKLOG.md`." Confirms, doesn't contradict, §1.3's
  citation below.
- **Explicitly out of scope, checked and excluded, not silently skipped:**
  `ciu/docs/plans/V2-PACKETS.md` (a v2-era work-packet execution board — one
  packet per commit, Status column, no design content) and
  `ciu/nyxloom-trove/ciu-config-wave-BRIEF-2026-08-19.md` (self-declared in
  its own header: "This brief is now historical" — a closed implementation
  brief for the config-wave milestone, not v8-testing-gate design). Neither
  bears on this task; both were checked, not assumed irrelevant from their
  filenames.

### 1.3 ciu's actual product surface — read completely

- **`ciu/docs/SPEC.md`** (4133 lines) — the normative contract. This is the
  ground truth for "what does ciu actually guarantee today." Every schema
  proposal in the v8 docs must be checked against this, not against another
  proposal document's paraphrase of it.
- **`ciu/docs/CONFIG.md`** (1363 lines) — the config schema reference
  consumers actually use. **New since this prompt was drafted:** a
  "What CIU natively does with Vault, vs. what hooks do" subsection
  (2026-08-27, right after the `[vault]` section) — a 3-row table of the
  three overlapping Vault declarations (`[vault].stack_path`,
  `[vault.paths]`, `[topology.services.vault]`) plus prose drawing the exact
  boundary: CIU's own code is a KV2 client only (read/write against a given
  address+token); init/unseal/policy/AppRole is entirely project-authored
  hook code using CIU's generic `post_compose_*`/`[state]` mechanisms. Read
  this before scenario 1 below — it's the ground truth for "what does ciu
  actually do with vault" that the scenario asks you to trace.
- **`ciu/docs/CONSUMERS.md`** (942 lines) — the adoption contract; worked
  examples of how a real consumer (dstdns) wires up ciu.
- **`ciu/docs/DESIGN-GUIDE.md`** (480 lines) — naming/identity hazards ciu
  has already documented (the §3.6-class DNS-alias ambiguity family).
- **`ciu/KNOWN_ISSUES_TODO_BACKLOG.md`** (2167 lines) — the live backlog.
  You do not need to read the whole thing line-by-line, but you must locate
  and read **every entry from CIU-45 through the newest entry** (currently
  **CIU-68**, not CIU-66 as this prompt originally said) — this is the exact
  span produced by the design sessions this integration is meant to
  reconcile, and §6 below tells you what each one means so you don't have to
  re-derive it, but you must still read the entries themselves for the full
  reasoning and proposed contracts. **CIU-67/CIU-68 are new since this
  prompt was drafted** (filed 2026-08-26, from a live fresh-deploy failure,
  `dstdns` D-212): CIU-67 is `deploy.health.timeout` being read for two
  incompatible purposes (a per-probe Docker HEALTHCHECK duration vs. the
  S7.7 inter-phase health-gate's overall wait budget) with no distinct
  config key, proposing a new `deploy.health.gate_timeout` key; CIU-68 is
  the S7.7 health gate not running by default under a bare `ciu up` (needs
  both `--deploy` and `--healthcheck`, undiscoverable from `--help`) plus
  its live-probe path having zero retry. Both propose schema changes — fold
  them into §4.5's per-key validation tables, don't treat them as pure
  operational bugs out of scope just because a dstdns-side workaround
  already shipped.
- **`src/ciu/deploy.py`, `src/ciu/provisioning.py`, `src/ciu/composefile.py`,
  `src/ciu/warn_policy.py`** — read these four modules directly, not just
  docs about them. Several backlog findings this session (CIU-63, CIU-65,
  CIU-66) were only discoverable by reading the actual function signatures
  and refusal logic, not the SPEC's description of them. Trust source over
  prose when they disagree, and say so if you find another instance of this.
- **`src/ciu/hook_templates/post_compose_db.py`** — the one shipped
  reference implementation of `validate_config()` (S9.5). Confirm for
  yourself whether it is single-stack/self-referential only (as CIU-63/64/65
  found) or whether anything has changed.

### 1.4 assay's actual product surface — read completely

assay has no separate `SPEC.md`; treat these as its normative surface:

- **`assay/README.md`** and **`assay/docs/CONSUMERS.md`** (747 lines) — the
  adoption contract.
- **`assay/docs/DESIGN-GUIDE.md`** (1874 lines) — design rationale; read in
  full, it explains *why* assay's lane/judge/rigor model is shaped the way
  it is, which you need before proposing any change that touches it.
- **`assay/src/assay/schemas/verdict.schema.json`** (2383 lines) — the
  actual machine-checkable verdict contract. This is ground truth for what a
  verdict can and cannot contain — more authoritative than any prose
  description, including this prompt's.
- **`assay/CHANGES.md`** — read from v2.4.0 onward at minimum; this
  integration must account for the `whole_target`+`judge.base` refusal
  (2.4.2) and the `mutation.progress_artifact` removal (2.4.1), both of
  which just required a dstdns-side config fix (`dstdns@41b07b22`, D-211) —
  confirm whether either has a schema-design implication for v8 beyond the
  immediate fix already applied.

**Missing from this section until 2026-08-29: `assay/nyxloom-trove/` is a
full nyxloom-trove, same shape as ciu's and dstdns's, and it was not being
read at all.** It carries assay's own north-star, product definition,
roadmap, backlog, and a schema-design rationale doc — all genuinely part of
assay's product surface, not internal pipeline scaffolding. Read these too:

- **`assay/nyxloom-trove/1-north-star.md`** (111 lines) — why assay exists:
  four independently-diverged `coverage_gate.py` copies plus a Go
  reimplementation, the cost of pricing a shared capability as "adopt an
  opinion." Read this before proposing any change to assay's lane/judge
  model — it's the design philosophy DESIGN-GUIDE.md's specifics sit on top
  of.
- **`assay/nyxloom-trove/2-product-definition.md`** (528 lines) — read in
  full; the detailed product-scope companion to the north-star.
- **`assay/nyxloom-trove/3-roadmap.md`** (168 lines) — read in full;
  `kind: roadmap`, milestones M1 (done) onward — check whether anything on
  it interacts with what this integration proposes.
- **`assay/nyxloom-trove/4-backlog.md`** (3116 lines, ~186KB — **do not
  read line-by-line**) — `kind: backlog`, one YAML-frontmatter line per item
  (`{id: B0xx, title: "...", type:, component:, context_estimate:}`) with
  prose rationale below. Read the **full frontmatter item list** in one
  pass — it's already a complete one-line-per-item inventory, cheap to scan
  even at this size — then read the **prose rationale** only for items
  whose title plausibly touches this integration (schema/verdict changes,
  ciu integration, `whole_target`, provenance, distribution/pinning). This
  is assay's real, actively-maintained backlog — do not treat it as
  optional just because it's large.
- **`assay/nyxloom-trove/SCHEMA-V5-DESIGN.md`** (351 lines) — read in full.
  This is NOT covered by `verdict.schema.json` alone: it's the rationale
  doc for WHY the verdict schema is shaped the way it is (what it
  deliberately does and does not own — see its own header note on schema
  ownership). Directly relevant to §4.6's spec/schema check and to any
  cross-tool schema-validation design you propose.

**Explicitly out of scope in `assay/nyxloom-trove/`, checked and excluded,
not silently skipped:** `STATE.md`, `decisions.md` (415KB — if you suspect
something in it bears on this integration, `grep -i "ciu\|v8\|testing.gate\|
schema"` it rather than reading it whole), `WORKFLOW.md`,
`WAVE-CONTROLLER-PROMPT.md`, `FROZEN-WAVE-*.md`, every `W1/W2/W3-CARVE-*.md`
and `*-RESUME.md` — these are assay's own internal implementation-pipeline
dispatch machinery (which packages were carved, reviewed, and merged, and
when), analogous to dstdns's own core-workflow controller docs. Not product
surface; not needed for a schema-design task.

### 1.5 run-gate's actual product surface — read completely

- **`run-gate-project/SPEC.md`** (484 lines) — the normative contract.
- **`run-gate-project/CONSUMERS.md`** (437 lines) — the adoption contract.
- **`run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md`** and **`CHANGES.md`** —
  check for anything V8-relevant (lane/environment model, pin verification,
  conjunction-lane semantics) that the ciu-side v8 docs may not know about.

### 1.6 dstdns's actual, live usage — the concrete substrate every proposal must survive contact with

**Read the sub-bullets below as evidence of what dstdns needs and what it
tried, never as a spec you are implementing.** `spec-ciu-provisioning-model.md`
in particular reads like a settled design (it has a `Status: spec` header),
but it is one dstdns operator's proposed idea from 2026-06-22, written before
most of what §1.1–1.5 describes existed — nothing in it is load-bearing on
your own design unless you independently conclude it's still the right
answer. The same goes for every other dstdns doc in this section: they tell
you what was tried and why, not what must survive into your integration.

- **`dstdns/docs/spec/spec-ciu-provisioning-model.md`** — dstdns's own
  proposal for the `requires`/`provides` model, one input among several
  (`requires`/`provides` itself has since shipped and evolved well past what
  this doc describes — e.g. `stack:*:healthy|completed`, CIU-63 through
  CIU-66 — so treat it as a historical record of the ORIGINAL idea, not the
  current shape). Its §8.3 already tracks one limitation as RESOLVED
  (per-phase preflight, confirmed live in ciu 7.3) — check whether anything
  else in it is now stale, superseded, or was simply the wrong call in
  hindsight, the same way.
- **`dstdns/docs/spec/spec-ciu-remote-ssh-deploy.md`** (SPEC J, 295 lines) —
  ciu's remote-SSH transport (`ciu ssh`, `ciu up --host`), the actual
  mechanism for scenario 2 in §5 below. Its header cross-references "SPEC H
  (Consul deploy-control + Tailscale)" as a complement — search for SPEC H's
  actual location yourself (not confirmed to exist as a standalone file in
  this checkout at the time of writing this prompt) before citing it in your
  output; do not assume it exists just because it's named here.
- **`dstdns/nyxloom-trove/GUIDE.md` §3** (multi-stack / per-worktree stacks
  — Mode A attach vs Mode B isolate, identity dimensions, teardown rules,
  the §3.6 cockpit-alias-ambiguity hazard). This is the ONLY place the
  multi-instance/worktree-isolation requirements are written down as an
  operational contract rather than a design aspiration — any v8 schema
  proposal that doesn't satisfy this section's actual rules is not
  deployable, regardless of how clean it looks on paper.
- **`dstdns/nyxloom-trove/decisions.md`** — 5693 lines total, **do not read
  in full**. Read these specific ranges: **D-094 through D-101** (the
  config/landscape program charter — config v3 L0/L1/L2 model, SM2 secrets,
  boundary decisions), **D-170** (the CIU-45 disposition — the canonical
  example of "ciu can't express X" turning out to be "nobody looked"),
  **D-204 through D-212** (this session's own wave: merge-rigor policy,
  queue-item closures, the ciu 7.2/7.3 adoption chain, the stack:*
  dependency fix, the assay 2.4.2 repin, and — D-212 — the live fresh-deploy
  health-gate failure that produced CIU-67/CIU-68 above). **D-213 onward is
  a different, unrelated track** (`core-workflow`, invariants/cancel-fencing/
  admission — currently at D-228 and climbing) — do not read past D-212
  looking for more v8/ciu material, there isn't any there. Search the file
  for other `D-` numbers referenced BY the D-094–101/D-170/D-204–212 ranges
  and follow those pointers as needed, but do not read the file end to end.
- **`dstdns/ciu.global.defaults.toml.j2`** (1373 lines) — the actual
  rendered-from config for a real 16-stack deployment. Every schema key you
  validate in §4's tables should be checked against whether this file (or
  its per-stack siblings under `applications/*/`, `infra/*/`,
  `infra-global/*/`) already uses an equivalent, differently-shaped
  mechanism that v8 would need to migrate.
- **`dstdns/run-gate.toml`** (227 lines) and **`dstdns/assay.toml`** (838
  lines) — the actual, currently-working cross-tool config. This is your
  ground truth for "what does gluing these three tools together really look
  like today," including its accidental duplications (see §6, the
  version/sha256/filename triple-encoding of the same fact, D-211).

---

## 2. What "wholistic integration" means here — the standard to hold yourself to

**Not** a summary of the existing docs, and **not** bounded by them either.
Every source in §1 is one proposed answer to a problem, written by someone
who did not see the whole picture you now have. Your synthesis is free to:

- **Propose something none of the sources named** — a different entity
  split, a different unification of two things the sources kept separate, a
  mechanism nobody here has written down. "All the proposed solutions could
  be bad" is a live possibility you must actually consider, not a rhetorical
  disclaimer — if you conclude that after honestly weighing them, say so and
  propose your own.
- **Make breaking changes.** v8 exists specifically because dstdns's own
  `AGENTS.md` §4.1 (greenfield only, no dual-naming, no deprecated paths
  left running) makes clean cutovers the norm here, not the exception. A
  correct design that requires every consumer to migrate is preferable to
  an incorrect one that's backward-compatible. Where you propose a breaking
  change, say what breaks and roughly what a consumer's migration looks
  like (this is a completeness requirement on your reasoning, not a reason
  to avoid the change) — do not implement the migration.
- **Question mechanisms that already exist, not just gaps.** A tool being
  *used* today is not evidence it's being used *well*. Concretely: dstdns
  renders every config surface through Jinja2 templates
  (`ciu.*.toml.j2`) — is that mechanism actually being used to its
  potential (declarative, DRY, single-source-of-truth generation), or is it
  mostly plumbing bare values through unchanged, with the real logic living
  elsewhere (Python hooks, hand-maintained per-stack repetition — see
  CIU-51's `qname()` proposal, which exists specifically because the current
  templates DON'T use Jinja to eliminate repetition)? Answer this
  concretely, with evidence from the actual templates in §1.6, not in the
  abstract — and apply the same question to any other existing mechanism you
  find yourself tempted to keep just because it's already there.
- **Re-derive the whole schema's shape, not patch around the parts v8's
  sources happened to name.** The gaps found this session (vault-liveness,
  schema-completion, container naming, the `<stack>.<service>` addressing
  coupling) were found because someone happened to trace a specific
  scenario. There is no reason to believe they're the only ones. §4.5's
  validation table (full schema, every key, argued from first principles —
  not just the keys v8's sources happen to touch) exists precisely to catch
  what scenario-tracing alone would miss.

### Guiding principles — hold every design choice to these, and name which one(s) justify it

These aren't a checklist to run once at the end; they're the lens for every
decision in §4.3. Where a principle and an existing mechanism conflict,
the principle wins and the mechanism is a **candidate to drop** (§4.8) or
change, not a constraint the principle has to fit around.

1. **Single source of truth.** A fact that two config surfaces (or two keys
   within one surface) can independently state is a fact that WILL drift —
   this session found exactly this (`run-gate.toml`'s `pins.assay.version`
   and its `.pyz` filename encoding the same release in two shapes that
   share no substring, D-211). Every fact should have exactly one place it's
   declared; everywhere else derives it or references it.
2. **Fail fast, not fail eventually.** A missing or wrong value should
   refuse loudly at the earliest point it CAN be checked (ideally: authoring
   time via schema validation; failing that, `ciu check`; failing that,
   deploy time) — never silently substitute a default that happens to work
   until it doesn't, and never let a wrong value surface only as a confusing
   runtime symptom three layers away from its cause.
3. **Explicitness over magic.** If a value is derived, the derivation should
   be visible/discoverable (in rendered config, in `ciu check` output, in
   `--help`), not an implicit behavior a reader has to already know about or
   find by reading source. This is dstdns's own `AGENTS.md` §4.2a stated as
   a general design principle: a default is a hazard the moment it
   substitutes for a fact that exists somewhere else — apply that test to
   every implicit/derived value you propose.
4. **Mechanical checkability.** Prefer a schema shape a program can validate
   completely (types, referential integrity, graph completeness) over one
   that relies on a human reading carefully. If two facts must agree (a lane
   referencing a stack that must exist; an assay lane a run-gate lane
   references), that agreement should be something `ciu check` or an
   equivalent can verify, not something only a careful reviewer catches —
   §4.6 (spec/schema check) is where you make this concrete.
5. **Full preflight validation.** Everything checkable without side effects
   should be checked before anything with side effects runs — this is
   CIU-64's finding (`ciu check` should run automatically before `ciu up`)
   generalized: the principle isn't "add one more check," it's "no class of
   error that COULD be caught statically should ever be discovered by a
   live deploy instead."
6. **One authoritative derivation per identity, used everywhere.** Where the
   same identity fact needs to appear in multiple forms (a container name, a
   DNS hostname, a compose service key, a config-referenced `internal_host`),
   there should be one function/mechanism computing all of them, not several
   independent reimplementations that can individually go stale — CIU-66
   names exactly this problem for the current container-naming layer.
7. **Minimize special-casing per mechanism kind.** A generic check that's
   blind to a specific kind's real semantics is a smell (CIU-63: the static
   graph lint doesn't know `stack:*` refs resolve by live probe, so it wrongly
   demands a `provides` declaration nothing reads). Prefer a schema/validator
   shape where adding a new kind of reference, secret directive, or
   dependency doesn't require hand-adding a carve-out to every consumer of
   that shape.
8. **Separate declaration from resolution.** The LogicalService/Realization
   split is one instance of a more general principle: declaring that
   something is NEEDED should be a different concern from declaring HOW it's
   currently satisfied. Look for other places in the schema where these two
   are currently fused, and consider whether splitting them the same way
   would help (or whether it would just add indirection for no benefit —
   this principle can be over-applied).
9. **Config as data, not a place for logic to hide.** Templates should
   generate declarative data; if a `.j2` file's control flow IS the
   business logic (not just value substitution), that's a sign the logic
   belongs in the tool, not the template. This is the concrete form of the
   Jinja2 question above.

Add more if, while doing the work, you find yourself repeatedly justifying a
choice the same way and it isn't on this list — name the principle
explicitly when you do, the same way you'd name any other tool in your
reasoning.

---

## 3. Work — do these steps in order

1. Read every source in §1, in full, as specified.
2. Build an inventory of every distinct mechanism/idea proposed anywhere in
   the sources (both V8 docs, the four supporting docs, every CIU-45..66
   backlog entry, every relevant decision) — AND of every existing schema
   key/mechanism in ciu/assay/run-gate's actual current config surface,
   whether or not any v8 source mentions it (§4.5 requires you to argue for
   or against every one, not just the ones already under discussion). Tag
   each one: **SHIPPED** (already works, verify how), **PROPOSED — NOT
   BUILT**, **CONTRADICTED** (conflicts with another source — name both),
   **SUPERSEDED** (an earlier idea a later document already replaced, but
   the earlier document was never updated to say so — `V8-REALIZATION-GRAPH.md`'s
   own git history this session is full of these), or **QUESTIONABLE**
   (exists and works, but §2's principles suggest it may be the wrong shape
   — this tag is new work you're doing, not something any source will have
   already flagged).
3. For every **CONTRADICTED** item: determine which side is actually
   correct by checking the tool's real source/SPEC (§1.3–1.5), and write the
   resolution into your reasoning (§4's elongated-reasoning requirement) —
   don't just state the answer, show why the other side was wrong, the same
   way `V8-REALIZATION-GRAPH.md`'s "Full system trace" section keeps its own
   three-round correction history rather than only the final answer.
4. Walk every scenario in §5 against your emerging integrated schema. Where
   a scenario reveals the schema doesn't work, that's a required schema
   change, not a footnote — fold it back in and re-check the other
   scenarios you already walked for regressions. **Before writing a TOML
   shape for any genuinely new schema concern, name its entities and
   relationships first — ERD-style, the same way `V8-REALIZATION-GRAPH.md`
   did for LogicalService/Realization/RealnessVariant (§6's closing
   bullet).** The topology/transport layer (§6, scenario 3/4) is the
   concrete case in front of you and the reason this rule is being stated
   explicitly (operator directive, 2026-08-29): its current few keys
   (`transport`, `address`) were designed against exactly one scenario
   (remote deployment) and never checked against any other, which is
   directly why `direct`'s meaning is still ambiguous and `proxy` has never
   been worked through — a TOML-first design with no named entities behind
   it. Whatever you propose for topology/transport must (a) be modeled as
   entities/relationships before any key is written — is there a `Transport`
   entity distinct from `Host`? is a route a relationship between a
   Realization and a `Host`, or between two LogicalServices, or something
   else? — and (b) be walked against EVERY scenario in §5, not only 3 and 4:
   does it need to say anything for vault-liveness (1, is the vault
   reachable across whichever transport applies?), remote deployment (2),
   shared-infra join (6, does a joining instance need transport facts to
   reach services on the reference instance's host?), and credential
   delivery (8, does a delivery mode silently assume same-host
   reachability?). Apply the same "entities before keys, then the full
   scenario set" discipline to any other new schema concern you find
   yourself needing, not only this one.
5. **Interview the operator (§0.1) on every genuine fork surfaced by steps
   2–4** — every CONTRADICTED item whose resolution isn't forced by source/
   SPEC, every QUESTIONABLE item, every scenario-driven schema choice with
   more than one defensible shape. Do this as its own pass, live, before
   step 6 — do not silently decide these while building the inventory and
   only surface them in a written §4.9 afterward. Where the interview
   converges, note the decision and move on. Where it doesn't (§0.1's
   "not every fork converges" case), note that too — it feeds §4.3a, not
   §4.9.
6. Decide what to **build** (name the concrete ciu/assay/run-gate mechanism,
   its rough shape, which tool owns it) and what to **drop** (name the
   specific proposed idea and why it's wrong, superseded, or not worth its
   complexity) — both with reasoning, not just a verdict. This should now be
   mostly mechanical: step 5 already settled the genuine forks.
7. Write the full per-key validation tables (§4.5) for **every key in
   ciu's entire config schema** (stack-level, global, and every other
   surface named in §1) **at every nesting level** — not only the keys the
   v8 sources happen to discuss. A key that's fine as-is still needs its row
   (reason for existence, argued from §2's principles); a key you can't
   justify is a finding (§4.8 drop candidate), not something to silently
   omit from the table.
8. Do the spec/schema check (§4.6): validate the integrated proposal against
   the real upstream mechanisms named in §1.3–1.5, and sketch how the
   proposal's own schema would itself become a checkable schema (tie this to
   ciu's existing S5.7 schema-validated configfile render and S9.5
   `validate_config`, extended per CIU-63/64/65's findings — see §6).
9. If, anywhere in steps 1–8, you find an actual defect in ciu, assay, or
   run-gate's shipped behavior (not a design gap the integration itself is
   meant to resolve — a real bug: wrong behavior against the tool's own
   documented contract) — file it upstream using the `backlog` skill/
   convention (search the target tool's `KNOWN_ISSUES_TODO_BACKLOG.md`
   first, write it with enough mechanism + reproduction + proposed contract
   that an implementer doesn't have to re-derive your reasoning, the same
   discipline CIU-63/64/65/66 in this session's own backlog entries used).
   File as you find them, don't batch them for the end. Design gaps and
   "this mechanism is the wrong shape" findings do NOT go to the backlog —
   those are exactly what §4 is for.
10. Rewrite `ciu/docs/CIU-V8-TESTING-GATE-PROPOSAL.md` **in place** as
    revision 2.0, per the structure in §4, including §4.11 (non-breaking
    improvements). Bump its header (`**Proposal revision:**`,
    `**Updated:**`) the same way every prior revision has, but this is the
    one revision bump that also cleans out the accumulated dated-archaeology
    style — see §4's opening note before you start writing.

---

## 4. Output document structure — `CIU-V8-TESTING-GATE-PROPOSAL.md`, revision 2.0

Use this as a checklist, not a rigid template — adapt section order if a
different structure serves a clearer read, but every item below must appear
somewhere and be locatable.

**Two-part shape — read this before writing anything.** Revision 2.0
rewrites the file **in place**, not as a patch on top of 1.10 (§4's opening
header note already said so; this is where it becomes concrete). Structure
the document in two clearly-separated parts, in this order:

- **Part 1 — The Proposal** (§4.1, §4.3a, §4.4, §4.5, §4.6, §4.11): the
  normative spec itself. Written as if authored fresh — config model,
  entities/relationships, schema, migration notes. **No dated
  "Superseded 2026-08-27" / "Applied 2026-08-27" / "corrected on date X"
  self-referential editing-history notes anywhere in this part** — that
  convention was right for the iterative working draft (1.5→1.10) and is
  wrong for the document meant to stand as THE v8 proposal. Where a design
  choice needs justification, argue it affirmatively and in present tense
  ("the entity/realization split exists because coupling identity to
  location breaks under a stack rename; a single compound-keyed table was
  rejected because X" — not "this used to say Y, corrected 2026-08-27
  because..."). Where §3.a applies, present alternatives inline instead of
  forcing one answer.
- **Part 2 — Design Rationale & Audit Trail** (§4.2, §4.3, §4.7, §4.8, §4.9,
  §4.10): everything a reader would want in order to audit or challenge
  *how* Part 1 was reached, clearly demarcated as a separate part (a real
  section break, not just a heading) so a reader of Part 1 alone gets a
  clean, unified spec, and a reader who wants the full derivation can keep
  going. This is where the "keep the reasoning trail" discipline from
  earlier revisions still belongs — it's just no longer interleaved through
  the normative sections themselves.

### 4.1 Header
Status, source documents integrated (list them), what this document
supersedes/refines, revision (2.0), date. State plainly that 2.0 supersedes
every prior revision (1.5 through 1.10) of this same file — it does not
coexist with them the way 1.5→1.10 each incrementally patched the last; a
reader should never need to consult a prior revision to understand 2.0.

### 4.2 Inventory *(Part 2)*
The tagged mechanism inventory from Work step 2, as a table: mechanism |
source doc(s) | tag (SHIPPED/PROPOSED/CONTRADICTED/SUPERSEDED/QUESTIONABLE) |
one-line disposition.

### 4.3 Elongated reasoning — the integrated design, walked through *(Part 2)*

This is the bulk of Part 2. Write it so a reader with no memory of the
source discussions can follow *why* each decision landed where it did, not
just what the decision was — including what the interview (§0.1/Work step
5) actually decided and why, not only what you reasoned out alone. Unlike
Part 1, this section MAY narrate correction history the way
`V8-REALIZATION-GRAPH.md`'s "Full system trace" section does (three waves →
five waves → zero gaps) — that convention is exactly right here, in the
audit trail, and exactly wrong in Part 1's normative prose (§4's opening
note). When you resolve a contradiction, drop an idea, or the interview
lands on a decision, show the reasoning that got you there, not just the
verdict. A reader should be able to reconstruct your judgment, agree or
disagree with a specific step, and not have to take the conclusion on
faith.

Cover, at minimum: the LogicalService/Realization/RealnessVariant split and
whether it survives contact with every scenario in §5; the container-naming
identity problem (CIU-66/CIU-51 — does the final schema use ONE identity
derivation for hostname, container_name, compose service key, and
`internal_host`, or does it still have multiple independent
re-implementations of "qualify with project+instance"?); the
`[deploy.phases]` → computed-topological-sort transition and whether the
graph-completeness check it depends on (§4.6) is actually specified, not
just gestured at; the shared-infra join mechanism (S16.1/CIU-22/CIU-52)
versus realness-variant selection — are these the same axis or two that
interact, and how does a joining instance pick both at once; the
access/transport layer as its own first-class schema concern (scenario 3,
§5 — not folded silently into whatever entity happens to need it first),
**written up ERD-style (named entities/relationships preceding any TOML
key, per Work step 4's operator directive) and walked against every
scenario in §5, not only 3 and 4** — show the entity table the same way
`V8-REALIZATION-GRAPH.md`'s own precedes its worked example, not just the
final keys; and, explicitly, whether Jinja2 templating is the right mechanism used well
or a crutch for something ciu itself should own (§2's principle 9) — with a
real verdict, not a deferral.

### 4.3a Where more than one design is genuinely valid — present alternatives *(Part 1)*

Not every design question has one right answer, and forcing convergence on
one that doesn't is worse than admitting it. §0.1 already covers WHEN this
applies (a fork the interview didn't converge on, or the operator
explicitly frames as deployment-dependent) — this section is HOW to write
it into Part 1 when it happens.

For each such question: name the surviving alternatives (2–4 — if you have
more than that, the options probably aren't distinct enough yet), give each
a short label, then pros | cons | when you'd actually pick this one. End
with your own recommendation, stated as a recommendation ("recommend X for
the common case; Y if `<condition>`"), not a silent default — the reader
chooses, informed, rather than discovering later that an "example" was
quietly the only path you actually built out.

This lives IN Part 1, not Part 2 — a menu of genuinely valid designs is
normative content a reader needs to choose from, not rationale for how you
got here (that reasoning, including why the OTHER candidates were narrowed
out, still belongs in Part 2's §4.3). The topology/transport schema (§6,
scenario 3/4) is flagged as the most likely candidate for this treatment
going in — confirm during the interview whether it actually needs it, don't
assume it does just because it was flagged.

### 4.4 What still needs to be built *(Part 1)*
Concrete list: mechanism name, which tool owns it, rough shape, why it
doesn't already exist (checked against §1.3–1.5's real source, not assumed).

### 4.5 Validation — every key, every level, the entire schema *(Part 1)*

This is not optional and not abbreviable, and it is **not scoped to what
this integration changes** — it is scoped to ciu's (and, where relevant,
assay's and run-gate's) **entire config schema as your integration leaves
it**, including every key that survives unchanged from before v8. For
**every** config surface the integrated schema uses (ciu stack-level TOML,
ciu global TOML, assay lane TOML, run-gate lane TOML, and any new v8 table
you propose), produce a table with these columns for every key at every
nesting level:

| key | table / level | type | reason for existence | owner (who writes it / who reads it) | example |
|---|---|---|---|---|---|

"Reason for existence" must be a real justification, not a restatement of
the key's name — if you cannot state why a key needs to exist independently
of the fact that it's convenient, that is itself a finding (flag it as a
candidate to drop, per Work step 5). Group tables by config surface/table so
the document stays navigable; cross-reference between tables where one
level's key references another (e.g. a `realized_by` pointer's target).

### 4.6 Spec/schema check *(Part 1)*

Cross-validate the integrated proposal's schema against the real upstream
mechanisms in §1.3–1.5: for every new key/table proposed, state whether it
already exists in some form (cite the SPEC section or source location), and
whether the proposal changes its shape, meaning, or owner. Then address:
how would THIS schema itself be validated mechanically? Tie this explicitly
to ciu's existing `S5.7` schema-validated configfile render and `S9.5`
`validate_config`, and to CIU-63 (the static lint's blindness to
live-probe-resolved ref kinds), CIU-64 (`ciu check` should run automatically
before `ciu up`), and CIU-65 (`validate_config` findings need WARN/ERROR
severity via `warn_policy.py`'s existing enum) — does the integrated
proposal's own schema surface get checked by the SAME mechanism it proposes
extending, or does it need something else? Say which, and why.

### 4.7 Contradictions found and resolved *(Part 2)*
Table or list: contradiction (name both sides + their sources) | resolution
| reasoning pointer (which part of §4.3 works through it) | whether the
resolution came from source/SPEC (forced) or from the interview (§0.1).

### 4.8 What to drop *(Part 2)*
Concrete list: idea | source it came from | why it should be dropped
(superseded, wrong, not worth its complexity — be specific).

### 4.9 Open product decisions *(Part 2)*
**Narrowed by §0.1 — this is the fallback, not the default path.** With the
operator interviewed live during Work step 5, this section should be short,
possibly empty. Use it only for a fork you genuinely could not put to the
operator (they were unavailable for that specific question, or explicitly
deferred it to a later session) — never as a substitute for asking when
they were present. For each: name it precisely, give the options, do NOT
silently pick one and present it as settled. See §7. (A fork the operator
WAS asked, but didn't converge on, is not this — that's §4.3a in Part 1.)

### 4.10 Known gaps in this proposal *(Part 2)*
Explicit self-critique: what did you not have time/information to fully
resolve, what would need a live probe or a tracer-bullet to actually confirm
rather than reason about on paper, what scenario did you not think of. This
project's own convention throughout this design work has been to never
overclaim completeness — hold yourself to the same bar your output will be
reviewed against.

### 4.11 Non-breaking improvements to the existing tools *(Part 1)*

A separate list from §4.4 ("what still needs to be built" is v8/breaking).
This section names improvements or additions to ciu, assay, or run-gate that
would help NOW, independent of whether or when the full v8 cutover happens
— things a maintainer could ship incrementally on the current schema without
waiting for this integration to land. Pull genuinely non-breaking candidates
out of everything you found in §4.3/§4.4/§4.8 (a principle violation that
has a backward-compatible fix belongs here even if the FULL fix you're
proposing for v8 is breaking) and name any new ones this exercise surfaced.
For each: mechanism, tool, why it's safe to ship without breaking existing
consumers, and what it unblocks or improves. This is the section a
maintainer reads first if they want value before committing to v8 at all —
write it so it stands alone.

---

## 5. Scenarios to walk through — mandatory, plus your own additions

For each scenario: trace it against your emerging integrated schema
concretely (real key names, real example values — not "the schema would
handle this"), and note explicitly whether it works, needs a schema change
you're folding back in, or reveals an open product decision.

1. **Vault usage with different consumers.** A stack that mints its own
   secret via `GEN_TO_VAULT` (db-core, redis-core, consul-server today); a
   stack that only reads via `ASK_VAULT` (db-init reading db-core's admin
   password); a stack minted BY a post_compose hook rather than a directive
   (vault's own AppRole minting for controller/webapp-server). Does the
   integrated schema express the vault-liveness dependency automatically
   for all three shapes, or only some? (This session found the first two
   already resolved via `stack:infra/vault:healthy` — verify that finding
   still holds, don't re-assume it.)
2. **Remote deployment** — more than one host. `spec-ciu-remote-ssh-deploy.md`
   (SPEC J, §1.6 above) is the actual shipped mechanism (`ciu ssh`, `ciu up
   --host`) — walk the integrated schema through it, not through a
   from-scratch design. `V8-REALIZATION-GRAPH.md` flagged that "nothing in
   this graph or the current V8 proposal models cross-host transport
   readiness as an init dependency at all" — confirm whether that is still
   true once SPEC J's actual mechanism is accounted for, or whether it was
   an overclaim made without checking this spec.
3. **The access/transport layer, as one question, not four.** This is
   broader than "does VPN fit the schema" — the real question is: does the
   integrated schema hold **elegantly** (one coherent concept, not four
   independent ad hoc mechanisms bolted on separately) how any two services
   actually reach each other, across every distance they might be apart?
   Walk through all of these against the SAME schema concept and check
   whether it's actually one mechanism wearing different clothes or
   genuinely four:
   - same-stack, same-instance (trivial — Compose's own network);
   - different stack, same instance (today: bare Docker DNS aliasing, the
     whole CIU-48/49/51/66 hazard family this session found);
   - different instance, shared-infra join (CIU-52/S16.1's `ref_services` —
     crosses an instance boundary but stays on one host);
   - different host entirely — dstdns's disabled-by-default
     `infra/tailscale-node` stack (host-level VPN mesh) versus a
     per-stack sidecar proxy container, and SPEC J's `ciu ssh`/`ciu up
     --host` transport (§1.6) as the actual shipped remote-execution path.
   For each: does the consumer-facing config look and feel the same
   regardless of which case it turns out to be (the way a well-designed
   schema should make "how far away is this dependency" an implementation
   detail, not something every consumer re-derives), or does the distance
   leak into how a dependency is declared? If it leaks, is that a real,
   necessary distinction (some cases genuinely need different guarantees) or
   an accident of how each mechanism was built separately? Also check: does
   `requires`/`provides` need a distinct ref kind for "transport readiness"
   at all (`stack:*:healthy` doesn't obviously mean "and the network path to
   it is up"), or does the current model already cover it once identity and
   location are properly separated?
   **This is the newest open schema issue (flagged 2026-08-27, operator) —
   read proposal §1.17 before walking this scenario, but do not mistake what
   it settled for what it left open.** §1.17/§4.2/§8 got ONE narrow thing
   fixed 2026-08-27: the `transport`/`address` key naming was inconsistent
   across those three sections and is now reconciled to one shape
   (`[topology] transport = "direct"|"wireguard"|"proxy"`,
   `[topology.hosts.<host>] address = "..."`), plus a scope-boundary
   statement (CIU is declarative-only — it never provisions the tunnel/
   proxy itself, mirroring the Vault boundary in CONFIG.md) and a proposed,
   not-yet-placed `ciu check` completeness rule. **None of that answers
   this scenario's actual question.** Concretely, as of proposal rev 1.10:
   - **`transport = "direct"` is semantically overloaded and never
     disambiguated.** Its own inline comment glosses it as "same network,"
     which is used elsewhere in the doc to mean same-stack/same-instance
     (case 1, trivial). But nothing says whether "direct" is ALSO the right
     value for two real separate hosts reachable by raw IP on a shared LAN
     with no VPN and no proxy (a real deployment shape, distinct from case
     1) — that's a plausible fifth case this scenario list doesn't even
     name yet, and §1.17 doesn't say which of the two it means, or whether
     it needs its own value.
   - **Only ONE transport value has ever been worked through end-to-end.**
     §4.2's and §8's sample configs both use `transport = "wireguard"`,
     always as the same two-host worked example. `direct` has no worked
     multi-host example at all; `proxy` has no worked example at all — its
     only concrete artifact anywhere in the proposal is a `path_prefix`
     stub (`[deploy.profiles.<name>.routes.<name>]`) with no producer/
     consumer trace showing how a real reverse-proxy config would actually
     be assembled from it.
   - **The three-value enum has never been checked against this scenario's
     other three cases at all.** §1.17 was written purely about "different
     host entirely" (case 4). It says nothing about whether shared-infra
     join (case 3, CIU-52/S16.1) or same-host cross-stack DNS aliasing
     (case 2) are expressed through `[topology]` too, through some other
     mechanism, or not at all — i.e. the exact "one mechanism wearing
     different clothes, or genuinely four" question this scenario asks is
     completely untouched by the 2026-08-27 topology work. Don't read the
     key-naming fix as progress on this question; it isn't.
4. **Remote proxy with firewall rules limiting traffic to own hosts.** A
   reverse-proxy or edge component that must only accept/forward traffic
   to/from a specific set of hosts (not "anyone who can reach the network").
   Where would that constraint live in the integrated schema — is it a
   property of the LogicalService, the Realization, or something the schema
   doesn't model at all today (name it as a gap if so)? **`transport =
   "proxy"` (§1.17) is the closest existing hook and has never been worked
   through** — its only artifact is the unconnected `path_prefix` route-
   override stub in §4.2, with no host-allowlist/firewall-scope field
   anywhere near it. Treat this scenario as designing that field from
   scratch, not validating one that already exists.
5. **Realness selector bringing up a corresponding stack.** The core
   RealnessVariant mechanism: a consumer asks for "this logical service, at
   this realness level" and gets a live stack. Walk this through for BOTH a
   ciu-managed realization (`[ciu_stack.*]`) and an external one
   (`[external.*]`, e.g. a managed cloud DB) — does "bring up the
   corresponding stack" mean the same thing in both cases, or does the
   external case need a different mechanism entirely (nothing to "bring
   up")? This was sketched but never validated end-to-end against a real
   external-realization example in the source discussions — you are the
   first pass to actually do so. **Start from proposal §10.13** (realness
   immutable per environment instance, fixed at `ciu up` time) — it doesn't
   fully answer this scenario (it settles WHEN realness can change, not
   what "bring up the corresponding stack" means for `[external.*]`) but it
   constrains the answer: whatever mechanism you propose, it cannot involve
   a live swap.
6. **Shared-infra cross-instance join, interacting with realness.** A
   worktree instance (Mode B, `nyxloom-trove/GUIDE.md` §3.1) joins a
   reference instance's shared services (CIU-52/S16.1) — but the reference
   instance might expose a service at `live` realness while the joining
   instance wants `mock`. Does the schema let a joiner pick BOTH which
   instance to join AND which realness variant it wants, or does joining
   implicitly inherit whatever realness the reference happens to be running?
   **Start from proposal §10.14** (shared infra, was M7 — sharpened, not
   resolved, with a worked monorepo example already done) for the join
   mechanism's own gap, and from **§10.13** (realness immutability) for why
   "implicitly inherit" is likely wrong regardless of the join answer — a
   joiner's own `ciu up` already locked in its own realness selection before
   any join happens.
7. **Multi-stack service-name collision.** Two independently-authored
   stacks both declaring a service named `postgres` (CIU-66) — walk through
   whether the integrated schema's container-naming/identity scheme
   structurally prevents this or merely makes it less likely.
8. **Credential rotation — verify non-obstruction, don't design a
   mechanism.** Settled (operator directive): rotation is an APP-LEVEL
   concern, handled through Consul (a service watches its own KV path live
   and picks up a rotated value without a restart) — this is not ciu's
   mechanism to build. What ciu's schema DOES need to get right: does
   anything in the current or integrated config model quietly work against
   that? Concretely — check `expose_env`-style secret directives (baking a
   value into a container's environment at start time is inherently
   NOT rotation-friendly, restart-required by construction) against
   Consul-KV-backed delivery (rotation-friendly, no restart) for the same
   kind of secret, and check whether the schema makes clear to an author
   which they're choosing and why, or whether `expose_env` is silently the
   default/easy path even for a secret that will need live rotation later.
   `V8-REALIZATION-GRAPH.md`'s framing of this as "a genuinely separate,
   unaddressed axis" needs re-examination in this light — unaddressed by
   ciu is correct BY DESIGN, not a gap, provided the schema doesn't get in
   the way; confirm that's actually true rather than restating the earlier
   framing. **Proposal §10.3 (2026-08-27) added a delivery-mode axis since
   this scenario was written** — file delivery is the stated default, env
   delivery is an explicit per-secret opt-in (`delivery = "env"`) carrying
   an inline warning that plain env vars leak via `docker inspect`/`/proc`/
   crash dumps. That's real progress on "does the schema make clear which
   they're choosing and why" — but it was written about file-vs-env
   visibility, not rotation-vs-restart specifically, and it says nothing
   about Consul-KV-backed delivery as a distinct mode. Check whether it
   actually closes this scenario's question or only partially overlaps it.
9. **assay lane referencing a ciu stack that doesn't exist, or a run-gate
   lane referencing an assay lane that doesn't exist.** Walk through what
   happens today (nothing catches it until run time) versus what the
   integrated schema's cross-tool validation (§4.6) should catch, and when
   (author time / `ciu check` time / gate dispatch time).
10. **Add your own.** If, while walking the above, you find a scenario class
    the sources don't cover but a real deployment would hit, add it and walk
    it too. Name why you added it.

---

## 6. Known design tensions and prior findings — author's hints, not a substitute for reading §1

This section distills what this session already learned, so you spend your
reasoning budget extending it rather than rediscovering it. Every item below
is a **starting point to verify against the real source**, not a conclusion
to accept on faith — this project's own hard-won rule, learned from the
withdrawn CIU-45 finding, is that "X can't be done" claims must be checked
against actual source before being trusted, and this list is no exception.

- **The addressing-coupled-to-location problem is the root tension.**
  `<stack>.<service>` (proposal §3.1) ties a logical service's identity to
  wherever it happens to run today; renaming a stack or moving a database to
  a managed provider breaks every consumer's `init_requires`.
  `V8-REALIZATION-GRAPH.md`'s LogicalService/Realization split exists solely
  to fix this. Confirm it actually does, under every scenario in §5 — don't
  assume the split is sufficient just because it's well-argued. **Update
  2026-08-27:** the split has since been folded into the proposal itself
  (§1.15/§3.1–3.3/§4.1–4.2/§8, revision 1.10) — "confirm it actually does"
  now means checking a completed fold-back against §5, not reconciling two
  disagreeing documents.
- **Four more sections landed the same 2026-08-27 session, summarized in
  full in §1.1's proposal bullet above — do not re-derive any of these from
  scratch, verify and extend them instead:**
  - Secrets (was open, tagged B3) is now RESOLVED at §10.3, including a
    file/env/fixed-path/native-SDK delivery-mode axis with file as the
    stated-safer default — this bears directly on scenario 8's rotation
    question below, though §10.3 wasn't written with rotation specifically
    in mind; check the overlap holds rather than assuming it's a complete
    answer.
  - Concurrency/locking (M8) has a proposed reconciliation at §10.11 (new
    whole-run instance mutex, keyed on `INSTANCE_ID`, targeting
    `ciu.global.defaults.toml.j2` as a verified-never-runtime-written lock
    file) and §10.12 (fixes its acquisition order against S4.26, the
    worktree allocation lock, S16.3's budget lock, and §5.7's gate-admission
    logic). Still flagged open inside §10.11: mutex default-on-vs-opt-in and
    CLI/error UX. **Neither section checks this reconciliation against
    assay's or run-gate's own locking assumptions** — that cross-tool check
    is still this integration's job.
  - Realness selection is now a firm design stance, not an open question:
    **§10.13 says it is immutable per environment instance** — fixed at
    `ciu up` time for the instance's whole life (a live swap risks DB-state
    leakage across the boundary), changeable only via `ciu down`/`ciu clean`
    + `ciu up`, with `ciu up` proposed to refuse on a realness mismatch
    against the instance's already-locked-in selection (tied to §10.11's
    mutex). This directly answers scenario 5 below and partially answers
    scenario 6 (a joining instance cannot silently inherit a different
    realness than its own `ciu up` already committed to) — walk both against
    this stance and confirm it holds; a `ciu realness set <svc>=<level>`
    verb is sketched but its exact shape is explicitly left unsettled.
  - Shared infrastructure (M7) is **sharpened, not resolved**, at §10.14 —
    read it before re-deriving scenario 6's finding from scratch. It already
    contains a worked monorepo example (this very checkout: `vbpub/ciu`,
    `vbpub/cmru`, `vbpub/assay` each an independent `ciu.global` root) that
    shows S16.1's shared-infra join is welded to the `ciu worktree add` verb
    and has no path to apply when a "new instance" is a second independent
    `ciu.global` root rather than a new worktree. The two-way fork (keep
    worktree-scoped vs. generalize shared-infra join into a property of any
    environment instance) is left explicitly undecided — that's the actual
    open decision to bring forward into §4.9, not "does M7 still apply."
- **The topology/transport schema is the newest open root tension (flagged
  2026-08-27, operator) — a naming cleanup got mistaken for progress on the
  real question.** §1.17/§4.2/§8 spent 2026-08-27 reconciling THREE
  sections onto one key shape (`transport`/`address`) after they'd drifted
  (`mode`, `wireguard_ip` had crept into two of the three) — necessary
  hygiene, but it settled *spelling*, not *design*. The actual, still fully
  open question — this scenario's own framing, scenario 3 in §5 — is
  whether "how do any two services reach each other" is genuinely ONE
  schema concept across every distance (same-stack, cross-stack-same-host,
  cross-instance shared-infra join, cross-host), or four ad hoc mechanisms
  that happen to share a `[topology]` table name. Evidence it's still
  genuinely open, not just unverified: the three-value `transport` enum has
  exactly ONE worked example anywhere in the proposal (`wireguard`,
  two-host) — `direct`'s meaning is ambiguous between "trivial single-host"
  and "real multi-host, raw IP, no tunnel," and `proxy` has no worked
  example at all, just an unconnected `path_prefix` stub. **Whatever you
  propose to close this gap follows Work step 4's ERD-first,
  all-ten-scenario-walk requirement** (§3 above) — name the entities before
  the keys, and check it against scenarios 1/2/6/8 too, not only 3/4.
  Scenarios 3 and 4 in §5 below are where this gets walked properly — start
  there, this
  bullet exists only so you don't mistake the 2026-08-27 renaming for a
  design that's already been checked against the scenario space, because it
  hasn't.
- **Container naming has the same disease as config addressing, in a
  different place.** `deploy.container_name()` computes
  `{project}-{instance}-{service}` with no stack identifier at all (CIU-66);
  CIU-51's own proposed `qname()` replacement inherits the same gap. If the
  wholistic schema fixes identity at the config-addressing layer but not at
  the container-naming layer, it has only half-solved the underlying
  problem — these need ONE derivation, used everywhere (hostname,
  container_name, compose service key, `internal_host` default), not two
  independently-evolving schemes.
- **Derive, don't declare, wherever mechanically possible.** The
  vault-liveness dependency (every `GEN_TO_VAULT`/`ASK_VAULT` consumer
  implicitly needs the vault store live) was, for months, only satisfied by
  accident (hand-placed phase numbers happened to order it correctly). The
  general principle — and this echoes dstdns's own `AGENTS.md` §4.2a
  (DERIVE > READ > FAIL, never invent a silent default) — is that a fact
  which can be mechanically derived from something already declared (a
  directive's presence, a ref kind's own semantics) should never require a
  human to also hand-declare it as a separate `requires` entry. Apply this
  test to every dependency edge you propose keeping manual.
- **The `stack:*:healthy|completed` ref kind resolves via live probe, not
  via `provides`, but the STATIC lint doesn't know that** (CIU-63) — forcing
  a redundant self-declaration undocumented anywhere. Any wholistic schema
  that keeps ref-kind-based dependency expression needs its static
  validator to be ref-kind-AWARE from the start, not a blanket string
  matcher retrofitted per kind as gaps are found.
- **The graph-completeness check is a PREREQUISITE for dropping
  `[deploy.phases]`, not a parallel nice-to-have.** A topological sort
  computed from `requires`/`provides` is only as trustworthy as the graph is
  complete — every real gap found this session (vault-liveness,
  schema-completion, CIU-63 itself) was a case of the graph silently NOT
  containing an edge that mattered, silently masked for weeks by the
  hand-maintained phase list happening to get it right anyway. §4.3's
  ambition to drop phases entirely is not safe to ship until the
  completeness check (§4.6 here) exists and is itself proven, not assumed.
- **`ciu check` should run automatically before `ciu up`** (CIU-64), and its
  findings need WARN/ERROR severity typed against `warn_policy.py`'s
  existing `exit_on` enum rather than an untyped `list[str]` (CIU-65) — the
  shipped `validate_config()` (S9.5) is per-hook, opt-in, and — confirmed by
  reading the one reference implementation, `post_compose_db.py` — checks
  only a single stack's own declared config, never cross-stack. The
  graph-completeness check this integration needs is a CORE, automatic,
  cross-stack invariant, not something achievable by asking every hook
  author to opt in. **CIU-67/CIU-68 (filed 2026-08-26, newer than this
  bullet) are the same "relies on someone remembering" shape one layer
  down**: the S7.7 health gate that makes `stack:*:healthy` reliable across
  a phase boundary isn't on by default under a bare `ciu up` (needs both
  `--deploy` and `--healthcheck`, undiscoverable from `--help`) and its
  live-probe has zero retry — a live fresh-deploy reproduction (`dstdns`
  D-212) hit exactly this. If the integrated schema's automatic preflight
  (this bullet) ships, check whether it also self-selects the health gate on
  whenever a run declares any `stack:*` requirement — CIU-68's own proposed
  fix — rather than leaving that as a second, independently-forgettable flag
  pair.
- **A "ciu/assay can't do X" claim needs a grep before it's trusted.** This
  happened TWICE this session with the identical shape: CIU-45 (withdrawn,
  2026-08-21 — a working provider-registration mechanism already existed,
  nobody had used it) and this session's own near-repeat with
  vault-liveness/schema-completion (caught only because the backlog-filing
  workflow's own search-before-file step surfaced CIU-45 first). Treat every
  "gap" you think you've found the same way: grep the actual mechanism named
  in the tool's own source/error text before writing it down as a finding.
- **Three tools, three config files, one accidental duplicated fact.**
  `run-gate.toml`'s `pins.assay.version` and its `assay_command`'s `.pyz`
  filename encode the SAME fact (which assay release a lane expects) in two
  string shapes that share no substring — a sed fixing one silently left the
  other stale (D-211, caught only by a follow-up grep, not by any check).
  Consider, as part of §4.5/§4.6, whether the wholistic schema should have a
  single source for any fact two tools both need to agree on, rather than
  letting each tool's config restate it independently.
- **ERD discipline, applied deliberately, is what produced the current best
  answer to the addressing problem** — `V8-REALIZATION-GRAPH.md` was reached
  by explicitly separating entity (LogicalService) from relationship
  (RealnessVariant, `realized_by`) from the concrete row that currently
  satisfies it (Realization), rather than trying to flatten everything into
  one TOML table shape first and reasoning about it informally. Bring the
  same discipline to any NEW entity you find yourself needing — **for
  topology/transport specifically this is not a judgment call, it's a
  requirement (operator directive, 2026-08-29 — see Work step 4 and §4.3):
  name the entities and relationships before any TOML key**, the same way
  `V8-REALIZATION-GRAPH.md`'s own entity table precedes its worked TOML
  example, and then walk whatever you propose against every scenario in
  §5, not only the ones that motivated it.

---

## 7. Open-decision rule — superseded by §0.1, kept for the fallback case

**This section predates interview mode (§0.1) and no longer describes the
primary path.** When this prompt was first written, the task ran
unattended: a genuine fork got written into §4.9 for a human to review
later, because no human was available to ask in the moment. That is no
longer true — the operator is present for this entire task (§0.1) — so the
primary rule now is simply: **ask, live, when you hit a fork.** Don't defer
to a document section what you can resolve in the next two minutes of
conversation.

What survives from this section: if you reach a point where a scenario or
contradiction genuinely cannot be put to the operator at all — they were
unavailable for that specific question, the session ended before you got to
it, or they explicitly said "decide this one later" — do not silently pick
one and present it as settled. Write it into §4.9 (Open product decisions)
with the concrete options and what's at stake in choosing each. This is
still not a failure mode: naming a real open decision correctly is a more
valuable output than a confident-looking answer to a question nobody was
actually able to settle. It is now the **exception path**, not the default
one — see §0.1 and Work step 5.

Two things this is NOT: (1) a fork the operator WAS asked but the two of you
didn't converge on — that's §4.3a (present alternatives in Part 1), not
§4.9; (2) something you could resolve by reading further. If the answer is
in the source named in §1 and you didn't find it, that's a
reading-thoroughness problem, not a genuine open decision — go back and
read more carefully before asking about it, let alone before writing it into
§4.9.

---

## 8. Scope

**Touch:** rewrite `ciu/docs/CIU-V8-TESTING-GATE-PROPOSAL.md` **in place** as
revision 2.0 (§4). This is the one file this task is allowed — required —
to edit; every prior revision (1.5 through 1.10) was reached by editing this
same file in place, and 2.0 continues that, not a new filename. Per Work
step 9, you MAY also create new backlog entries in the relevant tool's
`KNOWN_ISSUES_TODO_BACKLOG.md` (ciu, assay, or run-gate-project) — but only
for genuine tool defects (wrong behavior against the tool's own documented
contract), never for design gaps or "wrong shape" findings, which belong in
the proposal itself instead. Nothing else.

**Forbid:** do not edit `V8-REALIZATION-GRAPH.md` — it is a pure source, and
its own edit history (the three-round corrections Part 2/§4.3 is modeled on)
is part of the record this integration is reasoning over; it is not itself
being superseded by revision 2.0 the way `CIU-V8-TESTING-GATE-PROPOSAL.md`'s
own 1.5–1.10 are. Do not write or modify any code, config, or test in
`ciu/`, `assay/`, `run-gate-project/`, or `dstdns/` — this is a
design-synthesis task; implementation is explicitly out of scope and
belongs to whatever handoffs get carved from your output afterward. Do not
edit or re-file an EXISTING backlog entry beyond what the search-before-file
step of a genuinely new finding requires (a `note` on a match you found, not
a rewrite) — you are filing new defects you found, not auditing the backlog
itself. Do not proceed past a genuine fork (§0.1) without asking the
operator live — a confident-sounding answer to a question they were never
actually asked is a worse outcome here than stopping to ask.
