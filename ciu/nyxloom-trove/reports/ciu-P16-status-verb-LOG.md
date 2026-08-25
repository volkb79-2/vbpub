# ciu-P16 — `ciu status` verb (CIU-QOL-6) — LOG

Package: `nyxloom-trove/handoffs/ciu-P16-status-verb.md`
HEAD at start: `3b670c067ece28ad55cac029e69f36714798d9a2` (ciu-P15's final LOG
commit) — confirmed matching before any edits.
Implementation commit: `7d3f0e0d2e668f7b4870e882893f60e16199cdd9`

## What was built

- `src/ciu/deploy.py`:
  - Extracted `_stack_compose_project(repo_root, config, stack_dir) -> str`
    (single-stack, per-entry compose-project resolution — the exact
    tags-present → `engine.compose_project_name` / tags-absent →
    `engine.identity_compose_project_name` branching that
    `_stack_compose_projects` already used). `_stack_compose_projects` was
    refactored to call this helper instead of duplicating the branch, so the
    two paths can never drift out of sync (packet's explicit concern).
  - Added `action_status(repo_root, profile, selection, *, json_output) ->
    int` — the new verb's engine. For every selection entry: resolves
    `stack_dir`; missing on disk → row with `compose_project: None`,
    `containers: []`, never dropped; existing → resolves the compose
    project via the shared helper, calls `diagnose._inspect(project)`
    (imported read-only — `from . import diagnose`, no changes to
    `diagnose.py`), and for each returned item builds `{name:
    item["Name"].lstrip("/"), status: health_pkg.classify(item.get
    ("State")), image: item.get("Config", {}).get("Image")}` — exactly the
    packet's normative expressions, not a re-derivation.
  - `STATUS_SCHEMA_VERSION = 1` (plain top-level int, matching
    `worktree.py`'s `WORKTREE_JSON_SCHEMA_VERSION`/`BRANCHES_SCHEMA_VERSION`
    and this module's own `ProvenanceResult.schema_version` convention — no
    new numbering scheme).
  - `RuntimeError` from `diagnose._inspect` (Docker daemon unreachable) is
    NOT caught inside `action_status` — it propagates, by design, to the CLI
    layer.
- `src/ciu/cli.py`:
  - New `_status(rest: list[str]) -> int` handler (same shape as
    `_provenance`): parses `--profile` (repeatable + comma form, same
    expansion logic as `deploy._run`), `--json`, `--define-root`; resolves
    `repo_root` via `dev.resolve_repo_root`; calls `load_global_config` →
    `resolve_profiles` → `build_selection` → `action_status`, i.e. the exact
    chain `ciu up --profile` uses. Wraps that chain in
    `except (RuntimeError, ValueError)` → `[ERROR] ciu status: <reason>` to
    stderr, return 2 — the ONLY place that turns a resolution/Docker failure
    into prose, matching `_provenance`'s "one place decides prose/raise"
    discipline.
  - `elif verb == "status": raise SystemExit(_status(rest))` wired into
    `main()`'s dispatch, next to `provenance`.
  - `_USAGE`: new `status [--profile NAME] [--json]` line under STACK
    ORCHESTRATION, next to `diagnose`.
  - `_VERB_HELP["status"]`: full verb-scoped help block (options, closed
    vocabulary, missing-vs-empty distinction, daemon-unreachable behavior).
- `tests/tests/test_ciu_cli_status.py` (new, 26 tests) — see Oracle proof
  below.
- Docs: `README.md` (one bullet), `docs/FEATURES.md` (feature-table row +
  CLI-table row), `docs/SPEC.md` (new **S7.10** subsection under a new
  "### Status reporting" heading, right after S7.9/Registry — chosen over
  S17 because `ciu status` reuses S7's own selection chain and health
  vocabulary (S7.7) and resolves the S8.7 compose-project scheme verbatim,
  where S17 is specifically about commit/vendor provenance evidence, a
  different question), `docs/CONSUMERS.md` (new §13 with a worked `--json`
  example plus the null-vs-empty-vs-daemon-down explanation),
  `CHANGES.md` (Unreleased/Added entry), `docs/BACKLOG-2026-08-24.md`
  (CIU-QOL-6 row → `✅ FIXED (ciu-P16)` with an Evidence paragraph).

## Oracle-by-oracle

**O1-resolution** — `action_status` resolves every selected stack via the
new `_stack_compose_project` helper, reusing `engine.compose_project_name`/
`engine.identity_compose_project_name` verbatim (never reimplemented); a
missing stack directory gets a named row rather than vanishing. Proved by:
- `TestActionStatusResolution::test_missing_stack_dir_gets_a_named_row_not_dropped`
- `TestActionStatusResolution::test_one_missing_stack_does_not_hide_other_stacks`
- `TestActionStatusResolution::test_resolution_uses_the_shared_per_entry_helper_tagged`
- `TestActionStatusResolution::test_resolution_uses_the_shared_per_entry_helper_untagged_identity`
- `TestActionStatusResolution::test_missing_identity_record_refuses_rather_than_guesses`
  (a stack dir that EXISTS but whose project cannot be named — no ciu.env,
  tags absent — raises `ValueError` rather than guessing; mirrors
  `_stack_compose_projects`'s own "a report/teardown that cannot be named
  refuses" discipline)

**O2-envelope** — the `--json` document is exactly `{schema_version: 1,
profile, stacks: [{path, name, phase_key, compose_project, containers:
[{name, status, image}]}]}`; container `status` uses
`health_pkg.classify`'s five-value closed vocabulary applied to the
container's own `State` dict; `image` is `Config.Image` verbatim, no
normalization. Proved by:
- `TestActionStatusEnvelope::test_existing_project_zero_containers_is_a_legitimate_empty_not_an_error`
- `TestActionStatusEnvelope::test_populated_project_reports_mixed_container_statuses_and_images`
  (exercises all five `classify()` outcomes: healthy, starting, unhealthy,
  no-healthcheck, not-found, plus a container entirely missing `Config`)
- `TestActionStatusEnvelope::test_json_document_is_the_only_thing_on_stdout`
- `TestActionStatusEnvelope::test_profile_field_carries_the_resolved_profile_name`
- `TestActionStatusEnvelope::test_human_output_distinguishes_missing_from_not_started`
- `TestActionStatusEnvelope::test_human_output_represents_container_name_and_status`

**O3-cli / review_focus false-PASS attack** — `ciu status` is wired into
`cli.py`'s dispatch, `_USAGE`, and `_VERB_HELP`; it never invokes compose
up/down/build/exec; a Docker daemon failure is a clean `[ERROR]` + exit 2,
**never** an empty/healthy-looking report. This is the oracle the package
exists to prove tightly. Proved by:
- `TestDockerUnreachableVsEmpty::test_docker_daemon_unreachable_is_not_caught_and_emptied`
  (RuntimeError from `diagnose._inspect` propagates out of `action_status`
  uncaught; nothing at all is printed on that path)
- `TestDockerUnreachableVsEmpty::test_zero_containers_and_daemon_unreachable_produce_different_outcomes`
  (same stack/project, two different `_inspect` behaviors → a normal return
  vs. a raise — never collapsed)
- `TestDockerUnreachableVsEmpty::test_action_status_never_invokes_compose_docker`
  (monkeypatches `procutil.docker` to raise `AssertionError` if called at
  all — `action_status` never reaches it)
- `TestCliStatusDispatch::test_docker_daemon_unreachable_is_a_clean_error_not_a_traceback`
  (through `cli._status`: RuntimeError → exit 2, `[ERROR] ciu status: ...`
  on stderr, never a raw traceback)
- `TestCliStatusDispatch::test_config_load_failure_is_also_a_clean_error_exit_2`
- `TestCliStatusDispatch::test_reuses_the_same_chain_ciu_up_uses` (asserts
  call order `load_global_config → resolve_profiles → build_selection →
  action_status`, with the exact objects threaded through — same chain
  `ciu up --profile` uses)
- `TestCliStatusDispatch::test_no_profile_flag_resolves_with_names_none`,
  `test_repeatable_profile_flags_expand`,
  `test_comma_form_skips_empty_segments`,
  `test_all_empty_segments_defaults_to_none`,
  `test_json_flag_reaches_action_status`,
  `test_end_to_end_through_cli_status_with_real_action_status`
- `TestCliStatusWiring::test_main_dispatches_the_status_verb`,
  `test_status_is_listed_in_top_level_usage`,
  `test_status_has_its_own_verb_scoped_help`

**O4-docs** — README bullet, FEATURES.md feature-table row + CLI-table row,
SPEC.md §S7.10, CONSUMERS.md §13 worked `--json` example, CHANGES.md
Unreleased entry, BACKLOG-2026-08-24.md CIU-QOL-6 → FIXED with an evidence
paragraph naming the implementing symbols and this LOG. Not exercised by
tests (documentation-only oracle) — verified by direct read of each file
after editing.

## Design choice made under "Degrees of freedom"

SPEC section: **S7.10** under a new "### Status reporting" heading,
immediately after S7.9 (Registry) and before "## S8 — Compose execution".
Chosen over S17 (Image provenance) because `ciu status` reuses S7's own
selection chain (`build_selection`) and S7.7's health-classification
vocabulary directly, and resolves the S8.7 compose-project scheme verbatim
via the shared `_stack_compose_project` helper — it is an orchestration
status query, not a commit/vendor provenance evidence check (S17's actual
subject).

Human (non-JSON) output format: one `info()` line per stack,
`<name>  <compose_project-or-"(not on disk)">  <container>=<status> ...-or-"(no containers)"`
— every field from the JSON envelope is represented, and the
missing-vs-not-started distinction is preserved in the human format too
(`(not on disk)` vs `(no containers)`), not just in JSON.

Single-stack helper: extracted (`_stack_compose_project`) rather than
duplicated inline, and `_stack_compose_projects` was refactored to call it —
this was the explicit recommendation in Context item 2 ("you need the
entry→project association... Extract a single-stack helper both this
function and your new `action_status` can call") and removes the
duplicate-branch drift risk entirely rather than merely documenting it.

## Gate output (real, pasted — not predicted)

Command: `.venv/bin/python run-ciu-tests.py` (full suite, `-n auto`,
`--cov=ciu --cov-branch --cov-fail-under=100`), run AFTER all doc edits and
the new test file were in place, immediately before committing:

```
============================= test session starts ==============================
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0
rootdir: /workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu
configfile: pyproject.toml
plugins: xdist-3.8.0, cov-7.1.0
created: 8/8 workers
8 workers [2478 items]

................................................................................
[... all 8 xdist workers report 100% dots ...]
................................................................

================================ tests coverage ================================
_______________ coverage: platform linux, python 3.14.6-final-0 ________________

Name                                             Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------------------------------
src/ciu/__init__.py                                  3      0      0      0   100%
src/ciu/__main__.py                                  3      0      2      0   100%
src/ciu/_version.py                                 11      0      0      0   100%
src/ciu/activate.py                                119      0     46      0   100%
src/ciu/cli.py                                     700      0    252      0   100%
src/ciu/cli_utils.py                                11      0      0      0   100%
src/ciu/composefile.py                             388      0    180      0   100%
src/ciu/config_constants.py                         29      0      4      0   100%
src/ciu/config_model.py                            276      0    128      0   100%
src/ciu/deploy.py                                 1345      0    566      0   100%
src/ciu/deploy_pkg/__init__.py                       8      0      0      0   100%
src/ciu/deploy_pkg/health.py                       205      0    108      0   100%
src/ciu/deploy_pkg/http_util.py                     24      0      2      0   100%
src/ciu/deploy_pkg/layouts.py                       63      0     24      0   100%
src/ciu/deploy_pkg/phases.py                        76      0     44      0   100%
src/ciu/deploy_pkg/profiles.py                     131      0     64      0   100%
src/ciu/deploy_pkg/registry.py                      38      0     20      0   100%
src/ciu/dev.py                                     196      0     74      0   100%
src/ciu/diagnose.py                                 79      0     34      0   100%
src/ciu/engine.py                                  887      0    292      0   100%
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
TOTAL                                             8146      0   3232      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
============================ 2478 passed in 15.66s =============================
```

Also run in isolation before the full suite (`.venv/bin/python -m pytest
tests/tests/test_ciu_cli_status.py -q`): `26 passed in 2.70s`.

## Scope discipline

Touched only files listed in `scope.touch`
(`src/ciu/deploy.py`, `src/ciu/cli.py`,
`tests/tests/test_ciu_cli_status.py`, `docs/SPEC.md`, `docs/FEATURES.md`,
`README.md`, `docs/CONSUMERS.md`, `CHANGES.md`,
`docs/BACKLOG-2026-08-24.md`, and this LOG). `src/ciu/diagnose.py` was
imported (`from . import diagnose`) and consumed strictly read-only
(`diagnose._inspect`) — never edited; no change to `diagnose.py`,
`engine.py`, `deploy_pkg/health.py`, `config_model.py`, `worktree.py`, or
any `nyxloom-trove/{backlog,decisions,roadmap}.md` file was made or needed.
No blast-radius issue analogous to ciu-P15's was encountered: the only
existing-behavior change is `_stack_compose_projects`'s internal
refactor to call the new `_stack_compose_project` helper, which is
behavior-preserving by construction (same branch, same per-entry logic,
same dedup) and is proven so by every pre-existing
`test_ciu_clean_identity_project.py`/`test_ciu_clean_identity_networks.py`
test continuing to pass unchanged, and by the 100%-coverage full-suite gate
above.

## Result

Not blocked. Commit hashes (both real, read via `git log -1 --format=%H`
after each commit — not predicted):
- `7d3f0e0d2e668f7b4870e882893f60e16199cdd9` — implementation (deploy.py,
  cli.py, tests, docs, CHANGES.md, backlog row)
- `758e8324ba18e2c5bb2045b0b91a621f0705cc87` — this LOG, committed
  separately as instructed
