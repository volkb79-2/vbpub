//! One segment file: open, append, seal, and the scanner that decides whether a file on disk
//! is readable data or a tail a crash left behind.
//!
//! # The tear, and why it is a *report* rather than a `try`
//!
//! A crash between "the bytes reached the file" and "the record was complete" leaves a
//! segment whose last record is short. Nothing about the file says so; the only evidence is
//! that the record's declared length does not fit. So [`scan`] walks the file one record at
//! a time and stops at the first record that does not hold, reporting
//! [`TailState::Incomplete`] (the bytes stop early) or [`TailState::Corrupt`] (the bytes are
//! there but do not checksum, or a record is present after a non-record):
//!
//! * a **`Incomplete`** tail is what a writer that died mid-append leaves. The good records
//!   before it are complete and readable, so the repair is [`SegmentScan::truncate_to_good_end`]
//!   — the file keeps every byte that is a record.
//! * a **`Corrupt`** tail is not a tear: a checksum failed, which means the bytes that are
//!   there were not written by this store (a bad sector, a foreign file, a hand edit). The
//!   repair is [`SegmentScan::quarantine`], which renames the whole file to a name a reader
//!   never scans, so nothing is lost and nothing corrupt is served. The report names the
//!   file, the offset and the refusal.
//! * **Nothing is silently dropped** in either case: the scan returns the reason, the tier
//!   counts it ([`crate::tier::TierScan`]), and the store's status exposes the counter.
//!
//! # Why an open segment has a different name
//!
//! An open segment is `<tier>-<start_s>-<seq>.seg.open`; a sealed one is `.seg`. The name is
//! the seal: a reader never treats an open file as history, an open file is never evicted
//! (it is being written), and a crash leaves an unambiguous `.seg.open` whose leading records
//! are still readable. Sealing is atomic (`crate::fsio::StoreDir::seal_open_segment`): temp
//! file, fsync, rename, fsync the directory, and only then remove the open name.

use std::collections::BTreeSet;

use crate::crc::crc32;
use crate::error::StoreError;
use crate::format::{
    check_payload, Footer, RecordHeader, SegmentFileName, SegmentHeader, FOOTER_LEN, FOOTER_MAGIC,
    HEADER_LEN,
};
use crate::fsio::{Appender, StoreDir};

/// The largest number of records one scan will walk, so a file of tiny records cannot make a
/// scan cost unbounded work.
pub const SCAN_RECORD_BOUND: usize = 1 << 20;

/// One record's facts, with the payload the scan read for it.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ScannedRecord {
    /// The record's sequence number within the segment.
    pub seq: u64,
    /// Real time of the block's end.
    pub real_ns: i64,
    /// The incarnation ids the record's body names.
    pub incarnations: Vec<u64>,
    /// The payload (a `proto` BLOCK frame, byte-identical to what was written).
    pub payload: Vec<u8>,
}

/// What a scan found at the end of a segment.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum TailState {
    /// The file ends exactly after its last record (or after a verified footer).
    Complete,
    /// The bytes stop before the last record does: a writer died mid-append.
    Incomplete {
        /// The offset at which the incomplete record begins.
        offset: u64,
    },
    /// The bytes are there but do not hold: a checksum failed or a record follows a non-record.
    Corrupt {
        /// The offset at which the unreadable record begins.
        offset: u64,
        /// The refusal the scan reached there.
        reason: StoreError,
    },
}

impl TailState {
    /// True when the segment needs a repair before it is appended to or read whole.
    #[must_use]
    pub fn is_damaged(&self) -> bool {
        !matches!(self, TailState::Complete)
    }
}

/// The result of scanning one segment file.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SegmentScan {
    /// The name scanned.
    pub name: String,
    /// The file's header.
    pub header: SegmentHeader,
    /// Every complete record, in order.
    pub records: Vec<ScannedRecord>,
    /// The offset just after the last complete record (where the tail begins).
    pub good_end: u64,
    /// The file's length in bytes.
    pub file_len: u64,
    /// The footer, when the file is sealed.
    pub footer: Option<Footer>,
    /// What the file ends with.
    pub tail: TailState,
    /// The ids any record in this segment names (for dictionary reachability).
    pub incarnations: BTreeSet<u64>,
    /// True when the scan stopped at [`SCAN_RECORD_BOUND`] instead of the file's end.
    pub record_bound_reached: bool,
}

impl SegmentScan {
    /// True when the segment is sealed (it carries a verified footer).
    #[must_use]
    pub const fn is_sealed(&self) -> bool {
        self.footer.is_some()
    }

    /// The real time of the first record, or `None` for an empty segment.
    #[must_use]
    pub fn first_real_ns(&self) -> Option<i64> {
        self.records.first().map(|record| record.real_ns)
    }

    /// The real time of the last record, or `None`.
    #[must_use]
    pub fn last_real_ns(&self) -> Option<i64> {
        self.records.last().map(|record| record.real_ns)
    }

    /// Truncate an incomplete tail back to `good_end`, leaving every complete record in place.
    ///
    /// # Errors
    ///
    /// [`StoreError::Config`] when the tail is not [`TailState::Incomplete`] (a corrupt tail
    /// must be quarantined, not truncated: truncating it would keep the corrupt records that
    /// precede the failure inside a segment that still looks readable).
    pub fn truncate_to_good_end(&self, dir: &StoreDir) -> Result<u64, StoreError> {
        match self.tail {
            TailState::Incomplete { .. } => {
                dir.truncate(&self.name, self.good_end)?;
                Ok(self.good_end)
            }
            _ => Err(StoreError::Config {
                key: "tail-repair",
                reason: "only an incomplete tail is truncated; a corrupt one is quarantined",
            }),
        }
    }

    /// Quarantine a corrupt file by renaming it out of the name a reader scans.
    ///
    /// # Errors
    ///
    /// [`StoreError::Config`] when the tail is not [`TailState::Corrupt`].
    pub fn quarantine(&self, dir: &StoreDir, quarantine_name: &str) -> Result<(), StoreError> {
        match self.tail {
            TailState::Corrupt { .. } => dir.rename(&self.name, quarantine_name),
            _ => Err(StoreError::Config {
                key: "tail-repair",
                reason: "only a corrupt segment is quarantined",
            }),
        }
    }

    /// The blocks whose real time falls in `[from, to]`, in segment order, bounded by
    /// `max_records` and `max_bytes`.
    ///
    /// Time filtering happens on each record's own real time, so a segment that overlaps the
    /// window only partly costs one comparison per record — no payload is copied for a record
    /// outside the window.
    ///
    /// # Errors
    ///
    /// [`StoreError::RangeInverted`] for `to < from` and [`StoreError::QueryLimit`] for a
    /// `max_records` of zero.
    pub fn range(
        &self,
        from: i64,
        to: i64,
        max_records: usize,
        max_bytes: usize,
    ) -> Result<SegmentRange, StoreError> {
        if to < from {
            return Err(StoreError::RangeInverted { from, to });
        }
        if max_records == 0 {
            return Err(StoreError::QueryLimit { asked: 0, max: 0 });
        }
        let mut answer = SegmentRange::default();
        let mut bytes = 0usize;
        for record in &self.records {
            if record.real_ns < from || record.real_ns > to {
                continue;
            }
            if answer.records.len() >= max_records {
                answer.truncated = true;
                break;
            }
            let add = record.payload.len().saturating_add(64);
            if bytes.saturating_add(add) > max_bytes {
                answer.truncated = true;
                break;
            }
            bytes = bytes.saturating_add(add);
            answer.records.push(record.clone());
        }
        Ok(answer)
    }
}

/// The records of one segment inside a time range.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct SegmentRange {
    /// The records in the window, in segment order.
    pub records: Vec<ScannedRecord>,
    /// True when a bound stopped the answer before the window ended.
    pub truncated: bool,
}

/// An open segment: a header already on disk, an append handle, and its running facts.
#[derive(Debug)]
pub struct OpenSegment {
    name: String,
    header: SegmentHeader,
    appender: Appender,
    record_count: u64,
    first_real_ns: Option<i64>,
    last_real_ns: Option<i64>,
    incarnations: BTreeSet<u64>,
}

impl OpenSegment {
    /// Create a new open segment: write its header, fsync, and hold it for appending.
    ///
    /// # Errors
    ///
    /// [`StoreError::ForeignEntry`] when the name exists already (the store never overwrites a
    /// name it did not create), and every header/IO refusal.
    pub fn create(
        dir: &StoreDir,
        name: &str,
        header: SegmentHeader,
    ) -> Result<OpenSegment, StoreError> {
        let header_bytes = header.encode()?;
        dir.create(name, &header_bytes)?;
        let appender = dir.open_appender(name)?;
        Ok(OpenSegment {
            name: name.to_string(),
            header,
            appender,
            record_count: 0,
            first_real_ns: None,
            last_real_ns: None,
            incarnations: BTreeSet::new(),
        })
    }

    /// Reopen an existing open segment for appending, continuing its sequence numbering.
    ///
    /// The file is scanned first: a repair must happen **before** anything is appended behind
    /// a torn or corrupt tail (appending behind a tear is how a segment becomes unreadable
    /// from its middle onward). The scan's refusal is returned, so the caller repairs and then
    /// reopens — this function never repairs silently.
    ///
    /// # Errors
    ///
    /// Every scan refusal, [`StoreError::Config`] when the header's kind is not the expected
    /// one, [`StoreError::Truncated`] for a file with no header.
    pub fn open(
        dir: &StoreDir,
        name: &str,
        expected_kind: crate::format::TierKind,
        max_bytes: usize,
        max_incarnations: u32,
    ) -> Result<OpenSegment, StoreError> {
        let scan = scan(dir, name, max_bytes, max_incarnations)?;
        if scan.header.kind != expected_kind {
            return Err(StoreError::TierKindMismatch {
                tier: expected_kind.name(),
                payload: scan.header.kind.name(),
            });
        }
        if scan.is_sealed() {
            return Err(StoreError::Config {
                key: "segment-name",
                reason: "a sealed segment is not an open one",
            });
        }
        if scan.tail.is_damaged() {
            return Err(match scan.tail {
                TailState::Incomplete { offset } => StoreError::Truncated {
                    context: "open segment tail",
                    need: usize::try_from(offset).unwrap_or(usize::MAX),
                    have: usize::try_from(scan.good_end).unwrap_or(usize::MAX),
                },
                TailState::Corrupt { reason, .. } => reason,
                TailState::Complete => StoreError::NotSealed,
            });
        }
        let appender = dir.open_appender(name)?;
        Ok(OpenSegment {
            name: name.to_string(),
            header: scan.header,
            appender,
            record_count: u64::try_from(scan.records.len())
                .map_err(|_| StoreError::LengthOverflow)?,
            first_real_ns: scan.first_real_ns(),
            last_real_ns: scan.last_real_ns(),
            incarnations: scan.incarnations,
        })
    }

    /// The file's name.
    #[must_use]
    pub fn name(&self) -> &str {
        &self.name
    }

    /// The header it was created with.
    #[must_use]
    pub const fn header(&self) -> &SegmentHeader {
        &self.header
    }

    /// How many records the segment holds.
    #[must_use]
    pub const fn record_count(&self) -> u64 {
        self.record_count
    }

    /// The ids this segment names.
    #[must_use]
    pub const fn incarnations(&self) -> &BTreeSet<u64> {
        &self.incarnations
    }

    /// The last record's real time.
    #[must_use]
    pub const fn last_real_ns(&self) -> Option<i64> {
        self.last_real_ns
    }

    /// Append one payload, with the incarnation ids its block names.
    ///
    /// The payload is checked as the `proto` frame its corpus requires before anything is
    /// written, so a frame of the other kind can never reach the file; the record kind written
    /// is the segment header's own, so a history segment cannot hold event records. A failure rolls the file back to the length it had
    /// before this call, so a failed append never leaves a half record behind — the caller
    /// counts the loss and moves on.
    ///
    /// # Errors
    ///
    /// [`StoreError::PayloadFrameType`]/[`StoreError::PayloadTooLarge`] for a bad payload,
    /// [`StoreError::EpochMismatch`]/[`StoreError::GenerationMismatch`] for a payload whose
    /// frame header disagrees with the segment, and every IO refusal.
    pub fn append(
        &mut self,
        real_ns: i64,
        incarnations: &[u64],
        payload: &[u8],
    ) -> Result<u64, StoreError> {
        check_payload(payload, self.header.kind, self.header.max_payload)?;
        let record = RecordHeader::encode_record(
            self.record_count,
            self.header.kind.record_kind(),
            real_ns,
            incarnations,
            payload,
            self.header.max_payload,
            self.header.max_incarnations,
        )?;
        let start_len = self.appender.len();
        match self.appender.append(&record) {
            Ok(_) => {}
            Err(error) => {
                // Roll back to the length before this record, so the next append cannot land
                // behind a partial one. A rollback that itself fails means the segment can no
                // longer be appended to; the error still reports the original cause and the
                // caller reopens a fresh segment.
                let _ = self.appender.truncate_to(start_len);
                return Err(error);
            }
        }
        self.record_count = self
            .record_count
            .checked_add(1)
            .ok_or(StoreError::LengthOverflow)?;
        if self.first_real_ns.is_none() {
            self.first_real_ns = Some(real_ns);
        }
        self.last_real_ns = Some(real_ns);
        for inc in incarnations {
            self.incarnations.insert(*inc);
        }
        Ok(self.record_count)
    }

    /// fsync what has been appended.
    ///
    /// # Errors
    ///
    /// [`StoreError::Io`].
    pub fn sync(&mut self) -> Result<(), StoreError> {
        self.appender.sync()
    }

    /// The file's length after every append this handle completed.
    #[must_use]
    pub const fn len(&self) -> u64 {
        self.appender.len()
    }

    /// True when the file holds its header but no record yet.
    #[must_use]
    pub const fn is_empty(&self) -> bool {
        self.record_count == 0
    }

    /// Seal the segment: an atomic copy with a footer, then the open name disappears.
    ///
    /// `max_bytes` bounds the copy (the tier's own `segment_bytes`), so a file that grew past
    /// its tier's bound is refused rather than duplicated into a trusted name.
    ///
    /// # Errors
    ///
    /// [`StoreError::Io`], [`StoreError::ForeignEntry`] and [`StoreError::FileTooLarge`].
    pub fn seal(
        mut self,
        dir: &StoreDir,
        sealed_name: &str,
        temp_name: &str,
        max_bytes: u64,
    ) -> Result<SealReport, StoreError> {
        self.appender.sync()?;
        let footer = Footer {
            seq: self.header.seq,
            record_count: self.record_count,
            end_offset: self.appender.len(),
            dict_generation: self.header.dict_generation,
            first_real_ns: self.first_real_ns.unwrap_or(0),
            last_real_ns: self.last_real_ns.unwrap_or(0),
        };
        let footer_bytes = footer.encode();
        dir.seal_open_segment(&self.name, sealed_name, temp_name, &footer_bytes, max_bytes)?;
        Ok(SealReport {
            sealed: sealed_name.to_string(),
            records: self.record_count,
            first_real_ns: self.first_real_ns,
            last_real_ns: self.last_real_ns,
        })
    }
}

/// What a seal produced.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SealReport {
    /// The sealed name.
    pub sealed: String,
    /// How many records it holds.
    pub records: u64,
    /// The first record's real time.
    pub first_real_ns: Option<i64>,
    /// The last record's real time.
    pub last_real_ns: Option<i64>,
}

/// Scan one segment file completely, recording what it holds and what its tail looks like.
///
/// The whole file is read inside `max_bytes` (a segment is bounded by its tier's
/// `segment_bytes`, so a query never needs more memory than one segment), and the walk stops
/// at [`SCAN_RECORD_BOUND`] records.
///
/// # Errors
///
/// [`StoreError::FileTooLarge`], [`StoreError::Symlink`], [`StoreError::Truncated`] (no
/// header), every header refusal, and [`StoreError::Io`]. A damaged *tail* is not an error:
/// it is the scan's [`SegmentScan::tail`].
pub fn scan(
    dir: &StoreDir,
    name: &str,
    max_bytes: usize,
    max_incarnations: u32,
) -> Result<SegmentScan, StoreError> {
    let bytes = dir.read(name, max_bytes)?;
    scan_bytes(name, &bytes, max_incarnations)
}

/// Scan one segment's bytes, without a directory: the entry point a fixture test, an
/// importer or an evidence tool uses.
///
/// [`scan`] is this function plus the bounded read, so a scan of a fixture and a scan of a
/// store take the identical path through the format. Nothing here allocates beyond what the
/// file already holds, and nothing panics on what it finds.
///
/// # Errors
///
/// [`StoreError::Truncated`] (no header), every header refusal, and
/// [`StoreError::LengthOverflow`]. A damaged *tail* is not an error: it is the returned
/// scan's [`SegmentScan::tail`].
pub fn scan_bytes(
    name: &str,
    bytes: &[u8],
    max_incarnations: u32,
) -> Result<SegmentScan, StoreError> {
    let file_len = u64::try_from(bytes.len()).map_err(|_| StoreError::LengthOverflow)?;
    let header_bytes = bytes.get(..HEADER_LEN).ok_or(StoreError::Truncated {
        context: "segment header",
        need: HEADER_LEN,
        have: bytes.len(),
    })?;
    let header = SegmentHeader::check_bytes(header_bytes)?;
    let mut position = HEADER_LEN;
    let mut records: Vec<ScannedRecord> = Vec::new();
    let mut incarnations: BTreeSet<u64> = BTreeSet::new();
    let mut footer: Option<Footer> = None;
    let mut tail = TailState::Complete;
    let mut record_bound_reached = false;
    loop {
        let remaining = bytes.len().saturating_sub(position);
        if remaining == 0 {
            break;
        }
        let at = bytes.get(position..).ok_or(StoreError::Truncated {
            context: "segment body",
            need: position,
            have: bytes.len(),
        })?;
        // A footer is the last 64 bytes before the end and starts with its magic. Anything
        // else with that magic in the middle is not a footer, and is left to the record
        // walk below (which refuses a non-record immediately).
        if remaining == FOOTER_LEN && at.starts_with(&FOOTER_MAGIC) {
            let index = records.len();
            let offset = u64::try_from(position).map_err(|_| StoreError::LengthOverflow)?;
            let measured = u64::try_from(records.len()).map_err(|_| StoreError::LengthOverflow)?;
            match Footer::check_bytes(at, &header, measured, offset) {
                Ok(checked) => {
                    footer = Some(checked);
                    position = bytes.len();
                    break;
                }
                Err(reason) => {
                    let _ = index;
                    tail = TailState::Corrupt { offset, reason };
                    break;
                }
            }
        }
        if records.len() >= SCAN_RECORD_BOUND {
            record_bound_reached = true;
            break;
        }
        let index = records.len();
        let offset = u64::try_from(position).map_err(|_| StoreError::LengthOverflow)?;
        match RecordHeader::check_bytes(at, index, max_incarnations) {
            Ok(record_header) => {
                // The record's kind must be the one this segment's corpus carries: a header
                // that says "history" may not hold an event record, and vice versa.
                if record_header.kind != header.kind.record_kind() {
                    tail = TailState::Corrupt {
                        offset,
                        reason: StoreError::RecordCorpus {
                            index,
                            tier: header.kind.name(),
                            expected: header.kind.record_kind(),
                            found: record_header.kind,
                        },
                    };
                    break;
                }
                let record_len = record_header.record_len()?;
                if record_len > remaining {
                    tail = TailState::Incomplete { offset };
                    break;
                }
                let record_end = position
                    .checked_add(record_len)
                    .ok_or(StoreError::LengthOverflow)?;
                let record = bytes
                    .get(position..record_end)
                    .ok_or(StoreError::Truncated {
                        context: "record",
                        need: record_end,
                        have: bytes.len(),
                    })?;
                match record_header.split_body(record, index) {
                    Ok((ids, payload)) => {
                        if record_header.seq
                            != u64::try_from(index).map_err(|_| StoreError::LengthOverflow)?
                        {
                            tail = TailState::Corrupt {
                                offset,
                                reason: StoreError::RecordSequence {
                                    index,
                                    expected: u64::try_from(index)
                                        .map_err(|_| StoreError::LengthOverflow)?,
                                    found: record_header.seq,
                                },
                            };
                            break;
                        }
                        match check_payload(payload, header.kind, header.max_payload) {
                            Ok(_) => {}
                            Err(reason) => {
                                tail = TailState::Corrupt { offset, reason };
                                break;
                            }
                        }
                        for inc in &ids {
                            incarnations.insert(*inc);
                        }
                        records.push(ScannedRecord {
                            seq: record_header.seq,
                            real_ns: record_header.real_ns,
                            incarnations: ids,
                            payload: payload.to_vec(),
                        });
                        position = record_end;
                    }
                    Err(reason) => {
                        tail = TailState::Corrupt { offset, reason };
                        break;
                    }
                }
            }
            Err(StoreError::RecordMagic { .. }) => {
                // A record that is not a record: either a tear that lost the record's first
                // bytes, or something else entirely. Both are damage, and both are reported
                // by their offset rather than by a guess about what was meant.
                tail = TailState::Corrupt {
                    offset,
                    reason: StoreError::RecordMagic {
                        index,
                        found: [0u8; 4],
                    },
                };
                break;
            }
            Err(StoreError::Truncated { .. }) => {
                tail = TailState::Incomplete { offset };
                break;
            }
            Err(reason) => {
                tail = TailState::Corrupt { offset, reason };
                break;
            }
        }
    }
    let good_end = u64::try_from(position).map_err(|_| StoreError::LengthOverflow)?;
    Ok(SegmentScan {
        name: name.to_string(),
        header,
        records,
        good_end,
        file_len,
        footer,
        tail,
        incarnations,
        record_bound_reached,
    })
}

/// The quarantine name a damaged segment is renamed to.
///
/// The name is derived from the segment's own name so the report can be traced back, and it
/// ends with `.corrupt`, which no scan matches: a quarantined file is kept for an operator to
/// inspect and is never served as data.
///
/// # Errors
///
/// [`StoreError::SegmentName`] for a name that is not a segment name.
pub fn quarantine_name(name: &str) -> Result<String, StoreError> {
    let parsed = SegmentFileName::parse(name)?;
    Ok(format!("{}.corrupt", parsed.file_name()))
}

/// A record's payload crc, for a caller that wants to verify a record it read itself.
///
/// # Must Use
///
/// The result must be compared with the record's declared checksum; ignoring it is a bug.
#[must_use]
pub fn payload_crc(payload: &[u8]) -> u32 {
    crc32(payload)
}

#[cfg(test)]
mod tests {
    use super::{quarantine_name, scan, scan_bytes, OpenSegment, TailState};
    use crate::error::StoreError;
    use crate::format::{
        TierKind, BLOCK_FRAME_TYPE, FOOTER_LEN, HEADER_LEN, PAYLOAD_BLOCK, RECORD_HEADER_LEN,
        SEGMENT_VERSION,
    };
    use crate::fsio::testdir::TempDir;
    use crate::fsio::StoreDir;

    fn header() -> crate::format::SegmentHeader {
        crate::format::SegmentHeader {
            seq: 1,
            epoch: 9,
            dict_generation: 4,
            kind: TierKind::History,
            payload: PAYLOAD_BLOCK,
            start_real_ns: 1_700_000_000_000_000_000,
            max_payload: 4096,
            max_incarnations: 64,
        }
    }

    fn block(rows: usize, fill: u8) -> Vec<u8> {
        // A minimal, self-consistent BLOCK frame: a 12-byte header (type BLOCK, the sectioned
        // flag, a zero reserved word, a correlation id), then the directory word and bodies
        // that the store never looks at, filled with `fill` so a test can tell two payloads
        // apart byte for byte.
        let len = 16 + rows * 8;
        let mut frame = vec![0u8; len];
        frame[0..4].copy_from_slice(&u32::try_from(len).expect("fits").to_le_bytes());
        frame[4] = crate::format::BLOCK_FRAME_TYPE;
        frame[5] = 1;
        frame[6..8].copy_from_slice(&0u16.to_le_bytes());
        frame[8..12].copy_from_slice(&7u32.to_le_bytes());
        for byte in frame.get_mut(12..).expect("body") {
            *byte = fill;
        }
        frame
    }

    fn segment(dir: &StoreDir, name: &str) -> OpenSegment {
        OpenSegment::create(dir, name, header()).expect("create segment")
    }

    #[test]
    fn a_fresh_segment_holds_its_header_and_appends_records_in_sequence() {
        let temp = TempDir::new("append");
        let dir = StoreDir::open(temp.path(), false).expect("open");
        let mut open = segment(&dir, "t-1-1.seg.open");
        assert_eq!(open.record_count(), 0);
        assert_eq!(open.last_real_ns(), None);
        assert_eq!(
            open.append(10, &[1, 2], &block(1, 0x11))
                .map_err(|e| e.kind()),
            Ok(1)
        );
        assert_eq!(
            open.append(20, &[2, 3], &block(2, 0x22))
                .map_err(|e| e.kind()),
            Ok(2)
        );
        assert_eq!(open.record_count(), 2);
        assert_eq!(open.last_real_ns(), Some(20));
        assert_eq!(
            open.incarnations().iter().copied().collect::<Vec<_>>(),
            vec![1, 2, 3]
        );
        // Record 1: 48-byte header + body (2 ids × 8 + a 24-byte block) = 40 bytes, already
        // eight-aligned. Record 2: 48 + (2 × 8 + a 32-byte block) = 48 bytes. Header 64.
        // 64 + 40 + 48 + 48 = 248.
        assert_eq!(
            open.len(),
            u64::try_from(
                HEADER_LEN + (RECORD_HEADER_LEN + 16 + 24) + (RECORD_HEADER_LEN + 16 + 32)
            )
            .unwrap_or(0)
        );
        assert_eq!(open.len(), 248);
        open.sync().expect("sync");
        let scanned = scan(&dir, "t-1-1.seg.open", 1 << 20, 64).expect("scan");
        assert_eq!(scanned.records.len(), 2);
        assert_eq!(scanned.first_real_ns(), Some(10));
        assert_eq!(scanned.last_real_ns(), Some(20));
        assert_eq!(scanned.records[0].incarnations, vec![1, 2]);
        assert_eq!(scanned.records[0].payload, block(1, 0x11));
        assert_eq!(scanned.header, header());
        assert_eq!(scanned.tail, TailState::Complete);
        assert!(!scanned.is_sealed());
        assert_eq!(scanned.good_end, scanned.file_len);
        assert_eq!(scanned.name, "t-1-1.seg.open");
    }

    #[test]
    fn sealing_produces_a_footer_a_reader_verifies_and_the_open_name_is_gone() {
        let temp = TempDir::new("seal");
        let dir = StoreDir::open(temp.path(), false).expect("open");
        let mut open = segment(&dir, "t-1-1.seg.open");
        open.append(10, &[1], &block(1, 0x11)).expect("append");
        open.append(20, &[2], &block(1, 0x22)).expect("append");
        let report = open
            .seal(&dir, "t-1-1.seg", "t-1-1.seg.tmp", 1 << 22)
            .expect("seal");
        assert_eq!(report.sealed, "t-1-1.seg");
        assert_eq!(report.records, 2);
        assert_eq!(report.first_real_ns, Some(10));
        assert_eq!(report.last_real_ns, Some(20));
        let scanned = scan(&dir, "t-1-1.seg", 1 << 20, 64).expect("scan sealed");
        assert!(scanned.is_sealed());
        assert_eq!(scanned.records.len(), 2);
        let footer = scanned.footer.expect("footer");
        assert_eq!(footer.record_count, 2);
        assert_eq!(footer.first_real_ns, 10);
        assert_eq!(footer.last_real_ns, 20);
        assert_eq!(footer.dict_generation, 4);
        assert_eq!(scanned.file_len, scanned.good_end);
        assert_eq!(dir.metadata("t-1-1.seg.open"), Ok(None));
        // Sealing is refused for a sealed file, and truncating a sealed file is not offered:
        // `scan` reports Complete for it, so truncate_to_good_end is a typed refusal.
        assert_eq!(
            scanned.truncate_to_good_end(&dir),
            Err(StoreError::Config {
                key: "tail-repair",
                reason: "only an incomplete tail is truncated; a corrupt one is quarantined"
            })
        );
        assert_eq!(
            scanned.quarantine(&dir, "t-1-1.seg.corrupt"),
            Err(StoreError::Config {
                key: "tail-repair",
                reason: "only a corrupt segment is quarantined"
            })
        );
        // A complete scan of a sealed file ends past the footer, so the footer begins exactly
        // 64 bytes before the end, which is where the footer itself says it begins.
        let footer_len = u64::try_from(FOOTER_LEN).unwrap_or(0);
        assert_eq!(
            scanned.file_len - footer.end_offset,
            footer_len,
            "the footer is exactly the last 64 bytes"
        );
        assert_eq!(scanned.good_end, scanned.file_len);
    }

    #[test]
    fn a_torn_tail_is_reported_and_truncated_to_the_last_good_record() {
        let temp = TempDir::new("torn");
        let dir = StoreDir::open(temp.path(), false).expect("open");
        let mut open = segment(&dir, "t-1-1.seg.open");
        open.append(10, &[1], &block(1, 0x11)).expect("append");
        open.append(20, &[2], &block(2, 0x22)).expect("append");
        let len = open.len();
        open.sync().expect("sync");
        drop(open);
        // Cut the file in the middle of the second record: what a killed writer leaves.
        dir.truncate("t-1-1.seg.open", len - 10).expect("truncate");
        let scanned = scan(&dir, "t-1-1.seg.open", 1 << 20, 64).expect("scan");
        assert_eq!(
            scanned.records.len(),
            1,
            "the complete record is still readable"
        );
        assert!(matches!(scanned.tail, TailState::Incomplete { .. }));
        assert!(scanned.tail.is_damaged());
        let good = scanned.good_end;
        let repaired = scanned.truncate_to_good_end(&dir).expect("repair");
        assert_eq!(repaired, good);
        let after = scan(&dir, "t-1-1.seg.open", 1 << 20, 64).expect("scan repaired");
        assert_eq!(after.tail, TailState::Complete);
        assert_eq!(after.records.len(), 1);
        assert_eq!(after.file_len, good);
        // And the same file can be reopened and appended to, continuing its sequence.
        let mut reopened =
            OpenSegment::open(&dir, "t-1-1.seg.open", TierKind::History, 1 << 20, 64)
                .expect("reopen");
        assert_eq!(reopened.record_count(), 1);
        reopened.append(30, &[3], &block(1, 0x33)).expect("append");
        let final_scan = scan(&dir, "t-1-1.seg.open", 1 << 20, 64).expect("scan");
        assert_eq!(final_scan.records.len(), 2);
        assert_eq!(final_scan.records[1].seq, 1);
    }

    #[test]
    fn an_open_segment_with_a_damaged_tail_refuses_to_open_and_repairs_are_typed() {
        let temp = TempDir::new("refuse");
        let dir = StoreDir::open(temp.path(), false).expect("open");
        let mut open = segment(&dir, "t-1-1.seg.open");
        open.append(10, &[1], &block(1, 0x11)).expect("append");
        let len = open.len();
        drop(open);
        dir.truncate("t-1-1.seg.open", len - 4).expect("truncate");
        assert_eq!(
            OpenSegment::open(&dir, "t-1-1.seg.open", TierKind::History, 1 << 20, 64).map(|_| ()),
            Err(StoreError::Truncated {
                context: "open segment tail",
                need: HEADER_LEN,
                have: HEADER_LEN,
            })
        );
        assert_eq!(
            OpenSegment::open(&dir, "t-1-1.seg.open", TierKind::Events, 1 << 20, 64).map(|_| ()),
            Err(StoreError::TierKindMismatch {
                tier: "events",
                payload: "history"
            })
        );
    }

    #[test]
    fn a_corrupt_tail_is_quarantined_rather_than_truncated() {
        let temp = TempDir::new("corrupt");
        let dir = StoreDir::open(temp.path(), false).expect("open");
        let mut open = segment(&dir, "t-1-1.seg.open");
        open.append(10, &[1], &block(1, 0x11)).expect("append");
        open.append(20, &[2], &block(1, 0x22)).expect("append");
        let len = open.len();
        drop(open);
        // Flip a byte in the *first* record's body: the header still checksums, the body does
        // not, so the scan stops at record 0 with a RecordCrc refusal.
        let bytes = dir.read("t-1-1.seg.open", 1 << 20).expect("read");
        let offset = HEADER_LEN + RECORD_HEADER_LEN + 8 + 4;
        let mut damaged = bytes.clone();
        damaged[offset] ^= 0xFF;
        temp.write_file("t-1-1.seg.open", &damaged);
        let scanned = scan(&dir, "t-1-1.seg.open", 1 << 20, 64).expect("scan");
        assert_eq!(scanned.records.len(), 0);
        match &scanned.tail {
            TailState::Corrupt { offset, reason } => {
                assert_eq!(*offset, u64::try_from(HEADER_LEN).unwrap_or(0));
                assert_eq!(reason.kind(), "record-crc");
            }
            other => panic!("expected a corrupt tail, got {other:?}"),
        }
        assert_eq!(
            scanned.truncate_to_good_end(&dir),
            Err(StoreError::Config {
                key: "tail-repair",
                reason: "only an incomplete tail is truncated; a corrupt one is quarantined"
            })
        );
        let name = quarantine_name("t-1-1.seg.open").expect("quarantine name");
        assert_eq!(name, "t-1-1.seg.open.corrupt");
        scanned.quarantine(&dir, &name).expect("quarantine");
        assert_eq!(dir.metadata("t-1-1.seg.open"), Ok(None));
        assert_eq!(dir.size_of(&name), Ok(Some(len)));
        // The quarantined file is not a segment name, so a scan of the tier never matches it.
        assert_eq!(
            crate::format::SegmentFileName::parse(&name).map(|_| ()),
            Err(StoreError::SegmentName { name })
        );
    }

    #[test]
    fn a_range_query_returns_only_the_window_and_stops_at_its_bounds() {
        let temp = TempDir::new("range");
        let dir = StoreDir::open(temp.path(), false).expect("open");
        let mut open = segment(&dir, "t-1-1.seg.open");
        for (index, time) in [10i64, 20, 30, 40, 50].iter().enumerate() {
            open.append(*time, &[u64::try_from(index).unwrap_or(0)], &block(1, 0x10))
                .expect("append");
        }
        drop(open);
        let scanned = scan(&dir, "t-1-1.seg.open", 1 << 20, 64).expect("scan");
        let answer = scanned.range(20, 40, 10, 1 << 20).expect("range");
        assert_eq!(
            answer.records.iter().map(|r| r.real_ns).collect::<Vec<_>>(),
            vec![20, 30, 40],
            "the window is inclusive at both ends and keeps segment order"
        );
        assert!(!answer.truncated);
        let bounded = scanned.range(0, 100, 2, 1 << 20).expect("range");
        assert_eq!(bounded.records.len(), 2);
        assert!(bounded.truncated);
        let no_bytes = scanned.range(0, 100, 10, 40).expect("range");
        assert!(no_bytes.truncated);
        assert!(no_bytes.records.is_empty());
        assert_eq!(
            scanned.range(50, 10, 10, 1 << 20),
            Err(StoreError::RangeInverted { from: 50, to: 10 })
        );
        assert_eq!(
            scanned.range(0, 100, 0, 1 << 20),
            Err(StoreError::QueryLimit { asked: 0, max: 0 })
        );
        let empty = scanned.range(100, 200, 10, 1 << 20).expect("range");
        assert!(empty.records.is_empty());
        assert!(!empty.truncated);
    }

    #[test]
    fn a_payload_that_is_not_a_block_is_refused_before_anything_is_written() {
        let temp = TempDir::new("payload");
        let dir = StoreDir::open(temp.path(), false).expect("open");
        let mut open = segment(&dir, "t-1-1.seg.open");
        let len = open.len();
        let mut not_a_block = block(1, 0x11);
        not_a_block[4] = 1;
        assert_eq!(
            open.append(10, &[1], &not_a_block),
            Err(StoreError::PayloadFrameType {
                expected: BLOCK_FRAME_TYPE,
                found: 1,
            })
        );
        assert_eq!(open.len(), len, "nothing was written");
        // A block whose frame header agrees with its own (oversized) length.
        let too_large = block(600, 0x11);
        assert_eq!(too_large.len(), 16 + 600 * 8);
        assert_eq!(
            open.append(10, &[1], &too_large),
            Err(StoreError::PayloadTooLarge {
                len: 16 + 600 * 8,
                max: 4096
            })
        );
        let mut too_many_ids = vec![1u64; 65];
        assert_eq!(
            open.append(10, &too_many_ids, &block(1, 0x11)),
            Err(StoreError::RecordIncarnations {
                index: 0,
                count: 65,
                max: 64
            })
        );
        too_many_ids.truncate(64);
        assert_eq!(open.append(10, &too_many_ids, &block(1, 0x11)), Ok(1));
    }

    #[test]
    fn a_segment_refuses_a_record_of_the_other_corpus() {
        // The segment header names the corpus, and the record kind must follow it: a history
        // segment may not hold an event record even though the record envelope alone would
        // accept that kind. Without this check a reader would hand a consumer an event where
        // it expects a column block.
        let temp = TempDir::new("corpus");
        let dir = StoreDir::open(temp.path(), false).expect("open");
        let mut open = segment(&dir, "t-1-1.seg.open");
        open.append(10, &[1], &block(1, 0x11)).expect("append");
        drop(open);
        let mut bytes = dir.read("t-1-1.seg.open", 1 << 20).expect("read");
        // Rewrite the first record's kind byte to the events kind and re-checksum its header.
        let second = HEADER_LEN;
        bytes[second + 16] = crate::format::RECORD_EVT;
        let crc = crate::crc::crc32(&bytes[second..second + 44]);
        bytes[second + 44..second + 48].copy_from_slice(&crc.to_le_bytes());
        let scanned = scan_bytes("t-1-1.seg.open", &bytes, 64).expect("scan");
        assert!(scanned.records.is_empty());
        match &scanned.tail {
            TailState::Corrupt { offset, reason } => {
                assert_eq!(*offset, HEADER_LEN as u64);
                assert_eq!(reason.kind(), "record-corpus");
                assert_eq!(
                    reason.to_string(),
                    "record 0 carries kind 2, but a history segment holds only 1"
                );
            }
            other => panic!("expected a corrupt tail, got {other:?}"),
        }
    }

    #[test]
    fn a_sequence_that_is_not_the_position_is_a_corrupt_tail() {
        let temp = TempDir::new("sequence");
        let dir = StoreDir::open(temp.path(), false).expect("open");
        let mut open = segment(&dir, "t-1-1.seg.open");
        open.append(10, &[1], &block(1, 0x11)).expect("append");
        open.append(20, &[2], &block(1, 0x22)).expect("append");
        drop(open);
        // Rewrite the second record's declared sequence to 9 and re-checksum its header, so
        // the header is valid but the position is wrong.
        let mut bytes = dir.read("t-1-1.seg.open", 1 << 20).expect("read");
        let second = HEADER_LEN + (RECORD_HEADER_LEN + 8 + 24);
        bytes[second + 8..second + 16].copy_from_slice(&9u64.to_le_bytes());
        let crc = crate::crc::crc32(&bytes[second..second + 44]);
        bytes[second + 44..second + 48].copy_from_slice(&crc.to_le_bytes());
        temp.write_file("t-1-1.seg.open", &bytes);
        let scanned = scan(&dir, "t-1-1.seg.open", 1 << 20, 64).expect("scan");
        assert_eq!(scanned.records.len(), 1);
        match &scanned.tail {
            TailState::Corrupt { reason, .. } => assert_eq!(reason.kind(), "record-sequence"),
            other => panic!("expected a corrupt tail, got {other:?}"),
        }
    }

    #[test]
    fn a_segment_without_a_header_or_past_its_bound_is_a_typed_refusal() {
        let temp = TempDir::new("bounds");
        let dir = StoreDir::open(temp.path(), false).expect("open");
        dir.create("empty.seg.open", b"").expect("create");
        assert_eq!(
            scan(&dir, "empty.seg.open", 1 << 20, 64).map(|_| ()),
            Err(StoreError::Truncated {
                context: "segment header",
                need: HEADER_LEN,
                have: 0
            })
        );
        dir.create("short.seg.open", &[0u8; HEADER_LEN - 4])
            .expect("create");
        assert_eq!(
            scan(&dir, "short.seg.open", 1 << 20, 64).map(|_| ()),
            Err(StoreError::Truncated {
                context: "segment header",
                need: HEADER_LEN,
                have: HEADER_LEN - 4
            })
        );
        assert_eq!(
            scan(&dir, "short.seg.open", 1, 64).map(|_| ()),
            Err(StoreError::FileTooLarge { len: 60, max: 1 })
        );
        assert_eq!(
            scan(&dir, "missing.seg.open", 1 << 20, 64).map(|_| ()),
            Err(StoreError::Io {
                op: "stat",
                os_code: Some(2),
                message: "No such file or directory (os error 2)".into()
            })
        );
        assert_eq!(
            scan(&dir, "../escape", 1 << 20, 64).map(|_| ()),
            Err(StoreError::BadName {
                name: "../escape".into()
            })
        );
        assert_eq!(SEGMENT_VERSION, 1);
    }

    #[test]
    fn a_sealed_segment_is_never_opened_for_appending() {
        let temp = TempDir::new("sealed");
        let dir = StoreDir::open(temp.path(), false).expect("open");
        let mut open = segment(&dir, "t-1-1.seg.open");
        open.append(10, &[1], &block(1, 0x11)).expect("append");
        open.seal(&dir, "t-1-1.seg", "t-1-1.seg.tmp", 1 << 22)
            .expect("seal");
        assert_eq!(
            OpenSegment::open(&dir, "t-1-1.seg", TierKind::History, 1 << 20, 64).map(|_| ()),
            Err(StoreError::Config {
                key: "segment-name",
                reason: "a sealed segment is not an open one"
            })
        );
        assert_eq!(
            quarantine_name("t-1-1.seg").map_err(|e| e.kind()),
            Ok("t-1-1.seg.corrupt".to_string())
        );
        assert_eq!(
            quarantine_name("notes.txt"),
            Err(StoreError::SegmentName {
                name: "notes.txt".into()
            })
        );
        assert_eq!(super::payload_crc(b"abc"), crate::crc::crc32(b"abc"));
    }

    #[test]
    fn an_append_rolls_back_a_failed_write() {
        let temp = TempDir::new("rollback");
        let dir = StoreDir::open(temp.path(), false).expect("open");
        let mut open = segment(&dir, "t-1-1.seg.open");
        open.append(10, &[1], &block(1, 0x11)).expect("append");
        let before = open.len();
        // A payload that passes the frame check but exceeds the declared maximum is refused
        // before any byte is written, so the rollback path itself is exercised by a payload
        // the header's bound rejects.
        let big = block(600, 0x11);
        assert!(open.append(20, &[2], &big).is_err());
        assert_eq!(open.len(), before);
        let scanned = scan(&dir, "t-1-1.seg.open", 1 << 20, 64).expect("scan");
        assert_eq!(scanned.records.len(), 1);
        assert_eq!(scanned.tail, TailState::Complete);
    }
}
