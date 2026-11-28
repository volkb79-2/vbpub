//! The connection state machine of the probe's serving side (design R1 to R3, R6 to R10, R20 to
//! R24; WIRE.md sections 1, 5.2 and 5.4), sans-IO: no sockets, no threads, no clock reads and no
//! allocation from an untrusted length.
//!
//! A [`Conn`] is one accepted connection. The driver (the `Server` of the next package) hands it
//! received bytes ([`Conn::on_bytes`]), the end of the stream ([`Conn::on_eof`]), the clock
//! ([`Conn::poll`]) and the count of bytes it wrote ([`Conn::on_written`]); everything the
//! connection sends goes through the one `SendHub` the driver passes in, except the final frame.
//!
//! # Contracts
//!
//! - **One hub, no mutex.** The conn methods take `&mut SendHub` and `&dyn Backend` as disjoint
//!   field borrows of the server (R27); a single-threaded `poll(2)` driver needs no lock.
//! - **Writing goes through [`Conn::on_written`] only.** It calls `SendHub::wrote` itself and
//!   counts the bytes (R2 needs "bytes written so far"); the driver never calls `wrote`
//!   directly. `n` must not exceed the bytes of the head the driver was offered.
//! - **A connection-fatal outcome yields ONE final frame held outside the hub (R1).** The conn
//!   calls `drain_unpinned`; when no head is partly written the final bytes are kept for
//!   [`Conn::take_final`], else there are none; either way the conn is closed
//!   ([`Conn::is_closed`]) and the driver makes one non-blocking write of the final bytes and
//!   calls `finish`. The final bytes are the REP (or error line) of the outcome, the `closing` EVT
//!   (or error line) of a server-originated close, or the server prefix alone (major mismatch).
//! - **Prefix guarantee (R2).** When nothing at all was written and the server prefix was queued,
//!   the final bytes start with the prefix, so a client that sent the prefix and a bad request in
//!   one read still sees a well-formed handshake.
//! - **Frames left in a `Feed` after a fatal outcome are discarded (R20).**
//! - **Silent closes (R10).** EOF before `Ready`, EOF inside a frame or line, bytes after EOF and
//!   a completed `Draining` close without a reply (`close_code` is `None` or the cause).
//! - **Timer order (R20), per `poll`:** the hello deadline, then the reader's age (the request
//!   deadline), then idle. The hub's `slow-client` is the server's: it calls
//!   [`Conn::close_for`] with [`Code::SlowClient`]. The idle condition (only `Ready`, reader at a
//!   frame boundary, nothing queued) is recomputed at the end of every entry point.
//! - **No request waits (D-W49).** Every request is answered or refused in the turn that reads
//!   it. The connection's only request records are its live subscriptions; a Query block remains
//!   queued output, not a pending request. A clean EOF ends subscriptions and closes silently once
//!   queued output is written.
//! - **The driver never writes the hub head after `is_closed`.** A head pinned with 0 bytes
//!   written is not partial, so `drain_unpinned` keeps it, and when it is the server prefix the
//!   final bytes repeat the prefix (R2); writing the hub head as well would send the prefix
//!   twice. `Server::output` answers `None` for a closed connection and `on_written` ignores one,
//!   so the `Server` already follows the rule; any other driver must too.
//! - **`close_for` takes the three `closing` reasons only:** `idle` (the conn's own timer calls
//!   it), `slow-client` and `shutting-down` (WIRE.md section 5.4). Another code would put that
//!   code in a `closing` EVT, which a client does not expect; a fatal request or reader outcome
//!   never goes through `close_for`.
//! - **Reader errors are mapped once.** The frame reader maps every error except oversize and
//!   deadline to `bad-frame`; the line reader supplies its own code. An `eof()` error is consumed
//!   by [`Conn::on_eof`] as the silent-close branch, so it never reaches dispatch. A `SECTIONED`
//!   REQ never reaches `classify`: the frame-header parse refuses the flag on every non-BLOCK
//!   frame and reports `bad-frame` through that same reader path.
//! - **P5b-2 stream state.** `subs` maps a live subscription `corr` to its registry selection id.
//!   The registry itself belongs to [`Server`](crate::server::Server), which releases every entry
//!   at `finish` even when the connection closed before it could be finished.

use std::collections::BTreeMap;
use std::sync::Arc;

use crate::hub::{Close, ConnId, Pools, QueryOutcome, SendHub};
use hostobs_proto::error::ProtoError;
use hostobs_proto::frame::{FrameType, FRAME_HEADER_LEN, PROTO_MINOR};
use hostobs_proto::quality;
use hostobs_proto::stream::{ConnTimers, Detect, Feed, FrameReader, ModeDetect, RawFrame, Timer};

use crate::backend::{
    AnnotateKind, AnnotateRefusal, Backend, EventWindow, EventsArgs, Identity, PageRefusal,
    StatusInfo,
};
use crate::classify::{
    classify_with, Call, ConnFacts, Dispatch, Mode, Phase, Prefix, Query, Refusal, SERVER_MAJOR,
};
use crate::codes::{Code, Detail, Pool};
use crate::encode::{
    annotate_result, closing_evt, current_result, end_error, end_ok, error_line, push_event_item,
    push_string, rep_error, rep_ok, rep_ok_line, server_prefix, subscribe_result,
    unknown_mode_line, EndReason, UNSUBSCRIBE_RESULT,
};
use crate::limits::{ConnLimits, Level};
use crate::lines::{LineError, LineFeed, LineReader};
use crate::registry::{SelId, SelectionCaps, SelectionStreams, SubKey};
use crate::selection::Selection;
use crate::tick::{query_block, BlockBounds, LiveTick};
use crate::verbs::served_at;

/// The highest protocol minor the probe serves.
pub const SERVER_MINOR: u16 = PROTO_MINOR;

/// A cgroup identity label captured from the peer's `/proc` entry.
///
/// This is the same `(st_dev, inode)` pair stored by the model cgroup identity and used by the
/// `cgroup_id` selector, read from the peer's unverified `/proc` entry.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct CgroupKey {
    /// The `st_dev` device number of the cgroup directory.
    pub dev: u64,
    /// The inode number of the cgroup directory.
    pub inode: u64,
}

/// The peer credentials and advisory process label captured once at accept.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Peer {
    /// The peer's uid.
    pub uid: u32,
    /// The pid reported by `SO_PEERCRED` at connect time, in the probe's PID namespace,
    /// if visible.
    /// A `/proc` label read later for this pid can describe another process.
    pub pid: Option<u32>,
    /// The unverified process name when valid UTF-8 and free of control characters.
    pub comm: Option<String>,
    /// The peer's cgroup device and inode for a directory joined under the configured root and
    /// found on that root's device.
    pub cgroup: Option<CgroupKey>,
}

/// The connection states of WIRE.md section 5.4. `Closing` is not a state: it is the moment
/// [`State::Closed`] is entered with a final frame.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum State {
    /// Fewer than 8 prefix bytes (or no byte) seen.
    Undecided,
    /// Binary: the server prefix is queued, waiting for the `hello` REQ.
    AwaitHello,
    /// JSON: waiting for the first complete line.
    JsonNoHello,
    /// Requests are dispatched.
    Ready,
    /// The client half-closed: finish queued output, then close silently.
    Draining,
    /// Finished: the driver takes [`Conn::take_final`] and calls `finish`.
    Closed,
}

#[derive(Debug)]
enum Reader {
    None,
    Frames(FrameReader),
    Lines(LineReader),
}

/// One complete request: a frame (binary) or a line without its `\n` (JSON).
enum Input {
    Frame(RawFrame),
    Line(Vec<u8>),
}

/// The requests one read completed and the code of the reader error that ended it (the reply
/// is fatal, `corr` 0).
struct Batch {
    inputs: Vec<Input>,
    error: Option<Code>,
}

/// The code a frame reader error closes with (WIRE.md section 5.2 "Wire mapping on the
/// server"): too large and the deadline are their own codes, every other error is `bad-frame`.
fn frame_fail(error: &ProtoError) -> Code {
    match error {
        ProtoError::OversizedFrame { .. } => Code::FrameTooLarge,
        ProtoError::Deadline => Code::Deadline,
        _ => Code::BadFrame,
    }
}

fn frame_batch(feed: Feed) -> Batch {
    Batch {
        inputs: feed.frames.into_iter().map(Input::Frame).collect(),
        error: feed.error.map(|error| frame_fail(&error)),
    }
}

/// A line reader error owes a reply only when it has a code (`LineError::code`); the others
/// cannot occur while the conn feeds the reader (module doc) and are ignored.
fn line_batch(feed: LineFeed) -> Batch {
    Batch {
        inputs: feed.lines.into_iter().map(Input::Line).collect(),
        error: feed.error.and_then(LineError::code),
    }
}

fn ms_to_ns(ms: u32) -> u64 {
    u64::from(ms).saturating_mul(1_000_000)
}

fn push_array<'a>(out: &mut String, items: impl Iterator<Item = &'a str>) {
    out.push('[');
    for (index, item) in items.enumerate() {
        if index > 0 {
            out.push(',');
        }
        push_string(out, item);
    }
    out.push(']');
}

fn fnv1a_extend(mut hash: u64, bytes: &[u8]) -> u64 {
    for byte in bytes {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x100000001b3);
    }
    hash
}

fn events_digest(args: &EventsArgs) -> u64 {
    let mut digest = 0xcbf29ce484222325u64;
    digest = fnv1a_extend(digest, &args.from_real_ns.to_le_bytes());
    digest = fnv1a_extend(digest, &args.to_real_ns.to_le_bytes());
    digest = fnv1a_extend(
        digest,
        &u32::try_from(args.names.len())
            .unwrap_or(u32::MAX)
            .to_le_bytes(),
    );
    for name in &args.names {
        digest = fnv1a_extend(
            digest,
            &u32::try_from(name.len()).unwrap_or(u32::MAX).to_le_bytes(),
        );
        digest = fnv1a_extend(digest, name.as_bytes());
    }
    digest
}

fn parse_events_cursor(cursor: &str) -> Option<(u64, u64, u64)> {
    let mut parts = cursor.split(':');
    if parts.next()? != "v1" {
        return None;
    }
    let epoch = parts.next()?.parse().ok()?;
    let digest = parts.next()?.parse().ok()?;
    let ordinal = parts.next()?.parse().ok()?;
    if parts.next().is_some() {
        return None;
    }
    Some((epoch, digest, ordinal))
}

fn events_cursor(epoch: u64, digest: u64, ordinal: u64) -> String {
    format!("v1:{epoch}:{digest}:{ordinal}")
}

fn events_suffix(window: &EventWindow, next: Option<&str>) -> String {
    let mut suffix = String::from("],\"next\":");
    if let Some(cursor) = next {
        push_string(&mut suffix, cursor);
    } else {
        suffix.push_str("null");
    }
    suffix.push_str(&format!(",\"displaced\":{}", window.displaced));
    suffix.push_str(",\"oldest_real_ns\":");
    if let Some(oldest) = window.oldest_real_ns {
        suffix.push_str(&oldest.to_string());
    } else {
        suffix.push_str("null");
    }
    suffix.push_str(&format!(
        ",\"started_real_ns\":{}}}",
        window.started_real_ns
    ));
    suffix
}

fn events_reply_len(result_len: usize, corr: u32, json: bool) -> usize {
    if json {
        "{\"ok\":true,\"corr\":"
            .len()
            .saturating_add(corr.to_string().len())
            .saturating_add(",\"result\":".len())
            .saturating_add(result_len)
            .saturating_add(1)
    } else {
        "{\"ok\":true,\"result\":"
            .len()
            .saturating_add(result_len)
            .saturating_add(1)
    }
}

/// The `hello` result of WIRE.md section 4.4: `minor` is the negotiated N.
fn hello_result(level: Level, minor: u16, identity: &Identity, limits: &ConnLimits) -> String {
    let mut out = format!("{{\"proto\":{{\"major\":{SERVER_MAJOR},\"minor\":{minor}}}");
    out.push_str(",\"level\":");
    push_string(&mut out, level.name());
    out.push_str(",\"server\":");
    push_string(&mut out, &identity.server);
    out.push_str(",\"boot_id\":");
    push_string(&mut out, &identity.boot_id);
    out.push_str(&format!(",\"epoch\":{}", identity.epoch));
    out.push_str(",\"features\":");
    push_array(&mut out, identity.features.iter().map(String::as_str));
    out.push_str(",\"verbs\":");
    push_array(&mut out, served_at(level).into_iter());
    out.push_str(",\"quality_codes\":[");
    for (index, entry) in quality::ALL.iter().enumerate() {
        if index > 0 {
            out.push(',');
        }
        out.push_str(&format!("{{\"code\":{},\"name\":", entry.code()));
        push_string(&mut out, entry.name());
        out.push('}');
    }
    out.push_str("],\"limits\":");
    out.push_str(&limits.hello_json());
    out.push('}');
    out
}

/// The `status` result of WIRE.md section 4.5; `queued` is the global Live plus Query bytes.
fn status_json(identity: &Identity, status: &StatusInfo, queued: Pools, dropped: u64) -> String {
    let mut out = String::from("{\"server\":");
    push_string(&mut out, &identity.server);
    out.push_str(",\"boot_id\":");
    push_string(&mut out, &identity.boot_id);
    out.push_str(&format!(",\"epoch\":{}", identity.epoch));
    out.push_str(&format!(",\"started_real_ns\":{}", status.started_real_ns));
    out.push_str(&format!(",\"uptime_ms\":{}", status.uptime_ms));
    let tick = status.tick;
    out.push_str(&format!(
        ",\"tick\":{{\"seq\":{},\"last_mono_ns\":{},\"scan_ms\":{}}}",
        tick.seq, tick.last_mono_ns, tick.scan_ms
    ));
    out.push_str(",\"clients\":{");
    for (index, (level, count)) in status.clients.iter().enumerate() {
        if index > 0 {
            out.push(',');
        }
        push_string(&mut out, level.name());
        out.push_str(&format!(":{count}"));
    }
    let bytes = queued.live.saturating_add(queued.query);
    let bytes = u64::try_from(bytes).unwrap_or(u64::MAX);
    out.push_str(&format!(
        "}},\"queues\":{{\"queued_bytes\":{bytes},\"dropped_blocks\":{}}}",
        dropped
    ));
    out.push_str(",\"tiers\":[");
    for (index, tier) in status.tiers.iter().enumerate() {
        if index > 0 {
            out.push(',');
        }
        out.push_str("{\"name\":");
        push_string(&mut out, &tier.name);
        out.push_str(&format!(
            ",\"step_ms\":{},\"keep_s\":{},\"bytes\":{},\"cap_bytes\":{}}}",
            tier.step_ms, tier.keep_s, tier.bytes, tier.cap_bytes
        ));
    }
    out.push_str("]}");
    out
}

/// One accepted connection (module doc: the contracts).
#[derive(Debug)]
pub struct Conn {
    id: ConnId,
    level: Level,
    peer: Peer,
    limits: ConnLimits,
    state: State,
    detect: ModeDetect,
    timers: ConnTimers,
    reader: Reader,
    client_prefix: Prefix,
    minor: u16,
    prefix_queued: bool,
    written: u64,
    close_code: Option<Code>,
    final_bytes: Option<Vec<u8>>,
    subs: BTreeMap<u32, SelId>,
    open_spans: usize,
}

impl Conn {
    /// A connection accepted at `now_ns`: `Undecided`, the hello deadline armed. `id` is the
    /// hub's `add(now_ns)`; `peer` is stored once and never re-read (R23).
    #[must_use]
    pub fn new(id: ConnId, level: Level, peer: Peer, limits: &ConnLimits, now_ns: u64) -> Conn {
        let hello_ns = ms_to_ns(limits.level.hello_deadline_ms);
        let mut timers = ConnTimers::new(hello_ns, ms_to_ns(limits.level.idle_ms));
        timers.on_accept(now_ns);
        Conn {
            id,
            level,
            peer,
            limits: *limits,
            state: State::Undecided,
            detect: ModeDetect::new(),
            timers,
            reader: Reader::None,
            client_prefix: Prefix { major: 0, minor: 0 },
            minor: 0,
            prefix_queued: false,
            written: 0,
            close_code: None,
            final_bytes: None,
            subs: BTreeMap::new(),
            open_spans: 0,
        }
    }

    /// The hub id of the connection.
    #[must_use]
    pub fn id(&self) -> ConnId {
        self.id
    }

    /// The serving level.
    #[must_use]
    pub fn level(&self) -> Level {
        self.level
    }

    /// The peer credentials captured at accept.
    #[must_use]
    pub fn peer(&self) -> &Peer {
        &self.peer
    }

    /// The current state.
    #[must_use]
    pub fn state(&self) -> State {
        self.state
    }

    /// Whether the connection is finished: the driver takes the final bytes and calls `finish`.
    #[must_use]
    pub fn is_closed(&self) -> bool {
        self.state == State::Closed
    }

    /// The code the connection closed for; `None` while open and for a silent close.
    #[must_use]
    pub fn close_code(&self) -> Option<Code> {
        self.close_code
    }

    /// The negotiated minor N (0 until a `hello` or the prefix decided it; JSON without a
    /// `hello` runs at minor 0).
    #[must_use]
    pub fn minor(&self) -> u16 {
        self.minor
    }

    /// The bytes the driver reported written through [`Conn::on_written`].
    #[must_use]
    pub fn bytes_written(&self) -> u64 {
        self.written
    }

    /// The final bytes (R1): the driver writes them once, non-blocking, and abandons a short
    /// write. `None` when there is none, or after the first call.
    pub fn take_final(&mut self) -> Option<Vec<u8>> {
        self.final_bytes.take()
    }

    /// Whether final bytes are still waiting for [`Conn::take_final`].
    #[must_use]
    pub fn has_final(&self) -> bool {
        self.final_bytes.is_some()
    }

    /// Bytes arrived from the peer at `now_ns` (an empty read is not an event).
    pub fn on_bytes(
        &mut self,
        bytes: &[u8],
        now_ns: u64,
        hub: &mut SendHub,
        backend: &mut dyn Backend,
        streams: &mut SelectionStreams,
    ) {
        if bytes.is_empty() || self.state == State::Closed {
            return;
        }
        self.timers.on_byte();
        match self.state {
            State::Undecided => self.on_undecided(bytes, now_ns, hub, backend, streams),
            State::AwaitHello | State::JsonNoHello | State::Ready => {
                self.on_stream(bytes, now_ns, hub, backend, streams);
            }
            State::Draining => self.finish_close(hub, None, None),
            State::Closed => {}
        }
        self.refresh_idle(now_ns, hub);
    }

    /// The peer ended its stream (R10): silent close before `Ready` or inside a frame or line;
    /// in `Ready` a clean end is `Draining`, which closes when the queue is empty.
    pub fn on_eof(&mut self, now_ns: u64, hub: &mut SendHub, streams: &mut SelectionStreams) {
        match self.state {
            State::Closed | State::Draining => {}
            State::Ready => {
                let clean = match &mut self.reader {
                    Reader::Frames(reader) => reader.eof().is_ok(),
                    Reader::Lines(reader) => reader.eof().is_ok(),
                    Reader::None => false,
                };
                if clean {
                    self.state = State::Draining;
                    let corrs: Vec<u32> = self.subs.keys().copied().collect();
                    for corr in corrs {
                        if self.is_closed() {
                            break;
                        }
                        self.subs.remove(&corr);
                        streams.unsubscribe(SubKey {
                            conn: self.id,
                            corr,
                        });
                        let counts = hub.end_stream(self.id, corr);
                        let bytes =
                            end_ok(corr, counts.blocks, counts.dropped, EndReason::ClientEof);
                        self.push(hub, bytes, now_ns);
                    }
                    self.complete_draining(hub);
                } else {
                    self.finish_close(hub, None, None);
                }
            }
            State::Undecided | State::AwaitHello | State::JsonNoHello => {
                self.finish_close(hub, None, None);
            }
        }
        self.refresh_idle(now_ns, hub);
    }

    /// Queue one subscription tick (design S13). A closed or draining connection, or one that
    /// no longer owns `corr`, ignores it; a registry/connection mismatch never panics.
    pub fn push_tick(&mut self, corr: u32, tick: &LiveTick, now_ns: u64, hub: &mut SendHub) {
        if self.is_closed() || self.state == State::Draining || !self.subs.contains_key(&corr) {
            return;
        }
        hub.push_live(self.id, corr, tick, now_ns);
        self.timers.on_enqueue();
    }

    /// End a subscription the server ended (design S17). On a closed connection this only
    /// removes the connection-side key; the server releases the registry entry through `finish`.
    pub fn end_subscription(
        &mut self,
        corr: u32,
        code: Code,
        detail: &Detail,
        now_ns: u64,
        hub: &mut SendHub,
    ) {
        let removed = self.subs.remove(&corr).is_some();
        if self.is_closed() || !removed {
            return;
        }
        let counts = hub.end_stream(self.id, corr);
        let bytes = end_error(corr, counts.blocks, counts.dropped, code, detail);
        self.push(hub, bytes, now_ns);
    }

    /// The number of live subscriptions of this connection.
    #[must_use]
    pub fn subscriptions(&self) -> usize {
        self.subs.len()
    }

    /// The clock reached `now_ns` (R20): the hello deadline, then the request deadline (the
    /// reader's age), then idle; the first fatal outcome wins.
    pub fn poll(&mut self, now_ns: u64, hub: &mut SendHub) {
        if self.state == State::Closed {
            return;
        }
        let timer = self.timers.expired(now_ns);
        if timer == Some(Timer::Hello) {
            let frame = match self.reader {
                Reader::None => None,
                _ => Some(self.error_bytes(0, Code::Deadline, &Detail::Empty)),
            };
            self.finish_close(hub, Some(Code::Deadline), frame);
            return;
        }
        if let Some(code) = self.reader_deadline(now_ns) {
            self.close_reply(hub, code, 0, &Detail::Empty);
            return;
        }
        if timer == Some(Timer::Idle) {
            self.close_for(Code::Idle, hub);
            return;
        }
        self.refresh_idle(now_ns, hub);
    }

    /// The driver wrote `n` bytes of the head it was offered. This calls `SendHub::wrote`, counts
    /// the bytes (R2), completes `Draining` when the queue is empty, and re-arms idle.
    pub fn on_written(&mut self, n: usize, now_ns: u64, hub: &mut SendHub) {
        if self.state == State::Closed {
            return;
        }
        hub.wrote(self.id, n, now_ns);
        self.written = self
            .written
            .saturating_add(u64::try_from(n).unwrap_or(u64::MAX));
        if self.state == State::Draining {
            self.complete_draining(hub);
        }
        self.refresh_idle(now_ns, hub);
    }

    /// A server-originated close: the final frame is the `closing` EVT (binary) or the error
    /// line (JSON); an `Undecided` connection has no mode and closes silently.
    ///
    /// The contract (module doc): `code` is one of the three `closing` reasons, [`Code::Idle`],
    /// [`Code::SlowClient`] (the server calls it for the hub's `slow-client`) or
    /// [`Code::ShuttingDown`]; the conn does not check it.
    pub fn close_for(&mut self, code: Code, hub: &mut SendHub) {
        if self.state == State::Closed {
            return;
        }
        let frame = if matches!(self.reader, Reader::None) {
            None
        } else if matches!(self.reader, Reader::Lines(_)) {
            Some(error_line(0, code, &Detail::Empty))
        } else {
            Some(closing_evt(code))
        };
        self.finish_close(hub, Some(code), frame);
    }

    fn phase(&self) -> Phase {
        if self.state == State::Ready {
            Phase::Ready
        } else {
            Phase::BeforeHello
        }
    }

    fn is_json(&self) -> bool {
        matches!(self.reader, Reader::Lines(_))
    }

    fn at_boundary(&self) -> bool {
        match &self.reader {
            Reader::Frames(reader) => reader.buffered_len() == 0,
            Reader::Lines(reader) => reader.buffered_len() == 0,
            Reader::None => false,
        }
    }

    fn refresh_idle(&mut self, now_ns: u64, hub: &SendHub) {
        let idle = self.state == State::Ready
            && self.at_boundary()
            && hub.queued(self.id) == Pools::default()
            && self.subs.is_empty()
            && self.open_spans == 0;
        self.timers.on_idle_condition(idle, now_ns);
    }

    fn complete_draining(&mut self, hub: &mut SendHub) {
        if self.state == State::Draining && hub.queued(self.id) == Pools::default() {
            self.finish_close(hub, None, None);
        }
    }

    fn reader_deadline(&mut self, now_ns: u64) -> Option<Code> {
        match &mut self.reader {
            Reader::Frames(reader) => {
                let polled = reader.poll(now_ns);
                polled.err().map(|error| frame_fail(&error))
            }
            Reader::Lines(reader) => reader.poll(now_ns).err().and_then(LineError::code),
            Reader::None => None,
        }
    }

    fn error_bytes(&self, corr: u32, code: Code, detail: &Detail) -> Vec<u8> {
        if self.is_json() {
            error_line(corr, code, detail)
        } else {
            rep_error(corr, code, detail)
        }
    }

    fn reply_bytes(&self, corr: u32, result: &str) -> Vec<u8> {
        let cap = self.limits.level.max_reply_bytes;
        if self.is_json() {
            rep_ok_line(corr, result, cap)
        } else {
            rep_ok(corr, result, cap)
        }
    }

    fn events_result(
        &self,
        corr: u32,
        args: &EventsArgs,
        window: &EventWindow,
        epoch: u64,
    ) -> String {
        let digest = events_digest(args);
        let limit = usize::try_from(args.limit).unwrap_or(usize::MAX);
        let count = limit.min(window.items.len());
        let mut encoded_items = String::new();
        let mut next = None;
        for index in 0..count {
            let item = &window.items[index];
            let next_after = window.items.get(index + 1).map(|item| item.ordinal);
            let cursor = next_after.map(|ordinal| events_cursor(epoch, digest, ordinal));
            let mut encoded = String::new();
            push_event_item(&mut encoded, item);
            let comma = usize::from(!encoded_items.is_empty());
            let suffix = events_suffix(window, cursor.as_deref());
            let result_len = "{\"events\":["
                .len()
                .saturating_add(encoded_items.len())
                .saturating_add(comma)
                .saturating_add(encoded.len())
                .saturating_add(suffix.len());
            if events_reply_len(result_len, corr, self.is_json())
                > usize::try_from(self.limits.level.max_reply_bytes).unwrap_or(usize::MAX)
            {
                next = Some(events_cursor(epoch, digest, item.ordinal));
                break;
            }
            if comma != 0 {
                encoded_items.push(',');
            }
            encoded_items.push_str(&encoded);
            next = cursor;
        }
        if count == 0 && window.items.is_empty() {
            next = None;
        }
        let suffix = events_suffix(window, next.as_deref());
        let mut result = String::from("{\"events\":[");
        result.push_str(&encoded_items);
        result.push_str(&suffix);
        result
    }

    /// A fatal outcome that answers a request or a reader error: the final frame is the REP (or
    /// error line).
    fn close_reply(&mut self, hub: &mut SendHub, code: Code, corr: u32, detail: &Detail) {
        let frame = self.error_bytes(corr, code, detail);
        self.finish_close(hub, Some(code), Some(frame));
    }

    /// Queue one frame on the control queue; a refused frame is `slow-client`.
    fn push(&mut self, hub: &mut SendHub, bytes: Vec<u8>, now_ns: u64) {
        self.timers.on_enqueue();
        if let Err(Close::SlowClient) = hub.push_control(self.id, Arc::from(bytes), now_ns) {
            self.close_for(Code::SlowClient, hub);
        }
    }

    /// `Closing` (R1, R2): drain the unpinned entries; when no head is partly written the final
    /// bytes are `frame`, preceded by the server prefix when it is queued and nothing was
    /// written; then the conn is closed. A silent close (`frame` is `None`) has no final bytes.
    fn finish_close(&mut self, hub: &mut SendHub, code: Option<Code>, frame: Option<Vec<u8>>) {
        if self.state == State::Closed {
            return;
        }
        hub.drain_unpinned(self.id);
        if !hub.head_partial(self.id) {
            if let Some(frame) = frame {
                let mut bytes = Vec::new();
                if self.prefix_queued && self.written == 0 {
                    bytes.extend_from_slice(&server_prefix(SERVER_MAJOR, self.minor));
                }
                bytes.extend_from_slice(&frame);
                self.final_bytes = Some(bytes);
            }
        }
        self.close_code = code;
        self.state = State::Closed;
        self.reader = Reader::None;
    }

    fn on_undecided(
        &mut self,
        bytes: &[u8],
        now_ns: u64,
        hub: &mut SendHub,
        backend: &mut dyn Backend,
        streams: &mut SelectionStreams,
    ) {
        match self.detect.feed(bytes) {
            Detect::Need => {}
            Detect::Unknown => {
                let line = unknown_mode_line();
                self.finish_close(hub, Some(Code::UnknownMode), Some(line));
            }
            Detect::Json { at } => {
                if self.level.serves_blocks() {
                    let line = error_line(0, Code::BinaryRequired, &Detail::Empty);
                    self.finish_close(hub, Some(Code::BinaryRequired), Some(line));
                    return;
                }
                let completion = ms_to_ns(self.limits.level.request_deadline_ms);
                let reader = LineReader::new(self.limits.level.max_request_bytes, completion);
                self.reader = Reader::Lines(reader);
                self.state = State::JsonNoHello;
                let rest = bytes.get(at..).unwrap_or_default();
                self.on_stream(rest, now_ns, hub, backend, streams);
            }
            Detect::Binary { prefix, at } => {
                let rest = bytes.get(at..).unwrap_or_default();
                self.on_prefix(prefix, rest, now_ns, hub, backend, streams);
            }
        }
    }

    /// The 8 prefix bytes are in (WIRE.md section 1.4): a major other than ours gets the prefix
    /// alone (R2); otherwise the negotiated reply prefix is the first queued frame.
    fn on_prefix(
        &mut self,
        prefix: [u8; 8],
        rest: &[u8],
        now_ns: u64,
        hub: &mut SendHub,
        backend: &mut dyn Backend,
        streams: &mut SelectionStreams,
    ) {
        let [_, _, _, _, a0, a1, b0, b1] = prefix;
        let major = u16::from_le_bytes([a0, a1]);
        let minor = u16::from_le_bytes([b0, b1]);
        if major != SERVER_MAJOR {
            let alone = server_prefix(SERVER_MAJOR, SERVER_MINOR).to_vec();
            self.finish_close(hub, Some(Code::UnsupportedVersion), Some(alone));
            return;
        }
        self.client_prefix = Prefix { major, minor };
        self.minor = minor.min(SERVER_MINOR);
        let completion = ms_to_ns(self.limits.level.request_deadline_ms);
        let reader = FrameReader::new(self.limits.level.max_request_bytes, completion);
        self.reader = Reader::Frames(reader);
        self.state = State::AwaitHello;
        self.prefix_queued = true;
        let reply = server_prefix(SERVER_MAJOR, self.minor).to_vec();
        self.push(hub, reply, now_ns);
        self.on_stream(rest, now_ns, hub, backend, streams);
    }

    fn on_stream(
        &mut self,
        bytes: &[u8],
        now_ns: u64,
        hub: &mut SendHub,
        backend: &mut dyn Backend,
        streams: &mut SelectionStreams,
    ) {
        if bytes.is_empty() || self.state == State::Closed {
            return;
        }
        let batch = match &mut self.reader {
            Reader::Frames(reader) => frame_batch(reader.feed(bytes, now_ns)),
            Reader::Lines(reader) => line_batch(reader.feed(bytes, now_ns)),
            Reader::None => return,
        };
        for input in &batch.inputs {
            if self.state == State::Closed {
                return;
            }
            self.dispatch(input, now_ns, hub, backend, streams);
        }
        if self.state == State::Closed {
            return;
        }
        if let Some(code) = batch.error {
            self.close_reply(hub, code, 0, &Detail::Empty);
        }
    }

    fn classify_input(&self, input: &Input, hub: &SendHub) -> Result<Dispatch, Refusal> {
        let corr_in_use = match input {
            Input::Frame(frame) => self.subs.contains_key(&frame.header.corr),
            Input::Line(_) => false,
        };
        let facts = ConnFacts {
            corr_in_use,
            query_occupied: hub.queued(self.id).query != 0,
            interval: self.limits.interval_rule(),
        };
        match input {
            Input::Frame(frame) => {
                let header = frame.header;
                let payload = frame.bytes.get(FRAME_HEADER_LEN..).unwrap_or_default();
                classify_with(
                    &Query {
                        frame_type: header.frame_type,
                        corr: header.corr,
                        mode: Mode::Binary,
                        phase: self.phase(),
                        level: self.level,
                        prefix: self.client_prefix,
                        payload,
                    },
                    &facts,
                )
            }
            Input::Line(line) => classify_with(
                &Query {
                    frame_type: FrameType::Req,
                    corr: 0,
                    mode: Mode::Json,
                    phase: self.phase(),
                    level: self.level,
                    prefix: self.client_prefix,
                    payload: line,
                },
                &facts,
            ),
        }
    }

    fn dispatch(
        &mut self,
        input: &Input,
        now_ns: u64,
        hub: &mut SendHub,
        backend: &mut dyn Backend,
        streams: &mut SelectionStreams,
    ) {
        let first_line = self.state == State::JsonNoHello;
        match self.classify_input(input, hub) {
            Ok(dispatch) => self.execute(&dispatch, now_ns, hub, backend, streams),
            Err(refusal) => self.refuse(&refusal, now_ns, hub),
        }
        if first_line && self.state == State::JsonNoHello {
            self.state = State::Ready;
            self.timers.on_hello_complete();
        }
    }

    fn refuse(&mut self, refusal: &Refusal, now_ns: u64, hub: &mut SendHub) {
        if refusal.fatal() {
            self.close_reply(hub, refusal.code, refusal.corr, &refusal.detail);
        } else {
            let bytes = self.error_bytes(refusal.corr, refusal.code, &refusal.detail);
            self.push(hub, bytes, now_ns);
        }
    }

    fn execute(
        &mut self,
        dispatch: &Dispatch,
        now_ns: u64,
        hub: &mut SendHub,
        backend: &mut dyn Backend,
        streams: &mut SelectionStreams,
    ) {
        if self.is_closed() {
            return;
        }
        let result = match &dispatch.call {
            Call::Hello(args) => {
                self.minor = args.minor.min(SERVER_MINOR);
                let identity = backend.identity();
                hello_result(self.level, self.minor, &identity, &self.limits)
            }
            Call::Status => {
                let queued = hub.global_pools();
                let dropped = hub.dropped_blocks();
                status_json(&backend.identity(), &backend.status(), queued, dropped)
            }
            Call::Current(selection) => {
                let block = match self.current_block(selection, backend) {
                    Ok(block) => block,
                    Err((code, detail)) => {
                        let bytes = self.error_bytes(dispatch.corr, code, &detail);
                        self.push(hub, bytes, now_ns);
                        return;
                    }
                };
                if !hub.query_room(self.id, block.len()) {
                    self.budget(dispatch.corr, Pool::Query, now_ns, hub);
                    return;
                }
                let result = current_result(self.limits.probe.tick_ms);
                let bytes = self.reply_bytes(dispatch.corr, &result);
                self.push(hub, bytes, now_ns);
                if self.is_closed() {
                    return;
                }
                let outcome = hub.push_query(self.id, dispatch.corr, block, now_ns);
                debug_assert!(
                    matches!(&outcome, QueryOutcome::Queued),
                    "query_room was true immediately before push_query"
                );
                if let QueryOutcome::Backpressure(_) = outcome {
                    let bytes = end_error(dispatch.corr, 0, 0, Code::Internal, &Detail::Empty);
                    self.push(hub, bytes, now_ns);
                    return;
                }
                let counts = hub.end_stream(self.id, dispatch.corr);
                let bytes = end_ok(
                    dispatch.corr,
                    counts.blocks,
                    counts.dropped,
                    EndReason::Complete,
                );
                self.push(hub, bytes, now_ns);
                return;
            }
            Call::Subscribe(args) => {
                let cap =
                    usize::try_from(self.limits.level.max_subscriptions).unwrap_or(usize::MAX);
                if self.subs.len() >= cap {
                    self.budget(dispatch.corr, Pool::Subscriptions, now_ns, hub);
                    return;
                }
                let every = args
                    .interval_ms
                    .checked_div(self.limits.probe.tick_ms)
                    .filter(|value| *value > 0);
                let Some(every) = every else {
                    let bytes = self.error_bytes(dispatch.corr, Code::Internal, &Detail::Empty);
                    self.push(hub, bytes, now_ns);
                    return;
                };
                let caps = SelectionCaps {
                    per_uid: self.limits.level.max_uid_selections,
                    per_probe: self.limits.level.max_selections,
                };
                let key = SubKey {
                    conn: self.id,
                    corr: dispatch.corr,
                };
                match streams.subscribe(key, self.peer.uid, every, &args.selection, caps) {
                    Err(crate::registry::Refused::Budget(pool)) => {
                        self.budget(dispatch.corr, pool, now_ns, hub);
                        return;
                    }
                    Err(_) => {
                        let bytes = self.error_bytes(dispatch.corr, Code::Internal, &Detail::Empty);
                        self.push(hub, bytes, now_ns);
                        return;
                    }
                    Ok(admit) => {
                        let sel_id = match admit {
                            crate::registry::Admit::Joined(id)
                            | crate::registry::Admit::Created(id) => id,
                        };
                        self.subs.insert(dispatch.corr, sel_id);
                    }
                }
                let result = subscribe_result(dispatch.corr, args.interval_ms);
                let bytes = self.reply_bytes(dispatch.corr, &result);
                self.push(hub, bytes, now_ns);
                return;
            }
            Call::Unsubscribe(sub) => {
                if !self.subs.contains_key(sub) {
                    let bytes = self.error_bytes(dispatch.corr, Code::NotFound, &Detail::Empty);
                    self.push(hub, bytes, now_ns);
                    return;
                }
                self.subs.remove(sub);
                streams.unsubscribe(SubKey {
                    conn: self.id,
                    corr: *sub,
                });
                let bytes = self.reply_bytes(dispatch.corr, UNSUBSCRIBE_RESULT);
                self.push(hub, bytes, now_ns);
                if self.is_closed() {
                    return;
                }
                let counts = hub.end_stream(self.id, *sub);
                let bytes = end_ok(*sub, counts.blocks, counts.dropped, EndReason::Unsubscribed);
                self.push(hub, bytes, now_ns);
                return;
            }
            Call::Annotate(args) => match backend.annotate(self.id, &self.peer, args) {
                Ok(reply) => {
                    match args.kind {
                        AnnotateKind::Begin => {
                            self.open_spans = self.open_spans.saturating_add(1);
                        }
                        AnnotateKind::End => {
                            self.open_spans = self.open_spans.saturating_sub(1);
                        }
                        AnnotateKind::Point => {}
                    }
                    annotate_result(reply.t_mono_ns, reply.span)
                }
                Err(refusal) => {
                    let (code, detail) = match refusal {
                        AnnotateRefusal::RateLimited { retry_after_ms } => {
                            (Code::RateLimited, Detail::RetryAfterMs(retry_after_ms))
                        }
                        AnnotateRefusal::Budget => (Code::Budget, Detail::Pool(Pool::Spans)),
                        AnnotateRefusal::SpanUnknown => (Code::SpanUnknown, Detail::Empty),
                        AnnotateRefusal::Internal => (Code::Internal, Detail::Empty),
                    };
                    let bytes = self.error_bytes(dispatch.corr, code, &detail);
                    self.push(hub, bytes, now_ns);
                    return;
                }
            },
            Call::Events(args) => {
                let identity = backend.identity();
                let digest = events_digest(args);
                if let Some(cursor) = args.cursor.as_deref() {
                    let valid =
                        parse_events_cursor(cursor).is_some_and(|(epoch, found_digest, _)| {
                            epoch == identity.epoch && found_digest == digest
                        });
                    if !valid {
                        let bytes =
                            self.error_bytes(dispatch.corr, Code::CursorInvalid, &Detail::Empty);
                        self.push(hub, bytes, now_ns);
                        return;
                    }
                }
                let window = match backend.events(args) {
                    Ok(window) => window,
                    Err(refusal) => {
                        let code = match refusal {
                            PageRefusal::CursorInvalid => Code::CursorInvalid,
                            PageRefusal::Internal => Code::Internal,
                        };
                        let bytes = self.error_bytes(dispatch.corr, code, &Detail::Empty);
                        self.push(hub, bytes, now_ns);
                        return;
                    }
                };
                let result = self.events_result(dispatch.corr, args, &window, identity.epoch);
                let bytes = self.reply_bytes(dispatch.corr, &result);
                self.push(hub, bytes, now_ns);
                return;
            }
        };
        let bytes = self.reply_bytes(dispatch.corr, &result);
        self.push(hub, bytes, now_ns);
        let hello = matches!(dispatch.call, Call::Hello(_));
        if hello && matches!(self.state, State::AwaitHello | State::JsonNoHello) {
            self.state = State::Ready;
            self.timers.on_hello_complete();
        }
    }

    /// Dispatch a subscription directly in server tests before P5b-2d enables its wire verb.
    #[cfg(test)]
    pub(crate) fn subscribe_for_server_test(
        &mut self,
        corr: u32,
        selection: Selection,
        now_ns: u64,
        hub: &mut SendHub,
        backend: &mut dyn Backend,
        streams: &mut SelectionStreams,
    ) {
        self.execute(
            &Dispatch {
                corr,
                call: Call::Subscribe(crate::args::SubscribeArgs {
                    selection,
                    interval_ms: self.limits.probe.tick_ms,
                }),
            },
            now_ns,
            hub,
            backend,
            streams,
        );
    }

    fn budget(&mut self, corr: u32, pool: Pool, now_ns: u64, hub: &mut SendHub) {
        let bytes = self.error_bytes(corr, Code::Budget, &Detail::Pool(pool));
        self.push(hub, bytes, now_ns);
    }

    fn current_block(
        &self,
        selection: &Selection,
        backend: &dyn Backend,
    ) -> Result<Arc<[u8]>, (Code, Detail)> {
        let data = backend
            .current(selection, self.limits.probe.max_rows)
            .map_err(|error| {
                let detail = Detail::Pool(Pool::Rows);
                match error {
                    crate::backend::BackendError::TooManyRows => (Code::Budget, detail),
                    crate::backend::BackendError::Unavailable => (Code::Internal, Detail::Empty),
                }
            })?;
        let bounds = BlockBounds {
            max_rows: self.limits.probe.max_rows,
            max_block_bytes: self.limits.probe.max_block_bytes,
        };
        query_block(data, bounds).map_err(|error| error.refusal())
    }
}

#[cfg(test)]
mod tests {
    use std::cell::Cell;
    use std::collections::{BTreeMap, VecDeque};
    use std::sync::Arc;

    use crate::hub::{ConnId, LiveOutcome, QueryOutcome, QueueLimits, SendHub, TickForms};
    use hostobs_proto::envelope::{check_end, check_rep, members_up_to};
    use hostobs_proto::frame::{decode_block_with, FrameType};
    use hostobs_proto::quality;
    use hostobs_proto::stream_dicts::StreamDicts;

    use super::{Call, Conn, Dispatch, Peer, Pools, Reader, State, SERVER_MINOR};
    use crate::args::SubscribeArgs;
    use crate::backend::{
        AnnotateRefusal, AnnotateReply, Backend, BackendError, EventCtxKeyView, EventItem,
        EventKindView, EventOutcomeView, EventPeerView, EventWindow, FixedBackend, Identity,
        StatusInfo, TickData, TickInfo, TierInfo,
    };
    use crate::codes::{Code, Detail};
    use crate::encode::{
        closing_evt, control_frame, error_line, rep_error, server_prefix, unknown_mode_line,
    };
    use crate::limits::{ConnLimits, Level, LevelLimits, ProbeLimits};
    use crate::registry::{Delivery, SelectionStreams, StreamLimits};
    use crate::selection::{Sel, Selection};
    use crate::tick::BlockBounds;

    const MS: u64 = 1_000_000;
    const HELLO_ARGS: &str = r#"{"client":"t","proto":{"major":1,"minor":2}}"#;
    const BEGIN_ARGS: &str = concat!(
        r#"{"kind":"begin","name":"run","key":null,"ctx":{},"#,
        r#""outcome":null,"code":null,"attrs":{}}"#,
    );
    const END_ARGS: &str = concat!(
        r#"{"kind":"end","name":"run","key":null,"ctx":{},"#,
        r#""outcome":"ok","code":null,"attrs":{}}"#,
    );

    fn req(corr: u32, verb: &str) -> Vec<u8> {
        let args = if verb == "hello" { HELLO_ARGS } else { "{}" };
        let json = format!("{{\"verb\":\"{verb}\",\"args\":{args}}}");
        control_frame(FrameType::Req, corr, json.as_bytes())
    }

    fn raw_req(corr: u32, json: &str) -> Vec<u8> {
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

    /// `(type, corr, whole frame)` of every well-formed frame in `bytes`.
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

    fn payload(frame: &[u8]) -> &[u8] {
        frame.get(12..).unwrap_or_default()
    }

    fn selection(inc: u64) -> Selection {
        Selection::new(vec![Sel::Inc(inc)], vec![String::from("m")]).expect("selection")
    }

    fn is_error(frame: &[u8], code: Code) -> bool {
        let body = text(payload(frame));
        let rep = check_rep(body.as_bytes(), false).expect("REP");
        assert!(!rep.ok, "{body}");
        body.contains(&format!("\"code\":\"{}\"", code.name()))
    }

    fn end_info(frame: &[u8]) -> (bool, u32, u32, Option<String>) {
        let body = payload(frame);
        let end = check_end(body).expect("END");
        let reason = end.reason.map(|range| {
            String::from_utf8(body.get(range).expect("reason range").to_vec()).expect("utf-8")
        });
        (end.ok, end.blocks, end.dropped, reason)
    }

    fn assert_end_ok(frame: &[u8], blocks: u32, dropped: u32, reason: &str) {
        assert_eq!(
            end_info(frame),
            (true, blocks, dropped, Some(reason.to_string()))
        );
    }

    fn assert_end_error(frame: &[u8], blocks: u32, dropped: u32, code: Code) {
        let (ok, got_blocks, got_dropped, _) = end_info(frame);
        assert!(!ok);
        assert_eq!((got_blocks, got_dropped), (blocks, dropped));
        assert!(is_error_payload(payload(frame), code));
    }

    fn is_error_payload(body: &[u8], code: Code) -> bool {
        text(body).contains(&format!("\"code\":\"{}\"", code.name()))
    }

    fn queue_limits(limits: &ConnLimits) -> QueueLimits {
        limits.queue_limits()
    }

    fn drain_hub(hub: &mut SendHub, id: ConnId, now_ns: u64) -> Vec<u8> {
        let mut out = Vec::new();
        while let Some([first, second]) = hub.head(id, now_ns) {
            let len = first.len().saturating_add(second.len());
            out.extend_from_slice(first);
            out.extend_from_slice(second);
            hub.wrote(id, len, now_ns);
        }
        out
    }

    struct CountingBackend {
        inner: FixedBackend,
        current_calls: Cell<usize>,
        /// `annotate` answers in order; the fixed backend answers once this is empty.
        annotate_script: VecDeque<Result<AnnotateReply, AnnotateRefusal>>,
    }

    impl CountingBackend {
        fn new() -> CountingBackend {
            CountingBackend {
                inner: FixedBackend::new(),
                current_calls: Cell::new(0),
                annotate_script: VecDeque::new(),
            }
        }
    }

    impl Backend for CountingBackend {
        fn identity(&self) -> Identity {
            self.inner.identity()
        }

        fn status(&self) -> StatusInfo {
            self.inner.status()
        }

        fn current(&self, selection: &Selection, max_rows: u32) -> Result<TickData, BackendError> {
            self.current_calls
                .set(self.current_calls.get().saturating_add(1));
            self.inner.current(selection, max_rows)
        }

        fn annotate(
            &mut self,
            conn: ConnId,
            peer: &Peer,
            args: &crate::backend::AnnotateArgs,
        ) -> Result<AnnotateReply, AnnotateRefusal> {
            match self.annotate_script.pop_front() {
                Some(answer) => answer,
                None => self.inner.annotate(conn, peer, args),
            }
        }

        fn events(
            &mut self,
            args: &crate::backend::EventsArgs,
        ) -> Result<crate::backend::EventWindow, crate::backend::PageRefusal> {
            self.inner.events(args)
        }

        fn connection_closed(&mut self, conn: ConnId) {
            self.inner.connection_closed(conn);
        }
    }

    fn send_annotate(rig: &mut Rig, backend: &mut CountingBackend, corr: u32, args: &str) {
        let input = line(corr, "annotate", args);
        rig.conn
            .on_bytes(&input, rig.now, &mut rig.hub, backend, &mut rig.streams);
        rig.pump(usize::MAX);
    }

    #[test]
    fn annotate_replies_with_the_stamp_and_maps_every_refusal_to_its_wire_error() {
        let args = r#"{"kind":"point","name":"run","key":null,"ctx":{},"outcome":null,"code":null,"attrs":{}}"#;
        let mut rig = ready_json(Level::Observer);
        let mut backend = CountingBackend::new();
        backend.annotate_script.extend([
            Ok(AnnotateReply {
                t_mono_ns: 41,
                span: Some(3),
            }),
            Ok(AnnotateReply {
                t_mono_ns: 42,
                span: None,
            }),
            Err(AnnotateRefusal::RateLimited { retry_after_ms: 20 }),
            Err(AnnotateRefusal::Budget),
            Err(AnnotateRefusal::SpanUnknown),
        ]);
        // The sixth request finds the script empty and the fixed backend refuses it.
        let expected: [&[&str]; 6] = [
            &[r#""t_mono_ns":41,"span":3"#],
            &[r#""t_mono_ns":42,"span":null"#],
            &[r#""code":"rate-limited""#, r#""retry_after_ms":20"#],
            &[r#""code":"budget""#, r#""pool":"spans""#],
            &[r#""code":"span-unknown""#],
            &[r#""code":"internal""#],
        ];
        for (corr, _) in (2_u32..).zip(expected) {
            rig.conn.on_bytes(
                &line(corr, "annotate", args),
                rig.now,
                &mut rig.hub,
                &mut backend,
                &mut rig.streams,
            );
            rig.pump(usize::MAX);
        }
        assert_eq!(rig.conn.state(), State::Ready);
        let out = text(&rig.finish());
        let replies: Vec<&str> = out.lines().collect();
        assert_eq!(replies.len(), expected.len(), "{out}");
        for ((corr, reply), needles) in (2_u32..).zip(replies).zip(expected) {
            assert!(reply.contains(&format!("\"corr\":{corr},")), "{reply}");
            for needle in needles {
                assert!(reply.contains(needle), "{needle} in {reply}");
            }
        }
    }

    struct Rig {
        hub: SendHub,
        conn: Conn,
        backend: FixedBackend,
        streams: SelectionStreams,
        limits: ConnLimits,
        now: u64,
        out: Vec<u8>,
    }

    impl Rig {
        fn new(level: Level) -> Rig {
            Rig::with(
                level,
                ConnLimits::new(level, LevelLimits::for_level(level), ProbeLimits::new()),
            )
        }

        fn with(level: Level, limits: ConnLimits) -> Rig {
            let mut hub = SendHub::new(queue_limits(&limits));
            let id = hub.add(0);
            let peer = Peer {
                uid: 1000,
                pid: Some(7),
                comm: None,
                cgroup: None,
            };
            let conn = Conn::new(id, level, peer, &limits, 0);
            let streams = SelectionStreams::new(StreamLimits {
                bounds: limits.probe.block_bounds(),
            });
            Rig {
                hub,
                conn,
                backend: FixedBackend::new(),
                streams,
                limits,
                now: 0,
                out: Vec::new(),
            }
        }

        fn send(&mut self, bytes: &[u8]) {
            self.conn.on_bytes(
                bytes,
                self.now,
                &mut self.hub,
                &mut self.backend,
                &mut self.streams,
            );
        }

        fn eof(&mut self) {
            self.conn.on_eof(self.now, &mut self.hub, &mut self.streams);
        }

        fn ready(&mut self) {
            let mut input = client_prefix(2);
            input.extend_from_slice(&req(1, "hello"));
            self.send(&input);
            self.pump(usize::MAX);
            self.out.clear();
            assert_eq!(self.conn.state(), State::Ready);
        }

        fn execute(&mut self, corr: u32, call: Call) {
            let dispatch = Dispatch { corr, call };
            self.conn.execute(
                &dispatch,
                self.now,
                &mut self.hub,
                &mut self.backend,
                &mut self.streams,
            );
        }

        fn current(&mut self, corr: u32, selection: Selection) {
            self.execute(corr, Call::Current(selection));
        }

        fn subscribe(&mut self, corr: u32, selection: Selection, interval_ms: u32) {
            self.execute(
                corr,
                Call::Subscribe(SubscribeArgs {
                    selection,
                    interval_ms,
                }),
            );
        }

        fn streaming_tick(&mut self) {
            let deliveries = self.streams.on_tick(&self.backend);
            for delivery in deliveries {
                match delivery {
                    Delivery::Push { tick, to } => {
                        for key in to {
                            if key.conn == self.conn.id() {
                                self.conn
                                    .push_tick(key.corr, &tick, self.now, &mut self.hub);
                            }
                        }
                    }
                    Delivery::End { to, code, detail } => {
                        for key in to {
                            if key.conn == self.conn.id() {
                                self.conn.end_subscription(
                                    key.corr,
                                    code,
                                    &detail,
                                    self.now,
                                    &mut self.hub,
                                );
                            }
                        }
                    }
                }
            }
        }

        fn tick(&mut self, ns: u64) {
            self.now += ns;
            self.conn.poll(self.now, &mut self.hub);
        }

        /// Write at most `max` bytes of the queue the way a driver would.
        fn pump(&mut self, max: usize) -> usize {
            let mut total = 0;
            while total < max && !self.conn.is_closed() {
                let chunk: Vec<u8> = match self.hub.head(self.conn.id(), self.now) {
                    Some([a, b]) => [a, b].concat(),
                    None => break,
                };
                let take = chunk.len().min(max - total);
                if take == 0 {
                    break;
                }
                self.out.extend_from_slice(&chunk[..take]);
                self.conn.on_written(take, self.now, &mut self.hub);
                total += take;
            }
            total
        }

        /// Everything the peer would receive: the queue, then the final bytes.
        fn finish(&mut self) -> Vec<u8> {
            self.pump(usize::MAX);
            if let Some(last) = self.conn.take_final() {
                self.out.extend_from_slice(&last);
            }
            std::mem::take(&mut self.out)
        }

        fn queue_empty(&self) -> bool {
            self.hub.queued(self.conn.id()) == Pools::default()
        }
    }

    fn limits(level: Level) -> ConnLimits {
        ConnLimits::new(level, LevelLimits::for_level(level), ProbeLimits::new())
    }

    fn ready_bin(level: Level) -> Rig {
        let mut rig = Rig::new(level);
        let mut input = client_prefix(2);
        input.extend_from_slice(&req(1, "hello"));
        rig.send(&input);
        rig.pump(usize::MAX);
        rig.out.clear();
        assert_eq!(rig.conn.state(), State::Ready);
        rig
    }

    fn ready_json(level: Level) -> Rig {
        let mut rig = Rig::new(level);
        rig.send(&line(1, "hello", HELLO_ARGS));
        rig.pump(usize::MAX);
        rig.out.clear();
        assert_eq!(rig.conn.state(), State::Ready);
        rig
    }

    fn event_item(ordinal: u64, name: &str, t_mono_ns: u64, real_ns: i64) -> EventItem {
        EventItem {
            ordinal,
            kind: EventKindView::Point,
            name: name.to_owned(),
            key: name.to_owned(),
            ctx: BTreeMap::from([(EventCtxKeyView::Run, String::from("r"))]),
            outcome: Some(EventOutcomeView::Ok),
            code: None,
            attrs: BTreeMap::new(),
            t_mono_ns,
            real_ns,
            issuer: String::from("uid:1000"),
            peer: EventPeerView::default(),
            span: None,
            inferred: false,
        }
    }

    fn events_req(corr: u32, from: i64, to: i64, limit: u32, cursor: &str) -> Vec<u8> {
        raw_req(
            corr,
            &format!(
                r#"{{"verb":"events","args":{{"from_real_ns":{from},"to_real_ns":{to},"names":[],"limit":{limit},"cursor":{cursor}}}}}"#
            ),
        )
    }

    fn event_reply(rig: &mut Rig) -> String {
        let replies = frames(&rig.finish());
        replies
            .into_iter()
            .find(|(kind, _, _)| *kind == FrameType::Rep as u8)
            .map(|(_, _, payload)| payload)
            .expect("events REP")
    }

    fn page_cursor(reply: &str) -> Option<String> {
        let start = reply.find("\"next\":\"")? + "\"next\":\"".len();
        let end = reply[start..].find('"')? + start;
        Some(reply[start..end].to_owned())
    }

    #[test]
    fn events_pages_are_ordered_and_bind_cursors_to_epoch_and_window() {
        let mut rig = ready_bin(Level::Observe);
        rig.backend.event_window = EventWindow {
            items: vec![
                event_item(1, "second", 7, 11),
                event_item(0, "first", 7, 10),
                event_item(2, "third", 6, 12),
            ],
            oldest_real_ns: Some(10),
            displaced: 0,
            started_real_ns: 9,
        };
        rig.send(&events_req(2, 0, 20, 3, "null"));
        let first = event_reply(&mut rig);
        assert!(
            first.find("\"name\":\"third\"").unwrap() < first.find("\"name\":\"first\"").unwrap()
        );
        assert!(
            first.find("\"name\":\"first\"").unwrap() < first.find("\"name\":\"second\"").unwrap()
        );
        assert!(first.contains("\"next\":null"), "{first}");
        assert!(first.contains("\"oldest_real_ns\":10"), "{first}");
        assert!(first.contains("\"displaced\":0"), "{first}");
        assert!(first.contains("\"started_real_ns\":9"), "{first}");

        rig.send(&events_req(3, 0, 20, 1, "null"));
        let page_one = event_reply(&mut rig);
        let cursor = page_cursor(&page_one).expect("page cursor");
        assert!(cursor.ends_with(":0"), "{cursor}");

        rig.send(&events_req(4, 0, 20, 2, &format!("\"{cursor}\"")));
        let continued = event_reply(&mut rig);
        assert!(continued.contains("\"name\":\"first\""), "{continued}");
        assert!(continued.contains("\"name\":\"second\""), "{continued}");
        assert!(!continued.contains("\"name\":\"third\""), "{continued}");
        assert!(continued.contains("\"next\":null"), "{continued}");

        rig.send(&events_req(5, 1, 20, 2, &format!("\"{cursor}\"")));
        assert!(event_reply(&mut rig).contains("\"code\":\"cursor-invalid\""));

        let other_epoch = cursor.replacen("v1:1:", "v1:2:", 1);
        rig.send(&events_req(6, 0, 20, 2, &format!("\"{other_epoch}\"")));
        assert!(event_reply(&mut rig).contains("\"code\":\"cursor-invalid\""));
    }

    #[test]
    fn events_empty_window_has_ring_facts_and_null_oldest() {
        let mut rig = ready_bin(Level::Observe);
        rig.backend.event_window = EventWindow {
            items: vec![event_item(0, "outside", 1, 10)],
            oldest_real_ns: Some(10),
            displaced: 0,
            started_real_ns: 9,
        };
        rig.send(&events_req(2, 10, 10, 1, "null"));
        let equal = event_reply(&mut rig);
        assert!(equal.contains("\"events\":[]"), "{equal}");
        assert!(equal.contains("\"next\":null"), "{equal}");
        assert!(equal.contains("\"started_real_ns\":9"), "{equal}");

        rig.backend.event_window = EventWindow {
            oldest_real_ns: None,
            started_real_ns: 9,
            ..EventWindow::default()
        };
        rig.send(&events_req(3, 0, 20, 1, "null"));
        let empty = event_reply(&mut rig);
        assert!(empty.contains("\"oldest_real_ns\":null"), "{empty}");
        assert!(empty.contains("\"displaced\":0"), "{empty}");
    }

    #[test]
    fn events_rejects_a_continuation_ordinal_below_displaced_floor() {
        let mut rig = ready_bin(Level::Observe);
        rig.backend.event_window = EventWindow {
            items: vec![
                event_item(0, "first", 1, 10),
                event_item(1, "second", 2, 11),
            ],
            oldest_real_ns: Some(10),
            displaced: 0,
            started_real_ns: 9,
        };
        rig.send(&events_req(2, 0, 20, 1, "null"));
        let first = event_reply(&mut rig);
        let cursor = page_cursor(&first).expect("page cursor");
        assert!(cursor.ends_with(":1"), "{cursor}");
        rig.backend.event_window.displaced = 2;
        rig.send(&events_req(3, 0, 20, 1, &format!("\"{cursor}\"")));
        assert!(event_reply(&mut rig).contains("\"code\":\"cursor-invalid\""));
    }

    #[test]
    fn events_reply_reports_cumulative_displacement_and_backend_start_time() {
        let mut rig = ready_bin(Level::Observe);
        rig.backend.event_window = EventWindow {
            items: vec![event_item(7, "survivor", 1, 10)],
            oldest_real_ns: Some(10),
            displaced: 7,
            started_real_ns: 9,
        };
        rig.send(&events_req(2, 0, 20, 10, "null"));
        let reply = event_reply(&mut rig);
        assert!(reply.contains("\"displaced\":7"), "{reply}");
        assert!(reply.contains("\"oldest_real_ns\":10"), "{reply}");
        assert!(reply.contains("\"started_real_ns\":9"), "{reply}");
    }

    #[test]
    fn events_page_trims_before_reply_cap_and_keeps_connection_open() {
        let mut rig = ready_bin(Level::Observe);
        rig.backend.event_window = EventWindow {
            items: (0..60)
                .map(|ordinal| {
                    let mut item = event_item(ordinal, "worst", ordinal, 10 + ordinal as i64);
                    item.attrs
                        .insert(String::from("payload"), "x".repeat(9_400));
                    item
                })
                .collect(),
            oldest_real_ns: Some(10),
            displaced: 0,
            started_real_ns: 9,
        };
        rig.send(&events_req(2, 0, 100, 1_000, "null"));
        let reply = event_reply(&mut rig);
        assert_eq!(rig.conn.state(), State::Ready);
        assert!(reply.len() <= 448 * 1024, "reply is {} bytes", reply.len());
        assert!(reply.contains("\"next\":\"v1:1:"), "{reply}");
        assert!(reply.contains("\"name\":\"worst\""), "{reply}");
        assert!(reply.matches("\"kind\":").count() >= 46);
    }

    fn await_rig() -> Rig {
        let mut rig = Rig::new(Level::Observe);
        rig.send(&client_prefix(2));
        rig.pump(usize::MAX);
        rig.out.clear();
        assert_eq!(rig.conn.state(), State::AwaitHello);
        rig
    }

    #[test]
    fn conn_row_1_accept_arms_the_hello_deadline_and_starts_undecided() {
        let rig = Rig::new(Level::Observe);
        assert_eq!(rig.conn.state(), State::Undecided);
        assert_eq!(
            rig.conn.peer(),
            &Peer {
                uid: 1000,
                pid: Some(7),
                comm: None,
                cgroup: None,
            }
        );
        assert_eq!(rig.conn.level(), Level::Observe);
        assert_eq!(rig.conn.id(), ConnId(1));
        assert_eq!(rig.conn.bytes_written(), 0);
        assert!(!rig.conn.is_closed() && rig.queue_empty());
    }

    #[test]
    fn conn_row_2_brace_selects_json_and_the_first_line_is_the_hello() {
        let mut rig = Rig::new(Level::Control);
        rig.send(b"{");
        assert_eq!(rig.conn.state(), State::JsonNoHello);
        rig.send(&line(1, "hello", HELLO_ARGS)[1..]);
        assert_eq!(rig.conn.state(), State::Ready);
        let out = text(&rig.finish());
        let head = "{\"ok\":true,\"corr\":1,\"result\":{\"proto\":{\"major\":1";
        assert!(out.starts_with(head));
        assert!(out.contains("\"minor\":2}") && out.ends_with("}\n"));
        assert_eq!(rig.conn.minor(), 2);
    }

    #[test]
    fn conn_dw51_json_is_refused_exactly_at_the_block_levels() {
        let input = line(1, "hello", HELLO_ARGS);
        for level in Level::ALL {
            let mut rig = Rig::new(level);
            rig.send(b"{");
            if level.serves_blocks() {
                let expected = error_line(0, Code::BinaryRequired, &Detail::Empty);
                assert!(rig.conn.is_closed(), "{}", level.name());
                assert_eq!(rig.conn.close_code(), Some(Code::BinaryRequired));
                assert_eq!(rig.finish(), expected, "{}", level.name());
                assert!(rig.queue_empty(), "{}", level.name());
            } else {
                assert_eq!(rig.conn.state(), State::JsonNoHello, "{}", level.name());
                assert!(rig.queue_empty(), "{}", level.name());
                assert_eq!(rig.conn.take_final(), None, "{}", level.name());
                rig.send(&input[1..]);
                assert_eq!(rig.conn.state(), State::Ready, "{}", level.name());
                assert_eq!(rig.conn.close_code(), None);
                assert_eq!(rig.conn.take_final(), None);
                let reply = text(&rig.finish());
                assert_eq!(reply.lines().count(), 1, "{}", level.name());
                assert!(reply.starts_with("{\"ok\":true,\"corr\":1,"));
            }
        }
    }

    #[test]
    fn conn_row_3_prefix_progression_and_the_negotiated_minor() {
        for (client, negotiated) in [(0u16, 0u16), (1, 1), (5, 2)] {
            let mut rig = Rig::new(Level::Observe);
            let prefix = client_prefix(client);
            for byte in &prefix[..7] {
                rig.send(&[*byte]);
                assert_eq!(rig.conn.state(), State::Undecided);
                assert!(rig.queue_empty());
            }
            rig.send(&prefix[7..]);
            assert_eq!(rig.conn.state(), State::AwaitHello);
            assert_eq!(rig.finish(), server_prefix(1, negotiated).to_vec());
        }
    }

    #[test]
    fn conn_row_4_any_other_first_byte_is_the_unknown_mode_line() {
        for input in [&b"GET / HTTP"[..], b"HOX", b"\x00", b"H\x00"] {
            let mut rig = Rig::new(Level::Observe);
            rig.send(input);
            assert!(rig.conn.is_closed());
            assert_eq!(rig.conn.close_code(), Some(Code::UnknownMode));
            assert_eq!(rig.conn.take_final(), Some(unknown_mode_line()));
        }
    }

    #[test]
    fn conn_row_5_hello_timer_in_each_state_with_its_exact_reply() {
        let deadline = 5_000 * MS;
        let mut rig = Rig::new(Level::Observe);
        rig.send(b"HOB");
        rig.tick(deadline);
        assert!(!rig.conn.is_closed());
        rig.tick(1);
        assert!(rig.conn.is_closed());
        assert_eq!(rig.conn.close_code(), Some(Code::Deadline));
        assert_eq!(rig.conn.take_final(), None);
        let mut rig = await_rig();
        rig.tick(deadline + 1);
        let expected = rep_error(0, Code::Deadline, &Detail::Empty);
        assert_eq!(rig.conn.close_code(), Some(Code::Deadline));
        assert_eq!(rig.conn.take_final(), Some(expected.clone()));
        let mut unwritten = Rig::new(Level::Observe);
        unwritten.send(&client_prefix(2));
        unwritten.tick(deadline + 1);
        let mut both = server_prefix(1, 2).to_vec();
        both.extend_from_slice(&expected);
        assert_eq!(unwritten.conn.close_code(), Some(Code::Deadline));
        assert_eq!(unwritten.conn.take_final(), Some(both));
        let mut json = Rig::new(Level::Control);
        json.send(b"{");
        json.tick(deadline + 1);
        let line = error_line(0, Code::Deadline, &Detail::Empty);
        assert_eq!(json.conn.close_code(), Some(Code::Deadline));
        assert_eq!(json.conn.take_final(), Some(line));
    }

    #[test]
    fn conn_row_6_hello_in_await_hello_answers_and_becomes_ready() {
        let mut rig = Rig::new(Level::Observe);
        let mut input = client_prefix(2);
        input.extend_from_slice(&req(1, "hello"));
        rig.send(&input);
        assert_eq!(rig.conn.state(), State::Ready);
        let out = rig.finish();
        let len = u64::try_from(out.len()).unwrap_or(0);
        assert_eq!(rig.conn.bytes_written(), len);
        assert_eq!(out[..8], server_prefix(1, 2));
        let parsed = frames(&out[8..]);
        assert_eq!((parsed[0].0, parsed[0].1), (FrameType::Rep.code(), 1));
        let body = &parsed[0].2;
        assert!(body.contains("\"level\":\"observe\""));
        assert!(body.contains("\"epoch\":1"));
        let verbs =
            "\"verbs\":[\"hello\",\"status\",\"current\",\"subscribe\",\"unsubscribe\",\"events\"]";
        assert!(body.contains(verbs));
        let codes = "\"quality_codes\":[{\"code\":0,\"name\":\"ok\"}";
        assert!(body.contains(codes));
        assert!(body.contains("\"limits\":{\"max_frame_bytes\":16777216,"));
        let observer = text(&ready_hello_body(Level::Observer));
        assert!(observer.contains("\"verbs\":[\"hello\",\"annotate\"]"));
        assert!(observer.contains("\"max_subscriptions\":0"));
    }

    fn ready_hello_body(level: Level) -> Vec<u8> {
        let mut rig = Rig::new(level);
        let mut input = client_prefix(2);
        input.extend_from_slice(&req(1, "hello"));
        rig.send(&input);
        rig.finish()
    }

    #[test]
    fn conn_row_7_await_hello_other_verb_or_wrong_corr_is_fatal() {
        let mut rig = await_rig();
        rig.send(&req(7, "status"));
        assert!(rig.conn.is_closed());
        let expected = rep_error(7, Code::HelloRequired, &Detail::Empty);
        assert_eq!(rig.conn.close_code(), Some(Code::HelloRequired));
        assert_eq!(rig.conn.take_final(), Some(expected));
        for bad in [req(2, "hello"), req(0, "hello"), raw_req(0, "{")] {
            let mut rig = await_rig();
            rig.send(&bad);
            let expected = rep_error(0, Code::BadFrame, &Detail::Empty);
            assert_eq!(rig.conn.close_code(), Some(Code::BadFrame));
            assert_eq!(rig.conn.take_final(), Some(expected));
        }
    }

    #[test]
    fn conn_row_7_a_defective_hello_or_request_is_non_fatal_on_its_own_corr() {
        let mut rig = await_rig();
        let skewed = r#"{"verb":"hello","args":{"client":"t","proto":{"major":1,"minor":0}}}"#;
        rig.send(&raw_req(1, skewed));
        rig.send(&raw_req(5, "{"));
        assert_eq!(rig.conn.state(), State::AwaitHello);
        let parsed = frames(&rig.finish());
        assert_eq!(parsed[0].1, 1);
        assert!(parsed[0].2.contains("\"key\":\"proto.minor\""));
        assert_eq!(parsed[1].1, 5);
        assert!(parsed[1].2.contains("\"code\":\"bad-request\""));
        rig.send(&req(1, "hello"));
        assert_eq!(rig.conn.state(), State::Ready);
    }

    #[test]
    fn conn_row_8_json_first_line_is_handled_as_a_request_and_ends_the_hello_wait() {
        let mut rig = Rig::new(Level::Control);
        rig.send(&line(3, "status", "{}"));
        assert_eq!(rig.conn.state(), State::Ready);
        assert_eq!(rig.conn.minor(), 0);
        // The first complete line disarmed the hello timer, though `status` is no `hello`.
        rig.tick(5_001 * MS);
        assert!(!rig.conn.is_closed());
        assert_eq!(rig.conn.state(), State::Ready);
        rig.send(&line(4, "hello", HELLO_ARGS));
        let out = text(&rig.finish());
        assert!(out.contains("\"corr\":3,\"result\":{\"server\":"));
        let wrong = "\"corr\":4,\"error\":{\"code\":\"bad-request\"";
        assert!(out.contains(wrong));
        assert!(out.contains("\"detail\":{\"key\":\"verb\"}"));
        let mut garbage = Rig::new(Level::Control);
        garbage.send(b"{x\n");
        assert_eq!(garbage.conn.state(), State::Ready);
        garbage.tick(5_001 * MS);
        assert!(!garbage.conn.is_closed());
        assert_eq!(garbage.conn.state(), State::Ready);
        let reply = text(&garbage.finish());
        let key = "\"corr\":0,\"error\":{\"code\":\"bad-request\"";
        assert!(reply.contains(key));
        let mut major = Rig::new(Level::Control);
        let skew = r#"{"client":"t","proto":{"major":2,"minor":2}}"#;
        major.send(&line(1, "hello", skew));
        let expected = error_line(1, Code::UnsupportedVersion, &Detail::Empty);
        assert_eq!(major.conn.close_code(), Some(Code::UnsupportedVersion));
        assert_eq!(major.conn.take_final(), Some(expected));
    }

    #[test]
    fn conn_row_9_ready_dispatch_status_in_both_modes() {
        let mut bin = ready_bin(Level::Operator);
        bin.send(&req(2, "status"));
        let parsed = frames(&bin.finish());
        assert_eq!((parsed[0].0, parsed[0].1), (FrameType::Rep.code(), 2));
        let body = &parsed[0].2;
        let head = "{\"ok\":true,\"result\":{\"server\":\"hostobs-probe\"";
        assert!(body.starts_with(head));
        let queues = "\"queues\":{\"queued_bytes\":0,\"dropped_blocks\":0}";
        assert!(body.contains(queues));
        assert!(body.contains("\"clients\":{}"));
        assert!(body.contains("\"tiers\":[]"));
        let mut json = ready_json(Level::Control);
        json.send(&line(9, "status", "{}"));
        let out = text(&json.finish());
        let head = "{\"ok\":true,\"corr\":9,\"result\":{\"server\"";
        assert!(out.starts_with(head));
    }

    #[test]
    fn conn_row_9_ready_refusals_keep_the_connection() {
        let mut rig = ready_bin(Level::Observer);
        rig.send(&req(2, "status"));
        rig.send(&req(3, "current"));
        rig.send(&req(4, "bogus"));
        rig.send(&req(5, "hello"));
        let parsed = frames(&rig.finish());
        assert!(parsed[0].2.contains("\"code\":\"forbidden\""));
        assert!(parsed[1].2.contains("\"code\":\"forbidden\""));
        assert!(parsed[2].2.contains("\"code\":\"unknown-verb\""));
        assert!(parsed[3].2.contains("\"key\":\"verb\""));
        assert_eq!(rig.conn.state(), State::Ready);
    }

    #[test]
    fn conn_row_10_11_idle_arms_when_quiet_and_expires_strictly() {
        let mut rig = ready_bin(Level::Observe);
        rig.tick(300_000 * MS);
        assert!(!rig.conn.is_closed());
        rig.tick(1);
        assert!(rig.conn.is_closed());
        assert_eq!(rig.conn.close_code(), Some(Code::Idle));
        assert_eq!(rig.conn.take_final(), Some(closing_evt(Code::Idle)));
        let mut json = ready_json(Level::Control);
        json.tick(300_000 * MS + 1);
        assert_eq!(json.conn.close_code(), Some(Code::Idle));
        let expected = error_line(0, Code::Idle, &Detail::Empty);
        assert_eq!(json.conn.take_final(), Some(expected));
    }

    #[test]
    fn conn_idle_stays_disarmed_while_a_span_is_open() {
        let mut rig = ready_json(Level::Observer);
        let mut backend = CountingBackend::new();
        backend.annotate_script.push_back(Ok(AnnotateReply {
            t_mono_ns: 1,
            span: Some(1),
        }));
        send_annotate(&mut rig, &mut backend, 2, BEGIN_ARGS);
        rig.tick(400_000 * MS);
        assert_eq!(rig.conn.state(), State::Ready);
        assert_eq!(rig.conn.close_code(), None);
    }

    #[test]
    fn conn_idle_rearms_after_the_last_span_ends() {
        let mut rig = ready_json(Level::Observer);
        let mut backend = CountingBackend::new();
        backend.annotate_script.extend([
            Ok(AnnotateReply {
                t_mono_ns: 1,
                span: Some(1),
            }),
            Ok(AnnotateReply {
                t_mono_ns: 2,
                span: Some(1),
            }),
        ]);
        send_annotate(&mut rig, &mut backend, 2, BEGIN_ARGS);
        send_annotate(&mut rig, &mut backend, 3, END_ARGS);
        rig.tick(300_000 * MS);
        assert_eq!(rig.conn.state(), State::Ready);
        rig.tick(1);
        assert_eq!(rig.conn.close_code(), Some(Code::Idle));
    }

    #[test]
    fn conn_refused_begin_does_not_hold_the_idle_slot() {
        let mut rig = ready_json(Level::Observer);
        let mut backend = CountingBackend::new();
        backend
            .annotate_script
            .push_back(Err(AnnotateRefusal::Internal));
        send_annotate(&mut rig, &mut backend, 2, BEGIN_ARGS);
        rig.tick(300_000 * MS + 1);
        assert_eq!(rig.conn.close_code(), Some(Code::Idle));
    }

    #[test]
    fn conn_one_ended_span_keeps_idle_disarmed_while_another_remains_open() {
        let mut rig = ready_json(Level::Observer);
        let mut backend = CountingBackend::new();
        backend.annotate_script.extend([
            Ok(AnnotateReply {
                t_mono_ns: 1,
                span: Some(1),
            }),
            Ok(AnnotateReply {
                t_mono_ns: 2,
                span: Some(2),
            }),
            Ok(AnnotateReply {
                t_mono_ns: 3,
                span: Some(2),
            }),
        ]);
        send_annotate(&mut rig, &mut backend, 2, BEGIN_ARGS);
        send_annotate(&mut rig, &mut backend, 3, BEGIN_ARGS);
        send_annotate(&mut rig, &mut backend, 4, END_ARGS);
        rig.tick(400_000 * MS);
        assert_eq!(rig.conn.state(), State::Ready);
        assert_eq!(rig.conn.close_code(), None);
    }

    #[test]
    fn conn_row_10_a_received_byte_or_a_queued_frame_disarms_idle_and_the_deadline_wins() {
        let mut rig = ready_bin(Level::Observe);
        rig.send(&[0x40]);
        rig.tick(400_000 * MS);
        let expected = rep_error(0, Code::Deadline, &Detail::Empty);
        assert_eq!(rig.conn.close_code(), Some(Code::Deadline));
        assert_eq!(rig.conn.take_final(), Some(expected));
        let mut busy = ready_bin(Level::Observe);
        busy.send(&req(2, "status"));
        busy.tick(400_000 * MS);
        assert!(!busy.conn.is_closed());
        busy.pump(usize::MAX);
        busy.tick(300_000 * MS + 1);
        assert_eq!(busy.conn.close_code(), Some(Code::Idle));
    }

    #[test]
    fn conn_row_12_clean_eof_drains_the_queue_then_closes_without_a_frame() {
        let mut rig = ready_bin(Level::Observe);
        rig.send(&req(2, "status"));
        rig.eof();
        assert_eq!(rig.conn.state(), State::Draining);
        rig.pump(5);
        assert_eq!(rig.conn.state(), State::Draining);
        rig.pump(usize::MAX);
        assert!(rig.conn.is_closed());
        assert_eq!(rig.conn.close_code(), None);
        assert_eq!(rig.conn.take_final(), None);
        assert_eq!(frames(&rig.out).len(), 1);
        let mut empty = ready_json(Level::Control);
        empty.eof();
        assert!(empty.conn.is_closed());
        let mut late = ready_bin(Level::Observe);
        late.send(&req(2, "status"));
        late.eof();
        late.send(b"x");
        assert!(late.conn.is_closed());
        assert_eq!(late.conn.close_code(), None);
        assert_eq!(late.conn.take_final(), None);
    }

    #[test]
    fn conn_row_13_eof_before_ready_or_inside_a_request_closes_silently() {
        let mut undecided = Rig::new(Level::Observe);
        undecided.send(b"HO");
        let mut await_hello = await_rig();
        let mut json = Rig::new(Level::Control);
        json.send(b"{\"corr\":1");
        let mut inside = ready_bin(Level::Observe);
        inside.send(&req(2, "status")[..14]);
        let mut rigs = [&mut undecided, &mut await_hello, &mut json, &mut inside];
        for rig in &mut rigs {
            rig.eof();
            assert!(rig.conn.is_closed());
            assert_eq!(rig.conn.close_code(), None);
            assert_eq!(rig.conn.take_final(), None);
        }
    }

    #[test]
    fn conn_row_14_closing_writes_the_final_frame_only_without_a_partial_head() {
        let mut rig = ready_bin(Level::Observe);
        rig.send(&req(2, "status"));
        rig.pump(5);
        rig.conn.close_for(Code::SlowClient, &mut rig.hub);
        assert!(rig.conn.is_closed());
        assert_eq!(rig.conn.close_code(), Some(Code::SlowClient));
        assert_eq!(rig.conn.take_final(), None);
        let mut whole = ready_bin(Level::Observe);
        whole.send(&req(2, "status"));
        whole.conn.close_for(Code::ShuttingDown, &mut whole.hub);
        assert!(whole.queue_empty());
        let evt = closing_evt(Code::ShuttingDown);
        assert_eq!(whole.conn.close_code(), Some(Code::ShuttingDown));
        assert_eq!(whole.conn.take_final(), Some(evt));
        assert_eq!(whole.conn.take_final(), None);
        let mut json = ready_json(Level::Control);
        json.conn.close_for(Code::SlowClient, &mut json.hub);
        let expected = error_line(0, Code::SlowClient, &Detail::Empty);
        assert_eq!(json.conn.close_code(), Some(Code::SlowClient));
        assert_eq!(json.conn.take_final(), Some(expected));
        let mut early = Rig::new(Level::Observe);
        early.conn.close_for(Code::ShuttingDown, &mut early.hub);
        assert!(early.conn.is_closed() && early.conn.take_final().is_none());
        assert_eq!(early.conn.close_code(), Some(Code::ShuttingDown));
    }

    #[test]
    fn close_for_with_each_reason_gives_a_closing_evt() {
        for code in [Code::Idle, Code::SlowClient, Code::ShuttingDown] {
            let mut bin = ready_bin(Level::Observe);
            bin.conn.close_for(code, &mut bin.hub);
            assert!(bin.conn.is_closed() && bin.queue_empty());
            assert_eq!(bin.conn.close_code(), Some(code));
            assert_eq!(bin.conn.take_final(), Some(closing_evt(code)));
            let mut json = ready_json(Level::Control);
            json.conn.close_for(code, &mut json.hub);
            assert_eq!(json.conn.close_code(), Some(code));
            let expected = error_line(0, code, &Detail::Empty);
            assert_eq!(json.conn.take_final(), Some(expected));
            let mut again = ready_bin(Level::Observe);
            again.conn.close_for(code, &mut again.hub);
            again.conn.close_for(Code::Idle, &mut again.hub);
            assert_eq!(again.conn.close_code(), Some(code));
        }
    }

    #[test]
    fn conn_a_head_pinned_with_nothing_written_then_a_fatal_outcome_repeats_the_prefix() {
        let mut rig = Rig::new(Level::Observe);
        rig.send(&client_prefix(2));
        let id = rig.conn.id();
        let pinned = rig.hub.head(id, rig.now).map(|[a, b]| [a, b].concat());
        assert_eq!(pinned, Some(server_prefix(1, 2).to_vec()));
        assert!(!rig.hub.head_partial(id));
        rig.send(&[12, 0, 0, 0, 9, 0, 0, 0, 0, 0, 0, 0]);
        assert!(rig.conn.is_closed());
        assert_eq!(rig.conn.bytes_written(), 0);
        let mut expected = server_prefix(1, 2).to_vec();
        expected.extend_from_slice(&rep_error(0, Code::BadFrame, &Detail::Empty));
        assert_eq!(rig.conn.take_final(), Some(expected));
        // The hub still holds the pinned prefix: the final bytes carry it, so a driver that wrote
        // the hub head after `is_closed` would send the prefix twice (module doc).
        assert!(!rig.queue_empty());
        rig.conn.on_written(8, rig.now, &mut rig.hub);
        assert_eq!(rig.conn.bytes_written(), 0);
        assert_eq!(rig.pump(usize::MAX), 0);
        assert!(rig.out.is_empty());
    }

    #[test]
    fn conn_r2_prefix_and_a_bad_request_in_one_read_keep_the_handshake_whole() {
        let mut rig = Rig::new(Level::Observe);
        let mut input = client_prefix(2);
        input.extend_from_slice(&[12, 0, 0, 0, 9, 0, 0, 0, 0, 0, 0, 0]);
        rig.send(&input);
        assert!(rig.conn.is_closed() && rig.queue_empty());
        let mut expected = server_prefix(1, 2).to_vec();
        expected.extend_from_slice(&rep_error(0, Code::BadFrame, &Detail::Empty));
        assert_eq!(rig.conn.close_code(), Some(Code::BadFrame));
        assert_eq!(rig.conn.take_final(), Some(expected));
        let mut half = Rig::new(Level::Observe);
        half.send(&client_prefix(2));
        half.pump(3);
        assert_eq!(half.conn.bytes_written(), 3);
        half.send(&[12, 0, 0, 0, 9, 0, 0, 0, 0, 0, 0, 0]);
        assert!(half.conn.is_closed());
        assert_eq!(half.conn.close_code(), Some(Code::BadFrame));
        assert_eq!(half.conn.take_final(), None);
        assert_eq!(half.out, server_prefix(1, 2)[..3].to_vec());
    }

    #[test]
    fn conn_r2_a_major_mismatch_gets_the_server_prefix_alone() {
        let mut rig = Rig::new(Level::Observe);
        let wrong = [b'H', b'O', b'B', 1, 2, 0, 0, 0];
        rig.send(&wrong);
        assert!(rig.conn.is_closed() && rig.queue_empty());
        assert_eq!(rig.conn.close_code(), Some(Code::UnsupportedVersion));
        assert_eq!(rig.conn.take_final(), Some(server_prefix(1, 2).to_vec()));
    }

    #[test]
    fn conn_r1_frames_after_a_fatal_outcome_are_discarded() {
        let mut rig = ready_bin(Level::Observe);
        let mut input = req(0, "status");
        input.extend_from_slice(&req(2, "status"));
        rig.send(&input);
        assert!(rig.queue_empty());
        assert_eq!(rig.finish(), rep_error(0, Code::BadFrame, &Detail::Empty));
        assert_eq!(rig.conn.close_code(), Some(Code::BadFrame));
        let mut json = ready_json(Level::Control);
        let mut input = line(3, "hello", HELLO_ARGS);
        input.extend_from_slice(&line(4, "status", "{}"));
        json.send(&input);
        let out = text(&json.finish());
        assert!(out.contains("\"corr\":3") && out.contains("\"corr\":4"));
        assert_eq!(json.conn.state(), State::Ready);
    }

    #[test]
    fn conn_r6_to_r9_reader_errors_map_to_their_codes() {
        let mut big = ready_bin(Level::Observe);
        big.send(&[0x40, 0x9C, 0, 0, 1, 0, 0, 0, 5, 0, 0, 0]);
        let expected = rep_error(0, Code::FrameTooLarge, &Detail::Empty);
        assert_eq!(big.conn.close_code(), Some(Code::FrameTooLarge));
        assert_eq!(big.conn.take_final(), Some(expected));
        let mut line_big = ready_json(Level::Observer);
        let line_max = usize::try_from(LevelLimits::for_level(Level::Observer).max_request_bytes)
            .expect("line bound");
        line_big.send(&vec![b'a'; line_max.saturating_add(1)]);
        let expected = error_line(0, Code::FrameTooLarge, &Detail::Empty);
        assert_eq!(line_big.conn.close_code(), Some(Code::FrameTooLarge));
        assert_eq!(line_big.conn.take_final(), Some(expected));
        // A SECTIONED REQ (flag bit 0 on a non-BLOCK frame) is refused by `FrameHeader::parse`
        // inside the frame reader, so it takes this reader-error path (`bad-frame`); the conn
        // has no check of its own for it.
        let mut sectioned = ready_bin(Level::Observe);
        let mut frame = req(2, "status");
        frame[5] = 1;
        sectioned.send(&frame);
        let expected = rep_error(0, Code::BadFrame, &Detail::Empty);
        assert_eq!(sectioned.conn.close_code(), Some(Code::BadFrame));
        assert_eq!(sectioned.conn.take_final(), Some(expected));
    }

    #[test]
    fn conn_control_queue_overflow_is_slow_client_with_the_prefix_kept() {
        let mut limits = limits(Level::Observe);
        limits.level.control_queue_bytes = 600;
        let mut rig = Rig::with(Level::Observe, limits);
        let mut input = client_prefix(2);
        input.extend_from_slice(&req(1, "hello"));
        rig.send(&input);
        assert!(rig.conn.is_closed());
        assert_eq!(rig.conn.close_code(), Some(Code::SlowClient));
        let mut expected = server_prefix(1, 2).to_vec();
        expected.extend_from_slice(&closing_evt(Code::SlowClient));
        assert_eq!(rig.conn.take_final(), Some(expected));
    }

    #[test]
    fn conn_bytewise_delivery_gives_the_one_shot_reply_in_both_modes() {
        let mut binary = client_prefix(2);
        binary.extend_from_slice(&req(1, "hello"));
        binary.extend_from_slice(&req(2, "status"));
        let mut json = line(1, "hello", HELLO_ARGS);
        json.extend_from_slice(&line(2, "status", "{}"));
        for (level, input) in [(Level::Operator, binary), (Level::Control, json)] {
            let mut whole = Rig::new(level);
            whole.send(&input);
            let expected = whole.finish();
            let mut slow = Rig::new(level);
            for byte in &input {
                slow.send(&[*byte]);
                slow.pump(usize::MAX);
            }
            assert_eq!(slow.finish(), expected);
            assert!(expected.len() > 100 && !slow.conn.is_closed());
        }
    }

    fn hello_args(minor: u16) -> String {
        format!(r#"{{"client":"t","proto":{{"major":1,"minor":{minor}}}}}"#)
    }

    /// `(key, raw value text)` of every member of the JSON object `json`, in order.
    fn pairs(json: &str) -> Vec<(String, String)> {
        let bytes = json.as_bytes();
        let found = members_up_to(bytes, 32).expect("one object");
        let mut out = Vec::new();
        for member in &found {
            let key = text(&bytes[member.key.clone()]);
            out.push((key, text(&bytes[member.value.clone()])));
        }
        out
    }

    fn want(items: &[(&str, &str)]) -> Vec<(String, String)> {
        let mut out = Vec::new();
        for (key, value) in items {
            out.push((String::from(*key), String::from(*value)));
        }
        out
    }

    /// The raw value text of member `key` of the JSON object `json`.
    fn member(json: &str, key: &str) -> String {
        let found = pairs(json.trim_end());
        let hit = found.into_iter().find(|(name, _)| name.as_str() == key);
        hit.map(|(_, value)| value).expect("member")
    }

    fn keys_of(json: &str) -> Vec<String> {
        pairs(json).into_iter().map(|(key, _)| key).collect()
    }

    /// The objects of a flat array of flat objects, each with its braces.
    fn flat_objects(array: &str) -> Vec<String> {
        let inner = array.trim_start_matches("[{").trim_end_matches("}]");
        let parts = inner.split("},{");
        parts.map(|part| format!("{{{part}}}")).collect()
    }

    const HELLO_KEYS: [&str; 9] = [
        "proto",
        "level",
        "server",
        "boot_id",
        "epoch",
        "features",
        "verbs",
        "quality_codes",
        "limits",
    ];

    const STATUS_KEYS: [&str; 9] = [
        "server",
        "boot_id",
        "epoch",
        "started_real_ns",
        "uptime_ms",
        "tick",
        "clients",
        "queues",
        "tiers",
    ];

    const STATUS_SCALARS: [(&str, &str); 5] = [
        ("server", r#""hostobs-probe 9.9""#),
        ("boot_id", r#""11111111-2222-4333-8444-555555555555""#),
        ("epoch", "42"),
        ("started_real_ns", "1700000000123456789"),
        ("uptime_ms", "86400001"),
    ];

    /// A backend whose every answer differs from the default and from the others.
    fn rich_backend() -> FixedBackend {
        let mut backend = FixedBackend::new();
        backend.identity.server = String::from("hostobs-probe 9.9");
        backend.identity.boot_id = String::from("11111111-2222-4333-8444-555555555555");
        backend.identity.epoch = 42;
        backend.identity.features = vec![String::from("peak_reset"), String::from("damon")];
        backend.status.started_real_ns = 1_700_000_000_123_456_789;
        backend.status.uptime_ms = 86_400_001;
        backend.status.tick = TickInfo {
            seq: 77,
            last_mono_ns: 5_000_000_123,
            scan_ms: 31,
        };
        backend.status.clients = vec![
            (Level::Observe, 2),
            (Level::Operator, 0),
            (Level::Observer, 1),
        ];
        backend.status.dropped_blocks = 5;
        backend.status.tiers = vec![
            TierInfo {
                name: String::from("hot"),
                step_ms: 1_000,
                keep_s: 3_600,
                bytes: 123_456,
                cap_bytes: 1_048_576,
            },
            TierInfo {
                name: String::from("warm"),
                step_ms: 10_000,
                keep_s: 86_400,
                bytes: 7_654_321,
                cap_bytes: 8_388_608,
            },
        ];
        backend
    }

    /// A tick of `self.0` zero bytes in the dictionary form: the hub only counts its bytes.
    struct Forms(usize);

    impl TickForms for Forms {
        fn generation(&self) -> u32 {
            1
        }

        fn bare(&self) -> Option<Arc<[u8]>> {
            None
        }

        fn dict_form(&self) -> Arc<[u8]> {
            Arc::from(vec![0u8; self.0])
        }
    }

    struct FrameForm(Arc<[u8]>);

    impl TickForms for FrameForm {
        fn generation(&self) -> u32 {
            1
        }

        fn bare(&self) -> Option<Arc<[u8]>> {
            Some(Arc::clone(&self.0))
        }

        fn dict_form(&self) -> Arc<[u8]> {
            Arc::clone(&self.0)
        }
    }

    #[test]
    fn conn_hello_negotiates_the_minor_in_both_modes() {
        assert_eq!(SERVER_MINOR, 2);
        for client in [0u16, 1, 5] {
            let negotiated = client.min(SERVER_MINOR);
            let proto = format!("{{\"major\":1,\"minor\":{negotiated}}}");
            let args = hello_args(client);
            let hello = format!("{{\"verb\":\"hello\",\"args\":{args}}}");
            let mut bin = Rig::new(Level::Observe);
            let mut input = client_prefix(client);
            input.extend_from_slice(&raw_req(1, &hello));
            bin.send(&input);
            assert_eq!(bin.conn.state(), State::Ready);
            assert_eq!(bin.conn.minor(), negotiated);
            let out = bin.finish();
            assert_eq!(out[..8], server_prefix(1, negotiated));
            let parsed = frames(&out[8..]);
            let result = member(&parsed[0].2, "result");
            assert_eq!(member(&result, "proto"), proto);
            let mut json = Rig::new(Level::Control);
            json.send(&line(1, "hello", &args));
            assert_eq!(json.conn.minor(), negotiated);
            let reply = text(&json.finish());
            assert_eq!(member(&member(&reply, "result"), "proto"), proto);
        }
        let mut bare = Rig::new(Level::Control);
        bare.send(&line(1, "status", "{}"));
        assert_eq!(bare.conn.state(), State::Ready);
        assert_eq!(bare.conn.minor(), 0);
    }

    fn current_data(seq: u64) -> TickData {
        TickData::example(seq, &[1], &["m"])
    }

    fn current_rig(limits: ConnLimits, selection: Selection, data: TickData) -> Rig {
        let mut rig = Rig::with(Level::Observe, limits);
        rig.backend.current.insert(selection, Ok(data));
        rig
    }

    fn query_len(limits: &ConnLimits, data: &TickData) -> usize {
        crate::tick::query_block(
            data.clone(),
            BlockBounds {
                max_rows: limits.probe.max_rows,
                max_block_bytes: limits.probe.max_block_bytes,
            },
        )
        .expect("query block")
        .len()
    }

    #[test]
    fn conn_current_queued_replies_block_and_complete_in_fifo_order() {
        let sel = selection(1);
        let mut rig = current_rig(limits(Level::Observe), sel.clone(), current_data(5));
        rig.current(2, sel);
        let out = rig.finish();
        let raw = raw_frames(&out);
        assert_eq!(raw.len(), 3);
        assert_eq!((raw[0].0, raw[0].1), (FrameType::Rep.code(), 2));
        assert_eq!((raw[1].0, raw[1].1), (FrameType::Block.code(), 2));
        assert_eq!((raw[2].0, raw[2].1), (FrameType::End.code(), 2));
        assert!(text(payload(&raw[0].2)).contains("\"stream\":{\"tick_ms\":1000}"));
        let block = decode_block_with(&raw[1].2, None).expect("block");
        assert!(block.dictionary.is_some());
        let mut dicts = StreamDicts::new();
        dicts.accept(2, &block).expect("client dictionary");
        assert_end_ok(&raw[2].2, 1, 0, "complete");
    }

    #[test]
    fn conn_current_backend_error_is_internal_without_a_block() {
        let mut rig = Rig::new(Level::Observe);
        rig.current(2, selection(1));
        let raw = raw_frames(&rig.finish());
        assert_eq!(raw.len(), 1);
        assert!(is_error(&raw[0].2, Code::Internal));
        assert_eq!(rig.hub.queued(rig.conn.id()).query, 0);
    }

    #[test]
    fn conn_current_rows_budget_queues_no_block() {
        let mut limits = limits(Level::Observe);
        limits.probe.max_rows = 1;
        let sel = selection(1);
        let data = TickData::example(5, &[1, 2], &["m"]);
        let mut rig = current_rig(limits, sel.clone(), data);
        rig.current(2, sel);
        let raw = raw_frames(&rig.finish());
        assert_eq!(raw.len(), 1);
        assert!(is_error(&raw[0].2, Code::Budget));
        assert!(text(payload(&raw[0].2)).contains("\"detail\":{\"pool\":\"rows\"}"));
        assert_eq!(rig.hub.queued(rig.conn.id()).query, 0);
    }

    #[test]
    fn conn_current_oversize_is_internal_before_the_rep() {
        let mut limits = limits(Level::Observe);
        limits.probe.max_block_bytes = 128;
        let sel = selection(1);
        let mut rig = current_rig(limits, sel.clone(), current_data(5));
        rig.current(2, sel);
        let raw = raw_frames(&rig.finish());
        assert_eq!(raw.len(), 1);
        assert!(is_error(&raw[0].2, Code::Internal));
        assert_eq!(rig.hub.queued(rig.conn.id()).query, 0);
    }

    #[test]
    fn conn_current_query_budget_is_refused_before_the_rep() {
        let sel = selection(1);
        let data = current_data(5);
        let mut limits = limits(Level::Observe);
        let need = query_len(&limits, &data);
        let queue = u32::try_from(need).expect("query length");
        limits.probe.query_queue_bytes = queue;
        limits.probe.max_block_bytes = queue;
        let mut rig = current_rig(limits, sel.clone(), data);
        rig.ready();
        let blockers: Vec<ConnId> = [1, 2, 3, 4]
            .into_iter()
            .map(|uid| {
                let id = rig.hub.add(rig.now);
                let block = Arc::from(vec![0u8; need]);
                assert_eq!(
                    rig.hub.push_query(id, uid, block, rig.now),
                    QueryOutcome::Queued
                );
                id
            })
            .collect();
        assert!(!rig.hub.query_room(rig.conn.id(), need));

        rig.current(2, sel);
        let raw = raw_frames(&rig.finish());
        assert_eq!(raw.len(), 1);
        assert!(is_error(&raw[0].2, Code::Budget));
        assert!(text(payload(&raw[0].2)).contains("\"detail\":{\"pool\":\"query\"}"));
        assert_eq!(rig.hub.queued(rig.conn.id()).query, 0);
        for id in blockers {
            rig.hub.remove(id);
        }
    }

    #[test]
    fn conn_current_uses_unified_bounds() {
        let sel = selection(1);
        let data = current_data(5);
        let need = u32::try_from(
            crate::tick::query_block(
                data.clone(),
                BlockBounds {
                    max_rows: 4096,
                    max_block_bytes: u32::MAX,
                },
            )
            .expect("block")
            .len(),
        )
        .expect("length");
        let mut limits = limits(Level::Observe);
        limits.probe.max_block_bytes = need;
        limits.probe.query_queue_bytes = need;
        let mut rig = current_rig(limits, sel.clone(), data);
        rig.current(2, sel);
        let raw = raw_frames(&rig.finish());
        assert_eq!(raw.len(), 3);
        assert_end_ok(&raw[2].2, 1, 0, "complete");
    }

    #[test]
    fn conn_subscribe_rep_shape_echoes_the_request() {
        let mut rig = ready_bin(Level::Observe);
        rig.subscribe(2, selection(1), 2_000);
        let raw = raw_frames(&rig.finish());
        assert_eq!(raw.len(), 1);
        assert_eq!((raw[0].0, raw[0].1), (FrameType::Rep.code(), 2));
        let body = text(payload(&raw[0].2));
        let rep = check_rep(body.as_bytes(), false).expect("REP");
        assert!(rep.ok);
        assert_eq!(member(&body, "result"), "{\"sub\":2,\"interval_ms\":2000}");
        assert_eq!(rig.conn.subscriptions(), 1);
        assert_eq!(rig.streams.subscribers(), 1);
    }

    #[test]
    fn conn_subscribe_max_subscriptions_accepts_four_and_refuses_five() {
        let mut rig = ready_bin(Level::Observe);
        for corr in 2..6 {
            rig.subscribe(corr, selection(u64::from(corr)), 1_000);
        }
        rig.subscribe(6, selection(6), 1_000);
        let raw = raw_frames(&rig.finish());
        assert_eq!(raw.len(), 5);
        assert!(is_error(&raw[4].2, Code::Budget));
        assert!(text(payload(&raw[4].2)).contains("\"pool\":\"subscriptions\""));
        assert_eq!(rig.conn.subscriptions(), 4);
        assert_eq!(rig.streams.subscribers(), 4);
    }

    #[test]
    fn conn_subscribe_uid_and_probe_budgets_use_the_requesting_level() {
        let mut uid_limits = limits(Level::Observe);
        uid_limits.level.max_uid_selections = 1;
        let mut uid = Rig::with(Level::Observe, uid_limits);
        uid.subscribe(2, selection(1), 1_000);
        uid.subscribe(3, selection(2), 1_000);
        let raw = raw_frames(&uid.finish());
        assert!(is_error(&raw[1].2, Code::Budget));
        assert!(text(payload(&raw[1].2)).contains("\"pool\":\"uid-selections\""));

        let mut probe_limits = limits(Level::Observe);
        probe_limits.level.max_selections = 1;
        let mut probe = Rig::with(Level::Observe, probe_limits);
        probe.subscribe(2, selection(1), 1_000);
        probe.subscribe(3, selection(2), 1_000);
        let raw = raw_frames(&probe.finish());
        assert!(is_error(&raw[1].2, Code::Budget));
        assert!(text(payload(&raw[1].2)).contains("\"pool\":\"selections\""));
    }

    #[test]
    fn conn_subscribe_does_no_block_or_backend_work_before_the_next_tick() {
        let limits = limits(Level::Observe);
        let mut hub = SendHub::new(queue_limits(&limits));
        let id = hub.add(0);
        let peer = Peer {
            uid: 1000,
            pid: Some(7),
            comm: None,
            cgroup: None,
        };
        let mut conn = Conn::new(id, Level::Observe, peer, &limits, 0);
        let mut backend = CountingBackend::new();
        let mut streams = SelectionStreams::new(StreamLimits {
            bounds: BlockBounds {
                max_rows: limits.probe.max_rows,
                max_block_bytes: limits.probe.max_block_bytes,
            },
        });
        let mut input = client_prefix(2);
        input.extend_from_slice(&req(1, "hello"));
        conn.on_bytes(&input, 0, &mut hub, &mut backend, &mut streams);
        let _ = drain_hub(&mut hub, id, 0);
        assert_eq!(conn.state(), State::Ready);

        conn.execute(
            &Dispatch {
                corr: 2,
                call: Call::Subscribe(SubscribeArgs {
                    selection: selection(1),
                    interval_ms: 1_000,
                }),
            },
            0,
            &mut hub,
            &mut backend,
            &mut streams,
        );
        assert_eq!(backend.current_calls.get(), 0);
        assert_eq!(conn.subscriptions(), 1);
        assert_eq!(streams.subscribers(), 1);
        assert_eq!(hub.queued(id).live, 0);
        assert_eq!(hub.queued(id).query, 0);
        let raw = raw_frames(&drain_hub(&mut hub, id, 0));
        assert_eq!(raw.len(), 1);
        assert_eq!((raw[0].0, raw[0].1), (FrameType::Rep.code(), 2));
        assert!(
            check_rep(text(payload(&raw[0].2)).as_bytes(), false)
                .expect("subscribe reply")
                .ok
        );
    }

    #[test]
    fn conn_subscribe_first_block_is_dictionary_bearing_at_the_next_tick() {
        let sel = selection(1);
        let mut rig = ready_bin(Level::Observe);
        rig.backend.current.insert(sel.clone(), Ok(current_data(5)));
        rig.subscribe(2, sel, 1_000);
        rig.pump(usize::MAX);
        rig.out.clear();
        rig.streaming_tick();
        let raw = raw_frames(&rig.finish());
        assert_eq!(raw.len(), 1);
        assert_eq!((raw[0].0, raw[0].1), (FrameType::Block.code(), 2));
        let block = decode_block_with(&raw[0].2, None).expect("block");
        assert!(block.dictionary.is_some());
        let mut dicts = StreamDicts::new();
        dicts.accept(2, &block).expect("first dictionary");
        assert_eq!(block.header.seq, 5);
    }

    fn subscribe_then_tick(blocks_written: bool) -> Rig {
        let sel = selection(1);
        let mut rig = ready_bin(Level::Observe);
        rig.backend.current.insert(sel.clone(), Ok(current_data(5)));
        rig.subscribe(2, sel, 1_000);
        rig.pump(usize::MAX);
        rig.out.clear();
        rig.streaming_tick();
        if blocks_written {
            rig.pump(usize::MAX);
            rig.out.clear();
        }
        rig
    }

    #[test]
    fn conn_unsubscribe_replies_then_ends_with_unsubscribed() {
        let mut rig = ready_bin(Level::Observe);
        rig.subscribe(2, selection(1), 1_000);
        rig.pump(usize::MAX);
        rig.out.clear();
        rig.execute(3, Call::Unsubscribe(2));
        let raw = raw_frames(&rig.finish());
        assert_eq!(raw.len(), 2);
        assert_eq!((raw[0].0, raw[0].1), (FrameType::Rep.code(), 3));
        assert!(text(payload(&raw[0].2)).contains("\"result\":{}"));
        assert_eq!((raw[1].0, raw[1].1), (FrameType::End.code(), 2));
        assert_end_ok(&raw[1].2, 0, 0, "unsubscribed");
        assert_eq!(rig.conn.subscriptions(), 0);
        assert_eq!(rig.streams.subscribers(), 0);
        assert_eq!(rig.streams.live_selections(), 0);
    }

    #[test]
    fn conn_unsubscribe_unknown_zero_and_query_corr_are_not_found() {
        let mut rig = ready_bin(Level::Observe);
        let sel = selection(1);
        rig.backend.current.insert(sel.clone(), Ok(current_data(5)));
        rig.current(2, sel);
        rig.pump(usize::MAX);
        rig.out.clear();
        for (corr, sub) in [(3, 0), (4, 2), (5, 99)] {
            rig.execute(corr, Call::Unsubscribe(sub));
        }
        let raw = raw_frames(&rig.finish());
        assert_eq!(raw.len(), 3);
        for frame in &raw {
            assert_eq!(frame.0, FrameType::Rep.code());
            assert!(is_error(&frame.2, Code::NotFound));
        }
    }

    #[test]
    fn conn_unsubscribe_queued_unpinned_tick_is_counted_in_dropped() {
        let mut rig = subscribe_then_tick(false);
        rig.execute(3, Call::Unsubscribe(2));
        let raw = raw_frames(&rig.finish());
        let ends: Vec<_> = raw
            .iter()
            .filter(|frame| frame.0 == FrameType::End.code())
            .collect();
        assert_eq!(ends.len(), 1);
        assert_end_ok(&ends[0].2, 0, 1, "unsubscribed");
        assert!(!raw.iter().any(|frame| frame.0 == FrameType::Block.code()));
    }

    #[test]
    fn conn_unsubscribe_pinned_tick_is_counted_in_blocks() {
        let mut rig = subscribe_then_tick(false);
        assert!(rig.hub.head(rig.conn.id(), rig.now).is_some());
        rig.execute(3, Call::Unsubscribe(2));
        let raw = raw_frames(&rig.finish());
        let block = raw
            .iter()
            .position(|frame| frame.0 == FrameType::Block.code() && frame.1 == 2)
            .expect("pinned block remains before unsubscribe replies");
        let rep = raw
            .iter()
            .position(|frame| frame.0 == FrameType::Rep.code() && frame.1 == 3)
            .expect("unsubscribe reply");
        let end = raw
            .iter()
            .position(|frame| frame.0 == FrameType::End.code() && frame.1 == 2)
            .expect("unsubscribe end");
        assert!(block < rep && rep < end);
        assert_end_ok(&raw[end].2, 1, 0, "unsubscribed");
    }

    #[test]
    fn conn_corr_reuse_after_end_ok_is_legal() {
        let mut rig = ready_bin(Level::Observe);
        rig.subscribe(2, selection(1), 1_000);
        rig.execute(3, Call::Unsubscribe(2));
        rig.pump(usize::MAX);
        rig.out.clear();
        rig.subscribe(2, selection(2), 1_000);
        let raw = raw_frames(&rig.finish());
        assert_eq!(raw.len(), 1);
        assert_eq!((raw[0].0, raw[0].1), (FrameType::Rep.code(), 2));
        assert_eq!(rig.conn.subscriptions(), 1);
        assert_eq!(rig.streams.subscribers(), 1);
    }

    #[test]
    fn conn_corr_in_use_subscription_is_fatal() {
        let mut rig = ready_bin(Level::Observe);
        rig.subscribe(2, selection(1), 1_000);
        rig.pump(usize::MAX);
        rig.out.clear();
        rig.send(&raw_req(2, "{"));
        assert!(rig.conn.is_closed());
        assert_eq!(rig.conn.close_code(), Some(Code::BadFrame));
        assert_eq!(
            text(&rig.finish()),
            text(&rep_error(0, Code::BadFrame, &Detail::Empty))
        );
    }

    #[test]
    fn conn_too_many_requests_refuses_current_but_spares_status() {
        let sel = selection(1);
        let data = current_data(5);
        let need = {
            let limits = limits(Level::Observe);
            query_len(&limits, &data)
        };
        let mut rig = current_rig(limits(Level::Observe), sel, data);
        rig.ready();
        let current = String::from(
            "{\"verb\":\"current\",\"args\":{\"selection\":{\"entities\":[{\"inc\":1}],\
             \"metrics\":[\"m\"]}}}",
        );
        rig.send(&raw_req(2, &current));
        assert_eq!(rig.hub.queued(rig.conn.id()).query, need);
        rig.send(&raw_req(3, &current));
        rig.send(&req(4, "status"));
        let raw = raw_frames(&rig.finish());
        assert_eq!(rig.conn.state(), State::Ready);
        assert_eq!((raw[0].0, raw[0].1), (FrameType::Rep.code(), 2));
        assert_eq!((raw[1].0, raw[1].1), (FrameType::Block.code(), 2));
        assert_eq!((raw[2].0, raw[2].1), (FrameType::End.code(), 2));
        assert_end_ok(&raw[2].2, 1, 0, "complete");
        assert_eq!((raw[3].0, raw[3].1), (FrameType::Rep.code(), 3));
        assert!(is_error(&raw[3].2, Code::TooManyRequests));
        let status = raw
            .iter()
            .find(|frame| frame.1 == 4 && frame.0 == FrameType::Rep.code())
            .expect("status reply");
        assert!(
            check_rep(text(payload(&status.2)).as_bytes(), false)
                .expect("status")
                .ok
        );
        assert_eq!(rig.hub.queued(rig.conn.id()).query, 0);
    }

    #[test]
    fn conn_closed_stops_every_push_path() {
        let mut limits = limits(Level::Observe);
        limits.level.control_queue_bytes = 1;
        let mut rig = Rig::with(Level::Observe, limits);
        let sub_selection = selection(1);
        rig.send(&client_prefix(2));
        rig.pump(usize::MAX);
        rig.out.clear();
        rig.send(&req(1, "hello"));
        rig.pump(usize::MAX);
        rig.out.clear();
        rig.subscribe(2, sub_selection.clone(), 1_000);
        rig.pump(usize::MAX);
        rig.out.clear();
        let current_selection = selection(3);
        rig.backend
            .current
            .insert(current_selection.clone(), Ok(current_data(6)));
        assert!(rig
            .hub
            .push_control(rig.conn.id(), Arc::from(vec![0u8]), rig.now)
            .is_ok());
        rig.current(3, current_selection);
        assert_eq!(rig.conn.close_code(), Some(Code::SlowClient));
        assert_eq!(rig.hub.queued(rig.conn.id()).query, 0);
        assert_eq!(rig.conn.subscriptions(), 1);
        assert_eq!(rig.streams.subscribers(), 1);
        let key = crate::registry::SubKey {
            conn: rig.conn.id(),
            corr: 2,
        };
        let uid = rig.conn.peer().uid;
        assert_eq!(rig.streams.live_selections(), 1);
        assert_eq!(rig.streams.uid_selections(uid), 1);
        assert_eq!(rig.streams.selection_of(key), Some(&sub_selection));
        let before = rig.hub.queued(rig.conn.id());
        let subscribers = rig.streams.subscribers();
        let live_selections = rig.streams.live_selections();
        let uid_selections = rig.streams.uid_selections(uid);
        let registry_selection = rig.streams.selection_of(key).cloned();
        rig.conn
            .end_subscription(2, Code::Internal, &Detail::Empty, rig.now, &mut rig.hub);
        assert_eq!(rig.conn.subscriptions(), 0);
        assert_eq!(rig.streams.subscribers(), subscribers);
        assert_eq!(rig.streams.live_selections(), live_selections);
        assert_eq!(rig.streams.uid_selections(uid), uid_selections);
        assert_eq!(rig.streams.selection_of(key), registry_selection.as_ref());
        assert_eq!(rig.hub.queued(rig.conn.id()), before);
        rig.execute(3, Call::Status);
        rig.current(4, selection(2));
        rig.subscribe(5, selection(3), 1_000);
        rig.execute(6, Call::Unsubscribe(2));
        let tick = crate::tick::LiveTick::new(
            1,
            None,
            Arc::from(vec![0u8; 200]),
            Arc::from(Vec::<u8>::new()),
        );
        rig.conn.push_tick(2, &tick, rig.now, &mut rig.hub);
        assert_eq!(rig.hub.queued(rig.conn.id()), before);
        assert_eq!(rig.streams.subscribers(), subscribers);
        assert_eq!(rig.streams.live_selections(), live_selections);
        assert_eq!(rig.streams.uid_selections(uid), uid_selections);
        assert_eq!(rig.streams.selection_of(key), registry_selection.as_ref());
    }

    #[test]
    fn conn_draining_ends_each_subscription_in_corr_order_with_exact_counts() {
        let sel = selection(1);
        let mut rig = ready_bin(Level::Observe);
        rig.backend.current.insert(sel.clone(), Ok(current_data(5)));
        rig.subscribe(2, sel.clone(), 1_000);
        rig.subscribe(3, sel, 1_000);
        rig.pump(usize::MAX);
        rig.out.clear();
        rig.streaming_tick();
        rig.pump(usize::MAX);
        rig.out.clear();
        rig.eof();
        assert_eq!(rig.conn.state(), State::Draining);
        let raw = raw_frames(&rig.finish());
        assert_eq!(raw.len(), 2);
        assert_eq!((raw[0].0, raw[0].1), (FrameType::End.code(), 2));
        assert_eq!((raw[1].0, raw[1].1), (FrameType::End.code(), 3));
        assert_end_ok(&raw[0].2, 1, 0, "client-eof");
        assert_end_ok(&raw[1].2, 1, 0, "client-eof");
        assert!(rig.conn.is_closed());
        assert_eq!(rig.conn.close_code(), None);
    }

    #[test]
    fn conn_draining_closes_only_when_the_queue_is_empty() {
        let mut rig = ready_bin(Level::Observe);
        rig.subscribe(2, selection(1), 1_000);
        rig.pump(usize::MAX);
        rig.out.clear();
        rig.eof();
        assert_eq!(rig.conn.state(), State::Draining);
        rig.pump(1);
        assert_eq!(rig.conn.state(), State::Draining);
        rig.pump(usize::MAX);
        assert!(rig.conn.is_closed());
    }

    #[test]
    fn conn_idle_is_not_armed_with_a_subscription() {
        let mut subscribed = ready_bin(Level::Observe);
        subscribed.subscribe(2, selection(1), 1_000);
        subscribed.pump(usize::MAX);
        subscribed.tick(300_001 * MS);
        assert!(!subscribed.conn.is_closed());
    }

    #[test]
    fn conn_idle_is_armed_again_after_unsubscribe() {
        let mut rig = ready_bin(Level::Observe);
        rig.subscribe(2, selection(1), 1_000);
        rig.execute(3, Call::Unsubscribe(2));
        rig.pump(usize::MAX);
        rig.out.clear();
        rig.tick(300_001 * MS);
        assert!(rig.conn.is_closed());
        assert_eq!(rig.conn.close_code(), Some(Code::Idle));
    }

    #[test]
    fn conn_server_end_budget_rows_and_internal_are_end_errors() {
        let mut rig = ready_bin(Level::Observe);
        rig.subscribe(2, selection(1), 1_000);
        rig.subscribe(3, selection(2), 1_000);
        rig.pump(usize::MAX);
        rig.out.clear();
        rig.conn.end_subscription(
            2,
            Code::Budget,
            &Detail::Pool(crate::codes::Pool::Rows),
            rig.now,
            &mut rig.hub,
        );
        rig.conn
            .end_subscription(3, Code::Internal, &Detail::Empty, rig.now, &mut rig.hub);
        let raw = raw_frames(&rig.finish());
        assert_eq!(raw.len(), 2);
        assert_end_error(&raw[0].2, 0, 0, Code::Budget);
        assert!(text(payload(&raw[0].2)).contains("\"pool\":\"rows\""));
        assert_end_error(&raw[1].2, 0, 0, Code::Internal);
        assert_eq!(rig.conn.subscriptions(), 0);
    }

    #[test]
    fn conn_server_end_unknown_corr_is_ignored() {
        let mut rig = ready_bin(Level::Observe);
        rig.conn
            .end_subscription(9, Code::Internal, &Detail::Empty, rig.now, &mut rig.hub);
        assert!(rig.finish().is_empty());
        assert!(!rig.conn.is_closed());
    }

    #[test]
    fn conn_hello_result_pins_every_field_and_the_key_order() {
        let mut rig = Rig::new(Level::Observe);
        rig.backend = rich_backend();
        let mut input = client_prefix(2);
        input.extend_from_slice(&req(1, "hello"));
        rig.send(&input);
        let out = rig.finish();
        let parsed = frames(&out[8..]);
        let result = member(&parsed[0].2, "result");
        assert_eq!(keys_of(&result), HELLO_KEYS);
        let mut codes = String::from("[");
        for (index, entry) in quality::ALL.iter().enumerate() {
            if index > 0 {
                codes.push(',');
            }
            let (code, name) = (entry.code(), entry.name());
            codes.push_str(&format!("{{\"code\":{code},\"name\":\"{name}\"}}"));
        }
        codes.push(']');
        let expected = [
            ("level", r#""observe""#),
            ("server", r#""hostobs-probe 9.9""#),
            ("boot_id", r#""11111111-2222-4333-8444-555555555555""#),
            ("epoch", "42"),
            ("features", r#"["peak_reset","damon"]"#),
            (
                "verbs",
                r#"["hello","status","current","subscribe","unsubscribe","events"]"#,
            ),
            ("quality_codes", codes.as_str()),
        ];
        for (key, value) in expected {
            assert_eq!(member(&result, key), value, "{key}");
        }
        assert!(codes.starts_with(r#"[{"code":0,"name":"ok"}"#));
        let proto = member(&result, "proto");
        let expected = want(&[("major", "1"), ("minor", "2")]);
        assert_eq!(pairs(&proto), expected);
        let announced_limits = member(&result, "limits");
        let announced = limits(Level::Observe).hello_json();
        assert_eq!(pairs(&announced_limits), pairs(&announced));
        assert_eq!(member(&announced_limits, "idle_ms"), "300000");
        assert_eq!(member(&announced_limits, "hello_deadline_ms"), "5000");
        let names = keys_of(&announced_limits);
        assert_eq!(names.len(), 19);
        assert_eq!(names.first().map(String::as_str), Some("max_frame_bytes"));
        assert_eq!(names.last().map(String::as_str), Some("tick_ms"));
    }

    #[test]
    fn conn_status_result_pins_every_field_the_key_order_and_the_queued_bytes() {
        let mut rig = ready_bin(Level::Observe);
        rig.backend = rich_backend();
        let id = rig.conn.id();
        let live = rig.hub.push_live(id, 7, &Forms(1_000), rig.now);
        assert_eq!(live, LiveOutcome::Queued);
        let block: Arc<[u8]> = Arc::from(vec![0u8; 300]);
        let query = rig.hub.push_query(id, 8, block, rig.now);
        assert_eq!(query, QueryOutcome::Queued);
        rig.send(&req(2, "status"));
        let out = rig.finish();
        let parsed = frames(&out[1_300..]);
        assert_eq!((parsed[0].0, parsed[0].1), (FrameType::Rep.code(), 2));
        let result = member(&parsed[0].2, "result");
        assert_eq!(keys_of(&result), STATUS_KEYS);
        for (key, value) in STATUS_SCALARS {
            assert_eq!(member(&result, key), value, "{key}");
        }
        let expected = want(&[
            ("seq", "77"),
            ("last_mono_ns", "5000000123"),
            ("scan_ms", "31"),
        ]);
        assert_eq!(pairs(&member(&result, "tick")), expected);
        let expected = want(&[("observe", "2"), ("operator", "0"), ("observer", "1")]);
        assert_eq!(pairs(&member(&result, "clients")), expected);
        let expected = want(&[("queued_bytes", "1300"), ("dropped_blocks", "0")]);
        assert_eq!(pairs(&member(&result, "queues")), expected);
        let tiers = flat_objects(&member(&result, "tiers"));
        assert_eq!(tiers.len(), 2);
        let hot = want(&[
            ("name", r#""hot""#),
            ("step_ms", "1000"),
            ("keep_s", "3600"),
            ("bytes", "123456"),
            ("cap_bytes", "1048576"),
        ]);
        assert_eq!(pairs(&tiers[0]), hot);
        let warm = want(&[
            ("name", r#""warm""#),
            ("step_ms", "10000"),
            ("keep_s", "86400"),
            ("bytes", "7654321"),
            ("cap_bytes", "8388608"),
        ]);
        assert_eq!(pairs(&tiers[1]), warm);
    }

    fn status_result_from(out: &[u8]) -> String {
        let raw = raw_frames(out);
        let status = raw
            .iter()
            .find(|frame| frame.0 == FrameType::Rep.code())
            .expect("status REP");
        let body = text(payload(&status.2));
        let rep = check_rep(body.as_bytes(), false).expect("status REP");
        assert!(rep.ok);
        member(&body, "result")
    }

    #[test]
    fn conn_status_tests_rewritten_ignore_the_backends_drop_count() {
        let mut rig = ready_bin(Level::Observe);
        rig.backend = rich_backend();
        assert_eq!(rig.backend.status.dropped_blocks, 5);
        rig.send(&req(2, "status"));
        let result = status_result_from(&rig.finish());
        assert_eq!(
            pairs(&member(&result, "queues")),
            want(&[("queued_bytes", "0"), ("dropped_blocks", "0")])
        );
    }

    #[test]
    fn conn_status_dropped_blocks_from_hub_is_the_counted_total() {
        let mut rig = ready_bin(Level::Observe);
        rig.backend = rich_backend();
        let id = rig.conn.id();
        let live: Arc<[u8]> = Arc::from(control_frame(FrameType::Evt, 99, &[b'x'; 80]));
        let len = live.len();
        for index in 0..6 {
            let tick = FrameForm(Arc::clone(&live));
            let outcome = rig.hub.push_live(id, 9, &tick, rig.now);
            if index == 0 {
                assert_eq!(outcome, LiveOutcome::Queued);
            } else {
                assert_eq!(outcome, LiveOutcome::Coalesced);
            }
        }
        assert_eq!(rig.hub.dropped_blocks(), 5);
        rig.send(&req(2, "status"));
        let result = status_result_from(&rig.finish());
        let queued = len.to_string();
        assert_eq!(
            pairs(&member(&result, "queues")),
            want(&[("queued_bytes", queued.as_str()), ("dropped_blocks", "5")])
        );
    }

    fn xorshift(state: &mut u64) -> u64 {
        let mut x = *state;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        *state = x;
        x
    }

    fn bounded(rig: &Rig) {
        let max = usize::try_from(rig.limits.level.max_request_bytes).unwrap_or(0);
        let held = match &rig.conn.reader {
            Reader::Frames(reader) => reader.buffered_capacity(),
            Reader::Lines(reader) => reader.buffered_capacity(),
            Reader::None => 0,
        };
        assert!(held <= max + 16);
        assert!(rig.hub.queued(rig.conn.id()).control <= 64 * 1024);
    }

    #[test]
    fn conn_hostile_2000_runs_stay_bounded_and_always_close() {
        let mut seed = 0x9E37_79B9_7F4A_7C15u64;
        let mut coverage = OracleCoverage::default();
        {
            let mut alone = Rig::new(Level::Observe);
            alone.send(&[b'H', b'O', b'B', 1, 2, 0, 0, 0]);
            closed_oracle(&mut alone, 0, 0, &mut coverage);
        }
        {
            let mut owed = Rig::new(Level::Observe);
            let mut input = client_prefix(2);
            input.extend_from_slice(&[12, 0, 0, 0, 9, 0, 0, 0, 0, 0, 0, 0]);
            owed.send(&input);
            closed_oracle(&mut owed, 0, 0, &mut coverage);
        }
        {
            let mut partial = ready_bin(Level::Observe);
            let written_base = partial.conn.bytes_written();
            partial.send(&req(2, "status"));
            partial.pump(3);
            partial.conn.close_for(Code::SlowClient, &mut partial.hub);
            closed_oracle(&mut partial, 0, written_base, &mut coverage);
        }
        let levels = [Level::Observe, Level::Operator, Level::Observer];
        for run in 0..2_000usize {
            let mut rig = Rig::new(levels[run % 3]);
            let mut input = Vec::new();
            for _ in 0..(xorshift(&mut seed) % 6) {
                let pick = xorshift(&mut seed) % 7;
                match pick {
                    0 => {
                        let major = 1 + u16::from(xorshift(&mut seed).is_multiple_of(5));
                        let minor = u16::try_from(xorshift(&mut seed) % 4).unwrap_or(0);
                        input.extend_from_slice(&server_prefix(major, minor));
                    }
                    1 => input.extend_from_slice(&req(1, "hello")),
                    2 => input.extend_from_slice(&req(2, "status")),
                    3 => input.extend_from_slice(&line(1, "hello", HELLO_ARGS)),
                    4 => input.extend_from_slice(&line(5, "status", "{}")),
                    5 => input.extend_from_slice(b"{\"corr\":1,\"ve"),
                    _ => {
                        for _ in 0..(xorshift(&mut seed) % 40) {
                            input.push(u8::try_from(xorshift(&mut seed) % 256).unwrap_or(0));
                        }
                    }
                }
            }
            let mut rest = &input[..];
            while !rest.is_empty() && !rig.conn.is_closed() {
                let step = usize::try_from(1 + xorshift(&mut seed) % 9).unwrap_or(1);
                let (head, tail) = rest.split_at(step.min(rest.len()));
                rig.send(head);
                rest = tail;
                if xorshift(&mut seed).is_multiple_of(3) {
                    rig.pump(usize::try_from(xorshift(&mut seed) % 50).unwrap_or(0));
                }
                if xorshift(&mut seed).is_multiple_of(11) {
                    rig.tick(xorshift(&mut seed) % (6_000 * MS));
                }
                bounded(&rig);
            }
            if xorshift(&mut seed).is_multiple_of(2) {
                rig.eof();
            }
            rig.pump(usize::MAX);
            rig.tick(400_000 * MS);
            rig.tick(400_000 * MS);
            assert!(rig.conn.is_closed(), "run {run} left the connection open");
            closed_oracle(&mut rig, run, 0, &mut coverage);
        }
        assert!(coverage.owed > 0, "{coverage:?}");
        assert!(coverage.alone > 0, "{coverage:?}");
        assert!(coverage.partial > 0, "{coverage:?}");
    }

    #[test]
    fn conn_hostile_2000_stream_operations_keep_registry_and_hub_exact() {
        let mut seed = 0xD1B5_4A32_D192_ED03u64;
        for run in 0..2_000usize {
            let mut rig = ready_bin(Level::Observe);
            for inc in 1..=4 {
                rig.backend
                    .current
                    .insert(selection(inc), Ok(current_data(inc)));
            }
            for _ in 0..32 {
                let pick = xorshift(&mut seed) % 9;
                match pick {
                    0 => {
                        let corr = 2 + u32::try_from(xorshift(&mut seed) % 4).unwrap_or(0);
                        let inc = 1 + xorshift(&mut seed) % 4;
                        rig.current(corr, selection(inc));
                    }
                    1 => {
                        let corr = 10 + u32::try_from(xorshift(&mut seed) % 4).unwrap_or(0);
                        let inc = 1 + xorshift(&mut seed) % 4;
                        let interval =
                            1_000 * (1 + u32::try_from(xorshift(&mut seed) % 3).unwrap_or(0));
                        rig.subscribe(corr, selection(inc), interval);
                    }
                    2 => {
                        let request = 20 + u32::try_from(xorshift(&mut seed) % 4).unwrap_or(0);
                        let sub = 10 + u32::try_from(xorshift(&mut seed) % 4).unwrap_or(0);
                        rig.execute(request, Call::Unsubscribe(sub));
                    }
                    3 => rig.streaming_tick(),
                    4 => {
                        let max = usize::try_from(xorshift(&mut seed) % 600).unwrap_or(0);
                        rig.pump(max);
                    }
                    5 => {
                        rig.pump(usize::MAX);
                    }
                    6 => {
                        let sub = 10 + u32::try_from(xorshift(&mut seed) % 4).unwrap_or(0);
                        let (code, detail) = if xorshift(&mut seed).is_multiple_of(2) {
                            (Code::Budget, Detail::Pool(crate::codes::Pool::Rows))
                        } else {
                            (Code::Internal, Detail::Empty)
                        };
                        rig.conn
                            .end_subscription(sub, code, &detail, rig.now, &mut rig.hub);
                    }
                    7 => rig.execute(30, Call::Status),
                    _ => {
                        let ns = xorshift(&mut seed) % (2_000 * MS);
                        rig.tick(ns);
                    }
                }
                bounded(&rig);
                if rig.conn.is_closed() {
                    break;
                }
            }
            if !rig.conn.is_closed() {
                rig.conn.close_for(Code::ShuttingDown, &mut rig.hub);
            }
            let id = rig.conn.id();
            let uid = rig.conn.peer().uid;
            let subscribers = rig.streams.subscribers();
            assert_eq!(rig.streams.release_conn(id), subscribers);
            rig.hub.remove(id);
            assert_eq!(rig.streams.live_selections(), 0, "run {run}");
            assert_eq!(rig.streams.subscribers(), 0, "run {run}");
            assert_eq!(rig.streams.uid_selections(uid), 0, "run {run}");
            assert_eq!(rig.hub.queued(id), Pools::default(), "run {run}");
        }
    }

    #[derive(Debug, Default)]
    struct OracleCoverage {
        owed: usize,
        alone: usize,
        partial: usize,
    }

    /// What must hold of a closed connection (WIRE.md section 5.4 `Closing`, design R1, R2), and
    /// that nothing more happens to it after the close.
    fn closed_oracle(rig: &mut Rig, run: usize, written_base: u64, coverage: &mut OracleCoverage) {
        let id = rig.conn.id();
        let code = rig.conn.close_code();
        assert!(code.is_none_or(Code::fatal), "run {run}: {code:?}");
        let at_close = rig.hub.queued(id);
        assert!(at_close.live == 0 && at_close.query == 0, "run {run}");
        assert!(at_close.control <= 8 * 1024, "run {run}");
        let partial = rig.hub.head_partial(id);
        let (written, queued) = (rig.conn.bytes_written(), rig.conn.prefix_queued);
        let at_oracle = written.saturating_sub(written_base);
        assert_eq!(at_oracle, u64::try_from(rig.out.len()).unwrap_or(u64::MAX));
        coverage.partial += usize::from(partial);
        let last = rig.conn.take_final();
        if partial {
            assert_eq!(last, None, "run {run}: a partly written head");
        }
        if let Some(bytes) = &last {
            assert!(bytes.len() <= 8 + 4096, "run {run}");
            let prefixed = bytes.starts_with(b"HOB\x01");
            let owed = queued && written == 0;
            let alone = !queued && code == Some(Code::UnsupportedVersion) && bytes.len() == 8;
            coverage.owed += usize::from(owed);
            coverage.alone += usize::from(alone);
            assert_eq!(prefixed, owed || alone, "run {run}: the prefix guarantee");
            if owed {
                let minor = rig.conn.minor();
                assert_eq!(bytes[..8], server_prefix(1, minor), "run {run}");
            }
        }
        rig.send(&req(2, "status"));
        rig.send(&line(5, "status", "{}"));
        rig.eof();
        rig.tick(MS);
        rig.pump(usize::MAX);
        assert_eq!(rig.hub.queued(id), at_close, "run {run}: queued late");
        assert_eq!(rig.conn.bytes_written(), written, "run {run}");
        assert_eq!(rig.conn.close_code(), code, "run {run}");
        assert!(!rig.conn.has_final(), "run {run}");
    }
}
