# ciu-P38 -- CIU-74 StrictUndefined render fix -- implementation LOG

| | |
|---|---|
| Package | `ciu-P38-strict-undefined` |
| Branch | `fix/ciu-P38-strict-undefined` (worktree `.worktrees/ciu-P38-strict-undefined`) |
| Base | `a78a0046` (vbpub main at worktree creation) |
| Backlog item | CIU-74 (`ciu/KNOWN_ISSUES_TODO_BACKLOG.md`) |
| Gate | `./run-gate.py ciu --worktree <worktree-root>` -- see REPORT for the real verdict |
| Status | Fix + tests + docs COMPLETE; real gate is RED for reasons confirmed pre-existing and unrelated (see REPORT) |

One entry per commit, each naming the commit hash it describes.

---

## Commit `8416ce93` -- fix(ciu): CIU-74 -- render_jinja2_text uses StrictUndefined; ciu.instances always present

The core fix. `render_jinja2_text` (`src/ciu/config_model.py:386`) switched from
the bare `jinja2.Template(template_text)` constructor (library-default
`Undefined`) to `jinja2.Environment(undefined=jinja2.StrictUndefined,
keep_trailing_newline=True).from_string(template_text)`, exactly as the backlog
entry proposed. This is the one render function all three call sites share
(`composefile.py:287` in `render_compose`, `composefile.py:973` in
`render_configfiles`, `config_model.py:418` in `render_toml_template`) --
confirmed by grep before touching anything (`grep -rn "render_jinja2_text\|render_toml_template" src/ciu/`),
matching the backlog's own claim that this hasn't changed since 2026-08-31.

**The S7.5b interaction** (already resolved by the task brief, not re-litigated
here): `ciu.instances` was previously ABSENT from the render context whenever
no service anywhere resolved a fan-out count > 1, so `'api' in ciu.instances`
evaluated to `False` by accident under the old default `Undefined`. Under
`StrictUndefined` that same membership test on a genuinely undefined name would
raise instead. Fixed by making every context-assembly site that merges the
`ciu` table in default `instances` to `{}` rather than omitting it:

- `config_model._make_render_context` (`config_model.py:471`, used by
  `render_global_chain`/`render_stack` for every TOML-layer template)
- `composefile.render_compose` (`composefile.py:290`, compose templates)
- `composefile.render_configfiles`'s per-instance render loop
  (`composefile.py:979`, configfile templates)

Each site adds one line, `merged_ciu.setdefault("instances", {})`, right after
the existing `merged_ciu.update(ciu_context)` merge -- matching the existing
(already duplicated 3x) merge-in style rather than introducing a new shared
helper, to keep the diff minimal and localized to the exact lines the task
scoped.

`ciu.selected_profiles`/`ciu.deployed_stacks` (S3.12) are deliberately left
alone -- they keep their existing fail-loud-when-absent contract; only
`ciu.instances` gets the always-present treatment, per the task's own
resolution and confirmed correct by re-reading `_make_render_context`'s own
docstring, which already documented the *opposite* contract for those two
facts before this change.

**Tests** (`tests/tests/test_ciu_config_model.py`):
- `test_render_jinja2_text_unknown_var_raises_strict_undefined` replaces
  `test_render_jinja2_text_unknown_var_renders_empty` (the old test asserted
  the silent-empty behavior this package removes).
- `test_render_jinja2_text_leaf_typo_raises_naming_the_bad_key` -- the
  backlog's own oracle: the `dstdns--postgres` repro
  (`{{ deploy.project_name }}-{{ deploy.environment_tg }}-postgres` against a
  present `[deploy]` table) must raise naming `environment_tg`. Confirmed the
  real error text: `jinja2.exceptions.TemplateError: Jinja2 render error:
  'dict object' has no attribute 'environment_tg'`.
- `test_render_jinja2_text_leaf_typo_controlled_wrong_implementation` -- the
  task's required controlled-wrong-implementation sanity check, kept as a
  permanent regression test: calls the real `jinja2.Template(...)` (library
  default, i.e. what `render_jinja2_text` looked like before this fix)
  directly and asserts it reproduces `"dstdns--postgres"` -- proving the strict
  test above is actually exercising the fix, not a tautology. Manually
  confirmed the same thing at the shell before writing this test (see REPORT
  Sanity Check section).
- `test_render_jinja2_text_keeps_trailing_newline` -- covers
  `keep_trailing_newline=True`.

**Tests** (`tests/tests/test_ciu_render_selection_context.py`):
- `test_render_compose_ciu_instances_membership_check_with_no_fanout` --
  compose-side: `'api' in ciu.instances` renders `False` (not a raise) with
  `_CTX` (no `instances` key) as the `ciu_context`, proving the
  always-present-empty-mapping fix specifically -- StrictUndefined alone
  (without the context-assembly fix) would make this same test raise instead.
- `test_global_chain_ciu_instances_membership_check_with_no_fanout` -- the
  TOML-layer side of the identical proof, via `render_global_chain` /
  `_make_render_context`.

**Pre-existing test updated** (`tests/tests/test_ciu_configfile_schema.py`):
- `test_valid_render_passes_and_mount_is_emitted` asserted an exact rendered
  string with no trailing newline; `keep_trailing_newline=True` now preserves
  the template's own trailing `\n`. Updated the expected string to include it
  -- this is the ONE pre-existing test in the whole suite that depended on the
  old silent-newline-stripping behavior (found by running the full suite
  before and after the fix and diffing the failure set).

**Docs**: `docs/SPEC.md` S3.2 (documents the `StrictUndefined` +
`keep_trailing_newline=True` contract) and S7.5d (`ciu.instances` is now
documented as ALWAYS PRESENT, replacing the old "absent when nothing fans out,
fails loudly" language that this fix deliberately supersedes for that one
fact); `docs/CONSUMERS.md` S16 worked example (same correction, plus a callout
box explaining the leaf-typo-now-raises behavior change); `README.md`'s "one
template adapts to every host" bullet (one added sentence).

Baseline established before touching any test: local
`PYTHONPATH=src python3 -m pytest tests -q` on the unmodified worktree gave
**3260 passed, 1 failed** (`test_re_expiring_after_an_extend_becomes_lease_expired_again`
-- a real-clock-vs-frozen-fixture issue, unrelated, later confirmed already
filed as CIU-76 on main). After this commit, the same local run (no xdist,
devcontainer venv, `PYTHONPATH=src`) gives **3265 passed**, same 1 pre-existing
failure, with `--cov=ciu --cov-branch` showing `src/ciu/composefile.py` and
`src/ciu/config_model.py` both at 100%/100% line+branch and `TOTAL` 100%.

---

## Commit `616637c1` -- Merge branch 'main-sync-tmp' into fix/ciu-P38-strict-undefined

Not a content commit -- a sync merge. The worktree's base (`a78a0046`) was 3
commits behind `main` by the time the fix above was ready to gate, including
`b8102bc2` (`fix(ciu): correct stale assay pin version 2.2.0 -> 2.3.0`) --
without it, `./run-gate.py ciu` refuses every invocation with a pin-version
mismatch (verified live: the first `--dry-run` against the un-merged branch
showed `declared 2.2.0, artifact reports: assay 2.3.0`). Merged clean, no
conflicts (the only overlapping-directory file, `KNOWN_ISSUES_TODO_BACKLOG.md`,
was touched by main's `858766d1` filing CIU-76/CIU-77, not by anything in this
package's own first commit).

---

## Commit `61fa0bf9` -- backlog(ciu): file CIU-78 -- dont_write_bytecode test/env mismatch found gating CIU-74

Filed via the `backlog` skill (legacy big-file convention -- `ciu` has not
migrated to the per-entry `nyxloom-trove/backlog/` schema yet, confirmed by
absence of `[backlog_entries]` in `ciu/nyxloom-trove/nyxloom.toml`). See
REPORT for the full defect description and why it's confirmed pre-existing and
unrelated to CIU-74.

---

## Gate runs (no further commits -- see REPORT for the verdict discussion)

Two full real-gate runs, both `./run-gate.py ciu --worktree
/workspaces/vbpub/.worktrees/ciu-P38-strict-undefined` (the WORKTREE ROOT, not
`.../ciu` -- see REPORT §"the --worktree path gotcha"), at commits `616637c1`
and `61fa0bf9` respectively. Both: **FAIL/COMMAND_FAILED, exit 1**, R0 (raw
pytest exit code) FAIL, **R1 (assay's changed-lines 100% coverage judgment)
PASS at 100.0%**, identical 3 failing tests both times (2 newly-found and now
filed as CIU-78, 1 already filed as CIU-76). Verbatim verdict in REPORT.

---

## Commit `4884b960` -- backlog(ciu): mark CIU-74 FIXED -- ciu-P38 (8416ce93)

Closed out the CIU-74 backlog row itself (`Medium | OPEN -> Medium | FIXED`),
following the file's own documented convention ("A FIXED issue means code,
behavioral tests, SPEC, and user documentation landed together" -- all four
landed in `8416ce93`). Points at this REPORT for the real gate verdict rather
than re-summarizing it in the backlog row. Added a matching "Last updated"
header paragraph.
