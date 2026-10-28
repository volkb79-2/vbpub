# run-gate — consumer & adoption guide

How a project adopts the installed `run-gate` CLI, how partner tools plug in, and what the
lane declarations look like per project type. Companion to `README.md`
(design authority) and **`SPEC.md` (normative contract)**. BUILT as of P01
(2026-08-22); first adopter nyxloom.

Two sibling guides answer the questions this file does not: **`LANE-AUTHORING.md`**
(what makes a lane good — which tools belong in run-gate, in assay, or inside
the suite; determinism, exit-code truth, budgets, per-language recipes, the
best-practice checklist and the open TODOs) and **`REMOTE-LANES-BUILDKITE.md`**
(enrolling a remote host as a Buildkite agent, triggering and collecting from
this host, the image on remote hosts, and the ciu/run-gate/assay seams).

## The adoption steps (any project)

1. **Install the wheel.** Install the chosen released SemVer into the
   environment that drives gates. For example:
   `python -m pip install https://github.com/volkb79-2/vbpub/releases/download/run-gate-v24.0.0/run_gate-24.0.0-py3-none-any.whl`.
   Confirm it with `run-gate --version`. Do not create or copy a project-local
   script or symlink.
2. **Declare lanes** in `run-gate.toml` in the project directory (final schema
   below — parsed by the installed `run-gate` CLI ONLY; no other tool may read
   this file). Every
   `[environments.<name>]` needs an explicit `mode`. When upgrading an older
   config, run `run-gate migrate-modes run-gate.toml` and, if present,
   `run-gate migrate-modes run-gate.root.toml`; review and commit the
   text diff. Shared environment facts do NOT belong here if a repo-root
   `run-gate.root.toml` already defines them (see below). Central config
   lookup stops at the project's Git toplevel, so a nested standalone
   repository needs its own `run-gate.root.toml` to share environment facts.
   `bare-host` and `host` are ordinary environment names that exist only when
   declared; to preserve the old bare-host behavior, declare
   `[environments.bare-host] mode = "host"` (and a container `host` as
   `[environments.host]` with `mode = "ephemeral"` and an `image`). A lane
   naming either undeclared is refused with exactly that declaration and the
   `run-gate migrate-modes <file>` command that adds it; for a lane inherited
   from `run-gate.root.toml` the refusal names the root file.
3. **Point consumers at lanes** — e.g. nyxloom's `[gates.<name>]`:
   `argv = ["bash", "-c", "cd {worktree}/<proj> && run-gate --worktree {worktree} <lane>"]`.
   `{worktree}` is substituted textually into a shell string, so judged
   trees must live at gate-safe paths (letters/digits `_ . / -`; no spaces or
   shell metacharacters) — the gate refuses any other path, every lane kind.
   The consumer adds NO test logic of its own — no suite argv, no lane
   sequencing, no coverage flags. All test definitions live in
   `run-gate.toml` (the SSOT); if multiple sub-lanes must run together,
   declare a conjunction lane in `run-gate.toml` (see below) and point the
   consumer at it. Keep `asserts`/`timeout_seconds` as the daemon's own
   policy.
3a. **Certify the linkage (RG-2).** Add ONE project test that runs
   `run-gate validate-pointers <your-consumer-document>` (exit 0 = every
   pointer names a lane the SSOT really declares, with the canonical
   `--worktree {worktree}` shape). This is what makes a renamed lane go RED
   at TEST time instead of dying as `unknown lane` at dispatch time — the
   pointer is part of the dispatched surface, so it is certified like any
   other artifact, not assumed.
4. **AGENTS.md/README**: name `run-gate` as the canonical test entrypoint
   (do this in the adoption commit — docs never lead the tool).
5. **Gitignore the artifacts.** Lanes write evidence into the tree —
   `.assay/`, `coverage.json`, `.run-gate/` (RG-27 lane history), and
   anything else a lane's `artifacts` list names. The vbpub root
   `.gitignore` already covers internal projects; every consumer MUST
   ignore the entries for every path its lanes write, or the NEXT lane's
   `clean_tree` check refuses mysteriously on yesterday's evidence. Treat
   the union of declared `artifacts` lists plus `.run-gate/` as the
   checklist. Only the last of these is self-enforcing — run-gate refuses
   to write an un-ignored history store and names the remedy ("What each
   lane costs" below).
6. **Profiling (RG-55/RG-57) needs nothing from a consumer to be SAFE, but
   one thing to be PRECISE.** run-gate profiles EVERY lane kind — container,
   exec, AND bare-host (RG-57) — with no consumer action at all: a coarse
   in-lane sample (cgroup-based for container/exec lanes; for bare-host,
   `os.wait4()` reaps the lane's own child and records `ru_maxrss * 1024`
   in bytes, with no cgroup/pressure/DAMON/events data, RG-57) when no daemon
   answers, every time, unless
   `[profile] enabled = false` or `RUN_GATE_PROFILE=off`. For the PRECISE
   path (exact `memory.peak`, DAMON hot-set), the **cgroup-profiler daemon
   is host infrastructure, started once from the vbpub checkout** — `cd
   scripts/cgroup-profiler && ciu up` — never per-project, and never
   something a consumer's own gate config starts or stops. A runner
   without `docker exec` rights to that daemon (a locked-down CI box, a
   sandboxed agent) should set `RUN_GATE_PROFILE=off` rather than eat one
   `docker exec`-per-tick failure per profiled lane; `doctor`'s "profiler"
   check reports the daemon's reachability either way. A Docker permission,
   transport, or `docker ps` failure is reported as **state unknown**, never
   as “daemon absent” with a start-it remedy. On the bare-host daemon path,
   run-gate resolves `/etc/hostname` only as a candidate and compares that
   Docker object's mount namespace with its own; a mismatch or unverifiable
   identity safely takes the `wait4()` fallback. `run-gate.footprint.json`
   (written by `run-gate footprint --write`, once a lane's history has
   at least one profiled PASS run) is **TRACKED — commit it**, unlike
   `.run-gate/` itself (still gitignored, per-instance telemetry): the
   manifest is a distilled, portable BUDGET meant to travel with the
   config it describes, not a fact about this one checkout.

   `RUN_GATE_PROFILE` (`"on"|"off"`, checked BEFORE any config table) is
   the ambient override: `off` disables profiling for EVERY lane
   unconditionally — no token, no daemon call, no sampler, regardless of
   `[profile]`/a lane's own `profile =` — and `on` FORCE-ENABLES it even
   over a lane's own `profile = false` (checked LAST, after config and the
   lane override, so it always wins). Any other value refuses at load, by
   name — a mistyped override is a refusal, never a silent no-op.

   The daemon watch is one stream per profiling session. Readings and the
   final verdict are recorded in the run's history/inflight record; a daemon
   `killed` verdict follows the declared stall path, while `reported` is one
   warning and never a lane verdict. A terminal kill remains available from
   the stopped session summary if its stream event arrives too late. An exec
   or bare-host lane without a placement request asks the daemon to report
   stalls and keeps its declared local watch as the verdict authority; it
   cannot authorize a shared-cgroup kill. If the daemon or its watch transport is
   unavailable, run-gate uses its in-process assay progress watch. That
   fallback bounds the R2 baseline by Assay's `command_started` /
   `command_running` heartbeat (`300s` minimum, reset by each tick; for a lane
   proven to declare R2 a 600 s bound covers the wait for the plan, an R0/R1 lane's
   silent judge phase is never bounded; run-gate requests a 60 s heartbeat and
   refuses an assay `stall_timeout` below `120s`), then uses the plan's calibrated cadence (`max(300s, 3x
   the advertised next-event bound)`), and pauses its idle clock while host
   memory PSI `full avg10` is above 5, so host contention cannot become a
   false liveness verdict. It stays armed for an assay lane even when
   profiling is disabled; a stall is an infrastructure error, never a mutant
   classification (see
   [docs/DESIGN-GUIDE.md](docs/DESIGN-GUIDE.md#liveness-is-evidence-not-a-verdict)).

   <!-- run-gate-config -->
   ```toml
   schema_version = 1

   # run-gate.toml or run-gate.root.toml — [profile]: central/project,
   # whole-table shadowing
   # (the SAME rule [history] uses — a project's own [profile] REPLACES a
   # central one entirely, never a per-key merge)
   [profile]
   enabled = true                # default true; false disables every lane
                                  # (a lane's own `profile = false` disables
                                  # just that one; RUN_GATE_PROFILE=on/off
                                  # overrides both, see above)
   daemon = "cgprofile-host-daemon"  # the daemon CONTAINER NAME (default shown)
   interval = "1s"                # the `budget` grammar; basic/daemon sample tick
   damon = true                   # default true; DAMON hot-set classification
                                  # when the daemon supports it (basic-path
                                  # sampling never has DAMON data either way)
   transport = "auto"             # auto | exec | socket
   socket_path = "/run/cgprofile/ctl.sock"  # socket carrier path
   admission_wait = "10m"          # wait bound; then proceed with one warning

   # run-gate.toml or run-gate.root.toml — [footprint]: same whole-table
   # shadowing rule; consumed
   # by `doctor`'s staleness/drift check and by `footprint --write`
   [footprint]
   tolerance_pct = 25             # default 25; doctor WARNs when the live
                                  # history median peak differs from the
                                  # committed manifest by more than this
   max_age_days = 30              # default 30; doctor WARNs when the
                                  # manifest's distilled_at is older than this
   ```

## Central defaults (vbpub monorepo)

`run-gate.root.toml` at the REPO ROOT holds environment facts once for all
internal projects (`[environments.<name>]`: `image`, optional ephemeral-only `image_env`,
`cgroup_slice` or `cgroup_slice_env`, optional ephemeral-only `user` — `"invoker"` for the
invoking process's `uid:gid` or a numeric `uid[:gid]`, passed as `--user` so files the
container writes into the bind-mounted worktree are owned by its owner, RG-106) and, since
RG-16, SHARED LANES too: every package can use
the identical lane without copying definitions. Discovery: nearest STRICT
ancestor of the project dir; project entries shadow a central name entirely
(whole table, no field merging — same rule for environments and lanes).
Internal assay lanes omit `assay_command` and `pins`; run-gate installs the
selected worktree's `assay/` source in the lane environment, and the verdict
records the runtime version and source commit. Explicit command + pin sidecars
remain available for external consumers. External repositories such as dstdns
install the Run-Gate wheel alongside their own project config; they are not
self-contained copies of the CLI.

<!-- run-gate-config -->
```toml
# repo-root run-gate.root.toml — one shared internal assay lane every package
# inherits; the selected worktree's ../assay source is installed at run time:
schema_version = 1

[environments.tester-unified]
mode = "ephemeral"
image = "tester-unified:local"

[lanes.assay-shared]
kind = "assay"
environment = "tester-unified"
assay_lane = "gate"
clean_tree = false
```

## Lane schema (final — what run-gate actually validates)

<!-- run-gate-config -->
```toml
# run-gate.toml — parsed by run-gate only
schema_version = 1

[environments.tester-unified]
mode = "ephemeral"
image = "tester-unified:local"
image_env = "RUN_GATE_TESTER_UNIFIED_IMAGE" # optional per-invocation image override
cgroup_slice_env = "CGROUP_PARENT_DEV_GATES"
forward_env = ["SCHEMA_GATE_PW", "CGROUP_PARENT_DEV_BACKGROUND"]
# user = "invoker"                           # optional (ephemeral only): run as the invoker's uid:gid (RG-106)

[lanes.suite]
kind = "command"
environment = "tester-unified"
argv = ["bash", "-c", "pytest -q"]  # {worktree} may be substituted
budget = "20m"                      # hard wall-clock bound; state remains resumable
clean_tree = true                   # default TRUE; false needs a written reason
description = "one-line what/why"   # optional; shown by --help (never by --list)
required_env = ["SCHEMA_GATE_PW"]   # optional; gate refuses to start if unset/empty,
                                    # and (container lanes) if not on the env's forward_env
artifacts = ["coverage.json"]       # optional; paths printed after EVERY run (success or
                                    # failure); {worktree} substituted; relative entries
                                    # resolve against the effective project dir; assay
                                    # lanes always disclose .assay/verdict-<lane>.json too
profile = false                     # optional (RG-55): opt this lane OUT of profiling
                                    # entirely; or a table {enabled, damon} overriding
                                    # [profile]'s own defaults for this lane only

# RG-20 resources (optional sub-table — declare RAM so admission can protect
# the host; `resources.memory` supersedes lane-level `memory`, never declare both):
[lanes.suite.resources]
memory = "2g"                       # hard RAM cap (--memory) + admission accounting:
                                    # refused if slice usage + this exceeds memory.max
memory_swap = "16g"                 # --memory-swap; tight RAM + ample swap absorbs bursts
cpu_weight = 100                    # advisory 1..10000 (printed; no portable docker flag)
io_weight = 100                     # advisory 1..10000 (printed; no portable docker flag)
shared = ["pg-main"]                # serialize with any other gate declaring the same name
cpus = "2"                          # RG-48: docker run --cpus (decimal string, > 0).
                                    # Same key on [environments.<name>.resources] as a
                                    # FALLBACK for lanes that declare none of their own
                                    # (this lane's own value wins when both are set) --
                                    # the only resources key an environment accepts.

[lanes.native-r2]
kind = "assay"
environment = "tester-unified"
assay_lane = "r2"
resource_observer = "cgprofile"
clean_tree = true

[lanes.native-r2.resources]
memory = "2g"                       # required local ceiling for memory.events attribution
pids = 8192                          # required local ceiling for pids.events attribution
```

### Per-invocation image selection

Use `image_env` on an **ephemeral** environment when a workflow must select an
image by immutable ID or receipt-specific tag:

<!-- run-gate-config -->
```toml
schema_version = 1

[environments.tester-unified]
mode = "ephemeral"
image = "tester-unified:local"
image_env = "RUN_GATE_TESTER_UNIFIED_IMAGE"

[lanes.suite]
kind = "command"
environment = "tester-unified"
argv = ["true"]
```

Run-Gate reads the named variable before starting the container. An unset
variable uses the declared `image`; a set but empty value is an error. The
value is consumed by the host-side Run-Gate process and is not forwarded into
the test container. The run log states whether the configured value or the
override selected the image. `run-gate history --json` records the declared
image, override variable, effective image, and immutable image ID inspected
from the container that actually ran. An image ID inspection failure is
recorded as unavailable with its reason rather than silently omitting the
selection. `image_env` is refused for `exec` environments because Run-Gate does
not start or replace their persistent container.

The `native-r2` lane demonstrates the opt-in observer-only socket integration:
it exposes only the read-only resource-observer socket to the Assay process in
the private tester namespace; it does not expose the daemon control socket.
Internal Assay lanes omit `assay_command` and `pins`, so run-gate installs the
selected worktree's `assay/` source. Explicit command plus immutable pin
sidecars remain available for external consumers. Lane options belong on the
lane itself, and the target `assay.toml` owns the mutation budget.

The host's shipped `dev-gates.slice` example has a `1.5G` memory maximum, so it
cannot admit this `2G` R2 lane. Before running it, configure the gate slice and
its `dev.slice` ancestor for the requested lane caps plus overhead. Two
concurrent `2G` lanes need more than `4G` of gate-slice headroom; the full MDT
assay lane declares `6G`. Run-Gate refuses a request when it can read the
declared slice's `memory.max` and `memory.current` and the request exceeds that
slice's remaining budget. If those files are hidden or unreadable, it warns and
continues under the other shared-infrastructure checks; it does not prove
ancestor or whole-host headroom. Check and record those limits and current use
before a high-memory campaign, and serialize campaigns when capacity is tight.
See the [host sizing guidance](../modern-debian-tools-python-debug/host-setup/README.md).

For daemon-backed `exec`/`bare-host` lanes, `resources.memory_max` is an
optional placement ceiling, not a second Docker memory limit. Without it,
run-gate derives the ceiling from the verified gates-slice cap and request.
When placement is requested, Run-Gate holds the lane body until the daemon
returns a verified leaf and read-back limits. An explicit refusal or
incomplete/looser read-back exits as an infrastructure refusal before the
body runs; it is not a candidate verdict. A profiler/transport failure before
any placement result follows the disclosed R-36h fallback instead.

### Native R2 resource observer

Native Assay R2 must inspect the candidate cgroup and every enforcing
ancestor; a private cgroup namespace hides that ancestry, and Assay refuses
instead of guessing. When the existing cgprofile daemon is available, an
Assay or command lane whose resolved environment has `mode = "ephemeral"` may
explicitly opt in with `resource_observer = "cgprofile"` as shown above. This
value is closed and legal only for `kind = "assay"` or `kind = "command"` in
an ephemeral environment. It exposes a read-only query about that lane's
own cgroup ancestry; it is intended for native Assay R2 or a command wrapper
that launches it. Such a wrapper must preserve the inherited
`ASSAY_RESOURCE_OBSERVER_SOCKET` variable for the Assay process. Opting in
does not grant placement or daemon-control authority.

For a command-wrapped qualification such as Assay's B105 self-qualification,
the declaration is complete as shown — the observer opt-in requires the
ephemeral environment and both ceilings (`memory = "2g"` and `pids = 8192`, the
caps B105's own container wrapper applies), and the config is refused at load
without them:

<!-- run-gate-config -->
```toml
schema_version = 1

[environments.tester-unified]
mode = "ephemeral"
image = "tester-unified:local"

[lanes.self-qualification]
kind = "command"
environment = "tester-unified"
argv = ["bash", "{worktree}/assay/tools/self-qualification-gate.sh", "{worktree}", "self-qualification"]
resource_observer = "cgprofile"
clean_tree = true

[lanes.self-qualification.resources]
memory = "2g"
pids = 8192
```

Before starting the lane, Run-Gate queries cgprofile's control `version`
response and requires observer contract 2, the fixed observer socket path,
and `listening = true`. It checks the root-owned, non-writable observer
directory and mounts that directory read-only at `/run/cgprofile-observer`.
The daemon owns only the socket name inside this host-setup-owned directory,
so a daemon restart rebinds a path an already-running lane can still resolve.
Run-Gate grants the dedicated observer GID and sets
`ASSAY_RESOURCE_OBSERVER_SOCKET` to the in-container socket path. Observer
lanes require explicit per-container `resources.memory` and `resources.pids`
ceilings so candidate-local `memory.events` and `pids.events` can support
attribution. `RUN_GATE_EXTRA_MOUNTS` is refused for these lanes. It does not mount
`/run/cgprofile/ctl.sock`, the Docker socket, or any host namespace. The
existing daemon's separate observer listener accepts only a resource-limit
query bound to the caller's peer credentials and native thread ID; it returns
the caller cgroup's ancestry under the configured gates slice, without PIDs or
sibling cgroups. The launch guard compares the observer GID with the numeric
GID of the root-owned `/run/cgprofile` control directory (provisioned as
`root:docker` by host setup); it does not trust the devcontainer's group-name
table and refuses when that host path is unavailable.

Assay uses local counters when its own hierarchy view is complete. Remote
observation is used only when the local error specifically indicates hidden
ancestors; a missing socket, malformed identity, incomplete ancestry, or
uncertain shared-ancestor event remains an infrastructure ERROR with no
candidate bucket. Do not mount the control socket as a fallback: a read-only
bind does not restrict the verbs accepted by its protocol. See the
[design rationale](docs/DESIGN-GUIDE.md#private-namespace-r2-uses-a-read-only-cgroup-observer).

For this project's native R2 lane, keep `[lanes.r2.judge.mutation] jobs = 1`
in `assay.toml`: workers in the same tester container share cgroup event
counters even with separate scratch trees. After a budget stop, rerun the
registered lane on the same exact tree so Assay resumes its saved campaign.

#### Native R2 resource observation refusals

Run-Gate maps `NO_MEASUREMENT/CGROUP_OBSERVATION_UNAVAILABLE` to gate
`ERROR` only when the R2 refusal is the sole non-passing result: every other
claim and external evidence entry must be `PASS`. If another claim or evidence
entry is `BUDGET_EXCEEDED`, the gate preserves `BUDGET_EXCEEDED`; another
`FAIL`, `INCONCLUSIVE`, or `NO_MEASUREMENT` remains `FAIL`. Malformed claim or
evidence records are `ERROR`. Other Assay `NO_MEASUREMENT` and `INCONCLUSIVE`
outcomes remain FAIL. If an observer read or event comparison fails after a
candidate starts, Assay returns `ERROR/EXEC_FAILED` and does not assign that
candidate a mutation bucket. The JSON result keeps the raw Assay outcome and
reason code. This v16 rollup contract requires Assay 10.0.0 or newer.

## Closed results and runner mode migration

Every invocation returns one of five statuses: **PASS 0**, **FAIL 1**,
**ERROR 2**, **NOT_RUN 3**, or **BUDGET_EXCEEDED 4**. The result and
`run-gate <lane> --json` retain the lane's raw `exit_code`; command
statuses map to PASS only at zero, and pytest 5 is FAIL with reason
`pytest-collected-no-tests`. Assay's `NO_MEASUREMENT` and `INCONCLUSIVE`
outcomes are FAIL except `NO_MEASUREMENT/CGROUP_OBSERVATION_UNAVAILABLE`,
which is gate ERROR. Use verdict plus the raw code; never infer that run-gate
itself returned an ERROR from a raw lane code of 2. For Assay lanes, the
artifact's `exit_code` must be a non-boolean integer equal to the process
status, and must match the outcome's closed Assay code table. If the artifact
is missing that field or either comparison disagrees, the gate returns
infrastructure ERROR while retaining the runner's raw status and Assay
outcome. This prevents a PASS artifact from overriding a failing process.

### Exec mode Git safety

For `mode = "exec"`, use the existing single-user test-runner declared by
your project. Run-Gate injects `safe.directory=*` into each lane process
through Git's `GIT_CONFIG_COUNT` environment variables, appended after any
`GIT_CONFIG_*` entries your runner already sets (they keep working). No global
Git config or lock file needs to be created, and no lane mutex is added.
Concurrent lanes do not contend on Run-Gate's safe-directory setup. Keep one
user per test container. The image needs **Git 2.38 or newer** — older Git
ignores `safe.directory` from the environment; the lane then refuses at
start-up with exit 2 and names the floor instead of dying later as "dubious
ownership". A `GIT_CONFIG_COUNT` in the runner that is not a plain integer is
refused the same way. Live concurrent exec-mode acceptance remains the last
open RG-99 item. Do not add a `git config --global safe.directory` preamble to
the lane command. This covers Run-Gate's own Git safety setting; a lane that
intentionally changes global Git settings owns that behavior separately. See the
[design rationale](docs/DESIGN-GUIDE.md#concurrent-exec-lanes-use-read-only-git-safety).

### Init reaping and resource events

When another tool starts run-gate inside a container, give it an init reaper.
Without one, run-gate refuses to start as PID 1. A Docker launcher adds
`--init` to its existing `docker run` options. In Compose, add `init: true`
to the existing service while keeping its mounts and cgroup placement:

```yaml
services:
  test-runner:
    image: tester-unified:local
    init: true
    # Keep the service's existing cgroup_parent, mounts and command here.
```

For each real lane, run-gate compares the `pids.events` `max` and
`memory.events` `oom_kill` counters in the cgroup containing the run-gate
process. A counter increase forces ERROR/2 even when the raw lane command
returns zero; `--json` retains that raw status in `exit_code`. If the cgroup
path or counters cannot be read, run-gate returns an infrastructure ERROR
instead of certifying an unobserved run, retaining any raw status already
known. `--dry-run` does not read or change these counters. The read-only
`--version` operation remains available when run-gate is PID 1.

An older config can be migrated without reserializing its TOML or dropping
comments:

```console
run-gate migrate-modes run-gate.toml
run-gate migrate-modes run-gate.root.toml  # if present
```

The command edits one file per call. Review its diff and make each environment
mode explicit: `ephemeral` starts a fresh image, `exec` targets an external
persistent runner, and `host` runs a subprocess on the invoking host. Names
do not select behavior. This config migration does not rewrite wrappers; audit
callers that branch on raw command exit codes:

- nyxloom `[gates.*]` pointers that only distinguish zero from non-zero keep
  their meaning;
- dstdns `gate-slot.sh` must stop treating a lane's old raw status as the
  semaphore's exit-75 signal (RG-80 removes that wrapper); review its
  `gate-base.sh` and any scripts that inspect nested run-gate statuses;
- review CI workflow shell branches and `cmru tester-gate` status handling;
- reclassify tests that asserted raw command passthrough to assert both the
  closed verdict and retained raw `exit_code`.

The migration and wrapper audit are part of one consumer cutover. A script
that wants to distinguish a run-gate refusal should use the closed outer
status; a shell conjunction still collapses a nested refusal into its own
non-zero command result until it moves to a native sequence lane.

Environment facts resolution order (no silent fallbacks anywhere):
`cgroup_slice` declared on the environment → the variable named by
`cgroup_slice_env` → `$CGROUP_PARENT_DEV_GATES` (hard error if the selected
source is absent); physical repo root DERIVED from `/proc/self/mountinfo`;
where systemd is reachable, require `LoadState=loaded` and a non-empty
`FragmentPath`. An auto-created typo slice can report loaded without an
installed unit file, so LoadState alone is insufficient. Container lanes
rely on the outer gate launcher for host-side placement verification and
dual-mount (physical + namespace views) the judged worktree and the git
common dir for worktree gitfiles; outside the devcontainer namespace — where
no second view is derivable — the lane refuses rather than silently mounting
once; declare the alias with `$RUN_GATE_MOUNT_ALIAS='<host>=<namespace>'`
(host side must equal the repo root).

**What an ephemeral container sees (RG-86).** Only the worktree under test
and the git metadata it needs — never the main checkout or any other
worktree, so another checkout's git-ignored files (secrets, local configs)
are not visible. Concretely, for a linked worktree: the worktree itself
(ALL of its own files, git-ignored overlays and rendered files such as
`ciu.rendered/`, `ciu.secret-copy.*`, `ciu.env` included — ciu worktree
instances rely on this), the git common dir read-write (assay's repository
snapshot runs `git worktree add`; ciu v8 SPEC S16.4.9), `<repo>/.run-gate`
for assay lanes without a declared `state_root`, and whatever the operator
adds through `RUN_GATE_EXTRA_MOUNTS`. `<common>/config` inside the container
is a per-run copy with credentials removed (URL userinfo, `credential.*`,
`*.extraheader`, the `sendemail.*`/`imap.*` sections, keys ending in
pass/password/token/secret/apikey, `git+https://` style userinfo,
credential-bearing `url.*.insteadOf`; every `config`/`config.worktree` at any
depth under `modules/`, nested submodules included),
so a remote URL that embeds a token on the host reads without it in the
container; `git status`, `log`, `diff` and `rev-parse` work as usual.
Because the common dir is read-write, `<common>/worktrees` is mounted
READ-ONLY with its real contents and your worktree's own admin dir
read-write on top: a `git worktree prune` inside the container fails
(EROFS) instead of deleting other worktrees' admin dirs on the host, `git
gc` still sees every other worktree's HEAD and index, and `git branch -D` of
a branch checked out elsewhere is refused. `git worktree add` inside the
  container cannot create a new admin dir (assay's snapshot uses `clone
  --no-local` and is unaffected).

The shared Git directory remains read-write for object and in-lane commit
operations, but its host hook path is not available to the tester: Run-Gate
overlays `<common>/hooks` with a private empty read-only directory and mounts
`<common>/info` read-only. A lane can therefore commit inside its worktree
without installing a hook that a later host Git command could execute or
changing host `info/exclude`. The container acceptance test plants a host
`post-commit` hook, attempts both writes from the lane, and verifies the
in-container commit cannot run or change it. A later host commit still runs
the original hook unchanged. See the
[shared Git mount design](docs/DESIGN-GUIDE.md#shared-git-metadata-keeps-host-hooks-inert).

**Main checkout.** An ephemeral lane or probe whose judged tree is a plain
checkout (not a linked worktree) is REFUSED: the whole checkout, including
every git-ignored file, would be visible. Judge a worktree (`--worktree`), or
opt in explicitly with `--allow-main-checkout` / `RUN_GATE_ALLOW_MAIN_CHECKOUT=1`;
the opt-in WARNs, and still sanitizes the git config. `mode = "exec"` lanes
are never refused and not changed: their containers are owned by ciu or the
project's stack. Disposable CI clones (Buildkite agents) are plain checkouts:
set the opt-in in the agent environment.

> **BREAKING CHANGE — migrate if you use `mode = "exec"` (RG-23).** Early
> revisions hardcoded `MOCK_MODE` and `RUN_LIVE_TESTS` into the exec-mode
> forwarding loop; they were replaced by declarative `forward_env` with no
> migration pass. If your lane relies on either name, **it is not reaching
> your container today** — and the symptom is a false GREEN, not an error: a
> suite that skips its live tests when the flag is absent exits 0 having run
> none of them. Migrate both halves:
>
> <!-- run-gate-config -->
> ```toml
> schema_version = 1
> [environments.test-runner]
> image = "yourproj/test-runner:latest"
> mode = "exec"
> forward_env = ["RUN_LIVE_TESTS", "MOCK_MODE"]   # was implicit; now required
>
> [lanes.release]
> kind = "command"
> environment = "test-runner"
> argv = ["bash", "-c", "cd {worktree} && pytest -m 'integration or e2e' -q"]
> required_env = ["RUN_LIVE_TESTS"]   # absence now REFUSES instead of skipping
> ```
>
> `forward_env` alone restores the old behaviour; adding `required_env` is
> what turns the silent-skip class into a loud refusal, and is why the
> implicit names are not coming back — a value with an authoritative source
> (your config) must not be shadowed by a literal inside the tool. Audit
> your own configs with `run-gate --check-env` (limits below) and
> `grep -n forward_env run-gate.toml`. Estate audit at rev 25: no vbpub
> project declares `mode = "exec"` or `forward_env` at all, so the confirmed
> blast radius is dstdns, which is tracked in its own repo.

Container lanes forward only the implicit cgroup infrastructure variable plus
the environment's explicit `forward_env = ["SCHEMA_GATE_DSN"]` allowlist. A
declared but unset value remains absent rather than becoming a default; the
lane's own required-input policy must fail loudly when absence matters.
Lanes that NEED a variable declare `required_env = ["SCHEMA_GATE_PW"]`: the
gate refuses to start unless each name is present and non-empty, verifies
container lanes can actually receive it (it must be on `forward_env`), and
prints which forwarding keys were present at start — names only, never
values (the docker-argv print redacts them too). Run
`run-gate --check-env` for an advisory sweep of env references in the
project's Python sources that no lane forwards or requires.

**What `--check-env` can and cannot see (RG-23).** It parses each `*.py` and
reports `os.environ["X"]`, `os.environ.get/setdefault/pop("X", …)`,
`getenv("X")`, `"X" in os.environ`, **and** a literal handed to your own
env-reader helper — a function that reads the environment through one of its
parameters, which is how the flag that motivated this hid from the previous
line-based sweep:

```python
def _env_flag_enabled(name):        # the read is here …
    return os.getenv(name, "").lower() in ("1", "true")

RUN_LIVE = _env_flag_enabled("RUN_LIVE_TESTS")    # … the NAME is here
```

```
run-gate: env-drift: $RUN_LIVE_TESTS referenced in conftest.py:6
  (helper _env_flag_enabled()) is neither forwarded nor declared
  required_env — add it to the environment's forward_env or the lane's
  required_env
```

It CANNOT see a name assembled at runtime (`os.getenv(prefix + suffix)`), a
name read from a non-Python source, or an indirection it does not model. A
file that does not parse is reported by name and falls back to the old line
regex, so a parse failure is never silently reported as "nothing found". If a
Python file cannot be read or decoded, Run-Gate names it and marks the scan
`INCOMPLETE`; a zero count does not certify unreadable files. The drift scan
remains advisory, while toolchain failures retain their existing exit status.
**A clean sweep is evidence, not a certificate** — it stays advisory (always
exit 0 for drift); `required_env` is the mechanism that actually refuses.

### Assay lanes — projects that adopt assay (the quality partnership)

run-gate does the ORCHESTRATION (environment, mounts, cgroup, optional
artifact verification, clean `--init` detached run), then invokes assay; **assay
does the JUDGMENT** — its lane in `assay.toml` owns argv-under-test, coverage
floors, R-levels, changed-line policy, snapshot isolation. Two files, two
owners, no duplicated registry. Internal vbpub projects use source mode:

<!-- run-gate-config -->
```toml
schema_version = 1
[environments.tester-unified]
mode = "ephemeral"
image = "tester-unified:local"

[lanes.ciu]
kind = "assay"
assay_lane = "ciu"                  # -> assay.toml [lanes.ciu]
environment = "tester-unified"
# No assay_command or pins: run-gate installs <selected worktree>/assay.
```

For a consumer outside this monorepo, retain the explicit immutable boundary:

<!-- run-gate-config -->
```toml
schema_version = 1
[environments.tester-unified]
mode = "ephemeral"
image = "tester-unified:local"

[lanes.ciu]
kind = "assay"
assay_lane = "ciu"
environment = "tester-unified"
assay_command = ["/opt/tester-venv/bin/python", "tools/assay/assay-<version>.pyz"]

[lanes.ciu.pins.assay]
version = "<version>"
sha256 = "tools/assay/assay-<version>.pyz.sha256"
```

The internal source mode is selected by omitting both fields. The source is
installed with `--no-deps` from the selected tree, so no registry or ambient
Assay installation is consulted. Run-gate uses Python isolated mode (`-I`)
for both the source check and the actual `assay.cli` command, so `PYTHONPATH`
does not select another package. Before the lane runs, it inspects the
consumer directory without executing its contents: a regular local `assay`
module or package is rejected, while a namespace-only `assay/` directory is
allowed. It then resolves the installed package spec to the selected tree's
`assay/src/assay/__init__.py`. Afterward it requires a non-empty
`assay_version` and a full Git `commit` in the verdict that matches the run
record's selected commit. Missing identity or a different commit makes the
lane ERROR. This is source identity, not an artifact digest; external
immutable mode continues to require `judge_provenance`. Run-Gate accepts full
SHA-1 and SHA-256 commit IDs here; Assay's P22 snapshot source still requires
SHA-1 object storage for high-rigor snapshot lanes.

An inflight Assay record stores whether that run launched in source or
artifact mode. Re-attachment uses the recorded mode even if `run-gate.toml`
has changed since launch; it does not reinterpret an old verdict under the
current config. If the lane has changed from Assay to another kind, it also
refuses rather than treating the old Assay result as a command result.
Inflight schema 2 adds this field, so an older schema is
refused instead of guessed. For a container-runner record, a missing or
invalid mode, or an Assay artifact path that differs from the lane's derived
verdict/progress paths, is refused before the Assay inventory Docker probe
or admission, and the record is preserved. An Assay artifact path on a
non-Assay record is also refused. A
lost container's old mode does not carry over to its fresh
replacement. A promoted follower retains the launch-time mode until its
caller parses the verdict; the history record and parsed result do not expose
that private field. For an ephemeral-container lane, after confirming the old
container has stopped, `run-gate <lane> --fresh` discards that record and
starts under the current config. Host and exec lanes cannot use `--fresh`; ask
the named runner's lifecycle owner to confirm it has stopped, then remove the
stale recovery record at the path named in the refusal. On an older-schema
record marked with a non-container `runner` (such as `exec`), `--fresh` also
refuses; use that runner's lifecycle owner to confirm the run is over before
removing the record.
Before imported Assay inventory or sequence admission, Run-Gate checks the
requested lane's recovery record and every sequence node and member. If a
lane was changed to `kind = "sequence"` while its old Assay or command
container record remains, Run-Gate refuses before starting members. Ask the
prior runner's lifecycle owner to confirm it has stopped, then remove the
record at the path named in the refusal. A record owned by `exec` is also
refused before inventory or admission, including when `--fresh` was requested.

If a lane changes from an ephemeral container to host or exec while its old
container record remains, Run-Gate refuses before imported Assay inventory or
lane execution. The current mode cannot safely re-attach to or replace that
container; ask its prior lifecycle owner to confirm it has stopped, then
remove the recovery record named in the refusal.

Assay inventory and toolchain probes in ephemeral environments receive the
same configured container user and RUN_GATE_EXTRA_MOUNTS as the judged lane.
An imported command or tool mounted there can therefore be checked before the
lane starts, under the permissions it will actually have.

Division of labor, spelled out:

| concern | owner |
|---|---|
| container image, mounts, cgroup slice, env passthrough | run-gate.toml |
| external artifact pins, clean-tree refusal | run-gate.toml / assay (S18.4) |
| suite argv, coverage floors, R0/R1/R3, isolation snapshot | assay.toml |
| verdict artifact + PASS/FAIL meaning | assay |
| WHEN a lane must pass (release policy) | the project's release config (cmru) |

### Resume, progress, and durable assay state

Every assay invocation receives `--resume`,
`--progress .assay/progress-<assay_lane>.jsonl`, and `--state-dir`.
Verdict and progress files stay under the effective project directory in
`.assay/`; keep that directory git-ignored. The state directory is outside the
judged worktree so a deleted or recreated worktree can resume:

```text
<checkout owning the shared .git>/.run-gate/assay-state/<project-relative-path>/
```

For a nested project, the key includes its path relative to the checkout, so
two projects named `backend` do not share state. A project outside that
checkout uses the separate `assay-state-external/<sha256>` namespace, keyed
by its resolved absolute path. By default the state root is
`<checkout owning the shared .git>/.run-gate`. A container environment whose
durable mount is at another in-container path declares that exact path as
`state_root` in its `[environments.<name>]` table. The runner owner must mount
the corresponding durable host directory read-write before the gate runs;
run-gate never creates a root-owned directory in an exec runner's disposable
container layer. For example, if the runner mounts a durable host directory
at `/workspace/.run-gate` and the judged checkout at `/workspace/project`,
declare:

<!-- run-gate-config -->
```toml
schema_version = 1

[environments.test-runner]
mode = "exec"
image = "yourproj/test-runner:local"
container_name = "yourproj-test-runner"
state_root = "/workspace/.run-gate"
```

For a host environment, run-gate uses the default checkout path directly;
`state_root` is only meaningful for container environments. For a bare-host
lane, Run-Gate creates `<checkout>/.run-gate` if absent and refuses a symlink
or other non-directory there. Its default root is also created before a fresh
ephemeral container lane mounts it. An explicitly configured container
`state_root` still needs a real, writable durable mount from the runner
owner; Run-Gate does not create that configured path. Before Assay runs,
Run-Gate checks that the
selected root exists and is writable by the lane user, then checks the
deepest existing directory along that lane's keyed state path. This catches
an old state directory owned by a different user before Assay starts. A
missing or unwritable root or ancestor is refused
before the assay command with NOT_RUN/`state-mount`, naming the path and
applicable remedy; it is never reported as an assay FAIL. The `doctor`
command checks once per assay environment, and `--dry-run` prints the planned
probe. For an ephemeral or bare-host lane, a fresh default root is reported
as unverified by `doctor` until the live lane creates and probes it; an exec
runner is probed directly. `--dry-run` shows the mount that the live
lane will add without creating the directory. Run-Gate derives
`GIT_CEILING_DIRECTORIES` and a test-command temporary path from this
selected state path (`RUN_GATE_TMPDIR`, and `TMPDIR` too when the root is
outside the judged worktree; see the next paragraph). `state_root` is a container path, so
run-gate does not stat it in the host namespace. Pin versions below **5.2.0**
are refused before the lane starts because that is the first Assay release
with `--state-dir` (the other two flags require only 2.4.1). The run header
names the resolved state directory. See the
[mount rationale](docs/DESIGN-GUIDE.md#assay-resume-state-needs-an-environment-owned-mount)
for why Run-Gate does not create a configured root inside an exec runner.

**The default in-repo root works for snapshot-isolated lanes (RG-98).** Assay
chooses its own snapshot scratch from `TMPDIR`, and that scratch must be outside
the judged repository, so Run-Gate never exports an in-worktree state root as
the judge's `TMPDIR`. Any R0–R3 lane therefore runs with the default
`<checkout>/.run-gate`; no external `state_root` is required. What changes is
how the test command learns the verified path:

| state root | judge environment | test command gets `TMPDIR` via |
|---|---|---|
| outside the judged worktree (explicit `state_root`, or a linked worktree) | `TMPDIR`, `RUN_GATE_TMPDIR`, `GIT_CEILING_DIRECTORIES` | `env_passthrough = ["TMPDIR"]`, as before |
| inside the judged worktree (default root, primary checkout) | `RUN_GATE_TMPDIR`, `GIT_CEILING_DIRECTORIES` — the operator's own `TMPDIR` is untouched | the mapping below |

For the in-worktree case, map Run-Gate's path onto the test command's `TMPDIR`
with an Assay infrastructure fact, in the consumer's `assay.toml` (drop `TMPDIR`
from that lane's `env_passthrough`; Assay refuses a passthrough name that
collides with an infrastructure fact):

```toml
# assay.toml — the lane's test command still sees TMPDIR inside the checkout
[lanes.r1]
env_passthrough = ["PATH", "GIT_CEILING_DIRECTORIES"]

[lanes.r1.infrastructure]
TMPDIR = "required-env:RUN_GATE_TMPDIR"
```

`required-env:` refuses when the variable is empty, so a lane written this way
runs through `run-gate`, not a bare `assay run`. A lane that needs neither
(pytest's own temp directory is fine) needs no change.

The progress JSONL is append-only: Assay writes a `run` header for each
invocation. Run-Gate attributes candidate status and silence only to records
after the newest header, so old candidates retained for resume are not
presented as work completed by the current invocation. Rates count the
current run's candidate events, while remaining-work ETAs use Assay's
`pending_total` when available. If a `run` header exists but its current-run
`pending_total` is absent, invalid, or inconsistent, Run-Gate suppresses the
ETA; campaign-index fallback is reserved for legacy streams without a `run`
header. The campaign-wide candidate index is never divided by the new run's
elapsed clock. Do not truncate the progress file to reset monitoring;
`--resume` owns that history. See the
[progress design](docs/DESIGN-GUIDE.md#progress-is-scoped-to-the-current-assay-run).

### Automatic Assay campaign closeout (B108 phase 2)

Every live `kind = "assay"` lane is followed by Assay's `analyze campaign`
without any configuration. Nothing to paste into `run-gate.toml`; the lane you
already have is enough:

<!-- run-gate-config -->
```toml
schema_version = 1

[environments.tester-unified]
mode = "ephemeral"
image = "tester-unified:local"

[lanes.r2]
kind = "assay"
environment = "tester-unified"
assay_lane = "r2"
```

```bash
run-gate r2    # lane result and exit are exactly what they were before
# run-gate: assay closeout: campaign incomplete (analysis only; lane verdict FAIL (exit 1) is unchanged); json .../r2/<run id>.campaign.json, text .../r2/<run id>.campaign.txt
ls "${RUN_GATE_EVIDENCE_DIR:-/tmp/run-gate}/lanes/r2/"   # <run id>.log  <run id>.campaign.json  <run id>.campaign.txt
```

The two files sit beside the lane log, owner-only, named by the run id. The run
record (`run-gate history`) carries `closeout` with the status, the analysis
exit, both paths and both SHA-256 digests. Statuses: `complete`, `incomplete`,
`evidence_error` (Assay's own), `unavailable` (no analysis output: a pinned
judge older than `analyze campaign`, a crash, the per-call cap — a quarter of
the lane budget left, 900 s at most — hit, or the lane budget nearly spent so
the closeout was skipped; the reason names which),
`invalid` (not this lane's or commit's, or it disagrees with the lane result),
`retention_error` (the evidence directory is not writable) and
`not_applicable` (the verdict's verified claim set carries no R2 claim; nothing
is printed; a malformed or unverified claim set is analyzed, never assumed
absent). Every
status other than `complete`/`incomplete` prints a `WARNING` on stderr saying
the lane verdict and exit are unchanged. Pin the judge to a version with
`analyze campaign` (see Assay's README) to avoid a standing `unavailable`.

A `kind = "command"` lane that runs `assay run` itself is not analyzed, and
this release has no key that opts it in (Assay decision A-497); run
`assay analyze campaign` by hand over its artifacts (Assay CONSUMERS,
"Run the closeout automatically from Run-Gate"). A lane stopped by its budget
or killed has no closeout for the same reason. Design:
[the closeout is evidence, never a verdict](docs/DESIGN-GUIDE.md#the-closeout-is-evidence-never-a-verdict-b108-phase-2).

### Shared assay lane imports

An external-assay consumer can pin the judge once and import the exact lane
set it exposes. `lanes = "all"` imports every reported lane; a string list
selects exact names (globs are refused). A project lane with an imported name
overrides it. The nested shape matches CIU v8's
`[testing.judge] import` contract:

<!-- run-gate-config -->
```toml
schema_version = 1

[environments.test-runner]
mode = "exec"
image = "yourproj/test-runner:local"
container_name = "yourproj-test-runner"
state_root = "/workspace/.run-gate"

[assay]
command = ["/opt/tester-venv/bin/python", "tools/assay/assay-7.2.0.pyz"]
import = { environment = "test-runner", lanes = "all" }

[assay.pins.assay]
version = "7.2.0"
sha256 = "tools/assay/assay-7.2.0.pyz.sha256"
```

The verifier asks `assay lanes --json` inside `test-runner` only when the
answer needs it: an imported lane is selected, a local sequence uses one, or
the verb reports on every lane. It refuses a failed or malformed inventory and
verifies the same pin for every imported lane.

| you run | asks the judge? | when the runner is stopped |
|---|---|---|
| `run-gate --list`, `--help`, `run-gate` (no lane) | never | works; the import is shown as *not queried* on stderr |
| a local lane, `admission`, `status`, `validate-pointers` | never | works |
| `run-gate history LANE` / `footprint LANE` for a lane declared in `run-gate.toml` | never | works |
| `run-gate doctor`, `--check-env`, `history`, `footprint` (all lanes), `footprint --write` | yes | refused (exit 2) with the runner remedy: no clean report, no partial answer, no manifest written |
| an imported lane, or a sequence that reaches one | yes | refused (exit 2) with the runner remedy |

`run-gate --list` keeps its three-column output; it lists explicitly named
imports as unresolved and does not invent names for `lanes = "all"`, so run
`run-gate doctor` or `run-gate history --json` (with the judge up) to see the
full lane set. This lets a host-only lane and project discovery work without
starting a persistent judge, while no verb whose answer covers every lane can
silently answer from the declared lanes alone.

### Selective lane requests

Selective assay controls apply to one assay lane and are refused on command
lanes and sequences. The reuse verdict must resolve inside the judged
worktree. `--rejudge` can be repeated, and `--rejudge-outcome` selects an
Assay outcome bucket: `killed`, `survived`, `crashed`, `budget_exceeded`,
`equivalent`, or `hung`; the Assay CLI alias `error` means `crashed`.

```console
run-gate mutation --reuse-from .assay/verdict-mutation.json \
  --rejudge tests/test_schema.py::test_one --rejudge-outcome survived
```

Command lanes accept per-run arguments only when `accepts_args = true` is
declared. The separator keeps request arguments out of run-gate's own option
parser, and the arguments are appended to that single command's argv:

<!-- run-gate-config -->
```toml
schema_version = 1

[environments.local]
mode = "host"

[lanes.schema]
kind = "command"
environment = "local"
argv = ["pytest"]
accepts_args = true
```

```console
run-gate schema -- tests/schema/test_one.py
```

Sequences and command composites refuse request arguments because their
members own separate argv declarations.

**Where a lane's dependency closure lives (assay B041 / ciu CIU-73).** assay
runs the lane command in a private snapshot of the *committed* tree —
gitignored trees such as `node_modules/` are absent by construction, and
`environment_command` cannot vouch for them (it runs in the invoking
environment, not the snapshot). A JavaScript lane therefore rebuilds its
closure OFFLINE from the committed lockfile as the first step of its own
argv (`npm ci --offline …`, then `npx --no-install vitest run --coverage`)
out of a package cache the ENVIRONMENT provides: for an `exec` environment,
bake the cache into the runner image at build from the same lockfile, or add
a volume to the runner's own stack; for an ephemeral environment,
`RUN_GATE_EXTRA_MOUNTS=/var/cache/<project>/npm=/opt/npm-cache`. Python
(venv) and Go (`GOMODCACHE`) closures are out-of-tree and need nothing.

**Preflight the toolchain instead of discovering it mid-run (RG-25).**
`run-gate doctor` and `run-gate --check-env` now ask the JUDGE what
each `kind = "assay"` lane needs — the resolved assay command's `lanes
--json --file assay.toml` (assay ≥ 3.2.0) run INSIDE the lane's own
environment — and then
check that environment for it. run-gate still never parses `assay.toml`:

```
run-gate: doctor: [OK]   lane 'ui-unit' toolchain: node, npm
run-gate: doctor: [FAIL] lane 'ui-unit' toolchain: needs npm in environment
  'test-runner' (project /repo/run-gate.toml) — assay would reach
  MISSING_EXTERNAL_TOOL/NO_MEASUREMENT mid-run instead
run-gate: doctor: [FAIL] lane 'ui-unit' toolchain: assay lane 'ui_unit' is not
  declared in assay.toml (declared: py_unit, sql_schema) — this lane can only
  ERROR at run time
run-gate: doctor: [SKIP] lane 'ui-unit' toolchain: `assay lanes --json` did not
  run in environment 'test-runner' (exit 2: unrecognized arguments: --json) —
  an assay older than 3.2.0 has no inventory (B044). The pin declares the
  version this lane needs; run-gate does not impose a floor it never declared
```

Read the statuses precisely: **`[FAIL]` only ever states a fact the judge
established** (a tool it named is absent; a lane it does not declare).
Everything meaning *"I could not determine this"* — an older judge, an
unreachable environment, an inventory schema this run-gate does not read, a
`host` environment, no docker — is `[SKIP]` with the reason, and **never
turns a healthy project red**. `doctor` exits 2 only on FAIL; `--check-env`
exits 2 on a toolchain FAIL while its env-drift half stays advisory.

**`--worktree` redirects the whole report, not just the run (RG-30).**
`run-gate doctor --worktree B` and `run-gate --check-env
--worktree B` probe B's environment, scan B's Python sources, and read B's
git identity — never the invoking checkout's under B's name. `doctor` names
the selected tree up front and repeats it on the `[OK] git` line; a
`--worktree` that names no real git worktree fails the `git` check loudly
(never a silent `[OK]`) while the rest of the report still runs. `--check-env`
has no per-check ledger for a bad override to land in gracefully, so it
refuses outright instead of scanning nothing under the wrong tree's name.

Which tools get checked: `external_tools` and `argv0` as the inventory
reports them, plus the toolchain implied by `language` (`javascript` →
`node`, `npm`; `go` → `go`). That last mapping lives in run-gate only
because assay 3.2.0 reports `external_tools: []` for every shipped adapter
and documents the language fact in prose instead; a language run-gate has no
fact for is reported with an explicit caveat on the line rather than being
treated as "nothing needed".

> **`doctor` and `--check-env` START CONTAINERS for this check.** Fitness
> cannot be read, only observed, so the inventory question and the
> `command -v` checks execute inside the lane's own environment. `doctor`
> also checks Assay's durable state root at most once per assay environment;
> a live assay invocation checks it once per lane. A creatable but absent
> default `.run-gate` root is `[SKIP]` in `ephemeral` and `host` environments;
> a known invalid parent fails. Doctor does not create the root or launch a
> lane-user probe, because the first live lane in those environments creates
> it before checking access. A configured `state_root` or an `exec` environment
> is checked through its declared runner, never by host-statting a container
> path. `--check-env` does not run the state-root check. These checks are
> short-lived and read-only (`assay lanes` runs nothing; `command -v` is a
> shell builtin), they judge nothing and write nothing into your tree, and
> ephemeral ones carry `--cgroup-parent` like every container run-gate
> starts. The cost is bounded: `doctor` runs **one inventory probe per
> (environment, judge identity), one batched `command -v` probe per
> environment, and at most one state-root check per assay environment**;
> `--check-env` runs only the first two; a live assay invocation adds one
> state-root check for its lane. A project with no `kind = "assay"` lane
> starts none of these probes, and neither verb ever starts your judged
> lane. If you run `doctor` in a context where starting a container is
> unacceptable, that is the check to know about.

### Lanes that take their comparison base from the gate (RG-26)

assay ≥ 3.0.0 lets a changed-line lane omit `judge.base` and declare
`judge.base_source = "request"` instead — the PR-scoped shape, where the
orchestrator owns which branch point is being judged. Pass it with `--base`:

```toml
# assay.toml — the judgment half owns the fact
[lanes.p129_enumeration_cursor.judge]
mode = "changed_lines"
base_source = "request"        # the gate supplies the base; assay never guesses
```

<!-- run-gate-config -->
```toml
# run-gate.toml — the orchestration half restates NOTHING
schema_version = 1
[environments.tester-unified]
mode = "ephemeral"
image = "tester-unified:local"

[lanes.cursor]
kind = "assay"
environment = "tester-unified"
assay_lane = "p129_enumeration_cursor"
   # Internal source mode: omit assay_command and pins.
```

```bash
run-gate cursor --base "$(git merge-base HEAD origin/main)"
# run-gate: comparison base 4c6eb2b6… (from --base) → --request-base
```

```bash
run-gate cursor          # no --base: the judged tree's own upstream
# run-gate: comparison base 4c6eb2b6… (from merge-base HEAD @{upstream}) → --request-base
```

**In a `ciu worktree` the default is the COMMIT that worktree forked from**
(RG-51). `ciu worktree create|add` records that ref as `base_ref` in
`ciu.worktree-instance.json`, and run-gate prefers it over `@{upstream}`:

```bash
run-gate cursor          # no --base, inside a ciu-managed worktree
# run-gate: /w/proj/ciu.worktree-instance.json pins this tree's fork point at 9f2c1ab… (its base_ref still resolves there) — using that COMMIT instead of merge-base HEAD @{upstream} (4c6eb2b6…)
# run-gate: comparison base 9f2c1ab… (from ciu.worktree-instance.json fork point) → --request-base
```

Why this is the better default: `@{upstream}` is a REMOTE-tracking ref, so
any workflow that batches commits locally before pushing lets it drift behind
local work with nobody changing a line of config — and every changed-line
judgment against it silently widens from "this change's diff" to "everything
since the drift began" (assay's own README, "Pitfall: a `judge.base` literal
pointing at a remote-tracking ref rots silently"). The recorded `base_ref` is
normally a LOCAL branch, which does not drift that way — and what run-gate
hands the judge is not that name but the COMMIT it has just verified the
branch still forks from, so nothing can move it in between. Both the commit
taken and the `@{upstream}` ref it displaced are printed, so you can SEE the
drift.

One boundary this cannot cross: run-gate chooses the base, the judge decides
what to do with it, and assay's `resolve_base` does NOT always compute
`merge-base(base, HEAD)` — when HEAD is a merge commit it returns HEAD's
first parent and discards the supplied base entirely (assay B008). A gate run
on a merge of a sibling topic branch can therefore still judge less than you
expect, under any base, including `--base`. Tracked as `RG-54`.

**Which projects it covers in a monorepo.** run-gate looks in the judged
worktree root and then in the project dir inside it, because ciu writes the
record at that worktree's CIU root. A worktree created with
`ciu_root_offset = "."` therefore covers every project in it; one rooted at a
single project (`ciu_root_offset = "run-gate-project"`) covers that project
only — a gate run for a sibling project in the same worktree finds no record
and falls through to `@{upstream}` exactly as before.

**Which key names the branch (ciu record schema).** In records v1–v5 the
symbolic `base_ref` (`main`) is the branch. From schema **v6** ciu pins
`base_ref` to the fork commit's SHA (equal to `fork_point_sha`) and records the
moving branch separately as `base_branch_ref`; run-gate then applies every
check below to that branch and hands the judge the validated `fork_point_sha`.
It never derives the branch from the SHA. A v6 record whose `base_branch_ref` is
`null` (an adopted checkout, a tag, a raw commit), missing, malformed or unsafe,
or whose SHA fields are missing or disagree, is ignored with a reason; so is a
v1–v5 record whose `base_ref` is a SHA. In both cases pass an explicit `--base`
if you want something other than `@{upstream}`.

**When a record is present but NOT used.** run-gate uses the record only when
its `branch` still matches the tree, git resolves the base branch (`base_ref`
in v1–v5, `base_branch_ref` in v6) to a LOCAL branch there,
`merge-base(branch, HEAD)` still equals the `fork_point_sha` ciu recorded at
creation, and that branch does not already contain the tree's HEAD. Anything
else is ignored with a reason on stdout:

```bash
# run-gate: ignoring /w/proj/ciu.worktree-instance.json: the shared history of 'main' and this tree has MOVED since the record was written: merge-base is now 388737cc2461, the recorded fork point is 9f2c1ab0de34. Whichever way it moved — 'main' absorbed this branch (a merge or a fast-forward), or this branch took 'main' in (a merge or a rebase), or the record describes a different tree — judging against the new merge-base could silently drop work this branch really did, so the record is spent
```

The message names the possible causes without asserting one: the SAME
inequality is produced by merging the base in or rebasing onto it, and
telling an operator to tear down a worktree for a reason that did not happen
is its own defect.

Those rules are load-bearing, not fussiness — each was found by adversarial
review as ways this feature could pass a lane that should have failed:

- `ciu worktree adopt` records the adopted checkout's **HEAD**, and
  `merge-base` against an ancestor of HEAD collapses to that commit. Run the
  gate right after an adopt and it would judge *zero* changed lines.
- A worktree whose work has been merged into its base and not torn down has a
  real, live `base_ref = "main"` that `main` has since absorbed — same
  collapse, and it was true of 4 of the 7 real ciu worktrees in this estate
  when the check was added.
- A worktree whose base has since absorbed its work and which then got one
  more commit looks healthy to every other check — `merge-base` has quietly
  moved to the pre-merge branch tip. Only comparing against the recorded
  fork point catches it.
- A branch that committed work and then REVERTED it passes every check about
  the commit graph and still produces an empty diff, which a changed-line
  floor scores as `0/0 = 100%`. No work escapes the judge there — there is
  none — but a pass on nothing reads exactly like a pass on something, so
  the record loses to a base that has something to judge.

**What it costs you.** Keeping a long-lived worktree current SPENDS the
record: merging the base INTO the branch, or rebasing onto it, moves the
shared history just as surely as merging the other way, so the gate falls
back to `@{upstream}` precisely where an operator was being diligent. That
is a loss of benefit rather than a new hazard — the fallback errs WIDE — but
it means the feature goes inert until the worktree is recreated. Nothing
available locally distinguishes that from a base that absorbed the branch;
it is the same missing fact CIU-106 exists to supply, one level further up.

A frozen id, a tag, a remote-tracking ref, a deleted branch and the tree's own
branch all fail one of the clauses, so none of them displaces `@{upstream}`.
Note this is a **safe** failure: falling back is exactly the pre-RG-51
behaviour.

**Requires ciu CIU-106.** `fork_point_sha` is recorded by `ciu worktree
create|add` only since that change, and `ciu worktree adopt` never records
one. run-gate fails CLOSED without it — a worktree created by an older ciu
keeps its previous `@{upstream}` behaviour until it is recreated, and says so
rather than guessing. What run-gate then hands the judge is the fork COMMIT
itself, not the branch name, so nothing can move it between the check and the
judge's own `merge-base`.

Nothing to configure and nothing to opt out of: `--base` still wins outright,
and an absent, unreadable, non-JSON or otherwise unusable record degrades to
the `@{upstream}` line above. A plain checkout never has this file at all.
run-gate reads the filename, never imports `ciu`.

There is **no `run-gate.toml` key** for this — run-gate DERIVES it by asking
the judge (`assay lanes --json`), so the fact has exactly one spelling. What
that costs you: an assay lane invocation now issues one short read-only
inventory probe in its environment before the judged run.

Refusals name the lane and map to the closed result table. A missing base is
NOT_RUN 3/`no-base`; malformed config or an unsupported flag is ERROR 2:

| situation | what happens |
|---|---|
| delegating lane, no `--base`, no worktree record, tree has no upstream | `lane 'cursor' delegates its comparison base; pass --base REF (worktree has no upstream)` — a guessed base is not a base |
| `--base` on a lane whose `base_source` is not `"request"` | refused, naming the value assay declared (assay would refuse it anyway; this refuses earlier and clearer) |
| `--base` on a command lane with no `{base}` token | refused — the ref could only be silently dropped |
| `--base` with a judge too old to answer (`assay lanes --json` missing) | refused, naming assay **3.2.0** (B044) as the version that carries the inventory |
| no `--base`, judge too old | **nothing changes** — the old judge keeps working exactly as before |

**Conjunction lanes propagate it** the same way they propagate `--worktree`
(RG-1): a token in the lane's own argv.

<!-- run-gate-config -->
```toml
schema_version = 1
[environments.host]
mode = "ephemeral"
image = "tester-unified:local"

[lanes.gate]
kind = "command"
environment = "host"
argv = ["bash", "-c",
        '''run-gate --worktree {worktree} --base {base} cursor &&
           run-gate --worktree {worktree} unit''']
```

A lane carrying `{base}` resolves its ref by the same rules above — recorded
worktree fork point included — so `run-gate gate` on a tree with neither
refuses instead of substituting an empty string.

### Worked example — run-gate × assay, end to end

The halves are documented separately (this file owns orchestration;
[`../assay/docs/CONSUMERS.md`](../assay/docs/CONSUMERS.md) owns judgment).
Here is the whole seam on one page:

1. **Get the judge** into the project with its sidecar:
   ```bash
   mkdir -p tools/assay && cp /path/to/assay-<version>.pyz{,.sha256} tools/assay/
   (cd tools/assay && sha256sum -c assay-<version>.pyz.sha256)
   ```
2. **Declare one R0 lane** in the project root as `assay.toml` — start from
   `assay/templates/consumer-assay.toml`; R0 claims nothing but a
   schema-validated verdict:
   ```toml
   schema_version = 2

   [lanes.unit]
   scope = "S1"
   rigor = ["R0"]
   enforcement = "gate"
   argv = ["/opt/tester-venv/bin/python", "-m", "pytest", "tests", "-q"]
   env = {}
   env_passthrough = []
   budget = "20m"
   allow_argv_append = false
   ```
3. **Declare the run-gate lane** in `run-gate.toml`. For an internal vbpub
   project, use source mode and omit `assay_command` and `pins`:
   <!-- run-gate-config -->
   ```toml
   schema_version = 1

   [lanes.unit]
   kind = "assay"
   assay_lane = "unit"                 # -> assay.toml [lanes.unit]
   environment = "tester-unified"
   [environments.tester-unified]
   mode = "ephemeral"
   image = "tester-unified:local"
   ```
   run-gate installs the selected worktree's `assay/` source and the verdict
   records the actual version. For a consumer outside vbpub, use the explicit
   command + pin form shown in the previous section.
4. **Point the consumer** at it in the canonical, `validate-pointers`-certifiable
   form:
   `argv = ["bash", "-c", "cd {worktree}/<proj> && exec run-gate --worktree {worktree} unit"]`

   Both tool names certify: the script form (`run-gate …`) and — since
   RG-14 — the installed console script (`exec run-gate --worktree {worktree}
   unit`). Discovery snippets (`--list`, `--help`, `--check-env`) and the
   reserved verbs (`doctor`, `validate-pointers`) name no lane by design and
   are exempt from the lane check; prose-named fields (`label`,
   `description`, …) are never parsed as invocations. One deliberate limit:
   a path-anchored console form (`/usr/local/bin/run-gate …`) is NOT
   recognized — it stays uncertified (fail closed) rather than waved through;
   invoke it as bare `run-gate` if you want it certified.
5. **First run:** `run-gate unit` — run-gate verifies the pin, runs the
   lane in the declared environment, prints the verdict path
   (`.assay/verdict-unit.json`) on success AND failure, and maps assay's
   verdict through the closed run-gate result table.
6. **Read the evidence:** `assay verify .assay/verdict-unit.json`
   re-validates the retained verdict later. Keep `.assay/` gitignored
   (adoption step 5) so yesterday's evidence never dirties today's tree.

Adopting R1/R2/R3 (coverage floors, mutation, canary) is an `assay.toml`
edit per assay's docs — the run-gate lane above does not change.

### `kind = "command"` — projects that cannot (or need not) adopt assay

The lane runs a command in the declared environment with the same
orchestration guarantees, and the command's exit status is the verdict:

<!-- run-gate-config -->
```toml
schema_version = 1
[environments.test-runner]
mode = "ephemeral"
image = "yourproj/test-runner:latest"

[lanes.suite]
kind = "command"
environment = "test-runner"
argv = ["pytest", "tests/", "-q"]
clean_tree = true
```

### Native sequences and trunk bases

Use a native sequence when each step is already a run-gate lane. The member
lanes remain independently visible, but the sequence runs them in order,
records each result, and owns one composite admission ticket. A failing member
stops the default `stop_on = "FAIL"` sequence; set `stop_on = "never"` only
when later steps are meaningful after an earlier failure.

Declare the trunk once. A merge commit at HEAD on that local branch resolves
to its first parent with source `trunk-merge-first-parent`. If HEAD on the
declared trunk is not a merge commit, a request-base sequence returns
NOT_RUN with reason `no-base` instead of guessing a diff boundary. `--base`
still explicitly overrides that derivation. run-gate resolves the base once
and passes it only to members whose assay inventory or `{base}` argv declares
that they accept one.

<!-- run-gate-config -->
```toml
schema_version = 1

[project]
trunk = "main"

[environments.host]
mode = "host"

[lanes.schema]
kind = "command"
environment = "host"
argv = ["bash", "-c", "exec tools/schema-check {base}"]
clean_tree = false

[lanes.unit]
kind = "command"
environment = "host"
argv = ["python3", "-m", "pytest", "tests/unit", "-q"]
clean_tree = false

[lanes.release]
kind = "sequence"
lanes = ["schema", "unit"]
stop_on = "FAIL"
```

The consumer points to `run-gate release`. A direct invocation may pass
`--base <commit>` when it has an explicit base. The sequence summary contains
its ordered member results; `history release` and each member's history keep
their own scope.

A sequence has no `environment` of its own — it is forbidden on the sequence
lane — so `run-gate doctor` reports each member instead, transitively through
nested sequences:

```console
$ run-gate doctor
run-gate: doctor: [OK] sequence 'release' member 'schema': environment 'host': mode host (declared [environments.host] in run-gate.toml)
run-gate: doctor: [OK] sequence 'release' member 'unit': environment 'host': mode host (declared [environments.host] in run-gate.toml)
```

A member that is not a declared lane, a sequence cycle, an empty member list,
a member without an environment, or a member whose environment cannot resolve
is a named `[FAIL]` line, never a crash. `--check-env` likewise skips the
sequence itself and covers its members as lanes.

### Legacy shell conjunctions

Some callers need one arbitrary shell pipeline rather than a list of lane
members. Keep that as a `kind = "command"` lane only when run-gate cannot
model the steps directly. A shell `&&` chain still reports its wrapper
command's result, so a nested refusal appears as FAIL 1 at the outer lane;
the log retains the nested reason. Native sequences should own registered
gate composition.

#### Keeping an arbitrary shell pipeline

Keep a `kind = "command"` wrapper only when the steps cannot be declared as
run-gate member lanes. A wrapper that calls nested lanes still reports its own
command result: any non-zero nested run becomes outer FAIL 1, while its log
retains the nested reason. `--worktree` must appear in every nested invocation
so the selected tree is preserved. `--fresh` remains an explicit per-sub-lane
choice in this shell form because run-gate cannot inspect a free-form script.

For registered lane composition, use the native sequence above. It keeps each
member's closed result and history, resolves a request base once, and holds one
composite admission ticket across serial execution.

### Consumer examples

**nyxloom `[gates.<name>]` — thin pointer only:**

```toml
[gates.test-runner]
argv = ["bash", "-lc", '''cd /workspaces/dstdns &&
    CGROUP_PARENT_DEV_GATES="${CGROUP_PARENT_DEV_GATES:?...}" &&
    run-gate gate --worktree {worktree}''']
phase = "implementation"
timeout_seconds = 4500
environment = "test-runner"
asserts = ["tests-pass", "canary-verified"]
```

**CI pipeline (Buildkite/future) — same pattern:**

```yaml
commands:
  - run-gate gate
```

**Human invocation — identical entrypoint:**

```bash
run-gate --list        # discover lanes
run-gate gate          # full implementation gate
run-gate schema        # schema-only iteration
```

`--worktree PATH` selects a DIFFERENT tree to judge (the daemon/dispatch
case): the lane's execution relocates there — assay runs from
`<PATH>/<project>`, pin verification and verdict/artifacts resolve under it,
host lanes get that cwd. The invoking checkout is never judged by side
effect (SPEC R-21).

Scripting against gates: run-gate returns only PASS 0, FAIL 1, ERROR 2,
NOT_RUN 3, or BUDGET_EXCEEDED 4. A command lane's raw status remains in
the result data; ERROR 2 covers configuration and infrastructure failures,
while NOT_RUN 3 means a precondition prevented the judge from starting.

### Daemon-wide gate admission

Admission is an opt-in count cap shared by every run-gate client using one
local Docker daemon. The `[admission]` table is project-local and defaults to
disabled; do not put it in `run-gate.root.toml`. The ticket image must already
exist locally and provide `/bin/true`. run-gate probes it with `--pull=never`
and places ticket containers in the loaded `CGROUP_PARENT_DEV_GATES` slice.
Enabled lane runs and both admission verbs require a local Docker Unix
endpoint; remote Docker contexts refuse because they do not prove the shared
host daemon identity.

<!-- run-gate-config -->
```toml
schema_version = 1

[admission]
enabled = true
ticket_image = "yourproj/test-runner:local"
unreadable_policy = "refuse"
```

An operator publishes the daemon-wide cap from a checkout with this
run-gate config:

```console
run-gate admission set --max-concurrent 2 --unreadable-policy refuse
run-gate admission show
```

Publishing and inspection require a local Docker endpoint. `--replace` advances the
published generation; the newest readable object is the active policy. Each
enabled run creates a numbered ticket with Docker's name-uniqueness check,
then waits in ticket order. Configure `--admission-wait 15m` to change the
default ten-minute wait for one invocation. A full cap at timeout returns
NOT_RUN 3 with reason `no-headroom`. `--override-admission` is a disclosed,
per-invocation exception to a readable count cap; it still records its
ticket. When `enabled = false`, both flags are accepted and ignored with one
notice. With no readable published cap, `unreadable_policy = "refuse"`
returns NOT_RUN; `"unbudgeted"` permits the run without a ticket and says so.

Admission is local to one daemon. A remote Docker context is refused for
`admission set`; do not publish a cap on a different daemon and expect this
client to use it. Resource limits and cgroup placement continue to apply
independently of the admission switch.

### Runner occupancy status

Use `status` when a gate appears to be waiting and you need to see who owns
or is queued for the runner:

```console
run-gate status
run-gate status --worktree .worktrees/feature
run-gate status --json
```

The selected project's `.run-gate/inflight/` records follow `--worktree` and
the config path shown in the report. The exec-lock and Docker admission views
are host-wide: run-gate maps its `/tmp/run-gate-exec-<container>.lock` files
to Linux `/proc/locks` entries, then reports holder and waiter PIDs and their
local liveness. That internal lock is authoritative for run-gate access to
the runner; a project's own wrapper lock cannot reveal runs that did not use
that wrapper. Docker admission output includes the published
`ciu-admission-<generation>` cap, decoded tickets and owner labels, queued or
running state, and exited tombstones.

The command does not start, stop, or remove containers. Its Docker reads are
limited to the admission objects, tickets, and ticket run markers. It does
not repair inflight records or reap tickets. If one source cannot be
read, human output names the failure and JSON returns the available partial
data with `verdict = "ERROR"`, `exit_code = 2` (the closed result table's
ERROR code). It never presents an unreadable source as an empty queue.

When `[admission] enabled = true`, `doctor` also checks that
`ticket_image` exists on the local daemon, a valid published admission object
exists, and its positive `max_concurrent` value is readable. This preflight
uses `docker image inspect` only; it does not run the image. Its remedies are
specific: load the configured image or name a local one, publish a cap with
`run-gate admission set --max-concurrent <desired-N>`, or republish an
unreadable cap with `--replace`. If Docker shows an admission object whose
identity labels are unreadable, doctor names that object and reports that
`admission set` refuses to replace it. Resolve that exact object with the
daemon owner before publishing another cap; doctor will not suggest a
command that cannot succeed safely.

### Consumer timeouts must not cut lanes short

A consumer `timeout_seconds` tighter than the paired lane's `budget`
stops the lane before its declared wall-clock bound and therefore becomes
the effective limit. The rule remains **consumer timeout >= lane budget**
(wider is fine), so run-gate can enforce its hard budget and preserve the
expected resumable outcome. The estate sweep in
`run-gate-project/tests/test_run_gate.py::TestEstateBudgetTimeoutPairing`
enforces this pairing for every trove that points at run-gate lanes (assay's
assert-it pattern, replicated estate-wide); when you add a gate, it joins
the sweep automatically by naming the lane in its argv.

### Host lanes that delegate to a host-path-mounting harness (RG-21)

If a `kind = "command"`, `environment = "host"` lane shells out to your own
script that bind-mounts the repo into a container by HOST path
(srdm's `tools/gate.sh`: `repo_root` = the git toplevel, one
`-v "$host_repo_root:$repo_root"`), that lane is **main-checkout-only today
when the judged tree is a linked worktree.** run-gate is not the defect —
`{worktree}` forwarding and exit-status passthrough are correct — but a
linked worktree's `.git` is a FILE naming an absolute gitdir under the MAIN
checkout, which your single mount does not include, so every in-container git
plumbing call dies:

```
covergate: git rev-list --parents -n 1 HEAD failed: exit status 128:
fatal: not a git repository: /workspaces/vbpub/.git/worktrees/run-gate-rg-sweep
```

`run-gate doctor` names the condition BEFORE the lane fails mid-run:

```
run-gate: doctor: [WARN] host-lane git view (RG-21): /repo/.worktrees/w1 is a
  LINKED worktree; its gitdir is /repo/.git/worktrees/w1, OUTSIDE the tree.
  run-gate's own container lanes are fine (they dual-mount the worktree and
  the git common dir), but
  a host lane delegating to a harness that bind-mounts only the judged tree by
  host path will fail with 'not a git repository: …'. Mount the common gitdir
  into that container too, or pass it as GIT_DIR, or run the lane from the
  main checkout
```

Fix it in YOUR harness, one of three ways — run-gate cannot do it for you,
because it does not own that `docker run`:

```bash
# 1. mount the common gitdir at the path the gitfile records (preferred):
common=$(git -C "$repo_root" rev-parse --git-common-dir)
docker run -v "$host_repo_root:$repo_root" -v "$common:$common" …

# 2. or hand the container an explicit GIT_DIR (still needs the mount above):
docker run … -e GIT_DIR="$common" …

# 3. or run this lane from the main checkout only, and say so in its
#    `description` so the next person does not rediscover it at 2am.
```

Note that `run-gate`'s OWN container/exec lanes never hit this: `R-23`
dual-mounts the judged worktree AND the git common dir (RG-86), so the gitdir
is inside the mounts by construction.
Related: an auto-derived host path for a worktree (e.g. `SRDM_HOST_REPO_ROOT`)
cannot be inferred from `docker inspect`, which maps only the devcontainer's
own `/workspaces/<repo>` — export it explicitly.

## What each lane costs — the `history` verb (RG-27)

Every lane run now leaves a record behind, so "how long does this lane take,
and how does that compare to recent runs" stops being something an operator
remembers until the terminal scrolls. run-gate **measures and persists**; it
decides no rigor/defer policy — that is yours to build on top of this data.

### Adopt it (one line, and it is enforced)

Add the store to your `.gitignore`. This is not a reminder — run-gate asks git
before every write, refuses to write an un-ignored store, and tells you why:

```gitignore
# run-gate lane invocation history (RG-27)
.run-gate/
```

> **BREAKING CHANGE (load-time) — a lane named `history` (RG-27).** `history`
> is now a CLI verb, so it joined `doctor` and `validate-pointers` as a
> RESERVED lane name and `[lanes.history]` is refused when the config loads,
> naming the file. No project in this estate declares one, so nothing here
> moved; an external project that happens to have a lane by that name must
> rename it (the lane was already unreachable — the verb would have won)
> before installing a Run-Gate release that provides `history`.
>
> **BREAKING CHANGE (load-time) — a lane named `footprint` (RG-55, rev 10).**
> Same shape, one more verb: `footprint` joined the reserved set at rev 10.
> No project in this estate declares one either; an external project with a
> lane by that name must rename it before installing a release that provides
> `footprint`.

```
run-gate: WARNING: lane history not recorded: /repo/proj/.run-gate is not
fully git-ignored, and writing there would leave the judged tree dirty for the
NEXT lane's clean-tree check — add '.run-gate/' to the .gitignore covering /repo
```

The lane verdict and mapped exit status are untouched either way: telemetry
is a note in the margin, never the product. The vbpub root `.gitignore` already
carries the entry for internal projects; an external project must add it.

Optionally declare how much trend to keep (default 10 commits per lane; a
central `run-gate.root.toml` may declare it once and a project shadows it whole,
the R-09 rule):

<!-- run-gate-config -->
```toml
# run-gate.toml
schema_version = 1

[history]
keep = 20
```

### Read it

```console
$ run-gate history selftest
run-gate 24.0.0 — lane invocation history
store: /workspaces/vbpub/run-gate-project/.run-gate/history.json
keep:  10  (default (10))

lane selftest
  latest:  pass exit 0  61.4s  4c6eb2b6a1f0  2026-08-31T11:02:17Z
           worktree /workspaces/vbpub
  history: 3 of at most 10 commit(s), oldest first
    COMMIT        OUTCOME   DURATION  STARTED
    9f1c0aa41b3d  pass         58.9s  2026-08-30T18:44:02Z
    b2884e76c0d1  fail          7.2s  2026-08-31T09:15:30Z
    4c6eb2b6a1f0  pass         61.4s  2026-08-31T11:02:17Z
    passes: n=2 median 60.2s (min 58.9s, max 61.4s)
    completed (passes + fails): n=3 median 58.9s (min 7.2s, max 61.4s)
```

`run-gate history` with no lane reports every declared lane. The verb
runs no lane, starts no container, and exits 0 whenever the query itself
worked — an empty store is an answer, not a failure.

**`--worktree` redirects the READ, exactly as it redirects a run.** The store
is per (judged worktree x project), so a query about another tree must name
it — and the answer says which tree it describes:

```console
$ run-gate history selftest --worktree /workspaces/vbpub/.worktrees/feat-x
run-gate 24.0.0 — lane invocation history
tree:  /workspaces/vbpub/.worktrees/feat-x  (--worktree; this answer describes THAT tree, not the invoking checkout)
store: /workspaces/vbpub/.worktrees/feat-x/run-gate-project/.run-gate/history.json
```

In `--json` that tree appears as `worktree_scope` (`null` when the flag is
absent). A `--worktree` that is not a directory refuses (exit 2), and one
that is not a git work tree refuses (ERROR, exit 2) — never a quiet fallback to the
invoking checkout's store, which would hand you tree A's medians under tree
B's name.

**`--json` is honored by `history` and `footprint` (RG-55) only.** Every
other verb refuses it by name rather than printing its human form anyway
(`--list` is already a
machine table).

### Consume it

`--json` is the machine form. Same data, same slots:

```console
$ run-gate history selftest --json
{
  "keep": 10,
  "keep_source": "default (10)",
  "lanes": {
    "selftest": {
      "history": [
        {
          "commit": "9f1c0aa41b3d…",
          "dirty": false,
          "duration_seconds": 58.9,
          "excluded_reason": null,
          "exit_code": 0,
          "git_operation": null,
          "history_eligible": true,
          "lane": "selftest",
          "outcome": "pass",
          "repo": "/workspaces/vbpub",
          "revision": 30,
          "started_at": "2026-08-30T18:44:02Z",
          "worktree": "/workspaces/vbpub"
        }
      ],
      "latest": { "…": "same shape, ANY outcome" },
      "stats": {
        "completed": {"count": 3, "min_seconds": 7.2,
                      "median_seconds": 58.9, "max_seconds": 61.4},
        "passes":    {"count": 2, "min_seconds": 58.9,
                      "median_seconds": 60.2, "max_seconds": 61.4}
      }
    }
  },
  "revision": 30,
  "schema": 1,
  "store": "/workspaces/vbpub/run-gate-project/.run-gate/history.json"
}
```

```bash
# "is this lane cheap enough to always run?" — ask the PASS series, not the
# mixed one: a red lane short-circuits, so its duration is not the cost of
# running the lane, only the cost of failing it.
run-gate history selftest --json \
  | python3 -c 'import json,sys; s=json.load(sys.stdin)["lanes"]["selftest"]["stats"]["passes"]; print(s["median_seconds"])'
```

### The two slots, and why they differ

| slot | what it holds | when it updates |
|---|---|---|
| `latest` | the most recent invocation, **whatever happened to it** — pass, fail, tool error, Ctrl-C, dirty tree, mid-rebase | every invocation, always |
| `history` | a curated trend series keyed by **(lane, commit)**, bounded to the last `keep` commits | only when the measurement is trustworthy (below) |

A run reaches `history` only if it **completed with its own exit status**, on
a **clean** tree, with **no git operation in flight**, at a **resolvable
commit**. Otherwise `latest` still moves and the entry records why it was held
back:

```
  latest:  error  0.1s  4c6eb2b6a1f0  2026-08-31T11:40:03Z
           worktree /workspaces/vbpub
           NOT in history: the judged tree was dirty — the duration does not
           belong to this commit
```

Three things follow that are easy to get wrong, so they are stated:

- **A completed FAIL is kept**, with `"outcome": "fail"`. Its duration is real
  data. That is why the stats are split — merge `passes` and `completed`
  yourself only if your policy wants them merged.
- **`clean_tree = false` does not exclude you.** The test is whether the tree
  *was* dirty, not whether dirt was permitted.
- **`--dry-run` records nothing**, and neither does a configuration error
  (unknown lane, bad key) — no lane started, so there is no result.

### Two worktrees, two stores

The store lives at `<project>/.run-gate/history.json` **inside the judged
tree**, so `--worktree B` writes B's measurement into B's store and never into
the invoking checkout's. That is the concurrency answer: parallel worktree
gates address different files and never contend. Two lanes of one project in
one tree do contend, and are serialized on `.run-gate/history.lock` with an
atomic replace of the store — bounded, unlike the `resources.shared` lock: a
gate never hangs waiting to write telemetry.

Because the replace is atomic, readers take no lock. `history` answers
correctly while a gate is mid-write.

### The footprint manifest — a committed budget (RG-55)

`history` answers "what did this lane cost, in THIS checkout's own store" —
per-instance telemetry, gitignored, never committed. `footprint` answers a
different question: "what SHOULD this lane cost, as a number the whole
project can commit and carry forward." It distills the SAME history (PASS +
history-eligible entries that were actually profiled — peak memory,
+baseline, hot-set p90, CPU cores, memory-full stall, alongside duration)
into `run-gate.footprint.json`, next to `run-gate.toml`, and that file is
**TRACKED — commit it** (`.run-gate/` stays ignored; the two are opposite
answers to "does this belong in git" for a reason: one is this checkout's
raw log, the other is the distilled number everyone downstream should see).

Captured from clean-tree, profiled PASSes on candidate `5b2d169ca36e`
(2026-10-10), with `cgprofile-host-daemon` reachable. `assay-r1` and
`selftest` use daemon-backed container `memory.peak`; host `DAMON_STAT` made
the optional DAMON series unavailable with `EBUSY`, without affecting either
verdict. The short `assay-r3` canaries completed before the daemon could
resolve their container, so their peak remains unknown, not zero. This
supersedes the September `rusage-maxrss` sample, which measured the lane
child's RSS rather than the whole container cgroup: the values from those two
sources are not directly comparable as a workload change. Verbatim output
(the `.run-gate/` path is checkout-relative; the table shape generalizes):

```console
$ run-gate footprint --write
run-gate 24.0.0 — lane resource footprint
store: /workspaces/vbpub/.worktrees/rg89-p1-r2-20261009/run-gate-project/.run-gate/history.json
manifest written: /workspaces/vbpub/.worktrees/rg89-p1-r2-20261009/run-gate-project/run-gate.footprint.json
  LANE                 RUNS  PEAK(med/max)            +BASE    HOT p90   CORES    STALL   DURATION
  assay-r1                5    708 MiB/807 MiB            -          -    0.79     0.7s     184.9s
  assay-r3                5          -/-                  -          -       -     0.0s       1.5s
  selftest                4    568 MiB/777 MiB            -          -    0.75     0.5s     162.6s
```

(This refresh uses the daemon's cgroup `memory.peak` for `assay-r1` and
`selftest`; `peak_at_floor` is unknown for those daemon measurements, not
false. `assay-r3` has no peak because its ephemeral target disappeared before
the daemon attached, so its expected memory value remains null. The manifest
records the source and method per lane. DAMON sample count was zero because
`DAMON_STAT` was enabled; this is optional evidence and does not invalidate
the ordinary cgroup footprint or lane verdict.)

A lane with no profiled PASS run yet is simply OMITTED (absent means
unknown, never zero) — `--write` REFUSES outright (exit 2, naming why) when
**no** lane in the store qualifies yet; run a profiled lane first, then
`--write` again. A `LANE` filter queries one lane's numbers without
touching disk, but is REFUSED together with `--write` (a partial write
would silently drop every other lane's committed data).

**What the manifest is FOR, downstream of this package:**

- **`doctor`** reads it: a per-lane WARNING when the LIVE history's median
  peak has drifted more than `[footprint] tolerance_pct` (default 25%) from
  the committed number, and one WARNING when `distilled_at` is older than
  `[footprint] max_age_days` (default 30) — "this number nobody has
  re-measured in a while" is a fact worth surfacing, never a failure.
- **The run path** reads it too: the daemon's `--meta` carries this lane's
  own committed median as `expected` (so the daemon, and a human reading a
  session's own report, can see "measured vs. expected" without a second
  lookup), and the footprint disclosure line prints a `| manifest <n> MiB`
  tail next to the run's own peak and the standing history median — three
  numbers, one line: what just happened, what has been typical, and what
  was budgeted.
- **RG-55/P5 bounded headroom wait (R-45).** Before a daemon-backed lane starts, run-gate reads
  the gates slice through the profiler registry. When the lane's declared or
  derived request would exceed the 90% headroom threshold, it waits in 30-second
  checks, prints a notice at most once per minute, and then proceeds after the
  bounded `admission_wait` (10 minutes by default) with one warning. It never
  refuses a lane or changes its verdict because of host load; `--dry-run`
  reports `would-wait` without waiting or starting a profiler session. This
  check does not reserve capacity and is not the open atomic RG-56 protocol.
  Ephemeral lanes perform the check before Docker starts and use the parent
  resolved by their declared cgroup policy:
  `cgroup_slice`, then `cgroup_slice_env`, then `$CGROUP_PARENT_DEV_GATES`.
  The selected source is required; absence fails closed instead of launching
  under Docker's unconfined default. Long-running application stacks receive
  `$CGROUP_PARENT_DEV_BACKGROUND` separately through declared `forward_env`.

### Failure evidence and selective footprint updates

When an assay lane returns a non-PASS verdict, run-gate prints a short digest
with the verdict, up to five failing node IDs and exception classes, and the
pytest summary line when it can read one. It copies the current verdict and
progress file before a rerun can overwrite them:

```text
<checkout>/.run-gate/failed/<lane>/<run_id>/verdict.json
<checkout>/.run-gate/failed/<lane>/<run_id>/progress.jsonl
```

The archive is git-ignored and retains the latest ten runs per lane. The
history record names the archive when files were copied. A missing or
non-ignored archive root is disclosed as a warning rather than writing
tracked evidence into the judged tree.

For a budget estimate, a completed profiled FAIL (such as an assay mutation
lane with surviving candidates) is a valid duration and resource sample. It
is excluded by default; include it explicitly:

```console
run-gate footprint mutation --include-failed
run-gate footprint --write --include-failed --lane mutation
```

`--lane` is repeatable. A selective write requires an existing schema-1
manifest, replaces only the named lane entries, and preserves every other
entry. The selected lanes still need completed, eligible, profiled history;
aborted, dirty, unprofiled, and budget-stopped runs are not measurements.

## Per-project-type recipes

**Python service repo with assay (ciu, cmru, assay itself):** the
`kind="assay"` shape above. First adopter was planned as **ciu** (HANDOFF-P01)
but DEFERRED by controller amendment A1 (parallel development); **nyxloom**
adopted first with a `kind="command"` lane (its judgment is still the in-tree
coverage gate until NL-1 migrates it to assay). ciu's adoption will move its
current `nyxloom.toml [gates.tester-unified]` argv INTO the tool and shrink
the gate entry to the two-token form.

**Python app estate with its own runner (dstdns):** dstdns uses `mode = "exec"`
against its CIU-managed persistent `test-runner`. run-gate owns invocation
uniformly (clean-tree, budget, worktree substitution); CIU owns build/deploy/
lifecycle. A not-running refusal prescribes the lifecycle of whichever
authority resolved the container name — declared `container_name` → your
project's own deployment authority; ciu-derived → the ciu lifecycle naming
the config file (never a vbpub-specific remedy for another project's tree).
The old `testing-exec.sh` shim is retired — run-gate execs directly.

**Worktree config and runner follow the JUDGED TREE (RG-47/RG-65/RG-79).**
When invoking from a main checkout with `--worktree PATH`, run-gate derives
the project directory as `PATH` plus that project's path relative to the Git
toplevel. It loads `run-gate.toml` and the nearest `run-gate.root.toml` from
that tree. The target project config must exist; otherwise the invocation
refuses instead of using the main checkout's lanes. The header prints the
selected project config path, and a run's history record keeps its path and
SHA-256 (plus the central config path/hash when used).

For an exec lane whose name is derived from CIU, run-gate reads only the
judged worktree's rendered `<worktree>/ciu.global.toml`. If your worktrees
get their own isolated stacks (`ciu worktree create` — each with its own
rendered config, network and `test-runner`), a missing file refuses with a
remedy to start that worktree's runner using
`ciu up --dir <test-runner stack> --deploy --healthcheck`. This remedy
requires CIU 7.15.2 or newer (CIU-124). A stopped runner gets the same remedy
before `docker exec`. run-gate never falls back to
`<repo>/ciu.global.toml`, because that file names the main checkout's
landscape. Do NOT work around a wrong container by pinning `container_name`
in the tracked `run-gate.toml`: that literal is correct for exactly one
running instance and wrong for the next worktree created. Verify which config
decided the lane and runner — the pre-execution disclosure names both:

```
$ run-gate test-runner --worktree /repo/.worktrees/p147b
run-gate: config: /repo/.worktrees/p147b/run-gate.toml
run-gate: rev 47 | lane test-runner | env project /repo/.worktrees/p147b/run-gate.toml |
  container p147b-8a6bc3-test-runner (ciu.global.toml
  deploy.project_name+environment_tag (judged worktree:
  /repo/.worktrees/p147b/ciu.global.toml)) | slice … (…)
```

A missing or stale `<worktree>/ciu.global.toml` means this worktree's runner
has not been rendered or started — run the worktree's CIU up command, don't
declare a main-checkout name.
Set `$RUN_GATE_EXTRA_MOUNTS=/var/run/docker.sock=/var/run/docker.sock` when a
lane needs Docker-in-Docker. Keep `assay.toml` lanes for the whole-target
coverage work as they land (B1-style).
The implementation gate is declared as a `gate` conjunction lane (see above);
nyxloom consumes it via `run-gate gate --worktree {worktree}`.

<!-- run-gate-config -->
```toml
schema_version = 1
[environments.test-runner]
image = "dstdns/test-runner:latest"
mode = "exec"

[lanes.test-runner]
kind = "command"
environment = "test-runner"
argv = ["bash", "-c", "cd {worktree} && MOCK_MODE=true pytest tests/unit -q"]
clean_tree = false
budget = "30m"
```

**Image-building / host-tooling projects (modern-debian-tools-python-debug,
tester-unified, tester-unified-go):** mostly not Python-coverage material —
assay's R1 judge has nothing to bite. They still get lanes:

<!-- run-gate-config -->
```toml
schema_version = 1
[environments.host]
mode = "ephemeral"
image = "yourproj/host-tools:latest"

[lanes.build]
kind = "command"
environment = "host"
argv = ["./build.sh", "--check"]     # image builds, smoke boots
[lanes.shellcheck]
kind = "command"
environment = "host"
argv = ["shellcheck", "-x", "host-setup/"]
```

The value here is uniformity: `run-gate --list` answers "how do I test
this?" identically in every repo, and nyxloom/CI wire these projects with the
same two-token argv as the Python ones.

**Go projects (tester-unified-go lineage):** `kind="command"` with
`go test ./... -cover` in the Go image environment; if assay grows a Go
coverage adapter later, the lane flips to `kind="assay"` without any consumer
noticing — that boundary is the point.

## Partner integration notes

- **assay:** see the split table above. assay's own docs document
  `assay.toml`'s role (assay backlog B009); run-gate never re-implements
  judgment, and never bypasses assay's clean-tree/verdict rules.
- **nyxloom:** gates become thin argv pointers to named `run-gate.toml`
  lanes; all test definitions (suite argv, sequencing, conjunctions) live in
  the SSOT. The daemon keeps scheduling, timeouts, and asserts. The four-trap
  manual recipe in vbpub AGENTS.md is superseded for adopted projects (the
  section gains a pointer here).
- **cmru:** release gates call the same lanes; cmru's dependency checking is
  what makes the fresh-clone story work for the image-baked assay judge
  (build order: assay wheel → tester-unified image → gates run).
- **Buildkite (future):** agents on the remote hosts run long lanes
  (mutation, fuzz) via the identical entrypoint; `--list` output is the
  pipeline generator's input. Design note lives in assay B009.

## Distribution — installed wheel and SemVer

Run-Gate is distributed as a wheel and invoked through its installed
`run-gate` console command. Install the same released SemVer in every
environment that drives lanes. `run-gate --version` reads the installed
wheel metadata, so consumers can compare the version with their declared
minimum. There is no consumer script to copy or synchronize.

Run-Gate loads `run-gate.toml` from the current project directory. Start the
command from the directory whose lanes you want to run; when selecting a
separate worktree, use `--worktree` as well. Internal `revision` fields in
history or recovery records identify the record writer and remain distinct
from the installed package version; they are not compatibility floors.
See the [design rationale](docs/DESIGN-GUIDE.md#semver-names-the-installed-tool)
for the distinction between package compatibility and saved-record provenance.

A release artifact can be installed directly, for example:

```console
python -m pip install https://github.com/volkb79-2/vbpub/releases/download/run-gate-v24.0.0/run_gate-24.0.0-py3-none-any.whl
run-gate --version
```

### Release coverage baseline

The Run-Gate package release lane measures changed executable lines against
the newest reachable stable `run-gate-vX.Y.Z` tag. That release tag remains
the same before and after merging to `main`; a moving branch name would make
the merge-base collapse to the candidate itself and hide its source changes.
The lane invokes its coverage helper this way:

```console
python tools/coverage_gate.py --repo . --base latest-release-tag \
  --allow-first-release --coverage-json coverage.json --source run_gate.py
```

On the first stable release, when no stable Run-Gate tag exists, the explicit
`--allow-first-release` option reports this diff check as `SKIPPED`: there is
no released source baseline to compare. The test suite still runs, and the
separate `assay-r1` lane must supply whole-source line-and-branch
qualification before merge. If stable tags exist but none are reachable from
the candidate, the helper still errors rather than treating that as a first
release. Later releases always compare against the newest reachable stable
tag and enforce the same 100% changed-line floor.
For the first stable release, run `run-gate assay-r1` from the project root
against the reviewed source; this whole-source line-and-branch lane supplies
the qualification that has no prior release diff baseline.
See the [design rationale](docs/DESIGN-GUIDE.md#release-coverage-uses-a-fixed-tag-baseline-and-follows-content-across-renames)
and [release-gate overview](README.md#version-probe).

### Exact-wheel release verification

The supported `cmru release run-gate-project` workflow prepares the wheel from
the versioned changelog section before running `selftest`. CMRU supplies the
protected `CMRU_RELEASE_GATE_COMMIT` to that gate, and preparation refuses a
missing or mismatched commit. It builds the tester image from those exact wheel
bytes; the lane checks the image's baked SHA-256
against a candidate receipt before pytest runs. CMRU rebuilds the release wheel
after tagging and refuses publication if its digest does not match that
receipt. Keep the package, wheel, and image steps inside this release workflow
so the gate and published asset refer to the same candidate commit and bytes.
Preparation also checks the transaction checkout's release tags against
origin. When it refuses because this checkout's newest tag is older than
origin's, fetch the tags and retry:

```bash
git fetch --tags origin
cmru release run-gate-project --set-version 24.0.1 --from-worktree
```

A first stable release is accepted only when origin shows no stable
`run-gate-vX.Y.Z` tag. If origin cannot be read, that is a refusal, not a
first release.
See the [design rationale](docs/DESIGN-GUIDE.md#the-release-gate-tests-the-published-wheel)
and [tester image build procedure](../tester-unified/README.md).

For a first-party zero-release bootstrap, the release transaction can build
its tester inputs from the selected repository source and an upstream Python
base. Set both choices explicitly:

```bash
RUN_GATE_TESTER_UNIFIED_BASE_IMAGE=mcr.microsoft.com/devcontainers/python:3.14-trixie \
RUN_GATE_TESTER_CLI_EXTENDED_RESOLVE=source \
  cmru release run-gate-project --set-version 24.0.0
```

The source mode builds the CMRU runtime's `cli-extended` wheel from the same
transaction checkout and verifies its digest before installing it in the
tester image. With either variable unset, the normal configured base and
dependency mode apply; a missing first-party artifact does not trigger a
fallback.

After `cli-extended` has been released, use `RUN_GATE_TESTER_CLI_EXTENDED_RESOLVE=pinned`
(the Dockerfile pin names the released asset) or `latest`. `latest` follows a mutable pointer, so
it also requires `RUN_GATE_TESTER_CLI_EXTENDED_SHA256` to be the published wheel's SHA-256
(for example `CLI_EXTENDED_RELEASED_SHA256` from CMRU's release-identity check); the tooling
refuses to build without it, passes it to `tester-unified/build-image.sh`, and, before the gate
may use the image, requires its `org.vbpub.cli-extended.wheel-sha256` label to equal the
expected digest (the supplied digest for `source`/`latest`, the Dockerfile pin otherwise).
`RUN_GATE_TESTER_CLI_EXTENDED_SHA256` is refused with any other mode, where it would be
silently unused. Preparation also requires the image's `org.vbpub.tester-unified.base-image`
label to equal `RUN_GATE_TESTER_UNIFIED_BASE_IMAGE` (or the Dockerfile's default base when it
is unset). A malformed base reference, an unknown mode, or a missing or misplaced digest is
refused before any wheel or image build starts.


## Anti-goals (read before extending)

- NO second parser of `run-gate.toml`, ever. A consumer that wants lane
  metadata calls `run-gate --list` (stable, machine-readable output).
- NO judgment policy in `run-gate.toml` — floors and rigor belong to assay.
- NO third-party runtime dependencies — the installed wheel uses only the
  Python standard library. A checkout still needs the Run-Gate wheel installed
  before the supported CLI can run.
- NO silent defaults for environment facts (slice names, physical paths):
  DERIVE or READ or FAIL, per AGENTS §4.2a.

Help, usage, argument errors, and lane banners identify the same installed
SemVer from wheel metadata. Before selecting a lane, a consumer can run
`run-gate --version`; it prints exactly one `run-gate X.Y.Z` line on stdout,
exits 0 without loading project configuration, and writes nothing to stderr.
Missing or malformed installed metadata is a refusal.
