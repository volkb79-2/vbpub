# cgprofile daemon protocol — the wire, verb by verb

**Scope.** How a consumer talks to `cgprofile serve`. The authority for the
CONTENT of every response is `RG55-INTERFACE-CONTRACT.md` (§2 the verbs, §3
the Summary, §7 the computation rules, §8 the v1.1 amendment, §9 the native
R2 observer); this file is
the authority for the REQUEST side — the carriers, the one request shape,
and each verb's `args` names — because that is what a consumer has to get
byte-right and the contract states it only in prose.

Started in RG-55 P6 C6 (socket carrier); `watch` (§8.2) and the stall policy
(§8.4) landed in C7, placement (§8.3) in C8. Every verb, every `args` name
and every error code below is live.

## 1. Two carriers, one protocol (§8.1, design D-30)

| carrier | how a request travels | who is authorised |
|---|---|---|
| `exec` (default, permanent) | `docker exec cgprofile-host-daemon cgprofile ctl <verb> … --json` — the in-image client connects to the socket from inside and prints ONE JSON document on stdout | anyone who can use the docker socket; arrives inside the daemon as uid 0 |
| `socket` (opt-in) | connect to `/run/cgprofile/ctl.sock` (bind-mounted to the same path on the host) and write ONE request line; the daemon answers with one line and closes | the group that owns the directory — the `docker` group, per mdt host-setup's `mdt-cgprofile.conf` — plus, when `CGPROFILE_ALLOW_UIDS` is set, only those uids |

Every verb, response, error code and the `contract` field are identical on
both carriers. A consumer may diff them; `tests/test_serve_socket_carrier.py`
does exactly that on every verb, against one serve loop.

The in-image `ctl` client is the **reference translator**: whatever
`cgprofile.py`'s `_ctl_request()` builds for a given command line is what a
socket-carrier consumer should send. The frozen bytes are in
`tests/fixtures/rg55/socket/<verb>-request.json`.

## 2. The request line

```json
{"verb": "<verb>", "args": {…}, "contract": 1}
```

* Exactly these three top-level keys. **Any other top-level key is refused**
  with `bad-argument` — in particular contract v1's flat request
  (`{"verb": "stop", "session": "s-…"}`) is an error, not a shorthand.
* `args` may be omitted for a verb that takes none; it must be a JSON object
  when present.
* `contract` may be omitted (it is assumed to be `1`); any other value is
  refused with `bad-argument` naming both numbers.
* `args` keys are **the long-option names with the dashes stripped**:
  `--memory-high` → `memory_high`. `--meta` is the one option whose CLI form
  is a JSON *string* and whose wire form is a JSON *object*.
* Optional-argument omission and `null` semantics are defined per field in
  the table below. In particular, omit `damon` to use the daemon's configured
  default; a present `damon: null` is malformed and receives `bad-argument`.
* One request per connection, terminated by `\n`. The complete request line,
  including its newline, is limited to 1,048,576 bytes and must arrive within
  25 seconds of connection acceptance; trickle bytes do not extend that
  deadline. An oversized line receives `bad-argument`; an incomplete line at
  the deadline or EOF is closed without dispatch. The response is one JSON
  object plus `\n`, then the daemon closes.
* **The streaming exception (§8.2, C7).** `watch` — and only `watch` — is
  answered with one JSON object PER LINE until the session ends, on one
  connection that stays open the whole time. Concretely, for a consumer:
  send the request line exactly as for any other verb, then read lines
  until the daemon closes; the last line is always `{"event": "end"}`. Do
  not reuse the connection for a second request, and do not expect the
  first line to be the whole answer.
* The daemon's server-side connection timeout is **25 s** for non-streaming
  verbs (§8.1 rule 4). A `watch` connection has **no** server-side timeout —
  silence for a whole `--watch-interval` is its normal state. Consumers
  apply the per-verb timeouts of §1.5 for the other verbs, and for `watch`
  an idle timeout of `3 x watch-interval` with a re-attach (§8.2).
  The in-image `ctl watch` exits 3 if the socket closes without exactly one
  complete terminal `end` line; EOF by itself is not a successful watch.
* A `watch` request that reaches a caller which cannot stream is refused
  with §8.8's `not-streaming` — never answered with a single reading.

## 3. `args` per verb

| verb | `args` key | CLI option | type | required | notes |
|---|---|---|---|---|---|
| `version` | — | — | — | — | no arguments |
| `host` | — | — | — | — | no arguments |
| `gc` | — | — | — | — | no arguments |
| `status` | `session` | positional `<session>` | string \| null | no | `null` lists every LIVE session plus `host`; an id returns that one session plus `host`; an unknown id is `unknown-session` |
| `stop` | `session` | positional `<session>` | string | yes | idempotent: a stopped session answers `already_stopped: true` |
| `report` | `session` | positional `<session>` | string | yes | finished sessions only; may exceed 30 s |
| `start` | `target` | `--target` | string | yes | `containerid:<64 lowercase hex>`; the consumer resolves the id |
| | `scope` | `--scope` | string | yes | `container` \| `container-shared` |
| | `token` | `--token` | string \| null | no | `[A-Za-z0-9._-]{8,64}`; the value exported as `RUN_GATE_PROFILE_SESSION` into the lane |
| | `damon` | `--damon` | string | no | `on` \| `off`; omission = the daemon's `damon_default`; explicit `null` is invalid |
| | `interval` | `--interval` | number \| null | no | seconds, clamped to [0.25, 30]; `null` = the daemon default |
| | `meta` | `--meta` | object | yes | §2.2's keys; unknown keys are stored verbatim, never rejected |
| | `progress_stream` | `--progress-stream` | string \| null | no | the lane's own progress NDJSON path AS THE LANE SEES IT; must be absolute (§8.4) |
| | `idle_bound` | `--idle-bound` | `"auto"` \| number \| null | no | `auto` (the default) = `max(300, 3 x cadence hint)` |
| | `ceiling` | `--ceiling` | `"auto"` \| number \| null | no | `auto` (the default) = `3 x meta.expected.duration_s`, else none |
| | `on_stall` | `--on-stall` | string \| null | no | `kill` \| `report`; `null` = `report` |
| `watch` | `session` | positional `<session>` | string | yes | streaming, §8.2 |
| | `watch_interval` | `--watch-interval` | number | no | seconds, default 30, clamped [5, 300] |

The four policy options are forwarded **verbatim** by the reference
translator: `--idle-bound`/`--ceiling` take `auto` or a number, so the
client cannot type them as a float, and the daemon owns the judgement. An
unparsable value is §8.8's **`bad-policy`**: exit 2 and the session is NOT
started (distinct from `bad-argument`, which never had a session at stake).
A non-numeric `watch_interval` is a `bad-argument`, not a `bad-policy` —
it is a request argument, not a stall policy.

`--on-stall kill` uses no numeric PID signals. With `scope=container`, the
daemon may write `cgroup.kill` only on the exact container cgroup whose leaf
name proves the requested 64-hex ID, whose path is beneath the verified,
bounded gates slice, and whose `cgroup.events` reports `populated 1`.
With `scope=container-shared`, it requires a token and an
explicit successful `--place` leaf; it never adds placement itself. If the
requested boundary is unavailable at start, the request is `bad-policy` and
no live session is created. If the boundary is empty, unreadable, or later
refuses the write, the watch verdict is `reported`, not `killed`.

`watch`'s lines are §8.2's three shapes, frozen one per file in
`tests/fixtures/rg55/watch-{reading,verdict,end}.json` (and as the whole
stream in `socket/watch-response.json`):

* `reading` — every `--watch-interval`: `live` (as in `status`), `liveness`
  (§8.4) and `placement` (§8.3 — the block for a placed or refused session,
  `null` for one that never asked to be placed).
* `verdict` — only when the state CHANGES, carrying exactly
  `{state, verdict, reason, readings}`. The policy those came from is in
  `status`/`stop`/the Summary (§8.7), not on every line.
* `end` — exactly one, last: `stopped` \| `killed` \| `daemon-shutdown`.

`start`'s placement options (§8.3) follow the same naming rule:

| verb | `args` key | CLI option | type | required | notes |
|---|---|---|---|---|---|
| `start` | `place` | `--place` | boolean | no | `true` asks systemd for a transient delegated scope under the verified gates slice and a cgprofile-owned leaf `<scope>/rg-<token>`; requires `token` (`place-refused:no-token` without one). The other three are IGNORED when it is false |
| | `memory_high` | `--memory-high` | integer \| null | no | bytes; `memory.high` on the leaf (the throttle point), applying to cgroup charges rather than total process RSS |
| | `memory_max` | `--memory-max` | integer \| null | no | bytes; `memory.max` on the leaf, applying to cgroup charges rather than total process RSS. Above the gates slice's own `memory.max` → `place-refused:over-slice` |
| | `cpu_weight` | `--cpu-weight` | integer \| null | no | 1..10000; `cpu.weight` on the leaf |

A cap that is not a number, or a `cpu_weight` outside `[1, 10000]`, is a
**`bad-argument`** (exit 2, no session): the CLIENT typed it wrong. Every
`place-refused:*` code is the opposite case — what the HOST turned out to
be — and none of them fails `start`: the session runs unplaced with
`placement.error` set and `leaf: null`. The block itself is `null` only for
a session that never asked to be placed. An existing token-derived scope name
is refused as `place-refused:scope-unavailable`; an existing leaf below a
newly created scope is refused as `place-refused:write-failed:<leaf path>`.
The daemon never adopts or reuses a scope or leaf whose ownership it cannot
verify.

After successful placement, memory metrics are charges attributed to the leaf.
Moving a process does not transfer charges for pages it faulted before the
move, so leaf counters are not total RSS and leaf `memory.max` is not a hard
cap on all memory already resident in the lane. See the resource-accounting
note in `docs/CONSUMERS.md` and the rationale in `docs/DESIGN-GUIDE.md`.

## 4. Authorisation on the socket carrier (§8.1)

1. The host directory's mode and group decide who may connect at all. mdt
   host-setup ships `mdt-cgprofile.conf` (`d /run/cgprofile 0770 root docker
   -`); the daemon only **re-asserts** `0770` on the directory and
   `root:<that directory's gid>` `0660` on the socket at every start. It
   never chooses the group itself (RW-35(b), RW-37).
2. A `root:root` directory (host-setup not installed) means the socket is
   root-only. The daemon logs one INFO line and keeps serving — the exec
   carrier is unaffected.
3. `CGPROFILE_ALLOW_UIDS` (comma-separated uids, compose env) adds an
   `SO_PEERCRED` check: uid 0 is always allowed, listed uids are allowed,
   everything else gets
   `{"ok": false, "contract": 1, "error": {"code": "peer-refused", "message": …}}`
   and the connection closes. Unreadable peer credentials are refused when
   an allowlist is configured (fail closed) and served when none is
   (nothing to check against). A malformed allowlist refuses to start the
   daemon.

## 5. `version.transports` (§8.6)

```json
"transports": {"exec": true,
               "socket": {"path": "/run/cgprofile/ctl.sock", "listening": true,
                          "allow_uids": [], "peer_cred": true}}
```

`allow_uids` is `[]` when none is configured. `listening` reflects the real
listener. `exec` is `true` by construction — the exec carrier is permanent
(§8.1 rule 5), not probed.

The additive `resource_observer` field reports the distinct observer listener:

```json
"resource_observer": {"contract": 2, "contracts": [2, 3],
                       "path": "/run/cgprofile/observer/resource-observer.sock",
                       "listening": true}
```

Its contract number is independent of the control protocol's `contract: 1`.
If binding the observer socket failed, `listening` is false; opted-in native
R2 consumers still fail closed when their configured endpoint is unavailable.

## 6. Errors

Response shape is always
`{"ok": false, "contract": 1, "error": {"code": "<kebab-code>", "message": "…"}}`,
exit code 2 over the exec carrier (§1.3). Codes: `bad-argument`,
`target-not-found`, `too-many-sessions`, `unknown-session`,
`report-unavailable`, `report-failed`, `stop-incomplete` (a `stop` whose
sampling worker did not exit within 10 s: the session is NOT finalized, keeps
its placement and DAMON resources, and `stop` may be retried; the third
consecutive incomplete attempt finalizes anyway with `stop_forced`; also
answered when finalization itself failed, naming the error, in which case the
session is reopened and a retry is safe), `busy` (additive, contract stays 1:
the daemon is at an admission bound, 32 connections reading a request line, 16
verbs running, 2 `report` verbs running or 32 `watch` streams, or is shutting down; retry shortly), and
from §8.8 `peer-refused`, `place-refused:*`, `bad-policy`, `not-streaming`.

Additive optional Summary keys (cgprofile 1.1.0; the contract number stays 1
and the Summary schema stays v1.1; full definitions in
`RG55-INTERFACE-CONTRACT.md` §8.7):

* `summary.damon_cleanup` — `{confirmed, slot, quarantined}`, present when a
  DAMON owner was closed; `confirmed: false` means the kdamond teardown could
  not be verified (it may still be active), which is NOT the same as DAMON off.
* `summary.stop_forced` — `"sampler-still-running"` when the session was
  finalized while its sampling worker was still alive (the third consecutive
  `stop-incomplete`, or daemon shutdown); the manifest is then `aborted` with
  the same reason. Absent otherwise.
* `summary.ancestors_memory_events_local` — the local `memory.events` deltas of
  the target's ancestors, so a consumer can tell reclaim driven from above the
  lane from the lane's own limit (the lane's own events stay in `events`):
  `[{path, high, max, oom, oom_kill}, ...]`, ordered from the target's parent
  up to and including `/dev.slice`. Each counter is the increase over the
  session. An ancestor whose `memory.events.local` could not be read at either
  end keeps its `path` and carries `null` counters. A session recovered after
  a daemon restart has no start reading, so the key is absent. Golden:
  `tests/fixtures/rg55/summary-v1.1-ancestors.json`.

## 7. Goldens

`tests/fixtures/rg55/socket/<verb>-request.json` and `<verb>-response.json`
are produced by running the real client and the real daemon (see that
directory's `README.md` for how, and for the only normalization applied).
They are compared byte-for-byte by `tests/test_serve_socket_carrier.py`, and
they are what the run-gate socket client (P5) is written against.

## 8. Native R2 resource observer (RG-89)

This is a **separate listener**, not a new verb on the control carrier:
`/run/cgprofile/observer/resource-observer.sock`. Host setup provisions the
root-owned `/run/cgprofile/observer` directory as `0751` with the dedicated
`cgprofile-observer` group. Run-Gate bind-mounts this directory read-only into
an explicitly opted-in Assay R2 container, so daemon restarts replace the
socket inode at a path an existing container can still resolve. The listener is served
by the existing daemon and accepts no control operation, including `start`,
`stop`, or placement. Its socket mode uses the dedicated observer directory
group and the `CGPROFILE_ALLOW_UIDS` policy; Run-Gate forwards the observed
socket GID as a supplementary group. Observer lanes also declare per-container
memory and PID ceilings and refuse arbitrary extra mounts. A read-only mount of `ctl.sock`
is not equivalent and is prohibited for this use.

The observer requires contract 2, independently of the daemon's contract-1
control protocol. Contract 2 reports regular event scope and effective local
counters. Contract-1 observer requests receive `bad-argument` with an
unsupported-contract message. The request is:

```json
{"verb":"resource-limits","args":{"thread_id":1234},"contract":2}
```

There is one positive native TID, one line, and one connection. The complete
line, including its terminating newline, is at most 4096 bytes; a larger
complete line or an unterminated request that grows beyond that limit gets a
`bad-argument` response and is never dispatched. The server enforces one
absolute five-second deadline. `SO_PEERCRED` is required.
The daemon uses that PID/UID plus the requested TID to prove the caller's
thread identity, shared thread cgroup/credentials, and gates-slice membership;
the request cannot choose a PID, cgroup path, or slice. A successful response
contains only the complete canonical leaf-to-root cgroup-v2 ancestry, mount
ID, limit presence/value pairs, both event-counter scopes, and effective
local event maps at every path. Successful responses always report
`pids_events_scope: "hierarchical"`; the observer refuses a mount using
`pids_localevents`, because that option counts failed forks in the cgroup where
the process tried to fork even when an ancestor's `pids.max` rejected it. That
counter cannot identify the enforcing cgroup. `memory_events_scope` reports
whether regular memory events are hierarchical or local under
`memory_localevents`. The observer reads the `.local` files when available; if
a regular memory file is already local under the mount mode, it can supply
equivalent local evidence when the `.local` file is unavailable. The response
contains no process list, PID, or sibling cgroup data. See contract §9 for exact response
shapes and the verified private-cgroup-namespace path mapping.

Assay configures this socket explicitly and consults it only after its local
reader proves that ancestors are hidden. Any other local failure, invalid
response, identity mismatch, incomplete hierarchy, or positive shared-ancestor
local event without a candidate event remains a fail-closed lane error.

## 9. Observer contract 3: measurement windows

Contract 3 adds two verbs to the observer socket of section 8. It is additive:
`resource-limits` and contract 2 are unchanged for every 1.x release, a
contract-2 client sees no difference, and `version.resource_observer` reports
`"contract": 2` with `"contracts": [2, 3]`. The same framing rules apply: one
request line of at most 4096 bytes including the newline, one connection, a
five-second deadline, `SO_PEERCRED` required, the `CGPROFILE_ALLOW_UIDS`
policy. Contract-3 replies, including errors, carry `"contract": 3`; a
contract-2 request cannot reach the window verbs, and a contract-3 request
cannot reach `resource-limits`.

The daemon is the single source of per-subject resource numbers. A consumer
that gets no number records `NO_MEASUREMENT` with a reason code (see
[the consumer rules](CONSUMERS.md#measurement-windows-observer-contract-3)); it
never reads procfs or cgroupfs itself.

```json
{"verb":"window-open","args":{"thread_id":1234,"root_pid":5678,"mode":"process-tree","interval_ms":250},"contract":3}
{"ok":true,"contract":3,"window":"w-0123456789abcdef0123456789abcdef","mode":"process-tree","started_at":"2026-11-03T10:00:00Z","kernel":{"peak_reset":false}}
{"verb":"window-close","args":{"thread_id":1234,"window":"w-0123456789abcdef0123456789abcdef"},"contract":3}
```

`thread_id` is the caller's native TID, proven exactly as for contract 2.
`root_pid` is in the caller's PID namespace. `interval_ms` is optional
(default 250) and must be 100-5000. The window id is 128 bits of hex: a
per-daemon epoch and random bits. Fixtures (one request and its reply each):
`tests/fixtures/rg55/observer-window-v3-open.json` and
`observer-window-v3-close-{process-tree,cgroup,shared}.json`.

**Binding.** `root_pid` must be the caller or a live descendant of it (parent
chain), in the caller's cgroup or one below it; its start time is rechecked.
The caller must be below the gates slice, as for contract 2. A window belongs
to the opening process; a close from another process gets `peer-refused`.

**Modes.**

* `process-tree` works in a shared container. Every interval the daemon walks
  the descendants of `root_pid`: `rss_peak_bytes` is the largest summed
  `statm` resident size (`sampled-sum-rss`); `io_read_bytes`/`io_write_bytes`
  sum `/proc/<pid>/io`, keeping an exited process's last value
  (`proc-io-lower-bound`); CPU is the root's `utime+stime+cutime+cstime` at
  close, labelled `proc-stat-exact` for an unreaped zombie root,
  `proc-stat-live` for a running root, `proc-stat-sampled` for a reaped one
  (its last sampled value). Everything only a cgroup can answer is
  `REQUIRES_CGROUP_MODE`. Close a window after `waitid(WEXITED|WNOWAIT)` and
  before reaping the root to get exact CPU.
* `cgroup` reads the leaf that holds the root: `memory.peak` and
  `memory.swap.peak` through file descriptors opened and reset at open
  (per-file reset, Linux 6.12 or later; otherwise `PEAK_RESET_UNSUPPORTED` and
  `kernel.peak_reset` is false), `cpu.stat`, `io.stat` summed over devices and
  PSI totals, all as deltas from open. `rss_peak_bytes` is sampled as in
  process-tree mode. `attribution.baseline_bytes` is `memory.current` at open;
  `memory_peak_bytes - baseline_bytes` is the subject's increment (an upper
  estimate). A file the leaf does not expose gives `CONTROLLER_DISABLED`.
  Every interval the daemon reads `cgroup.procs`; a PID that is not in the
  root's tree, not the caller and not one of the caller's ancestors is
  foreign. `attribution.foreign_pids_max > 0` makes every cgroup-sourced
  metric null with `SHARED_CGROUP`.

All fifteen metric keys are always present in `metrics`; each null has its
reason in `missing`, and each non-null has its label in `sources`.
`attribution` is `{mode, foreign_pids_max, tree_pids_max, samples,
baseline_bytes}` (`foreign_pids_max` is 0 and `baseline_bytes` null in
process-tree mode). `context` is not attributed to the subject: the containing
cgroup's PSI (`container_psi_*_seconds`) and local `memory.events` deltas
(`container_memory_events_local`), the host's `/proc/pressure` deltas
(`host_psi_*_seconds`), and `ancestors_memory_events_local`, the same list as
the Summary's, over the window.

**Errors.** `bad-argument` (malformed request, bad mode or interval, an unknown
or already-closed window), `peer-refused` (no credentials, or a close from
another process), `observer-refused` (the peer or `root_pid` cannot be bound),
`window-refused` with `"reason":"limit"` (64 open windows per process, 4096 per
daemon, or no file descriptors left), `window-lost` (a well-formed id the
daemon no longer holds: it restarted, the window expired, or its process
died), `daemon-error`.

**Bounds.** A window expires after 6 hours. One daemon thread samples every
window; request handlers only open and read, so a slow window never delays
another peer's answer. A window keeps running maxima and sums only. Open
windows are not persisted: after a restart a close gets `window-lost`.
