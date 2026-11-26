//! Scripted-server helpers shared by the client tests: frame builders, a scripted transport
//! and a fake clock. The tests pre-load the server's bytes (the replies are deterministic:
//! `corr` 1 is the hello, then 2, 3, ...), so there are no threads and no sleeps.

#![allow(dead_code)]

use std::cell::{Cell, RefCell};
use std::collections::VecDeque;
use std::io;
use std::rc::Rc;
use std::time::Duration;

use hostobs_client::{Client, ClientError, ClientInfo, Clock, Level, Redial, Transport};

/// One millisecond in nanoseconds.
pub const MS: u64 = 1_000_000;

/// The most reads (and the most writes) one scripted transport serves. No legitimate test comes
/// near it (a frame delivered one byte per read needs one call per byte of a small frame); a client
/// that loops on its transport without the clock moving (for example a hang mutant of the
/// retry path) would otherwise push a wait per call for as long as the test runner lets it
/// live: gigabytes within seconds. At this bound the script panics, so the test fails at once
/// (about 16 MB of waits, a few milliseconds).
pub const MAX_TRANSPORT_CALLS: usize = 1_000_000;

/// The default limit on injected-clock reads in a client test. A clock-driven retry loop
/// fails here instead of relying on the runner's timeout.
pub const MAX_CLOCK_CALLS: usize = 100_000;

/// A tighter clock bound for a test that deliberately exercises a timed-out socket.
pub const FAIL_FAST_CLOCK_CALLS: usize = 1_000;

/// The names of the 19 limits of a hello.
pub const LIMIT_KEYS: [&str; 19] = [
    "max_frame_bytes",
    "max_request_bytes",
    "request_deadline_ms",
    "hello_deadline_ms",
    "idle_ms",
    "stall_ms",
    "frame_completion_ms",
    "max_outstanding",
    "max_subscriptions",
    "client_queue_bytes",
    "query_queue_bytes",
    "control_queue_bytes",
    "max_reply_bytes",
    "max_block_bytes",
    "max_rows",
    "max_block_buckets",
    "max_page_blocks",
    "min_interval_ms",
    "tick_ms",
];

/// A clock the test advances by hand.
pub struct FakeClock(pub Rc<Cell<u64>>);

impl Clock for FakeClock {
    fn now_ns(&self) -> u64 {
        self.0.get()
    }
}

/// A clock wrapper that panics when a client keeps reading it without making progress.
pub struct BoundedClock<C> {
    inner: C,
    calls: Cell<usize>,
    limit: usize,
}

impl<C> BoundedClock<C> {
    pub fn new(inner: C) -> BoundedClock<C> {
        BoundedClock::with_limit(inner, MAX_CLOCK_CALLS)
    }

    pub fn with_limit(inner: C, limit: usize) -> BoundedClock<C> {
        BoundedClock {
            inner,
            calls: Cell::new(0),
            limit,
        }
    }
}

impl<C: Clock> Clock for BoundedClock<C> {
    fn now_ns(&self) -> u64 {
        let calls = self.calls.get();
        assert!(calls < self.limit, "runaway clock loop: {calls} calls");
        self.calls.set(calls + 1);
        self.inner.now_ns()
    }
}

/// A transport that serves pre-loaded bytes, at most `chunk` per read.
pub struct Script {
    pub incoming: VecDeque<u8>,
    /// Chunks released one per idle read (the clock moves by `idle_ns` first).
    pub later: VecDeque<Vec<u8>>,
    pub chunk: usize,
    pub eof: bool,
    pub idle_ns: u64,
    pub clock: Rc<Cell<u64>>,
    pub written: Rc<RefCell<Vec<u8>>>,
    pub closed: Rc<Cell<bool>>,
    pub write_error: Option<io::ErrorKind>,
    pub read_error: Option<io::ErrorKind>,
    /// An error returned by the first read only.
    pub read_error_once: Option<io::ErrorKind>,
    /// The timeout of every read, in call order.
    pub read_waits: Rc<RefCell<Vec<Duration>>>,
    /// The timeout of every write, in call order.
    pub write_waits: Rc<RefCell<Vec<Duration>>>,
}

impl Transport for Script {
    fn read(&mut self, buf: &mut [u8], timeout: Duration) -> io::Result<usize> {
        let calls = self.read_waits.borrow().len();
        assert!(
            calls < MAX_TRANSPORT_CALLS,
            "runaway transport loop: {calls} reads"
        );
        self.read_waits.borrow_mut().push(timeout);
        if let Some(kind) = self.read_error_once.take() {
            return Err(kind.into());
        }
        if let Some(kind) = self.read_error {
            return Err(kind.into());
        }
        if self.incoming.is_empty() {
            if self.eof && self.later.is_empty() {
                return Ok(0);
            }
            self.clock.set(self.clock.get() + self.idle_ns);
            if let Some(next) = self.later.pop_front() {
                self.incoming.extend(next);
            }
            return Err(io::ErrorKind::TimedOut.into());
        }
        let count = buf.len().min(self.chunk).min(self.incoming.len());
        for slot in buf.iter_mut().take(count) {
            *slot = self.incoming.pop_front().unwrap_or(0);
        }
        Ok(count)
    }

    fn write_all(&mut self, data: &[u8], timeout: Duration) -> io::Result<()> {
        let calls = self.write_waits.borrow().len();
        assert!(
            calls < MAX_TRANSPORT_CALLS,
            "runaway transport loop: {calls} writes"
        );
        self.write_waits.borrow_mut().push(timeout);
        if let Some(kind) = self.write_error {
            return Err(kind.into());
        }
        self.written.borrow_mut().extend_from_slice(data);
        Ok(())
    }

    fn close(&mut self) {
        self.closed.set(true);
    }
}

/// What a test keeps to look at after the script went into the client.
pub struct Rig {
    pub clock: Rc<Cell<u64>>,
    pub written: Rc<RefCell<Vec<u8>>>,
    pub closed: Rc<Cell<bool>>,
    pub read_waits: Rc<RefCell<Vec<Duration>>>,
    pub write_waits: Rc<RefCell<Vec<Duration>>>,
}

/// A script that serves `bytes`, then end of stream.
pub fn script(clock: &Rc<Cell<u64>>, bytes: Vec<u8>) -> (Script, Rig) {
    let written = Rc::new(RefCell::new(Vec::new()));
    let closed = Rc::new(Cell::new(false));
    let read_waits = Rc::new(RefCell::new(Vec::new()));
    let write_waits = Rc::new(RefCell::new(Vec::new()));
    let script = Script {
        incoming: bytes.into(),
        later: VecDeque::new(),
        chunk: usize::MAX,
        eof: true,
        idle_ns: 0,
        clock: Rc::clone(clock),
        written: Rc::clone(&written),
        closed: Rc::clone(&closed),
        write_error: None,
        read_error: None,
        read_error_once: None,
        read_waits: Rc::clone(&read_waits),
        write_waits: Rc::clone(&write_waits),
    };
    let rig = Rig {
        clock: Rc::clone(clock),
        written,
        closed,
        read_waits,
        write_waits,
    };
    (script, rig)
}

/// The default client info of the tests: a 1 ms operation timeout.
pub fn info() -> ClientInfo {
    ClientInfo::new("test").with_timeout(Duration::from_millis(1))
}

/// Build a client over `script` at `level`.
pub fn open(script: Script, level: Level, redial: Option<Redial>) -> Result<Client, ClientError> {
    let clock = Box::new(BoundedClock::new(FakeClock(Rc::clone(&script.clock))));
    Client::from_transport(Box::new(script), clock, level, info(), redial)
}

/// Run a whole session script (prefix, hello, `tail`) and return the client and its rig.
pub fn session(tail: &[u8]) -> (Client, Rig) {
    let clock = Rc::new(Cell::new(0));
    let (script, rig) = script(&clock, session_bytes(Level::Observe, tail));
    let client = open(script, Level::Observe, None).expect("handshake");
    (client, rig)
}

pub fn prefix(major: u16, minor: u16) -> Vec<u8> {
    let mut out = b"HOB\x01".to_vec();
    out.extend_from_slice(&major.to_le_bytes());
    out.extend_from_slice(&minor.to_le_bytes());
    out
}

/// The `result` object of a hello; every limit is 1000 unless `overrides` names it.
pub fn hello_json(level: &str, overrides: &[(&str, u32)], boot: &str, epoch: u64) -> String {
    let mut limits = Vec::new();
    for key in LIMIT_KEYS {
        let found = overrides.iter().find(|(name, _)| *name == key);
        let value = found.map_or(1000, |(_, value)| *value);
        limits.push(format!("\"{key}\":{value}"));
    }
    let limits = limits.join(",");
    let parts = [
        "\"proto\":{\"major\":1,\"minor\":2}".to_owned(),
        format!("\"level\":\"{level}\""),
        "\"server\":\"hostobsd\"".to_owned(),
        format!("\"boot_id\":\"{boot}\""),
        format!("\"epoch\":{epoch}"),
        "\"features\":[\"peak_reset\"]".to_owned(),
        "\"verbs\":[\"hello\",\"status\"]".to_owned(),
        "\"quality_codes\":[{\"code\":0,\"name\":\"ok\"}]".to_owned(),
        format!("\"limits\":{{{limits}}}"),
    ];
    format!("{{{}}}", parts.join(","))
}

pub const BOOT: &str = "00112233-4455-6677-8899-aabbccddeeff";

/// The server's side of a successful handshake plus `tail`.
pub fn session_bytes(level: Level, tail: &[u8]) -> Vec<u8> {
    let mut out = prefix(1, 2);
    out.extend(rep_ok(1, &hello_json(level.name(), &[], BOOT, 7)));
    out.extend_from_slice(tail);
    out
}

pub fn frame(code: u8, corr: u32, payload: &[u8]) -> Vec<u8> {
    let total = u32::try_from(12 + payload.len()).expect("small");
    let mut out = total.to_le_bytes().to_vec();
    out.extend_from_slice(&[code, 0, 0, 0]);
    out.extend_from_slice(&corr.to_le_bytes());
    out.extend_from_slice(payload);
    out
}

pub fn rep_ok(corr: u32, result: &str) -> Vec<u8> {
    let payload = format!("{{\"ok\":true,\"result\":{result}}}");
    frame(2, corr, payload.as_bytes())
}

pub fn rep_err(corr: u32, code: &str, detail: &str) -> Vec<u8> {
    let head = format!("{{\"code\":\"{code}\",\"message\":\"m\"");
    let error = format!("{head},\"detail\":{detail}}}");
    let payload = format!("{{\"ok\":false,\"error\":{error}}}");
    frame(2, corr, payload.as_bytes())
}

pub fn end_ok(corr: u32, blocks: u32, dropped: u32, reason: &str, next: Option<&str>) -> Vec<u8> {
    let next = next.map_or("null".to_owned(), |cursor| format!("\"{cursor}\""));
    let head = format!("{{\"ok\":true,\"blocks\":{blocks},\"dropped\":{dropped}");
    let payload = format!("{head},\"next\":{next},\"reason\":\"{reason}\"}}");
    frame(6, corr, payload.as_bytes())
}

pub fn end_err(corr: u32, blocks: u32, code: &str) -> Vec<u8> {
    end_err_dropped(corr, blocks, 0, code)
}

pub fn end_err_dropped(corr: u32, blocks: u32, dropped: u32, code: &str) -> Vec<u8> {
    let head = format!("{{\"code\":\"{code}\",\"message\":\"m\"");
    let error = format!("{head},\"detail\":{{}}}}");
    let start = format!("{{\"ok\":false,\"blocks\":{blocks},\"dropped\":{dropped}");
    let payload = format!("{start},\"error\":{error}}}");
    frame(6, corr, payload.as_bytes())
}

pub fn evt_closing(reason: &str) -> Vec<u8> {
    let payload = format!("{{\"event\":\"closing\",\"reason\":\"{reason}\"}}");
    frame(3, 0, payload.as_bytes())
}

fn hex(text: &str) -> Vec<u8> {
    let bytes = text.as_bytes();
    let mut out = Vec::new();
    for pair in bytes.chunks(2) {
        let digits = std::str::from_utf8(pair).expect("ascii");
        out.push(u8::from_str_radix(digits, 16).expect("hex"));
    }
    out
}

/// The BLOCK frames (stream corr 5) of `fixtures/stream/<case>.json`, in step order.
pub fn fixture(case: &str) -> Vec<Vec<u8>> {
    let dir = concat!(env!("CARGO_MANIFEST_DIR"), "/../../fixtures/stream");
    let path = format!("{dir}/{case}.json");
    let text = std::fs::read_to_string(path).expect("fixture");
    let mut frames = Vec::new();
    for part in text.split("\"feed_hex\": \"").skip(1) {
        frames.push(hex(part.split('"').next().expect("hex text")));
    }
    frames
}

/// `frame` re-addressed to stream `corr`.
pub fn with_corr(frame: &[u8], corr: u32) -> Vec<u8> {
    let mut out = frame.to_vec();
    out[8..12].copy_from_slice(&corr.to_le_bytes());
    out
}

/// `frame` (a BLOCK) with its header's `dropped` counter set (WIRE.md §5.3: at `88 + 8 n_sec`).
pub fn with_dropped(frame: &[u8], dropped: u32) -> Vec<u8> {
    let sections = usize::from(u16::from_le_bytes([frame[12], frame[13]]));
    let at = 88 + 8 * sections;
    let mut out = frame.to_vec();
    out[at..at + 4].copy_from_slice(&dropped.to_le_bytes());
    out
}

/// The xorshift64 generator of the hostile-bytes tests (fixed seed, no dependency).
pub struct Xorshift(pub u64);

impl Xorshift {
    pub fn step(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        self.0 = x;
        x
    }

    pub fn below(&mut self, bound: usize) -> usize {
        let bound = u64::try_from(bound).expect("small");
        let value = self.step() % bound;
        usize::try_from(value).expect("below bound")
    }
}
