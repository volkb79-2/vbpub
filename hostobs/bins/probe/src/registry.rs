//! Shared selection streams, subscribers and generation counters (design S9-S11, S19 and S24).
//!
//! Every canonical [`Selection`] has at most one live `Stream`. Subscribers join it by their
//! connection id and request `corr`; the next due tick is encoded once and shared by all of
//! them. The registry owns no block bytes between ticks.

use std::collections::BTreeMap;
use std::sync::Arc;

use hostobs_proto::hub::ConnId;

use crate::backend::{Backend, BackendError, TickData};
use crate::codes::{Code, Detail, Pool};
use crate::selection::Selection;
use crate::tick::{
    dictionary_body, encode_bare, encoded_len, validate, BlockBounds, LiveTick, TickError,
};

/// A monotonic live-selection id. Zero is never assigned.
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct SelId(
    /// The monotonic id.
    pub u64,
);

/// One connection's subscription request.
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct SubKey {
    /// The connection.
    pub conn: ConnId,
    /// The subscription's correlation id.
    pub corr: u32,
}

/// The server-wide bounds used to build each selection tick.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct StreamLimits {
    /// The unified row and byte bounds.
    pub bounds: BlockBounds,
}

/// The distinct-selection caps of the requesting connection's level.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SelectionCaps {
    /// Distinct selections per peer uid.
    pub per_uid: u32,
    /// Distinct live selections per probe.
    pub per_probe: u32,
}

/// Whether a subscription joined a live stream or created one.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Admit {
    /// The selection stream already existed.
    Joined(SelId),
    /// The subscription created the selection stream.
    Created(SelId),
}

/// A refused subscription.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Refused {
    /// A distinct-selection pool is exhausted.
    Budget(Pool),
    /// The connection already has this `(conn, corr)` subscription.
    Duplicate,
    /// Every possible [`SelId`] has been used.
    Exhausted,
}

/// One turn of registry work returned to the server.
pub enum Delivery {
    /// One shared tick for the subscribers that are due.
    Push {
        /// The encoded tick.
        tick: LiveTick,
        /// The due subscribers, ascending.
        to: Vec<SubKey>,
    },
    /// End every subscriber of a stream that failed.
    End {
        /// Every subscriber of the failed stream, ascending.
        to: Vec<SubKey>,
        /// The wire error.
        code: Code,
        /// The bounded error detail.
        detail: Detail,
    },
}

struct Stream {
    selection: Selection,
    generation: u32,
    entries: Vec<hostobs_proto::dict::Entry>,
    dict_body: Option<Arc<[u8]>>,
    subscribers: BTreeMap<SubKey, Sub>,
}

struct Sub {
    uid: u32,
    every: u32,
    left: u32,
}

/// The live selection streams of one server.
pub struct SelectionStreams {
    limits: StreamLimits,
    next_id: u64,
    streams: BTreeMap<SelId, Stream>,
    by_selection: BTreeMap<Selection, SelId>,
    by_sub: BTreeMap<SubKey, SelId>,
    uid_refs: BTreeMap<u32, BTreeMap<SelId, u32>>,
}

struct Built {
    generation: u32,
    change: bool,
    base: Arc<[u8]>,
    dict_body: Arc<[u8]>,
}

impl SelectionStreams {
    /// An empty registry.
    #[must_use]
    pub fn new(limits: StreamLimits) -> SelectionStreams {
        SelectionStreams {
            limits,
            next_id: 0,
            streams: BTreeMap::new(),
            by_selection: BTreeMap::new(),
            by_sub: BTreeMap::new(),
            uid_refs: BTreeMap::new(),
        }
    }

    /// Add one subscription, joining a live selection when possible (S9 and S19).
    ///
    /// # Errors
    ///
    /// [`Refused::Duplicate`] for an existing key, [`Refused::Budget`] for the requesting
    /// level's uid or probe cap and [`Refused::Exhausted`] when no selection id remains.
    pub fn subscribe(
        &mut self,
        key: SubKey,
        uid: u32,
        every: u32,
        selection: &Selection,
        caps: SelectionCaps,
    ) -> Result<Admit, Refused> {
        if self.by_sub.contains_key(&key) {
            return Err(Refused::Duplicate);
        }
        let used = self.uid_uses(uid);
        let live = self.by_selection.get(selection).copied();
        let id = match live {
            Some(id) => {
                if used.get(&id).copied().unwrap_or(0) > 0 {
                    id
                } else if used.len() >= usable_cap(caps.per_uid) {
                    return Err(Refused::Budget(Pool::UidSelections));
                } else {
                    id
                }
            }
            None => {
                if used.len() >= usable_cap(caps.per_uid) {
                    return Err(Refused::Budget(Pool::UidSelections));
                }
                if self.streams.len() >= usable_cap(caps.per_probe) {
                    return Err(Refused::Budget(Pool::Selections));
                }
                let next = self.next_id.checked_add(1).ok_or(Refused::Exhausted)?;
                self.next_id = next;
                let id = SelId(next);
                self.streams.insert(
                    id,
                    Stream {
                        selection: selection.clone(),
                        generation: 0,
                        entries: Vec::new(),
                        dict_body: None,
                        subscribers: BTreeMap::new(),
                    },
                );
                self.by_selection.insert(selection.clone(), id);
                id
            }
        };
        let created = live.is_none();
        self.add_subscriber(id, key, uid, every);
        Ok(if created {
            Admit::Created(id)
        } else {
            Admit::Joined(id)
        })
    }

    /// Remove one subscription; `false` when the key is unknown.
    pub fn unsubscribe(&mut self, key: SubKey) -> bool {
        let Some(id) = self.by_sub.remove(&key) else {
            return false;
        };
        let Some(uid) = self
            .streams
            .get_mut(&id)
            .and_then(|stream| stream.subscribers.remove(&key))
            .map(|sub| sub.uid)
        else {
            return true;
        };
        self.release_uid_ref(uid, id);
        if self
            .streams
            .get(&id)
            .is_some_and(|stream| stream.subscribers.is_empty())
        {
            let selection = self.streams.remove(&id).map(|stream| stream.selection);
            if let Some(selection) = selection {
                self.by_selection.remove(&selection);
            }
        }
        true
    }

    /// Remove every subscription of `conn`, idempotently, and return how many were removed.
    pub fn release_conn(&mut self, conn: ConnId) -> usize {
        let start = SubKey { conn, corr: 0 };
        let end = SubKey {
            conn,
            corr: u32::MAX,
        };
        let keys: Vec<SubKey> = self
            .by_sub
            .range(start..=end)
            .map(|(key, _)| *key)
            .collect();
        let count = keys.len();
        for key in keys {
            self.unsubscribe(key);
        }
        count
    }

    /// Build every due selection stream and return deliveries in [`SelId`] order (S10 and S11).
    pub fn on_tick(&mut self, backend: &dyn Backend) -> Vec<Delivery> {
        for stream in self.streams.values_mut() {
            for sub in stream.subscribers.values_mut() {
                sub.left = sub.left.saturating_sub(1);
            }
        }
        let due: Vec<SelId> = self
            .streams
            .iter()
            .filter(|(_, stream)| stream.subscribers.values().any(|sub| sub.left == 0))
            .map(|(id, _)| *id)
            .collect();
        let mut deliveries = Vec::new();
        for id in due {
            let Some(selection) = self.streams.get(&id).map(|stream| stream.selection.clone())
            else {
                continue;
            };
            let data = backend.current(&selection, self.limits.bounds.max_rows);
            let data = match data {
                Ok(data) => data,
                Err(BackendError::Unavailable) => continue,
                Err(BackendError::TooManyRows) => {
                    deliveries.push(self.fail(id, TickError::Rows));
                    continue;
                }
            };
            if data.rows.len() > usize::try_from(self.limits.bounds.max_rows).unwrap_or(usize::MAX)
            {
                deliveries.push(self.fail(id, TickError::Rows));
                continue;
            }
            if let Err(error) = validate(&data) {
                deliveries.push(self.fail(id, error));
                continue;
            }
            let Some(stream) = self.streams.get(&id) else {
                continue;
            };
            let built = build(stream, &data, self.limits.bounds.max_block_bytes);
            let built = match built {
                Ok(built) => built,
                Err(error) => {
                    deliveries.push(self.fail(id, error));
                    continue;
                }
            };
            let Some(stream) = self.streams.get_mut(&id) else {
                continue;
            };
            stream.generation = built.generation;
            stream.entries = data.entries;
            stream.dict_body = Some(Arc::clone(&built.dict_body));
            let to: Vec<SubKey> = stream
                .subscribers
                .iter_mut()
                .filter_map(|(key, sub)| {
                    if sub.left == 0 {
                        sub.left = sub.every;
                        Some(*key)
                    } else {
                        None
                    }
                })
                .collect();
            deliveries.push(Delivery::Push {
                tick: LiveTick::new(
                    built.generation,
                    (!built.change).then(|| Arc::clone(&built.base)),
                    built.base,
                    built.dict_body,
                ),
                to,
            });
        }
        deliveries
    }

    /// The number of live selection streams.
    #[must_use]
    pub fn live_selections(&self) -> usize {
        self.streams.len()
    }

    /// The number of distinct selections used by `uid` across all connections and levels.
    #[must_use]
    pub fn uid_selections(&self, uid: u32) -> usize {
        self.uid_uses(uid).len()
    }

    /// The number of live subscriptions.
    #[must_use]
    pub fn subscribers(&self) -> usize {
        self.by_sub.len()
    }

    /// The stream's generation; 0 means nothing was built, `None` means the stream is gone.
    #[must_use]
    pub fn generation(&self, id: SelId) -> Option<u32> {
        self.streams.get(&id).map(|stream| stream.generation)
    }

    /// The canonical selection of one subscription.
    #[must_use]
    pub fn selection_of(&self, key: SubKey) -> Option<&Selection> {
        let id = self.by_sub.get(&key)?;
        self.streams.get(id).map(|stream| &stream.selection)
    }

    fn uid_uses(&self, uid: u32) -> &BTreeMap<SelId, u32> {
        static EMPTY: std::sync::OnceLock<BTreeMap<SelId, u32>> = std::sync::OnceLock::new();
        self.uid_refs
            .get(&uid)
            .unwrap_or_else(|| EMPTY.get_or_init(BTreeMap::new))
    }

    fn add_subscriber(&mut self, id: SelId, key: SubKey, uid: u32, every: u32) {
        let refs = self.uid_refs.entry(uid).or_default().entry(id).or_insert(0);
        *refs = refs.saturating_add(1);
        if let Some(stream) = self.streams.get_mut(&id) {
            stream.subscribers.insert(
                key,
                Sub {
                    uid,
                    every,
                    left: 1,
                },
            );
        }
        self.by_sub.insert(key, id);
    }

    fn release_uid_ref(&mut self, uid: u32, id: SelId) {
        let mut remove_uid = false;
        if let Some(refs) = self.uid_refs.get_mut(&uid) {
            let mut remove_selection = false;
            if let Some(count) = refs.get_mut(&id) {
                *count = count.saturating_sub(1);
                remove_selection = *count == 0;
            }
            if remove_selection {
                refs.remove(&id);
            }
            remove_uid = refs.is_empty();
        }
        if remove_uid {
            self.uid_refs.remove(&uid);
        }
    }

    fn fail(&mut self, id: SelId, error: TickError) -> Delivery {
        let Some(stream) = self.streams.remove(&id) else {
            return Delivery::End {
                to: Vec::new(),
                code: Code::Internal,
                detail: Detail::Empty,
            };
        };
        self.by_selection.remove(&stream.selection);
        let (code, detail) = error.refusal();
        let mut to = Vec::with_capacity(stream.subscribers.len());
        for (key, sub) in stream.subscribers {
            self.by_sub.remove(&key);
            self.release_uid_ref(sub.uid, id);
            to.push(key);
        }
        Delivery::End { to, code, detail }
    }
}

fn usable_cap(cap: u32) -> usize {
    usize::try_from(cap).unwrap_or(0)
}

fn build(stream: &Stream, data: &TickData, max_bytes: u32) -> Result<Built, TickError> {
    let change = stream.generation == 0 || data.entries != stream.entries;
    let generation = if change {
        if stream.generation == 0 {
            1
        } else {
            stream
                .generation
                .checked_add(1)
                .ok_or(TickError::Exhausted)?
        }
    } else {
        stream.generation
    };
    let body = if change {
        Arc::from(dictionary_body(generation, &data.entries)?)
    } else {
        stream.dict_body.clone().ok_or(TickError::Invalid)?
    };
    let limit = usize::try_from(max_bytes).unwrap_or(usize::MAX);
    let Some(length) = encoded_len(data, Some(body.len())) else {
        return Err(TickError::Oversize);
    };
    if length > limit {
        return Err(TickError::Oversize);
    }
    let base = Arc::from(encode_bare(data, generation)?);
    Ok(Built {
        generation,
        change,
        base,
        dict_body: body,
    })
}

#[cfg(test)]
mod tests {
    use std::cell::{Cell, RefCell};
    use std::collections::BTreeMap;

    use hostobs_proto::dict::Entry;
    use hostobs_proto::hub::{ConnId, TickForms};

    use super::{
        Admit, Delivery, Refused, SelId, SelectionCaps, SelectionStreams, StreamLimits, SubKey,
    };
    use crate::backend::{Backend, BackendError, Identity, StatusInfo, TickData};
    use crate::codes::{Code, Detail, Pool};
    use crate::selection::{Sel, Selection};
    use crate::tick::BlockBounds;

    fn selection(inc: u64) -> Selection {
        Selection::new(vec![Sel::Inc(inc)], vec!["m".to_string()]).expect("selection")
    }

    fn caps() -> SelectionCaps {
        SelectionCaps {
            per_uid: 4,
            per_probe: 8,
        }
    }

    fn registry() -> SelectionStreams {
        SelectionStreams::new(StreamLimits {
            bounds: BlockBounds {
                max_rows: 4_096,
                max_block_bytes: 4 << 20,
            },
        })
    }

    fn key(conn: u32, corr: u32) -> SubKey {
        SubKey {
            conn: ConnId(conn),
            corr,
        }
    }

    fn data(seq: u64, incs: &[u64]) -> TickData {
        TickData::example(seq, incs, &["m"])
    }

    struct ProbeBackend {
        fixed: crate::backend::FixedBackend,
        calls: Cell<usize>,
        data: RefCell<Vec<(Selection, Result<TickData, BackendError>)>>,
    }

    impl ProbeBackend {
        fn new() -> ProbeBackend {
            ProbeBackend {
                fixed: crate::backend::FixedBackend::new(),
                calls: Cell::new(0),
                data: RefCell::new(Vec::new()),
            }
        }

        fn set(&self, selection: Selection, value: Result<TickData, BackendError>) {
            let mut data = self.data.borrow_mut();
            if let Some((_, held)) = data.iter_mut().find(|(known, _)| known == &selection) {
                *held = value;
            } else {
                data.push((selection, value));
            }
        }
    }

    impl Backend for ProbeBackend {
        fn identity(&self) -> Identity {
            self.fixed.identity()
        }

        fn status(&self) -> StatusInfo {
            self.fixed.status()
        }

        fn current(&self, selection: &Selection, max_rows: u32) -> Result<TickData, BackendError> {
            self.calls.set(self.calls.get().saturating_add(1));
            let data = self.data.borrow();
            let Some((_, found)) = data.iter().find(|(known, _)| known == selection) else {
                return Err(BackendError::Unavailable);
            };
            match found {
                Err(error) => Err(*error),
                Ok(tick) => {
                    if tick.rows.len() > usize::try_from(max_rows).unwrap_or(usize::MAX) {
                        Err(BackendError::TooManyRows)
                    } else {
                        Ok(tick.clone())
                    }
                }
            }
        }
    }

    struct OverfullBackend {
        fixed: crate::backend::FixedBackend,
        tick: TickData,
    }

    impl Backend for OverfullBackend {
        fn identity(&self) -> Identity {
            self.fixed.identity()
        }

        fn status(&self) -> StatusInfo {
            self.fixed.status()
        }

        fn current(
            &self,
            _selection: &Selection,
            _max_rows: u32,
        ) -> Result<TickData, BackendError> {
            Ok(self.tick.clone())
        }
    }

    fn fixed_with(selection: Selection, tick: TickData) -> crate::backend::FixedBackend {
        let mut backend = crate::backend::FixedBackend::new();
        backend.current.insert(selection, Ok(tick));
        backend
    }

    fn keys(delivery: &Delivery) -> Vec<SubKey> {
        match delivery {
            Delivery::Push { to, .. } | Delivery::End { to, .. } => to.clone(),
        }
    }

    fn push(delivery: &Delivery) -> (u32, Option<u32>) {
        match delivery {
            Delivery::Push { tick, .. } => (
                tick.generation(),
                tick.bare()
                    .map(|bare| u32::try_from(bare.len()).expect("small")),
            ),
            Delivery::End { .. } => panic!("not a push"),
        }
    }

    #[test]
    fn registry_subscribe_create() {
        let mut streams = registry();
        let one = selection(1);
        let admitted = streams
            .subscribe(key(1, 1), 1, 1, &one, caps())
            .expect("subscribe");
        assert_eq!(admitted, Admit::Created(SelId(1)));
        assert_eq!(streams.live_selections(), 1);
        assert_eq!(streams.subscribers(), 1);
        assert_eq!(streams.uid_selections(1), 1);
        assert_eq!(streams.generation(SelId(1)), Some(0));
        assert_eq!(streams.selection_of(key(1, 1)), Some(&one));
    }

    #[test]
    fn registry_subscribe_join_same_uid_no_cap() {
        let mut streams = registry();
        let one = selection(1);
        streams
            .subscribe(key(1, 1), 1, 1, &one, caps())
            .expect("create");
        let capped = SelectionCaps {
            per_uid: 1,
            per_probe: 1,
        };
        assert_eq!(
            streams.subscribe(key(1, 2), 1, 1, &one, capped),
            Ok(Admit::Joined(SelId(1)))
        );
        assert_eq!(streams.uid_selections(1), 1);
        assert_eq!(streams.subscribers(), 2);
    }

    #[test]
    fn registry_subscribe_new_selection_uid_at_cap() {
        let mut streams = registry();
        streams
            .subscribe(key(1, 1), 1, 1, &selection(1), caps())
            .expect("first");
        let at_cap = SelectionCaps {
            per_uid: 1,
            per_probe: 8,
        };
        assert_eq!(
            streams.subscribe(key(1, 2), 1, 1, &selection(2), at_cap),
            Err(Refused::Budget(Pool::UidSelections))
        );
    }

    #[test]
    fn registry_subscribe_below_cap_with_probe_at_8() {
        let mut streams = registry();
        for index in 1..=8 {
            let uid = u32::try_from(index).expect("small");
            streams
                .subscribe(key(uid, 1), uid, 1, &selection(index), caps())
                .expect("create");
        }
        assert_eq!(streams.live_selections(), 8);
        let join = selection(1);
        assert_eq!(
            streams.subscribe(
                key(2, 2),
                2,
                1,
                &join,
                SelectionCaps {
                    per_uid: 4,
                    per_probe: 1,
                }
            ),
            Ok(Admit::Joined(SelId(1)))
        );
    }

    #[test]
    fn registry_subscribe_uid_cap_4_and_5() {
        let mut streams = registry();
        for corr in 1..=4 {
            streams
                .subscribe(key(1, corr), 1, 1, &selection(u64::from(corr)), caps())
                .expect("within uid cap");
        }
        assert_eq!(
            streams.subscribe(key(1, 5), 1, 1, &selection(5), caps()),
            Err(Refused::Budget(Pool::UidSelections))
        );
    }

    #[test]
    fn registry_subscribe_probe_cap_8_and_9_two_uids() {
        let mut streams = registry();
        for index in 0..4 {
            streams
                .subscribe(
                    key(1, u32::try_from(index).expect("small") + 1),
                    1,
                    1,
                    &selection(u64::try_from(index).expect("small") + 1),
                    caps(),
                )
                .expect("uid one");
            streams
                .subscribe(
                    key(2, u32::try_from(index).expect("small") + 1),
                    2,
                    1,
                    &selection(u64::try_from(index).expect("small") + 5),
                    caps(),
                )
                .expect("uid two");
        }
        assert_eq!(streams.live_selections(), 8);
        assert_eq!(
            streams.subscribe(key(3, 1), 3, 1, &selection(9), caps()),
            Err(Refused::Budget(Pool::Selections))
        );
    }

    #[test]
    fn registry_subscribe_cap_order_uid_before_probe() {
        let mut streams = registry();
        streams
            .subscribe(
                key(1, 1),
                1,
                1,
                &selection(1),
                SelectionCaps {
                    per_uid: 1,
                    per_probe: 1,
                },
            )
            .expect("first only");
        assert_eq!(
            streams.subscribe(
                key(1, 5),
                1,
                1,
                &selection(5),
                SelectionCaps {
                    per_uid: 1,
                    per_probe: 1,
                }
            ),
            Err(Refused::Budget(Pool::UidSelections))
        );
    }

    #[test]
    fn registry_subscribe_caps_taken_from_argument() {
        let mut streams = registry();
        assert_eq!(
            streams.subscribe(
                key(1, 1),
                1,
                1,
                &selection(1),
                SelectionCaps {
                    per_uid: 0,
                    per_probe: 8,
                }
            ),
            Err(Refused::Budget(Pool::UidSelections))
        );
        assert_eq!(
            streams.subscribe(
                key(2, 1),
                2,
                1,
                &selection(2),
                SelectionCaps {
                    per_uid: 1,
                    per_probe: 0,
                }
            ),
            Err(Refused::Budget(Pool::Selections))
        );
    }

    #[test]
    fn registry_subscribe_duplicate_key() {
        let mut streams = registry();
        let one = selection(1);
        streams
            .subscribe(key(1, 1), 1, 1, &one, caps())
            .expect("first");
        assert_eq!(
            streams.subscribe(key(1, 1), 1, 1, &one, caps()),
            Err(Refused::Duplicate)
        );
    }

    #[test]
    fn registry_subscribe_sel_id_exhaustion() {
        let mut streams = registry();
        streams.next_id = u64::MAX;
        assert_eq!(
            streams.subscribe(key(1, 1), 1, 1, &selection(1), caps()),
            Err(Refused::Exhausted)
        );
    }

    #[test]
    fn registry_leave_unsubscribe_last_destroys_and_recreation_restarts_at_generation_1() {
        let mut streams = registry();
        let one = selection(1);
        streams
            .subscribe(key(1, 1), 1, 1, &one, caps())
            .expect("create");
        let backend = fixed_with(one.clone(), data(1, &[1]));
        streams.on_tick(&backend);
        assert_eq!(streams.generation(SelId(1)), Some(1));
        assert!(streams.unsubscribe(key(1, 1)));
        assert!(!streams.unsubscribe(key(1, 1)));
        assert_eq!(streams.live_selections(), 0);
        assert_eq!(streams.generation(SelId(1)), None);
        let recreated = streams
            .subscribe(key(1, 2), 1, 1, &one, caps())
            .expect("recreate");
        assert_eq!(recreated, Admit::Created(SelId(2)));
        assert_eq!(streams.generation(SelId(2)), Some(0));
    }

    #[test]
    fn registry_leave_release_conn_idempotent_and_complete() {
        let mut streams = registry();
        for corr in 1..=2 {
            streams
                .subscribe(key(1, corr), 1, 1, &selection(u64::from(corr)), caps())
                .expect("subscription");
        }
        streams
            .subscribe(key(2, 1), 2, 1, &selection(1), caps())
            .expect("join");
        assert_eq!(streams.release_conn(ConnId(1)), 2);
        assert_eq!(streams.release_conn(ConnId(1)), 0);
        assert_eq!(streams.live_selections(), 1, "uid 2 still uses selection 1");
        assert_eq!(streams.subscribers(), 1);
        assert_eq!(streams.release_conn(ConnId(2)), 1);
        assert_eq!(streams.release_conn(ConnId(2)), 0);
        assert_eq!(streams.live_selections(), 0);
        assert_eq!(streams.subscribers(), 0);
        assert_eq!(streams.uid_selections(1), 0);
        assert_eq!(streams.uid_selections(2), 0);
    }

    #[test]
    fn registry_tick_schedule_every_1_2_3_over_7_ticks() {
        let mut streams = registry();
        let one = selection(1);
        for corr in 1..=3 {
            streams
                .subscribe(key(1, corr), 1, corr, &one, caps())
                .expect("subscription");
        }
        let backend = fixed_with(one, data(1, &[1]));
        let mut seen = [0usize; 4];
        for _ in 0..7 {
            let deliveries = streams.on_tick(&backend);
            for delivery in deliveries {
                for key in keys(&delivery) {
                    seen[usize::try_from(key.corr).expect("small")] += 1;
                }
            }
        }
        assert_eq!(seen[1], 7);
        assert_eq!(seen[2], 4);
        assert_eq!(seen[3], 3);
    }

    #[test]
    fn registry_tick_joiner_due_at_the_next_tick() {
        let mut streams = registry();
        let one = selection(1);
        streams
            .subscribe(key(1, 1), 1, 1, &one, caps())
            .expect("create");
        let backend = fixed_with(one.clone(), data(1, &[1]));
        streams.on_tick(&backend);
        streams
            .subscribe(key(2, 2), 2, 1, &one, caps())
            .expect("join");
        let deliveries = streams.on_tick(&backend);
        assert!(keys(&deliveries[0]).contains(&key(2, 2)));
    }

    #[test]
    fn registry_tick_not_due_stream_not_built() {
        let mut streams = registry();
        let one = selection(1);
        streams
            .subscribe(key(1, 1), 1, 2, &one, caps())
            .expect("create");
        let backend = ProbeBackend::new();
        backend.set(one, Ok(data(1, &[1])));
        streams.on_tick(&backend);
        assert_eq!(backend.calls.get(), 1);
        streams.on_tick(&backend);
        assert_eq!(backend.calls.get(), 1);
    }

    #[test]
    fn registry_tick_backend_unavailable_keeps_due() {
        let mut streams = registry();
        let one = selection(1);
        streams
            .subscribe(key(1, 1), 1, 1, &one.clone(), caps())
            .expect("create");
        let backend = ProbeBackend::new();
        backend.set(one, Err(BackendError::Unavailable));
        assert!(streams.on_tick(&backend).is_empty());
        assert_eq!(streams.generation(SelId(1)), Some(0));
        backend.set(selection(1), Ok(data(2, &[1])));
        let deliveries = streams.on_tick(&backend);
        assert_eq!(keys(&deliveries[0]), vec![key(1, 1)]);
        assert_eq!(backend.calls.get(), 2);
    }

    #[test]
    fn registry_tick_backend_too_many_rows_budget_rows() {
        let mut streams = registry();
        let one = selection(1);
        streams
            .subscribe(key(1, 1), 1, 1, &one.clone(), caps())
            .expect("create");
        let backend = ProbeBackend::new();
        backend.set(one, Err(BackendError::TooManyRows));
        let deliveries = streams.on_tick(&backend);
        match &deliveries[0] {
            Delivery::End { to, code, detail } => {
                assert_eq!(to, &vec![key(1, 1)]);
                assert_eq!(*code, Code::Budget);
                assert_eq!(*detail, Detail::Pool(Pool::Rows));
            }
            Delivery::Push { .. } => panic!("not a push"),
        }
    }

    #[test]
    fn registry_tick_nonconforming_backend_rows_end_budget_all() {
        let mut streams = registry();
        streams.limits.bounds.max_rows = 1;
        let one = selection(1);
        streams
            .subscribe(key(1, 1), 1, 1, &one, caps())
            .expect("create");
        streams
            .subscribe(key(1, 2), 1, 1, &one, caps())
            .expect("join");
        let backend = OverfullBackend {
            fixed: crate::backend::FixedBackend::new(),
            tick: TickData::example(1, &[1, 2], &["m"]),
        };

        let deliveries = streams.on_tick(&backend);
        assert_eq!(deliveries.len(), 1);
        match &deliveries[0] {
            Delivery::End { to, code, detail } => {
                assert_eq!(to, &vec![key(1, 1), key(1, 2)]);
                assert_eq!(*code, Code::Budget);
                assert_eq!(*detail, Detail::Pool(Pool::Rows));
            }
            Delivery::Push { .. } => panic!("not an end"),
        }
        assert_eq!(streams.live_selections(), 0);
        assert_eq!(streams.subscribers(), 0);
    }

    #[test]
    fn registry_tick_change_generation_and_no_bare() {
        let mut streams = registry();
        let one = selection(1);
        streams
            .subscribe(key(1, 1), 1, 1, &one.clone(), caps())
            .expect("create");
        let backend = fixed_with(one.clone(), data(1, &[1]));
        let first = streams.on_tick(&backend);
        assert_eq!(push(&first[0]), (1, None));
        let same = streams.on_tick(&backend);
        assert_eq!(push(&same[0]).0, 1);
        assert!(push(&same[0]).1.is_some());
        let changed = fixed_with(one, data(3, &[1, 2]));
        let deliveries = streams.on_tick(&changed);
        assert_eq!(push(&deliveries[0]), (2, None));
    }

    #[test]
    fn registry_tick_same_entries_bare() {
        let mut streams = registry();
        let one = selection(1);
        streams
            .subscribe(key(1, 1), 1, 1, &one.clone(), caps())
            .expect("create");
        let backend = fixed_with(one, data(1, &[1]));
        streams.on_tick(&backend);
        let deliveries = streams.on_tick(&backend);
        assert_eq!(streams.generation(SelId(1)), Some(1));
        assert_eq!(push(&deliveries[0]).0, 1);
        assert!(push(&deliveries[0]).1.is_some());
    }

    #[test]
    fn registry_tick_reordered_entries_change() {
        let mut streams = registry();
        let one = selection(1);
        streams
            .subscribe(key(1, 1), 1, 1, &one.clone(), caps())
            .expect("create");
        let backend = fixed_with(one.clone(), data(1, &[1, 2]));
        streams.on_tick(&backend);
        let mut reordered = data(2, &[1, 2]);
        reordered.entries.swap(0, 1);
        let backend = fixed_with(one, reordered);
        let deliveries = streams.on_tick(&backend);
        assert_eq!(push(&deliveries[0]), (2, None));
    }

    #[test]
    fn registry_tick_generation_exhausted_internal() {
        let mut streams = registry();
        let one = selection(1);
        streams
            .subscribe(key(1, 1), 1, 1, &one.clone(), caps())
            .expect("create");
        let backend = fixed_with(one.clone(), data(1, &[1]));
        streams.on_tick(&backend);
        streams
            .streams
            .get_mut(&SelId(1))
            .expect("stream")
            .generation = u32::MAX;
        let changed = fixed_with(one, data(2, &[1, 2]));
        let deliveries = streams.on_tick(&changed);
        match &deliveries[0] {
            Delivery::End { to, code, detail } => {
                assert_eq!(to, &vec![key(1, 1)]);
                assert_eq!(*code, Code::Internal);
                assert_eq!(*detail, Detail::Empty);
            }
            Delivery::Push { .. } => panic!("not a push"),
        }
    }

    #[test]
    fn registry_tick_rows_end_budget_all() {
        let mut streams = registry();
        streams.limits.bounds.max_rows = 1;
        let one = selection(1);
        streams
            .subscribe(key(1, 1), 1, 1, &one, caps())
            .expect("create");
        streams
            .subscribe(key(1, 2), 1, 2, &selection(1), caps())
            .expect("join");
        let backend = ProbeBackend::new();
        backend.set(one.clone(), Ok(TickData::example(1, &[1], &["m"])));
        let deliveries = streams.on_tick(&backend);
        match &deliveries[0] {
            Delivery::Push { to, .. } => {
                assert_eq!(to, &vec![key(1, 1), key(1, 2)]);
            }
            Delivery::End { .. } => panic!("not a push"),
        }
        let too_many = TickData::example(1, &[1, 2], &["m"]);
        backend.set(one, Ok(too_many));
        let deliveries = streams.on_tick(&backend);
        match &deliveries[0] {
            Delivery::End { to, code, detail } => {
                assert_eq!(to, &vec![key(1, 1), key(1, 2)]);
                assert_eq!(*code, Code::Budget);
                assert_eq!(*detail, Detail::Pool(Pool::Rows));
            }
            Delivery::Push { .. } => panic!("not a push"),
        }
        assert_eq!(streams.live_selections(), 0);
    }

    #[test]
    fn registry_tick_deliveries_selid_order_keys_ascending() {
        let mut streams = registry();
        let one = selection(1);
        let two = selection(2);
        streams
            .subscribe(key(2, 1), 2, 1, &one, caps())
            .expect("one");
        streams
            .subscribe(key(1, 2), 1, 1, &two, caps())
            .expect("two");
        streams
            .subscribe(key(1, 1), 1, 1, &selection(1), caps())
            .expect("join");
        let backend = ProbeBackend::new();
        backend.set(selection(1), Ok(data(1, &[1])));
        backend.set(selection(2), Ok(data(2, &[2])));
        let deliveries = streams.on_tick(&backend);
        assert_eq!(keys(&deliveries[0]), vec![key(1, 1), key(2, 1)]);
        assert_eq!(keys(&deliveries[1]), vec![key(1, 2)]);
        assert_eq!(streams.generation(SelId(1)), Some(1));
        assert_eq!(streams.generation(SelId(2)), Some(1));
    }

    #[test]
    fn registry_hostile_deterministic_runs() {
        let mut streams = registry();
        let selections: Vec<Selection> = (1..=12).map(selection).collect();
        let backend = ProbeBackend::new();
        for selection in &selections {
            backend.set(selection.clone(), Ok(data(1, &[1])));
        }
        let conns = [
            (ConnId(1), 1),
            (ConnId(2), 1),
            (ConnId(3), 2),
            (ConnId(4), 2),
            (ConnId(5), 3),
            (ConnId(6), 3),
        ];
        let mut known: BTreeMap<SelId, (Option<u32>, Option<Vec<Entry>>)> = BTreeMap::new();
        let mut seed = 0x1234_5678_9abc_def0u64;
        for step in 0..2_000u64 {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            let pick = usize::try_from(seed >> 20).expect("fits");
            match step % 5 {
                0 | 1 => {
                    let (conn, uid) = conns[pick % conns.len()];
                    let selection = &selections[(pick / 7) % selections.len()];
                    let corr = u32::try_from((pick / 17) % 6 + 1).expect("small");
                    if let Ok(Admit::Created(id)) = streams.subscribe(
                        SubKey { conn, corr },
                        uid,
                        u32::try_from((pick / 19) % 3 + 1).expect("small"),
                        selection,
                        caps(),
                    ) {
                        known.insert(id, (None, None));
                    }
                }
                2 => {
                    let (conn, _uid) = conns[pick % conns.len()];
                    let corr = u32::try_from((pick / 17) % 6 + 1).expect("small");
                    streams.unsubscribe(SubKey { conn, corr });
                }
                3 => {
                    streams.release_conn(conns[pick % conns.len()].0);
                }
                _ => {
                    let selection = &selections[(pick / 11) % selections.len()];
                    let mut changed = data(step + 2, &[selection_inc(selection)]);
                    if pick.is_multiple_of(3) {
                        if let Some(Entry::Entity { label, .. }) = changed.entries.first_mut() {
                            label.push('x');
                        }
                    }
                    backend.set(selection.clone(), Ok(changed));
                    for delivery in streams.on_tick(&backend) {
                        match delivery {
                            Delivery::Push { tick, to } => {
                                let Some(first) =
                                    to.first().and_then(|key| streams.by_sub.get(key))
                                else {
                                    panic!("push key is subscribed");
                                };
                                let selection = streams
                                    .selection_of(*to.first().expect("key"))
                                    .expect("selection");
                                let current =
                                    backend.current(selection, 4_096).expect("backend data");
                                let Some((generation, entries)) = known.get_mut(first) else {
                                    continue;
                                };
                                let change = entries.as_ref() != Some(&current.entries);
                                let want = if change {
                                    (*generation).map_or(1, |value| {
                                        value.checked_add(1).expect("generation")
                                    })
                                } else {
                                    (*generation).expect("unchanged streams were built")
                                };
                                assert_eq!(
                                    tick.generation(),
                                    want,
                                    "stream {first:?}, selection {selection:?}, change {change}"
                                );
                                assert_eq!(
                                    tick.has_bare(),
                                    !change,
                                    "stream {first:?}, selection {selection:?}"
                                );
                                *generation = Some(want);
                                *entries = Some(current.entries);
                            }
                            Delivery::End { to, .. } => {
                                for key in to {
                                    let id = streams.by_sub.get(&key).copied();
                                    assert!(id.is_none(), "ended keys are removed");
                                }
                            }
                        }
                    }
                }
            }
            let recount = recount(&streams);
            assert_eq!(streams.uid_refs, recount);
            for uid in 1..=3 {
                assert!(streams.uid_selections(uid) <= 4);
            }
            assert!(streams.live_selections() <= 8);
            for (id, (generation, _)) in &mut known {
                if streams.generation(*id).is_none() {
                    *generation = None;
                } else if let Some(value) = generation {
                    assert_eq!(streams.generation(*id), Some(*value));
                }
            }
        }
    }

    fn selection_inc(selection: &Selection) -> u64 {
        match selection.entities().first() {
            Some(Sel::Inc(inc)) => *inc,
            _ => 1,
        }
    }

    fn recount(streams: &SelectionStreams) -> BTreeMap<u32, BTreeMap<SelId, u32>> {
        let mut out: BTreeMap<u32, BTreeMap<SelId, u32>> = BTreeMap::new();
        for (id, stream) in &streams.streams {
            for sub in stream.subscribers.values() {
                let count = out.entry(sub.uid).or_default().entry(*id).or_insert(0);
                *count = count.saturating_add(1);
            }
        }
        out
    }
}
