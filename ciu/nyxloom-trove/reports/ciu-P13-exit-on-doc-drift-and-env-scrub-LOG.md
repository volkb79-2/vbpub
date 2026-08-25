# LOG — ciu-P13-exit-on-doc-drift-and-env-scrub

- Package: `ciu-P13-exit-on-doc-drift-and-env-scrub`
- Worktree: `/workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu`
- Branch: `feat/ciu-qol-v8prep-wave`
- Handoff `input_revision`: `64d7e359d99377a58772367286c053b047980276`
- Status: **PARTIAL — O5 BLOCKED (per the handoff's own named escalate_if trap),
  O1-O4 and O6-O8 all COMPLETE**

## O5 — BLOCKED (do not edit forbidden test)

Per the handoff's `escalate_if`: "fixing O5's tag requires editing a file in
`scope.forbid` (a test asserting `[S10.7]` literally) — BLOCKED naming the
exact file:line, do not edit the forbidden test."

I read `tests/tests/test_ciu_warn_policy.py` (READ-ONLY, `scope.forbid`)
before touching `src/ciu/warn_policy.py`, as instructed. It asserts the
literal tag in two places:

- `tests/tests/test_ciu_warn_policy.py:82` —
  `test_rejects_invalid_config_value_naming_source_and_vocabulary`:
  `assert "[S10.7]" in message`
- `tests/tests/test_ciu_warn_policy.py:95` —
  `test_rejects_invalid_env_value_naming_source_and_vocabulary`:
  `assert "[S10.7]" in message`

**BLOCKED: retagging `src/ciu/warn_policy.py`'s `_validate_exit_on` raise
message from `[S10.7]` to `[S10.6]` would fail both of these assertions in
`tests/tests/test_ciu_warn_policy.py` (lines 82 and 95), and that file is
`scope.forbid`. Per the BLOCKED rule I did not edit the tag, did not invent
a new `S10.7` doc heading, and did not touch the forbidden test file.**

I also checked `src/ciu/deploy.py:1128`'s own `# S10.7 —` comment (a
different call site, not the oracle's target) and left it untouched too —
changing only warn_policy.py's message while leaving that adjacent comment
would create the exact same-shipped-inconsistency the review_focus item
warns about, and the underlying tag mismatch is the same BLOCKED condition
either way.

Per `review_focus` item 2 ("check CHANGES.md's own text for `[S10.7]`
mentions"): `grep -n "S10\.7" CHANGES.md` returns zero matches — CHANGES.md
does not reference the tag, so nothing there is affected by leaving the tag
as-is.

Since O5 alone is blocked and every other oracle in this package is
independent of it (the handoff frames all eight items as "small,
independent, mechanical fixes"), I completed O1-O4 and O6-O8 below rather
than discarding unrelated, already-verified-safe work.

## O1 — docs/SPEC.md S15.16 cross-reference fixed

`docs/SPEC.md:2176-2179` (pre-edit) claimed the S15.16 mem_min finding was
"by default also raised" and named the withdrawn `CIU_WARNINGS_AS_ERRORS=0`
as the softening lever. Replaced with prose matching the current, correct
S10.6 section (`docs/SPEC.md:1086-1108`, used as the template per the
handoff's Context section — left untouched, it is already correct):

```
required byte count, via `warn_policy.warn_or_raise` (S10.6): always
logged as `[WARN]`; by DEFAULT (`ciu.exit_on` = `"ERROR"`) it does NOT
raise. `ciu.exit_on = "WARN"` makes it raise (`ValueError`, S10.3 exit
2) for an operator who wants this fail-fast; `"NEVER"` suppresses even
the WARN-level abort entirely for this and every other S10.6 site.
```

Verified: `grep -n "CIU_WARNINGS_AS_ERRORS\|by default also raised"
docs/SPEC.md` now returns only `docs/SPEC.md:1105` — the S10.6 section's own
migration note ("The previous boolean `ciu.fail_fast` and env
`CIU_WARNINGS_AS_ERRORS` are withdrawn...") — which is legitimate historical/
migration documentation inside the section the handoff explicitly names as
"the CURRENT, correct S10.6 section," not drift. No other occurrence of
"by default also raised" remains anywhere in the file.

## O2 — docs/DESIGN-NOTES.md D6 row fixed

`docs/DESIGN-NOTES.md:301` named `CIU_WARNINGS_AS_ERRORS` as the S10.6
mechanism's env var. Changed only the mechanism name to `CIU_EXIT_ON`;
none of the surrounding survey reasoning (the candidate-site table, the
"Pattern for adding a new one" note) was touched. Verified: `grep -n
CIU_WARNINGS_AS_ERRORS docs/DESIGN-NOTES.md` returns zero matches.

## O3 — src/ciu/governance.py comment fixed

`src/ciu/governance.py:155`'s ambient-`CIU_*`-toggle example list named the
withdrawn `CIU_WARNINGS_AS_ERRORS`. Replaced with `CIU_EXIT_ON` (the current
ambient toggle for this exact mechanism), so the comment's claim "usable
directly, like every other ambient `CIU_*` toggle" stays accurate.

## O4 — src/ciu/deploy.py doubled "Set Set" fixed

`src/ciu/deploy.py:1145-1146` (pre-edit) rendered `"...if no floor is
actually required. Set " "Set ciu.exit_on = \"WARN\" ..."` — a doubled
"Set". Removed the duplicate; the message now reads "...actually required.
Set ciu.exit_on = \"WARN\" to make warnings fatal, or..." exactly once. No
other wording changed. Verified: `grep -rn "Set Set" src/` returns zero
matches.

## O6 — tests/conftest.py env-scrub fixture added

`tests/conftest.py` did not exist before this package (confirmed via `find
tests -iname conftest.py` returning nothing). Created it with a
function-scoped, autouse fixture that uses `monkeypatch.delenv(...,
raising=False)` — never direct `os.environ` mutation — to clear all seven
named variables (`REPO_ROOT`, `PHYSICAL_REPO_ROOT`, `REPO_NAME`,
`INSTANCE_ID`, `DOCKER_NETWORK_INTERNAL`, `PUBLIC_FQDN`, `CIU_EXIT_ON`)
before every test body runs. Because it depends on the built-in
`monkeypatch` fixture (itself function-scoped), this fixture runs
fresh before each test and pytest auto-restores the ambient value
afterward — a test's own `monkeypatch.setenv`/`monkeypatch.delenv` calls in
the same test body still apply normally afterward (fixture-then-test-body
composition), since nothing here uses a session-scoped monkeypatch or a
bare `os.environ` write that could stick across tests.

Both gate runs below (with and without the manual `env -u ...` prefix) are
green, proving the fixture actually neutralizes the ambient devcontainer
state instead of merely appearing to.

## O7 — README.md worktree-cap paragraph added

Confirmed first (per the handoff's Context item 7 and `escalate_if` item 2)
that both target docs already carry the content this handoff assumes:

- `docs/CONFIG.md:175` — `max_concurrent_instances` table row (default:
  "absent (no cap)", spec S16.3).
- `docs/CONSUMERS.md:17` — `max_concurrent_instances = 3` in the worked
  `[ciu.worktree]` TOML example.

README.md's worktree discussion (line 11 verb list, lines ~122-131 the
automation-surfaces paragraph) did NOT mention the cap. Added one new
paragraph immediately after the automation-surfaces paragraph (before "The
rule of thumb..."):

```
A repo can also cap how many managed worktree instances run at once via
`[ciu.worktree].max_concurrent_instances` (default: unlimited, no cap) —
see [docs/CONFIG.md](docs/CONFIG.md) for the config table and
[docs/CONSUMERS.md](docs/CONSUMERS.md) for a worked example of setting and
verifying it.
```

This is a short pointer only — no duplication of CONFIG.md's table or
CONSUMERS.md's worked example content. Neither `docs/CONFIG.md` nor
`docs/CONSUMERS.md` was edited (both `scope.forbid`).

## O8 — docs/BACKLOG-2026-08-24.md QOL-9 row updated

Updated `CIU-QOL-9`'s `**Status:**` line from `OPEN` to `✅ IMPLEMENTED
(ciu-P13)` and added a one-line `**Evidence:**` pointer to the README
change (O7) and the pre-existing CONSUMERS.md worked example. This file
was not in the handoff's original `scope.touch` list; the handoff's Work
item 8 explicitly calls this an oversight and directs it be treated as
included, so I edited it.

## Environment setup — both gate runs

### Run 1 — manual `env -u ...` prefix (six ambient vars unset), proves the
suite is green independent of the fixture

```
env -u REPO_ROOT -u PHYSICAL_REPO_ROOT -u REPO_NAME -u INSTANCE_ID -u DOCKER_NETWORK_INTERNAL -u PUBLIC_FQDN \
  .venv/bin/python run-ciu-tests.py
```

Output tail:

```
Name                                             Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------------------------------
src/ciu/__init__.py                                  3      0      0      0   100%
src/ciu/__main__.py                                  3      0      2      0   100%
src/ciu/_version.py                                 11      0      0      0   100%
src/ciu/activate.py                                119      0     46      0   100%
src/ciu/cli.py                                     670      0    242      0   100%
src/ciu/cli_utils.py                                11      0      0      0   100%
src/ciu/composefile.py                             388      0    180      0   100%
src/ciu/config_constants.py                         29      0      4      0   100%
src/ciu/config_model.py                            262      0    118      0   100%
src/ciu/deploy.py                                 1318      0    562      0   100%
src/ciu/deploy_pkg/__init__.py                       8      0      0      0   100%
src/ciu/deploy_pkg/health.py                       192      0     98      0   100%
src/ciu/deploy_pkg/http_util.py                     24      0      2      0   100%
src/ciu/deploy_pkg/layouts.py                       63      0     24      0   100%
src/ciu/deploy_pkg/phases.py                        69      0     40      0   100%
src/ciu/deploy_pkg/profiles.py                     131      0     64      0   100%
src/ciu/deploy_pkg/registry.py                      38      0     20      0   100%
src/ciu/dev.py                                     196      0     74      0   100%
src/ciu/diagnose.py                                 79      0     34      0   100%
src/ciu/engine.py                                  882      0    292      0   100%
src/ciu/governance.py                              382      0    158      0   100%
src/ciu/hooks/__init__.py                            0      0      0      0   100%
src/ciu/hooks/examples/__init__.py                   0      0      0      0   100%
src/ciu/hooks/examples/post_compose_example.py       5      0      0      0   100%
src/ciu/hooks/examples/pre_compose_example.py        4      0      0      0   100%
src/ciu/hooks_runner.py                            123      0     52      0   100%
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
====================== 2419 passed, 6 warnings in 16.80s =======================
```

2419 passed, 0 failed, 100.00% line+branch coverage.

### Run 2 — NO `env -u ...` prefix at all (the actual O6 proof)

Ambient shell state confirmed present immediately before this run (this
devcontainer's real, unrelated-checkout identity leaking in, exactly the
condition O6 targets):

```
REPO_ROOT=/workspaces/dstdns
PHYSICAL_REPO_ROOT=/home/vb/volkb79-2/dstdns
REPO_NAME=dstdns
INSTANCE_ID=98535c
DOCKER_NETWORK_INTERNAL=dstdns-98535c-network
PUBLIC_FQDN=gstammtisch.dchive.de
CIU_EXIT_ON=
```

Command:

```
.venv/bin/python run-ciu-tests.py
```

Output tail:

```
Name                                             Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------------------------------
src/ciu/__init__.py                                  3      0      0      0   100%
src/ciu/__main__.py                                  3      0      2      0   100%
src/ciu/_version.py                                 11      0      0      0   100%
src/ciu/activate.py                                119      0     46      0   100%
src/ciu/cli.py                                     670      0    242      0   100%
src/ciu/cli_utils.py                                11      0      0      0   100%
src/ciu/composefile.py                             388      0    180      0   100%
src/ciu/config_constants.py                         29      0      4      0   100%
src/ciu/config_model.py                            262      0    118      0   100%
src/ciu/deploy.py                                 1318      0    562      0   100%
src/ciu/deploy_pkg/__init__.py                       8      0      0      0   100%
src/ciu/deploy_pkg/health.py                       192      0     98      0   100%
src/ciu/deploy_pkg/http_util.py                     24      0      2      0   100%
src/ciu/deploy_pkg/layouts.py                       63      0     24      0   100%
src/ciu/deploy_pkg/phases.py                        69      0     40      0   100%
src/ciu/deploy_pkg/profiles.py                     131      0     64      0   100%
src/ciu/deploy_pkg/registry.py                      38      0     20      0   100%
src/ciu/dev.py                                     196      0     74      0   100%
src/ciu/diagnose.py                                 79      0     34      0   100%
src/ciu/engine.py                                  882      0    292      0   100%
src/ciu/governance.py                              382      0    158      0   100%
src/ciu/hooks/__init__.py                            0      0      0      0   100%
src/ciu/hooks/examples/__init__.py                   0      0      0      0   100%
src/ciu/hooks/examples/post_compose_example.py       5      0      0      0   100%
src/ciu/hooks/examples/pre_compose_example.py        4      0      0      0   100%
src/ciu/hooks_runner.py                            123      0     52      0   100%
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
====================== 2419 passed, 14 warnings in 16.32s ======================
```

2419 passed, 0 failed, 100.00% line+branch coverage — same pass count as
Run 1, confirming the autouse fixture neutralizes the ambient devcontainer
identity/policy vars without the manual `env -u ...` workaround.

## Oracle table

| Oracle | Status | Satisfied by |
|---|---|---|
| O1-spec-drift-fixed | DONE | `docs/SPEC.md:2176-2180` rewritten; no "by default also raised" or bare `CIU_WARNINGS_AS_ERRORS`-as-active-lever text remains anywhere in the file (the one remaining hit at `:1105` is the S10.6 section's own correct migration note). |
| O2-design-notes-drift-fixed | DONE | `docs/DESIGN-NOTES.md:301` now names `CIU_EXIT_ON`; zero remaining `CIU_WARNINGS_AS_ERRORS` hits in the file; surrounding D6 survey text untouched. |
| O3-governance-comment-fixed | DONE | `src/ciu/governance.py:155` now lists `CIU_EXIT_ON` instead of the withdrawn `CIU_WARNINGS_AS_ERRORS`. |
| O4-typo-fixed | DONE | `src/ciu/deploy.py:1145-1146` renders "Set ciu.exit_on = ..." exactly once; `grep -rn "Set Set" src/` is empty. |
| O5-tag-normalized | **BLOCKED** | `tests/tests/test_ciu_warn_policy.py:82` and `:95` both assert `"[S10.7]" in message` literally; that file is `scope.forbid`. `src/ciu/warn_policy.py`'s tag left as `[S10.7]` (unmodified) to keep the forbidden test green. See "O5 — BLOCKED" section above for the full reasoning. |
| O6-env-scrub-fixture | DONE | `tests/conftest.py` created with an autouse, function-scoped fixture using `monkeypatch.delenv(..., raising=False)` for all seven named vars. Both gate runs above are green, including the one with zero `env -u` unsetting. |
| O7-readme-worktree-budget | DONE | One new paragraph in `README.md` naming `ciu.worktree.max_concurrent_instances` and pointing at `docs/CONFIG.md`/`docs/CONSUMERS.md`; neither forbidden doc was edited. |

O8 (QOL-9 backlog row) is not a numbered oracle but was Work item 8;
completed as described above.

## Files changed

- `docs/SPEC.md`
- `docs/DESIGN-NOTES.md`
- `src/ciu/governance.py`
- `src/ciu/deploy.py`
- `tests/conftest.py` (new file)
- `README.md`
- `docs/BACKLOG-2026-08-24.md`
- `nyxloom-trove/reports/ciu-P13-exit-on-doc-drift-and-env-scrub-LOG.md` (this file)

`src/ciu/warn_policy.py` was deliberately NOT changed (O5 BLOCKED).

## Commit hash

Code/doc fix commit (read back with `git log -1 --format=%H` immediately
after committing, not predicted): `50f032b9653600522532df673c52d15358dd5b0f`

This LOG is committed separately, on top of that commit.
