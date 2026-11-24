//! The bounded, single-threaded socket driver for the sans-IO server.
//!
//! One poll turn has a fixed order: stop check, backend turn, accepts, reads, timers, then
//! writes. Every socket is non-blocking, every operation has a per-turn bound, and one
//! connection owns exactly one `UnixStream`. The driver never binds, spawns or installs a
//! signal handler.

use std::collections::{BTreeMap, BTreeSet};
use std::io::{self, IoSlice, Read, Write};
use std::os::fd::OwnedFd;
use std::os::unix::net::{UnixListener, UnixStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use hostobs_proto::hub::ConnId;
use rustix::event::{poll, PollFd, PollFlags, Timespec};

use hostobs_probe::backend::Turn;
use hostobs_probe::conn::Peer;
use hostobs_probe::limits::Level;
use hostobs_probe::server::{Accept, Server};

/// The longest one poll call waits, in milliseconds.
const TURN_MS: u64 = 100;
/// The most requests accepted from one readable listener in one turn.
const ACCEPTS_PER_TURN: usize = 8;
/// The largest read from one connection in one turn.
const READ_CHUNK: usize = 64 * 1024;
/// The most queued bytes written to one connection in one turn.
const WRITE_BUDGET: usize = 256 * 1024;

const EVENTFD_BYTES: usize = 8;
const NS_PER_MS: u64 = 1_000_000;

struct Open {
    stream: UnixStream,
    read_open: bool,
}

#[derive(Clone, Copy)]
enum PollKey {
    Wake,
    Listener(usize),
    Connection(ConnId),
}

#[derive(Default)]
struct Ready {
    wake: bool,
    listeners: BTreeSet<usize>,
    reads: BTreeSet<ConnId>,
    broken: BTreeSet<ConnId>,
}

fn io_error(error: rustix::io::Errno) -> io::Error {
    io::Error::from_raw_os_error(error.raw_os_error())
}

fn peer(stream: &UnixStream) -> Option<Peer> {
    let credentials = rustix::net::sockopt::socket_peercred(stream).ok()?;
    Some(Peer {
        uid: credentials.uid.as_raw(),
        pid: u32::try_from(credentials.pid.as_raw_pid()).ok()?,
    })
}

fn accept_ready<B: Turn>(
    server: &mut Server<B>,
    listeners: &mut [(Level, UnixListener)],
    sockets: &mut BTreeMap<ConnId, Open>,
    ready: &BTreeSet<usize>,
    skip_next: &mut BTreeSet<usize>,
    now_ns: u64,
) {
    for &index in ready {
        let Some((level, listener)) = listeners.get_mut(index) else {
            continue;
        };
        for _ in 0..ACCEPTS_PER_TURN {
            match listener.accept() {
                Ok((stream, _address)) => {
                    if stream.set_nonblocking(true).is_err() {
                        continue;
                    }
                    let peer = peer(&stream);
                    match server.accept(*level, peer, now_ns) {
                        Accept::Accepted(id) => {
                            sockets.insert(
                                id,
                                Open {
                                    stream,
                                    read_open: true,
                                },
                            );
                        }
                        Accept::Refused => drop(stream),
                    }
                }
                Err(error)
                    if error.kind() == io::ErrorKind::WouldBlock
                        || error.kind() == io::ErrorKind::Interrupted
                        || error.raw_os_error()
                            == Some(rustix::io::Errno::CONNABORTED.raw_os_error()) =>
                {
                    if error.kind() == io::ErrorKind::WouldBlock {
                        break;
                    }
                }
                Err(_) => {
                    skip_next.insert(index);
                    break;
                }
            }
        }
    }
}

fn drop_finished(sockets: &mut BTreeMap<ConnId, Open>, ids: &[ConnId]) {
    for id in ids {
        sockets.remove(id);
    }
}

fn drain_wake(wake: &OwnedFd) -> io::Result<()> {
    let mut token = [0u8; EVENTFD_BYTES];
    loop {
        match rustix::io::read(wake, &mut token) {
            Ok(EVENTFD_BYTES) => return Ok(()),
            Ok(_) => {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "eventfd read did not return eight bytes",
                ));
            }
            Err(error) if error == rustix::io::Errno::INTR => {}
            Err(error) if error == rustix::io::Errno::AGAIN => return Ok(()),
            Err(error) => return Err(io_error(error)),
        }
    }
}

fn read_ready<B: Turn>(
    server: &mut Server<B>,
    sockets: &mut BTreeMap<ConnId, Open>,
    ready: &BTreeSet<ConnId>,
    buffer: &mut [u8],
    now_ns: u64,
) {
    for &id in ready {
        if server.is_closed(id) {
            continue;
        }
        let Some(open) = sockets.get_mut(&id) else {
            continue;
        };
        if !open.read_open {
            continue;
        }
        match open.stream.read(buffer) {
            Ok(0) => {
                open.read_open = false;
                server.on_eof(id, now_ns);
            }
            Ok(count) => server.on_bytes(id, buffer.get(..count).unwrap_or_default(), now_ns),
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => {}
            Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
            Err(_) => {
                server.finish(id);
                sockets.remove(&id);
            }
        }
    }
}

fn write_final(sockets: &mut BTreeMap<ConnId, Open>, id: ConnId, bytes: &[u8]) {
    if let Some(open) = sockets.get_mut(&id) {
        let _ = open.stream.write(bytes);
    }
}

fn finish_if_closed<B: Turn>(
    server: &mut Server<B>,
    sockets: &mut BTreeMap<ConnId, Open>,
    id: ConnId,
) -> bool {
    if !server.is_closed(id) {
        return false;
    }
    if let Some(final_bytes) = server.take_final(id) {
        write_final(sockets, id, &final_bytes);
    }
    server.finish(id);
    sockets.remove(&id);
    true
}

fn write_ready<B: Turn>(server: &mut Server<B>, sockets: &mut BTreeMap<ConnId, Open>, now_ns: u64) {
    let ids: Vec<ConnId> = sockets.keys().copied().collect();
    for id in ids {
        if finish_if_closed(server, sockets, id) {
            continue;
        }

        let mut budget = WRITE_BUDGET;
        loop {
            if budget == 0 {
                break;
            }
            let written = {
                let Some(open) = sockets.get_mut(&id) else {
                    break;
                };
                let Some([first, second]) = server.output(id, now_ns) else {
                    break;
                };
                let first_len = first.len().min(budget);
                let second_len = second.len().min(budget.saturating_sub(first_len));
                let slices = [
                    IoSlice::new(first.get(..first_len).unwrap_or_default()),
                    IoSlice::new(second.get(..second_len).unwrap_or_default()),
                ];
                if first_len == 0 && second_len == 0 {
                    break;
                }
                open.stream.write_vectored(&slices)
            };
            match written {
                Ok(0) => break,
                Ok(count) => {
                    server.wrote(id, count, now_ns);
                    budget = budget.saturating_sub(count);
                }
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => break,
                Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                Err(_) => {
                    server.finish(id);
                    sockets.remove(&id);
                    break;
                }
            }
        }
        finish_if_closed(server, sockets, id);
    }
}

fn finish_broken<B: Turn>(
    server: &mut Server<B>,
    sockets: &mut BTreeMap<ConnId, Open>,
    broken: &BTreeSet<ConnId>,
) {
    for id in broken {
        if sockets.contains_key(id) {
            server.finish(*id);
            sockets.remove(id);
        }
    }
}

fn graceful_stop<B: Turn>(server: &mut Server<B>, sockets: &mut BTreeMap<ConnId, Open>) {
    let swept = server.shutdown();
    drop_finished(sockets, &swept);
    for id in server.ids() {
        if let Some(final_bytes) = server.take_final(id) {
            write_final(sockets, id, &final_bytes);
        }
        server.finish(id);
    }
    sockets.clear();
}

/// Serve the activated listeners until `stop` is set.
///
/// `now_ns` is called exactly once for a turn that is served. A stop observed at the top of
/// a turn closes every connection with `shutting-down`, gives each held final frame one
/// non-blocking write, releases every socket and returns `Ok(())`.
///
/// # Errors
///
/// An error polling the wake descriptor. Socket errors are local to the connection and
/// finish it without stopping the process.
pub(crate) fn run<B: Turn>(
    mut server: Server<B>,
    mut listeners: Vec<(Level, UnixListener)>,
    wake: OwnedFd,
    stop: Arc<AtomicBool>,
    now_ns: &mut dyn FnMut() -> u64,
) -> io::Result<()> {
    let timeout = Timespec {
        tv_sec: i64::try_from(TURN_MS / 1_000).unwrap_or(i64::MAX),
        tv_nsec: i64::try_from((TURN_MS % 1_000).saturating_mul(NS_PER_MS)).unwrap_or(i64::MAX),
    };
    let mut sockets: BTreeMap<ConnId, Open> = BTreeMap::new();
    let mut read_buffer = vec![0u8; READ_CHUNK];
    let mut skip_next = BTreeSet::new();

    loop {
        let mut ready = Ready::default();
        {
            let skipped = std::mem::take(&mut skip_next);
            let mut fds = Vec::with_capacity(
                1usize
                    .saturating_add(listeners.len())
                    .saturating_add(sockets.len()),
            );
            let mut keys = Vec::with_capacity(fds.capacity());

            fds.push(PollFd::new(&wake, PollFlags::IN));
            keys.push(PollKey::Wake);

            for (index, (_level, listener)) in listeners.iter().enumerate() {
                if skipped.contains(&index) {
                    continue;
                }
                fds.push(PollFd::new(listener, PollFlags::IN));
                keys.push(PollKey::Listener(index));
            }

            for (&id, open) in &sockets {
                let mut events = PollFlags::empty();
                if open.read_open {
                    events |= PollFlags::IN;
                }
                let has_output = server.hub().queued(id) != hostobs_proto::hub::Pools::default();
                if has_output {
                    events |= PollFlags::OUT;
                }
                if events.is_empty() {
                    continue;
                }
                fds.push(PollFd::new(&open.stream, events));
                keys.push(PollKey::Connection(id));
            }

            match poll(&mut fds, Some(&timeout)) {
                Ok(_) => {
                    for (fd, key) in fds.iter().zip(keys) {
                        let revents = fd.revents();
                        if revents.is_empty() {
                            continue;
                        }
                        match key {
                            PollKey::Wake => {
                                if revents
                                    .intersects(PollFlags::IN | PollFlags::HUP | PollFlags::ERR)
                                {
                                    ready.wake = true;
                                }
                            }
                            PollKey::Listener(index) => {
                                if revents
                                    .intersects(PollFlags::IN | PollFlags::HUP | PollFlags::ERR)
                                {
                                    ready.listeners.insert(index);
                                }
                            }
                            PollKey::Connection(id) => {
                                if revents
                                    .intersects(PollFlags::IN | PollFlags::HUP | PollFlags::ERR)
                                {
                                    ready.reads.insert(id);
                                }
                                if revents.intersects(PollFlags::HUP | PollFlags::ERR) {
                                    ready.broken.insert(id);
                                }
                            }
                        }
                    }
                }
                Err(error) if error == rustix::io::Errno::INTR => {}
                Err(error) => return Err(io_error(error)),
            }
        }

        if stop.load(Ordering::SeqCst) {
            graceful_stop(&mut server, &mut sockets);
            return Ok(());
        }

        if ready.wake {
            drain_wake(&wake)?;
        }

        let now_ns = now_ns();
        if server.backend_mut().begin_turn(now_ns) {
            server.on_tick(now_ns);
        }

        accept_ready(
            &mut server,
            &mut listeners,
            &mut sockets,
            &ready.listeners,
            &mut skip_next,
            now_ns,
        );

        read_ready(
            &mut server,
            &mut sockets,
            &ready.reads,
            &mut read_buffer,
            now_ns,
        );

        let swept = server.poll(now_ns);
        drop_finished(&mut sockets, &swept);

        write_ready(&mut server, &mut sockets, now_ns);

        finish_broken(&mut server, &mut sockets, &ready.broken);
    }
}

#[cfg(test)]
mod tests {
    use std::io::{self, Read, Write};
    use std::net::Shutdown;
    use std::os::fd::OwnedFd;
    use std::os::linux::net::SocketAddrExt;
    use std::os::unix::net::{SocketAddr, UnixListener, UnixStream};
    use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
    use std::sync::Arc;
    use std::thread::{self, JoinHandle};
    use std::time::{Duration, Instant};

    use hostobs_proto::envelope::{check_end, check_evt, check_rep};
    use hostobs_proto::frame::{decode_block, FrameHeader, FrameType};
    use rustix::event::{eventfd, EventfdFlags};

    use super::run;
    use hostobs_probe::backend::{
        Backend, BackendError, FixedBackend, Identity, StatusInfo, TickData, Turn,
    };
    use hostobs_probe::encode::{control_frame, server_prefix};
    use hostobs_probe::limits::Level;
    use hostobs_probe::selection::Selection;
    use hostobs_probe::server::{Server, ServerLimits};
    use hostobs_probe::tick::{query_block, BlockBounds};

    const CLIENT_WAIT: Duration = Duration::from_secs(30);
    const HELLO_ARGS: &str = r#"{"client":"driver-test","proto":{"major":1,"minor":1}}"#;
    const CURRENT_ARGS: &str = r#"{"selection":{"entities":[{"inc":1}],"metrics":["m"]}}"#;

    static NEXT_SOCKET: AtomicU32 = AtomicU32::new(0);

    struct TestBackend {
        fixed: FixedBackend,
        seq: Arc<AtomicU64>,
        data: TickData,
    }

    impl TestBackend {
        fn new(seq: Arc<AtomicU64>, data: TickData) -> TestBackend {
            let mut fixed = FixedBackend::new();
            fixed.identity.boot_id = String::from("00000000-0000-4000-8000-000000000009");
            TestBackend { fixed, seq, data }
        }
    }

    impl Backend for TestBackend {
        fn identity(&self) -> Identity {
            self.fixed.identity()
        }

        fn status(&self) -> StatusInfo {
            self.fixed.status()
        }

        fn current(&self, _selection: &Selection, max_rows: u32) -> Result<TickData, BackendError> {
            let limit = usize::try_from(max_rows).unwrap_or(usize::MAX);
            if self.data.rows.len() > limit {
                return Err(BackendError::TooManyRows);
            }
            let mut data = self.data.clone();
            data.header.seq = self.fixed.seen;
            Ok(data)
        }
    }

    impl Turn for TestBackend {
        fn begin_turn(&mut self, _now_ns: u64) -> bool {
            let seq = self.seq.load(Ordering::SeqCst);
            let fresh = seq != self.fixed.seen;
            self.fixed.seen = seq;
            fresh
        }
    }

    struct Rig {
        addresses: Vec<(Level, SocketAddr)>,
        wake: OwnedFd,
        stop: Arc<AtomicBool>,
        seq: Arc<AtomicU64>,
        now: Arc<AtomicU64>,
        join: Option<JoinHandle<io::Result<()>>>,
    }

    impl Rig {
        fn new(limits: ServerLimits, levels: &[Level], data: TickData) -> Rig {
            let wake =
                eventfd(0, EventfdFlags::CLOEXEC | EventfdFlags::NONBLOCK).expect("wake eventfd");
            let sampler_wake = wake.try_clone().expect("clone wake fd");
            let stop = Arc::new(AtomicBool::new(false));
            let sampler_stop = Arc::clone(&stop);
            let seq = Arc::new(AtomicU64::new(0));
            let now = Arc::new(AtomicU64::new(1));
            let thread_now = Arc::clone(&now);

            let mut listeners = Vec::with_capacity(levels.len());
            let mut addresses = Vec::with_capacity(levels.len());
            for level in levels {
                let serial = NEXT_SOCKET.fetch_add(1, Ordering::Relaxed);
                let name = format!(
                    "hostobs-driver-{}-{}-{serial}",
                    std::process::id(),
                    level.name()
                );
                let address =
                    SocketAddr::from_abstract_name(name.as_bytes()).expect("abstract address");
                let listener = UnixListener::bind_addr(&address).expect("abstract listener");
                listener
                    .set_nonblocking(true)
                    .expect("non-blocking listener");
                addresses.push((*level, address));
                listeners.push((*level, listener));
            }

            let backend = TestBackend::new(Arc::clone(&seq), data);
            let server = Server::new(limits, backend);
            let join = thread::spawn(move || {
                let mut clock = move || thread_now.load(Ordering::SeqCst);
                run(server, listeners, wake, sampler_stop, &mut clock)
            });

            Rig {
                addresses,
                wake: sampler_wake,
                stop,
                seq,
                now,
                join: Some(join),
            }
        }

        fn connect(&self, level: Level) -> Client {
            let address = self
                .addresses
                .iter()
                .find_map(|(held, address)| (*held == level).then_some(address))
                .expect("configured level");
            let stream = UnixStream::connect_addr(address).expect("connect to driver");
            stream
                .set_read_timeout(Some(CLIENT_WAIT))
                .expect("read timeout");
            stream
                .set_write_timeout(Some(CLIENT_WAIT))
                .expect("write timeout");
            Client { stream }
        }

        fn tick(&self) {
            let next = self.seq.fetch_add(1, Ordering::SeqCst).saturating_add(1);
            assert_eq!(self.seq.load(Ordering::SeqCst), next);
            self.now.fetch_add(1_000, Ordering::SeqCst);
            rustix::io::write(&self.wake, &1u64.to_le_bytes()).expect("wake eventfd");
        }

        fn stop_and_join(&mut self) -> io::Result<()> {
            if let Some(join) = self.join.take() {
                self.stop.store(true, Ordering::SeqCst);
                rustix::io::write(&self.wake, &1u64.to_le_bytes()).expect("stop wake");
                join.join().expect("driver thread does not panic")
            } else {
                Ok(())
            }
        }
    }

    impl Drop for Rig {
        fn drop(&mut self) {
            if let Some(join) = self.join.take() {
                self.stop.store(true, Ordering::SeqCst);
                let _ = rustix::io::write(&self.wake, &1u64.to_le_bytes());
                let _ = join.join();
            }
        }
    }

    struct Client {
        stream: UnixStream,
    }

    fn request_frame(corr: u32, verb: &str, args: &str) -> Vec<u8> {
        let body = format!(r#"{{"verb":"{verb}","args":{args}}}"#);
        control_frame(FrameType::Req, corr, body.as_bytes())
    }

    impl Client {
        fn send_request(&mut self, corr: u32, verb: &str, args: &str) {
            let frame = request_frame(corr, verb, args);
            self.stream.write_all(&frame).expect("write request");
        }

        fn read_prefix(&mut self) {
            let mut prefix = [0u8; 8];
            self.stream.read_exact(&mut prefix).expect("server prefix");
            assert_eq!(prefix, server_prefix(1, 1));
        }

        fn frame(&mut self) -> Option<Vec<u8>> {
            let mut header = [0u8; 12];
            match self.stream.read_exact(&mut header) {
                Ok(()) => {}
                Err(error)
                    if matches!(
                        error.kind(),
                        io::ErrorKind::UnexpectedEof
                            | io::ErrorKind::ConnectionReset
                            | io::ErrorKind::ConnectionAborted
                    ) =>
                {
                    return None;
                }
                Err(error) => panic!("read frame header: {error}"),
            }
            let len = u32::from_le_bytes([header[0], header[1], header[2], header[3]]);
            let len = usize::try_from(len).expect("frame length");
            assert!(len >= header.len(), "frame length {len} below header");
            let mut frame = vec![0u8; len];
            frame[..header.len()].copy_from_slice(&header);
            self.stream
                .read_exact(&mut frame[header.len()..])
                .expect("frame body");
            Some(frame)
        }

        fn hello(&mut self) -> String {
            self.stream
                .write_all(&server_prefix(1, 1))
                .expect("client prefix");
            self.send_request(1, "hello", HELLO_ARGS);
            self.read_prefix();
            let frame = self.frame().expect("hello REP");
            assert_frame(&frame, FrameType::Rep, 1);
            let payload = payload(&frame);
            let rep = check_rep(payload, false).expect("hello REP envelope");
            assert!(rep.ok);
            String::from_utf8(payload.to_vec()).expect("UTF-8 payload")
        }

        fn current(&mut self, corr: u32) {
            self.send_request(corr, "current", CURRENT_ARGS);
            let rep = self.frame().expect("current REP");
            assert_frame(&rep, FrameType::Rep, corr);
            let body = payload(&rep);
            assert!(check_rep(body, false).expect("current REP envelope").ok);

            let block = self.frame().expect("current BLOCK");
            assert_frame(&block, FrameType::Block, corr);
            let decoded = decode_block(&block).expect("current block");
            assert_eq!(decoded.corr, corr);

            let end = self.frame().expect("current END");
            assert_frame(&end, FrameType::End, corr);
            assert_eq!(
                end_reason(&end),
                (true, 1, 0, Some(String::from("complete")))
            );
        }

        fn subscribe(&mut self, corr: u32, args: &str) {
            self.send_request(corr, "subscribe", args);
            let rep = self.frame().expect("subscribe REP");
            assert_frame(&rep, FrameType::Rep, corr);
            assert!(
                check_rep(payload(&rep), false)
                    .expect("subscribe envelope")
                    .ok
            );
        }

        fn block_seq(&mut self, corr: u32) -> u64 {
            let frame = self.frame().expect("live BLOCK");
            assert_frame(&frame, FrameType::Block, corr);
            decode_block(&frame).expect("live block").header.seq
        }

        fn expect_clean_eof(&mut self) {
            assert!(
                self.frame().is_none(),
                "connection must end after the final frame"
            );
        }
    }

    fn payload(frame: &[u8]) -> &[u8] {
        frame.get(12..).expect("frame payload")
    }

    fn assert_frame(frame: &[u8], kind: FrameType, corr: u32) {
        let header = FrameHeader::parse(frame).expect("frame header");
        assert_eq!(header.frame_type, kind, "frame type");
        assert_eq!(header.corr, corr, "frame correlation");
    }

    fn end_reason(frame: &[u8]) -> (bool, u32, u32, Option<String>) {
        let body = payload(frame);
        let end = check_end(body).expect("END envelope");
        let reason = end.reason.map(|range| {
            String::from_utf8(body.get(range).expect("reason range").to_vec())
                .expect("UTF-8 reason")
        });
        (end.ok, end.blocks, end.dropped, reason)
    }

    fn selection_args(metrics: &[String]) -> String {
        let metrics = metrics
            .iter()
            .map(|name| format!("\"{name}\""))
            .collect::<Vec<_>>()
            .join(",");
        format!(
            r#"{{"selection":{{"entities":[{{"all":true}}],"metrics":[{metrics}]}},"interval_ms":1000}}"#
        )
    }

    fn small_data() -> TickData {
        TickData::example(0, &[1], &["m"])
    }

    fn wait_for_served(rig: &Rig, level: Level) -> Client {
        let deadline = Instant::now() + CLIENT_WAIT;
        loop {
            let mut client = rig.connect(level);
            let wrote = client
                .stream
                .write_all(&server_prefix(1, 1))
                .and_then(|()| {
                    client
                        .stream
                        .write_all(&request_frame(1, "hello", HELLO_ARGS))
                })
                .is_ok();
            if wrote {
                let mut prefix = [0u8; 8];
                if client.stream.read_exact(&mut prefix).is_ok() {
                    assert_eq!(prefix, server_prefix(1, 1));
                    let frame = client.frame().expect("retried hello REP");
                    assert_frame(&frame, FrameType::Rep, 1);
                    return client;
                }
            }
            assert!(
                Instant::now() < deadline,
                "the previous connection was never released"
            );
            thread::sleep(Duration::from_millis(5));
        }
    }

    #[test]
    fn hello_names_the_level_and_announces_the_configured_limits() {
        let mut limits = ServerLimits::new();
        limits.probe.tick_ms = 250;
        limits.probe.max_rows = 7;
        let rig = Rig::new(limits, &[Level::Observe, Level::Operator], small_data());

        let mut observe = rig.connect(Level::Observe);
        let observe_hello = observe.hello();
        assert!(observe_hello.contains(r#""level":"observe""#));
        assert!(observe_hello.contains(r#""tick_ms":250"#));
        assert!(observe_hello.contains(r#""max_rows":7"#));

        let mut operator = rig.connect(Level::Operator);
        let operator_hello = operator.hello();
        assert!(operator_hello.contains(r#""level":"operator""#));
        assert!(operator_hello.contains(r#""tick_ms":250"#));
        assert!(operator_hello.contains(r#""max_rows":7"#));
    }

    #[test]
    fn current_is_a_rep_then_one_block_then_a_complete_end() {
        let rig = Rig::new(ServerLimits::new(), &[Level::Observe], small_data());
        let mut client = rig.connect(Level::Observe);
        client.hello();
        client.current(2);
    }

    #[test]
    fn subscribed_clients_receive_each_bumped_tick_in_increasing_sequence() {
        let mut limits = ServerLimits::new();
        limits.probe.tick_ms = 1_000;
        let rig = Rig::new(limits, &[Level::Observe], small_data());
        let mut client = rig.connect(Level::Observe);
        client.hello();
        client.subscribe(2, &selection_args(&[String::from("m")]));

        rig.tick();
        assert_eq!(client.block_seq(2), 1);
        rig.tick();
        assert_eq!(client.block_seq(2), 2);
    }

    #[test]
    fn peer_credentials_enforce_the_per_uid_connection_cap() {
        // A consistent per-peer uid reaches the per-uid cap; neither uid nor pid values are
        // client-visible, so a pid-path mutant may legitimately survive assay.
        let mut limits = ServerLimits::new();
        limits.observe.max_connections_per_uid = 1;
        let rig = Rig::new(limits, &[Level::Observe], small_data());

        let mut first = rig.connect(Level::Observe);
        first.hello();
        let mut second = rig.connect(Level::Observe);
        let mut byte = [0u8; 1];
        match second.stream.read(&mut byte) {
            Ok(0) => {}
            Err(error)
                if matches!(
                    error.kind(),
                    io::ErrorKind::ConnectionReset | io::ErrorKind::ConnectionAborted
                ) => {}
            Ok(count) => panic!("refused connection sent {count} bytes"),
            Err(error) => panic!("refused connection read: {error}"),
        }

        first.current(2);
    }

    #[test]
    fn a_slow_reader_is_shed_while_another_subscriber_keeps_receiving() {
        let mut limits = ServerLimits::new();
        limits.probe.tick_ms = 1_000;
        limits.probe.max_rows = 1_024;
        limits.observe.stall_ms = 3_600_000;
        limits.observe.frame_completion_ms = 3_600_000;
        let incs: Vec<u64> = (0..1_024).collect();
        let metrics: Vec<String> = (0..64).map(|index| format!("m{index:02}")).collect();
        let data = TickData::example(
            0,
            &incs,
            &metrics.iter().map(String::as_str).collect::<Vec<_>>(),
        );
        let block_len = query_block(
            data.clone(),
            BlockBounds {
                max_rows: limits.probe.max_rows,
                max_block_bytes: limits.probe.max_block_bytes,
            },
        )
        .expect("test block fits the default bound")
        .len();
        let block_len = u32::try_from(block_len).expect("test block length");
        limits.probe.client_queue_bytes = block_len;
        limits.probe.query_queue_bytes = block_len;
        limits.probe.max_block_bytes = block_len;
        let rig = Rig::new(limits, &[Level::Observe], data);

        let mut slow = rig.connect(Level::Observe);
        slow.hello();
        slow.subscribe(2, &selection_args(&metrics));
        let mut fast = rig.connect(Level::Observe);
        fast.hello();
        fast.subscribe(2, &selection_args(&metrics));

        let mut later_dropped = None;
        for expected in 1..=48u64 {
            rig.tick();
            assert_eq!(fast.block_seq(2), expected);
        }

        for _ in 0..96 {
            let frame = slow.frame().expect("slow client stream");
            let header = FrameHeader::parse(&frame).expect("slow frame header");
            match header.frame_type {
                FrameType::Block => {
                    assert_eq!(header.corr, 2);
                    let block = decode_block(&frame).expect("slow block");
                    if block.header.dropped > 0 {
                        later_dropped = Some(block.header.dropped);
                        break;
                    }
                    rig.tick();
                }
                other => panic!(
                    "unexpected slow-reader frame {other:?}; WIRE section 5.3 must count drops"
                ),
            }
        }
        assert!(
            later_dropped.is_some_and(|dropped| dropped > 0),
            "the next pinned block must show dropped > 0"
        );
    }

    #[test]
    fn half_close_gets_client_eof_and_a_finished_client_makes_room() {
        let mut limits = ServerLimits::new();
        limits.probe.tick_ms = 1_000;
        limits.observe.max_connections = 1;
        let rig = Rig::new(limits, &[Level::Observe], small_data());

        let mut client = rig.connect(Level::Observe);
        client.hello();
        client.subscribe(2, &selection_args(&[String::from("m")]));
        client
            .stream
            .shutdown(Shutdown::Write)
            .expect("client half-close");
        let end = client.frame().expect("client-eof END");
        assert_frame(&end, FrameType::End, 2);
        assert_eq!(
            end_reason(&end),
            (true, 0, 0, Some(String::from("client-eof")))
        );
        client.expect_clean_eof();

        let disconnected = rig.connect(Level::Observe);
        drop(disconnected);
        let mut replacement = wait_for_served(&rig, Level::Observe);
        replacement.current(3);
    }

    #[test]
    fn stop_closes_every_client_with_shutting_down_then_returns() {
        let mut rig = Rig::new(ServerLimits::new(), &[Level::Observe], small_data());
        let mut client = rig.connect(Level::Observe);
        client.hello();
        client.subscribe(2, &selection_args(&[String::from("m")]));

        rig.stop_and_join().expect("driver stop");
        let closing = client.frame().expect("closing EVT");
        assert_frame(&closing, FrameType::Evt, 0);
        let body = payload(&closing);
        check_evt(body).expect("closing event");
        assert_eq!(
            String::from_utf8(body.to_vec()).expect("UTF-8 closing"),
            r#"{"event":"closing","reason":"shutting-down"}"#
        );
        client.expect_clean_eof();
    }
}
