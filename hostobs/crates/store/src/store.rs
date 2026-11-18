//! The [`Store`]: the tiers, the active dictionary and the bounded writer queue, tied together
//! under one configuration and one set of counters.
//!
//! Plan v4 §A.4 lists the stores as one table and §C.5 states the failure each bound exists
//! for. This module is that table as code: one place that opens every tier, refuses a
//! configuration that cannot be enforced (a hot tier that is not on tmpfs, two tiers with the
//! same name, a cap of zero bytes), routes a record to the tier its step belongs to, retires
//! dictionary entries from the union of what the tiers still reference, and reports every
//! loss.
//!
//! # Contracts
//!
//! * **The configuration is a policy choice, and a missing key is an error.** The plan's rule
//!   is "every key required", and this module's [`StoreConfig::check`] is where that is
//!   enforced: there is no default cap, no default step and no "tmpfs if you happen to be on
//!   it". A key that is absent is [`StoreError::Config`], never a guess (the estate's
//!   "defaults are hazards" rule).
//! * **A tier's directory may be shared by focus tiers, and its name must not be.** Focus
//!   tiers live inside the cold directory (§A.4: "focus tiers (`[[focus]]`) | cold dir | own
//!   `step_s/keep_s/cap_bytes`"), so one [`StoreDir`] can hold several tiers. Each tier
//!   therefore only ever claims names whose *tier prefix* is its own, and the scan's
//!   `names another tier` note is what proves a shared directory is not a collision.
//! * **`require_tmpfs` is checked before a single byte is written.**
//!   [`MountTable`](crate::tmpfs::MountTable) answers "is this path on tmpfs", and a tier that
//!   requires tmpfs and is not on it refuses to open ([`StoreError::NotTmpfs`]). An unreadable
//!   mount table refuses too, rather than assuming disk: "could not determine" is never
//!   reported as "is not tmpfs" or as "is tmpfs".
//! * **One writer.** [`Store::submit`] puts a job on the bounded queue; [`Store::write_pending`]
//!   is the only way a job reaches a segment, and it writes at most `max_jobs` per call, so a
//!   caller cannot accidentally drain an unbounded amount in one loop.
//! * **Retirement is by reachability, from all tiers at once.** §A.4: "inactive incs retire
//!   with their segments". [`Store::retire`] unions every tier's reachable set before retiring
//!   anything, so an id that moved from hot to cold by re-sealing is never retired between the
//!   two — and it unions the two sources a segment cannot see: the jobs still in the writer
//!   queue, and the ids this store minted or adopted whose first record is not on disk yet. An
//!   id the store still owes is **live**, so a pass can never free it and turn a later write
//!   into a record naming an id the index no longer holds.
//! * **A store's loss counters are cumulative and typed.** Every dropped job, every torn tail,
//!   every quarantine and every disk-full is in [`StoreStats`]; nothing is a log line only.

use std::collections::BTreeMap;
use std::collections::BTreeSet;
use std::collections::VecDeque;
use std::path::{Path, PathBuf};

use crate::dictionary::{ActiveDictionary, Retired, DEFAULT_ACTIVE_MAX};
use crate::error::StoreError;
use crate::events::{Event, EventsStore};
use crate::format::TierKind;
use crate::fsio::StoreDir;
use crate::queue::{Job, QueueStats, Submit, WriterQueue};
use crate::tier::{AppendOutcome, Evicted, Tier, TierConfig, TierNote};

/// The default read bound of one segment file.
///
/// Plan v4's per-tier budget is `max_payload × 64` (64 MiB at the 1 MiB payload bound), and a
/// writer seals *after* appending the record that crossed the budget, so a legitimate segment
/// may hold one record more than its budget. The read bound must therefore exceed the largest
/// tier's [`TierConfig::segment_file_bound`] — 64 MiB + a 1 MiB record + slack — or the store
/// would refuse to read a segment it just wrote, leave it out of the ring and never evict it
/// (`StoreConfig::check` refuses that combination rather than allowing it).
pub const DEFAULT_MAX_FILE_BYTES: usize = 96 << 20;

/// The tier names the store always has, in the order the plan lists them.
pub const HOT: &str = "hot";
/// The tier name of the 5 s rollup history.
pub const WARM: &str = "warm";
/// The tier name of the on-disk history.
pub const COLD: &str = "cold";
/// The events corpus' tier name.
pub const EVENTS: &str = "events";

/// Where each corpus lives: one directory for the tmpfs tiers, one for cold, one for focus, one
/// for events.
///
/// The directories are *declared*, not derived: the plan gives each of them its own path
/// (`/run/hostobs/tiers`, `/var/lib/hostobs/probe/cold`, `/var/lib/hostobs/probe/events`), and a
/// store that guessed a layout from the first tier's name would put a caller's focus tier
/// somewhere nobody configured.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StoreDirs {
    /// The tmpfs directory hot and warm share (`/run/hostobs/tiers`), or `None` when the
    /// caller keeps them elsewhere.
    pub tmpfs: Option<PathBuf>,
    /// The durable cold history directory (`/var/lib/hostobs/probe/cold`).
    pub cold: PathBuf,
    /// The events directory (`/var/lib/hostobs/probe/events`).
    pub events: PathBuf,
}

impl StoreDirs {
    /// The plan's paths, rooted at `root` so a test or a probe restart can use a temp dir.
    #[must_use]
    pub fn under(root: &Path) -> StoreDirs {
        StoreDirs {
            tmpfs: Some(root.join("tiers")),
            cold: root.join("cold"),
            events: root.join("events"),
        }
    }

    /// The directory one tier's segments go in.
    ///
    /// # Errors
    ///
    /// [`StoreError::Config`] when a tmpfs tier is configured without a tmpfs directory.
    pub fn for_tier(&self, config: &TierConfig) -> Result<&Path, StoreError> {
        match config.kind {
            TierKind::Events => Ok(&self.events),
            TierKind::History => {
                if config.require_tmpfs {
                    match &self.tmpfs {
                        Some(path) => Ok(path),
                        None => Err(StoreError::Config {
                            key: "tmpfs_dir",
                            reason: "a tmpfs tier needs a tmpfs directory",
                        }),
                    }
                } else {
                    Ok(&self.cold)
                }
            }
        }
    }
}

/// The store's configuration: every key the plan requires, and no default for any of them.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StoreConfig {
    /// The history tiers, in the order the store routes records.
    pub tiers: Vec<TierConfig>,
    /// The events tier's configuration (its own cap, §A.4).
    pub events: TierConfig,
    /// The writer queue's byte bound (§A.4's `writer_queue_bytes`).
    pub writer_queue_bytes: u64,
    /// The active dictionary's entry bound (§A.4's `dict_active_max`).
    pub dict_active_max: usize,
    /// The events RAM ring's byte bound (§A.4).
    pub events_ring_bytes: u64,
    /// The epoch the probe is in (§C.1: the store never reuses an id across epochs).
    pub epoch: u64,
    /// The dictionary generation the writer is currently emitting (§B.3).
    pub dict_generation: u32,
    /// Where the tiers live.
    pub dirs: StoreDirs,
    /// The largest segment file a reader will hold in memory.
    pub max_file_bytes: usize,
}

impl StoreConfig {
    /// The plan's proposal for §A.4 as one configuration: hot 1 s × 60 s and warm 5 s × 300 s
    /// on tmpfs, cold 60 s steps with a 1 GiB cap and 14 days, a 8 MiB writer queue, a 64 MiB
    /// events cap with a 4 MiB ring, and `dict_active_max` 65,536.
    ///
    /// This is *the plan's* numbers, not a default: it is built only when a caller asks for it
    /// by name, and every field is set here rather than inherited from an absent key.
    ///
    /// # Errors
    ///
    /// Every [`TierConfig`] refusal (`segment_s`/`keep_s` of zero, a name that cannot be a
    /// file prefix).
    pub fn plan_v4(
        root: &Path,
        epoch: u64,
        dict_generation: u32,
    ) -> Result<StoreConfig, StoreError> {
        let hot = TierConfig::history(HOT, 1, 60, 64 << 20, true)?;
        let warm = TierConfig::history(WARM, 5, 300, 128 << 20, true)?;
        let cold = TierConfig::history(COLD, 60, 14 * 24 * 3600, 1 << 30, false)?;
        let events = TierConfig::new(
            EVENTS,
            TierKind::Events,
            60,
            14 * 24 * 3600,
            64 << 20,
            false,
        )?;
        Ok(StoreConfig {
            tiers: vec![hot, warm, cold],
            events,
            writer_queue_bytes: 8 << 20,
            dict_active_max: DEFAULT_ACTIVE_MAX,
            events_ring_bytes: 4 << 20,
            epoch,
            dict_generation,
            dirs: StoreDirs::under(root),
            max_file_bytes: DEFAULT_MAX_FILE_BYTES,
        })
    }

    /// Check everything that can be checked without touching the filesystem.
    ///
    /// # Errors
    ///
    /// [`StoreError::DuplicateTier`] for a repeated name, [`StoreError::Config`] for a
    /// meaningless bound, and the `events` tier's own checks.
    pub fn check(&self) -> Result<(), StoreError> {
        if self.tiers.is_empty() {
            return Err(StoreError::Config {
                key: "tiers",
                reason: "a store needs at least one history tier",
            });
        }
        let mut seen: BTreeSet<&str> = BTreeSet::new();
        for tier in &self.tiers {
            if !seen.insert(tier.name.as_str()) {
                return Err(StoreError::DuplicateTier {
                    name: tier.name.clone(),
                });
            }
            if tier.kind != TierKind::History {
                return Err(StoreError::TierKindMismatch {
                    tier: "history tiers",
                    payload: tier.kind.name(),
                });
            }
        }
        if self.events.kind != TierKind::Events {
            return Err(StoreError::TierKindMismatch {
                tier: "the events tier",
                payload: self.events.kind.name(),
            });
        }
        if seen.contains(self.events.name.as_str()) {
            return Err(StoreError::DuplicateTier {
                name: self.events.name.clone(),
            });
        }
        if self.writer_queue_bytes == 0 {
            return Err(StoreError::Config {
                key: "writer_queue_bytes",
                reason: "must be at least one byte",
            });
        }
        if self.dict_active_max == 0 {
            return Err(StoreError::Config {
                key: "dict_active_max",
                reason: "must hold at least one incarnation",
            });
        }
        if self.max_file_bytes < crate::format::HEADER_LEN {
            return Err(StoreError::Config {
                key: "max_file_bytes",
                reason: "cannot hold a segment header",
            });
        }
        // The read bound must be able to hold what the writer may legitimately produce: a
        // segment whose budget was reached by the record that crossed it. A smaller bound makes
        // a store-written segment unreadable, so the rescan could not put it in the ring, the
        // byte total would understate the disk, and eviction would never reach it.
        for tier in self.tiers.iter().chain(std::iter::once(&self.events)) {
            let needed = tier.segment_file_bound();
            if u64::try_from(self.max_file_bytes).unwrap_or(u64::MAX) < needed {
                return Err(StoreError::Config {
                    key: "max_file_bytes",
                    reason: "is below a tier's own segment file bound",
                });
            }
        }
        Ok(())
    }

    /// The tier a record at `real_ns` belongs to: the **finest** tier whose step the time falls
    /// in.
    ///
    /// The rule is the plan's ladder, not a calendar: a record is first offered to the tier
    /// with the smallest `segment_s`, and the first tier whose retention covers the record's
    /// age keeps it. A record older than every tier's `keep_s` is refused
    /// ([`StoreError::UnknownTier`] carries the name it could not be placed under — a store
    /// does not silently drop a record it was handed).
    ///
    /// # Errors
    ///
    /// [`StoreError::NegativeTime`] for a time before the Unix epoch.
    pub fn route(&self, real_ns: i64, now_ns: i64) -> Result<&TierConfig, StoreError> {
        if real_ns < 0 {
            return Err(StoreError::NegativeTime { ns: real_ns });
        }
        if now_ns < real_ns {
            return Err(StoreError::NegativeTime {
                ns: now_ns - real_ns,
            });
        }
        let age_s = u64::try_from((now_ns - real_ns) / 1_000_000_000).unwrap_or(u64::MAX);
        let mut candidates: Vec<&TierConfig> = self
            .tiers
            .iter()
            .filter(|tier| age_s <= tier.keep_s)
            .collect();
        // The finest step wins, then the smallest cap: a record the store was just handed
        // belongs in the most detailed tier that will keep it.
        candidates.sort_by(|left, right| {
            left.segment_s
                .cmp(&right.segment_s)
                .then(left.cap_bytes.cmp(&right.cap_bytes))
                .then(left.name.cmp(&right.name))
        });
        candidates.first().copied().ok_or(StoreError::UnknownTier {
            name: format!("a record {age_s} s old"),
        })
    }

    /// The tier named `name`, when the configuration has one.
    #[must_use]
    pub fn tier(&self, name: &str) -> Option<&TierConfig> {
        self.tiers.iter().find(|tier| tier.name == name)
    }
}

/// The most eviction reports ([`Evicted::notes`]) the store keeps for [`Store::notes`]; the
/// oldest leave first, so a source of repeated vanished files cannot grow the store's memory.
pub const MAX_EVICTION_NOTES: usize = 256;

/// What the store as a whole has done and lost.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct StoreStats {
    /// Jobs accepted by the writer queue since the store was built.
    pub queued: u64,
    /// Jobs written to a segment.
    pub written: u64,
    /// Jobs dropped because the queue was at its byte bound.
    pub dropped: u64,
    /// Records lost with those dropped jobs.
    pub dropped_records: u64,
    /// Bytes lost with those dropped jobs.
    pub dropped_bytes: u64,
    /// Records that reached a segment.
    pub records: u64,
    /// Segment bytes on disk: the sum of every tier's own byte total (live segments and
    /// quarantined files) and the events store's. It is recomputed from those totals after
    /// every operation that can change one, never maintained by arithmetic of its own, so it
    /// cannot drift from them (HOB-56).
    pub bytes: u64,
    /// Whole segments evicted by a bound, all tiers.
    pub evicted_segments: u64,
    /// Quarantined files (`*.corrupt`) evicted by a bound, all tiers; counted apart from
    /// `evicted_segments` so a damage storm is visible as such (HOB-43).
    pub evicted_quarantined: u64,
    /// Bytes those evicted segments and quarantined files held.
    pub evicted_bytes: u64,
    /// Torn tails repaired at open, all tiers.
    pub torn_repaired: u64,
    /// Segments quarantined because they were corrupt.
    pub quarantined: u64,
    /// Dictionary entries retired (ids no stored segment references).
    pub retired: u64,
    /// Incarnations the active index holds now.
    pub dictionary_entries: u64,
    /// Append-level failures (a full disk, an IO error, a refused payload).
    pub write_failures: u64,
}

impl StoreStats {
    /// Count what one eviction pass removed: live segments and quarantined files apart, the
    /// bytes together.
    fn note_eviction(&mut self, done: &Evicted) {
        self.evicted_segments = self
            .evicted_segments
            .saturating_add(u64::try_from(done.len()).unwrap_or(u64::MAX));
        self.evicted_quarantined = self
            .evicted_quarantined
            .saturating_add(u64::try_from(done.quarantined.len()).unwrap_or(u64::MAX));
        self.evicted_bytes = self.evicted_bytes.saturating_add(done.bytes);
    }

    /// True when the store lost nothing: no dropped job, no quarantine, no write failure.
    ///
    /// A torn tail is a *repair*, not a loss: the record it cut back to was never completed, so
    /// the store is honest rather than lossless there.
    #[must_use]
    pub const fn lossless(&self) -> bool {
        self.dropped == 0 && self.quarantined == 0 && self.write_failures == 0
    }
}

/// One tier's living state inside the store.
#[derive(Debug)]
struct TierState {
    tier: Tier,
}

/// The whole store: tiers, the dictionary, the queue, and the counters.
#[derive(Debug)]
pub struct Store {
    config: StoreConfig,
    tiers: BTreeMap<String, TierState>,
    events: EventsStore,
    dictionary: ActiveDictionary,
    queue: WriterQueue,
    stats: StoreStats,
    /// Incarnation ids minted or adopted for which no record has been written yet.
    ///
    /// A segment cannot prove an id is live before its first record exists, and the id is
    /// exactly what the next write will name, so the store pins it here: [`Store::adopt`]
    /// inserts, a successful [`Store::write_one`] releases only the ids that record named, and
    /// [`Store::retire`] unions the set. Without it, `adopt(0, …)` followed by a retirement pass
    /// would free an id the store is about to write (the review of `pi-hostobs-m1c`, B1).
    unwritten: BTreeSet<u64>,
    /// The reports of eviction passes ([`Evicted::notes`]), newest last, at most
    /// [`MAX_EVICTION_NOTES`]: a tier's own `scan.notes` is reset by a rescan, and these are
    /// what the caller of `write_one`, `record_event` or `evict` is shown by [`Store::notes`].
    eviction_notes: VecDeque<TierNote>,
}

/// Keep the reports of one eviction pass, dropping the oldest past [`MAX_EVICTION_NOTES`].
fn keep_notes(ring: &mut VecDeque<TierNote>, done: &Evicted) {
    for note in &done.notes {
        if ring.len() >= MAX_EVICTION_NOTES {
            ring.pop_front();
        }
        ring.push_back(note.clone());
    }
}

impl Store {
    /// Open the store: check the configuration, prove the tmpfs tiers are on tmpfs, then open
    /// every tier and the events store.
    ///
    /// The tmpfs check happens **before** the directories are created, so a misconfigured hot
    /// tier does not first write to a disk it will then refuse.
    ///
    /// # Errors
    ///
    /// [`StoreError::Config`]/[`StoreError::DuplicateTier`] for the configuration,
    /// [`StoreError::NotTmpfs`]/[`StoreError::MountTableUnreadable`] for a `require_tmpfs`
    /// tier, and every tier/dictionary/queue refusal.
    pub fn open(config: StoreConfig) -> Result<Store, StoreError> {
        Store::open_with_mounts(config, None)
    }

    /// Open the store against a mount table a caller supplies (the tests' entry point, and a
    /// probe that has already read `/proc/self/mounts`).
    ///
    /// Passing `None` reads `/proc/self/mounts` here, and only when a tier requires tmpfs.
    ///
    /// # Errors
    ///
    /// As [`Store::open`], plus [`StoreError::MountTableUnreadable`] when the table cannot be
    /// read.
    pub fn open_with_mounts(
        config: StoreConfig,
        mounts: Option<crate::tmpfs::MountTable>,
    ) -> Result<Store, StoreError> {
        config.check()?;
        // Read the table at most once, and only when a tier actually requires tmpfs. The table
        // is passed to the later tiers rather than re-read per tier.
        let mut table = mounts;
        for tier in config.tiers.iter().chain(std::iter::once(&config.events)) {
            if !tier.require_tmpfs {
                continue;
            }
            if table.is_none() {
                table = Some(crate::tmpfs::read_self()?);
            }
            let path = config.dirs.for_tier(tier)?;
            let Some(current) = table.as_ref() else {
                return Err(StoreError::MountTableUnreadable {
                    reason: "no mount table was available".to_string(),
                });
            };
            // A required tier whose filesystem cannot be determined is a refusal, never a pass:
            // "could not determine" is not "is tmpfs".
            current.check_tmpfs(path, true)?;
        }
        Store::open_inner(config)
    }

    fn open_inner(config: StoreConfig) -> Result<Store, StoreError> {
        let mut tiers: BTreeMap<String, TierState> = BTreeMap::new();
        for tier_config in &config.tiers {
            let path = config.dirs.for_tier(tier_config)?;
            let dir = StoreDir::open(path, true)?;
            let tier = Tier::open(tier_config.clone(), dir, config.max_file_bytes)?;
            tiers.insert(tier_config.name.clone(), TierState { tier });
        }
        let events_path = config.dirs.for_tier(&config.events)?;
        let events = EventsStore::open(
            config.events.clone(),
            config.events_ring_bytes,
            StoreDir::open(events_path, true)?,
            config.max_file_bytes,
        )?;
        let dictionary = ActiveDictionary::new(config.epoch, config.dict_active_max)?;
        let queue = WriterQueue::new(config.writer_queue_bytes)?;
        let mut store = Store {
            config,
            tiers,
            events,
            dictionary,
            queue,
            stats: StoreStats::default(),
            unwritten: BTreeSet::new(),
            eviction_notes: VecDeque::new(),
        };
        store.recount();
        Ok(store)
    }

    /// The configuration.
    #[must_use]
    pub const fn config(&self) -> &StoreConfig {
        &self.config
    }

    /// The tier named `name`.
    #[must_use]
    pub fn tier(&self, name: &str) -> Option<&Tier> {
        self.tiers.get(name).map(|state| &state.tier)
    }

    /// The events store.
    #[must_use]
    pub const fn events(&self) -> &EventsStore {
        &self.events
    }

    /// The active dictionary.
    #[must_use]
    pub const fn dictionary(&self) -> &ActiveDictionary {
        &self.dictionary
    }

    /// The writer queue.
    #[must_use]
    pub const fn queue(&self) -> &WriterQueue {
        &self.queue
    }

    /// The store's counters, as they now stand.
    #[must_use]
    pub fn stats(&self) -> StoreStats {
        let QueueStats {
            accepted,
            dropped,
            dropped_records,
            dropped_bytes,
            ..
        } = self.queue.full_stats();
        let mut stats = self.stats.clone();
        stats.queued = accepted;
        // `written` stays the store's own count of **successful appends** (`write_one`), not the
        // queue's count of jobs *handed* to a writer: those two numbers differ by exactly the
        // failed appends, and a caller reading "3 written" from a store whose third append was
        // refused would be reading a false success. The queue's own hand-off count is not the
        // store's `written` for that reason.
        //
        // `dropped` is the queue's own count, and the events store's `dropped` is the same
        // number by construction (`submit` mirrors every queue drop into it) — so this is one
        // fact with one source, not a `max` of two counters that would hide a future
        // disagreement behind a plausible-looking total. The events *ring's* displacement is a
        // different loss and stays in `EventsStats::displaced`, where a caller can see it.
        stats.dropped = dropped;
        stats.dropped_records = dropped_records;
        stats.dropped_bytes = dropped_bytes;
        stats.dictionary_entries = u64::try_from(self.dictionary.len()).unwrap_or(u64::MAX);
        stats
    }

    /// Recount what the tiers actually hold (after an open or an eviction pass).
    fn recount(&mut self) {
        self.refresh_bytes();
        let mut records = 0u64;
        let mut torn = self
            .events
            .tier()
            .scan()
            .torn_repaired
            .len()
            .try_into()
            .unwrap_or(u64::MAX);
        let mut quarantined: u64 = self
            .events
            .tier()
            .scan()
            .quarantined
            .len()
            .try_into()
            .unwrap_or(u64::MAX);
        for state in self.tiers.values() {
            records = records.saturating_add(
                state
                    .tier
                    .scan()
                    .segments
                    .iter()
                    .map(|segment| segment.records)
                    .sum::<u64>(),
            );
            torn = torn.saturating_add(
                u64::try_from(state.tier.scan().torn_repaired.len()).unwrap_or(u64::MAX),
            );
            quarantined = quarantined.saturating_add(
                u64::try_from(state.tier.scan().quarantined.len()).unwrap_or(u64::MAX),
            );
        }
        self.stats.records = records;
        self.stats.torn_repaired = torn;
        self.stats.quarantined = quarantined;
    }

    /// Set `stats.bytes` to the sum of what the tiers themselves report: every history tier
    /// and the events store, each total counting live segments and quarantined files. The
    /// tiers' own totals are the single source (HOB-56); this is called after every
    /// operation that can change one, on the error path as well as the success path.
    fn refresh_bytes(&mut self) {
        let mut bytes = self.events.tier().bytes();
        for state in self.tiers.values() {
            bytes = bytes.saturating_add(state.tier.bytes());
        }
        self.stats.bytes = bytes;
    }

    /// The names a tier left untouched, with why (a foreign file is reported, never removed).
    ///
    /// A tier that shares its directory with another tier of this store sees the other's
    /// segments and reports each as "names another tier". Those are *expected*: one directory
    /// holding hot and warm is the plan's own layout, so a note about a name this store owns
    /// is not reported as foreign. What remains is a name **no** tier of this store claims —
    /// which is exactly what an operator needs to see.
    ///
    /// The reports of eviction passes follow (an entry dropped because its file was already
    /// gone, or could not be removed): they name a file this store owns, so they are not
    /// filtered, they survive a tier's rescan, and at most [`MAX_EVICTION_NOTES`] are kept
    /// (HOB-58). A quarantined file that could not be removed stays on disk, uncounted, and
    /// is adopted again at the next restart.
    #[must_use]
    pub fn notes(&self) -> Vec<TierNote> {
        let owned: Vec<&str> = self
            .tiers
            .keys()
            .map(String::as_str)
            .chain(std::iter::once(self.config.events.name.as_str()))
            .collect();
        let mut notes: Vec<TierNote> = Vec::new();
        for state in self.tiers.values() {
            notes.extend(state.tier.scan().notes.iter().cloned());
        }
        notes.extend(self.events.tier().scan().notes.iter().cloned());
        // A quarantined name (`<segment name>.corrupt`) is a segment name of the tier it names
        // too: hot and warm share a directory, so warm notes hot's quarantined file as "names
        // another tier", and that is as expected as the same note about a live segment.
        notes.retain(|note| {
            let parsed = crate::format::SegmentFileName::parse(&note.name)
                .or_else(|_| crate::format::SegmentFileName::parse_quarantined(&note.name));
            match parsed {
                Ok(parsed) => !owned.contains(&parsed.tier.as_str()),
                Err(_) => true,
            }
        });
        for note in &self.eviction_notes {
            if !notes.contains(note) {
                notes.push(note.clone());
            }
        }
        notes
    }

    /// Mint or adopt an incarnation in the active index.
    ///
    /// # Errors
    ///
    /// [`StoreError::DictionaryFull`] at the bound, [`StoreError::IncarnationReused`] for an
    /// id the index retired, and every [`ActiveDictionary::adopt`] refusal.
    pub fn adopt(&mut self, inc: u64, label: &str) -> Result<u64, StoreError> {
        if inc == 0 {
            let minted = self.dictionary.mint(label)?;
            self.unwritten.insert(minted);
            return Ok(minted);
        }
        self.dictionary.adopt(inc, label)?;
        self.unwritten.insert(inc);
        Ok(inc)
    }

    /// The ids this store has minted or adopted that no record names yet.
    ///
    /// These are live by [`Store::retire`]'s own rule: the store owes their first record. The
    /// set is bounded by `dict_active_max`, because it is a subset of the active index.
    #[must_use]
    pub const fn unwritten(&self) -> &BTreeSet<u64> {
        &self.unwritten
    }

    /// Put a job on the writer queue, reporting exactly what it displaced.
    ///
    /// # Errors
    ///
    /// [`StoreError::JobTooLarge`] when the job alone is larger than the whole queue.
    pub fn submit(&mut self, job: Job) -> Result<Submit, StoreError> {
        let report = self.queue.submit(job)?;
        if report.dropped_anything() {
            // The queue counts its own drops; the store mirrors them so one counter answers
            // "did this store lose anything", and the *events* store's drop count is not
            // silently separate from the history's.
            self.events.count_dropped(report.dropped_jobs);
        }
        Ok(report)
    }

    /// Write up to `max_jobs` queued jobs, routing each to the tier its time belongs to.
    ///
    /// `now_ns` decides which tier a record belongs to **and** which bounds are enforced; it is
    /// the caller's clock, so a test never depends on host time.
    ///
    /// # Errors
    ///
    /// The first refusal is returned and the job stays out of the queue (it was already taken,
    /// so it is counted as a write failure rather than silently lost).
    pub fn write_pending(
        &mut self,
        now_ns: i64,
        max_jobs: usize,
    ) -> Result<Vec<WriteOutcome>, StoreError> {
        if max_jobs == 0 {
            return Err(StoreError::QueryLimit { asked: 0, max: 0 });
        }
        let mut done = Vec::new();
        for _ in 0..max_jobs {
            let Some(job) = self.queue.take_next() else {
                break;
            };
            match self.write_one(&job, now_ns) {
                Ok(outcome) => done.push(outcome),
                Err(error) => {
                    self.stats.write_failures = self.stats.write_failures.saturating_add(1);
                    return Err(error);
                }
            }
        }
        Ok(done)
    }

    /// Write one job into the tier `route` names.
    ///
    /// # Errors
    ///
    /// [`StoreError::UnknownTier`] when no tier's retention covers the record, and every
    /// append refusal.
    pub fn write_one(&mut self, job: &Job, now_ns: i64) -> Result<WriteOutcome, StoreError> {
        let route = self.config.route(job.real_ns, now_ns)?;
        let name = route.name.clone();
        let epoch = self.config.epoch;
        let generation = self.config.dict_generation;
        let state = self
            .tiers
            .get_mut(&name)
            .ok_or(StoreError::UnknownTier { name: name.clone() })?;
        let appended = state.tier.append(job, epoch, generation);
        // The tier's own total is the truth, on a refusal as well (a seal or an eviction may
        // have changed it before the error).
        self.refresh_bytes();
        let outcome = appended?;
        // The record is on disk, so the ids it named are no longer "owed": the segment that
        // holds them is now their proof. A *failed* append leaves them pinned, because the
        // store never durably named them and the caller may resubmit the same job.
        for inc in &job.incarnations {
            self.unwritten.remove(inc);
        }
        self.stats.written = self.stats.written.saturating_add(1);
        self.stats.records = self.stats.records.saturating_add(1);
        self.stats.note_eviction(&outcome.evicted);
        keep_notes(&mut self.eviction_notes, &outcome.evicted);
        Ok(WriteOutcome {
            tier: name,
            outcome,
        })
    }

    /// Append one event to the events store (its own cap and ring, §A.4).
    ///
    /// # Errors
    ///
    /// [`StoreError::PayloadFrameType`] for a payload that is not an EVT frame, and every
    /// append refusal.
    pub fn record_event(
        &mut self,
        event: &Event,
        payload: &[u8],
        now_ns: i64,
    ) -> Result<AppendOutcome, StoreError> {
        let recorded = self.events.record(
            event,
            payload,
            self.config.epoch,
            self.config.dict_generation,
        );
        self.refresh_bytes();
        let outcome = recorded?;
        self.stats.records = self.stats.records.saturating_add(1);
        self.stats.note_eviction(&outcome.evicted);
        keep_notes(&mut self.eviction_notes, &outcome.evicted);
        let _ = now_ns;
        Ok(outcome)
    }

    /// Seal every tier's open segment and the events store's.
    ///
    /// # Errors
    ///
    /// The first seal refusal (each seal is atomic, so a refusal leaves the segment open and
    /// recoverable rather than half-renamed).
    pub fn seal_all(&mut self) -> Result<Vec<String>, StoreError> {
        let sealed = self.seal_each();
        // A seal replaces the open name by the sealed one (footer bytes), so the total moves.
        self.refresh_bytes();
        sealed
    }

    /// Seal the events store's open segment and then every tier's, stopping at the first
    /// refusal.
    fn seal_each(&mut self) -> Result<Vec<String>, StoreError> {
        let mut sealed = Vec::new();
        if let Some(name) = self.events.seal()? {
            sealed.push(name);
        }
        for state in self.tiers.values_mut() {
            if let Some(name) = state.tier.seal_open_segment()? {
                sealed.push(name);
            }
        }
        Ok(sealed)
    }

    /// Enforce every tier's bounds (and the events store's) for `now_ns`.
    ///
    /// # Errors
    ///
    /// The first eviction refusal.
    pub fn evict(&mut self, now_ns: i64) -> Result<BTreeMap<String, Evicted>, StoreError> {
        let mut evicted: BTreeMap<String, Evicted> = BTreeMap::new();
        let result = self.evict_each(now_ns, &mut evicted);
        // The tiers' own totals are the truth, after a refusal as well.
        self.refresh_bytes();
        result?;
        Ok(evicted)
    }

    /// Run every tier's eviction and then the events store's, collecting what each removed
    /// (a pass that only reported a vanished file is kept too, for its notes) and stopping
    /// at the first refusal.
    fn evict_each(
        &mut self,
        now_ns: i64,
        evicted: &mut BTreeMap<String, Evicted>,
    ) -> Result<(), StoreError> {
        for (name, state) in &mut self.tiers {
            let done = state.tier.evict(now_ns)?;
            keep_notes(&mut self.eviction_notes, &done);
            if !done.is_empty() {
                self.stats.note_eviction(&done);
                evicted.insert(name.clone(), done);
            }
        }
        let done = self.events.evict(now_ns)?;
        keep_notes(&mut self.eviction_notes, &done);
        if !done.is_empty() {
            self.stats.note_eviction(&done);
            evicted.insert(EVENTS.to_string(), done);
        }
        Ok(())
    }

    /// Retire dictionary entries nothing can still reach (§A.4's "GC by segment reachability").
    ///
    /// Reachability has **three** sources and not one:
    ///
    /// * every tier and the events corpus, for the records a stored segment still names;
    /// * [`WriterQueue::pending_incarnations`], for the records the store owes but has not
    ///   written (a queued job is a record in flight);
    /// * [`Store::unwritten`], for ids minted or adopted whose first record does not exist yet.
    ///
    /// A pass therefore cannot free a live id. Only an id that no segment, no queued job and no
    /// pin names is retired — and from then on [`Store::adopt`] of that id is the documented
    /// [`StoreError::IncarnationReused`], never a silent id reuse.
    #[must_use]
    pub fn retire(&mut self) -> Retired {
        let mut reach: BTreeSet<u64> = self.reachable();
        for inc in self.queue.pending_incarnations() {
            reach.insert(inc);
        }
        for inc in &self.unwritten {
            reach.insert(*inc);
        }
        let retired = self.dictionary.retire_with_set(&reach);
        self.stats.retired = self.stats.retired.saturating_add(retired.len() as u64);
        self.stats.dictionary_entries = u64::try_from(self.dictionary.len()).unwrap_or(u64::MAX);
        retired
    }

    /// The ids any stored segment still references, across every tier.
    ///
    /// This deliberately means *on disk only*: [`Store::retire`] adds the writer queue's own
    /// pending ids and this store's unwritten pins to it, and a caller that wants the whole
    /// live set should read `retire`'s rule rather than this one (the two are different facts).
    #[must_use]
    pub fn reachable(&self) -> BTreeSet<u64> {
        let mut reach = BTreeSet::new();
        for state in self.tiers.values() {
            reach.extend(state.tier.reachable());
        }
        reach.extend(self.events.tier().reachable());
        reach
    }

    /// A range query over every history tier, oldest tier first, bounded by the caller.
    ///
    /// The tiers are consulted in the configuration's order (hot, then warm, then cold), and a
    /// bound stops the whole answer rather than each tier separately.
    ///
    /// # Errors
    ///
    /// [`StoreError::RangeInverted`], [`StoreError::QueryLimit`], and every tier refusal.
    pub fn range(
        &self,
        from: i64,
        to: i64,
        max_records: usize,
        max_bytes: usize,
    ) -> Result<StoreRange, StoreError> {
        if to < from {
            return Err(StoreError::RangeInverted { from, to });
        }
        if max_records == 0 {
            return Err(StoreError::QueryLimit { asked: 0, max: 0 });
        }
        let mut answer = StoreRange::default();
        let mut bytes = 0usize;
        for tier_config in &self.config.tiers {
            let Some(state) = self.tiers.get(&tier_config.name) else {
                continue;
            };
            let part = state.tier.range(
                from,
                to,
                max_records.saturating_sub(answer.records.len()),
                max_bytes.saturating_sub(bytes),
            )?;
            for record in &part.records {
                bytes = bytes.saturating_add(record.payload.len().saturating_add(64));
            }
            answer.truncated |= part.truncated;
            answer
                .records
                .extend(part.records.into_iter().map(|record| RoutedRecord {
                    tier: tier_config.name.clone(),
                    record,
                }));
            if answer.records.len() >= max_records {
                answer.truncated = true;
                break;
            }
        }
        Ok(answer)
    }
}

/// One written record and where it landed.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WriteOutcome {
    /// The tier the record went to.
    pub tier: String,
    /// What the append did.
    pub outcome: AppendOutcome,
}

/// One record in a store-wide range answer, with its tier.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RoutedRecord {
    /// The tier it was stored in.
    pub tier: String,
    /// The record itself.
    pub record: crate::segment::ScannedRecord,
}

/// A store-wide range answer.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct StoreRange {
    /// The records in the window, tier by tier (hot, warm, cold).
    pub records: Vec<RoutedRecord>,
    /// True when a bound stopped the answer.
    pub truncated: bool,
}

impl StoreRange {
    /// How many records the answer holds.
    #[must_use]
    pub fn len(&self) -> usize {
        self.records.len()
    }

    /// True when the window held nothing.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.records.is_empty()
    }

    /// The tiers the answer touched, in order.
    #[must_use]
    pub fn tiers(&self) -> Vec<&str> {
        let mut seen: Vec<&str> = Vec::new();
        for record in &self.records {
            if !seen.contains(&record.tier.as_str()) {
                seen.push(record.tier.as_str());
            }
        }
        seen
    }
}

#[cfg(test)]
mod tests {
    use super::{
        StoreConfig, StoreDirs, StoreStats, COLD, DEFAULT_ACTIVE_MAX, DEFAULT_MAX_FILE_BYTES,
        EVENTS, HOT, WARM,
    };
    use crate::error::StoreError;
    use crate::events::{EventKind, Outcome};
    use crate::format::{TierKind, BLOCK_FRAME_TYPE, FRAME_FLAG_SECTIONED};
    use crate::fsio::testdir::TempDir;
    use crate::queue::Job;
    use crate::store::Store;
    use crate::tier::Tier;
    use crate::tmpfs::{Mount, MountTable};
    use std::collections::BTreeSet;

    fn block(len: usize) -> Vec<u8> {
        let mut frame = vec![0u8; len.max(16)];
        let declared = u32::try_from(frame.len()).expect("fits");
        frame[0..4].copy_from_slice(&declared.to_le_bytes());
        frame[4] = BLOCK_FRAME_TYPE;
        frame[5] = FRAME_FLAG_SECTIONED;
        frame[8..12].copy_from_slice(&9u32.to_le_bytes());
        frame
    }

    fn evt(extra: usize) -> Vec<u8> {
        let mut frame = block(16 + extra);
        frame[4] = crate::format::EVT_FRAME_TYPE;
        frame
    }

    /// A store configuration whose every field is set by the test, never defaulted.
    fn config(temp: &TempDir) -> StoreConfig {
        let mut store = StoreConfig::plan_v4(temp.path(), 4242, 7).expect("plan v4");
        store.tiers = vec![
            store.tiers[0].clone(),
            store.tiers[1].clone(),
            store.tiers[2].clone(),
        ];
        store.writer_queue_bytes = 1 << 16;
        store.dict_active_max = 16;
        store.events_ring_bytes = 1 << 12;
        // The read bound must exceed the largest tier's own segment file bound (the plan's
        // 64 MiB budget plus the record that crossed it), which `DEFAULT_MAX_FILE_BYTES`
        // does; a smaller value is refused by `check`.
        store.max_file_bytes = DEFAULT_MAX_FILE_BYTES;
        store
    }

    /// A mount table that says `/tmp` (where the tests live) is tmpfs, plus the store's own
    /// directories — so a test of the tmpfs *policy* never depends on the host's real mounts.
    fn tmpfs_everywhere() -> MountTable {
        MountTable::from_mounts(vec![Mount {
            point: "/".to_string(),
            fstype: "tmpfs".to_string(),
            options: "rw".to_string(),
        }])
    }

    fn disk_everywhere() -> MountTable {
        MountTable::from_mounts(vec![Mount {
            point: "/".to_string(),
            fstype: "ext4".to_string(),
            options: "rw".to_string(),
        }])
    }

    #[test]
    fn a_configuration_is_checked_key_by_key_and_a_duplicate_tier_is_refused() {
        let temp = TempDir::new("config");
        let store = config(&temp);
        assert_eq!(store.check(), Ok(()));
        assert_eq!(store.tiers.len(), 3);
        assert_eq!(store.dict_active_max, 16);
        assert_eq!(store.writer_queue_bytes, 1 << 16);
        assert_eq!(store.epoch, 4242);
        assert_eq!(store.dict_generation, 7);
        assert_eq!(store.dirs.tmpfs, Some(temp.path().join("tiers")));
        assert_eq!(store.dirs.cold, temp.path().join("cold"));
        assert_eq!(store.dirs.events, temp.path().join("events"));
        assert_eq!(store.tier(HOT).map(|tier| tier.segment_s), Some(1));
        assert_eq!(store.tier(WARM).map(|tier| tier.keep_s), Some(300));
        assert_eq!(store.tier(COLD).map(|tier| tier.cap_bytes), Some(1 << 30));
        assert_eq!(store.tier("nope"), None);
        let mut duplicate = store.clone();
        duplicate.tiers.push(duplicate.tiers[0].clone());
        assert_eq!(
            duplicate.check(),
            Err(StoreError::DuplicateTier {
                name: HOT.to_string()
            })
        );
        let mut no_tiers = store.clone();
        no_tiers.tiers.clear();
        assert_eq!(
            no_tiers.check(),
            Err(StoreError::Config {
                key: "tiers",
                reason: "a store needs at least one history tier"
            })
        );
        let mut bad_events = store.clone();
        bad_events.events = store.tiers[0].clone();
        assert_eq!(
            bad_events.check(),
            Err(StoreError::TierKindMismatch {
                tier: "the events tier",
                payload: "history"
            })
        );
        let mut events_name_collides = store.clone();
        events_name_collides.tiers.push(
            crate::tier::TierConfig::new(EVENTS, TierKind::History, 60, 60, 1, false)
                .expect("config"),
        );
        assert_eq!(
            events_name_collides.check(),
            Err(StoreError::DuplicateTier {
                name: EVENTS.to_string()
            })
        );
        let mut no_queue = store.clone();
        no_queue.writer_queue_bytes = 0;
        assert_eq!(
            no_queue.check(),
            Err(StoreError::Config {
                key: "writer_queue_bytes",
                reason: "must be at least one byte"
            })
        );
        let mut no_dict = store.clone();
        no_dict.dict_active_max = 0;
        assert_eq!(
            no_dict.check(),
            Err(StoreError::Config {
                key: "dict_active_max",
                reason: "must hold at least one incarnation"
            })
        );
        let mut tiny_files = store;
        tiny_files.max_file_bytes = 8;
        assert_eq!(
            tiny_files.check(),
            Err(StoreError::Config {
                key: "max_file_bytes",
                reason: "cannot hold a segment header"
            })
        );
        // A bound that fits a header but not a tier's own largest legal segment is refused too:
        // accepting it would make a segment this store wrote unreadable, so the rescan could
        // not put it in the ring, the byte total would understate the disk and eviction would
        // never reach it.
        let mut below_segment = config(&temp);
        let needed = below_segment
            .tiers
            .iter()
            .map(crate::tier::TierConfig::segment_file_bound)
            .max()
            .expect("a tier");
        assert!(needed > crate::format::HEADER_LEN as u64);
        below_segment.max_file_bytes = usize::try_from(needed - 1).expect("fits");
        assert_eq!(
            below_segment.check(),
            Err(StoreError::Config {
                key: "max_file_bytes",
                reason: "is below a tier's own segment file bound"
            })
        );
        below_segment.max_file_bytes = usize::try_from(needed).expect("fits");
        assert_eq!(
            below_segment.check(),
            Ok(()),
            "exactly the bound is enough: the writer can never exceed it"
        );
    }

    #[test]
    fn the_plan_configuration_states_every_bound_the_plan_states() {
        let temp = TempDir::new("plan");
        let store = StoreConfig::plan_v4(temp.path(), 1, 1).expect("plan v4");
        assert_eq!(store.tiers[0].segment_s, 1);
        assert_eq!(store.tiers[0].keep_s, 60);
        assert!(store.tiers[0].require_tmpfs);
        assert_eq!(store.tiers[1].segment_s, 5);
        assert_eq!(store.tiers[1].keep_s, 300);
        assert!(store.tiers[1].require_tmpfs);
        assert_eq!(store.tiers[2].segment_s, 60);
        assert_eq!(store.tiers[2].keep_s, 14 * 24 * 3600);
        assert_eq!(store.tiers[2].cap_bytes, 1 << 30);
        assert!(!store.tiers[2].require_tmpfs);
        assert_eq!(store.events.kind, TierKind::Events);
        assert_eq!(store.events.cap_bytes, 64 << 20);
        assert_eq!(store.writer_queue_bytes, 8 << 20);
        assert_eq!(store.events_ring_bytes, 4 << 20);
        assert_eq!(store.dict_active_max, DEFAULT_ACTIVE_MAX);
        assert_eq!(store.dirs, StoreDirs::under(temp.path()));
    }

    #[test]
    fn a_record_is_routed_to_the_finest_tier_whose_retention_keeps_it() {
        let temp = TempDir::new("route");
        let store = config(&temp);
        let now = 1_000_000_000_000i64;
        assert_eq!(
            store.route(now, now).map(|tier| tier.name.as_str()),
            Ok(HOT)
        );
        assert_eq!(
            store
                .route(now - 30_000_000_000, now)
                .map(|tier| tier.name.as_str()),
            Ok(HOT),
            "a 30 s old record is still inside hot's 60 s window"
        );
        assert_eq!(
            store
                .route(now - 120_000_000_000, now)
                .map(|tier| tier.name.as_str()),
            Ok(WARM),
            "past hot's window, warm's 300 s keeps it"
        );
        assert_eq!(
            store
                .route(now - 600_000_000_000, now)
                .map(|tier| tier.name.as_str()),
            Ok(COLD)
        );
        // A record older than every retention is refused, never silently dropped. `now` is
        // far enough past the epoch that the record itself is still a positive time.
        let later = 2_000_000_000_000_000_000i64;
        assert_eq!(
            store.route(later - 20 * 24 * 3600 * 1_000_000_000, later),
            Err(StoreError::UnknownTier {
                name: "a record 1728000 s old".to_string()
            }),
            "older than every retention: refused, never silently dropped"
        );
        assert_eq!(
            store.route(-1, now),
            Err(StoreError::NegativeTime { ns: -1 })
        );
        assert_eq!(
            store.route(now, now - 1),
            Err(StoreError::NegativeTime { ns: -1 }),
            "a record from the future is a caller error"
        );
    }

    #[test]
    fn a_required_tmpfs_tier_is_refused_on_disk_and_a_determinate_one_on_unknown_filesystems() {
        let temp = TempDir::new("tmpfs");
        let store = config(&temp);
        assert_eq!(
            Store::open_with_mounts(store.clone(), Some(disk_everywhere())).map(|_| ()),
            Err(StoreError::NotTmpfs {
                tier: temp.path().join("tiers").display().to_string(),
                fstype: "ext4".to_string()
            })
        );
        // The refusal happened before any directory was created.
        assert!(!temp.path().join("tiers").exists());
        assert!(!temp.path().join("cold").exists());
        // An empty table cannot cover the path: "unknown" never passes a required check.
        assert_eq!(
            Store::open_with_mounts(store.clone(), Some(MountTable::default())).map(|_| ()),
            Err(StoreError::MountNotFound {
                path: temp.path().join("tiers").display().to_string()
            })
        );
        let ok = Store::open_with_mounts(store, Some(tmpfs_everywhere())).expect("opens");
        assert_eq!(ok.tier(HOT).map(|tier| tier.bytes()), Some(0));
        assert!(temp.path().join("tiers").is_dir());
        assert!(temp.path().join("cold").is_dir());
        assert!(temp.path().join("events").is_dir());
    }

    #[test]
    fn a_minted_id_is_live_until_its_record_reaches_a_segment_and_only_then_retirable() {
        // A minted id has no record yet, so no segment can prove it: retirement must keep it
        // anyway (the review of `pi-hostobs-m1c`, B1). The sequence is mint, retire (live),
        // write and seal (the segment now proves it), evict (nothing proves it) and only then
        // retire — and from that point `adopt` of the id is the documented typed refusal.
        let temp = TempDir::new("mint-retire");
        let mut config = config(&temp);
        config.tiers[0].cap_bytes = 0;
        let mut store = Store::open_with_mounts(config, Some(tmpfs_everywhere())).expect("open");
        let inc = store.adopt(0, "/probe").expect("mint");
        assert_eq!(store.unwritten(), &BTreeSet::from([inc]));
        // A retirement pass with no segment and no job holding it still keeps it: the store
        // owes its first record.
        assert!(
            store.retire().is_empty(),
            "a minted id is live before its record"
        );
        assert!(store.dictionary().holds(inc));
        assert!(store.reachable().is_empty(), "nothing on disk names it yet");
        store
            .submit(Job::new(block(16), 20_000_000_000, vec![inc]))
            .expect("submit");
        // A queued job is a record in flight; its ids are live too.
        assert!(store.retire().is_empty(), "a queued job keeps its ids live");
        assert_eq!(
            store
                .adopt(inc, "/probe")
                .expect("an id the index holds is adopted again"),
            inc
        );
        store.write_pending(20_000_000_000, 1).expect("write");
        assert!(
            store.unwritten().is_empty(),
            "the record released the pin: the segment proof now exists"
        );
        assert!(store.retire().is_empty(), "the open segment still names it");
        store.seal_all().expect("seal");
        assert_eq!(
            store.reachable(),
            BTreeSet::from([inc]),
            "the sealed segment is the proof"
        );
        assert!(store.retire().is_empty());
        store.evict(i64::MAX / 2).expect("evict");
        assert!(store.reachable().is_empty());
        let retired = store.retire();
        assert_eq!(retired.ids, vec![inc]);
        assert!(!store.dictionary().holds(inc));
        // The id was issued once: adopting it again is a typed refusal, never a fresh id.
        assert_eq!(
            store.adopt(inc, "/probe"),
            Err(StoreError::IncarnationReused {
                epoch: 4242,
                inc,
                highest: inc
            })
        );
    }

    #[test]
    fn a_queued_job_keeps_an_id_live_after_its_segment_is_evicted() {
        // The id's only remaining proof is the queue: the sealed segment that named it was
        // evicted, and the record that will name it again is still in flight. A retirement pass
        // that consulted only the segments would free it here and refuse the write's own id.
        let temp = TempDir::new("queued-retire");
        let mut config = config(&temp);
        config.tiers[0].cap_bytes = 0;
        let mut store = Store::open_with_mounts(config, Some(tmpfs_everywhere())).expect("open");
        let inc = store.adopt(0, "/probe").expect("mint");
        let now = 20_000_000_000i64;
        store
            .submit(Job::new(block(16), now, vec![inc]))
            .expect("submit");
        store.write_pending(now, 1).expect("write");
        store.seal_all().expect("seal");
        assert_eq!(store.reachable(), BTreeSet::from([inc]));
        // A second record naming the same incarnation is in flight, and the segment that
        // proved the id is about to be evicted.
        store
            .submit(Job::new(block(16), now, vec![inc]))
            .expect("submit");
        assert_eq!(
            store.queue().pending_incarnations(),
            BTreeSet::from([inc]),
            "the queued job names the id"
        );
        store
            .evict(i64::MAX / 2)
            .expect("evict every sealed segment");
        assert!(store.reachable().is_empty(), "nothing on disk names it");
        assert!(
            store.unwritten().is_empty(),
            "its first record was written already"
        );
        assert!(
            store.retire().is_empty(),
            "a queued job is a record the store still owes"
        );
        assert!(store.dictionary().holds(inc));
        // And the write it was owed succeeds, naming an id the index still holds.
        store
            .write_pending(now, 1)
            .expect("the owed record is written");
        assert!(store.retire().is_empty(), "the new segment proves it");
    }

    #[test]
    fn a_failed_write_leaves_the_id_pinned_because_nothing_named_it_durably() {
        // A write the store refused wrote nothing, so the id is still owed. Releasing the pin
        // on a refusal would make the next retirement pass free the id the caller is about to
        // resubmit — the same hole as B1, one step later.
        let temp = TempDir::new("failed-pin");
        let mut store =
            Store::open_with_mounts(config(&temp), Some(tmpfs_everywhere())).expect("open");
        let inc = store.adopt(0, "/probe").expect("mint");
        // A record older than every tier is refused by `route`, before any append.
        store
            .submit(Job::new(block(16), 0, vec![inc]))
            .expect("submit");
        assert!(store.write_pending(i64::MAX / 2, 1).is_err());
        assert_eq!(store.stats().write_failures, 1);
        assert_eq!(
            store.unwritten(),
            &BTreeSet::from([inc]),
            "a refused write named nothing on disk"
        );
        assert!(store.retire().is_empty());
        assert!(store.dictionary().holds(inc));
    }

    #[test]
    fn a_store_routes_writes_enforces_bounds_and_retires_by_reachability() {
        let temp = TempDir::new("write");
        let mut store = config(&temp);
        // Keep every tier's retention small so one clock moves records between them.
        store.tiers[0].cap_bytes = 0;
        store.tiers[1].cap_bytes = 0;
        store.tiers[2].cap_bytes = 0;
        let mut store = Store::open_with_mounts(store, Some(tmpfs_everywhere())).expect("open");
        let inc = store.adopt(0, "/system.slice/probe.service").expect("mint");
        assert_eq!(inc, 1, "the first minted id is 1, never 0");
        // A clock far enough past the epoch that even a 20-day-old record is still positive.
        let now = 2_000_000_000_000_000_000i64;
        let fresh = Job::new(block(24), now, vec![inc]);
        store.submit(fresh).expect("submit");
        let done = store.write_pending(now, 8).expect("write");
        assert_eq!(done.len(), 1);
        assert_eq!(done[0].tier, HOT);
        assert_eq!(done[0].outcome.records, 1);
        let stats = store.stats();
        assert_eq!(stats.written, 1);
        assert_eq!(stats.records, 1);
        assert!(stats.bytes > 0);
        assert!(stats.lossless());
        assert_eq!(stats.dictionary_entries, 1);
        // An older record goes to warm, an older one still to cold.
        let warm_job = Job::new(block(24), now - 120_000_000_000, vec![inc]);
        store.submit(warm_job).expect("submit");
        let written = store.write_pending(now, 8).expect("write");
        assert_eq!(written[0].tier, WARM);
        let cold_job = Job::new(block(24), now - 600_000_000_000, vec![inc]);
        store.submit(cold_job).expect("submit");
        let written = store.write_pending(now, 8).expect("write");
        assert_eq!(written[0].tier, COLD);
        assert_eq!(store.stats().written, 3);
        assert_eq!(
            store.write_pending(now, 0),
            Err(StoreError::QueryLimit { asked: 0, max: 0 })
        );
        // A record older than every tier is refused and counted as a write failure.
        let ancient = Job::new(block(24), now - 20 * 24 * 3600 * 1_000_000_000, vec![inc]);
        store.submit(ancient).expect("submit");
        let refused = store.write_pending(now, 1);
        assert!(matches!(refused, Err(StoreError::UnknownTier { .. })));
        assert_eq!(store.stats().write_failures, 1);
        // The queue handed that job to the writer and the writer refused it: the store's
        // `written` counts appends that happened, so it stays 3 and the queue's own counter is
        // the one that moved. Reporting the hand-off as a write would certify a record that is
        // not on disk (review, MINOR `stats.written`).
        assert_eq!(store.stats().written, 3, "a refused append is not a write");
        assert_eq!(
            store.queue().full_stats().written,
            4,
            "the queue handed four"
        );
        assert!(!store.stats().lossless());
        // The sealed store answers a bounded range across its tiers.
        let sealed = store.seal_all().expect("seal");
        assert_eq!(sealed.len(), 3, "one segment per tier that held a record");
        let range = store
            .range(now - 700_000_000_000, now, 16, 1 << 20)
            .expect("range");
        assert_eq!(range.len(), 3);
        assert_eq!(range.tiers(), vec![HOT, WARM, COLD]);
        assert!(!range.is_empty());
        assert!(!range.truncated);
        let one = store
            .range(now - 700_000_000_000, now, 1, 1 << 20)
            .expect("range");
        assert_eq!(one.len(), 1);
        assert!(one.truncated);
        assert_eq!(
            store.range(5, 1, 4, 1 << 20),
            Err(StoreError::RangeInverted { from: 5, to: 1 })
        );
        // Every tier still references the id, so nothing retires yet.
        let retired = store.retire();
        assert!(retired.is_empty());
        assert!(store.retire().is_empty());
        assert_eq!(store.dictionary().len(), 1);
        // Evicting every tier retires the id that only those segments referenced.
        let evicted = store.evict(i64::MAX / 2).expect("evict");
        assert_eq!(evicted.len(), 3);
        assert!(store.reachable().is_empty());
        let retired = store.retire();
        assert_eq!(retired.ids, vec![inc]);
        assert_eq!(store.stats().retired, 1);
        assert!(store.dictionary().is_empty());
        assert!(store.notes().is_empty(), "no foreign file was ever created");
    }

    #[test]
    fn dropping_a_job_is_counted_by_the_store_and_never_silent() {
        let temp = TempDir::new("drops");
        let mut store = config(&temp);
        // A queue that holds exactly one job of this size.
        let one = Job::new(block(16), 0, Vec::new()).bytes();
        store.writer_queue_bytes = one;
        let store = Store::open_with_mounts(store, Some(tmpfs_everywhere())).expect("open");
        let mut store = store;
        store
            .submit(Job::new(block(16), 0, Vec::new()))
            .expect("first");
        let report = store
            .submit(Job::new(block(16), 0, Vec::new()))
            .expect("second");
        assert_eq!(report.dropped_jobs, 1);
        assert_eq!(report.dropped_records, 1);
        let stats = store.stats();
        assert_eq!(stats.dropped, 1);
        assert_eq!(stats.dropped_records, 1, "the store reports the same loss");
        assert!(!stats.lossless());
        assert!(
            StoreStats::default().lossless(),
            "an untouched store has lost nothing"
        );
    }

    #[test]
    fn an_event_is_recorded_in_its_own_corpus_and_its_ring_is_bounded() {
        let temp = TempDir::new("events");
        let mut store = config(&temp);
        store.events_ring_bytes = 4 * (crate::events::EVENT_OVERHEAD + 4);
        let mut store = Store::open_with_mounts(store, Some(tmpfs_everywhere())).expect("open");
        let event =
            crate::events::Event::new(EventKind::Point, "run", Some("run-1"), "u1", 10, None)
                .expect("point");
        let outcome = store.record_event(&event, &evt(8), 10).expect("record");
        assert_eq!(outcome.segment, "events-0-0.seg.open");
        assert_eq!(store.stats().records, 1);
        // A BLOCK frame is refused by the events corpus, and nothing is written.
        let mut wrong = evt(0);
        wrong[4] = BLOCK_FRAME_TYPE;
        assert_eq!(
            store.record_event(&event, &wrong, 10),
            Err(StoreError::PayloadFrameType {
                expected: crate::format::EVT_FRAME_TYPE,
                found: BLOCK_FRAME_TYPE
            })
        );
        assert_eq!(store.stats().records, 1);
        for index in 1..8 {
            let more = crate::events::Event::new(
                EventKind::Point,
                "lane",
                Some(&format!("lane-{index}")),
                "u1",
                10,
                None,
            )
            .expect("point");
            store.record_event(&more, &evt(8), 10).expect("record");
        }
        // The ring displaced the oldest events: the count is available from the events store.
        assert!(store.events().stats().displaced >= 1);
        assert_eq!(store.events().stats().stored, 8);
        // A span's end is inferred when its issuer disappears.
        let mut spans = crate::events::SpanTable::new();
        let begin = crate::events::Event::new(EventKind::Begin, "run", Some("run"), "u1", 5, None)
            .expect("begin");
        spans.begin(&begin).expect("open");
        let inferred = spans.abandon_issuer("u1", 50);
        assert_eq!(inferred.len(), 1);
        assert_eq!(inferred[0].outcome, Some(Outcome::Abandoned));
        assert!(inferred[0].inferred);
    }

    #[test]
    fn a_stored_record_survives_a_reopen_and_the_store_adopts_its_open_segment() {
        let temp = TempDir::new("reopen");
        let mut store = config(&temp);
        store.tiers[0].keep_s = 3600;
        let store_config = store.clone();
        let now = 1_000_000_000i64;
        {
            let mut store = Store::open_with_mounts(store_config.clone(), Some(tmpfs_everywhere()))
                .expect("open");
            let inc = store.adopt(0, "/probe").expect("mint");
            store
                .submit(Job::new(block(24), now, vec![inc]))
                .expect("submit");
            store.write_pending(now, 1).expect("write");
            assert!(store.tier(HOT).expect("hot").has_open());
        }
        // A second handle on the same directories adopts the open segment and its records.
        let reopened =
            Store::open_with_mounts(store_config, Some(tmpfs_everywhere())).expect("reopen");
        assert_eq!(reopened.stats().records, 1, "the record is still there");
        let tier = reopened.tier(HOT).expect("hot");
        assert!(tier.has_open());
        assert_eq!(tier.scan().segments.len(), 1);
        assert_eq!(tier.scan().segments[0].records, 1);
        assert!(reopened.stats().torn_repaired == 0, "nothing was torn");
        assert_eq!(reopened.notes(), Vec::new());
    }

    #[test]
    fn an_evicted_quarantined_file_is_counted_apart_from_evicted_segments() {
        // HOB-43 at the store's counters: a quarantined file removed by the cap is
        // `evicted_quarantined`, never `evicted_segments`, and its bytes are in `evicted_bytes`.
        let temp = TempDir::new("q-stats");
        let now = 1_000_000_000i64;
        let name = {
            let mut store =
                Store::open_with_mounts(config(&temp), Some(tmpfs_everywhere())).expect("open");
            store
                .submit(Job::new(block(24), now, Vec::new()))
                .expect("submit");
            store.write_pending(now, 1).expect("write");
            store.seal_all().expect("seal");
            let hot = store.tier(HOT).expect("hot");
            hot.scan().segments[0].name.clone()
        };
        let path = format!("tiers/{name}");
        let mut bytes = temp.read_file(&path);
        let held = u64::try_from(bytes.len()).expect("fits");
        bytes[crate::format::HEADER_LEN + 48 + 8 + 1] ^= 0xFF;
        temp.write_file(&path, &bytes);
        let mut tight = config(&temp);
        tight.tiers[0].cap_bytes = held - 1;
        let mut store = Store::open_with_mounts(tight, Some(tmpfs_everywhere())).expect("reopen");
        assert_eq!(store.stats().quarantined, 1);
        assert_eq!(
            store.stats().bytes,
            held,
            "the quarantined file is in the total"
        );
        let done = store.evict(now).expect("evict");
        assert_eq!(done[HOT].quarantined, vec![format!("{name}.corrupt")]);
        let stats = store.stats();
        assert_eq!(stats.evicted_quarantined, 1);
        assert_eq!(stats.evicted_segments, 0, "debris is not a live segment");
        assert_eq!(stats.evicted_bytes, held);
        assert_eq!(stats.bytes, 0);
        assert!(!temp.exists(&format!("{path}.corrupt")));
    }

    #[test]
    fn the_write_paths_subtract_what_their_own_eviction_removed() {
        // A strict cap drops the segment the record just landed in. The store's byte counter
        // added the record's bytes on the way in and must subtract the eviction on the way
        // out, as `Store::evict` does, or it drifts upward until the next recount.
        let temp = TempDir::new("write-bytes");
        let now = 1_000_000_000i64;
        let mut tight = config(&temp);
        tight.tiers[0].cap_bytes = 1;
        let mut store = Store::open_with_mounts(tight, Some(tmpfs_everywhere())).expect("open");
        let written = store
            .write_one(&Job::new(block(24), now, Vec::new()), now)
            .expect("write");
        assert!(
            !written.outcome.evicted.is_empty(),
            "the cap took the segment"
        );
        assert_eq!(store.stats().bytes, 0, "nothing is left on disk");
        assert_eq!(store.stats().bytes, store.tier(HOT).expect("hot").bytes());
    }

    #[test]
    fn the_event_write_path_subtracts_what_its_own_eviction_removed() {
        let temp = TempDir::new("event-bytes");
        let mut tight = config(&temp);
        tight.events.cap_bytes = 1;
        let mut store = Store::open_with_mounts(tight, Some(tmpfs_everywhere())).expect("open");
        let event =
            crate::events::Event::new(EventKind::Point, "run", Some("run-1"), "u1", 10, None)
                .expect("point");
        let outcome = store.record_event(&event, &evt(8), 10).expect("record");
        assert!(!outcome.evicted.is_empty(), "the cap took the segment");
        assert_eq!(store.stats().bytes, 0, "nothing is left on disk");
        assert_eq!(store.stats().bytes, store.events().tier().bytes());
    }

    /// The sum of every history tier's own byte total and the events store's.
    fn tiers_total(store: &Store) -> u64 {
        let tiers: u64 = [HOT, WARM, COLD]
            .iter()
            .filter_map(|name| store.tier(name))
            .map(Tier::bytes)
            .sum();
        tiers + store.events().tier().bytes()
    }

    /// Write one record to the hot tier of a store over `temp`, seal it, and return the
    /// sealed segment's name.
    fn sealed_hot_name(temp: &TempDir, now: i64) -> String {
        let mut store =
            Store::open_with_mounts(config(temp), Some(tmpfs_everywhere())).expect("open");
        store
            .submit(Job::new(block(24), now, Vec::new()))
            .expect("submit");
        store.write_pending(now, 1).expect("write");
        store.seal_all().expect("seal");
        let hot = store.tier(HOT).expect("hot");
        hot.scan().segments[0].name.clone()
    }

    #[test]
    fn the_store_total_is_the_tiers_own_total_after_every_operation() {
        // HOB-56: a new segment's header is in the tier's total but not in `bytes_written`,
        // and a seal adds a footer; arithmetic on the written bytes drifted from both.
        let temp = TempDir::new("total-ops");
        let now = 2_000_000_000_000_000_000i64;
        let mut store =
            Store::open_with_mounts(config(&temp), Some(tmpfs_everywhere())).expect("open");
        assert_eq!(store.stats().bytes, tiers_total(&store));
        let written = store
            .write_one(&Job::new(block(24), now, Vec::new()), now)
            .expect("write");
        assert_eq!(store.stats().bytes, tiers_total(&store));
        assert!(
            store.stats().bytes > written.outcome.bytes_written,
            "the new segment's header is in the total"
        );
        let event =
            crate::events::Event::new(EventKind::Point, "run", Some("run-1"), "u1", 10, None)
                .expect("point");
        store.record_event(&event, &evt(8), 10).expect("record");
        assert_eq!(store.stats().bytes, tiers_total(&store));
        store.seal_all().expect("seal");
        assert_eq!(
            store.stats().bytes,
            tiers_total(&store),
            "the footers are in the total"
        );
        let warm = Job::new(block(24), now - 120_000_000_000, Vec::new());
        store.write_one(&warm, now).expect("write warm");
        assert_eq!(store.stats().bytes, tiers_total(&store));
        store.evict(i64::MAX / 2).expect("evict");
        assert_eq!(store.stats().bytes, tiers_total(&store));
    }

    #[test]
    fn the_store_total_drops_a_quarantined_file_deleted_by_hand() {
        // HOB-56: the vanished entry leaves the tier's total but was never in
        // `Evicted::bytes`, so subtracting the evicted bytes left the store's total too high.
        let temp = TempDir::new("total-q-hand");
        let now = 1_000_000_000i64;
        let name = sealed_hot_name(&temp, now);
        let path = format!("tiers/{name}");
        let mut bytes = temp.read_file(&path);
        let held = u64::try_from(bytes.len()).expect("fits");
        bytes[crate::format::HEADER_LEN + 48 + 8 + 1] ^= 0xFF;
        temp.write_file(&path, &bytes);
        let mut tight = config(&temp);
        tight.tiers[0].cap_bytes = held - 1;
        let mut store = Store::open_with_mounts(tight, Some(tmpfs_everywhere())).expect("reopen");
        assert_eq!(store.stats().bytes, held, "the debris counts");
        assert_eq!(store.stats().bytes, tiers_total(&store));
        temp.remove(&format!("{path}.corrupt"));
        let done = store.evict(now).expect("a vanished file must not fail");
        assert!(done.is_empty(), "nothing was removed");
        assert_eq!(store.stats().bytes, 0);
        assert_eq!(store.stats().bytes, tiers_total(&store));
        assert_eq!(store.stats().evicted_bytes, 0);
        // HOB-58: the report reaches the store's notes.
        let notes = store.notes();
        assert_eq!(notes.len(), 1);
        assert_eq!(notes[0].name, format!("{name}.corrupt"));
        assert!(notes[0].reason.contains("already gone"));
    }

    #[test]
    fn the_store_total_drops_a_live_segment_deleted_by_hand() {
        let temp = TempDir::new("total-seg-hand");
        let now = 1_000_000_000i64;
        let name = sealed_hot_name(&temp, now);
        let mut tight = config(&temp);
        tight.tiers[0].cap_bytes = 1;
        let mut store = Store::open_with_mounts(tight, Some(tmpfs_everywhere())).expect("reopen");
        assert!(store.stats().bytes > 0);
        assert_eq!(store.stats().bytes, tiers_total(&store));
        temp.remove(&format!("tiers/{name}"));
        let done = store.evict(now).expect("a vanished head must not fail");
        assert!(done.is_empty(), "nothing was removed");
        assert_eq!(store.stats().bytes, 0);
        assert_eq!(store.stats().bytes, tiers_total(&store));
        assert_eq!(store.stats().evicted_segments, 0);
        // HOB-58: a live segment's name is a tier's own, yet its report is not filtered out.
        let notes = store.notes();
        assert_eq!(notes.len(), 1);
        assert_eq!(notes[0].name, name);
        assert!(notes[0].reason.contains("already gone"));
    }

    #[test]
    fn eviction_reports_from_the_write_path_reach_the_store_and_are_bounded() {
        // HOB-58: `write_one` runs an eviction whose report used to reach only the tier's
        // `scan.notes`; a rescan resets those, the store's own ring does not.
        let temp = TempDir::new("notes-write");
        let now = 1_000_000_000i64;
        let name = sealed_hot_name(&temp, now);
        let mut tight = config(&temp);
        tight.tiers[0].cap_bytes = 1;
        let mut store = Store::open_with_mounts(tight, Some(tmpfs_everywhere())).expect("reopen");
        temp.remove(&format!("tiers/{name}"));
        let written = store
            .write_one(&Job::new(block(24), now, Vec::new()), now)
            .expect("the append must not fail on a file that is already gone");
        assert_eq!(written.outcome.evicted.notes.len(), 1);
        assert_eq!(written.outcome.evicted.notes[0].name, name);
        assert_eq!(store.notes().len(), 1);
        assert_eq!(store.notes()[0].name, name);
        assert_eq!(store.stats().bytes, tiers_total(&store));
        let mut ring = std::collections::VecDeque::new();
        let many = crate::tier::Evicted {
            notes: (0..super::MAX_EVICTION_NOTES + 5)
                .map(|index| crate::tier::TierNote {
                    name: format!("n{index}"),
                    reason: "gone".to_string(),
                })
                .collect(),
            ..crate::tier::Evicted::default()
        };
        super::keep_notes(&mut ring, &many);
        assert_eq!(ring.len(), super::MAX_EVICTION_NOTES);
        assert_eq!(ring.front().map(|note| note.name.as_str()), Some("n5"));
    }
}
