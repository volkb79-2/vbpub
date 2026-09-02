# assay Wave D (v10) — BRIEF-5 (generation 5 → generation 6)

Written at generation 5's E-008 checkpoint. **Cumulative delta since BRIEF-4
only.** Read BRIEF-1 (the seam map), BRIEF-2, BRIEF-3, BRIEF-4 (especially
**§5, the phase-2 seam table, which is still your map**), then this.

**The headline: R-1 round 1's fix package is LANDED and GATE-GREEN.
FIX-TIP `e3ae8ada`. Phase 2 has not started, and it is your whole job.**

---

## 1. Where the branch stands

- Worktree `/workspaces/vbpub/.worktrees/assay-wave-d-v10`, branch
  `feature/assay-wave-d-v10`, forked from `main` at `a4a865da`.
- **Tip:** this brief's own commit. **Gate-verified commit: `e3ae8ada`.**
- **Phase 1: 10/10 DONE. R-1 round 1's fix package: DONE.**
- **Phase 2 and phase 3: NOT STARTED.** Nothing under `verdict.py`,
  `verify.py`, `src/assay/schemas/` or the drift-guard carve-assets has been
  modified on this branch, no commit carries `!`, and both v9 schema gate
  phases passed again on `e3ae8ada`. The branch is still releasable on v9.

| commit | what |
|---|---|
| `e44c1056` | R-1's FINAL report (839 lines), verbatim, replacing the 756-line interim |
| `8895ffbf` | the fix package — A-418..A-424 |
| `e3ae8ada` | **B063** filed (not fixed) — **THE GATE-VERIFIED FIX-TIP** |
| this one | records: LOG, REPORT, BRIEF-5 |

## 2. What generation 5 landed (details: LOG entries 15–18, REPORT "Generation 5")

| ruling | A-row | what |
|---|---|---|
| DA-R8 (BLOCKER 1) | A-418 | both POST-command dirt/HEAD guards announce, both dispatch paths, sentence blames the lane's OWN command |
| — (BLOCKER 2) | A-419 | the signature-only B029 test replaced by a value assertion, proven red four ways |
| DA-R9 (SF-1) | A-420 | `LANE_TIMEOUT`-scoped handlers in `cli._run_reserved` |
| — (SF-2) | A-421 | the silent `OSError` → `GIT_FAILED` says what failed |
| — (SF-3) | A-422 | `_report_probe_refusal` folded onto the one emitter |
| — (SF-4) | A-423 | `try`/`finally` around the two per-mutant reservation reads |
| — (SF-5) | A-424 | the `statement_attribution` carries documented as insurance |

**Three things you must not re-derive:**

1. **DA-R9's contingency fired, and the answer is in the code.** The `0.001s`
   deadline escapes at `cli.py`'s OWN `git.head_rev`, not inside `run_lane` —
   stack in the REPORT. The unavailable field is `Verdict.commit`, and it is
   **read** (an unbounded `head_rev`, on that refusal path only) rather than
   fabricated. **That is decision ask 1 and the controller has not ruled it.**
   Do not change it on your own judgement; if the controller rules against it,
   the revert is one `except` block and two test parametrisations.
2. **The three "no exceptions" claims are now TRUE** and stay as written
   (`CHANGES.md`, `test_refusal_announcement.py`'s DA-R3 section,
   `runner.py`'s DA-R3 comment). If phase 2 adds a refusal site, it owes the
   same one line through `runner.announce_refusal` — that is now a standing
   contract with a per-reason-code counting test behind it.
3. **`runner.post_command_refusal` (`runner.py:352`) is the ONE composer** for
   both post-command guards; `SnapshotUnitResult` gained `post_dirty` /
   `post_observed_head`. If phase 2 touches `_execute_snapshot_unit` or
   `_finish_direct_r0_lane` (B052's ingest block is next door), keep both.

## 3. GENERATION 6's WORK — phase 2, in the wave prompt's order

Nothing before it is outstanding. The literal sequence, from the wave prompt's
Sequencing plus DA-D4..DA-D8 and **DA-R12**:

1. **Design A-rows FIRST, before any schema edit** (A-425 onward): B050's
   field (DA-D6), B053's `detail` (DA-D2 c), B004's reason code + the §5.4
   narrowing (DA-D7 as narrowed by **DA-R12**), B007's
   `targets`/`aggregation`/per-attempt payload (DA-D8), F015's claim shape
   (DA-D9). **No wire change may exist without its A-row first.**
2. **THE SINGLE `feat(assay)!:` CUT**, one commit, carrying all of:
   `VERDICT_SCHEMA_VERSION = 10` (hard cut, A-138/A-170 — `assay verify`
   refuses v9 exactly as v9 refused v8), every new field in
   `src/assay/schemas/verdict.schema.json`, the dataclass, **and `verify.py`
   (the third place — the 2.4.0 lesson)**, plus the new drift guard
   `nyxloom-trove/carve-assets/W6/verdict.schema.v10.json` +
   `test_acceptance_v10.py` + `expected/` with **W5 kept as history**.
   `LANE_SCHEMA_VERSION` stays **2**; `inventory_schema` stays **1**; no
   renames of `assay.diff` / `assay.git` / `assay.mutation` /
   `assay.adapters.python` (cmru imports those four by name).
3. Then, in order: **B050** (DA-D6) → **B051** (DA-D4) → **B052** (DA-D5) →
   **B053 `detail`** (DA-D2 c) → **B004** (DA-D7 + DA-R12) → **B007**
   (DA-D8) → the CONSUMERS "Migration notes (v9 → v10)" section.
4. Gate after each coherent step, at most as often as §6's throttle allows.

**DA-R12, verbatim in effect (ruled after BRIEF-4 asked):** B004's
`schema_version` **accepts the integer set `{1, 2}` through ONE parser**;
anything else (the integer 3, the string `"2"`, absent) is refused with a
message naming the accepted values AND the observed one. Tests: one per frozen
asset, plus the three refusals. W2 §5.4 is narrowed accordingly under DA-D7.
**`overall` is still `mismatch` on this host, so the green path keeps
`ciu-provenance-green-reference.json` as its only witness — say so in the
A-row.** The wave prompt's "`unlabelled` is new in schema 2" phrasing is
**wrong by measurement** (BRIEF-4 §4) and is corrected, not followed.

## 4. B004's ciu assets — RE-CAPTURED, do not redo (BRIEF-4 §4 stands)

`nyxloom-trove/carve-assets/W2/ciu-provenance-live-mismatch-ciu-7.10.1.json`,
sha256 `e7fa23dab5cc5e08e2d8156c82a16c2f4ed2742c9b9657805c96508ba68765af`,
3512 bytes, ciu 7.10.1, exit 2, stderr empty. **The ONLY schema-relevant delta
against the frozen 6.0.3/schema-1 asset is the integer `schema_version` 1 → 2**
— keys, container count (20), status vocabulary (16 `unlabelled` + 4
`mismatch` in BOTH) and `overall` are identical. The MANIFEST addendum has the
per-container table.

## 5. Phase-2 seams — BRIEF-4 §5's table is unchanged and is your map

Every row of it still holds; nothing generation 5 touched moved any of them.
Re-read it rather than re-deriving. The two rows to have in mind first:

- **B050's load-time refusal to DELETE:** `src/assay/config.py:2483-2512`
  (the `if fail_under != 100.0` raise). The range check at `:2478` STAYS.
- **W5's four-part shape is W6's template:**
  `nyxloom-trove/carve-assets/W5/` = `verdict.schema.v9.json` +
  `test_acceptance_v9.py` + `expected/` (7 `*-v9-template.json`) +
  `MANIFEST.md`; the gate phase that runs it is
  `tools/tester-unified-gate.sh:557-569`.

One addition from generation 5, for B052's ingest work: `_run_prepared_lane`
now announces the post-command refusal at the top of its `post_reason` branch,
and `_execute_snapshot_unit` keeps `post_dirty`/`post_observed_head`. B052's
content check lands in `_ingest_r2_report`, downstream of both.

## 6. Gate state, and the throttle (BRIEF-4 §6, unchanged, plus what worked)

**GATE-VERIFIED COMMIT: `e3ae8ada`.** One run, first try, green:
`COMPLETE_MARKERS=1`, `GATE_EXIT=0`, `BAD=0`, wheel
`assay-4.1.1.dev20+ge3ae8ada-py3-none-any.whl` (size 530841, sha256
`55a5bee13489546a7ff7471d1f4031d2cc0abc1bfe5c7de062900ca095dfb976`),
`tester-unified: PASS (exit 0)` at
`commit: e3ae8ada1c4b00364aa9c3e8e320ea7ee9a40e45`, twelve phases ending
`ASSAY_GATE_PHASE=pyflakes-clean`. Full transcript in the LOG.

**The launch recipe that worked, end to end — copy it:**

```
# 1. check, by IMAGE (names are random) and by argv
docker ps --format '{{.ID}} {{.Image}}' | grep -i tester-unified   # must be empty
pgrep -af 'tester-unified-gate.sh'                                 # must be empty
# 2. commit first; the worktree stays UNTOUCHED for the whole run
# 3. launch from /workspaces/vbpub, one command, nothing before the setsid
setsid nohup bash -c '{ bash assay/tools/tester-unified-gate.sh <worktree>; \
  echo GATE_EXIT=$?; } > <log> 2>&1' < /dev/null > /dev/null 2>&1 &
# 4. IMMEDIATELY
docker update --cpus=3 $(docker ps -q --filter ancestor=tester-unified:local)
# 5. arm Monitor: until grep -q 'GATE_EXIT=' <log>; do sleep 30; done
# 6. read the verdict in a SEPARATE step
```

Throttle rules, unchanged and binding: never `pytest -n`/xdist (serial,
`nice -n 19 ionice -c 3`, targeted files, whole suite at most once per
checkpoint); never two gate containers; no build/pip/wheel step concurrent
with a suite run. Host load stayed 5–6 through generation 5's whole run.

**A foreground `sleep` is blocked and polling does not advance the wall
clock** — `Monitor` with the `until` loop is the only thing that works.

## 7. Next free ids (re-checked against `main`, which MOVED again)

```
$ git -C <worktree> show main:assay/nyxloom-trove/decisions.md | grep -o '^| A-[0-9]*' | tail -1
| A-407
$ git -C <worktree> show main:assay/nyxloom-trove/4-backlog.md  | grep -o '^## B[0-9]*'  | tail -1
## B061
$ git -C <worktree> rev-parse --short main
c35baa9e
```

`main` moved `72bc041f` → `c35baa9e`; assay's two ledgers are untouched on it.
Generation 5 allocated **A-418..A-424** and **B063**. **Next free: A-425,
B064.** Re-run both commands before allocating — `main` has moved in every
generation of this wave, twice in one of them.

## 8. Rules (BRIEF-4 §8, unchanged, plus what generation 5 confirmed)

- File edits through the Edit tool, never `sed`/python rewrite scripts.
- **Never a bare `git stash`.** Red-prove in a **detached scratch worktree**
  (`git worktree add --detach <scratch> HEAD`, copy the new tests in, mutate
  the copy with Edit, run, `git worktree remove --force`). Generation 5 used
  it four times and it is clean and fast.
- **Run every git command from the worktree** (`git -C <worktree> …`), never
  after `cd /workspaces/vbpub`. The only thing that belongs in
  `/workspaces/vbpub` is the gate launch.
- `git commit -F <msgfile> --only -- <paths>` with BOTH trailers; new files
  `git add`ed first.
- **Exactly ONE `!` commit on the branch: the v10 cut.** Nothing before it.
- Commit BEFORE you gate, and leave the worktree untouched for the WHOLE run.
- Read the gate verdict in a SEPARATE step.
- A-334: no test double as evidence about an EXTERNAL system. (Stubbing
  assay's own function to reach an unraceable internal seam is not that —
  generation 5 did it once, said so, and named the alternative.)
- `decisions.md` is APPEND-ONLY. Touch ONLY `assay/**`.
- **One owner at a time on this worktree.** R-1's probe worktrees are not
  yours; R-1 round 2 runs its own gate, so check before launching.

## 9. Retention prompt for generation 6 (self-authored)

> **KEEP:** the branch/worktree identity and that the **gate-verified commit
> and FIX-TIP is `e3ae8ada`** (R-1 round 2 reviews `93188912..e3ae8ada`); that
> **phase 1 AND R-1 round 1's fix package are DONE** (A-408..A-424 allocated,
> B062/B063 filed) and **phase 2 is the whole remaining job**; §3's literal
> sequence — design A-rows FIRST, then the SINGLE `feat(assay)!:` cut carrying
> schema + dataclass + **verify.py** + the W6 drift guard with W5 kept, then
> B050 → B051 → B052 → B053 `detail` → B004 → B007 → migration notes; that
> **`LANE_SCHEMA_VERSION` stays 2, `inventory_schema` stays 1, and the four
> cmru-imported module paths must not be renamed**; **DA-R12** (`{1, 2}`
> through one parser, refusals naming accepted-and-observed, the green path's
> only witness is `ciu-provenance-green-reference.json`); **§4's ciu
> re-capture is DONE and the only delta is the integer 1 → 2**; **BRIEF-4 §5's
> seam table verbatim**, especially `config.py:2483-2512` as B050's refusal to
> delete and W5's four-part shape as W6's template; §6's launch recipe and
> throttle; §7's ids (next free A-425, B064) and that `main` moves; §8's
> rules; and **the one open decision ask — SF-1's unbounded commit-label read
> in `cli._run_reserved`**, which the controller has not ruled and which you
> must not change on your own judgement.
>
> **DROP:** the reading trail behind phase 1 and the fix package (the REPORT
> has every transcript and conclusion); R-1's round-1 report in full (its
> blockers are all resolved; the LOG names which seam is which); the four
> red-proof transcripts; the per-container detail of the ciu documents; the
> docs-wording debates from generations 2–4.
>
> **DO NOT** write the `feat(assay)!:` cut before every wire change of step 1
> exists as an A-row; do not bump the schema twice; do not re-open a settled
> phase-1 item or a landed fix; do not decide the SF-1 commit-label question
> on silence; do not run two gate containers or an xdist pytest; do not build
> B020, B023, B001's residual, B010's orchestration half, B048's judge verb,
> Go R2/R3, or an `assay canary qualify` document kind.
