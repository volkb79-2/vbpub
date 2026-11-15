<!-- Evidence scrub 2026-11-10: environment-specific host identities, addresses, controller paths, and key material were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->
<!-- origin: 2026-11-04; status: decided; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/CGPCTL-PLAN-2026-11-04.md -->
<!-- relation: cgpctl operator-accepted plan -->

# cgpctl: CLI, cli-extended adoption and native deployment, 2026-11-04

Read-only plan. Facts are from main `9c350d233`. Paths are relative to `scripts/cgroup-profiler/` unless prefixed.
Builds on `DESIGN-PLATFORM-OPTIONS-2026-11-04.md` §2 (`mdt-docker` group) and §4 (native service, option B).
Section 6 holds the ciu interview fact sheet. Section 5 holds the operator questions.

## 0. Starting point

**CLI.** A bash wrapper `cgprofile` (38 lines) dispatches to `cgprofile.py` (1762 lines, 17 argparse verbs).
- System python3 runs `_collect`, `mark`, `serve`, `ctl` and `--version`.
- Everything else runs on a pandas venv.
- The verbs are `run`, `attach`, `mark`, `report`, `targets`, `doctor`, `_collect` (hidden), `serve`, and `ctl version|host|status|start|stop|watch|report|gc`.
- Only `ctl` prints JSON, and it always does; its `--json` flag is a no-op kept for run-gate.
- Exit codes: `ctl` returns 0 ok, 2 refused and 3 daemon fault, with CP-31 cause tags (`socket-not-mounted`, `socket-missing`, `daemon-refusing`, `socket-permission`). `serve` returns 3 when the socket is lost.

**Daemon.** The container `cgprofile-host-daemon` runs privileged with `pid: host` and `network_mode: none` in `cgroup_parent: cgprofile.slice`, deployed by ciu with the image pinned to 1.1.0.
- It makes no docker calls. It uses cgroupfs, procfs, DAMON sysfs and `busctl`.
- It is configured by flags and env only: `--sessions`, `--socket`, `--interval`, `--damon-default`, `--keep-sessions`, `--keep-days`, `--max-sessions`, `--observe-slices`, `--gates-slice`, plus `CGPROFILE_ALLOW_UIDS`, `CGPROFILE_PROC_ROOT`, `CGPROFILE_PID_NAMESPACE_MODE` and `CGPROFILE_DAMON_LOCK_PATH`.
- There is no config file, no reload, and no SIGHUP handler.

**Sockets.**
- `ctl.sock`: contract 1, JSON lines. Ops: `version`, `host`, `status`, `start`, `stop`, `report`, `gc`, `watch`.
- `observer/resource-observer.sock`: contracts 2 and 3, read-only limits plus windows. Its clients are assay and run-gate, never the CLI.

**Docker use.** Only the standalone CLI paths use docker:
- target resolution with `docker inspect` and `docker ps`;
- the privileged helper container (`CGPROFILE_HELPER_IMAGE`), which exists because a devcontainer cannot see host cgroups;
- the placement probe in `tester-unified:local`, with a hard-coded `--user 1003:1003`;
- `docker logs -f` for `--log-tail`.

**Packaging.**
- `pyproject.toml` has distribution `cgroup-profiler`, `py-modules = []`, no scripts, and a literal version `0.1.0`.
- `cmru.toml` declares `artifacts = ["oci-image"]`.
- The DAMON library is loaded from the sibling `scripts/damon-analysis` by relative path (`lib/damon.py:45-48`).
- `ctl report` spawns `<project>/venv/bin/python cgprofile.py report` (`lib/serve.py:111-114`).

**Consumers of the CLI name.**
- run-gate once used the daemon container's historical control subcommand as its fallback carrier (`run_gate.py:2994,3140`); the accepted plan below replaces that unregistered path with the socket-backed client.
  - The default transport `auto` probes `ctl.sock` first (`:3018-3032`).
  - `tests/test_run_gate.py` has 174 cgprofile hits.
- assay uses only the observer socket. mdt-cockpit host-setup owns `/run/cgprofile` (tmpfiles), the `cgprofile-observer` group and `check.sh`; it never calls the CLI.
- The documentation that names the CLI is `RESOURCE-PROFILES.md`, `BOOTSTRAP-FROM-ZERO.md`, the netcup backlog and RUNBOOK, debian-install-v2 `USAGE-TESTING.md`, and the project's own README, CONSUMERS, PROTOCOL, ATTACH-GUIDE and RG55 contract.

## 1. CLI design

### 1.1 Names and entry points

| Item | Recommendation | Why |
|---|---|---|
| Product, project id, release prefix, image | stay `cgprofile` / `cgprofile-v` | Tag continuity; the cockpit resolver matches `<name>-latest` |
| Wheel distribution | rename `cgroup-profiler` to `cgprofile` | Closes the CP-41 name mismatch with no mapping table |
| User-facing client | `cgpctl` console script | Operator direction |
| Daemon | separate console script `cgpd` | See below |
| Python package | `cgprofile` (`src/cgprofile/`, absorbs `lib/` and `cgprofile.py`) | A wheel needs a real package; `lib` is not a valid top-level name |
| Env prefix | keep `CGPROFILE_` | It names the product, not the command. Most daemon vars disappear anyway (§3) |

**Why a separate `cgpd` and not `cgpctl serve`:**
- The daemon is root-only, runs from a systemd unit and has its own grammar (config path, unit rendering).
- Inside the client it would be one more privileged verb in an unprivileged user's `--help`.
- The surface review would judge two audiences in one catalog.
- Two scripts in one wheel cost nothing.
- The systemctl/systemd split is the precedent operators expect.

`cgpd` also adopts cli-extended, with a deliberately tiny surface:
- `cgpd run`: the default and the unit's ExecStart.
- `cgpd unit print|check`: CP-42's renderer, with the unit template shipped inside the wheel.
- `cgpd config check [--file]`.

### 1.2 Use cases

| Who | Need | Path today | Path after |
|---|---|---|---|
| run-gate lanes | session start, stop, watch, report | `ctl.sock`, or the exec carrier | `ctl.sock` only; exec carrier removed (RG-next) |
| assay and run-gate lanes | resource limits, windows | observer socket | unchanged (no CLI) |
| operator or agent | is the daemon healthy, which version | `docker exec … ctl version/status` | `cgpctl status`, `cgpctl doctor` |
| operator or agent | host facts (gates slice limits, observed slices, DAMON state, PSI) | `ctl host` | `cgpctl host` |
| operator or agent | profile a running container or cgroup ad hoc | `attach`, or helper mode in a devcontainer | `cgpctl start --target container:NAME\|cgroup:PATH`, then `cgpctl stop` |
| operator or agent | follow a session | `ctl watch` | `cgpctl watch ID` |
| operator or agent | list, show and prune stored sessions | `ctl status`, `ctl gc` | `cgpctl sessions`, `cgpctl show ID`, `cgpctl prune` |
| operator or agent | render a report from a stored session | `ctl report`, daemon-side with pandas | `cgpctl report ID`, client-side (§1.4) |
| developer | profile one command locally | `cgprofile run -- cmd`, `mark` | `cgpctl run -- cmd`, `cgpctl mark` (direct mode only) |
| operator | change retention, default interval, DAMON default, max sessions | daemon restart | edit `/etc/cgprofile/cgpd.toml`, then `systemctl reload cgpd` (§1.5) |
| operator | change access control (allow-uids) | env plus restart | same file plus reload; never over the socket |
| agent harness | skill | checkout-only `.claude/skills/cgprofile` | `cgpctl skills install`, via the finalize step |

### 1.3 Minimum good surface for `cgpctl`

All read verbs offer `--json` through cli-extended's runtime. Exit codes:
- 0 ok;
- 1 failure, plus the library's usage and Ctrl-C codes;
- 3 daemon unreachable, with the CP-31 cause tag in the `[ERROR]` line and in the JSON.

The 0/2/3 `ctl` table is wire contract for run-gate's exec carrier only. Once that carrier is gone (RG-next), cgpctl follows the library's table and keeps 3 as a tool-specific code. pwmcp's exit 3 is the precedent.

| Verb | Socket op | Mutating | Notes |
|---|---|---|---|
| `status` | `version`, `status` | no | Daemon and client versions, contracts, uptime, slots in use, settings in effect, config file digest |
| `host` | `host` | no | Fixes CP-36 by reading the observed set from config |
| `sessions [--state live\|done\|all] [--mine]` | `status` | no | One line per session: owner uid, target, age, state |
| `show ID` | `status ID` | no | The summary of a finished session, or live counters |
| `start --target … [--interval --damon --memory-high … --on-stall …]` | `start` | yes (`--yes` not required) | Client resolves `container:NAME` to `containerid:<64hex>` with `docker inspect` when docker is reachable; otherwise only `containerid:`, `cgroup:` and `slice:` |
| `stop ID` | `stop` | yes | Own sessions only, unless the peer is uid 0 (§1.6) |
| `watch ID [--interval]` | `watch` | no | Streams; `--json` gives JSON lines |
| `report ID [--out DIR] [--html-only\|--md-only]` | `export` (new) | no | Client-side render; needs the `report` extra, and `doctor` says so when it is missing |
| `report --run-dir DIR` | none | no | Renders a standalone `run` result |
| `prune [--keep N] [--keep-days D]` | `gc` | yes (`--dry-run`, `--yes`) | `--dry-run` lists what would go |
| `run [--phase] -- CMD` | none | no | Standalone, direct mode only (§1.7) |
| `mark NAME [--kind --meta]` | none | no | Stays stdlib-only so it runs inside wrapped commands |
| `doctor [--check NAME]` | `version` | no | `register_doctor` checks are listed after this table |
| `config show` | `status` | no | Effective settings and their source (default, file) |
| `skills install\|uninstall\|check\|list` | none | | Comes from `register_skills_verbs` |

The `doctor` checks are:
- socket present and connectable, with the CP-31 cause tag and a remedy per tag;
- caller in the socket's group, derived by `stat`;
- client/daemon version skew;
- ctl contract and observer contract set;
- observer directory mode 0751 and group;
- `cgprofile.slice` bounded;
- DAMON available and lock free;
- sessions directory writable by the daemon (reported, not tested from the client);
- `report` extra installed.

**Dropped:**
- `attach`: use `start` and `stop`, or `run`.
- `targets`: folded into `start --dry-run`, which resolves and prints the target and its limits.
- `_collect` and helper mode, the placement probe, and `--log-tail`/`--log-match` (`docker logs`). These are what make the CLI need docker and privileged containers. A host daemon reachable through `ctl.sock` makes them redundant (decision Q5).

**Not added:**
- `config set` over the socket (§1.5).
- Shell completion. cli-extended does not offer it; file it as CLI-EXT-next rather than hand-rolling it per tool.

### 1.4 Reports leave the daemon

Today `ctl report` runs pandas, plotly and matplotlib as a daemon subprocess. The cgprofile delta review (M2) notes it shares the 16-slot handler pool.

Recommendation:
- The daemon gains an `export ID` op that streams the session's `summary.json`, `samples.jsonl.gz`, `damon.jsonl` and `events.jsonl`. It is size-capped and owner-checked.
- `cgpctl report` renders locally.
- Report CPU and memory are then charged to the caller's cgroup, not to the protected `cgprofile.slice`.
- The daemon and its venv stay stdlib-only, so `/opt/cgprofile` holds no pandas.
- The `report` daemon op is removed. Contract 1 becomes **ctl contract 2**, with additive ops `export` and `config` and the `report` op removed. run-gate's `_wire_args` drops `report` and calls `cgpctl report` client-side, or skips the report.

### 1.5 Runtime settings: a file plus reload, not socket writes

Location: `/etc/cgprofile/cgpd.toml`.
- Root-owned, mode 0644.
- Rendered by mdt host-setup from `host-setup.env`, which stays the single operator source on mdt hosts.
- Hand-written anywhere else, so cgprofile works off-mdt.
- `cgpd run --config PATH` overrides the location.
- No env fallbacks: an unknown key or a stale `CGPROFILE_*` daemon env var is refused, naming the key that replaces it.

**Keys:**

| Group | Keys | On change |
|---|---|---|
| Fixed | `sessions_dir`, `ctl_socket`, `observer_socket`, `damon_lock` | restart |
| Reloadable | `interval`, `damon_default`, `keep_sessions`, `keep_days`, `max_sessions`, `observe_slices`, `gates_slice`, `allow_uids` | SIGHUP, through `ExecReload`, applies them atomically |

- A reload never touches live sessions.
- A reload with an invalid file is refused, and the old settings stay in force.

A socket `config set` op is rejected for three reasons:
- It would drift from host-setup's declared state.
- It would let the socket change its own access list.
- It needs persistence the daemon does not otherwise have.

`cgpctl config show` is read-only.

### 1.6 Access control

| Caller | Can do |
|---|---|
| Any user, no group | `cgpctl --version`, `--help`, `run` (direct mode, own cgroup subtree), `mark`, `report --run-dir`. `doctor` reports `socket-permission` with the remedy. |
| Member of the `ctl.sock` group (`mdt-docker`, gid 2751 per CP-43; `docker` until then), listed in `allow_uids` when set | everything in §1.3 |
| `cgprofile-observer` | observer socket only: limits and windows. No CLI path, by design (CP-45). |
| uid 0 | as the group, plus stopping or exporting any session |

Two new daemon-side rules:
- **Session ownership.** `start` records `SO_PEERCRED` uid. `stop`, `export` and `watch --kill` from another uid are refused unless the peer is uid 0.
- **Recorded privilege.** `on_stall=kill` stays limited to exact gates-slice containers, which is the CP-33-era rule. The ctl group is root-equivalent on mdt hosts because it is the docker relay group, so this ownership rule is hygiene, not a security boundary. Docs must say so plainly.

### 1.7 Standalone `run` without docker

- `run` keeps direct mode, which profiles the wrapped command's own cgroup subtree where cgroupfs is visible.
- In a devcontainer the honest path is a daemon session. run-gate already does this.
- `run` inside a container without host visibility fails with one line that names `cgpctl start` as the remedy.

## 2. cli-extended adoption

CP-17 is the existing entry, fourth in the CX-D11 order. Adopt for both `cgpctl` and `cgpd`. Both are stdlib-only with Python ≥ 3.11, which suits the daemon venv.

**What it takes:**
- Re-register the grammar with `CliRegistry`, `VerbSpec`, `ArgumentSpec` and `OptionSpec`, and delete `CgprofileArgumentParser`.
- `CliIdentity.resolve` replaces `cgprofile.py:44-52`, `lib/version.py` and the `0.0.0-dev` literal in `lib/serve.py:75` (AC-01). VERSION comes from scm in the wheel.
- `unexpected_exceptions="report"` (AC-10), and domain failures become `CliFailure` (AC-11).
- `register_doctor`: the current `cmd_doctor` content moves into checks (AC-20).
- `register_skills_verbs(registry, package="cgprofile")`. Move `.claude/skills/cgprofile/SKILL.md` to `src/cgprofile/skills/cgpctl/SKILL.md` and rewrite it for the new surface (AC-19).
- Surface files: `[tool.cli-extended]` with two `clis` entries (`cgpctl`, `cgpd`), plus `docs/cli-review.toml`, `docs/cli-surface.json` and `docs/CLI-SPEC.md`. Run `surface check` as a pytest test (AC-16/17).
- Tests: `assert_cli_contract`, `invoke_script`, `-p cli_extended.pytest_plugin`, and `cli-extended audit --json` with no `fail` (AC-21/22/23).
- Dependency `cli-extended>=0.5.1` (CLI-EXT-30 collision filter), with the reason in a comment (AC-24). No PYTHONPATH tricks (AC-25).

**CP-17's open question goes away.**
- CP-17 asked how cli-extended reaches the bare `python3` used by the collector and helper.
- With helper mode, `_collect` and the probe removed, nothing runs outside the venv.
- The exception is `mark`, which is invoked by wrapped commands. It runs through the `cgpctl` console script, whose shebang is the venv interpreter, so it inherits the dependency.
- Oracle: `mark` imports nothing outside the stdlib and cli-extended (a test pins this).

**Cost estimate.**
- pwmcp is the measured analogue: +658/−68 for the registry, doctor and skills, plus about +3.1k of generated and reviewed surface files.
- cgprofile has twice the verbs and two CLIs. Estimate two packages of 1-2 agent days each, plus one surface-review round.

**What breaks for consumers.** No aliases: `cgprofile` disappears outright.

| Consumer | Change | Owner |
|---|---|---|
| run-gate exec carrier (`run_gate.py:2994,3140`), `_wire_args` `report`, transport `exec`/`auto`, `RUN_GATE_PROFILE_TRANSPORT`, about 174 test hits, RG55 contract mirror | remove the exec carrier and the transport setting (socket only); drop the `report` op; refuse the env var naming its removal | RG-next |
| `ciu.compose.yml.j2`, Dockerfile symlink, `docker-bake.hcl` | deleted with the ciu deployment (CP-44) | CP-44 |
| mdt-cockpit `pip/wheels.list` comment, `FIRST_PARTY_WHEEL_NAMES`, `FIRST_PARTY_NO_AGE_WINDOW`, Dockerfile manifest, README/CONSUMERS tables, `SKILLS_CLIS` (`finalize_container_environment.py:260`) | add `cgprofile` / `cgpctl` | MDT-048 |
| mdt-cockpit host-setup `install.sh`, `check.sh`, `test-render.sh`, README "out of scope" statement | native install (§3) | MDT-055 |
| docs: `RESOURCE-PROFILES.md`, `BOOTSTRAP-FROM-ZERO.md`, `LANE-MATRIX-PROGRESS.md`, netcup RUNBOOK and backlog, debian-install-v2 `USAGE-TESTING.md`, cgprofile README, CONSUMERS, PROTOCOL, ATTACH-GUIDE, DESIGN-GUIDE | sweep, plus the CP-45 docs-sync test | docs package |
| cmru `run-gate.toml:66,219`, `test_bootstrap_from_zero_sequence.py` | update the names in the bootstrap sequence | cmru |
| assay | none; observer paths unchanged | — |

## 3. Native deployment: what is missing

| Gap | Today | Needed | Backlog |
|---|---|---|---|
| Wheel | none; `py-modules = []` | `src/cgprofile/` package, distribution `cgprofile`, scripts `cgpctl` and `cgpd`, `report` extra, `wheel` artifact with sha256 sidecar plus `latest.json`, and a clean-venv `pip install --no-index` lane | CP-41, MDT-048 |
| DAMON library | sibling `scripts/damon-analysis` loaded by relative path | vendor it as `cgprofile._damon` with a sync test against the source, or make damon-analysis its own wheel (Q7) | CP-41 |
| Report subprocess | `<project>/venv/bin/python` | removed (§1.4) | CP-next |
| Container-shaped checks | `cmd_serve` requires `CGPROFILE_PID_NAMESPACE_MODE=host` and `CGPROFILE_PROC_ROOT=/hostproc` | native default `/proc`; the pid-namespace inode check stays and passes trivially; both env vars removed, and a stale one is refused | CP-next |
| Config | flags and env | `/etc/cgprofile/cgpd.toml`, `--config`, SIGHUP reload (§1.5) | CP-next |
| Unit | none; `infra/cgprofile.slice` is installed by hand | `cgpd unit print\|check` renders `cgprofile.slice` plus `cgpd.service` with the CP-42 limits (below) | CP-42 |
| Group | `/run/cgprofile` is `root:docker` | `root:mdt-docker`. The daemon adopts the directory's gid with no code change; clients derive by `stat`. Tests for gids 2751, 999 and 0 | CP-43, platform memo §2 |
| Install, upgrade, rollback | `ciu up` from a checkout with hand edits | host-setup `CGPROFILE_ENABLE`, plus `CGPROFILE_WHEEL` as version plus sha256 (refused when empty while enabled), installed into `/opt/cgprofile/<ver>/` with a `current` symlink | MDT-055 |
| Session drain on upgrade | a restart aborts live sessions (`daemon-stopped`) | host-setup restarts only on drift, and first waits up to `CGPROFILE_DRAIN_TIMEOUT` (default 10m) for `cgpctl sessions --state live` to be empty; otherwise it refuses unless `--force-restart` (Q8) | MDT-055 |
| Single-daemon guard | CP-34 covers a stolen socket | `cgpd` refuses if `ctl.sock` answers. This also guards the container-to-native cutover | CP-44 |
| Doctor and check | `check.sh:391-420` checks the directory and group only | `check.sh` runs `cgpctl doctor --json` as root and asserts the pinned version | MDT-055 |
| Tests | 2241 tests and 100% coverage in a container layout | golden unit, `systemd-analyze verify` on Debian 13, fake-root install tests in host-setup, the CP-42 measured RSS/threads table, r3 canary against a native daemon | CP-42, MDT-055 |

CP-42 unit limits, carried over:

| Unit | Settings |
|---|---|
| `cgprofile.slice` | MemoryMin 128M, MemoryHigh 768M, MemoryMax 1G, CPUWeight 100, IOWeight 50 |
| `cgpd.service` memory and CPU | MemoryHigh 512M, MemoryMax 768M, MemorySwapMax 0, CPUQuota 50%, TasksMax 256, LimitNOFILE 4096 |
| `cgpd.service` hardening | `PrivateNetwork`, `ProtectHome`, `ProtectSystem=strict` with `ReadWritePaths` for `/run/cgprofile`, `/var/lib/cgprofile` and `/sys/fs/cgroup`; capability set shrunk by test |
| Restart | `Restart=on-failure` with backoff |
| State | `StateDirectory=cgprofile`; no `RuntimeDirectory`, because tmpfiles owns `/run/cgprofile` |

**Does a native daemon still need docker? No.**
- The daemon never used it.
- The helper and probe images disappear with standalone helper mode (Q5).
- The only remaining docker use is optional client-side name resolution in `cgpctl start --target container:NAME`.
- `CGPROFILE_HELPER_IMAGE` and `CGPROFILE_PLACEMENT_PROBE_IMAGE` are removed. So is the hard-coded uid 1003 in the probe.

**What depends on the container today:**
- the run-gate exec carrier: removed;
- the ciu stack and its 1.1.0 pin: retired by CP-44 after both hosts migrate;
- the `cgprofile-sessions` named volume: copy its sessions into `/var/lib/cgprofile/sessions` once per host, or drop them, since retention is 14 days anyway (Q9).

**Unchanged, so no consumer change:** the socket bind mounts for run-gate lanes, assay and cockpits. The paths `/run/cgprofile/ctl.sock` and `/run/cgprofile/observer/resource-observer.sock` stay.

**Getting `cgpctl` into the mdt-cockpit image:**
1. cgprofile 2.0.0 publishes the wheel and `latest.json`.
2. MDT-048 adds `cgprofile` to `pip/wheels.list` and the first-party sets (no age window). cli-extended is already in the set.
3. Add `cgpctl` to `SKILLS_CLIS`.
4. Finalize then runs `cgpctl skills check` and `install`.
5. The image carries only the core. The `report` extra needs pandas and friends in the image wheel set, so it is Q6: core-only image (recommended; render reports on demand in a venv) or full.

## 4. Implementation plan

### 4.1 Semver

- **cgprofile 1.2.0 now:** the delta fixes, released with `--set-version 1.2.0`, the image and container as today.
- **2.0.0** carries the breaking set in one release: the package and wheel, `cgpctl`/`cgpd`, removal of `cgprofile`, ctl contract 2 (with `report` removed), the config file, the native unit, and removal of the standalone docker paths.
- No 1.3.0. Shipping the wheel under the old command first would make every consumer change twice.
- Commits carry `!` or a `BREAKING CHANGE` footer, so the conventional bump computes 2.0.0. The tls-edge delta review showed what happens when it doesn't.

### 4.2 Packages

The integration branch is `cgp2-20261104`. Every package branches from it and merges back after its own review. The last step is a cmru release from origin/main after the integration merge.

| # | Branch | Content | Depends on | Parallel with |
|---|---|---|---|---|
| 0 | `cgp2-0-contract-20261104` | Docs only: PROTOCOL.md ctl contract 2 (`export`, `config`, session ownership, `report` removed), `CLI-SPEC.md` draft of the §1.3 surface, `cgpd.toml` schema, decisions recorded | operator answers Q1-Q9 | — |
| 1 | `cgp2-1-package-20261104` | Move `lib/` and `cgprofile.py` to `src/cgprofile/`; pyproject (distribution `cgprofile`, scripts, extras, scm version, package data); vendor DAMON; `wheel` artifact in `cmru.toml`; clean-venv install lane; CP-41 metadata test. Mechanical, but touches every import | 0 | — |
| 2 | `cgp2-2-cgpctl-20261104` | cli-extended registry for `cgpctl`; §1.3 verbs; client-side report from `export`; doctor; skills; surface files and tests; delete the wrapper, helper, probe, `attach`, `targets` and `logtail` | 1 | 3 |
| 3 | `cgp2-3-cgpd-20261104` | `cgpd` registry; config loader plus SIGHUP reload; native checks; `export` and `config` ops; session ownership; `report` op removed; `cgpd unit print\|check` with the template in package data; golden unit; `systemd-analyze verify` lane | 1 | 2 |
| 4 | `rg-cgp-socket-20261104` | run-gate: remove the exec carrier, transport and env var; drop the `report` op; tests and RG55 mirror. Works against a 1.x daemon already, so it can ship in any run-gate release | 0 | 1-3 |
| 5 | `cgp2-5-docs-20261104` | Estate docs sweep; CP-45 README overview plus docs-sync test; cmru bootstrap names | 2, 3 | — |
| 6 | release | cgprofile 2.0.0 (r0-r1, r3 rewritten for native, the installed-wheel lane; R2 waived per CP-30, recorded) | 2, 3, 5 | — |
| 7 | `mdt-cgp-native-20261104` | host-setup MDT-055 (env keys, `/opt/cgprofile`, config and unit render, drain, `check.sh`); MDT-048 (`wheels.list`, first-party sets, `SKILLS_CLIS`); CP-43 regroup if platform decision 2 is taken | 6 | — |
| 8 | ops | NC-05 on <host>: `ciu down`, host-setup, verify sockets, groups and version, one run-gate lane plus one `cgpctl` session, delete the hand edits. Then the shared host in an idle window. Rollback is `ciu up --profile host` at the 1.2.0 image | 7, mdt-cockpit release | — |
| 9 | `cgp2-9-retire-ciu-20261104` | CP-44: delete the compose, ciu files, image build, `infra/cgprofile.slice` and the CONSUMERS chapter, plus a grep test pinning the deletions. Release as 2.0.1, or 2.1.0 if anything is added | 8 on both hosts | — |

**Safety for implementers:**
- No package starts or stops the live `cgprofile-host-daemon`.
- Native tests run against a scratch socket directory or in vm-system.
- Package 8 is the only one that touches a host, and it follows the host load rule.

## 5. Interview questions (minimal)

| # | Question | Recommendation |
|---|---|---|
| Q1 | Daemon entry: separate `cgpd`, or `cgpctl serve`? | `cgpd` |
| Q2 | Keep `cgprofile` as product, release prefix and wheel distribution, with commands `cgpctl` and `cgpd`? Or rename everything to `cgpctl`, with a tag-prefix change? | keep `cgprofile` |
| Q3 | Reports rendered client-side from an `export` op (daemon stdlib-only), or daemon-side with pandas in `/opt/cgprofile`? | client-side |
| Q4 | Runtime settings by config file plus `systemctl reload`, with no socket writes? | yes |
| Q5 | Drop the standalone docker paths (helper container, placement probe, `attach`, `targets`, `--log-tail`)? | yes |
| Q6 | Cockpit image carries the core wheel only, or also the `report` extra (pandas, plotly, matplotlib, scipy, ruptures)? | core only |
| Q7 | DAMON library: vendor it into the wheel with a sync test, or make damon-analysis its own released wheel? | vendor |
| Q8 | Upgrade drain: wait up to 10 min for live sessions, then refuse unless forced? | yes |
| Q9 | Container-to-native cutover: copy the stored sessions from the named volume, or drop them? | drop; retention is 14 days |
| Q10 | Session ownership: `stop`/`export` of another uid's session is root-only? | yes |
| Q11 | Semver: 1.2.0 now, 2.0.0 for the whole breaking set, no 1.3.0? | yes |

## 6. Second request: ciu on cli-extended (fact sheet for the interview)

**Timing.** Start after ciu 7.20.0 has shipped and been quiet for one cycle. Branch `feat-ciu-clix-20261104`, carving CIU-114, whose line numbers are stale (`cli.py` is now 3036 lines).

### 6.1 Facts

**CLI construction.**
- `ciu/src/ciu/cli.py:2465` `main()` is a hand-rolled `if verb ==` chain.
- Each verb has its own `CiuArgumentParser` (`cli_utils.py:37`). `up` and `graph` forward to `deploy.py:5683`. Help is the hand-written `_USAGE` and `_VERB_HELP`.
- Exit codes are 0/1/2/3 (SPEC S10.3).

**Verbs, with external reference counts.** Counts are vbpub-outside-ciu / dstdns, by text-pattern lines (a lower bound).

| Usage | Verbs |
|---|---|
| Heavy | `up` 421/1113, `clean` 18/255, `env generate` 41/103, `worktree rm` 8/134, `worktree create` 22/107, `render` 35/208, `resolve` 12/98, `exec` 4/87, `check` 27/81, `env print` 3/68, `down` 40/66, `provenance` 57/4 |
| Light | `bake` 8/23, `host enroll` 4/29, `ssh` 2/35, `profiles` 0/35, `worktree up` 0/28, `worktree inspect` 14/14, `worktree adopt` 14/6, `worktree ensure` 0/10, `graph` 0/11, `health` 0/6, `status` 0/4, `diagnose` 1/4, `dev` 5/4, `secrets` 2/– |
| Zero outside tests and docs | `layouts`, `iops-baseline`, `migration-check`, `ksm build`, `host-secrets`, `capabilities`, `init`, `worktree lease`, `worktree refork`, `worktree branches`, `worktree reap`, `worktree exec` (1) |

**`worktree add`.** It is no longer a verb (removed in `d1eb98770`), yet dstdns still has 143 lines in 91 files and vbpub has 5. Verify whether dstdns runs an older ciu or is broken. Sweep regardless.

**Flags.**
- `--json` exists on `diagnose`, `status`, `check`, `graph` (via `--format`), `migration-check`, `capabilities`, `provenance`, `resolve` (where it is required) and `worktree *`. It is missing on `up`, `down`, `clean`, `health`, `render`, `profiles`, `layouts`, `env` and `host`.
- `--yes` and `--dry-run` are hand-declared per verb. There is no confirmation on an owner's `down` or `clean` (CIU-105).
- `env generate` and `up --dry-run` have side effects (CIU-162, CIU-103).
- `profiles` writes (CIU-109).
- `--root-folder` is parsed per verb.
- `--host` and `--dir` use hand-written abbreviation routing (S10.4).

**cli-extended features ciu does not use.** That is all of them: `CliIdentity` (ciu has two version readers with literal fallbacks), `CliRegistry` and derived synopsis, constraints, `SelectorList`, `unexpected_exceptions="report"`/`--traceback` (CIU-140 is a traceback leak), `CliFailure`, library `--yes`/`--dry-run`/`runtime.confirm`, `runtime.output` and `--json`, colour and progress, `--quiet`/`--verbose`/`--log-level`, the exact-spelling default, surface export/review/check, `register_skills_verbs`, `register_doctor`, `assert_cli_contract`, the pytest plugin, `invoke_script`, `fallback` verbs, and the audit.
- ciu's skills are a `.claude/skills/{ciu-cli,ciu-stack}` source tree. It has no `doctor` verb.
- ciu has no cli-extended dependency. `get.py` installs cli-extended first anyway.
- `assay.toml:42` puts the library source on PYTHONPATH.
- `ciu/pyproject.toml:46-50` maps `worktree` via package-dir, the pattern AC-25 forbids for cli-extended. `worktree` itself is not covered by AC-25, but it is the same smell.

**v8 (`ciu/docs/SPEC-V8.md` draft.12, "ready for sign-off", implementation barely started in `ciu8/`).**
- S18 verb groups: `instance`, `lease`, `query`, `gate`, `footprint`, `host admission`, `governance`, `migrate`, `skills`, `doctor`, `schema`, `hook run`, `show`.
- Merges:
  - `status` absorbs `health`;
  - `build` replaces `bake`;
  - `secrets` absorbs `host-secrets`;
  - `show` absorbs `profiles` and `layouts`;
  - `governance` absorbs `ksm` and `iops-baseline`;
  - `migrate` absorbs `migration-check`;
  - `schema` absorbs `capabilities`;
  - `instance` replaces `env generate` and `worktree *`.
- Globals: `--root` (replacing `--root-folder`), `--layout`, `--host`, `--realization`, `--json`. Exit codes add 4 for contention and 5 for remote failure, with a JSON envelope.

**run-gate in v8.**
- It becomes `ciu gate [<lane>…] | history | footprint | doctor | exec`, with lanes in `ciu.toml [testing.lanes]`. run-gate is archived at 8.0.0, and `ciu migrate --gate` converts per repo.
- The gate exit table (PASS 0, FAIL 1, ERROR 2, NOT_RUN 3, BUDGET 4) collides with the general table (3 environment, 4 contention). That collision has to be settled before v7 adopts any v8 name.

### 6.2 Candidate surface changes

| # | Change | Pros | Cons | Blast (vbpub/dstdns) |
|---|---|---|---|---|
| C1 | Full `CliRegistry` adoption | Deletes the dispatch chain and hand help; library `--yes`/`--dry-run`/`--json`/`--traceback`; audit-enforceable | Every help and error text changes; 22 test files shell out; the `deploy.py` forwarding is the hard part | indirect, tests |
| C2 | `--json` on every read verb, v8 envelope | Agents parse instead of scraping | `resolve` schema is versioned; `graph --format json` is a duplicate | additive |
| C3 | Global `--root` (rename from `--root-folder`), `--host`, `--layout`, exact spellings | Kills S10.4 routing; equals v8 | Renames a flag every script uses | `--root-folder` not counted; check first |
| C4 | `doctor` plus `skills` verbs | Free, and already in v8 | — | none |
| C5 | `status` absorbs `health` | v8 parity; two near-duplicates | `health --preflight` semantics need a home (`doctor`?) | 0/10 |
| C6 | `show profiles\|layouts` | v8 parity; fixes CIU-109 in passing | — | 0/35 |
| C7 | `governance ksm\|iops-baseline`, `schema` for `capabilities`, `migrate --check` for `migration-check` | v8 parity; all zero external use | v8 still draft | ≈0 |
| C8 | `secrets host …` for `host-secrets` | v8 parity; zero external use | — | 0/0 |
| C9 | `build` for `bake` | v8 parity | "build" is overloaded with cmru | 8/23 |
| C10 | `instance …` for `env generate` and `worktree …` | v8 parity; one lifecycle noun | Largest blast; dstdns dominates | about 75/400+ |
| C11 | Remove `env` with no subverb (already deprecated) | Less surface | — | small |
| C12 | `runtime.confirm` on `down` and `clean` of flagged instances; `--dry-run` with no side effects | Closes CIU-105, CIU-162, CIU-103 | A new prompt in scripts needs `--yes` | `clean` 18/255 |
| C13 | `ciu run` one-shot (CIU-141) | Asked for | Not in v8 S18; collides conceptually with `gate` lanes | additive |
| C14 | Leave the `gate` group name unused in v7 | Keeps v8's run-gate merge clean | — | none |

### 6.3 Interview questions for ciu

| # | Question | Recommendation |
|---|---|---|
| CQ1 | Adopt cli-extended in v7 (7.x minor) or only in v8? | v7, choosing v8 S18 names wherever a name changes, so v8 becomes additive |
| CQ2 | Which renames now? | C4-C8 and C11-C12 (near-zero blast, v8 parity) now. C3, C9 and C10 wait for v8. C1 and C2 are the adoption itself |
| CQ3 | Exit codes: adopt the library table plus ciu's 3, and settle the v8 gate collision now (gate buckets inside the JSON, process exit 0/1/2)? | yes |
| CQ4 | dstdns `worktree add` callers: sweep them (no alias, per the rule)? | yes; verify dstdns's ciu version first |
| CQ5 | Is `ciu run` (CIU-141) in scope? | defer to v8, as `gate exec`/`run` |
| CQ6 | Is v8's sign-off still pending? | yes: SPEC-V8 says "ready for sign-off" and the reconciliation flags S18 as predating cli-extended. One sign-off decides CQ1 and CQ2 |
