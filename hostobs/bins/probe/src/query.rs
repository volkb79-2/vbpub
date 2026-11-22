//! Typed, bounded query serving for `snapshot_at`, `series_batch` and `procs`
//! (WIRE.md sections 4.8, 4.9 and 4.11; P5B-SERVER-DESIGN.md section 7).
//!
//! The source seam is [`QueryBackend`]. It returns immutable typed views and closed
//! [`QuerySourceError`] outcomes; it never constructs JSON, chooses wire page boundaries or
//! owns cursor strings. This module owns the server-side catalog, exact frame-size helpers,
//! series partitioning and the bounded cursor/view accounting used by [`crate::server`].

use std::collections::BTreeMap;
use std::sync::Arc;

use hostobs_proto::dict::{
    Aggregation, Dictionary, Entry, MetricFacts, MetricKind, MetricSource, MetricUnit,
    MetricValueType, DEFAULT_ACTIVE_MAX,
};
use hostobs_proto::frame::{
    encode_block, Block, BlockHeaderFacts, Column, FrameType, Row, TimeBucket, FRAME_HEADER_LEN,
    HEADER_BODY_LEN, SECTION_DIR_ENTRY_LEN, SUBJECT_BODY_LEN,
};
use hostobs_proto::quality::Quality;

use crate::backend::{Identity, StatusInfo, TickData};
use crate::codes::{Code, Detail, Pool};
use crate::encode::push_string;
use crate::selection::{Sel, Selection};
use crate::tick;
use hostobs_proto::hub::ConnId;

/// Longest canonical cursor token.
pub const MAX_CURSOR_BYTES: usize = 256;
/// Longest snapshot source label, in UTF-8 bytes.
pub const MAX_LABEL_BYTES: usize = 256;
/// Largest number of provenance cells emitted by one snapshot response.
pub const MAX_PROVENANCE_CELLS: usize = 64;
/// Largest legal encoded provenance object.
pub const MAX_PROVENANCE_OBJECT_BYTES: usize = 4_096;
/// Largest UTF-8 `comm`.
pub const MAX_COMM_BYTES: usize = 45;
/// Largest rendered operator `cmdline`.
pub const MAX_CMDLINE_BYTES: usize = 4_096;
/// Maximum `procs` rows a request may ask to emit.
pub const MAX_PROCS_LIMIT: u32 = 256;
/// Cursor entry count.
pub const MAX_CURSOR_ENTRIES: usize = 256;
/// Largest canonical cursor binding.
pub const MAX_CURSOR_BINDING_BYTES: usize = 16_384;
/// One cursor's fixed state allowance.
pub const CURSOR_STATE_BYTES: usize = 512;
/// A cursor's maximum lifetime in nanoseconds.
pub const CURSOR_LIFETIME_NS: u64 = 60_000_000_000;

/// The three standard history tiers.
#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub enum Tier {
    /// The hot tier.
    Hot,
    /// The warm tier.
    Warm,
    /// The cold tier.
    Cold,
}

impl Tier {
    /// The wire name.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Tier::Hot => "hot",
            Tier::Warm => "warm",
            Tier::Cold => "cold",
        }
    }

    /// Parse a configured tier name.
    #[must_use]
    pub fn from_name(name: &str) -> Option<Tier> {
        match name {
            "hot" => Some(Tier::Hot),
            "warm" => Some(Tier::Warm),
            "cold" => Some(Tier::Cold),
            _ => None,
        }
    }

    const fn order(self) -> u8 {
        match self {
            Tier::Hot => 0,
            Tier::Warm => 1,
            Tier::Cold => 2,
        }
    }
}

/// A snapshot tier argument.
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum TierChoice {
    /// Select the finest eligible tier.
    Auto,
    /// Use exactly this configured tier.
    Explicit(Tier),
}

/// One immutable configured tier fact.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct TierCatalogEntry {
    /// The standard tier.
    pub tier: Tier,
    /// Its configured bucket width.
    pub step_ms: u32,
}

/// The immutable catalog derived from the same `StatusInfo.tiers` facts that answer `status`.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct TierCatalog {
    entries: Vec<TierCatalogEntry>,
}

impl TierCatalog {
    /// Derive only the recognized `hot`, `warm` and `cold` entries from `status`.
    #[must_use]
    pub fn from_status(status: &StatusInfo) -> TierCatalog {
        let mut entries: Vec<TierCatalogEntry> = status
            .tiers
            .iter()
            .filter_map(|info| {
                Tier::from_name(&info.name).map(|tier| TierCatalogEntry {
                    tier,
                    step_ms: info.step_ms,
                })
            })
            .filter(|entry| entry.step_ms > 0)
            .collect();
        entries.sort_by_key(|entry| (entry.tier.order(), entry.step_ms));
        entries.dedup();
        TierCatalog { entries }
    }

    /// The configured entries.
    #[must_use]
    pub fn entries(&self) -> &[TierCatalogEntry] {
        &self.entries
    }

    /// The configured step of `tier`, if configured.
    #[must_use]
    pub fn step(&self, tier: Tier) -> Option<u32> {
        self.entries
            .iter()
            .find(|entry| entry.tier == tier)
            .map(|entry| entry.step_ms)
    }

    /// Whether a nonzero step names one of the configured standard tiers.
    #[must_use]
    pub fn has_step(&self, step_ms: u32) -> bool {
        step_ms > 0 && self.entries.iter().any(|entry| entry.step_ms == step_ms)
    }

    /// Validate an explicit snapshot tier before the source is called.
    #[must_use]
    pub fn explicit(&self, tier: Tier) -> Option<TierCatalogEntry> {
        self.entries
            .iter()
            .find(|entry| entry.tier == tier)
            .copied()
    }
}

/// Checked Euclidean floor of `real_ns` at a millisecond step, as an `i64`.
#[must_use]
pub fn bucket_floor(real_ns: i64, step_ms: u32) -> Option<i64> {
    if step_ms == 0 {
        return None;
    }
    let step_ns = i128::from(step_ms).checked_mul(1_000_000)?;
    let bucket = i128::from(real_ns)
        .div_euclid(step_ns)
        .checked_mul(step_ns)?;
    i64::try_from(bucket).ok()
}

/// The current-tick snapshot query.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct SnapshotRequest {
    /// The canonical selection.
    pub selection: Selection,
    /// The requested real time.
    pub real_ns: i64,
    /// The requested tier choice.
    pub tier: TierChoice,
    /// Whether provenance objects were requested.
    pub provenance: bool,
}

/// The bounded arguments of one `snapshot_at` request.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct SnapshotArgs {
    /// The typed request.
    pub request: SnapshotRequest,
}

/// The history query.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct SeriesRequest {
    /// The canonical selection.
    pub selection: Selection,
    /// The inclusive start of the half-open range.
    pub from_real_ns: i64,
    /// The exclusive end of the half-open range.
    pub to_real_ns: i64,
    /// The explicit step, or 0 for the coarsest covering tier.
    pub step_ms: u32,
    /// Aggregations in request order.
    pub aggs: Vec<Aggregation>,
}

/// The bounded arguments of one `series_batch` request.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct SeriesArgs {
    /// The typed request.
    pub request: SeriesRequest,
    /// The incoming continuation token.
    pub cursor: Option<String>,
    /// The maximum BLOCK frames this page may carry.
    pub max_blocks: u32,
}

/// One requested process field.
#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub enum ProcField {
    /// The stat command field.
    Comm,
    /// The parent PID.
    Ppid,
    /// The one-byte state.
    State,
    /// Process CPU time in microseconds.
    CpuUsec,
    /// Resident bytes.
    RssBytes,
    /// Bytes read.
    IoRbytes,
    /// Bytes written.
    IoWbytes,
    /// Thread count.
    Threads,
    /// Redacted operator command line.
    Cmdline,
}

impl ProcField {
    /// The wire name.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            ProcField::Comm => "comm",
            ProcField::Ppid => "ppid",
            ProcField::State => "state",
            ProcField::CpuUsec => "cpu_usec",
            ProcField::RssBytes => "rss_bytes",
            ProcField::IoRbytes => "io_rbytes",
            ProcField::IoWbytes => "io_wbytes",
            ProcField::Threads => "threads",
            ProcField::Cmdline => "cmdline",
        }
    }

    /// Parse a process field name.
    #[must_use]
    pub fn from_name(name: &str) -> Option<ProcField> {
        match name {
            "comm" => Some(ProcField::Comm),
            "ppid" => Some(ProcField::Ppid),
            "state" => Some(ProcField::State),
            "cpu_usec" => Some(ProcField::CpuUsec),
            "rss_bytes" => Some(ProcField::RssBytes),
            "io_rbytes" => Some(ProcField::IoRbytes),
            "io_wbytes" => Some(ProcField::IoWbytes),
            "threads" => Some(ProcField::Threads),
            "cmdline" => Some(ProcField::Cmdline),
            _ => None,
        }
    }
}

/// The process subject/filter request.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct ProcsRequest {
    /// The subject incarnation.
    pub subject_inc: u64,
    /// Whether descendant incarnations are included.
    pub descendants: bool,
    /// Requested fields, in canonical field order.
    pub fields: Vec<ProcField>,
}

/// The bounded arguments of one `procs` request.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct ProcsArgs {
    /// The typed request.
    pub request: ProcsRequest,
    /// The page size.
    pub limit: u32,
    /// The incoming continuation token.
    pub cursor: Option<String>,
}

/// A `procs` argument refusal: a schema error or the operator-only `cmdline` gate.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ProcsArgError {
    /// The first malformed schema path.
    Bad(crate::args::ArgError),
    /// The observer level requested `cmdline`.
    ForbiddenCmdline,
}

fn push_len(out: &mut Vec<u8>, len: usize) -> Option<()> {
    out.extend_from_slice(&u32::try_from(len).ok()?.to_le_bytes());
    Some(())
}

fn push_name(out: &mut Vec<u8>, name: &str) -> Option<()> {
    push_len(out, name.len())?;
    out.extend_from_slice(name.as_bytes());
    Some(())
}

fn push_selection(out: &mut Vec<u8>, selection: &Selection) -> Option<()> {
    push_len(out, selection.entities().len())?;
    for entity in selection.entities() {
        match entity {
            Sel::Inc(inc) => {
                out.push(0);
                out.extend_from_slice(&inc.to_le_bytes());
            }
            Sel::Path(path) => {
                out.push(1);
                push_name(out, path)?;
            }
            Sel::Prefix(prefix) => {
                out.push(2);
                push_name(out, prefix)?;
            }
            Sel::All => out.push(3),
        }
    }
    push_len(out, selection.metrics().len())?;
    for metric in selection.metrics() {
        push_name(out, metric)?;
    }
    Some(())
}

/// The canonical, decoded-byte arguments bound to a history cursor. `max_blocks` and the
/// incoming cursor are deliberately absent.
#[must_use]
pub fn canonical_series_arguments(request: &SeriesRequest) -> Option<Vec<u8>> {
    let mut out = Vec::new();
    push_selection(&mut out, &request.selection)?;
    out.extend_from_slice(&request.from_real_ns.to_le_bytes());
    out.extend_from_slice(&request.to_real_ns.to_le_bytes());
    out.extend_from_slice(&request.step_ms.to_le_bytes());
    push_len(&mut out, request.aggs.len())?;
    for agg in &request.aggs {
        out.push(*agg as u8);
    }
    (out.len() <= MAX_CURSOR_BINDING_BYTES).then_some(out)
}

/// The canonical, decoded-byte arguments bound to a process cursor. `limit` and the incoming
/// cursor are deliberately absent.
#[must_use]
pub fn canonical_procs_arguments(request: &ProcsRequest) -> Option<Vec<u8>> {
    let mut out = Vec::new();
    out.extend_from_slice(&request.subject_inc.to_le_bytes());
    out.push(u8::from(request.descendants));
    out.push(u8::try_from(request.fields.len()).ok()?);
    for field in &request.fields {
        out.push(*field as u8);
    }
    (out.len() <= MAX_CURSOR_BINDING_BYTES).then_some(out)
}

/// The row and block bounds passed to a query source.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct QueryBounds {
    /// Most resolved subjects.
    pub max_rows: u32,
    /// Most buckets in one history BLOCK.
    pub max_block_buckets: u32,
    /// Largest dictionary-bearing BLOCK.
    pub max_block_bytes: u32,
}

/// The feature whose negotiated support may be absent.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum QueryFeature {
    /// Snapshot provenance objects.
    Provenance,
}

/// One closed source outcome.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum QuerySourceError {
    /// The subject/range is not retained.
    NotFound,
    /// The selected snapshot bucket is retained but absent.
    DataGap,
    /// A checked snapshot bucket start does not fit `i64`.
    BucketOutOfRange,
    /// This source does not provide the feature.
    Unsupported(QueryFeature),
    /// A method-specific budget is exhausted.
    Budget(Pool),
    /// The source view is no longer valid.
    ViewInvalid,
    /// The source fact is malformed.
    Internal,
}

/// Snapshot view identifier.
#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct SnapshotViewId(pub u64);

/// History view identifier.
#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct SeriesViewId(pub u64);

/// One immutable published process scan identifier.
#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct ProcScanId(pub u64);

/// One immutable filtered process projection identifier.
#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct ProcProjectionId(pub u64);

/// One source view identifier.
#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub enum QueryViewId {
    /// A snapshot view.
    Snapshot(SnapshotViewId),
    /// A history view.
    Series(SeriesViewId),
    /// A process projection.
    Procs(ProcProjectionId),
}

/// The stable zero-based ordinal of a data-bearing history bucket.
#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct SeriesPosition(pub u64);

impl SeriesPosition {
    /// The next ordinal.
    #[must_use]
    pub fn checked_add(self, count: u64) -> Option<SeriesPosition> {
        self.0.checked_add(count).map(SeriesPosition)
    }
}

/// One canonical metric column fact.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct QueryColumnMeta {
    /// The metric name.
    pub metric: String,
    /// Its dictionary facts.
    pub facts: MetricFacts,
    /// Its wire value type.
    pub value_type: MetricValueType,
}

/// One subject's bounded snapshot metadata.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SnapshotSubjectMeta {
    /// The incarnation.
    pub inc: u64,
    /// The UTF-8 byte length of the pinned source label.
    pub label_len: u16,
}

/// Metadata-only snapshot resolution.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SnapshotMeta {
    /// The resolved tier.
    pub tier: Tier,
    /// The checked bucket start.
    pub bucket_real_ns: i64,
    /// The tier step.
    pub step_ms: u32,
    /// Observed coverage in the bucket.
    pub covered_ms: u32,
    /// Complete ordered subjects.
    pub subjects: Vec<SnapshotSubjectMeta>,
    /// Complete requested metric columns.
    pub columns: Vec<QueryColumnMeta>,
}

/// The immutable snapshot view.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SnapshotView {
    /// The view id.
    pub id: SnapshotViewId,
    /// The metadata-only facts.
    pub meta: SnapshotMeta,
}

/// The one typed snapshot candidate and its optional provenance.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SnapshotData {
    /// The live-shaped candidate.
    pub tick: TickData,
    /// Provenance, absent when not requested.
    pub provenance: Option<Vec<CellProvenance>>,
}

/// One immutable history view.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SeriesView {
    /// The view id.
    pub id: SeriesViewId,
    /// The selected tier.
    pub tier: Tier,
    /// The selected step.
    pub step_ms: u32,
    /// Resolved subjects, ascending.
    pub subjects: Vec<u64>,
    /// Complete requested metric columns.
    pub columns: Vec<QueryColumnMeta>,
    /// First data-bearing ordinal, or no data.
    pub first: Option<SeriesPosition>,
}

/// One contiguous `(subject, agg)` run.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SeriesGroup {
    /// Subject incarnation.
    pub subject_inc: u64,
    /// Aggregation.
    pub agg: Aggregation,
    /// First ordinal.
    pub first: SeriesPosition,
    /// Exact number of buckets.
    pub bucket_count: u64,
}

/// One server-owned fixed-size block plan.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct BlockPlan {
    /// Subject incarnation.
    pub subject_inc: u64,
    /// Aggregation.
    pub agg: Aggregation,
    /// First ordinal.
    pub start: SeriesPosition,
    /// Bucket count in this block.
    pub buckets: u32,
}

/// One history BLOCK's typed data.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HistoryBlockData {
    /// Subject incarnation.
    pub subject_inc: u64,
    /// Aggregation.
    pub agg: Aggregation,
    /// Buckets.
    pub buckets: Vec<HistoryBucket>,
    /// Metric columns.
    pub columns: Vec<HistoryColumn>,
}

/// One history bucket's source facts.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct HistoryBucket {
    /// Bucket start.
    pub bucket_real_ns: i64,
    /// Covered milliseconds.
    pub covered_ms: u32,
    /// Exact represented sample count.
    pub samples: u16,
}

/// One history metric column.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HistoryColumn {
    /// Metric name.
    pub metric: String,
    /// Dictionary facts.
    pub facts: MetricFacts,
    /// Wire value type.
    pub value_type: MetricValueType,
    /// Encoded eight-byte row words.
    pub values: Vec<u64>,
    /// Quality codes.
    pub quality: Vec<u8>,
}

/// The newest filtered process projection metadata.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProcScanView {
    /// The projection id.
    pub id: ProcProjectionId,
    /// The pinned published scan.
    pub scan_id: ProcScanId,
    /// Publication monotonic time.
    pub published_mono_ns: u64,
    /// Rows in this filtered projection.
    pub row_count: u32,
    /// Candidates inspected.
    pub scanned: u32,
    /// Candidates listed.
    pub total: u32,
    /// Whether the bounded acquisition procedure completed without omissions.
    pub complete: bool,
}

/// One immutable process row.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProcRow {
    /// PID.
    pub pid: u32,
    /// Process start ticks.
    pub start_ticks: u64,
    /// Membership incarnation.
    pub inc: u64,
    /// Optional command name.
    pub comm: Option<String>,
    /// Optional parent PID.
    pub ppid: Option<u32>,
    /// Optional state byte.
    pub state: Option<u8>,
    /// Optional CPU time.
    pub cpu_usec: Option<u64>,
    /// Optional RSS.
    pub rss_bytes: Option<u64>,
    /// Optional bytes read.
    pub io_rbytes: Option<u64>,
    /// Optional bytes written.
    pub io_wbytes: Option<u64>,
    /// Optional thread count.
    pub threads: Option<u64>,
    /// Optional redacted command line.
    pub cmdline: Option<String>,
}

/// A raw input's exact wire number.
#[derive(Clone, Copy, Debug)]
pub enum RawNumber {
    /// Unsigned.
    U64(u64),
    /// Signed.
    I64(i64),
    /// Finite floating point.
    F64(f64),
}

impl PartialEq for RawNumber {
    fn eq(&self, other: &Self) -> bool {
        match (self, other) {
            (RawNumber::U64(a), RawNumber::U64(b)) => a == b,
            (RawNumber::I64(a), RawNumber::I64(b)) => a == b,
            (RawNumber::F64(a), RawNumber::F64(b)) => a.to_bits() == b.to_bits(),
            _ => false,
        }
    }
}

impl Eq for RawNumber {}

/// One monotonic half-open interval.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct MonoInterval {
    /// Start.
    pub start: u64,
    /// End.
    pub end: u64,
}

/// One cell's complete provenance.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CellProvenance {
    /// Subject.
    pub inc: u64,
    /// Metric.
    pub metric: String,
    /// Quality.
    pub quality: Quality,
    /// Source.
    pub source: MetricSource,
    /// Symbolic source key.
    pub source_ref: Option<String>,
    /// Observation time.
    pub observed_mono_ns: Option<u64>,
    /// Observation interval.
    pub interval_mono_ns: Option<MonoInterval>,
    /// Raw inputs.
    pub raw_inputs: Option<Vec<RawInput>>,
    /// Constraints.
    pub constraints: Option<Vec<ConstraintFact>>,
}

/// One raw provenance input.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RawInput {
    /// Metric.
    pub metric: String,
    /// Declared type.
    pub value_type: MetricValueType,
    /// Represented typed number.
    pub value: Option<RawNumber>,
    /// Quality.
    pub quality: Quality,
    /// Source.
    pub source: MetricSource,
    /// Unit.
    pub unit: MetricUnit,
    /// Symbolic source key.
    pub source_ref: Option<String>,
    /// Observation time.
    pub observed_mono_ns: Option<u64>,
    /// Observation interval.
    pub interval_mono_ns: Option<MonoInterval>,
}

/// One constraint's exact typed value.
#[derive(Clone, Debug)]
pub enum ConstraintValue {
    /// Unsigned.
    U64(u64),
    /// Signed.
    I64(i64),
    /// Finite floating point.
    F64(f64),
    /// Bounded ASCII text.
    String(String),
}

impl PartialEq for ConstraintValue {
    fn eq(&self, other: &Self) -> bool {
        match (self, other) {
            (ConstraintValue::U64(a), ConstraintValue::U64(b)) => a == b,
            (ConstraintValue::I64(a), ConstraintValue::I64(b)) => a == b,
            (ConstraintValue::F64(a), ConstraintValue::F64(b)) => a.to_bits() == b.to_bits(),
            (ConstraintValue::String(a), ConstraintValue::String(b)) => a == b,
            _ => false,
        }
    }
}

impl Eq for ConstraintValue {}

/// One provenance constraint.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ConstraintFact {
    /// Name.
    pub name: String,
    /// Typed value.
    pub value: ConstraintValue,
    /// Optional unit.
    pub unit: Option<MetricUnit>,
    /// Symbolic source key.
    pub source_ref: Option<String>,
    /// Observation time.
    pub observed_mono_ns: Option<u64>,
}

/// The source seam for immutable query data.
pub trait QueryBackend {
    /// Resolve metadata only for a snapshot.
    fn snapshot_open(
        &self,
        request: &SnapshotRequest,
        now_ns: u64,
        bounds: QueryBounds,
    ) -> Result<SnapshotView, QuerySourceError>;
    /// Read the one admitted snapshot candidate.
    fn snapshot_read(&self, view: SnapshotViewId) -> Result<SnapshotData, QuerySourceError>;
    /// Resolve metadata only for a history range.
    fn series_open(
        &self,
        request: &SeriesRequest,
        now_ns: u64,
        bounds: QueryBounds,
    ) -> Result<SeriesView, QuerySourceError>;
    /// The unique group containing `at`, or `None` at the exact terminal ordinal.
    fn series_group(
        &self,
        view: SeriesViewId,
        at: SeriesPosition,
    ) -> Result<Option<SeriesGroup>, QuerySourceError>;
    /// Read exactly one planned in-group range.
    fn series_read(
        &self,
        view: SeriesViewId,
        at: SeriesPosition,
        buckets: u32,
    ) -> Result<HistoryBlockData, QuerySourceError>;
    /// Select the newest scan and create this request's distinct projection.
    fn procs_open(
        &self,
        request: &ProcsRequest,
        now_ns: u64,
        max_rows: u32,
    ) -> Result<ProcScanView, QuerySourceError>;
    /// Read the projection row at `index`.
    fn procs_read(
        &self,
        view: ProcProjectionId,
        index: u32,
        fields: &[ProcField],
    ) -> Result<Option<ProcRow>, QuerySourceError>;
    /// Release one view after its last active request/cursor reference.
    fn release_view(&self, view: QueryViewId);
}

fn fail(key: &str) -> crate::args::ArgError {
    crate::args::ArgError {
        key: key.to_string(),
    }
}

fn path(parent: &str, name: &str) -> String {
    if parent.is_empty() {
        name.to_string()
    } else {
        format!("{parent}.{name}")
    }
}

fn string_array(
    object: &crate::args::Obj<'_>,
    name: &str,
) -> Result<Vec<String>, crate::args::ArgError> {
    let elements = object.elements(name)?;
    let schema = path(&object_path(object), name);
    let mut out = Vec::with_capacity(elements.len());
    for (kind, raw) in elements {
        if kind != hostobs_proto::envelope::Kind::Str {
            return Err(fail(&schema));
        }
        out.push(crate::args::unescape(raw, 64).ok_or_else(|| fail(&schema))?);
    }
    Ok(out)
}

fn object_path(object: &crate::args::Obj<'_>) -> String {
    object.path().to_string()
}

/// Parse `snapshot_at` arguments.
pub fn parse_snapshot(args: &[u8]) -> Result<SnapshotArgs, crate::args::ArgError> {
    let object = crate::args::Obj::parse(args, "")?;
    object.only(&["selection", "real_ns", "tier", "provenance"])?;
    let selection = crate::args::selection(&object, "selection")?;
    let real_ns = object.int("real_ns")?;
    let tier_name = object.string("tier", 4)?;
    let tier = if tier_name == "auto" {
        TierChoice::Auto
    } else {
        Tier::from_name(&tier_name)
            .map(TierChoice::Explicit)
            .ok_or_else(|| fail("tier"))?
    };
    let provenance = match object.get("provenance", hostobs_proto::envelope::Kind::True) {
        Ok(_) => true,
        Err(_) => {
            object.get("provenance", hostobs_proto::envelope::Kind::False)?;
            false
        }
    };
    Ok(SnapshotArgs {
        request: SnapshotRequest {
            selection,
            real_ns,
            tier,
            provenance,
        },
    })
}

/// Parse `series_batch` arguments.
pub fn parse_series(
    args: &[u8],
    max_page_blocks: u32,
) -> Result<SeriesArgs, crate::args::ArgError> {
    let object = crate::args::Obj::parse(args, "")?;
    object.only(&[
        "selection",
        "from_real_ns",
        "to_real_ns",
        "step_ms",
        "aggs",
        "cursor",
        "max_blocks",
    ])?;
    let selection = crate::args::selection(&object, "selection")?;
    let from_real_ns = object.int("from_real_ns")?;
    let to_real_ns = object.int("to_real_ns")?;
    if from_real_ns >= to_real_ns {
        return Err(fail("to_real_ns"));
    }
    let step = object.uint("step_ms", u64::from(u32::MAX))?;
    let step_ms = u32::try_from(step).map_err(|_| fail("step_ms"))?;
    let names = string_array(&object, "aggs")?;
    if names.is_empty() || names.len() > 4 {
        return Err(fail("aggs"));
    }
    let mut aggs = Vec::with_capacity(names.len());
    for name in names {
        let agg = [
            Aggregation::Raw,
            Aggregation::Last,
            Aggregation::Delta,
            Aggregation::Mean,
            Aggregation::Min,
            Aggregation::Max,
        ]
        .into_iter()
        .find(|agg| agg.name() == name)
        .ok_or_else(|| fail("aggs"))?;
        if aggs.contains(&agg) {
            return Err(fail("aggs"));
        }
        aggs.push(agg);
    }
    let cursor = optional_cursor(&object)?;
    let max = object.uint("max_blocks", u64::from(max_page_blocks))?;
    let max_blocks = u32::try_from(max).map_err(|_| fail("max_blocks"))?;
    if max_blocks == 0 || max_blocks > max_page_blocks {
        return Err(fail("max_blocks"));
    }
    Ok(SeriesArgs {
        request: SeriesRequest {
            selection,
            from_real_ns,
            to_real_ns,
            step_ms,
            aggs,
        },
        cursor,
        max_blocks,
    })
}

/// Parse `procs` arguments. `operator` controls the `cmdline` privacy gate.
pub fn parse_procs(args: &[u8], operator: bool) -> Result<ProcsArgs, ProcsArgError> {
    let object = crate::args::Obj::parse(args, "").map_err(ProcsArgError::Bad)?;
    object
        .only(&["subject", "descendants", "fields", "limit", "cursor"])
        .map_err(ProcsArgError::Bad)?;
    let subject = object.object("subject").map_err(ProcsArgError::Bad)?;
    subject.only(&["inc"]).map_err(ProcsArgError::Bad)?;
    let subject_inc = subject.uint("inc", u64::MAX).map_err(ProcsArgError::Bad)?;
    let descendants = match object.get("descendants", hostobs_proto::envelope::Kind::True) {
        Ok(_) => true,
        Err(_) => {
            object
                .get("descendants", hostobs_proto::envelope::Kind::False)
                .map_err(ProcsArgError::Bad)?;
            false
        }
    };
    let names = string_array(&object, "fields").map_err(ProcsArgError::Bad)?;
    let mut fields = Vec::with_capacity(names.len());
    for name in names {
        let field =
            ProcField::from_name(&name).ok_or_else(|| ProcsArgError::Bad(fail("fields")))?;
        if field == ProcField::Cmdline && !operator {
            return Err(ProcsArgError::ForbiddenCmdline);
        }
        if fields.contains(&field) {
            return Err(ProcsArgError::Bad(fail("fields")));
        }
        fields.push(field);
    }
    fields.sort_unstable();
    let limit = object
        .uint("limit", u64::from(MAX_PROCS_LIMIT))
        .map_err(ProcsArgError::Bad)?;
    let limit = u32::try_from(limit).map_err(|_| ProcsArgError::Bad(fail("limit")))?;
    if limit == 0 || limit > MAX_PROCS_LIMIT {
        return Err(ProcsArgError::Bad(fail("limit")));
    }
    let cursor = optional_cursor(&object).map_err(ProcsArgError::Bad)?;
    Ok(ProcsArgs {
        request: ProcsRequest {
            subject_inc,
            descendants,
            fields,
        },
        limit,
        cursor,
    })
}

fn optional_cursor(object: &crate::args::Obj<'_>) -> Result<Option<String>, crate::args::ArgError> {
    let member = object.get("cursor", hostobs_proto::envelope::Kind::Null);
    if member.is_ok() {
        return Ok(None);
    }
    let cursor = object.string("cursor", MAX_CURSOR_BYTES)?;
    if cursor.is_empty()
        || !cursor.is_ascii()
        || cursor
            .chars()
            .any(|ch| u32::from(ch) < 0x20 || u32::from(ch) == 0x7f)
    {
        return Err(fail("cursor"));
    }
    Ok(Some(cursor))
}

/// Map an allowed `snapshot_open` error.
#[must_use]
pub fn snapshot_open_error(error: QuerySourceError) -> (Code, Detail) {
    match error {
        QuerySourceError::NotFound => (Code::NotFound, Detail::Empty),
        QuerySourceError::DataGap => (Code::DataGap, Detail::Empty),
        QuerySourceError::BucketOutOfRange => (Code::BadRequest, Detail::key("real_ns")),
        QuerySourceError::Unsupported(QueryFeature::Provenance) => {
            (Code::UnsupportedFeature, Detail::Feature("provenance"))
        }
        QuerySourceError::Budget(Pool::Rows) => (Code::Budget, Detail::Pool(Pool::Rows)),
        _ => (Code::Internal, Detail::Empty),
    }
}

/// Map an allowed `series_open`/`series_group` error.
#[must_use]
pub fn series_open_error(error: QuerySourceError, had_cursor: bool) -> (Code, Detail) {
    match error {
        QuerySourceError::NotFound => (Code::NotFound, Detail::Empty),
        QuerySourceError::Budget(Pool::Rows) => (Code::Budget, Detail::Pool(Pool::Rows)),
        QuerySourceError::ViewInvalid if had_cursor => (Code::CursorInvalid, Detail::Empty),
        _ => (Code::Internal, Detail::Empty),
    }
}

/// Map an allowed `series_group` error. `NotFound` belongs to the open operation and is
/// `internal` when a group lookup returns it.
#[must_use]
pub fn series_group_error(error: QuerySourceError, had_cursor: bool) -> (Code, Detail) {
    match error {
        QuerySourceError::Budget(Pool::Rows) => (Code::Budget, Detail::Pool(Pool::Rows)),
        QuerySourceError::ViewInvalid if had_cursor => (Code::CursorInvalid, Detail::Empty),
        _ => (Code::Internal, Detail::Empty),
    }
}

/// Map an allowed `snapshot_read` error.
#[must_use]
pub fn snapshot_read_error(error: QuerySourceError) -> (Code, Detail) {
    match error {
        QuerySourceError::Budget(Pool::Rows) => (Code::Budget, Detail::Pool(Pool::Rows)),
        _ => (Code::Internal, Detail::Empty),
    }
}

/// Map an allowed `procs_open` error.
#[must_use]
pub fn procs_open_error(error: QuerySourceError) -> (Code, Detail) {
    match error {
        QuerySourceError::NotFound => (Code::NotFound, Detail::Empty),
        QuerySourceError::Budget(Pool::Procs) => (Code::Budget, Detail::Pool(Pool::Procs)),
        _ => (Code::Internal, Detail::Empty),
    }
}

/// Map an allowed `procs_read` error.
#[must_use]
pub fn procs_read_error(error: QuerySourceError, had_cursor: bool) -> (Code, Detail) {
    match error {
        QuerySourceError::ViewInvalid if had_cursor => (Code::CursorInvalid, Detail::Empty),
        _ => (Code::Internal, Detail::Empty),
    }
}

/// Map an allowed post-REP `series_read` error.
#[must_use]
pub fn series_read_end_error(error: QuerySourceError) -> (Code, Detail) {
    match error {
        QuerySourceError::ViewInvalid => (Code::CursorInvalid, Detail::Empty),
        QuerySourceError::Budget(Pool::Rows) => (Code::Budget, Detail::Pool(Pool::Rows)),
        _ => (Code::Internal, Detail::Empty),
    }
}

pub(crate) fn metric_entry(column: &QueryColumnMeta) -> Entry {
    Entry::Metric {
        facts: column.facts,
        name: column.metric.clone(),
    }
}

fn placeholder_label_bytes(len: usize) -> String {
    "x".repeat(len)
}

/// Validate the snapshot metadata returned before any value read.
pub fn validate_snapshot_meta(
    meta: &SnapshotMeta,
    request: &SnapshotRequest,
    catalog: &TierCatalog,
    max_rows: u32,
) -> Result<(), QuerySourceError> {
    let Some(configured) = catalog.step(meta.tier) else {
        return Err(QuerySourceError::Internal);
    };
    if let TierChoice::Explicit(tier) = request.tier {
        if meta.tier != tier {
            return Err(QuerySourceError::Internal);
        }
    }
    if configured != meta.step_ms || meta.covered_ms > meta.step_ms {
        return Err(QuerySourceError::Internal);
    }
    let Some(bucket) = bucket_floor(request.real_ns, meta.step_ms) else {
        return Err(QuerySourceError::Internal);
    };
    if bucket != meta.bucket_real_ns {
        return Err(QuerySourceError::Internal);
    }
    let max = usize::try_from(max_rows).unwrap_or(usize::MAX);
    if meta.subjects.len() > max {
        return Err(QuerySourceError::Budget(Pool::Rows));
    }
    let mut previous = None;
    for subject in &meta.subjects {
        if subject.label_len > 256 || previous.is_some_and(|inc| inc >= subject.inc) {
            return Err(QuerySourceError::Internal);
        }
        previous = Some(subject.inc);
    }
    validate_columns(&meta.columns, &request.selection.metrics())?;
    Ok(())
}

fn validate_columns(
    columns: &[QueryColumnMeta],
    requested: &[String],
) -> Result<(), QuerySourceError> {
    if columns.len() != requested.len() {
        return Err(QuerySourceError::Internal);
    }
    let mut previous: Option<&str> = None;
    for (column, name) in columns.iter().zip(requested) {
        if &column.metric != name || previous.is_some_and(|value| value >= column.metric.as_str()) {
            return Err(QuerySourceError::Internal);
        }
        previous = Some(&column.metric);
    }
    Ok(())
}

/// Metadata-only snapshot dictionary bytes. The source's exact labels remain pinned in its view.
pub fn snapshot_dictionary(meta: &SnapshotMeta) -> Result<Vec<u8>, QuerySourceError> {
    let mut entries = Vec::with_capacity(meta.subjects.len().saturating_add(meta.columns.len()));
    for subject in &meta.subjects {
        entries.push(Entry::Entity {
            inc: subject.inc,
            label: placeholder_label_bytes(usize::from(subject.label_len)),
        });
    }
    entries.extend(meta.columns.iter().map(metric_entry));
    tick::dictionary_body(1, &entries).map_err(|_| QuerySourceError::Internal)
}

/// The exact dictionary-bearing live frame size for snapshot metadata.
pub fn snapshot_frame_len(meta: &SnapshotMeta) -> Option<usize> {
    let dictionary = snapshot_dictionary(meta).ok()?;
    let data = TickData {
        header: BlockHeaderFacts {
            boot_id: [0; 16],
            epoch: 0,
            seq: 0,
            mono_start_ns: 0,
            mono_end_ns: 0,
            boot_ns: 0,
            real_ns: meta.bucket_real_ns,
            dict_gen: 0,
            n_rows: u32::try_from(meta.subjects.len()).ok()?,
            dropped: 0,
            flags: 0,
        },
        entries: Vec::new(),
        rows: vec![
            Row {
                ent: 0,
                t_off_us: 0,
            };
            meta.subjects.len()
        ],
        columns: (0..meta.columns.len())
            .map(|_| Column {
                metric: 0,
                value_type: MetricValueType::U64,
                kind: MetricKind::Gauge,
                values: (0..meta.subjects.len()).map(|_| 0).collect(),
                quality: (0..meta.subjects.len()).map(|_| 0).collect(),
            })
            .collect(),
    };
    tick::encoded_len(&data, Some(dictionary.len()))
}

/// Validate the admitted snapshot candidate against independent metadata.
pub fn validate_snapshot_data(
    meta: &SnapshotMeta,
    data: &SnapshotData,
    request: &SnapshotRequest,
    now_ns: u64,
) -> Result<(), QuerySourceError> {
    tick::validate(&data.tick).map_err(|_| QuerySourceError::Internal)?;
    if data.tick.header.real_ns != meta.bucket_real_ns
        || data.tick.rows.len() != meta.subjects.len()
    {
        return Err(QuerySourceError::Internal);
    }
    let entities = meta.subjects.len();
    if data.tick.entries.len() != entities.saturating_add(meta.columns.len()) {
        return Err(QuerySourceError::Internal);
    }
    for (index, subject) in meta.subjects.iter().enumerate() {
        match data.tick.entries.get(index) {
            Some(Entry::Entity { inc, label })
                if *inc == subject.inc && label.len() == usize::from(subject.label_len) => {}
            _ => return Err(QuerySourceError::Internal),
        }
        let Some(row) = data.tick.rows.get(index) else {
            return Err(QuerySourceError::Internal);
        };
        if usize::try_from(row.ent).ok() != Some(index) || row.t_off_us != 0 {
            return Err(QuerySourceError::Internal);
        }
    }
    for (index, expected) in meta.columns.iter().enumerate() {
        let entry_index = entities
            .checked_add(index)
            .ok_or(QuerySourceError::Internal)?;
        let Some(Entry::Metric { facts, name }) = data.tick.entries.get(entry_index) else {
            return Err(QuerySourceError::Internal);
        };
        if name != &expected.metric || facts != &expected.facts {
            return Err(QuerySourceError::Internal);
        }
        let Some(column) = data.tick.columns.get(index) else {
            return Err(QuerySourceError::Internal);
        };
        if column.metric != u32::try_from(entry_index).unwrap_or(u32::MAX)
            || column.kind != expected.facts.kind
            || column.value_type != expected.value_type
            || column.values.len() != entities
            || column.quality.len() != entities
        {
            return Err(QuerySourceError::Internal);
        }
        if column.value_type == MetricValueType::F64
            && column
                .values
                .iter()
                .any(|bits| !f64::from_bits(*bits).is_finite())
        {
            return Err(QuerySourceError::Internal);
        }
    }
    if data.tick.columns.len() != meta.columns.len() {
        return Err(QuerySourceError::Internal);
    }
    if request.provenance {
        validate_provenance(meta, data.provenance.as_deref(), now_ns)?;
    } else if data.provenance.is_some() {
        return Err(QuerySourceError::Internal);
    }
    Ok(())
}

fn valid_ref(text: &str) -> bool {
    (1..=96).contains(&text.len())
        && text
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'.' | b':' | b'-'))
}

fn valid_interval(interval: MonoInterval) -> bool {
    interval.start < interval.end
}

fn valid_raw(raw: &RawInput) -> bool {
    if (raw.quality == Quality::Ok && raw.value.is_none()) || raw.value_type_mismatch() {
        return false;
    }
    if raw
        .source_ref
        .as_deref()
        .is_some_and(|text| !valid_ref(text))
    {
        return false;
    }
    if raw
        .interval_mono_ns
        .is_some_and(|interval| !valid_interval(interval))
    {
        return false;
    }
    if !crate::selection::is_metric_name(&raw.metric) {
        return false;
    }
    if let Some(RawNumber::F64(value)) = raw.value {
        if !value.is_finite() {
            return false;
        }
    }
    true
}

trait RawTypeCheck {
    fn value_type_mismatch(&self) -> bool;
}

impl RawTypeCheck for RawInput {
    fn value_type_mismatch(&self) -> bool {
        match (self.value_type, self.value) {
            (_, None) => false,
            (MetricValueType::U64, Some(RawNumber::U64(_)))
            | (MetricValueType::I64, Some(RawNumber::I64(_)))
            | (MetricValueType::F64, Some(RawNumber::F64(_))) => false,
            _ => true,
        }
    }
}

fn valid_constraint(fact: &ConstraintFact) -> bool {
    if !crate::selection::is_metric_name(&fact.name) {
        return false;
    }
    if fact
        .source_ref
        .as_deref()
        .is_some_and(|text| !valid_ref(text))
    {
        return false;
    }
    match &fact.value {
        ConstraintValue::F64(value) if !value.is_finite() => false,
        ConstraintValue::String(value) => {
            (1..=64).contains(&value.len())
                && value.bytes().all(|byte| {
                    byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b',' | b':' | b'/' | b'-')
                })
        }
        _ => true,
    }
}

fn validate_provenance(
    meta: &SnapshotMeta,
    provenance: Option<&[CellProvenance]>,
    now_ns: u64,
) -> Result<(), QuerySourceError> {
    let Some(cells) = provenance else {
        return Err(QuerySourceError::Internal);
    };
    let expected_cells = meta
        .subjects
        .len()
        .checked_mul(meta.columns.len())
        .ok_or(QuerySourceError::Internal)?;
    if cells.len() != expected_cells || cells.len() > MAX_PROVENANCE_CELLS {
        return Err(QuerySourceError::Internal);
    }
    let mut expected = Vec::with_capacity(cells.len());
    for subject in &meta.subjects {
        for column in &meta.columns {
            expected.push((subject.inc, column.metric.as_str()));
        }
    }
    for (cell, (inc, metric)) in cells.iter().zip(expected) {
        if cell.inc != inc || cell.metric != metric {
            return Err(QuerySourceError::Internal);
        }
        if cell
            .source_ref
            .as_deref()
            .is_some_and(|text| !valid_ref(text))
        {
            return Err(QuerySourceError::Internal);
        }
        if cell
            .observed_mono_ns
            .is_some_and(|observed| observed > now_ns)
        {
            return Err(QuerySourceError::Internal);
        }
        if cell
            .interval_mono_ns
            .is_some_and(|interval| !valid_interval(interval))
        {
            return Err(QuerySourceError::Internal);
        }
        if let Some(items) = &cell.raw_inputs {
            if items.len() > 4
                || !items.iter().all(|raw| {
                    valid_raw(raw)
                        && raw
                            .observed_mono_ns
                            .is_none_or(|observed| observed <= now_ns)
                })
            {
                return Err(QuerySourceError::Internal);
            }
        }
        if let Some(items) = &cell.constraints {
            if items.len() > 4
                || !items.iter().all(|fact| {
                    valid_constraint(fact)
                        && fact
                            .observed_mono_ns
                            .is_none_or(|observed| observed <= now_ns)
                })
            {
                return Err(QuerySourceError::Internal);
            }
        }
        let mut encoded = String::new();
        push_provenance(&mut encoded, cell, now_ns);
        if encoded.len() > MAX_PROVENANCE_OBJECT_BYTES {
            return Err(QuerySourceError::Internal);
        }
    }
    Ok(())
}

/// The number of represented provenance cells, checked.
#[must_use]
pub fn provenance_cells(meta: &SnapshotMeta) -> Option<usize> {
    meta.subjects.len().checked_mul(meta.columns.len())
}

/// Validate and bind the series view against its request.
pub fn validate_series_view(
    view: &SeriesView,
    request: &SeriesRequest,
    catalog: &TierCatalog,
    max_rows: u32,
) -> Result<(), QuerySourceError> {
    if catalog.step(view.tier) != Some(view.step_ms) {
        return Err(QuerySourceError::Internal);
    }
    let max = usize::try_from(max_rows).unwrap_or(usize::MAX);
    if view.subjects.len() > max {
        return Err(QuerySourceError::Budget(Pool::Rows));
    }
    if view
        .subjects
        .iter()
        .zip(view.subjects.iter().skip(1))
        .any(|(left, right)| left >= right)
    {
        return Err(QuerySourceError::Internal);
    }
    validate_columns(&view.columns, &request.selection.metrics())
}

/// The derived omitted set and dictionary metadata for a history request.
pub fn series_dictionary(
    view: &SeriesView,
    request: &SeriesRequest,
) -> Result<(Vec<u8>, Vec<String>, Vec<QueryColumnMeta>), QuerySourceError> {
    let mut permitted = Vec::new();
    let mut omitted = Vec::new();
    for column in &view.columns {
        if request
            .aggs
            .iter()
            .any(|agg| column.facts.aggregation_mask() & agg.bit() != 0)
        {
            permitted.push(column.clone());
        } else {
            omitted.push(column.metric.clone());
        }
    }
    let mut entries = Vec::with_capacity(view.subjects.len().saturating_add(permitted.len()));
    for &inc in &view.subjects {
        entries.push(Entry::Entity {
            inc,
            label: String::new(),
        });
    }
    entries.extend(permitted.iter().map(metric_entry));
    let body = tick::dictionary_body(1, &entries).map_err(|_| QuerySourceError::Internal)?;
    Ok((body, omitted, permitted))
}

fn align8(value: usize) -> Option<usize> {
    value.checked_add(7)?.checked_div(8)?.checked_mul(8)
}

fn columns_for_agg<'a>(
    permitted: &'a [QueryColumnMeta],
    agg: Aggregation,
) -> Vec<&'a QueryColumnMeta> {
    permitted
        .iter()
        .filter(|column| column.facts.aggregation_mask() & agg.bit() != 0)
        .collect()
}

/// The exact history-frame size used by the planner and the encoder.
#[must_use]
pub fn series_frame_len(
    buckets: u32,
    columns: usize,
    dictionary_body_len: Option<usize>,
) -> Option<usize> {
    let buckets = usize::try_from(buckets).ok()?;
    let mut sections = 3usize; // header, subject, time
    if dictionary_body_len.is_some() {
        sections = sections.checked_add(1)?;
    }
    sections = sections.checked_add(columns)?;
    let directory = sections.checked_mul(SECTION_DIR_ENTRY_LEN)?;
    let mut total = FRAME_HEADER_LEN
        .checked_add(4)?
        .checked_add(directory)?
        .checked_add(HEADER_BODY_LEN)?
        .checked_add(SUBJECT_BODY_LEN)?;
    let time_raw = buckets.checked_mul(15)?;
    total = total.checked_add(align8(time_raw)?)?;
    let column_raw = buckets.checked_mul(9)?.checked_add(8)?;
    total = total.checked_add(columns.checked_mul(align8(column_raw)?)?)?;
    if let Some(body_len) = dictionary_body_len {
        total = total.checked_add(body_len)?;
    }
    Some(total)
}

/// The exact frame length of one planned history BLOCK for its aggregation.
#[must_use]
pub fn series_plan_frame_len(
    buckets: u32,
    permitted: &[QueryColumnMeta],
    agg: Aggregation,
    dictionary_body_len: Option<usize>,
) -> Option<usize> {
    let columns = columns_for_agg(permitted, agg).len();
    if columns == 0 {
        None
    } else {
        series_frame_len(buckets, columns, dictionary_body_len)
    }
}

/// One planned page and its continuation position.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PagePlan {
    /// At most `max_blocks` plans retained for this request.
    pub blocks: Vec<BlockPlan>,
    /// First ordinal of the extra plan, when the page continues.
    pub continuation: Option<SeriesPosition>,
}

/// One snapshot request retained while waiting for exact Query room.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PendingSnapshot {
    /// Canonical request arguments.
    pub args: SnapshotArgs,
    /// Metadata-only source view.
    pub view: SnapshotView,
    /// Exact dictionary-bearing frame length.
    pub frame_bytes: usize,
}

/// One history request retained across page planning and Query admission.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PendingSeries {
    /// Canonical request arguments.
    pub args: SeriesArgs,
    /// The immutable source view.
    pub view: Arc<SeriesView>,
    /// Server-derived dictionary metrics.
    pub permitted: Vec<QueryColumnMeta>,
    /// Server-derived omitted metrics.
    pub omitted: Vec<String>,
    /// Exact series dictionary bytes.
    pub dictionary: Vec<u8>,
    /// The deterministic page plan.
    pub page: PagePlan,
    /// Next planned block to send.
    pub next_block: usize,
    /// Reserved successor token, exposed only by the terminal END.
    pub next_token: Option<String>,
    /// Whether this request created the successor reservation (rather than reusing an
    /// independently exposed token).
    pub next_token_owned: bool,
    /// Whether the success REP has been queued.
    pub rep_sent: bool,
    /// Whether this request owns a new active view reference.
    pub owns_active_ref: bool,
}

impl PendingSeries {
    /// The independently releasable view.
    #[must_use]
    pub fn view_id(&self) -> QueryViewId {
        QueryViewId::Series(self.view.id)
    }
}

impl PendingSnapshot {
    /// The independently releasable view.
    #[must_use]
    pub fn view_id(&self) -> QueryViewId {
        QueryViewId::Snapshot(self.view.id)
    }
}

fn series_group_facts(
    view: &SeriesView,
    request: &SeriesRequest,
    group: SeriesGroup,
    at: SeriesPosition,
) -> Result<((usize, usize), SeriesPosition), QuerySourceError> {
    if group.bucket_count == 0 {
        return Err(QuerySourceError::Internal);
    }
    let Some(subject_index) = view
        .subjects
        .iter()
        .position(|inc| *inc == group.subject_inc)
    else {
        return Err(QuerySourceError::Internal);
    };
    let Some(agg_index) = request.aggs.iter().position(|agg| *agg == group.agg) else {
        return Err(QuerySourceError::Internal);
    };
    let end = group
        .first
        .checked_add(group.bucket_count)
        .ok_or(QuerySourceError::Internal)?;
    if at < group.first || at >= end {
        return Err(QuerySourceError::Internal);
    }
    Ok(((subject_index, agg_index), end))
}

fn source_series_group<Q: QueryBackend + ?Sized>(
    backend: &Q,
    view: SeriesViewId,
    at: SeriesPosition,
) -> Result<Option<SeriesGroup>, QuerySourceError> {
    backend.series_group(view, at).map_err(|error| match error {
        QuerySourceError::Budget(Pool::Rows)
        | QuerySourceError::ViewInvalid
        | QuerySourceError::Internal => error,
        _ => QuerySourceError::Internal,
    })
}

/// Plan the largest deterministic page from `start`.
pub fn plan_series_page<Q: QueryBackend + ?Sized>(
    backend: &Q,
    view: &SeriesView,
    request: &SeriesRequest,
    permitted_columns: &[QueryColumnMeta],
    dictionary_len: usize,
    bounds: QueryBounds,
    max_blocks: u32,
    start: SeriesPosition,
) -> Result<PagePlan, QuerySourceError> {
    let mut blocks = Vec::new();
    let mut at = start;
    let mut first_in_request = true;
    if view.first.is_some_and(|first| start < first) {
        return Err(QuerySourceError::Internal);
    }
    let Some(view_first) = view.first else {
        if start != SeriesPosition(0) {
            return Err(QuerySourceError::Internal);
        }
        return match source_series_group(backend, view.id, SeriesPosition(0))? {
            None => Ok(PagePlan {
                blocks,
                continuation: None,
            }),
            Some(_) => Err(QuerySourceError::Internal),
        };
    };
    let mut prior_group = None;
    let mut prior_end = None;
    let mut terminal = false;
    while blocks.len() < usize::try_from(max_blocks).unwrap_or(usize::MAX) {
        let Some(group) = source_series_group(backend, view.id, at)? else {
            if blocks.is_empty() {
                return Err(QuerySourceError::Internal);
            }
            terminal = true;
            break;
        };
        let (order, end) = series_group_facts(view, request, group, at)?;
        if blocks.is_empty() && start == view_first && group.first != start {
            return Err(QuerySourceError::Internal);
        }
        if prior_group.is_some_and(|prior| {
            let same_group = prior == order && prior_end.is_some_and(|prior_end| prior_end > at);
            let next_group = prior < order && prior_end == Some(at) && group.first == at;
            !same_group && !next_group
        }) {
            return Err(QuerySourceError::Internal);
        }
        prior_group = Some(order);
        prior_end = Some(end);
        let planned_columns = columns_for_agg(permitted_columns, group.agg);
        if planned_columns.is_empty() {
            return Err(QuerySourceError::Internal);
        }
        if blocks.is_empty() && at != start {
            return Err(QuerySourceError::Internal);
        }
        let remaining = end.0.saturating_sub(at.0);
        let max_buckets = u64::from(
            bounds
                .max_block_buckets
                .min(u32::try_from(remaining).unwrap_or(u32::MAX)),
        );
        let mut low = 1u32;
        let mut high = u32::try_from(max_buckets).unwrap_or(u32::MAX);
        if high == 0 {
            return Err(QuerySourceError::Internal);
        }
        let limit = usize::try_from(bounds.max_block_bytes).unwrap_or(usize::MAX);
        let dictionary_body = first_in_request.then_some(dictionary_len);
        let first_size = series_plan_frame_len(1, permitted_columns, group.agg, dictionary_body)
            .ok_or(QuerySourceError::Internal)?;
        if first_size > limit {
            if blocks.is_empty() && first_in_request {
                return Err(QuerySourceError::Budget(Pool::Block));
            }
            return Ok(PagePlan {
                blocks,
                continuation: Some(at),
            });
        }
        while low < high {
            let mid = low.saturating_add((high - low).div_ceil(2));
            let fits = series_plan_frame_len(mid, permitted_columns, group.agg, dictionary_body)
                .is_some_and(|size| size <= limit);
            if fits {
                low = mid;
            } else {
                high = mid.saturating_sub(1);
            }
        }
        let buckets = low.min(u32::try_from(max_buckets).unwrap_or(u32::MAX));
        let size = series_plan_frame_len(buckets, permitted_columns, group.agg, dictionary_body)
            .ok_or(QuerySourceError::Internal)?;
        if size > limit {
            if blocks.is_empty() && first_in_request {
                return Err(QuerySourceError::Budget(Pool::Block));
            }
            break;
        }
        blocks.push(BlockPlan {
            subject_inc: group.subject_inc,
            agg: group.agg,
            start: at,
            buckets,
        });
        at = at
            .checked_add(u64::from(buckets))
            .ok_or(QuerySourceError::Internal)?;
        first_in_request = false;
        if at == end {
            continue;
        }
    }
    let continuation = if terminal {
        None
    } else if blocks.len() == usize::try_from(max_blocks).unwrap_or(usize::MAX) {
        match source_series_group(backend, view.id, at)? {
            Some(group) => {
                let (order, _end) = series_group_facts(view, request, group, at)?;
                let prior = prior_group.ok_or(QuerySourceError::Internal)?;
                let same_group =
                    prior == order && prior_end.is_some_and(|prior_end| prior_end > at);
                let next_group = prior < order && prior_end == Some(at) && group.first == at;
                if !same_group && !next_group {
                    return Err(QuerySourceError::Internal);
                }
                Some(at)
            }
            None => None,
        }
    } else if blocks.is_empty() && at == start {
        // The first one-bucket block did not fit; the budget refusal was handled above.
        None
    } else {
        Some(at)
    };
    Ok(PagePlan {
        blocks,
        continuation,
    })
}

fn parse_boot_id(text: &str) -> Option<[u8; 16]> {
    let mut bytes = [0u8; 16];
    let mut at = 0usize;
    for part in text.split('-') {
        if part.len() % 2 != 0 {
            return None;
        }
        for pair in part.as_bytes().chunks(2) {
            let high = u8::from_str_radix(core::str::from_utf8(pair.get(0..1)?).ok()?, 16).ok()?;
            let low = u8::from_str_radix(core::str::from_utf8(pair.get(1..2)?).ok()?, 16).ok()?;
            *bytes.get_mut(at)? = high.checked_mul(16)?.checked_add(low)?;
            at = at.checked_add(1)?;
        }
    }
    (at == 16).then_some(bytes)
}

/// Validate and encode one planned history block.
pub fn series_block(
    identity: &Identity,
    view: &SeriesView,
    request: &SeriesRequest,
    permitted: &[QueryColumnMeta],
    dictionary: Option<Vec<u8>>,
    plan: BlockPlan,
    data: HistoryBlockData,
) -> Result<Arc<[u8]>, QuerySourceError> {
    let expected_columns = columns_for_agg(permitted, plan.agg);
    if expected_columns.is_empty() {
        return Err(QuerySourceError::Internal);
    }
    if data.subject_inc != plan.subject_inc
        || data.agg != plan.agg
        || data.buckets.len() != usize::try_from(plan.buckets).unwrap_or(usize::MAX)
        || data.columns.len() != expected_columns.len()
    {
        return Err(QuerySourceError::Internal);
    }
    if data
        .buckets
        .iter()
        .any(|bucket| bucket.covered_ms > view.step_ms)
        || data.buckets.iter().any(|bucket| {
            bucket.bucket_real_ns < request.from_real_ns
                || bucket.bucket_real_ns >= request.to_real_ns
                || bucket
                    .bucket_real_ns
                    .rem_euclid(i64::from(view.step_ms) * 1_000_000)
                    != 0
        })
        || data
            .buckets
            .iter()
            .zip(data.buckets.iter().skip(1))
            .any(|(left, right)| left.bucket_real_ns >= right.bucket_real_ns)
    {
        return Err(QuerySourceError::Internal);
    }
    for (column, expected) in data.columns.iter().zip(expected_columns.iter()) {
        if column.metric != expected.metric
            || column.facts != expected.facts
            || column.value_type != expected.value_type
            || column.values.len() != data.buckets.len()
            || column.quality.len() != data.buckets.len()
            || expected.facts.aggregation_mask() & plan.agg.bit() == 0
        {
            return Err(QuerySourceError::Internal);
        }
        for (row, code) in column.quality.iter().copied().enumerate() {
            Quality::from_code(code, row).map_err(|_| QuerySourceError::Internal)?;
        }
        if column.value_type == MetricValueType::F64
            && column
                .values
                .iter()
                .any(|bits| !f64::from_bits(*bits).is_finite())
        {
            return Err(QuerySourceError::Internal);
        }
    }
    let subject_index = view
        .subjects
        .iter()
        .position(|inc| *inc == plan.subject_inc)
        .ok_or(QuerySourceError::Internal)?;
    let mut entries = Vec::with_capacity(view.subjects.len().saturating_add(permitted.len()));
    for &inc in &view.subjects {
        entries.push(Entry::Entity {
            inc,
            label: String::new(),
        });
    }
    entries.extend(permitted.iter().map(metric_entry));
    let dictionary_body_len = dictionary.as_ref().map(Vec::len);
    let dict = if let Some(body) = dictionary.as_deref() {
        let mut d = Dictionary::with_capacity(1, DEFAULT_ACTIVE_MAX);
        for (index, entry) in entries.iter().cloned().enumerate() {
            let got = d.intern(entry).map_err(|_| QuerySourceError::Internal)?;
            if got != u32::try_from(index).unwrap_or(u32::MAX) {
                return Err(QuerySourceError::Internal);
            }
        }
        let encoded = d.encode().map_err(|_| QuerySourceError::Internal)?;
        if encoded != body {
            return Err(QuerySourceError::Internal);
        }
        Some(d)
    } else {
        None
    };
    let first_bucket_real_ns = data_bucket_start(&data.buckets);
    let mut columns = Vec::with_capacity(data.columns.len());
    for column in data.columns {
        let permitted_index = permitted
            .iter()
            .position(|expected| expected.metric == column.metric)
            .ok_or(QuerySourceError::Internal)?;
        let metric = view
            .subjects
            .len()
            .checked_add(permitted_index)
            .and_then(|value| u32::try_from(value).ok())
            .ok_or(QuerySourceError::Internal)?;
        let expected = permitted
            .get(permitted_index)
            .ok_or(QuerySourceError::Internal)?;
        if expected.facts.aggregation_mask() & plan.agg.bit() == 0 {
            return Err(QuerySourceError::Internal);
        }
        columns.push(
            Column::new(
                metric,
                column.value_type,
                column.facts.kind,
                column.values,
                column.quality,
            )
            .map_err(|_| QuerySourceError::Internal)?,
        );
    }
    let time = data
        .buckets
        .into_iter()
        .map(|bucket| TimeBucket {
            real_ns: bucket.bucket_real_ns,
            covered_ms: bucket.covered_ms,
            samples: bucket.samples,
            agg: plan.agg,
        })
        .collect();
    let block = Block {
        frame_type: FrameType::Block,
        corr: 0,
        header: BlockHeaderFacts {
            boot_id: parse_boot_id(&identity.boot_id).ok_or(QuerySourceError::Internal)?,
            epoch: identity.epoch,
            seq: 0,
            mono_start_ns: 0,
            mono_end_ns: 0,
            boot_ns: 0,
            real_ns: first_bucket_real_ns,
            dict_gen: 1,
            n_rows: plan.buckets,
            dropped: 0,
            flags: 0,
        },
        rows: Vec::new(),
        time,
        columns,
        dictionary: dict,
        subject: Some(u32::try_from(subject_index).map_err(|_| QuerySourceError::Internal)?),
        skipped_sections: Vec::new(),
    };
    let encoded = encode_block(&block).map_err(|_| QuerySourceError::Internal)?;
    if encoded.len()
        != series_plan_frame_len(plan.buckets, permitted, plan.agg, dictionary_body_len)
            .ok_or(QuerySourceError::Internal)?
    {
        return Err(QuerySourceError::Internal);
    }
    Ok(Arc::from(encoded))
}

fn data_bucket_start(buckets: &[HistoryBucket]) -> i64 {
    buckets
        .first()
        .map(|bucket| bucket.bucket_real_ns)
        .unwrap_or(0)
}

/// Render a snapshot REP result.
#[must_use]
pub fn snapshot_result(
    meta: &SnapshotMeta,
    provenance: Option<&[CellProvenance]>,
    now_ns: u64,
) -> String {
    let mut out = format!(
        "{{\"resolved\":{{\"tier\":\"{}\",\"bucket_real_ns\":{},\"step_ms\":{},\"covered_ms\":{}}},\"provenance\":",
        meta.tier.name(),
        meta.bucket_real_ns,
        meta.step_ms,
        meta.covered_ms
    );
    match provenance {
        None => out.push_str("null"),
        Some(cells) => {
            out.push('[');
            for (index, cell) in cells.iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                push_provenance(&mut out, cell, now_ns);
            }
            out.push(']');
        }
    }
    out.push('}');
    out
}

fn push_optional_ref(out: &mut String, value: Option<&str>) {
    if let Some(text) = value {
        push_string(out, text);
    } else {
        out.push_str("null");
    }
}

fn push_optional_u64(out: &mut String, value: Option<u64>) {
    if let Some(value) = value {
        out.push_str(&value.to_string());
    } else {
        out.push_str("null");
    }
}

fn push_interval(out: &mut String, interval: Option<MonoInterval>) {
    if let Some(interval) = interval {
        out.push_str(&format!(
            "{{\"start\":{},\"end\":{}}}",
            interval.start, interval.end
        ));
    } else {
        out.push_str("null");
    }
}

fn push_number(out: &mut String, value: RawNumber) {
    match value {
        RawNumber::U64(value) => out.push_str(&value.to_string()),
        RawNumber::I64(value) => out.push_str(&value.to_string()),
        RawNumber::F64(value) => out.push_str(&format_f64(value)),
    }
}

fn format_f64(value: f64) -> String {
    if value == 0.0 {
        "0".to_string()
    } else {
        value.to_string()
    }
}

fn push_raw(out: &mut String, raw: &RawInput) {
    out.push_str("{\"metric\":");
    push_string(out, &raw.metric);
    out.push_str(",\"type\":");
    push_string(out, raw.value_type.name());
    out.push_str(",\"value\":");
    if let Some(value) = raw.value {
        push_number(out, value);
    } else {
        out.push_str("null");
    }
    out.push_str(&format!(",\"quality\":{},\"source\":", raw.quality.code()));
    push_string(out, raw.source.name());
    out.push_str(",\"unit\":");
    push_string(out, raw.unit.name());
    out.push_str(",\"source_ref\":");
    push_optional_ref(out, raw.source_ref.as_deref());
    out.push_str(",\"observed_mono_ns\":");
    push_optional_u64(out, raw.observed_mono_ns);
    out.push_str(",\"interval_mono_ns\":");
    push_interval(out, raw.interval_mono_ns);
    out.push('}');
}

fn push_constraint(out: &mut String, fact: &ConstraintFact) {
    out.push_str("{\"name\":");
    push_string(out, &fact.name);
    out.push_str(",\"type\":");
    match &fact.value {
        ConstraintValue::U64(_) => push_string(out, "u64"),
        ConstraintValue::I64(_) => push_string(out, "i64"),
        ConstraintValue::F64(_) => push_string(out, "f64"),
        ConstraintValue::String(_) => push_string(out, "string"),
    }
    out.push_str(",\"value\":");
    match &fact.value {
        ConstraintValue::U64(value) => out.push_str(&value.to_string()),
        ConstraintValue::I64(value) => out.push_str(&value.to_string()),
        ConstraintValue::F64(value) => out.push_str(&format_f64(*value)),
        ConstraintValue::String(value) => push_string(out, value),
    }
    out.push_str(",\"unit\":");
    if let Some(unit) = fact.unit {
        push_string(out, unit.name());
    } else {
        out.push_str("null");
    }
    out.push_str(",\"source_ref\":");
    push_optional_ref(out, fact.source_ref.as_deref());
    out.push_str(",\"observed_mono_ns\":");
    push_optional_u64(out, fact.observed_mono_ns);
    out.push('}');
}

fn push_provenance(out: &mut String, cell: &CellProvenance, now_ns: u64) {
    out.push_str("{\"inc\":");
    out.push_str(&cell.inc.to_string());
    out.push_str(",\"metric\":");
    push_string(out, &cell.metric);
    out.push_str(&format!(",\"quality\":{},\"source\":", cell.quality.code()));
    push_string(out, cell.source.name());
    out.push_str(",\"source_ref\":");
    push_optional_ref(out, cell.source_ref.as_deref());
    out.push_str(",\"observed_mono_ns\":");
    push_optional_u64(out, cell.observed_mono_ns);
    out.push_str(",\"interval_mono_ns\":");
    push_interval(out, cell.interval_mono_ns);
    out.push_str(",\"raw_inputs\":");
    match &cell.raw_inputs {
        None => out.push_str("null"),
        Some(items) => {
            out.push('[');
            for (index, raw) in items.iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                push_raw(out, raw);
            }
            out.push(']');
        }
    }
    out.push_str(",\"constraints\":");
    match &cell.constraints {
        None => out.push_str("null"),
        Some(items) => {
            out.push('[');
            for (index, fact) in items.iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                push_constraint(out, fact);
            }
            out.push(']');
        }
    }
    out.push_str(",\"age_ms\":");
    if let Some(observed) = cell.observed_mono_ns {
        let age = now_ns.saturating_sub(observed) / 1_000_000;
        out.push_str(&u32::try_from(age).unwrap_or(u32::MAX).to_string());
    } else {
        out.push_str("null");
    }
    out.push_str(",\"missing_reason\":");
    if cell.quality == Quality::Ok {
        out.push_str("null");
    } else {
        push_string(out, cell.quality.name());
    }
    out.push('}');
}

/// Render a history page REP result.
#[must_use]
pub fn series_result(view: &SeriesView, omitted: &[String]) -> String {
    let mut out = format!(
        "{{\"tier\":\"{}\",\"step_ms\":{},\"omitted\":[",
        view.tier.name(),
        view.step_ms
    );
    for (index, metric) in omitted.iter().enumerate() {
        if index > 0 {
            out.push(',');
        }
        push_string(&mut out, metric);
    }
    out.push_str("]}");
    out
}

/// Validate one process row against the request and previous key.
pub fn validate_proc_row(
    row: &ProcRow,
    fields: &[ProcField],
    previous: Option<(u32, u64)>,
) -> bool {
    if previous.is_some_and(|prior| prior >= (row.pid, row.start_ticks)) {
        return false;
    }
    if row
        .comm
        .as_deref()
        .is_some_and(|text| text.len() > MAX_COMM_BYTES)
    {
        return false;
    }
    if row
        .cmdline
        .as_deref()
        .is_some_and(|text| text.len() > MAX_CMDLINE_BYTES)
    {
        return false;
    }
    if row
        .state
        .is_some_and(|state| !state.is_ascii() || state == 0 || state == b'\n' || state == b'\r')
    {
        return false;
    }
    fields.iter().all(|field| *field != ProcField::Cmdline)
        || row
            .cmdline
            .as_deref()
            .is_none_or(|text| text.len() <= MAX_CMDLINE_BYTES)
}

fn push_opt_u32(out: &mut String, value: Option<u32>) {
    if let Some(value) = value {
        out.push_str(&value.to_string());
    } else {
        out.push_str("null");
    }
}

fn push_opt_u64_value(out: &mut String, value: Option<u64>) {
    if let Some(value) = value {
        out.push_str(&value.to_string());
    } else {
        out.push_str("null");
    }
}

fn push_opt_str(out: &mut String, value: Option<&str>) {
    if let Some(value) = value {
        push_string(out, value);
    } else {
        out.push_str("null");
    }
}

fn push_proc_row(out: &mut String, row: &ProcRow, fields: &[ProcField]) {
    out.push_str(&format!(
        "{{\"pid\":{},\"start_ticks\":{},\"inc\":{}",
        row.pid, row.start_ticks, row.inc
    ));
    for field in fields {
        out.push(',');
        push_string(out, field.name());
        out.push(':');
        match field {
            ProcField::Comm => push_opt_str(out, row.comm.as_deref()),
            ProcField::Ppid => push_opt_u32(out, row.ppid),
            ProcField::State => {
                if let Some(state) = row.state {
                    push_string(out, &char::from(state).to_string());
                } else {
                    out.push_str("null");
                }
            }
            ProcField::CpuUsec => push_opt_u64_value(out, row.cpu_usec),
            ProcField::RssBytes => push_opt_u64_value(out, row.rss_bytes),
            ProcField::IoRbytes => push_opt_u64_value(out, row.io_rbytes),
            ProcField::IoWbytes => push_opt_u64_value(out, row.io_wbytes),
            ProcField::Threads => push_opt_u64_value(out, row.threads),
            ProcField::Cmdline => push_opt_str(out, row.cmdline.as_deref()),
        }
    }
    out.push('}');
}

/// Render one bounded `procs` result.
#[must_use]
pub fn procs_result(
    rows: &[ProcRow],
    fields: &[ProcField],
    scan_age_ms: u32,
    view: &ProcScanView,
    next: Option<&str>,
) -> String {
    let mut out = String::from("{\"procs\":[");
    for (index, row) in rows.iter().enumerate() {
        if index > 0 {
            out.push(',');
        }
        push_proc_row(&mut out, row, fields);
    }
    out.push_str(&format!(
        "],\"coverage\":{{\"scanned\":{},\"total\":{},\"scan_age_ms\":{},\"complete\":{}}},\"next\":",
        view.scanned, view.total, scan_age_ms, view.complete
    ));
    if let Some(next) = next {
        push_string(&mut out, next);
    } else {
        out.push_str("null");
    }
    out.push('}');
    out
}

/// The canonical binding of one issued cursor.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CursorBinding {
    /// Serving boot.
    pub boot_id: String,
    /// Probe epoch.
    pub epoch: u64,
    /// Connection level.
    pub level: crate::limits::Level,
    /// Peer uid.
    pub uid: u32,
    /// Query verb.
    pub verb: &'static str,
    /// Canonical request arguments; page-size arguments are deliberately absent.
    pub arguments: Vec<u8>,
}

/// The exact position a cursor resumes.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CursorTarget {
    /// Continue a history view at this ordinal.
    Series {
        /// Pinned view metadata shared by every cursor on this view.
        view: Arc<SeriesView>,
        /// Next ordinal.
        position: SeriesPosition,
    },
    /// Continue a process projection after this row key.
    Procs {
        /// Pinned projection metadata.
        view: ProcScanView,
        /// Next row index.
        index: u32,
        /// Last emitted `(pid,start_ticks)`.
        last: Option<(u32, u64)>,
    },
}

impl CursorTarget {
    /// The independently releasable view.
    #[must_use]
    pub fn view(&self) -> QueryViewId {
        match self {
            CursorTarget::Series { view, .. } => QueryViewId::Series(view.id),
            CursorTarget::Procs { view, .. } => QueryViewId::Procs(view.id),
        }
    }
}

/// One successor token reservation. `created` is false when the token was already
/// independently exposed for this exact binding and target, so this request must not abort it.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CursorReservation {
    /// The canonical token.
    pub token: String,
    /// Whether this call created the token entry and its view reference.
    pub created: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct CursorRecord {
    binding: CursorBinding,
    target: CursorTarget,
    issued_ns: u64,
    owner: Option<(ConnId, u32)>,
}

/// Bounded cursor and view-reference accounting owned by the Server.
#[derive(Debug, Default)]
pub struct CursorTable {
    entries: BTreeMap<String, CursorRecord>,
    refs: BTreeMap<QueryViewId, u32>,
    next_token: u64,
}

impl CursorTable {
    /// Remove expired tokens and return views whose final reference went away.
    pub fn expire(&mut self, now_ns: u64) -> Vec<QueryViewId> {
        let expired: Vec<String> = self
            .entries
            .iter()
            .filter_map(|(token, entry)| {
                (now_ns.saturating_sub(entry.issued_ns) >= CURSOR_LIFETIME_NS)
                    .then_some(token.clone())
            })
            .collect();
        let mut release = Vec::new();
        for token in expired {
            if let Some(entry) = self.entries.remove(&token) {
                if self.release_view(entry.target.view()) {
                    release.push(entry.target.view());
                }
            }
        }
        release
    }

    /// Add one active view reference and return whether it was new.
    pub fn add_view(&mut self, view: QueryViewId) -> bool {
        let count = self.refs.entry(view).or_insert(0);
        *count = count.saturating_add(1);
        *count == 1
    }

    /// Release one active or cursor reference. Returns true when the last reference went away.
    pub fn release_view(&mut self, view: QueryViewId) -> bool {
        let Some(count) = self.refs.get_mut(&view) else {
            return false;
        };
        *count = count.saturating_sub(1);
        if *count == 0 {
            self.refs.remove(&view);
            true
        } else {
            false
        }
    }

    /// The remaining references of a view.
    #[must_use]
    pub fn references(&self, view: QueryViewId) -> u32 {
        self.refs.get(&view).copied().unwrap_or(0)
    }

    /// Number of current cursor entries.
    #[must_use]
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// Whether the table is empty.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// Reserve a cursor for the target, reusing an identical already-issued successor.
    pub fn reserve(
        &mut self,
        binding: &CursorBinding,
        target: CursorTarget,
        owner: Option<(ConnId, u32)>,
        now_ns: u64,
    ) -> Result<CursorReservation, QuerySourceError> {
        if binding.arguments.len() > MAX_CURSOR_BINDING_BYTES {
            return Err(QuerySourceError::Internal);
        }
        if let Some((token, _)) = self.entries.iter().find(|(_, entry)| {
            entry.binding == *binding
                && entry.target == target
                && entry.owner.is_none()
                && now_ns.saturating_sub(entry.issued_ns) < CURSOR_LIFETIME_NS
        }) {
            return Ok(CursorReservation {
                token: token.clone(),
                created: false,
            });
        }
        if self.entries.len() >= MAX_CURSOR_ENTRIES {
            return Err(QuerySourceError::Budget(Pool::Cursors));
        }
        let token = loop {
            self.next_token = self.next_token.wrapping_add(1);
            let candidate = format!("c{:016x}", self.next_token);
            if !self.entries.contains_key(&candidate) {
                break candidate;
            }
        };
        self.add_view(target.view());
        self.entries.insert(
            token.clone(),
            CursorRecord {
                binding: binding.clone(),
                target,
                issued_ns: now_ns,
                owner,
            },
        );
        Ok(CursorReservation {
            token,
            created: true,
        })
    }

    /// Look up and validate a token for this exact binding.
    pub fn lookup(
        &self,
        token: &str,
        binding: &CursorBinding,
        now_ns: u64,
    ) -> Result<CursorTarget, QuerySourceError> {
        let entry = self
            .entries
            .get(token)
            .filter(|entry| entry.binding == *binding)
            .ok_or(QuerySourceError::ViewInvalid)?;
        if now_ns.saturating_sub(entry.issued_ns) >= CURSOR_LIFETIME_NS {
            return Err(QuerySourceError::ViewInvalid);
        }
        Ok(entry.target.clone())
    }

    /// Expose a reserved successor and release its request ownership.
    pub fn expose(&mut self, token: &str) -> bool {
        let Some(entry) = self.entries.get_mut(token) else {
            return false;
        };
        entry.owner = None;
        true
    }

    /// Remove one unexposed reservation.
    pub fn abort_reservation(&mut self, token: &str) -> Option<QueryViewId> {
        if let Some(entry) = self.entries.remove(token) {
            if self.release_view(entry.target.view()) {
                return Some(entry.target.view());
            }
        }
        None
    }

    /// Remove every unexposed cursor owned by a connection.
    pub fn abort_conn(&mut self, conn: ConnId) -> Vec<QueryViewId> {
        let tokens: Vec<String> = self
            .entries
            .iter()
            .filter_map(|(token, entry)| {
                (entry.owner.is_some_and(|(owner, _corr)| owner == conn)).then_some(token.clone())
            })
            .collect();
        let mut release = Vec::new();
        for token in tokens {
            if let Some(entry) = self.entries.remove(&token) {
                if self.release_view(entry.target.view()) {
                    release.push(entry.target.view());
                }
            }
        }
        release
    }

    /// Invalidate every cursor on a view and return refs that need source release.
    pub fn invalidate(&mut self, view: QueryViewId) -> Vec<QueryViewId> {
        let tokens: Vec<String> = self
            .entries
            .iter()
            .filter_map(|(token, entry)| (entry.target.view() == view).then_some(token.clone()))
            .collect();
        for token in &tokens {
            self.entries.remove(token);
        }
        let mut release = Vec::new();
        if let Some(count) = self.refs.remove(&view) {
            if count > tokens.len() as u32 {
                // One active request still owns the view; it will observe ViewInvalid.
                self.refs.insert(view, count - tokens.len() as u32);
            } else {
                release.push(view);
            }
        }
        release
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use hostobs_proto::dict::{
        Entry, MetricFacts, MetricKind, MetricScope, MetricSource, MetricUnit, MetricValueType,
    };
    use serde_json::Value;

    use super::{
        bucket_floor, canonical_procs_arguments, canonical_series_arguments, plan_series_page,
        procs_open_error, procs_read_error, series_block, series_dictionary, series_frame_len,
        series_group_error, series_open_error, series_plan_frame_len, series_read_end_error,
        snapshot_open_error, snapshot_read_error, validate_proc_row, validate_provenance,
        validate_snapshot_data, validate_snapshot_meta, CellProvenance, ConstraintFact,
        ConstraintValue, CursorBinding, CursorTable, CursorTarget, HistoryBlockData, HistoryBucket,
        HistoryColumn, ProcField, ProcProjectionId, ProcRow, ProcScanId, ProcScanView,
        ProcsRequest, QueryColumnMeta, QuerySourceError, QueryViewId, RawInput, RawNumber,
        SeriesArgs, SeriesGroup, SeriesPosition, SeriesRequest, SeriesView, SeriesViewId,
        SnapshotData, SnapshotMeta, SnapshotRequest, SnapshotSubjectMeta, Tier, TierCatalog,
        TierCatalogEntry, TierChoice, MAX_CURSOR_ENTRIES,
    };
    use crate::backend::{FixedBackend, Identity, TickData};
    use crate::codes::{Code, Detail, Pool};
    use crate::limits::Level;
    use crate::selection::{Sel, Selection};
    use hostobs_proto::hub::ConnId;

    const STEP_NS: i64 = 1_000_000_000;

    fn catalog() -> TierCatalog {
        TierCatalog {
            entries: vec![TierCatalogEntry {
                tier: Tier::Hot,
                step_ms: 1_000,
            }],
        }
    }

    fn facts(kind: MetricKind) -> MetricFacts {
        MetricFacts {
            unit: MetricUnit::None,
            kind,
            scope: MetricScope::Cgroup,
            source: MetricSource::CgroupFs,
        }
    }

    fn column(name: &str, kind: MetricKind) -> QueryColumnMeta {
        QueryColumnMeta {
            metric: name.to_string(),
            facts: facts(kind),
            value_type: MetricValueType::U64,
        }
    }

    fn request() -> SeriesRequest {
        SeriesRequest {
            selection: Selection::new(
                vec![Sel::Inc(7), Sel::Inc(9)],
                vec!["m_a".to_string(), "m_b".to_string(), "m_never".to_string()],
            )
            .expect("selection"),
            from_real_ns: 0,
            to_real_ns: 4 * STEP_NS,
            step_ms: 1_000,
            aggs: vec![
                hostobs_proto::dict::Aggregation::Last,
                hostobs_proto::dict::Aggregation::Mean,
            ],
        }
    }

    fn view(id: u64) -> SeriesView {
        SeriesView {
            id: SeriesViewId(id),
            tier: Tier::Hot,
            step_ms: 1_000,
            subjects: vec![7, 9],
            columns: vec![
                column("m_a", MetricKind::Gauge),
                column("m_b", MetricKind::State),
                column("m_never", MetricKind::Counter),
            ],
            first: Some(SeriesPosition(0)),
        }
    }

    fn groups() -> Vec<SeriesGroup> {
        use hostobs_proto::dict::Aggregation;
        vec![
            SeriesGroup {
                subject_inc: 7,
                agg: Aggregation::Last,
                first: SeriesPosition(0),
                bucket_count: 3,
            },
            SeriesGroup {
                subject_inc: 7,
                agg: Aggregation::Mean,
                first: SeriesPosition(3),
                bucket_count: 1,
            },
            SeriesGroup {
                subject_inc: 9,
                agg: Aggregation::Last,
                first: SeriesPosition(4),
                bucket_count: 1,
            },
            SeriesGroup {
                subject_inc: 9,
                agg: Aggregation::Mean,
                first: SeriesPosition(5),
                bucket_count: 1,
            },
        ]
    }

    fn planning_backend(view: &SeriesView) -> FixedBackend {
        let mut backend = FixedBackend::new();
        for at in 0..=5u64 {
            let group = groups()
                .into_iter()
                .find(|group| group.first.0 <= at && at < group.first.0 + group.bucket_count)
                .expect("covered ordinal");
            backend
                .series_group
                .insert((view.id, SeriesPosition(at)), Ok(Some(group)));
        }
        backend
            .series_group
            .insert((view.id, SeriesPosition(6)), Ok(None));
        backend
    }

    fn fixture() -> Value {
        serde_json::from_str(include_str!("../tests/fixtures/p5b3-series-plans.json"))
            .expect("fixture")
    }

    #[test]
    fn checked_euclidean_floor_and_catalog_are_fact_driven() {
        assert_eq!(bucket_floor(-1, 1_000), Some(-1_000_000_000));
        assert_eq!(bucket_floor(i64::MAX, 1_000), None);
        assert_eq!(catalog().step(Tier::Hot), Some(1_000));
        assert!(catalog().has_step(1_000));
        assert!(!catalog().has_step(0));

        let mut status = crate::backend::StatusInfo::default();
        status.tiers.push(crate::backend::TierInfo {
            name: "cold".to_string(),
            step_ms: 10_000,
            keep_s: 1,
            bytes: 0,
            cap_bytes: 0,
        });
        status.tiers.push(crate::backend::TierInfo {
            name: "hot".to_string(),
            step_ms: 1_000,
            keep_s: 1,
            bytes: 0,
            cap_bytes: 0,
        });
        let built = TierCatalog::from_status(&status);
        assert_eq!(
            built.entries(),
            &[
                TierCatalogEntry {
                    tier: Tier::Hot,
                    step_ms: 1_000,
                },
                TierCatalogEntry {
                    tier: Tier::Cold,
                    step_ms: 10_000,
                },
            ]
        );
    }

    #[test]
    fn shared_step_uses_a_configured_covering_tier_without_splicing() {
        let mut status = crate::backend::StatusInfo::default();
        for (name, step_ms) in [("hot", 1_000), ("warm", 2_000)] {
            status.tiers.push(crate::backend::TierInfo {
                name: name.to_string(),
                step_ms,
                keep_s: 1,
                bytes: 0,
                cap_bytes: 0,
            });
        }
        let catalog = TierCatalog::from_status(&status);
        assert_eq!(catalog.step(Tier::Hot), Some(1_000));
        assert_eq!(catalog.step(Tier::Warm), Some(2_000));
        assert!(catalog.has_step(2_000));
        assert!(!catalog.has_step(1_500));

        let request = SeriesRequest {
            selection: Selection::new(vec![Sel::Inc(7)], vec!["m_a".to_string()])
                .expect("selection"),
            from_real_ns: -1,
            to_real_ns: 2_000_000_000,
            step_ms: 2_000,
            aggs: vec![hostobs_proto::dict::Aggregation::Last],
        };
        let mut warm = view(6);
        warm.tier = Tier::Warm;
        warm.step_ms = 2_000;
        warm.subjects = vec![7];
        warm.columns = vec![column("m_a", MetricKind::Gauge)];
        super::validate_series_view(&warm, &request, &catalog, 4_096).expect("warm view");

        warm.step_ms = 1_500;
        assert_eq!(
            super::validate_series_view(&warm, &request, &catalog, 4_096),
            Err(QuerySourceError::Internal)
        );
    }

    #[test]
    fn frame_sizes_match_the_independent_carver_artifact() {
        let fixture = fixture();
        let sizes = &fixture["independent_frame_sizes"];
        assert_eq!(
            series_frame_len(1, 3, Some(80)),
            Some(
                usize::try_from(
                    sizes["dictionary_first_one_bucket_three_columns"]
                        .as_u64()
                        .expect("one")
                )
                .expect("usize")
            )
        );
        assert_eq!(
            series_frame_len(2, 3, Some(80)),
            Some(
                usize::try_from(
                    sizes["dictionary_first_two_buckets_three_columns"]
                        .as_u64()
                        .expect("two")
                )
                .expect("usize")
            )
        );
        assert_eq!(
            series_frame_len(2, 3, None),
            Some(
                usize::try_from(
                    sizes["bare_two_buckets_three_columns"]
                        .as_u64()
                        .expect("bare")
                )
                .expect("usize")
            )
        );
        assert_eq!(series_frame_len(1, 1, Some(80)), Some(264));
    }

    #[test]
    fn largest_prefix_plan_and_replay_match_the_frozen_fixture() {
        let fixture = fixture();
        let request = request();
        let view = view(1);
        let backend = planning_backend(&view);
        let (dictionary, omitted, permitted) =
            series_dictionary(&view, &request).expect("dictionary");
        assert_eq!(
            fixture["dictionary"]["encoded_body_bytes"]
                .as_u64()
                .map(|value| usize::try_from(value).expect("usize")),
            Some(dictionary.len())
        );
        assert!(omitted.is_empty());

        let bounds = super::QueryBounds {
            max_rows: 4_096,
            max_block_buckets: 4,
            max_block_bytes: 328,
        };
        let page = plan_series_page(
            &backend,
            &view,
            &request,
            &permitted,
            dictionary.len(),
            bounds,
            2,
            SeriesPosition(0),
        )
        .expect("page");
        assert_eq!(page.continuation, Some(SeriesPosition(3)));
        assert_eq!(
            page.blocks,
            vec![
                super::BlockPlan {
                    subject_inc: 7,
                    agg: hostobs_proto::dict::Aggregation::Last,
                    start: SeriesPosition(0),
                    buckets: 1,
                },
                super::BlockPlan {
                    subject_inc: 7,
                    agg: hostobs_proto::dict::Aggregation::Last,
                    start: SeriesPosition(1),
                    buckets: 2,
                },
            ]
        );

        let replay_one = plan_series_page(
            &backend,
            &view,
            &request,
            &permitted,
            dictionary.len(),
            bounds,
            1,
            SeriesPosition(3),
        )
        .expect("one-block replay");
        assert_eq!(replay_one.blocks.len(), 1);
        assert_eq!(replay_one.blocks[0].buckets, 1);
        assert_eq!(replay_one.continuation, Some(SeriesPosition(4)));

        let replay_two = plan_series_page(
            &backend,
            &view,
            &request,
            &permitted,
            dictionary.len(),
            bounds,
            2,
            SeriesPosition(3),
        )
        .expect("two-block replay");
        assert_eq!(replay_two.blocks.len(), 2);
        assert_eq!(replay_two.blocks[1].start, SeriesPosition(4));
        assert_eq!(replay_two.continuation, Some(SeriesPosition(5)));

        let expected = &fixture["expected_first_page"];
        assert_eq!(expected["end_reason"].as_str(), Some("page"));
        assert_eq!(
            expected["next_position"]["bucket_ordinal"].as_u64(),
            Some(3)
        );
    }

    #[test]
    fn omitted_metric_is_derived_from_the_mask_and_not_data_presence() {
        let request = SeriesRequest {
            selection: Selection::new(vec![Sel::Inc(7)], vec!["m_never".to_string()])
                .expect("selection"),
            from_real_ns: 0,
            to_real_ns: STEP_NS,
            step_ms: 1_000,
            aggs: vec![hostobs_proto::dict::Aggregation::Mean],
        };
        let mut view = view(2);
        view.subjects = vec![7];
        view.columns = vec![column("m_never", MetricKind::Counter)];
        let (dictionary, omitted, permitted) =
            series_dictionary(&view, &request).expect("dictionary");
        assert_eq!(omitted, vec!["m_never".to_string()]);
        assert!(permitted.is_empty());
        let entries = Entry::Entity {
            inc: 7,
            label: String::new(),
        };
        let expected = crate::tick::dictionary_body(1, &[entries]).expect("entity-only");
        assert_eq!(dictionary, expected);
    }

    #[test]
    fn malformed_group_order_and_zero_groups_are_internal() {
        let request = request();
        let view = view(3);
        let mut backend = planning_backend(&view);
        backend.series_group.insert(
            (view.id, SeriesPosition(0)),
            Ok(Some(SeriesGroup {
                subject_inc: 7,
                agg: hostobs_proto::dict::Aggregation::Last,
                first: SeriesPosition(0),
                bucket_count: 0,
            })),
        );
        let bounds = super::QueryBounds {
            max_rows: 4_096,
            max_block_buckets: 4,
            max_block_bytes: 328,
        };
        assert_eq!(
            plan_series_page(
                &backend,
                &view,
                &request,
                &view.columns,
                80,
                bounds,
                2,
                SeriesPosition(0),
            ),
            Err(QuerySourceError::Internal)
        );
    }

    #[test]
    fn first_position_and_empty_view_metadata_are_structurally_checked() {
        let request = request();
        let bounds = super::QueryBounds {
            max_rows: 4_096,
            max_block_buckets: 4,
            max_block_bytes: 328,
        };
        let mut empty = view(4);
        empty.first = None;
        let mut backend = FixedBackend::new();
        backend
            .series_group
            .insert((empty.id, SeriesPosition(0)), Ok(Some(groups()[0])));
        assert_eq!(
            plan_series_page(
                &backend,
                &empty,
                &request,
                &empty.columns,
                80,
                bounds,
                2,
                SeriesPosition(0),
            ),
            Err(QuerySourceError::Internal)
        );
        backend
            .series_group
            .insert((empty.id, SeriesPosition(0)), Ok(None));
        assert_eq!(
            plan_series_page(
                &backend,
                &empty,
                &request,
                &empty.columns,
                80,
                bounds,
                2,
                SeriesPosition(0),
            ),
            Ok(super::PagePlan {
                blocks: Vec::new(),
                continuation: None,
            })
        );

        let mut later = view(5);
        later.first = Some(SeriesPosition(1));
        let mut backend = planning_backend(&later);
        backend.series_group.insert(
            (later.id, SeriesPosition(1)),
            Ok(Some(SeriesGroup {
                subject_inc: 7,
                agg: hostobs_proto::dict::Aggregation::Last,
                first: SeriesPosition(0),
                bucket_count: 3,
            })),
        );
        assert_eq!(
            plan_series_page(
                &backend,
                &later,
                &request,
                &later.columns,
                80,
                bounds,
                2,
                SeriesPosition(1),
            ),
            Err(QuerySourceError::Internal)
        );
    }

    #[test]
    fn source_outcomes_map_by_method_and_phase() {
        let cases = [
            (
                snapshot_open_error(QuerySourceError::NotFound),
                (Code::NotFound, Detail::Empty),
            ),
            (
                snapshot_open_error(QuerySourceError::DataGap),
                (Code::DataGap, Detail::Empty),
            ),
            (
                snapshot_open_error(QuerySourceError::BucketOutOfRange),
                (Code::BadRequest, Detail::key("real_ns")),
            ),
            (
                snapshot_open_error(QuerySourceError::Unsupported(
                    super::QueryFeature::Provenance,
                )),
                (Code::UnsupportedFeature, Detail::Feature("provenance")),
            ),
            (
                snapshot_open_error(QuerySourceError::Budget(Pool::Rows)),
                (Code::Budget, Detail::Pool(Pool::Rows)),
            ),
            (
                snapshot_open_error(QuerySourceError::Budget(Pool::Procs)),
                (Code::Internal, Detail::Empty),
            ),
            (
                snapshot_read_error(QuerySourceError::Budget(Pool::Rows)),
                (Code::Budget, Detail::Pool(Pool::Rows)),
            ),
            (
                snapshot_read_error(QuerySourceError::ViewInvalid),
                (Code::Internal, Detail::Empty),
            ),
            (
                series_open_error(QuerySourceError::NotFound, false),
                (Code::NotFound, Detail::Empty),
            ),
            (
                series_open_error(QuerySourceError::Budget(Pool::Rows), false),
                (Code::Budget, Detail::Pool(Pool::Rows)),
            ),
            (
                series_open_error(QuerySourceError::Budget(Pool::Procs), false),
                (Code::Internal, Detail::Empty),
            ),
            (
                series_group_error(QuerySourceError::NotFound, true),
                (Code::Internal, Detail::Empty),
            ),
            (
                series_group_error(QuerySourceError::ViewInvalid, true),
                (Code::CursorInvalid, Detail::Empty),
            ),
            (
                series_group_error(QuerySourceError::ViewInvalid, false),
                (Code::Internal, Detail::Empty),
            ),
            (
                procs_open_error(QuerySourceError::NotFound),
                (Code::NotFound, Detail::Empty),
            ),
            (
                procs_open_error(QuerySourceError::Budget(Pool::Procs)),
                (Code::Budget, Detail::Pool(Pool::Procs)),
            ),
            (
                procs_open_error(QuerySourceError::Budget(Pool::Rows)),
                (Code::Internal, Detail::Empty),
            ),
            (
                procs_read_error(QuerySourceError::ViewInvalid, true),
                (Code::CursorInvalid, Detail::Empty),
            ),
            (
                procs_read_error(QuerySourceError::ViewInvalid, false),
                (Code::Internal, Detail::Empty),
            ),
            (
                series_read_end_error(QuerySourceError::ViewInvalid),
                (Code::CursorInvalid, Detail::Empty),
            ),
            (
                series_read_end_error(QuerySourceError::Budget(Pool::Rows)),
                (Code::Budget, Detail::Pool(Pool::Rows)),
            ),
            (
                series_read_end_error(QuerySourceError::NotFound),
                (Code::Internal, Detail::Empty),
            ),
        ];
        for (actual, expected) in cases {
            assert_eq!(actual, expected);
        }
    }

    #[test]
    fn snapshot_metadata_checks_subject_and_column_sets_independently() {
        let request = SnapshotRequest {
            selection: Selection::new(vec![Sel::Inc(7)], vec!["cpu".to_string()])
                .expect("selection"),
            real_ns: 0,
            tier: TierChoice::Explicit(Tier::Hot),
            provenance: false,
        };
        let mut meta = SnapshotMeta {
            tier: Tier::Hot,
            bucket_real_ns: 0,
            step_ms: 1_000,
            covered_ms: 1_000,
            subjects: vec![
                SnapshotSubjectMeta {
                    inc: 7,
                    label_len: 0,
                },
                SnapshotSubjectMeta {
                    inc: 9,
                    label_len: 0,
                },
            ],
            columns: vec![column("cpu", MetricKind::Gauge)],
        };
        validate_snapshot_meta(&meta, &request, &catalog(), 4_096).expect("metadata");

        meta.subjects[1].inc = 7;
        assert_eq!(
            validate_snapshot_meta(&meta, &request, &catalog(), 4_096),
            Err(QuerySourceError::Internal)
        );
        meta.subjects[1].inc = 9;
        meta.subjects.swap(0, 1);
        assert_eq!(
            validate_snapshot_meta(&meta, &request, &catalog(), 4_096),
            Err(QuerySourceError::Internal)
        );
        meta.subjects.swap(0, 1);
        meta.columns.clear();
        assert_eq!(
            validate_snapshot_meta(&meta, &request, &catalog(), 4_096),
            Err(QuerySourceError::Internal)
        );
        meta.columns = vec![
            column("cpu", MetricKind::Gauge),
            column("other", MetricKind::Gauge),
        ];
        assert_eq!(
            validate_snapshot_meta(&meta, &request, &catalog(), 4_096),
            Err(QuerySourceError::Internal)
        );
        meta.columns = vec![
            column("cpu", MetricKind::Gauge),
            column("cpu", MetricKind::Gauge),
        ];
        assert_eq!(
            validate_snapshot_meta(&meta, &request, &catalog(), 4_096),
            Err(QuerySourceError::Internal)
        );
    }

    #[test]
    fn series_view_column_set_must_match_the_request() {
        let request = request();
        let view = view(12);
        super::validate_series_view(&view, &request, &catalog(), 4_096).expect("series view");
        let cases = [
            vec![view.columns[0].clone(), view.columns[1].clone()],
            vec![
                view.columns[0].clone(),
                view.columns[1].clone(),
                view.columns[2].clone(),
                column("m_extra", MetricKind::Gauge),
            ],
            vec![
                view.columns[0].clone(),
                view.columns[1].clone(),
                view.columns[1].clone(),
            ],
        ];
        for columns in cases {
            let mut malformed = view.clone();
            malformed.columns = columns;
            assert_eq!(
                super::validate_series_view(&malformed, &request, &catalog(), 4_096),
                Err(QuerySourceError::Internal)
            );
        }
    }

    #[test]
    fn snapshot_metadata_and_candidate_validate_independently() {
        let request = SnapshotRequest {
            selection: Selection::new(vec![Sel::Inc(7)], vec!["cpu".to_string()])
                .expect("selection"),
            real_ns: -1,
            tier: TierChoice::Explicit(Tier::Hot),
            provenance: false,
        };
        let meta = SnapshotMeta {
            tier: Tier::Hot,
            bucket_real_ns: -1_000_000_000,
            step_ms: 1_000,
            covered_ms: 700,
            subjects: vec![SnapshotSubjectMeta {
                inc: 7,
                label_len: 2,
            }],
            columns: vec![column("cpu", MetricKind::Gauge)],
        };
        validate_snapshot_meta(&meta, &request, &catalog(), 4_096).expect("metadata");

        let mut data = TickData::example(1, &[7], &["cpu"]);
        data.header.real_ns = meta.bucket_real_ns;
        data.entries[0] = Entry::Entity {
            inc: 7,
            label: "é".to_string(),
        };
        data.entries[1] = Entry::Metric {
            facts: meta.columns[0].facts,
            name: "cpu".to_string(),
        };
        data.columns[0].metric = 1;
        data.columns[0].kind = MetricKind::Gauge;
        data.columns[0].value_type = MetricValueType::U64;
        validate_snapshot_data(
            &meta,
            &SnapshotData {
                tick: data.clone(),
                provenance: None,
            },
            &request,
            10,
        )
        .expect("candidate");

        data.columns[0].metric = 0;
        assert!(validate_snapshot_data(
            &meta,
            &SnapshotData {
                tick: data,
                provenance: None,
            },
            &request,
            10,
        )
        .is_err());

        let mut bad_meta = meta.clone();
        bad_meta.subjects[0].label_len = 257;
        assert!(validate_snapshot_meta(&bad_meta, &request, &catalog(), 4_096).is_err());
    }

    #[test]
    fn provenance_serialization_is_exact_and_ordered() {
        let meta = SnapshotMeta {
            tier: Tier::Hot,
            bucket_real_ns: 0,
            step_ms: 1_000,
            covered_ms: 1_000,
            subjects: vec![SnapshotSubjectMeta {
                inc: 7,
                label_len: 0,
            }],
            columns: vec![column("cpu", MetricKind::Gauge)],
        };
        let cell = CellProvenance {
            inc: 7,
            metric: "cpu".to_string(),
            quality: hostobs_proto::quality::Quality::Ok,
            source: MetricSource::CgroupFs,
            source_ref: Some("cgroup.cpu".to_string()),
            observed_mono_ns: Some(5_000_000),
            interval_mono_ns: Some(super::MonoInterval { start: 1, end: 2 }),
            raw_inputs: None,
            constraints: Some(vec![super::ConstraintFact {
                name: "mode".to_string(),
                value: ConstraintValue::String("shared".to_string()),
                unit: None,
                source_ref: None,
                observed_mono_ns: None,
            }]),
        };
        let result = super::snapshot_result(&meta, Some(&[cell]), 8_000_000);
        assert!(result.starts_with(
            "{\"resolved\":{\"tier\":\"hot\",\"bucket_real_ns\":0,\"step_ms\":1000,\"covered_ms\":1000},\"provenance\":[{\"inc\":7"
        ));
        assert!(result.contains("\"age_ms\":3,"));
        assert!(result.ends_with("}]}"));
    }

    #[test]
    fn provenance_validation_rejects_each_malformed_fact() {
        let meta = SnapshotMeta {
            tier: Tier::Hot,
            bucket_real_ns: 0,
            step_ms: 1_000,
            covered_ms: 1_000,
            subjects: vec![SnapshotSubjectMeta {
                inc: 7,
                label_len: 0,
            }],
            columns: vec![column("cpu", MetricKind::Gauge)],
        };
        let good = CellProvenance {
            inc: 7,
            metric: "cpu".to_string(),
            quality: hostobs_proto::quality::Quality::Ok,
            source: MetricSource::CgroupFs,
            source_ref: Some("cgroup.cpu".to_string()),
            observed_mono_ns: Some(5),
            interval_mono_ns: Some(super::MonoInterval { start: 1, end: 2 }),
            raw_inputs: Some(vec![RawInput {
                metric: "cpu".to_string(),
                value_type: MetricValueType::U64,
                value: Some(RawNumber::U64(1)),
                quality: hostobs_proto::quality::Quality::Ok,
                source: MetricSource::CgroupFs,
                unit: MetricUnit::None,
                source_ref: Some("cgroup.cpu".to_string()),
                observed_mono_ns: Some(5),
                interval_mono_ns: Some(super::MonoInterval { start: 1, end: 2 }),
            }]),
            constraints: Some(vec![ConstraintFact {
                name: "mode".to_string(),
                value: ConstraintValue::String("shared".to_string()),
                unit: None,
                source_ref: None,
                observed_mono_ns: Some(5),
            }]),
        };
        assert_eq!(
            validate_provenance(&meta, Some(&[good.clone()]), 10),
            Ok(())
        );

        let cases = [
            vec![good.clone(), good.clone()],
            vec![CellProvenance {
                inc: 8,
                ..good.clone()
            }],
            vec![CellProvenance {
                observed_mono_ns: Some(11),
                ..good.clone()
            }],
            vec![CellProvenance {
                raw_inputs: Some(vec![RawInput {
                    value_type: MetricValueType::I64,
                    ..good.raw_inputs.as_ref().expect("raw")[0].clone()
                }]),
                ..good.clone()
            }],
            vec![CellProvenance {
                raw_inputs: Some(vec![RawInput {
                    value_type: MetricValueType::F64,
                    value: Some(RawNumber::F64(f64::INFINITY)),
                    ..good.raw_inputs.as_ref().expect("raw")[0].clone()
                }]),
                ..good.clone()
            }],
            vec![CellProvenance {
                constraints: Some(vec![ConstraintFact {
                    value: ConstraintValue::F64(f64::NAN),
                    ..good.constraints.as_ref().expect("constraints")[0].clone()
                }]),
                ..good.clone()
            }],
        ];
        for cells in cases {
            assert_eq!(
                validate_provenance(&meta, Some(&cells), 10),
                Err(QuerySourceError::Internal)
            );
        }
        assert_eq!(
            validate_provenance(&meta, None, 10),
            Err(QuerySourceError::Internal)
        );
    }

    #[test]
    fn snapshot_candidate_rejects_dictionary_column_and_value_shape_mismatches() {
        let meta = SnapshotMeta {
            tier: Tier::Hot,
            bucket_real_ns: 0,
            step_ms: 1_000,
            covered_ms: 1_000,
            subjects: vec![SnapshotSubjectMeta {
                inc: 7,
                label_len: 0,
            }],
            columns: vec![
                column("cpu", MetricKind::Gauge),
                column("mem", MetricKind::Gauge),
            ],
        };
        let request = SnapshotRequest {
            selection: Selection::new(
                vec![Sel::Inc(7)],
                vec!["cpu".to_string(), "mem".to_string()],
            )
            .expect("selection"),
            real_ns: 0,
            tier: TierChoice::Explicit(Tier::Hot),
            provenance: false,
        };
        let mut data = TickData::example(1, &[7], &["cpu", "mem"]);
        data.header.real_ns = 0;
        data.entries[0] = Entry::Entity {
            inc: 7,
            label: String::new(),
        };
        for (index, expected) in meta.columns.iter().enumerate() {
            data.entries[index + 1] = Entry::Metric {
                facts: expected.facts,
                name: expected.metric.clone(),
            };
            data.columns[index].metric = u32::try_from(index + 1).expect("metric index");
            data.columns[index].kind = expected.facts.kind;
            data.columns[index].value_type = expected.value_type;
            data.columns[index].values = vec![1];
            data.columns[index].quality = vec![0];
        }
        let good = SnapshotData {
            tick: data.clone(),
            provenance: None,
        };
        validate_snapshot_data(&meta, &good, &request, 10).expect("candidate");

        let mut missing = data.clone();
        missing.entries.pop();
        assert_eq!(
            validate_snapshot_data(
                &meta,
                &SnapshotData {
                    tick: missing,
                    provenance: None,
                },
                &request,
                10,
            ),
            Err(QuerySourceError::Internal)
        );

        let mut extra = data.clone();
        extra.entries.push(Entry::Metric {
            facts: meta.columns[1].facts,
            name: meta.columns[1].metric.clone(),
        });
        assert_eq!(
            validate_snapshot_data(
                &meta,
                &SnapshotData {
                    tick: extra,
                    provenance: None,
                },
                &request,
                10,
            ),
            Err(QuerySourceError::Internal)
        );

        let mut duplicate = data.clone();
        duplicate.entries[2] = duplicate.entries[1].clone();
        assert_eq!(
            validate_snapshot_data(
                &meta,
                &SnapshotData {
                    tick: duplicate,
                    provenance: None,
                },
                &request,
                10,
            ),
            Err(QuerySourceError::Internal)
        );

        let mut wrong_type = data.clone();
        wrong_type.columns[0].value_type = MetricValueType::I64;
        assert_eq!(
            validate_snapshot_data(
                &meta,
                &SnapshotData {
                    tick: wrong_type,
                    provenance: None,
                },
                &request,
                10,
            ),
            Err(QuerySourceError::Internal)
        );

        let mut f64_meta = meta;
        f64_meta.columns[0].value_type = MetricValueType::F64;
        let mut nonfinite = data;
        nonfinite.columns[0].value_type = MetricValueType::F64;
        nonfinite.columns[0].values = vec![f64::NAN.to_bits()];
        assert_eq!(
            validate_snapshot_data(
                &f64_meta,
                &SnapshotData {
                    tick: nonfinite,
                    provenance: None,
                },
                &request,
                10,
            ),
            Err(QuerySourceError::Internal)
        );

        let mut late_coverage = f64_meta;
        late_coverage.step_ms = 500;
        late_coverage.covered_ms = 501;
        let late_catalog = TierCatalog {
            entries: vec![TierCatalogEntry {
                tier: Tier::Hot,
                step_ms: 500,
            }],
        };
        assert_eq!(
            validate_snapshot_meta(&late_coverage, &request, &late_catalog, 4_096),
            Err(QuerySourceError::Internal)
        );
    }

    #[test]
    fn canonical_bindings_exclude_page_size_and_bound_by_length() {
        let request = request();
        let args = canonical_series_arguments(&request).expect("canonical");
        assert!(args.len() <= super::MAX_CURSOR_BINDING_BYTES);
        let series = SeriesArgs {
            request: request.clone(),
            cursor: None,
            max_blocks: 1,
        };
        let other = SeriesArgs {
            request,
            cursor: None,
            max_blocks: 2,
        };
        assert_eq!(
            canonical_series_arguments(&series.request),
            canonical_series_arguments(&other.request)
        );

        let procs = ProcsRequest {
            subject_inc: 7,
            descendants: true,
            fields: vec![super::ProcField::Comm],
        };
        assert!(canonical_procs_arguments(&procs).is_some());
    }

    #[test]
    fn cursor_table_caps_full_table_and_rejects_cross_binding_replay() {
        let mut table = CursorTable::default();
        let binding = CursorBinding {
            boot_id: "boot".to_string(),
            epoch: 1,
            level: Level::Observe,
            uid: 7,
            verb: "procs",
            arguments: vec![1, 2, 3],
        };
        let view = ProcScanView {
            id: ProcProjectionId(1),
            scan_id: ProcScanId(1),
            published_mono_ns: 0,
            row_count: 1,
            scanned: 1,
            total: 1,
            complete: true,
        };
        for index in 0..MAX_CURSOR_ENTRIES {
            let reservation = table
                .reserve(
                    &binding,
                    CursorTarget::Procs {
                        view: view.clone(),
                        index: u32::try_from(index).expect("u32"),
                        last: None,
                    },
                    None,
                    0,
                )
                .expect("slot");
            assert!(reservation.created);
            assert!(reservation.token.starts_with('c'));
        }
        assert_eq!(
            table.reserve(
                &binding,
                CursorTarget::Procs {
                    view,
                    index: 999,
                    last: None,
                },
                None,
                0,
            ),
            Err(QuerySourceError::Budget(Pool::Cursors))
        );
        let mut other = binding.clone();
        other.uid = 8;
        assert!(table.lookup("c0000000000000001", &other, 0).is_err());
    }

    #[test]
    fn cursor_reservation_reuse_is_independently_exposed_and_expiry_is_exact() {
        let mut table = CursorTable::default();
        let binding = CursorBinding {
            boot_id: "boot".to_string(),
            epoch: 1,
            level: Level::Observe,
            uid: 7,
            verb: "series_batch",
            arguments: vec![1, 2, 3],
        };
        let target = CursorTarget::Series {
            view: Arc::new(view(13)),
            position: SeriesPosition(3),
        };
        let first = table
            .reserve(&binding, target.clone(), Some((ConnId(1), 1)), 0)
            .expect("first reservation");
        assert!(first.created);
        let second = table
            .reserve(&binding, target.clone(), Some((ConnId(1), 2)), 0)
            .expect("second independent reservation");
        assert!(second.created);
        assert_ne!(first.token, second.token);
        assert!(table.expose(&first.token));
        let reused = table
            .reserve(&binding, target, Some((ConnId(1), 3)), 10)
            .expect("reuse");
        assert!(!reused.created);
        assert_eq!(reused.token, first.token);
        table.abort_reservation(&second.token);
        assert_eq!(
            table.lookup(&first.token, &binding, 10).ok(),
            Some(CursorTarget::Series {
                view: Arc::new(view(13)),
                position: SeriesPosition(3),
            })
        );
        assert_eq!(
            table.expire(super::CURSOR_LIFETIME_NS),
            vec![QueryViewId::Series(SeriesViewId(13))]
        );
        assert_eq!(table.len(), 0);
        assert!(table.lookup(&first.token, &binding, 0).is_err());
    }

    #[test]
    fn continuation_dictionary_form_is_rechecked_before_rep() {
        let request = SeriesRequest {
            selection: Selection::new(
                vec![Sel::Inc(7)],
                vec![
                    "m_alpha".to_string(),
                    "m_beta".to_string(),
                    "m_gamma".to_string(),
                ],
            )
            .expect("selection"),
            from_real_ns: 0,
            to_real_ns: 3 * STEP_NS,
            step_ms: 1_000,
            aggs: vec![
                hostobs_proto::dict::Aggregation::Mean,
                hostobs_proto::dict::Aggregation::Last,
            ],
        };
        let mut history = view(14);
        history.subjects = vec![7];
        history.columns = vec![
            column("m_alpha", MetricKind::Gauge),
            column("m_beta", MetricKind::State),
            column("m_gamma", MetricKind::State),
        ];
        let mut backend = FixedBackend::new();
        backend.series_group.insert(
            (history.id, SeriesPosition(0)),
            Ok(Some(SeriesGroup {
                subject_inc: 7,
                agg: hostobs_proto::dict::Aggregation::Mean,
                first: SeriesPosition(0),
                bucket_count: 1,
            })),
        );
        backend.series_group.insert(
            (history.id, SeriesPosition(1)),
            Ok(Some(SeriesGroup {
                subject_inc: 7,
                agg: hostobs_proto::dict::Aggregation::Last,
                first: SeriesPosition(1),
                bucket_count: 1,
            })),
        );
        backend
            .series_group
            .insert((history.id, SeriesPosition(2)), Ok(None));
        let (dictionary, _, permitted) = series_dictionary(&history, &request).expect("dictionary");
        let one_column_cap =
            series_frame_len(1, 1, Some(dictionary.len())).expect("one-column frame");
        let bounds = super::QueryBounds {
            max_rows: 4_096,
            max_block_buckets: 4,
            max_block_bytes: u32::try_from(one_column_cap).expect("small frame"),
        };
        let first = plan_series_page(
            &backend,
            &history,
            &request,
            &permitted,
            dictionary.len(),
            bounds,
            1,
            SeriesPosition(0),
        )
        .expect("first page");
        assert_eq!(first.blocks.len(), 1);
        assert_eq!(first.blocks[0].agg, hostobs_proto::dict::Aggregation::Mean);
        assert_eq!(first.continuation, Some(SeriesPosition(1)));
        assert_eq!(
            series_plan_frame_len(
                first.blocks[0].buckets,
                &permitted,
                first.blocks[0].agg,
                Some(dictionary.len())
            ),
            Some(one_column_cap)
        );
        assert_eq!(
            plan_series_page(
                &backend,
                &history,
                &request,
                &permitted,
                dictionary.len(),
                bounds,
                1,
                SeriesPosition(1),
            ),
            Err(QuerySourceError::Budget(Pool::Block))
        );
    }

    #[test]
    fn proc_rows_enforce_order_and_rendered_field_bounds() {
        let mut row = ProcRow {
            pid: 10,
            start_ticks: 20,
            inc: 7,
            comm: Some(format!("{}x", "é".repeat(22))),
            ppid: None,
            state: Some(b'S'),
            cpu_usec: None,
            rss_bytes: None,
            io_rbytes: None,
            io_wbytes: None,
            threads: None,
            cmdline: Some("x".repeat(super::MAX_CMDLINE_BYTES)),
        };
        assert_eq!(
            row.comm.as_ref().expect("comm").len(),
            super::MAX_COMM_BYTES
        );
        assert!(validate_proc_row(&row, &[ProcField::Comm], None));
        assert!(!validate_proc_row(&row, &[ProcField::Comm], Some((10, 20))));

        row.comm = Some(format!("{}xy", "é".repeat(22)));
        assert_eq!(row.comm.as_ref().expect("comm").len(), 46);
        assert!(!validate_proc_row(&row, &[ProcField::Comm], None));

        row.comm = Some("x".to_string());
        row.cmdline = Some("x".repeat(super::MAX_CMDLINE_BYTES + 1));
        assert!(!validate_proc_row(&row, &[ProcField::Cmdline], None));

        row.cmdline = None;
        row.state = Some(b'\n');
        assert!(!validate_proc_row(&row, &[ProcField::State], None));
    }

    #[test]
    fn history_view_is_shared_by_cursor_targets() {
        let view = Arc::new(view(9));
        let target = CursorTarget::Series {
            view: Arc::clone(&view),
            position: SeriesPosition(3),
        };
        assert_eq!(target.view(), QueryViewId::Series(SeriesViewId(9)));
        assert_eq!(Arc::strong_count(&view), 2);
    }

    #[test]
    fn series_read_candidate_must_match_its_aggregation_columns() {
        let request = request();
        let view = view(10);
        let permitted = view.columns.clone();
        let identity = Identity {
            server: "hostobs-probe".to_string(),
            boot_id: "00000000-0000-4000-8000-000000000001".to_string(),
            epoch: 1,
            features: Vec::new(),
        };
        let plan = super::BlockPlan {
            subject_inc: 7,
            agg: hostobs_proto::dict::Aggregation::Mean,
            start: SeriesPosition(3),
            buckets: 1,
        };
        let data = HistoryBlockData {
            subject_inc: 7,
            agg: hostobs_proto::dict::Aggregation::Mean,
            buckets: vec![HistoryBucket {
                bucket_real_ns: 0,
                covered_ms: 1_000,
                samples: 1,
            }],
            columns: vec![HistoryColumn {
                metric: "m_a".to_string(),
                facts: facts(MetricKind::Gauge),
                value_type: MetricValueType::U64,
                values: vec![0],
                quality: vec![hostobs_proto::quality::Quality::Absent.code()],
            }],
        };
        let (dictionary, _, _) = series_dictionary(&view, &request).expect("dictionary");
        assert!(series_block(
            &identity,
            &view,
            &request,
            &permitted,
            Some(dictionary),
            plan,
            data,
        )
        .is_ok());
    }
}
