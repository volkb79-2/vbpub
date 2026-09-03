# nyxloom-P100-tier-routes-toml-validation -- REPORT

**Status: BLOCKED after Work items 1-4 complete.** Work items 1-4 are
implemented and every oracle (O1-O6) independently passes at the
code/local-test level. The package cannot reach a real `tester-unified`
gate green for a reason this package's diff cannot fix inside
`scope.touch` -- see "Blocking finding" at the end. Work items 5-6 (final
gate confirmation, closing claim) are not reachable until the blocking
finding is resolved.

## What changed

- `reference/AUTHORING.md`: replaced the false "Only `implement-1` and
  `implement-2` are deployed today" paragraph (lines ~88-93) with Work
  item 1's pinned verbatim text; replaced the Level 2 worked example's
  literal `tier: implement-2` (line 390) with the placeholder
  `tier: <a live key from routes.toml>`.
- `src/nyxloom/lint.py`: added `import difflib`; added `Routes` to the
  existing `from .config import ProjectConfig` import; added
  `_check_l14(findings, path, fm)` (called from `lint_file` immediately
  after `_check_l13`); the module docstring's rule range updated
  `L1-L13` -> `L1-L14`. The broad `except Exception` inside `_check_l14`
  carries `# census: advisory-degradation (nyxloom-P100)` (required by
  `tests/test_exception_census.py`, discovered while pre-verifying the
  gate locally -- see LOG `52dad492`).
- `tests/test_lint.py`: added `import re`; added module-level
  `AUTHORING_MD_PATH`/`AUTHORING_TIER_PARAGRAPH` constants and
  `TestAuthoringDocTierGuidance` (O1, 3 tests); added
  `SONNET_ROUTES_TOML`/`MALFORMED_ROUTES_TOML_BAD_SYNTAX`/
  `MALFORMED_ROUTES_TOML_MISSING_ROUTES_KEY`/`_l14_handoff_text` and
  `TestL14TierRoutesToml` (O2-O5, 10 tests, 4 of them one parametrize
  block).
- `src/nyxloom/schemas/handoff-frontmatter.schema.json`: verified, NOT
  edited -- `tier`'s `"type": "string"`/`"pattern"` are unchanged.
- `tests/conftest.py`: verified, NOT edited -- `sample_project`'s
  `paths.routes_path().write_text(SAMPLE_ROUTES_TOML)` pattern is what
  every new L14 test fixture mirrors.
- `nyxloom-trove/handoffs`, `nyxloom-trove/archive`: verified via O6 (see
  below), NOT edited.

## Independent orientation sweep (before editing anything)

`git grep -n "implement-1\|implement-2\|implement-3" -- reference/ src/
tests/` -- every real hit accounted for:

- `reference/AUTHORING.md`'s ladder table (lines 80-86) and 2a-2e section
  headers (95-134): unchanged, correctly describe the PLANNED
  contract-class-to-tier mapping, never claim these are live `routes.toml`
  keys today (confirmed by direct reading, matching CARVE-REVIEW's own
  finding).
- `src/nyxloom/adapters.py`'s `_TIER_BAND` (line 181) and its three other
  mentions (187, 202, 209): Work item 7 / NL-7, explicitly out of this
  package's scope -- not touched.
- `src/nyxloom/config.py`'s `next_implement_tier` (~842-879) and its
  docstring's `implement-N` mentions, plus its callers in `daemon.py`
  (~1683) and `effects_carve.py` (~536): a pre-existing, ALREADY
  live-routes-data-driven mechanism (CR-09a) for a different job --
  computing the next-higher `implement-N` tier bump after an "incapable"
  reject verdict -- not a hardcoded tier list, and not L14's concern.
  Confirmed by reading `next_implement_tier`'s implementation: it iterates
  `routes.tiers` live, never a baked-in list.
- Tests referencing `implement-N` in `test_adapters.py`, `test_config.py`,
  `test_daemon.py`, `test_route_doctor.py`, `test_capability_map.py`,
  `test_effects_attempt.py`: all exercise the pre-existing mechanisms
  above using `implement-N` as sample tier-name shapes; none are lint/L14
  related.

No undocumented hit found. `escalate_if` #1's pre-edit trigger did not
fire at this stage (it fired later -- see "Blocking finding").

## Oracle evidence

### O1 -- AUTHORING.md's tier prose

Manual verbatim checks, run directly against the edited file:

```
$ grep -c "are deployed today" reference/AUTHORING.md
0
$ grep -n "tier: implement-2" reference/AUTHORING.md
(no output, exit 1)
$ grep -n "tier: <a live key" reference/AUTHORING.md
395:tier: <a live key from routes.toml>    # live capability band, not a model name
```

Automated (`tests/test_lint.py::TestAuthoringDocTierGuidance`, run via
`pytest tests/test_lint.py -k TestAuthoringDocTierGuidance -o addopts="" -v`):

```
tests/test_lint.py::TestAuthoringDocTierGuidance::test_pinned_replacement_paragraph_present_verbatim PASSED
tests/test_lint.py::TestAuthoringDocTierGuidance::test_old_deployed_today_claim_is_gone PASSED
tests/test_lint.py::TestAuthoringDocTierGuidance::test_worked_example_tier_is_a_placeholder_not_implement_2 PASSED
```

`test_pinned_replacement_paragraph_present_verbatim` compares
whitespace-normalized text (per FIX-VERIFICATION-2.md's own note that a
literal multi-line `grep -F` is not the intended mechanism -- "ordinary
implementation latitude") against Work item 1's pinned paragraph,
extracted programmatically from the handoff's own blockquote (not
re-typed by hand) when the constant was authored, so no transcription
drift is possible.

### O2 -- valid tier, both call paths agree

```
tests/test_lint.py::TestL14TierRoutesToml::test_valid_tier_no_finding_direct_call PASSED
tests/test_lint.py::TestL14TierRoutesToml::test_valid_tier_no_finding_real_cli PASSED
```

Both write a real on-disk `routes.toml` declaring only `[tiers.sonnet5-
high]` via `paths.routes_path().write_text(...)` inside `sample_project`/
`tmp_state`. The direct-call test asserts `lint.lint_file(...)` produces no
L14 finding; the real-CLI test asserts `cli.main(["lint", str(handoff)])`
(the same invocation style `TestCmdLintResolvesOwnProject` already uses)
prints no `"L14"` substring and exits 0.

### O3 -- three historical bad values + required near-miss, each with exactly one ERROR

```
tests/test_lint.py::TestL14TierRoutesToml::test_bad_tier_produces_l14_error[implement-2-False] PASSED
tests/test_lint.py::TestL14TierRoutesToml::test_bad_tier_produces_l14_error[sonnet-xhigh-True] PASSED
tests/test_lint.py::TestL14TierRoutesToml::test_bad_tier_produces_l14_error[opus-xhigh-False] PASSED
tests/test_lint.py::TestL14TierRoutesToml::test_bad_tier_produces_l14_error[sonnet5-hgih-True] PASSED
```

Each asserts exactly one L14 finding, `severity == "error"`, and the bad
value named in the message. Per Work item 3's explicit allowance ("empty
list is fine -- do not error if no close match exists"), only
`sonnet-xhigh` and `sonnet5-hgih` additionally assert `"sonnet5-high"` is
named as a suggestion -- confirmed by direct computation before writing
the test that, against a routes.toml declaring only `sonnet5-high`,
`difflib.get_close_matches` (default `cutoff=0.6`) returns `[]` for
`implement-2` and `opus-xhigh` but `['sonnet5-high']` for the other two:

```
$ python3 -c "
import difflib
for bad in ['implement-2','sonnet-xhigh','opus-xhigh','sonnet5-hgih']:
    print(bad, difflib.get_close_matches(bad, ['sonnet5-high'], n=3))
"
implement-2 []
sonnet-xhigh ['sonnet5-high']
opus-xhigh []
sonnet5-hgih ['sonnet5-high']
```

This distinguishes a real `Routes.load().tiers`-driven check from a
hardcoded allowlist/blocklist: `_check_l14` never special-cases any of the
four strings, yet correctly errors on all four.

### O4 -- missing and malformed routes.toml, both WARN via the real CLI, other rules unaffected

```
tests/test_lint.py::TestL14TierRoutesToml::test_missing_routes_toml_warns_via_real_cli PASSED
tests/test_lint.py::TestL14TierRoutesToml::test_malformed_routes_toml_bad_syntax_warns_and_keeps_other_findings PASSED
tests/test_lint.py::TestL14TierRoutesToml::test_malformed_routes_toml_missing_routes_key_warns PASSED
```

Case (a) (`routes.toml` absent -- `tmp_state`'s `ensure_layout()` only
creates directories, never the file itself, confirmed by reading
`paths.ensure_layout`) and case (b) in both its named variants (invalid
TOML syntax -> `tomllib.TOMLDecodeError`, a `[tiers.x]` entry missing its
`routes` key -> `KeyError` from `Routes.load()`'s own `spec["routes"]`
comprehension) all produce an L14 WARNING through `cli.main(["lint",
str(handoff)])`, exit code 0. The bad-syntax case additionally pads the
handoff body to 45,000 chars (reusing `TestL10Size`'s own over-threshold
shape) and asserts `"L10"` is still present in the same output --
confirming L1-L13's other findings for the file are not lost to the
broad-except path.

### O5 -- live re-read, no caching

```
tests/test_lint.py::TestL14TierRoutesToml::test_live_reread_no_caching_across_routes_toml_mutation PASSED
```

First pass: `routes.toml` declares `sonnet5-high`, handoff `tier:
sonnet5-high` -> no L14 finding. Same process, same path, `routes.toml`
overwritten in place to rename the tier to `new-tier-name` (`str.replace`
on the fixture text, no code change, no restart). Second pass on the
IDENTICAL handoff object: exactly one L14 ERROR now. Third pass, a new
handoff with `tier: new-tier-name`: no finding. Proves `_check_l14` never
caches/memoizes -- confirmed independently by reading `Routes.load()`
itself (`src/nyxloom/config.py:690-715`): a plain `tomllib.loads(p.
read_text(...))` on every call, no `@lru_cache`, no class/module-level
cache attribute, at this `input_revision`.

### O6 -- real host-filesystem sweep (run outside any container, per the handoff's explicit instruction)

```
$ PYTHONPATH=src python3 -m nyxloom.cli lint nyxloom-trove/handoffs/*.md
nyxloom-trove/handoffs/CORE-REDESIGN-SESSION-HANDOFF-2026-08-03.md:- L1 error parse/schema error: missing leading '---'
nyxloom-trove/handoffs/CORE-REDESIGN-SESSION-HANDOFF-2026-08-04.md:- L1 error parse/schema error: missing leading '---'
$ echo $?
1
```

Run from the worktree root with `PYTHONPATH=src` (not the installed
`nyxloom` console script -- `pip show nyxloom` shows it resolves to a
stale site-packages build, `0.3.1.dev1263+gf3b89f46`, a different commit
than this worktree's HEAD; confirmed `PYTHONPATH=src python3 -c "from
nyxloom import lint; print(lint._check_l14)"` resolves to THIS worktree's
function before relying on this invocation). Both CORE-REDESIGN session
notes have no YAML frontmatter at all (`missing leading '---'`, an L1
error, pre-existing and unrelated to L14 -- unchanged since
FIX-VERIFICATION.md's round-1 confirmation). `nyxloom-P100-tier-routes-
toml-validation.md` itself -- this package's own handoff, `tier:
luna-high` -- produces **no output line at all**, i.e. zero findings of
any rule, confirming it is fully lint-clean (not merely "no L14 finding")
against the real, host-scoped `routes.toml`
(`~/.local/state/nyxloom/routes.toml`, confirmed to declare
`[tiers.luna-high]` among its eight keys). The non-zero exit code (1) is
caused entirely by the two pre-existing L1 findings on the frontmatter-
less notes, not by any L14 finding. **O6 passes; `escalate_if` #2 did not
fire** (no OTHER handoff in the live directory carries an invalid tier).

## Blocking finding

**Gate mechanics note first:** the real containerized gate
(`./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-nl2
tester-unified`) was **not run**. `docker ps` at the start of this session
showed a `tester-unified:local` container already running for a
concurrent package (`assay-wave-d-v10`, up 11+ minutes) plus an unrelated
container; the estate's standing host-load rule caps gate containers at
ONE at a time across all agents on this shared host. In its place, `pytest
tests -n auto -q` was run directly on the worktree's own shell -- the
exact `argv` `assay.toml [lanes.tester-unified]` uses, minus `--cov`/
`--cov-report` (which affect coverage reporting, not pass/fail) -- as the
`tests-pass` assert's equivalent signal. All O1-O5 evidence above is from
this local run plus targeted `pytest` invocations, not a `run-gate.py`
verdict JSON.

That full local run surfaced, on top of this package's own new tests
(all passing), exactly one FAILING pre-existing repo-wide
self-consistency test caused by this package's own in-scope diff:

```
$ pytest tests/test_core_characterization.py -q
...
E       AssertionError: inventory sizes have drifted; re-measure and update:
E         src/nyxloom/lint.py: recorded 1,112, actual 1262 (tolerance 126)
E       assert not ['src/nyxloom/lint.py: recorded 1,112, actual 1262 (tolerance 126)']
FAILED tests/test_core_characterization.py::test_inventory_sizes_are_within_the_declared_tolerance
```

`nyxloom-trove/reports/CORE-REDESIGN-OWNERSHIP-INVENTORY-2026-08-02.md:119`
records `src/nyxloom/lint.py | 1,112 | CR-01: document-truth contradiction
rule is a standing gate, not a cleanup script`. This package's Work item 3
(the ONLY authorized non-test source edit to `lint.py`) grows the file
from its pre-P100 baseline of 1211 lines to 1262 (confirmed via `wc -l`
before and after each `lint.py` commit) -- past the row's ±126-line
(10%, floor 40) tolerance from the recorded 1,112.

**This is not fixable inside `scope.touch`.** The only remedy --
correcting the recorded count on `CORE-REDESIGN-OWNERSHIP-INVENTORY-
2026-08-02.md`'s `lint.py` row -- requires editing a file `scope.touch`
does not list. This matches `escalate_if` #1 verbatim: "any touched
non-test file outside this list needs an edit to keep the gate green (a
reverse-dependency this carve's sweep missed)."

Two things worth naming for whoever resolves this:

1. **The four carve-review rounds never caught this** because none of
   them ran the repo's full local test suite against a real L14
   implementation (all four rounds worked from source-reading and
   targeted checks, per their own text) -- this reverse dependency only
   surfaces once `lint.py` actually grows by an implementation-sized
   amount, which a review of the frozen HANDOFF TEXT alone cannot
   exercise.
2. **The tolerance was already nearly exhausted before this package
   touched the file at all**: pre-P100, `lint.py` was 1211 lines against
   a recorded 1,112 (99 of 121 allowed lines already used, ~82%). Any
   reasonably-documented, reasonably-tested L14 implementation -- not
   specifically this one -- was very likely to trip this budget once the
   also-required `# census:` tag (a real, independent, in-scope fix
   already applied -- see LOG `52dad492`) added its own few lines.
   Compressing this implementation's docstrings/comments below the rest
   of the file's own documentation density, purely to dodge an unrelated
   accounting test, was considered and rejected: that would be exactly
   the kind of "silently narrow... or route around" move the BLOCKED
   protocol exists to prevent, and it would degrade code quality
   inconsistently with every other `_check_lNN` function in this file.

A directly analogous precedent already exists in this repository:
`nyxloom-trove/archive/nyxloom-P48-assay-gate-LOG.md`'s "Why this is
BLOCKED, not worked around" section, where an in-scope, correct change
tripped a different pre-existing self-consistency test
(`TestConfigLintSchema::test_repos_own_config_no_findings`) whose fix
also fell outside that package's `scope.touch`/`scope.forbid`, and the
implementer stopped rather than route around it. The coordinator resolved
that one with an explicit, out-of-band correction; this finding is
presented for the same kind of resolution.

**`BLOCKED: src/nyxloom/lint.py's authorized L14 growth (1211 -> 1262
lines) exceeds nyxloom-trove/reports/CORE-REDESIGN-OWNERSHIP-INVENTORY-
2026-08-02.md's recorded tolerance for that file (1,112 recorded, ±126
allowed), failing tests/test_core_characterization.py::
test_inventory_sizes_are_within_the_declared_tolerance -- part of the real
tester-unified gate's "tests-pass" assert. The fix (updating the recorded
line count) requires editing a file outside this package's scope.touch,
matching escalate_if #1 exactly. Work items 1-4 are otherwise complete;
O1-O6 all independently pass. Awaiting a scope decision (widen
scope.touch to include the INVENTORY file's one-line remeasurement,
authorize an out-of-band correction as nyxloom-P48's own BLOCKED
precedent received, or another disposition) before Work items 5-6 and a
real gate run can proceed.`**
