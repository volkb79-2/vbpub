# assay Wave D (v10) — BRIEF-4 (generation 4 → generation 5)

Written at generation 4's E-008 checkpoint, on the controller's explicit
instruction to cut here. **Cumulative delta since BRIEF-3 only.** Read
BRIEF-1 (the seam map), then BRIEF-2, then BRIEF-3, then this.

**The headline: phase 1 is complete AND B024 is now landed and gate-green on
`7c9e8dd1` — but R-1's round 1 on the phase-1 tip came back NOT ACCEPT, and
the controller has ruled that its fix package lands on this branch BEFORE the
v10 cut. That package is generation 5's FIRST work item. Phase 2 has not
started.**

---

## 1. Where the branch stands

- Worktree `/workspaces/vbpub/.worktrees/assay-wave-d-v10`, branch
  `feature/assay-wave-d-v10`, forked from `main` at `a4a865da`.
- **Tip:** this brief's own commit. **Gate-verified commit: `7c9e8dd1`.**
- **Phase 1: 10 of 10 DONE**, B024 included — the one item BRIEF-3 left
  blocked.
- **Phase 2 and phase 3: NOT STARTED.** Nothing under `verdict.py`,
  `verify.py`, `src/assay/schemas/` or the drift-guard carve-assets has been
  modified on this branch, and no commit carries `!`. Both v9 schema gate
  phases passed again on `7c9e8dd1`, so the branch is still releasable on v9.

| # | item | ruling | status |
|---|---|---|---|
| 1 | B049 | DA-D1 | DONE (gen 1) — `3b2b8e62`, A-408 |
| 2 | B054 | DA-D3 + DA-R2 (held by DA-R10) | DONE (gen 2) — `c37ca3fb`, A-410 |
| 3 | B053 (a)+(b) | DA-D2 + DA-R1 | DONE (gen 2) — `440d5da9`, A-409 |
| 3b | B053 follow-ups | DA-R3 + DA-R4 | DONE (gen 3) — `21bdf19d`, A-414 |
| 4 | B028 | DA-D10 | DONE (gen 3) — `dd8f4d2c`, A-415 |
| 5 | B029 | DA-D11 → DA-R6 | DONE (gen 3) — `81228b25`, A-416 |
| 6 | B060 | DA-D14 | DONE (gen 2) — `c80b3452`, A-411 |
| 7 | B056 | DA-D13 | DONE (gen 2) — `c80b3452`, A-412 |
| 8 | **B024** | DA-D15 → **DA-R7** | **DONE (gen 4) — `7c9e8dd1`, A-417** |
| 9 | B055 | DA-D12 | DONE (gen 2) — `c80b3452`, A-413 |
| 10 | B009 | DA-D16 | DONE (gen 2) — `c80b3452` |

## 2. What generation 4 landed (details: LOG entry 13, REPORT "Generation 4")

**`7c9e8dd1` — B024, A-417 (DA-R7).** The registered gate lints its own
source. pyflakes gets its **own** hash-bound offline closure in a **third**
venv:

- `gate/distribution/lint-requirements.txt` — one line, `pyflakes==3.4.0
  --hash=sha256:f742a7db…`.
- `gate/distribution/lint-wheelhouse/pyflakes-3.4.0-py2.py3-none-any.whl` —
  63551 bytes, sha256
  `f742a7dbd0d9cb9ea41e9a24a918996e8170c799fa528688d40dd582c8265f4f`,
  fetched ONCE here (`pip download pyflakes --no-deps`) and committed.
- `gate/distribution/lint-wheelhouse-manifest.json` — same digest and size,
  same shape as `build-wheelhouse-manifest.json`.
- `tools/tester-unified-gate.sh:84` `build_lint_venv`, `:117`
  `run_lint_phase`, `:121` `ASSAY_GATE_PHASE=pyflakes-clean`, called at
  `:623-624` after `run_independent_witness`.
- Six new tests in `tests/test_distribution_gate.py` (`:577`, `:604`,
  `:630`, `:641`, `:671`, `:695`); the `gate_functions` fixture's
  `/opt/tester-venv/bin/python` occurrence count went 3 → **4**.

**Scope is `src/assay` only, by measurement:** `src/assay` 0 findings,
`gate/` 0 findings, `tests/` **31 findings across 19 modules** plus a
deliberately unparseable fixture. Filed as **B062**.

**Also landed in the checkpoint commit** (records only): R-1's round-1 report
verbatim; the re-captured ciu asset (§4).

## 3. GENERATION 5's FIRST WORK ITEM — R-1 round 1's fix package

**Read `nyxloom-trove/reports/assay-WAVE-D-v10-REVIEW-R1-round1.md` FIRST
(it is now in the repo, verbatim) — sections BLOCKERS, SHOULD-FIX, DECISION
ASKS carry the full prescriptions and probe transcripts.** R-1's verdict on
`93188912` is **NOT ACCEPT — 2 blockers, 5 should-fixes**. The controller
ruled the package lands on THIS branch BEFORE the v10 cut. Ids: **A-418
onward**; **B062** for the filing; **B063** next free after that.

Literal task list, in the controller's own order:

1. **~~(a) copy R-1's report into the repo~~ — DONE by generation 4**, at
   `nyxloom-trove/reports/assay-WAVE-D-v10-REVIEW-R1-round1.md`.
2. **(b) BLOCKER 1 — DA-R8, fix in scope, no narrowing.** The two
   POST-command dirt/HEAD guards still refuse SILENTLY on both dispatch
   paths, so the branch's three "no exceptions" claims are false:
   - `_finish_direct_r0_lane` — `runner.py:4618-4642`, has **no
     `diagnostics` parameter**. Give it one; announce before the terminal
     `assemble_verdict`.
   - `_execute_snapshot_unit` — `runner.py:2340-2344` → `:2809-2827`. Carry
     `post_dirty` / `post_observed_head` on `_PreparedUnit` **beside the
     existing `post_reason`**, and announce in `_run_prepared_lane`, where
     `diagnostics` is already in scope.
   - **The sentence must blame the lane's OWN command** and name the paths /
     the two revisions. "commit or stash" is the WRONG remedy here (the user
     did not leave the dirt; the command did).
   - Two CLI-level tests, one per dispatch path, in
     `test_refusal_announcement.py`'s existing shape. R-1's `probe53b` shape
     is the reproduction: the lane command writes `leftover.txt`, or makes an
     empty commit.
   - The three claims stay as written and BECOME TRUE: `CHANGES.md:32`,
     `test_refusal_announcement.py:384`, `runner.py:3813-3819`.
3. **(c) BLOCKER 2.** `test_r3_canary_sees_infrastructure.py:139` is
   signature-only and **survives deleting both forwards** (R-1 measured it).
   Replace with a VALUE assertion: call `runner.execute_command` and
   `canary.run_python_canary` directly on a lane declaring `derived:` with a
   real `infrastructure_source`, and assert the resolved fact REACHES the
   command (`env_effective`, or the command observes it). **Prove it red by
   deleting the two forwards and record that red run in the REPORT.**
4. **(d) SF-1 — DA-R9, option (a).** One `except AssayError` scoped to
   `ReasonCode.LANE_TIMEOUT` around `runner.run_lane` in `cli._run_reserved`,
   modelled on the attestation-timeout handler at `cli.py:695-716`; verdict
   via `runner.refuse_lane`, WRITTEN. One test per path with `budget =
   "0.001s"` asserting the verdict file exists with
   `BUDGET_EXCEEDED`/`LANE_TIMEOUT`. If `refuse_lane` needs a fact
   unavailable before `git.repo_top`, carry what is known and **record which
   field in the REPORT**.
5. **(e) SF-2.** `runner.py:3944-3950` `except OSError` → compose
   `AssayError(f"snapshot preparation or cleanup failed: {exc}", ERROR,
   GIT_FAILED)` and announce it. (A third bare silent refusal.)
6. **(f) SF-3.** Fold `_report_probe_refusal` (`runner.py:447-451`) onto
   `announce_refusal`, keeping the probe stderr/stdout tails as indented
   context lines in the shape of `runner.py:3117-3125`.
7. **(g) SF-4.** `try`/`finally` around the two reservation reads at
   `mutation.py:1642-1651` (a leaked descriptor on B049's new raise path).
8. **(h) SF-5.** KEEP the `contradictory_branch_lines` carries
   (`statement_attribution.py:225`, `:344`) but state in their docstring AND
   in the REPORT that **no real artifact reaches them today**.
9. **(i) File B062** in `4-backlog.md`: three test modules
   (`test_python_qualification`, `test_runner_snapshot_selection`,
   `test_distribution_build_release`) run `git -C PROJECT_ROOT.parent` and
   fail on any copy of the tree outside the vbpub checkout — R-1's
   measurement, **not fixed this wave**. **NOTE:** generation 4 already used
   **B062** for the `tests/` pyflakes sweep. **Check `4-backlog.md` before
   allocating and use B063 for R-1's filing** (and say so in the LOG) — this
   is exactly the id-collision the wave has already hit twice.
10. **Rulings that need no work:** **DA-R10** — DA-D3 (B054, no
    `excluded_files` wire field) HELD as written. **DA-R11** — DA-R5 stands.
11. **Then re-run the registered gate on the fix tip** (§6's throttle rules
    apply: ONE container, check `docker ps | grep tester-unified` is empty
    first, `docker update --cpus=3` right after launch), verify markers,
    record, and **NAME THE FIX-TIP SHA in the LOG and the next BRIEF** — R-1
    round 2 reviews `93188912..<that sha>` only, while phase 2 proceeds
    behind it.

R-1 also left a strictly serial, niced, targeted pytest rerun script that
starts when generation 4's gate container exits. It is **host** pytest, not a
container, and is allowed to coexist with generation 5's next gate.

## 4. B004's ciu assets are RE-CAPTURED — done, with a correction to the ruling

DA-D7's re-capture is **DONE** (it needed a live ciu and a running dstdns
instance, both available at the time). New frozen asset:
`nyxloom-trove/carve-assets/W2/ciu-provenance-live-mismatch-ciu-7.10.1.json`,
sha256 `e7fa23dab5cc5e08e2d8156c82a16c2f4ed2742c9b9657805c96508ba68765af`,
3512 bytes, from `ciu 7.10.1`, exit 2, stderr empty. Full delta table in that
directory's `MANIFEST.md` addendum and in the REPORT.

**Do not re-derive this. The measured delta is much smaller than DA-D7
assumes:**

- Top-level keys **identical**; per-container keys **identical**; 20
  containers in both; **16 `unlabelled` + 4 `mismatch` in BOTH**; `overall`
  `mismatch` in both.
- **The ONLY schema-relevant change is the integer `schema_version` 1 → 2.**
- **`unlabelled` is NOT new in schema 2.** The frozen 6.0.3 / schema-1 asset
  already carries sixteen of them. The wave prompt's phrasing implies
  otherwise; it is wrong, and the adjudicator's status vocabulary needs no
  widening for the observed set.
- Everything else that moved is a fact about dstdns/vendor images, not about
  ciu's document shape.

**Open decision ask (in the REPORT, for the controller):** W2 §5.4 refuses
any `schema_version` that is not the integer `1`. Should the adjudicator
accept `{1, 2}`, accept `2` only (a hard cut, at the cost of ciu 6.x hosts),
or take the accepted version from the lane declaration? Generation 4 did not
decide it (A-334). **`overall` is still pinned at `mismatch` on this host**,
so the green-path oracle still has no live-host witness and
`ciu-provenance-green-reference.json` remains the only real `verified-match`
document.

## 5. Phase-2 seams generation 4 located — do not re-derive these

Read-only reconnaissance done while the gate ran. This is a map, not a
design; the A-rows are still generation 5's to write.

| what | where |
|---|---|
| **The three places a wire field must land** | `src/assay/verdict.py:245` `VERDICT_SCHEMA_VERSION = 9` (+ the dataclass at `:3110`, `__post_init__` refusal at `:3126`); `src/assay/schemas/verdict.schema.json` (the ONLY schema file); `src/assay/verify.py` (imports it at `:95`, refuses a foreign version at `:2115-2128`) |
| Re-exports | `src/assay/__init__.py:32,57` |
| **B050's load-time refusal to DELETE** | `src/assay/config.py:2483-2512` — the `if fail_under != 100.0` raise, whose own message says "track B050 for the wire field a lower floor needs". The range check at `:2478` STAYS. `_MUTATION_INGESTED_FIELDS` at `:362` already lists `fail_under` |
| B050: where the floor must be taken | `src/assay/mutation.py:2184` `judge_mutation` |
| B050: the CONSUMERS paragraph to drop | `docs/CONSUMERS.md:1271` "**`fail_under` must be `100.0` in this release.**" (worked lanes at `:251`, `:427`, `:803`, `:1250` declare `100.0`) |
| **B051's two derivation sites** | `src/assay/mutation.py:1864` `ingest_mutation_report`; `src/assay/verify.py:826` `_check_ingested_r2_agrees_with_its_payload` (called at `:2158`). Today's `discarded` handling: `verify.py:964-972` (required, non-bool int, ≥ 0), doc'd at `:854`, read at `:1534` |
| **B052's read seam** | `src/assay/isolation.py:449` `SnapshotRepository.read_regular_file`; the ingest block is `runner.py:3468` `_ingest_r2_report`, called at `:3178`. Existing precedent for reading committed bytes through the snapshot: `canary.py:511`, `runner.py:2435` |
| **B007's declaration seam** | `src/assay/config.py:530` `CanaryConfig`; the R3 required-key set at `:240`; **the B005 rule DA-D8 wants generalised already exists for the single-target form at `config.py:1240-1257`** ("canary target is itself one of `targets`") |
| **The drift-guard shape to replicate as W6** | `nyxloom-trove/carve-assets/W5/` = `verdict.schema.v9.json` + `test_acceptance_v9.py` + `expected/` (7 `*-v9-template.json` files) + `MANIFEST.md`. The gate phase that runs it: `tools/tester-unified-gate.sh:557-569` → `ASSAY_GATE_PHASE=verdict-v9-successors-verified`. The hard-cut guard phase is `:512-517` → `verdict-v6-v7-v8-hard-cut-verified` (18 frozen templates) |
| Where "Migration notes (v9 → v10)" goes | `docs/CONSUMERS.md` — its last sections are `## What is not shipped` (`:2023`) and `## Go lanes…` (`:2031`); `## Adopting a v2-capable release` (`:1621`) is the precedent for a migration section's shape |

## 6. Gate state, and the HOST LOAD THROTTLE (binding, inherited)

**GATE-VERIFIED COMMIT: `7c9e8dd1`.** One run, first try, green:
`COMPLETE_MARKERS=1`, `GATE_EXIT=0`, `BAD=0`, wheel
`assay-4.1.1.dev16+g7c9e8dd1-py3-none-any.whl`
(sha256 `cc1116d7591e3f5f7c35bee40ed5c5e439f5453c3070dfed48edb4c881c147b4`),
`tester-unified: PASS (exit 0)` at
`commit: 7c9e8dd142cfb8c6057655218846fa3aed680c5c`, thirteen phases ending
`ASSAY_GATE_PHASE=pyflakes-clean`. Full transcript in the LOG and REPORT. The
tip is one docs-only commit past it; nothing executable changed after it.

**HOST LOAD THROTTLE — controller directive, 2026-09-02, binding for the rest
of the wave.** The host's 1-minute load hit **85 on 8 cores** and degraded a
production game server sharing it, from this wave's own concurrent work
(R-1's pytest + a gate container). Generation 4's gate was capped to 3 CPUs
live and finished green, slower; load fell 85 → 10.28 → 7.45.

1. **Never `pytest -n` / xdist.** Serial only, prefixed `nice -n 19 ionice -c
   3`. Prefer targeted test files; **run the whole suite at most once per
   checkpoint.**
2. **Never two gate containers at once.** Check `docker ps | grep
   tester-unified` is EMPTY before launching and wait if it is not — R-1 runs
   one too. Immediately after launching:
   `docker update --cpus=3 $(docker ps -q --filter ancestor=tester-unified:local)`.
3. **No build/pip/wheel step concurrently with a suite run.**

**Two launch-check corrections generation 4 paid for.** (i) Two
`tester-unified` containers at launch is NOT automatically BRIEF-1's hazard —
R-1 runs its own gate on `.worktrees/assay-wave-d-r1`. `pgrep -af
'tester-unified-gate.sh'` shows the worktree path in the argv; check THAT,
not the count. The real hazard is two gates appending to ONE log. (ii)
`docker ps --format '{{.Names}}'` never matches "tester-unified" — container
names are random (`hardcore_euler`); grep the IMAGE. That mistake read as "my
gate died" twice.

**The Monitor note from BRIEF-3 §5 still holds and is still the only thing
that works**: a foreground `sleep` is blocked and polling does not advance the
wall clock. Arm `Monitor` with `until grep -q 'GATE_EXIT=' <log>; do sleep
30; done` at launch and let it fire.

## 7. Next free ids (re-checked against `main`, which MOVED)

`main` advanced to **`72bc041f`** (the controller's own Wave D log entry
recording phase 1 complete and DA-R7). assay's ledgers untouched:

```
$ git show main:assay/nyxloom-trove/decisions.md | grep -o '^| A-[0-9]*' | tail -1
| A-407
$ git show main:assay/nyxloom-trove/4-backlog.md  | grep -o '^## B[0-9]*'  | tail -1
## B061
```

Generation 4 allocated **A-417** and filed **B062** (the `tests/` pyflakes
sweep). **Next free: A-418, B063.** The controller's package text says "B062
for the filing" — that id is already taken on this branch; use **B063** for
R-1's `git -C PROJECT_ROOT.parent` filing and say so in the LOG. Re-run both
commands against `main` before allocating: it has moved in every generation
of this wave, twice in one of them.

## 8. Rules (BRIEF-1 §8, unchanged, plus what generation 4 added)

- File edits through the Edit tool, never `sed`/python rewrite scripts.
- **Never a bare `git stash` / `git stash pop`** (shared stack). Red-prove
  with a WIP commit or a **detached scratch worktree** (`git worktree add
  --detach <scratch> HEAD`, copy the new tests in, run, `git worktree remove
  --force`) — that is what generation 4 used for B024's red-first proof and
  it works cleanly.
- **Run every git command from the worktree**, never after `cd
  /workspaces/vbpub`. The only thing that belongs in `/workspaces/vbpub` is
  the gate launch.
- `git commit -F <msgfile> --only -- <paths>` with BOTH trailers. **New
  files must be `git add`ed first** — `--only` on an untracked path fails.
- **Exactly ONE `!` commit on the branch: the v10 cut.** Nothing before it.
- **Commit BEFORE you gate, and leave the worktree untouched for the WHOLE
  run** (not merely clean at launch).
- Read the gate verdict in a SEPARATE step from the log's own markers.
- A-334: no test double as evidence about an external system. Record measured
  numbers; never quote an arithmetic expectation as a measurement.
- `decisions.md` is APPEND-ONLY. Touch ONLY `assay/**`.
- **One owner at a time on this worktree.** R-1's probe worktrees
  (`.worktrees/assay-wave-d-r1`, its own scratchpad) are not yours.

## 9. Retention prompt for generation 5 (self-authored)

> **KEEP:** the branch/worktree identity and that the **gate-verified commit
> is `7c9e8dd1`**; that **phase 1 is 10/10 DONE including B024** (A-408..A-417
> allocated, B062 filed); that **R-1 round 1 on `93188912` is NOT ACCEPT** and
> §3's fix package is the FIRST work item, with its report already in the repo
> at `reports/assay-WAVE-D-v10-REVIEW-R1-round1.md` (read it before touching
> anything) — especially BLOCKER 1's two silent POST-command guards
> (`runner.py:4618-4642` needs a `diagnostics` parameter; `:2340-2344` →
> `:2809-2827` needs `post_dirty`/`post_observed_head` on `_PreparedUnit`) and
> that the remedy sentence must blame the lane's OWN command; that BLOCKER 2's
> test is hollow and must become a VALUE assertion proven red; DA-R8/DA-R9's
> prescriptions and DA-R10/DA-R11 needing no work; **§4's ciu re-capture, which
> is DONE — the ONLY schema delta is the integer 1 → 2 and `unlabelled` is NOT
> new**, plus its open decision ask; **§5's phase-2 seam table verbatim**
> (especially `config.py:2483-2512` as B050's refusal to delete, and W5's
> four-part shape as W6's template); **§6's throttle rules and both launch-check
> corrections**; §7's ids and that **the controller's "B062" for R-1's filing
> must become B063**; §8's rules.
>
> **DROP:** the reading trail behind B024 (the REPORT has the conclusions and
> every transcript); the full text of every resolved phase-1 backlog entry;
> the phase-1 and B024 red-first transcripts; the docs wording debates; the
> per-container detail of the ciu documents (the MANIFEST addendum has the
> table).
>
> **DO NOT** write the `feat(assay)!:` cut before R-1's fix package is landed
> and gate-green AND every wire change of step 7 exists as an A-row; do not
> re-open a settled phase-1 item; do not decide the ciu `schema_version`
> question on silence; do not run two gate containers or an xdist pytest.
