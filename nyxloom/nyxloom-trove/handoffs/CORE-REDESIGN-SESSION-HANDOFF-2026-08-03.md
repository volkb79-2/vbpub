# Core redesign — session handoff, 2026-08-03

Purpose: everything a fresh session needs to continue the core-redesign
program without re-deriving it. The authoritative documents are still
[`CORE-REDESIGN-IMPLEMENTATION-PLAN-2026-08-02-AMENDMENT.md`](../reports/CORE-REDESIGN-IMPLEMENTATION-PLAN-2026-08-02-AMENDMENT.md)
and [`DEEP-REVIEW-2026-08-02-AMENDMENT.md`](../reports/DEEP-REVIEW-2026-08-02-AMENDMENT.md);
this file records HOW to work the program and what was learned doing it.

Read the plan's "Implementation progress" ledger first — it is the live state.
This file is the operating manual that sits beside it.

## Where the program is

Accepted and merged on `main`: CR-00, CR-15, CR-01, CR-02a, CR-02b, CR-03,
CR-04a, CR-04b, CR-05a, CR-05b, CR-05c, CR-05d, CR-05f. Every one through the authoritative
`tester-unified` gate at 100% changed-line coverage; gate evidence and commit
SHAs are in the ledger.

Next by dependency order: **CR-05e** — `EmitAttemptExit` alone, the LAST
effect family on the shell. It routes by attempt ROLE into the implementer
outcome table, the review verdict consumer, the self-review consumer, and
carve's two, so it needed every other family to move first. Budget 1 -> 0, at
which point the readiness audit's final structural oracle applies: reject
effector imports or effector-owned mutable state on `Daemon`, not merely a
long `_execute`.

`daemon.py` is 5,275 lines, down from 9,077 when CR-05 started.

**A reader is not a blocker.** The CR-05d row was once marked blocked because
two of its functions are called by `_build_input` as well as by the effects.
That reasoning was wrong and cost a package: a reader MOVES into the effector
module as a function, and the shell calls it there. The boundary rule is
one-directional -- the shell may call an effector; an effector may not call
the shell -- so a shared reader is a delegate, never a blocker.

**Write the delegates the shell actually calls, and no others.** CR-05d's
gate rejected at 413/415 on two forwarding methods with no caller. Grep for
callers before writing a delegate; the cockpit's per-module coverage will not
show you this, because the module reads 100% while the changed line sits
unexercised.

Remaining after that: CR-06, CR-07, CR-08, CR-13a, CR-16, CR-09, CR-10, CR-11,
CR-12, CR-13b, CR-14. Section 7 of the plan is the authoritative order.

## The working loop (follow this exactly)

One package at a time, in its own worktree, on its own branch:

```
git worktree add -b cr/nyxloom-cr05 .worktrees/nyxloom-cr05 main
```

1. Implement in the worktree. Run focused tests as you go.
2. Run the whole cockpit suite: `PYTHONPATH=src python -m pytest tests -n auto -q`.
   This is DIAGNOSTIC only, never evidence.
3. Commit on the package branch.
4. **REBASE onto `main`** — never merge `main` in. See the trap below.
5. Run the authoritative gate (the exact argv is `[gates.tester-unified]` in
   `nyxloom-trove/nyxloom.toml`):

```
docker run --rm --cgroup-parent=nyxloom-gates.slice \
  -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd /workspaces/vbpub/.worktrees/nyxloom-cr05/nyxloom && \
    PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -n auto -q \
      --cov=src/nyxloom --cov-report=json:/tmp/nyxloom-cov.json && \
    PYTHONPATH=src /opt/tester-venv/bin/python -m nyxloom.coverage_gate \
      --base main --coverage-json /tmp/nyxloom-cov.json --source src/nyxloom'
```

6. Verify the reported denominator against the branch's own changed-line count
   (see the trap below) before believing a green.
7. `git merge --no-ff` into `main` with the gate evidence in the message.
8. Update the ledger in the plan amendment: implementation SHA, review SHA,
   gate line, merge SHA.

A gate run takes about 6 minutes. Run it in the background and wait on the
log rather than blocking.

## The gate-base trap (this cost a near-miss)

`coverage_gate` resolves its base from HEAD's parent count:

- a normal tip is diffed against `merge-base(main, HEAD)` — the package's own
  delta, which is what you want;
- a MERGE commit is diffed against its FIRST parent, which for
  `git merge main` inside a package branch is the package tip — so the
  measured delta is **what `main` brought in**, not your work.

CR-01's first gate run reported `360/360 (100.0%)` and exit 0 while measuring
CR-15's 360 lines. Rebased, the identical tree failed at 178/190. It would
have merged on the previous package's evidence.

Two cheap checks catch it, and both are now standing policy:

- the denominator being byte-identical to the previously accepted package's is
  a red flag;
- recount out of band and compare. The script now lives in the repo at
  `tools/count_changed_lines.py` (CR-05a); run it from the package root:
  `python tools/count_changed_lines.py main src/nyxloom`.

**The recount script has a false-green of its own.** Its pathspec is passed to
`git diff` and is therefore interpreted RELATIVE TO CWD, and its output paths
need `--relative` to be openable from there. Give it the repo-root-relative
path (`nyxloom/src/nyxloom`) while standing in `nyxloom/` and it matches
nothing and prints a confident `TOTAL ... 0` — which reads exactly like "this
package changed no code", the same shape of wrong answer the gate-base trap
produces. Sanity-check the per-file breakdown, not just the total: a real
package prints one row per changed file.

**Never run another pytest while the `--cov` run is in flight.** Coverage
data from concurrent runs in the same directory merges into nonsense: a run
whose daemon.py genuinely sat at 94.5% reported 54.2% with two thousand
spurious "missing" lines. It looks like a catastrophic coverage regression and
it is an artifact. Re-run alone before believing any coverage number.

**The gate argv's inner `cd {worktree}/nyxloom` is easy to lose.** It sits
inside a nested single-quoted `bash -c` string; dropping it silently runs the
gate against the container's default cwd, which fails with an unrelated
`sqlite3.OperationalError: unable to open database file` from coverage. Write
the whole command to a shell script and grep the file for the `cd` before
running it, rather than re-typing it into a shell.

## Hard-won specifics a new session will otherwise rediscover

**A bare `...` line inside a docstring silently excludes the whole docstring
from coverage.** coverage.py's exclusion pass is textual, not AST-aware, so it
matches inside strings; excluding any line of a docstring excludes the
docstring statement. On a changed file the gate then reports it as a
`pragma: no cover` escape — correctly, just accidentally. Write `# ...` in
usage examples.

**The cockpit and the gate container are different interpreters.** The fake CLI
the behavioural corpus launches runs under a bare interpreter without
`jsonschema`. A module-scope `import jsonschema` in `results.py` killed every
dispatched leg in the container while passing cleanly in the cockpit. Keep
validator imports lazy in anything the wrapper imports.

**The gate diffs committed HEAD.** An uncommitted tree yields `NO MEASUREMENT`
(exit 3), which is neither a pass nor a tool failure. Commit first.

**Two ratchets now fire on every package and must be answered, not widened:**

- `tests/test_exception_census.py` — per-module budget of unclassified broad
  exception handlers. It fails BOTH over budget (new debt) and under budget
  (debt repaid without recording it). Classify a new handler with a trailing
  `# census: <class>` from the closed four-class vocabulary in
  `src/nyxloom/exception_census.py`. Never raise a number.
- `tests/test_core_characterization.py` — the ownership inventory. Every path
  must exist, the control-plane import closure must be covered, sizes are
  checked with a 10%/40-line tolerance, and a surface owned by CR-05/06/07
  must declare its test module's retirement handling.

**Test retirement is a required, classified step** (amendment 5.2). For every
test a package touches, decide: *behaviour oracle* (asserts an observable
artifact/event/state/exit code — migrate it, assertions unchanged) or
*structure mirror* (asserts internal call shape or a deleted mechanism —
delete it and NAME it in the commit). A third case came up repeatedly and is
worth naming: an oracle whose PROPERTY survives but whose OBSERVABLE moved
(events.jsonl → the store). Restate those; do not delete them.

**Package splits are legitimate and precedented.** CR-02 → CR-02a/CR-02b and
CR-04 → CR-04a/CR-04b were both split because a sub-package was independently
gateable and had cross-package consequences. The readiness audit already
sanctions this shape. Record the split in the ledger with the reason.

## Consequences of accepted packages that later packages must respect

These changed the ground rules; a package written against the old ones will
fail in confusing ways.

**CR-04a — the store derives the projection from committed state.**
`append_and_apply(project, states, **kwargs)` keeps its signature, but `states`
is now a caller CACHE that gets refreshed after commit, never the source the
projection is written from. Therefore:

- a task that exists only in a caller's dict CANNOT receive a transition — it
  is not in committed state, so `apply_event` skips it. Test fixtures must
  persist a task before transitioning it. Three fixtures relied on the old
  weakness.
- a caller can no longer smuggle a hand-edited field through an unrelated
  append. The store writes what the EVENT says.
- validation reads committed state, so a transition legal from a caller's
  stale snapshot and illegal from committed is now refused.
- a bare `storage.append_event` writes an event WITHOUT moving the projection,
  so the two diverge and the next transition is judged from a projection the
  log has left behind. The readiness audit counted 13 such bypasses in
  production code; they now fail loudly. Prefer `append_and_apply`.

**CR-04b — one store, three modules.** `storage.py` is the public API,
`storage_sqlite.py` the implementation, `projection.py` the pure validation and
projection both need. That split is what removed the import cycle; do not
reintroduce a dependency from `projection` to either of the others. The
`NYXLOOM_STATE_BACKEND` selector and the file backend are gone. New store
verbs available: `backup`, `restore`, `export_jsonl`, `import_jsonl`.

**CR-03 — the merge gate reads a typed record, never prose.** An agent writes
`nyxloom-judgement-<task>.json` in its worktree; the wrapper binds it to the
facts (head commit from git, evidence digest from the bytes it persisted) and
writes `result-<task>.json` into the attempt directory; the daemon reads THAT
through `results.load_result` with the expected identity. The wrapper exports
`NYXLOOM_TASK_ID` / `NYXLOOM_ATTEMPT_ID` to the child. `_parse_reject_class`
and `_review_rationale` still read markdown — they are routing hints and
re-dispatch text, and carry no merge authority. Do not let anything else read
prose for a decision.

**CR-02a — the snapshot fan-in fails closed.** Any AUTHORITATIVE input that is
not OK means the pass plans nothing and emits one `SNAPSHOT_UNAVAILABLE`.
Acquisitions go through `snapshot.SnapshotBuilder`; a function that takes a
`builder` parameter or names a snapshot type is automatically in scope of the
census rule. `_build_input` has ONE `permits_effects` guard and everything
below it is advisory by construction — `test_the_authoritative_input_set_is_closed`
fails if that stops being true.

**CR-05a — actions reach their effect through a registry, not a ladder.**
`Daemon._execute` is now a lookup into `effects.EffectRegistry`. Therefore:

- every `reconcile.Action` subclass MUST have a registered handler, and
  `require_covers` runs when the `Daemon` is constructed — a new action class
  with no handler fails at construction, including in every test that builds a
  daemon, not on the first pass that plans it.
- the effect families CR-05b still owns are registered as legacy specs whose
  handler is `_execute_legacy`. There is no fallback path: an unregistered
  action raises `effects.UnownedAction`.
- `effects.LEGACY_HANDLER_BUDGET` is a two-directional ratchet like the
  exception census. Moving a family LOWERS it in the same commit; nothing
  raises it.
- an effector reaches the outside world only through `ctx.ports` and records
  only through `ctx.append` / `ctx.transition`. No `effects*.py` module may
  import `daemon` or name `Daemon`; `tests/test_effects.py` reads the import
  graph with `ast`.
- `HandlerSpec.emits` is verified against the handler's own call graph, so
  appending a new event type fails until the spec says so. Declaring an event
  the code cannot emit fails too.
- background work is keyed by `ctx.idempotency_key`, which the registry
  computes from the spec. Do not key a new in-flight registry on a field you
  picked yourself.
- `PROVIDER_PAUSE_SECONDS` moved from `daemon.py` to `effects_lifecycle.py`,
  and the pause registry is `Daemon._provider_backoff` (the method
  `_provider_pause` kept its name, so the attribute could not).

**CR-05b — launching is gated at the effect boundary, not the planner.**
`effects_dispatch.admissible(ctx, kind)` runs immediately before every
wrapper launch. A test that patched `Daemon._dispatch_admissible` to simulate
a refusal no longer intercepts anything the effectors call -- patch
`effects_dispatch.admissible`. The daemon keeps a delegate for the carve
families CR-05d still owns. Also:

- the pass's snapshot verdict rides on `ctx.snapshot_audit`. `None` means no
  fan-in ran in this call stack (an operator-initiated verb) and is
  PERMITTED; it is not the same as clean.
- reading a committed review report goes through
  `effects_review.parse_reject_class(git, cfg, task_id)`. It is a routing
  hint and carries no merge authority -- do not grow a second reader.
- `effects_merge` refuses in five distinct ways and leaves the task at
  MERGE_READY in every one. A new failure path must escalate, never advance.

**CR-01 — document truth is a standing gate.** `product_truth.py` compares a
marker in a canonical doc against a machine fact. If a package changes one of
those facts, updating the doc (or the fact reader) is that package's
obligation. CR-04b had to do exactly this: `state_backend`'s machine source
moved from a compose env var to "does `storage.py` still select at all", which
is also CR-04's own acceptance — one mechanism instead of two that can
disagree.

## Environment notes

- Work dir `/workspaces/vbpub/nyxloom`. Package worktrees under
  `/workspaces/vbpub/.worktrees/nyxloom-cr<NN>`.
- `vbpub` has a concurrent committer. Use `git commit --only -- <paths>`;
  never stage-then-commit in the shared repo.
- The nyxloom daemon stays STOPPED for the duration of the program.
- Cockpit interpreter is the one on `PATH` (3.14, with pytest-cov and xdist
  available). The gate container's is `/opt/tester-venv/bin/python`.

## Suggested opening move for the next session

1. Read the ledger in the plan amendment.
2. Read this file.
3. `git log --oneline -15` on `main` to see the accepted packages.
4. Create the CR-05c worktree and read, in this order: `effects.py` (the
   boundary), `effects_review.py` (the worked example of a dispatch family),
   and then `Daemon._execute_legacy` — which is the whole remaining scope and
   nothing else. Do NOT read `daemon.py` end to end; it is 7,600 lines and
   reading it will consume the session before any code is written.

CR-05 is flagged operator-carved and frontier-implemented, and the stop-loss
watches it most closely: if the differential diff cannot be driven to
explained-or-zero, stop and re-scope rather than proceeding to CR-07. CR-05a's
was zero. CR-05b inherits the harness — add a scenario to
`tests/effect_differential.py` per family moved, and re-record the fixture
from the PRE-package tree (create a detached worktree at the branch point,
copy the harness in, run with `NYXLOOM_RECORD_EFFECT_TRANSCRIPTS=1`, copy the
fixture back) rather than from the tree you just changed. Recording from your
own tree proves nothing, and it is the one way this mechanism can be turned
into decoration.
