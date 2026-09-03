# Wave D (v10 integrity) — BRIEF-12

**Written by generation 12 at its E-008 checkpoint, for generation 13.**
Branch `feature/assay-wave-d-v10`, worktree
`/workspaces/vbpub/.worktrees/assay-wave-d-v10`.

## 1. Where the branch stands, in one paragraph

**SEVEN of the eight post-cut items are done and the registered gate is GREEN
on each of the last two.** The tip is this checkpoint commit; beneath it
**`fd489620`** (`docs(assay): the v9 -> v10 migration notes …`, A-441,
**gate-verified**, `gate-gen12b.log`, wheel
`assay-4.1.1.dev41+gfd489620-py3-none-any.whl`) and **`d30b313b`**
(`feat(assay): a canary declares SEVERAL probes …`, B007/A-440,
**gate-verified**, `gate-gen12a.log`, wheel
`assay-4.1.1.dev40+gd30b313b-py3-none-any.whl`), then `97907425`
(generation 11's checkpoint), `d0e212e2` (B053/A-439), `83c31f18` (B052),
`5b2730b6` (B051), `962211cd` (B050), `61b8d836` (B069) and the one and only
`!` commit `b2fd09f3`. Both gate logs, read in a separate step: `GATE_EXIT=0`
exactly once, one `ASSAY_REGISTERED_GATE_COMPLETE=1`, **zero**
`FAILED|DIRTY_TREE|Traceback`, the wheel carrying that commit, twelve phases
including `verdict-v10-successors-verified`. **ONE item remains: B004, the
whole carve.** Nothing is blocked. Generation 12 raises **one** decision ask
(REPORT, "Decision asks for the controller (generation 12)", ask 1) and it
blocks nothing.

## 2. Read this, in this order, before touching anything

1. `nyxloom-trove/WAVE-PROMPT-2026-09-02-wave-d-v10-integrity.md`, the
   **"Implementer prompt — generation 1"** section — standing orders, all
   still binding: `git -C <worktree>` for every git command, **Edit tool for
   every change to EXISTING content** (heredoc appends of purely NEW blocks
   are accepted, DA-R29), both commit trailers, `git commit -F <msgfile>
   --only -- <paths>`, `decisions.md` append-only, `assay/**` only, no push,
   no merge, A-334, and the BLOCKED clause.
2. `reports/assay-WAVE-D-v10-CONTROLLER-LOG.md` on `main` — DA-R1..**DA-R29**,
   plus **anything the controller added past DA-R29, which you read FIRST.**
3. **`nyxloom-trove/W2-CARVE-B004-provenance-verified.md` IN FULL**, and
   `reports/assay-B004-carve-review-fable.md`. **B004 is a CARVE, not a
   one-file edit.**
4. `decisions.md`'s **A-430** (B004's design) and **A-431** (the three ledger
   corrections its W0 owes); the branch `-LOG.md` entries **37-39** and
   `-REPORT.md`'s **"Generation 12"** section; and, before touching any ciu
   asset, `-REPORT.md`'s **"Generation 4 — B004's ciu assets, RE-CAPTURED
   (DA-D7), with the measured delta"** section.

## 3. The three lessons that still cost runs

**(a) `pytest tests -q` is green here with 20 tests skipped** — exactly the
harnesses that read a real produced artifact inside `tester-unified:local`.
Every consumer of a wire shape under `gate/python/` is invisible to a local
run. **Before your first gate run of a change that touches a wire shape,
`grep -rn <field> gate/ tools/`.** Generations 9-12 all did; all went green
first time. Generation 12's grep is written out in the REPORT
(`qualify_cmru_b006a.py` is the only canary reader, and it declares the
singular spelling).

**(b) A produced-output change can break a HAND-WRITTEN FIXTURE that no grep
finds.** Run `tests/test_runner_evaluate_r1.py` and the whole
`tests/fixtures/verdicts/` consumer set EARLY. (B007 did not break one, but
B004's producers add fields to produced documents, so this is live again.)

**(c) NEW — a ruling written before the cut can name a file:line the cut
moved.** A-432 said to spell `MIN_/MAX_CANARY_TARGETS` in `config.py`; the cut
had already given them an owner in `verdict.py`, and duplicating them would
have created exactly the drift the estate hates. The rule generation 12
applied, and B004 will need it too: **when a ruling's mechanics conflict with
what the cut actually landed, follow the ruling's ARGUMENT and record the
deviation in the A-row** — do not silently do either one.

## 4. The one item that remains: B004

Ruling **A-430** (four numbered parts) with **A-431**'s ledger corrections.
BRIEF-11 §4.1's scoping is unchanged and still binds:

**On the wire already** (landed with the cut): `ReasonCode.PROVENANCE_UNVERIFIED`
(`errors.py:166`), `REASON_CODES[NO_MEASUREMENT]` (`errors.py:243`),
`verdict.schema.json:622` (flat enum) and `:667`
(`$defs/reason_codes/NO_MEASUREMENT`), and A-430 (2)'s
`adjudicated ⇒ verified_by_assay is False` narrowing in BOTH layers.

**NOT there, and the actual work.** `src/assay/` has **no image-provenance
adjudicator at all** — `provenance.py` is B018's JUDGE provenance and is
unrelated. So: A-430 (3)'s ONE `schema_version` parser accepting the integer
set `{1, 2}` with `ERROR`/`UNREADABLE_ARTIFACT` for `3`, for the STRING
`"2"` and for absence; A-430 (4)'s lane surface (`judge.adjudication_dir`,
`judge.evidence = [{source = "adjudicated", key = "image-provenance"}]`,
`_EVIDENCE_SOURCES` widened, the per-source pairing rule replacing
`config.py:1160`'s `has_attestation_dir == has_evidence`); the
`mismatch → NO_MEASUREMENT/PROVENANCE_UNVERIFIED` mapping; every producer;
and the carve's W2–W7. **`LANE_SCHEMA_VERSION` stays 2** (additive-optional,
DA-D7).

**Assets.** `nyxloom-trove/carve-assets/W2/` already holds
`ciu-provenance-green-reference.json`, `ciu-provenance-live-mismatch.json`
and `ciu-provenance-live-mismatch-ciu-7.10.1.json`. **W2 is otherwise
FROZEN.** Generation 4 already re-captured and recorded the measured delta —
read that REPORT section before re-capturing anything. A-334: the
`verified-match` PASS oracle rides `ciu-provenance-green-reference.json` (a
real ciu-produced document), never a hand-written one, because `overall` is
`mismatch` on this host.

**THE TRIPWIRE, still armed.** `tests/test_verdict_conformance.py:221-227`'s
`EXCLUDED_ENTIRELY` still contains `("NO_MEASUREMENT",
"PROVENANCE_UNVERIFIED")`, and the obligation at `:206-216` says it leaves
**the moment the producer exists**. **Removing it is part of B004's own
commit and is the box most likely to be missed.**

**One documentation edit B004 OWES, already staged for it.** The migration
notes' last subsection ("Reserved on the wire, producer pending: adjudicated
image provenance (B004)") describes the lane shape as *designed and not yet
available* and carries the `|| true` example. **It is written to be AMENDED,
not rewritten**: when the producer lands, change that framing and nothing
else, and check the anchor `#migration-notes-v9--v10` still resolves (four
links depend on it; a link check over README/CONSUMERS/DESIGN-GUIDE was
clean at `fd489620`).

**Cut at B004's internal boundaries** (after the parser, after the lane
surface, after the adjudicator, after the producers, after W2–W7), each
gate-green or at least suite-green with the tripwire still armed.

## 5. Hard invariants — re-verify, do not assume

* **Exactly ONE `!` commit on this branch and it already exists**
  (`b2fd09f3`). If an item feels like it needs a second, STOP and ask.
* `LANE_SCHEMA_VERSION` stays **2**; `inventory_schema` stays **1**.
* No rename of `assay.diff` / `assay.git` / `assay.mutation` /
  `assay.adapters.python`.
* dstdns reads `outcome`, `status`, `coverage.pct`, `coverage.missing_lines`,
  `coverage.missing_branch_lines` **by name**.
* `carve-assets/W1`..`W5` stay frozen (only
  `W3/expected/dstdns-sql-r2-v6-witness.json` is deliberately live).
  **W6's one authorised post-cut edit is now SPENT** — B007's multi-target
  template is real output and its MANIFEST row says so. W6 is frozen again.
* **`src/assay/schemas/verdict.schema.json` is byte-frozen** against
  `carve-assets/W6/verdict.schema.v10.json`. Do not edit either without a
  ruling that says so.
* DA-R21: F015/R4's IMPLEMENTATION is not in this wave; M7 stays PLANNED.
* A-334: no test double, and no hand-authored document, as evidence about an
  external system.

## 6. Ids

* Next free A-id: **A-442** (`main` still at A-407; branch high-water A-441).
* Next free B-id: **B071** (`main` at **B068**, re-checked by generation 12).
  Re-run `git -C /workspaces/vbpub show main:assay/nyxloom-trove/4-backlog.md
  | grep -o '^## B[0-9]*' | tail -1` before allocating, **every time**.
  Expect a trivial append-conflict on `4-backlog.md` at merge; **main wins on
  ids.**

## 7. Host load — binding, a production game server shares this 8-core host

pytest **serial only**, never `-n`/xdist, always `nice -n 19 ionice -c 3
python -m pytest …`. Targeted files while iterating; the whole suite **at most
once per checkpoint**. **ONE gate container at a time across all agents**:
check BOTH `docker ps --format '{{.Image}} {{.Names}}'` (no
`tester-unified:local`, no `run-gate-*`) AND `pgrep -af tester-unified-gate.sh`,
and WAIT rather than run two — generation 12 waited ~3 minutes for a
`run-gate-vbpub-tester-unified-*` container to finish and that was the correct
call. `pgrep -af` matches your OWN command line; read the match. Long-lived
`assay-p34w9-*` postgres and `dstdns-*` service containers are NOT gate runs.
Cap within seconds of launch: `docker update --cpus=3 $(docker ps -q --filter
ancestor=tester-unified:local)`. **Do not edit the worktree while the gate is
running** — draft into the scratchpad instead.

## 8. The gate recipe that has now worked eight times

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
600000` — a run is ~15-25 minutes, so expect to need that wait twice. **Read
the verdict in a SEPARATE step** (LESSONS L4) and require ALL of:
`GATE_EXIT=0` (exactly one), exactly one `ASSAY_REGISTERED_GATE_COMPLETE=1`,
zero `FAILED|DIRTY_TREE|Traceback`, the wheel name carrying YOUR commit, and
the `verdict-v10-successors-verified` phase.

## 9. Retention prompt for generation 13 (paste as the `/compact` seed)

> **KEEP.** Branch `feature/assay-wave-d-v10` in
> `/workspaces/vbpub/.worktrees/assay-wave-d-v10`. Gate-verified tips
> **`d30b313b`** (B007's multi-target canary loop, A-440) and **`fd489620`**
> (the v9→v10 migration notes, A-441); `gate-gen12a.log` and
> `gate-gen12b.log` are both GREEN (`GATE_EXIT=0` once, one
> `ASSAY_REGISTERED_GATE_COMPLETE=1`, zero `FAILED|DIRTY_TREE|Traceback`,
> wheels `assay-4.1.1.dev40+gd30b313b` / `dev41+gfd489620`, twelve phases
> incl. `verdict-v10-successors-verified`). Beneath them `d0e212e2` (B053,
> A-439), `83c31f18` (B052), `5b2730b6` (B051), `962211cd` (B050),
> `61b8d836` (B069) and the one and only `!` commit `b2fd09f3`. **Seven of
> eight post-cut items done. ONE remains: B004, the whole carve. Nothing is
> blocked.** Load-bearing seams: **B004 is a CARVE, not an edit** —
> `src/assay/` has NO image-provenance adjudicator (`provenance.py` is
> B018's JUDGE provenance); A-430 (1)+(2) are on the wire
> (`errors.py:166`/`:243`, `verdict.schema.json:622`/`:667`, the
> `adjudicated ⇒ verified_by_assay is False` narrowing in both layers) and
> A-430 (3) the `{1, 2}` `schema_version` parser, (4) the
> `judge.adjudication_dir` + `adjudicated`-evidence lane surface (widening
> `_EVIDENCE_SOURCES`, replacing `config.py:1160`'s pairing rule with the
> per-source one), the `mismatch → NO_MEASUREMENT` mapping and every
> producer are NOT; the ciu assets already sit in `carve-assets/W2/`
> (`ciu-provenance-green-reference.json` is the A-334 PASS oracle, because
> `overall` is `mismatch` on this host) and generation 4's REPORT section
> records the measured 7.10.1 delta; **the tripwire
> `tests/test_verdict_conformance.py:221-227`'s `EXCLUDED_ENTIRELY` entry
> `("NO_MEASUREMENT", "PROVENANCE_UNVERIFIED")` is STILL ARMED and leaves in
> B004's own commit** (obligation at `:206-216`). **B004 also OWES one
> documentation amendment**: the migration notes' last subsection
> ("Reserved on the wire, producer pending … (B004)") is written to be
> amended, not rewritten, and the anchor `#migration-notes-v9--v10` has four
> referrers. **W6's one authorised post-cut edit is SPENT** (B007's
> multi-target template is now REAL output of `assay run`; MANIFEST says
> so); `src/assay/schemas/verdict.schema.json` stays BYTE-FROZEN against the
> W6 copy. Generation 12's own lesson: when a pre-cut ruling's mechanics
> conflict with what the cut landed (A-432 said to spell
> `MIN_/MAX_CANARY_TARGETS` in `config.py`; the cut gave them an owner in
> `verdict.py`), follow the ruling's ARGUMENT and record the deviation in
> the A-row. Wire changes break HAND-WRITTEN FIXTURES no grep finds — run
> `tests/test_runner_evaluate_r1.py` and the `tests/fixtures/verdicts/`
> consumers EARLY — and every consumer under `gate/python/` is invisible to
> a local run, so grep `gate/ tools/` for each field you touch before the
> gate. Next ids **A-442 / B071**, re-checked against `main` every time.
> DA-R21: F015/M7 stay PLANNED. Host rule: serial `nice -n 19 ionice -c 3`
> pytest, whole suite once per checkpoint, ONE gate container (wait, don't
> race), cap at 3 CPUs, never edit the worktree during a gate run.
> **DROP.** B007's site-by-site loop detail (settled in A-440 and the code's
> own docstrings), the two verifier defects' argument (fixed and recorded),
> B053/B052/B051's deliberations, the eight gate-run transcripts, and
> generations 8-11's blocked-item narrative.
