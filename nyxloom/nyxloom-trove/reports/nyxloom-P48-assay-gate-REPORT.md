# nyxloom-P48-assay-gate -- REPORT

Status: **BLOCKED** (see BLOCKED rule, handoff frontmatter `escalate_if`).
Tip commit: `345944cb623608b85f7f53429af02b33f31769c0` (branch
`feat/nyxloom-P48-assay-gate`). W1-W5 are implemented exactly as locked; the
package cannot reach a real green live-gate run for a reason outside this
implementer's scope to fix (see O2 below).

## Summary

W1 (`.gitignore`), W2 (`assay.toml`), W3 (`run-gate.toml`), and W4
(`nyxloom-trove/nyxloom.toml`) were written byte-for-byte per the handoff's
locked content. W5's backlog-CLI steps (`carved_handoff` stamp,
`set-status NL-1 carved`, `backlog index`) all succeeded. `coverage_gate.py`
and `mutation_gate.py` are untouched (O4 holds). The live gate
(`./run-gate.py --worktree ... tester-unified`) ran for real (verified via
`docker ps`/`docker exec ps aux` mid-run, not assumed) and exited 1:
`FAIL/COMMAND_FAILED`. Root cause of the blocking failure: nyxloom's own
`asserts` JSON-schema enum (`src/nyxloom/schemas/nyxloom-config.schema.json`,
untouched, forbidden) does not contain `"assay-verdict"`, the exact locked
value W4 requires writing into nyxloom's own config -- so nyxloom's own
dogfood self-lint unit test (`tests/test_lint.py::TestConfigLintSchema::
test_repos_own_config_no_findings`) fails CFG1, failing R0. Fixing it needs
either a `src/` schema edit or a `tests/` test edit (both `scope.forbid`), or
un-locking the W4-pinned value (a product call, not mine to make). This is
the handoff's own named escalation condition, verbatim.

## Oracle evidence table

| Oracle | Status | Evidence |
|---|---|---|
| **O1-vendor-integrity** (positive) | **PASS** | `cd tools/assay && sha256sum -c assay-4.0.0.pyz.sha256` -> `assay-4.0.0.pyz: OK`. `python3 tools/assay/assay-4.0.0.pyz --version` -> `assay 4.0.0`. Also independently confirmed inside the live gate container's own preflight: log line `assay-4.0.0.pyz: OK` before the assay `run` step started. |
| **O1-vendor-integrity** (negative) | **PASS** | Byte-flip on a SCRATCH COPY (`/tmp/.../o1-negative/assay-4.0.0.pyz`, never committed): `sha256sum -c assay-4.0.0.pyz.sha256` -> `assay-4.0.0.pyz: FAILED` / `sha256sum: WARNING: 1 computed checksum did NOT match`, non-zero exit (command list aborted under `set -e` on this exact failure -- no test executed after it, matching the oracle's "fails ... before any test executes, non-zero exit, no test output"). |
| **O2-assay-verdict** (positive) | **FAIL** | Clean tree confirmed (`git status --short` empty) immediately before the run. `cd nyxloom && ./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-P48-assay-gate tester-unified` -> **exit 1** (captured via the backgrounded run's own trailing `echo $?`, a step separate from any piped tail: `EXIT_CODE:1`). Verdict read as a SEPARATE `cat` after the run exited: `.assay/verdict-tester-unified.json` shows `"outcome": "FAIL"`, `"reason_code": "COMMAND_FAILED"`, `"exit_code": 1`. Claims: R0 = FAIL/COMMAND_FAILED; R1 = PASS (coverage pct 100.0, correctly trivial since this diff adds no executable Python lines). The command genuinely ran (verified live via `docker ps` showing the container "Up" and `docker exec <cid> ps aux` showing the real `assay-4.0.0.pyz run tester-unified` process plus 7 active `pytest -n auto` xdist workers accumulating CPU time mid-run) -- not a wrapper artifact. Does NOT meet the oracle's "exits 0 ... R0 PASS and R1 PASS" claim. See "Root-cause of blocking failure" below. |
| **O2-assay-verdict** (negative) | Not executed | Blocked by the same finding as the positive half: a throwaway rerun with coverage.json deleted was not attempted because the package cannot even reach a normal FAIL-for-the-right-reason state to meaningfully vary from (R0 already fails on an unrelated/pre-existing config-schema mismatch before the coverage-absence scenario could be isolated). Left for the controller/reviewer once the schema gap is resolved. |
| **O3-config-integrity** (positive) | **PASS** | `python3 tools/assay/assay-4.0.0.pyz lanes` (host, real `assay.toml`) -> loads and prints the lane inventory with no refusal: `.../assay.toml: schema_version=2, 1 lane` / `tester-unified scope=S1 rigor=R0,R1 enforcement=gate ...`. `python3 -c "import tomllib; ..."` also confirmed both `assay.toml` and `run-gate.toml` parse as valid TOML. The live gate run itself never raised `BAD_LANE_CONFIG` for either file (it reached and ran the declared pytest argv) -- config loads without refusal. |
| **O3-config-integrity** (negative) | **PASS** | In-place, immediately-reverted uncommitted edit of the real `assay.toml` (`fail_under = 100.0` -> `fail_under = "100"`): `python3 tools/assay/assay-4.0.0.pyz lanes` -> `assay: ERROR/BAD_LANE_CONFIG: .../assay.toml: lane 'tester-unified': 'judge.fail_under' must be a number, got str`, exit 2, before any test ran. Reverted with `git checkout -- assay.toml`; `git status --short` / `git diff --stat assay.toml` both confirmed empty afterward -- no corrupted config left in any commit. (A first attempt via a separate `/tmp` scratch copy + `--file` produced a different, non-representative `BAD_LANE_CONFIG` -- "source root 'src' does not exist" -- because that scratch directory has no sibling `src/`; that confound is noted and the in-place-revert form used instead, per the handoff's explicit allowance for either approach.) |
| **O4-no-regression** | **PASS** | `git diff a74bc6f6 -- src/nyxloom/coverage_gate.py` -> empty (0 lines). `git diff a74bc6f6 -- src/nyxloom/mutation_gate.py` -> empty (0 lines). Both byte-identical to `input_revision`. The existing suite was in fact exercised by the live O2 run (same pytest invocation); coverage_gate/mutation_gate related tests were not among the 3 reported failures (only 2 `test_lint.py::TestL10Size` tests and 1 `test_lint.py::TestConfigLintSchema` test failed; nothing in `test_coverage_gate.py`/`test_mutation_gate.py` failed), consistent with "nothing in src/ changed". |

## Root-cause of the blocking O2 failure (full detail)

`result_stdout_tail` in the verdict JSON names three pytest failures:

1. `tests/test_lint.py::TestL10Size::test_large_handoff_warning` -- `assert False`
2. `tests/test_lint.py::TestL10Size::test_huge_handoff_error` -- `assert False`
3. `tests/test_lint.py::TestConfigLintSchema::test_repos_own_config_no_findings`:
   ```
   assert [LintFinding(...)] == []
   Left contains one more item: LintFinding(rule='CFG1', severity='error',
     message="gates.tester-unified.asserts.3: 'assay-verdict' is not one of
     ['te...", ...)
   ```

Failures 1-2 touch no file this package edited and are not investigated
further (out of scope to fix regardless of cause -- both live under
`tests/`, which is `scope.forbid`).

Failure 3 is mechanically caused by this package's own locked W4 edit.
`src/nyxloom/schemas/nyxloom-config.schema.json` (confirmed untouched:
`git diff a74bc6f6 -- src/nyxloom/schemas/nyxloom-config.schema.json` is
empty) declares the `asserts` array's `items.enum` as exactly:
```json
["tests-pass", "changed-line-coverage", "mutation", "canary-verified"]
```
The handoff's W4 locks `asserts = ["tests-pass", "changed-line-coverage",
"canary-verified", "assay-verdict"]` for nyxloom's OWN
`nyxloom-trove/nyxloom.toml [gates.tester-unified]` -- mirroring
`ciu/nyxloom-trove/nyxloom.toml`'s identical use of `"assay-verdict"`. But
nyxloom is the one project whose OWN suite hard-lints its OWN config file
for zero findings (`test_repos_own_config_no_findings`); ciu carries no
equivalent self-check, so the same locked value never surfaces this gap
there. Writing the locked value is therefore both correct-per-handoff and
guaranteed to fail nyxloom's live gate, given the current (unmodified,
forbidden) schema.

Three theoretically possible fixes are all foreclosed by scope:
1. Add `"assay-verdict"` to the schema enum -- touches `src/` (`scope.forbid`).
2. Adjust the failing test -- touches `tests/` (`scope.forbid`).
3. Drop `"assay-verdict"` from the `nyxloom.toml` line -- the FILE is in
   `scope.touch`, but the VALUE is explicitly locked by the handoff ("every
   value below is locked ... If you find one anyway, that is a carve defect:
   STOP and write BLOCKED, do not invent the missing value"); silently
   dropping a pinned assert to force a green is exactly the kind of
   unauthorized product call the BLOCKED rule reserves for the controller.

This is the handoff's own `escalate_if` condition, verbatim: "the new
assay-judged gate cannot reach a real green on this worktree's clean HEAD for
a reason your diff cannot fix" AND "a needed change falls outside
scope.touch, or requires touching a forbidden file."

## Files touched

- `nyxloom/.gitignore` (W1)
- `nyxloom/assay.toml` (new, W2)
- `nyxloom/run-gate.toml` (W3)
- `nyxloom/nyxloom-trove/nyxloom.toml` (W4)
- `nyxloom/nyxloom-trove/backlog/NL-1-assay-backed-implementation-gate-pinned-pyz-judge-replaces-cove.md` (W5)
- `nyxloom/nyxloom-trove/backlog/INDEX.md` (W5, regenerated)
- `nyxloom/tools/assay/assay-4.0.0.pyz` + `.sha256` (tracked as part of the
  W1 un-ignore; content untouched/still sha256-identical to the carver's drop)
- `nyxloom/nyxloom-trove/reports/nyxloom-P48-assay-gate-LOG.md` (this
  package's LOG)
- `nyxloom/nyxloom-trove/reports/nyxloom-P48-assay-gate-REPORT.md` (this file)

## Recommendation for the controller

Either (a) file a follow-up backlog entry against nyxloom's own schema
(`src/nyxloom/schemas/nyxloom-config.schema.json`) to add `"assay-verdict"`
to the `asserts` enum before this package can land, sequenced ahead of or
alongside this one, or (b) make an explicit D-numbered product call on
whether nyxloom's own adoption should use a different assert token than
ciu's `"assay-verdict"` precedent. Neither is this implementer's call to
make.
