# Changelog

## [Unreleased]

- **Fixed (P-hostobs-06b):** the serving core has no production panic in `accept`, and frame-level tests now cover the D-W53 shared-uid Query path, observe's level/uid caps, and D-W52's clamped `current` size refusal; retired JSON block-level cells are gone.
- **Changed (P-hostobs-07, `HOB-7`):** `IncarnationRegistry` now stores only live incarnations (O(live + retired · log live) `end_round`, plus one O(`max_active`) reverse-index rebuild after a round that retires, about 3 MiB at the 65,536 default; model-owned default `dict_active_max` 65,536, configurable hard bound with typed `RegistryFull`, and no live eviction); store's dictionary re-uses that default.
- **Added (probe sampler):** `hostobs-sampler` walks the configured cgroup tree under the kernel/policy bounds, publishes immutable quality-bearing `Tick`s, coalesces wakeups through eventfd, and reports walk cuts through `probe.cgroups_skipped`.
- **Added (probe live backend):** `ProbeBackend` answers `current`, `status` and `identity` from one published tick per turn, with selector de-duplication, row budgets, zero-row success and no I/O or publication lock in the backends.
- **Changed (kernel clocks):** `SystemClock` reads absolute `CLOCK_MONOTONIC` and `CLOCK_BOOTTIME` through rustix, and `ManualClock::suspend` advances boot time without moving monotonic time.
- **Changed (probe epoch):** the process epoch is the `CLOCK_BOOTTIME` nanosecond value at start rather than a value persisted beside the store, so nothing is persisted and a restart in one boot still gets a greater epoch.
- **Added (probe metrics):** `probe.cgroups_skipped` (gauge) and `probe.ticks_skipped` (counter) are daemon-scope metrics on the root row.
- **Build (P-hostobs-08):** rustix 1.1.6 (`std`, `event`, `net`, `time`, no default features) is a workspace dependency of `kernel` and `probe`. The probe's unused `ruzstd`, `serde`, `serde_json` and `toml` are gone. The Rust lanes and `tools/dev` run on `tester-unified-rust(-dev):rs-20261124`, which carries the new lock's registry set; `rs-20261118` stays tagged for rollback.
- **Docs (P-hostobs-19, closes `HOB-74`):** `docs/CONSUMERS.md` gets a "Boundaries" section with the four consumer rules (cgprofile frozen, span emission is one JSON line on the observer socket, act never writes `io.cost`/`io.weight`/sysctl, iogovmon reads hostobs only after M3); cgprofile's freeze is filed as `CP-66`.
- The hostobs backlog now lists only open items; fixed and closed entries are archived one per line in `archive/2026-Q4.md`, with HOB-62..74 filed and HOB-49's WIRE citation corrected.
- **Changed (WIRE.md, P-hostobs-03, `HOB-65`/`HOB-71`/`HOB-72` spec):** no request waits (D-W49, D-W50; D-W30 retired), JSON mode only at observer/control (D-W51), probe-wide keys with `max_rows` 1,024, Query 1/4 MiB, `max_block_bytes` 1 MiB and history 2/8 (D-W52), no per-uid pools and per-uid caps equal to the level caps at observe (D-W20, D-W53).
- **Changed (probe serving, P-hostobs-05, `HOB-65`):** `current` is answered in its read turn or refused before REP with `too-many-requests` while its connection's Query blocks are unwritten, or `budget {"pool":"query"}` when the Query pools lack room; pending requests, retry, re-resolution and `SendHub::query_ready` are removed.
- **Changed (probe serving, P-hostobs-06, `HOB-71`):** `tick_ms`, `max_rows` 1,024, `max_block_bytes` 1 MiB, Live 4/32 MiB, Query 1/4 MiB and history concurrency 2/8 are probe-wide; `max_block_bytes` is clamped to both connection pools.
- **Changed (send hub, P-hostobs-06, `HOB-71`):** per-uid Live and Query pools are removed; the same-uid multiple-connection and query-backpressure cases now exercise only connection and global pools, and observe's connection and selection uid caps equal its level caps.
- **Changed (JSON mode, P-hostobs-06, `HOB-72`):** observe and operator refuse a first `{` with exactly one fatal `binary-required` line at `corr` 0 and close; observer and control keep JSON mode, and both clients now treat ten codes as fatal.
- **Docs (P-hostobs-02, `HOB-66`)**: `docs/PROBE-PROCESS.md` decides the probe's process and thread model (sampler, server loop and placed writer threads; non-blocking backend rule; eventfd wakeup; rustix with one audited `unsafe` line in `kernel`; named `LISTEN_FDS`; shutdown; minimal config; in-process test harness); the hub doc no longer claims a mutex.

### Changed (P-hostobs-04 workspace unification)

- **Crates**: `model` now owns the one quality/value vocabulary and metric fact table;
  `kernel` and `store` depend on `model`, `proto` depends on `model`, and `store` depends on
  both, with deleted local copies replaced by re-exports or model types.
- **SP-2**: the kernel evidence now derives rates through `model::derive_rate` instead of a
  test-local arithmetic implementation.
- **Store payloads**: fixed frame-header fields are parsed by `proto`; the store applies its
  configured payload bound, declared length, corpus frame type, flags and reserved checks in
  their original order, with the existing typed refusals unchanged.
- **Probe**: the unused `hostobs-kernel` workspace dependency is removed; the four
  unused registry dependencies (`ruzstd`, `serde`, `serde_json`, `toml`) remain
  until P-hostobs-08 removes them with its image rebuild.
- **Docs**: the sampler source is cited by archive tag, commit and path; the README records
  the measured working dev image and remote `tools/dev` run.

### Added (P5b-2d server: tick delivery, retries, served verbs and reachable §11.4 cases; `HOB-54` stays open)

- **`bins/probe/src/server.rs`**: `Server::on_tick(now_ns)` pulls one shared delivery set from the
  registry and sends every push or stream-ending delivery through `Server::drive`. `wrote` and
  `poll` now finish with the pending-`current` retry pass over connections and correlations in
  ascending id order.
- **`bins/probe/src/verbs.rs`**: `current`, `subscribe` and `unsubscribe` are served at observe and
  operator, so `hello.verbs` lists `hello,status,current,subscribe,unsubscribe` there. JSON mode
  keeps `current` and `subscribe` at `binary-required`; `unsubscribe` is served and answers
  `not-found` for an unknown subscription.
- **Tests**: server-level shared-tick, joiner-generation, interval, failed-stream and retry-order
  cases; REQ/BLOCK/END sequences for all three verbs; the reachable §11.4 connection cases for
  subscription idle, dribbling readers, half-close, corr reuse and `too-many-requests`; and a
  2,000-turn hostile run over four slots and two uids whose modelled client decodes every BLOCK,
  applies `StreamDicts`, and checks every END count.
- **Docs and backlog**: WIRE §4.3/§5.4/§11.4, the live-stream and server design notes, and
  `HOB-54`; the backlog row remains OPEN because P5b-3 and P5b-4 remain.

### Added (P5b-2c connection: subscribe lifecycle, current, outstanding requests and Draining; `HOB-54` stays open)

- **`bins/probe/src/conn.rs`**: `current` now resolves, bounds, encodes and queues a dictionary-bearing
  Query block in REP/BLOCK/END order; Query backpressure records only the selection and needed length,
  and `retry_pending` re-resolves the latest tick when room appears. `subscribe` applies the
  per-connection, uid and probe budgets, returns its REP without resolving a tick and waits for the
  first due stream block. `unsubscribe` removes the registry entry and ends the stream with exact
  pinned-block/unpinned-drop counts. Clean client EOF enters Draining, ends every subscription in
  `corr` order with `client-eof`, then finishes outstanding requests before closing silently.
- **State guards**: `subs` and `pending` participate in `corr`-in-use, `max_outstanding`,
  `subscriptions()`, idle suppression and every push path. All new pushes stop immediately after the
  connection closes; `end_subscription` on a closed connection only removes the connection-side key,
  leaving registry release to `Server::finish`.
- **`bins/probe/src/server.rs`**: the server owns `SelectionStreams` built from unified stream bounds;
  observe/operator now share `tick_ms`, `max_rows` and one `max_block_bytes`, clamped to the smallest
  Query and Live pool cap of both levels. `drive` passes the registry, `on_eof` uses `drive`, `finish`
  releases the connection's registry entries before removing its hub state, and `Server::streams()`
  exposes the counters. `status` takes `dropped_blocks` from the hub and ignores the backend's field.
- **Tests**: the P5b-2c section 5.3 cases cover `current`, subscribe, unsubscribe, outstanding,
  corr-in-use, Draining, idle, server-ended stream and status behavior. The close-path matrix includes
  real connection-side subscriptions for slow-client, shutdown, fatal request, frame-too-large, request
  deadline, Draining completion and driver finish, then checks registry, hub and connection counters.
  `Conn` sequences are parsed by the Rust client's REP/END checks and block dictionary state. The
  2,000-step stream-operation hostile run checks registry and hub exactness after termination.
- **`WIRE.md`**: the `current` pre-REP resolution/size rule and pending retry, subscription ownership
  and next-tick delivery, unsubscribe counts, Draining completion, outstanding accounting, unified
  block bounds, idle/Draining rows and decisions D-W29/D-W30.

### Added (P5b-2b data side: backend seam, tick encoding, registry and hub additions; `HOB-54` stays open)

- **`Backend`**: `TickData` (server-owned header fields separated from backend facts), a deterministic
  `TickData::example`, `BackendError::{Unavailable, TooManyRows}` and
  `Backend::current(&Selection, max_rows)`; `FixedBackend` answers from a canonical-selection map and
  models the stop-at-`max_rows + 1` contract. `StatusView` forwards `current`.
- **`tick.rs`**: full S16 validation, position-preserving dictionary bodies with every `intern` error
  mapped to `internal`, a checked arithmetic frame-length preflight, canonical bare encoding, the
  infallible dictionary splice, the generation-1 `current` block and a `LiveTick` whose dictionary form
  is built once. Oversize is judged before `encode_bare`.
- **`registry.rs`**: shared canonical selection streams, per-connection/subscription and per-uid
  accounting, requesting-level uid/probe caps, `SelId` monotonicity, interval scheduling,
  generation/form lifecycle, backend-unavailable skip, row/internal termination and deterministic
  delivery order.
- **`hostobs-proto` hub**: `SendHub::dropped_blocks`, `SendHub::query_room` and the Query-pin fix that
  stamps 0 without consuming a reused Live stream's pending drops.
- **Tests and fixtures**: every P5b-2b section 5.2 case, including S16 intern refusals, 4,096-row
  encode equality, all registry caps/schedules/failures, a 2,000-step registry hostile run, hub
  drop/room tests and the twentieth fixture `hub-query-pin-after-corr-reuse`. `assay.toml` judges the
  two new source files.
- **`WIRE.md`**: M2 END failures, hub drop accounting and Query room, Query pin behavior, join-cap
  precedence and the generation lifecycle.

### Added (P5b-2a request side: canonical selections and the three request schemas; `HOB-54` stays open)

- **`bins/probe/src/selection.rs`**: the checked canonical `Selection` stream key, with the 16-entity,
  64-metric, 512-byte path/prefix and 64-byte metric-name bounds; the derived order sorts inc before
  path before prefix before all, then by value. `Selection::new` re-checks every decoded content rule,
  sorts both vectors and refuses duplicates.
- **`args`**: `Obj::elements` returns each array element's JSON `Kind` before any decoder sees its bytes;
  `selection`, `current`, `subscribe` and `unsubscribe` implement the strict section 4.2, 4.6 and 4.7
  schemas, including decoded-duplicate detection and non-string element refusal.
- **`limits` and `classify`**: `IntervalRule` accepts discrete unrounded intervals; `max_uid_selections`
  and `max_selections` are 4 and 8 at block levels and 0 elsewhere without joining `hello.limits`;
  `ConnFacts` and `classify_with` implement `corr` in use and the BLK-only outstanding cap; `Current`,
  `Subscribe` and `Unsubscribe` join `Call`, with compile-only `internal` arms in `Conn`.
- **`codes` and `encode`**: budget `Pool`/`Detail::Pool`, `EndReason`, `end_ok`, `end_error`,
  `current_result`, `subscribe_result` and `UNSUBSCRIBE_RESULT`.
- **Tests and hostile runs**: every P5b-2a selection, argument, interval, classify, code, limit and
  encoder case in design section 5.1, plus 2,000 random and 2,000 mutated-valid request-argument runs.
- **`WIRE.md`**: the selection decode and canonical rules, request precedence steps 2 and 10, BLK-only
  outstanding refusal and the no-rounding interval rule. `assay.toml` judges the new file.

### Added (P5b-1b part 2: the `Server` layer; `HOB-54` stays open)

- **`bins/probe/src/server.rs`**: `Server<B: Backend>` owns ONE `SendHub`, the conns (iterated in
  `ConnId` order), the per-level and per-(level, uid) counters, the per-level `Limits`
  (`ServerLimits`) and the backend. `accept(level, peer, now)` answers `Accept::Accepted(id)` or
  `Accept::Refused` (Control, a missing peer, a full level or a full uid: nothing counted, no hub
  entry, no byte; accept when `count < cap`); `on_bytes`, `on_eof`, `poll` (R20: every conn's timers
  in id order, then the hub's `slow-client` closes, then a sweep), `output`, `wrote` (delegates to
  `Conn::on_written`), `take_final`, `is_closed` (true for a gone id), `shutdown` and `finish`, the
  single idempotent release path (R22): level and uid counters, a zero uid entry, the hub entry, the
  conn. `poll` and `shutdown` finish the closed conns that hold no final bytes. The backend handed to
  the conns fills `status.clients` per level (`observe`, `operator`, `observer`, zeros included). The
  hub's caps are the widest of the levels (`ServerLimits::queue_limits`); the observer's 320 KiB
  control queue is enforced by the server after every `on_bytes` (`slow-client`), because the hub has
  one `control_queue_bytes` for all levels.
- **`Limits::queue_limits`** (the hub caps of one level: per-uid pools a quarter of the global ones)
  and **`Conn::has_final`**.
- Tests: one per §5.1 accept row, R22 counters, `uid_pools`, `global_pools` and hub back to zero after
  each of ten close paths at every level, `finish` idempotence, R20 ties (hello deadline against the
  stall on the unread prefix) and id order, shutdown with several conns, byte-by-byte interleaving of a
  binary and a JSON conn against a one-shot feed, `status` clients per level, the observer control cap
  and 2,000 deterministic hostile steps over three slots.

### Added (P5b-1b part 1: the sans-IO connection state machine; `HOB-54` stays open)

- **`bins/probe/src/backend.rs`**: the `Backend` trait (`identity()`, `status()`), `Identity`,
  `StatusInfo` (+ `TickInfo`, `TierInfo`) and `FixedBackend` for tests and the P5b-4 bring-up.
- **`bins/probe/src/conn.rs`**: `Conn` (`Undecided`, `AwaitHello`, `JsonNoHello`, `Ready`,
  `Draining`, `Closed`): mode detection (R24), the negotiated prefix as the first queued frame, binary
  and JSON request reading, `classify` dispatch, `hello` and `status` replies, the final frame held
  outside the hub (R1) with the prefix guarantee (R2), the EOF rules (R10), the timer order (R20) and
  the idle condition; `max_outstanding` and corr-in-use stay P5b-2. Row tests `conn_row_*`, R1/R2
  tests, byte-by-byte delivery and 2,000 deterministic hostile runs.

### Added (P5b-1a: the probe's pure serving functions; `HOB-54` stays open)

- **`bins/probe/src`** gains seven modules, none of which does I/O, reads a clock or keeps connection
  state (design `docs/P5B-SERVER-DESIGN.md`): `limits` (levels and the WIRE.md §5.1 bounds per level; the
  observer `control_queue_bytes` is derived from `max_reply_bytes` 16 KiB as 4 * 16 KiB + 256 KiB =
  320 KiB, design R28), `verbs` (the §4.3 table as const data), `codes` (the §10 codes with fixed messages
  and the bounded `detail`, R14), `args` (the strict request-argument reader on `proto::envelope`, R4 and
  R14 to R17, with the `hello` and `status` schemas), `classify` (the request precedence function: frame
  type, `corr` 0, `check_req`, the hello gate, table, level, a second `hello`, JSON `binary-required`,
  served, then the argument schema and the `hello.proto` rules; R6 to R12), `encode` (the prefix, control
  frames, error envelope and JSON error line, reply cap, `closing` EVT; R14, R25) and `lines` (the JSON
  `LineReader`: line bound, deadline stamp per line, linear scan, bounded capacity; R13).
  `hostobs-probe` now depends on `hostobs-proto`.
- **Tests**: unit tests per module (one per precedence step and per tie between two steps in `classify`,
  the JSON `corr` recovery of R12, every `detail` mapping, the §5.2-style `LineReader` table) and
  `bins/probe/tests/serving_hostile.rs`: 2,000 random + 2,000 mutated-valid deterministic runs through
  `classify`, `args` and the encoders (closed set of refusal codes, exactly the nine fatal codes fatal,
  every encoder output re-parses with the `proto` checkers) and random line streams through the
  `LineReader` (bounded capacity, linear scan, poisoning). `assay.toml` judges the nine `bins/probe/src`
  files (53 judged files together with the 44 before); this package was written without a compiler and
  is verified by the remote lane.
- **`WIRE.md`**: §4.2 request precedence and the digits-only integer rule, §4.4 `hello.proto` equals the
  client's own prefix, JSON-mode negotiation and the block keys reported as 0, §8.1 line rules and JSON
  `corr` recovery.

### Added (the hand-run `assay-r2` lane; assay B245 U8b)

- **`assay.toml` `[lanes.assay-r2]` and `run-gate.toml` `[lanes.assay-r2]`**: Rust R2 (mutation) of the changed
  lines of the five judged crates by ingesting cargo-mutants' report (`cargo-mutants-outcomes`, `--in-diff`
  from `{assay.changed_diff}`, `--jobs 1`, nextest, output `assay-mutants-out`, git-ignored). Hand-run only
  (`run-gate assay-r2 --base <ref>`), `budget = "4h"` is an estimate, never part of a default gate; assay K15
  was open until the first real hostobs run (2026-11-18, 345 mutants in 17 minutes: 271 caught, 21 missed, 47
  unviable, 6 timeouts; closed by that evidence).
- **`assay-r2` memory cap**: the first real run (2026-11-18) reached 6.4 GiB in the container within 90 s
  on the shared 8 GiB host and was cancelled, and a second attempt (cargo and test threads capped at 1) reached
  4 GiB (both figures are from the controller's monitoring, not recorded in files); a sampled diagnostic run
  named the cause: ONE test process of a cargo-mutants MUTANT run of the client crate (`client.rs` differs from
  git; a hang mutant), `client_scripted ... transport_failures_are_classified`
  (`crates/client/tests/client_scripted.rs`), grew 200-400 MB/s (the unmutated test passes in
  `selftest`/`assay-r1`). The client crate's test double now panics after one million scripted calls
  (`e2efb1c55`), which bounds such a runaway (in the real run the two client-crate hang mutants,
  `client.rs:623` `read_prefix` and `client.rs:706` `read_some`, still ended as 120 s timeouts, but without
  growing memory beyond the cap). The lane now sets `CARGO_BUILD_JOBS=1`, `RUST_TEST_THREADS=1` and `NEXTEST_TEST_THREADS=1`, and
  `run-gate.toml [lanes.assay-r2.resources] memory = "2500m"` with `memory_swap = "2500m"` (docker
  `--memory`/`--memory-swap`, memory plus swap equal): a runaway is OOM-killed inside its own container and the
  lane ends non-green (ERROR or FAIL, not yet observed) instead of spilling into the host's swap and taking
  neighbours down. The next run measured it: the full lane (baseline plus 345 mutants, 17 minutes) completed
  inside the cap with no OOM kill (`crashed 0`, normal exit); no memory peak was recorded.

### Added (the `conformance` lane; `HOB-4` fixed)

- **`run-gate.toml` `[lanes.conformance]`**: the Python reference decoders, fixture regeneration checks
  and `hostobs_client` tests (`python3 -B -I -W error::ResourceWarning -m unittest discover -s
  python/tests`) on `tester-unified-rust`, `network = "none"`, budget 10 minutes, not judged by Assay.
  The image's `python3` is Debian's apt package (CPython 3.13.5 on Debian 13), so the WIRE.md §7.8
  absolute memory bound (measured on CPython 3.14.10) is skipped there and the growth and line-count
  gates run. `tester-unified-rust/tests/test_image_contract.py` expects the fourth lane.

### Added (P5a: `crates/client`, the Rust client; the Rust client part of `HOB-54` closed)

- **`hostobs-client`**: `Client::connect(path, Level, ClientInfo)` and `Client::from_transport` over an injected
  `Transport` (a `UnixStream` by default) and `Clock`; the handshake of §1.4 (prefix alone, server prefix, `hello`
  with `corr` 1) with the Python client's `hello` validation (`HelloResult`, `Limits`); `request(verb, args_json)`
  with local §4.2 checks; `blocks()` (`BlockStream`, END counts checked); `subscribe()` (`Subscription`, an iterator
  with `dropped()`); `reconnect()` per §6.4 (`Session` first/same/changed); `close()` (idempotent, also on drop).
  `ClientError` is a closed enum with `kind()` and `Display`; no panic on any server byte.
- **`proto`**: `envelope::members_up_to(bytes, max)` is `members` with the key bound chosen by the caller (`members`
  is `members_up_to(bytes, MAX_MEMBERS)`; every envelope check is unchanged): the client reads the 9-key `hello` result
  and its 19-key `limits` object with a bound of 64.
- **Review round**: a stream's frames are re-checked against the announced `max_frame_bytes` after the hello; an
  `END ok:false` counts its `dropped`; socket timeouts, the frame-age stamp, reconnect state and transport error
  classes are asserted in tests; the hostile-bytes test asserts a closed set of error kinds.
- **Tests**: scripted-server tests over a fake clock (`crates/client/tests/client_scripted.rs`: handshake refusals,
  every §10 code, corr and END violations, fixtures of `fixtures/stream/client-*.json` through a live client,
  exact-deadline and completion-age boundaries, byte-by-byte delivery, reconnect, a real `UnixStream` pair and
  2,000 + 2,000 deterministic hostile-byte runs) and unit tests in every module. `assay.toml` judges the seven
  `crates/client/src` files (44 judged files together with the 37 of P2b); `crates/client` joins the judge's
  `source_roots`.

### Added (P2b: the send hub; `HOB-54` stays open)

- **`proto::hub::SendHub`** (WIRE.md §5.3, §12): `QueueLimits`, `ConnId`, `add`/`remove`/
  `drain_unpinned`, `push_control`/`push_live`/`push_query`, `query_ready`, `end_stream`, `head`
  (pins and stamps `corr` and `dropped` into a private prefix)/`head_partial`/`wrote`, `poll`,
  `queued`/`uid_pools`/`global_pools`, `TickForms`, `Close`, `LiveOutcome`, `QueryOutcome`,
  `StreamCounts`, `Pools`. Std-only; one unit test per §5.3 operation row and consequence and a
  pool-exactness checker over a pseudo-random run. The decisions the tables leave open are the
  `Implemented` paragraph of §5.3.
- **Hub fixtures** (WIRE.md §11.4): 19 `fixtures/stream/hub-*.json` with hand-derived
  expectations, written by `python/reference/make_hub_fixtures.py`, pinned byte for byte by
  `python/tests/test_hub_fixtures.py` and replayed by `crates/proto/tests/hub_fixtures.rs`.
- **Stall clock.** A push restarts `last_progress_ns` only when the queue was empty before the
  push; a coalesce or a shed that empties the queue does not restart it, so a socket that
  accepts nothing is closed after `stall_ms` however often a tick replaces the queued one.
- **Drain, ids, first push.** `drain_unpinned` of a dictionary-bearing Live tick sets
  `needs_dict` for its stream (the tick is still not counted); a `ConnId` is never 0; the first
  push of a stream is the dictionary form whatever generation it carries.
- **Gates.** `crates/proto/src/hub.rs` is a declared `assay-r1` target (37 judged files together
  with `envelope.rs` and `stream_dicts.rs`).
- **Hand mutants.** `M1d-14`'s Python patch follows the directory guards `hostobs_wire_ref.py`
  has since the P2a1 codec (two patches, one per guard): 54 stored patches, `--dry-run` 0 stale.
- **Hand-mutant survivors closed** (`M1d-22`, `M1d-15`; the first 2026-11-18 run, on
  `0dd7230b1`, killed 50 of 54; the re-run on `6fa50236f` killed 54 of 54): two store tests
  rescan a tier and read the eviction report from the store's ring after a write-path eviction
  and after `Store::evict` (also kills the `Store::notes` ring patch), and the quarantine
  look-alike test asserts the reason of a `.corrupt` directory. None of the four was an
  equivalent mutant at the unit level (through the public API the ring is redundant, `HOB-60`).

### Added (P3b: `python/hostobs_client` connection, `require_profiling`, `hostobs-mark`; the P3 part of `HOB-54` closed)

- **Review round**: the envelope text checks are linear on hostile input (parse first, count depth only on parsed
  text); `Connection.open` validates before it connects (no socket leak, `timeout_s` finite and bounded); `hello`
  integers are exact and `epoch` is a `u64`; hostile `hello` limits are `hello-mismatch`; the query queue is also
  bounded in bytes (64 MiB); `hostobs-mark --timeout` rejects `inf`/`nan`; exact-boundary, corr, queue and table
  tests (`test_client_hardening.py`).
- **`hostobs_client.conn`**: `Connection.open(path, level, *, client, timeout_s, clock)` (binary handshake at
  minor 1), `.request()`, `.blocks()` (REP at call time, END counts checked), `.subscribe()` (dropped counters,
  newest-8 queue), `.reconnect()` (`session` first/same/changed per §6.4), `.close()`; the §7.3 receive loop on one
  socket and one `FrameReader`, no threads, every deadline from the injected clock; `OperationTimeout`,
  `ProtocolError`. **`hostobs_client.envelope`**: REP/END/EVT shape checks (the byte rules of `envelope.rs`, plus
  duplicate keys at any depth) as `EnvelopeError`.
- **`hostobs_client.profiling`**: `require_profiling(level, required)` and the five plan D.2 states
  (`absent`, `unconfigured`, `unreachable`, `refused`, `lost`) plus `available`. **`hostobs_client.mark`**:
  `hostobs-mark` (`python3 -m hostobs_client.mark`), JSON mode on `HOSTOBS_OBSERVER_SOCKET`.
- **Tests**: `python/tests/test_client_{conn,path,zipimport}.py` on a scripted fake probe (`fake_probe.py`,
  socketpair stepped by hand, no sleeps; the one server thread is in `test_client_path.py`), a zipimport smoke
  test, and the stdlib-only test now also forbids threads and any clock use outside a default argument.

### Added (P3a: `python/hostobs_client` codec, reader, stream dictionaries and rates; `HOB-54` stays open)

- **`hostobs_client.wire`** (WIRE.md §7.4): `decode_block(buf, held) -> Block`, `Dictionary`, `Column`,
  `WireError(kind, detail)` (the 46 decoder kinds), `ServerError`, `ConnectionLost`. Explicit `<` struct
  formats, one cached directory `Struct` per `n_sec`, memoryview casts on little-endian hosts (one swapped
  `array.array` copy per accessed array on big-endian), quality validity by `bytes.translate`, row
  references by `max` and a per-generation kind map, and a retained previous rows view that skips both
  row checks on byte-identical entity references. Reproduces the 70 `fixtures/wire` cases (kinds and
  fields) and agrees with the Python reference on a seeded byte-flip fuzz.
- **`hostobs_client.stream`**: `FrameReader` (the §5.2 machine; §7.3 buffering through `fill()` and
  `advance()`, `feed()` on top) replaying the 12 `reader-*` cases, `StreamDicts` (§6.2) replaying the 6
  `client-*` cases. **`hostobs_client.rates`**: `rate(prev, cur, nominal_interval_ns)` on
  `fixtures/rates/rates.json`, integer delta first, divide last.
- **Tests and measurements**: `python/tests/test_client_{wire,stream,rates,stdlib_only,budgets}.py` (the
  §7.8 gated settrace-line-count and tracemalloc bounds) and `python/bench_decode.py` (CPU, not gated; numbers
  in `docs/SP-4-WIRE-SIZES.md`).

### Added (P2c: client dictionary state and envelope checks; `HOB-54` stays open)

- **`proto::stream_dicts::StreamDicts`** (WIRE.md §6.1, §6.2, §6.4, §6.5): one dictionary per stream
  `corr`; `held`, `accept`, `close`, `reset`, `stream_count` and `on_hello(boot_id, epoch) ->
  Session::{First, Same, Changed}`. A dictionary-bearing block must exceed the held generation (nothing
  held is generation 0, so a first block at generation 0 is refused too) and a bare block must carry
  exactly the held one; `accept` forgets the stream's dictionary on any error.
  `ProtoError::GenerationRegressed { held, got }` (`generation-regressed`) is the new variant (46).
- **`proto::envelope`** (§2.2, §4.1, §4.2, §8.2): byte-level `check_req`, `check_rep`, `check_end` and
  `check_evt` without a JSON dependency, returning byte ranges; text rules (UTF-8, no BOM or trailing
  byte, escapes, number grammar, depth 8), envelope-level duplicate keys, key sets and types, JSON-mode
  `corr`. Both modules are `assay-r1` targets (36 judged files).
- **Fixtures.** `fixtures/stream/client-*.json` (6 cases, written by
  `python/reference/make_client_stream_fixtures.py`, replayed by `crates/proto/tests/client_stream_fixtures.rs`
  and checked by `python/tests/test_client_stream_fixtures.py` with an independent replay); the §11.4 format
  gains the `hello` step and the `ok` and `session` outputs.

### Added (P2a2: the stream reader, mode detection and connection timers; `HOB-54` stays open)

- **`proto::stream::FrameReader`** (WIRE.md §5.2): `feed(bytes, now_ns) -> Feed { frames, error }`,
  `poll(now_ns)`, `eof()`. A frame completed before an error in the same call is returned with it; the
  age runs from a frame's first byte and fires strictly after `completion`; a body buffer grows with
  the received bytes (capacity at most `min(len, received + 64 KiB)`, never from `len` alone, and an
  oversized or malformed header is refused with a 12-byte buffer); an error poisons the reader; a feed
  after a clean `eof` is `trailing-bytes`. `ProtoError::Deadline` (`deadline`) is the new variant (45).
- **`ModeDetect`** (§5.3, `Detect { Need, Json { at }, Binary { prefix, at }, Unknown }`) and
  **`ConnTimers`** (§5.4, `Timer { Hello, Idle }`; `ConnTimers::new(hello_ns, idle_ns)` takes the
  level's two durations). `SendHub`, `StreamDicts` and `envelope.rs` are not part of this change.
- **Fixtures.** `fixtures/stream/reader-*.json` (12 cases, written by
  `python/reference/make_stream_fixtures.py`, replayed by `crates/proto/tests/stream_fixtures.rs` and
  checked by `python/tests/test_stream_fixtures.py` with an independent replay). The valid frames of
  `fixtures/wire` are also fed through every split point and a fixed pseudo-random splitter.
- **Gates.** `crates/proto/src/stream.rs` is a declared `assay-r1` target (34 judged files). A `len`
  above the codec's 16 MiB limit is reported against the reader's own `max_frame`; `ModeDetect::feed`
  after a decision returns `Unknown` in every build.

### Added and fixed (P2a1: wire codec and Python reference)

- **`HOB-48`: the `subject` section (kind 6) and `PROTO_MINOR = 1`.** A history block names the one
  incarnation it describes (`u32 ent | u32 0`, canonical rank between the dictionary and the
  rows/time sections); `Block::subject`, `encode_subject_body` and the Python reference carry it.
  It needs the time section (`subject-without-time`, decoder and encoder), must name an entity
  (`row-reference-not-entity`) and has a zero reserved word (`unknown-section-flags`).
- **`HOB-46`: `decode_block_with(bytes, held)`.** A block without an in-frame dictionary is judged
  against the held one (`stale-dictionary-generation`, reference, kind and mask checks); `decode_block` is
  `held = None` and behaves as before. Fixtures carry the held dictionary as a top-level `"held"` key.
- **`HOB-47`: `aggregation-not-permitted`.** Every history bucket's aggregation must be in every
  column metric's mask, in both decoders. `history-buckets` and `history-five-buckets` carry `last`
  buckets (their counter and gauge columns permit no other); the 15-byte length pin is unchanged.
- **`HOB-50`/`HOB-51`: received frames.** `SECTIONED` on a non-BLOCK frame is `unknown-frame-flags`
  (`block-as-req` has the flag clear), every section length is a multiple of 8
  (`section-length`), an unknown kind at directory entry 0 is `section-order` at index 0 (after the
  rank check), and the directory bytes are proven present before the vector is reserved.
- **Both decoders give the same error for the same bytes.** Only the first header body is decoded (a
  second header is `duplicate-section`); a non-zero pad byte in a column, time or dictionary body is
  `non-zero-reserved`, and the time body's `agg` bytes are judged before its pad; the Python
  `decode_dictionary` refuses exactly what Rust `Dictionary::decode` refuses, with the same kinds, and
  raises nothing but `WireError`; the fixed header fields are read and judged in wire order (a short
  buffer with an unknown type byte is `unknown-frame-type`); the header and the subject must carry the
  directory required flag, judged after the section version; the aggregation mask needs no dictionary
  (`held = None` included) and `encode_block` refuses `aggregation-not-permitted`; the column scan uses
  a set.
- **`HOB-52`/`HOB-53`: one outcome per defect.** A metric name repeated with other facts is
  `duplicate-dictionary-entry` in Rust, judged before the text's control bytes; the Python reference
  judges `not-a-block` before the buffer length. There are two new `ProtoError` variants (44 in all).
- **Fixtures.** `fixtures/wire` holds 70 cases (11 valid, 59 refusals): 29 existing cases and 41 new ones.
  Not part of this change: the stream layer (`HOB-54`), the `generation-regressed` rule, and `HOB-49`.

### Fixed (P0b store follow-ups)

- **`HOB-55`: a look-alike name with a huge sequence cannot poison the tier.** The store never
  mints a segment sequence above `SEQ_NAME_MAX = u32::MAX`, so a name above it
  (`hot-0-18446744073709551615.seg`, also `.seg.open`, `.seg.corrupt`, `.seg.open.corrupt`) is not a
  segment name: the Rust and Python parsers refuse it, and the scan notes it and never counts,
  adopts or deletes it or lets it raise the water mark. An unproven name never raises the mark
  above the bound minus one; a proven one (an adopted segment whose header agrees with its name)
  is adopted at the bound itself, and the sequence after the bound is the typed `LengthOverflow`.
- **`HOB-56`: `StoreStats::bytes` is the tiers' own total.** It is recomputed from the history
  tiers and the events store after every write, seal and eviction pass (error paths too), with no
  incremental add/subtract arithmetic, so a vanished file, a new segment's header and a seal's
  footer are always in it.
- **`HOB-58`: eviction reports reach the caller.** `Evicted::notes` carries the reports of an
  entry dropped because its file was already gone or could not be removed (a quarantined file that
  cannot be removed stays on disk, uncounted, and the next restart adopts it again). A tier keeps
  at most `MAX_EVICTION_NOTES` (256) of them in `scan.notes` after its rescan notes, and the store
  at most 256 in its ring, the oldest dropped first; `Store::notes()` shows them once and hides
  only one tier's "names another tier" note about another tier's file in a shared directory, so a
  note about a tier's own garbage name stays visible.
- **`HOB-57`: the M1d hand-mutation script has a per-run timeout.** `--timeout-s` (default 1800 s
  for a Rust suite, 300 s for Python); a timeout is `KILLED-TIMEOUT`, counted apart (and
  inconclusive under host load), and the container is removed by its exact name
  `hostobs-mut-<pid>-<n>` on a timeout or any exception. 53 stored patches (`M1d-20` to `M1d-22`
  added, `M1d-18` rewritten); when this entry was written the campaign had not been run (it ran
  on 2026-11-18, see the survivors entry above).

### Fixed (P0 bug fold-in)

- **`HOB-8`: `FrameHeader::parse` bounds the declared length.** It refuses a length above
  `MAX_FRAME_BYTES` (`oversized-frame`) or below `FRAME_HEADER_LEN` (`truncated`), after the
  fixed-field checks, in Rust and in the Python reference, so a streaming reader may allocate
  `len` straight after `parse`. `decode_block` keeps its own check; through `parse` it can no
  longer fire, which leaves its multi-line `return Err(ProtoError::OversizedFrame { .. })`
  uncovered (documented in `docs/HAND-MUTANTS-M1D.md`). No
  wire change. The Python reference also stops leaking a `struct.error` for a 12 to 15 byte
  block frame.
- **`HOB-43`: quarantined files are inside the cap.** A `<segment>.corrupt` file whose name
  parses to the tier and whose header is the tier's corpus is adopted: it counts in the byte
  total, `keep_s` applies to it by its name's `(start_s, seq)`, and under `cap_bytes` it is
  evicted before any live segment, oldest first. Evictions are reported
  (`Evicted::quarantined`) and counted (`StoreStats::evicted_quarantined`); no new config key.
  Look-alikes are reported and never counted, evicted or removed.
- **`HOB-42`: a sealed name must be sealed, and a name must match its header.** A non-`.seg.open`
  file without a verified footer, or whose header `seq`/`start_real_ns` disagrees with its
  name, is quarantined and reported (`SealedWithoutFooter`, `SegmentIdentity`), never served.
  The seal keeps the open name's start second. The Python segment reference refuses the same
  files; `docs/SEGMENT-FORMAT.md` states both rules.
- **`HOB-48` (length half only): the time section is 15 bytes per bucket, not 14.** A bucket is
  `i64 real_ns` + `u32 covered_ms` + `u16 samples` + `u8 agg`. The encoder wrote that, but
  `time_body_len` (Rust `frame.rs`, Python `hostobs_wire_ref.py`) computed `align8(14 * n)`,
  which agrees only up to three buckets: for `n = 4` the encoder wrote 64 bytes and the decoder
  demanded 56. Both sides now share one `TIME_BUCKET_BYTES = 15`. Tests pin the exact body length
  and the frame round trip for 0 to 9 buckets in both languages, and a new golden fixture
  `history-five-buckets` is decoded by both (with the two HOB-8 refusal fixtures
  `frame-length-below-header` and `block-without-directory`, `fixtures/wire` grows from 26 to
  29 cases: 8 valid, 21 refusals; the other 26 regenerate byte-identically). **No wire version
  bump is needed**: no
  hostobs release or tag exists, no store segment fixture carries a history block, and the only
  history fixture before this had three buckets, so no byte stream with `n >= 4` has ever been
  written by a shipped build. The `subject` section (kind 6) and the rest of `HOB-48` are not
  part of this change.

### Added

- **`tools/dev`: a developer container wrapper** (build, check, test, clippy, fmt, example, gdb,
  strace, shell, status, stop, clean) that runs in `tester-unified-rust-dev` on the gate host `v1001`
  through the controller's `hx.sh` (HEAD as a git bundle plus uncommitted changes up to 20 MiB),
  capped, network-less and unprivileged, with a pressure gate (exit 75) and a one-at-a-time rule
  (exit 3). Source and stub-tested argv only: the image has not been built. See
  `tester-unified-rust/docs/DEV.md`.
- **`docs/WIRE.md`: the normative `hostobs/1` specification** — connection and handshake,
  frame types and `corr`, the implemented block layout with offsets and the received-frame
  alignment rules, every M2-M3 verb with strict keys and worst-case request sizes, closed error
  codes, fake-clock state machines for the reader, the send hub (pin-time `dropped` stamping,
  per-stream coalescing, query backpressure, a global budget owner) and the connection timers
  (hello and idle; the request deadline is the reader's completion age), per-selection dictionary generations, deterministic `series_batch`
  packing, the efficient Python access path with deterministic gates set from measurements on
  CPython 3.14, versioning, conformance cases and the P2/P3/P5 work breakdown. Files
  `HOB-46`..`HOB-50` (bare-block validation, aggregation mask, the history subject section, a
  stale spec pointer, header and directory checks); `HOB-48` keeps only the subject half, the
  time-length half is the P0 bug fold-in's. `HOB-5` is decided (separate per-aggregation history
  blocks) and `HOB-6` closed by design (`DICT` reserved). Rev 3 (second review): the send hub
  pushes the dictionary form whenever a tick's generation differs from the stream's last pushed
  one, closes a connection whose pinned frame is not written within 5 s, caps every uid at a
  quarter of the Live and of a new separate Query pool, sheds only connections that hold an
  unpinned tick, admits any frame into an empty control queue and bounds every JSON reply to
  448 KiB with paging; `section-order` checks rank before the header-first rule; the Python path
  retains at most one previous frame of ≤ 1 MiB per stream and pins CPython 3.14.10. Files
  `HOB-51`..`HOB-54` (received-frame alignment rules, one outcome for a repeated dictionary
  identity, the reference's check precedence, the stream layer machines).
- **Hand-mutation record for the P0 fold-in** (`docs/HAND-MUTANTS-M1D.md`,
  `docs/hand-mutants/m1d-mutants.py`): thirty-five stored patches in the method of
  `HAND-MUTANTS-M1C.md`. When this entry was written the campaign had not been run (it ran on
  2026-11-18; the doc states the result).
- **Gate invocations in `docs/CONSUMERS.md` and `README.md`** now state the estate wrappers
  (`scripts/controller/gate-run.sh <lane>` from a linked worktree's project directory;
  `scripts/controller/remote-gate.sh v1001 hostobs <lane> <commit-or-branch>` with
  `CONTROLLER_REMOTE_GATE_HOSTS=v1001`) instead of a bare `run-gate`, and that the Rust lanes
  need the `tester-unified-rust` image on the executing host.

- **`assay-r1`: the coverage verdict is assay's.** A new `assay-r1` lane in
  `run-gate.toml` binds to `assay.toml [lanes.assay-r1]`, which declares a Rust
  `whole_target` R1 judge over the 33 `src/*.rs` files of `model`, `proto`, `kernel` and
  `store` at a 95.0% line floor (measured 96.0377%, 15100/15723 executable lines, floored one
  point down; derivation in the lane file). The lane keeps the exact workspace command
  `selftest` runs — offline, locked, nextest, doctests and the per-crate line/region floors
  from `coverage-floors.toml` — so what was executed cannot drift; assay now renders the
  coverage claim, prints the measured totals in the verdict, lists every uncovered
  executable line as a finding, and reports branch capability as `unavailable` (stable
  `cargo-llvm-cov` emits no `BRDA` arcs; assay B523) rather than as zero-of-zero. R2 (mutation)
  and R3 (canary) stay unregistered for Rust (`HOB-45`). Proven a real judge, not a
  relabelled floor: deleting one kernel test file leaves nextest green but drops the R1
  claim to 93.9833% and FAILs it. The lane is a **command lane**: assay is stdlib-only, so it
  runs from the mounted worktree's own source (`PYTHONPATH={worktree}/assay/src`) with no
  install, which is why it needs no `pip` in the Rust gate image (`HOB-44` was the
  `kind = "assay"` declaration's install step, and is closed by this re-declaration; the
  image is unchanged). Proven through Run-Gate in this branch: the lane log carries
  `verdict PASS` and the measured totals, and a lane-run negative (one kernel test file
  deleted and committed in a temporary worktree) logs `FAIL/UNCOVERED_LINES`. `selftest`
  stays as the enforced baseline.
- **`store` crate (M1 part 3):** the on-disk history and events store. A segment is a
  sequence of `proto` payloads inside a checksummed record envelope (64-byte header, records
  carrying their own total length, sequence number, body CRC and incarnation list, a 64-byte
  footer on a sealed file), named `<tier>-<start_s>-<seq>.seg` (`*.seg.open` while being
  written). Tier directories are bounded by `keep_s` **and** `cap_bytes` (whichever bites
  first) and evict whole segments oldest first by `(start_s, seq)`; the active dictionary is
  RAM-bounded by `dict_active_max`, ids are monotone and never reused, and retirement frees an
  incarnation only when no stored segment still names it; the writer queue is byte-bounded
  with an oversize job refused (`job-too-large`) and every drop **counted**; a crash's torn
  tail is detected at open (`complete | incomplete | corrupt`) and either truncated back to
  its last good record or quarantined as `<name>.corrupt`; a seal is atomic in one order
  (temp name, footer, `fsync`, rename, `fsync` directory, unlink the open name); range reads
  are bounded in records and bytes and return payloads byte-identical to what was written
  (typed decoder errors, never a panic); and the events store has the same machinery plus the
  plan's RAM ring, open-span table with `(issuer, key)` LIFO closes and inferred `abandoned`
  ends, under its own cap. The byte layout is specified in
  [`docs/SEGMENT-FORMAT.md`](docs/SEGMENT-FORMAT.md).
- **The write boundary is a lint, and the lint has a mutation check.** Every filesystem
  mutation in the `store` crate lives in `crates/store/src/fsio.rs` (tests included, which is
  why the test temp-directory helper is `fsio::testdir::TempDir`);
  `tools/check-lints.py` refuses the write tokens anywhere else, and the new
  `tools/check-lints-mutation.py` (a `lint` lane step) plants five violations — including a
  whole new source file containing a write and a `#[mutants::skip]` inside the allowed file —
  and asserts each is reported, then asserts the baseline and `fsio.rs` are not. Plan v4 §A.3.
- **Segment fixtures and an independent reference decoder:** `fixtures/segment/` holds five
  whole segment files (open and sealed history, a sealed segment with no records, the events
  corpus, and a later epoch/dictionary generation) generated by
  `python/reference/make_segment_fixtures.py` from the format document.
  `python/reference/hostobs_segment_ref.py` decodes and re-encodes them with nothing but the
  standard library, and `crates/store/tests/golden_segment.rs` decodes them and reproduces
  them record for record with the Rust writer. Twenty Python tests pass under `python3 -I`.
- **Hand-mutation record for M1 part 3** (`docs/HAND-MUTANTS-M1C.md`,
  `docs/hand-mutants/m1c-mutants.py`): fifteen stored patches over the brief's nine failure
  modes (cap off-by-one, `keep_s` ignored, eviction order from both sides, torn tail accepted
  in the reader and in the tier's rescan, seal without a rename, retirement ignoring
  reachability, an uncounted drop, a foreign file deleted, a foreign-corpus segment adopted,
  a symlink followed, and three in the Python reference). All fifteen killed; the ring-order
  patch survived the first pass and was killed by a test that pins `oldest()`/`newest()` by
  name rather than the outcome of one eviction.
- **Backlog** grows from `HOB-26` to `HOB-40`: the `store`'s dependency limit on
  `proto`/`model` (and with it a range answer that is stored payload rather than decoded
  `model` rows, and a `plan_v4` with no focus tier), the O(records) range scan, the
  mount-table tmpfs check, the absent session spools, the non-atomic symlink refusal, the
  absent fault injection, the single writer as a property rather than an observable,
  retirement's blindness to externally-held incarnations, the in-process events displacement
  count, string-keyed event issuers, caller-driven span abandonment and the drop-oldest
  queue policy.
- **`kernel` crate (M1 part 2):** the read-only collectors — cgroupfs (`cpu.stat`,
  `cpu.max`/`weight`, `memory.current`/`.stat`/`.events`/`.max` and the other limits,
  `io.stat` with iocost, `pids.current`/`.max`, `memory.swap.current`, the three PSI files)
  and `/proc` (`stat`, `meminfo`, `vmstat`, `loadavg`, one process's
  `stat`/`statm`/`status`/`cmdline`, the boot id, the host `/proc/pressure/*` files), plus
  the identity inputs (`CgroupIdentity` from `(device, inode)`, `ProcessIdentity` from
  `(boot id, pid, starttime ticks)`) and an injectable clock (`Clock`, `ManualClock`,
  `SystemClock`, `Timeline`, `interval_between`). Every file access goes through one
  root-anchored, read-only `Fs` trait (`RealFs`, and `MemFs` for fixtures) with a byte
  bound, a line bound and an entry bound; every parse failure is a typed error carrying a
  quality code, never a zero. `#![forbid(unsafe_code)]`, no I/O beyond `File::open`/`read_dir`/
  `symlink_metadata`, no threads, no writes (`HOB-22`).
- **Committed kernel fixtures:** `crates/kernel/tests/fixtures/cgroupfs/` (the sampler's own
  cgroupfs tree, `gstammtisch`'s contents and `transient-footgun`) and
  `crates/kernel/tests/fixtures/proc/` (a hand-built `/proc` tree whose `stat` fields follow
  the kernel's own numbering). The readers are driven by those trees only, never by a live
  host, so the suite cannot depend on host load or version.
- **SP-2 evidence as tests** (`crates/kernel/tests/sp2_evidence.rs`): incarnation reuse (a
  cgroup recreated at one path, a recycled pid after exit, a reboot repeating pid and start
  tick), wall-clock jumps versus a monotonic interval (with the wall-based rate computed and
  shown negative), a pair from two boots/epochs refused rather than subtracted, a
  zero-length interval refusing a rate, and late discovery (an age since discovery and none
  since boot). Every expectation is hand-computed.
- **Hand-mutation record for M1 part 2** (`docs/HAND-MUTANTS-M1B.md`,
  `docs/hand-mutants/m1b-mutants.py`): thirty-one stored patches over the proto/Python
  refusals, the kernel collectors, the parse/value/identity guards, the clocks and the fix
  round's stat mapping. All killed, each restore SHA-256-verified; a patch that does not
  compile is reported as `NOT-BUILT` instead of counted as a kill, and a patch whose stored
  fragment no longer matches the source is `NOT APPLIED` rather than a survivor.
- **Backlog** grows from `HOB-13` to `HOB-26`: the three luna findings as fixed entries, the
  kernel's own value/quality types and their consequences, `st_dev` versus a `statx` mount
  id, the missing `/sys` topology source, the fixture-only reader evidence, and (from the
  fix round) the `stat` field mapping, the reported-length read, the iocost overflow rule and
  the SP-2 rate-helper limit.

### Fixed

- **A block shape is refused under any other frame type** (`not-a-block`, `HOB-14`).
  `decode_block` read the directory and bodies of whatever frame it was handed, so a `REQ`,
  `DICT` or `END` frame carrying block sections decoded as a block; `encode_block` had the
  same hole on the write side. Both now refuse, the Python decoder refuses with the same
  kind, and the `block-as-req` fixture pins it.
- **The canonical section order is enforced on decode** (`section-order`, `HOB-15`). The
  encoder checked it and the decoder did not, so a reordered directory decoded and then
  re-encoded to different bytes. `check_canonical_order` now runs before any body is read, a
  `direction`-free `canonical_rank` table is shared with the encoder, and the
  `section-order` fixture moves entries and bodies together.
- **A truncated section directory is a `WireError`, not a `struct.error`** (`HOB-16`). The
  Python decoder unpacked each entry after checking only the count, so a frame declaring
  more sections than it carried raised `struct.error`. The read is now bounded with context
  `block directory` and `need`/`have`; the `truncated-directory` fixture pins it and a
  Python test asserts the refusal for declared counts 1..4.
- **A `/proc/<pid>/stat` field is named by its proc(5) number, not by a hand-written index**
  (`HOB-23`). `parse_stat` read `minflt` from field 12 (which is `majflt`) and `majflt` from
  field 13 (`cmajflt`), and the unit-test helper, the committed fixture and the SP-2
  assertions carried the same wrong table, so the tests pinned the defect. The reader now
  declares field numbers 10/12/14/15/20/22/24/42 and derives every index through one
  `STAT_FIELD_OFFSET`, children's faults (11/13) are deliberately unread, the fixture and
  helper are generated from field numbers, and a new test gives field `n` the value `n` so
  every assertion proves its own field.
- **A read is bounded by the bytes it actually reads** (`HOB-24`). `RealFs::read` refused a
  file by its *reported* length (a sysfs attribute reports one page whatever its size) and
  computed `max_bytes + 1` unsaturating, so `usize::MAX` (“no bound”) wrapped the limit to 0
  and returned an empty file. It now applies the bound to the bytes read and saturates the
  `+ 1`.
- **An overflowing iocost sum is unreadable, not clamped** (`HOB-25`). `io.stat`'s counter
  sums used `checked_add` while its iocost sums used `saturating_add`, publishing
  `u64::MAX` as a measured cost. Both refuse now.
- **An unknown device topology is a state, not a bottom device** (`HOB-20`). The sampler
  treated an unclassified device as bottom
  (`collect.rs::is_bottom`, `last.get(dev).copied().unwrap_or(true)`), so a failed `/sys`
  read summed a stacked device's counters into the cgroup aggregate. `cgroup::TopologySource`
  makes the answer explicit and an `Unknown` device makes every `io.*` aggregate
  `unknown-topology` with no values at all.
- **An unparsable field no longer becomes a zero or a vanished metric.** A `cpu.stat`,
  `memory.stat`, `memory.events`, PSI, `statm` or `status` field that does not parse is
  published under its own name with the `unreadable` code; one bad `io.stat` counter makes
  the whole sum unavailable (a partial total would be a wrong number that looks right); a
  `max` limit is a `unsupported` state with the kernel's own text kept, not `-1`.

### Changed

- **`tools/verify-crate-cache.py`: the image lock identity is the registry-set digest.** One
  canonicalisation function (the lock `version`, then one sorted line per package that has a
  `source`: `name version checksum`, plus the source text when it is not crates.io) replaces
  the whole-file sha256, so a path dependency between workspace crates (which only rewrites
  workspace members' `dependencies` arrays) no longer needs an image rebuild; a changed,
  added or removed registry/git package, checksum, source or lock version still does, and
  non-crates.io sources are still refused. New mode `--print-registry-digest --lock PATH`;
  the wrapper flag `--baked-lock-sha256` became `--baked-registry-sha256`. The Rust lanes
  run on the image built from this source (`tester-unified-rust:rs-20261118`, named by
  `run-gate.root.toml`); HOB-3/17/27/39 stay OPEN until their refactors land.
- `docs/HAND-MUTANTS-M1B.md` and `docs/hand-mutants/m1b-mutants.py`: twenty-six patches
  became thirty-one (the fix round adds the `stat` mapping's three patches, the iocost
  overflow and the unbounded read).
- `coverage-floors.toml`: `crates/kernel` raised from the M0 bootstrap 90%/85% to 92% line /
  93% region after its own tests reached 92.19% line / 93.50% region. No floor was lowered
  and no code is excluded from coverage. The fix round adds tests without changing the
  floor: a raised floor is re-measured, never lowered to fit.
- `coverage-floors.toml`: `crates/store` raised from the M0 bootstrap 90%/85% to 94% line /
  93% region after its own tests reached 94.87% line / 93.90% region (108 unit tests plus the
  segment-fixture and quality-table integration tests). Five packages carry the bootstrap
  floor now, because the remaining crates are still the compilable scaffold.
- `docs/CONSUMERS.md` gains a worked `Store` adoption section (configure, open, submit,
  `write_pending`, `evict`, `seal_all`, `range`) with the refusal names a caller meets, and
  `docs/DESIGN-GUIDE.md` gains the five decisions behind the `store`'s shape (one write
  module, four bounds with a typed answer each, corpus-bound payload kinds, whole-segment
  eviction oldest-first with a sequence water mark, and a torn tail as a state). `README.md`
  describes the crate and points at the format document.
- `docs/SEGMENT-FORMAT.md` is the new normative document for the byte layout; both
  implementations are written from it rather than from each other.
- The `proto` refusal vocabulary grows from 40 to 42 names (`not-a-block`, `section-order`);
  the count test, the display test, the fixture comparison in both languages and the crate
  docs were updated together. `fixtures/wire` grows from 23 to 26 cases (7 valid, 19 refusals).

## [M1]

### Added

- **`model` crate (M1):** incarnation identity (`BootId`, `Epoch`, `CgroupKey` keyed by
  `(mount id, inode)`, `ProcessKey`, `IncarnationRegistry` with two-absent-scan retirement
  and rename-preserving relabel), the closed eight-code quality vocabulary, good/bad-quality
  values (`Sample::missing` refuses `Quality::Ok`), the metric registry with
  unit/kind/scope/source/aggregation (aggregation derived from kind), and counter
  classification plus rate derivation over the actual monotonic interval (`dt = 0`,
  `dt < 0`, reset, missing and wrong-type outcomes as typed refusals).
- **`proto` crate (M1):** the wire codec — frame header, section directory, fixed-width
  typed little-endian columns (raw `u64` counters and a mandatory `u8` quality per value),
  live rows, history buckets, and bounded per-segment dictionaries with generation
  retirement. Every decode failure is one typed `ProtoError`; nothing panics, nothing
  returns a partial block, and unknown or future section versions are refused.
- **Shared golden fixtures:** `fixtures/wire/` (26 cases: u64 boundaries, `-0.0`/NaN
  placeholder, Unicode dictionary, every quality code, empty selection, unknown optional
  section, and nineteen refusal cases) and `fixtures/rates/rates.json` (18 hand-computed
  classifier and rate cases). Bind the Rust tests and an independent stdlib-only Python
  reference codec (`python/reference/`, `python/tests/`) to one contract.
- **SP-4 measurement** (`crates/proto/examples/sp4_wire_size.rs`, deterministic,
  criterion-free) with published numbers in `docs/SP-4-WIRE-SIZES.md`: at 100/1,000
  cgroups, 82.7/80.2 B per row for the column codec against the sampler TLV's 50.1/50.9 B
  (per-tick deltas) and 62.1/62.9 B (raw counters). Confirms decision D4.
- **`KNOWN_ISSUES_TODO_BACKLOG.md`** with the first six `HOB-` entries (counter wrap
  policy, unmeasured CPU/allocations, the duplicated-vocabulary consequence of the locked
  image, the missing registered `conformance` lane, history min/max/mean, and the unused
  `DICT` frame type).
- **Hand-mutation record** `docs/HAND-MUTANTS-M1.md`: twenty-one mutations across the
  classifier boundaries and codec bounds, all killed in the lane image, each restored and
  SHA-256-verified; the two first-pass survivors became tests.
- **Docs:** `README.md`, `docs/DESIGN-GUIDE.md` and `docs/CONSUMERS.md` rewritten for the
  functional crates (contracts, rejected alternatives, paste-ready examples).

### Fixed

- **Dictionary decode no longer de-duplicates the wire list** (`duplicate-dictionary-entry`).
  `intern` merges a repeated identity (correct for a live table, wrong on the wire), so a
  body repeating an incarnation, metric name, label or free text came back with fewer
  entries and every later index shifted. The decoder now refuses when the entry it interned
  is not at the position the sender wrote; the Python reference decoder refuses the same
  input with the same kind name, and `duplicate-dictionary-entry` is a committed fixture.
- **A metric aggregation mask must equal the mask its kind derives**
  (`dictionary-entry-mask`). Previously only extra bits were refused, so a strict subset
  decoded and was silently widened on re-encode. Both languages now refuse a subset or an
  extra bit, pinned by the `bad-metric-mask` fixture.
- **Dictionary references are checked for kind, not only for range.** A row must name an
  incarnation (`row-reference-not-entity`), a column a metric
  (`column-reference-not-metric`), and a column's declared kind must agree with the
  dictionary's metric facts (`column-kind-mismatch`); fixtures pin all three in both
  languages.
- **Values without an address are refused** (`no-rows-or-time`): a non-zero row count
  requires a rows or a time section, on the encoder and the decoder side. Rows and buckets
  are now exclusive **by presence**, so a block carrying both sections is refused even
  when both are empty (`n_rows == 0`), which the count-based check missed.
- `Column::sample` computes its reported need with `checked_add` (a caller-supplied
  `usize::MAX` row was a debug-build overflow), and `decode_block` takes the directory slice
  with `.get(..)` instead of an index.
- The refusal vocabulary grows from 34 to 40 names; the count test, the display test, the
  fixture comparison in both languages and the crate docs were updated together.
- `Dictionary::encode_with` writes an exact entry list (tests and fixtures need to express
  wire lists a live table would never build); `Dictionary::encode` is unchanged.
- Fix-round hand-mutation campaign stored as a replayable script
  (`docs/hand-mutants/fix-round-mutants.py`, 16 patches, all killed) and the backlog grew
  to `HOB-13`.

### Changed

- `coverage-floors.toml`: `crates/model` raised to 97% line / 96% region and
  `crates/proto` to 98% line / 96% region (measured 98.06%/97.90% and 99.43%/97.69%).
  No floor was lowered and no code is excluded from coverage.

## [M0]

### Added

- Added the minimal hostobs Cargo workspace skeleton for the planned sampler crate split.
- Added offline Cargo settings and a lock-bound launcher that checks the baked crate cache before invoking Cargo with `--locked`.
- Added zero-retry nextest configuration, cargo-deny policy, source lint tooling, and per-crate coverage-floor checks at 90% line and 85% region.
- Added the `lint` and `selftest` Run-Gate lanes using the `tester-unified-rust` image; both run without network access.
