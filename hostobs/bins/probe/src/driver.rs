//! The bounded, single-threaded socket driver for the sans-IO server.
//!
//! One poll turn has a fixed order: stop check, backend turn, accepts, reads, timers, then
//! writes. Every socket is non-blocking, every operation has a per-turn bound, and one
//! connection owns exactly one `UnixStream`. The driver never binds, spawns or installs a
//! signal handler.

use std::collections::{BTreeMap, BTreeSet};
use std::io::{self, IoSlice, Read, Write};
use std::os::fd::OwnedFd;
use std::os::unix::fs::MetadataExt;
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use rustix::event::{poll, PollFd, PollFlags, Timespec};

use hostobs_kernel::activation::peer_credentials_raw;
use hostobs_serve::backend::Turn;
use hostobs_serve::conn::{CgroupKey, Peer};
use hostobs_serve::hub::ConnId;
use hostobs_serve::limits::Level;
use hostobs_serve::server::{Accept, Server};

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
const UCRED_LEN: usize = 12;
const MAX_COMM_BYTES: usize = 16;
const MAX_CGROUP_BYTES: usize = 64 * 1024;

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

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum PeerCredentialError {
    InvalidLength,
    NegativePid,
}

fn decode_ucred(
    bytes: &[u8],
    returned_len: usize,
) -> Result<(u32, Option<u32>), PeerCredentialError> {
    if returned_len != UCRED_LEN || bytes.len() != UCRED_LEN {
        return Err(PeerCredentialError::InvalidLength);
    }

    let pid = i32::from_ne_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]);
    if pid < 0 {
        return Err(PeerCredentialError::NegativePid);
    }
    let uid = u32::from_ne_bytes([bytes[4], bytes[5], bytes[6], bytes[7]]);
    let pid = if pid == 0 {
        None
    } else {
        Some(u32::try_from(pid).map_err(|_| PeerCredentialError::NegativePid)?)
    };
    Ok((uid, pid))
}

fn read_bounded(path: &Path, max_bytes: usize) -> Option<Vec<u8>> {
    let file = std::fs::File::open(path).ok()?;
    let read_limit = u64::try_from(max_bytes).ok()?.checked_add(1)?;
    let mut bytes = Vec::with_capacity(max_bytes.min(4096));
    file.take(read_limit).read_to_end(&mut bytes).ok()?;
    (bytes.len() <= max_bytes).then_some(bytes)
}

fn parse_comm(bytes: &[u8]) -> Option<String> {
    let bytes = bytes.strip_suffix(b"\n").unwrap_or(bytes);
    std::str::from_utf8(bytes).ok().map(str::to_owned)
}

fn unified_cgroup_path(contents: &str) -> Option<PathBuf> {
    let mut paths = contents
        .lines()
        .filter_map(|line| line.strip_prefix("0::/"));
    let relative = Path::new(paths.next()?);
    if paths.next().is_some()
        || relative.is_absolute()
        || relative
            .components()
            .any(|component| matches!(component, Component::ParentDir))
    {
        return None;
    }
    Some(relative.to_path_buf())
}

fn read_cgroup_key(
    proc_cgroup: &Path,
    cgroup_root: &Path,
    mut file_identity: impl FnMut(&Path) -> io::Result<(u64, u64, bool)>,
) -> Option<CgroupKey> {
    let bytes = read_bounded(proc_cgroup, MAX_CGROUP_BYTES)?;
    let contents = std::str::from_utf8(&bytes).ok()?;
    let relative = unified_cgroup_path(contents)?;
    if relative
        .components()
        .any(|component| !matches!(component, Component::Normal(_)))
    {
        return None;
    }
    let (root_dev, root_inode, root_is_dir) = file_identity(cgroup_root).ok()?;
    if !root_is_dir {
        return None;
    }
    let mut target = cgroup_root.to_path_buf();
    let mut identity = (root_dev, root_inode, true);
    for component in relative.components() {
        let Component::Normal(name) = component else {
            return None;
        };
        target.push(name);
        identity = file_identity(&target).ok()?;
        if !identity.2 {
            return None;
        }
    }
    let (target_dev, inode, _) = identity;
    (target_dev == root_dev).then_some(CgroupKey {
        dev: target_dev,
        inode,
    })
}

fn cgroup_path_identity(path: &Path) -> io::Result<(u64, u64, bool)> {
    let metadata = std::fs::symlink_metadata(path)?;
    Ok((metadata.dev(), metadata.ino(), metadata.is_dir()))
}

fn peer_labels(
    pid: u32,
    proc_root: &Path,
    cgroup_root: &Path,
) -> (Option<String>, Option<CgroupKey>) {
    let pid_root = proc_root.join(pid.to_string());
    let comm =
        read_bounded(&pid_root.join("comm"), MAX_COMM_BYTES).and_then(|bytes| parse_comm(&bytes));
    let cgroup = read_cgroup_key(&pid_root.join("cgroup"), cgroup_root, cgroup_path_identity);
    (comm, cgroup)
}

fn peer_from_ucred(
    bytes: &[u8],
    returned_len: usize,
    proc_root: &Path,
    cgroup_root: &Path,
) -> Option<Peer> {
    let (uid, pid) = decode_ucred(bytes, returned_len).ok()?;
    let (comm, cgroup) = pid
        .map(|pid| peer_labels(pid, proc_root, cgroup_root))
        .unwrap_or_default();
    Some(Peer {
        uid,
        pid,
        comm,
        cgroup,
    })
}

fn peer(stream: &UnixStream, proc_root: &Path, cgroup_root: &Path) -> Option<Peer> {
    let (bytes, returned_len) = peer_credentials_raw(stream).ok()?;
    peer_from_ucred(
        &bytes,
        usize::try_from(returned_len).ok()?,
        proc_root,
        cgroup_root,
    )
}

/// Where the advisory peer label is read from.
#[derive(Clone, Copy)]
struct PeerRoots<'a> {
    proc: &'a Path,
    cgroup: &'a Path,
}

fn accept_ready<B: Turn>(
    server: &mut Server<B>,
    listeners: &mut [(Level, UnixListener)],
    sockets: &mut BTreeMap<ConnId, Open>,
    ready: &BTreeSet<usize>,
    skip_next: &mut BTreeSet<usize>,
    roots: PeerRoots<'_>,
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
                    let peer = peer(&stream, roots.proc, roots.cgroup);
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
    proc_root: &Path,
    cgroup_root: &Path,
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
                let has_output = server.hub().queued(id) != hostobs_serve::hub::Pools::default();
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
            PeerRoots {
                proc: proc_root,
                cgroup: cgroup_root,
            },
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
    use std::path::{Path, PathBuf};
    use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
    use std::sync::Arc;
    use std::thread::{self, JoinHandle};
    use std::time::{Duration, Instant};

    use hostobs_proto::envelope::{check_end, check_evt, check_rep};
    use hostobs_proto::frame::{decode_block, FrameHeader, FrameType};
    use rustix::event::{eventfd, EventfdFlags};

    use super::{
        cgroup_path_identity, parse_comm, peer_from_ucred, read_bounded, read_cgroup_key, run,
    };
    use hostobs_serve::backend::{
        Backend, BackendError, FixedBackend, Identity, StatusInfo, TickData, Turn,
    };
    use hostobs_serve::conn::CgroupKey;
    use hostobs_serve::encode::{control_frame, server_prefix};
    use hostobs_serve::limits::Level;
    use hostobs_serve::selection::Selection;
    use hostobs_serve::server::{Server, ServerLimits};
    use hostobs_serve::tick::{query_block, BlockBounds};

    const CLIENT_WAIT: Duration = Duration::from_secs(30);
    const HELLO_ARGS: &str = r#"{"client":"driver-test","proto":{"major":1,"minor":1}}"#;
    const CURRENT_ARGS: &str = r#"{"selection":{"entities":[{"inc":1}],"metrics":["m"]}}"#;

    static NEXT_SOCKET: AtomicU32 = AtomicU32::new(0);
    static NEXT_TEMP_DIR: AtomicU32 = AtomicU32::new(0);

    fn peer_fixture_root() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/peer-label")
    }

    fn raw_ucred(pid: i32, uid: u32) -> [u8; 12] {
        let mut bytes = [0_u8; 12];
        bytes[..4].copy_from_slice(&pid.to_ne_bytes());
        bytes[4..8].copy_from_slice(&uid.to_ne_bytes());
        bytes[8..12].copy_from_slice(&0_u32.to_ne_bytes());
        bytes
    }

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
            let proc_root = peer_fixture_root().join("proc");
            let cgroup_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("../../crates/kernel/tests/fixtures/cgroupfs");
            let join = thread::spawn(move || {
                let mut clock = move || thread_now.load(Ordering::SeqCst);
                run(
                    server,
                    listeners,
                    wake,
                    sampler_stop,
                    &proc_root,
                    &cgroup_root,
                    &mut clock,
                )
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
    fn pid_zero_is_invisible_without_constructing_a_rustix_pid() {
        let fixture = peer_fixture_root();
        let credentials = raw_ucred(0, 1000);
        let peer = peer_from_ucred(
            &credentials,
            credentials.len(),
            &fixture.join("proc"),
            &fixture.join("proc"),
        )
        .expect("pid zero still has valid credentials");
        assert_eq!(peer.uid, 1000);
        assert_eq!(peer.pid, None);
        assert_eq!(peer.comm, None);
        assert_eq!(peer.cgroup, None);
    }

    #[test]
    fn positive_pid_is_visible() {
        let fixture = peer_fixture_root();
        let credentials = raw_ucred(42, 1000);
        let peer = peer_from_ucred(
            &credentials,
            credentials.len(),
            &fixture.join("proc"),
            &fixture.join("proc"),
        )
        .expect("pid 42 credentials");
        assert_eq!(peer.pid, Some(42));
        assert_eq!(peer.comm, Some(String::from("probe")));
        assert_eq!(parse_comm(b"probe\n"), Some(String::from("probe")));
    }

    #[test]
    fn read_bounded_refuses_content_over_limit() {
        let fixture = peer_fixture_root();
        assert_eq!(read_bounded(&fixture.join("proc/42/comm"), 5), None);
        assert_eq!(read_bounded(&fixture.join("proc/18/cgroup"), 8), None);
    }

    #[test]
    fn truncated_and_negative_peer_credentials_are_refused() {
        let fixture = peer_fixture_root();
        let credentials = raw_ucred(42, 1000);
        assert!(peer_from_ucred(
            &credentials,
            8,
            &fixture.join("proc"),
            &fixture.join("proc"),
        )
        .is_none());

        let credentials = raw_ucred(-1, 1000);
        assert!(peer_from_ucred(
            &credentials,
            credentials.len(),
            &fixture.join("proc"),
            &fixture.join("proc"),
        )
        .is_none());
    }

    #[test]
    fn gone_peer_has_no_comm() {
        let fixture = peer_fixture_root();
        let credentials = raw_ucred(42, 1000);
        let peer = peer_from_ucred(
            &credentials,
            credentials.len(),
            &fixture.join("proc"),
            &fixture.join("proc"),
        )
        .expect("pid 42 credentials");
        assert_eq!(peer.comm, None);
    }

    #[test]
    fn non_utf8_comm_is_omitted() {
        assert_eq!(parse_comm(&[0xff, b'\n']), None);
    }

    #[test]
    fn parent_component_in_peer_cgroup_path_is_omitted() {
        let fixture = peer_fixture_root().join("proc/17/cgroup");
        let key = read_cgroup_key(&fixture, Path::new("unused-root"), |_| {
            panic!("invalid cgroup path must be rejected before stat")
        });
        assert_eq!(key, None);
    }

    #[test]
    fn peer_cgroup_on_a_different_device_is_omitted() {
        let fixture = peer_fixture_root().join("proc/18/cgroup");
        let root = Path::new("fixture-cgroup-root");
        let key = read_cgroup_key(&fixture, root, |path| {
            if path == root {
                Ok((10, 1, true))
            } else {
                assert_eq!(path, root.join("child"));
                Ok((11, 2, true))
            }
        });
        assert_eq!(key, None);

        let key = read_cgroup_key(&fixture, root, |path| {
            if path == root {
                Ok((10, 1, true))
            } else {
                assert_eq!(path, root.join("child"));
                Ok((10, 23, true))
            }
        });
        assert_eq!(key, Some(CgroupKey { dev: 10, inode: 23 }));
    }

    #[test]
    fn symlinked_or_non_directory_peer_cgroup_path_is_omitted() {
        let serial = NEXT_TEMP_DIR.fetch_add(1, Ordering::Relaxed);
        let temporary = std::env::temp_dir().join(format!(
            "hostobs-peer-label-{}-{serial}",
            std::process::id()
        ));
        let cgroup_root = temporary.join("cgroup");
        let outside = temporary.join("outside");
        let proc_cgroup = temporary.join("proc-cgroup");
        std::fs::create_dir(&temporary).expect("temporary fixture root");
        std::fs::create_dir(&cgroup_root).expect("temporary cgroup root");
        std::fs::create_dir_all(outside.join("grandchild")).expect("outside target");
        std::fs::write(&proc_cgroup, b"0::/child/grandchild\n").expect("proc cgroup fixture");
        std::os::unix::fs::symlink(&outside, cgroup_root.join("child")).expect("child symlink");
        assert_eq!(
            read_cgroup_key(&proc_cgroup, &cgroup_root, cgroup_path_identity),
            None
        );

        std::fs::remove_file(cgroup_root.join("child")).expect("remove child symlink");
        std::fs::write(cgroup_root.join("child"), b"not a directory").expect("child file");
        std::fs::write(&proc_cgroup, b"0::/child\n").expect("proc cgroup file fixture");
        assert_eq!(
            read_cgroup_key(&proc_cgroup, &cgroup_root, cgroup_path_identity),
            None
        );
        std::fs::remove_dir_all(temporary).expect("remove temporary cgroup fixture");
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
