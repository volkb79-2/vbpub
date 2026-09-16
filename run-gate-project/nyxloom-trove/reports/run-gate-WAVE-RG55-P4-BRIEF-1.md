# run-gate-WAVE-RG55-P4 — BRIEF-1 (checkpoint cut, E-008)

Checkpoint reason: tool-call count passed the ~60 threshold (E-008's
superseding rule) with a clean, coherent boundary available — C1/C2/C3 all
committed, targeted-tested green, working tree clean. C4 (the largest,
riskiest deliverable) and C5 have not been started; cutting HERE rather
than mid-C4 keeps every commit atomic and avoids handing a successor a
half-built bare-host profiling path.

## State

Tip: **`e698835f`** on branch `rg55-followups-run-gate`, worktree
`/workspaces/vbpub/.worktrees/rg55-followups-run-gate`, base `186461de`.
Working tree clean (`git status --porcelain` empty). Three commits, in
order:

1. `a6716422` — C1 (RG-60): exec-lane inflight record.
2. `b5e4a9c6` — C2 (RG-59): live-run daemon-absent warning names the real
   cause.
3. `e698835f` — C3 (RG-58): bare-host `stall_timeout` load-time + doctor
   WARN.

Each commit's own message + the LOG entries below (`run-gate-WAVE-RG55-P4-LOG.md`,
"Commit 1"/"Commit 2"/"Commit 3") carry full rationale, decision-asks
handled per RW-9, and targeted-test evidence. Read the LOG before touching
anything — it has the one non-obvious wrinkle (C1's decision ask: why
`container_state()` stays gated on `profiling` being enabled rather than
called unconditionally) that a successor must not re-break.

**No package-wide gates have run yet** (no selftest, no assay-r1/r3/r2) —
only targeted `pytest -k` subsets per commit, per the handoff's own
iterating-vs-final-gates split. That is intentional and matches the
handoff: "Gates ... on the final tip, each at most once" — final tip is
after C5, not here.

## Host load (STILL BINDING — re-check before any pytest/gate run)

At last check (end of this session): PID **2415767** (P2's bare-host
`assay-r2`) was **still ALIVE**. Also live at dispatch: container
`run-gate-vbpub-r2-2315801-1789214565` (P1's r2) — re-check with
`docker ps --no-trunc` since it may have exited by now. Re-verify BOTH
before running anything beyond a single targeted `pytest tests/<file>`:

```
kill -0 2415767 2>&1 && echo ALIVE || echo GONE
docker ps --no-trunc | grep -i 'run-gate-vbpub-r2\|tester-unified'
cat /proc/pressure/memory
```

While 2415767 is alive: targeted pytest files ONLY, serial,
`nice -n 19 ionice -c 3`, no whole-suite run, no assay lane. Once BOTH are
gone: the whole `./run-gate.py selftest` at most once, then r1/r3, then r2
only when `pgrep -af 'assay-r2|assay.cli run r2'` shows no other mutation
run anywhere on the host (≤ 2 gate containers total, `docker update
--cpus=3` after any launch, remove in a `finally`).

## What remains

### C4 — RG-57 (the big one): bare-host lanes ARE profiled

NOT STARTED. Full design already worked out during this session's
orientation — do not re-derive it, follow this:

**Two sub-paths inside `run_bare_host_lane`** (`run-gate.py`, currently
ends ~line 7150 post-C3 edits — re-grep `def run_bare_host_lane`):

1. **Daemon path.** New helper `resolve_self_container_id(docker) ->
   tuple[str|None, str|None]` (id, skip-reason) — reads `/etc/hostname`,
   confirms via a DIRECT `docker inspect -f '{{.Id}}' <hostname>` call
   (NOT `container_state()` — that one calls `fail_infra`/raises on an
   ambiguous failure, which must never be allowed to abort a bare-host
   lane's own run; write this helper in the same never-raises style as
   `ProfilerClient._ctl`). Neither a read failure nor an inspect miss is
   an error — "not running in a container" is the ORDINARY case for a
   plain host invocation and degrades to the rusage path with a disclosed
   reason (goes through `print_profile_warning`, mode `"rusage"` — see
   below, it's an ADDITIVE change to that function's existing
   `mode == "basic"` branch, add a `mode == "rusage"` branch with suffix
   text along the lines of "coarse rusage sampling only").

   New function `start_bare_host_profiling(lane, lane_name, project_dir,
   worktree, docker, plan) -> dict` — parallels `start_lane_profiling` but
   (a) resolves the target via `resolve_self_container_id` instead of a
   caller-supplied id, (b) scope is ALWAYS `"container-shared"`, (c) on
   ANY failure (no self id, `ctl version` fails, `ctl start` fails)
   returns `mode: "rusage"` with `state["warning"]` set — **NEVER**
   constructs a `BasicSampler` (RW-27b: "NO basic cgroup sampler on this
   path" is explicit and deliberate). On `start` success, mode
   `"daemon"`, same state shape `start_lane_profiling` returns (client,
   session, session_line, profiler_status) — this lets
   `finish_lane_profiling(state)` be reused VERBATIM for the daemon
   sub-case (its `mode == "daemon"` branch is generic over scope/target
   already; do not duplicate it). Also print ONE extra disclosure line
   when mode becomes `"daemon"`: RG-57 requires the devcontainer-wide
   caveat be disclosed, and `print_profile_session_line`'s contract-exact
   shape (Sec 4.6) must stay untouched for every OTHER lane kind, so add a
   bare-host-only follow-up print, e.g. `run-gate: profile session <id> is
   DEVCONTAINER-WIDE (RG-57 bare-host daemon path): cgroup numbers reflect
   the whole devcontainer, not lane <name> exclusively`.

2. **Rusage path (daemon-absent).** `import resource` (stdlib, POSIX —
   not yet imported at the top of run-gate.py, add it alongside the
   existing stdlib imports). Bracket the child process's OWN `wait()`
   with `resource.getrusage(resource.RUSAGE_CHILDREN)` immediately before
   spawning and immediately after reaping — NOT a global snapshot at
   function entry, has to bracket ONLY this lane's own child (the delta
   isolates it from anything else this run-gate process has spawned/
   reaped, e.g. `docker ps` preflight calls elsewhere in the same
   invocation). New function `finish_bare_host_profiling(state, ru_before,
   ru_after, started_at, ended_at) -> dict` (mirrors
   `finish_lane_profiling`'s return shape: `{resources, profile_error,
   profile_ref}`) — for `mode == "daemon"` just delegates to
   `finish_lane_profiling(state)` unchanged; for `mode == "rusage"` builds
   the FULL schema-1 Summary dict by hand:
   - `method: "rusage"`, `scope: None` (not a cgroup concept at all —
     decided during this session's design pass, see rationale below),
     `session: None`, `daemon: None`.
   - `memory.peak_bytes = ru_after.ru_maxrss * 1024` (Linux reports KiB),
     `memory.source = "rusage-maxrss"`, every OTHER `memory.*` leaf
     (`baseline_bytes`, `peak_over_baseline_bytes`, `p90_bytes`,
     `median_bytes`, `swap_peak_bytes`, `anon_peak_bytes`,
     `file_peak_bytes`) `None`.
   - `cpu.seconds = round((ru_after.ru_utime - ru_before.ru_utime) +
     (ru_after.ru_stime - ru_before.ru_stime), 3)`, `cpu.cores_avg =
     cpu.seconds / duration_seconds` (guard `duration_seconds` truthy),
     `cpu.cores_max`/`throttled_seconds`/`nr_throttled` all `None`.
   - `pressure`, `faults`, `pids`, `damon`, `host`, `events`: every leaf
     `None` (handoff's own words: "everything getrusage cannot give
     (pressure, damon, events, host) is null, never fabricated" — this
     ALSO covers the host-PSI fields even though `read_host_pressure_
     snapshot()` could technically read them; the recorded `resources`
     stays null there on purpose, the separate host-PSI DISCLOSURE LINE
     via `print_host_pressure_line()` is unaffected and still prints).
   - `target`: `container_id`/`cgroup`/`token`/`targets_seen` all `None`.
   - `samples`/`interval_seconds`: `None` (no sampling happened).
   - **`scope: None` rationale** (worked out, not re-derive): the
     contract's `scope` enum (`"container"|"container-shared"`) describes
     a CGROUP relationship; rusage measures via `wait4()`/process
     accounting, not a cgroup read at all — no contract value is honest
     here, and the handoff's own "everything getrusage cannot give is
     null" rule extends naturally to a fact (cgroup scope) getrusage
     structurally cannot supply. Verified this does not break
     `build_footprint_manifest` (`run-gate.py` ~3360): it merely echoes
     `profiled_res.get("scope")` into the manifest with no validation, so
     `null` there is safe. **A reviewer may reasonably prefer a different
     value — flag this as a decision-ask-already-resolved, not an open
     question, in the LOG when C4 lands, with this rationale attached.**

   `RESOURCE_SERIES_GETTERS`/`series_stats`/`_lane_stats`/
   `build_footprint_manifest` need **ZERO code changes** — confirmed by
   reading them: they pull generically via `.get("memory","peak_bytes")`/
   `.get("cpu","cores_avg")` etc., regardless of `method`, so a
   correctly-shaped rusage Summary flows into `stats`/`history --json`/
   `footprint` automatically. The ONE piece that DOES need a small,
   deliberate addition: **`source: "rusage-maxrss"` disclosure "next to
   such medians"** per the handoff. This requires adding a `"source"` key
   to `build_footprint_manifest`'s per-lane output dict (pull from
   `profiled_res.get("memory", {}).get("source")`, same `next(...)`
   most-recent-profiled-entry the function already computes for
   `scope`/`method` — literally one more line beside those two), then
   showing it in `_fmt_footprint_row`/`print_footprint_report` and in
   `cmd_doctor`'s footprint-staleness/drift section ONLY when
   `source == "rusage-maxrss"` (do not clutter every other lane's normal
   output). Grep `_fmt_footprint_row`, `print_footprint_report`,
   `cmd_doctor`'s "footprint staleness" block (search
   `FOOTPRINT_MAX_AGE_DAYS_DEFAULT`/`"footprint staleness"` in
   `cmd_doctor`) for the exact insertion points — not yet located
   precisely, budget a read pass there.

3. **R-36h containment.** The WHOLE profiling attempt (self-id resolve,
   `ctl start`, OR the `getrusage` calls) must be wrapped so an unexpected
   exception never escapes to abort the lane's own `subprocess.run`.
   Structure (worked out, follow this shape):
   ```
   try:
       profiler_state = start_bare_host_profiling(...)
   except Exception as exc:
       profiler_state = {"mode": "rusage", ..., "warning": f"profiling crashed unexpectedly: {exc}"}
   print_profile_warning(profiler_state); print_host_pressure_line(...); print_profile_session_line(...)
   if profiler_state["mode"] == "daemon": <devcontainer-wide extra line>
   ru_before = resource.getrusage(RUSAGE_CHILDREN) if profiler_state["mode"] == "rusage" else None
   started_at = _iso_utc(time.time())
   try:
       code = subprocess.run(argv, cwd=str(project_dir)).returncode
   finally:
       ended_at = _iso_utc(time.time())
       ru_after = resource.getrusage(RUSAGE_CHILDREN) if profiler_state["mode"] == "rusage" else None
       try:
           profile_result = finish_bare_host_profiling(profiler_state, ru_before, ru_after, started_at, ended_at)
           print_profile_warning(profiler_state)
           print_footprint_line(lane_name, project_dir, profile_result["resources"])
           if run_record is not None: run_record["resources"/"profile_error"/"profile_ref"] = ...
       except Exception as exc:
           <same B1d pattern run_container_lane/run_exec_lane already use>
   ```
   The handoff explicitly requires: "Plant exceptions in both paths in
   tests and prove it" — write a test that makes
   `resolve_self_container_id` raise, and one that makes
   `resource.getrusage` raise (monkeypatch `run_gate.resource.getrusage`),
   asserting the lane's own exit code is unaffected both times (mirror
   `TestProfilingNeverRaisesEndToEnd`'s existing style, `run-gate.py`
   test file ~line 14097 pre-C1, re-grep — line numbers have shifted).

4. **`--dry-run`.** Add `print_profile_plan_dry_run(profile_plan,
   "container-shared")` to `run_bare_host_lane`'s existing dry-run branch
   (currently prints only the argv; the profiling plan print is currently
   MISSING there entirely for bare-host — confirm by reading the function
   fresh, this brief is describing the pre-C4 state from memory of the
   session's own reading, verify before assuming).

5. **`RUN_GATE_PROFILE=off` / `profile = false`** still opt out — this is
   already true structurally (the `profiling = bool(profile_plan and
   profile_plan["enabled"])` gate at the top of the function, unchanged
   from before this package started) — just confirm the disabled branch
   (current early-return-ish code, `run_record["resources"] = None`,
   `profile_error = "bare-host lanes are not profiled (RG-57)"`) gets
   REWORDED — that literal `profile_error` string is now WRONG once C4
   ships (bare-host lanes ARE profiled when enabled; only a DISABLED
   bare-host lane records null) — change it to match the OTHER lane
   kinds' disabled-path wording (`profile_plan.get("disabled_reason",
   "disabled")`), not a bare-host-specific string anymore.

6. **`__revision__` bump to 42** happens in C5, not here — do not bump it
   in the C4 commit (the handoff assigns the rev bump + the "every
   CHANGES `[Unreleased]` entry names its RG id and the rev bump" rule to
   C5 specifically). C4's own commit should still describe itself in a
   `rev 41:` trailing addendum to the existing header comment block IF
   the convention demands one per-commit note — check how C1/C2/C3 handled
   this (they did NOT add rev-N: comment-block entries, only the
   SPEC.md/CHANGES.md-style prose was deferred to C5 throughout this
   package) — stay consistent, defer ALL of it to C5 as the three prior
   commits did.

7. **Docs deferred to C5**: SPEC R-43b's "Bare-host lanes are categorically
   unprofiled (RG-57)" line, SPEC R-43 new sub-rule R-43i, R-36 series doc,
   `CONSUMERS.md` §6 + LANE-AUTHORING.md resources section, the backlog's
   own RG-57 entry → FIXED with evidence. Do NOT touch these in C4's
   commit — C5 owns the whole documentation sweep by design (so nothing
   re-drifts once C4's real behavior is known). C4's own commit is CODE +
   TESTS ONLY, same discipline C1/C2/C3 kept.

8. **The consequence to exploit** (handoff's own words): after C4 lands,
   `./run-gate.py selftest` on THIS project (`run-gate-project` — its
   five lanes are all bare-host) will itself record a profile, and
   `footprint --write` STOPS refusing. This is the REAL, LIVE transcript
   C5's RG-61 item 5 needs (a fabricated one in `CONSUMERS.md` is one of
   the eight drift items C5 fixes) and produces the tracked
   `run-gate-project/run-gate.footprint.json` the wave's own goal names.
   **Do not attempt this live run while PID 2415767 or any other mutation
   run is alive** (host load rule) — it needs one real `./run-gate.py
   selftest` (or at minimum the `sql-mutation`/one lightweight lane) with
   profiling ENABLED, which means NOT under the test suite's ambient
   `RUN_GATE_PROFILE=off` kill switch, run for real against this
   project's OWN `run-gate.toml`. Budget host-load re-checks before doing
   this — it is the evidence C5's item 5 needs, capture the transcript
   into the REPORT when you run it.

### C5 — RG-61 (8-item doc sweep) + the docs deferred from C4 above + RW-28 addendum

NOT STARTED. Do this AFTER C4 lands (its own commit). Checklist, each of
the 8 backlog items (`KNOWN_ISSUES_TODO_BACKLOG.md` RG-61 entry, lines
4546-4609 as of this session — re-grep, may have shifted) — re-read the
entry itself before starting, this brief only summarizes:

1. `SPEC.md:555`/`:1660`-ish (re-grep, line numbers drift): `doctor`'s
   "profiler" check (C7) has no rule id of its own — R-44 is entirely the
   footprint manifest, R-30 is Doctor and untouched. Give it one (e.g.
   `R-30b`-adjacent or a new letter under R-44 — the entry does not
   prescribe the exact id, use judgment, name it consistently everywhere
   it is cross-referenced).
2. `R-30` still says doctor's summary counts "all four" statuses; code has
   emitted a FIFTH (`INFO`) since before this wave — fix the count/list.
3. `R-33` (RG-53's own promised SPEC amendment) never landed; `SPEC.md`
   (search `R-35a`) still says the now-FALSE "scores `0/0` as 100% — a
   silent false green" (RW-5 this wave already fixed the actual 0/0
   behavior; only the SPEC prose is stale). Fix the prose to describe the
   SKIPPED-verdict behavior RW-5 actually shipped.
4. `[profile]`/`[footprint]` documented as prose only in `CONSUMERS.md` —
   give them a full-schema block like `[history]`/`[lanes.<n>.resources]`
   already have there; also fix `RUN_GATE_PROFILE` being documented as
   `off`-only in CONSUMERS (the `on`-override and by-name-value-refusal
   behavior only appear in SPEC/`usage()` today).
5. `CONSUMERS.md`'s `footprint --write` transcript is FABRICATED (an
   impossible run — this project's own lanes are all bare-host, so
   `--write` refuses today) — replace with a REAL transcript from a live
   `footprint --write` run AFTER C4 + one profiled selftest PASS (see C4
   item 8 above — this is exactly that transcript's destination). Also
   fix the column layout to match `print_footprint_report`'s REAL output
   (compare byte-for-byte against a live run, do not guess).
6. `CHANGES.md`'s `[Unreleased]` header comment ("Verified empty as of
   2026-09-11's release") is stale above 100+ new lines — fix the comment
   and add a `__revision__` drift-marker bump note (every prior dated
   entry has one — copy the pattern from the most recent dated entry
   above it). Also: every `[Unreleased]` entry this package (C1-C4) added
   must name its own RG id and the rev-42 bump explicitly per the
   handoff's C5 instruction — this means going back and ensuring C1/C2/C3/
   C4's CHANGES.md entries (added HERE, in C5, not per-commit — confirm
   this package did NOT touch CHANGES.md in C1-C4; if so, C5 adds ONE
   consolidated set of `[Unreleased]` entries covering all five RG ids).
7. `usage()` (`run-gate.py`) omits `RUN_GATE_PROC_ROOT` while listing its
   sibling `RUN_GATE_CGROUPFS_ROOT` — add it. `SPEC.md:178`-ish still
   points the `profile` lane-schema key at `R-43g` (it is `R-43h` —
   `README.md` already has this right, copy its wording). `SPEC.md:50`-ish
   "both had shipped in code" claim (`resources`/`cpus`) is inaccurate for
   the `cpus` half — fix the claim.
8. Backlog RG-53's own evidence cites a stale test-count delta; this
   file's FIXED entries generally cite no commit hashes — **this package's
   own five FIXED entries (RG-57/58/59/60/61) get commit hashes when C5
   marks them FIXED** (`a6716422`/`b5e4a9c6`/`e698835f`/<C4 hash>/<C5's
   own hash for RG-61 itself, self-referential — use the "self-hash rule"
   the LOG already follows: the hash lands in the NEXT commit's entry, or
   note "this commit" if there is no next one). LOG's own test-count
   claims (in two places, per the entry) do not match tests actually
   added — audit THIS package's own LOG file for the same defect before
   closing it out (a "physician heal thyself" check the entry's own
   wording invites).

**RW-28 addendum (controller message received mid-session, fold into
C5, NOT a separate package):** `run-gate-project/assay.toml`'s `r2` lane
needs `budget_per_candidate = "900s"` added (assay's own key — r2 baseline
is ~130s serial, jobs=2; must bound a HUNG candidate, not merely a slow
one) with a one-line comment explaining why (a mutant flipping a thread's
`daemon=True` makes pytest hang at exit; without this key assay waits
forever — P1 hit this for real 2026-09-12 13:22Z). Mention it in
`CHANGES.md` `[Unreleased]` under the rev-42 entry, and add ONE sentence
to `LANE-AUTHORING.md`'s mutation-lane paragraph: every r2 lane sets
`budget_per_candidate`. **Do not start any assay lane while checking this
host-load rule is still binding** — this is a CONFIG edit, not a lane run,
so it needs no gate container itself, but do not let it tempt a "let's
just verify r2 respects it" live probe while a mutation run is active
elsewhere on the host.

### Gates (run ONCE, on the FINAL tip, after C4 AND C5 both land)

Per the handoff, in order, each at most once: `./run-gate.py selftest`
(bare-host; RG-53 judge, 100% line AND branch on every changed line vs
`--base rg55-run-gate-client` if the controller has announced the P2→main
merge by then, else `--base 186461de`), `./run-gate.py --base <same>
assay-r1`, `assay-r3`, and LAST `assay-r2` (mutation, `jobs = 2` already in
`assay.toml`; **RW-28's new `budget_per_candidate` lands as part of C5,
before this r2 run** — do not run r2 against a `r2` lane still missing
it). Every mutation survivor: a test that kills it, or a written
equivalent-mutant justification in the REPORT (RW-20/RW-22). Launch r2
UNTRACKED (`nohup ./run-gate.py --base <base> assay-r2 >
<scratchpad>/p4-r2.log 2>&1 & disown`) with a cheap tracked
`until ! kill -0 <pid>; do sleep 60; done` watcher — RW-26: the Claude
Code low-memory guard kills TRACKED background commands, not untracked
ones. Read each gate's verdict in a SEPARATE step, never a pipe tail.

## Exact next command for the successor

1. Re-check host load (commands above).
2. `cd /workspaces/vbpub/.worktrees/rg55-followups-run-gate/run-gate-project`
3. Re-grep `def run_bare_host_lane` and read it fresh (line numbers have
   drifted from every prior commit's edits — do not trust any line number
   cited in this brief without re-confirming).
4. Implement C4 per the design above (already fully worked out — this is
   an implementation pass, not a design pass).
5. `git commit -m "feat(rg55-p4): RG-57 -- bare-host lanes are profiled (C4)" --only -- run-gate-project/run-gate.py run-gate-project/tests/test_run_gate.py run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P4-LOG.md`
   (run from the WORKTREE ROOT, `/workspaces/vbpub/.worktrees/rg55-followups-run-gate`
   — pathspecs are repo-root-relative; running `git commit --only` from
   inside `run-gate-project/` with `run-gate-project/...` pathspecs FAILS
   with "did not match any file(s)" — this session hit that twice, it
   costs two wasted tool calls each time, do not repeat it. ALSO: `-m`
   and the message must come BEFORE `--only -- <paths>`, never after —
   `--` ends option parsing, so `-m "msg"` placed after `--` is itself
   swallowed as a bogus pathspec; this session hit that too.)
6. Then C5.
7. Then the final gate sweep.

## Self-authored retention prompt (paste into the successor's context)

> KEEP: this BRIEF-1 file in full (it is the plan); the four LOG entries
> already written (`run-gate-WAVE-RG55-P4-LOG.md`, Commits 1-3); tip hash
> `e698835f`; the git-pathspec gotcha in "Exact next command" step 5; the
> host-load re-check requirement before any gate/pytest command; the
> `scope: None` rationale for the rusage Summary (a real design decision,
> not an oversight — restate it if a reviewer questions it); the R-36h
> containment structure pseudocode (copy it, do not re-derive); the
> `container_state()`-stays-gated-on-profiling decision from C1's LOG
> entry (do not accidentally re-introduce that regression while touching
> nearby code in C4).
> DROP: the full orientation-reading narrative from this session's own
> LOG "Orientation" section (already absorbed into this BRIEF — no need to
> re-read the controller log/backlog/contract from scratch, this BRIEF is
> the distilled spec); the tool-call-by-tool-call trace of how C1-C3 were
> debugged (the fixes are already IN the commits; only the LOG's own
> "Decision asks"/"Note on exact wording" prose need to survive, and it
> already does, in the LOG file itself).

## Telemetry (E-002, for the REPORT this package still owes)

Orientation call count before C1's first edit: ~30 tool calls (see LOG).
Total tool calls this session through this checkpoint: ~65 (rough count;
not tracked precisely — a successor picking up C4 should track its own
count against the SAME ~60-call/~120k-context threshold independently,
this session's count does not carry over). Nothing missing from the
handoff was found that blocked progress — the one real gap was the
controller log's own base-tip snapshot being stale relative to `main`
(RW-24..RW-27 not yet landed at `186461de`'s own copy of the file),
resolved by reading the current `main` copy instead; worth a note back to
the controller log family if this pattern recurs (a package's base tip
freezing a document that keeps moving on `main` is a real orientation
trap, distinct from anything RW-9 covers).
