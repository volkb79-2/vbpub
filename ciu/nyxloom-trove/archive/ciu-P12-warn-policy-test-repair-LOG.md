# LOG — ciu-P12-warn-policy-test-repair

- Package: `ciu-P12-warn-policy-test-repair`
- Worktree: `/workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu`
- Branch: `feat/ciu-qol-v8prep-wave`
- Handoff `input_revision`: `370ea814`
- Status: COMPLETE (no BLOCKED)
- Code fix commit: `64d7e359d99377a58772367286c053b047980276`

## What I found

The gate was red exactly as the handoff described. Commit `51d5d4f7` rewrote
`src/ciu/warn_policy.py` from the boolean `CIU_WARNINGS_AS_ERRORS` env-var
model to the closed-vocabulary `ciu.exit_on` model (`WARN`/`ERROR`/`NEVER`,
default `ERROR`), updated the one real call site
(`deploy.py:1135-1150`, with the intent recorded in the comment at
`:1128-1134`), but left two test surfaces asserting the old contract:

1. `tests/tests/test_ciu_warn_policy.py` — every test referenced
   `warn_policy.WARNINGS_AS_ERRORS_ENV_VAR` and
   `warn_policy.warnings_as_errors_enabled()`, neither of which exist in the
   current module (the latter existed only as a dead compat shim at
   `warn_policy.py:90-98`).
2. `tests/tests/test_ciu_deploy_actions.py` — the two
   `governance_slice_preflight` mem_min tests drove the old env-var lever and
   asserted the OLD default (raise with no config at all), which is no
   longer the shipped behavior.

`grep -rn warnings_as_errors_enabled\|WARNINGS_AS_ERRORS_ENV_VAR src/ tests/`
(run before any edit) confirmed the ONLY real references were: the
definition itself (`warn_policy.py:91`) and the two stale test files — no
other call site anywhere in `src/` or `tests/`. This confirmed the fix is
mechanical/self-contained per the handoff's `escalate_if` check; no
escalation was needed.

## What I changed and why

1. **`src/ciu/warn_policy.py`** — deleted `warnings_as_errors_enabled()` and
   its docstring (the dead compat shim, `:90-98` pre-edit) per oracle O2.
   Restored the standard two-blank-line separation between top-level
   functions after the deletion (the file otherwise uses that convention
   throughout).
2. **`tests/tests/test_ciu_warn_policy.py`** — fully rewritten against the
   real module (`EXIT_ON_ENV_VAR`, `EXIT_ON_VALUES`, `DEFAULT_EXIT_ON`,
   `_resolve_exit_on`, `_validate_exit_on`, `should_exit_on`,
   `warn_or_raise`). Four test classes:
   - `TestResolveExitOn` — the three-tier precedence: default when neither
     source is set; env wins when config is `None`, has no `ciu` table, has
     a `ciu` table without `exit_on`, or has a non-dict `ciu` value; config
     wins over env even when env is set to a different value; env value is
     stripped/uppercased.
   - `TestValidateExitOn` — valid values normalize through; invalid values
     raise `ValueError` tagged `[S10.7]` naming the source and the full
     vocabulary, exercised from BOTH the config-sourced and the
     env-sourced call paths (the O1 negative explicitly calls this out).
   - `TestShouldExitOn` — the full 3 (threshold) x 2 (severity)
     parametrized truth table, plus a default-threshold sanity check.
   - `TestWarnOrRaise` — prints `[<SEVERITY>] <message>` in every case;
     raises exactly when `should_exit_on` says so (WARN severity under
     exit_on=WARN, ERROR severity under the default) and does not raise
     otherwise (WARN severity under the default, ERROR severity under
     exit_on=NEVER); the raised exception's text is exactly the message
     with no tag.
3. **`tests/tests/test_ciu_deploy_actions.py`** — rewrote the two failing
   tests (previously `..._raises_when_mem_min_inadequate` and
   `..._mem_min_inadequate_logs_only_when_warnings_opted_out`), renamed to
   describe the NEW default/override shape:
   - `test_governance_slice_preflight_mem_min_inadequate_warns_by_default` —
     drives `_plain_config()` (no `ciu` table at all, i.e. default
     `exit_on=ERROR`) through `Profile(...).config`; asserts the run does
     NOT raise and prints `[WARN] ... [S15.16] ...`.
   - `test_governance_slice_preflight_raises_when_mem_min_inadequate_and_exit_on_warn`
     — builds on `_plain_config()` with `config["ciu"] = {"exit_on":
     "WARN"}`; asserts the SAME finding now raises `ValueError` containing
     `[S15.16]` and the slice/stack names.
   Both keep the `check_slice_unit` / `check_slice_memory_min` monkeypatches
   byte-identical to the originals and reuse `_plain_config()` and
   `_governance_selection_rendered_mem_min()` unchanged, only changing the
   exit_on lever (via the `config` parameter that `governance_slice_preflight`
   reads off `profile.config`, per `deploy.py:1036`) and the expected
   outcome — neither test references `CIU_WARNINGS_AS_ERRORS` or monkeypatches
   `os.environ` for this policy.

No changes were made to `src/ciu/deploy.py`, `docs/SPEC.md`,
`docs/DESIGN-NOTES.md`, or `CHANGES.md` (all in `scope.forbid`).

## Final gate command and output

```
env -u REPO_ROOT -u PHYSICAL_REPO_ROOT -u REPO_NAME -u INSTANCE_ID -u DOCKER_NETWORK_INTERNAL -u PUBLIC_FQDN \
  .venv/bin/python run-ciu-tests.py
```

Output tail (coverage table trimmed to the last rows + summary; full table
showed 100% on every module):

```
src/ciu/hosts.py                                    61      0     28      0   100%
src/ciu/ksm.py                                     180      0     64      0   100%
src/ciu/output.py                                   89      0     34      0   100%
src/ciu/paths.py                                    30      0     12      0   100%
src/ciu/procutil.py                                 17      0      2      0   100%
src/ciu/provisioning.py                            256      0    120      0   100%
src/ciu/scaffold.py                                104      0     36      0   100%
src/ciu/secrets/__init__.py                          3      0      0      0   100%
src/ciu/secrets/directives.py                      140      0     78      0   100%
src/ciu/secrets/materialize.py                     229      0     64      0   100%
src/ciu/secrets/providers.py                       111      0     38      0   100%
src/ciu/transport_ssh.py                           219      0     70      0   100%
src/ciu/warn_policy.py                              32      0     14      0   100%
src/ciu/workspace_env.py                           454      0    190      0   100%
src/ciu/worktree.py                               1115      0    432      0   100%
--------------------------------------------------------------------------------------------
TOTAL                                             8050      0   3194      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
============================ 2419 passed in 17.79s =============================
```

2419 passed, 0 failed, 100.00% line+branch coverage (`--cov-fail-under=100`
satisfied).

## Oracle table

| Oracle | Satisfied by |
|---|---|
| O1-warn-policy-tests-match-exit-on | `tests/tests/test_ciu_warn_policy.py` rewritten to import and exercise only `EXIT_ON_ENV_VAR`, `EXIT_ON_VALUES`, `DEFAULT_EXIT_ON`, `_resolve_exit_on`, `_validate_exit_on`, `should_exit_on`, `warn_or_raise` — no reference to the old `WARNINGS_AS_ERRORS_ENV_VAR`/`warnings_as_errors_enabled`. `TestShouldExitOn.test_truth_table` parametrizes all 3x2 threshold/severity combinations. `TestResolveExitOn` covers config-wins-over-env, env-wins-over-default, and default-when-neither-set. `TestValidateExitOn` has one test raising via the config path (`test_rejects_invalid_config_value_...`) and one via the env path (`test_rejects_invalid_env_value_...`), both asserting the `[S10.7]` tag, the source name, and the full vocabulary. `TestWarnOrRaise` asserts the exact `[<SEVERITY>] <message>` print and the exact untagged `ValueError(message)` text, for both the raise and no-raise cases. |
| O2-dead-shim-removed | `warnings_as_errors_enabled()` deleted from `src/ciu/warn_policy.py` (was `:90-98`). `grep -rn warnings_as_errors_enabled src/ tests/` and `grep -rn WARNINGS_AS_ERRORS_ENV_VAR src/ tests/` both return zero matches after this package (verified above and re-verified after the final edit). No re-export or alias was added. |
| O3-governance-mem-min-tests-match-shipped-decision | The two rewritten tests in `test_ciu_deploy_actions.py` drive the lever exclusively via the `config` parameter (`_plain_config()` for the default case, `config["ciu"] = {"exit_on": "WARN"}` for the override case) passed into `Profile(...)`, which `governance_slice_preflight` reads as `profile.config` (`deploy.py:1036`) and threads to `warn_policy.warn_or_raise(..., config=config)` (`deploy.py:1150`). Default case asserts no raise + `[WARN]`/`[S15.16]` in stdout; override case asserts `ValueError` containing `[S15.16]` and the slice/stack names. Neither test references `CIU_WARNINGS_AS_ERRORS` or monkeypatches `os.environ`. Both keep the exact same `check_slice_unit`/`check_slice_memory_min` monkeypatches as the pre-edit versions. |
| O4-full-gate-green | `env -u REPO_ROOT -u PHYSICAL_REPO_ROOT -u REPO_NAME -u INSTANCE_ID -u DOCKER_NETWORK_INTERNAL -u PUBLIC_FQDN .venv/bin/python run-ciu-tests.py` exits 0: 2419 passed, 0 failed, 100.00% line+branch coverage — output pasted verbatim above. |

## Deviations / degrees of freedom taken

- Kept two separate `TestXxx` classes plus a flat `TestWarnOrRaise` structure
  in the rewritten `test_ciu_warn_policy.py` (the handoff left class-vs-flat
  as a free choice).
- Renamed the two `test_ciu_deploy_actions.py` tests to
  `test_governance_slice_preflight_mem_min_inadequate_warns_by_default` and
  `test_governance_slice_preflight_raises_when_mem_min_inadequate_and_exit_on_warn`
  (the handoff explicitly invited renaming since the old names described the
  withdrawn opt-out shape).
- No changes to `docs/SPEC.md` even though its S10.6 prose (`:2177-2179`)
  still describes the OLD default ("by default also raised ... 
  `CIU_WARNINGS_AS_ERRORS=0` downgrades") — that file is in `scope.forbid`
  and out of scope for this package; not fixed here, not filed as a new
  finding since the handoff's own Context section already flags SPEC.md as
  "read only" for this package.
