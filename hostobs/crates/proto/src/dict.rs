//! Per-segment dictionaries and bounded generations (plan v4 §B.3, §A.4).
//!
//! A block's rows and columns reference dictionary **indices**, never strings, so one
//! frame carries names once instead of per value. A segment, query reply or exported
//! bundle carries its own complete dictionary, which is what makes a segment self
//! contained (plan v4 §C.5).
//!
//! # Contracts
//!
//! - **Indices never change meaning within `(epoch, dict_gen)`.** [`Dictionary::intern`]
//!   returns the same index for the same entry for the whole life of the generation, and
//!   the generation number is part of every header that uses it.
//! - **Compaction is a new generation.** [`Dictionary::retire_unreachable`] drops entries
//!   that no live segment can reference and rebuilds the table, so indices *do* change —
//!   and therefore the result carries `generation + 1` and must be sent complete before
//!   any value that references it. There is no in-place index reuse inside one
//!   generation: that is precisely how a dictionary silently renames a series.
//! - **Every generation is bounded.** The entry count is capped at
//!   [`DEFAULT_ACTIVE_MAX`] (plan v4 §A.4's `dict_active_max` proposal, 65,536) and each
//!   entry's text at 256 bytes. A dictionary that would exceed the count is refused with
//!   [`ProtoError::DictionaryTooLarge`] instead of growing without a limit.
//! - **Dangling references are typed errors.** [`Dictionary::lookup`] refuses an index
//!   outside the generation; nothing returns a default entry.
//! - **A receiver refuses a block from a generation it has not been sent**
//!   ([`Dictionary::accepts_generation`]), so a block can never be decoded against the
//!   wrong names.
//!
//! # Kinds
//!
//! The four entry kinds of §B.3 are entity incarnations (`inc` plus its display label),
//! metrics (with unit, kind, scope, source and permitted aggregation — the facts a
//! consumer needs to read a column correctly), enrichment labels and free strings.

use core::fmt;
use std::collections::HashMap;

use crate::error::ProtoError;
use crate::read::Cursor;
use crate::write::Writer;

/// Largest dictionary entry text in bytes (plan v4 §B.3).
pub const MAX_ENTRY_TEXT: usize = 256;

/// The default bound on entries in one generation (plan v4 §A.4 `dict_active_max`).
pub const DEFAULT_ACTIVE_MAX: usize = 65_536;

/// What an entry names (plan v4 §B.3).
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
#[repr(u8)]
pub enum EntryKind {
    /// A cgroup incarnation: the identity, plus a path/label for humans.
    Entity = 0,
    /// A metric, with the facts a reader needs.
    Metric = 1,
    /// An enrichment label bound to an incarnation.
    Label = 2,
    /// Any other string (a span name, an issuer, a free tag).
    Free = 3,
}

/// The stable stored code of a metric value kind (plan v4 §B.2's column types).
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
#[repr(u8)]
pub enum MetricValueType {
    /// Raw `u64` (counters and integer gauges).
    U64 = 1,
    /// `i64` (signed gauges).
    I64 = 2,
    /// `f64` (derived and fractional values).
    F64 = 3,
}

/// The stable stored code of a metric's arithmetic kind (§B.2 `kind`).
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
#[repr(u8)]
pub enum MetricKind {
    /// A point-in-time gauge.
    Gauge = 0,
    /// A cumulative counter, stored raw (§C.3).
    Counter = 1,
    /// A closed state code.
    State = 2,
}

/// The metric's unit, as the dictionary declares it (plan v4 §A.3/§B.3).
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
#[repr(u8)]
pub enum MetricUnit {
    /// Dimensionless.
    None = 0,
    /// Microseconds.
    Microseconds = 1,
    /// Milliseconds.
    Milliseconds = 2,
    /// Nanoseconds.
    Nanoseconds = 3,
    /// Bytes.
    Bytes = 4,
    /// A count of objects.
    Count = 5,
    /// A ratio or percentage.
    Ratio = 6,
    /// Volts.
    Volts = 7,
    /// Degrees Celsius.
    Celsius = 8,
}

/// The scope a metric is measured in (plan v4 §C.3).
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
#[repr(u8)]
pub enum MetricScope {
    /// A host-wide fact.
    Host = 0,
    /// One cgroup (inclusive of its descendants).
    Cgroup = 1,
    /// One process.
    Process = 2,
    /// The probe itself.
    Daemon = 3,
}

/// The reader a metric comes from (plan v4 §A.3).
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
#[repr(u8)]
pub enum MetricSource {
    /// A cgroupfs file.
    CgroupFs = 0,
    /// A `/proc` file.
    ProcFs = 1,
    /// A `/sys` file.
    SysFs = 2,
    /// The probe's own bookkeeping.
    Probe = 3,
    /// Derived by `model` from another metric.
    Derived = 4,
}

/// An aggregation a stored series may use (§B.2 `agg`, §B.3 permitted aggregation).
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
#[repr(u8)]
pub enum Aggregation {
    /// The raw reading.
    Raw = 0,
    /// The value at the end of a bucket.
    Last = 1,
    /// A counter difference over the covered interval.
    Delta = 2,
    /// A sample-weighted mean.
    Mean = 3,
    /// A bucket minimum.
    Min = 4,
    /// A bucket maximum.
    Max = 5,
}

/// Every aggregation, in code order.
pub const ALL_AGGREGATIONS: [Aggregation; 6] = [
    Aggregation::Raw,
    Aggregation::Last,
    Aggregation::Delta,
    Aggregation::Mean,
    Aggregation::Min,
    Aggregation::Max,
];

impl Aggregation {
    /// The bit this aggregation uses in a metric entry's permitted-aggregation mask.
    #[must_use]
    pub const fn bit(self) -> u16 {
        1 << (self as u16)
    }

    /// Decode the stored `agg` byte of a history bucket.
    ///
    /// # Errors
    ///
    /// [`ProtoError::UnknownAggregation`].
    pub const fn from_code(code: u8) -> Result<Aggregation, ProtoError> {
        match code {
            0 => Ok(Aggregation::Raw),
            1 => Ok(Aggregation::Last),
            2 => Ok(Aggregation::Delta),
            3 => Ok(Aggregation::Mean),
            4 => Ok(Aggregation::Min),
            5 => Ok(Aggregation::Max),
            other => Err(ProtoError::UnknownAggregation(other)),
        }
    }

    /// The stable lower-case name used by fixtures and operator output.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Aggregation::Raw => "raw",
            Aggregation::Last => "last",
            Aggregation::Delta => "delta",
            Aggregation::Mean => "mean",
            Aggregation::Min => "min",
            Aggregation::Max => "max",
        }
    }
}

impl MetricValueType {
    /// Decode a column's `type` byte.
    ///
    /// # Errors
    ///
    /// [`ProtoError::UnknownValueType`].
    pub const fn from_code(code: u8) -> Result<MetricValueType, ProtoError> {
        match code {
            1 => Ok(MetricValueType::U64),
            2 => Ok(MetricValueType::I64),
            3 => Ok(MetricValueType::F64),
            other => Err(ProtoError::UnknownValueType(other)),
        }
    }

    /// The stable lower-case name used by fixtures.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            MetricValueType::U64 => "u64",
            MetricValueType::I64 => "i64",
            MetricValueType::F64 => "f64",
        }
    }
}

impl MetricKind {
    /// Decode a column's `kind` byte.
    ///
    /// # Errors
    ///
    /// [`ProtoError::UnknownColumnKind`].
    pub const fn from_code(code: u8) -> Result<MetricKind, ProtoError> {
        match code {
            0 => Ok(MetricKind::Gauge),
            1 => Ok(MetricKind::Counter),
            2 => Ok(MetricKind::State),
            other => Err(ProtoError::UnknownColumnKind(other)),
        }
    }

    /// The stable lower-case name used by fixtures.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            MetricKind::Gauge => "gauge",
            MetricKind::Counter => "counter",
            MetricKind::State => "state",
        }
    }

    /// The aggregation mask this kind permits (§B.3).
    #[must_use]
    pub const fn aggregation_mask(self) -> u16 {
        match self {
            MetricKind::Counter => {
                Aggregation::Raw.bit() | Aggregation::Last.bit() | Aggregation::Delta.bit()
            }
            MetricKind::Gauge => {
                Aggregation::Last.bit()
                    | Aggregation::Mean.bit()
                    | Aggregation::Min.bit()
                    | Aggregation::Max.bit()
            }
            MetricKind::State => Aggregation::Last.bit(),
        }
    }
}

impl MetricUnit {
    /// The stable lower-case name used by fixtures.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            MetricUnit::None => "none",
            MetricUnit::Microseconds => "usec",
            MetricUnit::Milliseconds => "msec",
            MetricUnit::Nanoseconds => "nsec",
            MetricUnit::Bytes => "bytes",
            MetricUnit::Count => "count",
            MetricUnit::Ratio => "ratio",
            MetricUnit::Volts => "volts",
            MetricUnit::Celsius => "celsius",
        }
    }

    /// Decode a stored unit code.
    ///
    /// # Errors
    ///
    /// [`ProtoError::DictionaryEntryUnknownCode`] carries the entry index; a unit outside
    /// the table is a corrupt dictionary entry.
    pub const fn from_code(code: u8, index: usize) -> Result<MetricUnit, ProtoError> {
        match code {
            0 => Ok(MetricUnit::None),
            1 => Ok(MetricUnit::Microseconds),
            2 => Ok(MetricUnit::Milliseconds),
            3 => Ok(MetricUnit::Nanoseconds),
            4 => Ok(MetricUnit::Bytes),
            5 => Ok(MetricUnit::Count),
            6 => Ok(MetricUnit::Ratio),
            7 => Ok(MetricUnit::Volts),
            8 => Ok(MetricUnit::Celsius),
            _ => Err(ProtoError::DictionaryEntryUnknownCode { index, code }),
        }
    }
}

impl MetricScope {
    /// The stable lower-case name used by fixtures.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            MetricScope::Host => "host",
            MetricScope::Cgroup => "cgroup",
            MetricScope::Process => "process",
            MetricScope::Daemon => "daemon",
        }
    }

    /// Decode a stored scope code.
    ///
    /// # Errors
    ///
    /// [`ProtoError::DictionaryEntryUnknownCode`].
    pub const fn from_code(code: u8, index: usize) -> Result<MetricScope, ProtoError> {
        match code {
            0 => Ok(MetricScope::Host),
            1 => Ok(MetricScope::Cgroup),
            2 => Ok(MetricScope::Process),
            3 => Ok(MetricScope::Daemon),
            _ => Err(ProtoError::DictionaryEntryUnknownCode { index, code }),
        }
    }
}

impl MetricSource {
    /// The stable lower-case name used by fixtures.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            MetricSource::CgroupFs => "cgroupfs",
            MetricSource::ProcFs => "procfs",
            MetricSource::SysFs => "sysfs",
            MetricSource::Probe => "probe",
            MetricSource::Derived => "derived",
        }
    }

    /// Decode a stored source code.
    ///
    /// # Errors
    ///
    /// [`ProtoError::DictionaryEntryUnknownCode`].
    pub const fn from_code(code: u8, index: usize) -> Result<MetricSource, ProtoError> {
        match code {
            0 => Ok(MetricSource::CgroupFs),
            1 => Ok(MetricSource::ProcFs),
            2 => Ok(MetricSource::SysFs),
            3 => Ok(MetricSource::Probe),
            4 => Ok(MetricSource::Derived),
            _ => Err(ProtoError::DictionaryEntryUnknownCode { index, code }),
        }
    }
}

/// The facts a metric entry carries (§B.3).
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct MetricFacts {
    /// The metric's unit.
    pub unit: MetricUnit,
    /// The metric's arithmetic kind.
    pub kind: MetricKind,
    /// The scope it is measured in.
    pub scope: MetricScope,
    /// The reader it comes from.
    pub source: MetricSource,
}

impl MetricFacts {
    /// The permitted aggregations of this metric as a mask, derived from its kind.
    #[must_use]
    pub const fn aggregation_mask(self) -> u16 {
        self.kind.aggregation_mask()
    }
}

/// One dictionary entry.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Entry {
    /// An incarnation and its display label.
    Entity {
        /// The incarnation id.
        inc: u64,
        /// A label for humans (a path, or empty).
        label: String,
    },
    /// A metric with its facts.
    Metric {
        /// The facts a reader needs.
        facts: MetricFacts,
        /// The metric's name.
        name: String,
    },
    /// An enrichment label.
    Label {
        /// The label's kind code.
        kind: u8,
        /// The label's source code.
        source: u8,
        /// The label's text.
        text: String,
    },
    /// Any other string.
    Free {
        /// The string.
        text: String,
    },
}

impl Entry {
    /// The entry's kind.
    #[must_use]
    pub const fn kind(&self) -> EntryKind {
        match self {
            Entry::Entity { .. } => EntryKind::Entity,
            Entry::Metric { .. } => EntryKind::Metric,
            Entry::Label { .. } => EntryKind::Label,
            Entry::Free { .. } => EntryKind::Free,
        }
    }

    /// The entry's text (empty for an entity without a label).
    #[must_use]
    pub fn text(&self) -> &str {
        match self {
            Entry::Entity { label, .. } => label,
            Entry::Metric { name, .. } => name,
            Entry::Label { text, .. } => text,
            Entry::Free { text } => text,
        }
    }

    /// The stored code of the entry kind.
    #[must_use]
    pub const fn kind_code(&self) -> u8 {
        self.kind() as u8
    }

    fn validate(&self, index: usize) -> Result<(), ProtoError> {
        let text = self.text();
        if text.len() > MAX_ENTRY_TEXT {
            return Err(ProtoError::DictionaryEntryTooLong {
                index,
                len: text.len(),
            });
        }
        for (offset, byte) in text.bytes().enumerate() {
            if byte < 0x20 || byte == 0x7f {
                return Err(ProtoError::DictionaryEntryControl { index, offset });
            }
        }
        Ok(())
    }
}

impl fmt::Display for Entry {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Entry::Entity { inc, label } => write!(f, "entity:{inc}:{label}"),
            Entry::Metric { facts, name } => write!(f, "metric:{name}:{:?}", facts.kind),
            Entry::Label { kind, source, text } => write!(f, "label:{kind}:{source}:{text}"),
            Entry::Free { text } => write!(f, "free:{text}"),
        }
    }
}

/// The key an entry is de-duplicated by: its identity, not its formatting.
#[derive(Clone, Debug, Eq, PartialEq, Hash)]
enum Key {
    Entity(u64),
    Metric(String),
    Label(u8, u8, String),
    Free(String),
}

impl Entry {
    fn key(&self) -> Key {
        match self {
            Entry::Entity { inc, .. } => Key::Entity(*inc),
            Entry::Metric { name, .. } => Key::Metric(name.clone()),
            Entry::Label { kind, source, text } => Key::Label(*kind, *source, text.clone()),
            Entry::Free { text } => Key::Free(text.clone()),
        }
    }
}

/// A dictionary generation: a bounded, index-stable table of entries.
///
/// Two dictionaries are equal when they hold the same entries with the same generation;
/// the internal index is derived state and is not compared.
#[derive(Clone, Debug)]
pub struct Dictionary {
    generation: u32,
    entries: Vec<Entry>,
    index: HashMap<Key, u32>,
    max_entries: usize,
}

impl Default for Dictionary {
    fn default() -> Self {
        Dictionary::new(0)
    }
}

impl Dictionary {
    /// An empty generation `generation` with the default entry bound.
    #[must_use]
    pub fn new(generation: u32) -> Dictionary {
        Dictionary::with_capacity(generation, DEFAULT_ACTIVE_MAX)
    }

    /// An empty generation with an explicit entry bound.
    #[must_use]
    pub fn with_capacity(generation: u32, max_entries: usize) -> Dictionary {
        Dictionary {
            generation,
            entries: Vec::new(),
            index: HashMap::new(),
            max_entries,
        }
    }

    /// The entries and their generation; the lookup index is derived state.
    fn same_as(&self, other: &Dictionary) -> bool {
        self.generation == other.generation && self.entries == other.entries
    }

    /// The generation number that must be sent with every block using this table.
    #[must_use]
    pub const fn generation(&self) -> u32 {
        self.generation
    }

    /// The number of entries.
    #[must_use]
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// True when the generation holds nothing.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// The entry bound of this generation.
    #[must_use]
    pub const fn max_entries(&self) -> usize {
        self.max_entries
    }

    /// Intern an entry and return its stable index within this generation.
    ///
    /// # Errors
    ///
    /// [`ProtoError::DictionaryEntryTooLong`], [`ProtoError::DictionaryEntryControl`] or
    /// [`ProtoError::DictionaryTooLarge`].
    pub fn intern(&mut self, entry: Entry) -> Result<u32, ProtoError> {
        let key = entry.key();
        if let Some(&index) = self.index.get(&key) {
            // The same identity can only carry one entry per generation: a second
            // declaration of the same metric name with other facts is refused rather
            // than silently reusing the index with different meanings.
            let len = self.entries.len();
            let existing = self
                .entries
                .get(index as usize)
                .ok_or(ProtoError::DanglingDictionaryRef { index, len })?;
            if existing == &entry {
                return Ok(index);
            }
            // An incarnation's label is not its identity (plan v4 §C.1): the same `inc` with
            // a newer label updates the label and keeps its index. Any other entry whose
            // identity matches but whose facts differ is a caller defect and is refused.
            let relabel = matches!(
                (existing, &entry),
                (Entry::Entity { inc: existing_inc, .. }, Entry::Entity { inc, .. })
                    if *existing_inc == *inc
            );
            if !relabel {
                return Err(ProtoError::ConflictingColumn { metric: index });
            }
            // The index was just proved in bounds, so the slot exists; a missing slot here
            // would mean the table changed under this call, which its type forbids.
            if let Some(slot) = self.entries.get_mut(index as usize) {
                *slot = entry;
            }
            return Ok(index);
        }
        entry.validate(self.entries.len())?;
        if self.entries.len() >= self.max_entries {
            return Err(ProtoError::DictionaryTooLarge {
                count: self.entries.len() + 1,
                max: self.max_entries,
            });
        }
        let index = u32::try_from(self.entries.len())
            .map_err(|_| ProtoError::RowCountOverflow(self.entries.len() as u64))?;
        self.entries.push(entry);
        self.index.insert(key, index);
        Ok(index)
    }

    /// The entry at `index`.
    ///
    /// # Errors
    ///
    /// [`ProtoError::DanglingDictionaryRef`]: an index outside the generation is refused,
    /// never answered with a placeholder entry.
    pub fn lookup(&self, index: u32) -> Result<&Entry, ProtoError> {
        self.entries
            .get(index as usize)
            .ok_or(ProtoError::DanglingDictionaryRef {
                index,
                len: self.entries.len(),
            })
    }

    /// The entries, in index order.
    #[must_use]
    pub fn entries(&self) -> &[Entry] {
        &self.entries
    }

    /// True when this generation can decode a block that declares `generation`.
    #[must_use]
    pub const fn accepts_generation(&self, generation: u32) -> bool {
        generation == self.generation
    }

    /// Check that a block declaring `generation` can be decoded against this table.
    ///
    /// # Errors
    ///
    /// [`ProtoError::StaleDictionaryGeneration`].
    pub fn require_generation(&self, generation: u32) -> Result<(), ProtoError> {
        if self.accepts_generation(generation) {
            Ok(())
        } else {
            Err(ProtoError::StaleDictionaryGeneration {
                needed: generation,
                held: self.generation,
            })
        }
    }

    /// Replace the generation number (a new generation is a new epoch of meaning).
    ///
    /// # Errors
    ///
    /// [`ProtoError::GenerationExhausted`].
    pub fn advance_generation(&mut self) -> Result<u32, ProtoError> {
        let next = self
            .generation
            .checked_add(1)
            .ok_or(ProtoError::GenerationExhausted)?;
        self.generation = next;
        Ok(next)
    }

    /// Build the next generation, keeping only entries with a reachable index.
    ///
    /// `reachable` holds the indices some live segment still references. Returned entries
    /// are renumbered in old index order, so the mapping from an old index to a new one is
    /// `position in keep order`; the second return value records it (`None` for a dropped
    /// entry). The result's generation is `generation + 1`.
    ///
    /// # Errors
    ///
    /// [`ProtoError::GenerationExhausted`].
    pub fn retire_unreachable(
        &self,
        reachable: &[u32],
    ) -> Result<(Dictionary, Vec<Option<u32>>), ProtoError> {
        let generation = self
            .generation
            .checked_add(1)
            .ok_or(ProtoError::GenerationExhausted)?;
        let mut next = Dictionary::with_capacity(generation, self.max_entries);
        let mut removed = Vec::with_capacity(self.entries.len());
        for (old_index, entry) in self.entries.iter().enumerate() {
            let old = u32::try_from(old_index)
                .map_err(|_| ProtoError::RowCountOverflow(old_index as u64))?;
            if reachable.contains(&old) {
                let new_index = next.intern(entry.clone())?;
                removed.push(Some(new_index));
            } else {
                removed.push(None);
            }
        }
        Ok((next, removed))
    }

    /// The canonical DICT body: generation, count, entries, padded to eight bytes.
    ///
    /// # Errors
    ///
    /// [`ProtoError::DictionaryEntryTooLong`] or [`ProtoError::DictionaryEntryControl`]
    /// if an entry was mutated into an invalid shape after interning.
    pub fn encode(&self) -> Result<Vec<u8>, ProtoError> {
        let mut writer = Writer::with_capacity(8 + self.entries.len() * 8);
        writer.u32(self.generation);
        writer.u32(
            u32::try_from(self.entries.len())
                .map_err(|_| ProtoError::RowCountOverflow(self.entries.len() as u64))?,
        );
        for (index, entry) in self.entries.iter().enumerate() {
            entry.validate(index)?;
            writer.u8(entry.kind_code());
            writer.u8(0);
            writer.u16(u16::try_from(entry.text().len()).map_err(|_| ProtoError::LengthOverflow)?);
            match entry {
                Entry::Entity { inc, .. } => writer.u64(*inc),
                Entry::Metric { facts, .. } => {
                    writer.u8(facts.unit as u8);
                    writer.u8(facts.kind as u8);
                    writer.u8(facts.scope as u8);
                    writer.u8(facts.source as u8);
                    writer.u16(facts.aggregation_mask());
                }
                Entry::Label { kind, source, .. } => {
                    writer.u8(*kind);
                    writer.u8(*source);
                }
                Entry::Free { .. } => {}
            }
            writer.bytes(entry.text().as_bytes());
        }
        writer.pad_to(8);
        Ok(writer.finish())
    }

    /// Encode an exact list of entries, without the live table's deduplication.
    ///
    /// This exists for tests and fixtures that must express a wire list a live table would
    /// never build (a repeated identity, for example): it writes `entries` at their
    /// positions verbatim, which is what a sender does and what a decoder must reproduce.
    ///
    /// # Errors
    ///
    /// [`ProtoError::DictionaryEntryTooLong`] or [`ProtoError::DictionaryEntryControl`] for
    /// an entry that cannot be written, and [`ProtoError::LengthOverflow`] for an oversized
    /// count.
    pub fn encode_with(&self, entries: &[Entry]) -> Result<Vec<u8>, ProtoError> {
        let mut writer = Writer::with_capacity(8 + entries.len() * 8);
        writer.u32(self.generation);
        writer.u32(
            u32::try_from(entries.len())
                .map_err(|_| ProtoError::RowCountOverflow(entries.len() as u64))?,
        );
        for (index, entry) in entries.iter().enumerate() {
            entry.validate(index)?;
            writer.u8(entry.kind_code());
            writer.u8(0);
            writer.u16(u16::try_from(entry.text().len()).map_err(|_| ProtoError::LengthOverflow)?);
            match entry {
                Entry::Entity { inc, .. } => writer.u64(*inc),
                Entry::Metric { facts, .. } => {
                    writer.u8(facts.unit as u8);
                    writer.u8(facts.kind as u8);
                    writer.u8(facts.scope as u8);
                    writer.u8(facts.source as u8);
                    writer.u16(facts.aggregation_mask());
                }
                Entry::Label { kind, source, .. } => {
                    writer.u8(*kind);
                    writer.u8(*source);
                }
                Entry::Free { .. } => {}
            }
            writer.bytes(entry.text().as_bytes());
        }
        writer.pad_to(8);
        Ok(writer.finish())
    }

    /// Decode a canonical DICT body of exactly `body` bytes.
    ///
    /// # Errors
    ///
    /// Every malformed body is a typed refusal: a truncated entry, an unknown kind or
    /// field code, over-long or non-UTF-8 text, a non-zero pad byte, a count that
    /// disagrees with the body, or trailing bytes.
    pub fn decode(body: &[u8], max_entries: usize) -> Result<Dictionary, ProtoError> {
        let mut cursor = Cursor::new(body, "dictionary section");
        let generation = cursor.u32()?;
        let count = cursor.u32()?;
        let count =
            usize::try_from(count).map_err(|_| ProtoError::RowCountOverflow(u64::from(count)))?;
        if count > max_entries {
            return Err(ProtoError::DictionaryTooLarge {
                count,
                max: max_entries,
            });
        }
        let mut dictionary = Dictionary::with_capacity(generation, max_entries);
        for index in 0..count {
            let kind_code = cursor.u8()?;
            let flags = cursor.u8()?;
            if flags != 0 {
                return Err(ProtoError::DictionaryEntryReserved { index, flags });
            }
            let len = usize::from(cursor.u16()?);
            if len > MAX_ENTRY_TEXT {
                return Err(ProtoError::DictionaryEntryTooLong { index, len });
            }
            // Each kind reads its fixed fields and then builds the entry with the text
            // that follows them, so there is no placeholder entry and no unreachable
            // "no kind matched" arm after the fixed fields are read.
            let entry = match kind_code {
                0 => {
                    let inc = cursor.u64()?;
                    Entry::Entity {
                        inc,
                        label: String::new(),
                    }
                }
                1 => {
                    let unit = MetricUnit::from_code(cursor.u8()?, index)?;
                    let kind = MetricKind::from_code(cursor.u8()?)?;
                    let scope = MetricScope::from_code(cursor.u8()?, index)?;
                    let source = MetricSource::from_code(cursor.u8()?, index)?;
                    let mask = cursor.u16()?;
                    // The mask is *derived* from the kind (§B.3), so it must equal the
                    // derived mask exactly: an extra bit and a missing bit are both a
                    // corrupt entry. Accepting a subset would widen it again on re-encode
                    // and silently drop the sender's restriction, and the independent
                    // Python decoder refuses both the same way.
                    let expected_mask = kind.aggregation_mask();
                    if mask != expected_mask {
                        return Err(ProtoError::DictionaryEntryMask {
                            index,
                            mask,
                            expected: expected_mask,
                        });
                    }
                    Entry::Metric {
                        facts: MetricFacts {
                            unit,
                            kind,
                            scope,
                            source,
                        },
                        name: String::new(),
                    }
                }
                2 => {
                    let kind = cursor.u8()?;
                    let source = cursor.u8()?;
                    Entry::Label {
                        kind,
                        source,
                        text: String::new(),
                    }
                }
                3 => Entry::Free {
                    text: String::new(),
                },
                code => return Err(ProtoError::UnknownDictionaryKind { index, code }),
            };
            let text = cursor.take_bytes(len)?;
            let text = core::str::from_utf8(text)
                .map_err(|_| ProtoError::DictionaryEntryNotUtf8 { index })?
                .to_owned();
            let entry = match entry {
                Entry::Entity { inc, .. } => Entry::Entity { inc, label: text },
                Entry::Metric { facts, .. } => Entry::Metric { facts, name: text },
                Entry::Label { kind, source, .. } => Entry::Label { kind, source, text },
                Entry::Free { .. } => Entry::Free { text },
            };
            // A wire list is the sender's numbering: entry `index` must *be* at index
            // `index`. `intern` de-duplicates and re-labels (both correct for a live
            // table), so a body that repeats an identity would come back with fewer
            // entries and every later index shifted. That is a corrupt frame, not a table
            // to normalise: refuse it instead of renumbering the sender's data.
            let interned = match dictionary.intern(entry) {
                Err(ProtoError::ConflictingColumn { .. }) => {
                    return Err(ProtoError::DuplicateDictionaryEntry { index });
                }
                other => other?,
            };
            let position =
                u32::try_from(index).map_err(|_| ProtoError::RowCountOverflow(index as u64))?;
            if interned != position {
                return Err(ProtoError::DuplicateDictionaryEntry { index });
            }
        }
        cursor.pad_to_end(8)?;
        Ok(dictionary)
    }
}

impl PartialEq for Dictionary {
    fn eq(&self, other: &Dictionary) -> bool {
        self.same_as(other)
    }
}

impl Eq for Dictionary {}

#[cfg(test)]
mod tests {
    use super::{
        Aggregation, Dictionary, Entry, EntryKind, MetricFacts, MetricKind, MetricScope,
        MetricSource, MetricUnit, MetricValueType, DEFAULT_ACTIVE_MAX, MAX_ENTRY_TEXT,
    };
    use crate::error::ProtoError;
    use crate::quality::Quality;

    fn facts() -> MetricFacts {
        MetricFacts {
            unit: MetricUnit::Microseconds,
            kind: MetricKind::Counter,
            scope: MetricScope::Cgroup,
            source: MetricSource::CgroupFs,
        }
    }

    fn metric(name: &str) -> Entry {
        Entry::Metric {
            facts: facts(),
            name: name.to_string(),
        }
    }

    #[test]
    fn interning_is_stable_deduplicated_and_generation_scoped() {
        let mut dictionary = Dictionary::new(7);
        assert_eq!(dictionary.generation(), 7);
        let first = dictionary.intern(metric("cpu.usage_usec")).expect("intern");
        let second = dictionary.intern(metric("mem.current")).expect("intern");
        assert_eq!(first, 0);
        assert_eq!(second, 1);
        assert_eq!(
            dictionary.intern(metric("cpu.usage_usec")),
            Ok(0),
            "the same name keeps its index"
        );
        assert_eq!(
            dictionary.intern(Entry::Entity {
                inc: 3,
                label: "/docker/abc".into()
            }),
            Ok(2)
        );
        assert_eq!(
            dictionary.intern(Entry::Entity {
                inc: 3,
                label: "/docker/xyz".into()
            }),
            Ok(2),
            "identity is the inc, not the label"
        );
        assert_eq!(
            dictionary.intern(Entry::Free {
                text: "lane".into()
            }),
            Ok(3)
        );
        assert_eq!(dictionary.len(), 4);
        assert!(!dictionary.is_empty());
        assert_eq!(dictionary.lookup(0).map(Entry::text), Ok("cpu.usage_usec"));
        assert_eq!(
            dictionary.lookup(9),
            Err(ProtoError::DanglingDictionaryRef { index: 9, len: 4 })
        );
        assert!(dictionary.accepts_generation(7));
        assert_eq!(
            dictionary.require_generation(6),
            Err(ProtoError::StaleDictionaryGeneration { needed: 6, held: 7 })
        );
        let next = dictionary.advance_generation().expect("advance");
        assert_eq!(next, 8);
        assert_eq!(dictionary.generation(), 8);
    }

    #[test]
    fn the_same_key_with_different_facts_is_refused() {
        let mut dictionary = Dictionary::new(1);
        assert!(dictionary.intern(metric("host.ctxt")).is_ok());
        let mut other = facts();
        other.kind = MetricKind::Gauge;
        assert_eq!(
            dictionary.intern(Entry::Metric {
                facts: other,
                name: "host.ctxt".into()
            }),
            Err(ProtoError::ConflictingColumn { metric: 0 }),
            "one name, two meanings, one generation: refused"
        );
    }

    #[test]
    fn entries_are_bounded_in_text_and_count() {
        let mut dictionary = Dictionary::with_capacity(2, 2);
        assert_eq!(
            dictionary.intern(Entry::Free {
                text: "x".repeat(MAX_ENTRY_TEXT + 1)
            }),
            Err(ProtoError::DictionaryEntryTooLong {
                index: 0,
                len: MAX_ENTRY_TEXT + 1
            })
        );
        assert!(
            dictionary.entries().is_empty(),
            "a refused entry is not stored"
        );
        assert_eq!(
            dictionary.intern(Entry::Free {
                text: "a\nb".into()
            }),
            Err(ProtoError::DictionaryEntryControl {
                index: 0,
                offset: 1
            })
        );
        assert!(dictionary
            .intern(Entry::Free { text: "one".into() })
            .is_ok());
        assert!(dictionary
            .intern(Entry::Free { text: "two".into() })
            .is_ok());
        assert_eq!(
            dictionary.intern(Entry::Free {
                text: "three".into()
            }),
            Err(ProtoError::DictionaryTooLarge { count: 3, max: 2 })
        );
        assert_eq!(dictionary.max_entries(), 2);
        assert_eq!(super::Dictionary::new(0).max_entries(), DEFAULT_ACTIVE_MAX);
    }

    #[test]
    fn a_new_generation_compacts_and_renumbers_while_dropping_unreachable_entries() {
        let mut dictionary = Dictionary::new(3);
        let a = dictionary.intern(metric("cpu.usage_usec")).expect("intern");
        let b = dictionary
            .intern(Entry::Entity {
                inc: 11,
                label: "/a".into(),
            })
            .expect("intern");
        let c = dictionary
            .intern(Entry::Free { text: "old".into() })
            .expect("intern");
        let d = dictionary
            .intern(Entry::Free {
                text: "live".into(),
            })
            .expect("intern");
        assert_eq!((a, b, c, d), (0, 1, 2, 3));
        let (next, mapping) = dictionary.retire_unreachable(&[0, 3]).expect("retire");
        assert_eq!(next.generation(), 4, "compaction is a new generation");
        assert_eq!(next.len(), 2);
        assert_eq!(mapping, vec![Some(0), None, None, Some(1)]);
        assert_eq!(next.lookup(0).map(Entry::text), Ok("cpu.usage_usec"));
        assert_eq!(next.lookup(1).map(Entry::text), Ok("live"));
        assert_eq!(
            next.lookup(2),
            Err(ProtoError::DanglingDictionaryRef { index: 2, len: 2 }),
            "a retired entry is gone, its old index is not reinterpreted"
        );
        assert!(
            dictionary.accepts_generation(3),
            "retirement does not mutate the old table"
        );
        assert!(!next.accepts_generation(3));
    }

    #[test]
    fn retiring_everything_keeps_an_empty_generation_with_a_new_number() {
        let mut dictionary = Dictionary::new(5);
        dictionary.intern(metric("cpu.usage_usec")).expect("intern");
        let (next, mapping) = dictionary.retire_unreachable(&[]).expect("retire");
        assert!(next.is_empty());
        assert_eq!(next.generation(), 6);
        assert_eq!(mapping, vec![None]);
        let (again, _) = dictionary.retire_unreachable(&[]).expect("retire");
        assert_eq!(again.generation(), 6);
    }

    #[test]
    fn a_generation_cannot_advance_past_the_end_of_its_number_space() {
        let mut dictionary = Dictionary::new(u32::MAX);
        assert_eq!(
            dictionary.advance_generation(),
            Err(ProtoError::GenerationExhausted)
        );
        assert_eq!(
            dictionary.retire_unreachable(&[]),
            Err(ProtoError::GenerationExhausted)
        );
        assert_eq!(
            dictionary.generation(),
            u32::MAX,
            "a refusal never changes the generation"
        );
    }

    #[test]
    fn encoding_and_decoding_a_dictionary_is_byte_exact() {
        let mut dictionary = Dictionary::new(9);
        dictionary
            .intern(Entry::Entity {
                inc: 1,
                label: "/system.slice".into(),
            })
            .expect("intern");
        dictionary.intern(metric("cpu.usage_usec")).expect("intern");
        dictionary
            .intern(Entry::Label {
                kind: 1,
                source: 1,
                text: "web-1".into(),
            })
            .expect("intern");
        dictionary
            .intern(Entry::Free {
                text: "unicode: \u{00e4}\u{00f6}".into(),
            })
            .expect("intern");
        let bytes = dictionary.encode().expect("encode");
        assert_eq!(bytes.len() % 8, 0, "bodies are padded to eight bytes");
        let decoded = Dictionary::decode(&bytes, DEFAULT_ACTIVE_MAX).expect("decode");
        assert_eq!(decoded, dictionary);
        let reencoded = decoded.encode().expect("encode again");
        assert_eq!(
            reencoded, bytes,
            "decode/encode is the identity on canonical bytes"
        );
    }

    #[test]
    fn decode_keeps_every_wire_position_and_never_shortens_the_table() {
        // The decoder's job is to reproduce the sender's numbering exactly. A body mixing
        // all four entry kinds must come back with the same count and the same entry at
        // every index — that is the property the duplicate check protects.
        let entries = vec![
            Entry::Entity {
                inc: 5,
                label: "/a".into(),
            },
            Entry::Metric {
                facts: facts(),
                name: "cpu.usage_usec".into(),
            },
            Entry::Label {
                kind: 1,
                source: 2,
                text: "web".into(),
            },
            Entry::Free { text: "/b".into() },
        ];
        let body = Dictionary::new(9).encode_with(&entries).expect("encode");
        let decoded = Dictionary::decode(&body, DEFAULT_ACTIVE_MAX).expect("decode");
        assert_eq!(decoded.len(), 4, "no entry is merged, dropped or added");
        assert_eq!(decoded.generation(), 9);
        for (index, entry) in entries.iter().enumerate() {
            let expected_index = u32::try_from(index).expect("index");
            assert_eq!(
                decoded.lookup(expected_index),
                Ok(entry),
                "entry {index} must stay at index {index}"
            );
        }
        // ...and the decoded table re-encodes to the same bytes, which is the encoder's
        // half of the same contract.
        assert_eq!(decoded.encode().expect("re-encode"), body);
    }

    #[test]
    fn a_corrupt_dictionary_body_is_refused_every_way_it_can_be_corrupt() {
        let mut dictionary = Dictionary::new(2);
        dictionary
            .intern(Entry::Entity {
                inc: 4,
                label: "abc".into(),
            })
            .expect("intern");
        let bytes = dictionary.encode().expect("encode");

        assert_eq!(
            Dictionary::decode(&bytes[..4], DEFAULT_ACTIVE_MAX).map(|_| ()),
            Err(ProtoError::Truncated {
                context: "dictionary section",
                need: 8,
                have: 4
            })
        );
        // A count that disagrees with the body.
        let mut bad = bytes.clone();
        bad[4..8].copy_from_slice(&2u32.to_le_bytes());
        assert!(Dictionary::decode(&bad, DEFAULT_ACTIVE_MAX).is_err());
        // An unknown entry kind.
        let mut bad = bytes.clone();
        bad[8] = 9;
        assert_eq!(
            Dictionary::decode(&bad, DEFAULT_ACTIVE_MAX).map(|_| ()),
            Err(ProtoError::UnknownDictionaryKind { index: 0, code: 9 })
        );
        // Non-zero entry flags.
        let mut bad = bytes.clone();
        bad[9] = 1;
        assert!(Dictionary::decode(&bad, DEFAULT_ACTIVE_MAX).is_err());
        // A text length that runs past the body.
        let mut bad = bytes.clone();
        bad[10..12].copy_from_slice(&200u16.to_le_bytes());
        assert!(Dictionary::decode(&bad, DEFAULT_ACTIVE_MAX).is_err());
        // An over-long declared text.
        let mut bad = bytes.clone();
        bad[10..12].copy_from_slice(&257u16.to_le_bytes());
        assert_eq!(
            Dictionary::decode(&bad, DEFAULT_ACTIVE_MAX).map(|_| ()),
            Err(ProtoError::DictionaryEntryTooLong { index: 0, len: 257 })
        );
        // Non-zero padding.
        let mut bad = bytes.clone();
        let last = bad.len() - 1;
        bad[last] = 1;
        assert!(
            Dictionary::decode(&bad, DEFAULT_ACTIVE_MAX).is_err(),
            "a non-zero pad byte is a corrupt body"
        );
        // A count above the receiver's bound.
        let mut bad = bytes.clone();
        bad[4..8].copy_from_slice(&3u32.to_le_bytes());
        assert!(Dictionary::decode(&bad, 2).is_err());
        // Trailing bytes after the alignment padding are refused, not ignored.
        let mut bad = bytes.clone();
        bad.extend_from_slice(&[0; 8]);
        assert!(
            Dictionary::decode(&bad, DEFAULT_ACTIVE_MAX).is_err(),
            "a body longer than its entries is corrupt"
        );
        // An aggregation mask that permits anything the entry's kind does not.
        let mut metrics = Dictionary::new(4);
        metrics.intern(metric("cpu.usage_usec")).expect("intern");
        let mut bad = metrics.encode().expect("encode");
        // generation + count + entry header, then unit, kind, scope, source, mask.
        let mask_at = 8 + 4 + 4;
        let counter_mask = MetricKind::Counter.aggregation_mask();
        // Bit 3 is `mean`, which a counter does not permit: an EXTRA bit.
        bad[mask_at..mask_at + 2].copy_from_slice(&(counter_mask | (1u16 << 3)).to_le_bytes());
        assert_eq!(
            Dictionary::decode(&bad, DEFAULT_ACTIVE_MAX).map(|_| ()),
            Err(ProtoError::DictionaryEntryMask {
                index: 0,
                mask: counter_mask | (1u16 << 3),
                expected: counter_mask,
            })
        );
        // A strict SUBSET is refused too: the mask is derived from the kind, so a missing
        // bit would be re-widened on re-encode and the sender's restriction silently lost.
        let mut bad = metrics.encode().expect("encode");
        bad[mask_at..mask_at + 2].copy_from_slice(&0u16.to_le_bytes());
        assert_eq!(
            Dictionary::decode(&bad, DEFAULT_ACTIVE_MAX).map(|_| ()),
            Err(ProtoError::DictionaryEntryMask {
                index: 0,
                mask: 0,
                expected: counter_mask,
            })
        );

        // A column kind code outside the table (metric entry).
        let mut metrics = Dictionary::new(5);
        metrics.intern(metric("cpu.usage_usec")).expect("intern");
        let mut bad = metrics.encode().expect("encode");
        bad[8 + 4 + 1] = 9; // the column kind byte
        assert_eq!(
            Dictionary::decode(&bad, DEFAULT_ACTIVE_MAX).map(|_| ()),
            Err(ProtoError::UnknownColumnKind(9))
        );

        // A unit code outside the table (metric entry).
        let mut metrics = Dictionary::new(3);
        metrics.intern(metric("cpu.usage_usec")).expect("intern");
        let metric_bytes = metrics.encode().expect("encode");
        let mut bad = metric_bytes.clone();
        bad[12] = 9;
        assert_eq!(
            Dictionary::decode(&bad, DEFAULT_ACTIVE_MAX).map(|_| ()),
            Err(ProtoError::DictionaryEntryUnknownCode { index: 0, code: 9 })
        );
    }

    #[test]
    fn metric_entries_carry_the_facts_that_let_a_reader_interpret_a_column() {
        let mut dictionary = Dictionary::new(1);
        let index = dictionary.intern(metric("io.rbytes")).expect("intern");
        let entry = dictionary.lookup(index).expect("lookup");
        match entry {
            Entry::Metric { facts, name } => {
                assert_eq!(name, "io.rbytes");
                assert_eq!(facts.unit, MetricUnit::Microseconds);
                assert_eq!(facts.kind, MetricKind::Counter);
                assert_eq!(facts.scope, MetricScope::Cgroup);
                assert_eq!(facts.source, MetricSource::CgroupFs);
                assert_eq!(
                    facts.aggregation_mask(),
                    Aggregation::Raw.bit() | Aggregation::Last.bit() | Aggregation::Delta.bit(),
                    "a counter's permitted aggregation excludes sum and mean"
                );
            }
            other => panic!("expected a metric entry, got {other:?}"),
        }
        assert_eq!(entry.kind(), EntryKind::Metric);
        assert_eq!(entry.kind_code(), 1);
    }

    #[test]
    fn aggregation_and_kind_names_and_codes_are_stable() {
        for (code, expected) in [
            (0u8, Aggregation::Raw),
            (1, Aggregation::Last),
            (2, Aggregation::Delta),
            (3, Aggregation::Mean),
            (4, Aggregation::Min),
            (5, Aggregation::Max),
        ] {
            assert_eq!(Aggregation::from_code(code), Ok(expected));
        }
        for code in 6u8..=u8::MAX {
            assert_eq!(
                Aggregation::from_code(code),
                Err(ProtoError::UnknownAggregation(code))
            );
        }
        assert_eq!(
            super::ALL_AGGREGATIONS.map(Aggregation::name),
            ["raw", "last", "delta", "mean", "min", "max"]
        );
        for (code, expected) in [
            (0u8, MetricKind::Gauge),
            (1, MetricKind::Counter),
            (2, MetricKind::State),
        ] {
            assert_eq!(MetricKind::from_code(code), Ok(expected));
        }
        for code in 3u8..=u8::MAX {
            assert_eq!(
                MetricKind::from_code(code),
                Err(ProtoError::UnknownColumnKind(code))
            );
        }
        for (code, expected) in [
            (1u8, MetricValueType::U64),
            (2, MetricValueType::I64),
            (3, MetricValueType::F64),
        ] {
            assert_eq!(MetricValueType::from_code(code), Ok(expected));
        }
        for code in [0u8, 4, 255] {
            assert_eq!(
                MetricValueType::from_code(code),
                Err(ProtoError::UnknownValueType(code))
            );
        }
        assert_eq!(MetricValueType::U64.name(), "u64");
        assert_eq!(MetricKind::Counter.name(), "counter");
        assert_eq!(MetricUnit::Bytes.name(), "bytes");
        assert_eq!(MetricScope::Daemon.name(), "daemon");
        assert_eq!(MetricSource::Derived.name(), "derived");
        assert_eq!(
            MetricKind::State.aggregation_mask(),
            Aggregation::Last.bit()
        );
    }

    #[test]
    fn every_vocabulary_name_and_code_is_stable_and_exhaustive() {
        // The names travel in fixtures and in dictionary entries; a renamed variant would
        // silently change what a decoder writes down, so they are pinned here.
        for (value_type, name, code) in [
            (MetricValueType::U64, "u64", 1u8),
            (MetricValueType::I64, "i64", 2),
            (MetricValueType::F64, "f64", 3),
        ] {
            assert_eq!(value_type.name(), name);
            assert_eq!(value_type as u8, code);
        }
        for (kind, name, code) in [
            (MetricKind::Gauge, "gauge", 0u8),
            (MetricKind::Counter, "counter", 1),
            (MetricKind::State, "state", 2),
        ] {
            assert_eq!(kind.name(), name);
            assert_eq!(kind as u8, code);
        }
        for (unit, name, code) in [
            (MetricUnit::None, "none", 0u8),
            (MetricUnit::Microseconds, "usec", 1),
            (MetricUnit::Milliseconds, "msec", 2),
            (MetricUnit::Nanoseconds, "nsec", 3),
            (MetricUnit::Bytes, "bytes", 4),
            (MetricUnit::Count, "count", 5),
            (MetricUnit::Ratio, "ratio", 6),
            (MetricUnit::Volts, "volts", 7),
            (MetricUnit::Celsius, "celsius", 8),
        ] {
            assert_eq!(unit.name(), name);
            assert_eq!(unit as u8, code);
            assert_eq!(MetricUnit::from_code(code, 0), Ok(unit));
        }
        for (scope, name, code) in [
            (MetricScope::Host, "host", 0u8),
            (MetricScope::Cgroup, "cgroup", 1),
            (MetricScope::Process, "process", 2),
            (MetricScope::Daemon, "daemon", 3),
        ] {
            assert_eq!(scope.name(), name);
            assert_eq!(scope as u8, code);
            assert_eq!(MetricScope::from_code(code, 0), Ok(scope));
        }
        for (source, name, code) in [
            (MetricSource::CgroupFs, "cgroupfs", 0u8),
            (MetricSource::ProcFs, "procfs", 1),
            (MetricSource::SysFs, "sysfs", 2),
            (MetricSource::Probe, "probe", 3),
            (MetricSource::Derived, "derived", 4),
        ] {
            assert_eq!(source.name(), name);
            assert_eq!(source as u8, code);
            assert_eq!(MetricSource::from_code(code, 0), Ok(source));
        }
        for code in 9u8..=u8::MAX {
            assert_eq!(
                MetricUnit::from_code(code, 3),
                Err(ProtoError::DictionaryEntryUnknownCode { index: 3, code })
            );
        }
        for code in 4u8..=u8::MAX {
            assert_eq!(
                MetricScope::from_code(code, 3),
                Err(ProtoError::DictionaryEntryUnknownCode { index: 3, code })
            );
        }
        for code in 5u8..=u8::MAX {
            assert_eq!(
                MetricSource::from_code(code, 3),
                Err(ProtoError::DictionaryEntryUnknownCode { index: 3, code })
            );
        }
        assert_eq!(Aggregation::Raw as u8, 0);
        assert_eq!(Aggregation::Max as u8, 5);
        assert_eq!(EntryKind::Entity as u8, 0);
        assert_eq!(EntryKind::Free as u8, 3);
    }

    #[test]
    fn the_default_generation_is_zero_and_empty() {
        let dictionary = Dictionary::default();
        assert_eq!(dictionary.generation(), 0);
        assert!(dictionary.is_empty());
        assert_eq!(dictionary.max_entries(), DEFAULT_ACTIVE_MAX);
        assert_eq!(
            dictionary.encode().expect("encode").len(),
            8,
            "generation and count only"
        );
        let decoded = Dictionary::decode(&dictionary.encode().expect("encode"), DEFAULT_ACTIVE_MAX)
            .expect("decode");
        assert_eq!(decoded, dictionary);
    }

    #[test]
    fn a_relabel_keeps_one_entry_in_a_live_table_but_a_wire_repeat_is_refused() {
        // Two entries with the same `inc` but different labels are a *live-table* relabel
        // (plan v4 §C.1): the entry is rewritten and keeps its index.
        let mut good = Dictionary::new(1);
        good.intern(Entry::Entity {
            inc: 1,
            label: "a".into(),
        })
        .expect("intern");
        good.intern(Entry::Entity {
            inc: 1,
            label: "b".into(),
        })
        .expect("relabel");
        assert_eq!(
            good.len(),
            1,
            "a relabel rewrites the entry, it does not add one"
        );
        assert_eq!(good.lookup(0).map(Entry::text), Ok("b"));

        // On the WIRE the same shape is a different thing: the sender's list is the
        // sender's numbering, so entry 1 must live at index 1. A decoder that interned
        // here would return index 0 and shift every later entry one position down.
        let body = Dictionary::new(1)
            .encode_with(&[
                Entry::Entity {
                    inc: 1,
                    label: "a".into(),
                },
                Entry::Entity {
                    inc: 1,
                    label: "b".into(),
                },
                metric("cpu.usage_usec"),
            ])
            .expect("encode");
        assert_eq!(
            Dictionary::decode(&body, DEFAULT_ACTIVE_MAX).map(|_| ()),
            Err(ProtoError::DuplicateDictionaryEntry { index: 1 }),
            "a wire repeat is refused, never deduplicated into a shorter table"
        );

        // The same identity repeated at a LATER position (a metric name twice) is refused
        // identically, so the check is not specific to incarnations.
        let body = Dictionary::new(2)
            .encode_with(&[
                metric("cpu.usage_usec"),
                Entry::Free { text: "/a".into() },
                metric("cpu.usage_usec"),
            ])
            .expect("encode");
        assert_eq!(
            Dictionary::decode(&body, DEFAULT_ACTIVE_MAX).map(|_| ()),
            Err(ProtoError::DuplicateDictionaryEntry { index: 2 })
        );
    }

    #[test]
    fn a_metric_name_repeated_with_other_facts_is_a_duplicate_entry_on_the_wire() {
        // `intern` calls the same name with other facts a conflicting column, a caller defect
        // of a live table. A wire body that does it repeats an identity, and a repeated
        // identity is `duplicate-dictionary-entry` whatever the other fields hold.
        let other = Entry::Metric {
            facts: MetricFacts {
                kind: MetricKind::Gauge,
                ..facts()
            },
            name: "cpu.usage_usec".into(),
        };
        let body = Dictionary::new(1)
            .encode_with(&[metric("cpu.usage_usec"), other.clone()])
            .expect("encode");
        assert_eq!(
            Dictionary::decode(&body, DEFAULT_ACTIVE_MAX).map(|_| ()),
            Err(ProtoError::DuplicateDictionaryEntry { index: 1 })
        );
        // The live table still reports the same shape as a conflict.
        let mut live = Dictionary::new(1);
        live.intern(metric("cpu.usage_usec")).expect("intern");
        assert_eq!(
            live.intern(other),
            Err(ProtoError::ConflictingColumn { metric: 0 })
        );
    }

    #[test]
    fn entry_display_names_its_identity() {
        assert_eq!(
            Entry::Entity {
                inc: 2,
                label: "/a".into()
            }
            .to_string(),
            "entity:2:/a"
        );
        assert_eq!(Entry::Free { text: "x".into() }.to_string(), "free:x");
        assert_eq!(
            Entry::Label {
                kind: 1,
                source: 2,
                text: "unit".into()
            }
            .to_string(),
            "label:1:2:unit"
        );
        assert!(metric("m").to_string().starts_with("metric:m:"));
    }

    #[test]
    fn a_quality_code_from_the_shared_table_is_usable_in_a_column() {
        let mut dictionary = Dictionary::new(1);
        dictionary.intern(metric("mem.current")).expect("intern");
        assert_eq!(Quality::from_code(Quality::Ok.code(), 0), Ok(Quality::Ok));
    }
}
