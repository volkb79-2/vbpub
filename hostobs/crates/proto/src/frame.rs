//! Frame header, section directory and column blocks (plan v4 §B.1–§B.2).
//!
//! # The layout, exactly
//!
//! ```text
//! FRAME    := HDR | BLOCK
//! HDR      := u32 len (whole frame, header included) | u8 type | u8 flags | u16 0 | u32 corr
//!             (12 bytes, little-endian)
//! BLOCK    := u16 n_sec | u16 0 | n_sec × SECDIR | bodies
//! SECDIR   := u16 kind | u8 ver | u8 flags (bit0 = required) | u32 len
//! HDRSEC   := boot_id[16] | u64 epoch | u64 seq | u64 mono_start_ns | u64 mono_end_ns |
//!             u64 boot_ns | i64 real_ns | u32 dict_gen | u32 n_rows | u32 dropped | u32 flags
//! ROWSSEC  := n_rows × u32 ent | n_rows × u32 t_off_us
//! COLSEC   := u32 metric | u8 type | u8 kind | u16 0 | n_rows × 8B value |
//!             n_rows × u8 quality | pad to 8
//! TIMESEC  := n × i64 bucket_real_ns | n × u32 covered_ms | n × u16 samples |
//!             n × u8 agg | pad to 8      -- replaces ROWSSEC in a history block
//! DICTSEC  := the whole generation, see [`crate::dict`]
//! SUBJSEC  := u32 ent | u32 0                 -- minor 1, history blocks only
//! ```
//!
//! Every body length is a multiple of eight, so every body starts eight-byte aligned
//! (`16 + 8 × n_sec` is divisible by eight) and no inter-body padding exists to argue
//! about. A body's declared length is its content length **including** its own internal
//! padding; a reader requires the declared length to equal what its content needs.
//!
//! # Contracts
//!
//! - **Fixed-width typed little-endian columns.** No varints, no per-value parsing: one
//!   `memoryview` + `struct.unpack_from` reads a column on the Python side (plan v4 §B.1,
//!   decision D4).
//! - **Counters stay raw `u64`.** The column type is part of its header and is given back
//!   with every value; nothing is converted, and no `u64` is ever routed through `f64`.
//! - **Every value carries a `u8` quality.** The column is mandatory; it is what makes
//!   "zero" distinguishable from "absent" (§C.2). A missing row keeps its own eight bytes
//!   and the quality code says not to read them: a value row is never omitted and never
//!   rewritten.
//! - **A required section is never skipped.** Unknown kind + required flag is
//!   `unsupported-section`; unknown kind without the flag is skipped by its declared
//!   length (§B.2). A *known* kind with an unknown version is refused even when optional:
//!   skipping it would silently drop data the reader was promised.
//! - **No partial success, no panic.** A decode returns either a complete validated
//!   [`Block`] or one typed [`ProtoError`]. All lengths are checked against the real
//!   buffer before a slice is taken; all arithmetic is checked; nothing indexes untrusted
//!   bytes.
//! - **Oversize is refused before allocation.** A frame declaring more than
//!   [`MAX_FRAME_BYTES`] is refused without using that number as an allocation size.
//! - **Encoding is the exact inverse of decoding** on canonical bytes, which is what the
//!   golden fixtures assert in two languages (§B.5).
//!
//! # Sections (version 1)
//!
//! | kind | name | required | repeatable | body |
//! |---|---|---|---|---|
//! | 1 | header | yes | no | `HDRSEC` |
//! | 2 | rows | no | no | `ROWSSEC` (live block) |
//! | 3 | columns | no | yes | `COLSEC` |
//! | 4 | time | no | no | `TIMESEC` (history block) |
//! | 5 | dictionary | no | no | `DICTSEC` |
//! | 6 | subject (minor 1) | yes when present | no | `SUBJSEC` (history block) |
//!
//! A block carries rows **or** time buckets; both at once, or columns for a row count
//! nobody declared, are refused. The canonical order is header, dictionary, subject,
//! rows or time, then columns. The subject names the one incarnation a history block
//! describes and needs the time section (`subject-without-time`).
//!
//! # Received frames
//!
//! `decode_block_with(bytes, held)` judges a block that carries no dictionary against the
//! dictionary the stream holds. Beyond the encoder's layout, a received frame must have
//! every section length a multiple of eight (`section-length`), the header at directory
//! entry 0 (an unknown kind there is `section-order`, judged after the rank check), the
//! directory's bytes present before storage is reserved for them, `SECTIONED` on BLOCK
//! frames only, and every history bucket's aggregation inside every column's metric mask
//! (`aggregation-not-permitted`). The order in which they are judged is WIRE.md §3.10.

use std::collections::HashSet;

use crate::dict::{Aggregation, Dictionary, Entry, MetricKind, MetricValueType};
use crate::error::{ProtoError, MAX_FRAME_BYTES};
use crate::quality::Quality;
use crate::read::Cursor;
use crate::write::Writer;

/// Magic of the binary mode (plan v4 §B.1).
pub const MAGIC: [u8; 4] = *b"HOB\x01";
/// The protocol major this codec speaks.
pub const PROTO_MAJOR: u16 = 1;
/// The protocol minor this codec speaks; an additive change raises it.
pub const PROTO_MINOR: u16 = 1;
/// The frame header size in bytes.
pub const FRAME_HEADER_LEN: usize = 12;
/// The section directory entry size in bytes.
pub const SECTION_DIR_ENTRY_LEN: usize = 8;
/// The header section body size (80 bytes, eight-aligned).
pub const HEADER_BODY_LEN: usize = 80;
/// The subject section body size (`u32 ent | u32 0`, eight-aligned).
pub const SUBJECT_BODY_LEN: usize = 8;

/// The frame types of plan v4 §B.1.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash)]
#[repr(u8)]
pub enum FrameType {
    /// A request.
    Req = 1,
    /// A reply.
    Rep = 2,
    /// An event.
    Evt = 3,
    /// A dictionary delta or a full generation.
    Dict = 4,
    /// A column block: a live tick, a series page or a snapshot reply.
    Block = 5,
    /// The end of a paged reply.
    End = 6,
}

/// Frame flag: the frame carries a section directory.
pub const FLAG_SECTIONED: u8 = 0b0000_0001;
/// Every defined frame flag bit.
pub const DEFINED_FRAME_FLAGS: u8 = FLAG_SECTIONED;

impl FrameType {
    /// Decode the type byte.
    ///
    /// # Errors
    ///
    /// [`ProtoError::UnknownFrameType`].
    pub const fn from_code(code: u8) -> Result<FrameType, ProtoError> {
        match code {
            1 => Ok(FrameType::Req),
            2 => Ok(FrameType::Rep),
            3 => Ok(FrameType::Evt),
            4 => Ok(FrameType::Dict),
            5 => Ok(FrameType::Block),
            6 => Ok(FrameType::End),
            other => Err(ProtoError::UnknownFrameType(other)),
        }
    }

    /// The stable lower-case name used by fixtures.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            FrameType::Req => "req",
            FrameType::Rep => "rep",
            FrameType::Evt => "evt",
            FrameType::Dict => "dict",
            FrameType::Block => "block",
            FrameType::End => "end",
        }
    }

    /// The stored code.
    #[must_use]
    pub const fn code(self) -> u8 {
        self as u8
    }
}

/// The section kinds this codec implements, version 1.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash)]
#[repr(u16)]
pub enum SectionKind {
    /// The block header (`HDRSEC`).
    Header = 1,
    /// The entity and time-offset rows (`ROWSSEC`).
    Rows = 2,
    /// One typed column (`COLSEC`); repeatable.
    Column = 3,
    /// History buckets (`TIMESEC`).
    Time = 4,
    /// A complete dictionary generation.
    Dictionary = 5,
    /// The one incarnation a history block describes (minor 1).
    Subject = 6,
}

/// A raw section kind code that this codec does not know.
pub type UnknownSectionKind = u16;

impl SectionKind {
    /// The stored code.
    #[must_use]
    pub const fn code(self) -> u16 {
        self as u16
    }

    /// The stable lower-case name used by fixtures.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            SectionKind::Header => "header",
            SectionKind::Rows => "rows",
            SectionKind::Column => "columns",
            SectionKind::Time => "time",
            SectionKind::Dictionary => "dictionary",
            SectionKind::Subject => "subject",
        }
    }

    /// The version this codec implements.
    #[must_use]
    pub const fn version(self) -> u8 {
        1
    }

    /// Decode a kind code, or `None` for a kind this codec does not know.
    #[must_use]
    pub const fn from_code(code: u16) -> Option<SectionKind> {
        match code {
            1 => Some(SectionKind::Header),
            2 => Some(SectionKind::Rows),
            3 => Some(SectionKind::Column),
            4 => Some(SectionKind::Time),
            5 => Some(SectionKind::Dictionary),
            6 => Some(SectionKind::Subject),
            _ => None,
        }
    }

    /// True for the kinds that may appear at most once in a block.
    #[must_use]
    pub const fn is_single(self) -> bool {
        !matches!(self, SectionKind::Column)
    }

    /// True for the sections a reader cannot work without.
    #[must_use]
    pub const fn is_required(self) -> bool {
        matches!(self, SectionKind::Header | SectionKind::Subject)
    }
}

/// One section of a frame being built: kind, flags and body.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Section {
    kind: UnknownSectionKind,
    version: u8,
    flags: u8,
    body: Vec<u8>,
}

impl Section {
    /// A section of a known kind, with the required flag of that kind.
    #[must_use]
    pub fn new(kind: SectionKind, body: Vec<u8>) -> Section {
        Section {
            kind: kind.code(),
            version: kind.version(),
            flags: if kind.is_required() { 1 } else { 0 },
            body,
        }
    }

    /// A section with explicit directory fields, for fixtures of future or unknown kinds.
    ///
    /// This is the only way to build a frame that a *receiver* must refuse or skip, which
    /// is exactly what the conformance fixtures need; the wire format is not extended by
    /// it.
    #[must_use]
    pub fn raw(kind: UnknownSectionKind, version: u8, flags: u8, body: Vec<u8>) -> Section {
        Section {
            kind,
            version,
            flags,
            body,
        }
    }

    /// The section's kind code.
    #[must_use]
    pub const fn kind_code(&self) -> UnknownSectionKind {
        self.kind
    }

    /// The section's version byte.
    #[must_use]
    pub const fn version(&self) -> u8 {
        self.version
    }

    /// The section's flags byte.
    #[must_use]
    pub const fn flags(&self) -> u8 {
        self.flags
    }

    /// The section's body.
    #[must_use]
    pub fn body(&self) -> &[u8] {
        &self.body
    }

    /// True when the section's required flag is set.
    #[must_use]
    pub const fn is_required(&self) -> bool {
        self.flags & 1 != 0
    }
}

/// The facts the block header carries (plan v4 §B.2 `HDR`).
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BlockHeaderFacts {
    /// The boot the block was sampled in (16 bytes).
    pub boot_id: [u8; 16],
    /// The probe epoch.
    pub epoch: u64,
    /// The sequence number of this block within the epoch.
    pub seq: u64,
    /// Monotonic start of the sampling window, nanoseconds.
    pub mono_start_ns: u64,
    /// Monotonic end of the sampling window, nanoseconds.
    pub mono_end_ns: u64,
    /// Monotonic time of the boot, nanoseconds.
    pub boot_ns: u64,
    /// Real time at the end of the window, nanoseconds since the Unix epoch.
    pub real_ns: i64,
    /// The dictionary generation the block's indices refer to.
    pub dict_gen: u32,
    /// Rows (live block) or time buckets (history block).
    pub n_rows: u32,
    /// Frames dropped since the receiver's previous block.
    pub dropped: u32,
    /// Header flags (none defined by version 1; refused when non-zero).
    pub flags: u32,
}

/// One row identity of a live block: which incarnation, and when.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Row {
    /// The dictionary index of the entity incarnation.
    pub ent: u32,
    /// Offset of this row from the block's monotonic start, microseconds (§B.2).
    pub t_off_us: u32,
}

/// One history bucket's time facts (§B.2 `TIME`).
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct TimeBucket {
    /// The bucket's real time, nanoseconds since the Unix epoch.
    pub real_ns: i64,
    /// How much of the bucket real samples covered, milliseconds.
    pub covered_ms: u32,
    /// How many samples landed in the bucket.
    pub samples: u16,
    /// The aggregation this bucket's values use.
    pub agg: Aggregation,
}

/// One typed column: the metric, its declared facts and its rows.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Column {
    /// The dictionary index of the metric.
    pub metric: u32,
    /// The declared value type.
    pub value_type: MetricValueType,
    /// The declared arithmetic kind.
    pub kind: MetricKind,
    /// The eight bytes of each row, little-endian (unused when the quality says missing).
    pub values: Vec<u64>,
    /// The quality code of each row.
    pub quality: Vec<u8>,
}

impl Column {
    /// A column whose value and quality rows agree in length.
    ///
    /// # Errors
    ///
    /// [`ProtoError::SectionLength`] when the two row counts differ.
    pub fn new(
        metric: u32,
        value_type: MetricValueType,
        kind: MetricKind,
        values: Vec<u64>,
        quality: Vec<u8>,
    ) -> Result<Column, ProtoError> {
        if values.len() != quality.len() {
            return Err(ProtoError::SectionLength {
                kind: SectionKind::Column.code(),
                declared: values.len(),
                expected: quality.len(),
            });
        }
        Ok(Column {
            metric,
            value_type,
            kind,
            values,
            quality,
        })
    }

    /// The number of rows.
    #[must_use]
    pub fn rows(&self) -> usize {
        self.values.len()
    }

    /// The row's typed value and its quality.
    ///
    /// # Errors
    ///
    /// [`ProtoError::Truncated`] for a row that does not exist and
    /// [`ProtoError::UnknownQuality`] for a quality byte outside the closed table.
    pub fn sample(&self, row: usize) -> Result<SampleRow, ProtoError> {
        // `row` is caller-supplied: `row + 1` could overflow at `usize::MAX`, so the
        // reported need is computed checked and the lookup is a `get`, never an index.
        let need = row.checked_add(1).ok_or(ProtoError::LengthOverflow)?;
        let raw = *self.values.get(row).ok_or(ProtoError::Truncated {
            context: "column values",
            need,
            have: self.values.len(),
        })?;
        let code = *self.quality.get(row).ok_or(ProtoError::Truncated {
            context: "column quality",
            need,
            have: self.quality.len(),
        })?;
        let quality = Quality::from_code(code, row)?;
        let value = match self.value_type {
            MetricValueType::U64 => TypedValue::U64(raw),
            MetricValueType::I64 => TypedValue::I64(i64::from_le_bytes(raw.to_le_bytes())),
            MetricValueType::F64 => TypedValue::F64(f64::from_bits(raw)),
        };
        Ok(SampleRow { value, quality })
    }
}

/// A decoded value with its quality.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SampleRow {
    /// The typed value.
    pub value: TypedValue,
    /// Its quality.
    pub quality: Quality,
}

/// A decoded typed value (§B.2's three column types).
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum TypedValue {
    /// A `u64` reading.
    U64(u64),
    /// An `i64` reading.
    I64(i64),
    /// An `f64` reading, stored as its bits.
    F64(f64),
}

impl TypedValue {
    /// The reading, or `None` when the quality says this row is not a measurement.
    ///
    /// A consumer can therefore never read a placeholder as a value (§C.2).
    #[must_use]
    pub fn measured(self, quality: Quality) -> Option<TypedValue> {
        if quality.is_measurement() {
            Some(self)
        } else {
            None
        }
    }
}

/// A whole decoded block, and the frame around it.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Block {
    /// The frame type the block arrived in.
    pub frame_type: FrameType,
    /// The frame's correlation id.
    pub corr: u32,
    /// The header facts.
    pub header: BlockHeaderFacts,
    /// The rows (empty for a history block, or a live block with no rows).
    pub rows: Vec<Row>,
    /// The history buckets (empty for a live block).
    pub time: Vec<TimeBucket>,
    /// The typed columns.
    pub columns: Vec<Column>,
    /// The dictionary generation carried inside the frame, if any.
    pub dictionary: Option<Dictionary>,
    /// The dictionary index of the incarnation a history block describes (kind 6), if any.
    pub subject: Option<u32>,
    /// Unknown **optional** section kinds that were skipped, in directory order.
    pub skipped_sections: Vec<UnknownSectionKind>,
}

impl Block {
    /// True when this block carries history buckets rather than rows.
    #[must_use]
    pub fn is_history(&self) -> bool {
        !self.time.is_empty()
    }

    /// The rows of column `column`, or `None` when it has fewer rows.
    #[must_use]
    pub fn row_count(&self, column: usize) -> Option<usize> {
        self.columns.get(column).map(Column::rows)
    }
}

/// The parsed frame header.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct FrameHeader {
    /// The declared length of the whole frame, header included.
    pub len: u32,
    /// The frame type.
    pub frame_type: FrameType,
    /// The frame flags.
    pub flags: u8,
    /// The correlation id.
    pub corr: u32,
}

impl FrameHeader {
    /// Parse the first [`FRAME_HEADER_LEN`] bytes of a frame.
    ///
    /// The declared length is bounded here, not only in [`decode_block`]: a streaming
    /// reader allocates `len` bytes straight after `parse`, so a returned header always
    /// carries `FRAME_HEADER_LEN <= len <= MAX_FRAME_BYTES`.
    ///
    /// # Errors
    ///
    /// [`ProtoError::Truncated`] (also for a declared length shorter than the header
    /// itself), [`ProtoError::UnknownFrameType`], [`ProtoError::UnknownFrameFlags`],
    /// [`ProtoError::NonZeroReserved`] or [`ProtoError::OversizedFrame`] for a declared
    /// length above [`MAX_FRAME_BYTES`].
    pub fn parse(bytes: &[u8]) -> Result<FrameHeader, ProtoError> {
        let mut cursor = Cursor::new(bytes, "frame header");
        let len = cursor.u32()?;
        let frame_type = FrameType::from_code(cursor.u8()?)?;
        let flags = cursor.u8()?;
        if flags & !DEFINED_FRAME_FLAGS != 0 {
            return Err(ProtoError::UnknownFrameFlags(flags));
        }
        // SECTIONED is defined for BLOCK only (WIRE.md §2.1): on any other frame it is an
        // unknown flag, `bad-frame` on the wire.
        if flags & FLAG_SECTIONED != 0 && frame_type != FrameType::Block {
            return Err(ProtoError::UnknownFrameFlags(flags));
        }
        let reserved = cursor.u16()?;
        if reserved != 0 {
            return Err(ProtoError::NonZeroReserved(reserved));
        }
        let corr = cursor.u32()?;
        // The length is judged after the fixed fields, the order the Python reference
        // uses, so a frame with several defects reports the same first refusal in both.
        let declared = usize::try_from(len).map_err(|_| ProtoError::LengthOverflow)?;
        if declared > MAX_FRAME_BYTES {
            return Err(ProtoError::OversizedFrame {
                len: declared,
                max: MAX_FRAME_BYTES,
            });
        }
        if declared < FRAME_HEADER_LEN {
            return Err(ProtoError::Truncated {
                context: "frame length shorter than the header",
                need: FRAME_HEADER_LEN,
                have: declared,
            });
        }
        Ok(FrameHeader {
            len,
            frame_type,
            flags,
            corr,
        })
    }
}

/// The connection mode decided by the first bytes (plan v4 §B.1).
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Mode {
    /// JSON lines for the whole connection.
    Json,
    /// Binary frames, with the version the hello announced.
    Binary {
        /// The protocol major.
        major: u16,
        /// The protocol minor.
        minor: u16,
    },
}

/// Detect the connection mode and refuse an unsupported protocol major.
///
/// # Errors
///
/// [`ProtoError::UnknownMode`] for a first byte that is neither `{` nor the binary magic,
/// [`ProtoError::Truncated`] for a prefix of the binary magic, and
/// [`ProtoError::UnsupportedVersion`] for a major this codec does not implement.
pub fn detect_mode(bytes: &[u8]) -> Result<Mode, ProtoError> {
    match bytes.first() {
        Some(b'{') => Ok(Mode::Json),
        Some(byte) => {
            // A short input that is a prefix of the binary magic is a truncated hello, not
            // a different mode: it is reported as truncation so a stream defect is visible.
            if bytes.len() < MAGIC.len() {
                if MAGIC.get(..bytes.len()) == Some(bytes) {
                    return Err(ProtoError::Truncated {
                        context: "binary hello",
                        need: MAGIC.len(),
                        have: bytes.len(),
                    });
                }
                return Err(ProtoError::UnknownMode { first: Some(*byte) });
            }
            let mut cursor = Cursor::new(bytes, "binary hello");
            let magic = cursor.take_bytes(4)?;
            if magic != MAGIC {
                return Err(ProtoError::UnknownMode { first: Some(*byte) });
            }
            let major = cursor.u16()?;
            let minor = cursor.u16()?;
            if major != PROTO_MAJOR {
                return Err(ProtoError::UnsupportedVersion { major });
            }
            Ok(Mode::Binary { major, minor })
        }
        None => Err(ProtoError::UnknownMode { first: None }),
    }
}

/// The hello prefix of the binary mode: magic plus version.
#[must_use]
pub fn hello() -> Vec<u8> {
    let mut writer = Writer::with_capacity(8);
    writer.bytes(&MAGIC);
    writer.u16(PROTO_MAJOR);
    writer.u16(PROTO_MINOR);
    writer.finish()
}

/// Encode the header section body.
#[must_use]
pub fn encode_header_body(header: &BlockHeaderFacts) -> Vec<u8> {
    let mut writer = Writer::with_capacity(HEADER_BODY_LEN);
    writer.bytes(&header.boot_id);
    writer.u64(header.epoch);
    writer.u64(header.seq);
    writer.u64(header.mono_start_ns);
    writer.u64(header.mono_end_ns);
    writer.u64(header.boot_ns);
    writer.i64(header.real_ns);
    writer.u32(header.dict_gen);
    writer.u32(header.n_rows);
    writer.u32(header.dropped);
    writer.u32(header.flags);
    writer.finish()
}

fn decode_header_body(body: &[u8]) -> Result<BlockHeaderFacts, ProtoError> {
    if body.len() != HEADER_BODY_LEN {
        return Err(ProtoError::SectionLength {
            kind: SectionKind::Header.code(),
            declared: body.len(),
            expected: HEADER_BODY_LEN,
        });
    }
    let mut cursor = Cursor::new(body, "header section");
    let mut boot_id = [0u8; 16];
    boot_id.copy_from_slice(cursor.take_bytes(16)?);
    let epoch = cursor.u64()?;
    let seq = cursor.u64()?;
    let mono_start_ns = cursor.u64()?;
    let mono_end_ns = cursor.u64()?;
    let boot_ns = cursor.u64()?;
    let real_ns = cursor.i64()?;
    let dict_gen = cursor.u32()?;
    let n_rows = cursor.u32()?;
    let dropped = cursor.u32()?;
    let flags = cursor.u32()?;
    if flags != 0 {
        return Err(ProtoError::UnknownSectionFlags {
            kind: SectionKind::Header.code(),
            flags,
        });
    }
    // The length check above is exact and every field is fixed width, so the body is
    // consumed entirely here; there is no trailing-byte case left to test for.
    debug_assert_eq!(cursor.remaining(), 0, "the header body is fully consumed");
    Ok(BlockHeaderFacts {
        boot_id,
        epoch,
        seq,
        mono_start_ns,
        mono_end_ns,
        boot_ns,
        real_ns,
        dict_gen,
        n_rows,
        dropped,
        flags,
    })
}

/// Encode the rows section body for `rows`.
#[must_use]
pub fn encode_rows_body(rows: &[Row]) -> Vec<u8> {
    let mut writer = Writer::with_capacity(rows.len() * 8);
    for row in rows {
        writer.u32(row.ent);
    }
    for row in rows {
        writer.u32(row.t_off_us);
    }
    writer.finish()
}

fn decode_rows_body(body: &[u8], n_rows: usize) -> Result<Vec<Row>, ProtoError> {
    let expected = n_rows.checked_mul(8).ok_or(ProtoError::LengthOverflow)?;
    if body.len() != expected {
        return Err(ProtoError::SectionLength {
            kind: SectionKind::Rows.code(),
            declared: body.len(),
            expected,
        });
    }
    let mut cursor = Cursor::new(body, "rows section");
    let mut rows = Vec::with_capacity(n_rows);
    for _ in 0..n_rows {
        rows.push(Row {
            ent: cursor.u32()?,
            t_off_us: 0,
        });
    }
    for row in &mut rows {
        row.t_off_us = cursor.u32()?;
    }
    Ok(rows)
}

fn column_body_len(n_rows: usize) -> Result<usize, ProtoError> {
    let raw = n_rows
        .checked_mul(9)
        .and_then(|value| value.checked_add(8))
        .ok_or(ProtoError::LengthOverflow)?;
    crate::read::align_up(raw, 8)
}

/// Encode one column section body.
///
/// # Errors
///
/// [`ProtoError::ColumnReserved`] is never produced here (the writer always pads with
/// zeros); the only failure is a length overflow.
pub fn encode_column_body(column: &Column) -> Result<Vec<u8>, ProtoError> {
    let n_rows = column.rows();
    let capacity = column_body_len(n_rows)?;
    let mut writer = Writer::with_capacity(capacity);
    writer.u32(column.metric);
    writer.u8(column.value_type as u8);
    writer.u8(column.kind as u8);
    writer.u16(0);
    for value in &column.values {
        writer.u64(*value);
    }
    writer.bytes(&column.quality);
    writer.pad_to(8);
    Ok(writer.finish())
}

fn decode_column_body(body: &[u8], n_rows: usize) -> Result<Column, ProtoError> {
    let expected = column_body_len(n_rows)?;
    if body.len() != expected {
        return Err(ProtoError::SectionLength {
            kind: SectionKind::Column.code(),
            declared: body.len(),
            expected,
        });
    }
    let mut cursor = Cursor::new(body, "column section");
    let metric = cursor.u32()?;
    let value_type = MetricValueType::from_code(cursor.u8()?)?;
    let kind = MetricKind::from_code(cursor.u8()?)?;
    let pad = cursor.u16()?;
    if pad != 0 {
        return Err(ProtoError::ColumnReserved { metric, pad });
    }
    let mut values = Vec::with_capacity(n_rows);
    for _ in 0..n_rows {
        values.push(cursor.u64()?);
    }
    let quality = cursor.take_bytes(n_rows)?.to_vec();
    for (row, code) in quality.iter().enumerate() {
        Quality::from_code(*code, row)?;
    }
    cursor.pad_to_end(8)?;
    Column::new(metric, value_type, kind, values, quality)
}

/// Bytes one time bucket occupies in the (column-wise) time section body: `i64 real_ns` (8),
/// `u32 covered_ms` (4), `u16 samples` (2) and `u8 agg` (1). The body is these four columns
/// back to back, padded to eight bytes as a whole.
const TIME_BUCKET_BYTES: usize = 8 + 4 + 2 + 1;

fn time_body_len(buckets: usize) -> Result<usize, ProtoError> {
    let raw = buckets
        .checked_mul(TIME_BUCKET_BYTES)
        .ok_or(ProtoError::LengthOverflow)?;
    crate::read::align_up(raw, 8)
}

/// Encode the time section body of a history block.
///
/// # Errors
///
/// [`ProtoError::LengthOverflow`] only.
pub fn encode_time_body(time: &[TimeBucket]) -> Result<Vec<u8>, ProtoError> {
    let capacity = time_body_len(time.len())?;
    let mut writer = Writer::with_capacity(capacity);
    for bucket in time {
        writer.i64(bucket.real_ns);
    }
    for bucket in time {
        writer.u32(bucket.covered_ms);
    }
    for bucket in time {
        writer.u16(bucket.samples);
    }
    for bucket in time {
        writer.u8(bucket.agg as u8);
    }
    writer.pad_to(8);
    Ok(writer.finish())
}

fn decode_time_body(body: &[u8], n_buckets: usize) -> Result<Vec<TimeBucket>, ProtoError> {
    let expected = time_body_len(n_buckets)?;
    if body.len() != expected {
        return Err(ProtoError::SectionLength {
            kind: SectionKind::Time.code(),
            declared: body.len(),
            expected,
        });
    }
    let mut cursor = Cursor::new(body, "time section");
    let mut real_ns = Vec::with_capacity(n_buckets);
    for _ in 0..n_buckets {
        real_ns.push(cursor.i64()?);
    }
    let mut covered_ms = Vec::with_capacity(n_buckets);
    for _ in 0..n_buckets {
        covered_ms.push(cursor.u32()?);
    }
    let mut samples = Vec::with_capacity(n_buckets);
    for _ in 0..n_buckets {
        samples.push(cursor.u16()?);
    }
    let mut agg = Vec::with_capacity(n_buckets);
    for _ in 0..n_buckets {
        agg.push(Aggregation::from_code(cursor.u8()?)?);
    }
    cursor.pad_to_end(8)?;
    Ok(real_ns
        .into_iter()
        .zip(covered_ms)
        .zip(samples)
        .zip(agg)
        .map(|(((real_ns, covered_ms), samples), agg)| TimeBucket {
            real_ns,
            covered_ms,
            samples,
            agg,
        })
        .collect())
}

/// The canonical position of a section kind in a block directory (§B.2).
///
/// `encode_block` emits exactly one order — header, dictionary, rows *or* time, then
/// columns — and both the encoder and the decoder require it. Without that requirement
/// two different byte strings (a directory reordering with its bodies moved along) would
/// decode to one value, which contradicts §B.5's byte-identity goal. An unknown optional
/// section is exempt: it is outside this version's layout by definition, and a decoder
/// that skips it cannot re-encode it byte-identically anyway.
fn canonical_rank(kind: SectionKind) -> u8 {
    match kind {
        SectionKind::Header => 0,
        SectionKind::Dictionary => 1,
        SectionKind::Subject => 2,
        SectionKind::Rows | SectionKind::Time => 3,
        SectionKind::Column => 4,
    }
}

/// Encode the subject section body: `u32 ent | u32 0`.
#[must_use]
pub fn encode_subject_body(ent: u32) -> Vec<u8> {
    let mut writer = Writer::with_capacity(SUBJECT_BODY_LEN);
    writer.u32(ent);
    writer.u32(0);
    writer.finish()
}

fn decode_subject_body(body: &[u8]) -> Result<u32, ProtoError> {
    if body.len() != SUBJECT_BODY_LEN {
        return Err(ProtoError::SectionLength {
            kind: SectionKind::Subject.code(),
            declared: body.len(),
            expected: SUBJECT_BODY_LEN,
        });
    }
    let mut cursor = Cursor::new(body, "subject section");
    let ent = cursor.u32()?;
    let reserved = cursor.u32()?;
    if reserved != 0 {
        return Err(ProtoError::UnknownSectionFlags {
            kind: SectionKind::Subject.code(),
            flags: reserved,
        });
    }
    Ok(ent)
}

/// Check that every **known** section appears in the canonical order.
fn check_canonical_order(sections: &[Section]) -> Result<(), ProtoError> {
    let mut last: Option<u8> = None;
    for (index, section) in sections.iter().enumerate() {
        let Some(kind) = SectionKind::from_code(section.kind_code()) else {
            continue;
        };
        let rank = canonical_rank(kind);
        if let Some(previous) = last {
            if rank < previous {
                return Err(ProtoError::SectionOrder {
                    index,
                    kind: section.kind_code(),
                });
            }
        }
        last = Some(rank);
    }
    Ok(())
}

/// Encode a block (with its frame) as canonical bytes.
///
/// # Errors
///
/// [`ProtoError::NotABlock`] when `block.frame_type` is not [`FrameType::Block`],
/// [`ProtoError::SectionOrder`], [`ProtoError::OversizedFrame`],
/// [`ProtoError::RowsAndTime`], [`ProtoError::DuplicateSection`],
/// [`ProtoError::SectionLength`] (a column whose row count differs from `header.n_rows`),
/// [`ProtoError::SubjectWithoutTime`], [`ProtoError::AggregationNotPermitted`] (a bucket
/// whose aggregation a column's kind does not permit) and every dictionary-encoding refusal.
pub fn encode_block(block: &Block) -> Result<Vec<u8>, ProtoError> {
    // §B.1 gives each frame type one payload shape. A `REQ`/`REP`/`EVT` carries a small
    // JSON control object and a `DICT` a dictionary, so a block section list under any
    // other type is a producer defect: refuse it here rather than let a caller write
    // measurements under the wrong envelope.
    if block.frame_type != FrameType::Block {
        return Err(ProtoError::NotABlock {
            frame_type: block.frame_type.code(),
        });
    }
    if !block.rows.is_empty() && !block.time.is_empty() {
        return Err(ProtoError::RowsAndTime {
            rows: block.rows.len(),
            buckets: block.time.len(),
        });
    }
    let n_rows = usize::try_from(block.header.n_rows).map_err(|_| ProtoError::LengthOverflow)?;
    // The encoder refuses the shape the decoder refuses: values with no entity and no bucket
    // have no place in a block, so a non-zero row count requires one of the two sections.
    if n_rows > 0 && block.rows.is_empty() && block.time.is_empty() {
        return Err(ProtoError::NoRowsOrTime {
            n_rows: block.header.n_rows,
        });
    }
    if !block.rows.is_empty() && block.rows.len() != n_rows {
        return Err(ProtoError::SectionLength {
            kind: SectionKind::Rows.code(),
            declared: block.rows.len(),
            expected: n_rows,
        });
    }
    if !block.time.is_empty() && block.time.len() != n_rows {
        return Err(ProtoError::SectionLength {
            kind: SectionKind::Time.code(),
            declared: block.time.len(),
            expected: n_rows,
        });
    }
    if let Some(subject) = block.subject {
        if block.time.is_empty() {
            return Err(ProtoError::SubjectWithoutTime { subject });
        }
    }
    // The encoder refuses what the decoder refuses (§B.5): a bucket whose aggregation a column's
    // kind does not permit. The mask follows the column's own kind, so no dictionary is needed.
    // A subject that does not name an entity cannot be checked here: the block may carry no
    // dictionary, and the decoder judges it against the dictionary that is in force.
    for bucket in &block.time {
        for column in &block.columns {
            if column.kind.aggregation_mask() & bucket.agg.bit() == 0 {
                return Err(ProtoError::AggregationNotPermitted {
                    metric: column.metric,
                    agg: bucket.agg as u8,
                });
            }
        }
    }
    let mut sections = Vec::new();
    sections.push(Section::new(
        SectionKind::Header,
        encode_header_body(&block.header),
    ));
    if let Some(dictionary) = &block.dictionary {
        sections.push(Section::new(SectionKind::Dictionary, dictionary.encode()?));
    }
    if let Some(ent) = block.subject {
        let body = encode_subject_body(ent);
        sections.push(Section::new(SectionKind::Subject, body));
    }
    if !block.rows.is_empty() {
        sections.push(Section::new(
            SectionKind::Rows,
            encode_rows_body(&block.rows),
        ));
    }
    if !block.time.is_empty() {
        sections.push(Section::new(
            SectionKind::Time,
            encode_time_body(&block.time)?,
        ));
    }
    for column in &block.columns {
        if column.rows() != n_rows {
            return Err(ProtoError::SectionLength {
                kind: SectionKind::Column.code(),
                declared: column.rows(),
                expected: n_rows,
            });
        }
        sections.push(Section::new(
            SectionKind::Column,
            encode_column_body(column)?,
        ));
    }
    encode_frame(block.frame_type, block.corr, &sections)
}

/// Encode a frame of any type from ordered sections.
///
/// # Errors
///
/// [`ProtoError::MissingSection`] when the required header section is absent,
/// [`ProtoError::SectionOrder`] for known sections in a non-canonical order,
/// [`ProtoError::DuplicateSection`] for a repeated single-instance kind,
/// [`ProtoError::SectionLength`] for a body whose length is not eight-aligned, and
/// [`ProtoError::OversizedFrame`] beyond [`MAX_FRAME_BYTES`].
pub fn encode_frame(
    frame_type: FrameType,
    corr: u32,
    sections: &[Section],
) -> Result<Vec<u8>, ProtoError> {
    if !sections
        .iter()
        .any(|section| section.kind_code() == SectionKind::Header.code())
    {
        return Err(ProtoError::MissingSection {
            kind: SectionKind::Header.code(),
        });
    }
    // The producer contract is the decoder's rule: one canonical order, regardless of
    // which frame type is being written. A caller that wants an out-of-order fixture
    // patches the canonical bytes instead (that is what a hostile sender does).
    check_canonical_order(sections)?;
    let mut seen: Vec<UnknownSectionKind> = Vec::with_capacity(sections.len());
    for section in sections {
        if let Some(kind) = SectionKind::from_code(section.kind_code()) {
            if kind.is_single() && seen.contains(&section.kind_code()) {
                return Err(ProtoError::DuplicateSection {
                    kind: section.kind_code(),
                });
            }
        }
        seen.push(section.kind_code());
        if section.body.len() % 8 != 0 {
            return Err(ProtoError::SectionLength {
                kind: section.kind_code(),
                declared: section.body.len(),
                expected: crate::read::align_up(section.body.len(), 8)?,
            });
        }
    }
    let dir_len = sections
        .len()
        .checked_mul(SECTION_DIR_ENTRY_LEN)
        .ok_or(ProtoError::LengthOverflow)?;
    let mut total = FRAME_HEADER_LEN
        .checked_add(4)
        .ok_or(ProtoError::LengthOverflow)?;
    total = total
        .checked_add(dir_len)
        .ok_or(ProtoError::LengthOverflow)?;
    for section in sections {
        total = total
            .checked_add(section.body.len())
            .ok_or(ProtoError::LengthOverflow)?;
    }
    if total > MAX_FRAME_BYTES {
        return Err(ProtoError::OversizedFrame {
            len: total,
            max: MAX_FRAME_BYTES,
        });
    }
    let declared_len = u32::try_from(total).map_err(|_| ProtoError::OversizedFrame {
        len: total,
        max: MAX_FRAME_BYTES,
    })?;
    let mut writer = Writer::with_capacity(total);
    writer.u32(declared_len);
    writer.u8(frame_type.code());
    writer.u8(FLAG_SECTIONED);
    writer.u16(0);
    writer.u32(corr);
    writer.u16(
        u16::try_from(sections.len())
            .map_err(|_| ProtoError::RowCountOverflow(sections.len() as u64))?,
    );
    writer.u16(0);
    for section in sections {
        writer.u16(section.kind_code());
        writer.u8(section.version);
        writer.u8(section.flags);
        writer.u32(u32::try_from(section.body.len()).map_err(|_| ProtoError::LengthOverflow)?);
    }
    for section in sections {
        writer.bytes(&section.body);
    }
    debug_assert_eq!(writer.len(), total);
    Ok(writer.finish())
}

/// Decode a whole frame into a block.
///
/// # Errors
///
/// Every malformed frame is a typed refusal; nothing is silently skipped except an
/// unknown **optional** section, which is reported in [`Block::skipped_sections`].
pub fn decode_block(bytes: &[u8]) -> Result<Block, ProtoError> {
    decode_block_with(bytes, None)
}

/// Decode a whole frame into a block, judging a block without an in-frame dictionary against
/// the dictionary generation the stream holds (WIRE.md §6.1, HOB-46).
///
/// With an in-frame dictionary the checks are those of [`decode_block`] and `held` is not
/// consulted. Without one and with `held` given, the header's `dict_gen` must equal the held
/// generation (`stale-dictionary-generation`) and every reference and column kind is checked
/// against it. Without one and with `held` of `None` no generation or reference check is
/// possible and none is made (the stream layer owns that judgement, HOB-54). Every bucket's
/// aggregation is checked against its column's own kind in all three cases.
///
/// # Errors
///
/// Every malformed frame is a typed refusal, in the order of WIRE.md §3.10.
pub fn decode_block_with(bytes: &[u8], held: Option<&Dictionary>) -> Result<Block, ProtoError> {
    let header_frame = FrameHeader::parse(bytes)?;
    // §B.1: only `BLOCK` carries block sections. A `REQ`/`REP`/`EVT` payload is a small
    // JSON control object and a `DICT` payload is a dictionary, so a block-shaped frame
    // under another type is type/payload confusion, refused before any section is read.
    if header_frame.frame_type != FrameType::Block {
        return Err(ProtoError::NotABlock {
            frame_type: header_frame.frame_type.code(),
        });
    }
    let declared = usize::try_from(header_frame.len).map_err(|_| ProtoError::LengthOverflow)?;
    if declared > MAX_FRAME_BYTES {
        return Err(ProtoError::OversizedFrame {
            len: declared,
            max: MAX_FRAME_BYTES,
        });
    }
    if bytes.len() < declared {
        return Err(ProtoError::Truncated {
            context: "frame",
            need: declared,
            have: bytes.len(),
        });
    }
    if bytes.len() > declared {
        return Err(ProtoError::TrailingBytes {
            extra: bytes.len() - declared,
        });
    }
    if header_frame.flags & FLAG_SECTIONED == 0 {
        return Err(ProtoError::MissingSection {
            kind: SectionKind::Header.code(),
        });
    }
    // `FrameHeader::parse` has already consumed these bytes, so the slice exists; using
    // `.get(..)` keeps the crate's "no indexing on untrusted input" rule literal.
    let directory_bytes = bytes.get(FRAME_HEADER_LEN..).ok_or(ProtoError::Truncated {
        context: "frame",
        need: FRAME_HEADER_LEN,
        have: bytes.len(),
    })?;
    let mut cursor = Cursor::new(directory_bytes, "block directory");
    let n_sec = cursor.u16()?;
    let reserved = cursor.u16()?;
    if reserved != 0 {
        return Err(ProtoError::SectionReserved(reserved));
    }
    // HOB-50: the directory bytes are proven present before storage is reserved for them.
    let dir_len = usize::from(n_sec)
        .checked_mul(SECTION_DIR_ENTRY_LEN)
        .ok_or(ProtoError::LengthOverflow)?;
    if cursor.remaining() < dir_len {
        return Err(ProtoError::Truncated {
            context: "block directory",
            need: dir_len,
            have: cursor.remaining(),
        });
    }
    let mut directory = Vec::with_capacity(usize::from(n_sec));
    for _ in 0..n_sec {
        let kind = cursor.u16()?;
        let version = cursor.u8()?;
        let flags = cursor.u8()?;
        let len = cursor.u32()?;
        directory.push((kind, version, flags, len));
    }
    // Every section length is a multiple of 8, unknown optional kinds included (HOB-51); it is
    // judged before the order, as WIRE.md §3.10 lists the directory checks.
    for (kind, _version, _flags, len) in &directory {
        if !len.is_multiple_of(8) {
            let declared = usize::try_from(*len).map_err(|_| ProtoError::LengthOverflow)?;
            return Err(ProtoError::SectionLength {
                kind: *kind,
                declared,
                expected: crate::read::align_up(declared, 8)?,
            });
        }
    }
    // The canonical order is the encoder's, checked before any body is decoded: a
    // directory reordering of otherwise valid sections would produce a second byte string
    // for one value (§B.5). An unknown optional section is exempt — it is outside this
    // version's layout and is skipped by its declared length.
    let mut last_rank: Option<u8> = None;
    for (index, (kind, _version, _flags, _len)) in directory.iter().enumerate() {
        let Some(section) = SectionKind::from_code(*kind) else {
            continue;
        };
        let rank = canonical_rank(section);
        if let Some(previous) = last_rank {
            if rank < previous {
                return Err(ProtoError::SectionOrder { index, kind: *kind });
            }
        }
        last_rank = Some(rank);
    }
    // Entry 0 is the header (WIRE.md §3.1). A known non-header kind there is already a rank
    // violation (or a missing header), so only an unknown kind is left to refuse here.
    if let Some((kind, _version, _flags, _len)) = directory.first() {
        if SectionKind::from_code(*kind).is_none() {
            return Err(ProtoError::SectionOrder {
                index: 0,
                kind: *kind,
            });
        }
    }

    let mut body_pos = FRAME_HEADER_LEN
        .checked_add(cursor.position())
        .ok_or(ProtoError::LengthOverflow)?;
    let mut bodies = Vec::with_capacity(directory.len());
    for (kind, _version, _flags, len) in &directory {
        let length = usize::try_from(*len).map_err(|_| ProtoError::LengthOverflow)?;
        let end = body_pos
            .checked_add(length)
            .ok_or(ProtoError::LengthOverflow)?;
        let body = bytes.get(body_pos..end).ok_or(ProtoError::Truncated {
            context: "section body",
            need: end,
            have: bytes.len(),
        })?;
        let _ = *kind;
        bodies.push(body);
        body_pos = end;
    }
    if body_pos != bytes.len() {
        return Err(ProtoError::TrailingBytes {
            extra: bytes.len() - body_pos,
        });
    }

    // Pass one: the header, and the directory's own validity. A block's other sections
    // are sized by the row count the header declares, so nothing else can be decoded
    // before it — and a required unknown section must be refused wherever it appears.
    let mut header_facts: Option<BlockHeaderFacts> = None;
    let mut skipped_sections = Vec::new();
    for (kind, version, flags, _len) in &directory {
        if *flags & !1 != 0 {
            return Err(ProtoError::UnknownSectionFlags {
                kind: *kind,
                // A directory flag byte widened to the same field type the block header uses.
                flags: u32::from(*flags),
            });
        }
        match SectionKind::from_code(*kind) {
            None => {
                if *flags & 1 == 1 {
                    return Err(ProtoError::UnsupportedRequiredSection { kind: *kind });
                }
                skipped_sections.push(*kind);
            }
            Some(section) => {
                if *version != section.version() {
                    return Err(ProtoError::UnsupportedSectionVersion {
                        kind: section.code(),
                        version: *version,
                        supported: section.version(),
                    });
                }
                // The header and the subject are required sections: their directory entry
                // carries the required flag (WIRE.md §3.1, §3.8), and a clear flag is refused
                // as a flags defect of that section.
                if section.is_required() && *flags & 1 == 0 {
                    return Err(ProtoError::UnknownSectionFlags {
                        kind: section.code(),
                        flags: u32::from(*flags),
                    });
                }
                if section == SectionKind::Header && header_facts.is_none() {
                    // The body index matches the directory order.
                    let index = directory
                        .iter()
                        .position(|entry| entry.0 == *kind)
                        .unwrap_or(0);
                    header_facts = Some(decode_header_body(
                        bodies.get(index).copied().unwrap_or(&[]),
                    )?);
                }
            }
        }
    }
    let Some(header) = header_facts else {
        return Err(ProtoError::MissingSection {
            kind: SectionKind::Header.code(),
        });
    };

    let n_rows = usize::try_from(header.n_rows).map_err(|_| ProtoError::LengthOverflow)?;
    let mut rows: Vec<Row> = Vec::new();
    let mut time: Vec<TimeBucket> = Vec::new();
    let mut columns: Vec<Column> = Vec::new();
    let mut seen_metrics: HashSet<u32> = HashSet::new();
    let mut dictionary: Option<Dictionary> = None;
    let mut subject: Option<u32> = None;
    let mut seen_single: Vec<UnknownSectionKind> = Vec::new();
    let mut saw_rows = false;
    let mut saw_time = false;
    for ((kind, _version, flags, _len), body) in directory.iter().zip(bodies.iter()) {
        let Some(section) = SectionKind::from_code(*kind) else {
            continue;
        };
        if section.is_single() {
            if seen_single.contains(&section.code()) {
                return Err(ProtoError::DuplicateSection {
                    kind: section.code(),
                });
            }
            seen_single.push(section.code());
        }
        let _ = *flags;
        match section {
            SectionKind::Header => {}
            SectionKind::Rows => {
                saw_rows = true;
                rows = decode_rows_body(body, n_rows)?;
            }
            SectionKind::Time => {
                saw_time = true;
                time = decode_time_body(body, n_rows)?;
            }
            SectionKind::Column => {
                let column = decode_column_body(body, n_rows)?;
                if !seen_metrics.insert(column.metric) {
                    return Err(ProtoError::ConflictingColumn {
                        metric: column.metric,
                    });
                }
                columns.push(column);
            }
            SectionKind::Dictionary => {
                dictionary = Some(Dictionary::decode(body, crate::dict::DEFAULT_ACTIVE_MAX)?)
            }
            SectionKind::Subject => subject = Some(decode_subject_body(body)?),
        }
    }
    // Rows and buckets are exclusive by presence, not by count: a block carrying both
    // sections is refused even when both are empty (that is the n_rows == 0 case), so the
    // two layouts can never be mixed by accident.
    if saw_rows && saw_time {
        return Err(ProtoError::RowsAndTime {
            rows: rows.len(),
            buckets: time.len(),
        });
    }
    // Values without an entity or a bucket have no place in a block: a declared row count
    // with neither section would hand the consumer eight-byte values it cannot address.
    if n_rows > 0 && !saw_rows && !saw_time {
        return Err(ProtoError::NoRowsOrTime {
            n_rows: header.n_rows,
        });
    }
    // A subject names the incarnation a history block describes: it needs the time section.
    if let Some(ent) = subject {
        if !saw_time {
            return Err(ProtoError::SubjectWithoutTime { subject: ent });
        }
    }
    // The in-frame dictionary wins; a bare block is judged against the held one (HOB-46).
    if let Some(table) = dictionary.as_ref().or(held) {
        table.require_generation(header.dict_gen)?;
        if let Some(ent) = subject {
            match table.lookup(ent)? {
                Entry::Entity { .. } => {}
                _ => return Err(ProtoError::RowReferenceNotEntity { index: ent }),
            }
        }
        // A row addresses an incarnation and a column addresses a metric. A reference of
        // the right range but the wrong entry kind would make a consumer read a label's
        // text as a cgroup id or a metric's facts as a path, so the kind is checked too.
        for row in &rows {
            match table.lookup(row.ent)? {
                Entry::Entity { .. } => {}
                _ => return Err(ProtoError::RowReferenceNotEntity { index: row.ent }),
            }
        }
        for column in &columns {
            match table.lookup(column.metric)? {
                Entry::Metric { facts, .. } => {
                    // The column header and the dictionary both declare the arithmetic kind.
                    // They must agree: a consumer aggregates by one of them, and a
                    // disagreement silently picks whichever it trusts.
                    if column.kind != facts.kind {
                        return Err(ProtoError::ColumnKindMismatch {
                            index: column.metric,
                            declared: column.kind as u8,
                            dictionary: facts.kind as u8,
                        });
                    }
                }
                _ => {
                    return Err(ProtoError::ColumnReferenceNotMetric {
                        index: column.metric,
                    })
                }
            }
        }
    }
    // Every bucket's aggregation must be permitted by every column's mask (HOB-47). The mask is
    // the column's own kind's: with a dictionary the loop above has just proven that kind equal
    // to the metric's, and without one the column kind is all there is, so the check also runs
    // for a bare block with nothing held.
    for bucket in &time {
        for column in &columns {
            if column.kind.aggregation_mask() & bucket.agg.bit() == 0 {
                return Err(ProtoError::AggregationNotPermitted {
                    metric: column.metric,
                    agg: bucket.agg as u8,
                });
            }
        }
    }
    Ok(Block {
        frame_type: header_frame.frame_type,
        corr: header_frame.corr,
        header,
        rows,
        time,
        columns,
        dictionary,
        subject,
        skipped_sections,
    })
}

#[cfg(test)]
mod tests {
    use super::{
        decode_block, detect_mode, encode_block, encode_frame, hello, Block, BlockHeaderFacts,
        Column, FrameHeader, FrameType, Mode, Row, Section, SectionKind, TimeBucket, TypedValue,
        FRAME_HEADER_LEN, HEADER_BODY_LEN, MAGIC, MAX_FRAME_BYTES, PROTO_MAJOR, PROTO_MINOR,
    };
    use crate::dict::{
        Aggregation, Dictionary, Entry, MetricFacts, MetricKind, MetricScope, MetricSource,
        MetricUnit, MetricValueType,
    };
    use crate::error::ProtoError;
    use crate::quality::Quality;

    fn facts() -> BlockHeaderFacts {
        BlockHeaderFacts {
            boot_id: [
                0x00, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88, 0x99, 0xaa, 0xbb, 0xcc, 0xdd,
                0xee, 0xff,
            ],
            epoch: 7,
            seq: 42,
            mono_start_ns: 1_000_000_000,
            mono_end_ns: 2_000_000_000,
            boot_ns: 500,
            real_ns: 1_700_000_000_000_000_000,
            dict_gen: 3,
            n_rows: 2,
            dropped: 1,
            flags: 0,
        }
    }

    fn dictionary() -> Dictionary {
        let mut dictionary = Dictionary::new(3);
        dictionary
            .intern(Entry::Entity {
                inc: 11,
                label: "/a".into(),
            })
            .expect("intern");
        dictionary
            .intern(Entry::Entity {
                inc: 12,
                label: "/b".into(),
            })
            .expect("intern");
        dictionary
            .intern(Entry::Metric {
                facts: MetricFacts {
                    unit: MetricUnit::Microseconds,
                    kind: MetricKind::Counter,
                    scope: MetricScope::Cgroup,
                    source: MetricSource::CgroupFs,
                },
                name: "cpu.usage_usec".into(),
            })
            .expect("intern");
        dictionary
            .intern(Entry::Metric {
                facts: MetricFacts {
                    unit: MetricUnit::Bytes,
                    kind: MetricKind::Gauge,
                    scope: MetricScope::Cgroup,
                    source: MetricSource::CgroupFs,
                },
                name: "mem.current".into(),
            })
            .expect("intern");
        dictionary
    }

    fn live_block() -> Block {
        Block {
            frame_type: FrameType::Block,
            corr: 99,
            header: facts(),
            rows: vec![
                Row {
                    ent: 0,
                    t_off_us: 0,
                },
                Row {
                    ent: 1,
                    t_off_us: 20_000,
                },
            ],
            time: Vec::new(),
            columns: vec![
                Column::new(
                    2,
                    MetricValueType::U64,
                    MetricKind::Counter,
                    vec![1000, u64::MAX],
                    vec![Quality::Ok.code(), Quality::Ok.code()],
                )
                .expect("column"),
                Column::new(
                    3,
                    MetricValueType::F64,
                    MetricKind::Gauge,
                    vec![0x4059000000000000, 0],
                    vec![Quality::Ok.code(), Quality::Absent.code()],
                )
                .expect("column"),
            ],
            dictionary: Some(dictionary()),
            subject: None,
            skipped_sections: Vec::new(),
        }
    }

    #[test]
    fn a_live_block_round_trips_byte_exactly() {
        let block = live_block();
        let bytes = encode_block(&block).expect("encode");
        let decoded = decode_block(&bytes).expect("decode");
        assert_eq!(decoded, block);
        let reencoded = encode_block(&decoded).expect("encode again");
        assert_eq!(
            reencoded, bytes,
            "encoding is the exact inverse of decoding"
        );
    }

    #[test]
    fn values_keep_their_declared_type_their_bits_and_their_quality() {
        let decoded = decode_block(&encode_block(&live_block()).expect("encode")).expect("decode");
        let counter = decoded.columns.first().expect("counter column");
        assert_eq!(counter.value_type, MetricValueType::U64);
        assert_eq!(counter.sample(0).expect("row").value, TypedValue::U64(1000));
        assert_eq!(
            counter.sample(1).expect("row").value,
            TypedValue::U64(u64::MAX),
            "2^64-1 survives exactly; it would not survive an f64 round trip"
        );
        let gauge = decoded.columns.get(1).expect("gauge column");
        assert_eq!(gauge.sample(0).expect("row").value, TypedValue::F64(100.0));
        let absent = gauge.sample(1).expect("row");
        assert_eq!(absent.quality, Quality::Absent);
        assert_eq!(
            absent.value.measured(absent.quality),
            None,
            "an absence is not a reading"
        );
        assert_eq!(
            counter.sample(0).expect("row").value.measured(Quality::Ok),
            Some(TypedValue::U64(1000))
        );
        assert_eq!(
            gauge.sample(2),
            Err(ProtoError::Truncated {
                context: "column values",
                need: 3,
                have: 2
            })
        );
    }

    #[test]
    fn a_row_index_past_the_end_is_refused_without_arithmetic_overflow() {
        // `row` is caller-supplied, so `row + 1` must not overflow: at usize::MAX the
        // reported need is a typed length refusal, not a panic in debug or a wrap in release.
        let block = live_block();
        let column = &block.columns[0];
        assert_eq!(
            column.sample(usize::MAX),
            Err(ProtoError::LengthOverflow),
            "the row index itself is out of range before any addition"
        );
        assert_eq!(
            column.sample(2).map(|sample| sample.value),
            Err(ProtoError::Truncated {
                context: "column values",
                need: 3,
                have: 2,
            })
        );
    }

    #[test]
    fn the_frame_header_is_twelve_bytes_and_carries_the_whole_length() {
        let bytes = encode_block(&live_block()).expect("encode");
        let header = FrameHeader::parse(&bytes).expect("header");
        assert_eq!(header.len as usize, bytes.len());
        assert_eq!(header.frame_type, FrameType::Block);
        assert_eq!(header.corr, 99);
        assert_eq!(header.flags, super::FLAG_SECTIONED);
        assert_eq!(
            bytes.len() % 8,
            0,
            "bodies are eight-aligned so the frame is too"
        );
        assert_eq!(super::encode_header_body(&facts()).len(), HEADER_BODY_LEN);
        assert_eq!(FRAME_HEADER_LEN, 12);
    }

    /// Twelve header bytes declaring `len`, a valid `BLOCK` type and sectioned flag.
    fn header_declaring(len: u32) -> [u8; FRAME_HEADER_LEN] {
        let mut bytes = [0u8; FRAME_HEADER_LEN];
        bytes[0..4].copy_from_slice(&len.to_le_bytes());
        bytes[4] = FrameType::Block.code();
        bytes[5] = super::FLAG_SECTIONED;
        bytes
    }

    #[test]
    fn parse_itself_bounds_the_declared_length_at_both_ends() {
        // HOB-8: a streaming reader allocates `len` bytes right after `parse`, so the
        // bound must hold here, not only in `decode_block`. Both bounds are inclusive.
        let max = u32::try_from(MAX_FRAME_BYTES).expect("bounded constant");
        let header_len = u32::try_from(FRAME_HEADER_LEN).expect("header length");

        let at_max = FrameHeader::parse(&header_declaring(max)).expect("exactly the bound");
        assert_eq!(at_max.len, max);
        let at_min = FrameHeader::parse(&header_declaring(header_len)).expect("header only");
        assert_eq!(at_min.len, header_len);

        assert_eq!(
            FrameHeader::parse(&header_declaring(max + 1)),
            Err(ProtoError::OversizedFrame {
                len: MAX_FRAME_BYTES + 1,
                max: MAX_FRAME_BYTES,
            }),
            "one past the bound"
        );
        assert_eq!(
            FrameHeader::parse(&header_declaring(u32::MAX)),
            Err(ProtoError::OversizedFrame {
                len: usize::try_from(u32::MAX).expect("u32 fits usize"),
                max: MAX_FRAME_BYTES,
            }),
            "the largest declarable length"
        );
        assert_eq!(
            FrameHeader::parse(&header_declaring(header_len - 1)),
            Err(ProtoError::Truncated {
                context: "frame length shorter than the header",
                need: FRAME_HEADER_LEN,
                have: FRAME_HEADER_LEN - 1,
            }),
            "one below the header length"
        );
        assert_eq!(
            FrameHeader::parse(&header_declaring(0)),
            Err(ProtoError::Truncated {
                context: "frame length shorter than the header",
                need: FRAME_HEADER_LEN,
                have: 0,
            }),
            "a zero length"
        );
    }

    #[test]
    fn parse_reports_the_fixed_field_refusals_before_the_length_refusal() {
        // The Python reference checks the fixed fields first and the length last; the
        // two decoders must name the same first refusal for a frame with several defects.
        let max = u32::try_from(MAX_FRAME_BYTES).expect("bounded constant");
        let mut reserved = header_declaring(max + 1);
        reserved[6] = 1;
        assert_eq!(
            FrameHeader::parse(&reserved),
            Err(ProtoError::NonZeroReserved(1))
        );
        let mut kind = header_declaring(0);
        kind[4] = 9;
        assert_eq!(
            FrameHeader::parse(&kind),
            Err(ProtoError::UnknownFrameType(9))
        );
        let mut flags = header_declaring(0);
        flags[5] = 0x80;
        assert_eq!(
            FrameHeader::parse(&flags),
            Err(ProtoError::UnknownFrameFlags(0x80))
        );
    }

    #[test]
    fn decode_block_refuses_a_declared_length_below_the_header_before_trailing_bytes() {
        // Before HOB-8 a frame declaring 4 bytes but carrying 12 reported
        // `TrailingBytes`; the declared length is nonsense, not a trailing remainder.
        assert_eq!(
            decode_block(&header_declaring(4)),
            Err(ProtoError::Truncated {
                context: "frame length shorter than the header",
                need: FRAME_HEADER_LEN,
                have: 4,
            })
        );
    }

    #[test]
    fn an_i64_column_is_stored_as_its_twos_complement_bits() {
        let mut block = live_block();
        block.columns.push(
            Column::new(
                4,
                MetricValueType::I64,
                MetricKind::Gauge,
                vec![
                    u64::from_le_bytes(i64::MIN.to_le_bytes()),
                    u64::from_le_bytes((-1i64).to_le_bytes()),
                ],
                vec![Quality::Ok.code(), Quality::Reset.code()],
            )
            .expect("column"),
        );
        let mut dictionary = block.dictionary.clone().expect("dictionary");
        dictionary
            .intern(Entry::Metric {
                facts: MetricFacts {
                    unit: MetricUnit::None,
                    kind: MetricKind::Gauge,
                    scope: MetricScope::Daemon,
                    source: MetricSource::Probe,
                },
                name: "signed.value".into(),
            })
            .expect("intern");
        block.dictionary = Some(dictionary);
        let decoded = decode_block(&encode_block(&block).expect("encode")).expect("decode");
        let signed = decoded.columns.get(2).expect("signed column");
        assert_eq!(
            signed.sample(0).expect("row").value,
            TypedValue::I64(i64::MIN)
        );
        assert_eq!(signed.sample(1).expect("row").value, TypedValue::I64(-1));
        assert_eq!(signed.sample(1).expect("row").quality, Quality::Reset);
    }

    #[test]
    fn a_corrupt_time_and_rows_length_is_refused() {
        let mut block = live_block();
        block.rows = Vec::new();
        block.time = vec![
            TimeBucket {
                real_ns: 0,
                covered_ms: 0,
                samples: 0,
                agg: Aggregation::Last,
            },
            TimeBucket {
                real_ns: 1,
                covered_ms: 1,
                samples: 1,
                agg: Aggregation::Last,
            },
        ];
        block.header.n_rows = 2;
        let bytes = encode_block(&block).expect("encode");
        let index = section_index_of(&bytes, SectionKind::Time.code()).expect("a time section");
        let mut corrupt = bytes.clone();
        let len_at = FRAME_HEADER_LEN + 4 + index * super::SECTION_DIR_ENTRY_LEN + 4;
        // Declare the time body longer than it is: the body then overruns the frame, which is
        // a length disagreement and not a partial success.
        let declared = u32::from_le_bytes([
            corrupt[len_at],
            corrupt[len_at + 1],
            corrupt[len_at + 2],
            corrupt[len_at + 3],
        ]);
        corrupt[len_at..len_at + 4].copy_from_slice(&(declared + 8).to_le_bytes());
        let error = decode_block(&corrupt).expect_err("an over-long time section must be refused");
        assert!(
            matches!(
                error,
                ProtoError::Truncated { .. } | ProtoError::TrailingBytes { .. }
            ),
            "{error:?}"
        );
    }

    #[test]
    fn two_columns_for_one_metric_are_refused() {
        let mut block = live_block();
        block.columns.push(block.columns[0].clone());
        assert_eq!(
            decode_block(&encode_block(&block).expect("encode")),
            Err(ProtoError::ConflictingColumn { metric: 2 })
        );
    }

    #[test]
    fn a_block_decoded_from_bytes_with_rows_and_time_is_refused() {
        // Encode a valid live block, then append a time section: the encoder refuses that
        // combination, so it is built by hand to prove the decoder refuses it too.
        let block = live_block();
        let header = Section::new(
            SectionKind::Header,
            super::encode_header_body(&block.header),
        );
        let dictionary = Section::new(
            SectionKind::Dictionary,
            block
                .dictionary
                .as_ref()
                .expect("dict")
                .encode()
                .expect("dict"),
        );
        let rows = Section::new(SectionKind::Rows, super::encode_rows_body(&block.rows));
        // Three buckets match the header's three rows, so the frame passes the per-section
        // length checks and reaches the rows-versus-time rule.
        let time = Section::new(
            SectionKind::Time,
            super::encode_time_body(&[
                TimeBucket {
                    real_ns: 0,
                    covered_ms: 0,
                    samples: 0,
                    agg: Aggregation::Raw,
                },
                TimeBucket {
                    real_ns: 0,
                    covered_ms: 0,
                    samples: 0,
                    agg: Aggregation::Raw,
                },
            ])
            .expect("time"),
        );
        let bytes =
            encode_frame(FrameType::Block, 1, &[header, dictionary, rows, time]).expect("encode");
        assert_eq!(
            decode_block(&bytes),
            Err(ProtoError::RowsAndTime {
                rows: 2,
                buckets: 2
            })
        );
    }

    fn section_index_of(bytes: &[u8], kind: u16) -> Option<usize> {
        let n_sec =
            u16::from_le_bytes([bytes[FRAME_HEADER_LEN], bytes[FRAME_HEADER_LEN + 1]]) as usize;
        (0..n_sec).find(|index| {
            let entry = FRAME_HEADER_LEN + 4 + index * super::SECTION_DIR_ENTRY_LEN;
            u16::from_le_bytes([bytes[entry], bytes[entry + 1]]) == kind
        })
    }

    #[test]
    fn a_history_block_uses_time_buckets_in_place_of_rows() {
        let mut block = live_block();
        block.rows = Vec::new();
        block.time = vec![
            TimeBucket {
                real_ns: 1_700_000_000_000_000_000,
                covered_ms: 1000,
                samples: 5,
                agg: Aggregation::Last,
            },
            TimeBucket {
                real_ns: -1,
                covered_ms: 0,
                samples: 0,
                agg: Aggregation::Last,
            },
        ];
        // `last` is the one aggregation both columns (a counter and a gauge) permit (HOB-47).
        let bytes = encode_block(&block).expect("encode");
        let decoded = decode_block(&bytes).expect("decode");
        assert_eq!(decoded, block);
        assert!(decoded.is_history());
        assert_eq!(decoded.row_count(0), Some(2));
        assert_eq!(decoded.row_count(5), None);
        assert!(
            decoded.time[1].real_ns < 0,
            "a negative real time is a legal i64"
        );
        assert!(!decode_block(&encode_block(&live_block()).expect("encode"))
            .expect("decode")
            .is_history());
    }

    /// `n` distinct buckets, so a reader that confuses two columns or two buckets is caught.
    fn buckets(n: usize) -> Vec<TimeBucket> {
        let aggs = [
            Aggregation::Raw,
            Aggregation::Last,
            Aggregation::Delta,
            Aggregation::Mean,
            Aggregation::Min,
            Aggregation::Max,
        ];
        (0..n)
            .map(|index| TimeBucket {
                real_ns: i64::try_from(index).expect("small") * 1_000_000_007 - 1,
                covered_ms: u32::try_from(index).expect("small") * 100_003,
                samples: u16::try_from(index).expect("small") * 7_001,
                agg: aggs[index % aggs.len()],
            })
            .collect()
    }

    /// `n` distinct buckets whose aggregations a counter column permits (raw, last, delta).
    fn counter_buckets(n: usize) -> Vec<TimeBucket> {
        let permitted = [Aggregation::Raw, Aggregation::Last, Aggregation::Delta];
        buckets(n)
            .into_iter()
            .enumerate()
            .map(|(index, bucket)| TimeBucket {
                agg: permitted[index % permitted.len()],
                ..bucket
            })
            .collect()
    }

    #[test]
    fn the_time_section_is_fifteen_bytes_per_bucket_padded_to_eight() {
        // HOB-48 (length half): i64 + u32 + u16 + u8 = 15 bytes a bucket. The old factor of 14
        // agrees with this only up to three buckets (42 and 45 both pad to 48), so every count
        // from 0 to 9 is pinned by its exact length, not by a formula.
        let expected = [0usize, 16, 32, 48, 64, 80, 96, 112, 120, 136];
        for (n, want) in expected.iter().enumerate() {
            let time = buckets(n);
            let body = super::encode_time_body(&time).expect("encode");
            assert_eq!(body.len(), *want, "{n} buckets");
            assert_eq!(
                super::time_body_len(n).expect("length"),
                *want,
                "the length helper agrees with the encoder for {n} buckets"
            );
            assert_eq!(
                super::decode_time_body(&body, n).expect("decode"),
                time,
                "{n} buckets round-trip"
            );
        }
        assert_eq!(super::TIME_BUCKET_BYTES, 15);
    }

    #[test]
    fn a_history_block_of_zero_to_nine_buckets_round_trips_through_the_frame() {
        for n in 0..=9usize {
            let mut block = live_block();
            block.rows = Vec::new();
            block.header.n_rows = u32::try_from(n).expect("small");
            block.time = counter_buckets(n);
            block.columns = vec![Column::new(
                2,
                MetricValueType::U64,
                MetricKind::Counter,
                (0..u64::try_from(n).expect("small")).collect(),
                vec![Quality::Ok.code(); n],
            )
            .expect("column")];
            let bytes = encode_block(&block).expect("encode");
            let decoded = decode_block(&bytes).expect("decode");
            assert_eq!(decoded, block, "{n} buckets");
            assert_eq!(
                encode_block(&decoded).expect("encode again"),
                bytes,
                "{n} buckets: encoding is the exact inverse"
            );
        }
    }

    #[test]
    fn a_time_body_of_the_old_length_is_refused() {
        // Four buckets used to be sized 56 bytes by the decoder and written as 64 by the
        // encoder; the decoder now refuses the 56-byte body instead of reading it.
        let mut body = super::encode_time_body(&buckets(4)).expect("encode");
        body.truncate(56);
        assert_eq!(
            super::decode_time_body(&body, 4),
            Err(ProtoError::SectionLength {
                kind: SectionKind::Time.code(),
                declared: 56,
                expected: 64,
            })
        );
    }

    #[test]
    fn a_block_can_not_carry_rows_and_buckets_at_once() {
        let mut block = live_block();
        block.time = vec![TimeBucket {
            real_ns: 0,
            covered_ms: 1,
            samples: 1,
            agg: Aggregation::Last,
        }];
        assert_eq!(
            encode_block(&block),
            Err(ProtoError::RowsAndTime {
                rows: 2,
                buckets: 1
            })
        );
    }

    #[test]
    fn the_declared_row_count_is_enforced_against_every_section() {
        let mut block = live_block();
        block.header.n_rows = 1;
        assert_eq!(
            encode_block(&block),
            Err(ProtoError::SectionLength {
                kind: SectionKind::Rows.code(),
                declared: 2,
                expected: 1
            })
        );
        let mut block = live_block();
        block.rows = Vec::new();
        assert_eq!(
            encode_block(&block),
            Err(ProtoError::NoRowsOrTime { n_rows: 2 }),
            "values without an entity or a bucket are refused, never encoded rowless"
        );
        block.header.n_rows = 0;
        block.columns = Vec::new();
        assert!(
            encode_block(&block).is_ok(),
            "a zero-row block (the empty selection) needs no rows section"
        );
        // A row-count disagreement with a column is still reported as that disagreement;
        // the rowless-shape rule is about a block with NO rows section at all.
        block = live_block();
        block.rows = vec![
            Row {
                ent: 0,
                t_off_us: 0,
            },
            Row {
                ent: 1,
                t_off_us: 1,
            },
            Row {
                ent: 0,
                t_off_us: 2,
            },
        ];
        block.header.n_rows = 3;
        assert_eq!(
            encode_block(&block),
            Err(ProtoError::SectionLength {
                kind: SectionKind::Column.code(),
                declared: 2,
                expected: 3
            })
        );
        let mut block = live_block();
        block.columns[0] = Column::new(
            2,
            MetricValueType::U64,
            MetricKind::Counter,
            vec![1, 2, 3],
            vec![0, 0, 0],
        )
        .expect("column");
        assert_eq!(
            encode_block(&block),
            Err(ProtoError::SectionLength {
                kind: SectionKind::Column.code(),
                declared: 3,
                expected: 2
            })
        );
    }

    #[test]
    fn a_column_whose_quality_rows_disagree_with_its_values_is_refused() {
        assert_eq!(
            Column::new(
                1,
                MetricValueType::U64,
                MetricKind::Counter,
                vec![1, 2],
                vec![0]
            ),
            Err(ProtoError::SectionLength {
                kind: SectionKind::Column.code(),
                declared: 2,
                expected: 1
            })
        );
    }

    #[test]
    fn an_empty_block_is_a_legal_fully_declared_snapshot() {
        let mut block = live_block();
        block.header.n_rows = 0;
        block.rows = Vec::new();
        block.columns = Vec::new();
        block.dictionary = Some(Dictionary::new(3));
        let bytes = encode_block(&block).expect("encode");
        assert_eq!(decode_block(&bytes).expect("decode"), block);
    }

    #[test]
    fn an_unknown_optional_section_is_skipped_and_reported() {
        let block = live_block();
        let header = Section::new(
            SectionKind::Header,
            super::encode_header_body(&block.header),
        );
        let dictionary = Section::new(
            SectionKind::Dictionary,
            block
                .dictionary
                .as_ref()
                .expect("dict")
                .encode()
                .expect("dict"),
        );
        let rows = Section::new(SectionKind::Rows, super::encode_rows_body(&block.rows));
        let column = Section::new(
            SectionKind::Column,
            super::encode_column_body(&block.columns[0]).expect("column"),
        );
        let unknown = Section::raw(9, 1, 0, vec![0u8; 8]);
        let bytes = encode_frame(
            FrameType::Block,
            5,
            &[header, dictionary, rows, column, unknown],
        )
        .expect("encode");
        let decoded = decode_block(&bytes).expect("decode");
        assert_eq!(decoded.skipped_sections, vec![9]);
        assert_eq!(decoded.columns.len(), 1);
        assert_eq!(decoded.corr, 5);
    }

    #[test]
    fn an_unknown_required_section_is_refused_wherever_it_appears() {
        let block = live_block();
        let header = Section::new(
            SectionKind::Header,
            super::encode_header_body(&block.header),
        );
        let required = Section::raw(9, 1, 1, vec![0u8; 8]);
        let bytes = encode_frame(FrameType::Block, 1, &[header, required]).expect("encode");
        assert_eq!(
            decode_block(&bytes),
            Err(ProtoError::UnsupportedRequiredSection { kind: 9 })
        );
    }

    #[test]
    fn a_known_section_with_a_future_version_is_refused_even_when_optional() {
        let block = live_block();
        let header = Section::new(
            SectionKind::Header,
            super::encode_header_body(&block.header),
        );
        let future = Section::raw(SectionKind::Column.code(), 2, 0, vec![0u8; 32]);
        let bytes = encode_frame(FrameType::Block, 1, &[header, future]).expect("encode");
        assert_eq!(
            decode_block(&bytes),
            Err(ProtoError::UnsupportedSectionVersion {
                kind: SectionKind::Column.code(),
                version: 2,
                supported: 1
            }),
            "skipping a known section would silently drop data the reader was promised"
        );
    }

    #[test]
    fn a_known_section_with_an_older_version_is_refused_too() {
        // Only version 1 exists. An older version is not "the same layout with fewer
        // guarantees": decoding it as version 1 would read a different body and report
        // values the sender never wrote, so it is refused like a future one.
        let block = live_block();
        let header = Section::new(
            SectionKind::Header,
            super::encode_header_body(&block.header),
        );
        let older = Section::raw(
            SectionKind::Column.code(),
            0,
            0,
            super::encode_column_body(&block.columns[0]).expect("column"),
        );
        let bytes = encode_frame(FrameType::Block, 1, &[header, older]).expect("encode");
        assert_eq!(
            decode_block(&bytes),
            Err(ProtoError::UnsupportedSectionVersion {
                kind: SectionKind::Column.code(),
                version: 0,
                supported: 1,
            })
        );
    }

    #[test]
    fn a_duplicate_single_instance_section_is_refused() {
        let block = live_block();
        let header = Section::new(
            SectionKind::Header,
            super::encode_header_body(&block.header),
        );
        let rows = Section::new(SectionKind::Rows, super::encode_rows_body(&block.rows));
        let dictionary = Section::new(
            SectionKind::Dictionary,
            block
                .dictionary
                .as_ref()
                .expect("dict")
                .encode()
                .expect("dict"),
        );
        let bytes = encode_frame(
            FrameType::Block,
            1,
            &[header.clone(), dictionary, rows.clone()],
        )
        .expect("encode");
        assert!(decode_block(&bytes).is_ok(), "one of each is fine");
        assert_eq!(
            encode_frame(FrameType::Block, 1, &[header.clone(), rows.clone(), rows]),
            Err(ProtoError::DuplicateSection {
                kind: SectionKind::Rows.code()
            }),
            "the encoder refuses a repeated single-instance section as well"
        );
        // A duplicate that only reaches the decoder (a hand-built fixture) is refused there too.
        let rows_body = super::encode_rows_body(&[]);
        let mut empty_header = block.header.clone();
        empty_header.n_rows = 0;
        let header_body = super::encode_header_body(&empty_header);
        let mut writer = crate::write::Writer::new();
        writer.u16(3);
        writer.u16(0);
        writer.u16(SectionKind::Header.code());
        writer.u8(1);
        writer.u8(1);
        writer.u32(u32::try_from(header_body.len()).expect("header body"));
        for _ in 0..2 {
            writer.u16(SectionKind::Rows.code());
            writer.u8(1);
            writer.u8(0);
            writer.u32(0);
        }
        let mut frame = Vec::new();
        frame.extend_from_slice(&0u32.to_le_bytes());
        frame.push(FrameType::Block.code());
        frame.push(super::FLAG_SECTIONED);
        frame.extend_from_slice(&0u16.to_le_bytes());
        frame.extend_from_slice(&1u32.to_le_bytes());
        frame.extend_from_slice(writer.as_bytes());
        frame.extend_from_slice(&header_body);
        for _ in 0..2 {
            frame.extend_from_slice(&rows_body);
        }
        let total = u32::try_from(frame.len()).expect("small frame");
        frame[0..4].copy_from_slice(&total.to_le_bytes());
        assert_eq!(
            decode_block(&frame),
            Err(ProtoError::DuplicateSection {
                kind: SectionKind::Rows.code()
            })
        );
    }

    #[test]
    fn a_missing_header_section_is_refused_in_both_directions() {
        let rows = Section::new(SectionKind::Rows, super::encode_rows_body(&[]));
        assert_eq!(
            encode_frame(FrameType::Block, 1, &[rows]),
            Err(ProtoError::MissingSection {
                kind: SectionKind::Header.code()
            })
        );
        let dir_start = FRAME_HEADER_LEN + 4;
        // Replace the header section's declared kind with an optional unknown one: entry 0 is
        // then not the header, which WIRE.md §3.1 reports as `section-order` at index 0 (the
        // rank pass skips unknown kinds, so this is the entry-0 check) and not as a missing
        // header.
        let block = live_block();
        let mut bytes = encode_block(&block).expect("encode");
        bytes[dir_start] = 9;
        bytes[dir_start + 3] = 0;
        assert_eq!(
            decode_block(&bytes),
            Err(ProtoError::SectionOrder { index: 0, kind: 9 })
        );
        // A frame whose known sections are in rank order but hold no header at all is a
        // missing header: the header's kind is rewritten to the time section's, which shares
        // rank 3 with the rows that follow it.
        let mut block = live_block();
        block.dictionary = None;
        let mut bytes = encode_block(&block).expect("encode");
        bytes[dir_start] = 4;
        bytes[dir_start + 3] = 0;
        assert_eq!(
            decode_block(&bytes),
            Err(ProtoError::MissingSection {
                kind: SectionKind::Header.code()
            })
        );
    }

    #[test]
    fn a_block_shape_is_refused_under_any_other_frame_type() {
        // §B.1 gives every frame type one payload shape. Without this guard a caller that
        // trusted the codec would read a control envelope's bytes as measurements.
        for frame_type in [
            FrameType::Req,
            FrameType::Rep,
            FrameType::Evt,
            FrameType::Dict,
            FrameType::End,
        ] {
            let mut block = live_block();
            block.frame_type = frame_type;
            assert_eq!(
                encode_block(&block),
                Err(ProtoError::NotABlock {
                    frame_type: frame_type.code()
                }),
                "the encoder refuses to write a block under {frame_type:?}"
            );
        }
        // And on the read path: the same canonical bytes with the type byte changed and the
        // SECTIONED flag cleared, because SECTIONED is defined for BLOCK only (WIRE.md §2.1)
        // and is judged first, as one of the frame header's fixed fields (§3.10).
        let bytes = encode_block(&live_block()).expect("encode");
        for frame_type in [
            FrameType::Req,
            FrameType::Rep,
            FrameType::Evt,
            FrameType::Dict,
            FrameType::End,
        ] {
            let mut corrupt = bytes.clone();
            corrupt[4] = frame_type.code();
            corrupt[5] = 0;
            assert_eq!(
                decode_block(&corrupt),
                Err(ProtoError::NotABlock {
                    frame_type: frame_type.code()
                }),
                "a block-shaped payload under {frame_type:?} is refused"
            );
        }
    }

    #[test]
    fn sectioned_is_defined_for_block_frames_only() {
        // WIRE.md §2.1: SECTIONED is set on BLOCK and clear on every other type; on any other
        // type it is an unknown flag, refused by the header parse before the length and the
        // payload shape are looked at (`block-as-req`, `rep-sectioned-flag`).
        let bytes = encode_block(&live_block()).expect("encode");
        for frame_type in [
            FrameType::Req,
            FrameType::Rep,
            FrameType::Evt,
            FrameType::Dict,
            FrameType::End,
        ] {
            let mut flagged = bytes.clone();
            flagged[4] = frame_type.code();
            assert_eq!(
                FrameHeader::parse(&flagged),
                Err(ProtoError::UnknownFrameFlags(super::FLAG_SECTIONED)),
                "SECTIONED under {frame_type:?} is an unknown flag"
            );
            assert_eq!(
                decode_block(&flagged),
                Err(ProtoError::UnknownFrameFlags(super::FLAG_SECTIONED)),
                "and the decoder reports it before not-a-block under {frame_type:?}"
            );
        }
        let block = FrameHeader::parse(&bytes).expect("a BLOCK may carry SECTIONED");
        assert_eq!(block.flags, super::FLAG_SECTIONED);
        // A bare header of any other type with the flag clear parses.
        let mut plain = header_declaring(12);
        plain[4] = FrameType::Req.code();
        plain[5] = 0;
        assert_eq!(
            FrameHeader::parse(&plain).map(|header| header.frame_type),
            Ok(FrameType::Req)
        );
    }

    #[test]
    fn sections_must_be_in_the_canonical_order() {
        // One order exists (header, dictionary, rows-or-time, then columns), so two byte
        // strings never decode to one value (§B.5). The encoder refuses to build a
        // non-canonical directory and the decoder refuses to accept one.
        let block = live_block();
        let header = Section::new(
            SectionKind::Header,
            super::encode_header_body(&block.header),
        );
        let dictionary = Section::new(
            SectionKind::Dictionary,
            block
                .dictionary
                .as_ref()
                .expect("dict")
                .encode()
                .expect("dict"),
        );
        let rows = Section::new(SectionKind::Rows, super::encode_rows_body(&block.rows));
        let column = Section::new(
            SectionKind::Column,
            super::encode_column_body(&block.columns[0]).expect("column"),
        );
        assert_eq!(
            encode_frame(
                FrameType::Block,
                1,
                &[dictionary.clone(), header.clone(), rows.clone()],
            ),
            Err(ProtoError::SectionOrder {
                index: 1,
                kind: SectionKind::Header.code(),
            }),
            "a dictionary before its header is not a canonical block"
        );
        assert_eq!(
            encode_frame(
                FrameType::Block,
                1,
                &[header.clone(), column.clone(), rows.clone()],
            ),
            Err(ProtoError::SectionOrder {
                index: 2,
                kind: SectionKind::Rows.code(),
            }),
            "a column before the rows it addresses is not a canonical block"
        );
        // The canonical order decodes and re-encodes byte-identically, which is the whole
        // point of the rule.
        let bytes = encode_frame(FrameType::Block, 1, &[header, dictionary, rows, column])
            .expect("canonical order encodes");
        let decoded = decode_block(&bytes).expect("canonical order decodes");
        assert_eq!(decoded.rows.len(), 2);
        assert_eq!(encode_block(&decoded).expect("re-encode"), bytes);
    }

    #[test]
    fn a_reordered_directory_is_refused_on_the_read_path() {
        // Hand-built bytes: header and dictionary directory entries and bodies swapped. Every
        // declared length still matches the body beside it, so only the order rule can
        // refuse this frame — and it must, or the same block would have two spellings.
        let block = live_block();
        let header_body = super::encode_header_body(&block.header);
        let dictionary_body = block
            .dictionary
            .as_ref()
            .expect("dict")
            .encode()
            .expect("dict");
        let mut writer = crate::write::Writer::new();
        writer.u16(2);
        writer.u16(0);
        for (kind, version, flags, body) in [
            (
                SectionKind::Dictionary.code(),
                1u8,
                0u8,
                dictionary_body.as_slice(),
            ),
            (SectionKind::Header.code(), 1, 1, header_body.as_slice()),
        ] {
            writer.u16(kind);
            writer.u8(version);
            writer.u8(flags);
            writer.u32(u32::try_from(body.len()).expect("body length"));
        }
        let bodies_len = header_body.len() + dictionary_body.len();
        let total = FRAME_HEADER_LEN + 4 + 2 * super::SECTION_DIR_ENTRY_LEN + bodies_len;
        let mut frame = Vec::with_capacity(total);
        frame.extend_from_slice(&u32::try_from(total).expect("total").to_le_bytes());
        frame.push(FrameType::Block.code());
        frame.push(super::FLAG_SECTIONED);
        frame.extend_from_slice(&0u16.to_le_bytes());
        frame.extend_from_slice(&1u32.to_le_bytes());
        frame.extend_from_slice(writer.as_bytes());
        frame.extend_from_slice(&dictionary_body);
        frame.extend_from_slice(&header_body);
        assert_eq!(frame.len(), total);
        assert_eq!(
            decode_block(&frame),
            Err(ProtoError::SectionOrder {
                index: 1,
                kind: SectionKind::Header.code(),
            })
        );
    }

    #[test]
    fn a_frame_without_a_section_directory_is_not_a_block() {
        let mut bytes = vec![0u8; FRAME_HEADER_LEN];
        bytes[0..4].copy_from_slice(
            &u32::try_from(FRAME_HEADER_LEN)
                .expect("header length")
                .to_le_bytes(),
        );
        bytes[4] = FrameType::Block.code();
        bytes[5] = 0;
        bytes[8..12].copy_from_slice(&4u32.to_le_bytes());
        assert_eq!(
            decode_block(&bytes),
            Err(ProtoError::MissingSection {
                kind: SectionKind::Header.code()
            })
        );
    }

    #[test]
    fn a_frame_of_exactly_the_maximum_size_is_accepted() {
        // The bound is inclusive ("max frame 16 MiB"): a frame that declares exactly
        // MAX_FRAME_BYTES is legal, one byte more is not. Without this case an off-by-one
        // in the comparison is invisible.
        // A block with zero rows is the legal "empty selection": the size bound is a
        // property of the frame layout, not of how many rows it carries.
        let mut block = live_block();
        block.header.n_rows = 0;
        let header = Section::new(
            SectionKind::Header,
            super::encode_header_body(&block.header),
        );
        // Two sections: the truncated directory header (4 bytes) plus two entries.
        let payload_len = MAX_FRAME_BYTES
            - FRAME_HEADER_LEN
            - 4
            - 2 * super::SECTION_DIR_ENTRY_LEN
            - super::HEADER_BODY_LEN;
        let payload = Section::raw(9, 1, 0, vec![0u8; payload_len]);
        let at_the_limit =
            encode_frame(FrameType::Block, 1, &[header.clone(), payload]).expect("encode");
        assert_eq!(
            at_the_limit.len(),
            MAX_FRAME_BYTES,
            "the fixture is exactly the bound"
        );
        assert!(
            decode_block(&at_the_limit).is_ok(),
            "exactly the bound is legal"
        );

        let one_more = Section::raw(9, 1, 0, vec![0u8; payload_len + 8]);
        assert!(
            matches!(
                encode_frame(FrameType::Block, 1, &[header, one_more]),
                Err(ProtoError::OversizedFrame { .. })
            ),
            "one byte past the bound is refused"
        );
    }

    #[test]
    fn truncated_corrupt_and_oversized_frames_are_typed_refusals() {
        let bytes = encode_block(&live_block()).expect("encode");

        // Truncated: every prefix is refused, never partially decoded.
        for cut in 1..bytes.len() {
            let error = decode_block(&bytes[..cut]).expect_err("a prefix is not a frame");
            assert!(
                matches!(
                    error,
                    ProtoError::Truncated { .. }
                        | ProtoError::SectionLength { .. }
                        | ProtoError::MissingSection { .. }
                ),
                "prefix of {cut} bytes gave {error:?}"
            );
        }

        // Trailing bytes.
        let mut longer = bytes.clone();
        longer.extend_from_slice(&[0; 8]);
        assert_eq!(
            decode_block(&longer),
            Err(ProtoError::TrailingBytes { extra: 8 })
        );

        // An oversized declared length, refused before it is used as an allocation size.
        let mut huge = vec![0u8; FRAME_HEADER_LEN];
        let too_big = u32::try_from(MAX_FRAME_BYTES).expect("bounded constant") + 1;
        huge[0..4].copy_from_slice(&too_big.to_le_bytes());
        huge[4] = FrameType::Block.code();
        huge[5] = super::FLAG_SECTIONED;
        assert_eq!(
            decode_block(&huge),
            Err(ProtoError::OversizedFrame {
                len: MAX_FRAME_BYTES + 1,
                max: MAX_FRAME_BYTES
            })
        );

        // A reserved header field that is not zero.
        let mut reserved = bytes.clone();
        reserved[6] = 1;
        assert_eq!(decode_block(&reserved), Err(ProtoError::NonZeroReserved(1)));

        // An unknown frame type and an unknown frame flag.
        let mut kind = bytes.clone();
        kind[4] = 9;
        assert_eq!(decode_block(&kind), Err(ProtoError::UnknownFrameType(9)));
        let mut flags = bytes.clone();
        flags[5] = 0x80;
        assert_eq!(
            decode_block(&flags),
            Err(ProtoError::UnknownFrameFlags(0x80))
        );

        // A corrupt quality byte in the counter column.
        let mut corrupt = bytes.clone();
        let column_start = body_at(&corrupt, SectionKind::Column.code());
        corrupt[column_start + 8 + 16] = 9;
        assert_eq!(
            decode_block(&corrupt),
            Err(ProtoError::UnknownQuality { row: 0, code: 9 })
        );

        // A non-zero column padding field.
        let mut pad = bytes.clone();
        pad[column_start + 6] = 1;
        assert_eq!(
            decode_block(&pad),
            Err(ProtoError::ColumnReserved { metric: 2, pad: 1 })
        );

        // An unknown column value type and column kind.
        let mut value_type = bytes.clone();
        value_type[column_start + 4] = 9;
        assert_eq!(
            decode_block(&value_type),
            Err(ProtoError::UnknownValueType(9))
        );
        let mut column_kind = bytes.clone();
        column_kind[column_start + 5] = 9;
        assert_eq!(
            decode_block(&column_kind),
            Err(ProtoError::UnknownColumnKind(9))
        );

        // A section body that claims a length it does not have.
        let mut short = bytes.clone();
        let section_len_at = FRAME_HEADER_LEN + 4 + 4;
        short[section_len_at..section_len_at + 4].copy_from_slice(&1u32.to_le_bytes());
        assert!(decode_block(&short).is_err());

        // A non-zero reserved field in the block directory.
        let mut dir_reserved = bytes.clone();
        dir_reserved[FRAME_HEADER_LEN + 2] = 1;
        assert_eq!(
            decode_block(&dir_reserved),
            Err(ProtoError::SectionReserved(1))
        );
    }

    fn find_section_body(bytes: &[u8], kind: u16) -> Option<usize> {
        let n_sec =
            u16::from_le_bytes([bytes[FRAME_HEADER_LEN], bytes[FRAME_HEADER_LEN + 1]]) as usize;
        let mut body = FRAME_HEADER_LEN + 4 + n_sec * super::SECTION_DIR_ENTRY_LEN;
        for index in 0..n_sec {
            let entry = FRAME_HEADER_LEN + 4 + index * super::SECTION_DIR_ENTRY_LEN;
            let entry_kind = u16::from_le_bytes([bytes[entry], bytes[entry + 1]]);
            let len = u32::from_le_bytes([
                bytes[entry + 4],
                bytes[entry + 5],
                bytes[entry + 6],
                bytes[entry + 7],
            ]) as usize;
            if entry_kind == kind {
                return Some(body);
            }
            body += len;
        }
        None
    }

    fn body_at(bytes: &[u8], kind: u16) -> usize {
        find_section_body(bytes, kind).expect("the fixture frame carries the section")
    }

    #[test]
    fn every_length_and_flag_refusal_names_the_section_it_is_about() {
        let block = live_block();
        let header_body = super::encode_header_body(&block.header);

        // A header body that is not the fixed 80 bytes.
        let long_header = Section::raw(
            SectionKind::Header.code(),
            1,
            1,
            vec![0u8; HEADER_BODY_LEN + 8],
        );
        let bytes = encode_frame(FrameType::Block, 1, &[long_header]).expect("encode");
        assert_eq!(
            decode_block(&bytes),
            Err(ProtoError::SectionLength {
                kind: SectionKind::Header.code(),
                declared: HEADER_BODY_LEN + 8,
                expected: HEADER_BODY_LEN,
            })
        );

        // The block header's own flags word is reserved and must be zero.
        let mut flagged = header_body.clone();
        flagged[HEADER_BODY_LEN - 4..].copy_from_slice(&1u32.to_le_bytes());
        let bytes = encode_frame(
            FrameType::Block,
            1,
            &[Section::new(SectionKind::Header, flagged)],
        )
        .expect("encode");
        assert_eq!(
            decode_block(&bytes),
            Err(ProtoError::UnknownSectionFlags {
                kind: SectionKind::Header.code(),
                flags: 1
            })
        );

        // Rows, columns and time must each carry exactly the header's row count.
        let rows = Section::new(SectionKind::Rows, super::encode_rows_body(&block.rows[..1]));
        let bytes = encode_frame(
            FrameType::Block,
            1,
            &[Section::new(SectionKind::Header, header_body.clone()), rows],
        )
        .expect("encode");
        assert_eq!(
            decode_block(&bytes),
            Err(ProtoError::SectionLength {
                kind: SectionKind::Rows.code(),
                declared: 8,
                expected: 16
            })
        );

        let mut short_column = block.columns[0].clone();
        short_column.values.truncate(1);
        short_column.quality.truncate(1);
        let column = Section::new(
            SectionKind::Column,
            super::encode_column_body(&short_column).expect("column"),
        );
        let bytes = encode_frame(
            FrameType::Block,
            1,
            &[
                Section::new(SectionKind::Header, header_body.clone()),
                column,
            ],
        )
        .expect("encode");
        assert_eq!(
            decode_block(&bytes),
            Err(ProtoError::SectionLength {
                kind: SectionKind::Column.code(),
                declared: 24,
                expected: 32
            })
        );

        let one_bucket = Section::new(
            SectionKind::Time,
            super::encode_time_body(&[TimeBucket {
                real_ns: 0,
                covered_ms: 0,
                samples: 0,
                agg: Aggregation::Raw,
            }])
            .expect("time"),
        );
        let bytes = encode_frame(
            FrameType::Block,
            1,
            &[Section::new(SectionKind::Header, header_body), one_bucket],
        )
        .expect("encode");
        assert_eq!(
            decode_block(&bytes),
            Err(ProtoError::SectionLength {
                kind: SectionKind::Time.code(),
                declared: 16,
                expected: 32
            })
        );

        // A section body *longer* than its content also disagrees with the layout: the
        // frame is well formed (every declared length matches the bytes present), so this
        // is the per-section content check and not a truncation.
        let mut long_rows = super::encode_rows_body(&block.rows);
        long_rows.extend_from_slice(&[0u8; 8]);
        let bytes = encode_frame(
            FrameType::Block,
            1,
            &[
                Section::new(
                    SectionKind::Header,
                    super::encode_header_body(&block.header),
                ),
                Section::new(SectionKind::Rows, long_rows),
            ],
        )
        .expect("encode");
        assert_eq!(
            decode_block(&bytes),
            Err(ProtoError::SectionLength {
                kind: SectionKind::Rows.code(),
                declared: 24,
                expected: 16
            })
        );

        let mut long_column = super::encode_column_body(&block.columns[0]).expect("column");
        long_column.extend_from_slice(&[0u8; 8]);
        let bytes = encode_frame(
            FrameType::Block,
            1,
            &[
                Section::new(
                    SectionKind::Header,
                    super::encode_header_body(&block.header),
                ),
                Section::new(SectionKind::Column, long_column),
            ],
        )
        .expect("encode");
        assert_eq!(
            decode_block(&bytes),
            Err(ProtoError::SectionLength {
                kind: SectionKind::Column.code(),
                declared: 40,
                expected: 32
            })
        );

        // And the dictionary body, whose padding rule is the same rule.
        let mut long_dictionary = block
            .dictionary
            .as_ref()
            .expect("dict")
            .encode()
            .expect("dict");
        long_dictionary.extend_from_slice(&[0u8; 8]);
        let bytes = encode_frame(
            FrameType::Block,
            1,
            &[
                Section::new(
                    SectionKind::Header,
                    super::encode_header_body(&block.header),
                ),
                Section::new(SectionKind::Dictionary, long_dictionary),
            ],
        )
        .expect("encode");
        let error = decode_block(&bytes).expect_err("an over-long dictionary is corrupt");
        assert!(
            matches!(
                error,
                ProtoError::TrailingBytes { extra: 8 } | ProtoError::SectionLength { .. }
            ),
            "{error:?}"
        );

        // The encoder refuses a bucket count that disagrees with the header, before
        // producing a frame a reader would have to reject.
        let mut history = live_block();
        history.rows = Vec::new();
        history.time = vec![TimeBucket {
            real_ns: 0,
            covered_ms: 0,
            samples: 0,
            agg: Aggregation::Raw,
        }];
        assert_eq!(
            encode_block(&history),
            Err(ProtoError::SectionLength {
                kind: SectionKind::Time.code(),
                declared: 1,
                expected: 2
            })
        );

        // An undefined directory flag bit is refused wherever the section appears, even
        // when the section kind itself is unknown and optional.
        let optional_with_flags = Section::raw(9, 1, 2, vec![0u8; 8]);
        let bytes = encode_frame(
            FrameType::Block,
            1,
            &[Section::new(
                SectionKind::Header,
                super::encode_header_body(&facts()),
            )]
            .into_iter()
            .chain(std::iter::once(optional_with_flags))
            .collect::<Vec<_>>(),
        )
        .expect("encode");
        assert_eq!(
            decode_block(&bytes),
            Err(ProtoError::UnknownSectionFlags { kind: 9, flags: 2 })
        );
    }

    #[test]
    fn encoding_refuses_an_oversized_frame_before_building_it() {
        let block = live_block();
        let header = Section::new(
            SectionKind::Header,
            super::encode_header_body(&block.header),
        );
        let huge = Section::raw(9, 1, 0, vec![0u8; MAX_FRAME_BYTES]);
        assert!(
            matches!(
                encode_frame(FrameType::Block, 1, &[header, huge]),
                Err(ProtoError::OversizedFrame { .. })
            ),
            "a frame past 16 MiB is refused before it is assembled"
        );
    }

    #[test]
    fn a_dangling_reference_in_a_later_column_is_refused_too() {
        // Every column's metric is checked, not just the first one: a reader that stopped
        // at the first valid reference would accept a block whose later column names an
        // entity or metric the generation does not hold.
        let mut block = live_block();
        block.columns[1].metric = 40;
        assert_eq!(
            decode_block(&encode_block(&block).expect("encode")),
            Err(ProtoError::DanglingDictionaryRef { index: 40, len: 4 })
        );
        let mut block = live_block();
        block.rows[1].ent = 40;
        assert_eq!(
            decode_block(&encode_block(&block).expect("encode")),
            Err(ProtoError::DanglingDictionaryRef { index: 40, len: 4 })
        );
    }

    #[test]
    fn a_reference_of_the_right_range_but_the_wrong_entry_kind_is_refused() {
        // Range is not consistency: a row must name an incarnation and a column a metric.
        // A row pointing at a *metric* entry would make a consumer print a metric name as
        // a cgroup, and a column pointing at an *entity* would do the reverse.
        let mut block = live_block();
        block.rows[0].ent = 2; // dictionary entry 2 is the cpu.usage_usec metric
        assert_eq!(
            decode_block(&encode_block(&block).expect("encode")),
            Err(ProtoError::RowReferenceNotEntity { index: 2 })
        );
        let mut block = live_block();
        block.columns[0].metric = 0; // dictionary entry 0 is an incarnation
        assert_eq!(
            decode_block(&encode_block(&block).expect("encode")),
            Err(ProtoError::ColumnReferenceNotMetric { index: 0 })
        );
        // A label entry is neither an incarnation nor a metric.
        let mut block = live_block();
        let mut dictionary = block.dictionary.clone().expect("dictionary");
        dictionary
            .intern(Entry::Label {
                kind: 1,
                source: 1,
                text: "web".into(),
            })
            .expect("intern");
        block.dictionary = Some(dictionary);
        block.rows[0].ent = 4;
        assert_eq!(
            decode_block(&encode_block(&block).expect("encode")),
            Err(ProtoError::RowReferenceNotEntity { index: 4 })
        );
    }

    #[test]
    fn a_column_kind_that_contradicts_the_dictionary_is_refused() {
        // The column header and the dictionary both declare the arithmetic kind. A consumer
        // aggregates by whichever it trusts, so a disagreement is refused rather than
        // silently resolved in favour of one of them.
        let mut block = live_block();
        block.columns[0].kind = MetricKind::Gauge; // the dictionary says counter
        assert_eq!(
            decode_block(&encode_block(&block).expect("encode")),
            Err(ProtoError::ColumnKindMismatch {
                index: 2,
                declared: MetricKind::Gauge as u8,
                dictionary: MetricKind::Counter as u8,
            })
        );
        // The reverse direction is refused too (a counter column for a gauge metric).
        let mut block = live_block();
        block.columns[1].kind = MetricKind::Counter;
        assert_eq!(
            decode_block(&encode_block(&block).expect("encode")),
            Err(ProtoError::ColumnKindMismatch {
                index: 3,
                declared: MetricKind::Counter as u8,
                dictionary: MetricKind::Gauge as u8,
            })
        );
    }

    #[test]
    fn a_non_zero_row_count_needs_a_rows_or_a_time_section() {
        // Three declared rows and neither section: the values cannot be addressed, so the
        // shape is refused in both directions (encoder and decoder).
        let block = live_block();
        let header = Section::new(
            SectionKind::Header,
            super::encode_header_body(&block.header),
        );
        let column = Section::new(
            SectionKind::Column,
            super::encode_column_body(&block.columns[0]).expect("column"),
        );
        let bytes = encode_frame(FrameType::Block, 1, &[header, column]).expect("encode");
        assert_eq!(
            decode_block(&bytes),
            Err(ProtoError::NoRowsOrTime { n_rows: 2 })
        );
        let mut block = live_block();
        block.rows = Vec::new();
        assert_eq!(
            encode_block(&block),
            Err(ProtoError::NoRowsOrTime { n_rows: 2 })
        );
        // One row is the smallest declared count that still needs an address: the bound is
        // `> 0`, not `> 1`, so a single row without a section is refused too.
        let block = live_block();
        let mut single = block.clone();
        single.header.n_rows = 1;
        single.rows = Vec::new();
        single.columns = vec![Column::new(
            2,
            MetricValueType::U64,
            MetricKind::Counter,
            vec![1],
            vec![Quality::Ok.code()],
        )
        .expect("column")];
        assert_eq!(
            encode_block(&single),
            Err(ProtoError::NoRowsOrTime { n_rows: 1 })
        );

        // ...and the decoder refuses the same single row without a section, so the bound
        // is enforced on both sides and not only in `encode_block`.
        let mut single_block = live_block();
        single_block.header.n_rows = 1;
        let header = Section::new(
            SectionKind::Header,
            super::encode_header_body(&single_block.header),
        );
        let column = Section::new(
            SectionKind::Column,
            super::encode_column_body(
                &Column::new(
                    2,
                    MetricValueType::U64,
                    MetricKind::Counter,
                    vec![1],
                    vec![Quality::Ok.code()],
                )
                .expect("column"),
            )
            .expect("column"),
        );
        let bytes = encode_frame(FrameType::Block, 1, &[header, column]).expect("encode");
        assert_eq!(
            decode_block(&bytes),
            Err(ProtoError::NoRowsOrTime { n_rows: 1 })
        );

        // A zero-row block is the empty selection and needs neither section.
        let mut block = live_block();
        block.header.n_rows = 0;
        block.rows = Vec::new();
        block.columns = Vec::new();
        assert!(encode_block(&block).is_ok(), "the empty selection is legal");
    }

    #[test]
    fn rows_and_buckets_are_exclusive_by_presence_even_when_empty() {
        // With n_rows == 0 the count-based check cannot fire, so the exclusivity rule is
        // presence-based: a block carrying both sections is refused even when both are empty.
        let mut block = live_block();
        block.header.n_rows = 0;
        block.rows = Vec::new();
        let empty_buckets: Vec<TimeBucket> = Vec::new();
        let header = Section::new(
            SectionKind::Header,
            super::encode_header_body(&block.header),
        );
        let rows = Section::new(SectionKind::Rows, super::encode_rows_body(&block.rows));
        let time = Section::new(
            SectionKind::Time,
            super::encode_time_body(&empty_buckets).expect("time"),
        );
        let bytes = encode_frame(FrameType::Block, 1, &[header, rows, time]).expect("encode");
        assert_eq!(
            decode_block(&bytes),
            Err(ProtoError::RowsAndTime {
                rows: 0,
                buckets: 0
            })
        );
    }

    #[test]
    fn dangling_dictionary_references_are_refused_against_the_frames_own_dictionary() {
        let mut block = live_block();
        block.rows[0].ent = 40;
        assert_eq!(
            decode_block(&encode_block(&block).expect("encode")),
            Err(ProtoError::DanglingDictionaryRef { index: 40, len: 4 })
        );
        let mut block = live_block();
        block.columns[0].metric = 40;
        assert_eq!(
            decode_block(&encode_block(&block).expect("encode")),
            Err(ProtoError::DanglingDictionaryRef { index: 40, len: 4 })
        );
        let mut block = live_block();
        block.header.dict_gen = 4;
        assert_eq!(
            decode_block(&encode_block(&block).expect("encode")),
            Err(ProtoError::StaleDictionaryGeneration { needed: 4, held: 3 })
        );
    }

    #[test]
    fn the_mode_is_decided_by_the_first_byte_and_the_version_is_strict() {
        assert_eq!(detect_mode(b"{\"op\":\"hello\"}"), Ok(Mode::Json));
        assert_eq!(detect_mode(b"{\n"), Ok(Mode::Json));
        assert_eq!(
            detect_mode(&hello()),
            Ok(Mode::Binary {
                major: PROTO_MAJOR,
                minor: PROTO_MINOR
            })
        );
        assert_eq!(
            detect_mode(b""),
            Err(ProtoError::UnknownMode { first: None })
        );
        assert_eq!(
            detect_mode(b"\x00"),
            Err(ProtoError::UnknownMode { first: Some(0) })
        );
        assert_eq!(
            detect_mode(b"H"),
            Err(ProtoError::Truncated {
                context: "binary hello",
                need: 4,
                have: 1
            }),
            "a prefix of the magic is a truncated hello, not another mode"
        );
        assert_eq!(
            detect_mode(b"HOB\x02\x01\x00\x00\x00"),
            Err(ProtoError::UnknownMode { first: Some(b'H') })
        );
        assert_eq!(&hello()[0..4], &MAGIC);
        let mut future = hello();
        future[4..6].copy_from_slice(&2u16.to_le_bytes());
        assert_eq!(
            detect_mode(&future),
            Err(ProtoError::UnsupportedVersion { major: 2 })
        );
        let mut prefix = hello();
        prefix.truncate(5);
        assert!(
            detect_mode(&prefix).is_err(),
            "a partial hello is truncated, not guessed"
        );
    }

    #[test]
    fn frame_type_and_section_kind_names_and_codes_are_stable() {
        for (code, name) in [
            (1u8, "req"),
            (2, "rep"),
            (3, "evt"),
            (4, "dict"),
            (5, "block"),
            (6, "end"),
        ] {
            let frame_type = FrameType::from_code(code).expect("known type");
            assert_eq!(frame_type.name(), name);
            assert_eq!(frame_type.code(), code);
        }
        for code in [0u8, 7, 255] {
            assert_eq!(
                FrameType::from_code(code),
                Err(ProtoError::UnknownFrameType(code))
            );
        }
        for kind in [
            SectionKind::Header,
            SectionKind::Rows,
            SectionKind::Column,
            SectionKind::Time,
            SectionKind::Dictionary,
            SectionKind::Subject,
        ] {
            assert_eq!(SectionKind::from_code(kind.code()), Some(kind));
            assert_eq!(kind.version(), 1);
            assert_eq!(
                kind.is_required(),
                kind == SectionKind::Header || kind == SectionKind::Subject,
                "the header and the subject are the required kinds"
            );
            assert_eq!(kind.is_single(), kind != SectionKind::Column);
            assert!(!kind.name().is_empty());
        }
        assert_eq!(SectionKind::from_code(9), None);
        let section = Section::new(SectionKind::Rows, vec![0; 8]);
        assert_eq!(section.kind_code(), 2);
        assert_eq!(section.version(), 1);
        assert_eq!(section.flags(), 0);
        assert!(!section.is_required());
        assert_eq!(section.body().len(), 8);
        assert!(Section::new(SectionKind::Header, vec![0; 8]).is_required());
    }

    #[test]
    fn a_section_body_must_be_eight_byte_aligned() {
        let block = live_block();
        let header = Section::new(
            SectionKind::Header,
            super::encode_header_body(&block.header),
        );
        let odd = Section::raw(9, 1, 0, vec![0u8; 3]);
        assert_eq!(
            encode_frame(FrameType::Block, 1, &[header, odd]),
            Err(ProtoError::SectionLength {
                kind: 9,
                declared: 3,
                expected: 8
            })
        );
    }

    // ---- P2a1: subject, held dictionary, aggregation mask, received-frame rules ----

    /// The header section of the shared facts.
    fn header_section() -> Section {
        Section::new(SectionKind::Header, super::encode_header_body(&facts()))
    }

    /// A history block with one bucket per aggregation in `aggs` and the given columns.
    fn history_block(aggs: &[Aggregation], columns: Vec<Column>) -> Block {
        let mut block = live_block();
        block.rows = Vec::new();
        block.header.n_rows = u32::try_from(aggs.len()).expect("small");
        block.time = aggs
            .iter()
            .map(|agg| TimeBucket {
                real_ns: 1,
                covered_ms: 1,
                samples: 1,
                agg: *agg,
            })
            .collect();
        block.columns = columns;
        block
    }

    /// A `u64` column of `rows` readings of metric `metric`.
    fn column_of(metric: u32, kind: MetricKind, rows: usize) -> Column {
        Column::new(
            metric,
            MetricValueType::U64,
            kind,
            vec![7; rows],
            vec![Quality::Ok.code(); rows],
        )
        .expect("column")
    }

    /// A history block describing incarnation 1: two `last` buckets, a counter and a gauge.
    fn subject_block() -> Block {
        let counter = column_of(2, MetricKind::Counter, 2);
        let gauge = column_of(3, MetricKind::Gauge, 2);
        let aggs = [Aggregation::Last, Aggregation::Last];
        let mut block = history_block(&aggs, vec![counter, gauge]);
        block.subject = Some(1);
        block
    }

    /// The shared dictionary with metric 2 (`cpu.usage_usec`) of kind `cpu_kind`.
    fn held_dictionary(cpu_kind: MetricKind) -> Dictionary {
        let mut held = Dictionary::new(3);
        for entry in dictionary().entries().iter().take(2) {
            held.intern(entry.clone()).expect("intern");
        }
        held.intern(Entry::Metric {
            facts: MetricFacts {
                unit: MetricUnit::Microseconds,
                kind: cpu_kind,
                scope: MetricScope::Cgroup,
                source: MetricSource::CgroupFs,
            },
            name: "cpu.usage_usec".into(),
        })
        .expect("intern");
        held
    }

    fn decode_held(bytes: &[u8], held: &Dictionary) -> Result<Block, ProtoError> {
        super::decode_block_with(bytes, Some(held))
    }

    /// Overwrite the `kind` of directory entry `index` of a frame.
    fn set_directory_kind(bytes: &mut [u8], index: usize, kind: u16) {
        let at = FRAME_HEADER_LEN + 4 + index * super::SECTION_DIR_ENTRY_LEN;
        bytes[at..at + 2].copy_from_slice(&kind.to_le_bytes());
    }

    /// Overwrite the `len` of directory entry `index` of a frame.
    fn set_directory_len(bytes: &mut [u8], index: usize, len: u32) {
        let at = FRAME_HEADER_LEN + 4 + index * super::SECTION_DIR_ENTRY_LEN + 4;
        bytes[at..at + 4].copy_from_slice(&len.to_le_bytes());
    }

    #[test]
    fn decoding_with_no_held_dictionary_is_decode_block() {
        let bytes = encode_block(&live_block()).expect("encode");
        let plain = decode_block(&bytes);
        assert_eq!(super::decode_block_with(&bytes, None), plain);
        // A bare block with a dangling row is not judged without a held dictionary: there is
        // nothing to judge it against, so `None` keeps the old behaviour exactly.
        let mut bare = live_block();
        bare.dictionary = None;
        bare.rows[0].ent = 40;
        let bytes = encode_block(&bare).expect("encode");
        let plain = decode_block(&bytes);
        assert_eq!(super::decode_block_with(&bytes, None), plain);
        assert_eq!(plain.expect("no reference check").rows[0].ent, 40);
    }

    #[test]
    fn a_bare_block_is_judged_against_the_held_dictionary() {
        let mut bare = live_block();
        bare.dictionary = None;
        let bytes = encode_block(&bare).expect("encode");
        let held = dictionary();
        assert_eq!(decode_held(&bytes, &held), Ok(bare.clone()));

        // Another generation is stale, whatever the references say.
        let newer = Dictionary::new(4);
        assert_eq!(
            decode_held(&bytes, &newer),
            Err(ProtoError::StaleDictionaryGeneration { needed: 3, held: 4 })
        );

        // A reference the held table does not hold dangles: two entries, metric 2 asked for.
        let mut short = Dictionary::new(3);
        for entry in dictionary().entries().iter().take(2) {
            short.intern(entry.clone()).expect("intern");
        }
        assert_eq!(
            decode_held(&bytes, &short),
            Err(ProtoError::DanglingDictionaryRef { index: 2, len: 2 })
        );

        // A column kind that contradicts the held metric is refused.
        let gauge_cpu = held_dictionary(MetricKind::Gauge);
        assert_eq!(
            decode_held(&bytes, &gauge_cpu),
            Err(ProtoError::ColumnKindMismatch {
                index: 2,
                declared: MetricKind::Counter as u8,
                dictionary: MetricKind::Gauge as u8,
            })
        );

        // A row that names a non-entity of the held table is refused.
        let mut other = Dictionary::new(3);
        other
            .intern(Entry::Free { text: "x".into() })
            .expect("intern");
        assert_eq!(
            decode_held(&bytes, &other),
            Err(ProtoError::RowReferenceNotEntity { index: 0 })
        );
    }

    #[test]
    fn an_in_frame_dictionary_wins_over_the_held_one() {
        let bytes = encode_block(&live_block()).expect("encode");
        let unrelated = Dictionary::new(9);
        assert_eq!(decode_held(&bytes, &unrelated), Ok(live_block()));
    }

    /// The bytes of a history block built section by section, so that a bucket the encoder
    /// refuses (`aggregation-not-permitted`) can still reach the decoder.
    fn history_bytes(block: &Block) -> Vec<u8> {
        let header = super::encode_header_body(&block.header);
        let mut sections = vec![Section::new(SectionKind::Header, header)];
        if let Some(table) = &block.dictionary {
            let body = table.encode().expect("dict");
            sections.push(Section::new(SectionKind::Dictionary, body));
        }
        let time = super::encode_time_body(&block.time).expect("time");
        sections.push(Section::new(SectionKind::Time, time));
        for column in &block.columns {
            let body = super::encode_column_body(column).expect("column");
            sections.push(Section::new(SectionKind::Column, body));
        }
        encode_frame(FrameType::Block, block.corr, &sections).expect("encode")
    }

    #[test]
    fn a_bare_history_block_has_its_aggregations_judged_with_or_without_a_held_dictionary() {
        let gauge = column_of(3, MetricKind::Gauge, 1);
        let mut bare = history_block(&[Aggregation::Raw], vec![gauge]);
        bare.dictionary = None;
        let bytes = history_bytes(&bare);
        let held = dictionary();
        let refused = Err(ProtoError::AggregationNotPermitted { metric: 3, agg: 0 });
        assert_eq!(decode_held(&bytes, &held), refused);
        // With nothing held the mask is still the column's own kind's (WIRE.md 3.10).
        assert_eq!(super::decode_block_with(&bytes, None), refused);
        assert_eq!(decode_block(&bytes), refused);
    }

    #[test]
    fn the_encoder_refuses_an_aggregation_a_column_does_not_permit() {
        // Gauge and `raw`, counter and `mean`, state and `delta`: each is refused by the
        // encoder without any dictionary, naming the column's metric and the bucket's code.
        let cases = [
            (3, MetricKind::Gauge, Aggregation::Raw, 0),
            (2, MetricKind::Counter, Aggregation::Mean, 3),
            (4, MetricKind::State, Aggregation::Delta, 2),
        ];
        for (metric, kind, agg, code) in cases {
            let column = column_of(metric, kind, 1);
            let mut block = history_block(&[agg], vec![column]);
            block.dictionary = None;
            assert_eq!(
                encode_block(&block),
                Err(ProtoError::AggregationNotPermitted { metric, agg: code }),
                "{kind:?} does not permit {agg:?}"
            );
        }
        // A permitted aggregation encodes, and the second column is judged too.
        let counter = column_of(2, MetricKind::Counter, 1);
        let gauge = column_of(3, MetricKind::Gauge, 1);
        let both = history_block(&[Aggregation::Delta], vec![counter, gauge]);
        assert_eq!(
            encode_block(&both),
            Err(ProtoError::AggregationNotPermitted { metric: 3, agg: 2 })
        );
        let last = history_block(&[Aggregation::Last], both.columns.clone());
        assert!(encode_block(&last).is_ok(), "last suits every kind");
    }

    #[test]
    fn every_aggregation_is_judged_against_every_columns_mask() {
        // WIRE.md 3.6: counters permit raw, last and delta; gauges last, mean, min and max;
        // states last. One bucket and one column, so the error names exactly that pair.
        let mut table = dictionary();
        table
            .intern(Entry::Metric {
                facts: MetricFacts {
                    unit: MetricUnit::None,
                    kind: MetricKind::State,
                    scope: MetricScope::Cgroup,
                    source: MetricSource::CgroupFs,
                },
                name: "state.value".into(),
            })
            .expect("intern");
        // The permitted aggregations as bits in code order: raw, last, delta, mean, min, max.
        let permitted = [
            (2, MetricKind::Counter, 0b0111),
            (3, MetricKind::Gauge, 0b11_1010),
            (4, MetricKind::State, 0b0010),
        ];
        for (metric, kind, mask) in permitted {
            for code in 0..6u8 {
                let agg = Aggregation::from_code(code).expect("a defined aggregation");
                let mut block = history_block(&[agg], vec![column_of(metric, kind, 1)]);
                block.dictionary = Some(table.clone());
                let decoded = decode_block(&history_bytes(&block));
                if (mask >> code) & 1 == 1 {
                    assert_eq!(decoded, Ok(block), "{kind:?} permits {agg:?}");
                } else {
                    assert_eq!(
                        decoded,
                        Err(ProtoError::AggregationNotPermitted { metric, agg: code }),
                        "{kind:?} does not permit {agg:?}"
                    );
                }
            }
        }
        // Every column is judged, not only the first: `delta` suits the counter but not the gauge.
        let counter = column_of(2, MetricKind::Counter, 1);
        let gauge = column_of(3, MetricKind::Gauge, 1);
        let both = history_block(&[Aggregation::Delta], vec![counter, gauge]);
        assert_eq!(
            decode_block(&history_bytes(&both)),
            Err(ProtoError::AggregationNotPermitted { metric: 3, agg: 2 })
        );
    }

    #[test]
    fn the_subject_body_is_the_entity_then_a_zero_word() {
        assert_eq!(super::encode_subject_body(5), vec![5, 0, 0, 0, 0, 0, 0, 0]);
        assert_eq!(
            super::encode_subject_body(0x0102_0304),
            vec![4, 3, 2, 1, 0, 0, 0, 0]
        );
        let body = super::encode_subject_body(0);
        assert_eq!(body.len(), super::SUBJECT_BODY_LEN);
        assert_eq!(super::SUBJECT_BODY_LEN, 8);
    }

    #[test]
    fn a_history_block_names_its_subject_in_a_kind_six_section() {
        let block = subject_block();
        let bytes = encode_block(&block).expect("encode");
        let decoded = decode_block(&bytes).expect("decode");
        assert_eq!(decoded, block);
        assert_eq!(decoded.subject, Some(1));
        assert_eq!(encode_block(&decoded).expect("again"), bytes);
        // The directory is header, dictionary, subject, time, columns: the subject has its
        // own rank between the dictionary and the time section, version 1, required flag set.
        let index = section_index_of(&bytes, SectionKind::Subject.code()).expect("a subject");
        assert_eq!(index, 2);
        let entry = FRAME_HEADER_LEN + 4 + index * super::SECTION_DIR_ENTRY_LEN;
        assert_eq!(&bytes[entry..entry + 4], &[6, 0, 1, 1]);
        let body = body_at(&bytes, SectionKind::Subject.code());
        assert_eq!(&bytes[body..body + 8], &[1, 0, 0, 0, 0, 0, 0, 0]);
    }

    #[test]
    fn the_hello_announces_minor_one_and_a_minor_zero_hello_is_still_accepted() {
        assert_eq!(PROTO_MINOR, 1);
        assert_eq!(hello(), vec![b'H', b'O', b'B', 1, 1, 0, 1, 0]);
        assert_eq!(
            detect_mode(b"HOB\x01\x01\x00\x00\x00"),
            Ok(Mode::Binary { major: 1, minor: 0 })
        );
    }

    #[test]
    fn a_non_zero_subject_reserved_word_is_refused() {
        let bytes = encode_block(&subject_block()).expect("encode");
        let body = body_at(&bytes, SectionKind::Subject.code());
        let mut corrupt = bytes.clone();
        corrupt[body + 4] = 1;
        assert_eq!(
            decode_block(&corrupt),
            Err(ProtoError::UnknownSectionFlags { kind: 6, flags: 1 })
        );
    }

    #[test]
    fn a_subject_body_of_the_wrong_length_is_refused() {
        let long = Section::raw(SectionKind::Subject.code(), 1, 1, vec![0u8; 16]);
        let bytes = encode_frame(FrameType::Block, 1, &[header_section(), long]).expect("encode");
        assert_eq!(
            decode_block(&bytes),
            Err(ProtoError::SectionLength {
                kind: 6,
                declared: 16,
                expected: 8,
            })
        );
    }

    #[test]
    fn a_subject_must_name_an_entity_of_the_dictionary() {
        let mut block = subject_block();
        block.subject = Some(2); // dictionary entry 2 is the cpu.usage_usec metric
        assert_eq!(
            decode_block(&encode_block(&block).expect("encode")),
            Err(ProtoError::RowReferenceNotEntity { index: 2 })
        );
        block.subject = Some(40);
        assert_eq!(
            decode_block(&encode_block(&block).expect("encode")),
            Err(ProtoError::DanglingDictionaryRef { index: 40, len: 4 })
        );
        // The held dictionary judges a bare block's subject the same way.
        block.subject = Some(2);
        block.dictionary = None;
        let bytes = encode_block(&block).expect("encode");
        let held = dictionary();
        assert_eq!(
            decode_held(&bytes, &held),
            Err(ProtoError::RowReferenceNotEntity { index: 2 })
        );
        // And an entity is accepted.
        block.subject = Some(0);
        let bytes = encode_block(&block).expect("encode");
        assert_eq!(decode_held(&bytes, &held), Ok(block));
    }

    #[test]
    fn a_subject_needs_a_time_section_in_the_decoder_and_the_encoder() {
        let mut live = live_block();
        live.subject = Some(1);
        assert_eq!(
            encode_block(&live),
            Err(ProtoError::SubjectWithoutTime { subject: 1 })
        );
        // Hand-built: rows instead of the time section, and a subject that dangles. The
        // missing time section is reported before any reference is looked at.
        let block = live_block();
        let encoded = dictionary().encode().expect("dict");
        let table = Section::new(SectionKind::Dictionary, encoded);
        let subject = Section::new(SectionKind::Subject, super::encode_subject_body(40));
        let rows = Section::new(SectionKind::Rows, super::encode_rows_body(&block.rows));
        let sections = [header_section(), table, subject, rows];
        let bytes = encode_frame(FrameType::Block, 1, &sections).expect("encode");
        assert_eq!(
            decode_block(&bytes),
            Err(ProtoError::SubjectWithoutTime { subject: 40 })
        );
    }

    #[test]
    fn the_header_and_the_subject_must_carry_the_required_flag() {
        let bytes = encode_block(&subject_block()).expect("encode");
        for kind in [SectionKind::Header, SectionKind::Subject] {
            let index = section_index_of(&bytes, kind.code()).expect("the section");
            let flags_at = FRAME_HEADER_LEN + 4 + index * super::SECTION_DIR_ENTRY_LEN + 3;
            assert_eq!(bytes[flags_at], 1, "{kind:?} flag is set");
            let mut cleared = bytes.clone();
            cleared[flags_at] = 0;
            assert_eq!(
                decode_block(&cleared),
                Err(ProtoError::UnknownSectionFlags {
                    kind: kind.code(),
                    flags: 0,
                }),
                "a clear required flag on {kind:?} is refused"
            );
        }
        // A section that is not required keeps its clear flag: the dictionary is written
        // with flags 0 and decodes.
        let index = section_index_of(&bytes, SectionKind::Dictionary.code()).expect("dictionary");
        let dictionary_flags = FRAME_HEADER_LEN + 4 + index * super::SECTION_DIR_ENTRY_LEN + 3;
        assert_eq!(bytes[dictionary_flags], 0);
        assert!(decode_block(&bytes).is_ok());
    }

    #[test]
    fn the_subject_has_its_own_place_in_the_canonical_order() {
        let encoded = dictionary().encode().expect("dict");
        let table = Section::new(SectionKind::Dictionary, encoded);
        let subject = Section::new(SectionKind::Subject, super::encode_subject_body(1));
        assert_eq!(
            encode_frame(FrameType::Block, 1, &[header_section(), subject, table]),
            Err(ProtoError::SectionOrder {
                index: 2,
                kind: SectionKind::Dictionary.code(),
            }),
            "a subject before the dictionary is not canonical"
        );
        // The decoder: the time section's and the subject's kind codes swapped in the
        // directory (kinds 1, 5, 4, 6, 3, 3), the rank check names the subject at index 3.
        let mut bytes = encode_block(&subject_block()).expect("encode");
        set_directory_kind(&mut bytes, 2, SectionKind::Time.code());
        set_directory_kind(&mut bytes, 3, SectionKind::Subject.code());
        assert_eq!(
            decode_block(&bytes),
            Err(ProtoError::SectionOrder { index: 3, kind: 6 })
        );
    }

    #[test]
    fn every_section_length_is_a_multiple_of_eight_including_unknown_kinds() {
        let unknown = Section::raw(9, 1, 0, vec![0u8; 8]);
        let mut bytes =
            encode_frame(FrameType::Block, 1, &[header_section(), unknown]).expect("encode");
        set_directory_len(&mut bytes, 1, 5);
        assert_eq!(
            decode_block(&bytes),
            Err(ProtoError::SectionLength {
                kind: 9,
                declared: 5,
                expected: 8,
            })
        );
        // A known kind is judged the same way, and before the order: the header and the
        // dictionary are swapped in the directory as well, which alone is `section-order`.
        let live = encode_block(&live_block()).expect("encode");
        let mut known = live.clone();
        set_directory_len(&mut known, 2, 12);
        assert_eq!(
            decode_block(&known),
            Err(ProtoError::SectionLength {
                kind: 2,
                declared: 12,
                expected: 16,
            })
        );
        set_directory_kind(&mut known, 0, SectionKind::Dictionary.code());
        set_directory_kind(&mut known, 1, SectionKind::Header.code());
        assert_eq!(
            decode_block(&known),
            Err(ProtoError::SectionLength {
                kind: 2,
                declared: 12,
                expected: 16,
            })
        );
        let mut swapped = live;
        set_directory_kind(&mut swapped, 0, SectionKind::Dictionary.code());
        set_directory_kind(&mut swapped, 1, SectionKind::Header.code());
        assert_eq!(
            decode_block(&swapped),
            Err(ProtoError::SectionOrder { index: 1, kind: 1 }),
            "the section-order fixture shape: kinds 5, 1, 2, 3, 3 name index 1, kind 1"
        );
    }

    #[test]
    fn entry_zero_must_be_the_header_and_is_judged_after_the_rank_check() {
        let live = encode_block(&live_block()).expect("encode");
        // An unknown kind at entry 0 with the known kinds in order: the entry-0 check.
        let mut unknown_first = live.clone();
        set_directory_kind(&mut unknown_first, 0, 9);
        assert_eq!(
            decode_block(&unknown_first),
            Err(ProtoError::SectionOrder { index: 0, kind: 9 })
        );
        // The same, with a rank violation further on (kinds 9, 5, 1, 3, 3): the rank check
        // runs first and names the header's index.
        let mut both = live;
        set_directory_kind(&mut both, 0, 9);
        set_directory_kind(&mut both, 2, SectionKind::Header.code());
        assert_eq!(
            decode_block(&both),
            Err(ProtoError::SectionOrder { index: 2, kind: 1 })
        );
    }

    #[test]
    fn a_directory_the_frame_cannot_hold_is_refused_by_its_byte_count() {
        // 16 bytes: the frame header, then n_sec = 65535 and the reserved word. The directory
        // would be 65535 * 8 = 524_280 bytes and none is present; nothing is reserved for it.
        let mut bytes = header_declaring(16).to_vec();
        bytes.extend_from_slice(&u16::MAX.to_le_bytes());
        bytes.extend_from_slice(&0u16.to_le_bytes());
        assert_eq!(
            decode_block(&bytes),
            Err(ProtoError::Truncated {
                context: "block directory",
                need: 524_280,
                have: 0,
            })
        );
        // Two entries declared and one present: the directory's own need and have.
        let mut one = header_declaring(24).to_vec();
        one.extend_from_slice(&2u16.to_le_bytes());
        one.extend_from_slice(&[0u8; 10]);
        assert_eq!(
            decode_block(&one),
            Err(ProtoError::Truncated {
                context: "block directory",
                need: 16,
                have: 8,
            })
        );
    }

    #[test]
    fn not_a_block_is_judged_before_the_buffer_length() {
        // A REQ declaring 100 bytes and holding 12 is not-a-block, the same bytes typed BLOCK
        // are truncated, and a REQ with trailing bytes is not-a-block as well.
        let mut req = header_declaring(100);
        req[4] = FrameType::Req.code();
        req[5] = 0;
        assert_eq!(
            decode_block(&req),
            Err(ProtoError::NotABlock { frame_type: 1 })
        );
        assert_eq!(
            decode_block(&header_declaring(100)),
            Err(ProtoError::Truncated {
                context: "frame",
                need: 100,
                have: 12,
            })
        );
        let mut long_req = header_declaring(12).to_vec();
        long_req[4] = FrameType::Req.code();
        long_req[5] = 0;
        long_req.extend_from_slice(&[0u8; 8]);
        assert_eq!(
            decode_block(&long_req),
            Err(ProtoError::NotABlock { frame_type: 1 })
        );
        // And the buffer length is judged before SECTIONED: a BLOCK without the flag and
        // with trailing bytes is `trailing-bytes`, not `missing-section`.
        let mut flagless = header_declaring(12).to_vec();
        flagless[5] = 0;
        flagless.extend_from_slice(&[0u8; 8]);
        assert_eq!(
            decode_block(&flagless),
            Err(ProtoError::TrailingBytes { extra: 8 })
        );
    }

    #[test]
    fn a_bad_subject_is_judged_before_a_bad_column() {
        // WIRE.md 3.10 cross-section order: generation, subject, rows, columns, mask. Both
        // defects are present and the subject is the one named; without it the column is.
        let mut block = subject_block();
        block.subject = Some(40);
        block.columns[0].kind = MetricKind::Gauge; // the dictionary says counter
        assert_eq!(
            decode_block(&encode_block(&block).expect("encode")),
            Err(ProtoError::DanglingDictionaryRef { index: 40, len: 4 })
        );
        block.subject = Some(1);
        assert_eq!(
            decode_block(&encode_block(&block).expect("encode")),
            Err(ProtoError::ColumnKindMismatch {
                index: 2,
                declared: MetricKind::Gauge as u8,
                dictionary: MetricKind::Counter as u8,
            })
        );
    }

    #[test]
    fn missing_rows_and_time_is_judged_before_a_subject_without_time() {
        // A subject, a column and a declared row count, with neither rows nor time: both
        // `no-rows-or-time` and `subject-without-time` apply, and the first is the one named.
        let block = live_block();
        let encoded = dictionary().encode().expect("dict");
        let table = Section::new(SectionKind::Dictionary, encoded);
        let subject = Section::new(SectionKind::Subject, super::encode_subject_body(0));
        let body = super::encode_column_body(&block.columns[0]).expect("column");
        let column = Section::new(SectionKind::Column, body);
        let sections = [header_section(), table, subject, column];
        let bytes = encode_frame(FrameType::Block, 1, &sections).expect("encode");
        assert_eq!(
            decode_block(&bytes),
            Err(ProtoError::NoRowsOrTime { n_rows: 2 })
        );
    }

    #[test]
    fn the_frame_header_fixed_fields_are_judged_in_wire_order() {
        // The type byte, then the flags, then the reserved word: each is judged as soon as it
        // is read, so a header with several defects names the first one on the wire.
        let mut typed = header_declaring(12);
        typed[4] = 9;
        typed[5] = 0x80;
        typed[6] = 1;
        assert_eq!(
            FrameHeader::parse(&typed),
            Err(ProtoError::UnknownFrameType(9))
        );
        let mut flagged = header_declaring(12);
        flagged[5] = 0x80;
        flagged[6] = 1;
        assert_eq!(
            FrameHeader::parse(&flagged),
            Err(ProtoError::UnknownFrameFlags(0x80))
        );
        // SECTIONED on a REQ is an unknown flag, and it is judged before the reserved word.
        let mut request = header_declaring(12);
        request[4] = FrameType::Req.code();
        request[6] = 1;
        assert_eq!(
            FrameHeader::parse(&request),
            Err(ProtoError::UnknownFrameFlags(super::FLAG_SECTIONED))
        );
    }

    #[test]
    fn a_directory_that_exactly_fills_the_frame_passes_the_byte_count_check() {
        // 12 + 4 header bytes and one 8-byte entry, no body: the directory byte count equals
        // what is present, so the directory is read and the unknown kind at entry 0 is what
        // is refused, not a truncation.
        let mut bytes = header_declaring(24).to_vec();
        bytes.extend_from_slice(&1u16.to_le_bytes());
        bytes.extend_from_slice(&0u16.to_le_bytes());
        bytes.extend_from_slice(&[9, 0, 1, 0, 0, 0, 0, 0]);
        assert_eq!(
            decode_block(&bytes),
            Err(ProtoError::SectionOrder { index: 0, kind: 9 })
        );
    }
}
