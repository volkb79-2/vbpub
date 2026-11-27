//! The production backend: answers from the turn's immutable published tick only.

use std::collections::BTreeSet;
use std::sync::Arc;

use hostobs_model::identity::CgroupKey;
use hostobs_proto::dict::Entry;
use hostobs_proto::frame::{Column, Row};

use hostobs_serve::backend::{
    AnnotateArgs, AnnotateCtxKey, AnnotateKind, AnnotateOutcome, AnnotateRefusal, AnnotateReply,
    Backend, BackendError, Identity, StatusInfo, TickData, TickInfo,
};
use hostobs_serve::conn::{CgroupKey as PeerCgroupKey, Peer};
use hostobs_serve::hub::ConnId;
use hostobs_serve::selection::{Sel, Selection};
use hostobs_store::annotate::{AnnotateError, Annotator, EventRequest, EventSink};
use hostobs_store::error::StoreError;
use hostobs_store::events::{CtxKey, Event, EventKind, EventPeer, EventCgroupKey, Outcome};
use hostobs_store::store::Store;
use hostobs_store::tier::AppendOutcome;

use crate::sampler::{Published, Tick};

/// The production backend over one immutable sampler publication.
pub struct ProbeBackend {
    identity: Identity,
    published: Arc<Published>,
    tick: Arc<Tick>,
    now_ns: u64,
    store: Store,
    annotator: Annotator,
    append_clock: Box<dyn Fn() -> u64 + Send + Sync>,
    append_failed: bool,
}

impl ProbeBackend {
    /// Start on the published seq-0 state.
    #[must_use]
    pub fn new(
        identity: Identity,
        published: Arc<Published>,
        store: Store,
        annotator: Annotator,
        append_clock: impl Fn() -> u64 + Send + Sync + 'static,
    ) -> ProbeBackend {
        let tick = published.load();
        ProbeBackend {
            identity,
            published,
            tick,
            now_ns: 0,
            store,
            annotator,
            append_clock: Box::new(append_clock),
            append_failed: false,
        }
    }

    /// Load this turn's tick and set its clock; true when the sequence advanced.
    pub fn begin_turn(&mut self, now_ns: u64) -> bool {
        let tick = self.published.load();
        let fresh = tick.seq() > self.tick.seq();
        self.tick = tick;
        self.now_ns = now_ns;
        fresh
    }
}

struct TimedEventSink<'a> {
    store: &'a mut Store,
    clock: &'a (dyn Fn() -> u64 + Send + Sync),
    append_failed: &'a mut bool,
}

impl EventSink for TimedEventSink<'_> {
    fn record_event(
        &mut self,
        event: &Event,
        payload: &[u8],
        real_ns: i64,
    ) -> Result<AppendOutcome, StoreError> {
        let started = (self.clock)();
        let result = self.store.record_event(event, payload, real_ns);
        let elapsed_ns = (self.clock)().saturating_sub(started);
        let elapsed_ms = elapsed_ns / 1_000_000;
        match &result {
            Ok(outcome) => {
                if let Some(line) = seal_stall_line(outcome.sealed.as_deref(), elapsed_ns) {
                    eprintln!("{line}");
                }
                if *self.append_failed {
                    eprintln!(
                        "hostobs-probe: events append succeeded again after {elapsed_ms} ms"
                    );
                    *self.append_failed = false;
                }
            }
            Err(error) if !*self.append_failed => {
                eprintln!(
                    "hostobs-probe: events append started failing: {} after {elapsed_ms} ms",
                    error
                );
                *self.append_failed = true;
            }
            Err(_) => {}
        }
        result
    }
}

fn event_peer(peer: &Peer) -> EventPeer {
    let comm = peer.comm.as_ref().and_then(|comm| {
        (!comm.is_empty()
            && comm.len() <= 15
            && !comm
                .chars()
                .any(|character| character <= '\u{1f}' || character == '\u{7f}'))
        .then(|| comm.clone())
    });
    let cgroup = peer.cgroup.and_then(|key: PeerCgroupKey| {
        (key.dev != 0 && key.inode != 0).then_some(EventCgroupKey {
            dev: key.dev,
            inode: key.inode,
        })
    });
    EventPeer {
        pid: peer.pid.filter(|pid| *pid != 0),
        comm,
        cgroup,
    }
}

fn event_request(args: &AnnotateArgs) -> EventRequest {
    let kind = match args.kind {
        AnnotateKind::Begin => EventKind::Begin,
        AnnotateKind::End => EventKind::End,
        AnnotateKind::Point => EventKind::Point,
    };
    let outcome = args.outcome.map(|outcome| match outcome {
        AnnotateOutcome::Ok => Outcome::Ok,
        AnnotateOutcome::Fail => Outcome::Fail,
        AnnotateOutcome::Error => Outcome::Error,
        AnnotateOutcome::Skipped => Outcome::Skipped,
        AnnotateOutcome::Cancelled => Outcome::Cancelled,
        AnnotateOutcome::Timeout => Outcome::Timeout,
        AnnotateOutcome::Abandoned => Outcome::Abandoned,
    });
    let ctx = args
        .ctx
        .iter()
        .map(|(key, value)| {
            let key = match key {
                AnnotateCtxKey::Run => CtxKey::Run,
                AnnotateCtxKey::Lane => CtxKey::Lane,
                AnnotateCtxKey::Candidate => CtxKey::Candidate,
                AnnotateCtxKey::Txn => CtxKey::Txn,
                AnnotateCtxKey::Session => CtxKey::Session,
            };
            (key, value.clone())
        })
        .collect();
    EventRequest {
        kind,
        name: args.name.clone(),
        key: args.key.clone(),
        ctx,
        outcome,
        code: args.code.clone(),
        attrs: args.attrs.clone(),
        idempotency: args.idempotency.clone(),
    }
}

fn annotate_refusal(error: AnnotateError) -> AnnotateRefusal {
    match error {
        AnnotateError::RateLimited { retry_after_ms } => {
            AnnotateRefusal::RateLimited { retry_after_ms }
        }
        AnnotateError::Budget => AnnotateRefusal::Budget,
        AnnotateError::SpanUnknown => AnnotateRefusal::SpanUnknown,
        AnnotateError::Internal => AnnotateRefusal::Internal,
    }
}

impl Backend for ProbeBackend {
    fn identity(&self) -> Identity {
        self.identity.clone()
    }

    fn status(&self) -> StatusInfo {
        let header = &self.tick.data().header;
        StatusInfo {
            started_real_ns: self.published.started_real_ns(),
            uptime_ms: self.now_ns.saturating_sub(self.published.started_mono_ns()) / 1_000_000,
            tick: TickInfo {
                seq: header.seq,
                last_mono_ns: header.mono_end_ns,
                scan_ms: self.tick.scan_ms(),
            },
            clients: Vec::new(),
            dropped_blocks: 0,
            tiers: Vec::new(),
        }
    }

    fn current(&self, selection: &Selection, max_rows: u32) -> Result<TickData, BackendError> {
        if self.tick.seq() == 0 {
            return Err(BackendError::Unavailable);
        }
        let data = self.tick.data();
        let limit = usize::try_from(max_rows).unwrap_or(usize::MAX);
        let scan_required = selection
            .entities()
            .iter()
            .any(|selector| matches!(selector, Sel::Prefix(_) | Sel::All));
        let mut resolved = BTreeSet::new();
        for selector in selection.entities() {
            let row_index = match selector {
                Sel::Inc(inc) => self.tick.row_for_inc(*inc),
                Sel::CgroupId { dev, inode } => CgroupKey::new(*dev, *inode)
                    .ok()
                    .and_then(|key| self.tick.row_for_key(key)),
                Sel::Path(path) => self.tick.row_for_label(path),
                Sel::Prefix(_) | Sel::All => None,
            };
            if let Some(row_index) = row_index {
                resolved.insert(row_index);
                if resolved.len() > limit {
                    return Err(BackendError::TooManyRows);
                }
            }
        }
        if scan_required {
            for (row_index, row) in data.rows.iter().enumerate() {
                let Some(Entry::Entity { label, .. }) = data.entries.get(row.ent as usize) else {
                    continue;
                };
                if selection
                    .entities()
                    .iter()
                    .any(|selector| matches_scan_selector(selector, label))
                    && resolved.insert(row_index)
                    && resolved.len() > limit
                {
                    return Err(BackendError::TooManyRows);
                }
            }
        }
        let resolved: Vec<(usize, u32)> = resolved
            .into_iter()
            .filter_map(|row_index| {
                data.rows
                    .get(row_index)
                    .map(|row| (row_index, row.t_off_us))
            })
            .collect();

        let entity_count = u32::try_from(resolved.len()).unwrap_or(u32::MAX);
        let mut entries =
            Vec::with_capacity(resolved.len().saturating_add(selection.metrics().len()));
        let mut rows = Vec::with_capacity(resolved.len());
        for (index, (row_index, t_off_us)) in resolved.iter().copied().enumerate() {
            let ent = u32::try_from(index).unwrap_or(u32::MAX);
            let original = data.rows.get(row_index).map_or(u32::MAX, |row| row.ent);
            if let Some(entity @ Entry::Entity { .. }) = data.entries.get(original as usize) {
                entries.push(entity.clone());
            }
            rows.push(Row { ent, t_off_us });
        }

        let mut columns = Vec::new();
        for name in selection.metrics() {
            let Some((metric_entry, column)) = self.tick.metric_column(name) else {
                continue;
            };
            let Some(Entry::Metric { facts, .. }) = data.entries.get(metric_entry as usize) else {
                continue;
            };
            entries.push(Entry::Metric {
                facts: *facts,
                name: name.clone(),
            });
            let metric =
                entity_count.saturating_add(u32::try_from(columns.len()).unwrap_or(u32::MAX));
            let values = resolved
                .iter()
                .map(|(row, _)| column.values.get(*row).copied().unwrap_or(0))
                .collect();
            let quality = resolved
                .iter()
                .map(|(row, _)| {
                    column
                        .quality
                        .get(*row)
                        .copied()
                        .unwrap_or(hostobs_model::quality::Quality::Absent.code())
                })
                .collect();
            columns.push(Column {
                metric,
                value_type: column.value_type,
                kind: column.kind,
                values,
                quality,
            });
        }
        Ok(TickData {
            header: data.header.clone(),
            entries,
            rows,
            columns,
        })
    }

    fn annotate(
        &mut self,
        conn: ConnId,
        peer: &Peer,
        args: &AnnotateArgs,
    ) -> Result<AnnotateReply, AnnotateRefusal> {
        let real_ns = real_at(&self.published, self.now_ns).ok_or(AnnotateRefusal::Internal)?;
        let request = event_request(args);
        let mut sink = TimedEventSink {
            store: &mut self.store,
            clock: self.append_clock.as_ref(),
            append_failed: &mut self.append_failed,
        };
        self.annotator
            .annotate(
                &mut sink,
                u64::from(conn.0),
                peer.uid,
                event_peer(peer),
                &request,
                self.now_ns,
                real_ns,
            )
            .map(|reply| AnnotateReply {
                t_mono_ns: reply.t_mono_ns,
                span: reply.span,
            })
            .map_err(annotate_refusal)
    }

    fn connection_closed(&mut self, conn: ConnId) {
        let Some(real_ns) = real_at(&self.published, self.now_ns) else {
            self.annotator.forget_connection(u64::from(conn.0));
            eprintln!("hostobs-probe: could not derive time for inferred event ends");
            return;
        };
        let mut sink = TimedEventSink {
            store: &mut self.store,
            clock: self.append_clock.as_ref(),
            append_failed: &mut self.append_failed,
        };
        self.annotator
            .connection_closed(&mut sink, u64::from(conn.0), self.now_ns, real_ns);
    }
}

fn real_at(published: &Published, now_ns: u64) -> Option<i64> {
    let elapsed = now_ns.checked_sub(published.started_mono_ns())?;
    let elapsed = i64::try_from(elapsed).ok()?;
    published.started_real_ns().checked_add(elapsed)
}

fn seal_stall_line(sealed: Option<&str>, elapsed_ns: u64) -> Option<String> {
    sealed.map(|name| {
        let elapsed_ms = elapsed_ns / 1_000_000;
        format!(
            "hostobs-probe: events segment {name} sealed, the append stalled the server turn {elapsed_ms} ms"
        )
    })
}

fn matches_scan_selector(selector: &Sel, label: &str) -> bool {
    match selector {
        Sel::Inc(_) | Sel::CgroupId { .. } | Sel::Path(_) => false,
        Sel::Prefix(prefix) => label.as_bytes().starts_with(prefix.as_bytes()),
        Sel::All => true,
    }
}

impl hostobs_serve::backend::Turn for ProbeBackend {
    fn begin_turn(&mut self, now_ns: u64) -> bool {
        ProbeBackend::begin_turn(self, now_ns)
    }
}

#[cfg(test)]
mod tests {
    use super::{real_at, seal_stall_line};
    use crate::sampler::Published;

    #[test]
    fn real_time_is_startup_wall_time_plus_checked_monotonic_elapsed() {
        let published = Published::new(100, 1_000);
        assert_eq!(real_at(&published, 250), Some(1_150));
        assert_eq!(real_at(&published, 99), None);
        assert_eq!(real_at(&Published::new(0, i64::MAX), 1), None);
    }

    #[test]
    fn the_segment_seal_line_exists_only_for_a_sealed_append() {
        assert_eq!(seal_stall_line(None, 25_000_000), None);
        assert_eq!(
            seal_stall_line(Some("events-1-2.seg"), 25_000_000),
            Some(String::from(
                "hostobs-probe: events segment events-1-2.seg sealed, the append stalled the server turn 25 ms"
            ))
        );
    }
}
