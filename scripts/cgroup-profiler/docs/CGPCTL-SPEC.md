# cgpctl and cgpd: the 2.0 command-line contract

**Status.** Specification for cgprofile 2.0.0. Nothing here is implemented in 1.x. Package 1 (wheel
layout), package 2 (`cgpctl`) and package 3 (`cgpd`) implement it, the run-gate socket-only change
consumes it, and [`PROTOCOL.md`](PROTOCOL.md) section 10 carries the wire half (ctl contract 2).
[`NATIVE-DEPLOYMENT.md`](NATIVE-DEPLOYMENT.md) carries packaging, the unit and the install path.
The generated `docs/CLI-SPEC.md`, `docs/cli-surface.json` and `docs/cli-review.toml` that cli-extended
derives from the registries replace the verb tables here as the mechanical authority once package 2 and
3 land; this file stays the prose authority for behaviour.

**Names.** Product, release tag prefix (`cgprofile-v`), wheel distribution and Python package are
`cgprofile`. The client console script is `cgpctl`; the daemon console script is `cgpd`. There is no
`cgprofile` command and no alias. Environment variables keep the `CGPROFILE_` prefix, except the one
client socket override, which is `CGPCTL_SOCKET`.

**Conventions.** Both CLIs are built on cli-extended (`CliRegistry`, `CliIdentity`, `register_doctor`,
`register_skills_verbs`) and inherit its global options (`--help`, `--version`, `--json` on read verbs,
`--quiet`, `--verbose`, `--log-level`, `--no-color`, `--traceback`, and `--yes` / `--dry-run` on the verbs
that declare them). Exact option spellings only, no abbreviation routing. Unexpected exceptions are
reported in one line with `--traceback` for the stack.

## 1. Exit codes

cli-extended's table, plus one tool-specific code.

| Code | Meaning in cgpctl / cgpd |
|---:|---|
| 0 | Completed. |
| 1 | Expected runtime failure: a daemon answer of `daemon-error`, `stop-incomplete`, `export-failed`, `export-too-large`, `busy` (retry); a filesystem error; the `report` extra failing to render. |
| 2 | Invocation, validation or safety refusal: usage errors; a daemon answer of `bad-argument`, `bad-policy`, `unknown-session`, `target-not-found`, `too-many-sessions`, `peer-refused`, `session-live`, `report-removed`; `prune` or `stop` declined without `--yes`; `cgpd` refusing a config, a second daemon, or a stale env var. |
| 3 | The daemon could not be reached or does not speak a usable contract. The `[ERROR]` line and the `--json` document carry one cause tag (below). |
| 130 | Ctrl-C. |

Cause tags for exit 3 (the CP-31 set, plus one):

| Tag | Meaning | Remedy printed by `doctor` and in the error line |
|---|---|---|
| `socket-not-mounted` | the socket's directory does not exist (a devcontainer without the `/run/cgprofile` bind) | add the bind mount; name the template line |
| `socket-missing` | directory exists, no socket (daemon not running) | `systemctl status cgpd`; start it |
| `daemon-refusing` | socket exists, connection refused or the daemon answers `busy` persistently | `journalctl -u cgpd`; report if it loops |
| `socket-permission` | the socket exists but the caller is not in its group, or `peer-refused` for the caller's uid | name the socket's group (derived by `stat`), the caller's groups, and the `allow_uids` key |
| `contract-unsupported` | the daemon answers contract 1 only and the verb needs contract 2, or the reverse | name both contract sets and the version to install |

The 1.x `ctl` table (0 ok, 2 refused, 3 fault) was wire for run-gate's exec carrier only and is not
preserved: that carrier is removed (docs/CONSUMERS.md, consumer impact).

## 2. `cgpctl`

```
cgpctl [--socket PATH] [--json] <verb> [args]
```

### 2.1 Socket selection

Resolution order, first hit wins: `--socket PATH`, then `CGPCTL_SOCKET`, then `/run/cgprofile/ctl.sock`.
`--socket` is a global option accepted before or after the verb. An empty `CGPCTL_SOCKET` is a usage
error (exit 2), not a silent fall-through to the default. The path may be any absolute or relative path
to a Unix socket; the client never rewrites it. Every verb that talks to the daemon uses the resolved
path, and `doctor` reports which source chose it (`flag`, `env`, `default`). The observer socket is not a
`cgpctl` concern; consumers reach it directly.

### 2.2 Verb surface

Daemon verbs need the group (section 2.4). "Mutating" verbs change daemon state.

| Verb | Wire op (contract 2) | Mutating | Summary |
|---|---|---|---|
| `status` | `version` + `status` | no | Daemon and client versions, contracts, uptime, slots, settings in effect, config digest. |
| `host` | `host` | no | Host facts: load, memory, pressure, per observed slice, gates slice, DAMON state. |
| `sessions [--state live\|done\|all] [--mine]` | `status` | no | One row per session. `done` is read from the stored-session listing carried by `status` in contract 2. |
| `show ID` | `status` (session) | no | Live counters for a live session, the summary for a finished one. |
| `start --target T [options]` | `start` | yes | Starts a daemon session. Prints the session id. |
| `stop ID` | `stop` | yes | Stops and finalizes; prints the summary. Own sessions only unless uid 0. |
| `watch ID [--watch-interval S]` | `watch` | no | Streams readings until the session ends. |
| `report ID [--out DIR] [--html-only\|--md-only]` | `export` | no | Exports the stored session and renders locally. |
| `report --run-dir DIR [...]` | none | no | Renders a standalone `run` result. |
| `prune [--keep N] [--keep-days D] [--dry-run] [--yes]` | `gc` | yes | Applies retention now. `--dry-run` lists what would go. |
| `run [--phase NAME=...] -- CMD...` | none | no | Profile one command locally, direct mode only. |
| `mark NAME [--kind K] [--meta JSON]` | none | no | Append a phase mark to the current run. Stdlib-only. |
| `doctor [--check NAME]` | `version` | no | Diagnose the install, the socket, the caller and the daemon. |
| `config show` | `config` | no | Effective settings, each with its source, and the config digest. |
| `skills install\|uninstall\|check\|list` | none | | cli-extended's `register_skills_verbs`, package `cgprofile`. |

Removed relative to 1.x with no replacement alias: `attach` (use `start` and `stop`, or `run`),
`targets` (use `start --dry-run`), the hidden `_collect`, `serve` (now `cgpd`), the whole `ctl` group,
helper mode, the placement probe container, and `--log-tail` / `--log-match`.

### 2.3 Verb details and JSON shapes

Every read verb accepts `--json` and then prints exactly one JSON document on stdout (watch: one JSON
object per line), nothing else on stdout. Human mode prints a table or key/value lines. All documents
carry `"schema": 1` (the cgpctl output schema, independent of the wire contract) and `"ok": true`. On
failure with `--json` the document is `{"schema": 1, "ok": false, "error": {"code": "...", "message":
"...", "cause": "<tag or null>"}}`, exit code per section 1. Timestamps are UTC ISO-8601 with `Z`, byte
counts are integers, seconds are numbers, an unknown value is `null`.

**`status`**

```
{"schema": 1, "ok": true,
 "client": {"version": "2.0.0", "contracts": [1, 2]},
 "daemon": {"version": "2.0.0", "contracts": [1, 2], "started_at": "...", "uptime_seconds": 0.0,
            "damon": "available|unavailable|disabled", "damon_default": "on|off",
            "sessions_live": 0, "max_sessions": 16},
 "socket": {"path": "...", "source": "flag|env|default", "group": "mdt-docker", "gid": 2751,
            "listening": true, "allow_uids": []},
 "resource_observer": {"contract": 2, "contracts": [2, 3], "path": "...", "listening": true},
 "config": {"file": "/etc/cgprofile/cgpd.toml", "digest": "sha256:...", "loaded_at": "...",
            "reloads": 0, "last_reload_error": null},
 "skew": {"client_vs_daemon": "equal|client-older|client-newer", "contract_ok": true}}
```

`status` exits 0 whenever the daemon answered. Skew is reported, never an error.

**`host`**

The `host` object exactly as the wire returns it (contract 1 fixture `host-v1.1.json`), plus `"schema"`
and `"ok"`. The observed slice set comes from the daemon's `observe_slices` config (fixes CP-36); the
per-slice block carries the full sampled groups.

**`sessions`**

```
{"schema": 1, "ok": true, "at": "...",
 "sessions": [{"session": "s-...", "state": "live|done|aborted", "owner_uid": 1003,
               "mine": true, "target": "containerid:<64hex>|cgroup:/...|slice:...",
               "scope": "container|container-shared|cgroup|slice", "started_at": "...",
               "ended_at": null, "elapsed_seconds": 0.0, "label": null}]}
```

`--mine` keeps rows whose `owner_uid` equals the caller's uid. Default state filter is `all`, newest
first. `label` is `meta.lane` when present.

**`show ID`**

For a live session the wire `status` entry (live counters, liveness, watch state, placement). For a
finished session the stored Summary (schema v1.1, `RG55-INTERFACE-CONTRACT.md` section 3) under a
`summary` key, with `"state"` beside it. An unknown id is exit 2 (`unknown-session`).

**`start`**

```
cgpctl start --target containerid:<64hex> | container:NAME | cgroup:/abs/path | slice:NAME.slice
             [--scope container|container-shared] [--token T] [--interval S] [--damon on|off]
             [--place --memory-high B --memory-max B --cpu-weight W]
             [--idle-bound auto|S] [--ceiling auto|S] [--on-stall report|kill]
             [--progress-stream PATH] [--meta JSON] [--dry-run]
```

`container:NAME` is resolved by the client with `docker inspect` to `containerid:<64 lowercase hex>`
when docker is reachable; without docker it is a usage error naming `containerid:` as the alternative.
The daemon never resolves names. `cgroup:` and `slice:` targets are new in contract 2 (PROTOCOL.md
section 10.4): read-only observation, no placement, no `on_stall=kill`. Defaults for an ad-hoc call: scope
`container` for container targets; the client fills the required `meta` keys with `kind: "adhoc"`,
`client: "cgpctl <version>"` and the caller's uid, and `--meta JSON` merges over them. `--dry-run` resolves
the target, prints what would be sent and the target's effective limits, and starts nothing. `--json`
output: `{"schema": 1, "ok": true, "session": "s-...", "started_at": "...", "target": {...},
"placement": null}` (the `target` and `placement` objects are the wire's). A `place-refused:*` outcome is
not a failure (exit 0, `placement.error` set), as on the wire.

**`stop ID`**

`{"schema": 1, "ok": true, "session": "s-...", "already_stopped": false, "summary": {...}}`, the wire's
fields under the CLI envelope. Without `--yes` on a session owned by another uid (uid 0 only) the verb
asks; `--yes` skips it. `stop-incomplete` is exit 1 with the retry hint.

**`watch ID`**

Human mode prints one status line per reading. `--json` prints the wire's lines verbatim, one JSON object
per line (`reading`, `verdict`, last `{"event": "end"}`), each with `"schema": 1` added. Exit 0 only when
exactly one terminal `end` line arrived; EOF alone is exit 3 (`daemon-refusing`), as in 1.x.

**`report`**

`report ID` runs `export` (PROTOCOL.md section 10.3), verifies the part digests, writes the four parts to
a temporary session directory, and calls the existing renderer into `--out DIR` (default
`./cgprofile-report-<session>/`). Output: `{"schema": 1, "ok": true, "session": "...", "out_dir": "...",
"files": [{"path": "report.html", "bytes": 0}, {"path": "report.md", "bytes": 0}]}`. The renderer needs
the `report` extra (pandas, plotly, matplotlib, scipy, ruptures). Without it the verb exits 1 with one
line naming `pip install 'cgprofile[report]'` and still offers `--export-only DIR`, which writes the raw
parts and renders nothing. Rendering cost is charged to the caller's cgroup. `report --run-dir DIR` is the
same renderer on a `run` result, needs no daemon, and is part of the degraded set.

**`prune`**

`{"schema": 1, "ok": true, "dry_run": false, "removed": ["s-..."], "kept": 12}`. Retention defaults come
from the daemon config; `--keep` and `--keep-days` override for this call only and are refused above the
daemon's configured maxima (no one can widen retention beyond the operator's policy, but a caller may
tighten it for sessions it owns; sessions of other uids are removed only by uid 0). Without `--yes` or
`--dry-run` the verb lists the candidates and asks.

**`run -- CMD...`**

Direct mode only: the command runs in the caller's own cgroup subtree, sampled by the stdlib collector;
the result is a run directory (`CGPROFILE_RUN_DIR` exported to the command; `mark` appends to it). Where
cgroupfs is not visible (a devcontainer without host cgroups) the verb fails with exit 2 and one line:
`direct mode needs cgroupfs; use 'cgpctl start' against the host daemon`. Output: `{"schema": 1, "ok":
true, "run_dir": "...", "exit_status": 0, "duration_seconds": 0.0}`; the profiled command's own exit
status is `exit_status`, and cgpctl exits 0 when profiling succeeded regardless of it unless
`--propagate-exit` is given.

**`mark`**

`cgpctl mark NAME [--kind K] [--meta JSON]` writes a phase mark into `$CGPROFILE_RUN_DIR`. It imports
nothing outside the standard library and cli-extended (a test pins this), needs no daemon, no socket and
no group. Exit 2 when `CGPROFILE_RUN_DIR` is unset.

**`doctor`**

cli-extended's doctor runtime (`--check NAME`, `--json`, exit 1 only on a `fail`). Checks, in order, each
with a `pass|warn|fail|skip` status and a remedy on non-pass:

| Check name | Passes when |
|---|---|
| `socket` | the resolved socket path exists, is a socket, and accepts a connection. Names the cause tag otherwise |
| `caller-group` | the caller's gid or supplementary gids include the socket's group (derived by `stat`); `skip` when the socket is absent |
| `daemon-version` | the daemon answers `version`; warns on client/daemon skew |
| `contracts` | the ctl contract sets overlap; the observer contract set contains 2 and 3 |
| `observer-dir` | `/run/cgprofile/observer` is mode 0751 with a group that is not the ctl group and not the docker group |
| `slice` | `cgprofile.slice` exists and is bounded (reads `memory.max` / `memory.high`) |
| `damon` | the daemon reports DAMON available and the lock free |
| `sessions-dir` | reported from the daemon's `status`, not tested from the client |
| `report-extra` | the `report` extra imports (`warn`, not `fail`, when missing) |
| `skills` | the packaged agent skill is installed and current (cli-extended `skills check`) |

`doctor` is in the degraded set: with no daemon access it still runs `socket`, `caller-group`,
`report-extra` and `skills`, marks the rest `skip`, and the failing check prints the fix, for example
`add yourself to mdt-docker (gid 2751) and re-login`, or `the socket is missing: systemctl start cgpd`.

**`config show`**

`{"schema": 1, "ok": true, "file": "...", "digest": "sha256:...", "keys": {"interval": {"value": 1.0,
"source": "default|file", "reloadable": true}, ...}}`, one entry per key of section 3.2. Read-only. There
is no `config set`: settings change only by editing the file and reloading (section 3.3).

### 2.4 Access-control behaviour

| Caller | Verbs that work |
|---|---|
| Any user | `--version`, `--help`, `run`, `mark`, `report --run-dir`, `skills *`, `doctor` (degraded: the local checks and the remedy for the failing one) |
| Member of the `ctl.sock` group, and in `allow_uids` when that is set | everything in section 2.2 against their own sessions; `sessions`, `show`, `watch`, `status`, `host` against all sessions |
| uid 0 | the above, plus `stop`, `report` (export) and `prune` of other uids' sessions |
| `cgprofile-observer` group member | nothing in `cgpctl`; the observer socket is reached by consumers directly, by design |

A daemon verb without access exits 3 with cause `socket-permission`, the socket's group, the caller's
groups and the remedy; no verb retries. The ctl group is root-equivalent on mdt hosts (it is the Docker
relay group), so session ownership is hygiene, not a security boundary, and this document says so on
purpose. Ownership is enforced by the daemon from `SO_PEERCRED` (PROTOCOL.md section 10.5), never by the
client.

### 2.5 Environment read by `cgpctl`

| Variable | Use |
|---|---|
| `CGPCTL_SOCKET` | socket override (section 2.1) |
| `CGPROFILE_RUN_DIR` | the current run directory (`mark`, `report --run-dir`); set by `run` for its child |
| `CGPROFILE_MARK` | removed: wrapped commands call `cgpctl mark` through `PATH`; `run` prepends the directory of the running `cgpctl` to the child's `PATH` |

## 3. `cgpd`

```
cgpd [--config PATH] <verb>
```

Root only. `cgpd run` refuses to start as a non-root euid (exit 2) and `cgpd unit` / `cgpd config` run
as any user (they read and render, never touch live state). `cgpd` is a service entrypoint in
cli-extended's sense: `--help`, `--version` and malformed arguments never start the service, create state
or touch the socket.

### 3.1 Verbs

| Verb | Summary |
|---|---|
| `run` | The default verb and the unit's `ExecStart`. Loads the config, runs the preflight, binds both sockets, serves until SIGTERM. |
| `unit print [--wheel-venv PATH] [--install-root PATH]` | Prints `cgprofile.slice` and `cgpd.service` rendered from the template in the wheel, with the CP-42 limits. Nothing is read from a checkout. |
| `unit check [--installed PATH ...]` | Compares installed unit files with the rendered output; exit 2 on drift, naming the first differing line. |
| `config check [--file PATH]` | Validates the file (unknown keys, types, ranges, slice names, uid list) without starting anything. Exit 0 valid, 2 invalid. |
| `skills`, `doctor` | Not registered: `cgpd` has no agent skill, and diagnosis is `cgpctl doctor`. |

`cgpd run` preflight, each failure a one-line exit 2 that names the fix, nothing partially started:

1. effective uid 0;
2. the config loads and validates; no stale `CGPROFILE_*` daemon variable is set (section 3.4);
3. the pid-namespace identity check passes (own `/proc/self/ns/pid` equals `/proc/1/ns/pid`; passes
   trivially natively, still enforced);
4. `cgprofile.slice` is bounded (the 1.1.0 rule, unchanged);
5. the sessions directory is creatable and writable;
6. no live daemon answers on `ctl_socket` (section 3.5 of NATIVE-DEPLOYMENT.md): a successful `version`
   exchange there is a refusal, exit 2, `another daemon already serves <path> (version X, pid Y)`;
7. the directory of `ctl_socket` exists; the daemon adopts its gid (unchanged CP-43 rule) and re-asserts
   0770 / 0660.

SIGTERM drains as today (shutdown grace unchanged). SIGHUP reloads (section 3.3). SIGUSR1 is unused.
`cgpd run` logs to stderr in `key=value` lines for the journal; there is no log file option.

### 3.2 Config file

`/etc/cgprofile/cgpd.toml`, root-owned, mode 0644, TOML, parsed with `tomllib`. Override with
`cgpd --config PATH`. A missing file at the default path means all defaults (so an off-mdt install works
with no file); a missing file at an explicit `--config PATH` is an error. A single table, no nesting.
Unknown keys are refused, naming the nearest valid key.

| Key | Type | Default | Group | Replaces (1.x flag / env) |
|---|---|---|---|---|
| `sessions_dir` | path | `/var/lib/cgprofile/sessions` | fixed | `serve --sessions`, `CGPROFILE_SESSIONS` (daemon use) |
| `ctl_socket` | path | `/run/cgprofile/ctl.sock` | fixed | `serve --socket` |
| `observer_socket` | path | `/run/cgprofile/observer/resource-observer.sock` | fixed | none (constant) |
| `damon_lock` | path | `/run/cgprofile/damon.lock` | fixed | `CGPROFILE_DAMON_LOCK_PATH` |
| `proc_root` | path | `/proc` | fixed | `CGPROFILE_PROC_ROOT` (was `/hostproc` in the container) |
| `busctl` | string | `busctl` | fixed | `CGPROFILE_BUSCTL` |
| `export_max_bytes` | integer | `268435456` (256 MiB) | reloadable | none (new, section 10.3 of PROTOCOL.md) |
| `interval` | number, seconds | `1.0` | reloadable | `serve --interval` |
| `damon_default` | `"on"` \| `"off"` | `"on"` | reloadable | `serve --damon-default` |
| `keep_sessions` | integer >= 1 | `200` | reloadable | `serve --keep-sessions` |
| `keep_days` | integer >= 1 | `14` | reloadable | `serve --keep-days` |
| `max_sessions` | integer 1..64 | `16` | reloadable | `serve --max-sessions` |
| `observe_slices` | array of slice names | `[]` | reloadable | `serve --observe-slices` (comma list) |
| `gates_slice` | slice name | `"dev-gates.slice"` | reloadable | `serve --gates-slice` |
| `allow_uids` | array of integers | `[]` | reloadable | `CGPROFILE_ALLOW_UIDS` (comma list) |

Removed with no replacement key: `CGPROFILE_PID_NAMESPACE_MODE` (the check always runs),
`CGPROFILE_HELPER_IMAGE`, `CGPROFILE_PLACEMENT_PROBE_IMAGE`, `CGPROFILE_HELPER_CGROUP_PARENT`,
`CGPROFILE_IN_HELPER` (helper mode is gone), `CGPROFILE_VERSION` (the version is the installed
distribution's, via scm; it is no longer an environment input).

Validation reuses the 1.x rules: slice names must expand to a cgroup path (`a--b`, `-a` and names with a
`/` are refused); `allow_uids` entries are non-negative integers; `max_sessions` bounds the handler pool
sizing. `interval` is clamped to [0.25, 30] at use, as on the wire.

### 3.3 Reload

`systemctl reload cgpd` runs `ExecReload=/bin/kill -HUP $MAINPID`. On SIGHUP the daemon reads the file,
validates it fully, and swaps all reloadable keys in one atomic step; live sessions keep the settings
they started with (a changed `interval` or `damon_default` applies to sessions started afterwards, a
changed `keep_*` applies at the next retention pass, `max_sessions` below the live count only blocks new
starts, `allow_uids` applies to the next connection). A change to a fixed key is reported and ignored:
`config.last_reload_error` names `restart required for <key>`, the old value stays, and `cgpctl status`
shows it. An invalid file leaves every old setting in force and records the error; it never kills the
daemon. The daemon logs one line per reload with the new digest. The daemon cannot change its own
settings over the socket: there is no write op (rejected in the plan, kept rejected: drift from
host-setup's declared state, the socket would rewrite its own access list, and persistence is not the
daemon's job).

### 3.4 Stale environment

The daemon reads no `CGPROFILE_*` configuration variable. If any variable listed in the "Replaces"
column or the removed list is set in the daemon's environment, `cgpd run` exits 2 and names the config
key that replaces it (or says it was removed). `CGPROFILE_ALLOW_UIDS` set in a unit's `Environment=` is
the expected leftover; the message is `CGPROFILE_ALLOW_UIDS is no longer read; set allow_uids in
/etc/cgprofile/cgpd.toml`. This is deliberate: a trust decision silently ignored is worse than a refusal.

## 4. What this specification does not fix

- The text of help and error lines beyond the shapes above: cli-extended's surface review owns it.
- The renderer's internals: `report` consumes the four exported parts through the existing
  `lib/analyze.py` path, now under `cgprofile.report`.
- Shell completion: not offered by cli-extended; filed upstream as CLI-EXT-next rather than hand-rolled.
