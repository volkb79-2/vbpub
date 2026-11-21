//! The server layer of the probe's serving side (design sections 1 and 3, R20, R22, R23, R27,
//! R28; WIRE.md sections 4.5, 5.1, 5.3 and 5.4), sans-IO like [`Conn`]: no sockets,
//! no threads, no clock reads.
//!
//! A [`Server`] owns ONE `SendHub`, the connections keyed by `ConnId` (iterated in ascending id
//! order), the per-level and per-uid connection counters, the per-level [`Limits`] and the
//! backend. A single-threaded driver calls [`Server::accept`], [`Server::on_bytes`],
//! [`Server::on_eof`], [`Server::poll`], [`Server::output`] and [`Server::wrote`], and finishes
//! every connection it is done with.
//!
//! # Contracts
//!
//! - **`accept` (WIRE.md section 5.4 `refuse`).** `Control` is never served by the probe (design
//!   section 1: it belongs to `act`), a missing peer has no identity, a level at its
//!   `max_connections` or a uid at its
//!   `max_connections_per_uid` on that level is full: all four answer [`Accept::Refused`] with
//!   no hub entry, no counter and no byte. Otherwise the connection is added to the hub with the
//!   peer's uid, a [`Conn`] is created and the counters grow (accept when `count < cap`). The peer
//!   is captured once: every later decrement and hub call uses the stored uid (R23).
//! - **`finish` is the single release path (R22).** It decrements the level and uid counters,
//!   deletes a uid entry that reaches 0, removes the connection from the hub and drops it. It is
//!   idempotent and is the only place that does so; every close path ends in it: the driver
//!   calls it after taking the final bytes (and for an I/O error or a mid-stream drop), and
//!   [`Server::poll`] and [`Server::shutdown`] call it for a closed connection that holds no
//!   final bytes (a silent close, a completed `Draining`, a partly written head). A closed
//!   connection that still holds final bytes stays until the driver took them
//!   ([`Server::take_final`]) and finished it.
//! - **A gone connection is closed.** [`Server::is_closed`] is `true` for an id the server does
//!   not know, so a driver that asks after a sweep closes its socket.
//! - **Writing goes through [`Server::wrote`] only**, which delegates to `Conn::on_written` (that
//!   calls the hub's `wrote` itself, R2); the server never calls the hub's `wrote`.
//!   [`Server::output`] pins the head: never call it to test emptiness.
//! - **Timer order (R20), per [`Server::poll`].** Connections are polled in `ConnId` order
//!   (the hello deadline, the reader's age, idle); then the hub's `poll` closes the stalled or
//!   dribbled ones with `slow-client` (again in id order); the first fatal outcome of a
//!   connection wins, so a tie is settled by the order: the hello deadline against the writer
//!   stall on the unread server prefix, and the reader's age (`deadline`) against a stall due in
//!   the same `poll` (the reader's age wins, the hub's `slow-client` finds a closed connection).
//! - **One hub, per-level caps.** The hub's caps are the widest of the served levels
//!   ([`ServerLimits::queue_limits`]). The observer's smaller `control_queue_bytes` (design
//!   R28) is enforced by the server after every `on_bytes`: a connection whose queued control
//!   bytes exceed its own level's cap is closed with `slow-client`. A frame is at most
//!   `max_reply_bytes`, below the cap, so this equals the hub's rule (a frame into an empty
//!   control queue is always admitted).
//! - **Every push path ends in `enforce_control`.** The server hands a connection the hub and
//!   the backend through one private method (`drive`), which runs the control-cap check right
//!   after the connection returns. Every path that can queue a frame of a connection (today
//!   `on_bytes`; P5b-2 adds the EVT, END, live and query pushes) MUST go through it and never
//!   call into a `Conn` directly, so a new push cannot forget the per-level cap.
//! - **One stall, one frame-completion value.** The hub has a single value of each, so
//!   [`Server::new`] sets `stall_ms` and `frame_completion_ms` of every served level to the
//!   widest of the three ([`ServerLimits::unified`]): what `hello.limits` announces is what the
//!   hub enforces, and a mismatched [`ServerLimits`] cannot be silently half-applied.
//!   [`Server::limits`] returns the unified bounds.
//! - **`status` (WIRE.md section 4.5).** The backend handed to the connections is wrapped so that
//!   `clients` carries the number of connections of each served level that are not closed
//!   (observe, operator, observer, in that order, 0 included). A closed connection that waits
//!   for its final bytes is no longer a client, but it keeps its slot toward the caps
//!   (`accept`) until [`Server::finish`] released it.
//! - **Shutdown has no clock and no flag.** [`Server::shutdown`] closes every connection with
//!   `shutting-down` ([`Conn::close_for`] reads no time). The server keeps no stopping flag: the
//!   driver stops calling [`Server::accept`] after `shutdown` (an `accept` after it would
//!   register a connection that nothing closes).

use std::collections::{BTreeMap, HashMap};
use std::hash::Hash;

use hostobs_proto::hub::{ConnId, QueueLimits, SendHub};

use crate::backend::{Backend, BackendError, Identity, StatusInfo, TickData};
use crate::codes::Code;
use crate::conn::{Conn, Peer};
use crate::limits::{Level, Limits};
use crate::registry::{SelectionStreams, StreamLimits};
use crate::selection::Selection;
use crate::tick::BlockBounds;

/// The levels the probe serves, in the order `status.clients` lists them.
const SERVED: [Level; 3] = [Level::Observe, Level::Operator, Level::Observer];

/// The per-level bounds of the served levels (`Control` belongs to `act`).
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ServerLimits {
    /// The bounds of the `observe` level.
    pub observe: Limits,
    /// The bounds of the `operator` level.
    pub operator: Limits,
    /// The bounds of the `observer` level.
    pub observer: Limits,
}

impl ServerLimits {
    /// The WIRE.md section 5.1 defaults of every served level. The fields are public, but the
    /// two timers `stall_ms` and `frame_completion_ms` are one value for the whole server:
    /// [`Server::new`] unifies them ([`ServerLimits::unified`]).
    #[must_use]
    pub fn new() -> ServerLimits {
        ServerLimits {
            observe: Limits::for_level(Level::Observe),
            operator: Limits::for_level(Level::Operator),
            observer: Limits::for_level(Level::Observer),
        }
    }

    /// The bounds of `level`; `None` for `Control`, which the probe never serves.
    #[must_use]
    pub fn get(&self, level: Level) -> Option<&Limits> {
        match level {
            Level::Observe => Some(&self.observe),
            Level::Operator => Some(&self.operator),
            Level::Observer => Some(&self.observer),
            Level::Control => None,
        }
    }

    /// These bounds with the server-wide facts unified (design S20): `stall_ms` and
    /// `frame_completion_ms` are widest, `tick_ms` is the larger observe/operator value, and
    /// `max_rows`/`max_block_bytes` are their smaller values. The shared block limit is also clamped
    /// to both levels' Query and Live connection pools. [`Server::new`] applies it.
    #[must_use]
    pub fn unified(&self) -> ServerLimits {
        let first = self.observe.stall_ms.max(self.operator.stall_ms);
        let stall_ms = first.max(self.observer.stall_ms);
        let first = self.observe.frame_completion_ms;
        let first = first.max(self.operator.frame_completion_ms);
        let frame_completion_ms = first.max(self.observer.frame_completion_ms);
        let tick_ms = self.observe.tick_ms.max(self.operator.tick_ms);
        let max_rows = self.observe.max_rows.min(self.operator.max_rows);
        let max_block_bytes = self
            .observe
            .max_block_bytes
            .min(self.operator.max_block_bytes)
            .min(self.observe.query_queue_bytes)
            .min(self.observe.client_queue_bytes)
            .min(self.operator.query_queue_bytes)
            .min(self.operator.client_queue_bytes);
        let mut out = *self;
        for limits in [&mut out.observe, &mut out.operator, &mut out.observer] {
            limits.stall_ms = stall_ms;
            limits.frame_completion_ms = frame_completion_ms;
        }
        for limits in [&mut out.observe, &mut out.operator] {
            limits.max_block_bytes = max_block_bytes;
        }
        out.observer.max_block_bytes = self
            .observer
            .max_block_bytes
            .min(self.observer.query_queue_bytes)
            .min(self.observer.client_queue_bytes);
        for limits in [&mut out.observe, &mut out.operator] {
            limits.tick_ms = tick_ms;
            limits.max_rows = max_rows;
        }
        out
    }

    /// The unified row and byte bounds shared by every live selection stream (design S20).
    #[must_use]
    pub fn stream_limits(&self) -> StreamLimits {
        StreamLimits {
            bounds: BlockBounds {
                max_rows: self.observe.max_rows,
                max_block_bytes: self.observe.max_block_bytes,
            },
        }
    }

    /// The caps of the one hub: every field is the widest value of the three levels (the
    /// observer's smaller control queue is enforced by the server, see the module docs).
    #[must_use]
    pub fn queue_limits(&self) -> QueueLimits {
        let first = widest(self.observe.queue_limits(), self.operator.queue_limits());
        widest(first, self.observer.queue_limits())
    }
}

impl Default for ServerLimits {
    fn default() -> ServerLimits {
        ServerLimits::new()
    }
}

fn widest(a: QueueLimits, b: QueueLimits) -> QueueLimits {
    QueueLimits {
        client_queue_bytes: a.client_queue_bytes.max(b.client_queue_bytes),
        uid_live_bytes: a.uid_live_bytes.max(b.uid_live_bytes),
        global_live_bytes: a.global_live_bytes.max(b.global_live_bytes),
        query_queue_bytes: a.query_queue_bytes.max(b.query_queue_bytes),
        uid_query_bytes: a.uid_query_bytes.max(b.uid_query_bytes),
        global_query_bytes: a.global_query_bytes.max(b.global_query_bytes),
        control_queue_bytes: a.control_queue_bytes.max(b.control_queue_bytes),
        stall_ns: a.stall_ns.max(b.stall_ns),
        frame_completion_ns: a.frame_completion_ns.max(b.frame_completion_ns),
    }
}

/// The answer of [`Server::accept`].
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Accept {
    /// The connection is registered under this id.
    Accepted(ConnId),
    /// Refused: the driver closes the socket at once, no byte written, nothing counted.
    Refused,
}

impl Accept {
    /// The id of an accepted connection.
    #[must_use]
    pub fn id(self) -> Option<ConnId> {
        match self {
            Accept::Accepted(id) => Some(id),
            Accept::Refused => None,
        }
    }
}

/// The connection counters: per level and per (level, uid); an entry at 0 does not exist.
#[derive(Debug, Default)]
struct Counts {
    levels: HashMap<Level, u32>,
    uids: HashMap<(Level, u32), u32>,
}

fn decrement<K: Eq + Hash>(map: &mut HashMap<K, u32>, key: K) {
    let left = match map.get_mut(&key) {
        Some(count) => {
            *count = count.saturating_sub(1);
            *count
        }
        None => return,
    };
    if left == 0 {
        map.remove(&key);
    }
}

impl Counts {
    fn level(&self, level: Level) -> u32 {
        self.levels.get(&level).copied().unwrap_or(0)
    }

    fn uid(&self, level: Level, uid: u32) -> u32 {
        self.uids.get(&(level, uid)).copied().unwrap_or(0)
    }

    fn add(&mut self, level: Level, uid: u32) {
        let by_level = self.levels.entry(level).or_insert(0);
        *by_level = by_level.saturating_add(1);
        let by_uid = self.uids.entry((level, uid)).or_insert(0);
        *by_uid = by_uid.saturating_add(1);
    }

    fn release(&mut self, level: Level, uid: u32) {
        decrement(&mut self.levels, level);
        decrement(&mut self.uids, (level, uid));
    }
}

/// The backend the connections see: the injected one, with `clients` filled from the
/// connections that are not closed. The connection being driven is not in `others` (the server
/// took it out of the map for the call, see `Server::drive`); it is open and counts as one of
/// level `mine`.
struct StatusView<'a, B> {
    backend: &'a B,
    others: &'a BTreeMap<ConnId, Conn>,
    mine: Level,
}

impl<B> StatusView<'_, B> {
    /// The connections of `level` that are not closed.
    fn open(&self, level: Level) -> u32 {
        let others = self
            .others
            .values()
            .filter(|conn| conn.level() == level && !conn.is_closed())
            .count();
        let own = usize::from(self.mine == level);
        u32::try_from(others.saturating_add(own)).unwrap_or(u32::MAX)
    }
}

impl<B: Backend> Backend for StatusView<'_, B> {
    fn identity(&self) -> Identity {
        self.backend.identity()
    }

    fn status(&self) -> StatusInfo {
        let mut status = self.backend.status();
        let mut clients = Vec::new();
        for level in SERVED {
            clients.push((level, self.open(level)));
        }
        status.clients = clients;
        status
    }

    fn current(&self, selection: &Selection, max_rows: u32) -> Result<TickData, BackendError> {
        self.backend.current(selection, max_rows)
    }
}

/// The serving core: one hub, the connections, the counters and the backend (module docs).
pub struct Server<B: Backend> {
    limits: ServerLimits,
    hub: SendHub,
    conns: BTreeMap<ConnId, Conn>,
    counts: Counts,
    streams: SelectionStreams,
    backend: B,
}

impl<B: Backend> Server<B> {
    /// A server with no connection.
    #[must_use]
    pub fn new(limits: ServerLimits, backend: B) -> Server<B> {
        let limits = limits.unified();
        let hub = SendHub::new(limits.queue_limits());
        let streams = SelectionStreams::new(limits.stream_limits());
        Server {
            limits,
            hub,
            conns: BTreeMap::new(),
            counts: Counts::default(),
            streams,
            backend,
        }
    }

    /// A connection of `level` arrived at `now_ns`. `peer` is the credentials the driver read
    /// from the socket (`None`: unreadable, refused). The server has no stopping flag: after
    /// [`Server::shutdown`] the driver stops calling `accept`.
    pub fn accept(&mut self, level: Level, peer: Option<Peer>, now_ns: u64) -> Accept {
        let Some(peer) = peer else {
            return Accept::Refused;
        };
        let Some(limits) = self.limits.get(level).copied() else {
            return Accept::Refused;
        };
        if self.counts.level(level) >= limits.max_connections {
            return Accept::Refused;
        }
        if self.counts.uid(level, peer.uid) >= limits.max_connections_per_uid {
            return Accept::Refused;
        }
        let id = self.hub.add(peer.uid, now_ns);
        let conn = Conn::new(id, level, peer, &limits, now_ns);
        self.counts.add(level, peer.uid);
        self.conns.insert(id, conn);
        Accept::Accepted(id)
    }

    /// Bytes arrived on `id` at `now_ns` (an unknown id is ignored).
    pub fn on_bytes(&mut self, id: ConnId, bytes: &[u8], now_ns: u64) {
        self.drive(id, |c, hub, back, streams| {
            c.on_bytes(bytes, now_ns, hub, back, streams);
        });
    }

    /// The peer of `id` ended its stream.
    pub fn on_eof(&mut self, id: ConnId, now_ns: u64) {
        self.drive(id, |c, hub, _back, streams| c.on_eof(now_ns, hub, streams));
    }

    /// The clock reached `now_ns` (R20): every connection's timers in `ConnId` order, then the
    /// hub's `slow-client` closes, then a sweep of the closed connections that hold no final
    /// bytes. Returns the ids the sweep finished, ascending.
    pub fn poll(&mut self, now_ns: u64) -> Vec<ConnId> {
        for conn in self.conns.values_mut() {
            conn.poll(now_ns, &mut self.hub);
        }
        for (id, _close) in self.hub.poll(now_ns) {
            if let Some(conn) = self.conns.get_mut(&id) {
                conn.close_for(Code::SlowClient, &mut self.hub);
            }
        }
        self.sweep()
    }

    /// The unwritten rest of the head of `id` as `[prefix part, tail part]`; `None` when the
    /// queue is empty, the connection is closed or unknown. The first call pins the head.
    pub fn output(&mut self, id: ConnId, now_ns: u64) -> Option<[&[u8]; 2]> {
        if self.is_closed(id) {
            return None;
        }
        self.hub.head(id, now_ns)
    }

    /// The driver wrote `n` bytes of the head [`Server::output`] offered.
    pub fn wrote(&mut self, id: ConnId, n: usize, now_ns: u64) {
        if let Some(conn) = self.conns.get_mut(&id) {
            conn.on_written(n, now_ns, &mut self.hub);
        }
    }

    /// The final bytes of a closed connection (module docs): taken once.
    pub fn take_final(&mut self, id: ConnId) -> Option<Vec<u8>> {
        self.conns.get_mut(&id).and_then(Conn::take_final)
    }

    /// Whether `id` is finished: closed, or no longer known.
    #[must_use]
    pub fn is_closed(&self, id: ConnId) -> bool {
        match self.conns.get(&id) {
            Some(conn) => conn.is_closed(),
            None => true,
        }
    }

    /// Release `id`: counters, uid entry, hub entry, the connection. Idempotent (module docs).
    /// Until then a closed connection holds its slot toward the caps.
    pub fn finish(&mut self, id: ConnId) {
        let Some(conn) = self.conns.remove(&id) else {
            return;
        };
        self.counts.release(conn.level(), conn.peer().uid);
        self.streams.release_conn(id);
        self.hub.remove(id);
    }

    /// Close every connection with `shutting-down` (the server is stopping). Returns the ids
    /// of the connections that hold no final bytes and were finished, ascending; the driver
    /// takes the final bytes of the others and finishes them.
    pub fn shutdown(&mut self) -> Vec<ConnId> {
        for conn in self.conns.values_mut() {
            conn.close_for(Code::ShuttingDown, &mut self.hub);
        }
        self.sweep()
    }

    /// The connection of `id`, if known.
    #[must_use]
    pub fn conn(&self, id: ConnId) -> Option<&Conn> {
        self.conns.get(&id)
    }

    /// Every known connection id, ascending.
    #[must_use]
    pub fn ids(&self) -> Vec<ConnId> {
        self.conns.keys().copied().collect()
    }

    /// The number of known connections.
    #[must_use]
    pub fn conn_count(&self) -> usize {
        self.conns.len()
    }

    /// The connections counted at `level`.
    #[must_use]
    pub fn count(&self, level: Level) -> u32 {
        self.counts.level(level)
    }

    /// The connections counted at `level` for peer `uid`.
    #[must_use]
    pub fn uid_count(&self, level: Level, uid: u32) -> u32 {
        self.counts.uid(level, uid)
    }

    /// The number of (level, uid) counter entries (0 when no connection is counted).
    #[must_use]
    pub fn uid_entries(&self) -> usize {
        self.counts.uids.len()
    }

    /// The hub, read-only (pools, queue sizes).
    #[must_use]
    pub fn hub(&self) -> &SendHub {
        &self.hub
    }

    /// The shared selection streams, read-only.
    #[must_use]
    pub fn streams(&self) -> &SelectionStreams {
        &self.streams
    }

    /// The bounds the server runs with: the ones given to [`Server::new`], unified
    /// ([`ServerLimits::unified`]).
    #[must_use]
    pub fn limits(&self) -> &ServerLimits {
        &self.limits
    }

    /// The backend.
    #[must_use]
    pub fn backend(&self) -> &B {
        &self.backend
    }

    /// The backend, mutable (tests and the bring-up change what it answers).
    pub fn backend_mut(&mut self) -> &mut B {
        &mut self.backend
    }

    /// Run `act` on connection `id` with the hub and the status-filling backend, then enforce
    /// the control cap. The ONE path through which a connection is handed the hub: every push
    /// path of the server goes through it (module docs), so none can skip `enforce_control`.
    /// The connection is out of the map during `act`, which lets the backend view read the
    /// others; an unknown id does nothing.
    fn drive<F>(&mut self, id: ConnId, act: F)
    where
        F: FnOnce(&mut Conn, &mut SendHub, &dyn Backend, &mut SelectionStreams),
    {
        let Some(mut conn) = self.conns.remove(&id) else {
            return;
        };
        let view = StatusView {
            backend: &self.backend,
            others: &self.conns,
            mine: conn.level(),
        };
        let backend: &dyn Backend = &view;
        act(&mut conn, &mut self.hub, backend, &mut self.streams);
        self.conns.insert(id, conn);
        self.enforce_control(id);
    }

    /// Close `id` with `slow-client` when its queued control bytes exceed its level's cap.
    fn enforce_control(&mut self, id: ConnId) {
        let Some(conn) = self.conns.get_mut(&id) else {
            return;
        };
        if conn.is_closed() {
            return;
        }
        let Some(limits) = self.limits.get(conn.level()) else {
            return;
        };
        let cap = usize::try_from(limits.control_queue_bytes).unwrap_or(usize::MAX);
        if self.hub.queued(id).control > cap {
            conn.close_for(Code::SlowClient, &mut self.hub);
        }
    }

    /// Finish every closed connection that holds no final bytes; the ids, ascending.
    fn sweep(&mut self) -> Vec<ConnId> {
        let mut done = Vec::new();
        for (id, conn) in &self.conns {
            if conn.is_closed() && !conn.has_final() {
                done.push(*id);
            }
        }
        for id in &done {
            self.finish(*id);
        }
        done
    }
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;
    use std::sync::Arc;

    use hostobs_proto::frame::FrameType;
    use hostobs_proto::hub::{ConnId, LiveOutcome, Pools, QueryOutcome, TickForms};

    use super::{Accept, Server, ServerLimits, SERVED};
    use crate::backend::{FixedBackend, TickData, TickInfo, TierInfo};
    use crate::codes::Code;
    use crate::conn::{Peer, State, SERVER_MINOR};
    use crate::encode::{control_frame, server_prefix};
    use crate::limits::{Level, Limits};
    use crate::registry::{Delivery, SelectionCaps, SubKey};
    use crate::selection::{Sel, Selection};
    use crate::tick::BlockBounds;

    const MS: u64 = 1_000_000;
    const HELLO_ARGS: &str = r#"{"client":"t","proto":{"major":1,"minor":1}}"#;

    fn req(corr: u32, verb: &str) -> Vec<u8> {
        let args = if verb == "hello" { HELLO_ARGS } else { "{}" };
        let json = format!("{{\"verb\":\"{verb}\",\"args\":{args}}}");
        control_frame(FrameType::Req, corr, json.as_bytes())
    }

    fn line(corr: u32, verb: &str, args: &str) -> Vec<u8> {
        format!("{{\"corr\":{corr},\"verb\":\"{verb}\",\"args\":{args}}}\n").into_bytes()
    }

    fn text(bytes: &[u8]) -> String {
        String::from_utf8_lossy(bytes).into_owned()
    }

    fn client_prefix(minor: u16) -> Vec<u8> {
        server_prefix(1, minor).to_vec()
    }

    /// `(type, corr, payload text)` of every frame of `bytes` (well-formed input only).
    fn frames(bytes: &[u8]) -> Vec<(u8, u32, String)> {
        let mut out = Vec::new();
        let mut rest = bytes;
        while rest.len() >= 12 {
            let len = u32::from_le_bytes([rest[0], rest[1], rest[2], rest[3]]);
            let len = usize::try_from(len).expect("len");
            let corr = u32::from_le_bytes([rest[8], rest[9], rest[10], rest[11]]);
            out.push((rest[4], corr, text(&rest[12..len])));
            rest = &rest[len..];
        }
        out
    }

    fn xorshift(state: &mut u64) -> u64 {
        let mut x = *state;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        *state = x;
        x
    }

    /// A block frame of `len` zero bytes: no section (`n_sec` 0), enough for the hub to pin.
    fn block(len: usize) -> Arc<[u8]> {
        Arc::from(vec![0u8; len])
    }

    /// A subscription tick whose two forms are the same bytes, at generation 1.
    struct Tick {
        bytes: Arc<[u8]>,
    }

    impl TickForms for Tick {
        fn generation(&self) -> u32 {
            1
        }

        fn bare(&self) -> Option<Arc<[u8]>> {
            Some(Arc::clone(&self.bytes))
        }

        fn dict_form(&self) -> Arc<[u8]> {
            Arc::clone(&self.bytes)
        }
    }

    fn pools(live: usize, query: usize) -> Pools {
        Pools {
            live,
            query,
            control: 0,
        }
    }

    /// The driver the server expects: it writes what `output` offers, takes the final bytes
    /// and finishes closed connections.
    struct Driver {
        server: Server<FixedBackend>,
        now: u64,
        out: BTreeMap<ConnId, Vec<u8>>,
        opened: Vec<ConnId>,
        uids: Vec<u32>,
    }

    impl Driver {
        fn new() -> Driver {
            Driver::with(ServerLimits::new())
        }

        fn with(limits: ServerLimits) -> Driver {
            Driver {
                server: Server::new(limits, FixedBackend::new()),
                now: 0,
                out: BTreeMap::new(),
                opened: Vec::new(),
                uids: Vec::new(),
            }
        }

        /// Remember a uid the run used: `assert_clean` checks its pools.
        fn note_uid(&mut self, uid: u32) {
            if !self.uids.contains(&uid) {
                self.uids.push(uid);
            }
        }

        fn try_open(&mut self, level: Level, uid: u32) -> Accept {
            let peer = Peer { uid, pid: 7 };
            let accept = self.server.accept(level, Some(peer), self.now);
            if let Accept::Accepted(id) = accept {
                self.opened.push(id);
                self.note_uid(uid);
            }
            accept
        }

        /// Push a Live tick of `len` bytes into the hub queue of `id`, as P5b-2 will.
        fn push_live(&mut self, id: ConnId, stream: u32, len: usize) -> LiveOutcome {
            let tick = Tick { bytes: block(len) };
            self.server.hub.push_live(id, stream, &tick, self.now)
        }

        /// Push a Query block of `len` bytes into the hub queue of `id`.
        fn push_query(&mut self, id: ConnId, stream: u32, len: usize) -> QueryOutcome {
            let bytes = block(len);
            self.server.hub.push_query(id, stream, bytes, self.now)
        }

        fn open(&mut self, level: Level, uid: u32) -> ConnId {
            match self.try_open(level, uid) {
                Accept::Accepted(id) => id,
                Accept::Refused => panic!("accept refused"),
            }
        }

        fn send(&mut self, id: ConnId, bytes: &[u8]) {
            self.server.on_bytes(id, bytes, self.now);
        }

        fn eof(&mut self, id: ConnId) {
            self.server.on_eof(id, self.now);
        }

        fn tick(&mut self, ns: u64) -> Vec<ConnId> {
            self.now += ns;
            self.server.poll(self.now)
        }

        fn state(&self, id: ConnId) -> Option<State> {
            self.server.conn(id).map(|conn| conn.state())
        }

        fn close_code(&self, id: ConnId) -> Option<Code> {
            self.server.conn(id).and_then(|conn| conn.close_code())
        }

        /// Write at most `max` bytes of the head queue of `id`.
        fn pump(&mut self, id: ConnId, max: usize) -> usize {
            let mut total = 0;
            while total < max {
                let chunk: Vec<u8> = match self.server.output(id, self.now) {
                    Some([a, b]) => [a, b].concat(),
                    None => break,
                };
                let take = chunk.len().min(max - total);
                if take == 0 {
                    break;
                }
                self.out
                    .entry(id)
                    .or_default()
                    .extend_from_slice(&chunk[..take]);
                self.server.wrote(id, take, self.now);
                total += take;
            }
            total
        }

        fn take_out(&mut self, id: ConnId) -> Vec<u8> {
            self.out.remove(&id).unwrap_or_default()
        }

        /// What a driver does with a closed connection: the final bytes, then `finish`.
        fn close_out(&mut self, id: ConnId) {
            if let Some(last) = self.server.take_final(id) {
                self.out.entry(id).or_default().extend_from_slice(&last);
            }
            self.server.finish(id);
        }

        fn settle_all(&mut self) {
            for id in self.server.ids() {
                if self.server.is_closed(id) {
                    self.close_out(id);
                }
            }
        }

        fn ready(&mut self, id: ConnId) {
            let mut input = client_prefix(1);
            input.extend_from_slice(&req(1, "hello"));
            self.send(id, &input);
            self.pump(id, usize::MAX);
            self.take_out(id);
            assert_eq!(self.state(id), Some(State::Ready));
        }
    }

    fn assert_clean(d: &Driver, what: &str) {
        assert_eq!(d.server.conn_count(), 0, "{what}: connections left");
        for level in Level::ALL {
            assert_eq!(d.server.count(level), 0, "{what}: level counter {level:?}");
        }
        assert_eq!(d.server.uid_entries(), 0, "{what}: uid entries left");
        for uid in &d.uids {
            let held = d.server.hub().uid_pools(*uid);
            assert_eq!(held, Pools::default(), "{what}: pools of uid {uid}");
        }
        assert_eq!(d.server.hub().global_pools(), Pools::default(), "{what}");
        for id in &d.opened {
            assert_eq!(d.server.hub().queued(*id), Pools::default(), "{what}");
        }
        assert_eq!(d.server.streams.live_selections(), 0, "{what}: streams");
        assert_eq!(d.server.streams.subscribers(), 0, "{what}: subscribers");
        for uid in &d.uids {
            assert_eq!(
                d.server.streams.uid_selections(*uid),
                0,
                "{what}: selections of uid {uid}"
            );
        }
        let stale = d.server.hub().poll(u64::MAX);
        assert!(stale.is_empty(), "{what}: hub entry left");
    }

    fn selection(inc: u64) -> Selection {
        Selection::new(vec![Sel::Inc(inc)], vec![String::from("m")]).expect("selection")
    }

    /// Register a real Conn and registry subscription before 2d serves the wire verb.
    fn register(d: &mut Driver, id: ConnId, corr: u32, inc: u64) {
        let Some(conn) = d.server.conn(id) else {
            return;
        };
        let level = conn.level();
        if !level.serves_blocks() {
            return;
        }
        let limits = d.server.limits.get(level).expect("served level");
        let caps = SelectionCaps {
            per_uid: limits.max_uid_selections,
            per_probe: limits.max_selections,
        };
        if caps.per_uid == 0 || caps.per_probe == 0 {
            return;
        }
        let now_ns = d.now;
        let selected = selection(inc);
        d.server.drive(id, |conn, hub, backend, streams| {
            conn.subscribe_for_server_test(corr, selected, now_ns, hub, backend, streams);
        });
        assert_eq!(d.server.conn(id).map(|conn| conn.subscriptions()), Some(1));
    }

    // ---- accept rows (WIRE.md 5.1 and 5.4 `refuse`) ----

    #[test]
    fn accept_row_level_cap_refuses_at_count_equal_cap() {
        let mut limits = ServerLimits::new();
        limits.observe.max_connections = 2;
        let mut d = Driver::with(limits);
        let first = d.open(Level::Observe, 1);
        assert_eq!(d.server.count(Level::Observe), 1);
        d.open(Level::Observe, 2);
        assert_eq!(d.try_open(Level::Observe, 3), Accept::Refused);
        assert_eq!(d.server.count(Level::Observe), 2);
        let other = d.try_open(Level::Operator, 3);
        assert!(matches!(other, Accept::Accepted(_)));
        d.server.finish(first);
        assert!(matches!(d.try_open(Level::Observe, 3), Accept::Accepted(_)));
        assert_eq!(d.server.count(Level::Observe), 2);
    }

    #[test]
    fn accept_row_uid_cap_is_per_level() {
        let mut limits = ServerLimits::new();
        limits.observe.max_connections_per_uid = 2;
        limits.operator.max_connections_per_uid = 1;
        let mut d = Driver::with(limits);
        let first = d.open(Level::Observe, 7);
        d.open(Level::Observe, 7);
        assert_eq!(d.try_open(Level::Observe, 7), Accept::Refused);
        assert!(matches!(d.try_open(Level::Observe, 8), Accept::Accepted(_)));
        let operator = d.try_open(Level::Operator, 7);
        assert!(matches!(operator, Accept::Accepted(_)));
        assert_eq!(d.try_open(Level::Operator, 7), Accept::Refused);
        assert_eq!(d.server.uid_count(Level::Observe, 7), 2);
        assert_eq!(d.server.uid_count(Level::Operator, 7), 1);
        d.server.finish(first);
        assert!(matches!(d.try_open(Level::Observe, 7), Accept::Accepted(_)));
    }

    #[test]
    fn accept_row_default_operator_caps_are_8_and_4() {
        let mut d = Driver::new();
        for _ in 0..4 {
            d.open(Level::Operator, 1);
        }
        assert_eq!(d.try_open(Level::Operator, 1), Accept::Refused);
        for _ in 0..4 {
            d.open(Level::Operator, 2);
        }
        assert_eq!(d.try_open(Level::Operator, 3), Accept::Refused);
        assert_eq!(d.server.count(Level::Operator), 8);
        assert_eq!(d.server.uid_entries(), 2);
    }

    #[test]
    fn accept_row_refusal_counts_nothing_and_consumes_no_id() {
        let mut limits = ServerLimits::new();
        limits.observe.max_connections = 1;
        let mut d = Driver::with(limits);
        let first = d.open(Level::Observe, 1);
        assert_eq!(d.try_open(Level::Observe, 2), Accept::Refused);
        assert_eq!(d.server.conn_count(), 1);
        assert_eq!(d.server.count(Level::Observe), 1);
        assert_eq!(d.server.uid_count(Level::Observe, 2), 0);
        assert_eq!(d.server.uid_entries(), 1);
        assert_eq!(d.server.hub().uid_pools(2), Pools::default());
        d.server.finish(first);
        let next = d.open(Level::Observe, 2);
        assert_eq!(next, ConnId(first.0 + 1));
    }

    #[test]
    fn accept_row_control_level_and_missing_peer_are_refused_uncounted() {
        let mut d = Driver::new();
        let peer = Peer { uid: 1, pid: 7 };
        let control = d.server.accept(Level::Control, Some(peer), 0);
        assert_eq!(control, Accept::Refused);
        assert_eq!(d.server.accept(Level::Observe, None, 0), Accept::Refused);
        assert_eq!(d.server.accept(Level::Observer, None, 0), Accept::Refused);
        assert_clean(&d, "refused");
        assert_eq!(d.open(Level::Observe, 1), ConnId(1));
    }

    #[test]
    fn accept_stores_the_peer_and_arms_the_hello_deadline() {
        let mut d = Driver::new();
        let id = d.open(Level::Observer, 42);
        let conn = d.server.conn(id).expect("conn");
        assert_eq!(conn.peer(), Peer { uid: 42, pid: 7 });
        assert_eq!(conn.state(), State::Undecided);
        assert_eq!(d.server.uid_count(Level::Observer, 42), 1);
        d.tick(5_001 * MS);
        assert!(d.server.is_closed(id));
    }

    // ---- R22: every close path releases everything ----

    type Path = fn(&mut Driver, Level);

    fn ready_with_reply(d: &mut Driver, level: Level) -> ConnId {
        let id = d.open(level, 1000);
        d.ready(id);
        d.send(id, &req(2, "status"));
        id
    }

    fn path_silent_eof(d: &mut Driver, level: Level) {
        let id = d.open(level, 1000);
        register(d, id, 2, 1);
        d.eof(id);
        assert!(d.server.is_closed(id));
    }

    fn path_unknown_mode(d: &mut Driver, level: Level) {
        let id = d.open(level, 1000);
        register(d, id, 2, 1);
        d.send(id, b"X");
        assert_eq!(d.close_code(id), Some(Code::UnknownMode));
    }

    fn path_major_mismatch(d: &mut Driver, level: Level) {
        let id = d.open(level, 1000);
        register(d, id, 2, 1);
        d.send(id, &server_prefix(2, 0));
        assert_eq!(d.close_code(id), Some(Code::UnsupportedVersion));
    }

    fn path_closing_with_final(d: &mut Driver, level: Level) {
        let id = d.open(level, 1000);
        register(d, id, 2, 1);
        let mut input = client_prefix(1);
        input.extend_from_slice(&req(0, "status"));
        d.send(id, &input);
        assert_eq!(d.close_code(id), Some(Code::BadFrame));
        assert!(d.server.conn(id).is_some_and(|conn| conn.has_final()));
    }

    fn path_draining_done(d: &mut Driver, level: Level) {
        let id = ready_with_reply(d, level);
        register(d, id, 4, 1);
        d.eof(id);
        assert_eq!(d.state(id), Some(State::Draining));
        d.pump(id, usize::MAX);
        assert!(d.server.is_closed(id));
    }

    fn path_driver_finish_mid_stream(d: &mut Driver, level: Level) {
        let id = ready_with_reply(d, level);
        register(d, id, 4, 1);
        assert!(!d.server.is_closed(id));
        d.server.finish(id);
    }

    fn path_shutdown(d: &mut Driver, level: Level) {
        let id = ready_with_reply(d, level);
        register(d, id, 4, 1);
        d.server.shutdown();
    }

    fn path_slow_client_from_the_hub(d: &mut Driver, level: Level) {
        let id = ready_with_reply(d, level);
        register(d, id, 4, 1);
        d.tick(5_001 * MS);
        assert!(d.server.is_closed(id));
    }

    fn path_idle(d: &mut Driver, level: Level) {
        let id = d.open(level, 1000);
        d.ready(id);
        d.tick(300_001 * MS);
        assert!(d.server.is_closed(id));
    }

    fn path_frame_too_large(d: &mut Driver, level: Level) {
        let id = d.open(level, 1000);
        register(d, id, 2, 1);
        d.send(id, &client_prefix(1));
        d.send(id, &[0x40, 0x9C, 0, 0, 1, 0, 0, 0, 5, 0, 0, 0]);
        assert_eq!(d.close_code(id), Some(Code::FrameTooLarge));
    }

    fn path_request_deadline(d: &mut Driver, level: Level) {
        let id = d.open(level, 1000);
        register(d, id, 2, 1);
        d.send(id, &client_prefix(1));
        d.send(id, &[0x0C]);
        let deadline_ms = d
            .server
            .limits()
            .get(level)
            .expect("served level")
            .request_deadline_ms;
        d.tick(u64::from(deadline_ms).saturating_mul(MS).saturating_add(1));
        assert_eq!(d.close_code(id), Some(Code::Deadline));
    }

    fn path_hello_timer(d: &mut Driver, level: Level) {
        let id = d.open(level, 1000);
        register(d, id, 2, 1);
        d.send(id, &client_prefix(1));
        let deadline_ms = d
            .server
            .limits()
            .get(level)
            .expect("served level")
            .hello_deadline_ms;
        d.tick(u64::from(deadline_ms).saturating_mul(MS).saturating_add(1));
        assert!(d.server.is_closed(id));
    }

    #[test]
    fn r22_every_close_path_returns_counters_pools_and_hub_to_zero() {
        let paths: [(&str, Path); 12] = [
            ("silent eof", path_silent_eof),
            ("unknown mode", path_unknown_mode),
            ("major mismatch", path_major_mismatch),
            ("closing with final", path_closing_with_final),
            ("draining done", path_draining_done),
            ("driver finish mid-stream", path_driver_finish_mid_stream),
            ("shutdown", path_shutdown),
            ("slow client from the hub", path_slow_client_from_the_hub),
            ("idle", path_idle),
            ("frame too large", path_frame_too_large),
            ("request deadline", path_request_deadline),
            ("hello timer", path_hello_timer),
        ];
        let mut limits = ServerLimits::new();
        limits.observe.hello_deadline_ms = 10_000;
        limits.operator.hello_deadline_ms = 10_000;
        limits.observer.hello_deadline_ms = 10_000;
        for level in SERVED {
            for &(name, path) in &paths {
                let mut d = Driver::with(limits);
                path(&mut d, level);
                d.settle_all();
                assert_clean(&d, name);
            }
        }
    }

    #[test]
    fn r22_finish_is_idempotent_and_leaves_other_connections_counted() {
        let mut d = Driver::new();
        let a = d.open(Level::Observe, 5);
        let b = d.open(Level::Observe, 5);
        d.server.finish(a);
        d.server.finish(a);
        d.server.finish(ConnId(99));
        assert_eq!(d.server.count(Level::Observe), 1);
        assert_eq!(d.server.uid_count(Level::Observe, 5), 1);
        assert_eq!(d.server.uid_entries(), 1);
        assert_eq!(d.server.conn_count(), 1);
        assert!(d.server.is_closed(a));
        assert!(!d.server.is_closed(b));
        d.server.finish(b);
        d.server.finish(b);
        assert_clean(&d, "after both");
    }

    #[test]
    fn r22_a_closed_connection_with_final_bytes_stays_until_taken_then_finished() {
        let mut d = Driver::new();
        let id = d.open(Level::Observe, 1);
        d.send(id, b"X");
        assert!(d.tick(MS).is_empty());
        assert_eq!(d.server.count(Level::Observe), 1);
        let last = d.server.take_final(id).expect("final");
        assert!(text(&last).contains("unknown-mode"));
        assert_eq!(d.server.take_final(id), None);
        assert_eq!(d.tick(MS), vec![id]);
        assert_clean(&d, "swept after take");
    }

    #[test]
    fn r22_a_closed_connection_offers_no_output_and_ignores_wrote() {
        let mut d = Driver::new();
        let id = ready_with_reply(&mut d, Level::Observe);
        assert!(d.server.output(id, 0).is_some());
        let before = d.server.conn(id).map(|conn| conn.bytes_written());
        d.server.shutdown();
        assert!(d.server.output(id, 0).is_none());
        d.server.wrote(id, 5, 0);
        let after = d.server.conn(id).map(|conn| conn.bytes_written());
        assert!(before.is_some());
        assert_eq!(before, after);
        d.settle_all();
        assert_clean(&d, "after shutdown");
    }

    // ---- R22 with real Live and Query frames in the hub ----

    const LIVE_LEN: usize = 200;
    const QUERY_LEN: usize = 300;

    /// A ready connection of uid 1000 whose hub queue holds a Live tick, a Query block, a second
    /// Live tick and a status reply. The head is pinned (`query_first` puts the Query block
    /// there) and `written` bytes of it are reported written (0: pinned, not partial).
    fn loaded(d: &mut Driver, level: Level, query_first: bool, written: usize) -> ConnId {
        let id = d.open(level, 1000);
        d.ready(id);
        if query_first {
            assert_eq!(d.push_query(id, 2, QUERY_LEN), QueryOutcome::Queued);
            assert_eq!(d.push_live(id, 1, LIVE_LEN), LiveOutcome::Queued);
        } else {
            assert_eq!(d.push_live(id, 1, LIVE_LEN), LiveOutcome::Queued);
            assert_eq!(d.push_query(id, 2, QUERY_LEN), QueryOutcome::Queued);
        }
        assert_eq!(d.push_live(id, 3, LIVE_LEN), LiveOutcome::Queued);
        d.send(id, &req(2, "status"));
        let held = pools(2 * LIVE_LEN, QUERY_LEN);
        let queued = d.server.hub().queued(id);
        assert_eq!((queued.live, queued.query), (held.live, held.query));
        assert!(queued.control > 0);
        assert_eq!(d.server.hub().uid_pools(1000), held);
        assert_eq!(d.server.hub().global_pools(), held);
        let head = if query_first { QUERY_LEN } else { LIVE_LEN };
        let offered = d.server.output(id, d.now).map(|[a, b]| a.len() + b.len());
        assert_eq!(offered, Some(head));
        if written > 0 {
            d.server.wrote(id, written, d.now);
            assert!(d.server.hub().head_partial(id));
        }
        id
    }

    type LoadedPath = fn(&mut Driver, ConnId);

    fn loaded_finish(d: &mut Driver, id: ConnId) {
        d.server.finish(id);
    }

    fn loaded_shutdown(d: &mut Driver, _id: ConnId) {
        d.server.shutdown();
    }

    /// The hub closed `id` for a slow client. A conn whose head was partly written has no final
    /// bytes (the head is not abandoned mid-frame), so the same `poll` already swept it and its
    /// code is gone; a conn with final bytes stays, with the code.
    fn assert_slow_client_closed(d: &Driver, id: ConnId) {
        assert!(d.server.is_closed(id));
        if let Some(code) = d.close_code(id) {
            assert_eq!(code, Code::SlowClient);
        }
    }

    fn loaded_slow_client(d: &mut Driver, id: ConnId) {
        d.tick(5_001 * MS);
        assert_slow_client_closed(d, id);
    }

    fn loaded_eof_then_slow_client(d: &mut Driver, id: ConnId) {
        d.eof(id);
        assert_eq!(d.state(id), Some(State::Draining));
        d.tick(5_001 * MS);
        assert_slow_client_closed(d, id);
    }

    fn loaded_bad_frame(d: &mut Driver, id: ConnId) {
        d.send(id, &req(0, "status"));
        assert_eq!(d.close_code(id), Some(Code::BadFrame));
    }

    fn loaded_eof_inside_a_frame(d: &mut Driver, id: ConnId) {
        d.send(id, &req(3, "status")[..5]);
        d.eof(id);
        assert!(d.server.is_closed(id));
        assert_eq!(d.close_code(id), None);
    }

    #[test]
    fn r22_pools_return_to_zero_with_real_frames_queued_on_every_close_path() {
        let paths: [(&str, LoadedPath); 6] = [
            ("driver finish mid-stream", loaded_finish),
            ("shutdown", loaded_shutdown),
            ("slow client from the hub", loaded_slow_client),
            ("eof then slow client", loaded_eof_then_slow_client),
            ("bad frame", loaded_bad_frame),
            ("eof inside a frame", loaded_eof_inside_a_frame),
        ];
        for level in SERVED {
            for query_first in [false, true] {
                for written in [0usize, 10] {
                    for &(name, path) in &paths {
                        let mut d = Driver::new();
                        let id = loaded(&mut d, level, query_first, written);
                        path(&mut d, id);
                        d.settle_all();
                        assert_clean(&d, name);
                    }
                }
            }
        }
    }

    #[test]
    fn r22_a_closed_connection_keeps_only_its_pinned_head_in_the_pools_until_finish() {
        for written in [0usize, 10] {
            let mut d = Driver::new();
            let id = loaded(&mut d, Level::Observe, false, written);
            loaded_bad_frame(&mut d, id);
            let head = pools(LIVE_LEN, 0);
            assert_eq!(d.server.hub().queued(id), head);
            assert_eq!(d.server.hub().uid_pools(1000), head);
            assert_eq!(d.server.hub().global_pools(), head);
            let has_final = d.server.conn(id).is_some_and(|conn| conn.has_final());
            assert_eq!(has_final, written == 0);
            d.settle_all();
            assert_clean(&d, "bad frame with a pinned head");
        }
    }

    #[test]
    fn the_hub_uid_is_the_peers_stored_uid() {
        let mut d = Driver::new();
        let a = d.open(Level::Observe, 1000);
        let b = d.open(Level::Observe, 1001);
        assert_eq!(d.push_live(a, 1, 200), LiveOutcome::Queued);
        assert_eq!(d.push_query(a, 2, 300), QueryOutcome::Queued);
        assert_eq!(d.push_live(b, 1, 500), LiveOutcome::Queued);
        assert_eq!(d.push_query(b, 2, 700), QueryOutcome::Queued);
        assert_eq!(d.server.hub().uid_pools(1000), pools(200, 300));
        assert_eq!(d.server.hub().uid_pools(1001), pools(500, 700));
        assert_eq!(d.server.hub().uid_pools(0), Pools::default());
        assert_eq!(d.server.hub().global_pools(), pools(700, 1_000));
        assert_eq!(d.server.hub().queued(a), pools(200, 300));
        d.server.finish(a);
        assert_eq!(d.server.hub().uid_pools(1000), Pools::default());
        assert_eq!(d.server.hub().uid_pools(1001), pools(500, 700));
        assert_eq!(d.server.hub().global_pools(), pools(500, 700));
        d.server.finish(b);
        assert_clean(&d, "both finished");
    }

    // ---- R20: timer order ----

    #[test]
    fn r20_hello_deadline_beats_the_stall_on_the_unread_prefix() {
        let mut d = Driver::new();
        let id = d.open(Level::Observe, 1000);
        d.send(id, &client_prefix(1));
        d.tick(5_001 * MS);
        assert_eq!(d.close_code(id), Some(Code::Deadline));
        let last = d.server.take_final(id).expect("final");
        assert!(last.starts_with(&server_prefix(1, SERVER_MINOR.min(1))));
        assert!(text(&last).contains("deadline"));
    }

    #[test]
    fn r20_the_stall_wins_when_it_is_the_only_due_timer() {
        let mut limits = ServerLimits::new();
        limits.observe.hello_deadline_ms = 10_000;
        let mut d = Driver::with(limits);
        let id = d.open(Level::Observe, 1000);
        d.send(id, &client_prefix(1));
        d.tick(5_000 * MS);
        assert_eq!(d.close_code(id), None);
        d.tick(MS);
        assert_eq!(d.close_code(id), Some(Code::SlowClient));
        let last = d.server.take_final(id).expect("final");
        assert!(last.starts_with(&server_prefix(1, SERVER_MINOR.min(1))));
        assert!(text(&last).contains("slow-client"));
    }

    #[test]
    fn r20_connections_are_polled_and_swept_in_connection_id_order() {
        let mut d = Driver::new();
        let a = d.open(Level::Observe, 1);
        let b = d.open(Level::Operator, 1);
        let c = d.open(Level::Observer, 1);
        d.server.finish(b);
        let e = d.open(Level::Observe, 2);
        assert!(a < c && c < e);
        assert_eq!(d.tick(5_001 * MS), vec![a, c, e]);
        assert_clean(&d, "ordered sweep");
    }

    /// A ready connection with a status reply queued and pinned at t=0 (nothing written): its
    /// stall is due 5 s later.
    fn ready_with_pinned_reply(d: &mut Driver) -> ConnId {
        let id = ready_with_reply(d, Level::Observe);
        let offered = d.server.output(id, d.now).map(|[a, b]| a.len() + b.len());
        assert!(offered.is_some_and(|len| len > 0));
        id
    }

    #[test]
    fn r20_the_request_age_wins_over_a_stall_due_in_the_same_poll() {
        let mut d = Driver::new();
        let id = ready_with_pinned_reply(&mut d);
        d.send(id, &req(3, "status")[..5]);
        d.tick(5_001 * MS);
        assert!(!d.server.hub().poll(5_001 * MS).is_empty());
        assert_eq!(d.close_code(id), Some(Code::Deadline));
        let last = d.server.take_final(id).expect("final");
        assert!(text(&last).contains("deadline"));
        assert!(!text(&last).contains("slow-client"));
    }

    #[test]
    fn r20_the_stall_wins_when_the_request_age_is_not_yet_due() {
        let mut d = Driver::new();
        let id = ready_with_pinned_reply(&mut d);
        d.now = 1_000 * MS;
        d.send(id, &req(3, "status")[..5]);
        d.tick(4_001 * MS);
        assert_eq!(d.close_code(id), Some(Code::SlowClient));
        let last = d.server.take_final(id).expect("final");
        assert!(text(&last).contains("slow-client"));
    }

    // ---- shutdown ----

    #[test]
    fn shutdown_closes_every_connection_and_hands_out_the_final_bytes() {
        let mut d = Driver::new();
        let undecided = d.open(Level::Observe, 1);
        let awaiting = d.open(Level::Operator, 1);
        d.send(awaiting, &client_prefix(1));
        let partial = d.open(Level::Observe, 2);
        d.send(partial, &client_prefix(1));
        assert_eq!(d.pump(partial, 5), 5);
        let swept = d.server.shutdown();
        assert_eq!(swept, vec![undecided, partial]);
        assert!(d.server.is_closed(awaiting));
        let last = d.server.take_final(awaiting).expect("final");
        assert!(last.starts_with(&server_prefix(1, SERVER_MINOR.min(1))));
        assert!(text(&last).contains("shutting-down"));
        assert_eq!(d.server.take_final(partial), None);
        d.settle_all();
        assert_clean(&d, "shutdown");
    }

    // ---- interleaving, status, control cap ----

    fn binary_script() -> Vec<u8> {
        let mut input = client_prefix(1);
        input.extend_from_slice(&req(1, "hello"));
        input.extend_from_slice(&req(2, "status"));
        input
    }

    fn json_script() -> Vec<u8> {
        let mut input = line(1, "hello", HELLO_ARGS);
        input.extend_from_slice(&line(2, "status", "{}"));
        input
    }

    #[test]
    fn two_connections_fed_byte_by_byte_answer_like_a_one_shot_feed() {
        let binary = binary_script();
        let json = json_script();
        let mut whole = Driver::new();
        let wa = whole.open(Level::Observe, 1);
        let wb = whole.open(Level::Observe, 2);
        whole.send(wa, &binary);
        whole.send(wb, &json);
        whole.pump(wa, usize::MAX);
        whole.pump(wb, usize::MAX);
        let mut dribble = Driver::new();
        let da = dribble.open(Level::Observe, 1);
        let db = dribble.open(Level::Observe, 2);
        for index in 0..binary.len().max(json.len()) {
            if let Some(byte) = binary.get(index) {
                dribble.send(da, &[*byte]);
                dribble.pump(da, usize::MAX);
            }
            if let Some(byte) = json.get(index) {
                dribble.send(db, &[*byte]);
                dribble.pump(db, usize::MAX);
            }
        }
        let expected_a = whole.take_out(wa);
        let expected_b = whole.take_out(wb);
        assert!(expected_a.len() > 200 && expected_b.len() > 200);
        assert_eq!(dribble.take_out(da), expected_a);
        assert_eq!(dribble.take_out(db), expected_b);
    }

    #[test]
    fn status_reports_the_connections_of_each_level() {
        let mut d = Driver::new();
        d.open(Level::Observe, 1);
        d.open(Level::Observe, 2);
        d.open(Level::Observer, 1);
        d.open(Level::Observer, 2);
        let last_observer = d.open(Level::Observer, 3);
        let asker = d.open(Level::Operator, 1);
        d.ready(asker);
        d.send(asker, &req(2, "status"));
        d.pump(asker, usize::MAX);
        let out = d.take_out(asker);
        let reply = frames(&out).pop().expect("reply").2;
        let want = r#""clients":{"observe":2,"operator":1,"observer":3}"#;
        assert!(reply.contains(want));
        d.server.finish(last_observer);
        d.send(asker, &req(3, "status"));
        d.pump(asker, usize::MAX);
        let out = d.take_out(asker);
        let reply = frames(&out).pop().expect("reply").2;
        let want = r#""clients":{"observe":2,"operator":1,"observer":2}"#;
        assert!(reply.contains(want));
    }

    fn pipeline_of_forbidden_requests() -> Vec<u8> {
        let mut input = client_prefix(1);
        input.extend_from_slice(&req(1, "hello"));
        for corr in 2..122 {
            input.extend_from_slice(&req(corr, "status"));
        }
        input
    }

    #[test]
    fn the_observer_control_cap_is_enforced_by_the_server() {
        let mut limits = ServerLimits::new();
        limits.observer.control_queue_bytes = 8 * 1024;
        let mut d = Driver::with(limits);
        let id = d.open(Level::Observer, 1);
        d.send(id, &pipeline_of_forbidden_requests());
        assert_eq!(d.close_code(id), Some(Code::SlowClient));
        d.settle_all();
        assert_clean(&d, "control cap");
    }

    #[test]
    fn the_default_observer_cap_admits_the_same_pipeline() {
        let mut d = Driver::new();
        let id = d.open(Level::Observer, 1);
        d.send(id, &pipeline_of_forbidden_requests());
        assert_eq!(d.close_code(id), None);
        assert_eq!(d.state(id), Some(State::Ready));
    }

    #[test]
    fn the_hub_caps_are_the_widest_of_the_levels() {
        let limits = ServerLimits::new();
        let hub = limits.queue_limits();
        assert_eq!(hub.control_queue_bytes, 2 * 1024 * 1024);
        assert_eq!(hub.client_queue_bytes, 4 * 1024 * 1024);
        assert_eq!(hub.uid_live_bytes, 8 * 1024 * 1024);
        assert_eq!(hub.global_live_bytes, 32 * 1024 * 1024);
        assert_eq!(hub.uid_query_bytes, 4 * 1024 * 1024);
        assert_eq!(hub.global_query_bytes, 16 * 1024 * 1024);
        assert_eq!(hub.query_queue_bytes, 4 * 1024 * 1024);
        assert_eq!(hub.stall_ns, 5_000 * MS);
        assert_eq!(hub.frame_completion_ns, 5_000 * MS);
        assert_eq!(limits.get(Level::Control), None);
    }

    #[test]
    fn the_hub_caps_take_every_field_from_its_widest_level() {
        let mut limits = ServerLimits::new();
        limits.observe.client_queue_bytes = 100_000;
        limits.operator.client_queue_bytes = 300_000;
        limits.observer.client_queue_bytes = 200_000;
        limits.observe.query_queue_bytes = 300_000;
        limits.operator.query_queue_bytes = 100_000;
        limits.observer.query_queue_bytes = 200_000;
        limits.observe.control_queue_bytes = 100_000;
        limits.operator.control_queue_bytes = 200_000;
        limits.observer.control_queue_bytes = 300_000;
        limits.observe.stall_ms = 8_000;
        limits.operator.stall_ms = 7_000;
        limits.observer.stall_ms = 9_000;
        limits.observe.frame_completion_ms = 6_000;
        limits.operator.frame_completion_ms = 8_000;
        limits.observer.frame_completion_ms = 7_000;
        let hub = limits.queue_limits();
        assert_eq!(hub.client_queue_bytes, 300_000);
        assert_eq!(hub.uid_live_bytes, 600_000);
        assert_eq!(hub.global_live_bytes, 2_400_000);
        assert_eq!(hub.query_queue_bytes, 300_000);
        assert_eq!(hub.uid_query_bytes, 300_000);
        assert_eq!(hub.global_query_bytes, 1_200_000);
        assert_eq!(hub.control_queue_bytes, 300_000);
        assert_eq!(hub.stall_ns, 9_000 * MS);
        assert_eq!(hub.frame_completion_ns, 8_000 * MS);
    }

    #[test]
    fn server_limits_block_invariant_holds_for_defaults_and_a_wrong_input() {
        let defaults = ServerLimits::new().unified();
        for level in [Level::Observe, Level::Operator] {
            let limits = defaults.get(level).expect("served");
            assert_eq!(limits.tick_ms, 1_000);
            assert_eq!(limits.max_rows, 4_096);
            assert_eq!(limits.max_block_bytes, 4 << 20);
            assert!(limits.max_block_bytes <= limits.query_queue_bytes);
            assert!(limits.max_block_bytes <= limits.client_queue_bytes);
        }
        assert_eq!(
            defaults.stream_limits().bounds,
            BlockBounds {
                max_rows: 4_096,
                max_block_bytes: 4 << 20,
            }
        );

        let mut wrong = ServerLimits::new();
        wrong.observe.tick_ms = 500;
        wrong.operator.tick_ms = 900;
        wrong.observe.max_rows = 100;
        wrong.operator.max_rows = 200;
        wrong.observe.max_block_bytes = 4_000;
        wrong.operator.max_block_bytes = 4_000;
        wrong.observe.query_queue_bytes = 1_000;
        wrong.observe.client_queue_bytes = 500;
        wrong.operator.query_queue_bytes = 2_000;
        wrong.operator.client_queue_bytes = 1_500;
        let unified = wrong.unified();
        for level in [Level::Observe, Level::Operator] {
            let limits = unified.get(level).expect("served");
            assert_eq!(limits.tick_ms, 900);
            assert_eq!(limits.max_rows, 100);
            assert!(limits.max_block_bytes <= limits.query_queue_bytes);
            assert!(limits.max_block_bytes <= limits.client_queue_bytes);
        }
        assert_eq!(unified.observe.max_block_bytes, 500);
        assert_eq!(unified.operator.max_block_bytes, 500);
        let server = Server::new(wrong, FixedBackend::new());
        assert_eq!(server.streams().live_selections(), 0);
        assert_eq!(server.limits(), &unified);
        assert_eq!(server.limits().stream_limits(), unified.stream_limits());
    }

    #[test]
    fn server_shared_stream_uses_both_levels_smallest_block_pool() {
        let mut limits = ServerLimits::new();
        limits.observe.max_block_bytes = 4_000;
        limits.operator.max_block_bytes = 4_000;
        limits.observe.query_queue_bytes = 4_000;
        limits.observe.client_queue_bytes = 4_000;
        limits.operator.query_queue_bytes = 2_000;
        limits.operator.client_queue_bytes = 500;

        let selection = selection(1);
        let incarnations: Vec<u64> = (1..=20).collect();
        let data = TickData::example(1, &incarnations, &["m"]);
        let encoded = crate::tick::query_block(
            data.clone(),
            BlockBounds {
                max_rows: 4_096,
                max_block_bytes: u32::MAX,
            },
        )
        .expect("fixture fits the large bound");
        assert!(encoded.len() > 500 && encoded.len() <= 1_500);
        let mut backend = FixedBackend::new();
        backend.current.insert(selection.clone(), Ok(data));
        let mut server = Server::new(limits, backend);
        let uid = 42;
        let Accept::Accepted(observe) =
            server.accept(Level::Observe, Some(Peer { uid, pid: 7 }), 0)
        else {
            panic!("observe accepted");
        };
        let Accept::Accepted(operator) =
            server.accept(Level::Operator, Some(Peer { uid, pid: 8 }), 0)
        else {
            panic!("operator accepted");
        };

        for level in [Level::Observe, Level::Operator] {
            assert_eq!(
                server.limits().get(level).expect("served").max_block_bytes,
                500
            );
        }
        assert_eq!(server.limits().stream_limits().bounds.max_block_bytes, 500);

        let observe_caps = {
            let limits = server.limits().get(Level::Observe).expect("observe");
            SelectionCaps {
                per_uid: limits.max_uid_selections,
                per_probe: limits.max_selections,
            }
        };
        let operator_caps = {
            let limits = server.limits().get(Level::Operator).expect("operator");
            SelectionCaps {
                per_uid: limits.max_uid_selections,
                per_probe: limits.max_selections,
            }
        };
        server
            .streams
            .subscribe(
                SubKey {
                    conn: observe,
                    corr: 2,
                },
                uid,
                1,
                &selection,
                observe_caps,
            )
            .expect("observe joins the stream");
        server
            .streams
            .subscribe(
                SubKey {
                    conn: operator,
                    corr: 3,
                },
                uid,
                1,
                &selection,
                operator_caps,
            )
            .expect("operator joins the same stream");

        let deliveries = server.streams.on_tick(&server.backend);
        assert_eq!(deliveries.len(), 1);
        match &deliveries[0] {
            Delivery::End { to, code, .. } => {
                assert_eq!(
                    to,
                    &[
                        SubKey {
                            conn: observe,
                            corr: 2,
                        },
                        SubKey {
                            conn: operator,
                            corr: 3,
                        },
                    ]
                );
                assert_eq!(*code, Code::Internal);
            }
            Delivery::Push { .. } => panic!("oversize shared tick was delivered"),
        }
        assert_eq!(server.streams.live_selections(), 0);
        assert_eq!(server.streams.subscribers(), 0);
        server.finish(observe);
        server.finish(operator);
    }

    #[test]
    fn server_new_unifies_the_two_timers_because_the_hub_has_one_value() {
        let mut limits = ServerLimits::new();
        limits.observe.stall_ms = 4_000;
        limits.operator.stall_ms = 6_000;
        limits.observer.stall_ms = 5_000;
        limits.observe.frame_completion_ms = 3_000;
        limits.operator.frame_completion_ms = 2_000;
        limits.observer.frame_completion_ms = 9_000;
        let unified = limits.unified();
        for level in SERVED {
            let each = unified.get(level).expect("served");
            assert_eq!(each.stall_ms, 6_000);
            assert_eq!(each.frame_completion_ms, 9_000);
            let mut given = *limits.get(level).expect("served");
            given.stall_ms = 6_000;
            given.frame_completion_ms = 9_000;
            assert_eq!(*each, given);
        }
        let mut d = Driver::with(limits);
        assert_eq!(d.server.limits(), &unified);
        let id = d.open(Level::Observe, 1);
        let mut input = client_prefix(1);
        input.extend_from_slice(&req(1, "hello"));
        d.send(id, &input);
        d.pump(id, usize::MAX);
        let hello = text(&d.take_out(id));
        assert!(hello.contains(r#""stall_ms":6000"#));
        assert!(hello.contains(r#""frame_completion_ms":9000"#));
        d.send(id, &req(2, "status"));
        d.tick(5_001 * MS);
        assert_eq!(d.close_code(id), None);
        d.tick(1_000 * MS);
        assert_eq!(d.close_code(id), Some(Code::SlowClient));
    }

    // ---- R28: the control cap boundary ----

    const BURST: u32 = 5;

    fn burst() -> Vec<u8> {
        let mut input = Vec::new();
        for corr in 2..2 + BURST {
            input.extend_from_slice(&req(corr, "status"));
        }
        input
    }

    fn set_control_cap(d: &mut Driver, level: Level, cap: u32) {
        let limits = &mut d.server.limits;
        match level {
            Level::Observe => limits.observe.control_queue_bytes = cap,
            Level::Operator => limits.operator.control_queue_bytes = cap,
            Level::Observer => limits.observer.control_queue_bytes = cap,
            Level::Control => {}
        }
    }

    /// A ready connection of `level` with a control cap of `cap` (set after the hello reply
    /// went out) that then receives the burst.
    fn burst_at_cap(level: Level, cap: usize) -> (Driver, ConnId) {
        let mut d = Driver::new();
        let id = d.open(level, 1);
        d.ready(id);
        set_control_cap(&mut d, level, u32::try_from(cap).expect("cap"));
        d.send(id, &burst());
        (d, id)
    }

    #[test]
    fn r28_the_control_cap_boundary_is_exact_at_every_level() {
        for level in SERVED {
            let (probe, probe_id) = burst_at_cap(level, 1 << 20);
            let total = probe.server.hub().queued(probe_id).control;
            let replies = usize::try_from(BURST).expect("burst");
            assert!(total >= 12 * replies, "{level:?}: {total}");
            assert_eq!(probe.close_code(probe_id), None);
            let (mut at, id) = burst_at_cap(level, total);
            assert_eq!(at.close_code(id), None, "{level:?}: at the cap");
            assert_eq!(at.state(id), Some(State::Ready));
            assert_eq!(at.server.hub().queued(id).control, total);
            at.send(id, &req(2 + BURST, "status"));
            let code = at.close_code(id);
            assert_eq!(code, Some(Code::SlowClient), "{level:?}: one reply over");
            at.settle_all();
            assert_clean(&at, "over the cap");
            let (mut under, id) = burst_at_cap(level, total - 1);
            let code = under.close_code(id);
            assert_eq!(code, Some(Code::SlowClient), "{level:?}: one byte under");
            under.settle_all();
            assert_clean(&under, "one byte under the cap");
        }
    }

    // ---- hello and status pass the backend through ----

    fn other_backend() -> FixedBackend {
        let mut backend = FixedBackend::new();
        backend.identity.server = String::from("other-probe-9");
        backend.identity.boot_id = String::from("boot-xyz");
        backend.identity.epoch = 42;
        backend.identity.features = vec![String::from("peak_reset")];
        backend.status.started_real_ns = 1_700_000_000_123;
        backend.status.uptime_ms = 61_000;
        backend.status.tick = TickInfo {
            seq: 9,
            last_mono_ns: 777,
            scan_ms: 3,
        };
        backend.status.tiers = vec![TierInfo {
            name: String::from("hot"),
            step_ms: 1_000,
            keep_s: 60,
            bytes: 10,
            cap_bytes: 20,
        }];
        backend
    }

    #[test]
    fn hello_and_status_carry_the_backends_identity_and_status() {
        let mut d = Driver::new();
        *d.server.backend_mut() = other_backend();
        let id = d.open(Level::Observe, 1);
        d.send(id, &binary_script());
        d.pump(id, usize::MAX);
        let out = d.take_out(id);
        let list = frames(&out[8..]);
        assert_eq!(list.len(), 2);
        let identity = r#""server":"other-probe-9","boot_id":"boot-xyz","epoch":42"#;
        let hello = &list[0].2;
        assert!(hello.contains(identity));
        assert!(hello.contains(r#""features":["peak_reset"]"#));
        let status = &list[1].2;
        assert!(status.contains(identity));
        let times = r#""started_real_ns":1700000000123,"uptime_ms":61000"#;
        assert!(status.contains(times));
        let tick = r#""tick":{"seq":9,"last_mono_ns":777,"scan_ms":3}"#;
        assert!(status.contains(tick));
        let name = r#""tiers":[{"name":"hot","step_ms":1000,"#;
        let rest = r#""keep_s":60,"bytes":10,"cap_bytes":20}]"#;
        assert!(status.contains(&format!("{name}{rest}")));
    }

    #[test]
    fn status_counts_open_connections_while_a_closed_one_holds_its_slot_until_finish() {
        let mut limits = ServerLimits::new();
        limits.observe.max_connections = 2;
        let mut d = Driver::with(limits);
        let closing = d.open(Level::Observe, 1);
        d.open(Level::Observe, 2);
        let closed_operator = d.open(Level::Operator, 5);
        let closed_observer = d.open(Level::Observer, 6);
        let asker = d.open(Level::Operator, 1);
        d.ready(asker);
        for id in [closing, closed_operator, closed_observer] {
            d.send(id, b"X");
            assert!(d.server.is_closed(id));
            assert!(d.server.conn(id).is_some_and(|conn| conn.has_final()));
        }
        d.send(asker, &req(2, "status"));
        d.pump(asker, usize::MAX);
        let out = d.take_out(asker);
        let reply = frames(&out).pop().expect("reply").2;
        let want = r#""clients":{"observe":1,"operator":1,"observer":0}"#;
        assert!(reply.contains(want));
        assert_eq!(d.server.count(Level::Observe), 2);
        assert_eq!(d.server.count(Level::Operator), 2);
        assert_eq!(d.server.count(Level::Observer), 1);
        assert_eq!(d.try_open(Level::Observe, 3), Accept::Refused);
        d.close_out(closing);
        assert_eq!(d.server.count(Level::Observe), 1);
        assert!(matches!(d.try_open(Level::Observe, 3), Accept::Accepted(_)));
    }

    #[test]
    fn accessors_report_what_the_server_holds() {
        assert_eq!(Accept::Accepted(ConnId(3)).id(), Some(ConnId(3)));
        assert_eq!(Accept::Refused.id(), None);
        let defaults = ServerLimits::default();
        assert_eq!(defaults, ServerLimits::new());
        assert_eq!(defaults.observe, Limits::for_level(Level::Observe));
        assert_eq!(defaults.operator, Limits::for_level(Level::Operator));
        assert_eq!(defaults.observer, Limits::for_level(Level::Observer));
        assert_eq!(defaults.get(Level::Operator), Some(&defaults.operator));
        let mut d = Driver::new();
        assert_eq!(d.server.limits(), &defaults);
        assert_eq!(d.server.backend().identity.epoch, 1);
        d.server.backend_mut().identity.epoch = 5;
        assert_eq!(d.server.backend().identity.epoch, 5);
        let a = d.open(Level::Observe, 1);
        let b = d.open(Level::Operator, 2);
        assert_eq!(d.server.ids(), vec![a, b]);
        assert_eq!(d.server.conn_count(), 2);
        assert_eq!(d.server.conn(b).map(|conn| conn.id()), Some(b));
        assert_eq!(d.server.conn(ConnId(99)).map(|conn| conn.id()), None);
        assert_eq!(d.server.hub().queued(a), Pools::default());
        assert_eq!(d.server.uid_entries(), 2);
        assert_eq!(d.server.uid_count(Level::Operator, 2), 1);
    }

    // ---- hostile runs ----

    fn payload(seed: &mut u64) -> Vec<u8> {
        match xorshift(seed) % 9 {
            0 => client_prefix(1),
            1 => req(1, "hello"),
            2 => req(2, "status"),
            3 => line(1, "hello", HELLO_ARGS),
            4 => line(5, "status", "{}"),
            5 => br#"{"corr":1,"ve"#.to_vec(),
            6 => server_prefix(2, 0).to_vec(),
            7 => req(0, "status"),
            _ => {
                let mut junk = Vec::new();
                for _ in 0..(xorshift(seed) % 20) {
                    junk.push(u8::try_from(xorshift(seed) % 256).unwrap_or(0));
                }
                junk
            }
        }
    }

    /// The counters recomputed from the live connections, and the caps.
    fn check_counters(d: &Driver, limits: &ServerLimits) {
        let mut live = 0usize;
        for level in SERVED {
            let cap = limits.get(level).expect("served");
            let mut tally = 0u32;
            let mut per_uid = [0u32; 4];
            for id in d.server.ids() {
                let Some(conn) = d.server.conn(id) else {
                    continue;
                };
                if conn.level() == level {
                    tally += 1;
                    let slot = usize::try_from(conn.peer().uid).expect("uid");
                    per_uid[slot] += 1;
                }
            }
            assert_eq!(d.server.count(level), tally);
            assert!(tally <= cap.max_connections);
            for uid in 0..4u32 {
                let held = per_uid[usize::try_from(uid).expect("uid")];
                assert_eq!(d.server.uid_count(level, uid), held);
                assert!(held <= cap.max_connections_per_uid);
            }
            live += usize::try_from(tally).expect("tally");
        }
        assert_eq!(live, d.server.conn_count());
    }

    #[test]
    fn server_hostile_2000_steps_keep_every_counter_exact() {
        let mut limits = ServerLimits::new();
        limits.observe.max_connections = 2;
        limits.observe.max_connections_per_uid = 1;
        limits.operator.max_connections_per_uid = 2;
        let mut d = Driver::with(limits);
        let mut seed = 0x2545_F491_4F6C_DD1Du64;
        let mut slots: [Option<ConnId>; 3] = [None; 3];
        for _ in 0..2_000usize {
            let slot = usize::try_from(xorshift(&mut seed) % 3).unwrap_or(0);
            if let Some(id) = slots[slot] {
                if d.server.conn(id).is_none() {
                    slots[slot] = None;
                }
            }
            match slots[slot] {
                None => {
                    let pick = usize::try_from(xorshift(&mut seed) % 4).unwrap_or(0);
                    let level = Level::ALL[pick];
                    let uid = 1 + u32::try_from(xorshift(&mut seed) % 3).unwrap_or(0);
                    let mut peer = Some(Peer { uid, pid: 7 });
                    if xorshift(&mut seed).is_multiple_of(8) {
                        peer = None;
                    }
                    if let Accept::Accepted(id) = d.server.accept(level, peer, d.now) {
                        d.opened.push(id);
                        d.note_uid(uid);
                        slots[slot] = Some(id);
                    }
                }
                Some(id) => match xorshift(&mut seed) % 10 {
                    0..=2 => {
                        let bytes = payload(&mut seed);
                        d.send(id, &bytes);
                    }
                    3 => d.eof(id),
                    4 => {
                        let ns = xorshift(&mut seed) % (7_000 * MS);
                        for done in d.tick(ns) {
                            assert!(d.server.conn(done).is_none());
                        }
                        for open in d.server.ids() {
                            let conn = d.server.conn(open).expect("conn");
                            assert!(!conn.is_closed() || conn.has_final());
                        }
                    }
                    5 | 6 => {
                        let max = usize::try_from(xorshift(&mut seed) % 60).unwrap_or(0);
                        d.pump(id, max);
                    }
                    7 => {
                        if d.server.is_closed(id) {
                            d.close_out(id);
                        }
                    }
                    8 => {
                        if xorshift(&mut seed).is_multiple_of(4) {
                            d.server.finish(id);
                        }
                    }
                    _ => {
                        if xorshift(&mut seed).is_multiple_of(8) {
                            d.server.shutdown();
                        }
                    }
                },
            }
            check_counters(&d, &limits);
        }
        for _ in 0..3 {
            d.tick(400_000 * MS);
        }
        d.settle_all();
        assert_clean(&d, "after the run");
    }
}
