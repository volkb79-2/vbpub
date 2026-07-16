# nyxloom-P36-doctor-replay-resilience — REVIEW

Reviewer: independent frontier reviewer (Opus 4.8), fresh session. Date: 2026-07-16.
Branch: `feat/nyxloom-P36-doctor-replay-resilience` @ `06c027a`.
Handoff: `nyxloom-trove/handoffs/nyxloom-P36-doctor-replay-resilience.md`.

## Verdict

**APPROVED.** All four oracles are genuinely met, and I verified the tests are
not hollow by running them against `main`'s unfixed source rather than by
reading them. The gate passes when I re-run it myself. Scope was respected
exactly: no forbidden file was touched.

Both required layers are present and correct: the root-cause fix in
`storage.apply_event` and the defence-in-depth degradation in
`doctor.doctor_project`.

I fixed nothing — I found no defect that both warrants a change and can be
fixed without contradicting a named oracle. I did find one real, reproducible
inconsistency (§Findings F1) that I judge to be **inherent in the handoff's
oracle set rather than an implementation error**, and which is strictly better
than the pre-fix behaviour. It belongs in the backlog, not in a rejection.

Do NOT merge — per role contract, this branch is left for the pipeline.

## Verified git state (not the receipt)

Receipt fields were not trusted; git state read directly:

- `git log main..feat/nyxloom-P36-doctor-replay-resilience` → exactly one
  implementer commit, `06c027a`.
- The real worktree is
  `/workspaces/vbpub/.worktrees/feat/nyxloom-P36-doctor-replay-resilience`,
  **not** the `/workspaces/vbpub/nyxloom` path the packet lists (that checkout
  is on `main`). It was **clean** — the packet's "no uncommitted changes"
  claim is confirmed. The modified `legacy-workflow-origin/*.md` and
  `nyxloom-trove/backlog.md` files in the main checkout predate this task and
  are outside its scope.
- Scope: `git diff main...HEAD --name-only` → exactly the four files in
  `scope.touch` (`storage.py`, `doctor.py`, `test_storage.py`,
  `test_doctor.py`). **No forbidden file touched**: `types.py`,
  `reconcile.py`, `daemon.py` are all untouched. In particular `types.py`
  gained **no** `BLOCKED -> BLOCKED` self-edge — the normative transition
  graph (SPEC §4) is intact, and the fix lives at the apply layer as the
  handoff required.

## Gate — re-run by me, not trusted from a report

```
docker run --rm -w <worktree>/nyxloom -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub \
  tester-unified:local bash -c 'PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
→ exit 0 (full suite)
```

Note for future reviewers: this suite's reporter suppresses both test names and
the `N passed` summary line, so `grep passed` returns nothing and an empty grep
is **not** evidence of failure. Verify via exit code and `--collect-only`
counts. The container's default workdir is `/`, so `-w` (or an explicit `cd`)
is mandatory or pytest silently collects nothing.

New tests are real and collected — counts, `main` → branch:

| file | main | branch | delta |
|---|---|---|---|
| `tests/test_storage.py` | 5 | 10 | +5 |
| `tests/test_doctor.py` | 16 | 17 | +1 |

That +5/+1 matches the diff exactly (the SUPERSEDED/CANCELLED test is
parametrised ×2).

## Hollow-test check — the tests were mutation-verified, not read

I checked out `main`'s source, copied **only** the branch's two test files onto
it, and ran them. If the tests were hollow they would pass against the unfixed
code. Five of six failed, with exactly the errors the handoff predicts:

| test | oracle | on unfixed `main` |
|---|---|---|
| `test_blocked_reassert_apply_is_silent_noop` | O1 | **FAILED** — `TransitionError` |
| `test_replay_tolerates_duplicate_blocked_and_keeps_latest_blocker` | O2 | **FAILED** |
| `test_superseded_cancelled_reassert_apply_is_silent_noop[SUPERSEDED]` | — | **FAILED** |
| `test_superseded_cancelled_reassert_apply_is_silent_noop[CANCELLED]` | — | **FAILED** |
| `test_doctor_replay_check_failure_degrades_and_other_checks_still_run` | O3 | **FAILED** — `RuntimeError: boom` propagated out of `doctor_project`, reproducing the live incident |
| `test_invalid_fixed_target_transition_still_raises` | O4 | PASSED — **correct**; it is a regression guard for behaviour that must be *preserved*, so it must pass both before and after |

That is the right signature for a genuine fix.

## Oracle-by-oracle

- **O1 — fixed-target from==to is an idempotent no-op.** Met. The guard at
  `storage.py:209` changed from `t is EventType.TASK_TRANSITIONED and tsf.state == to`
  to `tsf.state == to`, inside the branch already scoped to the four task
  events, so it correctly covers TASK_BLOCKED/SUPERSEDED/CANCELLED and nothing
  else. Verified independently: the handoff's root-cause claim is accurate —
  `TASK_TRANSITIONS[BLOCKED]` (`types.py:91-93`) genuinely excludes `BLOCKED`.

- **O2 — replay tolerates a duplicate BLOCKED and the latest blocker wins.**
  Met. The refresh inside the no-op is load-bearing: without it the first
  blocker would stick and O2 would fail. `replay()` builds its projection from
  the log alone, so the latest blocker legitimately wins there.

- **O3 — doctor degrades instead of propagating.** Met. `except NotImplementedError`
  is correctly ordered **before** `except Exception`; since `NotImplementedError`
  subclasses `RuntimeError`, that ordering is what preserves the existing
  `check-unavailable` behaviour — reversing it would have silently broken it.
  `except Exception` also correctly does not swallow `KeyboardInterrupt`/`SystemExit`
  (they derive from `BaseException`). The message names the failure
  (`{type(exc).__name__}: {exc}`), and the test proves checks 2-11 still
  contribute.

- **O4 — genuinely illegal transitions still raise.** Met, and the test is a
  real violation rather than a token one: `TASK_TRANSITIONS[COMPLETED]` is
  `frozenset()` (`types.py:94`), so `COMPLETED -> BLOCKED` has distinct from/to
  and must raise. The test also asserts the state did not move **on disk**, not
  just in memory. Validation was not weakened: `check_task_transition` is still
  called for every distinct from/to pair, and nothing catches `TransitionError`
  inside `apply_event` — the failure mode O4's negative warns about.

## Findings

### F1 (accepted, follow-up) — a re-asserted BLOCKED refreshes the blocker in memory but is never persisted

Real and reproducible, not theoretical. `apply_event` refreshes `tsf.blocker`
in the no-op path but returns `affected == []`, and `append_and_apply` saves
**only** the task_ids in `affected`:

```python
ev = append_event(project, **kwargs)
for tid in apply_event(states, ev):   # [] -> no save
    save_state(states[tid])
```

I reproduced it on the branch with a live duplicate `TASK_BLOCKED` carrying a
changed blocker reason:

```
in-memory blocker : second
on-disk   blocker : first
replayed  blocker : second
DIVERGENCE replay != disk: True
```

The irony is worth stating plainly: doctor's check 1 **is** the
replay-divergence check, so on this path doctor stops crashing and starts
reporting a `critical` divergence instead. The finding is honest, not a false
positive — the on-disk statefile really is stale relative to the log.

**Why this is not a rejection, and why I did not "fix" it:**

1. **The oracles mandate it.** O1 explicitly requires that "no task_id is
   reported as affected" for a TASK_BLOCKED applied to an already-BLOCKED task,
   while O2 requires the latest blocker to win. Given `affected` is exactly
   what drives `save_state`, those two requirements plus the existing save
   contract cannot all hold on the live path. The implementer satisfied both
   oracles as literally written; the tension is in the **contract**, not the
   code. Making `apply_event` return the task_id — the semantically honest fix,
   since the statefile genuinely did change — would fail O1's test.
2. **Fixing it means redefining `affected`** (does it mean "transitioned" or
   "changed"?) or special-casing a save in `append_and_apply`, the canonical
   mutation chokepoint. That is an architectural/semantic decision for the
   handoff author, and the handoff's own BLOCKED rule says not to improvise
   around a contract conflict. Patching it unilaterally would add unmandated
   risk to a package that meets every oracle and passes its gate.
3. **It is strictly better than before.** Pre-fix this same scenario was a hard
   crash (`TransitionError` / TICK_ERROR) *after* `append_event` had already
   written the event, so the log-vs-disk staleness existed then too — replay
   simply could not complete to report it.
4. **It is narrow and self-healing.** `reconcile.py:471` is the only
   `Transition(to=BLOCKED, ...)` producer and it sits behind the `!= BLOCKED`
   guard at `reconcile.py:448`, so the primary live path cannot produce a
   duplicate today — matching the handoff's own "latent rather than constant"
   framing. It also requires the blocker payload to actually *differ* (an
   identical re-assert mutates nothing, so disk and memory agree). The stale
   field is the blocker *detail* only — `state` is always correct on disk — and
   it heals on the next genuine transition, which clears `blocker` and saves.

**Recommended follow-up (backlog):** decide whether `affected` means
"transitioned" or "statefile changed", and if the latter, have a
payload-refreshing BLOCKED re-assert report its task_id so `append_and_apply`
persists it. That requires amending O1, hence a new package rather than a
review-fix here.

### F2 (observation, no action) — residual fragility in checks 2-11

Checks 2-11 still catch only `NotImplementedError`, so a non-`NotImplementedError`
failure in, say, check 2 would still take doctor down. This is **within
contract**: O3 and Work item 2 scope the broad degradation to check 1, and the
handoff's layer-2 rationale is explicitly about a *replay* defect not killing
the other ten. Worth a future package if doctor is to be robust across the
board, but not a defect in P36.

### F3 (non-finding, checked and dismissed)

- `ev.payload["blocker"]` in the no-op path is guarded by `if t is EventType.TASK_BLOCKED`,
  and the pre-existing non-no-op path at `storage.py:230` already indexes it
  unconditionally for TASK_BLOCKED — so no new `KeyError` surface.
- The no-op skips `tsf.since = ev.timestamp`, so a re-asserted BLOCKED keeps
  its original block time. That is correct (the task has been blocked since
  then) and consistent with the pre-existing P20 semantics.
- No implementer REPORT file exists, but **no `nyxloom-P*` task has one** —
  only REVIEWs. The handoff's "say so in the report" request is satisfied in
  substance by the unusually thorough code comment documenting the decision.
  Not a violation of convention.

## What I fixed

Nothing. No defect met the bar of "small, and fixable without contradicting a
named oracle". F1 is a contract-level question, F2 is explicitly out of scope,
F3 dismissed on inspection.
