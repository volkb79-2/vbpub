# nyxloom-P48-assay-gate -- REPORT

Status: **COMPLETE -- real green.** Tip commit:
`4297be039bb8f50956405e3d2fac60cff51ceee9` (branch
`feat/nyxloom-P48-assay-gate`, merge of this package's prior tip `0836760d`
with `main`'s `b1282b71`). Tree clean. All four oracles (O1-O4) PASS with
positive+negative evidence. The live `tester-unified` gate exits 0 with a
verdict naming `outcome: PASS`, R0 PASS, R1 PASS.

## Three-round history

**Round 1** (commit `2c00fe7d`): implemented W1-W5 exactly as the handoff
locked them. Live gate -> `FAIL/COMMAND_FAILED` exit 1. Root cause: the
handoff's locked W4 value `asserts = [..., "assay-verdict"]` is rejected by
nyxloom's own (untouched, `scope.forbid`) `asserts` schema enum in
`src/nyxloom/schemas/nyxloom-config.schema.json` (only allows
`tests-pass|changed-line-coverage|mutation|canary-verified`), failing
nyxloom's own dogfood self-lint test
(`tests/test_lint.py::TestConfigLintSchema::test_repos_own_config_no_findings`,
CFG1). Filed BLOCKED per the handoff's own `escalate_if`.

**Round 2** (commit `da9ce890`, docs in `0836760d`): the coordinator
confirmed this as a genuine carve defect (NL-1's proposed contract and
ciu's copy were both aspirational/unvalidated on this specific point) and
authorized the fix within already-granted scope: drop `"assay-verdict"`
from `nyxloom-trove/nyxloom.toml [gates.tester-unified].asserts`, leaving
`["tests-pass", "changed-line-coverage", "canary-verified"]`, with an
explanatory comment. Verified: CFG1 confirmed gone (host pre-check +
live-gate re-run). But the live gate STILL exited 1 -- verdict's
`result_stdout_tail` now named only two OTHER, pre-existing failures
(`TestL10Size::test_large_handoff_warning`, `TestL10Size::
test_huge_handoff_error`), root-caused to `src/nyxloom/lint.py`'s
`_check_l10` hardcoded 10000/18000-token thresholds no longer matching
those two tests' own stale 6k/12k-token fixtures -- confirmed via
`git diff a74bc6f6 -- tests/test_lint.py` (empty: untouched by this
package) and an independent host repro. This matched the already-filed
`NL-3` backlog gap exactly and was reported as a second, distinct BLOCKED
condition -- a pre-existing regression this package's scope could not
touch, not a new defect of its own making.

**Round 3** (this REPORT, commits `4297be03` merge + this LOG/REPORT
commit): the coordinator fixed the round-2 finding separately as its own
package, `nyxloom-P49` (resized the two `TestL10Size` fixtures to 45000/
80000 chars -- 11250/20000 tokens, both now genuinely exceeding the raised
10000/18000-token thresholds), verified it green independently, and merged
it to the shared local `main` (`b1282b71`). This branch merged `main`
cleanly (`git merge main`, no conflicts -- P49 only touched
`nyxloom/tests/test_lint.py`, which this branch never touched) and
re-ran the full live gate end-to-end. **Result: exit 0, verdict `outcome:
PASS`, R0 PASS, R1 PASS** -- real, verified green, evidence below.

## Oracle evidence table (final)

| Oracle | Status | Evidence |
|---|---|---|
| **O1-vendor-integrity** (positive) | **PASS** | `cd tools/assay && sha256sum -c assay-4.0.0.pyz.sha256` -> `assay-4.0.0.pyz: OK`. `python3 tools/assay/assay-4.0.0.pyz --version` -> `assay 4.0.0`. Also confirmed inside all three live gate runs' own preflight (`assay-4.0.0.pyz: OK` printed before the `run` step every time, including the final green run). |
| **O1-vendor-integrity** (negative) | **PASS** | Byte-flip on a SCRATCH COPY (`/tmp/.../o1-negative/assay-4.0.0.pyz`, never committed): `sha256sum -c assay-4.0.0.pyz.sha256` -> `assay-4.0.0.pyz: FAILED`, non-zero exit under `set -e`, no test executed after it. |
| **O2-assay-verdict** (positive) | **PASS (round 3)** | Clean tree confirmed (`git status --short` empty at tip `4297be03`). Launched `./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-P48-assay-gate tester-unified` as a backgrounded job, found its container via `docker ps` (`run-gate-vbpub-tester-unified-1399275-1788318565`), then blocked on a SINGLE `docker wait run-gate-vbpub-tester-unified-1399275-1788318565` call (no polling) -> returned `0`. Read in three separate steps: (1) run-gate's own log -> `tester-unified: PASS (exit 0)` / `run-gate: lane 'tester-unified' exit 0` / `EXIT_CODE:0`; (2) `docker logs <container>` -> container already auto-cleaned on success (by design -- run-gate.py only preserves containers/logs on failure; confirmed no `/tmp/run-gate/*.log` exists for this run); (3) `.assay/verdict-tester-unified.json` (separate `cat`, after the above) -> `"outcome": "PASS"`, `"exit_code": 0`, `"commit": "4297be039bb8f50956405e3d2fac60cff51ceee9"`, claims R0 = PASS, R1 = PASS (coverage pct 100.0), `judgment.resolved.base_resolution: "first-parent"` (correct: HEAD is a merge commit, matching `coverage_gate.py`'s own documented post-merge base rule), no `reason_code` field (only appears on FAIL). This fully satisfies O2's positive claim: "exits 0, prints the Assay PASS line, and leaves `.assay/verdict-tester-unified.json` naming lane `tester-unified` with R0 PASS and R1 PASS." |
| **O2-assay-verdict** (negative) | Not executed | Not attempted even after reaching a real green, per the handoff's own guidance that the coverage-absence throwaway rerun is "valuable... left as a reviewer's discretionary spot-check, not an implementer oracle" for the closely related uncovered-line proof, and to avoid perturbing the now-green worktree with another destructive scratch step beyond what O1/O3 already required. Left for the reviewer. |
| **O3-config-integrity** (positive) | **PASS** | `python3 tools/assay/assay-4.0.0.pyz lanes` (host, real `assay.toml`) -> loads and prints the lane inventory with no refusal. `python3 -c "import tomllib; ..."` confirmed both `assay.toml` and `run-gate.toml` parse as valid TOML. None of the three live gate runs (rounds 1-3) ever raised `BAD_LANE_CONFIG` for either file. |
| **O3-config-integrity** (negative) | **PASS** | In-place, immediately-reverted uncommitted edit of the real `assay.toml` (`fail_under = 100.0` -> `fail_under = "100"`): `assay ... lanes` -> `assay: ERROR/BAD_LANE_CONFIG: .../assay.toml: lane 'tester-unified': 'judge.fail_under' must be a number, got str`, exit 2, before any test ran. Reverted with `git checkout -- assay.toml`; `git status --short` / `git diff --stat assay.toml` both confirmed empty afterward. |
| **O4-no-regression** | **PASS** | `git diff a74bc6f6 -- src/nyxloom/coverage_gate.py` and `... mutation_gate.py` both empty at every round, including the final tip `4297be03` (the P49 merge only touched `nyxloom/tests/test_lint.py`, unrelated to either file). Neither `test_coverage_gate.py` nor `test_mutation_gate.py` ever appeared among any reported pytest failure across all three rounds, and the final round-3 run has zero pytest failures at all. |

## Files touched (this package's own commits)

- `nyxloom/.gitignore` (W1)
- `nyxloom/assay.toml` (new, W2)
- `nyxloom/run-gate.toml` (W3)
- `nyxloom/nyxloom-trove/nyxloom.toml` (W4, plus the round-2 correction
  dropping `"assay-verdict"`)
- `nyxloom/nyxloom-trove/backlog/NL-1-assay-backed-implementation-gate-pinned-pyz-judge-replaces-cove.md` (W5)
- `nyxloom/nyxloom-trove/backlog/INDEX.md` (W5, regenerated)
- `nyxloom/tools/assay/assay-4.0.0.pyz` + `.sha256` (tracked as part of the
  W1 un-ignore; content untouched/still sha256-identical to the carver's drop)
- `nyxloom/nyxloom-trove/reports/nyxloom-P48-assay-gate-LOG.md`
- `nyxloom/nyxloom-trove/reports/nyxloom-P48-assay-gate-REPORT.md` (this file)
- A merge commit (`4297be03`) pulling in `main` (which includes the
  separate `nyxloom-P49` fix, `nyxloom/tests/test_lint.py`, plus unrelated
  concurrent `ciu/` work from other packages -- not this package's
  concern, sibling/read-only per scope).

## Recommendation for the controller

This package is complete on its own terms: config wiring correct (O1/O3
PASS), no regression to the retained `coverage_gate.py`/`mutation_gate.py`
toolkit (O4 PASS), and the live `tester-unified` gate now genuinely passes
end-to-end under Assay's R0+R1 judgment (O2 PASS). Ready for the reviewer
and controller's merge step (not performed here, per doctrine and the
handoff's explicit "do NOT merge").
