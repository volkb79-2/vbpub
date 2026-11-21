# hostobs probe serving (P5b): design, resolutions and package split

Status: design reviewed 2026-11-18 by an independent Opus reviewer (verdict GO-WITH-CHANGES; its
findings are folded in below as rules R1..R28). Author: session vbpub-cb. Normative protocol:
`WIRE.md` (sections quoted as §n). This file says HOW the probe's serving side is built, cut into
packages, and how it RESOLVES the places where WIRE.md is silent or contradicts itself. A rule here
that needs a WIRE.md edit lists that edit in §6; the package that implements the rule makes the edit.

## 1. Shape: a sans-IO core, injected backend, thin drivers

`bins/probe/src/` library modules (no new crate; `main.rs` stays a thin entry until P5b-4; the
probe serves levels observe, operator and observer; `control` belongs to `act`: `Server::accept(Control)`
refuses uncounted. A serving core inside `bins/probe` is not reusable by `bins/act`: backlog item for
a later move into a crate).

- **`Conn`**: one per accepted connection, a pure state machine. No sockets, threads or clock reads,
  no allocation by an untrusted length.
- **`Server`**: owns ONE `SendHub` (§5.3), the conns keyed by `ConnId`, the per-level and per-uid
  counters, and the backend (`B: Backend`, methods `&self` in P5b-1). A single-threaded `poll(2)`
  driver needs no mutex (WIRE §5.3's "one mutex" is not needed; recorded as a deviation).
- **`Backend`** (trait): P5b-1 needs only `identity() -> Identity {server, boot_id, epoch, features}`
  and `status() -> StatusInfo`. `Server` fills `clients` per level and `queues.queued_bytes` (from
  `hub.global_pools()`); `queues.dropped_blocks` is 0 in P5b-1 (a hub counter arrives in P5b-2).
  `peer_identity`/issuer is NOT in P5b-1 (§4.12 is P5b-3). Test backends in `#[cfg(test)]`.
- **API** (all deterministic under a fake clock): `Server::accept(level, peer: Option<Peer>, now) ->
  Accepted(ConnId) | Refused` (`Peer{uid,pid}` captured ONCE and stored in the `Conn`; every counter
  decrement and hub `add` uses the stored uid, never a re-read; `None` = refuse uncounted),
  `on_bytes(id, bytes, now)`, `on_eof(id, now)`, `poll(now)`, `output(id, now) -> Option<[&[u8]; 2]>`
  (the hub's `head`, which PINS: never call it to test emptiness; use `hub.queued(c) == Pools::default()`),
  `wrote(id, n, now)`, `take_final(id) -> Option<Vec<u8>>`, `is_closed(id)`, `finish(id)`, `shutdown()`
  (no clock: it closes every conn with `shutting-down`; the server keeps no stopping flag, the driver
  stops calling `accept` after it).
- **`finish(id)` is the single close path** (R22): it decrements the level and uid counters, deletes a
  uid entry at 0, calls `hub.remove`, drops the conn; idempotent; used by EVERY close (silent,
  unknown-mode, major mismatch, Closing, Draining done, driver I/O error, shutdown). `refuse` never
  increments. Accept when `count < cap`. Test: after each close path counters, `uid_pools` and
  `global_pools` are back to zero.
- **Levels** are a closed enum; the verb table of §4.3 is `const` data, one row per verb with its
  level mask, shape (JSON/BLK), `served` flag (P5b-1: hello, status) and reserved flag (§4.13).

## 2. Package cut (R-cut)

Blind-compilation risk is minimised by splitting the original P5b-1:

- **P5b-1a, pure functions, no connection state** (about two review rounds; its API is the fixed input
  of 1b): `Limits` (the §5.1 defaults per level, doc comments carry the `hostobsd.toml` key names; no
  TOML parsing), `Level`, the verb table, the **args reader** (R4), `hello`/`status` argument schemas,
  the **precedence function** `classify(...) -> Dispatch | Refusal{code, detail}` (R11), the encoders
  (`control_frame(type, corr, payload)`, the §4.1 error envelope, the `closing` EVT, the JSON line;
  message/`detail` rules R14, reply cap R25), and the JSON **`LineReader`** (R13) as a §5.2-style
  table with row tests. Each piece gets deterministic hostile-run tests (the style of the client's
  2,000 + 2,000 runs: closed set of outcomes, no panic, bounded buffers).
- **P5b-1b, the state machine**: `Conn`/`Server`, every §5.4 row, the final-frame `Closing` (R1, R2),
  counters (R22), the timer order (R20), `hello`/`status` end to end, binary and JSON mode, and the
  reachable §11.4 connection cases (R21). About two to three rounds.
- Later: P5b-2 (live streams), P5b-3 (history verbs, issuer), P5b-4 (drivers, acceptance; Opus review
  of the final series), see WIRE.md §12 P5b and the controller's approval of 2026-11-18.

## 3. Resolutions (the source of truth where WIRE.md is silent or inconsistent)

**R1 Final frame, not a hub frame (WIRE §5.2 vs §5.4 `Closing`).** A connection-fatal outcome yields
exactly ONE final frame held OUTSIDE the hub: in binary mode the REP the table names when the outcome
answers a request or a reader error (`hello-required`, `bad-frame`, `frame-too-large`, `deadline`), the
`EVT closing {event,reason}` for `idle`, `slow-client`, `shutting-down` and `unsupported-version` /
`unknown-mode` as specified; in JSON mode the error line. Algorithm at `Closing(code)`: `drain_unpinned(c)`;
if `head_partial(c)` is false then `final = Some(bytes)` else `None`; then the conn is closed (`is_closed`)
and the driver takes `take_final` and makes ONE non-blocking write, abandoning a short write; `finish` removes it.
**R2 Prefix guarantee.** The conn counts the bytes reported through `wrote`. When it is 0 at `Closing`
and the server prefix was queued (the `conn-prefix-surplus` shape: prefix + a bad REQ in one read), the
final bytes are the 8 prefix bytes followed by the final frame; 1..7 written means `head_partial` is
already true and nothing more is written. A major-version mismatch: the final bytes are the prefix alone.
**R3 Superseded by P5b-2c + P5b-2d (unreachable in P5b-1):** `too-many-requests` and "corr already
in use" need a request that stays outstanding; `hello`/`status` are synchronous, so outstanding is 0
whenever the next request arrives in P5b-1. P5b-2c supplies the outstanding `current` and
subscription state, and P5b-2d serves the block verbs; their position in the precedence list (R11)
is unchanged. See `P5B2-LIVE-STREAMS-DESIGN.md` S5, S6 and S28.
**R4 Args reader.** Do NOT use serde for requests (it accepts duplicate keys in map-typed objects,
recursion limit 128 not 8, and `detail.key` would be scraped from error text), although `bins/probe/Cargo.toml`
depends on `serde_json`. Build on `proto::envelope`: `check_req` already enforces UTF-8, grammar, depth <= 8,
no BOM and no trailing bytes; the args layer adds `members_up_to(slice, 16)` per object (duplicates per
level; cap 16 members per object and 64 elements per array: the largest request object has 7), the strict
schema, string unescaping that REFUSES surrogates (lone or paired-wrongly: `bad-request`; a valid pair is
decoded), decoded-length bounds and exact integers. `client::json::unescape` must NOT be lifted: it maps a
lone surrogate to U+FFFD, which §4.2 forbids server-side.
**R5 Backend sources.** `hello` needs `server`, `boot_id`, `epoch`, `features` from `Backend::identity()`;
see §1.
**R6 Invalid hello in `AwaitHello`.** Bad args: REP `corr=1` `bad-request`, state stays `AwaitHello`
(non-fatal), hello timer keeps running. A REQ failing `check_req` in `AwaitHello`: `bad-request` on its own
`corr`, stays.
*Open for P5b-1b:* WIRE §5.4's row `AwaitHello` (a malformed REQ answers `hello-required` and closes) must
be reconciled with §4.2 (a request failing `check_req` is a non-fatal `bad-request`, as `classify` does):
fix the row or the rule in P5b-1b, together with the connection state machine.
**R7 `hello.proto`** must equal the CLIENT's own prefix (major 1, minor C), not the negotiated N. The
result carries N = min(C, server minor). (The Rust client sends `PROTO_MINOR` in `hello` and checks
`result.proto.minor == N`.)
**R8 JSON-mode `hello`:** negotiation N = min(C, S_minor); a major other than 1 is `unsupported-version`
(fatal; the only wire place the code is reachable); `hello` is accepted only as the first line; a second
`hello` in `Ready`, either mode, is `bad-request` with `detail.key` `"verb"`.
**R9 `AwaitHello` row pinned:** `corr == 0` -> `bad-frame` `corr=0` (§2.3 first); verb other than `hello`
with any `corr != 0` -> `hello-required` on that `corr`; `hello` with `corr != 1` -> `bad-frame` `corr=0`.
**R10 EOF before `Ready`:** `Undecided`, `JsonNoHello` without a line, `AwaitHello`: silent close (no
request owed); EOF mid-line in JSON mode: close without reply (mirrors §5.2 `truncated`); EOF in `Ready`:
`Draining`; bytes after EOF: `trailing-bytes`, close without reply.
**R11 Request check precedence (§4.2/§4.3/§10):** (1) frame type != REQ: `bad-frame`, fatal; (2) `corr == 0`
(binary), or (P5b-2) `corr` in use: `bad-frame`, fatal; (3) `check_req`: `bad-request`; (4) `AwaitHello` gate;
(5) verb not in the table or reserved (§4.13): `unknown-verb`; (6) level: `forbidden`; (7) `hello` when the
connection is `Ready` (either mode): `bad-request` `{"key":"verb"}` (R8); (8) JSON mode and a block verb:
`binary-required`; (9) known but not served in this package: `unknown-verb` (last of the table checks, so
`forbidden`, the second-hello check and `binary-required` still come from the full table); (10) (P5b-2)
`max_outstanding`: `too-many-requests`; (11) args schema: `bad-request`; (12) (P5b-3) verb-level feature
`unsupported-feature`; (13) execute. So duplicate keys inside an UNKNOWN verb's args are never inspected
(state it in §4.2). Known M2 verbs not served yet answer `unknown-verb` and are absent from
`hello.verbs`; `forbidden` and `binary-required` stay testable. Never `internal` for them.
**R12 JSON-mode `corr`:** echoed only if `members()` succeeds and `corr` is one exact u32 != 0, else 0;
a JSON-mode `corr` of 0 is `bad-request` `{"key":"corr"}`, non-fatal; binary `corr` 0 is `bad-frame`, fatal.
**R13 JSON `LineReader`:** a line including its `\n` is at most `max_request_bytes`; at that many bytes
buffered without a `\n` the reader refuses with `frame-too-large` (fatal); `\r\n` reaches `check_req` with a
trailing `\r` = `bad-request` (non-fatal); the request-deadline age is stamped at each line's first byte;
linear time: keep a scanned offset (no rescan per feed), slice complete lines straight out of the fed
`bytes`, buffer only the incomplete tail, compact at most once per feed; capacity <= min(`max_request_bytes`,
received + step); an empty line is `bad-request`.
**R14 Bounded echo.** `message` is a `&'static str`, never client text. `detail.key` is built from schema
names; an unknown or duplicate key is included only if it is <= 64 bytes of `[A-Za-z0-9_.-]` with no
backslash, otherwise the path stops at the parent. `EnvelopeError` -> `detail.key`: `Json` -> `""`;
`MissingKey`/`WrongType(n)` -> `n`; `DuplicateKey`/`UnknownKey` -> the bounded rule.
**R15 Escaped keys.** Duplicate detection compares raw bytes; to stay exact, any key containing a backslash
is refused as unknown before it is compared (already the envelope rule; make it the args rule).
**R16 Integers (§8.3).** Digits only: a leading `-` on an unsigned field is `bad-request`, `-0` is refused
for signed fields too, `1.0`, `1e2`, a value beyond the field's own type (`proto.minor` is u16: 65536 is
refused) are `bad-request`; checked arithmetic, stop after 20 digits.
**R17 `hello.client`:** 0..64 decoded bytes, no control characters (< 0x20, 0x7F), lone surrogates refused
(matches the Rust client's own rule).
**R18 Block keys of `hello.limits`** are 0 at levels that serve no block verb: `max_subscriptions`,
`client_queue_bytes`, `query_queue_bytes`, `max_block_bytes`, `max_rows`, `max_block_buckets`,
`max_page_blocks`, `min_interval_ms`, `tick_ms`; `hello.verbs` = the verbs served at the level now.
**R19 Quadratic CPU:** duplicate detection is O(k^2) per object, hence the caps in R4; line reading R13;
no `Vec::drain` per line.
**R20 Timer order**, one deterministic order per conn per `poll`: (1) `ConnTimers` Hello, (2)
`FrameReader::poll` / line-reader age (`deadline`), (3) `ConnTimers` Idle, (4) hub `poll` `slow-client`;
the first fatal outcome wins and the rest are ignored; conns are processed in `ConnId` order and closed
conns are `finish`ed (`hub.poll` keeps reporting until removal). Hello and Idle are exclusive (Idle arms
only in `Ready`), Idle and stall are exclusive (Idle needs an empty queue); the only tie is Hello vs stall
on the unread prefix, settled by the order. The idle condition is recomputed at the end of every `Server`
entry point (`accept`, `on_bytes`, `wrote`, `poll`). Within `on_bytes`: `on_byte`, feed, dispatch each
frame (`on_enqueue` per push), stop at the first fatal outcome, then apply `Feed.error`; frames left in a
`Feed` after a fatal outcome are DISCARDED, not dispatched. `Feed`'s frame vector is bounded only by the
driver's read size: P5b-4 fixes one read size (<= 64 KiB).
**R21 Superseded for block-verb connection cases by P5b-2c + P5b-2d (reachable in P5b-1):** all
connection cases ran in P5b-1 except `conn-reply-page-truncated` (uses `events`, M3: retarget to
`procs` in P5b-3), `conn-not-idle-with-subscription`, `conn-dribbling-reader`, the END half of
`conn-half-close`, `conn-corr-in-use-sub`, `conn-corr-in-use-pending` and
`conn-too-many-requests`. P5b-2c supplies the connection state and P5b-2d delivers ticks, retries
and the served block verbs; those cases now run against the in-memory `Conn`/`Server`.
"In-process socketpair" becomes the sans-IO core under test; the socketpair runs move to P5b-4
(deviation note in §11.4). See `P5B2-LIVE-STREAMS-DESIGN.md` S31.
**R23 Peer:** `client` is a free label, never identity; a request key naming a uid, pid or cgroup is
`bad-request` through the strict schemas (test it); `pid` is unused in P5b-1.
**R24 Mode detection** uses `stream::ModeDetect`, NOT `frame::detect_mode` (one-shot, reports a partial
magic as `truncated`).
**R25 Reply cap:** one `control_frame(type, corr, payload) -> Vec<u8>` helper (`encode_frame` needs
sections); a REP longer than `max_reply_bytes` is replaced by an `internal` error (bounds a misbehaving
backend, e.g. more than 8 tiers).
**R26 Gates:** each new src file is an `assay.toml` `judge.targets` entry as well as under `source_roots`
(`bins/probe/src` joins `source_roots`); `coverage-floors.toml` `[crates."bins/probe"]` is the bootstrap
90/85 and becomes a measured behavioural floor; `lib.rs`/`main.rs` are judged only if they carry executable
lines (module declarations only: not judged).
**R27 Hub:** the conn methods take `&mut SendHub` and `&dyn Backend` as disjoint field borrows of `Server`.
**R28 Control queue memory** (`control_queue_bytes` 2 MiB with no per-uid/global cap: 64 observer conns
from one uid hold 128 MiB, 128 conns of the level 256 MiB): DECISION PENDING with the controller before
`Limits` defaults freeze; candidates: observer `control_queue_bytes` ~ 320 KiB (`max_reply_bytes` ~ 16 KiB
by the §5.1 formula) or a per-uid/global control pool. Until decided, `Limits` carries the WIRE defaults
and the choice is a one-line change.

## 4. P5b-1a test list (minimum)

Args reader / schemas: escaped key spellings and duplicates of them; surrogate pair valid, high + non-low,
low first, high at end; `\u0000` and controls in `client`; decoded vs escaped length at 64/65; 16/17
members, 64/65 elements; duplicate at depth 2 (`{"proto":{...},"proto":...}`); `true`/`null`/string where
an integer is expected; `-0`, `1.0`, `1e2`, 2^64, i64 edges, u16 65535/65536; a 30 KB key with `detail`
<= 2 KiB; keys naming uid/pid/cgroup; depth 8/9. LineReader: CRLF, empty line, a line of exactly
`max_request_bytes` and one byte more, 10k lines in one feed and 1-byte dribbles (linear-time asserts via
operation counters or capacity bounds, not wall time), EOF mid-line. Precedence: one test per step of R11
and per tie between steps. Encoders: every §10 code reachable, `max_reply_bytes` fallback, `detail` bounds.
Hostile runs: 2,000 + 2,000 deterministic runs with a closed outcome set and no panic.

## 5. P5b-1b test list (minimum)

`conn_row_<n>_*` one per §5.4 row; the reachable §11.4 cases; one per §5.1 connection-level row
(caps per level/uid, hello deadline, request size, request deadline, idle, control queue, stall, frame
completion, max_reply_bytes); R1/R2 (prefix + bad REQ in one read; final frame held back when a frame is
half written; major mismatch prefix alone); R6..R10 rows; counters back to zero after every close path;
timer order ties (R20); byte-by-byte delivery of prefix, hello, status in both modes; frames after a fatal
outcome discarded; no unbounded buffer growth (`buffered_capacity` bounds) under hostile bytes.

## 6. WIRE.md edits that belong to these packages

§4.2 request precedence (R11) and the digits-only integer rule (R16); §4.4 `hello.proto` = client prefix
(R7) and JSON-mode negotiation (R8), the block-key list (R18); §5.2/§5.4 final-frame rule and the new rows
(R1, R2, R6, R9, R10) with a decision D-W28; §8.1 line rules (R13), JSON `corr` recovery (R12); §11.4
`events` -> `procs` retarget and socketpair -> P5b-4 (R21); §5.3 "no mutex" note (R27); §5.1 control-queue
memory per R28 once decided.

## 7. P5b-3 and P5b-4 boundary

The P5b package split is: **P5b-3 implements bounded, injected query serving; P5b-4 supplies the
real socket driver and proves it over socketpairs.** Neither package supplies the real history store
reader or the `/proc` process scanner. WIRE §4.8–§4.11 is normative for both the fixed backend tests
and the later production source. P5c/HOB-62 owns the production source path and must land before the
M2 live-exit claim.

Keep responsibilities single-sourced:

- `Conn` owns parsed request state, correlation ordering, REP/BLOCK/END transitions, and the typed
  state needed to resume a request after Query backpressure or while Draining.
- `Server` owns cursor tokens and their bounded canonical-argument bindings, source-view identifiers,
  expiry, and the fair Query admission scheduler. There is one cursor authority; a backend does not
  mint or interpret wire cursor strings.
- `Backend` exposes narrow typed operations for one snapshot result, an immutable history view with
  one bounded next candidate at a time, and an immutable process scan/page. It does not encode JSON or
  wire BLOCKs and does not decide protocol errors. A source-view eviction/expiry is an explicit typed
  outcome, not an empty page.
- `SendHub` owns encoded queued bytes and the Query byte pools. P5b-3 stores only typed view/arguments/
  position while blocked. It preflights `query_room(c, max_block_bytes)`, builds at most one candidate
  BLOCK at a time, and queues that BLOCK directly. Retry turns rotate by peer uid and request; one
  request cannot monopolize all producer progress. The existing D-W30 rule for `current` remains.
- The P5b-4 driver owns `LISTEN_FDS`, nonblocking socket reads/writes, mode detection plumbing,
  `SO_PEERCRED`/`SO_PEERPIDFD` capture at accept, and configuration. It passes immutable peer facts
  into `Server`; it never supplies identity from REQ JSON and never re-reads a peer by numeric PID
  after accept. The core remains sans-IO and fake-clock testable.

P5b-3's fixed backend proves decoded wire results and refusal precedence, not production `/proc` or
store behavior. Its behavioral oracles belong to `hostobs:selftest` and `hostobs:assay-r1`; the
Python-only `hostobs:conformance` lane is not evidence for Rust serving behavior. P5b-4 may claim
socket transport acceptance only for the rows and verbs its socketpair fixtures actually execute.
Register every new Rust source module in `hostobs/assay.toml` and maintain the declared crate coverage
floor; do not use `conformance` as a proxy for those tests.

### P5b-3 typed backend contract

The P5b-3 implementation extends the existing `Backend` seam with these exact view, position, data,
and failure types. The fields below are Rust types; the response behavior and field bounds remain
those in `WIRE.md`.

```rust
struct SnapshotViewId(u64);
struct SeriesViewId(u64);
struct ProcScanId(u64);
struct SeriesPosition(u64); // opaque to Server; identifies the next committed block
enum Tier { Hot, Warm, Cold } // wire names: "hot", "warm", "cold"
enum TierChoice { Auto, Explicit(Tier) } // "auto" or an explicit wire tier
enum QueryFeature { Provenance } // wire name: "provenance"
enum ProcField { Comm, Ppid, State, CpuUsec, RssBytes, IoRbytes, IoWbytes, Threads, Cmdline }
struct QueryBounds {
    max_rows: u32,
    max_block_buckets: u32,
    max_block_bytes: u32,
}
struct SnapshotRequest {
    selection: Selection,
    real_ns: i64,
    tier: TierChoice,
    provenance: bool,
}
struct SeriesRequest {
    selection: Selection,
    from_real_ns: i64,
    to_real_ns: i64,
    step_ms: u32,
    aggs: Vec<Aggregation>,
}
struct ProcsRequest {
    subject_inc: u64,
    descendants: bool,
    fields: Vec<ProcField>,
}
enum RawNumber { U64(u64), I64(i64), F64(f64) }
struct MonoInterval { start: u64, end: u64 }
struct CellProvenance {
    inc: u64,
    metric: String,
    quality: Quality,
    source: MetricSource,
    source_ref: Option<String>,
    observed_mono_ns: Option<u64>,
    interval_mono_ns: Option<MonoInterval>,
    raw_inputs: Option<Vec<RawInput>>,
    constraints: Option<Vec<ConstraintFact>>,
}
struct RawInput {
    metric: String,
    value_type: MetricValueType,
    value: Option<RawNumber>,
    quality: Quality,
    source: MetricSource,
    unit: MetricUnit,
    source_ref: Option<String>,
    observed_mono_ns: Option<u64>,
    interval_mono_ns: Option<MonoInterval>,
}
enum ConstraintValue { U64(u64), I64(i64), F64(f64), String(String) }
struct ConstraintFact {
    name: String,
    value: ConstraintValue,
    unit: Option<MetricUnit>,
    source_ref: Option<String>,
    observed_mono_ns: Option<u64>,
}

enum QuerySourceError {
    NotFound,
    DataGap,
    BucketOutOfRange,
    Unsupported(QueryFeature),
    Budget(Pool),
    ViewInvalid,
    Internal,
}

struct SnapshotView {
    id: SnapshotViewId,
    meta: SnapshotMeta,
}
struct SnapshotMeta {
    tier: Tier,
    bucket_real_ns: i64,
    step_ms: u32,
    covered_ms: u32,
    provenance: Option<Vec<CellProvenance>>,
}
struct SeriesView {
    id: SeriesViewId,
    tier: Tier,
    step_ms: u32,
    omitted: Vec<String>,
    first: Option<SeriesPosition>,
}
struct HistoryBlockData {
    subject_inc: u64,
    agg: Aggregation,
    buckets: Vec<HistoryBucket>, // ≤ max_block_buckets
    columns: Vec<HistoryColumn>, // ≤ 64; one value and quality per bucket
}
struct HistoryBucket {
    bucket_real_ns: i64,
    covered_ms: u32,
    samples: u16,
}
struct HistoryColumn {
    metric: String,
    facts: MetricFacts,
    value_type: MetricValueType,
    values: Vec<u64>,
    quality: Vec<u8>,
}
struct ProcScanView {
    id: ProcScanId,
    published_mono_ns: u64,
    row_count: u32, // 0..=max_rows
    scanned: u32,
    total: u32,
    complete: bool,
}
struct ProcRow {
    pid: u32,
    start_ticks: u64,
    inc: u64,
    comm: Option<String>,
    ppid: Option<u32>,
    state: Option<u8>,
    cpu_usec: Option<u64>,
    rss_bytes: Option<u64>,
    io_rbytes: Option<u64>,
    io_wbytes: Option<u64>,
    threads: Option<u64>,
    cmdline: Option<String>,
}

trait QueryBackend {
    fn snapshot_open(&self, request: &SnapshotRequest, now_ns: u64, bounds: QueryBounds)
        -> Result<SnapshotView, QuerySourceError>;
    fn snapshot_read(&self, view: SnapshotViewId)
        -> Result<TickData, QuerySourceError>;
    fn series_open(&self, request: &SeriesRequest, now_ns: u64, bounds: QueryBounds)
        -> Result<SeriesView, QuerySourceError>;
    fn series_next(&self, view: SeriesViewId, after: SeriesPosition)
        -> Result<Option<SeriesPosition>, QuerySourceError>;
    fn series_read(&self, view: SeriesViewId, at: SeriesPosition)
        -> Result<HistoryBlockData, QuerySourceError>;
    fn procs_open(&self, request: &ProcsRequest, now_ns: u64, max_rows: u32)
        -> Result<ProcScanView, QuerySourceError>;
    fn procs_read(&self, view: ProcScanId, index: u32, fields: &[ProcField])
        -> Result<Option<ProcRow>, QuerySourceError>;
}
trait Backend: QueryBackend {
    // Existing identity(), status(), and current() operations remain here.
}
```

`SnapshotMeta` and `SeriesView` contain no block values. `Tier`, `TierChoice`, `QueryFeature`, and
`ProcField` are closed enums owned by `backend.rs`; `Quality`, `MetricSource`, `MetricUnit`,
`MetricValueType`, `MetricFacts`, and `Aggregation` are the existing `hostobs_proto` types. `Peer`
adds only the captured `cgroup_inc: Option<u64>` fact in P5b-3, alongside its existing uid and pid;
the observer issuer is derived from those captured fields without a lookup. `CellProvenance` and its
children map one-for-one to the exact WIRE §4.8 schema. `Server` computes each JSON `age_ms` from
`observed_mono_ns` at REP construction and validates types, limits, finite f64 values, references,
array null-versus-empty meaning, ordering, and the encoded object/reply bounds. `RawInput.value` must
match `value_type` when present; `ConstraintFact.value`'s enum variant is the serialized `type`.
`ProcField` serializes to the exact snake-case WIRE field names and the order passed to `procs_open`
is canonical by those names.

`SnapshotMeta.covered_ms` and each
`HistoryBucket.covered_ms` are source-computed facts under WIRE §3.6; P5b validates their range and
copies them into the result, and never receives interval lists. `HistoryBucket.samples` is the
source's exact `u16` count and the server copies it unchanged. For `snapshot_at`, the server checks
the returned tick's boot and epoch against the serving identity, preserves its source tick `seq` and
monotonic/boot times, sets header `real_ns` from the validated `SnapshotMeta.bucket_real_ns`, and
forces every row's `t_off_us` to zero. `series_next` is metadata-only and
returns only an opaque fixed-size position; `SeriesView.first` is the first candidate or `None`, and
`series_next(view, after)` returns the next position or `None`. It skips gaps and follows the
deterministic subject/aggregation/time packing order. `series_open` uses `QueryBounds` to define each
candidate's largest consecutive bucket prefix that fits both limits from WIRE §4.9. `Server` examines at most
`max_page_blocks + 1` positions before REP to find a page continuation, then discards the lookahead.
`series_read` reads exactly one candidate after Query room is available; its columns and bucket
metadata have no more than 64 × 4,096 values and the matching quality bytes. `Server` verifies the
candidate's bounds and encoded size before pushing it. `procs_read` reads one row from the immutable
scan at the requested index; the server serializes only requested fields and stops before
`max_reply_bytes`. The process view's `row_count` and coverage facts are frozen at `procs_open`; only
`scan_age_ms` is computed by the server at REP construction. `ProcScanView.row_count` is the row count
after the source has applied the requested subject and descendant filter, while `scanned`, `total`,
and `complete` describe coverage of the whole pinned source scan. A `ProcRow` index is stable in
ascending `(pid,start_ticks)` order for the life of the view. The server rejects inconsistent typed
coverage (`scanned > min(total, 50,000)`, `row_count > max_rows`, or `complete=true` with
`total > 50,000` or `scanned != total`), duplicate/out-of-order row identities, malformed state,
`comm`/`cmdline` beyond the WIRE field bounds, or any reply over `max_reply_bytes` as `internal`.
The backend request structs deliberately omit wire cursors and page sizes (`max_blocks`, `limit`):
those belong to the Server's one cursor and reply-page authority.

The server maps `QuerySourceError` to protocol codes and fixed details. `BucketOutOfRange` maps to
`bad-request {"key":"real_ns"}`; snapshot tier resolution is metadata-only and no bucket values are
read before this refusal. The server independently checks a successful `SnapshotMeta`'s tier, step,
Euclidean bucket start and `covered_ms` range; any mismatch is `internal`. `ViewInvalid` on an incoming
cursor is `cursor-invalid` before REP; after REP it is `END ok:false cursor-invalid`. An unexpected
source error or malformed typed fact is `internal`. The backend never receives a wire cursor string,
peer uid, or protocol error object; the server binds its own cursor table to the returned opaque view
id and position. This keeps the source API usable with both deterministic P5b fakes and the later
P5c implementation without duplicating wire state in the source.
