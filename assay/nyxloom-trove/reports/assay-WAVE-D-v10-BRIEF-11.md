# Wave D (v10 integrity) — BRIEF-11

**Written by generation 11 at its E-008 checkpoint, for generation 12.**
Branch `feature/assay-wave-d-v10`, worktree
`/workspaces/vbpub/.worktrees/assay-wave-d-v10`.

## 1. Where the branch stands, in one paragraph

**Five of the eight post-cut items are DONE and the registered gate is GREEN
on the fifth.** The tip is the checkpoint commit; beneath it **`d0e212e2`**
(`feat(assay): a refusal's own sentence reaches the wire …`, B053/A-439) — the
**gate-verified commit** — then `9de276bd` (generation 10's checkpoint),
`83c31f18` (B052/A-438), `5b2730b6` (B051/A-437), `1b127356` (generation 9's
checkpoint), `962211cd` (B050/A-436), `61b8d836` (B069/A-435), and the one and
only `!` commit `b2fd09f3`. Gate log `<scratchpad>/gate-gen11a.log`:
`GATE_EXIT=0` (exactly one), one `ASSAY_REGISTERED_GATE_COMPLETE=1`, **zero**
`FAILED|DIRTY_TREE|Traceback`, wheel
**`assay-4.1.1.dev38+gd0e212e2-py3-none-any.whl`**, twelve phases including
`verdict-v10-successors-verified`. **Nothing is blocked and generation 11
raises no decision asks.** Three items remain, in DA-R27's order: B004
`PROVENANCE_UNVERIFIED` producer → B007 multi-target canary loop → CONSUMERS
"Migration notes (v9 → v10)" LAST.

## 2. Read this, in this order, before touching anything

1. `nyxloom-trove/WAVE-PROMPT-2026-09-02-wave-d-v10-integrity.md`, the
   **"Implementer prompt — generation 1"** section — standing orders, all
   still binding: `git -C <worktree>` for every git command, **Edit tool
   never `sed`/python rewrite scripts**, both commit trailers, `git commit -F
   <msgfile> --only -- <paths>`, `decisions.md` append-only, `assay/**` only,
   no push, no merge, A-334, and the BLOCKED clause.
2. `reports/assay-WAVE-D-v10-CONTROLLER-LOG.md` on `main` — DA-R1..**DA-R27**,
   plus **anything the controller added past DA-R27, which you read FIRST.**
3. **`nyxloom-trove/W2-CARVE-B004-provenance-verified.md` IN FULL**, and
   `reports/assay-B004-carve-review-fable.md`. B004 is the next item and it is
   a carve, not a one-file edit — see §4.1.
4. `decisions.md`'s **A-430** (B004's design) and **A-431** (the three ledger
   corrections its W0 owes), and the branch `-LOG.md` entry **35** +
   `-REPORT.md`'s **"Generation 11"** section.

## 3. The two lessons that still cost runs

**(a) `pytest tests -q` is green here with 20 tests skipped** — exactly the
harnesses that read a real produced artifact inside `tester-unified:local`.
Every consumer of a wire shape under `gate/python/` is invisible to a local
run. `tests/test_gate_harness_version_pins.py` (B069) fires locally on a stale
`schema_version` pin or a stale `carve-assets/W<n>` root and covers nothing
else, so **before your first gate run of a change that touches a wire shape,
`grep gate/ tools/` for every field you touch.** Generations 9, 10 and 11 all
did; all three went green first time. Generation 11's grep is written out in
the REPORT (`detail` → two prose hits, no reader).

**(b) A produced-output change can break a HAND-WRITTEN FIXTURE that no
grep finds.** B053's producers broke three
`tests/fixtures/verdicts/r1_no_measurement_*.json` whole-document comparisons
— correctly, because the wire genuinely gained a field. Two of those three
sentences name a per-run temp path or an abbreviated revision, which is why
`_fixture_with` + a `<SOURCE_ROOT>`/`<HEAD12>` placeholder now exists in
`tests/test_runner_evaluate_r1.py`. **B004 and B007 will both add fields to
produced documents. Run `tests/test_runner_evaluate_r1.py` and the whole
`tests/fixtures/verdicts/` consumer set early**, not at the end.

## 4. The work that remains, in DA-R27's order. Each item = one A-row + one commit.

### 4.0 DONE — do not re-land

* **B069** (A-435, `61b8d836`), **B050** (A-436, `962211cd`) — generation 9.
* **B051** (A-437, `5b2730b6`) — resolved by DA-R26's route 1; B070 filed.
* **B052** (A-438, `83c31f18`) — DA-D5's content tier.
* **B053 (c)** (A-439, **`d0e212e2`**) — the `detail` PRODUCERS. **GATE GREEN
  on this commit.** `announce_refusal` now RETURNS the bounded sentence it
  printed (`verdict.RefusalDetail` / `verdict.refusal_detail`), and ~17
  conversion sites plus `cli.py`'s four pass it into the `Claim` they build.
  Two sites take `refusal_detail(str(exc))` directly and say why in a comment
  (the deferred equivalence refusal; the R3 claim an R2 fault also refuses).
  Twelve new tests at the end of `tests/test_refusal_announcement.py`.

### 4.1 B004 — `PROVENANCE_UNVERIFIED`'s producer. **NEXT, and BIGGER than BRIEF-10 implied.**

Ruling **A-430** (four numbered parts) with **A-431**'s ledger corrections.

**What is already on the wire** (landed with the cut, verified by generation
11): the reason code in all four places —
`src/assay/errors.py:166` (`ReasonCode.PROVENANCE_UNVERIFIED`),
`errors.py:243` (`REASON_CODES[NO_MEASUREMENT]`),
`src/assay/schemas/verdict.schema.json:622` (flat enum) and `:667`
(`$defs/reason_codes/NO_MEASUREMENT`) — and A-430 (2)'s
`adjudicated ⇒ verified_by_assay is False` narrowing in BOTH layers.

**What is NOT there, and is the actual work.** `src/assay/` has **no
image-provenance adjudicator at all** — `provenance.py` is B018's JUDGE
provenance (which build of assay is running) and is unrelated. So A-430 (4)'s
lane surface (`judge.adjudication_dir`; `judge.evidence = [{source =
"adjudicated", name = "image-provenance"}]`), A-430 (3)'s ONE
`schema_version` parser accepting the integer set `{1, 2}` with
`ERROR`/`UNREADABLE_ARTIFACT` for `3`, for the STRING `"2"` and for absence,
the `mismatch → NO_MEASUREMENT/PROVENANCE_UNVERIFIED` mapping, and every
producer of the code all remain to be built, together with the carve's
W2–W7. **`LANE_SCHEMA_VERSION` stays 2** (the key is additive-optional, ruled
by DA-D7).

**Assets.** `nyxloom-trove/carve-assets/W2/` already holds
`ciu-provenance-green-reference.json`,
`ciu-provenance-live-mismatch.json` and
`ciu-provenance-live-mismatch-ciu-7.10.1.json`. **W2 is otherwise FROZEN**;
the wave prompt's "re-capture the ciu 7.10.1 provenance assets FIRST" refers
to the CARVE's own W2–W7 work items, not to `carve-assets/W2`. Generation 4
already re-captured and recorded the measured delta —
`-REPORT.md`'s "Generation 4 — B004's ciu assets, RE-CAPTURED (DA-D7), with
the measured delta" section. **Read that section before re-capturing
anything.** A-334: the `verified-match` PASS oracle rides
`ciu-provenance-green-reference.json` (a real ciu-produced document with a
PROVENANCE entry), never a hand-written one, because `overall` is `mismatch`
on this host.

**THE TRIPWIRE, still armed.** `tests/test_verdict_conformance.py:221-227`'s
`EXCLUDED_ENTIRELY` set still contains `("NO_MEASUREMENT",
"PROVENANCE_UNVERIFIED")`, and the written obligation at `:206-216` says the
entry leaves **the moment the producer exists**. Generation 11 deliberately
left it: removing it without a producer asserts something untrue. **Removing
it is part of B004's commit, not a separate step, and it is the box most
likely to be missed.**

### 4.2 B007 — the multi-target canary LOOP

Ruling **A-432**. `canary.py` already has `judge_attempt(attempt)` and
`judge_canary(result, *, aggregation=None)`; `run_isolated_canary` is the
single-target runner. The loop producing more than ONE `CanaryAttempt` over
`judgment.r3.targets` (1..8, ordered, unique) with `aggregation`
(`any`/`all`, required iff >1 target) is what is missing.
`MAX_CANARY_TARGETS = 8` at `verdict.py:444`.
**DA-R19: `all` does NOT short-circuit on FAIL — the 2N materialisation bound
is deliberate. DA-R20: one shared control materialisation stays REJECTED.**
`not_attempted_reason`'s vocabulary is closed: `short_circuited`,
`budget_exhausted`, `earlier_target_terminal`.
**Replace the HAND-AUTHORED
`carve-assets/W6/expected/multi-target-r3-v10-template.json` with real output
produced through the shipped substrate, and fix the W6 MANIFEST row — the ONE
authorised W6 edit** (A-334: no hand-authored witness).

### 4.3 CONSUMERS.md — "Migration notes (v9 → v10)". **THE LAST ITEM.**

A new section beside `## Adopting a v2-capable release`, carrying: the
one-element `targets` list for R3 lanes; `judgment.r2.fail_under` for ingested
lanes; **`judgment.r2.discarded` is declared-not-verified** (DA-R26/DA-R27);
v9 verdicts refused by `assay verify`; the new refusals (B052's content check,
B054's per-file istanbul disposition); **`detail` present on refusals**; the
adjudicated provenance lane shape with the `|| true` example; Python and Go
lanes without R3 unchanged.

**FOUR sections now forward-reference it and must not be left dangling** —
generation 11 added the fourth:
1. `docs/CONSUMERS.md`'s ingested-R2 floor paragraph ("**B050**, schema v10 —
   see the migration notes"),
2. its content-tier paragraph ("**B052**, this release — see the migration
   notes"),
3. `CHANGES.md`'s v9→v10 block,
4. **NEW:** `docs/CONSUMERS.md`'s retitled refusal-line section, which links
   `(#migration-notes-v9--v10)` by anchor. **That anchor must resolve** —
   check the heading spelling against it when you write the section.

## 5. Hard invariants — re-verify, do not assume

* **Exactly ONE `!` commit on this branch and it already exists**
  (`b2fd09f3`). If an item feels like it needs a second, STOP and ask.
* `LANE_SCHEMA_VERSION` stays **2**; `inventory_schema` stays **1**.
* No rename of `assay.diff` / `assay.git` / `assay.mutation` /
  `assay.adapters.python`.
* dstdns reads `outcome`, `status`, `coverage.pct`, `coverage.missing_lines`,
  `coverage.missing_branch_lines` **by name**.
* `carve-assets/W1`..`W5` stay frozen (only
  `W3/expected/dstdns-sql-r2-v6-witness.json` is deliberately live). **W6 is
  frozen too**, with ONE authorised exception left: B007's multi-target
  template (§4.2).
* **`src/assay/schemas/verdict.schema.json` is byte-frozen** against
  `carve-assets/W6/verdict.schema.v10.json`. Generation 10's
  description-only amendment was ruled (DA-R26) and is recorded in the W6
  MANIFEST. **Do not edit either again without a ruling that says so.**
* DA-R21: F015/R4's IMPLEMENTATION is not in this wave; M7 stays PLANNED.
* A-334: no test double, and no hand-authored document, as evidence about an
  external system.

## 6. Ids

* Next free A-id: **A-440** (`main` still at A-407; branch high-water A-439).
* Next free B-id: **B071** (`main` at **B068**, re-checked by generation 11;
  the branch has taken B069 and B070). Re-run
  `git -C /workspaces/vbpub show main:assay/nyxloom-trove/4-backlog.md |
  grep -o '^## B[0-9]*' | tail -1` before allocating, **every time**. Expect a
  trivial append-conflict on `4-backlog.md` at merge; **main wins on ids.**

## 7. Host load — binding, a production game server shares this 8-core host

pytest **serial only**, never `-n`/xdist, always `nice -n 19 ionice -c 3
python -m pytest …`. Targeted files while iterating; the whole suite **at most
once per checkpoint**. **ONE gate container at a time across all agents**:
check BOTH `docker ps --format '{{.Image}} {{.Names}}'` (no
`tester-unified:local`, no `run-gate-*`) AND `pgrep -af
tester-unified-gate.sh`, and WAIT rather than run two. Note that `pgrep -af`
matches your OWN command line — generation 11's hit was itself; read the
match before believing it. Long-lived `assay-p34w9-*` postgres containers and
`dstdns-*` service containers are NOT gate runs. Cap within seconds of launch:
`docker update --cpus=3 $(docker ps -q --filter
ancestor=tester-unified:local)`. **Do not edit the worktree while the gate is
running** (draft into the scratchpad instead — generation 11 wrote its whole
REPORT section there during the run).

## 8. The gate recipe that has now worked six times

Launch from `/workspaces/vbpub` (the ONLY thing that belongs there), after
committing, with the worktree clean:

```
setsid nohup bash -c '{ bash assay/tools/tester-unified-gate.sh \
  /workspaces/vbpub/.worktrees/assay-wave-d-v10; echo GATE_EXIT=$?; } \
  > <scratchpad>/gate-<tag>.log 2>&1' < /dev/null > /dev/null 2>&1 &
```

Then, in the NEXT call, a cap loop (`for i in $(seq 1 40); do CID=$(docker ps
-q --filter ancestor=tester-unified:local); [ -n "$CID" ] && docker update
--cpus=3 $CID && break; sleep 10; done`), then block on `for i in $(seq 1 19);
do grep -q 'GATE_EXIT=' <log> && break; sleep 30; done` with `timeout:
600000` — a run is ~22-25 minutes, so expect to need that wait twice. **Read
the verdict in a SEPARATE step** (LESSONS L4) and require ALL of: `GATE_EXIT=0`
(exactly one), exactly one `ASSAY_REGISTERED_GATE_COMPLETE=1`, zero
`FAILED|DIRTY_TREE|Traceback`, the wheel name carrying YOUR commit, and the
`verdict-v10-successors-verified` phase.

## 9. Retention prompt for generation 12 (paste as the `/compact` seed)

> **KEEP.** Branch `feature/assay-wave-d-v10` in
> `/workspaces/vbpub/.worktrees/assay-wave-d-v10`. Gate-verified tip
> **`d0e212e2`** (B053 `detail` PRODUCERS, A-439); `gate-gen11a.log` is GREEN
> (`GATE_EXIT=0` once, one `ASSAY_REGISTERED_GATE_COMPLETE=1`, zero
> `FAILED|DIRTY_TREE|Traceback`, wheel
> `assay-4.1.1.dev38+gd0e212e2-py3-none-any.whl`, twelve phases incl.
> `verdict-v10-successors-verified`). Beneath it `83c31f18` (B052, A-438),
> `5b2730b6` (B051, A-437), `962211cd` (B050, A-436), `61b8d836` (B069,
> A-435) and the one and only `!` commit `b2fd09f3`. **Five of eight post-cut
> items done; three remain, in DA-R27's order: B004 `PROVENANCE_UNVERIFIED`
> producer → B007 multi-target canary loop → CONSUMERS "Migration notes
> (v9 → v10)" LAST. Nothing is blocked.** Load-bearing seams: **B004 is a
> CARVE, not an edit** — `src/assay/` has NO image-provenance adjudicator
> (`provenance.py` is B018's JUDGE provenance); A-430 (1)+(2) are on the wire
> (`errors.py:166`/`:243`, `verdict.schema.json:622`/`:667`, the
> `adjudicated ⇒ verified_by_assay is False` narrowing in both layers) and
> A-430 (3) the `{1, 2}` `schema_version` parser, (4) the
> `judge.adjudication_dir` + `adjudicated` evidence lane surface, the
> `mismatch → NO_MEASUREMENT` mapping and every producer are NOT; the ciu
> assets already sit in `carve-assets/W2/`
> (`ciu-provenance-green-reference.json` is the A-334 PASS oracle, because
> `overall` is `mismatch` on this host) and generation 4's REPORT section
> records the measured 7.10.1 delta; **the tripwire
> `tests/test_verdict_conformance.py:221-227`'s `EXCLUDED_ENTIRELY` entry
> `("NO_MEASUREMENT", "PROVENANCE_UNVERIFIED")` is STILL ARMED and leaves in
> B004's own commit** (obligation written at `:206-216`). B007 needs the
> RUNNER LOOP beside `canary.judge_attempt`/`judge_canary`, DA-R19 (`all`
> does NOT short-circuit; the 2N bound is deliberate), DA-R20 (one shared
> control stays rejected), `MAX_CANARY_TARGETS = 8` at `verdict.py:444`, the
> closed `not_attempted_reason` vocabulary, and
> `carve-assets/W6/expected/multi-target-r3-v10-template.json` replaced by
> REAL output with the MANIFEST fixed (the ONE authorised W6 exception). The
> migration notes are forward-referenced from **FOUR** places now — the two
> CONSUMERS paragraphs (B050 floor, B052 content tier), CHANGES.md's v9→v10
> block, and CONSUMERS' retitled refusal-line section, which links
> `(#migration-notes-v9--v10)` **by anchor, so the heading spelling must
> match** — and must state that `discarded` is declared-not-verified. Wire
> changes break HAND-WRITTEN FIXTURES no grep finds: B053 broke three
> `tests/fixtures/verdicts/r1_no_measurement_*.json`, now carrying
> `<SOURCE_ROOT>`/`<HEAD12>` placeholders filled by `_fixture_with` in
> `tests/test_runner_evaluate_r1.py`; run that module EARLY.
> `src/assay/schemas/verdict.schema.json` is BYTE-FROZEN against W6. Next ids
> **A-440 / B071**, re-checked against `main` every time. DA-R21: F015/M7
> stay PLANNED. Host rule: serial `nice -n 19 ionice -c 3` pytest, whole
> suite once per checkpoint, ONE gate container (`pgrep -af` matches your own
> command line — read the match), cap at 3 CPUs, never edit the worktree
> during a gate run.
> **DROP.** B053's site-by-site threading detail (settled in A-439 and the
> code's own comments), B051's three-routes argument, B052's normalisation
> deliberation, the six gate-run transcripts, and generations 8-10's
> blocked-item narrative.
