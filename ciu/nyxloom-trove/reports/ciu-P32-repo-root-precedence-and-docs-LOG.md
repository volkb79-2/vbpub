# ciu-P32 — `dev.resolve_repo_root` precedence fix + docs/usage() clarification — LOG

**Status: BLOCKED (blast-radius, not an `escalate_if` trigger) — implementation,
tests, and docs are all COMPLETE and verified correct; the package cannot be
declared 0-failures/100%-coverage without touching ONE test file outside
`scope.touch`. Per the controller's standing instruction for this wave ("if
you discover a real blast-radius issue outside scope.touch... stop, document
evidence and options in the LOG, don't ship red or silently widen scope"),
this is written up here for controller review/authorization rather than
resolved unilaterally.**

HEAD at start: `9a859545a4d4a6b0a3d6b1b71a434fb67d7d8967` (confirmed via
`git log -1` before starting, matched the handoff).

---

## 0. Headline finding — the docs-vs-code contradiction is REAL

Before touching any code I read `docs/SPEC.md` S1.1, `docs/CONFIG.md`'s
`REPO_ROOT` row, and `docs/CIU.md`'s `REPO_ROOT` row, then diffed them against
`src/ciu/dev.py:32-51`'s actual pre-fix body:

```python
env_root = os.environ.get("REPO_ROOT")
if env_root:
    return Path(env_root).resolve()
if define_root:
    return Path(define_root).resolve()
current = Path(start_dir).resolve()
while True:
    if (current / GLOBAL_CONFIG_DEFAULTS).exists():
        return current
    if current == current.parent:
        return Path(start_dir).resolve()
    current = current.parent
```

All three docs said, verbatim (SPEC.md S1.1, pre-edit):

> CIU MUST resolve the repo root in this order: `--define-root` (alias
> `--root-folder`) → `REPO_ROOT` from the environment → walk-up from the
> working directory...

**The code checked `REPO_ROOT` before `define_root` — the exact opposite of
what SPEC.md/CONFIG.md/CIU.md all three claimed.** This is not a hypothetical
concern the handoff raised defensively; it is confirmed, and it means an
explicit `--define-root` flag did not even win over a stale ambient
`REPO_ROOT` before this fix, contradicting SPEC S1.1 outright. This is
independently interesting regardless of the live-reproduction narrative: the
CODE was violating its OWN documented contract.

**Second, subtler finding, also confirmed:** fixing the code to simply match
the *previously documented* order (`define_root → REPO_ROOT env → walk-up`)
would **not** have closed the operator's live hazard. Under that order,
whenever `--define-root` is omitted, `REPO_ROOT` from the environment is
still checked **before** walk-up — so an ambient value would still
unconditionally outrank a successful derivation from cwd. The real fix
therefore goes *further* than restoring the documented order: it flips the
relationship between walk-up derivation and ambient `REPO_ROOT` (derivation
first, ambient only adopted when consistent or when nothing is derivable),
mirroring the already-shipped S2.7 refined-precedence pattern
(`workspace_env._compute_network_name`, CIU-41) but as a **refusal** rather
than a warn-and-proceed, per O1's explicit design (this resolver feeds
destructive verbs, not a value about to be freshly written to a generated
file). SPEC.md/CONFIG.md/CIU.md are corrected to this **new** precedence, not
merely restored to the old documented one — see §4.

## 1. Method

1. Read `src/ciu/dev.py` in full, `docs/SPEC.md` (S1.1 + surrounding S1/S2),
   `docs/CONFIG.md` (`REPO_ROOT` row + the "Pre-set-wins exceptions" section),
   `docs/CIU.md` (`REPO_ROOT` row + `PHYSICAL_REPO_ROOT` consistency-check
   prose used as the tone/placement precedent), `docs/DESIGN-GUIDE.md`'s
   CIU-41 section (found at line 57, not the handoff's approximate 213-222 —
   the file has moved since the handoff was written; re-verified by search,
   not assumed), and `src/ciu/workspace_env.py`'s `_compute_network_name` /
   `_warn_inconsistent_ambient` (read-only, borrowing the PATTERN per the
   handoff, not the code — `workspace_env.py` is `scope.forbid`).
2. Enumerated every real call site of `dev.resolve_repo_root` in `cli.py`
   myself (did not trust the handoff's "~8" estimate): `grep -n
   "resolve_repo_root"` found **7 direct invocations** (`_ksm`, `_provenance`,
   `_status`, `_bake`, `_worktree_exec` via its injected-callable parameter,
   `_worktree`'s main body, and the `dev` verb inline in `main()`) plus **one
   pass-through injection site** (`_worktree` passing the resolver function
   into `_worktree_exec`) — matching the handoff's "~8" once the pass-through
   is counted. Also enumerated the SEPARATE bare-fallback family for O6:
   `grep -n 'os.environ.get("REPO_ROOT", Path.cwd())'` in `cli.py` found
   **exactly 8** sites, across `render --host`, `layouts`, `up --layout`,
   `up --host`, `down --host`, `health --host`, `host-secrets`, `ssh`.
3. Fixed `dev.resolve_repo_root` per O1, verified by hand with a standalone
   script exercising all six precedence branches before touching `cli.py` or
   any test.
4. Added one `_resolve_repo_root_cli` helper in `cli.py` (the ONE seam every
   real call site funnels through) so the new `ValueError` always surfaces as
   this codebase's standard `[ERROR] ...` + non-zero exit — verified this was
   necessary: **none** of the 7 call sites had any exception handling around
   `resolve_repo_root`'s return value before this fix (it never raised
   before), so an unwrapped refusal would have produced a raw traceback with
   no top-level handler catching it anywhere in `cli.main()` (confirmed: no
   try/except wraps `main()`'s dispatch, and none of the 7 individual
   call-site try blocks that DO exist catch `ValueError` from a point
   preceding their own `try:`).
5. Wrote the two O2 tests (live-scenario reproduction + unaffected common
   case) plus 4 more precedence-branch tests and 8 O3 propagation tests, all
   in the one new file `tests/tests/test_ciu_dev.py` scope.touch names.
6. Corrected SPEC.md/CONFIG.md/CIU.md, added the DESIGN-GUIDE.md hazard
   section, added `_USAGE`/per-verb `--help` text, filed CIU-53 (this fix)
   and CIU-54 (the O6 follow-up) in `KNOWN_ISSUES_TODO_BACKLOG.md`, added a
   CHANGES.md entry.
7. Ran the full gate (`.venv/bin/python run-ciu-tests.py`) and discovered the
   blast-radius issue in §6 below.

## 2. The fix (O1)

`src/ciu/dev.py::resolve_repo_root` now implements, in order:

1. `define_root`, given explicitly, wins outright — returned immediately, no
   consistency check against ambient `REPO_ROOT` (an explicit flag is not
   second-guessed by a shell variable).
2. Otherwise walk up from `start_dir` for `ciu.global.defaults.toml.j2`.
3. If that walk-up SUCCEEDS: no ambient `REPO_ROOT` → use the derived root
   silently (today's common case, byte-identical). A CONSISTENT ambient value
   → silent. A DISAGREEING ambient value → raise a `[S1.1]`-tagged
   `ValueError` naming both paths and three remedies (unset `REPO_ROOT`, pass
   `--define-root`/`--root-folder`, or `cd` into the intended repo).
4. If the walk-up finds NOTHING at all: fall back to ambient `REPO_ROOT` if
   set, else `start_dir` (today's ultimate fallback, unchanged).

Verified by hand (before writing any test) with a standalone script covering
all six branches — all six behaved exactly as designed on the first attempt.

### `escalate_if` #2 — due-diligence check, NOT triggered

The handoff's second `escalate_if` asks me to verify the walk-up-finds-nothing
fallback-to-ambient case (step 4) doesn't reintroduce the same hazard. I
checked this two ways:

- **Logically:** step 4 only fires when there is NO derivable answer at all —
  cwd is not inside any CIU repo. In that case there is no legitimate,
  different identity being masked (the defining feature of the hazard this
  package closes); falling back to an already-sourced `ciu.env`'s `REPO_ROOT`
  is a reasonable convenience for "I'm running from an unrelated location but
  have a real project's environment sourced," not a masked default.
- **Empirically, against the literal operator narrative:** the handoff's
  illustrative scenario says the operator stood in `/workspaces/vbpub` with no
  `--define-root`. I checked the actual filesystem: neither
  `/workspaces/vbpub` nor `/workspaces/vbpub/ciu` (this tool's own source
  tree) carries a `ciu.global.defaults.toml.j2` — only `topos/`, `nyxloom/`,
  `pwmcp/`, and `ciu/test-repo/` do. A literal reproduction of "standing at
  the vbpub top level" would hit step 4 (walk-up finds nothing), not step 3
  (the refusal) — meaning if that is read as the EXACT literal cwd, my step-4
  fallback would NOT have protected that specific case. I judge this to be an
  imprecise paraphrase in the handoff's narrative rather than the operative
  scenario: O2's own oracle text (and the one I actually built the reproducing
  test against) is explicit that cwd must be "inside a real ciu-managed repo
  tree (containing ciu.global.defaults.toml.j2)" — i.e., the walk-up SUCCEEDS
  — which is the step-3 refusal path, not step 4. I record this discrepancy
  here for the record rather than silently reconciling it, but do not treat it
  as `escalate_if` #2 firing: the fallback case, as actually specified by O1
  and O2, does not reintroduce the hazard.

## 3. O3 — clean refusal propagation

Added `cli._resolve_repo_root_cli(define_root, start_dir)`:

```python
def _resolve_repo_root_cli(define_root: Path | str | None, start_dir: Path) -> Path:
    from .dev import resolve_repo_root
    try:
        return resolve_repo_root(define_root, start_dir)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(2)
```

All 7 real call sites now go through this helper (verified individually, not
just by pattern-matching the call text):

| Call site | Was the raw call inside a surrounding `try`? | Would a raw `ValueError` have been caught before this fix? |
|---|---|---|
| `_ksm` | No (`try` starts one line after) | No — raw traceback |
| `_provenance` | No (`try` starts one line after) | No — raw traceback |
| `_status` | No (`try` starts one line after) | No — raw traceback |
| `_bake` (`--profile` branch) | No (`try` starts one line after) | No — raw traceback |
| `_worktree_exec` (via injected callable) | No — caller's `try` only catches `wt_mod.WorktreeError` | No — raw traceback |
| `_worktree` main body | No (`try` starts a few lines after) | No — raw traceback |
| `dev` verb inline in `main()` | No — `main()` has NO surrounding try/except at all | No — raw traceback all the way to the interpreter |

Every one of these would have produced an unhandled traceback before this
fix's helper was added — the refusal alone (O1) was NOT sufficient for O3;
the helper was a necessary, separate piece of work. `_worktree_exec` keeps
accepting an injectable resolver-callable parameter for testability;
`_worktree` now passes `_resolve_repo_root_cli` instead of the raw
`dev.resolve_repo_root`, so tests that monkeypatch `dev.resolve_repo_root`
directly (the existing convention throughout this test suite) keep working
unchanged, since the helper always does a late `from .dev import
resolve_repo_root` at call time.

Verified live via `tests/tests/test_ciu_dev.py`'s
`test_every_cli_call_site_refuses_cleanly_on_conflicting_ambient_root`
(parametrized over `worktree list`, `status`, `bake --profile`, `ksm build`,
`provenance`, `dev web`) plus two dedicated tests for `worktree exec` and the
`dev` verb (proving `exec_instance`/`run_dev` are never reached — the
negative constraint from O2/O3: not just message text, but that no caller
proceeds).

## 4. O4 — docs corrected, hazard named

- `docs/SPEC.md` S1.1 rewritten to the new, ACTUAL precedence, with an
  explicit "Correction (2026-08-25, CIU-53)" paragraph stating plainly that
  the code was violating this SPEC's own prior text.
- `docs/CONFIG.md`'s `REPO_ROOT` provenance row rewritten; a new bullet added
  to the existing "Pre-set-wins exceptions" list explaining `REPO_ROOT` is the
  one exception that REFUSES rather than warns.
- `docs/CIU.md`'s `REPO_ROOT` row rewritten; a new paragraph added after the
  existing `PHYSICAL_REPO_ROOT` consistency-check prose (the closest existing
  precedent in that file), explicitly contrasting REFUSE vs. that section's
  warn-and-proceed.
- `docs/DESIGN-GUIDE.md` gains "Why `dev`/`worktree` refuse an ambient
  REPO_ROOT that disagrees with the derived root (CIU-53)", placed
  immediately after the existing CIU-41 section (found at line 57 by search,
  not the handoff's approximate 213-222 — the file has grown since), which it
  cross-references rather than duplicates ("The section above closes the
  masked-default hazard for the identity tuple... `REPO_ROOT` itself carries
  the SAME hazard one level up, for a DIFFERENT check").

## 5. O5 — usage/help text

Checked both candidate locations per the handoff's instruction. This
codebase's existing convention for CROSS-CUTTING run-scoped notes (not
specific to one verb) is the top-level `_USAGE` docstring's own bolded
sub-section (see the existing "Run-scoped overrides" block for `--ksm`/
`--log-prefix-time-short`) — so a new "REPO_ROOT resolution (`dev`/`worktree`
verbs, S1.1)" paragraph was added there, readable via plain `ciu --help`
before ever hitting the refusal. Additionally (belt-and-braces, since this is
also very much a per-verb concern) added a shorter cross-reference note to
`worktree`'s and `dev`'s own `_VERB_HELP` blocks, each pointing back to
`ciu --help`/`docs/DESIGN-GUIDE.md` rather than duplicating the full
explanation three times.

## 6. O6 — follow-up filed, not silently expanded

Filed **CIU-54** in `KNOWN_ISSUES_TODO_BACKLOG.md` (table row + full detail
section), naming the exact 8 sites found by `grep -n
'os.environ.get("REPO_ROOT", Path.cwd())' src/ciu/cli.py`: the `--host`
branches of `render`/`up`/`down`/`health`, `up --layout`, `layouts`,
`host-secrets`, and `ssh`. States plainly that this package closes the gap
for `ciu dev`/`ciu worktree *` specifically, not for every ciu verb, and that
unifying these 8 sites is a separate, larger design question (closer to
`deploy.py`'s own resolver) — not touched here.

Also filed **CIU-53** for this package's own core fix (FIXED), since this
codebase's convention (CIU-41/47/52) is to give every substantive fix a
permanent numbered record even when landed in the same package that files
it — this gives O4's doc corrections and CHANGES.md's entry something
concrete to cite, and gives CIU-54 a specific "not closed by CIU-53" anchor.
This was not explicitly required by any oracle but follows established house
style; happy to have it folded into a single entry if the reviewer prefers.

**Backlog file staleness (per this branch's established pattern from
ciu-P30/P31):** checked whether this branch's copy of
`KNOWN_ISSUES_TODO_BACKLOG.md` predates a filing commit on `main` the way
ciu-P30/P31 found. It does NOT for this case — `main`'s copy also tops out at
CIU-52 (confirmed via `git show main:ciu/KNOWN_ISSUES_TODO_BACKLOG.md | grep
'^## CIU-'`), so CIU-53/CIU-54 are genuinely new numbers with no collision or
porting-from-main needed (unlike CIU-48/49/52, which already existed on
`main` with different disposition text this branch's stale copy lacked).

## 7. Oracle-by-oracle evidence table

| Oracle | Status | Evidence |
|---|---|---|
| O1-precedence-corrected | DONE | `src/ciu/dev.py::resolve_repo_root`, rewritten; manually verified all 6 branches with a standalone script before any test was written (§2); `escalate_if` #2 checked and not triggered (§2). |
| O2-live-reproduction-closed | DONE | `tests/tests/test_ciu_dev.py::test_live_scenario_refuses_conflicting_ambient_repo_root` (real nested tree, real marker file, conflicting ambient path, asserts refusal naming both paths) + `::test_uncontaminated_case_is_completely_unaffected` (no ambient at all, derives silently) + `::test_every_cli_call_site_refuses_cleanly_on_conflicting_ambient_root`/`::test_worktree_exec_refuses_before_running_anything`/`::test_dev_verb_refuses_before_running_dev` (negative constraint: downstream `exec_instance`/`run_dev` proven never reached, not just message-checked). |
| O3-callers-propagate-refusal-cleanly | DONE | `cli._resolve_repo_root_cli` helper (§3); all 7 real call sites converted; table in §3 showing each was previously UNPROTECTED; end-to-end tests for 8 verb paths (`worktree list`, `status`, `bake --profile`, `ksm build`, `provenance`, `dev web`, `worktree exec`, plus the helper unit test) all assert `SystemExit(2)` + `[ERROR]` on stderr, never a swallowed/downgraded warning. |
| O4-docs-corrected-and-hazard-named | DONE | SPEC.md S1.1, CONFIG.md, CIU.md rewritten (§4); docs-vs-code contradiction stated plainly in §0 as its own notable finding, not buried; DESIGN-GUIDE.md new section cross-references the CIU-41 section rather than duplicating it. |
| O5-usage-text | DONE | Top-level `_USAGE` gains a cross-cutting paragraph (found by matching this codebase's own existing convention for such notes); `worktree`/`dev` `_VERB_HELP` blocks each gain a short cross-reference (§5). |
| O6-followup-filed-not-silently-expanded | DONE | CIU-54 filed with the exact 8 sites named (§6); CIU-53 filed for this package's own fix; neither of the 8 CIU-54 sites was touched (confirmed: `git diff` touches only `scope.touch` files, and within `cli.py` only the 7 `dev.resolve_repo_root` call sites — verified by re-reading the full diff before writing this table). |

## 8. Gate output (verbatim)

```
$ .venv/bin/python run-ciu-tests.py
...
=================================== FAILURES ===================================
_ TestDevProfileAndExecutionBoundaries.test_repo_root_precedence_and_marker_walk _
[gw1] linux -- Python 3.14.6 /workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu/.venv/bin/python

self = <test_ciu_workspace_dev_remaining_boundaries.TestDevProfileAndExecutionBoundaries object at 0x7f372e7fe490>
tmp_path = PosixPath('/tmp/pytest-of-vscode/pytest-3946/popen-gw1/test_repo_root_precedence_and_0')
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7f372e9e7000>

    def test_repo_root_precedence_and_marker_walk(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        marker_root = tmp_path / "repo"
        nested = marker_root / "a" / "b"
        nested.mkdir(parents=True)
        (marker_root / "ciu.global.defaults.toml.j2").write_text("", encoding="utf-8")
        explicit = tmp_path / "explicit"
        explicit.mkdir()

        monkeypatch.setenv("REPO_ROOT", str(explicit))
>       assert resolve_repo_root(None, nested) == explicit.resolve()
E       ValueError: [S1.1] refusing to guess the CIU repo root: ambient $REPO_ROOT=/tmp/pytest-of-vscode/pytest-3946/popen-gw1/test_repo_root_precedence_and_0/explicit disagrees with the root derived by walking up from /tmp/pytest-of-vscode/pytest-3946/popen-gw1/test_repo_root_precedence_and_0/repo/a/b (/tmp/pytest-of-vscode/pytest-3946/popen-gw1/test_repo_root_precedence_and_0/repo). This decides which repo destructive verbs (worktree rm/branches -y/clean) operate on, so CIU will not silently pick one. Fix by one of: (1) unset REPO_ROOT in this shell, (2) pass --define-root/--root-folder explicitly, or (3) cd into the repo you intend to operate on.

src/ciu/dev.py:85: ValueError
================================ tests coverage ================================
...
TOTAL                                             8693      0   3472      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
=========================== short test summary info ============================
FAILED tests/tests/test_ciu_workspace_dev_remaining_boundaries.py::TestDevProfileAndExecutionBoundaries::test_repo_root_precedence_and_marker_walk
======================= 1 failed, 2785 passed in 16.54s ========================
```

**Every other file in the ~2786-test suite passes; coverage is 100.00%
line+branch across every source module, including the new `dev.py` logic and
the new `cli._resolve_repo_root_cli` helper's except-branch.** This is the
ONLY failure, and it is a pre-existing test, unchanged since `main`
(confirmed: `diff <(git show main:ciu/tests/tests/test_ciu_workspace_dev_remaining_boundaries.py) tests/tests/test_ciu_workspace_dev_remaining_boundaries.py` — byte-identical; this is not a regression this branch introduced elsewhere, it is a direct, unavoidable consequence of O1's corrected precedence colliding with a test that hard-codes the OLD, buggy precedence as its expected behavior).

## 9. The blast-radius finding — BLOCKED, not `escalate_if`, per the wave's standing instruction

`tests/tests/test_ciu_workspace_dev_remaining_boundaries.py` is **not** in
`scope.touch`. Its `TestDevProfileAndExecutionBoundaries::
test_repo_root_precedence_and_marker_walk` (lines ~101-113) asserts:

```python
monkeypatch.setenv("REPO_ROOT", str(explicit))
assert resolve_repo_root(None, nested) == explicit.resolve()   # <-- old bug, hard-coded as "correct"
monkeypatch.delenv("REPO_ROOT", raising=False)
assert resolve_repo_root(None, nested) == marker_root.resolve()
assert resolve_repo_root(explicit, nested) == explicit.resolve()
```

The first assertion is a direct, literal test of the OLD, buggy precedence
(`nested` is inside `marker_root`, which has the marker file, so the walk-up
SUCCEEDS and derives `marker_root`; `REPO_ROOT` is set to a DIFFERENT,
disagreeing `explicit` path — exactly the scenario O1 says must now REFUSE).
There is no way to satisfy O1 (the core, load-bearing ask of this entire
package) without this specific assertion failing. I verified there is no
subtler reading that avoids this: I confirmed against `main` that this file
is untouched/identical there too (§8), so this is not something a rebase or
merge already resolved; and I confirmed it is the ONLY test in the ~2786-test
suite affected (the other resolve-repo-root direct test,
`test_ciu_dev_deeper7.py::test_repo_root_falls_back_to_start_directory_without_global_marker`,
tests the "nothing derivable, no ambient" fallback branch — which O1 leaves
unchanged — and passes unmodified).

Per this wave's standing instruction to me: *"If you discover a real
blast-radius issue outside scope.touch (an out-of-scope test breaking,
mirroring this wave's established pattern), stop, document evidence and
options in the LOG, don't ship red or silently widen scope — the controller
reviews and authorizes."* This is exactly that situation. I have **not**
edited `tests/tests/test_ciu_workspace_dev_remaining_boundaries.py`.

### Options for the controller

**A — (recommended) widen `scope.touch` by exactly this one file, to update
exactly this one test method's stale assertion.** The fix is a single-line,
mechanical change: replace the first `assert ... == explicit.resolve()` line
with a `pytest.raises(ValueError, match=r"\[S1\.1\]")` block (mirroring the
style already used at `test_ciu_dev.py::test_live_scenario_refuses_conflicting_ambient_repo_root`),
and adjust the two subsequent assertions' setup accordingly (they remain
valid as-is: `delenv` then check derivation, then check `define_root`
wins — neither of those two lines tests the now-corrected buggy behavior).
No other test in that file needs to change. This is the minimal, surgical,
almost mechanically-obvious fix, and it is what O1's own correctness
inherently requires downstream. Risk: essentially none — it deletes an
assertion of a bug this very package exists to fix, in a file about the exact
same function.

**B — Leave this package's `dev.py`/`cli.py` changes uncommitted / defer
merge, and land only the non-code artifacts (docs, backlog entries) that
don't depend on O1 being live.** Rejected as the default: it discards the
verified-correct core fix (the entire reason this package exists) and leaves
CHANGES.md/SPEC.md describing behavior the shipped code does not yet have,
which is its own inconsistency.

**C — File a companion package/backlog item authorizing exactly the Option A
edit as its own tiny, separately-reviewed unit**, if the controller prefers a
paper trail distinguishing "ciu-P32's own scope" from "the one out-of-scope
line it required changing" even after authorization. Functionally identical
to A, just organized as two commits/reviews instead of one.

I have implemented, tested, and verified everything else in this package to
completion (§7's table is all DONE, not PARTIAL). The full diff (code, new
tests, doc corrections, CHANGES.md, backlog entries) is committed on this
private feature branch in one clearly-labeled commit (see §11) so the
controller/reviewer can inspect and run it directly via normal `git
show`/`git log` tooling — committing to a private branch is not "shipping":
the actual ship/merge gate is the controller's merge decision, which this LOG
explicitly flags as NOT yet authorized. Nothing here is presented as a clean,
mergeable, all-green package.

## 10. Files changed (uncommitted, pending controller decision)

- `src/ciu/dev.py` — `resolve_repo_root` precedence fix (O1)
- `src/ciu/cli.py` — `_resolve_repo_root_cli` helper + 7 call sites converted (O3); `_USAGE` + `worktree`/`dev` `_VERB_HELP` text (O5)
- `tests/tests/test_ciu_dev.py` — NEW file: 15 tests (O2 + O3 + precedence-branch coverage)
- `docs/SPEC.md`, `docs/CONFIG.md`, `docs/CIU.md` — corrected REPO_ROOT precedence (O4)
- `docs/DESIGN-GUIDE.md` — new hazard section (O4)
- `CHANGES.md` — Unreleased/Fixed entry
- `KNOWN_ISSUES_TODO_BACKLOG.md` — CIU-53 (FIXED) + CIU-54 (OPEN, O6 follow-up)

No `scope.forbid` file was touched. `git diff --stat` (for the record):

```
 CHANGES.md                   |  26 ++++++
 KNOWN_ISSUES_TODO_BACKLOG.md | 170 ++++++++++++++++++++++++++++++++++++++-
 docs/CIU.md                  |  12 ++-
 docs/CONFIG.md                |   9 ++-
 docs/DESIGN-GUIDE.md         |  37 +++++++++
 docs/SPEC.md                  |  26 +++++-
 src/ciu/cli.py                |  61 ++++++++++----
 src/ciu/dev.py                |  67 ++++++++++++---
 8 files changed, 379 insertions(+), 29 deletions(-)
```
(plus the new, untracked `tests/tests/test_ciu_dev.py`.)

## 11. Commits

Two commits on this branch (`feat/ciu-qol-v8prep-wave`):

1. The full implementation/test/doc diff from §10, with a commit message that
   states plainly this is BLOCKED pending the §9 decision (not a clean,
   green, mergeable package as-is).
2. This LOG file, `docs(ciu):` prefix, per the handoff's instructions.

Exact hashes are in this package's final report (read back via
`git log --format=%H`, not predicted ahead of the actual commit).
