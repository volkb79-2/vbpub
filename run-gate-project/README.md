# run-gate — the per-project gate entrypoint

**Status:** BUILT (P01, 2026-08-22). First consumer: **nyxloom** (controller
amendment A1 — ciu was under parallel development and is DEFERRED; its assay
lane ships construction-tested with live proof at its adoption). This README describes the current user-facing surface; **`SPEC.md` is the
normative implementation contract**. The installed wheel's SemVer is the
public tool identity. Build-and-adopt handoff: `HANDOFF-P01-build-and-adopt-ciu.md`;
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
   in the current working directory. The installed console command carries
   no project identity in its executable path. With `--worktree`, run-gate first
   preserves that project's path relative to its Git toplevel, then reads
   `run-gate.toml` and the nearest `run-gate.root.toml` within that Git tree.
   A missing target `run-gate.toml` is an error; the invoking checkout's
   lanes are never substituted (see the
   [design rationale](docs/DESIGN-GUIDE.md#a-worktree-is-the-complete-judgment-boundary)).
   Invoke Run-Gate from the project directory that owns `run-gate.toml`.
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
   table — `memory`, optional `memory_max` placement ceiling for daemon-backed
   exec/bare-host lanes, `memory_swap`, `pids` (`--pids-limit`), `cpu_weight`/`io_weight` advisory,
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
   `judge.mutation.budget_per_candidate` + this key; progress output is
   scoped to the latest Assay run header, with rates and ETAs based on that
   run's candidate events ([design rationale](docs/DESIGN-GUIDE.md#progress-is-scoped-to-the-current-assay-run)),
   `profile` (RG-55:
   `false` to opt a lane out of profiling entirely, or a table
   `{enabled, damon}` overriding `[profile]`'s own defaults — SPEC
   `R-43h`); and `resource_observer = "cgprofile"` for an Assay or command
   lane in any resolved `mode = "ephemeral"` environment, which exposes only
   the read-only observer directory to recover hidden cgroup ancestors for native
   R2. These lanes declare local memory and PID ceilings and cannot add arbitrary
   host mounts. Before launch, Run-Gate requires the daemon to advertise observer
   contract 2, the configured socket path, and `listening = true`. A command wrapper that
   invokes Assay must preserve the inherited observer-path variable for its
   Assay process. It does not expose
   `ctl.sock` or change the lane's private cgroup namespace. See the
   [R2 observer design](docs/DESIGN-GUIDE.md#private-namespace-r2-uses-a-read-only-cgroup-observer)
   and [consumer setup](CONSUMERS.md#native-r2-resource-observer).
   The project-level `[profile]` table also accepts
   `transport = "auto" | "exec" | "socket"`, `socket_path`, and bounded
   `admission_wait`; `auto` uses the mounted Unix socket only after a
   successful version probe and otherwise uses the Docker-exec carrier.
   Daemon-backed exec and bare-host lanes may request measured memory
   placement; ephemeral lanes use the selected, verified gate cgroup parent.
   The inherited tester environment resolves it from `cgroup_slice`, then
   `cgroup_slice_env`, then `$CGROUP_PARENT_DEV_GATES`; a missing selected
   value is a hard error, never an unconfined Docker launch. Long-running
   application stacks receive `$CGROUP_PARENT_DEV_BACKGROUND` separately
   through the environment's declared `forward_env`.
   A daemon watch is one reader per session and records readings/verdicts;
   `killed` follows the declared stall path and `reported` is one warning,
   never a profiling verdict. When the daemon/watch is unavailable, the
   in-process assay watch bounds the baseline heartbeat and the plan's
   calibrated cadence and pauses its idle clock while host memory PSI `full avg10` is above 5. Before a
   daemon-backed lane starts, R-45 admission checks the verified gates slice;
   any wait is bounded by `admission_wait` (10m default), then the lane
   proceeds with one warning rather than being refused or changing its
   verdict. `--dry-run` reports `would-wait` without waiting, reserving
   capacity, starting a profiler session, or starting the lane. This is an
   observational headroom check, not an atomic reservation; see the
   [admission design rationale](docs/DESIGN-GUIDE.md#bounded-headroom-admission-is-observational-not-atomic).

## Gate and evidence

### Closed results and explicit runner modes

run-gate retains the judge's raw exit code in the result and returns one of
five process statuses: **PASS 0**, **FAIL 1**, **ERROR 2**, **NOT_RUN 3**, or
**BUDGET_EXCEEDED 4**. A command's non-zero code is a FAIL, including pytest
5 for an empty collection; an ERROR or NOT_RUN result names its reason. Use
`--json` to read `verdict`, raw `exit_code`, `reason`, `log_path`, and assay
outcome and admission as structured result fields. The full mapping is in
[the CLI contract](SPEC.md#2-cli-contract).
For an Assay lane, run-gate verifies that the process exit code equals the
verdict artifact's `exit_code`, and that the artifact's outcome uses Assay's
closed exit table (PASS 0, FAIL 1, ERROR 2, NO_MEASUREMENT 3,
BUDGET_EXCEEDED 4, INCONCLUSIVE 5). A missing or inconsistent value is
infrastructure ERROR with the raw process code and Assay outcome retained; a
PASS verdict alone cannot override a failed runner.
NOT_RUN reasons are closed: realness-mismatch, service-down,
environment-down, environment-mismatch, env-missing, external-missing,
external-down, dirty-tree, no-headroom, lock-busy, no-base, judge-floor,
judge-digest, provenance-mismatch, and state-mount.

Container lanes receive Git's `safe.directory=*` through per-process
`GIT_CONFIG_COUNT` variables, appended after any entries the runner already
set. Run-Gate writes no global config or lock file, so its setup does not
contend on a shared config lock. This needs **Git 2.38 or newer** in the lane
image: older Git ignores the entry, and the lane refuses up front with exit 2
naming the floor instead of failing later as "dubious ownership". Live
concurrent exec-mode acceptance is the one remaining RG-99 item. Use one user
per test container. See the
[design rationale](docs/DESIGN-GUIDE.md#concurrent-exec-lanes-use-read-only-git-safety)
and [consumer guidance](CONSUMERS.md#exec-mode-git-safety).

Every `[environments.<name>]` declares `mode = "ephemeral"`, `"exec"`, or
`"host"`. Names carry no behavior: `host` and `bare-host` are ordinary names
that exist only when declared — `host` is a container only when its
declaration says `mode = "ephemeral"`, and a "no container" environment is
`[environments.bare-host]` with `mode = "host"`, which runs on the invoking
host. A lane naming either one undeclared is refused with that declaration
and the migration command below. Existing configs can be migrated one at a
time without losing comments:

```console
run-gate migrate-modes run-gate.toml
run-gate migrate-modes run-gate.root.toml  # when this repo has one
```

The tool refuses a missing mode. Review the diff, then migrate wrapper
assumptions separately. See the [mode rationale](docs/DESIGN-GUIDE.md#runner-modes-name-the-runtime-contract) and the
[consumer migration steps](CONSUMERS.md#closed-results-and-runner-mode-migration).

Ephemeral container environments may also declare `image_env = "NAME"`. If
that environment variable is set, its non-empty value selects the image for
that invocation; if unset, the declared `image` remains in effect. This lets a
release gate use a receipt-specific image without replacing a shared tag such
as `tester-unified:local`. Run-Gate prints the override source and effective
image, then records the declared image, selected value, and actual Docker image
ID (or why it could not be read) in `run-gate history --json`. The override is
read by the Run-Gate process that starts the container and is not forwarded
into it. See the
[image identity rationale](docs/DESIGN-GUIDE.md#per-invocation-image-identity)
and [consumer example](CONSUMERS.md#per-invocation-image-selection).

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

For Assay lanes, `NO_MEASUREMENT/CGROUP_OBSERVATION_UNAVAILABLE` maps to gate
`ERROR`: the R2 measurement could not be made safely, so the gate must not
report a product failure. Before applying that mapping, run-gate checks the
claims and external evidence in the verdict: an independent `BUDGET_EXCEEDED`
remains `BUDGET_EXCEEDED`, and an independent `FAIL`, `INCONCLUSIVE`, or other
`NO_MEASUREMENT` remains `FAIL`. Only an R2 observer refusal alongside passing
claims and evidence becomes infrastructure `ERROR`; malformed claim or
evidence records also become `ERROR`. Other Assay `NO_MEASUREMENT` and
`INCONCLUSIVE` outcomes retain their existing FAIL mapping. See the
[Assay result rationale](docs/DESIGN-GUIDE.md#assay-unavailable-observation-is-an-infrastructure-error).
This v16 reason-code contract requires Assay 10.0.0 or newer.

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
Run-Gate asks for inventory only when the answer needs it. Help, `--list`,
an omitted lane and unrelated local lanes never probe, so they stay usable
while a persistent judge is stopped; `--list` says on stderr that the import
was not queried and keeps its three-column machine-readable output. `doctor`,
`--check-env`, and `history` / `footprint` over every lane do resolve it, and
refuse with a remedy if the judge is unavailable rather than report a clean
result or write a manifest that omits imported lanes.

An assay lane can pass `--reuse-from PATH`, repeatable `--rejudge ID`, and
`--rejudge-outcome BUCKET` to one selected Assay run. A command lane accepts
arguments after `--` only when it declares `accepts_args = true`; sequences
and composite commands refuse them. See [selective requests](CONSUMERS.md#selective-lane-requests)
and the [request-scope rationale](docs/DESIGN-GUIDE.md#selective-requests-stay-with-the-selected-lane).

Every assay lane receives `--resume`, a worktree-local progress file, and a
durable `--state-dir` under the checkout that owns the shared Git directory.
The default state root is `<checkout>/.run-gate`; container environments can
declare `state_root` for a different durable mount. For a fresh ephemeral
container or bare-host lane, Run-Gate creates its default root when absent
and refuses a symlink or other object in its
place, then checks that root and the deepest existing directory on the keyed
state path as the lane user. An explicitly configured `state_root` must
already be mounted and writable. For an ephemeral or bare-host lane, `doctor`
reports an uncreated default root as unverified until the lane creates and
probes it; an exec runner is probed directly. Run-Gate also derives
`GIT_CEILING_DIRECTORIES` and the test command's temporary path
(`RUN_GATE_TMPDIR`, and `TMPDIR` when safe — see below) from the verified
state path, so a guessed container path cannot move tests into an unmapped private
`/tmp`. An unavailable or unwritable state area is
NOT_RUN/`state-mount`, not a test failure. The durable-state option requires
Assay 5.2.0. Read the
[state contract](CONSUMERS.md#resume-progress-and-durable-assay-state) and
[mount rationale](docs/DESIGN-GUIDE.md#assay-resume-state-needs-an-environment-owned-mount)
before removing an ephemeral worktree.

Assay allocates its own snapshot scratch under `TMPDIR`, and that scratch must
be outside the judged repository. Run-Gate therefore never makes the default
in-repo `<checkout>/.run-gate` the Assay judge's `TMPDIR`: a state root inside
the judged worktree is exported only as `RUN_GATE_TMPDIR`, which Assay ignores,
so snapshot-isolated R1–R3 lanes run with the default root and every lane
keeps the same state layout. A root outside the judged worktree (an explicit
`state_root`, or the main checkout's root seen from a linked worktree) is also
exported as `TMPDIR`, so a lane that forwards `TMPDIR` by `env_passthrough`
is unchanged. To hand the verified path to the test command in the in-repo
case, map it in `assay.toml`; see the
[consumer recipe](CONSUMERS.md#resume-progress-and-durable-assay-state) and the
[scratch rationale](docs/DESIGN-GUIDE.md#assay-resume-state-needs-an-environment-owned-mount).

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

`run-gate --version` prints exactly one `run-gate X.Y.Z` line from installed
wheel metadata, exits 0 without loading project configuration, and writes
nothing to stderr. Help, usage, argument errors, and configuration diagnostics
identify the same installed version. Missing or malformed package metadata is
a refusal; Run-Gate does not invent a version from source files. Lane runs end
with a one-line verdict summary; `--json` sends ordinary run output to stderr
and prints one result object on stdout.

This project's own `run-gate.toml` declares five lanes (dogfooding — see
"Built deltas" above):

1. **`selftest`** — the release gate (`cmru.toml [steps.run-tests]`
   points at it; a release cannot be tagged unless it passes). It owns a
   capped `tester-unified` container and runs from the selected worktree's
   project directory. When CMRU releases this package, the run-tests step first
   builds the prepared-version wheel and tester image from those exact bytes,
   then writes a commit-bound receipt. The lane checks that image identity
   before testing. Wheel/image preparation requires CMRU's protected
   `CMRU_RELEASE_GATE_COMMIT` to match the selected checkout; a manual command
   cannot stamp a stable release version onto an unrelated tree. In `tester-unified`, it runs
   `/opt/tester-venv/bin/python -m pytest tests -q --cov=. --cov-branch`, followed by
   the vendored `tools/coverage_gate.py`, scoped to `--source run_gate.py`
   alone — a diff-coverage floor at 100% on every executable line changed
   since the newest reachable stable `run-gate-vX.Y.Z` release tag. On the
   first stable release, when no stable Run-Gate tag exists, it reports the
   diff check as `SKIPPED` because there is no released source baseline. The
   release transaction's preparation accepts a first release only when origin
   shows no stable tag, and refuses a checkout missing origin's release tags
   ([why](docs/DESIGN-GUIDE.md#the-release-gate-tests-the-published-wheel)); the
   separately run `assay-r1` campaign remains the whole-source line-and-branch
   qualification before merge. A stable tag that exists but is unreachable is
   still an error. This immutable baseline stays meaningful after merge; using `main` there would
   resolve the merge base to the candidate itself and hide the release's
   source changes. The comparison detects renames before filtering to
   `run_gate.py`, so replacing the retired compatibility symlink does not
   make the whole module appear new; see the [coverage design](docs/DESIGN-GUIDE.md#release-coverage-uses-a-fixed-tag-baseline-and-follows-content-across-renames)
   and [consumer adoption note](CONSUMERS.md#release-coverage-baseline).
   The release contract deliberately does not claim a
   whole-project 100% floor; the separate `assay-r1` lane is the broader
   line-and-branch judge. After the gate and tag, CMRU rebuilds the wheel and
   refuses publication unless its version and SHA-256 still match the receipted
   wheel and tester image; see [the exact-wheel release design](docs/DESIGN-GUIDE.md#the-release-gate-tests-the-published-wheel).
2. **`assay-r1`** (RG-55 wave, package P2, C2) — the stricter, SECOND
   judge in its own `tester-unified` container: the selected worktree's
   assay source running the same test command, `assay.toml [lanes.r1]`
   judging `source_roots = ["."]`
   — the whole project, not just `run_gate.py`. `tools/coverage_gate.py`
   itself is IN SCOPE here (a controller ruling, RW-8: "100% on every
   changed line" means every changed line in this project, and the
   coverage command already measures it with `--cov=.`); `tests/` stays
   excluded, as it is on every assay lane. The comparison base is
   delegated to the invoking gate request (`judge.base_source =
   "request"`), never hardcoded.
3. **`assay-r2`** — mutation testing (R2) over the same scope in a private
   `tester-unified` container, capped at 3 CPUs with one mutant worker.
   The worker count is one because concurrent candidates would share the
   tester cgroup's resource-event counters. Its `resource_observer = "cgprofile"` opt-in mounts only the
   daemon's read-only resource-observer directory after checking the daemon's
   contract-2 version advertisement; the control socket is not
   exposed, and the lane never uses `--cgroupns=host`. This lets Assay read
   the complete host cgroup ancestry required by B145 while keeping the
   mutation campaign inside its own container. The 4h budget is a lane-level
   outer bound, not a per-candidate performance verdict. Run separately,
   pre-merge — never a member of `gate-full` (below), so a routine "does
   this gate clean" check never has to pay for a lane that can take hours.
4. **`assay-r3`** — the canary: `tools/canary-run.sh` breaks one
   provably-covered invariant (today: `duration_stats`'s median, flipped
   to a mean) in a disposable copy and asserts the exact test that
   encodes it goes red in its own `tester-unified` container. Proves the
   suite is a real oracle, not a green
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

**The tool:** one tested Run-Gate wheel provides the installed `run-gate`
command and owns all lane mechanics. Every consumer (nyxloomd, CMRU,
Buildkite, a CLI agent, a human) invokes the command from the project's
directory:

```
run-gate <lane>          # run one gate lane
run-gate <lane> --base REF   # comparison base for a lane that delegates it
run-gate doctor          # preflight: docker, slices, git, images, assay toolchains (sequence lanes: each member's environment)
run-gate doctor --worktree B   # … for tree B's state, not this checkout's
run-gate --check-env --worktree B   # … same redirect for the drift/toolchain report
run-gate history [LANE]  # what each lane last did + what it typically costs
run-gate history --json  #   … the same, machine-readable (this verb only)
run-gate history --worktree B   # … for tree B's store, not this checkout's
run-gate --list          # machine-readable lane inventory (for CI fan-out)
run-gate --help          # usage(), incl. the installed SemVer
```

A defect fixed in the tool is fixed for every consumer at once, and the
four-trap recipe becomes executable code with its own test suite instead of
doctrine prose.

## Design

### One parser, argv for everyone (the tar-pit line)

The estate explicitly REJECTED a neutral `gates.toml` meta-schema that all
consumers parse (D-110): N parsers of a shared format is a meta-CI system —
schema drift, per-consumer bugs, governance forever. What we build instead:

- **`run-gate`** — the ONLY parser. A shared tool distributed as a wheel.
- **`run-gate.toml`** — per project, in its project directory. Declarative
  lane definitions read by the installed CLI alone. No other program parses it.
- **Consumers speak argv.** nyxloom's `[gates]` entry shrinks to
  `argv = ["run-gate", "<lane>"]`. Buildkite steps, cmru release gates,
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
supplied with `run-gate <lane> --base REF`). Neither becomes a
`run-gate.toml` key: a second spelling of a fact `assay.toml` already owns is
the drift this design exists to remove.

Asking has a price, stated rather than hidden: those questions are answered
INSIDE the lane's environment. `doctor` performs one inventory probe per
environment+judge, one batched `command -v` probe per environment, and at most
one Assay state-root check per assay environment. It reports a creatable but
absent default `.run-gate` root as `[SKIP]` in `ephemeral` and `host`
environments; a known invalid parent fails. Doctor does not create the root
or launch a lane-user probe. The first live lane in those environments
creates it and checks access. A configured `state_root` or an `exec`
environment is checked through its declared runner, never by host-statting a
container path. `--check-env` runs only the inventory and tool probes. A live
assay invocation adds one state-root check for its lane; `--dry-run`
discloses that check without running it. These checks judge nothing and write
nothing into your tree, and they never start your judged lane; a project with
no `kind = "assay"` lane starts none of them.

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
  `$RUN_GATE_MOUNT_ALIAS='<host>=<namespace>'`. Run-Gate supplies
  `safe.directory=*` through per-process Git environment variables; no global
  config write is needed.
- **Mounts (RG-86):** an ephemeral container sees ONLY the judged worktree
  (all its own files, git-ignored ones included) plus the git common dir
  (read-write, ciu v8 SPEC S16.4.9), `<repo>/.run-gate` for assay state, and
  explicit `RUN_GATE_EXTRA_MOUNTS`; never the main checkout or other
  worktrees. `<common>/config` (and every `config`/`config.worktree` at any
  depth under `modules/`) is overlaid with a per-run credential-free copy.
  `<common>/worktrees/` is mounted read-only with its real contents and the
  judged worktree's own admin dir read-write on top, so an in-container `git
  worktree prune` fails (EROFS), `gc` keeps every sibling's objects and
  `branch -D` of a sibling's branch is refused; `git worktree add` cannot
  create a new admin dir inside the container. The common `hooks/` directory
  is overlaid with an empty read-only directory and common `info/` is mounted
  read-only, so a tester cannot plant a host hook or edit host exclude metadata.
  See the [shared Git mount design](docs/DESIGN-GUIDE.md#shared-git-metadata-keeps-host-hooks-inert).
  A plain (main) checkout is refused unless `--allow-main-checkout` /
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
  reads wrapped in the project's own helper functions. It names unreadable
  files and marks the scan incomplete; see the
  [design rationale](docs/DESIGN-GUIDE.md#env-drift-does-not-certify-unreadable-source).
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
  with `run-gate history [LANE] [--worktree PATH] [--json]` — the read
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
  `profile = false`). `run-gate footprint [LANE] [--json] [--write]`
  distills that history into a COMMITTED `run-gate.footprint.json` —
  budgets other projects and `doctor` can compare against, not just this
  invocation's own number (SPEC `R-44`).

  The v1.1 client opens one daemon watch per session. A daemon `killed`
  liveness verdict follows the existing stall exit path; `reported` is one
  warning and never changes the test verdict. The stopped session summary
  preserves a verified kill if the lane exits before its stream event is
  read. Shared-scope lanes without placement request reporting rather than
  authorizing a shared-cgroup kill. If the daemon or its watch is
  unavailable, the in-process progress fallback uses the assay stream's
  calibrated cadence and pauses its idle clock while host memory PSI is above
  the threshold. Host contention is therefore disclosed scheduling state,
  never a test result. Before a daemon-backed lane starts, gates-slice
  admission waits for headroom and then proceeds at the configured bound; it
  does not refuse a lane merely because the host was busy.

  When an exec or bare-host lane requests daemon placement, Run-Gate holds a
  token-bearing wrapper outside the lane body until the daemon confirms the
  leaf and reads back the requested limits. An explicit refusal or incomplete
  read-back aborts the wrapper and returns an infrastructure refusal before
  the body runs; no mutation candidate or candidate verdict is produced. If
  the profiler is unavailable before it returns a placement result, the
  documented R-36h non-verdict fallback still applies. See the
  [placement launch rationale](docs/DESIGN-GUIDE.md#explicit-placement-is-a-pre-body-launch-gate).

### Distribution — installed wheel

Run-Gate is distributed and invoked as the `run-gate` console command from
its installed wheel. Install the released wheel into each environment that
drives lanes. Run `run-gate --version` to read its SemVer compatibility
identity. There is no consumer script to copy, symlink, or synchronize.
The [design guide](docs/DESIGN-GUIDE.md#semver-names-the-installed-tool)
explains why release SemVer replaces the old copied-script revision marker.

#### Breaking migration in 24.0.0

Replace any copied `run-gate.py` file or symlink with the released Run-Gate
wheel. Invoke `run-gate <lane>` from the project directory that owns
`run-gate.toml`, and use `run-gate --version` to check the installed SemVer
floor. Integer `__revision__` values and saved `revision` fields remain only
as provenance; they are not compatibility versions.

Project lane declarations remain in `run-gate.toml`; the installed command
loads that file from the current project directory. Internal revision values
may appear in persisted history records to identify their writer, but they do
not identify the installed package or act as an upgrade floor.

Install the published artifact with pip:

```console
python -m pip install run_gate-<version>-py3-none-any.whl
run-gate --version
```

### Future: async long lanes

Mutation campaigns and fuzzing become additional `run-gate.toml` lanes with
large budgets, executed asynchronously by Buildkite agents on remote hosts —
which call the SAME `run-gate <lane>`. nyxloomd, the operator, and a
controller session are equal triggers. (Backlogged: assay B009 forward note;
carved after the dstdns config-cutover.)

## Decision trail

- dstdns `nyxloom-trove/decisions.md` **D-110** (gate layering, assay
  distribution, assay.toml role, async lanes) and **D-111** (+amendment:
  original run-gate.py/run-gate.toml naming, this home, one-parser rationale,
  distribution model; the script distribution was retired by RG-94).
- ciu backlog **CIU-40** (build + first adoption), assay backlog **B009**
  (assay.toml role docs + image-baked distribution), assay **B008** (the
  merge-tip R1 base-resolution finding from the same review day).
- vbpub `AGENTS.md` "Manual tester-unified gate runs — the four traps" — the
  prose this tool turns into tested code (the section gains a pointer here
  when the tool ships, and per-project AGENTS.md name `run-gate` as the
  canonical starting point IN the adoption carve, never before).

The installed parser's help, usage, and argument errors begin with `RUN-GATE <version> — per-project gate entrypoint`. The version comes from wheel metadata; missing metadata is an error.
