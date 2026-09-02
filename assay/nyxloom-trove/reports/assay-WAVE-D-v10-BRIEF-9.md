# Wave D (v10 integrity) — BRIEF-9

**Written by generation 9 at its E-008 checkpoint, for generation 10.**
Branch `feature/assay-wave-d-v10`, worktree
`/workspaces/vbpub/.worktrees/assay-wave-d-v10`.

## 1. Where the branch stands, in one paragraph

**Two of the eight post-cut items are DONE and the registered gate is GREEN on
them.** The tip is `962211cd` (`feat(assay): judgment.r2.fail_under becomes a
floor that is TAKEN`), beneath it `61b8d836` (the B069 tripwire), beneath that
generation 8's checkpoint and the one `!` commit `b2fd09f3`. The gate log is
`gate-gen9a.log`: `GATE_EXIT=0`, one `ASSAY_REGISTERED_GATE_COMPLETE=1`, zero
`FAILED|DIRTY_TREE|Traceback`, wheel
`assay-4.1.1.dev33+g962211cd-py3-none-any.whl`, twelve phases including
`verdict-v10-successors-verified`. **The next item in order, B051, is BLOCKED
on a ruling that cannot be applied as written** (decision ask 2, below and in
the REPORT); nothing was improvised in its place and B052 was deliberately not
started ahead of it. **You must not start item 3 until the controller answers
ask 2 and ask 3.**

## 2. Read this, in this order, before touching anything

1. `nyxloom-trove/WAVE-PROMPT-2026-09-02-wave-d-v10-integrity.md`, IMPLEMENTER
   PROMPT section — standing orders, all still binding: `git -C <worktree>` for
   every git command, **Edit tool never `sed`/python rewrite scripts**, both
   commit trailers, `git commit -F <msgfile> --only -- <paths>`, `decisions.md`
   append-only, `assay/**` only, no push, no merge, A-334, and the **BLOCKED
   clause** (implement what does not depend on a ruling, write the exact
   question, commit, return — do not improvise a product call).
2. `reports/assay-WAVE-D-v10-BRIEF-7.md` §3.1 — the cut's own spec, so you do
   not re-land what is already on the wire. `reports/assay-WAVE-D-v10-BRIEF-8.md`
   §4 — the remaining items' measured seams, still accurate except where §4
   below supersedes it.
3. `reports/assay-WAVE-D-v10-CONTROLLER-LOG.md` — DA-R1..**DA-R24**, plus
   whatever the controller adds in answer to this brief's three asks. **Read
   the answers first; two of the remaining items are gated on them.**
4. This file, §3 onward, and the REPORT's **"Decision asks for the controller
   (generation 9)"** section — ask 2 is a full argument with file:line
   evidence, not a one-liner, and it is the reason the wave stopped where it
   did.
5. The branch `-LOG.md` entries **29-31** and the `-REPORT.md` generation-9
   section.

## 3. The lesson that still costs gate runs

`pytest tests -q` is green here with **20 tests skipped**, and those twenty are
exactly the harnesses that read a real produced artifact inside
`tester-unified:local`. Every consumer of a wire shape under `gate/python/` is
invisible to a local run.

**This is now half-automated. Use it.** `tests/test_gate_harness_version_pins.py`
(B069) fires locally on a stale `schema_version` pin or a stale
`carve-assets/W<n>` root. It does **not** cover any other field, so the rule
stands: **before your first gate run of a change that touches a wire shape,
`grep gate/python/` for it.** Generation 9 did exactly that for B050
(`fail_under`: two hits, both the unrelated R1 coverage floor) and its gate
went green first time.

## 4. The work, in order. Each item = one A-row + one commit.

### 4.0 DONE — B069 (A-435) and B050 (A-436)

Do not re-land either. B050's backlog acceptance boxes are ticked with
file:line evidence; B069 was filed RESOLVED at filing.

### 4.1 B051 — `discarded` becomes a LISTED field. **BLOCKED — do not start.**

Ruling **DA-D4** instructs a re-derivation in
`verify._check_ingested_r2_agrees_with_its_payload`. **It is not constructible
from the document**, and the REPORT's decision ask 2 proves it at three
file:line seams:

* a discarded mutant is in no `Mutation` bucket — `mutation.py:1967-1969`
  `continue`s past the assignment;
* it is in no count either — `candidate_count` and `total` are both
  `attempted` (`mutation.py:1992-1997`), and `Mutation._check_arithmetic`
  (`verdict.py:1684-1703`) FORBIDS `candidate_count != total` outside the
  limit sentinel, so `discarded = candidate_count - total` is not merely
  absent but illegal;
* its LINE is not in `lines_without_candidates` — `mutated_lines.add(...)`
  precedes the discard `continue` (`mutation.py:1966-1968`), correctly, since
  the tool did produce a candidate there.

Every bound that catches the `9999` reproduction (`discarded <= total`,
`<= candidate_count`) refuses a TRUTHFUL report that could not compile most of
its mutants — the exact report the field exists to make visible. The ingest
half of DA-D4 **already ships** (`_INGESTED_DISCARDED_STATUSES`,
`mutation.py:1845`, counted at `:1968`) — B046 landed "listed" semantics
before DA-D4 named them.

Three routes are laid out in the REPORT (declared-not-verified in three places,
the `producer_tool` pattern / a new wire field, which needs a v11 / an unsound
bound, not recommended). **Pick none of them yourself.** DA-D4's own witness
clause is also open: a real Stryker report with non-zero `discarded` cannot be
produced by an implementer (the gate image must not be invoked directly) and
A-334 forbids the hand-edited alternative.

### 4.2 B052 — content-tier normalised compare

Ruling **DA-D5**, via `SnapshotRepository.read_regular_file`, at ingest, inside
the baseline snapshot block `_ingest_r2_report` already runs in. **DA-R23
orders B051 before this**; generation 9 did not reorder on its own initiative
and neither should you (decision ask 3 asks the controller to say explicitly
whether B052 may take B051's slot).

### 4.3 B053 — `claim.detail`'s PRODUCERS

Rulings **DA-D2 (c)** and **A-428**. The field, its 2048-BYTE bound,
`detail_dropped_bytes` and the schema's two-bound split all landed in the cut;
what is missing is the code that composes a sentence. `DESIGN-GUIDE.md` §6's
B026 N-4 paragraph is already corrected where B053 supersedes it.

### 4.4 B004 — `PROVENANCE_UNVERIFIED`'s producer

Ruling **A-430**. Code and the `adjudicated ⇒ verified_by_assay is False`
narrowing are on the wire. **Landing the producer means removing B004 from
`tests/test_verdict_conformance.py`'s `EXCLUDED_ENTIRELY` set** — that is the
tripwire; the set carries a written obligation saying no producer exists yet.

### 4.5 B007 — the multi-target canary LOOP

Ruling **A-432**. `canary.py` already has `judge_attempt(attempt)` and
`judge_canary(result, *, aggregation=None)`; the runner loop that produces more
than one attempt is what is missing. **DA-R19: `all` does NOT short-circuit on
FAIL and the 2N bound is deliberate. DA-R20: one shared control materialisation
stays REJECTED.** When the loop lands, replace the HAND-AUTHORED
`carve-assets/W6/expected/multi-target-r3-v10-template.json` with real output
and fix the MANIFEST (which currently says it is hand-authored).

### 4.6 CONSUMERS.md — "Migration notes (v9 → v10)"

A new section beside `## Adopting a v2-capable release`. **The LAST item.**
Note that `docs/CONSUMERS.md`'s ingested-R2 section now ends with "(**B050**,
schema v10 — see the migration notes)", so the section is already
forward-referenced and must exist by the end of the wave.

## 5. Hard invariants — re-verify, do not assume

* **Exactly ONE `!` commit on this branch and it already exists** (`b2fd09f3`).
  If an item feels like it needs a second, that is a signal to stop and ask.
  **Decision ask 2's route 2 is exactly such a case** — it is a v11 item, not
  a Wave D one.
* `LANE_SCHEMA_VERSION` stays **2**; `inventory_schema` stays **1**.
* No rename of `assay.diff` / `assay.git` / `assay.mutation` /
  `assay.adapters.python` — cmru imports those four from a pinned zipapp.
* dstdns reads `outcome`, `status`, `coverage.pct`, `coverage.missing_lines`,
  `coverage.missing_branch_lines` **by name**.
* `carve-assets/W1`..`W5` stay frozen and unedited (only
  `W3/expected/dstdns-sql-r2-v6-witness.json` is deliberately live). **W6 is
  frozen too**, with ONE authorised exception: B007's multi-target template.
* **`src/assay/schemas/verdict.schema.json` is byte-frozen** against
  `carve-assets/W6/verdict.schema.v10.json` by
  `test_shipped_schema_is_byte_identical_to_the_locked_v10_asset`. Editing one
  without the other is an instant red; editing both retroactively rewrites the
  cut's own frozen record. Generation 9 touched neither.
* DA-R21: F015/R4's IMPLEMENTATION is not in this wave; M7 stays PLANNED; the
  R4 wire shape ships without a producer.

## 6. Ids

* Next free A-id: **A-437** (`main` is still at A-407; the branch's high-water
  mark is A-436).
* Next free B-id: **B070** (`main` was at B068 when generation 9 checked;
  re-run `git -C /workspaces/vbpub show main:assay/nyxloom-trove/4-backlog.md |
  grep -o '^## B[0-9]*' | tail -1` before allocating, **every time**). Expect a
  trivial append-conflict on `4-backlog.md` at merge; **main wins on ids.**

## 7. Host load — binding, a production game server shares this 8-core host

pytest **serial only**, never `-n`/xdist, always `nice -n 19 ionice -c 3 python
-m pytest …`. Targeted files while iterating; the whole suite **at most once
per checkpoint** (generation 9's single full run: **4081 passed, 20 skipped**,
484s). **ONE gate container at a time across all agents**: check BOTH `docker
ps --format '{{.Image}} {{.Names}}'` (no `tester-unified:local`, no
`run-gate-vbpub-*`) AND `pgrep -af tester-unified-gate.sh`, and WAIT rather
than run two. Cap it within seconds of launch: `docker update --cpus=3 $(docker
ps -q --filter ancestor=tester-unified:local)`. **No build/pip/wheel step
concurrent with a suite run** — which also means: do not edit the worktree
while the gate is running, it needs a clean tree untouched for the whole run.

## 8. The gate recipe that has now worked four times

Launch from `/workspaces/vbpub` (the ONLY thing that belongs there), after
committing, with the worktree clean:

```
setsid nohup bash -c '{ bash assay/tools/tester-unified-gate.sh \
  /workspaces/vbpub/.worktrees/assay-wave-d-v10; echo GATE_EXIT=$?; } \
  > <scratchpad>/gate-<tag>.log 2>&1' < /dev/null > /dev/null 2>&1 &
```

Cap immediately (poll for the container in a loop; it appears within ~30s),
then block on `until grep -q 'GATE_EXIT=' <log>; do sleep 30; done` inside a
`Bash` call with `timeout: 600000` — a run is ~25 minutes, so expect the call
to background itself once and to need a second one. **Read the verdict in a
SEPARATE step** (LESSONS L4) and require ALL of: `GATE_EXIT=0`, exactly one
`ASSAY_REGISTERED_GATE_COMPLETE=1`, zero `FAILED|DIRTY_TREE|Traceback`, the
wheel name carrying YOUR commit, and every phase marker present.

## 9. Retention prompt for generation 10 (paste as the `/compact` seed)

> **KEEP.** Branch `feature/assay-wave-d-v10` in
> `/workspaces/vbpub/.worktrees/assay-wave-d-v10`. Tip **`962211cd`** (B050,
> A-436) and its gate is GREEN (`gate-gen9a.log`: `GATE_EXIT=0`, one
> `ASSAY_REGISTERED_GATE_COMPLETE=1`, zero `FAILED|DIRTY_TREE|Traceback`, wheel
> `assay-4.1.1.dev33+g962211cd-py3-none-any.whl`, twelve phases incl.
> `verdict-v10-successors-verified`); beneath it `61b8d836` (B069 tripwire,
> A-435) and the one and only `!` commit `b2fd09f3`. **Two of eight post-cut
> items done; six remain: B051 (BLOCKED) → B052 → B053 `detail` → B004 → B007 →
> CONSUMERS "Migration notes (v9 → v10)".** **B051 is blocked and B052 must not
> jump its slot unasked** — DA-D4 asks `verify` to re-derive `discarded`, and a
> discarded mutant is absent from every bucket (`mutation.py:1967-1969`), from
> `candidate_count` (`:1992-1997` + `verdict.py:1684-1703` forbids
> `candidate_count != total`), and from `lines_without_candidates`
> (`mutation.py:1966-1968`); every bound catching the `9999` case refuses a
> truthful high-discard report. Three routes are in the REPORT; the controller
> picks. Load-bearing seams for what remains:
> `tests/test_verdict_conformance.py`'s `EXCLUDED_ENTIRELY` set (B004 must
> leave it); `canary.py`'s `judge_attempt`/`judge_canary` (B007 needs the
> runner LOOP, DA-R19 `all` does not short-circuit, DA-R20 one shared control
> rejected); `carve-assets/W6/expected/multi-target-r3-v10-template.json` is
> HAND-AUTHORED and must be replaced by real output with the MANIFEST fixed;
> `docs/CONSUMERS.md` already forward-references the migration notes.
> `src/assay/schemas/verdict.schema.json` is BYTE-FROZEN against W6 — do not
> edit it. Next ids **A-437 / B070**, re-checked against `main` every time.
> DA-R21: F015/M7 stay PLANNED. Host rule: serial `nice -n 19 ionice -c 3`
> pytest, whole suite once per checkpoint, ONE gate container, cap at 3 CPUs,
> never edit the worktree during a gate run.
> **DROP.** The B050 implementation narrative (settled, in A-436 and LOG entry
> 30), the B069 regex design details (in A-435 and the test's own docstring),
> the four gate-run transcripts, and generation 8's blast-radius repair story.
