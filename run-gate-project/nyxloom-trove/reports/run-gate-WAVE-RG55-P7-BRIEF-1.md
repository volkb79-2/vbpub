# run-gate-WAVE-RG55-P7 — CHECKPOINT BRIEF-1

Checkpoint fired per the handoff's own checkpoint clause (E-008: ARM at
~120k context / ~60 tool calls, CUT at a coherent boundary) — not a decision
ask (RW-9 doesn't apply, this is a genuine capacity checkpoint), not a red
gate. Cut point: A1 committed, tests green, LOG/REPORT written — the
clause's own top-priority boundary ("commit > LOG/REPORT write").

**Tip at cut:** `afac6fcb` on branch `assay-liveness`, worktree
`/workspaces/vbpub/.worktrees/assay-liveness`. Commits so far:

```
de32bb91 feat(assay): B091 A1 -- budget_per_candidate = "auto" default (D-23)
5a25655f docs(run-gate-project): P7 LOG/REPORT records for A1 (de32bb91)
f649a249 test(assay): fix a stray leftover assertion in the A1 diagnostics=None regression test
afac6fcb docs(run-gate-project): P7 LOG entry for f649a249
```

## Successor instructions

1. Re-read the ORIGINAL handoff in full first:
   `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P7-HANDOFF.md`.
   It has not changed and every rule in it (worktree scope, HOST LOAD,
   commit trailers, gate ordering, records contract) still binds.
2. Re-read `run-gate-WAVE-RG55-P7-REPORT.md`'s A1 section for the oracle →
   test mapping and the design decisions already made (do not re-litigate
   them: where `budget_per_candidate_derived_s` lives on the wire, the
   `run_mutation(budget_per_candidate_auto=...)` kwarg shape, the
   `_refuse_unbounded_without_unit_bounds` loosening).
3. Re-read `run-gate-WAVE-RG55-P7-LOG.md` for the per-commit record and the
   one real bug this session caught and fixed by self-review (a
   `diagnostics=None`+`"none"` crash) — a lesson for A2-A5 too: re-read
   every new conditional you write for what happens when the OTHER
   optional parameter is at its default, not just the case you're testing.
4. No new controller decisions landed since this brief was written; D-17/
   D-23 stand exactly as read in the design doc.

## What's DONE (A1)

`judge.mutation.budget_per_candidate = "auto"` default, fully implemented,
tested (targeted diff-coverage self-check: 0 missing lines/branches on all
5 touched src files), documented (CHANGES.md, CONSUMERS.md, backlog B091
progress note), and gate-verified only at the TARGETED-test level — the
real `tools/tester-unified-gate.sh` gate has NOT been run yet (deferred to
A6 per the handoff's own "mutation lane last, one at a time" ordering; do
not run it until every deliverable lands, to avoid burning a gate cycle on
work still in flight).

## What's OPEN — A2 through A6

### A2 — `os._exit(rc)` runner wrapper (python/pytest + baseline)

**The key architectural finding from this session's orientation, not yet
acted on:** assay does NOT invoke pytest via an in-process API — every R0/R2
command (baseline AND every mutant candidate) runs through
`runner.execute_plan`/`default_process_runner`, which calls
`subprocess.run(argv, ...)` on the LANE'S OWN DECLARED `argv` (e.g.
`["pytest", "-q"]`). Assay has no idea it's "pytest" beyond the adapter's
`language == "python"`. **The baseline and every R2 candidate share the
exact SAME `CommandPlan` object** (`mutation._execute_mutation_jobs`'s
`_run_one` calls `execute_plan(plan, cwd=snapshot.project_root, ...)` with
the identical `plan` `run_mutation` received as a parameter, and that same
`plan` is what R0 used for the baseline too, per `run_mutation`'s own
docstring: "baseline is the exact R0 result... never re-executed here").

**Implication:** injecting `os._exit(rc)` behavior has to happen by
modifying the EFFECTIVE ARGV of that one shared `plan`, not by teaching
`execute_plan` a special code path (that would touch every language/every
lane, violating "other runners untouched"). The natural mechanism already
exists: `CommandPlan.argv_appended`/`resolve_command_plan(..., argv_append=
...)` — used elsewhere (R1 coverage flags) to append extra argv WITHOUT the
lane declaring it. **Design not yet validated with a spike:** ship an
internal pytest plugin module inside the `assay` package (a real Python
module on `sys.path` when `assay` is installed, e.g.
`assay._pytest_progress` or similar — name is a design choice) that:
- registers `pytest_sessionfinish` (or wraps `pytest_cmdline_main`) and
  calls `os._exit(session.exitstatus)` after `pytest.main()` would
  otherwise return, bypassing the interpreter's normal thread-join-at-exit
  sequence (this is D-23's actual point: RW-28's incident was pytest
  printing its summary then hanging joining a non-daemon thread at
  interpreter shutdown — `os._exit` never runs shutdown handlers at all).
- (A4 will extend the SAME plugin to also emit per-test progress events.)

Inject it via `-p <module>` appended to the shared `plan`'s argv, ONLY when
`adapter.language == "python"` AND the lane declares native R2 mutation
(`lane.judge.mutation is not None and not lane.judge.mutation.is_ingested`)
— conditioned at the SAME place `_run_prepared_lane`/`_run_higher_rigor_lane`
builds/resolves the plan for a lane with R2, before baseline executes. Check
whether `resolve_command_plan`'s `argv_append` is already threaded through
that call site for another purpose (R1 `--cov` injection) and reuse the
identical mechanism rather than inventing a second one — this needs a fresh
read of how R1's coverage argv gets appended, which this session did not
reach.

**Open questions for the next session to resolve, not yet answered:**
- Does `-p modulename` reliably resolve a package-internal plugin module
  when assay is installed as a wheel/zipapp (not just from `src/`)? Needs a
  real spike, not just reasoning about it.
- What if the lane's own argv is `["python", "-m", "pytest", ...]` vs. bare
  `["pytest", ...]` vs. something that isn't pytest at all despite
  `language == "python"` (e.g. `unittest`)? The design assumes pytest
  because D-23 says "pytest.main()" explicitly and A4's hook is
  pytest-specific — if a real Python lane's argv doesn't actually invoke
  pytest, `-p <module>` appended to its argv would either be silently
  ignored (if the command isn't pytest) or break the command outright (if
  it's some other tool that doesn't understand `-p`). This may need to be
  scoped to "only when the lane's own argv literally contains `pytest`" as
  an extra guard, or documented as a hard requirement (a native Python R2
  lane's argv MUST invoke pytest) — this is a real design decision the
  next session should make explicitly and document, not discover by a
  broken lane.

### A3 — `hung` outcome

Needs a NEW `MUTATION_BUCKETS` member `"hung"`, touching (non-exhaustive,
found by grepping `MUTATION_BUCKETS` — re-grep, this list may be
incomplete): `verdict.py` (`Mutation.hung` field, `to_dict`, schema JSON's
`mutation` definition + outcome enum for `mutant_outcome`/wherever
`outcome_bucket` is closed-enum-validated), `mutation.py`
(`_execute_mutation_jobs`'s bucket dict, `_classify_mutant_result`),
`verify.py` (bucket-driven re-derivation checks — NOT yet read this
session, read it before touching `hung`). The handoff says "additive" (no
v12 schema cut) — confirm the outcome_bucket enum in `verdict.schema.json`
can be widened without a hard cut the same way `budget_per_candidate_
derived_s` was additive (it likely can, since JSON Schema enums are just
lists, but the MODEL's closed-vocabulary validation in Python — wherever
`outcome_bucket` gets checked against a fixed set — needs the SAME
widening, and `verify.py`'s re-derivation needs to accept `hung` as a valid,
non-`killed` bucket that behaves like `budget_exceeded` for scoring
(excluded from the `killed/(killed+survived)` denominator) without being
confused for it in reporting).

**Detection mechanism, not started:** requires (a) knowing the candidate's
subprocess PID (and its children — a process TREE, since pytest itself may
fork/spawn workers) to sample `/proc/<pid>/stat` utime+stime every 5s; (b)
detecting "runner reported completion" from the captured stdout tail (parse
a pytest summary line, e.g. a regex for `====... passed`/`failed`/`error`
in `N.NNs` `====`) while the process is still alive; (c) a 30s grace after
that detection, OR no CPU-time growth for `max(60s, 2 x slowest test)` since
the last output line (needs A4's per-test events to know "slowest test" —
**A3 and A4 are coupled**; consider implementing them together, or building
a placeholder cadence estimate for A3 that A4 later replaces).

**This is the largest single architectural gap found this session:**
`default_process_runner` is a BLOCKING call (`subprocess.run(...).wait()`
equivalent) — there is currently NO live-monitoring loop anywhere in this
codebase that watches stdout WHILE a subprocess runs. Implementing `hung`
detection requires either (a) a new `ProcessRunner` implementation that
launches non-blocking (`subprocess.Popen`), polls `/proc/<pid>/stat` +
drains stdout incrementally in a loop, and enforces the grace/CPU-growth
rule itself before returning a `CompletedProcess`-shaped result — replacing
`default_process_runner` for the R2 mutation-candidate path only (NOT for
R0/R1/every other lane — "other runners untouched"); or (b) a wrapper
subprocess (a small Python driver assay ships, invoked via argv rewriting
similar to A2's plugin) that does its own internal monitoring and reports
`hung` via its own exit code or a side-channel file, which `execute_plan`'s
EXISTING blocking model can still consume without assay's own process ever
polling `/proc` itself. (b) is probably architecturally cleaner (keeps
`execute_plan`'s contract unchanged, all liveness logic lives in one
subprocess-side driver) but has NOT been designed in detail — this is the
single most important design decision for the next session to make FIRST,
before writing any code, because A2's `os._exit` wrapper and A3's hung
detector are natural to build as the SAME driver script/plugin rather than
two independent mechanisms bolted together.

### A4 — progress stream: `plan` event's remaining fields + `test` events

A1 already shipped the `plan` event with `baseline_s`/`budget_per_candidate_s`/
`derived` (see `mutation.py`'s `run_mutation`, right after the baseline PASS
check). A4 needs to ADD to that SAME event (not a new one):
`slowest_test_s`, `expect_next_event_within_s` (`= max(3 x slowest test,
15s)`) — both require per-test timing, which only exists once the A2/A3
pytest plugin is emitting `test` events (nodeid, outcome, duration) DURING
the baseline run. This means **A4 cannot ship before A2's plugin exists** —
the plugin has to write per-test timing somewhere assay's own process can
read it back (the SAME progress NDJSON file the plugin's own subprocess is
a different process from — needs either the plugin writing DIRECTLY to the
shared progress file via appended lines matching `ProgressStream`'s exact
enrichment shape, which means the plugin needs to know the progress file
path, likely via an environment variable assay sets when launching the
candidate/baseline subprocess; or the plugin writes to a side file assay's
own process reads back and translates). Add `"test"` to `PROGRESS_EVENTS`
(mutation.py) when this lands.

### A5 — `--rejudge <id>[,...]` / `--rejudge-outcome hung,budget_exceeded,error`

**Design drafted this session, NOT implemented or tested yet.** Sketch (all
file/line references are from BEFORE this checkpoint; re-verify line
numbers after A2-A4 land, since they touch `mutation.py`/`runner.py`
heavily too):

- New pure function in `mutation.py`, placed near `select_mutation_shard`/
  `_outcome_from_record` (~line 1391-1406 as of `afac6fcb`):
  `drop_rejudge_records(state_root: Path, *, ids: Iterable[str] = (),
  outcome_names: Iterable[str] = ()) -> tuple[str, ...]`. Validates `ids`
  against `_CANDIDATE_ID_RE` (already defined, used by
  `mutation_state_record_name`); refuses (raises `MutationStateError`,
  already an `AssayError` subclass so `main()`'s existing `except
  AssayError` gives a clean CLI exit) any id with no matching
  `<state_root>/<id>.json` file — "refuse an unknown id" from the handoff.
  Scans `state_root` once, reading each `*.json`'s `outcome_bucket` field
  to resolve `--rejudge-outcome` names, then deletes (`Path.unlink`) every
  matched record and returns the sorted, deduplicated set of dropped ids.
- **`--rejudge-outcome`'s CLI vocabulary needs an alias map** — the handoff
  spells it `hung,budget_exceeded,error` but the internal bucket name is
  `"crashed"`, not `"error"` (`MUTATION_BUCKETS` = killed/survived/crashed/
  budget_exceeded/equivalent, +`hung` once A3 lands). Draft:
  `REJUDGE_OUTCOME_ALIASES = {"hung": "hung", "budget_exceeded":
  "budget_exceeded", "error": "crashed"}`, refusing any name outside this
  closed set (mirrors the `--operators` unknown-name refusal pattern in
  `cli.py`). **This alias choice ("error" -> "crashed") is a judgment call,
  not confirmed against any other document — reconsider it, or at least
  state it explicitly in CHANGES.md/CONSUMERS.md once implemented, since a
  reviewer will ask why the CLI flag and the wire field disagree.**
- CLI wiring (`cli.py`): new `run.add_argument("--rejudge", default=None)`
  and `run.add_argument("--rejudge-outcome", default=None)`, both comma-
  separated. Refuse both if `--resume` is not also passed (rejudge only
  makes sense as a pre-resume step; without `--resume` everything already
  re-executes). Refuse if the lane isn't a native R2 mutation lane (no
  `judge.mutation`, or ingested). Resolve the EFFECTIVE state root the same
  way `runner.py`'s R2 dispatch does (`state_dir if state_dir is not None
  else mutation.default_state_root(project_root)` — `_resolve_state_dir`
  already gives you the raw `state_dir`, possibly `None`) BEFORE calling
  `run_lane`, call `mutation.drop_rejudge_records(...)`, THEN call
  `run_lane(resume=True, ...)` as normal — the dropped records are just
  gone from disk by the time `run_mutation`'s existing resume loop reads
  them, so no change to `run_mutation`'s resume logic itself is needed.
- Oracle from the backlog: "`--rejudge` re-executes exactly the named ids
  and nothing else" — needs a test proving candidates NOT named stay
  resumed (their state records untouched) while named ones re-execute.
- Cross-reference B088 explicitly in a comment: this is orthogonal to
  judge-identity invalidation, not a replacement for it.

### A6 — close-out

Backlog B091 → FIXED (only once A1-A5 are ALL done — do not mark FIXED
early); CHANGES.md folding; CONSUMERS.md final pass (this session's A1 docs
are already in place, just needs the same treatment for A2-A5); README if
it references mutation execution; THEN the real gate
(`tools/tester-unified-gate.sh`, self-hosting, `bash -c` driven per
`run-gate.toml` — see the original handoff's Gates section) — the mutation
lane goes LAST, launched untracked (`nohup ... & disown`) with a cheap
tracked watcher, per the handoff verbatim. Do NOT run `cmru release`.

## HOST LOAD — this session's observation

The shared host got genuinely busy partway through this session (RAM PSI
`full avg10` spiked from ~1.5 to 16+ while OTHER wave tracks — P1/P2/P4, at
least one other agent's `pytest --cov` and a live `assay-*.pyz run r2
--resume` process for `rg55-run-gate-client` were observed via `ps
aux`/`docker ps`) — waited it out twice (~5-10 min each) using a `Monitor`
with an until-loop on `/proc/pressure/memory`, never ran a heavy test
during the spike. Re-check `/proc/pressure/memory` before your FIRST heavy
run in the next session; do not assume the ~1.5 baseline from early in this
session still holds.

## Self-authored retention prompt (paste into the successor's first turn)

```
Resume RG-55 P7 (assay B091) from BRIEF-1. Re-read the ORIGINAL handoff
(run-gate-WAVE-RG55-P7-HANDOFF.md) in full, then this BRIEF
(run-gate-WAVE-RG55-P7-BRIEF-1.md) in full, then REPORT.md's A1 section
and LOG.md. Tip is afac6fcb on branch assay-liveness (already the
worktree's current branch -- no new worktree add). A1 is DONE, tested,
documented, committed in 4 commits; do not re-implement or re-verify it
beyond a quick confirming test run if you touch adjacent code. KEEP: the
A2/A3 architectural finding (baseline and every R2 candidate share ONE
CommandPlan; no live-monitoring subprocess model exists yet; A2+A3 are
natural to build as one driver/plugin, design that FIRST); the A5 design
sketch (drop_rejudge_records, REJUDGE_OUTCOME_ALIASES, CLI wiring order).
DROP: the exploration narrative (which files/lines were read to reach
those conclusions) -- the conclusions in this BRIEF are already distilled;
re-reading mutation.py/runner.py fresh as you implement each deliverable is
still expected and fine, but you do not need to re-derive the shared-
CommandPlan finding or re-discover PROGRESS_EVENTS' closed vocabulary.
Proceed A2+A3 together (coupled), then A4 (needs A2's plugin), then A5
(independent, can be done anytime -- consider doing it FIRST if you want a
quick win before the hard A2/A3 architecture work), then A6. One commit
per deliverable, tests first, LOG per commit, checkpoint again at ~60 tool
calls or a coherent boundary if A2/A3's real complexity runs long -- do not
try to force all of A2-A6 into one unchecked pass given how large A2/A3
turned out to be on inspection.
```
