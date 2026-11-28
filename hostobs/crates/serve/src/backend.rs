//! The injected backend of the serving core (design R5 and section 1; WIRE.md sections 4.4 and
//! 4.5).
//!
//! A [`Conn`](crate::conn::Conn) never reads the sampler, the store or a clock: everything the
//! two P5b-1 verbs need from the probe comes through [`Backend`]. `hello` needs the server's
//! identity ([`Identity`]); `status` needs the facts only the backend knows ([`StatusInfo`]:
//! start time, uptime, the tick and the tier list).
//!
//! # Who fills what in `status` (WIRE.md section 4.5)
//!
//! - `queues.queued_bytes` is read by the connection from the send hub it is handed
//!   (`global_pools()`: Live plus Query bytes; the control pool exists per connection only).
//! - `clients` (connections per level) is the server layer's: the server wraps its backend in
//!   an adapter whose [`Backend::status`] returns the counters in [`StatusInfo::clients`].
//! - `queues.dropped_blocks` comes from the server's send hub, not from
//!   [`StatusInfo::dropped_blocks`]; the connection reads the hub's count.

use std::collections::BTreeMap;

use hostobs_proto::dict::{
    Entry, MetricFacts, MetricKind, MetricScope, MetricSource, MetricUnit, MetricValueType,
};
use hostobs_proto::frame::{BlockHeaderFacts, Column, Row};
use hostobs_proto::quality::Quality;

use crate::limits::Level;
use crate::selection::Selection;
use crate::{conn::Peer, hub::ConnId};

pub use crate::args::EventsArgs;

/// Event kind crossing the serve/backend seam.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EventKindView {
    /// A span begins.
    Begin,
    /// A span ends.
    End,
    /// A point event.
    Point,
}

impl EventKindView {
    /// The wire spelling.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Self::Begin => "begin",
            Self::End => "end",
            Self::Point => "point",
        }
    }
}

/// Event context key crossing the serve/backend seam.
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum EventCtxKeyView {
    /// `run`.
    Run,
    /// `lane`.
    Lane,
    /// `candidate`.
    Candidate,
    /// `txn`.
    Txn,
    /// `session`.
    Session,
}

impl EventCtxKeyView {
    /// The wire spelling.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Self::Run => "run",
            Self::Lane => "lane",
            Self::Candidate => "candidate",
            Self::Txn => "txn",
            Self::Session => "session",
        }
    }
}

/// Event outcome crossing the serve/backend seam.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EventOutcomeView {
    /// `ok`.
    Ok,
    /// `fail`.
    Fail,
    /// `error`.
    Error,
    /// `skipped`.
    Skipped,
    /// `cancelled`.
    Cancelled,
    /// `timeout`.
    Timeout,
    /// `abandoned`.
    Abandoned,
}

impl EventOutcomeView {
    /// The wire spelling.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Self::Ok => "ok",
            Self::Fail => "fail",
            Self::Error => "error",
            Self::Skipped => "skipped",
            Self::Cancelled => "cancelled",
            Self::Timeout => "timeout",
            Self::Abandoned => "abandoned",
        }
    }
}

/// The unverified peer label on a stored event.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct EventPeerView {
    /// PID, or null when unavailable.
    pub pid: Option<u32>,
    /// Command label, or null when unavailable.
    pub comm: Option<String>,
    /// Device and inode pair, or null when unavailable.
    pub cgroup: Option<(u64, u64)>,
}

/// One typed event returned by a backend, with its stable ring ordinal.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EventItem {
    /// Stable acceptance ordinal used only for paging.
    pub ordinal: u64,
    /// Event kind.
    pub kind: EventKindView,
    /// Event name.
    pub name: String,
    /// Event key.
    pub key: String,
    /// Client context.
    pub ctx: BTreeMap<EventCtxKeyView, String>,
    /// Span outcome, if this is an end.
    pub outcome: Option<EventOutcomeView>,
    /// Client event code.
    pub code: Option<String>,
    /// Client attributes.
    pub attrs: BTreeMap<String, String>,
    /// Server-stamped monotonic nanoseconds.
    pub t_mono_ns: u64,
    /// Wall-clock nanoseconds.
    pub real_ns: i64,
    /// Stored issuer text.
    pub issuer: String,
    /// Unverified peer label.
    pub peer: EventPeerView,
    /// Span id, null for points.
    pub span: Option<u64>,
    /// Whether the end was inferred.
    pub inferred: bool,
}

/// The bounded facts needed to serve one event window.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct EventWindow {
    /// Matching ring items, ordered by `(t_mono_ns, ordinal)`.
    pub items: Vec<EventItem>,
    /// Oldest real time still in the ring.
    pub oldest_real_ns: Option<i64>,
    /// Cumulative ring displacements.
    pub displaced: u64,
    /// The process start time also reported by `status`.
    pub started_real_ns: i64,
}

/// A typed refusal from the event page source.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PageRefusal {
    /// The requested cursor is no longer available or is malformed at the backend boundary.
    CursorInvalid,
    /// The backend failed internally.
    Internal,
}

/// The checked `annotate` event kind.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AnnotateKind {
    /// Open a connection-owned span.
    Begin,
    /// Close the innermost matching span on this connection.
    End,
    /// Record a point event.
    Point,
}

/// The checked outcome for an `end` event.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AnnotateOutcome {
    /// Work completed successfully.
    Ok,
    /// Work completed unsuccessfully.
    Fail,
    /// Work failed with an error.
    Error,
    /// Work was skipped.
    Skipped,
    /// Work was cancelled.
    Cancelled,
    /// Work timed out.
    Timeout,
    /// Work was abandoned by connection close.
    Abandoned,
}

/// The checked context keys accepted by `annotate`.
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum AnnotateCtxKey {
    /// `run`.
    Run,
    /// `lane`.
    Lane,
    /// `candidate`.
    Candidate,
    /// `txn`.
    Txn,
    /// `session`.
    Session,
}

/// Schema-checked `annotate` arguments.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AnnotateArgs {
    /// Event kind.
    pub kind: AnnotateKind,
    /// Event name.
    pub name: String,
    /// Explicit key, or `None` to use `name`.
    pub key: Option<String>,
    /// Client-written context.
    pub ctx: BTreeMap<AnnotateCtxKey, String>,
    /// End outcome, required only for `end`.
    pub outcome: Option<AnnotateOutcome>,
    /// Optional event code.
    pub code: Option<String>,
    /// Client-written attributes.
    pub attrs: BTreeMap<String, String>,
    /// Validated point-only `(boot_id, seq)` idempotency key, when present.
    pub idempotency: Option<(String, u64)>,
}

/// Successful `annotate` reply values.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct AnnotateReply {
    /// Server-stamped monotonic nanoseconds.
    pub t_mono_ns: u64,
    /// Opened or closed span id, or `None` for a point.
    pub span: Option<u64>,
}

/// A typed refusal from the annotation engine.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AnnotateRefusal {
    /// The per-uid token bucket refused the request.
    RateLimited {
        /// Milliseconds until one token is available.
        retry_after_ms: u32,
    },
    /// The span table is at its uid or global bound.
    Budget,
    /// No span with this key is open on this connection.
    SpanUnknown,
    /// Timestamp derivation, event encoding or store append failed.
    Internal,
}

/// What `hello` announces about the server (WIRE.md section 4.4).
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Identity {
    /// `server`: the server's name and version text.
    pub server: String,
    /// `boot_id`: the UUID text of this boot.
    pub boot_id: String,
    /// `epoch`.
    pub epoch: u64,
    /// `features`, a subset of `peak_reset`, `placement`, `damon` and `enrich`.
    pub features: Vec<String>,
}

/// The `tick` member of `status`.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct TickInfo {
    /// The latest tick's sequence number.
    pub seq: u64,
    /// The latest tick's monotonic time.
    pub last_mono_ns: u64,
    /// How long the latest scan took.
    pub scan_ms: u32,
}

/// One entry of the `tiers` list of `status`.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TierInfo {
    /// The tier name (`hot`, `warm`, `cold`).
    pub name: String,
    /// The bucket width.
    pub step_ms: u32,
    /// The retention.
    pub keep_s: u32,
    /// The bytes held now.
    pub bytes: u64,
    /// The byte cap.
    pub cap_bytes: u64,
}

/// What `status` reports (WIRE.md section 4.5), minus what the connection reads itself.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct StatusInfo {
    /// `started_real_ns`: the wall-clock start of this boot.
    pub started_real_ns: i64,
    /// `uptime_ms`.
    pub uptime_ms: u64,
    /// `tick`.
    pub tick: TickInfo,
    /// `clients`: connections per level, in the order given (the server layer fills it).
    pub clients: Vec<(Level, u32)>,
    /// `queues.dropped_blocks` (ignored by the connection, which reads the hub's count).
    pub dropped_blocks: u64,
    /// `tiers`, at most 8 (a longer list overflows `max_reply_bytes` and answers `internal`).
    pub tiers: Vec<TierInfo>,
}

/// One backend tick: the dictionary plus the live block's rows and columns.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TickData {
    /// `boot_id`, `epoch`, `seq`, `mono_start_ns`, `mono_end_ns`, `boot_ns` and `real_ns` are
    /// taken as given; `dict_gen`, `n_rows`, `dropped` and `flags` are overwritten by the
    /// server (design S16).
    pub header: BlockHeaderFacts,
    /// The dictionary of this tick: index `i` of a row's `ent` or a column's `metric` is
    /// `entries[i]`.
    pub entries: Vec<Entry>,
    /// The task's entity rows.
    pub rows: Vec<Row>,
    /// The task's metric columns.
    pub columns: Vec<Column>,
}

impl TickData {
    /// A deterministic sample for tests and the P5b-4 bring-up: one [`Entry::Entity`] per
    /// incarnation (label `inc-<n>`), then one [`Entry::Metric`] per name. Rows are in
    /// incarnation order; every metric is a gauge/count/probe/U64 column whose value is
    /// `inc.wrapping_mul(1000).wrapping_add(seq)` with quality [`Quality::Ok`]. The header
    /// has `seq = seq`, epoch 1, zero boot id and times, and zero server-owned fields.
    #[must_use]
    pub fn example(seq: u64, incs: &[u64], metrics: &[&str]) -> TickData {
        let mut entries = Vec::with_capacity(incs.len().saturating_add(metrics.len()));
        for &inc in incs {
            entries.push(Entry::Entity {
                inc,
                label: format!("inc-{inc}"),
            });
        }
        let entity_count = u32::try_from(entries.len()).unwrap_or(u32::MAX);
        for name in metrics {
            entries.push(Entry::Metric {
                facts: MetricFacts {
                    unit: MetricUnit::Count,
                    kind: MetricKind::Gauge,
                    scope: MetricScope::Cgroup,
                    source: MetricSource::Probe,
                },
                name: (*name).to_string(),
            });
        }
        let rows: Vec<Row> = incs
            .iter()
            .enumerate()
            .map(|(index, _)| Row {
                ent: u32::try_from(index).unwrap_or(u32::MAX),
                t_off_us: 0,
            })
            .collect();
        let quality = vec![Quality::Ok.code(); incs.len()];
        let columns = metrics
            .iter()
            .enumerate()
            .map(|(index, _)| Column {
                metric: entity_count.saturating_add(u32::try_from(index).unwrap_or(u32::MAX)),
                value_type: MetricValueType::U64,
                kind: MetricKind::Gauge,
                values: incs
                    .iter()
                    .map(|inc| inc.wrapping_mul(1_000).wrapping_add(seq))
                    .collect(),
                quality: quality.clone(),
            })
            .collect();
        TickData {
            header: BlockHeaderFacts {
                boot_id: [0; 16],
                epoch: 1,
                seq,
                mono_start_ns: 0,
                mono_end_ns: 0,
                boot_ns: 0,
                real_ns: 0,
                dict_gen: 0,
                n_rows: u32::try_from(rows.len()).unwrap_or(u32::MAX),
                dropped: 0,
                flags: 0,
            },
            entries,
            rows,
            columns,
        }
    }
}

/// Why a backend could not answer [`Backend::current`].
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BackendError {
    /// No current tick resolves this selection.
    Unavailable,
    /// Resolution would exceed the request's row bound.
    TooManyRows,
}

/// The probe's side of the serving core: identity, status and the current tick data.
pub trait Backend {
    /// The facts `hello` announces.
    fn identity(&self) -> Identity;
    /// The facts `status` reports.
    fn status(&self) -> StatusInfo;
    /// The latest tick's data for `selection`. Resolution is the backend's: the protocol has
    /// none. The backend stops resolving at `max_rows + 1` rows and answers
    /// [`BackendError::TooManyRows`], so one request costs at most that many rows of work.
    fn current(&self, selection: &Selection, max_rows: u32) -> Result<TickData, BackendError>;
    /// Append one schema-checked annotation on the server thread.
    fn annotate(
        &mut self,
        conn: ConnId,
        peer: &Peer,
        args: &AnnotateArgs,
    ) -> Result<AnnotateReply, AnnotateRefusal>;
    /// Read one bounded page from the process-local event view.
    fn events(&mut self, args: &EventsArgs) -> Result<EventWindow, PageRefusal>;
    /// Close spans owned by a connection that the server is removing.
    fn connection_closed(&mut self, conn: ConnId);
}

/// The driver's per-turn hook (docs/PROBE-PROCESS.md §(b)).
pub trait Turn: Backend {
    /// Load the turn's published state; `true` when its tick is newer than the previous turn's.
    fn begin_turn(&mut self, now_ns: u64) -> bool;
}

/// A backend with fixed answers, for tests and the P5b-4 bring-up.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct FixedBackend {
    /// The answer of [`Backend::identity`].
    pub identity: Identity,
    /// The answer of [`Backend::status`].
    pub status: StatusInfo,
    /// The answer of [`Backend::current`], keyed by the canonical selection.
    pub current: BTreeMap<Selection, Result<TickData, BackendError>>,
    /// The events returned by [`Backend::events`].
    pub event_window: EventWindow,
    /// The test-controlled sequence number of the current published tick.
    pub seq: u64,
    /// The last sequence number announced by [`Turn::begin_turn`].
    pub seen: u64,
}

impl FixedBackend {
    /// A backend named `hostobs-probe`, epoch 1, no features, no tiers, all counters 0.
    #[must_use]
    pub fn new() -> FixedBackend {
        FixedBackend {
            identity: Identity {
                server: String::from("hostobs-probe"),
                boot_id: String::from("00000000-0000-4000-8000-000000000001"),
                epoch: 1,
                features: Vec::new(),
            },
            status: StatusInfo::default(),
            current: BTreeMap::new(),
            event_window: EventWindow::default(),
            seq: 0,
            seen: 0,
        }
    }
}

impl Default for FixedBackend {
    fn default() -> FixedBackend {
        FixedBackend::new()
    }
}

impl Backend for FixedBackend {
    fn identity(&self) -> Identity {
        self.identity.clone()
    }

    fn status(&self) -> StatusInfo {
        self.status.clone()
    }

    fn current(&self, selection: &Selection, max_rows: u32) -> Result<TickData, BackendError> {
        match self.current.get(selection) {
            None => Err(BackendError::Unavailable),
            Some(Err(error)) => Err(*error),
            Some(Ok(data)) => {
                let limit = usize::try_from(max_rows).unwrap_or(usize::MAX);
                if data.rows.len() > limit {
                    Err(BackendError::TooManyRows)
                } else {
                    Ok(data.clone())
                }
            }
        }
    }

    fn annotate(
        &mut self,
        _conn: ConnId,
        _peer: &Peer,
        _args: &AnnotateArgs,
    ) -> Result<AnnotateReply, AnnotateRefusal> {
        Err(AnnotateRefusal::Internal)
    }

    fn events(&mut self, args: &EventsArgs) -> Result<EventWindow, PageRefusal> {
        let after = match args.cursor.as_deref() {
            None => 0,
            Some(cursor) => cursor
                .rsplit(':')
                .next()
                .and_then(|ordinal| ordinal.parse::<u64>().ok())
                .ok_or(PageRefusal::CursorInvalid)?,
        };
        if args.cursor.is_some() && after < self.event_window.displaced {
            return Err(PageRefusal::CursorInvalid);
        }
        let mut window = self.event_window.clone();
        window.items.retain(|item| {
            item.ordinal >= after
                && item.real_ns >= args.from_real_ns
                && item.real_ns < args.to_real_ns
                && (args.names.is_empty() || args.names.iter().any(|name| name == &item.name))
        });
        window
            .items
            .sort_by_key(|item| (item.t_mono_ns, item.ordinal));
        window.items.truncate(
            usize::try_from(args.limit)
                .unwrap_or(usize::MAX)
                .saturating_add(1),
        );
        Ok(window)
    }

    fn connection_closed(&mut self, _conn: ConnId) {}
}

impl Turn for FixedBackend {
    fn begin_turn(&mut self, _now_ns: u64) -> bool {
        let fresh = self.seq != self.seen;
        self.seen = self.seq;
        fresh
    }
}

#[cfg(test)]
mod tests {
    use hostobs_proto::dict::Entry;

    use super::{Backend, BackendError, FixedBackend, TickData, Turn};
    use crate::selection::{Sel, Selection};

    fn selection() -> Selection {
        Selection::new(vec![Sel::Inc(1)], vec!["m".to_string()]).expect("selection")
    }

    #[test]
    fn fixed_backend_announces_each_sequence_change_once() {
        let mut backend = FixedBackend::new();
        assert!(!backend.begin_turn(1));
        backend.seq = 2;
        assert!(backend.begin_turn(2));
        assert!(!backend.begin_turn(3));
        backend.seq = 2;
        assert!(!backend.begin_turn(4));
        backend.seq = 3;
        assert!(backend.begin_turn(5));
        assert_eq!(backend.seen, 3);
    }

    #[test]
    fn fixed_backend_answers_what_it_holds() {
        let mut backend = FixedBackend::new();
        assert_eq!(backend.identity().server, "hostobs-probe");
        assert_eq!(backend.identity().epoch, 1);
        assert!(backend.status().tiers.is_empty());
        backend.identity.epoch = 7;
        backend.status.uptime_ms = 9;
        assert_eq!(backend.identity().epoch, 7);
        assert_eq!(backend.status().uptime_ms, 9);
        assert_eq!(FixedBackend::default(), FixedBackend::new());
    }

    #[test]
    fn backend_fixed_too_many_rows() {
        let key = selection();
        let mut backend = FixedBackend::new();
        backend
            .current
            .insert(key.clone(), Ok(TickData::example(3, &[1, 2, 3], &["m"])));
        assert_eq!(backend.current(&key, 2), Err(BackendError::TooManyRows));
        let found = backend.current(&key, 3).expect("three rows fit");
        assert_eq!(found.rows.len(), 3);
        let other = Selection::new(vec![Sel::Inc(2)], vec!["m".to_string()]).expect("other");
        assert_eq!(backend.current(&other, 4), Err(BackendError::Unavailable));
    }

    #[test]
    fn tick_example_wraps() {
        let data = TickData::example(u64::MAX, &[u64::MAX], &["m"]);
        assert_eq!(
            data.entries.first(),
            Some(&Entry::Entity {
                inc: u64::MAX,
                label: format!("inc-{}", u64::MAX),
            })
        );
        let column = data.columns.first().expect("column");
        let expected = u64::MAX.wrapping_mul(1_000).wrapping_add(u64::MAX);
        assert_eq!(column.values.first(), Some(&expected));
    }
}
