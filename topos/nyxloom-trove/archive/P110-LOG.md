# P110 log

## Scope and baseline

- Input revision: `6aa902ac`
- Branch: `feat/topos-P110-action-policy-coverage`
- Immutable implementation: `06fe1dc11f107222b4adef9c22813aad9c3ba81e`
- Baseline: 2,088 cases
- Product files: `actions/catalog.py`, `actions/governance.py`

The handoff froze 24 missing lines and 15 missing pairs across the two files.

## Product repairs

`validate_target` previously checked an empty target twice: the shared guard at
lines 156–157 rejects every non-string/empty target before kind dispatch, so
the set-property-specific empty check could never execute. The duplicate two
lines were replaced with an invariant comment. The exact public empty-target
error remains tested, neighboring set-property validation is covered, and the
complete file record is empty.

`build_set_property_preview` previously caught `BaseException`, swallowing
`KeyboardInterrupt` and `SystemExit` from its reader seam. The catch now uses
`Exception`. A complete fallback-plan test covers `RuntimeError`; a separate
test proves `KeyboardInterrupt` propagates after exactly one reader call.

## Test construction

`tests/test_p110_action_policy_coverage.py` adds 22 collected cases covering:

- empty/composite catalog set-property targets;
- defensive execution-allowlist fallthrough;
- exact specialized Docker/systemd target refusals;
- the recognized-digit conversion failure boundary;
- systemctl show transport, return-code, null-value, and success results with
  complete subprocess arguments;
- empty structured unit validation;
- explicit preview persistence; and
- ordinary-versus-operator-interrupt reader behavior.

All systemctl interactions use the subprocess seam; no host mutation runs.

## Discarded attempts

The first complete suite ran while the source/tests were still uncommitted. It
passed 2,110 cases and closed both whole-file records, but the commit-based
changed-line evaluator correctly reported `0/0`. This is not an authoritative
receipt: uncommitted source is outside the merge-base-to-HEAD diff.

After committing, the first immutable preflight used an incorrectly expanded
full commit hash. It exited 97 before pytest and printed the real hash. No gate
work ran and it is not evidence.

## Authoritative receipt command

Both accepted receipts first asserted the exact clean HEAD, then ran in
`tester-unified:local`:

```text
cd /workspaces/vbpub/.worktrees/feat/topos-P110-action-policy-coverage
test "$(git rev-parse HEAD)" = 06fe1dc11f107222b4adef9c22813aad9c3ba81e
test -z "$(git status --porcelain)"
export PYTHONPATH=topos/src:topos
/opt/tester-venv/bin/python -m pytest topos/tests -q -n auto \
  --cov=topos/src/topos --cov-branch \
  --cov-report=json:/tmp/topos-p110-coverage.json
/opt/tester-venv/bin/python topos/tools/coverage_gate.py \
  --repo . --base main \
  --coverage-json /tmp/topos-p110-coverage.json \
  --source topos/src/topos
```

The normalized records for both targets were asserted empty, printed, and
hashed inside the container.

## Receipts

| Run | Pytest | Changed-line floor | Target record hash | Exit |
| --- | --- | --- | --- | ---: |
| 1 | 2,110 passed in 63.26s | 1/1, 100% ≥ 100% | `374dd7751da55ddfd3de60c47a98443b1579177754795798f02621f6898ebcfd` | 0 |
| 2 | 2,110 passed in 64.13s | 1/1, 100% ≥ 100% | `374dd7751da55ddfd3de60c47a98443b1579177754795798f02621f6898ebcfd` | 0 |

Both runs reported empty `missing_lines` and `missing_branches` for both target
files. The intersections with every retained literal line/pair are empty;
catalog lines/pair 188–189 no longer exist as executable coverage objects under
the invariant proof above. Collection arithmetic is exact: 2,088 + 22 = 2,110.
