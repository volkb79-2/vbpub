//! The connection-owned annotation engine (WIRE.md §4.12).
//!
//! The engine has no I/O of its own. Its sink is the one append operation the probe performs
//! synchronously on the server thread; tests can supply a sink that refuses an append.

use std::collections::BTreeMap;

use hostobs_proto::event::encode_event_frame;

use crate::dedupe::{EventDedup, Reply};
use crate::error::StoreError;
use crate::events::{ConnectionSpanTable, CtxKey, Event, EventKind, EventPeer, OpenSpan, Outcome};
use crate::store::Store;
use crate::tier::AppendOutcome;

/// The maximum number of non-full uid buckets retained by one probe.
pub const MAX_ANNOTATE_BUCKETS: usize = 1024;
const NS_PER_SECOND: u64 = 1_000_000_000;
const NS_PER_MILLISECOND: u64 = 1_000_000;

/// A checked event request after the serving schema has been read.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EventRequest {
    /// Event kind.
    pub kind: EventKind,
    /// Event name.
    pub name: String,
    /// Explicit key, or `None` to use the event name.
    pub key: Option<String>,
    /// Client-written context.
    pub ctx: BTreeMap<CtxKey, String>,
    /// End outcome, required only for an end.
    pub outcome: Option<Outcome>,
    /// Optional event code.
    pub code: Option<String>,
    /// Client-written attributes.
    pub attrs: BTreeMap<String, String>,
    /// Validated point-only `(boot_id, seq)` idempotency key.
    pub idempotency: Option<(String, u64)>,
}
/// One successful annotation reply.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct EventReply {
    /// Server-stamped monotonic nanoseconds.
    pub t_mono_ns: u64,
    /// Opened or closed span id, or `None` for a point.
    pub span: Option<u64>,
}

/// A typed refusal from the annotation engine.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AnnotateError {
    /// The uid's shared token bucket has insufficient credit.
    RateLimited {
        /// Milliseconds until one token is available.
        retry_after_ms: u32,
    },
    /// The per-uid or global open-span table is full.
    Budget,
    /// No matching span belongs to this connection.
    SpanUnknown,
    /// Event construction, encoding or append failed.
    Internal,
}

/// A configuration value outside the annotation bucket bounds.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AnnotatorConfigError {
    /// `annotate_rate_per_s` must be in `1..=1000`.
    Rate,
    /// `annotate_burst` must be in `1..=1024`.
    Burst,
}

/// The append seam used by [`Annotator`].
pub trait EventSink {
    /// Append one event frame at its server-derived real timestamp.
    fn record_event(
        &mut self,
        event: &Event,
        payload: &[u8],
        real_ns: i64,
    ) -> Result<AppendOutcome, StoreError>;
}

impl EventSink for Store {
    fn record_event(
        &mut self,
        event: &Event,
        payload: &[u8],
        real_ns: i64,
    ) -> Result<AppendOutcome, StoreError> {
        Store::record_event(self, event, payload, real_ns)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct Bucket {
    credit_ns: u64,
    last_ns: u64,
}

impl Bucket {
    fn refill(&mut self, now_ns: u64, capacity_ns: u64) {
        let effective_now = self.last_ns.max(now_ns);
        let elapsed = effective_now.saturating_sub(self.last_ns);
        self.credit_ns = self.credit_ns.saturating_add(elapsed).min(capacity_ns);
        self.last_ns = effective_now;
    }
}

/// Point-event dedupe, uid token buckets, and connection-owned spans for one probe run.
#[derive(Debug)]
pub struct Annotator {
    dedupe: EventDedup,
    buckets: BTreeMap<u32, Bucket>,
    spans: ConnectionSpanTable,
    next_span: u64,
    rate_per_s: u32,
    burst: u32,
    cost_ns: u64,
    capacity_ns: u64,
}

impl Annotator {
    /// Create a fresh engine with required `[limits]` settings.
    ///
    /// # Errors
    ///
    /// [`AnnotatorConfigError::Rate`] or [`AnnotatorConfigError::Burst`] when a setting is out
    /// of its declared range.
    pub fn new(rate_per_s: u32, burst: u32) -> Result<Annotator, AnnotatorConfigError> {
        if !(1..=1000).contains(&rate_per_s) {
            return Err(AnnotatorConfigError::Rate);
        }
        if !(1..=1024).contains(&burst) {
            return Err(AnnotatorConfigError::Burst);
        }
        let cost_ns = NS_PER_SECOND / u64::from(rate_per_s);
        let capacity_ns = u64::from(burst).saturating_mul(cost_ns);
        Ok(Annotator {
            dedupe: EventDedup::new(),
            buckets: BTreeMap::new(),
            spans: ConnectionSpanTable::new(),
            next_span: 1,
            rate_per_s,
            burst,
            cost_ns,
            capacity_ns,
        })
    }

    /// The configured sustained rate.
    #[must_use]
    pub const fn rate_per_s(&self) -> u32 {
        self.rate_per_s
    }

    /// The configured burst size.
    #[must_use]
    pub const fn burst(&self) -> u32 {
        self.burst
    }

    /// Number of currently retained non-full uid buckets.
    #[must_use]
    pub fn bucket_count(&self) -> usize {
        self.buckets.len()
    }

    /// Number of open spans across the probe.
    #[must_use]
    pub const fn open_spans(&self) -> usize {
        self.spans.len()
    }

    /// Number of open spans held by `uid` across its connections.
    #[must_use]
    pub fn open_spans_for_uid(&self, uid: u32) -> usize {
        self.spans.open_for_uid(uid)
    }

    /// Handle one schema-checked annotation. The event is committed before any span or dedupe
    /// state changes; a failed append leaves those tables untouched and keeps the spent token.
    pub fn annotate<S: EventSink>(
        &mut self,
        sink: &mut S,
        conn: u64,
        uid: u32,
        peer: EventPeer,
        request: &EventRequest,
        t_mono_ns: u64,
        real_ns: i64,
    ) -> Result<EventReply, AnnotateError> {
        if let Some((boot_id, seq)) = &request.idempotency {
            if request.kind != EventKind::Point {
                return Err(AnnotateError::Internal);
            }
            if let Some(reply) = self
                .dedupe
                .peek(uid, &request.name, boot_id, *seq, t_mono_ns)
            {
                return Ok(reply_from_cache(reply)?);
            }
        }

        self.consume(uid, t_mono_ns)?;
        let effective_key = request.key.as_deref().unwrap_or(&request.name);
        let span = match request.kind {
            EventKind::Begin => {
                self.spans
                    .can_open(uid, &format!("uid:{uid}"))
                    .map_err(|_| AnnotateError::Budget)?;
                let span = self.next_span;
                self.next_span = self
                    .next_span
                    .checked_add(1)
                    .ok_or(AnnotateError::Internal)?;
                Some(span)
            }
            EventKind::End => Some(
                self.spans
                    .peek(conn, effective_key)
                    .map(|open| open.span)
                    .ok_or(AnnotateError::SpanUnknown)?,
            ),
            EventKind::Point => None,
        };
        let span_id = span.unwrap_or(0);
        let t_mono_ns_i64 = i64::try_from(t_mono_ns).map_err(|_| AnnotateError::Internal)?;
        let mut event = Event::new_record(
            request.kind,
            &request.name,
            Some(effective_key),
            uid,
            t_mono_ns_i64,
            real_ns,
            request.outcome,
            span_id,
            Some(peer),
            request.ctx.clone(),
            request.attrs.clone(),
        )
        .map_err(|_| AnnotateError::Internal)?;
        if let Some(code) = &request.code {
            event = event.with_code(code).map_err(|_| AnnotateError::Internal)?;
        }
        let payload = encode_event_frame(&event.to_payload().map_err(|_| AnnotateError::Internal)?)
            .map_err(|_| AnnotateError::Internal)?;
        sink.record_event(&event, &payload, real_ns)
            .map_err(|_| AnnotateError::Internal)?;

        match request.kind {
            EventKind::Begin => self.spans.open_after_append(conn, &event),
            EventKind::End => {
                let _ = self.spans.close(conn, effective_key);
            }
            EventKind::Point => {}
        }
        if let Some((boot_id, seq)) = &request.idempotency {
            let reply = Reply {
                t_mono_ns: t_mono_ns_i64,
                span: None,
            };
            let _ = self
                .dedupe
                .insert(uid, &request.name, boot_id, *seq, reply, t_mono_ns);
        } else {
            self.dedupe.expire(t_mono_ns);
        }
        Ok(EventReply { t_mono_ns, span })
    }

    /// Infer and append one abandoned end for every span owned by `conn`. Spans are removed
    /// before appends so an append failure cannot leave a dead connection's span open.
    pub fn connection_closed<S: EventSink>(
        &mut self,
        sink: &mut S,
        conn: u64,
        t_mono_ns: u64,
        real_ns: i64,
    ) {
        let spans = self.spans.take_connection(conn);
        let Ok(t_mono_ns) = i64::try_from(t_mono_ns) else {
            return;
        };
        for span in spans {
            self.append_inferred(sink, &span, t_mono_ns, real_ns);
        }
    }

    /// Discard a connection's open spans when its close timestamp cannot be represented.
    pub fn forget_connection(&mut self, conn: u64) {
        let _ = self.spans.take_connection(conn);
    }

    fn append_inferred<S: EventSink>(
        &mut self,
        sink: &mut S,
        span: &OpenSpan,
        t_mono_ns: i64,
        real_ns: i64,
    ) {
        let Ok(event) = span.inferred_end(t_mono_ns, real_ns) else {
            return;
        };
        let Ok(payload) = event
            .to_payload()
            .and_then(|payload| encode_event_frame(&payload))
        else {
            return;
        };
        let _ = sink.record_event(&event, &payload, real_ns);
    }

    fn consume(&mut self, uid: u32, now_ns: u64) -> Result<(), AnnotateError> {
        for bucket in self.buckets.values_mut() {
            bucket.refill(now_ns, self.capacity_ns);
        }
        self.buckets
            .retain(|_, bucket| bucket.credit_ns < self.capacity_ns);

        if let Some(bucket) = self.buckets.get_mut(&uid) {
            if bucket.credit_ns < self.cost_ns {
                let deficit = self.cost_ns.saturating_sub(bucket.credit_ns);
                return Err(AnnotateError::RateLimited {
                    retry_after_ms: ceil_ms(deficit),
                });
            }
            bucket.credit_ns = bucket.credit_ns.saturating_sub(self.cost_ns);
            return Ok(());
        }

        if self.buckets.len() >= MAX_ANNOTATE_BUCKETS {
            return Err(AnnotateError::RateLimited {
                retry_after_ms: ceil_ms(self.cost_ns),
            });
        }
        self.buckets.insert(
            uid,
            Bucket {
                credit_ns: self.capacity_ns.saturating_sub(self.cost_ns),
                last_ns: now_ns,
            },
        );
        Ok(())
    }
}

fn reply_from_cache(reply: Reply) -> Result<EventReply, AnnotateError> {
    Ok(EventReply {
        t_mono_ns: u64::try_from(reply.t_mono_ns).map_err(|_| AnnotateError::Internal)?,
        span: reply.span,
    })
}

fn ceil_ms(ns: u64) -> u32 {
    let milliseconds = ns.saturating_add(NS_PER_MILLISECOND.saturating_sub(1)) / NS_PER_MILLISECOND;
    u32::try_from(milliseconds).expect("annotation retry delay fits u32")
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use hostobs_proto::event::decode_event_frame;

    use super::{
        AnnotateError, Annotator, EventReply, EventRequest, EventSink, MAX_ANNOTATE_BUCKETS,
        NS_PER_MILLISECOND,
    };
    use crate::error::StoreError;
    use crate::events::{CtxKey, Event, EventCgroupKey, EventKind, EventPeer, Outcome};
    use crate::tier::{AppendOutcome, Evicted};

    #[derive(Default)]
    struct MemorySink {
        events: Vec<Event>,
        payloads: Vec<Vec<u8>>,
        real_times: Vec<i64>,
        fail_next: bool,
    }

    impl EventSink for MemorySink {
        fn record_event(
            &mut self,
            event: &Event,
            payload: &[u8],
            real_ns: i64,
        ) -> Result<AppendOutcome, StoreError> {
            if self.fail_next {
                self.fail_next = false;
                return Err(StoreError::Io {
                    op: "append",
                    os_code: None,
                    message: String::from("injected failure"),
                });
            }
            self.events.push(event.clone());
            self.payloads.push(payload.to_vec());
            self.real_times.push(real_ns);
            Ok(AppendOutcome {
                segment: String::from("events-0-0.seg.open"),
                sealed: None,
                records: 1,
                bytes_written: 0,
                evicted: Evicted::default(),
            })
        }
    }

    fn peer() -> EventPeer {
        EventPeer {
            pid: None,
            comm: None,
            cgroup: None,
        }
    }

    fn request(kind: EventKind, name: &str, key: Option<&str>) -> EventRequest {
        EventRequest {
            kind,
            name: name.to_string(),
            key: key.map(str::to_string),
            ctx: BTreeMap::new(),
            outcome: (kind == EventKind::End).then_some(Outcome::Ok),
            code: None,
            attrs: BTreeMap::new(),
            idempotency: None,
        }
    }

    fn annotate(
        annotator: &mut Annotator,
        sink: &mut MemorySink,
        conn: u64,
        uid: u32,
        request: &EventRequest,
        now_ns: u64,
    ) -> Result<EventReply, AnnotateError> {
        annotator.annotate(sink, conn, uid, peer(), request, now_ns, 10_000)
    }

    fn expect_ok(
        annotator: &mut Annotator,
        sink: &mut MemorySink,
        conn: u64,
        uid: u32,
        request: &EventRequest,
        now_ns: u64,
    ) -> EventReply {
        annotate(annotator, sink, conn, uid, request, now_ns).expect("annotation accepted")
    }

    #[test]
    fn one_uid_bucket_is_shared_across_connections_and_refills_by_fake_time() {
        let mut annotator = Annotator::new(50, 64).expect("config");
        let mut sink = MemorySink::default();
        let point = request(EventKind::Point, "run", None);
        for index in 0..64_u64 {
            expect_ok(&mut annotator, &mut sink, index % 2, 9, &point, 1_000);
        }
        assert_eq!(sink.events[0].peer, Some(peer()));
        assert_eq!(
            annotate(&mut annotator, &mut sink, 1, 9, &point, 1_000),
            Err(AnnotateError::RateLimited { retry_after_ms: 20 })
        );
        assert_eq!(
            annotate(&mut annotator, &mut sink, 2, 9, &point, 0),
            Err(AnnotateError::RateLimited { retry_after_ms: 20 }),
            "a backward clock and a reconnect do not refill the bucket"
        );
        expect_ok(
            &mut annotator,
            &mut sink,
            2,
            9,
            &point,
            1_000 + 20 * NS_PER_MILLISECOND,
        );
        assert_eq!(sink.events.len(), 65);
    }

    #[test]
    fn an_idempotency_hit_precedes_rate_even_when_the_uid_bucket_is_empty() {
        let mut annotator = Annotator::new(50, 64).expect("config");
        let mut sink = MemorySink::default();
        let mut keyed = request(EventKind::Point, "thrashguard.freeze", None);
        keyed
            .attrs
            .insert(String::from("boot_id"), String::from("boot"));
        keyed.attrs.insert(String::from("seq"), String::from("7"));
        keyed.idempotency = Some((String::from("boot"), 7));
        let first = expect_ok(&mut annotator, &mut sink, 1, 9, &keyed, 0);
        let ordinary = request(EventKind::Point, "candidate.run", None);
        for _ in 0..63 {
            expect_ok(&mut annotator, &mut sink, 1, 9, &ordinary, 0);
        }

        assert_eq!(
            annotate(&mut annotator, &mut sink, 2, 9, &keyed, 0),
            Ok(first)
        );
        assert_eq!(sink.events.len(), 64);
        assert_eq!(
            annotate(&mut annotator, &mut sink, 2, 9, &ordinary, 0),
            Err(AnnotateError::RateLimited { retry_after_ms: 20 })
        );
    }

    #[test]
    fn budget_and_span_unknown_refusals_each_spend_a_token() {
        let mut annotator = Annotator::new(50, 66).expect("config");
        let mut sink = MemorySink::default();
        let begin = request(EventKind::Begin, "run", Some("job"));
        for index in 0..64_u64 {
            expect_ok(&mut annotator, &mut sink, index % 2, 9, &begin, 5);
        }
        assert_eq!(annotator.open_spans_for_uid(9), 64);
        assert_eq!(
            annotate(&mut annotator, &mut sink, 2, 9, &begin, 5),
            Err(AnnotateError::Budget)
        );
        let unknown = request(EventKind::End, "run", Some("missing"));
        assert_eq!(
            annotate(&mut annotator, &mut sink, 2, 9, &unknown, 5),
            Err(AnnotateError::SpanUnknown)
        );
        let point = request(EventKind::Point, "lane", None);
        assert_eq!(
            annotate(&mut annotator, &mut sink, 2, 9, &point, 5),
            Err(AnnotateError::RateLimited { retry_after_ms: 20 })
        );
    }

    #[test]
    fn span_limits_are_per_uid_across_connections_and_probe_wide() {
        let mut annotator = Annotator::new(50, 64).expect("config");
        let mut sink = MemorySink::default();
        let begin = request(EventKind::Begin, "run", None);
        for uid in 0..64_u32 {
            for _ in 0..64 {
                expect_ok(&mut annotator, &mut sink, u64::from(uid), uid, &begin, 0);
            }
        }
        assert_eq!(annotator.open_spans(), 4_096);
        assert_eq!(
            annotate(&mut annotator, &mut sink, 64, 64, &begin, 0),
            Err(AnnotateError::Budget),
            "the global span cap applies after a new uid spends a token"
        );
        assert_eq!(annotator.open_spans_for_uid(64), 0);
    }

    #[test]
    fn nested_end_closes_innermost_span_and_uses_the_end_request_fields() {
        let mut annotator = Annotator::new(50, 64).expect("config");
        let mut sink = MemorySink::default();
        let outer = request(EventKind::Begin, "candidate", Some("shared"));
        let inner = request(EventKind::Begin, "candidate.build", Some("shared"));
        let first = expect_ok(&mut annotator, &mut sink, 4, 7, &outer, 10);
        let second = expect_ok(&mut annotator, &mut sink, 4, 7, &inner, 11);
        assert_eq!((first.span, second.span), (Some(1), Some(2)));

        let mut end = request(EventKind::End, "candidate.done", Some("shared"));
        end.outcome = Some(Outcome::Fail);
        end.ctx.insert(CtxKey::Run, String::from("run-end"));
        end.attrs
            .insert(String::from("phase"), String::from("close"));
        let closed = expect_ok(&mut annotator, &mut sink, 4, 7, &end, 12);
        assert_eq!(closed.span, Some(2));
        assert_eq!(sink.events[2].name, "candidate.done");
        assert_eq!(
            sink.events[2].ctx.get(&CtxKey::Run).map(String::as_str),
            Some("run-end")
        );
        assert_eq!(
            sink.events[2].attrs.get("phase").map(String::as_str),
            Some("close")
        );
        assert_eq!(annotator.open_spans(), 1);
        let closed = expect_ok(&mut annotator, &mut sink, 4, 7, &end, 13);
        assert_eq!(closed.span, Some(1));
        assert_eq!(annotator.open_spans(), 0);
        assert_eq!(
            annotate(&mut annotator, &mut sink, 5, 7, &end, 14),
            Err(AnnotateError::SpanUnknown)
        );
    }

    #[test]
    fn same_sequence_on_freeze_and_thaw_is_distinct_but_freeze_repeat_is_a_hit() {
        let mut annotator = Annotator::new(50, 64).expect("config");
        let mut sink = MemorySink::default();
        let mut freeze = request(EventKind::Point, "thrashguard.freeze", Some("unit"));
        freeze
            .attrs
            .insert(String::from("boot_id"), String::from("boot"));
        freeze.attrs.insert(String::from("seq"), String::from("7"));
        freeze.idempotency = Some((String::from("boot"), 7));
        let first = expect_ok(&mut annotator, &mut sink, 1, 7, &freeze, 10);

        let mut thaw = request(EventKind::Point, "thrashguard.thaw", Some("unit"));
        thaw.attrs
            .insert(String::from("boot_id"), String::from("boot"));
        thaw.attrs.insert(String::from("seq"), String::from("7"));
        thaw.idempotency = Some((String::from("boot"), 7));
        expect_ok(&mut annotator, &mut sink, 1, 7, &thaw, 11);

        assert_eq!(
            annotate(&mut annotator, &mut sink, 2, 7, &freeze, 12),
            Ok(first)
        );
        assert_eq!(sink.events.len(), 2);
        assert_eq!(sink.events[0].name, "thrashguard.freeze");
        assert_eq!(sink.events[1].name, "thrashguard.thaw");
    }

    #[test]
    fn failed_append_keeps_state_unchanged_but_spends_token_and_skips_span_id() {
        let mut annotator = Annotator::new(50, 2).expect("config");
        let mut sink = MemorySink {
            fail_next: true,
            ..MemorySink::default()
        };
        let begin = request(EventKind::Begin, "run", None);
        assert_eq!(
            annotate(&mut annotator, &mut sink, 1, 7, &begin, 10),
            Err(AnnotateError::Internal)
        );
        assert_eq!(annotator.open_spans(), 0);
        let opened = expect_ok(&mut annotator, &mut sink, 1, 7, &begin, 10);
        assert_eq!(opened.span, Some(2));
        assert_eq!(annotator.open_spans(), 1);
        assert_eq!(
            annotate(
                &mut annotator,
                &mut sink,
                1,
                7,
                &request(EventKind::Point, "candidate", None),
                10
            ),
            Err(AnnotateError::RateLimited { retry_after_ms: 20 })
        );
    }

    #[test]
    fn failed_keyed_append_does_not_create_a_dedupe_entry() {
        let mut annotator = Annotator::new(50, 2).expect("config");
        let mut sink = MemorySink {
            fail_next: true,
            ..MemorySink::default()
        };
        let mut point = request(EventKind::Point, "lane", None);
        point.idempotency = Some((String::from("boot"), 1));
        assert_eq!(
            annotate(&mut annotator, &mut sink, 1, 7, &point, 10),
            Err(AnnotateError::Internal)
        );
        let first = expect_ok(&mut annotator, &mut sink, 1, 7, &point, 10);
        assert_eq!(
            annotate(&mut annotator, &mut sink, 2, 7, &point, 11),
            Ok(first)
        );
        assert_eq!(sink.events.len(), 1);
    }

    #[test]
    fn failed_end_append_keeps_the_span_open_until_a_successful_append() {
        let mut annotator = Annotator::new(50, 3).expect("config");
        let mut sink = MemorySink::default();
        let begin = request(EventKind::Begin, "run", Some("job"));
        expect_ok(&mut annotator, &mut sink, 1, 7, &begin, 10);
        let end = request(EventKind::End, "run", Some("job"));
        sink.fail_next = true;

        assert_eq!(
            annotate(&mut annotator, &mut sink, 1, 7, &end, 11),
            Err(AnnotateError::Internal)
        );
        assert_eq!(annotator.open_spans(), 1);
        let closed = expect_ok(&mut annotator, &mut sink, 1, 7, &end, 11);
        assert_eq!(closed.span, Some(1));
        assert_eq!(annotator.open_spans(), 0);
        assert_eq!(sink.events.len(), 2);
    }

    #[test]
    fn disconnect_writes_inferred_ends_with_identity_in_open_order_and_close_time() {
        let mut annotator = Annotator::new(50, 64).expect("config");
        let mut sink = MemorySink::default();
        let mut begin = request(EventKind::Begin, "candidate", Some("outer"));
        begin.ctx.insert(CtxKey::Run, String::from("run-7"));
        begin
            .attrs
            .insert(String::from("txn"), String::from("txn-9"));
        let peer = EventPeer {
            pid: Some(17),
            comm: Some(String::from("hostobs")),
            cgroup: Some(EventCgroupKey { dev: 4, inode: 9 }),
        };
        let first = annotator
            .annotate(&mut sink, 4, 7, peer.clone(), &begin, 10, 100)
            .expect("begin");
        let mut second = request(EventKind::Begin, "candidate.build", Some("inner"));
        second.ctx.insert(CtxKey::Run, String::from("run-7"));
        annotator
            .annotate(&mut sink, 4, 7, peer, &second, 11, 101)
            .expect("nested begin");

        annotator.connection_closed(&mut sink, 4, 20, 900);
        assert_eq!(annotator.open_spans(), 0);
        assert_eq!(sink.events.len(), 4);
        let inferred = &sink.events[2..];
        assert_eq!(
            inferred.iter().map(|event| event.span).collect::<Vec<_>>(),
            vec![first.span.expect("span"), 2]
        );
        assert!(inferred.iter().all(|event| {
            event.kind == EventKind::End
                && event.outcome == Some(Outcome::Abandoned)
                && event.inferred
                && event.uid == 7
                && event.issuer == "uid:7"
                && event.real_ns == 900
                && event.peer.as_ref().and_then(|peer| peer.pid) == Some(17)
                && event.ctx.get(&CtxKey::Run).map(String::as_str) == Some("run-7")
        }));
        assert_eq!(inferred[0].name, "candidate");
        assert_eq!(inferred[0].key, "outer");
        assert_eq!(
            inferred[0].attrs.get("txn").map(String::as_str),
            Some("txn-9")
        );
        assert_eq!(&sink.real_times[2..], &[900, 900]);

        let payload = decode_event_frame(&sink.payloads[2]).expect("event/1 end payload");
        assert_eq!(payload.span, first.span.unwrap_or_default());
        assert_eq!(payload.uid, 7);
        assert_eq!(payload.peer_pid, 17);
        assert_eq!(payload.peer_dev, 4);
        assert_eq!(payload.peer_inode, 9);
        assert_eq!(payload.name, "candidate");
        assert_eq!(payload.key, "outer");
        assert_eq!(payload.ctx.len(), 1);
        assert_eq!(payload.attrs.len(), 1);
        assert!(payload.inferred);
    }

    #[test]
    fn disconnect_removes_every_span_even_when_an_inferred_append_fails() {
        let mut annotator = Annotator::new(50, 64).expect("config");
        let mut sink = MemorySink::default();
        let begin = request(EventKind::Begin, "run", None);
        expect_ok(&mut annotator, &mut sink, 1, 7, &begin, 0);
        expect_ok(&mut annotator, &mut sink, 1, 7, &begin, 0);
        sink.fail_next = true;
        annotator.connection_closed(&mut sink, 1, 2, 3);
        assert_eq!(annotator.open_spans(), 0);
        assert_eq!(sink.events.len(), 3);
        assert_eq!(sink.events[2].span, 2);
        assert!(sink.events[2].inferred);
    }

    #[test]
    fn a_new_uid_is_refused_after_1024_non_full_buckets_then_full_buckets_are_dropped() {
        let mut annotator = Annotator::new(50, 64).expect("config");
        let mut sink = MemorySink::default();
        let point = request(EventKind::Point, "run", None);
        for uid in 0..MAX_ANNOTATE_BUCKETS {
            expect_ok(
                &mut annotator,
                &mut sink,
                u64::try_from(uid).expect("connection id"),
                u32::try_from(uid).expect("uid"),
                &point,
                0,
            );
        }
        assert_eq!(annotator.bucket_count(), MAX_ANNOTATE_BUCKETS);
        assert_eq!(
            annotate(&mut annotator, &mut sink, 2_000, 2_000, &point, 0),
            Err(AnnotateError::RateLimited { retry_after_ms: 20 })
        );
        expect_ok(
            &mut annotator,
            &mut sink,
            2_000,
            2_000,
            &point,
            1_280_000_000,
        );
        assert_eq!(annotator.bucket_count(), 1);
    }
}
