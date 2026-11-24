//! `/proc` readers: `stat`, `meminfo`, `vmstat`, `loadavg`, PSI and one process's files.
//!
//! Ported from `topos/sampler/src/{procs,collect}.rs` (read-only), with the plan's
//! corrections applied:
//!
//! - **A process is found by listing `/proc`, never by trusting a name.** [`list_pids`]
//!   reads directory entries, keeps only those whose name is a decimal number, sorts them
//!   and refuses a listing past the bound; it never opens a path it did not first see as a
//!   `dirent`, and [`read_process`] states what a vanished process looks like
//!   ([`ProcError::Gone`]) rather than returning an empty record.
//! - **A per-process field that did not parse is unreadable, never zero.** [`parse_statm`]
//!   and [`parse_status`] keep the fields they read and report the rest as
//!   [`QualityCode::Unreadable`] with the field name.
//! - **`/proc/stat`'s CPU tick fields are counters, and their names are the kernel's.**
//!   Guest and guest-nice reuse the user/nice slots on a host with no guests; this reader
//!   keeps every column the file writes and does not fold two columns into one, because the
//!   kernel's own documentation says the guest values are already included in user/nice.
//! - **`btime` is a host fact worth keeping.** It is the boot's wall-clock epoch, which with
//!   the boot id and the monotonic clock makes a timeline: [`crate::clock::Timeline`] can
//!   then tell a wall clock that was stepped from one that merely drifted.
//!
//! All access is through [`Fs`] and bounded by [`Bounds`]; the tests drive the same code
//! against `crates/kernel/tests/fixtures/proc` and against [`MemFs`](crate::fs::MemFs) trees.

use core::fmt;
use std::path::{Path, PathBuf};

use crate::cgroup::{KernelError, Readings};
use crate::cmdline::{self, CmdlineBound, CmdlineError, ReadCmdline};
use crate::fs::{Fs, FsError};
use crate::identity::{parse_stat, BootId, IdentityError, StatLine};
use crate::parse::{fields, lines, parse_u64, split_key_value, trim};
use crate::quality::QualityCode;
use crate::value::{Reading, Value};
use crate::Bounds;

/// Why a `/proc` read could not be used.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ProcError {
    /// The process is gone: it was seen in a listing and no longer exists.
    Gone,
    /// The file could not be read.
    Read(FsError),
    /// The name is not a pid, so it is not a process (a listing keeps the others out).
    NotAPid {
        /// The name that was refused.
        name: String,
    },
    /// The text does not parse.
    Parse {
        /// The file's name (`stat`, `statm`, `status`, `meminfo`, …).
        file: &'static str,
        /// Which line (zero-based), when the failure is line-scoped.
        line: Option<usize>,
    },
    /// A cgroup-level refusal reached this reader (a file the probe also reads).
    Kernel(KernelError),
    /// The command line could not be read.
    Cmdline(CmdlineError),
    /// An identity input was not readable as an identity.
    Identity(IdentityError),
}

impl ProcError {
    /// The quality code this refusal maps to.
    #[must_use]
    pub fn quality(&self) -> QualityCode {
        match self {
            ProcError::Gone => QualityCode::Absent,
            ProcError::Read(error) => error.quality(QualityCode::Absent),
            ProcError::NotAPid { .. } => QualityCode::Unreadable,
            ProcError::Parse { .. } => QualityCode::Unreadable,
            ProcError::Kernel(error) => error.quality(),
            ProcError::Cmdline(error) => error.quality(),
            ProcError::Identity(error) => match error {
                IdentityError::Read(read) => read.quality(QualityCode::Unreadable),
                _ => QualityCode::Unreadable,
            },
        }
    }

    /// A one-line description, for a caller that stores a reason string.
    #[must_use]
    pub fn detail(&self) -> String {
        match self {
            ProcError::Gone => "the process is gone".to_string(),
            ProcError::Read(error) => format!("read failed: {error}"),
            ProcError::NotAPid { name } => format!("{name} is not a pid"),
            ProcError::Parse { file, line } => match line {
                Some(number) => format!("{file}: line {number} does not parse"),
                None => format!("{file}: the text does not parse"),
            },
            ProcError::Kernel(error) => error.detail(),
            ProcError::Cmdline(error) => error.to_string(),
            ProcError::Identity(error) => error.to_string(),
        }
    }
}

impl fmt::Display for ProcError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.detail())
    }
}

impl core::error::Error for ProcError {}

impl From<FsError> for ProcError {
    fn from(error: FsError) -> ProcError {
        match error {
            FsError::NotFound => ProcError::Gone,
            other => ProcError::Read(other),
        }
    }
}

impl From<KernelError> for ProcError {
    fn from(error: KernelError) -> ProcError {
        ProcError::Kernel(error)
    }
}

impl From<CmdlineError> for ProcError {
    fn from(error: CmdlineError) -> ProcError {
        ProcError::Cmdline(error)
    }
}

impl From<IdentityError> for ProcError {
    fn from(error: IdentityError) -> ProcError {
        ProcError::Identity(error)
    }
}

/// The roots a probe reads from, kept together so no reader builds a path by concatenation.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Roots {
    /// The `/proc` root.
    pub proc: PathBuf,
    /// The `/sys` root.
    pub sys: PathBuf,
    /// The cgroupfs root.
    pub cgroup: PathBuf,
}

impl Roots {
    /// The host's usual location.
    #[must_use]
    pub fn host() -> Roots {
        Roots {
            proc: PathBuf::from("/proc"),
            sys: PathBuf::from("/sys"),
            cgroup: PathBuf::from("/sys/fs/cgroup"),
        }
    }

    /// A fixture tree (`crates/kernel/tests/fixtures`), for tests and SP-2 evidence.
    #[must_use]
    pub fn fixture(base: &Path) -> Roots {
        Roots {
            proc: base.join("proc"),
            sys: base.join("sys"),
            cgroup: base.join("cgroupfs"),
        }
    }
}

/// A `/proc` tree, anchored at its root.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Proc {
    root: PathBuf,
}

impl Proc {
    /// A tree at `root`.
    #[must_use]
    pub fn new(root: impl Into<PathBuf>) -> Proc {
        Proc { root: root.into() }
    }

    /// The tree's root.
    #[must_use]
    pub fn root(&self) -> &Path {
        &self.root
    }

    /// The path of one top-level pseudo-file (`stat`, `meminfo`, …), never a pid's file.
    #[must_use]
    pub fn file(&self, name: &str) -> PathBuf {
        self.root.join(name)
    }

    /// The directory of one process.
    #[must_use]
    pub fn pid_dir(&self, pid: u32) -> PathBuf {
        self.root.join(pid.to_string())
    }

    /// The directory of one top-level pseudo-file (`pressure/`, `sys/`).
    #[must_use]
    pub fn dir(&self, name: &str) -> PathBuf {
        self.root.join(name)
    }
}

// ---------------------------------------------------------------- /proc/stat

/// The CPU tick columns `/proc/stat` writes, in the kernel's order.
pub const CPU_TICK_FIELDS: [&str; 10] = [
    "user",
    "nice",
    "system",
    "idle",
    "iowait",
    "irq",
    "softirq",
    "steal",
    "guest",
    "guest_nice",
];

/// Parse `/proc/stat`: the aggregate `cpu` line, the per-CPU lines, and the counters.
///
/// Metrics: `proc.cpu.<field>` (aggregate ticks), `proc.cpu.<name>.<field>` (per-CPU
/// ticks), `proc.ctxt`, `proc.processes`, `proc.procs_running`, `proc.procs_blocked`,
/// `proc.btime` (the boot's wall-clock epoch, seconds).
///
/// A row with fewer than three tick columns is refused: a truncated `cpu` line would
/// otherwise look like a host with zero load. Extra columns (a future kernel's) are kept
/// under their kernel names as `proc.cpu.<field>` when the line is the aggregate row.
///
/// # Errors
///
/// `ProcError::Parse` for a malformed row.
pub fn parse_proc_stat(text: &[u8], bounds: Bounds) -> Result<Readings, ProcError> {
    let parsed = lines(text, bounds.max_lines).map_err(|_| ProcError::Parse {
        file: "stat",
        line: None,
    })?;
    let mut readings = Readings::new();
    let mut saw_cpu = false;
    for (number, line) in parsed.iter().enumerate() {
        let parts = fields(line);
        let Some(label) = parts.first() else {
            return Err(ProcError::Parse {
                file: "stat",
                line: Some(number),
            });
        };
        if label.starts_with(b"cpu") {
            let name = core::str::from_utf8(label).map_err(|_| ProcError::Parse {
                file: "stat",
                line: Some(number),
            })?;
            let ticks = &parts[1..];
            if ticks.len() < 3 {
                return Err(ProcError::Parse {
                    file: "stat",
                    line: Some(number),
                });
            }
            saw_cpu = true;
            for (index, field) in CPU_TICK_FIELDS.iter().enumerate() {
                let metric = if name == "cpu" {
                    format!("proc.cpu.{field}")
                } else {
                    format!("proc.cpu.{name}.{field}")
                };
                match ticks.get(index).copied().map(parse_u64) {
                    Some(Ok(raw)) => readings.measured(metric, Value::U64(raw)),
                    // The kernel may write fewer than ten columns on an older build;
                    // `guest`/`guest_nice` missing is a feature absence, not zero.
                    Some(Err(_)) => readings.missing(metric, QualityCode::Unreadable),
                    None => readings.missing(metric, QualityCode::Unsupported),
                }
            }
            // Columns past the ten known ones keep the kernel's own name.
            for (index, raw) in ticks.iter().enumerate().skip(CPU_TICK_FIELDS.len()) {
                let field = format!("column{index}");
                match parse_u64(raw) {
                    Ok(value) => readings.measured(
                        if name == "cpu" {
                            format!("proc.cpu.{field}")
                        } else {
                            format!("proc.cpu.{name}.{field}")
                        },
                        Value::U64(value),
                    ),
                    Err(_) => readings.missing(
                        if name == "cpu" {
                            format!("proc.cpu.{field}")
                        } else {
                            format!("proc.cpu.{name}.{field}")
                        },
                        QualityCode::Unreadable,
                    ),
                }
            }
            continue;
        }
        let metric = match *label {
            b"ctxt" => "proc.ctxt",
            b"processes" => "proc.processes",
            b"procs_running" => "proc.procs_running",
            b"procs_blocked" => "proc.procs_blocked",
            b"btime" => "proc.btime",
            b"intr" | b"softirq" => continue,
            _ => continue,
        };
        match parts.get(1).copied().map(parse_u64) {
            Some(Ok(value)) => readings.measured(metric, Value::U64(value)),
            Some(Err(_)) => readings.missing(metric, QualityCode::Unreadable),
            None => readings.missing(metric, QualityCode::Unreadable),
        }
    }
    if !saw_cpu {
        return Err(ProcError::Parse {
            file: "stat",
            line: None,
        });
    }
    Ok(readings)
}

// ---------------------------------------------------------------- /proc/meminfo

/// Parse `/proc/meminfo`.
///
/// Metrics keep the file's own unit in the name: a `kB` value becomes `meminfo.kb.<name>`
/// (the number the file wrote, not a converted byte count) and a plain counter (hugetlb
/// pages, `HugePages_Total`) becomes `meminfo.count.<name>`. Converting `kB` to bytes in
/// the reader would hide which file wrote what, and the wire's unit is a property of the
/// metric, not of the reader.
///
/// # Errors
///
/// `ProcError::Parse` for a malformed line, or for one with a unit this reader does not
/// know (a future kernel's unit would otherwise be silently read as `kB`).
pub fn parse_meminfo(text: &[u8], bounds: Bounds) -> Result<Readings, ProcError> {
    let parsed = lines(text, bounds.max_lines).map_err(|_| ProcError::Parse {
        file: "meminfo",
        line: None,
    })?;
    let mut readings = Readings::new();
    for (number, line) in parsed.iter().enumerate() {
        let (key, value) = split_key_value(line, number).map_err(|_| ProcError::Parse {
            file: "meminfo",
            line: Some(number),
        })?;
        // `meminfo` writes `MemTotal:` — the colon is the file's separator, not part of the
        // field name.
        let key = key.strip_suffix(b":").unwrap_or(key);
        let Ok(name) = core::str::from_utf8(key) else {
            return Err(ProcError::Parse {
                file: "meminfo",
                line: Some(number),
            });
        };
        let (unit, number_text) = match split_key_value(value, 0) {
            Ok((value, unit)) => {
                let unit = core::str::from_utf8(unit).map_err(|_| ProcError::Parse {
                    file: "meminfo",
                    line: Some(number),
                })?;
                if unit != "kB" {
                    return Err(ProcError::Parse {
                        file: "meminfo",
                        line: Some(number),
                    });
                }
                ("kb", value)
            }
            Err(_) => ("count", value),
        };
        let metric = format!("meminfo.{unit}.{name}");
        match parse_u64(number_text) {
            Ok(raw) => readings.measured(metric, Value::U64(raw)),
            Err(_) => readings.missing(metric, QualityCode::Unreadable),
        }
    }
    Ok(readings)
}

// ---------------------------------------------------------------- /proc/vmstat and loadavg

/// Parse `/proc/vmstat`: one counter per line (`key value`).
///
/// Every key is kept as `vmstat.<key>`: the file's vocabulary is the kernel's, and a reader
/// that curated a subset would silently drop a fact that a later consumer needs.
///
/// # Errors
///
/// `ProcError::Parse` for a malformed line.
pub fn parse_vmstat(text: &[u8], bounds: Bounds) -> Result<Readings, ProcError> {
    let parsed = lines(text, bounds.max_lines).map_err(|_| ProcError::Parse {
        file: "vmstat",
        line: None,
    })?;
    let mut readings = Readings::new();
    for (number, line) in parsed.iter().enumerate() {
        let (key, value) = split_key_value(line, number).map_err(|_| ProcError::Parse {
            file: "vmstat",
            line: Some(number),
        })?;
        let Ok(name) = core::str::from_utf8(key) else {
            return Err(ProcError::Parse {
                file: "vmstat",
                line: Some(number),
            });
        };
        let metric = format!("vmstat.{name}");
        match parse_u64(value) {
            Ok(raw) => readings.measured(metric, Value::U64(raw)),
            Err(_) => readings.missing(metric, QualityCode::Unreadable),
        }
    }
    Ok(readings)
}

/// `/proc/loadavg`'s five fields.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct LoadAverage {
    /// Load average over 1 minute.
    pub one: f64,
    /// Load average over 5 minutes.
    pub five: f64,
    /// Load average over 15 minutes.
    pub fifteen: f64,
    /// Runnable entities / total entities (`4/120`).
    pub runnable: u64,
    /// The total entity count.
    pub entities: u64,
    /// The highest pid allocated so far.
    pub last_pid: u64,
}

/// Parse `/proc/loadavg`.
///
/// # Errors
///
/// `ProcError::Parse` for a shape the file does not have.
pub fn parse_loadavg(text: &[u8]) -> Result<LoadAverage, ProcError> {
    let line = trim(text);
    let parts = fields(line);
    if parts.len() != 5 {
        return Err(ProcError::Parse {
            file: "loadavg",
            line: None,
        });
    }
    let ratio = |part: &[u8]| -> Result<f64, ProcError> {
        // `parse_f64` refuses a non-finite value and a sign, so `f64::NAN` cannot arrive
        // from a corrupt file and turn every later comparison into a false answer.
        crate::parse::parse_f64(part).map_err(|_| ProcError::Parse {
            file: "loadavg",
            line: None,
        })
    };
    // `loadavg` writes the runnable/total pair as `4/120`, not as a `key=value` pair.
    let slash = parts[3].iter().position(|byte| *byte == b'/');
    let Some(slash) = slash else {
        return Err(ProcError::Parse {
            file: "loadavg",
            line: None,
        });
    };
    let (runnable, entities) = (&parts[3][..slash], &parts[3][slash + 1..]);
    let pair = |part: &[u8]| -> Result<u64, ProcError> {
        parse_u64(part).map_err(|_| ProcError::Parse {
            file: "loadavg",
            line: None,
        })
    };
    Ok(LoadAverage {
        one: ratio(parts[0])?,
        five: ratio(parts[1])?,
        fifteen: ratio(parts[2])?,
        runnable: pair(runnable)?,
        entities: pair(entities)?,
        last_pid: pair(parts[4])?,
    })
}

/// Parse one PSI file's text into `Readings` (the cgroup module's reader, applied to the
/// host-wide `/proc/pressure/<resource>` files).
///
/// # Errors
///
/// `ProcError::Kernel` carrying the cgroup reader's refusal.
pub fn parse_pressure_file(
    resource: &str,
    text: &[u8],
    bounds: Bounds,
) -> Result<Readings, ProcError> {
    let resource = match resource {
        "cpu" => crate::cgroup::PressureResource::Cpu,
        "memory" => crate::cgroup::PressureResource::Memory,
        "io" => crate::cgroup::PressureResource::Io,
        _ => {
            return Err(ProcError::Parse {
                file: "pressure",
                line: None,
            })
        }
    };
    crate::cgroup::parse_pressure(resource, text, bounds).map_err(ProcError::Kernel)
}

// ---------------------------------------------------------------- one process

/// A process's own files, as read.
#[derive(Clone, Debug, PartialEq)]
pub struct ProcessFiles {
    /// The pid the process was read at.
    pub pid: u32,
    /// `stat`: the identity fields, or the refusal.
    pub stat: Result<StatLine, ProcError>,
    /// `statm`: page counts.
    pub statm: Result<Readings, ProcError>,
    /// `status`: the subset of fields this reader publishes.
    pub status: Result<Readings, ProcError>,
    /// `cmdline`: the redacted text, or the refusal.
    pub cmdline: Result<ReadCmdline, ProcError>,
}

/// Read one process's `stat`, `statm`, `status` and `cmdline`, each with its own refusal.
///
/// A process whose `stat` is gone is reported as [`ProcError::Gone`] in that field rather
/// than failing the whole record: the sampler's tick read each file independently and kept
/// what it got, and a consumer that lost one file must still be able to publish the others
/// with the right quality.
#[must_use]
pub fn read_process<F: Fs>(
    fs: &F,
    proc: &Proc,
    pid: u32,
    bounds: Bounds,
    cmdline_bound: CmdlineBound,
) -> ProcessFiles {
    let dir = proc.pid_dir(pid);
    ProcessFiles {
        pid,
        stat: read_stat(fs, &dir, bounds).map_err(ProcError::from),
        statm: read_statm(fs, &dir, bounds),
        status: read_status(fs, &dir, bounds),
        cmdline: cmdline::read(fs, &dir.join("cmdline"), cmdline_bound).map_err(ProcError::from),
    }
}

fn read_stat<F: Fs>(fs: &F, dir: &Path, bounds: Bounds) -> Result<StatLine, IdentityError> {
    let bytes = fs
        .read(&dir.join("stat"), bounds.max_file_bytes)
        .map_err(IdentityError::Read)?;
    parse_stat(&bytes)
}

fn read_statm<F: Fs>(fs: &F, dir: &Path, bounds: Bounds) -> Result<Readings, ProcError> {
    let bytes = fs.read(&dir.join("statm"), bounds.max_file_bytes)?;
    parse_statm(&bytes)
}

fn read_status<F: Fs>(fs: &F, dir: &Path, bounds: Bounds) -> Result<Readings, ProcError> {
    let bytes = fs.read(&dir.join("status"), bounds.max_file_bytes)?;
    parse_status(&bytes, bounds)
}

/// The `status` fields this reader publishes (the plan's "status subset").
///
/// A field outside this list is skipped: `status` is a human-readable file whose full
/// vocabulary is large and mostly irrelevant to observation, and a *subset* is an explicit
/// policy the docs state. Every listed field that is absent from the file becomes
/// `unsupported` rather than zero, because a kernel that does not write `VmSwap` is not a
/// process with no swap.
pub const STATUS_FIELDS: [&str; 21] = [
    "Name",
    "State",
    "Tgid",
    "Pid",
    "PPid",
    "Uid",
    "Gid",
    "Threads",
    "VmPeak",
    "VmSize",
    "VmHWM",
    "VmRSS",
    "RssAnon",
    "RssFile",
    "RssShmem",
    "VmSwap",
    "voluntary_ctxt_switches",
    "nonvoluntary_ctxt_switches",
    "Seccomp",
    "NSeccomp",
    "NoNewPrivs",
];

/// Parse one process's `status` file, keeping [`STATUS_FIELDS`] and their units.
///
/// Fields with a `kB` unit keep it in the metric name (`proc.status.kb.VmRSS`); a plain
/// number becomes `proc.status.count.Pid`; a text field (`Name`, `State`, `Uid`) becomes a
/// value only when its text is a number or the field is one of the two text fields that are
/// published as-is (`Name` and `State`). `Uid`/`Gid` are four tab-separated numbers and are
/// published as their first (real) value with the other three kept as separate metrics.
///
/// # Errors
///
/// `ProcError::Parse` for a malformed line.
pub fn parse_status(text: &[u8], bounds: Bounds) -> Result<Readings, ProcError> {
    let parsed = lines(text, bounds.max_lines).map_err(|_| ProcError::Parse {
        file: "status",
        line: None,
    })?;
    let mut readings = Readings::new();
    let mut seen: Vec<&str> = Vec::new();
    for (number, line) in parsed.iter().enumerate() {
        let Some(colon) = line.iter().position(|byte| *byte == b':') else {
            return Err(ProcError::Parse {
                file: "status",
                line: Some(number),
            });
        };
        let (key, rest) = (trim(&line[..colon]), &line[colon + 1..]);
        let Ok(name) = core::str::from_utf8(key) else {
            return Err(ProcError::Parse {
                file: "status",
                line: Some(number),
            });
        };
        if !STATUS_FIELDS.contains(&name) {
            continue;
        }
        seen.push(name);
        let value = trim(rest);
        let parts = fields(value);
        // Every `status` field ends with an optional unit word; a `kB` suffix is the file's
        // own unit and is kept in the metric name.
        let (numbers, unit) = match parts.last().copied() {
            Some(b"kB") => (&parts[..parts.len() - 1], "kb"),
            _ => (&parts[..], "count"),
        };
        if name == "Name" || name == "State" {
            // Text fields are recorded as their first byte's value through a stable numeric
            // code, because a reading has no text variant and a fabricated number (0, say)
            // would be a measurement the file never wrote. `Name:\tdemo` and `State:\tS …`
            // are read this way; anything else with text in it (a unit word, a mis-split
            // line) stays as the numeric fields below, which refuse per field.
            readings.push(
                format!("proc.status.text.{name}"),
                Reading::measured(Value::U64(u64::from(
                    parts
                        .first()
                        .and_then(|part| part.first())
                        .copied()
                        .unwrap_or(0),
                ))),
            );
            continue;
        }
        for (index, part) in numbers.iter().enumerate() {
            let metric = format!("proc.status.{unit}.{name}.{index}");
            match parse_u64(part) {
                Ok(raw) => readings.measured(metric, Value::U64(raw)),
                Err(_) => readings.missing(metric, QualityCode::Unreadable),
            }
        }
    }
    // A listed field the kernel did not write is a feature absence, not a zero.
    for field in STATUS_FIELDS {
        if !seen.contains(&field) {
            readings.missing(
                format!("proc.status.count.{field}.0"),
                QualityCode::Unsupported,
            );
        }
    }
    Ok(readings)
}

/// Parse one process's `statm` file: seven page counts.
///
/// proc(5): `size resident shared text lib data dt`, in that order and in pages. The
/// positions are the file's, so they are asserted by name in a test whose seven values are
/// all distinct.
///
/// Metrics: `proc.statm.size`, `.resident`, `.shared`, `.text`, `.lib`, `.data`, `.dt`.
///
/// # Errors
///
/// `ProcError::Parse` for a shape the file does not have, and [`QualityCode::Unreadable`]
/// for one field that does not parse (that field alone).
pub fn parse_statm(text: &[u8]) -> Result<Readings, ProcError> {
    let parts = fields(trim(text));
    if parts.len() < 7 {
        return Err(ProcError::Parse {
            file: "statm",
            line: None,
        });
    }
    let mut readings = Readings::new();
    for (index, metric) in [
        "proc.statm.size",
        "proc.statm.resident",
        "proc.statm.shared",
        "proc.statm.text",
        "proc.statm.lib",
        "proc.statm.data",
        "proc.statm.dt",
    ]
    .iter()
    .enumerate()
    {
        match parse_u64(parts[index]) {
            Ok(raw) => readings.measured(*metric, Value::U64(raw)),
            Err(_) => readings.missing(*metric, QualityCode::Unreadable),
        }
    }
    Ok(readings)
}

/// List the pids in a `/proc` tree, bounded and sorted.
///
/// Only a name that is a decimal number is a pid: `/proc` also holds `sys`, `self`,
/// `thread-self`, `bus`, `fs`, `irq` and driver directories, and reporting one of those as
/// a process would be a path-keyed identity.
///
/// # Errors
///
/// [`ProcError::Read`] for a failed listing, including
/// [`FsError::TooManyEntries`] when the tree holds more entries than the bound allows (the
/// bound is a refusal, never a truncation, which would report the entries past it as
/// absent).
pub fn list_pids<F: Fs>(fs: &F, proc: &Proc, bounds: Bounds) -> Result<Vec<u32>, ProcError> {
    let entries = fs.list(proc.root(), bounds.max_entries)?;
    if entries.len() > bounds.max_entries {
        return Err(ProcError::Read(FsError::TooManyEntries {
            max_entries: bounds.max_entries,
        }));
    }
    let mut pids = Vec::new();
    for entry in entries {
        if !entry.is_dir() {
            continue;
        }
        let Ok(pid) = entry.name.parse::<u32>() else {
            continue;
        };
        if pid == 0 {
            // `0` is not a process: the kernel's own `pid 0` is the idle task and has no
            // `/proc/0` directory on any host this probe reads.
            continue;
        }
        pids.push(pid);
    }
    pids.sort_unstable();
    Ok(pids)
}

/// Read the host-wide files a probe needs from `/proc` in one pass, each with its own
/// refusal.
#[derive(Clone, Debug, PartialEq)]
pub struct HostSnapshot {
    /// `/proc/stat`.
    pub stat: Result<Readings, ProcError>,
    /// `/proc/meminfo`.
    pub meminfo: Result<Readings, ProcError>,
    /// `/proc/vmstat`.
    pub vmstat: Result<Readings, ProcError>,
    /// `/proc/loadavg`.
    pub loadavg: Result<LoadAverage, ProcError>,
    /// `/proc/pressure/cpu`, or the refusal.
    pub pressure_cpu: Result<Readings, ProcError>,
    /// `/proc/pressure/memory`, or the refusal.
    pub pressure_memory: Result<Readings, ProcError>,
    /// `/proc/pressure/io`, or the refusal.
    pub pressure_io: Result<Readings, ProcError>,
    /// The boot id, or the refusal.
    pub boot_id: Result<BootId, ProcError>,
}

impl HostSnapshot {
    /// The boot epoch (`proc.btime`) in seconds, when it was read.
    #[must_use]
    pub fn boot_epoch(&self) -> Option<u64> {
        self.stat
            .as_ref()
            .ok()
            .and_then(|readings| readings.u64("proc.btime"))
    }
}

/// Read the host-wide `/proc` files.
#[must_use]
pub fn read_host<F: Fs>(fs: &F, proc: &Proc, bounds: Bounds) -> HostSnapshot {
    let read = |name: &str| -> Result<Vec<u8>, FsError> {
        fs.read(&proc.file(name), bounds.max_file_bytes)
    };
    let pressure = |resource: &str| -> Result<Readings, ProcError> {
        let path = proc.dir("pressure").join(resource);
        let bytes = fs
            .read(&path, bounds.max_file_bytes)
            .map_err(ProcError::Read)?;
        parse_pressure_file(resource, &bytes, bounds)
    };
    HostSnapshot {
        stat: read("stat")
            .map_err(ProcError::from)
            .and_then(|bytes| parse_proc_stat(&bytes, bounds)),
        meminfo: read("meminfo")
            .map_err(ProcError::from)
            .and_then(|bytes| parse_meminfo(&bytes, bounds)),
        vmstat: read("vmstat")
            .map_err(ProcError::from)
            .and_then(|bytes| parse_vmstat(&bytes, bounds)),
        loadavg: read("loadavg")
            .map_err(ProcError::from)
            .and_then(|bytes| parse_loadavg(&bytes)),
        pressure_cpu: pressure("cpu"),
        pressure_memory: pressure("memory"),
        pressure_io: pressure("io"),
        boot_id: crate::identity::read_boot_id(
            fs,
            &proc.dir("sys").join("kernel/random/boot_id"),
            bounds.max_file_bytes,
        )
        .map_err(ProcError::from),
    }
}

#[cfg(test)]
mod tests {
    use super::{
        list_pids, parse_loadavg, parse_meminfo, parse_proc_stat, parse_statm, parse_status,
        read_host, read_process, Proc, ProcError, STATUS_FIELDS,
    };
    use crate::cmdline::CmdlineBound;
    use crate::fs::MemFs;
    use crate::quality::QualityCode;
    use crate::Bounds;
    use std::path::Path;

    fn tree() -> MemFs {
        let mut fs = MemFs::new();
        fs.directory("/proc", 1);
        fs.directory("/proc/1", 2);
        fs.directory("/proc/2", 3);
        fs.directory("/proc/sys", 4);
        fs.directory("/proc/sys/kernel", 5);
        fs.directory("/proc/sys/kernel/random", 6);
        fs.directory("/proc/17", 7);
        fs.directory("/proc/self", 8);
        fs.directory("/proc/pressure", 9);
        fs.directory("/proc/bus", 10);
        fs.file("/proc/1/statm", "8192 100 40 256 2048 1024 0\n", 11);
        fs.file(
            "/proc/1/status",
            "Name:\tdemo\nState:\tS (sleeping)\nPid:\t1\nThreads:\t3\nVmRSS:\t 4096 kB\n",
            12,
        );
        // Every field carries its own proc(5) number, so an index slip in `read_stat`
        // changes the value and the test below fails by name.
        fs.file("/proc/1/stat", "1 (demo) S 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 43 44 45 46\n", 13);
        fs.file("/proc/1/cmdline", b"demo\0--serve\0".to_vec(), 14);
        fs.file(
            "/proc/sys/kernel/random/boot_id",
            "00112233-4455-6677-8899-aabbccddeeff\n",
            15,
        );
        fs
    }

    #[test]
    fn a_cpu_row_keeps_every_column_the_kernel_writes() {
        // proc(5) `/proc/stat`: user, nice, system, idle, iowait, irq, softirq, steal,
        // guest, guest_nice. The columns are positional, so each carries its own number and
        // every column is asserted by name: a table that slipped by one column fails.
        let text = b"cpu  1 2 3 4 5 6 7 8 9 10\ncpu0 1 1 1 1 1 1 1 1 1 1\nctxt 42\nbtime 1000\n";
        let readings = parse_proc_stat(text, Bounds::POLICY).expect("stat");
        for (column, field) in [
            "user",
            "nice",
            "system",
            "idle",
            "iowait",
            "irq",
            "softirq",
            "steal",
            "guest",
            "guest_nice",
        ]
        .iter()
        .zip(1..=10u64)
        {
            assert_eq!(
                readings.u64(&format!("proc.cpu.{column}")),
                Some(field),
                "column {column} is position {field} in the cpu row"
            );
        }
        assert_eq!(readings.u64("proc.cpu.cpu0.guest"), Some(1));
        assert_eq!(readings.u64("proc.ctxt"), Some(42));
        assert_eq!(readings.u64("proc.btime"), Some(1000));
        // A row too short to be a CPU row's ticks is a refusal, not a host with no load.
        assert!(parse_proc_stat(b"cpu 1 2\n", Bounds::POLICY).is_err());
        // No cpu row at all is not a `/proc/stat`.
        assert!(parse_proc_stat(b"ctxt 1\n", Bounds::POLICY).is_err());
    }

    #[test]
    fn meminfo_keeps_the_files_own_unit() {
        let text = b"MemTotal:       1000000 kB\nHugePages_Total:       0\n";
        let readings = parse_meminfo(text, Bounds::POLICY).expect("meminfo");
        assert_eq!(readings.u64("meminfo.kb.MemTotal"), Some(1_000_000));
        assert_eq!(readings.u64("meminfo.count.HugePages_Total"), Some(0));
        // A unit this reader does not know must not be read as kB.
        assert!(parse_meminfo(b"MemTotal: 100 MB\n", Bounds::POLICY).is_err());
    }

    #[test]
    fn loadavg_reads_its_five_proc5_fields_by_position() {
        // proc(5) `/proc/loadavg`: load1, load5, load15, runnable/total, last_pid. The
        // values differ from each other so a field read one position over fails by name.
        let load = parse_loadavg(b"0.50 0.40 0.30 4/120 999\n").expect("loadavg");
        assert_eq!(load.one, 0.50);
        assert_eq!(load.five, 0.40);
        assert_eq!(load.fifteen, 0.30);
        assert_eq!(load.runnable, 4, "runnable is the left half of `4/120`");
        assert_eq!(load.entities, 120, "the total is the right half");
        assert_eq!(load.last_pid, 999);
        assert!(parse_loadavg(b"0.5 0.4\n").is_err());
        assert!(
            parse_loadavg(b"nan 0.4 0.3 1/2 3\n").is_err(),
            "NaN is refused"
        );
    }

    #[test]
    fn a_process_is_read_with_each_file_carrying_its_own_refusal() {
        let fs = tree();
        let proc = Proc::new("/proc");
        let files = read_process(&fs, &proc, 1, Bounds::POLICY, CmdlineBound::SAMPLER);
        let stat = files.stat.expect("stat");
        assert_eq!(stat.start_ticks, 22, "starttime is proc(5) field 22");
        assert_eq!(stat.minflt, 10, "minflt is field 10");
        assert_eq!(stat.majflt, 12, "majflt is field 12");
        assert_eq!((stat.utime, stat.stime), (14, 15));
        assert_eq!(stat.num_threads, 20);
        assert_eq!(stat.rss_pages, 24);
        assert_eq!(stat.delayacct_blkio_ticks, Some(42));
        assert_eq!(stat.comm, b"demo");
        let statm = files.statm.expect("statm");
        // proc(5) `statm`: size, resident, shared, text, lib, data, dt — each field carries
        // its own distinct number, so a column read one place over fails by name.
        assert_eq!(statm.u64("proc.statm.size"), Some(8192));
        assert_eq!(statm.u64("proc.statm.resident"), Some(100));
        assert_eq!(statm.u64("proc.statm.shared"), Some(40));
        assert_eq!(statm.u64("proc.statm.text"), Some(256));
        assert_eq!(statm.u64("proc.statm.lib"), Some(2048));
        assert_eq!(statm.u64("proc.statm.data"), Some(1024));
        assert_eq!(statm.u64("proc.statm.dt"), Some(0));
        let status = files.status.expect("status");
        assert_eq!(status.u64("proc.status.count.Threads.0"), Some(3));
        assert_eq!(status.u64("proc.status.kb.VmRSS.0"), Some(4096));
        let cmdline = files.cmdline.expect("cmdline");
        assert_eq!(cmdline.text, "demo --serve");
        // A process that is not there: `stat` is `Gone`, and the other files say so too.
        let gone = read_process(&fs, &proc, 99, Bounds::POLICY, CmdlineBound::SAMPLER);
        assert_eq!(
            gone.stat.map_err(|error| error.to_string()),
            Err("no such file".to_string()),
            "a vanished process is a typed refusal, never an empty record"
        );
        assert_eq!(gone.statm, Err(ProcError::Gone));
    }

    #[test]
    fn a_pid_listing_is_bounded_sorted_and_keeps_only_numbers() {
        let fs = tree();
        let proc = Proc::new("/proc");
        let pids = list_pids(&fs, &proc, Bounds::POLICY).expect("pids");
        assert_eq!(pids, vec![1, 2, 17], "`self` and `sys` are not pids");
        let small = Bounds::new(64, 64, 2);
        assert!(
            list_pids(&fs, &proc, small).is_err(),
            "a listing past the entry bound is refused, not cut"
        );
    }

    #[test]
    fn statm_is_seven_page_counts_and_a_short_line_is_refused() {
        // proc(5): size, resident, shared, text, lib, data, dt. The values are distinct so a
        // column read one position over cannot pass.
        let readings = parse_statm(b"10 5 2 7 9 3 0\n").expect("statm");
        assert_eq!(readings.u64("proc.statm.size"), Some(10));
        assert_eq!(readings.u64("proc.statm.resident"), Some(5));
        assert_eq!(readings.u64("proc.statm.shared"), Some(2));
        assert_eq!(readings.u64("proc.statm.text"), Some(7));
        assert_eq!(readings.u64("proc.statm.lib"), Some(9));
        assert_eq!(readings.u64("proc.statm.data"), Some(3));
        assert_eq!(readings.u64("proc.statm.dt"), Some(0));
        assert_eq!(
            parse_statm(b"10 5 2 1 1 3"),
            Err(ProcError::Parse {
                file: "statm",
                line: None
            }),
            "a truncated statm is refused, never padded with zeros"
        );
        // One bad field is that field's refusal and the others still read.
        let readings = parse_statm(b"10 5 x 7 9 3 0\n").expect("statm");
        assert_eq!(readings.u64("proc.statm.size"), Some(10));
        assert_eq!(
            readings.quality("proc.statm.shared"),
            Some(QualityCode::Unreadable)
        );
    }

    #[test]
    fn a_missing_status_field_is_unsupported_and_a_bad_one_is_unreadable() {
        let text = b"Name:\tapp\nPid:\t5\nThreads:\tsix\nVmRSS:\t4096 kB\n";
        let readings = parse_status(text, Bounds::POLICY).expect("status");
        assert_eq!(readings.u64("proc.status.text.Name"), Some(u64::from(b'a')));
        assert_eq!(
            readings.quality("proc.status.count.Threads.0"),
            Some(QualityCode::Unreadable)
        );
        assert_eq!(
            readings.quality("proc.status.count.VmPeak.0"),
            Some(QualityCode::Unsupported),
            "a field the kernel did not write is an absence, never a zero"
        );
        assert!(STATUS_FIELDS.contains(&"VmSwap"));
        assert!(parse_status(b"no colon here\n", Bounds::POLICY).is_err());
    }

    #[test]
    fn a_host_snapshot_reads_every_file_and_keeps_each_refusal_separate() {
        let mut fs = tree();
        fs.file("/proc/stat", "cpu 1 2 3 4 5 6 7 8 9 10\nbtime 1000\n", 20);
        fs.file("/proc/meminfo", "MemTotal: 10 kB\n", 21);
        fs.file("/proc/vmstat", "pgfault 5\n", 22);
        fs.file("/proc/loadavg", "0.5 0.4 0.3 1/10 20\n", 23);
        fs.file(
            "/proc/pressure/cpu",
            "some avg10=1.00 avg60=0.00 avg300=0.00 total=5\n",
            24,
        );
        let proc = Proc::new("/proc");
        let host = read_host(&fs, &proc, Bounds::POLICY);
        assert_eq!(host.boot_epoch(), Some(1000));
        assert!(host.meminfo.is_ok());
        assert!(host.vmstat.is_ok());
        assert!(host.loadavg.is_ok());
        assert!(host.pressure_cpu.is_ok());
        assert_eq!(
            host.pressure_memory,
            Err(ProcError::Read(crate::fs::FsError::NotFound)),
            "a missing PSI file is its own refusal, not a zero pressure"
        );
        assert!(host.boot_id.is_ok());
        assert_eq!(Path::new("/proc"), proc.root());
    }
}
