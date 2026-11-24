//! The probe's producer: bounded cgroup walking, identity, clocked ticks and publication.

use std::collections::{BTreeMap, BTreeSet};
use std::io;
use std::os::fd::OwnedFd;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use hostobs_kernel::cgroup::{
    self, Absence, CgroupSnapshot, DeviceTopology, FixedTopology, KernelError, Readings,
};
use hostobs_kernel::clock::{Clock, SystemClock, Timeline};
use hostobs_kernel::fs::{Fs, RealFs};
use hostobs_kernel::identity::cgroup_identity;
use hostobs_kernel::procfs::{self, HostSnapshot, Proc, Roots};
use hostobs_kernel::Bounds;
use hostobs_model::identity::{
    CgroupKey, IdentityError as ModelIdentityError, IncarnationRegistry, DEFAULT_ACTIVE_MAX,
    MAX_LABEL_LEN,
};
use hostobs_model::metric::{sampler_baseline, Metric, MetricRegistry};
use hostobs_model::quality::Quality;
use hostobs_model::value::{Sample, Value};
use hostobs_proto::dict::{Entry, MetricFacts, MetricValueType};
use hostobs_proto::frame::{BlockHeaderFacts, Column, Row};

use crate::backend::TickData;

const MAX_SLEEP_NS: u64 = 100_000_000;

const CGROUP_METRICS: [&str; 31] = [
    "cpu.usage_usec",
    "cpu.user_usec",
    "cpu.system_usec",
    "mem.current",
    "mem.anon",
    "mem.file",
    "mem.swap_current",
    "pids.current",
    "io.rbytes",
    "io.wbytes",
    "io.rios",
    "io.wios",
    "io.dbytes",
    "io.dios",
    "io.cost.usage",
    "io.cost.wait",
    "io.cost.indelay",
    "io.cost.vrate",
    "memev.oom_kill",
    "psi.cpu.some.avg10",
    "psi.cpu.full.avg10",
    "psi.cpu.some.total",
    "psi.cpu.full.total",
    "psi.memory.some.avg10",
    "psi.memory.full.avg10",
    "psi.memory.some.total",
    "psi.memory.full.total",
    "psi.io.some.avg10",
    "psi.io.full.avg10",
    "psi.io.some.total",
    "psi.io.full.total",
];

const HOST_METRICS: [&str; 7] = [
    "host.ctxt",
    "host.forks",
    "host.procs_running",
    "host.procs_blocked",
    "host.load1",
    "host.load5",
    "host.load15",
];

const DAEMON_METRICS: [&str; 2] = ["probe.cgroups_skipped", "probe.ticks_skipped"];

/// The fixed facts of one sampler process.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SamplerConfig {
    /// The boot id the process is sampling.
    pub boot_id: [u8; 16],
    /// The process epoch, the boot clock at process start.
    pub epoch: u64,
    /// The nominal period.
    pub tick_ms: u32,
    /// The cgroup tree to walk.
    pub cgroup_root: PathBuf,
    /// The largest number of rows a query may ask for.
    pub max_rows: u32,
}

/// One immutable, published sampler snapshot.
#[derive(Debug)]
pub struct Tick {
    data: TickData,
    scan_ms: u32,
    metric_columns: BTreeMap<String, MetricSlot>,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct MetricSlot {
    pub(crate) entry: u32,
    pub(crate) column: usize,
}

impl Tick {
    /// Build a tick, indexing the metric dictionary entries clients may request.
    #[must_use]
    pub fn new(data: TickData, scan_ms: u32) -> Tick {
        let mut metric_columns = BTreeMap::new();
        for (column, value) in data.columns.iter().enumerate() {
            if let Some(Entry::Metric { name, .. }) = data.entries.get(value.metric as usize) {
                metric_columns.insert(
                    name.clone(),
                    MetricSlot {
                        entry: value.metric,
                        column,
                    },
                );
            }
        }
        Tick {
            data,
            scan_ms,
            metric_columns,
        }
    }

    /// The immutable block data.
    #[must_use]
    pub fn data(&self) -> &TickData {
        &self.data
    }

    /// The latest scan's duration.
    #[must_use]
    pub const fn scan_ms(&self) -> u32 {
        self.scan_ms
    }

    /// The tick sequence; zero is the state before the first tick.
    #[must_use]
    pub fn seq(&self) -> u64 {
        self.data.header.seq
    }

    pub(crate) fn metric_column(&self, name: &str) -> Option<(MetricSlot, &Column)> {
        let slot = self.metric_columns.get(name).copied()?;
        Some((slot, self.data.columns.get(slot.column)?))
    }
}

/// The sampler's single-slot publication.
#[derive(Debug)]
pub struct Published {
    current: Mutex<Arc<Tick>>,
    started_mono_ns: u64,
    started_real_ns: i64,
}

impl Published {
    /// The seq-0 state before the first tick.
    #[must_use]
    pub fn new(started_mono_ns: u64, started_real_ns: i64) -> Published {
        Published {
            current: Mutex::new(Arc::new(Tick::new(TickData::empty(), 0))),
            started_mono_ns,
            started_real_ns,
        }
    }

    /// Replace the published tick; the old `Arc` is dropped after the lock is released.
    pub fn store(&self, tick: Arc<Tick>) {
        let old = {
            let mut guard = lock(&self.current);
            std::mem::replace(&mut *guard, tick)
        };
        drop(old);
    }

    /// Clone the current `Arc` under the lock.
    #[must_use]
    pub fn load(&self) -> Arc<Tick> {
        let guard = lock(&self.current);
        Arc::clone(&guard)
    }

    /// The monotonic process start.
    #[must_use]
    pub const fn started_mono_ns(&self) -> u64 {
        self.started_mono_ns
    }

    /// The wall-clock process start.
    #[must_use]
    pub const fn started_real_ns(&self) -> i64 {
        self.started_real_ns
    }
}

fn lock<T>(value: &Mutex<T>) -> MutexGuard<'_, T> {
    match value.lock() {
        Ok(guard) => guard,
        Err(error) => error.into_inner(),
    }
}

/// A clocked sampler over an injected filesystem and clock.
#[derive(Debug)]
pub struct Sampler<F, C> {
    fs: F,
    clock: C,
    proc_root: Proc,
    cgroup_root: PathBuf,
    cfg: SamplerConfig,
    registry: IncarnationRegistry,
    metrics: Vec<MetricSpec>,
    positions: BTreeMap<&'static str, usize>,
    seq: u64,
    ticks_skipped: u64,
    next_deadline_ns: u64,
}

#[derive(Clone, Copy, Debug)]
struct MetricSpec {
    name: &'static str,
    facts: MetricFacts,
    value_type: MetricValueType,
}

impl<F: Fs, C: Clock> Sampler<F, C> {
    /// Build a sampler from the filesystem, roots, clock, config and registry bound.
    #[must_use]
    pub fn new(
        fs: F,
        roots: Roots,
        clock: C,
        cfg: SamplerConfig,
        registry_max: usize,
    ) -> Sampler<F, C> {
        let registry = sampler_baseline().expect("the static metric baseline is valid");
        let metrics = supported_metrics(&registry);
        let positions = metrics
            .iter()
            .enumerate()
            .map(|(index, metric)| (metric.name, index))
            .collect();
        let next_deadline_ns = clock.mono_ns();
        Sampler {
            fs,
            clock,
            proc_root: Proc::new(roots.proc),
            cgroup_root: roots.cgroup,
            cfg,
            registry: IncarnationRegistry::with_max(registry_max),
            metrics,
            positions,
            seq: 0,
            ticks_skipped: 0,
            next_deadline_ns,
        }
    }

    /// Run one complete scan and build the next immutable tick.
    pub fn tick(&mut self) -> Tick {
        let now_ns = self.clock.mono_ns();
        let tick_ns = u64::from(self.cfg.tick_ms) * 1_000_000;
        let skipped = advance_deadline(&mut self.next_deadline_ns, now_ns, tick_ns);
        self.ticks_skipped = self.ticks_skipped.saturating_add(skipped);
        self.seq = self.seq.saturating_add(1);
        let mono_start_ns = now_ns;

        let (rows, cgroups_skipped) = self.walk();
        let host = procfs::read_host(&self.fs, &self.proc_root, Bounds::POLICY);
        let topology = FixedTopology::with_default(DeviceTopology::Unknown);
        let mut all_samples = Vec::with_capacity(rows.len());
        for row in &rows {
            let snapshot = cgroup::read_cgroup(&self.fs, &row.path, Bounds::POLICY, &topology);
            let mut samples = self.absent_samples();
            fill_cgroup(
                &self.fs,
                &self.positions,
                &mut samples,
                &row.path,
                &snapshot,
                row.label == "/",
            );
            if row.label == "/" {
                fill_host(&self.positions, &mut samples, &host);
                set(
                    &self.positions,
                    &mut samples,
                    "probe.cgroups_skipped",
                    measured(Value::U64(cgroups_skipped)),
                );
                set(
                    &self.positions,
                    &mut samples,
                    "probe.ticks_skipped",
                    measured(Value::U64(self.ticks_skipped)),
                );
            }
            all_samples.push(samples);
        }

        let mono_end_ns = self.clock.mono_ns();
        let scan_ms = u32::try_from(mono_end_ns.saturating_sub(mono_start_ns) / 1_000_000)
            .unwrap_or(u32::MAX);
        let boot_ns = self.clock.boot_ns();
        let real_ns = self.clock.wall_ns();
        let times = ScanTimes {
            mono_start_ns,
            mono_end_ns,
            boot_ns,
            real_ns,
        };
        let data = build_tick_data(
            &self.cfg,
            self.seq,
            times,
            &rows,
            &self.metrics,
            &all_samples,
        );
        Tick::new(data, scan_ms)
    }

    fn absent_samples(&self) -> Vec<Sample> {
        self.metrics
            .iter()
            .map(|_| missing(Quality::Absent))
            .collect()
    }

    fn walk(&mut self) -> (Vec<WalkedRow>, u64) {
        self.registry.begin_round();
        let cap = usize::try_from(self.cfg.max_rows)
            .unwrap_or(usize::MAX)
            .saturating_add(1);
        let mut stack = vec![(self.cgroup_root.clone(), "/".to_string())];
        let mut rows = Vec::new();
        let mut skipped = 0u64;
        let mut seen = BTreeSet::new();

        while let Some((path, label)) = stack.pop() {
            let publishable = rows.len() < cap && label.len() <= MAX_LABEL_LEN;
            let identity = if publishable {
                cgroup_identity(&self.fs, &path).ok().and_then(|identity| {
                    CgroupKey::new(identity.device(), identity.inode())
                        .ok()
                        .map(|key| (identity, key))
                })
            } else {
                None
            };
            if publishable {
                match identity {
                    Some((_, key)) => match self.registry.observe(key, Some(&label)) {
                        Ok(inc) if seen.insert(inc) => rows.push(WalkedRow {
                            path: path.clone(),
                            label: label.clone(),
                            inc: inc.raw(),
                        }),
                        Ok(_) => skipped = skipped.saturating_add(1),
                        Err(ModelIdentityError::RegistryFull { .. }) => {
                            skipped = skipped.saturating_add(1);
                        }
                        Err(_) => skipped = skipped.saturating_add(1),
                    },
                    None => skipped = skipped.saturating_add(1),
                }
            } else {
                skipped = skipped.saturating_add(1);
            }

            let children = match self.fs.list(&path, Bounds::POLICY.max_entries) {
                Ok(children) => children,
                Err(_) => {
                    skipped = skipped.saturating_add(1);
                    continue;
                }
            };
            let mut directories: Vec<_> = children
                .into_iter()
                .filter(|entry| entry.is_dir())
                .collect();
            directories.sort_by(|left, right| left.name.as_bytes().cmp(right.name.as_bytes()));
            for entry in directories.into_iter().rev() {
                let mut child_label = String::from(&label);
                if child_label != "/" {
                    child_label.push('/');
                }
                child_label.push_str(&entry.name);
                stack.push((path.join(entry.name), child_label));
            }
        }
        self.registry.end_round();
        (rows, skipped)
    }

    fn sleep_until_deadline(&self, stop: &AtomicBool) {
        while !stop.load(Ordering::Acquire) {
            let now = self.clock.mono_ns();
            if now >= self.next_deadline_ns {
                return;
            }
            let wait = self.next_deadline_ns.saturating_sub(now).min(MAX_SLEEP_NS);
            thread::sleep(Duration::from_nanos(wait));
        }
    }
}

#[derive(Debug)]
struct WalkedRow {
    path: PathBuf,
    label: String,
    inc: u64,
}

impl TickData {
    fn empty() -> TickData {
        TickData {
            header: BlockHeaderFacts {
                boot_id: [0; 16],
                epoch: 0,
                seq: 0,
                mono_start_ns: 0,
                mono_end_ns: 0,
                boot_ns: 0,
                real_ns: 0,
                dict_gen: 0,
                n_rows: 0,
                dropped: 0,
                flags: 0,
            },
            entries: Vec::new(),
            rows: Vec::new(),
            columns: Vec::new(),
        }
    }
}

fn supported_metrics(registry: &MetricRegistry) -> Vec<MetricSpec> {
    let names = CGROUP_METRICS
        .into_iter()
        .chain(HOST_METRICS)
        .chain(DAEMON_METRICS);
    names
        .map(|name| {
            let metric = registry.get(name).expect("supported metric is registered");
            MetricSpec {
                name,
                facts: facts(metric),
                value_type: value_type(name),
            }
        })
        .collect()
}

fn facts(metric: Metric) -> MetricFacts {
    MetricFacts {
        unit: metric.unit(),
        kind: metric.kind(),
        scope: metric.scope(),
        source: metric.source(),
    }
}

fn value_type(name: &str) -> MetricValueType {
    if matches!(
        name,
        "io.cost.vrate" | "host.load1" | "host.load5" | "host.load15"
    ) || name.starts_with("psi.") && name.ends_with(".avg10")
    {
        MetricValueType::F64
    } else {
        MetricValueType::U64
    }
}

fn missing(quality: Quality) -> Sample {
    Sample::from_raw(Value::U64(0), quality)
}

fn measured(value: Value) -> Sample {
    Sample::measured(value)
}

fn set(
    positions: &BTreeMap<&'static str, usize>,
    samples: &mut [Sample],
    name: &str,
    sample: Sample,
) {
    if let Some(index) = positions.get(name).copied() {
        samples[index] = sample;
    }
}

fn fill_file(
    positions: &BTreeMap<&'static str, usize>,
    samples: &mut [Sample],
    snapshot: &CgroupSnapshot,
    file: &str,
    mappings: &[(&str, &'static str)],
) {
    if let Some(readings) = snapshot.readings_of(file) {
        for (reader, published) in mappings {
            if let Some(&sample) = readings.get(reader) {
                set(positions, samples, published, sample);
            }
        }
    } else {
        let quality = snapshot
            .failure_of(file)
            .map_or(Quality::Absent, KernelError::quality);
        fill_group(positions, samples, mappings, quality);
    }
}

fn fill_group(
    positions: &BTreeMap<&'static str, usize>,
    samples: &mut [Sample],
    mappings: &[(&str, &'static str)],
    quality: Quality,
) {
    for (_, published) in mappings {
        set(positions, samples, published, missing(quality));
    }
}

fn fill_cgroup<F: Fs>(
    fs: &F,
    positions: &BTreeMap<&'static str, usize>,
    samples: &mut [Sample],
    dir: &Path,
    snapshot: &CgroupSnapshot,
    root: bool,
) {
    fill_file(
        positions,
        samples,
        snapshot,
        "cpu.stat",
        &[
            ("cpu.usage_usec", "cpu.usage_usec"),
            ("cpu.user_usec", "cpu.user_usec"),
            ("cpu.system_usec", "cpu.system_usec"),
        ],
    );
    fill_file(
        positions,
        samples,
        snapshot,
        "memory.current",
        &[("mem.current", "mem.current")],
    );
    fill_file(
        positions,
        samples,
        snapshot,
        "memory.stat",
        &[("mem.stat.anon", "mem.anon"), ("mem.stat.file", "mem.file")],
    );
    fill_file(
        positions,
        samples,
        snapshot,
        "memory.swap.current",
        &[("mem.swap_current", "mem.swap_current")],
    );
    fill_file(
        positions,
        samples,
        snapshot,
        "pids.current",
        &[("pids.current", "pids.current")],
    );
    fill_file(
        positions,
        samples,
        snapshot,
        "memory.events",
        &[("memev.oom_kill", "memev.oom_kill")],
    );
    fill_file(
        positions,
        samples,
        snapshot,
        "io.stat",
        &[
            ("io.rbytes", "io.rbytes"),
            ("io.wbytes", "io.wbytes"),
            ("io.rios", "io.rios"),
            ("io.wios", "io.wios"),
            ("io.dbytes", "io.dbytes"),
            ("io.dios", "io.dios"),
            ("io.cost.usage", "io.cost.usage"),
            ("io.cost.wait", "io.cost.wait"),
            ("io.cost.indelay", "io.cost.indelay"),
            ("io.cost.vrate", "io.cost.vrate"),
        ],
    );
    if root {
        return;
    }
    for (resource, file) in [
        (cgroup::PressureResource::Cpu, "cpu.pressure"),
        (cgroup::PressureResource::Memory, "memory.pressure"),
        (cgroup::PressureResource::Io, "io.pressure"),
    ] {
        let result = cgroup::read_file(fs, dir, file, Bounds::POLICY, Absence::Feature)
            .and_then(|bytes| cgroup::parse_pressure(resource, &bytes, Bounds::POLICY));
        fill_pressure(positions, samples, result, resource.name());
    }
}

fn fill_pressure(
    positions: &BTreeMap<&'static str, usize>,
    samples: &mut [Sample],
    result: Result<Readings, KernelError>,
    resource: &str,
) {
    let names = pressure_names(resource);
    match result {
        Ok(readings) => {
            for name in names {
                let sample = readings
                    .get(name)
                    .copied()
                    .unwrap_or_else(|| missing(Quality::Unsupported));
                set(positions, samples, name, sample);
            }
        }
        Err(error) => {
            for name in names {
                set(positions, samples, name, missing(error.quality()));
            }
        }
    }
}

fn pressure_names(resource: &str) -> [&'static str; 4] {
    match resource {
        "cpu" => [
            "psi.cpu.some.avg10",
            "psi.cpu.full.avg10",
            "psi.cpu.some.total",
            "psi.cpu.full.total",
        ],
        "memory" => [
            "psi.memory.some.avg10",
            "psi.memory.full.avg10",
            "psi.memory.some.total",
            "psi.memory.full.total",
        ],
        _ => [
            "psi.io.some.avg10",
            "psi.io.full.avg10",
            "psi.io.some.total",
            "psi.io.full.total",
        ],
    }
}

fn fill_host(
    positions: &BTreeMap<&'static str, usize>,
    samples: &mut [Sample],
    host: &HostSnapshot,
) {
    let stat = [
        ("proc.ctxt", "host.ctxt"),
        ("proc.processes", "host.forks"),
        ("proc.procs_running", "host.procs_running"),
        ("proc.procs_blocked", "host.procs_blocked"),
    ];
    match &host.stat {
        Ok(readings) => {
            for (reader, published) in stat {
                let sample = readings
                    .get(reader)
                    .copied()
                    .unwrap_or_else(|| missing(Quality::Unsupported));
                set(positions, samples, published, sample);
            }
        }
        Err(error) => {
            for (_, published) in stat {
                set(positions, samples, published, missing(error.quality()));
            }
        }
    }
    match &host.loadavg {
        Ok(load) => {
            set(
                positions,
                samples,
                "host.load1",
                measured(Value::F64(load.one)),
            );
            set(
                positions,
                samples,
                "host.load5",
                measured(Value::F64(load.five)),
            );
            set(
                positions,
                samples,
                "host.load15",
                measured(Value::F64(load.fifteen)),
            );
        }
        Err(error) => {
            for published in ["host.load1", "host.load5", "host.load15"] {
                set(positions, samples, published, missing(error.quality()));
            }
        }
    }
    for (resource, result) in [
        ("cpu", &host.pressure_cpu),
        ("memory", &host.pressure_memory),
        ("io", &host.pressure_io),
    ] {
        match result {
            Ok(readings) => {
                for name in pressure_names(resource) {
                    let sample = readings
                        .get(name)
                        .copied()
                        .unwrap_or_else(|| missing(Quality::Unsupported));
                    set(positions, samples, name, sample);
                }
            }
            Err(error) => {
                for name in pressure_names(resource) {
                    set(positions, samples, name, missing(error.quality()));
                }
            }
        }
    }
}

#[derive(Clone, Copy, Debug)]
struct ScanTimes {
    mono_start_ns: u64,
    mono_end_ns: u64,
    boot_ns: u64,
    real_ns: i64,
}

fn build_tick_data(
    cfg: &SamplerConfig,
    seq: u64,
    times: ScanTimes,
    rows: &[WalkedRow],
    metrics: &[MetricSpec],
    samples: &[Vec<Sample>],
) -> TickData {
    let mut entries = Vec::with_capacity(rows.len().saturating_add(metrics.len()));
    for row in rows {
        entries.push(Entry::Entity {
            inc: row.inc,
            label: row.label.clone(),
        });
    }
    let metric_base = u32::try_from(entries.len()).unwrap_or(u32::MAX);
    for metric in metrics {
        entries.push(Entry::Metric {
            facts: metric.facts,
            name: metric.name.to_string(),
        });
    }
    let block_rows: Vec<Row> = (0..rows.len())
        .map(|index| Row {
            ent: u32::try_from(index).unwrap_or(u32::MAX),
            t_off_us: 0,
        })
        .collect();
    let mut columns = Vec::with_capacity(metrics.len());
    for (metric_index, metric) in metrics.iter().enumerate() {
        let mut values = Vec::with_capacity(rows.len());
        let mut quality = Vec::with_capacity(rows.len());
        for row_samples in samples {
            let sample = row_samples
                .get(metric_index)
                .copied()
                .unwrap_or_else(|| missing(Quality::Absent));
            let (value, code) = encode(sample, metric.value_type);
            values.push(value);
            quality.push(code.code());
        }
        columns.push(Column {
            metric: metric_base.saturating_add(u32::try_from(metric_index).unwrap_or(u32::MAX)),
            value_type: metric.value_type,
            kind: metric.facts.kind,
            values,
            quality,
        });
    }
    TickData {
        header: BlockHeaderFacts {
            boot_id: cfg.boot_id,
            epoch: cfg.epoch,
            seq,
            mono_start_ns: times.mono_start_ns,
            mono_end_ns: times.mono_end_ns,
            boot_ns: times.boot_ns,
            real_ns: times.real_ns,
            dict_gen: 0,
            n_rows: u32::try_from(rows.len()).unwrap_or(u32::MAX),
            dropped: 0,
            flags: 0,
        },
        entries,
        rows: block_rows,
        columns,
    }
}

fn encode(sample: Sample, expected: MetricValueType) -> (u64, Quality) {
    let quality = sample.quality();
    if !quality.is_measurement() {
        return (0, quality);
    }
    match (sample.value(), expected) {
        (Some(Value::U64(value)), MetricValueType::U64) => (value, quality),
        (Some(Value::I64(value)), MetricValueType::I64) => {
            (u64::from_le_bytes(value.to_le_bytes()), quality)
        }
        (Some(Value::F64(value)), MetricValueType::F64) => (value.to_bits(), quality),
        _ => (0, Quality::Unreadable),
    }
}

fn advance_deadline(next: &mut u64, now_ns: u64, tick_ns: u64) -> u64 {
    if tick_ns == 0 {
        return 0;
    }
    let after = next.saturating_add(tick_ns);
    if after >= now_ns {
        *next = after;
        return 0;
    }
    let late = now_ns.saturating_sub(after);
    let skipped = late.saturating_add(tick_ns - 1) / tick_ns;
    *next = after.saturating_add(skipped.saturating_mul(tick_ns));
    skipped
}

fn signal(wake: &OwnedFd) {
    let one = 1u64.to_le_bytes();
    let _ = rustix::io::write(wake, &one);
}

fn publish(published: &Published, wake: &OwnedFd, tick: Arc<Tick>) {
    published.store(tick);
    signal(wake);
}

struct StopGuard {
    stop: Arc<AtomicBool>,
    wake: Arc<OwnedFd>,
}

impl Drop for StopGuard {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        signal(&self.wake);
    }
}

/// Start the named sampler thread.
///
/// # Errors
///
/// [`io::ErrorKind::InvalidInput`] for `tick_ms == 0`; otherwise the thread spawn error.
pub fn spawn(
    cfg: SamplerConfig,
    published: Arc<Published>,
    wake: OwnedFd,
    stop: Arc<AtomicBool>,
) -> io::Result<JoinHandle<()>> {
    if cfg.tick_ms == 0 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "sampler tick_ms must be non-zero",
        ));
    }
    let roots = Roots {
        proc: PathBuf::from("/proc"),
        sys: PathBuf::from("/sys"),
        cgroup: cfg.cgroup_root.clone(),
    };
    let clock = SystemClock::new(Timeline::new(cfg.boot_id, cfg.epoch));
    let mut sampler = Sampler::new(RealFs::new(), roots, clock, cfg, DEFAULT_ACTIVE_MAX);
    let wake = Arc::new(wake);
    thread::Builder::new()
        .name("hostobs-sampler".to_string())
        .spawn(move || {
            let _guard = StopGuard {
                stop: Arc::clone(&stop),
                wake: Arc::clone(&wake),
            };
            loop {
                if stop.load(Ordering::Acquire) {
                    break;
                }
                let tick = Arc::new(sampler.tick());
                publish(&published, &wake, tick);
                if stop.load(Ordering::Acquire) {
                    break;
                }
                sampler.sleep_until_deadline(&stop);
            }
        })
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Arc;

    use hostobs_kernel::fs::MemFs;
    use hostobs_model::identity::Incarnation;
    use hostobs_model::quality::Quality;

    use super::{
        advance_deadline, publish, signal, spawn, Published, Sampler, SamplerConfig, StopGuard,
        Tick, Timeline,
    };
    use crate::backend::TickData as Data;
    use hostobs_kernel::clock::{Clock, ManualClock};
    use hostobs_kernel::procfs::Roots;

    const WALL: i64 = 1_700_000_000_000_000_000;

    fn timeline() -> Timeline {
        Timeline::new([7; 16], 11)
    }

    fn config(root: &str, max_rows: u32) -> SamplerConfig {
        SamplerConfig {
            boot_id: [7; 16],
            epoch: 11,
            tick_ms: 100,
            cgroup_root: root.into(),
            max_rows,
        }
    }

    fn sampler(
        fs: MemFs,
        root: &str,
        max_rows: u32,
        registry_max: usize,
    ) -> Sampler<MemFs, ManualClock> {
        let cfg = config(root, max_rows);
        Sampler::new(
            fs,
            Roots {
                proc: "/proc".into(),
                sys: "/sys".into(),
                cgroup: cfg.cgroup_root.clone(),
            },
            ManualClock::new(timeline(), 100, WALL),
            cfg,
            registry_max,
        )
    }

    fn filled() -> MemFs {
        let mut fs = MemFs::new();
        fs.directory("/cg", 1);
        fs.file(
            "/cg/cpu.stat",
            "usage_usec 10\nuser_usec 7\nsystem_usec 3\n",
            2,
        );
        fs.file("/cg/memory.current", "4096\n", 3);
        fs.file("/cg/memory.stat", "anon 1000\nfile 2000\n", 4);
        fs.file("/cg/io.stat", "8:0 rbytes=5 wbytes=6 rios=7 wios=8\n", 5);
        fs.directory("/cg/a.slice", 6);
        fs.file("/cg/a.slice/cpu.stat", "usage_usec 20\n", 7);
        fs.directory("/cg/b.slice", 8);
        fs.directory("/cg/broken.slice", 9);
        fs.file("/cg/broken.slice/memory.current", "not-a-number\n", 10);
        fs
    }

    fn value(tick: &Tick, name: &str, row: usize) -> (u64, u8) {
        let (_, column) = tick.metric_column(name).expect("registered column");
        (
            *column.values.get(row).expect("row value"),
            *column.quality.get(row).expect("row quality"),
        )
    }

    fn labels(tick: &Tick) -> Vec<&str> {
        tick.data()
            .rows
            .iter()
            .filter_map(|row| {
                tick.data()
                    .entries
                    .get(row.ent as usize)
                    .and_then(|entry| match entry {
                        hostobs_proto::dict::Entry::Entity { label, .. } => Some(label.as_str()),
                        _ => None,
                    })
            })
            .collect()
    }

    fn incs(tick: &Tick) -> Vec<u64> {
        tick.data()
            .rows
            .iter()
            .filter_map(|row| {
                tick.data()
                    .entries
                    .get(row.ent as usize)
                    .and_then(|entry| match entry {
                        hostobs_proto::dict::Entry::Entity { inc, .. } => Some(*inc),
                        _ => None,
                    })
            })
            .collect()
    }

    fn skipped(sampler: &mut Sampler<MemFs, ManualClock>) -> u64 {
        let tick = sampler.tick();
        let (_, column) = tick
            .metric_column("probe.cgroups_skipped")
            .expect("skip counter");
        column.values[0]
    }

    #[test]
    fn fixture_rows_carry_hand_derived_values_and_absence_qualities() {
        let mut sampler = sampler(filled(), "/cg", 16, 64);
        let tick = sampler.tick();
        assert_eq!(
            labels(&tick),
            ["/", "/a.slice", "/b.slice", "/broken.slice",]
        );
        assert_eq!(value(&tick, "cpu.usage_usec", 0), (10, Quality::Ok.code()));
        assert_eq!(value(&tick, "mem.current", 0), (4096, Quality::Ok.code()));
        assert_eq!(value(&tick, "mem.anon", 0), (1000, Quality::Ok.code()));
        assert_eq!(value(&tick, "cpu.usage_usec", 1), (20, Quality::Ok.code()));
        assert_eq!(
            value(&tick, "io.rbytes", 0),
            (0, Quality::UnknownTopology.code()),
            "unknown topology is a quality state, never a measured aggregate"
        );
        assert_eq!(
            value(&tick, "mem.current", 3),
            (0, Quality::Unreadable.code()),
            "the broken fixture is unreadable, not a measured zero"
        );
        for daemon in ["probe.cgroups_skipped", "probe.ticks_skipped"] {
            assert_eq!(value(&tick, daemon, 0), (0, Quality::Ok.code()));
            assert_eq!(
                value(&tick, daemon, 1),
                (0, Quality::Absent.code()),
                "{daemon} is root-row only"
            );
        }
    }

    #[test]
    fn identity_survives_one_absent_round_and_retires_after_two_then_recycles() {
        let mut fs = MemFs::new();
        fs.directory("/cg", 1);
        fs.directory("/cg/a.slice", 2);
        let mut sampler = sampler(fs, "/cg", 16, 16);
        let first = sampler.tick();
        let child = incs(&first)[1];
        let second = sampler.tick();
        assert_eq!(
            incs(&second)[1],
            child,
            "a continuously present cgroup keeps one incarnation"
        );

        sampler.fs = {
            let mut gone = MemFs::new();
            gone.directory("/cg", 1);
            gone
        };
        sampler.tick();
        assert!(sampler.registry.is_active(Incarnation::from_raw(child)));
        sampler.tick();
        assert!(!sampler.registry.is_active(Incarnation::from_raw(child)));

        let mut recycled = MemFs::new();
        recycled.directory("/cg", 1);
        recycled.directory("/cg/a.slice", 3);
        sampler.fs = recycled;
        let next = sampler.tick();
        assert_ne!(incs(&next)[1], child, "a recycled inode mints a new inc");
    }

    #[test]
    fn every_walk_bound_increments_the_visible_skip_gauge() {
        let mut cap = MemFs::new();
        cap.directory("/cg", 1);
        for inode in 2..=4 {
            cap.directory(format!("/cg/{inode}.slice"), inode);
        }
        assert_eq!(skipped(&mut sampler(cap, "/cg", 1, 16)), 2);

        let mut cut = MemFs::new();
        cut.directory("/cg", 1);
        for inode in 0..=u64::try_from(hostobs_kernel::Bounds::POLICY.max_entries)
            .expect("policy bound fits u64")
        {
            cut.directory(format!("/cg/{inode:04}.slice"), inode + 2);
        }
        assert_eq!(skipped(&mut sampler(cut, "/cg", 512, 1024)), 1);

        let mut long = MemFs::new();
        long.directory("/cg", 1);
        long.directory(format!("/cg/{}", "a".repeat(130)), 2);
        long.directory(format!("/cg/{}/{}", "a".repeat(130), "b".repeat(130)), 3);
        assert_eq!(skipped(&mut sampler(long, "/cg", 16, 16)), 1);

        let mut full = MemFs::new();
        full.directory("/cg", 1);
        full.directory("/cg/a.slice", 2);
        assert_eq!(skipped(&mut sampler(full, "/cg", 16, 1)), 1);
    }

    #[test]
    fn deadline_schedule_skips_to_the_first_deadline_at_or_after_now() {
        let mut clock = ManualClock::new(timeline(), 10, WALL);
        let mut next = clock.mono_ns();
        assert_eq!(advance_deadline(&mut next, clock.mono_ns(), 5), 0);
        clock.advance(11);
        assert_eq!(advance_deadline(&mut next, clock.mono_ns(), 5), 1);
        assert_eq!(next, 25);
        clock.advance(29);
        assert_eq!(advance_deadline(&mut next, clock.mono_ns(), 5), 4);
        assert_eq!(next, 50);
    }

    #[test]
    fn tick_headers_count_sequence_and_clock_facts() {
        let mut sampler = sampler(filled(), "/cg", 16, 64);
        let first = sampler.tick().data().header.clone();
        assert_eq!((first.seq, first.boot_id, first.epoch), (1, [7; 16], 11));
        assert_eq!(
            (first.mono_start_ns, first.mono_end_ns, first.boot_ns),
            (100, 100, 100)
        );
        assert_eq!(first.real_ns, WALL);

        sampler.clock.advance(250_000_000);
        let second = sampler.tick();
        let header = &second.data().header;
        assert_eq!(
            (
                header.seq,
                header.mono_start_ns,
                header.mono_end_ns,
                header.boot_ns,
                header.real_ns
            ),
            (2, 250_000_100, 250_000_100, 250_000_100, WALL + 250_000_000)
        );
        assert_eq!(value(&second, "probe.ticks_skipped", 0).0, 1);
    }

    #[test]
    fn published_ticks_are_loadable_and_eventfd_counts_coalesce() {
        let wake = rustix::event::eventfd(
            0,
            rustix::event::EventfdFlags::CLOEXEC | rustix::event::EventfdFlags::NONBLOCK,
        )
        .expect("eventfd");
        let published = Published::new(1, WALL);
        publish(
            &published,
            &wake,
            Arc::new(Tick::new(Data::example(1, &[1], &["m"]), 3)),
        );
        assert_eq!(published.load().seq(), 1);
        assert!(read_counter(&wake) >= 1);
        publish(
            &published,
            &wake,
            Arc::new(Tick::new(Data::example(2, &[1], &["m"]), 3)),
        );
        publish(
            &published,
            &wake,
            Arc::new(Tick::new(Data::example(3, &[1], &["m"]), 3)),
        );
        assert_eq!(read_counter(&wake), 2);
        assert_eq!(published.load().seq(), 3);
    }

    fn read_counter(fd: &std::os::fd::OwnedFd) -> u64 {
        let mut bytes = [0u8; 8];
        rustix::io::read(fd, &mut bytes).expect("eventfd read");
        u64::from_le_bytes(bytes)
    }

    #[test]
    fn a_panicking_owner_leaves_stop_set_and_the_wake_readable() {
        let wake = rustix::event::eventfd(
            0,
            rustix::event::EventfdFlags::CLOEXEC | rustix::event::EventfdFlags::NONBLOCK,
        )
        .expect("eventfd");
        let read = wake.try_clone().expect("dup");
        let stop = Arc::new(AtomicBool::new(false));
        let guard_stop = Arc::clone(&stop);
        let guard_wake = Arc::new(wake);
        let joined = std::thread::spawn(move || {
            let _guard = StopGuard {
                stop: guard_stop,
                wake: guard_wake,
            };
            panic!("sampler panic");
        })
        .join();
        assert!(joined.is_err());
        assert!(stop.load(Ordering::Acquire));
        assert!(read_counter(&read) >= 1);
    }

    #[test]
    fn spawn_smoke_uses_the_fixture_tree_and_honours_stop() {
        let fixture = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../crates/kernel/tests/fixtures");
        let mut cfg = config(
            fixture
                .join("cgroupfs")
                .to_str()
                .expect("utf-8 fixture path"),
            16,
        );
        cfg.tick_ms = 100;
        let wake = rustix::event::eventfd(
            0,
            rustix::event::EventfdFlags::CLOEXEC | rustix::event::EventfdFlags::NONBLOCK,
        )
        .expect("eventfd");
        let read = wake.try_clone().expect("dup");
        let stop = Arc::new(AtomicBool::new(false));
        let published = Arc::new(Published::new(0, WALL));
        let joined = spawn(
            cfg,
            Arc::clone(&published),
            wake.try_clone().expect("dup"),
            Arc::clone(&stop),
        )
        .expect("spawn sampler");
        let timeout = rustix::time::Timespec {
            tv_sec: 30,
            tv_nsec: 0,
        };
        let mut polls = [rustix::event::PollFd::new(
            &read,
            rustix::event::PollFlags::IN,
        )];
        assert!(rustix::event::poll(&mut polls, Some(&timeout)).expect("poll") > 0);
        assert!(published.load().seq() >= 1);
        stop.store(true, Ordering::Release);
        signal(&read);
        joined.join().expect("sampler thread");
    }
}
