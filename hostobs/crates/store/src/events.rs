//! The events and typed spans store: the segment machinery of [`crate::tier`] with its **own
//! cap**, plus the two bounded tables that make spans meaningful (plan v4 §C.4).
//!
//! The events corpus is *not* history: history is periodic samples of values, events are
//! points in time with a name, a kind and an outcome, and the volume is bursty (a mutation
//! campaign emits thousands of candidate spans). So the plan gives it a separate directory
//! (`/var/lib/hostobs/probe/events`), a separate cap (`events_cap_bytes`), and a RAM ring so a
//! live subscriber sees the last few thousand events without reading a segment.
//!
//! # Contracts
//!
//! * **The ring is bounded, and its overflow is counted.** A ring that silently drops the
//!   oldest event is the "silent data loss" failure §C.5 forbids, so
//!   [`EventRing::push`] returns the [`EvictedEvent`] it displaced (or nothing) and
//!   [`EventsStats`] accumulates every displacement.
//! * **A span table has hard limits, and hitting one is a refusal, not a squeeze.** §C.4:
//!   "64 open spans per issuer, 4,096 global". An issuer that opens a 65th span gets
//!   [`StoreError::SpanLimit`]; the table never evicts a span that is still open, because
//!   doing so would erase the start time of a span something else may already reference.
//! * **`end` closes the innermost open span for that key**, and an unknown key is
//!   [`StoreError::SpanUnknown`] (§C.4's `span-unknown`), not a silent no-op.
//! * **An abandoned span is inferred, and says so.** [`SpanTable::abandon_issuer`] closes
//!   every span an issuer left open with outcome `abandoned` and `inferred: true` (§C.4).
//! * **Vocabulary is closed and checked on the way in.** [`EventKind`] and [`Outcome`] refuse
//!   an unknown name, [`check_name`] enforces §C.4's `^[a-z][a-z0-9_.-]{0,63}$`, and
//!   [`check_bounded`] enforces the byte bounds. Nothing is truncated to fit: a 65-byte key is
//!   a refused key.
//! * **The payload is still a `proto` EVT frame.** The store does not build one (it has no
//!   `proto` dependency, `HOB-27`): it checks the frame header facts that decide whether these
//!   bytes may be stored, exactly as it does for a BLOCK frame in a history tier.

use std::collections::BTreeMap;

use crate::error::StoreError;
use crate::format::TierKind;
use crate::queue::Job;
use crate::tier::{AppendOutcome, Tier, TierConfig};

/// The longest event or span name (§C.4: `^[a-z][a-z0-9_.-]{0,63}$`).
pub const MAX_NAME: usize = 64;
/// The longest client key (§C.4: 64 B).
pub const MAX_KEY: usize = 64;
/// The longest context value (§C.4: 128 B).
pub const MAX_CTX: usize = 128;
/// The longest outcome code (§C.4: 64 B).
pub const MAX_CODE: usize = 64;
/// The most attributes one event may carry (§C.4: 8 pairs).
pub const MAX_ATTRS: usize = 8;
/// The longest attribute value (§C.4: 256 B).
pub const MAX_ATTR_VALUE: usize = 256;
/// The most open spans one issuer may hold (§C.4: 64).
pub const MAX_OPEN_SPANS_PER_ISSUER: usize = 64;
/// The most open spans all issuers together may hold (§C.4: 4,096).
pub const MAX_OPEN_SPANS: usize = 4096;
/// The largest event RAM ring the configuration accepts (the plan's proposal is 4 MiB, well
/// under the 64 MiB events cap).
pub const MAX_RING_BYTES: u64 = 1 << 30;

/// The kind of an event (§C.4, closed vocabulary).
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
pub enum EventKind {
    /// A span opens.
    Begin,
    /// A span closes.
    End,
    /// A single point in time.
    Point,
}

impl EventKind {
    /// The stored byte.
    #[must_use]
    pub const fn code(self) -> u8 {
        match self {
            EventKind::Begin => 1,
            EventKind::End => 2,
            EventKind::Point => 3,
        }
    }

    /// The stored name.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            EventKind::Begin => "begin",
            EventKind::End => "end",
            EventKind::Point => "point",
        }
    }

    /// The kind a stored byte names.
    ///
    /// # Errors
    ///
    /// [`StoreError::EventVocabulary`] for a byte outside `1..=3`.
    pub const fn from_code(code: u8) -> Result<EventKind, StoreError> {
        match code {
            1 => Ok(EventKind::Begin),
            2 => Ok(EventKind::End),
            3 => Ok(EventKind::Point),
            found => Err(StoreError::EventVocabulary {
                field: "kind",
                found,
            }),
        }
    }
}

/// The outcome of a span (§C.4, closed vocabulary).
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
pub enum Outcome {
    /// The work finished as intended.
    Ok,
    /// The work finished but did not succeed.
    Fail,
    /// The work failed with an error.
    Error,
    /// The work did not run.
    Skipped,
    /// The work was cancelled.
    Cancelled,
    /// The work ran out of time.
    Timeout,
    /// The span was closed without an `end`, or its issuer disappeared.
    Abandoned,
}

impl Outcome {
    /// The stored byte.
    #[must_use]
    pub const fn code(self) -> u8 {
        self as u8
    }

    /// The stored name.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Outcome::Ok => "ok",
            Outcome::Fail => "fail",
            Outcome::Error => "error",
            Outcome::Skipped => "skipped",
            Outcome::Cancelled => "cancelled",
            Outcome::Timeout => "timeout",
            Outcome::Abandoned => "abandoned",
        }
    }

    /// The outcome a stored byte names.
    ///
    /// # Errors
    ///
    /// [`StoreError::EventVocabulary`] for a byte outside `0..=6`.
    pub const fn from_code(code: u8) -> Result<Outcome, StoreError> {
        match code {
            0 => Ok(Outcome::Ok),
            1 => Ok(Outcome::Fail),
            2 => Ok(Outcome::Error),
            3 => Ok(Outcome::Skipped),
            4 => Ok(Outcome::Cancelled),
            5 => Ok(Outcome::Timeout),
            6 => Ok(Outcome::Abandoned),
            found => Err(StoreError::EventVocabulary {
                field: "outcome",
                found,
            }),
        }
    }

    /// The outcome a name names.
    ///
    /// # Errors
    ///
    /// [`StoreError::EventVocabulary`] for a name outside the vocabulary.
    pub fn from_name(name: &str) -> Result<Outcome, StoreError> {
        match name {
            "ok" => Ok(Outcome::Ok),
            "fail" => Ok(Outcome::Fail),
            "error" => Ok(Outcome::Error),
            "skipped" => Ok(Outcome::Skipped),
            "cancelled" => Ok(Outcome::Cancelled),
            "timeout" => Ok(Outcome::Timeout),
            "abandoned" => Ok(Outcome::Abandoned),
            _ => Err(StoreError::EventVocabularyName {
                field: "outcome",
                found: name.to_string(),
            }),
        }
    }
}

/// Check §C.4's name grammar: one lower-case letter, then lower-case letters, digits, `.`,
/// `_` or `-`, at most [`MAX_NAME`] bytes.
///
/// # Errors
///
/// [`StoreError::EventVocabularyName`] naming the rule that did not hold.
pub fn check_name(name: &str) -> Result<(), StoreError> {
    check_bounded("name", name, MAX_NAME)?;
    let mut bytes = name.bytes();
    match bytes.next() {
        Some(first) if first.is_ascii_lowercase() => {}
        _ => {
            return Err(StoreError::EventVocabularyName {
                field: "name",
                found: name.to_string(),
            })
        }
    }
    if !bytes.all(|byte| {
        byte.is_ascii_lowercase()
            || byte.is_ascii_digit()
            || byte == b'.'
            || byte == b'_'
            || byte == b'-'
    }) {
        return Err(StoreError::EventVocabularyName {
            field: "name",
            found: name.to_string(),
        });
    }
    Ok(())
}

/// Check that `value` is at most `max` bytes.
///
/// # Errors
///
/// [`StoreError::EventFieldTooLong`].
pub fn check_bounded(field: &'static str, value: &str, max: usize) -> Result<(), StoreError> {
    if value.len() > max {
        return Err(StoreError::EventFieldTooLong {
            field,
            len: value.len(),
            max,
        });
    }
    Ok(())
}

/// One event, with the facts a consumer queries and the payload the store keeps.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Event {
    /// The event's kind.
    pub kind: EventKind,
    /// The event's name (§C.4 grammar).
    pub name: String,
    /// The client key; `name` when the issuer did not give one.
    pub key: String,
    /// The issuer (uid plus, at observe level, the peer's cgroup incarnation).
    pub issuer: String,
    /// The monotonic time the probe stamped at receipt (§C.4: never trusted from the client).
    pub t_mono_ns: i64,
    /// The outcome, for `end` and an inferred `end`.
    pub outcome: Option<Outcome>,
    /// The outcome's code, ≤ 64 B.
    pub code: Option<String>,
    /// True when the probe inferred the close rather than receiving it.
    pub inferred: bool,
}

impl Event {
    /// An event, with each field checked against §C.4's bounds.
    ///
    /// # Errors
    ///
    /// [`StoreError::EventVocabularyName`], [`StoreError::EventFieldTooLong`] and
    /// [`StoreError::EventVocabulary`] (an `end` without an outcome, or a `begin`/`point`
    /// with one).
    pub fn new(
        kind: EventKind,
        name: &str,
        key: Option<&str>,
        issuer: &str,
        t_mono_ns: i64,
        outcome: Option<Outcome>,
    ) -> Result<Event, StoreError> {
        check_name(name)?;
        let key = key.unwrap_or(name);
        check_bounded("key", key, MAX_KEY)?;
        check_bounded("issuer", issuer, MAX_CTX)?;
        if t_mono_ns < 0 {
            return Err(StoreError::NegativeTime { ns: t_mono_ns });
        }
        match (kind, outcome) {
            (EventKind::End, Some(_)) => {}
            (EventKind::Begin | EventKind::Point, None) => {}
            (EventKind::End, None) => {
                return Err(StoreError::EventVocabulary {
                    field: "outcome",
                    found: 0,
                })
            }
            (EventKind::Begin | EventKind::Point, Some(outcome)) => {
                return Err(StoreError::EventVocabulary {
                    field: "outcome",
                    found: outcome.code(),
                })
            }
        }
        Ok(Event {
            kind,
            name: name.to_string(),
            key: key.to_string(),
            issuer: issuer.to_string(),
            t_mono_ns,
            outcome,
            code: None,
            inferred: false,
        })
    }

    /// Attach the outcome code (≤ 64 B).
    ///
    /// # Errors
    ///
    /// [`StoreError::EventFieldTooLong`].
    pub fn with_code(mut self, code: &str) -> Result<Event, StoreError> {
        check_bounded("code", code, MAX_CODE)?;
        self.code = Some(code.to_string());
        Ok(self)
    }

    /// Mark this event as an inferred close (§C.4).
    #[must_use]
    pub fn inferred(mut self) -> Event {
        self.inferred = true;
        self
    }

    /// The bytes this event occupies in a RAM ring (its fields plus a fixed envelope).
    #[must_use]
    pub fn bytes(&self) -> u64 {
        let fields = self
            .name
            .len()
            .saturating_add(self.key.len())
            .saturating_add(self.issuer.len())
            .saturating_add(self.code.as_ref().map_or(0, String::len));
        u64::try_from(fields)
            .unwrap_or(u64::MAX)
            .saturating_add(EVENT_OVERHEAD)
    }
}

/// The fixed per-event envelope a ring charges (kind, times, outcome, flags, `String`s).
pub const EVENT_OVERHEAD: u64 = 80;

/// What a ring push displaced.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EvictedEvent {
    /// The displaced event.
    pub event: Event,
    /// The bytes it held.
    pub bytes: u64,
}

/// The counters of the events store.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct EventsStats {
    /// Events accepted by the ring.
    pub accepted: u64,
    /// Events displaced from the ring by its bound.
    pub displaced: u64,
    /// Bytes displaced from the ring.
    pub displaced_bytes: u64,
    /// Events written to segments.
    pub stored: u64,
    /// Jobs the writer queue dropped, as reported by the store.
    pub dropped: u64,
    /// The events store's segment bytes.
    pub bytes: u64,
    /// The ring's own bytes.
    pub ring_bytes: u64,
    /// Spans opened and not yet closed.
    pub open_spans: u64,
    /// Spans closed with an inferred `abandoned`.
    pub abandoned: u64,
}

impl core::fmt::Display for Outcome {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        f.write_str(self.name())
    }
}

impl core::fmt::Display for EventKind {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        f.write_str(self.name())
    }
}

/// The bounded RAM ring of recent events.
///
/// A ring is not a cache with a policy: it is the last `capacity` bytes of events, and the
/// oldest one leaves when a new one does not fit. The displacement is *returned* so the caller
/// can count it ([`EventsStats::displaced`]).
#[derive(Clone, Debug)]
pub struct EventRing {
    events: std::collections::VecDeque<(Event, u64)>,
    bytes: u64,
    capacity: u64,
    displaced: u64,
    displaced_bytes: u64,
    accepted: u64,
}

impl EventRing {
    /// A ring bounded by `capacity` bytes.
    ///
    /// # Errors
    ///
    /// [`StoreError::Config`] for a zero capacity or one past [`MAX_RING_BYTES`].
    pub fn new(capacity: u64) -> Result<EventRing, StoreError> {
        if capacity == 0 {
            return Err(StoreError::Config {
                key: "events_ring_bytes",
                reason: "must be at least one byte",
            });
        }
        if capacity > MAX_RING_BYTES {
            return Err(StoreError::Config {
                key: "events_ring_bytes",
                reason: "is past the store's own bound",
            });
        }
        Ok(EventRing {
            events: std::collections::VecDeque::new(),
            bytes: 0,
            capacity,
            displaced: 0,
            displaced_bytes: 0,
            accepted: 0,
        })
    }

    /// The bound.
    #[must_use]
    pub const fn capacity(&self) -> u64 {
        self.capacity
    }

    /// The bytes the ring holds.
    #[must_use]
    pub const fn bytes(&self) -> u64 {
        self.bytes
    }

    /// How many events the ring holds.
    #[must_use]
    pub fn len(&self) -> usize {
        self.events.len()
    }

    /// True when the ring holds nothing.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.events.is_empty()
    }

    /// The events the ring holds, oldest first.
    pub fn events(&self) -> impl Iterator<Item = &Event> {
        self.events.iter().map(|(event, _)| event)
    }

    /// The newest `count` events, oldest first.
    #[must_use]
    pub fn recent(&self, count: usize) -> Vec<Event> {
        let skip = self.events.len().saturating_sub(count);
        self.events
            .iter()
            .skip(skip)
            .map(|(event, _)| event.clone())
            .collect()
    }

    /// The displacements so far.
    #[must_use]
    pub const fn displaced(&self) -> u64 {
        self.displaced
    }

    /// The bytes displaced so far.
    #[must_use]
    pub const fn displaced_bytes(&self) -> u64 {
        self.displaced_bytes
    }

    /// The events accepted so far.
    #[must_use]
    pub const fn accepted(&self) -> u64 {
        self.accepted
    }

    /// Push one event, displacing the oldest if the bound requires it.
    ///
    /// # Errors
    ///
    /// [`StoreError::JobTooLarge`] when the event alone is larger than the whole ring (a
    /// `VecDeque` that accepted it would have to be over its bound to be honest about it).
    pub fn push(&mut self, event: Event) -> Result<Vec<EvictedEvent>, StoreError> {
        let bytes = event.bytes();
        if bytes > self.capacity {
            return Err(StoreError::JobTooLarge {
                len: bytes,
                max: self.capacity,
            });
        }
        let mut evicted = Vec::new();
        while self.bytes.saturating_add(bytes) > self.capacity {
            let Some((old, old_bytes)) = self.events.pop_front() else {
                break;
            };
            self.bytes = self.bytes.saturating_sub(old_bytes);
            self.displaced = self.displaced.saturating_add(1);
            self.displaced_bytes = self.displaced_bytes.saturating_add(old_bytes);
            evicted.push(EvictedEvent {
                event: old,
                bytes: old_bytes,
            });
        }
        self.events.push_back((event, bytes));
        self.bytes = self.bytes.saturating_add(bytes);
        self.accepted = self.accepted.saturating_add(1);
        Ok(evicted)
    }
}

/// One open span: the facts an `end` needs.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OpenSpan {
    /// The span's name.
    pub name: String,
    /// The issuer that opened it.
    pub issuer: String,
    /// The monotonic start time the probe stamped.
    pub t_mono_ns: i64,
}

/// What closing a span produced: the span and the event describing its end.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ClosedSpan {
    /// The span that was open.
    pub span: OpenSpan,
    /// Its duration on the monotonic clock.
    pub duration_ns: i64,
    /// The event recording the close.
    pub event: Event,
}

/// The bounded open-span table (§C.4's "64 per issuer, 4,096 global").
///
/// The innermost open span wins: `end` closes the most recent `begin` for that (issuer, key),
/// which is what a nested `candidate.build` inside `candidate` needs.
#[derive(Clone, Debug, Default)]
pub struct SpanTable {
    /// One `Vec` per (issuer, key): the open spans in open order, innermost last.
    open: BTreeMap<(String, String), Vec<OpenSpan>>,
    per_issuer: BTreeMap<String, usize>,
    total: usize,
    abandoned: u64,
}

impl SpanTable {
    /// A table with the plan's bounds.
    #[must_use]
    pub fn new() -> SpanTable {
        SpanTable::default()
    }

    /// How many spans are open.
    #[must_use]
    pub const fn len(&self) -> usize {
        self.total
    }

    /// True when no span is open.
    #[must_use]
    pub const fn is_empty(&self) -> bool {
        self.total == 0
    }

    /// How many spans an issuer holds open.
    #[must_use]
    pub fn open_for(&self, issuer: &str) -> usize {
        self.per_issuer.get(issuer).copied().unwrap_or(0)
    }

    /// How many spans have been closed with an inferred `abandoned`.
    #[must_use]
    pub const fn abandoned(&self) -> u64 {
        self.abandoned
    }

    /// The open span an `end` would close, without closing it.
    #[must_use]
    pub fn peek(&self, issuer: &str, key: &str) -> Option<&OpenSpan> {
        self.open
            .get(&(issuer.to_string(), key.to_string()))
            .and_then(|stack| stack.last())
    }

    /// Open a span.
    ///
    /// # Errors
    ///
    /// [`StoreError::SpanLimit`] when the issuer or the table is at its bound, and every
    /// [`Event::new`] refusal.
    pub fn begin(&mut self, event: &Event) -> Result<(), StoreError> {
        if event.kind != EventKind::Begin {
            return Err(StoreError::EventVocabulary {
                field: "kind",
                found: event.kind.code(),
            });
        }
        if self.open_for(&event.issuer) >= MAX_OPEN_SPANS_PER_ISSUER {
            return Err(StoreError::SpanLimit {
                issuer: event.issuer.clone(),
                open: self.open_for(&event.issuer),
                max: MAX_OPEN_SPANS_PER_ISSUER,
                global: false,
            });
        }
        if self.total >= MAX_OPEN_SPANS {
            return Err(StoreError::SpanLimit {
                issuer: event.issuer.clone(),
                open: self.total,
                max: MAX_OPEN_SPANS,
                global: true,
            });
        }
        self.open
            .entry((event.issuer.clone(), event.key.clone()))
            .or_default()
            .push(OpenSpan {
                name: event.name.clone(),
                issuer: event.issuer.clone(),
                t_mono_ns: event.t_mono_ns,
            });
        *self.per_issuer.entry(event.issuer.clone()).or_insert(0) += 1;
        self.total += 1;
        Ok(())
    }

    /// Close the innermost open span for this issuer and key, producing the `end` event.
    ///
    /// # Errors
    ///
    /// [`StoreError::SpanUnknown`] when no span is open under that key, and
    /// [`StoreError::NegativeTime`] when the end precedes its start (a caller's clock
    /// regression is reported, not clamped).
    pub fn end(&mut self, event: &Event) -> Result<ClosedSpan, StoreError> {
        if event.kind != EventKind::End {
            return Err(StoreError::EventVocabulary {
                field: "kind",
                found: event.kind.code(),
            });
        }
        let key = (event.issuer.clone(), event.key.clone());
        let Some(stack) = self.open.get_mut(&key) else {
            return Err(StoreError::SpanUnknown {
                issuer: event.issuer.clone(),
                key: event.key.clone(),
            });
        };
        let Some(span) = stack.pop() else {
            return Err(StoreError::SpanUnknown {
                issuer: event.issuer.clone(),
                key: event.key.clone(),
            });
        };
        if stack.is_empty() {
            self.open.remove(&key);
        }
        if let Some(count) = self.per_issuer.get_mut(&event.issuer) {
            *count = count.saturating_sub(1);
            if *count == 0 {
                self.per_issuer.remove(&event.issuer);
            }
        }
        self.total = self.total.saturating_sub(1);
        let duration_ns = event.t_mono_ns.saturating_sub(span.t_mono_ns);
        if duration_ns < 0 {
            return Err(StoreError::NegativeTime { ns: duration_ns });
        }
        Ok(ClosedSpan {
            span,
            duration_ns,
            event: event.clone(),
        })
    }

    /// Close every span an issuer left open (§C.4: a disappeared cgroup incarnation seals its
    /// open spans with `abandoned`, `inferred: true`).
    ///
    /// Returns one inferred `end` event per span, in open order (outermost first).
    #[must_use]
    pub fn abandon_issuer(&mut self, issuer: &str, t_mono_ns: i64) -> Vec<Event> {
        let mut keys: Vec<(String, String)> = self
            .open
            .keys()
            .filter(|(open_issuer, _)| open_issuer == issuer)
            .cloned()
            .collect();
        keys.sort();
        let mut out = Vec::new();
        for key in keys {
            if let Some(stack) = self.open.get_mut(&key) {
                while let Some(span) = stack.pop() {
                    self.total = self.total.saturating_sub(1);
                    if let Some(count) = self.per_issuer.get_mut(issuer) {
                        *count = count.saturating_sub(1);
                    }
                    self.abandoned = self.abandoned.saturating_add(1);
                    if let Ok(event) = Event::new(
                        EventKind::End,
                        &span.name,
                        Some(&key.1),
                        issuer,
                        t_mono_ns.max(span.t_mono_ns),
                        Some(Outcome::Abandoned),
                    ) {
                        out.push(event.inferred());
                    }
                }
                self.open.remove(&key);
            }
        }
        if self.per_issuer.get(issuer) == Some(&0) {
            self.per_issuer.remove(issuer);
        }
        out
    }
}

/// The events store: one [`Tier`] of the events corpus with its own cap, the RAM ring and the
/// open-span table.
#[derive(Debug)]
pub struct EventsStore {
    tier: Tier,
    ring: EventRing,
    spans: SpanTable,
    stored: u64,
    dropped: u64,
    max_file_bytes: usize,
}

impl EventsStore {
    /// Open (or create) the events store at the tier's directory.
    ///
    /// # Errors
    ///
    /// Every [`Tier::open`] refusal, [`EventRing::new`] and [`TierConfig`] refusal.
    pub fn open(
        config: TierConfig,
        ring_bytes: u64,
        dir: crate::fsio::StoreDir,
        max_file_bytes: usize,
    ) -> Result<EventsStore, StoreError> {
        if config.kind != TierKind::Events {
            return Err(StoreError::TierKindMismatch {
                tier: "events",
                payload: config.kind.name(),
            });
        }
        let tier = Tier::open(config, dir, max_file_bytes)?;
        let ring = EventRing::new(ring_bytes)?;
        Ok(EventsStore {
            tier,
            ring,
            spans: SpanTable::new(),
            stored: 0,
            dropped: 0,
            max_file_bytes,
        })
    }

    /// The tier.
    #[must_use]
    pub const fn tier(&self) -> &Tier {
        &self.tier
    }

    /// The RAM ring.
    #[must_use]
    pub const fn ring(&self) -> &EventRing {
        &self.ring
    }

    /// The open-span table.
    #[must_use]
    pub const fn spans(&self) -> &SpanTable {
        &self.spans
    }

    /// The counters.
    #[must_use]
    pub fn stats(&self) -> EventsStats {
        EventsStats {
            accepted: self.ring.accepted,
            displaced: self.ring.displaced,
            displaced_bytes: self.ring.displaced_bytes,
            stored: self.stored,
            dropped: self.dropped,
            bytes: self.tier.bytes(),
            ring_bytes: self.ring.bytes(),
            open_spans: u64::try_from(self.spans.len()).unwrap_or(u64::MAX),
            abandoned: self.spans.abandoned(),
        }
    }

    /// Record one event: validate it, push it into the ring, and append its payload.
    ///
    /// The *payload* is what makes the event durable; it must be a `proto` EVT frame (the
    /// store checks the four frame-header facts, and the consumer's decoder checks the
    /// sections). The typed [`Event`] is what the RAM ring answers, so a live subscriber
    /// costs a clone and never a decode.
    ///
    /// # Errors
    ///
    /// The vocabulary refusals of [`Event::new`] ([`StoreError::EventVocabulary`],
    /// [`StoreError::EventVocabularyName`], [`StoreError::EventFieldTooLong`]),
    /// [`StoreError::PayloadFrameType`] for a payload that is not an EVT frame, and every
    /// append refusal.
    pub fn record(
        &mut self,
        event: &Event,
        payload: &[u8],
        epoch: u64,
        dict_generation: u32,
    ) -> Result<AppendOutcome, StoreError> {
        let job = Job::new(payload.to_vec(), event.t_mono_ns, Vec::new());
        let outcome = self.tier.append(&job, epoch, dict_generation)?;
        self.ring.push(event.clone())?;
        self.stored = self.stored.saturating_add(1);
        Ok(outcome)
    }

    /// Count a job the writer queue dropped (the store's one loss counter).
    pub fn count_dropped(&mut self, jobs: u64) {
        self.dropped = self.dropped.saturating_add(jobs);
    }

    /// Seal the open segment (§C.4's events cap acts on sealed segments, so a full tier keeps
    /// serving the segment it is writing to).
    ///
    /// # Errors
    ///
    /// Every seal refusal.
    pub fn seal(&mut self) -> Result<Option<String>, StoreError> {
        self.tier.seal_open_segment()
    }

    /// Enforce the events store's own bounds for a time.
    ///
    /// # Errors
    ///
    /// Every eviction refusal.
    pub fn evict(&mut self, now_ns: i64) -> Result<crate::tier::Evicted, StoreError> {
        self.tier.evict(now_ns)
    }

    /// The events in a real-time window, bounded by `max_records` and `max_bytes`.
    ///
    /// # Errors
    ///
    /// Every [`Tier::range`] refusal.
    pub fn range(
        &self,
        from: i64,
        to: i64,
        max_records: usize,
        max_bytes: usize,
    ) -> Result<crate::tier::TierRange, StoreError> {
        self.tier.range(from, to, max_records, max_bytes)
    }

    /// The read bound a scan of one events segment uses.
    #[must_use]
    pub const fn max_file_bytes(&self) -> usize {
        self.max_file_bytes
    }
}

#[cfg(test)]
mod tests {
    use super::{
        check_bounded, check_name, Event, EventKind, EventRing, EventsStore, Outcome, SpanTable,
        EVENT_OVERHEAD, MAX_ATTRS, MAX_CODE, MAX_KEY, MAX_NAME, MAX_OPEN_SPANS,
        MAX_OPEN_SPANS_PER_ISSUER, MAX_RING_BYTES,
    };
    use crate::error::StoreError;
    use crate::format::{TierKind, EVT_FRAME_TYPE, FRAME_FLAG_SECTIONED};
    use crate::fsio::testdir::TempDir;
    use crate::fsio::StoreDir;
    use crate::tier::TierConfig;

    /// A minimal `proto` EVT frame: a 12-byte header and `data` zero bytes.
    fn evt(extra: usize) -> Vec<u8> {
        let len = 16 + extra;
        let mut frame = vec![0u8; len];
        let declared = u32::try_from(len).expect("fits");
        frame[0..4].copy_from_slice(&declared.to_le_bytes());
        frame[4] = EVT_FRAME_TYPE;
        frame[5] = FRAME_FLAG_SECTIONED;
        frame[8..12].copy_from_slice(&11u32.to_le_bytes());
        frame
    }

    fn begin(name: &str, key: &str, issuer: &str, t: i64) -> Event {
        Event::new(EventKind::Begin, name, Some(key), issuer, t, None).expect("begin")
    }

    fn end(name: &str, key: &str, issuer: &str, t: i64, outcome: Outcome) -> Event {
        Event::new(EventKind::End, name, Some(key), issuer, t, Some(outcome)).expect("end")
    }

    #[test]
    fn the_kind_and_outcome_vocabularies_are_closed_in_both_directions() {
        assert_eq!(EventKind::Begin.code(), 1);
        assert_eq!(EventKind::End.name(), "end");
        assert_eq!(EventKind::Point.code(), 3);
        assert_eq!(EventKind::from_code(1), Ok(EventKind::Begin));
        assert_eq!(EventKind::from_code(2), Ok(EventKind::End));
        assert_eq!(EventKind::from_code(3), Ok(EventKind::Point));
        for code in [0u8, 4, 9, 255] {
            assert_eq!(
                EventKind::from_code(code),
                Err(StoreError::EventVocabulary {
                    field: "kind",
                    found: code
                })
            );
        }
        for outcome in [
            Outcome::Ok,
            Outcome::Fail,
            Outcome::Error,
            Outcome::Skipped,
            Outcome::Cancelled,
            Outcome::Timeout,
            Outcome::Abandoned,
        ] {
            assert_eq!(Outcome::from_name(outcome.name()), Ok(outcome));
            assert_eq!(Outcome::from_code(outcome.code()), Ok(outcome));
            assert_eq!(outcome.to_string(), outcome.name());
        }
        assert_eq!(Outcome::Abandoned.code(), 6);
        for code in [7u8, 8, 255] {
            assert_eq!(
                Outcome::from_code(code),
                Err(StoreError::EventVocabulary {
                    field: "outcome",
                    found: code
                })
            );
        }
        assert_eq!(
            Outcome::from_name("nope"),
            Err(StoreError::EventVocabularyName {
                field: "outcome",
                found: "nope".into()
            })
        );
    }

    #[test]
    fn a_name_must_follow_the_plan_grammar_and_a_field_must_fit_its_bound() {
        for good in ["run", "candidate.build", "txn_step", "a1", "lane-x"] {
            assert_eq!(check_name(good), Ok(()), "{good}");
        }
        for bad in ["", "Run", "1run", "run!", "run space", ".run", "-run"] {
            assert_eq!(
                check_name(bad),
                Err(StoreError::EventVocabularyName {
                    field: "name",
                    found: bad.to_string()
                }),
                "{bad}"
            );
        }
        let too_long = "a".repeat(MAX_NAME + 1);
        assert_eq!(
            check_name(&too_long),
            Err(StoreError::EventFieldTooLong {
                field: "name",
                len: MAX_NAME + 1,
                max: MAX_NAME
            })
        );
        assert_eq!(MAX_NAME, 64);
        assert_eq!(MAX_KEY, 64);
        assert_eq!(MAX_CODE, 64);
        assert_eq!(MAX_ATTRS, 8);
        assert_eq!(check_bounded("key", &"k".repeat(MAX_KEY), MAX_KEY), Ok(()));
        assert_eq!(
            check_bounded("key", &"k".repeat(MAX_KEY + 1), MAX_KEY),
            Err(StoreError::EventFieldTooLong {
                field: "key",
                len: MAX_KEY + 1,
                max: MAX_KEY
            })
        );
    }

    #[test]
    fn an_event_is_refused_when_its_kind_and_outcome_disagree() {
        let point = Event::new(EventKind::Point, "run", None, "u1", 5, None).expect("point");
        assert_eq!(point.key, "run", "the key defaults to the name");
        assert_eq!(point.bytes(), 3 + 3 + 2 + EVENT_OVERHEAD);
        let begin_with_outcome =
            Event::new(EventKind::Begin, "run", None, "u1", 5, Some(Outcome::Ok));
        assert_eq!(
            begin_with_outcome,
            Err(StoreError::EventVocabulary {
                field: "outcome",
                found: 0
            })
        );
        let end_without_outcome = Event::new(EventKind::End, "run", None, "u1", 5, None);
        assert_eq!(
            end_without_outcome,
            Err(StoreError::EventVocabulary {
                field: "outcome",
                found: 0
            })
        );
        assert_eq!(
            Event::new(EventKind::Point, "run", None, "u1", -1, None),
            Err(StoreError::NegativeTime { ns: -1 })
        );
        assert_eq!(
            Event::new(EventKind::Point, "Run", None, "u1", 0, None),
            Err(StoreError::EventVocabularyName {
                field: "name",
                found: "Run".into()
            })
        );
        let coded = end("candidate", "k", "u1", 10, Outcome::Fail)
            .with_code("exit 2")
            .expect("a short code")
            .inferred();
        assert_eq!(coded.code.as_deref(), Some("exit 2"));
        assert!(coded.inferred);
        assert_eq!(
            end("candidate", "k", "u1", 10, Outcome::Fail).with_code(&"c".repeat(MAX_CODE + 1)),
            Err(StoreError::EventFieldTooLong {
                field: "code",
                len: MAX_CODE + 1,
                max: MAX_CODE
            })
        );
    }

    #[test]
    fn the_ring_is_bounded_and_reports_exactly_what_it_displaced() {
        let mut ring = EventRing::new(4 * (EVENT_OVERHEAD + 16)).expect("ring");
        assert!(ring.is_empty());
        assert_eq!(ring.capacity(), 4 * (EVENT_OVERHEAD + 16));
        let sizes: Vec<u64> = (0..4).map(|_| begin("a", "a", "u1", 1).bytes()).collect();
        assert_eq!(sizes[0], EVENT_OVERHEAD + 4, "\"a\" + \"a\" + \"u1\"");
        let mut displaced_total = 0u64;
        for _ in 0..9 {
            let evicted = ring.push(begin("aa", "aa", "u1", 1)).expect("push");
            displaced_total =
                displaced_total.saturating_add(u64::try_from(evicted.len()).unwrap_or(u64::MAX));
            assert!(
                ring.bytes() <= ring.capacity(),
                "the ring is over its bound"
            );
        }
        assert_eq!(displaced_total, 5);
        assert_eq!(ring.displaced(), 5);
        assert_eq!(ring.len(), 4);
        assert!(ring.displaced_bytes() > 0);
        let recent = ring.recent(2);
        assert_eq!(recent.len(), 2);
        assert_eq!(recent[0].name, "aa");
        assert_eq!(ring.recent(0).len(), 0);
        assert_eq!(
            EventRing::new(0).map(|_| ()),
            Err(StoreError::Config {
                key: "events_ring_bytes",
                reason: "must be at least one byte"
            })
        );
        assert_eq!(
            EventRing::new(MAX_RING_BYTES + 1).map(|_| ()),
            Err(StoreError::Config {
                key: "events_ring_bytes",
                reason: "is past the store's own bound"
            })
        );
        // An event larger than the whole ring is refused, never queued-then-dropped.
        let mut small = EventRing::new(EVENT_OVERHEAD).expect("ring");
        let oversized = begin("run", "run", "u1", 0)
            .with_code(&"c".repeat(64))
            .expect("code");
        let expected = oversized.bytes();
        assert_eq!(
            small.push(oversized),
            Err(StoreError::JobTooLarge {
                len: expected,
                max: EVENT_OVERHEAD
            })
        );
        assert!(small.is_empty(), "a refused push changed nothing");
    }

    #[test]
    fn the_span_table_closes_the_innermost_span_and_refuses_an_unknown_key() {
        let mut spans = SpanTable::new();
        assert!(spans.is_empty());
        assert_eq!(spans.len(), 0);
        spans
            .begin(&begin("candidate", "c", "u1", 100))
            .expect("outer");
        spans
            .begin(&begin("candidate.build", "b", "u1", 150))
            .expect("inner");
        assert_eq!(spans.len(), 2);
        assert_eq!(spans.open_for("u1"), 2);
        assert_eq!(spans.open_for("u2"), 0);
        assert_eq!(
            spans.peek("u1", "b").map(|span| span.name.as_str()),
            Some("candidate.build")
        );
        let closed = spans
            .end(&end("candidate.build", "b", "u1", 400, Outcome::Ok))
            .expect("close");
        assert_eq!(closed.span.name, "candidate.build");
        assert_eq!(closed.duration_ns, 250);
        assert_eq!(closed.event.t_mono_ns, 400);
        assert_eq!(spans.len(), 1);
        assert_eq!(spans.open_for("u1"), 1);
        let closed_outer = spans
            .end(&end("candidate", "c", "u1", 900, Outcome::Fail))
            .expect("close outer");
        assert_eq!(closed_outer.duration_ns, 800);
        assert!(spans.is_empty());
        assert_eq!(spans.open_for("u1"), 0);
        assert_eq!(
            spans.end(&end("candidate", "c", "u1", 1_000, Outcome::Ok)),
            Err(StoreError::SpanUnknown {
                issuer: "u1".into(),
                key: "c".into()
            })
        );
        assert_eq!(
            spans.end(&end("candidate", "c", "u9", 1_000, Outcome::Ok)),
            Err(StoreError::SpanUnknown {
                issuer: "u9".into(),
                key: "c".into()
            })
        );
    }

    #[test]
    fn a_nested_span_of_the_same_name_closes_last_in_first_out() {
        let mut spans = SpanTable::new();
        spans.begin(&begin("run", "run", "u1", 10)).expect("outer");
        spans.begin(&begin("run", "run", "u1", 20)).expect("inner");
        assert_eq!(spans.peek("u1", "run").map(|span| span.t_mono_ns), Some(20));
        let first = spans
            .end(&end("run", "run", "u1", 30, Outcome::Ok))
            .expect("inner closes");
        assert_eq!(first.span.t_mono_ns, 20);
        assert_eq!(first.duration_ns, 10);
        assert_eq!(spans.peek("u1", "run").map(|span| span.t_mono_ns), Some(10));
        let second = spans
            .end(&end("run", "run", "u1", 40, Outcome::Ok))
            .expect("outer closes");
        assert_eq!(second.duration_ns, 30);
        assert!(spans.is_empty());
    }

    #[test]
    fn a_span_table_bound_is_a_refusal_not_an_eviction() {
        let mut spans = SpanTable::new();
        for index in 0..MAX_OPEN_SPANS_PER_ISSUER {
            spans
                .begin(&begin("lane", &format!("lane-{index}"), "u1", 1))
                .expect("under the bound");
        }
        assert_eq!(spans.len(), MAX_OPEN_SPANS_PER_ISSUER);
        assert_eq!(
            spans.begin(&begin("lane", "one-more", "u1", 1)),
            Err(StoreError::SpanLimit {
                issuer: "u1".into(),
                open: MAX_OPEN_SPANS_PER_ISSUER,
                max: MAX_OPEN_SPANS_PER_ISSUER,
                global: false
            })
        );
        // Another issuer still fits: the bound is per issuer, not per table, until the
        // global bound binds.
        spans
            .begin(&begin("lane", "lane-0", "u2", 1))
            .expect("other issuer");
        assert_eq!(spans.open_for("u1"), MAX_OPEN_SPANS_PER_ISSUER);
        assert_eq!(spans.open_for("u2"), 1);
    }

    #[test]
    fn the_global_span_bound_refuses_even_a_fresh_issuer() {
        let mut spans = SpanTable::new();
        let issuers = MAX_OPEN_SPANS / 8;
        for issuer in 0..issuers {
            for slot in 0..8 {
                spans
                    .begin(&begin(
                        "lane",
                        &format!("lane-{slot}"),
                        &format!("u{issuer}"),
                        1,
                    ))
                    .expect("under the global bound");
            }
        }
        assert_eq!(spans.len(), MAX_OPEN_SPANS);
        assert_eq!(
            spans.begin(&begin("lane", "lane", "u-fresh", 1)),
            Err(StoreError::SpanLimit {
                issuer: "u-fresh".into(),
                open: MAX_OPEN_SPANS,
                max: MAX_OPEN_SPANS,
                global: true
            })
        );
        assert_eq!(spans.len(), MAX_OPEN_SPANS);
    }

    #[test]
    fn abandoning_an_issuer_closes_every_span_it_left_open_as_inferred() {
        let mut spans = SpanTable::new();
        spans.begin(&begin("run", "run", "u1", 100)).expect("open");
        spans
            .begin(&begin("lane", "lane", "u1", 200))
            .expect("open");
        spans.begin(&begin("txn", "txn", "u2", 300)).expect("open");
        let inferred = spans.abandon_issuer("u1", 1_000);
        assert_eq!(inferred.len(), 2, "both of u1's spans close");
        for event in &inferred {
            assert!(event.inferred);
            assert_eq!(event.outcome, Some(Outcome::Abandoned));
            assert_eq!(event.kind, EventKind::End);
            assert!(
                event.t_mono_ns >= 200,
                "the close is never before the start"
            );
        }
        assert_eq!(spans.abandoned(), 2);
        assert_eq!(spans.len(), 1, "u2's span is untouched");
        assert_eq!(spans.open_for("u1"), 0);
        assert_eq!(spans.open_for("u2"), 1);
        assert!(spans.abandon_issuer("u1", 5).is_empty());
        assert!(spans.abandon_issuer("nobody", 5).is_empty());
    }

    #[test]
    fn an_events_store_records_ring_and_persists_in_its_own_tier() {
        let temp = TempDir::new("store");
        let config =
            TierConfig::new("events", TierKind::Events, 60, 3600, 1 << 20, false).expect("config");
        let mut store = EventsStore::open(
            config,
            EVENT_OVERHEAD * 16,
            StoreDir::open(temp.path(), false).expect("dir"),
            1 << 22,
        )
        .expect("events store");
        let first = begin("run", "run", "u1", 0);
        store
            .record(&first, &evt(8), 3, 1)
            .expect("record an event");
        let stats = store.stats();
        assert_eq!(stats.stored, 1);
        assert_eq!(
            stats.open_spans, 0,
            "recording does not open a span by itself"
        );
        assert_eq!(stats.accepted, 1);
        assert_eq!(stats.ring_bytes, first.bytes());
        assert!(stats.displaced == 0);
        // The payload was appended as an EVT-framed record in an events segment.
        assert_eq!(store.tier().scan().segments.len(), 1);
        assert_eq!(store.tier().scan().segments[0].parsed.tier, "events");
        // A BLOCK frame is refused by the events corpus.
        let mut block = evt(0);
        block[4] = crate::format::BLOCK_FRAME_TYPE;
        assert_eq!(
            store.record(&first, &block, 3, 1),
            Err(StoreError::PayloadFrameType {
                expected: EVT_FRAME_TYPE,
                found: crate::format::BLOCK_FRAME_TYPE
            })
        );
        assert_eq!(
            store.stats().stored,
            1,
            "the refused record changed nothing"
        );
        store.count_dropped(3);
        assert_eq!(store.stats().dropped, 3);
        store.seal().expect("seal");
        assert_eq!(store.tier().scan().segments.len(), 1);
        let windows = store.range(0, 0, 8, 1 << 20).expect("range");
        assert_eq!(windows.len(), 1);
        assert_eq!(windows.payloads()[0], evt(8));
        let empty = store
            .range(1_000_000_000, 2_000_000_000, 8, 1 << 20)
            .expect("range");
        assert!(empty.is_empty());
        assert_eq!(store.max_file_bytes(), 1 << 22);
        assert!(store.tier().reachable().is_empty());
        // A second handle on the same directory opens its own tier over the same scan.
        let second_config =
            TierConfig::new("events", TierKind::Events, 60, 3600, 1 << 20, false).expect("config");
        assert!(
            EventsStore::open(
                second_config,
                EVENT_OVERHEAD,
                StoreDir::open(temp.path(), false).expect("dir"),
                1 << 22,
            )
            .is_ok(),
            "one events tier per directory is enough"
        );
    }

    #[test]
    fn an_events_store_refuses_a_history_configuration() {
        let temp = TempDir::new("wrong-kind");
        let config = TierConfig::history("events", 60, 3600, 1 << 20, false).expect("config");
        assert_eq!(
            EventsStore::open(
                config,
                EVENT_OVERHEAD,
                StoreDir::open(temp.path(), false).expect("dir"),
                1 << 22
            )
            .map(|_| ()),
            Err(StoreError::TierKindMismatch {
                tier: "events",
                payload: "history"
            })
        );
    }
}
