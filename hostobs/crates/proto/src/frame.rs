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
//!
//! A block carries rows **or** time buckets; both at once, or columns for a row count
//! nobody declared, are refused.

use crate::dict::{Aggregation, Dictionary, MetricKind, MetricValueType};
use crate::error::{ProtoError, MAX_FRAME_BYTES};
use crate::quality::Quality;
use crate::read::Cursor;
use crate::write::Writer;

/// Magic of the binary mode (plan v4 §B.1).
pub const MAGIC: [u8; 4] = *b"HOB\x01";
/// The protocol major this codec speaks.
pub const PROTO_MAJOR: u16 = 1;
/// The protocol minor this codec speaks; an additive change raises it.
pub const PROTO_MINOR: u16 = 0;
/// The frame header size in bytes.
pub const FRAME_HEADER_LEN: usize = 12;
/// The section directory entry size in bytes.
pub const SECTION_DIR_ENTRY_LEN: usize = 8;
/// The header section body size (80 bytes, eight-aligned).
pub const HEADER_BODY_LEN: usize = 80;

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
        matches!(self, SectionKind::Header)
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
        let raw = *self.values.get(row).ok_or(ProtoError::Truncated {
            context: "column values",
            need: row + 1,
            have: self.values.len(),
        })?;
        let code = *self.quality.get(row).ok_or(ProtoError::Truncated {
            context: "column quality",
            need: row + 1,
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
    /// # Errors
    ///
    /// [`ProtoError::Truncated`], [`ProtoError::UnknownFrameType`],
    /// [`ProtoError::UnknownFrameFlags`] or [`ProtoError::NonZeroReserved`].
    pub fn parse(bytes: &[u8]) -> Result<FrameHeader, ProtoError> {
        let mut cursor = Cursor::new(bytes, "frame header");
        let len = cursor.u32()?;
        let frame_type = FrameType::from_code(cursor.u8()?)?;
        let flags = cursor.u8()?;
        if flags & !DEFINED_FRAME_FLAGS != 0 {
            return Err(ProtoError::UnknownFrameFlags(flags));
        }
        let reserved = cursor.u16()?;
        if reserved != 0 {
            return Err(ProtoError::NonZeroReserved(reserved));
        }
        let corr = cursor.u32()?;
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

fn time_body_len(buckets: usize) -> Result<usize, ProtoError> {
    let raw = buckets.checked_mul(14).ok_or(ProtoError::LengthOverflow)?;
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

/// Encode a block (with its frame) as canonical bytes.
///
/// # Errors
///
/// [`ProtoError::OversizedFrame`], [`ProtoError::RowsAndTime`],
/// [`ProtoError::DuplicateSection`], [`ProtoError::SectionLength`] (a column whose row
/// count differs from `header.n_rows`) and every dictionary-encoding refusal.
pub fn encode_block(block: &Block) -> Result<Vec<u8>, ProtoError> {
    if !block.rows.is_empty() && !block.time.is_empty() {
        return Err(ProtoError::RowsAndTime {
            rows: block.rows.len(),
            buckets: block.time.len(),
        });
    }
    let n_rows = usize::try_from(block.header.n_rows).map_err(|_| ProtoError::LengthOverflow)?;
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
    let mut sections = Vec::new();
    sections.push(Section::new(
        SectionKind::Header,
        encode_header_body(&block.header),
    ));
    if let Some(dictionary) = &block.dictionary {
        sections.push(Section::new(SectionKind::Dictionary, dictionary.encode()?));
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
    let header_frame = FrameHeader::parse(bytes)?;
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
    let mut cursor = Cursor::new(&bytes[FRAME_HEADER_LEN..], "block directory");
    let n_sec = cursor.u16()?;
    let reserved = cursor.u16()?;
    if reserved != 0 {
        return Err(ProtoError::SectionReserved(reserved));
    }
    let mut directory = Vec::with_capacity(usize::from(n_sec));
    for _ in 0..n_sec {
        let kind = cursor.u16()?;
        let version = cursor.u8()?;
        let flags = cursor.u8()?;
        let len = cursor.u32()?;
        directory.push((kind, version, flags, len));
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
    let mut dictionary: Option<Dictionary> = None;
    let mut seen_single: Vec<UnknownSectionKind> = Vec::new();
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
            SectionKind::Rows => rows = decode_rows_body(body, n_rows)?,
            SectionKind::Time => time = decode_time_body(body, n_rows)?,
            SectionKind::Column => {
                let column = decode_column_body(body, n_rows)?;
                if columns
                    .iter()
                    .any(|existing| existing.metric == column.metric)
                {
                    return Err(ProtoError::ConflictingColumn {
                        metric: column.metric,
                    });
                }
                columns.push(column);
            }
            SectionKind::Dictionary => {
                dictionary = Some(Dictionary::decode(body, crate::dict::DEFAULT_ACTIVE_MAX)?)
            }
        }
    }
    if !rows.is_empty() && !time.is_empty() {
        return Err(ProtoError::RowsAndTime {
            rows: rows.len(),
            buckets: time.len(),
        });
    }
    if let Some(table) = &dictionary {
        table.require_generation(header.dict_gen)?;
        for row in &rows {
            table.lookup(row.ent)?;
        }
        for column in &columns {
            table.lookup(column.metric)?;
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
                agg: Aggregation::Raw,
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
                agg: Aggregation::Mean,
            },
            TimeBucket {
                real_ns: -1,
                covered_ms: 0,
                samples: 0,
                agg: Aggregation::Raw,
            },
        ];
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
        assert!(
            encode_block(&block).is_ok(),
            "a block without rows has no rows section"
        );
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
            &[header.clone(), rows.clone(), dictionary],
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
        let block = live_block();
        let mut bytes = encode_block(&block).expect("encode");
        // Replace the header section's declared kind with an optional unknown one.
        let dir_start = FRAME_HEADER_LEN + 4;
        bytes[dir_start] = 9;
        bytes[dir_start + 3] = 0;
        assert_eq!(
            decode_block(&bytes),
            Err(ProtoError::MissingSection {
                kind: SectionKind::Header.code()
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
        let block = live_block();
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
        ] {
            assert_eq!(SectionKind::from_code(kind.code()), Some(kind));
            assert_eq!(kind.version(), 1);
            assert_eq!(kind.is_required(), kind == SectionKind::Header);
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
}
