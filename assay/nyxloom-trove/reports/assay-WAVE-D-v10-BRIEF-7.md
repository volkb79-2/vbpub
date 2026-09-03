# assay Wave D (v10) — BRIEF-7 (generation 7 → generation 8)

Written at generation 7's E-008 checkpoint. **Cumulative delta since BRIEF-6
only.** Read BRIEF-1 (the seam map), BRIEF-4 **§5 (the phase-2 seam table —
still your map, do not re-derive it)**, BRIEF-6 (§4's two easily-missed design
points, §5's gate recipe, §8's rules), then this.

**The headline: ALL FIVE phase-2 wire changes now exist as A-rows — the
precondition the wave prompt puts on writing the cut is MET. B007's bound is
MEASURED. Nothing else changed. THE `feat(assay)!:` CUT IS YOUR WHOLE JOB and
it has not been started.**

---

## 1. Where the branch stands

- Worktree `/workspaces/vbpub/.worktrees/assay-wave-d-v10`, branch
  `feature/assay-wave-d-v10`.
- **Tip:** this brief's own commit. **GATE-VERIFIED COMMIT: still
  `bfb55e3f`** — generation 7 landed NO code, so no gate was run (a
  registered-gate run on a records-only commit buys nothing and costs the
  shared host 25 minutes; generation 6 set the same precedent). Proof the tip
  is code-identical to the gate-verified commit:
  `git diff --stat bfb55e3f HEAD` → four files, all under
  `assay/nyxloom-trove/` (`decisions.md`, BRIEF-6, LOG, REPORT), 739 insertions,
  zero deletions. **Your first gate run judges your cut, not this.**
- **Zero `!` commits.** Nothing under `src/assay/verdict.py`,
  `src/assay/verify.py`, `src/assay/schemas/` or `nyxloom-trove/carve-assets/`
  has been touched on this branch. The branch is still releasable on v9.

| commit | what |
|---|---|
| `bfb55e3f` | **the gate-verified tip** (generation 6) |
| `ed287d73` | generation 6's records + BRIEF-6 |
| `26b38cc4` | **A-430..A-433** — the last three design rows + the carve's W0 corrections |
| this one | records: LOG entry 25, REPORT, BRIEF-7 |

## 2. What generation 7 landed

1. **B007's bound is MEASURED (DA-R17 discharged).** Full table in the REPORT
   ("B007's target-bound measurement"). The number you need:
   **one canary TARGET = ~2.76 s of materialisation** (control enter→exit
   ~1.26 s + transform enter→exit ~1.50 s) **plus two full runs of the lane's
   command**, peak disk ONE snapshot (~96 MB) because
   `run_isolated_canary`'s two contexts are sequential, not nested
   (`canary.py:479-544`, `:551`). `prepare_snapshot` is 4.071 s ONCE per lane.
   **`MAX_CANARY_TARGETS = 8`** follows: 8 × 2.76 = 22.1 s = 7.4 % of the
   smallest budget any worked example declares (`5m`,
   `docs/DESIGN-GUIDE.md:2127`). Script kept at
   `scratchpad/measure_b007.py` — it drives the SHIPPED substrate; do not
   re-measure unless you change the substrate.
2. **A-430** B004 (DA-D7 as narrowed by DA-R12), **A-431** the carve W0
   corrections, **A-432** B007 (DA-D8 + DA-R17), **A-433** F015/R4
   (DA-D9 + DA-R16). Read the ROWS, not this list.
3. **Nothing else.** No code, no lane change, no backlog entry.

## 3. GENERATION 8's WORK — THE CUT, then phase 2's implementations

**Every wire change below is already designed. Do not re-derive one; if a row
does not answer a question, that is a decision ask, not an invitation.**

### 3.1 The single `feat(assay)!:` commit — its exact contents

One commit. It must be gate-green before you write another. Its parts:

**(a) `src/assay/verdict.py`**
- `VERDICT_SCHEMA_VERSION = 10` (`:245`); the dataclass field at `:3110` and
  its `__post_init__` refusal at `:3126` follow.
- `JudgmentR2` gains `fail_under` — required under `producer == "ingested"`,
  FORBIDDEN under `"native"`, range 0..100 (**A-427**).
- `Claim` gains `detail` / `detail_dropped_bytes` — non-PASS only, sibling
  present iff `detail` is, head-kept truncation at **2048 BYTES** (**A-428**).
- `Evidence.__post_init__` (`:2773-2781`) gains
  `adjudicated ⇒ verified_by_assay is False` (**A-430**).
- `JudgmentR3` becomes `{mechanism, targets, aggregation | None}`;
  `CanaryResult` becomes `{mechanism, attempts}` with a per-attempt record
  carrying today's canary body plus `disposition` and
  `not_attempted_reason` (**A-432**). The existing cross-check
  `claim[R3].canary.target == judgment.r3.target` becomes the **pairwise,
  in-order** equality of `attempts[i].target` with `targets[i]`.
- New `JudgmentR4` `{tests, broken_commit, broken_commit_source}` and the
  `red_first` claim payload `{broken_commit, tests, before_outcome,
  after_outcome?}`; `RIGOR_LEVELS`-side enum gains `"R4"` (**A-433**).

**(b) `src/assay/errors.py`** — `PROVENANCE_UNVERIFIED` (**A-430**) and
`RED_FIRST_UNPROVEN` (**A-433**, reserved now / rendered in phase 3) as
`ReasonCode` members and in `REASON_CODES[Outcome.NO_MEASUREMENT]`
(`:203-215`). **See §7 decision ask 1 before you place `RED_FIRST_UNPROVEN`.**

**(c) `src/assay/schemas/verdict.schema.json`** (the ONLY schema file)
- `$defs/reason_code`'s flat enum at **`:593`** and
  `$defs/reason_codes/NO_MEASUREMENT` at **`:650`** gain both new codes.
- `$defs/rigor` (just after `$defs/outcome`, `:531`) gains `"R4"`.
- `$defs/judgment_r2.allOf` gains the `fail_under` producer fork (A-427).
- `$defs/judgment_r3` `{mechanism, target}` → `{mechanism, targets,
  aggregation?}`; `$defs/canary` `{mechanism, target, description,
  control_outcome, …}` → `{mechanism, attempts[]}` with today's body (and its
  three `allOf` pairing rules) preserved verbatim INSIDE the attempt, and
  `control_outcome` moved into the `attempted` branch (A-432).
- New `$defs/judgment_r4` + the `red_first` claim payload (A-433).
- The claim gains `detail` (`maxLength: 2048`) + `detail_dropped_bytes`, with
  the `status == "PASS" ⇒ neither` rule (A-428).
- The evidence `else` branch at **`:2463`** (today it only forbids the
  attestation payload) gains `"verified_by_assay": {"const": false}` (A-430).

**(d) `src/assay/verify.py` — THE THIRD PLACE, and the 2.4.0 lesson.** It
imports the version at `:95` and refuses a foreign one at `:2115-2128`; v9 must
now be refused exactly as v9 refused v8 (A-138/A-170, a hard cut with one
version-only diagnostic). Every new field is re-derived WITHOUT importing the
model (A-182): B050's floor read FROM the document in
`_check_r2_rederivation` (`:1966`, called `:2171`); B051's `discarded`
re-derived beside the other three in
`_check_ingested_r2_agrees_with_its_payload` (`:826`, called `:2158`);
B007's per-attempt status, the aggregation AND the short-circuit bookkeeping
hand-transcribed; F015's status re-derived from the two outcomes; `detail`'s
presence rule and its exact BYTE bound (the schema's `maxLength` counts
characters — A-428 splits them deliberately).

**(e) The drift guard — `nyxloom-trove/carve-assets/W6/`**, replicating W5's
four-part shape exactly: `verdict.schema.v10.json` (a frozen copy),
`test_acceptance_v10.py`, `expected/` (W5 has **seven** templates:
`ca1-r3-no-base`, `ca4-all-equivalent`, `ingested-r2`, `missing-tool`,
`p25-missing`, `p25-pass`, `sql-r2` — each `*-v9-template.json` gets a
`*-v10-template.json` successor) and `MANIFEST.md` (its table gains a `W6`
row; read W5's MANIFEST for the "every earlier generation stays frozen and
unedited" rule — **W5 is KEPT, never rewritten**). Per DA-R16 the templates
include **one R4 verdict**, and per A-432 at least one multi-target R3
verdict with a `not_attempted` attempt.

**(f) The gate phase.** `tools/tester-unified-gate.sh` runs W5's suite at
**`:578-581`** ending `ASSAY_GATE_PHASE=verdict-v9-successors-verified`; the
hard-cut guard is `:567`'s `verdict-v6-v7-v8-hard-cut-verified`. W6 gets the
same treatment one generation on: a `verdict-v10-successors-verified` phase,
and v9 joins the hard-cut guard. **Gate any edit to that script, however
small** — generation 6's three-token edit went red; read
`tests/test_distribution_gate.py` first (it reads the script's text AND the
stubs' received argv).

**(g) Invariants the cut must NOT break** — restated because they are the
things a schema cut silently violates: `LANE_SCHEMA_VERSION` stays **2**;
`inventory_schema` stays **1**; no rename of `assay.diff`, `assay.git`,
`assay.mutation`, `assay.adapters.python` (cmru imports those four by name
from a pinned zipapp); dstdns reads `outcome`, `status`, `coverage.pct`,
`coverage.missing_lines`, `coverage.missing_branch_lines` **by name**.

### 3.2 Blast radius — measured, so you can budget

`grep -rln` over `tests/`: **4** modules construct `CanaryResult(`, **14**
pass `canary=`, **56** name `VERDICT_SCHEMA_VERSION` or a `schema_version` 9.
The suite is ~4000 tests. The `$defs/canary` → `attempts[]` restructure is the
expensive half; the rest are additive. **Budget the cut as most of a
generation on its own**, and remember the clause: never cut a checkpoint
mid-schema.

### 3.3 After the cut, in this order (unchanged)

**B050** (A-427) → **B051** (DA-D4) → **B052** (DA-D5) → **B053 `detail`**
(A-428) → **B004** (A-430; W2–W7 of the carve, against the already-re-captured
ciu 7.10.1 assets) → **B007** (A-432) → the CONSUMERS **"Migration notes
(v9 → v10)"** section, which goes beside `## Adopting a v2-capable release`
(`docs/CONSUMERS.md:1621`) and must name: the one-element `targets` list as an
R3 lane's migration, `judgment.r2.fail_under` for ingested lanes, `assay
verify` refusing v9, the new refusals (B052's content check, B054's per-file
rule), `detail` on refusals, the adjudicated provenance lane shape, and that
Python/Go lanes without R3 are unchanged.

**F015's IMPLEMENTATION is phase 3** — designed only (A-433). Do not build it.

## 4. Ids

Re-checked immediately before allocating (main moves every generation):

```
$ git -C <worktree> show main:assay/nyxloom-trove/decisions.md | grep -o '^| A-[0-9]*' | tail -1
| A-407
$ git -C <worktree> show main:assay/nyxloom-trove/4-backlog.md  | grep -o '^## B[0-9]*'  | tail -1
## B061
$ git -C <worktree> rev-parse --short main
6917423d
```

`main` moved `af98e1f0` → `6917423d` during generation 7; assay's two ledgers
are still untouched on it. Generation 7 allocated **A-430..A-433** and no
backlog id. **Next free: A-434, B065.** Re-run both commands before allocating.

## 5. Gate state and the throttle

**GATE-VERIFIED COMMIT: `bfb55e3f`** (`scratchpad/gate-gen6c.log`:
`COMPLETE_MARKERS=1`, `GATE_EXIT=0`, `BAD=0`, wheel
`assay-4.1.1.dev25+gbfb55e3f-py3-none-any.whl`, twelve phases ending
`pyflakes-clean`). BRIEF-6 §5's launch recipe is unchanged and has worked four
times; its two additions still hold — **a foreign `run-gate-vbpub-*` container
counts, wait for it and never touch it**, and a blocking
`timeout 590 bash -c 'until grep -q "GATE_EXIT=" <log>; do sleep 20; done'`
is much cheaper than polling. Read the verdict in a SEPARATE step regardless.

Throttle, binding: never `pytest -n`/xdist (serial, `nice -n 19 ionice -c 3`,
targeted files, whole suite at most once per checkpoint); never two gate
containers; `docker update --cpus=3` on yours within seconds of launch; no
build/pip/wheel step concurrent with a suite run. Generation 7 ran no gate and
one measurement; host load was 3.87 at its start.

## 6. Rules (BRIEF-6 §8, unchanged — the three that bit someone)

- File edits through the **Edit tool**, never `sed`/python rewrite scripts.
- **Never a bare `git stash`**; red-prove in a detached scratch worktree.
- **Run every git command from the worktree** (`git -C <worktree> …`). The only
  thing that belongs in `/workspaces/vbpub` is the gate launch. Logs and
  scratch scripts live in your scratchpad, **never** in the worktree.
- `git commit -F <msgfile> --only -- <paths>`, BOTH trailers, new files
  `git add`ed first. **Exactly ONE `!` commit on the branch.**
- `decisions.md` is APPEND-ONLY (A-408): a later row corrects an earlier one.
- A-334: no test double as evidence about an EXTERNAL system.
- Commit BEFORE you gate; worktree untouched for the whole run.

## 7. Decision asks the controller has NOT ruled

1. **`RED_FIRST_UNPROVEN`'s outcome class — `NO_MEASUREMENT` (as A-433 is
   written, following DA-D9's literal words) or `FAIL`?** The case that
   matters, *the declared test PASSED at the broken commit*, is not a
   measurement failure: assay materialised both commits, ran both tests, and
   learned the test does not discriminate the fix — which is `CANARY_SURVIVED`
   one tier up. **It must be ruled INSIDE this cut**: the code's set
   membership is a schema fact, free to change before the merge and a v11
   afterwards. A-433 is written to DA-D9 as ruled; either ruling is a
   two-line change. Full argument and a suggested split in the REPORT.
2. **`all` does not short-circuit on a FAIL** (A-432, ruled by the implementer
   on measured cost + diagnostic value). Flagged because R-2's prompt names
   "the 2N bound" and this is the choice that makes the worst case exactly 2N.
3. **One shared control materialisation across targets was REJECTED**
   (A-432), price ~1.26 s per extra target (≤8.8 s on a full lane), bought
   per-attempt independence for the payload, the bookkeeping and B064's resume
   keying. Recorded so the controller sees the price.

## 8. Retention prompt for generation 8 (self-authored)

> **KEEP:** the branch/worktree identity; that the **gate-verified commit is
> `bfb55e3f`** and the tip differs from it only by four `nyxloom-trove`
> markdown files; that **phase 1 and both R-1 rounds are CLOSED** and **ALL
> FIVE phase-2 wire changes now exist as A-rows (A-427 `fail_under`, A-428
> `detail`, A-430 B004, A-432 B007, A-433 F015/R4)**, so the cut's
> precondition is met and **the single `feat(assay)!:` cut is the whole
> remaining job**; **§3.1's part list (a)–(g) verbatim**, including the
> measured line numbers (`verdict.schema.json:593`, `:650`, `:531`, `:2463`;
> `verify.py:95`, `:826`, `:1966`, `:2115-2128`; `verdict.py:245`, `:2773`,
> `:3110`; the gate script's `:567` and `:578-581`) and W5's **seven** expected
> templates that need v10 successors with W5 KEPT; **§3.2's blast radius**
> (4 modules build `CanaryResult`, 14 pass `canary=`, 56 name the version);
> **§3.3's post-cut order** and where the migration notes go
> (`docs/CONSUMERS.md:1621`'s precedent); the invariants in §3.1(g)
> (`LANE_SCHEMA_VERSION` 2, `inventory_schema` 1, four un-renamable module
> paths, five read-by-name verdict fields); **B007's measured 2.76 s / 96 MB
> and `MAX_CANARY_TARGETS = 8`** (do not re-measure); §4's ids (next free
> **A-434**, **B065**) and that `main` moves every generation; §5's gate
> recipe and throttle; §6's rules; and **§7's three open decision asks, the
> first of which must be ruled before the cut is merged.**
>
> **DROP:** everything behind phase 1 and both R-1 rounds; generation 6's
> three fixes and the gate-stub argv history; the ciu re-capture's per-document
> detail (BRIEF-6 §4 records the one delta, the integer 1 → 2); the
> measurement script's setup troubleshooting; the docs-wording debates of
> generations 2–4.
>
> **DO NOT** split the cut across two commits or two schema bumps; do not
> rewrite W5; do not rename the four cmru-imported module paths or the five
> dstdns-read verdict fields; do not raise `LANE_SCHEMA_VERSION` or
> `inventory_schema`; do not implement F015 (phase 3); do not decide ask 1 on
> silence; do not checkpoint mid-cut with a half-moved schema; do not run two
> gate containers or an xdist pytest; do not build B020, B023, B001's
> residual, B010's orchestration half, B048's judge verb, Go R2/R3, an
> `assay canary qualify` document kind, or any part of B064.
