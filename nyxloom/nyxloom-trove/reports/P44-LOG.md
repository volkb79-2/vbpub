# P44 — Role-scoped `build_dispatch` — LOG

## Context read
- `src/nyxloom/adapters.py` L1-238 (module docstring contract + `build_dispatch` itself).
- `src/nyxloom/daemon.py` L170-256 (`_execute_carve_dispatch`/`_build_carve_packet` module
  docstring: the three `carve_authority` modes — `branch`/`main`/`files` — and what each implies
  about committing).
- `src/nyxloom/daemon.py` L1600-1741 (`_build_carve_packet`'s existing per-authority packet text
  at L1619-1636, and `_execute_carve_dispatch` incl. L1676 `authority = getattr(...)` and the
  L1725-1728 CARVER `build_dispatch` call site).
- `src/nyxloom/daemon.py` L2002-2040 (IMPLEMENTER call site, `build_dispatch` at L2026-2029).
- `src/nyxloom/daemon.py` L2330-2377 (FRONTIER_REVIEW wave-launch call site, `build_dispatch` at
  L2362-2366 — confirmed it passes `branch=cfg.default_branch`, i.e. today's literal bug: a
  reviewer told to commit to main).
- `src/nyxloom/types.py` L132-145 — `Role` enum (`IMPLEMENTER`/`SELF_REVIEW`/`FRONTIER_REVIEW`/
  `CARVER`; `SELF_REVIEW` is reserved/not dispatched — no branch needed for it here).
- `tests/test_adapters.py` (full file) — existing IMPLEMENTER-path assertions, in particular
  `test_build_dispatch_prompt_contains_required_info` and
  `test_build_dispatch_prompt_commit_instruction_is_truthful` (the P21 truthfulness pin).

## Extra investigation (not in "Context to read first", needed to avoid breaking out-of-scope callers)
`grep -rn "build_dispatch" **/*.py` turned up FOUR more real call sites beyond the three named in
the handoff, all outside `scope.touch` (so they must NOT be edited): `intake_chat.py:378`,
`onboarding_scan.py:451`, `decision_chat.py:406`, `onboarding_questionnaire.py:530`. None of these
pass a `role=` kwarg today. Read each: all four discard the returned prompt (`_prompt`) and instead
append their own `--append-system-prompt <chat-specific text>` to `argv` — but `build_dispatch`
still embeds the "generic" prompt text inside `argv` itself for CLI shapes that put `prompt` in the
argv list (e.g. `claude`'s `-p prompt`), so a behavior change to the default prompt text WOULD
change these four callers' argv too, and I cannot edit their call sites to compensate (out of
scope/forbidden implicitly by scope.touch not listing them).

**Decision:** `role` must be a keyword-only parameter with a default of `Role.IMPLEMENTER` (not a
required kwarg with no default). This keeps every un-migrated call site byte-for-byte
behavior-identical (satisfies O1's explicit "zero behavior change for the implementer leg" for
IMPLEMENTER, and as a side effect for these four out-of-scope callers too, since they were never
part of this bug — only CARVER/FRONTIER_REVIEW leak the wrong prompt). The three daemon.py call
sites named in the handoff are updated to pass `role=` explicitly per O2; the four others are left
untouched (out of scope) and get the default.

## Signature decided
```python
def build_dispatch(route: RouteDef, *, handoff_path: str, worktree: str,
                   branch: str, task_id: str, gate_hint: str,
                   receipt_path: str, role: Role = Role.IMPLEMENTER,
                   carve_authority: str | None = None) -> tuple[list[str], str]:
```
`carve_authority` is only consulted when `role is Role.CARVER`: `"files"` drops the commit
instruction entirely (no git, matches the daemon.py module docstring's "files" contract);
`"branch"`/`"main"`/anything else keeps a (carver-worded) commit instruction, per O1's explicit
permission ("when authority is 'branch' or 'main', the carve leg's commit instruction is fine to
keep").

## Implementation
- `adapters.py`: imported `Role` from `.types`; branched prompt construction into three shapes
  (IMPLEMENTER/default = today's exact text, CARVER = authority-conditional, FRONTIER_REVIEW = no
  commit/no branch-to-commit-to); left the incremental-write / free-endpoint hint appends and all
  per-CLI argv branches untouched (role-agnostic, as instructed); added a dated docstring addendum
  documenting the new param instead of silently changing a section marked "frozen".
- `daemon.py`: three call sites (`_execute_carve_dispatch` ~L1725, IMPLEMENTER ~L2026,
  FRONTIER_REVIEW wave-launch ~L2362) now pass `role=Role.CARVER`/`role=Role.IMPLEMENTER`/
  `role=Role.FRONTIER_REVIEW` explicitly; the CARVER site also passes
  `carve_authority=authority` (the already-in-scope local variable at L1676).
- `tests/test_adapters.py`: added the three non-hollow anchors (O1a/O1b/O1c) plus one extra
  positive case (CARVER under `branch` authority keeps the commit instruction) and a grep-style
  assertion mirroring O2's `grep -c 'role=Role\.'` check via a source-scan test.

## Blockers
None. No escalate_if condition was hit: FRONTIER_REVIEW's prompt fix stayed entirely inside
`adapters.py`'s prompt text (did not need to touch how the review packet itself is built in
daemon.py), and no new `RouteDef`/config.py field was needed — `carve_authority` is threaded as a
plain `str | None` argument sourced from the daemon's own existing `authority` local, not a new
config schema field.

## Gate iteration (first full-suite run: 6 failed / 759 passed / 2 xfailed)
1. **2 failures in my own new `tests/test_adapters.py` cases** (CARVER-files and FRONTIER_REVIEW
   anchors): my first-draft prompt text used phrasing like "`git add`/`git commit`" and "Do not
   `git commit` anything" -- negations that still CONTAIN the literal substring "git commit"
   (backticks don't break substring matching), so my own oracle assertions
   (`assert "git commit" not in prompt`) failed against my own prompt text. Fixed by rewording to
   "without running git at all (no staging, no committing)" (CARVER/files) and "Do not commit
   anything to git" (FRONTIER_REVIEW) -- same meaning, no literal "git commit"/"git add" substring.
2. **4 failures outside test_adapters.py**: `tests/test_daemon.py::test_dispatch_implementer`,
   `tests/test_daemon.py::test_carve_dispatch_branch_authority_creates_worktree_and_carver_attempt`,
   `tests/test_carve_from_brief.py::test_dispatch_targeted_carve_seeds_only_the_chosen_items_brief`,
   `tests/test_carve_from_brief.py::test_dispatch_targeted_carve_distinct_from_untargeted_carve_dispatch`.
   Root cause: both files define a local `fake_build_dispatch` monkeypatch of `adapters.build_dispatch`
   with a FIXED keyword signature (no `**kwargs`) that doesn't know about the new `role=`/
   `carve_authority=` kwargs the (now-updated) daemon.py call sites pass -- `TypeError: unexpected
   keyword argument 'role'`. Neither file is in `scope.touch`. Judged this an unavoidable mechanical
   ripple of the O2-mandated signature change (not scope creep) and widened ONLY the
   `fake_build_dispatch` closure signature in both files (added `**_kw`, already the convention
   used by other fakes in this same test suite, e.g. test_intake_chat.py's), changing nothing else.
   Full justification + explicit flag for review in P44-REPORT.md's "Deviation from strict
   scope.touch" section.

Final full-suite gate: `765 passed, 2 xfailed in 233.27s`.
