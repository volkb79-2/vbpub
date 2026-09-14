# run-gate RG-26 sidecar review — round 1

**Date:** 2026-09-14  
**Reviewer:** fresh adversarial review, Luna xhigh  
**Reviewed:** `84fab73ec76ef8cf5c02c81810ca9ae4df915798` against
`be2a44f896f9dd8b3fa8a6947919b1171eecae89`  
**Scope:** `run-gate-project/run-gate.toml` `gate-full` base propagation and
the corresponding README/CHANGES and proof obligations.

## Verdict: REJECT

The shipped command is functionally correct and the existing live acceptance
evidence is genuine, but this commit has one blocking proof gap: it changes the
shipped `gate-full` declaration without adding a committed test that exercises
that declaration. The generic conjunction tests do not protect this exact
configuration line.

## Blocker

### B1 — no construction test for the shipped `gate-full` argv

`run-gate-project/run-gate.toml:142-148` changes the production declaration to
embed `--base {base}` only in the `assay-r1` sub-invocation. The commit changes
no test file (`git diff --name-only 84fab73e^ 84fab73e` lists only
`CHANGES.md`, `README.md`, and `run-gate.toml`).

The closest tests, `tests/test_run_gate.py:5035-5041` and
`tests/test_run_gate.py:6060-6079`, construct their own synthetic conjunction
TOML. They prove the generic renderer and RG-52 shell-safety behavior, but
remain green if the actual project config is reverted to the pre-commit line
with no `{base}`. Likewise, the existing linked-worktree conjunction tests
use another synthetic config (`tests/test_run_gate.py:2478-2502`). Thus the
new config can silently lose the fix while the test suite continues to certify
it.

**Required repair:** add a committed construction-level test that reads the
real project `run-gate.toml` and asserts the `gate-full` argv's ordered
sub-lanes and `{base}` placement (including that the other lane shapes remain
unchanged). The test should be capable of going red if this exact declaration
is reverted. Retain a real acceptance probe for the shell/argv boundary; a
synthetic-config unit test alone is not sufficient for that boundary.

## Findings that pass

- The declaration parses as schema version 1 and has the intended exact shape:
  `bash -c './run-gate.py selftest && ./run-gate.py --base {base} assay-r1 && ./run-gate.py assay-r3'`.
- `R-35`/`R-35b` are respected by the existing implementation: the base is
  resolved before execution, gate-safe validation occurs before shell
  substitution, and the nested assay invocation receives `--request-base`.
  The targeted RG-26/RG-52 tests passed, including injection refusals,
  leading-dash handling, no-base refusal, and unresolved-token defense.
- Non-`gate-full` behavior remains correct. `selftest` still refuses a direct
  `--base` because it is non-delegating; `assay-r3` remains a non-base canary;
  `assay-r1` remains independently base-delegating. The release pointer in
  `cmru.toml` remains `selftest`.
- The linked-worktree path is valid. This checkout has a `.git` gitfile and no
  upstream; `./run-gate.py gate-full --dry-run` without a base refused with
  the contract's `pass --base REF (worktree has no upstream)` message. Both
  documented flag orders with `--base main` produced a dry-run command with
  the substituted nested `--base main`.
- Shell/argv safety is preserved by the existing gate-safe base allow-list and
  targeted RG-52 tests. No product Python file changed in this commit.
- Documentation is accurate for the changed surface: `README.md:87-92`
  names the conjunction and says the token forwards an explicit base to
  `assay-r1`; `CHANGES.md:12-14` records the linked-worktree fix. The wording
  does not claim that `selftest` or `assay-r3` consumes the base.

## Verification ledger

Commands run serially with the review priority:

- `nice -n 19 ionice -c 3 python3 -m pytest tests/test_run_gate.py -q -k 'conjunction_lane_propagates_base_to_every_sub_invocation or conjunction_lane_without_base_and_without_upstream_refuses or command_lane_without_the_token_refuses_base or substitute_worktree_leaves_base_token_when_none_resolved or injecting_base_flag_is_refused_before_any_lane_runs or assay_lane_refuses_the_same_base or leading_dash_is_refused_on_POSITION_grounds'`
  → **16 passed, 973 deselected**.
- TOML parse, exact `gate-full` argv assertion, `py_compile`, and
  `git diff --check` → **PASS**.
- `./run-gate.py gate-full --dry-run` → **PASS**, printed the substituted
  command and started no lane. The no-base form on this no-upstream linked
  worktree → expected **exit 2** refusal.
- `./run-gate.py --list` → **PASS**, all five expected lanes present.
- Existing live acceptance probe `/tmp/rg55-gate-full-base-live.log` is real,
  not a fake-docker construction log: it begins with
  `comparison base main (from --base) → {base} in the lane argv`, runs the
  actual `selftest`, then reports `assay-r1`'s real `--request-base` path and
  `assay-r3`'s two rejected canaries, and ends with
  `SIDEcar_GATE_FULL_EXIT=0`. The same worktree's `.run-gate/history.json`
  independently records `gate-full` PASS at commit `84fab73e` with exit 0.
  This satisfies the live acceptance check, but it is not a committed test
  and therefore does not close B1.

A repeat of the full gate was not launched: the existing probe is sufficient
and the host was already carrying multiple long-running containers with high
memory PSI at review time.

