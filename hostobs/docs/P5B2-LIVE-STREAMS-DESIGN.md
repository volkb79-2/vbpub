# hostobs probe serving, P5b-2 (live streams): design, resolutions and package split

Status: reviewed 2026-11-18 by an independent Opus reviewer (verdict FIX); every finding and the
controller's decisions are folded in (section 8 lists them). Continues
`P5B-SERVER-DESIGN.md` (rules R1..R28; this file's rules are S1..S34) on the P5b-1b head `9ce8d228a`.
Normative protocol: `WIRE.md` (sections quoted as §n). Where this file and WIRE.md disagree, this file
names the WIRE.md edit (section 6) and the package that owns it. Nothing here was compiled.

## 1. Scope and shape

P5b-2 serves `current`, `subscribe` and `unsubscribe` (WIRE §12 P5b, §4.6, §4.7) through the ONE
`SendHub` of the `Server`: shared selection streams (one bare encoding per tick, the dictionary form
lazily, one generation counter per selection, §6.2), the §5.1 subscription and selection budgets, the
Live and Query pools, `budget` errors with their pool, `max_outstanding` / `too-many-requests` and
"`corr` in use" (R3: now reachable), `END` reasons `complete`, `unsubscribed`, `client-eof`, and the
§11.4 cases that become reachable. The probe still has NO collector: the data source is the
`Backend` trait (`Backend::current(&Selection, max_rows)`), with `FixedBackend` as the test backend.

The shape of P5b-1 is kept: sans-IO `Conn` / `Server`, the backend injected, every time an injected
`now_ns`, every map a `BTreeMap` (determinism). New pieces:

- `selection.rs` (pure): the checked `Selection` and its canonical form (the stream key).
- `tick.rs` (pure): tick validation, the bare block encoder, the dictionary body, the infallible
  dictionary splice, the `LiveTick` (`hub::TickForms`) and the `current` query block.
- `registry.rs` (pure state, owned by `Server`): `SelectionStreams`, the shared selection streams,
  their subscribers, generations and the distinct-selection accounting.
- `Conn` gains its subscriptions (`subs`) and its outstanding requests (`pending`); `Server` gains
  `on_tick(now)` and a retry pass for outstanding `current` requests.

**Not in P5b-2:** a collector or sampler (later package; `Backend::current` is its seam), `snapshot_at`,
`series_batch`, `procs` (P5b-3), END reason `page`, the `issuer`/`peer_identity` (P5b-3), socketpair
runs, `LISTEN_FDS` drivers and `hostobsd.toml` parsing (P5b-4), a rate limit on `subscribe` (not in
WIRE; S9 makes it unnecessary), moving the serving core into a crate (backlog, design §1).

## 2. Package cut

Four blind-written packages, stacked: 2a -> 2b -> 2c -> 2d (each needs the previous one's API; each
review is bound to its own head). 2a and 2b merge on their own; **2c and 2d merge together** (2c alone
would have no tick delivery and no retry pass; the `served` flip lives in 2d). Each is
sized for one implementer with ~200k tokens: it reads this file, the WIRE sections it names and the
modules it changes; nothing else. Each package adds its new files to `assay.toml` `judge.targets`
(R26), adds one `CHANGES.md` "Added (P5b-2x ...)" entry in the P5b-1 style, updates the `HOB-54` row of
`KNOWN_ISSUES_TODO_BACKLOG.md` (still OPEN) and makes the WIRE.md edits section 6 assigns to it.

### P5b-2a: request side (pure functions)

Modules: NEW `bins/probe/src/selection.rs`; `args.rs`, `classify.rs`, `codes.rs`, `encode.rs`,
`limits.rs`, `lib.rs` (`pub mod selection;`), `conn.rs` (compile-only: the new `Call` arms, S30).
Depends on P5b-1a/1b APIs only (`Obj`, `unescape`, `classify`, `Detail`, `control_frame`, `Limits`).

```rust
// selection.rs
pub const MAX_ENTITIES: usize = 16;      // §4.2
pub const MAX_METRICS: usize = 64;       // §4.2
pub const MAX_PATH_BYTES: usize = 512;   // decoded
pub const MAX_NAME_BYTES: usize = 64;
/// Canonical order = the derived Ord: variant Inc < Path < Prefix < All, then value
/// (u64 numeric, strings bytewise: `str` Ord is bytewise).
#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub enum Sel { Inc(u64), Path(String), Prefix(String), All }
/// Canonical: both vectors sorted ascending and free of duplicates; only `Selection::new` builds one.
#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct Selection { entities: Vec<Sel>, metrics: Vec<String> }
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SelectionError { pub key: &'static str } // a schema path, S2
impl Selection {
    pub fn new(entities: Vec<Sel>, metrics: Vec<String>) -> Result<Selection, SelectionError>;
    pub fn entities(&self) -> &[Sel];
    pub fn metrics(&self) -> &[String];
}
pub fn is_metric_name(text: &str) -> bool;  // ^[a-z][a-z0-9_.-]{0,63}$
// args.rs
// array() as typed slices: the element's JSON kind (first byte: { [ " digit/- t f n) and its bytes
impl<'a> Obj<'a> { pub fn elements(&self, name: &str) -> Result<Vec<(Kind, &'a [u8])>, ArgError>; }
pub fn selection(object: &Obj<'_>, name: &str) -> Result<Selection, ArgError>;
pub fn current(args: &[u8]) -> Result<Selection, ArgError>;
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SubscribeArgs { pub selection: Selection, pub interval_ms: u32 }
pub fn subscribe(args: &[u8], rule: IntervalRule) -> Result<SubscribeArgs, ArgError>;
pub fn unsubscribe(args: &[u8]) -> Result<u32, ArgError>;
// limits.rs
pub const MAX_INTERVAL_MS: u32 = 60_000;
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct IntervalRule { pub min_ms: u32, pub tick_ms: u32 }   // Default refuses every interval
impl IntervalRule { pub fn accepts(self, interval_ms: u32) -> bool; }  // S8
impl Limits { pub fn interval_rule(&self) -> IntervalRule; }
// Limits gains: pub max_uid_selections: u32 (4), pub max_selections: u32 (8); 0 at non-block
// levels; NOT in hello.limits (HELLO_KEYS stays 19); doc names the hostobsd.toml keys.
// classify.rs
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct ConnFacts { pub corr_in_use: bool, pub outstanding_full: bool, pub interval: IntervalRule }
// outstanding_full refuses only Shape::Block verbs (current, subscribe): S5, controller decision Q6
pub fn classify_with(query: &Query<'_>, facts: &ConnFacts) -> Result<Dispatch, Refusal>;
pub fn classify(query: &Query<'_>) -> Result<Dispatch, Refusal>; // = classify_with(query, &ConnFacts::default())
pub enum Call { Hello(HelloArgs), Status, Current(Selection), Subscribe(SubscribeArgs), Unsubscribe(u32) }
// codes.rs
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub enum Pool { Subscriptions, UidSelections, Selections, Rows, Spans } // names: "subscriptions",
impl Pool { pub const fn name(self) -> &'static str; }                  // "uid-selections", "selections", "rows", "spans"
pub enum Detail { Empty, Key(String), Pool(Pool) }                      // Pool -> {"pool":"<name>"}
// encode.rs
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EndReason { Complete, Unsubscribed, ClientEof }                // Page arrives with P5b-3
impl EndReason { pub const fn name(self) -> &'static str; }
pub fn end_ok(corr: u32, blocks: u32, dropped: u32, reason: EndReason) -> Vec<u8>;
pub fn end_error(corr: u32, blocks: u32, dropped: u32, code: Code, detail: &Detail) -> Vec<u8>;
pub fn current_result(tick_ms: u32) -> String;                      // {"stream":{"tick_ms":T}}
pub fn subscribe_result(sub: u32, interval_ms: u32) -> String;      // {"sub":C,"interval_ms":V}
pub const UNSUBSCRIBE_RESULT: &str = "{}";
```

The verb table is NOT changed in 2a (`served` stays false for the three verbs: hello.verbs must not
announce what no `Conn` executes); the new `call_of` arms are tested directly in `classify.rs`'s own
test module. Tests: section 5.1. WIRE edits: §4.2 (S1, S2; precedence steps S4, S5 implemented), §4.7 interval (S8),
§10 `detail` (unchanged list, `spans` reserved).

### P5b-2b: data side (backend seam, tick encoding, registry, hub additions)

Modules: `backend.rs`; NEW `tick.rs`, NEW `registry.rs`; `crates/proto/src/hub.rs` (S12 stamp fix, S21 `dropped_blocks`, S7 `query_room`);
`server.rs` compile-only (`StatusView` forwards `current`). Depends on 2a (`Selection`, `Pool`, `Code`).

```rust
// backend.rs (Eq is required: FixedBackend derives Eq and will hold TickData)
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TickData {
    /// boot_id, epoch, seq, mono_start_ns, mono_end_ns, boot_ns, real_ns are taken as given;
    /// dict_gen, n_rows, dropped and flags are overwritten by the server (S16).
    pub header: BlockHeaderFacts,
    /// The dictionary of this tick: index i of a row's `ent` or a column's `metric` is entries[i].
    pub entries: Vec<Entry>,
    pub rows: Vec<Row>,
    pub columns: Vec<Column>,
}
impl TickData {
    /// Deterministic sample for tests and the P5b-4 bring-up: one Entity per inc (label "inc-<n>"),
    /// then one Metric per name (unit/scope/source fixed, MetricKind::Gauge, U64), rows in inc order,
    /// value = inc.wrapping_mul(1000).wrapping_add(seq), quality 0, header seq = seq, other times 0,
    /// boot_id = zeros, epoch 1.
    pub fn example(seq: u64, incs: &[u64], metrics: &[&str]) -> TickData;
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BackendError { Unavailable, TooManyRows }
pub trait Backend {
    fn identity(&self) -> Identity;
    fn status(&self) -> StatusInfo;
    /// The latest tick's data for `selection` (resolution is the backend's: §4.2 has none). The
    /// backend stops resolving at `max_rows + 1` rows and answers Err(TooManyRows) (S6): a request
    /// never costs more than max_rows + 1 rows of work.
    fn current(&self, selection: &Selection, max_rows: u32) -> Result<TickData, BackendError>;
}
// FixedBackend gains `pub current: BTreeMap<Selection, Result<TickData, BackendError>>`;
// a selection that is not a key answers Err(Unavailable); a stored Ok whose rows exceed max_rows
// answers Err(TooManyRows) (the fixed backend models the contract). The server still re-checks
// rows.len() <= max_rows (S16) so a non-conforming backend cannot pass more.
// backend.rs module doc lines 13-16 are rewritten: dropped_blocks comes from the hub (S21).
```

```rust
// tick.rs
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct BlockBounds { pub max_rows: u32, pub max_block_bytes: u32 }
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TickError { Rows, Invalid, Exhausted, Oversize, Encode }
impl TickError { pub fn refusal(self) -> (Code, Detail); } // Rows -> (Budget, Pool(Rows)); others -> (Internal, Empty)
pub fn validate(data: &TickData) -> Result<(), TickError>;                       // S16
pub fn dictionary_body(generation: u32, entries: &[Entry]) -> Result<Vec<u8>, TickError>; // S16
/// The frame length encode_bare would produce, plus 8 + dict_body_len when Some: computed from the
/// section lengths (12 + 4 + 8 n_sec + 80 + 8 n_rows (rows, when n_rows > 0) + align8(8 + 9 n_rows)
/// per column), all checked; None on overflow (treated as Oversize). Pinned equal to the real
/// encoders' lengths by test. Judged BEFORE any encode (S6, S11).
pub fn encoded_len(data: &TickData, dict_body_len: Option<usize>) -> Option<usize>;
pub fn encode_bare(data: &TickData, generation: u32) -> Result<Vec<u8>, TickError>;      // corr 0
pub fn with_dictionary(bare: &[u8], dict_body: &[u8]) -> Vec<u8>;                 // S14, infallible
pub fn query_block(data: TickData, bounds: BlockBounds) -> Result<Arc<[u8]>, TickError>; // S6
pub struct LiveTick { /* generation: u32, bare: Option<Arc<[u8]>>, base: Arc<[u8]>,
                         dict_body: Arc<[u8]>, dict: std::cell::OnceCell<Arc<[u8]>> */ }
impl LiveTick {
    /// `bare` is None on a tick that sets a generation; `base` is the bare bytes in every case.
    pub(crate) fn new(generation: u32, bare: Option<Arc<[u8]>>, base: Arc<[u8]>, dict_body: Arc<[u8]>) -> LiveTick;
    pub fn generation(&self) -> u32; pub fn has_bare(&self) -> bool; pub fn dict_built(&self) -> bool;
}
impl TickForms for LiveTick { /* bare(): bare.clone(); dict_form(): dict.get_or_init(splice) */ }
```

```rust
// registry.rs
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)] pub struct SelId(pub u64);
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)] pub struct SubKey { pub conn: ConnId, pub corr: u32 }
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct StreamLimits { pub bounds: BlockBounds }        // the unified observe/operator bounds (S20)
/// The selection caps of the REQUESTING connection's level (S19, finding 14).
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SelectionCaps { pub per_uid: u32, pub per_probe: u32 }
#[derive(Clone, Copy, Debug, Eq, PartialEq)] pub enum Admit { Joined(SelId), Created(SelId) }
#[derive(Clone, Copy, Debug, Eq, PartialEq)] pub enum Refused { Budget(Pool), Duplicate, Exhausted }
pub enum Delivery {
    Push { tick: LiveTick, to: Vec<SubKey> },              // the due subscribers, ascending
    End { to: Vec<SubKey>, code: Code, detail: Detail },   // every subscriber; already removed
}
pub struct SelectionStreams { /* limits, next_id: u64, streams: BTreeMap<SelId, Stream>,
    by_selection: BTreeMap<Selection, SelId>, by_sub: BTreeMap<SubKey, SelId>,
    uid_refs: BTreeMap<u32, BTreeMap<SelId, u32>> */ }
// private: struct Stream { selection, generation: u32 (0 = nothing built), entries: Vec<Entry>,
//   dict_body: Option<Arc<[u8]>>, subscribers: BTreeMap<SubKey, Sub> }; struct Sub { uid, every, left }
impl SelectionStreams {
    pub fn new(limits: StreamLimits) -> SelectionStreams;
    pub fn subscribe(&mut self, key: SubKey, uid: u32, every: u32, selection: &Selection, caps: SelectionCaps) -> Result<Admit, Refused>; // S9, S19
    pub fn unsubscribe(&mut self, key: SubKey) -> bool;        // false when unknown
    pub fn release_conn(&mut self, conn: ConnId) -> usize;     // removes every key of conn; idempotent
    pub fn on_tick(&mut self, backend: &dyn Backend) -> Vec<Delivery>; // S10, S11
    pub fn live_selections(&self) -> usize;
    pub fn uid_selections(&self, uid: u32) -> usize;
    pub fn subscribers(&self) -> usize;
    pub fn generation(&self, id: SelId) -> Option<u32>;
    pub fn selection_of(&self, key: SubKey) -> Option<&Selection>;
}
```

```rust
// crates/proto/src/hub.rs additions
impl SendHub {
    /// Every counted drop since `new`: each increment of any stream's pending_drops (coalesce,
    /// shed, dropped incoming, the unpinned tick end_stream removes). drain_unpinned/remove count none.
    pub fn dropped_blocks(&self) -> u64;
    /// Whether push_query(id, _, block of len) would be Queued now (the conn, uid and global Query
    /// pools all fit len); true for an unknown id (the retry is discarded as Queued).
    pub fn query_room(&self, id: ConnId, len: usize) -> bool;
}
// and S12: a Query entry is stamped dropped = 0 at pin and leaves pending_drops untouched.
```

Tests: section 5.2. WIRE edits: §2.2 END ok:false codes (S11), §4.5 `dropped_blocks` (S22), §5.3 pin
rule and the two hub functions (S12), §6.2 generation life cycle (S10), §11.4 hub table (20 files, the
new row). Hub fixtures (S12): no committed case mixes Query and Live on one `(c, s)`, so no existing file
changes; 2b ADDS the case `hub-query-pin-after-corr-reuse` to `python/reference/make_hub_fixtures.py`
(its model stamps a Query pin 0 without resetting `pending_drops`), commits the generated
`fixtures/stream/hub-query-pin-after-corr-reuse.json`, and bumps `test_exactly_the_19_cases` (and its
docstring, line 9) in `python/tests/test_hub_fixtures.py` to 20; `crates/proto/tests/hub_fixtures.rs`
replays it with the others.

### P5b-2c: the connection (subscribe lifecycle, current, outstanding, Draining)

Modules: `conn.rs`; `server.rs` minimal: field `streams: SelectionStreams` built from
`ServerLimits::stream_limits()` (S20), `unified()` also unifies `tick_ms`, `max_rows` and
`max_block_bytes` (S20), `drive` passes the registry, `on_eof` goes through `drive`, `finish` calls
`streams.release_conn(id)` before `hub.remove` (S18). `verbs.rs` is NOT changed here (the `served` flip
is 2d's): 2c's Conn tests reach the new calls by building a `Dispatch` and calling the private
`execute` from `conn.rs`'s own test module, and reach steps 2/10 through `on_bytes` (they precede the
served check). 2c rewrites the two `status` tests (conn.rs ~1456 and ~1596) that set
`backend.status.dropped_blocks = 5` and expect 5: the value now comes from the hub (S21). Depends on 2a, 2b.

```rust
// conn.rs (new or changed; P5b-1b methods not listed are unchanged)
pub fn on_bytes(&mut self, bytes: &[u8], now_ns: u64, hub: &mut SendHub, backend: &dyn Backend, streams: &mut SelectionStreams);
pub fn on_eof(&mut self, now_ns: u64, hub: &mut SendHub, streams: &mut SelectionStreams);
pub fn push_tick(&mut self, corr: u32, tick: &LiveTick, now_ns: u64, hub: &mut SendHub);           // S13
pub fn end_subscription(&mut self, corr: u32, code: Code, detail: &Detail, now_ns: u64, hub: &mut SendHub); // S17
pub fn retry_pending(&mut self, now_ns: u64, hub: &mut SendHub, backend: &dyn Backend);           // S7
pub fn subscriptions(&self) -> usize;   pub fn outstanding(&self) -> usize;  pub fn has_pending(&self) -> bool;
// private state: subs: BTreeMap<u32, SelId>; pending: BTreeMap<u32, Pending>;
// struct Pending { selection: Selection, need: usize }
// status_json gains `dropped: u64` (= hub.dropped_blocks(), S22).
// server.rs: fn drive<F: FnOnce(&mut Conn, &mut SendHub, &dyn Backend, &mut SelectionStreams)>
impl ServerLimits { pub fn stream_limits(&self) -> StreamLimits; }   // S20
impl<B: Backend> Server<B> { pub fn streams(&self) -> &SelectionStreams; }
```

Tests: section 5.3 (Conn-level, against a real `SendHub`, `SelectionStreams` and `FixedBackend`,
`registry.on_tick` called directly to produce ticks). WIRE edits: §4.6
(S6, S7), §4.7 (S9, S15, S23), §5.1 outstanding (S5), §5.4 rows (S23), §13 D-W29, D-W30.

### P5b-2d: the server (tick delivery, retries, reachable §11.4 cases)

Modules: `server.rs`; `verbs.rs` (`served = true` for `current`, `subscribe`, `unsubscribe`; hello.verbs
at observe/operator then lists them, S32); `bins/probe/tests/serving_hostile.rs` (server-level hostile
runs with the block verbs). Depends on 2c; merges together with 2c.

```rust
impl<B: Backend> Server<B> {
    /// One sampler tick (S11, S25): registry.on_tick(&self.backend), then every Delivery through
    /// `drive` in order: Push -> push_tick per key, End -> end_subscription per key.
    pub fn on_tick(&mut self, now_ns: u64);
}
// wrote(id, n, now) and poll(now) end with the retry pass (S7): for every conn with has_pending(),
// ascending id, drive(id, retry_pending).
```

Tests: section 5.4 (incl. every §11.4 case of section 5.5). WIRE edits: §4.3 note (hello.verbs, S32),
§11.4 (S31), §5.4 driver note
(S25), design R3/R21 superseded notes, `HOB-54` row; the series gets the Opus final review (design §2).

**Implemented with P5b-2c + P5b-2d.** The merged package delivers shared ticks through
`Server::on_tick`, runs the pending-`current` retry pass after `wrote` and `poll`, serves the three
block verbs and updates `hello.verbs`, and adds the server-level section 5.4/5.5 tests including
the block-verb hostile run. P5b-3 and P5b-4 remain, so `HOB-54` stays open.

## 3. Resolutions (S1..S34)

**S1 Selection parsing (§4.2).** `args::selection(obj, "selection")`: `selection` is an object with
`only(["entities","metrics"])`; then `entities`, then `metrics` (schema order; the first defect wins).
Arrays come from `Obj::elements` (at most 64 elements, R4), each with its `Kind`; the kind is checked
BEFORE any decoding, because `unescape` (args.rs:349) strips the first and last byte without checking
for quotes (`true` would decode to `ru`). Entities: count 1..16 else `selection.entities`; each element,
in array order, must be of kind `Object` (else `selection.entities`) and is parsed with
`Obj::parse(element, "selection.entities")`; exactly one member else `selection.entities`, `only(["inc","path",
"prefix","all"])` (unknown key -> `selection.entities.<key>` under R14), `inc` an exact u64
(`selection.entities.inc`), `path`/`prefix` a string decoded by `unescape(raw, 512)` (refused surrogates,
over 512 decoded bytes, 0 bytes, any char < 0x20 or 0x7F -> `selection.entities.path` / `.prefix`), `all`
of kind `True` (anything else -> `selection.entities.all`). Metrics: count 1..64 else `selection.metrics`;
each element of kind `Str` (any other kind -> `selection.metrics`), decoded by `unescape(raw, 64)`, matching `is_metric_name` on the DECODED text, else
`selection.metrics` (S1, the kind check comes first). Then `Selection::new` sorts both vectors and refuses an adjacent duplicate with
`selection.entities` / `selection.metrics`. `Selection::new` re-checks every content rule (counts,
lengths, controls, names, duplicates) so a test or backend cannot build a non-canonical value; its
`SelectionError.key` uses the same paths. Duplicates compare DECODED values (`"a"` and `"a"` are
one metric). `{"all":true}` together with other selectors is legal (a distinct canonical form).
**S2 Canonical form = the `Selection` value** (derived `Ord`/`Eq` on the sorted vectors). No resolution:
`{"inc":5}` and the path of incarnation 5 are distinct streams (§4.2). The registry keys on it.

**S3 Stream id = `corr`.** The hub `stream` of a `current` is its request `corr`, of a subscription the
`subscribe` `corr`. Every block is encoded with `corr` 0 and `dropped` 0; the hub stamps both at pin
(D-W7). One rule for query and live blocks.
**S4 `corr` in use (§2.3, R11 step 2).** Binary mode: `ConnFacts.corr_in_use` is true when the frame
`corr` is a key of `subs` or of `pending`. `classify_with` refuses it exactly like `corr` 0 (`bad-frame`,
REP `corr` 0, fatal), before `check_req`. JSON mode: always false (no block verb, nothing outstanding).
A `corr` is free again once its final REP or END is queued; reuse after that is legal (§2.3) and S12
keeps it exact.
**S5 Outstanding (§5.1, R11 step 10).** `outstanding = pending.len()`. In P5b-2 only a `current` whose
block met `Backpressure` is outstanding. `ConnFacts.outstanding_full = pending.len() >= max_outstanding`;
a REQ of a **BLK-shape verb** (`current`, `subscribe`; later `snapshot_at`, `series_batch`) then answers
`too-many-requests` (non-fatal, `detail {}`), after step 9 and before the argument schema. JSON-shape
verbs (`status`, `unsubscribe`, ...) are never refused by it: they never become outstanding, and a
uniform refusal would make Python `Subscription.close()` raise while 4 `current`s are pending
(controller decision Q6). JSON mode: always false.

**S6 `current` (§4.6).** After `classify`: `backend.current(&sel, max_rows)`: `Err(TooManyRows)` -> REP
`budget {"pool":"rows"}`, `Err(Unavailable)` -> REP `internal`; then `tick::query_block(data, bounds)`
with the conn's own (unified, S20) `max_rows` and `max_block_bytes`: `Rows` -> REP `budget
{"pool":"rows"}`, other errors -> REP `internal` (all before any REP ok, §2.4). Else push REP
`current_result(tick_ms)` (`tick_ms` of the conn's level, unified by S20); if that push closed the conn,
stop (S27); then `push_query(id, corr, block)`: `Queued` -> `counts = end_stream(id, corr)`, push
`end_ok(corr, counts.blocks, counts.dropped, Complete)` (1 and 0). `Backpressure(_)` -> the bytes are
dropped and `pending.insert(corr, Pending { selection, need: len })`. `query_block` = the S11 build
with its own generation 1, always dictionary-bearing (a query stream numbers its own, §6.2), checks in
the order rows, `validate`, dictionary body, size (`encoded_len(&data, Some(body.len()))` is `None` or
`> max_block_bytes` -> `Oversize`, judged arithmetically BEFORE any block encode), then encode.
Invariant: `max_block_bytes <= query_queue_bytes` (S20), so a block that passes always fits an empty
Query pool and a pending request always becomes queueable once its own connection's pool drains.
**S7 Pending retry.** `retry_pending` (called by the server's retry pass after every `wrote` and at the
end of every `poll`, S27) handles `pending` in ascending `corr`: when `hub.query_room(id, need)` it re-runs
the S6 pipeline from `backend.current(&sel, max_rows)` (the latest tick NOW: the request holds no block
bytes, D-W30):
`Queued` -> END `complete`, entry removed; `Backpressure` -> `need` = the new length; a failure (backend,
rows, invalid) -> `counts = end_stream`, push `end_error(corr, counts.blocks, counts.dropped, code,
detail)` (the REP was already sent, §2.4), entry removed. A closed conn retries nothing, and the pass
stops at the first push that closes it (S27). Draining keeps retrying.
**S8 Interval (§4.7).** `IntervalRule::accepts(v)`: `tick_ms > 0 && v % tick_ms == 0 && v >= max(min_ms,
tick_ms) && v <= 60_000`; otherwise `bad-request {"key":"interval_ms"}` (step 11). Never rounded: both
clients require `result.interval_ms` to equal the request (Rust `confirms`, Python `subscribe`).
`every = v / tick_ms >= 1`. `ConnFacts.interval = limits.interval_rule()`; the default rule refuses all.
**S9 `subscribe` (§4.7).** Execution order: (1) `subs.len() >= max_subscriptions` -> REP `budget
{"pool":"subscriptions"}`; (2) `streams.subscribe(SubKey{id, corr}, peer.uid, every, &sel, caps)` with
`caps = SelectionCaps { per_uid: limits.max_uid_selections, per_probe: limits.max_selections }` of the
conn's OWN level (finding 14):
`Err(Budget(p))` -> REP `budget {"pool":p}`, `Err(Duplicate | Exhausted)` -> REP `internal`; (3) `Ok(admit)`
-> `subs.insert(corr, sel_id)`, push REP `subscribe_result(corr, v)`. NO backend call, no encoding and no
block at subscribe: the first block (creator and joiner alike) comes from the next tick (S10). If the REP
push closes the conn (`slow-client`), the registry entry stays until `finish` (S18).
**S10 Schedule.** A subscriber starts with `left = 1`. Each `on_tick` first sets `left =
left.saturating_sub(1)` for every subscriber of every stream; `left == 0` is due. A stream with no due
subscriber is not built and the backend is not called. A due subscriber that is pushed gets `left =
every` whatever the hub outcome (`Queued`, `Coalesced`, `DroppedIncoming`); a backend `Err(Unavailable)` skips the
stream and its due subscribers stay due. So the first block follows the REP within one tick, interval
`every` ticks thereafter.
**S11 Shared selection stream and tick build (§6.2).** Created by the first `subscribe` of a selection
that is not live (`generation = 0`, nothing built); destroyed when its last subscriber leaves (any
path); a recreated stream starts again at generation 1. Build of a due stream, in this order, first
failure wins: (0) `backend.current(&sel, max_rows)`: `Err(TooManyRows)` -> `TickError::Rows`,
`Err(Unavailable)` -> skip (S10); (1) `rows.len() > max_rows` -> `TickError::Rows` (re-check); (2)
`validate`; (3) change = `generation == 0 || data.entries != held entries` (`Vec<Entry>` equality, order
included); on change the new generation is 1 (first) or `generation.checked_add(1)` (`None` at `u32::MAX`
-> `Exhausted`), and `dict_body = dictionary_body(new, &entries)` (else the held body); (4)
`encoded_len(&data, Some(dict_body.len()))` `None` or `> max_block_bytes` -> `Oversize`, judged BEFORE any
encode; (5) `base = encode_bare(&data, gen)`; (6) commit
generation, entries and `dict_body`, then `LiveTick { generation, bare: None on change else Some(base),
base, dict_body, dict: empty }`. A failure ends the stream: the registry removes the stream and ALL its
subscribers (not only the due ones, with their uid refs) and returns `Delivery::End { to: every key,
code, detail }` from `TickError::refusal()` (`budget rows` or `internal`). Streams are built in `SelId`
order, deliveries returned in that order.
**S12 Hub pin of a Query entry (proto change).** At pin a Query entry is stamped `dropped = 0` and the
stream's `pending_drops` is NOT reset (`Stream::stamp(false)` returns 0 without `take`). Reason: after a
`current` on `corr` X ended (END queued) its Query block may still be queued; a legal new `subscribe`
on X creates fresh hub state, and the old block's pin would otherwise carry the new stream's drops (and
reset them), breaking "every drop counted exactly once". For a real query stream `pending_drops` is
always 0, so nothing else changes.
**S13 Forms and coalescing are the hub's.** `push_tick(corr, tick)` calls `hub.push_live(id, corr, tick,
now)` for every due subscriber, never filters a lagging one, and calls `timers.on_enqueue()`. Coalescing,
`dropped` stamping at pin, `needs_dict` after a dropped or replaced dictionary-bearing entry, the first
block dictionary-bearing (`last_pushed_gen` 0) and the interval that skipped a generation change are §5.3
rules already implemented and tested in `hub.rs`. `push_tick` does nothing when the conn is closed,
Draining or `corr` is not in `subs` (a registry/conn mismatch is ignored, never a panic).
**S14 Encodings per tick.** Bare: once per built tick (`encode_bare`, eager). Dictionary body: once per
generation (eager, at the change). Dictionary form: `with_dictionary(base, dict_body)` lazily, at most
once per `LiveTick` (a `OnceCell` shared by every subscriber of that tick). `with_dictionary` computes
`total = bare.len() + 8 + body.len()` (checked; and `n_sec.checked_add(1)`: either `None` -> return the
input unchanged, unreachable after S11), writes `u32::try_from(total).unwrap_or(u32::MAX)` at 0, copies
bytes 4..12, writes `n_sec + 1` at 12, copies 14..16 and directory entry 0
(the header), inserts the entry `kind 5, ver SectionKind::Dictionary.version(), flags 0, len body.len()`,
copies the remaining entries, the header body (80 bytes at `16 + 8 n_sec`), the dictionary body, then the
rest; every write uses `get`/`get_mut` (no panic, a short input yields a short output, unreachable after
S11). Pinned by a test: `with_dictionary(encode_bare(d, g), dictionary_body(g, e)) == encode_block(the same
block with the interned dictionary)` byte for byte.
**S15 `unsubscribe` (§4.7).** `{"sub": u32}`. `sub` not in `subs` (0, a query `corr`, an already ended
subscription) -> REP `not-found` (both clients tolerate it). Else `subs.remove`, `streams.unsubscribe`,
push REP `{}` on the request `corr`, `counts = hub.end_stream(id, sub)`, push `end_ok(sub, counts.blocks,
counts.dropped, Unsubscribed)`. The race "tick queued while unsubscribing": the unpinned tick is removed
by `end_stream` and counted in `dropped`; a pinned one is written before the END and counted in
`blocks`. In JSON mode `unsubscribe` is a served JSON verb (not `binary-required`) and always answers
`not-found`.
**S16 Tick validation and header.** `validate`: every row `ent < entries.len()` and an `Entity`; every
column `metric < entries.len()` and a `Metric` whose `facts.kind == column.kind`, `column.rows() ==
rows.len()`, metric indices pairwise distinct, every quality byte a known code (`Quality::from_code`);
`rows.len()` fits u32. `dictionary_body`: interns `entries` in order into
`Dictionary::with_capacity(gen, DEFAULT_ACTIVE_MAX)`, each `intern` must return its own position (a
repeated identity returns an earlier index: `Invalid`), then `encode()`. Every `intern`/`encode` error
(`DictionaryEntryTooLong`, `DictionaryEntryControl`, `DictionaryTooLarge`, `ConflictingColumn`, any
other `ProtoError`) maps to `TickError::Invalid`, which surfaces as `internal`; an `encode_block` error
in `encode_bare` maps to `TickError::Encode` (also `internal`). The server writes header
`dict_gen = generation`, `n_rows = rows.len()`, `dropped = 0`, `flags = 0`, `corr = 0`; every other header
field is the backend's (trusted; the collector package owns their truth).
**S17 Server-ended stream.** `end_subscription(corr, code, detail)`: `subs.remove(&corr)`; on a closed
conn that is all (no hub call, no push); else, when it was Some: `counts = end_stream`, push
`end_error(corr, counts.blocks, counts.dropped, code, detail)`. Else nothing. The registry already
forgot the keys.
**S18 Every close path releases the registry (R22 extended).** `Server::finish(id)` calls
`streams.release_conn(id)` (idempotent) before `hub.remove`. A closed but unfinished conn keeps its
registry entries and selection slots until `finish`; ticks skip it (S13). Shutdown and fatal closes send
no END (§2.2, §5.4).
**S19 Distinct selections (§4.7, §5.1).** `uid_refs[uid][sel]` = that uid's subscriptions on `sel`; a
uid's distinct count is the inner map's length, across levels (the registry is server-wide). Order in
`SelectionStreams::subscribe(.., caps)`: key already present -> `Duplicate` (unreachable through `Conn`,
S4); selection live and already used by this uid -> join (no cap); selection live and NOT yet used by
this uid -> `uid count >= caps.per_uid` -> `Budget(UidSelections)`, else join (the probe cap is NOT
judged for a join: no new stream is created; controller decision, finding 4); selection not live ->
`uid count >= caps.per_uid` -> `Budget(UidSelections)`; else `live_selections >= caps.per_probe` ->
`Budget(Selections)`; else `next_id.checked_add(1)` (`None` -> `Exhausted`) and create. `caps` are the
requesting connection's level values (finding 14). Every leave
decrements the ref, removes a 0 ref and an empty uid entry, and destroys an empty stream.
**S20 Server-wide stream bounds.** `ServerLimits::unified()` also sets, for observe and operator:
`tick_ms` to the larger of the two (one sampler tick); `max_rows`, `client_queue_bytes`, and
`query_queue_bytes` to their smaller values; and one shared `max_block_bytes` to the smaller
configured byte limit, clamped again by those shared queue caps. The per-uid and global Live and
Query pool limits are derived from the shared per-connection caps. Thus the same queue and block
bounds are announced and enforced for either level and `max_block_bytes <= query_queue_bytes` (and
`<= client_queue_bytes`) holds for both levels in every built server (no panic: a misconfiguration
is clamped; a `Server::new` test covers defaults, unequal pool caps and a deliberately wrong input).
What hello announces is what the server enforces.
`ServerLimits::stream_limits()` = `StreamLimits { bounds: BlockBounds { max_rows, max_block_bytes } }`
of the unified observe level (equal to operator's after unification). `min_interval_ms`,
`max_uid_selections` and `max_selections` stay per level (S19 passes the requesting level's caps).
**S21 `dropped_blocks` (§4.5).** `SendHub::dropped_blocks()` (package P5b-2b); the conn's `status` reads it from
the hub (as it reads `global_pools()`); `StatusInfo::dropped_blocks` from the backend is ignored. 2c
rewrites the two conn.rs status tests that expect the backend's 5, and the `backend.rs` module doc
(lines 13-16) says the value is the hub's.
**S22 Bytes accounting.** The hub counts each connection's entry at its full length although tick bytes
are a shared `Arc` (conservative overcount; caps are on the sum). The registry keeps no block bytes,
but per stream it holds, outside every pool, the current `dict_body` (at most `max_block_bytes`) and its
`Vec<Entry>`: bounded by `max_selections x (max_block_bytes + entries)` = 8 x (4 MiB + entries) with
the defaults (entries <= 65,536 x ~300 B by the dictionary bounds; ~1.2 MB at 4,096 rows). A `LiveTick`
lives only inside one `on_tick`. `Pending` keeps the `Selection` and a length, never block bytes.
**S23 Draining and idle (§1.5, §5.4).** Clean EOF in `Ready`: state `Draining`; for every `corr` in
`subs` ascending: `streams.unsubscribe`, `counts = end_stream`, push `end_ok(corr, .., ClientEof)`; `subs`
cleared; `pending` keeps retrying. Draining closes silently when `pending` is empty and `hub.queued(id)
== Pools::default()` (checked after `on_eof`, `on_written` and `retry_pending`). The idle condition is
`Ready && reader at a boundary && queue empty && subs empty && pending empty`.
**S24 Determinism.** `BTreeMap` everywhere; `SelId` from 1, monotonic, never reused; subscribers in
`SubKey` order (`ConnId`, then `corr`); `on_tick` deliveries in `SelId` order; the retry pass in `ConnId`
then `corr` order.
**S25 Tick delivery API: backend pull.** `Server::on_tick(now_ns)` asks the registry, which asks
`backend.current` for each due stream; the driver (P5b-4) calls it once per sampler tick, after `poll`
in the same loop iteration. Rejected: `deliver_tick(selection_id, bytes)` (the driver would have to know
registry ids, and encoding, validation and generations would leave the server).
**S26 Integers.** Checked or saturating everywhere: `SelId` `checked_add`; `left`/`every` u32
saturating; generation `checked_add`; `n_rows` `u32::try_from`; lengths `checked_add`; ref counts
`saturating_add`; `need` is a `usize` from a real length.
**S27 The `drive` rule extended (design 1b module doc).** Every new push path goes through
`Server::drive`: `on_eof` (client-eof ENDs), `on_tick` (Live pushes and ENDs), the retry pass (blocks and
ENDs). `drive` runs `enforce_control` after each. Every new push path (`push_tick`, `end_subscription`,
`retry_pending`, the subscribe/unsubscribe/current arms, the Draining ENDs) returns at once when
`is_closed()`, and stops after any push that closes the conn (S6, S7, S15, S17, S23): no later push,
`end_stream` or registry change is made on a closed conn except S17's `subs.remove`.
**S28 Precedence with the new verbs** (R11 numbering): (2) `corr` 0 or in use (S4); (9) served flags flip
in 2d; (10) `too-many-requests` (S5); (11) argument schemas (S1, S8, `sub` u32); execution budgets
(S9) come after the schema, then the backend. `forbidden` / `binary-required` keep coming from the full
table.
**S29 END payload.** `end_ok`: `{"ok":true,"blocks":B,"dropped":D,"next":null,"reason":"<r>"}`;
`end_error`: `{"ok":false,"blocks":B,"dropped":D,"error":{"code","message","detail"}}` (the existing
error member). Frame type END (6), flags 0, `corr` = the stream's. Both parse under `check_end`.
**S30 2a compile rule.** In 2a `Conn::execute` handles the three new `Call`s with a REP `internal` (they
cannot be dispatched while `served` is false); 2c replaces the arm; 2d flips `served`.
**S31 §11.4 cases reachable in P5b-2** (against the sans-IO core, R21): `conn-not-idle-with-subscription`,
`conn-dribbling-reader`, the END half of `conn-half-close`, plus the new rows `conn-corr-in-use-sub`,
`conn-corr-in-use-pending`, `conn-too-many-requests`. Only `conn-reply-page-truncated` stays (P5b-3).
**S32 Levels.** Block verbs only at observe and operator (table); `hello.verbs` there becomes `hello,
status, current, subscribe, unsubscribe`; observer unchanged (R18).
**S33 Hostile bounds** (section 4) are rules: request parsing stays O(request bytes) (S1 caps), per tick
at most `max_selections` builds and dictionary splices, no request-driven encode except `current`.
A `current` resolves at most `max_rows + 1` rows (the backend stops there), judges `Oversize`
arithmetically before encoding, and encodes only a block it will send; a FAILING `current` (rows,
internal) is never outstanding, so `max_outstanding` does not bound its repetition: its cost per request
is one bounded resolution, and the error REPs are bounded by the control queue (`slow-client`).
**S34 Not done** — section 1 list; anything else found missing is a NEW rule in a follow-up, not an
implementer's choice.

## 4. Hostile input and DoS analysis

- **Selection parsing.** Bounded by `max_request_bytes` (32 KiB) and R4 (16 members, 64 elements,
  depth 8); per element one `Obj::parse` or `unescape`, O(element bytes); sort O(n log n) with n <= 64;
  duplicates O(n) after the sort. `detail.key` only schema paths or R14-bounded echoes. No allocation
  above the request size. Hostile set: section 5.1.
- **Many subscriptions.** At most `max_subscriptions` (4) per conn, 24 block-level conns: <= 96
  subscribers, <= 8 streams. `on_tick` costs <= 8 backend calls, 8 bare encodes, 8 lazy splices, 96 hub
  pushes. Subscribe/unsubscribe churn costs registry map operations and two small control frames, never an
  encode (S9): no CPU amplification from a stream of small requests; the control frames are bounded by
  `control_queue_bytes` (`slow-client`).
- **`current` amplification.** A SUCCESSFUL `current` costs one resolution and one encode; a non-reading
  client gets one block queued per free Query room, then <= `max_outstanding` pending requests (holding
  no bytes, S22), then `too-many-requests`. Retries need room, which needs reads (S7). A FAILING
  `current` (`budget rows`, `internal` after `Oversize`) is never outstanding, so it can repeat at line
  rate: its cost is bounded per request by the backend stopping at `max_rows + 1` rows and by `Oversize`
  being judged arithmetically before any encode (S6), so a failing request never encodes. Residual:
  one bounded resolution per request (~60 B); a client that reads its error REPs can keep that up at its
  read rate (the same order as any served request); one that does not is `slow-client` after
  `control_queue_bytes` of REPs.
- **Slow readers with shared encodings.** Shared `Arc` bytes, per-conn pools (§5.3); a dribbler is closed
  at `pinned_ns + frame_completion_ms` and is the shed victim before anyone else (largest Live bytes);
  `conn-dribbling-reader` pins that a second subscriber of the same stream is never shed meanwhile.
- **Dictionary replay amplification.** A lagging subscriber whose dictionary-bearing entries are shed
  gets the dictionary form again (`needs_dict`); the form is built once per tick (`OnceCell`) for all
  subscribers, so the CPU is per tick, not per subscriber; the bytes are bounded by the Live pools. A
  joiner forces at most one splice per tick per stream.
- **Selection budgets (squatting).** A join that adds a selection the uid does not use yet is judged
  against that uid's cap (S19), so joining cannot grow one uid past 4. Residual gap (recorded, not
  closed): two uids can still create all 8 probe selections (4 each) and hold them for as long as they
  read one block per interval (max 60 s); other uids can then only join those selections (within their
  own cap of 4), never create one. This is D-W20 as decided.
- **Memory per subscriber vs shared bytes.** Per subscriber: a `subs` entry, a registry `Sub`, a hub stream
  state (tens of bytes each) and <= 2 queue entries (one pinned, one unpinned) referencing shared ticks.
  Per stream, outside every pool: `entries` + `dict_body` (`dict_body` <= `max_block_bytes`), so <= 8 x
  (4 MiB + entries) with the defaults (S22). Physical tick memory <= the Live pools (global 32 MiB)
  because nothing else holds tick bytes.
- **Backend misbehaviour.** Bounded by S16 (validation), S11 (rows, size) and R25 (replies); every
  failure is `internal`/`budget` and ends the stream, never a panic or an oversized frame.

## 5. Test lists (minimum; names are binding, bodies are the implementer's)

### 5.1 P5b-2a
- `selection_*`: `canonical_sorts_variants_then_values` (inc numeric: 2 < 10; path bytewise: "B" < "a"),
  `decoded_duplicates_are_refused` (`"a"` vs `"a"`, `{"inc":5}` twice), `inc_and_path_are_distinct`,
  `counts_0_1_16_17_entities_and_0_1_64_65_metrics`, `path_512_513_decoded_and_escaped`, `empty_path_refused`,
  `control_and_del_in_path_refused`, `lone_and_misordered_surrogates_refused`, `all_must_be_true`
  (`false`, `1`, `"true"`, `null`), `element_with_two_members_refused`, `unknown_selector_key_echo_bounded`
  (a 30 KB key: `detail` <= 2 KiB), `metric_name_grammar` (`A`, `1a`, `a` x 64 / 65, `a-b.c_d`, escaped letters),
  `new_rechecks_every_rule` (a hand-built invalid vector); `metric_name_grammar` also feeds the non-string
  elements `true`, `null`, `false`, `1e5`, `12`, `[]`, `{}` (all `selection.metrics`), and
  `entity_elements_of_every_non_object_kind` (`"x"`, `5`, `true`, `null`, `[]`: `selection.entities`);
  `elements_report_kinds` for `Obj::elements`.
- `args_current_*`, `args_subscribe_*` (`interval_ms` at `tick`-1, `tick`, `min`-tick, `min`, 60,000,
  60,001, non-multiple, 0, `-0`, `1.0`, 2^32), `args_unsubscribe_*` (0, u32::MAX, 2^32, missing, extra key),
  `args_key_order` (unknown key before a missing one; `selection` before `interval_ms`).
- `interval_rule_*`: default refuses all; `tick_ms` 0 refuses; `min_ms` 0 still needs `>= tick_ms`.
- `classify_step_2_corr_in_use_is_bad_frame_before_check_req` (malformed payload too),
  `classify_step_10_too_many_requests_after_served_before_schema` (BLK verb with bad args + full ->
  too-many; unknown verb + full -> unknown-verb; JSON facts ignored),
  `classify_step_10_spares_json_shape_verbs` (`status`, `unsubscribe` with full facts are dispatched), `classify_new_calls_via_call_of` (each arm),
  `classify_default_facts_equal_old_classify` (the P5b-1a seeds give identical results).
- `codes_pool_names_and_detail_pool_encoding`, `encode_end_ok_and_error_parse_with_check_end` (every
  reason, max u32 counts), `encode_results_shapes` (`current_result`, `subscribe_result` parse, exact keys).
- `limits_selection_caps_defaults_and_zero_at_non_block_levels`, `hello_keys_still_19`.
- Hostile: `serving_hostile.rs` adds 2,000 random + 2,000 mutated-valid selection/subscribe/unsubscribe
  args (closed outcome set: Ok or `bad-request` with a schema path; no panic; `Selection` always
  canonical: sorted, unique).

### 5.2 P5b-2b
- `tick_validate_*`: one test per S16 refusal (each `intern` error -> `Invalid`); `tick_rows_over_max_is_rows_error`;
  `tick_size_bound_on_dict_form` (bare fits, dict form does not -> `Oversize`, and `encode_bare` is not
  called: a counting wrapper or the order of results proves it); `tick_encoded_len_equals_real_lengths`
  (0, 1, 4,096 rows; 0, 1, 8 columns; with and without a dictionary body); `tick_encoded_len_overflow_is_none`;
  `tick_with_dictionary_degenerate_inputs` (n_sec `u16::MAX`, short input: unchanged, no panic);
  `backend_fixed_too_many_rows` (a stored tick above `max_rows` answers `TooManyRows`);
  `tick_example_wraps` (`inc` `u64::MAX`).
- `tick_dictionary_body_repeated_identity_is_invalid`, `tick_splice_equals_encode_block` (0 rows, 1 row,
  4,096 rows x 8 columns, with unknown-free entries), `tick_splice_decodes_with_decode_block`,
  `tick_bare_decodes_against_held_dictionary` (`decode_block_with`), `tick_header_fields_overwritten`,
  `tick_live_tick_dict_form_built_once` (two calls, `dict_built`), `tick_query_block_generation_1`.
- `registry_subscribe_*`: create, join of a selection the uid already uses (no cap), join of a NEW
  selection for the uid at its cap -> `UidSelections` and below it -> joined with the probe at 8 (no
  probe cap on a join), uid cap 4/5, probe cap 8/9 with two uids, cap order (uid before probe), caps
  taken from the argument (two levels with different caps), duplicate key, `SelId` exhaustion (private
  field set in the test module).
- `registry_leave_*`: unsubscribe last destroys and recreation restarts at generation 1;
  `release_conn` idempotent and complete (all counters 0).
- `registry_tick_*`: schedule `every` 1/2/3 over 7 ticks; joiner due at the next tick; not-due stream not
  built (backend call counter); backend `Unavailable` keeps due, `TooManyRows` ends the stream with budget rows; change -> generation + 1 and no bare; same entries
  -> bare; reordered entries -> change; generation `u32::MAX` change -> End internal to all; rows -> End
  budget rows to ALL (also not-due) subscribers; deliveries in `SelId` order and keys ascending.
- `hub_query_pin_leaves_pending_drops` (S12: ended query X with a queued block, new live X with a drop,
  pin of the old block stamps 0, next live pin stamps 1), `hub_dropped_blocks_counts_every_note_drop`
  (coalesce, shed, incoming, end_stream; not drain/remove), `hub_query_room_*` (each pool, unknown id).
  The hub pool-exactness checker runs over the new functions too. Fixture `hub-query-pin-after-corr-reuse`
  (generated, replayed by `hub_fixtures.rs`; the Python count test says 20).
- Hostile: 2,000 deterministic registry runs (random subscribe/unsubscribe/release/on_tick over 3 uids,
  6 conns, 12 selections, a backend that changes entries randomly): after each step the uid refs equal a
  recount from the subscribers, caps never exceeded, generations strictly increase per stream lifetime,
  every Push's bare form absent exactly on change ticks.

### 5.3 P5b-2c
- `conn_current_*`: queued (REP, BLOCK, END `complete` blocks 1 in FIFO order, the client `StreamDicts`
  accepts the block), backend error, rows budget, oversize -> `internal`, backpressure -> outstanding 1,
  retry when room -> BLOCK + END, retry failure after REP -> `END ok:false`, the retried block is from the
  newer tick.
- `conn_subscribe_*`: REP shape echoes exactly; `max_subscriptions` 4/5 -> `budget subscriptions`;
  uid/probe budgets through the conn; no block before the next tick; first block dictionary-bearing.
- `conn_unsubscribe_*`: REP then END `unsubscribed`; unknown/0/query corr `not-found`; queued unpinned tick
  counted in `dropped`; pinned tick counted in `blocks`; JSON-mode `not-found`; corr reusable after.
- `conn_corr_in_use_subscription_is_fatal`, `conn_corr_in_use_pending_is_fatal`, `conn_corr_reuse_after_end_ok`.
- `conn_too_many_requests` (4 pending: a 5th `current` or a `subscribe` refused non-fatal, `status` and
  `unsubscribe` still answered; after a retry frees one the next `current` is accepted).
- `conn_closed_stops_every_push_path` (a push that closes the conn: no later push, `end_stream` or
  registry change; `end_subscription` on a closed conn only removes `subs`).
- `conn_current_uses_unified_bounds` and `server_limits_block_invariant` (`max_block_bytes <=
  query_queue_bytes, client_queue_bytes` after `unified()`, defaults and a wrong input), plus
  `server_current_uses_smallest_shared_query_pool` (a second valid BLOCK waits when the smaller
  observe/operator Query pool is full).
- `conn_status_tests_rewritten` (the two P5b-1b status tests now expect the hub's count).
- `conn_draining_*`: client-eof END per subscription in corr order with exact counts; pending finishes then
  silent close; close only when pending empty and queue empty.
- `conn_idle_*`: not idle with a subscription or a pending request; idle again after unsubscribe.
- `conn_server_end_*`: `end_subscription` budget rows / internal; unknown corr ignored.
- `conn_status_dropped_blocks_from_hub` (`conn_hello_verbs_at_block_levels` moves to 2d with the flip).
- R18/R22: counters, `uid_pools`, `global_pools`, and registry counts return to zero after every close
  path with a real connection-side subscription (slow-client, shutdown, fatal request, frame-too-large,
  request deadline, Draining done, driver finish) -- through `Server::finish`.
- Every client-visible sequence is replayed through the Rust client's checks where possible:
  `check_rep`/`check_end`, `decode_block_with` + `StreamDicts::accept`, END `blocks == received`.

### 5.4 P5b-2d
- `server_on_tick_*`: shared bytes (two conns, one `Arc`, distinct `corr`/`dropped` prefixes), joiner on
  generation 5 gets the dictionary form then bare, and a faster subscriber changes the generation on
  a tick skipped by a slower subscriber that then gets the dictionary form on its next due tick (§5.3
  D-W27 at server level); Push skipped for a closed-unfinished conn; End delivered to every subscriber.
- `server_retry_pass_*`: after `wrote` of another conn of the same uid frees the uid Query pool; at `poll`;
  id order.
- `server_finish_releases_selections` for all close paths (S18).
- `server_hello_verbs_at_block_levels` (after the `served` flip) and byte-level REQ -> REP/BLOCK/END
  sequences of the three verbs in binary mode, `binary-required` / `not-found` in JSON mode.
- The §11.4 cases of 5.5, each a named test.
- Hostile: 2,000 deterministic server runs (random bytes plus valid subscribe/unsubscribe/current REQs,
  random `wrote`/`poll`/`on_tick`/EOF over 4 slots and 2 uids): no panic, hub and registry counters back
  to zero after `shutdown` + finish, every BLOCK seen by a modelled client decodes under its stream rules,
  every END count equals the blocks that stream delivered.

### 5.5 §11.4 cases P5b-2 makes reachable
- `conn-not-idle-with-subscription`: subscribed, every tick drained, no close at `idle_ms + 1` and later.
- `conn-dribbling-reader`: a subscriber writing 1 byte per 4 s: `slow-client` at its pinned head's `pinned_ns
  + frame_completion_ms + 1`; a second subscriber of the same selection (other uid) is never shed and
  receives every tick meanwhile.
- `conn-half-close`: subscribe, two ticks written, `SHUT_WR`: END `client-eof` with `blocks` 2, then silent
  close when the queue drains.
- new: `conn-corr-in-use` (subscription and pending variants), `conn-too-many-requests`.

## 6. WIRE.md edits implied (owner package)

- §2.2 (2b): `END ok:false` in M2 carries `internal` or `budget {"pool":"rows"}` (S11, S7).
- §4.2 (2a): selection rules of S1 (decoded identity, empty path refused, `all` must be `true`, element
  key paths); precedence: steps 2 (`corr` in use) and 10 (`too-many-requests`) are now implemented (S4,
  S5), and step 10 refuses BLK-shape verbs only (Q6; the same one line in §5.4's `Ready` row, 2a).
- §4.3 (2d): `hello.verbs` at observe/operator lists the three verbs; `unsubscribe` in JSON mode answers
  `not-found` (S15, S32).
- §4.5 (2b): `queues.dropped_blocks` = the hub's counted drops since start (S21).
- §4.6 (2c): `current` checks before the REP (S6; resolution stops at `max_rows + 1`, size judged
  before encoding); a backpressured `current` is re-resolved when it is queued, so the block is the
  latest tick at queue time (S7, D-W30).
- §4.7 (2a + 2c): interval rule without rounding (S8); first block at the next tick, REP first (S9, S10);
  `max_rows` for a subscription is judged per tick and ends the stream with `budget rows` (S11); the
  unsubscribe race (S15); Draining (S23).
- §4.7 and §5.1 (2b): a join that adds a selection the uid does not use yet counts against the uid cap
  (`budget uid-selections`) but not the probe cap (S19, finding 4); the caps applied are the requesting
  level's; a uid's selections are counted across levels.
- §5.1 (2c): outstanding = a backpressured `current` in M2 (S5); `max_rows`, `client_queue_bytes`,
  `query_queue_bytes`, and `max_block_bytes` are unified to the smaller of observe/operator;
  `max_block_bytes <= query_queue_bytes, client_queue_bytes` holds by construction (S20).
- §5.3 (2b): Query pin stamps 0 without resetting `pending_drops` (S12); `dropped_blocks`, `query_room`.
- §5.4 (2c): `Ready` idle row includes "no pending request"; `Draining` row closes when pending and queue
  are empty (S23); (2d) driver note: `on_tick` once per tick after `poll` (S25).
- §6.2 (2b): generation life cycle: 0 before the first built tick, destroyed with the last subscriber,
  recreated at 1; the `u32::MAX` rule is "a change at `u32::MAX`" (S10, S11).
- §11.4 (2b): hub table "20 files", new row `hub-query-pin-after-corr-reuse` (S12). (2d): the
  connection cases of 5.5, R21 superseded for them (S31).
- §13 (2c): D-W29 (subscribe does no data work; the first block comes from the next tick, S9/S10) and
  D-W30 (a pending query holds its request, not its bytes, S7/S22).
- `P5B-SERVER-DESIGN.md` (2d): R3 and R21 point to this file.

## 7. Questions (all DECIDED 2026-11-18 by the controller/coordinator)

- **Q1 Rows overflow of a live subscription: END `ok:false budget {"pool":"rows"}`** (S11), with the
  §2.2 edit. Reason: the client learns why; a skip leaves it to a timeout.
- **Q2 First block at the next tick, not at subscribe** (S9, S10). Reason: an encode at subscribe makes
  subscribe/unsubscribe churn an encode amplifier (the Live tick can be shed unread); the cost is <=
  `tick_ms` latency.
- **Q3 A backpressured `current` holds its request, not its bytes**, and is re-resolved at queue time
  (S7, D-W30). Reason: holding bytes keeps up to 16 MiB per conn outside every pool. Invariant: a pending
  request always becomes queueable, because `max_block_bytes <= query_queue_bytes` (S20).
- **Q4 The three hub changes in `proto`** (S12 stamp fix, `dropped_blocks`, `query_room`) go into 2b with
  a hub delta review, plus the new hub fixture case (finding 12).
- **Q5 An empty `path`/`prefix` is refused** (S1).
- **Q6 `too-many-requests` applies to BLK-shape verbs only** (`current`, `subscribe`; S5), replacing the
  draft's "uniform", with the one-line WIRE §4.2/§5.4 edit. Reason (reviewer): `unsubscribe`/`status`
  never become outstanding, and a uniform refusal makes Python `Subscription.close()` raise while 4
  `current`s are pending.
- **Q7 Selection caps:** per uid across levels, the REQUESTING level's caps passed to
  `SelectionStreams::subscribe` (S19, finding 14); `max_rows`/`max_block_bytes` unified to the minimum (S20).
- **Q8 Backend `Unavailable` during a tick: skip**; due subscribers stay due. Note for the collector
  package: a long backend outage is seen by a client only as silence, which ends in the client's own
  wait bound (Rust: timeout + interval; Python: `timeout_s` + interval), i.e. a client-side timeout, not a
  server END; the collector package decides whether N consecutive failures end the stream (`internal`).
- **Q9 Four packages**, 2c and 2d merged together (section 2, finding 13).

## 8. Review round 1 (independent Opus review of `280efb4bf`, verdict FIX)

| # | Finding (severity) | Where fixed |
|---|---|---|
| 1 | `Obj::elements` untyped; `unescape` strips first/last byte unchecked, so `true`/`null`/`1e5` became metric names (major) | 2a API (`Vec<(Kind, &[u8])>`), S1 kind checks first, 5.1 `metric_name_grammar` + entity kinds |
| 2 | `current` amplification: a failing `current` is never outstanding; Oversize found after a full encode; full resolution per request (major) | `Backend::current(&sel, max_rows)` + `TooManyRows`, `tick::encoded_len`, S6 and S11 (size before encode), S33, section 4 |
| 3 | `max_rows`/`max_block_bytes` must be unified; `query_block` uses the conn's unified limits; block <= Query pool (major) | S20 (`unified()` minimum + clamp, test), S6, 5.3 |
| 4 | selection pool squatting through joins (major; controller decision) | S19 (a new-to-uid join is judged against the uid cap, not the probe cap), section 4 residual gap, section 6 §4.7/§5.1 |
| 5 | `TickData` must derive `Eq` (`FixedBackend` derives `Eq`) (major) | 2b API |
| 6 | every new push path stops when closed; S17 on a closed conn only removes `subs` | S27, S6, S7, S17, 5.3 test |
| 7 | S21 breaks the two conn.rs status tests; backend.rs module doc | 2c module list, S21, 2b API note, 5.3 |
| 8 | `LiveTick::new` signature | 2b API |
| 9 | `with_dictionary` length and `n_sec` arithmetic | S14, 5.2 test |
| 10 | `TickData::example` overflow | 2b API (`wrapping_mul`/`wrapping_add`), 5.2 test |
| 11 | registry memory outside the pools | S22, section 4 |
| 12 | S12 changes no fixture; add `hub-query-pin-after-corr-reuse`, bump the 19-file check | 2b edits, 5.2, section 6 §11.4 |
| 13 | 2c alone not mergeable | section 2 (2c and 2d merge together), `served` flip moved to 2d, S30, 5.3/5.4 |
| 14 | caps of the requesting level | `SelectionCaps`, S9, S19, S20 |
| 15 | `intern` error mapping | S16, 5.2 |
| Q6 | `too-many-requests` BLK-only (controller decision) | S5, 2a API comment, 5.1, 5.3, section 6 §4.2/§5.4 |
