# Wave D (v10 integrity) — BRIEF-8

**Written by generation 8 at its E-008 checkpoint, for generation 9.**
Branch `feature/assay-wave-d-v10`, worktree
`/workspaces/vbpub/.worktrees/assay-wave-d-v10`.

## 1. Where the branch stands, in one paragraph

**The v10 cut is DONE and the registered gate is GREEN on it.** The branch's
only `!` commit is **`b2fd09f3`** — `feat(assay)!: verdict schema v9 -> v10 --
the integrity cut` — and its gate run is `GATE_EXIT=0`, one
`ASSAY_REGISTERED_GATE_COMPLETE=1`, zero `FAILED|DIRTY_TREE|Traceback`, wheel
`assay-4.1.1.dev30+gb2fd09f3-py3-none-any.whl`, twelve phases including the new
`ASSAY_GATE_PHASE=verdict-v10-successors-verified`. Beneath it sit `4538bd66`
(A-434) and the generation-8 checkpoint commit. **All six wire changes are on
the wire; NONE of the seven post-cut producer/consumer items has been started.**
That is your work.

## 2. Read this, in this order, before touching anything

1. `nyxloom-trove/WAVE-PROMPT-2026-09-02-wave-d-v10-integrity.md`, IMPLEMENTER
   PROMPT section — your standing orders. Everything in it still binds:
   `git -C <worktree>` for every git command, Edit tool never `sed`, both commit
   trailers, `git commit -F <msg> --only -- <paths>`, `decisions.md`
   append-only, `assay/**` only, no push, no merge, A-334.
2. `nyxloom-trove/reports/assay-WAVE-D-v10-BRIEF-7.md` §3.1 — the cut's own
   spec; you need it to know what already landed so you do not re-land it.
3. This file, §4 onward.
4. `nyxloom-trove/reports/assay-WAVE-D-v10-CONTROLLER-LOG.md` — DA-R1..**DA-R21**.
   **DA-R21 is new and it shortens the wave**: F015/R4's IMPLEMENTATION leaves
   Wave D for the post-v10 plan's E-4. The R4 wire shape stays in the cut. Do
   NOT mark M7 done or F015 proven; generation 8 already wrote the two
   one-line PLANNED notes DA-R21 asked for.
5. The branch `-LOG.md` (entries 26-28) and `-REPORT.md` (the generation-8
   section) for the evidence behind all of the above.

## 3. The lesson that cost this generation two gate runs

`pytest tests/` is green on this branch **with 20 tests skipped**, and those 20
are exactly the harnesses that read a real produced artifact inside the
tester-unified image. Every consumer of a wire shape under `gate/python/` is
therefore **invisible to a local run**. Generation 8's cut went red twice for
exactly this: `qualify_topos.py` (a hardcoded `schema_version != 9` and a
`carve-assets/W5/expected` root) and then `qualify_cmru_b006a.py` (a flat
`canary.control_outcome` that is `canary.attempts[0].control_outcome` now).

**Before your first gate run of any change that touches a wire shape, grep
`gate/python/` for it.** Five seconds against fifty minutes.

## 4. The work, in order. Each item = one A-row + one commit.

Every one of these is fully specified by an existing A-row. **Do not improvise
product calls; do not widen or narrow any wire field beyond the rulings.**

### 4.1 B050 — `judgment.r2.fail_under` becomes a floor that is TAKEN

Ruling: **A-427** (`decisions.md:869`). The wire half already landed.
Remaining:

* `src/assay/mutation.py:2195 judge_mutation` regains a `fail_under` parameter;
  its `survived` branch consults the mutation score against the floor.
* `src/assay/config.py` — **delete** the `if fail_under != 100.0:` load-time
  refusal block (the one whose message says "can only honour 100.0" and names
  B050); **keep** the `0.0 <= fail_under <= 100.0` range check immediately
  above it.
* `src/assay/verify.py:2040 _check_r2_rederivation` — read the floor FROM the
  document (`judgment.r2.fail_under`) rather than assuming 100, so the
  re-derivation stays total.
* `tests/test_config_ingested_mutation.py:213
  test_a_sub_hundred_fail_under_is_refused_naming_the_wire_gap` — replaced by
  its positive counterpart.
* `docs/CONSUMERS.md:1281` — the "**`fail_under` must be `100.0` in this
  release**" paragraph is DROPPED. The four worked lanes at `:251`/`:437`/
  `:813`/`:1260` keep `100.0` and stay legal.
* B050's acceptance witness is the document this build could not produce: a
  verdict that PASSes with recorded survivors and `assay verify`s clean.

**DECISION ASK — settle this before writing the arithmetic.** A-427 says the
parameter was "already written and tested once, so a re-wiring, not new
arithmetic". **It was not.** `git log -S fail_under -- src/assay/mutation.py`
returns exactly one commit (`d0aab6fd`, B046) and that commit only *describes*
the removal in prose. The denominator is still derivable rather than
inventable — the `survived` branch is reached only after `crashed` and
`budget_exceeded` are known empty, and `Mutation.equivalent`'s own docstring
states it is "excluded from the mutation score's denominator" — so
`pct = 100 * len(killed) / (len(killed) + len(survived))`. **Get the controller
to confirm that reading**, and see §4.2: B051's `discarded` is excluded from
the same denominator, so land the two adjacently and say so in both rows.

### 4.2 B051 — `discarded` becomes a LISTED field

Ruling: **DA-D4**. Both derivation sites. Interacts with §4.1's denominator.

### 4.3 B052 — content-tier normalised compare

Ruling: **DA-D5**, via `SnapshotRepository.read_regular_file`.

### 4.4 B053 — `claim.detail`'s PRODUCERS

Rulings: **DA-D2** and **A-428**. The field, its 2048-BYTE bound,
`detail_dropped_bytes` (B014's `*_dropped_bytes` convention) and the schema's
two-bound split all landed in the cut; what is missing is the code that
actually composes a sentence. `DESIGN-GUIDE.md` §6's B026 N-4 paragraph is
already corrected where B053 supersedes it.

### 4.5 B004 — `PROVENANCE_UNVERIFIED`'s producer

Ruling: **A-430**. Both the code and the `adjudicated ⇒ verified_by_assay is
False` narrowing are on the wire and in `assay.errors`; both are currently in
`test_verdict_conformance.py`'s `EXCLUDED_ENTIRELY` set **with a written
obligation** saying no producer exists yet. **Landing the producer means
removing it from that set** — that is the tripwire, do not miss it.

### 4.6 B007 — the multi-target canary LOOP

Ruling: **A-432**. `judgment.r3.targets` (1..8, ordered, unique),
`aggregation` (`any`/`all`, required iff >1 target, forbidden with 1),
`CanaryAttempt` with its `disposition` fork and the closed
`not_attempted_reason` vocabulary (`short_circuited`, `budget_exhausted`,
`earlier_target_terminal`) are all on the wire. `canary.py` has
`judge_attempt(attempt)` and `judge_canary(result, *, aggregation=None)`
already; what is missing is the runner loop that produces more than one
attempt. **DA-R19 binds: `all` does NOT short-circuit on FAIL, and the 2N
bound is deliberate. DA-R20 binds: one shared control materialisation stays
REJECTED.** When the loop lands, replace
`carve-assets/W6/expected/multi-target-r3-v10-template.json` (currently
HAND-AUTHORED, and the MANIFEST says so) with its real output.

### 4.7 CONSUMERS.md — "Migration notes (v9 → v10)"

A new section beside `## Adopting a v2-capable release`
(`docs/CONSUMERS.md:1631`). This is the LAST item; `CHANGES.md`'s
`[Unreleased]` breaking entry currently stands on its own without forward-
referencing it.

## 5. Hard invariants — re-verify, do not assume

* `LANE_SCHEMA_VERSION` stays **2**; `inventory_schema` stays **1**.
* No renames of `assay.diff` / `assay.git` / `assay.mutation` /
  `assay.adapters.python` — cmru imports those four from a pinned 4.1.0
  zipapp.
* dstdns reads `outcome`, `status`, `coverage.pct`, `coverage.missing_lines`,
  `coverage.missing_branch_lines` **by name**. They stay.
* **Exactly ONE `!` commit on this branch and it already exists** (`b2fd09f3`).
  Nothing you write may add another; if a post-cut item feels like it needs
  one, that is a signal to stop and ask, not to add it.
* `carve-assets/W1`..`W5` stay frozen and unedited. Only `W3/expected/
  dstdns-sql-r2-v6-witness.json` is deliberately live.

## 6. Ids

* Next free A-id: **A-435** (`main` is still at A-407; the branch's high-water
  mark is A-434).
* Next free B-id: **B069** (`main` gained B065-B068 during generation 8;
  re-run `git show main:assay/nyxloom-trove/4-backlog.md | grep -o '^## B[0-9]*'
  | tail -1` before allocating, every time). Expect a trivial append-conflict
  on `4-backlog.md` at merge; **main wins on ids.**

## 7. Host load — binding, a production game server shares this 8-core host

pytest **serial only**, never `-n`/xdist, always
`nice -n 19 ionice -c 3 python -m pytest …`. Targeted test files while working
a blast radius; the whole suite at most once per checkpoint. **ONE gate
container at a time across all agents** — check `docker ps --format
'{{.Image}}'` for `tester-unified:local` and `pgrep -af tester-unified-gate.sh`
and WAIT rather than run two. Cap it the moment it appears:
`docker update --cpus=3 $(docker ps -q --filter ancestor=tester-unified:local)`.
No build/pip/wheel step concurrent with a suite run.

## 8. The gate recipe that worked three times

Launch from `/workspaces/vbpub` (the ONLY thing that belongs there), after
committing, with the worktree clean and untouched for the whole run:

```
setsid nohup bash -c '{ bash assay/tools/tester-unified-gate.sh \
  /workspaces/vbpub/.worktrees/assay-wave-d-v10; echo GATE_EXIT=$?; } \
  > <scratchpad>/gate-<tag>.log 2>&1' < /dev/null > /dev/null 2>&1 &
```

Cap immediately, then block on
`until grep -q 'GATE_EXIT=' <log>; do sleep 30; done` (foreground `sleep` alone
is blocked; a `Bash` call with `timeout: 600000` around the loop works and
backgrounds itself cleanly if it overruns). **Read the verdict in a SEPARATE
step** (LESSONS L4) and require ALL of: `GATE_EXIT=0`, exactly one
`ASSAY_REGISTERED_GATE_COMPLETE=1`, zero `FAILED|DIRTY_TREE|Traceback`, the
wheel name carrying your commit, and every phase marker present. A run is
~25 minutes.

## 9. Retention prompt for generation 9 (paste as the `/compact` seed)

> **KEEP.** Branch `feature/assay-wave-d-v10` in
> `/workspaces/vbpub/.worktrees/assay-wave-d-v10`; the v10 cut is `b2fd09f3`
> and its gate is GREEN (`GATE_EXIT=0`, one `ASSAY_REGISTERED_GATE_COMPLETE=1`,
> zero `FAILED|DIRTY_TREE|Traceback`, wheel
> `assay-4.1.1.dev30+gb2fd09f3-py3-none-any.whl`, phase
> `verdict-v10-successors-verified` present). Exactly one `!` commit exists and
> it is that one. Six wire changes are ON the wire (A-427 `judgment.r2.fail_under`,
> A-428 `claim.detail`/`detail_dropped_bytes`, A-430 `PROVENANCE_UNVERIFIED` +
> the `adjudicated ⇒ verified_by_assay: false` narrowing, A-432 canary
> `targets`/`aggregation`/`attempts`, A-433+A-434 `R4`/`red_first`/
> `RED_FIRST_UNPROVEN`). ZERO producers exist for any of them. Seven items
> remain, in order: B050 → B051 → B052 → B053 `detail` → B004 → B007 →
> CONSUMERS "Migration notes (v9 → v10)", each one A-row + one commit.
> Load-bearing seams: `config.py`'s `if fail_under != 100.0:` block to delete
> and the range check above it to keep; `mutation.py:2195 judge_mutation`;
> `verify.py:2040 _check_r2_rederivation`;
> `tests/test_config_ingested_mutation.py:213`; `docs/CONSUMERS.md:1281`
> (drop) and `:1631` (the migration-notes anchor);
> `test_verdict_conformance.py`'s `EXCLUDED_ENTIRELY` set (B004 must leave it).
> Next ids A-435 / B069, re-checked against `main` every time. DA-R21: F015/M7
> stay PLANNED, implementation moved to the post-v10 plan's E-4. Host rule:
> serial nice/ionice pytest, ONE gate container, cap at 3 CPUs.
> **DROP.** The three gate-run transcripts (their verdicts are in LOG entry 27),
> the 117-failure repair narrative from generation 8's blast-radius work, and
> the A-434 argument (settled, in `decisions.md`).
