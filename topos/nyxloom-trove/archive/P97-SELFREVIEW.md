# P97-SELFREVIEW — Final adversarial self-review

## Acceptance

| Oracle | Result | Evidence |
|---|---|---|
| O1: every target exact 100% statements and branches | PASS | Two full xdist JSON checks: 16/16, no missing lines or branches |
| O2: behavioral tests, no percentage painting | PASS | Exact outputs/errors/state; only external boundaries replaced |
| O3: failure sensitivity | PASS | P96 ledger and two independent `CHANGES_REQUIRED` reviews exposed every ineffective/absent test before final closure |
| O4: two clean exact-gate runs and parity | PASS | 1825 passed twice; all target missing sets empty |
| O5: explicit tools package and trustworthy assertions | PASS | `tools/__init__.py`; post-P96 canary verdict `TRUSTWORTHY` |

## Adversarial checks

- Removed, rather than excluded, two paths proven unreachable or redundant.
- Collector coverage uses a real cgroup/DAMON fixture and verifies that the
  selected structured block survives.
- Textual is mocked only at the screen-dismiss boundary; the action under test
  is real and its exact result is asserted.
- Paddr refusal tests execute before sysfs writes and assert exact domain
  exceptions.
- No exception is swallowed, no source unit is mocked, and no test merely
  checks non-`None` to obtain coverage.
- No `# pragma: no cover`, omit rule, dependency change, or evaluator change.
- All changed files are inside the handoff touch set.
- `git diff --check` is clean.

## Prior review resolution

F1–F9 and F10–F17 are resolved. In particular, the previously deferred
collector, Textual, and paddr paths are now exercised; sparkline and registry
dead paths are removed; ring and UTF-8 edge paths are directly asserted; and
the canary assertion is backed by the real control-path verdict.
