//! Typed failures of the column codec. Every decode path returns one of these and no
//! path panics, slices an untrusted length or returns a partial result.
//!
//! The names are stable because they appear in operator output and in the fixtures
//! (`fixtures/wire/*.json` `"error"` fields), which both the Rust decoder and the
//! independent Python decoder compare against.

use core::fmt;

/// Largest frame this codec accepts, header included (plan v4 §B.1: "Max frame 16 MiB").
pub const MAX_FRAME_BYTES: usize = 16 * 1024 * 1024;

/// Every refusal of the wire codec.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ProtoError {
    /// Fewer bytes are available than the structure needs.
    Truncated {
        /// The stable machine name of the tool that reports a truncated frame.
        context: &'static str,
        /// Bytes required.
        need: usize,
        /// Bytes available.
        have: usize,
    },
    /// A frame claims more bytes than [`MAX_FRAME_BYTES`].
    OversizedFrame {
        /// The claimed length.
        len: usize,
        /// The maximum.
        max: usize,
    },
    /// The frame header's reserved field is not zero.
    NonZeroReserved(u16),
    /// The frame type byte is outside the closed vocabulary of §B.1.
    UnknownFrameType(u8),
    /// A frame flag bit this codec does not define.
    UnknownFrameFlags(u8),
    /// The first bytes are not JSON (`{`) and not the binary hello magic.
    UnknownMode {
        /// The offending first byte, or `None` for an empty input.
        first: Option<u8>,
    },
    /// The binary hello magic (`HOB\x01`) is present but the version is not supported.
    UnsupportedVersion {
        /// The protocol major the peer sent.
        major: u16,
    },
    /// A section directory header field is not zero.
    SectionReserved(u16),
    /// A section kind the codec does not know is marked required (§B.2: fail, never skip).
    UnsupportedRequiredSection {
        /// The unknown kind code.
        kind: u16,
    },
    /// A known section carries a version this codec cannot decode.
    UnsupportedSectionVersion {
        /// The section kind.
        kind: u16,
        /// The version found.
        version: u8,
        /// The version this codec implements.
        supported: u8,
    },
    /// A section's flags carry bits this codec does not define.
    UnknownSectionFlags {
        /// The section kind.
        kind: u16,
        /// The offending flag bits (a byte in the directory, a word in the block header).
        /// The header's flags field is a `u32`, so this is too: truncating it would report
        /// a different value from the one the sender wrote.
        flags: u32,
    },
    /// The same single-instance section appears twice.
    DuplicateSection {
        /// The section kind.
        kind: u16,
    },
    /// A required section is missing.
    MissingSection {
        /// The section kind.
        kind: u16,
    },
    /// A section body's declared length does not match its content and the frame layout.
    SectionLength {
        /// The section kind (`0` when the layout itself is wrong).
        kind: u16,
        /// The declared length.
        declared: usize,
        /// The length the content requires.
        expected: usize,
    },
    /// The frame contains bytes beyond the last section body.
    TrailingBytes {
        /// Bytes left over.
        extra: usize,
    },
    /// A column's declared value type is outside §B.2's three types.
    UnknownValueType(u8),
    /// A column's declared kind is outside §B.2's gauge/counter/state.
    UnknownColumnKind(u8),
    /// A column's non-zero padding field.
    ColumnReserved {
        /// The metric whose column carries the padding.
        metric: u32,
        /// The padding value.
        pad: u16,
    },
    /// Two columns of one frame declare the same metric but different facts.
    ConflictingColumn {
        /// The metric index.
        metric: u32,
    },
    /// A quality byte is outside the closed vocabulary of plan v4 §C.2.
    UnknownQuality {
        /// The zero-based row.
        row: usize,
        /// The offending code.
        code: u8,
    },
    /// A history aggregation code is outside §B.2's aggregation kinds.
    UnknownAggregation(u8),
    /// A row or time count exceeds `u32`.
    RowCountOverflow(u64),
    /// A length computation overflowed `usize`.
    LengthOverflow,
    /// A dictionary entry exceeds the 256-byte bound of §B.3.
    DictionaryEntryTooLong {
        /// The index in the frame.
        index: usize,
        /// Observed length.
        len: usize,
    },
    /// A dictionary entry is not valid UTF-8.
    DictionaryEntryNotUtf8 {
        /// The index in the frame.
        index: usize,
    },
    /// A dictionary entry's kind code is outside §B.3's four kinds.
    UnknownDictionaryKind {
        /// The index in the frame.
        index: usize,
        /// The code.
        code: u8,
    },
    /// A dictionary entry's own field code (unit, column kind, scope, source) is outside
    /// the table.
    DictionaryEntryUnknownCode {
        /// The index in the frame.
        index: usize,
        /// The code.
        code: u8,
    },
    /// A dictionary entry's aggregation mask is not the mask its kind derives.
    ///
    /// The mask is a *derived* fact (§B.3), so a subset is as corrupt as an extra bit: a
    /// decoder that kept a subset would widen it on re-encode and silently drop the
    /// sender's restriction.
    DictionaryEntryMask {
        /// The index in the frame.
        index: usize,
        /// The mask the sender wrote.
        mask: u16,
        /// The mask the entry's kind derives.
        expected: u16,
    },
    /// A dictionary carries an entry whose identity already appears earlier.
    ///
    /// Indices must not change meaning inside one generation (§B.3), so a wire list that
    /// repeats an identity is refused instead of deduplicated: deduplication would shift
    /// every later index away from the sender's numbering.
    DuplicateDictionaryEntry {
        /// The wire position of the repeated entry.
        index: usize,
    },
    /// A dictionary entry's reserved flags byte is not zero.
    DictionaryEntryReserved {
        /// The index in the frame.
        index: usize,
        /// The flags byte.
        flags: u8,
    },
    /// A dictionary holds more entries than the configured active bound.
    DictionaryTooLarge {
        /// The count.
        count: usize,
        /// The bound.
        max: usize,
    },
    /// A reference names a dictionary slot that does not exist (a dangling reference).
    DanglingDictionaryRef {
        /// The index asked for.
        index: u32,
        /// The number of entries in the generation.
        len: usize,
    },
    /// A dictionary entry's text is valid UTF-8 but carries a control character, which no
    /// line-oriented tool can carry faithfully.
    DictionaryEntryControl {
        /// The index in the frame.
        index: usize,
        /// The byte offset of the control character.
        offset: usize,
    },
    /// A generation number cannot be advanced further.
    GenerationExhausted,
    /// A block references a dictionary generation the receiver has not been sent.
    StaleDictionaryGeneration {
        /// The generation the block needs.
        needed: u32,
        /// The generation the receiver holds.
        held: u32,
    },
    /// A block's rows section and its history time section are mutually exclusive.
    RowsAndTime {
        /// Rows entries.
        rows: usize,
        /// Time buckets.
        buckets: usize,
    },
    /// A block declares rows or buckets but carries neither a rows nor a time section.
    ///
    /// Values without an entity or a bucket have no place in the frame, so this is refused
    /// rather than carried as a "host-scope block" nobody defined.
    NoRowsOrTime {
        /// The row count the header declares.
        n_rows: u32,
    },
    /// A row references a dictionary entry that is not an incarnation.
    RowReferenceNotEntity {
        /// The dictionary index the row names.
        index: u32,
    },
    /// A column references a dictionary entry that is not a metric.
    ColumnReferenceNotMetric {
        /// The dictionary index the column names.
        index: u32,
    },
    /// A column's declared kind contradicts the metric facts in the dictionary.
    ColumnKindMismatch {
        /// The dictionary index the column names.
        index: u32,
        /// The kind code the column header declares.
        declared: u8,
        /// The kind code the dictionary declares.
        dictionary: u8,
    },
    /// A block-shaped frame whose type is not `BLOCK`.
    ///
    /// Plan v4 §B.1 gives each frame type one payload shape: `REQ`/`REP`/`EVT` carry a
    /// small JSON control object, `DICT` a dictionary, `BLOCK` the column block. A
    /// decoder that accepted block sections under any type would let a caller read a
    /// control envelope as measurements (and an encoder emit one), so the shape is
    /// checked against the declared type in both directions.
    NotABlock {
        /// The frame type the frame declared (its stored code).
        frame_type: u8,
    },
    /// Sections appear in an order the encoder never emits.
    ///
    /// The encoder has exactly one order — header, dictionary, rows-or-time, then
    /// columns — and two byte strings must never decode to one value (§B.5's
    /// byte-identity goal). An unordered directory would let a reordering of the same
    /// content pass as a different frame, so the decoder requires the canonical order.
    SectionOrder {
        /// The directory position of the section that broke the order.
        index: usize,
        /// The section kind found at that position.
        kind: u16,
    },
}

impl fmt::Display for ProtoError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ProtoError::Truncated {
                context,
                need,
                have,
            } => {
                write!(f, "{context} truncated: need {need} bytes, have {have}")
            }
            ProtoError::OversizedFrame { len, max } => {
                write!(f, "frame of {len} bytes exceeds the {max}-byte maximum")
            }
            ProtoError::NonZeroReserved(value) => {
                write!(f, "frame header reserved field is {value}, not 0")
            }
            ProtoError::UnknownFrameType(code) => write!(f, "unknown frame type {code}"),
            ProtoError::UnknownFrameFlags(flags) => {
                write!(f, "unknown frame flag bits {flags:#04x}")
            }
            ProtoError::UnknownMode { first: Some(byte) } => write!(
                f,
                "unexpected first byte {byte:#04x}: neither JSON nor binary"
            ),
            ProtoError::UnknownMode { first: None } => {
                f.write_str("empty input: neither JSON nor binary")
            }
            ProtoError::UnsupportedVersion { major } => {
                write!(f, "unsupported protocol major {major}")
            }
            ProtoError::SectionReserved(value) => {
                write!(f, "section directory reserved field is {value}, not 0")
            }
            ProtoError::UnsupportedRequiredSection { kind } => {
                write!(
                    f,
                    "required section kind {kind} is not supported; refusing the frame"
                )
            }
            ProtoError::UnsupportedSectionVersion {
                kind,
                version,
                supported,
            } => {
                write!(
                    f,
                    "section kind {kind} has version {version}; this codec implements {supported}"
                )
            }
            ProtoError::UnknownSectionFlags { kind, flags } => {
                write!(f, "section kind {kind} has unknown flag bits {flags:#04x}")
            }
            ProtoError::DuplicateSection { kind } => write!(f, "section kind {kind} appears twice"),
            ProtoError::MissingSection { kind } => {
                write!(f, "required section kind {kind} is missing")
            }
            ProtoError::SectionLength {
                kind,
                declared,
                expected,
            } => {
                write!(f, "section kind {kind} declares {declared} bytes, but its content needs {expected}")
            }
            ProtoError::TrailingBytes { extra } => {
                write!(f, "{extra} bytes remain after the last section body")
            }
            ProtoError::UnknownValueType(code) => write!(f, "unknown column value type {code}"),
            ProtoError::UnknownColumnKind(code) => write!(f, "unknown column kind {code}"),
            ProtoError::ColumnReserved { metric, pad } => {
                write!(
                    f,
                    "column for metric {metric} has a non-zero padding field {pad}"
                )
            }
            ProtoError::ConflictingColumn { metric } => {
                write!(
                    f,
                    "metric {metric} appears twice with different column facts"
                )
            }
            ProtoError::UnknownQuality { row, code } => {
                write!(f, "row {row} has unknown quality code {code}")
            }
            ProtoError::UnknownAggregation(code) => write!(f, "unknown aggregation code {code}"),
            ProtoError::RowCountOverflow(count) => {
                write!(f, "{count} rows do not fit a u32 row count")
            }
            ProtoError::LengthOverflow => f.write_str("a length computation overflowed"),
            ProtoError::DictionaryEntryTooLong { index, len } => {
                write!(
                    f,
                    "dictionary entry {index} is {len} bytes, the bound is 256"
                )
            }
            ProtoError::DictionaryEntryNotUtf8 { index } => {
                write!(f, "dictionary entry {index} is not valid UTF-8")
            }
            ProtoError::UnknownDictionaryKind { index, code } => {
                write!(f, "dictionary entry {index} has unknown kind code {code}")
            }
            ProtoError::DictionaryEntryUnknownCode { index, code } => {
                write!(f, "dictionary entry {index} has unknown field code {code}")
            }
            ProtoError::DictionaryEntryMask {
                index,
                mask,
                expected,
            } => {
                write!(
                    f,
                    "dictionary entry {index} declares aggregation mask {mask:#04x}, its kind derives {expected:#04x}"
                )
            }
            ProtoError::DuplicateDictionaryEntry { index } => {
                write!(
                    f,
                    "dictionary entry {index} repeats an identity an earlier entry already holds"
                )
            }
            ProtoError::DictionaryEntryReserved { index, flags } => {
                write!(
                    f,
                    "dictionary entry {index} has a non-zero reserved flags byte {flags}"
                )
            }
            ProtoError::DictionaryTooLarge { count, max } => {
                write!(f, "dictionary holds {count} entries, the bound is {max}")
            }
            ProtoError::DanglingDictionaryRef { index, len } => {
                write!(
                    f,
                    "dictionary reference {index} is outside the {len}-entry generation"
                )
            }
            ProtoError::DictionaryEntryControl { index, offset } => {
                write!(
                    f,
                    "dictionary entry {index} has a control character at byte {offset}"
                )
            }
            ProtoError::GenerationExhausted => {
                f.write_str("dictionary generation cannot advance further")
            }
            ProtoError::StaleDictionaryGeneration { needed, held } => {
                write!(
                    f,
                    "block needs dictionary generation {needed}, receiver holds {held}"
                )
            }
            ProtoError::RowsAndTime { rows, buckets } => {
                write!(
                    f,
                    "block carries {rows} rows and {buckets} time buckets; they are exclusive"
                )
            }
            ProtoError::NoRowsOrTime { n_rows } => {
                write!(
                    f,
                    "block declares {n_rows} rows but carries neither a rows nor a time section"
                )
            }
            ProtoError::RowReferenceNotEntity { index } => {
                write!(
                    f,
                    "row references dictionary entry {index}, which is not an incarnation"
                )
            }
            ProtoError::ColumnReferenceNotMetric { index } => {
                write!(
                    f,
                    "column references dictionary entry {index}, which is not a metric"
                )
            }
            ProtoError::ColumnKindMismatch {
                index,
                declared,
                dictionary,
            } => {
                write!(
                    f,
                    "column for metric {index} declares kind {declared}, the dictionary declares {dictionary}"
                )
            }
            ProtoError::NotABlock { frame_type } => {
                write!(f, "frame type {frame_type} does not carry a column block")
            }
            ProtoError::SectionOrder { index, kind } => {
                write!(
                    f,
                    "section {index} (kind {kind}) is out of the canonical order"
                )
            }
        }
    }
}

impl core::error::Error for ProtoError {}

impl ProtoError {
    /// The stable machine name of this refusal.
    ///
    /// These names are part of the wire contract: the independent Python decoder uses the
    /// same vocabulary (`python/reference/hostobs_wire_ref.py`) and the golden fixtures
    /// (`fixtures/wire/*.expect`) record which refusal a corrupt case must produce, so a
    /// renamed variant is a visible contract change rather than a silent one.
    #[must_use]
    pub const fn kind(&self) -> &'static str {
        match self {
            ProtoError::Truncated { .. } => "truncated",
            ProtoError::OversizedFrame { .. } => "oversized-frame",
            ProtoError::NonZeroReserved(_) => "non-zero-reserved",
            ProtoError::UnknownFrameType(_) => "unknown-frame-type",
            ProtoError::UnknownFrameFlags(_) => "unknown-frame-flags",
            ProtoError::UnknownMode { .. } => "unknown-mode",
            ProtoError::UnsupportedVersion { .. } => "unsupported-version",
            ProtoError::SectionReserved(_) => "section-reserved",
            ProtoError::UnsupportedRequiredSection { .. } => "unsupported-required-section",
            ProtoError::UnsupportedSectionVersion { .. } => "unsupported-section-version",
            ProtoError::UnknownSectionFlags { .. } => "unknown-section-flags",
            ProtoError::DuplicateSection { .. } => "duplicate-section",
            ProtoError::MissingSection { .. } => "missing-section",
            ProtoError::SectionLength { .. } => "section-length",
            ProtoError::TrailingBytes { .. } => "trailing-bytes",
            ProtoError::UnknownValueType(_) => "unknown-value-type",
            ProtoError::UnknownColumnKind(_) => "unknown-column-kind",
            ProtoError::ColumnReserved { .. } => "column-reserved",
            ProtoError::ConflictingColumn { .. } => "conflicting-column",
            ProtoError::UnknownQuality { .. } => "unknown-quality",
            ProtoError::UnknownAggregation(_) => "unknown-aggregation",
            ProtoError::RowCountOverflow(_) => "row-count-overflow",
            ProtoError::LengthOverflow => "length-overflow",
            ProtoError::DictionaryEntryTooLong { .. } => "dictionary-entry-too-long",
            ProtoError::DictionaryEntryNotUtf8 { .. } => "dictionary-entry-not-utf8",
            ProtoError::UnknownDictionaryKind { .. } => "unknown-dictionary-kind",
            ProtoError::DictionaryEntryUnknownCode { .. } => "dictionary-entry-unknown-code",
            ProtoError::DictionaryEntryMask { .. } => "dictionary-entry-mask",
            ProtoError::DuplicateDictionaryEntry { .. } => "duplicate-dictionary-entry",
            ProtoError::DictionaryEntryReserved { .. } => "dictionary-entry-reserved",
            ProtoError::DictionaryTooLarge { .. } => "dictionary-too-large",
            ProtoError::DanglingDictionaryRef { .. } => "dangling-dictionary-ref",
            ProtoError::DictionaryEntryControl { .. } => "dictionary-entry-control",
            ProtoError::GenerationExhausted => "generation-exhausted",
            ProtoError::StaleDictionaryGeneration { .. } => "stale-dictionary-generation",
            ProtoError::RowsAndTime { .. } => "rows-and-time",
            ProtoError::NoRowsOrTime { .. } => "no-rows-or-time",
            ProtoError::RowReferenceNotEntity { .. } => "row-reference-not-entity",
            ProtoError::ColumnReferenceNotMetric { .. } => "column-reference-not-metric",
            ProtoError::ColumnKindMismatch { .. } => "column-kind-mismatch",
            ProtoError::NotABlock { .. } => "not-a-block",
            ProtoError::SectionOrder { .. } => "section-order",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::ProtoError;

    #[test]
    fn every_refusal_renders_without_panicking_and_names_its_fact() {
        let cases: Vec<ProtoError> = vec![
            ProtoError::Truncated {
                context: "frame header",
                need: 12,
                have: 5,
            },
            ProtoError::OversizedFrame {
                len: 17 * 1024 * 1024,
                max: 16 * 1024 * 1024,
            },
            ProtoError::NonZeroReserved(1),
            ProtoError::UnknownFrameType(9),
            ProtoError::UnknownFrameFlags(0x80),
            ProtoError::UnknownMode { first: Some(b'X') },
            ProtoError::UnknownMode { first: None },
            ProtoError::UnsupportedVersion { major: 2 },
            ProtoError::SectionReserved(3),
            ProtoError::UnsupportedRequiredSection { kind: 9 },
            ProtoError::UnsupportedSectionVersion {
                kind: 3,
                version: 2,
                supported: 1,
            },
            ProtoError::UnknownSectionFlags { kind: 3, flags: 2 },
            ProtoError::DuplicateSection { kind: 1 },
            ProtoError::MissingSection { kind: 1 },
            ProtoError::SectionLength {
                kind: 3,
                declared: 9,
                expected: 17,
            },
            ProtoError::TrailingBytes { extra: 4 },
            ProtoError::UnknownValueType(4),
            ProtoError::UnknownColumnKind(3),
            ProtoError::ColumnReserved { metric: 7, pad: 1 },
            ProtoError::ConflictingColumn { metric: 7 },
            ProtoError::UnknownQuality { row: 2, code: 8 },
            ProtoError::UnknownAggregation(4),
            ProtoError::RowCountOverflow(1 << 33),
            ProtoError::LengthOverflow,
            ProtoError::DictionaryEntryTooLong { index: 1, len: 257 },
            ProtoError::DictionaryEntryNotUtf8 { index: 1 },
            ProtoError::UnknownDictionaryKind { index: 1, code: 9 },
            ProtoError::DictionaryEntryUnknownCode { index: 0, code: 9 },
            ProtoError::DictionaryEntryMask {
                index: 0,
                mask: 0,
                expected: 0b0011,
            },
            ProtoError::DuplicateDictionaryEntry { index: 3 },
            ProtoError::DictionaryEntryReserved { index: 0, flags: 1 },
            ProtoError::DictionaryTooLarge {
                count: 70000,
                max: 65536,
            },
            ProtoError::DanglingDictionaryRef { index: 5, len: 3 },
            ProtoError::DictionaryEntryControl {
                index: 0,
                offset: 2,
            },
            ProtoError::GenerationExhausted,
            ProtoError::StaleDictionaryGeneration { needed: 2, held: 1 },
            ProtoError::RowsAndTime {
                rows: 3,
                buckets: 2,
            },
            ProtoError::NoRowsOrTime { n_rows: 3 },
            ProtoError::RowReferenceNotEntity { index: 2 },
            ProtoError::ColumnReferenceNotMetric { index: 0 },
            ProtoError::ColumnKindMismatch {
                index: 2,
                declared: 0,
                dictionary: 1,
            },
            ProtoError::NotABlock { frame_type: 1 },
            ProtoError::SectionOrder { index: 2, kind: 1 },
        ];
        for case in cases {
            let rendered = case.to_string();
            assert!(!rendered.is_empty(), "{case:?}");
            assert!(
                !rendered.contains("{{"),
                "no unrendered placeholder: {rendered}"
            );
        }
    }

    #[test]
    fn every_refusal_has_one_stable_machine_name() {
        let cases: Vec<(&str, ProtoError)> = vec![
            (
                "truncated",
                ProtoError::Truncated {
                    context: "x",
                    need: 1,
                    have: 0,
                },
            ),
            (
                "oversized-frame",
                ProtoError::OversizedFrame { len: 1, max: 1 },
            ),
            ("non-zero-reserved", ProtoError::NonZeroReserved(1)),
            ("unknown-frame-type", ProtoError::UnknownFrameType(1)),
            ("unknown-frame-flags", ProtoError::UnknownFrameFlags(1)),
            ("unknown-mode", ProtoError::UnknownMode { first: None }),
            (
                "unsupported-version",
                ProtoError::UnsupportedVersion { major: 1 },
            ),
            ("section-reserved", ProtoError::SectionReserved(1)),
            (
                "unsupported-required-section",
                ProtoError::UnsupportedRequiredSection { kind: 1 },
            ),
            (
                "unsupported-section-version",
                ProtoError::UnsupportedSectionVersion {
                    kind: 1,
                    version: 1,
                    supported: 1,
                },
            ),
            (
                "unknown-section-flags",
                ProtoError::UnknownSectionFlags { kind: 1, flags: 1 },
            ),
            (
                "duplicate-section",
                ProtoError::DuplicateSection { kind: 1 },
            ),
            ("missing-section", ProtoError::MissingSection { kind: 1 }),
            (
                "section-length",
                ProtoError::SectionLength {
                    kind: 1,
                    declared: 1,
                    expected: 2,
                },
            ),
            ("trailing-bytes", ProtoError::TrailingBytes { extra: 1 }),
            ("unknown-value-type", ProtoError::UnknownValueType(1)),
            ("unknown-column-kind", ProtoError::UnknownColumnKind(1)),
            (
                "column-reserved",
                ProtoError::ColumnReserved { metric: 1, pad: 1 },
            ),
            (
                "conflicting-column",
                ProtoError::ConflictingColumn { metric: 1 },
            ),
            (
                "unknown-quality",
                ProtoError::UnknownQuality { row: 1, code: 1 },
            ),
            ("unknown-aggregation", ProtoError::UnknownAggregation(1)),
            ("row-count-overflow", ProtoError::RowCountOverflow(1)),
            ("length-overflow", ProtoError::LengthOverflow),
            (
                "dictionary-entry-too-long",
                ProtoError::DictionaryEntryTooLong { index: 1, len: 1 },
            ),
            (
                "dictionary-entry-not-utf8",
                ProtoError::DictionaryEntryNotUtf8 { index: 1 },
            ),
            (
                "unknown-dictionary-kind",
                ProtoError::UnknownDictionaryKind { index: 1, code: 1 },
            ),
            (
                "dictionary-entry-unknown-code",
                ProtoError::DictionaryEntryUnknownCode { index: 1, code: 1 },
            ),
            (
                "dictionary-entry-mask",
                ProtoError::DictionaryEntryMask {
                    index: 1,
                    mask: 0,
                    expected: 1,
                },
            ),
            (
                "duplicate-dictionary-entry",
                ProtoError::DuplicateDictionaryEntry { index: 1 },
            ),
            (
                "dictionary-entry-reserved",
                ProtoError::DictionaryEntryReserved { index: 1, flags: 1 },
            ),
            (
                "dictionary-too-large",
                ProtoError::DictionaryTooLarge { count: 1, max: 1 },
            ),
            (
                "dangling-dictionary-ref",
                ProtoError::DanglingDictionaryRef { index: 1, len: 1 },
            ),
            (
                "dictionary-entry-control",
                ProtoError::DictionaryEntryControl {
                    index: 1,
                    offset: 1,
                },
            ),
            ("generation-exhausted", ProtoError::GenerationExhausted),
            (
                "stale-dictionary-generation",
                ProtoError::StaleDictionaryGeneration { needed: 1, held: 2 },
            ),
            (
                "rows-and-time",
                ProtoError::RowsAndTime {
                    rows: 1,
                    buckets: 1,
                },
            ),
            ("no-rows-or-time", ProtoError::NoRowsOrTime { n_rows: 1 }),
            (
                "row-reference-not-entity",
                ProtoError::RowReferenceNotEntity { index: 1 },
            ),
            (
                "column-reference-not-metric",
                ProtoError::ColumnReferenceNotMetric { index: 1 },
            ),
            (
                "column-kind-mismatch",
                ProtoError::ColumnKindMismatch {
                    index: 1,
                    declared: 0,
                    dictionary: 1,
                },
            ),
            ("not-a-block", ProtoError::NotABlock { frame_type: 1 }),
            (
                "section-order",
                ProtoError::SectionOrder { index: 2, kind: 1 },
            ),
        ];
        let mut names = Vec::new();
        for (expected, error) in cases {
            assert_eq!(error.kind(), expected);
            assert!(
                !names.contains(&expected),
                "{expected} is used by two variants; the fixture vocabulary must be one-to-one"
            );
            names.push(expected);
        }
        assert_eq!(
            names.len(),
            42,
            "the refused-variant count is part of the contract"
        );
    }

    #[test]
    fn the_maximum_frame_is_the_documented_sixteen_mebibytes() {
        assert_eq!(super::MAX_FRAME_BYTES, 16 << 20);
    }
}
