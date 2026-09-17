# ciu-P48 — CIU-87: integration test suite leaks a Docker network +
# devcontainer network-membership per real run

**Input revision:** ciu `main` @ current HEAD (post ciu-v7.9.0/ciu-P46;
ciu-P47 is in flight on its own branch, unmerged — this package does not
depend on it and should not touch the same functions; if you find yourself
editing `write_generated_facts`/`read_generated_facts`/the overlay-rename
machinery, stop, you're in the wrong package).

**Status:** the fix contract is already fully specified in the filed
backlog entry — this is not an open design question, it's an implementation
task. Read `KNOWN_ISSUES_TODO_BACKLOG.md`'s `## CIU-87` section (search for
it) in full before writing code; it has the reproduction, the root cause,
the proposed contract (two fixes, operator has approved **both**), and
literal **behavioral oracles** you must satisfy exactly. This handoff adds
verified corrections and constraints on top of that entry — where they
differ, THIS document wins (the backlog entry was written from a live
reproduction at filing time; some details have since been re-verified
directly against current source, and are called out below).

## What's already true, verified directly against current source

- `_connect_devcontainer_to_network()` is `src/ciu/workspace_env.py:850-888`.
  Its existing guard is `os.environ.get("ENV_TYPE", "").lower() !=
  "devcontainer"` → no-op. **This guard is legitimate S1.9 behavior, not a
  bug** — it's what lets the feature work in real production devcontainer
  use. The test suite leaks BECAUSE it runs for real inside an actual
  devcontainer where `ENV_TYPE` genuinely IS `"devcontainer"` — from ciu's
  perspective, a test run and a real provisioning run are indistinguishable
  by this check alone. **Fix 2 (below) needs a NEW, test-suite-specific
  signal — do not just relax or remove the existing ENV_TYPE check, that
  would break real production behavior.**
- The backlog entry's claim that ciu already has an established "mocked-
  Docker vs real-Docker" test convention to reuse could **not** be
  confirmed by a broad grep of `tests/conftest.py` and the test suite
  (search turned up no existing `monkeypatch`-based docker-mocking fixture
  or naming convention). **Verify this yourself before designing around it
  — if no such convention actually exists, don't invent a fictitious one to
  match; just design the smallest new signal that does the job (an env var
  the test's own `conftest.py` sets, e.g. `CIU_TEST_SUITE=1`, checked
  alongside the existing `ENV_TYPE` check — your call on the exact name/
  shape, but keep it minimal and note in your REPORT that the "existing
  convention" claim didn't hold up.**
- **The backlog entry's own file count is incomplete — treat it as a floor,
  not a ceiling.** It names `test_ciu_test_repo.py` + "7 sibling test
  files" using the `test-repo` fixture-name pattern. A partial grep this
  session found only 6 files matching that literal pattern (`test_ciu_test_
  repo.py`, `test_hook_interfaces.py`, `test_ciu_render_selection_context.
  py`, `test_ciu_shipped_hook_contracts.py`, `test_ciu_templates.py`,
  `test_spec_contracts.py`) — 2 short of "7 siblings," meaning either the
  count is off or 2 more use a pattern this grep missed. **Separately, and
  NOT named anywhere in the CIU-87 backlog entry at all**: `test_ciu_
  worktree.py` and `test_ciu_worktree_lease.py` construct
  `f"repo-{instance_id}-network"` directly (a DIFFERENT literal prefix,
  `repo-*` not `test-repo-*`) and leak under the identical mechanism — this
  was found live on this host 2026-09-02 (33→14 networks after a sweep
  cleaned 19: 15 `repo-*` + 4 `test-repo-*`). **Do your own complete
  accounting** — grep the whole test suite for real (non-mocked) calls into
  `ensure_workspace_network`/`_connect_devcontainer_to_network` or anything
  that drives a real `ciu env generate`/`ciu up` under a devcontainer
  `ENV_TYPE`, rather than trusting either list above as exhaustive. Cover
  every file you find, not just the ones named here.

## Required fix — BOTH, per the backlog entry's own recommendation

### Fix 1 — `ENV_TYPE`-adjacent test-mode gate (proactive)

Add a new, minimal, test-suite-specific signal that `_connect_devcontainer_
to_network()` (and any other real-Docker-side-effect code these tests
incidentally trigger — check `ensure_workspace_network` and anything it
calls) checks and skips the real `docker network connect`/`docker network
inspect` subprocess calls when set. This should be the DEFAULT for the
bulk of the affected test files (the ones above, and whatever your own
sweep finds) — they're not testing this feature, they're just incidentally
triggering it as a side effect of exercising something else.

- Default ON (gate active, real side effect skipped) for every test that
  doesn't specifically need to verify S1.9's real devcontainer-attach
  behavior.
- Whatever test(s) DO specifically exist to verify `_connect_devcontainer_
  to_network()`'s real behavior (check if one exists; if not, that's fine —
  don't invent test coverage this package doesn't need) must be able to opt
  OUT of the gate to exercise the real path, and MUST use Fix 2's teardown
  fixture when doing so.

### Fix 2 — teardown fixture (reactive, for whatever still runs for real)

A `conftest.py`-level fixture that disconnects the devcontainer from, and
removes, every network a test run actually created for real — for the
opted-out cases from Fix 1, and as a safety net in general.

- **Must run even when the test fails** — a plain end-of-test cleanup call
  that a raised assertion would skip does NOT satisfy this; use a pytest
  yield-fixture (teardown code after `yield`) or `request.addfinalizer`,
  whichever fits the existing fixture style in this test suite.
- **Must be surgical, not a blanket sweep.** Track exactly which network
  name(s)/`instance_id`(s) THIS test run actually created (the code already
  computes `instance_id = sha256(physical_root)[:6]` deterministically —
  capture it at creation time) and clean up only those. **Do NOT** write a
  fixture that does `docker network ls --filter name=test-repo-` and
  removes everything matching — this devcontainer is a SHARED host (other
  sessions/agents may have their own concurrent, legitimate, not-yet-
  cleaned-up work on it; this exact confusion is what CIU-87 was originally
  — wrongly — suspected of being, per its own filing). A test's teardown
  must only ever touch what that same test created.
- Mirror what `ciu clean`/`ciu worktree reap` already do for a real
  workspace (`deploy.py:3621`, `worktree.py:2680` per the backlog entry —
  re-verify these line numbers against current source, they may have
  shifted) — disconnect the devcontainer first, then remove the network,
  same order, same tolerance for "already gone."

## Behavioral oracles (from the backlog entry, verbatim — satisfy these
exactly, they are the acceptance test)

- Run the real (non-mocked) suite twice in a row inside one devcontainer;
  `docker network ls | grep -c '^test-repo-'` (and, given the correction
  above, also `'^repo-'`) must return to its pre-run count after each run,
  not accumulate.
- `docker network inspect` on any network the suite creates must show the
  devcontainer disconnected (zero attached containers, or the network
  itself absent) once the suite process exits, success or failure.
- **Controlled wrong implementation**: reverting the teardown fixture (or
  removing the test-mode gate) must make the first oracle fail again within
  one run. Write this as a real, executable check — not just asserted in
  prose in your REPORT — the same way P46/P47 pinned their guards with
  actual mutation-style tests.

## Process requirements (same as P46/P47 — read
`nyxloom-trove/reports/ciu-P46-{LOG,REPORT}.md` once for the expected
shape if you haven't seen this repo's convention before)

- Fresh implementer, zero prior context beyond this document, the backlog
  entry, and the live repo.
- **This is NOT a breaking change** — no consumer-facing behavior changes
  (test-only + an internal skip-condition that only ever suppresses a side
  effect that was never supposed to be observable in test runs anyway).
  `CHANGES.md`'s new section does not need the MINOR-despite-BREAKING
  framing P46/P47 used; write it as a normal fix entry.
- **Real gate required**: `./run-gate.py ciu` (`--worktree <path>` if you're
  in an isolated worktree without a clean top-level tree). A green `pytest
  tests/` alone is not proof — read the gate's verdict in a separate step,
  never off a piped tail. **This package's own oracle requires running the
  real suite TWICE in a row and diffing the host's network count** — do
  this for real, on the real shared Docker daemon this devcontainer talks
  to, not a mocked/simulated count. Report the actual before/after numbers.
- Update `KNOWN_ISSUES_TODO_BACKLOG.md`'s CIU-87 entry to FIXED, with the
  real fix mechanism described (not the proposed-contract language it
  currently carries) and the actual before/after oracle numbers from your
  own run. Do not touch CIU-38, CIU-50, or anything from the P46/P47
  program.
- LOG/REPORT: `nyxloom-trove/reports/ciu-P48-{LOG,REPORT}.md`, same
  convention as the P46/P47 pairs.
- Checkpoint clause: ARM at ~120k context or ~60 tool calls, CUT at the next
  coherent boundary, continuation brief to a durable file if you need to
  stop, commit, and stop.
- Commit trailer on every commit:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_015vMn5oN1w6KpvjGsStwVbW
  ```
- **Do not merge to `main`.** Commit in your worktree/branch and stop — a
  fresh adversarial reviewer verifies before any merge (fresh implementer →
  real gate → fresh reviewer → merge on ACCEPT, same as P46/P47). The
  reviewer will independently re-run the real-suite-twice oracle themselves
  in their own control worktree, on the real shared daemon — make sure your
  fix actually holds up to that, not just to a single local run.
- Closing discipline: claim only what you ran, with the real numbers.
