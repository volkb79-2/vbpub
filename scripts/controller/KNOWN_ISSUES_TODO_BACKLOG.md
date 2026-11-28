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
| CTL-01 | OpenCode and incomplete Codex run-name guards can reuse stale completion or mixed run records | Major | SUPERSEDED by CTL-37 (2026-11-24) |
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
| CTL-12 | The remote-gate helper refreshes its cached `remote-gate.py` through a shared `.new` temp name, so concurrent launches collide before anything reaches the host | P2 | FIXED 2026-11-16 (repository successor `scripts/netcup/runner/remote-gate-cache.sh`) |
| CTL-13 | A final assistant message with `stopReason=stop`, a tool call and no text classifies `SUCCESS` although the documented contract requires text | Minor | FIXED 2026-11-16 |
| CTL-14 | Nothing stops two wrappers from running the same run name (or session) at once; both write the same `.done`, `.events` and retry counter | Should-fix | FIXED 2026-11-16 |
| CTL-15 | `.live`'s `latest_total_tokens` reads `0`: a zero usage report stands in for the measured context size | Should-fix | FIXED 2026-11-16 |
| CTL-16 | `since-commit` shows the branch's last-commit age unqualified, so before the run's first commit it reports the base commit's age | Minor | FIXED 2026-11-16 |
| CTL-17 | `stopReason=length` (the context window filled) is classified `INCOMPLETE`, so the controller cannot tell a truncated run from a broken stream | Should-fix | FIXED 2026-11-16 |
| CTL-18 | `HUNG-TOOL` is decided on wall-clock alone: the sidecar carries no progress evidence, no tool command and no child identity, so a heavy-but-progressing tool (or one blocked by the agent's own FIFO) reads as hung | Should-fix | FIXED 2026-11-17 (2026-11-16 fix reopened in review round 1: it summed the wrapper's whole subtree) |
| CTL-19 | A wrapper that dies without writing `.done` is indistinguishable from a running one in `job-check` (`RUNNING (no .done)`), and `codex-impl.sh ... resume` never writes a marker on a kill | Should-fix | FIXED 2026-11-16 |
| CTL-20 | An epoch the sidecar could not re-read is lifted to the tool's start, so `HUNG-TOOL` is again decided on wall-clock alone | Should-fix | FIXED 2026-11-17 |
| CTL-21 | The progress rule compares an exactly-equal pid set, so any churn (`make -j`, per-test subprocesses) resets the baseline and a silent churning tool reads `LONG-TOOL` indefinitely | Should-fix | FIXED 2026-11-17 |
| CTL-22 | The Codex ownership-descriptor close on the resume and auto-resume exec lines has no individual test or mutant, so deleting it there passes the suite | Should-fix | FIXED 2026-11-17 |
| CTL-23 | The owner record is written before the route, account and credential refusals, so a refused launch overwrites the previous owner's identity and `job-check` reports `DIED` for a name that never started | Should-fix | FIXED 2026-11-17 |
| CTL-24 | The tool-subtree scope for the progress rule is all of the agent's descendants, so a long-lived helper the agent keeps as a child reads as progress (errs toward `LONG-TOOL`) | Known limit | BY DESIGN 2026-11-17 |
| CTL-25 | `job-check`'s read-only lock probe holds an exclusive `flock -n` for about a millisecond, so a claim that lands in that window fails with `already owned by pid <unknown>` (exit 2) | Should-fix | OPEN |
| CTL-26 | `pi-live.py` makes the whole progress reading `None` when one member vanishes between the process scan and its `/proc/<pid>/stat` read, and a churn-only tool under an idle parent reads `HUNG-TOOL` while the row's `steer=` points at the parent | Should-fix | OPEN |
| CTL-27 | `ctl-status.sh` lacks three generic sections the older controller-local copy had: the controller memory/swap/load/PSI line, the "worktrees with work" table (ahead of origin/main, dirty, or committed in the last 24 h) and a `--remote` one-call status of the newest remote-gate run | Nice-to-have | OPEN |
| CTL-28 | `scripts/netcup/runner/remote-gate.py` hardcodes the controller's program directory as `DEFAULT_OUT`, so a direct run without `--out-dir` or `REMOTE_GATE_OUT` writes into an unversioned, controller-local path | Should-fix | OPEN |
| CTL-29 | `remote-gate.sh` repeats `hx.sh`'s guard-wrapping and quoting for its OOM-counter probe instead of calling one shared function, so a change to one can drift from the other | Should-fix | OPEN |
| CTL-30 | `scripts/netcup/runner/remote-gate-hosts.example.json` carries a controller-local home path (`run_gate_bins`) in a committed example, against the no-local-paths rule | Nice-to-have | FIXED 2026-11-18 |
| CTL-31 | `ctl-doctor.sh` reads `controller.local.env` line by line (a multi-line assignment reads as a stray line) and does not run the identity check | Nice-to-have | OPEN |
| CTL-32 | `ctl-state-backup.sh` has no restore helper, and keys/pins are outside the archive by design | Nice-to-have | OPEN |
| CTL-33 | The local gate slots are hardcoded to two (`CONTROLLER_GATE_LOCK`/`CONTROLLER_GATE2_LOCK`); the count should be configurable | Should-fix | DONE 2026-11-18 |
| CTL-37 | A run is ten loose files keyed by a caller name | Must-fix | OPEN 2026-11-24 (Opus design review) |
| CTL-38 | Auto-continued Codex runs lose the final answer | Must-fix | FIXED 2026-11-24 (0c366bf2fa11743281cf4bbcddb0cc981ebba5d1; r1 follow-ups 93fe8963d, 2f52b9793) |
| CTL-39 | Routes in about nine places | Should-fix | OPEN 2026-11-24 (Opus design review) |
| CTL-40 | agctl package; bash frozen | Should-fix | OPEN 2026-11-24 (Opus design review) |
| CTL-41 | ctl-clean treats the symptom | Should-fix | OPEN 2026-11-24 (Opus design review) |
| CTL-42 | Limit note on the launch path | Should-fix | PARTIAL 2026-11-24 (0c366bf2fa11743281cf4bbcddb0cc981ebba5d1; sol + wrapper wording fixed; limits-reader and OD ordering remain OPEN) |
| CTL-43 | Live ctx hook untracked, contradicts AG-HANDOVER-PRIORITY | Must-fix | FIXED-pending-wiring 2026-11-24 (P-plat-05: tracked `hooks/ctx-hooks.py`) |
| CTL-44 | HANDOFF is an append-only log | Should-fix | OPEN 2026-11-24 (Opus design review) |
| CTL-45 | Launcher twins | Should-fix | OPEN 2026-11-24 (Opus design review) |
| CTL-46 | Four readers of run records | Nice-to-have | OPEN 2026-11-24 (Opus design review) |
| CTL-47 | Credential loading differs per launcher | Should-fix | OPEN 2026-11-24 (Opus design review) |
| CTL-48 | remote-gate local worktree | Nice-to-have | OPEN 2026-11-24 (Opus design review) |
| CTL-49 | agctl DESIGN behind the operator | Should-fix | OPEN 2026-11-24 (Opus design review) |
| CTL-50 | Brief house rules | Should-fix | OPEN 2026-11-24 (Opus design review) |
| CTL-51 | Three tree-shipping transports | Nice-to-have | OPEN 2026-11-24 (Opus design review) |
| CTL-52 | Dev container is hostobs-only and admits one job host-wide | Must-fix | FIXED 2026-11-24 (P-plat-02: generic `scripts/controller/dev`) |
| CTL-53 | Worktree sprawl | Should-fix | OPEN 2026-11-24 (Opus design review) |
| CTL-54 | Backlogs and SPECs are append-only | Should-fix | OPEN 2026-11-24 (Opus design review) |
| CTL-55 | `test-pi-impl.sh` `check_live_snapshot` waits a fixed ~10 s of wall clock for the live sidecar, so the suite fails under host load | Should-fix | FIXED 2026-11-24 |
| CTL-56 | P-plat-04 r1 leftovers: locale oracle, interim-message fake, misplaced Pi mutant reference, Unicode continuation digits | Should-fix | OPEN 2026-11-24 |
| CTL-61 | P-plat-21 r1 leftovers: resume-helper prompt limits, test gaps, fail-open input check, live smoke before relying on clear-resume (B2-B13) | Should-fix | OPEN 2026-11-24 |
| CTL-57 | `gate-run.sh` local admission only queues on the slot locks; it starts a gate while the cockpit is at the memory-PSI stop signal | Must-fix | FIXED 2026-11-24 |
| CTL-58 | `push-image.sh` destroys the host's rollback image: `docker load` untags the old `<image:tag>` and no `:prev-*` tag is kept | Should-fix | OPEN 2026-11-24 |
| CTL-59 | P-plat-02 r2 leftovers (dev tool) | Nice-to-have | OPEN 2026-11-24 (Opus r2 review) |
| CTL-60 | P-plat-20 r1 leftovers: fourth-snapshot assertion, never-started sidecar reported as inconclusive, no-PSI host refused with 75, chain-run shows 75 as STOPPED-ON-FAIL | Should-fix | OPEN 2026-11-24 |
| CTL-62 | remote-gate admission holds the admit ticket while waiting, is not PSI-only, and lets an identical live launch through | Must-fix | OPEN 2026-11-24 |
| CTL-63 | `ctl-hygiene.sh` B1 is blind to 6 of the 21 tracked backlogs: `**State:** DONE` files and files without any status line read as all-open, and the `unparsed` report fires only at 0 entries | Nice-to-have | OPEN 2026-11-24 |
| CTL-64 | `ctl-hygiene.sh` reads the backlogs (B1) of the checkout that holds the script, the state of the main checkout: a lagging worktree reports stale B1 numbers next to fresh state numbers | Nice-to-have | OPEN 2026-11-24 |
| CTL-65 | The pi PID namespace guard's same-user-writable assets can be edited by an implementer, changing how later commands launch | Known limit | BY DESIGN 2026-11-27 (H2 ruling) |
| CTL-67 | P-plat-26d review follow-ups: guard test gaps, no live isolation coverage, a process-group kill in a test, a missing mutant file counting as killed | Nice-to-have | OPEN |
| CTL-68 | P-plat-35 review follow-ups: progress lines stop while the mutation queue drains; a missing runner mutant file counts as killed | Nice-to-have | OPEN |
| CTL-69 | Pi automatic compaction summaries grow without bound and omit older in-flight status | Medium | OPEN 2026-11-27 |
| CTL-79 | `pi-ctl-send.sh` has a check-then-use window after its symlink scan | Known limit | BY DESIGN 2026-11-28 (main ruling) |
| CTL-80 | `compact-resume.sh` can deliver `/compact` as pasted text and then send the resume prompt although no compaction ran | High | FIXED 2026-11-28 (P-plat-CTL80 r3) |
| CTL-81 | A fresh pi controller session saves the previous session's monitor-restart notice as its original goal | Medium | FIXED 2026-11-28 (P-plat-CTL80) |
| CTL-82 | An IDE selection chip in an empty Claude Code prompt row is read as typed input | Medium | OPEN 2026-11-28 (main smoke evidence) |
| CTL-85 | `controller.local.env` and launcher-specific route/environment precedence | Known limit | BY DESIGN 2026-11-28 (main decision) |
| CTL-86 | `start_job` prints `job-check.sh` with the requested Codex route, not the effective configured route | Medium | OPEN |

---

## CTL-01 — a reused run name silently consumes stale completion and report records

**Status:** SUPERSEDED by CTL-37 (2026-11-24). Was: OPEN (filed 2026-11-12); severity: major (a caller can report a completed or passing job while the new child is still running or has failed). `codex-impl.sh` now refuses a new run when `.done`, `.jsonl`, or `.err` exists unless `CONTROLLER_RUN_FORCE=1`; the remaining record-set and OpenCode gaps below are open.

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

## CTL-12 — the remote-gate helper refreshes its cached copy through a shared `.new` name, and concurrent launches collide

**Status:** FIXED 2026-11-16 (code, test, mutants and the documents in one
change); priority P2. The controller-local helper (`codex/remote-gate.sh` in the
controller's program directory) is deliberately OUTSIDE the repository, so the
fix landed as the repository-side successor the entry asked for:
`scripts/netcup/runner/remote-gate-cache.sh` with
`scripts/netcup/runner/tests/test_remote_gate_cache.sh`. It stages under a
per-invocation name (`.remote-gate-<blob>.stage.<pid>.<rand>`), publishes by one
atomic `rename` to the content-addressed `remote-gate-<blob>.py`, verifies an
already published copy with `git hash-object` instead of trusting its name,
serialises the refresh with `flock` on `<cache-dir>/.refresh.lock` (bounded by
`REMOTE_GATE_CACHE_LOCK_WAIT`, default 60 s), and keeps three distinct refusals:
`could not refresh the cache` (exit 3), `the tool is absent from ref <ref>`
(exit 4), and an unresolvable ref (exit 5), each naming the ref, the tool path
and the cache directory. Success prints `cache=`, `ref=`, `commit=` and
`identity=blob:<id>` so a caller records the revision that actually ran. Mutants
`remote-gate-cache-inplace-publish`, `remote-gate-cache-name-only-validity`,
`remote-gate-cache-lock-ignored`, `remote-gate-cache-absent-as-refresh` and
`remote-gate-cache-identity-missing` are killed. Docs: `scripts/netcup/CHANGES.md`
[Unreleased], `DESIGN-GUIDE.md` ("Why a cached tool copy is content-addressed"),
`CONSUMERS.md` (a copyable call) and `runner/README.md`. The per-invocation
staging NAME is deliberately not mutated into a shared name: the serialised
refresh makes two stagers mutually exclusive inside one cache directory, so such
a mutant would be equivalent on observable behaviour; the unique name is still
required, and the left-over-staging check covers the property that matters (a
crashed invocation's file is never published by another one).

**Problem.** The helper runs the repository's
`scripts/netcup/runner/remote-gate.py` from a cached copy of `origin/main`,
which it refreshes by writing `<cache>.new` and `mv`-ing it into place. The
`.new` name is shared, not per-invocation, so two launches that start in the
same second race on it: the observed instance was three calls started together,
of which one failed with `mv: cannot move ... remote-gate-main.py.new` and
another with `cannot read remote-gate.py from origin/main`, both exiting 3
before anything was sent to the host (so no host time was lost, but the batch
lost two lanes). A stagger of a few seconds avoids it, which is exactly the
unsafe state: the fix is a human remembering a timing rule nobody documented.

The failure shape is the interesting part. The helper's contract is "run the
current origin/main tool"; the race publishes a cache write failure AS a
"cannot read the tool from origin/main" refusal, so a reader cannot tell a
concurrent refresh from a genuinely missing file. Two invocations disagree
about what they are running, and nothing in the run record names which copy
was used.

**Why this belongs in this repository.** The requirement is that the
capability the helper provides (resolve a ref, take a slot, write a run log and
a commit binding, run the repository tool, collect evidence) is owned by
code inside the repository, where it can be tested and reviewed. The `.new`
race is one symptom of a tool that exists only as a controller-local script.

**What a repository-side successor needs.** (a) A per-invocation unique temp
name plus an atomic rename, so two starts never share a staging file. (b) A
lock (or a per-process staging directory) around the cache refresh itself, so
one invocation's refresh cannot invalidate another's in-flight read. (c) A
distinct refusal for "could not refresh the cache" versus "the tool is absent
from the resolved ref", each naming the ref it tried. (d) The refreshed copy's
identity (resolved commit of the tool file) recorded in the run's own evidence,
so a reader knows which revision ran.

**Where.** No file in this repository today. The successor belongs beside the
runner tooling this repository already owns
(`scripts/netcup/runner/`), with its tests in `scripts/netcup/tests/`; this
entry is the requirement, and it should be closed by that successor plus a test
that starts several refreshes concurrently.

**Acceptance test idea.** N concurrent starts against one cache, each with a
different staging name, all complete and all report the same resolved tool
identity; a deliberately held staging file makes one invocation wait or refuse
BY NAME (refresh failure, not absence); a missing tool at the resolved ref
produces the other refusal. No invocation ever moves another's staging file.

**Priority.** P2 — it costs whole lane launches per batch and the workaround is
undocumented timing.

**Close when.** A repository-owned helper (or an unambiguous documented
successor) provides the same capability with unique staging, a serialised
refresh, two distinct refusals, and the refreshed tool's identity in the run
evidence, and a concurrency test covers it.

## CTL-13 — `stop` with a tool call and no text classifies `SUCCESS`, against the wrapper's own documented contract

**Status:** FIXED 2026-11-16 (code, fixture, test, mutants and the four
documents in one change); severity Minor. The classifier now decides
`TOOL-CALL-NO-TEXT` before either the `EMPTY-COMPLETION` branch or the
`SUCCESS` fall-through; fixture `tests/fixtures/pi-stop-tool-call-no-text.jsonl`;
test `check_outcome_tool_call_no_text` (`tests/test-pi-impl.sh`, focus
`outcome-tool-call`); mutant `pi-tool-call-no-text-success` (killed); docs
`pi-impl.sh` header, `README.md`, `docs/DESIGN-GUIDE.md`,
`docs/CONSUMERS.md`, `docs/design/ctlstat/REQUIREMENTS.md` §15.1 item 5.
The alternative in the entry (declaring a tool call to be completion evidence)
was not taken: the guard's own message claims the run said what it did, and a
truncated or resumed turn that ends on a call is exactly the shape a caller
must not read as done. Found by the independent
review of the overwatch package (`pi-overwatch-REVIEW.md`, finding F7: "not
fixed, noted") and re-verified against the merged code.

**Problem.** The wrapper's contract, stated in `pi-impl.sh`'s own header, the
`docs/DESIGN-GUIDE.md` classification section and the `.done` vocabulary, is
that `SUCCESS` requires the final assistant message to carry text. The decision
order in `classify_run()` gets there differently: it checks
`has_text != 1 && has_tool_call != 1` for `EMPTY-COMPLETION` and then falls
through to `SUCCESS`, so a final message with `stopReason: stop`, a tool call
and NO text is certified successful. That is the same false-certification class
the package exists to remove (a status word asserting a stronger thing than it
checked), just one branch further down.

The measured risk is low for the pi harness as it runs today: real pi streams
emit `toolUse`/`toolCall` parts for tool-calling turns and the review's edge
matrix never produced that shape from real data. That is why the review fixed
nothing and noted it. It is still a defect of the *check*: the guard's message
("SUCCESS means the run said what it did") is stronger than its comparison
("the run either said something or called a tool").

**Minimal solution.** Decide the contract explicitly rather than by fall-through:
either tighten the classifier so a final message with no text can never be
`SUCCESS` (adding a distinct word, because `EMPTY-COMPLETION` is defined as
"no text AND no tool call" and the auto-continue is bound to that class), or
state in the header/guides that a tool call counts as completion evidence and
make the check assert it. Tightening is preferred: a resumed or truncated turn
that ends on a tool call is exactly the shape a caller must not read as done.
A new word must keep the `EXIT=<n> OUTCOME=<word>` first line and must be
additive (every consumer matching `EXIT=0` keeps working), and the harness
documentation for any new class belongs in the same change.

**Where.** `scripts/controller/pi-impl.sh` (`classify_run`, and the header
vocabulary), `scripts/controller/tests/test-pi-impl.sh` (a focused case and one
hand mutant), `scripts/controller/tests/fixtures/` (a new fixture shaped like a
real stream: `message_end` with `content: [{type: toolCall}]`, `stopReason:
stop`), and `scripts/controller/docs/DESIGN-GUIDE.md` plus
`docs/design/ctlstat/REQUIREMENTS.md` §15's closed anomaly vocabulary if the
word set changes.

**Acceptance test idea.** A fixture whose last assistant message holds one
`toolCall` part, no text, `stopReason: stop`, a settled stream and a clean
worktree produces the tightened outcome, and a mutant that reintroduces
`has_tool_call` into the success path makes the focused case fail. A real-text
`SUCCESS` case and the `EMPTY-COMPLETION` case stay green, and the `.done`
first line keeps its shape.

**Priority.** Minor — not reproducible from real pi streams today, but it is a
silent false-positive path in the one function whose job is to prevent those.

**Close when.** The classifier's behaviour and the documented contract agree
(one of them changed with a test that goes red on the old behaviour), the new
or clarified rule has a focused case and a killing mutant, and the vocabulary
documentation is in sync.

## CTL-14 — two wrappers can run the same run name at once and silently share its records

**Status:** FIXED 2026-11-16, extended 2026-11-17 in review round 1 (the Codex
child inherited the lock; see below). Severity Should-fix. Implemented as `lib.sh` `controller_run_claim`/`controller_run_release`/
`controller_close_ownership_fds`/`controller_run_retire_records` plus the claim,
trap and forced-retire path in `pi-impl.sh`; tests `two-wrappers-one-run`,
`ownership-force` and `ownership-release` in `tests/test-pi-impl.sh`; mutants
`pi-run-name-claim`, `pi-run-name-child-close`, `pi-run-name-live-owner-bypass`,
`pi-run-name-retire-deletes`; docs `README.md`, `docs/DESIGN-GUIDE.md`,
`docs/CONSUMERS.md`. Both launch paths close the ownership descriptor before every
exec (`pi-impl.sh` since the first change, `codex-impl.sh` including its
resume/new/autoresume paths since review round 1). The remaining launcher path of
CTL-01 still owes the same guard (OpenCode), tracked there. Found by the same
review (`pi-overwatch-REVIEW.md`, finding F8: "no cross-invocation guard"), and
re-verified: `pi-impl.sh` takes no lock on the run name.

**Problem.** Everything a run owns is keyed by its NAME: `<run>.done`,
`<run>.events`, `<run>.jsonl`, `<run>.err`, `<run>.last.txt`, `<run>.thread`,
`<run>.live`, and the retry counter, which lives in the invocation itself. Two
wrappers started for the same run name — a manual relaunch while a background
launch is still alive, or two schedulers racing — therefore interleave on the
same files with no error: the second truncates `.events` (`: > "$out/$name.events"`
on a new run), both append to the same stream, the pid in `.live` is whichever
wrote last, and each writes its own `.done`. A reader (`.done` or
`job-status.sh`) then sees a completion from one invocation while the other is
still working, which is precisely the "stale completion read as the new job's
result" class the backlog already tracks across launchers (CTL-01) — here for
pi, and with the additional hazard that the account bookkeeping (`block-run`)
is also name-scoped.

The review's own assessment is that this is a pre-existing property of the
launcher, bounded per invocation, and that a pid-file lock is "a separate
package". This entry is that package's requirement.

**Minimal solution.** One ownership guard, in the shared library so every pi
launcher path (new run, `resume`, auto-continue) gets it: take an exclusive
lock keyed on the run name (the kit's `gate-run.sh`/queue already use `flock`
and `O_NOFOLLOW`-style discipline; `lib.sh` is where a reusable helper
belongs), write the owning pid + start-time into the lock's own record, and
refuse a second invocation BY NAME, naming the live owner and the supported
ways forward (wait, a different run name, or an explicit force that first
retires the previous owner's records). A dead owner's lock must be recoverable
(the same recycled-pid/start-time conjunction the review asked for in the
other direction), and the guard must not strand a lock when the wrapper exits
by `TERM`/`KILL` or on an error path. `resume` of the SAME session keeps
working: it is a different invocation of an owned run, so it takes the same
lock rather than a second dimension.

**Where.** `scripts/controller/lib.sh` (shared lock helper),
`scripts/controller/pi-impl.sh` (acquire before anything writes, release on
every exit path including the trap), `scripts/controller/tests/test-pi-impl.sh`
(a two-invocation case), and `scripts/controller/README.md` /
`docs/CONSUMERS.md` for the refusal's wording and the force path.

**What landed (2026-11-16).** The lock is `<runs-dir>/<name>.lock`, an
exclusive kernel `flock`, and the lock file itself is the owner record
(`pid=`, `started_epoch=`, `started_utc=`, `pid_start_ticks=`, `boot_id=`),
so a reader can name a dead owner as well as a live one (CTL-19 consumes it).
The wrapper claims before it writes any name-owned record; the trap releases
on success, on an error path and on SIGTERM, and SIGKILL is covered by the
kernel because the agent and the sidecar close their inherited copy of the
descriptor (`controller_close_ownership_fds`). `CONTROLLER_RUN_FORCE=1` is
refused while an owner is alive; for a dead owner it RETIRES the whole record
set into `retired/<run>/<utc-stamp>/` with the previous owner written into
`PREVIOUS-OWNER.txt`. The new-run evidence preflight now covers the full
record set (`done`, `jsonl`, `err`, `events`, `live`, `last.txt`, `wt`,
`route`, `thread`, `autoresume.log`).

**Acceptance test idea.** A first fake run holds the lock; a second invocation
with the same name refuses with exit 2 before truncating anything, naming the
owner and the run's records; a different run name proceeds concurrently; killing
the first wrapper (-TERM and -KILL cases) leaves the run name usable again;
`resume` of the same session while the same run is live refuses rather than
interleaves. Mutants: removing the acquire, acquiring after the `.events`
truncate, and dropping the release on the error path are each killed.

**Priority.** Should-fix — the failure mode reports a completion that belongs to
another process, and the estate's rule about narrowed checks applies: a
completion check that reads a name-shared file certifies one job with another
job's evidence.

**Close when.** Two invocations of one run name can never interleave (each
refusal names the owner), every exit path releases the lock, a dead owner is
recoverable safely, and the two-invocation, resume and kill cases pass with
killing mutants.

## CTL-15 — `.live` reports `latest_total_tokens: 0` for the long runs it exists to describe

**Status:** FIXED 2026-11-16 (code, fixtures, tests, mutants and the documents
in one change); severity Should-fix. Evidence framing below corrected in review
round 1 the same day. The zero was OBSERVED in live `job-status`
output during the 2026-11-16 controller work: a run at 568 assistant turns and
619 tool calls published `"latest_total_tokens": 0`, while a neighbouring run of
the same day published `258492`. No capture of that stream was kept, so the
observing run cannot be re-read — which is itself part of the fix asked for
below.

**Problem.** `pi-live.py`'s incremental reader keeps the LAST integer
`usage.totalTokens` it saw, from either shape, with no test on whether that
value is a measurement or a placeholder (`pi-live.py:110-116`: `message_end`
usage and `message_update` usage both assign `self.total_tokens`
unconditionally). So any event that reports `0` overwrites the real context
figure, and the sidecar then publishes `0` for a run that is large.

**What the repository's own fixtures do and do not show.** They do not show the
placeholder premise. `tests/fixtures/pi-usage-only.jsonl` and
`pi-empty-usage.jsonl` both carry a `message_update` whose
`assistantMessageEvent.type` is `thinking_delta` and whose
`usage.totalTokens` is **101** — a streaming delta with a NON-ZERO total, which
is the opposite of "every delta event reports 0". What the fixtures DO show is
that the unconditional assignment accepts a `0` wherever it appears:
`pi-402.jsonl`, `pi-402-unknown.jsonl` and `pi-error-other.jsonl` each carry a
`message_end` with `totalTokens: 0`, and `pi-live.py` would publish that 0 as the
latest figure (the existing checks assert exactly that behaviour for the delta
fixtures: `check_live_usage_trend` requires `latest_total_tokens == 129` from
`pi-empty-usage.jsonl`, and the `live-usage-trend` case requires `101` from
`pi-usage-only.jsonl` — both figures come from a `thinking_delta`).

So two statements are separable and only the first is established: (a) the code
publishes whatever integer arrived last, including a 0 that means "not
measured" [code-verified above]; (b) STREAMING deltas in particular always carry
0 [NOT established by any captured stream, and contradicted by the two fixtures
that carry 101 on a delta]. Statement (b) was asserted in the first version of
this entry as fact; it is now a hypothesis to be measured, not a premise to fix
against.

**Consequence, established either way.** A status line that looks measured and
is wrong: the value is not `null` (which the design says an unreadable fact must
be), it is a false 0, which reads as "the context is empty" on a run that may be
at 200k. The token figure is also the input to the restart-vs-continue decision
the ctlstat requirements call for (`docs/design/ctlstat/REQUIREMENTS.md` §15
items 4 and 6), so a 0 there is worse than an absent value.

**Minimal solution.** Do not accept a usage record that is structurally a
placeholder as the latest measurement: keep the last NON-ZERO reading (or
distinguish "reported 0" from "reported" and prefer the last completion event's
usage), and keep `null` for "never read one" so the distinction the design
requires survives. Because 0 is also a legitimate value for a fresh stream
before the model reports anything, the rule must be stated. The safest
mechanism-independent form is the second one — carry BOTH the last non-zero
figure and a "last report was 0" fact — because it does not depend on the
placeholder hypothesis above being true; a rule keyed on the event's own
`assistantMessageEvent.type` is only justified once a captured stream shows that
only placeholder deltas report 0. Record the chosen semantics in the
`pi-live.py` docstring, `docs/DESIGN-GUIDE.md`'s liveness section, and
`job-status.sh`'s header.

**Where.** `scripts/controller/pi-live.py` (`Snapshot.consume` and `payload`),
`scripts/controller/job-status.sh` (the printed field's label if the semantics
become two facts), `scripts/controller/tests/fixtures/` (a fixture capturing
which events report 0, built from a real long run — plus, if the capture
differs from it, a synthetic case where a real completion is followed by
zero-valued reports), `scripts/controller/tests/test-pi-impl.sh` (the focused
case plus a mutant), `scripts/controller/tests/fixtures/README.md` (the captured
stream's shape), and the three documents named above.

**Acceptance test idea.** TWO things must be asserted, one of each kind.
Behaviour: a fixture where a real completion with a non-zero `totalTokens` is
followed by events reporting `0` must publish the non-zero figure (or publish
both facts, with the zero labelled as a zero report), never a bare 0 that reads
as the context size; a stream with NO usage publishes `null`, not 0; a stream
whose only usage is a genuine 0 states that. A mutant that keeps the last usage
record unconditionally must fail the first case. Evidence: the rule about WHICH
events report 0 must be established from a captured stream, so the acceptance
work includes capturing a sanitized fixture from a real long pi run (the shape
that produced the observed 0) and recording in the fixture README whether a
delta carries 0 or a real total — because the two delta fixtures already in the
repository carry 101 and 129, which is why the placeholder hypothesis is not
assumed here.

**Priority.** Should-fix — a wrong number in the only live read of a run, on the
runs the tool was built for.

**Close when.** A captured, sanitized real stream establishes which events report
0 and which carry a real total (its shape recorded in the fixtures README, since
the two delta fixtures already in the repository carry 101 and 129); the sidecar
distinguishes "no usage read yet" (`null`), "a report arrived saying 0", and a
real total, and never publishes a placeholder 0 as a context figure; the focused
cases and the mutant behave as described; and the three documents state the
semantics.

## CTL-16 — `since-commit` shows the branch's last-commit age with no qualification, so before the run's first commit it reports somebody else's commit

**Status:** FIXED 2026-11-16 (code, tests, mutants and the documents in one
change); severity Minor. The first version of this entry was rewritten in review
round 1 because it claimed the payload cannot tell whether the run committed,
which is false. Observed in live `job-status` output on 2026-11-16
(several runs printed a `since-commit=…s` figure immediately after starting) and
re-verified against `scripts/controller/pi-live.py` and
`scripts/controller/job-status.sh` on origin/main.

**Problem.** `job-status.sh` prints `since-commit={age}s` from
`payload["last_commit_epoch"]` (`job-status.sh:125`), and `pi-live.py` fills that
field from `git log -1 --format=%ct HEAD` (`pi-live.py:141,167`). For a run
started on an existing branch — the normal case, where the worktree is created
from `main` and its tip IS the base commit — the value is the BASE commit's age,
possibly days or weeks, from the very first tick. It is not an arithmetic error
(the field is recomputed every tick, so a commit the run makes does move it); it
is a fact without provenance: nothing in the row, and nothing in the field name,
says whose commit that age belongs to, so a reader asks "how long since this run
last committed" and is answered about somebody else's commit — and cannot
tell "this run has not committed yet" from "this run committed long ago".

**What the payload already has, and why that makes the fix cheap.**
`pi-live.py` publishes `commits_ahead` (`rev-list --count <base>..HEAD`,)
(`pi-live.py:137,163`) and `job-status.sh` prints it as `ahead=` on the SAME
line as `since-commit=` (`job-status.sh:123,125`). So the qualifier the reader
needs is already present one field away: `ahead=0` means the run has no commit
of its own and the age describes the base commit. The first version of this
entry asserted that the information "did this run commit at all" was not in
the payload; that was wrong (and the `commits_ahead` field is the correction).

**Minimal solution.** Qualify or suppress the figure, using the fact already
published: when `commits_ahead` is 0, print `since-commit=no-commits-yet`
instead of an age (or, if the reader needs a duration, print the age since the RUN
started, with its own label and source), and print the commit age only when
`commits_ahead` is a positive count — at which point the age really is this
run's last commit. Keep the `ahead=` field as authority for the distinction and
make `since-commit` cite it in the row (for example `since-commit=…s(ahead=1)`
versus `since-commit=no-commits-yet(ahead=0)`), so a reader cannot see one
without the other. `commits_ahead` that cannot be read is `null` (the existing
rule) and then `since-commit` must print `null`, never a bare age: an unreadable
qualifier must not silently become a licence to show the number. Update the
`job-status.sh` header line, which today describes the field as "seconds since
the last commit" without naming whose.

**Where.** `scripts/controller/job-status.sh` (the row and its header line),
and `scripts/controller/pi-live.py` only if a run-start epoch is added (the
`commits_ahead` fact needed for the distinction is already published, so no
new git call is required for the primary fix);
`scripts/controller/tests/test-pi-impl.sh` plus the fixtures under
`scripts/controller/tests/fixtures/` (a run-shaped case with a base commit older
than the run and no commit of its own); and the liveness paragraph of
`docs/DESIGN-GUIDE.md` / `scripts/controller/docs/DESIGN-GUIDE.md` if it names
the field.

**Acceptance test idea.** A fixture run whose base commit is days old and which
has not committed (`commits_ahead` = 0): the printed row shows no bare
`since-commit` age — it shows `no-commits-yet` (or a clearly labelled
since-run-start age) — and it shows that beside `ahead=0`, never an unqualified
age. After one commit made by the fake agent (`commits_ahead` = 1), the field
names that commit's age, qualified by `ahead=1`. A fixture whose
`commits_ahead` is `null` (git cannot answer) prints `null` for `since-commit`
too, so an unreadable qualifier cannot license an unqualified number. A mutant
that prints the age whenever `last_commit_epoch` is readable fails the first
case.

**Priority.** Minor — it misleads a controller reading a status line, and it is
cheap to record honestly; it costs nothing on the lane path.

**Close when.** A run with `ahead=0` never prints an unqualified `since-commit`
age, a run with `ahead>0` prints its own last commit's age with that qualifier
visible, an unreadable `commits_ahead` suppresses the age (`null`), both fixtures
pass, and the reader/guide text says which commit the age belongs to.

## CTL-17 — a run stopped by `stopReason=length` (context window full) reads as `INCOMPLETE`, which hides the one class the controller acts on

**Status:** FIXED 2026-11-16 (code, fixture, tests, mutants and the five
documents in one change); severity Should-fix. `classify_run` decides
`CONTEXT-FULL` after the settling check and BEFORE the generic
`stopReason != stop` branch; the retry loop still matches only
`EMPTY-COMPLETION`. The existing `pi-length` fixture now yields
`EXIT=0 OUTCOME=CONTEXT-FULL` (`check_outcome_length`, focus `outcome-length`),
and `tests/fixtures/pi-settled-other-stop.jsonl` pins that every OTHER
unattributable stop stays `INCOMPLETE` (`check_outcome_stop_other`, focus
`outcome-stop-other`). Mutants `pi-context-full-missing`, `pi-context-full-order`
and `pi-context-full-continued` are killed. Docs: the wrapper header,
`README.md`, `docs/DESIGN-GUIDE.md`, `docs/CONSUMERS.md` (the successor action)
and `docs/design/ctlstat/REQUIREMENTS.md` §15.1 item 5. Filed from the
controller's own live experience of long pi runs and from the operator decision
of 2026-11-16 recorded in the controller state (classify `stopReason=length` as
`CONTEXT-FULL` so the overwatch can start a successor). Verified against the
merged classifier: `classify_run()` sends every `stopReason != stop` to
`INCOMPLETE`.

**Problem.** `INCOMPLETE` is documented as "a settled or unreadable stream that
is not a stop" — a catch-all for states the controller cannot attribute. A run
whose final `stopReason` is `length` is not unattributable: the harness stopped
the model because the context window filled, the work is intact, the session can
be continued or succeeded by a fresh session seeded from the state file, and the
controller's restart decision is exactly what it exists to make. Folded into
`INCOMPLETE` it is indistinguishable from a broken/absent stream, from a run
that never settled, and from an empty thread; the three have different remedies
(continue the session, restart from a brief, investigate the harness), and the
closed anomaly vocabulary in `docs/design/ctlstat/REQUIREMENTS.md` §15 does not
contain the class at all, so ctlstat cannot report it either. The current
gate test pins the collapse (`check_outcome_length` expects
`EXIT=0 OUTCOME=INCOMPLETE` for the `pi-length` fixture), so this is a deliberate
decision made once, not an oversight — but it is a decision made before the
overwatch needed the distinction, and it now costs the distinction.

**Minimal solution.** Add `CONTEXT-FULL` to the outcome vocabulary for a settled
run whose final `stopReason` is `length`, decided BEFORE the generic
`stopReason != stop` branch, and (per the operator decision) treat it as the
class where the controller starts a successor from the forward-oriented brief
rather than auto-continuing: the wrapper itself must NOT continue it
(`EMPTY-COMPLETION` remains the only continued class, because continuing a
full context window is the one continuation that cannot succeed). Keep the
`.done` first line's shape, keep the exit status as pi's own, extend
`docs/design/ctlstat/REQUIREMENTS.md` §15's closed vocabulary with the new word
(and with the successor-start requirement it implies), state the distinction in
the wrapper header and DESIGN-GUIDE, and update the existing
`check_outcome_length` expectation in the same change — that is a behavioural
change to a documented contract, so the documents are part of the same commit.

**Where.** `scripts/controller/pi-impl.sh` (`classify_run`, header vocabulary),
`scripts/controller/tests/test-pi-impl.sh` (`check_outcome_length` plus a
killing mutant), `scripts/controller/tests/fixtures/pi-length.jsonl` (fixture
already exists and is right), `scripts/controller/docs/DESIGN-GUIDE.md`,
`scripts/controller/docs/CONSUMERS.md`, `scripts/controller/README.md`, and
`docs/design/ctlstat/REQUIREMENTS.md` §15 (anomaly vocabulary + the
successor-start rule).

**Acceptance test idea.** The `pi-length` fixture produces
`EXIT=0 OUTCOME=CONTEXT-FULL`; the retry loop does not continue it (no
`empty-completion-retry` event appears); a `stopReason=error`/`aborted`/unsettled
stream still produces `ERROR`/`INCOMPLETE`; `EXIT=0` consumers still see the
first line unchanged. Mutants: removing the new branch, placing it after the
generic branch, and letting the retry loop catch it are each killed.

**Priority.** Should-fix — it is a real, common end state of the long runs this
tooling exists for, and today it costs a controller the decision input.

**Close when.** The classifier, the closed anomaly vocabulary, the four
documents above and the retry rule agree, the length fixture yields
`CONTEXT-FULL`, the retry loop provably ignores it, and the mutants are killed.

## CTL-18 — `HUNG-TOOL` is decided on wall-clock alone, and the row cannot show what the tool is or what to kill

**Status:** FIXED 2026-11-17 after being REOPENED in review round 1. The
2026-11-16 fix added the facts (`progress_epoch`, the tool command, the child
pids) but summed the WRAPPER's whole subtree, which also holds the sidecar and
the agent: both grow every tick, so a blocked tool still read as working and
`HUNG-TOOL` was unreachable - the accepted change did not implement the entry's
own acceptance case. The round-1 change sums only the in-flight tool call's
subtree, defines progress as a strictly positive delta on the same process set,
keeps an unreadable `/proc` unknown, and is judged by checks that drive a fake
`/proc` on a tick clock. Original filing follows. Filed from the controller's
own overwatch experience of 2026-11-16 (a slow `grep` over a large
tree, an agent's own blocking experiment, and a long `pytest` all read as
`HUNG-TOOL` while they were working) and verified against the merged code.

**Problem.** `job-status.sh` derives `HUNG-TOOL` from one quantity: the age of
`tool_in_flight_started_epoch` past `JOB_STATUS_HUNG_TOOL_SECONDS`
(`job-status.sh:...`, "elif tool_seconds is not None and tool_seconds > hung_limit").
The sidecar publishes nothing that says whether the tool is still doing
anything: `pi-live.py` counts `tool_execution_start`/`tool_execution_end` and
publishes the tool's NAME and start epoch only. It does not read
`tool_execution_update` (whose `partialResult` is the tool's own output growth),
it does not read the tool's `args` (so the row cannot say `command: git diff`),
and it does not name the process doing the work (so the only steering action
available to a controller reading that row is to kill the wrapper, which the
estate forbids). The result is a word (`HUNG-TOOL`) that asserts a stronger
thing (the tool is stuck) than what was checked (the tool has been running for
N seconds), which is the same false-certification shape the estate's
"a check is only as strong as what it actually compares" rule names.

Two separable conditions collapse into that one word:

1. a tool that is heavy but progressing (a slow `grep`, a long `pytest`, a
   build): the right answer is `ACTIVE`/`LONG-TOOL` with the age visible, not
   `HUNG-TOOL`;
2. a tool blocked by the agent's own experiment (a `git` command waiting on a
   FIFO the agent created, a read of a file nothing writes): no progress
   evidence of that kind is available, and a human has to decide, so the row
   must show the tool's COMMAND and AGE and the exact child pid to steer.

**Minimal solution.** Publish evidence of progress and identity, and let the
reader decide on it:

- `pi-live.py` keeps `tool_progress_epoch`: the last tick at which the run
  showed progress, derived from observable growth only (bytes appended to the
  run's event stream, or the CPU and IO counters of the wrapper's own process
  subtree from `/proc/<pid>/stat` fields 14/15 and `/proc/<pid>/io`
  `read_bytes`/`write_bytes`). A fact that cannot be read stays `null`.
- The sidecar also publishes `tool_in_flight_command` (the tool's own
  `args.command`, bounded) and `tool_process_tree` (the wrapper's live
  descendants as `[pid, short command line]`, bounded), so the row can name the
  work and the controller can kill the exact child pid, never the wrapper.
- `job-status.sh` splits the word: `HUNG-TOOL` only when the tool is past the
  threshold AND its progress age is past the threshold too; a tool past the
  threshold WITH recent progress is `LONG-TOOL`. Both print the command, the
  tool age, the progress age and the child pids.
- The steering rule is documented where the state word is defined: kill the
  exact child pid shown in the row, never the wrapper (a wrapper kill discards
  the run's classification and its `.done` marker).

**Where.** `scripts/controller/pi-live.py` (`Snapshot.consume` for
`tool_execution_update` and `tool_execution_start.args`, `payload`, the
`/proc` progress probe); `scripts/controller/job-status.sh` (the state decision
and the row); `scripts/controller/tests/test-pi-impl.sh` (a progressing tool and
a genuinely stuck one, both synthetic, plus a real-fixture sidecar case);
`scripts/controller/tests/fixtures/` (a `tool_execution_update`-carrying stream,
from the captured `/tmp` run in the CTL-15 work); `README.md` and
`docs/DESIGN-GUIDE.md`/`docs/CONSUMERS.md` (the states list and the steering
action); `docs/design/ctlstat/REQUIREMENTS.md` §15 item 2 (progress is a
separate column from liveness, which this entry now makes true in the kit's
read side).

**Acceptance test idea.** A sidecar fixture with a tool age past the threshold
and a progress age inside it prints `LONG-TOOL` (never `HUNG-TOOL`) and names
the tool's command; one with both ages past the threshold prints `HUNG-TOOL`
and names the command and the child pids; a stream that only gained
`tool_execution_update` lines keeps the progress age fresh; a `/proc` that
cannot be read leaves the progress fact `null` and does not invent progress.
Mutants: `HUNG-TOOL` decided on the tool age alone; `LONG-TOOL` never produced;
the progress age not refreshed by tool output; the command and pids dropped
from the row.

**Priority.** Should-fix — it is the word that makes a controller kill a
working job, and the row cannot name what to do instead.

**Close when.** `HUNG-TOOL` requires absent progress evidence as well as age,
a progressing tool reads `LONG-TOOL` with its command, the row shows the
command and child pids, the steering rule is in the state-word documentation,
and the focused cases and mutants behave as described.

## CTL-19 — a wrapper that died without `.done` reads as a running job, and the Codex resume writes no marker at all

**Status:** FIXED 2026-11-16 (code, tests and docs in the same change);
severity Should-fix. Filed from the
controller's own experience of 2026-11-16 (a Luna `codex-impl.sh ... resume`
that was killed mid-run left no `.done`, so every later `job-check` said
`RUNNING (no .done)` and the job looked active for hours) and verified against
the merged code: `codex-impl.sh` writes `<run>.done` only on its normal exit
path, and `job-check.sh` prints `RUNNING (no .done)` and exits 0 whenever the
marker is absent, without any evidence that a process still exists.

**Problem.** `job-check.sh`'s no-marker branch is `echo "RUNNING (no .done)";
exit 0`. The word `RUNNING` asserts a stronger thing than it checked: it did not
look at any process, at any sidecar, or at any lock record. A wrapper killed by
`TERM`/`KILL`, an OOM kill, or a container restart therefore reads as a live
job forever, and the controller's only other signal for that run
(`job-status.sh`) requires a `.live` sidecar that the Codex wrapper never
writes at all. The narrower claim ("no completion marker was found") is mixed
with the stronger one ("the job is running") in the same word, which is exactly
the collapse the estate's comparison rule forbids.

The Codex half is worse in one respect: `codex-impl.sh` writes `.done` on its
normal path, and a resume that is killed between the state call and the marker
leaves the name with a stream, an `.err` and no completion fact; the caller
then cannot distinguish "still working" from "died", and the automatic
continuation (`CODEX_AUTO_CONTINUE`) has its own loop that can exit through a
`break` with a stale `rc`.

**Minimal solution.** Give every wrapper the same name-owned liveness record
it already has to take for CTL-14 (`<runs-dir>/<name>.lock`: owner pid, start
epoch, the pid's kernel start ticks, boot id), keep it on every exit path, and
make the readers use it:

- `codex-impl.sh` claims the run name (the same `lib.sh` helper as
  `pi-impl.sh`), releases it on every exit path, and writes the marker in the
already-documented shape with an outcome word: `EXIT=<n> OUTCOME=<word>
AUTORESUME=<k>` (additive to the existing `EXIT=`/`AUTORESUME=` first-line
  contract).
- `job-check.sh` reads `<run>.lock`: pid gone (or the recorded kernel start
ticks no longer match that pid) AND no `.done` is `DIED (wrapper pid <pid> gone,
  no .done)` and a red flag, never `RUNNING`; a live recorded owner keeps
  `RUNNING`; no record at all keeps the prototype's `RUNNING (no .done)` with
  exit 0, and the line says that no owner record exists.

**Where.** `scripts/controller/lib.sh` (`controller_run_claim`,
`controller_pid_start_ticks`, `controller_run_owner_pid`);
`scripts/controller/codex-impl.sh` (claim, trap, `.done` with `OUTCOME`);
`scripts/controller/job-check.sh` (the no-marker branch and its pid/start-time
check); `scripts/controller/tests/test-job-check.sh` (a dead wrapper, a live
wrapper, and no record) and `scripts/controller/tests/test-wrappers.sh` (a
Codex run and a Codex resume marker); `scripts/controller/tests/test-mutations.sh`
and `tests/mutate-sources.py` (mutants); `README.md` and
`docs/DESIGN-GUIDE.md`/`docs/CONSUMERS.md` (the `DIED` word and the
`.done`/lock-record contract).

**Acceptance test idea.** A run with a lock record whose pid is gone and no
`.done` prints `DIED` and a red flag; the same record with a live pid prints
`RUNNING`; a record whose pid exists but whose kernel start ticks differ (a
recycled pid) is `DIED`, not `RUNNING`; no record at all keeps `RUNNING` with
exit 0 and says so. The Codex wrapper's normal exit and its resumed invocation
both write `EXIT=<n> OUTCOME=<word>AUTORESUME=<k>`. Mutants: the pid test
replaced by `true`; the start-ticks comparison dropped; the marker not written
on the resume path; the lock released before the child exits.

**Priority.** Should-fix — a dead job that reads as running stops the
controller from relaunching it, which is the same class of hour-cost the other
entries in this file describe.

**Close when.** `.done` is written on every wrapper exit path (including
Codex resume), `job-check` reports a dead owner without a marker as `DIED` and
keeps a live owner and an unrecorded run distinct, the focused cases pass, and
the documents state the lock record and the `DIED` word.

## CTL-20 — an epoch the sidecar could not re-read is read as measured progress, so `HUNG-TOOL` is decided on the tool's age again

**Status:** FIXED 2026-11-17 (code, fixtures, tests, mutants and the three
documents in one change); severity Should-fix. Found by the independent review
of the CTL-18 package (`pi-controller-burn-REVIEW.md`) and re-verified against
the merged code: `pi-live.py`'s `payload()` lifted an epoch older than the
in-flight tool up to the tool's start, and `job-status.sh` read that lifted
epoch as measured progress.

**Problem.** `Progress.observe` correctly refuses to treat an unread tick as
progress, and correctly leaves `progress_epoch` null when the tool's subtree
cannot be located (`progress_reason=unread`). But `progress_epoch` is not the
only fact the reader uses: `payload()` bounded it with

```python
if tool_started is not None and progress_epoch is not None:
    if progress_epoch < tool_started:
        progress_epoch = tool_started
```

which is a bound on a MEASURED fact only in appearance. The run's own stream
bytes (a `session` event, a turn start, a tool's own end) are progress evidence
for the RUN, and they are appended before the next tool call starts. So the
sequence "the stream grew, then a tool started, then the tool's subtree became
unreadable" publishes `progress_epoch == tool_started` while nothing about the
tool was ever read. `job-status.sh` then computes `progress = age(tool_started)`
— the tool's own age — and the only remaining test is
`tool_seconds > hung_limit`, so `HUNG-TOOL` is decided on wall-clock alone: the
exact defect CTL-18 exists to remove, in its third form (the first was a null
clamped up to the start, which the earlier fix removed; the second was the
wrapper's whole subtree being summed).

The existing `progress-unknown` case could not see it. Its fixture's stream
bytes ARE the tool's `tool_execution_start`, so the progress epoch and the tool
start are the same instant and a lifted epoch is indistinguishable from a
measured one.

**What separates the two facts.** *Which tick the epoch came from* is one fact;
*whether that tick measured the CURRENT tool* is another. A reader needs both,
so `pi-live.py` now publishes `progress_measured` beside `progress_epoch`:

- true only when the current tick read the in-flight tool's own subtree, or
  evidence was produced this tick (stream growth, the tool's own output);
- false when the tick could not read it (`progress_reason == "unread"`, the
  previous epoch carried forward as a leftover) or when the epoch is older than
  the in-flight call (the epoch is then published as `null`, never lifted).

`job-status.sh` takes the flag as the licence and nothing else: an unmeasured
epoch is unknown, the row prints `progress=-` with its reason, and the word is
`LONG-TOOL`. A payload that predates the field is unknown for the same reason —
absence of a measurement is not a measurement.

**Where.** `scripts/controller/pi-live.py` (`Progress.measured`, `payload`),
`scripts/controller/job-status.sh` (the row and the state decision),
`scripts/controller/tests/test-pi-impl.sh` (cases (5), (5b) and the status
fixture), `scripts/controller/tests/mutate-sources.py` +
`tests/test-mutations.sh` (hand mutants), `README.md`,
`docs/DESIGN-GUIDE.md`, `docs/CONSUMERS.md`.

**Acceptance test idea (as landed).** A fake `/proc` with no agent subtree at
all, driven on a tick clock, where the stream grew BEFORE the tool started:
tick 1 is a real measurement of the run, every later tick carries the epoch
unmeasured, the snapshot publishes `progress_epoch: null` with
`progress_measured: false`, and the row built from that payload is `LONG-TOOL`
with `progress=- progress-why=unread` although the tool is past the hung
threshold. Mutants `pi-live-progress-lift-unmeasured`,
`pi-live-progress-measured-kept`, `pi-live-progress-null-clamped`,
`job-status-progress-unmeasured-as-measured` and
`job-status-progress-absent-flag-as-measured` are each killed by that case.

**Close when.** An epoch the sidecar could not re-read can never reach the
`HUNG-TOOL` decision as a progress age, the producer and the reader agree on
the two facts, the focused case fails on the old behaviour (and on each
mutant), and the three documents state the distinction.

## CTL-21 — any churn of the process set resets the progress baseline, so a silent churning tool never reads `HUNG-TOOL`

**Status:** FIXED 2026-11-17 (code, tests, mutants and the three documents in one
change); severity Should-fix. Found by the independent review of the CTL-18
package (`pi-controller-burn-REVIEW.md`) and re-verified against the merged code.

**Problem.** `Progress.observe` accepted a counter reading as comparable only
when `previous.identity == sample.identity` — an EXACTLY EQUAL pid set — and any
other reading replaced the stored sample and left the baseline where it was. That
is right for a tool whose set is stable, and wrong for the tools the fact exists
for: `make -j` forks and reaps workers continuously, `pytest -n` and per-test
subprocesses do the same, and a shell pipeline's children come and go. Every such
tick was a set change, so a tool that was silent but churning could hold
`LONG-TOOL` indefinitely: the absence-of-progress evidence the word `HUNG-TOOL`
needs was never established, and the controller waited instead of acting. The
rule was stated over the set ("the SAME set of processes"), which cannot express
"these members are the same process and did not move while the set around them
churned".

**The rule, stated precisely.** Compare MEMBERS, not the set:

* a member is a `(pid, kernel start ticks)` identity, so a recycled pid number is
  a different process;
* a SURVIVOR is a member present in BOTH readings with the same identity. Only a
  survivor's strictly positive CPU/IO delta is progress;
* a set change alone (members added or removed) is NOT progress and does NOT
  reset the quiet clock: the baseline is the tick the CURRENT tool call's
  counters were first read, and it stays there for that call;
* the consequences follow directly: churn with idle survivors is quiet (so
  `HUNG-TOOL` at the threshold), churn with a CPU-burning survivor is progress
  (`LONG-TOOL`), and a pid recycled between two ticks is not a survivor at all.

**Where.** `scripts/controller/pi-live.py` (`Sample` per-member counters,
`survivor_delta`, `read_counters`, `Progress.observe`),
`scripts/controller/tests/test-pi-impl.sh` (cases 4b-i, 4b-ii, 4b-iii and the
`progress-churn` row), `scripts/controller/tests/mutate-sources.py` +
`tests/test-mutations.sh` (mutants), `README.md`,
`docs/DESIGN-GUIDE.md`, `docs/CONSUMERS.md`, `CHANGES.md`.

**Acceptance test idea (as landed).** On a fake `/proc` and a tick clock: (i)
three ticks of churn (a worker added and reaped each tick) around two idle
survivors produce no progress, `measured` true on every tick, and the baseline
identical on every row — and the row built from that payload is `HUNG-TOOL` at
the threshold; (ii) the same churn with a survivor whose CPU grew is progress at
that tick (`reason=counters`); (iii) a pid whose start ticks changed between two
ticks is not a survivor and its larger counter is not progress. Mutants
`pi-live-progress-sum-delta`, `pi-live-progress-churn-resets-baseline` and
`pi-live-progress-pid-recycle` are each killed by these cases.

**Close when.** A silently churning tool reaches `HUNG-TOOL` at the threshold, a
churning tool with a working survivor stays `LONG-TOOL`, a recycled pid is never
a survivor, and the documents state the rule as members rather than as a set.

## CTL-22 — the Codex resume and auto-resume exec sites close the ownership descriptor without an individual test or mutant

**Status:** FIXED 2026-11-17 (tests, mutants and the documents in one change);
severity Should-fix. Found by the independent review of the CTL-14/CTL-19
package (`pi-controller-burn-REVIEW.md`) and re-verified against the merged
code.

**Problem.** `controller_close_ownership_fds` is what keeps a run name claimable
after a wrapper is killed: the flock belongs to the wrapper's open file
description, a forked child inherits it, and a Codex agent can outlive its
wrapper by hours. `codex-impl.sh` calls it at THREE exec sites — the new launch,
the manual `resume`, and the automatic continuation loop — but the evidence was
one shared-function mutant (a `lib.sh` variant where `controller_close_ownership_fds`
is a no-op, judged by the SIGKILL case for the NEW-LAUNCH path only). Deleting
the call from the `resume` line, or from the auto-resume line, passed the whole
suite: the lib mutant is still present at the other sites, so a killed resume
never happened in the check and its close was never exercised. The resume path
is the ROUTINE one — every follow-up of a long job is a resume — so the single
least-used site was the only one actually judged.

A shared-function mutant cannot fix this: it makes ALL sites lose the close at
once, which one case detects. The defect is a missing INDIVIDUAL assertion per
call site, so the fix is a per-site case plus a per-site mutant.

**What landed.** `tests/test-wrappers.sh` now kills a wrapper for real (SIGKILL)
at each site and asserts the same facts at each: the agent's own process is
still alive, the setsid daemon it left behind is still alive, the wrapper's wait
status is 137 (a real SIGKILL), the run name is free to an OUTSIDE `flock -n`
reader, and the relaunch is accepted (no "already owned by pid" refusal; the
new-launch and auto-resume cases relaunch with `CONTROLLER_RUN_FORCE=1`, the
resume case needs none). The fake agent gained deterministic hooks:
a per-CALL hold (`FAKE_CODEX_HOLD_ON_CALL`, so the FIRST call finishes and the
auto-resume loop is actually reached before the wrapper is killed) and a
reasoning-ending stream (`FAKE_CODEX_REASONING`, the stream shape the
continuation is defined on), and the held agent publishes its own pid
(`FAKE_CODEX_HOLD_PID`) — no sleep decides any case (the hold loop is released
by a file the check creates and is capped so a failed check leaves no process
for more than five minutes).

Mutants `codex-newlaunch-child-close`, `codex-resume-child-close` and
`codex-autoresume-child-close` delete the call from ONE exec line each; the
shared-function `codex-run-name-child-close` stays as the guard for the library
function itself. All four are killed, each by the case of its own site.

**Where.** `scripts/controller/tests/test-wrappers.sh` (the three SIGKILL cases
and the fake agent's hooks), `scripts/controller/tests/mutate-sources.py` +
`tests/test-mutations.sh` (the three per-site mutants), `docs/DESIGN-GUIDE.md`
("Every exec site, not just the first one"), `CHANGES.md`.

**Acceptance test idea (as landed).** For the resume site: start a resume,
wait until the fake agent's own pid and its daemon pid are published (the hold
file is written by the held call itself), SIGKILL the wrapper, `flock -n` the
lock from outside, then relaunch with `CONTROLLER_RUN_FORCE=1` and assert the
refusal never fires and the relaunched invocation writes its own `.done` (no
force is needed for a resume: the evidence preflight is a new-run rule). The
auto-resume site is the same, with `CODEX_AUTO_CONTINUE` left at its DeepSeek
default and the hold on call 2 so the continuation is the child that is killed.

**Close when.** A killed wrapper at each of the three exec sites leaves its run
name claimable while the agent and daemon it started still live, and a mutant
that removes the close from that one line makes its own case fail.

## CTL-23 — the owner record is written before the refusals that can still follow the claim

**Status:** FIXED 2026-11-17 (code, tests, mutants and documents in one change);
severity Should-fix. Found by the independent review of the CTL-14/CTL-19
package and re-verified against the merged code.

**Problem.** The claim takes the run name's lock and a launch then writes its
identity into the lock file (`controller_run_record_owner`). `codex-impl.sh` and
`pi-impl.sh` wrote it right after the evidence preflight, BEFORE the route
validation, the continuation-count check, the Open Design account selection
(blocked accounts exit 3) and, in `pi-impl.sh`, the local credential file and
key checks. A launch refused at any of those points had already overwritten the
previous owner's identity with its own pid, which then died: `job-check` read
that record as `DIED (wrapper pid <refuser> gone, no .done)` for a name that
never started, and the real previous owner's identity (what a forced retire
archives, what a reader names) was lost. `pi-impl.sh` also rewrote `.wt`,
`.route` and `.thread` and removed the previous `.done` before its credential
checks, so a refused resume destroyed the previous completion marker, and a
refused first launch left evidence that refused its own retry.

**What landed.** The record is written only once the launch is real:
`codex-impl.sh` writes it after the account selection, immediately before its
first name-owned record; `pi-impl.sh` writes it after the credential and key
checks and moves the `.wt`, `.route`, `.thread` and `.done` handling behind the
same point, in the same relative order as before. A refused launch leaves the
previous records byte-identical, and a refused launch of a fresh name leaves
only the empty lock file the claim created. `job-check.sh` reads that file as
its own state instead of the misleading `RUNNING`: an empty lock with no stream
that nobody holds is `NOT-STARTED` (red, exit 1, tells the controller to read
the launch's own refusal and relaunch); the same empty lock while a wrapper
holds it is that wrapper still preflighting and stays `RUNNING`; an unparsable
lock or an empty one beside a stream keeps the no-owner-record wording.

**Tests and mutants.** `tests/test-wrappers.sh` refuses Codex launches at the
route, the count and the blocked-accounts checks (and a resume, and a fresh
name) and compares the lock byte for byte; `tests/test-pi-impl.sh`
(`refusal-keeps-records`) does the same for the unknown route, blocked
accounts, missing credential file and missing key and also checks the previous
`.done`, `.wt`, `.route` and `.thread`; `tests/test-job-check.sh` covers the
four lock states. Mutants write the record just before each refusal
(`codex-record-owner-early`, `-before-count`, `-before-accounts`,
`pi-run-name-refusal-overwrites`, `pi-record-owner-before-accounts`,
`pi-record-owner-before-credentials`), restore the `.done` removal and the
`.route` write ahead of the credential checks (`pi-done-removed-before-credentials`,
`pi-route-written-before-credentials`) and weaken the `NOT-STARTED` reading
(`job-check-not-started-off`, `-held`, `-stream`, `-content`); each dies on its
own case.

**Review follow-up (same entry, landed 2026-11-17 after the independent review).**
Four refinements, each with its own test and mutant:

- A HELD lock whose record still names a dead previous owner (a `DIED` name being
  resumed, now in the preflight window before its own record) was read as `DIED`.
  `job-check.sh` probes the lock whenever the recorded owner is dead and prints
  `RUNNING (preflighting, previous owner gone)` when a wrapper holds it; the same
  record with a free lock is still `DIED`. Mutants `job-check-died-held-lock`,
  `job-check-died-no-probe`.
- An ACCEPTED pi resume is pinned from the other side: while its agent works the
  lock names the wrapper's own pid, the previous `.done` is gone and `.thread` is
  the resumed session (`accepted-resume-records`). Mutants
  `pi-resume-block-inverted`, `pi-record-owner-new-only`.
- `CONTROLLER_RUN_FORCE=1` no longer retires the previous records before the
  refusals: the evidence preflight only decides that a retirement is wanted, and
  the retirement runs with the owner record once the launch is real, in both
  wrappers. A forced launch refused at the route, the accounts or the
  credentials moves nothing into `retired/` and leaves the owner record. Mutants
  `codex-force-retire-early`, `pi-force-retire-early`,
  `pi-force-retire-before-credentials`.
- The `lib.sh` header says the lock file records who STARTED the name last, not
  who claimed it.

**Where.** `codex-impl.sh`, `pi-impl.sh`, `job-check.sh`, the three test files and
`tests/mutate-sources.py` / `tests/test-mutations.sh`, `README.md`,
`docs/CONSUMERS.md`, `docs/DESIGN-GUIDE.md`, `CHANGES.md`.

**Close when.** A launch refused after the claim leaves the previous owner
record, completion marker and worktree/route/thread records byte-identical, a
refused fresh name reads `NOT-STARTED` and never `DIED`, and each mutant above
fails its case.

## CTL-24 — the tool-subtree scope is every descendant of the agent

**Status:** BY DESIGN 2026-11-17 (documented, no code); severity: known limit.

**Note.** The progress rule counts the CPU/IO deltas of the in-flight tool's
subtree, and the sidecar locates that subtree structurally: the agent is the
wrapper's other direct child and ALL the agent's descendants are in the set. It
cannot tell which descendant belongs to which tool call, so a long-lived helper
the agent keeps as a child (a language server, a watcher, a dev server) is in the
set and, while it works, reads as progress. That errs toward `LONG-TOOL`, the
weaker word: the controller waits longer on a tool that is really stuck and never
kills one that is working, which is the safe direction. Narrowing the set needs
the agent's own call-to-pid mapping, which the stream does not carry. The scope
is stated in `docs/DESIGN-GUIDE.md` ("Scope of the tool's subtree").

**Revisit when.** The agent stream carries the pid or process group of each tool
call, or a stuck tool behind a busy helper is seen in practice.

## CTL-25 — the job-check lock probe can make a concurrent claim fail

**Status:** OPEN (filed 2026-11-17 by the CTL-23 review, not fixed); severity
Should-fix, low probability.

**Problem.** `job-check.sh` decides `NOT-STARTED` versus a wrapper that is still
preflighting (and, since the CTL-23 review follow-up, `DIED` versus a resume that
is preflighting) by opening the run's lock file read-only and taking
`flock -n`. A non-blocking exclusive lock held for the instant of the probe (about
a millisecond) makes a wrapper's `controller_run_claim` that lands in that window
fail its own `flock -n` and exit 2 with `run name '<name>' is already owned by pid
<unknown>`: a read-only status check can refuse a real launch. The window is tiny
and the launch can simply be repeated, but it is a reader changing what a writer
sees.

**Fix direction.** Read `/proc/locks` (a held lock appears there with the
holder's pid and the file's inode) instead of taking the lock; a shared lock
would not help, because a shared holder also makes an exclusive `flock -n` fail.
If the `/proc/locks` read is not wanted, document the window as a known limit in
the job-check section of the design guide.

**Close when.** A status read cannot make a claim fail, with a test that holds the
probe open across a claim, or the limit is documented as by design.

## CTL-26 — one vanished member voids the progress reading; churn under an idle parent points steering at the parent

**Status:** OPEN (filed 2026-11-17 by the CTL-23 review, not fixed); severity
Should-fix for the first part, by-design note for the second.

**Problem 1.** In `pi-live.py` (around the per-member `/proc/<pid>/stat` read
of the progress sums) a member that exits between the process scan and its stat
read makes the whole reading `None`. A churning tool (`make -j`, per-test
subprocesses) makes that race common, so the reading is voided exactly when
churn is the question, and the reader takes the weaker word for that tick. The
fix is to skip the vanished member and keep the survivors' readings; a member
that disappears is a set change, which the rule already says is not progress.

**Problem 2 (by design, to document).** A tool that only churns children while
its own parent is idle makes no survivor progress, so it reads `HUNG-TOOL` at the
threshold while the row's `steer=` prints the idle parent: a working `make -j`
can be pointed at for killing. The rule deliberately does not count churn as
progress (CTL-21), and the sidecar cannot tell churn that works from churn that
spins. The mitigation is the row text, which names the command and the pid so the
controller can look before acting.

**Close when.** A vanished member no longer voids the reading (test with a member
that exits between scan and read, mutant that restores the whole-reading `None`),
and the by-design note is in the design guide next to the CTL-24 scope note.

## CTL-27 — ctl-status lost three generic sections in the rewrite

**Status:** OPEN (filed 2026-11-18 by the controller-import inventory; not fixed).

The controller-local copy of `ctl-status.sh` printed (a) one line of the
controller cgroup's anon/swap/max memory, load and memory PSI, (b) a table of
worktrees with work (ahead of `origin/main`, dirty, or last commit under 24 h),
and (c) with `--remote`, one identity-checked status call for the newest
remote-gate run. The repo rewrite is lib.sh-driven and deliberately read-only
(no fetch, SSH or Docker), and has none of them. (a) and (b) are generic and
cheap and need `tests/test-ctl-status.sh` cases with a fake root; (c) needs the
remote-gate result layout and should come with its own test, or be dropped.

**Close when.** (a) and (b) are in with tests and mutants, and (c) is either
implemented with a test or recorded as not wanted.

## CTL-28 — remote-gate.py defaults its output directory to a controller-local path

**Status:** OPEN (filed 2026-11-18 by the controller-import inventory; not fixed,
`remote-gate.py` was out of scope for that work).

`DEFAULT_OUT` in `scripts/netcup/runner/remote-gate.py` is an absolute path inside
the unversioned program directory. `remote-gate.sh` always passes `--out-dir`, but
a direct invocation silently writes there. The default should come from
`REMOTE_GATE_OUT` or `$CONTROLLER_HOME/remote-gates` and refuse when neither is
resolvable.

**Close when.** The runner has no controller-local literal and its tests show the
refusal.

## CTL-29 — one shared host-command function for hx.sh and remote-gate.sh

**Status:** OPEN (filed 2026-11-18 by the controller-import; not fixed).

`hx.sh` runs a command on a host through the table's `ssh` argv, the `guard`
text under `set -e` and `shlex.quote`. `remote-gate.sh` needs the same wrapping
for its OOM-counter probe against its own host table and repeats it
(`remote_probe`). Extract `controller_host_run <host> <command> [hosts-file]`
into `lib.sh`, make `hx.sh` and `remote-gate.sh` call it, and keep the existing
`hx.sh` tests and mutants (the identity-check and guard-failure mutants move with
the code).

**Close when.** Both callers use one function, with the hx and remote-gate tests
green and a mutant for the guard wrapper killed in both.

## CTL-30 — a committed example carries a controller-local home path

**Status:** FIXED 2026-11-18 (DONE in the controller-import review round; filed the same day by the controller-import hygiene grep).

`scripts/netcup/runner/remote-gate-hosts.example.json` listed `run_gate_bins`
under a home-directory path of the controller account. It now carries
placeholder paths. The same literal also sat in the comments of
`scripts/netcup/runner/runner.env.example` (the `RUN_GATE_BIN` lines: a default path and a venv
path under the controller account's home); those two comment lines now read
`<cockpit home>/.local/bin/run-gate` and `/path/to/venv/bin/run-gate` (package
pi-ctl33; no netcup test reads those comments: `test_runner_host_endpoints.py` only
scans the file for endpoint-path and GID literals, `test_runner_scripts.py` and
`test_cow_select.py` read variable names and `COW_DEVICE`).

**Close when.** Done: a `git grep` for the controller account's home directory name over `scripts/netcup/runner/remote-gate-hosts.example.json` and `scripts/netcup/runner/runner.env.example` prints nothing. Other files (docs, evidence, `scripts/netcup/runner/README.md`) still name controller-local paths in prose and are outside this entry.

## CTL-31 — ctl-doctor reads controller.local.env line by line and does not run the identity check

**Status:** OPEN (filed 2026-11-18 by the import's doctor work; by design for now).

`ctl-doctor.sh` flags (and stops before sourcing) every line of
`controller.local.env` that is not a `NAME=value` assignment, so a legitimate
multi-line assignment reads as a stray line (an assignment with a command
substitution passes the filter and runs when `lib.sh` sources the file), and it only checks that each host's `api_check` command is an
executable file: it does not run it (the identity check talks to the provider). A
`--live` option could run `api_check` per alias and report the exit code only.

**Close when.** Either the env-file scan understands quoted multi-line values and
`--live` exists with a test and a mutant, or the README keeps the limit stated.

## CTL-32 — no restore helper for ctl-state-backup, and keys/pins are outside it by design

**Status:** OPEN (filed 2026-11-18 by the import's backup work; not fixed).

`ctl-state-backup.sh` writes `repo/`, `home/` and `runs/` members and the operator
restores by hand (`tar -xf`, then copy). SSH keys and pin files are deliberately
not saved. A `--restore <archive> <dir>` that extracts into an empty directory and
prints the copy commands (never overwriting) would remove the manual step; keys and
pins stay a documented operator duty (`docs/STATE-VS-WORK.md`).

**Close when.** `--restore` exists with a test for the no-overwrite refusal, or the
by-design note stays in `docs/STATE-VS-WORK.md`.

## CTL-33 — the local gate slot count is hardcoded to two

**Status:** DONE 2026-11-18 (package pi-ctl33; filed the same day in the controller-import review round).

`gate-run.sh` knew exactly two local slots, `CONTROLLER_GATE_LOCK` and
`CONTROLLER_GATE2_LOCK`. `CONTROLLER_GATE_SLOTS` (an integer from 1 to 8, default 2)
now sets the count: slot 1 and 2 keep their variable names, slots 3.. are
`gate<N>.lock` in `CONTROLLER_GATE_LOCK_DIR` or beside `CONTROLLER_GATE_LOCK`
(`lib.sh` `controller_gate_derived_lock`; the exact rule is in the README). Unset
behaves and prints exactly as before. `ctl-doctor.sh` prints the effective count and
checks every slot lock; `chain-run.sh` needed no code change (it only calls
`gate-run.sh`), its documentation names the count. Tests: `tests/test-gate-slots.sh`
(N = 1, 2, 3, 4 and 8) and the doctor test; hand mutants for both.

Reasoning kept: the DEFAULT stays 2 because the local slots guard the controller's
own devcontainer, which has an 8 GiB memory cap and was OOM-killed once when too
much ran at the same time. The remote hosts already run four weight-based slots
(`CONTROLLER_REMOTE_SLOTS`, a different mechanism), and the fleet daemon's RAM-first
admission is meant to replace static slot counts altogether; this change covers the
interim only.

**Close when.** Done: `git grep -n CONTROLLER_GATE_SLOTS -- scripts/controller` shows
`gate-run.sh`, `ctl-doctor.sh`, the docs and the tests. Open question for the fleet
daemon: retire the static count when its admission exists.

## CTL-37 — a run is ten loose files keyed by a caller name

**Status:** OPEN (filed 2026-11-24).

Generated id, a synchronous `RUN <id>` before `--detach`, `runs/<id>/run.json` plus stream and `last.txt`, one runs dir. *Supersedes CTL-01.*

Source: .controller-state/design-review-2026-11-24/controller-tooling.md H1

## CTL-38 — auto-continued Codex runs lose the final answer

**Status:** FIXED 2026-11-24 (commit
`0c366bf2fa11743281cf4bbcddb0cc981ebba5d1`; filed from the same-day design
review).

A new Codex run used `-o` for `.last.txt`, but manual `resume` and automatic
continuation did not. A reasoning-only first turn therefore left either no final
text or the previous invocation's text, including after the continuation produced
the actual final answer.

`codex-impl.sh` now has one `codex_extract_last_text` function used after the new,
manual-resume and each auto-continue child exits. It takes the last completed
`agent_message` item from that child's JSON stream, replaces `.last.txt`
atomically, and removes an older file when the stream has no final text. The new
run no longer passes `-o`; the child exit and `.done` classification are unchanged.

`check_ctl38_last_text_all_paths` covers `FINAL-A` to `FINAL-B` on resume across a
reasoning-only first turn, automatic continuation, and stale-file removal for a
no-text stream. The wrapper suite also asserts a positive new-run extraction.
A hand mutant that removed the resume extraction was killed by the focused gate
test (`CTL-38 resume did not derive exactly FINAL-B from its stream`).

Review r1 closed two remaining gaps. Commit `93fe8963d` removes the old
`.done` and `.last.txt` before a resume launch and extracts from the current
stream on the EXIT/signal path; its focused test holds a resumed child, verifies
the old text is gone, recreates it once, and then SIGTERMs the wrapper.
Commit `2f52b9793` extracts from this invocation's accumulated `$name.jsonl`
after an auto-continuation, so a later empty attempt does not erase text already
produced by attempt 1.

**Close when.** Done: all Codex exec paths derive `.last.txt` from their own
stream, and the no-text and stale-file cases are tested.

Source: .controller-state/design-review-2026-11-24/controller-tooling.md H1

## CTL-39 — routes in about nine places

**Status:** OPEN (filed 2026-11-24).

`routes.toml` plus a local overlay, read by the launchers, job-check, the limits reader and the docs; no effort override.

Source: .controller-state/design-review-2026-11-24/controller-tooling.md H2

## CTL-40 — agctl package; bash frozen

**Status:** OPEN (filed 2026-11-24).

Stdlib package on cli-extended (`runs`, `routes`, `streams`, `config`). Port opendesign-accounts and ctl-limits first; skills via `register_skills_verbs`.

Source: .controller-state/design-review-2026-11-24/controller-tooling.md H3

## CTL-41 — ctl-clean treats the symptom

**Status:** OPEN (filed 2026-11-24).

Drop the branch; RG-294/295 plus the assay p23 prune; one-off cleanup after OK.

Source: .controller-state/design-review-2026-11-24/controller-tooling.md H4

## CTL-42 — limit note on the launch path

**Status:** PARTIAL 2026-11-24 (commit
`0c366bf2fa11743281cf4bbcddb0cc981ebba5d1`; sol and wrapper wording fixed,
limits-reader and Open Design ordering remain open); filed from the same-day
design review.

The unmerged `ctl-routes-limits-20261118` branch was reviewed as two separable
changes. Its useful route correction was re-applied by hand: `codex-impl.sh`
accepts `sol` with the independent review home, Luna defaults to `$HOME/.codex2`,
and `CODEX_HOME_OVERRIDE` wins for Luna and explicit `CODEX_ROUTE=sol` Codex
launches. `codex-review.sh` deliberately chooses `CODEX_HOME_REVIEW`, which wins
for reviews. The env example, config/consumer docs, and AGENTS.md wording now
name the wrapper rather than a Codex home.

The branch's launch-path limits note, standalone limits reader, reader test, and
detached reader fix were not carried. Tests `route-home-luna`, `route-home-sol`,
both override cases, and the review-route assertion exercise the salvaged
behavior. A hand mutant restoring the Pi Open Design default from `max` to `high`
was killed by the focused gate test.

Remaining open work: make the read-only limits reader consume the planned
`routes.toml` and run registry and cover Pi routes, and make Open Design
`blocked_until` an ordering hint rather than a refusal based on stale local state.

**Close when.** The limits reader has the new sources and Pi coverage, and the
Open Design picker treats local blocking as ordering with the provider's 402 as
authority.

Source: .controller-state/design-review-2026-11-24/controller-tooling.md H5

## CTL-43 — live ctx hook untracked, contradicts AG-HANDOVER-PRIORITY

**Status:** FIXED-pending-wiring 2026-11-24 (package P-plat-05): `scripts/controller/hooks/ctx-hooks.py` is a drop-in for the untracked `.controller-state/hooks/ctx.py`. Remaining controller steps: the operator swaps the five hook paths in `.claude/settings.local.json` (path only, mode words unchanged); `.controller-state/hooks/ctx.py` is deleted after one clean day. AGENTS.md "Context pressure ladder" and the controller-handover skill step 7 now name `ctx-hooks.py` (review r1 fix). Swapping drops the live `state-*.json` cadence state, so each live session gets one extra note at its current step.

Wire `hooks/ctx-hooks.py` and delete the copy. Ceiling per model; past the soft threshold, handover, READY, end of turn.

Source: .controller-state/design-review-2026-11-24/controller-tooling.md H6

## CTL-44 — HANDOFF is an append-only log

**Status:** OPEN (filed 2026-11-24).

One current file plus an archive, with a generated header. `plan.yaml` gets one `state` key and a schema check; PROGRESS entries move out.

Source: .controller-state/design-review-2026-11-24/controller-tooling.md H7

## CTL-45 — launcher twins

**Status:** OPEN (filed 2026-11-24).

`agctl run --route` with adapters and a shared classifier; the old names become shims.

Source: .controller-state/design-review-2026-11-24/controller-tooling.md M1

## CTL-46 — four readers of run records

**Status:** OPEN (filed 2026-11-24).

`agctl status`/`check`; `check` also flags direct hx.sh/docker/pytest use in implementer streams.

Source: .controller-state/design-review-2026-11-24/controller-tooling.md M2

## CTL-47 — credential loading differs per launcher

**Status:** OPEN (filed 2026-11-24).

Load only the route's `key_var`.

Source: .controller-state/design-review-2026-11-24/controller-tooling.md M3

## CTL-48 — remote-gate local worktree

**Status:** OPEN (filed 2026-11-24).

`--commit <sha>` bundles from the repo; drop `CONTROLLER_REMOTE_GATE_WORKROOT`.

Source: .controller-state/design-review-2026-11-24/controller-tooling.md M4

## CTL-49 — agctl DESIGN behind the operator

**Status:** OPEN (filed 2026-11-24).

Rev 4: slice 1 = registry, handover cycle and hook; finalize/prime second; §8a reduced to one threshold. *Absorbs informal CTL-36, drops the CTL-34 `.env.<name>` rename, folds CTL-35 into run dirs.*

Source: .controller-state/design-review-2026-11-24/controller-tooling.md M6

## CTL-50 — brief house rules

**Status:** OPEN (filed 2026-11-24).

Compile and test via the remote dev container before each commit; contract plus target size; at most 2 review rounds; explicit launch effort until CTL-39; unique run names; resumed runs verified from git until CTL-38.

Source: .controller-state/design-review-2026-11-24/hostobs.md F4; SYNTHESIS.md T14

## CTL-51 — three tree-shipping transports

**Status:** OPEN (filed 2026-11-24).

One shared host runner, home fleetd (`fleet host …` agent + client) for remote-gate, the dev tool, fleetd and the dr runner; carries `.assay/r2-store/<lane>/`.

Source: .controller-state/design-review-2026-11-24/fleetd.md H4; SYNTHESIS.md T11

## CTL-52 — dev container is hostobs-only and admits one job host-wide

**Status:** FIXED 2026-11-24 (package P-plat-02; guide `scripts/controller/docs/DEV.md`).

Generic `scripts/controller/dev` with Python and Rust profiles (`scripts/controller/dev-profiles.toml`). Admission is host-local and memory-PSI-only (host-admission/1: memory `full` avg10 >= 3 waits, bounded by `AGENT_DEV_WAIT_S`, then exit 75); RAM overcommit (finite MemoryMax, large swap) is by design. `gc`, a NOT-EVIDENCE banner, and the hostobs shim.

Source: .controller-state/design-review-2026-11-24/hostobs.md F4; operator 2026-11-24

## CTL-53 — worktree sprawl

**Status:** OPEN (filed 2026-11-24).

43 merged worktrees plus 2 rgw worktrees. One-off removal by exact path after dirty and ignored checks; later `agctl worktrees prune`.

Source: .controller-state/PROGRAM-2026-11-24/PROGRAM.md §5

## CTL-54 — backlogs and SPECs are append-only

**Status:** OPEN (filed 2026-11-24).

`docs/BACKLOG-FORMAT.md` plus `agctl backlog index|check`. Pilot on scripts/controller and mdt-cockpit.

Source: .controller-state/PROGRAM-2026-11-24/PROGRAM.md §6

## CTL-55 — the live-sidecar test waits a fixed wall-clock time

**Status:** FIXED (2026-11-24, P-plat-20: `check_live_snapshot` waits on sidecar progress; the 300 s hang guard is reported as inconclusive).

`tests/test-pi-impl.sh` `check_live_snapshot` polls 100 × `sleep 0.1` (about 10 s of wall clock) for `"tool_in_flight": "bash"` in the `.live` sidecar, which is written every `PI_LIVE_INTERVAL_SECONDS=1` by a process tree started under the same load. On 2026-11-24 the `suite` lane at 0314fdf1b (a docs-only change) failed with `the live sidecar never reported the tool call in flight` while another session's mutation lane loaded the host (load average ~6 on 8 cores). Against the estate rule that verdicts do not depend on host load. Fix: wait on a progress condition instead of a clock, for example until the fake pi's own "tool started" marker exists AND the sidecar has completed at least two write cycles (a sidecar sequence counter), with only a generous outer bound (minutes) that reports "inconclusive under load" separately from "never reported".

Source: suite lane log of 2026-11-24 (run-gate lane `suite`, head 0314fdf1b).

## CTL-56 — P-plat-04 r1 leftovers

**Status:** OPEN (filed 2026-11-24).

- Finding 4: the locale test does not discriminate on glibc ≥2.28 or the
  tester-unified `C.UTF-8` locale; the explicit ASCII digit list is correct, but
  the oracle needs a locale where the old range admits `٣`, or a direct
  validator-list check.
- The CTL-38 fake resume stream has no interim `agent_message` before
  `FINAL-B`, so a first-message-wins extractor could still pass.
- CTL-42's text still cites the Pi `max` mutant, which belongs to the launcher
  effort change rather than the route-salvage item.
- `CODEX_AUTO_CONTINUE` is ASCII-checked in shell and then passed to Python
  `int()`, which accepts Unicode digits; reject those explicitly at the wrapper
  boundary.

Source: P-plat-04 r1 Opus review findings 4 and 9, explicitly deferred by the
controller.

## CTL-61 — P-plat-21 r1 leftovers (resume helpers)

**Status:** OPEN (filed 2026-11-24 from the Opus r1 and r2 reviews `.controller-state/platform/reviews/plat-21-r1-opus.md`, `plat-21-r2-opus.md`; F1-F3, R1 and R2 fixed).

Not blocking, for `compact-resume.sh`, `clear-resume.sh`, `resume-lib.sh` and their tests:
- **B2 prompt limits undocumented:** keep prompt and guidance short (the tmux client refuses a command line over about 16 KiB, "command too long"), no trailing `;` (tmux drops it as a command separator), no leading `/` or `!` (Claude Code treats them as commands) and no trailing `@mention` (Enter accepts the completion). Document in AGENTS.md; consider refusing such input with exit 2.
- **B3 test gaps:** a compact scenario where the spinner appears only after 3 or more idle polls (pins `START_TRIES` and the start-wait loop), a frame with the `Compacting` line 5-6 lines up (pins `FOOT_LINES=6`), and the stub recording the `capture-pane` target (a wrong `-t` survives today).
- **B4 defaults unpinned:** no test pins poll 2 s, idle 900, clear 30, start 60, done 1800 (surviving mutants `IDLE_TRIES 900->5`, `CLEAR_TRIES 30->1`, `poll 2->0.01`, `START_TRIES 60->2`, `$idle_tries` used in place of `$done_tries` in the done wait).
- **B5 missing lib:** `source resume-lib.sh` failing under `set -u` ends as exit 3 "never idle" with nothing sent; use `|| exit 2`.
- **B6 broad shellcheck disables:** the file-wide `disable=SC1091,SC2034,SC2154` in both helpers hides real unused/unassigned variables; run `shellcheck -x` in `tests/run-suite.sh` and narrow them.
- **B7 DESIGN 6a:** a compaction is not a "fresh session" (step 3 framing); the SessionStart[compact] note of section 6.4 ("the handover predates it") will false-alarm on every deliberate compact; `agctl successor <pane> <prompt-file> [--clear]` needs a guidance argument for the default compact mode.
- **B8 self-compaction example** uses the relative `scripts/controller/...` and `>/dev/null 2>&1`: launched from the wrong cwd it fails invisibly after the session has ended its turn. Give an absolute or tools-relative path (the controller tools run from `.worktrees/controller-tools`) and log to a file.
- **B9 fail-open input check:** a pane with NO `❯` row counts as ready (the cleared fixtures depend on it), so a permission or selection menu with an indented cursor could receive our Enter. Measure the real empty row (`❯` plus NBSP) and treat "no input row" as not ready.
- **B10 live smoke before relying on clear-resume again:** neither helper's empty-input check has been observed on a live pane. a plain `capture-pane -p` drops attributes, so dim suggestion text in an empty input box read as typed text (all five live helpers hung on 2026-11-24); the check now captures with `-e` and drops dim runs (stub-tested only). Other dim placeholders after `/clear` or a reverse-video cursor cell are unmeasured. One operator-watched run of EACH helper settles it.
- **B11 type/Enter race:** text the operator types between the third ready check and the send, or during the `type_pause` before Enter, merges into ours; inherent to polling, document it.
- **B12 first-line-empty input:** an input whose first line is empty with text only on continuation lines (Shift+Enter first) reads as empty.
- **B13 timeout classification:** `wait_idle` reports 3 or 5 from the LAST poll only, so a pane that had text for 29 min and was busy at the final poll reports 3 (cosmetic).

Source: Opus r1 review of P-plat-21 at head 4b9563cbd and r2 review at head b2162f3f1.
## CTL-57 — gate-run.sh starts local gates at the memory-PSI stop signal

**Status:** FIXED (2026-11-24, P-plat-20: `gate-run.sh` waits for memory PSI `full` avg10 < 3 after taking a slot, bounded by `CONTROLLER_GATE_PSI_WAIT_S`; exit 75 "not admitted, nothing ran", unreadable PSI exits 75 at once).

**Update 2026-11-27 (operator via main):** the threshold is now memory PSI `full` avg60 >= 10; avg10 spikes are ignored. Still open: one rule for `gate-run.sh`, `dev` host-admission/1 (`full` avg10 >= 3) and `remote-gate.py` (memory `some` / io `full` avg10 > 25); see CTL-62, whose avg10 >= 3 wording predates this decision. The text below records the original defect.

`gate-run.sh` admitted a local gate as soon as one of its slot locks was free. At 07:50Z on 2026-11-24 the cockpit host ran 4 local gates at memory PSI `full` avg10 3-6, above the programme's only stop signal (>= 3). Sessions can be told to stop, but external codex implementers that call `gate-run.sh` mid-run cannot. Same semantics as host-admission/1 of `scripts/controller/dev` (CTL-52, package P-plat-02), so one rule covers both.

Source: vbpub-main-controller, 2026-11-24 07:50Z (cockpit PSI 3-6 with 4 local gates).

## CTL-58 — push-image.sh destroys the host's rollback image

**Status:** OPEN (filed 2026-11-24, routed from vbpub-core / T2 via the main controller).

`scripts/controller/push-image.sh <host> <image:tag>` streams the image and runs `docker load` on the host. When the tag already exists there, docker untags the old image ("The image tester-unified:local already exists, renaming the old one with ID sha256:04ad9c4010f4… to empty string"), and the host keeps no `:prev-*` tag, so a host-side rollback is impossible without re-pushing the old image from the cockpit. Seen 2026-11-24 pushing tester-unified:local = dcaf4222c68d to v1001 and r1002; on the cockpit, `tester-unified/promote-image.sh` kept `tester-unified:prev-20261124b`, the hosts have no such tag.

Wanted: before `docker load`, retag the host's current `<image:tag>` as `<image>:prev-<date>`, never overwriting an existing prev tag (the same rule as `tester-unified/promote-image.sh`); print the kept image id. Test at a fake docker boundary (current tag present / absent / prev tag already present).

Interim rollback path: push `tester-unified:prev-20261124b` from the cockpit.

## CTL-59 — P-plat-02 r2 leftovers (dev tool)

**Status:** OPEN (filed 2026-11-24, from the Opus r2 review of `scripts/controller/dev`, bound to aaa56b27e).

Six small items, none of which blocks the tool:

1. A signal that arrives before the cidfile is written orphans the container: `cleanup` finds no cid, and the background `timeout`/docker client goes on to create and run it with nobody waiting. Bounded by `max_s`; the next call exits 3 "container exists, use dev stop". Fix: with an empty cid, `docker rm -f "$NAME"` (safe under the lock after the exists check) and/or kill the background job.
2. A few host steps still fail with errexit's code 1 or 2 and no `dev: exit 3` line: `rm -rf "$W"`, `rm -rf`/`mkdir` of `.git/hooks`, and the upload `tar -xf - -C "$UP"` in `upload_text`. Wrap them in `step`/`own 3` like the sync steps.
3. `--dry-run` shows the image tag in its argv, while a real run uses the host image id. Say so in DEV.md or print "image id resolved at preflight".
4. The signal test's assertion message "removed only after the client ended" says the opposite of what it checks; reword it.
5. The docker command is built in two places (`build_docker_argv` for dry-run and tests, `container_command_text` for the run), so the read-only `.git` mount had to be added at both and the `dev-git-ro` mutant covers only one. Derive the run text from the arrays.
6. `dev` is 552 lines against DESIGN §0's "~250 lines on top of the moved code" and is at the bash freeze limit (CTL-40): grow in Python (`dev_profile.py`), not in the shell.

## CTL-60 — P-plat-20 r1 leftovers

**Status:** OPEN (filed 2026-11-24, from the P-plat-20 round-1 Opus review; CTL-56 here is P-plat-04's; the older plat-docs-20261124 CTL-56 (remote-gate admission) is now CTL-62 and its duplicate CTL-55/CTL-57 were dropped).

1. `check_live_snapshot` asserts on the third distinct snapshot only; a `tool_in_flight` that decays on idle ticks is caught only by the fourth, the first guaranteed idle tick. Also assert on it (about 1 s).
2. A sidecar that never starts, dies at once or writes to the wrong `--out` is reported after 300 s as `inconclusive: no sidecar progress`. Detect that the held marker exists, the wrapper lives and no pi-live child is left, and fail with a definite message.
3. A host without PSI (`CONFIG_PSI` off, `psi=0`) makes every local `gate-run.sh` exit 75, "retry later" for a permanent condition. ctl-doctor should report PSI readability and the docs should say what to do.
4. `chain-run.sh` reports a `gate-run.sh` exit 75 ("not admitted, nothing ran") as `STOPPED-ON-FAIL: lane exited 75`, which reads like a lane verdict. It needs its own marker.

## CTL-62 — remote-gate admission: ticket held while waiting, not PSI-only, duplicate launches

**Status:** OPEN (filed 2026-11-24; package P-plat-14b; renumbered from CTL-56 when plat-docs-20261124 merged, because CTL-56 is P-plat-04's).

Wanted: the admit ticket is never held while waiting; admission is memory-PSI-only (programme rule: memory `full` avg10 >= 3 waits), so `CONTROLLER_REMOTE_SLOTS` / `CONTROLLER_REMOTE_BUDGET_MB` become no-ops, `remote-gate.py` `mem_admit_ok` becomes PSI-only, and its host-side `psi_high` / `--wait-psi` check (today memory `some` > 25 or io `full` > 25) moves to memory `full` avg10 >= 3; the anti-starvation ticket design in the `remote-gate.sh` header is retired with it; a second identical live launch (host + project + lane + sha + args digest) is refused with exit 2; the run name (today second-resolution and fixed before the host-side wait) gets a pid/random suffix so it is unique, and a refused run prints REFUSED, never another run's result.

Source: P-plat-14b plan; observations in runs rgw-assay-first-release-r2-20261124T054814-v1001 (vbpub-assay) and rgw-r0-r1-20261124T060903-v1001 (vbpub-hostobs).

## CTL-63 — ctl-hygiene.sh B1 cannot see 6 of the 21 tracked backlogs

**Status:** OPEN (filed 2026-11-24, P-plat-22b-C review r1 F4; a spec decision for the controller).

B1 counts an entry as closed only from a `Status:` line (or a closed word in the heading's parentheses). `fleetd` (46 entries), `scripts/debian-rollback` (36) and `vm-lab` (30) use `**State:** DONE|open` instead; `hostobs` (58), `tls-edge` (10) and `scripts/truenas` (10) carry no status line at all. Every entry then counts as open and B1 stays silent, and the `unparsed` report only fires for a file with 0 entries. Cheap fix that keeps the spec's closed-word rule: also report `B1 <path>: unparsed: <n> entries but no Status line` when a file has entries, no Status line and no closed heading. Whether `State:` becomes an accepted alias for `Status:` is the controller's call.

## CTL-64 — ctl-hygiene.sh B1 reads the script's own checkout, the state comes from the main checkout

**Status:** OPEN (filed 2026-11-24, P-plat-22b-C review r1 F6).

`ctl-hygiene.sh` resolves `state_dir` to the main checkout's `.controller-state` but leaves `repo` at the checkout that holds the script. Run from a lagging worktree such as `.worktrees/controller-tools`, B1 reports stale backlog numbers beside fresh state numbers. Fix: resolve the default `repo` to the main checkout too (`${common%/.git}`), or say in the header that B1 follows the script's checkout.

## CTL-65 — the same-user implementer can edit the Pi signal guard for later commands

**Status:** BY DESIGN (filed 2026-11-27; controller and main ruling on P-plat-26d H2).

The extension, Python bootstrap, Bash worker, and launcher live in the
controller checkout writable by the implementer. The bootstrap runs Python
interpreter startup before bwrap with `-I -S`, which ignores `PYTHON*`
interpreter settings and skips site initialization; its original environment
is still passed through to the command. The guard prevents accidental signal
commands from an agent using the current launch path, but it does not stop a
same-user agent from editing those files and changing how a later command
starts. This is a documented limit, not a tamper-resistance guarantee.

No stronger same-user protection is part of P-plat-26d. If that guarantee is
needed later, the enforcement assets must be installed somewhere the agent UID
cannot modify and mounted or owned read-only to that UID.

Source: P-plat-26d final safety review H2; controller and main ruling, 2026-11-27.

## CTL-66 — remote-gate admission line prints `slice_oom_kill_delta=-1` for "could not read"

**Status:** OPEN (filed 2026-11-27 by vbpub-core on main's request). Do not fix with this entry.

Run rgw-r2-20261127T021120-v1001 (cmru `r2`, shard 1) ended with `admission: weight=2048MiB slots=1/8 slice_oom_kill_delta=-1`. A counter delta cannot be negative. Read from `scripts/controller/remote-gate.sh`: `oomd` starts at `-1` and is replaced only when both readings of the `dev-gates.slice` `oom_kill` counter are non-negative integers; `oom_kill()` prints `-1` when the identity check or the probe fails. So `-1` is most likely the "unreadable" sentinel (not established for this run: the two readings are not logged). It is also indistinguishable from a real difference of -1, which a recreated slice could produce. Wanted: print an explicit `unknown` with the reason (which reading failed), and report a decreased counter by name instead of subtracting.

## CTL-67 — P-plat-26d review follow-ups: guard test gaps and the missing mutant file

**Status:** OPEN (filed 2026-11-27 by vbpub-platform on main's request).

From the static delta review of P-plat-26d:

- No mutant covers the Bash startup-hook stripping in `pi-pidns-bash` or `sanitize_bash_child_environment`, and the continuation leg of that check in `tests/test-pi-impl.sh` ignores the launch status and counts no invocations, so it can pass without a launch.
- The live PID namespace check is skipped in the gate image (no `/usr/bin/bwrap`): add bwrap to the image, or record one `PI_PIDNS_TEST_FOCUS=live` run in a capped container that has it. `CHANGES.md` names only the "gate forbids user namespaces" skip.
- `tests/test-wrappers.sh` still sends `kill -TERM` to a process group, against the exact-recorded-PID rule.
- `tests/test-mutations.sh` must report ERROR, not KILLED, when any mutant's target file is missing. Today only the three pi override variables are covered.

## CTL-68 — P-plat-35 review follow-ups: queue progress and the missing runner mutant file

**Status:** OPEN (filed 2026-11-27 by vbpub-platform on main's request).

From the static re-review of P-plat-35 (head `eaabf89ea`), two Low findings:

- `PROGRESS:` lines print only from `expect_killed`, which no longer blocks. All of them appear at submission with `reaped=0`, and `mutation_runner_finish`, where nearly the whole run happens, prints none. Call `mutation_runner_progress` after each reap in `finish`.
- A missing or misspelled `CONTROLLER_MUTATION_RUNNER_SCRIPT` in `tests/test-mutation-runner.sh` fails at `source`, exits 1 and is counted as KILLED. Not live at that head (all names match). Print the `ERROR: unreadable override` line, exit 2, and add the variable to the override `case` in `tests/mutation-runner.sh`.

## CTL-69 — Pi automatic compaction summary grows without bound and omits older in-flight status

**Status:** OPEN (filed 2026-11-27 from the P-plat-PICTL closure review; Medium, M1). FILED, NOT FIXED.

`core.mjs` copies `preparation.previousSummary` into each automatic compaction summary in full. Repeated compactions therefore grow the retained summary without a bound. The transcript excerpt is bounded to the latest 32 messages, so in-flight status mentioned only in older messages can disappear.

**Proposed minimal fix:** enforce a fixed summary budget while explicitly carrying the current in-flight list and identifiers forward. Add a test that performs consecutive automatic compactions with a long previous summary and verifies the bound and retained in-flight state.

**Where:** `scripts/controller/pi-ctl/core.mjs:1295-1331` at review head `46ecf967cf0e4b87c1283063d8cfc6321f2eb984`.

**Acceptance test idea:** pass a long previous summary through multiple automatic compactions; each returned summary stays under the budget and retains the current in-flight identifiers even when their source messages predate the latest 32-message excerpt.

## CTL-70 — remote-gate's ssh argv must always carry `IdentityAgent=none`

**Status:** OPEN (filed 2026-11-28 on the main controller's order).

Since about 2026-11-28T00:01Z the forwarded VS Code ssh agent made every ssh from the controller hang. The workaround is `-o IdentityAgent=none` in the local controller hosts file, which another hosts file or a new host entry would not carry. Hand-written ssh needs `env -u SSH_AUTH_SOCK`.

**Proposed minimal fix:** add `-o IdentityAgent=none` where the ssh argv is built (`controller_host_argv`, `scripts/controller/lib.sh:417`, used by `remote-gate.sh:127`), whatever the hosts file says, and check the other ssh callers of that helper.

**Acceptance test idea:** with a fake hosts file that has no `IdentityAgent` option, the recorded ssh argv still contains `-o IdentityAgent=none` exactly once.

**Also in scope (added 2026-11-28 on the main controller's order): ssh inside `pi-pidns-bash`.** In the wrapper's user namespace root-owned files show as uid 65534, so ssh stops with `Bad owner or permissions on /etc/ssh/ssh_config.d/20-systemd-ssh-proxy.conf`. This broke `scripts/controller/dev ... test` in a pi implementer job. The local workaround is `-F <empty user-owned file>` in the controller hosts file's ssh argv.

**Proposed fix:** `controller_host_argv` always adds `-F <empty user-owned file>` as well as `-o IdentityAgent=none`.

**Acceptance test idea:** `test-pi-ctl` runs one ssh dry check through `pi-pidns-bash`.

## CTL-71 — a claimed inbox message can be lost when pi aborts before persistence

**Status:** OPEN (filed 2026-11-28 from P-plat-PICTL-FIX F1b). The controller
cannot distinguish a still-queued custom message from one dropped by Escape or
another abort while the same pi session runtime remains live. At each
`agent_end`, it logs and queues a one-line notice when a processing claim has no
persisted pi entry: `N inbox message(s) claimed but not confirmed; restart or
ask the sender to resend`. The notice does not start a turn, and the durable
claim is not re-injected into the live queue. Restarting the session runtime
replays the claim; otherwise the sender may resend after checking its id.
The same notice can also be sent by the idle-observation path. A later
`agent_end` sends it again while the claim remains unconfirmed; this is BY
DESIGN per the rule, “At each agent_end, the harness logs it to the event log
and injects a one-line notice.”

optional: repeat the notice only when the claimed set changes.

**Where:** `scripts/controller/pi-ctl/core.mjs` claim reconciliation and the
fake-pi queue model in `scripts/controller/pi-ctl/test-core.mjs`.

**Close when:** pi exposes a reliable way to prove the accepted copy was
dropped without risking a duplicate in its live queue, with a gate test for
abort loss and no notice-triggered turn loop.

## CTL-79 — pi-ctl-send has a symlink scan check-then-use window

**Status:** KNOWN LIMIT (accepted 2026-11-28 by main). `pi-ctl-send.sh` checks
the state path for symlinks, then uses it later for `mkdir`, `mktemp` and
`mv`. A same-uid writer could swap a path component between those operations.
This is accepted because a same-uid writer can write directly into the inbox
anyway.

**Where:** `scripts/controller/pi-ctl-send.sh` symlink scan and inbox writes.

## CTL-72 — self-compaction waits for `agent_end`

**Status:** OPEN (observed 2026-11-28; filed from P-plat-PICTL-FIX). The
controller checks its token threshold only at `agent_end`. A model run that
continues without ending its turn therefore delays self-compaction.

**Where:** `scripts/controller/pi-ctl/core.mjs` self-compaction lifecycle hooks.

**Close when:** the extension can request compaction at a supported safe point
while a long-running turn remains active, with a fake-pi test proving it does
not interfere with the active run.

## CTL-73 — post-compaction resume readback supports only topic roles

**Status:** OPEN (filed 2026-11-28 from P-plat-PICTL-FIX). The completion
message directs every role to `scripts/controller/resume-read.sh <role>`, but
that helper works only for the topic roles.

**Where:** `scripts/controller/pi-ctl/core.mjs` compaction completion message
and `scripts/controller/resume-read.sh` role handling.

**Close when:** every supported controller role has a correct bounded readback
path after compaction, with a test for each role class.

## CTL-74 — shutdown leaves stopped monitors in their state files until restart

**Status:** OPEN (observed 2026-11-28; filed from P-plat-PICTL-FIX). A monitor
stopped during session shutdown remains listed in `monitors/<name>.json` until
the next session start reconciles the monitor state.

**Where:** `scripts/controller/pi-ctl/core.mjs` monitor shutdown and recovery.

**Close when:** shutdown removes or marks every stopped monitor in durable state
before the extension returns, with a fake-pi test that reads the state before
any subsequent start.

## CTL-75 — pi-ctl-send derives the repository from the caller's working directory

**Status:** OPEN (filed 2026-11-28 from the P-plat-PICTL-FIX review; Medium).
The outside-repository guard searches upward from `$PWD`. A caller launched from
outside a checkout can point `PI_CTL_ROOT` into that checkout without the guard
discovering the repository.

**Where:** `scripts/controller/pi-ctl-send.sh` repository-root check.

**Close when:** the repository boundary comes from a trusted source independent
of the caller's working directory, and a test invokes the sender outside the
checkout with its state root inside that checkout and verifies no write.

## CTL-76 — pi-ctl-send stages stdin without a bound

**Status:** OPEN (filed 2026-11-28 from the P-plat-PICTL-FIX review; Medium).
The sender copies all stdin to a temporary file before measuring it against
`PI_CTL_MAX_SEND_BYTES`; an oversized or endless stream can consume unbounded
disk space or wait indefinitely before refusal.

**Where:** `scripts/controller/pi-ctl-send.sh` stdin body staging.

**Close when:** staging reads no more than the configured maximum plus one byte,
refuses overflow promptly, and an oversized-input test proves the temporary
file's size is bounded.

## CTL-77 — pi-ctl-send ignores inbox chmod failure

**Status:** OPEN (filed 2026-11-28 from the P-plat-PICTL-FIX review; Medium).
The sender suppresses errors from setting the inbox directory to mode 0700 and
can publish while an existing directory retains wider permissions.

**Where:** `scripts/controller/pi-ctl-send.sh` inbox permission setup.

**Close when:** a chmod failure refuses publication and the directory mode is
verified as 0700 before the message is published.

## CTL-78 — fake pi queues triggerTurn:false notices differently from pi 1.6.0

**Status:** OPEN (filed 2026-11-28 from the P-plat-PICTL-FIX review; Medium).
The fake puts F1b `triggerTurn:false` notices into its follow-up queue. Pi 1.6.0
appends such a notice to the session immediately when idle and defers it to the
end of the active run when streaming, without creating a follow-up turn.

**Where:** `scripts/controller/pi-ctl/test-core.mjs` fake `sendMessage()` model.

**Close when:** the fake models immediate idle persistence and the streaming
pending-custom-message path, with an assertion that the notice is persisted and
does not start a turn.

## CTL-80 — compact-resume can paste `/compact` and resume without a compaction

**Status:** FIXED 2026-11-28 (P-plat-CTL80 r3). The command prefix and guidance are sent separately; all long literal text is chunked at Unicode-safe boundaries, and the resume prompt is withheld unless a changed receipt from the session's `SessionStart(compact)` hook is observed. When known, the receipt's session id must match the explicit expected id or the caller's id when `TMUX_PANE` matches the target; without a known id, any changed pane receipt is accepted, so a different session sharing that pane can confirm. Spawned sessions do not write receipts. A missing or mismatched receipt with an idle pane exits 8; a pane that remains busy is bounded by `DONE_TRIES` and exits 4. Live pane behavior remains for the controller's smoke check.

Filed 2026-11-28, reported by main; High. At about 11:28Z on
pane `%13` both the `/compact <guidance>` line and the resume prompt arrived in
Claude Code as pasted content. `/compact` did not run as a command, and the
script sent the resume prompt as if the compaction had finished. The same script
worked at 03:59Z on that pane.

**Observed:** the failed run (`main-compact35`) had a guidance file of 988
characters and a prompt of 983; the working run (`main-compact34`) had 698 and
709. Run files are in `~/controller/successor-runs/`; no `main-compact35`
compact log was found there. Likely cause, not verified: the single long
`tmux send-keys -l` in `resume-lib.sh` `send_text` is treated as a paste above
some length.

**Where:** `scripts/controller/resume-lib.sh` `send_text`;
`scripts/controller/compact-resume.sh`, which now requires a changed per-pane
receipt written by `ctx-hooks.py` on `SessionStart(compact)` before it can send
the resume prompt.

The tests pin the separate command prefix, bounded chunks (including multibyte
text and a semicolon at a potential boundary), no-prompt exit 8 without a changed
receipt, and empty-guidance submission. The live pane check is still required
before relying on the new typing behavior. The pane's session must run the
checkout's compact SessionStart hook; otherwise the compact may finish without a
receipt and the helper will safely exit 8.

## CTL-81 — a fresh pi session takes a stale monitor notice as its goal

**Status:** FIXED 2026-11-28 (P-plat-CTL80). The monitor notice is skipped by the PRIME/goal hook; the next real prompt becomes the saved goal and consumes PRIME once. Monitor state is stored per role. A `--fresh` start after an earlier
session of the same role injects that session's `monitor-restart` notice as the
first message. It starts a model turn without any prompt, and the PRIME/goal
step saves the notice as `pi-ctl-original-goal`. A role with no earlier pi state
is not affected. Related: CTL-74.

**Where:** `scripts/controller/pi-ctl/core.mjs` monitor restart notice and
original-goal capture.

The fake-pi test pins the monitor notice, next real prompt and one-shot PRIME
behavior. The live-pi side remains for the controller's smoke check.

## CTL-82 — an IDE selection chip is mistaken for typed input

**Status:** OPEN 2026-11-28; pre-existing on main. Severity: Medium.

An IDE-connected Claude Code pane with an editor selection displays a coloured
(not dim) `[⧉ N lines selected]` chip in an otherwise empty input row.
`pane_state_of` reads the chip as typed text, so the resume helpers wait until
they exit 5 instead of typing over the selection.

**Evidence:** `/workspaces/vbpub/.controller-state/platform/smoke/evidence-20261128-ctl80/frame-ide-chip.ansi`
(Claude Code 2.1.332).

## CTL-83 — no test for an invalid expected compact session id

**Status:** OPEN 2026-11-28. Severity: Low.

`compact-resume.sh` exits 2, sending nothing, when the expected session id
(`COMPACT_RESUME_SESSION_ID`, or the self-launch `CLAUDE_CODE_SESSION_ID`)
contains characters outside `[A-Za-z0-9-]`. `tests/test-compact-resume.sh`
has no case for that refusal.

## CTL-84 — a job launched from a pi bash call dies when the call ends

**Status:** FIXED 2026-11-28 (detached `start_job`, owner identity and liveness,
tests, docs). Found by the hostobs pi pilot. Severity: Medium.

A child started from a pi bash tool call (observed with `codex-impl.sh`, also
under `setsid`) dies when the call returns. `pi-pidns-bash-worker.sh` runs each
call under `bwrap --unshare-pid --die-with-parent`, so a new session does not
leave the call's PID namespace.

**Resolution.** The pi-ctl `start_job` tool launches only `codex-impl.sh`,
`pi-impl.sh`, or `pi-review.sh` from the controller's own directory, with the
validated arguments and route/effort environment. It detaches the process,
closes the controller session-lock descriptor, logs to `<run>.launch.log`, and
has no `start_job` runtime cap or stop tool for Codex and pi implementer jobs.
`pi-review` keeps `PI_REVIEW_MAX_SECONDS` (default 7200) and
`PI_REVIEW_MAX_STREAM_BYTES` (default 268435456 bytes), and stops its review
when either limit is exceeded. The tool returns after the owner record changes,
or reports an early exit / 30-second preflight. A live session receives one
`pi-ctl-job` completion notice; after restart, use
`job-check.sh '<run>' '<worktree>' '<route>' [ '<report>' ]`.

Codex launches run through `pi-pidns-bash`. Pi launchers run directly because
the wrapper cannot be nested: the attempted nested `bwrap` fails opening
`/proc/10/ns/ns` with exit 125. `pi-impl.sh` performs its own wrapper preflight
and starts the agent's bash calls inside that wrapper.

**Closed when.** `start_job` is registered, the owner record holds the
procfs-visible pid and start ticks, `job-check.sh` recognizes an outer procfs
owner that `kill -0` cannot reach, and fake-pi tests pin the launcher and
completion contract.

## CTL-85 — controller.local.env and launcher-specific start_job precedence

**Status:** BY DESIGN 2026-11-28 (main decision); severity: known limit.

`controller_init` sources `controller.local.env`, but field precedence depends
on the launcher. `codex-impl.sh` sources it after launch setup, so a local
`CODEX_ROUTE` wins over an inherited or `start_job` route. `pi-impl.sh` restores
nonempty caller `PI_ROUTE`, `PI_MODEL`, `PI_THINKING`, and `PI_SESSION_DIR`; a
caller-set `PI_EXTRA_ARGS` or `PI_RUN_MODE` wins even when empty. When those
values are absent (or empty for the first four fields), the local file can
supply them. `pi-review.sh` passes its account-derived `PI_ROUTE` explicitly
and sets `PI_RUN_MODE=review` and `PI_EXTRA_ARGS=''`, which win for that child.
The launcher documentation records these distinctions.

**Where:** `scripts/controller/lib.sh` (`controller_init`) and launcher docs.

## CTL-86 — `start_job` prints a `job-check.sh` command with the requested Codex route

**Status:** OPEN; severity: medium.

`start_job` builds its printed `job-check.sh` command from the requested route
(`params.route`, defaulting to `luna`). `codex-impl.sh` then sources
`controller.local.env` through `controller_init`, where a configured
`CODEX_ROUTE` can replace that route. The printed command can therefore select
the wrong route-specific completion expectation for the launched run.

**Where:** `scripts/controller/pi-ctl/core.mjs` (`start_job` response) and
`scripts/controller/codex-impl.sh` (`controller_init` route precedence).
