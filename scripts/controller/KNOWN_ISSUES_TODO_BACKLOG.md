# controller — known issues, TODO, and backlog

Created 2026-11-12 from the backlog-filing review of the controller-side job
launchers and resume tooling. IDs are `CTL-N`, allocated sequentially in this
file (no other allocator exists).

Normative behavior belongs in this directory's `README.md`, `rules.md`, and
`docs/` guides. A FIXED entry means code, tests, and documentation landed
together.

## Status

| ID | Summary | Severity | Status |
|---|---|---|---|
| CTL-01 | OpenCode and incomplete Codex run-name guards can reuse stale completion or mixed run records | Major | OPEN 2026-11-12 |
| CTL-02 | Host queue status is read from the configured controller home; retained queue state must move with that home before purge | Minor | OPEN 2026-11-13 |
| CTL-03 | `pi-impl.sh` passes the whole prompt file as one argv element; a prompt above about 128 KiB fails to start, and a leading `@` may be read as a file reference | Minor | OPEN 2026-11-14 |
| CTL-04 | The parallel-start test rarely fails without the account-state lock, and no mutant depends on it | P2 | OPEN 2026-11-15 |
| CTL-05 | The account state `threads` map grows by one entry per thread and is never pruned | P3 | OPEN 2026-11-15 |
| CTL-06 | `find-thread-account` falls back to reading every run stream in the runs directory | P3 | OPEN 2026-11-15 |
| CTL-07 | A catalog-guard refusal on the automatically picked account does not try the other account | P3 | OPEN 2026-11-15 |
| CTL-08 | Experiment harnesses overwrite their own logs; no per-run log directory and no teed driver output | P3 | OPEN 2026-11-15 |
| CTL-09 | The Codex compaction retest records no per-run compaction setting and reads no app-server compaction item | P3 | OPEN 2026-11-15 |
| CTL-10 | Worker briefs do not state the measured harness environment limits, so workers rediscover them | P3 | OPEN 2026-11-15 |
| CTL-11 | A readback built by a non-Claude controller has no layout (no recent-prose tail, no marker) until NL-65 | P3 | OPEN 2026-11-15 |

---

## CTL-01 — a reused run name silently consumes stale completion and report records

**Status:** OPEN (filed 2026-11-12); severity: major (a caller can report a completed or passing job while the new child is still running or has failed). `codex-impl.sh` now refuses a new run when `.done`, `.jsonl`, or `.err` exists unless `CONTROLLER_RUN_FORCE=1`; the remaining record-set and OpenCode gaps below are open.

**Problem.** The current Codex launcher checks only `.done`, `.jsonl`, and `.err` before a new run; other name-owned records such as `.last.txt`, `.wt`, `.route`, `.thread`, and auto-resume records are not part of that check. `opencode-impl.sh` has no collision preflight: it truncates the named `.jsonl` and `.err` files, then writes `.done` only after the child exits. A previous `<run>.done` therefore remains visible while the new OpenCode child runs, so `job-check.sh` can observe a stale `EXIT=0`, evaluate a stale report or gate log, and present a completed result for a job that has only just started. Brief/report paths can also be silently replaced or mixed across jobs. Two controller incidents observed the collision behavior with the run names `review-rg-selftest-fix` and `r2-jobs-fix`.

**Proposed minimal solution.** Use one run-name ownership preflight across Codex, OpenCode, review, resume, and scheduling launchers. For a new job, refuse if any name-owned record exists in the selected runs directory, including `.done`, event/error streams, final text, worktree binding, route, thread, brief/report identity, and auto-resume records; print the conflicting files before any overwrite or child start. Preserve an explicit force path (Codex currently uses `CONTROLLER_RUN_FORCE=1`) only for an intentional replacement, and make it reset the complete name-owned record set atomically while leaving unrelated runs and controller logs untouched. A deliberate same-thread resume uses the existing resume flow and must not acquire ownership through implicit name reuse. Bind resolved brief/report paths in run metadata and refuse a changed path unless forced.

**Where.** `scripts/controller/lib.sh` shared configuration; `scripts/controller/codex-impl.sh`; `scripts/controller/opencode-impl.sh`; `scripts/controller/codex-review.sh`; `scripts/controller/resume-usage-limited.sh` and `start-after.sh` where they invoke a launcher; `scripts/controller/job-check.sh` completion lookup; `scripts/controller/tests/test-wrappers.sh` and focused resume/job-check fake-root tests; `scripts/controller/README.md`, `docs/CONSUMERS.md`, and `docs/DESIGN-GUIDE.md`.

**Acceptance test idea.** Pre-seed a fake runs directory separately with each record type (`.done`, `.jsonl`, `.err`, `.last.txt`, `.wt`, `.route`, `.thread`, and auto-resume files) and assert every new-run launcher refuses before truncating a file or starting a fake child. A concurrent `job-check` while a fake child is running cannot see an old completion marker. The explicit force path replaces exactly the named run's full record set and records its previous identity. A same-thread resume retains its explicit session identity without being mistaken for a fresh name collision.

**Priority.** Major. The failure mode can falsely report success and undermine the controller's central completion-evidence guarantee.

**Close when.** Every launcher refuses the full pre-existing run-name record set by default, the explicit force path is atomic and name-scoped with prior identity disclosed, brief/report/worktree identity is bound for the run, and fake-root collision, force, resume, and concurrent job-check tests pass on a clean final commit with the user docs synchronized.

## CTL-02 — host queue status depends on state under CONTROLLER_HOME

**Status:** OPEN (filed 2026-11-13); documentation of the current state-location
contract and the remaining migration step.

**Dependency.** `scripts/controller/ctl-status.sh` reads per-host queue
markers from `$CONTROLLER_HOME/logs/host-sat-queue/`, the results ledger from
`$CONTROLLER_HOME/logs/host-sat-results.tsv`, and per-host pause flags from
`$CONTROLLER_HOME/host-sat-queue.disabled.*`. The shared configuration in
`scripts/controller/lib.sh` supports selecting another controller home, so
the status command works without the old program directory when those state
files are available under the configured home.

**Limitation.** Changing `CONTROLLER_HOME` does not migrate the queue logs,
markers or pause flags. If they are absent from the selected home,
`ctl-status.sh` cannot report the previous queue state. Preserve or recreate
the required state before retiring the current controller home.

**Close when.** The controller-home migration procedure names these files,
retains the needed status history and pause state, and a fake-root check
confirms `ctl-status.sh` reads them from an alternate configured home.

---

## CTL-03 — pi-impl.sh delivers the prompt as a single command-line argument

**Status:** OPEN (filed 2026-11-14); known limit of the pi route, not a defect in a
shipped guard.

**Limit.** `pi-impl.sh` reads the prompt file into one shell variable and passes
it as the last argv element of `pi -p --mode json ... -- <prompt>`. Linux caps a
single argv element at 131072 bytes (measured: a 100000-byte argument runs, a
200000-byte one fails with "Argument list too long", exec status 126), so a
prompt file above that size makes the wrapper record `OUTCOME=ERROR` without
starting pi. `pi --help` documents the form `pi [options] [--] [@files...]
[messages...]` and states that `--` treats the remaining arguments as
messages or files, so a prompt whose first characters are `@` may be read as a
file reference; this was read from the help text only, because a check needs a
model request and the kit rule is no token spend in review.

**Close when.** Either the wrapper refuses a prompt above a documented size
before launching, or it passes the brief through an `@file` argument that the
real pi accepts, and a fake-pi test covers a prompt beginning with `@` and one
larger than the limit. Re-measure the `@` behavior against the real pi in a
controller session with budget for one request.

---

## CTL-04 — the parallel-start test rarely fails when the account-state lock is removed

**Status:** OPEN (filed 2026-11-15); priority P2. Filed from the Open Design
account (`od-accounts`) review of the controller kit (merged).

**Problem.** `tests/test-wrappers.sh` starts 20 automatic DeepSeek launches
simultaneously and then asserts an even split of the recorded routes and an
intact account state. The wrapper mutation lane switches that block off for
speed (`CONTROLLER_TEST_PARALLEL_OD_STARTS=0` in `tests/test-mutations.sh`), and
no mutant depends on it. With the exclusive state lock replaced by a no-op, the
test failed in only 2 of 3 runs, so in its present form it is not a discriminator
for a lost lock. The lock is not unguarded: the `od-exclusive-state-flock`
mutant (`LOCK_EX` downgraded to `LOCK_SH`) is killed by `tests/test-hardening-tools.sh`,
which asserts that the exclusive lock operation is requested. That test checks
the call, not the serialization it provides, and no mutant removes the lock
entirely, so the race itself is covered only by the weak parallel-start test.

**Where.** `scripts/controller/tests/test-wrappers.sh` (the parallel-start block
and its `CONTROLLER_TEST_PARALLEL_OD_STARTS` gate);
`scripts/controller/tests/test-mutations.sh` (which sets the variable to `0`);
`scripts/controller/tests/mutate-sources.py` (the `od-exclusive-state-flock`
mutant that downgrades the lock).

**Wanted.** Make the race reliable rather than incidental: raise the number of
simultaneous starts, or add a test-only delay hook between the state read and
the state write so a no-op lock loses the split deterministically, and make at
least one mutant depend on the test (add a mutant that removes the lock
entirely and judge it against `test-wrappers.sh` with the parallel-start block
enabled, and the lane declares the runtime cost of that mode).

**Priority.** P2 — the only test of the account-state serialization race is
unreliable and outside the mutation lane; that state is the guard behind the
account-blocking and 402 accounting.

**Close when.** With the lock replaced by a no-op, a repeated campaign of the
parallel-start test fails every time; a mutant that removes the lock is killed
by that test inside the mutation lane; and the mutation lane and the test's own
comments state the start count, the enabled-in-mutation mode, and its runtime
cost.

---

## CTL-05 — the account state `threads` map grows without bound

**Status:** OPEN (filed 2026-11-15); priority P3. Filed from the `od-accounts`
review of the controller kit (merged).

**Problem.** The account state records one `threads` entry (thread id to account)
for every start or resume that supplies a thread id and never removes any. Nothing ages an entry,
and nothing drops one whose run evidence is gone, so the state file grows by one
entry per thread for the life of the runs directory and carries stale ids
indefinitely. Entries also carry no timestamp today, so an age rule cannot be
applied to the existing file as it stands.

**Where.** `scripts/controller/opendesign-accounts.py` (`fresh_state`,
`locked_state`, `save_state`, `record_start`, the `record-thread` and start
actions), and the same file's schema validation (`normalize_state`), which must
keep accepting a pruned state file.

**Wanted.** Prune entries older than a documented retention window or whose run
files (`.jsonl`, `.route`, `.thread`) are gone; record a timestamp with each
entry (or derive age from the named run's files) so the age rule has a source;
run the prune at a defined point (selection, save, or bootstrap) so it cannot be
skipped silently; and document the retention rule where the state format is
described.

**Priority.** P3 — slow state growth; no incorrect selection observed.

**Close when.** Retention is documented, the prune is implemented and covered by
fake-root tests (an old entry and a vanished-run entry are dropped, a live one
is kept), the schema still validates a pruned file, and no caller depends on a
pruned id for a resume that is still live.

---

## CTL-06 — `find-thread-account` falls back to scanning every run in the runs directory

**Status:** OPEN (filed 2026-11-15); priority P3. Filed from the `od-accounts`
review of the controller kit (merged).

**Problem.** The threads map is consulted first, but on a miss
`account_for_thread` globs every `*.route` file in the runs directory, reads the
matching `.thread` file and then reads the whole run stream to recover the
thread id. A resume therefore costs work proportional to the whole runs
directory whenever the map does not hold the id (for example after a state
reset or a prune), and large streams are parsed in full only to find one entry.
The map-first path is already correct; the fallback is what is unbounded.

**Where.** `scripts/controller/opendesign-accounts.py` (`account_for_thread`,
`thread_in_stream`, the `find-thread-account` action) and its callers
`scripts/controller/codex-impl.sh` and `scripts/controller/pi-impl.sh`, which run
it on every resume.

**Wanted.** Bound the fallback: use an index (the map, or a small thread-id index
derived from run metadata) as the primary lookup, and keep the directory scan as
a last resort. The scan already compares a run's `.thread` file before parsing
that run's stream; what is still unbounded is that the stream of every run whose
`.thread` file does not match is read in full (`Path.read_text`), so for example
make a first pass over all `.thread` files only, and parse streams only when
that pass finds nothing.

**Priority.** P3 — a cost and latency issue, not an incorrect answer.

**Close when.** The lookup answers from the map/index in the common case with a
test that asserts no run stream is read, the residual scan is bounded and
documented, and the multi-account ambiguity refusal still fires.

---

## CTL-07 — a catalog-guard refusal on the automatically picked account does not try the other account

**Status:** OPEN (filed 2026-11-15); priority P3. Product decision needed, see
`Wanted`. Filed from the `od-accounts` review of the controller kit (merged).

**Problem.** An automatic start picks an available account, then checks that
account's provider catalog and refuses (`exit 3`) when it cannot be read or
carries a non-null `tool_mode`. At that point the other account's catalog, which
may be healthy, is never considered, so an automatic start fails while a
correct automatic answer existed. Pinned routes must keep their current
behaviour and must not fail over: a pinned route names an account deliberately.

**Where.** `scripts/controller/opendesign-accounts.py` (`pick_available`,
`ensure_catalog_safe`, the automatic form of the `start` action) and
`scripts/controller/codex-impl.sh` where the selected account is used after the
call. The `start-pi` action performs no catalog check (pi owns its provider
catalog), and `start-resume` checks the catalog of a recorded account, so
neither is the case described here.

**Wanted.** Decide and implement one of two outcomes: (a) an automatic pick tries
the other available account when the first one's catalog is unsafe, printing one
log line that names the refusal and the account tried, and recording the account
actually started; or (b) keep the refusal as by design and document it, including
what an operator does next, so the behaviour is intentional rather than a
default nobody chose.

**Priority.** P3 — a lost automatic start in a rare catalog-corruption case.

**Close when.** The chosen behaviour is implemented, tests assert both
directions (automatic start fails over or refuses with the documented reason;
pinned start never fails over), and the user-facing documentation states which
case applies.

---

## CTL-08 — experiment harnesses overwrite their own logs

**Status:** OPEN (filed 2026-11-15); priority P3. Filed from the compaction-steering
(`ctx-steer`) research review of `docs/design/ctlstat` (merged).

**Problem.** The X1-X5 experiment runs kept one scratch subdirectory per experiment
and reused driver and agent log filenames within it, so later runs overwrote earlier raw logs (the
passive hook log of the basic run, the pi extension log and RPC outputs of the
earlier runs, and the driver console output with the SIGINT exit codes). Those
claims now rest on the author's run notes, and the experiment document had to
record what could not be re-read.

**Where.** `docs/design/ctlstat/EXPERIMENT-compaction-steering-2026-11.md`
(evidence retention section) and the experiment conventions it relies on; a
small runner helper or convention belongs beside the kit's other experiment
practice rather than in a single experiment note.

**Wanted.** One log directory per run, created by the runner and named by run
and experiment, with the driver output teed into it so console-only results
(exit codes, timing) are retained as files; state the convention in the
experiment template/brief so a future experiment inherits it, and keep a directory
per run instead of per experiment so a rerun cannot overwrite the previous
run.

**Priority.** P3 — evidence hygiene; the findings themselves were measured, but
their re-readability depends on notes.

**Close when.** The convention exists as a helper or documented runner rule,
a new experiment brief uses it, and an experiment rerun leaves the earlier run's
raw logs intact and re-readable.

---

## CTL-09 — the Codex compaction retest records no per-run compaction setting and reads no app-server compaction item

**Status:** OPEN (filed 2026-11-15); priority P3. Filed from the compaction-steering
(`ctx-steer`) research review of `docs/design/ctlstat` (merged).

**Problem.** For the Codex harness only the key name and `exec resume` were
established; the compaction event never fired under the tried settings, so its
shape is UNTESTED. Two gaps make a retest hard to compare: the exact compaction
setting used in each attempt was not recorded per run, and the compaction was
looked for only in the `exec --json` stream, although the binary carries a
`contextCompaction` thread item that the app-server exposes.

**Where.** `docs/design/ctlstat/EXPERIMENT-compaction-steering-2026-11.md`
(the X3/X4 sections and the open-items list).

**Wanted.** Retest with a task that really grows the context (tens of thousands
of tokens) and a threshold just below the measured per-request size; record, per
run, the exact compaction setting (key and value) and the harness version; read
the compaction item from the app-server (`contextCompaction` thread item) instead
of inferring it from the `exec --json` event stream; and write the observed
event shape into the document with one captured example.

**Priority.** P3 — an untested claim in a research document, not a shipped
behavior.

**Close when.** The retest records the setting and version per run, an app-server
compaction item is captured and its shape documented, and the document's
UNTESTED marker for that item is replaced by the measurement or by a
declared decision to stop pursuing it.

---

## CTL-10 — worker briefs do not state the measured harness environment limits

**Status:** OPEN (filed 2026-11-15); priority P3. Filed from the compaction-steering
(`ctx-steer`) research review (`docs/design/ctlstat`, merged); the worker-facing
rules bullet landed with this filing, the per-harness re-measurement is open.

**Problem.** Two limits cost worker runs in this devcontainer: a Codex worker
started with a read-only sandbox fails (its nested bubblewrap cannot mount
`/proc`), and the Claude Code Bash tool rejects a long foreground sleep such as
`sleep 40`. Both were rediscovered inside experiment runs because no worker-facing
document listed them; `BRIEF-TEMPLATE.md` section 6 sends a worker to `rules.md`,
and `rules.md` had no environment-limits bullet. These are harness properties,
not properties of the devcontainer as such: in the pi control-plane run of
2026-11-15 a shell-level bubblewrap probe succeeded and a foreground `sleep 40`
was accepted.

**Where.** `scripts/controller/rules.md` (worker-facing binding rules);
`scripts/controller/BRIEF-TEMPLATE.md` section 6 points at it and needs no list of
its own unless the dispatcher, not the worker, must see the limits first.

**Wanted.** State both limits in the worker-facing rules list, each qualified by
the harness it belongs to and the date it was measured, so a worker does not
spend a run rediscovering them; and treat the qualification as the contract —
re-measure per harness instead of generalising a limit to every agent that runs
here.

**Priority.** P3 — repeated avoidable token and time spend per worker run.

**Close when.** The worker-facing rules carry both limits with their harness and
measurement date, and a small per-harness probe (read-only Codex sandbox, Claude
Bash tool, pi Bash tool) records which limits still apply, so a stale limit is
dropped rather than carried forever.

## CTL-11 — a non-Claude controller's readback has no layout until NL-65

**Status:** OPEN (filed 2026-11-15); priority P3. Filed from the readback
layout work of `pi-readback-auto-20261115`, which is Claude Code only by
construction.

**Problem.** `nyxloom-harness extract --preset readback` now emits a three-part
layout (opt-in goal anchor, the newest user/assistant prose of the session, delta)
and ends with a `[readback marker: …]` line. The preset is `claude_only`: its
intent and agent-communication surface, and now its `--since marker` resolution
and goal discovery, are all Claude Code-specific. A controller running on
Codex, opencode or pi therefore falls back to
`extract --profile all --agent-comms full`, which emits no recent-prose tail
and no marker line. That fallback also cannot pass `--tail-prose-tokens` or
`--goal-file` (the CLI refuses the layout knobs without the readback preset),
so a non-Claude controller must record the source marker itself and keep a
spawned session's goal prompt in its own state files.

`scripts/controller/hooks/ctx-hooks.py` inherits the same limit: its
`pre-compact` runs the preset, so it only produces a laid-out readback where a
Claude Code transcript exists. Its marker parsing degrades to "keep the
previous marker" when the extract emits none, which is honest but meaningless
on those harnesses.

**Where.** `nyxloom/src/nyxloom/session_extract/` (the `readback` preset,
`layout.py`, and each adapter's `--since` marker rule);
`nyxloom/docs/session-extract.md`; `scripts/controller/hooks/ctx-hooks.py`;
`docs/design/ctlstat/HARNESS-COMPACTION-CHECKLIST.md` (the rows that name the
fallback).

**Wanted.** Either extend the layout to the Codex, opencode and pi adapters
(NL-65 is the open entry for the weighting half of this), or make the fallback
explicit and mechanical: a documented profile that emits a marker line and a
prose tail without the Claude-only selection classes, so one recipe works on
every harness. Whichever is chosen, the checklist rows must stop describing a
capability that only one harness has.

**Priority.** P3 — the Claude Code controller is the only one currently running
this recipe; a second harness makes it P1.

**Close when.** A readback with a recent-prose tail and a `[readback marker: …]`
line exists for at least the Codex and opencode adapters (fixture-tested, with
the no-duplication property), or the checklist and the hook docs say plainly
which harnesses have no layout and name the substitute the controller uses
instead.
