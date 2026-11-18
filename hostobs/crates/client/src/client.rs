//! The binary connection: handshake, requests and the receive loop (WIRE.md §1.4, §2, §5.2, §7.3).
//!
//! One [`Client`] is one connection, driven by the caller's thread: nothing runs between calls.
//! A wait for a frame is bounded by an operation deadline of `now + timeout` read from the
//! injected [`Clock`]; once a frame is partly received it is bounded only by that frame's
//! completion age (30 s from its first byte, strict `>`, WIRE.md §5.2, §7.3), so a server that
//! chains frames, each completing in time, can keep one call waiting longer than the timeout.
//! The socket timeout is derived from the nearer bound and is never below 1 ms. A failure of the
//! server's bytes, a protocol violation or a timeout closes the connection; a later call is
//! [`ClientError::ConnectionLost`]. A `Client` is not `Send` (its `Transport` and `Clock` carry no
//! `Send` bound) and, while a stream borrowing it is alive, it cannot issue another call.

use core::fmt;
use std::collections::VecDeque;
use std::io::{self, ErrorKind};
use std::ops::Range;
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::time::Duration;

use hostobs_proto::envelope::{check_end, check_evt, check_rep, Kind};
use hostobs_proto::error::ProtoError;
use hostobs_proto::frame::{
    decode_block_with, Block, FrameType, FRAME_HEADER_LEN, MAGIC, PROTO_MAJOR, PROTO_MINOR,
};
use hostobs_proto::stream::{FrameReader, RawFrame};
use hostobs_proto::stream_dicts::{Session, StreamDicts};

use crate::error::{is_fatal_code, ClientError, ProtocolKind};
use crate::hello::{parse_hello, HelloResult, PRE_HELLO_MAX_FRAME};
use crate::json::{self, Object};
use crate::level::Level;
use crate::streams::{BlockStream, StreamCtx, StreamEnd, Subscription};
use crate::transport::{Clock, MonotonicClock, Transport};

/// A frame must complete within 30 s of its first byte (WIRE.md §5.2, D-W16).
const COMPLETION_NS: u64 = 30_000_000_000;

/// The longest operation timeout accepted: one year.
const MAX_TIMEOUT: Duration = Duration::from_secs(365 * 24 * 3600);

/// The shortest transport timeout: a zero timeout would mean "forever" to a socket.
const MIN_WAIT: Duration = Duration::from_millis(1);

/// The size of one read from the transport.
const READ_BUFFER: usize = 64 * 1024;

/// The most bytes a client name may have (WIRE.md §4.4).
const MAX_NAME_BYTES: usize = 64;

/// Error kinds that mean "no byte arrived in time".
const IDLE_KINDS: [ErrorKind; 3] = [
    ErrorKind::WouldBlock,
    ErrorKind::TimedOut,
    ErrorKind::Interrupted,
];

/// Error kinds that mean the peer is gone.
const LOST_KINDS: [ErrorKind; 5] = [
    ErrorKind::ConnectionReset,
    ErrorKind::ConnectionAborted,
    ErrorKind::BrokenPipe,
    ErrorKind::UnexpectedEof,
    ErrorKind::NotConnected,
];

/// Opens a fresh transport to the same peer, for [`Client::reconnect`].
pub type Redial = Box<dyn FnMut() -> io::Result<Box<dyn Transport>>>;

/// Who the client is and how long it waits (WIRE.md §4.4 `client`).
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ClientInfo {
    /// The client's name, sent in `hello` (1..=64 bytes, no control character).
    pub name: String,
    /// The operation timeout: how long one call waits for the server (`(0, 1 year]`).
    pub timeout: Duration,
}

impl ClientInfo {
    /// The timeout of [`ClientInfo::new`]: ten seconds.
    pub const DEFAULT_TIMEOUT: Duration = Duration::from_secs(10);

    /// A client named `name` with the default timeout.
    #[must_use]
    pub fn new(name: impl Into<String>) -> ClientInfo {
        ClientInfo {
            name: name.into(),
            timeout: ClientInfo::DEFAULT_TIMEOUT,
        }
    }

    /// The same client with another operation timeout.
    #[must_use]
    pub fn with_timeout(mut self, timeout: Duration) -> ClientInfo {
        self.timeout = timeout;
        self
    }

    /// Check the name and the timeout; the timeout in nanoseconds.
    fn validate(&self) -> Result<u64, ClientError> {
        let name_ok = !self.name.is_empty()
            && self.name.len() <= MAX_NAME_BYTES
            && !self.name.chars().any(char::is_control);
        if !name_ok {
            return Err(ClientError::Usage("bad-client"));
        }
        if self.timeout.is_zero() || self.timeout > MAX_TIMEOUT {
            return Err(ClientError::Usage("bad-timeout"));
        }
        let nanos = self.timeout.as_nanos();
        Ok(u64::try_from(nanos).unwrap_or(u64::MAX))
    }
}

/// A validated REP of a JSON verb (WIRE.md §2.4).
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Reply {
    /// The whole REP payload: JSON text that passed the envelope checks of WIRE.md §4.2.
    pub text: String,
    result: Range<usize>,
}

impl Reply {
    /// The `result` object of the reply as JSON text.
    #[must_use]
    pub fn result_json(&self) -> &str {
        self.text.get(self.result.clone()).unwrap_or_default()
    }
}

/// What the receive loop hands to a caller.
enum Event {
    /// A REP with a non-zero `corr`.
    Rep(RepFrame),
    /// A BLOCK frame, not yet decoded.
    Block(RawFrame),
    /// An END.
    End(EndFrame),
}

/// A checked REP.
struct RepFrame {
    corr: u32,
    ok: bool,
    text: String,
    body: Range<usize>,
}

/// A checked END.
struct EndFrame {
    corr: u32,
    ok: bool,
    blocks: u32,
    dropped: u32,
    reason: Option<String>,
    next: Option<String>,
    error: Option<String>,
}

/// The error object of an END or REP as a `ClientError::Server`.
fn server_from(object: Object<'_>) -> ClientError {
    ClientError::Server {
        code: object.string("code").unwrap_or_default(),
        message: object.string("message").unwrap_or_default(),
        detail_json: object.raw("detail").unwrap_or_default().to_owned(),
    }
}

/// The server error described by the error object `text` (already checked by the envelope
/// scanner, so the fallback is a formality).
fn server_error(text: &str) -> ClientError {
    Object::parse(text)
        .map(server_from)
        .unwrap_or_else(ClientError::Envelope)
}

/// The `error` object of an END payload.
fn error_text(text: &str) -> Option<String> {
    let object = Object::parse(text).ok()?;
    object.raw("error").map(str::to_owned)
}

/// The text of `range` of `text`, owned.
fn slice_owned(text: &str, range: Option<Range<usize>>) -> Option<String> {
    range.and_then(|r| text.get(r)).map(str::to_owned)
}

/// Whether the reason and cursor of an `END ok:true` fit the stream (WIRE.md §2.2): a query
/// ends `complete`, or `page` with a cursor; a subscription ends `unsubscribed` or `client-eof`.
fn end_reason_ok(live: bool, reason: &str, has_next: bool) -> bool {
    let allowed: &[&str] = if live {
        &["unsubscribed", "client-eof"]
    } else {
        &["complete", "page"]
    };
    allowed.contains(&reason) && (reason == "page") == has_next
}

/// Whether an I/O error means "nothing arrived in time".
fn is_idle(error: &io::Error) -> bool {
    IDLE_KINDS.contains(&error.kind())
}

/// The client error for an I/O failure.
fn classify(error: &io::Error) -> ClientError {
    let kind = error.kind();
    if IDLE_KINDS.contains(&kind) {
        ClientError::Timeout
    } else if LOST_KINDS.contains(&kind) {
        ClientError::ConnectionLost
    } else {
        ClientError::Io(kind)
    }
}

/// `value` as a `usize`; `usize` is at least 32 bits on every supported target, so the
/// saturation is a formality.
fn to_usize(value: u32) -> usize {
    usize::try_from(value).unwrap_or(usize::MAX)
}

/// The length of the first completed frame in `frames` that is longer than `max`.
fn first_over(frames: &VecDeque<RawFrame>, max: u32) -> Option<u32> {
    frames.iter().map(|f| f.header.len).find(|&len| len > max)
}

/// The transport timeout for a wait that ends at `limit_ns` when it is `now_ns`.
fn wait_for(limit_ns: u64, now_ns: u64) -> Duration {
    let left = limit_ns.saturating_sub(now_ns);
    Duration::from_nanos(left).max(MIN_WAIT)
}

/// The negotiated minor of the server's 8-byte prefix.
fn server_minor(prefix: [u8; 8]) -> Result<u16, ClientError> {
    let [m0, m1, m2, m3, major_lo, major_hi, minor_lo, minor_hi] = prefix;
    if [m0, m1, m2, m3] != MAGIC {
        let error = ProtoError::UnknownMode { first: Some(m0) };
        return Err(ClientError::Wire(error));
    }
    let major = u16::from_le_bytes([major_lo, major_hi]);
    let minor = u16::from_le_bytes([minor_lo, minor_hi]);
    if major != PROTO_MAJOR {
        let error = ProtoError::UnsupportedVersion { major };
        return Err(ClientError::Wire(error));
    }
    if minor > PROTO_MINOR {
        return Err(ProtocolKind::HelloMismatch.into());
    }
    Ok(minor)
}

/// Whether the `subscribe` result echoes the request (WIRE.md §4.7): `{"sub": corr,
/// "interval_ms": n}`.
fn confirms(result: &str, corr: u32, interval_ms: u32) -> bool {
    let object = Object::parse(result).ok();
    let sub = object.as_ref().and_then(|o| o.u32("sub"));
    let echoed = object.as_ref().and_then(|o| o.u32("interval_ms"));
    sub == Some(corr) && echoed == Some(interval_ms)
}

/// Check that `selection_json` is one JSON object (and nothing that could add a key to `args`).
fn check_selection(selection_json: &str) -> Result<(), ClientError> {
    let wrapped = format!("{{\"selection\":{selection_json}}}");
    let parsed = Object::parse(&wrapped);
    let object = parsed.map_err(|_| bad_selection())?;
    if object.len() == 1 && object.kind("selection") == Some(Kind::Object) {
        Ok(())
    } else {
        Err(bad_selection())
    }
}

/// The refusal of a selection that is not one JSON object.
fn bad_selection() -> ClientError {
    ClientError::Usage("bad-selection")
}

/// The refusal of a request frame above the server's `max_request_bytes`.
fn too_large() -> ClientError {
    ClientError::Usage("request-too-large")
}

/// The dialer of [`Client::connect`].
fn dialer(path: PathBuf) -> Redial {
    Box::new(move || -> io::Result<Box<dyn Transport>> {
        let stream = UnixStream::connect(&path)?;
        Ok(Box::new(stream))
    })
}

/// One binary-mode connection to a hostobs socket at one level (WIRE.md §1.4).
///
/// The client is single-threaded and pull-driven: a call writes its request and reads until its
/// answer. [`blocks`](Client::blocks) and [`subscribe`](Client::subscribe) return streams that
/// borrow the client, so one stream is live at a time; open a second client for a second
/// concurrent subscription.
pub struct Client {
    transport: Option<Box<dyn Transport>>,
    clock: Box<dyn Clock>,
    redial: Option<Redial>,
    level: Level,
    name: String,
    timeout_ns: u64,
    hello: HelloResult,
    session: Session,
    reader: FrameReader,
    dicts: StreamDicts,
    pending: VecDeque<RawFrame>,
    deferred: Option<ProtoError>,
    buf: Vec<u8>,
    frame_started: u64,
    next_corr: u32,
    closing_reason: Option<String>,
}

impl fmt::Debug for Client {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Client")
            .field("level", &self.level)
            .field("closed", &self.is_closed())
            .finish_non_exhaustive()
    }
}

impl Drop for Client {
    fn drop(&mut self) {
        self.close();
    }
}

impl Client {
    /// Connect to the `AF_UNIX` stream socket `path` and complete the handshake at `level`.
    ///
    /// # Errors
    ///
    /// `Usage` for a bad name or timeout (before any socket exists), `Io` when the connect
    /// fails (the system's error kind), and everything [`Client::from_transport`] reports.
    pub fn connect(
        path: impl AsRef<Path>,
        level: Level,
        info: ClientInfo,
    ) -> Result<Client, ClientError> {
        info.validate()?;
        let mut dial = dialer(path.as_ref().to_path_buf());
        let transport = dial().map_err(|error| ClientError::Io(error.kind()))?;
        let clock = Box::new(MonotonicClock::new());
        Client::from_transport(transport, clock, level, info, Some(dial))
    }

    /// Build a client over `transport` and complete the handshake: the client sends the 8-byte
    /// prefix alone, reads the server's, then sends `hello` with `corr` 1 (WIRE.md §1.4).
    ///
    /// `redial`, when given, opens the transport again for [`Client::reconnect`].
    ///
    /// # Errors
    ///
    /// `Usage` for a bad name or timeout; `Wire(unknown-mode)` or `Wire(unsupported-version)`
    /// for a server prefix of another protocol; `Protocol(HelloMismatch)` for a hello that
    /// contradicts the prefix, the level or the limits rules; `Server` when the server refuses
    /// the hello; `Timeout`, `ConnectionLost` and `Io` for transport failures.
    pub fn from_transport(
        transport: Box<dyn Transport>,
        clock: Box<dyn Clock>,
        level: Level,
        info: ClientInfo,
        redial: Option<Redial>,
    ) -> Result<Client, ClientError> {
        let timeout_ns = info.validate()?;
        let mut client = Client {
            transport: Some(transport),
            clock,
            redial,
            level,
            name: info.name,
            timeout_ns,
            hello: HelloResult::pending(level),
            session: Session::First,
            reader: FrameReader::new(PRE_HELLO_MAX_FRAME, COMPLETION_NS),
            dicts: StreamDicts::new(),
            pending: VecDeque::new(),
            deferred: None,
            buf: vec![0; READ_BUFFER],
            frame_started: 0,
            next_corr: 1,
            closing_reason: None,
        };
        client.open()?;
        Ok(client)
    }

    /// The validated `hello` result of the current connection.
    #[must_use]
    pub fn hello(&self) -> &HelloResult {
        &self.hello
    }

    /// The level this client asked for and the server confirmed.
    #[must_use]
    pub fn level(&self) -> Level {
        self.level
    }

    /// What the last hello said about the probe compared with the one before it (WIRE.md §6.4):
    /// after a [`reconnect`](Client::reconnect) `Changed` means every cursor, open span key,
    /// session and window held from before is invalid.
    #[must_use]
    pub fn session(&self) -> Session {
        self.session
    }

    /// The reason of the last `closing` EVT the server sent, if any.
    #[must_use]
    pub fn closing_reason(&self) -> Option<&str> {
        self.closing_reason.as_deref()
    }

    /// Whether the connection is closed.
    #[must_use]
    pub fn is_closed(&self) -> bool {
        self.transport.is_none()
    }

    /// Close the connection and forget every dictionary; idempotent (also on drop).
    pub fn close(&mut self) {
        if let Some(mut transport) = self.transport.take() {
            transport.close();
        }
        self.pending.clear();
        self.deferred = None;
        self.dicts.reset();
    }

    /// Reconnect (WIRE.md §6.4): close, open a new transport through the dialer, redo the
    /// handshake and drop every dictionary. The result says whether the probe is the same
    /// (`Same`) or restarted (`Changed`); a restarted probe invalidates cursors and sessions.
    /// Streams borrow the client, so none can outlive a reconnect: the caller resubscribes.
    ///
    /// # Errors
    ///
    /// `Usage("no-reconnect")` for a client without a dialer, `Io` when the dial fails, and
    /// everything the handshake reports.
    pub fn reconnect(&mut self) -> Result<Session, ClientError> {
        self.close();
        let dial = self.redial.as_mut();
        let redial = dial.ok_or(ClientError::Usage("no-reconnect"))?;
        let transport = redial().map_err(|error| ClientError::Io(error.kind()))?;
        self.transport = Some(transport);
        self.reader = FrameReader::new(PRE_HELLO_MAX_FRAME, COMPLETION_NS);
        self.hello = HelloResult::pending(self.level);
        self.next_corr = 1;
        self.closing_reason = None;
        self.open()?;
        Ok(self.session)
    }

    /// Send a JSON verb and return its reply. `args_json` is JSON text the caller owns: one
    /// object, checked locally (WIRE.md §4.2: UTF-8, depth at most 8 counting the envelope)
    /// before anything is sent.
    ///
    /// # Errors
    ///
    /// `Usage` for a bad verb, args or size (nothing sent, connection untouched), `Server` for
    /// an error reply (the connection is closed when the code is connection-fatal, §5.4), and
    /// the transport and protocol errors of [`ClientError`].
    pub fn request(&mut self, verb: &str, args_json: &str) -> Result<Reply, ClientError> {
        let payload = json::request_payload(verb, args_json)?;
        let (corr, deadline) = self.begin(&payload)?;
        self.wait_reply(corr, deadline)
    }

    /// Send a block verb (`current`, `snapshot_at`, `series_batch`) and return the stream of its
    /// BLOCK frames; the stream header is [`BlockStream::reply`]. The stream holds a dictionary
    /// for its `corr` (WIRE.md §6.2) and checks the END counts.
    ///
    /// # Errors
    ///
    /// As [`Client::request`] for the request and the first REP.
    pub fn blocks(&mut self, verb: &str, args_json: &str) -> Result<BlockStream<'_>, ClientError> {
        let payload = json::request_payload(verb, args_json)?;
        let (corr, deadline) = self.begin(&payload)?;
        let reply = self.wait_reply(corr, deadline)?;
        Ok(BlockStream::new(self, corr, reply))
    }

    /// Subscribe to `selection_json` (one JSON object, WIRE.md §4.2) every `interval_ms`
    /// (WIRE.md §4.7). The returned [`Subscription`] iterates the live blocks.
    ///
    /// # Errors
    ///
    /// `Usage("bad-selection")` when `selection_json` is not one JSON object, otherwise as
    /// [`Client::blocks`]; `Protocol(BadReply)` when the result does not echo the request.
    pub fn subscribe(
        &mut self,
        selection_json: &str,
        interval_ms: u32,
    ) -> Result<Subscription<'_>, ClientError> {
        check_selection(selection_json)?;
        let mut args = String::from("{\"selection\":");
        args.push_str(selection_json);
        args.push_str(",\"interval_ms\":");
        args.push_str(&interval_ms.to_string());
        args.push('}');
        let payload = json::request_payload("subscribe", &args)?;
        let (corr, deadline) = self.begin(&payload)?;
        let reply = self.wait_reply(corr, deadline)?;
        if !confirms(reply.result_json(), corr, interval_ms) {
            return Err(self.fail(ProtocolKind::BadReply.into()));
        }
        Ok(Subscription::new(self, corr, interval_ms))
    }

    // ---- crate-internal helpers used by the streams ------------------------------------

    /// The deadline of an operation that starts now and may wait `extra_ns` beyond the timeout.
    pub(crate) fn deadline_after(&self, extra_ns: u64) -> u64 {
        let wait = self.timeout_ns.saturating_add(extra_ns);
        self.clock.now_ns().saturating_add(wait)
    }

    /// The next BLOCK of the stream `ctx`, or `None` once its END is in.
    pub(crate) fn next_block(
        &mut self,
        ctx: &mut StreamCtx,
        deadline: u64,
    ) -> Result<Option<Block>, ClientError> {
        match self.recv_event(deadline)? {
            Event::Block(frame) => self.on_block(ctx, &frame).map(Some),
            Event::End(end) => self.on_end(ctx, end).map(|()| None),
            Event::Rep(_) => Err(self.fail(ProtocolKind::UnknownCorr.into())),
        }
    }

    /// Read and discard the rest of the stream `ctx` up to its END (blocks are counted, not
    /// decoded).
    pub(crate) fn drain(&mut self, ctx: &mut StreamCtx) -> Result<(), ClientError> {
        while ctx.end.is_none() {
            let deadline = self.deadline_after(0);
            match self.recv_event(deadline)? {
                Event::Block(frame) => self.count_block(ctx, &frame)?,
                Event::End(end) => self.on_end(ctx, end)?,
                Event::Rep(_) => return Err(self.fail(ProtocolKind::UnknownCorr.into())),
            }
        }
        Ok(())
    }

    /// End the subscription `ctx`: `unsubscribe`, its REP, then the END of the stream. A
    /// `not-found` REP is tolerated (the server already ended it); blocks that arrive meanwhile
    /// are counted and dropped.
    pub(crate) fn unsubscribe(&mut self, ctx: &mut StreamCtx) -> Result<(), ClientError> {
        let args = format!("{{\"sub\":{}}}", ctx.corr);
        let payload = json::request_payload("unsubscribe", &args)?;
        let (corr, deadline) = self.begin(&payload)?;
        let replied = loop {
            match self.recv_event(deadline)? {
                Event::Rep(rep) if rep.corr == corr => break self.reply_of(rep),
                Event::Block(frame) => self.count_block(ctx, &frame)?,
                Event::End(end) => self.on_end(ctx, end)?,
                Event::Rep(_) => return Err(self.fail(ProtocolKind::UnknownCorr.into())),
            }
        };
        match replied {
            Ok(_) => {}
            Err(ClientError::Server { code, .. }) if code == "not-found" => {}
            Err(error) => return Err(error),
        }
        self.drain(ctx)
    }

    // ---- connection set-up ----------------------------------------------------------------

    /// Run the handshake; any failure closes the connection.
    fn open(&mut self) -> Result<(), ClientError> {
        let opened = self.handshake();
        opened.map_err(|error| self.fail(error))
    }

    fn handshake(&mut self) -> Result<(), ClientError> {
        let deadline = self.deadline_after(0);
        let mut prefix = MAGIC.to_vec();
        prefix.extend_from_slice(&PROTO_MAJOR.to_le_bytes());
        prefix.extend_from_slice(&PROTO_MINOR.to_le_bytes());
        self.send(&prefix, deadline)?;
        let server = self.read_prefix(deadline)?;
        let minor = server_minor(server)?;
        let corr = self.allocate()?;
        let args = json::hello_args(&self.name);
        let payload = json::request_payload("hello", &args)?;
        self.send_request(corr, &payload, deadline)?;
        let reply = self.wait_reply(corr, deadline)?;
        let total = FRAME_HEADER_LEN + payload.len();
        let hello_len = u32::try_from(total).unwrap_or(u32::MAX);
        let hello = parse_hello(reply.result_json(), minor, self.level, hello_len)?;
        self.session = self.dicts.on_hello(&hello.boot_id, hello.epoch);
        let max_frame = hello.limits.max_frame_bytes;
        if let Some(len) = first_over(&self.pending, max_frame) {
            let error = ProtoError::OversizedFrame {
                len: to_usize(len),
                max: to_usize(max_frame),
            };
            return Err(ClientError::Wire(error));
        }
        if self.reader.buffered_len() == 0 {
            self.reader = FrameReader::new(max_frame, COMPLETION_NS);
        }
        self.hello = hello;
        Ok(())
    }

    /// Read exactly the 8 prefix bytes (never more: the hello reply follows our hello).
    fn read_prefix(&mut self, deadline: u64) -> Result<[u8; 8], ClientError> {
        let mut prefix = [0u8; 8];
        let mut have = 0;
        while have < prefix.len() {
            let wait = wait_for(deadline, self.clock.now_ns());
            let slot = self.transport.as_mut();
            let transport = slot.ok_or(ClientError::ConnectionLost)?;
            let rest = prefix.get_mut(have..).unwrap_or_default();
            match transport.read(rest, wait) {
                Ok(0) => return Err(self.fail(ClientError::ConnectionLost)),
                Ok(got) => have += got,
                Err(error) if is_idle(&error) => {
                    if self.clock.now_ns() > deadline {
                        return Err(self.fail(ClientError::Timeout));
                    }
                }
                Err(error) => return Err(self.fail(classify(&error))),
            }
        }
        Ok(prefix)
    }

    // ---- sending --------------------------------------------------------------------------

    /// Check the open connection and the size of `payload`, allocate a `corr` and send the
    /// request; the `corr` and the operation deadline.
    fn begin(&mut self, payload: &str) -> Result<(u32, u64), ClientError> {
        if self.transport.is_none() {
            return Err(ClientError::ConnectionLost);
        }
        let total = FRAME_HEADER_LEN + payload.len();
        if total > to_usize(self.hello.limits.max_request_bytes) {
            return Err(too_large());
        }
        let deadline = self.deadline_after(0);
        let corr = self.allocate()?;
        self.send_request(corr, payload, deadline)?;
        Ok((corr, deadline))
    }

    /// The next `corr`: monotonic from 1; every value is used once (WIRE.md §2.3).
    fn allocate(&mut self) -> Result<u32, ClientError> {
        let corr = self.next_corr;
        if corr == 0 {
            return Err(self.fail(ProtocolKind::CorrExhausted.into()));
        }
        self.next_corr = corr.checked_add(1).unwrap_or(0);
        Ok(corr)
    }

    fn send_request(&mut self, corr: u32, payload: &str, deadline: u64) -> Result<(), ClientError> {
        let total = FRAME_HEADER_LEN + payload.len();
        let sized = u32::try_from(total);
        let len = sized.map_err(|_| too_large())?;
        let mut frame = Vec::with_capacity(total);
        frame.extend_from_slice(&len.to_le_bytes());
        frame.extend_from_slice(&[FrameType::Req.code(), 0, 0, 0]);
        frame.extend_from_slice(&corr.to_le_bytes());
        frame.extend_from_slice(payload.as_bytes());
        self.send(&frame, deadline)
    }

    fn send(&mut self, data: &[u8], deadline: u64) -> Result<(), ClientError> {
        let wait = wait_for(deadline, self.clock.now_ns());
        let slot = self.transport.as_mut();
        let transport = slot.ok_or(ClientError::ConnectionLost)?;
        let sent = transport.write_all(data, wait);
        sent.map_err(|error| self.fail(classify(&error)))
    }

    /// Close the connection and return `error`.
    fn fail(&mut self, error: ClientError) -> ClientError {
        self.close();
        error
    }

    // ---- receiving ------------------------------------------------------------------------

    /// The next complete frame: frames the reader already completed first, then the reader's
    /// deferred error, else one more read.
    fn next_frame(&mut self, deadline: u64) -> Result<RawFrame, ClientError> {
        loop {
            if let Some(frame) = self.pending.pop_front() {
                return Ok(frame);
            }
            if let Some(error) = self.deferred.take() {
                return Err(self.fail(ClientError::Wire(error)));
            }
            self.read_some(deadline)?;
        }
    }

    /// One transport read, bounded by the nearer of the operation deadline and the completion
    /// age of a partly received frame (WIRE.md §7.3).
    fn read_some(&mut self, deadline: u64) -> Result<(), ClientError> {
        let now = self.clock.now_ns();
        let polled = self.reader.poll(now);
        polled.map_err(|error| self.fail(error.into()))?;
        let partial = self.reader.buffered_len() > 0;
        if !partial && now > deadline {
            return Err(self.fail(ClientError::Timeout));
        }
        let limit = if partial {
            self.frame_started.saturating_add(COMPLETION_NS)
        } else {
            deadline
        };
        let wait = wait_for(limit, now);
        let slot = self.transport.as_mut();
        let transport = slot.ok_or(ClientError::ConnectionLost)?;
        match transport.read(&mut self.buf, wait) {
            Ok(0) => Err(self.fail(ClientError::ConnectionLost)),
            Ok(got) => {
                self.absorb(got);
                Ok(())
            }
            Err(error) if is_idle(&error) => self.after_idle(deadline),
            Err(error) => Err(self.fail(classify(&error))),
        }
    }

    /// Feed `got` bytes of the read buffer to the reader.
    fn absorb(&mut self, got: usize) {
        let now = self.clock.now_ns();
        let boundary = self.reader.buffered_len() == 0;
        let chunk = self.buf.get(..got).unwrap_or_default();
        let feed = self.reader.feed(chunk, now);
        if boundary || !feed.frames.is_empty() {
            self.frame_started = now;
        }
        self.pending.extend(feed.frames);
        self.deferred = feed.error;
    }

    /// A read timed out: judge the completion age and the operation deadline.
    fn after_idle(&mut self, deadline: u64) -> Result<(), ClientError> {
        let now = self.clock.now_ns();
        let polled = self.reader.poll(now);
        polled.map_err(|error| self.fail(error.into()))?;
        if self.reader.buffered_len() == 0 && now > deadline {
            return Err(self.fail(ClientError::Timeout));
        }
        Ok(())
    }

    /// The next REP, BLOCK or END; EVT frames are consumed here.
    fn recv_event(&mut self, deadline: u64) -> Result<Event, ClientError> {
        loop {
            let frame = self.next_frame(deadline)?;
            if let Some(event) = self.dispatch(frame)? {
                return Ok(event);
            }
        }
    }

    fn dispatch(&mut self, frame: RawFrame) -> Result<Option<Event>, ClientError> {
        let corr = frame.header.corr;
        let payload = frame.bytes.get(FRAME_HEADER_LEN..).unwrap_or_default();
        match frame.header.frame_type {
            FrameType::Rep => self.on_rep(corr, payload),
            FrameType::End => self.on_end_frame(corr, payload),
            FrameType::Evt => self.on_evt(payload),
            FrameType::Block => Ok(Some(Event::Block(frame))),
            FrameType::Req | FrameType::Dict => {
                Err(self.fail(ProtocolKind::UnexpectedFrame.into()))
            }
        }
    }

    fn on_rep(&mut self, corr: u32, payload: &[u8]) -> Result<Option<Event>, ClientError> {
        let checked = check_rep(payload, false);
        let rep = checked.map_err(|error| self.fail(error.into()))?;
        let text = String::from_utf8_lossy(payload).into_owned();
        if corr == 0 && rep.ok {
            return Err(self.fail(ProtocolKind::BadReply.into()));
        }
        if corr == 0 {
            let error = server_error(text.get(rep.body).unwrap_or_default());
            return Err(self.fail(error));
        }
        let event = Event::Rep(RepFrame {
            corr,
            ok: rep.ok,
            text,
            body: rep.body,
        });
        Ok(Some(event))
    }

    fn on_end_frame(&mut self, corr: u32, payload: &[u8]) -> Result<Option<Event>, ClientError> {
        let checked = check_end(payload);
        let end = checked.map_err(|error| self.fail(error.into()))?;
        let text = String::from_utf8_lossy(payload).into_owned();
        let event = Event::End(EndFrame {
            corr,
            ok: end.ok,
            blocks: end.blocks,
            dropped: end.dropped,
            reason: slice_owned(&text, end.reason),
            next: slice_owned(&text, end.next),
            error: error_text(&text),
        });
        Ok(Some(event))
    }

    fn on_evt(&mut self, payload: &[u8]) -> Result<Option<Event>, ClientError> {
        let checked = check_evt(payload);
        let evt = checked.map_err(|error| self.fail(error.into()))?;
        let text = String::from_utf8_lossy(payload).into_owned();
        if text.get(evt.event) == Some("closing") {
            self.closing_reason = Object::parse(&text).ok().and_then(|o| o.string("reason"));
        }
        Ok(None)
    }

    // ---- replies and streams --------------------------------------------------------------

    /// Wait for the REP of `corr`; an error reply is `ClientError::Server`.
    fn wait_reply(&mut self, corr: u32, deadline: u64) -> Result<Reply, ClientError> {
        match self.recv_event(deadline)? {
            Event::Rep(rep) if rep.corr == corr => self.reply_of(rep),
            _ => Err(self.fail(ProtocolKind::UnknownCorr.into())),
        }
    }

    fn reply_of(&mut self, rep: RepFrame) -> Result<Reply, ClientError> {
        if rep.ok {
            return Ok(Reply {
                text: rep.text,
                result: rep.body,
            });
        }
        let text = rep.text.get(rep.body).unwrap_or_default();
        Err(self.server_failure(text))
    }

    /// The server error of `text`; a connection-fatal code closes the connection.
    fn server_failure(&mut self, text: &str) -> ClientError {
        let error = server_error(text);
        if is_fatal_code(error.kind()) {
            self.close();
        }
        error
    }

    fn on_block(&mut self, ctx: &mut StreamCtx, frame: &RawFrame) -> Result<Block, ClientError> {
        self.count_block(ctx, frame)?;
        let decoded = decode_block_with(&frame.bytes, self.dicts.held(ctx.corr));
        let block = decoded.map_err(|error| self.fail(error.into()))?;
        let accepted = self.dicts.accept(ctx.corr, &block);
        accepted.map_err(|error| self.fail(error.into()))?;
        let dropped = u64::from(block.header.dropped);
        ctx.header_dropped = ctx.header_dropped.saturating_add(dropped);
        Ok(block)
    }

    /// Count one BLOCK of the stream (a frame of another `corr` is a protocol error).
    fn count_block(&mut self, ctx: &mut StreamCtx, frame: &RawFrame) -> Result<(), ClientError> {
        if frame.header.corr != ctx.corr {
            return Err(self.fail(ProtocolKind::UnknownCorr.into()));
        }
        ctx.received = ctx.received.saturating_add(1);
        Ok(())
    }

    fn on_end(&mut self, ctx: &mut StreamCtx, end: EndFrame) -> Result<(), ClientError> {
        if end.corr != ctx.corr {
            return Err(self.fail(ProtocolKind::UnknownCorr.into()));
        }
        let counted = end.blocks == ctx.received && (ctx.live || end.dropped == 0);
        if !counted {
            return Err(self.fail(ProtocolKind::EndCounts.into()));
        }
        self.dicts.close(ctx.corr);
        if !end.ok {
            ctx.header_dropped = ctx.header_dropped.saturating_add(u64::from(end.dropped));
            let text = end.error.unwrap_or_default();
            return Err(self.server_failure(&text));
        }
        let reason = end.reason.unwrap_or_default();
        if !end_reason_ok(ctx.live, &reason, end.next.is_some()) {
            return Err(self.fail(ProtocolKind::BadReply.into()));
        }
        ctx.end = Some(StreamEnd {
            blocks: end.blocks,
            dropped: end.dropped,
            reason,
            next: end.next,
        });
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::{
        io, Client, ClientError, ClientInfo, Clock, Duration, FrameReader, HelloResult, Level,
        ProtocolKind, Session, StreamDicts, Transport, VecDeque, COMPLETION_NS, MAX_TIMEOUT,
        PRE_HELLO_MAX_FRAME,
    };
    use hostobs_proto::error::ProtoError;
    use std::cell::Cell;
    use std::rc::Rc;

    struct Mute;

    impl Transport for Mute {
        fn read(&mut self, _buf: &mut [u8], _timeout: Duration) -> io::Result<usize> {
            Ok(0)
        }

        fn write_all(&mut self, _data: &[u8], _timeout: Duration) -> io::Result<()> {
            Ok(())
        }

        fn close(&mut self) {}
    }

    struct Zero;

    impl Clock for Zero {
        fn now_ns(&self) -> u64 {
            0
        }
    }

    struct ManualClock(Rc<Cell<u64>>);

    impl Clock for ManualClock {
        fn now_ns(&self) -> u64 {
            self.0.get()
        }
    }

    /// A client that never did a handshake, whose next `corr` is `next_corr`.
    fn bare(next_corr: u32) -> Client {
        Client {
            transport: Some(Box::new(Mute)),
            clock: Box::new(Zero),
            redial: None,
            level: Level::Observe,
            name: "t".to_owned(),
            timeout_ns: 1,
            hello: HelloResult::pending(Level::Observe),
            session: Session::First,
            reader: FrameReader::new(PRE_HELLO_MAX_FRAME, COMPLETION_NS),
            dicts: StreamDicts::new(),
            pending: VecDeque::new(),
            deferred: None,
            buf: Vec::new(),
            frame_started: 0,
            next_corr,
            closing_reason: None,
        }
    }

    #[test]
    fn corr_runs_up_to_u32_max_and_then_is_exhausted() {
        let mut client = bare(u32::MAX - 1);
        assert_eq!(client.allocate(), Ok(u32::MAX - 1));
        assert_eq!(client.allocate(), Ok(u32::MAX));
        assert!(!client.is_closed());
        let exhausted = ClientError::Protocol(ProtocolKind::CorrExhausted);
        assert_eq!(client.allocate(), Err(exhausted));
        assert!(client.is_closed());
    }

    #[test]
    fn client_info_accepts_the_one_year_timeout_boundary() {
        let boundary = Duration::from_secs(31_536_000);
        assert_eq!(MAX_TIMEOUT, boundary);
        let nanos = u64::try_from(boundary.as_nanos()).expect("fits");
        assert_eq!(
            ClientInfo::new("a").with_timeout(boundary).validate(),
            Ok(nanos)
        );
        let over = boundary + Duration::from_nanos(1);
        assert_eq!(
            ClientInfo::new("a").with_timeout(over).validate(),
            Err(ClientError::Usage("bad-timeout"))
        );
        let longest_name = "n".repeat(64);
        assert!(ClientInfo::new(longest_name).validate().is_ok());
    }

    #[test]
    fn after_idle_enforces_operation_and_completion_deadlines() {
        let clock = Rc::new(Cell::new(1));
        let mut empty = bare(1);
        empty.clock = Box::new(ManualClock(Rc::clone(&clock)));
        assert_eq!(empty.after_idle(0), Err(ClientError::Timeout));
        assert!(empty.is_closed());

        clock.set(0);
        let mut partial = bare(1);
        partial.clock = Box::new(ManualClock(Rc::clone(&clock)));
        partial.reader.feed(&[12, 0, 0, 0, 2], 0);
        clock.set(COMPLETION_NS + 1);
        assert_eq!(
            partial.after_idle(u64::MAX),
            Err(ClientError::Wire(ProtoError::Deadline))
        );
        assert!(partial.is_closed());
    }
}
