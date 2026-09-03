# assay Wave D (v10) — BRIEF-2 (generation 2 → generation 3)

Written at generation 2's E-008 checkpoint. **Cumulative delta since BRIEF-1
only** — BRIEF-1 stays the seam map for everything it settled, and generation
3 should read it first, then this.

---

## 1. Where the branch stands

- Worktree `/workspaces/vbpub/.worktrees/assay-wave-d-v10`, branch
  `feature/assay-wave-d-v10`, forked from `main` at `a4a865da`.
- **Phase 1 items DONE: 7 of 10.**

| # | item | ruling | status |
|---|---|---|---|
| 1 | B049 | DA-D1 | DONE (gen 1) — `3b2b8e62`, A-408 |
| 2 | **B054** | DA-D3 + DA-R2 | **DONE** — `c37ca3fb`, A-410 |
| 3 | **B053 (a)+(b)** | DA-D2 + DA-R1 | **DONE** — `440d5da9`, A-409 |
| 4 | B028 | DA-D10 | **NOT STARTED** — §3 |
| 5 | B029 | DA-D11 | **NOT STARTED** — §4 |
| 6 | **B060** | DA-D14 | **DONE** — `c80b3452`, A-411 |
| 7 | **B056** | DA-D13 | **DONE** — `c80b3452`, A-412 |
| 8 | B024 | DA-D15 | **NOT STARTED** — §5 |
| 9 | **B055** | DA-D12 | **DONE** — `c80b3452`, A-413 (ruling + docs) |
| 10 | **B009** | DA-D16 | **DONE** — `c80b3452` (docs only) |

Phase 2 and phase 3 untouched. **Nothing under `verdict.py`, `verify.py`,
`src/assay/schemas/` or the drift-guard carve-assets has been modified**, and
no commit carries `!` — the branch is still releasable on v9.

**Order note:** generation 2 did B053 before B054 (BRIEF-1 §3 left the choice
open). Nothing depended on it; the LOG says so.

## 2. What landed, in one line each (details: LOG entries 4-6, REPORT)

- **`440d5da9` — B053 (a)+(b), A-409.** One emitter,
  `runner.announce_refusal(exc, *, diagnostics)` (`runner.py:307`), printing
  exactly `assay: {outcome}/{reason_code}: {message}`, called at 13 conversion
  sites in `runner.py` plus `cli.py`'s three refactored prints. `evaluate_r1`
  gained `diagnostics` (default `None`, so `canary.py` stays silent).
  9 new tests; red-first 6F/3P.
- **`c37ca3fb` — B054, A-410.** `FileCoverage.contradictory_branch_lines`
  (`model.py:366`); the istanbul parser drops the contradicting arcs and
  records their lines; `evaluate._refuse_contradictory_branch_arcs`
  (`evaluate.py:195`) refuses only for a JUDGED file, from the same two places
  A-405's check is called from; `runner._announce_contradictory_branch_records`
  (`runner.py:352`) names every defective record on diagnostics. 7 new tests;
  red-first 5F/2P.
- **`c80b3452` — B060/A-411, B056/A-412, B055/A-413, B009.** `build_zipapp`
  stages under a `TemporaryDirectory`; the packaging test's refuted
  measurement corrected in docstring AND assertion message; the Go
  line-granularity limit ruled and documented; a new CONSUMERS section on
  `assay.toml`'s role and the MEASURED distribution model.

## 3. NEXT — item 4, B028 (DA-D10). Nothing done; here is what generation 2 found

**The ruling:** one outer catch per higher-rigor entry point
(`_run_higher_rigor_lane`) and one for direct R0's own loop (inside
`run_lane`): a lane-wide `LANE_TIMEOUT` becomes the refusal claim the existing
refuse path already builds, the reserved `--verdict-json` is WRITTEN, cleanup
of a half-built snapshot is attempted and its failure recorded through the
existing cleanup-failure path (never masking the timeout). Test red-first
**through the installed CLI** with `budget_seconds = 1` and a real slow
command. ONE outer catch per entry point, NOT per call site.

**Seams, re-verified at `c80b3452`** (BRIEF-1's numbers have shifted — B053
and B054 added ~150 lines to `runner.py`):

- `LaneDeadline.remaining` — `src/assay/runner.py:215`.
- `_run_higher_rigor_lane` — `src/assay/runner.py:3612`, its `diagnostics`
  parameter at `:3646`. Its existing outer `try` around the snapshot block
  is at `:3776` (`except AssayError` → `refuse_all` / the GIT_FAILED claim
  replacement) — **that is almost certainly where B028's catch belongs**, and
  generation 2 already added an `announce_refusal` call there; read that
  handler before adding a second one beside it.
- `run_lane` — `src/assay/runner.py:3838`, `diagnostics` at `:3867`. The
  direct-R0 path's own plan resolution catch is at `:4254`.
- `refuse_lane` — `src/assay/runner.py:1584`-ish (public; unchanged).
- The reserved-`--verdict-json` write is `cli.py`'s
  `runner.write_verdict(verdict, destination)`; every refusal that RETURNS a
  `Verdict` already gets it. So "the reserved `--verdict-json` is WRITTEN"
  reduces to "the timeout must become a returned Verdict, not an escaping
  exception".

**The one thing to check first:** whether a lane-wide `LANE_TIMEOUT` can
currently escape as an exception at all, or whether it already becomes a
claim. `grep -n 'LANE_TIMEOUT' src/assay/runner.py src/assay/mutation.py
src/assay/canary.py` and write the failing CLI test BEFORE deciding where the
catch goes — B028's value is the end-to-end behaviour, not the handler.

## 4. NEXT — item 5, B029 (DA-D11). Seam located, one call site not two

**The ruling:** thread `infrastructure_source`/`infrastructure_environment`
through `runner.execute_command` into `canary.py`'s call sites; CLI-driven
test with a resolvable `derived:` fact asserting the R3 claim is PASS/FAIL on
the canary, not `ERROR`/`BAD_LANE_CONFIG`.

**Measured, and it corrects BRIEF-1's "two call sites":**

```
$ grep -n 'execute_command(' src/assay/*.py
src/assay/runner.py:828:def execute_command(          <- the definition
src/assay/canary.py:213:    result = execute_command(  <- the ONLY caller
```

`canary.py:213` is inside `_run_pipeline`, which BOTH halves of
`run_python_canary` (control and transformed) go through — so "two call
sites" is two CALLERS of one seam, and one edit covers both.
`execute_command`'s own docstring already names B029 at
`src/assay/runner.py:845-852` and says exactly what is wrong: it accepts
neither parameter, so `resolve_command_plan` always raises for a `derived:`
fact regardless of whether it would resolve.

**The part generation 2 did NOT verify and generation 3 must:** whether the
ISOLATED canary path (`canary.run_isolated_canary`, which is what
`_run_prepared_lane` actually uses) reaches `execute_command` at all. It takes
a pre-executed `unit.result`, so the command may already have been run by the
snapshot-unit machinery WITH infrastructure — in which case B029's defect is
confined to the legacy standalone `run_python_canary` path and the CLI test
the ruling asks for may not reach it. **Resolve that before writing code**:
if the shipped R3 path is unaffected, that is a decision ask, not a fix.

The misattribution site the entry names is `_run_higher_rigor_lane`'s catch
around `run_isolated_canary` — now `src/assay/runner.py:3301` (the R3 claim
built from an `AssayError`, where generation 2 added `announce_refusal`).

## 5. NEXT — item 8, B024 (DA-D15). Read the ruling's escape hatch first

**The ruling:** wire `pyflakes` and `ruff` (pyflakes-equivalent rule set ONLY
— F-rules, no style rules; name which in the A-row) into
`assay/tools/tester-unified-gate.sh` as a phase AFTER the suite; inside the
image if the tools are there, else inside `run-venv` from the offline
wheelhouse **if the closure already carries them**. **If neither is possible
without a network fetch: write the decision ask and land nothing** —
A-198's hash-bound closure is not to be loosened for a linter.

Not started. Check, in this order:
1. `docker run --rm tester-unified:local sh -lc 'python -m pyflakes --version; ruff --version'`
2. `assay/gate/distribution/build-wheelhouse/` and
   `assay/gate/distribution/build-requirements.txt` for an existing pin.
3. Only then decide. R-1 will plant an unused import and expect the gate red.

## 6. Load-bearing seams generation 2 added (read before touching them)

| seam | where | why it matters |
|---|---|---|
| `announce_refusal` | `runner.py:307` | the ONE refusal emitter; any new `AssayError`→claim conversion must call it, exactly once per error |
| `_announce_contradictory_branch_records` | `runner.py:352`, called from `evaluate_r1` at `:1267` | the skip notice; runs after `check_empty_coverage`, before the branch-capability guard |
| `evaluate_r1(diagnostics=…)` | `runner.py:1124` | default `None`; `canary.py:219`/`:360` deliberately do not pass it |
| `FileCoverage.contradictory_branch_lines` | `model.py:366` | STORED, exempt from the arc invariants, positivity-checked; carried by BOTH rebuilds in `statement_attribution.py` (`:225`, `:342`) |
| `_refuse_contradictory_branch_arcs` | `evaluate.py:195`, called at `:561` and `:1156` | sits immediately after `_refuse_line_directive_remapped` in both modes — keep them adjacent |
| the `built` fixture's shape | `tests/test_distribution_build_release.py` | each build now targets `<tmp>/dist`; `test_a_build_writes_nothing_outside_its_own_outdir` depends on the enclosing directory being otherwise empty |

## 7. Gate state

Launch recipe (unchanged from BRIEF-1 §6, and it worked first time this
generation — one process, one container, confirmed by `pgrep -af
'tester-unified-gate.sh'` and `docker ps | grep tester-unified` within 8s of
launch):

```
cd /workspaces/vbpub && setsid nohup bash -c '{ bash assay/tools/tester-unified-gate.sh \
  /workspaces/vbpub/.worktrees/assay-wave-d-v10; echo GATE_EXIT=$?; } \
  > <log> 2>&1' < /dev/null > /dev/null 2>&1 &
```

BRIEF-1's two traps were both avoided: the log path is a literal inside the
`bash -c` string (no `VAR=x && setsid` list), and the worktree was committed
clean and left untouched for the whole run.

**GATE-VERIFIED COMMIT: `c80b3452`.** One run, first try, green. Verdict read
in a SEPARATE step from the log's own markers:

```
COMPLETE_MARKERS=1          (ASSAY_REGISTERED_GATE_COMPLETE=1, exactly one)
GATE_EXIT=0
BAD=0                       (grep -c -E 'FAILED|DIRTY_TREE|Traceback')
Created wheel for assay: assay-4.1.1.dev9+gc80b3452-py3-none-any.whl
  sha256=c67b74feae1ccb866c40b1b810fdd5ec9e4d38be267d81e0b56789d8d8927b0c
tester-unified: PASS (exit 0)
  commit: c80b34521150c82a8dc87760e987b54e2f977c55
ASSAY_GATE_PHASE=self-hosted-lane-passed
ASSAY_B006A_CMRU_QUALIFIED=1
ASSAY_GATE_PHASE=independent-self-hosting-passed
```

The wheel name carries the judged commit. The branch tip is one docs-only
commit past it (this brief and the LOG's gate entry); nothing executable
changed after `c80b3452`.

Whole suite, worktree-local, before the gate: **3968 passed, 20 skipped in
509.12s**, zero failures.

**One trap of this generation's own, worth passing on:** `git commit` was
issued after `cd /workspaces/vbpub`, which is the MAIN checkout, not the
worktree — it failed on a pathspec rather than committing to `main`, but only
by luck of the untracked test file. **Run every git command from the worktree
(the Bash tool's default cwd), never after a `cd /workspaces/vbpub`** — the
only thing that belongs in `/workspaces/vbpub` is the gate launch.

## 8. Next free ids (re-checked against `main`, which MOVED)

`main` advanced from `a4a865da` to **`9b0bca62`** during this generation
(`602e1930` the Wave D controller log, `9b0bca62` a ciu backlog filing).
Neither touches assay's ledgers:

```
$ git show main:assay/nyxloom-trove/decisions.md | grep -o '^| A-[0-9]*' | tail -1
| A-407
$ git show main:assay/nyxloom-trove/4-backlog.md  | grep -o '^## B[0-9]*'  | tail -1
## B061
```

Generation 2 allocated **A-409, A-410, A-411, A-412, A-413** and filed no new
backlog entry. **Next free: A-414, B062.** Re-run both commands against
`main` before allocating — it moves.

## 9. Decision asks open for the controller (full text in the REPORT)

1. **B053:** refusals that carry no `AssayError` (`DIRTY_TREE`,
   `HEAD_CHANGED`, `MISSING_EXTERNAL_TOOL`, the `env_required` and `--shard`
   refusals) print no line, because there is no message to copy. Should phase
   2 give those five sites their own `AssayError` so "every refusal prints
   exactly one line" holds without qualification?
2. **B053:** an `r2_early_claim` that is later superseded by a failed command
   has already been announced — one extra TRUE sentence, never a false one.
   Noise, or correct?
3. **B056:** option 2 (a second wheel build with the git finder disabled) was
   declined on cost. R-1 may rule the other way.

## 10. Retention prompt for generation 3 (self-authored)

> **KEEP:** the branch/worktree identity and the tip; that phase 1 items 1,
> 2, 3, 6, 7, 9, 10 are DONE with their hashes and A-rows (A-408..A-413);
> that only **B028, B029, B024** remain in phase 1; §3/§4/§5's seams
> *including the two corrections generation 2 measured* (B029 has ONE
> `execute_command` caller, `canary.py:213`, and the isolated-canary path may
> not be affected at all; B028's likely catch is the existing handler at
> `runner.py:3776`); §6's table of the seams generation 2 added; the gate
> launch recipe and BOTH trap notes (including "never run git after `cd
> /workspaces/vbpub`"); next free ids A-414 / B062 and that `main` moves;
> BRIEF-1 §8's rules (Edit tool only, no bare `git stash`, `git commit -F …
> --only -- <paths>` with both trailers, no `!` in phase 1, commit before you
> gate, read the verdict in a separate step, touch only `assay/**`).
>
> **DROP:** the reading trail behind B053's and B054's seams (the REPORT has
> the conclusions); the full text of every backlog entry already resolved
> (B049, B053, B054, B055, B056, B009, B060); the test transcripts (LOG and
> REPORT carry them); the docs wording.
>
> **DO NOT** start phase 2, touch `verdict.py`/`verify.py`/the schema/the
> drift-guard, or use a `!` commit marker, until B028, B029 and B024 are
> landed (or B024's decision ask is written) and the gate is green on that
> tip.
