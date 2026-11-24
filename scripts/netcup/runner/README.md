<!-- Evidence scrub 2026-11-10: environment-specific host identities, host addresses, controller paths, SSH key material and fingerprints were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->
# Runner host scripts

Generic, reviewed scripts that turn a freshly installed Debian host (see `install-host.py`) into a parallel test
runner: a `runner` account, a Btrfs CoW area, detached-job and checkout-sync helpers, the gate tools, the
`tester-base`/`tester-unified` images and a cockpit container that drives Run-Gate lanes. Runner-specific host settings
(identity, uid, disk layout and pinned versions) come from `runner.env`, which is gitignored. Copy
`runner.env.example` to `runner.env`, fill it in, and upload the whole
directory (scripts, `lib.sh`, `run-job`, `sync-from-devcontainer`, `runner.env`) to the host together.

The cockpit's service endpoints and access GIDs come from the host-published `/etc/host-endpoints.env` record, not
`runner.env`. The loader accepts only a regular, non-symlink file owned by root with no group or other write bit. It
parses comments, blank lines and the published `KEY=VALUE` alphabet as data; malformed and duplicate entries fail with
the file and line named. It exports only the runner's keys. `start-cockpit-runner.sh` refuses with the first missing
variable's name. Repair the record through its owner (host-setup for Docker API and BuildKit, `cgprofiled install` for
cgprofile, or `toposd install` for topos), then run
the corresponding host checker and retry; do not add socket paths or endpoint GIDs to `runner.env`. The cockpit
receives the record's endpoint values, including both `CGPROFILE_OBSERVER_SOCKET` and its directory, gets read-only
identity binds of the published socket directories, and joins the Docker, cgprofile-control, topos-observe and
mdt-buildkit groups by canonical numeric GID. It never joins the observer group; Run-Gate grants that GID to observer
lanes when it starts them. GIDs must be positive decimal values without leading zeros, and cockpit startup verifies
that `MDT_HOST_DOCKER_GID` matches the local `docker` group. The example pins Run-Gate 25.4.0 for the host endpoint
cutover. See the [runner endpoint rationale](../DESIGN-GUIDE.md#runner-cockpit-uses-the-host-endpoint-record)
and the [runner host setup steps](../CONSUMERS.md#runner-cockpit-endpoints).
Host-setup is mandatory: `start-cockpit-runner.sh` has no degraded mode. Without its
`/etc/host-endpoints.env`, Docker API and BuildKit facts, and cgprofile observer, the cockpit cannot be started
consistently; a raw-socket variant is unsupported because observer lanes and governed builds depend on those facts.

Provisioning normally precedes endpoint publication. As that explicit bootstrap exception, `provision-runner.sh`
adds the runner to the host's group named `docker`; it does not choose or hard-code a GID. Host-setup publishes the
GID of that same group, and provisioning checks the record if it already exists. Later cockpit startup checks the
published GID against the local group before using it.

Every script sources `lib.sh`, which loads `runner.env` (or `$RUNNER_ENV`) and defines `identity_check`: the host must
answer to the configured FQDN, static hostname and `/etc/vbpub/server-id` before anything is changed. Verify the
provider-side identity (API id, name, hostname, address, state) from the controller before each remote write; the
scripts can only check what the host itself reports.

## Gates-tier memory policy

A pure test host protects only its measured control plane: lanes may overcommit RAM and page (swap exists from the
install plan), and a lane that needs more RAM than the host has finishes slowly instead of failing. That is the
operator decision of 2026-11-16 (option 1), measured on a 4 vCPU / 8 GiB test host. `provision-gates-limits.sh` renders
it into drop-ins only — no unit file is ever edited — and every number is derived from the host itself.

```bash
sudo ~/runner-scripts/provision-gates-limits.sh            # provisioning runs this; re-run it alone any time
sudo ~/runner-scripts/provision-gates-limits.sh --dry-run    # print the plan, write nothing, restart nothing
sudo ~/runner-scripts/provision-gates-limits.sh --adopt      # ONE TIME on a host an operator configured by hand
```

What it writes, and why:

| Drop-in | Key | Why |
| --- | --- | --- |
| `<gates slice>.d/20-memory-policy.conf` (the slice name is `GATES_SLICE`, default `dev-gates.slice`) | `MemoryHigh=infinity`, `MemoryMax` = nominal `MemTotal` − 1536 MiB, `MemorySwapMax` = nominal `SwapTotal` − 8192 MiB | no throttling mark (a `MemoryHigh` throttles the tier before the control plane is ever at risk); the RAM cap keeps the control plane's measured floor (anon ≈ 0.1 GiB + hot file ≈ 0.5 + kernel ≈ 0.3 + cockpit/run-gate ≈ 0.3 = ≈ 1.2 GiB, rounded down to a 1024 MiB floor plus a 512 MiB slack); the swap cap leaves the host its own share so an oversized lane pages instead of ending the whole tier |
| `docker-.scope.d/20-oom-group.conf` | `[Scope] OOMPolicy=kill` | gives **every** transient `docker-<id>.scope` `memory.oom.group=1`: an OOM inside one container kills that container's own process tree, not an arbitrary process of it. Deliberately NOT on the slice: a slice-level group would kill every lane when one runaway exhausts the tier |
| `ssh.service.d`, `systemd-logind.service.d`, `systemd-journald.service.d`, `docker.service.d`, `cgprofiled.service.d` `30-oom-protect.conf` | `[Service] OOMScoreAdjust=-900` | the global OOM victim ranking scores processes by their own size and score, and a cgroup floor never enters it; on the measured kernel an adj-0 2 MiB service and an adj-0 lane process score about alike, so a lane made of many small processes would tie with, or win against, the host's own services. **Only units whose own unit file sets nothing, or something weaker, get this drop-in**: Debian 13's packaged `systemd-journald.service` sets `OOMScoreAdjust=-250` (weaker, so the policy raises it to `-900`; verify on the host with `grep -H OOMScoreAdjust /usr/lib/systemd/system/{systemd-journald,ssh,systemd-logind,docker}.service`), while a unit whose own file already sets `-999`/`-1000` keeps its value — see “protection is never reduced” below |

**The ceiling is derived from the NOMINAL size, not the raw kB.** A kernel reserves a little, so an 8 GiB host reports
7935 MiB — and raw arithmetic then produced one MiB less than the figure the operator had measured and chosen for it.
Each total is therefore rounded **up** to the install plan's step (`GATES_NOMINAL_STEP_MIB`, default 1024 = one GiB)
before the reserve is subtracted: 7935 → 8192 → `MemoryMax=6656M`, and 64 GiB of swap → 64 GiB − 8 GiB = 57344M. The
rule is exercised by tests on three host sizes (4/8/16 GiB), so a hard-coded number cannot pass.

Overrides (they pin a value the operator measured for this host; they never replace a fact the host can state):
`GATES_MEMORY_MAX_MIB`, `GATES_MEMORY_SWAP_MAX_MIB`, `GATES_CONTROL_FLOOR_MIB` (default 1024),
`GATES_MEMORY_SLACK_MIB` (default 512), `GATES_NOMINAL_STEP_MIB` (default 1024), `GATES_SWAP_RESERVE_MIB` (default 8192).
A pin that is not below the raw `MemTotal`/`SwapTotal`, a total that cannot be read, or a host whose swap is not above its
reserve is refused by name — never papered over with a default. The measured 8 GiB policy is `MemoryMax=6656M`
(6.5 GiB): at 7 GiB the host fell to 107 MiB `MemAvailable` with 5.5 s ssh latency, at 6656M it kept 465-591 MiB with
unchanged latency.

Consequences to know before changing it:

- **`OOMScoreAdjust=-900` is inherited by ssh sessions.** sshd restores the unit's score for its children, so a heavy
  process started directly in an ssh login is hard for the global OOM killer to pick. Lane processes are unaffected:
  Docker gives a container its own (0) score. Do not put a *stronger* value on `ssh` than on the rest of the control
  plane, and never on a unit an operator runs heavy work through.
- **A slice restart is the only way to apply new limits to a running slice.** `daemon-reload` alone does not re-apply
  cgroup limits to an active slice, and a restart resets the slice's `memory.events` counters — including `oom_kill`,
  which remote-gate's `INCONCLUSIVE-OOM` check reads as a delta. The script therefore restarts the slice **only** when
  it has no tasks anywhere (every `cgroup.procs` under it is empty) and only when the live values disagree; otherwise it
  refuses with exit 4 and installs/keeps the drop-ins. Never restart the slice while a lane runs. Idle detection is a
  read of `cgroup.procs` taken just before the restart: a task created between that read and the restart is not covered,
  which is why this step is never part of lane startup and belongs to provisioning or to a quiet tier. An **unreadable**
  process list is not an idle slice: the script exits 3 instead of guessing, because the only thing the answer gates is
  the restart that resets those counters.
- **A host with no swap refuses (exit 3) by design.** The policy pages an oversized lane instead of killing it; without
  swap there is nothing to page onto, so rendering a `MemorySwapMax` would change the policy's meaning. Lower the reserve
  only to a swap share the operator has measured; otherwise this host is not a candidate for this policy.
- **A loaded but INACTIVE slice is a no-op (exit 0).** A freshly installed host (or a slice deactivated after a finished
  run) has no live cgroup, so there is no limit to compare and no counter to reset: the drop-ins are staged and apply at
  the first start. Only an active slice whose `ControlGroup` cannot be resolved is an error (exit 3).
- **`OOMScoreAdjust` needs no restart, and the script does not restart services.** A drop-in applies at the unit's next
  start; writing `/proc/<MainPID>/oom_score_adj` reproduces the measured state immediately, so ssh, logind, journald and
  docker (which every cockpit depends on) keep running.
- **Protection is never reduced, in either half of the policy.** `oom_score_adj` is a *ranking*, so a LOWER number is
  MORE protective (`-1000` strongest, `+1000` weakest). Two rules follow, and both are measured defects from the
  2026-11-16 adoption:
  - *live*: the MainPID score is written **only when the current value is higher (less protective) than `-900`**. A
    `-1000`, `-999` or `-900` value is left alone and reported as “already at least as protected”; an unreadable value
    is exit 8, never treated as `0` (the read gates a write, and a write on an assumed `0` is exactly how a value nobody
    read gets replaced). sshd protects its own listener at `-1000` at runtime (not from its unit file), so the live rule
    leaves that value alone: the adoption run lowered it to `-900` and the host agent had to restore `-1000` by hand.
    The unit's `-900` drop-in still matters, because it is the score sshd hands to its children (the sessions).
  - *persistent*: the `30-oom-protect.conf` drop-in is rendered **only for a unit whose own unit file sets nothing (or
    something less protective)**. A drop-in is merged after the unit file, so `-900` on a unit whose file says `-1000`
    REPLACES that value at the next start: a downgrade invisible to `--dry-run` and to any cgroup read. Such a unit is
    reported and left with no drop-in; a less protective own value is still converged (the policy strengthens it, e.g.
    Debian 13's packaged `systemd-journald.service`, whose file sets `OOMScoreAdjust=-250` — read it on the host with
    `grep OOMScoreAdjust /usr/lib/systemd/system/systemd-journald.service`), and an unreadable own value is exit 3. A
    drop-in this tool rendered on an EARLIER run is **retired** (`<file>.retired-<UTC stamp>`, never deleted) if the unit
    now protects itself better, because that file would otherwise keep winning at the next start.
  Each half compares only its own fact: the live rule reads `/proc/<MainPID>/oom_score_adj` and is independent of the
  unit file (a main process that is unprotected NOW is brought to `-900` even when the unit file is stronger, because
  that write raises protection); the persistent rule reads the unit's OWN fragment (`systemctl show -p FragmentPath`),
  never the effective property as its answer to "does this unit protect itself" — the effective value already includes a
  drop-in this tool rendered on an earlier run. It is read for ONE decision: when the policy would be RENDERED, the
  effective score is compared too, because a **vendor drop-in this tool cannot see** (a packaged one under
  `/usr/lib/systemd/system/<unit>.d/`, which this tool never writes to) may already protect the unit more strongly. An
  equal-or-more protective effective value means nothing is rendered and the message names the directory to inspect; an
  empty or unreadable one is exit 3, never an assumed `0`. This is the same rule mdt host-setup's dedicated-dev
  protection follows (`never weaken a unit that is already protected harder`).
- **A foreign drop-in is refused, not overridden — and refused before anything is written.** A file under the slice's `.d/`
  from another owner (for example the hand `10-remote-gate-limits.conf` a 2026-11-08 cut-over left with `MemoryHigh=5G`)
  that sets one of the memory keys is named and refused with exit 5, so a refused run leaves no half-written policy behind.
  **Every** competing file is named before the refusal (a plain `--dry-run` lists them all, instead of stopping at the
  first one and letting the operator discover the next on a later run), and that one list covers the slice's `.d/`, the
  `docker-.scope.d/` scope policy and every protected unit's `.d/`.
  CPU-only drop-ins (`CPUQuota`, `TasksMax`) are tolerated: CPU quota stays a host-owned input this script does not invent.
  The same check covers `OOMPolicy` under `docker-.scope.d/` and `OOMScoreAdjust` under each protected unit: a
  *less protective* value is refused (a later fragment wins) while an *equal or more protective* one is kept and reported
  (the same policy twice, for example the dedicated-dev host protection mdt host-setup renders, or a packaged `-1000`).
  A file at this tool's OWN path is judged the same way: its value is read, so an equal-or-more protective one there is
  kept and reported, and only a file that would LOWER the unit's protection is refused.
- **The docker prefix drop-in covers every container**, the cockpit and any buildkitd scope included: an OOM in one of
  them kills that whole container (backlog NC-63).
- **The slice's own fragment is mentioned only when it really differs.** A fragment carrying a memory key is overridden
  by the drop-in (a drop-in is merged after the unit file), so the script says so — but only when the VALUES differ. A
  fragment that spells the same amount another way (`MemoryMax=6G` against the rendered `6144M`) or one that carries
  only CPU keys produces no note, because a convergence note on every run of a converged host is noise nobody reads.

The cockpit container itself is started with `--oom-score-adj` (`COCKPIT_OOM_SCORE_ADJ`, default `-500`): it drives
run-gate, so the global OOM killer must not prefer it, and a weaker value than the host units' `-900` keeps it killable.
Its own `memory.oom.group` comes from the same docker-scope drop-in. The cockpit launcher sets the flag at *creation*:
re-create the cockpit when the running one was created before this flag existed (`docker inspect -f '{{.HostConfig.OomScoreAdj}}' "$COCKPIT_NAME"`,
which reads `0` for "unprotected"). A `docker exec` into the cockpit inherits that score (still `0` on a cockpit created
before the flag), never the `-900` of a host unit.

### Adopting a hand-configured host (one time)

A host an operator configured by hand — the 2026-11-16 measurement itself, and any other host that is given the same
policy by hand — has drop-ins this tool did not render. Without a flag they are refused (exit 5), and the message names
`--adopt`:

```bash
sudo ~/runner-scripts/provision-gates-limits.sh --adopt --dry-run   # see what would be adopted, change nothing
sudo ~/runner-scripts/provision-gates-limits.sh --adopt             # adopt once
sudo ~/runner-scripts/provision-gates-limits.sh                     # afterwards: a NO-OP that changes nothing
```

`--adopt` recognises a file **only** at one of this tool's own paths, and only when every non-comment line is a section
header naming exactly the section this file owns (`[Slice]`, `[Scope]`, `[Service]`) or a directive whose key belongs to
that policy. **The values are not compared**: a hand file legitimately writes `MemorySwapMax=56G` where the tool renders
`57344M` (the same amount, a different spelling), so the adopted file becomes the tool's owned rendering and the original
is kept aside. Anything else — `CPUQuota`, `MemoryLow`, a second section, a typo in the key, no directive at all — stays
foreign and is refused even with the flag. Every file at one of those paths is inspected before anything is written, so
one `--dry-run` reports **all** foreign and adoptable files at once instead of stopping at the first.
A recognised hand drop-in that sits at a protected unit's own `30-oom-protect.conf` while that unit's unit file sets a
**stronger** value (the v1001 migration shape: hand `-900` over a packaged `-1000`) is adopted too — and then NOT
replaced. The never-reduce rule outranks the policy value, so the hand file is retired and the unit keeps its own
protection; a run without `--adopt` refuses it and names the flag.
The adopted content is **copied, never deleted**, to `<file>.retired-<UTC stamp>` — a name systemd does not load — so
what was superseded stays readable and can be restored by hand. On a host that already matches, `--adopt` adopts nothing.
Provisioning passes the flag only when the operator sets `GATES_MEMORY_ADOPT=1`, so a migration is an explicit act.

Host-side verification (read-only):

```bash
systemctl show "$GATES_SLICE" -p MemoryHigh,MemoryMax,MemorySwapMax
cat /sys/fs/cgroup/dev.slice/dev-gates.slice/memory.{high,max,swap.max}
ps -eo pid,comm,oom_score_adj | grep -E 'sshd|dockerd|cgprofiled|journald|logind'
# a started lane container must read 1: place the probe IN the tier and read the host's own cgroup files (never a
# --cgroupns=host container, which would inspect a namespace instead of the host's own view)
# The image is tester-base:local, which provisioning's build-tester-base.sh leaves on every runner: the probe runs on
# a host that may have no network, so it never depends on a registry (a bare `alpine` is a silent `docker pull`, and
# `--pull=never` here turns a missing local image into an error instead of a fetch).
s=dev-gates.slice; cg=/sys/fs/cgroup/dev.slice/$s
docker run -d --rm --pull=never --cgroup-parent "$s" --name probe-oomgroup tester-base:local sleep 30 >/dev/null
cat "$cg"/docker-*/memory.oom.group          # 1 = an OOM inside one container kills that container's whole tree
docker rm -f probe-oomgroup
```

The last probe starts a throwaway container in the gates tier: run it only while the tier is otherwise quiet, and remove
it by its exact name (the command does). Running it with `--cgroup-parent` (rather than reading an arbitrary container's
scope) is what makes the reading apply to the tier the policy is written for. Its image is `tester-base:local` — the tag
`build-tester-base.sh` leaves on every runner — with `--pull=never`: a bare `alpine` was measured on 2026-11-16 to be
absent from the host, so the documented check silently pulled an image, and a runner may have no network at all. A
missing image now fails the probe instead of fetching one.

A process started with `docker exec` runs INSIDE the cockpit container and inherits the cockpit's own OOM score
(`-500`), not a `-900` unit score. An existing cockpit keeps the score it was created with (`0` before the launcher
carried `--oom-score-adj`), so re-create it once; `docker inspect -f '{{.HostConfig.OomScoreAdj}}' "$COCKPIT_NAME"`
reports `0` for "unprotected".

## Cockpit restart policy

`start-cockpit-runner.sh` starts the cockpit with Docker restart policy `unless-stopped`. After a host reboot Docker
brings `mdt-cockpit-runner` back automatically, so the host-sat queue can launch a lane without an operator running
`docker start`. If the cockpit was deliberately stopped before the reboot, it stays stopped; `unless-stopped` does not
override an explicit `docker stop`.

Inspect the live policy with:

```bash
docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' "$COCKPIT_NAME"
```

The operator applied the live workaround on both test hosts on 2026-11-09 by updating the existing container. The
launcher now carries the same durable setting, so a replaced cockpit inherits it:

```bash
docker update --restart unless-stopped "$COCKPIT_NAME"
```

To revert the live workaround, restore Docker's default policy:

```bash
docker update --restart no "$COCKPIT_NAME"
```

The full reboot acceptance still requires a test-host reboot and a queue launch without a manual cockpit start.

| Script | Runs as | What it does |
| --- | --- | --- |
| `provision-runner.sh` | root, detached | writes `/etc/vbpub/server-id`, creates the runner account (uid `RUNNER_UID`, default 1003), formats and mounts the Btrfs CoW partition, installs `run-job` and `sync-from-devcontainer` and the runner scripts with `runner.env` into `~/runner-scripts` (the path the `run-job ... -- bash ~/runner-scripts/<script>` invocations use), renders and applies the gates-tier memory policy through `provision-gates-limits.sh`, creates the tools venv and `uv` |
| `provision-gates-limits.sh` | root on host (called by `provision-runner.sh`, re-runnable alone) | renders from this host's own `MemTotal`/`SwapTotal` the gates-tier drop-ins (`MemoryHigh=infinity`, `MemoryMax`, `MemorySwapMax`, `docker-.scope` `OOMPolicy=kill`, `OOMScoreAdjust=-900` on the control-plane units), sets the live OOM scores in place, and restarts the idle slice only when its live limits disagree; `--adopt` performs the one-time migration from a hand-configured drop-in; see [Gates-tier memory policy](#gates-tier-memory-policy) |
| `install-augeaswrap.sh` | root on host | verifies the supplied Augeaswrap and cli-extended wheel hashes, installs them offline into `/opt/augeaswrap/<version>/venv`, and links `/usr/local/bin/augeaswrap` |
| `install-fleetd.sh` | root on host | verifies the supplied fleetd and cli-extended wheel hashes, installs them offline into `/opt/fleetd/<version>/venv`, and links `/usr/local/bin/fleet`; `--check` reports whether that exact wheel hash is already installed without writing |
| `sync-from-devcontainer` | runner | imports a controller-made Git bundle into `~/checkouts/<commit>`; default calls update `~/src/vbpub-current`, while `--no-current-link` preserves it for T3 imports |
| `run-job` | runner | runs a named command detached with `~/jobs/<name>/job.log` and an `EXIT` marker |
| `setup-assay-venvs.sh`, `setup-gate-tools.sh` | runner, via `run-job` | Assay venvs (3.13, 3.14) and the tools venv (cmru, cli-extended, the published Run-Gate wheel) from the synced checkout |
| `build-tester-base.sh`, `build-tester-unified.sh` | runner, via `run-job` | build the two images inside the gates slice |
| `start-cockpit-runner.sh` | runner | creates the cockpit container (docker-run form of the host-folder devcontainer) and remaps its `vscode` user to the runner uid |
| `run-lane.sh` | runner, via `run-job` | runs one registered Run-Gate lane from a runner checkout inside the cockpit using `RUN_GATE_BIN` or the established cockpit default |
| `remote-gate.py` | controller | `remote-gate run <host\|auto> <worktree-of-any-git-repo> <lane>`: ships a committed worktree (vbpub, dstdns, any repo) to a runner host and runs one lane there (see CONSUMERS.md, "Remote gate"); host table in a gitignored JSON (`remote-gate-hosts.example.json`) |
| `setup-cockpit-run-gate.sh` | runner, via `run-job` | installs one pinned Run-Gate wheel into its own cockpit venv `~/.venvs/gate-tools<version>` (for repos that need a newer Run-Gate than the cockpit default, e.g. dstdns >= 25.0.1 through `remote-gate.py`) |
| `run-r2-campaign.sh` | runner, via `run-job` | supervises native Assay R2 in a clean persistent checkout; gives each `-s<N>` start its own campaign directory, counts completed candidates, supports `stop <campaign>`, clamps `--jobs` to the lane maximum, copies a fresh Assay verdict byte for byte, and writes `final-exit` plus a PASS/NOT_PASS summary |
| `run-cowtree-assay-smokes.sh` | runner, via `run-job` | cowtree and Assay workspace tests against the real CoW root |
| `setup-cockpit-tools.sh` | runner | installs the pinned Run-Gate wheel and the checkout's Assay into the cockpit user's `~/.venvs/gate-tools` (run once after `start-cockpit-runner.sh`; `run-lane.sh` needs it) |
| `run-jobs-window.sh`, `run-jobs-compare.sh` | runner, via `run-job` | native-R2 `--jobs 1/3/4/6/auto` comparison on fresh state: time-boxed throughput, or full wall time |

## Persistent T3 R2 campaigns

The runner can resume a native Assay R2 campaign from the persistent
checkouts/<commit> tree after a Run-Gate budget exit. Each supervision start
uses a unique -s<N> suffix for both its detached job and campaign directory;
the stable campaign base still owns one lock. Logs and terminal records live
under campaigns/<base>-s<N>/, so a re-supervision never overwrites the
previous start. The checkout's .assay state remains in place.

Import a T3 bundle without changing the runner's shared source symlink:

~~~bash
sync-from-devcontainer --no-current-link \
  ~/incoming/t3-<tool>-<tag>-<lane>.bundle t3/<tool>-<tag>
~~~

Run the campaign with the same start suffix on the job and campaign names.
run-job requires the separator before the command:

~~~bash
run-job t3-<tool>-<tag>-<lane>-s1 -- \
  bash ~/runner-scripts/run-r2-campaign.sh \
  t3-<tool>-<tag>-<lane>-s1 <commit> <project-dir> <lane>
~~~

Re-supervision increments s<N> and keeps the same unsuffixed campaign base.
The current terminal result is campaigns/<base>-s<N>/final-exit; exit 4
means the supervisor stopped after the configured attempt cap or two attempts
without a completed candidate. campaign-summary.json records NOT_PASS,
reason=budget, and stop_reason=max-attempts or stalled. Completed candidate
events are counted from Assay event=candidate records; replayed baseline events do
not reset the stall counter.

Run several shard instances of one lane at once (NC-42). Functional floor:
`--instances`, `--merge-only` and `--carry-over-main` first read the cockpit
Run-Gate's `--help` (through run-lane.sh, so `RUN_GATE_BIN` applies) and
refuse with exit 2 before any campaign state unless it lists `--shard K/N`,
the STRICT `--merge-shards N` and `--carry-over DIR` (RG-265/RG-266). That
Run-Gate refuses an Assay judge below 12.2.0 by name before admission. The
established older cockpit Run-Gate (25.0.2, 25.4.0) is refused for these
options; the legacy single campaign (none of them) never probes and keeps
working on it. An unreadable help is exit 75.

~~~bash
run-job t3-<tool>-<tag>-<lane>-s1 -- \
  bash ~/runner-scripts/run-r2-campaign.sh \
  t3-<tool>-<tag>-<lane>-s1 <commit> <project-dir> <lane> --instances 2 --jobs 2
~~~

The fleet start supervises one child campaign per shard,
`<base>.i<K>of<N>-s<M>`, each with its own lock, lane container, attempts,
stall counter (it counts only its own
`progress-<assay_lane>.<lane>.shard<K>of<N>.jsonl`)
and `final-exit`. Child output is in `campaigns/<base>-s<M>/<child>.log`.
Children start `R2_INSTANCE_STAGGER_S` seconds apart (default 20), so
baselines do not peak together. When every shard has ended, the fleet runs
`<base>.merge-s<M>` (`run-gate <lane> --merge-shards N`): a STRICT merge that
needs every shard store complete for the current judge and plan, folds them
into the main store and writes the whole-lane verdict, which is copied to the
fleet's `verdict.json`. The
fleet's `final-exit` is the merge run's exit; `campaign-summary.json` lists
every child (`mode=fleet`). Remaining run-gate arguments are passed to every
child; `--jobs` is clamped per child as before.

Options are validated by presence: `--instances` 1..16, `--merge-only` and
`--shard-total` 1..64, `--shard-offset` 0..63 (offset + instances <= total),
`--carry-over-main` a non-empty one-line reason; a zero, empty, malformed or
repeated value is refused by name (exit 2), never read as absent.

Memory admission estimates expected peaks; the lane's `resources.memory`
remains the full Run-Gate ceiling on each container because Run-Gate has no
per-invocation memory override. For a capped lane, the default per-worker
estimate is `ceil(lane memory / jobs_max)`. Each instance's scaled lane
ceiling is `max(128 MiB, ceil(lane memory x workers per instance / jobs_max))`;
the minimum is an accounting floor, not a Run-Gate cap. `--jobs N` selects the
workers per instance. When a lane declares `jobs = "auto"` and a fleet start
omits `--jobs`, the supervisor passes `--jobs max(1, ceil(jobs_max / K))` to
each shard, where K is `--instances`. For example, jobs_max 3 gives one worker
for each of three instances and two workers for each of two instances. An
explicit `--jobs` overrides that default; the same argument is passed through
to Run-Gate. With no lane jobs setting, Run-Gate leaves Assay serial.
`R2_WORKER_MEM_MIB` overrides the per-worker estimate with a measured peak.
`R2_INSTANCE_MEM_MIB` keeps its existing meaning as a measured expected peak
per container and overrides the worker estimate. Values are positive decimal
MiB below 10^7.

The expected-peak budget is `MemAvailable - R2_HOST_RESERVE_MIB +
R2_EXPECTED_PEAK_CREDIT_MIB`. The reserve defaults to 1024 MiB and the
operator-provided credit defaults to 0 MiB. Expected peaks must fit this
budget; free swap and KSM state never add automatic credit. Set
`R2_EXPECTED_PEAK_CREDIT_MIB` only to a measured value that the operator
chooses to count (for example, an independently measured KSM saving converted
to MiB from shared pages and page size). KSM's `run=1` alone does not show
that a lane's processes opted in.

Separately, the sum of full declared container caps (`K x resources.memory`)
must fit `MemAvailable x R2_OVERCOMMIT`. `R2_OVERCOMMIT` accepts 1.0 through
4.0 and defaults to 2.0; it applies only to that cap comparison. The scaled
lane ceiling above is printed for arithmetic review but does not change
Run-Gate's per-container cap. The guard prints both budgets and exits 75 when
either comparison fails. An uncapped lane still needs `R2_INSTANCE_MEM_MIB` or
is refused by name (exit 2); a worker estimate alone does not imply a Run-Gate
cap.

Every campaign start prints KSM `run`, `pages_sharing`, `pages_shared` and
lane eligibility. The runner cannot infer process opt-in, so eligibility is
reported as unverified; workloads merge only when they use `MADV_MERGEABLE` or
`PR_SET_MEMORY_MERGE`. Missing KSM sysfs values print as `unavailable`; the
campaign continues. The values are also saved for `status <base>`. Override
the sysfs root with `R2_KSM_SYSFS` for tests. KSM is observed only; the runner
does not enable it or use its state as expected-peak credit.

For a native Assay R2 lane that has fewer workers than CPUs, the runner prints
an advisory `--instances K` hint, where `K = floor(nproc / workers per
instance)`, if each worker is single-threaded. It does not change the run.

`status <base>` prints one line per start, child and merge run (running or
`final_exit`, the last attempts line, completed candidates). Fleet starts also
show the saved memory arithmetic, including the explicit expected-peak credit,
followed by the KSM observation when available.
`stop <base>` on a fleet stops every child that is still active and its exact
containers, then the fleet itself (exit 143 everywhere).

Split one campaign across two hosts: both hosts import the same commit; host A
runs `--instances 2 --shard-total 4 --shard-offset 0`, host B
`--instances 2 --shard-total 4 --shard-offset 2`. A host that ran only part of
the shards ends with `final-exit` 75 (`reason=merge-pending`). Copy host B's
`<checkout>/.run-gate/assay-shards/<project>/<lane>/<assay_lane>/{2of4,3of4}`
into the same path of host A's checkout (a manual step), then run on host A:

~~~bash
run-job t3-<tool>-<tag>-<lane>-s2 -- bash ~/runner-scripts/run-r2-campaign.sh \
  t3-<tool>-<tag>-<lane>-s2 <commit> <project-dir> <lane> --merge-only 4
~~~

A missing, incomplete or foreign shard store refuses the merge by name (the
merge never executes a lost shard's candidates); finish or copy it first.

Restart after a judge change (an Assay upgrade, a local lane commit) without
re-executing finished candidates: add `--carry-over-main "<reason>"`. It
passes `--carry-over <checkout>/.run-gate/assay-state/<project-dir>` with the
reason to Run-Gate, so `killed`/`survived` records of the previous judge are
imported with provenance (Assay B530). Resource changes (`jobs_max`, memory,
budgets) need no carry-over since Assay 12.2.0 (B529).

Stop an active or orphaned campaign with the supported command:

~~~bash
bash ~/runner-scripts/run-r2-campaign.sh stop t3-<tool>-<tag>-<lane>
~~~

The stop command signals the supervisor and tracked lane client and sampler,
removes lane containers carrying that start's exact Run-Gate job and campaign
labels, and uses each attempt's recorded exact container name when labels are
unavailable. It waits until the campaign lock is free. Run-Gate 25.2.0 or newer
is required for label-based stop. Run-Gate 25.0.2 names a plain-clone container
`run-gate-<checkout-basename>-<lane>-<pid>-<epoch>`; the persistent checkout's
basename is the full commit ID. The newer name schema starts
`<repo>-run-gate-<project>-<lane>-` and uses the remote-derived repository
name. If neither labels nor a recorded exact name identifies a container,
stop fails with a manual exact-name instruction. A persistent
checkout must be clean before a campaign starts. Commit any lane-config change
first; Run-Gate has no budget override, so a shorter test budget requires a
committed run-gate.toml change in the imported checkout.

run-lane.sh accepts RUN_GATE_BIN as an absolute path inside the cockpit.
When omitted, it keeps the established cockpit default. Hosts with cgprofile
3.0.0 should point RUN_GATE_BIN to Run-Gate 25.4.x; set it in the runner's
private runner.env for observer lanes. RUNNER_ENV can point to the runner
environment file when a copied script is kept beside lib.sh but outside the
installed runner-scripts directory. A copied supervisor needs both lib.sh and
run-lane.sh beside it.

## One uid for everything

The `tester-unified` lane user is uid 1003. The runner account (which owns the checkouts and the CoW root) and the
cockpit user (which invokes `run-gate`) must be the same uid, otherwise the `worktree` lane fails as `dubious
ownership`, `Permission denied` under `.run-gate`, or `MDT_COW_ROOT must be owned by the non-root caller uid`
(Run-Gate RG-150, backlog NC-04, mdt-cockpit MDT-052). `provision-runner.sh` creates the account with `RUNNER_UID` and
refuses a different existing uid unless `RUNNER_UID_ALLOW_CHANGE=1`; `start-cockpit-runner.sh` aligns the cockpit user.

## Host profile defaults

HP-D10, accepted 2026-11-10: both test hosts target Run-Gate 25.4.1 as the
default after old campaign and resume state has been collected. Keep the
existing pins and venvs until that collection is complete; then retain the
prior venvs for rollback. This records the accepted target, not a claim that
either host has switched. No host hosts-file was changed for this note.

## Order

1. **P1 — reinstall the provider host** from the reviewed Debian-install-v2 bundle. The controller checks the target
   identity before submitting the install.
2. **P2 — pin the new host key and check the installed layout.** Verify the provider identity and disk layout before
   making further host changes.
3. **P3 — provision the runner.** Run `provision-runner.sh` as a detached root job. After provisioning alone, the host
   can run the remote `selftest` lane; the cockpit, CoW smokes and cgprofile-observed lanes are not ready. Provisioning
   also stages the gates-tier memory policy ([Gates-tier memory policy](#gates-tier-memory-policy)); when the tier is
   still busy it prints `GATES_MEMORY_POLICY_PENDING` and one idle re-run of `provision-gates-limits.sh` applies it.
4. **P4 — sync the reviewed source.** Bundle the reviewed commit and required `assay-v*` and `ciu-v*` tags, upload it
   to `~/incoming`, then run `sync-from-devcontainer`. Host-setup and runner venv setup use this checkout.
5. **P5 then P6 — install Augeaswrap, then run host-setup.** Use `install-augeaswrap.sh` with the verified local
   wheels before running `mdt-cockpit/host-setup/install.sh` and its wizard. Set `PYTHONDONTWRITEBYTECODE=1` for
   host-setup (NC-10). Host-setup publishes the endpoint record and loads the slices and Docker facts needed by later
   steps.
6. **P7–P11 — install services and gate prerequisites.** Install cgprofile and topos, run endpoint and host checks,
   create the runner venvs from the synced checkout, then build `tester-base` and `tester-unified`. Start each
   detached job only after the previous `EXIT` is 0. Host-setup must precede the tester images; the cockpit itself
   does not require the `tester-unified` image.
7. **P12–P15 — start, register and accept.** Start the cockpit and install cockpit Run-Gate, register controller
   pins and repository profiles, run the selftest/observer/CoW/cgroup/workspace/reboot acceptance checks, then enable
   the queue and require its first PASS. Run one gate at a time unless the host is deliberately overcommitted.

`build-tester-unified.sh` sets `BUILDX_BUILDER=default MDT_BUILDX_PASSTHROUGH=1`: the managed rootless BuildKit cannot see the locally
loaded `tester-base:local` (mdt-cockpit MDT-049).

## Install fleetd for host facts

`fleetd` owns the read-only `fleet facts collect` and `fleet guard validate`
commands a managed host runs, and its wheel declares `cli-extended` as a real
dependency, so both wheels are installed together. Debian 13 refuses
`pip install` into the system Python (PEP 668), so `install-fleetd.sh` installs
into a versioned venv, verifies both wheel hashes first, and publishes
`/usr/local/bin/fleet`. It contacts no host and downloads nothing.

```bash
wheel_dir=/var/cache/host-wheels
fleetd_sha256=$(awk 'NR == 1 { print $1 }' "$wheel_dir/fleetd-0.3.0-py3-none-any.whl.sha256")
cli_extended_sha256=$(awk 'NR == 1 { print $1 }' "$wheel_dir/cli_extended-0.5.0-py3-none-any.whl.sha256")
sudo bash "$HOME/runner-scripts/install-fleetd.sh" \
  --wheel-dir "$wheel_dir" \
  --fleetd-version 0.3.0 --fleetd-sha256 "$fleetd_sha256" \
  --cli-extended-version 0.5.0 --cli-extended-sha256 "$cli_extended_sha256" \
  --check
sudo bash "$HOME/runner-scripts/install-fleetd.sh" \
  --wheel-dir "$wheel_dir" \
  --fleetd-version 0.3.0 --fleetd-sha256 "$fleetd_sha256" \
  --cli-extended-version 0.5.0 --cli-extended-sha256 "$cli_extended_sha256"
command -v fleet
```

`--check` exits 0 when `/opt/fleetd/<version>/venv` already holds the exact
fleetd and cli-extended hashes you pass, exits 1 with "NOT installed" otherwise,
and writes nothing either way, so it is safe to run before an apply and safe to
run as root. The apply run is idempotent for the same hashes and reinstalls when
a hash changes; it refuses a symlinked install path, an incomplete existing venv,
and a non-symlink file at the command path rather than overwriting it. The
version strings are explicit inputs; the script invents no default. The
per-host `facts.toml` at `/etc/fleetd/facts.toml` remains host-owned and is not
written by this script.

## Install augeaswrap for host-setup

`host-setup` requires `augeaswrap` on root's `PATH`. Install the release
wheels from a directory already copied to the host; this script does not
download packages. The Augeaswrap wheel depends on `cli-extended`, so provide
and verify both wheels and their release SHA-256 sidecars before running the
installer. The expected hashes are explicit inputs to the installer.

```bash
wheel_dir=/var/cache/host-wheels
augeaswrap_sha256=$(awk 'NR == 1 { print $1 }' "$wheel_dir/augeaswrap-0.2.0-py3-none-any.whl.sha256")
cli_extended_sha256=$(awk 'NR == 1 { print $1 }' "$wheel_dir/cli_extended-0.6.0-py3-none-any.whl.sha256")
sudo bash "$HOME/runner-scripts/install-augeaswrap.sh" \
  --wheel-dir "$wheel_dir" \
  --augeaswrap-version 0.2.0 --augeaswrap-sha256 "$augeaswrap_sha256" \
  --cli-extended-version 0.6.0 --cli-extended-sha256 "$cli_extended_sha256" \
  --dry-run
sudo bash "$HOME/runner-scripts/install-augeaswrap.sh" \
  --wheel-dir "$wheel_dir" \
  --augeaswrap-version 0.2.0 --augeaswrap-sha256 "$augeaswrap_sha256" \
  --cli-extended-version 0.6.0 --cli-extended-sha256 "$cli_extended_sha256"
command -v augeaswrap
```

The dry run verifies both wheel files and shows the versioned venv, offline
pip command and root-PATH link without writing. The apply run uses
`python3 -m venv`, then `pip install --no-index --no-deps`; repeat runs with
the same wheel hashes reuse the venv and repair the symlink if needed. Review
the hashes against the release record before passing them in; a sidecar copied
beside a wheel is only useful when its provenance is trusted.

## Host without mdt host-setup

This section records the cut-over path observed on the reinstalled `<host>` runner host on 2026-11-08. It is documentation
for the degraded, manual path, not a claim that the checked-in launcher already implements it. The reviewed
`start-cockpit-runner.sh` still requires the complete host endpoint record, and the cgprofile-aware and no-host-setup
cockpit launchers used below remain outside this repository under `<host>/runner-scripts-local/` (backlog NC-21).
Moving that path into supported scripts and re-proving it on a fresh host are still open.

The observed path used the existing runner provisioning and image build sequence, then added these host prerequisites:

1. installed systemd slice units accepted by assay's `cgroup-parent.sh`;
2. the exact pinned PostgreSQL image needed by the SQL qualification lane;
3. cgprofile 3.0.0, its groups, sockets and six-key endpoint record;
4. Run-Gate 25.4.0 in an additive cockpit venv;
5. a recreated cockpit with cgprofile endpoint forwarding, the control GID, the Docker GID, and the explicit
   `<checkout>` workspace bind.

Pause the host-sat queue and let its current lane finish before any cockpit recreation or host change. The pause flag
and drain procedure are in [Host-sat queue pause flags](#host-sat-queue-pause-flags). Keep the pre-change container
inspection, generated scripts and wheel directory until the cut-over is accepted or rolled back.

### 1. Install the two slice marker units

`assay/tools/cgroup-parent.sh` requires both `dev-interactive.slice` and `dev-gates.slice` to be loaded from installed
unit fragments. It also requires positive CPU and I/O weights and at least one non-default resource control on the
gates slice, and, for the B105 self-qualification path, finite RAM headroom of at least `2147483648` bytes. The
weights are read as effective unit properties; the checker does not test whether they were explicitly assigned in
the unit file. A host reinstalled without mdt host-setup had only runtime cgroups, no `FragmentPath`, so the lane
failed with the excerpt preserved in the handoff:

```text
cgroup-parent: Docker-host unit "dev-interactive.slice" is not a loaded installed slice (FragmentPath=)
```

The checker's current diagnostic also formats `LoadState`, but the handoff excerpt does not preserve its runtime
value.

The source records the required directives but does not preserve the exact `Description=` text, comments, staging
commands, or process-count checks. The following are minimal proposed unit bodies that preserve the recorded
directives; the exact observed file wording and installation transcript are `to be confirmed`.

`/etc/systemd/system/dev-interactive.slice`

```ini
[Unit]
Description=Interactive devcontainer tier

[Slice]
```

`/etc/systemd/system/dev-gates.slice`

```ini
[Unit]
Description=Gate and lane container tier

[Slice]
# assay cgroup-parent.sh requires positive effective weights and a non-default control;
# IOWeight=101 is the non-default control in this observed unit.
CPUWeight=100
IOWeight=101
# Recorded value for the cut-over host. Derive the value for another host from MemTotal.
MemoryMax=6G
```

The first file is a marker: it deliberately carries no resource controls. The second supplies positive effective
`CPUWeight` and `IOWeight` properties, one non-default value (`IOWeight=101`), and finite memory headroom, satisfying
the controls assay checks. The source does not record an explanation of the observed host's I/O scheduler behavior.

`MemoryMax=6G` is the value recorded on the cut-over host, not a portable default. Do **not** copy it blindly to a
different host, and do not write it into the unit fragment at all: the limits belong in the drop-ins of
[Gates-tier memory policy](#gates-tier-memory-policy), which `provision-gates-limits.sh` derives from that host's own
`MemTotal`/`SwapTotal` (`MemoryMax` = the host's NOMINAL `MemTotal` rounded up to the step, minus the control-plane
reserve; `MemoryHigh=infinity`). The unit fragment
then carries only what makes it a *loaded installed slice* for `cgroup-parent.sh` (`CPUWeight`, `IOWeight`, and the
finite `MemoryMax` a host-setup render may include, which the drop-in overrides). Installing the two fragments is still
the manual step tracked as NC-25; the memory values are no longer a hand decision.

The following validation and installation sequence is proposed; the source does not preserve the exact observed
argv or file mode. Run it as root:

```bash
install -d -m 0755 <home>
# Write the two exact [Unit]/[Slice] bodies shown above to:
#   <home>/dev-interactive.slice
#   <home>/dev-gates.slice
systemd-analyze verify \
  <home>/dev-interactive.slice \
  <home>/dev-gates.slice
install -m 0644 -o root -g root \
  <home>/dev-interactive.slice \
  /etc/systemd/system/dev-interactive.slice
install -m 0644 -o root -g root \
  <home>/dev-gates.slice \
  /etc/systemd/system/dev-gates.slice
systemctl daemon-reload
```

The source records that `dev-gates.slice` was restarted while empty so the newly configured memory limit was applied;
it does not record the exact emptiness checks. Before restarting, verify that no processes or child cgroups exist and
stop if either is present. This emptiness check is proposed rather than an observed transcript:

```bash
gates=/sys/fs/cgroup/dev.slice/dev-gates.slice
processes="$(<"$gates/cgroup.procs")"
child_cgroup="$(find "$gates" -mindepth 1 -maxdepth 1 -type d -print -quit)"
if [[ -n "$processes" || -n "$child_cgroup" ]]; then
  echo "dev-gates.slice is not empty; do not restart it yet" >&2
else
  systemctl restart dev-gates.slice
fi
```

Verify the installed identity and controls:

```bash
for unit in dev-interactive.slice dev-gates.slice; do
  systemctl show "$unit" \
    -p Id,LoadState,ActiveState,FragmentPath,DropInPaths,Transient,MemoryMax,CPUWeight,IOWeight
done
```

Expected evidence is `LoadState=loaded`, a real `FragmentPath` under `/etc/systemd/system/`, and
`dev-gates.slice` showing `CPUWeight=100`, `IOWeight=101`, and a finite `MemoryMax`. Verify the actual cgroup value and
the assay contract as used by the lane:

```bash
cat /sys/fs/cgroup/dev.slice/dev-gates.slice/memory.max
assay/tools/cgroup-parent.sh --memory-reserve-bytes 2147483648
```

The source records that a later full `tester-unified` attempt ran after the units were installed and failed tests; it
does not record the results of these two standalone invocations, which are `to be confirmed`. The rollback below
removes only the two files added by this workaround. Retain the units while any cockpit that uses
`dev-interactive.slice` is running:

```bash
# Stop and remove that cockpit first, and confirm the slices have no processes or child cgroups.
rm -f /etc/systemd/system/dev-interactive.slice \
      /etc/systemd/system/dev-gates.slice
systemctl daemon-reload
```

The source preserved the gates file that existed before the controller added `MemoryMax=6G` at
`<home>/dev-gates.slice.before-memmax` on the observed host. It is an evidence backup, not an
automatic restore command.

### 2. Make the pinned PostgreSQL image locally present

Assay's SQL qualification checks with `docker image inspect "$IMAGE"` and, when absent, reports the exact immutable
reference to pull. It never pulls by itself. The exact qualification image is:

```text
postgres:18-alpine@sha256:d3e1620b530c944afa6e887d22eb899824da68e19c52024bf98f5220c88a65b2
```

Prepare and verify it:

```bash
image='postgres:18-alpine@sha256:d3e1620b530c944afa6e887d22eb899824da68e19c52024bf98f5220c88a65b2'
docker image inspect "$image" >/dev/null 2>&1 || docker pull "$image"
docker image inspect "$image" >/dev/null
```

The observed host initially had no PostgreSQL image. Pull the pinned reference when it is absent:

```bash
docker pull \
  postgres:18-alpine@sha256:d3e1620b530c944afa6e887d22eb899824da68e19c52024bf98f5220c88a65b2
```

The same string is the constant in `assay/gate/python/qualify_sql.py`; `docker run` then uses `--pull=never`, so a
missing or differently resolved image fails loudly rather than silently fetching another version.

After every lane that needs this image has finished, remove the exact immutable reference and verify that it is absent:

```bash
docker rmi 'postgres:18-alpine@sha256:d3e1620b530c944afa6e887d22eb899824da68e19c52024bf98f5220c88a65b2'
docker image inspect 'postgres:18-alpine@sha256:d3e1620b530c944afa6e887d22eb899824da68e19c52024bf98f5220c88a65b2'
```

The inspect command must return nonzero after removal. The pull and inspect paths above use this same reference.

### 3. Install cgprofile 3.0.0 and publish the endpoint record

The observed cut-over acquired and hash-checked these release wheels from their GitHub releases:

| Wheel | Version | SHA-256 |
| --- | --- | --- |
| `cgprofile-3.0.0-py3-none-any.whl` | 3.0.0 | `4dd5f1855be5a3265a994c187d4046d2e6e7cebd7b335b303112d524eee45711` |
| `cli_extended-0.5.3-py3-none-any.whl` | 0.5.3 | `5a88979592ec5d3c7bebae57787d1674d88d72413c764f2dcde82f672fd267d4` |
| `augeaswrap-0.1.0-py3-none-any.whl` | 0.1.0 | `461d45bdc0480d58a55dc952baec5e2c029ffa2bfab0c28ef332d74cf20d8714` |

Put all three in one root-owned directory, verify their sidecars or recorded hashes, and run the installer through
zipimport from the wheels themselves. Use the same command first with `--dry-run --json`, inspect the plan, then apply
it. The observed non-interactive inputs were:

```bash
wheel_dir=<home>/wheels
cgprofile_wheel="$wheel_dir/cgprofile-3.0.0-py3-none-any.whl"
cli_extended_wheel="$wheel_dir/cli_extended-0.5.3-py3-none-any.whl"
augeaswrap_wheel="$wheel_dir/augeaswrap-0.1.0-py3-none-any.whl"

PYTHONPATH="$cgprofile_wheel:$cli_extended_wheel:$augeaswrap_wheel" \
python3 -m cgprofile.cli.cgprofiled install \
  --wheel '3.0.0@sha256:4dd5f1855be5a3265a994c187d4046d2e6e7cebd7b335b303112d524eee45711' \
  --cli-extended-wheel '0.5.3@sha256:5a88979592ec5d3c7bebae57787d1674d88d72413c764f2dcde82f672fd267d4' \
  --augeaswrap-wheel '0.1.0@sha256:461d45bdc0480d58a55dc952baec5e2c029ffa2bfab0c28ef332d74cf20d8714' \
  --wheel-source "$wheel_dir" \
  --control-group cgprofile-control \
  --observer-group cgprofile-observer \
  --add-user control=runner \
  --enable --yes \
  --dry-run --json
```

After reviewing the plan, repeat that exact command without `--dry-run --json` to apply it. It creates the control and
observer groups, socket units and daemon, installs the immutable versioned venv, and publishes
`/etc/host-endpoints.d/cgprofile.env`, aggregated into `/etc/host-endpoints.env`. On the observed host this produced:

```text
CGPROFILE_CONTROL_SOCKET
CGPROFILE_CONTROL_SOCKET_DIR
CGPROFILE_CONTROL_GID
CGPROFILE_OBSERVER_SOCKET
CGPROFILE_OBSERVER_SOCKET_DIR
CGPROFILE_OBSERVER_GID
```

The observed values were control GID 986 (runner is a control member) and observer GID 985 (no users; Run-Gate grants
that GID only to observer lane containers). The runner must not be added to the observer group.

Verify the service, record and group membership:

```bash
systemctl is-active \
  cgprofile-control.socket cgprofile-observer.socket cgprofiled.service
/opt/cgprofile/current/venv/bin/cgprofiled doctor --json
grep -E '^CGPROFILE_[A-Z_]+=' /etc/host-endpoints.env
getent group cgprofile-control cgprofile-observer
id -nG runner
```

The observed result was all three units active and enabled, six endpoint keys, and
`cgprofiled doctor --json`: 12 OK, 0 warn, 0 fail. `cgprofilectl doctor` additionally had one non-blocking
`report-extra` warning because matplotlib was absent; the daemon and gates did not need it.

Rollback is below, after the cockpit has been returned to a launcher and shape that do not use cgprofile.

### 4. Install Run-Gate 25.4.0 beside the cockpit default

The cgprofile endpoint layout is implemented by Run-Gate 25.4.0. The observed cockpit default was 25.0.2, which
hard-coded the old socket layout and ignored the `CGPROFILE_*` environment. The supported installer adds the new
version in its own venv and leaves the default in place:

```bash
run-job setup-rg-25.4.0 -- \
  bash ~/runner-scripts/setup-cockpit-run-gate.sh \
    25.4.0 '<wheel-sha256-from-run-gate-v25.4.0-release>'
```

The source report preserved only the abbreviated wheel hash `d417e38b...f383`; the full 64-character value is
`to be confirmed` from the `run-gate-v25.4.0` release `.sha256` asset. Do not substitute a guessed hash.

Verify the additive venv without changing the default:

```bash
docker exec -u vscode "$COCKPIT_NAME" bash -lc \
  '"$HOME/.venvs/gate-tools2540/bin/run-gate" --version'
docker exec -u vscode "$COCKPIT_NAME" bash -lc \
  'command -v run-gate; run-gate --version'
```

Expected results are 25.4.0 from `gate-tools2540` and the prior default still resolvable. A lane selects the new venv
through its private hosts-file `repos.<repo>.run_gate_bins` entry. The observed cut-over used a private release hosts
file and did not change the queue's hosts file (NC-23). Rollback is to re-pin that hosts-file entry to the prior
Run-Gate, or use the default. After every host profile has stopped referencing this venv, remove only that additive
cockpit environment with the command in rollback step 4 below; it does not touch the cockpit's default Run-Gate.

### 5. Recreate the cockpit with cgprofile and the workspace bind

The cut-over used three operator-run local variants copied to the host's `~runner/runner-scripts/`:

| Script | Purpose |
| --- | --- |
| `start-cockpit-runner-nohostsetup.sh` | Pre-cgprofile cockpit shape with the raw Docker socket bind and no endpoint mounts. |
| `start-cockpit-runner-cgprofile.sh` | Adds the six cgprofile environment keys, control/observer directory binds and control GID. |
| `start-cockpit-runner-cgprofile-ws.sh` | Adds all of the above plus the required nested `<checkout>` bind. |

These files are not under `scripts/netcup/runner/` yet. Copy the reviewed local variants before this step; the exact
controller copies are under `<host>/runner-scripts-local/`.

The cgprofile-and-workspace variant must make these exact cockpit additions:

| Addition | Exact observed form |
| --- | --- |
| Workspace bind | `--mount type=bind,source="$RUNNER_HOME/checkouts/vbpub",target=<checkout>,consistency=cached` |
| Control directory bind | `--mount type=bind,source="${EP[CGPROFILE_CONTROL_SOCKET_DIR]}",target="${EP[CGPROFILE_CONTROL_SOCKET_DIR]}",readonly` |
| Observer directory bind | `--mount type=bind,source="${EP[CGPROFILE_OBSERVER_SOCKET_DIR]}",target="${EP[CGPROFILE_OBSERVER_SOCKET_DIR]}",readonly` |
| Control group | `--group-add "${EP[CGPROFILE_CONTROL_GID]}"` |
| Docker group | `--group-add "$docker_gid"` |
| Docker GID env | `-e "MDT_HOST_DOCKER_GID=$docker_gid"` |
| Endpoint env | One `-e "$key=${EP[$key]}"` for each of the six required `CGPROFILE_*` keys |

The script sets `docker_gid` and parses the record as data with this exact command:

```bash
docker_gid=$(getent group docker | cut -d: -f3)
```

It refuses `/etc/host-endpoints.env` unless it is a regular non-symlink root-owned file with no group/other write
bits. It accepts only lines matching
`^(CGPROFILE_[A-Z_]+)=([A-Za-z0-9_./:@+-]+)$`, requires all six keys, requires both GIDs to be positive decimal
values, and passes the values with `-e`. It does not source the record and does not type values into the launcher.

The extra workspace bind is needed because `assay/tools/self-qualification-container.sh` requires `findmnt` for the
selected worktree to report target exactly `<checkout>` with an absolute FSROOT other than `/`. The outer
`checkouts -> /workspaces` bind alone made the target `/workspaces` and the lane refused:

```text
self-qualification-container: the selected worktree is not inside the declared <checkout> bind
```

With the queue paused and its in-flight lane finished, recreate the cockpit by running the reviewed local variant as
the runner/root path used by the source:

```bash
bash <home>/runner-scripts/start-cockpit-runner-cgprofile-ws.sh
```

Do not run the reviewed repository `start-cockpit-runner.sh` for this degraded path: it requires the additional
host-setup Docker API, BuildKit and topos record keys that this host does not publish (NC-18/NC-21).

Verify the six endpoint values and the Docker GID:

```bash
docker_gid=$(getent group docker | cut -d: -f3)
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$COCKPIT_NAME" |
  grep -E '^(CGPROFILE_[A-Z_]+=|MDT_HOST_DOCKER_GID=)'
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$COCKPIT_NAME" |
  grep -F "MDT_HOST_DOCKER_GID=$docker_gid"
```

Verify the control GID was added and the observer GID was not:

```bash
docker inspect -f '{{json .HostConfig.GroupAdd}}' "$COCKPIT_NAME"
docker exec -u vscode "$COCKPIT_NAME" id
```

The observed process groups contained the Docker GID and cgprofile-control GID but not cgprofile-observer. Run-Gate
stats the observer socket and gives only the observer lane `--group-add <observer-gid>`.

Verify the endpoint-directory binds are read-only identity mounts:

```bash
docker inspect -f \
  '{{range .Mounts}}{{println .Source "->" .Destination "rw=" .RW}}{{end}}' \
  "$COCKPIT_NAME"
```

Verify the workspace target and host FSROOT:

```bash
docker exec -u vscode "$COCKPIT_NAME" \
  findmnt --target <checkout> --noheadings --output TARGET,FSROOT
```

The observed output reported target `<checkout>` and FSROOT `<checkout>`.

Run-Gate 25.3 and later require `RUN_GATE_HOST_DOCKER_GID` or `MDT_HOST_DOCKER_GID` as a strict decimal value before
cockpit observer lanes can pass. Run-Gate intentionally does not infer it from the Docker socket's owning group. The
cockpit launcher resolves the host `docker` group on the host and exports:

```bash
docker_gid=$(getent group docker | cut -d: -f3)
# In the cockpit docker-run argv:
--group-add "$docker_gid"
-e "MDT_HOST_DOCKER_GID=$docker_gid"
```

The observed values were Docker GID 990, cgprofile-control GID 986, and cgprofile-observer GID 985. These are examples
from that host, not constants for another host.

### 6. Run acceptance checks

Use the project's registered lanes for test execution. The observed cut-over accepted the path with these checks:

| Check | Expected result |
| --- | --- |
| `assay/tools/cgroup-parent.sh --memory-reserve-bytes 2147483648` inside the cockpit | Emits `dev-gates.slice` and reports RAM admission OK. |
| `findmnt --target <checkout> --noheadings --output TARGET,FSROOT` inside the cockpit | Target `<checkout>`; absolute host FSROOT other than `/`. |
| `docker inspect` cockpit environment | Six `CGPROFILE_*` keys and `MDT_HOST_DOCKER_GID=<host docker gid>`. |
| `docker inspect` cockpit groups | Docker and control GIDs present; observer GID absent. |
| `docker image inspect postgres:18-alpine@sha256:d3e1620b530c944afa6e887d22eb899824da68e19c52024bf98f5220c88a65b2` | Exit 0. |
| Run-Gate 25.4.0 preflight through the private hosts-file pin | Host Docker GID, control group, observer directory/socket checks pass. |
| Observer lane smoke | Lane receives the observer directory mount and its numeric GID; assay reports observed cgroup measurements. |

The observed observer smoke produced the lane arguments
`--mount type=bind,source=/run/cgprofile/observer,target=/run/cgprofile/observer,readonly`,
`--group-add 985`, and
`-e ASSAY_RESOURCE_OBSERVER_SOCKET=/run/cgprofile/observer/cgprofile.sock`; Assay baseline passed and candidate
progress carried cgroup measurements from cgprofile. A later self-qualification attempt exercised the actual release
gate. A complete fresh-host replay using only checked-in files has not been performed and is `to be confirmed`.

After the host is idle and accepted, remove the per-host pause flag so the queue loop can relaunch.

### Rollback

Rollback in dependency order so an active cockpit does not retain the new mounts after their publisher is removed.
Pause the queue and drain before each cockpit recreation:

```bash
CONTROLLER_HOME=${CONTROLLER_HOME:-"$HOME/controller"}
[[ ! -f $CONTROLLER_HOME/controller.local.env ]] || . "$CONTROLLER_HOME/controller.local.env"
queue=${CONTROLLER_QUEUE:?set CONTROLLER_QUEUE in controller.local.env}
host=<host>
touch "$CONTROLLER_HOME/host-sat-queue.disabled.$host"
bash "$queue" status
# Wait until status reports no in-flight lane for "$host".
```

1. Replace the cgprofile-and-workspace cockpit with the pre-cgprofile no-host-setup shape:

   ```bash
   bash <home>/runner-scripts/start-cockpit-runner-nohostsetup.sh
   ```

2. Remove cgprofile and its groups. The recorded uninstall removes the installer's endpoint fragment, units, sysusers
   file, links, versioned tree, `/etc/cgprofile`, `/run/cgprofile`, and the two groups. It is data-preserving when
   `--purge-data` is omitted: it intentionally keeps `/var/lib/cgprofile`, and it does not remove the
   aggregate/profile files or the uploaded wheel directory. Do not use `--purge-data` during normal rollback.

   ```bash
   /opt/cgprofile/current/venv/bin/cgprofiled uninstall --yes --remove-groups
   ```

3. On the observed host, where cgprofile was the only publisher of the endpoint aggregate, remove the remaining
   installer artifacts by exact path:

   Warning: the first command deletes the `<home>` upload directory, including the wheels and
   install evidence kept there. It does not delete `/var/lib/cgprofile`.

   ```bash
   rm -rf <home>
   rm -f /etc/host-endpoints.env /etc/profile.d/host-endpoints.sh
   rmdir /etc/host-endpoints.d
   ```

   `/var/lib/cgprofile` is deliberately preserved. These aggregate cleanup commands apply only when cgprofile was the
   sole endpoint publisher. On a host with another publisher, do not remove the shared aggregate or profile loader;
   collect the live fragment and publisher inventory first. That host-specific cleanup remains open under NC-35.

4. Re-pin lanes to the prior Run-Gate. Remove the additive venv only after no host profile references it and the
   cockpit is running:

   ```bash
   docker exec -u vscode "$COCKPIT_NAME" bash -lc 'rm -rf -- "$HOME/.venvs/gate-tools2540"'
   ```

5. The rollback cockpit still uses `$INTERACTIVE_SLICE` for `--cgroup-parent`. Retain the two slice units while that
   cockpit is running. To remove them, stop and remove the cockpit first, confirm the slices have no processes or
   child cgroups, then:

   ```bash
   rm -f /etc/systemd/system/dev-interactive.slice \
         /etc/systemd/system/dev-gates.slice
   systemctl daemon-reload
   ```

6. Remove the pinned PostgreSQL image after all lanes that use it have finished:

   ```bash
   docker rmi 'postgres:18-alpine@sha256:d3e1620b530c944afa6e887d22eb899824da68e19c52024bf98f5220c88a65b2'
   docker image inspect 'postgres:18-alpine@sha256:d3e1620b530c944afa6e887d22eb899824da68e19c52024bf98f5220c88a65b2'
   ```

   The inspect command must return nonzero.

7. After rollback verification, remove this operation's per-host pause flag and confirm the queue can launch again:

   ```bash
   rm -f "$CONTROLLER_HOME/host-sat-queue.disabled.$host"
   bash "$queue" status
   ```

## Live memory admission on the host (remote-gate)

With no static ceiling, `remote-gate.py` decides at admission time: the host job reads `/proc/meminfo` and starts the
lane only when `MemAvailable - expected_peak >= floor`. `--mem-need-mb` is the lane's expected peak (the controller's
admission weight) and `--mem-floor-mb` (default 1024) is what must remain for the control plane; `--wait-mem`
(default 900 s) bounds the wait. **`--mem-need-mb 0` (the default) declares no peak, so the pre-existing behaviour is
unchanged: no ticket, no floor check, no wait, the lane starts immediately.** That matters because the controller
wrapper does not pass the flag yet; a floor nobody declared a peak for would queue every lane for memory it never
asked for. The expected peak is capped at `MemTotal - 2 x floor`, so **one** lane larger than RAM always runs alone
and pages instead of waiting forever, while a smaller lane still waits when the host is occupied. A host-wide ticket
(`~/rg/.mem-admit.lock`, held for a 20 s settle after the lane starts, so the lane's memory is already visible in
`MemAvailable`) keeps two callers from reading the same figure. Unreadable meminfo is *indeterminate*: the job refuses
rather than admitting, and the memory fact is the kernel's own `/proc/meminfo` with no environment override.

A refusal is exit 75 and verdict `MEM_REFUSED` (a memory admission refusal is never reported as `PSI_REFUSED`: that
would name a cause nobody observed). The verdict is decided from the job log's **refusal line** (`…: refusing to start`),
not from the phrase "memory admission" appearing anywhere: a lane that was admitted and then exited 75 for its own
reason is not a memory refusal either. Neither refusal started the lane, so nothing was cleaned up and no container
exists.

## Collecting a run's store directory (remote-gate `--collect-store`, RG-298)

A remote run's checkout is throwaway: the job removes it on exit, so a directory the lane filled inside it (for example an
Assay shard store under `<project>/.run-gate/assay-shards/...`) was lost. `--collect-store <rel>` (repeatable) packs that
directory **before** the checkout is removed:

```bash
remote-gate.py run v1001 <worktree> assay --detach --name r1 \
    --collect-store scripts/netcup/.run-gate/assay-shards --store-max-mib 512
remote-gate.py collect r1    # fetches assay-store.tgz + assay-store.tgz.sha256 into <out-dir>/r1/ and verifies the checksum
```

- `<rel>` is relative to the **repository root of the run's own host checkout** and normalised (`a//b/./c/` is `a/b/c`).
  An absolute path, `..`, the root itself, a character outside `A-Za-z0-9._@+=,:-` or a symlinked component is refused
  with exit 2 before anything is sent (the local worktree is checked, and the host job checks its own tree again and
  reports `STORE-COLLECT-FAILED <rel> symlink`). Each run has its own checkout, so concurrent runs never share a store.
- After the lane exits, per path: absent gives one `STORE-ABSENT <rel>` job-log line and no archive; more than
  `--store-max-mib` (default 512) **uncompressed tar bytes** gives `STORE-TOO-LARGE <rel> <bytes>` and nothing is packed;
  any packing failure gives `STORE-COLLECT-FAILED <rel> <reason>`; success logs `STORE-COLLECTED <rels>`. None of
  these changes the lane's verdict or the job's exit code. `collect` echoes these lines before the verdict line.
- Archive layout (one choice, for all paths): **one** `assay-store.tgz` per run whose members keep their `<rel>/` prefix
  (`tar -xzf assay-store.tgz -C <dir>` recreates `<dir>/<rel>/...`), plus `assay-store.tgz.sha256` in `sha256sum` format.
  A path that was absent, too large or refused is simply not in it. If the archive itself cannot be written, every path
  that would have gone into it gets its own `STORE-COLLECT-FAILED` line and no archive is left behind.
- `collect` verifies the fetched archive against the host's sha256. A mismatch prints `STORE-CHECKSUM-MISMATCH ...`,
  renames the file to `assay-store.tgz.rejected` (a corrupt archive never keeps its usable name) and makes `collect`
  exit 77 when the lane's own exit code was 0; a lane that failed keeps its own exit code. `result.json` and the
  final `lane ... verdict ...` line are never changed by a store problem.
- A run cancelled with `cancel` (TERM) is not collected: the job stops before the packing step. Never `/tmp`; the archive
  is written under the run's own `~/rg/<name>/` directory.

## Cached tool copies and concurrent launches (CTL-12)

`runner/remote-gate-cache.sh` is the repository-owned way for a controller helper to run this repository's
`remote-gate.py` from a cached copy of a ref. It exists because the previous controller-local helper refreshed
`<cache>.new` and `mv`-ed it into place: one shared staging name meant two launches in the same second collided (`mv:
cannot move ... .new`), and that failure was reported as "cannot read the tool from origin/main" — a cache write
failure published as a missing file, so the batch lost lanes and nobody could tell which revision ran.

```bash
# prints cache=, ref=, commit= and identity=blob:<id>, which a caller appends to its run record
scripts/netcup/runner/remote-gate-cache.sh origin/main
```

Its four guarantees are the acceptance contract: a **per-invocation staging file** (`.remote-gate-<blob>.stage.<pid>.<rand>`,
never a shared name), a **content-addressed publish** by one atomic `rename` to `remote-gate-<blob>.py` (an already
published copy is verified by `git hash-object`, so a truncated or foreign file is replaced instead of trusted by
name, and a reader never sees a half-written file), **one refresh at a time** (`flock` on `<cache-dir>/.refresh.lock`,
released by the kernel if the process dies, bounded by `REMOTE_GATE_CACHE_LOCK_WAIT`, default 60 s), and **two
refusals that cannot be confused**: `could not refresh the cache` (exit 3) versus `the tool is absent from ref <ref>`
(exit 4), plus its own refusal for a ref that does not resolve (exit 5). Each names the ref, the tool path and the
cache directory it tried. Two copies of different revisions stay addressable side by side, so a run record's
`identity=` still points at the bytes that ran after a later refresh.

## Host-sat queue pause flags

The host-satellite queue is checked in at `scripts/netcup/runner/host-sat-queue.sh`.
It is controller-side orchestration for serial Run-Gate lanes and uses the same
private host table as `remote-gate.py`. Copy
`scripts/controller/hosts.local.json.example` to
`$CONTROLLER_HOME/hosts.local.json`, then add `queue_lanes` arrays to the hosts
that should receive queue work. Each entry must be exactly one non-empty
`project|lane` pair. The `ssh`,
`api_check`, and `guard` values remain local because they contain addresses,
key paths, provider identifiers, or provider-specific commands. Every lane
launch and standalone `probe` command checks provider identity; the periodic
resource probe does not. Remote commands also run the configured on-host
guard. Host aliases must be accepted by the installed
`remote-gate.py`.

Pause flags live under `$CONTROLLER_HOME`, alongside controller logs:

| Flag | Scope | Effect |
| --- | --- | --- |
| `$CONTROLLER_HOME/host-sat-queue.disabled` | Global | Every queue loop idles and launches no new lane. |
| `$CONTROLLER_HOME/host-sat-queue.disabled.<host>` | One host | Only that host's queue loop idles and launches no new lane. |

When either flag exists, the loop logs
`disabled flag present: not launching` and rechecks every 60 seconds. The flag does not kill the loop and does not
cancel an in-flight remote run: the current lane finishes normally. `stop [host]` terminates only the queue loop;
an in-flight remote-gate job is left to finish.

The commands accept configured host aliases, or discover all configured queue
hosts for `start`, `stop`, and `status`:

```bash
CONTROLLER_HOME=${CONTROLLER_HOME:-"$HOME/controller"}
[[ ! -f $CONTROLLER_HOME/controller.local.env ]] || . "$CONTROLLER_HOME/controller.local.env"
queue=${CONTROLLER_QUEUE:-$CONTROLLER_HOME/bin/host-sat-queue.sh}
bash "$queue" status
bash "$queue" start
bash "$queue" pause runner-a
bash "$queue" resume runner-a
bash "$queue" stop runner-a
```

With no host argument, `pause` and `resume` set or clear the global flag. A
host-specific `resume` clears only that host's flag. A global resume clears
only the global flag, so it cannot accidentally remove another host's pause.

`status` reports queue pids, the current lane from `<host>.current`, pause
metadata, recent results when the TSV exists, and the last three log lines.
Queue state and results are in `$CONTROLLER_HOME/logs/host-sat-queue/` and
`$CONTROLLER_HOME/logs/host-sat-results.tsv`; remote-gate results are under
`$CONTROLLER_HOME/remote-gates/`.

After five consecutive `INFRA` verdicts the queue automatically pauses that
host and writes the reason, last lane, result, run, and log path into the flag.
If the metadata write fails, it leaves an empty pause flag and logs the write
failure. Every `INFRA` result also causes a 600-second back-off. Remove the
per-host flag only after diagnosing the failures.

Any release, cockpit recreation, or other host-exclusive operation must create
the per-host flag before it starts and let the current lane drain. For a queue
update, keep the flag in place until both `<host>.current` is absent and the
last queue log line contains `disabled flag present: not launching`; that line
proves the loop observed the pause. Then stop the loop and wait for its PID
file to disappear before replacing files. Never send TERM while a lane is in
flight: the TERM trap exits immediately and can leave a stale `.current`
marker. The full direct-predicate drain, atomic replacement, and bounded
startup-verification procedure is documented in
[`scripts/controller/README.md`](../../controller/README.md#installing-a-queue-update).

## Source trail

Reusable operating contracts are linked to their repository sources:

- Queue flags, drain behavior, back-off, and auto-pause: this guide's
  [Host-sat queue pause flags](#host-sat-queue-pause-flags) section and
  `host-sat-queue.sh`.
- Queue installation ordering and rollback-safe replacement:
  [`scripts/controller/README.md`](../../controller/README.md#installing-a-queue-update).
- Slice validation: `assay/tools/cgroup-parent.sh` and
  `scripts/netcup/runner/provision-runner.sh`.
- Workspace bind requirements: `assay/tools/self-qualification-container.sh`
  and `run-gate-project/CONSUMERS.md`.
- Run-Gate environment installation:
  `scripts/netcup/runner/setup-cockpit-run-gate.sh`.

Host execution logs, handoff notes, and machine-specific command captures are
scratch evidence. The reusable facts from them are stated here with links to
the maintained code and operating procedures.

The retained cut-over acceptance records are therefore:

- [host-cutover-EXEC-LOG.md](../docs/evidence/host-cutover-EXEC-LOG.md):
  wheel hashes, cgprofile dry-run/apply, endpoint record, service verification,
  group model, and cgprofile rollback.
- [host-cockpit-EXEC-LOG.md](../docs/evidence/host-cockpit-EXEC-LOG.md):
  cockpit additions, workspace-bind failure, Run-Gate 25.4.0, Docker GID,
  observer-smoke evidence, and cockpit rollback.
