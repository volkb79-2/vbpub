//! The store's typed refusal vocabulary.
//!
//! Nothing in this crate panics on data it reads from disk, and nothing invents a value it
//! could not read: every failure is one variant here, carrying the facts an operator needs
//! to act on it. [`StoreError::kind`] is a stable machine name, and the independent Python
//! reference decoder (`python/reference/hostobs_segment_ref.py`) uses the same names for the
//! same conditions, so a format disagreement is a named difference rather than "it failed".
//!
//! Three groups exist on purpose and are never conflated:
//!
//! * **Format** refusals (`header-*`, `record-*`, `footer-*`, `payload-*`, `truncated`,
//!   `trailing-bytes`): the bytes are not a segment this version can read.
//! * **Physical** refusals (`io`, `symlink`, `not-a-directory`, `entry-limit`,
//!   `file-too-large`, `no-space`): the filesystem refused or the layout in the directory is
//!   not what the store created.
//! * **Contract** refusals (`unknown-incarnation`, `incarnation-reused`,
//!   `dictionary-full`, `config-*`, `range-inverted`, `job-too-large`): the caller asked for
//!   something the store's declared bounds or invariants forbid.

use core::fmt;

/// Why a read region could not be produced (a caller-supplied range past the buffer).
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct RegionOutOfBounds {
    /// The exclusive end the caller asked for.
    pub need: usize,
    /// The bytes actually available.
    pub have: usize,
}

/// The store's one error type.
///
/// Every variant is `Clone` and `Eq` where its fields allow it, so a test can compare
/// refusals exactly instead of matching a message. `Io` carries the operation, the errno
/// and a short message (an `std::io::Error` is neither `Clone` nor `Eq`, and a store must be
/// able to hold and report a failure after the call returns).
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum StoreError {
    /// A bounded read ran past the bytes that exist.
    Truncated {
        /// What was being read.
        context: &'static str,
        /// The bytes needed.
        need: usize,
        /// The bytes available.
        have: usize,
    },
    /// Bytes remain after the structure that should have ended the file.
    TrailingBytes {
        /// How many bytes are left.
        extra: usize,
    },
    /// A `usize`/`u64` computation would have wrapped.
    LengthOverflow,
    /// A region of a buffer does not exist.
    RegionOutOfBounds(RegionOutOfBounds),
    /// The segment header's magic is not this format's.
    HeaderMagic {
        /// The seven magic bytes found.
        found: [u8; 7],
    },
    /// The segment header's version is not one this codec reads.
    HeaderVersion {
        /// The version found.
        found: u16,
        /// The version implemented.
        supported: u16,
    },
    /// The segment header's checksum does not cover its own bytes.
    HeaderCrc {
        /// The checksum the header carries.
        declared: u32,
        /// The checksum of the bytes read.
        computed: u32,
    },
    /// A header or record field that this version requires to be zero or a fixed value.
    Reserved {
        /// The field's name.
        field: &'static str,
        /// The value found.
        found: u32,
    },
    /// A segment header declares a length other than this version's.
    HeaderLength {
        /// The declared length.
        declared: u32,
        /// The length this version writes.
        expected: u32,
    },
    /// A payload kind outside the closed vocabulary (`1` block, `2` event block).
    PayloadKind {
        /// The stored byte.
        found: u8,
    },
    /// A record's kind is not this segment's corpus kind: a history segment holding an
    /// event record, or the reverse. Distinct from [`StoreError::RecordKind`], which is a
    /// kind outside the vocabulary altogether.
    RecordCorpus {
        /// The record's index.
        index: usize,
        /// The corpus the segment header declares.
        tier: &'static str,
        /// The kind the corpus requires.
        expected: u8,
        /// The kind the record carries.
        found: u8,
    },
    /// A record's magic is not a record's.
    RecordMagic {
        /// The record's zero-based index in the segment.
        index: usize,
        /// The bytes found at the record's start.
        found: [u8; 4],
    },
    /// A record's own header checksum does not cover its bytes.
    RecordHeaderCrc {
        /// The record's index.
        index: usize,
        /// The checksum the record carries.
        declared: u32,
        /// The checksum of the bytes read.
        computed: u32,
    },
    /// A record's payload checksum does not cover its payload.
    RecordCrc {
        /// The record's index.
        index: usize,
        /// The checksum the record carries.
        declared: u32,
        /// The checksum of the payload read.
        computed: u32,
    },
    /// A record's sequence number is not the one this position implies.
    RecordSequence {
        /// The record's index.
        index: usize,
        /// The sequence the position implies.
        expected: u64,
        /// The sequence stored.
        found: u64,
    },
    /// A record declares a kind outside the closed vocabulary.
    RecordKind {
        /// The record's index.
        index: usize,
        /// The stored byte.
        found: u8,
    },
    /// A record declares more incarnation ids than the segment admits.
    RecordIncarnations {
        /// The record's index.
        index: usize,
        /// The declared count.
        count: usize,
        /// The bound.
        max: usize,
    },
    /// A record's padding is not the number this layout implies.
    RecordPadding {
        /// The record's index.
        index: usize,
        /// The declared padding.
        declared: u32,
        /// The padding this layout implies.
        expected: u32,
    },
    /// A record's declared payload length disagrees with the payload read.
    PayloadLength {
        /// The record's index.
        index: usize,
        /// The declared length.
        declared: usize,
        /// The length actually available.
        actual: usize,
    },
    /// A payload's frame type is not the one its segment's corpus requires.
    PayloadFrameType {
        /// The frame type the corpus requires (BLOCK 5 for history, EVT 3 for events).
        expected: u8,
        /// The frame type byte found (plan v4 §B.1).
        found: u8,
    },
    /// A payload's own frame header declares a length other than the payload's.
    PayloadFrameLength {
        /// The length the frame header declares.
        declared: u32,
        /// The payload length stored.
        have: usize,
    },
    /// A payload's frame header carries a flag this protocol version does not define.
    PayloadFrameFlags {
        /// The flags byte found.
        flags: u8,
    },
    /// A payload's frame header carries a non-zero reserved field.
    PayloadFrameReserved {
        /// The reserved value found.
        found: u16,
    },
    /// `proto` refused a payload frame header for a reason without a store-specific variant.
    PayloadFrameHeader {
        /// The codec's typed refusal.
        error: hostobs_proto::error::ProtoError,
    },
    /// A payload is larger than the store's configured bound.
    PayloadTooLarge {
        /// The payload's length.
        len: usize,
        /// The bound.
        max: usize,
    },
    /// The segment ends without a footer: it is open (never sealed).
    NotSealed,
    /// A sealed segment's footer magic is wrong.
    FooterMagic {
        /// The four bytes found.
        found: [u8; 4],
    },
    /// A sealed segment's footer checksum does not cover its bytes.
    FooterCrc {
        /// The checksum the footer carries.
        declared: u32,
        /// The checksum of the bytes read.
        computed: u32,
    },
    /// A footer's declared record count disagrees with the records before it.
    FooterCount {
        /// The declared count.
        declared: u64,
        /// The records actually present.
        measured: u64,
    },
    /// A footer's declared end offset is not where the footer begins.
    FooterOffset {
        /// The declared offset.
        declared: u64,
        /// The offset the records ended at.
        measured: u64,
    },
    /// A footer belongs to a different segment than the header it follows.
    FooterIdentity {
        /// The header's sequence number.
        header: u64,
        /// The footer's sequence number.
        footer: u64,
    },
    /// A footer's declared dictionary generation is not the header's.
    FooterGeneration {
        /// The header's generation.
        header: u32,
        /// The footer's generation.
        footer: u32,
    },
    /// A file named as a sealed segment (`.seg`) has no footer that verifies: it is not a
    /// sealed segment, whatever its name says. HOB-42.
    SealedWithoutFooter {
        /// The name that claims to be sealed.
        name: String,
    },
    /// A file named as an open segment (`.seg.open`) carries a verified footer: a closed file
    /// under a name that says it is still being appended to.
    OpenWithFooter {
        /// The name that claims to be open.
        name: String,
    },
    /// A segment header's `seq` or `start_real_ns` disagrees with the numbers in the file's
    /// name (a hand-renamed or swapped file). HOB-42.
    SegmentIdentity {
        /// Which fact disagrees: `seq` or `start_real_ns`.
        field: &'static str,
        /// The value the name carries (`start_s` is reported as nanoseconds).
        name: i128,
        /// The value the header carries.
        header: i128,
    },
    /// A directory entry's name is not one this store writes.
    SegmentName {
        /// The name refused.
        name: String,
    },
    /// A path component is not safe to join under the store's root.
    BadName {
        /// The name refused.
        name: String,
    },
    /// A file the store would open is a symbolic link. Links are never followed.
    Symlink {
        /// The name that is a link.
        name: String,
    },
    /// The configured directory is not a directory (or is not one it may be).
    NotADirectory {
        /// The path (the store's own root spelling, never a resolved path).
        path: String,
    },
    /// A directory cannot be listed inside the bound.
    EntryLimit {
        /// The bound.
        max: usize,
    },
    /// A file is larger than the bound of the read that would take it whole.
    FileTooLarge {
        /// The file's size.
        len: u64,
        /// The bound.
        max: usize,
    },
    /// The filesystem refused the operation.
    Io {
        /// What the store was doing (`open`, `append`, `sync`, `rename`, `truncate`, `list`,
        /// `read`, `create-dir`, `sync-dir`).
        op: &'static str,
        /// The errno, when the platform reported one.
        os_code: Option<i32>,
        /// The short system message.
        message: String,
    },
    /// The disk is full: a counted, reported condition, never a silent loss.
    NoSpace {
        /// What the store was doing.
        op: &'static str,
        /// The name written.
        name: String,
    },
    /// The mount table says the tier is not on tmpfs while `require_tmpfs` is set.
    NotTmpfs {
        /// The tier's name.
        tier: String,
        /// The filesystem type found.
        fstype: String,
    },
    /// The mount table could not be read, so `require_tmpfs` cannot be satisfied.
    MountTableUnreadable {
        /// Why not.
        reason: String,
    },
    /// No mount table entry covers the path, so its filesystem type is unknown.
    MountNotFound {
        /// The path asked about.
        path: String,
    },
    /// An appended frame names an epoch the open segment does not belong to.
    EpochMismatch {
        /// The open segment's epoch.
        segment: u64,
        /// The frame's epoch.
        frame: u64,
    },
    /// An appended frame names a dictionary generation the open segment does not hold.
    GenerationMismatch {
        /// The open segment's generation.
        segment: u32,
        /// The frame's generation.
        frame: u32,
    },
    /// A payload kind disagrees with the tier that would store it.
    TierKindMismatch {
        /// The tier's configured kind.
        tier: &'static str,
        /// The payload's kind.
        payload: &'static str,
    },
    /// The active incarnation index refused an id it has already retired.
    IncarnationReused {
        /// The epoch the id belongs to.
        epoch: u64,
        /// The id.
        inc: u64,
        /// The highest id this epoch has issued.
        highest: u64,
    },
    /// The active incarnation index is at its bound and holds nothing it may retire.
    DictionaryFull {
        /// The bound.
        max: usize,
        /// The live entries.
        live: usize,
    },
    /// An incarnation id is not in the active index.
    UnknownIncarnation {
        /// The epoch asked in.
        epoch: u64,
        /// The id asked for.
        inc: u64,
    },
    /// A segment would reference more incarnations than the tier admits.
    SegmentIncarnations {
        /// The segment's sequence number.
        seq: u64,
        /// The count the append would produce.
        count: usize,
        /// The bound.
        max: usize,
    },
    /// A job is larger than the entire writer queue, so it cannot be queued at all.
    JobTooLarge {
        /// The job's size.
        len: u64,
        /// The queue's bound.
        max: u64,
    },
    /// A time range's end precedes its start.
    RangeInverted {
        /// The start.
        from: i64,
        /// The end.
        to: i64,
    },
    /// A query asked for more records than the store will read in one answer.
    QueryLimit {
        /// The asked count.
        asked: u64,
        /// The bound.
        max: u64,
    },
    /// A time value that cannot be part of a segment name.
    NegativeTime {
        /// The value.
        ns: i64,
    },
    /// The caller's configuration is unusable.
    Config {
        /// The key.
        key: &'static str,
        /// Why it is unusable.
        reason: &'static str,
    },
    /// A tier name does not exist in this store.
    UnknownTier {
        /// The name asked for.
        name: String,
    },
    /// A tier name is declared twice.
    DuplicateTier {
        /// The name.
        name: String,
    },
    /// The store's directory holds a name it did not create, in a place it needed.
    ForeignEntry {
        /// The name.
        name: String,
        /// What the store found instead of its own file.
        found: &'static str,
    },
    /// A stored byte is outside the closed events vocabulary (plan v4 §C.4).
    EventVocabulary {
        /// The field's name.
        field: &'static str,
        /// The byte found.
        found: u8,
    },
    /// A value is outside the closed events vocabulary by *name* (plan v4 §C.4).
    EventVocabularyName {
        /// The field's name.
        field: &'static str,
        /// The value found.
        found: String,
    },
    /// An event or span field is longer than §C.4 allows.
    EventFieldTooLong {
        /// The field's name.
        field: &'static str,
        /// The bytes given.
        len: usize,
        /// The bound.
        max: usize,
    },
    /// An issuer or the whole table is at its open-span bound (plan v4 §C.4).
    SpanLimit {
        /// The issuer that hit the bound.
        issuer: String,
        /// The spans open against that bound.
        open: usize,
        /// The bound.
        max: usize,
        /// True when the *global* bound was hit rather than the per-issuer one.
        global: bool,
    },
    /// An `end` named a key no open span holds (plan v4 §C.4's `span-unknown`).
    SpanUnknown {
        /// The issuer.
        issuer: String,
        /// The key it asked to close.
        key: String,
    },
    /// A quality code outside the closed §C.2 vocabulary.
    QualityCode {
        /// The byte found.
        found: u8,
    },
    /// A store's in-RAM index holds an id it has not declared (an unknown dictionary entry a
    /// stored payload names).
    UnknownDictionaryEntry {
        /// The segment the record is in.
        segment: String,
        /// The record's index within it.
        index: usize,
        /// The id the record names.
        inc: u64,
    },
}

/// The errno value Linux reports for "no space left on device".
const ENOSPC: i32 = 28;

impl StoreError {
    /// A stable machine name, shared with the Python reference decoder.
    #[must_use]
    pub const fn kind(&self) -> &'static str {
        match self {
            StoreError::Truncated { .. } => "truncated",
            StoreError::TrailingBytes { .. } => "trailing-bytes",
            StoreError::LengthOverflow => "length-overflow",
            StoreError::RegionOutOfBounds(_) => "region-out-of-bounds",
            StoreError::HeaderMagic { .. } => "header-magic",
            StoreError::HeaderVersion { .. } => "header-version",
            StoreError::HeaderCrc { .. } => "header-crc",
            StoreError::Reserved { .. } => "reserved",
            StoreError::HeaderLength { .. } => "header-length",
            StoreError::PayloadKind { .. } => "payload-kind",
            StoreError::RecordMagic { .. } => "record-magic",
            StoreError::RecordCorpus { .. } => "record-corpus",
            StoreError::RecordHeaderCrc { .. } => "record-header-crc",
            StoreError::RecordCrc { .. } => "record-crc",
            StoreError::RecordSequence { .. } => "record-sequence",
            StoreError::RecordKind { .. } => "record-kind",
            StoreError::RecordIncarnations { .. } => "record-incarnations",
            StoreError::RecordPadding { .. } => "record-padding",
            StoreError::PayloadLength { .. } => "payload-length",
            StoreError::PayloadFrameType { .. } => "payload-frame-type",
            StoreError::PayloadFrameLength { .. } => "payload-frame-length",
            StoreError::PayloadFrameFlags { .. } => "payload-frame-flags",
            StoreError::PayloadFrameReserved { .. } => "payload-frame-reserved",
            StoreError::PayloadFrameHeader { .. } => "payload-frame-header",
            StoreError::PayloadTooLarge { .. } => "payload-too-large",
            StoreError::NotSealed => "not-sealed",
            StoreError::FooterMagic { .. } => "footer-magic",
            StoreError::FooterCrc { .. } => "footer-crc",
            StoreError::FooterCount { .. } => "footer-count",
            StoreError::FooterOffset { .. } => "footer-offset",
            StoreError::FooterIdentity { .. } => "footer-identity",
            StoreError::FooterGeneration { .. } => "footer-generation",
            StoreError::SealedWithoutFooter { .. } => "sealed-without-footer",
            StoreError::OpenWithFooter { .. } => "open-with-footer",
            StoreError::SegmentIdentity { .. } => "segment-identity",
            StoreError::SegmentName { .. } => "segment-name",
            StoreError::BadName { .. } => "bad-name",
            StoreError::Symlink { .. } => "symlink",
            StoreError::NotADirectory { .. } => "not-a-directory",
            StoreError::EntryLimit { .. } => "entry-limit",
            StoreError::FileTooLarge { .. } => "file-too-large",
            StoreError::Io { .. } => "io",
            StoreError::NoSpace { .. } => "no-space",
            StoreError::NotTmpfs { .. } => "not-tmpfs",
            StoreError::MountTableUnreadable { .. } => "mount-table-unreadable",
            StoreError::MountNotFound { .. } => "mount-not-found",
            StoreError::EpochMismatch { .. } => "epoch-mismatch",
            StoreError::GenerationMismatch { .. } => "generation-mismatch",
            StoreError::TierKindMismatch { .. } => "tier-kind-mismatch",
            StoreError::IncarnationReused { .. } => "incarnation-reused",
            StoreError::DictionaryFull { .. } => "dictionary-full",
            StoreError::UnknownIncarnation { .. } => "unknown-incarnation",
            StoreError::SegmentIncarnations { .. } => "segment-incarnations",
            StoreError::JobTooLarge { .. } => "job-too-large",
            StoreError::RangeInverted { .. } => "range-inverted",
            StoreError::QueryLimit { .. } => "query-limit",
            StoreError::NegativeTime { .. } => "negative-time",
            StoreError::Config { .. } => "config",
            StoreError::UnknownTier { .. } => "unknown-tier",
            StoreError::DuplicateTier { .. } => "duplicate-tier",
            StoreError::ForeignEntry { .. } => "foreign-entry",
            StoreError::EventVocabulary { .. } => "event-vocabulary",
            StoreError::EventVocabularyName { .. } => "event-vocabulary-name",
            StoreError::EventFieldTooLong { .. } => "event-field-too-long",
            StoreError::SpanLimit { .. } => "span-limit",
            StoreError::SpanUnknown { .. } => "span-unknown",
            StoreError::QualityCode { .. } => "quality-code",
            StoreError::UnknownDictionaryEntry { .. } => "unknown-dictionary-entry",
        }
    }

    /// True when the failure is a full disk (a counted, reported condition).
    #[must_use]
    pub const fn is_no_space(&self) -> bool {
        matches!(self, StoreError::NoSpace { .. })
    }

    /// True when the refusal is about the *bytes* rather than the caller's contract.
    #[must_use]
    pub const fn is_format(&self) -> bool {
        matches!(
            self,
            StoreError::Truncated { .. }
                | StoreError::TrailingBytes { .. }
                | StoreError::RegionOutOfBounds(_)
                | StoreError::HeaderMagic { .. }
                | StoreError::HeaderVersion { .. }
                | StoreError::HeaderCrc { .. }
                | StoreError::Reserved { .. }
                | StoreError::HeaderLength { .. }
                | StoreError::PayloadKind { .. }
                | StoreError::RecordMagic { .. }
                | StoreError::RecordCorpus { .. }
                | StoreError::RecordHeaderCrc { .. }
                | StoreError::RecordCrc { .. }
                | StoreError::RecordSequence { .. }
                | StoreError::RecordKind { .. }
                | StoreError::RecordIncarnations { .. }
                | StoreError::RecordPadding { .. }
                | StoreError::PayloadLength { .. }
                | StoreError::PayloadFrameType { .. }
                | StoreError::PayloadFrameLength { .. }
                | StoreError::PayloadFrameFlags { .. }
                | StoreError::PayloadFrameReserved { .. }
                | StoreError::PayloadFrameHeader { .. }
                | StoreError::NotSealed
                | StoreError::FooterMagic { .. }
                | StoreError::FooterCrc { .. }
                | StoreError::FooterCount { .. }
                | StoreError::FooterOffset { .. }
                | StoreError::FooterIdentity { .. }
                | StoreError::FooterGeneration { .. }
                | StoreError::SealedWithoutFooter { .. }
                | StoreError::OpenWithFooter { .. }
                | StoreError::SegmentIdentity { .. }
        )
    }

    /// Wrap an `std::io::Error` in the store's vocabulary, mapping a full disk to its own
    /// variant so a disk-full is a distinct, counted report rather than a generic `io`.
    #[must_use]
    pub fn io(op: &'static str, name: &str, error: &std::io::Error) -> StoreError {
        let os_code = error.raw_os_error();
        if os_code == Some(ENOSPC) {
            return StoreError::NoSpace {
                op,
                name: name.to_string(),
            };
        }
        StoreError::Io {
            op,
            os_code,
            message: error.to_string(),
        }
    }

    /// The errno, when this refusal came from the filesystem.
    #[must_use]
    pub const fn os_code(&self) -> Option<i32> {
        match self {
            StoreError::Io { os_code, .. } => *os_code,
            StoreError::NoSpace { .. } => Some(ENOSPC),
            _ => None,
        }
    }
}

/// A checked `end - start` for a buffer region, so a caller's arithmetic cannot wrap.
///
/// # Errors
///
/// [`StoreError::LengthOverflow`] when `start > end`, which no caller may turn into a
/// negative length.
pub fn length_overflow(start: usize, end: usize) -> Result<usize, StoreError> {
    end.checked_sub(start).ok_or(StoreError::LengthOverflow)
}

impl fmt::Display for StoreError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            StoreError::Truncated {
                context,
                need,
                have,
            } => write!(f, "{context} is truncated: needed {need} bytes, {have} present"),
            StoreError::TrailingBytes { extra } => {
                write!(f, "{extra} bytes remain after the last record")
            }
            StoreError::LengthOverflow => f.write_str("a length computation would overflow"),
            StoreError::RegionOutOfBounds(region) => write!(
                f,
                "region ends at {} but only {} bytes exist",
                region.need, region.have
            ),
            StoreError::HeaderMagic { found } => {
                write!(f, "segment header magic is {:02x?}, not HOBSEG", found)
            }
            StoreError::HeaderVersion { found, supported } => write!(
                f,
                "segment header version {found} is not {supported}"
            ),
            StoreError::HeaderCrc { declared, computed } => write!(
                f,
                "segment header crc {declared:#x} does not cover its bytes ({computed:#x})"
            ),
            StoreError::Reserved { field, found } => {
                write!(f, "{field} must be zero, found {found}")
            }
            StoreError::HeaderLength { declared, expected } => {
                write!(f, "segment header length {declared} is not {expected}")
            }
            StoreError::PayloadKind { found } => write!(f, "payload kind {found} is not defined"),
            StoreError::RecordMagic { index, found } => write!(
                f,
                "record {index} starts with {:02x?}, not HOBR or a footer",
                found
            ),
            StoreError::RecordHeaderCrc {
                index,
                declared,
                computed,
            } => write!(
                f,
                "record {index} header crc {declared:#x} does not cover its bytes ({computed:#x})"
            ),
            StoreError::RecordCrc {
                index,
                declared,
                computed,
            } => write!(
                f,
                "record {index} payload crc {declared:#x} does not cover its payload ({computed:#x})"
            ),
            StoreError::RecordSequence {
                index,
                expected,
                found,
            } => write!(
                f,
                "record {index} carries sequence {found}, expected {expected}"
            ),
            StoreError::RecordKind { index, found } => {
                write!(f, "record {index} carries kind {found}, which is not defined")
            }
            StoreError::RecordCorpus {
                index,
                tier,
                expected,
                found,
            } => write!(
                f,
                "record {index} carries kind {found}, but a {tier} segment holds only {expected}"
            ),
            StoreError::RecordIncarnations { index, count, max } => write!(
                f,
                "record {index} declares {count} incarnation ids, the bound is {max}"
            ),
            StoreError::RecordPadding {
                index,
                declared,
                expected,
            } => write!(
                f,
                "record {index} declares {declared} pad bytes, {expected} is implied"
            ),
            StoreError::PayloadLength {
                index,
                declared,
                actual,
            } => write!(
                f,
                "record {index} declares a {declared}-byte payload with {actual} present"
            ),
            StoreError::PayloadFrameType { expected, found } => write!(
                f,
                "payload frame type {found} is not the {expected} this segment's corpus requires"
            ),
            StoreError::PayloadFrameLength { declared, have } => write!(
                f,
                "payload frame header declares {declared} bytes, payload holds {have}"
            ),
            StoreError::PayloadFrameFlags { flags } => {
                write!(f, "payload frame flags {flags:#x} are not defined")
            }
            StoreError::PayloadFrameReserved { found } => {
                write!(f, "payload frame reserved field is {found}, not zero")
            }
            StoreError::PayloadFrameHeader { error } => {
                write!(f, "payload frame header refused by proto: {error}")
            }
            StoreError::PayloadTooLarge { len, max } => {
                write!(f, "payload of {len} bytes exceeds the bound of {max}")
            }
            StoreError::NotSealed => f.write_str("segment has no footer: it is open"),
            StoreError::FooterMagic { found } => {
                write!(f, "segment footer magic is {:02x?}, not HOBF", found)
            }
            StoreError::FooterCrc { declared, computed } => write!(
                f,
                "segment footer crc {declared:#x} does not cover its bytes ({computed:#x})"
            ),
            StoreError::FooterCount { declared, measured } => write!(
                f,
                "segment footer declares {declared} records, {measured} are present"
            ),
            StoreError::FooterOffset { declared, measured } => write!(
                f,
                "segment footer declares it begins at {declared}, records end at {measured}"
            ),
            StoreError::FooterIdentity { header, footer } => write!(
                f,
                "segment footer names sequence {footer}, the header names {header}"
            ),
            StoreError::FooterGeneration { header, footer } => write!(
                f,
                "segment footer names generation {footer}, the header names {header}"
            ),
            StoreError::SealedWithoutFooter { name } => write!(
                f,
                "{name} is named as a sealed segment but has no footer that verifies"
            ),
            StoreError::OpenWithFooter { name } => write!(
                f,
                "{name} is named as an open segment but carries a footer"
            ),
            StoreError::SegmentIdentity {
                field,
                name,
                header,
            } => write!(
                f,
                "segment name carries {field} {name}, its header carries {header}"
            ),
            StoreError::SegmentName { name } => {
                write!(f, "{name} is not a segment name this store writes")
            }
            StoreError::BadName { name } => write!(f, "{name} is not a safe path component"),
            StoreError::Symlink { name } => {
                write!(f, "{name} is a symbolic link; links are never followed")
            }
            StoreError::NotADirectory { path } => write!(f, "{path} is not a directory"),
            StoreError::EntryLimit { max } => {
                write!(f, "the directory holds more than {max} entries")
            }
            StoreError::FileTooLarge { len, max } => {
                write!(f, "file of {len} bytes exceeds the bound of {max}")
            }
            StoreError::Io {
                op,
                os_code,
                message,
            } => match os_code {
                Some(code) => write!(f, "{op} failed with errno {code}: {message}"),
                None => write!(f, "{op} failed: {message}"),
            },
            StoreError::NoSpace { op, name } => {
                write!(f, "{op} {name}: no space left on device")
            }
            StoreError::NotTmpfs { tier, fstype } => write!(
                f,
                "tier {tier} requires tmpfs and is on {fstype}"
            ),
            StoreError::MountTableUnreadable { reason } => {
                write!(f, "the mount table cannot be read: {reason}")
            }
            StoreError::MountNotFound { path } => {
                write!(f, "no mount entry covers {path}, so its filesystem is unknown")
            }
            StoreError::EpochMismatch { segment, frame } => write!(
                f,
                "frame epoch {frame} differs from the open segment's epoch {segment}"
            ),
            StoreError::GenerationMismatch { segment, frame } => write!(
                f,
                "frame generation {frame} differs from the open segment's {segment}"
            ),
            StoreError::TierKindMismatch { tier, payload } => {
                write!(f, "tier {tier} cannot hold a {payload} payload")
            }
            StoreError::IncarnationReused {
                epoch,
                inc,
                highest,
            } => write!(
                f,
                "incarnation {inc} in epoch {epoch} was already issued (highest {highest})"
            ),
            StoreError::DictionaryFull { max, live } => {
                write!(f, "the incarnation index holds {live} of {max} and cannot retire")
            }
            StoreError::UnknownIncarnation { epoch, inc } => {
                write!(f, "incarnation {inc} of epoch {epoch} is not active")
            }
            StoreError::SegmentIncarnations { seq, count, max } => write!(
                f,
                "segment {seq} would reference {count} incarnations, the bound is {max}"
            ),
            StoreError::JobTooLarge { len, max } => {
                write!(f, "a {len}-byte job exceeds the whole queue bound of {max}")
            }
            StoreError::RangeInverted { from, to } => {
                write!(f, "range end {to} precedes its start {from}")
            }
            StoreError::QueryLimit { asked, max } => {
                write!(f, "a query asked for {asked} records, the bound is {max}")
            }
            StoreError::NegativeTime { ns } => {
                write!(f, "time {ns} cannot name a segment")
            }
            StoreError::Config { key, reason } => write!(f, "config {key}: {reason}"),
            StoreError::UnknownTier { name } => write!(f, "no tier named {name}"),
            StoreError::DuplicateTier { name } => write!(f, "tier {name} is declared twice"),
            StoreError::ForeignEntry { name, found } => write!(
                f,
                "{name} is not the store's ({found}); it is left untouched and reported"
            ),
            StoreError::EventVocabulary { field, found } => {
                write!(f, "an event {field} byte of {found} is outside the vocabulary")
            }
            StoreError::EventVocabularyName { field, found } => {
                write!(f, "an event {field} of {found:?} is outside the vocabulary")
            }
            StoreError::EventFieldTooLong { field, len, max } => {
                write!(f, "an event {field} of {len} bytes exceeds the {max}-byte bound")
            }
            StoreError::SpanLimit {
                issuer,
                open,
                max,
                global,
            } => {
                if *global {
                    write!(f, "the span table is full: {open} open spans of {max}")
                } else {
                    write!(f, "issuer {issuer} holds {open} open spans of {max}")
                }
            }
            StoreError::SpanUnknown { issuer, key } => {
                write!(f, "issuer {issuer} has no open span keyed {key:?}")
            }
            StoreError::QualityCode { found } => {
                write!(f, "quality code {found} is outside the eight-code vocabulary")
            }
            StoreError::UnknownDictionaryEntry {
                segment,
                index,
                inc,
            } => write!(
                f,
                "record {index} of {segment} names incarnation {inc}, which its dictionary lacks"
            ),
        }
    }
}

impl core::error::Error for StoreError {}

impl From<RegionOutOfBounds> for StoreError {
    fn from(region: RegionOutOfBounds) -> StoreError {
        StoreError::RegionOutOfBounds(region)
    }
}

#[cfg(test)]
mod tests {
    use super::{length_overflow, StoreError};

    #[test]
    fn every_kind_name_is_distinct_and_machine_readable() {
        let names = [
            StoreError::LengthOverflow,
            StoreError::NotSealed,
            StoreError::SealedWithoutFooter { name: "n".into() },
            StoreError::OpenWithFooter { name: "n".into() },
            StoreError::SegmentIdentity {
                field: "seq",
                name: 1,
                header: 2,
            },
            StoreError::Truncated {
                context: "x",
                need: 1,
                have: 0,
            },
            StoreError::TrailingBytes { extra: 1 },
            StoreError::Config {
                key: "k",
                reason: "r",
            },
            StoreError::UnknownTier { name: "hot".into() },
            StoreError::EventVocabulary {
                field: "kind",
                found: 9,
            },
            StoreError::EventVocabularyName {
                field: "outcome",
                found: "nope".into(),
            },
            StoreError::EventFieldTooLong {
                field: "code",
                len: 65,
                max: 64,
            },
            StoreError::SpanLimit {
                issuer: "u".into(),
                open: 1,
                max: 1,
                global: false,
            },
            StoreError::SpanUnknown {
                issuer: "u".into(),
                key: "k".into(),
            },
            StoreError::QualityCode { found: 9 },
            StoreError::UnknownDictionaryEntry {
                segment: "s".into(),
                index: 1,
                inc: 7,
            },
        ];
        let mut kinds: Vec<&str> = names.iter().map(StoreError::kind).collect();
        kinds.sort_unstable();
        let unique = {
            let mut copy = kinds.clone();
            copy.dedup();
            copy
        };
        assert_eq!(kinds, unique, "kind names must not collide");
        for kind in &kinds {
            assert!(
                kind.chars()
                    .all(|c| c.is_ascii_lowercase() || c == '-' || c.is_ascii_digit()),
                "{kind} is not a machine name"
            );
        }
    }

    #[test]
    fn a_full_disk_is_its_own_variant_and_counted() {
        let full = StoreError::io(
            "append",
            "hot-1",
            &std::io::Error::from_raw_os_error(super::ENOSPC),
        );
        assert_eq!(
            full,
            StoreError::NoSpace {
                op: "append",
                name: "hot-1".into()
            }
        );
        assert!(full.is_no_space());
        assert_eq!(full.kind(), "no-space");
        assert_eq!(full.os_code(), Some(super::ENOSPC));
        assert_eq!(full.to_string(), "append hot-1: no space left on device");
        let other = StoreError::io("open", "hot-1", &std::io::Error::from_raw_os_error(13));
        assert!(!other.is_no_space());
        assert_eq!(other.kind(), "io");
        assert_eq!(other.os_code(), Some(13));
        let plain = StoreError::io("read", "hot-1", &std::io::Error::other("boom"));
        assert_eq!(plain.os_code(), None);
        assert_eq!(plain.kind(), "io");
        assert!(!plain.is_no_space());
        assert!(!plain.is_format());
    }

    #[test]
    fn format_and_contract_refusals_are_separated() {
        let format = [
            StoreError::Truncated {
                context: "header",
                need: 64,
                have: 12,
            },
            StoreError::HeaderMagic { found: [0; 7] },
            StoreError::RecordCrc {
                index: 2,
                declared: 1,
                computed: 2,
            },
            StoreError::NotSealed,
            StoreError::SealedWithoutFooter {
                name: "hot-0-0.seg".into(),
            },
            StoreError::OpenWithFooter {
                name: "hot-0-0.seg.open".into(),
            },
            StoreError::SegmentIdentity {
                field: "seq",
                name: 1,
                header: 2,
            },
            StoreError::FooterCount {
                declared: 3,
                measured: 1,
            },
        ];
        for error in format {
            assert!(error.is_format(), "{error} should be a format refusal");
        }
        let contract = [
            StoreError::UnknownIncarnation { epoch: 1, inc: 4 },
            StoreError::Config {
                key: "hot.step_s",
                reason: "must be positive",
            },
            StoreError::RangeInverted { from: 5, to: 1 },
        ];
        for error in contract {
            assert!(!error.is_format(), "{error} should be a contract refusal");
        }
    }

    #[test]
    fn every_variant_renders_a_message_naming_its_own_facts() {
        let rendered = [
            StoreError::RegionOutOfBounds(super::RegionOutOfBounds { need: 5, have: 4 })
                .to_string(),
            StoreError::Reserved {
                field: "flags",
                found: 7,
            }
            .to_string(),
            StoreError::HeaderLength {
                declared: 8,
                expected: 64,
            }
            .to_string(),
            StoreError::PayloadKind { found: 9 }.to_string(),
            StoreError::RecordMagic {
                index: 1,
                found: [1, 2, 3, 4],
            }
            .to_string(),
            StoreError::RecordHeaderCrc {
                index: 0,
                declared: 1,
                computed: 2,
            }
            .to_string(),
            StoreError::RecordSequence {
                index: 1,
                expected: 1,
                found: 2,
            }
            .to_string(),
            StoreError::RecordKind { index: 0, found: 4 }.to_string(),
            StoreError::RecordIncarnations {
                index: 0,
                count: 9,
                max: 8,
            }
            .to_string(),
            StoreError::RecordPadding {
                index: 0,
                declared: 3,
                expected: 0,
            }
            .to_string(),
            StoreError::PayloadLength {
                index: 0,
                declared: 16,
                actual: 8,
            }
            .to_string(),
            StoreError::PayloadFrameType {
                expected: 5,
                found: 1,
            }
            .to_string(),
            StoreError::PayloadFrameLength {
                declared: 16,
                have: 8,
            }
            .to_string(),
            StoreError::PayloadFrameFlags { flags: 2 }.to_string(),
            StoreError::PayloadFrameReserved { found: 1 }.to_string(),
            StoreError::PayloadTooLarge { len: 9, max: 8 }.to_string(),
            StoreError::FooterMagic { found: [0; 4] }.to_string(),
            StoreError::FooterCrc {
                declared: 1,
                computed: 2,
            }
            .to_string(),
            StoreError::FooterOffset {
                declared: 1,
                measured: 2,
            }
            .to_string(),
            StoreError::FooterIdentity {
                header: 1,
                footer: 2,
            }
            .to_string(),
            StoreError::FooterGeneration {
                header: 1,
                footer: 2,
            }
            .to_string(),
            StoreError::SegmentName { name: "x".into() }.to_string(),
            StoreError::SealedWithoutFooter {
                name: "hot-0-0.seg".into(),
            }
            .to_string(),
            StoreError::OpenWithFooter {
                name: "hot-0-0.seg.open".into(),
            }
            .to_string(),
            StoreError::SegmentIdentity {
                field: "start_real_ns",
                name: 60,
                header: 0,
            }
            .to_string(),
            StoreError::BadName {
                name: "../x".into(),
            }
            .to_string(),
            StoreError::Symlink { name: "x".into() }.to_string(),
            StoreError::NotADirectory {
                path: "/tmp/x".into(),
            }
            .to_string(),
            StoreError::EntryLimit { max: 4 }.to_string(),
            StoreError::FileTooLarge { len: 9, max: 4 }.to_string(),
            StoreError::Io {
                op: "read",
                os_code: None,
                message: "x".into(),
            }
            .to_string(),
            StoreError::NotTmpfs {
                tier: "hot".into(),
                fstype: "ext4".into(),
            }
            .to_string(),
            StoreError::MountTableUnreadable { reason: "x".into() }.to_string(),
            StoreError::MountNotFound { path: "/x".into() }.to_string(),
            StoreError::EpochMismatch {
                segment: 1,
                frame: 2,
            }
            .to_string(),
            StoreError::GenerationMismatch {
                segment: 1,
                frame: 2,
            }
            .to_string(),
            StoreError::TierKindMismatch {
                tier: "hot",
                payload: "events",
            }
            .to_string(),
            StoreError::IncarnationReused {
                epoch: 1,
                inc: 2,
                highest: 3,
            }
            .to_string(),
            StoreError::DictionaryFull { max: 4, live: 4 }.to_string(),
            StoreError::UnknownIncarnation { epoch: 1, inc: 2 }.to_string(),
            StoreError::SegmentIncarnations {
                seq: 1,
                count: 9,
                max: 8,
            }
            .to_string(),
            StoreError::JobTooLarge { len: 9, max: 8 }.to_string(),
            StoreError::QueryLimit { asked: 9, max: 8 }.to_string(),
            StoreError::NegativeTime { ns: -1 }.to_string(),
            StoreError::Config {
                key: "k",
                reason: "r",
            }
            .to_string(),
            StoreError::DuplicateTier { name: "hot".into() }.to_string(),
            StoreError::ForeignEntry {
                name: "x".into(),
                found: "a symlink",
            }
            .to_string(),
        ];
        for (index, message) in rendered.iter().enumerate() {
            assert!(!message.is_empty(), "variant {index} renders nothing");
            assert!(
                message.len() > 8,
                "variant {index} renders a message that names nothing: {message}"
            );
        }
        assert!(rendered.iter().all(|m| !m.contains("NaN")));
    }

    #[test]
    fn a_reversed_region_is_a_length_overflow() {
        assert_eq!(length_overflow(3, 7), Ok(4));
        assert_eq!(length_overflow(7, 3), Err(StoreError::LengthOverflow));
    }

    #[test]
    fn an_error_converts_from_a_region_refusal() {
        let region = super::RegionOutOfBounds { need: 3, have: 2 };
        assert_eq!(
            StoreError::from(region),
            StoreError::RegionOutOfBounds(region)
        );
    }
}
