# ciu-P51 — CIU-91+CIU-58 (test-repo/ xdist race) + CIU-96 (admission
# redeploy double-count) + CIU-88 (nyxloom.toml schema violation)

**Input revision:** ciu `main` @ `f5366cddfd116c9cc8b23e446b23d5c403b23217`
(the commit that fixed ciu-P50's CIU-96/CIU-97 backlog collision). Post
ciu-P50.

**Why bundled:** three small, independent, disjoint-file fixes — operator
direction (2026-09-08), same shape as ciu-P49's own bundling precedent.
**Do not let them interact**: if a change to one touches a file another
part's section doesn't name, stop — you're doing something no part asked
for.

**A fourth item, CIU-93 (`ciu host enroll`), is planned for this SAME
branch but is NOT part of this dispatch** — it is blocked on `cmru` KI-24
(a new `get.py enroll` subcommand) shipping and releasing first, which is
in flight separately. A follow-up handoff will be added to this branch once
that lands. Do not start CIU-93 work; it is explicitly out of scope for
THIS package (see Forbid).

---

## Part A — CIU-91 (confirmed root cause) + CIU-58 (broader class)

### Context, verified against current source

- `KNOWN_ISSUES_TODO_BACKLOG.md` — search `## CIU-91` and `| CIU-58`. Read
  both in full; CIU-91's own entry already contains a corrected,
  reviewer-verified root-cause analysis (do not re-derive it, trust it —
  a prior wrong mechanism claim in an earlier draft was explicitly struck
  and corrected there).
- The race: `tests/tests/test_ciu_render_selection_context.py`'s
  `_add_stack()` (line 223) does `shutil.copytree(SRC_APP, dst)` where
  `SRC_APP` is `TEST_REPO / "applications" / "app-config"` — the shared,
  checked-in `test-repo/` tree, read IN PLACE, not from a per-test copy.
  `tests/tests/test_ciu_test_repo.py`'s `_clean_stack_artifacts()`
  (line 83) — called directly on that same shared path
  (`_clean_stack_artifacts(APP_STACK)`, line 163;
  `_clean_stack_artifacts(WORKERS_STACK)`, line 503) — `.unlink()`s
  `ciu.toml`/`ciu.toml.j2`/`ciu.compose.yml` there as prep for its own
  fresh in-place render. Confirmed mechanism (CIU-91's own corrected text):
  a genuine cross-worker TOCTOU race under `--dist loadfile`
  (`run-ciu-tests.py:40` — this flag only serializes WITHIN one test file,
  never BETWEEN files) — `shutil.copytree`'s own `os.scandir` snapshot
  finds `ciu.toml` present, then a DIFFERENT xdist worker's
  `_clean_stack_artifacts` unlinks it before the copy's own
  `copy_function` reaches it a few lines later.
- No `xdist_group` marker (or any other cross-file serialization
  mechanism) exists anywhere in this test suite today — confirmed by grep.
  You are introducing this pattern, not extending an existing one.
- **Reproduced live this session** (ciu-P50's own post-merge gate run,
  2026-09-08): first run FAILED with exactly this `shutil.Error`
  reproduction; an immediate retry at the identical commit PASSED cleanly
  — consistent with the documented intermittent race, not a deterministic
  regression.

### Fix — CIU-91's own confirmed-correct proposed direction (b)

Give every test that touches a shared `test-repo/` path IN PLACE (not via
its own `tmp_path` copy) a dedicated `pytest.mark.xdist_group` marker, so
`pytest-xdist` schedules all of them onto the SAME worker — eliminating
the cross-worker race entirely without serializing the whole suite. This
is CIU-91's own text calling fix direction (b) "the one that actually
addresses the confirmed mechanism" (as opposed to struck-out direction
(c), which does not address the actual TOCTOU mechanism at all — do not
implement (c), it was explicitly ruled out).

Concrete steps:
1. Enumerate EVERY test in this suite that reads from or writes to a
   `test-repo/` path OUTSIDE of a per-test `tmp_path` (grep for
   `TEST_REPO`, `APP_STACK`, `WORKERS_STACK`, and any other module-level
   constant pointing under `test-repo/`, across every test file — not
   just the two CIU-91 already names). This is CIU-58's own ask (the
   broader class) — closing it means finding every consumer, not just
   the two files already confirmed to collide. Tabulate what you find
   (file | function | which shared path) in your REPORT.
2. Give every one of those tests the SAME `xdist_group` name (e.g.
   `@pytest.mark.xdist_group(name="test-repo-inplace")`) — a single group
   is correct here (they all share ONE physical resource, the checked-in
   `test-repo/` tree); do not invent per-path sub-groups unless you find
   a concrete reason two of them can never actually collide (state it if
   so).
3. Confirm `pytest-xdist`'s `xdist_group` plugin marker is registered
   (check `pyproject.toml`/`pytest.ini`/`conftest.py` for
   `markers = [...]` registration — pytest strict-markers mode, if this
   suite uses it, will reject an unregistered marker with a collection
   error, not a silent no-op; verify which mode applies and register the
   marker if needed).
4. **Scope decision, made here — do NOT do more than this.** CIU-58 also
   suggests a heavier fix ("isolate a pristine copy once per session" or
   "generate the fixture tree synthetically per test") — that is a bigger
   architectural change explicitly NOT in scope for this package. The
   `xdist_group` serialization fully closes the confirmed TOCTOU mechanism
   (CIU-91) and every other same-shape collision your Part A step 1
   enumeration finds; it is a complete fix for the class as currently
   understood, not a stopgap. If your own enumeration finds a
   consumer that this serialization genuinely cannot cover (e.g. a
   non-test process touching the same path), STOP and describe it in your
   REPORT rather than redesigning the fixture architecture.

### Oracle

- Run the full suite at least 5 times in immediate succession
  (`./run-gate.py ciu`, or the equivalent local `run-ciu-tests.py`
  invocation directly if faster to iterate on) with the fix applied — zero
  occurrences of the `shutil.Error`/`os.scandir`-related failure across
  all 5 runs. (This is a flake-suppression oracle, not a deterministic
  one — see the wall-clock/flake caution in Part D below; report the
  actual number of runs and their actual outcomes, don't just assert "it
  passed.")
- **Controlled wrong implementation**: temporarily remove the
  `xdist_group` marker from ONE of the two originally-colliding functions
  (`_add_stack`'s caller and one of `_clean_stack_artifacts`'s callers) —
  confirm the race becomes reproducible again under enough repeated runs
  (this may take several attempts given it's inherently timing-dependent;
  report how many runs it took to reproduce, and don't spend more than a
  reasonable number of attempts — if it doesn't reproduce in ~10 tries,
  say so honestly rather than claiming a clean negative result), then
  restore the marker and confirm it's gone again.

---

## Part B — CIU-96 (admission control double-counts a redeploying stack's own prior instance)

### Context, verified against current source

- `KNOWN_ISSUES_TODO_BACKLOG.md` — search `| CIU-96 |` (filed by this
  package's own predecessor, ciu-P50's adversarial reviewer). Read the
  full entry — it already states the mechanism, the concrete consequence,
  and why the fix is real follow-up work, not a one-liner.
- `docs/SPEC.md` **S15.23**'s "Known v1 limitation — a redeploy
  double-counts..." callout (search for it) is the same content, in
  shipped-doc form — read it too, and update it once fixed (do not leave
  stale "known limitation" text describing a bug this package just
  closed).
- `governance.check_mem_min_admission` (`src/ciu/governance.py`, search
  for the function) and `deploy.mem_min_admission_check`
  (`src/ciu/deploy.py`, called from `action_deploy`'s per-entry loop
  immediately before `_run_stack`) — the call site.
- `governance.enumerate_slice_children` — the primitive admission control
  sums over. It has no notion of "this occupant is entry X's own prior
  instance."

### Design call — THIS PACKAGE MUST MAKE ONE, deliberately left open by
CIU-96's own filing

The core problem: at the admission call site (pre-`_run_stack`, for THIS
invocation), the entry's compose file has not been rendered yet, so there
is no direct way to compute the CURRENT invocation's own container names
to exclude them — but that's not actually what needs excluding. What
needs excluding is the OUTGOING (currently-running, about-to-be-replaced)
instance's occupant(s), if any. Two candidate approaches, neither
prescribed — pick one, document why, in your REPORT:

1. **Read the PRIOR rendered compose file, if one exists.** If this
   entry was deployed before, its compose output from that EARLIER
   deploy may still be on disk (check where `engine.main_execution`
   writes `CIU_COMPOSE_OUTPUT` and whether it persists between deploys,
   or gets cleaned up first). If it's still there, read its
   `container_name`s the same way `apply_mem_min_injections`/
   `resolve_selection_health_containers` already do, and exclude any
   occupant whose scope corresponds to one of those names from the
   admission sum (you will need a name→scope mapping — `docker inspect`
   on each candidate name, then `container_transient_scope` on its PID,
   same technique `apply_mem_min_injections` already uses).
2. **Label-based correlation instead of name-based.** If ciu already
   injects (or could injedt) any identifying label onto governed
   containers (check `build_injections`/the compose overlay generation
   for anything resembling this — `governance.py`'s existing injected
   keys), a live `docker ps --filter label=...` scoped to this entry
   might be more robust than reconstructing names from a possibly-stale
   prior compose file. Only pursue this if you find real existing label
   infrastructure to build on — do not invent a brand-new labeling
   scheme as part of this fix; that would be a bigger change than this
   package's own scope warrants.

Whichever you choose: the fix must still refuse a GENUINE over-subscription
(a different stack, or a second concurrent instance of the SAME stack, that
would actually blow the ceiling) — only the entry's OWN prior, about-to-be-
replaced instance is excluded, nothing else. If you cannot make either
approach work cleanly within a reasonable scope, **STOP** and write
`BLOCKED: <what you tried, why it doesn't fit>` — this is exactly the kind
of judgment call CIU-96's own filing anticipated might need escalation
rather than a forced implementation.

### Oracle

- A stack declaring `governance.mem_min` at a value that exactly fills its
  slice's ceiling: first deploy admits (regression guard — must still
  work). A REDEPLOY of the SAME stack (same entry, prior instance still
  running under the same claim) must now ALSO admit (the fix — today it
  would spuriously refuse). A deploy of a DIFFERENT stack whose claim
  would genuinely exceed the ceiling, with the first stack's instance
  still running, must still be REFUSED (regression guard — the fix must
  not accidentally exclude unrelated occupants).
- **Controlled wrong implementation**: revert your exclusion logic,
  confirm the redeploy case fails again (spurious refuse), then restore
  it.
- Update `docs/SPEC.md` S15.23's known-limitation callout to describe the
  fix instead of the limitation (or remove the callout entirely if fully
  closed — say which in your REPORT).
- Flip `KNOWN_ISSUES_TODO_BACKLOG.md`'s CIU-96 row to FIXED with your own
  file:line citations.

---

## Part C — CIU-88 (nyxloom.toml schema violation)

### Context and fix — trivial, already precedented

- `KNOWN_ISSUES_TODO_BACKLOG.md` — search `| CIU-88 |`.
- `nyxloom-trove/nyxloom.toml:58` currently reads:
  ```
  asserts = ["tests-pass", "changed-line-coverage", "canary-verified", "assay-verdict"]
  ```
  `"assay-verdict"` is not a valid value in nyxloom's own schema enum
  (`vbpub/nyxloom/src/nyxloom/schemas/nyxloom-config.schema.json`).
- **Exact fix, already shipped once for nyxloom's own identical copy**
  (`nyxloom-P48`, see `vbpub/nyxloom/nyxloom-trove/nyxloom.toml` — read it
  for the precedent) — drop `"assay-verdict"` from the array and add a
  comment matching that precedent's own wording:
  ```
  # "assay-verdict" is NOT listed: it isn't a supported value in the asserts
  # schema enum yet (src/nyxloom/schemas/nyxloom-config.schema.json) -- adding
  # it there is a separate future schema change, not this package. Omitted
  # deliberately, not forgotten.
  asserts = ["tests-pass", "changed-line-coverage", "canary-verified"]
  ```
- No behavior change: `asserts` is a declared-intent list nyxloom's own
  lint validates against its schema; dropping an already-invalid value
  changes nothing about what actually gates a merge (the gate command
  itself, `run-gate.py ciu`, is unaffected — this is purely closing a
  checked-in schema violation nyxloom's own tooling would flag if it ever
  lints ciu's config).

### Oracle

- `python3 /workspaces/vbpub/nyxloom/exec-nyxloom.py lint
  ciu/nyxloom-trove/nyxloom.toml` (if this file is even lintable
  standalone — check; if not, whatever nyxloom-P48 used to confirm ITS OWN
  fix is the right precedent to reuse here) no longer reports the
  `"assay-verdict"` enum violation for this file.
- Flip CIU-88 to FIXED in the backlog.

---

## Process requirements (same convention as P46-P50)

- Fresh implementer, zero prior context beyond this document, all three
  backlog entries in full, and the live repo.
- Frame commits as `fix(ciu):` for Parts A and B (real defects), and
  `chore(ciu):`/`fix(ciu):` for Part C (your call — it's a checked-in
  config-file correctness fix, not new capability).
- **Real gate required**: `./run-gate.py ciu` (`--worktree <path>` if in
  an isolated worktree). Read the verdict in a separate step, never off a
  piped tail. Given Part A's own fix touches test scheduling, run the
  gate at least twice after your fix lands (not just once) to build
  confidence the race is actually closed, not coincidentally not-hit.
- Update all three backlog entries to FIXED with real file:line citations
  from your own diff — not the proposed-contract language they currently
  carry.
- LOG/REPORT: `nyxloom-trove/reports/ciu-P51-{LOG,REPORT}.md`, same
  convention as prior packages — LOG per commit, REPORT with per-oracle
  evidence for all three parts SEPARATELY (don't conflate CIU-91/58's
  flake-suppression evidence with CIU-96's admission-control oracle or
  CIU-88's lint check).
- Checkpoint clause: ARM at ~120k context or ~60 tool calls (whichever
  first), CUT at the next coherent boundary (green gate > commit >
  LOG/REPORT write > edit-cluster end; never on a red gate), repeat every
  ~40-55 calls, stop when <~40 calls remain. At the cut: continuation
  brief to `nyxloom-trove/reports/ciu-P51-BRIEF.md` + a self-authored
  `/compact`-style retention prompt at
  `nyxloom-trove/reports/ciu-P51-COMPACT.md`, commit, stop.
- Commit trailer on every commit:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01K6ZFTkbjEnh4haBGDUGyuJ
  ```
- **Do not merge to `main`.** Commit in your worktree/branch and stop — a
  fresh adversarial reviewer verifies before any merge. **Do not release
  yet either** — this branch has a fourth part (CIU-93) still to come; the
  single combined release happens once that lands too (separate
  instruction to the controller, not something this dispatch needs to act
  on).
- **Host is shared with a production game server** — 8 cores: serial
  pytest under nice/ionice, ONE gate container at a time across all
  agents, `docker update --cpus=3` right after launch, no builds
  concurrent with suites. Part A's own repeated-run oracle needs this
  discipline doubly — do not run multiple gate containers in parallel to
  "speed up" the 5x-repro requirement.
- Closing discipline: claim only what you ran, with the real numbers —
  especially Part A's repeated-run counts and Part B's controlled-wrong-
  implementation output.

## Scope / forbid

**Touch:** `tests/tests/test_ciu_render_selection_context.py`,
`tests/tests/test_ciu_test_repo.py` (and any OTHER test file your Part A
step-1 enumeration finds touching a shared `test-repo/` path in place),
`pyproject.toml`/`pytest.ini`/a `conftest.py` (only if marker registration
is genuinely required), `src/ciu/governance.py`, `src/ciu/deploy.py`,
`docs/SPEC.md` (S15.23's callout only), `nyxloom-trove/nyxloom.toml`,
`KNOWN_ISSUES_TODO_BACKLOG.md`.

**Forbid:** anything under `test-repo/` itself (the fixture CONTENT is not
what's wrong; only the test scheduling/admission logic is), `ciu8/`,
`modern-debian-tools-python-debug/`, `vbpub/cmru/` (KI-24 is a separate,
already-dispatched package), any file implementing CIU-93 (`ciu host
enroll` — that follow-up work has not started and this package must not
get ahead of it).

**BLOCKED rule:** if a named contract cannot be met as specified, or scope
requires a forbidden file, STOP — write `BLOCKED: <reason>` to the LOG,
commit, and exit. Do NOT improvise a workaround.
