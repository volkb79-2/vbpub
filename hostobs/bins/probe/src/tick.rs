//! Validation and encoding of one backend tick (design S6, S11, S14 and S16).
//!
//! A backend [`TickData`] becomes one canonical BLOCK. The bare form carries no dictionary;
//! the dictionary form inserts the generation's DICT body after the header. The server judges
//! the dictionary form's full frame length arithmetically before it encodes anything.

use std::cell::OnceCell;
use std::collections::BTreeSet;
use std::sync::Arc;

use hostobs_proto::dict::{Dictionary, Entry, DEFAULT_ACTIVE_MAX};
use hostobs_proto::error::ProtoError;
use hostobs_proto::frame::{
    encode_block, Block, FrameType, SectionKind, FRAME_HEADER_LEN, HEADER_BODY_LEN,
    SECTION_DIR_ENTRY_LEN,
};
use hostobs_proto::hub::TickForms;
use hostobs_proto::quality::Quality;

use crate::backend::TickData;
use crate::codes::{Code, Detail, Pool};

const N_SEC_OFFSET: usize = 12;
const DIRECTORY_OFFSET: usize = 16;

/// The row and byte ceilings of one built tick.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct BlockBounds {
    /// Most rows a backend resolution may return.
    pub max_rows: u32,
    /// Largest frame the dictionary-bearing form may have.
    pub max_block_bytes: u32,
}

/// Why a tick could not be validated or encoded.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TickError {
    /// The resolution exceeded the row bound.
    Rows,
    /// A dictionary, row, column or quality fact is invalid.
    Invalid,
    /// A generation counter has no next value.
    Exhausted,
    /// The dictionary-bearing form exceeds the byte bound.
    Oversize,
    /// The protocol encoder refused the block.
    Encode,
}

impl TickError {
    /// The wire refusal this tick failure produces.
    #[must_use]
    pub fn refusal(self) -> (Code, Detail) {
        match self {
            TickError::Rows => (Code::Budget, Detail::Pool(Pool::Rows)),
            TickError::Invalid | TickError::Exhausted | TickError::Oversize | TickError::Encode => {
                (Code::Internal, Detail::Empty)
            }
        }
    }
}

fn n_rows_u32(len: usize) -> Result<u32, TickError> {
    u32::try_from(len).map_err(|_| TickError::Invalid)
}

fn checked_align8(value: usize) -> Option<usize> {
    value.checked_add(7)?.checked_div(8)?.checked_mul(8)
}

/// Validate every backend-owned fact of a live tick (design S16).
///
/// # Errors
///
/// [`TickError::Invalid`] for a non-entity row reference, a non-metric or out-of-range
/// column reference, a column-kind mismatch, a row-count mismatch, a repeated metric
/// index, an unknown quality code or a row count that does not fit `u32`.
pub fn validate(data: &TickData) -> Result<(), TickError> {
    n_rows_u32(data.rows.len())?;
    for row in &data.rows {
        let index = usize::try_from(row.ent).map_err(|_| TickError::Invalid)?;
        if !matches!(data.entries.get(index), Some(Entry::Entity { .. })) {
            return Err(TickError::Invalid);
        }
    }
    let mut metrics = BTreeSet::new();
    for column in &data.columns {
        let index = usize::try_from(column.metric).map_err(|_| TickError::Invalid)?;
        match data.entries.get(index) {
            Some(Entry::Metric { facts, .. }) if facts.kind == column.kind => {}
            _ => return Err(TickError::Invalid),
        }
        if column.values.len() != data.rows.len() || column.quality.len() != data.rows.len() {
            return Err(TickError::Invalid);
        }
        if !metrics.insert(column.metric) {
            return Err(TickError::Invalid);
        }
        for (row, code) in column.quality.iter().copied().enumerate() {
            Quality::from_code(code, row).map_err(|_| TickError::Invalid)?;
        }
    }
    Ok(())
}

/// Encode the generation's canonical dictionary body, interning each entry at its own
/// position.
///
/// # Errors
///
/// [`TickError::Invalid`] when an entry repeats an earlier identity, cannot be interned or
/// cannot be encoded.
pub fn dictionary_body(generation: u32, entries: &[Entry]) -> Result<Vec<u8>, TickError> {
    let mut dictionary = Dictionary::with_capacity(generation, DEFAULT_ACTIVE_MAX);
    for (position, entry) in entries.iter().enumerate() {
        let expected = u32::try_from(position).map_err(|_| TickError::Invalid)?;
        let found = dictionary.intern(entry.clone()).map_err(invalid)?;
        if found != expected {
            return Err(TickError::Invalid);
        }
    }
    dictionary.encode().map_err(invalid)
}

fn invalid(_error: ProtoError) -> TickError {
    TickError::Invalid
}

fn checked_encoded_len(
    n_rows: usize,
    n_columns: usize,
    dict_body_len: Option<usize>,
) -> Option<usize> {
    let mut n_sec = 1usize;
    if n_rows > 0 {
        n_sec = n_sec.checked_add(1)?;
    }
    n_sec = n_sec.checked_add(n_columns)?;
    let directory = n_sec.checked_mul(SECTION_DIR_ENTRY_LEN)?;
    let mut total = FRAME_HEADER_LEN
        .checked_add(4)?
        .checked_add(directory)?
        .checked_add(HEADER_BODY_LEN)?;
    if n_rows > 0 {
        total = total.checked_add(n_rows.checked_mul(8)?)?;
    }
    let column_raw = n_rows.checked_mul(9)?.checked_add(8)?;
    let column_body = checked_align8(column_raw)?;
    total = total.checked_add(n_columns.checked_mul(column_body)?)?;
    if let Some(body_len) = dict_body_len {
        total = total
            .checked_add(SECTION_DIR_ENTRY_LEN)?
            .checked_add(body_len)?;
    }
    Some(total)
}

/// The frame length [`encode_bare`] would produce, plus the dictionary section and body
/// when `dict_body_len` is given. Every arithmetic step is checked; `None` is an overflow
/// and is treated as [`TickError::Oversize`] by the caller.
#[must_use]
pub fn encoded_len(data: &TickData, dict_body_len: Option<usize>) -> Option<usize> {
    u32::try_from(data.rows.len()).ok()?;
    checked_encoded_len(data.rows.len(), data.columns.len(), dict_body_len)
}

fn block_of(data: &TickData, generation: u32, dictionary: Option<Dictionary>) -> Block {
    let mut header = data.header.clone();
    header.dict_gen = generation;
    header.n_rows = u32::try_from(data.rows.len()).unwrap_or(u32::MAX);
    header.dropped = 0;
    header.flags = 0;
    debug_assert!(u32::try_from(data.rows.len()).is_ok());
    Block {
        frame_type: FrameType::Block,
        corr: 0,
        header,
        rows: data.rows.clone(),
        time: Vec::new(),
        columns: data.columns.clone(),
        dictionary,
        subject: None,
        skipped_sections: Vec::new(),
    }
}

/// Encode the bare form of a tick with the server-owned header fields overwritten.
///
/// # Errors
///
/// [`TickError::Invalid`] when the row count does not fit `u32`; [`TickError::Encode`] when
/// [`encode_block`] refuses the block.
pub fn encode_bare(data: &TickData, generation: u32) -> Result<Vec<u8>, TickError> {
    n_rows_u32(data.rows.len())?;
    let block = block_of(data, generation, None);
    encode_block(&block).map_err(|_| TickError::Encode)
}

fn write_u8(bytes: &mut [u8], at: usize, value: u8) -> Option<()> {
    *bytes.get_mut(at)? = value;
    Some(())
}

fn write_u16(bytes: &mut [u8], at: usize, value: u16) -> Option<()> {
    let end = at.checked_add(2)?;
    bytes
        .get_mut(at..end)?
        .copy_from_slice(&value.to_le_bytes());
    Some(())
}

fn write_u32(bytes: &mut [u8], at: usize, value: u32) -> Option<()> {
    let end = at.checked_add(4)?;
    bytes
        .get_mut(at..end)?
        .copy_from_slice(&value.to_le_bytes());
    Some(())
}

fn copy_to(out: &mut [u8], at: usize, bytes: &[u8]) -> Option<()> {
    let end = at.checked_add(bytes.len())?;
    out.get_mut(at..end)?.copy_from_slice(bytes);
    Some(())
}

fn copy_span(out: &mut [u8], to: usize, source: &[u8], from: usize, until: usize) -> Option<()> {
    copy_to(out, to, source.get(from..until)?)
}

/// Insert a dictionary section and body after the bare frame's header, without a fallible
/// API. A degenerate input (no room for `n_sec + 1`, arithmetic overflow, a short frame or
/// a failed allocation) is returned unchanged.
#[must_use]
pub fn with_dictionary(bare: &[u8], dict_body: &[u8]) -> Vec<u8> {
    let Some(n_sec_end) = N_SEC_OFFSET.checked_add(2) else {
        return bare.to_vec();
    };
    let Some(raw) = bare.get(N_SEC_OFFSET..n_sec_end) else {
        return bare.to_vec();
    };
    let Ok(raw): Result<[u8; 2], _> = raw.try_into() else {
        return bare.to_vec();
    };
    let n_sec = usize::from(u16::from_le_bytes(raw));
    let Some(next) = n_sec.checked_add(1) else {
        return bare.to_vec();
    };
    let Ok(n_sec_next) = u16::try_from(next) else {
        return bare.to_vec();
    };
    let Some(old_directory_end) = n_sec
        .checked_mul(SECTION_DIR_ENTRY_LEN)
        .and_then(|len| DIRECTORY_OFFSET.checked_add(len))
    else {
        return bare.to_vec();
    };
    let Some(old_header_end) = old_directory_end.checked_add(HEADER_BODY_LEN) else {
        return bare.to_vec();
    };
    if bare.len() < old_header_end {
        return bare.to_vec();
    }
    let Some(new_directory_end) = old_directory_end.checked_add(SECTION_DIR_ENTRY_LEN) else {
        return bare.to_vec();
    };
    let Some(new_header_end) = old_header_end.checked_add(SECTION_DIR_ENTRY_LEN) else {
        return bare.to_vec();
    };
    let Some(dictionary_end) = new_header_end.checked_add(dict_body.len()) else {
        return bare.to_vec();
    };
    let Some(total) = bare
        .len()
        .checked_add(SECTION_DIR_ENTRY_LEN)
        .and_then(|len| len.checked_add(dict_body.len()))
    else {
        return bare.to_vec();
    };
    let extra = SECTION_DIR_ENTRY_LEN.saturating_add(dict_body.len());
    let mut out = bare.to_vec();
    if out.try_reserve(extra).is_err() {
        return bare.to_vec();
    }
    out.resize(total, 0);

    let length = u32::try_from(total).unwrap_or(u32::MAX);
    if write_u32(&mut out, 0, length).is_none()
        || copy_span(&mut out, 4, bare, 4, 12).is_none()
        || write_u16(&mut out, N_SEC_OFFSET, n_sec_next).is_none()
        || copy_span(&mut out, 14, bare, 14, DIRECTORY_OFFSET).is_none()
        || copy_span(&mut out, DIRECTORY_OFFSET, bare, DIRECTORY_OFFSET, 24).is_none()
    {
        return bare.to_vec();
    }
    let dictionary = SectionKind::Dictionary;
    if write_u16(&mut out, 24, dictionary.code()).is_none()
        || write_u8(&mut out, 26, dictionary.version()).is_none()
        || write_u8(&mut out, 27, 0).is_none()
        || u32::try_from(dict_body.len())
            .ok()
            .and_then(|len| write_u32(&mut out, 28, len))
            .is_none()
        || copy_span(&mut out, 32, bare, 24, old_directory_end).is_none()
        || copy_span(
            &mut out,
            new_directory_end,
            bare,
            old_directory_end,
            old_header_end,
        )
        .is_none()
        || copy_to(&mut out, new_header_end, dict_body).is_none()
        || copy_span(&mut out, dictionary_end, bare, old_header_end, bare.len()).is_none()
    {
        return bare.to_vec();
    }
    out
}

fn query_block_with<F>(
    data: TickData,
    bounds: BlockBounds,
    encode: F,
) -> Result<Arc<[u8]>, TickError>
where
    F: FnOnce(&TickData, u32) -> Result<Vec<u8>, TickError>,
{
    if data.rows.len() > usize::try_from(bounds.max_rows).unwrap_or(usize::MAX) {
        return Err(TickError::Rows);
    }
    validate(&data)?;
    let body = dictionary_body(1, &data.entries)?;
    let limit = usize::try_from(bounds.max_block_bytes).unwrap_or(usize::MAX);
    let Some(length) = encoded_len(&data, Some(body.len())) else {
        return Err(TickError::Oversize);
    };
    if length > limit {
        return Err(TickError::Oversize);
    }
    let bare = encode(&data, 1)?;
    Ok(Arc::from(with_dictionary(&bare, &body)))
}

/// Build a `current` query block at generation 1. The rows bound is checked, then validation,
/// the dictionary body and the full dictionary-form length, all before the bare encode.
///
/// # Errors
///
/// [`TickError::Rows`] for the row bound and the validation/dictionary/encoding failures of
/// [`validate`], [`dictionary_body`] and [`encode_bare`], or [`TickError::Oversize`] when
/// the dictionary-bearing frame exceeds `max_block_bytes`.
pub fn query_block(data: TickData, bounds: BlockBounds) -> Result<Arc<[u8]>, TickError> {
    query_block_with(data, bounds, encode_bare)
}

/// One encoded selection tick and its lazily built dictionary form.
pub struct LiveTick {
    generation: u32,
    bare: Option<Arc<[u8]>>,
    base: Arc<[u8]>,
    dict_body: Arc<[u8]>,
    dict: OnceCell<Arc<[u8]>>,
}

impl LiveTick {
    /// Build a tick. `bare` is absent on the tick that changes the generation; `base` is the
    /// no-dictionary frame in every case.
    #[must_use]
    pub(crate) fn new(
        generation: u32,
        bare: Option<Arc<[u8]>>,
        base: Arc<[u8]>,
        dict_body: Arc<[u8]>,
    ) -> LiveTick {
        LiveTick {
            generation,
            bare,
            base,
            dict_body,
            dict: OnceCell::new(),
        }
    }

    /// The dictionary generation every subscriber receives with this tick.
    #[must_use]
    pub fn generation(&self) -> u32 {
        self.generation
    }

    /// Whether this tick has a bare form.
    #[must_use]
    pub fn has_bare(&self) -> bool {
        self.bare.is_some()
    }

    /// Whether the dictionary form has already been built.
    #[must_use]
    pub fn dict_built(&self) -> bool {
        self.dict.get().is_some()
    }
}

impl TickForms for LiveTick {
    fn generation(&self) -> u32 {
        self.generation
    }

    fn bare(&self) -> Option<Arc<[u8]>> {
        self.bare.clone()
    }

    fn dict_form(&self) -> Arc<[u8]> {
        self.dict
            .get_or_init(|| Arc::from(with_dictionary(&self.base, &self.dict_body)))
            .clone()
    }
}

#[cfg(test)]
mod tests {
    use std::cell::Cell;
    use std::sync::Arc;

    use hostobs_proto::dict::{
        Dictionary, Entry, MetricFacts, MetricKind, MetricScope, MetricSource, MetricUnit,
        DEFAULT_ACTIVE_MAX,
    };
    use hostobs_proto::frame::{
        decode_block, decode_block_with, encode_block, Block, BlockHeaderFacts, FrameType,
    };
    use hostobs_proto::hub::TickForms;

    use super::{
        checked_encoded_len, dictionary_body, encode_bare, encoded_len, n_rows_u32, query_block,
        query_block_with, validate, with_dictionary, BlockBounds, LiveTick, TickError,
    };
    use crate::backend::TickData;
    use crate::codes::{Code, Detail, Pool};

    fn names(count: usize) -> Vec<String> {
        (0..count).map(|index| format!("m{index}")).collect()
    }

    fn data(rows: usize, columns: usize) -> TickData {
        let incs: Vec<u64> = (0..rows)
            .map(|index| u64::try_from(index).expect("small"))
            .collect();
        let names = names(columns);
        let refs: Vec<&str> = names.iter().map(String::as_str).collect();
        TickData::example(1, &incs, &refs)
    }

    fn metric() -> Entry {
        Entry::Metric {
            facts: MetricFacts {
                unit: MetricUnit::Count,
                kind: MetricKind::Gauge,
                scope: MetricScope::Cgroup,
                source: MetricSource::Probe,
            },
            name: "m".to_string(),
        }
    }

    fn with_dictionary_block(data: &TickData, generation: u32) -> Vec<u8> {
        let body = dictionary_body(generation, &data.entries).expect("dictionary body");
        let dictionary =
            Dictionary::decode(&body, DEFAULT_ACTIVE_MAX).expect("in-frame dictionary");
        let mut header = data.header.clone();
        header.dict_gen = generation;
        header.n_rows = u32::try_from(data.rows.len()).expect("small");
        header.dropped = 0;
        header.flags = 0;
        let block = Block {
            frame_type: FrameType::Block,
            corr: 0,
            header,
            rows: data.rows.clone(),
            time: Vec::new(),
            columns: data.columns.clone(),
            dictionary: Some(dictionary),
            subject: None,
            skipped_sections: Vec::new(),
        };
        encode_block(&block).expect("dictionary-bearing block")
    }

    #[test]
    fn tick_validate_rows_entity_ref() {
        let mut tick = data(1, 1);
        tick.rows[0].ent = 1;
        assert_eq!(validate(&tick), Err(TickError::Invalid));
        let mut tick = data(1, 1);
        tick.entries[0] = metric();
        assert_eq!(validate(&tick), Err(TickError::Invalid));
    }

    #[test]
    fn tick_validate_column_reference_kind_and_rows() {
        let original = data(1, 1);
        for mutate in [
            (|tick: &mut TickData| tick.columns[0].metric = 9) as fn(&mut TickData),
            |tick| {
                tick.entries[1] = Entry::Entity {
                    inc: 1,
                    label: String::new(),
                };
            },
            |tick| {
                tick.entries[1] = Entry::Metric {
                    facts: MetricFacts {
                        kind: MetricKind::Counter,
                        ..match &tick.entries[1] {
                            Entry::Metric { facts, .. } => *facts,
                            _ => unreachable!(),
                        }
                    },
                    name: "m".to_string(),
                };
            },
            |tick| {
                tick.columns[0].values.clear();
            },
            |tick| {
                tick.columns[0].quality.clear();
            },
        ] {
            let mut tick = original.clone();
            mutate(&mut tick);
            assert_eq!(validate(&tick), Err(TickError::Invalid));
        }
    }

    #[test]
    fn tick_validate_metric_indices_distinct() {
        let mut tick = data(1, 2);
        tick.columns[1].metric = tick.columns[0].metric;
        assert_eq!(validate(&tick), Err(TickError::Invalid));
    }

    #[test]
    fn tick_validate_quality_code() {
        let mut tick = data(1, 1);
        tick.columns[0].quality[0] = 9;
        assert_eq!(validate(&tick), Err(TickError::Invalid));
    }

    #[cfg(target_pointer_width = "64")]
    #[test]
    fn tick_validate_rows_fit_u32() {
        assert_eq!(n_rows_u32(usize::MAX), Err(TickError::Invalid));
    }

    #[test]
    fn tick_validate_intern_errors_map_to_invalid() {
        let too_long = vec![Entry::Free {
            text: "x".repeat(hostobs_proto::dict::MAX_ENTRY_TEXT + 1),
        }];
        assert_eq!(dictionary_body(1, &too_long), Err(TickError::Invalid));
        let control = vec![Entry::Free {
            text: "line\nbreak".to_string(),
        }];
        assert_eq!(dictionary_body(1, &control), Err(TickError::Invalid));
        let conflict = vec![
            metric(),
            Entry::Metric {
                facts: MetricFacts {
                    kind: MetricKind::Counter,
                    ..match metric() {
                        Entry::Metric { facts, .. } => facts,
                        _ => unreachable!(),
                    }
                },
                name: "m".to_string(),
            },
        ];
        assert_eq!(dictionary_body(1, &conflict), Err(TickError::Invalid));
        let too_many: Vec<Entry> = (0..=DEFAULT_ACTIVE_MAX)
            .map(|index| Entry::Free {
                text: format!("f{index}"),
            })
            .collect();
        assert_eq!(dictionary_body(1, &too_many), Err(TickError::Invalid));
    }

    #[test]
    fn tick_dictionary_body_repeated_identity_is_invalid() {
        let entity = Entry::Entity {
            inc: 1,
            label: "one".to_string(),
        };
        assert_eq!(
            dictionary_body(1, &[entity.clone(), entity]),
            Err(TickError::Invalid)
        );
    }

    #[test]
    fn tick_rows_over_max_is_rows_error() {
        let bounds = BlockBounds {
            max_rows: 0,
            max_block_bytes: u32::MAX,
        };
        assert_eq!(query_block(data(1, 1), bounds).err(), Some(TickError::Rows));
    }

    #[test]
    fn tick_size_bound_on_dict_form() {
        let tick = data(1, 8);
        let body = dictionary_body(1, &tick.entries).expect("body");
        let bare = encoded_len(&tick, None).expect("bare length");
        let dictionary = encoded_len(&tick, Some(body.len())).expect("dictionary length");
        assert!(bare < dictionary);
        let encoded = Cell::new(false);
        let bounds = BlockBounds {
            max_rows: 1,
            max_block_bytes: u32::try_from(bare).expect("small"),
        };
        let result = query_block_with(tick, bounds, |tick, generation| {
            encoded.set(true);
            encode_bare(tick, generation)
        });
        assert_eq!(result.err(), Some(TickError::Oversize));
        assert!(!encoded.get(), "encode_bare was not called");
    }

    #[test]
    fn tick_encoded_len_equals_real_lengths() {
        for rows in [0, 1, 4_096] {
            for columns in [0, 1, 8] {
                let tick = data(rows, columns);
                let body = dictionary_body(1, &tick.entries).expect("body");
                let bare = encode_bare(&tick, 1).expect("bare");
                assert_eq!(encoded_len(&tick, None), Some(bare.len()));
                let dictionary = with_dictionary(&bare, &body);
                assert_eq!(encoded_len(&tick, Some(body.len())), Some(dictionary.len()));
            }
        }
    }

    #[test]
    fn tick_encoded_len_overflow_is_none() {
        assert_eq!(checked_encoded_len(usize::MAX, 1, None), None);
        assert_eq!(checked_encoded_len(1, usize::MAX, Some(1)), None);
    }

    #[test]
    fn tick_with_dictionary_degenerate_inputs() {
        let mut maximum = vec![0u8; 16];
        maximum[12..14].copy_from_slice(&u16::MAX.to_le_bytes());
        assert_eq!(with_dictionary(&maximum, &[1, 2, 3]), maximum);
        assert_eq!(with_dictionary(b"short", &[1, 2, 3]), b"short");
    }

    #[test]
    fn tick_splice_equals_encode_block() {
        for (rows, columns) in [(0, 0), (1, 1), (4_096, 8)] {
            let tick = data(rows, columns);
            let body = dictionary_body(1, &tick.entries).expect("body");
            let bare = encode_bare(&tick, 1).expect("bare");
            assert_eq!(
                with_dictionary(&bare, &body),
                with_dictionary_block(&tick, 1)
            );
        }
    }

    #[test]
    fn tick_splice_decodes_with_decode_block() {
        let tick = data(1, 1);
        let body = dictionary_body(1, &tick.entries).expect("body");
        let bare = encode_bare(&tick, 1).expect("bare");
        let block = decode_block(&with_dictionary(&bare, &body)).expect("decode");
        assert!(block.dictionary.is_some());
        assert_eq!(block.header.dict_gen, 1);
        assert_eq!(block.rows, tick.rows);
        assert_eq!(block.columns, tick.columns);
    }

    #[test]
    fn tick_bare_decodes_against_held_dictionary() {
        let tick = data(1, 1);
        let body = dictionary_body(1, &tick.entries).expect("body");
        let dictionary = Dictionary::decode(&body, DEFAULT_ACTIVE_MAX).expect("dictionary");
        let bare = encode_bare(&tick, 1).expect("bare");
        let block = decode_block_with(&bare, Some(&dictionary)).expect("held decode");
        assert!(block.dictionary.is_none());
        assert_eq!(block.rows, tick.rows);
    }

    #[test]
    fn tick_header_fields_overwritten() {
        let mut tick = data(1, 1);
        tick.header = BlockHeaderFacts {
            boot_id: [7; 16],
            epoch: 9,
            seq: 11,
            mono_start_ns: 13,
            mono_end_ns: 17,
            boot_ns: 19,
            real_ns: -23,
            dict_gen: 99,
            n_rows: 99,
            dropped: 99,
            flags: 1,
        };
        let block = decode_block(&encode_bare(&tick, 7).expect("bare")).expect("decode");
        assert_eq!(block.header.boot_id, [7; 16]);
        assert_eq!(block.header.epoch, 9);
        assert_eq!(block.header.seq, 11);
        assert_eq!(block.header.mono_start_ns, 13);
        assert_eq!(block.header.mono_end_ns, 17);
        assert_eq!(block.header.boot_ns, 19);
        assert_eq!(block.header.real_ns, -23);
        assert_eq!(block.header.dict_gen, 7);
        assert_eq!(block.header.n_rows, 1);
        assert_eq!(block.header.dropped, 0);
        assert_eq!(block.header.flags, 0);
    }

    #[test]
    fn tick_live_tick_dict_form_built_once() {
        let tick = data(1, 1);
        let body = Arc::<[u8]>::from(dictionary_body(1, &tick.entries).expect("body"));
        let base = Arc::<[u8]>::from(encode_bare(&tick, 1).expect("bare"));
        let live = LiveTick::new(1, Some(Arc::clone(&base)), base, body);
        assert!(!live.dict_built());
        let first = live.dict_form();
        let second = live.dict_form();
        assert!(Arc::ptr_eq(&first, &second));
        assert!(live.dict_built());
        assert!(live.has_bare());
        assert_eq!(live.generation(), 1);
        assert_eq!(
            TickError::Rows.refusal(),
            (Code::Budget, Detail::Pool(Pool::Rows))
        );
        assert_eq!(
            TickError::Invalid.refusal(),
            (Code::Internal, Detail::Empty)
        );
    }

    #[test]
    fn tick_query_block_generation_1() {
        let bounds = BlockBounds {
            max_rows: 4,
            max_block_bytes: u32::MAX,
        };
        let block = query_block(data(1, 1), bounds).expect("query");
        let decoded = decode_block(&block).expect("decode");
        assert_eq!(decoded.header.dict_gen, 1);
        assert!(decoded.dictionary.is_some());
    }
}
