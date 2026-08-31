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

---

## Review round 1 fixes (coordinator review, 4 blockers) -- rebase + 3 new commits

The branch was rebased onto current `main` (`git rebase main`) rather than
merged again: `git log --oneline main..HEAD` before the rebase showed the
6-commit branch (including the earlier sync-merge `616637c1` and the
now-superseded `61fa0bf9` CIU-78 filing); a backup ref
`backup/ciu-P38-before-rebase` was created first. Default (non-`--rebase-merges`)
rebase correctly dropped the merge commit and replayed only the 4 unique
commits. One real conflict, at the old `61fa0bf9` commit: resolved per the
review's explicit prescription by `git rebase --skip` (that commit's entire
content -- a CIU-78 backlog filing -- was already superseded by main's own
`aa6cf1fd`, confirmed by diff-stat showing it touched nothing else). A second
conflict at `924dc844` (formerly `4884b960`, marking CIU-74 FIXED) was content,
not structural: resolved by keeping only the CIU-74 paragraph, dropping the
now-redundant CIU-78-FILED paragraph it used to sit next to, and rewording both
the header paragraph and the CIU-74 row's own status text to stop claiming
CIU-76/CIU-78 were "pre-existing... unrelated" failures (main had already fixed
both by the new base) -- exactly the review's blocker 2 instruction. Rebase
completed clean; `git merge-base --is-ancestor main HEAD` confirmed `main` is
now an ancestor.

## Commit `dbd8b13c` -- test(ciu): CIU-74 review blocker 3 -- cover render_configfiles' own instances setdefault

Reproduced the reviewer's deletion probe locally first: commenting out
`composefile.py`'s third `merged_ciu.setdefault("instances", {})` (inside
`render_configfiles`'s per-instance loop) left the full local suite at
**3268 passed**, identical to baseline -- confirmed genuinely untested, not a
false positive. Added
`test_render_configfiles_ciu_instances_membership_check_with_no_fanout` to
`test_ciu_configfile_schema.py`, reusing its `_setup()` harness per the
review's instruction: a configfile template `"fans_out = {{ 'api' in
ciu.instances }}\n"`, rendered through `render_configfiles` with a
`ciu_context` carrying no `instances` key, must produce `"fans_out =
False\n"`. Verified both directions again after writing the test: passes
against current code (11/11 in that file); raises
`jinja2.exceptions.TemplateError: ... 'dict object' has no attribute
'instances'` when the setdefault line is deleted. Full local suite:
**3269 passed** (one more than the 3268-passed probe run, since this commit
adds exactly one new test), 100% line+branch on `--cov=ciu --cov-branch`.

## Commit `4aa47250` -- fix(ciu): CIU-74 review blocker 4 -- scaffold.py's Jinja render docstring was false

Minimum required fix only, per the review's own framing (decision on
adopting `StrictUndefined` there is deferred to CIU-79, not attempted
blind here). Corrected `_render_jinja`'s docstring (`scaffold.py:91-107`),
which claimed "the SAME engine production uses (S3.2 step 1)" -- true before
CIU-74, false after. Added a matching comment at the OTHER site, the `ciu
init` validation preflight's own inline `Environment` construction inside
`build_files` (`scaffold.py:275-310`), flagging the false-certification risk
directly at the point a future reader would otherwise assume a green `ciu
init` proves no undefined-reference bug. Docstring/comment only, zero
behavior change -- confirmed by running the scaffold/init-tagged subset
(`-k "scaffold or init"`, 43 passed) and the full suite (3269 passed, 100%
coverage) unchanged.

## Commit `00f57308` -- backlog(ciu): file CIU-79 -- scaffold.py's two Jinja render paths still lenient

Filed per the review's explicit instruction (own new entry, not attempted
in-package): next free ID verified against the post-rebase `main` state
(`CIU-74`..`CIU-78` all already present; `CIU-79` free). Cites both line
ranges (`scaffold.py:91-107`, `scaffold.py:275-310`), the false-certification
risk, and the explicit need to render-and-check ciu's own shipped scaffold
templates before adopting `StrictUndefined` there -- verbatim per the
review's own three required contents.

## Final real gate run (post-rebase, post-all-4-blocker-fixes)

`./run-gate.py ciu --worktree /workspaces/vbpub/.worktrees/ciu-P38-strict-undefined`
at commit `00f57308`: **PASS, exit 0**. R0 (raw pytest, whole suite) PASS;
R1 (assay's changed-lines coverage judgment) PASS at 100.0%. See REPORT §4
for the verbatim verdict. All three previously-failing pre-existing tests
(the two `dont_write_bytecode` tests, CIU-78; the lease-clock flake, CIU-76)
are green now that the branch is rebased past main's own fixes for both.
