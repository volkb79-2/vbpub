//! Bounded idempotency for point events.
//!
//! The caller validates the point-only request shape, boot id and canonical sequence before
//! lookup. This cache owns no request parsing and keys only the server-authenticated uid and
//! event name alongside the validated boot id and sequence.

use std::collections::{HashMap, VecDeque};

/// The maximum number of live point-event replies retained.
pub const EVENT_DEDUP_CAPACITY: usize = 4096;
/// The retention window in monotonic nanoseconds (900 seconds).
pub const EVENT_DEDUP_TTL_NS: u64 = 900_000_000_000;

/// The first event reply retained for an idempotency key.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Reply {
    /// The server-stamped monotonic event time.
    pub t_mono_ns: i64,
    /// The assigned span id, or `None` for a point event.
    pub span: Option<u64>,
}

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
struct Key {
    uid: u32,
    name: String,
    boot_id: String,
    seq: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct Entry {
    reply: Reply,
    inserted_at: u64,
}

/// A 4,096-entry FIFO with a 900-second monotonic lifetime.
#[derive(Debug, Default)]
pub struct EventDedup {
    entries: HashMap<Key, Entry>,
    order: VecDeque<(Key, u64)>,
    last_now: u64,
    evicted: u64,
}

impl EventDedup {
    /// An empty cache. Process restarts deliberately start with no retained replies.
    #[must_use]
    pub fn new() -> EventDedup {
        EventDedup::default()
    }

    /// Look up an idempotency key, expiring old entries before the lookup.
    ///
    /// A hit returns the first reply and leaves its insertion age and FIFO position unchanged.
    pub fn get(
        &mut self,
        uid: u32,
        name: &str,
        boot_id: &str,
        seq: u64,
        now_mono_ns: u64,
    ) -> Option<Reply> {
        self.advance(now_mono_ns);
        self.entries
            .get(&Key {
                uid,
                name: name.to_string(),
                boot_id: boot_id.to_string(),
                seq,
            })
            .map(|entry| entry.reply)
    }

    /// Look up an unexpired key without changing retention state.
    ///
    /// The annotation engine uses this before append so a failed append cannot evict entries.
    #[must_use]
    pub fn peek(
        &self,
        uid: u32,
        name: &str,
        boot_id: &str,
        seq: u64,
        now_mono_ns: u64,
    ) -> Option<Reply> {
        let effective_now = self.last_now.max(now_mono_ns);
        self.entries
            .get(&Key {
                uid,
                name: name.to_string(),
                boot_id: boot_id.to_string(),
                seq,
            })
            .filter(|entry| {
                effective_now.saturating_sub(entry.inserted_at) < EVENT_DEDUP_TTL_NS
            })
            .map(|entry| entry.reply)
    }

    /// Expire entries after the caller has committed the event that advances retention time.
    pub fn expire(&mut self, now_mono_ns: u64) {
        self.advance(now_mono_ns);
    }

    /// Insert a reply or return the first reply already held for this key.
    pub fn insert(
        &mut self,
        uid: u32,
        name: &str,
        boot_id: &str,
        seq: u64,
        reply: Reply,
        now_mono_ns: u64,
    ) -> Reply {
        let effective_now = self.advance(now_mono_ns);
        let key = Key {
            uid,
            name: name.to_string(),
            boot_id: boot_id.to_string(),
            seq,
        };
        if let Some(existing) = self.entries.get(&key) {
            return existing.reply;
        }
        if self.entries.len() == EVENT_DEDUP_CAPACITY {
            self.evict_oldest();
        }
        let entry = Entry {
            reply,
            inserted_at: effective_now,
        };
        self.order.push_back((key.clone(), effective_now));
        self.entries.insert(key, entry);
        reply
    }

    /// The number of live entries after the most recent expiry pass.
    #[must_use]
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// Whether no live reply is retained.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// The number of entries removed for capacity, excluding TTL expiry.
    #[must_use]
    pub const fn evicted(&self) -> u64 {
        self.evicted
    }

    fn advance(&mut self, now_mono_ns: u64) -> u64 {
        self.last_now = self.last_now.max(now_mono_ns);
        let effective_now = self.last_now;
        loop {
            let expired = self.order.front().is_some_and(|(_, inserted_at)| {
                effective_now.saturating_sub(*inserted_at) >= EVENT_DEDUP_TTL_NS
            });
            if !expired {
                break;
            }
            let Some((key, inserted_at)) = self.order.pop_front() else {
                break;
            };
            if self
                .entries
                .get(&key)
                .is_some_and(|entry| entry.inserted_at == inserted_at)
            {
                self.entries.remove(&key);
            }
        }
        effective_now
    }

    fn evict_oldest(&mut self) {
        while let Some((key, inserted_at)) = self.order.pop_front() {
            let is_live = self
                .entries
                .get(&key)
                .is_some_and(|entry| entry.inserted_at == inserted_at);
            if is_live {
                self.entries.remove(&key);
                self.evicted = self.evicted.saturating_add(1);
                return;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{EventDedup, Reply, EVENT_DEDUP_CAPACITY, EVENT_DEDUP_TTL_NS};

    fn reply(t_mono_ns: i64, span: Option<u64>) -> Reply {
        Reply { t_mono_ns, span }
    }

    #[test]
    fn first_reply_is_preserved_for_the_exact_uid_name_boot_and_sequence_key() {
        let mut cache = EventDedup::new();
        let first = reply(17, None);
        assert_eq!(cache.insert(7, "freeze", "boot-a", 7, first, 10), first);
        assert_eq!(
            cache.insert(7, "freeze", "boot-a", 7, reply(99, Some(12)), 20),
            first
        );
        assert_eq!(cache.get(7, "freeze", "boot-a", 7, 30), Some(first));
        assert_eq!(cache.len(), 1);
        assert_eq!(cache.evicted(), 0);
    }

    #[test]
    fn uid_name_and_connection_independent_keys_partition_point_events() {
        let mut cache = EventDedup::new();
        let first = reply(1, None);
        let other_uid = reply(2, None);
        let other_name = reply(3, None);
        let other_boot = reply(4, None);
        cache.insert(7, "freeze", "boot-a", 7, first, 0);
        cache.insert(8, "freeze", "boot-a", 7, other_uid, 0);
        cache.insert(7, "thaw", "boot-a", 7, other_name, 0);
        cache.insert(7, "freeze", "boot-b", 7, other_boot, 0);
        assert_eq!(cache.get(7, "freeze", "boot-a", 7, 0), Some(first));
        assert_eq!(cache.get(8, "freeze", "boot-a", 7, 0), Some(other_uid));
        assert_eq!(cache.get(7, "thaw", "boot-a", 7, 0), Some(other_name));
        assert_eq!(cache.get(7, "freeze", "boot-b", 7, 0), Some(other_boot));
        // A connection id or peer label is not part of this API: the same uid and exact key
        // on either connection observes the same first reply.
        assert_eq!(cache.get(7, "freeze", "boot-a", 7, 0), Some(first));
    }

    #[test]
    fn freeze_and_thaw_may_reuse_a_sequence_but_same_name_is_a_hit() {
        let mut cache = EventDedup::new();
        let freeze = reply(10, None);
        let thaw = reply(20, None);
        assert_eq!(
            cache.insert(0, "thrashguard.freeze", "boot", 7, freeze, 1),
            freeze
        );
        assert_eq!(
            cache.insert(0, "thrashguard.thaw", "boot", 7, thaw, 2),
            thaw
        );
        assert_eq!(
            cache.insert(0, "thrashguard.freeze", "boot", 7, reply(30, None), 3),
            freeze
        );
        assert_eq!(cache.len(), 2);
    }

    #[test]
    fn capacity_evicts_exactly_the_oldest_live_insertion() {
        let mut cache = EventDedup::new();
        for seq in 0..EVENT_DEDUP_CAPACITY {
            cache.insert(
                1,
                "point",
                "boot",
                u64::try_from(seq).expect("fits"),
                reply(1, None),
                5,
            );
        }
        assert_eq!(cache.len(), EVENT_DEDUP_CAPACITY);
        assert_eq!(cache.get(1, "point", "boot", 0, 5), Some(reply(1, None)));
        assert_eq!(
            cache.insert(1, "point", "boot", 0, reply(99, Some(99)), 5),
            reply(1, None)
        );
        cache.insert(
            1,
            "point",
            "boot",
            u64::try_from(EVENT_DEDUP_CAPACITY).expect("fits"),
            reply(2, None),
            5,
        );
        assert_eq!(cache.len(), EVENT_DEDUP_CAPACITY);
        assert_eq!(cache.evicted(), 1);
        assert_eq!(cache.get(1, "point", "boot", 0, 5), None);
        assert_eq!(cache.get(1, "point", "boot", 1, 5), Some(reply(1, None)));
    }

    #[test]
    fn expiry_happens_at_the_exact_ttl_boundary_without_counting_as_eviction() {
        let mut cache = EventDedup::new();
        let original = reply(1, None);
        cache.insert(1, "point", "boot", 1, original, 100);
        assert_eq!(
            cache.get(1, "point", "boot", 1, 100 + EVENT_DEDUP_TTL_NS - 1),
            Some(original)
        );
        assert_eq!(
            cache.get(1, "point", "boot", 1, 100 + EVENT_DEDUP_TTL_NS),
            None
        );
        assert_eq!(cache.len(), 0);
        assert_eq!(cache.evicted(), 0);
    }

    #[test]
    fn a_backward_clock_uses_the_high_water_mark_for_expiry_and_insertion() {
        let mut cache = EventDedup::new();
        let first = reply(1, None);
        cache.insert(1, "point", "boot", 1, first, 100);
        assert_eq!(cache.get(1, "point", "boot", 1, 1_000), Some(first));
        let second = reply(2, None);
        cache.insert(1, "point", "boot", 2, second, 20);
        assert_eq!(
            cache.get(1, "point", "boot", 2, 100 + EVENT_DEDUP_TTL_NS - 1),
            Some(second)
        );
        assert_eq!(
            cache.get(1, "point", "boot", 2, 100 + EVENT_DEDUP_TTL_NS),
            Some(second)
        );
        assert_eq!(
            cache.get(1, "point", "boot", 2, 1_000 + EVENT_DEDUP_TTL_NS),
            None
        );
    }

    #[test]
    fn a_new_process_starts_with_an_empty_cache() {
        let mut cache = EventDedup::new();
        cache.insert(1, "point", "boot", 1, reply(1, None), 0);
        let restarted = EventDedup::new();
        assert_eq!(restarted.len(), 0);
        assert_eq!(restarted.evicted(), 0);
    }
}
