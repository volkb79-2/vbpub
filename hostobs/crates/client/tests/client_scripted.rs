//! Scripted-server tests of the client (WIRE.md §1.4, §2, §4, §5.2, §6, §7.3, §10, §12 `P5a`).
//!
//! The server's bytes are pre-loaded into a scripted transport over a fake clock: no threads
//! and no sleeps. Frame builders and the fixtures of `fixtures/stream/client-*.json` live in
//! `support`. A script with `eof = false` must set `idle_ns`, or its idle reads never end.

mod support;

use std::cell::Cell;
use std::collections::VecDeque;
use std::io::{self, Read, Write};
use std::net::Shutdown;
use std::os::unix::net::{UnixListener, UnixStream};
use std::rc::Rc;
use std::time::Duration;

use hostobs_client::{
    is_fatal_code, Client, ClientError, ClientInfo, Clock, Level, MonotonicClock, Redial, Session,
    Transport, ERROR_CODES,
};
use support::{
    end_err, end_err_dropped, end_ok, evt_closing, fixture, frame, hello_json, info, open, prefix,
    rep_err, rep_ok, script, session, session_bytes, with_corr, with_dropped, BoundedClock,
    FakeClock, Script, BOOT, FAIL_FAST_CLOCK_CALLS, MAX_CLOCK_CALLS, MAX_TRANSPORT_CALLS, MS,
};

const SUB_RESULT: &str = "{\"sub\":2,\"interval_ms\":1000}";
const SELECTION: &str = "{\"entities\":[{\"all\":true}],\"metrics\":[\"cpu\"]}";
const GEN5: &str = "client-first-block-gen-5";

fn kind_of<T>(result: Result<T, ClientError>) -> String {
    match result {
        Ok(_) => "ok".to_owned(),
        Err(error) => error.kind().to_owned(),
    }
}

fn expect_kind<T>(result: Result<T, ClientError>, kind: &str) {
    assert_eq!(kind_of(result), kind);
}

fn clock() -> Rc<Cell<u64>> {
    Rc::new(Cell::new(0))
}

/// A client over `script` with the operation timeout `timeout`.
fn open_slow(script: Script, timeout: Duration) -> Result<Client, ClientError> {
    let clock = Box::new(BoundedClock::new(FakeClock(Rc::clone(&script.clock))));
    let info = ClientInfo::new("slow").with_timeout(timeout);
    Client::from_transport(Box::new(script), clock, Level::Observe, info, None)
}

struct ClockedUnixStream {
    stream: UnixStream,
    clock: Rc<Cell<u64>>,
    advance_ns: u64,
}

impl Transport for ClockedUnixStream {
    fn read(&mut self, buf: &mut [u8], timeout: Duration) -> io::Result<usize> {
        self.stream.set_read_timeout(Some(timeout))?;
        let got = Read::read(&mut self.stream, buf);
        self.clock
            .set(self.clock.get().saturating_add(self.advance_ns));
        got
    }

    fn write_all(&mut self, data: &[u8], timeout: Duration) -> io::Result<()> {
        Transport::write_all(&mut self.stream, data, timeout)
    }

    fn close(&mut self) {
        let _ = self.stream.shutdown(Shutdown::Both);
    }
}

struct ErrorOnceAfterHandshake {
    script: Script,
    error: Rc<Cell<Option<io::ErrorKind>>>,
}

impl Transport for ErrorOnceAfterHandshake {
    fn read(&mut self, buf: &mut [u8], timeout: Duration) -> io::Result<usize> {
        if let Some(kind) = self.error.take() {
            return Err(kind.into());
        }
        self.script.read(buf, timeout)
    }

    fn write_all(&mut self, data: &[u8], timeout: Duration) -> io::Result<()> {
        self.script.write_all(data, timeout)
    }

    fn close(&mut self) {
        self.script.close();
    }
}

fn with_hello(json: &str) -> Vec<u8> {
    let mut out = prefix(1, 2);
    out.extend(rep_ok(1, json));
    out
}

/// `tail` of a block verb on corr 2: the REP, two blocks (dictionary, bare) and the END.
fn query_tail() -> Vec<u8> {
    let frames = fixture(GEN5);
    let mut tail = rep_ok(2, "{\"stream\":{\"tick_ms\":1000}}");
    tail.extend(with_corr(&frames[0], 2));
    tail.extend(with_corr(&frames[1], 2));
    tail.extend(end_ok(2, 2, 0, "complete", None));
    tail
}

/// The same stream on corr 4.
fn query_tail_again() -> Vec<u8> {
    let frames = fixture(GEN5);
    let mut tail = rep_ok(4, "{}");
    tail.extend(with_corr(&frames[0], 4));
    tail.extend(with_corr(&frames[1], 4));
    tail.extend(end_ok(4, 2, 0, "complete", None));
    tail
}

/// The subscribe REP on corr 2 and two blocks (dictionary, bare).
fn sub_tail() -> Vec<u8> {
    let frames = fixture(GEN5);
    let mut tail = rep_ok(2, SUB_RESULT);
    tail.extend(with_corr(&frames[0], 2));
    tail.extend(with_corr(&frames[1], 2));
    tail
}

fn end_reason(end: Option<&hostobs_client::StreamEnd>) -> Option<String> {
    end.map(|end| end.reason.clone())
}

#[test]
fn handshake_sends_the_prefix_alone_then_hello_with_corr_one() {
    let (client, rig) = session(&[]);
    let hello = client.hello();
    assert_eq!(hello.minor, 2);
    assert_eq!(hello.level, Level::Observe);
    assert_eq!(hello.server, "hostobsd");
    assert_eq!(hello.boot_id, BOOT);
    assert_eq!(hello.epoch, 7);
    assert_eq!(hello.features, ["peak_reset"]);
    assert_eq!(hello.verbs, ["hello", "status"]);
    assert_eq!(hello.limits.max_frame_bytes, 1000);
    assert_eq!(client.session(), Session::First);
    assert_eq!(client.level(), Level::Observe);
    assert_eq!(client.closing_reason(), None);
    let written = rig.written.borrow().clone();
    assert_eq!(written.get(..8), Some(prefix(1, 2).as_slice()));
    let text = String::from_utf8_lossy(written.get(8..).unwrap_or_default());
    let needle = "{\"verb\":\"hello\",\"args\":{\"client\":\"test\"";
    assert!(text.contains(needle));
    assert_eq!(written.get(16..20), Some([1, 0, 0, 0].as_slice()));
}

#[test]
fn handshake_accepts_a_hello_exactly_at_its_own_frame_length() {
    let payload = "{\"verb\":\"hello\",\"args\":{\"client\":\"test\",\
                   \"proto\":{\"major\":1,\"minor\":2}}}";
    let total = 12 + payload.len();
    let hello_len = u32::try_from(total).expect("small");
    let json = hello_json("observe", &[("max_request_bytes", hello_len)], BOOT, 7);
    let mut bytes = prefix(1, 2);
    bytes.extend(rep_ok(1, &json));
    let clock = clock();
    let (script, _rig) = script(&clock, bytes);
    let client = open(script, Level::Observe, None).expect("handshake");
    assert_eq!(client.hello().limits.max_request_bytes, hello_len);
}

#[test]
fn handshake_refusals_are_typed() {
    let bad_level = hello_json("control", &[], BOOT, 7);
    let small_request = hello_json("observe", &[("max_request_bytes", 10)], BOOT, 7);
    let small_frame = hello_json("observe", &[("max_frame_bytes", 11)], BOOT, 7);
    let mut wrong_magic = b"XOB\x01".to_vec();
    wrong_magic.extend_from_slice(&[1, 0, 2, 0]);
    let mut half = prefix(1, 2);
    half.truncate(4);
    let mut garbage = prefix(1, 2);
    garbage.extend(frame(2, 1, b"not json"));
    let mut server_req = prefix(1, 2);
    server_req.extend(frame(1, 1, b"{}"));
    let cases: Vec<(Vec<u8>, &str)> = vec![
        (Vec::new(), "connection-lost"),
        (half, "connection-lost"),
        (prefix(1, 2), "connection-lost"),
        (wrong_magic, "unknown-mode"),
        (prefix(2, 0), "unsupported-version"),
        (prefix(1, 3), "hello-mismatch"),
        (with_hello(&bad_level), "hello-mismatch"),
        (with_hello(&small_request), "hello-mismatch"),
        (with_hello(&small_frame), "hello-mismatch"),
        (with_hello("{}"), "hello-mismatch"),
        (garbage, "json-text"),
        (server_req, "unexpected-frame"),
    ];
    for (bytes, kind) in cases {
        let clock = clock();
        let (script, rig) = script(&clock, bytes);
        expect_kind(open(script, Level::Observe, None), kind);
        assert!(rig.closed.get(), "{kind}");
    }
}

#[test]
fn handshake_server_errors_and_stray_replies() {
    let mut required = prefix(1, 2);
    required.extend(rep_err(1, "hello-required", "{}"));
    let mut fatal = prefix(1, 2);
    fatal.extend(rep_err(0, "deadline", "{}"));
    let mut stray = prefix(1, 2);
    stray.extend(rep_ok(0, "{}"));
    let mut other = prefix(1, 2);
    other.extend(rep_ok(9, "{}"));
    let cases = [
        (required, "hello-required"),
        (fatal, "deadline"),
        (stray, "bad-reply"),
        (other, "unknown-corr"),
    ];
    for (bytes, kind) in cases {
        let clock = clock();
        let (script, _rig) = script(&clock, bytes);
        expect_kind(open(script, Level::Observe, None), kind);
    }
}

#[test]
fn handshake_waits_are_bounded_by_the_operation_timeout() {
    let clock = clock();
    let (mut mute, rig) = script(&clock, Vec::new());
    mute.eof = false;
    mute.idle_ns = 2 * MS;
    expect_kind(open(mute, Level::Observe, None), "timeout");
    assert!(rig.closed.get());
    let (mut half, _rig) = script(&clock, prefix(1, 2));
    half.eof = false;
    half.idle_ns = 2 * MS;
    expect_kind(open(half, Level::Observe, None), "timeout");
}

#[test]
fn prefix_idle_timeouts_use_the_exact_handshake_deadline() {
    for (extra, expected, hello_writes) in [(0, "ok", 2), (1, "timeout", 1)] {
        let clock = clock();
        let (mut script, rig) = script(&clock, Vec::new());
        script.eof = false;
        script.idle_ns = MS + extra;
        let mut bytes = prefix(1, 2);
        bytes.extend(rep_ok(1, &hello_json("observe", &[], BOOT, 7)));
        script.later.push_back(bytes);
        expect_kind(open(script, Level::Observe, None), expected);
        assert_eq!(rig.write_waits.borrow().len(), hello_writes, "{extra}");
    }
}

#[test]
fn transport_failures_are_classified() {
    let clock = clock();
    let writes = [
        (io::ErrorKind::BrokenPipe, "connection-lost"),
        (io::ErrorKind::ConnectionReset, "connection-lost"),
        (io::ErrorKind::ConnectionAborted, "connection-lost"),
        (io::ErrorKind::UnexpectedEof, "connection-lost"),
        (io::ErrorKind::NotConnected, "connection-lost"),
        (io::ErrorKind::TimedOut, "timeout"),
        (io::ErrorKind::WouldBlock, "timeout"),
        (io::ErrorKind::Interrupted, "timeout"),
        (io::ErrorKind::PermissionDenied, "io"),
    ];
    for (kind, name) in writes {
        let (mut broken, _rig) = script(&clock, Vec::new());
        broken.write_error = Some(kind);
        expect_kind(open(broken, Level::Observe, None), name);
    }
    let reads = [
        (io::ErrorKind::ConnectionReset, "connection-lost"),
        (io::ErrorKind::ConnectionAborted, "connection-lost"),
        (io::ErrorKind::BrokenPipe, "connection-lost"),
        (io::ErrorKind::PermissionDenied, "io"),
    ];
    for (kind, name) in reads {
        let (mut broken, _rig) = script(&clock, Vec::new());
        broken.read_error = Some(kind);
        expect_kind(open(broken, Level::Observe, None), name);
    }
}

#[test]
fn an_error_after_the_handshake_is_not_treated_as_an_idle_read() {
    let clock = clock();
    let (mut script, rig) = script(&clock, session_bytes(Level::Observe, &[]));
    script.eof = false;
    script.later.push_back(rep_ok(2, "{}"));
    let error = Rc::new(Cell::new(None));
    let transport = ErrorOnceAfterHandshake {
        script,
        error: Rc::clone(&error),
    };
    let clock = Box::new(BoundedClock::new(FakeClock(Rc::clone(&clock))));
    let mut client =
        Client::from_transport(Box::new(transport), clock, Level::Observe, info(), None)
            .expect("handshake");
    error.set(Some(io::ErrorKind::PermissionDenied));
    expect_kind(client.request("status", "{}"), "io");
    assert!(rig.closed.get());
}

// A client that loops on its transport with the clock standing still (a hang mutant of the
// retry path, found by the assay-r2 run) must fail the test within milliseconds, not push a
// wait per call until the runner's timeout: the scripted transport panics at its call bound.
#[test]
#[should_panic(expected = "runaway transport loop")]
fn a_runaway_read_loop_panics_instead_of_growing_without_bound() {
    let clock = clock();
    let (mut script, _rig) = script(&clock, Vec::new());
    script.eof = false;
    let mut buf = [0_u8; 4];
    for _ in 0..=MAX_TRANSPORT_CALLS {
        let _ = script.read(&mut buf, Duration::from_secs(1));
    }
}

#[test]
#[should_panic(expected = "runaway transport loop")]
fn a_runaway_write_loop_panics_instead_of_growing_without_bound() {
    let clock = clock();
    let (mut script, _rig) = script(&clock, Vec::new());
    script.write_error = Some(io::ErrorKind::WouldBlock);
    for _ in 0..=MAX_TRANSPORT_CALLS {
        let _ = script.write_all(b"x", Duration::from_secs(1));
    }
}

#[test]
#[should_panic(expected = "runaway clock loop")]
fn a_runaway_clock_loop_panics_instead_of_growing_without_bound() {
    let clock = BoundedClock::new(FakeClock(clock()));
    for _ in 0..=MAX_CLOCK_CALLS {
        let _ = clock.now_ns();
    }
}

#[test]
fn the_call_bound_is_far_above_what_any_test_needs() {
    let clock = clock();
    let (mut script, rig) = script(&clock, vec![7; 4096]);
    script.chunk = 1;
    let mut buf = [0_u8; 1];
    for _ in 0..4096 {
        assert_eq!(script.read(&mut buf, Duration::from_secs(1)).ok(), Some(1));
    }
    assert_eq!(rig.read_waits.borrow().len(), 4096);
    assert!(rig.read_waits.borrow().len() * 100 < MAX_TRANSPORT_CALLS);
}

#[test]
fn an_interrupted_read_is_retried_not_an_error() {
    let clock = clock();
    let (mut script, _rig) = script(&clock, session_bytes(Level::Observe, &[]));
    script.read_error_once = Some(io::ErrorKind::Interrupted);
    assert!(open(script, Level::Observe, None).is_ok());
    let (mut script, _rig) = self::script(&clock, session_bytes(Level::Observe, &[]));
    script.read_error_once = Some(io::ErrorKind::WouldBlock);
    assert!(open(script, Level::Observe, None).is_ok());
}

#[test]
fn bad_client_info_is_refused_before_anything_happens() {
    let clock = clock();
    let long_name = "n".repeat(65);
    let year = Duration::from_secs(366 * 24 * 3600);
    let zero = ClientInfo::new("a").with_timeout(Duration::ZERO);
    let long = ClientInfo::new("a").with_timeout(year);
    let infos = [
        (ClientInfo::new(""), "bad-client"),
        (ClientInfo::new(long_name), "bad-client"),
        (ClientInfo::new("a\nb"), "bad-client"),
        (zero, "bad-timeout"),
        (long, "bad-timeout"),
    ];
    for (info, kind) in infos {
        let (script, rig) = script(&clock, session_bytes(Level::Observe, &[]));
        let boxed = Box::new(BoundedClock::new(FakeClock(Rc::clone(&clock))));
        let result = Client::from_transport(Box::new(script), boxed, Level::Observe, info, None);
        expect_kind(result, kind);
        assert!(rig.written.borrow().is_empty());
    }
    let missing = Client::connect("/nonexistent/hostobs.sock", Level::Observe, info());
    let not_found = ClientError::Io(io::ErrorKind::NotFound);
    assert_eq!(missing.unwrap_err(), not_found);
    let bad = Client::connect("/nonexistent/x", Level::Observe, ClientInfo::new(""));
    assert_eq!(bad.unwrap_err(), ClientError::Usage("bad-client"));
}

#[test]
fn request_returns_the_validated_reply() {
    let (mut client, rig) = session(&rep_ok(2, "{\"x\":1}"));
    let reply = client.request("status", "{}").expect("reply");
    assert_eq!(reply.result_json(), "{\"x\":1}");
    assert_eq!(reply.text, "{\"ok\":true,\"result\":{\"x\":1}}");
    let written = rig.written.borrow().clone();
    let expected = frame(1, 2, b"{\"verb\":\"status\",\"args\":{}}");
    assert!(written.ends_with(&expected));
}

#[test]
fn every_server_error_code_is_a_typed_server_error() {
    for code in ERROR_CODES {
        let mut tail = rep_err(2, code, "{\"pool\":\"rows\"}");
        tail.extend(rep_ok(3, "{}"));
        let (mut client, rig) = session(&tail);
        let error = client.request("status", "{}").unwrap_err();
        let expected = ClientError::Server {
            code: code.to_owned(),
            message: "m".to_owned(),
            detail_json: "{\"pool\":\"rows\"}".to_owned(),
        };
        assert_eq!(error, expected);
        assert_eq!(error.kind(), code);
        assert_eq!(rig.closed.get(), is_fatal_code(code), "{code}");
        let next = client.request("status", "{}");
        let name = if is_fatal_code(code) {
            "connection-lost"
        } else {
            "ok"
        };
        assert_eq!(kind_of(next), name, "{code}");
    }
}

#[test]
fn protocol_violations_close_the_connection() {
    let extra_key = frame(2, 2, b"{\"ok\":true,\"result\":{},\"x\":1}");
    let cases: Vec<(Vec<u8>, &str)> = vec![
        (rep_ok(9, "{}"), "unknown-corr"),
        (end_ok(2, 0, 0, "complete", None), "unknown-corr"),
        (frame(5, 2, &[0; 16]), "unknown-corr"),
        (frame(1, 2, b"{}"), "unexpected-frame"),
        (frame(4, 2, b""), "unexpected-frame"),
        (rep_ok(0, "{}"), "bad-reply"),
        (frame(2, 2, b"{\"ok\":true}"), "missing-key"),
        (frame(2, 2, b"{\"ok\":1,\"result\":{}}"), "wrong-type"),
        (frame(3, 0, b"[]"), "json-text"),
        (extra_key, "unknown-key"),
    ];
    for (tail, kind) in cases {
        let (mut client, rig) = session(&tail);
        expect_kind(client.request("status", "{}"), kind);
        assert!(rig.closed.get(), "{kind}");
        expect_kind(client.request("status", "{}"), "connection-lost");
    }
}

#[test]
fn closing_events_are_remembered_and_other_events_ignored() {
    let (mut client, _rig) = session(&evt_closing("idle"));
    expect_kind(client.request("status", "{}"), "connection-lost");
    assert_eq!(client.closing_reason(), Some("idle"));
    let mut tail = frame(3, 0, b"{\"event\":\"other\",\"x\":1}");
    tail.extend(rep_ok(2, "{}"));
    let (mut client, _rig) = session(&tail);
    assert!(client.request("status", "{}").is_ok());
    assert_eq!(client.closing_reason(), None);
}

#[test]
fn local_refusals_keep_the_connection_and_consume_no_corr() {
    let (mut client, rig) = session(&rep_ok(2, "{}"));
    let deep = format!("{}1{}", "{\"a\":".repeat(8), "}".repeat(8));
    let big = format!("{{\"a\":\"{}\"}}", "x".repeat(1000));
    let refused = [
        ("status", deep.as_str(), "bad-args"),
        ("status", "{", "bad-args"),
        ("status", "[]", "bad-args"),
        ("status", "{},\"verb\":\"x\"", "bad-args"),
        ("Bad Verb", "{}", "bad-verb"),
        ("", "{}", "bad-verb"),
        ("status", big.as_str(), "request-too-large"),
    ];
    let sent = rig.written.borrow().len();
    for (verb, args, kind) in refused {
        expect_kind(client.request(verb, args), kind);
    }
    assert_eq!(rig.written.borrow().len(), sent);
    assert!(client.request("status", "{}").is_ok());
    assert!(!client.is_closed());
}

#[test]
fn a_request_at_the_announced_size_boundary_is_sent() {
    let args = format!("{{\"padding\":\"{}\"}}", "x".repeat(180));
    let payload = format!("{{\"verb\":\"status\",\"args\":{args}}}");
    let boundary = u32::try_from(12 + payload.len()).expect("small");
    let clock = clock();
    let mut accepted = prefix(1, 2);
    accepted.extend(rep_ok(
        1,
        &hello_json("observe", &[("max_request_bytes", boundary)], BOOT, 7),
    ));
    accepted.extend(rep_ok(2, "{}"));
    let (accepted_script, _rig) = script(&clock, accepted);
    let mut client = open(accepted_script, Level::Observe, None).expect("handshake");
    assert!(client.request("status", &args).is_ok());
    let below = boundary - 1;
    let mut refused = prefix(1, 2);
    refused.extend(rep_ok(
        1,
        &hello_json("observe", &[("max_request_bytes", below)], BOOT, 7),
    ));
    let (refused_script, _rig) = script(&clock, refused);
    let mut client = open(refused_script, Level::Observe, None).expect("handshake");
    expect_kind(client.request("status", &args), "request-too-large");
    assert!(!client.is_closed());
}

#[test]
fn block_streams_deliver_blocks_and_check_the_end() {
    let (mut client, _rig) = session(&query_tail());
    let mut stream = client.blocks("current", SELECTION).expect("stream");
    let header = stream.reply().result_json().to_owned();
    assert_eq!(header, "{\"stream\":{\"tick_ms\":1000}}");
    let blocks: Vec<_> = stream.by_ref().collect();
    assert_eq!(blocks.len(), 2);
    assert!(blocks.iter().all(Result::is_ok));
    assert_eq!(stream.received(), 2);
    let end = stream.end().expect("end").clone();
    assert_eq!((end.blocks, end.dropped), (2, 0));
    assert_eq!(end.reason, "complete");
    assert_eq!(end.next, None);
    assert!(stream.next().is_none());
    assert_eq!(stream.close(), Ok(()));
}

#[test]
fn end_checks_name_their_violation() {
    let frames = fixture(GEN5);
    let ends = [
        (end_ok(2, 3, 0, "complete", None), "end-counts"),
        (end_ok(2, 2, 1, "complete", None), "end-counts"),
        (end_ok(2, 2, 0, "unsubscribed", None), "bad-reply"),
        (end_ok(2, 2, 0, "page", None), "bad-reply"),
        (end_ok(2, 2, 0, "complete", Some("c")), "bad-reply"),
        (end_ok(9, 2, 0, "complete", None), "unknown-corr"),
    ];
    for (end, kind) in ends {
        let mut tail = rep_ok(2, "{}");
        tail.extend(with_corr(&frames[0], 2));
        tail.extend(with_corr(&frames[1], 2));
        tail.extend(end);
        let (mut client, rig) = session(&tail);
        let stream = client.blocks("current", "{}").expect("stream");
        let last = stream.last().expect("items");
        expect_kind(last, kind);
        assert!(rig.closed.get());
    }
    let mut paged = rep_ok(2, "{}");
    paged.extend(end_ok(2, 0, 0, "page", Some("cursor")));
    let (mut client, _rig) = session(&paged);
    let mut stream = client.blocks("series_batch", "{}").expect("stream");
    assert!(stream.next().is_none());
    let end = stream.end().expect("end");
    assert_eq!(end.next.as_deref(), Some("cursor"));
}

#[test]
fn an_end_with_an_error_follows_the_blocks_and_keeps_the_connection() {
    let frames = fixture(GEN5);
    let mut tail = rep_ok(2, "{}");
    tail.extend(with_corr(&frames[0], 2));
    tail.extend(end_err(2, 1, "internal"));
    tail.extend(rep_ok(3, "{}"));
    let (mut client, rig) = session(&tail);
    {
        let mut stream = client.blocks("current", "{}").expect("stream");
        assert!(stream.next().expect("block").is_ok());
        let error = stream.next().expect("error").unwrap_err();
        assert_eq!(error.kind(), "internal");
        assert!(stream.next().is_none());
    }
    assert!(!rig.closed.get());
    assert!(client.request("status", "{}").is_ok());
}

#[test]
fn a_block_verb_refused_at_the_first_reply_is_a_server_error() {
    let (mut client, _rig) = session(&rep_err(2, "bad-request", "{\"key\":\"\"}"));
    expect_kind(client.blocks("current", "{}"), "bad-request");
}

#[test]
fn decoder_and_dictionary_errors_close_the_connection() {
    let cases = [
        ("client-required-section", "unsupported-required-section"),
        ("client-first-block-bare", "stale-dictionary-generation"),
        ("client-generation-regressed", "generation-regressed"),
        ("client-generation-step", "stale-dictionary-generation"),
    ];
    for (case, kind) in cases {
        let mut tail = rep_ok(2, "{}");
        for block in fixture(case) {
            tail.extend(with_corr(&block, 2));
        }
        let (mut client, rig) = session(&tail);
        let stream = client.blocks("current", "{}").expect("stream");
        let items: Vec<_> = stream.collect();
        let found = items.into_iter().find_map(Result::err);
        let failure = found.expect("an error");
        assert_eq!(failure.kind(), kind, "{case}");
        assert!(rig.closed.get());
    }
}

#[test]
fn a_block_of_another_stream_is_a_protocol_error() {
    let frames = fixture(GEN5);
    let mut tail = rep_ok(2, "{}");
    tail.extend(with_corr(&frames[0], 9));
    let (mut client, _rig) = session(&tail);
    let stream = client.blocks("current", "{}").expect("stream");
    expect_kind(stream.last().expect("item"), "unknown-corr");
    let mut tail = rep_ok(2, "{}");
    tail.extend(rep_ok(3, "{}"));
    let (mut client, _rig) = session(&tail);
    let stream = client.blocks("current", "{}").expect("stream");
    expect_kind(stream.last().expect("item"), "unknown-corr");
}

#[test]
fn closing_or_dropping_a_block_stream_discards_the_rest() {
    let mut tail = query_tail();
    tail.extend(rep_ok(3, "{}"));
    tail.extend(query_tail_again());
    let (mut client, _rig) = session(&tail);
    {
        let mut stream = client.blocks("current", SELECTION).expect("stream");
        assert_eq!(stream.close(), Ok(()));
        assert_eq!(stream.received(), 2);
        assert!(stream.next().is_none());
    }
    assert!(client.request("status", "{}").is_ok());
    {
        let _stream = client.blocks("current", SELECTION).expect("stream");
    }
    assert!(!client.is_closed());
}

#[test]
fn dropping_a_partial_block_stream_drains_its_frames() {
    let mut tail = query_tail();
    tail.extend(rep_ok(3, "{}"));
    let (mut client, rig) = session(&tail);
    drop(client.blocks("current", SELECTION).expect("stream"));
    assert!(!rig.closed.get());
    assert!(client.request("status", "{}").is_ok());
}

#[test]
fn a_subscription_yields_dictionary_and_bare_blocks_and_counts_drops() {
    let frames = fixture(GEN5);
    let mut tail = rep_ok(2, SUB_RESULT);
    tail.extend(with_corr(&frames[0], 2));
    tail.extend(with_dropped(&with_corr(&frames[1], 2), 3));
    tail.extend(end_ok(2, 2, 1, "client-eof", None));
    let (mut client, rig) = session(&tail);
    let mut sub = client.subscribe(SELECTION, 1000).expect("subscription");
    assert_eq!((sub.corr(), sub.interval_ms()), (2, 1000));
    assert!(sub.next().expect("first").is_ok());
    assert!(sub.next().expect("second").is_ok());
    assert!(sub.next().is_none());
    assert_eq!(sub.received(), 2);
    assert_eq!(sub.dropped(), 4);
    let reason = end_reason(sub.end());
    assert_eq!(reason.as_deref(), Some("client-eof"));
    assert_eq!(sub.close(), Ok(()));
    assert!(sub.next().is_none());
    drop(sub);
    let written = rig.written.borrow().clone();
    let text = String::from_utf8_lossy(&written);
    assert!(text.contains("\"verb\":\"subscribe\""));
    assert!(text.contains("\"interval_ms\":1000}"));
    assert!(!text.contains("unsubscribe"));
}

#[test]
fn a_subscription_judges_generations_and_ends_with_the_connection() {
    let mut tail = rep_ok(2, SUB_RESULT);
    for block in fixture("client-generation-step") {
        tail.extend(with_corr(&block, 2));
    }
    let (mut client, rig) = session(&tail);
    let sub = client.subscribe(SELECTION, 1000).expect("subscription");
    let items: Vec<_> = sub.collect();
    let kinds: Vec<String> = items.into_iter().map(kind_of).collect();
    assert_eq!(kinds, ["ok", "ok", "ok", "stale-dictionary-generation"]);
    assert!(rig.closed.get());
    let (mut client, _rig) = session(&sub_tail());
    let mut sub = client.subscribe(SELECTION, 1000).expect("subscription");
    assert_eq!(sub.by_ref().take(2).filter(Result::is_ok).count(), 2);
    assert_eq!(sub.dropped(), 0);
    expect_kind(sub.next().expect("lost"), "connection-lost");
    assert!(sub.next().is_none());
    assert_eq!(sub.close(), Ok(()));
}

#[test]
fn close_unsubscribes_and_drains_the_stream() {
    let frames = fixture(GEN5);
    let mut tail = sub_tail();
    tail.extend(with_corr(&frames[1], 2));
    tail.extend(rep_ok(3, "{}"));
    tail.extend(end_ok(2, 3, 0, "unsubscribed", None));
    tail.extend(rep_ok(4, "{}"));
    let (mut client, rig) = session(&tail);
    {
        let mut sub = client.subscribe(SELECTION, 1000).expect("subscription");
        assert!(sub.next().expect("first").is_ok());
        assert_eq!(sub.close(), Ok(()));
        assert_eq!(sub.received(), 3);
        let reason = end_reason(sub.end());
        assert_eq!(reason.as_deref(), Some("unsubscribed"));
        assert_eq!(sub.close(), Ok(()));
    }
    assert!(client.request("status", "{}").is_ok());
    let written = rig.written.borrow().clone();
    let text = String::from_utf8_lossy(&written);
    assert!(text.contains("unsubscribe"));
    assert!(text.contains("{\"sub\":2}"));
}

#[test]
fn unsubscribe_tolerates_not_found_and_reports_other_refusals() {
    let mut gone = rep_ok(2, SUB_RESULT);
    gone.extend(rep_err(3, "not-found", "{}"));
    gone.extend(end_ok(2, 0, 0, "client-eof", None));
    let (mut client, _rig) = session(&gone);
    let mut sub = client.subscribe(SELECTION, 1000).expect("subscription");
    assert_eq!(sub.close(), Ok(()));
    drop(sub);
    let mut refused = rep_ok(2, SUB_RESULT);
    refused.extend(rep_err(3, "forbidden", "{}"));
    let (mut client, _rig) = session(&refused);
    let mut sub = client.subscribe(SELECTION, 1000).expect("subscription");
    expect_kind(sub.close(), "forbidden");
    let mut early = rep_ok(2, SUB_RESULT);
    early.extend(end_ok(2, 0, 0, "client-eof", None));
    early.extend(rep_err(3, "not-found", "{}"));
    let (mut client, _rig) = session(&early);
    let mut sub = client.subscribe(SELECTION, 1000).expect("subscription");
    assert_eq!(sub.close(), Ok(()));
    let mut stray = rep_ok(2, SUB_RESULT);
    stray.extend(rep_ok(9, "{}"));
    let (mut client, _rig) = session(&stray);
    let mut sub = client.subscribe(SELECTION, 1000).expect("subscription");
    expect_kind(sub.close(), "unknown-corr");
}

#[test]
fn dropping_a_subscription_unsubscribes() {
    let mut tail = rep_ok(2, SUB_RESULT);
    tail.extend(rep_ok(3, "{}"));
    tail.extend(end_ok(2, 0, 0, "unsubscribed", None));
    tail.extend(rep_ok(4, "{}"));
    let (mut client, rig) = session(&tail);
    drop(client.subscribe(SELECTION, 1000).expect("subscription"));
    assert!(client.request("status", "{}").is_ok());
    let written = rig.written.borrow().clone();
    assert!(String::from_utf8_lossy(&written).contains("unsubscribe"));
}

#[test]
fn subscribe_checks_the_selection_and_the_confirmation() {
    let (mut client, rig) = session(&[]);
    let sent = rig.written.borrow().len();
    for selection in ["[]", "1,\"interval_ms\":9", "", "{}}", "{\"a\":"] {
        expect_kind(client.subscribe(selection, 1000), "bad-selection");
    }
    assert_eq!(rig.written.borrow().len(), sent);
    for echoed in [
        "{\"sub\":9,\"interval_ms\":1000}",
        "{\"sub\":2,\"interval_ms\":5}",
        "{}",
    ] {
        let (mut client, rig) = session(&rep_ok(2, echoed));
        expect_kind(client.subscribe(SELECTION, 1000), "bad-reply");
        assert!(rig.closed.get());
    }
}

#[test]
fn a_request_wait_ends_strictly_after_the_deadline() {
    for (extra, expected) in [(0, "ok"), (1, "timeout")] {
        let clock = clock();
        let (mut script, rig) = script(&clock, session_bytes(Level::Observe, &[]));
        script.eof = false;
        script.idle_ns = MS + extra;
        script.later.push_back(rep_ok(2, "{}"));
        let mut client = open(script, Level::Observe, None).expect("open");
        assert_eq!(kind_of(client.request("status", "{}")), expected);
        assert_eq!(rig.closed.get(), extra == 1);
    }
}

#[test]
fn a_subscription_wait_adds_its_interval_and_ends_strictly_after() {
    for (extra, expected) in [(0, "ok"), (1, "timeout")] {
        let clock = clock();
        let frames = fixture(GEN5);
        let bytes = session_bytes(Level::Observe, &rep_ok(2, SUB_RESULT));
        let (mut script, _rig) = script(&clock, bytes);
        script.eof = false;
        script.idle_ns = 1001 * MS + extra;
        script.later.push_back(with_corr(&frames[0], 2));
        let mut client = open(script, Level::Observe, None).expect("open");
        let mut sub = client.subscribe(SELECTION, 1000).expect("subscription");
        let first = sub.next().expect("item");
        assert_eq!(kind_of(first), expected);
    }
}

#[test]
fn a_partial_frame_is_bounded_by_its_completion_age() {
    let reply = rep_ok(2, "{}");
    for (extra, expected) in [(0, "ok"), (1, "deadline")] {
        let clock = clock();
        let head = reply.get(..5).unwrap_or_default();
        let (mut script, rig) = script(&clock, session_bytes(Level::Observe, head));
        script.eof = false;
        script.idle_ns = 30_000 * MS + extra;
        script
            .later
            .push_back(reply.get(5..).unwrap_or_default().to_vec());
        let mut client = open_slow(script, Duration::from_secs(100)).expect("open");
        assert_eq!(kind_of(client.request("status", "{}")), expected);
        assert_eq!(rig.closed.get(), extra == 1);
    }
}

#[test]
fn a_partial_frame_keeps_the_completion_bound_after_the_call_deadline() {
    let reply = rep_ok(2, "{}");
    let head = reply.get(..5).unwrap_or_default();
    let handshake = session_bytes(Level::Observe, &[]);
    let clock = clock();
    let (mut script, _rig) = script(&clock, session_bytes(Level::Observe, head));
    script.chunk = handshake.len().saturating_sub(8);
    script.eof = false;
    script.idle_ns = 2 * MS;
    script
        .later
        .push_back(reply.get(5..).unwrap_or_default().to_vec());
    let mut client = open_slow(script, Duration::from_millis(1)).expect("open");
    assert!(client.request("status", "{}").is_ok());
}

#[test]
fn a_frame_started_before_the_deadline_ages_from_its_first_byte() {
    let reply = rep_ok(2, "{}");
    let head = reply.get(..5).unwrap_or_default();
    let clock = clock();
    let (mut script, rig) = script(&clock, session_bytes(Level::Observe, &[]));
    script.eof = false;
    script.idle_ns = 20_000 * MS;
    script.later.push_back(head.to_vec());
    script
        .later
        .push_back(reply.get(5..).unwrap_or_default().to_vec());
    let mut client = open_slow(script, secs(100)).expect("open");
    let before = waits(&rig).len();
    assert!(client.request("status", "{}").is_ok());
    let all = waits(&rig);
    let request_waits = all.get(before..).unwrap_or_default();
    assert_eq!(request_waits, [secs(100), secs(80), secs(30), secs(10)]);
}

#[test]
fn a_silent_request_with_a_connected_transport_times_out() {
    let clock = clock();
    let (mut script, rig) = script(&clock, session_bytes(Level::Observe, &[]));
    script.eof = false;
    script.idle_ns = MS;
    let mut client = open(script, Level::Observe, None).expect("handshake");
    expect_kind(client.request("status", "{}"), "timeout");
    assert!(rig.closed.get());
}

#[test]
fn a_request_without_reply_bytes_reports_connection_loss() {
    let (mut client, rig) = session(&[]);
    expect_kind(client.request("status", "{}"), "connection-lost");
    assert!(rig.closed.get());
}

#[test]
fn bytes_that_arrive_one_at_a_time_are_assembled() {
    let mut tail = rep_ok(2, "{}");
    tail.extend(rep_ok(3, "{\"x\":1}"));
    let clock = clock();
    let (mut script, _rig) = script(&clock, session_bytes(Level::Observe, &tail));
    script.chunk = 1;
    let mut client = open(script, Level::Observe, None).expect("open");
    assert!(client.request("status", "{}").is_ok());
    let reply = client.request("status", "{}").expect("reply");
    assert_eq!(reply.result_json(), "{\"x\":1}");
}

#[test]
fn blocks_that_arrive_one_byte_at_a_time_are_decoded() {
    let clock = clock();
    let (mut script, _rig) = script(&clock, session_bytes(Level::Observe, &query_tail()));
    script.chunk = 1;
    let mut client = open(script, Level::Observe, None).expect("open");
    let stream = client.blocks("current", SELECTION).expect("stream");
    assert_eq!(stream.count(), 2);
}

#[test]
fn broken_streams_are_typed() {
    let reply = rep_ok(2, "{}");
    // A REP header claiming 16 MiB + 1: past even the pre-hello bound that still applies to
    // a frame only partly buffered when the hello arrives.
    let oversize = vec![1, 0, 0, 1, 2, 0, 0, 0, 2, 0, 0, 0];
    let mut bad_type = vec![12, 0, 0, 0, 9, 0, 0, 0];
    bad_type.extend_from_slice(&2u32.to_le_bytes());
    let truncated = reply.get(..5).unwrap_or_default().to_vec();
    let cases = [
        (truncated, "connection-lost"),
        (Vec::new(), "connection-lost"),
        (oversize, "oversized-frame"),
        (bad_type, "unknown-frame-type"),
    ];
    for (tail, kind) in cases {
        let (mut client, rig) = session(&tail);
        expect_kind(client.request("status", "{}"), kind);
        assert!(rig.closed.get());
    }
    let mut tail = rep_ok(2, "{}");
    tail.extend_from_slice(&[12, 0, 0, 0, 9, 0, 0, 0, 3, 0, 0, 0]);
    let (mut client, _rig) = session(&tail);
    assert!(client.request("status", "{}").is_ok());
    expect_kind(client.request("status", "{}"), "unknown-frame-type");
}

#[test]
fn close_is_idempotent_and_a_closed_client_is_connection_lost() {
    let (mut client, rig) = session(&[]);
    assert!(!client.is_closed());
    assert!(format!("{client:?}").contains("Client"));
    client.close();
    client.close();
    assert!(client.is_closed());
    assert!(rig.closed.get());
    expect_kind(client.request("status", "{}"), "connection-lost");
    expect_kind(client.blocks("current", "{}"), "connection-lost");
    expect_kind(client.subscribe(SELECTION, 1000), "connection-lost");
    expect_kind(client.reconnect(), "no-reconnect");
    drop(client);
}

#[test]
fn dropping_a_client_closes_its_transport() {
    let clock = clock();
    let (script, rig) = script(&clock, session_bytes(Level::Observe, &[]));
    let client = open(script, Level::Observe, None).expect("handshake");
    assert!(!rig.closed.get());
    drop(client);
    assert!(rig.closed.get());
}

fn redial(scripts: Vec<Script>) -> Redial {
    let mut queue = VecDeque::from(scripts);
    Box::new(move || -> io::Result<Box<dyn Transport>> {
        match queue.pop_front() {
            Some(next) => Ok(Box::new(next)),
            None => Err(io::ErrorKind::ConnectionRefused.into()),
        }
    })
}

#[test]
fn reconnect_redoes_the_handshake_and_reports_the_session() {
    let clock = clock();
    let (first, _rig) = script(&clock, session_bytes(Level::Observe, &[]));
    let bytes = session_bytes(Level::Observe, &rep_ok(2, "{}"));
    let (same, _rig) = script(&clock, bytes);
    let mut other = prefix(1, 2);
    other.extend(rep_ok(1, &hello_json("observe", &[], BOOT, 8)));
    let (changed, _rig) = script(&clock, other);
    let (refused, _rig) = script(&clock, prefix(2, 0));
    let dialer = redial(vec![same, changed, refused]);
    let mut client = open(first, Level::Observe, Some(dialer)).expect("open");
    assert_eq!(client.session(), Session::First);
    client.close();
    assert_eq!(client.reconnect(), Ok(Session::Same));
    assert!(client.request("status", "{}").is_ok());
    assert_eq!(client.reconnect(), Ok(Session::Changed));
    assert_eq!(client.hello().epoch, 8);
    expect_kind(client.reconnect(), "unsupported-version");
    assert!(client.is_closed());
    let refused_dial = ClientError::Io(io::ErrorKind::ConnectionRefused);
    assert_eq!(client.reconnect(), Err(refused_dial));
}

#[test]
fn a_reconnect_drops_the_dictionaries_of_every_stream() {
    let clock = clock();
    let frames = fixture(GEN5);
    let (first, _rig) = script(&clock, session_bytes(Level::Observe, &sub_tail()));
    let mut fresh = rep_ok(2, SUB_RESULT);
    fresh.extend(with_corr(&frames[1], 2));
    let (second, _rig) = script(&clock, session_bytes(Level::Observe, &fresh));
    let dialer = redial(vec![second]);
    let mut client = open(first, Level::Observe, Some(dialer)).expect("open");
    {
        let sub = client.subscribe(SELECTION, 1000).expect("subscription");
        assert_eq!(sub.count(), 3);
    }
    assert_eq!(client.reconnect(), Ok(Session::Same));
    let sub = client.subscribe(SELECTION, 1000).expect("subscription");
    let kinds: Vec<String> = sub.map(kind_of).collect();
    assert_eq!(kinds[0], "stale-dictionary-generation");
}

#[test]
fn a_real_unix_socket_pair_carries_a_session() {
    let (near, mut far) = UnixStream::pair().expect("pair");
    let bytes = session_bytes(Level::Operator, &rep_ok(2, "{\"ok\":1}"));
    Write::write_all(&mut far, &bytes).expect("script");
    let clock = Box::new(BoundedClock::new(MonotonicClock::default()));
    let info = ClientInfo::new("socket").with_timeout(Duration::from_secs(5));
    let opened = Client::from_transport(Box::new(near), clock, Level::Operator, info, None);
    let mut client = opened.expect("open");
    let reply = client.request("status", "{}").expect("reply");
    assert_eq!(reply.result_json(), "{\"ok\":1}");
    drop(far);
    expect_kind(client.request("status", "{}"), "connection-lost");
}

#[test]
fn hostile_bytes_never_panic_and_always_fail_typed() {
    let mut rng = support::Xorshift(0x2545_F491_4F6C_DD1D);
    for _ in 0..2000 {
        let length = rng.below(300);
        let mut tail = Vec::new();
        for _ in 0..length {
            tail.push(u8::try_from(rng.step() % 256).expect("byte"));
        }
        drive(&session_bytes(Level::Observe, &tail));
    }
    let mut valid = session_bytes(Level::Observe, &query_tail());
    valid.extend(sub_tail());
    for _ in 0..2000 {
        let mut mutant = valid.clone();
        let at = rng.below(mutant.len());
        let flip = u8::try_from(rng.step() % 255).expect("byte") + 1;
        mutant[at] ^= flip;
        if rng.below(4) == 0 {
            let keep = rng.below(mutant.len());
            mutant.truncate(keep);
        }
        drive(&mutant);
    }
}

/// Run every call kind against `bytes`; each result is a value or a typed error.
fn drive(bytes: &[u8]) {
    let clock = clock();
    let (script, _rig) = script(&clock, bytes.to_vec());
    let Ok(mut client) = open(script, Level::Observe, None) else {
        return;
    };
    if let Err(error) = client.request("status", "{}") {
        assert_closed_kind(error.kind());
    }
    if let Ok(stream) = client.blocks("current", SELECTION) {
        for item in stream.take(8) {
            if let Err(error) = item {
                assert_closed_kind(error.kind());
            }
        }
    }
    let subscribed = client.subscribe(SELECTION, 1000);
    if let Ok(sub) = subscribed {
        for item in sub.take(8) {
            if let Err(error) = item {
                assert_closed_kind(error.kind());
            }
        }
    }
}

/// Every kind a client call can name: the 50 wire kinds of `ProtoError`, the envelope kinds
/// and the client's own protocol, usage and transport names (the §10 codes are `ERROR_CODES`).
const KNOWN_KINDS: &str = "truncated oversized-frame non-zero-reserved unknown-frame-type \
    unknown-frame-flags unknown-mode unsupported-version section-reserved \
    unsupported-required-section unsupported-section-version unknown-section-flags \
    duplicate-section missing-section section-length trailing-bytes unknown-value-type \
    unknown-column-kind column-reserved conflicting-column unknown-quality unknown-aggregation \
    row-count-overflow length-overflow dictionary-entry-too-long dictionary-entry-not-utf8 \
    unknown-dictionary-kind \
    dictionary-entry-unknown-code dictionary-entry-mask duplicate-dictionary-entry \
    dictionary-entry-reserved dictionary-too-large dangling-dictionary-ref \
    dictionary-entry-control generation-exhausted stale-dictionary-generation rows-and-time \
    no-rows-or-time row-reference-not-entity column-reference-not-metric column-kind-mismatch \
    not-a-block section-order aggregation-not-permitted subject-without-time deadline \
    generation-regressed event-unknown-code event-field-bound event-text event-order \
    json-text duplicate-key unknown-key missing-key wrong-type \
    unexpected-frame unknown-corr bad-reply end-counts hello-mismatch corr-exhausted io \
    timeout connection-lost bad-verb bad-args request-too-large bad-selection bad-client \
    bad-timeout no-reconnect";

fn assert_closed_kind(kind: &str) {
    let known = KNOWN_KINDS.split_whitespace().any(|name| name == kind);
    assert!(
        known || ERROR_CODES.contains(&kind),
        "unexpected kind {kind}"
    );
}

fn secs(count: u64) -> Duration {
    Duration::from_secs(count)
}

fn waits(rig: &support::Rig) -> Vec<Duration> {
    rig.read_waits.borrow().clone()
}

#[test]
fn every_transport_wait_is_the_remaining_time_to_the_nearer_bound() {
    let clock = clock();
    let (mut first, rig) = script(&clock, session_bytes(Level::Observe, &[]));
    first.eof = false;
    first.later.push_back(rep_ok(2, "{}"));
    let mut client = open_slow(first, secs(10)).expect("open");
    assert!(client.request("status", "{}").is_ok());
    assert_eq!(waits(&rig), [secs(10), secs(10), secs(10), secs(10)]);
    assert_eq!(*rig.write_waits.borrow(), [secs(10), secs(10), secs(10)]);
    let reply = rep_ok(2, "{}");
    let head = reply.get(..5).unwrap_or_default();
    let (mut partial, rig) = script(&clock, session_bytes(Level::Observe, head));
    partial.eof = false;
    partial.idle_ns = 10_000 * MS;
    partial
        .later
        .push_back(reply.get(5..).unwrap_or_default().to_vec());
    let mut client = open_slow(partial, secs(100)).expect("open");
    assert!(client.request("status", "{}").is_ok());
    assert_eq!(waits(&rig), [secs(100), secs(100), secs(30), secs(20)]);
}

#[test]
fn a_zero_remainder_waits_one_millisecond() {
    let clock = clock();
    let (mut script, rig) = script(&clock, session_bytes(Level::Observe, &[]));
    script.eof = false;
    script.idle_ns = 10 * MS;
    script.later.push_back(rep_ok(2, "{}"));
    let ten = Duration::from_millis(10);
    let mut client = open_slow(script, ten).expect("open");
    assert!(client.request("status", "{}").is_ok());
    let one = Duration::from_millis(1);
    assert_eq!(waits(&rig), [ten, ten, ten, one]);
}

#[test]
fn a_frame_that_starts_inside_a_read_ages_from_that_read() {
    let first = rep_ok(2, "{}");
    let second = rep_ok(3, "{\"x\":1}");
    let mut release = first.get(5..).unwrap_or_default().to_vec();
    release.extend_from_slice(second.get(..5).unwrap_or_default());
    let clock = clock();
    let head = first.get(..5).unwrap_or_default();
    let (mut script, rig) = script(&clock, session_bytes(Level::Observe, head));
    script.eof = false;
    script.idle_ns = 10_000 * MS;
    script.later.push_back(release);
    script
        .later
        .push_back(second.get(5..).unwrap_or_default().to_vec());
    let mut client = open_slow(script, secs(100)).expect("open");
    assert!(client.request("status", "{}").is_ok());
    assert!(client.request("status", "{}").is_ok());
    let expected = [100, 100, 30, 20, 30, 20].map(secs);
    assert_eq!(waits(&rig), expected);
}

#[test]
fn frames_beyond_the_announced_limit_are_refused_after_the_hello() {
    let at_limit = frame(2, 2, &[b' '; 988]);
    assert!(open_session_with(&at_limit).is_ok());
    let over = frame(2, 2, &[b' '; 989]);
    expect_kind(open_session_with(&over), "oversized-frame");
    let clock = clock();
    let (mut script, _rig) = script(&clock, session_bytes(Level::Observe, &[]));
    script.eof = false;
    script.idle_ns = 10_000 * MS;
    script.later.push_back(over);
    let mut client = open_slow(script, secs(100)).expect("open");
    expect_kind(client.request("status", "{}"), "oversized-frame");
}

fn open_session_with(tail: &[u8]) -> Result<Client, ClientError> {
    let clock = clock();
    let (script, _rig) = script(&clock, session_bytes(Level::Observe, tail));
    open(script, Level::Observe, None)
}

#[test]
fn an_end_with_an_error_still_counts_its_dropped_blocks() {
    let frames = fixture(GEN5);
    let mut tail = rep_ok(2, SUB_RESULT);
    tail.extend(with_corr(&frames[0], 2));
    tail.extend(end_err_dropped(2, 1, 5, "internal"));
    let (mut client, _rig) = session(&tail);
    let mut sub = client.subscribe(SELECTION, 1000).expect("subscription");
    let items: Vec<_> = sub.by_ref().collect();
    assert_eq!(items.len(), 2);
    assert_eq!(
        kind_of(items.into_iter().nth(1).expect("error")),
        "internal"
    );
    assert_eq!(sub.dropped(), 5);
}

#[test]
fn reconnect_restarts_the_corr_and_forgets_the_closing_reason() {
    let clock = clock();
    let (first, _rig) = script(&clock, session_bytes(Level::Observe, &evt_closing("idle")));
    let (second, rig) = script(&clock, session_bytes(Level::Observe, &rep_ok(2, "{}")));
    let dialer = redial(vec![second]);
    let mut client = open(first, Level::Observe, Some(dialer)).expect("open");
    expect_kind(client.request("status", "{}"), "connection-lost");
    assert_eq!(client.closing_reason(), Some("idle"));
    assert_eq!(client.reconnect(), Ok(Session::Same));
    assert_eq!(client.closing_reason(), None);
    assert!(client.request("status", "{}").is_ok());
    let written = rig.written.borrow().clone();
    let request = frame(1, 2, b"{\"verb\":\"status\",\"args\":{}}");
    assert!(written.ends_with(&request));
    assert_eq!(written.get(16..20), Some([1, 0, 0, 0].as_slice()));
}

#[test]
fn a_connected_socket_handshake_gives_up_after_its_timeout() {
    let (near, _far) = UnixStream::pair().expect("pair");
    let now = clock();
    let transport = ClockedUnixStream {
        stream: near,
        clock: Rc::clone(&now),
        advance_ns: 2 * MS,
    };
    let clock = Box::new(BoundedClock::with_limit(
        FakeClock(now),
        FAIL_FAST_CLOCK_CALLS,
    ));
    let info = ClientInfo::new("dial").with_timeout(Duration::from_millis(1));
    let result = Client::from_transport(Box::new(transport), clock, Level::Observe, info, None);
    assert_eq!(result.unwrap_err(), ClientError::Timeout);
}

#[test]
fn connect_dials_a_real_unix_socket() {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |elapsed| elapsed.subsec_nanos());
    let name = format!("hc-{}-{nanos}.sock", std::process::id());
    let path = std::env::temp_dir().join(name);
    let listener = UnixListener::bind(&path).expect("bind");
    let server = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().expect("accept");
        let bytes = session_bytes(Level::Observe, &rep_ok(2, "{\"ok\":1}"));
        Write::write_all(&mut stream, &bytes).expect("script");
        stream
    });
    let info = ClientInfo::new("dial").with_timeout(Duration::from_secs(10));
    let result = Client::connect(&path, Level::Observe, info);
    let mut client = result.expect("connect");
    let reply = client.request("status", "{}").expect("reply");
    assert_eq!(reply.result_json(), "{\"ok\":1}");
    drop(client);
    drop(server.join().expect("server"));
}
