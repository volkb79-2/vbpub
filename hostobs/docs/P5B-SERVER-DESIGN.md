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

### Design rationale and alternatives

- **Keep the serving seam separate from production readers.** `QueryBackend` is the long-term boundary:
  `Server` owns protocol policy, admission, cursors and page packing over typed immutable facts;
  production adapters own store/procfs acquisition and implement that same seam. P5c owns real store,
  procfs, membership and provenance adapters. The current Store
  range API returns bounded records and truncation state, not the committed history view and stable
  replay index this contract needs. The process reader reads individual procfs files and does not
  implement bounded lowest-PID enumeration, scan coverage or membership rechecks. Folding those
  sources into P5b would couple a large serving/state change to two independent data-source designs
  and their fake-root/fake-store race evidence. The tradeoff is that fixed-backend tests can pass before
  live M2 data exists; P5b is therefore not M2 live-exit evidence. P5c plus an integrated VM query
  against real sources is required. The strongest alternative is an end-to-end P5b package; it removes
  the interim gap but makes source races, storage semantics and cursor behavior one much larger review.
- **Publish the newest weakly consistent process scan.** Procfs does not offer an atomic host-wide
  snapshot. A bounded listing followed by identity and membership checks reports what was observed;
  `complete` describes that procedure and does not claim every returned process coexisted at one
  instant. Once published, rows, optional fields and coverage are immutable so page replay is stable.
  Two matching scans add work and can fail indefinitely under churn without making fields atomic;
  pidfds add up to 50,000 descriptors but do not freeze fields; event-fed inventory needs overflow and
  resync state; freezing workloads is privileged and changes what is measured; rejecting every changed
  scan would turn normal churn into routine refusal. The selected design uses bounded scans and makes
  the consistency limit visible.
- **Preflight the exact BLOCK and keep one encoded queue.** Snapshot and history metadata determine
  row/column counts and dictionary bytes, so the Server computes the exact frame length before value
  reads. In the single-threaded Server turn, `query_room` and `push_query` use the same unchanged pool
  authority; an encoded staging slot is redundant. One staged BLOCK would add up to 4 MiB outside all
  Query pools, global head-of-line blocking, and close/expiry cleanup. A future concurrent producer
  would need a charged hub reservation with explicit release, not a hidden staging buffer. Pending
  `current` remains the D-W30 exception because it re-resolves a potentially larger tick after its first
  attempt. The accepted progress limit is that per-uid rotation does not guarantee eventual service for
  every request within one busy uid; an oldest-waiter drain rule could provide that guarantee but would
  head-of-line block smaller requests from the same uid. Query progress also requires the peer to drain
  queued bytes.
- **Make history resolution deterministic and views byte-stable.** One tier must cover the full
  half-open range; step zero chooses the coarsest eligible tier and an explicit shared step chooses
  among full-range eligible tiers by hot/warm/cold. Gaps are omitted; tiers are never spliced. A valid
  range with no bucket starts emits an empty complete page. A metric is omitted only when its mask
  excludes every requested aggregation. The view freezes values, quality, coverage, samples, group
  ordinals and dictionary facts; a committed watermark alone cannot guarantee this when a later append
  changes the same bucket. Any eviction in the selected tier invalidates the view, even outside the
  requested range. This may end a cursor before 60 seconds, but avoids retention that grows with client
  pins. Tier splicing, fallback, gap markers, or retained evicted segments would return more data but
  mix resolutions, add wire state, or make client activity control storage lifetime. P5c must prove a
  bounded immutable/versioned read index; if the store cannot supply it, the source design must change
  before M2 exit. Packing is deterministic for a fixed cursor start and page size. Every continuation is
  a new query stream, so its first BLOCK repeats the dictionary and can be smaller than the bare-form
  plan that would have followed on the prior page; this keeps pages independently decodable and replayable
  across connections. Carrying dictionary-generation state across cursor requests would require more
  cursor state and tie replay to protocol history beyond the source position, so the per-stream rule is
  simpler and safer.
- **Preflight labels with lengths and pin the source facts once.** Snapshot metadata carries the
  resolved incarnation and UTF-8 label byte length, which is all the shared frame-size helper needs;
  the immutable source view pins the corresponding label bytes until the candidate is read. This keeps
  each pending request's row record to 16 B and avoids copying label text into request state. A per-row
  `Arc<str>` in `SnapshotMeta` would make the exact label directly comparable in Server, but adds one
  fat reference per row (about 3 MiB across the 96-request bound) and still needs shared source strings.
  Preflighting with the protocol maximum of 256 B would avoid pins but can refuse a query whose actual
  frame fits. The selected representation is smaller and exact; its cost is a source-view stability
  guarantee, which P5c must implement and bound. If the source cannot pin the exact bytes cheaply, use
  shared label references rather than weaken that guarantee.
- **Derive provenance cardinality from canonical metadata.** WIRE §4.8 requires exactly one object for
  every selected `(inc, metric)` pair, and `SnapshotMeta` already contains the complete subject and
  requested-column sets. A checked product is therefore the exact cell count; a second source-reported
  count duplicates the fact, expands the interface, and can itself be false-low. The production source
  still proves that the resolved sets are complete and that emitted provenance matches them. If the
  protocol ever permits sparse provenance cells, change that contract and then add an independent
  source count; the current all-cells rule has no such need.
- **Choose refusal precedence from facts available at the boundary.** For a configured explicit
  snapshot tier, checked bucket arithmetic is a pure request check, so an out-of-range time wins before
  subject or retention lookup. For `procs`, absence of a published scan wins before subject lookup
  because no complete answer can be produced; once a scan exists, an unknown subject is `not-found`.
  The alternative is to check subject first in both verbs, but that either requires querying incomplete
  or absent source state or returns misleading `not-found` when the process source is unavailable.
- **Report source unavailability and capture fields once.** No or expired process scan is
  `budget {"pool":"procs"}`, not an empty result that falsely means “no processes.” Optional fields,
  including redacted cmdline, are captured into the published scan; serving or replay never rereads
  procfs. A bounded worker policy is required for potentially blocking cmdline reads. Re-reading on
  demand reduces retained bytes but makes pages unstable and can stall the server loop.
- **Use a budget refusal for a legal BLOCK that cannot fit.** If the configured frame cap cannot hold
  the first dictionary-bearing BLOCK of a snapshot or history request, return `budget {"pool":"block"}`
  for that request before values or REP. A history continuation is a new request stream, so its first
  BLOCK is checked with the dictionary even if the previous page's bare-form continuation fit. Requiring
  a global minimum `max_block_bytes` would constrain deployments even when those deployments never issue
  the large query; returning `internal` would mislabel known capacity.

These choices are wire and lifecycle contract, not implementation suggestions. The following typed
interfaces and tables make each observable consequence testable; P5c owns source facts the P5b fixed
backend cannot prove.

Keep responsibilities single-sourced:

- `Conn` owns parsed request state, correlation ordering, REP/BLOCK/END transitions, and the typed
  state needed to resume a request after Query backpressure or while Draining.
- `Server` owns cursor tokens and their bounded canonical-argument bindings, source-view identifiers,
  expiry, and Query rotation position. It derives pending requests from the one `Conn` request-record
  authority; it does not keep a duplicate pending-request map. There is one cursor authority; a backend
  does not mint or interpret wire cursor strings.
- The existing `Backend` stays narrow for identity, status and `current`; the independent `QueryBackend`
  exposes a metadata-only snapshot view, ordered history-group metadata and one-bounded-block reads,
  plus an immutable process scan/page. Neither interface encodes JSON or decides protocol errors. A
  source-view eviction/expiry is an explicit typed outcome, not an empty page.
- `Server` owns exact snapshot/history frame sizing and the series block partition, using one checked
  frame-length helper shared with encoding. `SendHub` owns encoded queued bytes and Query byte pools.
  The Server preflights `query_room(c, exact_frame_bytes)` before every value read, builds at most one
  candidate BLOCK at a time, and queues it directly without a staged encoded block. Retry turns rotate
  by peer uid and request; this limits scheduler preference across uids but does not guarantee
  starvation-free service within one uid. The existing D-W30 rule for `current` remains.
- The P5b-4 driver owns `LISTEN_FDS`, nonblocking socket reads/writes, mode detection plumbing,
  `SO_PEERCRED`/`SO_PEERPIDFD` capture at accept, and configuration. It passes immutable peer facts
  into `Server`; it never supplies identity from REQ JSON and never re-reads a peer by numeric PID
  after accept. WIRE §4.12 also requires the accepted peer's cgroup incarnation. The P5b-4 contract
  must name how the pidfd-backed peer maps to that incarnation and how exit/replacement races refuse;
  P5b-3 only consumes the optional resolved fact, and P5c's process membership scan remains separate.
  Review this resolver boundary before P5b-4 implementation. The core remains sans-IO and fake-clock
  testable.

P5b-3's fixed backend proves decoded wire results and refusal precedence, not production `/proc` or
store behavior. Its behavioral oracles belong to `hostobs:selftest` and `hostobs:assay-r1`; the
Python-only `hostobs:conformance` lane is not evidence for Rust serving behavior. P5b-4 may claim
socket transport acceptance only for the rows and verbs its socketpair fixtures actually execute.
Register every new Rust source module in `hostobs/assay.toml` and maintain the declared crate coverage
floor; do not use `conformance` as a proxy for those tests.

### P5b-3 typed backend contract

P5b-3 adds an independent query seam. The existing `Backend` stays narrow for identity, status and
`current`; `QueryBackend` is called by `Server` for the three new query verbs. Do not make
`Backend: QueryBackend`: the existing `StatusView` adapter forwards core methods and must not become a
blanket forwarding adapter for every query method. `Server` is the sole cursor and retry-scheduler
authority; `Conn` owns wire reply order; `SendHub` owns encoded queue bytes. `query_ready` is a wakeup
hint, not a second producer queue.

The Server's immutable `TierCatalog` is derived from the parsed configuration facts that also produce
`StatusInfo.tiers`. Do not copy tier-step literals into `Limits` or `Server`. Each query validates
configured tier names and nonzero steps against this catalog before a query-view/value call. A config
reload, if later added, replaces the status facts and catalog atomically. The source owns retained
windows, committed watermarks and auto-tier eligibility; P5b checks the returned choice against the
catalog, and P5c proves retention selection against fake store metadata.

```rust
struct SnapshotViewId(u64);
struct SeriesViewId(u64);
struct ProcScanId(u64); // one immutable published acquisition
struct ProcProjectionId(u64); // one filtered projection pinned to a ProcScanId
enum QueryViewId { Snapshot(SnapshotViewId), Series(SeriesViewId), Procs(ProcProjectionId) }
struct SeriesPosition(u64); // ordinal of the next bucket in one immutable, ordered view
struct TierCatalogEntry { tier: Tier, step_ms: u32 }
enum Tier { Hot, Warm, Cold } // wire names: "hot", "warm", "cold"
enum TierChoice { Auto, Explicit(Tier) }
enum QueryFeature { Provenance }
enum ProcField { Comm, Ppid, State, CpuUsec, RssBytes, IoRbytes, IoWbytes, Threads, Cmdline }
struct QueryBounds { max_rows: u32, max_block_buckets: u32, max_block_bytes: u32 }
struct SnapshotRequest { selection: Selection, real_ns: i64, tier: TierChoice, provenance: bool }
struct SeriesRequest {
    selection: Selection, from_real_ns: i64, to_real_ns: i64, step_ms: u32,
    aggs: Vec<Aggregation>,
}
struct ProcsRequest { subject_inc: u64, descendants: bool, fields: Vec<ProcField> }

enum RawNumber { U64(u64), I64(i64), F64(f64) }
struct MonoInterval { start: u64, end: u64 }
struct CellProvenance {
    inc: u64, metric: String, quality: Quality, source: MetricSource,
    source_ref: Option<String>, observed_mono_ns: Option<u64>,
    interval_mono_ns: Option<MonoInterval>, raw_inputs: Option<Vec<RawInput>>,
    constraints: Option<Vec<ConstraintFact>>,
}
struct RawInput {
    metric: String, value_type: MetricValueType, value: Option<RawNumber>, quality: Quality,
    source: MetricSource, unit: MetricUnit, source_ref: Option<String>,
    observed_mono_ns: Option<u64>, interval_mono_ns: Option<MonoInterval>,
}
enum ConstraintValue { U64(u64), I64(i64), F64(f64), String(String) }
struct ConstraintFact {
    name: String, value: ConstraintValue, unit: Option<MetricUnit>,
    source_ref: Option<String>, observed_mono_ns: Option<u64>,
}

enum QuerySourceError {
    NotFound, DataGap, BucketOutOfRange, Unsupported(QueryFeature), Budget(Pool),
    ViewInvalid, Internal,
}
struct SnapshotView { id: SnapshotViewId, meta: SnapshotMeta }
struct SnapshotSubjectMeta { inc: u64, label_len: u16 }
struct SnapshotMeta {
    tier: Tier, bucket_real_ns: i64, step_ms: u32, covered_ms: u32,
    subjects: Vec<SnapshotSubjectMeta>, columns: Vec<QueryColumnMeta>,
}
struct SnapshotData { tick: TickData, provenance: Option<Vec<CellProvenance>> }
struct SeriesView {
    id: SeriesViewId, tier: Tier, step_ms: u32,
    subjects: Vec<u64>, columns: Vec<QueryColumnMeta>, first: Option<SeriesPosition>,
}
struct QueryColumnMeta { metric: String, facts: MetricFacts, value_type: MetricValueType }
struct SeriesGroup { subject_inc: u64, agg: Aggregation, first: SeriesPosition, bucket_count: u64 }
struct BlockPlan { subject_inc: u64, agg: Aggregation, start: SeriesPosition, buckets: u32 }
struct HistoryBlockData {
    subject_inc: u64, agg: Aggregation, buckets: Vec<HistoryBucket>,
    columns: Vec<HistoryColumn>,
}
struct HistoryBucket { bucket_real_ns: i64, covered_ms: u32, samples: u16 }
struct HistoryColumn {
    metric: String, facts: MetricFacts, value_type: MetricValueType,
    values: Vec<u64>, quality: Vec<u8>,
}
struct ProcScanView {
    id: ProcProjectionId, scan_id: ProcScanId, published_mono_ns: u64, row_count: u32,
    scanned: u32, total: u32, complete: bool,
}
struct ProcRow {
    pid: u32, start_ticks: u64, inc: u64, comm: Option<String>, ppid: Option<u32>,
    state: Option<u8>, cpu_usec: Option<u64>, rss_bytes: Option<u64>,
    io_rbytes: Option<u64>, io_wbytes: Option<u64>, threads: Option<u64>,
    cmdline: Option<String>,
}

trait QueryBackend {
    fn snapshot_open(&self, request: &SnapshotRequest, now_ns: u64, bounds: QueryBounds)
        -> Result<SnapshotView, QuerySourceError>;
    fn snapshot_read(&self, view: SnapshotViewId) -> Result<SnapshotData, QuerySourceError>;
    fn series_open(&self, request: &SeriesRequest, now_ns: u64, bounds: QueryBounds)
        -> Result<SeriesView, QuerySourceError>;
    fn series_group(&self, view: SeriesViewId, at: SeriesPosition)
        -> Result<Option<SeriesGroup>, QuerySourceError>;
    fn series_read(&self, view: SeriesViewId, at: SeriesPosition, buckets: u32)
        -> Result<HistoryBlockData, QuerySourceError>;
    fn procs_open(&self, request: &ProcsRequest, now_ns: u64, max_rows: u32)
        -> Result<ProcScanView, QuerySourceError>;
    fn procs_read(&self, view: ProcProjectionId, index: u32, fields: &[ProcField])
        -> Result<Option<ProcRow>, QuerySourceError>;
    fn release_view(&self, view: QueryViewId);
}

trait Backend {
    // Existing identity(), status() and current() operations remain here, unchanged.
}
```

`TierCatalog` uses only the configured `hot`, `warm`, and `cold` entries from `StatusInfo.tiers`; an
unconfigured explicit snapshot tier is `not-found` in Server before `QueryBackend`; a nonzero
`series_batch.step_ms` absent from the catalog is `bad-request` `{"key":"step_ms"}` before
`QueryBackend` is called. For a configured explicit snapshot tier, `snapshot_open` performs only
metadata resolution and returns `BucketOutOfRange` before retention lookup if the checked bucket start
cannot fit `i64`; Server maps it to `bad-request` `{"key":"real_ns"}`. `snapshot_open` may resolve an
`auto` choice from retention metadata but reads no bucket values. It returns a configured tier and
exact step. The Server checks the returned tier and step, computes checked Euclidean bucket floor from
the returned step, verifies the returned bucket, and validates `covered_ms`. For auto, overflow in an
ineligible candidate tier does not mask another eligible tier; no eligible tier is `not-found`.

`SnapshotMeta` contains the independently resolved subjects, the exact requested metric columns and no
bucket values or provenance objects. Subjects are complete and sorted by `inc`, bounded by `max_rows`,
and carry each source label's UTF-8 byte length (`label_len`, at most 256) so the Server can preflight
the exact snapshot dictionary without retaining a label handle per active request. `snapshot_open` pins
the source label facts in its immutable view; `snapshot_read` must return those exact label bytes. The
Server checks each candidate subject id and label byte length against `SnapshotMeta`; content stability
within the view is the backend's source-view guarantee. The source owns and bounds any retained label
storage, while the Server retains only ids and lengths. Across at most 96 active snapshot views the
conservative source-owned label ceiling is `96 × max_rows × 256 B` (96 MiB at default
`max_rows=4096`); P5c may use a tighter shared/versioned representation but must state and test its
explicit bound. These bytes are outside the Server's <56 MiB query-payload bound. The column names
must equal the complete
canonical set of requested metrics; missing, extra or repeated columns are an invalid source view and
map to `Internal` before REP. Columns are sorted `QueryColumnMeta` facts for those selected metrics.
When `provenance=true`, the Server checked-multiplies the independently validated subject and requested
column counts before query admission or value reads. That product is the exact number of required
provenance cells under WIRE §4.8; the backend supplies no duplicate count. If the product exceeds 64,
the Server returns `budget {"pool":"provenance"}` before `snapshot_read` or success REP. The Server computes exact
dictionary-bearing frame length from row
count, columns, subject label byte lengths and the shared checked frame-length helper. If one legal
BLOCK exceeds
`max_block_bytes`, it returns `budget {"pool":"block"}` before values or success REP. It calls
`query_room(c, exact_frame_bytes)` before `snapshot_read`; while waiting it retains only the view and
bounded metadata, with no success REP and no candidate values. Once admitted, `snapshot_read` returns
the bounded TickData plus requested provenance. The Server requires exact subject-id/label-byte-length/column/value-type agreement with
`SnapshotMeta` (the source view guarantees the corresponding exact label bytes), exactly one row per
resolved subject in ascending `inc` order,
and no missing/duplicate/foreign row before queuing success REP then BLOCK.
For requested provenance it checks the count against represented cells and requires complete, unique,
ordered, type-correct, finite, field-bounded and age-valid facts. `provenance=false` requires no
provenance values and emits JSON `null`; `true` emits an array, including `[]` for zero represented
cells. One legal object is at most 3,404 encoded bytes; the 4,096-byte cap is defensive, not an
independently reachable budget condition.

Each query stream uses the canonical dictionary from WIRE D-W44. For `snapshot_at`, metadata IDs,
label lengths and requested column facts determine exact size before values are read. After Query room
is available, Server builds the dictionary from the candidate `TickData`, validates its entity IDs,
label lengths, metric facts, types and canonical order against the independent metadata, and uses that
validated candidate dictionary for the BLOCK. It relies on the source view to pin exact label bytes;
Server does not claim to compare candidate label contents against metadata that contains only lengths.
For `series_batch`, `SeriesView.subjects` is the complete resolved subject list (≤ `max_rows`), in
ascending incarnation order. `SeriesView.columns` must contain exactly one `QueryColumnMeta` for every
requested metric, in canonical order; Server validates that set against the canonical request and
rejects missing, extra or repeated columns as `Internal`. Server derives `omitted` from those metric
aggregation masks and the requested aggs; the source does not supply a second omitted list. It builds
entity entries with empty labels, followed by only metrics permitted by at least one requested agg.
The dictionary stays fixed for the query and its exact encoded bytes are retained as bounded metadata
for planning/replay; the shared frame-length helper includes those bytes on the first BLOCK of each
request stream and excludes them on later BLOCKs. Packing is deterministic for a fixed cursor start and
`max_blocks`. A continuation starts a new stream and replans its first BLOCK with the dictionary, so its
first block may be smaller than the bare-form plan that would have followed in the previous page. The
source view holds references to shared catalog/index facts rather than copying history rows.

For `series_batch`, `SeriesPosition` is a stable zero-based ordinal over data-bearing buckets in the
immutable view, ordered by subject incarnation, request aggregation order, then bucket time. A
`SeriesGroup` describes one `(subject, agg)` run, its first ordinal and its exact bucket count. The
source's group index omits gaps, includes no value arrays, and remains immutable for the view lifetime.
`series_group(view, at)` returns the unique group containing ordinal `at`, including when `at` is in the
middle of a group; it returns `None` only when `at` is the exact terminal ordinal. A position beyond
the terminal ordinal, a zero-length group, or inconsistent group metadata is `Internal`. Within one
view there is at most one group per `(subject, agg)`, groups are ordered by ascending subject and the
request's aggregation order, and the group positions form one contiguous ordinal sequence. The Server
checks these structural claims as it reads the bounded page plan; P5c proves the production source's
group index is complete against committed store facts. `series_read(view, at, count)` accepts only a
nonzero planned range contained in that one group and returns exactly those buckets in ordinal order.
It never crosses a group boundary or returns a shortened result as success. The Server validates
the group order and contiguous positions as it creates `BlockPlan`s: for each group it selects the
largest prefix bounded by `max_block_buckets` whose exact encoded frame size fits `max_block_bytes`.
The same checked frame-length helper is used by metadata planning and final encoding; because column
values and TIME entries are fixed width, exact size depends on dictionary form, column count and
bucket count, not on values. First BLOCK uses the dictionary-bearing form and later BLOCKs in that
request use the bare form. `series_read(view, start, count)` returns exactly one planned block; the
Server validates its subject, aggregation, bucket count/order, metric columns, coverage, samples and
encoded size before queueing it. If the first dictionary-bearing one-bucket plan of this request exceeds
`max_block_bytes`, return `budget {"pool":"block"}` before this request's success REP or value read.
For example, a page may fit a bare-form continuation and return `next`; if that continuation's
dictionary-bearing first BLOCK exceeds the cap, the request using that cursor returns the budget refusal.
P5b fixtures prove this server partition and transfer logic. P5c/HOB-62 proves the production view reports every
eligible committed bucket exactly once, freezes the watermark and all bucket facts across later
same-bucket writes, applies the deterministic tier rule, omits only gaps, and invalidates on any
selected-tier eviction.

Before success REP, the Server performs metadata-only lookahead for at most `max_blocks + 1` block plans
(65 maximum), retaining at most the first 64 fixed-size plans. It reserves a cursor only if the extra
plan proves continuation. Terminal pages need no cursor slot and succeed when the cursor table is full.
Before success REP, the Server uses metadata and the shared checked frame-length helper to establish
the exact dictionary-bearing size of the first planned block and returns `budget {"pool":"block"}` if
it cannot fit the configured maximum. While Query room is unavailable, the request retains only the
immutable view handle, bounded canonical args, block plans, current position and any reserved cursor;
no values or encoded BLOCK bytes are held. After REP, each `series_read` preflights the exact planned
frame length, then produces at most one typed candidate and one encoded BLOCK before handing it
directly to SendHub. Preflight and push happen in the same single-threaded Server turn. A returned
`Backpressure` after exact preflight is `internal`; D-W30's current-tick re-resolution remains the
explicit exception. The planner and encoder call the same checked frame-length helper; code review or
a structural check proves helper reuse, while behavior oracles prove the calculated lengths.

`HistoryBucket.covered_ms` and `samples` are source facts. P5b validates `covered_ms <= step_ms`
and transfers the exact `u16` sample count. The source owns same-segment clock mapping, conservative
bucket assignment, interval union, and proof that sample counts fit `u16`; P5c tests those against fake
clock/store inputs. P5b does not claim those source properties.

`procs_open` selects the newest published immutable `ProcScanId` and creates a distinct immutable
filtered projection for that open request, anchored to the scan and its subject/descendant filter. It
returns `ProcProjectionId` for `procs_read` and the underlying `scan_id`. The Server cursor binding
retains both IDs, so a continuation is tied to this exact filter projection and published scan. Concurrent
filters over the same published scan receive distinct projection IDs; each projection pins its scan
until `release_view`. Publishing a newer scan cannot change an existing projection. Projection handles
reference shared scan rows and do not copy them; each source descriptor remains at most 512 B. At most
257 process projection handles are live (256 cursor-backed plus one synchronous `procs_open`); P5c
separately states and tests a bound for any projection-specific index or membership metadata.
`procs_open` first selects the newest published scan: if no scan exists or it has expired, it returns
`Budget(Procs)` before resolving the subject; it never fabricates an empty scan. With a usable scan, an
unknown subject is `NotFound`, while a known subject with no matching rows is an empty projection.
Acquisition is weakly
consistent because procfs listing and per-process reads are not atomic. `complete` means the bounded
listing and inspection procedure finished without omitted candidates or failed identity/membership
checks; it does not mean all rows coexisted at one instant. Once published, rows, membership, optional
field values and coverage facts are stable for replay. P5b does not rederive membership or descendant
closure. `procs_read` returns one row in ascending `(pid,start_ticks)` order; the Server validates
coverage bounds, row identity/order, requested fields, `comm` (at most 45 UTF-8 bytes, the maximum
lossy expansion of 15 raw bytes), cmdline output bound (4,096 UTF-8 bytes), and reply size before
constructing the REP, then serializes only requested fields one row at a time. The source captures optional fields into the scan;
it never rereads them while serving a page. P5c bounds potentially blocking cmdline reads with its
worker policy, and owns PID listing/selection, identity recheck, cgroup membership verification,
parent-incarnation traversal, field reads and cmdline redaction/truncation. Those are P5c/HOB-62
oracles.

### Source error and phase mapping

The interface uses a closed common error enum to avoid duplicate vocabularies. Each method has the
following allowed outcomes; any other enum variant or budget pool from that method is `Internal` at the
current phase. A metadata gap is represented by an omitted position, never by `DataGap` from series.

| Method | Phase | Allowed source result | Wire mapping |
|---|---|---|---|
| `snapshot_open` | before success REP | `NotFound` | REP `not-found`, `{}` |
| `snapshot_open` | before success REP | `DataGap` | REP `data-gap`, `{}` |
| `snapshot_open` | before success REP | `BucketOutOfRange` | REP `bad-request`, `{"key":"real_ns"}` |
| `snapshot_open` | before success REP | `Unsupported(Provenance)` | REP `unsupported-feature`, `{"feature":"provenance"}` |
| `snapshot_open` / `snapshot_read` | before success REP | `Budget(Rows)` | REP `budget`, `{"pool":"rows"}` |
| Server snapshot sizing | before success REP | minimum legal dictionary-bearing frame exceeds `max_block_bytes` | REP `budget`, `{"pool":"block"}` |
| `snapshot_read` | before success REP | `ViewInvalid` | REP `internal`, `{}`; no client cursor exists |
| `series_open` | before success REP | `NotFound` | REP `not-found`, `{}` |
| `series_open` / `series_group` | before success REP | `Budget(Rows)` | REP `budget`, `{"pool":"rows"}` |
| Server series sizing | before success REP | this request's first dictionary-bearing bucket exceeds `max_block_bytes` | REP `budget`, `{"pool":"block"}` |
| `series_group` | before success REP | `ViewInvalid` | REP `cursor-invalid` if input had a cursor; otherwise REP `internal` |
| `series_read` | after success REP | `ViewInvalid` | END `ok:false cursor-invalid` |
| `series_read` | after success REP | `Budget(Rows)` | END `ok:false budget {"pool":"rows"}` |
| `series_read` | after success REP | `Internal` | END `ok:false internal` |
| `procs_open` | before success REP | `NotFound` | REP `not-found`, `{}` |
| `procs_open` | before success REP | `Budget(Procs)` | REP `budget`, `{"pool":"procs"}` |
| `procs_read` | before success REP | `ViewInvalid` | REP `cursor-invalid` if input had a cursor; otherwise REP `internal` |
| any method | either phase | `Internal`, or a method-inapplicable result above | REP `internal` before success REP; END `ok:false internal` after it |

The two `Budget(Block)` outcomes are generated by Server metadata sizing, not by the backend. The
backend's `Budget` outcomes are restricted to the method-specific pools above. `Unsupported`,
`DataGap` and `BucketOutOfRange` are valid only from `snapshot_open`; `NotFound` is
valid only from the three open methods; `ViewInvalid` is valid only from a read/group on a previously
opened view. A series `Budget(Rows)` after REP preserves already queued BLOCKs. `procs_read` failures
occur while building the single REP; no partial result is emitted.

Before success REP, any new cursor reservation is released if the request fails. A newly opened source
view is released when no existing token references it. An incoming token remains replayable after an
unexposed-successor failure unless the source view was invalidated. After success REP, errors end with
END after the queued BLOCK prefix; an unexposed successor reservation is released. If the source view
is invalid, all tokens bound to it are invalidated and its last reference is released. Every terminal
path calls `release_view` exactly once after the last active request/token reference is gone.
Connection close, fatal close, shutdown and `Server::finish` discard pending request state and any
unexposed cursor reservation, then release active view references exactly once. A previously exposed
cursor remains valid across connection close while its token and source view remain valid; close never
silently revokes an independently issued token.

### Query state and memory accounting

There are at most 256 cursor entries, with the exact 4,325,376 B cursor-payload cap in WIRE §4.2. The
maximum outstanding observe/operator requests are 24 connections × 4 = 96. Each unbound canonical
request is at most 16,384 B; each pending series plan is at most 64 × 24 B plus 512 B fixed state (2
KiB). Server-visible source descriptors are capped at 353 live views (256 cursor-backed plus 96 active
requests without cursors plus one synchronous `procs_open`), at no more than 512 B logical state each
(176.5 KiB); they reference shared immutable store or scan rows and do not copy them. At most 257
process projection handles are live (256 cursor-backed plus one synchronous open). P5c separately
bounds source-owned projection indexes
or membership metadata; those bytes are outside the Server query-payload sum. Every live series view
retains its complete resolved subject
ids and up to 64 metric/omitted facts: at most `8 × max_rows + 12 KiB` per view. This is charged for
all 352 live views, including cursor-backed views. A live snapshot request retains up to `max_rows`
`SnapshotSubjectMeta` records (at most 16 B per subject: `inc`, `label_len` and alignment) and 64
`QueryColumnMeta` facts (up to 12 KiB), for at most `16 × max_rows + 12 KiB` per request. Label bytes
remain pinned by the bounded source view and are not copied or reference-counted per request; P5c
accounts that source-owned storage separately. At most 352 live history dictionaries
are charged at `align8(8 + 12 × max_rows + 74 × 64)` bytes each (dictionary header, empty-label entity
entries and 64-byte metric names). Snapshot dictionary bytes are transient and covered by the one
candidate/encoded-frame pair; they are not retained while waiting for Query room.

For the default `max_rows=4096`, the conservative components are 4.125 MiB of cursor payload, 1.5 MiB
of active request args, 192 KiB of active plans/state, 176.5 KiB of source descriptors, 15.125 MiB of
series-view metadata for all 352 views, 7.125 MiB of active snapshot metadata for 96 requests, 18.1
MiB of history dictionary bodies, one typed candidate up to 5 MiB (including bounded snapshot data
and at most 64 provenance objects), one encoded Query frame up to 4 MiB and one synchronous process REP
up to 448 KiB plus its 12-byte frame header. Their sum is below 56 MiB before encoder scratch. If
`max_rows` differs, the stated formulas replace
the default calculation. This excludes the independently bounded 48 MiB process-scan cache, shared
store/tier data and existing control/Query queue caps. These are logical payload bounds, not total RSS
claims.
