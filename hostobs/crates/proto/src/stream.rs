//! The stream layer of the wire protocol (WIRE.md §5.2, §5.3 mode detection, §5.4 timers).
//!
//! Three pieces, all pure: no sockets, no threads and no clock. Every time is an injected
//! monotonic `now_ns`, so each transition is a fake-clock unit test.
//!
//! - [`FrameReader`]: the §5.2 state machine that turns a byte stream into whole frames. It
//!   judges each header as soon as its 12 bytes are in, holds a frame's bytes only as they
//!   arrive, and returns the frames it completed **and** at most one error from one `feed`
//!   call, so a frame completed before a defect is never lost.
//! - [`ModeDetect`]: the first-bytes detector of a server connection (`{`, the 8-byte binary
//!   prefix, or neither). It hands back where the unconsumed bytes start, so surplus bytes
//!   after the prefix reach the reader in the same call.
//! - [`ConnTimers`]: the hello and idle deadlines of §5.4. The request deadline is not a
//!   timer here: it is the reader's completion age.
//!
//! # Contracts
//!
//! - **The age runs from a frame's first byte.** `started` is stamped when the first byte of
//!   a frame arrives (also for surplus bytes that open the next frame inside one `feed`), so
//!   time spent idle at a frame boundary never counts, and a frame that is never completed
//!   is bounded however slowly it dribbles in.
//! - **A deadline fires strictly after it:** `now > started + completion`.
//! - **No allocation from a declared length alone.** The header buffer is 12 bytes. A body
//!   buffer grows with the bytes received: its capacity never exceeds
//!   `min(len, received + 64 KiB)`. An oversized or malformed header is refused before a
//!   byte of body is stored.
//! - **An error is final.** After the first error the reader is poisoned: every later `feed`,
//!   `poll` and `eof` returns the same error and no frames.
//! - **No panic on untrusted bytes.** Every slice is taken with `get`, every length is
//!   converted with `try_from`.

use crate::error::ProtoError;
use crate::frame::{FrameHeader, FRAME_HEADER_LEN, MAGIC};

/// How far past the received bytes a body buffer may reserve (WIRE.md §5.2 buffering).
const GROWTH_STEP: usize = 64 * 1024;

/// The length of the binary prefix `HOB\x01` + `u16 major` + `u16 minor` (WIRE.md §1.3).
const PREFIX_LEN: usize = 8;

/// `value` as a `usize`; `usize` is at least 32 bits wide on every supported target, so the
/// saturation is a formality, not a path.
fn to_usize(value: u32) -> usize {
    usize::try_from(value).unwrap_or(usize::MAX)
}

/// A refusal of the codec's own length limit names the reader's limit instead, so the
/// diagnostic states the bound the caller configured.
fn with_reader_limit(error: ProtoError, max_frame: u32) -> ProtoError {
    match error {
        ProtoError::OversizedFrame { len, .. } => ProtoError::OversizedFrame {
            len,
            max: to_usize(max_frame),
        },
        other => other,
    }
}

/// One complete frame: the parsed header and every byte of the frame, header included.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RawFrame {
    /// The validated header (`12 <= len <= max_frame`).
    pub header: FrameHeader,
    /// The whole frame, `header.len` bytes, header first.
    pub bytes: Vec<u8>,
}

/// The result of one [`FrameReader::feed`] call.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Feed {
    /// The frames completed by this call, in stream order. Frames completed before an
    /// error in the same call are here, never dropped.
    pub frames: Vec<RawFrame>,
    /// The error that poisoned the reader in this call (or an earlier one), if any.
    pub error: Option<ProtoError>,
}

/// The reader's states (WIRE.md §5.2). The body bytes held so far are `buf.len()`.
///
/// `Poisoned(e)` and `Closed` of the table are the reader's `poisoned` and `closed` fields:
/// both are terminal and independent of where the frame machine stopped (a clean close is
/// only possible at `Header{0}`).
#[derive(Clone, Copy, Debug)]
enum State {
    /// Collecting the 12-byte header; `have` bytes are in `buf`.
    Header { have: usize },
    /// Header validated; collecting the rest of the frame.
    Body { header: FrameHeader },
}

/// The §5.2 frame reader, shared by both sides (the server's request reader and the
/// client's reply reader).
#[derive(Debug)]
pub struct FrameReader {
    max_frame: u32,
    completion_ns: u64,
    state: State,
    buf: Vec<u8>,
    started_ns: u64,
    poisoned: Option<ProtoError>,
    closed: bool,
}

impl FrameReader {
    /// A reader that accepts frames of at most `max_frame` bytes (header included) and
    /// requires every frame to complete within `completion_ns` of its first byte.
    ///
    /// The effective maximum is also bounded by the codec's own 16 MiB frame limit
    /// ([`crate::error::MAX_FRAME_BYTES`]): a larger `max_frame` does not widen it.
    #[must_use]
    pub fn new(max_frame: u32, completion_ns: u64) -> FrameReader {
        FrameReader {
            max_frame,
            completion_ns,
            state: State::Header { have: 0 },
            buf: Vec::with_capacity(FRAME_HEADER_LEN),
            started_ns: 0,
            poisoned: None,
            closed: false,
        }
    }

    /// Consume `bytes` received at `now_ns` and return the frames they completed and at
    /// most one error.
    ///
    /// An empty `bytes` is not an event: it stamps nothing and changes nothing (a poisoned
    /// reader still reports its error). A feed after a clean [`eof`](Self::eof) is
    /// `trailing-bytes`.
    pub fn feed(&mut self, bytes: &[u8], now_ns: u64) -> Feed {
        let mut frames = Vec::new();
        if let Some(error) = &self.poisoned {
            return Feed {
                frames,
                error: Some(error.clone()),
            };
        }
        if self.closed && !bytes.is_empty() {
            let extra = bytes.len();
            let error = self.poison(ProtoError::TrailingBytes { extra });
            return Feed {
                frames,
                error: Some(error),
            };
        }
        let mut rest = bytes;
        while !rest.is_empty() {
            match self.advance(rest, now_ns, &mut frames) {
                Ok(used) => rest = rest.get(used..).unwrap_or_default(),
                Err(error) => {
                    let error = self.poison(error);
                    return Feed {
                        frames,
                        error: Some(error),
                    };
                }
            }
        }
        Feed {
            frames,
            error: None,
        }
    }

    /// Check the completion age at `now_ns`.
    ///
    /// # Errors
    ///
    /// [`ProtoError::Deadline`] when a frame is partly received and `now_ns > started +
    /// completion` (the reader is then poisoned), or the poisoning error again. At a frame
    /// boundary (`Header{0}`) there is nothing to time: an idle connection is §5.4's
    /// business.
    pub fn poll(&mut self, now_ns: u64) -> Result<(), ProtoError> {
        if let Some(error) = &self.poisoned {
            return Err(error.clone());
        }
        match self.state {
            State::Header { have: 0 } => Ok(()),
            State::Header { .. } | State::Body { .. } => {
                if now_ns > self.started_ns.saturating_add(self.completion_ns) {
                    return Err(self.poison(ProtoError::Deadline));
                }
                Ok(())
            }
        }
    }

    /// The peer ended the stream.
    ///
    /// # Errors
    ///
    /// [`ProtoError::Truncated`] when a frame is partly received (the reader is then
    /// poisoned), or the poisoning error again. At a frame boundary the reader becomes
    /// `Closed` and the result is `Ok`; a second `eof` stays `Ok`.
    pub fn eof(&mut self) -> Result<(), ProtoError> {
        if let Some(error) = &self.poisoned {
            return Err(error.clone());
        }
        match self.state {
            State::Header { have: 0 } => {
                self.closed = true;
                Ok(())
            }
            State::Header { have } => Err(self.poison(ProtoError::Truncated {
                context: "frame header at end of stream",
                need: FRAME_HEADER_LEN,
                have,
            })),
            State::Body { header } => {
                let need = to_usize(header.len);
                let have = self.buf.len();
                Err(self.poison(ProtoError::Truncated {
                    context: "frame body at end of stream",
                    need,
                    have,
                }))
            }
        }
    }

    /// The bytes of the partly received frame held now (0 at a frame boundary).
    #[must_use]
    pub fn buffered_len(&self) -> usize {
        self.buf.len()
    }

    /// The capacity of the reader's buffer: at most 12 until a body byte arrives, then at
    /// most `min(len, received + 64 KiB)`; 0 once poisoned.
    #[must_use]
    pub fn buffered_capacity(&self) -> usize {
        self.buf.capacity()
    }

    fn poison(&mut self, error: ProtoError) -> ProtoError {
        self.buf = Vec::new();
        self.poisoned = Some(error.clone());
        error
    }

    fn advance(
        &mut self,
        rest: &[u8],
        now_ns: u64,
        frames: &mut Vec<RawFrame>,
    ) -> Result<usize, ProtoError> {
        match self.state {
            State::Header { have } => self.advance_header(have, rest, now_ns, frames),
            State::Body { header } => Ok(self.advance_body(header, rest, frames)),
        }
    }

    fn advance_header(
        &mut self,
        have: usize,
        rest: &[u8],
        now_ns: u64,
        frames: &mut Vec<RawFrame>,
    ) -> Result<usize, ProtoError> {
        if have == 0 {
            self.started_ns = now_ns;
        }
        let take = rest.len().min(FRAME_HEADER_LEN.saturating_sub(have));
        let chunk = rest.get(..take).unwrap_or_default();
        self.buf.extend_from_slice(chunk);
        let have = have + take;
        if have < FRAME_HEADER_LEN {
            self.state = State::Header { have };
            return Ok(take);
        }
        let max_frame = self.max_frame;
        let parsed = FrameHeader::parse(&self.buf);
        let header = parsed.map_err(|e| with_reader_limit(e, max_frame))?;
        let len = to_usize(header.len);
        let max = to_usize(max_frame);
        if len > max {
            return Err(ProtoError::OversizedFrame { len, max });
        }
        if len == FRAME_HEADER_LEN {
            self.emit(header, frames);
        } else {
            self.state = State::Body { header };
        }
        Ok(take)
    }

    fn advance_body(
        &mut self,
        header: FrameHeader,
        rest: &[u8],
        frames: &mut Vec<RawFrame>,
    ) -> usize {
        let total = to_usize(header.len);
        let have = self.buf.len();
        let take = rest.len().min(total.saturating_sub(have));
        let target = have + take;
        if self.buf.capacity() < target {
            let want = total.min(target.saturating_add(GROWTH_STEP));
            self.buf.reserve_exact(want.saturating_sub(have));
        }
        let chunk = rest.get(..take).unwrap_or_default();
        self.buf.extend_from_slice(chunk);
        if self.buf.len() >= total {
            self.emit(header, frames);
        }
        take
    }

    fn emit(&mut self, header: FrameHeader, frames: &mut Vec<RawFrame>) {
        let fresh = Vec::with_capacity(FRAME_HEADER_LEN);
        let bytes = std::mem::replace(&mut self.buf, fresh);
        frames.push(RawFrame { header, bytes });
        self.state = State::Header { have: 0 };
    }
}

/// What [`ModeDetect::feed`] decided (WIRE.md §5.3 mode detection).
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Detect {
    /// Everything was consumed and fewer than 8 prefix bytes have been seen.
    Need,
    /// The first byte is `{`: JSON lines for the whole connection. `at` is the index of
    /// that byte in the fed slice (it is the line's first byte, so it is not consumed).
    Json {
        /// The index in the fed bytes of the first byte not consumed by the detector.
        at: usize,
    },
    /// The 8-byte binary prefix is complete: `HOB\x01` + `u16 major` + `u16 minor`. The
    /// caller applies §1.4 to the version fields.
    Binary {
        /// The eight prefix bytes (possibly collected over several feeds).
        prefix: [u8; 8],
        /// The index in the fed bytes of the first byte after the prefix.
        at: usize,
    },
    /// The first bytes are neither: the first byte is not `{` and not `H`, or one of the
    /// first four bytes diverges from `HOB\x01`.
    Unknown,
}

/// The first-bytes detector of a server connection.
///
/// It decides at the earliest byte it can: `{` at offset 0 is JSON; a byte of the first
/// four that differs from `HOB\x01` is `Unknown`; bytes 5 to 8 (the version) may be any
/// value. A detector that returned anything but [`Detect::Need`] is finished, and a further
/// [`feed`](Self::feed) is a caller bug that answers `Unknown` in every build.
#[derive(Debug, Default)]
pub struct ModeDetect {
    have: usize,
    prefix: [u8; PREFIX_LEN],
    finished: bool,
}

impl ModeDetect {
    /// A detector that has seen nothing.
    #[must_use]
    pub fn new() -> ModeDetect {
        ModeDetect::default()
    }

    /// Feed the next received bytes and report the decision so far.
    pub fn feed(&mut self, bytes: &[u8]) -> Detect {
        if self.finished {
            return Detect::Unknown;
        }
        for (index, &byte) in bytes.iter().enumerate() {
            if self.have == 0 && byte == b'{' {
                self.finished = true;
                return Detect::Json { at: index };
            }
            let diverges = match MAGIC.get(self.have) {
                Some(expected) => *expected != byte,
                None => false,
            };
            if diverges {
                self.finished = true;
                return Detect::Unknown;
            }
            if let Some(slot) = self.prefix.get_mut(self.have) {
                *slot = byte;
            }
            self.have += 1;
            if self.have == PREFIX_LEN {
                self.finished = true;
                return Detect::Binary {
                    prefix: self.prefix,
                    at: index + 1,
                };
            }
        }
        Detect::Need
    }
}

/// Which connection timer fired.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Timer {
    /// The hello deadline (§5.4 `hello-timer`): accept to hello complete.
    Hello,
    /// The idle deadline (§5.4): `Ready` and nothing outstanding for `idle_ms`.
    Idle,
}

/// The hello and idle deadlines of one connection (WIRE.md §5.4).
///
/// A deadline fires when `now > deadline`, strictly. The request deadline is the reader's
/// completion age and is not held here.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ConnTimers {
    /// The hello deadline, armed by [`on_accept`](Self::on_accept).
    pub hello_deadline_ns: Option<u64>,
    /// The idle deadline, armed by [`on_idle_condition`](Self::on_idle_condition).
    pub idle_deadline_ns: Option<u64>,
    hello_ns: u64,
    idle_ns: u64,
}

impl ConnTimers {
    /// Timers with nothing armed: `hello_ns` is `hello_deadline_ms`, `idle_ns` is `idle_ms`
    /// of the level, in nanoseconds. A refused connection never calls `on_accept`, so it
    /// never has a deadline.
    #[must_use]
    pub fn new(hello_ns: u64, idle_ns: u64) -> ConnTimers {
        ConnTimers {
            hello_deadline_ns: None,
            idle_deadline_ns: None,
            hello_ns,
            idle_ns,
        }
    }

    /// The connection was accepted at `now_ns`: arm the hello deadline.
    pub fn on_accept(&mut self, now_ns: u64) {
        self.hello_deadline_ns = Some(now_ns.saturating_add(self.hello_ns));
    }

    /// The hello is complete (a valid `hello` REQ in binary mode, the first complete line
    /// in JSON mode): disarm the hello deadline.
    pub fn on_hello_complete(&mut self) {
        self.hello_deadline_ns = None;
    }

    /// A byte was received: disarm the idle deadline.
    pub fn on_byte(&mut self) {
        self.idle_deadline_ns = None;
    }

    /// A frame was enqueued for sending: disarm the idle deadline.
    pub fn on_enqueue(&mut self) {
        self.idle_deadline_ns = None;
    }

    /// Report whether the connection is idle now (`Ready`, no outstanding request, no live
    /// subscription, reader at `Header{0}`, queue empty). It arms the idle deadline at
    /// `now_ns + idle` when it turns true (a deadline already armed is kept) and disarms
    /// it when false.
    pub fn on_idle_condition(&mut self, idle: bool, now_ns: u64) {
        if !idle {
            self.idle_deadline_ns = None;
        } else if self.idle_deadline_ns.is_none() {
            self.idle_deadline_ns = Some(now_ns.saturating_add(self.idle_ns));
        }
    }

    /// The timer that has expired at `now_ns` (`now_ns` strictly after its deadline), the
    /// hello timer first.
    #[must_use]
    pub fn expired(&self, now_ns: u64) -> Option<Timer> {
        if passed(self.hello_deadline_ns, now_ns) {
            return Some(Timer::Hello);
        }
        if passed(self.idle_deadline_ns, now_ns) {
            return Some(Timer::Idle);
        }
        None
    }
}

/// An armed deadline that `now_ns` is strictly past.
fn passed(deadline: Option<u64>, now_ns: u64) -> bool {
    matches!(deadline, Some(at) if now_ns > at)
}

#[cfg(test)]
mod tests {
    use super::{ConnTimers, Detect, Feed, FrameReader, ModeDetect, RawFrame, Timer};
    use crate::error::ProtoError;
    use crate::frame::{FrameType, FRAME_HEADER_LEN};

    const MS: u64 = 1_000_000;
    const COMPLETION: u64 = 5_000 * MS;
    const MAX: u32 = 1024;

    /// A frame of `total` bytes (at least 12): header with `code`, flags 0, `corr`, then
    /// a deterministic payload.
    fn frame(code: u8, corr: u32, total: usize) -> Vec<u8> {
        let mut out = Vec::with_capacity(total);
        out.extend_from_slice(&u32::try_from(total).expect("fits u32").to_le_bytes());
        out.push(code);
        out.push(0);
        out.extend_from_slice(&[0, 0]);
        out.extend_from_slice(&corr.to_le_bytes());
        for index in 0..total - FRAME_HEADER_LEN {
            out.push(u8::try_from(index % 251).expect("below 251"));
        }
        out
    }

    fn reader() -> FrameReader {
        FrameReader::new(MAX, COMPLETION)
    }

    fn expect_frame(raw: &RawFrame, code: u8, corr: u32, total: usize) {
        assert_eq!(raw.header.frame_type.code(), code);
        assert_eq!(raw.header.corr, corr);
        assert_eq!(usize::try_from(raw.header.len).expect("len"), total);
        assert_eq!(raw.bytes.len(), total);
    }

    fn error_kind(feed: &Feed) -> Option<&'static str> {
        feed.error.as_ref().map(ProtoError::kind)
    }

    // ---- §5.2 row `Header{have}` / bytes ------------------------------------------------

    #[test]
    fn header_row_twelve_byte_frame_is_emitted_and_the_reader_returns_to_header_zero() {
        let mut reader = reader();
        let bytes = frame(1, 7, 12);
        let feed = reader.feed(&bytes, 0);
        assert_eq!(feed.error, None);
        assert_eq!(feed.frames.len(), 1);
        let first = feed.frames.first().expect("one frame");
        expect_frame(first, 1, 7, 12);
        assert_eq!(first.bytes, bytes);
        assert_eq!(reader.buffered_len(), 0);
        assert_eq!(reader.poll(u64::MAX), Ok(()));
    }

    #[test]
    fn header_row_validates_at_twelve_bytes_not_before() {
        let mut reader = reader();
        let bytes = frame(9, 1, 12);
        let early = reader.feed(bytes.get(..11).expect("11 bytes"), 0);
        assert_eq!(early.error, None);
        assert!(early.frames.is_empty());
        let last = reader.feed(bytes.get(11..).expect("last byte"), 1);
        assert_eq!(error_kind(&last), Some("unknown-frame-type"));
        assert!(last.frames.is_empty());
    }

    #[test]
    fn header_row_each_defect_poisons_with_its_own_kind() {
        let mut flags = frame(1, 1, 12);
        flags[5] = 0x02;
        let mut sectioned_req = frame(1, 1, 12);
        sectioned_req[5] = 0x01;
        let mut reserved = frame(1, 1, 12);
        reserved[6] = 1;
        let mut below = frame(1, 1, 12);
        below[0] = 11;
        let mut over = frame(1, 1, 12);
        over[..4].copy_from_slice(&(MAX + 1).to_le_bytes());
        let cases: Vec<(Vec<u8>, &str)> = vec![
            (frame(9, 1, 12), "unknown-frame-type"),
            (frame(0, 1, 12), "unknown-frame-type"),
            (flags, "unknown-frame-flags"),
            (sectioned_req, "unknown-frame-flags"),
            (reserved, "non-zero-reserved"),
            (below, "truncated"),
            (over, "oversized-frame"),
        ];
        for (bytes, kind) in cases {
            let mut reader = reader();
            let feed = reader.feed(&bytes, 0);
            assert_eq!(error_kind(&feed), Some(kind), "{kind}");
            assert!(feed.frames.is_empty(), "{kind}");
        }
    }

    #[test]
    fn header_row_fixed_fields_are_judged_before_the_length() {
        // Type 9 and a length above the maximum: the type is the first refusal, like the
        // codec's own `FrameHeader::parse` and the Python reference.
        let mut bytes = frame(9, 1, 12);
        bytes[..4].copy_from_slice(&(MAX + 1).to_le_bytes());
        let feed = reader().feed(&bytes, 0);
        assert_eq!(error_kind(&feed), Some("unknown-frame-type"));
    }

    #[test]
    fn header_row_a_length_above_the_reader_maximum_is_oversized_with_both_numbers() {
        let mut reader = FrameReader::new(100, COMPLETION);
        let over = frame(1, 1, 101);
        let feed = reader.feed(over.get(..12).expect("header"), 0);
        let error = feed.error.expect("oversized");
        assert_eq!(error.kind(), "oversized-frame");
        assert_eq!(
            error.to_string(),
            "frame of 101 bytes exceeds the 100-byte maximum"
        );
        let mut exact = FrameReader::new(100, COMPLETION);
        let bytes = frame(1, 1, 100);
        assert_eq!(exact.feed(&bytes, 0).frames.len(), 1);
    }

    #[test]
    fn header_row_started_is_stamped_at_the_first_byte() {
        let mut reader = reader();
        let bytes = frame(1, 1, 40);
        // First byte at t = 10 ms, the rest of the header at t = 4,000 ms.
        let _ = reader.feed(bytes.get(..1).expect("byte"), 10 * MS);
        let _ = reader.feed(bytes.get(1..5).expect("bytes"), 4_000 * MS);
        assert_eq!(reader.poll(5_010 * MS), Ok(()));
        assert_eq!(reader.poll(5_010 * MS + 1), Err(ProtoError::Deadline));
    }

    // ---- §5.2 row `Body` / bytes --------------------------------------------------------

    #[test]
    fn body_row_appends_up_to_len_and_emits_when_complete() {
        let mut reader = reader();
        let bytes = frame(2, 5, 100);
        let first = reader.feed(bytes.get(..50).expect("half"), 0);
        assert!(first.frames.is_empty());
        assert_eq!(reader.buffered_len(), 50);
        let second = reader.feed(bytes.get(50..).expect("rest"), 1);
        assert_eq!(second.error, None);
        let frame_out = second.frames.first().expect("frame");
        expect_frame(frame_out, 2, 5, 100);
        assert_eq!(frame_out.bytes, bytes);
        assert_eq!(reader.buffered_len(), 0);
    }

    #[test]
    fn body_row_surplus_bytes_start_the_next_frame_with_started_now() {
        let mut reader = reader();
        let first = frame(1, 1, 40);
        let second = frame(2, 2, 40);
        // Frame 1 starts at t = 0 and completes at t = 3,000 ms together with 3 bytes of
        // frame 2: frame 2 started at 3,000 ms, not at 0.
        let _ = reader.feed(first.get(..10).expect("start"), 0);
        let mut rest = first.get(10..).expect("rest").to_vec();
        rest.extend_from_slice(second.get(..3).expect("3 bytes"));
        let feed = reader.feed(&rest, 3_000 * MS);
        assert_eq!(feed.frames.len(), 1);
        assert_eq!(reader.buffered_len(), 3);
        assert_eq!(reader.poll(5_001 * MS), Ok(()));
        assert_eq!(reader.poll(8_000 * MS), Ok(()));
        assert_eq!(reader.poll(8_000 * MS + 1), Err(ProtoError::Deadline));
    }

    #[test]
    fn body_row_two_frames_in_one_feed_come_out_in_order() {
        let mut reader = reader();
        let mut stream = frame(1, 1, 30);
        stream.extend_from_slice(&frame(2, 2, 12));
        stream.extend_from_slice(&frame(6, 3, 77));
        let feed = reader.feed(&stream, 0);
        assert_eq!(feed.error, None);
        assert_eq!(feed.frames.len(), 3);
        expect_frame(&feed.frames[0], 1, 1, 30);
        expect_frame(&feed.frames[1], 2, 2, 12);
        expect_frame(&feed.frames[2], 6, 3, 77);
    }

    #[test]
    fn body_row_a_frame_completed_before_an_error_is_returned_with_it() {
        let mut reader = reader();
        let mut stream = frame(1, 1, 30);
        stream.extend_from_slice(&frame(9, 2, 12));
        stream.extend_from_slice(&frame(1, 3, 30));
        let feed = reader.feed(&stream, 0);
        assert_eq!(feed.frames.len(), 1);
        expect_frame(&feed.frames[0], 1, 1, 30);
        assert_eq!(error_kind(&feed), Some("unknown-frame-type"));
        // The reader is poisoned: later calls return the same error and no frame.
        let again = reader.feed(&frame(1, 4, 30), 1);
        assert!(again.frames.is_empty());
        assert_eq!(error_kind(&again), Some("unknown-frame-type"));
        let polled = reader.poll(2).expect_err("poisoned");
        let closed = reader.eof().expect_err("poisoned");
        assert_eq!(polled.kind(), "unknown-frame-type");
        assert_eq!(closed.kind(), "unknown-frame-type");
    }

    #[test]
    fn body_row_buffer_grows_with_received_bytes_never_with_len() {
        let big: u32 = 16 * 1024 * 1024;
        let mut reader = FrameReader::new(big, COMPLETION);
        let mut header = frame(1, 1, 12);
        header[..4].copy_from_slice(&big.to_le_bytes());
        assert!(reader.feed(&header, 0).frames.is_empty());
        assert!(reader.buffered_capacity() <= 12);
        let chunk = vec![0xAB_u8; 1000];
        for _ in 0..300 {
            let feed = reader.feed(&chunk, 0);
            assert_eq!(feed.error, None);
            let received = reader.buffered_len();
            let bound = received + 64 * 1024;
            assert!(
                reader.buffered_capacity() <= bound.min(16 * 1024 * 1024),
                "capacity {} for {received} received",
                reader.buffered_capacity()
            );
        }
        assert_eq!(reader.buffered_len(), 12 + 300 * 1000);
    }

    #[test]
    fn body_row_capacity_never_exceeds_len_for_a_small_frame() {
        let mut reader = reader();
        let bytes = frame(1, 1, 200);
        let _ = reader.feed(bytes.get(..13).expect("13 bytes"), 0);
        assert!(reader.buffered_capacity() <= 200);
    }

    // ---- §5.2 row `Header{have > 0}` or `Body` / poll -----------------------------------

    #[test]
    fn poll_row_partial_header_times_out_strictly_after_started_plus_completion() {
        let mut reader = reader();
        let _ = reader.feed(&[1, 2, 3], 7 * MS);
        let deadline = 7 * MS + COMPLETION;
        assert_eq!(reader.poll(deadline), Ok(()));
        assert_eq!(reader.poll(deadline + 1), Err(ProtoError::Deadline));
    }

    #[test]
    fn poll_row_partial_body_times_out_strictly_after_started_plus_completion() {
        let mut reader = reader();
        let bytes = frame(1, 1, 100);
        let _ = reader.feed(bytes.get(..60).expect("60 bytes"), 0);
        assert_eq!(reader.poll(COMPLETION), Ok(()));
        assert_eq!(reader.poll(COMPLETION + 1), Err(ProtoError::Deadline));
    }

    #[test]
    fn poll_row_a_deadline_poisons_the_reader() {
        let mut reader = reader();
        let _ = reader.feed(&[1], 0);
        assert_eq!(reader.poll(COMPLETION + 1), Err(ProtoError::Deadline));
        let feed = reader.feed(&frame(1, 1, 12), COMPLETION + 2);
        assert!(feed.frames.is_empty());
        assert_eq!(feed.error, Some(ProtoError::Deadline));
    }

    // ---- §5.2 row `Header{0}` / poll ----------------------------------------------------

    #[test]
    fn poll_row_header_zero_does_nothing_however_long_it_idles() {
        let mut reader = reader();
        assert_eq!(reader.poll(u64::MAX), Ok(()));
        let _ = reader.feed(&frame(1, 1, 12), 0);
        assert_eq!(reader.poll(u64::MAX), Ok(()));
    }

    #[test]
    fn poll_row_idle_time_at_a_frame_boundary_does_not_age_the_next_frame() {
        let mut reader = reader();
        let bytes = frame(1, 1, 40);
        assert_eq!(reader.poll(60_000 * MS), Ok(()));
        let _ = reader.feed(bytes.get(..1).expect("byte"), 60_000 * MS);
        assert_eq!(reader.poll(65_000 * MS), Ok(()));
        let feed = reader.feed(bytes.get(1..).expect("rest"), 65_000 * MS);
        assert_eq!(feed.frames.len(), 1);
    }

    // ---- §5.2 eof rows ------------------------------------------------------------------

    #[test]
    fn eof_row_header_zero_is_a_clean_close() {
        let mut reader = reader();
        let _ = reader.feed(&frame(1, 1, 30), 0);
        assert_eq!(reader.eof(), Ok(()));
        assert_eq!(reader.eof(), Ok(()));
        assert_eq!(reader.poll(u64::MAX), Ok(()));
    }

    #[test]
    fn eof_row_partial_header_is_truncated() {
        let mut reader = reader();
        let _ = reader.feed(&[1, 2, 3, 4, 5], 0);
        let error = reader.eof().expect_err("truncated");
        assert_eq!(error.kind(), "truncated");
        assert_eq!(
            error,
            ProtoError::Truncated {
                context: "frame header at end of stream",
                need: 12,
                have: 5,
            }
        );
    }

    #[test]
    fn eof_row_partial_body_is_truncated() {
        let mut reader = reader();
        let bytes = frame(1, 1, 100);
        let _ = reader.feed(bytes.get(..60).expect("60 bytes"), 0);
        assert_eq!(
            reader.eof(),
            Err(ProtoError::Truncated {
                context: "frame body at end of stream",
                need: 100,
                have: 60,
            })
        );
    }

    // ---- §5.2 rows `Poisoned(e)` and `Closed` -------------------------------------------

    #[test]
    fn poisoned_row_feed_poll_and_eof_return_the_same_error_and_no_frames() {
        let mut reader = reader();
        let first = reader.feed(&frame(9, 1, 12), 0);
        let expected = first.error.expect("poisoned");
        let again = reader.feed(&frame(1, 2, 30), 1);
        assert!(again.frames.is_empty());
        assert_eq!(again.error, Some(expected.clone()));
        assert_eq!(reader.feed(&[], 2).error, Some(expected.clone()));
        assert_eq!(reader.poll(u64::MAX), Err(expected.clone()));
        assert_eq!(reader.eof(), Err(expected));
    }

    #[test]
    fn closed_row_a_feed_after_a_clean_eof_is_trailing_bytes() {
        let mut reader = reader();
        assert_eq!(reader.eof(), Ok(()));
        let feed = reader.feed(&[1, 2, 3], 0);
        assert!(feed.frames.is_empty());
        assert_eq!(feed.error, Some(ProtoError::TrailingBytes { extra: 3 }));
        // And it is final.
        assert_eq!(
            reader.feed(&frame(1, 1, 12), 1).error,
            Some(ProtoError::TrailingBytes { extra: 3 })
        );
    }

    #[test]
    fn closed_row_an_empty_feed_after_eof_is_not_trailing_bytes() {
        let mut reader = reader();
        assert_eq!(reader.eof(), Ok(()));
        assert_eq!(reader.feed(&[], 0).error, None);
    }

    // ---- buffering ----------------------------------------------------------------------

    #[test]
    fn an_oversized_len_never_allocates_beyond_the_header_buffer() {
        let mut reader = reader();
        let mut header = frame(1, 1, 12);
        header[..4].copy_from_slice(&u32::MAX.to_le_bytes());
        let feed = reader.feed(&header, 0);
        assert_eq!(error_kind(&feed), Some("oversized-frame"));
        assert!(reader.buffered_capacity() <= FRAME_HEADER_LEN);
        let more = reader.feed(&vec![0_u8; 200_000], 1);
        assert_eq!(error_kind(&more), Some("oversized-frame"));
        assert!(reader.buffered_capacity() <= FRAME_HEADER_LEN);
        assert_eq!(reader.buffered_len(), 0);
    }

    #[test]
    fn a_len_above_the_codec_limit_names_the_readers_own_limit() {
        let mut reader = reader();
        let mut header = frame(1, 1, 12);
        header[..4].copy_from_slice(&u32::MAX.to_le_bytes());
        let error = reader.feed(&header, 0).error.expect("oversized");
        assert_eq!(error.kind(), "oversized-frame");
        assert_eq!(
            error.to_string(),
            "frame of 4294967295 bytes exceeds the 1024-byte maximum"
        );
    }

    #[test]
    fn capacity_is_back_at_the_header_size_after_a_completed_frame() {
        let mut reader = FrameReader::new(1 << 20, COMPLETION);
        let bytes = frame(1, 1, 5000);
        let feed = reader.feed(&bytes, 0);
        assert_eq!(feed.frames.len(), 1);
        assert_eq!(reader.buffered_len(), 0);
        assert_eq!(reader.buffered_capacity(), FRAME_HEADER_LEN);
    }

    #[test]
    fn growth_is_bounded_above_and_below_for_a_long_frame_fed_in_small_chunks() {
        let total = 200_000;
        let mut reader = FrameReader::new(1 << 20, COMPLETION);
        let bytes = frame(1, 1, total);
        let mut chunks = bytes.chunks(1000);
        let first = chunks.next().expect("first chunk");
        assert!(reader.feed(first, 0).frames.is_empty());
        // 12 header bytes were held; the 988 body bytes of the first chunk reserve exactly
        // received + 64 KiB.
        assert_eq!(reader.buffered_capacity(), 1000 + 64 * 1024);
        for chunk in chunks {
            let feed = reader.feed(chunk, 0);
            assert_eq!(feed.error, None);
            let received = reader.buffered_len();
            let capacity = reader.buffered_capacity();
            if feed.frames.is_empty() {
                assert!(capacity <= total.min(received + 64 * 1024), "{capacity}");
                assert!(capacity >= received, "{capacity} < {received}");
                assert!(capacity > 0);
            } else {
                assert_eq!(received, 0);
            }
        }
        assert_eq!(reader.buffered_len(), 0);
    }

    #[test]
    fn saturating_clock_arithmetic_never_wraps_at_the_end_of_time() {
        let mut reader = reader();
        let _ = reader.feed(&[1], u64::MAX - 1);
        // started + completion saturates at u64::MAX: nothing is strictly after it.
        assert_eq!(reader.poll(u64::MAX), Ok(()));
    }

    // ---- split invariance ---------------------------------------------------------------

    const SPEC: [(u8, u32, usize); 4] = [(1, 1, 30), (2, 2, 12), (5, 3, 61), (6, 4, 12)];

    fn stream_of_frames() -> Vec<u8> {
        let mut stream = Vec::new();
        for (code, corr, total) in SPEC {
            stream.extend_from_slice(&frame(code, corr, total));
        }
        stream
    }

    fn summary(raw: &RawFrame) -> (u8, u32, Vec<u8>) {
        let code = raw.header.frame_type.code();
        (code, raw.header.corr, raw.bytes.clone())
    }

    fn collect(frames: &[RawFrame]) -> Vec<(u8, u32, Vec<u8>)> {
        frames.iter().map(summary).collect()
    }

    #[test]
    fn split_at_every_offset_yields_the_same_frames() {
        let stream = stream_of_frames();
        let mut whole = reader();
        let reference = collect(&whole.feed(&stream, 0).frames);
        assert_eq!(reference.len(), SPEC.len());
        for cut in 0..=stream.len() {
            let mut reader = reader();
            let mut frames = Vec::new();
            for part in [&stream[..cut], &stream[cut..]] {
                let feed = reader.feed(part, 0);
                assert_eq!(feed.error, None, "cut {cut}");
                frames.extend(feed.frames);
            }
            assert_eq!(collect(&frames), reference, "cut {cut}");
            assert_eq!(reader.buffered_len(), 0, "cut {cut}");
        }
    }

    #[test]
    fn dribbled_one_byte_at_a_time_yields_the_same_frames() {
        let stream = stream_of_frames();
        let mut whole = reader();
        let reference = collect(&whole.feed(&stream, 0).frames);
        let mut reader = reader();
        let mut frames = Vec::new();
        for (index, byte) in stream.iter().enumerate() {
            let now = u64::try_from(index).expect("u64");
            let feed = reader.feed(std::slice::from_ref(byte), now);
            assert_eq!(feed.error, None);
            frames.extend(feed.frames);
        }
        assert_eq!(frames.len(), SPEC.len());
        assert_eq!(collect(&frames), reference);
    }

    #[test]
    fn a_frame_type_name_is_stable_for_the_fixture_vocabulary() {
        let mut reader = reader();
        let feed = reader.feed(&frame(5, 1, 12), 0);
        let first = feed.frames.first().expect("frame");
        assert_eq!(first.header.frame_type, FrameType::Block);
        assert_eq!(first.header.frame_type.name(), "block");
    }

    // ---- §5.3 mode detection ------------------------------------------------------------

    #[test]
    fn detect_row_open_brace_at_offset_zero_is_json_and_is_not_consumed() {
        let mut detect = ModeDetect::new();
        assert_eq!(detect.feed(b"{\"corr\":1}\n"), Detect::Json { at: 0 });
    }

    #[test]
    fn detect_row_a_brace_after_the_first_byte_is_not_json() {
        let mut detect = ModeDetect::new();
        assert_eq!(detect.feed(b"H{"), Detect::Unknown);
    }

    #[test]
    fn detect_row_an_empty_feed_needs_more() {
        let mut detect = ModeDetect::new();
        assert_eq!(detect.feed(b""), Detect::Need);
        assert_eq!(detect.feed(b"HO"), Detect::Need);
        assert_eq!(detect.feed(b""), Detect::Need);
    }

    #[test]
    fn detect_row_eight_prefix_bytes_are_binary_and_surplus_starts_at_at() {
        let mut detect = ModeDetect::new();
        let mut bytes = b"HOB\x01\x01\x00\x01\x00".to_vec();
        let tail = frame(1, 1, 12);
        bytes.extend_from_slice(&tail);
        let decided = detect.feed(&bytes);
        let Detect::Binary { prefix, at } = decided else {
            panic!("expected Binary, got {decided:?}");
        };
        assert_eq!(&prefix, b"HOB\x01\x01\x00\x01\x00");
        assert_eq!(at, 8);
        assert_eq!(bytes.get(at..), Some(tail.as_slice()));
    }

    #[test]
    fn detect_row_the_prefix_may_arrive_one_byte_at_a_time() {
        let mut detect = ModeDetect::new();
        let prefix = b"HOB\x01\x07\x00\x09\x00";
        for byte in prefix.iter().take(7) {
            assert_eq!(detect.feed(std::slice::from_ref(byte)), Detect::Need);
        }
        let decided = detect.feed(&[0x00, 0xAA]);
        let Detect::Binary { prefix: got, at } = decided else {
            panic!("expected Binary, got {decided:?}");
        };
        assert_eq!(&got, prefix);
        assert_eq!(at, 1);
    }

    #[test]
    fn detect_row_version_bytes_may_be_any_value() {
        let mut detect = ModeDetect::new();
        let mut bytes = b"HOB\x01".to_vec();
        bytes.extend_from_slice(&[b'{', 0xFF, 0x00, 0x7B]);
        assert!(matches!(detect.feed(&bytes), Detect::Binary { at: 8, .. }));
    }

    #[test]
    fn detect_row_a_diverging_byte_among_the_first_four_is_unknown() {
        for (bytes, label) in [
            (&b"X"[..], "first byte"),
            (&b"HX"[..], "second byte"),
            (&b"HOX"[..], "third byte"),
            (&b"HOB\x02"[..], "fourth byte"),
            (&b"\x00"[..], "nul"),
        ] {
            let mut detect = ModeDetect::new();
            assert_eq!(detect.feed(bytes), Detect::Unknown, "{label}");
        }
    }

    #[test]
    fn detect_row_unknown_is_decided_at_the_diverging_byte_across_feeds() {
        let mut detect = ModeDetect::new();
        assert_eq!(detect.feed(b"HO"), Detect::Need);
        assert_eq!(detect.feed(b"Bz"), Detect::Unknown);
    }

    #[test]
    fn detect_row_a_feed_after_a_decision_is_unknown_in_every_build() {
        let mut json = ModeDetect::new();
        assert_eq!(json.feed(b"{"), Detect::Json { at: 0 });
        assert_eq!(json.feed(b"{"), Detect::Unknown);
        let mut binary = ModeDetect::new();
        assert!(matches!(binary.feed(b"HOB\x01abcd"), Detect::Binary { .. }));
        assert_eq!(binary.feed(b"{"), Detect::Unknown);
        let mut unknown = ModeDetect::new();
        assert_eq!(unknown.feed(b"X"), Detect::Unknown);
        assert_eq!(unknown.feed(b"HOB\x01"), Detect::Unknown);
    }

    // ---- §5.4 ConnTimers rows -----------------------------------------------------------

    const HELLO_NS: u64 = 5_000 * MS;
    const IDLE_NS: u64 = 300_000 * MS;

    fn timers() -> ConnTimers {
        ConnTimers::new(HELLO_NS, IDLE_NS)
    }

    #[test]
    fn timers_row_refuse_never_arms_anything() {
        let timers = timers();
        assert_eq!(timers.hello_deadline_ns, None);
        assert_eq!(timers.idle_deadline_ns, None);
        assert_eq!(timers.expired(u64::MAX), None);
    }

    #[test]
    fn timers_row_accept_arms_the_hello_deadline_at_now_plus_hello() {
        let mut timers = timers();
        timers.on_accept(1_000 * MS);
        assert_eq!(timers.hello_deadline_ns, Some(6_000 * MS));
        assert_eq!(timers.idle_deadline_ns, None);
    }

    #[test]
    fn timers_row_hello_timer_fires_strictly_after_the_deadline() {
        let mut timers = timers();
        timers.on_accept(1_000 * MS);
        assert_eq!(timers.expired(6_000 * MS), None);
        assert_eq!(timers.expired(6_000 * MS + 1), Some(Timer::Hello));
    }

    #[test]
    fn timers_row_hello_complete_disarms_the_hello_timer() {
        let mut timers = timers();
        timers.on_accept(0);
        timers.on_hello_complete();
        assert_eq!(timers.hello_deadline_ns, None);
        assert_eq!(timers.expired(u64::MAX), None);
    }

    #[test]
    fn timers_row_idle_condition_arms_the_idle_deadline_when_it_turns_true() {
        let mut timers = timers();
        timers.on_accept(0);
        timers.on_hello_complete();
        timers.on_idle_condition(true, 10_000 * MS);
        assert_eq!(timers.idle_deadline_ns, Some(310_000 * MS));
        // Still true later: the armed deadline is kept, not pushed out.
        timers.on_idle_condition(true, 20_000 * MS);
        assert_eq!(timers.idle_deadline_ns, Some(310_000 * MS));
    }

    #[test]
    fn timers_row_idle_fires_strictly_after_the_deadline() {
        let mut timers = timers();
        timers.on_idle_condition(true, 0);
        assert_eq!(timers.expired(IDLE_NS), None);
        assert_eq!(timers.expired(IDLE_NS + 1), Some(Timer::Idle));
    }

    #[test]
    fn timers_row_a_received_byte_disarms_idle() {
        let mut timers = timers();
        timers.on_idle_condition(true, 0);
        timers.on_byte();
        assert_eq!(timers.idle_deadline_ns, None);
        assert_eq!(timers.expired(u64::MAX), None);
    }

    #[test]
    fn timers_row_an_enqueued_frame_disarms_idle() {
        let mut timers = timers();
        timers.on_idle_condition(true, 0);
        timers.on_enqueue();
        assert_eq!(timers.idle_deadline_ns, None);
        assert_eq!(timers.expired(u64::MAX), None);
    }

    #[test]
    fn timers_row_the_idle_condition_turning_false_disarms_idle() {
        let mut timers = timers();
        timers.on_idle_condition(true, 0);
        timers.on_idle_condition(false, 1);
        assert_eq!(timers.idle_deadline_ns, None);
        // Turning true again arms a fresh deadline from that moment.
        timers.on_idle_condition(true, 50 * MS);
        assert_eq!(timers.idle_deadline_ns, Some(50 * MS + IDLE_NS));
    }

    #[test]
    fn timers_row_the_hello_timer_is_reported_before_the_idle_timer() {
        let mut timers = timers();
        timers.on_accept(0);
        timers.on_idle_condition(true, 0);
        assert_eq!(timers.expired(u64::MAX), Some(Timer::Hello));
        timers.on_hello_complete();
        assert_eq!(timers.expired(u64::MAX), Some(Timer::Idle));
    }

    #[test]
    fn timers_row_a_deadline_past_the_clock_range_saturates_instead_of_wrapping() {
        let mut timers = timers();
        timers.on_accept(u64::MAX - 1);
        assert_eq!(timers.hello_deadline_ns, Some(u64::MAX));
        assert_eq!(timers.expired(u64::MAX), None);
    }

    #[test]
    fn timers_row_an_idle_deadline_past_the_clock_range_saturates_too() {
        let mut timers = timers();
        timers.on_idle_condition(true, u64::MAX - 1);
        assert_eq!(timers.idle_deadline_ns, Some(u64::MAX));
        assert_eq!(timers.expired(u64::MAX), None);
    }
}
