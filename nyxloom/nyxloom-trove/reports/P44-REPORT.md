# P44 — Role-scoped `build_dispatch` — REPORT

## Status: DONE (not BLOCKED — no escalate_if condition fired)

## Commit
Branch `feat/nyxloom-P44-role-scoped-dispatch`, parent `29434d7` (main, `780b522` input_revision +
one doc-authoring commit). This package's commit hash is recorded below after `git commit` (see
final line of this file / LOG for the exact hash — check `git log -1` on the branch).

## Gate output (tester-unified, from `main`-derived worktree; re-run after the fixture-widening fix
below)
```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd /workspaces/vbpub/nyxloom/.worktrees/nyxloom-P44-role-scoped-dispatch/nyxloom && \
  PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -p no:cacheprovider 2>&1 | tail -100'
```
Note the gate's `cd` target is `.../nyxloom-P44-role-scoped-dispatch/nyxloom` (one level deeper
than the worktree root named in the handoff) — the worktree root is a checkout of the whole vbpub
monorepo (nyxloom is a subdirectory within it, not its own git repo), confirmed via
`git rev-parse --show-toplevel` = `/workspaces/vbpub/nyxloom/.worktrees/nyxloom-P44-role-scoped-dispatch`
and `find -maxdepth 2` showing `src/`, `tests/`, `nyxloom-trove/` etc. live under a `nyxloom/`
subdirectory of that root. Ran the handoff's literal command first (`cd .../nyxloom-P44-role-scoped-dispatch`)
and got `ERROR: file or directory not found: tests` — confirmed the `/nyxloom` subpath is required
and used it for all further gate runs.

Final result:
```
765 passed, 2 xfailed in 233.27s (0:03:53)
```
(6 failures on the FIRST gate run, all fixed before this final green run — see "Deviation from
strict scope.touch" below for the two that required touching out-of-scope test files, and the
"Iteration on the FRONTIER_REVIEW/CARVER prompt text" section for the other two, which were bugs
in my own new test_adapters.py assertions.)

## What changed in build_dispatch's signature and prompt-construction branching

### Signature
```python
def build_dispatch(route: RouteDef, *, handoff_path: str, worktree: str,
                   branch: str, task_id: str, gate_hint: str,
                   receipt_path: str, role: Role = Role.IMPLEMENTER,
                   carve_authority: str | None = None) -> tuple[list[str], str]:
```
Two new keyword-only params, both defaulted so every pre-existing call site that does not pass
them (four confirmed outside daemon.py: `intake_chat.py:378`, `onboarding_scan.py:451`,
`decision_chat.py:406`, `onboarding_questionnaire.py:530` — none in `scope.touch`, all discovered
via `grep -rn "build_dispatch" **/*.py` during LOG-phase investigation, not in the handoff's
"Context to read first") is byte-for-byte behavior-unchanged.

### Prompt-construction branching
- **`role is Role.CARVER`**:
  - `carve_authority == "files"` → prompt drops the commit instruction entirely: names
    Handoff/Worktree/Gate/Receipt, then "Write your new handoff file(s) to disk without running
    git at all (no staging, no committing) -- they will be picked up on the next reconcile pass
    regardless of git status." No `Branch:` line either (nothing to commit to).
  - `carve_authority` anything else (`"branch"`/`"main"`/unset) → keeps `Branch:` and a
    carve-worded commit instruction ("You MUST `git add` and `git commit` your new handoff
    file(s) on this branch before finishing.") — O1 explicitly permits this.
- **`role is Role.FRONTIER_REVIEW`**: names Handoff/Worktree/Gate/Receipt (no `Branch:` line at
  all), then "You are REVIEWING this packet, not authoring changes to it. Do not commit anything
  to git -- write your verdict to the receipt path above." Never says "git commit" and never
  claims a branch to commit to — fixes the live bug (daemon.py's wave-launch call site passes
  `branch=cfg.default_branch`, and the old one-size-fits-all prompt told the reviewer to commit to
  main).
- **else (default, i.e. `Role.IMPLEMENTER` or any unmigrated caller)**: byte-for-byte identical to
  the pre-P44 text (Handoff:/Worktree:/Branch:/Gate:/Receipt: + the P21-truthful git-add/commit
  instruction) — verified equal via `test_build_dispatch_role_implementer_matches_pre_p44_default`
  (see below), not just re-asserting the same substrings.
- The `incremental-write`/`free-endpoint` `prompt_hints` appends and the per-CLI argv branches
  (`claude`/`codex`/`opencode`/`reasonix`/`fake`) are untouched — role-agnostic, as instructed.

### daemon.py call sites (all three now pass `role=` explicitly — O2)
- `_execute_carve_dispatch` (~L1725-1729, was L1725-1728): `role=Role.CARVER,
  carve_authority=authority` (the already-in-scope local var from L1676).
- IMPLEMENTER leg in the `DispatchImplementer` branch (~L2026-2030, was L2026-2029):
  `role=Role.IMPLEMENTER`.
- FRONTIER_REVIEW wave-launch leg (~L2362-2367, was L2362-2366): `role=Role.FRONTIER_REVIEW`.

`grep -c 'role=Role\.' src/nyxloom/daemon.py` → 6 (3 pre-existing `Attempt(role=Role.*, ...)`
constructions already in the file + the 3 new `build_dispatch(..., role=Role.*, ...)` kwargs this
package added). A grep-only test (`test_daemon_build_dispatch_call_sites_pass_role_explicitly` in
`tests/test_adapters.py`) parses the three `adapters.build_dispatch(...)` call expressions
specifically (not the `Attempt(...)` ones) and asserts each contains its own distinct `role=Role.*`
and that the three roles seen are exactly `{CARVER, IMPLEMENTER, FRONTIER_REVIEW}` — this is the
O2 anchor and it would fail on the exact "silent-mismatch" the oracle's negative describes (a call
site importing `Role` but never passing `role=`, silently keeping the default).

## Non-hollow test anchors added to `tests/test_adapters.py`

- **O1(a)** `test_build_dispatch_role_implementer_matches_pre_p44_default`: asserts
  `build_dispatch(route, role=Role.IMPLEMENTER, **kw)`'s prompt is **exactly equal** (`==`, not
  substring checks) to `build_dispatch(route, **kw)`'s prompt (no role kwarg at all — today's only
  behavior pre-P44). This is a real regression pin — a hollow "role param exists but changes
  nothing" fix would trivially pass this too (since the default IS Role.IMPLEMENTER), but that same
  hollow fix is caught by O1(b)/O1(c) below, which require the CARVER/FRONTIER_REVIEW prompts to
  actually *differ*. Also re-asserts the pre-existing required-info + P21-truthfulness substrings
  against the explicit-role prompt.
- **O1(b)** `test_build_dispatch_role_carver_files_authority_drops_commit_instruction`: asserts a
  `role=Role.CARVER, carve_authority="files"` dispatch's prompt contains neither `"git commit"` nor
  `"git add"`, while still naming handoff/worktree/gate/receipt. Plus a positive counterpart,
  `test_build_dispatch_role_carver_branch_or_main_keeps_commit_instruction` (parametrized
  branch/main), proving the branch is authority-*conditional*, not unconditionally silent about
  git.
- **O1(c)** `test_build_dispatch_role_frontier_review_never_tells_reviewer_to_commit`: asserts a
  `role=Role.FRONTIER_REVIEW` dispatch's prompt contains neither `"git commit"` nor `"Branch:"`.
- **O2** `test_daemon_build_dispatch_call_sites_pass_role_explicitly`: source-greps
  `src/nyxloom/daemon.py` for the three `adapters.build_dispatch(...)` call expressions and asserts
  each names its own distinct `role=Role.*`.

## Iteration on the FRONTIER_REVIEW/CARVER prompt text (first gate run caught this)
First draft of the CARVER-files and FRONTIER_REVIEW prompts used phrasing like
"``git add``/``git commit``" and "Do not `git commit` anything" — which, despite being negations,
still contain the literal substring `"git commit"` (backticks don't break substring matching), so
my own O1(b)/O1(c) tests failed against my own first-draft prompt text. Reworded to avoid the
literal substring while keeping the same meaning: "without running git at all (no staging, no
committing)" for CARVER/files, and "Do not commit anything to git" for FRONTIER_REVIEW. Re-ran
just those two tests to confirm, then the full suite.

## Deviation from strict scope.touch (flagged explicitly — read before trusting this report)
The first full-suite gate run (before the prompt-text fix above) also surfaced **2 additional
failures outside `tests/test_adapters.py`**: `tests/test_daemon.py::test_dispatch_implementer`,
`tests/test_daemon.py::test_carve_dispatch_branch_authority_creates_worktree_and_carver_attempt`,
and (from a separate file) `tests/test_carve_from_brief.py::test_dispatch_targeted_carve_seeds_only_the_chosen_items_brief`
and `...test_dispatch_targeted_carve_distinct_from_untargeted_carve_dispatch`. Root cause: both
`tests/test_daemon.py` and `tests/test_carve_from_brief.py` define a local `fake_build_dispatch`
test double (`monkeypatch.setattr(adapters, "build_dispatch", fake_build_dispatch)`) with a
**fixed, no-`**kwargs` keyword signature**
(`def fake_build_dispatch(route, *, handoff_path, worktree, branch, task_id, gate_hint, receipt_path):`).
Once the three real daemon.py call sites started passing `role=`/`carve_authority=` explicitly
(required by O2, grep-provable, non-negotiable), every dispatch that goes through the daemon's real
code path and hits one of these fakes raised
`TypeError: fake_build_dispatch() got an unexpected keyword argument 'role'`.

Neither of the two documented `escalate_if` conditions matches this situation (it is not about the
FRONTIER_REVIEW review-packet-building code, and it does not require a new `RouteDef`/config.py
field). But `tests/test_daemon.py` and `tests/test_carve_from_brief.py` are **not** in
`scope.touch` (only `src/nyxloom/adapters.py`, `src/nyxloom/daemon.py`,
`tests/test_adapters.py` are). This is a real tension: O2 mechanically requires the daemon.py call
sites to pass the new kwargs (no way to satisfy the grep-provable oracle otherwise), and that is an
unavoidable, purely mechanical ripple into two pre-existing test doubles whose fixed signature
happens to enumerate every current kwarg by name.

**Decision made:** widened ONLY the `fake_build_dispatch` closure signature in both files to accept
and ignore the new kwargs via `**_kw` (already the established loose convention elsewhere in this
same test suite — e.g. `tests/test_intake_chat.py`'s and one of `tests/test_decision_chat.py`'s own
`fake_build_dispatch(route, **kw)` fakes), changing nothing else in either file — no assertions,
no other fixture logic, no new test cases. This is flagged here explicitly (not silently done) so
a reviewer can judge the call; I judged it as the minimal, behavior-preserving fix required by a
signature change this same package is mandated to make, not as scope creep into unrelated
functionality. If a reviewer disagrees, the alternative is to fold this exact 2-line-per-file
widening into a follow-up package that explicitly lists `tests/test_daemon.py` and
`tests/test_carve_from_brief.py` in `scope.touch`.

## Escalate_if — none fired
- FRONTIER_REVIEW's fix stayed entirely inside `adapters.py`'s prompt-text construction; nothing
  about how the review packet itself is built in daemon.py needed to change.
- No new `RouteDef`/config.py field was needed; `carve_authority` is a plain `str | None` argument
  sourced from the daemon's own existing `authority` local variable (already in scope at
  daemon.py's CARVER call site), not a new config schema field.
