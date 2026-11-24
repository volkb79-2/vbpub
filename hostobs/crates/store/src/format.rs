//! The on-disk segment format: a 64-byte header, a sequence of records, an optional 64-byte
//! footer, and the file naming that ties each file to its tier and its start time.
//!
//! This module is the *only* place that knows a byte offset. Everything above it works on
//! `SegmentHeader`/`RecordHeader`/`Footer` values, and everything below it is [`crate::fsio`].
//! The normative description is [`docs/SEGMENT-FORMAT.md`](../../../docs/SEGMENT-FORMAT.md);
//! the independent Python reader (`python/reference/hostobs_segment_ref.py`) implements the
//! same document, so a layout change that only one side makes turns a lane red.
//!
//! # Why an envelope at all, when `proto` already has a frame?
//!
//! A wire frame is validated by its own lengths, types and section directory — over a
//! stream, where a truncated frame is the connection's problem. A *file* is different: a
//! crash, a full disk or a killed process can leave a record half-written, and the frames
//! after it are still perfectly readable. §C.5 asks for a checksum **per block**, and the
//! envelope below supplies it: a record's own header CRC covers where it ends, so the reader
//! knows exactly how many bytes are missing, and the payload CRC says whether the bytes it
//! did find are the bytes that were written. Without that, a torn tail would look like a
//! short-but-valid block and be served as data.
//!
//! # Layout (little-endian, every field fixed width)
//!
//! ```text
//! SEGMENT := HEADER RECORD* FOOTER?
//!
//! HEADER (64)  0 magic7 "HOBSEG\x01"      7 u8 0                8 u16 version=1
//!             10 u16 header_len=64
//!             12 u64 seq                20 u64 epoch          28 u32 dict_generation
//!             32 u8 kind                33 u8 payload         34 u16 0
//!             36 i64 start_real_ns      44 u32 max_payload     48 u32 max_incarnations
//!             52 u32 0                  56 u32 header_crc     60 u32 0
//!
//! RECORD (48 + body + pad)             pad = (8 - body % 8) & 7, zero bytes
//!              body = ids[8 × n_incarnations] || payload
//!              0 magic4 "HOBR"          4 u32 record_len       8 u64 seq
//!             16 u8 kind                17 u8 flags           18 u16 0
//!             20 u32 body_len          24 i64 real_ns         32 u16 n_incarnations
//!             34 u16 0                  36 u32 body_crc       40 u8 pad
//!             41 u8[3] 0                44 u32 header_crc
//!
//! FOOTER (64)  0 magic4 "HOBF"          4 u32 footer_len=64    8 u64 seq
//!             16 u64 record_count       24 u64 end_offset     32 u32 dict_generation
//!             36 u32 0                  40 i64 first_real_ns  48 i64 last_real_ns
//!             56 u32 footer_crc         60 u32 0
//! ```
//!
//! `header_crc` covers the 56 bytes before it (for the header) or the 44 bytes before it
//! (for the record); `body_crc` covers the record's whole body — **the incarnation ids and
//! the payload together** — and `footer_crc` covers the 56 bytes before it. A checksum that
//! covered the field it lives in could never be checked.
//!
//! The id table lives *inside* the record for a reason: the store must be able to answer
//! "which incarnations can any stored block still reference?" from the files alone, without
//! decoding a payload (which is `proto`'s job, not this crate's). That answer is what drives
//! dictionary retirement (§A.4: "inactive incarnations retire with the segments that
//! reference them"), so it has to survive a restart — it cannot live only in RAM.
//!
//! # Contract refusals this module owns
//!
//! * A header that is not this version, longer or shorter than 64 bytes, not `HOBSEG`, with
//!   a non-zero reserved field or a payload byte other than `1` is a typed refusal.
//! * A record whose kind is not `1`, whose sequence is not the position the reader is at,
//!   whose declared padding is not the implied padding, or whose lengths disagree is a typed
//!   refusal — a reader never *infers* a record's length from what happens to follow it.
//! * A payload must be the `proto` frame its corpus requires (BLOCK for history, EVT for
//!   events), and this module checks it before any byte is stored. The fixed fields come from
//!   [`hostobs_proto::frame::StoredFrameFields`], not a second header parser. The segment
//!   format applies its corpus frame type and SECTIONED flag and its configured payload
//!   bound; every question about the block's content remains `proto`'s.
//! * A footer must follow the records it counts, name the header's sequence and generation,
//!   and end the file exactly.

use core::fmt;

use hostobs_proto::frame::{FrameType, StoredFrameFields};

use crate::crc::{crc32, crc32_pair};
use crate::error::StoreError;

/// The seven-byte segment magic: `HOBSEG` and the format version byte `\x01`.
pub const SEGMENT_MAGIC: [u8; 7] = *b"HOBSEG\x01";

/// The segment format version this codec writes and reads.
pub const SEGMENT_VERSION: u16 = 1;

/// The segment header's length in bytes.
pub const HEADER_LEN: usize = 64;

/// The record magic.
pub const RECORD_MAGIC: [u8; 4] = *b"HOBR";

/// The record header's length in bytes.
pub const RECORD_HEADER_LEN: usize = 48;

/// The footer magic.
pub const FOOTER_MAGIC: [u8; 4] = *b"HOBF";

/// The footer's length in bytes.
pub const FOOTER_LEN: usize = 64;

/// Payload kind 1: a `proto` BLOCK frame (host history, plan v4 §B.1).
pub const PAYLOAD_BLOCK: u8 = 1;

/// Payload kind 2: a `proto` EVT frame (events and typed spans, plan v4 §C.4).
pub const PAYLOAD_EVT: u8 = 2;

/// The frame type byte of a `proto` BLOCK frame (plan v4 §B.1).
pub const BLOCK_FRAME_TYPE: u8 = FrameType::Block.code();

/// The frame type byte of a `proto` EVT frame (plan v4 §B.1).
pub const EVT_FRAME_TYPE: u8 = FrameType::Evt.code();

/// The frame flag bit a block must carry (plan v4 §B.1 `FLAG_SECTIONED`).
pub const FRAME_FLAG_SECTIONED: u8 = hostobs_proto::frame::FLAG_SECTIONED;

/// The frame header's length (plan v4 §B.1).
pub const FRAME_HEADER_LEN: usize = hostobs_proto::frame::FRAME_HEADER_LEN;

/// The largest padding a record may carry: records start eight-byte aligned.
pub const MAX_RECORD_PAD: usize = 7;

/// Record kind 1: the record carries a block payload.
pub const RECORD_BLOCK: u8 = 1;

/// Record kind 2: the record carries an event payload.
pub const RECORD_EVT: u8 = 2;

/// Which corpus a segment belongs to (plan v4 §A.4: history tiers and the events/spans store).
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
#[repr(u8)]
pub enum TierKind {
    /// Host history (hot, warm, cold, focus): `proto` BLOCK frames of sampled values.
    History = 1,
    /// The events and typed spans store (plan v4 §C.4).
    Events = 2,
}

impl TierKind {
    /// The stored byte.
    #[must_use]
    pub const fn code(self) -> u8 {
        self as u8
    }

    /// The payload kind this corpus stores, written into every segment header.
    ///
    /// History stores `proto` BLOCK frames and the events store stores `proto` EVT frames, so
    /// the two corpora can never be confused: a header, and through it every record it
    /// contains, names one corpus and one payload kind, and both are checked before anything
    /// is appended or served.
    #[must_use]
    pub const fn payload_kind(self) -> u8 {
        match self {
            TierKind::History => PAYLOAD_BLOCK,
            TierKind::Events => PAYLOAD_EVT,
        }
    }

    /// The record kind that carries this corpus's payload.
    #[must_use]
    pub const fn record_kind(self) -> u8 {
        match self {
            TierKind::History => RECORD_BLOCK,
            TierKind::Events => RECORD_EVT,
        }
    }

    /// The `proto` frame type this corpus's payloads must be.
    #[must_use]
    pub const fn frame_type(self) -> u8 {
        match self {
            TierKind::History => BLOCK_FRAME_TYPE,
            TierKind::Events => EVT_FRAME_TYPE,
        }
    }

    /// The corpus a payload kind names.
    ///
    /// # Errors
    ///
    /// [`StoreError::PayloadKind`] for a byte outside the closed vocabulary.
    pub const fn from_payload_kind(payload: u8) -> Result<TierKind, StoreError> {
        match payload {
            PAYLOAD_BLOCK => Ok(TierKind::History),
            PAYLOAD_EVT => Ok(TierKind::Events),
            found => Err(StoreError::PayloadKind { found }),
        }
    }

    /// The kind a stored byte names.
    ///
    /// # Errors
    ///
    /// [`StoreError::Reserved`] for a byte outside the closed vocabulary.
    pub const fn from_code(code: u8) -> Result<TierKind, StoreError> {
        match code {
            1 => Ok(TierKind::History),
            2 => Ok(TierKind::Events),
            found => Err(StoreError::Reserved {
                field: "segment kind",
                found: found as u32,
            }),
        }
    }

    /// The stable lower-case name used in reports.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            TierKind::History => "history",
            TierKind::Events => "events",
        }
    }
}

impl fmt::Display for TierKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.name())
    }
}

/// The facts a segment header declares.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SegmentHeader {
    /// The tier-local segment sequence number (monotone, never reused).
    pub seq: u64,
    /// The probe epoch the records were produced in (plan v4 §C.1).
    pub epoch: u64,
    /// The dictionary generation the records' indices refer to.
    pub dict_generation: u32,
    /// Which corpus this segment belongs to.
    pub kind: TierKind,
    /// The payload kind the header carries; must agree with [`SegmentHeader::kind`]
    /// ([`TierKind::payload_kind`]).
    pub payload: u8,
    /// The real time of the segment's first record, or `0` while it holds none.
    pub start_real_ns: i64,
    /// The largest payload the writer will store in this segment.
    pub max_payload: u32,
    /// The largest number of incarnation ids one record may name.
    pub max_incarnations: u32,
}

impl SegmentHeader {
    /// Encode this header as its 64 canonical bytes.
    ///
    /// # Errors
    ///
    /// [`StoreError::PayloadKind`] when the payload byte is not the one its `kind` requires:
    /// a header may only declare the corpus it belongs to, so a history segment can never
    /// claim to hold events.
    pub fn encode(&self) -> Result<Vec<u8>, StoreError> {
        if self.payload != self.kind.payload_kind() {
            return Err(StoreError::PayloadKind {
                found: self.payload,
            });
        }
        let mut out = Vec::with_capacity(HEADER_LEN);
        out.extend_from_slice(&SEGMENT_MAGIC);
        // Byte 7 stays reserved: the version follows at byte 8, so a future format can
        // widen the magic without moving any other field.
        out.push(0);
        out.extend_from_slice(&SEGMENT_VERSION.to_le_bytes());
        out.extend_from_slice(&64u16.to_le_bytes());
        out.extend_from_slice(&self.seq.to_le_bytes());
        out.extend_from_slice(&self.epoch.to_le_bytes());
        out.extend_from_slice(&self.dict_generation.to_le_bytes());
        out.push(self.kind.code());
        out.push(self.payload);
        out.extend_from_slice(&0u16.to_le_bytes());
        out.extend_from_slice(&self.start_real_ns.to_le_bytes());
        out.extend_from_slice(&self.max_payload.to_le_bytes());
        out.extend_from_slice(&self.max_incarnations.to_le_bytes());
        out.extend_from_slice(&0u32.to_le_bytes());
        // The checksum covers the 56 bytes written so far, never the field it lives in.
        let crc = crc32(&out);
        out.extend_from_slice(&crc.to_le_bytes());
        out.extend_from_slice(&0u32.to_le_bytes());
        Ok(out)
    }

    /// Decode a segment header from the first [`HEADER_LEN`] bytes of `bytes`.
    ///
    /// # Errors
    ///
    /// [`StoreError::Truncated`] for a short buffer, and every header refusal of
    /// [`SegmentHeader::check_bytes`].
    pub fn decode(bytes: &[u8]) -> Result<SegmentHeader, StoreError> {
        let header = bytes.get(..HEADER_LEN).ok_or(StoreError::Truncated {
            context: "segment header",
            need: HEADER_LEN,
            have: bytes.len(),
        })?;
        SegmentHeader::check_bytes(header)
    }

    /// Check one already-sized header buffer.
    ///
    /// # Errors
    ///
    /// [`StoreError::HeaderMagic`], [`StoreError::HeaderVersion`],
    /// [`StoreError::HeaderLength`], [`StoreError::Reserved`],
    /// [`StoreError::PayloadKind`] and [`StoreError::HeaderCrc`].
    pub fn check_bytes(header: &[u8]) -> Result<SegmentHeader, StoreError> {
        if header.len() != HEADER_LEN {
            return Err(StoreError::HeaderLength {
                declared: u32::try_from(header.len()).map_err(|_| StoreError::LengthOverflow)?,
                expected: u32::try_from(HEADER_LEN).map_err(|_| StoreError::LengthOverflow)?,
            });
        }
        let magic = header.get(..7).ok_or(StoreError::Truncated {
            context: "segment header magic",
            need: 7,
            have: header.len(),
        })?;
        if magic != SEGMENT_MAGIC {
            let mut found = [0u8; 7];
            for (slot, byte) in found.iter_mut().zip(magic.iter()) {
                *slot = *byte;
            }
            return Err(StoreError::HeaderMagic { found });
        }
        let reserved_magic = read_u8(header, 7)?;
        if reserved_magic != 0 {
            return Err(StoreError::Reserved {
                field: "segment header magic reserved",
                found: u32::from(reserved_magic),
            });
        }
        let version = read_u16(header, 8)?;
        if version != SEGMENT_VERSION {
            return Err(StoreError::HeaderVersion {
                found: version,
                supported: SEGMENT_VERSION,
            });
        }
        let declared_len = read_u16(header, 10)?;
        if usize::from(declared_len) != HEADER_LEN {
            return Err(StoreError::HeaderLength {
                declared: u32::from(declared_len),
                expected: u32::try_from(HEADER_LEN).map_err(|_| StoreError::LengthOverflow)?,
            });
        }
        let seq = read_u64(header, 12)?;
        let epoch = read_u64(header, 20)?;
        let dict_generation = read_u32(header, 28)?;
        let kind = TierKind::from_code(read_u8(header, 32)?)?;
        let payload = read_u8(header, 33)?;
        if payload != kind.payload_kind() {
            return Err(StoreError::TierKindMismatch {
                tier: kind.name(),
                payload: match payload {
                    PAYLOAD_BLOCK => "block",
                    PAYLOAD_EVT => "evt",
                    _ => "outside-the-vocabulary",
                },
            });
        }
        check_zero(header, 34, 2, "segment header reserved")?;
        let start_real_ns = read_i64(header, 36)?;
        let max_payload = read_u32(header, 44)?;
        let max_incarnations = read_u32(header, 48)?;
        check_zero(header, 52, 4, "segment header reserved")?;
        let declared_crc = read_u32(header, 56)?;
        let crc_bytes = header.get(..56).ok_or(StoreError::Truncated {
            context: "segment header checksum input",
            need: 56,
            have: header.len(),
        })?;
        let computed = crc32(crc_bytes);
        if computed != declared_crc {
            return Err(StoreError::HeaderCrc {
                declared: declared_crc,
                computed,
            });
        }
        check_zero(header, 60, 4, "segment header reserved")?;
        Ok(SegmentHeader {
            seq,
            epoch,
            dict_generation,
            kind,
            payload,
            start_real_ns,
            max_payload,
            max_incarnations,
        })
    }
}

/// The facts a record header declares.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct RecordHeader {
    /// The record's sequence number within the segment (0-based, monotone).
    pub seq: u64,
    /// The record kind; version 1 defines [`RECORD_BLOCK`] (history) and [`RECORD_EVT`]
    /// (events). Which of the two a segment holds is the segment header's corpus, checked by
    /// the reader that knows it — this header check only refuses a kind outside the
    /// vocabulary.
    pub kind: u8,
    /// Real time of the block's end, nanoseconds since the Unix epoch.
    pub real_ns: i64,
    /// The body's length: the id table followed by the payload.
    pub body_len: u32,
    /// The number of incarnation ids the body starts with.
    pub incarnations: u16,
    /// The checksum over the whole body (ids and payload together).
    pub body_crc: u32,
}

impl RecordHeader {
    /// The record's whole length in bytes: header, body, and the padding that aligns the
    /// next record to eight bytes.
    ///
    /// # Errors
    ///
    /// [`StoreError::LengthOverflow`] when the total does not fit a `usize`.
    pub fn record_len(&self) -> Result<usize, StoreError> {
        let body = usize::try_from(self.body_len).map_err(|_| StoreError::LengthOverflow)?;
        RECORD_HEADER_LEN
            .checked_add(body)
            .and_then(|value| value.checked_add(pad_for(body).ok()?))
            .ok_or(StoreError::LengthOverflow)
    }

    /// The bytes the body must start with: one `u64` per incarnation, little-endian.
    ///
    /// # Errors
    ///
    /// [`StoreError::LengthOverflow`] when the count cannot be a length.
    pub fn id_table_len(&self) -> Result<usize, StoreError> {
        usize::from(self.incarnations)
            .checked_mul(8)
            .ok_or(StoreError::LengthOverflow)
    }

    /// The payload's length: the body minus its id table.
    ///
    /// # Errors
    ///
    /// [`StoreError::PayloadLength`] when the body is shorter than the table it declares.
    pub fn payload_len(&self) -> Result<usize, StoreError> {
        let body = usize::try_from(self.body_len).map_err(|_| StoreError::LengthOverflow)?;
        let table = self.id_table_len()?;
        body.checked_sub(table).ok_or(StoreError::PayloadLength {
            index: usize::try_from(self.seq).unwrap_or(usize::MAX),
            declared: usize::try_from(self.body_len).unwrap_or(usize::MAX),
            actual: table,
        })
    }

    /// Encode a whole record (header, body and padding) from `incarnations` and `payload`.
    ///
    /// The declared lengths and the checksum are **computed here** from the bytes being
    /// written, so a header and a body cannot disagree: there is no way to build a record
    /// whose header says one length and whose body is another.
    ///
    /// # Errors
    ///
    /// [`StoreError::PayloadTooLarge`] when the payload exceeds `max_payload`,
    /// [`StoreError::RecordIncarnations`] when it names more ids than `max_incarnations`,
    /// and [`StoreError::LengthOverflow`].
    pub fn encode_record(
        seq: u64,
        kind: u8,
        real_ns: i64,
        incarnations: &[u64],
        payload: &[u8],
        max_payload: u32,
        max_incarnations: u32,
    ) -> Result<Vec<u8>, StoreError> {
        let index = usize::try_from(seq).map_err(|_| StoreError::LengthOverflow)?;
        let payload_len = u32::try_from(payload.len()).map_err(|_| StoreError::LengthOverflow)?;
        if payload_len > max_payload {
            return Err(StoreError::PayloadTooLarge {
                len: payload.len(),
                max: usize::try_from(max_payload).map_err(|_| StoreError::LengthOverflow)?,
            });
        }
        let count = u16::try_from(incarnations.len()).map_err(|_| StoreError::LengthOverflow)?;
        if u32::from(count) > max_incarnations {
            return Err(StoreError::RecordIncarnations {
                index,
                count: usize::from(count),
                max: usize::try_from(max_incarnations).map_err(|_| StoreError::LengthOverflow)?,
            });
        }
        let table_len = incarnations
            .len()
            .checked_mul(8)
            .ok_or(StoreError::LengthOverflow)?;
        let body_len_usize = table_len
            .checked_add(payload.len())
            .ok_or(StoreError::LengthOverflow)?;
        let body_len = u32::try_from(body_len_usize).map_err(|_| StoreError::LengthOverflow)?;
        let mut body = Vec::with_capacity(body_len_usize);
        for inc in incarnations {
            body.extend_from_slice(&inc.to_le_bytes());
        }
        body.extend_from_slice(payload);
        let body_crc = crc32(&body);
        let pad = pad_for(body_len_usize)?;
        let total = RECORD_HEADER_LEN
            .checked_add(body_len_usize)
            .and_then(|value| value.checked_add(pad))
            .ok_or(StoreError::LengthOverflow)?;
        let header = RecordHeader {
            seq,
            kind,
            real_ns,
            body_len,
            incarnations: count,
            body_crc,
        };
        let mut out = header.encode_header(total, pad)?;
        out.extend_from_slice(&body);
        out.extend(core::iter::repeat_n(0u8, pad));
        Ok(out)
    }

    /// The 48 header bytes for a record of `total` bytes with `pad` padding.
    ///
    /// # Errors
    ///
    /// [`StoreError::LengthOverflow`] when the padding cannot be a byte.
    fn encode_header(&self, total: usize, pad: usize) -> Result<Vec<u8>, StoreError> {
        let total = u32::try_from(total).map_err(|_| StoreError::LengthOverflow)?;
        let pad = u8::try_from(pad).map_err(|_| StoreError::LengthOverflow)?;
        let mut out = Vec::with_capacity(RECORD_HEADER_LEN);
        out.extend_from_slice(&RECORD_MAGIC);
        out.extend_from_slice(&total.to_le_bytes());
        out.extend_from_slice(&self.seq.to_le_bytes());
        out.push(self.kind);
        out.push(0);
        out.extend_from_slice(&0u16.to_le_bytes());
        out.extend_from_slice(&self.body_len.to_le_bytes());
        out.extend_from_slice(&self.real_ns.to_le_bytes());
        out.extend_from_slice(&self.incarnations.to_le_bytes());
        out.extend_from_slice(&0u16.to_le_bytes());
        out.extend_from_slice(&self.body_crc.to_le_bytes());
        out.push(pad);
        out.extend_from_slice(&[0u8; 3]);
        // The checksum is over the 44 bytes just written and is computed from those bytes,
        // so no field can be encoded twice in two different ways.
        let crc = crc32(&out);
        out.extend_from_slice(&crc.to_le_bytes());
        Ok(out)
    }

    /// Check a record header from the 48 bytes at the start of a record.
    ///
    /// # Errors
    ///
    /// [`StoreError::RecordMagic`], [`StoreError::RecordHeaderCrc`],
    /// [`StoreError::RecordKind`], [`StoreError::RecordPadding`],
    /// [`StoreError::RecordIncarnations`] and [`StoreError::Truncated`]. The sequence number
    /// is *returned*, not checked: the caller knows which position it is reading.
    pub fn check_bytes(
        record: &[u8],
        index: usize,
        max_incarnations: u32,
    ) -> Result<RecordHeader, StoreError> {
        let header = record
            .get(..RECORD_HEADER_LEN)
            .ok_or(StoreError::Truncated {
                context: "record header",
                need: RECORD_HEADER_LEN,
                have: record.len(),
            })?;
        let magic = header.get(..4).ok_or(StoreError::Truncated {
            context: "record magic",
            need: 4,
            have: header.len(),
        })?;
        if magic != RECORD_MAGIC {
            let mut found = [0u8; 4];
            found.copy_from_slice(magic);
            return Err(StoreError::RecordMagic { index, found });
        }
        let declared_crc = read_u32(header, 44)?;
        let crc_bytes = header.get(..44).ok_or(StoreError::Truncated {
            context: "record header checksum input",
            need: 44,
            have: header.len(),
        })?;
        let computed = crc32(crc_bytes);
        if computed != declared_crc {
            return Err(StoreError::RecordHeaderCrc {
                index,
                declared: declared_crc,
                computed,
            });
        }
        let seq = read_u64(header, 8)?;
        let kind = read_u8(header, 16)?;
        check_zero(header, 17, 1, "record flags")?;
        check_zero(header, 18, 2, "record reserved")?;
        let body_len = read_u32(header, 20)?;
        let real_ns = read_i64(header, 24)?;
        let incarnations = read_u16(header, 32)?;
        check_zero(header, 34, 2, "record reserved")?;
        let body_crc = read_u32(header, 36)?;
        let pad = usize::from(read_u8(header, 40)?);
        check_zero(header, 41, 3, "record reserved")?;
        if kind != RECORD_BLOCK && kind != RECORD_EVT {
            return Err(StoreError::RecordKind { index, found: kind });
        }
        let implied = pad_for(usize::try_from(body_len).map_err(|_| StoreError::LengthOverflow)?)?;
        if pad != implied {
            return Err(StoreError::RecordPadding {
                index,
                declared: u32::try_from(pad).map_err(|_| StoreError::LengthOverflow)?,
                expected: u32::try_from(implied).map_err(|_| StoreError::LengthOverflow)?,
            });
        }
        if u32::from(incarnations) > max_incarnations {
            return Err(StoreError::RecordIncarnations {
                index,
                count: usize::from(incarnations),
                max: usize::try_from(max_incarnations).map_err(|_| StoreError::LengthOverflow)?,
            });
        }
        Ok(RecordHeader {
            seq,
            kind,
            real_ns,
            body_len,
            incarnations,
            body_crc,
        })
    }

    /// Split a checked record into its id table and its payload, verifying the body checksum
    /// and the declared body length against the bytes that are actually there.
    ///
    /// # Errors
    ///
    /// [`StoreError::RecordCrc`] when the body's checksum does not cover it and
    /// [`StoreError::PayloadLength`] when the bytes present are not the declared length.
    pub fn split_body<'a>(
        &self,
        record: &'a [u8],
        index: usize,
    ) -> Result<(Vec<u64>, &'a [u8]), StoreError> {
        let body_len = usize::try_from(self.body_len).map_err(|_| StoreError::LengthOverflow)?;
        let start = RECORD_HEADER_LEN;
        let end = start
            .checked_add(body_len)
            .ok_or(StoreError::LengthOverflow)?;
        let body = record.get(start..end).ok_or(StoreError::PayloadLength {
            index,
            declared: body_len,
            actual: record.len().saturating_sub(start),
        })?;
        let computed = crc32(body);
        if computed != self.body_crc {
            return Err(StoreError::RecordCrc {
                index,
                declared: self.body_crc,
                computed,
            });
        }
        let table_len = self.id_table_len()?;
        let mut incarnations = Vec::with_capacity(usize::from(self.incarnations));
        let mut offset = 0usize;
        while offset < table_len {
            let slice = body.get(offset..).ok_or(StoreError::PayloadLength {
                index,
                declared: body_len,
                actual: offset,
            })?;
            incarnations.push(read_u64(slice, 0)?);
            offset = offset.checked_add(8).ok_or(StoreError::LengthOverflow)?;
        }
        let payload = body.get(table_len..).ok_or(StoreError::PayloadLength {
            index,
            declared: body_len,
            actual: table_len,
        })?;
        Ok((incarnations, payload))
    }
}

/// The facts a sealed segment's footer declares.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Footer {
    /// The header's sequence number.
    pub seq: u64,
    /// How many records precede the footer.
    pub record_count: u64,
    /// The byte offset at which the footer begins (where the records ended).
    pub end_offset: u64,
    /// The header's dictionary generation.
    pub dict_generation: u32,
    /// The first record's real time, or `0` for a segment with no records.
    pub first_real_ns: i64,
    /// The last record's real time, or `0`.
    pub last_real_ns: i64,
}

impl Footer {
    /// Encode this footer as its 64 canonical bytes.
    #[must_use]
    pub fn encode(&self) -> Vec<u8> {
        let mut out = Vec::with_capacity(FOOTER_LEN);
        out.extend_from_slice(&FOOTER_MAGIC);
        out.extend_from_slice(&u32::try_from(FOOTER_LEN).unwrap_or(u32::MAX).to_le_bytes());
        out.extend_from_slice(&self.seq.to_le_bytes());
        out.extend_from_slice(&self.record_count.to_le_bytes());
        out.extend_from_slice(&self.end_offset.to_le_bytes());
        out.extend_from_slice(&self.dict_generation.to_le_bytes());
        out.extend_from_slice(&0u32.to_le_bytes());
        out.extend_from_slice(&self.first_real_ns.to_le_bytes());
        out.extend_from_slice(&self.last_real_ns.to_le_bytes());
        let crc = crc32(&out);
        out.extend_from_slice(&crc.to_le_bytes());
        out.extend_from_slice(&0u32.to_le_bytes());
        out
    }

    /// Check a footer from the last [`FOOTER_LEN`] bytes of a sealed segment.
    ///
    /// # Errors
    ///
    /// [`StoreError::FooterMagic`], [`StoreError::FooterCrc`], [`StoreError::Reserved`],
    /// [`StoreError::FooterCount`], [`StoreError::FooterOffset`],
    /// [`StoreError::FooterIdentity`], [`StoreError::FooterGeneration`] and
    /// [`StoreError::Truncated`].
    pub fn check_bytes(
        footer: &[u8],
        header: &SegmentHeader,
        measured_records: u64,
        end_offset: u64,
    ) -> Result<Footer, StoreError> {
        let bytes = footer.get(..FOOTER_LEN).ok_or(StoreError::Truncated {
            context: "segment footer",
            need: FOOTER_LEN,
            have: footer.len(),
        })?;
        let magic = bytes.get(..4).ok_or(StoreError::Truncated {
            context: "footer magic",
            need: 4,
            have: bytes.len(),
        })?;
        if magic != FOOTER_MAGIC {
            let mut found = [0u8; 4];
            found.copy_from_slice(magic);
            return Err(StoreError::FooterMagic { found });
        }
        let declared_len = read_u32(bytes, 4)?;
        if usize::try_from(declared_len).ok() != Some(FOOTER_LEN) {
            return Err(StoreError::Reserved {
                field: "footer length",
                found: declared_len,
            });
        }
        let seq = read_u64(bytes, 8)?;
        let record_count = read_u64(bytes, 16)?;
        let declared_end = read_u64(bytes, 24)?;
        let dict_generation = read_u32(bytes, 32)?;
        check_zero(bytes, 36, 4, "footer reserved")?;
        let first_real_ns = read_i64(bytes, 40)?;
        let last_real_ns = read_i64(bytes, 48)?;
        let declared_crc = read_u32(bytes, 56)?;
        let crc_bytes = bytes.get(..56).ok_or(StoreError::Truncated {
            context: "footer checksum input",
            need: 56,
            have: bytes.len(),
        })?;
        let computed = crc32(crc_bytes);
        if computed != declared_crc {
            return Err(StoreError::FooterCrc {
                declared: declared_crc,
                computed,
            });
        }
        check_zero(bytes, 60, 4, "footer reserved")?;
        if record_count != measured_records {
            return Err(StoreError::FooterCount {
                declared: record_count,
                measured: measured_records,
            });
        }
        if declared_end != end_offset {
            return Err(StoreError::FooterOffset {
                declared: declared_end,
                measured: end_offset,
            });
        }
        if seq != header.seq {
            return Err(StoreError::FooterIdentity {
                header: header.seq,
                footer: seq,
            });
        }
        if dict_generation != header.dict_generation {
            return Err(StoreError::FooterGeneration {
                header: header.dict_generation,
                footer: dict_generation,
            });
        }
        Ok(Footer {
            seq,
            record_count,
            end_offset: declared_end,
            dict_generation,
            first_real_ns,
            last_real_ns,
        })
    }
}

/// The facts a stored payload's `proto` frame header carries (plan v4 §B.1).
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct FrameFacts {
    /// The frame's declared length (`u32` at byte 0).
    pub len: u32,
    /// The frame type byte (BLOCK in history tiers, EVT in the events store).
    pub frame_type: u8,
    /// The frame flags byte.
    pub flags: u8,
    /// The correlation id (`u32` at byte 8).
    pub corr: u32,
}

/// Check that `payload` is the `proto` frame its corpus requires, and read its header facts.
///
/// [`StoredFrameFields`] owns the fixed-header layout. The store checks the facts its segments
/// own in their established order: the configured payload bound, declared length, exact frame
/// type, exact flags and reserved word. Anything inside the frame (section directory, columns,
/// dictionary) is `proto`'s refusal, checked by its decoder when a consumer reads the payload
/// back. History stores BLOCK frames and the events store stores EVT frames, both with
/// SECTIONED set, so a corpus cannot accidentally serve the other's payload.
///
/// # Errors
///
/// [`StoreError::PayloadTooLarge`], [`StoreError::Truncated`],
/// [`StoreError::PayloadFrameType`], [`StoreError::PayloadFrameLength`],
/// [`StoreError::PayloadFrameFlags`] and [`StoreError::PayloadFrameReserved`].
pub fn check_payload(
    payload: &[u8],
    expected: TierKind,
    max_payload: u32,
) -> Result<FrameFacts, StoreError> {
    let len = u32::try_from(payload.len()).map_err(|_| StoreError::LengthOverflow)?;
    if len > max_payload {
        return Err(StoreError::PayloadTooLarge {
            len: payload.len(),
            max: usize::try_from(max_payload).map_err(|_| StoreError::LengthOverflow)?,
        });
    }
    if payload.len() < FRAME_HEADER_LEN {
        return Err(StoreError::Truncated {
            context: "payload frame header",
            need: FRAME_HEADER_LEN,
            have: payload.len(),
        });
    }
    let header_bytes: &[u8; FRAME_HEADER_LEN] = payload
        .get(..FRAME_HEADER_LEN)
        .and_then(|bytes| bytes.try_into().ok())
        .ok_or(StoreError::Truncated {
            context: "payload frame header",
            need: FRAME_HEADER_LEN,
            have: payload.len(),
        })?;
    let header = StoredFrameFields::parse_header(header_bytes);
    if header.len != len {
        return Err(StoreError::PayloadFrameLength {
            declared: header.len,
            have: payload.len(),
        });
    }
    if header.frame_type != expected.frame_type() {
        return Err(StoreError::PayloadFrameType {
            expected: expected.frame_type(),
            found: header.frame_type,
        });
    }
    if header.flags != FRAME_FLAG_SECTIONED {
        return Err(StoreError::PayloadFrameFlags {
            flags: header.flags,
        });
    }
    if header.reserved != 0 {
        return Err(StoreError::PayloadFrameReserved {
            found: header.reserved,
        });
    }
    Ok(FrameFacts {
        len: header.len,
        frame_type: header.frame_type,
        flags: header.flags,
        corr: header.corr,
    })
}

/// The padding a payload of `payload_len` bytes is followed by, so the next record starts
/// eight-byte aligned.
///
/// # Errors
///
/// [`StoreError::LengthOverflow`] when the arithmetic cannot be represented.
pub fn pad_for(payload_len: usize) -> Result<usize, StoreError> {
    let remainder = payload_len % 8;
    if remainder == 0 {
        return Ok(0);
    }
    8usize
        .checked_sub(remainder)
        .ok_or(StoreError::LengthOverflow)
}

/// The suffix a quarantined file carries. No segment-name parse matches it, so a reader
/// never scans a quarantined file; the tier's own bound still counts and evicts it (HOB-43).
pub const QUARANTINE_SUFFIX: &str = ".corrupt";

/// The largest segment sequence this store mints or recognises in a name (`u32::MAX`).
///
/// One segment per `segment_s >= 1` second makes this 136 years, so a real tier never
/// reaches it. A name whose sequence is above it is not a segment name of this store: the
/// parsers refuse it, so a look-alike such as `hot-0-18446744073709551615.seg` can never
/// raise a tier's water mark (HOB-55). A name at exactly this value is still ours, and the
/// tier's next sequence past it is the typed [`StoreError::LengthOverflow`].
pub const SEQ_NAME_MAX: u64 = 4_294_967_295;

/// A parsed segment file name.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SegmentFileName {
    /// The tier the file belongs to.
    pub tier: String,
    /// The segment's aligned start time in real seconds since the Unix epoch.
    pub start_s: u64,
    /// The tier-local sequence number.
    pub seq: u64,
    /// True while the file is still being appended to (`.seg.open`).
    pub open: bool,
}

impl SegmentFileName {
    /// The name a sealed segment carries.
    #[must_use]
    pub fn sealed_name(tier: &str, start_s: u64, seq: u64) -> String {
        format!("{tier}-{start_s}-{seq}.seg")
    }

    /// The name an open segment carries while it is appended to.
    #[must_use]
    pub fn open_name(tier: &str, start_s: u64, seq: u64) -> String {
        format!("{tier}-{start_s}-{seq}.seg.open")
    }

    /// The name this file carries.
    #[must_use]
    pub fn file_name(&self) -> String {
        if self.open {
            SegmentFileName::open_name(&self.tier, self.start_s, self.seq)
        } else {
            SegmentFileName::sealed_name(&self.tier, self.start_s, self.seq)
        }
    }

    /// The name a damaged file is quarantined under: its own name plus
    /// [`QUARANTINE_SUFFIX`].
    #[must_use]
    pub fn quarantine_file_name(&self) -> String {
        format!("{}{QUARANTINE_SUFFIX}", self.file_name())
    }

    /// The `start_real_ns` a header must carry for this name: the aligned start second in
    /// nanoseconds, exactly the value the tier writes when it creates the segment.
    #[must_use]
    pub fn start_real_ns(&self) -> i64 {
        i64::try_from(self.start_s.saturating_mul(1_000_000_000)).unwrap_or(i64::MAX)
    }

    /// Parse a quarantine name (`<segment name>.corrupt`) back to the segment name it came
    /// from, so a quarantined file has the same `(start_s, seq)` key a live one has.
    ///
    /// # Errors
    ///
    /// [`StoreError::SegmentName`] for a name that does not end in [`QUARANTINE_SUFFIX`] or
    /// whose remainder is not a segment name this store writes.
    pub fn parse_quarantined(name: &str) -> Result<SegmentFileName, StoreError> {
        match name.strip_suffix(QUARANTINE_SUFFIX) {
            Some(stem) => SegmentFileName::parse(stem).map_err(|_| StoreError::SegmentName {
                name: name.to_string(),
            }),
            None => Err(StoreError::SegmentName {
                name: name.to_string(),
            }),
        }
    }

    /// Parse a name, refusing anything this store did not write.
    ///
    /// The tier name is the *rest* of the name, because a tier name may legitimately contain
    /// `-` (plan v4 §A.4 names the focus tiers after their subject). The number fields are
    /// parsed strictly from the right, so a digit-only name cannot be misread as a segment.
    /// A sequence above [`SEQ_NAME_MAX`] is refused like any other name this store does not
    /// write (HOB-55).
    ///
    /// # Errors
    ///
    /// [`StoreError::SegmentName`] for any name this store does not write.
    pub fn parse(name: &str) -> Result<SegmentFileName, StoreError> {
        let (body, open) = match name.strip_suffix(".seg") {
            Some(body) => (body, false),
            None => match name.strip_suffix(".seg.open") {
                Some(body) => (body, true),
                None => {
                    return Err(StoreError::SegmentName {
                        name: name.to_string(),
                    })
                }
            },
        };
        let mut parts = body.rsplitn(3, '-');
        let seq = parts.next();
        let start_s = parts.next();
        let tier = parts.next();
        let (Some(seq), Some(start_s), Some(tier)) = (seq, start_s, tier) else {
            return Err(StoreError::SegmentName {
                name: name.to_string(),
            });
        };
        if tier.is_empty() || tier.ends_with('-') || tier.starts_with('-') {
            // A doubled separator is a name this store never writes: the tier is written
            // verbatim, so `hot--1-2.seg` would have to be a tier called `hot-`.
            return Err(StoreError::SegmentName {
                name: name.to_string(),
            });
        }
        let seq = parse_decimal(seq).ok_or_else(|| StoreError::SegmentName {
            name: name.to_string(),
        })?;
        let start_s = parse_decimal(start_s).ok_or_else(|| StoreError::SegmentName {
            name: name.to_string(),
        })?;
        if seq > SEQ_NAME_MAX {
            // Not a sequence this store ever mints (HOB-55): a look-alike, never ours.
            return Err(StoreError::SegmentName {
                name: name.to_string(),
            });
        }
        Ok(SegmentFileName {
            tier: tier.to_string(),
            start_s,
            seq,
            open,
        })
    }
}

/// Parse an unsigned decimal number without signs, spaces or an empty string.
fn parse_decimal(text: &str) -> Option<u64> {
    if text.is_empty() || !text.bytes().all(|byte| byte.is_ascii_digit()) {
        return None;
    }
    text.parse::<u64>().ok()
}

/// Check that a tier name is one this store may use in a file name.
///
/// # Errors
///
/// [`StoreError::BadName`] for an empty, over-long, separator-carrying or dotted-lead name.
pub fn check_tier_name(name: &str) -> Result<(), StoreError> {
    let refuse = || StoreError::BadName {
        name: name.to_string(),
    };
    if name.is_empty() || name.len() > 32 {
        return Err(refuse());
    }
    if name.starts_with('.') {
        return Err(refuse());
    }
    if !name
        .bytes()
        .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_' || byte == b'.')
    {
        return Err(refuse());
    }
    Ok(())
}

/// Read a `u8` at `offset`.
///
/// # Errors
///
/// [`StoreError::Truncated`] when the byte is not there.
pub fn read_u8(bytes: &[u8], offset: usize) -> Result<u8, StoreError> {
    bytes.get(offset).copied().ok_or(StoreError::Truncated {
        context: "field",
        need: offset.saturating_add(1),
        have: bytes.len(),
    })
}

/// Read a `u16` at `offset`.
///
/// # Errors
///
/// [`StoreError::Truncated`] when the bytes are not there.
pub fn read_u16(bytes: &[u8], offset: usize) -> Result<u16, StoreError> {
    let end = offset.checked_add(2).ok_or(StoreError::LengthOverflow)?;
    let slice = bytes.get(offset..end).ok_or(StoreError::Truncated {
        context: "field",
        need: end,
        have: bytes.len(),
    })?;
    let mut array = [0u8; 2];
    array.copy_from_slice(slice);
    Ok(u16::from_le_bytes(array))
}

/// Read a `u32` at `offset`.
///
/// # Errors
///
/// [`StoreError::Truncated`] when the bytes are not there.
pub fn read_u32(bytes: &[u8], offset: usize) -> Result<u32, StoreError> {
    let end = offset.checked_add(4).ok_or(StoreError::LengthOverflow)?;
    let slice = bytes.get(offset..end).ok_or(StoreError::Truncated {
        context: "field",
        need: end,
        have: bytes.len(),
    })?;
    let mut array = [0u8; 4];
    array.copy_from_slice(slice);
    Ok(u32::from_le_bytes(array))
}

/// Read a `u64` at `offset`.
///
/// # Errors
///
/// [`StoreError::Truncated`] when the bytes are not there.
pub fn read_u64(bytes: &[u8], offset: usize) -> Result<u64, StoreError> {
    let end = offset.checked_add(8).ok_or(StoreError::LengthOverflow)?;
    let slice = bytes.get(offset..end).ok_or(StoreError::Truncated {
        context: "field",
        need: end,
        have: bytes.len(),
    })?;
    let mut array = [0u8; 8];
    array.copy_from_slice(slice);
    Ok(u64::from_le_bytes(array))
}

/// Read an `i64` at `offset`.
///
/// # Errors
///
/// [`StoreError::Truncated`] when the bytes are not there.
pub fn read_i64(bytes: &[u8], offset: usize) -> Result<i64, StoreError> {
    let end = offset.checked_add(8).ok_or(StoreError::LengthOverflow)?;
    let slice = bytes.get(offset..end).ok_or(StoreError::Truncated {
        context: "field",
        need: end,
        have: bytes.len(),
    })?;
    let mut array = [0u8; 8];
    array.copy_from_slice(slice);
    Ok(i64::from_le_bytes(array))
}

/// Check that `len` bytes at `offset` are all zero.
///
/// # Errors
///
/// [`StoreError::Truncated`] when the bytes are not there and [`StoreError::Reserved`] for
/// a non-zero byte.
fn check_zero(
    bytes: &[u8],
    offset: usize,
    len: usize,
    field: &'static str,
) -> Result<(), StoreError> {
    let end = offset.checked_add(len).ok_or(StoreError::LengthOverflow)?;
    let slice = bytes.get(offset..end).ok_or(StoreError::Truncated {
        context: "reserved field",
        need: end,
        have: bytes.len(),
    })?;
    for byte in slice {
        if *byte != 0 {
            return Err(StoreError::Reserved {
                field,
                found: u32::from(*byte),
            });
        }
    }
    Ok(())
}

/// The CRC of a record's bytes as a reader sees them (used by the scan).
///
/// # Errors
///
/// [`StoreError::RegionOutOfBounds`] when the range does not exist.
pub fn record_crc(bytes: &[u8], start: usize, end: usize) -> Result<u32, StoreError> {
    if end > bytes.len() || start > end {
        return Err(StoreError::RegionOutOfBounds(
            crate::error::RegionOutOfBounds {
                need: end,
                have: bytes.len(),
            },
        ));
    }
    let region = bytes.get(start..end).ok_or(StoreError::RegionOutOfBounds(
        crate::error::RegionOutOfBounds {
            need: end,
            have: bytes.len(),
        },
    ))?;
    Ok(crc32(region))
}

/// The CRC of a header's own bytes plus its body, for a caller checking a range.
///
/// # Errors
///
/// [`StoreError::RegionOutOfBounds`] when the range does not exist.
pub fn header_and_body_crc(
    header: &[u8],
    body: &[u8],
    start: usize,
    end: usize,
) -> Result<u32, StoreError> {
    if end > header.len() || start > end {
        return Err(StoreError::RegionOutOfBounds(
            crate::error::RegionOutOfBounds {
                need: end,
                have: header.len(),
            },
        ));
    }
    let prefix = header.get(start..end).ok_or(StoreError::RegionOutOfBounds(
        crate::error::RegionOutOfBounds {
            need: end,
            have: header.len(),
        },
    ))?;
    Ok(crc32_pair(prefix, body))
}

#[cfg(test)]
mod tests {
    use super::{
        check_payload, check_tier_name, pad_for, read_u32, record_crc, Footer, FrameFacts,
        RecordHeader, SegmentFileName, SegmentHeader, TierKind, BLOCK_FRAME_TYPE, EVT_FRAME_TYPE,
        FOOTER_LEN, FRAME_FLAG_SECTIONED, FRAME_HEADER_LEN, HEADER_LEN, MAX_RECORD_PAD,
        PAYLOAD_BLOCK, QUARANTINE_SUFFIX, RECORD_BLOCK, RECORD_EVT, RECORD_HEADER_LEN,
        SEGMENT_MAGIC, SEGMENT_VERSION, SEQ_NAME_MAX,
    };
    use crate::crc::crc32;
    use crate::error::StoreError;

    fn header() -> SegmentHeader {
        SegmentHeader {
            seq: 3,
            epoch: 9,
            dict_generation: 2,
            kind: TierKind::History,
            payload: PAYLOAD_BLOCK,
            start_real_ns: 1_700_000_000_000_000_000,
            max_payload: 1 << 20,
            max_incarnations: 64,
        }
    }

    fn block_frame(payload_len: usize) -> Vec<u8> {
        let mut frame = vec![0u8; payload_len.max(FRAME_HEADER_LEN)];
        let declared = u32::try_from(frame.len()).expect("fits");
        frame[0..4].copy_from_slice(&declared.to_le_bytes());
        frame[4] = BLOCK_FRAME_TYPE;
        frame[5] = 1;
        frame[8..12].copy_from_slice(&77u32.to_le_bytes());
        frame
    }

    fn header_bytes() -> Vec<u8> {
        header().encode().expect("encode")
    }

    #[test]
    fn a_header_round_trips_and_every_field_is_where_the_document_says() {
        let encoded = header_bytes();
        assert_eq!(encoded.len(), HEADER_LEN);
        assert_eq!(&encoded[0..7], b"HOBSEG\x01");
        assert_eq!(SEGMENT_MAGIC, *b"HOBSEG\x01");
        assert_eq!(encoded[7], 0, "the byte after the magic is reserved");
        assert_eq!(encoded[8..10], SEGMENT_VERSION.to_le_bytes());
        assert_eq!(encoded[10..12], 64u16.to_le_bytes());
        assert_eq!(encoded[12..20], 3u64.to_le_bytes());
        assert_eq!(encoded[20..28], 9u64.to_le_bytes());
        assert_eq!(encoded[28..32], 2u32.to_le_bytes());
        assert_eq!(encoded[32], TierKind::History.code());
        assert_eq!(encoded[33], PAYLOAD_BLOCK);
        assert_eq!(encoded[36..44], 1_700_000_000_000_000_000i64.to_le_bytes());
        assert_eq!(encoded[44..48], (1u32 << 20).to_le_bytes());
        assert_eq!(encoded[48..52], 64u32.to_le_bytes());
        assert_eq!(
            encoded[56..60],
            crc32(&encoded[..56]).to_le_bytes(),
            "the checksum covers the 56 bytes before it"
        );
        assert_eq!(SegmentHeader::decode(&encoded), Ok(header()));
    }

    #[test]
    fn a_corrupt_header_names_the_field_that_does_not_hold() {
        let base = header_bytes();
        let mut bad_magic = base.clone();
        bad_magic[0] = b'X';
        assert_eq!(
            SegmentHeader::decode(&bad_magic),
            Err(StoreError::HeaderMagic {
                found: *b"XOBSEG\x01"
            })
        );
        let mut bad_version = base.clone();
        bad_version[8..10].copy_from_slice(&2u16.to_le_bytes());
        assert_eq!(
            SegmentHeader::decode(&bad_version),
            Err(StoreError::HeaderVersion {
                found: 2,
                supported: SEGMENT_VERSION
            })
        );
        let mut bad_len = base.clone();
        bad_len[10..12].copy_from_slice(&32u16.to_le_bytes());
        assert_eq!(
            SegmentHeader::decode(&bad_len),
            Err(StoreError::HeaderLength {
                declared: 32,
                expected: 64
            })
        );
        // A history header that claims an events payload is refused by corpus, not silently
        // accepted: the segment's kind and payload must agree in both directions.
        let mut bad_payload = base.clone();
        bad_payload[33] = 2;
        assert_eq!(
            SegmentHeader::decode(&bad_payload),
            Err(StoreError::TierKindMismatch {
                tier: "history",
                payload: "evt"
            })
        );
        let mut unknown_payload = base.clone();
        unknown_payload[33] = 9;
        assert_eq!(
            SegmentHeader::decode(&unknown_payload),
            Err(StoreError::TierKindMismatch {
                tier: "history",
                payload: "outside-the-vocabulary"
            })
        );
        let mut bad_reserved = base.clone();
        bad_reserved[34] = 1;
        assert_eq!(
            SegmentHeader::decode(&bad_reserved),
            Err(StoreError::Reserved {
                field: "segment header reserved",
                found: 1
            })
        );
        let mut bad_kind = base.clone();
        bad_kind[32] = 9;
        assert_eq!(
            SegmentHeader::decode(&bad_kind),
            Err(StoreError::Reserved {
                field: "segment kind",
                found: 9
            })
        );
        let mut bad_crc = base.clone();
        bad_crc[12] = 4;
        let computed = crc32(&bad_crc[..56]);
        assert_eq!(
            SegmentHeader::decode(&bad_crc),
            Err(StoreError::HeaderCrc {
                declared: u32::from_le_bytes([base[56], base[57], base[58], base[59]]),
                computed
            })
        );
        let mut bad_tail = base.clone();
        bad_tail[60] = 1;
        assert_eq!(
            SegmentHeader::decode(&bad_tail),
            Err(StoreError::Reserved {
                field: "segment header reserved",
                found: 1
            })
        );
        assert_eq!(
            SegmentHeader::decode(&base[..HEADER_LEN - 1]),
            Err(StoreError::Truncated {
                context: "segment header",
                need: HEADER_LEN,
                have: HEADER_LEN - 1
            })
        );
        assert_eq!(SegmentHeader::check_bytes(&base), Ok(header()));
        assert_eq!(
            SegmentHeader::check_bytes(&base[..HEADER_LEN - 1]),
            Err(StoreError::HeaderLength {
                declared: u32::try_from(HEADER_LEN - 1).expect("fits"),
                expected: 64
            })
        );
    }

    #[test]
    fn a_header_refuses_to_declare_a_payload_kind_this_version_does_not_write() {
        let mut declared = header();
        declared.payload = 7;
        assert_eq!(declared.encode(), Err(StoreError::PayloadKind { found: 7 }));
    }

    #[test]
    fn padding_is_what_makes_the_next_record_aligned() {
        assert_eq!(pad_for(0), Ok(0));
        assert_eq!(pad_for(8), Ok(0));
        assert_eq!(pad_for(9), Ok(7));
        assert_eq!(pad_for(15), Ok(1));
        assert_eq!(pad_for(16), Ok(0));
        for len in 0..64usize {
            let pad = pad_for(len).expect("fits");
            assert!(pad <= MAX_RECORD_PAD, "pad {pad} exceeds the maximum");
            assert_eq!((len + pad) % 8, 0, "len {len} + pad {pad} is not aligned");
        }
    }

    #[test]
    fn a_record_round_trips_and_a_reader_checks_it_by_position() {
        let payload = block_frame(20);
        let ids = [11u64, 12];
        let encoded = RecordHeader::encode_record(
            0,
            RECORD_BLOCK,
            1_700_000_001_000_000_000,
            &ids,
            &payload,
            1 << 20,
            64,
        )
        .expect("encode");
        assert_eq!(&encoded[0..4], b"HOBR");
        // body = 2 ids (16) + payload (20) = 36, pad 4 -> 48 + 36 + 4
        assert_eq!(encoded.len(), RECORD_HEADER_LEN + 36 + 4);
        assert_eq!(
            &encoded[RECORD_HEADER_LEN..RECORD_HEADER_LEN + 8],
            &11u64.to_le_bytes()
        );
        assert_eq!(
            &encoded[RECORD_HEADER_LEN + 8..RECORD_HEADER_LEN + 16],
            &12u64.to_le_bytes()
        );
        assert_eq!(
            &encoded[RECORD_HEADER_LEN + 16..RECORD_HEADER_LEN + 36],
            &payload[..]
        );
        let header = RecordHeader::check_bytes(&encoded, 0, 64).expect("check");
        assert_eq!(header.seq, 0);
        assert_eq!(header.kind, RECORD_BLOCK);
        assert_eq!(header.body_len, 36);
        assert_eq!(header.incarnations, 2);
        assert_eq!(header.id_table_len(), Ok(16));
        assert_eq!(header.payload_len(), Ok(20));
        assert_eq!(header.record_len(), Ok(RECORD_HEADER_LEN + 36 + 4));
        let (table, split_payload) = header.split_body(&encoded, 0).expect("split");
        assert_eq!(table, ids.to_vec());
        assert_eq!(split_payload, &payload[..]);
        assert_eq!(
            RecordHeader::encode_record(0, RECORD_BLOCK, 1, &ids, &payload, 8, 64),
            Err(StoreError::PayloadTooLarge { len: 20, max: 8 })
        );
        assert_eq!(
            RecordHeader::encode_record(0, RECORD_BLOCK, 1, &ids[..1], &payload, 1024, 0),
            Err(StoreError::RecordIncarnations {
                index: 0,
                count: 1,
                max: 0
            })
        );
    }

    #[test]
    fn a_reader_refuses_every_record_field_that_does_not_hold() {
        let payload = block_frame(12);
        let encoded = RecordHeader::encode_record(4, RECORD_BLOCK, 1, &[7], &payload, 1024, 64)
            .expect("encode");
        bad_cases(&encoded);
    }

    /// Set bytes at `offset` and recompute the header checksum over them, so a test can
    /// reach a field refusal that a plain `1 -> 2` patch would hide behind the CRC.
    fn patch(encoded: &[u8], offset: usize, bytes: &[u8]) -> Vec<u8> {
        let mut patched = encoded.to_vec();
        for (index, byte) in bytes.iter().enumerate() {
            if let Some(slot) = patched.get_mut(offset + index) {
                *slot = *byte;
            }
        }
        let crc = crc32(&patched[..44]);
        patched[44..48].copy_from_slice(&crc.to_le_bytes());
        patched
    }

    fn bad_cases(encoded: &[u8]) {
        let mut bad = encoded.to_vec();
        bad[0] = b'X';
        assert_eq!(
            RecordHeader::check_bytes(&bad, 4, 64),
            Err(StoreError::RecordMagic {
                index: 4,
                found: *b"XOBR"
            })
        );
        // A kind outside the closed vocabulary is refused by the record reader; which of the
        // two defined kinds a segment may hold is its header's corpus, checked by the
        // segment reader that knows it (see `an_events_segment_refuses_a_block_record`).
        let bad_kind = patch(encoded, 16, &[9]);
        assert_eq!(
            RecordHeader::check_bytes(&bad_kind, 1, 64),
            Err(StoreError::RecordKind { index: 1, found: 9 })
        );
        let event_kind = patch(encoded, 16, &[RECORD_EVT]);
        assert_eq!(
            RecordHeader::check_bytes(&event_kind, 1, 64).map(|header| header.kind),
            Ok(RECORD_EVT),
            "the record header itself admits both defined kinds"
        );
        let bad_flags = patch(encoded, 17, &[1]);
        assert_eq!(
            RecordHeader::check_bytes(&bad_flags, 0, 64),
            Err(StoreError::Reserved {
                field: "record flags",
                found: 1
            })
        );
        let bad_pad = patch(encoded, 40, &[5]);
        assert_eq!(
            RecordHeader::check_bytes(&bad_pad, 0, 64),
            Err(StoreError::RecordPadding {
                index: 0,
                declared: 5,
                expected: 4
            })
        );
        let bad_inc = patch(encoded, 32, &65u16.to_le_bytes());
        assert_eq!(
            RecordHeader::check_bytes(&bad_inc, 0, 64),
            Err(StoreError::RecordIncarnations {
                index: 0,
                count: 65,
                max: 64
            })
        );
        let mut bad_crc = encoded.to_vec();
        bad_crc[20] = 13;
        let computed = crc32(&bad_crc[..44]);
        assert_eq!(
            RecordHeader::check_bytes(&bad_crc, 0, 64),
            Err(StoreError::RecordHeaderCrc {
                index: 0,
                declared: u32::from_le_bytes([encoded[44], encoded[45], encoded[46], encoded[47]]),
                computed
            })
        );
        let bad_reserved = patch(encoded, 18, &[1]);
        assert_eq!(
            RecordHeader::check_bytes(&bad_reserved, 0, 64),
            Err(StoreError::Reserved {
                field: "record reserved",
                found: 1
            })
        );
        assert_eq!(
            RecordHeader::check_bytes(&encoded[..RECORD_HEADER_LEN - 1], 0, 64),
            Err(StoreError::Truncated {
                context: "record header",
                need: RECORD_HEADER_LEN,
                have: RECORD_HEADER_LEN - 1
            })
        );
    }

    #[test]
    fn a_body_is_checked_against_its_own_checksum_and_length() {
        let payload = block_frame(12);
        let encoded = RecordHeader::encode_record(0, RECORD_BLOCK, 1, &[3], &payload, 1024, 64)
            .expect("encode");
        let header = RecordHeader::check_bytes(&encoded, 0, 64).expect("check");
        let mut corrupt = encoded.clone();
        // Flip a payload byte without touching the header: the body checksum must catch it.
        corrupt[RECORD_HEADER_LEN + 8] ^= 0x01;
        assert_eq!(
            header.split_body(&corrupt, 0),
            Err(StoreError::RecordCrc {
                index: 0,
                declared: header.body_crc,
                computed: crc32(
                    &corrupt[RECORD_HEADER_LEN..RECORD_HEADER_LEN + header.body_len as usize]
                )
            })
        );
        let short = &encoded[..RECORD_HEADER_LEN + 4];
        assert_eq!(
            header.split_body(short, 0),
            Err(StoreError::PayloadLength {
                index: 0,
                declared: 20,
                actual: 4
            })
        );
        let mut padded = encoded.clone();
        padded.truncate(RECORD_HEADER_LEN + 20);
        assert_eq!(
            header
                .split_body(&padded, 0)
                .map(|(_, payload)| payload.len()),
            Ok(12)
        );
        // A header that declares more ids than its body can hold is a length refusal.
        let mut impossible = header;
        impossible.incarnations = 3;
        assert_eq!(
            impossible.payload_len(),
            Err(StoreError::PayloadLength {
                index: 0,
                declared: 20,
                actual: 24
            })
        );
    }

    #[test]
    fn a_footer_round_trips_and_must_agree_with_what_precedes_it() {
        let header = header();
        let footer = Footer {
            seq: header.seq,
            record_count: 5,
            end_offset: 1024,
            dict_generation: header.dict_generation,
            first_real_ns: 10,
            last_real_ns: 20,
        };
        let bytes = footer.encode();
        assert_eq!(bytes.len(), FOOTER_LEN);
        assert_eq!(&bytes[0..4], b"HOBF");
        assert_eq!(
            &bytes[4..8],
            &64u32.to_le_bytes(),
            "the footer declares its own length"
        );
        assert_eq!(Footer::check_bytes(&bytes, &header, 5, 1024), Ok(footer));
        assert_eq!(
            Footer::check_bytes(&bytes, &header, 4, 1024),
            Err(StoreError::FooterCount {
                declared: 5,
                measured: 4
            })
        );
        assert_eq!(
            Footer::check_bytes(&bytes, &header, 5, 1000),
            Err(StoreError::FooterOffset {
                declared: 1024,
                measured: 1000
            })
        );
        let mut other_seq = header;
        other_seq.seq = 8;
        assert_eq!(
            Footer::check_bytes(&bytes, &other_seq, 5, 1024),
            Err(StoreError::FooterIdentity {
                header: 8,
                footer: 3
            })
        );
        let mut other_generation = header;
        other_generation.dict_generation = 5;
        assert_eq!(
            Footer::check_bytes(&bytes, &other_generation, 5, 1024),
            Err(StoreError::FooterGeneration {
                header: 5,
                footer: 2
            })
        );
        let mut bad_magic = bytes.clone();
        bad_magic[0] = b'X';
        assert_eq!(
            Footer::check_bytes(&bad_magic, &header, 5, 1024),
            Err(StoreError::FooterMagic { found: *b"XOBF" })
        );
        let mut bad_crc = bytes.clone();
        bad_crc[8] = 9;
        let computed = crc32(&bad_crc[..56]);
        assert_eq!(
            Footer::check_bytes(&bad_crc, &header, 5, 1024),
            Err(StoreError::FooterCrc {
                declared: u32::from_le_bytes([bytes[56], bytes[57], bytes[58], bytes[59]]),
                computed
            })
        );
        let mut bad_len = bytes.clone();
        bad_len[4..8].copy_from_slice(&56u32.to_le_bytes());
        assert_eq!(
            Footer::check_bytes(&bad_len, &header, 5, 1024),
            Err(StoreError::Reserved {
                field: "footer length",
                found: 56
            })
        );
        assert_eq!(
            Footer::check_bytes(&bytes[..FOOTER_LEN - 1], &header, 5, 1024),
            Err(StoreError::Truncated {
                context: "segment footer",
                need: FOOTER_LEN,
                have: FOOTER_LEN - 1
            })
        );
    }

    #[test]
    fn a_header_may_only_declare_the_payload_of_its_own_corpus() {
        let history = header();
        assert_eq!(history.encode().expect("encode")[33], PAYLOAD_BLOCK);
        let mut events = history;
        events.kind = TierKind::Events;
        events.payload = crate::format::PAYLOAD_EVT;
        let bytes = events.encode().expect("an events header is canonical too");
        assert_eq!(bytes[33], crate::format::PAYLOAD_EVT);
        assert_eq!(SegmentHeader::decode(&bytes), Ok(events));
        let mismatched = SegmentHeader {
            payload: PAYLOAD_BLOCK,
            ..events
        };
        assert_eq!(
            mismatched.encode(),
            Err(StoreError::PayloadKind {
                found: PAYLOAD_BLOCK
            })
        );
        assert_eq!(
            TierKind::from_payload_kind(crate::format::PAYLOAD_EVT),
            Ok(TierKind::Events)
        );
        assert_eq!(TierKind::Events.frame_type(), crate::format::EVT_FRAME_TYPE);
        assert_eq!(TierKind::Events.record_kind(), crate::format::RECORD_EVT);
    }

    #[test]
    fn a_payload_must_carry_a_block_frame_header() {
        let payload = block_frame(32);
        let facts = check_payload(&payload, TierKind::History, 1024).expect("a block");
        assert_eq!(
            facts,
            FrameFacts {
                len: 32,
                frame_type: BLOCK_FRAME_TYPE,
                flags: FRAME_FLAG_SECTIONED,
                corr: 77
            }
        );
        let mut event = payload.clone();
        event[4] = EVT_FRAME_TYPE;
        let facts = check_payload(&event, TierKind::Events, 1024)
            .expect("the stored events corpus carries EVT with SECTIONED");
        assert_eq!(facts.frame_type, EVT_FRAME_TYPE);
        assert_eq!(facts.flags, FRAME_FLAG_SECTIONED);
        let mut not_a_block = payload.clone();
        not_a_block[4] = 1;
        assert_eq!(
            check_payload(&not_a_block, TierKind::History, 1024),
            Err(StoreError::PayloadFrameType {
                expected: BLOCK_FRAME_TYPE,
                found: 1,
            })
        );
        let mut wrong_len = payload.clone();
        wrong_len[0..4].copy_from_slice(&64u32.to_le_bytes());
        assert_eq!(
            check_payload(&wrong_len, TierKind::History, 1024),
            Err(StoreError::PayloadFrameLength {
                declared: 64,
                have: 32
            })
        );
        let mut length_before_type = wrong_len.clone();
        length_before_type[4] = 0;
        assert_eq!(
            check_payload(&length_before_type, TierKind::History, 1024),
            Err(StoreError::PayloadFrameLength {
                declared: 64,
                have: 32
            }),
            "the declared length is the first refusal"
        );
        let mut length_before_flags = wrong_len.clone();
        length_before_flags[5] = 0b11;
        assert_eq!(
            check_payload(&length_before_flags, TierKind::History, 1024),
            Err(StoreError::PayloadFrameLength {
                declared: 64,
                have: 32
            }),
            "the declared length precedes unknown flags"
        );
        let mut length_before_reserved = wrong_len;
        length_before_reserved[6] = 1;
        assert_eq!(
            check_payload(&length_before_reserved, TierKind::History, 1024),
            Err(StoreError::PayloadFrameLength {
                declared: 64,
                have: 32
            }),
            "the declared length precedes the reserved word"
        );
        let mut type_before_flags_and_reserved = payload.clone();
        type_before_flags_and_reserved[4] = 1;
        type_before_flags_and_reserved[5] = 0;
        type_before_flags_and_reserved[6] = 1;
        assert_eq!(
            check_payload(&type_before_flags_and_reserved, TierKind::History, 1024),
            Err(StoreError::PayloadFrameType {
                expected: BLOCK_FRAME_TYPE,
                found: 1,
            }),
            "the corpus frame type precedes flags and reserved"
        );
        let mut flags_before_reserved = payload.clone();
        flags_before_reserved[5] = 0;
        flags_before_reserved[6] = 1;
        assert_eq!(
            check_payload(&flags_before_reserved, TierKind::History, 1024),
            Err(StoreError::PayloadFrameFlags { flags: 0 }),
            "the exact flags precede the reserved word"
        );
        let mut extra_flag = payload.clone();
        extra_flag[5] = 0b0000_0011;
        assert_eq!(
            check_payload(&extra_flag, TierKind::History, 1024),
            Err(StoreError::PayloadFrameFlags { flags: 0b11 })
        );
        let mut no_sectioned = payload.clone();
        no_sectioned[5] = 0;
        assert_eq!(
            check_payload(&no_sectioned, TierKind::History, 1024),
            Err(StoreError::PayloadFrameFlags { flags: 0 })
        );
        let mut reserved = payload.clone();
        reserved[6] = 1;
        assert_eq!(
            check_payload(&reserved, TierKind::History, 1024),
            Err(StoreError::PayloadFrameReserved { found: 1 })
        );
        assert_eq!(
            check_payload(&payload, TierKind::History, 16),
            Err(StoreError::PayloadTooLarge { len: 32, max: 16 })
        );
        assert_eq!(
            check_payload(&payload[..8], TierKind::History, 1024),
            Err(StoreError::Truncated {
                context: "payload frame header",
                need: FRAME_HEADER_LEN,
                have: 8
            })
        );
    }

    #[test]
    fn a_segment_name_round_trips_and_refuses_foreign_names() {
        let name = SegmentFileName {
            tier: "focus-web".into(),
            start_s: 1_700_000_000,
            seq: 12,
            open: false,
        };
        assert_eq!(name.file_name(), "focus-web-1700000000-12.seg");
        assert_eq!(SegmentFileName::parse(&name.file_name()), Ok(name.clone()));
        let open = SegmentFileName { open: true, ..name };
        assert_eq!(open.file_name(), "focus-web-1700000000-12.seg.open");
        assert_eq!(SegmentFileName::parse(&open.file_name()), Ok(open.clone()));
        for foreign in [
            "notes.txt",
            "hot-1.seg",
            "hot--1-2.seg",
            "hot-1-2.seg.tmp",
            "-1-2.seg",
            "hot-1-x.seg",
            "hot-1-2.seg.open.open",
        ] {
            assert_eq!(
                SegmentFileName::parse(foreign),
                Err(StoreError::SegmentName {
                    name: foreign.to_string()
                }),
                "{foreign} should be refused"
            );
        }
        assert_eq!(
            SegmentFileName::sealed_name("hot", 1, 2),
            "hot-1-2.seg".to_string()
        );
        assert_eq!(
            SegmentFileName::open_name("hot", 1, 2),
            "hot-1-2.seg.open".to_string()
        );
    }

    #[test]
    fn a_sequence_above_the_name_bound_is_not_a_segment_name() {
        // HOB-55: the store never mints a sequence above `SEQ_NAME_MAX`, so such a name is a
        // look-alike in every spelling and every parser agrees; the bound itself is ours.
        assert_eq!(SEQ_NAME_MAX, u64::from(u32::MAX));
        for ours in [
            format!("hot-0-{SEQ_NAME_MAX}.seg"),
            format!("hot-0-{SEQ_NAME_MAX}.seg.open"),
        ] {
            let parsed = SegmentFileName::parse(&ours).expect("the bound is ours");
            assert_eq!(parsed.seq, SEQ_NAME_MAX);
            assert_eq!(parsed.file_name(), ours);
        }
        let quarantined = format!("hot-0-{SEQ_NAME_MAX}.seg.corrupt");
        assert_eq!(
            SegmentFileName::parse_quarantined(&quarantined).map(|parsed| parsed.seq),
            Ok(SEQ_NAME_MAX)
        );
        let above = SEQ_NAME_MAX + 1;
        for name in [
            format!("hot-0-{above}.seg"),
            format!("hot-0-{above}.seg.open"),
            format!("hot-0-{}.seg", u64::MAX),
            format!("hot-0-{}.seg.open", u64::MAX),
        ] {
            let refused = SegmentFileName::parse(&name).is_err();
            assert!(refused, "{name} is a look-alike");
            let corrupt = format!("{name}{QUARANTINE_SUFFIX}");
            let refused = SegmentFileName::parse_quarantined(&corrupt).is_err();
            assert!(refused, "{corrupt} is a look-alike");
        }
    }

    #[test]
    fn a_quarantine_name_parses_back_to_the_segment_key_and_nothing_else_does() {
        // HOB-43: the quarantined file carries the (start_s, seq) of the segment it was, so
        // the tier can order and expire it exactly like a live one.
        let sealed = SegmentFileName::parse("focus-web-1700000000-12.seg").expect("sealed");
        let open = SegmentFileName::parse("focus-web-1700000000-12.seg.open").expect("open");
        assert_eq!(
            sealed.quarantine_file_name(),
            "focus-web-1700000000-12.seg.corrupt"
        );
        assert_eq!(
            open.quarantine_file_name(),
            "focus-web-1700000000-12.seg.open.corrupt"
        );
        assert_eq!(
            SegmentFileName::parse_quarantined(&sealed.quarantine_file_name()),
            Ok(sealed.clone())
        );
        assert_eq!(
            SegmentFileName::parse_quarantined(&open.quarantine_file_name()),
            Ok(open)
        );
        for foreign in [
            "hot-1-2.seg",
            "hot-1-2.seg.open",
            "hot-x-2.seg.corrupt",
            "hot-1-2.corrupt",
            "hot-1-2.seg.corrupt.corrupt",
            "hot-1-2.seg.tmp.corrupt",
            ".corrupt",
            "notes.corrupt",
        ] {
            assert_eq!(
                SegmentFileName::parse_quarantined(foreign),
                Err(StoreError::SegmentName {
                    name: foreign.to_string()
                }),
                "{foreign} is not a quarantine name this store writes"
            );
        }
        assert_eq!(sealed.start_real_ns(), 1_700_000_000_000_000_000);
        let huge = SegmentFileName {
            start_s: u64::MAX,
            ..sealed
        };
        assert_eq!(huge.start_real_ns(), i64::MAX, "saturates, never wraps");
    }

    #[test]
    fn a_tier_name_is_bounded_and_never_a_path() {
        assert_eq!(check_tier_name("hot"), Ok(()));
        assert_eq!(check_tier_name("focus-web.1"), Ok(()));
        assert_eq!(check_tier_name("a-b_c.d-e"), Ok(()));
        for bad in ["", ".hidden", "../hot", "hot/1", "hot 1"] {
            let expected = check_tier_name(bad);
            if bad.len() <= 32 {
                assert_eq!(
                    expected,
                    Err(StoreError::BadName {
                        name: bad.to_string()
                    }),
                    "{bad} should be refused"
                );
            }
        }
        assert_eq!(
            check_tier_name(&"a".repeat(33)),
            Err(StoreError::BadName {
                name: "a".repeat(33)
            })
        );
    }

    #[test]
    fn a_tier_kind_has_two_codes_and_a_display_name() {
        assert_eq!(TierKind::History.code(), 1);
        assert_eq!(TierKind::Events.code(), 2);
        assert_eq!(TierKind::from_code(1), Ok(TierKind::History));
        assert_eq!(TierKind::from_code(2), Ok(TierKind::Events));
        assert_eq!(
            TierKind::from_code(0),
            Err(StoreError::Reserved {
                field: "segment kind",
                found: 0
            })
        );
        assert_eq!(TierKind::History.name(), "history");
        assert_eq!(TierKind::Events.to_string(), "events");
    }

    #[test]
    fn field_readers_refuse_a_missing_field() {
        let bytes = [1u8, 2, 3, 4];
        assert_eq!(super::read_u8(&bytes, 0), Ok(1));
        assert_eq!(read_u32(&bytes, 0), Ok(0x0403_0201));
        assert_eq!(
            super::read_u16(&bytes, 3).map_err(|error| error.kind()),
            Err("truncated")
        );
        assert_eq!(
            super::read_u64(&bytes, 0).map(|_| ()),
            Err(StoreError::Truncated {
                context: "field",
                need: 8,
                have: 4
            })
        );
        assert_eq!(
            super::read_i64(&bytes, 0).map(|_| ()),
            Err(StoreError::Truncated {
                context: "field",
                need: 8,
                have: 4
            })
        );
        assert_eq!(
            read_u32(&bytes, usize::MAX),
            Err(StoreError::LengthOverflow)
        );
    }

    #[test]
    fn checksum_helpers_refuse_a_range_past_the_buffer() {
        let bytes = [0u8; 8];
        assert_eq!(record_crc(&bytes, 0, 8), Ok(crc32(&bytes)));
        assert_eq!(
            record_crc(&bytes, 4, 9),
            Err(StoreError::RegionOutOfBounds(
                crate::error::RegionOutOfBounds { need: 9, have: 8 }
            ))
        );
        assert_eq!(
            super::header_and_body_crc(&bytes[..4], &bytes[4..], 0, 5),
            Err(StoreError::RegionOutOfBounds(
                crate::error::RegionOutOfBounds { need: 5, have: 4 }
            ))
        );
        assert_eq!(
            super::header_and_body_crc(&bytes[..4], &bytes[4..], 0, 4),
            Ok(crc32(&bytes))
        );
    }
}
