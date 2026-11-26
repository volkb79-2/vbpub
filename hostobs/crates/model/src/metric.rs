//! The metric registry: what a metric means and which operations it permits.
//!
//! Plan v4 §A.3 and §B.3 require each metric entry to carry **unit, kind, scope, source
//! and the permitted aggregation**, and §C.3 requires counters to be stored raw and
//! only turned into rates over a real interval. This module is where those facts live,
//! so that no consumer has to guess from a name (or from the magnitude of an unlabelled
//! number) whether a series may be summed, re-sampled or differenced.
//!
//! # Contracts
//!
//! - A registry is **closed and explicit**: every metric is declared exactly once, and
//!   a lookup for an undeclared metric is a typed error. There is no default metric, no
//!   wildcard entry and no "unknown, treat as gauge".
//! - **The permitted aggregation is derived from the kind, never configured by hand.** A
//!   counter may be read raw and differenced; a gauge may be averaged, minimised,
//!   maximised and kept last; a state may only be kept last. Declaring `sum` or `mean`
//!   for a counter is refused, because summing a cumulative counter across time is the
//!   bug plan v4 §C.3 exists to prevent, and the sampler's own rollup code only ever
//!   derived min/max/p95 for gauges (`topos/sampler/src/dict.rs:10`).
//! - **Inclusive parent counters are never summed with descendants** (§C.3). The registry
//!   states the scope so consumers can tell a host-wide total from a per-cgroup value;
//!   hierarchy arithmetic belongs to whoever reads the tree, not to a metric definition.
//! - Metric names are bounded, stable, lower-case identifiers (`prefix.key` or
//!   `prefix.key.subkey`, as the sampler's dictionary already produced, e.g.
//!   `cpu.usage_usec`, `psi.memory.some.avg10`).
//!
//! # Decisions taken where plan v4 is open
//!
//! - A metric **never changes its unit, kind or scope** within a registry. Two metrics
//!   with the same name but different meanings would silently merge two series, so that
//!   is refused at declaration time; a changed meaning needs a new name.
//! - Aggregation is not a stored field but a function of the kind
//!   ([`Metric::allows`]/[`Metric::aggregations`]); the wire entry in plan v4 §B.3
//!   carries a permitted-aggregation mask, which this module can render and parse.

use core::fmt;
use std::collections::HashMap;

/// Largest accepted metric name in bytes.
pub const MAX_METRIC_NAME_LEN: usize = 96;

/// The value kind of a metric (§B.2 column types: `u64`, `i64`, `f64`).
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash)]
#[repr(u8)]
pub enum MetricKind {
    /// A cumulative counter, stored raw as `u64` (§C.3).
    Counter = 1,
    /// A point-in-time gauge, stored as `u64`, `i64` or `f64` per its unit.
    Gauge = 0,
    /// A closed state code, stored as a small integer.
    State = 2,
}

impl MetricKind {
    /// The stable stored code.
    #[must_use]
    pub const fn code(self) -> u8 {
        self as u8
    }

    /// Parse a stored code.
    ///
    /// # Errors
    ///
    /// [`MetricError::UnknownKind`].
    pub const fn from_code(code: u8) -> Result<MetricKind, MetricError> {
        match code {
            0 => Ok(MetricKind::Gauge),
            1 => Ok(MetricKind::Counter),
            2 => Ok(MetricKind::State),
            other => Err(MetricError::UnknownKind { code: other }),
        }
    }

    /// The stable lower-case name used by fixtures and wire dictionaries.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            MetricKind::Gauge => "gauge",
            MetricKind::Counter => "counter",
            MetricKind::State => "state",
        }
    }

    /// The permitted aggregations of this kind, as the wire mask.
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

/// What one value measures.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
#[repr(u8)]
pub enum Unit {
    /// No dimension (a count of things, a ratio, a percentage).
    None = 0,
    /// Microseconds.
    Microseconds = 1,
    /// Milliseconds.
    Milliseconds = 2,
    /// Nanoseconds.
    Nanoseconds = 3,
    /// Bytes.
    Bytes = 4,
    /// A count of objects (pids, pages, faults).
    Count = 5,
    /// A ratio or percentage in `0..=100` or `0..=1` per the metric's own definition.
    Ratio = 6,
    /// Volts.
    Volts = 7,
    /// Degrees Celsius.
    Celsius = 8,
}

impl Unit {
    /// The stable stored code.
    #[must_use]
    pub const fn code(self) -> u8 {
        self as u8
    }

    /// Parse a stored code.
    ///
    /// # Errors
    ///
    /// [`MetricError::UnknownUnit`]: the code is outside the table.
    pub const fn from_code(code: u8) -> Result<Unit, MetricError> {
        match code {
            0 => Ok(Unit::None),
            1 => Ok(Unit::Microseconds),
            2 => Ok(Unit::Milliseconds),
            3 => Ok(Unit::Nanoseconds),
            4 => Ok(Unit::Bytes),
            5 => Ok(Unit::Count),
            6 => Ok(Unit::Ratio),
            7 => Ok(Unit::Volts),
            8 => Ok(Unit::Celsius),
            other => Err(MetricError::UnknownUnit { code: other }),
        }
    }

    /// The unit's short symbol, empty for a dimensionless value.
    #[must_use]
    pub const fn symbol(self) -> &'static str {
        match self {
            Unit::None => "",
            Unit::Microseconds => "us",
            Unit::Milliseconds => "ms",
            Unit::Nanoseconds => "ns",
            Unit::Bytes => "B",
            Unit::Count => "count",
            Unit::Ratio => "%",
            Unit::Volts => "V",
            Unit::Celsius => "degC",
        }
    }

    /// The stable lower-case name used by fixtures and wire dictionaries.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Unit::None => "none",
            Unit::Microseconds => "usec",
            Unit::Milliseconds => "msec",
            Unit::Nanoseconds => "nsec",
            Unit::Bytes => "bytes",
            Unit::Count => "count",
            Unit::Ratio => "ratio",
            Unit::Volts => "volts",
            Unit::Celsius => "celsius",
        }
    }
}

/// Where a value is measured (plan v4 §C.3 on inclusive hierarchy counters).
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
#[repr(u8)]
pub enum Scope {
    /// A whole-host fact read from a host file (`/proc/stat`, `/proc/pressure/...`).
    Host = 0,
    /// A fact about one cgroup (inclusive of its descendants, as cgroupfs reports it).
    Cgroup = 1,
    /// A fact about one process.
    Process = 2,
    /// A fact about the probe itself.
    Daemon = 3,
}

impl Scope {
    /// The stable stored code.
    #[must_use]
    pub const fn code(self) -> u8 {
        self as u8
    }

    /// Parse a stored code.
    ///
    /// # Errors
    ///
    /// [`MetricError::UnknownScope`].
    pub const fn from_code(code: u8) -> Result<Scope, MetricError> {
        match code {
            0 => Ok(Scope::Host),
            1 => Ok(Scope::Cgroup),
            2 => Ok(Scope::Process),
            3 => Ok(Scope::Daemon),
            other => Err(MetricError::UnknownScope { code: other }),
        }
    }

    /// The stable lower-case name used by fixtures and wire dictionaries.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Scope::Host => "host",
            Scope::Cgroup => "cgroup",
            Scope::Process => "process",
            Scope::Daemon => "daemon",
        }
    }
}

/// Which reader produced a value (plan v4 §A.3 `source`).
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
#[repr(u8)]
pub enum Source {
    /// A file in cgroupfs (`cpu.stat`, `memory.current`, `io.stat`).
    CgroupFs = 0,
    /// A file under `/proc` (host-wide counters, pressure, process data).
    ProcFs = 1,
    /// A file under `/sys` (device, zswap, ksm facts).
    SysFs = 2,
    /// The probe's own bookkeeping (self-monitoring, plan §A.1).
    Probe = 3,
    /// Kernels facts derived from another metric by `model` (rates, rollups).
    Derived = 4,
}

impl Source {
    /// The stable stored code.
    #[must_use]
    pub const fn code(self) -> u8 {
        self as u8
    }

    /// Parse a stored code.
    ///
    /// # Errors
    ///
    /// [`MetricError::UnknownSource`].
    pub const fn from_code(code: u8) -> Result<Source, MetricError> {
        match code {
            0 => Ok(Source::CgroupFs),
            1 => Ok(Source::ProcFs),
            2 => Ok(Source::SysFs),
            3 => Ok(Source::Probe),
            4 => Ok(Source::Derived),
            other => Err(MetricError::UnknownSource { code: other }),
        }
    }

    /// The stable lower-case name used by fixtures and wire dictionaries.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Source::CgroupFs => "cgroupfs",
            Source::ProcFs => "procfs",
            Source::SysFs => "sysfs",
            Source::Probe => "probe",
            Source::Derived => "derived",
        }
    }
}

/// An aggregation a stored series may use (plan v4 §B.2, §B.3).
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
#[repr(u8)]
pub enum Aggregation {
    /// Store the raw reading (counters).
    Raw = 0,
    /// The value at the end of a bucket (counters and gauges).
    Last = 1,
    /// Difference of two raw counter readings over the covered interval.
    Delta = 2,
    /// Sample-weighted mean (gauges only).
    Mean = 3,
    /// Bucket minimum (gauges only).
    Min = 4,
    /// Bucket maximum (gauges only).
    Max = 5,
}

impl Aggregation {
    /// The bit this aggregation uses in a permitted-aggregation mask.
    #[must_use]
    pub const fn bit(self) -> u16 {
        1 << (self as u16)
    }

    /// The stable stored code.
    #[must_use]
    pub const fn code(self) -> u8 {
        self as u8
    }

    /// Parse a stored aggregation code.
    ///
    /// # Errors
    ///
    /// [`MetricError::UnknownAggregation`].
    pub const fn from_code(code: u8) -> Result<Aggregation, MetricError> {
        match code {
            0 => Ok(Aggregation::Raw),
            1 => Ok(Aggregation::Last),
            2 => Ok(Aggregation::Delta),
            3 => Ok(Aggregation::Mean),
            4 => Ok(Aggregation::Min),
            5 => Ok(Aggregation::Max),
            other => Err(MetricError::UnknownAggregation { code: other }),
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

/// Every aggregation, in mask order.
pub const ALL_AGGREGATIONS: [Aggregation; 6] = [
    Aggregation::Raw,
    Aggregation::Last,
    Aggregation::Delta,
    Aggregation::Mean,
    Aggregation::Min,
    Aggregation::Max,
];

/// A metric declaration.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Metric {
    name: &'static str,
    kind: MetricKind,
    unit: Unit,
    scope: Scope,
    source: Source,
}

impl Metric {
    /// The metric's name.
    #[must_use]
    pub const fn name(self) -> &'static str {
        self.name
    }

    /// The metric's value kind.
    #[must_use]
    pub const fn kind(self) -> MetricKind {
        self.kind
    }

    /// The metric's unit.
    #[must_use]
    pub const fn unit(self) -> Unit {
        self.unit
    }

    /// The metric's scope.
    #[must_use]
    pub const fn scope(self) -> Scope {
        self.scope
    }

    /// The reader that produces the value.
    #[must_use]
    pub const fn source(self) -> Source {
        self.source
    }

    /// True when the metric is a cumulative counter (§C.3).
    #[must_use]
    pub const fn is_counter(self) -> bool {
        matches!(self.kind, MetricKind::Counter)
    }

    /// The aggregation this kind permits, as the wire mask of §B.3.
    #[must_use]
    pub const fn aggregation_mask(self) -> u16 {
        self.kind.aggregation_mask()
    }

    /// True when this metric's kind permits `aggregation`.
    #[must_use]
    pub const fn allows(self, aggregation: Aggregation) -> bool {
        self.aggregation_mask() & aggregation.bit() != 0
    }

    /// The permitted aggregations, in mask order.
    #[must_use]
    pub fn aggregations(self) -> Vec<Aggregation> {
        ALL_AGGREGATIONS
            .into_iter()
            .filter(|aggregation| self.allows(*aggregation))
            .collect()
    }
}

impl fmt::Display for Metric {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{} ({:?}, {})", self.name, self.kind, self.unit.symbol())
    }
}

/// Every metric declaration problem.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum MetricError {
    /// The name is empty, too long or not a lower-case metric path.
    InvalidName {
        /// The rejected name as declared.
        name: &'static str,
    },
    /// The same name is declared with different facts: two series would merge invisibly.
    ConflictingRedeclaration {
        /// The declared name.
        name: &'static str,
    },
    /// A lookup for a metric nobody declared.
    UnknownMetric {
        /// The requested name.
        name: String,
    },
    /// A counter declared with an aggregation that would sum cumulative values.
    AggregationNotPermitted {
        /// The declared name.
        name: &'static str,
        /// The refused aggregation.
        aggregation: Aggregation,
    },
    /// A unit code outside the table.
    UnknownUnit {
        /// The stored code.
        code: u8,
    },
    /// A scope code outside the table.
    UnknownScope {
        /// The stored code.
        code: u8,
    },
    /// A source code outside the table.
    UnknownSource {
        /// The stored code.
        code: u8,
    },
    /// A metric-kind code outside the table.
    UnknownKind {
        /// The stored code.
        code: u8,
    },
    /// An aggregation code outside the table.
    UnknownAggregation {
        /// The stored code.
        code: u8,
    },
}

impl fmt::Display for MetricError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            MetricError::InvalidName { name } => write!(f, "invalid metric name {name:?}"),
            MetricError::ConflictingRedeclaration { name } => {
                write!(f, "metric {name:?} is redeclared with different facts")
            }
            MetricError::UnknownMetric { name } => write!(f, "metric {name:?} is not registered"),
            MetricError::AggregationNotPermitted { name, aggregation } => {
                write!(
                    f,
                    "metric {name:?} does not permit the {aggregation:?} aggregation"
                )
            }
            MetricError::UnknownUnit { code } => write!(f, "unknown unit code {code}"),
            MetricError::UnknownScope { code } => write!(f, "unknown scope code {code}"),
            MetricError::UnknownSource { code } => write!(f, "unknown source code {code}"),
            MetricError::UnknownKind { code } => write!(f, "unknown metric-kind code {code}"),
            MetricError::UnknownAggregation { code } => {
                write!(f, "unknown aggregation code {code}")
            }
        }
    }
}

impl core::error::Error for MetricError {}

/// Validate a metric name: lower case, `prefix.` then dot-separated parts.
fn valid_name(name: &str) -> bool {
    if name.is_empty() || name.len() > MAX_METRIC_NAME_LEN {
        return false;
    }
    let mut parts = name.split('.');
    let Some(prefix) = parts.next() else {
        return false;
    };
    if prefix.is_empty() {
        return false;
    }
    for part in parts {
        if part.is_empty() {
            return false;
        }
    }
    name.bytes().all(|byte| {
        byte.is_ascii_lowercase()
            || byte.is_ascii_digit()
            || byte == b'.'
            || byte == b'_'
            || byte == b'-'
    }) && !name.starts_with('.')
        && !name.ends_with('.')
        && !name.contains("..")
}

fn check_parts(metric: Metric) -> Result<Metric, MetricError> {
    if !valid_name(metric.name) {
        return Err(MetricError::InvalidName { name: metric.name });
    }
    Ok(metric)
}

/// The declared metrics of a hostobs instance.
///
/// The registry is built once (declarations are static facts about what the probe reads
/// and derives) and is then read-only. Lookups refuse an unknown name instead of
/// inventing a default, because "defaults are hazards" is the estate rule: a metric
/// nobody declared is a bug in the caller or in the wiring, and must say so.
#[derive(Debug, Default)]
pub struct MetricRegistry {
    metrics: Vec<Metric>,
    by_name: HashMap<&'static str, usize>,
}

impl MetricRegistry {
    /// An empty registry.
    #[must_use]
    pub fn new() -> MetricRegistry {
        MetricRegistry::default()
    }

    /// Declare a metric.
    ///
    /// # Errors
    ///
    /// [`MetricError::InvalidName`] when the name is not a lower-case metric path, and
    /// [`MetricError::ConflictingRedeclaration`] when the same name is declared with
    /// different unit, kind, scope or source. Repeating an identical declaration is a
    /// no-op, so a caller may declare its whole baseline twice by accident without
    /// losing the check that matters.
    pub fn declare(
        &mut self,
        name: &'static str,
        kind: MetricKind,
        unit: Unit,
        scope: Scope,
        source: Source,
    ) -> Result<Metric, MetricError> {
        let metric = check_parts(Metric {
            name,
            kind,
            unit,
            scope,
            source,
        })?;
        if let Some(&index) = self.by_name.get(name) {
            let existing = self.metrics[index];
            if existing == metric {
                return Ok(existing);
            }
            return Err(MetricError::ConflictingRedeclaration { name });
        }
        let index = self.metrics.len();
        self.metrics.push(metric);
        self.by_name.insert(name, index);
        Ok(metric)
    }

    /// Declare a metric and require that its kind permits `aggregation`.
    ///
    /// # Errors
    ///
    /// Everything [`Self::declare`] refuses, plus [`MetricError::AggregationNotPermitted`].
    pub fn declare_with(
        &mut self,
        name: &'static str,
        kind: MetricKind,
        unit: Unit,
        scope: Scope,
        source: Source,
        aggregation: Aggregation,
    ) -> Result<Metric, MetricError> {
        let metric = self.declare(name, kind, unit, scope, source)?;
        if !metric.allows(aggregation) {
            return Err(MetricError::AggregationNotPermitted { name, aggregation });
        }
        Ok(metric)
    }

    /// Find a declared metric by name.
    ///
    /// # Errors
    ///
    /// [`MetricError::UnknownMetric`]: undeclared names are refused.
    pub fn get(&self, name: &str) -> Result<Metric, MetricError> {
        self.by_name
            .get(name)
            .map(|&index| self.metrics[index])
            .ok_or_else(|| MetricError::UnknownMetric {
                name: name.to_string(),
            })
    }

    /// The declared metrics, in declaration order.
    #[must_use]
    pub fn metrics(&self) -> &[Metric] {
        &self.metrics
    }

    /// The number of declared metrics.
    #[must_use]
    pub fn len(&self) -> usize {
        self.metrics.len()
    }

    /// True when nothing is declared.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.metrics.is_empty()
    }
}

/// The sampler's metric baseline, ported from the proven collector
/// (`topos/sampler/src/collect.rs` ids and key tables) with no behaviour invented: the
/// kinds are what the sampler treats as counters (`d.met(name, true)`), the units come
/// from the file each fact is read from, and the scopes follow plan v4 §C.3.
///
/// The list is the M1 baseline the model/proto tests use; `kernel` declares the rest of
/// its keys against the same registry when the collectors are imported.
pub fn sampler_baseline() -> Result<MetricRegistry, MetricError> {
    let mut registry = MetricRegistry::new();
    // cgroupfs facts (collect.rs `Ids::new`, `CG_FILES`).
    registry.declare(
        "cgroup.freeze",
        MetricKind::State,
        Unit::None,
        Scope::Cgroup,
        Source::CgroupFs,
    )?;
    registry.declare(
        "cgevents.frozen",
        MetricKind::State,
        Unit::None,
        Scope::Cgroup,
        Source::CgroupFs,
    )?;
    registry.declare(
        "cgevents.populated",
        MetricKind::State,
        Unit::None,
        Scope::Cgroup,
        Source::CgroupFs,
    )?;
    registry.declare(
        "cpu.usage_usec",
        MetricKind::Counter,
        Unit::Microseconds,
        Scope::Cgroup,
        Source::CgroupFs,
    )?;
    registry.declare(
        "cpu.user_usec",
        MetricKind::Counter,
        Unit::Microseconds,
        Scope::Cgroup,
        Source::CgroupFs,
    )?;
    registry.declare(
        "cpu.system_usec",
        MetricKind::Counter,
        Unit::Microseconds,
        Scope::Cgroup,
        Source::CgroupFs,
    )?;
    registry.declare(
        "mem.current",
        MetricKind::Gauge,
        Unit::Bytes,
        Scope::Cgroup,
        Source::CgroupFs,
    )?;
    registry.declare(
        "mem.anon",
        MetricKind::Gauge,
        Unit::Bytes,
        Scope::Cgroup,
        Source::CgroupFs,
    )?;
    registry.declare(
        "mem.file",
        MetricKind::Gauge,
        Unit::Bytes,
        Scope::Cgroup,
        Source::CgroupFs,
    )?;
    registry.declare(
        "mem.swap_current",
        MetricKind::Gauge,
        Unit::Bytes,
        Scope::Cgroup,
        Source::CgroupFs,
    )?;
    registry.declare(
        "pids.current",
        MetricKind::Gauge,
        Unit::Count,
        Scope::Cgroup,
        Source::CgroupFs,
    )?;
    registry.declare(
        "io.rbytes",
        MetricKind::Counter,
        Unit::Bytes,
        Scope::Cgroup,
        Source::CgroupFs,
    )?;
    registry.declare(
        "io.wbytes",
        MetricKind::Counter,
        Unit::Bytes,
        Scope::Cgroup,
        Source::CgroupFs,
    )?;
    registry.declare(
        "io.rios",
        MetricKind::Counter,
        Unit::Count,
        Scope::Cgroup,
        Source::CgroupFs,
    )?;
    registry.declare(
        "io.wios",
        MetricKind::Counter,
        Unit::Count,
        Scope::Cgroup,
        Source::CgroupFs,
    )?;
    registry.declare(
        "io.dbytes",
        MetricKind::Counter,
        Unit::Bytes,
        Scope::Cgroup,
        Source::CgroupFs,
    )?;
    registry.declare(
        "io.dios",
        MetricKind::Counter,
        Unit::Count,
        Scope::Cgroup,
        Source::CgroupFs,
    )?;
    registry.declare(
        "io.cost.usage",
        MetricKind::Counter,
        Unit::None,
        Scope::Cgroup,
        Source::CgroupFs,
    )?;
    registry.declare(
        "io.cost.wait",
        MetricKind::Counter,
        Unit::None,
        Scope::Cgroup,
        Source::CgroupFs,
    )?;
    registry.declare(
        "io.cost.indelay",
        MetricKind::Counter,
        Unit::None,
        Scope::Cgroup,
        Source::CgroupFs,
    )?;
    registry.declare(
        "io.cost.vrate",
        MetricKind::Gauge,
        Unit::None,
        Scope::Cgroup,
        Source::CgroupFs,
    )?;
    registry.declare(
        "memev.oom_kill",
        MetricKind::Counter,
        Unit::Count,
        Scope::Cgroup,
        Source::CgroupFs,
    )?;
    registry.declare(
        "memevl.oom_kill",
        MetricKind::Counter,
        Unit::Count,
        Scope::Cgroup,
        Source::CgroupFs,
    )?;
    for (name, kind) in [
        ("psi.cpu.some.avg10", MetricKind::Gauge),
        ("psi.memory.some.avg10", MetricKind::Gauge),
        ("psi.io.some.avg10", MetricKind::Gauge),
        ("psi.cpu.full.avg10", MetricKind::Gauge),
        ("psi.memory.full.avg10", MetricKind::Gauge),
        ("psi.io.full.avg10", MetricKind::Gauge),
        ("psi.cpu.some.total", MetricKind::Counter),
        ("psi.memory.some.total", MetricKind::Counter),
        ("psi.io.some.total", MetricKind::Counter),
        ("psi.cpu.full.total", MetricKind::Counter),
        ("psi.memory.full.total", MetricKind::Counter),
        ("psi.io.full.total", MetricKind::Counter),
    ] {
        let unit = if kind == MetricKind::Counter {
            Unit::Microseconds
        } else {
            Unit::Ratio
        };
        registry.declare(name, kind, unit, Scope::Cgroup, Source::CgroupFs)?;
    }
    // host-wide facts from /proc (collect.rs host file tables).
    registry.declare(
        "host.ctxt",
        MetricKind::Counter,
        Unit::Count,
        Scope::Host,
        Source::ProcFs,
    )?;
    registry.declare(
        "host.forks",
        MetricKind::Counter,
        Unit::Count,
        Scope::Host,
        Source::ProcFs,
    )?;
    registry.declare(
        "host.procs_running",
        MetricKind::Gauge,
        Unit::Count,
        Scope::Host,
        Source::ProcFs,
    )?;
    registry.declare(
        "host.procs_blocked",
        MetricKind::Gauge,
        Unit::Count,
        Scope::Host,
        Source::ProcFs,
    )?;
    registry.declare(
        "host.load1",
        MetricKind::Gauge,
        Unit::Ratio,
        Scope::Host,
        Source::ProcFs,
    )?;
    registry.declare(
        "host.load5",
        MetricKind::Gauge,
        Unit::Ratio,
        Scope::Host,
        Source::ProcFs,
    )?;
    registry.declare(
        "host.load15",
        MetricKind::Gauge,
        Unit::Ratio,
        Scope::Host,
        Source::ProcFs,
    )?;
    registry.declare(
        "cpu.busy_pct",
        MetricKind::Gauge,
        Unit::Ratio,
        Scope::Host,
        Source::ProcFs,
    )?;
    // kernel feature facts from /sys (zswap, ksm as the sampler reads them).
    registry.declare(
        "zswap.enabled",
        MetricKind::State,
        Unit::None,
        Scope::Host,
        Source::SysFs,
    )?;
    registry.declare(
        "zswap.max_pool_percent",
        MetricKind::Gauge,
        Unit::Ratio,
        Scope::Host,
        Source::SysFs,
    )?;
    registry.declare(
        "zswap.pool_bytes",
        MetricKind::Gauge,
        Unit::Bytes,
        Scope::Host,
        Source::SysFs,
    )?;
    registry.declare(
        "zswap.stored_bytes",
        MetricKind::Gauge,
        Unit::Bytes,
        Scope::Host,
        Source::SysFs,
    )?;
    registry.declare(
        "ksm.run",
        MetricKind::State,
        Unit::None,
        Scope::Host,
        Source::SysFs,
    )?;
    registry.declare(
        "ksm.pages_sharing",
        MetricKind::Gauge,
        Unit::Count,
        Scope::Host,
        Source::SysFs,
    )?;
    // the probe's own bookkeeping.
    registry.declare(
        "probe.cgroups_skipped",
        MetricKind::Gauge,
        Unit::Count,
        Scope::Daemon,
        Source::Probe,
    )?;
    registry.declare(
        "probe.ticks_skipped",
        MetricKind::Counter,
        Unit::Count,
        Scope::Daemon,
        Source::Probe,
    )?;
    registry.declare(
        "probe.resets",
        MetricKind::Counter,
        Unit::Count,
        Scope::Daemon,
        Source::Probe,
    )?;
    // derivations the model crate produces (rate of a counter over a real interval).
    registry.declare(
        "derived.cpu_pct",
        MetricKind::Gauge,
        Unit::Ratio,
        Scope::Cgroup,
        Source::Derived,
    )?;
    Ok(registry)
}

#[cfg(test)]
mod tests {
    use super::{
        sampler_baseline, valid_name, Aggregation, MetricError, MetricKind, MetricRegistry, Scope,
        Source, Unit,
    };

    #[test]
    fn names_are_lower_case_metric_paths() {
        for good in [
            "cpu.usage_usec",
            "psi.io.some.avg10",
            "a",
            "a.b-c_d",
            "derived.cpu_pct",
        ] {
            assert!(valid_name(good), "{good}");
        }
        for bad in [
            "",
            ".",
            "..",
            "a..b",
            ".a",
            "a.",
            "Cpu.usage",
            "cpu usage",
            "cpu/usage",
            "a\nb",
        ] {
            assert!(!valid_name(bad), "{bad:?}");
        }
        assert!(!valid_name(&"a".repeat(97)));
        assert!(valid_name(&format!("a.{}", "b".repeat(91))));
    }

    #[test]
    fn aggregation_is_a_function_of_the_kind_and_sum_is_never_permitted() {
        let mut registry = MetricRegistry::new();
        let counter = registry
            .declare(
                "host.ctxt",
                MetricKind::Counter,
                Unit::Count,
                Scope::Host,
                Source::ProcFs,
            )
            .expect("declare");
        assert_eq!(
            counter.aggregations(),
            vec![Aggregation::Raw, Aggregation::Last, Aggregation::Delta],
            "a counter is raw, last-of-bucket or a difference, never a sum of levels"
        );
        assert!(!counter.allows(Aggregation::Mean));
        assert_eq!(counter.aggregation_mask(), 0b000_0111, "raw | last | delta");

        let gauge = registry
            .declare(
                "mem.current",
                MetricKind::Gauge,
                Unit::Bytes,
                Scope::Cgroup,
                Source::CgroupFs,
            )
            .expect("declare");
        assert_eq!(
            gauge.aggregations(),
            vec![
                Aggregation::Last,
                Aggregation::Mean,
                Aggregation::Min,
                Aggregation::Max
            ]
        );
        assert!(
            !gauge.allows(Aggregation::Delta),
            "a gauge has no counter delta"
        );
        assert_eq!(
            gauge.aggregation_mask(),
            0b011_1010,
            "last | mean | min | max"
        );

        let state = registry
            .declare(
                "ksm.run",
                MetricKind::State,
                Unit::None,
                Scope::Host,
                Source::SysFs,
            )
            .expect("declare");
        assert_eq!(state.aggregations(), vec![Aggregation::Last]);
        assert!(!state.allows(Aggregation::Min));
    }

    #[test]
    fn every_aggregation_bit_is_distinct_and_covered_by_one_kind() {
        let all = Aggregation::Raw.bit()
            | Aggregation::Last.bit()
            | Aggregation::Delta.bit()
            | Aggregation::Mean.bit()
            | Aggregation::Min.bit()
            | Aggregation::Max.bit();
        assert_eq!(all, 0b11_1111);
        assert_eq!(Aggregation::Raw.bit(), 1);
        assert_eq!(Aggregation::Max.bit(), 1 << 5);
        for aggregation in super::ALL_AGGREGATIONS {
            let mut registry = MetricRegistry::new();
            let _ = registry.declare(
                "probe.aggs",
                MetricKind::Counter,
                Unit::None,
                Scope::Daemon,
                Source::Probe,
            );
            let counter = registry.get("probe.aggs").expect("declared");
            let gauge = MetricRegistry::new()
                .declare(
                    "probe.aggs",
                    MetricKind::Gauge,
                    Unit::None,
                    Scope::Daemon,
                    Source::Probe,
                )
                .expect("declared");
            assert!(
                counter.allows(aggregation) || gauge.allows(aggregation),
                "{aggregation:?} is permitted by no kind at all"
            );
        }
    }

    #[test]
    fn an_unpermitted_aggregation_is_refused_at_declaration() {
        let mut registry = MetricRegistry::new();
        assert_eq!(
            registry.declare_with(
                "host.ctxt",
                MetricKind::Counter,
                Unit::Count,
                Scope::Host,
                Source::ProcFs,
                Aggregation::Mean,
            ),
            Err(MetricError::AggregationNotPermitted {
                name: "host.ctxt",
                aggregation: Aggregation::Mean
            })
        );
        assert_eq!(
            registry.declare_with(
                "mem.current",
                MetricKind::Gauge,
                Unit::Bytes,
                Scope::Cgroup,
                Source::CgroupFs,
                Aggregation::Delta,
            ),
            Err(MetricError::AggregationNotPermitted {
                name: "mem.current",
                aggregation: Aggregation::Delta
            })
        );
        assert!(registry
            .declare_with(
                "mem.current",
                MetricKind::Gauge,
                Unit::Bytes,
                Scope::Cgroup,
                Source::CgroupFs,
                Aggregation::Max,
            )
            .is_ok());
    }

    #[test]
    fn a_redeclaration_must_be_identical_and_unknown_names_are_refused() {
        let mut registry = MetricRegistry::new();
        let first = registry
            .declare(
                "cpu.usage_usec",
                MetricKind::Counter,
                Unit::Microseconds,
                Scope::Cgroup,
                Source::CgroupFs,
            )
            .expect("declare");
        assert_eq!(
            registry.declare(
                "cpu.usage_usec",
                MetricKind::Counter,
                Unit::Microseconds,
                Scope::Cgroup,
                Source::CgroupFs
            ),
            Ok(first),
            "an identical re-declaration is a no-op"
        );
        assert_eq!(
            registry.declare(
                "cpu.usage_usec",
                MetricKind::Gauge,
                Unit::Microseconds,
                Scope::Cgroup,
                Source::CgroupFs
            ),
            Err(MetricError::ConflictingRedeclaration {
                name: "cpu.usage_usec"
            }),
            "kind is part of the identity of a metric"
        );
        assert_eq!(
            registry.declare(
                "cpu.usage_usec",
                MetricKind::Counter,
                Unit::Bytes,
                Scope::Cgroup,
                Source::CgroupFs
            ),
            Err(MetricError::ConflictingRedeclaration {
                name: "cpu.usage_usec"
            })
        );
        assert_eq!(
            registry.declare(
                "cpu.usage_usec",
                MetricKind::Counter,
                Unit::Microseconds,
                Scope::Host,
                Source::CgroupFs
            ),
            Err(MetricError::ConflictingRedeclaration {
                name: "cpu.usage_usec"
            })
        );
        assert_eq!(
            registry.declare(
                "cpu.usage_usec",
                MetricKind::Counter,
                Unit::Microseconds,
                Scope::Cgroup,
                Source::SysFs
            ),
            Err(MetricError::ConflictingRedeclaration {
                name: "cpu.usage_usec"
            })
        );
        assert_eq!(
            registry.declare(
                "Not A Name",
                MetricKind::Counter,
                Unit::None,
                Scope::Host,
                Source::ProcFs
            ),
            Err(MetricError::InvalidName { name: "Not A Name" })
        );
        assert_eq!(
            registry.get("cpu.iowait_usec"),
            Err(MetricError::UnknownMetric {
                name: "cpu.iowait_usec".to_string()
            }),
            "no metric is invented for an undeclared name"
        );
        assert_eq!(registry.len(), 1);
        assert!(!registry.is_empty());
    }

    #[test]
    fn lookup_is_exact_and_the_registry_lists_its_declarations_in_order() {
        let mut registry = MetricRegistry::new();
        registry
            .declare(
                "b.metric",
                MetricKind::Gauge,
                Unit::Bytes,
                Scope::Cgroup,
                Source::CgroupFs,
            )
            .expect("declare");
        registry
            .declare(
                "a.metric",
                MetricKind::Counter,
                Unit::Count,
                Scope::Host,
                Source::ProcFs,
            )
            .expect("declare");
        assert_eq!(registry.metrics()[0].name(), "b.metric");
        assert_eq!(registry.metrics()[1].name(), "a.metric");
        assert_eq!(
            registry.get("a.metric").map(|metric| metric.kind()),
            Ok(MetricKind::Counter)
        );
        assert_eq!(
            MetricRegistry::new().get("anything"),
            Err(MetricError::UnknownMetric {
                name: "anything".into()
            })
        );
    }

    #[test]
    fn kind_unit_scope_and_source_codes_round_trip_and_refuse_the_rest() {
        for unit in [
            Unit::None,
            Unit::Microseconds,
            Unit::Milliseconds,
            Unit::Nanoseconds,
            Unit::Bytes,
            Unit::Count,
            Unit::Ratio,
            Unit::Volts,
            Unit::Celsius,
        ] {
            assert_eq!(Unit::from_code(unit.code()), Ok(unit));
        }
        for code in 9u8..=u8::MAX {
            assert_eq!(
                Unit::from_code(code),
                Err(MetricError::UnknownUnit { code })
            );
        }
        for scope in [Scope::Host, Scope::Cgroup, Scope::Process, Scope::Daemon] {
            assert_eq!(Scope::from_code(scope.code()), Ok(scope));
        }
        for code in 4u8..=u8::MAX {
            assert_eq!(
                Scope::from_code(code),
                Err(MetricError::UnknownScope { code })
            );
        }
        for source in [
            Source::CgroupFs,
            Source::ProcFs,
            Source::SysFs,
            Source::Probe,
            Source::Derived,
        ] {
            assert_eq!(Source::from_code(source.code()), Ok(source));
        }
        for code in 5u8..=u8::MAX {
            assert_eq!(
                Source::from_code(code),
                Err(MetricError::UnknownSource { code })
            );
        }
        for kind in [MetricKind::Gauge, MetricKind::Counter, MetricKind::State] {
            assert_eq!(MetricKind::from_code(kind.code()), Ok(kind));
        }
        for code in 3u8..=u8::MAX {
            assert_eq!(
                MetricKind::from_code(code),
                Err(MetricError::UnknownKind { code })
            );
        }
        for aggregation in super::ALL_AGGREGATIONS {
            assert_eq!(Aggregation::from_code(aggregation.code()), Ok(aggregation));
        }
        for code in 6u8..=u8::MAX {
            assert_eq!(
                Aggregation::from_code(code),
                Err(MetricError::UnknownAggregation { code })
            );
        }
        assert_eq!(Unit::Bytes.symbol(), "B");
        assert_eq!(Unit::None.symbol(), "");
        assert_eq!(
            [
                Unit::None.name(),
                Unit::Microseconds.name(),
                Unit::Milliseconds.name(),
                Unit::Nanoseconds.name(),
                Unit::Bytes.name(),
                Unit::Count.name(),
                Unit::Ratio.name(),
                Unit::Volts.name(),
                Unit::Celsius.name(),
            ],
            ["none", "usec", "msec", "nsec", "bytes", "count", "ratio", "volts", "celsius"]
        );
    }

    #[test]
    fn the_sampler_baseline_is_consistent_with_where_the_facts_are_read() {
        let registry = sampler_baseline().expect("baseline declares cleanly");
        assert!(!registry.is_empty());
        for metric in registry.metrics() {
            assert!(valid_name(metric.name()), "{}", metric.name());
            // cgroupfs facts live in cgroups, /proc facts are host-wide, self facts are the probe's.
            match metric.source() {
                Source::CgroupFs => assert_eq!(metric.scope(), Scope::Cgroup, "{}", metric.name()),
                Source::ProcFs => assert_eq!(metric.scope(), Scope::Host, "{}", metric.name()),
                Source::SysFs => assert_eq!(metric.scope(), Scope::Host, "{}", metric.name()),
                Source::Probe => assert_eq!(metric.scope(), Scope::Daemon, "{}", metric.name()),
                Source::Derived => {}
            }
            if metric.kind() == MetricKind::Counter {
                assert!(metric.allows(Aggregation::Delta), "{}", metric.name());
                assert!(!metric.allows(Aggregation::Mean), "{}", metric.name());
            }
        }
        assert_eq!(
            registry.get("cpu.usage_usec").map(|m| m.is_counter()),
            Ok(true)
        );
        assert_eq!(
            registry.get("mem.current").map(|m| m.unit()),
            Ok(Unit::Bytes)
        );
        assert_eq!(
            registry.get("psi.memory.some.avg10").map(|m| m.kind()),
            Ok(MetricKind::Gauge)
        );
        assert_eq!(
            registry
                .get("psi.memory.some.total")
                .map(|m| m.is_counter()),
            Ok(true)
        );
        assert_eq!(
            registry.get("mem.current").map(|m| m.scope()),
            Ok(Scope::Cgroup),
            "mem.current is inclusive of descendants and is never summed with them"
        );
        for name in ["cgroup.freeze", "cgevents.frozen", "cgevents.populated"] {
            let metric = registry.get(name).expect("freezer state is registered");
            assert_eq!(
                (
                    metric.kind(),
                    metric.unit(),
                    metric.scope(),
                    metric.source()
                ),
                (
                    MetricKind::State,
                    Unit::None,
                    Scope::Cgroup,
                    Source::CgroupFs
                ),
                "{name} is sampled cgroup state"
            );
            assert_eq!(
                metric.aggregations(),
                vec![Aggregation::Last],
                "{name} is last-only"
            );
            assert!(
                !metric.allows(Aggregation::Mean),
                "{name} cannot be averaged"
            );
        }
        assert_eq!(
            registry.get("probe.resets").map(|m| m.source()),
            Ok(Source::Probe),
            "the sampler's own counters are the probe's, not host facts"
        );
        assert_eq!(
            registry.get("probe.cgroups_skipped").map(|m| (
                m.kind(),
                m.unit(),
                m.scope(),
                m.source()
            )),
            Ok((MetricKind::Gauge, Unit::Count, Scope::Daemon, Source::Probe))
        );
        assert_eq!(
            registry.get("probe.ticks_skipped").map(|m| (
                m.kind(),
                m.unit(),
                m.scope(),
                m.source()
            )),
            Ok((
                MetricKind::Counter,
                Unit::Count,
                Scope::Daemon,
                Source::Probe
            ))
        );
    }

    #[test]
    fn every_unit_renders_its_symbol_and_a_metric_renders_its_facts() {
        for (unit, symbol) in [
            (Unit::None, ""),
            (Unit::Microseconds, "us"),
            (Unit::Milliseconds, "ms"),
            (Unit::Nanoseconds, "ns"),
            (Unit::Bytes, "B"),
            (Unit::Count, "count"),
            (Unit::Ratio, "%"),
            (Unit::Volts, "V"),
            (Unit::Celsius, "degC"),
        ] {
            assert_eq!(unit.symbol(), symbol);
        }
        let mut registry = MetricRegistry::new();
        let metric = registry
            .declare(
                "mem.current",
                MetricKind::Gauge,
                Unit::Bytes,
                Scope::Cgroup,
                Source::CgroupFs,
            )
            .expect("declare");
        let rendered = metric.to_string();
        assert!(rendered.starts_with("mem.current ("), "{rendered}");
        assert!(rendered.ends_with("(Gauge, B)"), "{rendered}");
    }

    #[test]
    fn error_text_names_the_refusal() {
        assert_eq!(
            MetricError::UnknownUnit { code: 9 }.to_string(),
            "unknown unit code 9"
        );
        assert_eq!(
            MetricError::UnknownScope { code: 4 }.to_string(),
            "unknown scope code 4"
        );
        assert_eq!(
            MetricError::UnknownSource { code: 5 }.to_string(),
            "unknown source code 5"
        );
        assert_eq!(
            MetricError::UnknownKind { code: 3 }.to_string(),
            "unknown metric-kind code 3"
        );
        assert_eq!(
            MetricError::UnknownAggregation { code: 6 }.to_string(),
            "unknown aggregation code 6"
        );
        assert_eq!(
            MetricError::UnknownMetric { name: "x.y".into() }.to_string(),
            "metric \"x.y\" is not registered"
        );
        assert_eq!(
            MetricError::ConflictingRedeclaration { name: "x.y" }.to_string(),
            "metric \"x.y\" is redeclared with different facts"
        );
        assert_eq!(
            MetricError::InvalidName { name: "X" }.to_string(),
            "invalid metric name \"X\""
        );
        assert_eq!(
            MetricError::AggregationNotPermitted {
                name: "host.ctxt",
                aggregation: Aggregation::Mean
            }
            .to_string(),
            "metric \"host.ctxt\" does not permit the Mean aggregation"
        );
    }
}
