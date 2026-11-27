//! The daemon: one tick thread, one socket loop, and the report-mode decision
//! (design §2 "One tick thread plus a socket loop", §5 row P1).
//!
//! P1 `observe` journals what the guard **would** do. So this module owns the two
//! things a report needs: the tick that turns a pressure reading into a decision,
//! and the reply documents a client reads. It compiles no act path — the only
//! write it can cause is a journal row, through [`crate::events`].
//!
//! ## Why the reply builders live here and not in `main`
//!
//! A standalone host answers `status` locally (no daemon), and a host with a
//! daemon answers it over the reader socket. Both must say the same thing, so the
//! documents are built once, in [`status_reply`] and [`candidates_reply`], and
//! both paths call them. `main` keeps only the argv plumbing.
//!
//! ## What one tick does, and what it deliberately does not
//!
//! 1. read the host's memory `full` row ([`crate::psi::Poller`]);
//! 2. feed it to the hysteresis machine ([`crate::hysteresis`]);
//! 3. when the trigger *fires*, ask [`crate::report::choose_victim`] which unit it
//!    would park, and journal one row (`would-freeze` or a named hold);
//! 4. when the machine leaves parking (release or unreadable pressure), journal
//!    one `would-thaw` row;
//! 5. expire silent registrations and journal one `expired` row each.
//!
//! Rows are written on **transitions**, not on every tick: a journal that repeats
//! "would-freeze the same unit" every 5 s says nothing a reader can use, and a
//! 1 MiB rotating file would drown the rows that matter. The current decision
//! (including its reason word) is always readable from `status`/`candidates`.
//!
//! Nothing is ever parked in this build, so [`ParkingState::parked`] is always 0.
//! It is never faked: the planner is asked with the state that is true, which is
//! why the `thaw-grace` reason is reachable only through the planner's own tests.

use std::io;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use crate::clock::Clock;
use crate::config::{Config, Mode};
use crate::events::{self, Event, Journal};
use crate::hysteresis::{Action, Decision, Hysteresis};
use crate::json::Value;
use crate::psi::{Poller, Reading};
use crate::report::{self, ParkingState, Would};
use crate::socket::{self, Role};
use crate::units::{self, Priority, Registration, RegistrationSource, Store};
use crate::wire;
use crate::{EVENT_VERSION, STATE_VERSION};

/// How many client sessions may run at once before a new connection is refused.
///
/// A session handler is one request and one reply, but a peer that connects and
/// sends nothing holds its thread until the read timeout, so an unbounded accept
/// loop would be a trivial local thread exhaustion. The bound is a fact of this
/// daemon, not a host setting: a host with more than 64 simultaneous thrashguard
/// clients has a different problem.
pub const MAX_SESSIONS: usize = 64;

/// Maximum wait for one underlying socket read while a request line is arriving.
/// This is not an absolute line deadline: progress resets the kernel's read wait.
pub const SESSION_READ_TIMEOUT: Duration = Duration::from_secs(30);

/// How long one client may take to receive its reply before its thread is dropped.
pub const SESSION_WRITE_TIMEOUT: Duration = Duration::from_secs(30);

/// The guard's state: the configuration, the candidate set, the machine and the
/// journal. One instance is shared by the tick thread and every session.
#[derive(Debug)]
pub struct Core {
    config: Config,
    clock: Arc<dyn Clock>,
    store: Store,
    hysteresis: Hysteresis,
    poller: Poller,
    journal: Journal,
    cgroup_root: PathBuf,
    last_reading: Reading,
    last_would: Would,
}

impl Core {
    /// A guard over `config`, reading `pressure_path`, resolving scopes under
    /// `cgroup_root` and journaling into `config.state_dir`.
    ///
    /// The [`Journal`] constructor creates `state_dir` (it is `/run` tmpfs in
    /// production, recreated every boot, and its own tmpfiles entry owns the
    /// mode). `[[static_unit]]` entries are resolved here, so a host whose `/etc`
    /// names a cgroup that is absent hears about it at start rather than never.
    pub fn new(
        config: Config,
        clock: Arc<dyn Clock>,
        pressure_path: &Path,
        cgroup_root: &Path,
        boot_id: &str,
    ) -> Result<Self, String> {
        let hysteresis = Hysteresis::new(
            config.trigger_avg10,
            config.release_avg10(),
            config.hold_s,
            config.release_s,
        );
        let now = clock.mono_ns();
        let store = store_for(&config, cgroup_root, now)?;
        let journal = Journal::new(config.state_dir.clone(), boot_id)
            .map_err(|error| format!("cannot open the journal: {error}"))?;
        Ok(Self {
            poller: Poller::new(pressure_path),
            hysteresis,
            store,
            journal,
            cgroup_root: cgroup_root.to_path_buf(),
            clock,
            last_reading: Reading::Unknown(crate::psi::Unreadable::Malformed(
                "no tick has run yet".to_string(),
            )),
            last_would: Would::Hold {
                why: report::Why::NothingToDo,
            },
            config,
        })
    }

    /// The journal this guard appends to.
    pub fn journal(&self) -> &Journal {
        &self.journal
    }

    /// The configuration this guard runs with.
    pub fn config(&self) -> &Config {
        &self.config
    }

    /// The candidate set.
    pub fn store(&self) -> &Store {
        &self.store
    }

    /// The decision the last tick reached.
    pub fn last_would(&self) -> &Would {
        &self.last_would
    }

    /// The reading the last tick saw (or a named "no tick yet" fact).
    pub fn last_reading(&self) -> &Reading {
        &self.last_reading
    }

    /// Seconds the guard has been in parking mode, 0 when it is not.
    pub fn parked_s(&self) -> u64 {
        if !self.hysteresis.is_parking() {
            return 0;
        }
        let now = self.clock.mono_ns();
        match self.hysteresis.parking_since_ns() {
            Some(since) => crate::clock::seconds_of_ns(now.saturating_sub(since)),
            None => 0,
        }
    }

    /// One tick: read pressure, decide, journal a transition, age the candidates.
    ///
    /// Returns the action the machine demanded, so a caller (a test, or a future
    /// metric) sees the machine's own answer without re-deriving it.
    pub fn tick(&mut self) -> Action {
        let now = self.clock.mono_ns();
        let reading = self.poller.poll();
        self.last_reading = reading.clone();
        let mode = self.config.mode.as_str();
        let boot_grace_ns = self.config.boot_grace_s.saturating_mul(1_000_000_000);
        let decision = if now < boot_grace_ns && reading.avg10().is_some() {
            Decision {
                phase: self.hysteresis.phase(),
                parking: self.hysteresis.is_parking(),
                action: Action::None,
            }
        } else {
            self.hysteresis.observe(&reading, now)
        };
        let state = ParkingState {
            // P1 compiles no act path: nothing is parked, ever. Asking the planner
            // with an invented `parked` count would be the silent-wrong-answer shape.
            parked: 0,
            candidates: self.store.len(),
        };
        match decision.action {
            Action::Freeze => {
                let would = report::choose_victim(&self.config, &self.store, state, now, &|_| None);
                let event = match &would {
                    Would::Park {
                        unit,
                        cgroup,
                        cgroup_id,
                        rule,
                        ..
                    } => Event::decision("would-freeze", "pressure", mode, now)
                        .with_scope(Some(unit), Some(cgroup), Some(*cgroup_id))
                        .with_detail(&format!("victim rule: {}", rule.as_str())),
                    Would::Hold { why } => Event::decision("would-hold", why.as_str(), mode, now)
                        .with_detail("the trigger held, and no unit could be chosen this tick"),
                    Would::Thaw { unit, cgroup, .. } => {
                        Event::decision("would-thaw", "pressure", mode, now).with_scope(
                            Some(unit),
                            Some(cgroup),
                            None,
                        )
                    }
                };
                self.write(event);
                self.last_would = would;
            }
            Action::Thaw => {
                // Nothing is frozen in P1, so the row names no unit: it records that
                // the machine left parking mode, which is the fact a reader needs.
                let event = Event::decision("would-thaw", "pressure-released", mode, now)
                    .with_detail("simulated: the observe cut froze nothing to thaw");
                self.write(event);
                self.last_would = Would::Hold {
                    why: report::Why::NothingToDo,
                };
            }
            Action::Unknown => {
                let detail = match &reading {
                    Reading::Unknown(reason) => format!("{}: {}", reason.reason(), reason.detail()),
                    Reading::Known(_) => "unknown".to_string(),
                };
                self.write(
                    Event::decision("unknown-pressure", "unreadable_psi", mode, now)
                        .with_detail(&detail),
                );
            }
            Action::None => {
                if !self.hysteresis.is_parking() {
                    self.last_would =
                        report::choose_victim(&self.config, &self.store, state, now, &|_| None);
                }
            }
        }
        for expired in self.store.expire(now) {
            self.write(
                Event::decision("expired", "aged_out", mode, now)
                    .with_scope(
                        Some(&expired.id),
                        Some(&expired.scope.name),
                        Some(expired.scope.cgroup_id),
                    )
                    .with_detail(&format!(
                        "{} s of silence, ttl_s = {}",
                        expired.silent_s(now),
                        self.store.ttl_s()
                    )),
            );
        }
        decision.action
    }

    /// Append one row, reporting a failure on stderr rather than aborting the loop.
    ///
    /// A journal that cannot be written must not stop the guard observing, and it
    /// must not be silent either: `status` would otherwise look healthy while the
    /// crash record stays empty. This is the one place the daemon speaks to stderr,
    /// and it never invents a row it could not write.
    fn write(&mut self, event: Event) {
        if let Err(error) = self.journal.append(&event) {
            eprintln!(
                "thrashguard: cannot append the {} row: {error}",
                event.event
            );
        }
    }
}

/// The store for `config` with every `[[static_unit]]` resolved.
///
/// Public because the standalone verbs build the same candidate set without a
/// daemon: one resolver, one candidate shape, two callers (design §6d).
pub fn store_for(config: &Config, root: &Path, now_ns: u64) -> Result<Store, String> {
    let mut store = Store::from_config(config);
    for unit in &config.static_units {
        let scope = units::resolve_static_unit(root, unit, config)
            .map_err(|refusal| format!("[[static_unit]] {}: {}", unit.name, refusal.detail()))?;
        store
            .register(Registration {
                id: unit.name.clone(),
                container: unit.cgroup.clone(),
                scope,
                score: 0,
                priority: Priority::Freezable,
                kind: "static".to_string(),
                source: RegistrationSource::Static,
                owner: "/etc".to_string(),
                run: unit.name.clone(),
                heartbeat_s: None,
                registered_seq: 0,
                last_heartbeat_ns: now_ns,
            })
            .map_err(|error| error.to_string())?;
    }
    Ok(store)
}

impl socket::Dispatch for Core {
    fn respond(&mut self, _role: Role, request: wire::Request) -> Value {
        let now = self.clock.mono_ns();
        let mode = self.config.mode.as_str();
        match request {
            wire::Request::Register(body) => {
                let scope = match units::resolve_container(
                    &self.cgroup_root,
                    &body.container,
                    &self.config,
                ) {
                    Ok(scope) => scope,
                    Err(refusal) => {
                        self.write(
                            Event::decision("registration-refused", refusal.reason(), mode, now)
                                .with_detail(&refusal.detail()),
                        );
                        return crate::wire::refused(
                            Some(&body.id),
                            refusal.reason(),
                            &refusal.detail(),
                        );
                    }
                };
                let cgroup = scope.name.clone();
                let cgroup_id = scope.cgroup_id;
                let registration = Registration {
                    id: body.id.clone(),
                    container: body.container.clone(),
                    scope,
                    score: body.score,
                    priority: body.priority,
                    kind: body.kind.clone(),
                    source: RegistrationSource::Registrant,
                    owner: body.owner.clone(),
                    run: body.run.clone(),
                    heartbeat_s: body.heartbeat_s,
                    registered_seq: 0,
                    last_heartbeat_ns: now,
                };
                match self.store.register(registration) {
                    Ok(stored) => {
                        self.write(
                            Event::decision("registered", "registered", mode, now)
                                .with_scope(Some(&stored.id), Some(&cgroup), Some(cgroup_id))
                                .with_shadowed(&stored.scope.shadowed),
                        );
                        crate::wire::register_ok(&body.id, &cgroup, cgroup_id, self.store.ttl_s())
                    }
                    Err(error) => {
                        crate::wire::refused(Some(&body.id), error.reason(), &error.to_string())
                    }
                }
            }
            wire::Request::Update {
                id,
                score,
                priority,
            } => match self.store.update(&id, score, priority) {
                Ok(found) => crate::wire::update_ok(&id, found.score, found.priority),
                Err(error) => crate::wire::refused(Some(&id), error.reason(), &error.to_string()),
            },
            wire::Request::Heartbeat { id } => match self.store.heartbeat(&id, now) {
                Ok(_) => crate::wire::ok_id(&id),
                Err(error) => crate::wire::refused(Some(&id), error.reason(), &error.to_string()),
            },
            wire::Request::Deregister { id } => match self.store.deregister(&id) {
                Ok(found) => {
                    self.write(
                        Event::decision("deregistered", "deregistered", mode, now).with_scope(
                            Some(&found.id),
                            Some(&found.scope.name),
                            Some(found.scope.cgroup_id),
                        ),
                    );
                    crate::wire::ok_id(&id)
                }
                Err(error) => crate::wire::refused(Some(&id), error.reason(), &error.to_string()),
            },
            wire::Request::Status => self.status_document(),
            wire::Request::Candidates => self.candidates_document(),
            wire::Request::Events { since_seq } => {
                let (rows, truncated) = self.journal.rows_since(since_seq);
                bounded_events_reply(self.journal.boot_id(), rows, truncated)
            }
            wire::Request::Version => crate::wire::version(),
        }
    }
}

impl Core {
    /// This guard's `status` document.
    pub fn status_document(&self) -> Value {
        status_reply(
            &self.config,
            &self.last_reading,
            &self.store,
            &self.last_would,
            self.parked_s(),
        )
    }

    /// This guard's `candidates` document.
    pub fn candidates_document(&self) -> Value {
        candidates_reply(
            &self.config,
            &self.store,
            &self.last_would,
            self.clock.mono_ns(),
        )
    }
}

/// `status` as the wire document: mode, pressure (or its unknown reason), the
/// candidate count, the next decision and an empty frozen set (nothing is frozen
/// in P1 — `frozen[]` is the P2 fact and is reported as empty, never invented).
pub fn status_reply(
    config: &Config,
    reading: &Reading,
    store: &Store,
    would: &Would,
    parked_s: u64,
) -> Value {
    let psi = match reading {
        Reading::Known(pressure) => Value::Object(vec![
            (
                "v".to_string(),
                Value::Integer(i64::from(pressure.full.avg10)),
            ),
            (
                "some".to_string(),
                Value::Integer(i64::from(pressure.some.avg10)),
            ),
            (
                "full".to_string(),
                Value::Integer(i64::from(pressure.full.avg10)),
            ),
            (
                "total_us".to_string(),
                Value::Integer(integer(pressure.full.total_us)),
            ),
        ]),
        Reading::Unknown(why) => Value::Object(vec![
            ("v".to_string(), Value::Null),
            (
                "reason".to_string(),
                Value::String(why.reason().to_string()),
            ),
            ("detail".to_string(), Value::String(why.detail())),
        ]),
    };
    let mut body = vec![
        (
            "mode".to_string(),
            Value::String(config.mode.as_str().to_string()),
        ),
        (
            "enforce_refused".to_string(),
            Value::Bool(config.mode == Mode::Enforce),
        ),
        ("psi".to_string(), psi),
        ("candidates".to_string(), Value::Integer(store.len() as i64)),
        ("frozen".to_string(), Value::Array(Vec::new())),
        ("parked_s".to_string(), Value::Integer(integer(parked_s))),
        ("desync".to_string(), Value::Array(Vec::new())),
        ("ttl_s".to_string(), Value::Integer(integer(config.ttl_s()))),
        ("would".to_string(), would_value(would)),
    ];
    let (rule, unknown) = report::effective_rule(config);
    body.push(("rule".to_string(), Value::String(rule.as_str().to_string())));
    if let Some(reason) = unknown {
        body.push(("rule_reason".to_string(), Value::String(reason.to_string())));
    }
    crate::wire::ok(STATE_VERSION, body)
}

/// `candidates` as the wire document: the next tick's order and the unit it implies.
pub fn candidates_reply(config: &Config, store: &Store, would: &Would, now_ns: u64) -> Value {
    let mut rows = Vec::new();
    for candidate in store.newest_first() {
        rows.push(crate::wire::candidate_row(
            &candidate.id,
            &candidate.run,
            &candidate.scope.name,
            candidate.scope.cgroup_id,
            candidate.score,
            candidate.priority,
            candidate.silent_s(now_ns),
        ));
    }
    let mut body = vec![
        (
            "mode".to_string(),
            Value::String(config.mode.as_str().to_string()),
        ),
        (
            "rule".to_string(),
            Value::String(report::effective_rule(config).0.as_str().to_string()),
        ),
        ("order".to_string(), Value::Array(rows)),
        ("would".to_string(), would_value(would)),
    ];
    if let (_, Some(unknown)) = report::effective_rule(config) {
        body.push((
            "rule_reason".to_string(),
            Value::String(unknown.to_string()),
        ));
    }
    crate::wire::ok(STATE_VERSION, body)
}

/// `events` as the wire document.
pub fn events_reply(boot_id: &str, rows: Vec<Value>, truncated: bool) -> Value {
    crate::wire::ok(
        EVENT_VERSION,
        vec![
            ("boot_id".to_string(), Value::String(boot_id.to_string())),
            ("rows".to_string(), Value::Array(rows)),
            ("truncated".to_string(), Value::Bool(truncated)),
        ],
    )
}

/// Build the same bounded page for the daemon socket and the local CLI reader.
/// Rows omitted by either the row-count or encoded-byte ceiling make the page
/// explicitly incomplete.
pub fn bounded_events_reply(boot_id: &str, rows: &[Value], truncated: bool) -> Value {
    let mut page = Vec::new();
    let mut incomplete = truncated;
    for row in rows {
        if page.len() == crate::events::RECENT_ROWS {
            incomplete = true;
            break;
        }
        page.push(row.clone());
        if events_reply(boot_id, page.clone(), incomplete)
            .to_json()
            .len()
            > crate::json::MAX_DOCUMENT_BYTES
        {
            page.pop();
            incomplete = true;
            break;
        }
    }
    events_reply(boot_id, page, incomplete)
}

/// One decision as a document, with `simulated: true` — the marker that tells a
/// reader nothing was frozen (P1 has no interval to record).
pub fn would_value(would: &Would) -> Value {
    match would {
        Would::Park {
            id,
            unit,
            cgroup,
            cgroup_id,
            rule,
        } => Value::Object(vec![
            (
                "action".to_string(),
                Value::String("would-freeze".to_string()),
            ),
            ("id".to_string(), Value::String(id.clone())),
            ("unit".to_string(), Value::String(unit.clone())),
            ("cgroup".to_string(), Value::String(cgroup.clone())),
            ("cgroup_id".to_string(), Value::Integer(integer(*cgroup_id))),
            ("rule".to_string(), Value::String(rule.as_str().to_string())),
            ("simulated".to_string(), Value::Bool(true)),
        ]),
        Would::Hold { why } => Value::Object(vec![
            (
                "action".to_string(),
                Value::String("would-hold".to_string()),
            ),
            ("why".to_string(), Value::String(why.as_str().to_string())),
            ("simulated".to_string(), Value::Bool(true)),
        ]),
        Would::Thaw { id, unit, cgroup } => Value::Object(vec![
            (
                "action".to_string(),
                Value::String("would-thaw".to_string()),
            ),
            ("id".to_string(), Value::String(id.clone())),
            ("unit".to_string(), Value::String(unit.clone())),
            ("cgroup".to_string(), Value::String(cgroup.clone())),
            ("simulated".to_string(), Value::Bool(true)),
        ]),
    }
}

fn integer(value: u64) -> i64 {
    i64::try_from(value).unwrap_or(i64::MAX)
}

/// The production boot id, or `"unknown"` when the host's file is unreadable.
///
/// Two intervals must not be joined across a reboot (design §4), so an absent id
/// is reported as `unknown` rather than invented.
pub fn boot_id() -> String {
    events::read_boot_id(Path::new(events::BOOT_ID_PATH)).unwrap_or_else(|| "unknown".to_string())
}

/// Bind both paths directly: a foreground run under an operator's terminal, and
/// the shape every test uses.
///
/// A bind that fails because something else is listening is loud; it is never
/// "fixed" by deleting another process's socket.
pub fn bind(registrant: Option<&Path>, reader: &Path) -> Result<Endpoint, String> {
    Ok(Endpoint {
        registrant: registrant.map(bind_one).transpose()?,
        reader: bind_one(reader)?,
    })
}

fn bind_one(path: &Path) -> Result<std::os::unix::net::UnixListener, String> {
    if let Some(parent) = path.parent() {
        // `create_dir_all` writes no file and owns no byte, so it is not part of
        // the lint boundary's write list. In production the socket directories
        // come from `deploy/tmpfiles.d/thrashguard.conf` with their group and mode.
        std::fs::create_dir_all(parent)
            .map_err(|error| format!("cannot create {}: {error}", parent.display()))?;
    }
    std::os::unix::net::UnixListener::bind(path)
        .map_err(|error| {
            format!(
                "cannot bind {}: {error}; if this is a stale socket, confirm no daemon owns it, then remove that exact socket path and retry",
                path.display()
            )
        })
}

/// The two listeners this daemon serves.
#[derive(Debug)]
pub struct Endpoint {
    /// The registrant socket, absent when `registrant = false`.
    pub registrant: Option<std::os::unix::net::UnixListener>,
    /// The reader socket.
    pub reader: std::os::unix::net::UnixListener,
}

/// Serve the two sockets and run the tick loop until the process is stopped.
///
/// Two kinds of thread, deliberately: the tick thread owns the machine, and each
/// accepted client gets one bounded session. A client that connects and says
/// nothing blocks only its own handler, until one read reaches
/// [`SESSION_READ_TIMEOUT`]. A peer that keeps making progress can occupy one of
/// the [`MAX_SESSIONS`] slots until it finishes its line (TG-14).
pub fn serve(core: Core, endpoints: &Endpoint, tick_s: u64) -> io::Result<()> {
    let shared = Arc::new(Mutex::new(core));
    let ticker = Arc::clone(&shared);
    std::thread::spawn(move || loop {
        std::thread::sleep(Duration::from_secs(tick_s));
        let mut guard = ticker.lock().unwrap_or_else(|poison| poison.into_inner());
        guard.tick();
    });
    if let Some(registrant) = &endpoints.registrant {
        registrant.set_nonblocking(true)?;
    }
    endpoints.reader.set_nonblocking(true)?;
    let sessions = Arc::new(AtomicUsize::new(0));
    loop {
        let registrant = endpoints
            .registrant
            .iter()
            .map(|listener| (Role::Registrant, listener));
        for (role, listener) in registrant.chain(std::iter::once((Role::Reader, &endpoints.reader)))
        {
            let accepted = match accept_with_write_timeout(listener) {
                Ok(stream) => stream,
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => continue,
                Err(error) => return Err(error),
            };
            if sessions.load(Ordering::SeqCst) >= MAX_SESSIONS {
                let mut refused = socket::Session::new(std::io::empty(), accepted, role);
                let _ = refused.write_reply(&crate::wire::refused_reader(
                    role.version(),
                    "too-many-sessions",
                    &format!("{MAX_SESSIONS} sessions are already open"),
                ));
                continue;
            }
            sessions.fetch_add(1, Ordering::SeqCst);
            let core = Arc::clone(&shared);
            let counted = Arc::clone(&sessions);
            std::thread::spawn(move || {
                let outcome = handle_session(role, accepted, &core);
                counted.fetch_sub(1, Ordering::SeqCst);
                if let Err(error) = outcome {
                    eprintln!("thrashguard: session failed: {error}");
                }
            });
        }
        // The pause only bounds the accept loop's spin; nothing waits on a client,
        // and no test depends on this number.
        std::thread::sleep(Duration::from_millis(20));
    }
}

/// Accept one client and install the write bound before either refusal or spawn.
fn accept_with_write_timeout(
    listener: &std::os::unix::net::UnixListener,
) -> io::Result<std::os::unix::net::UnixStream> {
    let (stream, _) = listener.accept()?;
    stream.set_write_timeout(Some(SESSION_WRITE_TIMEOUT))?;
    Ok(stream)
}

/// Serve one accepted connection: one request line, one reply, then close.
fn handle_session(
    role: Role,
    stream: std::os::unix::net::UnixStream,
    core: &Arc<Mutex<Core>>,
) -> io::Result<()> {
    let peer = stream.try_clone()?;
    stream.set_read_timeout(Some(SESSION_READ_TIMEOUT))?;
    let mut session = socket::Session::new(stream, peer, role);
    match session.read_request()? {
        None => Ok(()),
        Some(line) => answer_then_write(core, role, &line, |reply| session.write_reply(reply)),
    }
}

/// Hold the core only while building the reply; a slow peer cannot hold the
/// tick mutex while `write_reply` waits for socket capacity.
fn answer_then_write(
    core: &Arc<Mutex<Core>>,
    role: Role,
    line: &str,
    write: impl FnOnce(&Value) -> io::Result<()>,
) -> io::Result<()> {
    let reply = {
        let mut guard = core.lock().unwrap_or_else(|poison| poison.into_inner());
        socket::answer(&mut *guard, role, line)
    };
    write(&reply)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::clock::ManualClock;
    use crate::config::StaticUnit;
    use crate::testutil::TempTree;
    use std::io::Write;
    use std::os::unix::net::{UnixListener, UnixStream};

    fn static_unit(name: &str, cgroup: &str) -> StaticUnit {
        StaticUnit {
            name: name.to_string(),
            cgroup: cgroup.to_string(),
        }
    }

    /// A pressure document with the given `full`/`some` `avg10` (hundredths).
    fn pressure_text(avg10: &str, total: u64) -> String {
        format!(
            "some avg10={avg10} avg60=0.00 avg300=0.00 total={total}\n\
             full avg10={avg10} avg60=0.00 avg300=0.00 total={total}\n"
        )
    }

    fn journal_rows(core: &Core) -> Vec<Value> {
        let text = std::fs::read_to_string(core.journal().file()).unwrap_or_default();
        text.lines()
            .filter(|line| !line.trim().is_empty())
            .map(|line| Value::parse(line).expect("a journal line is JSON"))
            .collect()
    }

    /// A guard over a temp tree: every path (including the journal's `state_dir`)
    /// points inside it, so no test writes to `/run` and none reads `/sys/fs/cgroup`.
    fn guard(
        tree: &TempTree,
        mut config: Config,
        clock: Arc<ManualClock>,
        pressure: &Path,
    ) -> Core {
        config.boot_grace_s = 0;
        config.state_dir = tree.at("run");
        Core::new(config, clock, pressure, tree.path(), "boot-test").expect("a guard over the tree")
    }

    fn two_scopes(tree: &TempTree, config: &mut Config) {
        for scope in ["a", "b"] {
            std::fs::create_dir_all(tree.at(&format!("dev-gates.slice/{scope}.scope")))
                .expect("a scope");
            config.static_units.push(static_unit(
                scope,
                &format!("dev-gates.slice/{scope}.scope"),
            ));
        }
    }

    #[test]
    fn one_trigger_journals_exactly_one_decision_row_and_never_a_flood() {
        let tree = TempTree::new("daemon-freeze");
        let mut config = Config::defaults();
        two_scopes(&tree, &mut config);
        let clock = Arc::new(ManualClock::new(1));
        let pressure = tree.write("memory.pressure", &pressure_text("4.00", 1));
        let mut core = guard(&tree, config, clock.clone(), &pressure);

        // t=0: armed, no row — a hold in progress is not a decision.
        assert_eq!(core.tick(), Action::None);
        assert!(journal_rows(&core).is_empty(), "{:?}", journal_rows(&core));
        // t=10 s (the default hold): the trigger fires and one unit is chosen.
        clock.advance_s(10);
        assert_eq!(core.tick(), Action::Freeze);
        let rows = journal_rows(&core);
        assert_eq!(rows.len(), 1, "{rows:?}");
        assert_eq!(
            rows[0].get("event").and_then(Value::as_str),
            Some("would-freeze")
        );
        assert_eq!(
            rows[0].get("simulated").and_then(Value::as_bool),
            Some(true),
            "P1 froze nothing, and the row says so"
        );
        assert_eq!(rows[0].get("seconds").and_then(Value::as_i64), Some(0));
        assert_eq!(rows[0].get("unit").and_then(Value::as_str), Some("b"));
        assert_eq!(rows[0].get("seq").and_then(Value::as_i64), Some(1));
        assert_eq!(rows[0].get("mode").and_then(Value::as_str), Some("report"));
        // The decision `status` reports is the one that was journalled.
        let Would::Park {
            id,
            unit,
            cgroup,
            cgroup_id,
            rule,
        } = core.last_would()
        else {
            panic!("{:?}", core.last_would())
        };
        assert_eq!(id, "b");
        assert_eq!(unit, "b");
        assert_eq!(cgroup, "b.scope");
        assert!(*cgroup_id > 0, "the scope's inode is the guard's own fact");
        assert_eq!(*rule, crate::config::VictimRule::Newest);
        // A second high tick is not a second freeze: the machine already parks.
        clock.advance_s(5);
        assert_eq!(core.tick(), Action::None);
        assert_eq!(journal_rows(&core).len(), 1, "transitions only");
    }

    #[test]
    fn a_release_journals_one_thaw_row_that_names_no_unit() {
        let tree = TempTree::new("daemon-thaw");
        let mut config = Config::defaults();
        config.hold_s = 0;
        config.release_s = 0;
        two_scopes(&tree, &mut config);
        let clock = Arc::new(ManualClock::new(1));
        let pressure = tree.write("memory.pressure", &pressure_text("4.00", 1));
        let mut core = guard(&tree, config, clock.clone(), &pressure);
        assert_eq!(core.tick(), Action::Freeze);
        let frozen = journal_rows(&core).len();
        // Rewrite the document: the poller's reset check compares totals, so a
        // release keeps the monotonic `total` growing (a real kernel does).
        tree.write("memory.pressure", &pressure_text("0.00", 1));
        assert_eq!(core.tick(), Action::Thaw);
        let rows = journal_rows(&core);
        assert_eq!(rows.len(), frozen + 1, "{rows:?}");
        let last = rows.last().expect("a row");
        assert_eq!(
            last.get("event").and_then(Value::as_str),
            Some("would-thaw")
        );
        assert_eq!(
            last.get("reason").and_then(Value::as_str),
            Some("pressure-released")
        );
        assert_eq!(
            last.get("unit").and_then(Value::as_str),
            None,
            "nothing was frozen, so no unit may be named"
        );
        assert_eq!(core.parked_s(), 0);
        // A calm reading on a non-parking machine is not a thaw.
        tree.write("memory.pressure", &pressure_text("0.00", 2));
        assert_eq!(core.tick(), Action::None);
        assert_eq!(journal_rows(&core).len(), frozen + 1);
    }

    #[test]
    fn unreadable_pressure_is_one_unknown_row_and_never_a_zero() {
        let tree = TempTree::new("daemon-unknown");
        let clock = Arc::new(ManualClock::new(1));
        let mut core = guard(&tree, Config::defaults(), clock, &tree.at("gone.pressure"));
        assert_eq!(core.tick(), Action::Unknown);
        let rows = journal_rows(&core);
        assert_eq!(rows.len(), 1);
        assert_eq!(
            rows[0].get("event").and_then(Value::as_str),
            Some("unknown-pressure")
        );
        assert_eq!(
            rows[0].get("reason").and_then(Value::as_str),
            Some("unreadable_psi")
        );
        assert!(rows[0]
            .get("detail")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .contains("not present"));
        assert_eq!(
            core.status_document()
                .get("psi")
                .and_then(|psi| psi.get("v")),
            Some(&Value::Null),
            "unknown is not 0.00"
        );
    }

    #[test]
    fn a_registration_is_resolved_stored_journalled_and_answered() {
        let tree = TempTree::new("daemon-register");
        std::fs::create_dir_all(tree.at("dev.slice/dev-gates.slice/docker-abc.scope"))
            .expect("scope");
        std::fs::create_dir_all(tree.at("dev.slice/dev-gates.slice/rg-abc.slice/docker-abc.scope"))
            .expect("shadowed nested scope");
        let clock = Arc::new(ManualClock::new(1));
        let mut core = guard(
            &tree,
            Config::defaults(),
            clock,
            &tree.at("absent.pressure"),
        );
        let register = format!(
            "{{\"v\":\"{}\",\"verb\":\"register\",\"id\":\"rg-1\",\"container\":\"abc\",\"run\":\"selftest\"}}",
            crate::REGISTRATION_VERSION
        );
        let reply = socket::answer(&mut core, Role::Registrant, &register);
        assert_eq!(reply.get("ok").and_then(Value::as_bool), Some(true));
        assert_eq!(
            reply.get("cgroup").and_then(Value::as_str),
            Some("docker-abc.scope")
        );
        assert_eq!(
            reply.get("ttl_s").and_then(Value::as_i64),
            Some(90),
            "derived: missed_heartbeats x heartbeat_s"
        );
        assert!(
            reply.get("cgroup_id").and_then(Value::as_i64).unwrap_or(0) > 0,
            "the scope's inode is the guard's own fact"
        );
        let rows = journal_rows(&core);
        assert_eq!(
            rows[0].get("event").and_then(Value::as_str),
            Some("registered")
        );
        assert_eq!(rows[0].get("unit").and_then(Value::as_str), Some("rg-1"));
        let shadowed: Vec<&str> = rows[0]
            .get("shadowed")
            .and_then(Value::as_array)
            .expect("registered event records shadowed matches")
            .iter()
            .filter_map(Value::as_str)
            .collect();
        assert_eq!(
            shadowed,
            vec!["dev.slice/dev-gates.slice/rg-abc.slice/docker-abc.scope"]
        );

        // A second registration with the same id is refused by name.
        let again = socket::answer(&mut core, Role::Registrant, &register);
        assert_eq!(again.get("ok").and_then(Value::as_bool), Some(false));
        assert_eq!(
            again.get("reason").and_then(Value::as_str),
            Some("duplicate")
        );

        // A container that resolves to nothing is refused, and the refusal is journalled.
        let missing = format!(
            "{{\"v\":\"{}\",\"verb\":\"register\",\"id\":\"rg-2\",\"container\":\"nosuch\"}}",
            crate::REGISTRATION_VERSION
        );
        let reply = socket::answer(&mut core, Role::Registrant, &missing);
        assert_eq!(reply.get("reason").and_then(Value::as_str), Some("none"));
        let rows = journal_rows(&core);
        assert_eq!(
            rows.last()
                .and_then(|row| row.get("event"))
                .and_then(Value::as_str),
            Some("registration-refused")
        );
        assert_eq!(
            core.store().len(),
            1,
            "a refused registration stores nothing"
        );
    }

    #[test]
    fn heartbeat_update_and_expiry_keep_the_candidate_set_honest() {
        let tree = TempTree::new("daemon-expiry");
        std::fs::create_dir_all(tree.at("dev.slice/dev-gates.slice/docker-abc.scope"))
            .expect("scope");
        let clock = Arc::new(ManualClock::new(1));
        let mut config = Config::defaults();
        config.heartbeat_s = 30;
        config.missed_heartbeats = 3;
        let mut core = guard(&tree, config, clock.clone(), &tree.at("absent.pressure"));
        let registration = |verb: &str, extra: &str| {
            format!(
                "{{\"v\":\"{}\",\"verb\":\"{verb}\",\"id\":\"rg-1\"{extra}}}",
                crate::REGISTRATION_VERSION
            )
        };
        assert_eq!(
            socket::answer(
                &mut core,
                Role::Registrant,
                &format!(
                    "{{\"v\":\"{}\",\"verb\":\"register\",\"id\":\"rg-1\",\"container\":\"abc\"}}",
                    crate::REGISTRATION_VERSION
                )
            )
            .get("ok")
            .and_then(Value::as_bool),
            Some(true)
        );
        let update = socket::answer(
            &mut core,
            Role::Registrant,
            &registration("update", ",\"score\":7,\"priority\":\"last-resort\""),
        );
        assert_eq!(update.get("ok").and_then(Value::as_bool), Some(true));
        assert_eq!(update.get("score").and_then(Value::as_i64), Some(7));
        assert_eq!(
            update.get("priority").and_then(Value::as_str),
            Some("last-resort")
        );
        // A heartbeat from an unknown id is refused, never created.
        let ghost = socket::answer(
            &mut core,
            Role::Registrant,
            &format!(
                "{{\"v\":\"{}\",\"verb\":\"heartbeat\",\"id\":\"nothing\"}}",
                crate::REGISTRATION_VERSION
            ),
        );
        assert_eq!(ghost.get("reason").and_then(Value::as_str), Some("missing"));
        clock.advance_s(90);
        assert_eq!(
            socket::answer(&mut core, Role::Registrant, &registration("heartbeat", ""))
                .get("ok")
                .and_then(Value::as_bool),
            Some(true),
            "at exactly ttl_s the heartbeat still finds the unit"
        );
        // 90 s after the last heartbeat the candidate silently expires.
        clock.advance_s(91);
        core.tick();
        let rows = journal_rows(&core);
        let last = rows.last().expect("a row");
        assert_eq!(last.get("event").and_then(Value::as_str), Some("expired"));
        assert_eq!(last.get("reason").and_then(Value::as_str), Some("aged_out"));
        assert_eq!(core.store().len(), 0);
        // `deregister` on a unit the expiry already removed is a named refusal.
        let deregister =
            socket::answer(&mut core, Role::Registrant, &registration("deregister", ""));
        assert_eq!(
            deregister.get("reason").and_then(Value::as_str),
            Some("missing")
        );
    }

    #[test]
    fn static_units_remain_candidates_after_multiple_heartbeat_ttls() {
        let tree = TempTree::new("daemon-static-lifetime");
        std::fs::create_dir_all(tree.at("dev-gates.slice/static.scope")).expect("static scope");
        let mut config = Config::defaults();
        config.static_units = vec![static_unit("static", "dev-gates.slice/static.scope")];
        let clock = Arc::new(ManualClock::new(1));
        let mut core = guard(
            &tree,
            config,
            Arc::clone(&clock),
            &tree.at("absent.pressure"),
        );
        let ttl_s = core.store().ttl_s();

        for _ in 0..2 {
            clock.advance_s(ttl_s + 1);
            core.tick();
            assert_eq!(core.store().len(), 1, "the configured candidate remains");
        }
        assert!(
            journal_rows(&core)
                .iter()
                .all(|row| row.get("event").and_then(Value::as_str) != Some("expired")),
            "a static unit has no heartbeat whose silence can expire"
        );
    }

    #[test]
    fn boot_grace_prevents_a_trigger_until_boot_relative_time_reaches_the_limit() {
        let tree = TempTree::new("daemon-boot-grace");
        let pressure = tree.write("memory.pressure", &pressure_text("3.50", 0));
        let mut config = Config::defaults();
        config.boot_grace_s = 60;
        config.hold_s = 0;
        config.state_dir = tree.at("run");
        let clock = Arc::new(ManualClock::at(59_000_000_000, 1_700_000_000_000_000_000));
        let mut core = Core::new(config, clock.clone(), &pressure, tree.path(), "boot-test")
            .expect("guard over the tree");

        assert_eq!(
            core.tick(),
            Action::None,
            "pressure is ignored during grace"
        );
        clock.advance_s(1);
        assert_eq!(core.tick(), Action::Freeze, "the threshold applies at 60 s");
    }

    #[test]
    fn deregister_removes_the_candidate_and_journals_the_unit() {
        let tree = TempTree::new("daemon-deregister");
        std::fs::create_dir_all(tree.at("dev.slice/dev-gates.slice/docker-abc.scope"))
            .expect("scope");
        let clock = Arc::new(ManualClock::new(1));
        let mut core = guard(
            &tree,
            Config::defaults(),
            clock,
            &tree.at("absent.pressure"),
        );
        let register = format!(
            "{{\"v\":\"{}\",\"verb\":\"register\",\"id\":\"rg-1\",\"container\":\"abc\"}}",
            crate::REGISTRATION_VERSION
        );
        socket::answer(&mut core, Role::Registrant, &register);
        let deregister = format!(
            "{{\"v\":\"{}\",\"verb\":\"deregister\",\"id\":\"rg-1\"}}",
            crate::REGISTRATION_VERSION
        );
        let reply = socket::answer(&mut core, Role::Registrant, &deregister);
        assert_eq!(reply.get("ok").and_then(Value::as_bool), Some(true));
        let rows = journal_rows(&core);
        assert_eq!(
            rows.last()
                .and_then(|row| row.get("event"))
                .and_then(Value::as_str),
            Some("deregistered")
        );
        assert_eq!(
            rows.last()
                .and_then(|row| row.get("reason"))
                .and_then(Value::as_str),
            Some("deregistered")
        );
        assert_eq!(core.store().len(), 0);
    }

    #[test]
    fn restart_survives_a_maximum_registration_and_a_refused_oversized_request() {
        let tree = TempTree::new("daemon-max-registration-restart");
        let id = "i".repeat(crate::wire::MAX_ID_BYTES);
        let container = "c".repeat(crate::wire::MAX_CONTAINER_BYTES);
        std::fs::create_dir_all(tree.at(&format!(
            "dev.slice/dev-gates.slice/docker-{container}.scope"
        )))
        .expect("maximum-length scope component");
        let mut core = guard(
            &tree,
            Config::defaults(),
            Arc::new(ManualClock::new(1)),
            &tree.at("absent.pressure"),
        );
        let register = format!(
            "{{\"v\":\"{}\",\"verb\":\"register\",\"id\":\"{id}\",\"container\":\"{container}\"}}",
            crate::REGISTRATION_VERSION
        );
        let reply = socket::answer(&mut core, Role::Registrant, &register);
        assert_eq!(reply.get("ok").and_then(Value::as_bool), Some(true));
        let line = std::fs::read_to_string(core.journal().file()).expect("maximum row");
        assert!(line.len() <= crate::json::MAX_DOCUMENT_BYTES);
        drop(core);

        let mut restarted = guard(
            &tree,
            Config::defaults(),
            Arc::new(ManualClock::new(2)),
            &tree.at("absent.pressure"),
        );
        assert_eq!(
            restarted.journal().seq(),
            1,
            "the largest accepted request recovers"
        );
        let too_long_id = "i".repeat(crate::wire::MAX_ID_BYTES + 1);
        let oversized = format!(
            "{{\"v\":\"{}\",\"verb\":\"heartbeat\",\"id\":\"{too_long_id}\"}}",
            crate::REGISTRATION_VERSION
        );
        let refusal = socket::answer(&mut restarted, Role::Registrant, &oversized);
        assert_eq!(
            refusal.get("reason").and_then(Value::as_str),
            Some("too-large")
        );
        assert_eq!(
            restarted.journal().seq(),
            1,
            "the request adds no journal row"
        );
        drop(restarted);

        let after_refusal = guard(
            &tree,
            Config::defaults(),
            Arc::new(ManualClock::new(3)),
            &tree.at("absent.pressure"),
        );
        assert_eq!(
            after_refusal.journal().seq(),
            1,
            "restart also succeeds after refusing an oversized request"
        );
    }

    #[test]
    fn socket_events_reply_fits_the_document_and_marks_rows_outside_its_page() {
        let tree = TempTree::new("daemon-events-reply-bound");
        let mut core = guard(
            &tree,
            Config::defaults(),
            Arc::new(ManualClock::new(1)),
            &tree.at("absent.pressure"),
        );
        let detail = "x".repeat(700);
        for at in 0..200 {
            core.journal
                .append(
                    &Event::decision("would-freeze", "pressure", "report", at).with_detail(&detail),
                )
                .expect("append event row");
        }
        let reply = socket::answer(
            &mut core,
            Role::Reader,
            &format!("{{\"v\":\"{EVENT_VERSION}\",\"verb\":\"events\",\"since_seq\":0}}"),
        );
        let text = reply.to_json();
        assert!(text.len() <= crate::json::MAX_DOCUMENT_BYTES);
        assert!(
            Value::parse(&text).is_ok(),
            "the client document parser accepts it"
        );
        assert_eq!(reply.get("truncated").and_then(Value::as_bool), Some(true));
        assert!(
            reply.get("rows").and_then(Value::as_array).unwrap().len() < 200,
            "the reply is byte-paged rather than returning the entire ring"
        );
    }

    #[test]
    fn the_reader_documents_report_the_guard_state_and_an_empty_frozen_set() {
        let tree = TempTree::new("daemon-reader");
        let mut config = Config::defaults();
        two_scopes(&tree, &mut config);
        let clock = Arc::new(ManualClock::new(1));
        let pressure = tree.write("memory.pressure", &pressure_text("4.00", 1));
        let mut core = guard(&tree, config, clock.clone(), &pressure);
        // Armed first (the hold is continuous), so the release of the next tick is
        // a real transition rather than a first-ever sighting.
        assert_eq!(core.tick(), Action::None);
        let status = socket::answer(
            &mut core,
            Role::Reader,
            &format!("{{\"v\":\"{}\",\"verb\":\"status\"}}", STATE_VERSION),
        );
        assert_eq!(status.get("mode").and_then(Value::as_str), Some("report"));
        assert_eq!(status.get("candidates").and_then(Value::as_i64), Some(2));
        assert_eq!(
            status
                .get("frozen")
                .and_then(Value::as_array)
                .map(<[_]>::len),
            Some(0),
            "P1 freezes nothing, so `frozen[]` is empty and never invented"
        );
        assert_eq!(
            status
                .get("desync")
                .and_then(Value::as_array)
                .map(<[_]>::len),
            Some(0)
        );
        let candidates = socket::answer(
            &mut core,
            Role::Reader,
            &format!("{{\"v\":\"{}\",\"verb\":\"candidates\"}}", STATE_VERSION),
        );
        assert_eq!(
            candidates
                .get("order")
                .and_then(Value::as_array)
                .map(<[_]>::len),
            Some(2)
        );
        assert_eq!(
            candidates.get("rule").and_then(Value::as_str),
            Some("newest")
        );
        assert_eq!(
            candidates
                .get("would")
                .and_then(|would| would.get("simulated"))
                .and_then(Value::as_bool),
            Some(true)
        );
        // The journal rows are served from the daemon's own ring, not a file read.
        clock.advance_s(10);
        assert_eq!(core.tick(), Action::Freeze);
        assert!(core.journal().seq() >= 1);
        let events = socket::answer(
            &mut core,
            Role::Reader,
            &format!(
                "{{\"v\":\"{}\",\"verb\":\"events\",\"since_seq\":0}}",
                EVENT_VERSION
            ),
        );
        assert_eq!(
            events.get("boot_id").and_then(Value::as_str),
            Some("boot-test")
        );
        assert_eq!(
            events.get("rows").and_then(Value::as_array).map(<[_]>::len),
            Some(1)
        );
        assert_eq!(
            events.get("truncated").and_then(Value::as_bool),
            Some(false)
        );
        // ...and a `since_seq` above the newest row is an empty page, not everything.
        let none = socket::answer(
            &mut core,
            Role::Reader,
            &format!(
                "{{\"v\":\"{}\",\"verb\":\"events\",\"since_seq\":5}}",
                EVENT_VERSION
            ),
        );
        assert_eq!(
            none.get("rows").and_then(Value::as_array).map(<[_]>::len),
            Some(0)
        );
    }

    #[test]
    fn one_request_over_a_real_socketpair_reaches_the_daemon() {
        let tree = TempTree::new("daemon-socketpair");
        std::fs::create_dir_all(tree.at("dev.slice/dev-gates.slice/docker-abc.scope"))
            .expect("scope");
        let clock = Arc::new(ManualClock::new(1));
        let mut core = guard(
            &tree,
            Config::defaults(),
            clock,
            &tree.at("absent.pressure"),
        );
        let (mut client, server) = UnixStream::pair().expect("socketpair");
        client
            .set_read_timeout(Some(SESSION_READ_TIMEOUT))
            .expect("a bounded read");
        let register = format!(
            "{{\"v\":\"{}\",\"verb\":\"register\",\"id\":\"rg-1\",\"container\":\"abc\"}}\n",
            crate::REGISTRATION_VERSION
        );
        // The session's write half is a buffer, so the session holds the only real
        // endpoint and the request arrives from exactly one writer.
        let mut session = socket::Session::new(server, Vec::<u8>::new(), Role::Registrant);
        client.write_all(register.as_bytes()).expect("write");
        let line = session.read_request().expect("read").expect("one line");
        assert_eq!(line, register, "one whole request line");
        let reply = socket::answer(&mut core, Role::Registrant, &line);
        assert_eq!(reply.get("ok").and_then(Value::as_bool), Some(true));
        assert_eq!(
            reply.get("cgroup").and_then(Value::as_str),
            Some("docker-abc.scope")
        );
        // A peer that closes without sending is a clean end, not an error.
        drop(client);
        let mut reader = socket::Session::new(std::io::empty(), Vec::<u8>::new(), Role::Reader);
        assert_eq!(reader.read_request().expect("a closed peer"), None);
        let _ = &mut reader;
    }

    #[test]
    fn accepted_stream_has_a_write_timeout_before_any_session_path() {
        let tree = TempTree::new("daemon-accept-write-timeout");
        let listener = UnixListener::bind(tree.at("reader.sock")).expect("listener");
        let _client = UnixStream::connect(tree.at("reader.sock")).expect("queued client");
        let accepted = accept_with_write_timeout(&listener).expect("accepted stream");
        assert_eq!(
            accepted.write_timeout().expect("write timeout query"),
            Some(SESSION_WRITE_TIMEOUT),
            "the bound applies before either a normal handler or a capacity refusal"
        );
    }

    #[test]
    fn a_blocked_reply_writer_does_not_keep_the_core_mutex() {
        let tree = TempTree::new("daemon-write-lock");
        let core = Arc::new(Mutex::new(guard(
            &tree,
            Config::defaults(),
            Arc::new(ManualClock::new(1)),
            &tree.at("absent.pressure"),
        )));
        let line = format!("{{\"v\":\"{STATE_VERSION}\",\"verb\":\"version\"}}");
        let (started_tx, started_rx) = std::sync::mpsc::channel();
        let (release_tx, release_rx) = std::sync::mpsc::channel();
        let worker_core = Arc::clone(&core);
        let worker = std::thread::spawn(move || {
            answer_then_write(&worker_core, Role::Reader, &line, move |_| {
                started_tx
                    .send(())
                    .map_err(|error| io::Error::other(error.to_string()))?;
                release_rx
                    .recv()
                    .map_err(|error| io::Error::other(error.to_string()))
            })
        });

        started_rx.recv().expect("reply writer started");
        let lock_available_while_writer_waits = core.try_lock().is_ok();
        release_tx.send(()).expect("release reply writer");
        worker.join().expect("writer thread").expect("write result");
        assert!(
            lock_available_while_writer_waits,
            "the tick and other sessions must not wait on a peer's write"
        );
    }

    #[test]
    fn binding_fails_before_creating_a_socket_when_the_parent_is_not_a_directory() {
        let tree = TempTree::new("daemon-bind");
        let parent = tree.write("parent-is-a-file", "fixture");
        let socket = parent.join("guard.sock");
        let reader = tree.at("reader/thrashguard.sock");
        let error = bind(Some(&socket), &reader).expect_err("file cannot be a parent directory");
        assert!(error.contains("cannot create"), "{error}");
        assert!(parent.is_file());
        assert!(!socket.exists());
    }

    #[test]
    fn a_disabled_registrant_role_binds_only_the_reader_path() {
        let tree = TempTree::new("daemon-reader-only-bind");
        let registrant = tree.at("reg/thrashguard.sock");
        let reader = tree.at("reader/thrashguard.sock");
        let endpoint = bind(None, &reader).expect("reader-only bind");
        assert!(endpoint.registrant.is_none());
        assert!(reader.exists());
        assert!(!registrant.exists());
    }
}
