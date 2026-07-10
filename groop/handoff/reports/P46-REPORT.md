# P46 — Admin Action Execution Kernel — Correction Report

## Summary

Corrected the P46 execution boundary after controller security review. The
production CLI now executes only validated Docker/systemd start, stop, and
restart plans after admin mode, exact confirmation, UID 0, timeout, immutable
argv, and mandatory durable audit gates pass. Refusals invoke neither audit nor
runner work when the refusal occurs before the audit gate.

## Corrections

- Added a root gate (`os.geteuid() == 0`) before audit/runner access. Root and
  identity checks are injectable only through the API fixture parameters; the
  CLI exposes no override.
- Made execution audit mandatory. The CLI has no `--audit-log`; it uses the
  fixed absolute default `/var/log/groop/actions.jsonl`. An absolute injected
  `audit_path` remains available to API fixtures only.
- Hardened audit creation and append with no-follow directory traversal,
  regular-file checks, private/sticky parent checks, `O_NOFOLLOW | O_APPEND |
  O_CREAT`, mode `0600`, bounded JSONL, stable `(uid, user)` identity, injected
  clock, and `fsync()` before the runner. Confirmation text is not logged.
- Closed audit handles on all pre/post paths. A post-audit failure returns the
  typed `audit_failure` outcome while preserving `action_outcome` and the
  return code/output from the mutation attempt.
- Changed catalog builders to fixed absolute `/usr/bin/docker` and
  `/usr/bin/systemctl` paths. No PATH lookup or arbitrary argv is accepted.
- Replaced unbounded `subprocess.run(capture_output=True)` with a selector-based
  pipe drainer that retains only a bounded prefix and continues draining excess
  output. UTF-8 decoding uses replacement; timeout is finite, positive, and
  limited to 30 seconds.
- Converted injected runner returns, `TimeoutExpired`, `OSError`, and other
  runner exceptions into bounded typed results and always attempted post-audit
  recording after the pre-audit succeeded.
- Tightened Docker names to start alphanumeric and tightened systemd units to
  safe, recognized suffixed forms. Preview and execution share the frozen
  `ActionPlan`/catalog contract; execution validates it again immediately
  before the runner.
- Kept the execution allowlist at exactly six Docker/systemd start/stop/restart
  kinds. `set-property`, kill, update, CIU, TUI actions, and daemon RPC remain
  outside this mutation boundary.

## Tests

`groop/tests/test_actions.py` now has 129 passing tests. The correction tests
cover root/admin/confirmation ordering, no runner/audit on refusal, fixed and
injected audit policy, symlinks, non-regular/broad-permission targets, parent
handling, pre/post audit failure, timeout rejection including NaN/infinity,
runner exceptions, huge output, exact absolute argv, structural no-`shell=True`,
invalid names, immutable-plan revalidation, stable identity, and absence of
confirmation text from audit records.

## Validation

```text
PYTHONPATH=groop/src /workspaces/vbpub/.venv/bin/python -m pytest groop/tests/test_actions.py -q
129 passed in 0.45s

PYTHONPATH=groop/src /workspaces/vbpub/.venv/bin/python -m pytest groop/tests -q
532 passed, 1 skipped in 48.77s

mapfile -d '' pyfiles < <(find groop/src/groop groop/tests -name '*.py' -print0)
/workspaces/vbpub/.venv/bin/python -m py_compile "${pyfiles[@]}"
# compiled 97 Python files; exit 0
```

The full-suite and full-source compile results are recorded in this report and
log after the final validation run.

Controller review also requires the root-check seam to return the literal
boolean `True` and kills/reaps a spawned child if bounded pipe draining fails
unexpectedly, preventing truthiness bypass and orphan processes.

Post-merge controller validation with P44 on main passed the combined focused
regression (`151 passed`) and full suite (`554 passed, 1 skipped`).

## Scope and known limitations

- The fixed production audit path must be provisioned root-owned and mode
  `0600` (the execution code creates `/var/log/groop` privately when running
  as root). Fixture paths are still subject to no-follow, regular-file, and
  restrictive-permission checks.
- Live Docker/systemd mutation was not run. All tests use injected runners and
  assert exact argv without host mutation.
- The TUI `k` action and mutation daemon RPC remain disabled/out of scope.
- Only the six explicit allowlisted kinds are executable; future catalog kinds
  require a deliberate allowlist change.

## Files changed

| File | Change |
|---|---|
| `groop/src/groop/actions/catalog.py` | Fixed absolute executables and shared strict target validator |
| `groop/src/groop/actions/execute.py` | Root-gated execution, secure mandatory audit, bounded runner, typed failure handling, immutable-plan revalidation |
| `groop/src/groop/actions/preview.py` | Uses the shared catalog validator |
| `groop/src/groop/actions/__init__.py` | Exports corrected execution contract symbols |
| `groop/src/groop/cli.py` | Removes arbitrary execute audit-path option |
| `groop/tests/test_actions.py` | Updates absolute-argv expectations and adds adversarial correction coverage |
| `groop/README.md` and `groop/docs/*` | Corrected operational and readiness claims |
| `groop/handoff/reports/P46-LOG.md` | Resumability log for the correction |

## Proposed contract changes

None. The injectable runner, clock, identity, root check, and fixture audit
path are additive execution-module test seams and are not CLI inputs.
