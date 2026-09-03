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
plus this LOG and the REPORT committed in a follow-up commit. Not merged
(controller's step).
