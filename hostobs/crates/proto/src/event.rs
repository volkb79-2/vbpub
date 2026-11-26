//! The stored event/1 section body and its EVT frame wrapper.
//!
//! This codec is separate from the socket frame parser: socket EVT frames keep flags clear,
//! while the events corpus stores one EVT frame with SECTIONED set and one required kind-7
//! section. Kind 7 is therefore not added to the BLOCK section vocabulary.

use crate::error::{ProtoError, MAX_FRAME_BYTES};
use crate::frame::{FrameType, FLAG_SECTIONED, FRAME_HEADER_LEN};
use crate::write::Writer;

/// The event/1 section kind, local to stored EVT frames.
pub const EVENT_SECTION_KIND: u16 = 7;
/// The event/1 section layout version.
pub const EVENT_SECTION_VERSION: u8 = 1;
/// The event/1 directory flags; the section is required.
pub const EVENT_SECTION_REQUIRED_FLAGS: u8 = 1;

const EVENT_FIXED_LEN: usize = 48;
const EVENT_FRAME_FIXED_LEN: usize = FRAME_HEADER_LEN + 4 + 8;
const EVENT_MAX_CONTEXT: u8 = 5;
const EVENT_MAX_ATTRIBUTES: u8 = 8;
const EVENT_MAX_TEXT: usize = 64;
const EVENT_MAX_CONTEXT_TEXT: usize = 128;
const EVENT_MAX_ATTRIBUTE_VALUE: usize = 256;
const EVENT_MAX_COMM: usize = 15;

/// One event/1 context entry. Keys use the closed codes 0 through 4.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EventContext {
    /// The context key code: run, lane, candidate, txn, or session.
    pub key: u8,
    /// The context value.
    pub value: String,
}

/// One event/1 attribute entry.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EventAttribute {
    /// The name-grammar attribute key.
    pub key: String,
    /// The attribute value.
    pub value: String,
}

/// The decoded event/1 body.
///
/// Raw codes and sentinel integers are retained so the structure mirrors the version-1
/// bytes exactly: kind is 1..=3, outcome is 0..=7, zero span means point, and zero peer
/// identifiers mean null. Context and attribute vectors must already be in canonical order.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EventPayload {
    /// The server-stamped monotonic time in nanoseconds.
    pub t_mono_ns: i64,
    /// The span identifier, or zero for a point.
    pub span: u64,
    /// The peer cgroup device number, or zero with peer_inode for null.
    pub peer_dev: u64,
    /// The peer cgroup inode number, or zero with peer_dev for null.
    pub peer_inode: u64,
    /// The kernel-reported peer uid.
    pub uid: u32,
    /// The unverified peer pid label, or zero for null.
    pub peer_pid: u32,
    /// The event kind code: 1 begin, 2 end, or 3 point.
    pub kind: u8,
    /// The outcome code, zero except for end events.
    pub outcome: u8,
    /// Whether the end was inferred.
    pub inferred: bool,
    /// Context entries, in strictly ascending key-code order.
    pub ctx: Vec<EventContext>,
    /// Attribute entries, in strictly ascending UTF-8 byte order.
    pub attrs: Vec<EventAttribute>,
    /// The event name, in the name grammar.
    pub name: String,
    /// The client key.
    pub key: String,
    /// The event code, or null.
    pub code: Option<String>,
    /// The unverified peer command label, or null.
    pub comm: Option<String>,
}

#[derive(Clone, Copy)]
struct ByteRange {
    start: usize,
    end: usize,
}

#[derive(Clone, Copy)]
struct EventFixed {
    t_mono_ns: i64,
    span: u64,
    peer_dev: u64,
    peer_inode: u64,
    uid: u32,
    peer_pid: u32,
    kind: u8,
    outcome: u8,
    flags: u8,
    n_ctx: u8,
    n_attrs: u8,
    reserved_byte: u8,
    reserved_word: u16,
}

struct BodyLayout {
    fixed: EventFixed,
    strings: [ByteRange; 4],
    ctx_start: usize,
    attrs_start: usize,
    content_end: usize,
}

fn field_bound(field: &'static str) -> ProtoError {
    ProtoError::EventFieldBound { field }
}

fn section_length(declared: usize, expected: usize) -> ProtoError {
    ProtoError::SectionLength {
        kind: EVENT_SECTION_KIND,
        declared,
        expected,
    }
}

fn fixed_array<const N: usize>(body: &[u8], offset: usize) -> Result<[u8; N], ProtoError> {
    let end = offset.checked_add(N).ok_or(ProtoError::LengthOverflow)?;
    let bytes = body.get(offset..end).ok_or(ProtoError::Truncated {
        context: "event body",
        need: end,
        have: body.len(),
    })?;
    let mut result = [0; N];
    result.copy_from_slice(bytes);
    Ok(result)
}

fn take_range(body: &[u8], offset: &mut usize, len: usize) -> Result<ByteRange, ProtoError> {
    let end = offset.checked_add(len).ok_or(ProtoError::LengthOverflow)?;
    if end > body.len() {
        return Err(section_length(body.len(), end));
    }
    let range = ByteRange {
        start: *offset,
        end,
    };
    *offset = end;
    Ok(range)
}

fn read_u8(body: &[u8], offset: &mut usize) -> Result<u8, ProtoError> {
    let range = take_range(body, offset, 1)?;
    body.get(range.start)
        .copied()
        .ok_or_else(|| section_length(body.len(), range.end))
}

fn read_u16(body: &[u8], offset: &mut usize) -> Result<u16, ProtoError> {
    let range = take_range(body, offset, 2)?;
    let bytes = body
        .get(range.start..range.end)
        .ok_or_else(|| section_length(body.len(), range.end))?;
    Ok(u16::from_le_bytes([bytes[0], bytes[1]]))
}

fn read_len_range(body: &[u8], offset: &mut usize) -> Result<ByteRange, ProtoError> {
    let len = usize::from(read_u16(body, offset)?);
    take_range(body, offset, len)
}

fn parse_layout(body: &[u8]) -> Result<BodyLayout, ProtoError> {
    if body.len() < EVENT_FIXED_LEN {
        return Err(ProtoError::Truncated {
            context: "event body",
            need: EVENT_FIXED_LEN,
            have: body.len(),
        });
    }

    let fixed = EventFixed {
        t_mono_ns: i64::from_le_bytes(fixed_array(body, 0)?),
        span: u64::from_le_bytes(fixed_array(body, 8)?),
        peer_dev: u64::from_le_bytes(fixed_array(body, 16)?),
        peer_inode: u64::from_le_bytes(fixed_array(body, 24)?),
        uid: u32::from_le_bytes(fixed_array(body, 32)?),
        peer_pid: u32::from_le_bytes(fixed_array(body, 36)?),
        kind: body[40],
        outcome: body[41],
        flags: body[42],
        n_ctx: body[43],
        n_attrs: body[44],
        reserved_byte: body[45],
        reserved_word: u16::from_le_bytes(fixed_array(body, 46)?),
    };

    let mut offset = EVENT_FIXED_LEN;
    let mut strings = [ByteRange { start: 0, end: 0 }; 4];
    for range in &mut strings {
        *range = read_len_range(body, &mut offset)?;
    }

    let ctx_start = offset;
    for _ in 0..fixed.n_ctx {
        let _key = read_u8(body, &mut offset)?;
        let _value = read_len_range(body, &mut offset)?;
    }

    let attrs_start = offset;
    for _ in 0..fixed.n_attrs {
        let _key = read_len_range(body, &mut offset)?;
        let _value = read_len_range(body, &mut offset)?;
    }

    let content_end = offset;
    let aligned = content_end
        .checked_add(7)
        .ok_or(ProtoError::LengthOverflow)?
        & !7;
    if body.len() != aligned {
        return Err(section_length(body.len(), aligned));
    }

    Ok(BodyLayout {
        fixed,
        strings,
        ctx_start,
        attrs_start,
        content_end,
    })
}

fn validate_reserved(body: &[u8], layout: &BodyLayout) -> Result<(), ProtoError> {
    if layout.fixed.flags & !1 != 0 {
        return Err(ProtoError::NonZeroReserved(u32::from(
            layout.fixed.flags & !1,
        )));
    }
    if layout.fixed.reserved_byte != 0 {
        return Err(ProtoError::NonZeroReserved(u32::from(
            layout.fixed.reserved_byte,
        )));
    }
    if layout.fixed.reserved_word != 0 {
        return Err(ProtoError::NonZeroReserved(u32::from(
            layout.fixed.reserved_word,
        )));
    }
    let padding = body
        .get(layout.content_end..)
        .ok_or_else(|| section_length(body.len(), layout.content_end))?;
    for byte in padding {
        if *byte != 0 {
            return Err(ProtoError::NonZeroReserved(u32::from(*byte)));
        }
    }
    Ok(())
}

fn validate_codes(body: &[u8], layout: &BodyLayout) -> Result<(), ProtoError> {
    if !(1..=3).contains(&layout.fixed.kind) {
        return Err(ProtoError::EventUnknownCode {
            field: "kind",
            code: layout.fixed.kind,
        });
    }
    if layout.fixed.outcome > 7 {
        return Err(ProtoError::EventUnknownCode {
            field: "outcome",
            code: layout.fixed.outcome,
        });
    }
    if (layout.fixed.kind == 2) != (layout.fixed.outcome != 0) {
        return Err(ProtoError::EventUnknownCode {
            field: "outcome/kind",
            code: layout.fixed.outcome,
        });
    }

    let mut offset = layout.ctx_start;
    for _ in 0..layout.fixed.n_ctx {
        let key = read_u8(body, &mut offset)?;
        if key > 4 {
            return Err(ProtoError::EventUnknownCode {
                field: "ctx",
                code: key,
            });
        }
        let _value = read_len_range(body, &mut offset)?;
    }
    Ok(())
}

fn validate_bounds(body: &[u8], layout: &BodyLayout) -> Result<(), ProtoError> {
    let fixed = layout.fixed;
    if fixed.t_mono_ns < 0 {
        return Err(field_bound("t_mono_ns"));
    }
    if (fixed.kind == 3) != (fixed.span == 0) {
        return Err(field_bound("span"));
    }
    if (fixed.peer_dev == 0) != (fixed.peer_inode == 0) {
        return Err(field_bound("peer"));
    }
    if fixed.n_ctx > EVENT_MAX_CONTEXT {
        return Err(field_bound("n_ctx"));
    }
    if fixed.n_attrs > EVENT_MAX_ATTRIBUTES {
        return Err(field_bound("n_attrs"));
    }

    let string_bounds = [
        (1, EVENT_MAX_TEXT, "name"),
        (1, EVENT_MAX_TEXT, "key"),
        (0, EVENT_MAX_TEXT, "code"),
        (0, EVENT_MAX_COMM, "comm"),
    ];
    for (range, (min, max, field)) in layout.strings.iter().zip(string_bounds) {
        let len = range.end - range.start;
        if len < min || len > max {
            return Err(field_bound(field));
        }
    }

    let mut offset = layout.ctx_start;
    for _ in 0..fixed.n_ctx {
        let _key = read_u8(body, &mut offset)?;
        let value = read_len_range(body, &mut offset)?;
        let len = value.end - value.start;
        if !(1..=EVENT_MAX_CONTEXT_TEXT).contains(&len) {
            return Err(field_bound("ctx.value"));
        }
    }

    offset = layout.attrs_start;
    for _ in 0..fixed.n_attrs {
        let key = read_len_range(body, &mut offset)?;
        let value = read_len_range(body, &mut offset)?;
        let key_len = key.end - key.start;
        let value_len = value.end - value.start;
        if !(1..=EVENT_MAX_TEXT).contains(&key_len) {
            return Err(field_bound("attrs.key"));
        }
        if value_len > EVENT_MAX_ATTRIBUTE_VALUE {
            return Err(field_bound("attrs.value"));
        }
    }
    Ok(())
}

fn is_name(bytes: &[u8]) -> bool {
    let Some((first, rest)) = bytes.split_first() else {
        return false;
    };
    first.is_ascii_lowercase()
        && rest.iter().all(|byte| {
            byte.is_ascii_lowercase()
                || byte.is_ascii_digit()
                || matches!(*byte, b'.' | b'_' | b'-')
        })
}

fn checked_text<'a>(
    body: &'a [u8],
    range: ByteRange,
    field: &'static str,
    name_grammar: bool,
) -> Result<&'a str, ProtoError> {
    let bytes = body
        .get(range.start..range.end)
        .ok_or_else(|| section_length(body.len(), range.end))?;
    let text = std::str::from_utf8(bytes).map_err(|_| ProtoError::EventText { field })?;
    if bytes.iter().any(|byte| *byte < 0x20 || *byte == 0x7f) || (name_grammar && !is_name(bytes)) {
        return Err(ProtoError::EventText { field });
    }
    Ok(text)
}

fn validate_text(body: &[u8], layout: &BodyLayout) -> Result<(), ProtoError> {
    let names = ["name", "key", "code", "comm"];
    for (range, field) in layout.strings.iter().zip(names) {
        checked_text(body, *range, field, field == "name")?;
    }

    let mut offset = layout.ctx_start;
    for _ in 0..layout.fixed.n_ctx {
        let _key = read_u8(body, &mut offset)?;
        let value = read_len_range(body, &mut offset)?;
        checked_text(body, value, "ctx.value", false)?;
    }

    offset = layout.attrs_start;
    for _ in 0..layout.fixed.n_attrs {
        let key = read_len_range(body, &mut offset)?;
        let value = read_len_range(body, &mut offset)?;
        checked_text(body, key, "attrs.key", true)?;
        checked_text(body, value, "attrs.value", false)?;
    }
    Ok(())
}

fn validate_order(body: &[u8], layout: &BodyLayout) -> Result<(), ProtoError> {
    let mut offset = layout.ctx_start;
    let mut previous = None;
    for index in 0..layout.fixed.n_ctx {
        let key = read_u8(body, &mut offset)?;
        if previous.is_some_and(|previous_key| key <= previous_key) {
            return Err(ProtoError::EventOrder {
                field: "ctx",
                index: usize::from(index),
            });
        }
        previous = Some(key);
        let _value = read_len_range(body, &mut offset)?;
    }

    offset = layout.attrs_start;
    let mut previous_key: Option<&[u8]> = None;
    for index in 0..layout.fixed.n_attrs {
        let key = read_len_range(body, &mut offset)?;
        let key_bytes = body
            .get(key.start..key.end)
            .ok_or_else(|| section_length(body.len(), key.end))?;
        if previous_key.is_some_and(|previous| key_bytes <= previous) {
            return Err(ProtoError::EventOrder {
                field: "attrs",
                index: usize::from(index),
            });
        }
        previous_key = Some(key_bytes);
        let _value = read_len_range(body, &mut offset)?;
    }
    Ok(())
}

/// Encode one event/1 section body, including its zero padding to an eight-byte boundary.
///
/// The value is validated before bytes are written. Context and attribute vectors must be
/// strictly ordered; this encoder does not reorder data.
///
/// # Errors
///
/// Returns the same event refusal kinds as the decoder for unknown codes, out-of-bound
/// fields, invalid text, and non-canonical ordering.
pub fn encode_event_payload(event: &EventPayload) -> Result<Vec<u8>, ProtoError> {
    if !(1..=3).contains(&event.kind) {
        return Err(ProtoError::EventUnknownCode {
            field: "kind",
            code: event.kind,
        });
    }
    if event.outcome > 7 {
        return Err(ProtoError::EventUnknownCode {
            field: "outcome",
            code: event.outcome,
        });
    }
    if (event.kind == 2) != (event.outcome != 0) {
        return Err(ProtoError::EventUnknownCode {
            field: "outcome/kind",
            code: event.outcome,
        });
    }
    for entry in &event.ctx {
        if entry.key > 4 {
            return Err(ProtoError::EventUnknownCode {
                field: "ctx",
                code: entry.key,
            });
        }
    }
    if event.t_mono_ns < 0 {
        return Err(field_bound("t_mono_ns"));
    }
    if (event.kind == 3) != (event.span == 0) {
        return Err(field_bound("span"));
    }
    if (event.peer_dev == 0) != (event.peer_inode == 0) {
        return Err(field_bound("peer"));
    }
    if event.ctx.len() > usize::from(EVENT_MAX_CONTEXT) {
        return Err(field_bound("n_ctx"));
    }
    if event.attrs.len() > usize::from(EVENT_MAX_ATTRIBUTES) {
        return Err(field_bound("n_attrs"));
    }

    let fixed_strings = [
        (event.name.as_str(), 1, EVENT_MAX_TEXT, "name", true, false),
        (event.key.as_str(), 1, EVENT_MAX_TEXT, "key", false, false),
        (
            event.code.as_deref().unwrap_or(""),
            0,
            EVENT_MAX_TEXT,
            "code",
            false,
            event.code.is_some(),
        ),
        (
            event.comm.as_deref().unwrap_or(""),
            0,
            EVENT_MAX_COMM,
            "comm",
            false,
            event.comm.is_some(),
        ),
    ];
    for (text, min, max, field, grammar, present_must_be_nonempty) in fixed_strings {
        let len = text.len();
        if len < min || len > max || (present_must_be_nonempty && text.is_empty()) {
            return Err(field_bound(field));
        }
        validate_string(text, field, grammar)?;
    }

    for entry in &event.ctx {
        if !(1..=EVENT_MAX_CONTEXT_TEXT).contains(&entry.value.len()) {
            return Err(field_bound("ctx.value"));
        }
        validate_string(&entry.value, "ctx.value", false)?;
    }
    for entry in &event.attrs {
        if !(1..=EVENT_MAX_TEXT).contains(&entry.key.len()) {
            return Err(field_bound("attrs.key"));
        }
        if entry.value.len() > EVENT_MAX_ATTRIBUTE_VALUE {
            return Err(field_bound("attrs.value"));
        }
        validate_string(&entry.key, "attrs.key", true)?;
        validate_string(&entry.value, "attrs.value", false)?;
    }
    for pair in event.ctx.windows(2) {
        if pair[0].key >= pair[1].key {
            return Err(ProtoError::EventOrder {
                field: "ctx",
                index: 1,
            });
        }
    }
    for pair in event.attrs.windows(2) {
        if pair[0].key.as_bytes() >= pair[1].key.as_bytes() {
            return Err(ProtoError::EventOrder {
                field: "attrs",
                index: 1,
            });
        }
    }

    let mut writer = Writer::new();
    writer.i64(event.t_mono_ns);
    writer.u64(event.span);
    writer.u64(event.peer_dev);
    writer.u64(event.peer_inode);
    writer.u32(event.uid);
    writer.u32(event.peer_pid);
    writer.u8(event.kind);
    writer.u8(event.outcome);
    writer.u8(u8::from(event.inferred));
    writer.u8(u8::try_from(event.ctx.len()).map_err(|_| field_bound("n_ctx"))?);
    writer.u8(u8::try_from(event.attrs.len()).map_err(|_| field_bound("n_attrs"))?);
    writer.u8(0);
    writer.u16(0);
    write_string(&mut writer, Some(&event.name))?;
    write_string(&mut writer, Some(&event.key))?;
    write_string(&mut writer, event.code.as_deref())?;
    write_string(&mut writer, event.comm.as_deref())?;
    for entry in &event.ctx {
        writer.u8(entry.key);
        write_string(&mut writer, Some(&entry.value))?;
    }
    for entry in &event.attrs {
        write_string(&mut writer, Some(&entry.key))?;
        write_string(&mut writer, Some(&entry.value))?;
    }
    writer.pad_to(8);
    Ok(writer.finish())
}

fn validate_string(text: &str, field: &'static str, name_grammar: bool) -> Result<(), ProtoError> {
    let bytes = text.as_bytes();
    if bytes.iter().any(|byte| *byte < 0x20 || *byte == 0x7f) || (name_grammar && !is_name(bytes)) {
        return Err(ProtoError::EventText { field });
    }
    Ok(())
}

fn write_string(writer: &mut Writer, value: Option<&str>) -> Result<(), ProtoError> {
    let bytes = value.unwrap_or("").as_bytes();
    let len = u16::try_from(bytes.len()).map_err(|_| field_bound("string"))?;
    writer.u16(len);
    writer.bytes(bytes);
    Ok(())
}

/// Decode one event/1 section body.
///
/// Checks run in WIRE.md §3.10 order: truncation, section length, reserved bytes and padding,
/// closed codes, bounds, text, then canonical ordering. Strings are copied only after all
/// counts and lengths have passed their bounds.
///
/// # Errors
///
/// Returns a typed refusal for every malformed body and never panics on untrusted input.
pub fn decode_event_payload(body: &[u8]) -> Result<EventPayload, ProtoError> {
    let layout = parse_layout(body)?;
    validate_reserved(body, &layout)?;
    validate_codes(body, &layout)?;
    validate_bounds(body, &layout)?;
    validate_text(body, &layout)?;
    validate_order(body, &layout)?;

    let mut offset = layout.ctx_start;
    let mut ctx = Vec::with_capacity(usize::from(layout.fixed.n_ctx));
    for _ in 0..layout.fixed.n_ctx {
        let key = read_u8(body, &mut offset)?;
        let range = read_len_range(body, &mut offset)?;
        let value = checked_text(body, range, "ctx.value", false)?.to_owned();
        ctx.push(EventContext { key, value });
    }

    offset = layout.attrs_start;
    let mut attrs = Vec::with_capacity(usize::from(layout.fixed.n_attrs));
    for _ in 0..layout.fixed.n_attrs {
        let key_range = read_len_range(body, &mut offset)?;
        let value_range = read_len_range(body, &mut offset)?;
        let key = checked_text(body, key_range, "attrs.key", true)?.to_owned();
        let value = checked_text(body, value_range, "attrs.value", false)?.to_owned();
        attrs.push(EventAttribute { key, value });
    }

    let name = checked_text(body, layout.strings[0], "name", true)?.to_owned();
    let key = checked_text(body, layout.strings[1], "key", false)?.to_owned();
    let code_text = checked_text(body, layout.strings[2], "code", false)?;
    let comm_text = checked_text(body, layout.strings[3], "comm", false)?;
    Ok(EventPayload {
        t_mono_ns: layout.fixed.t_mono_ns,
        span: layout.fixed.span,
        peer_dev: layout.fixed.peer_dev,
        peer_inode: layout.fixed.peer_inode,
        uid: layout.fixed.uid,
        peer_pid: layout.fixed.peer_pid,
        kind: layout.fixed.kind,
        outcome: layout.fixed.outcome,
        inferred: layout.fixed.flags & 1 != 0,
        ctx,
        attrs,
        name,
        key,
        code: (!code_text.is_empty()).then(|| code_text.to_owned()),
        comm: (!comm_text.is_empty()).then(|| comm_text.to_owned()),
    })
}

/// Encode an event/1 body as its stored EVT frame.
///
/// The frame has type 3, flags SECTIONED, reserved and correlation fields zero, and exactly
/// one required kind-7 version-1 section. It is for the events corpus and must not be sent on
/// a socket.
///
/// # Errors
///
/// Returns the event body encoder's validation errors or a typed frame-size refusal.
pub fn encode_event_frame(event: &EventPayload) -> Result<Vec<u8>, ProtoError> {
    let body = encode_event_payload(event)?;
    let total = FRAME_HEADER_LEN
        .checked_add(4)
        .and_then(|len| len.checked_add(8))
        .and_then(|len| len.checked_add(body.len()))
        .ok_or(ProtoError::LengthOverflow)?;
    if total > MAX_FRAME_BYTES {
        return Err(ProtoError::OversizedFrame {
            len: total,
            max: MAX_FRAME_BYTES,
        });
    }
    let total_u32 = u32::try_from(total).map_err(|_| ProtoError::LengthOverflow)?;
    let body_len = u32::try_from(body.len()).map_err(|_| ProtoError::LengthOverflow)?;
    let mut writer = Writer::with_capacity(total);
    writer.u32(total_u32);
    writer.u8(3);
    writer.u8(FLAG_SECTIONED);
    writer.u16(0);
    writer.u32(0);
    writer.u16(1);
    writer.u16(0);
    writer.u16(EVENT_SECTION_KIND);
    writer.u8(EVENT_SECTION_VERSION);
    writer.u8(EVENT_SECTION_REQUIRED_FLAGS);
    writer.u32(body_len);
    writer.bytes(&body);
    Ok(writer.finish())
}

/// Decode one stored event frame and its event/1 section.
///
/// Checks the EVT header and single-section directory in the same order as the Python
/// reference's `event_body_from_frame`, then validates and decodes the event body.
///
/// # Errors
///
/// Returns a typed refusal for a malformed frame, section directory, or event body.
pub fn decode_event_frame(frame: &[u8]) -> Result<EventPayload, ProtoError> {
    if frame.len() < EVENT_FRAME_FIXED_LEN {
        return Err(ProtoError::Truncated {
            context: "event frame",
            need: EVENT_FRAME_FIXED_LEN,
            have: frame.len(),
        });
    }

    let length = u32::from_le_bytes(fixed_array(frame, 0)?);
    let frame_type = frame[4];
    if frame_type != FrameType::Evt.code() {
        return Err(ProtoError::UnknownFrameType(frame_type));
    }
    let flags = frame[5];
    if flags != FLAG_SECTIONED {
        return Err(ProtoError::UnknownFrameFlags(flags));
    }
    let reserved = u16::from_le_bytes(fixed_array(frame, 6)?);
    let corr = u32::from_le_bytes(fixed_array(frame, 8)?);
    if reserved != 0 {
        return Err(ProtoError::NonZeroReserved(u32::from(reserved)));
    }
    if corr != 0 {
        return Err(ProtoError::NonZeroReserved(corr));
    }

    let declared_len = usize::try_from(length).map_err(|_| ProtoError::LengthOverflow)?;
    if declared_len > MAX_FRAME_BYTES {
        return Err(ProtoError::OversizedFrame {
            len: declared_len,
            max: MAX_FRAME_BYTES,
        });
    }
    if declared_len != frame.len() {
        return Err(ProtoError::Truncated {
            context: "event frame",
            need: declared_len,
            have: frame.len(),
        });
    }

    let aligned_len = frame
        .len()
        .checked_add(7)
        .ok_or(ProtoError::LengthOverflow)?
        & !7;
    if frame.len() != aligned_len {
        return Err(section_length(frame.len(), aligned_len));
    }

    let n_sec = u16::from_le_bytes(fixed_array(frame, FRAME_HEADER_LEN)?);
    let directory_reserved = u16::from_le_bytes(fixed_array(frame, FRAME_HEADER_LEN + 2)?);
    if directory_reserved != 0 {
        return Err(ProtoError::SectionReserved(directory_reserved));
    }
    let kind = u16::from_le_bytes(fixed_array(frame, FRAME_HEADER_LEN + 4)?);
    let version = frame[FRAME_HEADER_LEN + 6];
    let section_flags = frame[FRAME_HEADER_LEN + 7];
    let body_len = u32::from_le_bytes(fixed_array(frame, FRAME_HEADER_LEN + 8)?);

    if n_sec != 1 {
        return Err(section_length(usize::from(n_sec), 1));
    }
    if kind != EVENT_SECTION_KIND {
        return Err(ProtoError::UnsupportedRequiredSection { kind });
    }
    if version != EVENT_SECTION_VERSION {
        return Err(ProtoError::UnsupportedSectionVersion {
            kind,
            version,
            supported: EVENT_SECTION_VERSION,
        });
    }
    if section_flags != EVENT_SECTION_REQUIRED_FLAGS {
        return Err(ProtoError::UnknownSectionFlags {
            kind,
            flags: u32::from(section_flags),
        });
    }

    let declared_body_len = usize::try_from(body_len).map_err(|_| ProtoError::LengthOverflow)?;
    let actual_body_len = frame.len() - EVENT_FRAME_FIXED_LEN;
    if declared_body_len != actual_body_len {
        return Err(section_length(declared_body_len, actual_body_len));
    }
    decode_event_payload(&frame[EVENT_FRAME_FIXED_LEN..])
}

#[cfg(test)]
mod tests {
    use super::{
        decode_event_frame, decode_event_payload, encode_event_frame, encode_event_payload,
        EventAttribute, EventContext, EventPayload, EVENT_SECTION_KIND,
    };
    use crate::error::{ProtoError, MAX_FRAME_BYTES};
    use crate::frame::{decode_block, FrameHeader, SectionKind, FLAG_SECTIONED};

    const FIXTURE_DIR: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../../fixtures/wire");

    fn fixture(name: &str) -> Vec<u8> {
        std::fs::read(format!("{FIXTURE_DIR}/{name}.bin")).expect("event fixture is committed")
    }

    fn fixture_event(name: &str) -> EventPayload {
        decode_event_frame(&fixture(name)).expect("event frame fixture decodes")
    }

    fn minimal_event() -> EventPayload {
        EventPayload {
            t_mono_ns: 0,
            span: 0,
            peer_dev: 0,
            peer_inode: 0,
            uid: 0,
            peer_pid: 0,
            kind: 3,
            outcome: 0,
            inferred: false,
            ctx: Vec::new(),
            attrs: Vec::new(),
            name: "a".to_string(),
            key: "k".to_string(),
            code: None,
            comm: None,
        }
    }

    fn full_event() -> EventPayload {
        EventPayload {
            t_mono_ns: i64::MAX,
            span: u64::MAX,
            peer_dev: 1,
            peer_inode: u64::MAX,
            uid: u32::MAX,
            peer_pid: u32::MAX,
            kind: 2,
            outcome: 7,
            inferred: true,
            ctx: (0..5)
                .map(|key| EventContext {
                    key,
                    value: "x".repeat(128),
                })
                .collect(),
            attrs: (b'a'..=b'h')
                .map(|key| EventAttribute {
                    key: char::from(key).to_string(),
                    value: if key == b'a' {
                        String::new()
                    } else {
                        "v".repeat(256)
                    },
                })
                .collect(),
            name: "n".repeat(64),
            key: "k".repeat(64),
            code: Some("c".repeat(64)),
            comm: Some("m".repeat(15)),
        }
    }

    fn point_context_event() -> EventPayload {
        EventPayload {
            t_mono_ns: 1,
            span: 0,
            peer_dev: 0,
            peer_inode: 0,
            uid: 1000,
            peer_pid: 42,
            kind: 3,
            outcome: 0,
            inferred: false,
            ctx: vec![
                EventContext {
                    key: 0,
                    value: "run-1".to_string(),
                },
                EventContext {
                    key: 4,
                    value: "session-1".to_string(),
                },
            ],
            attrs: Vec::new(),
            name: "run.point".to_string(),
            key: "run.point".to_string(),
            code: None,
            comm: Some("worker".to_string()),
        }
    }

    fn assert_kind(result: Result<EventPayload, ProtoError>, expected: &str) {
        let error = result.expect_err("the event input must be refused");
        assert_eq!(error.kind(), expected);
    }

    #[test]
    fn event_fixtures_round_trip_and_match_the_python_frames() {
        for (name, event) in [
            ("event-minimal", minimal_event()),
            ("event-full", full_event()),
            ("event-point-ctx", point_context_event()),
        ] {
            let frame = fixture(name);
            assert_eq!(encode_event_frame(&event).expect("encode frame"), frame);
            assert_eq!(fixture_event(name), event);
            let body = encode_event_payload(&event).expect("encode body");
            assert_eq!(decode_event_payload(&body).expect("decode body"), event);
        }

        for (name, expected) in [
            ("event-unknown-code", "event-unknown-code"),
            ("event-overflow", "event-field-bound"),
            ("event-unsorted-attrs", "event-order"),
            ("event-control-text", "event-text"),
        ] {
            assert_kind(decode_event_frame(&fixture(name)), expected);
        }
    }

    #[test]
    fn stored_event_frame_rejects_header_and_directory_defects_in_order() {
        let valid = fixture("event-minimal");
        assert_eq!(decode_event_frame(&valid), Ok(minimal_event()));

        assert_kind(decode_event_frame(&valid[..23]), "truncated");

        let mut wrong_type = valid.clone();
        wrong_type[4] = 1;
        assert_kind(decode_event_frame(&wrong_type), "unknown-frame-type");

        let mut wrong_flags = valid.clone();
        wrong_flags[5] = 0;
        assert_kind(decode_event_frame(&wrong_flags), "unknown-frame-flags");

        let mut reserved = valid.clone();
        reserved[6] = 1;
        assert_kind(decode_event_frame(&reserved), "non-zero-reserved");

        let mut nonzero_corr = valid.clone();
        nonzero_corr[8..12].copy_from_slice(&65_536u32.to_le_bytes());
        let corr_error = decode_event_frame(&nonzero_corr).expect_err("corr must be zero");
        assert_eq!(corr_error.kind(), "non-zero-reserved");
        assert_eq!(corr_error.to_string(), "expected a zero value, found 65536");

        let mut oversized = valid.clone();
        let oversized_len = u32::try_from(MAX_FRAME_BYTES + 1).expect("maximum fits u32");
        oversized[..4].copy_from_slice(&oversized_len.to_le_bytes());
        assert_kind(decode_event_frame(&oversized), "oversized-frame");

        let mut wrong_length = valid.clone();
        let wrong_len = u32::try_from(valid.len() + 1).expect("test frame length fits u32");
        wrong_length[..4].copy_from_slice(&wrong_len.to_le_bytes());
        assert_kind(decode_event_frame(&wrong_length), "truncated");

        let mut unaligned = valid.clone();
        unaligned.push(0);
        let unaligned_len = u32::try_from(unaligned.len()).expect("test frame length fits u32");
        unaligned[..4].copy_from_slice(&unaligned_len.to_le_bytes());
        assert_kind(decode_event_frame(&unaligned), "section-length");

        let mut wrong_count = valid.clone();
        wrong_count[12..14].copy_from_slice(&2u16.to_le_bytes());
        assert_kind(decode_event_frame(&wrong_count), "section-length");

        let mut directory_reserved = valid.clone();
        directory_reserved[14] = 1;
        assert_kind(decode_event_frame(&directory_reserved), "section-reserved");

        let mut wrong_kind = valid.clone();
        wrong_kind[16..18].copy_from_slice(&8u16.to_le_bytes());
        assert_kind(
            decode_event_frame(&wrong_kind),
            "unsupported-required-section",
        );

        let mut wrong_version = valid.clone();
        wrong_version[18] = 2;
        assert_kind(
            decode_event_frame(&wrong_version),
            "unsupported-section-version",
        );

        let mut wrong_section_flags = valid.clone();
        wrong_section_flags[19] = 0;
        assert_kind(
            decode_event_frame(&wrong_section_flags),
            "unknown-section-flags",
        );

        let mut wrong_body_len = valid;
        wrong_body_len[20..24].copy_from_slice(&1u32.to_le_bytes());
        assert_kind(decode_event_frame(&wrong_body_len), "section-length");
    }

    #[test]
    fn every_event_full_body_prefix_is_refused_without_panicking() {
        let body = encode_event_payload(&full_event()).expect("full event body");
        for end in 0..body.len() {
            let error = decode_event_payload(&body[..end])
                .expect_err("a prefix shorter than event-full is incomplete");
            assert!(
                error.kind() == "truncated" || error.kind() == "section-length",
                "prefix {end} returned {}",
                error.kind()
            );
        }
        assert!(decode_event_payload(&body).is_ok());
    }

    #[test]
    fn section_length_reserved_and_padding_boundaries_are_checked_in_order() {
        let valid = encode_event_payload(&minimal_event()).expect("minimum body");
        assert_eq!(decode_event_payload(&valid), Ok(minimal_event()));

        let mut extra_pad = valid.clone();
        extra_pad.extend_from_slice(&[0; 8]);
        assert_kind(decode_event_payload(&extra_pad), "section-length");

        let mut past_end = valid.clone();
        past_end[48] = 0xff;
        past_end[49] = 0xff;
        assert_kind(decode_event_payload(&past_end), "section-length");

        let mut reserved = valid.clone();
        reserved[45] = 1;
        assert_kind(decode_event_payload(&reserved), "non-zero-reserved");

        let mut bad_pad = valid.clone();
        let last = bad_pad.len() - 1;
        bad_pad[last] = 1;
        assert_kind(decode_event_payload(&bad_pad), "non-zero-reserved");

        assert_kind(decode_event_payload(&[]), "truncated");
        assert_kind(decode_event_payload(&valid[..47]), "truncated");
    }

    #[test]
    fn unknown_codes_and_outcome_pairing_are_refused() {
        let valid = encode_event_payload(&point_context_event()).expect("valid point");
        let mut valid_begin = point_context_event();
        valid_begin.kind = 1;
        valid_begin.span = 1;
        let begin_body = encode_event_payload(&valid_begin).expect("kind 1 is a valid begin");
        assert_eq!(decode_event_payload(&begin_body), Ok(valid_begin));

        let mut unknown_kind = valid.clone();
        unknown_kind[40] = 4;
        assert_kind(decode_event_payload(&unknown_kind), "event-unknown-code");

        let mut unknown_outcome = valid.clone();
        unknown_outcome[41] = 8;
        assert_kind(decode_event_payload(&unknown_outcome), "event-unknown-code");

        let mut paired_outcome = valid.clone();
        paired_outcome[40] = 1;
        paired_outcome[41] = 1;
        assert_kind(decode_event_payload(&paired_outcome), "event-unknown-code");

        let mut unknown_context = valid;
        unknown_context[80] = 5;
        assert_kind(decode_event_payload(&unknown_context), "event-unknown-code");
    }

    #[test]
    fn field_bounds_text_and_context_order_have_inside_and_outside_cases() {
        let full = encode_event_payload(&full_event()).expect("full event body");
        assert!(
            decode_event_payload(&full).is_ok(),
            "maximum values are accepted"
        );

        let layout = super::parse_layout(&full).expect("the full body is well-formed");
        let mut too_many_contexts = full[..layout.content_end].to_vec();
        drop(too_many_contexts.splice(layout.attrs_start..layout.attrs_start, [0, 1, 0, b'x']));
        too_many_contexts[43] = 6;
        let aligned = too_many_contexts
            .len()
            .checked_add(7)
            .expect("aligned context length")
            & !7;
        too_many_contexts.resize(aligned, 0);
        assert_kind(
            decode_event_payload(&too_many_contexts),
            "event-field-bound",
        );

        let mut negative_time = full.to_vec();
        negative_time[..8].fill(0xff);
        assert_kind(decode_event_payload(&negative_time), "event-field-bound");

        let mut zero_span = full.to_vec();
        zero_span[8..16].fill(0);
        assert_kind(decode_event_payload(&zero_span), "event-field-bound");

        let mut half_null_peer = encode_event_payload(&point_context_event()).expect("point body");
        half_null_peer[16] = 1;
        assert_kind(decode_event_payload(&half_null_peer), "event-field-bound");

        let long_value = full.to_vec();
        let value_layout =
            super::parse_layout(&long_value).expect("the full fixture is well-formed");
        let mut offset = value_layout.attrs_start;
        let _first_key =
            super::read_len_range(&long_value, &mut offset).expect("first attribute key");
        let value_len_offset = offset;
        let value_end = super::read_len_range(&long_value, &mut offset)
            .expect("first attribute value")
            .end;
        let mut oversized_value = long_value[..value_layout.content_end].to_vec();
        drop(oversized_value.splice(value_end..value_end, vec![b'v'; 257]));
        oversized_value[value_len_offset..value_len_offset + 2]
            .copy_from_slice(&257u16.to_le_bytes());
        let new_len = oversized_value
            .len()
            .checked_add(7)
            .expect("aligned length")
            & !7;
        oversized_value.resize(new_len, 0);
        assert_kind(decode_event_payload(&oversized_value), "event-field-bound");

        let mut invalid_utf8 = full.to_vec();
        invalid_utf8[50] = 0xff;
        assert_kind(decode_event_payload(&invalid_utf8), "event-text");

        let mut reversed_context =
            encode_event_payload(&point_context_event()).expect("point context body");
        reversed_context.swap(80, 88);
        assert_kind(decode_event_payload(&reversed_context), "event-order");
    }

    #[test]
    fn encoder_refuses_invalid_events_and_frame_flags_stay_socket_local() {
        let mut too_many = full_event();
        too_many.attrs.push(EventAttribute {
            key: "i".to_string(),
            value: String::new(),
        });
        assert_eq!(
            encode_event_payload(&too_many)
                .expect_err("nine attributes exceed the bound")
                .kind(),
            "event-field-bound"
        );

        let mut empty_code = minimal_event();
        empty_code.code = Some(String::new());
        assert_eq!(
            encode_event_payload(&empty_code)
                .expect_err("a present empty code is wire-null")
                .kind(),
            "event-field-bound"
        );

        let mut empty_comm = minimal_event();
        empty_comm.comm = Some(String::new());
        assert_eq!(
            encode_event_payload(&empty_comm)
                .expect_err("a present empty comm is wire-null")
                .kind(),
            "event-field-bound"
        );

        let frame = encode_event_frame(&minimal_event()).expect("stored frame");
        assert_eq!(
            FrameHeader::parse(&frame),
            Err(ProtoError::UnknownFrameFlags(FLAG_SECTIONED))
        );

        assert_eq!(SectionKind::from_code(EVENT_SECTION_KIND), None);
    }

    #[test]
    fn kind_seven_remains_unknown_inside_a_block() {
        let body = encode_event_payload(&minimal_event()).expect("event body");
        let total = 12 + 4 + 16 + 80 + body.len();
        let mut block = WriterForTest::block_with_event(&body, total);
        assert_eq!(
            decode_block(&block)
                .expect_err("kind 7 is not a BLOCK section")
                .kind(),
            "unsupported-required-section"
        );
        block[27] = 0;
        assert_eq!(
            decode_block(&block)
                .expect("unknown optional kind is skipped")
                .skipped_sections,
            vec![EVENT_SECTION_KIND]
        );
    }

    struct WriterForTest;

    impl WriterForTest {
        fn block_with_event(body: &[u8], total: usize) -> Vec<u8> {
            let mut bytes = Vec::with_capacity(total);
            bytes.extend_from_slice(
                &u32::try_from(total)
                    .expect("test block length fits u32")
                    .to_le_bytes(),
            );
            bytes.extend_from_slice(&[5, 1, 0, 0]);
            bytes.extend_from_slice(&[0; 4]);
            bytes.extend_from_slice(&[2, 0, 0, 0]);
            bytes.extend_from_slice(&[1, 0, 1, 1, 80, 0, 0, 0]);
            bytes.extend_from_slice(&[7, 0, 1, 1]);
            bytes.extend_from_slice(
                &u32::try_from(body.len())
                    .expect("event body length fits u32")
                    .to_le_bytes(),
            );
            bytes.extend_from_slice(&[0; 80]);
            bytes.extend_from_slice(body);
            bytes
        }
    }
}
