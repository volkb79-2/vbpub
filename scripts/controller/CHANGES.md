# Changelog

All notable changes to the controller kit (`scripts/controller`) are recorded
here, newest first. The kit ships with the repository's `scripts/` tree; it has
no separate version number, so entries are grouped by the work that landed.

## [Unreleased]

### Suite budget and remote-gate OOM classification (P-plat-23)
- `run-gate.toml` `[lanes.suite]` budget 30m -> 90m (measured 29m57s wall on r1002, 34m59s and 55m14s on v1001; slowest x 1.6).
- `remote-gate.sh`: classification reads runner- or run-gate-written evidence only.
  A run-gate `BUDGET_EXCEEDED` verdict (run-gate exits 4), read from run-gate's own
  verdict line for the lane anchored at the line start, is never `INCONCLUSIVE-OOM`
  and keeps its code. rc 76 needs a slice oom_kill delta above 0. Words in the lane's
  own output (`OOMKilled`, `oom-kill`, `Killed`, `exit 137`, `BUDGET_EXCEEDED`, e.g.
  from tests that print them) no longer decide anything.

### Hygiene notifier (P-plat-22b part C)
- `ctl-hygiene.sh` (new, report-only, always exit 0): `HYGIENE <rule> <path>: <value> (threshold <t>)` lines for MEMORY.md size/stale markers/dead links (M1-M3), handoff and STATE block counts and size (H1, H2), stale or non-programme `handovers/` files (S1) and closed-entry share of tracked backlogs (B1). B1 follows the R2-4 review: the entry heading accepts `B001` and `IOB-BL-40` style IDs (any ID part with a digit), a status word in the heading's parentheses closes an entry, and a file with Status lines but no parsable entry is reported as `unparsed`. `ctl-doctor.sh` appends the lines to its report (`hygiene` array in `--json`), never changing the exit status. Tests: `tests/test-ctl-hygiene.sh` (fake trees, pinned date) and a hygiene case in `tests/test-ctl-doctor.sh`; both wired into `tests/run-suite.sh`. No hand mutants yet (not in this package's contract).

### Launcher must-fixes (CTL-38, CTL-42 part 1)
- CTL-38: every Codex exec path — new, manual resume, and automatic
  continuation — now derives `<run>.last.txt` from the last completed
  `agent_message` item in that invocation's JSON stream. A stream with no final
  text removes a stale file; the old `.done` outcome remains unchanged.
- Codex and Pi Open Design routes default to the catalog maximum `max` effort;
  `CODEX_EFFORT` and `PI_THINKING` still override it. Codex `.done` appends
  `EFFORT=<value>` after the historical fields; that field is Codex-only and
  Pi records no thinking level in its `.done` marker.
- `CODEX_EFFORT` accepts only `minimal`, `low`, `medium`, `high`, `xhigh` or
  `max`; another value exits 2 before the owner record or child launch.
- CTL-42 part 1: Codex accepts a `sol` route using `$HOME/.codex`, Luna defaults
  to `$HOME/.codex2`, and `CODEX_HOME_OVERRIDE` wins on Luna and an explicit
  `CODEX_ROUTE=sol codex-impl.sh` launch. `codex-review.sh` deliberately gives
  `CODEX_HOME_REVIEW` precedence, so a caller's override cannot redirect a
  review. Repository guidance names the wrappers rather than a Codex home. The
  limits note and reader remain dropped.
- Route/home migration: usage-limit recovery now accepts the review's `sol`
  `.route` value. Previously started Luna threads under `$HOME/.codex` must be
  resumed with `CODEX_HOME_LUNA="$HOME/.codex"`, because new Luna runs default
  to `$HOME/.codex2`.
- `gate-run.sh` validates `CONTROLLER_GATE_SLOTS` with explicit ASCII digit
  lists, so a Unicode collation cannot admit a non-ASCII digit.

### Added
- `remote-gate.sh`: forwards `--collect-store <rel>` (repeatable), `--seed-store <local dir>=<rel>` (repeatable; the local side is made absolute) and `--store-max-mib <n>` to `remote-gate.py run`, one argv word per value, accepted in any order with `--base` and before `--`, with the runner's own refusals (including a `.git` path component) applied before a slot is taken (RG-298; semantics in `scripts/netcup/runner/README.md`). Test: `tests/test-remote-gate.sh`.
- `gate-run.sh`: after it holds a slot and before it starts `run-gate`, it waits while memory PSI `full` avg10 is >= 3 (CTL-57): one line a minute, bounded by `CONTROLLER_GATE_PSI_WAIT_S` (default 1800), then exit 75 "not admitted ... nothing ran" (never a lane verdict). An unreadable PSI file exits 75 at once. `CONTROLLER_GATE_PSI_FILE` is the test seam. Same rule as `dev` host-admission/1. Tests in `tests/test-gate-slots.sh` (the other gate-run tests run against a quiet PSI file); 9 new hand mutants.
- `rules.md`: "House rules for every implementer brief" (CTL-50): remote dev container before each commit (interim `hostobs/tools/dev` for Rust), no direct `hx.sh`/`ssh`/`docker` and no tests in the devcontainer, contract plus target size with at most 2 review rounds, explicit launch effort, unique run names with resumed runs verified from git, DONE/PARTIAL/BLOCKED with a finalization. `BRIEF-TEMPLATE.md`, `finisher-generic.md` and the README point to it; the README records the bash freeze (CTL-40).
- `remote-gate.sh`: `CONTROLLER_REMOTE_GATE_WORKROOT` sets where the per-run throwaway worktree lives (default unchanged: `$CONTROLLER_HOME/remote-gate-work`). A workroot under an IDE workspace folder makes the editor's Git extension adopt every scratch worktree as a repository ("too many active changes" popup) and lets language servers index it; point it outside every workspace folder.
- `gate-run.sh`: `CONTROLLER_GATE_SLOTS` (integer 1 to 8, default 2) sets the number
  of local gate slots (CTL-33). Slot 1 is `CONTROLLER_GATE_LOCK`, slot 2 is
  `CONTROLLER_GATE2_LOCK`, slots 3.. are `gate<N>.lock` in `CONTROLLER_GATE_LOCK_DIR`
  or beside `CONTROLLER_GATE_LOCK`; with one slot `CONTROLLER_GATE2_LOCK` is not
  required. Unset behaves and prints exactly as before (the existing two-slot test is
  unchanged); the default stays 2 because the controller devcontainer has an 8 GiB cap
  and was OOM-killed once. Invalid counts exit 2. `ctl-doctor.sh` prints the effective
  count and checks every slot lock. New `tests/test-gate-slots.sh`; 39 new hand mutants.

### Fixed
- `tests/test-pi-impl.sh` `check_live_snapshot` no longer polls 100 x 0.1 s (CTL-55): the fake agent holds after its whole stream is written (`FAKE_PI_HELD_MARKER`), the check asserts on the third distinct sidecar snapshot seen after that, releases the hold and reaps the wrapper on every path. Only a 300 s hang guard remains, reported as `inconclusive: no sidecar progress within the hang guard`, distinct from a real failure.

### Fixed (review round of the controller import)
- `ctl-state-backup.sh`: the member relabel no longer rewrites symlink targets
  (`--transform flags=rh`; an absolute link stays absolute, a relative one stays
  relative, hardlink members keep pointing at the relabelled member); a
  pre-existing `CONTROLLER_BACKUP_DIR` keeps its mode (only a directory the run
  creates is 700); `--verify` exits 1 for an archive with 0 members or without
  its sidecar; a `CONTROLLER_REPO` that is a linked worktree without
  `.controller-state`/`plan.yaml` prints a WARNING. 8 new hand mutants.
- `remote-gate.sh`: each old program-directory environment name that is set
  prints a one-line warning naming its `CONTROLLER_REMOTE_*` replacement (never
  mapped); a weights file that exists but cannot be read warns; the runner
  repository name defaults to `vbpub`; the runner snapshot is written to a
  `mktemp` file and renamed, so concurrent runs cannot corrupt it (a
  concurrent-run test with sync files). README documents the differences, the
  moved throwaway worktree directory and the rule to stop using the old tool
  before using this one. 7 new hand mutants.
- `chain-run.sh`: `--unset-env NAME` (repeatable); the README states that
  `gate-run.sh` requires both gate locks (a chain over unset locks still ends
  with `CHAIN-DONE`, by design) and the old-to-new remote log and marker names.
  2 new hand mutants.
- `ctl-doctor.sh`: FAILs when `CONTROLLER_GATE_LOCK`/`CONTROLLER_GATE2_LOCK` are
  unset, relative, equal or in an unwritable directory; `UserKnownHostsFile=/dev/null`
  no longer counts as a pin; the wording no longer claims nothing is executed
  (`controller.local.env` is sourced by `lib.sh`). 6 new hand mutants; the
  root-only unwritable-directory check prints a SKIP line and the mutation
  runner reports its mutant as skipped under uid 0.
- `run-gate.toml`: the `suite` lane budget is 30m (measured about 15 min wall at
  354 mutants; it was 15m, sized for 222).
- `scripts/netcup/runner/remote-gate-hosts.example.json` carries placeholder
  paths (CTL-30 fixed); CTL-31, CTL-32 in the backlog index; CTL-33 filed (local
  gate slot count is hardcoded to two).

### Changed
- `gate-run.sh` defaults `RUN_GATE_PROFILE` to `off` (the caller can still set
  `on`): local gates run without profiling data so they behave the same before
  and after the resource-observer cutover. Reconciled from the program-directory
  copy, which was otherwise older (the repo copy already polls both slots).
  Two hand mutants.

- `remote-gate.sh` accepts the old pass-through spelling `-- --base <ref>` (and
  `--base=<ref>`) as the `--base` option: the base commit is bundled and
  forwarded once, instead of reaching the host's `run-gate` alone. A base given
  twice is refused. Two hand mutants.

- Rules and templates reconciled with the program-directory copies (the repo
  copies were the newer ones; only missing content was folded in): `rules.md`
  names `gate-run.sh` instead of a bare `flock` on one lock, forbids launching
  other agent sessions from inside a job, reports stale in-flight records
  instead of deleting them silently and requires a `description` on every Bash
  call; `BRIEF-TEMPLATE.md` section 6/8 gain the dependency, Docker, style and
  stop-condition lines; the two finisher prompts gain the "call tools for real"
  and incremental-report wording.

### Added
- `docs/STATE-VS-WORK.md` (the work-versus-state table with measured sizes, what the
  backup covers, what is rebuildable, the `git clean` hazard, operator checklist) and
  `docs/runbooks/host-access-template.md` (sanitized identity-checked host access flow,
  placeholders only, two sample host-table aliases). CTL-31 and CTL-32 filed.
- `ctl-state-backup.sh`: archive of the non-rebuildable controller state into
  `CONTROLLER_BACKUP_DIR` (refuses when unset, relative or inside a git work tree);
  `--list`, `--include-large`, `--max-file-bytes`, `--verify <archive>`; mode 0600,
  temporary name then rename, `.sha256` sidecar, symlinks stored as links, never
  `*.jsonl`, `.git`, worktrees, logs or artifacts. Test `tests/test-ctl-state-backup.sh`,
  15 hand mutants.
- `ctl-doctor.sh`: read-only preflight of a controller machine (home and env file,
  host table schema, key mode 600 and pin files, remote-gate allow-list, required
  tools, default tester image by exact name, the removed `run-gate-2560` tag,
  effective `RUN_GATE_PROFILE`, runs/logs directories, and the symlink-into-a-
  work-tree `git clean` hazard). Exits non-zero on any failure, prints the fix per
  failure, never prints a secret value; `--json` and `--scan <dir>`. `lib.sh`
  gains `CONTROLLER_INIT_READONLY=1` (no directory is created) and the exported
  `CONTROLLER_{HOME,LOGS_DIR,RUNS_DIR}_CONFIGURED` spellings (before symlinks are
  resolved). Test `tests/test-ctl-doctor.sh`, 44 hand mutants.
- `sonnet-continuation-preamble.md` and `backlog-burn-generic.md`: the two
  reusable prompt blocks that only existed in the program directory, sanitised
  (no absolute paths, addresses, pinned image tag).
- `chain-run.sh`: ONE generic chain runner replacing the 38 per-package
  `chain-*.sh` wrappers of the unversioned program directory. Arguments:
  worktree, project subdir, log prefix, lanes; `--head <sha>` pins the commit
  (refuses with `HEAD moved: ...` or `tree dirty` in the marker before any lane),
  `remote:<lane>[:--base=<sha>]` lanes go through `remote-gate.sh`
  (`--remote-host`, or the only allowed alias), `--stop-on-fail` stops on a
  non-zero lane exit; otherwise a failed lane never stops the chain and the
  chain never reads a verdict. Logs `<prefix>-<lane>.log` and
  `<prefix>-remote-<lane>.log`, marker `<prefix>.chain` (`CHAIN-DONE` or the
  reason, written atomically, removed at start). It exports no image tag and no
  profiler socket. New `tests/test-chain-run.sh` (stub launchers; asserts lane
  order, log names, marker content, pin and dirty refusals, remote forms,
  environment) and twenty hand mutants; README section with the migration
  recipe and two worked examples.
- `remote-gate.sh`: the controller's remote gate wrapper, imported from the
  unversioned program directory and parametrised. Host, SSH transport, identity
  check and guard come from the host table (`CONTROLLER_REMOTE_HOSTS_FILE`,
  default `CONTROLLER_HOSTS_FILE`) instead of a private env script; the
  allow-list of gate hosts is the required `CONTROLLER_REMOTE_GATE_HOSTS`; the
  throwaway worktree lives under `CONTROLLER_HOME`, not in the repository;
  `--base <ref>` is a wrapper option forwarded to `remote-gate.py`; the lane
  weight is rounded up (a measured 800 MiB peak with 15 percent headroom is 920,
  not 919). New `tests/test-remote-gate.sh` (fake `remote-gate.py`, ssh and
  identity stubs; no network) and eleven hand mutants. Sample
  `remote-gate-weights.tsv.example`; settings documented in
  `docs/CONFIGURATION.md`.
- `LONG-TOOL` / `HUNG-TOOL` (CTL-18): `job-status.sh` separates a heavy tool that
  is still working from one that stopped. `HUNG-TOOL` requires the ABSENCE of
  progress evidence, and the evidence set is the IN-FLIGHT TOOL CALL's own
  process subtree — never the wrapper's whole tree, which also holds the agent
  (a model loop) and the sidecar (its `/proc` scans and `git` calls), both of
  which grow every tick and once made a blocked tool read as working forever.
  Progress is a strictly positive delta in CPU ticks or IO bytes over the SAME
  processes (`pid` plus the kernel's own start ticks); a decrease, a changed set,
  a tool call's own START and an unreadable `/proc` are baselines, never
  evidence, and an unknown fact stays `null` (the row prints
  `progress=- progress-why=<reason>`). The row also prints the tool's own command
  (`cmd=`) and the exact child pids to steer (`steer=`), because the only process
  the earlier row could name was the wrapper — the one process that must not be
  killed. `pi-live.py` publishes `progress_reason`, `progress_baseline_epoch`
  (measured-but-never-moved, which is what licenses the stronger word) and
  `progress_tree_nodes`/`progress_tree_truncated` for the walk's documented cap
  (`PI_LIVE_TREE_NODE_LIMIT`, default 512).
- `DIED` (CTL-19): `job-check.sh` reports a run whose `.done` is missing from the
  run name's own owner record (`<run>.lock`: pid, start epoch, kernel start
  ticks, boot id) instead of assuming `RUNNING`. A live owner stays `RUNNING (no
  .done)`, a gone owner (or a recycled pid, or one recorded under a previous
  boot) is `DIED (wrapper pid <pid> gone, no .done)` and a red flag, and a name
  nobody ever owned keeps the prototype line plus `owner record: none`.
- One live wrapper per run name (CTL-14): `lib.sh` `controller_run_claim` takes an
  exclusive `flock` on `<runs-dir>/<name>.lock` before any name-owned record is
  written, so a second invocation on the same name is refused with exit 2 and the
  live owner's pid. `CONTROLLER_RUN_FORCE=1` never bypasses a live owner; for a
  dead one it retires the whole record set into `retired/<name>/<utc-stamp>/`
  (nothing is deleted) and names the previous owner.
- A settled `stop` whose final message carries TEXT **and** a trailing tool call
  is `SUCCESS` (pinned by a test): the text is the run's final message and the
  call is bookkeeping pi emits in the same message. `TOOL-CALL-NO-TEXT` is
  defined on the ABSENCE of text, so it never applies to that shape.
- Run outcome vocabulary (CTL-13, CTL-17): `pi-impl.sh` now classifies
  `CONTEXT-FULL` (a settled `stopReason=length`: the context window filled; the
  work is intact and the controller starts a FRESH successor from the run's
  forward-oriented state file — the wrapper never continues it) and
  `TOOL-CALL-NO-TEXT` (a settled `stop` whose final message is a tool call with no
  text: the run ended mid-work). Neither is `SUCCESS` and neither is the continued
  `EMPTY-COMPLETION` class; `job-check.sh` names both so a controller cannot miss
  them.
- `.live` token semantics (CTL-15): `latest_total_tokens` is the last NON-ZERO
  measurement (or `null` when none was ever read) and
  `latest_total_tokens_last_report` is the most recent usage report as its own
  fact, so a `0` from a streaming delta or an error turn never masquerades as an
  empty context. `job-status.sh` prints both.
- `since-commit` (CTL-16) names whose commit the age belongs to:
  `since-commit=no-commits-yet` when the run has no commit of its own,
  `since-commit=123s(ahead=2)` when it does, and `since-commit=null` when
  `commits_ahead` cannot be read — an unreadable qualifier never licenses the
  number, and a run started on an existing branch no longer reports the base
  commit's age as its own.
- `codex-impl.sh` claims its run name (the same `lib.sh` helper as `pi-impl.sh`),
  releases it on every exit path, and writes `<run>.done` on every exit path after
  the child launches as `EXIT=<n> OUTCOME=CODE-EXIT AUTORESUME=<k>`. A resume
  removes the previous invocation's marker before its child starts, so a reader
  never reports an older resume's exit while the new one works. Every launch path
  (new, resume, autoresume) closes the ownership descriptor before exec
  (`controller_close_ownership_fds`), so a SIGKILLed wrapper frees the name at
  once even while the agent it started — or a daemon the agent left behind — is
  still running; `CONTROLLER_RUN_FORCE=1` retires the previous records into
  `retired/<name>/<utc>/` and names what it retired. The owner record itself is
  written only AFTER the invocation's own preflight passes, so a REFUSED
  invocation never overwrites the identity of the owner it refused on.

### Fixed
- A refused launch no longer overwrites the previous owner record (CTL-23): both
  wrappers write the owner record only after every refusal that can follow the
  claim (route, continuation count, blocked accounts, and for `pi-impl.sh` the
  credential file and key), and `pi-impl.sh` touches `.wt`, `.route`, `.thread`
  and `.done` only after the same point, so a launch that never started leaves the
  previous run's records byte-identical and a refused first launch leaves no
  evidence that would refuse its retry. `job-check.sh` reads the empty lock a
  refused launch leaves as `NOT-STARTED` (red) instead of `RUNNING` or `DIED`;
  an empty lock that a wrapper still holds stays `RUNNING` (preflighting).
- CTL-23 review follow-up: `job-check.sh` reads a HELD lock that still carries a
  dead previous owner's record (a `DIED` name being resumed, before its own
  record) as `RUNNING (preflighting, previous owner gone)` instead of `DIED`;
  `CONTROLLER_RUN_FORCE=1` retires the previous records only once the launch is
  real, so a forced launch refused at the route, accounts or credentials moves
  nothing into `retired/`; an accepted pi resume is pinned from the other side
  (lock names the wrapper, `.done` absent while the agent works, `.thread` is the
  session); the `lib.sh` header says the lock records who STARTED the name. Filed
  without fixing: CTL-25 (the lock probe can make a concurrent claim fail) and
  CTL-26 (a vanished member voids the progress reading; churn under an idle
  parent points `steer=` at the parent).
- The scope of the tool's subtree (every descendant of the agent) is documented
  as a known limit that errs toward `LONG-TOOL` (CTL-24, no code).
- The Codex resume and auto-resume exec sites have their own killed-wrapper
  evidence (CTL-22): a SIGKILL check now kills the wrapper at each of the three
  sites (new launch, manual resume, automatic continuation) with the agent and
  the setsid daemon it left alive, asserts the run name is free at once and the
  relaunch is accepted, and one mutant per site deletes the ownership-descriptor
  close from that ONE line — so the routine resume path can no longer lose the
  close unnoticed.
- Progress is a surviving process's delta, not a change of the set (CTL-21):
  `pi-live.py` keeps each member of the in-flight tool's subtree separately and
  counts a SURVIVOR's strictly positive CPU/IO delta (the same pid AND the same
  kernel start ticks in both readings). A process-set change (`make -j` starting
  or reaping a worker, per-test subprocesses) is neither progress nor a reset of
  the quiet clock — its baseline is the first reading of the current tool call —
  so a silently churning tool still reads `HUNG-TOOL` at the threshold while a
  churning tool with a CPU-burning survivor reads `LONG-TOOL`. A recycled pid
  number is a different process and never a survivor.
- An unmeasurable progress epoch is no longer read as measured progress (CTL-20):
  `pi-live.py` publishes `progress_measured` beside `progress_epoch` — true only
  when the current tick read the in-flight tool's own subtree or evidence was
  produced on it, false when the tick could not re-read the subtree (the
  previous epoch is carried forward as a leftover) or when the epoch is older
  than the call (it is then published as `null`, never lifted to the tool's
  start). `job-status.sh` takes that flag as its only licence, so an unmeasured
  epoch prints `progress=- progress-why=unread` and the weaker word: the tool's
  age alone can no longer decide `HUNG-TOOL`, which the lift made it do again
  whenever the run's stream grew before a tool whose subtree could not be read.
- `job-status` liveness and the dead-wrapper/orphan case: `ACTIVE`/`WRAPPER-DEAD`
  is decided from the run name's owner record (`<run>.lock`), the same rule
  `job-check.sh` applies, so a recycled wrapper pid no longer reads `ACTIVE` and
  the row names the RECORDED pid (`recycled` is stated) instead of the stranger
  the snapshot carried. With no record the payload pid is the fallback and the
  row says `owner-record=none`. When the wrapper is dead and the agent it started
  still works, the row says `wrapper-dead-agent-alive` and names the agent
  (`agent=<pid>(<cmd>)`) and what that agent is running (`tool=<pid>`), found
  structurally (cwd = the run's worktree, argv[0] = `pi`/`codex`). The
  `last-event` age of a dead run comes from the run stream's mtime and says so
  (`last-event=42s(stream)`), because the dead sidecar can never be refreshed.
- `job-check` thread recovery: a `DIED` run whose `.thread` was never written now
  has its id READ from the run's own stream (pi's first `session` event, Codex's
  `thread.started`), printed as `thread: <id> (recovered …)`, with the exact
  `resume:` command; `thread: none` when the stream carries no id, and the file
  is never written by this read-only check.
- A final assistant message with `stopReason=stop`, a tool call and no text no
  longer classifies `SUCCESS` (CTL-13), and `stopReason=length` no longer
  classifies `INCOMPLETE` (CTL-17).
- A wrapper killed without writing `.done` no longer reads as a running job
  (CTL-19), and two wrappers can no longer share one run name's records (CTL-14).
- `HUNG-TOOL` is no longer decided on wall-clock alone (CTL-18), and
  `.live`/`job-status` no longer publish a placeholder `0` as the context size or
  an unqualified base-commit age as `since-commit` (CTL-15, CTL-16).
