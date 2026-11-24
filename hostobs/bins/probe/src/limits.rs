//! Serving levels, the probe-wide bounds and the per-level bounds of WIRE.md section 5.1
//! (design R18, R28; D-W52).
//!
//! The field names are the `hostobsd.toml` key names: probe-wide keys live once in
//! [`ProbeLimits`], and only genuinely per-level keys live in [`LevelLimits`]. [`ConnLimits`]
//! joins the two for one accepted connection and builds its `hello.limits` object; it is not a
//! second configuration.
//!
//! # Reply bound and control queue (R28)
//!
//! WIRE.md section 5.1 derives `max_reply_bytes = (control_queue_bytes - 256 KiB) /
//! max_outstanding`, so `control_queue_bytes = max_outstanding * max_reply_bytes + 256 KiB`: the
//! unread replies represented by the reply-bound slots plus 256 KiB of END and EVT frames
//! always fit one control queue. For observe and operator the largest reply is a paged `events`
//! or `procs` reply, so the default stays 2 MiB (4 * 448 KiB + 256 KiB). The observer level serves only
//! `hello` and `annotate` (`status` is forbidden there, WIRE.md section 4.3): WIRE.md section
//! 5.1 bounds every fixed-shape reply and error envelope there below 3 KiB, and the observer
//! `hello` reply (at most 2 verbs, at most 8 quality codes, the 19 limit keys) is below 3 KiB
//! too, so `max_reply_bytes` is 16 KiB and the observer
//! `control_queue_bytes` is derived: 4 * 16 KiB + 256 KiB = 320 KiB. That caps what 128 observer
//! connections can hold at 40 MiB instead of 256 MiB, with no hub change.

use hostobs_proto::hub::QueueLimits;

use crate::tick::BlockBounds;

/// Nanoseconds per millisecond.
const NS_PER_MS: u64 = 1_000_000;
/// One KiB.
const KIB: u32 = 1024;
/// One MiB.
const MIB: u32 = 1024 * 1024;

/// Largest accepted subscription interval (WIRE.md section 4.7).
pub const MAX_INTERVAL_MS: u32 = 60_000;

/// The room the control queue keeps for END and EVT frames beyond the replies (WIRE.md 5.1).
pub const END_SLACK_BYTES: u32 = 256 * KIB;

/// The accepted subscription interval classes of one serving level.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct IntervalRule {
    /// The level's `min_interval_ms`.
    pub min_ms: u32,
    /// The probe-wide `tick_ms`.
    pub tick_ms: u32,
}

impl IntervalRule {
    /// Whether `interval_ms` is a whole number of at least one tick, is at least
    /// `max(min_ms, tick_ms)` and is at most [`MAX_INTERVAL_MS`] (design S8).
    #[must_use]
    pub fn accepts(self, interval_ms: u32) -> bool {
        self.tick_ms > 0
            && interval_ms.is_multiple_of(self.tick_ms)
            && interval_ms >= self.min_ms.max(self.tick_ms)
            && interval_ms <= MAX_INTERVAL_MS
    }
}

/// A serving level: one socket, one set of verbs, one set of bounds.
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub enum Level {
    /// Read-only telemetry.
    Observe,
    /// Telemetry plus `cmdline`.
    Operator,
    /// Peer-bound `annotate`.
    Observer,
    /// The privileged level served by `act`, never by the probe.
    Control,
}

impl Level {
    /// Every level.
    pub const ALL: [Level; 4] = [
        Level::Observe,
        Level::Operator,
        Level::Observer,
        Level::Control,
    ];

    /// The wire name (`hello.level`).
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Level::Observe => "observe",
            Level::Operator => "operator",
            Level::Observer => "observer",
            Level::Control => "control",
        }
    }

    /// The level named `name`.
    #[must_use]
    pub fn from_name(name: &str) -> Option<Level> {
        let mut levels = Level::ALL.iter().copied();
        levels.find(|level| level.name() == name)
    }

    /// The level's bit in a verb's level mask.
    #[must_use]
    pub const fn bit(self) -> u8 {
        match self {
            Level::Observe => 1,
            Level::Operator => 2,
            Level::Observer => 4,
            Level::Control => 8,
        }
    }

    /// Whether the level serves block verbs (only observe and operator do).
    #[must_use]
    pub const fn serves_blocks(self) -> bool {
        matches!(self, Level::Observe | Level::Operator)
    }
}

/// The largest JSON reply that fits `control_queue_bytes` in each of the `max_outstanding`
/// reply-bound slots: `(control_queue_bytes - 256 KiB) / max_outstanding`, or 0 without a slot.
#[must_use]
pub fn max_reply_for(control_queue_bytes: u32, max_outstanding: u32) -> u32 {
    let room = control_queue_bytes.saturating_sub(END_SLACK_BYTES);
    room.checked_div(max_outstanding).unwrap_or(0)
}

/// The smallest control queue that holds `max_outstanding` replies of `max_reply_bytes` plus
/// the END and EVT slack: `max_outstanding * max_reply_bytes + 256 KiB`.
#[must_use]
pub fn control_queue_for(max_outstanding: u32, max_reply_bytes: u32) -> u32 {
    let replies = max_outstanding.saturating_mul(max_reply_bytes);
    replies.saturating_add(END_SLACK_BYTES)
}

/// The probe-wide keys of WIRE.md section 5.1 (D-W52). These values have one owner for the whole
/// probe, whatever level a connection serves.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ProbeLimits {
    /// The sampling tick and the unit of every subscription interval.
    pub tick_ms: u32,
    /// Most rows a selection may resolve to.
    pub max_rows: u32,
    /// Largest BLOCK the server builds; clamped to both connection pools.
    pub max_block_bytes: u32,
    /// Live pool bytes per connection; the global Live pool is eight times this.
    pub client_queue_bytes: u32,
    /// Query pool bytes per connection; the global Query pool is four times this.
    pub query_queue_bytes: u32,
    /// History streams with queued blocks allowed per connection.
    pub history_streams_per_connection: u32,
    /// History streams with queued blocks allowed per probe.
    pub history_streams: u32,
}

impl ProbeLimits {
    /// The WIRE.md section 5.1 defaults.
    #[must_use]
    pub const fn new() -> ProbeLimits {
        ProbeLimits {
            tick_ms: 1_000,
            max_rows: 1_024,
            max_block_bytes: MIB,
            client_queue_bytes: 4 * MIB,
            query_queue_bytes: MIB,
            history_streams_per_connection: 2,
            history_streams: 8,
        }
    }

    /// The configured values with `max_block_bytes` bounded by both connection pools, so any
    /// block that passes its own bound fits an empty connection pool (WIRE.md section 5.1).
    #[must_use]
    pub const fn clamped(self) -> ProbeLimits {
        let max_block_bytes = if self.max_block_bytes < self.client_queue_bytes {
            self.max_block_bytes
        } else {
            self.client_queue_bytes
        };
        let max_block_bytes = if max_block_bytes < self.query_queue_bytes {
            max_block_bytes
        } else {
            self.query_queue_bytes
        };
        ProbeLimits {
            max_block_bytes,
            ..self
        }
    }

    /// The row and byte bounds shared by every live selection stream.
    #[must_use]
    pub const fn block_bounds(self) -> BlockBounds {
        BlockBounds {
            max_rows: self.max_rows,
            max_block_bytes: self.max_block_bytes,
        }
    }
}

impl Default for ProbeLimits {
    fn default() -> ProbeLimits {
        ProbeLimits::new()
    }
}

/// The bounds that genuinely vary by serving level. The probe-wide keys and their `hello`
/// announcements are in [`ProbeLimits`].
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct LevelLimits {
    /// Connections per level (`refuse` beyond it).
    pub max_connections: u32,
    /// Connections per peer uid per level.
    pub max_connections_per_uid: u32,
    /// Largest frame either side accepts.
    pub max_frame_bytes: u32,
    /// Largest request frame or line.
    pub max_request_bytes: u32,
    /// Request deadline, first byte to complete.
    pub request_deadline_ms: u32,
    /// Hello deadline, accept to hello complete.
    pub hello_deadline_ms: u32,
    /// Idle deadline.
    pub idle_ms: u32,
    /// Writer stall bound.
    pub stall_ms: u32,
    /// Pinned head, pin to completely written.
    pub frame_completion_ms: u32,
    /// Unread JSON replies the reply bound allows for (not enforced itself).
    pub max_outstanding: u32,
    /// Distinct selections per peer uid across levels (block levels).
    pub max_uid_selections: u32,
    /// Distinct live selections per probe (block levels).
    pub max_selections: u32,
    /// Live subscriptions per connection (block levels).
    pub max_subscriptions: u32,
    /// Queued REP, EVT and END bytes per connection.
    pub control_queue_bytes: u32,
    /// Largest JSON REP the server builds.
    pub max_reply_bytes: u32,
    /// Most buckets per history block (block levels).
    pub max_block_buckets: u32,
    /// Most blocks per `series_batch` page (block levels).
    pub max_page_blocks: u32,
    /// Smallest subscription interval (block levels).
    pub min_interval_ms: u32,
}

impl LevelLimits {
    /// The per-level defaults of `level`.
    #[must_use]
    pub fn for_level(level: Level) -> LevelLimits {
        let max_outstanding = 4;
        let blocks = level.serves_blocks();
        let block = |value: u32| if blocks { value } else { 0 };
        let (control_queue_bytes, max_reply_bytes) = if level == Level::Observer {
            let reply = 16 * KIB;
            (control_queue_for(max_outstanding, reply), reply)
        } else {
            let control = 2 * MIB;
            (control, max_reply_for(control, max_outstanding))
        };
        let (max_connections, max_connections_per_uid) = match level {
            Level::Observe => (16, 16),
            Level::Operator => (8, 4),
            Level::Observer => (128, 64),
            Level::Control => (64, 32),
        };
        let max_request_bytes = match level {
            Level::Observe | Level::Operator => 32 * KIB,
            Level::Observer => 16 * KIB,
            Level::Control => MIB,
        };
        let request_deadline_ms = if level == Level::Control {
            25_000
        } else {
            5_000
        };
        LevelLimits {
            max_connections,
            max_connections_per_uid,
            max_frame_bytes: 16 * MIB,
            max_request_bytes,
            request_deadline_ms,
            hello_deadline_ms: 5_000,
            idle_ms: 300_000,
            stall_ms: 5_000,
            frame_completion_ms: 5_000,
            max_outstanding,
            max_uid_selections: block(8),
            max_selections: block(8),
            max_subscriptions: block(4),
            control_queue_bytes,
            max_reply_bytes,
            max_block_buckets: block(4_096),
            max_page_blocks: block(64),
            min_interval_ms: block(1_000),
        }
    }
}

/// The effective bounds of one connection: the level's own values joined with the one
/// probe-wide configuration. The nine block keys are announced as 0 at levels without BLK verbs.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ConnLimits {
    /// The level these bounds describe.
    pub serving: Level,
    /// Values that vary by serving level.
    pub level: LevelLimits,
    /// Values shared by every connection of the probe.
    pub probe: ProbeLimits,
}

impl ConnLimits {
    /// Join the configured probe-wide and per-level values for a connection.
    #[must_use]
    pub const fn new(serving: Level, level: LevelLimits, probe: ProbeLimits) -> ConnLimits {
        ConnLimits {
            serving,
            level,
            probe,
        }
    }

    /// Whether this connection's level serves block verbs.
    const fn serves_blocks(self) -> bool {
        self.serving.serves_blocks()
    }

    /// The number of keys of `hello.limits`.
    pub const HELLO_KEYS: usize = 19;

    /// The 19 `hello.limits` members in the order of WIRE.md section 4.4.
    #[must_use]
    pub fn hello_entries(&self) -> [(&'static str, u32); ConnLimits::HELLO_KEYS] {
        let blocks = self.serves_blocks();
        let block = |value: u32| if blocks { value } else { 0 };
        let level = self.level;
        let probe = self.probe;
        [
            ("max_frame_bytes", level.max_frame_bytes),
            ("max_request_bytes", level.max_request_bytes),
            ("request_deadline_ms", level.request_deadline_ms),
            ("hello_deadline_ms", level.hello_deadline_ms),
            ("idle_ms", level.idle_ms),
            ("stall_ms", level.stall_ms),
            ("frame_completion_ms", level.frame_completion_ms),
            ("max_outstanding", level.max_outstanding),
            ("max_subscriptions", level.max_subscriptions),
            ("client_queue_bytes", block(probe.client_queue_bytes)),
            ("query_queue_bytes", block(probe.query_queue_bytes)),
            ("control_queue_bytes", level.control_queue_bytes),
            ("max_reply_bytes", level.max_reply_bytes),
            ("max_block_bytes", block(probe.max_block_bytes)),
            ("max_rows", block(probe.max_rows)),
            ("max_block_buckets", level.max_block_buckets),
            ("max_page_blocks", level.max_page_blocks),
            ("min_interval_ms", level.min_interval_ms),
            ("tick_ms", block(probe.tick_ms)),
        ]
    }

    /// The `hello.limits` object: exactly the 19 keys, each an exact integer.
    #[must_use]
    pub fn hello_json(&self) -> String {
        let mut out = String::from("{");
        for (index, (key, value)) in self.hello_entries().iter().enumerate() {
            if index > 0 {
                out.push(',');
            }
            out.push_str(&format!("\"{key}\":{value}"));
        }
        out.push('}');
        out
    }

    /// The send-hub caps of this connection (WIRE.md sections 5.1 and 5.3): global Live is eight
    /// connection pools and global Query is four; there are no per-uid pools.
    #[must_use]
    pub fn queue_limits(&self) -> QueueLimits {
        let size = |value: u32| usize::try_from(value).unwrap_or(usize::MAX);
        let live = size(self.probe.client_queue_bytes);
        let query = size(self.probe.query_queue_bytes);
        let ns = |ms: u32| u64::from(ms).saturating_mul(NS_PER_MS);
        QueueLimits {
            client_queue_bytes: live,
            global_live_bytes: live.saturating_mul(8),
            query_queue_bytes: query,
            global_query_bytes: query.saturating_mul(4),
            control_queue_bytes: size(self.level.control_queue_bytes),
            stall_ns: ns(self.level.stall_ms),
            frame_completion_ns: ns(self.level.frame_completion_ms),
        }
    }

    /// The discrete subscription intervals this level accepts (design S8).
    #[must_use]
    pub fn interval_rule(&self) -> IntervalRule {
        IntervalRule {
            min_ms: self.level.min_interval_ms,
            tick_ms: if self.serves_blocks() {
                self.probe.tick_ms
            } else {
                0
            },
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{
        control_queue_for, max_reply_for, ConnLimits, IntervalRule, Level, LevelLimits,
        ProbeLimits, END_SLACK_BYTES, MAX_INTERVAL_MS,
    };
    use crate::backend::TickData;
    use crate::tick::{dictionary_body, encoded_len, query_block};
    use hostobs_proto::dict::Entry;
    use hostobs_proto::envelope::{members_up_to, Kind};

    const BLOCK_KEYS: [&str; 9] = [
        "max_subscriptions",
        "client_queue_bytes",
        "query_queue_bytes",
        "max_block_bytes",
        "max_rows",
        "max_block_buckets",
        "max_page_blocks",
        "min_interval_ms",
        "tick_ms",
    ];

    fn conn_limits(level: Level) -> ConnLimits {
        ConnLimits::new(level, LevelLimits::for_level(level), ProbeLimits::new())
    }

    fn value_of(limits: &ConnLimits, key: &str) -> u32 {
        let entries = limits.hello_entries();
        let found = entries.iter().find(|(name, _)| *name == key);
        found.map(|(_, value)| *value).expect("key present")
    }

    #[test]
    fn level_names_and_bits_round_trip() {
        let mut seen = 0u8;
        for level in Level::ALL {
            assert_eq!(Level::from_name(level.name()), Some(level));
            assert_eq!(seen & level.bit(), 0);
            seen |= level.bit();
        }
        assert_eq!(seen, 15);
        assert_eq!(Level::from_name("admin"), None);
        assert!(Level::Observe.serves_blocks());
        assert!(Level::Operator.serves_blocks());
        assert!(!Level::Observer.serves_blocks());
        assert!(!Level::Control.serves_blocks());
    }

    #[test]
    fn probe_defaults_are_the_section_5_1_and_dw52_values() {
        assert_eq!(
            ProbeLimits::new(),
            ProbeLimits {
                tick_ms: 1_000,
                max_rows: 1_024,
                max_block_bytes: 1 << 20,
                client_queue_bytes: 4 << 20,
                query_queue_bytes: 1 << 20,
                history_streams_per_connection: 2,
                history_streams: 8,
            }
        );
    }

    #[test]
    fn level_defaults_follow_the_section_5_1_table() {
        let observe = LevelLimits::for_level(Level::Observe);
        assert_eq!(observe.max_connections, 16);
        assert_eq!(
            observe.max_connections_per_uid, observe.max_connections,
            "D-W53: the observe uid cap is the level cap"
        );
        assert_eq!(observe.max_request_bytes, 32 * 1024);
        assert_eq!(observe.request_deadline_ms, 5_000);
        assert_eq!(observe.max_subscriptions, 4);
        assert_eq!(observe.max_reply_bytes, 448 * 1024);
        assert_eq!(observe.control_queue_bytes, 2 << 20);
        assert_eq!(observe.max_frame_bytes, 16 << 20);
        let operator = LevelLimits::for_level(Level::Operator);
        assert_eq!(operator.max_connections, 8);
        assert_eq!(operator.max_connections_per_uid, 4);
        assert_eq!(operator.max_request_bytes, 32 * 1024);
        let observer = LevelLimits::for_level(Level::Observer);
        assert_eq!(observer.max_connections, 128);
        assert_eq!(observer.max_connections_per_uid, 64);
        assert_eq!(observer.max_request_bytes, 16 * 1024);
        let control = LevelLimits::for_level(Level::Control);
        assert_eq!(control.max_connections, 64);
        assert_eq!(control.max_connections_per_uid, 32);
        assert_eq!(control.max_request_bytes, 1 << 20);
        assert_eq!(control.request_deadline_ms, 25_000);
        for level in Level::ALL {
            let limits = LevelLimits::for_level(level);
            assert_eq!(limits.hello_deadline_ms, 5_000);
            assert_eq!(limits.idle_ms, 300_000);
            assert_eq!(limits.stall_ms, 5_000);
            assert_eq!(limits.frame_completion_ms, 5_000);
            assert_eq!(limits.max_outstanding, 4);
            assert!(limits.max_connections_per_uid <= limits.max_connections);
        }
    }

    #[test]
    fn block_levels_announce_the_same_probe_wide_and_shared_defaults() {
        let observe = conn_limits(Level::Observe);
        let operator = conn_limits(Level::Operator);
        assert_eq!(
            observe.hello_entries(),
            operator.hello_entries(),
            "observe and operator share all 19 hello values"
        );
        for level in [Level::Observe, Level::Operator] {
            let limits = conn_limits(level);
            assert_eq!(value_of(&limits, "max_rows"), 1_024);
            assert_eq!(value_of(&limits, "max_block_buckets"), 4_096);
            assert_eq!(value_of(&limits, "max_page_blocks"), 64);
            assert_eq!(value_of(&limits, "min_interval_ms"), 1_000);
            assert_eq!(value_of(&limits, "tick_ms"), 1_000);
            assert_eq!(value_of(&limits, "client_queue_bytes"), 4 << 20);
            assert_eq!(value_of(&limits, "query_queue_bytes"), 1 << 20);
            assert_eq!(value_of(&limits, "max_block_bytes"), 1 << 20);
            assert_eq!(limits.level.max_uid_selections, 8);
            assert_eq!(limits.level.max_selections, 8);
        }
    }

    #[test]
    fn limits_selection_caps_defaults_and_zero_at_non_block_levels() {
        for level in [Level::Observe, Level::Operator] {
            let limits = conn_limits(level);
            assert_eq!(limits.level.max_uid_selections, 8);
            assert_eq!(limits.level.max_selections, 8);
            assert_eq!(
                limits.interval_rule(),
                IntervalRule {
                    min_ms: 1_000,
                    tick_ms: 1_000,
                }
            );
        }
        for level in [Level::Observer, Level::Control] {
            let limits = conn_limits(level);
            assert_eq!(limits.level.max_uid_selections, 0);
            assert_eq!(limits.level.max_selections, 0);
        }
    }

    #[test]
    fn hello_keys_still_19() {
        assert_eq!(ConnLimits::HELLO_KEYS, 19);
        for level in Level::ALL {
            let limits = conn_limits(level);
            assert_eq!(limits.hello_entries().len(), ConnLimits::HELLO_KEYS);
            assert_eq!(
                members_up_to(limits.hello_json().as_bytes(), 64)
                    .expect("hello limits object")
                    .len(),
                ConnLimits::HELLO_KEYS
            );
        }
    }

    #[test]
    fn interval_rule_default_refuses_all() {
        let rule = IntervalRule::default();
        for interval in [0u32, 1, 1_000, MAX_INTERVAL_MS] {
            assert!(!rule.accepts(interval), "{interval}");
        }
    }

    #[test]
    fn interval_rule_tick_ms_0_refuses() {
        for min in [0u32, 1, 1_000] {
            let rule = IntervalRule {
                min_ms: min,
                tick_ms: 0,
            };
            assert!(!rule.accepts(1_000));
        }
    }

    #[test]
    fn interval_rule_min_ms_0_still_needs_a_tick() {
        let rule = IntervalRule {
            min_ms: 0,
            tick_ms: 250,
        };
        assert!(!rule.accepts(0));
        assert!(!rule.accepts(249));
        assert!(rule.accepts(250));
        assert!(rule.accepts(MAX_INTERVAL_MS));
        assert!(!rule.accepts(MAX_INTERVAL_MS + 1));
    }

    #[test]
    fn r18_block_keys_are_zero_at_levels_without_block_verbs() {
        for level in [Level::Observer, Level::Control] {
            let limits = conn_limits(level);
            for key in BLOCK_KEYS {
                assert_eq!(value_of(&limits, key), 0, "{key} at {}", level.name());
            }
        }
        for level in [Level::Observe, Level::Operator] {
            let limits = conn_limits(level);
            for key in BLOCK_KEYS {
                assert_ne!(value_of(&limits, key), 0, "{key} at {}", level.name());
            }
        }
    }

    #[test]
    fn r28_control_queue_follows_the_reply_formula_at_every_level() {
        for level in Level::ALL {
            let limits = LevelLimits::for_level(level);
            let bound = limits.max_outstanding * limits.max_reply_bytes + END_SLACK_BYTES;
            assert!(limits.control_queue_bytes <= bound, "{}", level.name());
            assert_eq!(limits.control_queue_bytes, bound, "{}", level.name());
            assert_eq!(
                max_reply_for(limits.control_queue_bytes, limits.max_outstanding),
                limits.max_reply_bytes
            );
            assert!(limits.max_reply_bytes <= limits.control_queue_bytes);
        }
    }

    #[test]
    fn r28_the_observer_queue_is_320_kib_not_2_mib() {
        let observer = LevelLimits::for_level(Level::Observer);
        assert_eq!(observer.max_reply_bytes, 16 * 1024);
        assert_eq!(observer.control_queue_bytes, 320 * 1024);
        assert!(observer.max_reply_bytes >= 3 * 1024);
        let others = LevelLimits::for_level(Level::Observe);
        assert_eq!(others.max_reply_bytes, 448 * 1024);
        assert_eq!(others.control_queue_bytes, 2 * 1024 * 1024);
    }

    #[test]
    fn the_formula_helpers_saturate_and_never_divide_by_zero() {
        assert_eq!(max_reply_for(100, 4), 0);
        assert_eq!(max_reply_for(END_SLACK_BYTES, 4), 0);
        assert_eq!(max_reply_for(END_SLACK_BYTES + 400, 4), 100);
        assert_eq!(max_reply_for(u32::MAX, 0), 0);
        assert_eq!(control_queue_for(0, 100), END_SLACK_BYTES);
        assert_eq!(control_queue_for(u32::MAX, u32::MAX), u32::MAX);
    }

    #[test]
    fn max_block_is_clamped_to_both_connection_pools() {
        let probe = ProbeLimits {
            max_block_bytes: 16 << 20,
            client_queue_bytes: 4 << 20,
            query_queue_bytes: 1 << 20,
            ..ProbeLimits::new()
        }
        .clamped();
        assert_eq!(probe.max_block_bytes, probe.query_queue_bytes);
        assert!(probe.max_block_bytes <= probe.client_queue_bytes);
        let probe = ProbeLimits {
            max_block_bytes: 2 << 20,
            client_queue_bytes: 1 << 20,
            query_queue_bytes: 4 << 20,
            ..ProbeLimits::new()
        }
        .clamped();
        assert_eq!(probe.max_block_bytes, probe.client_queue_bytes);
        assert!(probe.max_block_bytes <= probe.query_queue_bytes);
    }

    fn worst_case_data(rows: usize) -> TickData {
        let entity_labels: Vec<String> = (0..rows)
            .map(|index| {
                let mut label = "e".repeat(256);
                label.replace_range(252.., &format!("{index:04}"));
                label
            })
            .collect();
        let metric_names: Vec<String> = (0..64)
            .map(|index| {
                let mut name = "m".repeat(256);
                name.replace_range(252.., &format!("{index:04}"));
                name
            })
            .collect();
        let names: Vec<&str> = metric_names.iter().map(String::as_str).collect();
        let row_count = u64::try_from(rows).expect("rows fit u64");
        let incarnations: Vec<u64> = (0..row_count).collect();
        let mut data = TickData::example(1, &incarnations, &names);
        for (entry, label) in data.entries.iter_mut().take(rows).zip(entity_labels) {
            let Entry::Entity { label: held, .. } = entry else {
                panic!("the first entries are entities");
            };
            *held = label;
        }
        data
    }

    #[test]
    fn worst_case_query_ram_is_derived_from_the_encoder_helpers() {
        let probe = ProbeLimits::new().clamped();
        let bounds = probe.block_bounds();
        let widest_data = worst_case_data(usize::try_from(probe.max_rows).expect("rows"));
        let dictionary = dictionary_body(1, &widest_data.entries).expect("dictionary");
        let widest_len = encoded_len(&widest_data, Some(dictionary.len())).expect("encoded length");
        let block = query_block(widest_data, bounds).expect("widest block fits");
        assert_eq!(block.len(), widest_len);
        assert_eq!(widest_len, 890_624, "WIRE.md section 5.1 worst-case block");
        assert!(widest_len <= usize::try_from(probe.max_block_bytes).expect("block bytes"));

        let resolution_data = worst_case_data(
            usize::try_from(probe.max_rows.saturating_add(1)).expect("resolution rows"),
        );
        let resolution_dictionary =
            dictionary_body(1, &resolution_data.entries).expect("resolution dictionary");
        let resolution_len = encoded_len(&resolution_data, Some(resolution_dictionary.len()))
            .expect("resolution length");
        assert!(resolution_len <= usize::try_from(probe.max_block_bytes).expect("block bytes"));

        let hub = ConnLimits::new(
            Level::Observe,
            LevelLimits::for_level(Level::Observe),
            probe,
        )
        .queue_limits();
        let total = hub
            .global_query_bytes
            .saturating_add(resolution_len)
            .saturating_add(widest_len);
        let ceiling = total
            .saturating_add((1 << 20) - 1)
            .checked_div(1 << 20)
            .expect("divisor")
            .saturating_mul(1 << 20);
        assert_eq!(ceiling, 6 << 20, "global Query + resolution + block");
        assert!(ceiling <= 9 << 20, "D-W52 query-RAM ceiling");
    }

    /// The WIRE.md section 4.4 key names in order, pinned here independently of the code.
    const HELLO_KEY_NAMES: [&str; 19] = [
        "max_frame_bytes",
        "max_request_bytes",
        "request_deadline_ms",
        "hello_deadline_ms",
        "idle_ms",
        "stall_ms",
        "frame_completion_ms",
        "max_outstanding",
        "max_subscriptions",
        "client_queue_bytes",
        "query_queue_bytes",
        "control_queue_bytes",
        "max_reply_bytes",
        "max_block_bytes",
        "max_rows",
        "max_block_buckets",
        "max_page_blocks",
        "min_interval_ms",
        "tick_ms",
    ];

    /// The struct fields spelled out per key, in the order of [`HELLO_KEY_NAMES`].
    fn field_values(limits: &ConnLimits) -> [u32; 19] {
        let level = limits.level;
        let probe = limits.probe;
        let block = |value: u32| {
            if limits.serves_blocks() {
                value
            } else {
                0
            }
        };
        [
            level.max_frame_bytes,
            level.max_request_bytes,
            level.request_deadline_ms,
            level.hello_deadline_ms,
            level.idle_ms,
            level.stall_ms,
            level.frame_completion_ms,
            level.max_outstanding,
            level.max_subscriptions,
            block(probe.client_queue_bytes),
            block(probe.query_queue_bytes),
            level.control_queue_bytes,
            level.max_reply_bytes,
            block(probe.max_block_bytes),
            block(probe.max_rows),
            level.max_block_buckets,
            level.max_page_blocks,
            level.min_interval_ms,
            block(probe.tick_ms),
        ]
    }

    #[test]
    fn hello_json_is_the_19_exact_integer_keys_in_order() {
        for level in Level::ALL {
            let limits = conn_limits(level);
            let fields = field_values(&limits);
            let entries = limits.hello_entries();
            let names: Vec<&str> = entries.iter().map(|(key, _)| *key).collect();
            assert_eq!(names, HELLO_KEY_NAMES, "{}", level.name());
            let values: Vec<u32> = entries.iter().map(|(_, value)| *value).collect();
            assert_eq!(values, fields, "{}", level.name());
            let json = limits.hello_json();
            let text = json.as_bytes();
            let found = members_up_to(text, 64).expect("one object");
            assert_eq!(found.len(), ConnLimits::HELLO_KEYS);
            for (index, member) in found.iter().enumerate() {
                assert_eq!(member.kind, Kind::Number);
                let key = HELLO_KEY_NAMES.get(index).expect("key name");
                assert_eq!(text.get(member.key.clone()), Some(key.as_bytes()));
                let field = fields.get(index).expect("field value");
                let raw = text.get(member.value.clone()).expect("value");
                assert_eq!(raw, field.to_string().as_bytes(), "{key}");
            }
        }
    }
}
