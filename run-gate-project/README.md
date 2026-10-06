# run-gate — the per-project gate entrypoint

**Status:** BUILT (P01, 2026-08-22). First consumer: **nyxloom** (controller
amendment A1 — ciu was under parallel development and is DEFERRED; its assay
lane ships construction-tested with live proof at its adoption). This README
remains the design authority; **`SPEC.md` is the normative implementation
contract** the code and tests adhere to (`__revision__` in `run-gate.py`
tracks it). Build-and-adopt handoff: `HANDOFF-P01-build-and-adopt-ciu.md`;
chronological build log incl. every failure:
`HANDOFF-P01-build-and-adopt-ciu-LOG.md`.

## Built deltas vs this design (all recorded, none silent)

The build stayed faithful to the intent; four things crystallized differently
than the prose predicted (full rationale in `SPEC.md` §8 and the LOG):

1. **Central defaults (controller A2):** shared environment facts live in a
   repo-root `run-gate.root.toml` — the NEAREST STRICT ANCESTOR of the project
   dir. At P01 build time environments only (`[lanes.*]` there was
   rejected) — **superseded by RG-16 (`R-22`)**: central `[lanes.*]` are
   legal shared lanes every consuming project inherits BY NAME. Project
   tables shadow a central name entirely (auditable override, no field
   merging). Lookup stops at the project's Git toplevel, so a nested standalone
   repository cannot inherit an enclosing repository's config.
2. **Config discovery:** without `--worktree`, the project config is found
   next to the INVOKED script path WITHOUT resolving symlinks (a symlink's
   parent is the project), CWD as fallback. With `--worktree`, run-gate first
   preserves that project's path relative to its Git toplevel, then reads
   `run-gate.toml` and the nearest `run-gate.root.toml` within that Git tree.
   A missing target `run-gate.toml` is an error; the invoking checkout's
   lanes are never substituted (see the
   [design rationale](docs/DESIGN-GUIDE.md#a-worktree-is-the-complete-judgment-boundary)).
   CWD-first (the handoff's wording) breaks `nyxloom/run-gate.py --list` from
   the repo root.
3. **Slice policy (controller A3):** the repository root's central
   `[environments.tester-unified]` binds `cgroup_slice_env` to
   `$CGROUP_PARENT_DEV_GATES`, so every inheriting project uses the same
   host-provided gates tier. `cgroup_slice` remains available for an explicit
   per-environment override, and a project may use `cgroup_slice_env` when it
   needs a different host variable; where host systemd is reachable, run-gate
   requires `LoadState=loaded` and a non-empty `FragmentPath`, so a transient
   typo-created slice does not count as installed. In container contexts the
   outer gate launcher verifies host placement. nyxloom's dev gate migrated
   OFF its hardcoded `nyxloom-gates.slice` literal (prod-instance intent).
4. **Lane schema final:** `memory` (docker `--memory`, per-lane RAM
   overrides; superseded by `resources.memory`), `resources` (`R-29`: a
   table — `memory`, `memory_swap`, `cpu_weight`/`io_weight` advisory,
   `shared`, and — RG-48, `R-29` amended — `cpus`, a decimal string →
   `docker run --cpus`, with an environment-level fallback
   `[environments.<e>.resources] cpus = …`), `clean_tree` (default TRUE —
   refusals are the doctrine; nyxloom adopts `false` explicitly until
   NL-1), `assay_command` optional (omit it for the selected worktree's
   `assay/` source; supply it for an external immutable artifact), `budget` (hard wall-clock bound; assay state remains resumable), `stall_timeout` (rev 34,
   RG-36/`R-40c`: same `\d+[smh]` grammar as `budget` and read beside it,
   but it bounds SILENCE in the lane's liveness signal — an assay lane's
   progress file, or (RG-41, rev 36) a command lane's own log-stream
   arrival times, `SPEC` `R-40f` — never total elapsed time. Legal on both
   `assay` and `command` lanes since RG-41; meaningless, but accepted, on
   host/exec lanes, which start nothing to watch. The documented shape for
   a mutation lane is a generous assay `budget` +
   `judge.mutation.budget_per_candidate` + this key), `profile` (RG-55:
   `false` to opt a lane out of profiling entirely, or a table
   `{enabled, damon}` overriding `[profile]`'s own defaults — SPEC
   `R-43h`).

## Gate and evidence

### Closed results and explicit runner modes

run-gate retains the judge's raw exit code in the result and returns one of
five process statuses: **PASS 0**, **FAIL 1**, **ERROR 2**, **NOT_RUN 3**, or
**BUDGET_EXCEEDED 4**. A command's non-zero code is a FAIL, including pytest
5 for an empty collection; an ERROR or NOT_RUN result names its reason. Use
`--json` to read `verdict`, raw `exit_code`, `reason`, `log_path`, and assay
outcome and admission as structured result fields. The full mapping is in
[the CLI contract](SPEC.md#2-cli-contract).
NOT_RUN reasons are closed: realness-mismatch, service-down,
environment-down, environment-mismatch, env-missing, external-missing,
external-down, dirty-tree, no-headroom, lock-busy, no-base, judge-floor,
judge-digest, provenance-mismatch, and state-mount.

Every `[environments.<name>]` declares `mode = "ephemeral"`, `"exec"`, or
`"host"`. Names carry no behavior: `host` is an ordinary name and is a
container only when its declaration says `mode = "ephemeral"`; `mode = "host"`
runs on the invoking host. Existing configs can be migrated one at a
time without losing comments:

```console
./run-gate.py migrate-modes run-gate.toml
./run-gate.py migrate-modes run-gate.root.toml  # when this repo has one
```

The tool refuses a missing mode. Review the diff, then migrate wrapper
assumptions separately. See the [mode rationale](docs/DESIGN-GUIDE.md#runner-modes-name-the-runtime-contract) and the
[consumer migration steps](CONSUMERS.md#closed-results-and-runner-mode-migration).

### PID 1 and cgroup resource failures

run-gate refuses to start as container PID 1, where orphaned lane processes
would accumulate instead of being reaped. Start Docker containers with
`--init` or set `init: true` on the Compose service. During each real lane,
run-gate samples its cgroup v2 `pids.events` and `memory.events` counters. A
refused fork or OOM kill forces ERROR/2 even if the lane reports success. Any
raw status already known remains in the result, including when the post-lane
counters are unavailable or cannot be compared safely. The standalone
`--version` operation remains available. See the [design rationale](docs/DESIGN-GUIDE.md#pid-1-and-cgroup-resource-events)
and the [consumer setup](CONSUMERS.md#init-reaping-and-resource-events).

### Native sequences and imported assay lanes

`kind = "sequence"` runs declared member lanes in order, records each member
and the composite, and can stop at the first non-PASS result. A project can
declare `[project].trunk` once: a merge commit at the trunk tip uses its first
parent as the request base; a non-merge trunk tip returns NOT_RUN with
`no-base`. A sequence resolves that base once and passes it only to members
that request one. This replaces shell conjunctions that hide nested run-gate
statuses. The user guide has a [native sequence example](CONSUMERS.md#native-sequences-and-trunk-bases);
the [design guide](docs/DESIGN-GUIDE.md#sequences-own-the-whole-composite) explains
the base and status contract.

External Assay consumers can declare one pinned judge and import lane names
from `assay lanes --json` with
`import = { environment = "<e>", lanes = "all" | [<names>] }`. Imported
lanes inherit the shared command and pin; a project lane with the same name
overrides it. This is the v7 shape chosen to carry directly into CIU v8's
`[testing.judge] import` contract. See the [consumer example](CONSUMERS.md#shared-assay-lane-imports).

An assay lane can pass `--reuse-from PATH`, repeatable `--rejudge ID`, and
`--rejudge-outcome BUCKET` to one selected Assay run. A command lane accepts
arguments after `--` only when it declares `accepts_args = true`; sequences
and composite commands refuse them. See [selective requests](CONSUMERS.md#selective-lane-requests)
and the [request-scope rationale](docs/DESIGN-GUIDE.md#selective-requests-stay-with-the-selected-lane).

Every assay lane receives `--resume`, a worktree-local progress file, and a
durable `--state-dir` under the checkout that owns the shared Git directory.
The default state root is `<checkout>/.run-gate`; container environments can
declare `state_root` for a different durable mount. Before Assay starts,
run-gate checks that root and the deepest existing directory on the keyed
state path are writable as the lane user, and `doctor` reports the result
per environment. An unavailable or unwritable state area is
NOT_RUN/`state-mount`, not a test failure. The durable-state option requires
Assay 5.2.0. Read the
[state contract](CONSUMERS.md#resume-progress-and-durable-assay-state) and
[mount rationale](docs/DESIGN-GUIDE.md#assay-resume-state-needs-an-environment-owned-mount)
before removing an ephemeral worktree.

Projects inside the checkout keep their relative path key. A project outside
the checkout uses a separate `assay-state-external/<sha256>` path based on its
resolved absolute path, avoiding aliases between paths that sanitize to the
same string.

In internal source mode, run-gate checks and invokes Assay with the same
isolated Python interpreter and selected worktree source. It rejects regular
project-local shadow modules without executing them and ignores `PYTHONPATH`
shadows. It also checks that the verdict's `assay_version` and commit identify
the recorded run. External artifact mode continues to require complete
`judge_provenance`. An inflight Assay record keeps the launch-time identity
mode, so re-attachment does not reinterpret an old verdict after config
changes or pass it as a command result if the lane kind changed. See the [source identity rationale](docs/DESIGN-GUIDE.md#source-backed-assay-identifies-code-as-source)
and [Assay adoption and recovery](CONSUMERS.md#assay-lanes-projects-that-adopt-assay-the-quality-partnership).

### Daemon-wide count admission and failed evidence

Count admission is an opt-in project switch backed by Docker-name tickets, so
gates on one daemon share one compare-and-swap and one published count cap.
It defaults off. Enabled runs, `admission set`, and `admission show` require a
local Docker Unix endpoint so every client addresses that daemon. Operators
publish and inspect the cap with
`run-gate admission set|show`; enabled lanes record their ticket, wait in
ticket order, and return NOT_RUN/`no-headroom` when the wait expires. The lane
budget starts once its runner locks and admission ticket are held, so queue
time is excluded. The [admission guide](CONSUMERS.md#daemon-wide-gate-admission) gives the config
and operator commands; the [design rationale](docs/DESIGN-GUIDE.md#docker-names-order-daemon-wide-admission)
covers the Docker object protocol.

`run-gate status [--worktree PATH] [--json]` reports the selected project's
inflight records, run-gate's host-wide exec-runner lock holders and waiters,
and the daemon's published admission cap, tickets, and tombstones. It only
reads those sources. A missing or unreadable source is reported as ERROR 2
with the available partial results. `doctor` checks an enabled admission
config's local ticket image, published object, and readable positive cap;
the image check uses `docker image inspect` and does not start a ticket
container. See the [status and doctor guide](CONSUMERS.md#runner-occupancy-status)
and [scope rationale](docs/DESIGN-GUIDE.md#status-uses-the-authoritative-host-signals).

Failed Assay lanes print a compact failure digest and preserve their verdict
and progress files under `.run-gate/failed/<lane>/<run_id>/` before a later
run can overwrite them. The archive keeps the latest ten per lane. Footprint
calibration can include completed profiled FAIL runs with `--include-failed`,
and `footprint --write --lane <name>` merges selected lanes into an existing
manifest. See [failure evidence and footprint updates](CONSUMERS.md#failure-evidence-and-selective-footprint-updates).

### Version probe

The script accepts `./run-gate.py --version` and prints exactly one
`run-gate rev N` identity line on stdout before exiting 0, with no stderr. Help,
usage, missing-argument, unknown-argument, and configuration diagnostics begin
with `RUN-GATE rev N — per-project gate entrypoint` as line 1. Lane runs now end with a one-line verdict summary; --json sends ordinary run output
to stderr and prints one result object on stdout. N is the script's `__revision__` copy-drift marker;
the wheel's SemVer remains a separate distribution identity.

This project's own `run-gate.toml` declares five lanes (dogfooding — see
"Built deltas" above):

1. **`selftest`** — the release gate (`cmru.toml [steps.run-tests]`
   points at it; a release cannot be tagged unless it passes). Zero
   install: `python3 -m pytest tests -q --cov=. --cov-branch` followed by
   the vendored `tools/coverage_gate.py`, scoped to `--source run-gate.py`
   alone — a diff-coverage floor at 100% on every executable line changed
   since `main`. The release contract deliberately does not claim a
   whole-project 100% floor; the separate `assay-r1` lane is the broader
   line-and-branch judge.
2. **`assay-r1`** (RG-55 wave, package P2, C2) — the stricter, SECOND
   judge: the selected worktree's assay source running the same
   test command, `assay.toml [lanes.r1]` judging `source_roots = ["."]`
   — the whole project, not just `run-gate.py`. `tools/coverage_gate.py`
   itself is IN SCOPE here (a controller ruling, RW-8: "100% on every
   changed line" means every changed line in this project, and the
   coverage command already measures it with `--cov=.`); `tests/` stays
   excluded, as it is on every assay lane. The comparison base is
   delegated to the invoking gate request (`judge.base_source =
   "request"`), never hardcoded.
3. **`assay-r2`** — mutation testing (R2) over the same scope, bare-host
   and serial (`jobs = 1`, HOST LOAD §6), budget 4h with a 20-minute
   `stall_timeout` on silence in its progress file. Run separately,
   pre-merge — never a member of `gate-full` (below), so a routine "does
   this gate clean" check never has to pay for a lane that can take
   hours.
4. **`assay-r3`** — the canary: `tools/canary-run.sh` breaks one
   provably-covered invariant (today: `duration_stats`'s median, flipped
   to a mean) in a disposable copy and asserts the exact test that
   encodes it goes red. Proves the suite is a real oracle, not a green
   checkmark that would pass on broken code too.
5. **`gate-full`** — the conjunction: `selftest` + `assay-r1` + `assay-r3`
   (r2 excluded for the reason above). Its `{base}` token forwards an
   explicit `--base REF` to `assay-r1`; use that form for a linked worktree
   without an upstream. Still not the release gate
   (`cmru.toml`'s own comment says why): `selftest` alone remains it,
   deliberately, for now.

## Intent — what problem this solves

Every project in this estate has a gate: the command whose green verdict means
"this change may ship". Today the *runnable mechanics* of that gate — the
`docker run` line, the cgroup slice, the mount pairs, the artifact-pin
verification, the clean-tree requirement — live in the **consumer's** config
(nyxloom's `nyxloom.toml [gates]` argv strings, cmru's `cmru.toml` step lists),
duplicated per consumer and per project, executed by machinery the project
itself never tests.

The measured cost of that arrangement, from a single review day (2026-08-20,
ciu checkpoint P07):

1. The committed gate argv had **never executed end-to-end** — it was validated
   with a substitute interpreter — and carried **three defects**, each invisible
   to a green 100%-coverage suite:
   - a missing `-e CGROUP_PARENT_DEV_GATES` (the image doesn't bake the
     var; env-passthrough cannot pass what does not exist),
   - an unconditional `systemctl` LoadState check that can never pass in a
     containerized context (the devcontainer ships a *shim* systemctl that
     exits 0 with advisory stdout),
   - `sha256sum -c` resolving the pin's bare filename against the wrong CWD.
2. The previous checkpoint (P06) had the same failure class: a `docker exec`
   argv pinned against a *fake* docker that real docker rejects (`--` executed
   as the in-container command, exit 127).
3. Manual gate runs require a four-trap recipe (cgroup env passthrough,
   dual-path mounts for worktree gitfiles, `safe.directory`, detached run form)
   that lives in AGENTS.md prose and is re-derived by every human who needs it.

**Root cause, stated once:** invocation mechanics that live in config strings
are code that nothing tests and only one consumer exercises. An argv proves
construction, not acceptance.

**The fix:** ONE small, tested program — `run-gate.py` — owned per project,
visible at the project root, carrying ALL of the mechanics. Every consumer
(nyxloomd, cmru, Buildkite, a CLI agent, a human) runs the same file:

```
./run-gate.py <lane>          # run one gate lane
./run-gate.py <lane> --base REF   # comparison base for a lane that delegates it
./run-gate.py doctor          # preflight: docker, slices, git, images, assay toolchains
./run-gate.py doctor --worktree B   # … for tree B's state, not this checkout's
./run-gate.py --check-env --worktree B   # … same redirect for the drift/toolchain report
./run-gate.py history [LANE]  # what each lane last did + what it typically costs
./run-gate.py history --json  #   … the same, machine-readable (this verb only)
./run-gate.py history --worktree B   # … for tree B's store, not this checkout's
./run-gate.py --list          # machine-readable lane inventory (for CI fan-out)
./run-gate.py --help          # usage(), incl. the tool revision
```

A defect fixed in the tool is fixed for every consumer at once, and the
four-trap recipe becomes executable code with its own test suite instead of
doctrine prose.

## Design

### One parser, argv for everyone (the tar-pit line)

The estate explicitly REJECTED a neutral `gates.toml` meta-schema that all
consumers parse (D-110): N parsers of a shared format is a meta-CI system —
schema drift, per-consumer bugs, governance forever. What we build instead:

- **`run-gate.py`** — the ONLY parser. A shared tool, developed here.
- **`run-gate.toml`** — per project, next to the script. Declarative lane
  definitions read by run-gate.py alone. No other program ever parses it.
- **Consumers speak argv.** nyxloom's `[gates]` entry shrinks to
  `argv = ["./run-gate.py", "<lane>"]`. Buildkite steps, cmru release gates,
  and humans use the identical invocation.

Same TOML bytes as the rejected design — completely different maintenance
surface, because exactly one program owns the schema (the
`pyproject.toml`/`assay.toml` pattern).

### Orchestration vs judgment — the assay split

`run-gate.toml` owns **orchestration**: which environment (container image,
cgroup slice, mounts), optional external pins, clean-tree policy, budgets, and
the command to run. It does NOT own quality **judgment**.

For projects that adopt **assay**, judgment (coverage floors, R-levels,
changed-line policy, isolation snapshots) stays in `assay.toml`, and the
`run-gate.toml` lane is a thin wrapper referencing the assay lane by name:

<!-- run-gate-config -->
```toml
schema_version = 1
[lanes.ciu]
kind = "assay"            # install selected ../assay + run the judge
assay_lane = "ciu"        # judgment policy lives in assay.toml — one registry each
environment = "tester-unified"

[environments.tester-unified]
mode = "ephemeral"
image = "tester-unified:local"
```

No duplicate lane registry: `run-gate.toml` = where/how it runs,
`assay.toml` = what counts as passing. Projects that cannot adopt assay
declare `kind = "command"` lanes and get everything except assay's judgment.

The split is kept honest by DERIVING assay facts instead of restating them.
run-gate asks the judge (`assay lanes --json`, assay ≥ 3.2.0) two questions
before it runs a lane: **what toolchain does this lane need from its
environment** (RG-25 — reported by `doctor`/`--check-env` instead of
surfacing as a mid-run `MISSING_EXTERNAL_TOOL`) and **does this lane take its
comparison base from the gate** (RG-26 — `judge.base_source = "request"`,
supplied with `./run-gate.py <lane> --base REF`). Neither becomes a
`run-gate.toml` key: a second spelling of a fact `assay.toml` already owns is
the drift this design exists to remove.

Asking has a price, stated rather than hidden: those questions are answered
INSIDE the lane's environment. `doctor` starts short read-only probes: one
inventory probe per environment+judge, one batched `command -v` probe per
environment for the fitness check, and one Assay state-root probe per assay
environment. `--check-env` runs the first two probes only. An assay-lane
invocation adds one state-root probe for its lane; `--dry-run` prints that
probe without running it. They judge nothing, write nothing, and never start
your judged lane; a project with no `kind = "assay"` lane starts none of
them.

For ephemeral environments, these probes receive the configured container
user and RUN_GATE_EXTRA_MOUNTS just like the judged lane, so the preflight
sees the same mounted tools and access permissions.

### Environment mechanics the tool must own (the hard-won list)

These are the exact behaviors whose absence caused measured failures; they are
the tool's reason to exist and MUST be implemented + tested:

- **Cgroup placement:** resolve the slice ONLY from
  `$CGROUP_PARENT_DEV_GATES` (no literal, no fallback — absent is a hard
  error, AGENTS §4.2a), pass it BOTH as `--cgroup-parent` AND `-e` into the
  container (suites read it ambiently). Require `LoadState=loaded` plus a
  non-empty `FragmentPath` where systemd is reachable; containerized contexts
  rely on the outer gate launcher's host-side placement check.
- **Path namespaces:** derive the physical repo root from
  `/proc/self/mountinfo` (the bind mount whose mount point contains the repo;
  cmru `tester-gate` precedent — never from `ciu.env`, whose generated values
  have been observed stale); dual-mount physical AND devcontainer
  paths so worktree gitfiles resolve — when no alias is derivable (bare
  host, `phys == repo`) the lane refuses instead of silently collapsing to
  one mount; declare the second view via
  `$RUN_GATE_MOUNT_ALIAS='<host>=<namespace>'`;
  `git config --global safe.directory '*'`
  inside the gate container.
- **Mounts (RG-86):** an ephemeral container sees ONLY the judged worktree
  (all its own files, git-ignored ones included) plus the git common dir
  (read-write, ciu v8 SPEC S16.4.9), `<repo>/.run-gate` for assay state, and
  explicit `RUN_GATE_EXTRA_MOUNTS`; never the main checkout or other
  worktrees. `<common>/config` is overlaid with a per-run credential-free
  copy. A plain (main) checkout is refused unless `--allow-main-checkout` /
  `RUN_GATE_ALLOW_MAIN_CHECKOUT=1` (WARNs). `mode = "exec"` lanes are
  unaffected. Isolation comes from mounting less; run-gate knows no
  project's secret file names.
- **Assay source or artifact:** internal lanes omit `assay_command` and
  `pins`; run-gate installs `assay/` from the selected worktree in the lane
  environment and the resulting verdict records the runtime version and
  source commit. Run-gate matches full SHA-1 or SHA-256 commit IDs; Assay's
  P22 snapshot source, used by high-rigor snapshot lanes, still requires
  SHA-1 object storage. External
  consumers may supply `assay_command` plus sha256/
  version pins; those are verified from the pin file's directory and the
  declared version is checked in-lane.
- **Env forwarding is declared, never implicit (RG-23):** a container/exec
  lane forwards `$CGROUP_PARENT_DEV_GATES` (the tool's own
  infrastructure) plus exactly the environment's `forward_env` list. The
  early hardcoded `MOCK_MODE`/`RUN_LIVE_TESTS` pair is GONE — consumers
  relying on it must migrate (CONSUMERS.md "BREAKING CHANGE"), because its
  absence produces a false GREEN, not an error. `--check-env` sweeps the
  project's Python for env reads no lane forwards or requires, including
  reads wrapped in the project's own helper functions.
- **Clean tree:** refuse a dirty judged tree by default (assay lanes get this
  from assay; command lanes get it from the tool) — a gate over uncommitted
  state is not evidence.
- **Effective tree:** `--worktree` doesn't just redirect checks — the lane
  EXECUTES in the selected tree (assay cd, pin verification, artifacts,
  host-lane cwd relocate; SPEC R-21), and lane plus inherited configuration
  are loaded from that same tree (RG-47/RG-65). The header prints
  `run-gate: config: <path>`; run history stores the selected config path and
  SHA-256, plus the nearest central config path and SHA when one was used.
  Exec-mode runner selection follows the same tree boundary (see the
  [design rationale](docs/DESIGN-GUIDE.md#the-runner-belongs-to-the-judged-worktree)).
  Judging checkout A while pointed at worktree B is the silent false-PASS
  class this kills. The missing-runner remedy uses
  `ciu up --dir <test-runner stack> --deploy --healthcheck`, which requires
  CIU 7.15.2 or newer (CIU-124). The READ-ONLY verbs follow the same config
  selection;
  `doctor`/`--check-env --worktree B` report B's git identity, host-lane
  view, and toolchain fitness, never the invoking checkout's under B's name
  (SPEC `R-37`, RG-30).
- **Run form:** detached container with Docker's init reaper + wait + logs
  (survives terminal loss and reaps orphaned descendants); the gate's exit
  status is the judged job's own — no wrapper/pipe masking.
- **Recovery records are ownership boundaries:** if a container lane finds
  an inflight record written by the exec runner, the live invocation refuses
  with exit 2 and starts nothing. It never treats “foreign” as “absent,” even
  with `--fresh`, because starting a replacement would overwrite the only
  recovery pointer to the still-owned runner (SPEC `R-39f`).
  `--fresh` is available only for ephemeral-container lanes; host and exec
  lanes require the named runner's lifecycle owner to confirm the run has
  stopped before its stale recovery record can be removed.
  Before imported Assay inventory or sequence admission, Run-Gate checks the
  requested lane's recovery record and every sequence node and member. A
  record left under a lane now configured as a sequence, or owned by a foreign
  runner, refuses before members start.
  A container-runner record left after the lane changes to host or exec mode
  also refuses before inventory or execution; the prior container's
  lifecycle owner must confirm it has stopped before the recovery record is
  removed.
  Run-gate uses ERROR 2 for configuration and infrastructure failures,
  and NOT_RUN 3 when a precondition prevents the judge from starting.
- **Gate-safe paths:** `{worktree}` is substituted textually into consumer
  shell strings, so a judged tree at a path with whitespace or shell
  metacharacters is refused up front (every lane kind) instead of
  word-splitting or executing downstream.
- **Verdict discipline:** print WHERE the verdict artifact lives; never bury
  it in a stream a consumer might truncate.
- **Lane cost is measured, not remembered (RG-27):** every run leaves a
  record in a per-(judged worktree × project) `.run-gate/history.json` — a
  `latest` slot holding the most recent invocation whatever happened to it,
  and a bounded per-commit trend series that only trustworthy measurements
  join (completed, clean tree, no rebase in flight, real commit). Read it
  with `./run-gate.py history [LANE] [--worktree PATH] [--json]` — the read
  scope follows `--worktree` exactly as the write scope does, and the answer
  names the tree it describes. run-gate MEASURES; the
  rigor/defer policy built on the numbers belongs to whoever reads them
  (CONSUMERS.md "What each lane costs"; SPEC `R-36`).
- **Lane cost is PROFILED too, when a cgroup-profiler daemon is reachable
  (RG-55):** peak memory, +baseline, hot-set p90 (DAMON), CPU cores, and
  memory-full stall join `history`'s own duration series (schema 2, SPEC
  `R-36j`) — precisely from the daemon (`cgprofile-host-daemon`, `docker
  exec ... cgprofile ctl ...`), or a coarser in-lane profile when it is
  absent: cgroup sampling for container/exec lanes and child-specific
  `wait4()` accounting for bare-host lanes. Bare-host daemon targeting uses
  a Docker object only after its mount namespace proves it is this process's
  container; a hostname match alone is never identity. Never nothing, unless
  profiling is disabled outright
  (`RUN_GATE_PROFILE=off`, `[profile] enabled = false`, or a lane's own
  `profile = false`). `./run-gate.py footprint [LANE] [--json] [--write]`
  distills that history into a COMMITTED `run-gate.footprint.json` —
  budgets other projects and `doctor` can compare against, not just this
  invocation's own number (SPEC `R-44`).

### Distribution — symlink inside vbpub, copy outside

The tool is a **mini-project of the vbpub monorepo** (this directory): its own
tests, its own revision discipline (`__revision__ = N` in-file, printed by
`--help`). Consumption:

- **vbpub-internal projects** (ciu, cmru, assay, nyxloom, tester-unified):
  `run-gate.py` at the project root is a **relative symlink** to
  `../run-gate-project/run-gate.py`. One edit updates all; git tracks symlinks.
- **External repos** (dstdns, groop): **copy** the file in; the in-file
  `__revision__` is the drift detector (estate sweeps compare it against this
  directory's source of truth). A cross-repo symlink dies on fresh clone.
- **Why not a pip wheel:** chick-egg. A fresh clone of any repo must be able
  to run its gate with zero release artifacts available (same reasoning that
  moved assay from per-repo vendored pyz blobs to being baked into the
  tester-unified image, built from in-repo source via the cmru dependency
  chain — D-110.2). A single-file stdlib-only script needs no install step.

Consequence: **run-gate.py is stdlib-only** (tomllib, argparse, subprocess,
hashlib, pathlib). Any richer dependency belongs in the environments it
launches, not in the launcher.

### Future: async long lanes

Mutation campaigns and fuzzing become additional `run-gate.toml` lanes with
large budgets, executed asynchronously by Buildkite agents on remote hosts —
which call the SAME `./run-gate.py <lane>`. nyxloomd, the operator, and a
controller session are equal triggers. (Backlogged: assay B009 forward note;
carved after the dstdns config-cutover.)

## Decision trail

- dstdns `nyxloom-trove/decisions.md` **D-110** (gate layering, assay
  distribution, assay.toml role, async lanes) and **D-111** (+amendment:
  run-gate.py/run-gate.toml naming, this home, one-parser rationale,
  distribution model).
- ciu backlog **CIU-40** (build + first adoption), assay backlog **B009**
  (assay.toml role docs + image-baked distribution), assay **B008** (the
  merge-tip R1 base-resolution finding from the same review day).
- vbpub `AGENTS.md` "Manual tester-unified gate runs — the four traps" — the
  prose this tool turns into tested code (the section gains a pointer here
  when the tool ships, and per-project AGENTS.md name `run-gate.py` as the
  canonical starting point IN the adoption carve, never before).

The standalone parser's help, usage, and argument errors begin with `RUN-GATE rev <revision> — per-project gate entrypoint`. The revision is the script's existing `__revision__` source, so a zero-install checkout can identify the exact launcher without importing another project.
