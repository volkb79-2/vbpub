//! The JSON-mode line reader (design R13; WIRE.md sections 5.2 and 8.1).
//!
//! [`LineReader`] turns a byte stream into request lines the way `proto::stream::FrameReader`
//! turns it into frames: pure, no clock (every time is an injected `now_ns`), no allocation from
//! a declared length (a line has none) and no panic on any bytes.
//!
//! # Contracts
//!
//! - **A line is at most `max_request_bytes` including its `\n`.** A complete line over the
//!   bound, or an incomplete tail that reaches the bound without a `\n`, is
//!   [`LineError::TooLarge`] (fatal `frame-too-large`).
//! - **The age runs from the first byte of an incomplete tail.** It is stamped when a line
//!   starts and is not complete in the same feed, also for surplus bytes after a complete line.
//!   Only [`LineReader::poll`] checks it, strictly (`now > started + completion`), so a line that
//!   completes late inside one feed is delivered, like the frame reader.
//! - **Linear time.** Every fed byte is scanned once: complete lines are cut straight out of the
//!   fed bytes and only the incomplete tail is buffered ([`LineReader::scanned`] counts the
//!   scanned bytes, so a test asserts `scanned == fed` instead of timing anything).
//! - **Bounded buffer.** The tail's capacity never exceeds `min(max_request_bytes, received +
//!   4 KiB)`, and a poisoned reader holds no buffer.
//! - **The reader does not interpret a line.** The returned line has its `\n` removed and
//!   nothing else: a `\r` stays (so a CRLF line reaches the envelope check with a trailing
//!   byte, `bad-request`), and an empty line is an empty line (`bad-request`, not fatal).

use crate::codes::Code;

/// How far past the received bytes the tail buffer may reserve.
const GROWTH_STEP: usize = 4096;

/// Why the line reader stopped.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LineError {
    /// A line (with its `\n`) is over `max_request_bytes`, or an incomplete tail reached it.
    TooLarge,
    /// An incomplete line is older than the request deadline.
    Deadline,
    /// The stream ended inside a line.
    Truncated,
    /// Bytes arrived after a clean end of stream.
    TrailingBytes,
}

impl LineError {
    /// The wire code of the reply the server owes, if any: `Truncated` and `TrailingBytes` mean
    /// the peer is gone, so the connection closes without a reply (design R10).
    #[must_use]
    pub const fn code(self) -> Option<Code> {
        match self {
            LineError::TooLarge => Some(Code::FrameTooLarge),
            LineError::Deadline => Some(Code::Deadline),
            LineError::Truncated | LineError::TrailingBytes => None,
        }
    }

    /// The stable local name.
    #[must_use]
    pub const fn kind(self) -> &'static str {
        match self {
            LineError::TooLarge => "line-too-large",
            LineError::Deadline => "deadline",
            LineError::Truncated => "truncated",
            LineError::TrailingBytes => "trailing-bytes",
        }
    }
}

/// The result of one [`LineReader::feed`]: the lines completed by the call (also those before an
/// error in the same call) and at most one error.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LineFeed {
    /// The completed lines in stream order, each without its `\n`.
    pub lines: Vec<Vec<u8>>,
    /// The error that poisoned the reader in this call or an earlier one.
    pub error: Option<LineError>,
}

fn feed_of(lines: Vec<Vec<u8>>, error: Option<LineError>) -> LineFeed {
    LineFeed { lines, error }
}

/// The JSON-mode request line reader.
#[derive(Debug)]
pub struct LineReader {
    max: usize,
    completion_ns: u64,
    tail: Vec<u8>,
    started_ns: u64,
    scanned: u64,
    poisoned: Option<LineError>,
    closed: bool,
}

impl LineReader {
    /// A reader that accepts lines of at most `max_request_bytes` (the `\n` included) and
    /// requires every line to complete within `completion_ns` of its first byte.
    #[must_use]
    pub fn new(max_request_bytes: u32, completion_ns: u64) -> LineReader {
        LineReader {
            max: usize::try_from(max_request_bytes).unwrap_or(usize::MAX),
            completion_ns,
            tail: Vec::new(),
            started_ns: 0,
            scanned: 0,
            poisoned: None,
            closed: false,
        }
    }

    /// Consume `bytes` received at `now_ns`. An empty feed is not an event. A feed after a clean
    /// [`eof`](Self::eof) is `TrailingBytes`; a poisoned reader returns its error and no lines.
    pub fn feed(&mut self, bytes: &[u8], now_ns: u64) -> LineFeed {
        let mut lines = Vec::new();
        if let Some(error) = self.poisoned {
            return feed_of(lines, Some(error));
        }
        if self.closed && !bytes.is_empty() {
            let error = self.poison(LineError::TrailingBytes);
            return feed_of(lines, Some(error));
        }
        let mut rest = bytes;
        while !rest.is_empty() {
            let newline = rest.iter().position(|&byte| byte == b'\n');
            let step = match newline {
                Some(at) => self.complete(rest, at, &mut lines),
                None => self.partial(rest, now_ns),
            };
            match step {
                Ok(used) => rest = rest.get(used..).unwrap_or_default(),
                Err(error) => {
                    let error = self.poison(error);
                    return feed_of(lines, Some(error));
                }
            }
        }
        feed_of(lines, None)
    }

    /// Check the age of an incomplete line at `now_ns`.
    ///
    /// # Errors
    ///
    /// [`LineError::Deadline`] when a line is partly received and `now_ns > started +
    /// completion` (the reader is then poisoned), or the poisoning error again. With no partial
    /// line there is nothing to time.
    pub fn poll(&mut self, now_ns: u64) -> Result<(), LineError> {
        if let Some(error) = self.poisoned {
            return Err(error);
        }
        let limit = self.started_ns.saturating_add(self.completion_ns);
        if !self.tail.is_empty() && now_ns > limit {
            return Err(self.poison(LineError::Deadline));
        }
        Ok(())
    }

    /// The peer ended the stream.
    ///
    /// # Errors
    ///
    /// [`LineError::Truncated`] when a line is partly received (the reader is then poisoned), or
    /// the poisoning error again. Otherwise the reader becomes closed; a second `eof` stays `Ok`.
    pub fn eof(&mut self) -> Result<(), LineError> {
        if let Some(error) = self.poisoned {
            return Err(error);
        }
        if self.tail.is_empty() {
            self.closed = true;
            return Ok(());
        }
        Err(self.poison(LineError::Truncated))
    }

    /// The bytes of the incomplete line held now.
    #[must_use]
    pub fn buffered_len(&self) -> usize {
        self.tail.len()
    }

    /// The capacity of the tail buffer; 0 once poisoned.
    #[must_use]
    pub fn buffered_capacity(&self) -> usize {
        self.tail.capacity()
    }

    /// How many bytes the reader has scanned for a `\n` since it was created: at most the bytes
    /// fed, each counted once (the linear-time evidence of the tests).
    #[must_use]
    pub fn scanned(&self) -> u64 {
        self.scanned
    }

    fn poison(&mut self, error: LineError) -> LineError {
        self.tail = Vec::new();
        self.poisoned = Some(error);
        error
    }

    fn scan(&mut self, count: usize) {
        let count = u64::try_from(count).unwrap_or(u64::MAX);
        self.scanned = self.scanned.saturating_add(count);
    }

    /// A `\n` at `at` of `rest`: the line is the tail plus `rest[..at]`.
    fn complete(
        &mut self,
        rest: &[u8],
        at: usize,
        lines: &mut Vec<Vec<u8>>,
    ) -> Result<usize, LineError> {
        self.scan(at.saturating_add(1));
        let head = rest.get(..at).unwrap_or_default();
        let body = self.tail.len().saturating_add(at);
        if body.saturating_add(1) > self.max {
            return Err(LineError::TooLarge);
        }
        let mut line = Vec::with_capacity(body);
        line.extend_from_slice(&self.tail);
        line.extend_from_slice(head);
        self.tail = Vec::new();
        lines.push(line);
        Ok(at.saturating_add(1))
    }

    /// No `\n` in `rest`: it joins the incomplete tail.
    fn partial(&mut self, rest: &[u8], now_ns: u64) -> Result<usize, LineError> {
        self.scan(rest.len());
        if self.tail.is_empty() {
            self.started_ns = now_ns;
        }
        let target = self.tail.len().saturating_add(rest.len());
        if target >= self.max {
            return Err(LineError::TooLarge);
        }
        if self.tail.capacity() < target {
            let want = self.max.min(target.saturating_add(GROWTH_STEP));
            let extra = want.saturating_sub(self.tail.len());
            self.tail.reserve_exact(extra);
        }
        self.tail.extend_from_slice(rest);
        Ok(rest.len())
    }
}

#[cfg(test)]
mod tests {
    use super::{LineError, LineFeed, LineReader};
    use crate::codes::Code;

    const MS: u64 = 1_000_000;
    const COMPLETION: u64 = 5_000 * MS;
    const MAX: u32 = 64;

    fn reader() -> LineReader {
        LineReader::new(MAX, COMPLETION)
    }

    fn texts(feed: &LineFeed) -> Vec<String> {
        let lossy = |line: &Vec<u8>| String::from_utf8_lossy(line).into_owned();
        feed.lines.iter().map(lossy).collect()
    }

    // ---- rows: bytes --------------------------------------------------------------------

    #[test]
    fn row_one_complete_line_is_returned_without_its_newline() {
        let mut reader = reader();
        let feed = reader.feed(b"{\"a\":1}\n", 0);
        assert_eq!(feed.error, None);
        assert_eq!(texts(&feed), ["{\"a\":1}"]);
        assert_eq!(reader.buffered_len(), 0);
        assert_eq!(reader.poll(u64::MAX), Ok(()));
    }

    #[test]
    fn row_several_lines_in_one_feed_keep_their_order() {
        let mut reader = reader();
        let feed = reader.feed(b"a\nbb\nccc\ndd", 0);
        assert_eq!(texts(&feed), ["a", "bb", "ccc"]);
        assert_eq!(reader.buffered_len(), 2);
        let rest = reader.feed(b"d\n", 1);
        assert_eq!(texts(&rest), ["ddd"]);
    }

    #[test]
    fn row_a_line_split_over_feeds_is_assembled() {
        let mut reader = reader();
        assert!(reader.feed(b"{\"verb\"", 0).lines.is_empty());
        assert!(reader.feed(b":\"status\"", 1).lines.is_empty());
        let feed = reader.feed(b"}\n", 2);
        assert_eq!(texts(&feed), ["{\"verb\":\"status\"}"]);
        let newline_first = reader.feed(b"x", 3);
        assert!(newline_first.lines.is_empty());
        assert_eq!(texts(&reader.feed(b"\n", 4)), ["x"]);
    }

    #[test]
    fn row_crlf_keeps_the_carriage_return_for_the_envelope_check() {
        let mut reader = reader();
        let feed = reader.feed(b"{}\r\n", 0);
        assert_eq!(feed.lines, [b"{}\r".to_vec()]);
        assert_eq!(feed.error, None);
    }

    #[test]
    fn row_an_empty_line_is_an_empty_line() {
        let mut reader = reader();
        let feed = reader.feed(b"\n\nx\n", 0);
        assert_eq!(feed.lines, [Vec::new(), Vec::new(), b"x".to_vec()]);
        assert_eq!(feed.error, None);
    }

    #[test]
    fn row_bytes_are_not_interpreted() {
        let mut reader = reader();
        let feed = reader.feed(&[0xFF, 0x00, 0x80, b'\n'], 0);
        assert_eq!(feed.lines, [vec![0xFF, 0x00, 0x80]]);
    }

    // ---- rows: the size bound -----------------------------------------------------------

    #[test]
    fn row_a_line_of_exactly_the_maximum_including_its_newline_is_accepted() {
        let mut reader = reader();
        let mut bytes = vec![b'a'; 63];
        bytes.push(b'\n');
        assert_eq!(bytes.len(), 64);
        let feed = reader.feed(&bytes, 0);
        assert_eq!(feed.error, None);
        assert_eq!(feed.lines.len(), 1);
        assert_eq!(feed.lines.first().map(Vec::len), Some(63));
    }

    #[test]
    fn row_a_complete_line_one_byte_over_is_too_large() {
        let mut reader = reader();
        let mut bytes = vec![b'a'; 64];
        bytes.push(b'\n');
        let feed = reader.feed(&bytes, 0);
        assert_eq!(feed.error, Some(LineError::TooLarge));
        assert!(feed.lines.is_empty());
        assert_eq!(reader.buffered_capacity(), 0);
    }

    #[test]
    fn row_an_incomplete_tail_reaching_the_maximum_is_too_large() {
        let mut reader = reader();
        let first = reader.feed(&[b'a'; 63], 0);
        assert_eq!(first.error, None);
        let second = reader.feed(b"a", 1);
        assert_eq!(second.error, Some(LineError::TooLarge));
        assert_eq!(reader.buffered_capacity(), 0);
        let mut other = LineReader::new(MAX, COMPLETION);
        let direct = other.feed(&[b'a'; 64], 0);
        assert_eq!(direct.error, Some(LineError::TooLarge));
    }

    #[test]
    fn row_the_tail_plus_the_fed_line_counts_against_the_bound() {
        let mut reader = reader();
        assert_eq!(reader.feed(&[b'a'; 40], 0).error, None);
        let mut rest = vec![b'b'; 24];
        rest.push(b'\n');
        assert_eq!(reader.feed(&rest, 1).error, Some(LineError::TooLarge));
        let mut fits = LineReader::new(MAX, COMPLETION);
        assert_eq!(fits.feed(&[b'a'; 40], 0).error, None);
        let mut rest = vec![b'b'; 23];
        rest.push(b'\n');
        let feed = fits.feed(&rest, 1);
        assert_eq!(feed.error, None);
        assert_eq!(feed.lines.first().map(Vec::len), Some(63));
    }

    #[test]
    fn row_lines_before_an_oversized_one_are_not_lost() {
        let mut reader = reader();
        let mut bytes = b"ok\n".to_vec();
        bytes.extend_from_slice(&[b'a'; 70]);
        bytes.push(b'\n');
        let feed = reader.feed(&bytes, 0);
        assert_eq!(texts(&feed), ["ok"]);
        assert_eq!(feed.error, Some(LineError::TooLarge));
    }

    #[test]
    fn the_error_maps_to_the_wire_code_or_to_a_silent_close() {
        assert_eq!(LineError::TooLarge.code(), Some(Code::FrameTooLarge));
        assert_eq!(LineError::Deadline.code(), Some(Code::Deadline));
        assert_eq!(LineError::Truncated.code(), None);
        assert_eq!(LineError::TrailingBytes.code(), None);
        let kinds = [
            LineError::TooLarge,
            LineError::Deadline,
            LineError::Truncated,
            LineError::TrailingBytes,
        ];
        let names: Vec<&str> = kinds.iter().map(|error| error.kind()).collect();
        assert_eq!(names.len(), 4);
        for (index, name) in names.iter().enumerate() {
            assert!(!names[..index].contains(name));
        }
    }

    // ---- rows: the age ------------------------------------------------------------------

    #[test]
    fn age_row_started_is_stamped_at_the_first_byte_of_the_tail() {
        let mut reader = reader();
        let _ = reader.feed(b"{", 10 * MS);
        let _ = reader.feed(b"\"a\"", 4_000 * MS);
        assert_eq!(reader.poll(5_010 * MS), Ok(()));
        assert_eq!(reader.poll(5_010 * MS + 1), Err(LineError::Deadline));
        assert_eq!(reader.poll(0), Err(LineError::Deadline));
    }

    #[test]
    fn age_row_surplus_bytes_start_the_next_line_with_started_now() {
        let mut reader = reader();
        let _ = reader.feed(b"abc", 0);
        let feed = reader.feed(b"d\nxy", 3_000 * MS);
        assert_eq!(texts(&feed), ["abcd"]);
        assert_eq!(reader.poll(5_000 * MS), Ok(()));
        assert_eq!(reader.poll(8_000 * MS), Ok(()));
        assert_eq!(reader.poll(8_000 * MS + 1), Err(LineError::Deadline));
    }

    #[test]
    fn age_row_feed_never_checks_the_age_so_a_late_line_is_delivered() {
        let mut reader = reader();
        let _ = reader.feed(b"abc", 0);
        let feed = reader.feed(b"\n", 60_000 * MS);
        assert_eq!(texts(&feed), ["abc"]);
        assert_eq!(feed.error, None);
        assert_eq!(reader.poll(u64::MAX), Ok(()));
    }

    #[test]
    fn age_row_a_boundary_has_nothing_to_time() {
        let mut reader = reader();
        assert_eq!(reader.poll(u64::MAX), Ok(()));
        let _ = reader.feed(b"a\n", 0);
        assert_eq!(reader.poll(u64::MAX), Ok(()));
        let _ = reader.feed(b"", 1);
        assert_eq!(reader.poll(u64::MAX), Ok(()));
    }

    // ---- rows: end of stream, poisoning --------------------------------------------------

    #[test]
    fn eof_row_at_a_boundary_is_clean_and_repeatable() {
        let mut reader = reader();
        let _ = reader.feed(b"a\n", 0);
        assert_eq!(reader.eof(), Ok(()));
        assert_eq!(reader.eof(), Ok(()));
        assert_eq!(reader.feed(b"", 1).error, None);
    }

    #[test]
    fn eof_row_inside_a_line_is_truncated_and_final() {
        let mut reader = reader();
        let _ = reader.feed(b"abc", 0);
        assert_eq!(reader.eof(), Err(LineError::Truncated));
        assert_eq!(reader.eof(), Err(LineError::Truncated));
        assert_eq!(reader.poll(0), Err(LineError::Truncated));
        let feed = reader.feed(b"x\n", 1);
        assert_eq!(feed.error, Some(LineError::Truncated));
        assert!(feed.lines.is_empty());
        assert_eq!(reader.buffered_capacity(), 0);
    }

    #[test]
    fn closed_row_a_feed_after_a_clean_eof_is_trailing_bytes() {
        let mut reader = reader();
        assert_eq!(reader.eof(), Ok(()));
        let feed = reader.feed(b"x", 0);
        assert_eq!(feed.error, Some(LineError::TrailingBytes));
        assert!(feed.lines.is_empty());
        assert_eq!(reader.eof(), Err(LineError::TrailingBytes));
    }

    #[test]
    fn poisoned_row_every_call_repeats_the_error() {
        let mut reader = reader();
        let _ = reader.feed(b"abc", 0);
        assert_eq!(reader.poll(6_000 * MS), Err(LineError::Deadline));
        for _ in 0..3 {
            let feed = reader.feed(b"ok\n", 7_000 * MS);
            assert_eq!(feed.error, Some(LineError::Deadline));
            assert_eq!(reader.poll(0), Err(LineError::Deadline));
            assert_eq!(reader.eof(), Err(LineError::Deadline));
        }
        assert_eq!(reader.buffered_len(), 0);
        assert_eq!(reader.buffered_capacity(), 0);
    }

    // ---- linear time and bounded buffers --------------------------------------------------

    #[test]
    fn ten_thousand_lines_in_one_feed_scan_each_byte_once() {
        let mut reader = LineReader::new(1024, COMPLETION);
        let mut bytes = Vec::new();
        for index in 0..10_000u32 {
            bytes.extend_from_slice(format!("{{\"n\":{index}}}\n").as_bytes());
        }
        let feed = reader.feed(&bytes, 0);
        assert_eq!(feed.error, None);
        assert_eq!(feed.lines.len(), 10_000);
        assert_eq!(reader.scanned(), u64::try_from(bytes.len()).expect("len"));
        assert_eq!(reader.buffered_len(), 0);
        assert_eq!(reader.buffered_capacity(), 0);
    }

    #[test]
    fn one_byte_dribbles_scan_each_byte_once_and_keep_the_capacity_bounded() {
        let max = 8192u32;
        let mut reader = LineReader::new(max, COMPLETION);
        let line = format!("{{\"pad\":\"{}\"}}\n", "x".repeat(5000));
        let mut total = 0usize;
        let mut got = 0usize;
        for _ in 0..3 {
            for (index, byte) in line.bytes().enumerate() {
                let feed = reader.feed(&[byte], 0);
                assert_eq!(feed.error, None);
                got += feed.lines.len();
                total += 1;
                let received = index + 1;
                let bound = usize::try_from(max).expect("max").min(received + 4096);
                assert!(reader.buffered_capacity() <= bound, "{received}");
            }
        }
        assert_eq!(got, 3);
        assert_eq!(reader.scanned(), u64::try_from(total).expect("total"));
    }

    #[test]
    fn a_huge_max_never_allocates_beyond_received_plus_the_step() {
        let mut reader = LineReader::new(u32::MAX, COMPLETION);
        let _ = reader.feed(&[b'a'; 10], 0);
        assert!(reader.buffered_capacity() <= 10 + 4096);
        let _ = reader.feed(&[b'a'; 5000], 1);
        assert!(reader.buffered_capacity() <= 5010 + 4096);
        assert_eq!(reader.buffered_len(), 5010);
    }

    #[test]
    fn a_zero_maximum_refuses_everything() {
        let mut reader = LineReader::new(0, COMPLETION);
        assert_eq!(reader.feed(b"\n", 0).error, Some(LineError::TooLarge));
        let mut other = LineReader::new(0, COMPLETION);
        assert_eq!(other.feed(b"a", 0).error, Some(LineError::TooLarge));
    }

    #[test]
    fn splitting_the_stream_anywhere_yields_the_same_lines() {
        let stream = b"{\"a\":1}\n\n{\"b\":2}\r\nxyz\n".to_vec();
        let mut whole = reader();
        let expect = whole.feed(&stream, 0).lines;
        assert_eq!(expect.len(), 4);
        for cut in 0..=stream.len() {
            let mut split = reader();
            let (head, tail) = stream.split_at(cut);
            let mut lines = split.feed(head, 0).lines;
            lines.extend(split.feed(tail, 1).lines);
            assert_eq!(lines, expect, "cut {cut}");
        }
    }
}
