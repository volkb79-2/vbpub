# LOG — ciu-P14-qol11-eager-s11-validation

- Package: `ciu-P14-qol11-eager-s11-validation` (QOL-11)
- Worktree: `/workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu`
- Branch: `feat/ciu-qol-v8prep-wave`
- Handoff `input_revision`: `3d2531ab`
- Status: **COMPLETE — O1-O5 all done, 0 failures, 100% line+branch coverage**
- Code commits:
  - `d2578430974c71ced9dde0e3c32f1a83311b1833` — the package itself (O1-O5)
  - `cc269db5993bee1530a844e769662ba297c24b69` — follow-up: corrected an
    overclaim in `validate_declared_features`'s own docstring after
    empirically re-checking it (see O1 below)

## Environment note

P13 had already landed on this branch before this package started (verified
via `git log --oneline -5`: `50f032b9 fix(ciu): repair exit_on doc/comment
drift, env-scrub test fixture, QOL-9 (ciu-P13)`), and `tests/conftest.py`'s
autouse env-scrub fixture was present. The bare
`.venv/bin/python run-ciu-tests.py` command worked without any `env -u`
prefix; no fallback needed.

## O1 — `config_model.validate_declared_features` (new function)

Added to `src/ciu/config_model.py` (end of file, after
`validate_stack_provisioning`). Re-verified before writing:

- `src/ciu/deploy_pkg/layouts.py:62-165` (`resolve_layout`, read in full,
  untouched) — confirmed it raises plain `ValueError`, tagged `[S7.5c]`,
  for every malformed-layout case (unknown layout, non-table entry, bad
  `environment`, non-table/empty `hosts`, unknown host, non-list/empty
  `bundles`, bad bundle name, cross-bundle conflict).
- `src/ciu/worktree.py:1357-1375` (`resolve_exec_targets_config`, read in
  full, untouched) — confirmed it is safe to call unconditionally: absent
  `[ciu]`/`[ciu.worktree]`/`exec_targets` all short-circuit to `return {}`;
  only a present-but-wrong-shaped table raises (`WorktreeError`, a
  `RuntimeError` subclass, not `ValueError`). No BLOCKED condition here —
  the "escalate_if" trap for this item did not fire.
- **Import-cycle warning confirmed empirically for the layouts half, not
  just by reading**: I temporarily added a *module-scope*
  `from .deploy_pkg.layouts import resolve_layout` to `config_model.py` as
  a throwaway reproduction, ran `python -c "import ciu.config_model"`, and
  reproduced the exact predicted failure:
  `ImportError: cannot import name 'deep_merge' from partially initialized
  module 'ciu.config_model' (most likely due to a circular import)`,
  traced through `deploy_pkg/__init__.py -> layouts.py -> profiles.py ->
  ..config_model import deep_merge`. Reverted the throwaway line
  immediately after.
- **The `worktree` half of that same claim does NOT actually reproduce** —
  I checked this empirically too, since I try not to assert a cycle I
  haven't seen fail. `worktree.py` does `from . import config_model` (the
  whole submodule, never a specific name) at its own module scope, and
  every `config_model.something` usage inside `worktree.py` is inside a
  function body (confirmed by
  `grep -n "config_model\." src/ciu/worktree.py`, all hits indented under
  a `def`), never at worktree.py's own module scope. I temporarily added
  both `from . import worktree` AND `from .worktree import
  resolve_exec_targets_config` (module-scope, throwaway, reverted
  immediately after each) to `config_model.py` and BOTH imported cleanly
  with no `ImportError` — a submodule-only import doesn't need the target
  module to have finished executing, only a *specific name pulled from
  it* does, and worktree.py never pulls a specific name from config_model
  at its own module scope. So the handoff's "same technique step 2 below
  already requires for `worktree` — apply it here too" over-states this
  specific case: there IS a real, reproduced cycle for `deploy_pkg.layouts`
  (confirmed above), but NOT a second independent one for `worktree`.
  This does not change what I shipped — I still used a function-local
  import for `worktree` too, matching the handoff's explicit instruction
  and costing nothing (it's a harmless, conservative choice, and keeping
  both reused-validator imports function-local next to each other reads
  more consistently than splitting them) — but I'm recording the actual
  empirical result rather than the unverified claim, since a fresh
  reviewer should not have to re-derive this themselves. Final landed
  version imports fine either way: `python -c "import ciu.config_model;
  import ciu.engine; import ciu.deploy"` — all three succeed, no
  `ImportError`.

Body does exactly the three steps in order, each step's own exception left
to propagate unmodified (no try/except inside the function):

1. `for name in global_cfg.get('deploy', {}).get('layouts', {}):
   resolve_layout(global_cfg, hosts_cfg, name)` — zero iterations on an
   absent/empty table.
2. `worktree.resolve_exec_targets_config(global_cfg)`, called
   unconditionally (no guard).
3. `[deploy.provenance].vendor_images`: absent key is a no-op; present but
   not a `list` raises `[S17.5]` naming the type; present as a `list` with
   any non-string or empty-string element raises `[S17.5]` naming the
   index and the offending value. A bare string is rejected by the
   `isinstance(vendor_images, list)` check BEFORE any iteration, so the
   `for v in "nginx"` character-iteration footgun never triggers (verified
   directly — see the smoke test transcript below).

Smoke-tested directly before writing the pytest file:

```
empty OK
OK bare string raised: [S17.5] [deploy.provenance] vendor_images must be a list of non-empty strings, got str.
OK non-string element raised: [S17.5] [deploy.provenance] vendor_images[1] must be a non-empty string, got 5.
valid list OK
OK unknown host raised: [S7.5c] Layout 'x': host 'ghost' is not in the hosts inventory. Available hosts: (none).
OK malformed exec_targets raised: WorktreeError [S16.7] exec target 'tester' must be a table
```

## O2 — wired into `engine.main_execution`

Re-read `src/ciu/engine.py:1140-1265` before editing (per the handoff's own
instruction not to assume the prose's variable names). Confirmed at Step 5
(actual line ~1259-1262 on this branch, after `git log`/`git blame`
shifted line numbers slightly from the handoff's "~1258-1261" estimate —
still the same seam, right after `validate_stack_provisioning`):
`global_config` (assigned at `render_global_chain(...)`, line ~1220) and
`repo_root` (resolved from `--define-root`/`REPO_ROOT`, line ~1216) are
both real local names already in scope by Step 5, exactly as the handoff
described — no BLOCKED condition.

Added `from . import hosts` to the top-level import block (no cycle:
`hosts.py` only imports `config_constants` and `secrets.directives`) and:

```python
config_model.validate_declared_features(global_config, hosts.load_hosts(repo_root))
```

placed immediately after the existing `validate_stack_shape` /
`validate_stack_provisioning` pair, called with `global_config` (never
`stack_config` or `merged`) per the O2 negative constraint.

## O3 — wired into `deploy.action_check`

Re-read `src/ciu/deploy.py:1688-1766` (`action_check`) and `~2789` (a
`profile.config.get('ciu', {})` accessor precedent for
`auto_connect_network`) before editing. Confirmed `profile.config` is the
global config (deep-merged with `topology_overrides` — see
`deploy_pkg/profiles.py`'s `Profile.config` docstring) — no BLOCKED
condition.

Added `from . import hosts as hosts_pkg` to the top-level import block and,
right after the initial `info("=" * 60)` banner and BEFORE the
`for entry in selection:` loop (so it runs even when `selection == []`,
confirmed by the O3 negative test below):

```python
try:
    config_model.validate_declared_features(
        profile.config, hosts_pkg.load_hosts(repo_root)
    )
except (ValueError, worktree_pkg.WorktreeError) as exc:
    error(str(exc))
    return 2
```

Catches both `ValueError` (layout/vendor_images) and `WorktreeError`
(exec-target shape) — a deliberate small departure from the handoff's
literal "letting each step's own ValueError propagate unmodified" phrasing
inside `validate_declared_features` itself (which the function honors: it
does not catch anything), but `action_check`'s own EXISTING per-stack loop
already catches `ValueError` from `validate_stack_shape`/
`validate_stack_provisioning` locally and converts it to `error(); return
2`; catching only `ValueError` here would let a malformed exec-targets
table (which raises `WorktreeError`, a `RuntimeError` subclass) skip that
local handling and fall through to `deploy.py`'s outer
`except BaseException` in `main()` instead — still handled gracefully, but
inconsistently (a different, less specific message path) from the other
two S11 shape defects checked one line away. Not wiring into `action_graph`
(out of scope per O3's negative constraint) — confirmed untouched.

## O4 — tests

New file `tests/tests/test_ciu_config_model_layouts_eager.py` (13 direct
unit tests + 2 integration tests, 15 total):

- Direct: zero/one/multiple valid layouts pass; unknown-host layout raises
  (fixture shape mirrors `test_ciu_deploy_layouts.py`'s own unknown-host
  case, read but not edited — that file is `scope.forbid`); exec_targets
  absent/valid pass, malformed raises (fixture shape mirrors
  `test_ciu_worktree.py`'s own "entry not a table" case, read but not
  edited — not in `scope.touch`); vendor_images: absent key passes, no
  `[deploy.provenance]` table at all passes, bare string raises tagged
  `[S17.5]`, non-string list element raises tagged `[S17.5]` naming the
  index, empty-string element raises tagged `[S17.5]`, valid list passes.
- O2 integration: `test_main_execution_surfaces_bad_globally_declared_layout_without_layout_flag`
  — stubs `check_runtime_dependencies`/`bootstrap_workspace_env`/
  `ensure_workspace_network`/`config_model.render_global_chain`/
  `config_model.render_stack` (the render/env/network machinery, not the
  system under test) and `engine.hosts.load_hosts` (hermetic — does not
  depend on `~/.ciu/hosts.toml` not existing on the runner), then calls the
  REAL `engine.main_execution(working_dir=tmp_path, define_root=tmp_path)`
  with a global config declaring a layout referencing an unknown host and
  no `--layout`-equivalent argument anywhere in the call. Asserts the real,
  unstubbed `resolve_layout` raises `[S7.5c] Layout 'prod': host
  'ghost-host' is not in the hosts inventory`.
- O3 integration: `test_action_check_surfaces_bad_globally_declared_layout_with_empty_selection`
  — constructs `Profile(config=<bad-layout-global-config>)` directly
  (no filesystem/render machinery needed at all, `action_check`'s only
  global-config accessor is `profile.config`), stubs only
  `deploy.hosts_pkg.load_hosts`, calls `deploy.action_check(tmp_path,
  profile, [], {})` with `selection=[]`, asserts `rc == 2` and the tagged
  message appears in captured stdout.
- Neither `resolve_layout` nor `resolve_exec_targets_config` is stubbed
  anywhere in this file — confirmed by grep (`grep -n "resolve_layout\|resolve_exec_targets_config" tests/tests/test_ciu_config_model_layouts_eager.py` shows only the real O1 call chain, no `monkeypatch.setattr` on either).
- Audited every existing `action_check(` call site across the whole test
  suite (`test_ciu_deploy_deeper2.py`, `test_ciu_deploy_direct73.py`,
  `test_ciu_deploy_deeper16.py`, `test_ciu_provisioning.py`,
  `test_spec_contracts.py`) before wiring O3, to confirm none of their
  `profile.config` fixtures declare `deploy.layouts` /
  `ciu.worktree.exec_targets` / `deploy.provenance.vendor_images` — none
  do, so the new eager call is a no-op pass for every pre-existing test
  (this is also what gives the new `try` block's success path its branch
  coverage, alongside the new file's own passing cases).

Per O4's scope: both integration tests live in the ONE new test file
(`scope.touch` lists only `test_ciu_config_model_layouts_eager.py` for
tests — the handoff prose's "may live in the existing engine/deploy test
files ... your call" is not actually an available choice under
`scope.touch`, so both integration tests are in the new file).

## O5 — docs and backlog

- `docs/SPEC.md`'s S11 catalog entry (the paragraph already covering S7.5c
  layout shape / S16.7 exec-target shape / S17.5 vendor_images checks)
  gains one appended sentence: `validate_declared_features` (QOL-11) now
  runs those three checks eagerly on every render path (single-stack
  `main_execution` and profile-mode `action_check`), explicitly framed as
  widened REACH, not new or corrected underlying logic (no "newly added"
  claim, per the O5 negative constraint).
- `docs/BACKLOG-2026-08-24.md`'s `CIU-QOL-11` row: `**Status:** OPEN` →
  `**Status:** ✅ IMPLEMENTED (ciu-P14)`, matching the exact convention
  used by the adjacent `CIU-QOL-9` row, plus a one-line `**Evidence:**`
  pointer naming the new function and both call sites.
- `CHANGES.md`: no `[Unreleased]` section existed at the top of the file
  (top entry was `## [7.0.0] - 2026-08-23`, per the file's own comment new
  release entries are normally CMRU-generated from commit subjects). Per
  the handoff's instruction to "match whatever heading convention the top
  of CHANGES.md currently uses", I checked prior package commits'
  `CHANGES.md` diffs (`git log --all -p -- CHANGES.md | grep -A8 '^+## \[Unreleased\]'`)
  and found the established per-package pattern: a hand-written `##
  [Unreleased]` / `### Fixed` (or `Added`/`Changed`) heading with a
  `- fix(ciu): ...` bullet, no commit hash inline (CMRU fills that in at
  release time). Added exactly that shape as a new section between the
  `<!-- cmru: release history -->` marker and `## [7.0.0]`.

## Gate output (real, pasted verbatim — not paraphrased)

Final run of `.venv/bin/python run-ciu-tests.py` (no env prefix;
conftest.py's autouse scrub was already active), after both commits above:

```
============================= test session starts ==============================
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0
rootdir: /workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu
configfile: pyproject.toml
plugins: xdist-3.8.0, cov-7.1.0
created: 8/8 workers
8 workers [2434 items]

........................................................................ [  2%]
........................................................................ [  5%]
........................................................................ [  8%]
........................................................................ [ 11%]
........................................................................ [ 14%]
........................................................................ [ 17%]
........................................................................ [ 20%]
........................................................................ [ 23%]
........................................................................ [ 26%]
........................................................................ [ 29%]
........................................................................ [ 32%]
........................................................................ [ 35%]
........................................................................ [ 38%]
........................................................................ [ 41%]
........................................................................ [ 44%]
........................................................................ [ 47%]
........................................................................ [ 50%]
........................................................................ [ 53%]
........................................................................ [ 56%]
........................................................................ [ 59%]
........................................................................ [ 62%]
........................................................................ [ 65%]
........................................................................ [ 68%]
........................................................................ [ 70%]
........................................................................ [ 73%]
........................................................................ [ 76%]
........................................................................ [ 79%]
........................................................................ [ 82%]
........................................................................ [ 85%]
........................................................................ [ 88%]
........................................................................ [ 91%]
........................................................................ [ 94%]
........................................................................ [ 97%]
..........................................................               [100%]
================================ tests coverage ================================
_______________ coverage: platform linux, python 3.14.6-final-0 ________________

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
src/ciu/config_model.py                            276      0    128      0   100%
src/ciu/deploy.py                                 1324      0    562      0   100%
src/ciu/deploy_pkg/__init__.py                       8      0      0      0   100%
src/ciu/deploy_pkg/health.py                       192      0     98      0   100%
src/ciu/deploy_pkg/http_util.py                     24      0      2      0   100%
src/ciu/deploy_pkg/layouts.py                       63      0     24      0   100%
src/ciu/deploy_pkg/phases.py                        69      0     40      0   100%
src/ciu/deploy_pkg/profiles.py                     131      0     64      0   100%
src/ciu/deploy_pkg/registry.py                      38      0     20      0   100%
src/ciu/dev.py                                     196      0     74      0   100%
src/ciu/diagnose.py                                 79      0     34      0   100%
src/ciu/engine.py                                  884      0    292      0   100%
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
TOTAL                                             8072      0   3204      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
============================ 2434 passed in 14.42s =============================
```

0 failures, 0 errors, 100.00% line+branch coverage across the whole `ciu`
package (not just the touched files).

## Files changed

Package commit `d2578430974c71ced9dde0e3c32f1a83311b1833`:

- `src/ciu/config_model.py` — new `validate_declared_features` function.
- `src/ciu/engine.py` — `from . import hosts` + one call at Step 5 of
  `main_execution`.
- `src/ciu/deploy.py` — `from . import hosts as hosts_pkg` + one call
  (wrapped in a local try/except) near the top of `action_check`.
- `tests/tests/test_ciu_config_model_layouts_eager.py` — new file, 15
  tests.
- `docs/SPEC.md` — S11 catalog entry correction.
- `docs/BACKLOG-2026-08-24.md` — `CIU-QOL-11` row closed with evidence.
- `CHANGES.md` — new `[Unreleased]` / `### Fixed` entry.

Follow-up commit `cc269db5993bee1530a844e769662ba297c24b69`:

- `src/ciu/config_model.py` — corrected `validate_declared_features`'s own
  docstring after empirically re-checking the `worktree` half of its
  import-cycle claim and finding it did not reproduce (see O1 above); no
  behavior change, comment-only.

No `scope.forbid` file was touched. No `escalate_if` condition fired —
nothing BLOCKED.
