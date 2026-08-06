# P01a — successor brief

## Conventions to match

**Errors.** `src/assay/errors.py` owns `Outcome` and `ReasonCode` (both
`StrEnum`), `EXIT_CODES`, `REASON_CODES`. **Import them; never redefine them** —
P01b's verdict model included. Base error is
`AssayError(message, *, outcome, reason_code)`, which validates the pairing at
construction and exposes `.exit_code`. Add a thin subclass per fixed pairing;
`LaneConfigError` (ERROR/BAD_LANE_CONFIG) is the template. `errors.py` imports
nothing, so anything may depend on it.

**Tests.** One module per oracle: `tests/test_<area>_<aspect>.py`, module
docstring naming the oracle *and* the negative it defends.
`tests/conftest.py` exports, and you should reuse: `PROJECT_ROOT` (derived from
`__file__`, asserted); canonical TOML templates `R0_LANE` / `R1_LANE` /
`R1_SOURCE_ROOTS`; `drop_key(text, key)` and `set_key(text, key, raw_toml)`
(both **raise if they mutated nothing**); `lane_table(text)` (a `tomllib` parse —
the independent round-trip oracle); `Project` dataclass + `project` fixture
(`.write()`, `.dir()`, `.file()`).

**The anti-hollow pattern that matters:** ACCEPT and REJECT tests load the *same*
canonical text, and each parametrised reject test asserts the untouched file
loads **in the same body**. Compare round-trips against `tomllib`, never against
assay's own parse. Every error message names file, lane and field; assert all
three.

## Traps

* A hook blocks scripted file edits (sed, Python `write_text`). Mutation-test by
  `Edit`-ing the real file and reverting.
* `/opt/tester-venv` has **no setuptools and no setuptools_scm**. setuptools
  82.0.1 exists only under `sys.base_prefix` (`/usr/local/lib/python3.14/
  site-packages`); derive it, don't hardcode.
* Offline install is **two steps with two environments**:
  `pip wheel --no-build-isolation --no-deps` with `PYTHONPATH=<setuptools dir>`,
  then `pip install --no-index <wheel>` with a **clean** env. Putting that
  PYTHONPATH on the *install* leaks the host site-packages into pip's resolver,
  so a declared runtime dependency is silently "already satisfied". My first
  version was vacuous exactly this way.
* Consequently setuptools_scm never loads: built wheels are version `0.0.0`, and
  `fallback_version` is declared but unexercised.
* `R0_LANE + 'key = 1'` appends into `[lanes.package]`, not top level. For a
  top-level key use `.replace("schema_version = 1", …)`.
* The gate container **has** network. Do not use it.

## Interpretations (candidates for decisions.md)

* Judge requirements are **per declared rigor level, not a cumulative ladder**
  (`JUDGE_FIELDS_BY_RIGOR` in `config.py`); R2 and R3 additionally require
  `language` + `source_roots`. `rigor` is a list of independent methods, so an
  R3-without-R2 lane is not forced to declare mutation config.
* `judge.coverage.format` is **not** enumerated — any non-empty string loads.
  **P03's registry owes the cross-check.**
* Surplus judge config for an undeclared level is allowed.
* Rejected without a citable decision: empty `rigor`, duplicate rigor level,
  empty `argv`, empty `source_roots`, absolute `source_root`, file with no
  lanes, and unknown keys in a lane or `judge` table — `where`, `mutation` and
  `canary` stay opaque.
* `schema_version` is required and must equal `LANE_SCHEMA_VERSION` (1); it is
  distinct from the verdict artifact's version.

## Shaped for you

`Lane.as_declared()` reconstructs the declared TOML (P07: `argv_declared`,
`env_declared`). `parse_duration()` is public for the runner.
`JudgeConfig.source_roots` is declared strings; `source_root_paths` is resolved,
existence-checked directories — P02 wants the latter. `find_lane_file()` searches
upward from cwd. `assay lanes` has no `--json`; add it when something consumes it.
