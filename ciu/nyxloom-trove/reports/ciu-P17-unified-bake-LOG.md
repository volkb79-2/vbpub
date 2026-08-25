# LOG — ciu-P17-unified-bake

- Package: `ciu-P17-unified-bake` (CIU-QOL-7)
- Worktree: `/workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu`
- Branch: `feat/ciu-qol-v8prep-wave`
- Handoff `input_revision`: `370ea8141f7f69399a751f2d5731a8ccf5419921`
- Starting HEAD (confirmed before any edit): `cdf01d5a5a9a566652fe2e9226f4420f516fe991`
  (ciu-P16's final LOG commit — matched the handoff's expected pointer).
- Status: **COMPLETE — O1-O4 all done.** Superseded the PARTIAL/BLOCKED
  status below: the controller read the 3 dependent tests personally,
  widened `scope.touch` to include the 2 files (handoff commit `8e2bc813`),
  and directed a behavior-equivalence check before any deletion/porting —
  see "Amendment — O1 redone and landed, O4 completed" at the end of this
  file for the full second-pass account. The narrative below this point
  (through the first "Gate output" section) is the ORIGINAL first-attempt
  record, kept verbatim for the audit trail — do not read it as the current
  state of the code.

  *(Original first-attempt status, kept for the audit trail: O2, O3,
  O4(partial) DONE. O1 (action_build removal) BLOCKED — a real
  blast-radius issue outside `scope.touch`, evidence below. Following the
  ciu-P15 pattern: shipped everything independently gate-able, reverted the
  risky change after gathering concrete evidence, documented rather than
  silently widening scope.)*

## Summary (read this first)

The handoff's Work item 1 says "verify `action_build` is genuinely dead
(O1); delete it." I re-verified independently at this package's own commit
tip (not trusting the carve-time grep), confirmed `action_build` has zero
callers and no CLI flag routes to it **anywhere in `src/ciu/`** — this part
of the carve-time finding holds. But O1's own oracle text scopes the
dead-code check to `src/ciu` specifically ("zero callers anywhere in
src/ciu"), and I found the wider reason why: **`action_build` is directly
unit-tested by 3 tests across 2 files that are NOT in this package's
`scope.touch`**:

- `tests/tests/test_ciu_deploy_actions_remaining.py::test_build_uses_selected_application_targets_and_propagates_docker_failure`
  (this file is otherwise a mixed grab-bag of unrelated `action_*` tests —
  only this one test calls `action_build`)
- `tests/tests/test_ciu_deploy_deeper8.py` — both of its 2 tests
  (`test_build_defaults_to_all_and_reports_success_when_bake_succeeds`,
  `test_build_reports_red_when_docker_client_is_unavailable`); this entire
  file exists solely to test `action_build`

Deleting `action_build` therefore breaks the test gate (0 failures
required) unless those 2 files are also edited — but neither is in
`scope.touch`, and my instructions are explicit: "Touch ONLY files in
scope.touch" and, for a real blast-radius issue outside the originally-
scoped files, "stop, document the evidence and options fully in the LOG
rather than either shipping red or silently widening scope yourself." This
does not literally match either named `escalate_if` bullet (no *production*
caller was found, and there is no import-cycle), so it isn't a hard STOP —
but it is exactly the "real blast-radius issue outside originally-scoped
files" case, so I followed the ciu-P15 precedent: build the risky change to
get concrete (not hypothetical) failure evidence, revert it cleanly, ship
everything else, and leave the decision to the controller.

## Concrete evidence (built, ran, then reverted)

1. Deleted `action_build` (`src/ciu/deploy.py:2590-2617`, the function body
   verbatim — kept `collect_bake_targets_from_selection`, per O1).
2. Ran the full suite: `.venv/bin/python -m pytest tests -q`:

```
FAILED tests/tests/test_ciu_deploy_actions_remaining.py::test_build_uses_selected_application_targets_and_propagates_docker_failure
FAILED tests/tests/test_ciu_deploy_deeper8.py::test_build_defaults_to_all_and_reports_success_when_bake_succeeds
FAILED tests/tests/test_ciu_deploy_deeper8.py::test_build_reports_red_when_docker_client_is_unavailable
3 failed, 2475 passed, 20 warnings in 22.62s
```

Exact failure (all 3, same shape — `AttributeError`, not a value/shape
mismatch, since the function is simply gone):

```
tests/tests/test_ciu_deploy_actions_remaining.py:164: in test_build_uses_selected_application_targets_and_propagates_docker_failure
>       assert deploy.action_build(
               ^^^^^^^^^^^^^^^^^^^
E       AttributeError: module 'ciu.deploy' has no attribute 'action_build'

tests/tests/test_ciu_deploy_deeper8.py:30: in test_build_defaults_to_all_and_reports_success_when_bake_succeeds
>       assert deploy.action_build(tmp_path, [], use_cache=True) == 0
               ^^^^^^^^^^^^^^^^^^^
E       AttributeError: module 'ciu.deploy' has no attribute 'action_build'

tests/tests/test_ciu_deploy_deeper8.py:44: in test_build_reports_red_when_docker_client_is_unavailable
>       assert deploy.action_build(tmp_path, [{"path": "applications/api"}], use_cache=True) == 1
               ^^^^^^^^^^^^^^^^^^^
E       AttributeError: module 'ciu.deploy' has no attribute 'action_build'
```

3 failures, matching exactly the 3 direct `action_build(...)` call sites
found by `grep -rn "action_build" --include="*.py" .` across the WHOLE
repo (not just `src/ciu/`) — no unaccounted failures, no other test file
affected.

3. Reverted: `git checkout -- src/ciu/deploy.py`. Confirmed clean:
   `git diff --stat src/ciu/deploy.py` produced no output after the
   revert — `deploy.py` is byte-for-byte its pre-package state.

### Grep evidence for the dead-code claim itself (O1, holds)

```
$ grep -rn "action_build(" src/ciu/
src/ciu/deploy.py:2590:def action_build(repo_root: Path, selection: list[dict], *, use_cache: bool) -> int:
```
(only the definition — zero callers in `src/ciu/`)

```
$ grep -rn -- "'--build'" src/ciu/ ; grep -rn -- '"--build"' src/ciu/
(no output — no CLI flag literal routes to it)
```

`--build` only appears in `src/ciu/deploy.py` as historical docstring prose
(`"""--build: thin ``docker buildx bake`` invocation..."""`, and the
`build_action_sequence` docstring listing v1's retained action-flag names)
— never as an actual registered argparse flag or dispatch branch. Confirmed
`build_action_sequence`'s `action_flags` dict (deploy.py ~2699-2710) does
NOT contain `"--build"` as a key. **The carve-time finding is correct: zero
production callers, zero CLI routing.** The only thing keeping it in the
tree is its own dedicated/embedded test coverage, outside `scope.touch`.

`collect_bake_targets_from_selection` (kept, reused by O2 below) and
`collect_bake_targets_from_phases` (left untouched): re-grepped, both still
have real callers (`collect_bake_targets_from_phases` is called by
`tests/tests/test_ciu_deploy_direct80.py` directly and is otherwise
unreferenced in `src/ciu/` — it is test-only-exercised but I was NOT
instructed to remove it, only to leave it alone unless MY OWN grep showed
it also has zero callers; it has a direct test caller, which is a different
question from "does production code call it", and the handoff's Work
section only asked me to delete `action_build`, not
`collect_bake_targets_from_phases` — left exactly as found, no scope
creep).

## Recommended resolution paths (not something I'm authorized to choose
between — leaving this for the controller, per the ciu-P15 precedent)

1. **Widen `scope.touch`** to include
   `tests/tests/test_ciu_deploy_actions_remaining.py` and
   `tests/tests/test_ciu_deploy_deeper8.py`, then: delete
   `test_ciu_deploy_deeper8.py` entirely (its only reason to exist is
   `action_build`) and delete the one `action_build`-testing function from
   `test_ciu_deploy_actions_remaining.py` (its other 5 tests are unrelated
   and must stay). This is what I'd expect to be the actual next step — the
   cleanup is mechanical and shallow (delete, don't rewrite), matching the
   `action_build`/`--build` internal contract having zero public surface
   (O4's own point).
2. **Leave `action_build` in place** (still unreachable from any CLI path,
   confirmed above) as intentionally-retained-but-dead code for this
   package, and file/track the removal as a distinct, narrowly-scoped
   follow-up package that scopes the 2 test files from the start.

I did not choose between these myself — that would be exactly the "silently
widening scope" the instructions warn against for option 1, or a unilateral
scope-narrowing decision for option 2.

## O2 — `ciu bake --profile NAME` resolves via the same chain (DONE)

New `_bake(rest: list[str]) -> int` helper in `src/ciu/cli.py` (right after
`_status`, which it deliberately mirrors), replacing the old 6-line inline
`elif verb == "bake":` block with `raise SystemExit(_bake(rest))` (same
dispatch pattern as `_status`, `_worktree`, etc.).

- **No `--profile`**: `positional = [a for a in rest if a != "--no-cache"]`
  then (since no `--profile` flag is present) `targets = positional` —
  this is the EXACT same two lines the old inline block used, so the
  no-profile path's argv construction is unchanged token-for-token. Verified
  byte-identical via the pre-existing tests that were NOT touched and still
  pass unchanged: `test_ciu_cli_parser.py::test_bake_constructs_default_and_no_cache_argv`
  and `test_ciu_final_branch112.py::test_bake_without_no_cache_omits_flag_and_propagates_exit`
  (both assert the exact argv list, both green with zero changes to their
  own file).
- **With `--profile`**: `load_global_config(repo_root)` ->
  `resolve_profiles(global_cfg, cli_profiles)` -> `build_selection(profile)`
  — the identical 3-function chain `_status` (ciu-P16) already uses for
  `ciu up --profile` parity, imported from `.deploy` exactly as `_status`
  does. The resulting `selection` is passed to (the REAL, un-mocked in
  tests) `collect_bake_targets_from_selection(selection)` to get the target
  list — the same pure resolver `action_build` uses, so `ciu bake --profile
  X` computes exactly the same target set `ciu up --profile X` would deploy
  onto. `repo_root` is resolved via `dev.resolve_repo_root(None,
  Path.cwd())`, matching `_status`'s own call shape (no `--define-root` flag
  was requested by the handoff title/oracle text, so none was added).
  `--profile` supports repeated flags and comma-separated values, expanded
  with the identical logic `_status`/`deploy._run` already use (kept for
  consistency, not independently re-derived).
- Either branch feeds the SAME final `cmd = ["docker", "buildx", "bake"] +
  (targets or ["all"]) + ["--load"]`, `cmd += bake_revision_args()`,
  conditional `--no-cache` append, `subprocess.call(cmd)` — one shared tail,
  confirmed by reading the diff: there is exactly ONE `cmd = [...]` /
  `bake_revision_args()` / `subprocess.call` triple in the new function.
- `load_global_config`/`resolve_profiles` failures (`ValueError`/
  `RuntimeError`) are caught and rendered as a clean `[ERROR] ciu bake: ...`
  + exit 2, mirroring `_status`'s own error-mapping (never a raw
  traceback).

## O3 — `--profile` vs. positional-targets mutual exclusion (DONE)

`has_profile_flag = any(a == "--profile" or a.startswith("--profile=") for
a in positional)` gates entry into the profile-resolution branch — this is
the prefix-aware check copied from the `up --layout` precedent's
`_LAYOUT_FORBIDDEN` pattern (`a == flag or a.startswith(flag + "=")`),
checked BEFORE deciding which branch to take (not just inside it), so
`--profile=NAME` is never misread as a raw positional buildx target the way
a bare `"--profile" in rest` membership check would misread it (the exact
regression class named in `review_focus` and fixed once already at
checkpoint C for `--layout`).

Inside the branch, `argparse`'s own `--profile` `action="append"` consumes
both `--profile NAME` and `--profile=NAME` forms natively; anything left
over in `remaining` after that (i.e. an explicit positional target given
alongside `--profile`, in either order) triggers:

```
ciu bake: --profile is mutually exclusive with explicit build targets --
--profile resolves the target list from the selection model (the same
chain `ciu up --profile` uses); pass one or the other, not both.
```
+ exit 2. `--no-cache` is stripped from `rest` BEFORE this check runs
(`positional = [a for a in rest if a != "--no-cache"]`), so it never
participates in the conflict check and combines with either mode
unchanged — tested explicitly for both modes.

Tests (`tests/tests/test_ciu_cli_bake.py::TestCliBakeProfileFlagConflict`):
space form + a following target, equals form + a following target
(the specific regression case), and a target given BEFORE `--profile=NAME`
(order-independence) — all assert exit 2 AND that `subprocess.call` is
never reached (a raising fake), so a caught bug can't slip through as a
"passing" false negative.

## O4 — docs (DONE for the shipped half; the internal-cleanup half is
correctly NOT claimed, since it isn't shipped)

- **`docs/FEATURES.md`**: the `ciu bake` CLI-reference table row now
  documents `--profile NAME` and the selection-chain reuse (CIU-QOL-7).
  `README.md` has no existing `bake` feature line (grepped, confirmed) —
  nothing to update there.
- **`docs/SPEC.md`**: added **S7.11** immediately after S7.10 (`ciu
  status`, ciu-P16's own recent addition) — the same section, extended,
  since S7.11 is the natural continuation of "verbs that reuse the S7.10
  selection chain." Describes the unified resolution, the byte-identical
  no-`--profile` regression bar, the mutual-exclusion contract, and that
  every invocation still carries S17.1's revision stamp. Deliberately does
  NOT mention `action_build` — SPEC.md documents the public contract, and
  `action_build` has no public CLI surface regardless of whether it's
  removed yet, so its (blocked) removal status is not a SPEC-level fact.
- **`CHANGES.md`**: new bullet at the top of the existing `[Unreleased] /
  ### Added` list (newest-first, matching this file's own ordering — the
  ciu-P16 status-verb bullet was already there, ciu-P17's goes above it)
  describing the shipped `--profile` capability. Deliberately does **NOT**
  claim `action_build`'s removal as a "internal-only cleanup" — the handoff
  asked me to say that ONLY once the removal actually ships (it explicitly
  frames the note as describing what happened, to avoid a false
  breaking-change alarm); since I did not remove it, writing that sentence
  now would itself be the misleading claim O4's negative constraint warns
  against, just inverted (claiming a cleanup that didn't happen, instead of
  mischaracterizing one that did).
- **`docs/BACKLOG-2026-08-24.md`**: CIU-QOL-7's `**Status:**` line ->
  `PARTIAL (ciu-P17) — public half shipped, internal cleanup blocked`, with
  a full **Evidence** block naming exactly what shipped (O2/O3, file/test
  pointers) and what didn't (O1, pointing at this LOG for the complete
  failure evidence and resolution options). Did NOT mark it `FIXED` — the
  backlog's own "Fix" line names BOTH halves ("make public `ciu bake
  --profile core` resolve the selection... **Remove internal action_build
  path**"), and only the first half is done.

## Regression bar re-confirmed

`git diff src/ciu/deploy.py` (after the revert) is empty — the ONLY
production file changed by this package is `src/ciu/cli.py`, and the
no-`--profile` path inside it is provably unchanged (same two list-comp
lines, same `cmd`/`bake_revision_args`/`subprocess.call` tail, same
pre-existing tests passing with zero edits to their own files).

## Gate output (real, pasted verbatim — final state)

```
================================ tests coverage ================================
_______________ coverage: platform linux, python 3.14.6-final-0 ________________

Name                                             Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------------------------------
src/ciu/__init__.py                                  3      0      0      0   100%
src/ciu/__main__.py                                  3      0      2      0   100%
src/ciu/_version.py                                 11      0      0      0   100%
src/ciu/activate.py                                119      0     46      0   100%
src/ciu/cli.py                                     730      0    262      0   100%
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
TOTAL                                             8176      0   3242      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
============================ 2493 passed in 15.23s =============================
```

2493 passed (2478 pre-existing + 15 new in `tests/tests/test_ciu_cli_bake.py`),
0 failed, 100.00% line+branch coverage across the whole `ciu` package.
`src/ciu/cli.py` grew from 670 to 730 statements (the new `_bake` helper +
docstrings); `src/ciu/deploy.py` unchanged at 1345 statements (confirms the
revert — this package's `deploy.py` line count matches its pre-package
state, since O1's deletion was reverted).

## Oracle table

| Oracle | Status | Satisfied by |
|---|---|---|
| O1-dead-code-removal | **BLOCKED** | `action_build` re-confirmed dead in `src/ciu/` (zero callers, no CLI flag) — but 3 tests across 2 files outside `scope.touch` call it directly; deleted it to get concrete failure evidence (3 failures, pasted above), then reverted `src/ciu/deploy.py` to its pre-package state. Full evidence and 2 resolution options above and in the BACKLOG entry. |
| O2-profile-resolution | DONE | `src/ciu/cli.py`'s new `_bake` helper; `--profile` resolves via `load_global_config` -> `resolve_profiles` -> `build_selection` -> `collect_bake_targets_from_selection`, identical to `ciu up --profile`'s chain; no-`--profile` path byte-identical (pre-existing tests pass unchanged); tests in `tests/tests/test_ciu_cli_bake.py`. |
| O3-flag-conflict | DONE | Prefix-aware `--profile`/`--profile=NAME` detection gates the branch; leftover positional targets after argparse consumes `--profile` trigger a clear stderr message + exit 2; tests cover space form, equals form, and target-before-flag ordering, with `subprocess.call` a raising fake to prove the reject actually short-circuits. |
| O4-docs | DONE (shipped half only) | `docs/FEATURES.md` CLI table, `docs/SPEC.md` S7.11, `CHANGES.md` Unreleased/Added all describe the shipped `--profile` capability; `docs/BACKLOG-2026-08-24.md` CIU-QOL-7 marked PARTIAL (not FIXED) with full evidence, since the backlog's own "Fix" line names the O1 half too and that half is not shipped. |

## Files changed

- `src/ciu/cli.py` — new `_bake(rest)` helper (replaces the old inline
  `elif verb == "bake":` block with `raise SystemExit(_bake(rest))`);
  `_USAGE` and `_VERB_HELP["bake"]` updated for `--profile NAME`.
- `tests/tests/test_ciu_cli_bake.py` — new file, 15 tests (O2/O3).
- `docs/FEATURES.md` — `ciu bake` CLI-reference row documents `--profile`.
- `docs/SPEC.md` — new **S7.11** clause (extends the S7.10 subsection).
- `CHANGES.md` — new `[Unreleased] / ### Added` bullet.
- `docs/BACKLOG-2026-08-24.md` — CIU-QOL-7 -> PARTIAL with evidence.
- `nyxloom-trove/reports/ciu-P17-unified-bake-LOG.md` (this file).

Not touched: `src/ciu/deploy.py` (deleted `action_build` then reverted —
`git diff` shows zero remaining delta), `src/ciu/engine.py`,
`src/ciu/deploy_pkg/profiles.py`, `src/ciu/deploy_pkg/phases.py` (all
`scope.forbid`), `nyxloom-trove/backlog.md`, `nyxloom-trove/decisions.md`,
`nyxloom-trove/roadmap.md` (all `scope.forbid`).

No `scope.forbid` file was touched (confirmed by `git status --short`
before committing — only the files listed above are modified/new).

## Commit hash(es)

Read back with `git log -1 --format=%H` immediately after each commit (not
predicted):

- Code/tests/docs commit: `7696c44daa40d022a540cf3ea8343ba058eee14a`
  — `src/ciu/cli.py`, `tests/tests/test_ciu_cli_bake.py` (new),
  `docs/FEATURES.md`, `docs/SPEC.md`, `CHANGES.md`,
  `docs/BACKLOG-2026-08-24.md`.
- LOG commit: `9f1548b4af0a3fed06414de5ab7ce869f7e1cb13` (recorded in this
  follow-up edit, per the ciu-P16 precedent — the LOG's own hash isn't
  knowable until after it's committed).

---

## Amendment — O1 redone and landed, O4 completed

Controller reviewed the BLOCKED evidence above, read the 3 dependent tests
personally (`test_ciu_deploy_deeper8.py:30,43`,
`test_ciu_deploy_actions_remaining.py:164`), and widened `scope.touch`
(handoff commit `8e2bc813`) to include both files, with an explicit
instruction: before deleting or porting those 3 tests, determine whether
the REAL behavior they assert (empty-selection-means-all,
Docker-unavailable-is-red, revision-label stamping, `--no-cache`
propagation, exact `commands` argv) is already equivalently covered for
the actual reachable path (`cli._bake`'s shared `docker buildx bake`
invocation tail), since that path — not `action_build`'s separate
implementation — is what both `ciu bake` and `ciu bake --profile` actually
run through.

### Behavior-equivalence check (done BEFORE touching anything)

1. **Revision-stamping + `--no-cache` propagation for the shared
   invocation tail**: already proven, unconditionally on whether
   `--profile` is given, by two PRE-EXISTING tests that this package never
   touched and that stayed green throughout —
   `test_ciu_cli_parser.py::test_bake_constructs_default_and_no_cache_argv`
   (revision `--set` arg present, `--no-cache` appended, `subprocess.call`'s
   return code (3) propagates) and
   `test_ciu_final_branch112.py::test_bake_without_no_cache_omits_flag_and_propagates_exit`
   (revision arg present, `--no-cache` correctly OMITTED, return code (9)
   propagates). Both exercise the exact `cmd = [...]; cmd +=
   bake_revision_args(); if no_cache: ...; return subprocess.call(cmd)`
   tail `_bake` uses for BOTH modes — confirmed by reading the diff: there
   is exactly one such tail in `_bake`, shared by both branches.
2. **`--profile`'s target-resolution correctness against the same tail**:
   already proven by this package's own `test_ciu_cli_bake.py` (15 tests,
   landed in the first commit) — in particular
   `test_resolves_via_the_same_chain_ciu_up_uses_in_order` (reuses the
   deleted `action_build` test's OWN fixture shape —
   `applications/api`/`tools/admin`/`infrastructure/network` ->
   `["admin", "api"]` — proving identical target-selection semantics) and
   `test_empty_buildable_selection_defaults_to_all` (empty selection ->
   `all`, the direct analogue of `action_build`'s
   `test_build_defaults_to_all_and_reports_success_when_bake_succeeds`).
3. **"docker buildx bake command runs and returns nonzero" (the failure
   mode `test_build_uses_selected_application_targets_and_propagates_docker_failure`
   exercised via a fake returning `CompletedProcess(cmd, 1, ...)`)**:
   already covered by the same two pre-existing argv tests above (both
   assert the mocked `subprocess.call`'s nonzero return value propagates
   through `SystemExit`).
4. **GAP FOUND**: no test anywhere proved what happens on the real,
   reachable path when the `docker` BINARY itself is missing (`procutil.docker`
   raising `FileNotFoundError`, the scenario
   `test_build_reports_red_when_docker_client_is_unavailable` covered for
   `action_build` alone). `cli._bake`'s shared tail calls raw
   `subprocess.call(cmd)` with no `try/except` around it (unchanged from
   the pre-P17 `ciu bake` verb — this is a PRE-EXISTING characteristic, not
   something this package introduced or is authorized to redesign, since
   the no-`--profile` regression bar requires the argv-construction/
   invocation code to stay byte-identical). Per the controller's option 3:
   closed this gap with ONE new test rather than silently dropping it —
   `TestCliBakeDockerUnavailable::test_missing_docker_binary_propagates_uncaught_rather_than_a_silent_success`
   in `test_ciu_cli_bake.py`, using the same monkeypatch style as the
   deleted test (patch the call site to raise `FileNotFoundError`), pinning
   the REAL current contract: the error propagates uncaught rather than
   being silently swallowed into an apparent success. This is a coverage
   addition, not a behavior change — confirmed by not touching `_bake`'s
   invocation tail at all in this amendment.

Conclusion: per the controller's option 2, the 3 `action_build` tests
assert behavior unique to its own private `procutil.docker` wrapper and
distinct print-message contract (`"build complete"`, `"docker not
available: docker"`) that the real CLI path never produced in the first
place — there was nothing to port. Deleted them outright, alongside
`action_build` itself, with the one genuine gap (item 4) closed instead of
dropped.

### O1 — redone (DONE)

- `src/ciu/deploy.py`: deleted `action_build` (`repo_root, selection, *,
  use_cache -> int`, its whole body) — re-verified immediately before
  deletion, at this exact commit tip, that it had zero callers and no CLI
  flag routing anywhere in `src/ciu/` (identical grep methodology as the
  first attempt, same result). `collect_bake_targets_from_selection` kept
  untouched (still reused by `cli._bake`'s `--profile` path — O2).
  `collect_bake_targets_from_phases` also left untouched (re-grepped: still
  has a direct test caller in `test_ciu_deploy_direct80.py`; the handoff
  only ever asked me to delete `action_build`, never this function, so
  leaving it alone is not scope creep in either direction).
- `tests/tests/test_ciu_deploy_deeper8.py`: deleted the whole file (both of
  its 2 tests existed solely to test `action_build`; nothing else in the
  file).
- `tests/tests/test_ciu_deploy_actions_remaining.py`: deleted only
  `test_build_uses_selected_application_targets_and_propagates_docker_failure`
  (the file's other 5 tests — preflight/clean/list-actions — are unrelated
  and kept byte-for-byte unchanged); removed the now-unused `import
  subprocess` (it was used only by the deleted test); trimmed the module
  docstring's "clean/build" mention to "clean" (accurate — the file no
  longer tests any build action).
- `tests/tests/test_ciu_cli_bake.py`: added
  `TestCliBakeDockerUnavailable` (1 test, the gap-closing test from item 4
  above); no other changes.
- `src/ciu/cli.py`: `_bake`'s own docstring updated from "the internal
  (dead, CLI-unreachable) `action_build` path also uses" (accurate at the
  time it was written, mid-BLOCKED) to "the removed internal `action_build`
  path used to reuse" (accurate now that it's actually gone) — a comment
  fix only, zero behavioral change (confirmed by the gate staying green
  with the exact same pass count minus/plus only the test file deltas
  described above).

### Regression bar re-confirmed

The no-`--profile` path's own tests
(`test_bake_constructs_default_and_no_cache_argv`,
`test_bake_without_no_cache_omits_flag_and_propagates_exit`, and this
package's own `TestCliBakeNoProfileRegressionBar` class, 4 tests) all still
pass with ZERO changes to their own files across both this package's
commits — the byte-identical no-`--profile` contract was never touched by
the O1 rework, only `deploy.py` (action_build) and the 2 dependent test
files changed in this amendment.

### O4 — docs (DONE, completing the deferred half)

- **`docs/BACKLOG-2026-08-24.md`**: CIU-QOL-7's `**Status:**` line
  `PARTIAL (ciu-P17) — public half shipped, internal cleanup blocked` ->
  `✅ FIXED (ciu-P17)`, with the **Evidence** block rewritten to name BOTH
  the target-resolution unification (unchanged from before) AND the
  dead-code removal (new: what was deleted, why the 3 dependent tests were
  deleted rather than ported, and the one coverage gap closed instead of
  dropped).
- **`CHANGES.md`**: extended the existing `ciu bake --profile NAME` bullet
  (added in the first commit) with a closing sentence: the internal
  `action_build`/`--build` path "had NO CLI surface (dead code,
  unreachable from any verb or flag) and is removed as an internal-only
  cleanup — not a breaking change for any user" — the exact framing O4's
  negative constraint asked for (never describe an unreachable removal as
  breaking).

### Full gate — final state (real, pasted verbatim)

```
================================ tests coverage ================================
_______________ coverage: platform linux, python 3.14.6-final-0 ________________

Name                                             Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------------------------------
src/ciu/cli.py                                     730      0    262      0   100%
src/ciu/deploy.py                                 1325      0    562      0   100%
--------------------------------------------------------------------------------------------
TOTAL                                             8156      0   3238      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
============================ 2491 passed in 14.53s =============================
```

(Full 40-module coverage table omitted here for length — every module
reports 100%, identical in shape to the first-attempt table except
`src/ciu/deploy.py` shrank from 1345 (mid-attempt, before the first revert)
back down to 1325 — 20 fewer statements than the pre-package 1345/1327-ish
baseline lineage, i.e. `action_build`'s own body, permanently gone this
time, no revert. Test count: 2493 (end of first commit) - 3
(deleted `action_build` tests) + 1 (new `TestCliBakeDockerUnavailable`
test) = 2491.)

### Oracle table — final

| Oracle | Status | Satisfied by |
|---|---|---|
| O1-dead-code-removal | **DONE** | `action_build` deleted from `src/ciu/deploy.py`, re-verified dead at this exact commit tip; `collect_bake_targets_from_selection` kept (O2 reuses it); its 3 dependent tests deleted (behavior was private to `action_build`'s own wrapper/messages, never reachable from any CLI path); the one genuine coverage gap (docker-binary-missing on the real path) closed with a new test instead of silently dropped. |
| O2-profile-resolution | DONE | Unchanged from the first attempt — see above. |
| O3-flag-conflict | DONE | Unchanged from the first attempt — see above. |
| O4-docs | **DONE** | `docs/BACKLOG-2026-08-24.md` CIU-QOL-7 -> FIXED with full evidence naming both halves; `CHANGES.md` action_build removal framed correctly as a non-breaking internal cleanup; `docs/SPEC.md`/`docs/FEATURES.md` unchanged from the first commit (already accurate, described only the public contract). |

### Files changed (this amendment)

- `src/ciu/deploy.py` — `action_build` deleted.
- `tests/tests/test_ciu_deploy_deeper8.py` — deleted (whole file).
- `tests/tests/test_ciu_deploy_actions_remaining.py` — one test deleted,
  unused import removed, docstring trimmed.
- `tests/tests/test_ciu_cli_bake.py` — `TestCliBakeDockerUnavailable`
  added (1 test).
- `src/ciu/cli.py` — `_bake`'s docstring comment updated (no behavior
  change).
- `docs/BACKLOG-2026-08-24.md` — CIU-QOL-7 -> FIXED.
- `CHANGES.md` — action_build removal note added to the existing bullet.

No `scope.forbid` file was touched at any point in this package (both
attempts) — confirmed by `git status --short` before each commit.

### Commit hashes (this amendment, read back via `git log -1 --format=%H`,
not predicted)

- Dead-code removal + test updates + docs commit: `11f833ad933dad13341117e0d65e56c8b4103240`
- LOG commit (this update): committed next, see repository history.
