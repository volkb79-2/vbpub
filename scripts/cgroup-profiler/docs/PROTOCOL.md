# cgprofile daemon protocol — the wire, verb by verb

**Scope.** How a consumer talks to `cgpd`. The authority for the
CONTENT of every response is `RG55-INTERFACE-CONTRACT.md` (§2 the verbs, §3
the Summary, §7 the computation rules, §8 the v1.1 amendment, §9 the native
R2 observer); this file is
the authority for the REQUEST side — the carriers, the one request shape,
and each verb's `args` names — because that is what a consumer has to get
byte-right and the contract states it only in prose.

Started in RG-55 P6 C6 (socket carrier); `watch` (§8.2) and the stall policy
(§8.4) landed in C7, placement (§8.3) in C8. Every verb, every `args` name
and every error code below is live.

> **Contracts.** The control socket speaks **ctl contract 2** (the `export`, `config`, `config-set` and
> `config-reset` operations, session ownership; section 10) and, for older consumers, **contract 1**
> (sections 1 to 9). The daemon serves both; a request without a `contract` field is contract 1. Section 10
> also says how peers recognise each other. The command-line half is [`CGPCTL-SPEC.md`](CGPCTL-SPEC.md).

## 1. One carrier: the Unix socket (§8.1, design D-30)

The control protocol travels over one carrier. Connect to the control socket (`/run/cgprofile/ctl.sock` by
default, the `ctl_socket` key of `cgpd.toml`) and write ONE request line; the daemon answers with one line and
closes. The only exception is `watch`, which streams. `cgpctl` is the reference client: whatever its
`ctlwire` module builds for a given command line is what any other consumer should send. The frozen bytes
are in `tests/fixtures/rg55/socket/<verb>-request.json`.

| aspect | rule |
|---|---|
| who may connect | the group that owns `/run/cgprofile` (the directory's mode and group, set by the host's tmpfiles rule), plus the `allow_uids` list of `cgpd.toml` when it is non-empty (section 4) |
| who is root-equivalent | uid 0 is always allowed |
| there is no fallback | a caller without access to the socket has no other path to the daemon; `cgpctl doctor` names the remedy |

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

## 4. Authorisation on the socket (§8.1)

1. The host directory's mode and group decide who may connect at all. The
   host's tmpfiles rule creates `/run/cgprofile` as `0770 root:<ctl group>`;
   the daemon only **re-asserts** `0770` on the directory and
   `root:<that directory's gid>` `0660` on the socket at every start. It
   never chooses the group itself (RW-35(b), RW-37).
2. A `root:root` directory (no host provisioning) means the socket is
   root-only. The daemon logs one INFO line and keeps serving.
3. The `allow_uids` key of `cgpd.toml` (an array of uids; runtime-tunable by
   uid 0, `CGPCTL-SPEC.md` section 3.5) adds an `SO_PEERCRED` check: uid 0 is
   always allowed, listed uids are allowed, everything else gets
   `{"ok": false, "contract": 1, "error": {"code": "peer-refused", "message": …}}`
   and the connection closes. Unreadable peer credentials are refused when
   an allowlist is configured (fail closed) and served when none is
   (nothing to check against). A malformed list refuses to load the
   configuration; the daemon reads no `CGPROFILE_ALLOW_UIDS` variable.

## 5. `version.transports` (§8.6)

```json
"transports": {"exec": false,
               "socket": {"path": "/run/cgprofile/ctl.sock", "listening": true,
                          "allow_uids": [], "peer_cred": true}}
```

`allow_uids` is `[]` when none is configured. `listening` reflects the real
listener. At contract 1 the `exec` key is kept for old readers and is `false`:
there is no second carrier. A contract-2 reply omits it (section 10.1).

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
`{"ok": false, "contract": 1, "error": {"code": "<kebab-code>", "message": "…"}}`
(the `contract` field echoes the request's). `cgpctl` maps codes to exit
codes in `CGPCTL-SPEC.md` section 1. Codes: `bad-argument`,
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
group and the `allow_uids` policy; Run-Gate forwards the observed
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
five-second deadline, `SO_PEERCRED` required, the `allow_uids`
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

## 10. ctl contract 2 (cgprofile 2.0.0)

Specification. Contract 2 is the control-socket contract of the native `cgpd` daemon. It is additive for
every verb that exists in contract 1 except one: the `report` verb is removed. The observer socket
(sections 8 and 9, contracts 2 and 3) is a separate protocol with its own numbers and is not touched.

### 10.1 Versioning and negotiation

* The request line is unchanged (section 2); `contract` may now be `1` or `2`.
* A 2.x daemon serves **both** contracts on the control socket. It stamps every response, error
  included, with the contract number of the request it answers. An omitted `contract` is still assumed to
  be `1`, so unmodified 1.x clients keep working without any change.
* `version` is answered identically at both contracts and gains, additively, `contracts: [1, 2]` and
  `daemon.version` (the installed distribution's version). A 1.x daemon answers `version` without
  `contracts`; a client treats its absence as `[1]`. The `transports.exec` key is dropped from the
  contract-2 reply of a 2.x daemon (the exec carrier no longer exists) and kept, `false`, in the
  contract-1 reply so 1.x consumers that read it do not break.
* `export`, `config`, `config-set` and `config-reset` exist only at contract 2. At contract 1 they are refused with `bad-argument`
  naming both numbers, exactly as a 1.x daemon refuses contract 2 today.
* A contract-2 client always opens with `version` at contract 1 (every daemon understands it), reads
  `contracts`, and uses contract 2 only when it is listed. Against a 1.x daemon, `cgpctl` therefore keeps
  working for `status`, `host`, `start`, `stop`, `watch`, `prune` (all contract 1) and fails `report` and
  `config show` / `config set` / `config reset` with exit 3, cause `contract-unsupported`, naming the daemon version that provides them.
* **How 1.x clients are told.** A 1.x client (run-gate's `_wire_args`, the 1.x in-image `ctl`) that sends
  `report` to a 2.x daemon at contract 1 receives a normal contract-1 error, never a silent drop:
  `{"ok": false, "contract": 1, "error": {"code": "report-removed", "message": "the daemon no longer
  renders reports; export the session with contract 2 and render locally (cgpctl report <session>)"}}`.
  `report-removed` is a new additive error code; a 1.x client that does not know it treats it like any
  unknown error code (exit 2 over the old carrier). Nothing else changes for contract-1 callers, which is
  why run-gate's socket-only change (RG-next) ships against 1.x and 2.x alike.
* Contract numbers are never reused. A future contract 3 follows the same rule: the daemon serves the
  previous contract for one major version after introducing the next.

### 10.2 Removed operations and carriers

* `report` (contract 1 only, answered with `report-removed` as above). The error codes `report-unavailable`
  and `report-failed` and the 2-job report bound stay documented for 1.x; a 2.x daemon never produces
  them. The daemon holds no pandas, plotly or matplotlib and runs no report subprocess.
* The `exec` carrier. The daemon has no in-image client and no `docker exec` entry. A caller without
  access to the socket has no fallback by design; `cgpctl doctor` names the remedy.

### 10.3 `export`

Streams the stored parts of a finished session so the caller can render a report locally.

```json
{"verb": "export", "args": {"session": "s-20261104T101500Z-9f01"}, "contract": 2}
```

| `args` key | type | required | notes |
|---|---|---|---|
| `session` | string | yes | a finished session id; a live session is `session-live`, an unknown one `unknown-session` |
| `parts` | array of string \| null | no | subset of the names below; `null` or omitted = every part the session has |

Parts: `summary.json`, `samples.jsonl.gz`, `damon.jsonl`, `events.jsonl`. A part the session does not
have is omitted from the header, never an error. Binary-safe framing, because `samples.jsonl.gz` is gzip:

1. one **header** line: `{"ok": true, "contract": 2, "session": "...", "parts": [{"name":
   "summary.json", "bytes": 1234, "sha256": "<hex>"}, ...], "total_bytes": 123456, "owner_uid": 1003}`;
2. zero or more **data** lines per part, in header order: `{"part": "samples.jsonl.gz", "seq": 0,
   "data": "<base64>"}`, at most 49,152 raw bytes per line (65,536 base64 characters), `seq` counting from 0
   per part;
3. one **end** line: `{"event": "end", "status": "complete"}`.

The caller verifies each part's byte count and sha256 against the header; a mismatch is the caller's
failure. If the daemon cannot finish the stream (the session directory vanished, a read error) it sends
`{"event": "end", "status": "aborted", "error": {"code": "export-failed", "message": "..."}}` and
closes, and the caller discards the partial output. A header-less refusal is the ordinary one-line error
response.

Bounds and rules:

* `export` is the second streaming verb: `STREAMING_VERBS = {watch, export}`. It has no overall
  deadline, but a stalled reader is cut after 25 s without progress. At most 4 exports run at once
  (separate from the 32 `watch` streams); beyond that, `busy`.
* `total_bytes` over the configured `export_max_bytes` (default 256 MiB) is refused before any data with
  `export-too-large`, naming the size and the key. The session is untouched.
* Owner check: only the session's recorded owner uid or uid 0 may export (section 10.5); anyone else gets
  `peer-refused`. Sessions with no recorded owner (recovered from a 1.x store) are uid 0 only.
* Read-only: the daemon takes no lock on the session beyond a read, and a retention pass skips a session
  that is being exported.
* The streamed bytes are the files on disk, so no schema is added: `summary.json` keeps the Summary
  schema v1.1 of `RG55-INTERFACE-CONTRACT.md` section 3.

### 10.4 `status` and `start` additions, and `config`

All additive within contract 2; contract-1 behaviour of the same verbs is unchanged.

**`status`** gains `args.state`: `"live"` (default, the contract-1 behaviour), `"done"` or `"all"`. Each
entry of `sessions` gains `owner_uid` (integer or `null`), `state` (`live` | `done` | `aborted`), and for
non-live entries `ended_at` and `scope`. `status` with a `session` is unchanged. Sessions are returned
newest first and capped at `keep_sessions`.

**`start`** gains target kinds beside `containerid:<64hex>`:

* `cgroup:<absolute cgroup path>`: a cgroup under `/sys/fs/cgroup`, canonicalized by the daemon, no `..`
  component, must exist and be readable;
* `slice:<name>.slice`: expanded by the daemon like the observed-slice names.

Both use the new `scope` value `observe`, which means read-only observation: `place`, `memory_high`,
`memory_max`, `cpu_weight` and `on_stall=kill` are refused with `bad-policy` (`kill` is only defined for
exact gates-slice containers). Every other `start` argument keeps its meaning. A `scope` other than
`observe` with a non-`containerid:` target, or `observe` with a `containerid:` target, is `bad-argument`.
The manifest records `owner_uid` for every session (section 10.5).

**`config`** (get; open to every authorised peer):

```json
{"verb": "config", "args": {}, "contract": 2}
```

Response: `{"ok": true, "contract": 2, "config": {"file": "/etc/cgprofile/cgpd.toml", "digest":
"sha256:<hex>", "loaded_at": "...", "reloads": 0, "last_reload_error": null, "overrides_file":
"/var/lib/cgprofile/overrides.toml", "overrides_ignored": 0, "keys": {"<key>": {"value": ..., "source":
"default" | "file" | "override", "reloadable": true, "tunable": true, "persisted": false, "shadows":
{"value": ..., "source": "file" | "default"}}}}}`. `shadows` is present only for an override. The key set
is `CGPCTL-SPEC.md` section 3.2; the precedence and the runtime-tunable allow-list are its section 3.5.

**`config-set`** (write; uid 0 only, section 10.5):

```json
{"verb": "config-set", "args": {"key": "allow_uids", "value": [1003, 1004], "persist": true}, "contract": 2}
```

| `args` key | type | required | notes |
|---|---|---|---|
| `key` | string | yes | a key on the runtime-tunable allow-list; any other key is `bad-argument` whose message lists the tunable keys |
| `value` | JSON value of the key's type | yes | typed on the wire (a number, integer, `"on"`/`"off"` string, string, or array), validated with the same schema function the config file uses; a wrong type or range is `bad-argument` naming the rule |
| `persist` | boolean | no | default `false`: memory only. `true` also writes `/var/lib/cgprofile/overrides.toml` before answering |
| `dry_run` | boolean | no | default `false`: validate and report the would-be change, apply and write nothing |

Response: `{"ok": true, "contract": 2, "config-set": {"key": "allow_uids", "old": {"value": [1003],
"source": "file"}, "new": {"value": [1003, 1004], "source": "override"}, "persisted": true, "applied":
true, "audit_id": "17"}}`. The daemon applies the value (atomically, with the reload swap of
`CGPCTL-SPEC.md` section 3.3) before it answers; `applied` is `false` only for `dry_run`. If `persist` was
requested and the write fails the daemon leaves the override unapplied and answers `persist-failed`.
Every attempt, accepted or refused, writes the audit line of `CGPCTL-SPEC.md` section 3.5.

**`config-reset`** (write; uid 0 only):

```json
{"verb": "config-reset", "args": {"key": "allow_uids"}, "contract": 2}
{"verb": "config-reset", "args": {"all": true}, "contract": 2}
```

`key` (one tunable key) and `all` (boolean) are mutually exclusive and one is required. Removes the
override from memory and from the overrides file. Response: `{"ok": true, "contract": 2, "config-reset":
{"reset": ["allow_uids"], "values": {"allow_uids": {"value": [1003], "source": "file"}}, "audit_id":
"18"}}`; a key without an override yields an empty `reset`, not an error.

**`version`** at contract 2 also carries `config: {"digest": "...", "last_reload_error": null,
"overrides": 0}`, so `cgpctl status` and the host-setup check read the digest and the override count
without a second call.

### 10.5 Session ownership

`start` records the peer's `SO_PEERCRED` uid in the session manifest as `owner_uid`. Then:

| Operation | Allowed to |
|---|---|
| `status`, `watch`, `host`, `version`, `config` | every authorised peer (group plus `allow_uids`) |
| `stop`, `export` | the session's `owner_uid`, or uid 0 |
| `gc` | every authorised peer; removes only sessions the peer owns, uid 0 removes any |
| `config-set`, `config-reset` | uid 0 only, including when `allow_uids` is empty; the access list itself (`allow_uids`) is therefore changeable only by uid 0 over the socket |

Anything else is `peer-refused` with a message naming the owner uid. Unreadable credentials fail closed
for these operations even when no `allow_uids` is configured. This rule is hygiene, not a security
boundary: on an mdt host the ctl group is the Docker relay group and therefore root-equivalent, which the
product documentation states plainly. `on_stall=kill` keeps its separate, stricter rule (exact
gates-slice container cgroups only, section 3). Contract-1 callers are subject to the same ownership
rules: run-gate starts and stops its sessions as one uid, so nothing changes for it.

### 10.6 New error codes

Additive at contract 2: `report-removed` (also answered at contract 1), `session-live` (`export` of a
live session), `export-too-large`, `export-failed` (only inside an aborted stream end line),
`persist-failed` (`config-set` with `persist` could not write the overrides file; nothing was applied). `peer-refused`
gains the ownership meaning of section 10.5. `busy` also covers the 4-export bound.

### 10.7 Goldens

Package 3 adds, under `tests/fixtures/rg55/socket/`: `export-request.json`, `export-response.ndjson`
(header, one data line per part, end), `config-request.json`, `config-response.json` (with one `override` entry),
`config-set-request.json`, `config-set-response.json`, `config-set-refused-nonroot-response.json`,
`config-set-persist-failed-response.json`, `config-reset-request.json`, `config-reset-response.json`,
`report-removed-response.json` (a contract-1 request answered by a 2.x daemon) and contract-2 variants of
`version-response.json` and `status-response.json`. The existing contract-1 goldens stay byte-for-byte
as the 1.x compatibility oracle.
