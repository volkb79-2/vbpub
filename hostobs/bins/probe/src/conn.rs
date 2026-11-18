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
//! - **Not in this package:** `max_outstanding` and "`corr` in use" need a request that stays
//!   outstanding, and `hello` and `status` answer synchronously, so the outstanding count is 0
//!   whenever the next request arrives (R3); the idle condition and `Draining` therefore look
//!   only at the queue. Subscriptions, `client-eof` ENDs and the counters belong to P5b-2.
//! - **Unreachable-by-design branches** close with `internal`, never a panic.

use std::sync::Arc;

use hostobs_proto::error::ProtoError;
use hostobs_proto::frame::{FrameType, FLAG_SECTIONED, FRAME_HEADER_LEN, PROTO_MINOR};
use hostobs_proto::hub::{Close, ConnId, Pools, SendHub};
use hostobs_proto::quality;
use hostobs_proto::stream::{ConnTimers, Detect, Feed, FrameReader, ModeDetect, RawFrame, Timer};

use crate::backend::{Backend, Identity, StatusInfo};
use crate::classify::{classify, Call, Dispatch, Mode, Phase, Prefix, Query, Refusal, SERVER_MAJOR};
use crate::codes::{Code, Detail};
use crate::encode::{
    closing_evt, error_line, push_string, rep_error, rep_ok, rep_ok_line, server_prefix,
    unknown_mode_line,
};
use crate::limits::{Level, Limits};
use crate::lines::{LineError, LineFeed, LineReader};
use crate::verbs::served_at;

/// The highest protocol minor the probe serves.
pub const SERVER_MINOR: u16 = PROTO_MINOR;

/// The peer credentials captured once at accept (R23: `pid` is unused in P5b-1).
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Peer {
    /// The peer's uid.
    pub uid: u32,
    /// The peer's pid.
    pub pid: u32,
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
    /// The client half-closed: finish the queue, then close.
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

/// How a reader error closes the connection.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ReadFail {
    /// A reply with `corr` 0 and this code.
    Reply(Code),
    /// The peer is gone: no reply.
    Silent,
}

/// The requests one read completed and the error that ended it.
struct Batch {
    inputs: Vec<Input>,
    error: Option<ReadFail>,
}

/// Why a reader error closes (WIRE.md section 5.2 "Wire mapping on the server"); at EOF every
/// error is silent.
fn frame_fail(error: &ProtoError, at_eof: bool) -> ReadFail {
    if at_eof {
        return ReadFail::Silent;
    }
    match error {
        ProtoError::OversizedFrame { .. } => ReadFail::Reply(Code::FrameTooLarge),
        ProtoError::Deadline => ReadFail::Reply(Code::Deadline),
        ProtoError::TrailingBytes { .. } => ReadFail::Silent,
        _ => ReadFail::Reply(Code::BadFrame),
    }
}

fn line_fail(error: LineError) -> ReadFail {
    match error.code() {
        Some(code) => ReadFail::Reply(code),
        None => ReadFail::Silent,
    }
}

fn frame_batch(feed: Feed) -> Batch {
    Batch {
        inputs: feed.frames.into_iter().map(Input::Frame).collect(),
        error: feed.error.map(|error| frame_fail(&error, false)),
    }
}

fn line_batch(feed: LineFeed) -> Batch {
    Batch {
        inputs: feed.lines.into_iter().map(Input::Line).collect(),
        error: feed.error.map(line_fail),
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

/// The `hello` result of WIRE.md section 4.4: `minor` is the negotiated N.
fn hello_result(level: Level, minor: u16, identity: &Identity, limits: &Limits) -> String {
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
fn status_json(identity: &Identity, status: &StatusInfo, queued: Pools) -> String {
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
        status.dropped_blocks
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
    limits: Limits,
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
}

impl Conn {
    /// A connection accepted at `now_ns`: `Undecided`, the hello deadline armed. `id` is the
    /// hub's `add(peer.uid, now_ns)`; `peer` is stored once and never re-read (R23).
    #[must_use]
    pub fn new(id: ConnId, level: Level, peer: Peer, limits: &Limits, now_ns: u64) -> Conn {
        let hello_ns = ms_to_ns(limits.hello_deadline_ms);
        let mut timers = ConnTimers::new(hello_ns, ms_to_ns(limits.idle_ms));
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
    pub fn peer(&self) -> Peer {
        self.peer
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

    /// Bytes arrived from the peer at `now_ns` (an empty read is not an event).
    pub fn on_bytes(
        &mut self,
        bytes: &[u8],
        now_ns: u64,
        hub: &mut SendHub,
        backend: &dyn Backend,
    ) {
        if bytes.is_empty() || self.state == State::Closed {
            return;
        }
        self.timers.on_byte();
        match self.state {
            State::Undecided => self.on_undecided(bytes, now_ns, hub, backend),
            State::AwaitHello | State::JsonNoHello | State::Ready => {
                self.on_stream(bytes, now_ns, hub, backend);
            }
            State::Draining => self.finish_close(hub, None, None),
            State::Closed => {}
        }
        self.refresh_idle(now_ns, hub);
    }

    /// The peer ended its stream (R10): silent close before `Ready` or inside a frame or line;
    /// in `Ready` a clean end is `Draining`, which closes when the queue is empty.
    pub fn on_eof(&mut self, now_ns: u64, hub: &mut SendHub) {
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
        if let Some(fail) = self.reader_deadline(now_ns) {
            self.fail(fail, hub);
            return;
        }
        if timer == Some(Timer::Idle) {
            self.close_for(Code::Idle, hub);
            return;
        }
        self.refresh_idle(now_ns, hub);
    }

    /// The driver wrote `n` bytes of the head it was offered. This calls `SendHub::wrote`, counts
    /// the bytes (R2), completes `Draining` when the queue is empty and re-arms idle.
    pub fn on_written(&mut self, n: usize, now_ns: u64, hub: &mut SendHub) {
        if self.state == State::Closed {
            return;
        }
        hub.wrote(self.id, n, now_ns);
        self.written = self.written.saturating_add(u64::try_from(n).unwrap_or(u64::MAX));
        if self.state == State::Draining {
            self.complete_draining(hub);
        }
        self.refresh_idle(now_ns, hub);
    }

    /// A server-originated close (the server calls it for the hub's `slow-client` and for
    /// `shutting-down`): the final frame is the `closing` EVT (binary) or the error line (JSON);
    /// an `Undecided` connection has no mode and closes silently.
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
            && hub.queued(self.id) == Pools::default();
        self.timers.on_idle_condition(idle, now_ns);
    }

    fn complete_draining(&mut self, hub: &mut SendHub) {
        if hub.queued(self.id) == Pools::default() {
            self.finish_close(hub, None, None);
        }
    }

    fn reader_deadline(&mut self, now_ns: u64) -> Option<ReadFail> {
        match &mut self.reader {
            Reader::Frames(reader) => {
                let polled = reader.poll(now_ns);
                polled.err().map(|error| frame_fail(&error, false))
            }
            Reader::Lines(reader) => reader.poll(now_ns).err().map(line_fail),
            Reader::None => None,
        }
    }

    fn fail(&mut self, fail: ReadFail, hub: &mut SendHub) {
        match fail {
            ReadFail::Reply(code) => self.close_reply(hub, code, 0, &Detail::Empty),
            ReadFail::Silent => self.finish_close(hub, None, None),
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
        let cap = self.limits.max_reply_bytes;
        if self.is_json() {
            rep_ok_line(corr, result, cap)
        } else {
            rep_ok(corr, result, cap)
        }
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
        backend: &dyn Backend,
    ) {
        match self.detect.feed(bytes) {
            Detect::Need => {}
            Detect::Unknown => {
                let line = unknown_mode_line();
                self.finish_close(hub, Some(Code::UnknownMode), Some(line));
            }
            Detect::Json { at } => {
                let completion = ms_to_ns(self.limits.request_deadline_ms);
                let reader = LineReader::new(self.limits.max_request_bytes, completion);
                self.reader = Reader::Lines(reader);
                self.state = State::JsonNoHello;
                let rest = bytes.get(at..).unwrap_or_default();
                self.on_stream(rest, now_ns, hub, backend);
            }
            Detect::Binary { prefix, at } => {
                let rest = bytes.get(at..).unwrap_or_default();
                self.on_prefix(prefix, rest, now_ns, hub, backend);
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
        backend: &dyn Backend,
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
        let completion = ms_to_ns(self.limits.request_deadline_ms);
        let reader = FrameReader::new(self.limits.max_request_bytes, completion);
        self.reader = Reader::Frames(reader);
        self.state = State::AwaitHello;
        self.prefix_queued = true;
        let reply = server_prefix(SERVER_MAJOR, self.minor).to_vec();
        self.push(hub, reply, now_ns);
        self.on_stream(rest, now_ns, hub, backend);
    }

    fn on_stream(&mut self, bytes: &[u8], now_ns: u64, hub: &mut SendHub, backend: &dyn Backend) {
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
            self.dispatch(input, now_ns, hub, backend);
        }
        if self.state == State::Closed {
            return;
        }
        if let Some(fail) = batch.error {
            self.fail(fail, hub);
        }
    }

    fn classify_input(&self, input: &Input) -> Result<Dispatch, Refusal> {
        match input {
            Input::Frame(frame) => {
                let header = frame.header;
                if header.frame_type == FrameType::Req && header.flags & FLAG_SECTIONED != 0 {
                    return Err(Refusal {
                        code: Code::BadFrame,
                        corr: 0,
                        detail: Detail::Empty,
                    });
                }
                let payload = frame.bytes.get(FRAME_HEADER_LEN..).unwrap_or_default();
                classify(&Query {
                    frame_type: header.frame_type,
                    corr: header.corr,
                    mode: Mode::Binary,
                    phase: self.phase(),
                    level: self.level,
                    prefix: self.client_prefix,
                    payload,
                })
            }
            Input::Line(line) => classify(&Query {
                frame_type: FrameType::Req,
                corr: 0,
                mode: Mode::Json,
                phase: self.phase(),
                level: self.level,
                prefix: self.client_prefix,
                payload: line,
            }),
        }
    }

    fn dispatch(&mut self, input: &Input, now_ns: u64, hub: &mut SendHub, backend: &dyn Backend) {
        let first_line = self.state == State::JsonNoHello;
        match self.classify_input(input) {
            Ok(dispatch) => self.execute(&dispatch, now_ns, hub, backend),
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
        backend: &dyn Backend,
    ) {
        let result = match &dispatch.call {
            Call::Hello(args) => {
                self.minor = args.minor.min(SERVER_MINOR);
                let identity = backend.identity();
                hello_result(self.level, self.minor, &identity, &self.limits)
            }
            Call::Status => {
                let queued = hub.global_pools();
                status_json(&backend.identity(), &backend.status(), queued)
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
}

#[cfg(test)]
mod tests {
    // PLACEHOLDER-TESTS
}
