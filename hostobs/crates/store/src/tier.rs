//! One tier directory: segments, their bounds, and ring eviction.
//!
//! Plan v4 §A.4: every tier is bounded by `keep_s` **and** `cap_bytes`, "whichever first", and
//! loses its **oldest whole segment first**. A tier here is exactly that: a directory, a
//! configured step (whose duration names each segment's start), the two bounds and the ring.
//!
//! # Contracts
//!
//! * **Eviction is by whole segment.** A segment is the unit of history: cutting *inside* one
//!   would leave a file whose footer no longer describes it, so eviction unlinks a name and
//!   nothing else.
//! * **Oldest first.** The ring orders segments by their own start second, then by sequence, so
//!   a clock step cannot make a newer segment look older (a segment's name carries its start,
//!   not an insertion order).
//! * **Both bounds are checked on every append and on open.** `keep_s` removes a segment whose
//!   whole span is older than the window; `cap_bytes` removes the oldest until the total is at
//!   or under the cap. `whichever first` is implemented literally: every append calls both and
//!   both can evict.
//! * **Only the store's own files are removed.** A candidate is a name [`SegmentFileName::parse`]
//!   accepts *and* a file whose first [`HEADER_LEN`] bytes are this store's header for this
//!   tier (`SegmentHeader::check_bytes` plus the tier's kind). Anything else in the directory —
//!   a foreign file, a link, a directory, a `.tmp` orphan of an interrupted seal — is **left
//!   untouched** and reported ([`TierScan::notes`]).
//! * **The cap is strict.** If the only remaining segment is the open one and it alone pushes
//!   the tier past the cap, it is dropped too — but the open handle is dropped first, so
//!   nothing is ever appended behind a name that no longer exists.
//! * **A tear is repaired before a segment is read or appended to**, and a corrupt one is
//!   quarantined (`crate::segment`), both reported ([`TierScan`]).
//!
//! # What a tier does *not* do
//!
//! It owns no thread and no timer: [`Tier::append`] appends and enforces the bounds, and the
//! caller decides when to open or seal a segment ([`Tier::open_segment`],
//! [`Tier::seal_open_segment`]). That keeps every bound checkable in a test without a clock
//! (the estate's rule: no test may depend on host timing).

use std::collections::BTreeSet;

use crate::dictionary::Retired;
use crate::error::StoreError;
use crate::format::{check_tier_name, SegmentFileName, SegmentHeader, TierKind, HEADER_LEN};
use crate::fsio::{EntryKind, StoreDir};
use crate::queue::{FlushPolicy, Job};
use crate::segment::{OpenSegment, SegmentScan, TailState};

/// The largest number of segments one tier will hold; a directory beyond this is a
/// configuration that cannot be enforced (the cap is meant to bind well below it).
pub const MAX_SEGMENTS: usize = 4096;

/// The largest byte cap the configuration accepts. The plan's proposal is 1 GiB per tier; this
/// is the bound past which the store refuses rather than pretending to enforce.
pub const MAX_CAP_BYTES: u64 = 1 << 34;

/// The configuration of one tier (plan v4 §A.4's per-tier row).
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TierConfig {
    /// The tier's name, used as the segment name prefix.
    pub name: String,
    /// Which corpus it holds. The events store is a tier with a different kind and its own cap.
    pub kind: TierKind,
    /// The duration one segment covers, in seconds; also the alignment of its name.
    pub segment_s: u64,
    /// How long history is kept, in seconds.
    pub keep_s: u64,
    /// The byte cap (`0` means "no byte cap", which only a tier on a bounded medium may use).
    pub cap_bytes: u64,
    /// The largest payload a record may carry.
    pub max_payload: u32,
    /// The largest number of incarnations one record may name.
    pub max_incarnations: u32,
    /// Require the directory to be on tmpfs (plan v4 §A.4: hot and warm).
    pub require_tmpfs: bool,
}

impl TierConfig {
    /// A history tier named `name` with the given bounds.
    ///
    /// # Errors
    ///
    /// [`StoreError::BadName`] for a name a file cannot carry, [`StoreError::Config`] for a
    /// zero step, a zero retention or a cap past [`MAX_CAP_BYTES`].
    pub fn history(
        name: &str,
        segment_s: u64,
        keep_s: u64,
        cap_bytes: u64,
        require_tmpfs: bool,
    ) -> Result<TierConfig, StoreError> {
        TierConfig::new(
            name,
            TierKind::History,
            segment_s,
            keep_s,
            cap_bytes,
            require_tmpfs,
        )
    }

    /// A tier of any kind.
    ///
    /// # Errors
    ///
    /// As [`TierConfig::history`].
    pub fn new(
        name: &str,
        kind: TierKind,
        segment_s: u64,
        keep_s: u64,
        cap_bytes: u64,
        require_tmpfs: bool,
    ) -> Result<TierConfig, StoreError> {
        check_tier_name(name)?;
        if segment_s == 0 {
            return Err(StoreError::Config {
                key: "segment_s",
                reason: "must be at least one second",
            });
        }
        if keep_s == 0 {
            return Err(StoreError::Config {
                key: "keep_s",
                reason: "must be at least one second",
            });
        }
        if cap_bytes > MAX_CAP_BYTES {
            return Err(StoreError::Config {
                key: "cap_bytes",
                reason: "is past the store's own bound",
            });
        }
        Ok(TierConfig {
            name: name.to_string(),
            kind,
            segment_s,
            keep_s,
            cap_bytes,
            max_payload: 1 << 20,
            max_incarnations: 1024,
            require_tmpfs,
        })
    }

    /// Set the payload bound (default 1 MiB; plan v4 §B.1's frame bound is 16 MiB).
    ///
    /// # Errors
    ///
    /// [`StoreError::Config`] for zero.
    pub fn with_max_payload(mut self, max_payload: u32) -> Result<TierConfig, StoreError> {
        if max_payload == 0 {
            return Err(StoreError::Config {
                key: "max_payload",
                reason: "must be at least one byte",
            });
        }
        self.max_payload = max_payload;
        Ok(self)
    }

    /// Set the incarnation bound of one record.
    ///
    /// # Errors
    ///
    /// [`StoreError::Config`] for zero.
    pub fn with_max_incarnations(
        mut self,
        max_incarnations: u32,
    ) -> Result<TierConfig, StoreError> {
        if max_incarnations == 0 {
            return Err(StoreError::Config {
                key: "max_incarnations",
                reason: "must name at least one incarnation",
            });
        }
        self.max_incarnations = max_incarnations;
        Ok(self)
    }

    /// The flush/seal policy this tier implies: flush on every record and seal when either
    /// bound of a segment is reached (the segment's byte budget is derived from the payload
    /// bound, and its time budget is its own `segment_s`).
    ///
    /// # Errors
    ///
    /// [`StoreError::Config`] when the derived policy would be meaningless (never, for a
    /// configuration this type accepted).
    pub fn flush_policy(&self) -> Result<FlushPolicy, StoreError> {
        FlushPolicy::new(1, self.segment_bytes(), self.segment_s)
    }

    /// The byte budget of one segment (`max_payload * 64`, so a segment holds many records and
    /// still has a bound of its own).
    #[must_use]
    pub fn segment_bytes(&self) -> u64 {
        u64::from(self.max_payload).saturating_mul(64).max(1 << 16)
    }

    /// The largest a segment file can legitimately be: its byte budget plus the one record
    /// that crossed it.
    ///
    /// A writer seals *after* appending the record that reaches the budget, so a segment may
    /// exceed `segment_bytes` by exactly one record. The seal's copy bound is therefore this
    /// value, and a file larger than it is one the store did not write (or one that grew after
    /// it was closed) and is refused rather than duplicated into a trusted name.
    #[must_use]
    pub fn segment_file_bound(&self) -> u64 {
        let record = u64::from(self.max_payload)
            .saturating_add(u64::from(self.max_incarnations).saturating_mul(8))
            .saturating_add(crate::format::RECORD_HEADER_LEN as u64)
            .saturating_add(crate::format::MAX_RECORD_PAD as u64);
        self.segment_bytes()
            .saturating_add(record)
            .saturating_add(1)
    }
}

/// The note a scan leaves about a name it refused to touch.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TierNote {
    /// The name found.
    pub name: String,
    /// What it is, and why it was left alone.
    pub reason: String,
}

/// One segment in the tier's ring, as the tier currently knows it.
///
/// The record payloads are *not* kept here: [`Tier::range`] reads a segment from disk inside
/// its own bound, so a tier's memory is proportional to the number of segments, never to the
/// bytes they hold (plan v4 §A.4's "bounded everything").
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RingSegment {
    /// The name it carries.
    pub name: String,
    /// Its parsed name.
    pub parsed: SegmentFileName,
    /// True while it is an open segment.
    pub open: bool,
    /// Its length in bytes.
    pub file_len: u64,
    /// How many records it holds.
    pub records: u64,
    /// The first record's real time.
    pub first_real_ns: Option<i64>,
    /// The last record's real time.
    pub last_real_ns: Option<i64>,
    /// The incarnation ids it names (dictionary reachability).
    pub incarnations: BTreeSet<u64>,
}

impl RingSegment {
    /// True when the segment is sealed.
    #[must_use]
    pub const fn is_sealed(&self) -> bool {
        !self.open
    }
}

/// What scanning a tier directory found.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct TierScan {
    /// Every segment this store owns, ordered oldest first.
    pub segments: Vec<RingSegment>,
    /// Names the tier left untouched, with why.
    pub notes: Vec<TierNote>,
    /// Segments whose tail was torn and were truncated to their last good record.
    pub torn_repaired: Vec<String>,
    /// Segments quarantined because their tail was corrupt.
    pub quarantined: Vec<String>,
    /// True when the open segment's tail was damaged in a way the scan could not repair.
    pub open_damaged: bool,
    /// The tier's total bytes in its own segments.
    pub bytes: u64,
}

impl TierScan {
    /// How many segments the scan found.
    #[must_use]
    pub fn len(&self) -> usize {
        self.segments.len()
    }

    /// True when the directory holds no segment of this store.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.segments.is_empty()
    }

    /// The ids any scanned segment still names (dictionary reachability).
    #[must_use]
    pub fn reachable(&self) -> BTreeSet<u64> {
        let mut reach = BTreeSet::new();
        for segment in &self.segments {
            for inc in &segment.incarnations {
                reach.insert(*inc);
            }
        }
        reach
    }
}

/// One tier directory with its configuration.
#[derive(Debug)]
pub struct Tier {
    config: TierConfig,
    dir: StoreDir,
    scan: TierScan,
    open: Option<OpenSegment>,
    bytes: u64,
    max_file_bytes: usize,
    /// The next sequence number this tier may issue. Monotone: eviction and a restart both
    /// leave it alone, so a segment name is never reused (a name that came back would make a
    /// quarantined `.corrupt` file and a live segment share an identity).
    seq_water: u64,
}

impl Tier {
    /// Create a tier: open (and create) its directory, scan it, repair torn tails, and take
    /// ownership of any open segment the scan found.
    ///
    /// The scan is what makes a restart safe: every torn tail is truncated to its last good
    /// record *before* anything is appended behind it, and every corrupt segment is renamed out
    /// of the way *before* a reader could see it.
    ///
    /// # Errors
    ///
    /// [`StoreError::Symlink`] for a linked root, [`StoreError::EntryLimit`], and every scan
    /// refusal. A *repaired* tail is a note, not an error.
    pub fn open(
        config: TierConfig,
        dir: StoreDir,
        max_file_bytes: usize,
    ) -> Result<Tier, StoreError> {
        let mut tier = Tier {
            config,
            dir,
            scan: TierScan::default(),
            open: None,
            bytes: 0,
            max_file_bytes: max_file_bytes.max(HEADER_LEN),
            seq_water: 0,
        };
        tier.rescan()?;
        if tier.scan.open_damaged {
            return Err(StoreError::Config {
                key: "open-segment",
                reason: "the open segment has a tail the store could not repair",
            });
        }
        tier.reopen_open_segment()?;
        Ok(tier)
    }

    /// The configuration.
    #[must_use]
    pub const fn config(&self) -> &TierConfig {
        &self.config
    }

    /// The directory.
    #[must_use]
    pub const fn dir(&self) -> &StoreDir {
        &self.dir
    }

    /// The last scan.
    #[must_use]
    pub const fn scan(&self) -> &TierScan {
        &self.scan
    }

    /// The tier's own segment bytes.
    #[must_use]
    pub const fn bytes(&self) -> u64 {
        self.bytes
    }

    /// The read bound a scan of one segment uses.
    #[must_use]
    pub const fn max_file_bytes(&self) -> usize {
        self.max_file_bytes
    }

    /// The open segment handle, when one is open.
    #[must_use]
    pub const fn open_handle(&self) -> Option<&OpenSegment> {
        self.open.as_ref()
    }

    /// True when an open segment exists.
    #[must_use]
    pub const fn has_open(&self) -> bool {
        self.open.is_some()
    }

    /// Scan the directory again: validate names and headers, repair torn tails, quarantine
    /// corrupt segments, and rebuild the ring and the byte total.
    ///
    /// # Errors
    ///
    /// [`StoreError::EntryLimit`], [`StoreError::Symlink`] (the root), [`StoreError::Io`] and
    /// every header/segment refusal the store cannot repair. A torn or corrupt *segment* is
    /// repaired or quarantined and reported, never an error.
    pub fn rescan(&mut self) -> Result<(), StoreError> {
        let entries = self.dir.list(MAX_SEGMENTS)?;
        let mut segments: Vec<RingSegment> = Vec::new();
        let mut notes: Vec<TierNote> = Vec::new();
        let mut torn_repaired: Vec<String> = Vec::new();
        let mut quarantined: Vec<String> = Vec::new();
        let mut open_damaged = false;
        let mut bytes: u64 = 0;
        for entry in entries {
            let parsed = match SegmentFileName::parse(&entry.name) {
                Ok(parsed) => parsed,
                Err(_) => {
                    notes.push(TierNote {
                        name: entry.name.clone(),
                        reason: "not a segment name this store writes".to_string(),
                    });
                    continue;
                }
            };
            if parsed.tier != self.config.name {
                notes.push(TierNote {
                    name: entry.name.clone(),
                    reason: "names another tier".to_string(),
                });
                continue;
            };
            match entry.kind {
                EntryKind::File => {}
                other => {
                    notes.push(TierNote {
                        name: entry.name.clone(),
                        reason: format!("is {other}, not a regular file"),
                    });
                    continue;
                }
            }
            // Only a file whose own header says it is this tier's corpus is ours to touch; a
            // name that looks right and holds something else is reported and left alone.
            let header = match self.header_of(&entry.name) {
                Ok(header) => header,
                Err(reason) => {
                    notes.push(TierNote {
                        name: entry.name.clone(),
                        reason: format!("holds no header this tier can read: {}", reason.kind()),
                    });
                    continue;
                }
            };
            if header.kind != self.config.kind {
                notes.push(TierNote {
                    name: entry.name.clone(),
                    reason: "header names another tier kind".to_string(),
                });
                continue;
            }
            let scan = crate::segment::scan(
                &self.dir,
                &entry.name,
                self.max_file_bytes,
                self.config.max_incarnations,
            )?;
            match &scan.tail {
                TailState::Complete => {}
                TailState::Incomplete { .. } => {
                    scan.truncate_to_good_end(&self.dir)?;
                    torn_repaired.push(entry.name.clone());
                }
                TailState::Corrupt { .. } => {
                    let quarantine = crate::segment::quarantine_name(&entry.name)?;
                    scan.quarantine(&self.dir, &quarantine)?;
                    quarantined.push(entry.name.clone());
                    continue;
                }
            }
            // Re-scan the repaired file, so the ring's facts describe what is actually there.
            let scan = if scan.tail.is_damaged() {
                crate::segment::scan(
                    &self.dir,
                    &entry.name,
                    self.max_file_bytes,
                    self.config.max_incarnations,
                )?
            } else {
                scan
            };
            if parsed.open && scan.tail.is_damaged() {
                open_damaged = true;
            }
            bytes = bytes.saturating_add(scan.file_len);
            segments.push(ring_segment(&entry.name, parsed, scan));
        }
        // The ring's order: a segment's own start, then its sequence. A clock step can make a
        // name's start repeat, so the sequence is the tie-break rather than the name's bytes.
        segments.sort_by(|left, right| {
            (left.parsed.start_s, left.parsed.seq).cmp(&(right.parsed.start_s, right.parsed.seq))
        });
        self.scan = TierScan {
            segments,
            notes,
            torn_repaired,
            quarantined,
            open_damaged,
            bytes,
        };
        self.bytes = self.scan.bytes;
        // The water mark only ever rises: a restart continues past every sequence the
        // directory can show, including the ones this handle already issued.
        let highest = self
            .scan
            .segments
            .iter()
            .map(|segment| segment.parsed.seq)
            .max();
        self.seq_water = match highest {
            Some(seq) => self.seq_water.max(seq.saturating_add(1)),
            None => self.seq_water,
        };
        Ok(())
    }

    /// The header one name holds, inside the read bound.
    fn header_of(&self, name: &str) -> Result<SegmentHeader, StoreError> {
        SegmentHeader::check_bytes(&self.dir.read_at(
            name,
            0,
            HEADER_LEN,
            u64::try_from(self.max_file_bytes).unwrap_or(u64::MAX),
        )?)
    }

    /// The oldest segment this tier owns.
    #[must_use]
    pub fn oldest(&self) -> Option<&RingSegment> {
        self.scan.segments.first()
    }

    /// The newest segment this tier owns.
    #[must_use]
    pub fn newest(&self) -> Option<&RingSegment> {
        self.scan.segments.last()
    }

    /// The segment name for `start_s` and `seq`, open or sealed.
    #[must_use]
    pub fn name_for(&self, start_s: u64, seq: u64, open: bool) -> String {
        if open {
            SegmentFileName::open_name(&self.config.name, start_s, seq)
        } else {
            SegmentFileName::sealed_name(&self.config.name, start_s, seq)
        }
    }

    /// The next tier-local sequence number.
    ///
    /// It is a high-water mark, not a count of what is on disk: a segment that was evicted or
    /// quarantined still consumed its sequence, and reusing it would give a new segment the
    /// name of a file an operator may still hold.
    #[must_use]
    pub const fn next_seq(&self) -> u64 {
        self.seq_water
    }

    /// Open a new segment aligned to `start_s`, failing when one is open already.
    ///
    /// # Errors
    ///
    /// [`StoreError::Config`] when a segment is already open (one open segment per tier is the
    /// policy that makes the ring's "evict whole segments" possible), and every IO refusal.
    pub fn open_segment(
        &mut self,
        start_s: u64,
        epoch: u64,
        dict_generation: u32,
    ) -> Result<(), StoreError> {
        if self.open.is_some() {
            return Err(StoreError::Config {
                key: "open-segment",
                reason: "a tier holds at most one open segment",
            });
        }
        let seq = self.seq_water;
        self.seq_water = seq.checked_add(1).ok_or(StoreError::LengthOverflow)?;
        let name = self.name_for(start_s, seq, true);
        let header = SegmentHeader {
            seq,
            epoch,
            dict_generation,
            kind: self.config.kind,
            payload: self.config.kind.payload_kind(),
            start_real_ns: i64::try_from(start_s.saturating_mul(1_000_000_000)).unwrap_or(i64::MAX),
            max_payload: self.config.max_payload,
            max_incarnations: self.config.max_incarnations,
        };
        let open = OpenSegment::create(&self.dir, &name, header)?;
        self.bytes = self.bytes.saturating_add(open.len());
        self.scan.segments.push(RingSegment {
            name: name.clone(),
            parsed: SegmentFileName {
                tier: self.config.name.clone(),
                start_s,
                seq,
                open: true,
            },
            open: true,
            file_len: open.len(),
            records: 0,
            first_real_ns: None,
            last_real_ns: None,
            incarnations: BTreeSet::new(),
        });
        self.scan.bytes = self.bytes;
        self.open = Some(open);
        Ok(())
    }

    /// Take ownership of the open segment the last scan found (or do nothing when there is
    /// none), continuing its sequence where it stopped.
    ///
    /// # Errors
    ///
    /// Every refusal of [`OpenSegment::open`].
    pub fn reopen_open_segment(&mut self) -> Result<bool, StoreError> {
        if self.open.is_some() {
            return Ok(true);
        }
        let candidate = match self.scan.segments.iter().find(|segment| segment.open) {
            Some(segment) => segment.name.clone(),
            None => return Ok(false),
        };
        let open = OpenSegment::open(
            &self.dir,
            &candidate,
            self.config.kind,
            self.max_file_bytes,
            self.config.max_incarnations,
        )?;
        // The open segment's ring entry is refreshed from the handle that owns it.
        if let Some(entry) = self
            .scan
            .segments
            .iter_mut()
            .find(|segment| segment.name == candidate)
        {
            entry.file_len = open.len();
            entry.records = open.record_count();
            entry.last_real_ns = open.last_real_ns();
            entry.incarnations = open.incarnations().clone();
        }
        self.open = Some(open);
        Ok(true)
    }

    /// The start second the next record's real time belongs to.
    ///
    /// # Errors
    ///
    /// [`StoreError::NegativeTime`] for a time before the Unix epoch.
    pub fn start_for(&self, real_ns: i64) -> Result<u64, StoreError> {
        FlushPolicy::segment_start_s(real_ns, self.config.segment_s)
    }

    /// Append one job, opening a new segment when the job's time leaves the open one's own
    /// start bucket or the open segment has reached its byte budget, and enforce both bounds
    /// afterwards.
    ///
    /// # Errors
    ///
    /// [`StoreError::SegmentIncarnations`] for more ids than this tier admits, and every
    /// append/seal/eviction refusal (a payload past the tier's frame bound is refused by the
    /// segment's own block-frame check, before anything is written).
    pub fn append(
        &mut self,
        job: &Job,
        epoch: u64,
        dict_generation: u32,
    ) -> Result<AppendOutcome, StoreError> {
        let start_s = self.start_for(job.real_ns)?;
        let needs_new = match &self.open {
            None => true,
            Some(open) => {
                let open_bucket = self.start_for(open.header().start_real_ns.max(0))?;
                open_bucket != start_s
                    || open.record_count() >= crate::segment::SCAN_RECORD_BOUND as u64
                    || self.config.segment_bytes() <= open.len()
            }
        };
        if needs_new {
            if self.open.is_some() {
                self.seal_open_segment()?;
            }
            self.open_segment(start_s, epoch, dict_generation)?;
        }
        if job.incarnations.len()
            > usize::try_from(self.config.max_incarnations).unwrap_or(usize::MAX)
        {
            return Err(StoreError::SegmentIncarnations {
                seq: self.next_seq(),
                count: job.incarnations.len(),
                max: usize::try_from(self.config.max_incarnations).unwrap_or(usize::MAX),
            });
        }
        let before = self.open.as_ref().map_or(0, OpenSegment::len);
        let open = self.open.as_mut().ok_or(StoreError::Config {
            key: "open-segment",
            reason: "no open segment",
        })?;
        let records = open.append(job.real_ns, &job.incarnations, &job.payload)?;
        let after = open.len();
        let added = after.saturating_sub(before);
        self.bytes = self.bytes.saturating_add(added);
        // Keep the ring's entry for the open segment current: eviction and reachability read
        // those facts, and a stale entry would understate the tier's own bytes.
        if let Some(entry) = self.scan.segments.iter_mut().find(|segment| segment.open) {
            entry.file_len = after;
            entry.records = records;
            entry.last_real_ns = Some(job.real_ns);
            if entry.first_real_ns.is_none() {
                entry.first_real_ns = Some(job.real_ns);
            }
            for inc in &job.incarnations {
                entry.incarnations.insert(*inc);
            }
        }
        self.scan.bytes = self.bytes;
        // The record's home is named *before* eviction: a strict cap can remove the very
        // segment this record landed in, and "written to the one I just dropped" is exactly
        // what the report should say rather than an empty name.
        let landed = self
            .open
            .as_ref()
            .map_or_else(String::new, |open| open.name().to_string());
        let evicted = self.evict(job.real_ns)?;
        Ok(AppendOutcome {
            segment: landed,
            records,
            bytes_written: added,
            evicted,
        })
    }

    /// Enforce both bounds for a record at `now_ns` (`keep_s` and `cap_bytes`, whichever first).
    ///
    /// # Errors
    ///
    /// [`StoreError::Io`] when a removal fails (the segment stays in the ring, so the total is
    /// still honest and the next pass retries).
    pub fn evict(&mut self, now_ns: i64) -> Result<Evicted, StoreError> {
        let mut evicted = Evicted::default();
        // `keep_s`: a segment leaves when its whole span is older than the window. Its span is
        // its start plus its own duration, so a segment whose end is exactly the window's edge
        // is still inside it (the comparison is strict).
        while let Some(segment) = self.scan.segments.first() {
            let (name, start_s) = (segment.name.clone(), segment.parsed.start_s);
            let start_ns = i64::try_from(start_s.saturating_mul(1_000_000_000)).unwrap_or(i64::MAX);
            let span_end_ns = start_ns.saturating_add(
                i64::try_from(self.config.segment_s.saturating_mul(1_000_000_000))
                    .unwrap_or(i64::MAX),
            );
            let keep_ns =
                i64::try_from(self.config.keep_s.saturating_mul(1_000_000_000)).unwrap_or(i64::MAX);
            let expired = span_end_ns.saturating_add(keep_ns) <= now_ns;
            if !expired {
                break;
            }
            self.remove_segment(&name, &mut evicted)?;
        }
        // `cap_bytes`: the oldest whole segment leaves until the total is at or under the cap.
        if self.config.cap_bytes > 0 {
            while self.bytes > self.config.cap_bytes {
                let Some(name) = self
                    .scan
                    .segments
                    .first()
                    .map(|segment| segment.name.clone())
                else {
                    break;
                };
                self.remove_segment(&name, &mut evicted)?;
            }
        }
        Ok(evicted)
    }

    /// Remove one segment this tier owns, updating the ring and the byte total.
    ///
    /// Ownership is re-validated immediately before the unlink: a name the scan accepted is a
    /// name this store may delete, and the scan is the only list the tier keeps.
    fn remove_segment(&mut self, name: &str, evicted: &mut Evicted) -> Result<(), StoreError> {
        let Some(position) = self
            .scan
            .segments
            .iter()
            .position(|segment| segment.name == name)
        else {
            return Ok(());
        };
        let len = self.scan.segments[position].file_len;
        let is_open = self.open.as_ref().is_some_and(|open| open.name() == name);
        if is_open {
            // The handle closes before the name disappears, so nothing can append behind a
            // file that is no longer there.
            self.open = None;
        }
        self.dir.remove(name)?;
        self.scan.segments.remove(position);
        self.bytes = self.bytes.saturating_sub(len);
        self.scan.bytes = self.bytes;
        evicted.segments.push(name.to_string());
        evicted.bytes = evicted.bytes.saturating_add(len);
        Ok(())
    }

    /// Seal the open segment: footer, fsync, rename to the sealed name, and the open name
    /// disappears atomically.
    ///
    /// # Errors
    ///
    /// [`StoreError::Config`] when nothing is open, and every seal refusal.
    pub fn seal_open_segment(&mut self) -> Result<Option<String>, StoreError> {
        let Some(open) = self.open.take() else {
            return Ok(None);
        };
        let start_s = self.start_for(open.header().start_real_ns.max(0))?;
        let seq = open.header().seq;
        let open_name = open.name().to_string();
        let sealed = self.name_for(start_s, seq, false);
        let temp = format!("{sealed}.tmp");
        let report = open.seal(&self.dir, &sealed, &temp, self.config.segment_file_bound())?;
        let file_len = self.dir.size_of(&sealed)?.unwrap_or(0);
        // The open name is gone and the sealed one took its place: recompute both entries from
        // what is on disk rather than from an assumption about the copy's size.
        let old_len = self
            .scan
            .segments
            .iter()
            .find(|segment| segment.name == open_name)
            .map_or(0, |segment| segment.file_len);
        self.bytes = self.bytes.saturating_sub(old_len).saturating_add(file_len);
        if let Some(entry) = self
            .scan
            .segments
            .iter_mut()
            .find(|segment| segment.name == open_name)
        {
            entry.name = sealed.clone();
            entry.parsed.open = false;
            entry.open = false;
            entry.file_len = file_len;
            entry.records = report.records;
            entry.first_real_ns = report.first_real_ns;
            entry.last_real_ns = report.last_real_ns;
        }
        // A sealed segment's start and sequence are unchanged, and the ring's order is by
        // (start, seq), so the entry stays where it is.
        self.scan.bytes = self.bytes;
        Ok(Some(sealed))
    }

    /// The ids every segment in this tier still names.
    #[must_use]
    pub fn reachable(&self) -> BTreeSet<u64> {
        self.scan.reachable()
    }

    /// A range query over every segment in this tier, oldest first, bounded by `max_records`
    /// and `max_bytes`.
    ///
    /// Each segment is read from disk inside the tier's own per-file bound, so the answer's
    /// memory is bounded by the caller's limits and never by the tier's size.
    ///
    /// # Errors
    ///
    /// [`StoreError::RangeInverted`], [`StoreError::QueryLimit`] for a zero record bound, and
    /// every read/scan refusal (a segment that cannot be read is an error a caller sees, never
    /// a panic).
    pub fn range(
        &self,
        from: i64,
        to: i64,
        max_records: usize,
        max_bytes: usize,
    ) -> Result<TierRange, StoreError> {
        if to < from {
            return Err(StoreError::RangeInverted { from, to });
        }
        if max_records == 0 {
            return Err(StoreError::QueryLimit { asked: 0, max: 0 });
        }
        let mut answer = TierRange::default();
        let mut bytes = 0usize;
        for segment in &self.scan.segments {
            // A segment whose whole span is outside the window costs one comparison.
            if segment.first_real_ns.is_some_and(|first| first > to) {
                break;
            }
            if segment.last_real_ns.is_some_and(|last| last < from) {
                continue;
            }
            let scan = crate::segment::scan(
                &self.dir,
                &segment.name,
                self.max_file_bytes,
                self.config.max_incarnations,
            )?;
            let part = scan.range(
                from,
                to,
                max_records.saturating_sub(answer.records.len()),
                max_bytes.saturating_sub(bytes),
            )?;
            for record in &part.records {
                bytes = bytes.saturating_add(record.payload.len().saturating_add(64));
            }
            answer.truncated |= part.truncated;
            answer.records.extend(part.records);
            if answer.records.len() >= max_records {
                answer.truncated = true;
                break;
            }
        }
        Ok(answer)
    }

    /// Retire dictionary entries this tier no longer references.
    ///
    /// A tier can only retire what *it* holds, so the store calls this per tier with the union
    /// of the other tiers' reachable sets as the second argument.
    #[must_use]
    pub fn retire(
        &self,
        dictionary: &mut crate::dictionary::ActiveDictionary,
        also_reachable: &BTreeSet<u64>,
    ) -> Retired {
        let mut reach = self.reachable();
        for inc in also_reachable {
            reach.insert(*inc);
        }
        dictionary.retire_with_set(&reach)
    }
}

/// Turn a scan into the ring's own facts.
fn ring_segment(name: &str, parsed: SegmentFileName, scan: SegmentScan) -> RingSegment {
    RingSegment {
        name: name.to_string(),
        open: parsed.open,
        parsed,
        file_len: scan.file_len,
        records: u64::try_from(scan.records.len()).unwrap_or(u64::MAX),
        first_real_ns: scan.first_real_ns(),
        last_real_ns: scan.last_real_ns(),
        incarnations: scan.incarnations,
    }
}

/// What an append did.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AppendOutcome {
    /// The segment the record landed in.
    pub segment: String,
    /// The record's sequence number within that segment.
    pub records: u64,
    /// Bytes this record added.
    pub bytes_written: u64,
    /// What the append evicted.
    pub evicted: Evicted,
}

/// What an eviction pass removed.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct Evicted {
    /// The segment names removed, oldest first.
    pub segments: Vec<String>,
    /// The bytes they held.
    pub bytes: u64,
}

impl Evicted {
    /// How many segments left.
    #[must_use]
    pub fn len(&self) -> usize {
        self.segments.len()
    }

    /// True when nothing was evicted.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.segments.is_empty()
    }
}

/// A range answer across one tier's segments.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct TierRange {
    /// The records in the window, oldest first.
    pub records: Vec<crate::segment::ScannedRecord>,
    /// True when a bound stopped the answer.
    pub truncated: bool,
}

impl TierRange {
    /// The payloads in the window, in order.
    #[must_use]
    pub fn payloads(&self) -> Vec<&[u8]> {
        self.records
            .iter()
            .map(|record| record.payload.as_slice())
            .collect()
    }

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
}

#[cfg(test)]
mod tests {
    use super::{Tier, TierConfig, MAX_CAP_BYTES};
    use crate::error::StoreError;
    use crate::format::{TierKind, HEADER_LEN};
    use crate::fsio::testdir::TempDir;
    use crate::fsio::StoreDir;
    use crate::queue::Job;
    use std::collections::BTreeSet;

    /// A minimal `proto` BLOCK frame: a 12-byte header plus `data` zero bytes.
    fn block(len: usize) -> Vec<u8> {
        let mut frame = vec![0u8; len];
        frame[0..4].copy_from_slice(&u32::try_from(len).expect("fits").to_le_bytes());
        frame[4] = crate::format::BLOCK_FRAME_TYPE;
        frame[5] = crate::format::FRAME_FLAG_SECTIONED;
        if len >= 12 {
            frame[8..12].copy_from_slice(&7u32.to_le_bytes());
        }
        frame
    }

    fn job(real_ns: i64, len: usize) -> Job {
        Job::new(block(len.max(16)), real_ns, vec![1])
    }

    /// A sealed segment's bytes: its header, one record and its footer.
    ///
    /// One record is a 48-byte header plus its body (8 bytes for the single id and the
    /// payload), padded to eight bytes.
    fn sealed_len(payload_len: usize) -> u64 {
        record_len(payload_len) + HEADER_LEN as u64 + crate::format::FOOTER_LEN as u64
    }

    /// One record's bytes: a 48-byte header plus its body (8 bytes for the single id and the
    /// payload), padded to eight bytes.
    fn record_len(payload_len: usize) -> u64 {
        let payload_len = payload_len.max(16);
        let body = 8 + payload_len;
        let pad = (8 - body % 8) % 8;
        u64::try_from(48 + body + pad).unwrap_or(0)
    }

    /// Reopen the *same* directory with a smaller cap, which is how a bound change and a
    /// restart are modelled: the scan rebuilds the ring from what is on disk.
    fn tier_with_cap(temp: &TempDir, keep_s: u64, cap_bytes: u64) -> Tier {
        Tier::open(
            TierConfig::history("hot", 60, keep_s, cap_bytes, false).expect("config"),
            StoreDir::open(temp.path(), false).expect("dir"),
            1 << 22,
        )
        .expect("tier")
    }

    fn tier(temp: &TempDir, keep_s: u64, cap_bytes: u64) -> Tier {
        let config = TierConfig::history("hot", 60, keep_s, cap_bytes, false).expect("config");
        Tier::open(
            config,
            StoreDir::open(temp.path(), false).expect("dir"),
            1 << 22,
        )
        .expect("tier")
    }

    #[test]
    fn a_configuration_refuses_the_values_that_would_make_a_bound_meaningless() {
        assert_eq!(
            TierConfig::history("hot", 0, 60, 1024, false).map(|_| ()),
            Err(StoreError::Config {
                key: "segment_s",
                reason: "must be at least one second"
            })
        );
        assert_eq!(
            TierConfig::history("hot", 60, 0, 1024, false).map(|_| ()),
            Err(StoreError::Config {
                key: "keep_s",
                reason: "must be at least one second"
            })
        );
        assert_eq!(
            TierConfig::history("hot", 60, 60, MAX_CAP_BYTES + 1, false).map(|_| ()),
            Err(StoreError::Config {
                key: "cap_bytes",
                reason: "is past the store's own bound"
            })
        );
        assert_eq!(
            TierConfig::history("../hot", 60, 60, 1024, false).map(|_| ()),
            Err(StoreError::BadName {
                name: "../hot".into()
            })
        );
        let base = TierConfig::history("hot", 60, 60, 1024, false).expect("config");
        assert_eq!(base.max_payload, 1 << 20);
        assert_eq!(base.max_incarnations, 1024);
        assert_eq!(
            base.clone().with_max_payload(0).map(|_| ()),
            Err(StoreError::Config {
                key: "max_payload",
                reason: "must be at least one byte"
            })
        );
        assert_eq!(
            base.clone().with_max_incarnations(0).map(|_| ()),
            Err(StoreError::Config {
                key: "max_incarnations",
                reason: "must name at least one incarnation"
            })
        );
        let bounded = base
            .clone()
            .with_max_payload(4096)
            .and_then(|config| config.with_max_incarnations(16))
            .expect("bounds");
        assert_eq!(bounded.max_payload, 4096);
        assert_eq!(bounded.max_incarnations, 16);
        let policy = bounded.flush_policy().expect("policy");
        assert_eq!(policy.flush_every, 1);
        assert_eq!(policy.segment_s, 60);
        assert_eq!(bounded.segment_bytes(), 4096 * 64);
        assert_eq!(base.kind, TierKind::History);
        assert!(!base.require_tmpfs);
    }

    #[test]
    fn appending_opens_a_segment_and_rolls_it_when_its_bucket_changes() {
        let temp = TempDir::new("append");
        let mut tier = tier(&temp, 3600, 0);
        let first = tier.append(&job(10_000_000_000, 16), 7, 3).expect("append");
        assert_eq!(first.records, 1);
        assert_eq!(first.segment, "hot-0-0.seg.open");
        assert_eq!(first.bytes_written, record_len(16));
        assert!(tier.has_open());
        assert_eq!(tier.bytes(), record_len(16) + HEADER_LEN as u64);
        assert!(first.evicted.is_empty());
        let second = tier.append(&job(20_000_000_000, 16), 7, 3).expect("append");
        assert_eq!(second.segment, "hot-0-0.seg.open");
        assert_eq!(second.records, 2);
        // A record at 65 s belongs to another bucket: the open segment seals and a new one
        // starts, so a segment never spans two steps.
        let third = tier.append(&job(65_000_000_000, 16), 7, 3).expect("append");
        assert_eq!(third.segment, "hot-60-1.seg.open");
        assert_eq!(third.records, 1);
        assert_eq!(tier.scan().segments.len(), 2);
        assert!(tier.scan().segments[0].is_sealed());
        assert!(!tier.scan().segments[1].is_sealed());
        let sealed = tier.seal_open_segment().expect("seal");
        assert_eq!(sealed.as_deref(), Some("hot-60-1.seg"));
        assert_eq!(tier.seal_open_segment().expect("nothing open"), None);
        assert_eq!(tier.next_seq(), 2);
        assert_eq!(tier.scan().segments.len(), 2);
        assert!(tier
            .scan()
            .segments
            .iter()
            .all(|segment| segment.is_sealed()));
        assert_eq!(tier.bytes(), tier.scan().bytes);
    }

    #[test]
    fn keep_s_evicts_a_segment_whose_whole_span_is_older_than_the_window() {
        let temp = TempDir::new("keep");
        let mut tier = tier(&temp, 120, 0);
        tier.append(&job(0, 16), 1, 1).expect("append");
        tier.seal_open_segment().expect("seal");
        // 0..60 plus 120 s of retention: at 179 s it is still inside, at 180 s it is not.
        let early = tier.evict(179_000_000_000).expect("evict");
        assert!(early.is_empty());
        let on_time = tier.evict(180_000_000_000).expect("evict");
        assert_eq!(on_time.segments, vec!["hot-0-0.seg".to_string()]);
        assert_eq!(on_time.bytes, sealed_len(16));
        assert_eq!(on_time.len(), 1);
        assert!(tier.scan().segments.is_empty());
        assert_eq!(tier.bytes(), 0);
    }

    #[test]
    fn eviction_takes_the_oldest_segment_and_the_ring_is_ordered_by_start_not_by_arrival() {
        // A tier over its cap by exactly one segment must lose the *oldest* one. The ring's
        // order is (start_s, seq), so a segment appended later in a later bucket is never
        // mistaken for the oldest, and a clock step that repeats a start second is broken by
        // the sequence rather than by the order the files were listed in.
        let temp = TempDir::new("ordering");
        let one = sealed_len(16);
        let mut tier = tier(&temp, 3600, 2 * one + 1);
        tier.append(&job(0, 16), 1, 1).expect("append bucket 0");
        tier.seal_open_segment().expect("seal bucket 0");
        tier.append(&job(60_000_000_000, 16), 1, 1)
            .expect("append bucket 60");
        tier.seal_open_segment().expect("seal bucket 60");
        assert_eq!(tier.scan().segments.len(), 2);
        assert_eq!(
            tier.oldest().map(|segment| segment.name.as_str()),
            Some("hot-0-0.seg")
        );
        assert_eq!(
            tier.newest().map(|segment| segment.name.as_str()),
            Some("hot-60-1.seg")
        );
        // Bring the total over the cap, so exactly one segment has to go.
        let mut capped = tier_with_cap(&temp, 3600, one);
        let evicted = capped.evict(70_000_000_000).expect("evict");
        assert_eq!(
            evicted.segments,
            vec!["hot-0-0.seg".to_string()],
            "the oldest goes, never the newest"
        );
        assert_eq!(capped.scan().segments.len(), 1);
        assert_eq!(
            capped.scan().segments[0].name,
            "hot-60-1.seg",
            "the newest survives"
        );
        assert_eq!(capped.bytes(), one);
    }

    #[test]
    fn a_segment_of_another_corpus_in_this_directory_is_reported_and_never_touched() {
        // A right-looking name holding an events segment is not a history segment: the
        // header's kind decides, and a tier that adopted it would later delete a file whose
        // records it cannot serve. The committed events fixture is exactly such a file.
        let temp = TempDir::new("corpus");
        temp.write_file("hot-0-0.seg", EVENTS_FIXTURE);
        let mut tier = Tier::open(
            TierConfig::history("hot", 60, 3600, 0, false).expect("config"),
            StoreDir::open(temp.path(), false).expect("dir"),
            1 << 22,
        )
        .expect("tier");
        assert!(tier.scan().is_empty());
        assert_eq!(tier.scan().notes.len(), 1);
        assert_eq!(tier.scan().notes[0].name, "hot-0-0.seg");
        assert!(
            tier.scan().notes[0].reason.contains("another tier kind"),
            "the note names the corpus: {}",
            tier.scan().notes[0].reason
        );
        assert_eq!(tier.bytes(), 0);
        // Eviction past every bound must not remove it.
        let evicted = tier.evict(i64::MAX / 2).expect("evict");
        assert!(evicted.is_empty());
        assert_eq!(temp.read_file("hot-0-0.seg"), EVENTS_FIXTURE.to_vec());
    }

    /// The committed events segment, used as a "right name, wrong corpus" file.
    const EVENTS_FIXTURE: &[u8] = include_bytes!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../fixtures/segment/open-events.seg"
    ));

    #[test]
    fn a_segment_rolls_to_a_new_file_when_its_byte_budget_is_reached() {
        // A segment has a byte budget of its own, derived from the payload bound. When it is
        // reached the open segment seals and a new one starts in the same bucket, so a tier
        // cannot grow one unbounded file while its `keep_s` window is still open.
        let temp = TempDir::new("roll");
        let config = TierConfig::history("hot", 60, 3600, 0, false)
            .expect("config")
            .with_max_payload(16)
            .expect("payload bound");
        assert_eq!(config.segment_bytes(), 1 << 16);
        let mut tier = Tier::open(
            config,
            StoreDir::open(temp.path(), false).expect("dir"),
            1 << 22,
        )
        .expect("tier");
        let mut rolls = 0usize;
        let mut previous = String::new();
        for index in 0..1_200 {
            // Every record is 72 bytes here (48 header + 8 id + 16 payload), so the 64 KiB
            // budget is first crossed around record 911 and the loop stops before a second.
            let outcome = tier
                .append(&job(index as i64 * 1_000_000, 16), 1, 1)
                .expect("append");
            if outcome.segment != previous {
                if index > 0 {
                    rolls += 1;
                }
                previous = outcome.segment;
            }
        }
        assert_eq!(
            rolls, 1,
            "one roll in 1,200 records at a 64 KiB budget: {rolls}"
        );
        assert_eq!(tier.scan().segments.len(), 2);
        assert!(
            tier.scan().segments[0].file_len <= tier.config().segment_file_bound(),
            "a sealed segment is at most its budget plus the record that crossed it and the footer"
        );
        assert!(
            !tier.scan().segments[0].open,
            "the segment that reached its budget is sealed"
        );
        assert!(tier.scan().segments[1].open);
        assert_eq!(tier.scan().segments[0].parsed.start_s, 0);
        assert_eq!(
            tier.scan().segments[1].parsed.start_s,
            0,
            "same bucket, new file"
        );
    }

    #[test]
    fn a_tier_exactly_at_its_byte_cap_keeps_every_segment() {
        // The cap is a *strict* bound: a tier whose bytes equal the cap is inside it, so an
        // eviction at equality would delete a segment the configuration said to keep.
        let temp = TempDir::new("cap-exact");
        let one_segment = sealed_len(16);
        let mut exact = tier(&temp, 3600, one_segment);
        exact.append(&job(0, 16), 1, 1).expect("append");
        exact.seal_open_segment().expect("seal");
        assert_eq!(exact.bytes(), exact.config().cap_bytes);
        let nothing = exact.evict(1_000_000_000).expect("evict");
        assert!(
            nothing.is_empty(),
            "equality is inside the cap: {nothing:?}"
        );
        assert_eq!(exact.scan().segments.len(), 1);
        // One byte over and the oldest segment leaves.
        let over_dir = TempDir::new("cap-exact-over");
        let mut over = tier(&over_dir, 3600, one_segment - 1);
        over.append(&job(0, 16), 1, 1).expect("append");
        over.seal_open_segment().expect("seal");
        let evicted = over.evict(1_000_000_000).expect("evict");
        assert_eq!(evicted.segments, vec!["hot-0-0.seg".to_string()]);
        assert_eq!(over.bytes(), 0);
    }

    #[test]
    fn a_byte_cap_evicts_the_oldest_whole_segment_first_and_holds_strictly() {
        let temp = TempDir::new("cap");
        let one_segment = sealed_len(16);
        let mut tier = tier(&temp, 3600, one_segment + 1);
        tier.append(&job(0, 16), 1, 1).expect("append");
        tier.seal_open_segment().expect("seal");
        assert_eq!(tier.bytes(), one_segment);
        let outcome = tier.append(&job(60_000_000_000, 16), 1, 1).expect("append");
        // The cap is one byte under two segments, so the first (and only sealed) segment goes.
        assert_eq!(outcome.evicted.segments, vec!["hot-0-0.seg".to_string()]);
        assert!(tier.bytes() <= tier.config().cap_bytes);
        assert_eq!(
            tier.scan().segments.len(),
            1,
            "only the new open segment remains"
        );
        assert!(tier.has_open());
        // The open segment fits inside the cap, so it stays: eviction removes whole segments
        // from the *oldest* end and never truncates the one being written.
        let nothing = tier.evict(61_000_000_000).expect("evict");
        assert!(nothing.is_empty());
        assert!(tier.has_open());
        assert_eq!(tier.scan().segments.len(), 1);
    }

    #[test]
    fn a_cap_smaller_than_one_segment_drops_the_open_segment_and_its_handle() {
        let temp = TempDir::new("cap-strict");
        // A cap below even the smallest open segment (header plus one record): the bound is
        // strict, so the tier cannot keep the file it is writing to either.
        let mut tier = tier(&temp, 3600, 100);
        let outcome = tier.append(&job(0, 16), 1, 1).expect("append");
        assert_eq!(
            outcome.evicted.segments,
            vec!["hot-0-0.seg.open".to_string()]
        );
        assert!(
            !tier.has_open(),
            "the handle closes with the name, so nothing appends behind a removed file"
        );
        assert!(tier.bytes() <= tier.config().cap_bytes);
        assert_eq!(tier.bytes(), 0);
        assert!(tier.scan().segments.is_empty());
        // The next append opens a fresh segment rather than writing to a removed one.
        let next = tier.append(&job(1_000_000_000, 16), 1, 1).expect("append");
        assert_eq!(next.segment, "hot-0-1.seg.open");
    }

    #[test]
    fn a_foreign_file_a_link_and_a_directory_are_left_alone_and_reported() {
        let temp = TempDir::new("foreign");
        temp.write_file("notes.txt", b"keep me");
        temp.write_file("hot-0-9.seg", b"not a segment of ours");
        temp.mkdir("hot-0-8.seg");
        // The link's target lives outside the tier directory: a store that followed it would
        // write, truncate or remove a file it never owned.
        let outside_dir = TempDir::new("tier-outside");
        let outside = outside_dir.write_file("outside", b"outside");
        temp.symlink_to("hot-0-7.seg", &outside);
        let mut tier = Tier::open(
            TierConfig::history("hot", 60, 3600, 0, false).expect("config"),
            StoreDir::open(temp.path(), false).expect("dir"),
            1 << 22,
        )
        .expect("tier");
        assert!(tier.scan().is_empty());
        assert_eq!(tier.scan().notes.len(), 4);
        let reasons: Vec<String> = tier
            .scan()
            .notes
            .iter()
            .map(|note| note.reason.clone())
            .collect();
        assert!(
            reasons
                .iter()
                .any(|reason| reason.contains("not a segment name")),
            "notes.txt: {reasons:?}"
        );
        assert!(
            reasons.iter().any(|reason| reason.contains("no header")),
            "a right-looking name holding the wrong bytes: {reasons:?}"
        );
        assert!(
            reasons.iter().any(|reason| reason.contains("is a symlink")),
            "the link is reported, never followed: {reasons:?}"
        );
        assert!(
            reasons
                .iter()
                .any(|reason| reason.contains("is a directory")),
            "the directory: {reasons:?}"
        );
        // Appending and evicting never touch them.
        tier.append(&job(0, 16), 1, 1).expect("append");
        tier.seal_open_segment().expect("seal");
        let evicted = tier.evict(i64::MAX / 2).expect("evict");
        assert_eq!(evicted.len(), 1, "only the store's own segment left");
        assert_eq!(temp.read_file("notes.txt"), b"keep me".to_vec());
        assert_eq!(
            temp.read_file("hot-0-9.seg"),
            b"not a segment of ours".to_vec()
        );
        assert_eq!(
            outside_dir.read_file("outside"),
            b"outside".to_vec(),
            "the link's target was never written, truncated or removed"
        );
        assert!(temp.at("hot-0-8.seg").is_dir());
        assert!(std::fs::symlink_metadata(temp.at("hot-0-7.seg"))
            .map(|meta| meta.file_type().is_symlink())
            .unwrap_or(false));
    }

    #[test]
    fn a_restart_repairs_a_torn_tail_before_appending_and_counts_it() {
        let temp = TempDir::new("restart");
        let mut tier = tier(&temp, 3600, 0);
        tier.append(&job(0, 16), 1, 1).expect("append");
        tier.append(&job(1_000_000_000, 16), 1, 1).expect("append");
        let name = tier
            .open_handle()
            .map(|open| open.name().to_string())
            .expect("open");
        let len = tier
            .open_handle()
            .map(crate::segment::OpenSegment::len)
            .expect("len");
        drop(tier);
        // Cut the second record in half: the crash the scan must repair.
        temp.truncate(&name, len - 8);
        let mut reopened = Tier::open(
            TierConfig::history("hot", 60, 3600, 0, false).expect("config"),
            StoreDir::open(temp.path(), false).expect("dir"),
            1 << 22,
        )
        .expect("tier");
        assert_eq!(reopened.scan().torn_repaired, vec![name.clone()]);
        assert_eq!(reopened.scan().segments.len(), 1);
        assert!(
            reopened.bytes() < len,
            "the tail was cut back to the last good record"
        );
        assert_eq!(reopened.scan().segments[0].records, 1);
        assert!(
            reopened.has_open(),
            "the open segment is adopted, not created again"
        );
        let continued = reopened
            .append(&job(2_000_000_000, 16), 1, 1)
            .expect("append after repair");
        assert_eq!(
            continued.records, 2,
            "the sequence continues from the repaired file"
        );
        assert_eq!(continued.segment, name);
    }

    #[test]
    fn a_corrupt_segment_is_quarantined_and_the_tier_keeps_the_rest() {
        let temp = TempDir::new("quarantine");
        let mut tier = tier(&temp, 3600, 0);
        tier.append(&job(0, 16), 1, 1).expect("append");
        tier.seal_open_segment().expect("seal");
        let sealed = "hot-0-0.seg";
        let mut bytes = temp.read_file(sealed);
        let before = bytes.len();
        // Flip a body byte of the first record: the body checksum fails.
        let offset = HEADER_LEN + 48 + 8 + 1;
        bytes[offset] ^= 0xFF;
        temp.write_file(sealed, &bytes);
        let reopened = Tier::open(
            TierConfig::history("hot", 60, 3600, 0, false).expect("config"),
            StoreDir::open(temp.path(), false).expect("dir"),
            1 << 22,
        )
        .expect("tier");
        assert_eq!(reopened.scan().quarantined, vec![sealed.to_string()]);
        assert!(reopened.scan().segments.is_empty());
        assert_eq!(reopened.bytes(), 0);
        assert!(reopened.scan().torn_repaired.is_empty());
        assert_eq!(
            temp.read_file(&format!("{sealed}.corrupt")).len(),
            before,
            "the corrupt bytes are kept for an operator, not deleted"
        );
        assert!(!temp.exists(sealed));
    }

    #[test]
    fn a_range_query_walks_segments_in_order_and_stops_at_its_bounds() {
        let temp = TempDir::new("range");
        let mut tier = tier(&temp, 3600, 0);
        for (index, time) in [0i64, 1_000_000_000, 2_000_000_000].iter().enumerate() {
            tier.append(&job(*time, 16 + index), 1, 1).expect("append");
        }
        let answer = tier
            .range(1_000_000_000, 2_000_000_000, 8, 1 << 20)
            .expect("range");
        assert_eq!(answer.len(), 2);
        assert!(!answer.is_empty());
        assert!(!answer.truncated);
        assert_eq!(
            answer.payloads()[0],
            block(17),
            "the record at 1 s carries 17 bytes"
        );
        let bounded = tier.range(0, i64::MAX, 1, 1 << 20).expect("range");
        assert_eq!(bounded.len(), 1);
        assert!(
            bounded.truncated,
            "a record bound that stops the answer is reported"
        );
        assert_eq!(
            tier.range(5, 1, 8, 1 << 20),
            Err(StoreError::RangeInverted { from: 5, to: 1 })
        );
        assert_eq!(
            tier.range(0, 9, 0, 1 << 20),
            Err(StoreError::QueryLimit { asked: 0, max: 0 })
        );
        // A window past every record answers nothing; the segment is skipped by one
        // comparison, not by reading it.
        let empty = tier
            .range(10_000_000_000, 20_000_000_000, 8, 1 << 20)
            .expect("range");
        assert!(empty.is_empty());
        // The reverse: a window before every record.
        let before = tier.range(0, 0, 8, 1 << 20).expect("range");
        assert_eq!(before.len(), 1, "only the record exactly at 0");
    }

    #[test]
    fn reachability_is_what_a_tier_holds_and_retirement_respects_the_other_tiers() {
        let temp = TempDir::new("reach");
        let mut tier = tier(&temp, 3600, 0);
        tier.append(&job(0, 16), 1, 1).expect("append");
        let mut dictionary = crate::dictionary::ActiveDictionary::new(1, 8).expect("dictionary");
        dictionary.adopt(1, "/a").expect("adopt");
        dictionary.adopt(2, "/b").expect("adopt");
        let reach = tier.reachable();
        assert!(reach.contains(&1));
        let elsewhere: BTreeSet<u64> = [2u64].into_iter().collect();
        let retired = tier.retire(&mut dictionary, &elsewhere);
        assert!(
            retired.is_empty(),
            "both ids are reachable: one here, one elsewhere"
        );
        let retired = tier.retire(&mut dictionary, &BTreeSet::new());
        assert_eq!(
            retired.ids,
            vec![2],
            "id 2 is referenced nowhere the store can see, so it retires; id 1 is still here"
        );
        assert!(dictionary.holds(1), "a segment still names id 1");
        tier.evict(i64::MAX / 2).expect("evict everything");
        let retired = tier.retire(&mut dictionary, &BTreeSet::new());
        assert_eq!(
            retired.ids,
            vec![1],
            "with no segment left, the last id retires"
        );
        assert!(dictionary.is_empty());
        assert!(tier.scan().notes.is_empty());
    }

    #[test]
    fn a_tier_refuses_an_incarnation_count_past_its_own_bound() {
        let temp = TempDir::new("ids");
        let config = TierConfig::history("hot", 60, 3600, 0, false)
            .expect("config")
            .with_max_incarnations(2)
            .expect("bound");
        let mut tier = Tier::open(
            config,
            StoreDir::open(temp.path(), false).expect("dir"),
            1 << 22,
        )
        .expect("tier");
        let mut many = job(0, 16);
        many.incarnations = vec![1, 2, 3];
        assert_eq!(
            tier.append(&many, 1, 1),
            Err(StoreError::SegmentIncarnations {
                seq: 1,
                count: 3,
                max: 2
            })
        );
        let mut two = job(0, 16);
        two.incarnations = vec![1, 2];
        assert_eq!(
            tier.append(&two, 1, 1).map(|outcome| outcome.records),
            Ok(1)
        );
    }
}
