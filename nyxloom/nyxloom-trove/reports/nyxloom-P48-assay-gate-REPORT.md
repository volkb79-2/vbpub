# nyxloom-P48-assay-gate -- REPORT

Status: **BLOCKED** (O2's "exit 0" cannot be reached today -- see "Current
blocker" below). Tip commit: `da9ce8904fe408aef86f19b7ecb1e130d89ee052`
(branch `feat/nyxloom-P48-assay-gate`). W1-W5 are implemented per the
handoff, with one controller-authorized correction (dropping the unsupported
`"assay-verdict"` assert token, detailed below). O1, O3, and O4 all PASS with
full positive+negative evidence. O2 cannot reach a real green, but for a
reason that is now confirmed to be entirely pre-existing and unrelated to
this package (nyxloom's own current baseline already fails 2 of its own
tests, independent of gates/asserts/coverage) -- not a defect in this
package's own diff.

## Summary of the two-round history

**Round 1** (commit `2c00fe7d`): implemented W1-W5 exactly as the handoff
locked them, ran the live gate, got `FAIL/COMMAND_FAILED` exit 1. Root cause:
the handoff's locked W4 value `asserts = [..., "assay-verdict"]` is rejected
by nyxloom's own (untouched, `scope.forbid`) `asserts` schema enum in
`src/nyxloom/schemas/nyxloom-config.schema.json` (which only allows
`tests-pass|changed-line-coverage|mutation|canary-verified`), so nyxloom's
own dogfood self-lint test
(`tests/test_lint.py::TestConfigLintSchema::test_repos_own_config_no_findings`)
failed CFG1. Filed BLOCKED per the handoff's own `escalate_if` ("a needed
change falls outside scope.touch, or requires touching a forbidden file").

**Round 2** (commit `da9ce890`): the coordinator reviewed the finding,
confirmed it was a genuine carve defect (NL-1's proposed contract and ciu's
copy were both aspirational/unvalidated on this point), and authorized the
fix within already-granted scope: drop `"assay-verdict"` from
`nyxloom-trove/nyxloom.toml [gates.tester-unified].asserts` (leaving
`["tests-pass", "changed-line-coverage", "canary-verified"]`), with a
comment noting the omission is deliberate. Verified locally
(`pytest -k test_repos_own_config_no_findings` passes) and via a full live
gate re-run: **CFG1 is confirmed gone** -- the verdict's `result_stdout_tail`
no longer names `test_repos_own_config_no_findings` as a failure. However
the live gate still exits 1, because nyxloom's OWN baseline currently fails
2 *other*, pre-existing tests (`TestL10Size::test_large_handoff_warning`,
`TestL10Size::test_huge_handoff_error`) that this package's diff never
touched and cannot fix (both live under forbidden `tests/`/`src/`). See
"Current blocker" below.

## Oracle evidence table (final)

| Oracle | Status | Evidence |
|---|---|---|
| **O1-vendor-integrity** (positive) | **PASS** | `cd tools/assay && sha256sum -c assay-4.0.0.pyz.sha256` -> `assay-4.0.0.pyz: OK`. `python3 tools/assay/assay-4.0.0.pyz --version` -> `assay 4.0.0`. Also confirmed inside both live gate runs' own preflight log line `assay-4.0.0.pyz: OK` before the assay `run` step started. |
| **O1-vendor-integrity** (negative) | **PASS** | Byte-flip on a SCRATCH COPY (`/tmp/.../o1-negative/assay-4.0.0.pyz`, never committed): `sha256sum -c assay-4.0.0.pyz.sha256` -> `assay-4.0.0.pyz: FAILED` / `sha256sum: WARNING: 1 computed checksum did NOT match`, non-zero exit under `set -e` -- no test executed after it. |
| **O2-assay-verdict** (positive) | **FAIL (pre-existing, out-of-scope cause)** | Round 1: clean tree confirmed, live run -> exit 1, `COMMAND_FAILED`, CFG1 (this package's own defect, since fixed). Round 2 (after the fix, commit `da9ce890`): clean tree confirmed again, live run re-executed for real (verified mid-run via `docker ps` -- fresh container `run-gate-vbpub-tester-unified-1162362-1788317606` "Up" -- and `docker exec <cid> ps aux` showing the genuine assay + pytest + 7 xdist worker processes accumulating CPU time), waited on with a foreground blocking poll (not a passive pause). Result: **exit 1 again** (`EXIT_CODE:1`). Verdict read as a SEPARATE `cat` step: `.assay/verdict-tester-unified.json` -> `"commit": "da9ce8904..."`, `"outcome": "FAIL"`, `"reason_code": "COMMAND_FAILED"`, `"exit_code": 1`. Claims: R0 = FAIL/COMMAND_FAILED; R1 = PASS (coverage pct 100.0, trivially correct -- an empty diff under `source_roots=["src"]`). `result_stdout_tail` now shows ONLY 2 failures (`TestL10Size::test_large_handoff_warning`, `TestL10Size::test_huge_handoff_error`) -- CFG1/`test_repos_own_config_no_findings` is confirmed gone. Both residual failures are pre-existing and unrelated (see "Current blocker"). Does not meet the oracle's literal "exits 0 ... R0 PASS" text, for a reason outside this package's diff. |
| **O2-assay-verdict** (negative) | Not executed | The coverage-artifact-absence throwaway-rerun scenario was not attempted: R0 already fails for reasons unrelated to coverage/judge behavior in both rounds, so there is no clean R0-PASS baseline from which to isolate the coverage-absence variation meaningfully. Left for the reviewer/controller once nyxloom's own baseline suite is green. |
| **O3-config-integrity** (positive) | **PASS** | `python3 tools/assay/assay-4.0.0.pyz lanes` (host, real `assay.toml`) -> loads and prints the lane inventory with no refusal. `python3 -c "import tomllib; ..."` confirmed both `assay.toml` and `run-gate.toml` parse as valid TOML. Neither live gate run (round 1 or round 2) ever raised `BAD_LANE_CONFIG` for either file. |
| **O3-config-integrity** (negative) | **PASS** | In-place, immediately-reverted uncommitted edit of the real `assay.toml` (`fail_under = 100.0` -> `fail_under = "100"`): `assay ... lanes` -> `assay: ERROR/BAD_LANE_CONFIG: .../assay.toml: lane 'tester-unified': 'judge.fail_under' must be a number, got str`, exit 2, before any test ran. Reverted with `git checkout -- assay.toml`; `git status --short` / `git diff --stat assay.toml` both confirmed empty afterward. (A first attempt via a separate `/tmp` scratch copy + `--file` produced a non-representative `BAD_LANE_CONFIG` about a missing `src/` sibling instead -- noted as a confound, not a defect; the in-place-revert form isolates the intended `fail_under` type check cleanly.) |
| **O4-no-regression** | **PASS** | `git diff a74bc6f6 -- src/nyxloom/coverage_gate.py` and `... mutation_gate.py` both empty (0 lines) -- byte-identical to `input_revision`, still true after the round-2 fix (that fix only touched `nyxloom-trove/nyxloom.toml`, in `scope.touch`). Neither `test_coverage_gate.py` nor `test_mutation_gate.py` appears among any of the reported pytest failures across both rounds. |

## Current blocker: pre-existing baseline test failures (not a carve defect in this package)

`_check_l10` (`src/nyxloom/lint.py:1078`, untouched by this package) hardcodes
its size thresholds at 10000 tokens (warning) / 18000 tokens (error). The two
still-failing tests' own docstrings say `"Test L10 warning for handoff over
6k tokens"` / `"Test L10 error for handoff over 12k tokens"` and construct
fixtures of 6250 / 12250 tokens respectively -- both UNDER the code's actual
10000/18000 thresholds, so neither ever fires and both assertions fail.
Confirmed:
- `git diff --stat a74bc6f6 -- tests/test_lint.py` is empty -- the test file
  is byte-identical to `input_revision`; nothing in this package's diff
  touched it.
- Both failures reproduce independently on host
  (`PYTHONPATH=src python3 -m pytest tests -k "test_large_handoff_warning or
  test_huge_handoff_error" -q`), using synthetic `tmp_path`-based fixtures
  that never read any file this package edited (`assay.toml`, `run-gate.toml`,
  `nyxloom-trove/nyxloom.toml`).
- This is precisely the gap the already-filed `NL-3` backlog entry tracks:
  *"L10 handoff-size thresholds are hardcoded constants, need a per-project
  nyxloom.toml override"* -- unrelated to gates/asserts/coverage config
  entirely.

Because assay's R0 claim is "the whole declared argv command exits 0," and
that argv is nyxloom's full `pytest tests -n auto ...` suite (unchanged from
before this package, and correctly so -- narrowing it would be an
undisclosed, unauthorized scope change), **any** R0-judged gate on this
worktree's current HEAD -- old self-judged or new assay-judged, this package
present or absent -- fails today, because nyxloom's own baseline test suite
is not currently green. This is not something introduced, causable, or
fixable by this package: fixing `_check_l10`'s thresholds lives in `src/`
and fixing/adjusting the two tests lives in `tests/`, both `scope.forbid`.

No further attempt was made to route around this (e.g. no test-selection
`-k` filter was added to `assay.toml`'s argv, since the handoff locks that
argv verbatim and doing so would silently narrow what the gate actually
proves -- an unauthorized, undisclosed product decision).

## Files touched

- `nyxloom/.gitignore` (W1)
- `nyxloom/assay.toml` (new, W2)
- `nyxloom/run-gate.toml` (W3)
- `nyxloom/nyxloom-trove/nyxloom.toml` (W4, plus the round-2 correction)
- `nyxloom/nyxloom-trove/backlog/NL-1-assay-backed-implementation-gate-pinned-pyz-judge-replaces-cove.md` (W5)
- `nyxloom/nyxloom-trove/backlog/INDEX.md` (W5, regenerated)
- `nyxloom/tools/assay/assay-4.0.0.pyz` + `.sha256` (tracked as part of the
  W1 un-ignore; content untouched/still sha256-identical to the carver's drop)
- `nyxloom/nyxloom-trove/reports/nyxloom-P48-assay-gate-LOG.md`
- `nyxloom/nyxloom-trove/reports/nyxloom-P48-assay-gate-REPORT.md` (this file)

## Recommendation for the controller

This package's own scope is now internally consistent and its own defect
(round 1's CFG1 finding) is fixed and verified. The residual non-green is
nyxloom's own pre-existing baseline defect (`NL-3`), which predates and is
independent of this package. Options for the controller:
(a) accept this package as complete on its own terms (config wiring correct,
its own contribution to the gate's failure eliminated) and sequence NL-3's
fix separately/first, re-running this same live gate afterward for a true
exit-0 confirmation; or (b) explicitly fold a minimal NL-3 fix into this
package's scope (a scope amendment, the controller's call, not this
implementer's). Either way, a genuine `exit 0` / R0+R1 PASS live-gate run for
NL-1 cannot happen until `NL-3` (or an equivalent fix to `_check_l10` /
the two tests) lands.
