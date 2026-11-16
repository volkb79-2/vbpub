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
| CTL-12 | The remote-gate helper refreshes its cached `remote-gate.py` through a shared `.new` temp name, so concurrent launches collide before anything reaches the host | P2 | OPEN 2026-11-16 (requires a repo-side successor) |
| CTL-13 | A final assistant message with `stopReason=stop`, a tool call and no text classifies `SUCCESS` although the documented contract requires text | Minor | OPEN 2026-11-16 |
| CTL-14 | Nothing stops two wrappers from running the same run name (or session) at once; both write the same `.done`, `.events` and retry counter | Should-fix | OPEN 2026-11-16 |
| CTL-15 | `.live`'s `latest_total_tokens` reads `0` for a stream whose usage is only carried on delta events, which is exactly the long run it exists for | Should-fix | OPEN 2026-11-16 |
| CTL-16 | `since-commit` shows the branch's last-commit age unqualified, so before the run's first commit it reports the base commit's age (the `ahead=` field on the same line is the qualifier the fix needs) | Minor | OPEN 2026-11-16 |
| CTL-17 | `stopReason=length` (the context window filled) is classified `INCOMPLETE`, so the controller cannot tell a truncated run from a broken stream | Should-fix | OPEN 2026-11-16 |

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

## CTL-12 — the remote-gate helper refreshes its cached copy through a shared `.new` name, and concurrent launches collide

**Status:** OPEN (filed 2026-11-16); priority P2 for the controller's own
tooling. The helper (`codex/remote-gate.sh` in the controller's program
directory) is deliberately OUTSIDE the repository, so this entry states the
REQUIREMENT for a repository-side successor rather than pointing at a file here.

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

**Status:** OPEN (filed 2026-11-16); severity Minor. Found by the independent
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

**Status:** OPEN (filed 2026-11-16); severity Should-fix. Found by the same
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

**Status:** OPEN (filed 2026-11-16; evidence framing corrected in review round
1 the same day); severity Should-fix. The zero was OBSERVED in live `job-status`
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
before the model reports anything, the rule must be stated: either ignore
zero-valued usage on delta events specifically (identified by the event's own
`assistantMessageEvent.type`), or carry both the last completion figure and a
`reported_zero` fact. Record the chosen semantics in the `pi-live.py` docstring,
`docs/DESIGN-GUIDE.md`'s liveness section, and `job-status.sh`'s header.

**Where.** `scripts/controller/pi-live.py` (`Snapshot.consume` and `payload`),
`scripts/controller/job-status.sh` (the printed field's label if the semantics
become two facts), `scripts/controller/tests/fixtures/` (a new fixture whose
stream ends in delta events carrying zero usage after a real completion),
`scripts/controller/tests/test-pi-impl.sh` (the focused case plus a mutant),
and the three documents named above.

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

**Status:** OPEN (filed 2026-11-16; rewritten in review round 1 the same day:
the first version claimed the payload cannot tell whether the run committed, which
is false); severity Minor. Observed in live `job-status` output on 2026-11-16
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

**Status:** OPEN (filed 2026-11-16); severity Should-fix. Filed from the
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
