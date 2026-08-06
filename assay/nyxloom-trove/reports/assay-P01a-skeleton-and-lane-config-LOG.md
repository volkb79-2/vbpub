# assay-P01a — skeleton and the lane config loader — LOG

**Status:** DONE. Gate green.
**Branch:** `feat/assay-P01a-skeleton-and-lane-config`
**Commits:** `c85d610d` (implementation), `c9119092` (closing the last untested
rejection paths). HEAD = `c9119092`.
**Base:** `main` at `61052ae4`.

## Gate

Declared as `[gates.tester-unified]` in `nyxloom-trove/nyxloom.toml`, run in the
foreground against HEAD, `{worktree}` substituted:

```
$ docker run --rm --cgroup-parent=nyxloom-gates.slice \
    -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
    bash -c 'cd /workspaces/vbpub/.worktrees/assay-P01a-skeleton-and-lane-config/assay \
             && export PYTHONPATH=src && /opt/tester-venv/bin/python -m pytest tests -q'
........................................................................ [ 34%]
........................................................................ [ 69%]
..............................................................           [100%]
206 passed in 6.17s
GATE_EXIT=0
```

Coverage of the new source, measured in the same image (not asserted by the
gate, which declares `asserts = ["tests-pass"]` only — deliberately, per
`nyxloom.toml`'s own note):

```
Name                    Stmts   Miss Branch BrPart  Cover
src/assay/__init__.py       9      0      0      0   100%
src/assay/cli.py           40      0      4      0   100%
src/assay/config.py       286      0    140      0   100%
src/assay/errors.py        53      0      4      0   100%
TOTAL                     388      0    148      0   100%
```

No image rebuild was involved (A-O02 held: `tester-unified:local` already
carries the whole closure). Nothing but ignored caches is left in the worktree
after a gate run.

## Delivered

| Work item | File | Notes |
|---|---|---|
| 1 | `pyproject.toml` | zero runtime deps, `test = [pytest, pytest-cov, jsonschema]` (A-056), `setuptools_scm` with `root = ".."`, `tag_regex`, and `fallback_version = "0.1.0"` (A-057) |
| 2 | `.gitignore` | `build/`, `dist/`, `src/*.egg-info/`, caches (A-057) |
| 3 | `src/assay/errors.py` | `Outcome` (6, A-021), `ReasonCode` (closed, A-022/A-050), `AssayError`, `LaneConfigError` |
| 4 | `src/assay/config.py` | the loader; no defaults anywhere |
| 5 | `src/assay/cli.py` | `assay lanes` only; lists and validates, runs nothing, writes nothing (A-054) |
| 6 | `assay.toml` | lane `tester-unified`, `S1`, `["R0"]`, **no `[judge]` table** |

1145 non-test lines added (388 executable statements), 2051 test lines.

## Per-oracle evidence

Each oracle is followed by the *mutation* that was actually applied to the
implementation to prove the test bites. Every mutation below was run and
reverted; the failure counts are real.

### O1 — both directions, required fields, no invented keys

* ACCEPT: `tests/test_config_accept.py` (11 tests). The strongest is
  `test_r0_lane_round_trips_exactly_what_the_file_declared` /
  `..._r1_...`, which assert `Lane.as_declared() == tomllib.loads(text)["lanes"]["package"]`.
  That is the "no key present that the file did not declare" claim as an
  equality against a parser that is not assay's own.
* REJECT: `tests/test_config_reject.py::test_omitting_a_required_field_fails_and_names_it`,
  parametrised over all eight fields. **Each parameter asserts both directions
  in one body** — the untouched template loads, the one-field-lighter mutant
  does not, and the message names the field, the lane and the file.
* Mutation 1 — invent defaults for `budget`/`env`/`allow_argv_append` on
  absence (AGENTS.md §4.2a anti-pattern #1, the shape all four existing copies
  ship): **5 failed**, including three of the eight parametrised cases.
* Mutation 3 — `load_lane_file` raises unconditionally (O1's stated negative,
  "the loader rejects everything"): **152 failed / 32 passed**. Every oracle
  module fails: accept 10/10, reject 32/36, rigor 20/21, source_roots 13/14,
  vocabularies 63/73, cli 8/15, self_lane 6/6.

### O2 — the five judge fields are CONDITIONALLY required

* `tests/test_config_rigor.py` (26 tests) and `tests/test_self_lane.py` (6).
  R0-only-with-no-judge loads; R1-with-all-five loads; R1 minus each of the five
  is rejected by name (parametrised); R2 additionally requires `mutation` and R3
  `canary`, each tested in both directions.
* `tests/test_self_lane.py` is O2's negative made mechanical: it loads assay's
  *own* `assay.toml` with the loader this package ships. The pre-flight's
  finding was that P01's deliverable was a lane its own loader had to reject.
* Mutation 4 — make the five unconditional (i.e. reintroduce exactly that bug):
  **46 failed**, including all six `test_self_lane` tests.

### O3 — `source_roots` resolve against the project root (A-049)

* `tests/test_config_source_roots.py` (14 tests) on a fixture where project root
  ≠ repo root, each having a directory the other lacks.
  `test_the_layout_really_distinguishes_the_two_roots` asserts the fixture is
  meaningful first, so "pkgs is rejected" cannot be true merely because nothing
  called `pkgs` exists.
  `test_repo_relative_root_is_rejected_even_when_cwd_is_the_repo_root` is the
  sharpest form: it stands in the directory where the wrong answer would look
  right.
* Mutation 2 — resolve against `Path.cwd()` instead of the project root: **20
  failed**, including 5 of the 6 cwd-independence cases (the one that passed is
  the parameter where cwd *is* the project root, i.e. exactly where the two
  answers coincide).

### O4 — closed vocabularies, and a budget that is parsed

* `tests/test_config_vocabularies.py` (73 tests). Every member of every
  vocabulary is asserted to LOAD (5 scopes, 4 rigor levels, 2 enforcements)
  before non-members are asserted to fail — the accept half is what a
  reject-everything loader cannot fake.
* `budget`: 9 valid durations asserted to parse to a numeric value, 12 invalid
  ones rejected, and `test_budget_seconds_is_additional_to_the_declaration_not_a_replacement`
  asserting the declared string still round-trips verbatim.
* Mutation 5 — accept the budget string without parsing it (`budget_seconds =
  0.0`): **17 failed**, including all 9 duration cases.

### O5 — dependency purity by AST walk, never grep (A-060)

* `tests/test_dependency_purity.py`. `import_roots()` walks the AST and takes
  the root name of every `Import` / `ImportFrom`, so it is immune to all four
  defects A-060 records: it sees indented imports (it walks, it does not
  line-match), it sees `from x import y` (a node type, not a spelling), it
  never looks at a path, and it does not substring-match module names.
* **The check is proven able to fail**, three ways:
  * `test_the_check_catches_every_import_shape_in_a_tainted_copy` copies the
    real package, injects a module importing `requests` (module level),
    `flask` (from-import) and `boto3` (inside a function), and requires all
    three to be reported.
  * `test_the_check_is_not_fooled_by_the_word_assay_in_the_path` puts the
    tainted copy under `…/assay/src/assay/` — the exact condition that made the
    original grep vacuous — and asserts it is still caught.
  * Mutation 7a — an unreachable `def _never_called(): import boto3` added to
    the real `cli.py`, i.e. the function-level shape that survives at runtime:
    **2 failed**, including `test_the_package_imports_nothing_outside_the_stdlib`.
* Anti-vacuity guard: the real-package test asserts `len(files) >= 4` and that
  the four module names were seen, so an empty offender set cannot mean "the
  glob found nothing".
* Standalone half: `standalone` builds assay's wheel and installs it into a
  fresh venv **offline** (`--no-index`), then imports `assay` and runs the
  installed `assay lanes` console script against a fixture lane file. Plus
  `test_the_built_wheel_declares_no_runtime_requirement`, which reads
  `METADATA` out of the wheel and requires every `Requires-Dist` to be guarded
  by an `extra ==` marker.
* Mutation 7b — declare `dependencies = ["requests>=2"]` in `pyproject.toml`:
  **1 failed + 4 errored** (the offline install fails outright).

## Self-review

### Would each oracle's test fail if the behaviour were removed?

Yes for all five, demonstrated by the mutations above rather than asserted. The
"reject everything" mutant — the negative the handoff singled out — fails 152
of 197 tests, and fails at least one test in every oracle's module.

### What I found wrong in my own work

**The first version of the O5 venv test was partially vacuous, and mutation
testing is what caught it.** It installed the source tree directly with
`--no-build-isolation` and `PYTHONPATH=<a site-packages containing setuptools>`.
Under mutation 7b (declaring `requests>=2` as a runtime dependency) the install
*still succeeded*, because that `PYTHONPATH` also exposed every other
distribution in that directory to pip's resolver, which then considered
`requests` already satisfied. The venv was not "containing only assay" in the
sense the claim needs. Fixed by splitting build and install into two
subprocesses with two environments: the build gets the backend on `PYTHONPATH`
and `--no-deps`; the install runs with a clean environment and `--no-index`, so
there is nothing to fetch and nothing to leak in. This is A-060's lesson one
level over — the check I wrote to prove a claim could not fail — and it was
found only because I mutated the thing the test was supposed to catch.

Coverage of `src/assay` was 96% branch on first pass. The eight uncovered lines
were all real rejection paths with no test (non-table `lanes`, empty
`judge.language`, non-table `judge.coverage`, empty coverage `format`/
`artifact`, non-table `judge.mutation`) plus the `as_declared()` round trip of
the opaque mutation/canary payloads. Closed in `c9119092`; now 100% statement
and 100% branch.

### What is MISSING from the diff that the handoff asked for

Nothing in `## Work`. All six items landed.

One thing the handoff asked for that is **weaker than it reads**: A-057's
`fallback_version` is asserted only as a *declaration*
(`test_pyproject_sets_a_setuptools_scm_fallback_version` checks `root == ".."`
and that `fallback_version` is set). It is not exercised, because
`setuptools_scm` is not installed anywhere in `tester-unified:local` — only
`setuptools` is, in `/usr/local/lib/python3.14/site-packages`. The offline
wheel build therefore never loads the `setuptools_scm` plugin at all and the
built wheel is versioned `0.0.0` rather than `0.1.0`. So the empirical
`LookupError` A-057 records is not reproduced by this suite; the setting is
present and correct for a host that *does* have the pinned build toolchain, and
that is all the test claims. Flagging it rather than dressing it up.

### What I implemented that the handoff did not ask for

* **The whole closed `ReasonCode` enumeration** (all 16 codes, not just
  `BAD_LANE_CONFIG`), with per-outcome membership enforced at `AssayError`
  construction. Justification: A-022/A-050 make the enumeration closed, and one
  home for it stops P01b/P07 each transcribing DESIGN-GUIDE §6. If the
  controller would rather this lived in P01b's `verdict.py`, it is a move, not a
  rewrite — `errors.py` imports nothing.
* **`Lane.as_declared()` / `JudgeConfig.as_declared()`.** ~30 lines. They exist
  to make O1's "no key the file did not declare" mechanical rather than a
  reviewer's eyeball, and P07 will want the same reconstruction for
  `argv_declared` / `env_declared`.
* **`find_lane_file()`** — upward search for `assay.toml` from cwd, so
  `assay lanes` works with no `--file`. DERIVE then FAIL; there is no default
  path.
* **Rejection of unknown keys** in a lane table and in `[…judge]` (but *not*
  inside `[…where]`, `judge.mutation` or `judge.canary`, which are opaque by
  design). Justified by §12's "closed vocabularies, so a loader can reject
  rather than guess": a silently-ignored `allow_argv_appended = true` reads as
  enforced.
* **`schema_version` is required and must equal 1.** Not in O1's list of eight,
  but it is in §12's example file and a lane file whose schema version is
  unknown cannot be validated at all.

### Judgement calls not fully determined by the specification

Each of these is a place I had to choose; all are documented in
`config.py`'s module docstring, and any of them can be reversed cheaply.

1. **R2/R3 requirements are per DECLARED level, not a cumulative ladder.**
   §12 says "R1 makes all five required; R2 **additionally** requires
   `judge.mutation`; R3 **additionally** requires `judge.canary`", which reads
   either as a ladder (R3 ⊇ R2 ⊇ R1) or as per-element requirements over a
   list. I took the second, because `rigor` is a *list* and §6 is explicit that
   a lane declaring `["R0","R1","R2"]` renders one claim per level — a set of
   independently declared methods, not a level number. Under the ladder reading,
   a lane doing canary but not mutation would be forced to declare mutation
   config it never uses. Consequence: R2 requires `language`, `source_roots` and
   `mutation`; R3 requires `language`, `source_roots` and `canary`. Pulling in
   those two is my inference from A-017 — mutation and canary cannot act at all
   without knowing the adapter and the tree — and it is the only part of the
   requirement map not written down somewhere.
2. **`judge.coverage.format` is NOT enumerated here.** §12 lists it as a closed
   vocabulary, but its vocabulary is "a key the parser registry knows" and the
   registry is P03's. Duplicating a format list in `config.py` would recreate the
   four-copies divergence one layer down, so the loader requires a non-empty
   string and P03/P04 must do the cross-check. **This is a real gap until P03
   lands**, and it is the one clause of O4 I did not implement literally.
3. **Surplus judge config for an undeclared level is ALLOWED** (a `mutation`
   table on a lane without R2). The rigor list is the claim; configuration makes
   no claim. Rejecting it would break the ordinary workflow of writing the
   config before declaring the level. There is an A-046 argument the other way.
4. **Rejected as degenerate, without an explicit decision to cite:** an empty
   `rigor` list, a duplicate rigor level, an empty `argv`, an empty
   `source_roots`, an absolute `source_root`, a file with no lanes. Each is
   argued in place; all are small and reversible. The one I am least sure of is
   the empty `rigor` list — a lane declaring no method is degenerate rather than
   wrong.
5. **`assay.toml` declares `env = { PYTHONPATH = "src" }` and
   `env_passthrough = []`.** The gate exports `PYTHONPATH=src` itself, so this
   is the true fact of how the lane runs today; the interpreter path stays in
   `nyxloom.toml` as a fact of WHERE. `budget = "30m"` is read from that file's
   `timeout_seconds = 1800` rather than invented, and
   `test_lane_budget_agrees_with_the_gate_timeout` keeps the two from drifting.

### Decision ids I could not honour as written

None. Every `A-NNN` cited in the handoff (A-048, A-049, A-052, A-053, A-056,
A-057, A-060) is implemented, with the two qualifications recorded above: A-057
is declared but not exercised (no `setuptools_scm` in the image), and §12's
`coverage.format` vocabulary is deliberately deferred to P03's registry rather
than duplicated.

The specification was implementable as written. The session-2 block closed every
ambiguity I would otherwise have hit except the R2/R3 decomposition in (1).

## House style set for P01b…P11

* One canonical artefact per shape (`R0_LANE`, `R1_LANE` in `conftest.py`),
  mutated for the negative direction. The ACCEPT tests and the REJECT tests load
  the *same* text, so a reject-everything implementation fails the accept half of
  every module.
* Mutation helpers (`drop_key`, `set_key`) raise if they mutated nothing — a
  typo'd key name cannot silently produce a no-op mutant.
* One test module per oracle, named `test_<area>_<aspect>.py`, with a module
  docstring naming the oracle and the negative it defends.
* Every rejection message names the file, the lane and the field.
* Round-trip equality against `tomllib` rather than against assay's own parse.
* No wall-clock assertions anywhere; no test asserts a duration or a deadline.

## Left undone / for the next package

* **P01b must not redefine `Outcome`/`ReasonCode`** — import them from
  `assay.errors`. The verdict model is the only remaining owner of the `claims[]`
  envelope (A-055).
* **P03 owes the `coverage.format` cross-check** described in judgement call (2).
* `assay lanes` has no `--json` output. Deliberate: nothing consumes it yet, and
  adding it would be naming for an absent consumer.
* The single warning in the local (non-gate) run is `schemathesis` deprecating a
  `jsonschema` attribute — a devcontainer package, absent from the gate image and
  unrelated to assay.
