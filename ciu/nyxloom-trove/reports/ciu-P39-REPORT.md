# ciu-P39 — CIU-63: `lint_graph` recognizes `stack:*` refs without a self-declared `provides`

**Status: CIU-63 FIXED, reviewed (ACCEPT-conditional, all three blockers
resolved), final gate PASS.** Final commit: `04097c4c93e0286fbea50d63cd10573b973652b0`.

## What CIU-63 was

`lint_graph` (`src/ciu/provisioning.py`) ran two passes over the
`requires`/`provides` graph. Pass 1 ("every required ref is provided")
treated a `stack:<path>:healthy|completed` ref exactly like every other ref
kind — satisfied only when some stack's `provides` array literally contained
the string. Pass 2 (cycle detection) already knew better: it resolves a
`stack:*` ref's selector to a real declared stack path via
`_resolve_declared_stack_path`, because that is how `_probe_stack` (the live
probe) actually resolves the ref at runtime — never by reading a `provides`
declaration. Consumers were forced to add a redundant self-declaration
(`provides = ["stack:X:healthy"]`) purely to satisfy pass 1's static
string-matcher, undocumented anywhere.

## The fix

`src/ciu/provisioning.py`, `lint_graph`'s requires-check loop: before
flagging a `requires` entry as unprovided, match it against `_STACK_RE` and
resolve the captured stack name via `_resolve_declared_stack_path(name,
stacks.keys())`. A ref resolving to a real declared stack is satisfied
without appearing in any `provides` array. Every other ref kind — and a
`stack:*` ref that does NOT resolve to a real declared stack — keep today's
exact behavior, unchanged. `_probe_stack` and the live-probe path are
untouched.

```python
# src/ciu/provisioning.py, lint_graph (excerpt, post-review)
for stack_path, stack_info in stacks.items():
    for ref in stack_info.get("requires", []):
        if ref in all_provided:
            continue
        m = _STACK_RE.fullmatch(ref)
        if m and _resolve_declared_stack_path(m.group(1), stacks.keys()) is not None:
            continue
        errors.append(
            f"[ERROR] Stack '{stack_path}' requires '{ref}' but nobody provides it"
        )
```

This was independently reviewed and returned **ACCEPT-conditional**: the fix
itself needed **no changes** — "the cleanest single change in this batch,"
minimal, genuinely sharing `_resolve_declared_stack_path` rather than
reimplementing it, properly negative-tested in both directions, and the
reviewer independently reproduced the controlled-wrong-implementation
sanity check. Three blockers were all merge hygiene (stale base, a
duplicate-fix collision, a backlog-status inversion) — none touched the fix
logic. All three are resolved below.

## Review round — three blockers, resolved

1. **Stale base (branch 14 commits behind main).** Rebased. Non-interactively:
   `git reset --hard main` (landing on `384993b6`) followed by `git
   cherry-pick` of only the CIU-63 fix commit — clean, zero conflicts, since
   that commit never touched any file the intervening main commits also
   touched.

2. **CIU-78 collision (three self-authored commits colliding with main's
   already-landed `aa6cf1fd`, different variable name, same two tests).**
   Per the reviewer's explicit prescription: dropped all three of this
   package's CIU-78 commits (file / fix / mark-FIXED) entirely during the
   rebase — never cherry-picked, never hand-merged. Took main's versions of
   `tests/tests/test_ciu_deploy_actions.py` and
   `KNOWN_ISSUES_TODO_BACKLOG.md` wholesale. Verified post-rebase:

   ```
   $ git diff main -- tests/tests/test_ciu_deploy_actions.py KNOWN_ISSUES_TODO_BACKLOG.md
   (empty)
   ```

   This session's CIU-78 finding was a genuine independent, parallel
   discovery of the same bug already fixed elsewhere — nothing in the CIU-63
   fix depended on it, so dropping it cost nothing.

3. **Backlog inversion (CIU-78 marked FIXED, CIU-63 left OPEN).** Corrected:
   CIU-63's own row now reads `FIXED — ciu-P39: shape (b) implemented, not
   (a)` — the original filing offered two shapes ("(a) document the
   self-declaration requirement... (b) have lint_graph recognize
   stack:<path>:healthy|completed as self-satisfied... — closer to how the
   ref kind actually behaves"); this records that (b) was the one shipped,
   matching the reviewer's own agreement that (b) was the better call since
   it removes the redundancy rather than enshrining it. This replaces the
   (now-moot, dropped) CIU-78 row rather than adding to it. Table structure
   verified consistent (4 cells, matching every other row) before
   committing.

## Non-blocking review notes — addressed (both, at this session's discretion)

Landed in one follow-up commit, deliberately kept separate from the
already-reviewed fix commit so that commit's content stays exactly what the
reviewer examined:

- **`.match` → `.fullmatch` consistency.** `provisioning.py:171`'s new
  resolver call now uses `_STACK_RE.fullmatch`, matching `parse_ref`'s own
  style at `:74` (functionally identical given the fully-anchored pattern —
  a pure consistency cleanup). The pre-existing cycle-detection pass's own
  `.match` call was deliberately left untouched — not part of this fix, and
  the reviewer's citation was specifically against `parse_ref`.
- **Ambiguous-selector coverage gap.** New test
  `test_lint_graph_ambiguous_bare_selector_with_no_provides_still_errors`:
  the existing ambiguous-selector test short-circuited through the `ref in
  all_provided` fast path (its stack self-declared a `provides` entry) and
  never actually reached this fix's own resolver call. The new test removes
  every `provides` declaration so the ambiguous basename genuinely exercises
  `_resolve_declared_stack_path`'s ambiguous-match branch from `lint_graph`'s
  new call site, confirming it still errors rather than guessing.

## Tests (`tests/tests/test_ciu_provisioning.py`) — final set, all passing

```
$ python3 -m pytest tests/tests/test_ciu_provisioning.py -q
167 passed, 1 warning in 0.72s
```

Six new tests total:

- **Positive (brief's required case):**
  `test_lint_graph_stack_ref_satisfied_without_self_declared_provides` +
  `..._completed_satisfied_...` + `..._bare_selector_satisfied_...` — a
  stack requiring `stack:infra/vault:healthy` (and the `:completed` /
  bare-selector variants) where `infra/vault` is real and declared but does
  NOT self-declare a matching `provides` entry — zero errors.
- **Negative (brief's required case):**
  `test_lint_graph_stack_ref_to_bogus_stack_still_errors` — a non-resolving
  selector still errors with exactly `"... but nobody provides it"`.
- **Regression bar:** `test_lint_graph_stack_ref_other_kinds_still_require_provides`
  — a real declared stack existing must not leak into satisfying an
  unrelated ref kind.
- **Review-added:** `test_lint_graph_ambiguous_bare_selector_with_no_provides_still_errors`
  — closes the coverage gap the reviewer identified (see above).

### Controlled-wrong-implementation sanity check (manual, per the original brief)

Performed in the original session, before the fix commit: temporarily
reverted the special-case block to the original two-line form and reran the
positive tests — all failed with exactly the `"but nobody provides it"`
message, confirming the fix is load-bearing. Restored and reran green
before committing. The reviewer independently reproduced this same check.

## Docs

`docs/SPEC.md`:
- **S13.2** — new paragraph stating a `stack:*` ref needs no `provides`
  self-declaration (CIU-63), unlike every other ref kind in the table.
- **S13.3** — new paragraph spelling out the exact resolution rule
  (`_STACK_RE` + `_resolve_declared_stack_path`) and that a non-resolving
  selector still errors, unchanged.

`ciu check --help` / `ciu --help`: grepped `src/ciu/cli.py` in full — no
per-ref-kind enumeration exists anywhere in either, so nothing implied a
self-declaration requirement to correct. No change made or needed.

## Gate — final, real, verbatim verdict (read in a separate step from the run)

Command run synchronously (blocking, no backgrounding), from inside `ciu/`,
against commit `04097c4c93e0286fbea50d63cd10573b973652b0` (post-rebase,
post-review-fixes):

```
$ ./run-gate.py ciu --worktree /workspaces/vbpub/.worktrees/ciu-P39-lint-graph-stack-refs
run-gate: admission: lane 'ciu' declares no resources.memory — not memory-accounted (shared-infra rules still apply)
run-gate: rev 23 | lane ciu | env [environments.tester-unified] in central .../run-gate.toml | slice dev-background.slice ($CGROUP_PARENT_DEV_BACKGROUND)
run-gate: ephemeral env (nothing declared)
run-gate: budget 30m (advisory)
run-gate: docker argv: /usr/bin/docker run -d --name run-gate-vbpub-ciu-92111-1788139661 ... tester-unified:local bash -c '... sha256sum -c assay-2.3.0.pyz.sha256 ... assay-2.3.0.pyz run ciu --file assay.toml --verdict-json .assay/verdict-ciu.json'
assay-2.3.0.pyz: OK
ciu: PASS (exit 0)
  commit: 04097c4c93e0286fbea50d63cd10573b973652b0
  argv: /opt/tester-venv/bin/python run-ciu-tests.py
run-gate: verdict artifact: .../ciu/.assay/verdict-ciu.json
run-gate: lane 'ciu' exit 0
```

Verdict JSON (`.assay/verdict-ciu.json`), read separately:

```json
{
  "outcome": "PASS",
  "exit_code": 0,
  "lane": "ciu",
  "commit": "04097c4c93e0286fbea50d63cd10573b973652b0",
  "declared_rigor": ["R0", "R1"],
  "enforcement": "gate"
}
{"rigor": "R0", "status": "PASS"}
{"rigor": "R1", "status": "PASS", "coverage": {"pct": 100.0, "covered": 15, "executable": 15, "branches_covered": 4, "branches_total": 4}}
```

A clean PASS on both R0 (whole-suite command exit 0) and R1 (changed-line +
branch coverage floor against `origin/main`, 100.0%). Local confirmation
beforehand, matching the gate's own declared environment
(`PYTHONDONTWRITEBYTECODE=1`, from `assay.toml [lanes.ciu].env`):

```
$ PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -q
3269 passed, 21 warnings in 38.90s
```

## Commits (`git log --format='%H %s' 384993b6..HEAD`, oldest first)

1. `f1b5a2a5` — `fix(ciu): CIU-63 -- lint_graph's requires-satisfied pass recognizes stack:* refs`
2. `bde6256f` — `fix(ciu): CIU-63 review follow-up -- fullmatch consistency + ambiguous-selector coverage gap`
3. `04097c4c` — `backlog(ciu): mark CIU-63 FIXED -- ciu-P39, shape (b) implemented`
4. (this LOG/REPORT commit)

`384993b6` is `main`'s tip at the time of the final rebase (ciu-P36's merge,
carrying the concurrent CIU-69/CIU-76/CIU-78 fixes this package's own
duplicate work was rebased onto and, for CIU-78, dropped in favor of).

## Scope discipline

`git diff --stat main -- src/ docs/SPEC.md tests/tests/test_ciu_provisioning.py`
(the only files this package's surviving commits actually change):

```
 ciu/docs/SPEC.md                         |  24 +++++++
 ciu/src/ciu/provisioning.py              |  29 ++++++--
 ciu/tests/tests/test_ciu_provisioning.py | 119 +++++++++++++++++++++++++++++++
 3 files changed, 167 insertions(+), 5 deletions(-)
```

Plus the backlog status-correction commit
(`KNOWN_ISSUES_TODO_BACKLOG.md`, CIU-63's row + header only — verified
`tests/tests/test_ciu_deploy_actions.py` carries zero diff against `main`,
confirming no CIU-78 hand-merge). `src/ciu/provisioning.py` is the only
product file touched, and only `lint_graph` within it. `_probe_stack`,
every other live-probe function, and `src/ciu/worktree.py` (CIU-76's owner,
already fixed on `main`) are untouched. Full reasoning for every deliberate
omission is in `ciu-P39-LOG.md`.
