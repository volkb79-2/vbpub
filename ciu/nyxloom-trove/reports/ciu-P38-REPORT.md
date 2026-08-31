# ciu-P38 -- CIU-74: `render_jinja2_text` StrictUndefined -- REPORT

| | |
|---|---|
| Package | `ciu-P38-strict-undefined` |
| Branch | `fix/ciu-P38-strict-undefined` |
| Worktree | `/workspaces/vbpub/.worktrees/ciu-P38-strict-undefined/ciu` |
| Backlog | CIU-74 fixed and closed; CIU-78 found, then superseded by main's own fix (`aa6cf1fd`) once rebased; scaffold.py StrictUndefined-adoption decision filed as CIU-79, renamed to CIU-80 (collision with ciu-P37), then to **CIU-81** (second collision, with ciu-P41) -- NOT fixed, out of scope by design |
| Base | rebased onto `main` @ `1f47601c` (35 commits; `git rebase main`, 4 of 9 real commits hit conflicts -- see §10) |
| Final commit | `a7388015` |
| Gate (FINAL, post-renumber, post-rebase) | `./run-gate.py ciu --worktree <worktree-root>` -- **PASS, exit 0** (R0 PASS, R1 PASS at 100.0%) -- see §10 |

This REPORT covers two coordinator review rounds. Round 1 (4 blockers, §8)
and round 2 (a second backlog-ID collision plus the `main` rebase, §9-§10)
are both fully addressed. §§1-3 and §5-7 describe the original
implementation and are still accurate; §4 carries round 1's post-rebase gate
verdict (superseded by §10's, which is the truly final one); the original
pre-round-1-rebase FAIL verdict is kept in §4a for the record.

## 1. What was done

`render_jinja2_text` (`src/ciu/config_model.py:386`) built `jinja2.Template(text)`
with the library-default `Undefined`, so a mistyped LEAF key rendered silently
as the empty string instead of raising. Changed to
`jinja2.Environment(undefined=jinja2.StrictUndefined,
keep_trailing_newline=True).from_string(text)`, exactly the backlog's proposed
fix. This is the one render function all three call sites share
(`composefile.py:287`/`973`, `config_model.py:418`) -- re-confirmed by grep
before starting; unchanged since the backlog entry was filed.

**S7.5b interaction** (resolved per the task brief's own decision, not
re-litigated): `ciu.instances` is now an ALWAYS-PRESENT mapping (defaulting to
`{}`) in every context-assembly site that merges the `ciu` table into a render
context, instead of being omitted when nothing fans out:

- `config_model._make_render_context` (`config_model.py:471`)
- `composefile.render_compose` (`composefile.py:290`)
- `composefile.render_configfiles` (`composefile.py:979`)

Each site adds `merged_ciu.setdefault("instances", {})` right after the
existing merge -- a one-line addition per site, matching the existing
(already 3x-duplicated) merge pattern rather than introducing a new shared
helper. `ciu.selected_profiles`/`ciu.deployed_stacks` keep their existing,
deliberately opposite, fail-loud-when-absent contract (S3.12) -- unchanged.

Full list of touched files (original implementation; §8 lists the review-round
additions):

```
ciu/README.md
ciu/docs/CONSUMERS.md
ciu/docs/SPEC.md
ciu/src/ciu/composefile.py
ciu/src/ciu/config_model.py
ciu/tests/tests/test_ciu_config_model.py
ciu/tests/tests/test_ciu_configfile_schema.py
ciu/tests/tests/test_ciu_render_selection_context.py
ciu/KNOWN_ISSUES_TODO_BACKLOG.md
```

Every file is inside the scope the task named: the render function, the
`ciu.instances` context-assembly sites (all in `composefile.py`/
`config_model.py`), test files under `tests/`, and the three named docs.

## 2. Pre-existing tests that had to change, and why each was correct

**`tests/tests/test_ciu_config_model.py::test_render_jinja2_text_unknown_var_renders_empty`**
-- deleted, replaced by `test_render_jinja2_text_unknown_var_raises_strict_undefined`.
This test's entire purpose was to assert the exact silent-empty behavior CIU-74
removes (`# Jinja2 default: undefined renders as empty string` /
`assert result == ""`). Keeping it would mean asserting the bug is still
present. Correct to replace, not a workaround.

**`tests/tests/test_ciu_configfile_schema.py::TestConfigfileSchemaValidation::test_valid_render_passes_and_mount_is_emitted`**
-- updated the expected rendered string from `'log = "myapp"\nport = 8080'` to
`'log = "myapp"\nport = 8080\n'`. `keep_trailing_newline=True` is a required
part of the backlog's own proposed fix signature (not something this package
invented) -- it preserves a template's own trailing newline instead of the
`Environment`/`Template` default silently stripping exactly one. The template
source (`'log = "{{ app.name }}"\nport = 8080\n'`) does end with `\n`; the
test's old expectation was asserting that newline got silently dropped, which
is itself the same class of silent-corruption CIU-74 is about, just for
whitespace instead of a leaf value. Correct to update, not a workaround.

These are the ONLY two pre-existing tests in the whole suite that depended on
the old behavior -- found by running the full local suite before and after the
fix (`PYTHONPATH=src python3 -m pytest tests -q`, no xdist, devcontainer venv)
and diffing the failure sets:

- Before any change: `3260 passed, 1 failed` (the pre-existing lease-clock
  flake, see §4a).
- After the render fix, before touching any test: `3258 passed, 3 failed` --
  the 1 pre-existing flake plus exactly these two.
- After updating both: `3265 passed, 1 failed` (only the pre-existing flake
  remains), with `--cov=ciu --cov-branch` showing both
  `src/ciu/composefile.py` and `src/ciu/config_model.py` at 100% line+branch,
  `TOTAL` 100%.

## 3. Sanity check: controlled wrong implementation

Manually confirmed at the shell, before writing any test, that reverting to
the bare `jinja2.Template(...)` constructor reproduces exactly the backlog's
`dstdns--postgres` claim:

```
$ python3 -c "
from jinja2 import Template
result = Template('{{ deploy.project_name }}-{{ deploy.environment_tg }}-postgres').render(deploy={'project_name': 'dstdns', 'environment_tag': 'prod'})
print(repr(result))
"
'dstdns--postgres'
```

And that the fixed `render_jinja2_text` raises, naming the bad key:

```
$ python3 -c "
from ciu.config_model import render_jinja2_text
render_jinja2_text('{{ deploy.project_name }}-{{ deploy.environment_tg }}-postgres', {'deploy': {'project_name': 'dstdns', 'environment_tag': 'prod'}})
"
jinja2.exceptions.TemplateError: Jinja2 render error: 'dict object' has no attribute 'environment_tg'
```

This sanity check is also captured as a permanent test,
`test_render_jinja2_text_leaf_typo_controlled_wrong_implementation` in
`test_ciu_config_model.py`, which calls the real `jinja2.Template(...)`
directly (not `render_jinja2_text`) and asserts it still reproduces the silent
`"dstdns--postgres"` output -- proving the strict-raise test next to it is
actually exercising the fix, not a tautology.

## 4. The FINAL real gate verdict (post-rebase, post-review-fixes; verbatim)

Ran `./run-gate.py ciu --worktree /workspaces/vbpub/.worktrees/ciu-P38-strict-undefined`
from inside `ciu/`, at the final commit `00f57308` (after the rebase onto
`main`@`384993b6` and all three review-fix commits, §8). Console output:

```
run-gate: admission: lane 'ciu' declares no resources.memory — not memory-accounted (shared-infra rules still apply)
run-gate: rev 23 | lane ciu | env [environments.tester-unified] in central /workspaces/vbpub/.worktrees/ciu-P38-strict-undefined/run-gate.toml | slice dev-background.slice ($CGROUP_PARENT_DEV_BACKGROUND)
run-gate: ephemeral env (nothing declared)
run-gate: budget 30m (advisory)
run-gate: docker argv: /usr/bin/docker run -d --name run-gate-vbpub-ciu-180305-1788140080 --cgroup-parent dev-background.slice -e CGROUP_PARENT_DEV_BACKGROUND=dev-background.slice -v /home/vb/volkb79-2/vbpub:/home/vb/volkb79-2/vbpub -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local bash -c '...'
assay-2.3.0.pyz: OK
ciu: PASS (exit 0)
  commit: 00f5730830d9097ba9d33629dad2fb2aa17c6f9d
  argv: /opt/tester-venv/bin/python run-ciu-tests.py
run-gate: verdict artifact: /workspaces/vbpub/.worktrees/ciu-P38-strict-undefined/ciu/.assay/verdict-ciu.json
run-gate: lane 'ciu' exit 0
GATE_EXIT=0
```

The gate's own exit status is **0 (PASS)**, read in its own step, not piped
(per AGENTS.md "Read the exit status from the job, never from the wrapper").

Verdict artifact (`.assay/verdict-ciu.json`), the two claims:

```json
{
  "outcome": "PASS",
  "exit_code": 0,
  "commit": "00f5730830d9097ba9d33629dad2fb2aa17c6f9d",
  "claims": [
    { "rigor": "R0", "status": "PASS", "verified_by_assay": true },
    { "rigor": "R1", "status": "PASS", "coverage": { "pct": 100.0, "considered": 3, "covered": 6, "executable": 6 }, "verified_by_assay": true }
  ],
  "judgment": {
    "r1": { "mode": "changed_lines", "require_branch": true, "fail_under": 100.0 },
    "resolved": { "base": "384993b6be0342f685743dded83deadf3728a7b6", "language": "python", "source_roots": ["src"] }
  }
}
```

**R0** (the raw `run-ciu-tests.py` / whole-suite pytest exit code) is **PASS**.
**R1** (assay's own changed-lines coverage judgment against this package's
actual diff, base `384993b6` = current `main`) is **PASS at 100.0%**. Local
run at the same commit (`PYTHONPATH=src python3 -m pytest tests -q
--cov=ciu --cov-branch`, no xdist): **3269 passed**, `TOTAL` coverage 100%
line+branch across all 40 modules in `src/ciu/`.

Both previously-failing pre-existing tests (the two `dont_write_bytecode`
tests, CIU-78; the lease-clock flake, CIU-76) are green now that the branch
is rebased past main's own independent fixes for both (`aa6cf1fd`, and
`eb023f24`/`384993b6` respectively) -- exactly what the review predicted
("Reviewer's local full-suite run on your branch shows exactly one real
remaining failure (CIU-76, fixed by the P36 merge) -- should go green after
rebase").

## 4a. The ORIGINAL (pre-rebase, pre-review) gate verdict -- superseded, kept for the record

Before the coordinator review, the branch was based on a stale `main` snapshot
(`a78a0046`, itself synced forward once by a plain merge to `858766d1` --
commit `616637c1`, since superseded by the proper rebase in §8). At that base,
`./run-gate.py ciu` returned **FAIL/COMMAND_FAILED, exit 1** with R0 FAIL /
R1 PASS at 100.0%, on exactly 3 failures:

```
FAILED tests/tests/test_ciu_deploy_actions.py::test_check_suppresses_bytecode_writes_while_importing_hooks
FAILED tests/tests/test_ciu_deploy_actions.py::test_check_restores_the_bytecode_flag_after_a_failed_import
FAILED tests/tests/test_ciu_worktree_reap.py::TestLeaseLifecycleChangesTheNextSurvey::test_re_expiring_after_an_extend_becomes_lease_expired_again
================== 3 failed, 3263 passed in 138.64s (0:02:18) ==================
```

All three were confirmed pre-existing and unrelated to this package at the
time (reproduced with zero code changes; none of the implicated files
-- `deploy.py`, `provisioning.py`, `worktree.py`,
`test_ciu_deploy_actions.py`, `test_ciu_worktree_reap.py` -- appear anywhere
in this package's diff). The lease-clock one was already filed as CIU-76 on
`main`; the two `dont_write_bytecode` ones were newly found here and filed as
CIU-78 (commit `61fa0bf9`, since dropped during the rebase -- §8 -- because
`main` had independently fixed the same defect in the meantime, in
`aa6cf1fd`). The review's independent verification of this same reasoning
(rather than disputing it) is why the prescribed remedy was "rebase past the
fixes," not "diagnose the failures."

## 5. The `--worktree` path gotcha (recording for whoever reads this next)

The task brief's literal example, `./run-gate.py ciu --worktree
<absolute-path-to-your-worktree>/ciu`, is misleading. `run-gate.py` resolves
the judged project path as `<worktree>/<project-dir-relative-to-invoking-toplevel>`;
since `run-gate.py` is invoked from inside `ciu/` and the invoking git
toplevel is the WORKTREE ROOT (`.worktrees/ciu-P38-strict-undefined`), the
relative part is already `ciu`. Passing `--worktree .../ciu-P38-strict-undefined/ciu`
(ending in `/ciu`, matching the brief literally) makes it resolve to a
DOUBLED, nonexistent `.../ciu/ciu` inside the container (`cd` would fail).
Confirmed via `--dry-run` before running for real. The correct value is the
WORKTREE ROOT, `.../ciu-P38-strict-undefined` (no trailing `/ciu`).

## 6. Base history: sync merge, then proper rebase (superseding it)

Originally the worktree's stale base (`a78a0046`) was brought forward with a
plain `git merge main` (commit `616637c1`) so the gate's assay-pin fix
(`b8102bc2`) would be present -- adequate to run the gate, but left the branch
still behind `main`'s later commits and, per the review, an awkward base for
judging the diff. The review's blocker 1 prescribed a proper `git rebase
main` instead; §8 covers that rebase, which replaced `616637c1`'s lineage
entirely (the merge commit itself carried no unique content, so the rebase
simply dropped it and replayed this package's own commits directly onto
current `main`).

## 7. Scope discipline

Nothing outside the task's named scope was touched: the CIU-78 backlog filing
(later superseded by the rebase, §8) and CIU-81 backlog filing (§8) are both
the sanctioned "find something out of scope -> record it, don't fix it" path
(task's own "If blocked" clause + the estate's `backlog` skill); the base
history changes (sync merge, then rebase) were necessary to run the gate
against current `main` at all and introduced no unresolved content conflicts
with this package's own diff (§8 details the two conflicts the rebase itself
hit and how each was resolved).

## 8. Review round 1 -- four blockers, all addressed

Coordinator review verdict: ACCEPT-conditional, 4 blockers.

**Blocker 1 (stale base).** Rebased onto current `main` (`384993b6`) via
`git rebase main` from a backup ref (`backup/ciu-P38-before-rebase`, created
first). The branch's own merge commit (`616637c1`) carried no unique
content and was dropped automatically; the 5 real commits were replayed.
Two conflicts, both in `ciu/KNOWN_ISSUES_TODO_BACKLOG.md`:
  - At the old CIU-78-filing commit: `git rebase --skip`, per the review's
    explicit prescription -- confirmed first (`git show --stat`) that commit
    touched nothing but that one file, so skipping it discarded nothing else.
  - At the mark-CIU-74-FIXED commit: resolved by hand, dropping the
    now-redundant "CIU-78 FILED" paragraph (main's own `aa6cf1fd` already
    carried a FIXED version) and rewording the CIU-74 header paragraph and
    row status text to stop claiming CIU-76/CIU-78 were still-open
    pre-existing failures -- this IS blocker 2's fix, done in the same
    conflict-resolution step since both blockers touched the identical
    lines. Final commit for this step: `924dc844`.

**Blocker 2 (CIU-78 backlog conflict).** Resolved as part of blocker 1's
conflict resolution above: my `61fa0bf9` was skipped entirely (main's
`aa6cf1fd` already carries CIU-78 marked FIXED, with a fuller and more
accurate fix description than my FILED-only row would have downgraded it
to), and the CIU-74 paragraph/row's stale "pre-existing... unrelated"
phrasing was corrected to point at `main`'s independent fixes instead.

**Blocker 3 (untested `render_configfiles` setdefault, `composefile.py:979`).**
Reproduced the reviewer's deletion probe first (commenting out that one
`setdefault` line, full suite stayed at 3268 passed -- no failure). Added
`test_render_configfiles_ciu_instances_membership_check_with_no_fanout` to
`tests/tests/test_ciu_configfile_schema.py`, built on its `_setup()` harness
exactly as instructed: a configfile template `"fans_out = {{ 'api' in
ciu.instances }}\n"` rendered through `render_configfiles` (not
`render_compose`) with a `ciu_context` carrying no `instances` key must
render `"fans_out = False\n"`. Verified both directions: passes at HEAD;
raises `jinja2.exceptions.TemplateError: ... 'dict object' has no attribute
'instances'` when the setdefault line is deleted. Commit: `dbd8b13c`.

**Blocker 4 (scaffold.py's two untouched, now-misdocumented Jinja paths).**
Minimum required fix only, as instructed: corrected `_render_jinja`'s
docstring (`scaffold.py:91-107`), which falsely claimed "the SAME engine
production uses (S3.2 step 1)" post-CIU-74, and added a comment at the `ciu
init` validation preflight's own inline `Environment` construction inside
`build_files` (`scaffold.py:275-310`) flagging the false-certification risk
in place, at the point a future reader would otherwise trust a green `ciu
init` too far. Zero behavior change (confirmed: scaffold/init-tagged test
subset 43 passed, full suite 3269 passed / 100% coverage, both unchanged).
Commit: `4aa47250`. Filed the decision ask as its own new entry (filed as
CIU-79, the free ID at the time against the post-rebase `main` state which
then carried CIU-74 through CIU-78; renamed to CIU-80 after a collision with
ciu-P37's own independent CIU-79 filing, then to **CIU-81** after a second
collision with ciu-P41's own independent CIU-80 filing that landed on `main`
mid-flight -- both renames are their own separate commits, S8 below) --
citing both line ranges, the false-certification risk, and the explicit need
to render-and-check ciu's own shipped scaffold templates before
`StrictUndefined` can be adopted
there safely; not attempted blind, per the review's own instruction.
Commit: `00f57308`.

**Final state after all four:** `./run-gate.py ciu` **PASS, exit 0** (§4);
`git merge-base --is-ancestor main HEAD` confirms `main` is a true ancestor
of the final commit.

## 9. Review round 2 -- second renumber (CIU-80 -> CIU-81), then rebase

The fresh adversarial reviewer confirmed all four round-1 blockers fully
closed (independently re-ran the `render_configfiles` deletion probe,
confirmed it now catches two failures; confirmed the CIU-78-skip left zero
trace; confirmed the gate artifact: PASS, R0 PASS, R1 100% at 6/6 lines).
One new, not-this-package's-fault blocker: **CIU-80 collided a second time**
-- this time with `ciu-P41`, which merged to `main` (`cafacce6`,
`27ab3574`: "file CIU-80 for the stricter variant") while this package's
fix round was in flight. The CIU-79 -> CIU-80 rename (§8, commit `a70871af`)
was correct and collision-free at the moment it was made; `main` simply
acquired its own independent CIU-80 afterward.

Renumbered CIU-80 -> **CIU-81** across the same four files the first rename
touched: `KNOWN_ISSUES_TODO_BACKLOG.md` (header paragraph + table row),
`src/ciu/scaffold.py:101` and `:276` (the two comments the coordinator named
explicitly), and this package's own LOG/REPORT. Confirmed `main` carries no
existing CIU-81 (`git show main:ciu/KNOWN_ISSUES_TODO_BACKLOG.md | grep -c
CIU-81` = 0); confirmed no test anywhere under `tests/` asserts on the
literal string `CIU-80` or `CIU-81`; confirmed the backlog row's pipe count
is unchanged; ran the scaffold/init-tagged test subset (43 passed) as a
comment-only-change smoke check. Full gate re-run not required for the
renumber itself, per the coordinator's own instruction.

**Caution for whoever reads LOG.md next:** an early, over-broad `sed`
pass on this second renumber also rewrote the FIRST rename's own historical
narrative (which correctly says CIU-79 -> CIU-80, describing what commit
`a70871af` actually did) to incorrectly say CIU-79 -> CIU-81. Caught before
committing and hand-corrected -- LOG.md's two rename sections now each
describe their own actual commit accurately. Lesson for future backlog-ID
renumbers on this file: a blind `sed -i 's/\bOLD\b/NEW\b/g'` is only safe on
documents that describe CURRENT state; it corrupts documents (like this
package's own LOG) that narrate PAST renames by their old numbers on
purpose.

Rebase onto current `main` and its own resulting REPORT update follow in
§10 below.

## 10. Rebase onto current `main` (35 commits) -- and the second collision it exposed

The fresh adversarial reviewer verified all four round-1 blockers fully
closed (independently re-ran the `render_configfiles` deletion probe --
confirmed it now catches TWO failures with the setdefault removed, this
package's own new test plus a pre-existing one -- and confirmed the CIU-78
skip left zero trace in `test_ciu_deploy_actions.py`; confirmed the gate
artifact: PASS, R0 PASS, R1 100% at 6/6 lines). One new blocker, not this
package's fault: **CIU-80 collided a second time**, with `ciu-P41`, which
merged to `main` (`27ab3574`: "file CIU-80 for the stricter variant") while
this package's fix round was still in flight -- covered in §9 above
(renumber to CIU-81, commit `bc888e84` pre-rebase).

**Action 1: renumber, done first (§9).** Already covered above.

**Action 2: rebase onto current `main`.** Backup ref
`backup/ciu-P38-before-rebase-2` created first. `main` had moved to `1f47601c`
by the time of this rebase (35 commits ahead of the branch's previous base
`384993b6`; the coordinator's cited `0ad5372d` had itself already been
superseded by 2 more commits by the time this session checked fresh, exactly
as the coordinator warned it might).

**`git merge-tree --write-tree main HEAD` run BEFORE the rebase, per the
coordinator's explicit instruction** ("do a `git merge-tree` check before
finalizing rather than assuming clean, the same way P41's reviewer caught a
real hazard there") -- and it caught a real one: TWO conflicting paths, not
the one (`KNOWN_ISSUES_TODO_BACKLOG.md`) both the coordinator and this
package expected. **`ciu/README.md` also conflicted.** Root cause: this
package's own README edit (bullet 3, the "one template adapts to every
host" line, S3.2/StrictUndefined callout) sits on the line immediately
ADJACENT to two of `main`'s own edits to the same numbered list (bullet 4,
CIU-71's `--project-directory` addition; bullet 7, CIU-64/65's `ciu check`
integration addition) -- confirmed via `git diff --unified=0` on both sides
that the actual edited LINES never overlap (mine: line 76 only; main's:
lines 77 and 80), but git's line-based 3-way merge still conflicts on
adjacent changed lines with zero shared context between them. Not a logical
conflict -- both edits are independent, additive sentences on different
bullets -- but git cannot auto-resolve it, so it would have silently become
a real rebase conflict if `--dry-run`-style verification hadn't been done
first (matching exactly the class of hazard the coordinator described from
P41's own review).

**Rebase execution** (`git rebase main`, from `main`@`1f47601c`): the
branch's own prior sync-merge commit (`616637c1`, no unique content) was
dropped automatically as expected; conflicts hit at 4 of the 9 real commits:

1. **First fix commit** (`README.md`): the adjacent-bullet conflict found by
   `merge-tree` above. Resolved by hand -- combined both bullets' additions
   (mine on bullet 3, main's on bullet 4), verified the numbered list reads
   correctly afterward with no duplication.
2. **Mark-CIU-74-FIXED commit** (`KNOWN_ISSUES_TODO_BACKLOG.md`): the
   CIU-70/CIU-71 rows this package's stale snapshot still showed OPEN had
   since been independently FIXED by `ciu-P40`/`ciu-P37` on `main` --
   confirmed by diffing "ours" vs "theirs" vs "base" directly (ours == base
   for the CIU-74 row itself; the CIU-70/71 rows differed only in
   FIXED-status text, not structure) before resolving, to avoid silently
   reverting either already-fixed entry. Resolution: kept `main`'s CIU-70/71
   FIXED rows verbatim, applied only this package's own CIU-74 FIXED-row
   edit on top.
3. **File-CIU-79 commit** (`KNOWN_ISSUES_TODO_BACKLOG.md`, two separate
   conflict blocks -- header paragraph and table row): `main` had
   independently acquired its OWN legitimate `CIU-79` (ciu-P37: `ciu dev`'s
   `_build_dev_image`) since this package's original filing point. Rather
   than replay the two-hop 79->80->81 rename dance through two more
   increasingly fragile conflict-prone commits, resolved by inserting this
   package's row directly as **CIU-81** here (skipping the intermediate
   CIU-80 state entirely) -- confirmed `main`'s real CIU-79 (P37) and CIU-80
   (P41) rows both survive untouched, and this package's own row lands at
   its final correct number in one step.
4. **First rename commit `a70871af`** (all four files it touches): since the
   backlog table had already jumped straight to CIU-81 in step 3, this
   commit's own diff (which expects to find, and rename, `CIU-79`) no longer
   matched cleanly anywhere consistent -- `scaffold.py`'s two comments and
   the backlog header paragraph auto-merged to an inconsistent intermediate
   `CIU-80` while the table row already said `CIU-81`. Rather than leave
   that inconsistency to (maybe) self-resolve across the two remaining
   original rename commits, made this commit's resulting tree fully
   self-consistent at `CIU-81` across all four files in one step, copying
   this package's own already-verified final LOG.md/REPORT.md content
   directly from the (not-yet-replayed) `bc888e84` commit for the two files
   that are exclusively this package's own (never touched by `main`) --
   `scaffold.py`/`KNOWN_ISSUES_TODO_BACKLOG.md` got the equivalent hand
   edits instead, since `main` has independent content in both that a
   wholesale copy would have regressed. Verified via `git show bc888e84 --
   ciu/KNOWN_ISSUES_TODO_BACKLOG.md ciu/src/ciu/scaffold.py` that its
   payload for those two files is PURELY the same ID-text substitution
   already applied by hand, at the same four locations -- nothing else.
5. **Second rename commit `bc888e84`**: became genuinely empty as a direct
   consequence of step 4 (every file it would have touched already matched
   its target content) except for one final residual table-row conflict
   (this package's own already-correct CIU-81 row vs. the commit's own
   now-redundant attempt to delete/recreate it) -- resolved by keeping the
   already-correct state. Reflog confirms git auto-dropped the remainder of
   this commit as empty after that (`rebase (finish)` follows directly after
   the conflict-resolution `rebase (continue)` for this step, with no
   separate pick step logged) -- the branch's final commit is `a7388015`
   (the renamed `a70871af`), one fewer real commit than before the rebase,
   which is correct: two renames collapsed into one clean, fully-resolved
   rename once both collisions were known at the same time.

**Post-rebase verification:**
- `git merge-base --is-ancestor 1f47601c main` confirms the rebase target
  (`main` as of this rebase) is still a real ancestor of whatever `main` has
  become since (it kept moving during this session -- confirmed 7 more
  commits landed on `main` after this rebase, none touching
  `config_model.py`/`composefile.py`/`scaffold.py`; not re-chased, matching
  the coordinator's own framing that one fresh rebase per review round is
  the expectation, not a moving-target chase).
- `grep -rln "CIU-79\|CIU-80\|CIU-81" ciu/` lists this package's 4 files
  (`KNOWN_ISSUES_TODO_BACKLOG.md`, `scaffold.py`, this LOG/REPORT) alongside
  `main`'s own unrelated hits (`CHANGES.md`, `deploy.py`, `engine.py`,
  `ciu-P37-LOG/REPORT.md`, `ciu-P41-LOG/REPORT.md`) -- all of the latter are
  `main`'s own legitimate content, untouched by this package.
- Targeted check confirms this package's own scaffold.py backlog row exists
  exactly ONCE, correctly labeled `CIU-81` (a small script located the row by
  its distinctive body text rather than by ID, to rule out a stray duplicate
  under a stale ID).
- `grep -rln "CIU-79\|CIU-80\|CIU-81" ciu/tests/` returns nothing -- no test
  asserts on any of the three literal strings.
- Local full suite post-rebase (`PYTHONPATH=src python3 -m pytest tests -q
  --cov=ciu --cov-branch`): **3360 passed**, `TOTAL` coverage 100%
  line+branch (module/test counts are higher than earlier rounds simply
  because `main`'s own 35 new commits, mostly `ciu-P39`'s CIU-63 fix and
  `ciu-P40`/`P41`'s work, add their own tests/coverage -- none of it
  overlaps this package's own files).

**Final real gate, post-rebase, at commit `a7388015`:**

```
assay-2.3.0.pyz: OK
ciu: PASS (exit 0)
  commit: a73880151f221c5a437b66cbeb6e1fd514b6ae0b
  argv: /opt/tester-venv/bin/python run-ciu-tests.py
run-gate: verdict artifact: /workspaces/vbpub/.worktrees/ciu-P38-strict-undefined/ciu/.assay/verdict-ciu.json
run-gate: lane 'ciu' exit 0
GATE_EXIT=0
```

Verdict artifact: `outcome: PASS`, `exit_code: 0`, R0 PASS, R1 PASS at
100.0% (`considered: 3, covered: 6`), judged against base `44581f72` (the
merge-base with `main` at gate-run time, confirming R1 judged this
package's actual diff, not a stale one).

**Final commit: `a7388015`.**
