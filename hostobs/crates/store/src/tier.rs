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
use crate::format::{
    check_tier_name, SegmentFileName, SegmentHeader, TierKind, HEADER_LEN, QUARANTINE_SUFFIX,
    SEQ_NAME_MAX,
};
use crate::fsio::{EntryKind, StoreDir};
use crate::queue::{FlushPolicy, Job};
use crate::segment::{OpenSegment, SegmentScan, TailState};

/// The largest number of segments one tier will hold; a directory beyond this is a
/// configuration that cannot be enforced (the cap is meant to bind well below it).
pub const MAX_SEGMENTS: usize = 4096;

/// The most eviction reports a tier keeps in [`TierScan::notes`] after its last rescan, and the
/// most the store keeps for `Store::notes`. When one more arrives the **oldest** report is
/// dropped, so a source of repeated vanished files cannot grow memory without bound. One pass's
/// [`Evicted::notes`] is bounded by the entries that pass removes (at most [`MAX_SEGMENTS`] live
/// segments and as many quarantined files), and the rescan's own notes by the directory listing.
pub const MAX_EVICTION_NOTES: usize = 256;

/// The highest sequence a name the scan has **not** proved to be its own may reserve: one below
/// [`SEQ_NAME_MAX`]. A name at the bound is reserved only by a file that is adopted (its header
/// is this tier's and agrees with the name), so a look-alike there cannot make the next
/// `open_segment` refuse for ever (HOB-55).
const UNPROVEN_SEQ_MAX: u64 = SEQ_NAME_MAX - 1;

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
    /// Segments quarantined by *this* scan: a corrupt tail, a file past the reader's bound, a
    /// sealed name without a verifiable footer, or a header that disagrees with its name.
    pub quarantined: Vec<String>,
    /// Every quarantined file this tier owns (`<segment name>.corrupt`), those this scan made
    /// and those an earlier run left, oldest `(start_s, seq)` first. They count in
    /// [`TierScan::bytes`] and are evicted before any live segment (HOB-43).
    pub quarantined_files: Vec<QuarantinedFile>,
    /// Files quarantined because they grew past the reader's bound: a file bigger than this
    /// store's own segment budget is not a segment it wrote, so it is taken out of service and
    /// counted rather than silently skipped.
    pub oversized: Vec<String>,
    /// Store-owned names that could not be read: reported and left in place, because a file
    /// whose header cannot be read cannot be *proved* to be this store's own, and a store only
    /// ever removes what it can prove it wrote.
    pub unreadable: Vec<String>,
    /// Stale `.seg.open` twins removed because a sealed segment with the same (tier, start_s,
    /// seq) was verified: the crash window between a seal's rename and its unlink.
    pub stale_open_removed: Vec<String>,
    /// The tier's total bytes in its own files: its segments plus its quarantined files, so
    /// `cap_bytes` bounds both together.
    pub bytes: u64,
}

/// One quarantined file the tier owns: a segment renamed to `<name>.corrupt`.
///
/// It is not served, but it is the tier's disk use, so it is in the byte total and the ring of
/// things the bounds may remove. It is adopted only when its name parses to this tier and its
/// header is this tier's corpus (the proof a live segment needs); the key is the `(start_s,
/// seq)` its name keeps.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct QuarantinedFile {
    /// The name on disk, with the quarantine suffix.
    pub name: String,
    /// The segment name it was, parsed (its `(start_s, seq)` is the ordering key).
    pub parsed: SegmentFileName,
    /// Its length in bytes.
    pub len: u64,
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
    /// Where the eviction reports start in `scan.notes`: the length of the notes the last
    /// rescan made. Reports are capped at [`MAX_EVICTION_NOTES`] from here; the rescan's own
    /// notes are never dropped to make room.
    reports_from: usize,
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
            reports_from: 0,
        };
        tier.rescan()?;
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
    /// Four crash and hostile-file shapes are recognised here rather than left to a reader:
    ///
    /// * **A stale `.seg.open` beside its sealed twin.** The seal's order (rename, fsync,
    ///   unlink) leaves both names if the process dies between the last two steps. A sealed
    ///   segment is verified first; an open name with the same `(tier, start_s, seq)` is a copy
    ///   of it, so it is removed (`stale_open_removed`) — never counted, never reopened, never
    ///   served twice.
    /// * **A file past the reader's bound.** It cannot be this store's segment (the bound is
    ///   checked against every tier's own `segment_file_bound` by
    ///   [`crate::store::StoreConfig::check`]), so it is quarantined under `<name>.corrupt` and
    ///   counted (`oversized`), not skipped: a skipped file would sit outside the ring and
    ///   outside the byte total, so eviction would never reach it.
    /// * **A store-owned name that cannot be read** (EACCES, EIO, a race) is reported
    ///   (`unreadable`) and left in place, because a file whose header cannot be read cannot be
    ///   *proved* to be this store's own and a store only removes what it can prove.
    /// * **A file the store did not write** (a foreign name, another tier's corpus, a link, a
    ///   directory, a FIFO) is reported as a note and never touched.
    ///
    /// # Errors
    ///
    /// [`StoreError::EntryLimit`] and [`StoreError::Symlink`] (the root). A per-file refusal is
    /// a report, not an abort: one damaged or unreadable segment must not make the whole tier
    /// unopenable.
    pub fn rescan(&mut self) -> Result<(), StoreError> {
        let entries = self.dir.list(MAX_SEGMENTS)?;
        let mut segments: Vec<RingSegment> = Vec::new();
        let mut notes: Vec<TierNote> = Vec::new();
        let mut torn_repaired: Vec<String> = Vec::new();
        let mut quarantined: Vec<String> = Vec::new();
        let mut oversized: Vec<String> = Vec::new();
        let mut unreadable: Vec<String> = Vec::new();
        let mut stale_open_removed: Vec<String> = Vec::new();
        let mut quarantined_files: Vec<QuarantinedFile> = Vec::new();
        let mut highest_seen: Option<u64> = None;
        let mut bytes: u64 = 0;
        for entry in entries {
            // HOB-43: a quarantined file (`<segment name>.corrupt`) is still this tier's disk
            // use, so it is counted in the byte total and ordered by the (start_s, seq) its
            // name keeps. It is adopted only on the same proof a live segment needs: a name
            // that parses to this tier, a regular file, and a header that is this tier's
            // corpus. Anything else is reported and never counted, evicted or removed.
            if entry.name.ends_with(QUARANTINE_SUFFIX) {
                let Ok(parsed) = SegmentFileName::parse_quarantined(&entry.name) else {
                    notes.push(TierNote {
                        name: entry.name.clone(),
                        reason: "not a segment name this store writes".to_string(),
                    });
                    continue;
                };
                if parsed.tier != self.config.name {
                    notes.push(TierNote {
                        name: entry.name.clone(),
                        reason: "names another tier".to_string(),
                    });
                    continue;
                }
                // Not yet proved this tier's own: reserves at most `UNPROVEN_SEQ_MAX`.
                highest_seen = highest_seen.max(Some(parsed.seq.min(UNPROVEN_SEQ_MAX)));
                if entry.kind != EntryKind::File {
                    notes.push(TierNote {
                        name: entry.name.clone(),
                        reason: format!("is {}, not a regular file", entry.kind),
                    });
                    continue;
                }
                match self.header_of_window(&entry.name) {
                    Ok(header) if header.kind == self.config.kind => {
                        bytes = bytes.saturating_add(entry.len);
                        quarantined_files.push(QuarantinedFile {
                            name: entry.name.clone(),
                            parsed,
                            len: entry.len,
                        });
                    }
                    Ok(_) => notes.push(TierNote {
                        name: entry.name.clone(),
                        reason: "quarantine name, but its header names another tier kind; left alone"
                            .to_string(),
                    }),
                    Err(error) => notes.push(TierNote {
                        name: entry.name.clone(),
                        reason: format!(
                            "quarantine name, but it holds no header this tier can read ({}); left alone",
                            error.kind()
                        ),
                    }),
                }
                continue;
            }
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
            // Every name of this tier reserves its sequence number, whatever becomes of the
            // file (adopted, quarantined, unreadable, foreign): a name never comes back. Until
            // the file is proved this tier's own it reserves at most `UNPROVEN_SEQ_MAX`; an
            // adopted file reserves its full sequence below.
            highest_seen = highest_seen.max(Some(parsed.seq.min(UNPROVEN_SEQ_MAX)));
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
            // A file past the bound is not a file this store wrote: the writer's largest legal
            // segment is `segment_file_bound`, and the bound is only accepted when it exceeds
            // that. It is quarantined rather than skipped, because a skipped file is one the
            // ring and the cap accounting both ignore.
            if entry.len > self.config.segment_file_bound()
                || entry.len > u64::try_from(self.max_file_bytes).unwrap_or(u64::MAX)
            {
                // The quarantine name comes from the parsed name this scan already accepted, so
                // it cannot refuse here: `quarantine_name` is exactly this formatting plus a
                // parse that has already succeeded.
                let quarantine = parsed.quarantine_file_name();
                // Only quarantine what is provably ours: a look-alike name holding someone
                // else's bytes is reported and left alone. The header is read as a **window**
                // here: this branch exists because the file is past the whole-file bound, so
                // the whole-file read is exactly the refusal that must not stop the check.
                match self.header_of_window(&entry.name) {
                    Ok(header) if header.kind == self.config.kind => {}
                    Ok(_) => {
                        notes.push(TierNote {
                            name: entry.name.clone(),
                            reason: "oversized, and header names another tier kind".to_string(),
                        });
                        continue;
                    }
                    Err(error) => {
                        notes.push(TierNote {
                            name: entry.name.clone(),
                            reason: format!(
                                "oversized, and holds no header this tier can read: {}",
                                error.kind()
                            ),
                        });
                        continue;
                    }
                }
                match self.dir.rename(&entry.name, &quarantine) {
                    Ok(()) => {
                        oversized.push(entry.name.clone());
                        quarantined.push(entry.name.clone());
                        bytes = bytes.saturating_add(entry.len);
                        quarantined_files.push(QuarantinedFile {
                            name: quarantine,
                            parsed,
                            len: entry.len,
                        });
                    }
                    Err(error) => notes.push(TierNote {
                        name: entry.name.clone(),
                        reason: format!("oversized and could not be quarantined: {}", error.kind()),
                    }),
                }
                continue;
            }
            // Only a file whose own header says it is this tier's corpus is ours to touch; a
            // name that looks right and holds something else is reported and left alone.
            let header = match self.header_of(&entry.name) {
                Ok(header) => header,
                Err(error) if error.is_format() => {
                    // Bytes that are not this format's header at all: a foreign file holding
                    // the right name. Reported, never counted as damage this tier suffered.
                    notes.push(TierNote {
                        name: entry.name.clone(),
                        reason: format!("holds no header this tier can read: {}", error.kind()),
                    });
                    continue;
                }
                Err(error) => {
                    // A header that could not be *read* (EACCES, EIO, a race, a file that grew
                    // past the bound between the listing and the read) is a different fact:
                    // the store cannot determine what this file is, it is not an absence of
                    // anything, and it is counted as unreadable and left in place.
                    unreadable.push(entry.name.clone());
                    notes.push(TierNote {
                        name: entry.name.clone(),
                        reason: format!("header could not be read: {}", error.kind()),
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
            let scan = match crate::segment::scan(
                &self.dir,
                &entry.name,
                self.max_file_bytes,
                self.config.max_incarnations,
            ) {
                Ok(scan) => scan,
                Err(error) => {
                    // A single unreadable segment must not brick the tier's open, and it must
                    // not be silently dropped either: it is reported and left where it is,
                    // because the store cannot prove what it is without reading it.
                    unreadable.push(entry.name.clone());
                    notes.push(TierNote {
                        name: entry.name.clone(),
                        reason: format!("could not be scanned: {}", error.kind()),
                    });
                    continue;
                }
            };
            // HOB-42: the name makes two claims, and the bytes must back both before the file
            // is served. The header's `seq`/`start_real_ns` are the name's numbers, and a name
            // that is not `.seg.open` is sealed, which needs a footer that verified. A file that
            // fails either is quarantined whole, never repaired: a torn tail is a normal shape
            // for an *open* segment, and for a sealed name it is a file that is not what it
            // says. A corrupt tail has its own, more precise, refusal below.
            let name_refusal = if matches!(scan.tail, TailState::Corrupt { .. }) {
                None
            } else {
                scan.check_name(&parsed).err()
            };
            if let Some(refusal) = name_refusal {
                let quarantine = parsed.quarantine_file_name();
                match self.dir.rename(&entry.name, &quarantine) {
                    Ok(()) => {
                        quarantined.push(entry.name.clone());
                        bytes = bytes.saturating_add(scan.file_len);
                        quarantined_files.push(QuarantinedFile {
                            name: quarantine,
                            parsed,
                            len: scan.file_len,
                        });
                        notes.push(TierNote {
                            name: entry.name.clone(),
                            reason: format!("quarantined, {}: {refusal}", refusal.kind()),
                        });
                    }
                    Err(error) => {
                        unreadable.push(entry.name.clone());
                        notes.push(TierNote {
                            name: entry.name.clone(),
                            reason: format!(
                                "{} and could not be quarantined: {}",
                                refusal.kind(),
                                error.kind()
                            ),
                        });
                    }
                }
                continue;
            }
            match &scan.tail {
                TailState::Complete => {}
                TailState::Incomplete { .. } => match scan.truncate_to_good_end(&self.dir) {
                    Ok(_) => torn_repaired.push(entry.name.clone()),
                    Err(error) => {
                        // The repair itself was refused (a read-only file, a full disk, a race).
                        // The file stays where it is and is reported: a tier that aborted here
                        // would be unopenable because of one file somebody else broke.
                        unreadable.push(entry.name.clone());
                        notes.push(TierNote {
                            name: entry.name.clone(),
                            reason: format!("torn tail could not be repaired: {}", error.kind()),
                        });
                        continue;
                    }
                },
                TailState::Corrupt { .. } => {
                    let quarantine = parsed.quarantine_file_name();
                    match scan.quarantine(&self.dir, &quarantine) {
                        Ok(()) => {
                            quarantined.push(entry.name.clone());
                            bytes = bytes.saturating_add(scan.file_len);
                            quarantined_files.push(QuarantinedFile {
                                name: quarantine,
                                parsed,
                                len: scan.file_len,
                            });
                        }
                        Err(error) => {
                            // A corrupt file that cannot be renamed out of the way stays in
                            // the ring's reach only as a report: it is not adopted, and the
                            // tier keeps serving the rest.
                            unreadable.push(entry.name.clone());
                            notes.push(TierNote {
                                name: entry.name.clone(),
                                reason: format!(
                                    "corrupt and could not be quarantined: {}",
                                    error.kind()
                                ),
                            });
                        }
                    }
                    continue;
                }
            }
            // Re-scan the repaired file, so the ring's facts describe what is actually there.
            let scan = if scan.tail.is_damaged() {
                match crate::segment::scan(
                    &self.dir,
                    &entry.name,
                    self.max_file_bytes,
                    self.config.max_incarnations,
                ) {
                    Ok(scan) => scan,
                    Err(error) => {
                        unreadable.push(entry.name.clone());
                        notes.push(TierNote {
                            name: entry.name.clone(),
                            reason: format!("could not be re-scanned: {}", error.kind()),
                        });
                        continue;
                    }
                }
            } else {
                scan
            };
            bytes = bytes.saturating_add(scan.file_len);
            segments.push(ring_segment(&entry.name, parsed, scan));
        }
        // The stale-open rule: a sealed segment wins, and its `.seg.open` twin is a copy of it.
        // A reader that saw both would serve every record twice, and `reopen_open_segment`
        // would append behind a file whose records already left.
        let sealed_keys: Vec<(u64, u64)> = segments
            .iter()
            .filter(|segment| !segment.open)
            .map(|segment| (segment.parsed.start_s, segment.parsed.seq))
            .collect();
        let mut kept: Vec<RingSegment> = Vec::with_capacity(segments.len());
        for segment in segments {
            if segment.open && sealed_keys.contains(&(segment.parsed.start_s, segment.parsed.seq)) {
                match self.dir.remove(&segment.name) {
                    Ok(()) => {
                        bytes = bytes.saturating_sub(segment.file_len);
                        stale_open_removed.push(segment.name.clone());
                        notes.push(TierNote {
                            name: segment.name.clone(),
                            reason: "stale open twin of a sealed segment; removed".to_string(),
                        });
                    }
                    Err(error) => notes.push(TierNote {
                        name: segment.name.clone(),
                        reason: format!(
                            "stale open twin of a sealed segment, could not be removed: {}",
                            error.kind()
                        ),
                    }),
                }
                continue;
            }
            kept.push(segment);
        }
        let mut segments = kept;
        // The ring's order: a segment's own start, then its sequence. A clock step can make a
        // name's start repeat, so the sequence is the tie-break rather than the name's bytes.
        segments.sort_by(|left, right| {
            (left.parsed.start_s, left.parsed.seq).cmp(&(right.parsed.start_s, right.parsed.seq))
        });
        // The quarantined files are ordered by the same key, so "oldest first" means the same
        // thing for debris as for history; the name is the last tie-break, so the order of a
        // directory listing never decides which file goes first.
        quarantined_files.sort_by_key(|f| (f.parsed.start_s, f.parsed.seq, f.name.clone()));
        // An adopted live segment is the one proof a name can have: its header is this tier's
        // and `check_name` held it to the name's own `seq`. It reserves its full sequence,
        // even `SEQ_NAME_MAX`; every other name (a quarantined file's key was not identity-
        // checked, a look-alike was not read as a segment) reserved at most `UNPROVEN_SEQ_MAX`
        // above.
        let proven_max = segments.iter().map(|segment| segment.parsed.seq).max();
        highest_seen = highest_seen.max(proven_max);
        self.scan = TierScan {
            segments,
            notes,
            torn_repaired,
            quarantined,
            quarantined_files,
            oversized,
            unreadable,
            stale_open_removed,
            bytes,
        };
        self.bytes = self.scan.bytes;
        self.reports_from = self.scan.notes.len();
        // The water mark only ever rises: a restart continues past every sequence the
        // directory can show, including the ones this handle already issued.
        //
        // The mark covers every name of this tier the directory shows, not only the live
        // segments: a quarantined file (`hot-H-0.seg.open.corrupt`) still holds its sequence,
        // or the next segment would reuse the name, crash corrupt again, and a later
        // quarantine would be refused by the existing destination.
        let highest = highest_seen;
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

    /// The header one name holds, read as a fixed window rather than under the file bound.
    ///
    /// The oversized branch of [`Tier::rescan`] must answer "is this file mine?" for a file
    /// that is *past* the whole-file bound, so [`StoreDir::read_at`] — which refuses by the
    /// file's size — would refuse before reading a byte. The window is the same 64 bytes and
    /// is bounded by its own length, so nothing is allocated beyond a header.
    fn header_of_window(&self, name: &str) -> Result<SegmentHeader, StoreError> {
        SegmentHeader::check_bytes(&self.dir.read_window(name, 0, HEADER_LEN)?)
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
    /// policy that makes the ring's "evict whole segments" possible),
    /// [`StoreError::LengthOverflow`] when the next sequence would exceed [`SEQ_NAME_MAX`]
    /// (legitimate exhaustion: 136 years at one segment a second), and every IO refusal.
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
        if seq > SEQ_NAME_MAX {
            // A name above the bound is not a segment name of this store (HOB-55), so the
            // sequence space is exhausted rather than continued under a name no scan accepts.
            return Err(StoreError::LengthOverflow);
        }
        self.seq_water = seq.checked_add(1).ok_or(StoreError::LengthOverflow)?;
        let name = self.name_for(start_s, seq, true);
        let parsed = SegmentFileName {
            tier: self.config.name.clone(),
            start_s,
            seq,
            open: true,
        };
        let header = SegmentHeader {
            seq,
            epoch,
            dict_generation,
            kind: self.config.kind,
            payload: self.config.kind.payload_kind(),
            // The header carries the number the name carries (HOB-42's identity rule).
            start_real_ns: parsed.start_real_ns(),
            max_payload: self.config.max_payload,
            max_incarnations: self.config.max_incarnations,
        };
        let open = OpenSegment::create(&self.dir, &name, header)?;
        self.bytes = self.bytes.saturating_add(open.len());
        // The ring is ordered at every insertion, not only by the next restart's scan: the
        // bounds loop below stops at the first segment that is still inside its window, so an
        // out-of-order entry would let an expired segment survive until a restart. A clock
        // step backwards is the case that produces one: bucket 60 open, then bucket 0.
        let entry = RingSegment {
            name: name.clone(),
            parsed: parsed.clone(),
            open: true,
            file_len: open.len(),
            records: 0,
            first_real_ns: None,
            last_real_ns: None,
            incarnations: BTreeSet::new(),
        };
        let at = self
            .scan
            .segments
            .iter()
            .position(|existing| (existing.parsed.start_s, existing.parsed.seq) > (start_s, seq))
            .unwrap_or(self.scan.segments.len());
        self.scan.segments.insert(at, entry);
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
                    || open.header().epoch != epoch
                    || open.header().dict_generation != dict_generation
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
        // those facts, and a stale entry would understate the tier's own bytes. The entry is
        // found by the handle's own name, never by `find(|s| s.open)`: a tier that failed a
        // seal could hold an entry still marked open for a name that is no longer the handle's.
        let handle_name = self
            .open
            .as_ref()
            .map_or_else(String::new, |open| open.name().to_string());
        if let Some(entry) = self
            .scan
            .segments
            .iter_mut()
            .find(|segment| segment.name == handle_name)
        {
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
        // `keep_s` also applies to quarantined files, by the (start_s, seq) their names keep:
        // debris does not outlive the history it came from (HOB-43).
        while let Some(file) = self.scan.quarantined_files.first() {
            let start_ns = i64::try_from(file.parsed.start_s.saturating_mul(1_000_000_000))
                .unwrap_or(i64::MAX);
            let span_end_ns = start_ns.saturating_add(
                i64::try_from(self.config.segment_s.saturating_mul(1_000_000_000))
                    .unwrap_or(i64::MAX),
            );
            let keep_ns =
                i64::try_from(self.config.keep_s.saturating_mul(1_000_000_000)).unwrap_or(i64::MAX);
            if span_end_ns.saturating_add(keep_ns) > now_ns {
                break;
            }
            self.remove_quarantined(&mut evicted);
        }
        // `cap_bytes`: the total counts live segments *and* quarantined files, and the debris
        // goes first, oldest first: a source of repeated damage must exhaust its own
        // quarantined files before it can cost the tier a single good segment. Only then does
        // the oldest whole live segment leave, until the total is at or under the cap.
        if self.config.cap_bytes > 0 {
            while self.bytes > self.config.cap_bytes {
                if !self.scan.quarantined_files.is_empty() {
                    self.remove_quarantined(&mut evicted);
                    continue;
                }
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

    /// Remove the oldest quarantined file this tier owns, updating the byte total.
    ///
    /// Only a file the last scan adopted is in `quarantined_files`: a name that did not parse
    /// to this tier, or whose header was not this tier's corpus, was reported and is never
    /// here, so it is never removed. The scan-time proof is the whole guarantee, as for
    /// [`Tier::remove_segment`]; [`StoreDir::remove`] still refuses a link.
    ///
    /// A quarantined file is kept for an operator to inspect, so an operator may delete it by
    /// hand; one bad file must not stop the cap loop, and an `append` that already wrote its
    /// record must not fail because of debris. The entry therefore always leaves the list and
    /// its bytes always leave the total: a file that is already gone is dropped quietly and is
    /// **not** counted as evicted (nothing was removed), and a removal that fails for another
    /// reason is dropped and reported, in [`TierScan::notes`] and in [`Evicted::notes`] (the
    /// caller of `append`/`evict` sees the second, and a later rescan resets the first,
    /// HOB-58).
    ///
    /// Known limit: a file that could not be removed stays on disk, uncounted, until the next
    /// restart's scan adopts it again.
    fn remove_quarantined(&mut self, evicted: &mut Evicted) {
        if self.scan.quarantined_files.is_empty() {
            return;
        }
        let file = self.scan.quarantined_files.remove(0);
        self.bytes = self.bytes.saturating_sub(file.len);
        self.scan.bytes = self.bytes;
        let gone = matches!(self.dir.metadata(&file.name), Ok(None));
        if gone {
            let note = TierNote {
                name: file.name,
                reason: "quarantined file already gone; dropped from the total".to_string(),
            };
            self.report(evicted, note);
            return;
        }
        match self.dir.remove(&file.name) {
            Ok(()) => {
                evicted.quarantined.push(file.name);
                evicted.bytes = evicted.bytes.saturating_add(file.len);
            }
            Err(error) => {
                let note = TierNote {
                    name: file.name,
                    reason: format!(
                        "quarantined file could not be removed ({}); dropped from the byte total",
                        error.kind()
                    ),
                };
                self.report(evicted, note);
            }
        }
    }

    /// Record a note about an entry an eviction dropped without removing: in the scan's own
    /// notes and in the pass's [`Evicted::notes`], which is what the caller of `append` or
    /// `evict` receives.
    ///
    /// The reports in `scan.notes` (those after the rescan's own notes) are capped at
    /// [`MAX_EVICTION_NOTES`]: the oldest report is dropped to make room, the same policy as
    /// the store's ring, and the rescan's notes are never dropped.
    fn report(&mut self, evicted: &mut Evicted, note: TierNote) {
        evicted.notes.push(note.clone());
        let kept = self.scan.notes.len().saturating_sub(self.reports_from);
        if kept >= MAX_EVICTION_NOTES {
            self.scan.notes.remove(self.reports_from);
        }
        self.scan.notes.push(note);
    }

    /// Remove one segment this tier owns, updating the ring and the byte total.
    ///
    /// The ownership check is the ring: a name this tier no longer holds in `scan.segments` is
    /// not removed. It is *not* re-validated on the filesystem immediately before the unlink —
    /// a name swapped between the scan and the unlink would be a link this call would then
    /// follow (`HOB-31`), so the scan-time check plus [`StoreDir::remove`]'s own
    /// `symlink_metadata` refusal are the whole guarantee, and nothing here claims more.
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
        // A segment someone already deleted by hand has nothing left to remove: its entry and
        // bytes leave the ring (so the cap loop makes progress instead of failing on the same
        // head every call), and it is reported, not counted as evicted.
        let gone = self.dir.metadata(name)?.is_none();
        if !gone {
            self.dir.remove(name)?;
        }
        self.scan.segments.remove(position);
        self.bytes = self.bytes.saturating_sub(len);
        self.scan.bytes = self.bytes;
        if gone {
            let note = TierNote {
                name: name.to_string(),
                reason: "segment already gone; dropped from the ring".to_string(),
            };
            self.report(evicted, note);
            return Ok(());
        }
        evicted.segments.push(name.to_string());
        evicted.bytes = evicted.bytes.saturating_add(len);
        Ok(())
    }

    /// Seal the open segment: footer, fsync, rename to the sealed name, and the open name
    /// disappears atomically.
    ///
    /// A refusal is rolled back to a consistent tier: the open handle is put back (a seal that
    /// failed before the rename left the open file exactly as it was), the ring entry is found
    /// by its name rather than by "whichever entry says open" (the failed entry is the one the
    /// next append would otherwise update), and the temporary file the seal created is removed
    /// through [`StoreDir::seal_open_segment`]'s own error path. When even the handle cannot be
    /// restored, the entry is reported in [`TierScan::notes`] rather than left claiming to be
    /// open.
    ///
    /// # Errors
    ///
    /// [`StoreError::Config`] when nothing is open, and every seal refusal.
    pub fn seal_open_segment(&mut self) -> Result<Option<String>, StoreError> {
        let Some(open) = self.open.take() else {
            return Ok(None);
        };
        // The sealed name keeps the start second of the open name. It is *not* re-derived from
        // the header with the current `segment_s`: after a restart with a different step that
        // would rename the segment to a start its header does not carry, and the identity
        // check (HOB-42) would then quarantine good data.
        let start_s = match SegmentFileName::parse(open.name()) {
            Ok(parsed) => parsed.start_s,
            Err(error) => {
                self.open = Some(open);
                return Err(error);
            }
        };
        let seq = open.header().seq;
        let open_name = open.name().to_string();
        let sealed = self.name_for(start_s, seq, false);
        let temp = format!("{sealed}.tmp");
        let sealed_report = open.seal(&self.dir, &sealed, &temp, self.config.segment_file_bound());
        let report = match sealed_report {
            Ok(report) => report,
            Err(error) => {
                // The seal's order is temp, footer, fsync, rename, fsync, unlink, and
                // `seal_open_segment` removes its own temporary file on every failure before
                // the rename. What is left for this caller is the handle and the ring entry.
                // The handle itself was consumed by `OpenSegment::seal`, so restoring it means
                // reopening the open file the refused seal left untouched.
                self.restore_after_failed_seal(&open_name, &error);
                return Err(error);
            }
        };
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

    /// Put the tier back together after a seal refused.
    ///
    /// What has to hold again is: the ring's entry describes the file that exists, and the
    /// tier's handle is that file's. The entry is found by `open_name`, never by
    /// `find(|s| s.open)`, so a stale claim cannot be updated instead. When the open file
    /// cannot be reopened the entry is dropped from the ring and reported, because an entry
    /// claiming to be open with no handle behind it is the shape that made the next append
    /// update the wrong file (`pi-hostobs-m1c` review, M2).
    ///
    /// The temporary file of a failed seal is **not** this function's business: it is created
    /// by [`StoreDir::seal_open_segment_until`], which removes its own copy on every failure
    /// before the rename, and a `.tmp` this call did not create is not a file it may delete.
    fn restore_after_failed_seal(&mut self, open_name: &str, error: &StoreError) {
        let reopened = OpenSegment::open(
            &self.dir,
            open_name,
            self.config.kind,
            self.max_file_bytes,
            self.config.max_incarnations,
        );
        match reopened {
            Ok(handle) => {
                let len = handle.len();
                if let Some(entry) = self
                    .scan
                    .segments
                    .iter_mut()
                    .find(|segment| segment.name == open_name)
                {
                    entry.open = true;
                    entry.file_len = len;
                    entry.records = handle.record_count();
                    entry.incarnations = handle.incarnations().clone();
                }
                self.open = Some(handle);
            }
            Err(_) => {
                // The file this handle wrote cannot be reopened (it was removed, or its tail
                // changed under us). The ring must not keep claiming it is open: drop the
                // entry, recompute the total from what the ring still holds, and report it.
                if let Some(position) = self
                    .scan
                    .segments
                    .iter()
                    .position(|segment| segment.name == open_name)
                {
                    let len = self.scan.segments[position].file_len;
                    self.scan.segments.remove(position);
                    self.bytes = self.bytes.saturating_sub(len);
                }
                self.open = None;
                self.scan.notes.push(TierNote {
                    name: open_name.to_string(),
                    reason: format!(
                        "seal refused ({}) and the open segment could not be reopened; its ring entry is dropped",
                        error.kind()
                    ),
                });
            }
        }
        self.scan.bytes = self.bytes;
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
    /// The live segment names removed, oldest first.
    pub segments: Vec<String>,
    /// The quarantined files (`*.corrupt`) removed, oldest first (HOB-43).
    pub quarantined: Vec<String>,
    /// The bytes they held, segments and quarantined files together.
    pub bytes: u64,
    /// Reports of entries the pass dropped without removing a file: one already gone, or one
    /// that could not be removed (a quarantined file that fails to unlink stays on disk,
    /// uncounted, and the next restart's scan adopts it again). They are not evictions, so
    /// they do not make [`Evicted::is_empty`] false (HOB-58).
    pub notes: Vec<TierNote>,
}

impl Evicted {
    /// How many live segments left (quarantined files are [`Evicted::quarantined`]).
    #[must_use]
    pub fn len(&self) -> usize {
        self.segments.len()
    }

    /// True when nothing was evicted: no segment and no quarantined file.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.segments.is_empty() && self.quarantined.is_empty()
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
    use super::{Tier, TierConfig, MAX_CAP_BYTES, MAX_EVICTION_NOTES};
    use crate::error::StoreError;
    use crate::format::{TierKind, HEADER_LEN, SEQ_NAME_MAX};
    use crate::fsio::testdir::TempDir;
    use crate::fsio::StoreDir;
    use crate::queue::Job;
    use crate::segment::OpenSegment;
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
    fn a_reopened_event_segment_starts_a_new_header_for_a_new_epoch_in_the_same_bucket() {
        let temp = TempDir::new("epoch-reopen");
        let config = TierConfig::new("events", TierKind::Events, 60, 3600, 1 << 20, false)
            .expect("config");
        let event_frame = || {
            hostobs_proto::event::encode_event_frame(&hostobs_proto::event::EventPayload {
                t_mono_ns: 10,
                span: 0,
                peer_dev: 0,
                peer_inode: 0,
                uid: 7,
                peer_pid: 0,
                kind: 3,
                outcome: 0,
                inferred: false,
                ctx: Vec::new(),
                attrs: Vec::new(),
                name: String::from("point"),
                key: String::from("point"),
                code: None,
                comm: None,
            })
            .expect("event/1 frame")
        };
        let first_name = {
            let mut first = Tier::open(
                config.clone(),
                StoreDir::open(temp.path(), false).expect("dir"),
                1 << 22,
            )
            .expect("first epoch tier");
            first
                .append(&Job::new(event_frame(), 10_000_000_000, Vec::new()), 7, 3)
                .expect("first epoch append");
            first
                .open_handle()
                .expect("open handle")
                .name()
                .to_string()
        };
        assert_eq!(first_name, "events-0-0.seg.open");

        let mut reopened = Tier::open(
            config,
            StoreDir::open(temp.path(), false).expect("dir"),
            1 << 22,
        )
        .expect("second epoch tier");
        assert_eq!(
            reopened
                .open_handle()
                .expect("adopted open segment")
                .header()
                .epoch,
            7
        );
        let second = reopened
            .append(&Job::new(event_frame(), 20_000_000_000, Vec::new()), 8, 4)
            .expect("second epoch append");
        assert_eq!(second.segment, "events-0-1.seg.open");
        assert_eq!(reopened.scan().segments.len(), 2);

        let dir = StoreDir::open(temp.path(), false).expect("dir");
        let headers: Vec<(u64, u32)> = reopened
            .scan()
            .segments
            .iter()
            .map(|segment| {
                let scanned = crate::segment::scan(&dir, &segment.name, 1 << 22, 1024)
                    .expect("scan segment");
                (scanned.header.epoch, scanned.header.dict_generation)
            })
            .collect();
        assert_eq!(headers, vec![(7, 3), (8, 4)]);
        assert!(reopened.scan().segments[0].is_sealed());
        assert!(reopened.scan().segments[1].open);
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
    fn a_failed_seal_puts_the_ring_entry_and_the_handle_back_and_leads_no_temp_behind() {
        // The seal's order is temp, footer, fsync, rename, fsync, unlink. A seal that refuses
        // *before* the rename leaves the open file untouched, and the tier must be exactly as
        // it was: its handle, its ring entry, and no temporary name (review, M2).
        let temp = TempDir::new("seal-fail");
        let mut tier = tier(&temp, 3600, 0);
        tier.append(&job(0, 16), 1, 1).expect("append");
        let open_name = tier
            .open_handle()
            .map(|open| open.name().to_string())
            .expect("open");
        // A sealed name that already exists makes the seal refuse after it has copied nothing
        // (the check runs before the temp file is created).
        temp.write_file("hot-0-0.seg", b"a planted sealed name");
        let refused = tier.seal_open_segment();
        assert!(matches!(refused, Err(StoreError::ForeignEntry { .. })));
        assert!(
            tier.has_open(),
            "the handle is back: a refused seal leaves the segment openable"
        );
        let entry = tier
            .scan()
            .segments
            .iter()
            .find(|segment| segment.name == open_name)
            .expect("the ring still holds the open entry");
        assert!(entry.open, "the entry says open, and now it is true");
        assert_eq!(entry.records, 1);
        assert!(
            !temp.exists("hot-0-0.seg.tmp"),
            "a refused seal leaves no temporary file behind"
        );
        // A second record lands in the same open segment: the failed seal did not detach it.
        let next = tier.append(&job(1_000_000_000, 16), 1, 1).expect("append");
        assert_eq!(next.segment, open_name);
        assert_eq!(next.records, 2);
    }

    #[test]
    fn a_file_past_the_bound_is_quarantined_and_counted_not_skipped() {
        // A store-written segment can never be past its tier's own file bound; a file that is
        // is not this store's segment. It must not be *skipped* either: a skipped file sits
        // outside the ring and outside the byte total, so eviction would never reach it and the
        // disk would grow past the cap the configuration declared (review, B4).
        let temp = TempDir::new("oversized");
        let mut tier = tier(&temp, 3600, 0);
        tier.append(&job(0, 16), 1, 1).expect("append");
        tier.seal_open_segment().expect("seal");
        let mut bytes = temp.read_file("hot-0-0.seg");
        // Grow the file past the bound without touching its header: the header still says this
        // is this tier's corpus, so it is provably the store's own file.
        let bound = tier.config().segment_file_bound();
        let grow = usize::try_from(bound).expect("fits") + 1;
        bytes.resize(grow, 0xA5);
        temp.write_file("hot-0-0.seg", &bytes);
        let reopened = Tier::open(
            TierConfig::history("hot", 60, 3600, 0, false).expect("config"),
            StoreDir::open(temp.path(), false).expect("dir"),
            1 << 22,
        )
        .expect("tier");
        assert_eq!(reopened.scan().oversized, vec!["hot-0-0.seg".to_string()]);
        assert_eq!(reopened.scan().quarantined, vec!["hot-0-0.seg".to_string()]);
        assert!(reopened.scan().segments.is_empty());
        assert_eq!(
            reopened.bytes(),
            u64::try_from(grow).expect("fits"),
            "the oversized file is out of the ring but inside the byte total (HOB-43)"
        );
        assert_eq!(reopened.scan().quarantined_files.len(), 1);
        assert_eq!(
            reopened.scan().quarantined_files[0].name,
            "hot-0-0.seg.corrupt"
        );
        assert!(!temp.exists("hot-0-0.seg"));
        assert!(
            temp.exists("hot-0-0.seg.corrupt"),
            "the bytes are kept for an operator under the quarantine name"
        );
        assert!(
            reopened
                .scan()
                .notes
                .iter()
                .all(|note| !note.reason.contains("no header")),
            "the header was read, so the file was recognised as this tier's own"
        );
    }

    #[test]
    fn a_rescan_keeps_going_when_one_store_file_cannot_be_read() {
        // One unreadable segment must not brick the tier's open: the file is reported and left
        // where it is, and every other segment is still in the ring (review, M5).
        //
        // The lane runs the suite as an unprivileged uid, so `chmod 000` really does make the
        // open fail; a process with CAP_DAC_OVERRIDE could not reproduce it, which is a limit of
        // this test's environment and not of the behaviour it checks.
        let temp = TempDir::new("unreadable");
        let mut tier = tier(&temp, 3600, 0);
        tier.append(&job(0, 16), 1, 1).expect("append");
        tier.seal_open_segment().expect("seal");
        tier.append(&job(60_000_000_000, 16), 1, 1).expect("append");
        let open_name = tier
            .open_handle()
            .map(|open| open.name().to_string())
            .expect("open");
        drop(tier);
        temp.chmod("hot-0-0.seg", 0o000);
        let reopened = Tier::open(
            TierConfig::history("hot", 60, 3600, 0, false).expect("config"),
            StoreDir::open(temp.path(), false).expect("dir"),
            1 << 22,
        )
        .expect("one unreadable file must not make the tier unopenable");
        assert_eq!(reopened.scan().unreadable, vec!["hot-0-0.seg".to_string()]);
        assert!(
            reopened
                .scan()
                .notes
                .iter()
                .any(|note| note.name == "hot-0-0.seg" && note.reason.contains("could not")),
            "the name is reported, never silently dropped"
        );
        assert_eq!(
            reopened.scan().segments.len(),
            1,
            "the readable open segment is still adopted"
        );
        assert_eq!(reopened.scan().segments[0].name, open_name);
        assert!(reopened.has_open());
        // The unreadable name is untouched, not quarantined and not deleted.
        assert!(temp.exists("hot-0-0.seg"));
    }

    #[test]
    fn a_zero_filled_tail_is_an_unwritten_tail_not_a_corrupt_segment() {
        // A power cut on a size-extending filesystem extends the file and never writes the
        // record: everything after the last good record is zero. Treating that as corruption
        // quarantines a whole durable segment for the commonest crash shape, so it is reported
        // as an incomplete (unwritten) tail and repaired by truncation (review, M6).
        let temp = TempDir::new("zero-tail");
        let mut tier = tier(&temp, 3600, 0);
        tier.append(&job(0, 16), 1, 1).expect("append");
        let name = tier
            .open_handle()
            .map(|open| open.name().to_string())
            .expect("open");
        let good = tier.open_handle().map(OpenSegment::len).expect("len");
        drop(tier);
        // Extend the file with zeros, exactly as a size extension leaves it.
        let mut bytes = temp.read_file(&name);
        bytes.resize(usize::try_from(good).expect("fits") + 4096, 0);
        temp.write_file(&name, &bytes);
        let mut reopened = Tier::open(
            TierConfig::history("hot", 60, 3600, 0, false).expect("config"),
            StoreDir::open(temp.path(), false).expect("dir"),
            1 << 22,
        )
        .expect("tier");
        assert_eq!(reopened.scan().torn_repaired, vec![name.clone()]);
        assert!(
            reopened.scan().quarantined.is_empty(),
            "an unwritten tail is not corruption: the segment stays in service"
        );
        assert_eq!(reopened.scan().segments.len(), 1);
        assert_eq!(
            reopened.scan().segments[0].records,
            1,
            "the record the store completed is still there"
        );
        assert_eq!(
            temp.read_file(&name).len(),
            usize::try_from(good).expect("fits")
        );
        assert!(reopened.has_open(), "and the repaired segment is adopted");
        let next = reopened
            .append(&job(1_000_000_000, 16), 1, 1)
            .expect("append after repair");
        assert_eq!(next.records, 2);
    }

    #[test]
    fn a_clock_step_backwards_still_leaves_the_ring_oldest_first() {
        // `open_segment` used to push at the end of the ring, so a new segment whose bucket is
        // earlier than the last one's (a clock step) landed after it and the `keep_s` sweep —
        // which stops at the first segment still inside its window — would leave an expired
        // one alive until a restart sorted it (review, M1).
        let temp = TempDir::new("ring-order");
        let mut tier = tier(&temp, 300, 0);
        tier.append(&job(60_000_000_000, 16), 1, 1)
            .expect("append bucket 60");
        tier.append(&job(0, 16), 1, 1).expect("append bucket 0");
        assert_eq!(tier.scan().segments.len(), 2);
        assert_eq!(
            tier.oldest().map(|segment| segment.name.as_str()),
            Some("hot-0-1.seg.open"),
            "the ring is ordered at insertion, not only by the next restart"
        );
        assert_eq!(
            tier.newest().map(|segment| segment.name.as_str()),
            Some("hot-60-0.seg")
        );
        // And the sweep reaches the expired one without a restart: bucket 0's span ends at 60 s
        // and 300 s of retention closes at 360 s.
        let evicted = tier.evict(361_000_000_000).expect("evict");
        assert_eq!(evicted.segments, vec!["hot-0-1.seg.open".to_string()]);
        assert_eq!(tier.scan().segments.len(), 1);
    }

    #[test]
    fn files_and_directories_the_store_creates_are_owner_only() {
        // The store's own state is not world-readable: an open segment carries the probe's
        // sampled history, so it is 0600 and its directory 0700 rather than whatever the
        // caller's umask happens to be (review, M3).
        let temp = TempDir::new("modes");
        let root = temp.path().join("tier");
        let dir = StoreDir::open(&root, true).expect("create the tier directory");
        let mode_of = |path: &std::path::Path| {
            use std::os::unix::fs::PermissionsExt;
            std::fs::metadata(path).expect("stat").permissions().mode() & 0o777
        };
        assert_eq!(mode_of(&root), 0o700, "the tier directory is owner-only");
        dir.create("hot-0-0.seg.open", b"header")
            .expect("create a segment");
        assert_eq!(
            mode_of(&dir.path_of("hot-0-0.seg.open")),
            0o600,
            "a segment the store creates is owner read/write only"
        );
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
    fn a_stale_open_twin_of_a_sealed_segment_is_removed_never_counted_or_reopened() {
        // The seal's order is rename, fsync, unlink. A crash between the last two leaves the
        // sealed name and its `.seg.open` twin with the same (tier, start_s, seq). A scan that
        // adopted both would count the bytes twice, serve every record twice and append behind
        // a copy — so the sealed name wins and the twin is removed (review, B2).
        //
        // The state is produced by the seal's own fault-injection point rather than by hand:
        // `SealStop::AfterRename` stops exactly there, on a real directory.
        let temp = TempDir::new("stale-open");
        let dir = StoreDir::open(&temp.path().join("tier"), true).expect("open dir");
        let config = TierConfig::history("hot", 60, 3600, 0, false).expect("config");
        let header = crate::format::SegmentHeader {
            seq: 0,
            epoch: 7,
            dict_generation: 3,
            kind: TierKind::History,
            payload: crate::format::PAYLOAD_BLOCK,
            start_real_ns: 0,
            max_payload: config.max_payload,
            max_incarnations: config.max_incarnations,
        };
        let mut open = OpenSegment::create(&dir, "hot-0-0.seg.open", header).expect("create");
        open.append(0, &[1], &block(16)).expect("append");
        open.seal_until(
            &dir,
            "hot-0-0.seg",
            "hot-0-0.seg.tmp",
            config.segment_file_bound(),
            crate::fsio::SealStop::AfterRename,
        )
        .expect("the injected crash: sealed name durable, open name still present");
        assert!(temp.at("tier/hot-0-0.seg").exists());
        assert!(temp.at("tier/hot-0-0.seg.open").exists());
        let reopened = Tier::open(
            config,
            StoreDir::open(temp.path().join("tier").as_path(), false).expect("dir"),
            1 << 22,
        )
        .expect("tier");
        assert_eq!(
            reopened.scan().stale_open_removed,
            vec!["hot-0-0.seg.open".to_string()]
        );
        assert_eq!(
            reopened.scan().segments.len(),
            1,
            "the pair is one segment, never two"
        );
        assert_eq!(reopened.scan().segments[0].name, "hot-0-0.seg");
        assert!(!reopened.scan().segments[0].open);
        assert_eq!(
            reopened.bytes(),
            reopened.scan().segments[0].file_len,
            "the twin's bytes are not counted twice"
        );
        assert!(
            !reopened.has_open(),
            "the stale open name is never reopened for appending"
        );
        assert_eq!(
            reopened
                .range(0, 1_000_000_000, 8, 1 << 20)
                .expect("range")
                .len(),
            1,
            "and every record is served once, not twice"
        );
        assert!(!temp.at("tier/hot-0-0.seg.open").exists());
        // The sealed file holds the record plus the footer the seal appended: its head is the
        // record's payload, exactly the bytes the range answer returned.
        let sealed_bytes = temp.read_file("tier/hot-0-0.seg");
        let payload = reopened
            .range(0, 1_000_000_000, 8, 1 << 20)
            .expect("range")
            .payloads()[0]
            .to_vec();
        assert!(sealed_bytes
            .windows(payload.len())
            .any(|window| window == payload));
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
        assert_eq!(
            reopened.bytes(),
            u64::try_from(before).expect("fits"),
            "the quarantined bytes are the tier's disk use and count against its cap"
        );
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

    /// Three sealed segments `hot-0-0.seg`, `hot-60-1.seg` and `hot-120-2.seg`, one record
    /// each, then the tier is dropped (a restart is what rescans the directory).
    fn three_sealed(temp: &TempDir) {
        let mut tier = tier(temp, 3600, 0);
        for at in [0i64, 60_000_000_000, 120_000_000_000] {
            tier.append(&job(at, 16), 1, 1).expect("append");
        }
        tier.seal_open_segment().expect("seal the last");
        assert_eq!(tier.scan().segments.len(), 3);
    }

    /// Flip one body byte of the first record, so the segment's record checksum fails and a
    /// rescan quarantines it.
    fn damage(temp: &TempDir, name: &str) {
        let mut bytes = temp.read_file(name);
        bytes[HEADER_LEN + 48 + 8 + 1] ^= 0xFF;
        temp.write_file(name, &bytes);
    }

    #[test]
    fn quarantined_bytes_count_against_the_cap_and_go_before_any_live_segment() {
        // HOB-43: the cap bounds segments and quarantined files together, and a damage storm
        // must exhaust its own debris, oldest first, before it can cost one good segment.
        let temp = TempDir::new("q-cap");
        three_sealed(&temp);
        let one = u64::try_from(temp.read_file("hot-0-0.seg").len()).expect("fits");
        damage(&temp, "hot-0-0.seg");
        damage(&temp, "hot-60-1.seg");
        let mut tier = tier_with_cap(&temp, 3600, one * 2);
        assert_eq!(
            tier.scan()
                .quarantined_files
                .iter()
                .map(|file| file.name.as_str())
                .collect::<Vec<_>>(),
            vec!["hot-0-0.seg.corrupt", "hot-60-1.seg.corrupt"],
            "the debris is ordered by the (start_s, seq) its names keep"
        );
        assert_eq!(
            tier.bytes(),
            one * 3,
            "two quarantined files and one live segment"
        );
        assert_eq!(tier.scan().bytes, one * 3);
        assert_eq!(tier.scan().segments.len(), 1);
        // 3 files, cap for 2: the OLDEST quarantined file goes, no live segment does.
        let first = tier.evict(0).expect("evict");
        assert_eq!(first.quarantined, vec!["hot-0-0.seg.corrupt".to_string()]);
        assert!(
            first.segments.is_empty(),
            "good history outlives the debris"
        );
        assert_eq!(first.bytes, one);
        assert!(!first.is_empty());
        assert_eq!(first.len(), 0, "len counts live segments only");
        assert!(!temp.exists("hot-0-0.seg.corrupt"));
        assert!(temp.exists("hot-60-1.seg.corrupt"));
        assert!(temp.exists("hot-120-2.seg"));
        assert_eq!(tier.bytes(), one * 2);
        // A tighter cap takes the remaining debris, still before the live segment.
        let mut tighter = tier_with_cap(&temp, 3600, one);
        let second = tighter.evict(0).expect("evict");
        assert_eq!(second.quarantined, vec!["hot-60-1.seg.corrupt".to_string()]);
        assert!(second.segments.is_empty());
        assert_eq!(tighter.bytes(), one);
        assert!(temp.exists("hot-120-2.seg"));
        // Only when no debris is left does the cap reach the live segment.
        let mut strict = tier_with_cap(&temp, 3600, one - 1);
        let third = strict.evict(0).expect("evict");
        assert_eq!(third.segments, vec!["hot-120-2.seg".to_string()]);
        assert!(third.quarantined.is_empty());
        assert_eq!(strict.bytes(), 0);
    }

    #[test]
    fn debris_newer_than_live_history_is_still_evicted_first() {
        // The order is "quarantined first", not "oldest first overall": the newest file is the
        // debris here, and the two older live segments must survive the cap.
        let temp = TempDir::new("q-newer");
        three_sealed(&temp);
        let one = u64::try_from(temp.read_file("hot-0-0.seg").len()).expect("fits");
        damage(&temp, "hot-120-2.seg");
        let mut tier = tier_with_cap(&temp, 3600, one * 2);
        let evicted = tier.evict(0).expect("evict");
        assert_eq!(
            evicted.quarantined,
            vec!["hot-120-2.seg.corrupt".to_string()]
        );
        assert!(evicted.segments.is_empty());
        assert!(temp.exists("hot-0-0.seg") && temp.exists("hot-60-1.seg"));
        assert_eq!(tier.scan().segments.len(), 2);
    }

    #[test]
    fn keep_s_removes_a_quarantined_file_by_the_key_its_name_keeps() {
        let temp = TempDir::new("q-keep");
        three_sealed(&temp);
        damage(&temp, "hot-0-0.seg");
        let mut tier = tier_with_cap(&temp, 60, 0);
        // The span of `hot-0-0` ends at 60 s and 60 s of retention closes at 120 s; the
        // comparison is strict, as for a live segment.
        let early = tier.evict(119_999_999_999).expect("evict");
        assert!(early.quarantined.is_empty(), "still inside the window");
        assert!(temp.exists("hot-0-0.seg.corrupt"));
        let late = tier.evict(120_000_000_000).expect("evict");
        assert_eq!(late.quarantined, vec!["hot-0-0.seg.corrupt".to_string()]);
        assert!(!temp.exists("hot-0-0.seg.corrupt"));
        assert!(tier.scan().quarantined_files.is_empty());
        assert_eq!(
            tier.bytes(),
            tier.scan()
                .segments
                .iter()
                .map(|segment| segment.file_len)
                .sum::<u64>()
        );
    }

    #[test]
    fn a_quarantine_look_alike_is_reported_and_never_counted_or_removed() {
        // The store removes only what name and header prove is its own: none of these is a
        // quarantined file of this tier, so none is counted, evicted or deleted, even under a
        // cap that any counted byte would exceed.
        let temp = TempDir::new("q-foreign");
        // A real history segment of this tier, so two look-alikes carry a header that *is* this
        // tier's corpus: only their names disqualify them.
        let mut seed = tier(&temp, 3600, 0);
        seed.append(&job(0, 16), 1, 1).expect("append");
        seed.seal_open_segment().expect("seal");
        drop(seed);
        let valid = temp.read_file("hot-0-0.seg");
        temp.remove("hot-0-0.seg");
        temp.write_file("hot-5-5.seg.corrupt", b"no header at all");
        temp.write_file("hot-x-1.seg.corrupt", &valid);
        temp.write_file("other-0-0.seg.corrupt", &valid);
        temp.write_file("hot-6-6.seg.corrupt", EVENTS_FIXTURE);
        temp.mkdir("hot-7-7.seg.corrupt");
        let mut tier = tier_with_cap(&temp, 3600, 1);
        assert!(tier.scan().quarantined_files.is_empty());
        assert_eq!(tier.bytes(), 0);
        let mut names: Vec<&str> = tier
            .scan()
            .notes
            .iter()
            .map(|note| note.name.as_str())
            .collect();
        names.sort_unstable();
        assert_eq!(
            names,
            vec![
                "hot-5-5.seg.corrupt",
                "hot-6-6.seg.corrupt",
                "hot-7-7.seg.corrupt",
                "hot-x-1.seg.corrupt",
                "other-0-0.seg.corrupt",
            ],
            "every look-alike is reported"
        );
        // A directory is refused by its kind, before any header read: a read of it would fail
        // too and the name would still be reported, but as a file with no readable header.
        let directory = tier
            .scan()
            .notes
            .iter()
            .find(|note| note.name == "hot-7-7.seg.corrupt")
            .expect("the directory is reported");
        assert_eq!(directory.reason, "is a directory, not a regular file");
        let evicted = tier.evict(i64::MAX / 2).expect("evict");
        assert!(evicted.is_empty());
        for name in [
            "hot-5-5.seg.corrupt",
            "hot-x-1.seg.corrupt",
            "other-0-0.seg.corrupt",
            "hot-6-6.seg.corrupt",
        ] {
            assert!(temp.exists(name), "{name} must still be there");
        }
        assert!(temp.at("hot-7-7.seg.corrupt").is_dir());
    }

    #[test]
    fn a_sealed_name_that_lost_its_footer_is_quarantined_not_served() {
        // HOB-42 (a): the name `.seg` says sealed, so the footer that closes the records must
        // be there and verify. Without it the file is not sealed history.
        let temp = TempDir::new("no-footer");
        let mut tier = tier(&temp, 3600, 0);
        tier.append(&job(0, 16), 1, 1).expect("append");
        tier.seal_open_segment().expect("seal");
        tier.append(&job(60_000_000_000, 16), 1, 1).expect("append");
        drop(tier);
        let len = u64::try_from(temp.read_file("hot-0-0.seg").len()).expect("fits");
        temp.truncate(
            "hot-0-0.seg",
            len - u64::try_from(crate::format::FOOTER_LEN).expect("fits"),
        );
        let reopened = tier_with_cap(&temp, 3600, 0);
        assert_eq!(reopened.scan().quarantined, vec!["hot-0-0.seg".to_string()]);
        assert!(reopened.scan().torn_repaired.is_empty());
        assert!(temp.exists("hot-0-0.seg.corrupt") && !temp.exists("hot-0-0.seg"));
        let footer_note = reopened
            .scan()
            .notes
            .iter()
            .find(|note| note.name == "hot-0-0.seg")
            .expect("the refusal is reported by name");
        assert!(footer_note.reason.contains("sealed-without-footer"));
        assert_eq!(
            reopened.scan().segments.len(),
            1,
            "only the open segment is served"
        );
        assert!(reopened
            .range(0, 1_000_000_000, 8, 1 << 20)
            .expect("range")
            .is_empty());
        assert_eq!(
            reopened.scan().quarantined_files.len(),
            1,
            "and the debris is counted"
        );
    }

    #[test]
    fn a_truncated_sealed_file_is_quarantined_whole_not_repaired() {
        // A torn tail is the normal shape of an *open* segment. A sealed name is written by an
        // atomic copy, so a torn one is a file that is not what its name says.
        let temp = TempDir::new("torn-sealed");
        let mut tier = tier(&temp, 3600, 0);
        tier.append(&job(0, 16), 1, 1).expect("append");
        tier.seal_open_segment().expect("seal");
        drop(tier);
        let len = u64::try_from(temp.read_file("hot-0-0.seg").len()).expect("fits");
        temp.truncate("hot-0-0.seg", len - 72);
        let reopened = tier_with_cap(&temp, 3600, 0);
        assert_eq!(reopened.scan().quarantined, vec!["hot-0-0.seg".to_string()]);
        assert!(
            reopened.scan().torn_repaired.is_empty(),
            "never truncated into service"
        );
        assert!(reopened.scan().segments.is_empty());
        assert!(temp.exists("hot-0-0.seg.corrupt"));
    }

    #[test]
    fn a_header_that_disagrees_with_its_name_is_quarantined() {
        // HOB-42 (b): a file renamed by hand must not keep serving its header's identity.
        let temp = TempDir::new("identity");
        let mut tier = tier(&temp, 3600, 0);
        tier.append(&job(0, 16), 1, 1).expect("append");
        tier.seal_open_segment().expect("seal");
        tier.append(&job(60_000_000_000, 16), 1, 1).expect("append");
        drop(tier);
        let sealed = temp.read_file("hot-0-0.seg");
        let open = temp.read_file("hot-60-1.seg.open");
        // seq differs (header 0, name 7), start differs (header 0, name 120), and an open
        // name is held to the same rule (header seq 1, name 9).
        temp.write_file("hot-0-7.seg", &sealed);
        temp.write_file("hot-120-0.seg", &sealed);
        temp.write_file("hot-60-9.seg.open", &open);
        let reopened = tier_with_cap(&temp, 3600, 0);
        let mut quarantined = reopened.scan().quarantined.clone();
        quarantined.sort_unstable();
        assert_eq!(
            quarantined,
            vec![
                "hot-0-7.seg".to_string(),
                "hot-120-0.seg".to_string(),
                "hot-60-9.seg.open".to_string(),
            ]
        );
        let mut served: Vec<&str> = reopened
            .scan()
            .segments
            .iter()
            .map(|segment| segment.name.as_str())
            .collect();
        served.sort_unstable();
        assert_eq!(served, vec!["hot-0-0.seg", "hot-60-1.seg.open"]);
        for name in [
            "hot-0-7.seg.corrupt",
            "hot-120-0.seg.corrupt",
            "hot-60-9.seg.open.corrupt",
        ] {
            assert!(temp.exists(name), "{name} is kept for an operator");
        }
        let reasons: Vec<&str> = reopened
            .scan()
            .notes
            .iter()
            .filter(|note| note.reason.contains("segment-identity"))
            .map(|note| note.name.as_str())
            .collect();
        assert_eq!(reasons.len(), 3, "each refusal is reported with its kind");
    }

    #[test]
    fn a_sealed_segment_keeps_the_start_of_its_open_name_across_a_step_change() {
        // The seal must not re-derive the start from the header with the *current* step: a
        // restart with a different `segment_s` would then name the file `hot-0-1.seg` while
        // its header says 60 s, and the identity rule would quarantine good history.
        let temp = TempDir::new("step-change");
        let mut first = tier(&temp, 3600, 0);
        first
            .append(&job(65_000_000_000, 16), 1, 1)
            .expect("append");
        drop(first);
        let mut wider = Tier::open(
            TierConfig::history("hot", 120, 3600, 0, false).expect("config"),
            StoreDir::open(temp.path(), false).expect("dir"),
            1 << 22,
        )
        .expect("tier");
        assert!(wider.has_open());
        let sealed = wider.seal_open_segment().expect("seal");
        assert_eq!(sealed.as_deref(), Some("hot-60-0.seg"));
        drop(wider);
        let reopened = tier_with_cap(&temp, 3600, 0);
        assert!(reopened.scan().quarantined.is_empty());
        assert_eq!(reopened.scan().segments.len(), 1);
        assert_eq!(reopened.scan().segments[0].name, "hot-60-0.seg");
    }

    #[test]
    fn a_quarantined_file_deleted_by_hand_does_not_fail_every_later_append() {
        // The documented flow keeps a quarantined file for an operator, who may delete it. The
        // entry must then leave the list and its bytes the total, quietly: a head entry whose
        // unlink fails would make every `evict`, and so every `append` after its record was
        // written, return an error, and a retry would store the record twice.
        let temp = TempDir::new("q-by-hand");
        three_sealed(&temp);
        damage(&temp, "hot-0-0.seg");
        let mut tier = tier_with_cap(&temp, 60, 0);
        assert_eq!(tier.scan().quarantined_files.len(), 1);
        temp.remove("hot-0-0.seg.corrupt");
        // Sixty seconds of retention are long past at 200 s, so `keep_s` reaches the entry.
        let outcome = tier
            .append(&job(200_000_000_000, 16), 1, 1)
            .expect("the append must not fail on a file that is already gone");
        assert!(
            outcome.evicted.quarantined.is_empty(),
            "nothing was removed, so nothing is reported as evicted"
        );
        assert!(tier.scan().quarantined_files.is_empty());
        let held: u64 = tier.scan().segments.iter().map(|s| s.file_len).sum();
        assert_eq!(
            tier.bytes(),
            held,
            "no byte of the vanished file stays in the total"
        );
        let gone_note = tier
            .scan()
            .notes
            .iter()
            .find(|note| note.name == "hot-0-0.seg.corrupt")
            .expect("the vanished file is reported by name");
        assert!(gone_note.reason.contains("already gone"));
        // HOB-58: the caller of `append` sees the same report, not only `scan.notes`.
        assert_eq!(outcome.evicted.notes.len(), 1);
        assert_eq!(outcome.evicted.notes[0], *gone_note);
        // The cap path takes the same way out: a tight cap and a debris file deleted by hand.
        let temp = TempDir::new("q-by-hand-cap");
        three_sealed(&temp);
        let one = u64::try_from(temp.read_file("hot-0-0.seg").len()).expect("fits");
        damage(&temp, "hot-0-0.seg");
        let mut capped = tier_with_cap(&temp, 3600, one * 2);
        temp.remove("hot-0-0.seg.corrupt");
        let evicted = capped.evict(0).expect("the cap loop must make progress");
        assert!(evicted.is_empty(), "not an eviction");
        assert_eq!(evicted.notes.len(), 1);
        assert!(evicted.notes[0].reason.contains("already gone"));
        assert!(capped.scan().quarantined_files.is_empty());
        assert_eq!(capped.bytes(), one * 2);
    }

    #[test]
    fn a_live_segment_deleted_by_hand_leaves_the_ring_without_failing_the_cap() {
        let temp = TempDir::new("seg-by-hand");
        three_sealed(&temp);
        let one = u64::try_from(temp.read_file("hot-0-0.seg").len()).expect("fits");
        let mut tier = tier_with_cap(&temp, 3600, one * 2);
        temp.remove("hot-0-0.seg");
        let evicted = tier.evict(0).expect("a vanished head must not fail");
        assert!(evicted.is_empty(), "nothing was removed");
        assert_eq!(evicted.notes.len(), 1);
        assert_eq!(evicted.notes[0].name, "hot-0-0.seg");
        assert!(evicted.notes[0].reason.contains("already gone"));
        assert_eq!(tier.scan().segments.len(), 2);
        assert_eq!(tier.bytes(), one * 2);
    }

    #[test]
    fn an_open_name_that_carries_a_footer_is_quarantined_not_adopted() {
        // A closed file under `.seg.open` cannot be reopened for appending; adopting it as the
        // open segment would make `Tier::open` fail as a whole on `reopen_open_segment`.
        let temp = TempDir::new("open-footer");
        three_sealed(&temp);
        let closed = temp.read_file("hot-60-1.seg");
        temp.remove("hot-60-1.seg");
        temp.write_file("hot-60-1.seg.open", &closed);
        let reopened = tier_with_cap(&temp, 3600, 0);
        assert_eq!(
            reopened.scan().quarantined,
            vec!["hot-60-1.seg.open".to_string()]
        );
        assert!(!reopened.has_open());
        assert!(temp.exists("hot-60-1.seg.open.corrupt"));
        assert_eq!(reopened.scan().segments.len(), 2);
        let note = reopened
            .scan()
            .notes
            .iter()
            .find(|note| note.name == "hot-60-1.seg.open")
            .expect("reported by name");
        assert!(note.reason.contains("open-with-footer"));
    }

    #[test]
    fn a_name_never_comes_back_after_a_quarantine_and_a_restart() {
        // Run 1 leaves an open segment with a corrupt tail. Run 2 quarantines it; the next
        // segment must not reuse its sequence, or it would crash corrupt under the same name
        // and run 3's quarantine would be refused by the existing `.corrupt` destination.
        let temp = TempDir::new("seq-water");
        let mut first = tier(&temp, 3600, 0);
        first.append(&job(0, 16), 1, 1).expect("append");
        drop(first);
        damage(&temp, "hot-0-0.seg.open");
        let second = tier_with_cap(&temp, 3600, 0);
        assert_eq!(
            second.scan().quarantined,
            vec!["hot-0-0.seg.open".to_string()]
        );
        assert_eq!(
            second.next_seq(),
            1,
            "the quarantined name holds its sequence"
        );
        // With the damaged open file renamed away, the quarantined file alone holds the name.
        drop(second);
        let mut second = tier_with_cap(&temp, 3600, 0);
        assert_eq!(second.next_seq(), 1, "the quarantined file alone holds it");
        let next = second.append(&job(0, 16), 1, 1).expect("append");
        assert_eq!(next.segment, "hot-0-1.seg.open");
        drop(second);
        damage(&temp, "hot-0-1.seg.open");
        let mut third = tier_with_cap(&temp, 3600, 0);
        assert_eq!(
            third.scan().quarantined,
            vec!["hot-0-1.seg.open".to_string()]
        );
        assert!(temp.exists("hot-0-0.seg.open.corrupt"));
        assert!(temp.exists("hot-0-1.seg.open.corrupt"));
        let last = third.append(&job(0, 16), 1, 1).expect("append works");
        assert_eq!(last.segment, "hot-0-2.seg.open");
    }

    #[test]
    fn a_name_above_the_sequence_bound_is_a_look_alike_and_never_poisons_the_water_mark() {
        // HOB-55: `hot-0-18446744073709551615.seg` must not set the water mark to u64::MAX
        // before its owner is proved, or every `open_segment` would fail until the file is
        // removed. A sequence above `SEQ_NAME_MAX` is not a name this store writes, in any of
        // the four spellings, and is treated like every other look-alike.
        let temp = TempDir::new("seq-lookalike");
        three_sealed(&temp);
        let valid = temp.read_file("hot-0-0.seg");
        let above = SEQ_NAME_MAX + 1;
        let mut planted: Vec<String> = Vec::new();
        for seq in [u64::MAX, above] {
            for suffix in [".seg", ".seg.open", ".seg.corrupt", ".seg.open.corrupt"] {
                planted.push(format!("hot-0-{seq}{suffix}"));
            }
        }
        for name in &planted {
            temp.write_file(name, &valid);
        }
        let mut tier = tier_with_cap(&temp, 3600, 0);
        assert_eq!(tier.next_seq(), 3, "only the real segments hold sequences");
        let mut noted: Vec<&str> = tier
            .scan()
            .notes
            .iter()
            .map(|note| note.name.as_str())
            .collect();
        noted.sort_unstable();
        let mut expected: Vec<&str> = planted.iter().map(String::as_str).collect();
        expected.sort_unstable();
        assert_eq!(
            noted, expected,
            "every look-alike is reported, none adopted"
        );
        assert!(tier.scan().quarantined_files.is_empty());
        assert_eq!(tier.scan().segments.len(), 3);
        let held: u64 = tier.scan().segments.iter().map(|s| s.file_len).sum();
        assert_eq!(tier.bytes(), held, "no look-alike is counted");
        let outcome = tier
            .append(&job(300_000_000_000, 16), 1, 1)
            .expect("appends still work");
        assert_eq!(outcome.segment, "hot-300-3.seg.open");
        let evicted = tier.evict(i64::MAX / 2).expect("evict");
        assert!(!evicted.is_empty(), "the real history is still evicted");
        for name in &planted {
            assert!(temp.exists(name), "{name} is never deleted");
        }
    }

    /// Plant `hot-0-<seq>.seg.open` holding one record, with a header that agrees with its
    /// name: a segment this tier proves its own when it scans the directory.
    fn plant_open_segment(temp: &TempDir, seq: u64) {
        let dir = StoreDir::open(temp.path(), false).expect("dir");
        let config = TierConfig::history("hot", 60, 3600, 0, false).expect("config");
        let header = crate::format::SegmentHeader {
            seq,
            epoch: 7,
            dict_generation: 3,
            kind: TierKind::History,
            payload: crate::format::PAYLOAD_BLOCK,
            start_real_ns: 0,
            max_payload: config.max_payload,
            max_incarnations: config.max_incarnations,
        };
        let name = format!("hot-0-{seq}.seg.open");
        let mut open = OpenSegment::create(&dir, &name, header).expect("create");
        open.append(0, &[1], &block(16)).expect("append");
    }

    #[test]
    fn an_unproven_name_at_the_sequence_bound_never_wedges_the_tier() {
        // HOB-55: a look-alike at `SEQ_NAME_MAX` whose bytes are not this tier's segment
        // reserves at most `SEQ_NAME_MAX - 1`, so the next `open_segment` succeeds (it mints
        // the bound itself) and only the one after is the typed overflow error.
        let temp = TempDir::new("seq-unproven");
        three_sealed(&temp);
        let at_bound = format!("hot-0-{SEQ_NAME_MAX}");
        temp.write_file(&format!("{at_bound}.seg"), b"not a segment");
        temp.write_file(&format!("{at_bound}.seg.corrupt"), b"not a segment");
        temp.write_file("hot-0-9.seg", b"not a segment");
        let mut tier = tier_with_cap(&temp, 3600, 0);
        assert_eq!(tier.scan().segments.len(), 3);
        // Capped at the bound minus one, so the bound itself is still free to mint.
        assert_eq!(tier.next_seq(), SEQ_NAME_MAX);
        let minted = tier
            .append(&job(300_000_000_000, 16), 1, 1)
            .expect("the tier is not wedged");
        assert_eq!(minted.segment, format!("hot-300-{SEQ_NAME_MAX}.seg.open"));
        let refused = tier.append(&job(360_000_000_000, 16), 1, 1);
        assert_eq!(refused.map(|_| ()), Err(StoreError::LengthOverflow));
        for name in [
            format!("{at_bound}.seg"),
            format!("{at_bound}.seg.corrupt"),
            "hot-0-9.seg".to_string(),
        ] {
            assert!(temp.exists(&name), "{name} is never deleted");
        }
    }

    #[test]
    fn an_unproven_name_still_reserves_its_own_sequence_below_the_bound() {
        let temp = TempDir::new("seq-reserve");
        three_sealed(&temp);
        temp.write_file("hot-0-9.seg", b"not a segment");
        let tier = tier_with_cap(&temp, 3600, 0);
        assert_eq!(tier.next_seq(), 10, "a name never comes back");
    }

    #[test]
    fn the_bound_is_minted_once_from_a_tier_whose_scan_saw_the_bound_minus_one() {
        // `open_segment` refuses only a sequence ABOVE `SEQ_NAME_MAX`: the bound itself is
        // minted, then the next one is the typed overflow error.
        let temp = TempDir::new("seq-mint");
        plant_open_segment(&temp, SEQ_NAME_MAX - 1);
        let mut tier = tier_with_cap(&temp, 3600, 0);
        assert!(tier.has_open());
        assert_eq!(tier.next_seq(), SEQ_NAME_MAX);
        let minted = tier
            .append(&job(65_000_000_000, 16), 1, 1)
            .expect("the bound itself is minted");
        assert_eq!(minted.segment, format!("hot-60-{SEQ_NAME_MAX}.seg.open"));
        assert_eq!(tier.next_seq(), SEQ_NAME_MAX + 1);
        let refused = tier.append(&job(125_000_000_000, 16), 1, 1);
        assert_eq!(refused.map(|_| ()), Err(StoreError::LengthOverflow));
    }

    #[test]
    fn eviction_reports_in_the_scan_notes_are_capped_and_drop_the_oldest() {
        // HOB-58: more vanished files than the cap. Every report is in the pass's own
        // `Evicted::notes`, `scan.notes` keeps the newest `MAX_EVICTION_NOTES`.
        let temp = TempDir::new("notes-cap");
        let vanished = u64::try_from(MAX_EVICTION_NOTES).expect("fits") + 5;
        let mut seed = tier(&temp, 1_000_000, 0);
        for index in 0..=vanished {
            let at = i64::try_from(index * 60).expect("fits") * 1_000_000_000;
            seed.append(&job(at, 16), 1, 1).expect("append");
        }
        drop(seed);
        // The reopened tier holds every segment in its ring; the files vanish afterwards.
        let mut tier = tier_with_cap(&temp, 1_000_000, 1);
        assert!(tier.scan().notes.is_empty());
        for index in 0..vanished {
            temp.remove(&format!("hot-{}-{index}.seg", index * 60));
        }
        let evicted = tier.evict(0).expect("every vanished head is dropped");
        // The pass reports every one of them.
        let expected = usize::try_from(vanished).expect("fits");
        assert_eq!(evicted.notes.len(), expected);
        let kept = &tier.scan().notes;
        assert_eq!(kept.len(), MAX_EVICTION_NOTES);
        // The oldest five are dropped, the newest is kept.
        assert_eq!(kept[0].name, "hot-300-5.seg");
        let newest = vanished - 1;
        let want = format!("hot-{}-{newest}.seg", newest * 60);
        let last = kept.last().expect("a report");
        assert_eq!(last.name, want);
    }

    #[test]
    fn a_name_at_the_sequence_bound_is_adopted_and_the_next_segment_is_refused() {
        // The bound itself is a sequence this store may have minted: it is adopted, and the
        // sequence after it is legitimate exhaustion, the typed overflow error.
        let temp = TempDir::new("seq-bound");
        plant_open_segment(&temp, SEQ_NAME_MAX);
        let mut tier = tier_with_cap(&temp, 3600, 0);
        assert!(tier.has_open(), "the name at the bound is ours");
        assert!(tier.scan().notes.is_empty());
        assert_eq!(tier.next_seq(), SEQ_NAME_MAX + 1);
        tier.append(&job(10_000_000_000, 16), 1, 1)
            .expect("the open segment still takes records");
        let refused = tier.append(&job(65_000_000_000, 16), 1, 1);
        assert_eq!(refused.map(|_| ()), Err(StoreError::LengthOverflow));
        assert_eq!(tier.next_seq(), SEQ_NAME_MAX + 1, "the mark does not move");
        assert_eq!(tier.scan().segments.len(), 1);
    }

    #[test]
    fn a_quarantined_file_that_cannot_be_removed_is_reported_to_the_caller() {
        // HOB-58: the report reaches the caller of `evict`, not only `scan.notes`. A link in
        // the file's place is refused by `StoreDir::remove`, so the entry is dropped from the
        // total and reported, and the file stays on disk.
        let temp = TempDir::new("q-unremovable");
        three_sealed(&temp);
        let one = u64::try_from(temp.read_file("hot-0-0.seg").len()).expect("fits");
        damage(&temp, "hot-0-0.seg");
        let mut capped = tier_with_cap(&temp, 3600, one * 2);
        assert_eq!(capped.scan().quarantined_files.len(), 1);
        temp.remove("hot-0-0.seg.corrupt");
        temp.symlink_to("hot-0-0.seg.corrupt", temp.path());
        let evicted = capped.evict(0).expect("the cap loop must make progress");
        assert!(evicted.is_empty(), "nothing was removed");
        assert_eq!(evicted.bytes, 0);
        assert_eq!(evicted.notes.len(), 1);
        assert_eq!(evicted.notes[0].name, "hot-0-0.seg.corrupt");
        assert!(evicted.notes[0].reason.contains("could not be removed"));
        assert_eq!(capped.scan().notes.last(), evicted.notes.first());
        assert_eq!(capped.bytes(), one * 2, "dropped from the total");
        assert!(temp.exists("hot-0-0.seg.corrupt"), "the file stays on disk");
    }
}
