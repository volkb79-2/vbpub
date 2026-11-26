//! cgroupfs readers: freezer state, `cpu.stat`, `memory.*`, `io.stat`, `pids.current`, PSI.
//!
//! This is the port of `topos/sampler/src/collect.rs` (`Ids::new`, `CG_FILES`,
//! `read_cg`/`limit_vals`/`is_bottom`) with the behaviours plan v4 requires to change.
//! Every reader takes the parsed text and returns [`Readings`] — keyed, typed readings —
//! or a [`KernelError`] that states why the file could not be used. Nothing here touches
//! the filesystem: [`read_file`] is the only bridge, so the fixtures under
//! `crates/kernel/tests/fixtures/cgroupfs/` drive the same code the probe will.
//!
//! # Contracts (the plan's quality rule)
//!
//! - **A value that does not parse makes its metric unavailable, never zero.** A
//!   `memory.stat` line with a non-numeric number reports `mem.stat.<key>` as
//!   `unreadable` and keeps the lines that did parse; the sampler's collector instead
//!   counted an unparseable value as "skip the line" (the metric silently disappeared) or
//!   as `0` (an invented reading). Both are refused here.
//! - **One bad field of a multi-field file poisons only the facts it feeds.** `io.stat`
//!   sums device counters into the cgroup's `io.*` metrics; a single unparseable field
//!   makes the *sum* unavailable (the sampler's `bad_io`) rather than a partial total,
//!   because a partial total is a wrong number that looks right.
//! - **A device whose topology is unknown is a state, not a bottom device** (plan §A.5,
//!   the M1 fix; `HOB-20`). The sampler asked each device whether it had a non-empty
//!   `/sys/dev/block/<dev>/slaves`; a failed read reused the last answer and a device never
//!   classified counted as bottom (`collect.rs::is_bottom`). Here the caller supplies
//!   [`DeviceTopology`], and a device whose topology is unknown makes the aggregate
//!   [`QualityCode::UnknownTopology`] with no values at all.
//! - **Absent vs unsupported vs unreadable are three different codes**, and each reader
//!   states which one an absence means: a missing `memory.swap.current` is `absent` (a
//!   controller's file, optional), a missing `cpu.pressure` is `unsupported` (a kernel
//!   feature). The caller's [`Absence`] parameter is what makes that explicit rather than
//!   a guess.
//! - **A limit file's `max` is a state, never a number.** `memory.max == "max"` means
//!   "no limit", which is not `-1`, not `0` and not `u64::MAX`: it is
//!   [`QualityCode::Unsupported`] (the kernel expresses absence of a limit in words) with
//!   the text kept for a human.

use core::fmt;
use std::path::{Path, PathBuf};

use crate::fs::{Fs, FsError};
use crate::parse::{fields, lines, parse_f64, parse_u64, split_key_value, split_pair, trim};
use crate::quality::QualityCode;
use crate::value::{Reading, Value};
use crate::Bounds;

/// Which quality code a missing (optional) file means.
///
/// A per-cgroup controller file is `absent` when the controller is off; a host-wide
/// injection point the kernel may not implement is `unsupported`. The caller says which, so
/// a reader never guesses the reason for an absence.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Absence {
    /// A controller or its file: absence is a normal state (`absent`).
    Controller,
    /// A kernel feature: absence is `unsupported`.
    Feature,
}

impl Absence {
    /// The quality code this absence maps to.
    #[must_use]
    pub const fn quality(self) -> QualityCode {
        match self {
            Absence::Controller => QualityCode::Absent,
            Absence::Feature => QualityCode::Unsupported,
        }
    }
}

/// Why a file could not be used.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum KernelError {
    /// The file is absent; the payload is the code the caller asked for.
    Absent(QualityCode),
    /// The file could not be read (permission, I/O, an over-long file).
    Read(FsError),
    /// The file's text does not parse.
    Parse {
        /// The file's own name (a stable metric prefix, not a path).
        file: &'static str,
        /// Which line (zero-based) failed, if the failure is line-scoped.
        line: Option<usize>,
    },
    /// The file parsed but describes a topology this crate does not understand.
    UnknownTopology,
    /// The kernel does not express this fact as a number (a `max` limit).
    NotAValue,
}

impl KernelError {
    /// The quality code this refusal maps to.
    #[must_use]
    pub fn quality(&self) -> QualityCode {
        match self {
            KernelError::Absent(code) => *code,
            KernelError::Read(error) => error.quality(QualityCode::Absent),
            KernelError::Parse { .. } => QualityCode::Unreadable,
            KernelError::UnknownTopology => QualityCode::UnknownTopology,
            KernelError::NotAValue => QualityCode::Unsupported,
        }
    }

    /// A one-line reading of this refusal, for a caller that stores a reason string.
    #[must_use]
    pub fn detail(&self) -> String {
        match self {
            KernelError::Absent(code) => format!("absent ({code})"),
            KernelError::Read(error) => format!("read failed: {error}"),
            KernelError::Parse { file, line } => match line {
                Some(number) => format!("{file}: line {number} does not parse"),
                None => format!("{file}: the text does not parse"),
            },
            KernelError::UnknownTopology => "device topology is unknown".to_string(),
            KernelError::NotAValue => {
                "the kernel writes `max` where a number is required".to_string()
            }
        }
    }
}

impl fmt::Display for KernelError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.detail())
    }
}

impl core::error::Error for KernelError {}

impl From<FsError> for KernelError {
    fn from(error: FsError) -> KernelError {
        KernelError::Read(error)
    }
}

/// One metric's reading: the metric key and the [`Reading`] (which carries its quality).
#[derive(Clone, Debug, PartialEq)]
pub struct MetricReading {
    /// The metric's stable name (`cpu.usage_usec`, `mem.anon`, `io.rbytes`).
    pub metric: String,
    /// The value and its quality.
    pub reading: Reading,
}

/// A set of readings from one file (or one file group), in the order the file listed them.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Readings {
    entries: Vec<MetricReading>,
}

impl Readings {
    /// An empty set.
    #[must_use]
    pub fn new() -> Readings {
        Readings::default()
    }

    /// Push a whole reading under a metric name (the form a reader that already has a
    /// [`Reading`] uses).
    pub fn push(&mut self, metric: impl Into<String>, reading: Reading) {
        self.entries.push(MetricReading {
            metric: metric.into(),
            reading,
        });
    }

    /// Push one measured value.
    pub fn measured(&mut self, metric: impl Into<String>, value: Value) {
        self.entries.push(MetricReading {
            metric: metric.into(),
            reading: Reading::measured(value),
        });
    }

    /// Push one absence; a missing entry that cannot be built (an `ok` code) is a caller
    /// defect and is refused by the value type, so this is a no-op for `ok`.
    pub fn missing(&mut self, metric: impl Into<String>, quality: QualityCode) {
        if let Ok(reading) = Reading::missing(quality) {
            self.entries.push(MetricReading {
                metric: metric.into(),
                reading,
            });
        }
    }

    /// The entries, in order.
    #[must_use]
    pub fn entries(&self) -> &[MetricReading] {
        &self.entries
    }

    /// The number of entries.
    #[must_use]
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// True when nothing was produced.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// One metric's reading by name.
    #[must_use]
    pub fn get(&self, metric: &str) -> Option<&Reading> {
        self.entries
            .iter()
            .find(|entry| entry.metric == metric)
            .map(|entry| &entry.reading)
    }

    /// The measured `u64` of one metric, if it is one.
    #[must_use]
    pub fn u64(&self, metric: &str) -> Option<u64> {
        self.get(metric).and_then(Reading::u64)
    }

    /// The measured `f64` of one metric, if it is one.
    #[must_use]
    pub fn f64(&self, metric: &str) -> Option<f64> {
        self.get(metric).and_then(Reading::f64)
    }

    /// The quality of one metric, or `None` when it was not produced at all.
    #[must_use]
    pub fn quality(&self, metric: &str) -> Option<QualityCode> {
        self.get(metric).map(Reading::quality)
    }
}

// ---------------------------------------------------------------- cpu.stat

/// Parse `cpu.stat` (cgroup v2): `key value` lines of raw `u64` counters (µs or counts).
///
/// Metrics: `cpu.usage_usec`, `cpu.user_usec`, `cpu.system_usec`, `cpu.nr_periods`,
/// `cpu.nr_throttled`, `cpu.throttled_usec`. A key this crate does not know is skipped
/// (the kernel adds keys over time and a future kernel's `cpu.stat` must not fail a
/// reader); a known key whose value does not parse is `unreadable` for that metric only.
///
/// # Errors
///
/// [`KernelError`] when the text cannot be split at all (no line with two fields).
pub fn parse_cpu_stat(text: &[u8], bounds: Bounds) -> Result<Readings, KernelError> {
    let parsed = lines(text, bounds.max_lines).map_err(|_| KernelError::Parse {
        file: "cpu.stat",
        line: None,
    })?;
    let mut readings = Readings::new();
    let mut saw_entry = false;
    for (number, line) in parsed.iter().enumerate() {
        let (key, value) = match split_key_value(line, number) {
            Ok(pair) => pair,
            Err(error) => {
                // A blank line is not a fact. A line with one field is a corrupt file that
                // this reader cannot skip silently: the metric it names is unknown.
                let _ = error;
                return Err(KernelError::Parse {
                    file: "cpu.stat",
                    line: Some(number),
                });
            }
        };
        let metric = match key {
            b"usage_usec" => "cpu.usage_usec",
            b"user_usec" => "cpu.user_usec",
            b"system_usec" => "cpu.system_usec",
            b"nr_periods" => "cpu.nr_periods",
            b"nr_throttled" => "cpu.nr_throttled",
            b"throttled_usec" => "cpu.throttled_usec",
            _ => continue,
        };
        saw_entry = true;
        match parse_u64(value) {
            Ok(raw) => readings.measured(metric, Value::U64(raw)),
            Err(_) => readings.missing(metric, QualityCode::Unreadable),
        }
    }
    if !saw_entry {
        return Err(KernelError::Parse {
            file: "cpu.stat",
            line: None,
        });
    }
    Ok(readings)
}

// ---------------------------------------------------------------- memory.current and friends

/// Parse a single-number file (`cgroup.freeze`, `memory.current`, `pids.current`,
/// `memory.swap.current`, `memory.zswap.current`): one unsigned number, nothing else.
///
/// # Errors
///
/// `KernelError::Parse` when the file is not one number.
pub fn parse_single_u64(file: &'static str, text: &[u8]) -> Result<Reading, KernelError> {
    match parse_u64(text) {
        Ok(raw) => Ok(Reading::measured(Value::U64(raw))),
        Err(error) => {
            let _ = error;
            Err(KernelError::Parse { file, line: None })
        }
    }
}

/// Parse `memory.stat` (cgroup v2): `key value` gauge pairs, one metric per key.
///
/// This port keeps every key the file lists as `mem.stat.<key>` (the sampler's `mem.*` set
/// for the keys it knew) and does not curate a subset: a key the probe does not declare in
/// the metric registry is dropped by the registry, not by the reader, so a reader that
/// silently discarded a key would be the wrong place to make that policy.
///
/// # Errors
///
/// `KernelError::Parse` for a line that is not `key value`, or for one whose key stays
/// unknown *and* whose value does not parse.
pub fn parse_memory_stat(text: &[u8], bounds: Bounds) -> Result<Readings, KernelError> {
    let parsed = lines(text, bounds.max_lines).map_err(|_| KernelError::Parse {
        file: "memory.stat",
        line: None,
    })?;
    let mut readings = Readings::new();
    for (number, line) in parsed.iter().enumerate() {
        let (key, value) = split_key_value(line, number).map_err(|_| KernelError::Parse {
            file: "memory.stat",
            line: Some(number),
        })?;
        let Ok(name) = core::str::from_utf8(key) else {
            return Err(KernelError::Parse {
                file: "memory.stat",
                line: Some(number),
            });
        };
        let metric = format!("mem.stat.{name}");
        match parse_u64(value) {
            Ok(raw) => readings.measured(metric, Value::U64(raw)),
            Err(_) => readings.missing(metric, QualityCode::Unreadable),
        }
    }
    if readings.is_empty() {
        return Err(KernelError::Parse {
            file: "memory.stat",
            line: None,
        });
    }
    Ok(readings)
}

// ---------------------------------------------------------------- memory.events

/// Parse `memory.events` / `memory.events.local`: `key value` event counters.
///
/// The prefix is the caller's (`memev.` for the hierarchical file, `memevl.` for the
/// `.local` one) because the two files carry the same keys with different meanings: the
/// hierarchical file counts an event in every ancestor, the `.local` one only in the
/// cgroup itself. That difference is exactly what the sampler used to attribute OOM kills
/// to the deepest cgroup, and keeping it in the metric name is what lets a consumer do the
/// same without guessing.
///
/// # Errors
///
/// `KernelError::Parse` for a malformed line.
pub fn parse_memory_events(
    prefix: &'static str,
    file: &'static str,
    text: &[u8],
    bounds: Bounds,
) -> Result<Readings, KernelError> {
    let parsed =
        lines(text, bounds.max_lines).map_err(|_| KernelError::Parse { file, line: None })?;
    let mut readings = Readings::new();
    for (number, line) in parsed.iter().enumerate() {
        let (key, value) = split_key_value(line, number).map_err(|_| KernelError::Parse {
            file,
            line: Some(number),
        })?;
        let Ok(name) = core::str::from_utf8(key) else {
            return Err(KernelError::Parse {
                file,
                line: Some(number),
            });
        };
        let metric = format!("{prefix}{name}");
        match parse_u64(value) {
            Ok(raw) => readings.measured(metric, Value::U64(raw)),
            Err(_) => readings.missing(metric, QualityCode::Unreadable),
        }
    }
    Ok(readings)
}

/// Parse `cgroup.events` into its `cgevents.*` readings.
///
/// This uses the same keyed event parser as `memory.events`; an unparseable value is
/// unreadable only for the metric named by that line.
pub fn parse_cgroup_events(text: &[u8], bounds: Bounds) -> Result<Readings, KernelError> {
    parse_memory_events("cgevents.", "cgroup.events", text, bounds)
}

// ---------------------------------------------------------------- io.stat

/// What the caller knows about one block device's stacking topology.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DeviceTopology {
    /// The device has no slaves: its I/O is not counted anywhere below it.
    Bottom,
    /// The device is stacked (dm, md, …): its I/O is already counted below.
    Stacked,
    /// The topology could not be read. **Not** a bottom device: the sampler's
    /// `unwrap_or(true)` counted one, which double-counted a stacked device's I/O into a
    /// cgroup total (plan §A.5's M1 fix).
    Unknown,
}

/// The topology answer for one device name (`8:0`), from a caller that can read `/sys`.
pub trait TopologySource {
    /// The topology of `device` (a `major:minor` name as `io.stat` writes it).
    fn topology(&self, device: &[u8]) -> DeviceTopology;
}

/// A topology source used by tests and by a caller that has no `/sys` access: every device
/// is [`DeviceTopology::Unknown`], which makes every `io.*` aggregate a state.
#[derive(Clone, Copy, Debug, Default)]
pub struct UnknownTopology;

impl TopologySource for UnknownTopology {
    fn topology(&self, _device: &[u8]) -> DeviceTopology {
        DeviceTopology::Unknown
    }
}

/// A topology source that knows nothing is stacked: a fixed answer per device.
#[derive(Clone, Debug, Default)]
pub struct FixedTopology {
    devices: Vec<(Vec<u8>, DeviceTopology)>,
    default: Option<DeviceTopology>,
}

impl FixedTopology {
    /// A source whose unknown devices answer `default`.
    #[must_use]
    pub fn with_default(default: DeviceTopology) -> FixedTopology {
        FixedTopology {
            devices: Vec::new(),
            default: Some(default),
        }
    }

    /// Record one device's answer.
    pub fn set(&mut self, device: impl Into<Vec<u8>>, topology: DeviceTopology) {
        let device = device.into();
        self.devices.retain(|(name, _)| *name != device);
        self.devices.push((device, topology));
    }
}

impl TopologySource for FixedTopology {
    fn topology(&self, device: &[u8]) -> DeviceTopology {
        self.devices
            .iter()
            .find(|(name, _)| name.as_slice() == device)
            .map(|(_, topology)| *topology)
            .or(self.default)
            .unwrap_or(DeviceTopology::Unknown)
    }
}

/// The `io.stat` counters this crate publishes, in the sampler's slot order.
pub const IO_SLOTS: [&str; 6] = [
    "io.rbytes",
    "io.wbytes",
    "io.rios",
    "io.wios",
    "io.dbytes",
    "io.dios",
];

/// Parse `io.stat` and sum the **bottom** devices into the cgroup's `io.*` counters.
///
/// # Errors
///
/// [`KernelError::UnknownTopology`] when any device's topology is unknown — and then
/// **no** aggregate is produced, not even a partial one; `KernelError::Parse` for a
/// malformed line.
pub fn parse_io_stat(
    text: &[u8],
    bounds: Bounds,
    topology: &dyn TopologySource,
) -> Result<Readings, KernelError> {
    let parsed = lines(text, bounds.max_lines).map_err(|_| KernelError::Parse {
        file: "io.stat",
        line: None,
    })?;
    let mut totals = [0u64; IO_SLOTS.len()];
    let mut bad = false;
    let mut unknown = false;
    let mut cost = [0u64; 3];
    let mut have_cost = false;
    let mut bad_cost = false;
    let mut vrate: Option<f64> = None;
    let mut saw_device = false;
    for (number, line) in parsed.iter().enumerate() {
        let parts = fields(line);
        let Some(device) = parts.first() else {
            return Err(KernelError::Parse {
                file: "io.stat",
                line: Some(number),
            });
        };
        saw_device = true;
        let topology_answer = topology.topology(device);
        if topology_answer == DeviceTopology::Unknown {
            unknown = true;
        }
        let bottom = topology_answer == DeviceTopology::Bottom;
        for part in parts.iter().skip(1) {
            let (key, value) = split_pair(part, 0).map_err(|_| KernelError::Parse {
                file: "io.stat",
                line: Some(number),
            })?;
            let slot = match key {
                b"rbytes" => 0,
                b"wbytes" => 1,
                b"rios" => 2,
                b"wios" => 3,
                b"dbytes" => 4,
                b"dios" => 5,
                b"cost.vrate" => {
                    // The virtual rate is a per-device gauge; the sampler published the
                    // highest one across the devices of the cgroup.
                    match parse_f64(value) {
                        Ok(rate) => {
                            vrate = Some(vrate.map_or(rate, |previous: f64| previous.max(rate)));
                        }
                        Err(_) => bad = true,
                    }
                    continue;
                }
                b"cost.usage" | b"cost.wait" | b"cost.indelay" => {
                    have_cost = true;
                    let index = match key {
                        b"cost.usage" => 0,
                        b"cost.wait" => 1,
                        _ => 2,
                    };
                    match parse_u64(value) {
                        // Same rule as the counters below: a sum that overflows is a
                        // corrupt file, not a clamped total. `saturating_add` would
                        // silently cap at `u64::MAX` and report a wrong number as a
                        // measurement, which is exactly what a saturating sum must not do
                        // here (it is fine for a bound or a budget, not for a reading).
                        Ok(raw) => match cost[index].checked_add(raw) {
                            Some(total) => cost[index] = total,
                            None => bad_cost = true,
                        },
                        Err(_) => bad_cost = true,
                    }
                    continue;
                }
                _ => continue,
            };
            if bottom {
                match parse_u64(value) {
                    // A sum that overflows is a corrupt file, not a wrapped total.
                    Ok(raw) => match totals[slot].checked_add(raw) {
                        Some(total) => totals[slot] = total,
                        None => bad = true,
                    },
                    Err(_) => bad = true,
                }
            }
        }
    }
    if !saw_device {
        // An empty `io.stat` is legal on a cgroup with no I/O controller. It carries no
        // device, so every counter is a genuine zero reading... except that a zero needs a
        // measurement behind it, and "no devices" is not one.
        return Ok(Readings::new());
    }
    let mut readings = Readings::new();
    // The order of the checks is the plan's: unknown topology first (it makes every value
    // meaningless), then a failed parse, then values.
    if unknown {
        return Err(KernelError::UnknownTopology);
    }
    for (slot, metric) in IO_SLOTS.iter().enumerate() {
        if bad {
            readings.missing(*metric, QualityCode::Unreadable);
        } else {
            readings.measured(*metric, Value::U64(totals[slot]));
        }
    }
    if have_cost && bad_cost {
        for metric in ["io.cost.usage", "io.cost.wait", "io.cost.indelay"] {
            readings.missing(metric, QualityCode::Unreadable);
        }
    } else if have_cost {
        for (index, metric) in ["io.cost.usage", "io.cost.wait", "io.cost.indelay"]
            .iter()
            .enumerate()
        {
            readings.measured(*metric, Value::U64(cost[index]));
        }
    } else {
        // iocost exists only on devices where it is enabled; that is a feature absence,
        // and the sampler said so with NA_FEATURE.
        for metric in ["io.cost.usage", "io.cost.wait", "io.cost.indelay"] {
            readings.missing(metric, QualityCode::Unsupported);
        }
    }
    match vrate {
        Some(rate) => readings.measured("io.cost.vrate", Value::F64(rate)),
        None => readings.missing("io.cost.vrate", QualityCode::Unsupported),
    }
    Ok(readings)
}

// ---------------------------------------------------------------- pids.current and limits

/// Parse a cgroup limit file whose value may be the word `max`.
///
/// `memory.max`, `memory.high`, `memory.swap.max`, `cpu.max` (two fields: quota and
/// period), `pids.max` and `io.max` all express "no limit" as `max`. The sampler encoded
/// it as `-1.0`; that is a sentinel a consumer must remember to special-case and is
/// indistinguishable from a real negative reading, so here it is
/// [`QualityCode::Unsupported`] with the text kept in [`Limit::raw`].
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Limit {
    /// The file the limit came from (`memory.max`, `cpu.max`, …).
    pub file: &'static str,
    /// The parsed number, or `None` for a `max` (and for an unparsable value).
    pub value: Option<u64>,
    /// The trimmed raw text, so an operator can see what the kernel actually wrote.
    pub raw: Vec<u8>,
    /// The quality of `value`.
    pub quality: QualityCode,
}

impl Limit {
    /// The reading for this limit's first value.
    #[must_use]
    pub fn reading(&self) -> Reading {
        match (self.value, self.quality) {
            (Some(raw), QualityCode::Ok) => Reading::measured(Value::U64(raw)),
            (_, code) => Reading::missing(code).unwrap_or(Reading::from_raw(Value::U64(0), code)),
        }
    }
}

/// Parse one limit file (`memory.max`, `pids.max`, …): a number, or `max`.
///
/// # Errors
///
/// `KernelError::Parse` when the file is neither a number nor `max`.
pub fn parse_limit(file: &'static str, text: &[u8]) -> Result<Limit, KernelError> {
    let raw = trim(text).to_vec();
    if raw == b"max" {
        return Ok(Limit {
            file,
            value: None,
            raw,
            quality: QualityCode::Unsupported,
        });
    }
    match parse_u64(&raw) {
        Ok(value) => Ok(Limit {
            file,
            value: Some(value),
            raw,
            quality: QualityCode::Ok,
        }),
        Err(error) => {
            let _ = error;
            Err(KernelError::Parse { file, line: None })
        }
    }
}

/// The two values of `cpu.max` (`quota period`, where quota may be `max`).
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct CpuMax {
    /// The quota in microseconds, or `None` for `max` (no quota).
    pub quota_usec: Option<u64>,
    /// The period in microseconds.
    pub period_usec: u64,
}

/// Parse `cpu.max`: `quota period`, `max period`.
///
/// # Errors
///
/// `KernelError::Parse` for a shape that is not two fields, or a period that does not
/// parse.
pub fn parse_cpu_max(text: &[u8]) -> Result<CpuMax, KernelError> {
    let parts = fields(text);
    if parts.len() != 2 {
        return Err(KernelError::Parse {
            file: "cpu.max",
            line: None,
        });
    }
    let quota = if parts[0] == b"max" {
        None
    } else {
        Some(parse_u64(parts[0]).map_err(|_| KernelError::Parse {
            file: "cpu.max",
            line: None,
        })?)
    };
    let period = parse_u64(parts[1]).map_err(|_| KernelError::Parse {
        file: "cpu.max",
        line: None,
    })?;
    Ok(CpuMax {
        quota_usec: quota,
        period_usec: period,
    })
}

// ---------------------------------------------------------------- PSI

/// One pressure resource (`cpu`, `memory`, `io`).
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PressureResource {
    /// CPU pressure.
    Cpu,
    /// Memory pressure.
    Memory,
    /// I/O pressure.
    Io,
}

impl PressureResource {
    /// The metric-name segment.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            PressureResource::Cpu => "cpu",
            PressureResource::Memory => "memory",
            PressureResource::Io => "io",
        }
    }
}

/// Parse one pressure file (`cpu.pressure`, `memory.pressure`, `io.pressure`).
///
/// The `avg10` values are gauges and the `total` values are cumulative counters (µs),
/// which is the split the sampler made. A file without a `full` line reports
/// `psi.<res>.full.*` as `unsupported`: the kernel omits `full` for resources where it has
/// no meaning (CPU), and that is a fact about the file, not about a missing reading.
///
/// # Errors
///
/// `KernelError::Parse` for a malformed line; `KernelError::Absent(caller code)` is the
/// caller's job (an absent pressure file is a kernel feature).
pub fn parse_pressure(
    resource: PressureResource,
    text: &[u8],
    bounds: Bounds,
) -> Result<Readings, KernelError> {
    let parsed = lines(text, bounds.max_lines).map_err(|_| KernelError::Parse {
        file: "pressure",
        line: None,
    })?;
    let mut readings = Readings::new();
    let mut saw_some = false;
    let mut saw_full = false;
    for (number, line) in parsed.iter().enumerate() {
        let parts = fields(line);
        let kind = match parts.first().copied() {
            Some(b"some") => {
                saw_some = true;
                "some"
            }
            Some(b"full") => {
                saw_full = true;
                "full"
            }
            _ => {
                return Err(KernelError::Parse {
                    file: "pressure",
                    line: Some(number),
                })
            }
        };
        for part in parts.iter().skip(1) {
            let (key, value) = split_pair(part, 0).map_err(|_| KernelError::Parse {
                file: "pressure",
                line: Some(number),
            })?;
            let (suffix, is_counter) = match key {
                b"avg10" => ("avg10", false),
                b"avg60" => ("avg60", false),
                b"avg300" => ("avg300", false),
                b"total" => ("total", true),
                _ => continue,
            };
            let metric = format!("psi.{}.{kind}.{suffix}", resource.name());
            if is_counter {
                match parse_u64(value) {
                    Ok(raw) => readings.measured(metric, Value::U64(raw)),
                    Err(_) => readings.missing(metric, QualityCode::Unreadable),
                }
            } else {
                match parse_f64(value) {
                    Ok(ratio) => readings.measured(metric, Value::F64(ratio)),
                    Err(_) => readings.missing(metric, QualityCode::Unreadable),
                }
            }
        }
    }
    if !saw_some {
        return Err(KernelError::Parse {
            file: "pressure",
            line: None,
        });
    }
    if !saw_full {
        for suffix in ["avg10", "avg60", "avg300", "total"] {
            readings.missing(
                format!("psi.{}.full.{suffix}", resource.name()),
                QualityCode::Unsupported,
            );
        }
    }
    Ok(readings)
}

// ---------------------------------------------------------------- the file bridge

/// Read one file below a cgroup directory and count it as a cgroup fact.
///
/// This is the only place a cgroup reader touches [`Fs`]: the path is built from the root
/// and the file name, the read is bounded by [`Bounds::max_file_bytes`], and an absence is
/// mapped to the code the caller declared.
///
/// # Errors
///
/// [`KernelError::Absent`] for a missing file, [`KernelError::Read`] for a failure.
pub fn read_file<F: Fs>(
    fs: &F,
    dir: &Path,
    file: &str,
    bounds: Bounds,
    absent: Absence,
) -> Result<Vec<u8>, KernelError> {
    let path = dir.join(file);
    match fs.read(&path, bounds.max_file_bytes) {
        Ok(bytes) => Ok(bytes),
        Err(error) => Err(read_file_error(error, absent)),
    }
}

/// Map one [`FsError`] to a [`KernelError`] with the caller's absence code.
///
/// The same rule [`read_file`] applies, exposed so a reader in another module (`procfs`)
/// maps a host-wide path without duplicating the table.
#[must_use]
pub fn read_file_error(error: FsError, absent: Absence) -> KernelError {
    match error {
        FsError::NotFound | FsError::NotAFile | FsError::NotADirectory => {
            KernelError::Absent(absent.quality())
        }
        other => KernelError::Read(other),
    }
}

/// The cgroup files one pass reads, with their absence semantics.
pub const CGROUP_FILES: [(&str, Absence); 13] = [
    ("cgroup.events", Absence::Feature),
    ("cgroup.freeze", Absence::Feature),
    ("cpu.stat", Absence::Controller),
    ("cpu.max", Absence::Controller),
    ("cpu.weight", Absence::Controller),
    ("memory.current", Absence::Controller),
    ("memory.stat", Absence::Controller),
    ("memory.events", Absence::Controller),
    ("memory.max", Absence::Controller),
    ("pids.current", Absence::Controller),
    ("pids.max", Absence::Controller),
    ("io.stat", Absence::Controller),
    ("memory.swap.current", Absence::Controller),
];

/// A whole cgroup directory's readings: one file's readings per file, keyed by file name.
///
/// A file that could not be read is present under its name as a [`KernelError`], so a
/// consumer can tell "the controller is off" from "this file was not asked for".
#[derive(Clone, Debug, Default, PartialEq)]
pub struct CgroupSnapshot {
    /// The files that produced readings.
    pub readings: Vec<(String, Readings)>,
    /// The files that could not be used, with their reason.
    pub failures: Vec<(String, KernelError)>,
}

impl CgroupSnapshot {
    /// One file's readings.
    #[must_use]
    pub fn readings_of(&self, file: &str) -> Option<&Readings> {
        self.readings
            .iter()
            .find(|(name, _)| name == file)
            .map(|(_, readings)| readings)
    }

    /// One file's failure.
    #[must_use]
    pub fn failure_of(&self, file: &str) -> Option<&KernelError> {
        self.failures
            .iter()
            .find(|(name, _)| name == file)
            .map(|(_, error)| error)
    }
}

/// Read and parse one whole cgroup directory, file by file.
///
/// The order of the files is [`CGROUP_FILES`]'s, so two runs over the same tree produce the
/// same `CgroupSnapshot` (a test asserts it).
pub fn read_cgroup<F: Fs>(
    fs: &F,
    dir: &Path,
    bounds: Bounds,
    topology: &dyn TopologySource,
) -> CgroupSnapshot {
    let mut snapshot = CgroupSnapshot::default();
    for (file, absent) in CGROUP_FILES {
        let parsed = match read_file(fs, dir, file, bounds, absent) {
            Ok(bytes) => parse_cgroup_file(file, &bytes, bounds, topology),
            Err(error) => Err(error),
        };
        match parsed {
            Ok(readings) => snapshot.readings.push((file.to_string(), readings)),
            Err(error) => snapshot.failures.push((file.to_string(), error)),
        }
    }
    snapshot
}

fn parse_cgroup_file(
    file: &str,
    bytes: &[u8],
    bounds: Bounds,
    topology: &dyn TopologySource,
) -> Result<Readings, KernelError> {
    match file {
        "cgroup.events" => parse_cgroup_events(bytes, bounds),
        "cgroup.freeze" => {
            let reading = parse_single_u64("cgroup.freeze", bytes)?;
            if !matches!(reading.u64(), Some(0 | 1)) {
                return Err(KernelError::Parse {
                    file: "cgroup.freeze",
                    line: None,
                });
            }
            let mut readings = Readings::new();
            readings.push("cgroup.freeze", reading);
            Ok(readings)
        }
        "cpu.stat" => parse_cpu_stat(bytes, bounds),
        "cpu.max" => {
            let limit = parse_cpu_max(bytes)?;
            let mut readings = Readings::new();
            match limit.quota_usec {
                Some(quota) => readings.measured("limit.cpu.max.quota", Value::U64(quota)),
                None => readings.missing("limit.cpu.max.quota", QualityCode::Unsupported),
            }
            readings.measured("limit.cpu.max.period", Value::U64(limit.period_usec));
            Ok(readings)
        }
        "cpu.weight" => {
            let mut readings = Readings::new();
            readings.measured(
                "limit.cpu.weight",
                Value::U64(parse_u64(bytes).map_err(|_| KernelError::Parse {
                    file: "cpu.weight",
                    line: None,
                })?),
            );
            Ok(readings)
        }
        "memory.current" => {
            let mut readings = Readings::new();
            let reading = parse_single_u64("memory.current", bytes)?;
            readings.push("mem.current", reading);
            Ok(readings)
        }
        "memory.stat" => parse_memory_stat(bytes, bounds),
        "memory.events" => parse_memory_events("memev.", "memory.events", bytes, bounds),
        "memory.max" => limit_reading("memory.max", "limit.memory.max", bytes),
        "pids.current" => {
            let mut readings = Readings::new();
            let reading = parse_single_u64("pids.current", bytes)?;
            readings.push("pids.current", reading);
            Ok(readings)
        }
        "pids.max" => limit_reading("pids.max", "limit.pids.max", bytes),
        "io.stat" => parse_io_stat(bytes, bounds, topology),
        "memory.swap.current" => {
            let mut readings = Readings::new();
            let reading = parse_single_u64("memory.swap.current", bytes)?;
            readings.push("mem.swap_current", reading);
            Ok(readings)
        }
        _ => Err(KernelError::NotAValue),
    }
}

fn limit_reading(
    file: &'static str,
    metric: &'static str,
    bytes: &[u8],
) -> Result<Readings, KernelError> {
    let limit = parse_limit(file, bytes)?;
    let mut readings = Readings::new();
    let reading = limit.reading();
    readings.push(metric, reading);
    Ok(readings)
}

/// The path of one cgroup directory below a root (`""` is the root itself).
#[must_use]
pub fn cgroup_dir(root: &Path, relative: &str) -> PathBuf {
    if relative.is_empty() {
        return root.to_path_buf();
    }
    let mut path = root.to_path_buf();
    for part in relative.split('/').filter(|part| !part.is_empty()) {
        path.push(part);
    }
    path
}
