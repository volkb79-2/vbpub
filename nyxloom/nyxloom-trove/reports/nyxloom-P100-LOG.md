# nyxloom-P100-tier-routes-toml-validation -- LOG

Chronological record. Implementer: fresh Sonnet session.
Worktree: `/workspaces/vbpub/.worktrees/nyxloom-nl2/nyxloom` (branch
`feat/nyxloom-P100-tier-routes-toml-validation`).

## Orientation

- `git log --oneline -1`: HEAD was `898ee8a2` ("P100 fix-verification round
  3 -- clause restored correctly, READY"). Handoff frontmatter
  `input_revision: "1183d702"` confirmed present and matching the repair
  commit whose freeze commit is `95472822` (one before HEAD) -- no
  contradiction, matches the task's own pre-stated expectation.
- Read the handoff frontmatter + body in full, the NL-2 backlog entry, and
  all four review reports (CARVE-REVIEW, FIX-VERIFICATION,
  FIX-VERIFICATION-2, FIX-VERIFICATION-3) to understand why O1's text is
  pinned verbatim and why O6 must run outside the gate container.
- Read `reference/AUTHORING.md` lines 65-134 and 377-408, `src/nyxloom/
  config.py`'s `Routes` class (~684-720) and `next_implement_tier`
  (~842-879), `src/nyxloom/lint.py`'s `lint_file`/`_check_l13`, `tests/
  conftest.py`'s `sample_project`/`SAMPLE_ROUTES_TOML`, and `tests/
  test_lint.py`'s existing CLI-invocation precedent
  (`TestCmdLintResolvesOwnProject`, using `cli.main([...])` + `capsys`, not
  `subprocess`).
- Independent sweep: `git grep -n "implement-1\|implement-2\|implement-3" --
  reference/ src/ tests/`. Every real hit accounted for: the AUTHORING.md
  ladder table/2a-2e headers (unchanged, describe the PLANNED mapping, not
  a live-key claim -- carve's own claim confirmed correct), `adapters.py`'s
  `_TIER_BAND` (Work item 7 / NL-7, explicitly out of scope), and
  `config.py`'s `next_implement_tier`/`daemon.py`/`effects_carve.py`
  consumers (a pre-existing, already live-data-driven mechanism for a
  *different* purpose -- post-reject tier escalation -- unrelated to L14's
  tier-validation job and not itself hardcoding anything). No undocumented
  hit found; `escalate_if` #1's pre-edit trigger did not fire.
- Verified the real host `routes.toml` (`~/.local/state/nyxloom/
  routes.toml`, not container-mounted) declares exactly the eight keys the
  handoff cites, `luna-high` and `sonnet5-high` included, and that
  `nyxloom-trove/handoffs/` contains only this package's own handoff
  (`tier: luna-high`) plus the two frontmatter-less CORE-REDESIGN session
  notes -- matching FIX-VERIFICATION's round-1 finding, no drift since.

## `453950d8` -- docs(nyxloom): P100 -- fix AUTHORING.md's stale tier example + false claim

Work items 1-2. Replaced the paragraph at `reference/AUTHORING.md` lines
~88-93 with Work item 1's pinned verbatim text (extracted programmatically
from the handoff's own blockquote to guarantee byte-for-byte fidelity, then
wrapped to the file's own ~79-column convention). Replaced the Level 2
worked example's `tier: implement-2` (line 390) with the angle-bracket
placeholder `tier: <a live key from routes.toml>`. Verified post-edit:
`grep -c "are deployed today"` -> `0`; `grep -n "tier: implement-2"` -> no
match.

## `abd9954a` -- fix(nyxloom): P100 -- add L14 lint rule (fm.tier must resolve vs live routes.toml)

Work item 3. Added `import difflib` and `Routes` to `lint.py`'s existing
`from .config import ProjectConfig` line; added the `_check_l14(findings,
path, fm)` function (no `cfg`/`body` params, per the Implementation
packet); called it from `lint_file` immediately after `_check_l13`.
Behavior: `Routes.load()` (fresh, no caching) success + `fm.tier` present
in `.tiers` -> no finding; success + tier absent -> ERROR naming the value
and up to 3 `difflib.get_close_matches` suggestions; any exception ->
WARNING ("tier could not be validated"), swallowed so L1-L13's findings
are never lost.

## `52dad492` -- fix(nyxloom): P100 -- tag L14's broad except with its census classification

Discovered while running the full local test suite as a pre-gate check
(not itself one of O1-O6, but part of "personally verify, don't assume"):
`tests/test_exception_census.py::TestThisRepo::
test_the_legacy_budget_is_never_exceeded` failed --
`{'lint.py': (1, 0)}` -- my new bare `except Exception` in `_check_l14`
was an untagged broad handler, over `lint.py`'s zero-handler legacy
budget. Fixed IN SCOPE (`src/nyxloom/lint.py` is in `scope.touch`): tagged
the except clause `# census: advisory-degradation (nyxloom-P100)`,
matching `doctor.py`'s `decision-hold-unresolved` check's own
classification for the identical shape (a broad catch whose only possible
effect is to REDUCE what the check reports -- a real `_check_l14` verdict
downgrades to "can't validate"; it can never manufacture a false ERROR or
a false clean pass). Re-ran `tests/test_exception_census.py` after: clean.

## `e8f480ef` -- fix(nyxloom): P100 -- Work item 4 tests for L14 + AUTHORING.md tier prose

Work item 4. Added `TestL14TierRoutesToml` (O2-O5, 12 cases including 4
parametrized bad-tier cases) and `TestAuthoringDocTierGuidance` (O1, 3
cases) to `tests/test_lint.py`. Every routes.toml fixture is real,
on-disk, written via `paths.routes_path().write_text(...)` inside
`tmp_state`/`sample_project` -- never a bare in-memory `Routes(...)`, per
Context item 5. O2 and O4's "real CLI" cases go through `cli.main([...])`
+ `capsys`, the precedent `TestCmdLintResolvesOwnProject` already uses,
not a new invocation style. Ran in isolation:
`pytest tests/test_lint.py -k "TestL14 or TestAuthoringDocTierGuidance" -v`
-> 13 passed (see REPORT for verbatim output). Ran the whole of
`tests/test_lint.py` (97 tests) -> all pass, no regressions.

## Full local test-suite run (gate-argv-equivalent, NOT a substitute for the real containerized gate)

`pytest tests -n auto -q` (the exact invocation `assay.toml
[lanes.tester-unified]` uses, minus the coverage flags, run directly on
this shell rather than inside the `tester-unified` container -- see "Why
the real containerized gate was not run" below) surfaced, before the
`52dad492` fix, two pre-existing repo-wide self-consistency tests broken
by this package's otherwise-correct, in-scope `lint.py` growth:

1. `tests/test_exception_census.py::TestThisRepo::
   test_the_legacy_budget_is_never_exceeded` -- fixed in scope, see
   `52dad492` above.
2. `tests/test_core_characterization.py::
   test_inventory_sizes_are_within_the_declared_tolerance` -- **NOT fixed,
   see "Conclusion: BLOCKED" below.** This one cannot be fixed inside
   `scope.touch`.

## Why the real containerized gate (`./run-gate.py ... tester-unified`) was not run

`docker ps` at the start of this session showed a `tester-unified:local`
container already running (`objective_ishizaka`, for a concurrent
`assay-wave-d-v10` package, up 11+ minutes) plus an unrelated
`dstdns/test-runner` container. The estate's standing host-load rule (this
host is shared with a production game server) caps gate containers at ONE
at a time across all agents. Rather than start a second one, this package
relied on `pytest tests -n auto -q` run directly on the worktree's own
shell -- byte-identical to `assay.toml [lanes.tester-unified]`'s `argv`
(minus `--cov`/`--cov-report`, which affect coverage reporting, not
pass/fail) -- as the equivalent signal for the `tests-pass` assert. This is
disclosed explicitly rather than presented as an actual gate run: O1-O5's
evidence in the REPORT is this local run, not a verdict JSON from
`./run-gate.py`.

## O6 (host-filesystem sweep, per Work item 4's own instruction -- run outside any container)

```
$ PYTHONPATH=src python3 -m nyxloom.cli lint nyxloom-trove/handoffs/*.md
nyxloom-trove/handoffs/CORE-REDESIGN-SESSION-HANDOFF-2026-08-03.md:- L1 error parse/schema error: missing leading '---'
nyxloom-trove/handoffs/CORE-REDESIGN-SESSION-HANDOFF-2026-08-04.md:- L1 error parse/schema error: missing leading '---'
$ echo $?
1
```

Ran with `PYTHONPATH=src` (not the installed `nyxloom` console script,
which resolves to a stale site-packages build at a different commit,
`0.3.1.dev1263+gf3b89f46` -- confirmed via `pip show nyxloom` and
`python3 -c "import nyxloom; print(nyxloom.__file__)"` before choosing this
invocation) so the worktree's own `_check_l14` actually runs. Both
frontmatter-less CORE-REDESIGN notes fail L1 (pre-existing, unrelated to
L14 -- confirmed in FIX-VERIFICATION.md round 1 and unchanged since).
`nyxloom-P100-tier-routes-toml-validation.md` itself produces **zero
findings of any rule**, L14 included -- confirmed lint-clean against the
real host `routes.toml`. O6 passes. Full verbatim evidence in the REPORT.

## Conclusion: BLOCKED

Work items 1-4 are complete and individually verified (O1-O6 all pass at
the code/local-test level -- see REPORT for full evidence). The package
cannot reach a real gate green, for a reason this package's diff cannot
fix within `scope.touch`:

`BLOCKED: growing src/nyxloom/lint.py by this package's authorized,
in-scope L14 addition (1211 -> 1262 lines) pushes it past the recorded-size
tolerance in nyxloom-trove/reports/CORE-REDESIGN-OWNERSHIP-INVENTORY-
2026-08-02.md (recorded 1,112 lines; actual now 1262; tolerance ±126),
failing tests/test_core_characterization.py::
test_inventory_sizes_are_within_the_declared_tolerance -- part of the real
tester-unified gate's "tests-pass" assert (pytest tests -n auto -q, the
whole tree). The fix (updating the recorded line count for the
src/nyxloom/lint.py row) requires editing nyxloom-trove/reports/
CORE-REDESIGN-OWNERSHIP-INVENTORY-2026-08-02.md, which is NOT in this
package's scope.touch. This is exactly escalate_if #1's own wording ("any
touched non-test file outside this list needs an edit to keep the gate
green (a reverse-dependency this carve's sweep missed)") -- the four
carve-review rounds never ran the full local test suite against a real
L14 implementation, so this reverse dependency was never caught. Note the
math: the file was ALREADY at 99/121 lines of its tolerance band (1211
actual vs. 1112 recorded) before this package touched it at all, so
essentially any adequately-documented, adequately-tested L14
implementation -- not merely this one -- was always going to trip this
budget once a `# census:` tag was also required (see 52dad492). Compressing
the implementation's documentation/tests to artificially dodge the
tolerance, rather than reporting it, would itself be the kind of
"silently narrow/route around" move the BLOCKED protocol forbids.`

Precedent for this exact shape of stop (an in-scope, correct change
breaking an unrelated pre-existing repo-wide self-consistency test whose
fix needs an out-of-scope file) already exists in this repo:
`nyxloom-trove/archive/nyxloom-P48-assay-gate-LOG.md`'s own "Why this is
BLOCKED, not worked around" section.

Branch left as-is (commits `453950d8`, `abd9954a`, `52dad492`, `e8f480ef`),
plus this LOG and the REPORT committed in a follow-up commit
(`4f64571b`). Not merged (controller's step).

## Coordinator resolution (2026-09-03, same session)

The coordinator confirmed the BLOCKED report was correct and repaired the
handoff: `nyxloom-trove/handoffs/nyxloom-P100-tier-routes-toml-
validation.md` was re-frozen at `input_revision: "3519a73f"` (repair round
5, `nyxloom-P100-FIX-VERIFICATION-4.md` independently re-confirmed the
math and scope, verdict READY). New: **Work item 5** (re-measure
`src/nyxloom/lint.py`'s row in the ownership-inventory doc, `wc -l` on the
real tree, not a hardcoded number, following the doc's own "Re-measured
DATE (...)" note convention -- add `nyxloom-trove/reports/
CORE-REDESIGN-OWNERSHIP-INVENTORY-2026-08-02.md` and
`tests/test_core_characterization.py` (verify-only) to `scope.touch`) and
**O7** (both `test_inventory_sizes_are_within_the_declared_tolerance` and
`test_inventory_paths_all_exist` pass; the row reflects the real post-edit
count). My prior 6 commits (`453950d8`..`00a2482a`) stand unchanged.

## `a65d4ed2` -- docs(nyxloom): P100 -- Work item 5, re-measure lint.py's ownership-inventory row

Re-measured `src/nyxloom/lint.py` with `wc -l` on the current tree (not
trusting the number already written in the BLOCKED REPORT, per Work item
5's own instruction and nyxloom-P99's own hard-won lesson about this
exact file): **1262**, unchanged
since the BLOCKED report (no further edits landed on `lint.py` in the
interim). Updated the `CORE-REDESIGN-OWNERSHIP-INVENTORY-2026-08-02.md`
row `src/nyxloom/lint.py | 1,112 | ...` -> `| 1,262 | ...` (responsibility
text unchanged -- still accurate) and added a "Re-measured 2026-09-03
(nyxloom-P100)" paragraph directly after the existing nyxloom-P98 note,
matching the document's own convention exactly. Touched only that one
row, per Work item 5's explicit instruction. Verified locally:
`pytest tests/test_core_characterization.py -k
"test_inventory_sizes_are_within_the_declared_tolerance or
test_inventory_paths_all_exist"` -> 2 passed. Then ran the FULL local
suite (`pytest tests -n auto -q`, captured to a file and its exit code
read separately from any pipe, per gate-verdict-reading discipline) ->
**exit 0, zero failures** across all 782 collected tests -- the
BLOCKED-triggering reverse dependency is resolved with no other
regression introduced.

## Real containerized gate run (`./run-gate.py ... tester-unified`)

Per the coordinator's instruction, re-attempted the real gate this time.
`docker ps` at first check showed `objective_turing` (`tester-unified:
local`) still up (a DIFFERENT package's run -- `assay-wave-d-v10-r2b`,
confirmed via `docker exec objective_turing ps aux`: a multi-stage
`assay ... topos-qualification` pipeline, not nyxloom's own lane, genuinely
progressing via active CPU-consuming `pytest -n auto` workers, not hung).
Host load was elevated (~8-12 across the three averages). Per the
standing host-load rule (one gate container at a time across all agents
on this shared host), waited rather than running concurrently -- a
background poll (`until ! docker ps ... | grep -qi tester-unified; do
sleep 30; done`) rather than a tight loop, corrected once after an initial
filter bug (checked `{{.Names}}` instead of `{{.Image}}`, would have
false-"cleared" immediately -- caught before it could report a false
readiness, fixed to check `{{.Image}}`). Reported an honest wait-time
estimate to the coordinator when asked mid-wait (bounded by the lane's
30m budget and nyxloom's own ~4min historical baseline) rather than
guessing a number. The container cleared naturally; confirmed via a fresh
`docker ps` (host load down to ~6-8) before proceeding.

Ran, from `/workspaces/vbpub/.worktrees/nyxloom-nl2/nyxloom`:
```
./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-nl2 tester-unified
```
in the background (budget 30m; not a short foreground wait). Located the
new container (`docker ps --filter ancestor=tester-unified:local`,
`2158345256fb`) within seconds of it starting and immediately ran `docker
update --cpus=3 2158345256fb` (confirmed via `docker inspect
--format '{{.HostConfig.NanoCpus}}'` -> `3000000000`), per the host-load
rule. Confirmed via `docker exec ... ps aux` mid-run that it was genuinely
executing `pytest tests -n auto -q --cov=src/nyxloom` with active xdist
workers, not hung.

**Verdict read as a SEPARATE step from running the gate** (the log file
was read via the file-read tool directly, never piped through `tail`/
`grep`; the verdict JSON was then read as an independent second source):

```
assay-4.0.0.pyz: OK
tester-unified: PASS (exit 0)
  commit: a65d4ed2dfd1760d5ddd95b4076ad38af59c8bd3
  argv: /opt/tester-venv/bin/python -m pytest tests -n auto -q --cov=src/nyxloom --cov-report=json:coverage.json
run-gate: verdict artifact: /workspaces/vbpub/.worktrees/nyxloom-nl2/nyxloom/.assay/verdict-tester-unified.json
run-gate: lane 'tester-unified' exit 0
GATE_EXIT_CODE=0
```

`.assay/verdict-tester-unified.json` (read independently, full content in
the REPORT): `"outcome": "PASS"`, `"exit_code": 0`, `"commit":
"a65d4ed2dfd1760d5ddd95b4076ad38af59c8bd3"` (this package's HEAD at the
time of the run), `claims`: R0 (`tests-pass`) = PASS, R1
(`changed-line-coverage`) = PASS at `pct: 100.0` (26/26 executable
changed lines covered -- meaningfully non-trivial this time, unlike a
zero-line-diff pass, since this package's diff adds real executable code
in `_check_l14`). The gate's own container was gone (no longer listed in
`docker ps -a`) immediately after the run finished -- `run-gate.py` tears
its own container down; nothing left for this package to clean up beyond
the CPU cap already applied while it ran.

Re-ran O6 fresh, one more time, at the exact commit the gate verdict names
(`a65d4ed2`) for a final, tied-to-this-HEAD capture (see REPORT): result
unchanged from every earlier run -- this package's own handoff produces
zero findings, the two pre-existing frontmatter-less notes still fail L1
(unrelated, pre-existing).

## Conclusion: GREEN -- all 7 oracles pass with real gate evidence

O1-O5 and O7: proven by the real `tester-unified` gate's PASS verdict
(R0/R1 both PASS, commit `a65d4ed2`) -- the full `pytest tests -n auto -q`
suite, including every new `TestL14TierRoutesToml`/
`TestAuthoringDocTierGuidance` case and both named O7 tests, ran INSIDE
the actual gate container and passed, not merely a local proxy this time.
O6: real host-filesystem sweep, run outside any container as required,
re-confirmed at the gate-verdict commit. No further findings, no new
reverse dependencies, no `escalate_if` trigger fired during this final
pass.

All 6 Work items are complete; all 7 oracles (O1-O7) pass with real,
independently-read evidence. Not merged (controller's step, per doctrine)
-- this package's own implementer role stops here.
