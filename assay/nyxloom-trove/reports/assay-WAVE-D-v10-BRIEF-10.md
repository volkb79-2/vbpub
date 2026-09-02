# Wave D (v10 integrity) — BRIEF-10

**Written by generation 10 at its E-008 checkpoint, for generation 11.**
Branch `feature/assay-wave-d-v10`, worktree
`/workspaces/vbpub/.worktrees/assay-wave-d-v10`.

## 1. Where the branch stands, in one paragraph

**Four of the eight post-cut items are DONE and the registered gate is GREEN
on the fourth.** The tip is the checkpoint commit; beneath it `83c31f18`
(`feat(assay): non-repudiation tier three …`, B052/A-438) — the **gate-verified
commit** — then `5b2730b6` (B051/A-437), `1b127356` (generation 9's
checkpoint), `962211cd` (B050/A-436), `61b8d836` (B069/A-435), and the one and
only `!` commit `b2fd09f3`. Gate log
`<scratchpad>/gate-gen10a.log`: `GATE_EXIT=0` (one), one
`ASSAY_REGISTERED_GATE_COMPLETE=1`, **zero** `FAILED|DIRTY_TREE|Traceback`,
wheel **`assay-4.1.1.dev36+g83c31f18-py3-none-any.whl`**, twelve phases
including `verdict-v10-successors-verified`. **Nothing is blocked.** Four items
remain, in DA-R27's order: B053 `detail` producers → B004
`PROVENANCE_UNVERIFIED` producer → B007 multi-target canary loop → CONSUMERS
"Migration notes (v9 → v10)" as the LAST item.

## 2. Read this, in this order, before touching anything

1. `nyxloom-trove/WAVE-PROMPT-2026-09-02-wave-d-v10-integrity.md`, the
   **"Implementer prompt — generation 1"** section (lines 244-386) — standing
   orders, all still binding: `git -C <worktree>` for every git command,
   **Edit tool never `sed`/python rewrite scripts**, both commit trailers,
   `git commit -F <msgfile> --only -- <paths>`, `decisions.md` append-only,
   `assay/**` only, no push, no merge, A-334, and the **BLOCKED clause**.
   Its "Rulings" section (lines 60-215) carries DA-D1..DA-D16.
2. `reports/assay-WAVE-D-v10-CONTROLLER-LOG.md` on `main` —
   DA-R1..**DA-R27**. DA-R27 fixes the order of what is left. **Read any
   entries the controller has added past DA-R27 first.**
3. `reports/assay-WAVE-D-v10-BRIEF-8.md` §4 — the remaining items' measured
   seams, still accurate except where §4 below supersedes it. BRIEF-9 is now
   history: its blocked-B051 argument was answered by DA-R26 and B051 has
   landed.
4. The branch `-LOG.md` entries **32-34** and the `-REPORT.md`
   **"Generation 10"** section.

## 3. The lesson that still costs gate runs

`pytest tests -q` is green here with **20 tests skipped**, and those twenty are
exactly the harnesses that read a real produced artifact inside
`tester-unified:local`. Every consumer of a wire shape under `gate/python/` is
invisible to a local run.

**Half-automated. Use it.** `tests/test_gate_harness_version_pins.py` (B069)
fires locally on a stale `schema_version` pin or a stale `carve-assets/W<n>`
root. It covers no other field, so: **before your first gate run of a change
that touches a wire shape, `grep gate/python/` for it.** Generations 9 and 10
both did, both went green first time. Generation 10's greps: `discarded` → 0
hits; `ingest|mutation-report-json|stryker` over `gate/` and `tools/` → one
unrelated SQL-corpus comment.

## 4. The work that remains, in DA-R27's order. Each item = one A-row + one commit.

### 4.0 DONE — do not re-land

* **B069** (A-435, `61b8d836`), **B050** (A-436, `962211cd`) — generation 9.
* **B051** (A-437, `5b2730b6`) — DA-R26's route 1. `judgment.r2.discarded` is
  DECLARED-not-verified, said in four places (schema description + the
  byte-identical W6 copy, DESIGN-GUIDE §11, CONSUMERS' ingested-lane section,
  and `verify._check_ingested_r2_agrees_with_its_payload`'s new "what this
  function does NOT check" section). The `9999` reproduction is re-run and
  **deliberately accepted**, recorded in the backlog row and in three tests.
  **B070 filed** as the v11 candidate. B051's boxes are all dispositioned.
* **B052** (A-438, `83c31f18`) — DA-D5's content tier. GATE GREEN on this
  commit.

### 4.1 B053 — `claim.detail`'s PRODUCERS. **NEXT.**

Rulings **DA-D2 (c)** and **A-428**. The field, its 2048-BYTE bound,
`detail_dropped_bytes` and the schema's deliberate two-bound split
(characters in JSON Schema, bytes in the model) all landed in the cut —
`verdict.py:2931` and the `__post_init__` checks at `verdict.py:3055-3084`.
What is missing is the code that COMPOSES a sentence onto a non-PASS claim.
The text must be **byte-copied from what the same conversion site puts on the
diagnostics stream** (`runner.announce_refusal`, B053/A-409, already shipped),
so the line a caller reads and the document a consumer archives can never say
different things. Per CLAIM, not per verdict: one lane can refuse at two tiers
for two reasons. `DESIGN-GUIDE.md` §6's B026 N-4 paragraph is already
corrected where B053 supersedes it. **Truncation keeps the HEAD** (the
opposite end from B014's command tails) and records the dropped byte count.

### 4.2 B004 — `PROVENANCE_UNVERIFIED`'s producer

Ruling **A-430**. The code and the `adjudicated ⇒ verified_by_assay is False`
narrowing are on the wire. **Landing the producer means removing
`("NO_MEASUREMENT", "PROVENANCE_UNVERIFIED")` from
`tests/test_verdict_conformance.py`'s `EXCLUDED_ENTIRELY` set
(`tests/test_verdict_conformance.py:221-227`)** — that set carries a written
obligation, at `:206-216`, saying the producer lands later in this same wave
and that the entry is removed the moment it does. **That is the tripwire.**
The wave prompt also says: re-capture the ciu 7.10.1 provenance assets FIRST
(W2-W7 of the B004 carve; `nyxloom-trove/W2-CARVE-B004-provenance-verified.md`
and `reports/assay-B004-carve-review-fable.md`).

### 4.3 B007 — the multi-target canary LOOP

Ruling **A-432**. `canary.py` already has `judge_attempt(attempt)`
(`canary.py:756`) and `judge_canary(result, *, aggregation=None)`
(`canary.py:785`); `run_isolated_canary` is at `canary.py:421`. The runner
loop that produces more than ONE attempt is what is missing.
`MAX_CANARY_TARGETS = 8` (`verdict.py:444`), enforced at `verdict.py:1360`
(attempts) and `verdict.py:2574` (targets).
**DA-R19: `all` does NOT short-circuit on FAIL — the 2N materialisation bound
is deliberate. DA-R20: one shared control materialisation stays REJECTED.**
When the loop lands, **replace the HAND-AUTHORED
`carve-assets/W6/expected/multi-target-r3-v10-template.json` with real output
and fix the MANIFEST**, whose "The two NEW templates" table says it becomes
real "when B007's multi-target loop lands (later in this same wave)". This is
the ONE authorised exception to W6's freeze.

### 4.4 CONSUMERS.md — "Migration notes (v9 → v10)". **THE LAST ITEM.**

A new section beside `## Adopting a v2-capable release`. It must carry: the
one-element `targets` list for R3 lanes; `judgment.r2.fail_under` for ingested
lanes; v9 verdicts refused by `assay verify`; the new refusals (**B052's
content check**, B054's per-file istanbul disposition); `detail` present on
refusals; the adjudicated provenance lane shape with the `|| true` example;
Python and Go lanes without R3 unchanged; **and that `judgment.r2.discarded`
is declared-not-verified** (DA-R27). Three sections already forward-reference
it and must not be left dangling: `docs/CONSUMERS.md`'s ingested-R2 floor
paragraph ("**B050**, schema v10 — see the migration notes"), the new
content-tier paragraph ("**B052**, this release — see the migration notes"),
and CHANGES.md's v9→v10 block ("The full 'Migration notes (v9 → v10)' section
lands in `docs/CONSUMERS.md` with the rest of this wave").

## 5. Hard invariants — re-verify, do not assume

* **Exactly ONE `!` commit on this branch and it already exists**
  (`b2fd09f3`). If an item feels like it needs a second, stop and ask.
* `LANE_SCHEMA_VERSION` stays **2**; `inventory_schema` stays **1**.
* No rename of `assay.diff` / `assay.git` / `assay.mutation` /
  `assay.adapters.python` — cmru imports those four from a pinned zipapp.
* dstdns reads `outcome`, `status`, `coverage.pct`, `coverage.missing_lines`,
  `coverage.missing_branch_lines` **by name**.
* `carve-assets/W1`..`W5` stay frozen (only
  `W3/expected/dstdns-sql-r2-v6-witness.json` is deliberately live). **W6 is
  frozen too**, with ONE authorised exception left: B007's multi-target
  template (§4.3).
* **`src/assay/schemas/verdict.schema.json` is byte-frozen** against
  `carve-assets/W6/verdict.schema.v10.json` by
  `test_shipped_schema_is_byte_identical_to_the_locked_v10_asset`. Generation
  10 edited both, in one commit, description bytes only, under DA-R26 — and
  the W6 MANIFEST row now records that amendment. **Do not edit either again
  without a ruling that says so.**
* DA-R21: F015/R4's IMPLEMENTATION is not in this wave; M7 stays PLANNED.
* A-334: no test double as evidence about an external system.

## 6. Ids

* Next free A-id: **A-439** (`main` is still at A-407; the branch's high-water
  mark is A-438).
* Next free B-id: **B071** (`main` was at **B068** when generation 10 checked,
  three times; the branch has taken B069 and B070). Re-run
  `git -C /workspaces/vbpub show main:assay/nyxloom-trove/4-backlog.md |
  grep -o '^## B[0-9]*' | tail -1` before allocating, **every time**. Expect a
  trivial append-conflict on `4-backlog.md` at merge; **main wins on ids.**

## 7. Host load — binding, a production game server shares this 8-core host

pytest **serial only**, never `-n`/xdist, always `nice -n 19 ionice -c 3
python -m pytest …`. Targeted files while iterating; the whole suite **at most
once per checkpoint** (generation 10's single full run: **4091 passed, 20
skipped**, 407 s, at load ~6). **ONE gate container at a time across all
agents**: check BOTH `docker ps --format '{{.Image}} {{.Names}}'` (no
`tester-unified:local`, no `run-gate-*`) AND `pgrep -af
tester-unified-gate.sh`, and WAIT rather than run two. Cap within seconds of
launch: `docker update --cpus=3 $(docker ps -q --filter
ancestor=tester-unified:local)` — generation 10's poll loop caught the
container on its first iteration. **Do not edit the worktree while the gate is
running.**

## 8. The gate recipe that has now worked five times

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
the verdict in a SEPARATE step** (LESSONS L4) and require ALL of: `GATE_EXIT=0`,
exactly one `ASSAY_REGISTERED_GATE_COMPLETE=1`, zero
`FAILED|DIRTY_TREE|Traceback`, the wheel name carrying YOUR commit, and the
`verdict-v10-successors-verified` phase.

## 9. Retention prompt for generation 11 (paste as the `/compact` seed)

> **KEEP.** Branch `feature/assay-wave-d-v10` in
> `/workspaces/vbpub/.worktrees/assay-wave-d-v10`. Gate-verified tip
> **`83c31f18`** (B052 content tier, A-438); `gate-gen10a.log` is GREEN
> (`GATE_EXIT=0`, one `ASSAY_REGISTERED_GATE_COMPLETE=1`, zero
> `FAILED|DIRTY_TREE|Traceback`, wheel
> `assay-4.1.1.dev36+g83c31f18-py3-none-any.whl`, twelve phases incl.
> `verdict-v10-successors-verified`). Beneath it `5b2730b6` (B051, A-437),
> `962211cd` (B050, A-436), `61b8d836` (B069, A-435) and the one and only `!`
> commit `b2fd09f3`. **Four of eight post-cut items done; four remain, in
> DA-R27's order: B053 `detail` PRODUCERS → B004 `PROVENANCE_UNVERIFIED`
> producer → B007 multi-target canary loop → CONSUMERS "Migration notes
> (v9 → v10)" LAST. Nothing is blocked.** Load-bearing seams:
> `claim.detail`/`detail_dropped_bytes` are on the wire at `verdict.py:2931`
> and `:3055-3084` with NO producer — the text must be byte-copied from
> `runner.announce_refusal`'s own line, per claim not per verdict, HEAD kept
> on truncation at 2048 BYTES; B004's tripwire is
> `tests/test_verdict_conformance.py:221-227`'s `EXCLUDED_ENTIRELY` set, whose
> written obligation at `:206-216` says the entry leaves the moment the
> producer exists (re-capture the ciu 7.10.1 assets first, W2-W7 of the
> carve); B007 needs the RUNNER LOOP beside `canary.judge_attempt`
> (`canary.py:756`) and `judge_canary` (`:785`), DA-R19 `all` does NOT
> short-circuit and the 2N bound is deliberate, DA-R20 one shared control
> stays rejected, `MAX_CANARY_TARGETS = 8` at `verdict.py:444`, and
> `carve-assets/W6/expected/multi-target-r3-v10-template.json` is HAND-AUTHORED
> and must be replaced by real output with the MANIFEST fixed (the ONE
> authorised W6 exception); the migration notes are forward-referenced from
> THREE places (CONSUMERS' B050 floor paragraph, CONSUMERS' new B052
> content-tier paragraph, CHANGES.md's v9→v10 block) and must also state that
> `discarded` is declared-not-verified. `src/assay/schemas/verdict.schema.json`
> is BYTE-FROZEN against W6 — generation 10's description-only amendment was
> ruled (DA-R26); do not edit either again unruled. Next ids **A-439 / B071**,
> re-checked against `main` every time. DA-R21: F015/M7 stay PLANNED. Host
> rule: serial `nice -n 19 ionice -c 3` pytest, whole suite once per
> checkpoint, ONE gate container, cap at 3 CPUs, never edit the worktree
> during a gate run.
> **DROP.** B051's three-routes argument (settled by DA-R26, recorded in
> A-437 and the backlog row), B052's normalisation-design deliberation
> (settled in A-438 and `_CONTENT_TIER_NORMALISATION`'s own comment), the
> five gate-run transcripts, and generations 8-9's blocked-item narrative.
