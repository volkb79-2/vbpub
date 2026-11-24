# hostobs probe: process and thread model

Status: design (package P-hostobs-02, closes `HOB-66`), 2026-11-24. It decides how the
`hostobs-probe` process runs the sans-IO serving core (`bins/probe/src/{server,conn}.rs`) and
the `kernel` readers: threads, the backend rule, wakeup, syscalls, socket activation, shutdown,
config and the test harness. The driver (P-09) and the sampler (P-10) follow it with no open
choice left; WIRE.md stays the protocol, this file is the process around it. Binding inputs: plan
decisions D5 (no server-side waiting), D6, D7, D8; plan v4 §A.1, §A.3, §B.4; review F3, F7, F8, F10.
Each section gives the decision, then the strongest alternative and why it lost.

## (a) Threads

| Thread | Owns | Never does |
|---|---|---|
| **server loop** (the main thread after start-up) | the one `Server<ProbeBackend>` (and with it the one `SendHub`), the listeners, every accepted `UnixStream`, the read end of the wake fd | block: every socket is non-blocking, every call into the core is bounded (§(b), P-09 rule 4) |
| **sampler** (`hostobs-sampler`) | the `kernel` readers over `RealFs`, the `IncarnationRegistry`, the clocks; builds one immutable `Tick` per `tick_ms` and publishes it as `Arc<Tick>` | touch a socket, the `Server` or the hub; wait for the server |
| **store writer** (`hostobs-writer`) | `crates/store` and its existing bounded writer queue (`writer_queue_bytes`, drops counted); later publishes an immutable `Arc` read index (P-15) | run on the sampler or server thread. **Placed, not spawned**: the live slice (P-09/P-10/P-11) starts no writer; P-16 spawns it |

- **Publication.** `Published` is a `Mutex<Arc<Tick>>`. The sampler replaces the `Arc` under the
  lock and does nothing else there; the server loop clones the `Arc` once per turn. No lock is held
  across I/O, a scan or an encode. A poisoned lock is recovered with `PoisonError::into_inner` (the
  guarded value is one pointer, never half-written).
- **`cmdline` and every `/proc/<pid>/*` read never run on the server thread.** The slice reads
  none at all (P-10 reads cgroupfs and host-wide `/proc` files only). The package that serves
  process detail gives those reads a bounded worker pool on the sampler side (the archived
  sampler's shape: a few workers, a bounded queue, one in-flight read per pid) and publishes the
  results in the tick.
- **A slow client cannot delay another one** because the loop never waits on any one socket: a
  full socket buffer is `WouldBlock`, the hub keeps or sheds that client's queue (WIRE §5.3) and the
  turn moves on. This supersedes plan v4 §B.4 "Control/observer verbs have their own serving
  threads": the probe serves no control level, and bounded turns give the observer level the same
  isolation.

**Alternative: a thread per connection** (the archived sampler, `archive/topos-sampler-poc-20261105`
`serve.rs`). Lost: WIRE §5.3 picks an overflow victim with every queue in view, so per-connection
threads would share the `Server` behind a mutex (the stale "one mutex" text) and re-introduce lock
order and blocking writes with deadlines, while every D5 guarantee is stated per server turn. Kept from the
archive: `Arc` publish, the sampler's deadline schedule and the stall evidence (`status.tick`).

## (b) The backend rule

1. A `Backend` (and later `QueryBackend`) method reads **only the turn's published immutable
   state**: the `Arc<Tick>` (and later the store's `Arc` read index) plus the turn's `now_ns`,
   which the driver sets before the turn's first core call (`begin_turn`, P-10 rule 6).
2. It makes **no syscall, no file read and no lock acquisition**, and takes no time beyond
   O(rows it resolves). `current` stops at `max_rows + 1` rows (`TooManyRows`), as it does now.
3. It never answers "not yet". Before the first tick it answers `Unavailable`. A history verb is
   answered from an in-RAM published index or refused; until P-16 the history verbs stay unserved
   (`unknown-verb`, design R11).
4. With D5 every request therefore finishes in the turn that parsed it: there is no completion to
   route back, so there is no request record.

**Alternative: blocking backend calls on a worker pool, with completion events to the loop.**
Lost: a completion needs a pending request record, a retry or resume pass and close-path cleanup,
which is exactly the machinery D5 and P-05 delete.

## (c) Tick wakeup: eventfd

The driver creates one `eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK)` and gives the sampler a dup
(`OwnedFd::try_clone`). After each publish the sampler writes the 8-byte value 1 (`EAGAIN` is
ignored: the counter is already non-zero). The loop polls the read end for `POLLIN` and reads
8 bytes, which clears the counter. Several publishes between two turns coalesce into one wakeup. The
loop compares the tick `seq` it loaded with the last one it delivered and calls `Server::on_tick`
once if it is newer, never once per missed tick. A lagging server serves the newest tick, and the
`seq` gap shows what it skipped. The same fd carries the stop wakeup (§(f)).

**Alternative: a `UnixStream::pair()` self-pipe.** It would work with std alone, but rustix is
needed anyway for `poll` and the peer credentials (§(d)), so that gain is void. It costs two fds, a
socket buffer that can fill, and a drain-until-`WouldBlock` loop, where eventfd has one fd and a
counter that coalesces by construction.

## (d) Syscall surface: rustix, one audited `unsafe` line in `kernel`

std has no `poll`, no eventfd and no absolute `CLOCK_MONOTONIC`/`CLOCK_BOOTTIME`, and
`UnixStream::peer_cred` is unstable (E0658, `peer_credentials_unix_socket`, rustc 1.99.1).

- **Crate:** `rustix`, `default-features = false`, features exactly `std` (the std fd types),
  `event` (`poll`, `eventfd`), `net` (`SO_PEERCRED`, and the `SO_DOMAIN`, `SO_TYPE` and
  `SO_ACCEPTCONN` checks of an adopted fd) and `time` (`clock_gettime` for `Monotonic` and
  `Boottime`). `fcntl_getfd`/`fcntl_setfd` and `read`/`write` are in rustix's always-on `io` module. It is
  licensed `Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT`, which `deny.toml` allows, and its
  Linux backend needs no `libc`. A later feature of the same crate needs no image rebuild unless it
  pulls in a new crate. `getpid` comes from `std::process::id`, so `process` is not enabled.
- **Version floor policy:** P-08 picks the newest rustix release that is at least 14 days past
  its crates.io release on the day P-08 resolves it. It is written once, in `[workspace.dependencies]`,
  in the same caret-floor form and reason-comment style as the existing entries; members say
  `rustix.workspace = true`, and `Cargo.lock` is the resolved authority. One rustix version only
  (`multiple-versions = "deny"`). P-08 adds the dependency to `crates/kernel` and `bins/probe`.
- **The one `unsafe`:** `OwnedFd::from_raw_fd` for each `LISTEN_FDS` descriptor lives in a new
  `crates/kernel/src/activation.rs`. `crates/kernel` already carries the plan's allowance
  (`unsafe_code = "allow"`, §A.3, `_EXPECTED_UNSAFE_OVERRIDES`) and is the plan's home for held
  fds. Its `lib.rs` changes `#![forbid(unsafe_code)]` to `#![deny(unsafe_code)]`; one function carries
  `#[allow(unsafe_code)]` with a `// SAFETY:` comment. The argument: `LISTEN_PID` equals our pid,
  the fd lies in `3..3+LISTEN_FDS`, adoption runs first in `main`, before any thread or other fd
  exists, and runs once per fd. The function then validates the fd and refuses it unless
  `fcntl_getfd` succeeds (on `EBADF` the `OwnedFd` is `mem::forget`-ed, never closed), `SO_DOMAIN`
  is `AF_UNIX`, `SO_TYPE` is `SOCK_STREAM` and `SO_ACCEPTCONN` is 1. It sets `FD_CLOEXEC` and
  returns a std `UnixListener` (`From<OwnedFd>`). Everything else in the probe stays safe code:
  rustix `poll`, `eventfd`, `socket_peercred`, `clock_gettime`.
  - The kernel doc bullet "free of `unsafe`" and the DESIGN-GUIDE "nothing uses it" paragraph
    change with it (P-09).

**Alternatives.** `libc` plus an unsafe module (the strongest): every call becomes an audited
`unsafe` block, about ten instead of one. `nix` pulls in `libc` too and adds nothing over rustix.
std thread-per-connection still needs the peer credentials and gives up the single hub (§(a)).
For the `unsafe` line, a new `crates/sys` would change `_EXPECTED_PACKAGES` and the override table
for one line, and `bins/probe` is `forbid` by §A.3.

## (e) Socket activation

The probe never binds. `main` adopts the systemd-passed sockets before anything else:

1. `kernel::activation::parse(listen_pid, listen_fds, listen_fdnames, my_pid)` is pure and tested
   without an environment. It refuses an absent or unparsable variable, `LISTEN_PID` !=
   `std::process::id()`, `LISTEN_FDS` of 0 or more than 3, a name count other than `LISTEN_FDS`,
   and an empty or duplicate name. It yields `(fd = 3 + i, name)`.
2. `kernel::activation::adopt(fd)` (§(d)) turns each into a non-blocking `UnixListener`.
3. The probe maps the names to levels against the config's `levels`. The names are `observe`,
   `operator` and `observer`; `control` (it belongs to act) and **any other name are refused**. The
   check runs both ways: every fd's name is a configured level, and every configured level has
   exactly one fd. The units set `FileDescriptorName=observe` and so on with `Accept=no`. The
   systemd default name (the unit name) is therefore refused, not guessed.

A refusal exits 2 before the sampler starts, with one line naming the variable or name. The
environment is read once and not unset (the probe spawns nothing).

**Alternative: map by fd order, or accept unnamed fds.** Lost: the order comes from the unit
files, so a mis-ordered unit would serve the operator socket (cmdline detail) as observe, or the
reverse, silently.

## (f) Shutdown

- **For the slice there is no signal handler.** `SIGTERM` keeps its default action. The kernel
  closes every socket and clients see EOF, or `ECONNRESET` when the probe held unread request
  bytes; the client treats both as connection loss. The listening sockets belong to systemd's
  `.socket` units, so connections made during a restart wait in the backlog for the next probe.
  There is no store writer to drain.
- **The internal stop path:** the sampler thread holds a drop guard that sets the shared stop
  flag and signals the wake fd, so a sampler panic (`panic = "unwind"`) or return reaches the
  loop. At the top of a turn with the flag set, the loop calls `Server::shutdown()`, makes one
  non-blocking write of each connection's final bytes (`closing shutting-down`), `finish`es every
  connection and returns; `main` exits 1 (`Restart=on-failure`). Tests stop the loop the same way.
- A sampler that is stuck (say, in a D-state read) is not a stop: the published tick stops
  advancing, which `status.tick.seq`/`last_mono_ns` show.

**Alternative: graceful `SIGTERM` handling with `closing shutting-down` for every client.** Lost for
the slice: it needs an `unsafe` signal-mask call or another crate (a new audited surface or an
image rebuild), and it buys only a politer close reason. The package that spawns the store writer
(P-16) must add it, because the writer has queued data to drain.

## (g) Config

`hostobs-probe --config <path>` (and `--version`); anything else exits 2. The file is read whole,
bounded to 64 KiB, and parsed with `toml` + serde derive and `deny_unknown_fields`. **Every key is
required and there are no others:**

```toml
[probe]
levels = ["observe", "operator"]   # served levels; each arrives as one named LISTEN_FDS socket
tick_ms = 1000                     # sampler period, and the block levels' tick_ms; 100..=60000
cgroup_root = "/sys/fs/cgroup"     # absolute, no `..`; the sampler walks under it

[limits]
max_rows = 1024                    # D8; probe-wide; 1..=4096
```

- `levels` is a non-empty subset of `observe`, `operator` and `observer`, with no duplicates.
- Every other bound stays at its compiled default (WIRE §5.1 as amended by P-06) and is announced
  in `hello.limits`. A package that needs a key adds it, required from then on.
- `cgroup_root` is configurable so the probe can watch a subtree, or a fixture tree in a test.
  `/proc` is fixed.

**Alternative: a full `[limits.<level>]` table mirroring `Limits`.** Lost: 69 keys (23 per level,
three levels) that nobody changes in the slice, and P-06 is moving the unified ones to probe-wide
keys, so the shape would change twice.

## (h) Test harness: no spawning, no check-lints spawn allowance

`Command::new` stays banned outside `crates/act` and `crates/store/src/fsio.rs`.

- **Rust tests run the driver in-process.** `driver::run(server, listeners, wake, stop)` is a
  library function. A test binds a `UnixListener` on a Linux **abstract** address
  (`SocketAddrExt::from_abstract_name`, `UnixListener::bind_addr`, a name unique per test and pid):
  no file is created, and the lane's private network namespace scopes the name. The test runs the
  loop on a thread and connects with `UnixStream::connect_addr`. That covers the real accept,
  `SO_PEERCRED` (the test's own uid and pid), the poll loop, partial writes and EOF.
- **`kernel::activation`:** `parse` is tested purely; `adopt` runs on fds whose ownership the test
  released with `into_raw_fd`: a listening abstract socket is adopted, and a connected
  `UnixStream::pair()` end, a `UnixDatagram` and a read-only file are refused.
- **The one Python run over a real socket** is a `python/tests` case. It binds an abstract
  listener and spawns the built probe with that listener as fd 3, through
  `sh -c 'exec 3<&"$2" && LISTEN_PID=$$ exec "$0" --config "$1"' <probe> <config> <fd>`
  (`pass_fds`, env `LISTEN_FDS=1`, `LISTEN_FDNAMES=observe`). Its config points `cgroup_root` at a
  committed kernel fixture tree. It is skipped,
  with a named reason, when `HOSTOBS_PROBE_BIN` is unset. P-09 makes the `conformance` lane build
  the probe (`tools/cargo-offline.sh build -p hostobs-probe`) and set the variable, so the lane
  always runs it. check-lints scans only `.rs` files, so this needs no lint change.
- **The one check-lints change (P-09)** audits the `unsafe` line instead of permitting a spawn:
  `allow(unsafe_code)` may occur exactly once in the workspace outside `crates/act`, in
  `crates/kernel/src/activation.rs`; `crates/kernel/src/lib.rs` must contain
  `#![deny(unsafe_code)]`; `tools/check-lints-mutation.py` gains a case that inserts a second
  `allow(unsafe_code)`.

**Alternative: allow `Command::new` in `bins/probe/tests/` and spawn the binary with `LISTEN_FDS`.**
Lost: it widens the spawn lint to a directory. Setting `LISTEN_PID` after the fork also needs
`CommandExt::pre_exec`, which is `unsafe` in a test crate. The in-process loop is the same code
`main` runs.

## (i) The hub text

`crates/proto/src/hub.rs` said "P5b wraps one `SendHub` in one mutex". That is wrong: the server
loop thread owns the one `SendHub` inside `Server`, with no lock (§(a)). This package fixes the hub
doc. WIRE.md §5.3 carries the same sentence, and its fix belongs to the package that owns that
section.

## Rules for P-09 (driver)

1. Files: `bins/probe/src/{driver,config}.rs`, `main.rs` and `crates/kernel/src/activation.rs`.
   Each new `src` file is added to `assay.toml` `targets`.
2. `main` order: adopt the activation fds (§(e) 1-2); read and parse the config; map names to
   levels; read `boot_id` once and fix the identity; create the eventfd; spawn the sampler; run
   the loop. Exit 2 on any start-up refusal, and 1 when the loop returns on the stop flag.
3. The poll set: the eventfd (`POLLIN`), each listener (`POLLIN`) and each connection (`POLLIN`
   until its read returned 0, `POLLOUT` only while the hub queues output for it). The timeout is
   `TURN_MS = 100`. `EINTR` counts as a turn with no events. `now_ns` is
   `clock_gettime(Monotonic)`, read once per turn.
4. **One turn, in this order, each step bounded:**
   1. check the stop flag;
   2. `begin_turn(now)`, and `on_tick(now)` once if the `seq` is new;
   3. accept up to 8 per readable listener;
   4. one read of at most 64 KiB per readable connection (design R20's read size), then
      `on_bytes`, or `on_eof` on 0;
   5. `Server::poll(now)`;
   6. writes in `ConnId` order: `output` → `write_vectored` of the two slices → `wrote`, until
      `WouldBlock`, an empty queue or 256 KiB this turn.
5. On accept, set the stream non-blocking and read `socket_peercred` into `Peer{uid,pid}`. A
   failure passes `None`, which `Server::accept` refuses uncounted. `Refused` drops the stream.
   An accept error other than `WouldBlock`, `EINTR` or `ECONNABORTED` (for example `EMFILE`)
   stops polling that listener for one turn, so the level-triggered loop cannot spin.
6. **Close ownership:** the driver keeps one `UnixStream` per `ConnId` and drops it exactly when
   it calls `Server::finish(id)`, or when the id comes back from a sweep or `is_closed` for an
   unknown id; never earlier. A closed connection gets `take_final`, one non-blocking write (a
   short write is abandoned), then `finish`. Any other read or write error is `finish` (Rust
   ignores `SIGPIPE`, so a gone peer is `EPIPE`). A connection still open after a turn whose poll
   reported `POLLHUP` or `POLLERR` is `finish`ed.
7. Never bind, install a signal handler, use `SO_PEERPIDFD` or spawn a process.
8. Tests (§(h)): hello, `current`, `subscribe` over a real socket on a scripted `FixedBackend`;
   a slow reader (the hub sheds, others are served); EOF; refused unknown and missing fd names;
   the stop path's `closing shutting-down`; and the one Python run.

## Rules for P-10 (sampler and `ProbeBackend`)

1. Files: `bins/probe/src/sampler.rs` (thread, schedule, `Published`) and
   `bins/probe/src/live.rs` (`ProbeBackend`). The work is a `Sampler::tick(&mut self) -> Tick`
   step, tested with `ManualClock` and the committed fixture trees. The thread loop is a thin
   wrapper around it.
2. **Deadline schedule:** `next += tick_ms` and sleep until `next`. When behind, skip to now and
   count the skipped deadlines. `seq` counts published ticks.
3. Each tick stamps `mono_*_ns` from `CLOCK_MONOTONIC`, `boot_ns` from `CLOCK_BOOTTIME` (both
   absolute, so a consumer can correlate them with its own clock) and `real_ns` from the wall
   clock. The epoch is `CLOCK_BOOTTIME` ns at process start. That keeps WIRE §6.4's "greater than
   every earlier epoch of this boot" with no persisted file.
4. **Publish order:** build the whole immutable `Tick`, replace the `Arc` in `Published` (nothing
   else under the lock), then write the wake eventfd. The sampler has no other channel to the
   server and never waits for it.
5. Read only cgroupfs under `cgroup_root` and host-wide `/proc` files, bounded by
   `kernel::Bounds::POLICY`. Read no `/proc/<pid>/*` and no cmdline. Identity comes from
   `IncarnationRegistry` (P-07). A failed read is a missing value with its quality code, never a
   zero, and a walk cut by a bound is a quality state, never a silent cut.
6. `ProbeBackend` holds the turn's `Option<Arc<Tick>>` and `now_ns`, set by
   `begin_turn(&mut self, now_ns) -> bool`. It is the one `&mut` method, it is called only by the
   driver before the turn's core calls, and it is `true` when the loaded `seq` is newer than the
   previous turn's.
   - `current`, `status` and `identity` follow §(b).
   - `status.tiers` is empty until the store runs.
7. The sampler thread holds the stop drop guard (§(f)). Its body must not hold the `Published`
   lock across anything that can panic.

## Request to T2 (image crate cache), forwarded verbatim

> **hostobs (P-hostobs-08) asks T2:** please add the crate `rustix` to the offline crate cache of
> the `tester-unified-rust` and `tester-unified-rust-dev` images. The version is the one P-08
> records: the newest rustix release that is at least 14 days past its crates.io release on the
> day P-08 resolves it. Include every crate `cargo fetch --locked` then lists for it from
> `hostobs/Cargo.lock` (on Linux, `bitflags` 2.x and `linux-raw-sys`, plus whatever
> target-specific entries the lock records), so that `--offline --locked` builds resolve. hostobs
> uses it with `default-features = false` and only the features `std`, `event` (`poll`,
> `eventfd`), `net` (`SO_PEERCRED`, because std's `UnixStream::peer_cred` is unstable, and
> socket-type checks on systemd-passed fds) and `time` (absolute `CLOCK_MONOTONIC` and
> `CLOCK_BOOTTIME`). Why rustix: std lacks these calls, rustix wraps them in a safe API, its
> licence (`Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT`) is inside hostobs `deny.toml`,
> and its Linux backend needs no `libc`. With it, the probe needs exactly one `unsafe` line,
> adopting a `LISTEN_FDS` descriptor. The images get a new registry-set identity, and P-08 moves
> `hostobs/run-gate.toml` and `hostobs/tools/dev` to the new tag. Design:
> `hostobs/docs/PROBE-PROCESS.md` §(d).
