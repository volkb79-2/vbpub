//! The production backend: answers from the turn's immutable published tick only.

use std::collections::BTreeSet;
use std::sync::Arc;

use hostobs_proto::dict::Entry;
use hostobs_proto::frame::{Column, Row};

use crate::backend::{Backend, BackendError, Identity, StatusInfo, TickData, TickInfo};
use crate::sampler::{Published, Tick};
use crate::selection::{Sel, Selection};

/// The production backend over one immutable sampler publication.
#[derive(Debug)]
pub struct ProbeBackend {
    identity: Identity,
    published: Arc<Published>,
    tick: Arc<Tick>,
    now_ns: u64,
}

impl ProbeBackend {
    /// Start on the published seq-0 state.
    #[must_use]
    pub fn new(identity: Identity, published: Arc<Published>) -> ProbeBackend {
        let tick = published.load();
        ProbeBackend {
            identity,
            published,
            tick,
            now_ns: 0,
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
        if scan_required {
            for (row_index, row) in data.rows.iter().enumerate() {
                let Some(Entry::Entity { inc, label }) = data.entries.get(row.ent as usize) else {
                    continue;
                };
                if selection
                    .entities()
                    .iter()
                    .any(|selector| matches_selector(selector, *inc, label))
                    && resolved.insert(row_index)
                    && resolved.len() > limit
                {
                    return Err(BackendError::TooManyRows);
                }
            }
        } else {
            for selector in selection.entities() {
                let row_index = match selector {
                    Sel::Inc(inc) => self.tick.row_for_inc(*inc),
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
}

fn matches_selector(selector: &Sel, inc: u64, label: &str) -> bool {
    match selector {
        Sel::Inc(candidate) => inc == *candidate,
        Sel::Path(path) => label.as_bytes() == path.as_bytes(),
        Sel::Prefix(prefix) => label.as_bytes().starts_with(prefix.as_bytes()),
        Sel::All => true,
    }
}

impl crate::backend::Turn for ProbeBackend {
    fn begin_turn(&mut self, now_ns: u64) -> bool {
        ProbeBackend::begin_turn(self, now_ns)
    }
}
