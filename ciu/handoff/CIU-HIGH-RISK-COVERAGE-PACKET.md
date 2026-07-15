# CIU high-risk coverage packet

Status: ready for dispatch  
Suggested worker: low-cost implementation agent (for example, DeepSeek Flash class)  
Reviewer: a stronger agent must review the diff and the behavioral assertions before merge  
Scope type: bounded, test-only Python work; no product design is delegated

## Mission

Increase CIU's useful test coverage by exercising currently uncovered failure and
dispatch paths in orchestration, diagnostics, health/config validation, and CLI
behavior. Coverage percentage is evidence, not the objective: tests must lock down
documented operator-visible behavior and exit codes.

This is intentionally suitable for a lower-cost agent because the expected behavior
already exists in source and specifications. The worker should add deterministic unit
tests around it, not invent behavior.

## Baseline evidence

Baseline recorded 2026-07-15 from a clean `vbpub/main` checkout:

```text
914 passed
4,908 executable lines
3,708 covered lines
1,200 missing lines
75.55012224938875% total line coverage
coverage gate: 75%
```

Reproduce the detailed baseline with:

```bash
cd /workspaces/vbpub/ciu
python run-ciu-tests.py -q
coverage report --show-missing --sort=cover
coverage json -o /tmp/ciu-coverage.json --pretty-print
```

The most relevant baseline gaps are:

| Module | Covered | Missing | Why it matters |
|---|---:|---:|---|
| `src/ciu/cli.py` | 42.64% | 148 | Public command routing and exit behavior |
| `src/ciu/deploy.py` | 60.14% | 348 | Phase orchestration, preflight and fail-stop behavior |
| `src/ciu/deploy_pkg/health.py` | 61.02% | 69 | Readiness and image healthcheck validation |
| `src/ciu/engine.py` | 68.45% | 242 | Stack execution and failure mapping |
| `src/ciu/workspace_env.py` | 69.89% | 112 | Bootstrap/configuration failures |
| `src/ciu/diagnose.py` | 74.03% | 20 | Operator diagnosis and actionable exit status |

Do not spend this packet on `__main__.py`, example hooks, trivial print-only lines, or
already-94%-plus modules merely because they offer easy percentage points.

## Authority and hard boundaries

The worker may:

- add or edit files under `ciu/tests/tests/`;
- add small test-only fixtures/helpers under that same directory;
- update `run-ciu-tests.py` only to raise `COV_FAIL_UNDER` after the complete suite
  proves a stable new baseline with at least a 0.25 percentage-point safety margin;
- report a suspected implementation or specification bug without fixing it.

The worker must not:

- edit anything under `ciu/src/`;
- change CLI output, return codes, defaults, timeouts, orchestration order, config
  semantics, Docker behavior, or exception mapping;
- add `# pragma: no cover`, omit modules, delete tests, weaken assertions, or mock the
  function whose behavior the test claims to exercise;
- invoke a live deployment, stop/remove containers, touch dstdns, use real SSH/Vault,
  access credentials, pull images, or depend on network access;
- rewrite existing tests mechanically or make unrelated formatting changes;
- commit, tag, release, or push unless the controller separately grants that authority.

All Docker, subprocess, time, socket, filesystem-error, SSH and engine boundaries must
be faked with `monkeypatch`, temporary paths, and `CompletedProcess` values. Tests must
be deterministic and safe to run in parallel or on a host without Docker.

## Required work, in priority order

### P1 — diagnosis behavior and failure exits

Target `src/ciu/diagnose.py`, especially currently missing lines 30, 38, 41, 47,
62-65, and 88-101. Extend `tests/tests/test_ciu_diagnose.py`.

Cover at least:

1. A requested project adds the exact Compose project-label filter to `docker ps`.
2. Non-zero `docker ps` and `docker inspect` produce the bounded `RuntimeError` paths.
3. A stopped exit-137 container reports both the warning-level `exit_137` evidence and
   the error-level bad state without claiming Docker's `OOMKilled` flag was set.
4. Dead/restarting/exited-nonzero state and each bounded log signature are classified;
   use parameterization for memory exhaustion, disk-full and segfault rules.
5. `run()` covers JSON output, clean human output, human findings output, exception
   output, and the documented return convention: 2 for diagnosis failure, 1 when any
   error finding exists, otherwise 0.

Assert codes, severity, exit status, and essential remedies rather than entire
presentation strings.

### P2 — healthcheck configuration and probe failure branches

Target `src/ciu/deploy_pkg/health.py`, especially missing lines 291-320, 347-348,
364-365 and 380-413. Add a focused test module or extend the closest existing health
tests.

Cover at least:

1. Invalid/unreadable Compose returns no extracted probes.
2. String, `CMD`, `CMD-SHELL`, `NONE`, unsupported, empty and malformed healthcheck
   forms; services missing `image` or `test` are ignored.
3. Quoting/tokenization errors fail closed without inventing a tool name.
4. Image metadata declares an entrypoint tool for a distroless image even when the
   shell probe fails.
5. Missing Docker, timeout and invalid metadata JSON return deterministic unavailable
   results rather than escaping exceptions.
6. `preflight_probe()` skips absent Compose files, reports no-probe files, caches
   duplicate image/tool probes, reports missing tools, and returns exactly the emitted
   warnings.

Never start a real image or open a real socket.

### P3 — orchestration fail-stop and health failure paths

Target behavior in `src/ciu/deploy.py`, particularly `action_deploy()` lines 798-877,
`_run_stack()` lines 899-939, `action_healthcheck()` lines 973-991, and `_run()` lines
1611-1752. Prefer extending `tests/tests/test_ciu_deploy_actions.py`.

Cover at least:

1. A per-phase provisioning probe failure marks that phase failed; without
   `ignore_errors` later phases do not run, while with it later phases may run but the
   final result remains failure.
2. A post-phase health-gate failure moves started entries from deployed to failed and
   applies the same fail-stop/ignore-errors contract.
3. `_run_stack()` selects shipped versus rendered execution, restores every temporary
   environment override on success and exception, maps `ComposeError` to `False`, and
   re-raises bootstrap/dependency failures for the outer exit mapper.
4. Empty selection and failed bare health action retain their current return codes.
5. `_run()` action dispatch forwards the relevant flags and stops on a nonzero action
   unless `ignore_errors` is true. Patch bootstrap, config/render/preflight, selection
   and action boundaries; do not create a real stack.
6. Dry-run performs static validation but skips registry/network/live-probe effects;
   normal deploy performs its current preflight order before starting an action.

Tests should assert call order and arguments where order is a documented safety
property. Do not copy the implementation into a fake coordinator.

### P4 — authoring/configuration failures at the orchestration boundary

Extend the Compose-derived health-target tests around
`resolve_selection_health_containers()` in `tests/tests/test_ciu_deploy_actions.py`.
These uncovered branches are higher value than chasing generic parser lines.

Cover:

- missing rendered versus missing shipped Compose file and their distinct diagnostic source;
- unreadable/invalid YAML;
- missing/empty `services`;
- a service definition that is not a mapping;
- invalid Compose `profiles` type or non-string member;
- no service active for the selected profile;
- blank, unresolved (`$...`) or missing `container_name`;
- stable de-duplication when two selected inputs resolve the same concrete container.

If time remains, add focused error-wrapping tests for `render_jinja2_text()` and
`render_toml_template()` in `src/ciu/config_model.py` lines 301 and 318-319. Do not
prioritize newline-format branches just to gain coverage.

## Implementation guidance

- Read the relevant function and its cited `docs/SPEC.md` section before writing each
  assertion. Existing nearby tests show fixture style and monkeypatch seams.
- Prefer parameterized tables for equivalent state/log/config variants.
- Test through the smallest public or orchestration-level function that demonstrates
  the contract. Direct helper tests are acceptable for parsing and exception mapping.
- A fake should record arguments and return a realistic value. Avoid a single giant
  fixture that makes failures hard to understand.
- Do not assert volatile absolute paths, Python exception formatting, full help text,
  or every informational line.
- Keep each test name as a sentence describing the protected contract.

Suggested focused commands while implementing:

```bash
cd /workspaces/vbpub/ciu
pytest -q tests/tests/test_ciu_diagnose.py
pytest -q tests/tests/test_ciu_deploy_actions.py
pytest -q tests/tests/test_ciu_diagnose.py tests/tests/test_ciu_deploy_actions.py \
  --cov=ciu.diagnose --cov=ciu.deploy --cov=ciu.deploy_pkg.health \
  --cov-report=term-missing
# Optional compatibility lane when a prepared Python 3.11 environment exists:
python3.11 -m pytest -q tests/tests/test_ciu_diagnose.py \
  tests/tests/test_ciu_deploy_actions.py
```

The authoritative final command is:

```bash
cd /workspaces/vbpub/ciu
python run-ciu-tests.py -q
```

## Definition of done

All conditions are required:

1. P1-P4 have meaningful coverage, or an escalation report identifies a concrete
   contradiction/blocker for an omitted case.
2. No production file under `src/` changed.
3. The full suite passes with no new skip, xfail, warning suppression or
   external-service dependency. If `python3.11` is available, also run the
   focused changed tests with it and report the result; otherwise report that
   the 3.11 lane was unavailable rather than claiming it ran.
4. Total coverage is at least 77.0%; 77.5% is the target and 78% is a stretch goal.
   Do not add low-value tests merely to cross the number.
5. `COV_FAIL_UNDER` is raised only to an integer that leaves at least 0.25 percentage
   points below the measured clean full-suite result. For example, measured 77.31%
   permits a floor of 77, not 78.
6. Tests prove error outcomes as well as happy paths and assert the relevant exit code,
   classification, call order or fail-stop effect.
7. `git diff --check` passes and the diff contains only authorized test/gate changes.

## Stop and escalate when

Stop adding tests and report the smallest reproducer if:

- the current implementation contradicts `docs/SPEC.md`, `docs/CIU-DEPLOY.md`, or an
  established test contract;
- the expected assertion can only pass after changing production code;
- a branch requires a real container, credential, network endpoint, timing wait, or
  privileged host mutation;
- a failure reveals a likely security issue, secret exposure, destructive clean/stop
  behavior, wrong exit-code family, environment leakage, or orchestration continuing
  after a required fail-stop;
- the clean baseline differs by more than 0.25 percentage points or existing tests fail;
- coverage tooling counts platform-only branches differently enough to make the gate
  unstable.

An escalation is a successful outcome for that case. Do not encode a suspected bug as
the desired behavior just to turn the suite green.

## Required worker report

Return a compact report with:

```text
Changed files:
Tests added by contract area:
Focused test result:
Full suite result:
Coverage before -> after (exact percentage and covered/missing lines):
Coverage floor before -> after:
Production files changed: no
Skipped/xfail added: no
Escalations or suspected defects:
```

The reviewer must inspect whether mocks preserve the real boundary contract, run the
full command independently, compare the coverage JSON, and reject tests that only
execute lines without checking meaningful outcomes.
