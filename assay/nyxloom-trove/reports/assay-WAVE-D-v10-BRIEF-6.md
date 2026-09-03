# assay Wave D (v10) — BRIEF-6 (generation 6 → generation 7)

Written at generation 6's E-008 checkpoint, on a green gate. **Cumulative
delta since BRIEF-5 only.** Read BRIEF-1 (the seam map), BRIEF-2, BRIEF-3,
**BRIEF-4 (§5, the phase-2 seam table, is still your map — do not re-derive
it)**, BRIEF-5, then this.

**The headline: R-1's review of phase 1 is CLOSED (round 2 ACCEPT-conditional,
its one condition landed as A-426), the estate's `--resume --progress`
directive is landed, and TWO of the five phase-2 design A-rows are written.
The v10 CUT has NOT been started, and it is your job. Three design rows must
exist first, and one of them is blocked on a decision ask.**

---

## 1. Where the branch stands

- Worktree `/workspaces/vbpub/.worktrees/assay-wave-d-v10`, branch
  `feature/assay-wave-d-v10`.
- **Tip:** this brief's own commit. **GATE-VERIFIED COMMIT: `bfb55e3f`.**
- Phase 1: 10/10 done. R-1 rounds 1 and 2: closed, fixes landed.
- **Phase 2: DESIGN 2 of 5. The cut: NOT STARTED.** Nothing under
  `verdict.py`, `verify.py`, `src/assay/schemas/` or the drift-guard
  carve-assets has been modified on this branch, **no commit carries `!`**,
  and both v9 schema gate phases passed again on `bfb55e3f`. The branch is
  still releasable on v9.

| commit | what |
|---|---|
| `ba2f1133` | **A-425** — DA-R13: the `LANE_TIMEOUT` commit-label read is bounded by `LABEL_GRACE_SECONDS = 2.0` |
| `b69a9248` | **A-426** — DA-R15/SF-6: the last silent terminal in `runner.py` announces; R-1's round-2 report committed verbatim |
| `f254b702` | **A-429** gate gets `--resume --progress`; **A-427/A-428** design rows; **B064** filed |
| `bfb55e3f` | the gate's `assay` stubs read `--verdict-json` from argv — **THE GATE-VERIFIED TIP** |
| this one | records: LOG, REPORT, BRIEF-6 |

## 2. What generation 6 landed (details: LOG entries 19–23, REPORT "Generation 6")

Three things you must not re-derive:

1. **A-425 closed BRIEF-5's open decision ask.** The unbounded commit-label
   read is gone; the grace is `cli.LABEL_GRACE_SECONDS = 2.0`, passed through
   the existing `remaining=` shape via a directly-constructed
   `runner.LaneDeadline` (its `start` rejects a non-positive budget, which the
   `0.0` test needs). Grace expiry → no verdict, one line naming the label and
   the grace, exit code unchanged. **Do not reopen it.**
2. **A-426 was R-1 round 2's single ACCEPT-condition, and phase-1 review is
   now closed.** The `except RuntimeError` announces on the
   `if outcome_holder:` side ONLY; the `raise` side is deliberate and has its
   own control test. Measured: that site is NOT reachable from `assay run`
   (only `prepare_snapshot`'s leak guard raises it), so A-414's "no
   exceptions" claim was and remains true.
3. **A-429 is estate policy, not a local choice**, and it bit: adding
   `--resume --progress` to the gate script turned the registered gate RED
   (4 failed / 3997 passed) because three `assay` stubs wrote to `"$5"`.
   Fixed at the cause in `bfb55e3f` — the stubs now read the token after
   `--verdict-json`. **If you add a flag to that invocation, the stubs no
   longer care; but re-run the gate anyway, because the argv-log test pins the
   exact list.**

## 3. GENERATION 7's WORK — phase 2, in order

1. **Finish the design rows. `A-430` is next free** (A-425..A-429 are taken).
   Still to write, and the cut cannot be written before all three exist:
   - **B004** — `PROVENANCE_UNVERIFIED` in the `NO_MEASUREMENT` set + the W2
     §5.4 narrowing, under **DA-D7 as narrowed by DA-R12**. Everything you
     need is already measured: the reason code's exact touch points are in the
     carve §5.2 (`errors.py` enum + `REASON_CODES[NO_MEASUREMENT]`;
     `verdict.schema.json` `$defs/reason_code` AND
     `$defs/reason_codes/NO_MEASUREMENT`), and §5.4 is the `Evidence`
     narrowing (`__post_init__` adjudicated ⇒ `verified_by_assay is False`
     **and**, since the bump is being paid, the JSON schema `else` branch
     `{"verified_by_assay": {"const": false}}`). DA-R12: `schema_version`
     accepted as the integer set `{1, 2}` through ONE parser; the integer 3,
     the string `"2"` and absence refused with a message naming accepted AND
     observed; tests per frozen asset plus the three refusals; **the green
     path's only witness stays `ciu-provenance-green-reference.json`** —
     say so in the row. Also correct A-O12's false `declared_unverified`
     claim (carve W0).
   - **B007** — `judge.canary.targets` (ordered, bounded — **MEASURE one
     materialisation before choosing the bound and record the number**;
     BRIEF-4 §5 has the declaration seam `config.py:530` and the existing
     single-target B005 rule at `config.py:1240-1257` that DA-D8 wants
     generalised), `aggregation = "any" | "all"` (closed), the per-attempt
     payload array with a closed "why not attempted" vocabulary,
     `judgment.r3` gaining `targets`/`aggregation`, short-circuit bookkeeping
     under `any`, `verify.py` recomputing the aggregation **hand-transcribed**,
     budget exhaustion staying its own terminal, and the B005 rule specified.
     Today's wire shape, so you do not have to look it up:
     `$defs/judgment_r3` is exactly `{mechanism, target}`, both required,
     `additionalProperties: false`; `$defs/canary` is
     `{mechanism, target, description, control_outcome}` required plus
     `transformed_outcome`/`expected_reason_code`/`observed_reason_code` with
     three `allOf` pairing rules; `verdict.py` cross-checks
     `claim[R3].canary.target` against `judgment.r3.target`, and that
     cross-check is what generalises.
     **One sentence is owed here by controller instruction:** B064 (progress
     and resume beyond R2) records that B007's per-attempt payload is exactly
     what a per-attempt progress EVENT would carry and what a per-target
     resume record would be keyed by — B007's A-row must say so. No scope
     change.
   - **F015 — BLOCKED on decision ask 1 (below). Do not improvise it.**
2. **THE SINGLE `feat(assay)!:` CUT**, one commit, carrying:
   `VERDICT_SCHEMA_VERSION = 10` (hard cut, A-138/A-170 — `assay verify`
   refuses v9 exactly as v9 refused v8), every new field in
   `src/assay/schemas/verdict.schema.json`, the dataclasses, **and `verify.py`
   (the third place — the 2.4.0 lesson)**, plus the new drift guard
   `nyxloom-trove/carve-assets/W6/verdict.schema.v10.json` +
   `test_acceptance_v10.py` + `expected/` with **W5 kept as history**, and the
   gate phase wired the way `verdict-v9-successors-verified` is
   (`tools/tester-unified-gate.sh:557-569`, now shifted by A-429's comment —
   grep, do not trust the line number).
   `LANE_SCHEMA_VERSION` stays **2**; `inventory_schema` stays **1**; no
   renames of `assay.diff` / `assay.git` / `assay.mutation` /
   `assay.adapters.python` (cmru imports those four by name); dstdns reads
   `outcome`, `status`, `coverage.pct`, `coverage.missing_lines`,
   `coverage.missing_branch_lines` **by name** — those stay.
3. Then, in order: **B050** (A-427 is its design) → **B051** (DA-D4) →
   **B052** (DA-D5) → **B053 `detail`** (A-428 is its design) → **B004** →
   **B007** → the CONSUMERS "Migration notes (v9 → v10)" section.
4. Gate after each coherent step, within §5's throttle.

## 4. The design already written — read the rows, not this summary

**A-427 (B050/DA-D6)** and **A-428 (B053/DA-D2 c)** in `decisions.md` are
implementable as written. The two things most likely to be missed if you only
skim them:

- A-427: the floor is **required under `producer = "ingested"` and FORBIDDEN
  under `"native"`** (native R2 has no floor at all), as a further branch on
  the B046 producer fork already in `$defs/judgment_r2.allOf`. The load-time
  refusal at **`config.py:2483-2512` is deleted; the range check at `:2478`
  stays.**
- A-428: the bound is **2048 BYTES**, and JSON Schema's `maxLength` counts
  CHARACTERS — so the schema carries `maxLength: 2048` (implied by, and never
  stricter than, the byte bound) and **`verify.py` carries the exact byte
  check**, which is where DA-D2 (c) puts it anyway. Truncation keeps the
  **head**, opposite to B014's command tails, with the reason stated; the
  sibling `detail_dropped_bytes` is present iff `detail` is.

## 5. Gate state and the throttle (BRIEF-5 §6, still binding, plus two facts)

**GATE-VERIFIED COMMIT: `bfb55e3f`.** `scratchpad/gate-gen6c.log`:
`COMPLETE_MARKERS=1`, `GATE_EXIT=0`, `BAD=0`, wheel
`assay-4.1.1.dev25+gbfb55e3f-py3-none-any.whl` (size 532337, sha256
`262e9c4b0ecaae5cc822d45c10cf982d781bad07a4e892554fbde58304ebe7a0`),
`tester-unified: PASS (exit 0)` at
`commit: bfb55e3f3b267050fff47d670f48e35a08a19d87`, twelve phases ending
`pyflakes-clean`.

The launch recipe in BRIEF-5 §6 is unchanged and worked three times. Two
additions generation 6 paid for or confirmed:

1. **A foreign gate container can be running.** Generation 6's third launch
   waited on `run-gate-vbpub-assay-*` (someone else's run-gate driving assay's
   gate). Check `docker ps --format '{{.Image}} {{.Names}}'` and **wait**; do
   not touch a container that is not yours.
2. **A blocking foreground wait DOES work and is much cheaper than polling:**
   `timeout 590 bash -c 'until grep -q "GATE_EXIT=" <log>; do sleep 20; done'`
   in one Bash call, repeated until it returns 0. (`Monitor` still works; this
   is simply fewer calls.) Read the verdict in a SEPARATE step regardless.

Throttle, unchanged and binding: never `pytest -n`/xdist (serial,
`nice -n 19 ionice -c 3`, targeted files, whole suite at most once per
checkpoint); never two gate containers; cap yours to 3 CPUs within seconds of
launch; no build/pip/wheel step concurrent with a suite run. Host load stayed
5.1–7.4 across generation 6's three gate runs.

## 6. Next free ids (re-checked against `main`, which MOVED again)

```
$ git -C <worktree> show main:assay/nyxloom-trove/decisions.md | grep -o '^| A-[0-9]*' | tail -1
| A-407
$ git -C <worktree> show main:assay/nyxloom-trove/4-backlog.md  | grep -o '^## B[0-9]*'  | tail -1
## B061
$ git -C <worktree> rev-parse --short main
af98e1f0
```

`main` moved `c35baa9e` → `b36c6925` → `af98e1f0` during generation 6 alone;
assay's two ledgers are still untouched on it. Generation 6 allocated
**A-425..A-429** and **B064**. **Next free: A-430, B065.** Re-run both
commands before allocating.

## 7. Decision asks the controller has NOT ruled

1. **F015's claim kind (DA-D9) — this blocks F015's design row and therefore
   the cut.** A claim is keyed by `rigor`; the enum is exactly `R0..R3`; the
   ladder is **ordered in shipped code** — `_replace_highest_higher_rigor_claim_with_git_failed`
   picks "the highest declared higher-rigor claim", and `RIGOR_LEVELS`
   (`config.py:145`) canonicalises a lane's declaration against that order. So
   adding `R4` does not merely add a claim kind: it makes fail-before/pass-after
   the HIGHEST tier and therefore the claim replaced on a cleanup failure,
   ahead of R3. That may be correct — F015 asserts a strictly more specific
   property than R3's canary — but it is a ruling. Alternatives: a non-ordered
   claim kind (the `claims`-keyed-by-rigor model does not currently admit
   one), or riding R3 with a second `judgment` block (two mechanisms under one
   claim). **Do not decide this on silence.**
2. **B007's target bound must be MEASURED** (DA-D8) and generation 6 did not
   measure it. It wants a quiet host window with no gate running; schedule it
   as generation 7's first phase-2 act rather than squeezing it beside a gate.

## 8. Rules (BRIEF-5 §8, unchanged, plus what generation 6 confirmed)

- File edits through the Edit tool, never `sed`/python rewrite scripts.
- **Never a bare `git stash`.** Red-prove in a **detached scratch worktree**
  (`git worktree add --detach <scratch> HEAD`, copy the changed files in,
  mutate the copy with Edit, run, `git worktree remove --force`). Generation 6
  used it twice; both proofs are in the LOG with their numbers.
- **Run every git command from the worktree** (`git -C <worktree> …`), never
  after `cd /workspaces/vbpub`. The only thing that belongs in
  `/workspaces/vbpub` is the gate launch.
- `git commit -F <msgfile> --only -- <paths>` with BOTH trailers; new files
  `git add`ed first.
- **Exactly ONE `!` commit on the branch: the v10 cut.** Nothing before it.
- Commit BEFORE you gate; leave the worktree untouched for the WHOLE run; read
  the verdict in a SEPARATE step.
- `decisions.md` is APPEND-ONLY — generation 6 recorded the stub-fix finding in
  the LOG/REPORT rather than editing A-429's committed row. Touch ONLY
  `assay/**`.
- A-334: no test double as evidence about an EXTERNAL system. (Driving assay's
  OWN cleanup seam with an exception whose text is pinned by a real-leak test
  next door is not that; generation 6 did it once and said so in A-426.)
- **Gate a change to the gate script**, however small. A three-token edit
  turned it red.

## 9. Retention prompt for generation 7 (self-authored)

> **KEEP:** the branch/worktree identity and that the **gate-verified commit
> is `bfb55e3f`**; that **phase 1 and BOTH R-1 rounds are CLOSED** (A-408..
> A-426 landed, A-429 landed, B062/B063/B064 filed) and **the v10 cut is the
> whole remaining job**; §3's literal sequence — finish the THREE missing
> design rows (B004 under DA-D7+DA-R12, B007 under DA-D8, F015 under DA-D9)
> **before** the single `feat(assay)!:` cut carrying schema + dataclasses +
> **verify.py** + the W6 drift guard with W5 kept, then B050 → B051 → B052 →
> B053 `detail` → B004 → B007 → migration notes; that **A-427 and A-428
> already specify B050's `judgment.r2.fail_under` and B053's `claim.detail`**
> and are implementable as written (§4's two easily-missed points: the
> producer fork and the deleted `config.py:2483-2512` refusal; the
> byte-vs-character bound split and head-kept truncation); that
> **`LANE_SCHEMA_VERSION` stays 2, `inventory_schema` stays 1, the four
> cmru-imported module paths must not be renamed, and dstdns's five
> read-by-name verdict fields stay**; **DA-R12** verbatim; that §4 of BRIEF-5
> (the ciu re-capture) is DONE with the integer 1 → 2 as its only delta;
> **BRIEF-4 §5's seam table**; §5's launch recipe, the foreign-container wait
> and the blocking-wait trick; §6's ids (next free **A-430**, **B065**) and
> that `main` moves every generation; §8's rules; and **§7's two open decision
> asks — F015's claim kind (which BLOCKS the cut) and B007's unmeasured
> target bound.**
>
> **DROP:** the reading trail behind phase 1, both R-1 rounds and the three
> generation-6 fixes (the REPORT has every transcript); the four red-proof
> transcripts; the per-container detail of the ciu documents; the docs-wording
> debates from generations 2–4; the argv-position history of the gate stubs.
>
> **DO NOT** write the `feat(assay)!:` cut before all five wire changes exist
> as A-rows; do not bump the schema twice; do not decide F015's claim kind on
> silence; do not choose B007's bound without measuring one materialisation;
> do not re-open a settled phase-1 item, a landed fix, or A-425's grace; do
> not run two gate containers or an xdist pytest; do not build B020, B023,
> B001's residual, B010's orchestration half, B048's judge verb, Go R2/R3, an
> `assay canary qualify` document kind, or any part of B064.
