//! The send hub (WIRE.md §5.3): every connection's outgoing queue and the three byte pools,
//! in one value so the victim of an overflow is chosen with every queue in view.
//!
//! The hub is pure like the rest of the crate: no sockets, no threads, no clock. Every time is
//! an injected monotonic `now_ns`, and every outcome is decided by the tables of §5.3, so each
//! row is a fake-clock unit test. The probe's server-loop thread owns the one `SendHub` inside
//! its `Server` and no mutex wraps it (`docs/PROBE-PROCESS.md` §(a), §(i)).
//!
//! # Contracts
//!
//! - **Three pools.** Live (unsent bytes of subscription ticks, droppable), Query (unsent
//!   bytes of query blocks, never dropped) and control (REP/EVT/END, per connection only).
//!   Live and Query are counted per connection and globally; D-W53 has no per-uid pools. Bytes
//!   enter a pool when an entry is queued and leave it when the entry is popped (a complete
//!   `wrote`), shed, coalesced, drained or its connection removed: a partly written head still
//!   counts.
//! - **Stamping at pin.** The first `head` call for an entry pins it. For a block the hub
//!   copies the first `16 + 8 * n_sec + 80` bytes, writes `corr` at 8 and the stream's pending
//!   `dropped` count at `88 + 8 * n_sec`, and returns that private prefix and the shared
//!   tail. A Live pin consumes the pending count; a Query pin stamps 0 and leaves it untouched,
//!   so a new Live stream that reused the Query stream's `corr` cannot inherit the old pin.
//!   Every drop happens to an unpinned entry, so each is counted exactly once.
//! - **One unpinned tick per stream.** A newer tick replaces an unpinned one in place.
//! - **Deterministic order.** Connections are kept by ascending [`ConnId`]; the victim tie
//!   break and the order of [`SendHub::poll`] results are by ascending id.
//! - **No panic on caller input.** An operation on an unknown (already removed) connection
//!   is a no-op that reports the safest outcome; a stamped field that does not fit inside a
//!   block shorter than its own header is not written.

use std::collections::{BTreeMap, VecDeque};
use std::sync::Arc;

use crate::frame::{HEADER_BODY_LEN, SECTION_DIR_ENTRY_LEN};

/// Offset of `corr` in a frame header (WIRE.md §2.1).
const CORR_OFFSET: usize = 8;

/// Offset of the `u16 n_sec` of a BLOCK (WIRE.md §3.1).
const N_SEC_OFFSET: usize = 12;

/// Offset of the first directory entry: the frame header, `n_sec` and the reserved word.
const DIRECTORY_OFFSET: usize = 16;

/// Offset of `dropped` inside the header body (WIRE.md §3.3).
const DROPPED_IN_HEADER: usize = 72;

/// A connection handle: increasing from 1, the order that breaks ties (WIRE.md §5.3).
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct ConnId(pub u32);

/// The caps and timers of the hub (WIRE.md §5.1; times in nanoseconds).
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct QueueLimits {
    /// Live pool bytes per connection (`client_queue_bytes`).
    pub client_queue_bytes: usize,
    /// Live pool bytes over all connections.
    pub global_live_bytes: usize,
    /// Query pool bytes per connection (`query_queue_bytes`).
    pub query_queue_bytes: usize,
    /// Query pool bytes over all connections.
    pub global_query_bytes: usize,
    /// Control pool bytes per connection (`control_queue_bytes`).
    pub control_queue_bytes: usize,
    /// Writer stall: a non-empty queue with no byte written (`stall_ms`).
    pub stall_ns: u64,
    /// A pinned head must be completely written within this of its pin (`frame_completion_ms`).
    pub frame_completion_ns: u64,
}

/// Queued bytes per pool. `global_pools` reports `control` as 0: that pool exists per connection
/// only.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct Pools {
    /// Unsent bytes of Live entries.
    pub live: usize,
    /// Unsent bytes of Query entries.
    pub query: usize,
    /// Unsent bytes of Control entries.
    pub control: usize,
}

/// The only reason the hub asks for a connection to be closed.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Close {
    /// The peer does not read: `slow-client`, fatal (WIRE.md §5.4).
    SlowClient,
}

/// What [`SendHub::push_live`] did with a tick.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LiveOutcome {
    /// Appended as a new entry.
    Queued,
    /// Replaced the stream's unpinned tick in place.
    Coalesced,
    /// Did not fit (or the connection is gone): the tick is dropped and counted.
    DroppedIncoming,
}

/// What [`SendHub::push_query`] did with a block.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum QueryOutcome {
    /// Appended (also the answer for a connection that is gone: the block is discarded).
    Queued,
    /// A Query pool refused the block: nothing was queued. The caller may refuse the request
    /// and let the client retry after it reads its queued blocks.
    Backpressure(Arc<[u8]>),
}

/// The END payload counts of a stream (WIRE.md §2.2).
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct StreamCounts {
    /// BLOCK frames of the stream sent before the END: Live at pin, Query when queued.
    pub blocks: u32,
    /// Blocks of the stream dropped after the last pinned one.
    pub dropped: u32,
}

/// The forms of one subscription tick (WIRE.md §5.3, §6.2).
pub trait TickForms {
    /// The `dict_gen` of the selection stream this tick encodes (at least 1).
    fn generation(&self) -> u32;
    /// The block without a dictionary section; `None` on the tick that changes the generation.
    fn bare(&self) -> Option<Arc<[u8]>>;
    /// The block with its dictionary; the hub calls it at most once per `push_live`.
    fn dict_form(&self) -> Arc<[u8]>;
}

/// The kind of a queue entry.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Kind {
    Control,
    Live,
    Query,
}

/// One queued frame. `stream` is 0 for a control frame; `dict` marks a dictionary-bearing
/// Live entry with the `generation` of its tick (0 for the other kinds); `seq` orders the
/// queue and is kept when a tick is replaced in place.
#[derive(Debug)]
struct Item {
    kind: Kind,
    stream: u32,
    bytes: Arc<[u8]>,
    dict: bool,
    generation: u32,
    seq: u64,
}

impl Pools {
    fn slot(&mut self, kind: Kind) -> &mut usize {
        match kind {
            Kind::Control => &mut self.control,
            Kind::Live => &mut self.live,
            Kind::Query => &mut self.query,
        }
    }
    fn charge(&mut self, kind: Kind, len: usize) {
        let slot = self.slot(kind);
        *slot = slot.saturating_add(len);
    }

    fn release(&mut self, kind: Kind, len: usize) {
        let slot = self.slot(kind);
        *slot = slot.saturating_sub(len);
    }
}

/// The per-stream state of one connection (WIRE.md §5.3).
#[derive(Clone, Copy, Debug, Default)]
struct Stream {
    pending_drops: u32,
    needs_dict: bool,
    last_pushed_gen: u32,
    blocks: u32,
}

impl Stream {
    /// The pin of one of this stream's blocks: a Live block is counted now and hands out the
    /// pending drops; a Query block stamps 0 and leaves the Live stream's pending drops alone.
    fn stamp(&mut self, live: bool) -> u32 {
        if live {
            self.blocks = self.blocks.saturating_add(1);
            std::mem::take(&mut self.pending_drops)
        } else {
            0
        }
    }
}

/// One connection's queue and counters. `pin` is the stamped private prefix of the head
/// (empty for a control frame) and `written` the bytes of the head already written.
#[derive(Debug)]
struct Conn {
    queue: VecDeque<Item>,
    totals: Pools,
    last_progress_ns: u64,
    pinned_ns: u64,
    pin: Option<Vec<u8>>,
    written: usize,
    streams: BTreeMap<u32, Stream>,
}

impl Conn {
    fn new(now_ns: u64) -> Conn {
        Conn {
            queue: VecDeque::new(),
            totals: Pools::default(),
            last_progress_ns: now_ns,
            pinned_ns: now_ns,
            pin: None,
            written: 0,
            streams: BTreeMap::new(),
        }
    }

    /// The oldest unpinned Live entry (of `only` stream when given).
    fn find_unpinned_live(&self, only: Option<u32>) -> Option<usize> {
        let skip = usize::from(self.pin.is_some());
        for (index, item) in self.queue.iter().enumerate().skip(skip) {
            let wanted = match only {
                Some(stream) => item.stream == stream,
                None => true,
            };
            if item.kind == Kind::Live && wanted {
                return Some(index);
            }
        }
        None
    }

    /// Remove the oldest unpinned Live entry (of `only` stream when given) and release its
    /// bytes from this connection's pool.
    fn take_unpinned_live(&mut self, only: Option<u32>) -> Option<Item> {
        let found = self.find_unpinned_live(only);
        let item = found.and_then(|index| self.queue.remove(index))?;
        self.totals.release(item.kind, item.bytes.len());
        Some(item)
    }

    /// A copy of the stream's state (the default for a stream not seen yet).
    fn stream_state(&self, stream: u32) -> Stream {
        self.streams.get(&stream).copied().unwrap_or_default()
    }

    /// A Live entry of `stream` was dropped: count it; a dictionary-bearing one sets `needs_dict`.
    fn note_drop(&mut self, stream: u32, dict: bool) {
        let state = self.streams.entry(stream).or_default();
        state.pending_drops = state.pending_drops.saturating_add(1);
        state.needs_dict |= dict;
    }

    /// A dictionary-bearing Live entry was removed without a count: the stream needs one again.
    fn note_dict_lost(&mut self, stream: u32) {
        self.streams.entry(stream).or_default().needs_dict = true;
    }

    /// A Live entry of `generation` was queued.
    fn mark_pushed(&mut self, stream: u32, generation: u32, dict: bool) {
        let state = self.streams.entry(stream).or_default();
        state.last_pushed_gen = generation;
        if dict {
            state.needs_dict = false;
        }
    }

    /// A Query block was queued: count it for the stream's END.
    fn note_query_queued(&mut self, stream: u32) {
        let state = self.streams.entry(stream).or_default();
        state.blocks = state.blocks.saturating_add(1);
    }

    /// A push into an empty queue restarts the stall clock (WIRE.md §5.3).
    fn arrive(&mut self, now_ns: u64) {
        if self.queue.is_empty() {
            self.last_progress_ns = now_ns;
        }
    }

    /// Pin the head: stamp a private prefix for a block, remember the time. An empty queue
    /// pins nothing.
    fn pin_front(&mut self, now_ns: u64) {
        let Some(item) = self.queue.front() else {
            return;
        };
        let (kind, stream) = (item.kind, item.stream);
        let bytes = Arc::clone(&item.bytes);
        let mut prefix = Vec::new();
        if kind != Kind::Control {
            let dropped = match self.streams.get_mut(&stream) {
                Some(state) => state.stamp(kind == Kind::Live),
                None => 0,
            };
            prefix = stamped_prefix(&bytes, stream, dropped);
        }
        self.pin = Some(prefix);
        self.written = 0;
        self.pinned_ns = now_ns;
    }

    /// The unwritten rest of the pinned head as `[prefix part, tail part]`; `None` while
    /// nothing is pinned (an empty queue).
    fn remainder(&self) -> Option<[&[u8]; 2]> {
        let (prefix, item) = self.pin.as_ref().zip(self.queue.front())?;
        let head = prefix.get(self.written..).unwrap_or_default();
        let tail_from = self.written.max(prefix.len());
        let tail = item.bytes.get(tail_from..).unwrap_or_default();
        Some([head, tail])
    }

    fn is_partial(&self) -> bool {
        self.pin.is_some() && self.written > 0
    }

    /// Account for `n` written bytes; a completely written head is popped and returned.
    fn advance(&mut self, n: usize, now_ns: u64) -> Option<Item> {
        let total = self.pin.as_ref().and(self.queue.front())?.bytes.len();
        if n > 0 {
            self.last_progress_ns = now_ns;
        }
        self.written = self.written.saturating_add(n).min(total);
        if self.written < total {
            return None;
        }
        self.pin = None;
        self.written = 0;
        self.queue.pop_front().inspect(|item| {
            self.totals.release(item.kind, total);
        })
    }

    fn is_slow(&self, now_ns: u64, limits: &QueueLimits) -> bool {
        if self.queue.is_empty() {
            return false;
        }
        let stall_at = self.last_progress_ns.saturating_add(limits.stall_ns);
        if now_ns > stall_at {
            return true;
        }
        let frame_at = self.pinned_ns.saturating_add(limits.frame_completion_ns);
        self.pin.is_some() && now_ns > frame_at
    }
}

fn read_u16(bytes: &[u8], at: usize) -> u16 {
    match bytes.get(at..at + 2) {
        Some(&[low, high]) => u16::from_le_bytes([low, high]),
        _ => 0,
    }
}

fn put_u32(buffer: &mut [u8], at: usize, value: u32) {
    if let Some(slot) = buffer.get_mut(at..at + 4) {
        slot.copy_from_slice(&value.to_le_bytes());
    }
}

/// The private copy of a block's first `16 + 8 * n_sec + 80` bytes with `corr` and
/// `dropped` written (WIRE.md §5.3 Pin, D-W7).
fn stamped_prefix(bytes: &[u8], corr: u32, dropped: u32) -> Vec<u8> {
    let n_sec = usize::from(read_u16(bytes, N_SEC_OFFSET));
    let body_at = DIRECTORY_OFFSET + SECTION_DIR_ENTRY_LEN * n_sec;
    let take = (body_at + HEADER_BODY_LEN).min(bytes.len());
    let mut prefix = bytes.get(..take).unwrap_or_default().to_vec();
    put_u32(&mut prefix, CORR_OFFSET, corr);
    put_u32(&mut prefix, body_at + DROPPED_IN_HEADER, dropped);
    prefix
}

/// Whether the connection and global Query pools have room for `len` more bytes.
fn query_fits(limits: &QueueLimits, global: &Pools, conn: &Conn, len: usize) -> bool {
    let fits = |used: usize, cap: usize| used.saturating_add(len) <= cap;
    fits(conn.totals.query, limits.query_queue_bytes)
        && fits(global.query, limits.global_query_bytes)
}

/// The id after `id`: increasing, skipping 0 on a wrap.
fn next_conn_id(id: u32) -> u32 {
    match id.wrapping_add(1) {
        0 => 1,
        next => next,
    }
}

/// The pool a fit check looks at.
#[derive(Clone, Copy, Debug)]
enum Level {
    Conn(ConnId),
    Global,
}

/// The send hub of one serving process (WIRE.md §5.3).
#[derive(Debug)]
pub struct SendHub {
    limits: QueueLimits,
    next_id: u32,
    next_seq: u64,
    counted_drops: u64,
    conns: BTreeMap<ConnId, Conn>,
    global: Pools,
}

impl SendHub {
    /// An empty hub with the given caps.
    #[must_use]
    pub fn new(limits: QueueLimits) -> SendHub {
        SendHub {
            limits,
            next_id: 1,
            next_seq: 0,
            counted_drops: 0,
            conns: BTreeMap::new(),
            global: Pools::default(),
        }
    }

    /// Register a connection; ids increase from 1 (a wrapped id is never 0, and one still in use
    /// is skipped).
    pub fn add(&mut self, now_ns: u64) -> ConnId {
        while self.conns.contains_key(&ConnId(self.next_id)) {
            self.next_id = next_conn_id(self.next_id);
        }
        let id = ConnId(self.next_id);
        self.next_id = next_conn_id(self.next_id);
        self.conns.insert(id, Conn::new(now_ns));
        id
    }

    /// Drop everything of `id`, release its bytes and forget it.
    pub fn remove(&mut self, id: ConnId) {
        if let Some(conn) = self.conns.remove(&id) {
            for item in conn.queue {
                if item.kind != Kind::Control {
                    self.global.release(item.kind, item.bytes.len());
                }
            }
        }
    }

    /// Remove every unpinned entry of `id` (all kinds) and release its bytes; a pinned head
    /// stays. The removed ticks are not counted (the connection is closing), but a removed
    /// dictionary-bearing tick sets `needs_dict` for its stream.
    pub fn drain_unpinned(&mut self, id: ConnId) {
        if let Some(conn) = self.conns.get_mut(&id) {
            let keep = usize::from(conn.pin.is_some());
            let from = keep.min(conn.queue.len());
            let removed: Vec<Item> = conn.queue.drain(from..).collect();
            for item in removed {
                let len = item.bytes.len();
                conn.totals.release(item.kind, len);
                if item.kind != Kind::Control {
                    self.global.release(item.kind, len);
                }
                if item.kind == Kind::Live && item.dict {
                    conn.note_dict_lost(item.stream);
                }
            }
        }
    }

    /// Queue a REP/EVT/END frame. An empty control pool admits any length; otherwise a frame
    /// that would exceed `control_queue_bytes` is `Close(SlowClient)` and is not queued.
    pub fn push_control(&mut self, id: ConnId, bytes: Arc<[u8]>, now_ns: u64) -> Result<(), Close> {
        self.arrive(id, now_ns);
        let used = self.conn_totals(id).control;
        if used != 0 && used.saturating_add(bytes.len()) > self.limits.control_queue_bytes {
            return Err(Close::SlowClient);
        }
        let seq = self.fresh_seq();
        let item = Item {
            kind: Kind::Control,
            stream: 0,
            bytes,
            dict: false,
            generation: 0,
            seq,
        };
        self.insert(id, item);
        Ok(())
    }

    /// Queue a subscription tick of `stream`: coalesce, pick the form, fit, insert.
    pub fn push_live(
        &mut self,
        id: ConnId,
        stream: u32,
        tick: &dyn TickForms,
        now_ns: u64,
    ) -> LiveOutcome {
        self.arrive(id, now_ns);
        if !self.conns.contains_key(&id) {
            return LiveOutcome::DroppedIncoming;
        }
        let replaced = self.take_unpinned_live(id, Some(stream)).map(|old| old.seq);
        let held = self.conns.get(&id);
        let state = held.map(|c| c.stream_state(stream)).unwrap_or_default();
        let generation = tick.generation();
        let first = state.last_pushed_gen == 0;
        let forced = first || state.needs_dict || state.last_pushed_gen != generation;
        let bare = if forced { None } else { tick.bare() };
        let (bytes, dict) = match bare {
            Some(bytes) => (bytes, false),
            None => (tick.dict_form(), true),
        };
        if !self.fit_live(id, bytes.len()) {
            self.note_drop(id, stream, dict);
            return LiveOutcome::DroppedIncoming;
        }
        let seq = match replaced {
            Some(seq) => seq,
            None => self.fresh_seq(),
        };
        let item = Item {
            kind: Kind::Live,
            stream,
            bytes,
            dict,
            generation,
            seq,
        };
        self.insert(id, item);
        if replaced.is_some() {
            LiveOutcome::Coalesced
        } else {
            LiveOutcome::Queued
        }
    }

    /// Queue a block of a query stream, or refuse it with `Backpressure` (nothing is shed).
    pub fn push_query(
        &mut self,
        id: ConnId,
        stream: u32,
        block: Arc<[u8]>,
        now_ns: u64,
    ) -> QueryOutcome {
        self.arrive(id, now_ns);
        let Some(conn) = self.conns.get_mut(&id) else {
            return QueryOutcome::Queued;
        };
        if !query_fits(&self.limits, &self.global, conn, block.len()) {
            return QueryOutcome::Backpressure(block);
        }
        conn.note_query_queued(stream);
        let seq = self.fresh_seq();
        let item = Item {
            kind: Kind::Query,
            stream,
            bytes: block,
            dict: false,
            generation: 0,
            seq,
        };
        self.insert(id, item);
        QueryOutcome::Queued
    }

    /// Every counted drop since [`SendHub::new`]: each increment of any stream's
    /// `pending_drops` (coalesce, shed, a dropped incoming tick, and the unpinned tick that
    /// [`SendHub::end_stream`] removes). `drain_unpinned` and `remove` count none.
    #[must_use]
    pub fn dropped_blocks(&self) -> u64 {
        self.counted_drops
    }

    /// Whether [`SendHub::push_query`] would answer `Queued` now for `len` bytes. The connection
    /// and global Query pools must both fit. An unknown connection answers `true`, because its
    /// push is discarded as `Queued`.
    #[must_use]
    pub fn query_room(&self, id: ConnId, len: usize) -> bool {
        match self.conns.get(&id) {
            Some(conn) => query_fits(&self.limits, &self.global, conn, len),
            None => true,
        }
    }

    /// End `stream`: remove its unpinned tick (counted), return the END counts, forget it.
    /// Queued Query entries of the stream stay.
    pub fn end_stream(&mut self, id: ConnId, stream: u32) -> StreamCounts {
        self.take_unpinned_live(id, Some(stream));
        match self.take_stream(id, stream) {
            Some(state) => StreamCounts {
                blocks: state.blocks,
                dropped: state.pending_drops,
            },
            None => StreamCounts::default(),
        }
    }

    /// The head entry as `[prefix, tail]`, the bytes not yet written. The first call for an
    /// entry pins it (stamping a block's prefix). `None` when the queue is empty.
    pub fn head(&mut self, id: ConnId, now_ns: u64) -> Option<[&[u8]; 2]> {
        let conn = self.conns.get_mut(&id)?;
        if conn.pin.is_none() {
            conn.pin_front(now_ns);
        }
        conn.remainder()
    }

    /// `true` when the head is pinned and some, not all, of its bytes are written.
    #[must_use]
    pub fn head_partial(&self, id: ConnId) -> bool {
        self.conns.get(&id).is_some_and(Conn::is_partial)
    }

    /// The writer wrote `n` bytes of the head. `n > 0` is progress; a completely written
    /// head is popped and its bytes leave its pools. Without a pinned head it is ignored.
    pub fn wrote(&mut self, id: ConnId, n: usize, now_ns: u64) {
        if let Some(conn) = self.conns.get_mut(&id) {
            if let Some(item) = conn.advance(n, now_ns) {
                if item.kind != Kind::Control {
                    self.global.release(item.kind, item.bytes.len());
                }
            }
        }
    }

    /// The connections to close at `now_ns`, by ascending id (strict `>` on both rules).
    #[must_use]
    pub fn poll(&self, now_ns: u64) -> Vec<(ConnId, Close)> {
        let mut closing = Vec::new();
        for (id, conn) in &self.conns {
            if conn.is_slow(now_ns, &self.limits) {
                closing.push((*id, Close::SlowClient));
            }
        }
        closing
    }

    /// The pools of one connection (zero for an unknown id).
    #[must_use]
    pub fn queued(&self, id: ConnId) -> Pools {
        self.conn_totals(id)
    }

    /// The global Live and Query pools (`control` is 0).
    #[must_use]
    pub fn global_pools(&self) -> Pools {
        self.global
    }

    fn fresh_seq(&mut self) -> u64 {
        let seq = self.next_seq;
        self.next_seq = self.next_seq.wrapping_add(1);
        seq
    }

    fn conn_totals(&self, id: ConnId) -> Pools {
        match self.conns.get(&id) {
            Some(conn) => conn.totals,
            None => Pools::default(),
        }
    }

    /// A push into an empty queue restarts the stall clock (judged before the push, so a
    /// coalesce or a shed never restarts it).
    fn arrive(&mut self, id: ConnId, now_ns: u64) {
        if let Some(conn) = self.conns.get_mut(&id) {
            conn.arrive(now_ns);
        }
    }

    /// Append `item` in sequence order (a replaced tick keeps its place), charge its pools and,
    /// for a Live entry, record the generation and form pushed on its stream.
    fn insert(&mut self, id: ConnId, item: Item) {
        if let Some(conn) = self.conns.get_mut(&id) {
            let (kind, stream, dict) = (item.kind, item.stream, item.dict);
            let (len, generation) = (item.bytes.len(), item.generation);
            let at = conn.queue.partition_point(|queued| queued.seq < item.seq);
            conn.queue.insert(at, item);
            conn.totals.charge(kind, len);
            if kind != Kind::Control {
                self.global.charge(kind, len);
            }
            if kind == Kind::Live {
                conn.mark_pushed(stream, generation, dict);
            }
        }
    }

    /// Remove the oldest unpinned Live entry of `id` (of `only` stream when given) from the
    /// queue and from every pool, counted as a drop on its stream.
    fn take_unpinned_live(&mut self, id: ConnId, only: Option<u32>) -> Option<Item> {
        let conn = self.conns.get_mut(&id)?;
        let item = conn.take_unpinned_live(only)?;
        conn.note_drop(item.stream, item.dict);
        self.counted_drops = self.counted_drops.saturating_add(1);
        if item.kind != Kind::Control {
            self.global.release(item.kind, item.bytes.len());
        }
        Some(item)
    }

    /// A tick of `stream` that did not fit is dropped and counted (the connection exists:
    /// `push_live` checked it).
    fn note_drop(&mut self, id: ConnId, stream: u32, dict: bool) {
        if let Some(conn) = self.conns.get_mut(&id) {
            conn.note_drop(stream, dict);
            self.counted_drops = self.counted_drops.saturating_add(1);
        }
    }

    fn take_stream(&mut self, id: ConnId, stream: u32) -> Option<Stream> {
        self.conns.get_mut(&id)?.streams.remove(&stream)
    }

    fn used(&self, level: Level) -> usize {
        match level {
            Level::Conn(id) => self.conn_totals(id).live,
            Level::Global => self.global.live,
        }
    }

    fn cap(&self, level: Level) -> usize {
        match level {
            Level::Conn(_) => self.limits.client_queue_bytes,
            Level::Global => self.limits.global_live_bytes,
        }
    }

    /// Fit `len` Live bytes into the connection then global pool, shedding victims; `false`
    /// when a pool has no victim left (entries already shed stay shed).
    fn fit_live(&mut self, id: ConnId, len: usize) -> bool {
        for level in [Level::Conn(id), Level::Global] {
            while self.used(level).saturating_add(len) > self.cap(level) {
                if !self.shed_for(level) {
                    return false;
                }
            }
        }
        true
    }

    fn shed_for(&mut self, level: Level) -> bool {
        let victim = match level {
            Level::Conn(id) => Some(id),
            Level::Global => self.victim(),
        };
        match victim {
            Some(id) => self.take_unpinned_live(id, None).is_some(),
            None => false,
        }
    }

    /// The connection with the most Live bytes among those that hold an unpinned Live entry;
    /// ties go to the lowest id (ascending iteration, strict `>`).
    fn victim(&self) -> Option<ConnId> {
        let mut best: Option<(ConnId, usize)> = None;
        for (id, conn) in &self.conns {
            if conn.find_unpinned_live(None).is_none() {
                continue;
            }
            let live = conn.totals.live;
            let better = match best {
                Some((_, top)) => live > top,
                None => true,
            };
            if better {
                best = Some((*id, live));
            }
        }
        best.map(|(id, _)| id)
    }
}

#[cfg(test)]
mod tests {
    use std::cell::Cell;
    use std::sync::Arc;

    use super::{
        Close, ConnId, LiveOutcome, Pools, QueryOutcome, QueueLimits, SendHub, StreamCounts,
        TickForms,
    };

    fn limits() -> QueueLimits {
        QueueLimits {
            client_queue_bytes: 1000,
            global_live_bytes: 8000,
            query_queue_bytes: 1000,
            global_query_bytes: 4000,
            control_queue_bytes: 500,
            stall_ns: 5000,
            frame_completion_ns: 5000,
        }
    }

    fn counts(blocks: u32, dropped: u32) -> StreamCounts {
        StreamCounts { blocks, dropped }
    }

    fn pools(live: usize, query: usize, control: usize) -> Pools {
        Pools {
            live,
            query,
            control,
        }
    }

    /// A BLOCK frame of `len` bytes (a multiple of 8) with `n_sec` sections: the header
    /// section (80 B), then empty sections, the last one holding the rest, filled with
    /// `tag`. `corr` and `dropped` hold 0xEE sentinels the hub must overwrite.
    fn block(n_sec: usize, len: usize, tag: u8) -> Arc<[u8]> {
        let dir_end = 16 + 8 * n_sec;
        let mut out = vec![tag; len];
        out[..dir_end + 80].fill(0);
        out[0..4].copy_from_slice(&u32::try_from(len).expect("len").to_le_bytes());
        out[4] = 5;
        out[5] = 1;
        out[8..12].fill(0xEE);
        out[12..14].copy_from_slice(&u16::try_from(n_sec).expect("n_sec").to_le_bytes());
        out[16..18].copy_from_slice(&1u16.to_le_bytes());
        out[18] = 1;
        out[19] = 1;
        out[20..24].copy_from_slice(&80u32.to_le_bytes());
        let last = dir_end - 8;
        let rest = u32::try_from(len - dir_end - 80).expect("rest");
        out[last..last + 2].copy_from_slice(&100u16.to_le_bytes());
        out[last + 2] = 1;
        out[last + 4..last + 8].copy_from_slice(&rest.to_le_bytes());
        out[dir_end + 72..dir_end + 76].fill(0xEE);
        Arc::from(out)
    }

    struct Tick {
        generation: u32,
        bare: Option<Arc<[u8]>>,
        dict: Arc<[u8]>,
        dict_calls: Cell<u32>,
    }

    impl TickForms for Tick {
        fn generation(&self) -> u32 {
            self.generation
        }

        fn bare(&self) -> Option<Arc<[u8]>> {
            self.bare.clone()
        }

        fn dict_form(&self) -> Arc<[u8]> {
            self.dict_calls.set(self.dict_calls.get() + 1);
            Arc::clone(&self.dict)
        }
    }

    fn tick(generation: u32, bare: Option<usize>, dict: usize) -> Tick {
        Tick {
            generation,
            bare: bare.map(|len| block(2, len, 0xB0)),
            dict: block(2, dict, 0xD0),
            dict_calls: Cell::new(0),
        }
    }

    fn rd(bytes: &[u8], at: usize) -> u32 {
        let raw = bytes.get(at..at + 4).expect("four bytes");
        u32::from_le_bytes(raw.try_into().expect("four bytes"))
    }

    /// Pin the head and report `(remaining bytes, corr, dropped)` (blocks of n_sec 2).
    fn head(hub: &mut SendHub, id: ConnId, now: u64) -> (usize, u32, u32) {
        let [prefix, tail] = hub.head(id, now).expect("a head");
        (prefix.len() + tail.len(), rd(prefix, 8), rd(prefix, 104))
    }

    fn add_all(hub: &mut SendHub, count: usize) -> Vec<ConnId> {
        (0..count).map(|_| hub.add(0)).collect()
    }

    /// Pin the head and report its unwritten bytes (0 for an empty queue or empty frame).
    fn remaining(hub: &mut SendHub, id: ConnId, now: u64) -> usize {
        match hub.head(id, now) {
            Some([prefix, tail]) => prefix.len() + tail.len(),
            None => 0,
        }
    }

    fn refused(outcome: &QueryOutcome) -> bool {
        matches!(outcome, QueryOutcome::Backpressure(_))
    }

    fn pop(hub: &mut SendHub, id: ConnId, now: u64) {
        let (remaining, _, _) = head(hub, id, now);
        hub.wrote(id, remaining, now);
    }

    fn live(hub: &mut SendHub, id: ConnId, s: u32, t: &Tick, now: u64) -> LiveOutcome {
        hub.push_live(id, s, t, now)
    }

    fn control(hub: &mut SendHub, id: ConnId, len: usize, now: u64) -> Result<(), Close> {
        hub.push_control(id, block(2, len, 0), now)
    }

    fn query(hub: &mut SendHub, id: ConnId, s: u32, len: usize) -> QueryOutcome {
        hub.push_query(id, s, block(2, len, 0xC0), 0)
    }

    /// The identity (`seq`, length) of every connection's pinned head, by ascending id.
    fn pinned(hub: &SendHub) -> Vec<Option<(u64, usize)>> {
        let mut out = Vec::new();
        for conn in hub.conns.values() {
            let head = conn.pin.as_ref().and(conn.queue.front());
            out.push(head.map(|item| (item.seq, item.bytes.len())));
        }
        out
    }

    /// Recompute every pool from the queues and compare with the counters.
    fn check(hub: &SendHub) {
        let mut global = pools(0, 0, 0);
        let mut pending = 0u64;
        for (id, conn) in &hub.conns {
            let mut sum = pools(0, 0, 0);
            let mut last_seq = None;
            for item in &conn.queue {
                *sum.slot(item.kind) += item.bytes.len();
                assert!(last_seq < Some(item.seq), "queue order is by seq");
                last_seq = Some(item.seq);
            }
            assert_eq!(conn.totals, sum, "connection pools");
            let fits = |used: usize, cap: usize| used.saturating_add(137) <= cap;
            let wanted = fits(conn.totals.query, hub.limits.query_queue_bytes)
                && fits(hub.global.query, hub.limits.global_query_bytes);
            assert_eq!(hub.query_room(*id, 137), wanted, "Query room");
            pending = pending.saturating_add(
                conn.streams
                    .values()
                    .map(|state| u64::from(state.pending_drops))
                    .sum::<u64>(),
            );
            global.live += sum.live;
            global.query += sum.query;
            if conn.pin.is_some() {
                assert!(!conn.queue.is_empty(), "a pin needs a head");
            }
            let skip = usize::from(conn.pin.is_some());
            let mut seen = Vec::new();
            for item in conn.queue.iter().skip(skip) {
                if item.kind == super::Kind::Live {
                    assert!(!seen.contains(&item.stream), "one unpinned tick");
                    seen.push(item.stream);
                }
            }
        }
        assert_eq!(hub.global, global, "global pools");
        assert!(
            hub.dropped_blocks() >= pending,
            "counted drops cannot trail pending drops"
        );
    }

    // ---- push_control -------------------------------------------------------------------

    #[test]
    fn control_row_an_empty_pool_admits_any_length_and_a_second_frame_closes() {
        let mut hub = SendHub::new(limits());
        let c = hub.add(0);
        assert_eq!(hub.push_control(c, block(2, 904, 1), 0), Ok(()));
        assert_eq!(hub.queued(c), pools(0, 0, 904));
        let second = hub.push_control(c, block(2, 112, 1), 0);
        assert_eq!(second, Err(Close::SlowClient));
        assert_eq!(hub.queued(c), pools(0, 0, 904));
        assert_eq!(hub.global_pools(), pools(0, 0, 0));
        check(&hub);
    }

    #[test]
    fn control_row_the_cap_is_inclusive_and_a_small_frame_fits_beside_blocks() {
        let mut hub = SendHub::new(limits());
        let c = hub.add(0);
        live(&mut hub, c, 1, &tick(1, None, 904), 0);
        assert_eq!(hub.push_control(c, block(2, 200, 1), 0), Ok(()));
        assert_eq!(hub.push_control(c, block(2, 300, 1), 0), Ok(()));
        assert_eq!(hub.queued(c), pools(904, 0, 500));
        let over = hub.push_control(c, block(2, 112, 1), 0);
        assert_eq!(over, Err(Close::SlowClient));
        check(&hub);
    }

    #[test]
    fn control_never_competes_with_blocks_and_is_never_dropped() {
        let mut hub = SendHub::new(limits());
        let c = hub.add(0);
        assert_eq!(hub.push_control(c, block(2, 400, 1), 0), Ok(()));
        let first = live(&mut hub, c, 1, &tick(1, None, 904), 0);
        assert_eq!(first, LiveOutcome::Queued);
        // The Live pool sheds, never control.
        let big = live(&mut hub, c, 2, &tick(1, None, 904), 0);
        assert_eq!(big, LiveOutcome::Queued);
        assert_eq!(hub.queued(c), pools(904, 0, 400));
        check(&hub);
    }

    // ---- push_live ----------------------------------------------------------------------

    #[test]
    fn live_row_coalesce_replaces_in_place_counts_and_keeps_the_place() {
        let mut hub = SendHub::new(limits());
        let c = hub.add(0);
        live(&mut hub, c, 1, &tick(1, Some(200), 240), 0);
        pop(&mut hub, c, 0);
        let first = live(&mut hub, c, 1, &tick(1, Some(200), 240), 0);
        assert_eq!(first, LiveOutcome::Queued);
        live(&mut hub, c, 2, &tick(1, Some(208), 240), 0);
        let newer = live(&mut hub, c, 1, &tick(1, Some(216), 240), 0);
        assert_eq!(newer, LiveOutcome::Coalesced);
        assert_eq!(hub.queued(c), pools(456, 0, 0));
        // The replaced entry kept its place: stream 1 is still the head.
        assert_eq!(head(&mut hub, c, 0), (216, 1, 1));
        check(&hub);
    }

    #[test]
    fn live_row_a_pinned_entry_is_never_coalesced() {
        let mut hub = SendHub::new(limits());
        let c = hub.add(0);
        live(&mut hub, c, 1, &tick(1, Some(200), 240), 0);
        assert_eq!(head(&mut hub, c, 0), (240, 1, 0));
        let next = live(&mut hub, c, 1, &tick(1, Some(200), 240), 0);
        assert_eq!(next, LiveOutcome::Queued);
        assert_eq!(hub.queued(c), pools(440, 0, 0));
        check(&hub);
    }

    #[test]
    fn live_row_form_rule_dictionary_for_first_new_generation_and_absent_bare() {
        let mut hub = SendHub::new(limits());
        let c = hub.add(0);
        let first = tick(1, Some(200), 240);
        live(&mut hub, c, 1, &first, 0);
        assert_eq!(first.dict_calls.get(), 1);
        pop(&mut hub, c, 0);
        let same = tick(1, Some(200), 240);
        live(&mut hub, c, 1, &same, 0);
        assert_eq!(same.dict_calls.get(), 0, "same generation, bare form");
        assert_eq!(hub.queued(c).live, 200);
        pop(&mut hub, c, 0);
        let skipped = tick(2, Some(200), 248);
        live(&mut hub, c, 1, &skipped, 0);
        assert_eq!(hub.queued(c).live, 248);
        pop(&mut hub, c, 0);
        let changing = tick(2, None, 256);
        live(&mut hub, c, 1, &changing, 0);
        assert_eq!(hub.queued(c).live, 256, "absent bare: dictionary form");
        assert_eq!(changing.dict_calls.get(), 1, "dict_form called once");
        check(&hub);
    }

    #[test]
    fn live_row_the_first_tick_is_a_dictionary_form_even_with_generation_zero() {
        let mut hub = SendHub::new(limits());
        let c = hub.add(0);
        let first = tick(0, Some(200), 240);
        live(&mut hub, c, 1, &first, 0);
        assert_eq!(hub.queued(c).live, 240);
        assert_eq!(first.dict_calls.get(), 1);
        check(&hub);
    }

    #[test]
    fn live_row_coalescing_a_dictionary_entry_makes_the_replacement_dictionary_bearing() {
        let mut hub = SendHub::new(limits());
        let c = hub.add(0);
        live(&mut hub, c, 1, &tick(1, Some(200), 240), 0);
        let again = live(&mut hub, c, 1, &tick(1, Some(200), 240), 0);
        assert_eq!(again, LiveOutcome::Coalesced);
        assert_eq!(hub.queued(c).live, 240);
        pop(&mut hub, c, 0);
        live(&mut hub, c, 1, &tick(1, Some(200), 240), 0);
        assert_eq!(hub.queued(c).live, 200);
        check(&hub);
    }

    #[test]
    fn live_row_a_dropped_dictionary_tick_leaves_needs_dict_sticky() {
        let mut hub = SendHub::new(limits());
        let c = hub.add(0);
        live(&mut hub, c, 2, &tick(1, Some(300), 400), 0);
        pop(&mut hub, c, 0);
        live(&mut hub, c, 1, &tick(1, Some(300), 400), 0);
        head(&mut hub, c, 0);
        let lost = live(&mut hub, c, 2, &tick(1, None, 904), 1);
        assert_eq!(lost, LiveOutcome::DroppedIncoming);
        let again = live(&mut hub, c, 2, &tick(1, Some(300), 904), 2);
        assert_eq!(again, LiveOutcome::DroppedIncoming);
        pop(&mut hub, c, 3);
        live(&mut hub, c, 2, &tick(1, Some(300), 400), 60_000);
        assert_eq!(head(&mut hub, c, 60_000), (400, 2, 2));
        check(&hub);
    }

    #[test]
    fn live_row_fit_sheds_the_oldest_unpinned_entry_of_the_connection() {
        let mut hub = SendHub::new(limits());
        let c = hub.add(0);
        for s in 1..=2 {
            live(&mut hub, c, s, &tick(1, None, 400), 0);
        }
        head(&mut hub, c, 0);
        let third = live(&mut hub, c, 3, &tick(1, None, 400), 0);
        assert_eq!(third, LiveOutcome::Queued);
        // Pinned head 400 plus the new 400; stream 2 was shed.
        assert_eq!(hub.queued(c), pools(800, 0, 0));
        pop(&mut hub, c, 0);
        assert_eq!(head(&mut hub, c, 0), (400, 3, 0));
        assert_eq!(hub.end_stream(c, 2), counts(0, 1));
        check(&hub);
    }

    #[test]
    fn live_row_with_no_victim_the_incoming_tick_is_dropped_and_counted() {
        let mut hub = SendHub::new(limits());
        let c = hub.add(0);
        live(&mut hub, c, 1, &tick(1, None, 904), 0);
        head(&mut hub, c, 0);
        let lost = live(&mut hub, c, 2, &tick(1, None, 904), 0);
        assert_eq!(lost, LiveOutcome::DroppedIncoming);
        assert_eq!(hub.queued(c), pools(904, 0, 0));
        assert_eq!(hub.end_stream(c, 2), counts(0, 1));
        check(&hub);
    }

    #[test]
    fn live_row_a_coalesced_tick_that_does_not_fit_is_two_drops() {
        let mut hub = SendHub::new(limits());
        let c = hub.add(0);
        live(&mut hub, c, 1, &tick(1, None, 600), 0);
        pop(&mut hub, c, 0);
        live(&mut hub, c, 2, &tick(1, None, 600), 0);
        head(&mut hub, c, 0);
        live(&mut hub, c, 1, &tick(1, Some(200), 240), 0);
        let big = live(&mut hub, c, 1, &tick(1, Some(504), 240), 0);
        assert_eq!(big, LiveOutcome::DroppedIncoming);
        assert_eq!(hub.end_stream(c, 1), counts(1, 2));
        check(&hub);
    }

    // ---- fit order and victims ----------------------------------------------------------

    #[test]
    fn fit_global_victim_is_the_largest_connection_with_an_unpinned_entry() {
        let mut limits = limits();
        limits.global_live_bytes = 1500;
        let mut hub = SendHub::new(limits);
        let ids: Vec<ConnId> = (1..=3).map(|_| hub.add(0)).collect();
        live(&mut hub, ids[0], 1, &tick(1, None, 400), 0);
        live(&mut hub, ids[0], 2, &tick(1, None, 400), 0);
        live(&mut hub, ids[1], 1, &tick(1, None, 400), 0);
        live(&mut hub, ids[2], 1, &tick(1, None, 200), 0);
        let pushed = live(&mut hub, ids[1], 2, &tick(1, None, 400), 0);
        assert_eq!(pushed, LiveOutcome::Queued);
        assert_eq!(hub.queued(ids[0]).live, 400);
        assert_eq!(hub.queued(ids[1]).live, 800);
        assert_eq!(hub.end_stream(ids[0], 1).dropped, 1);
        check(&hub);
    }

    #[test]
    fn fit_victim_skips_a_pinned_only_connection_and_ties_go_to_the_lowest_id() {
        let mut limits = limits();
        limits.global_live_bytes = 1200;
        let mut hub = SendHub::new(limits);
        let a = hub.add(0);
        let b = hub.add(0);
        let c = hub.add(0);
        let d = hub.add(0);
        live(&mut hub, a, 1, &tick(1, None, 500), 0);
        head(&mut hub, a, 0);
        live(&mut hub, b, 1, &tick(1, None, 200), 0);
        live(&mut hub, c, 1, &tick(1, None, 200), 0);
        assert_eq!(hub.global_pools().live, 900);
        let pushed = live(&mut hub, d, 1, &tick(1, None, 400), 0);
        assert_eq!(pushed, LiveOutcome::Queued);
        assert_eq!(hub.queued(a).live, 500);
        assert_eq!(hub.queued(b).live, 0, "tie: lowest id is shed");
        assert_eq!(hub.queued(c).live, 200);
        assert_eq!(hub.queued(d).live, 400);
        check(&hub);
    }

    #[test]
    fn fit_order_connection_before_global() {
        let mut limits = limits();
        limits.global_live_bytes = 1800;
        let mut hub = SendHub::new(limits);
        let ids = add_all(&mut hub, 2);
        for s in 1..=2 {
            live(&mut hub, ids[0], s, &tick(1, None, 400), 0);
        }
        for (s, len) in [(1, 400), (2, 400), (3, 112)] {
            live(&mut hub, ids[1], s, &tick(1, None, len), 0);
        }
        let pushed = live(&mut hub, ids[0], 3, &tick(1, None, 300), 0);
        assert_eq!(pushed, LiveOutcome::Queued);
        // The connection shed (400) already made room globally: the other connection is spared.
        assert_eq!(hub.queued(ids[0]).live, 700);
        assert_eq!(hub.queued(ids[1]).live, 912);
        assert_eq!(hub.global_pools().live, 1612);
        check(&hub);
    }

    // ---- push_query, query_room ---------------------------------------------------------

    #[test]
    fn query_row_backpressure_refuses_without_waiting_and_room_follows_freed_bytes() {
        let mut hub = SendHub::new(limits());
        let c = hub.add(0);
        assert!(hub.query_room(c, 600));
        assert_eq!(query(&mut hub, c, 1, 600), QueryOutcome::Queued);
        assert!(!hub.query_room(c, 600));
        let second = query(&mut hub, c, 1, 600);
        assert!(refused(&second));
        assert_eq!(second, QueryOutcome::Backpressure(block(2, 600, 0xC0)));
        head(&mut hub, c, 0);
        assert!(!hub.query_room(c, 600), "a pinned head frees nothing");
        hub.wrote(c, 600, 0);
        assert!(hub.query_room(c, 600));
        assert_eq!(query(&mut hub, c, 1, 600), QueryOutcome::Queued);
        assert!(!hub.query_room(c, 600), "the queued block refills the pool");
        assert_eq!(hub.end_stream(c, 1), counts(2, 0));
        check(&hub);
    }

    #[test]
    fn query_row_each_pool_refuses_and_room_tracks_the_same_pools() {
        let mut limits = limits();
        limits.global_query_bytes = 1200;
        let mut hub = SendHub::new(limits);
        let a = hub.add(0);
        let b = hub.add(0);
        query(&mut hub, a, 1, 700);
        query(&mut hub, b, 1, 400);
        let global = query(&mut hub, b, 2, 200);
        assert!(refused(&global), "global pool refuses");
        pop(&mut hub, a, 0);
        assert!(hub.query_room(b, 200), "a's bytes left the global pool");
        assert_eq!(query(&mut hub, b, 2, 200), QueryOutcome::Queued);
        check(&hub);
    }

    #[test]
    fn connections_share_only_the_connection_and_global_pools() {
        let mut limits = limits();
        limits.global_query_bytes = 4000;
        limits.global_live_bytes = 8000;
        let mut hub = SendHub::new(limits);
        let ids = add_all(&mut hub, 3);
        for id in &ids {
            assert_eq!(query(&mut hub, *id, 1, 1000), QueryOutcome::Queued);
            live(&mut hub, *id, 2, &tick(1, None, 1000), 0);
        }
        assert_eq!(hub.global_pools(), pools(3000, 3000, 0));
        for id in &ids {
            assert_eq!(hub.queued(*id), pools(1000, 1000, 0));
        }
        check(&hub);
    }

    #[test]
    fn hub_query_pin_leaves_pending_drops() {
        let mut hub = SendHub::new(limits());
        let c = hub.add(0);
        query(&mut hub, c, 3, 200);
        assert_eq!(hub.end_stream(c, 3), counts(1, 0));
        live(&mut hub, c, 3, &tick(1, Some(200), 240), 0);
        assert_eq!(
            live(&mut hub, c, 3, &tick(1, Some(200), 240), 0),
            LiveOutcome::Coalesced
        );
        assert_eq!(head(&mut hub, c, 0), (200, 3, 0), "old Query pin");
        hub.wrote(c, 200, 0);
        assert_eq!(head(&mut hub, c, 0), (240, 3, 1), "new Live pin");
        check(&hub);
    }

    #[test]
    fn hub_query_room_checks_each_pool_and_unknown_ids() {
        let mut hub = SendHub::new(limits());
        let c0 = hub.add(0);
        let c1 = hub.add(0);
        query(&mut hub, c0, 1, 600);
        assert!(hub.query_room(c0, 400));
        assert!(!hub.query_room(c0, 401), "connection pool");
        query(&mut hub, c1, 1, 400);
        assert!(hub.query_room(c0, 1), "another connection does not cap c0");
        assert!(hub.query_room(ConnId(999), usize::MAX));

        let mut global_limits = limits();
        global_limits.global_query_bytes = 1500;
        let mut global = SendHub::new(global_limits);
        let a = global.add(0);
        let b = global.add(0);
        query(&mut global, a, 1, 1000);
        query(&mut global, b, 1, 400);
        assert!(!global.query_room(b, 101), "global pool");
        check(&hub);
        check(&global);
    }

    #[test]
    fn hub_dropped_blocks_counts_every_note_drop() {
        let mut hub = SendHub::new(limits());
        let c = hub.add(0);
        live(&mut hub, c, 1, &tick(1, Some(304), 304), 0);
        live(&mut hub, c, 1, &tick(1, Some(304), 304), 0);
        assert_eq!(hub.dropped_blocks(), 1, "coalesce");
        live(&mut hub, c, 2, &tick(1, Some(304), 304), 0);
        live(&mut hub, c, 3, &tick(1, Some(304), 304), 0);
        live(&mut hub, c, 4, &tick(1, Some(304), 304), 0);
        assert_eq!(hub.dropped_blocks(), 2, "shed");
        head(&mut hub, c, 0);
        live(&mut hub, c, 5, &tick(1, Some(304), 304), 0);
        assert_eq!(hub.dropped_blocks(), 3, "shed behind a pinned head");
        hub.drain_unpinned(c);
        assert_eq!(hub.dropped_blocks(), 3, "drain counts none");
        pop(&mut hub, c, 0);
        live(&mut hub, c, 6, &tick(1, Some(904), 904), 0);
        head(&mut hub, c, 0);
        live(&mut hub, c, 7, &tick(1, Some(904), 904), 0);
        assert_eq!(hub.dropped_blocks(), 4, "dropped incoming");
        pop(&mut hub, c, 0);
        live(&mut hub, c, 8, &tick(1, Some(304), 304), 0);
        hub.end_stream(c, 8);
        assert_eq!(hub.dropped_blocks(), 5, "end_stream");
        live(&mut hub, c, 8, &tick(1, Some(304), 304), 0);
        hub.drain_unpinned(c);
        assert_eq!(hub.dropped_blocks(), 5);
        live(&mut hub, c, 8, &tick(1, Some(304), 304), 0);
        hub.remove(c);
        assert_eq!(hub.dropped_blocks(), 5, "remove counts none");
        check(&hub);
    }

    #[test]
    fn query_room_counts_only_query_bytes() {
        let mut hub = SendHub::new(limits());
        let c = hub.add(0);
        live(&mut hub, c, 2, &tick(1, None, 240), 0);
        assert_eq!(query(&mut hub, c, 1, 600), QueryOutcome::Queued);
        assert!(!hub.query_room(c, 600));
        let second = query(&mut hub, c, 1, 600);
        assert!(refused(&second));
        pop(&mut hub, c, 0);
        assert_eq!(hub.queued(c), pools(0, 600, 0), "the Live entry is gone");
        assert!(!hub.query_room(c, 600), "Live bytes are not Query room");
        pop(&mut hub, c, 0);
        assert!(hub.query_room(c, 600));
        check(&hub);
    }

    #[test]
    fn query_row_never_sheds_ticks_and_ticks_never_displace_queries() {
        let mut limits = limits();
        limits.global_live_bytes = 800;
        limits.query_queue_bytes = 500;
        let mut hub = SendHub::new(limits);
        let a = hub.add(0);
        let b = hub.add(0);
        live(&mut hub, b, 1, &tick(1, None, 400), 0);
        live(&mut hub, b, 2, &tick(1, None, 400), 0);
        assert_eq!(query(&mut hub, a, 1, 400), QueryOutcome::Queued);
        let full = query(&mut hub, a, 2, 112);
        assert!(refused(&full));
        assert_eq!(hub.queued(b).live, 800, "no tick shed by a query");
        live(&mut hub, a, 3, &tick(1, None, 200), 0);
        assert_eq!(hub.queued(a), pools(200, 400, 0), "query kept");
        check(&hub);
    }

    // ---- end_stream ---------------------------------------------------------------------

    #[test]
    fn end_stream_row_live_removes_the_tick_and_returns_counts() {
        let mut hub = SendHub::new(limits());
        let c = hub.add(0);
        live(&mut hub, c, 1, &tick(1, Some(200), 240), 0);
        head(&mut hub, c, 0);
        live(&mut hub, c, 1, &tick(1, Some(200), 240), 0);
        let ended = hub.end_stream(c, 1);
        assert_eq!(ended, counts(1, 1));
        assert_eq!(hub.queued(c), pools(240, 0, 0));
        assert_eq!(hub.end_stream(c, 1), counts(0, 0), "forgotten");
        check(&hub);
    }

    #[test]
    fn end_stream_row_query_counts_queued_blocks_and_leaves_them_queued() {
        let mut hub = SendHub::new(limits());
        let c = hub.add(0);
        for _ in 0..3 {
            query(&mut hub, c, 2, 200);
        }
        head(&mut hub, c, 0);
        assert_eq!(hub.end_stream(c, 2), counts(3, 0));
        assert_eq!(hub.queued(c), pools(0, 600, 0));
        hub.wrote(c, 200, 0);
        assert_eq!(head(&mut hub, c, 0), (200, 2, 0));
        check(&hub);
    }

    // ---- head, wrote, head_partial, poll ------------------------------------------------

    #[test]
    fn head_row_pins_stamps_and_shares_one_tail_between_connections() {
        let mut hub = SendHub::new(limits());
        let a = hub.add(0);
        let b = hub.add(0);
        let shared = tick(1, None, 240);
        live(&mut hub, a, 1, &shared, 0);
        live(&mut hub, b, 7, &shared, 0);
        live(&mut hub, b, 7, &shared, 0);
        let [pa, ta] = hub.head(a, 5).expect("head a");
        let (corr_a, dropped_a, len_a) = (rd(pa, 8), rd(pa, 104), pa.len());
        let tail_a = ta.as_ptr();
        let [pb, tb] = hub.head(b, 5).expect("head b");
        assert_eq!((corr_a, dropped_a), (1, 0));
        assert_eq!((rd(pb, 8), rd(pb, 104)), (7, 1));
        assert_eq!(len_a, 112);
        assert_eq!(tail_a, tb.as_ptr(), "one Arc, one tail");
        assert_eq!(shared.dict_calls.get(), 3, "one call per push");
        check(&hub);
    }

    #[test]
    fn head_row_returns_the_unwritten_rest_and_wrote_pops_a_complete_head() {
        let mut hub = SendHub::new(limits());
        let c = hub.add(0);
        assert!(hub.head(c, 0).is_none(), "empty queue");
        live(&mut hub, c, 1, &tick(1, None, 240), 0);
        assert!(!hub.head_partial(c));
        assert_eq!(head(&mut hub, c, 0), (240, 1, 0));
        hub.wrote(c, 100, 1);
        assert!(hub.head_partial(c));
        let [prefix, tail] = hub.head(c, 9).expect("rest");
        assert_eq!((prefix.len(), tail.len()), (12, 128));
        hub.wrote(c, 12 + 128, 2);
        assert!(!hub.head_partial(c));
        assert_eq!(hub.queued(c), pools(0, 0, 0));
        assert_eq!(hub.global_pools(), pools(0, 0, 0));
        check(&hub);
    }

    #[test]
    fn wrote_without_a_pinned_head_and_on_unknown_connections_is_ignored() {
        let mut hub = SendHub::new(limits());
        let c = hub.add(0);
        live(&mut hub, c, 1, &tick(1, None, 240), 0);
        hub.wrote(c, 50, 4);
        assert_eq!(hub.queued(c).live, 240);
        hub.wrote(ConnId(99), 5, 0);
        assert_eq!(hub.queued(ConnId(99)), pools(0, 0, 0));
        assert!(hub.head(ConnId(99), 0).is_none());
        assert!(!hub.head_partial(ConnId(99)));
        assert_eq!(hub.push_control(ConnId(99), block(2, 112, 0), 0), Ok(()));
        let gone = live(&mut hub, ConnId(99), 1, &tick(1, None, 240), 0);
        assert_eq!(gone, LiveOutcome::DroppedIncoming);
        assert_eq!(query(&mut hub, ConnId(99), 1, 200), QueryOutcome::Queued);
        assert!(hub.query_room(ConnId(99), 200));
        assert_eq!(hub.end_stream(ConnId(99), 1), StreamCounts::default());
        hub.drain_unpinned(ConnId(99));
        hub.remove(ConnId(99));
        check(&hub);
    }

    #[test]
    fn poll_row_frame_completion_is_strict_and_progress_does_not_save_a_dribbler() {
        let mut hub = SendHub::new(limits());
        let c = hub.add(0);
        live(&mut hub, c, 1, &tick(1, None, 240), 0);
        head(&mut hub, c, 0);
        hub.wrote(c, 1, 4000);
        assert!(hub.poll(4000).is_empty());
        assert!(hub.poll(5000).is_empty());
        assert_eq!(hub.poll(5001), vec![(c, Close::SlowClient)]);
    }

    #[test]
    fn poll_row_stall_needs_no_head_and_is_strict_from_the_last_byte() {
        let mut hub = SendHub::new(limits());
        let c = hub.add(0);
        control(&mut hub, c, 112, 1000).expect("admitted");
        assert!(hub.poll(6000).is_empty());
        assert_eq!(hub.poll(6001), vec![(c, Close::SlowClient)]);
    }

    #[test]
    fn poll_row_an_idle_queue_never_closes_and_the_first_push_restarts_the_clock() {
        let mut hub = SendHub::new(limits());
        let c = hub.add(0);
        assert!(hub.poll(60_000).is_empty());
        live(&mut hub, c, 1, &tick(1, None, 240), 60_000);
        assert!(hub.poll(60_001).is_empty());
        assert!(hub.poll(65_000).is_empty());
        assert_eq!(hub.poll(65_001), vec![(c, Close::SlowClient)]);
    }

    #[test]
    fn poll_row_a_replaced_or_shedding_push_does_not_restart_the_clock() {
        let mut hub = SendHub::new(limits());
        let c = hub.add(0);
        live(&mut hub, c, 1, &tick(1, None, 240), 0);
        let again = live(&mut hub, c, 1, &tick(1, None, 240), 4000);
        assert_eq!(again, LiveOutcome::Coalesced);
        assert!(hub.poll(5000).is_empty());
        let d = hub.add(0);
        live(&mut hub, d, 1, &tick(1, None, 400), 0);
        let shed = live(&mut hub, d, 2, &tick(1, None, 700), 4000);
        assert_eq!(shed, LiveOutcome::Queued);
        assert_eq!(hub.queued(d), pools(700, 0, 0));
        let want = vec![(c, Close::SlowClient), (d, Close::SlowClient)];
        assert_eq!(hub.poll(5001), want);
    }

    #[test]
    fn wrote_zero_bytes_is_no_progress() {
        let mut hub = SendHub::new(limits());
        let c = hub.add(0);
        live(&mut hub, c, 1, &tick(1, None, 240), 0);
        head(&mut hub, c, 3000);
        hub.wrote(c, 0, 4000);
        assert!(hub.poll(5000).is_empty());
        assert_eq!(hub.poll(5001), vec![(c, Close::SlowClient)]);
    }

    #[test]
    fn poll_results_are_by_ascending_connection_id() {
        let mut hub = SendHub::new(limits());
        let ids: Vec<ConnId> = (1..=3).map(|_| hub.add(0)).collect();
        for id in ids.iter().rev() {
            control(&mut hub, *id, 112, 0).expect("admitted");
        }
        let closed = hub.poll(5001);
        let want: Vec<(ConnId, Close)> = ids.iter().map(|id| (*id, Close::SlowClient)).collect();
        assert_eq!(closed, want);
    }

    // ---- drain_unpinned, remove, ids, malformed blocks ----------------------------------

    #[test]
    fn drain_unpinned_row_keeps_a_pinned_head_and_releases_everything_else() {
        let mut hub = SendHub::new(limits());
        let c = hub.add(0);
        live(&mut hub, c, 1, &tick(1, None, 240), 0);
        live(&mut hub, c, 2, &tick(1, None, 240), 0);
        query(&mut hub, c, 3, 200);
        control(&mut hub, c, 120, 0).expect("admitted");
        head(&mut hub, c, 0);
        hub.drain_unpinned(c);
        assert_eq!(hub.queued(c), pools(240, 0, 0));
        assert_eq!(hub.global_pools(), pools(240, 0, 0));
        hub.drain_unpinned(c);
        assert_eq!(hub.queued(c), pools(240, 0, 0));
        check(&hub);
    }

    #[test]
    fn drain_unpinned_row_a_removed_dictionary_tick_sets_needs_dict_uncounted() {
        let mut hub = SendHub::new(limits());
        let c = hub.add(0);
        live(&mut hub, c, 1, &tick(1, Some(200), 240), 0);
        live(&mut hub, c, 2, &tick(1, Some(200), 240), 0);
        head(&mut hub, c, 0);
        hub.drain_unpinned(c);
        assert_eq!(hub.queued(c), pools(240, 0, 0));
        let again = live(&mut hub, c, 2, &tick(1, Some(200), 240), 0);
        assert_eq!(again, LiveOutcome::Queued);
        assert_eq!(hub.queued(c), pools(480, 0, 0), "dict form again");
        pop(&mut hub, c, 0);
        assert_eq!(head(&mut hub, c, 0), (240, 2, 0), "uncounted");
        check(&hub);
    }

    #[test]
    fn remove_row_releases_every_byte_and_forgets_the_connection() {
        let mut hub = SendHub::new(limits());
        let a = hub.add(0);
        let b = hub.add(0);
        live(&mut hub, a, 1, &tick(1, None, 240), 0);
        query(&mut hub, a, 2, 200);
        live(&mut hub, b, 1, &tick(1, None, 240), 0);
        hub.remove(a);
        assert_eq!(hub.queued(a), pools(0, 0, 0));
        assert_eq!(hub.global_pools(), pools(240, 0, 0));
        hub.remove(b);
        assert_eq!(hub.global_pools(), pools(0, 0, 0));
        check(&hub);
    }

    #[test]
    fn ids_increase_from_one_and_a_wrapped_id_in_use_is_skipped() {
        let mut hub = SendHub::new(limits());
        assert_eq!(hub.add(0), ConnId(1));
        hub.next_id = u32::MAX;
        assert_eq!(hub.add(0), ConnId(u32::MAX));
        assert_eq!(hub.add(0), ConnId(2), "0 is never an id, 1 is in use");
        let mut fresh = SendHub::new(limits());
        fresh.next_id = u32::MAX;
        assert_eq!(fresh.add(0), ConnId(u32::MAX));
        assert_eq!(fresh.add(0), ConnId(1), "a wrap skips 0");
    }

    #[test]
    fn dropped_saturates_and_a_block_shorter_than_its_header_is_stamped_as_far_as_it_goes() {
        let mut hub = SendHub::new(limits());
        let c = hub.add(0);
        live(&mut hub, c, 1, &tick(1, Some(200), 240), 0);
        pop(&mut hub, c, 0);
        if let Some(conn) = hub.conns.get_mut(&c) {
            conn.streams.entry(1).or_default().pending_drops = u32::MAX;
        }
        live(&mut hub, c, 1, &tick(1, Some(200), 240), 0);
        live(&mut hub, c, 1, &tick(1, Some(200), 240), 0);
        assert_eq!(head(&mut hub, c, 0), (200, 1, u32::MAX));
        hub.push_query(c, 9, Arc::from(vec![7u8; 10]), 0);
        hub.push_query(c, 8, Arc::from(Vec::<u8>::new()), 0);
        hub.wrote(c, 200, 0);
        let [prefix, tail] = hub.head(c, 0).expect("short block");
        assert_eq!((prefix.len(), tail.len()), (10, 0));
        assert_eq!(prefix, &[7u8; 10][..]);
        hub.wrote(c, 10, 0);
        assert_eq!(remaining(&mut hub, c, 0), 0);
        hub.wrote(c, 0, 0);
        assert!(hub.head(c, 0).is_none());
        check(&hub);
    }

    #[test]
    fn pools_stay_exact_over_a_pseudo_random_run_of_every_operation() {
        let mut limits = limits();
        limits.global_live_bytes = 3000;
        limits.global_query_bytes = 1500;
        let mut hub = SendHub::new(limits);
        let ids = add_all(&mut hub, 4);
        let mut seed: u64 = 0x2545_F491_4F6C_DD1D;
        let mut now = 0u64;
        for _ in 0..4000 {
            seed = seed.wrapping_mul(6_364_136_223_846_793_005);
            seed = seed.wrapping_add(1_442_695_040_888_963_407);
            let pick = usize::try_from(seed >> 33).expect("fits");
            let id = ids[pick % ids.len()];
            let stream = u32::try_from(pick / 4 % 5).expect("small");
            let len = 120 + 8 * (pick / 20 % 40);
            now += 100;
            let op = pick / 800 % 12;
            let before = pinned(&hub);
            match op {
                0..=3 => {
                    let generation = u32::try_from(pick / 9600 % 2).expect("small") + 1;
                    let forms = tick(generation, Some(len), len + 40);
                    live(&mut hub, id, stream, &forms, now);
                }
                4 => {
                    query(&mut hub, id, stream, len);
                }
                5 => {
                    let _ = hub.push_control(id, block(2, len, 0), now);
                }
                6..=8 => {
                    let rest = remaining(&mut hub, id, now);
                    hub.wrote(id, rest.min(len), now);
                }
                9 => {
                    hub.end_stream(id, stream);
                }
                10 => hub.drain_unpinned(id),
                _ => {
                    let _ = hub.poll(now);
                }
            }
            if !(6..=8).contains(&op) {
                assert_eq!(pinned(&hub), before, "only wrote removes a pinned head");
            }
            check(&hub);
        }
    }
}
