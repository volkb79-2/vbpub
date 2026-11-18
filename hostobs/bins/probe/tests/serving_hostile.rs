//! Deterministic hostile runs of the pure serving functions of P5b-1a (design section 4, the
//! style of the client's 2,000 + 2,000 runs): `classify`, the argument reader, the JSON
//! `LineReader` and the encoders, over random and mutated inputs with a fixed seed.
//!
//! The closed outcome set: a request is dispatched or refused with one of the seven codes
//! `classify` can produce, the refusal is fatal exactly for the nine fatal codes, a `detail`
//! key is bounded and made of schema characters, every encoder output parses back with the
//! protocol crate's own checkers, and the line reader never holds more than its bound.

use hostobs_probe::args;
use hostobs_probe::classify::{classify, Call, Dispatch, Mode, Phase, Prefix, Query, Refusal};
use hostobs_probe::codes::{Code, Detail};
use hostobs_probe::encode::{closing_evt, error_line, rep_error, rep_ok, rep_ok_line};
use hostobs_probe::limits::Level;
use hostobs_probe::lines::{LineError, LineReader};
use hostobs_proto::envelope::{check_evt, check_rep};
use hostobs_proto::frame::FrameType;

const RUNS: usize = 2_000;
const MS: u64 = 1_000_000;
const LINE_MAX: u32 = 64;
const HELLO_ARGS: &str = "{\"client\":\"c\",\"proto\":{\"major\":1,\"minor\":1}}";
const VERBS: [&str; 5] = ["hello", "status", "current", "events", "annotate"];

/// The codes `classify` may refuse with.
const REFUSALS: [Code; 7] = [
    Code::BadFrame,
    Code::HelloRequired,
    Code::UnknownVerb,
    Code::Forbidden,
    Code::BadRequest,
    Code::BinaryRequired,
    Code::UnsupportedVersion,
];

const FATAL: [Code; 9] = [
    Code::UnknownMode,
    Code::UnsupportedVersion,
    Code::HelloRequired,
    Code::BadFrame,
    Code::FrameTooLarge,
    Code::Deadline,
    Code::Idle,
    Code::SlowClient,
    Code::ShuttingDown,
];

const ALPHABET: &[u8] = b"{}[]\":,\\ 0123456789-+.eEtrufalsnvbhcpgmoi\n\r\t";

const TOKENS: [&str; 18] = [
    "{",
    "}",
    "\"verb\":",
    "\"hello\"",
    "\"status\"",
    "\"args\":",
    "\"client\":",
    "\"proto\":",
    "\"major\":",
    "\"minor\":",
    "\"corr\":",
    "1",
    "0",
    ",",
    "\"x\"",
    "[1,2]",
    "null",
    "true",
];

/// The xorshift64 generator of the hostile tests (fixed seed, no dependency).
struct Xorshift(u64);

impl Xorshift {
    fn step(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        self.0 = x;
        x
    }

    fn below(&mut self, bound: usize) -> usize {
        let bound = u64::try_from(bound).expect("small");
        let value = self.step() % bound;
        usize::try_from(value).expect("below bound")
    }

    fn byte(&mut self) -> u8 {
        u8::try_from(self.below(256)).expect("byte")
    }
}

/// Valid and valid-but-refused requests of both modes: the mutation seeds.
fn seeds() -> Vec<(Mode, String)> {
    let mut out = Vec::new();
    for verb in VERBS {
        let args = if verb == "hello" { HELLO_ARGS } else { "{}" };
        let binary = format!("{{\"verb\":\"{verb}\",\"args\":{args}}}");
        let line = format!("{{\"verb\":\"{verb}\",\"args\":{args},\"corr\":7}}");
        out.push((Mode::Binary, binary));
        out.push((Mode::Json, line));
    }
    out
}

/// Random bytes: structured characters, a token soup or any byte.
fn random_payload(rng: &mut Xorshift) -> Vec<u8> {
    let style = rng.below(3);
    if style == 2 {
        let mut text = String::new();
        for _ in 0..=rng.below(12) {
            text.push_str(TOKENS[rng.below(TOKENS.len())]);
        }
        return text.into_bytes();
    }
    let mut out = Vec::new();
    for _ in 0..rng.below(97) {
        if style == 0 {
            out.push(ALPHABET[rng.below(ALPHABET.len())]);
        } else {
            out.push(rng.byte());
        }
    }
    out
}

/// One to four byte-level damages of `seed`: flip, truncate, insert, delete, replace, repeat.
fn mutate(rng: &mut Xorshift, seed: &[u8]) -> Vec<u8> {
    let mut out = seed.to_vec();
    for _ in 0..=rng.below(4) {
        let at = rng.below(out.len().max(1));
        match rng.below(6) {
            0 => {
                let flip = u8::try_from(1 + rng.below(255)).expect("byte");
                if let Some(byte) = out.get_mut(at) {
                    *byte ^= flip;
                }
            }
            1 => out.truncate(at),
            2 => {
                let byte = rng.byte();
                out.insert(at, byte);
            }
            3 => {
                if at < out.len() {
                    out.remove(at);
                }
            }
            4 => {
                let byte = ALPHABET[rng.below(ALPHABET.len())];
                if let Some(slot) = out.get_mut(at) {
                    *slot = byte;
                }
            }
            _ => {
                let copy = out.clone();
                out.extend_from_slice(copy.get(..at).unwrap_or_default());
            }
        }
    }
    out
}

/// A connection context derived from the run index alone (so every run is replayable).
fn query_for(index: usize, payload: &[u8]) -> Query<'_> {
    let mode = if index & 1 == 0 {
        Mode::Binary
    } else {
        Mode::Json
    };
    let phase = if index & 2 == 0 {
        Phase::Ready
    } else {
        Phase::BeforeHello
    };
    let frame_type = if index & 15 == 15 {
        FrameType::Rep
    } else {
        FrameType::Req
    };
    let corr = if phase == Phase::BeforeHello && index & 4 == 0 {
        1
    } else {
        u32::try_from(index % 5).expect("small")
    };
    Query {
        frame_type,
        corr,
        mode,
        phase,
        level: Level::ALL[(index >> 3) & 3],
        prefix: Prefix {
            major: 1,
            minor: u16::try_from(index % 3).expect("small"),
        },
        payload,
    }
}

fn frame_parts(bytes: &[u8]) -> (u8, u32, &[u8]) {
    let len = u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]);
    assert_eq!(usize::try_from(len).expect("len"), bytes.len());
    assert_eq!(bytes[5..8], [0, 0, 0]);
    let corr = u32::from_le_bytes([bytes[8], bytes[9], bytes[10], bytes[11]]);
    (bytes[4], corr, &bytes[12..])
}

fn key_is_schema_text(path: &str) -> bool {
    let plain = |c: char| c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | '-');
    path.len() <= Detail::MAX_KEY_BYTES && path.chars().all(plain)
}

/// Every error encoder output re-parses and names the code.
fn check_error_encodings(code: Code, corr: u32, detail: &Detail) {
    let frame = rep_error(corr, code, detail);
    let (kind, frame_corr, payload) = frame_parts(&frame);
    assert_eq!((kind, frame_corr), (FrameType::Rep.code(), corr));
    assert!(payload.len() <= 2048);
    let rep = check_rep(payload, false).expect("binary error envelope");
    assert!(!rep.ok);
    let body = std::str::from_utf8(&payload[rep.body]).expect("utf8");
    assert!(body.contains(&format!("\"code\":\"{}\"", code.name())));
    assert!(body.contains("\"detail\":{"));

    let line = error_line(corr, code, detail);
    assert_eq!(line.last(), Some(&b'\n'));
    let text = &line[..line.len() - 1];
    assert!(!text.contains(&b'\n'));
    let rep = check_rep(text, true).expect("json error line");
    assert!(!rep.ok);
    assert_eq!(rep.corr, Some(corr));

    if code.fatal() {
        let frame = closing_evt(code);
        let (kind, frame_corr, payload) = frame_parts(&frame);
        assert_eq!((kind, frame_corr), (FrameType::Evt.code(), 0));
        check_evt(payload).expect("closing event");
    }
}

fn check_refusal(query: &Query<'_>, refusal: &Refusal) {
    assert!(REFUSALS.contains(&refusal.code), "{refusal:?}");
    assert_eq!(refusal.fatal(), FATAL.contains(&refusal.code));
    if query.mode == Mode::Binary {
        assert!(refusal.corr == 0 || refusal.corr == query.corr);
    }
    if refusal.fatal() && refusal.code != Code::UnsupportedVersion {
        assert!(refusal.corr == 0 || refusal.code == Code::HelloRequired);
    }
    match &refusal.detail {
        Detail::Empty => assert_ne!(refusal.code, Code::BadRequest),
        Detail::Key(path) => {
            assert_eq!(refusal.code, Code::BadRequest);
            assert!(key_is_schema_text(path), "{path:?}");
        }
    }
    check_error_encodings(refusal.code, refusal.corr, &refusal.detail);
}

fn check_dispatch(query: &Query<'_>, done: &Dispatch) {
    assert_ne!(done.corr, 0);
    if query.mode == Mode::Binary {
        assert_eq!(done.corr, query.corr);
    }
    if let Call::Hello(hello) = &done.call {
        assert!(hello.client.len() <= 64);
        let low = |c: char| u32::from(c) < 0x20 || u32::from(c) == 0x7F;
        assert!(!hello.client.chars().any(low));
    }
    let frame = rep_ok(done.corr, "{}", 1024);
    let (kind, corr, payload) = frame_parts(&frame);
    assert_eq!((kind, corr), (FrameType::Rep.code(), done.corr));
    assert!(check_rep(payload, false).expect("ok reply").ok);
    let line = rep_ok_line(done.corr, "{}", 1024);
    let rep = check_rep(&line[..line.len() - 1], true).expect("ok line");
    assert!(rep.ok);
    assert_eq!(rep.corr, Some(done.corr));
}

fn check_outcome(query: &Query<'_>) {
    match classify(query) {
        Ok(done) => check_dispatch(query, &done),
        Err(refusal) => check_refusal(query, &refusal),
    }
}

/// The argument readers alone: never a panic, a bounded closed result.
fn check_args(bytes: &[u8]) {
    match args::hello(bytes) {
        Ok(hello) => assert!(hello.client.len() <= 64),
        Err(error) => assert!(key_is_schema_text(&error.key), "{:?}", error.key),
    }
    if let Err(error) = args::status(bytes) {
        assert!(key_is_schema_text(&error.key), "{:?}", error.key);
    }
}

#[test]
fn the_fatal_set_is_exactly_the_nine_codes() {
    let fatal: Vec<Code> = Code::ALL.into_iter().filter(|c| c.fatal()).collect();
    assert_eq!(fatal, FATAL);
    assert!(REFUSALS.iter().all(|code| Code::ALL.contains(code)));
}

#[test]
fn the_seeds_themselves_are_in_the_closed_set() {
    for (index, (mode, text)) in seeds().iter().enumerate() {
        for context in 0..64 {
            let query = Query {
                mode: *mode,
                ..query_for(context + index, text.as_bytes())
            };
            check_outcome(&query);
        }
    }
}

#[test]
fn a_valid_hello_and_status_dispatch_in_their_contexts() {
    let hello = format!("{{\"verb\":\"hello\",\"args\":{HELLO_ARGS}}}");
    let query = Query {
        frame_type: FrameType::Req,
        corr: 1,
        mode: Mode::Binary,
        phase: Phase::BeforeHello,
        level: Level::Observe,
        prefix: Prefix { major: 1, minor: 1 },
        payload: hello.as_bytes(),
    };
    let done = classify(&query).expect("hello");
    assert_eq!(done.corr, 1);
    let status = "{\"verb\":\"status\",\"args\":{},\"corr\":7}";
    let query = Query {
        mode: Mode::Json,
        phase: Phase::Ready,
        payload: status.as_bytes(),
        ..query
    };
    let done = classify(&query).expect("status");
    assert_eq!((done.corr, done.call), (7, Call::Status));
}

#[test]
fn two_thousand_random_inputs_stay_in_the_closed_set() {
    let mut rng = Xorshift(0x9E37_79B9_7F4A_7C15);
    for index in 0..RUNS {
        let payload = random_payload(&mut rng);
        check_outcome(&query_for(index, &payload));
        check_args(&payload);
    }
}

#[test]
fn two_thousand_mutated_valid_inputs_stay_in_the_closed_set() {
    let mut rng = Xorshift(0xD1B5_4A32_D192_ED03);
    let seeds = seeds();
    for index in 0..RUNS {
        let (mode, text) = &seeds[rng.below(seeds.len())];
        let payload = mutate(&mut rng, text.as_bytes());
        let query = Query {
            mode: *mode,
            ..query_for(index, &payload)
        };
        check_outcome(&query);
        check_args(&payload);
    }
}

#[test]
fn every_code_and_detail_encodes_and_parses_back() {
    let details = [
        Detail::Empty,
        Detail::Key(String::new()),
        Detail::Key("proto.minor".to_string()),
    ];
    for code in Code::ALL {
        for detail in &details {
            check_error_encodings(code, 9, detail);
            check_error_encodings(code, 0, detail);
        }
    }
}

#[test]
fn an_oversize_reply_becomes_an_internal_error_that_parses() {
    let big = format!("{{\"pad\":\"{}\"}}", "x".repeat(2000));
    let frame = rep_ok(3, &big, 1024);
    let (_, corr, payload) = frame_parts(&frame);
    assert_eq!(corr, 3);
    let rep = check_rep(payload, false).expect("error reply");
    assert!(!rep.ok);
    let body = std::str::from_utf8(&payload[rep.body]).expect("utf8");
    assert!(body.contains("\"code\":\"internal\""));
    let line = rep_ok_line(3, &big, 1024);
    let rep = check_rep(&line[..line.len() - 1], true).expect("error line");
    assert!(!rep.ok);
}

/// A stream of lines, long lines and noise.
fn stream(rng: &mut Xorshift, seeds: &[(Mode, String)]) -> Vec<u8> {
    let mut out = Vec::new();
    for _ in 0..=rng.below(12) {
        match rng.below(4) {
            0 => {
                let (_, text) = &seeds[rng.below(seeds.len())];
                out.extend_from_slice(text.as_bytes());
            }
            1 => out.extend_from_slice(&random_payload(rng)),
            2 => out.extend(std::iter::repeat_n(b'a', rng.below(90))),
            _ => out.push(b'\r'),
        }
        out.push(b'\n');
    }
    if rng.below(4) == 0 {
        out.extend_from_slice(b"{\"verb\":");
    }
    out
}

#[test]
fn the_line_reader_keeps_its_bounds_under_random_streams() {
    let mut rng = Xorshift(0x2545_F491_4F6C_DD1D);
    let seeds = seeds();
    let max = usize::try_from(LINE_MAX).expect("max");
    for run in 0..500 {
        let bytes = stream(&mut rng, &seeds);
        let mut reader = LineReader::new(LINE_MAX, 5_000 * MS);
        let (mut fed, mut now, mut error) = (0usize, 0u64, None);
        let mut rest = bytes.as_slice();
        while !rest.is_empty() {
            let take = (1 + rng.below(23)).min(rest.len());
            let (chunk, tail) = rest.split_at(take);
            rest = tail;
            now += MS;
            fed += take;
            let feed = reader.feed(chunk, now);
            if error.is_some() {
                assert!(feed.lines.is_empty());
                assert_eq!(feed.error, error);
            }
            for (number, line) in feed.lines.iter().enumerate() {
                assert!(line.len() < max, "run {run}");
                assert!(!line.contains(&b'\n'));
                let query = Query {
                    mode: Mode::Json,
                    ..query_for(2 * number + 1, line)
                };
                check_outcome(&query);
            }
            if feed.error.is_some() {
                error = feed.error;
                assert_eq!(reader.buffered_capacity(), 0);
            }
            assert!(reader.buffered_capacity() <= max.min(fed + 4096));
            assert!(reader.scanned() <= u64::try_from(fed).expect("fed"));
            assert!(reader.buffered_len() < max);
            let polled = reader.poll(now);
            let quiet = polled.is_ok() || error.is_some();
            assert!(quiet || polled == Err(LineError::Deadline));
        }
        match reader.eof() {
            Ok(()) => assert!(error.is_none(), "run {run}"),
            Err(LineError::Truncated) => assert!(error.is_none(), "run {run}"),
            Err(other) => assert_eq!(Some(other), error, "run {run}"),
        }
    }
}

/// One cell of the classify grid.
struct Case {
    mode: Mode,
    phase: Phase,
    level: Level,
    verb: &'static str,
    corr: u32,
}

const GRID_VERBS: [&str; 7] = [
    "hello",
    "status",
    "current",
    "snapshot_at",
    "procs",
    "events",
    "bogus",
];

const TELEMETRY: [Level; 2] = [Level::Observe, Level::Operator];
const WITH_CONTROL: [Level; 3] = [Level::Observe, Level::Operator, Level::Control];

/// Whether `level` knows `verb` (WIRE.md section 4.3), written out here from the table.
fn knows(level: Level, verb: &str) -> bool {
    match verb {
        "hello" => true,
        "status" | "events" => WITH_CONTROL.contains(&level),
        "current" | "snapshot_at" | "procs" => TELEMETRY.contains(&level),
        _ => false,
    }
}

/// The expected `(refusal code or None for a dispatch, corr)` of a valid-args request, derived
/// by hand from the R11 table (not by calling `classify`).
fn expected(case: &Case) -> (Option<Code>, u32) {
    let corr = case.corr;
    let binary = case.mode == Mode::Binary;
    let hello = case.verb == "hello";
    if corr == 0 && binary {
        return (Some(Code::BadFrame), 0);
    }
    if corr == 0 {
        return (Some(Code::BadRequest), 0);
    }
    if binary && case.phase == Phase::BeforeHello {
        if !hello {
            return (Some(Code::HelloRequired), corr);
        }
        if corr != 1 {
            return (Some(Code::BadFrame), 0);
        }
    }
    if case.verb == "bogus" {
        return (Some(Code::UnknownVerb), corr);
    }
    if !knows(case.level, case.verb) {
        return (Some(Code::Forbidden), corr);
    }
    if hello && case.phase == Phase::Ready {
        return (Some(Code::BadRequest), corr);
    }
    if !binary && matches!(case.verb, "current" | "snapshot_at") {
        return (Some(Code::BinaryRequired), corr);
    }
    if !matches!(case.verb, "hello" | "status") {
        return (Some(Code::UnknownVerb), corr);
    }
    (None, corr)
}

fn payload_of(case: &Case) -> String {
    let args = if case.verb == "hello" { HELLO_ARGS } else { "{}" };
    let verb = case.verb;
    if case.mode == Mode::Binary {
        format!("{{\"verb\":\"{verb}\",\"args\":{args}}}")
    } else {
        let corr = case.corr;
        format!("{{\"verb\":\"{verb}\",\"args\":{args},\"corr\":{corr}}}")
    }
}

#[test]
fn the_grid_of_valid_requests_matches_the_hand_derived_r11_table() {
    let mut runs = 0;
    for mode in [Mode::Binary, Mode::Json] {
        for phase in [Phase::BeforeHello, Phase::Ready] {
            for level in Level::ALL {
                for verb in GRID_VERBS {
                    for corr in [0u32, 1, 5] {
                        let case = Case {
                            mode,
                            phase,
                            level,
                            verb,
                            corr,
                        };
                        let text = payload_of(&case);
                        let query = Query {
                            frame_type: FrameType::Req,
                            corr,
                            mode,
                            phase,
                            level,
                            prefix: Prefix { major: 1, minor: 1 },
                            payload: text.as_bytes(),
                        };
                        let (code, want_corr) = expected(&case);
                        let label = format!("{mode:?} {phase:?} {} {verb} {corr}", level.name());
                        match (classify(&query), code) {
                            (Ok(done), None) => {
                                assert_eq!(done.corr, want_corr, "{label}");
                                let is_hello = matches!(done.call, Call::Hello(_));
                                assert_eq!(is_hello, verb == "hello", "{label}");
                            }
                            (Err(refusal), Some(code)) => {
                                let got = (refusal.code, refusal.corr);
                                assert_eq!(got, (code, want_corr), "{label}");
                            }
                            (other, _) => panic!("{label}: {other:?}"),
                        }
                        runs += 1;
                    }
                }
            }
        }
    }
    assert_eq!(runs, 336);
}

#[test]
fn a_line_reader_run_that_jumps_past_the_completion_deadline_is_deadline() {
    let mut reader = LineReader::new(LINE_MAX, 5_000 * MS);
    let feed = reader.feed(b"{\"verb\":\"status\"}\n{\"verb\":", MS);
    assert_eq!(feed.lines.len(), 1);
    assert_eq!(feed.error, None);
    // The incomplete tail started at `MS`: exactly at the limit is still in time.
    assert_eq!(reader.poll(5_001 * MS), Ok(()));
    // A clock that jumps far past the limit is the deadline, and the reader stays poisoned.
    assert_eq!(reader.poll(60_000 * MS), Err(LineError::Deadline));
    assert_eq!(reader.poll(0), Err(LineError::Deadline));
    let after = reader.feed(b"x", 61_000 * MS);
    assert_eq!(after.error, Some(LineError::Deadline));
}
