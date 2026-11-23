//! The server layer of the probe's serving side (design sections 1 and 3, R20, R22, R23, R27,
//! R28; WIRE.md sections 4.5, 5.1, 5.3 and 5.4), sans-IO like [`Conn`]: no sockets,
//! no threads, no clock reads.
//!
//! A [`Server`] owns ONE `SendHub`, the connections keyed by `ConnId` (iterated in ascending id
//! order), the per-level and per-uid connection counters, the per-level [`Limits`] and the
//! backend. A single-threaded driver calls [`Server::accept`], [`Server::on_bytes`],
//! [`Server::on_eof`], [`Server::poll`], [`Server::on_tick`], [`Server::output`] and
//! [`Server::wrote`], and finishes every connection it is done with.
//!
//! # Contracts
//!
//! - **`accept` (WIRE.md section 5.4 `refuse`).** `Control` is never served by the probe (design
//!   section 1: it belongs to `act`), a missing peer has no identity, an observer without the
//!   accept-time cgroup issuer is refused, a level at its `max_connections` or a uid at its
//!   `max_connections_per_uid` on that level is full: all four answer [`Accept::Refused`] with
//!   no hub entry, no counter and no byte. Otherwise the connection is added to the hub with the
//!   peer's uid, a [`Conn`] is created and the counters grow (accept when `count < cap`). The peer
//!   is captured once: every later decrement and hub call uses the stored uid (R23).
//! - **`finish` is the single release path (R22).** It decrements the level and uid counters,
//!   releases every query view, unexposed reservation and cursor it owns, deletes a uid entry
//!   that reaches 0, removes the connection from the hub and drops it. It is
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
//! - **Tick delivery and retries (design S7, S25 and S27).** [`Server::on_tick`] asks the
//!   registry for at most one shared tick per due stream, then drives each subscriber through
//!   [`Conn::push_tick`] or [`Conn::end_subscription`]. [`Server::wrote`] and [`Server::poll`]
//!   finish with a fair pass over every Conn-owned pending query, rotating peer uids and then
//!   requests within each uid. Each eligible request gets at most one BLOCK per pass.
//! - **Timer order (R20), per [`Server::poll`].** Connections are polled in `ConnId` order
//!   (the hello deadline, the reader's age, idle); then the hub's `poll` closes the stalled or
//!   dribbled ones with `slow-client` (again in id order); the first fatal outcome of a
//!   connection wins, so a tie is settled by the order: the hello deadline against the writer
//!   stall on the unread server prefix, and the reader's age (`deadline`) against a stall due in
//!   the same `poll` (the reader's age wins, the hub's `slow-client` finds a closed connection).
//! - **One hub, server-wide block-pool caps.** The Live and Query pool caps are the smaller
//!   observe/operator values and are unified in both levels' hello limits
//!   ([`ServerLimits::queue_limits`], S20). The hub's control cap and timers are the widest of the
//!   served levels. The observer's smaller `control_queue_bytes` (design R28) is enforced by the
//!   server after every `on_bytes`: a connection whose queued control bytes exceed its own level's
//!   cap is closed with `slow-client`. A frame is at most `max_reply_bytes`, below the cap, so this
//!   equals the hub's rule (a frame into an empty control queue is always admitted).
//! - **Every push path ends in `enforce_control`.** The server hands a connection the hub and
//!   the backend through one private method (`drive`), which runs the control-cap check right
//!   after the connection returns. Every path that can queue a frame of a connection (input,
//!   EVT, END, live and query pushes) MUST go through it and never
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

use hostobs_proto::hub::{ConnId, QueryOutcome, QueueLimits, SendHub};

use crate::backend::{Backend, BackendError, Identity, StatusInfo, TickData};
use crate::codes::{Code, Detail, Pool};
use crate::conn::{Conn, Peer};
use crate::limits::{Level, Limits};
use crate::query::{
    self, CursorBinding, CursorTable, CursorTarget, PendingSeries, PendingSnapshot, ProcsArgs,
    QueryBackend, QueryBounds, QuerySourceError, QueryViewId, SeriesArgs, SeriesPosition,
    SnapshotArgs, TierCatalog, TierChoice,
};
use crate::registry::{Delivery, SelectionStreams, StreamLimits};
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
    /// `frame_completion_ms` are widest; `tick_ms` is the larger observe/operator value; and
    /// `max_rows`, `max_block_bytes`, and the per-connection Live and Query pool caps are the
    /// smaller observe/operator values. This gives the one hub a server-wide block-pool bound and
    /// announces that same bound at both block-serving levels. [`Server::new`] applies it.
    #[must_use]
    pub fn unified(&self) -> ServerLimits {
        let client_queue_bytes = self
            .observe
            .client_queue_bytes
            .min(self.operator.client_queue_bytes);
        let query_queue_bytes = self
            .observe
            .query_queue_bytes
            .min(self.operator.query_queue_bytes);
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
            .min(query_queue_bytes)
            .min(client_queue_bytes);
        let mut out = *self;
        for limits in [&mut out.observe, &mut out.operator, &mut out.observer] {
            limits.stall_ms = stall_ms;
            limits.frame_completion_ms = frame_completion_ms;
        }
        for limits in [&mut out.observe, &mut out.operator] {
            limits.client_queue_bytes = client_queue_bytes;
            limits.query_queue_bytes = query_queue_bytes;
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

    /// The caps of the one hub: Live and Query pools use the smaller observe/operator values;
    /// the control queue and timers use the widest value of all served levels (the observer's
    /// smaller control queue is enforced by the server, see the module docs).
    #[must_use]
    pub fn queue_limits(&self) -> QueueLimits {
        let observe = self.observe.queue_limits();
        let operator = self.operator.queue_limits();
        let observer = self.observer.queue_limits();
        QueueLimits {
            client_queue_bytes: observe.client_queue_bytes.min(operator.client_queue_bytes),
            uid_live_bytes: observe.uid_live_bytes.min(operator.uid_live_bytes),
            global_live_bytes: observe.global_live_bytes.min(operator.global_live_bytes),
            query_queue_bytes: observe.query_queue_bytes.min(operator.query_queue_bytes),
            uid_query_bytes: observe.uid_query_bytes.min(operator.uid_query_bytes),
            global_query_bytes: observe.global_query_bytes.min(operator.global_query_bytes),
            control_queue_bytes: observe
                .control_queue_bytes
                .max(operator.control_queue_bytes)
                .max(observer.control_queue_bytes),
            stall_ns: observe
                .stall_ns
                .max(operator.stall_ns)
                .max(observer.stall_ns),
            frame_completion_ns: observe
                .frame_completion_ns
                .max(operator.frame_completion_ns)
                .max(observer.frame_completion_ns),
        }
    }
}

impl Default for ServerLimits {
    fn default() -> ServerLimits {
        ServerLimits::new()
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

fn scan_age_ms(published_mono_ns: u64, now_ns: u64) -> u32 {
    let age = now_ns.saturating_sub(published_mono_ns) / 1_000_000;
    u32::try_from(age).unwrap_or(u32::MAX)
}

fn reply_fits(conn: &Conn, corr: u32, result: &str) -> bool {
    let cap = usize::try_from(conn.reply_cap()).unwrap_or(usize::MAX);
    if conn.json_mode() {
        let line = format!("{{\"ok\":true,\"corr\":{corr},\"result\":{result}}}");
        line.len() <= cap
    } else {
        let payload = crate::encode::ok_payload(result);
        payload.len() <= cap
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
pub struct Server<B: Backend + QueryBackend> {
    limits: ServerLimits,
    hub: SendHub,
    conns: BTreeMap<ConnId, Conn>,
    counts: Counts,
    streams: SelectionStreams,
    catalog: TierCatalog,
    cursors: CursorTable,
    uid_rotation: usize,
    request_rotation: BTreeMap<u32, usize>,
    backend: B,
}

impl<B: Backend + QueryBackend> Server<B> {
    /// A server with no connection.
    #[must_use]
    pub fn new(limits: ServerLimits, backend: B) -> Server<B> {
        let limits = limits.unified();
        let hub = SendHub::new(limits.queue_limits());
        let streams = SelectionStreams::new(limits.stream_limits());
        let catalog = TierCatalog::from_status(&backend.status());
        Server {
            limits,
            hub,
            conns: BTreeMap::new(),
            counts: Counts::default(),
            streams,
            catalog,
            cursors: CursorTable::default(),
            uid_rotation: 0,
            request_rotation: BTreeMap::new(),
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
        if level == Level::Observer && peer.issuer().is_none() {
            return Accept::Refused;
        }
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
        self.query_pass(now_ns);
    }

    /// The peer of `id` ended its stream.
    pub fn on_eof(&mut self, id: ConnId, now_ns: u64) {
        self.drive(id, |c, hub, _back, streams| c.on_eof(now_ns, hub, streams));
        self.query_pass(now_ns);
    }

    /// One sampler tick (design S11 and S25): build the due selection streams, then deliver
    /// every push or stream-ending delivery through the per-connection drive path in registry order.
    pub fn on_tick(&mut self, now_ns: u64) {
        let deliveries = self.streams.on_tick(&self.backend);
        for delivery in deliveries {
            match delivery {
                Delivery::Push { tick, to } => {
                    for key in to {
                        self.drive(key.conn, |conn, hub, _backend, _streams| {
                            conn.push_tick(key.corr, &tick, now_ns, hub);
                        });
                    }
                }
                Delivery::End { to, code, detail } => {
                    for key in to {
                        self.drive(key.conn, |conn, hub, _backend, _streams| {
                            conn.end_subscription(key.corr, code, &detail, now_ns, hub);
                        });
                    }
                }
            }
        }
    }

    /// The clock reached `now_ns` (R20): every connection's timers in `ConnId` order, then the
    /// hub's `slow-client` closes, the retry pass, then a sweep of the closed connections that
    /// hold no final bytes. Returns the ids the sweep finished, ascending.
    pub fn poll(&mut self, now_ns: u64) -> Vec<ConnId> {
        for conn in self.conns.values_mut() {
            conn.poll(now_ns, &mut self.hub);
        }
        for (id, _close) in self.hub.poll(now_ns) {
            if let Some(conn) = self.conns.get_mut(&id) {
                conn.close_for(Code::SlowClient, &mut self.hub);
            }
        }
        self.query_pass(now_ns);
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

    /// The driver wrote `n` bytes of the head [`Server::output`] offered. A completed write frees
    /// Query room, so the method ends with the retry pass (design S7).
    pub fn wrote(&mut self, id: ConnId, n: usize, now_ns: u64) {
        if let Some(conn) = self.conns.get_mut(&id) {
            conn.on_written(n, now_ns, &mut self.hub);
        }
        self.query_pass(now_ns);
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
        let Some(mut conn) = self.conns.remove(&id) else {
            for view in self.cursors.abort_conn(id) {
                self.backend.release_view(view);
            }
            return;
        };
        let corrs = conn.query_corrs();
        for corr in corrs {
            match conn.take_query(corr) {
                Some(crate::conn::QueryPending::SnapshotPending(state)) => {
                    self.release_view(state.view_id());
                }
                Some(crate::conn::QueryPending::SeriesPage(mut state)) => {
                    self.abort_series_state(&mut state);
                }
                _ => {}
            }
        }
        for view in self.cursors.abort_conn(id) {
            self.backend.release_view(view);
        }
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

    /// The immutable tier catalog captured at construction.
    #[must_use]
    pub fn tiers(&self) -> &TierCatalog {
        &self.catalog
    }

    /// The live cursor count.
    #[must_use]
    pub fn cursor_count(&self) -> usize {
        self.cursors.len()
    }

    /// The current reference count of a query view.
    #[must_use]
    pub fn view_references(&self, view: QueryViewId) -> u32 {
        self.cursors.references(view)
    }

    /// Invalidate a source view and every cursor bound to it.
    pub fn invalidate_query_view(&mut self, view: QueryViewId) {
        self.abort_view(view);
    }

    fn abort_view(&mut self, view: QueryViewId) {
        let release = self.cursors.invalidate(view);
        for view in release {
            self.backend.release_view(view);
        }
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

    fn query_bounds(&self, conn: &Conn) -> QueryBounds {
        let limits = conn.limits();
        QueryBounds {
            max_rows: limits.max_rows,
            max_block_buckets: limits.max_block_buckets,
            max_block_bytes: limits.max_block_bytes,
        }
    }

    fn cursor_binding(&self, conn: &Conn, verb: &'static str, arguments: Vec<u8>) -> CursorBinding {
        let identity = self.backend.identity();
        CursorBinding {
            boot_id: identity.boot_id,
            epoch: identity.epoch,
            level: conn.level(),
            uid: conn.peer().uid,
            verb,
            arguments,
        }
    }

    fn release_view(&mut self, view: QueryViewId) {
        if self.cursors.release_view(view) {
            self.backend.release_view(view);
        }
    }

    fn invalidate_view(&mut self, view: QueryViewId) {
        self.abort_view(view);
    }

    /// One fair query scheduling pass. Peer uids rotate, and within each uid its requests
    /// rotate independently; every request receives at most one scheduler step per pass.
    fn query_pass(&mut self, now_ns: u64) {
        let expired = self.cursors.expire(now_ns);
        for view in expired {
            self.backend.release_view(view);
        }
        let mut work: BTreeMap<u32, Vec<(ConnId, u32, bool)>> = BTreeMap::new();
        for (id, conn) in &self.conns {
            let query_corrs = conn.query_corrs();
            let current_corrs = conn.current_corrs();
            if query_corrs.is_empty() && current_corrs.is_empty() {
                continue;
            }
            let requests = work.entry(conn.peer().uid).or_default();
            for corr in query_corrs {
                requests.push((*id, corr, false));
            }
            for corr in current_corrs {
                requests.push((*id, corr, true));
            }
        }
        if work.is_empty() {
            self.uid_rotation = 0;
            self.request_rotation.clear();
            return;
        }
        let mut uids: Vec<u32> = work.keys().copied().collect();
        let start = self.uid_rotation % uids.len();
        uids.rotate_left(start);
        self.uid_rotation = (start + 1) % work.len();
        for uid in uids {
            let requests = work.get_mut(&uid).expect("the uid key came from this map");
            requests.sort_unstable_by_key(|(id, corr, current)| (*id, *corr, *current));
            let start = self.request_rotation.get(&uid).copied().unwrap_or(0) % requests.len();
            requests.rotate_left(start);
            self.request_rotation
                .insert(uid, (start + 1) % requests.len());
            let ready = requests.clone();
            for (id, corr, current) in ready {
                if current {
                    self.drive(id, |conn, hub, backend, _streams| {
                        conn.retry_current(corr, now_ns, hub, backend);
                    });
                } else {
                    let Some(mut conn) = self.conns.remove(&id) else {
                        continue;
                    };
                    self.step_conn_query(&mut conn, corr, now_ns);
                    self.conns.insert(id, conn);
                    self.enforce_control(id);
                }
                if self.is_closed(id) && !self.conns.get(&id).is_some_and(Conn::has_final) {
                    self.finish(id);
                }
            }
        }
    }

    fn step_conn_query(&mut self, conn: &mut Conn, corr: u32, now_ns: u64) {
        if conn.is_closed() {
            return;
        }
        let Some(query) = conn.take_query(corr) else {
            return;
        };
        match query {
            crate::conn::QueryPending::Snapshot(args) => {
                self.start_snapshot(conn, corr, args, now_ns);
            }
            crate::conn::QueryPending::SnapshotPending(state) => {
                self.step_snapshot(conn, corr, *state, now_ns);
            }
            crate::conn::QueryPending::Series(args) => {
                self.start_series(conn, corr, args, now_ns);
            }
            crate::conn::QueryPending::SeriesPage(state) => {
                self.step_series(conn, corr, *state, now_ns);
            }
            crate::conn::QueryPending::Procs(args) => {
                self.serve_procs(conn, corr, args, now_ns);
            }
        }
        conn.complete_draining(&mut self.hub);
    }

    fn start_snapshot(&mut self, conn: &mut Conn, corr: u32, args: SnapshotArgs, now_ns: u64) {
        if let TierChoice::Explicit(tier) = args.request.tier {
            if self.catalog.explicit(tier).is_none() {
                conn.push_query_error(corr, Code::NotFound, &Detail::Empty, now_ns, &mut self.hub);
                return;
            }
        }
        let bounds = self.query_bounds(conn);
        let view = match self.backend.snapshot_open(&args.request, now_ns, bounds) {
            Ok(view) => view,
            Err(error) => {
                let (code, detail) = query::snapshot_open_error(error);
                conn.push_query_error(corr, code, &detail, now_ns, &mut self.hub);
                return;
            }
        };
        self.cursors.add_view(QueryViewId::Snapshot(view.id));
        if let Err(error) =
            query::validate_snapshot_meta(&view.meta, &args.request, &self.catalog, bounds.max_rows)
        {
            self.release_view(QueryViewId::Snapshot(view.id));
            let (code, detail) = match error {
                QuerySourceError::Budget(Pool::Rows) => (Code::Budget, Detail::Pool(Pool::Rows)),
                _ => (Code::Internal, Detail::Empty),
            };
            conn.push_query_error(corr, code, &detail, now_ns, &mut self.hub);
            return;
        }
        if args.request.provenance {
            let Some(cells) = query::provenance_cells(&view.meta) else {
                self.release_view(QueryViewId::Snapshot(view.id));
                conn.push_query_error(corr, Code::Internal, &Detail::Empty, now_ns, &mut self.hub);
                return;
            };
            if cells > query::MAX_PROVENANCE_CELLS {
                self.release_view(QueryViewId::Snapshot(view.id));
                conn.push_query_error(
                    corr,
                    Code::Budget,
                    &Detail::Pool(Pool::Provenance),
                    now_ns,
                    &mut self.hub,
                );
                return;
            }
        }
        let Some(frame_bytes) = query::snapshot_frame_len(&view.meta) else {
            self.release_view(QueryViewId::Snapshot(view.id));
            conn.push_query_error(corr, Code::Internal, &Detail::Empty, now_ns, &mut self.hub);
            return;
        };
        if frame_bytes > usize::try_from(bounds.max_block_bytes).unwrap_or(usize::MAX) {
            self.release_view(QueryViewId::Snapshot(view.id));
            conn.push_query_error(
                corr,
                Code::Budget,
                &Detail::Pool(Pool::Block),
                now_ns,
                &mut self.hub,
            );
            return;
        }
        let state = PendingSnapshot {
            args,
            view,
            frame_bytes,
        };
        self.step_snapshot(conn, corr, state, now_ns);
    }

    fn step_snapshot(&mut self, conn: &mut Conn, corr: u32, state: PendingSnapshot, now_ns: u64) {
        let frame_len = state.frame_bytes;
        if !self.hub.query_room(conn.id(), frame_len) {
            conn.put_query(
                corr,
                crate::conn::QueryPending::SnapshotPending(Box::new(state)),
            );
            return;
        }
        let data = match self.backend.snapshot_read(state.view.id) {
            Ok(data) => data,
            Err(error) => {
                self.release_view(QueryViewId::Snapshot(state.view.id));
                let (code, detail) = query::snapshot_read_error(error);
                conn.push_query_error(corr, code, &detail, now_ns, &mut self.hub);
                return;
            }
        };
        if query::validate_snapshot_data(&state.view.meta, &data, &state.args.request, now_ns)
            .is_err()
        {
            self.release_view(QueryViewId::Snapshot(state.view.id));
            conn.push_query_error(corr, Code::Internal, &Detail::Empty, now_ns, &mut self.hub);
            return;
        }
        let provenance = data.provenance.clone();
        let result = query::snapshot_result(&state.view.meta, provenance.as_deref(), now_ns);
        let bounds = BlockBounds {
            max_rows: conn.limits().max_rows,
            max_block_bytes: conn.limits().max_block_bytes,
        };
        let block = match crate::tick::query_block(data.tick, bounds) {
            Ok(block) => block,
            Err(error) => {
                self.release_view(QueryViewId::Snapshot(state.view.id));
                let (code, detail) = error.refusal();
                conn.push_query_error(corr, code, &detail, now_ns, &mut self.hub);
                return;
            }
        };
        if block.len() != frame_len {
            self.release_view(QueryViewId::Snapshot(state.view.id));
            conn.push_query_error(corr, Code::Internal, &Detail::Empty, now_ns, &mut self.hub);
            return;
        }
        conn.push_query_rep(corr, &result, now_ns, &mut self.hub);
        if conn.is_closed() {
            self.release_view(QueryViewId::Snapshot(state.view.id));
            return;
        }
        match conn.push_query_block(corr, block, now_ns, &mut self.hub) {
            QueryOutcome::Queued => {
                self.release_view(QueryViewId::Snapshot(state.view.id));
                conn.push_query_end(
                    corr,
                    crate::encode::EndReason::Complete,
                    None,
                    now_ns,
                    &mut self.hub,
                );
                conn.complete_query(corr, &mut self.hub);
            }
            QueryOutcome::Backpressure(_) => {
                self.release_view(QueryViewId::Snapshot(state.view.id));
                conn.push_query_end_error(
                    corr,
                    Code::Internal,
                    &Detail::Empty,
                    now_ns,
                    &mut self.hub,
                );
                conn.complete_query(corr, &mut self.hub);
            }
        }
    }

    fn start_series(&mut self, conn: &mut Conn, corr: u32, args: SeriesArgs, now_ns: u64) {
        if args.request.step_ms != 0 && !self.catalog.has_step(args.request.step_ms) {
            conn.push_query_error(
                corr,
                Code::BadRequest,
                &Detail::key("step_ms"),
                now_ns,
                &mut self.hub,
            );
            return;
        }
        let Some(arguments) = query::canonical_series_arguments(&args.request) else {
            conn.push_query_error(corr, Code::Internal, &Detail::Empty, now_ns, &mut self.hub);
            return;
        };
        let binding = self.cursor_binding(conn, "series_batch", arguments);
        let (view, start, owns_active_ref) = if let Some(token) = args.cursor.as_deref() {
            match self.cursors.lookup(token, &binding, now_ns) {
                Ok(CursorTarget::Series { view, position }) => (view, position, false),
                Ok(_) | Err(_) => {
                    conn.push_query_error(
                        corr,
                        Code::CursorInvalid,
                        &Detail::Empty,
                        now_ns,
                        &mut self.hub,
                    );
                    return;
                }
            }
        } else {
            let bounds = self.query_bounds(conn);
            let view = match self.backend.series_open(&args.request, now_ns, bounds) {
                Ok(view) => view,
                Err(error) => {
                    let (code, detail) = query::series_open_error(error, false);
                    conn.push_query_error(corr, code, &detail, now_ns, &mut self.hub);
                    return;
                }
            };
            self.cursors.add_view(QueryViewId::Series(view.id));
            let start = view.first.unwrap_or(SeriesPosition(0));
            (std::sync::Arc::new(view), start, true)
        };
        if let Err(error) =
            query::validate_series_view(&view, &args.request, &self.catalog, conn.limits().max_rows)
        {
            if owns_active_ref {
                self.release_view(QueryViewId::Series(view.id));
            }
            let (code, detail) = query::series_open_error(error, args.cursor.is_some());
            conn.push_query_error(corr, code, &detail, now_ns, &mut self.hub);
            return;
        }
        let dictionary = match query::series_dictionary(&view, &args.request) {
            Ok(dictionary) => dictionary,
            Err(_) => {
                if owns_active_ref {
                    self.release_view(QueryViewId::Series(view.id));
                }
                conn.push_query_error(corr, Code::Internal, &Detail::Empty, now_ns, &mut self.hub);
                return;
            }
        };
        let page = if view.first.is_none() {
            query::PagePlan {
                blocks: Vec::new(),
                continuation: None,
            }
        } else {
            match query::plan_series_page(
                &self.backend,
                query::SeriesPlanInput {
                    view: &view,
                    request: &args.request,
                    permitted_columns: &dictionary.permitted,
                    dictionary_len: dictionary.body.len(),
                    bounds: self.query_bounds(conn),
                    max_blocks: args.max_blocks,
                    start,
                },
            ) {
                Ok(page) => page,
                Err(error) => {
                    if owns_active_ref {
                        self.release_view(QueryViewId::Series(view.id));
                    }
                    if error == QuerySourceError::ViewInvalid {
                        self.invalidate_view(QueryViewId::Series(view.id));
                    }
                    let (code, detail) = match error {
                        QuerySourceError::Budget(Pool::Block) => {
                            (Code::Budget, Detail::Pool(Pool::Block))
                        }
                        other => query::series_group_error(other, args.cursor.is_some()),
                    };
                    conn.push_query_error(corr, code, &detail, now_ns, &mut self.hub);
                    return;
                }
            }
        };
        let (next_token, next_token_owned) = if let Some(position) = page.continuation {
            match self.cursors.reserve(
                &binding,
                CursorTarget::Series {
                    view: view.clone(),
                    position,
                },
                Some((conn.id(), corr)),
                now_ns,
            ) {
                Ok(reservation) => (Some(reservation.token), reservation.created),
                Err(_) => {
                    if owns_active_ref {
                        self.release_view(QueryViewId::Series(view.id));
                    }
                    conn.push_query_error(
                        corr,
                        Code::Budget,
                        &Detail::Pool(Pool::Cursors),
                        now_ns,
                        &mut self.hub,
                    );
                    return;
                }
            }
        } else {
            (None, false)
        };
        let result = query::series_result(&view, &dictionary.omitted);
        conn.push_query_rep(corr, &result, now_ns, &mut self.hub);
        if conn.is_closed() {
            if owns_active_ref {
                self.release_view(QueryViewId::Series(view.id));
            }
            if next_token_owned {
                let token = next_token
                    .as_deref()
                    .expect("a created reservation has a token");
                if let Some(view) = self.cursors.abort_reservation(token) {
                    self.backend.release_view(view);
                }
            }
            return;
        }
        let state = PendingSeries {
            args,
            view,
            permitted: dictionary.permitted,
            omitted: dictionary.omitted,
            dictionary: dictionary.body,
            page,
            next_block: 0,
            next_token,
            next_token_owned,
            rep_sent: true,
            owns_active_ref,
        };
        self.step_series(conn, corr, state, now_ns);
    }

    fn step_series(&mut self, conn: &mut Conn, corr: u32, mut state: PendingSeries, now_ns: u64) {
        if state.next_block >= state.page.blocks.len() {
            self.finish_series_page(conn, corr, &mut state, now_ns);
            return;
        }
        let plan = state.page.blocks[state.next_block];
        let dictionary = (state.next_block == 0).then_some(state.dictionary.clone());
        let exact = match query::series_plan_frame_len(
            plan.buckets,
            &state.permitted,
            plan.agg,
            dictionary.as_ref().map(Vec::len),
        ) {
            Some(exact) => exact,
            None => {
                self.fail_series(conn, corr, &mut state, QuerySourceError::Internal, now_ns);
                return;
            }
        };
        if !self.hub.query_room(conn.id(), exact) {
            conn.put_query(corr, crate::conn::QueryPending::SeriesPage(Box::new(state)));
            return;
        }
        let data = match self
            .backend
            .series_read(state.view.id, plan.start, plan.buckets)
        {
            Ok(data) => data,
            Err(error) => {
                self.fail_series(conn, corr, &mut state, error, now_ns);
                return;
            }
        };
        let identity = self.backend.identity();
        let block = match query::series_block(
            &identity,
            &state.view,
            &state.args.request,
            &state.permitted,
            dictionary,
            plan,
            data,
        ) {
            Ok(block) => block,
            Err(error) => {
                self.fail_series(conn, corr, &mut state, error, now_ns);
                return;
            }
        };
        if block.len() != exact {
            self.fail_series(conn, corr, &mut state, QuerySourceError::Internal, now_ns);
            return;
        }
        match conn.push_query_block(corr, block, now_ns, &mut self.hub) {
            QueryOutcome::Queued => {
                state.next_block += 1;
                if state.next_block >= state.page.blocks.len() {
                    self.finish_series_page(conn, corr, &mut state, now_ns);
                } else {
                    conn.put_query(corr, crate::conn::QueryPending::SeriesPage(Box::new(state)));
                }
            }
            QueryOutcome::Backpressure(_) => {
                conn.push_query_end_error(
                    corr,
                    Code::Internal,
                    &Detail::Empty,
                    now_ns,
                    &mut self.hub,
                );
                self.abort_series_state(&mut state);
                conn.complete_query(corr, &mut self.hub);
            }
        }
    }

    fn finish_series_page(
        &mut self,
        conn: &mut Conn,
        corr: u32,
        state: &mut PendingSeries,
        now_ns: u64,
    ) {
        if state
            .next_token
            .as_deref()
            .is_some_and(|token| !self.cursors.expose(token))
        {
            self.abort_series_state(state);
            conn.push_query_end_error(
                corr,
                Code::CursorInvalid,
                &Detail::Empty,
                now_ns,
                &mut self.hub,
            );
            conn.complete_query(corr, &mut self.hub);
            return;
        }
        conn.push_query_end(
            corr,
            if state.page.continuation.is_some() {
                crate::encode::EndReason::Page
            } else {
                crate::encode::EndReason::Complete
            },
            state.next_token.as_deref(),
            now_ns,
            &mut self.hub,
        );
        if conn.is_closed() && state.next_token_owned {
            if let Some(token) = state.next_token.as_deref() {
                if let Some(view) = self.cursors.abort_reservation(token) {
                    self.backend.release_view(view);
                }
            }
        }
        if state.owns_active_ref {
            self.release_view(state.view_id());
            state.owns_active_ref = false;
        }
        conn.complete_query(corr, &mut self.hub);
    }

    fn fail_series(
        &mut self,
        conn: &mut Conn,
        corr: u32,
        state: &mut PendingSeries,
        error: QuerySourceError,
        now_ns: u64,
    ) {
        let (code, detail) = if state.rep_sent {
            query::series_read_end_error(error)
        } else {
            query::series_open_error(error, state.args.cursor.is_some())
        };
        if error == QuerySourceError::ViewInvalid {
            self.abort_series_state(state);
            self.invalidate_view(state.view_id());
        } else {
            self.abort_series_state(state);
        }
        if state.rep_sent {
            conn.push_query_end_error(corr, code, &detail, now_ns, &mut self.hub);
        } else {
            conn.push_query_error(corr, code, &detail, now_ns, &mut self.hub);
        }
        conn.complete_query(corr, &mut self.hub);
    }

    fn abort_series_state(&mut self, state: &mut PendingSeries) {
        if state.owns_active_ref {
            self.release_view(state.view_id());
            state.owns_active_ref = false;
        }
        if state.next_token_owned {
            let token = state.next_token.take();
            let token = token.as_deref().unwrap_or_default();
            if let Some(view) = self.cursors.abort_reservation(token) {
                self.backend.release_view(view);
            }
        } else {
            state.next_token = None;
        }
        state.next_token_owned = false;
    }

    fn serve_procs(&mut self, conn: &mut Conn, corr: u32, args: ProcsArgs, now_ns: u64) {
        let Some(arguments) = query::canonical_procs_arguments(&args.request) else {
            conn.push_query_error(corr, Code::Internal, &Detail::Empty, now_ns, &mut self.hub);
            return;
        };
        let binding = self.cursor_binding(conn, "procs", arguments);
        let (view, mut index, mut last, owns_active_ref) =
            if let Some(token) = args.cursor.as_deref() {
                match self.cursors.lookup(token, &binding, now_ns) {
                    Ok(CursorTarget::Procs { view, index, last }) => (view, index, last, false),
                    Ok(_) | Err(_) => {
                        conn.push_query_error(
                            corr,
                            Code::CursorInvalid,
                            &Detail::Empty,
                            now_ns,
                            &mut self.hub,
                        );
                        return;
                    }
                }
            } else {
                match self
                    .backend
                    .procs_open(&args.request, now_ns, conn.limits().max_rows)
                {
                    Ok(view) => {
                        self.cursors.add_view(QueryViewId::Procs(view.id));
                        (view, 0, None, true)
                    }
                    Err(error) => {
                        let (code, detail) = query::procs_open_error(error);
                        conn.push_query_error(corr, code, &detail, now_ns, &mut self.hub);
                        return;
                    }
                }
            };
        if view.row_count > conn.limits().max_rows
            || view.scanned > view.total
            || view.published_mono_ns > now_ns
        {
            if owns_active_ref {
                self.release_view(QueryViewId::Procs(view.id));
            }
            conn.push_query_error(corr, Code::Internal, &Detail::Empty, now_ns, &mut self.hub);
            return;
        }
        let mut rows = Vec::new();
        let mut terminal = index >= view.row_count;
        while index < view.row_count
            && rows.len() < usize::try_from(args.limit).unwrap_or(usize::MAX)
        {
            let row = match self
                .backend
                .procs_read(view.id, index, &args.request.fields)
            {
                Ok(Some(row)) => row,
                Ok(None) => {
                    if owns_active_ref {
                        self.release_view(QueryViewId::Procs(view.id));
                    }
                    conn.push_query_error(
                        corr,
                        Code::Internal,
                        &Detail::Empty,
                        now_ns,
                        &mut self.hub,
                    );
                    return;
                }
                Err(error) => {
                    let (code, detail) = query::procs_read_error(error, args.cursor.is_some());
                    if error == QuerySourceError::ViewInvalid {
                        if owns_active_ref {
                            self.release_view(QueryViewId::Procs(view.id));
                        }
                        self.invalidate_view(QueryViewId::Procs(view.id));
                    } else if owns_active_ref {
                        self.release_view(QueryViewId::Procs(view.id));
                    }
                    conn.push_query_error(corr, code, &detail, now_ns, &mut self.hub);
                    return;
                }
            };
            if !query::validate_proc_row(&row, &args.request.fields, last) {
                if owns_active_ref {
                    self.release_view(QueryViewId::Procs(view.id));
                }
                conn.push_query_error(corr, Code::Internal, &Detail::Empty, now_ns, &mut self.hub);
                return;
            }
            last = Some((row.pid, row.start_ticks));
            rows.push(row);
            index = index.saturating_add(1);
            terminal = index >= view.row_count;
            let probe_next = (!terminal).then_some("c0000000000000000");
            let candidate = query::procs_result(
                &rows,
                &args.request.fields,
                scan_age_ms(view.published_mono_ns, now_ns),
                &view,
                probe_next,
            );
            if !reply_fits(conn, corr, &candidate) {
                if rows.len() == 1 {
                    if owns_active_ref {
                        self.release_view(QueryViewId::Procs(view.id));
                    }
                    conn.push_query_error(
                        corr,
                        Code::Internal,
                        &Detail::Empty,
                        now_ns,
                        &mut self.hub,
                    );
                    return;
                }
                rows.pop();
                index = index.saturating_sub(1);
                last = rows.last().map(|row| (row.pid, row.start_ticks));
                terminal = index >= view.row_count;
                break;
            }
        }
        let (next_token, next_token_owned) = if !terminal {
            match self.cursors.reserve(
                &binding,
                CursorTarget::Procs {
                    view: view.clone(),
                    index,
                    last,
                },
                Some((conn.id(), corr)),
                now_ns,
            ) {
                Ok(reservation) => (Some(reservation.token), reservation.created),
                Err(_) => {
                    if owns_active_ref {
                        self.release_view(QueryViewId::Procs(view.id));
                    }
                    conn.push_query_error(
                        corr,
                        Code::Budget,
                        &Detail::Pool(Pool::Cursors),
                        now_ns,
                        &mut self.hub,
                    );
                    return;
                }
            }
        } else {
            (None, false)
        };
        let result = query::procs_result(
            &rows,
            &args.request.fields,
            scan_age_ms(view.published_mono_ns, now_ns),
            &view,
            next_token.as_deref(),
        );
        if !reply_fits(conn, corr, &result) {
            if next_token_owned {
                let token = next_token
                    .as_deref()
                    .expect("a created reservation has a token");
                if let Some(view) = self.cursors.abort_reservation(token) {
                    self.backend.release_view(view);
                }
            }
            if owns_active_ref {
                self.release_view(QueryViewId::Procs(view.id));
            }
            conn.push_query_error(corr, Code::Internal, &Detail::Empty, now_ns, &mut self.hub);
            return;
        }
        conn.push_query_rep(corr, &result, now_ns, &mut self.hub);
        if let Some(token) = next_token.as_deref() {
            if conn.is_closed() {
                if next_token_owned {
                    if let Some(view) = self.cursors.abort_reservation(token) {
                        self.backend.release_view(view);
                    }
                }
            } else {
                self.cursors.expose(token);
            }
        }
        if owns_active_ref {
            self.release_view(QueryViewId::Procs(view.id));
        }
        conn.complete_query(corr, &mut self.hub);
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
    use std::collections::{BTreeMap, BTreeSet};
    use std::sync::Arc;

    use hostobs_proto::dict::{
        Aggregation, Entry, MetricFacts, MetricKind, MetricScope, MetricSource, MetricUnit,
        MetricValueType,
    };
    use hostobs_proto::envelope::{check_end, check_rep};
    use hostobs_proto::frame::{decode_block_with, Block, FrameType};
    use hostobs_proto::hub::{Close, ConnId, LiveOutcome, Pools, QueryOutcome, TickForms};
    use hostobs_proto::stream_dicts::StreamDicts;
    use serde_json::Value;

    use super::{Accept, Server, ServerLimits, SERVED};
    use crate::backend::{Backend, BackendError, FixedBackend, TickData, TickInfo, TierInfo};
    use crate::codes::{Code, Pool};
    use crate::conn::{Peer, State, SERVER_MINOR};
    use crate::encode::{control_frame, server_prefix};
    use crate::limits::{Level, Limits};
    use crate::query::{
        CursorBinding, CursorTarget, HistoryBlockData, HistoryBucket, HistoryColumn, ProcField,
        ProcProjectionId, ProcRow, ProcScanId, ProcScanView, ProcsRequest, QueryBackend,
        QueryColumnMeta, QuerySourceError, QueryViewId, SeriesGroup, SeriesPosition, SeriesRequest,
        SeriesView, SeriesViewId, SnapshotData, SnapshotMeta, SnapshotRequest, SnapshotSubjectMeta,
        SnapshotView, SnapshotViewId, Tier, TierChoice, CURSOR_LIFETIME_NS, MAX_CURSOR_ENTRIES,
    };
    use crate::registry::{Delivery, SelectionCaps, SubKey};
    use crate::selection::{Sel, Selection};
    use crate::tick::BlockBounds;

    const MS: u64 = 1_000_000;
    const HELLO_ARGS: &str = r#"{"client":"t","proto":{"major":1,"minor":1}}"#;
    const CURRENT_ARGS: &str = r#"{"selection":{"entities":[{"inc":1}],"metrics":["m"]}}"#;

    fn req(corr: u32, verb: &str) -> Vec<u8> {
        request(corr, verb, "{}")
    }

    fn request(corr: u32, verb: &str, args: &str) -> Vec<u8> {
        let args = if verb == "hello" { HELLO_ARGS } else { args };
        let json = format!("{{\"verb\":\"{verb}\",\"args\":{args}}}");
        control_frame(FrameType::Req, corr, json.as_bytes())
    }

    fn subscribe_args(inc: u64, interval_ms: u32) -> String {
        format!(
            "{{\"selection\":{{\"entities\":[{{\"inc\":{inc}}}],\"metrics\":[\"m\"]}},\
             \"interval_ms\":{interval_ms}}}"
        )
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

    /// `(type, corr, whole frame)` of every complete frame in `bytes`.
    fn raw_frames(bytes: &[u8]) -> Vec<(u8, u32, Vec<u8>)> {
        let mut out = Vec::new();
        let mut rest = bytes;
        while rest.len() >= 12 {
            let len = u32::from_le_bytes([rest[0], rest[1], rest[2], rest[3]]);
            let len = usize::try_from(len).expect("len");
            assert!(len >= 12 && len <= rest.len());
            let corr = u32::from_le_bytes([rest[8], rest[9], rest[10], rest[11]]);
            out.push((rest[4], corr, rest[..len].to_vec()));
            rest = &rest[len..];
        }
        out
    }

    fn frame_payload(frame: &[u8]) -> &[u8] {
        frame.get(12..).unwrap_or_default()
    }

    fn end_info(frame: &[u8]) -> (bool, u32, u32, Option<String>) {
        let body = frame_payload(frame);
        let end = check_end(body).expect("END");
        let reason = end.reason.map(|range| {
            String::from_utf8(body.get(range).expect("reason range").to_vec()).expect("utf-8")
        });
        (end.ok, end.blocks, end.dropped, reason)
    }

    fn assert_end_ok(frame: &[u8], blocks: u32, dropped: u32, reason: &str) {
        let want = (true, blocks, dropped, Some(reason.to_string()));
        assert_eq!(end_info(frame), want);
    }

    fn accept_block(dicts: &mut StreamDicts, corr: u32, frame: &[u8]) -> Block {
        let decoded = decode_block_with(frame, dicts.held(corr)).expect("stream block");
        dicts.accept(corr, &decoded).expect("dictionary transition");
        decoded
    }

    fn end_next(frame: &[u8]) -> Option<String> {
        let body = frame_payload(frame);
        let end = check_end(body).expect("END");
        end.next.map(|range| {
            String::from_utf8(body.get(range).expect("next range").to_vec()).expect("next UTF-8")
        })
    }

    fn stamped_fields(frame: &[u8]) -> (u32, u32) {
        let corr = u32::from_le_bytes([frame[8], frame[9], frame[10], frame[11]]);
        let n_sec = usize::from(u16::from_le_bytes([frame[12], frame[13]]));
        let dropped_at = 16 + 8 * n_sec + 72;
        let dropped = u32::from_le_bytes([
            frame[dropped_at],
            frame[dropped_at + 1],
            frame[dropped_at + 2],
            frame[dropped_at + 3],
        ]);
        (corr, dropped)
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

    struct TickAt {
        generation: u32,
        bytes: Arc<[u8]>,
    }

    impl TickForms for TickAt {
        fn generation(&self) -> u32 {
            self.generation
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
            Driver::with_backend(limits, FixedBackend::new())
        }

        fn with_backend(limits: ServerLimits, backend: FixedBackend) -> Driver {
            Driver {
                server: Server::new(limits, backend),
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
            let cgroup_inc = (level == Level::Observer).then_some(7);
            let peer = Peer {
                uid,
                pid: 7,
                cgroup_inc,
            };
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

        fn server_tick(&mut self) {
            self.server.on_tick(self.now);
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

    fn live_data(seq: u64, label: &str) -> TickData {
        let mut data = TickData::example(seq, &[1], &["m"]);
        for entry in &mut data.entries {
            if let Entry::Entity { label: held, .. } = entry {
                *held = label.to_string();
            }
        }
        data
    }

    fn put_current(d: &mut Driver, selected: &Selection, data: TickData) {
        d.server
            .backend_mut()
            .current
            .insert(selected.clone(), Ok(data));
    }

    /// Queue the subscribe REQ and drain its REP; the first block waits for an explicit tick.
    fn wire_subscribe(d: &mut Driver, id: ConnId, corr: u32, inc: u64, interval_ms: u32) {
        let args = subscribe_args(inc, interval_ms);
        d.send(id, &request(corr, "subscribe", &args));
        d.pump(id, usize::MAX);
        let out = d.take_out(id);
        let raw = raw_frames(&out);
        assert_eq!(raw.len(), 1);
        assert_eq!((raw[0].0, raw[0].1), (FrameType::Rep.code(), corr));
        let body = raw[0].2.get(12..).unwrap_or_default();
        assert!(check_rep(body, false).expect("subscribe REP").ok);
        assert_eq!(d.server.conn(id).map(|conn| conn.subscriptions()), Some(1));
    }

    fn stream_block_len(d: &ServerLimits, data: &TickData) -> usize {
        crate::tick::query_block(
            data.clone(),
            BlockBounds {
                max_rows: d.observe.max_rows,
                max_block_bytes: u32::MAX,
            },
        )
        .expect("query block length")
        .len()
    }

    fn query_bound_limits(need: usize) -> ServerLimits {
        let mut limits = ServerLimits::new();
        let size = u32::try_from(need).expect("query size");
        for level in [&mut limits.observe, &mut limits.operator] {
            level.query_queue_bytes = size;
            level.max_block_bytes = size;
        }
        limits
    }

    /// Fill `blocker`'s Query pool with `len` bytes from another connection of the same uid.
    fn fill_query(d: &mut Driver, blocker: ConnId, len: usize) {
        assert_eq!(d.push_query(blocker, 99, len), QueryOutcome::Queued);
    }

    /// Fill `id`'s Query pool until an exact `frame` cannot fit. The occupancy is derived from
    /// the declared pool and frame bounds, and every synthetic block obeys `max_block_bytes`.
    fn fill_query_short_of_frame(d: &mut Driver, id: ConnId, frame: usize) -> usize {
        let limits = d.server.limits().observe;
        let pool = usize::try_from(limits.query_queue_bytes).expect("query pool");
        let max_block = usize::try_from(limits.max_block_bytes).expect("block bound");
        assert!(
            frame > 0 && frame <= max_block,
            "frame must fit its block bound"
        );
        let occupied = pool
            .checked_sub(frame)
            .and_then(|remaining| remaining.checked_add(1))
            .expect("frame fits the query pool");

        let mut pushed = 0;
        let mut stream = 99;
        while pushed < occupied {
            let len = (occupied - pushed).min(max_block);
            assert_eq!(d.push_query(id, stream, len), QueryOutcome::Queued);
            pushed += len;
            stream += 1;
        }
        assert_eq!(d.server.hub().queued(id).query, occupied);
        occupied
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
        let uid = d.server.conn(id).expect("conn").peer().uid;
        let now_ns = d.now;
        let selected = selection(inc);
        let expected_selection = selected.clone();
        let key = SubKey { conn: id, corr };
        d.server.drive(id, |conn, hub, backend, streams| {
            conn.subscribe_for_server_test(corr, selected, now_ns, hub, backend, streams);
        });
        assert_eq!(d.server.conn(id).map(|conn| conn.subscriptions()), Some(1));
        assert_eq!(d.server.streams.subscribers(), 1);
        assert_eq!(d.server.streams.live_selections(), 1);
        assert_eq!(d.server.streams.uid_selections(uid), 1);
        assert_eq!(
            d.server.streams.selection_of(key),
            Some(&expected_selection)
        );
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
        let peer = Peer {
            uid: 1,
            pid: 7,
            cgroup_inc: None,
        };
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
        assert_eq!(
            conn.peer(),
            Peer {
                uid: 42,
                pid: 7,
                cgroup_inc: Some(7),
            }
        );
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
        d.ready(id);
        register(d, id, 2, 1);
        d.send(id, &req(0, "status"));
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
        d.ready(id);
        register(d, id, 2, 1);
        d.send(id, &[0x40, 0x9C, 0, 0, 1, 0, 0, 0, 5, 0, 0, 0]);
        assert_eq!(d.close_code(id), Some(Code::FrameTooLarge));
    }

    fn path_request_deadline(d: &mut Driver, level: Level) {
        let id = d.open(level, 1000);
        d.ready(id);
        register(d, id, 2, 1);
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
    fn the_default_hub_caps_match_shared_pools_and_widest_controls() {
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
    fn the_hub_caps_use_minimum_block_pools_and_widest_controls_and_timers() {
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
        assert_eq!(hub.client_queue_bytes, 100_000);
        assert_eq!(hub.uid_live_bytes, 200_000);
        assert_eq!(hub.global_live_bytes, 800_000);
        assert_eq!(hub.query_queue_bytes, 100_000);
        assert_eq!(hub.uid_query_bytes, 100_000);
        assert_eq!(hub.global_query_bytes, 400_000);
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
            assert_eq!(limits.client_queue_bytes, 500);
            assert_eq!(limits.query_queue_bytes, 1_000);
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
        let Accept::Accepted(observe) = server.accept(
            Level::Observe,
            Some(Peer {
                uid,
                pid: 7,
                cgroup_inc: None,
            }),
            0,
        ) else {
            panic!("observe accepted");
        };
        let Accept::Accepted(operator) = server.accept(
            Level::Operator,
            Some(Peer {
                uid,
                pid: 8,
                cgroup_inc: None,
            }),
            0,
        ) else {
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
    fn server_current_uses_smallest_shared_query_pool() {
        let incarnations: Vec<u64> = (1..=20).collect();
        let data = TickData::example(1, &incarnations, &["m"]);
        let encoded = crate::tick::query_block(
            data.clone(),
            BlockBounds {
                max_rows: 4_096,
                max_block_bytes: u32::MAX,
            },
        )
        .expect("query block");
        assert!(encoded.len() > 500 && encoded.len() <= 1_500);
        let query_cap = u32::try_from(encoded.len()).expect("query cap");

        let mut limits = ServerLimits::new();
        limits.observe.query_queue_bytes = query_cap;
        limits.operator.query_queue_bytes = query_cap.saturating_mul(4);
        limits.observe.client_queue_bytes = query_cap.saturating_mul(4);
        limits.operator.client_queue_bytes = query_cap.saturating_mul(4);
        let mut d = Driver::with(limits);
        let id = d.open(Level::Observe, 1);
        d.ready(id);
        for level in [Level::Observe, Level::Operator] {
            let advertised = d.server.limits().get(level).expect("served level");
            assert_eq!(advertised.query_queue_bytes, query_cap);
            assert_eq!(advertised.max_block_bytes, query_cap);
        }

        put_current(&mut d, &selection(1), data);
        d.send(id, &request(2, "current", CURRENT_ARGS));
        assert_eq!(d.server.hub().queued(id).query, encoded.len());
        assert!(!d.server.hub().query_room(id, encoded.len()));

        d.send(id, &request(3, "current", CURRENT_ARGS));
        assert_eq!(d.server.hub().queued(id).query, encoded.len());
        assert_eq!(d.server.hub().uid_pools(1).query, encoded.len());
        assert_eq!(d.server.hub().global_pools().query, encoded.len());

        d.server.finish(id);
        assert_clean(&d, "shared Query cap");
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

    #[test]
    fn observer_accept_requires_the_captured_cgroup_issuer() {
        let mut d = Driver::new();
        assert_eq!(
            d.server.accept(
                Level::Observer,
                Some(Peer {
                    uid: 7,
                    pid: 8,
                    cgroup_inc: None,
                }),
                0,
            ),
            Accept::Refused
        );
        let accepted = d.server.accept(
            Level::Observer,
            Some(Peer {
                uid: 7,
                pid: 8,
                cgroup_inc: Some(42),
            }),
            0,
        );
        let Accept::Accepted(id) = accepted else {
            panic!("observer with an issuer was refused");
        };
        d.server.finish(id);
    }

    // ---- P5b-2d: served verbs, tick delivery and retries ----

    #[test]
    fn server_hello_verbs_at_block_levels() {
        let mut d = Driver::new();
        for level in SERVED {
            let id = d.open(level, 1);
            let mut input = client_prefix(1);
            input.extend_from_slice(&req(1, "hello"));
            d.send(id, &input);
            d.pump(id, usize::MAX);
            let out = text(&d.take_out(id));
            let expected = if level.serves_blocks() {
                "\"verbs\":[\"hello\",\"status\",\"current\",\"subscribe\",\"unsubscribe\",\
                 \"snapshot_at\",\"series_batch\",\"procs\"]"
            } else {
                "\"verbs\":[\"hello\"]"
            };
            assert!(out.contains(expected), "{level:?}: {out}");
        }
        assert!(d.server.shutdown().is_empty());
        d.settle_all();
        assert_clean(&d, "hello verbs");
    }

    #[test]
    fn server_current_binary_sequence() {
        let mut d = Driver::new();
        for level in [Level::Observe, Level::Operator] {
            let id = d.open(level, 1);
            d.ready(id);
            put_current(&mut d, &selection(1), live_data(7, "one"));
            d.send(id, &request(2, "current", CURRENT_ARGS));
            d.pump(id, usize::MAX);
            let raw = raw_frames(&d.take_out(id));
            assert_eq!(raw.len(), 3);
            assert_eq!((raw[0].0, raw[0].1), (FrameType::Rep.code(), 2));
            let rep = check_rep(frame_payload(&raw[0].2), false).expect("current REP");
            assert!(rep.ok);
            assert_eq!((raw[1].0, raw[1].1), (FrameType::Block.code(), 2));
            let mut dicts = StreamDicts::new();
            let block = accept_block(&mut dicts, 2, &raw[1].2);
            assert!(block.dictionary.is_some());
            assert_eq!(block.header.seq, 7);
            assert_eq!((raw[2].0, raw[2].1), (FrameType::End.code(), 2));
            assert_end_ok(&raw[2].2, 1, 0, "complete");
            d.server.finish(id);
        }
        assert_clean(&d, "current sequence");
    }

    #[test]
    fn server_subscribe_binary_sequence() {
        let mut d = Driver::new();
        for level in [Level::Observe, Level::Operator] {
            let id = d.open(level, 1);
            d.ready(id);
            put_current(&mut d, &selection(1), live_data(7, "one"));
            wire_subscribe(&mut d, id, 2, 1, 1_000);
            d.server_tick();
            d.pump(id, usize::MAX);
            let raw = raw_frames(&d.take_out(id));
            assert_eq!(raw.len(), 1);
            assert_eq!((raw[0].0, raw[0].1), (FrameType::Block.code(), 2));
            let mut dicts = StreamDicts::new();
            let block = accept_block(&mut dicts, 2, &raw[0].2);
            assert!(block.dictionary.is_some());
            d.server.finish(id);
        }
        assert_clean(&d, "subscribe sequence");
    }

    #[test]
    fn server_unsubscribe_binary_sequence() {
        let mut d = Driver::new();
        for level in [Level::Observe, Level::Operator] {
            let id = d.open(level, 1);
            d.ready(id);
            wire_subscribe(&mut d, id, 2, 1, 1_000);
            d.send(id, &request(3, "unsubscribe", r#"{"sub":2}"#));
            d.pump(id, usize::MAX);
            let raw = raw_frames(&d.take_out(id));
            assert_eq!(raw.len(), 2);
            assert_eq!((raw[0].0, raw[0].1), (FrameType::Rep.code(), 3));
            assert!(
                check_rep(frame_payload(&raw[0].2), false)
                    .expect("unsubscribe REP")
                    .ok
            );
            assert_eq!((raw[1].0, raw[1].1), (FrameType::End.code(), 2));
            assert_end_ok(&raw[1].2, 0, 0, "unsubscribed");
            assert_eq!(d.server.streams().subscribers(), 0);
            d.server.finish(id);
        }
        assert_clean(&d, "unsubscribe sequence");
    }

    #[test]
    fn server_json_binary_required() {
        let mut d = Driver::new();
        let id = d.open(Level::Observe, 1);
        d.send(id, &line(1, "hello", HELLO_ARGS));
        d.pump(id, usize::MAX);
        d.take_out(id);
        d.send(id, &line(2, "current", CURRENT_ARGS));
        d.pump(id, usize::MAX);
        let out = d.take_out(id);
        let current_text = text(&out);
        assert!(current_text.contains("\"corr\":2"));
        assert!(current_text.contains("\"code\":\"binary-required\""));
        let subscribe_args = subscribe_args(1, 1_000);
        d.send(id, &line(3, "subscribe", subscribe_args.as_str()));
        d.pump(id, usize::MAX);
        let subscribe_text = text(&d.take_out(id));
        assert!(subscribe_text.contains("\"corr\":3"));
        assert!(subscribe_text.contains("\"code\":\"binary-required\""));
        assert_eq!(d.server.streams().subscribers(), 0);
        d.server.finish(id);
        assert_clean(&d, "JSON block refusal");
    }

    #[test]
    fn server_json_unsubscribe_not_found() {
        let mut d = Driver::new();
        let id = d.open(Level::Observe, 1);
        d.send(id, &line(1, "hello", HELLO_ARGS));
        d.pump(id, usize::MAX);
        d.take_out(id);
        d.send(id, &line(2, "unsubscribe", r#"{"sub":2}"#));
        d.pump(id, usize::MAX);
        let out = text(&d.take_out(id));
        assert!(out.contains("\"corr\":2"));
        assert!(out.contains("\"code\":\"not-found\""));
        d.server.finish(id);
        assert_clean(&d, "JSON unsubscribe");
    }

    #[test]
    fn server_on_tick_shared_bytes_have_distinct_stamped_prefixes() {
        let mut d = Driver::new();
        let a_conn = d.open(Level::Observe, 1);
        let b_conn = d.open(Level::Observe, 2);
        d.ready(a_conn);
        d.ready(b_conn);
        let selected = selection(1);
        put_current(&mut d, &selected, live_data(9, "one"));
        wire_subscribe(&mut d, a_conn, 2, 1, 1_000);
        wire_subscribe(&mut d, b_conn, 3, 1, 1_000);

        let stale = TickAt {
            generation: 2,
            bytes: block(200),
        };
        assert_eq!(
            d.server.hub.push_live(a_conn, 2, &stale, d.now),
            LiveOutcome::Queued
        );
        d.server_tick();

        let [a_prefix, a_tail] = match d.server.output(a_conn, d.now) {
            Some(output) => output,
            None => panic!("a has no shared tick"),
        };
        let a_parts = (a_prefix.to_vec(), a_tail.to_vec(), a_tail.as_ptr());
        let [b_prefix, b_tail] = match d.server.output(b_conn, d.now) {
            Some(output) => output,
            None => panic!("b has no shared tick"),
        };
        assert_eq!(stamped_fields(&a_parts.0), (2, 1));
        assert_eq!(stamped_fields(b_prefix), (3, 0));
        assert!(std::ptr::eq(b_tail.as_ptr(), a_parts.2));
        assert_eq!(b_tail, a_parts.1.as_slice());

        d.server.finish(a_conn);
        d.server.finish(b_conn);
        assert_clean(&d, "shared tick");
    }

    #[test]
    fn server_on_tick_joiner_at_generation_five_gets_dictionary_then_bare() {
        let mut d = Driver::new();
        let first = d.open(Level::Observe, 1);
        let joiner = d.open(Level::Observe, 2);
        d.ready(first);
        d.ready(joiner);
        let selected = selection(1);
        wire_subscribe(&mut d, first, 2, 1, 1_000);
        for seq in 1..=4 {
            let label = format!("generation-{seq}");
            put_current(&mut d, &selected, live_data(seq, &label));
            d.server_tick();
            d.pump(first, usize::MAX);
            d.take_out(first);
        }
        assert_eq!(
            d.server.conn(first).map(|conn| conn.subscriptions()),
            Some(1)
        );

        wire_subscribe(&mut d, joiner, 2, 1, 1_000);
        put_current(&mut d, &selected, live_data(5, "generation-5"));
        d.server_tick();
        d.pump(joiner, usize::MAX);
        let first_raw = raw_frames(&d.take_out(joiner));
        assert_eq!(first_raw.len(), 1);
        let mut dicts = StreamDicts::new();
        let dictionary = accept_block(&mut dicts, 2, &first_raw[0].2);
        assert_eq!(dictionary.header.dict_gen, 5);
        assert!(dictionary.dictionary.is_some());

        d.server_tick();
        d.pump(joiner, usize::MAX);
        let second_raw = raw_frames(&d.take_out(joiner));
        assert_eq!(second_raw.len(), 1);
        let bare = accept_block(&mut dicts, 2, &second_raw[0].2);
        assert_eq!(bare.header.dict_gen, 5);
        assert!(bare.dictionary.is_none());
        d.server.finish(first);
        d.server.finish(joiner);
        assert_clean(&d, "joiner generation five");
    }

    #[test]
    fn server_on_tick_interval_skips_a_generation_change() {
        let mut d = Driver::new();
        let fast = d.open(Level::Observe, 1);
        let slow = d.open(Level::Observe, 2);
        d.ready(fast);
        d.ready(slow);
        let selected = selection(1);
        wire_subscribe(&mut d, fast, 2, 1, 1_000);
        wire_subscribe(&mut d, slow, 3, 1, 2_000);
        let mut fast_dicts = StreamDicts::new();
        let mut slow_dicts = StreamDicts::new();

        put_current(&mut d, &selected, live_data(1, "one"));
        d.server_tick();
        d.pump(fast, usize::MAX);
        d.pump(slow, usize::MAX);
        let fast_first = raw_frames(&d.take_out(fast));
        let slow_first = raw_frames(&d.take_out(slow));
        assert_eq!(fast_first.len(), 1);
        assert_eq!(slow_first.len(), 1);
        let block = accept_block(&mut fast_dicts, 2, &fast_first[0].2);
        assert_eq!(block.header.dict_gen, 1);
        assert!(block.dictionary.is_some());
        let block = accept_block(&mut slow_dicts, 3, &slow_first[0].2);
        assert_eq!(block.header.dict_gen, 1);
        assert!(block.dictionary.is_some());

        put_current(&mut d, &selected, live_data(2, "two"));
        d.server_tick();
        d.pump(fast, usize::MAX);
        d.pump(slow, usize::MAX);
        let fast_changed = raw_frames(&d.take_out(fast));
        assert_eq!(fast_changed.len(), 1);
        let block = accept_block(&mut fast_dicts, 2, &fast_changed[0].2);
        assert_eq!(block.header.dict_gen, 2);
        assert!(block.dictionary.is_some());
        assert!(d.take_out(slow).is_empty());

        d.server_tick();
        d.pump(fast, usize::MAX);
        d.pump(slow, usize::MAX);
        let fast_bare = raw_frames(&d.take_out(fast));
        let slow_changed = raw_frames(&d.take_out(slow));
        assert_eq!(fast_bare.len(), 1);
        assert_eq!(slow_changed.len(), 1);
        let block = accept_block(&mut fast_dicts, 2, &fast_bare[0].2);
        assert_eq!(block.header.dict_gen, 2);
        assert!(block.dictionary.is_none());
        let block = accept_block(&mut slow_dicts, 3, &slow_changed[0].2);
        assert_eq!(block.header.dict_gen, 2);
        assert!(block.dictionary.is_some());
        d.server.finish(fast);
        d.server.finish(slow);
        assert_clean(&d, "interval generation");
    }

    #[test]
    fn server_on_tick_skips_a_closed_unfinished_subscriber() {
        let mut d = Driver::new();
        let id = d.open(Level::Observe, 1);
        d.ready(id);
        wire_subscribe(&mut d, id, 2, 1, 1_000);
        d.send(id, &req(0, "status"));
        assert_eq!(d.close_code(id), Some(Code::BadFrame));
        assert_eq!(d.server.conn(id).map(|conn| conn.subscriptions()), Some(1));
        let selected = selection(1);
        put_current(&mut d, &selected, live_data(3, "three"));
        d.server_tick();
        assert_eq!(d.server.hub().queued(id), Pools::default());
        assert_eq!(d.server.streams().subscribers(), 1);
        d.server.finish(id);
        assert_clean(&d, "closed subscriber skipped");
    }

    #[test]
    fn server_on_tick_ends_every_subscriber_of_a_failed_stream() {
        let mut d = Driver::new();
        let fast = d.open(Level::Observe, 1);
        let slow = d.open(Level::Observe, 2);
        d.ready(fast);
        d.ready(slow);
        let selected = selection(1);
        wire_subscribe(&mut d, fast, 2, 1, 1_000);
        wire_subscribe(&mut d, slow, 3, 1, 2_000);
        put_current(&mut d, &selected, live_data(1, "one"));
        d.server_tick();
        d.pump(fast, usize::MAX);
        d.pump(slow, usize::MAX);
        d.take_out(fast);
        d.take_out(slow);

        d.server
            .backend_mut()
            .current
            .insert(selected, Err(BackendError::TooManyRows));
        d.server_tick();
        d.pump(fast, usize::MAX);
        d.pump(slow, usize::MAX);
        for id in [fast, slow] {
            let raw = raw_frames(&d.take_out(id));
            assert_eq!(raw.len(), 1);
            assert_eq!(raw[0].0, FrameType::End.code());
            let (ok, blocks, _, _) = end_info(&raw[0].2);
            assert!(!ok);
            assert_eq!(blocks, 1);
            assert!(text(frame_payload(&raw[0].2)).contains("\"code\":\"budget\""));
            assert_eq!(d.server.conn(id).map(|conn| conn.subscriptions()), Some(0));
        }
        assert_eq!(d.server.streams().live_selections(), 0);
        d.server.finish(fast);
        d.server.finish(slow);
        assert_clean(&d, "failed tick");
    }

    #[test]
    fn server_retry_pass_after_wrote_frees_uid_query_pool() {
        let data = live_data(5, "one");
        let selected = selection(1);
        let need = stream_block_len(&ServerLimits::new(), &data);
        let mut d = Driver::with(query_bound_limits(need));
        let waiting = d.open(Level::Observe, 1);
        let blocker = d.open(Level::Observe, 1);
        d.ready(waiting);
        d.ready(blocker);
        put_current(&mut d, &selected, data);
        fill_query(&mut d, blocker, need);

        d.send(waiting, &request(2, "current", CURRENT_ARGS));
        d.pump(waiting, usize::MAX);
        d.take_out(waiting);
        assert_eq!(
            d.server.conn(waiting).map(|conn| conn.outstanding()),
            Some(1)
        );
        assert_eq!(d.server.hub().queued(waiting).query, 0);

        let written = match d.server.output(blocker, d.now) {
            Some([first, second]) => first.len().saturating_add(second.len()),
            None => panic!("blocker has no pinned Query block"),
        };
        assert_eq!(written, need);
        d.server.wrote(blocker, written, d.now);
        assert_eq!(
            d.server.conn(waiting).map(|conn| conn.outstanding()),
            Some(0)
        );
        assert_eq!(d.server.hub().queued(waiting).query, need);
        d.pump(waiting, usize::MAX);
        let raw = raw_frames(&d.take_out(waiting));
        assert_eq!(raw.len(), 2);
        assert_eq!(raw[0].0, FrameType::Block.code());
        assert_end_ok(&raw[1].2, 1, 0, "complete");
        d.server.finish(waiting);
        d.server.finish(blocker);
        assert_clean(&d, "retry after wrote");
    }

    #[test]
    fn server_retry_pass_at_poll_finishes_a_pending_current() {
        let data = live_data(5, "one");
        let selected = selection(1);
        let need = stream_block_len(&ServerLimits::new(), &data);
        let mut d = Driver::with(query_bound_limits(need));
        let waiting = d.open(Level::Observe, 1);
        let blocker = d.open(Level::Observe, 1);
        d.ready(waiting);
        d.ready(blocker);
        put_current(&mut d, &selected, data);
        fill_query(&mut d, blocker, need);
        d.send(waiting, &request(2, "current", CURRENT_ARGS));
        d.pump(waiting, usize::MAX);
        d.take_out(waiting);
        assert_eq!(
            d.server.conn(waiting).map(|conn| conn.outstanding()),
            Some(1)
        );

        d.server.finish(blocker);
        assert_eq!(
            d.server.conn(waiting).map(|conn| conn.outstanding()),
            Some(1)
        );
        d.tick(MS);
        assert_eq!(
            d.server.conn(waiting).map(|conn| conn.outstanding()),
            Some(0)
        );
        assert_eq!(d.server.hub().queued(waiting).query, need);
        d.pump(waiting, usize::MAX);
        let raw = raw_frames(&d.take_out(waiting));
        assert_eq!(raw.len(), 2);
        assert_eq!(raw[0].0, FrameType::Block.code());
        assert_end_ok(&raw[1].2, 1, 0, "complete");
        d.server.finish(waiting);
        assert_clean(&d, "retry at poll");
    }

    #[test]
    fn server_retry_pass_rotates_peer_uids_before_revisiting_a_busy_uid() {
        let data = live_data(5, "one");
        let selected = selection(1);
        let need = stream_block_len(&ServerLimits::new(), &data);
        let mut d = Driver::with(query_bound_limits(need.saturating_mul(2)));
        let first = d.open(Level::Observe, 1);
        let second = d.open(Level::Observe, 2);
        let blocker_a = d.open(Level::Observe, 1);
        let blocker_b = d.open(Level::Observe, 2);
        for id in [first, second, blocker_a, blocker_b] {
            d.ready(id);
        }
        put_current(&mut d, &selected, data);
        fill_query(&mut d, blocker_a, need);
        fill_query(&mut d, blocker_b, need);
        for (id, corr) in [(first, 2), (second, 3)] {
            d.send(id, &request(corr, "current", CURRENT_ARGS));
            d.pump(id, usize::MAX);
            d.take_out(id);
        }
        assert_eq!(d.server.conn(first).map(|conn| conn.outstanding()), Some(1));
        assert_eq!(
            d.server.conn(second).map(|conn| conn.outstanding()),
            Some(1)
        );

        d.server.finish(blocker_a);
        d.tick(MS);
        assert_eq!(d.server.conn(first).map(|conn| conn.outstanding()), Some(1));
        assert_eq!(
            d.server.conn(second).map(|conn| conn.outstanding()),
            Some(0)
        );
        assert_eq!(d.server.hub().queued(first).query, 0);
        assert_eq!(d.server.hub().queued(second).query, need);

        d.server.finish(blocker_b);
        d.tick(MS);
        assert_eq!(d.server.conn(first).map(|conn| conn.outstanding()), Some(0));
        assert_eq!(
            d.server.conn(second).map(|conn| conn.outstanding()),
            Some(0)
        );
        assert_eq!(d.server.hub().queued(first).query, need);
        d.server.finish(first);
        d.server.finish(second);
        assert_clean(&d, "uid rotation");
    }

    #[test]
    fn query_pass_rotates_uids_mixes_current_and_series_and_rechecks_latest_tick() {
        let cap = 700usize;
        let mut backend = FixedBackend::new();
        backend.status.tiers.push(TierInfo {
            name: "hot".to_string(),
            step_ms: 1_000,
            keep_s: 3_600,
            bytes: 0,
            cap_bytes: 0,
        });
        let mut d = Driver::with_backend(query_bound_limits(cap), backend);
        assert!(d.server.tiers().has_step(1_000));
        let selected = selection(1);
        let first_current = live_data(5, "v1");
        let current_frame = stream_block_len(d.server.limits(), &first_current);
        let dictionary = crate::query::series_dictionary(&series_view(), &series_request())
            .expect("series dictionary");
        let first_series_frame = crate::query::series_plan_frame_len(
            3,
            &dictionary.permitted,
            Aggregation::Last,
            Some(dictionary.body.len()),
        )
        .expect("first series BLOCK frame");
        let second_series_frame =
            crate::query::series_plan_frame_len(1, &dictionary.permitted, Aggregation::Mean, None)
                .expect("second series BLOCK frame");
        assert_eq!((first_series_frame, second_series_frame), (408, 176));
        let current_and_first = current_frame
            .checked_add(first_series_frame)
            .expect("current and first series frame size");
        let current_first_and_second = current_and_first
            .checked_add(second_series_frame)
            .expect("current and both series frame sizes");
        assert!(
            current_and_first <= cap,
            "current and first series BLOCK must fit the shared Query pool"
        );
        assert!(
            current_first_and_second > cap,
            "later series BLOCK must wait for shared Query room"
        );
        let validate_series_page = |raw: &[(u8, u32, Vec<u8>)], corr: u32| -> String {
            assert_eq!(raw.len(), 4);
            assert_eq!(
                (raw[0].0, raw[1].0, raw[2].0, raw[3].0),
                (
                    FrameType::Rep.code(),
                    FrameType::Block.code(),
                    FrameType::Block.code(),
                    FrameType::End.code(),
                )
            );
            assert_eq!(
                (raw[0].1, raw[1].1, raw[2].1, raw[3].1),
                (corr, corr, corr, corr)
            );
            assert!(
                check_rep(frame_payload(&raw[0].2), false)
                    .expect("series REP")
                    .ok
            );
            assert_eq!(
                (raw[1].2.len(), raw[2].2.len()),
                (first_series_frame, second_series_frame)
            );
            let mut dicts = StreamDicts::new();
            let first = accept_block(&mut dicts, corr, &raw[1].2);
            let second = decode_block_with(&raw[2].2, dicts.held(corr)).expect("bare series BLOCK");
            dicts
                .accept(corr, &second)
                .expect("bare series BLOCK accepts");
            assert_eq!(first.subject, Some(0));
            assert_eq!(first.header.n_rows, 3);
            assert_eq!(
                first
                    .time
                    .iter()
                    .map(|bucket| bucket.real_ns)
                    .collect::<Vec<_>>(),
                vec![0, 2_000_000_000, 3_000_000_000]
            );
            assert_eq!(first.columns.len(), 3);
            assert!(first.dictionary.is_some());
            assert_eq!(second.subject, Some(0));
            assert_eq!(second.header.n_rows, 1);
            assert_eq!(second.time[0].real_ns, 0);
            assert_eq!(second.columns.len(), 1);
            assert!(second.dictionary.is_none());
            assert_end_ok(&raw[3].2, 2, 0, "page");
            end_next(&raw[3].2).expect("series continuation")
        };
        put_current(&mut d, &selected, first_current);
        install_series(&mut d);

        let a_current = d.open(Level::Observe, 1);
        let a_series = d.open(Level::Observe, 1);
        let a_blocker = d.open(Level::Observe, 1);
        let b_current = d.open(Level::Observe, 2);
        let b_series = d.open(Level::Observe, 2);
        for id in [a_current, a_series, a_blocker, b_current, b_series] {
            d.ready(id);
        }
        fill_query(&mut d, a_blocker, cap);

        d.send(a_current, &request(2, "current", CURRENT_ARGS));
        d.send(a_series, &request(3, "series_batch", &series_args(None, 2)));
        d.send(b_current, &request(2, "current", CURRENT_ARGS));
        d.send(b_series, &request(3, "series_batch", &series_args(None, 2)));
        assert_eq!(
            d.server.conn(a_current).map(|conn| conn.outstanding()),
            Some(1)
        );
        assert_eq!(
            d.server.conn(a_series).map(|conn| conn.outstanding()),
            Some(1)
        );
        assert_eq!(d.server.hub().queued(a_current).query, 0);
        assert_eq!(d.server.hub().queued(a_series).query, 0);
        assert_eq!(d.server.hub().queued(b_current).query, current_frame);
        assert_eq!(d.server.hub().queued(b_series).query, first_series_frame);
        assert_eq!(
            d.server.conn(b_current).map(|conn| conn.outstanding()),
            Some(0)
        );
        assert_eq!(
            d.server.conn(b_series).map(|conn| conn.outstanding()),
            Some(1)
        );
        assert!(d
            .server
            .conn(b_series)
            .is_some_and(|conn| conn.has_pending_series_page(3)));

        put_current(&mut d, &selected, live_data(6, "v2"));
        d.eof(a_current);
        d.eof(a_series);
        assert_eq!(d.state(a_current), Some(State::Draining));
        assert_eq!(d.state(a_series), Some(State::Draining));
        assert!(!d.server.is_closed(a_current) && !d.server.is_closed(a_series));

        d.server.finish(a_blocker);
        d.tick(MS);
        assert_eq!(
            d.server.conn(a_current).map(|conn| conn.outstanding()),
            Some(0)
        );
        assert_eq!(
            d.server.conn(a_series).map(|conn| conn.outstanding()),
            Some(1)
        );
        assert_eq!(d.server.hub().queued(a_current).query, current_frame);
        assert_eq!(d.server.hub().queued(a_series).query, first_series_frame);
        assert!(d
            .server
            .conn(a_series)
            .is_some_and(|conn| conn.has_pending_series_page(3)));
        assert_eq!(d.server.hub().queued(b_series).query, first_series_frame);
        assert_eq!(d.server.hub().uid_pools(2).query, current_and_first);
        assert!(d
            .server
            .conn(b_series)
            .is_some_and(|conn| conn.has_pending_series_page(3)));

        d.pump(a_current, usize::MAX);
        let current_raw = raw_frames(&d.take_out(a_current));
        assert_eq!(current_raw.len(), 3);
        assert_eq!(
            (current_raw[0].0, current_raw[0].1),
            (FrameType::Rep.code(), 2)
        );
        assert!(
            check_rep(frame_payload(&current_raw[0].2), false)
                .expect("current REP")
                .ok
        );
        assert_eq!(
            (current_raw[1].0, current_raw[1].1),
            (FrameType::Block.code(), 2)
        );
        let mut dicts = StreamDicts::new();
        let current = accept_block(&mut dicts, 2, &current_raw[1].2);
        assert_eq!(current.header.seq, 6);
        assert!(matches!(
            &current.dictionary.as_ref().expect("dictionary").entries()[0],
            Entry::Entity { label, .. } if label == "v2"
        ));
        assert_eq!(
            (current_raw[2].0, current_raw[2].1),
            (FrameType::End.code(), 2)
        );
        assert_end_ok(&current_raw[2].2, 1, 0, "complete");

        assert_eq!(
            d.server.conn(a_series).map(|conn| conn.outstanding()),
            Some(0)
        );
        assert_eq!(
            d.server.hub().queued(a_series).query,
            first_series_frame + second_series_frame
        );
        d.pump(a_series, usize::MAX);
        let series_raw = raw_frames(&d.take_out(a_series));
        let a_token = validate_series_page(&series_raw, 3);
        d.close_out(a_current);
        d.close_out(a_series);

        d.pump(b_current, usize::MAX);
        let current_raw = raw_frames(&d.take_out(b_current));
        assert_eq!(current_raw.len(), 3);
        assert_eq!(
            (current_raw[0].0, current_raw[0].1),
            (FrameType::Rep.code(), 2)
        );
        assert!(
            check_rep(frame_payload(&current_raw[0].2), false)
                .expect("current REP")
                .ok
        );
        assert_eq!(
            (current_raw[1].0, current_raw[1].1),
            (FrameType::Block.code(), 2)
        );
        let mut dicts = StreamDicts::new();
        let current = accept_block(&mut dicts, 2, &current_raw[1].2);
        assert!(current.dictionary.is_some());
        assert_eq!(
            (current_raw[2].0, current_raw[2].1),
            (FrameType::End.code(), 2)
        );
        assert_end_ok(&current_raw[2].2, 1, 0, "complete");
        assert_eq!(
            d.server.conn(b_series).map(|conn| conn.outstanding()),
            Some(0)
        );
        assert_eq!(
            d.server.hub().queued(b_series).query,
            first_series_frame + second_series_frame
        );
        d.pump(b_series, usize::MAX);
        let series_raw = raw_frames(&d.take_out(b_series));
        let b_token = validate_series_page(&series_raw, 3);
        assert_ne!(a_token, b_token);
        let view = QueryViewId::Series(SeriesViewId(11));
        assert_eq!(d.server.view_references(view), 2);
        assert_eq!(d.server.cursor_count(), 2);
        clear_cursor_slots(&mut d);
        assert_eq!(d.server.view_references(view), 0);
        assert_eq!(d.server.backend().released.borrow().as_slice(), &[view]);
        d.server.finish(b_current);
        d.server.finish(b_series);
        assert_clean(&d, "mixed fair query pass");
    }

    #[test]
    fn server_finish_releases_selections() {
        for path in ["finish", "shutdown", "fatal", "draining"] {
            let mut d = Driver::new();
            let id = d.open(Level::Observe, 1);
            d.ready(id);
            wire_subscribe(&mut d, id, 2, 1, 1_000);
            match path {
                "finish" => d.server.finish(id),
                "shutdown" => {
                    d.server.shutdown();
                }
                "fatal" => {
                    d.send(id, &req(0, "status"));
                    assert_eq!(d.close_code(id), Some(Code::BadFrame));
                }
                _ => {
                    d.eof(id);
                    d.pump(id, usize::MAX);
                }
            }
            d.settle_all();
            assert_eq!(d.server.streams().live_selections(), 0, "{path}");
            assert_eq!(d.server.streams().subscribers(), 0, "{path}");
            assert_eq!(d.server.streams().uid_selections(1), 0, "{path}");
            assert_clean(&d, path);
        }
    }

    #[test]
    fn conn_not_idle_with_subscription() {
        let mut d = Driver::new();
        let id = d.open(Level::Observe, 1);
        d.ready(id);
        wire_subscribe(&mut d, id, 2, 1, 1_000);
        let idle_ms = d
            .server
            .limits()
            .get(Level::Observe)
            .expect("observe")
            .idle_ms;
        d.tick(u64::from(idle_ms).saturating_mul(MS).saturating_add(1));
        assert!(!d.server.is_closed(id));
        let selected = selection(1);
        put_current(&mut d, &selected, live_data(1, "one"));
        d.server_tick();
        d.pump(id, usize::MAX);
        d.take_out(id);
        d.tick(u64::from(idle_ms).saturating_mul(MS).saturating_add(1));
        assert!(!d.server.is_closed(id));
        assert_eq!(d.state(id), Some(State::Ready));
        d.server.finish(id);
        assert_clean(&d, "subscription is not idle");
    }

    #[test]
    fn conn_dribbling_reader() {
        let mut d = Driver::new();
        let slow = d.open(Level::Observe, 1);
        let healthy = d.open(Level::Observe, 2);
        d.ready(slow);
        d.ready(healthy);
        let selected = selection(1);
        put_current(&mut d, &selected, live_data(1, "one"));
        wire_subscribe(&mut d, slow, 2, 1, 1_000);
        wire_subscribe(&mut d, healthy, 3, 1, 1_000);

        d.server_tick();
        let _ = match d.server.output(slow, d.now) {
            Some(output) => output,
            None => panic!("slow subscriber has no pinned head"),
        };
        d.pump(healthy, usize::MAX);
        let mut healthy_blocks = raw_frames(&d.take_out(healthy))
            .into_iter()
            .filter(|(kind, corr, _)| *kind == FrameType::Block.code() && *corr == 3)
            .count();
        assert_eq!(healthy_blocks, 1);

        for _ in 0..2 {
            d.tick(2_000 * MS);
            d.server_tick();
            d.pump(healthy, usize::MAX);
            healthy_blocks += raw_frames(&d.take_out(healthy))
                .into_iter()
                .filter(|(kind, corr, _)| *kind == FrameType::Block.code() && *corr == 3)
                .count();
        }
        d.server.wrote(slow, 1, d.now);
        let completion_ms = d
            .server
            .limits()
            .get(Level::Observe)
            .expect("observe")
            .frame_completion_ms;
        let close_at = u64::from(completion_ms)
            .saturating_mul(MS)
            .saturating_add(1);
        assert!(d
            .server
            .hub()
            .poll(close_at)
            .iter()
            .any(|(id, close)| *id == slow && *close == Close::SlowClient));
        let finished = d.server.poll(close_at);
        d.now = close_at;
        assert!(finished.contains(&slow));
        assert!(d.server.is_closed(slow));
        assert!(!d.server.is_closed(healthy));
        assert_eq!(healthy_blocks, 3);
        d.server.shutdown();
        d.settle_all();
        assert_clean(&d, "dribbling reader");
    }

    #[test]
    fn conn_half_close() {
        let mut d = Driver::new();
        let id = d.open(Level::Observe, 1);
        d.ready(id);
        let selected = selection(1);
        put_current(&mut d, &selected, live_data(1, "one"));
        wire_subscribe(&mut d, id, 2, 1, 1_000);
        for _ in 0..2 {
            d.server_tick();
            d.pump(id, usize::MAX);
            d.take_out(id);
        }
        assert_eq!(d.server.conn(id).map(|conn| conn.subscriptions()), Some(1));
        d.eof(id);
        assert_eq!(d.state(id), Some(State::Draining));
        d.pump(id, usize::MAX);
        let raw = raw_frames(&d.take_out(id));
        assert_eq!(raw.len(), 1);
        assert_eq!((raw[0].0, raw[0].1), (FrameType::End.code(), 2));
        assert_end_ok(&raw[0].2, 2, 0, "client-eof");
        assert!(d.server.is_closed(id));
        d.tick(MS);
        assert_clean(&d, "half close");
    }

    #[test]
    fn conn_corr_in_use() {
        let mut subscription = Driver::new();
        let id = subscription.open(Level::Observe, 1);
        subscription.ready(id);
        wire_subscribe(&mut subscription, id, 2, 1, 1_000);
        subscription.send(id, &control_frame(FrameType::Req, 2, b"{"));
        assert_eq!(subscription.close_code(id), Some(Code::BadFrame));
        assert!(subscription.server.is_closed(id));
        subscription.settle_all();
        assert_clean(&subscription, "subscription corr in use");

        let data = live_data(5, "one");
        let selected = selection(1);
        let need = stream_block_len(&ServerLimits::new(), &data);
        let mut pending = Driver::with(query_bound_limits(need));
        let waiting = pending.open(Level::Observe, 1);
        let blocker = pending.open(Level::Observe, 1);
        pending.ready(waiting);
        pending.ready(blocker);
        put_current(&mut pending, &selected, data);
        fill_query(&mut pending, blocker, need);
        pending.send(waiting, &request(2, "current", CURRENT_ARGS));
        pending.pump(waiting, usize::MAX);
        pending.take_out(waiting);
        assert_eq!(
            pending.server.conn(waiting).map(|conn| conn.outstanding()),
            Some(1)
        );
        pending.send(waiting, &control_frame(FrameType::Req, 2, b"{"));
        assert_eq!(pending.close_code(waiting), Some(Code::BadFrame));
        assert!(pending.server.is_closed(waiting));
        pending.server.finish(blocker);
        pending.settle_all();
        assert_clean(&pending, "pending corr in use");
    }

    #[test]
    fn conn_too_many_requests() {
        let data = live_data(5, "one");
        let selected = selection(1);
        let need = stream_block_len(&ServerLimits::new(), &data);
        let mut d = Driver::with(query_bound_limits(need));
        let id = d.open(Level::Observe, 1);
        let blocker = d.open(Level::Observe, 1);
        d.ready(id);
        d.ready(blocker);
        put_current(&mut d, &selected, data);
        fill_query(&mut d, blocker, need);
        for corr in 2..6 {
            d.send(id, &request(corr, "current", CURRENT_ARGS));
        }
        d.send(id, &req(6, "status"));
        d.send(id, &request(7, "current", CURRENT_ARGS));
        d.pump(id, usize::MAX);
        let raw = raw_frames(&d.take_out(id));
        assert_eq!(d.server.conn(id).map(|conn| conn.outstanding()), Some(4));
        let refused = raw
            .iter()
            .find(|(kind, corr, _)| *kind == FrameType::Rep.code() && *corr == 7)
            .expect("too-many reply");
        let body = text(frame_payload(&refused.2));
        assert!(body.contains("\"code\":\"too-many-requests\""));
        let status = raw
            .iter()
            .find(|(kind, corr, _)| *kind == FrameType::Rep.code() && *corr == 6)
            .expect("status reply");
        assert!(
            check_rep(frame_payload(&status.2), false)
                .expect("status")
                .ok
        );
        assert_eq!(d.state(id), Some(State::Ready));
        d.server.finish(blocker);
        d.server.finish(id);
        assert_clean(&d, "too many requests");
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
                    let mut peer = Some(Peer {
                        uid,
                        pid: 7,
                        cgroup_inc: (level == Level::Observer).then_some(7),
                    });
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

    fn query_limits() -> ServerLimits {
        let mut limits = ServerLimits::new();
        for level in [&mut limits.observe, &mut limits.operator] {
            level.query_queue_bytes = 1_000;
            level.client_queue_bytes = 1_000;
            level.max_block_bytes = 328;
            level.max_page_blocks = 64;
        }
        limits
    }

    fn query_metric(name: &str, kind: MetricKind) -> QueryColumnMeta {
        QueryColumnMeta {
            metric: name.to_string(),
            facts: MetricFacts {
                unit: MetricUnit::None,
                kind,
                scope: MetricScope::Cgroup,
                source: MetricSource::CgroupFs,
            },
            value_type: MetricValueType::U64,
        }
    }

    fn series_request() -> SeriesRequest {
        SeriesRequest {
            selection: Selection::new(
                vec![Sel::Inc(7), Sel::Inc(9)],
                vec!["m_a".to_string(), "m_b".to_string(), "m_never".to_string()],
            )
            .expect("selection"),
            from_real_ns: 0,
            to_real_ns: 4_000_000_000,
            step_ms: 1_000,
            aggs: vec![Aggregation::Last, Aggregation::Mean],
        }
    }

    fn series_view() -> SeriesView {
        SeriesView {
            id: SeriesViewId(11),
            tier: crate::query::Tier::Hot,
            step_ms: 1_000,
            subjects: vec![7, 9],
            columns: vec![
                query_metric("m_a", MetricKind::Gauge),
                query_metric("m_b", MetricKind::State),
                query_metric("m_never", MetricKind::Counter),
            ],
            first: Some(SeriesPosition(0)),
        }
    }

    fn series_groups() -> Vec<SeriesGroup> {
        vec![
            SeriesGroup {
                subject_inc: 7,
                agg: Aggregation::Last,
                first: SeriesPosition(0),
                bucket_count: 3,
            },
            SeriesGroup {
                subject_inc: 7,
                agg: Aggregation::Mean,
                first: SeriesPosition(3),
                bucket_count: 1,
            },
            SeriesGroup {
                subject_inc: 9,
                agg: Aggregation::Last,
                first: SeriesPosition(4),
                bucket_count: 1,
            },
            SeriesGroup {
                subject_inc: 9,
                agg: Aggregation::Mean,
                first: SeriesPosition(5),
                bucket_count: 1,
            },
        ]
    }

    fn history_column(meta: &QueryColumnMeta, values: Vec<u64>) -> HistoryColumn {
        HistoryColumn {
            metric: meta.metric.clone(),
            facts: meta.facts,
            value_type: meta.value_type,
            quality: vec![0; values.len()],
            values,
        }
    }

    fn history_data(
        view: &SeriesView,
        subject_inc: u64,
        agg: Aggregation,
        times: &[i64],
    ) -> HistoryBlockData {
        let permitted: Vec<&QueryColumnMeta> = view
            .columns
            .iter()
            .filter(|column| column.facts.aggregation_mask() & agg.bit() != 0)
            .collect();
        let columns = permitted
            .into_iter()
            .enumerate()
            .map(|(index, meta)| {
                history_column(
                    meta,
                    (0..times.len())
                        .map(|row| u64::try_from(row + index).expect("small"))
                        .collect(),
                )
            })
            .collect();
        HistoryBlockData {
            subject_inc,
            agg,
            buckets: times
                .iter()
                .map(|time| HistoryBucket {
                    bucket_real_ns: *time,
                    covered_ms: 1_000,
                    samples: 1,
                })
                .collect(),
            columns,
        }
    }

    fn install_series(driver: &mut Driver) {
        let source_request = series_request();
        let view = series_view();
        let backend = driver.server.backend_mut();
        backend.series_open.insert(source_request, Ok(view.clone()));
        for at in 0..=5u64 {
            let group = series_groups()
                .into_iter()
                .find(|group| group.first.0 <= at && at < group.first.0 + group.bucket_count)
                .expect("group");
            backend
                .series_group
                .insert((view.id, SeriesPosition(at)), Ok(Some(group)));
        }
        backend
            .series_group
            .insert((view.id, SeriesPosition(6)), Ok(None));
        backend.series_read.insert(
            (view.id, SeriesPosition(0), 1),
            Ok(history_data(&view, 7, Aggregation::Last, &[0])),
        );
        backend.series_read.insert(
            (view.id, SeriesPosition(0), 3),
            Ok(history_data(
                &view,
                7,
                Aggregation::Last,
                &[0, 2_000_000_000, 3_000_000_000],
            )),
        );
        backend.series_read.insert(
            (view.id, SeriesPosition(1), 2),
            Ok(history_data(
                &view,
                7,
                Aggregation::Last,
                &[2_000_000_000, 3_000_000_000],
            )),
        );
        backend.series_read.insert(
            (view.id, SeriesPosition(3), 1),
            Ok(history_data(&view, 7, Aggregation::Mean, &[0])),
        );
        backend.series_read.insert(
            (view.id, SeriesPosition(4), 1),
            Ok(history_data(&view, 9, Aggregation::Last, &[1_000_000_000])),
        );
        backend.series_read.insert(
            (view.id, SeriesPosition(5), 1),
            Ok(history_data(&view, 9, Aggregation::Mean, &[1_000_000_000])),
        );
    }

    fn series_args(cursor: Option<&str>, max_blocks: u32) -> String {
        let cursor = match cursor {
            Some(cursor) => format!("\"{cursor}\""),
            None => "null".to_string(),
        };
        format!(
            "{{\"selection\":{{\"entities\":[{{\"inc\":7}},{{\"inc\":9}}],\
             \"metrics\":[\"m_a\",\"m_b\",\"m_never\"]}},\
             \"from_real_ns\":0,\"to_real_ns\":4000000000,\"step_ms\":1000,\
             \"aggs\":[\"last\",\"mean\"],\"cursor\":{cursor},\"max_blocks\":{max_blocks}}}"
        )
    }

    fn fill_cursor_slots(d: &mut Driver, used: usize) {
        let identity = d.server.backend().identity();
        for index in 0..MAX_CURSOR_ENTRIES.saturating_sub(used) {
            let view = Arc::new(SeriesView {
                id: SeriesViewId(10_000 + u64::try_from(index).expect("index")),
                ..series_view()
            });
            let binding = CursorBinding {
                boot_id: identity.boot_id.clone(),
                epoch: identity.epoch,
                level: Level::Observe,
                uid: 1,
                verb: "series_batch",
                arguments: vec![u8::try_from(index % 251).expect("small")],
            };
            d.server
                .cursors
                .reserve(
                    &binding,
                    CursorTarget::Series {
                        view,
                        position: SeriesPosition(u64::try_from(index).expect("index")),
                    },
                    None,
                    d.now,
                )
                .expect("filler cursor slot");
        }
        assert_eq!(d.server.cursor_count(), MAX_CURSOR_ENTRIES);
    }

    fn clear_cursor_slots(d: &mut Driver) {
        d.now = d.now.saturating_add(CURSOR_LIFETIME_NS);
        let released = d.server.cursors.expire(d.now);
        for view in released {
            d.server.backend.release_view(view);
        }
        assert_eq!(d.server.cursor_count(), 0);
    }

    #[test]
    fn server_series_uses_the_frozen_largest_prefix_and_replays_a_pinned_view() {
        let mut backend = FixedBackend::new();
        backend.status.tiers.push(TierInfo {
            name: "hot".to_string(),
            step_ms: 1_000,
            keep_s: 3_600,
            bytes: 0,
            cap_bytes: 0,
        });
        let mut d = Driver {
            server: Server::new(query_limits(), backend),
            now: 0,
            out: BTreeMap::new(),
            opened: Vec::new(),
            uids: Vec::new(),
        };
        install_series(&mut d);
        let id = d.open(Level::Observe, 1);
        d.ready(id);
        d.send(id, &request(3, "series_batch", &series_args(None, 2)));
        d.pump(id, usize::MAX);
        let out = d.take_out(id);
        let raw = raw_frames(&out);
        assert_eq!(raw.len(), 4);
        assert_eq!(raw[0].0, FrameType::Rep.code());
        assert_eq!(raw[1].0, FrameType::Block.code());
        assert_eq!(raw[2].0, FrameType::Block.code());
        assert_eq!(raw[3].0, FrameType::End.code());
        assert_eq!((raw[1].2.len(), raw[2].2.len()), (328, 280));
        let token = end_next(&raw[3].2).expect("page continuation");

        let mut dicts = StreamDicts::new();
        let first = accept_block(&mut dicts, 3, &raw[1].2);
        let second = decode_block_with(&raw[2].2, dicts.held(3)).expect("bare continuation");
        dicts.accept(3, &second).expect("bare accepts");
        assert_eq!(first.subject, Some(0));
        assert_eq!(first.header.n_rows, 1);
        assert_eq!(first.time[0].real_ns, 0);
        assert_eq!(second.subject, Some(0));
        assert_eq!(second.header.n_rows, 2);
        assert_eq!(second.time[0].real_ns, 2_000_000_000);
        assert_eq!(first.columns.len(), 3);
        assert_eq!(second.columns.len(), 3);

        let view = crate::query::QueryViewId::Series(SeriesViewId(11));
        assert_eq!(d.server.view_references(view), 1);
        d.eof(id);
        d.close_out(id);
        assert_eq!(d.server.view_references(view), 1);

        let replay = d.open(Level::Observe, 1);
        d.ready(replay);
        d.send(
            replay,
            &request(4, "series_batch", &series_args(Some(&token), 1)),
        );
        d.pump(replay, usize::MAX);
        let out = d.take_out(replay);
        let raw = raw_frames(&out);
        assert_eq!(raw.len(), 3);
        assert_eq!((raw[1].2.len(), end_next(&raw[2].2).is_some()), (264, true));
        let first = accept_block(&mut dicts, 4, &raw[1].2);
        assert_eq!(first.subject, Some(0));
        assert_eq!(first.time[0].real_ns, 0);
        assert_eq!(first.columns.len(), 1);

        let replay_two = d.open(Level::Observe, 2);
        d.ready(replay_two);
        d.send(
            replay_two,
            &request(5, "series_batch", &series_args(Some(&token), 2)),
        );
        d.pump(replay_two, usize::MAX);
        let out = d.take_out(replay_two);
        let raw = raw_frames(&out);
        assert_eq!(raw.len(), 4);
        assert_eq!((raw[1].2.len(), raw[2].2.len()), (264, 240));
        assert_eq!(end_next(&raw[3].2), Some("c0000000000000003".to_string()));
    }

    #[test]
    fn series_terminal_page_succeeds_at_cursor_capacity_while_continuation_refuses() {
        let mut backend = FixedBackend::new();
        backend.status.tiers.push(TierInfo {
            name: "hot".to_string(),
            step_ms: 1_000,
            keep_s: 3_600,
            bytes: 0,
            cap_bytes: 0,
        });
        let mut d = Driver {
            server: Server::new(query_limits(), backend),
            now: 0,
            out: BTreeMap::new(),
            opened: Vec::new(),
            uids: Vec::new(),
        };
        install_series(&mut d);
        let id = d.open(Level::Observe, 1);
        d.ready(id);
        d.send(id, &request(3, "series_batch", &series_args(None, 1)));
        d.pump(id, usize::MAX);
        let raw = raw_frames(&d.take_out(id));
        let token = end_next(&raw[2].2).expect("first page token");
        assert_eq!(d.server.cursor_count(), 1);
        fill_cursor_slots(&mut d, 1);

        d.send(
            id,
            &request(4, "series_batch", &series_args(Some(&token), 64)),
        );
        d.pump(id, usize::MAX);
        let raw = raw_frames(&d.take_out(id));
        assert_eq!(raw[0].0, FrameType::Rep.code());
        assert_eq!(raw.last().map(|frame| frame.0), Some(FrameType::End.code()));
        assert_end_ok(
            &raw.last().expect("end").2,
            u32::try_from(raw.len() - 2).expect("blocks"),
            0,
            "complete",
        );

        d.send(
            id,
            &request(5, "series_batch", &series_args(Some(&token), 1)),
        );
        d.pump(id, usize::MAX);
        let raw = raw_frames(&d.take_out(id));
        assert_eq!(raw.len(), 1);
        assert!(text(&raw[0].2)
            .contains("\"code\":\"budget\",\"message\":\"a budget is exhausted\",\"detail\":{\"pool\":\"cursors\"}"));

        clear_cursor_slots(&mut d);
        d.server.finish(id);
        assert_clean(&d, "full cursor table");
    }

    fn snapshot_request() -> SnapshotRequest {
        SnapshotRequest {
            selection: Selection::new(vec![Sel::Inc(7)], vec!["cpu".to_string()])
                .expect("selection"),
            real_ns: -1,
            tier: TierChoice::Explicit(crate::query::Tier::Hot),
            provenance: false,
        }
    }

    fn snapshot_args(provenance: bool) -> String {
        format!(
            "{{\"selection\":{{\"entities\":[{{\"inc\":7}}],\"metrics\":[\"cpu\"]}},\
             \"real_ns\":-1,\"tier\":\"hot\",\"provenance\":{provenance}}}"
        )
    }

    fn snapshot_meta() -> SnapshotMeta {
        SnapshotMeta {
            tier: crate::query::Tier::Hot,
            bucket_real_ns: -1_000_000_000,
            step_ms: 1_000,
            covered_ms: 1_000,
            subjects: vec![SnapshotSubjectMeta {
                inc: 7,
                label_len: 2,
            }],
            columns: vec![query_metric("cpu", MetricKind::Gauge)],
        }
    }

    fn snapshot_candidate(meta: &SnapshotMeta) -> SnapshotData {
        let mut data = TickData::example(1, &[7], &["cpu"]);
        data.header.real_ns = meta.bucket_real_ns;
        data.entries[0] = Entry::Entity {
            inc: 7,
            label: "é".to_string(),
        };
        data.entries[1] = Entry::Metric {
            facts: meta.columns[0].facts,
            name: "cpu".to_string(),
        };
        data.columns[0].metric = 1;
        data.columns[0].kind = MetricKind::Gauge;
        data.columns[0].value_type = MetricValueType::U64;
        data.columns[0].values = vec![41];
        SnapshotData {
            tick: data,
            provenance: None,
        }
    }

    #[test]
    fn server_snapshot_waits_for_exact_room_and_pins_multibyte_source_labels() {
        let mut backend = FixedBackend::new();
        backend.status.tiers.push(TierInfo {
            name: "hot".to_string(),
            step_ms: 1_000,
            keep_s: 3_600,
            bytes: 0,
            cap_bytes: 0,
        });
        let source_request = snapshot_request();
        let meta = snapshot_meta();
        let view = SnapshotView {
            id: SnapshotViewId(21),
            meta: meta.clone(),
        };
        backend
            .snapshot_open
            .insert(source_request.clone(), Ok(view.clone()));
        backend
            .snapshot_read
            .insert(view.id, Ok(snapshot_candidate(&meta)));
        let mut d = Driver {
            server: Server::new(query_limits(), backend),
            now: 0,
            out: BTreeMap::new(),
            opened: Vec::new(),
            uids: Vec::new(),
        };
        let id = d.open(Level::Observe, 1);
        d.ready(id);
        let snapshot_frame =
            crate::query::snapshot_frame_len(&meta).expect("snapshot frame length");
        let snapshot_fill = fill_query_short_of_frame(&mut d, id, snapshot_frame);
        d.send(id, &request(3, "snapshot_at", &snapshot_args(false)));
        assert!(d.take_out(id).is_empty());
        assert_eq!(d.server.conn(id).map(|conn| conn.outstanding()), Some(1));
        assert_eq!(d.server.hub().queued(id).query, snapshot_fill);
        let released = d.server.backend().released.borrow().len();
        assert_eq!(released, 0);
        let mut newer = snapshot_candidate(&meta);
        newer.tick.entries[0] = Entry::Entity {
            inc: 7,
            label: "à".to_string(),
        };
        d.server
            .backend_mut()
            .current
            .insert(source_request.selection.clone(), Ok(newer.tick));

        // Drain exactly the blocker; this frees query room and starts the admitted read.
        assert_eq!(d.pump(id, snapshot_fill), snapshot_fill);
        d.take_out(id);
        d.pump(id, usize::MAX);
        let out = d.take_out(id);
        let raw = raw_frames(&out);
        assert_eq!(raw.len(), 3);
        assert_eq!(
            (raw[0].0, raw[1].0, raw[2].0),
            (
                FrameType::Rep.code(),
                FrameType::Block.code(),
                FrameType::End.code(),
            )
        );
        assert!(text(&raw[0].2).contains(
            "\"resolved\":{\"tier\":\"hot\",\"bucket_real_ns\":-1000000000,\"step_ms\":1000,\"covered_ms\":1000},\"provenance\":null"
        ));
        assert_eq!(raw[1].2.len(), snapshot_frame);
        let mut dicts = StreamDicts::new();
        let block = accept_block(&mut dicts, 3, &raw[1].2);
        assert_eq!(block.header.real_ns, -1_000_000_000);
        assert_eq!(block.rows.len(), 1);
        let dictionary = block.dictionary.as_ref().expect("snapshot dictionary");
        assert_eq!(dictionary.entries().len(), 2);
        assert!(matches!(
            &dictionary.entries()[0],
            Entry::Entity {
                inc: 7,
                label
            } if label == "é"
        ));
        assert_eq!(block.columns[0].values, vec![41]);
        assert_end_ok(&raw[2].2, 1, 0, "complete");
        assert_eq!(
            d.server
                .view_references(QueryViewId::Snapshot(SnapshotViewId(21))),
            0
        );
        assert_eq!(
            d.server.backend().released.borrow().as_slice(),
            &[QueryViewId::Snapshot(SnapshotViewId(21))]
        );
    }

    #[test]
    fn snapshot_provenance_budget_wins_before_block_sizing_and_value_reads() {
        let mut backend = FixedBackend::new();
        backend.status.tiers.push(TierInfo {
            name: "hot".to_string(),
            step_ms: 1_000,
            keep_s: 3_600,
            bytes: 0,
            cap_bytes: 0,
        });
        let metrics: Vec<String> = (0..13).map(|index| format!("m{index}")).collect();
        let selection = Selection::new(vec![Sel::Inc(1)], metrics.clone()).expect("selection");
        let source_request = SnapshotRequest {
            selection,
            real_ns: 0,
            tier: TierChoice::Explicit(crate::query::Tier::Hot),
            provenance: true,
        };
        let columns: Vec<QueryColumnMeta> = source_request
            .selection
            .metrics()
            .iter()
            .map(|metric| query_metric(metric, MetricKind::Gauge))
            .collect();
        let meta = SnapshotMeta {
            tier: crate::query::Tier::Hot,
            bucket_real_ns: 0,
            step_ms: 1_000,
            covered_ms: 1_000,
            subjects: (1..=5)
                .map(|inc| SnapshotSubjectMeta { inc, label_len: 0 })
                .collect(),
            columns,
        };
        let view = SnapshotView {
            id: SnapshotViewId(22),
            meta,
        };
        backend.snapshot_open.insert(source_request, Ok(view));
        let mut d = Driver {
            server: Server::new(query_limits(), backend),
            now: 0,
            out: BTreeMap::new(),
            opened: Vec::new(),
            uids: Vec::new(),
        };
        let id = d.open(Level::Observe, 1);
        d.ready(id);
        let args = format!(
            "{{\"selection\":{{\"entities\":[{{\"inc\":1}}],\"metrics\":{}}},\
             \"real_ns\":0,\"tier\":\"hot\",\"provenance\":true}}",
            serde_json::to_string(&metrics).expect("metrics")
        );
        d.send(id, &request(3, "snapshot_at", &args));
        d.pump(id, usize::MAX);
        let raw = raw_frames(&d.take_out(id));
        assert_eq!(raw.len(), 1);
        assert_eq!(raw[0].0, FrameType::Rep.code());
        assert!(text(&raw[0].2).contains(
            "\"code\":\"budget\",\"message\":\"a budget is exhausted\",\"detail\":{\"pool\":\"provenance\"}"
        ));
        assert_eq!(
            d.server.backend().released.borrow().as_slice(),
            &[QueryViewId::Snapshot(SnapshotViewId(22))]
        );
    }

    #[test]
    fn snapshot_bucket_overflow_precedes_unknown_subject_resolution() {
        let mut backend = FixedBackend::new();
        backend.status.tiers.push(TierInfo {
            name: "hot".to_string(),
            step_ms: 1_000,
            keep_s: 3_600,
            bytes: 0,
            cap_bytes: 0,
        });
        let source_request = SnapshotRequest {
            selection: Selection::new(vec![Sel::Inc(u64::MAX)], vec!["cpu".to_string()])
                .expect("selection"),
            real_ns: i64::MIN,
            tier: TierChoice::Explicit(crate::query::Tier::Hot),
            provenance: false,
        };
        backend
            .snapshot_open
            .insert(source_request, Err(QuerySourceError::BucketOutOfRange));
        let mut d = Driver {
            server: Server::new(query_limits(), backend),
            now: 0,
            out: BTreeMap::new(),
            opened: Vec::new(),
            uids: Vec::new(),
        };
        let id = d.open(Level::Observe, 1);
        d.ready(id);
        d.send(
            id,
            &request(
                3,
                "snapshot_at",
                "{\"selection\":{\"entities\":[{\"inc\":18446744073709551615}],\
                 \"metrics\":[\"cpu\"]},\"real_ns\":-9223372036854775808,\
                 \"tier\":\"hot\",\"provenance\":false}",
            ),
        );
        d.pump(id, usize::MAX);
        let raw = raw_frames(&d.take_out(id));
        assert_eq!(raw.len(), 1);
        assert!(text(&raw[0].2).contains(
            "\"code\":\"bad-request\",\"message\":\"the request is malformed\",\"detail\":{\"key\":\"real_ns\"}"
        ));
    }

    #[test]
    fn catalog_refusals_precede_query_backend_resolution() {
        let mut d = Driver::with(query_limits());
        let id = d.open(Level::Observe, 1);
        d.ready(id);
        d.send(
            id,
            &request(
                3,
                "series_batch",
                "{\"selection\":{\"entities\":[{\"inc\":7}],\"metrics\":[\"m\"]},\
                 \"from_real_ns\":0,\"to_real_ns\":1000000000,\"step_ms\":2000,\
                 \"aggs\":[\"last\"],\"cursor\":null,\"max_blocks\":1}",
            ),
        );
        d.pump(id, usize::MAX);
        let raw = raw_frames(&d.take_out(id));
        assert_eq!(raw.len(), 1);
        assert!(text(&raw[0].2).contains(
            "\"code\":\"bad-request\",\"message\":\"the request is malformed\",\"detail\":{\"key\":\"step_ms\"}"
        ));

        d.send(id, &request(4, "snapshot_at", &snapshot_args(false)));
        d.pump(id, usize::MAX);
        let raw = raw_frames(&d.take_out(id));
        assert_eq!(raw.len(), 1);
        assert!(text(&raw[0].2).contains("\"code\":\"not-found\""));
        d.server.finish(id);
        assert_clean(&d, "catalog refusal");
    }

    #[test]
    fn snapshot_present_zero_row_bucket_serves_an_empty_dictionary_block() {
        let mut backend = FixedBackend::new();
        backend.status.tiers.push(TierInfo {
            name: "hot".to_string(),
            step_ms: 1_000,
            keep_s: 3_600,
            bytes: 0,
            cap_bytes: 0,
        });
        let source_request = snapshot_request();
        let meta = SnapshotMeta {
            tier: Tier::Hot,
            bucket_real_ns: -1_000_000_000,
            step_ms: 1_000,
            covered_ms: 0,
            subjects: Vec::new(),
            columns: vec![query_metric("cpu", MetricKind::Gauge)],
        };
        let view = SnapshotView {
            id: SnapshotViewId(23),
            meta: meta.clone(),
        };
        let mut data = TickData::example(1, &[], &["cpu"]);
        data.header.real_ns = meta.bucket_real_ns;
        data.entries[0] = Entry::Metric {
            facts: meta.columns[0].facts,
            name: "cpu".to_string(),
        };
        data.columns[0].metric = 0;
        data.columns[0].kind = MetricKind::Gauge;
        data.columns[0].value_type = MetricValueType::U64;
        data.columns[0].values.clear();
        data.columns[0].quality.clear();
        backend
            .snapshot_open
            .insert(source_request, Ok(view.clone()));
        backend.snapshot_read.insert(
            view.id,
            Ok(SnapshotData {
                tick: data,
                provenance: None,
            }),
        );
        let mut d = Driver {
            server: Server::new(query_limits(), backend),
            now: 0,
            out: BTreeMap::new(),
            opened: Vec::new(),
            uids: Vec::new(),
        };
        let id = d.open(Level::Observe, 1);
        d.ready(id);
        d.send(id, &request(3, "snapshot_at", &snapshot_args(false)));
        d.pump(id, usize::MAX);
        let raw = raw_frames(&d.take_out(id));
        assert_eq!(
            (raw[0].0, raw[1].0, raw[2].0),
            (
                FrameType::Rep.code(),
                FrameType::Block.code(),
                FrameType::End.code(),
            )
        );
        assert!(text(&raw[0].2).contains("\"covered_ms\":0"));
        let mut dicts = StreamDicts::new();
        let block = accept_block(&mut dicts, 3, &raw[1].2);
        assert!(block.rows.is_empty());
        assert_eq!(block.columns.len(), 1);
        assert!(block.columns[0].values.is_empty());
        assert_end_ok(&raw[2].2, 1, 0, "complete");
        d.server.finish(id);
        assert_clean(&d, "zero-row snapshot");
    }

    fn proc_args(cursor: Option<&str>) -> String {
        proc_args_page(cursor, 256, 1, true, &["cmdline"])
    }

    fn proc_args_page(
        cursor: Option<&str>,
        limit: u32,
        subject_inc: u64,
        descendants: bool,
        fields: &[&str],
    ) -> String {
        let cursor = match cursor {
            Some(cursor) => format!("\"{cursor}\""),
            None => "null".to_string(),
        };
        let fields = fields
            .iter()
            .map(|field| format!("\"{field}\""))
            .collect::<Vec<_>>()
            .join(",");
        format!(
            "{{\"subject\":{{\"inc\":{subject_inc}}},\"descendants\":{descendants},\
             \"fields\":[{fields}],\"limit\":{limit},\"cursor\":{cursor}}}"
        )
    }

    fn proc_row(pid: u32, start_ticks: u64, inc: u64) -> ProcRow {
        ProcRow {
            pid,
            start_ticks,
            inc,
            comm: None,
            ppid: None,
            state: None,
            cpu_usec: None,
            rss_bytes: None,
            io_rbytes: None,
            io_wbytes: None,
            threads: None,
            cmdline: None,
        }
    }

    fn procs_reply(raw: &[(u8, u32, Vec<u8>)]) -> Value {
        assert_eq!(raw.len(), 1);
        assert_eq!(raw[0].0, FrameType::Rep.code());
        let body = frame_payload(&raw[0].2);
        check_rep(body, false).expect("procs REP");
        serde_json::from_slice(body).expect("procs JSON")
    }

    fn procs_reply_once(backend: FixedBackend, args: &str) -> Vec<(u8, u32, Vec<u8>)> {
        let mut d = Driver {
            server: Server::new(ServerLimits::new(), backend),
            now: 0,
            out: BTreeMap::new(),
            opened: Vec::new(),
            uids: Vec::new(),
        };
        let id = d.open(Level::Observe, 1);
        d.ready(id);
        d.send(id, &request(3, "procs", args));
        d.pump(id, usize::MAX);
        let raw = raw_frames(&d.take_out(id));
        d.server.finish(id);
        raw
    }

    #[test]
    fn server_procs_pages_whole_rows_at_the_reply_cap_and_replays_coverage() {
        let mut backend = FixedBackend::new();
        let source_request = ProcsRequest {
            subject_inc: 1,
            descendants: true,
            fields: vec![ProcField::Cmdline],
        };
        let view = ProcScanView {
            id: ProcProjectionId(31),
            scan_id: ProcScanId(9),
            published_mono_ns: 0,
            row_count: 60,
            scanned: 60,
            total: 61,
            complete: false,
        };
        backend.procs_open.insert(source_request, Ok(view.clone()));
        let escaped = "\\".repeat(crate::query::MAX_CMDLINE_BYTES);
        for index in 0..60u32 {
            backend.procs_read.insert(
                (view.id, index),
                Ok(Some(ProcRow {
                    pid: index + 1,
                    start_ticks: u64::from(index),
                    inc: if index < 2 { 1 } else { 2 },
                    comm: None,
                    ppid: None,
                    state: None,
                    cpu_usec: None,
                    rss_bytes: None,
                    io_rbytes: None,
                    io_wbytes: None,
                    threads: None,
                    cmdline: Some(escaped.clone()),
                })),
            );
        }
        let mut d = Driver {
            server: Server::new(ServerLimits::new(), backend),
            now: 0,
            out: BTreeMap::new(),
            opened: Vec::new(),
            uids: Vec::new(),
        };
        let id = d.open(Level::Operator, 1);
        d.ready(id);
        d.send(id, &request(3, "procs", &proc_args(None)));
        d.pump(id, usize::MAX);
        let first = procs_reply(&raw_frames(&d.take_out(id)));
        let first_rows = first["result"]["procs"].as_array().expect("procs array");
        assert!(!first_rows.is_empty() && first_rows.len() < 60);
        assert_eq!(first["result"]["coverage"]["complete"], Value::Bool(false));
        assert_eq!(first["result"]["coverage"]["scanned"], Value::from(60));
        assert_eq!(first["result"]["coverage"]["total"], Value::from(61));
        let token = first["result"]["next"]
            .as_str()
            .expect("page continuation")
            .to_string();
        let mut pids = BTreeSet::new();
        for row in first_rows {
            pids.insert(row["pid"].as_u64().expect("pid"));
            assert_eq!(
                row["cmdline"].as_str().expect("cmdline").len(),
                crate::query::MAX_CMDLINE_BYTES
            );
        }
        let first_count = pids.len();

        d.send(id, &request(4, "procs", &proc_args(Some(&token))));
        d.pump(id, usize::MAX);
        let second = procs_reply(&raw_frames(&d.take_out(id)));
        assert_eq!(second["result"]["next"], Value::Null);
        assert_eq!(second["result"]["coverage"]["complete"], Value::Bool(false));
        for row in second["result"]["procs"].as_array().expect("procs array") {
            assert!(pids.insert(row["pid"].as_u64().expect("pid")));
        }
        assert_eq!(
            first_count + second["result"]["procs"].as_array().unwrap().len(),
            60
        );
        assert_eq!(pids.len(), 60);
    }

    #[test]
    fn procs_terminal_page_succeeds_at_cursor_capacity_while_continuation_refuses() {
        let source_request = ProcsRequest {
            subject_inc: 1,
            descendants: true,
            fields: vec![ProcField::Comm],
        };
        let view = ProcScanView {
            id: ProcProjectionId(51),
            scan_id: ProcScanId(10),
            published_mono_ns: 0,
            row_count: 3,
            scanned: 3,
            total: 3,
            complete: true,
        };
        let mut backend = FixedBackend::new();
        backend.procs_open.insert(source_request, Ok(view.clone()));
        for index in 0..3 {
            let mut row = proc_row(index + 1, u64::from(index), 1);
            row.comm = Some(format!("p{index}"));
            backend.procs_read.insert((view.id, index), Ok(Some(row)));
        }
        let mut d = Driver {
            server: Server::new(ServerLimits::new(), backend),
            now: 2 * MS,
            out: BTreeMap::new(),
            opened: Vec::new(),
            uids: Vec::new(),
        };
        let id = d.open(Level::Observe, 1);
        d.ready(id);
        d.send(
            id,
            &request(3, "procs", &proc_args_page(None, 1, 1, true, &["comm"])),
        );
        d.pump(id, usize::MAX);
        let first = procs_reply(&raw_frames(&d.take_out(id)));
        let token = first["result"]["next"]
            .as_str()
            .expect("process page token")
            .to_string();
        assert_eq!(d.server.cursor_count(), 1);
        fill_cursor_slots(&mut d, 1);

        d.send(
            id,
            &request(
                4,
                "procs",
                &proc_args_page(Some(&token), 2, 1, true, &["comm"]),
            ),
        );
        d.pump(id, usize::MAX);
        let terminal = procs_reply(&raw_frames(&d.take_out(id)));
        assert_eq!(terminal["result"]["next"], Value::Null);
        assert_eq!(terminal["result"]["procs"].as_array().unwrap().len(), 2);

        d.send(
            id,
            &request(
                5,
                "procs",
                &proc_args_page(Some(&token), 1, 1, true, &["comm"]),
            ),
        );
        d.pump(id, usize::MAX);
        let raw = raw_frames(&d.take_out(id));
        assert_eq!(raw.len(), 1);
        assert!(text(&raw[0].2)
            .contains("\"code\":\"budget\",\"message\":\"a budget is exhausted\",\"detail\":{\"pool\":\"cursors\"}"));

        clear_cursor_slots(&mut d);
        d.server.finish(id);
        assert_clean(&d, "full process cursor table");
    }

    #[test]
    fn procs_filters_have_distinct_pinned_projections_and_limit_only_changes_boundary() {
        let filter_a = ProcsRequest {
            subject_inc: 1,
            descendants: true,
            fields: vec![ProcField::Comm],
        };
        let filter_b = ProcsRequest {
            subject_inc: 2,
            descendants: false,
            fields: vec![ProcField::State],
        };
        let view_a = ProcScanView {
            id: ProcProjectionId(61),
            scan_id: ProcScanId(5),
            published_mono_ns: 0,
            row_count: 3,
            scanned: 3,
            total: 4,
            complete: true,
        };
        let view_b = ProcScanView {
            id: ProcProjectionId(62),
            scan_id: ProcScanId(5),
            published_mono_ns: 0,
            row_count: 2,
            scanned: 2,
            total: 3,
            complete: false,
        };
        let newer_a = ProcScanView {
            id: ProcProjectionId(63),
            scan_id: ProcScanId(6),
            published_mono_ns: 0,
            row_count: 1,
            scanned: 1,
            total: 1,
            complete: true,
        };
        let mut backend = FixedBackend::new();
        backend
            .procs_open
            .insert(filter_a.clone(), Ok(view_a.clone()));
        backend
            .procs_open
            .insert(filter_b.clone(), Ok(view_b.clone()));
        let mut alpha = proc_row(10, 1, 1);
        alpha.comm = Some("alpha".to_string());
        let mut absent = proc_row(20, 2, 2);
        absent.comm = None;
        let mut gamma = proc_row(30, 3, 1);
        gamma.comm = Some("gamma".to_string());
        for (index, row) in [alpha, absent, gamma].into_iter().enumerate() {
            backend.procs_read.insert(
                (view_a.id, u32::try_from(index).expect("index")),
                Ok(Some(row)),
            );
        }
        let mut running = proc_row(11, 1, 1);
        running.state = Some(b'S');
        let mut sleeping = proc_row(22, 2, 2);
        sleeping.state = Some(b'Z');
        for (index, row) in [running, sleeping].into_iter().enumerate() {
            backend.procs_read.insert(
                (view_b.id, u32::try_from(index).expect("index")),
                Ok(Some(row)),
            );
        }
        let mut d = Driver {
            server: Server::new(ServerLimits::new(), backend),
            now: 2 * MS,
            out: BTreeMap::new(),
            opened: Vec::new(),
            uids: Vec::new(),
        };
        let id = d.open(Level::Observe, 1);
        d.ready(id);
        d.send(
            id,
            &request(3, "procs", &proc_args_page(None, 1, 1, true, &["comm"])),
        );
        d.pump(id, usize::MAX);
        let a_first = procs_reply(&raw_frames(&d.take_out(id)));
        let a_token = a_first["result"]["next"]
            .as_str()
            .expect("filter A token")
            .to_string();
        assert_eq!(a_first["result"]["coverage"]["complete"], Value::Bool(true));

        d.send(
            id,
            &request(4, "procs", &proc_args_page(None, 1, 2, false, &["state"])),
        );
        d.pump(id, usize::MAX);
        let b_first = procs_reply(&raw_frames(&d.take_out(id)));
        let b_token = b_first["result"]["next"]
            .as_str()
            .expect("filter B token")
            .to_string();
        assert_eq!(
            b_first["result"]["coverage"]["complete"],
            Value::Bool(false)
        );
        assert_eq!(d.server.cursor_count(), 2);

        let mut newer = proc_row(99, 9, 1);
        newer.comm = Some("newer".to_string());
        d.server
            .backend_mut()
            .procs_open
            .insert(filter_a, Ok(newer_a.clone()));
        d.server
            .backend_mut()
            .procs_read
            .insert((newer_a.id, 0), Ok(Some(newer)));

        d.send(
            id,
            &request(
                5,
                "procs",
                &proc_args_page(Some(&a_token), 1, 1, true, &["comm"]),
            ),
        );
        d.pump(id, usize::MAX);
        let replay_one = raw_frames(&d.take_out(id));
        let replay_one_value = procs_reply(&replay_one);
        let replay_one_body = frame_payload(&replay_one[0].2).to_vec();
        assert_eq!(replay_one_value["result"]["procs"][0]["pid"], 20);
        assert_eq!(replay_one_value["result"]["procs"][0]["comm"], Value::Null);

        d.send(
            id,
            &request(
                6,
                "procs",
                &proc_args_page(Some(&a_token), 1, 1, true, &["comm"]),
            ),
        );
        d.pump(id, usize::MAX);
        let replay_again = raw_frames(&d.take_out(id));
        assert_eq!(
            frame_payload(&replay_again[0].2),
            replay_one_body.as_slice()
        );

        d.send(
            id,
            &request(
                7,
                "procs",
                &proc_args_page(Some(&a_token), 2, 1, true, &["comm"]),
            ),
        );
        d.pump(id, usize::MAX);
        let replay_two = procs_reply(&raw_frames(&d.take_out(id)));
        assert_eq!(replay_two["result"]["procs"].as_array().unwrap().len(), 2);
        assert_eq!(replay_two["result"]["procs"][0]["pid"], 20);
        assert_eq!(replay_two["result"]["next"], Value::Null);

        d.send(
            id,
            &request(
                8,
                "procs",
                &proc_args_page(Some(&b_token), 2, 2, false, &["state"]),
            ),
        );
        d.pump(id, usize::MAX);
        let b_replay = procs_reply(&raw_frames(&d.take_out(id)));
        assert_eq!(b_replay["result"]["procs"][0]["pid"], 22);
        assert_eq!(b_replay["result"]["procs"][0]["state"], "Z");
        assert_eq!(b_replay["result"]["next"], Value::Null);

        d.send(
            id,
            &request(
                9,
                "procs",
                &proc_args_page(Some(&a_token), 1, 2, false, &["state"]),
            ),
        );
        d.pump(id, usize::MAX);
        let mismatch = raw_frames(&d.take_out(id));
        assert_eq!(mismatch.len(), 1);
        assert!(text(&mismatch[0].2)
            .contains("\"code\":\"cursor-invalid\",\"message\":\"the cursor is not valid\""));

        clear_cursor_slots(&mut d);
        d.server.finish(id);
        assert_clean(&d, "pinned process filters");
    }

    #[test]
    fn procs_open_budget_precedes_subject_lookup_and_not_found_is_within_a_scan() {
        let source_request = ProcsRequest {
            subject_inc: u64::MAX,
            descendants: true,
            fields: Vec::new(),
        };
        let mut backend = FixedBackend::new();
        backend
            .procs_open
            .insert(source_request, Err(QuerySourceError::Budget(Pool::Procs)));
        let mut d = Driver {
            server: Server::new(ServerLimits::new(), backend),
            now: 0,
            out: BTreeMap::new(),
            opened: Vec::new(),
            uids: Vec::new(),
        };
        let id = d.open(Level::Observe, 1);
        d.ready(id);
        d.send(
            id,
            &request(
                3,
                "procs",
                "{\"subject\":{\"inc\":18446744073709551615},\"descendants\":true,\
                 \"fields\":[],\"limit\":1,\"cursor\":null}",
            ),
        );
        d.pump(id, usize::MAX);
        let raw = raw_frames(&d.take_out(id));
        assert!(text(&raw[0].2).contains("\"pool\":\"procs\""));

        let mut backend = FixedBackend::new();
        let source_request = ProcsRequest {
            subject_inc: 2,
            descendants: true,
            fields: Vec::new(),
        };
        backend
            .procs_open
            .insert(source_request, Err(QuerySourceError::NotFound));
        let mut d = Driver {
            server: Server::new(ServerLimits::new(), backend),
            now: 0,
            out: BTreeMap::new(),
            opened: Vec::new(),
            uids: Vec::new(),
        };
        let id = d.open(Level::Observe, 1);
        d.ready(id);
        d.send(
            id,
            &request(
                3,
                "procs",
                "{\"subject\":{\"inc\":2},\"descendants\":true,\
                 \"fields\":[],\"limit\":1,\"cursor\":null}",
            ),
        );
        d.pump(id, usize::MAX);
        let raw = raw_frames(&d.take_out(id));
        assert!(text(&raw[0].2).contains("\"code\":\"not-found\""));
    }

    #[test]
    fn procs_metadata_and_read_failures_are_internal_before_any_partial_rep() {
        let source_request = ProcsRequest {
            subject_inc: 7,
            descendants: true,
            fields: vec![ProcField::Comm],
        };
        let args = proc_args_page(None, 1, 7, true, &["comm"]);
        let valid = ProcScanView {
            id: ProcProjectionId(71),
            scan_id: ProcScanId(20),
            published_mono_ns: 0,
            row_count: 1,
            scanned: 1,
            total: 1,
            complete: true,
        };
        for bad in [
            ProcScanView {
                row_count: 4_097,
                ..valid.clone()
            },
            ProcScanView {
                scanned: 2,
                total: 1,
                ..valid.clone()
            },
            ProcScanView {
                published_mono_ns: 1,
                ..valid.clone()
            },
        ] {
            let mut backend = FixedBackend::new();
            backend.procs_open.insert(source_request.clone(), Ok(bad));
            let raw = procs_reply_once(backend, &args);
            assert_eq!(raw.len(), 1);
            assert!(text(&raw[0].2).contains("\"code\":\"internal\""));
        }

        let mut backend = FixedBackend::new();
        backend
            .procs_open
            .insert(source_request.clone(), Ok(valid.clone()));
        backend
            .procs_read
            .insert((valid.id, 0), Err(QuerySourceError::ViewInvalid));
        let raw = procs_reply_once(backend, &args);
        assert_eq!(raw.len(), 1);
        assert!(text(&raw[0].2).contains("\"code\":\"internal\""));

        for (comm, ok) in [
            (format!("{}x", "é".repeat(22)), true),
            (format!("{}xy", "é".repeat(22)), false),
        ] {
            let mut row = proc_row(1, 1, 7);
            row.comm = Some(comm);
            let mut backend = FixedBackend::new();
            backend
                .procs_open
                .insert(source_request.clone(), Ok(valid.clone()));
            backend.procs_read.insert((valid.id, 0), Ok(Some(row)));
            let raw = procs_reply_once(backend, &args);
            if ok {
                let value = procs_reply(&raw);
                assert_eq!(
                    value["result"]["procs"][0]["comm"].as_str().unwrap().len(),
                    45
                );
            } else {
                assert_eq!(raw.len(), 1);
                assert!(text(&raw[0].2).contains("\"code\":\"internal\""));
            }
        }
    }

    #[test]
    fn series_read_budget_after_rep_preserves_the_block_prefix() {
        let mut backend = FixedBackend::new();
        backend.status.tiers.push(TierInfo {
            name: "hot".to_string(),
            step_ms: 1_000,
            keep_s: 3_600,
            bytes: 0,
            cap_bytes: 0,
        });
        let mut d = Driver {
            server: Server::new(query_limits(), backend),
            now: 0,
            out: BTreeMap::new(),
            opened: Vec::new(),
            uids: Vec::new(),
        };
        install_series(&mut d);
        d.server.backend_mut().series_read.insert(
            (SeriesViewId(11), SeriesPosition(1), 2),
            Err(QuerySourceError::Budget(Pool::Rows)),
        );
        let id = d.open(Level::Observe, 1);
        d.ready(id);
        d.send(id, &request(3, "series_batch", &series_args(None, 2)));
        d.pump(id, usize::MAX);
        let raw = raw_frames(&d.take_out(id));
        assert_eq!(raw.len(), 3);
        assert_eq!(
            (raw[0].0, raw[1].0, raw[2].0),
            (
                FrameType::Rep.code(),
                FrameType::Block.code(),
                FrameType::End.code(),
            )
        );
        assert_eq!(raw[1].2.len(), 328);
        let (ok, blocks, _dropped, reason) = end_info(&raw[2].2);
        assert!(!ok);
        assert_eq!((blocks, reason), (1, None));
        assert!(text(&raw[2].2)
            .contains("\"code\":\"budget\",\"message\":\"a budget is exhausted\",\"detail\":{\"pool\":\"rows\"}"));
    }

    #[test]
    fn series_read_view_invalid_after_rep_preserves_the_queue_and_invalidates_cursors() {
        let mut backend = FixedBackend::new();
        backend.status.tiers.push(TierInfo {
            name: "hot".to_string(),
            step_ms: 1_000,
            keep_s: 3_600,
            bytes: 0,
            cap_bytes: 0,
        });
        let mut d = Driver {
            server: Server::new(query_limits(), backend),
            now: 0,
            out: BTreeMap::new(),
            opened: Vec::new(),
            uids: Vec::new(),
        };
        install_series(&mut d);
        d.server.backend_mut().series_read.insert(
            (SeriesViewId(11), SeriesPosition(1), 2),
            Err(QuerySourceError::ViewInvalid),
        );
        let id = d.open(Level::Observe, 1);
        d.ready(id);
        d.send(id, &request(3, "series_batch", &series_args(None, 2)));
        d.pump(id, usize::MAX);
        let raw = raw_frames(&d.take_out(id));
        assert_eq!(raw.len(), 3);
        assert_eq!(raw[0].0, FrameType::Rep.code());
        assert_eq!(raw[1].0, FrameType::Block.code());
        assert_eq!(raw[1].2.len(), 328);
        assert_eq!(raw[2].0, FrameType::End.code());
        assert!(!check_end(frame_payload(&raw[2].2)).expect("END").ok);
        assert_eq!(d.server.cursor_count(), 0);
        assert_eq!(
            d.server
                .view_references(QueryViewId::Series(SeriesViewId(11))),
            0
        );
        assert_eq!(
            d.server.backend().released.borrow().as_slice(),
            &[QueryViewId::Series(SeriesViewId(11))]
        );
        d.server.finish(id);
        assert_clean(&d, "view invalid after REP");
    }

    #[test]
    fn reused_successor_is_not_aborted_by_an_unexposed_replay_failure() {
        let mut backend = FixedBackend::new();
        backend.status.tiers.push(TierInfo {
            name: "hot".to_string(),
            step_ms: 1_000,
            keep_s: 3_600,
            bytes: 0,
            cap_bytes: 0,
        });
        let mut d = Driver {
            server: Server::new(query_limits(), backend),
            now: 0,
            out: BTreeMap::new(),
            opened: Vec::new(),
            uids: Vec::new(),
        };
        install_series(&mut d);
        let id = d.open(Level::Observe, 1);
        d.ready(id);
        d.send(id, &request(3, "series_batch", &series_args(None, 2)));
        d.pump(id, usize::MAX);
        let first = raw_frames(&d.take_out(id));
        let token = end_next(&first[3].2).expect("continuation");
        let view = QueryViewId::Series(SeriesViewId(11));
        assert_eq!(d.server.view_references(view), 1);

        d.server.backend_mut().series_read.insert(
            (SeriesViewId(11), SeriesPosition(0), 1),
            Err(QuerySourceError::Budget(Pool::Rows)),
        );
        d.send(id, &request(4, "series_batch", &series_args(None, 2)));
        d.pump(id, usize::MAX);
        let repeated = raw_frames(&d.take_out(id));
        assert_eq!(
            (repeated[0].0, repeated[1].0),
            (FrameType::Rep.code(), FrameType::End.code())
        );
        assert!(text(&repeated[1].2).contains("\"pool\":\"rows\""));
        assert_eq!(d.server.view_references(view), 1);

        let replay = d.open(Level::Observe, 1);
        d.ready(replay);
        d.send(
            replay,
            &request(5, "series_batch", &series_args(Some(&token), 1)),
        );
        d.pump(replay, usize::MAX);
        let replayed = raw_frames(&d.take_out(replay));
        assert_eq!(
            (replayed[0].0, replayed[1].0, replayed[2].0),
            (
                FrameType::Rep.code(),
                FrameType::Block.code(),
                FrameType::End.code(),
            )
        );
    }

    #[test]
    fn close_while_waiting_releases_snapshot_and_series_state_once() {
        let mut backend = FixedBackend::new();
        backend.status.tiers.push(TierInfo {
            name: "hot".to_string(),
            step_ms: 1_000,
            keep_s: 3_600,
            bytes: 0,
            cap_bytes: 0,
        });
        let source_request = snapshot_request();
        let meta = snapshot_meta();
        let view = SnapshotView {
            id: SnapshotViewId(41),
            meta: meta.clone(),
        };
        backend
            .snapshot_open
            .insert(source_request, Ok(view.clone()));
        backend
            .snapshot_read
            .insert(view.id, Ok(snapshot_candidate(&meta)));
        let series_source = series_request();
        let series = series_view();
        let mut d = Driver {
            server: Server::new(query_limits(), backend),
            now: 0,
            out: BTreeMap::new(),
            opened: Vec::new(),
            uids: Vec::new(),
        };

        let snapshot = d.open(Level::Observe, 1);
        d.ready(snapshot);
        let snapshot_frame = crate::query::snapshot_frame_len(&meta).expect("snapshot frame");
        let snapshot_fill = fill_query_short_of_frame(&mut d, snapshot, snapshot_frame);
        d.send(snapshot, &request(2, "snapshot_at", &snapshot_args(false)));
        let snapshot_view = QueryViewId::Snapshot(SnapshotViewId(41));
        assert_eq!(d.server.view_references(snapshot_view), 1);
        assert_eq!(
            d.server.conn(snapshot).map(|conn| conn.outstanding()),
            Some(1)
        );
        assert_eq!(d.server.hub().queued(snapshot).query, snapshot_fill);
        d.server.shutdown();
        d.close_out(snapshot);
        assert_eq!(d.server.view_references(snapshot_view), 0);
        assert_eq!(
            d.server
                .backend()
                .released
                .borrow()
                .iter()
                .filter(|view| **view == snapshot_view)
                .count(),
            1
        );

        install_series(&mut d);
        let series_dictionary =
            crate::query::series_dictionary(&series, &series_source).expect("series dictionary");
        let series_bounds = {
            let limits = d.server.limits().observe;
            crate::query::QueryBounds {
                max_rows: limits.max_rows,
                max_block_buckets: limits.max_block_buckets,
                max_block_bytes: limits.max_block_bytes,
            }
        };
        let series_page = crate::query::plan_series_page(
            d.server.backend(),
            crate::query::SeriesPlanInput {
                view: &series,
                request: &series_source,
                permitted_columns: &series_dictionary.permitted,
                dictionary_len: series_dictionary.body.len(),
                bounds: series_bounds,
                max_blocks: 2,
                start: series.first.expect("series start"),
            },
        )
        .expect("series page");
        let first_series_block = *series_page.blocks.first().expect("first series block");
        let series_frame = crate::query::series_plan_frame_len(
            first_series_block.buckets,
            &series_dictionary.permitted,
            first_series_block.agg,
            Some(series_dictionary.body.len()),
        )
        .expect("series frame");

        let series_conn = d.open(Level::Observe, 1);
        d.ready(series_conn);
        let series_fill = fill_query_short_of_frame(&mut d, series_conn, series_frame);
        d.send(
            series_conn,
            &request(2, "series_batch", &series_args(None, 2)),
        );
        let series_view = QueryViewId::Series(SeriesViewId(11));
        assert_eq!(d.server.view_references(series_view), 2);
        assert_eq!(
            d.server.conn(series_conn).map(|conn| conn.outstanding()),
            Some(1)
        );
        assert_eq!(d.server.hub().queued(series_conn).query, series_fill);
        d.server.shutdown();
        d.close_out(series_conn);
        assert_eq!(d.server.view_references(series_view), 0);
        assert_eq!(
            d.server
                .backend()
                .released
                .borrow()
                .iter()
                .filter(|view| **view == series_view)
                .count(),
            1
        );
    }

    fn waiting_series(d: &mut Driver, id: ConnId, blocker: ConnId, series_view: QueryViewId) {
        assert_eq!(d.server.hub().queued(blocker).query, 328);
        assert_eq!(d.server.hub().uid_pools(1).query, 328);
        assert_eq!(d.server.hub().queued(id).query, 0);
        assert!(!d.server.hub().query_room(id, 1));
        d.send(id, &request(3, "series_batch", &series_args(None, 2)));
        assert_eq!(d.server.view_references(series_view), 2);
        assert_eq!(d.server.conn(id).map(|conn| conn.outstanding()), Some(1));
        assert!(d
            .server
            .conn(id)
            .is_some_and(|conn| conn.has_pending_series_page(3)));
        assert_eq!(d.server.cursor_count(), 1);
    }

    fn waiting_series_fixture() -> (Driver, ConnId, ConnId, QueryViewId) {
        let mut backend = FixedBackend::new();
        backend.status.tiers.push(TierInfo {
            name: "hot".to_string(),
            step_ms: 1_000,
            keep_s: 3_600,
            bytes: 0,
            cap_bytes: 0,
        });
        let series_source = series_request();
        let series = series_view();
        backend.series_open.insert(series_source, Ok(series));
        let mut d = Driver {
            server: Server::new(query_bound_limits(328), backend),
            now: 0,
            out: BTreeMap::new(),
            opened: Vec::new(),
            uids: Vec::new(),
        };
        install_series(&mut d);
        let id = d.open(Level::Observe, 1);
        let blocker = d.open(Level::Observe, 1);
        d.ready(id);
        d.ready(blocker);
        fill_query(&mut d, blocker, 328);
        let series_view = QueryViewId::Series(SeriesViewId(11));
        waiting_series(&mut d, id, blocker, series_view);
        (d, id, blocker, series_view)
    }

    fn assert_series_release_count(d: &Driver, series_view: QueryViewId, expected: usize) {
        assert_eq!(
            d.server
                .backend()
                .released
                .borrow()
                .iter()
                .filter(|view| **view == series_view)
                .count(),
            expected
        );
    }

    #[test]
    fn direct_finish_releases_waiting_series_once() {
        let (mut d, id, blocker, series_view) = waiting_series_fixture();
        d.server.finish(id);
        assert_eq!(d.server.view_references(series_view), 0);
        assert_eq!(d.server.cursor_count(), 0);
        assert_series_release_count(&d, series_view, 1);
        d.server.finish(blocker);
        assert_clean(&d, "direct finish");
    }

    #[test]
    fn fatal_close_keeps_waiting_series_until_close_out_finishes_once() {
        let (mut d, id, blocker, series_view) = waiting_series_fixture();
        d.send(id, &[0, 0, 0, 0]);
        d.tick(5_000_000_001);
        assert_eq!(d.close_code(id), Some(Code::Deadline));
        assert!(d.server.conn(id).is_some_and(|conn| conn.has_final()));
        assert_eq!(d.server.view_references(series_view), 2);
        assert_eq!(d.server.cursor_count(), 1);
        assert_series_release_count(&d, series_view, 0);

        d.close_out(id);
        assert_eq!(d.server.view_references(series_view), 0);
        assert_eq!(d.server.cursor_count(), 0);
        assert_series_release_count(&d, series_view, 1);
        d.server.finish(blocker);
        assert_clean(&d, "fatal close");
    }
}
