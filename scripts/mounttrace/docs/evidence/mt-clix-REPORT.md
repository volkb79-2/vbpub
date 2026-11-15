# mounttrace cli-extended adoption report

<!-- Evidence scrub 2026-11-10: environment-specific checkout, controller, and test-host identifiers were normalized to neutral placeholders; report conclusions and evidence content are otherwise unchanged. -->

Worktree: `<worktree>`  
Branch: `mt-clix-20261110`  
Date: 2026-11-10

## Decision

**Option (b): keep a standard-library-only analysis core and put cli-extended in
a thin CLI adapter.** `scripts/mounttrace/src/mounttrace_core.py` retains
mountinfo parsing, the `Report` model, analysis and rendering. It imports no
third-party package and starts no subprocess. `src/mounttrace_cli/cli.py` owns
registration, grouped help, identity, common controls, the error boundary,
exit-status propagation and dispatch.

The supported CLI is now the `mounttrace` console entry point installed from
the mounttrace wheel, whose real dependency is `cli-extended>=0.6.0`. The old
single-file CLI copy deployment is retired. The development-tree
`mounttrace.py` launcher is not the deployable surface. Deployment uses the
wheel/venv procedure in `docs/CONSUMERS.md`, with both wheels installed from
verified GitHub Releases via `--no-index`.

Evidence:

- `libraries/iobench/pyproject.toml` declares a released cli-extended floor,
  while `src/iobench/cli.py` states it is the only module importing the
  library over a standard-library measurement core. That is the same boundary
  used here.
- `augeaswrap/pyproject.toml` declares a released dependency and configures
  `[tool.cli-extended]`; `src/augeaswrap/cli.py` is the registration adapter.
- `cgprofile/pyproject.toml` packages its CLIs with cli-extended and retains
  explicit import-boundary tests for stdlib-only paths.
- CX-D1 requires a real wheel dependency rather than vendoring; CX-D2 requires
  GitHub Releases with `--no-index`; CX-D3 permits the selected worktree's
  source on test gate `PYTHONPATH`; CX-D5 keeps library-owned controls out of
  consumer signatures.
- `cli-extended-v0.6.0` exists at `5b84f33ee7842b80a5894bbe3a708451dda73768`.
  The declared floor is exactly `cli-extended>=0.6.0` because 0.6.0 is the
  released baseline used to generate and validate this surface.

## Adoption checklist

| Item | Status | Evidence / reason |
|---|---|---|
| AC-01 | DONE | `CliIdentity.resolve` reads installed distribution metadata plus the absolute `src/mounttrace_cli/VERSION` file; tests require agreement with `pyproject.toml` version 3.2.0. No literal fallback. Audit `version-source` passes. |
| AC-02 | DONE | Identity is `MOUNTTRACE`, `read-only mount namespace analyzer`, command `mounttrace`, matching the configured CLI id. |
| AC-03 | DONE | All five verbs and all positional/option grammar are declared with `CliRegistry`, `VerbSpec`, `ArgumentSpec`, `OptionSpec`; handlers receive `CliRuntime`. No configure callback exists. |
| AC-04 | DONE | No `synopsis=` override; synopses are derived. Audit `synopsis-overrides` passes. |
| AC-05 | DONE | `--json`, `--verbose`, `--debug`, `--traceback`, help/version and other shared controls are library-owned. No consumer option shadows one; `-v` remains the one compatibility short spelling. Audit passes. |
| AC-06 | N.A. | The grammar has no option conflict, requirement or one-of relationship to declare. Argument types enforce path/PID form independently. |
| AC-07 | DONE | No configure callback is used for any route. Audit `configure-callbacks` passes. |
| AC-08 | DONE | No hidden options exist, so there is no undocumented hidden behavior. Audit `hidden-options` passes. |
| AC-09 | N.A. | There is no comma/space-separated name-list option. `compare OTHER...` is a variadic path operand, not a selector vocabulary. |
| AC-10 | DONE | Registry uses `unexpected_exceptions="report"`; tests pin one `[ERROR] unexpected ...` line, exit 1, and `--traceback` re-raising. Audit passes. |
| AC-11 | DONE | Expected `OSError`, `ValueError` and `RuntimeError` become `CliFailure` with the existing operational exit 1. Tests pin the formatted error and status. |
| AC-12 | DONE | All five verbs are read-only; there are no mutating verbs requiring confirmation/dry-run. The design and product docs record that reason. Audit passes. |
| AC-13 | N.A. | No interactive questions or confirmation flow exists. |
| AC-14 | DONE | Reports go through `runtime.output.primary`; all verbs expose library-owned `--json`; the logger namespace is `mounttrace`. Human and compact JSON renderers are tested. |
| AC-15 | N.A. | There is no long-operation progress UI, custom spinner, ANSI escape or consumer colour policy; watch emits report records only. |
| AC-16 | DONE | `[tool.cli-extended]` configures `review`, `manifest`, `spec` and `findings`. Audit passes. |
| AC-17 | DONE | `cli-extended surface check --cli mounttrace` passes. All 41 active candidates were reviewed and accepted; no open findings. |
| AC-18 | DONE | The generated surface reports syntax-complete for every route; no grammar hides in a callback or handler. Audit passes. |
| AC-19 | N.A. | mounttrace ships no agent workflow or packaged `SKILL.md`; there is nothing to install through `register_skills_verbs`. Audit reports MANUAL and this is the product decision. |
| AC-20 | N.A. | mounttrace has no daemon, service, endpoint or repository environment to diagnose. It reads `/proc`; wheel/venv installation is covered by the consumer procedure. Audit reports MANUAL and this is the product decision. |
| AC-21 | DONE | `assert_cli_contract` runs the real development executable with all five verbs and malformed invocations; adapter tests cover exit 0/1/2/3, JSON, watch flushing, verbose and unexpected exceptions. |
| AC-22 | DONE | `tests/conftest.py` enables `cli_extended.pytest_plugin`; all 41 reviewed cases carry exact test ids. Focused runs use `--cli-case-partial`; the registered gate is strict. Audit passes. |
| AC-23 | DONE | The new executable contract test uses the library's `make_invoker` helper. Existing namespace/topology helpers retain subprocess use because they create and inspect real throwaway mount namespaces; they are not per-project CLI wrappers. |
| AC-24 | DONE | `[project].dependencies` declares exactly `cli-extended>=0.6.0`; `package-dir` maps only mounttrace's own `src`, not `cli_extended`; the console entry point is declared. Audit passes. |
| AC-25 | DONE | Production package resolution uses the declared wheel dependency. Worktree source paths occur only in the registered test gate and VM test transport, as CX-D3 requires for testing the exact source. The VM assertion carrying the library path has the prescribed `# cli-extended: allow-path-assertion` marker. Audit passes. |

## Golden output

No golden was regenerated. `HumanGoldenTests` retains all 11 exact human-output
snapshots, including 120/80/non-TTY, verbose, ALERT and all five verbs. The
focused mounttrace file passed them unchanged. The JSON golden and exact compact
separators also passed unchanged; the adapter deliberately calls the core
renderer instead of cli-extended's default-indented JSON output.

Pinned JSON diff: none. Pinned human diff: none.

## Hand mutations

Every mutation was applied alone, exercised by one focused test file, and
restored with `apply_patch`.

| Mutation | Result |
|---|---|
| M1 `_path_spec` returned the raw `[PID:]PATH` value | KILLED by `test_parser_guards...` |
| M2 disabled the audit host-prefix absolute/no-PID guard | KILLED by `test_parser_guards...` |
| M3 disabled the audit `PID:PREFIX` guard | KILLED by `test_parser_guards...` |
| M4 disabled the absolute-path guard for `predict` and `--volumes-root` | KILLED by two `test_parser_guards...` parameters |
| M5 disabled positive-PID enforcement | KILLED by predict and soulmask parameters |
| M6 stopped converting invalid PID text to `ArgumentTypeError` | KILLED by the invalid-PID parameter |
| M7 disabled finite-positive `--watch` enforcement | KILLED by zero and NaN parameters |
| M8 stopped converting invalid watch text to `ArgumentTypeError` | KILLED by the invalid-watch parameter |
| M9 bypassed the core compact-JSON renderer | KILLED by `test_json_preserves_the_compact_wire_format` |
| M10 disabled JSON watch flushing | KILLED by `test_json_watch_flushes_the_first_report_before_sleeping` |
| M11 forced report verbosity off | KILLED by `test_verbose_compatibility_uses_library_debug` |
| M12 forced successful/ALERT reports to exit 0 | KILLED by `test_exit_status_contract_zero_three_one_and_two` |
| M13 changed operational failure exit from 1 to 2 | KILLED by the exit-status test |
| M14 changed unexpected-exception policy from report to raise | KILLED by `test_unexpected_exceptions...` |
| M15 lowered the dependency floor to `cli-extended>=0.5.0` | KILLED by `test_wheel_declares_the_released_cli_extended_floor` |
| M16 imported `cli_extended` in the core | KILLED by `test_core_imports_only_the_standard_library...` |
| M17 omitted cli-extended source from the guest `PYTHONPATH` | KILLED by the VM source-transport test |
| M18 changed the guest `src/` directory copy to one source file | KILLED by the VM source-transport test |
| M19 changed the project version from 3.2.0 to 3.2.1 | KILLED by the version-agreement assertion |

Survivors: none. No survivor test was needed.

## Focused test evidence

All focused runs were serial, with `nice -n 10`, `-p no:cacheprovider`, and
`PYTHONPATH=src:<worktree>/libraries/cli-extended/src`. They were repeated on
the resolved merge content immediately before the merge commit:

| File | Result |
|---|---|
| `tests/test_cli_adapter.py` | 26 passed |
| `tests/test_cli_review_cases.py` | 42 passed (41 case replays plus evidence check) |
| `tests/test_mounttrace.py` | 63 passed, 5 expected cockpit skips |

Conformance checks:

| Check | Result |
|---|---|
| `cli-extended surface check --cli mounttrace` | PASS |
| `cli-extended audit --cli mounttrace` | 13 pass, 0 warn, 0 fail, 2 manual (AC-19, AC-20) |
| `compileall` over `src/`, `tests/` and launcher | PASS |
| `git diff --check` | PASS |

A cockpit wheel-build smoke was attempted with `python -m build --wheel
--no-isolation`; the build correctly refused because the cockpit has
setuptools 84.2.1/wheel 0.48.0 while the manifest pins 82.0.1/0.47.0. No network
fallback or pin weakening was used, and the generated `egg-info` was removed.

## Registered gate and final head

Final head: `6daacd6f1f35d9d375419fbbd3b65761db0a795c`
(`Merge fresh origin/main into mt-clix-20261110`, parents `4e6642fe5` and
fresh `origin/main` at `ed3e98066c81af50738f7dd46e8323065b88dbf0`).

The registered suite lane ran from `scripts/mounttrace` on the clean final head
under the mandated shared lock:

```bash
flock <controller-program-dir>/gate.lock \
  nice -n 10 run-gate suite
```

Log:
`<controller-program-dir>/logs/mt-clix-suite-6daacd6f1.log`

| Evidence | Result |
|---|---|
| `run-gate.commit=` label | `6daacd6f1f35d9d375419fbbd3b65761db0a795c` |
| Suite verdict | PASS, exit code 0 |
| Failures | None |
| Test outcomes | 114 passed; 22 expected environment skips |
| Skip split | 17 unprivileged namespace checks plus 5 root-only VM checks |
| Final worktree | Clean after the gate; no commit followed it |

Conformance was rerun on the same final source with the exact worktree
cli-extended source on the documented test-only path:

```bash
cd <worktree>/scripts/mounttrace
PYTHONPATH=src:<worktree>/libraries/cli-extended/src \
  cli-extended surface check --cli mounttrace
PYTHONPATH=src:<worktree>/libraries/cli-extended/src \
  cli-extended audit --cli mounttrace
```

Results: surface check PASS; audit 13 pass, 0 warn, 0 fail, and the two
documented MANUAL/n.a. items AC-19 and AC-20.

## Owed VM and packaging re-runs

The VM lane cannot run in this devcontainer because it needs the host-backed
QEMU harness and `scripts/mounttrace/artifacts/vm-lab`. On `<test-host>` after merge,
run from `scripts/mounttrace`:

```bash
testing/vm/run-vm-tests.sh --rebuild-base
```

`--rebuild-base` is required because `python3-pytest` was added to the sealed
guest package set, and because both guest phases now run the complete pytest
directory with `src/` and the exact cli-extended source on `PYTHONPATH`.

The wheel build/install acceptance must run in the estate wheel-builder image
with its exact pinned setuptools/wheel versions. The cockpit cannot produce
valid evidence for that pin set.

## Commits and trailer check

Commits in `git log origin/main..HEAD`:

- `4e6642fe5b1cbcaa3abdead76c9ceb43b24af118`
  `feat(mounttrace): adopt cli-extended with a stdlib-only core`
- `6daacd6f1f35d9d375419fbbd3b65761db0a795c`
  `Merge fresh origin/main into mt-clix-20261110`

Mechanical trailer check, run from the final clean head:

```bash
for commit in $(git log --format=%H origin/main..HEAD); do
  count=$(git show -s --format=%B "$commit" |
    grep -Fxc 'Co-Authored-By: Codex deepseek-v4.1-flash <noreply@openai.com>' ||
    true)
  printf '%s trailer_count=%s\n' "$(git rev-parse --short=12 "$commit")" "$count"
  test "$count" -eq 1 || exit 1
done
```

Output:

```text
6daacd6f1f35 trailer_count=1
4e6642fe5b1c trailer_count=1
```

Every commit, including the merge commit, has exactly one required trailer.
