# ciu-P28 — HOTFIX: `worktree branches -y` unsafe prune — implementation LOG

- **Package:** `ciu-P28-hotfix-worktree-branches-prune-safety`
- **Branch:** `feat/ciu-qol-v8prep-wave` (worktree
  `/workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu`)
- **Input revision at start:** `98340362` (confirmed via `git log -1` before
  any edit; matches the dispatch brief)
- **Date:** 2026-08-25
- **Gate:** `tester-unified` — `.venv/bin/python run-ciu-tests.py` — **PASS**,
  2618 passed, 100.00% line+branch, exit 0 (verbatim output below)
- **BLOCKED:** none. No forbidden file was touched; `escalate_if`'s
  primary-worktree-threading clause did not trigger (see O2).

---

## 0. Method: every defect reproduced on the OLD code first

Nothing here was fixed from the handoff's description alone. Each of the four
defects was first reproduced against the **unmodified** released code, then
re-run against the **fixed** code with the *identical* script/test, so the
before/after is a controlled comparison rather than two different experiments.

Two artefacts back this up:

1. **The eight new tests were written and run BEFORE any source edit.** All
   eight failed against the released code:

   ```
   FAILED ... ::test_managed_instance_branch_is_never_pruned_without_ciu_clean
   FAILED ... ::test_managed_instance_outranks_prunable_in_every_lifecycle_state
   FAILED ... ::test_managed_instance_category_is_reported_by_the_cli
   FAILED ... ::test_prune_from_linked_worktree_judges_mergedness_against_primary_head
   FAILED ... ::test_candidate_not_contained_in_primary_head_fails_before_destruction
   FAILED ... ::test_prune_from_a_checkout_whose_own_branch_is_prunable_does_not_self_destruct
   FAILED ... ::test_unexpected_git_failure_on_one_branch_never_aborts_the_prune
   FAILED ... ::test_cli_json_prune_exits_nonzero_on_partial_real_subprocess
   8 failed, 3 passed, 27 deselected in 2.45s
   ```

2. **A standalone probe script** (real scratch repos in `tempfile`, real
   `git worktree add`, no mocking of anything) run twice — once with the
   released `src/ciu`, once with the fixed one. Because the destructive
   defects are about *what is left on disk*, the probe asserts on the
   filesystem, not on the document. Both runs are pasted verbatim in §2.

The probe lives at
`<scratchpad>/probe_old.py` (a throwaway, deliberately NOT committed — its
scenarios are all reproduced as committed tests; it exists only to produce the
side-by-side evidence below).

---

## 1. The O1 design choice, and why

The handoff offered two bounded options for O1 and asked for a deliberate
choice:

- **(a)** exclude a branch whose checkout carries a managed instance record
  from `prunable` entirely, into its own closed category never acted on by
  `-y`; or
- **(b)** route it through the existing `remove()` (clean-then-remove, the
  `ciu worktree rm` path).

### Chosen: **(a)** — a new closed category `managed-instance`, never acted on by `-y`.

Reasoning, in the order it actually decided the call:

1. **It makes O1's negative unreachable by construction, not by care.** The
   negative is "*any* code path where a branch with a non-null `ciu_instance`
   record reaches a bare `git worktree remove`". `prune_branches` removes
   exactly the list `[b for b in branches if b["category"] == "prunable"]`.
   Under (a), a record-carrying branch can never be in that list, so there is
   no ordering, no early-return and no future refactor of the loop body that
   can re-open the hole. Under (b) the safety would live *inside* the loop, in
   a conditional that a later edit could invert — the same species of "one
   branch of a two-branch decision drifted" bug as O4 itself.

2. **(b) would import Docker-scale failure into a git-scale command.**
   `remove()` shells out to `python -m ciu.cli clean` per instance. `ciu
   worktree branches -y` is the GIT half of CIU-25; its contract is "delete
   fully-merged branches". Silently escalating that into an N-instance
   container/volume/network teardown — long-running, daemon-dependent, and
   failing for reasons that have nothing to do with branch hygiene — changes
   what the operator consented to when they typed `-y`. The estate rule for
   this command is already "removal only on proof, survey otherwise"; a
   managed instance is precisely the case where the proof is *not* in git.

3. **(b) fights the fix I was making for O3.** `remove()` signals failure by
   *raising* `WorktreeError` (that raise is load-bearing — it is what stops a
   failed clean from being followed by a checkout removal). O3's whole point
   is that a raise inside the per-branch loop is what aborted the run. I would
   have had to wrap `remove()` in a `try`/`except` and then decide a `force`
   policy per branch — reintroducing the exact hazard class under a new name.

4. **(b) needs a logical name the prune does not have.** `remove()` is keyed
   by logical name / record lookup and re-derives the worktree; the prune has
   branch names. The plumbing is doable but it is new coupling between the git
   survey and the instance lifecycle, on a hotfix, with no test debt paid.

5. **The automation value (b) preserves is small and already served.** The
   operator who wants managed instances gone runs `ciu worktree rm NAME`,
   which already does exactly the right thing. So (a) costs one extra command
   in a rare case; (b) costs a silent teardown in a common one.

**Third-option check** (per the first `escalate_if`): I considered a
`--include-managed` opt-in flag that would route through `remove()` on demand
— i.e. (a) by default with (b) available. I rejected it *for this package*:
it is a new CLI surface with its own consent semantics on a hotfix whose job
is to stop data loss in released code, and it would have to be specified
(what does it do when `ciu clean` fails? `--force`?) rather than bolted on.
It is a clean follow-up if anyone ever asks for it; nothing in (a) forecloses
it. Documented here rather than escalated, as the handoff directs.

**Consequence, stated plainly:** `managed-instance` outranks `prunable`,
`merged-dirty` *and* `unmerged` in the classification chain, and applies at
**any** lifecycle state (`ready`, `allocating`, `recovery-required`). The
handoff is explicit that "the record having been loaded is the point", and an
`allocating`/`recovery-required` instance is if anything *more* fragile than a
ready one. The branch's attributes (`ahead`/`behind`/`merged`/`dirty`/
`ciu_instance`) are all still on the row, so nothing is hidden from a human —
only the destructive action is withheld. The survey's `hint` now names the
disposal command.

### Two smaller design calls made along the way

- **`schema_version` 1 → 2.** Adding a member to a *closed* vocabulary is a
  breaking change for a consumer that switches exhaustively — and CIU's own
  doctrine already says so: S17.3's provenance verdicts bumped to
  `schema_version: 2` for exactly this reason ("The widened closed
  vocabularies make every verdict `schema_version: 2`. Strict consumers refuse
  unknown members (fail-closed)"). Not bumping would hand a strict consumer an
  unknown `category` under a version that promised it could not happen. The
  capability id stays `worktree.branches.v1` — the *feature* did not change,
  the document grammar did, and those are separately versioned by S16.5.

- **O3 via "classify as `current`", not "refuse upfront".** The handoff allows
  either. Classifying is strictly better here: refusing upfront would mean an
  operator standing in a merged-branch checkout gets *nothing* pruned, which
  makes the safe fix feel like a regression and invites `-y` from somewhere
  else. Classifying means the other candidates are still pruned correctly and
  the only branch skipped is the one the operator is literally standing on —
  and it is reported, by name, in a category that already means "somebody's
  working context". It is also the same guard the primary checkout has always
  had, extended to the checkout that is equally live.

---

## 2. Before/after evidence — the identical probe, both codebases

Same script, same scenarios, only `src/ciu` differs.

### OLD (released) code

```
===== O1: managed instance destroyed by a BARE `git worktree remove` =====
  survey category      : prunable
  ciu_instance record  : {'logical_name': 'managed', 'state': 'ready'}
  prune removed        : ['feat/managed']
  checkout still there : False
  ciu.env still there  : False
  record still there   : False

===== O2: mergedness judged against the INVOKING linked worktree's HEAD =====
  status               : partial
  removed              : []
  FAILED               : fix/merged-a -> error: the branch 'fix/merged-a' is not fully merged
hint: If you are sure you want to delete it, run 'git branch -D fix/merged-a'
hint: Disable this message with "git config set advice.forceDeleteBranch false"
  FAILED               : fix/merged-b -> error: the branch 'fix/merged-b' is not fully merged
hint: If you are sure you want to delete it, run 'git branch -D fix/merged-b'
hint: Disable this message with "git config set advice.forceDeleteBranch false"
  fix/merged-a branch still exists : True
  ...but its checkout wt-a survives: False

===== O3: self-destruct mid-prune from a checkout whose own branch is prunable =====
  fix/aaa-self category (from itself): prunable
  RAISED               : WorktreeError -> [S16] could not run git: [Errno 2] No such file or directory: '/tmp/tmpb4p4qqh6/wt-self'
  document returned    : NONE
  invoking checkout survives     : False
  fix/zzz-other still un-pruned  : True
```

That is a verbatim match for what the reviews reported:

- **O1** — a live managed instance removed with no `ciu clean`. Note what is
  gone: the checkout, `ciu.env`, *and* `ciu.worktree-instance.json`. Nothing
  is left that could tell CIU what to clean, which is why the containers /
  volumes / networks are orphaned and the root-owned `vol-*` dirs are
  stranded.
- **O2** — `removed: []` with both branches falsely reported "not fully
  merged", **and `wt-a` destroyed anyway** (`survives: False`). Silent wrong
  report *plus* data loss, in one run.
- **O3** — unhandled `WorktreeError` mid-loop, **no document returned at all**,
  the operator's own working directory gone, and `fix/zzz-other` (which sorts
  after `fix/aaa-self`) never processed.

### NEW (fixed) code — same script

```
===== O1: managed instance destroyed by a BARE `git worktree remove` =====
  survey category      : managed-instance
  ciu_instance record  : {'logical_name': 'managed', 'state': 'ready'}
  prune removed        : []
  checkout still there : True
  ciu.env still there  : True
  record still there   : True

===== O2: mergedness judged against the INVOKING linked worktree's HEAD =====
  status               : pruned
  removed              : ['fix/merged-a', 'fix/merged-b']
  fix/merged-a branch still exists : False
  ...but its checkout wt-a survives: False

===== O3: self-destruct mid-prune from a checkout whose own branch is prunable =====
  fix/aaa-self category (from itself): current
  returned document    : pruned ['fix/zzz-other']
  invoking checkout survives     : True
  fix/zzz-other still un-pruned  : False
```

Reading O2's "after": both genuinely-merged branches are now correctly pruned
(`status: pruned`, `removed` has both, the refs are gone) — `wt-a` not
surviving is now the *correct* outcome, because `fix/merged-a` really was a
merged, clean, unmanaged candidate and its branch was deleted along with it.
The invoking checkout `wt-from` and its unmerged branch `feat/behind` are
untouched (asserted in the committed test, which checks both).

Reading O3's "after": a document *is* returned, `fix/zzz-other` (the branch
that sorts after the self-referential one) *was* processed, and the operator's
cwd and branch survive.

---

## 3. Oracle-by-oracle evidence table

| Oracle | What was changed | Where | Proof it holds | Proof it refuses in the dangerous case |
|---|---|---|---|---|
| **O1** no-clean-before-remove | Approach **(a)**: new closed category `managed-instance` — any branch whose checkout carries a loaded `ciu_instance` record, at any lifecycle state — placed ABOVE `prunable`/`merged-dirty`/`unmerged` in the chain. `prune_branches` only ever iterates `category == "prunable"`, so a record-carrying branch cannot reach `git worktree remove`. Survey `hint` names `ciu worktree rm NAME`. | `worktree.py` `BRANCH_CATEGORIES` + `branch_hygiene` classification chain + hint; `cli.py` headline counts | `test_managed_instance_branch_is_never_pruned_without_ciu_clean` (merged + CLEAN + record → category `managed-instance`, `removed == []`, checkout **and** record file **and** the branch all still present, `find_instance_record` still resolves); `test_managed_instance_category_is_reported_by_the_cli` | Probe §2: OLD `removed: ['feat/managed']`, checkout/`ciu.env`/record all `False` → NEW `removed: []`, all three `True`. The test asserts `entry["merged"] is True and entry["dirty"] is False` first, so it proves the branch *would* have been `prunable` — the refusal is the record, not incidental dirt. `test_managed_instance_outranks_prunable_in_every_lifecycle_state` covers `state: allocating`. |
| **O2** correct-HEAD-for-mergedness | Every destructive git command of the `-y` pass now runs with `cwd = primary_worktree_root(repo_root)` instead of the invoking root. Plus a THIRD read-only pre-check, `_prune_candidate_refusal`, which refuses — *before* `git worktree remove* — a candidate with no upstream that is not an ancestor of the primary's `HEAD`. `_prune_base_sanity` already anchored on the primary/origin-HEAD and is unchanged. | `worktree.py` `prune_branches` (`git_root`), new `_prune_candidate_refusal` | `test_prune_from_linked_worktree_judges_mergedness_against_primary_head` — the review's exact scenario: primary on `main`, `fix/merged-a`+`fix/merged-b` merged into `main` (one with its own clean linked checkout), invoked from a linked worktree on `feat/behind` which forked *before* both merges. The test first asserts the premise (`merge-base --is-ancestor fix/merged-a feat/behind` **fails**), then asserts `status == "pruned"`, `removed == [both]`, `failed == []`, both refs gone, and the invoking checkout + `feat/behind` untouched. | Probe §2 O2: OLD `removed: []` + "not fully merged" on both + `wt-a` destroyed → NEW both pruned, `failed: []`. And the *other* direction is covered too: `test_candidate_not_contained_in_primary_head_fails_before_destruction` builds the residual hole (base sanity passing via `origin/HEAD` alone while the primary HEAD does not contain the base) and asserts `status == "partial"`, reason contains `PRIMARY checkout's HEAD`, `removed == []`, **and the checkout still exists** — the refusal happens before any destruction. This is the oracle's negative explicitly: the fix is not limited to the invoking worktree's own branch, it corrects the comparison for OTHER branches. |
| **O3** no-self-destruct-mid-prune | Two independent guarantees. (i) `branch_hygiene` now guards the INVOKING checkout's branch as `current` (`guarded_paths = {git_toplevel(repo_root)} ∪ {primary paths}`), so it is never a candidate in its own run regardless of which worktree is primary. (ii) The per-branch loop body is wrapped: an unexpected `WorktreeError` becomes that branch's NAMED `failed` reason and the loop continues — nothing escapes, a document is always returned. | `worktree.py` `branch_hygiene` `guarded_paths`; `prune_branches` per-branch `try/except WorktreeError` | `test_prune_from_a_checkout_whose_own_branch_is_prunable_does_not_self_destruct` — `fix/aaa-self` (checked out at the invoking worktree) sorts BEFORE `fix/zzz-other`, exactly as `review_focus` asks. Asserts: survey classifies `fix/aaa-self` as `current` and `fix/zzz-other` as `prunable`; the prune returns a document; `removed == ["fix/zzz-other"]`; the invoking dir and its branch survive; and — the completeness check — from the PRIMARY, `fix/aaa-self` is an ordinary `prunable` candidate again, i.e. it was skipped for *this run*, not permanently hidden. | Probe §2 O3: OLD raised `WorktreeError`, `document returned: NONE`, cwd gone, `fix/zzz-other` un-pruned → NEW document returned, later branch processed, cwd survives. The oracle's negative ("an exception escaping under any input") is covered generally by `test_unexpected_git_failure_on_one_branch_never_aborts_the_prune`, which injects a raise on `branch -d` for the alphabetically-first candidate and asserts the LATER one is still `removed` while the raiser becomes a named `failed` entry. *Scope note:* the two documented UPFRONT refusals (`_resolve_base_branch`, `_prune_base_sanity`) still raise by design — they run before any mutation and are the contract S16.8 already specifies and tests. |
| **O4** json-exit-code | The `status == "partial" → 1` decision is **hoisted** to a single `code = 1 if doc.get("status") == "partial" else 0` above the `if json:` / `else:` split; both arms `return code`. Not duplicated into both arms — the oracle's negative calls that out as the same species as the bug. | `cli.py` `worktree branches` dispatch | `test_cli_json_prune_and_survey_exit_zero_when_not_partial_real_subprocess`: `--json` survey → exit 0, `--json -y` clean prune → exit 0. | `test_cli_json_prune_exits_nonzero_on_partial_real_subprocess`: a genuine partial (upstream-blocked candidate, no mocking) run as a **real `python -m ciu` subprocess**, asserting `res.returncode == 1` — the process exit code, per the oracle's negative, not the JSON body. It also parses the body to confirm `status == "partial"` and the `failed` entry. Old code returned **0** here (captured above). The pre-existing human-path test `test_cli_prune_surfaces_removed_failed_and_exits_nonzero_on_partial` was re-run **explicitly and on its own** per `review_focus` — passes. |
| **O5** tests | 8 new tests, all end-to-end on a real scratch git repo with real `git worktree add`, reusing the existing `repo` fixture and `_branch`/`_merge_into_main` helpers (no new fixture invented). Two of the eight are real subprocesses. Nothing mocks the comparison logic under test — the only `monkeypatch` is `test_unexpected_git_failure_...`, which deliberately *injects* a raise to prove the loop's no-escape property, and it delegates every other call to the real `_git`. | `tests/tests/test_ciu_worktree_branches.py` | 38 tests in that file pass; whole gate 2618 pass at 100% line+branch. | All 8 failed against the released code before any source edit (§0). |
| **O6** docs | `SPEC.md` S16.8: six-value → **seven**-value vocabulary; `current` redefined to include the invoking checkout; `managed-instance` specified with its rationale; the "gated TWICE" paragraph corrected to **THREE** gates and given the primary-worktree rule, the no-escape loop rule, and the shared exit-code rule; `schema_version: 1` → `2` with the reason. `CONSUMERS.md` §5b: the false **"gated twice so it can never half-prune"** claim is REPLACED with the guarantee that is actually true ("a candidate's checkout is never destroyed by a refusal this command could have foreseen"), explicitly conceding that Git can still refuse for a reason only Git knows, in which case that branch becomes a `FAILED` line and the prune continues; worked example updated with a `managed-instance:` block and the new headline; exit-code claim now says "in **both** `--json` and human output". `CHANGES.md`: `### Fixed` entry under `[Unreleased]` naming all four defects as a hotfix for released behaviour. `KNOWN_ISSUES_TODO_BACKLOG.md`: CIU-25's status row + detail section get a HOTFIX note pointing at this LOG. `cli.py`'s own `--help` text ("never age-based, never the mainline or the primary checkout's branch") was also corrected — it had drifted into the same overclaim. | `docs/SPEC.md`, `docs/CONSUMERS.md`, `CHANGES.md`, `KNOWN_ISSUES_TODO_BACKLOG.md`, `src/ciu/cli.py` | `test_ciu_documentation_contract.py` passes (every TOML example still parses, every closed public value still documented, every anchor still resolves). | `grep -rn "can never half-prune" docs/ src/ README.md` → no hits; the phrase "half-prune" now survives only where it names the *failure mode being guarded against* (SPEC's "gated THREE times against the half-prune failure", and two `worktree.py` docstrings), never as a claimed guarantee. |

**Affected versions.** O6 asks for the affected versions "if you can determine
them from CHANGES.md's own history". The retrospective reviews reported
v6.3.0/v6.4.0. CHANGES.md's own history does **not** support that: `ciu
worktree branches` first appears under **`## [7.0.0] - 2026-08-23`** as
`feat(ciu): worktree branch hygiene — grounded survey + prune of merged
branches (CIU-25 git half, SPEC S16.8) (c92377fb)`, and the v6.3.0/v6.4.0
sections contain no `branches` entry at all. The CHANGES.md entry therefore
names **v7.0.0 and every 7.x since**, and explicitly notes the discrepancy
with the reviews' attribution rather than silently overriding it.

---

## 4. Files changed

| File | Change |
|---|---|
| `src/ciu/worktree.py` | `BRANCHES_SCHEMA_VERSION` 1→2; `BRANCH_CATEGORIES` gains `managed-instance` (+ rewritten vocabulary comment); `branch_hygiene` gains `guarded_paths` (primary ∪ invoking) and the `managed-instance` arm, and the hint names `ciu worktree rm`; new `_prune_candidate_refusal` (upstream pre-check, moved out of the loop, + new primary-HEAD pre-check); `prune_branches` runs its destructive pass from `primary_worktree_root(...)` and wraps each candidate so no failure escapes the loop |
| `src/ciu/cli.py` | exit-code decision hoisted above the output-format branch; `managed-instance` in the headline counts; `--help` / usage text corrected |
| `tests/tests/test_ciu_worktree_branches.py` | 8 new end-to-end regressions (§3) + `_write_instance_record` / `_run_ciu` helpers; two pre-existing `schema_version == 1` assertions updated to `2` |
| `docs/SPEC.md` | S16.8 rewritten per O6 |
| `docs/CONSUMERS.md` | §5b rewritten per O6, false claim removed |
| `CHANGES.md` | `[Unreleased] → Fixed` hotfix entry |
| `KNOWN_ISSUES_TODO_BACKLOG.md` | CIU-25 status row + detail HOTFIX note |
| `nyxloom-trove/reports/ciu-P28-...-LOG.md` | this file |

**`scope.forbid` check:** `git diff --stat` lists exactly the `scope.touch`
files. `src/ciu/deploy.py`, `src/ciu/engine.py`, `src/ciu/config_model.py`,
`nyxloom-trove/backlog.md`, `nyxloom-trove/decisions.md` and
`nyxloom-trove/roadmap.md` are untouched.

**Blast radius:** none outside scope. No out-of-scope test needed changing —
the two assertion updates are both inside `test_ciu_worktree_branches.py`
(in scope), and the whole 2618-test suite is green at 100% with no other
edits. Notably `test_ciu_documentation_contract.py` (not in scope) still
passes unmodified: `managed-instance` is a new closed value, but that test
only requires the values in its own list to be documented, and every value on
that list still is.

---

## 5. Gate output (verbatim)

`.venv/bin/python run-ciu-tests.py`, exit code 0:

```
============================= test session starts ==============================
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0
rootdir: /workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu
configfile: pyproject.toml
plugins: xdist-3.8.0, cov-7.1.0
created: 8/8 workers
8 workers [2618 items]

...

_______________ coverage: platform linux, python 3.14.6-final-0 ________________

Name                                             Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------------------------------
src/ciu/__init__.py                                  3      0      0      0   100%
src/ciu/__main__.py                                  3      0      2      0   100%
src/ciu/_version.py                                 11      0      0      0   100%
src/ciu/activate.py                                119      0     46      0   100%
src/ciu/cli.py                                     729      0    260      0   100%
src/ciu/cli_utils.py                                11      0      0      0   100%
src/ciu/composefile.py                             388      0    180      0   100%
src/ciu/config_constants.py                         29      0      4      0   100%
src/ciu/config_model.py                            276      0    128      0   100%
src/ciu/deploy.py                                 1582      0    686      0   100%
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
src/ciu/hooks_runner.py                            139      0     56      0   100%
src/ciu/hosts.py                                    61      0     28      0   100%
src/ciu/ksm.py                                     180      0     64      0   100%
src/ciu/output.py                                   89      0     34      0   100%
src/ciu/paths.py                                    30      0     12      0   100%
src/ciu/procutil.py                                 17      0      2      0   100%
src/ciu/provisioning.py                            359      0    154      0   100%
src/ciu/scaffold.py                                104      0     36      0   100%
src/ciu/secrets/__init__.py                          3      0      0      0   100%
src/ciu/secrets/directives.py                      140      0     78      0   100%
src/ciu/secrets/materialize.py                     229      0     64      0   100%
src/ciu/secrets/providers.py                       111      0     38      0   100%
src/ciu/transport_ssh.py                           219      0     70      0   100%
src/ciu/warn_policy.py                              32      0     14      0   100%
src/ciu/workspace_env.py                           454      0    190      0   100%
src/ciu/worktree.py                               1128      0    438      0   100%
--------------------------------------------------------------------------------------------
TOTAL                                             8544      0   3404      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
============================ 2618 passed in 17.06s =============================
```

(The `...` elides the per-worker progress dots between the header and the
coverage table; every other line is verbatim. `cli.py` and `worktree.py`, the
two source files this package touched, are both at 100% with 0 partial
branches.)

### The `review_focus` runs, separately

Per `review_focus` item 3 ("run it explicitly, don't just trust the full
suite's green") — the pre-existing human-output exit-code test plus the two
new JSON ones, on their own:

```
$ .venv/bin/python -m pytest \
    tests/tests/test_ciu_worktree_branches.py::test_cli_prune_surfaces_removed_failed_and_exits_nonzero_on_partial \
    tests/tests/test_ciu_worktree_branches.py::test_cli_json_prune_exits_nonzero_on_partial_real_subprocess \
    tests/tests/test_ciu_worktree_branches.py::test_cli_json_prune_and_survey_exit_zero_when_not_partial_real_subprocess \
    -p no:randomly --no-header -q
...                                                                      [100%]
3 passed in 1.06s
```

---

## 6. Notes for the reviewer

- The strongest single artefact is §2: one script, two codebases, filesystem
  assertions. If you want to re-run it, the three scenarios are reproduced
  verbatim as committed tests, so `git stash` on `src/ciu` and running the 8
  new tests gives the same before/after.
- The `managed-instance` classification is deliberately *unconditional* on
  lifecycle state and on mergedness. If you think that is too broad — e.g.
  that an `unmerged` managed branch should stay `unmerged` for the signal —
  note that the row still carries `merged`, `ahead`, `behind` and
  `ciu_instance`, so no information is lost, and the narrower rule would make
  the invariant "a record-carrying branch is never in the removed list"
  conditional rather than structural.
- `_prune_base_sanity` was deliberately **not** tightened to require the base
  be contained in the *primary's* HEAD (rather than the primary's HEAD **or**
  the `origin/HEAD` target). Tightening it would have been a behaviour change
  for `test_yes_safety_includes_origin_head_target`'s legitimate case. The
  residual hole that arm leaves — base sanity satisfied by `origin/HEAD` while
  the primary's HEAD does not contain the base, so `branch -d` refuses after
  the checkout is gone — is instead closed by the new per-candidate HEAD
  pre-check, which is fail-closed and runs before any destruction. That is the
  narrower fix and it is directly tested.
- `escalate_if` clause 2 did not fire: the primary-worktree path was already
  available inside `worktree.py` as `primary_worktree_root()` (S16.3), so O2
  required no threading through `deploy.py`, `engine.py` or
  `config_model.py`, and none of them were opened.
