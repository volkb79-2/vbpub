//! The cgroupfs readers against the committed fixture tree the sampler proved its
//! collectors on (`crates/kernel/tests/fixtures/cgroupfs`, copied from
//! `topos/tests/fixtures/cgroupfs` in the sampler worktree).
//!
//! The tree is the sampler's own fixture, so these tests assert the *same facts* the
//! sampler asserted on the same bytes, and they add the behaviours plan v4 requires to
//! differ: an unknown topology is a state (never a bottom device), a `max` limit is not a
//! number, and an unparsable field is unreadable rather than zero.

use std::path::{Path, PathBuf};

use hostobs_kernel::cgroup::{
    cgroup_dir, parse_cpu_max, parse_cpu_stat, parse_io_stat, parse_limit, parse_memory_events,
    parse_memory_stat, parse_pressure, parse_single_u64, read_cgroup, DeviceTopology,
    FixedTopology, PressureResource, UnknownTopology, CGROUP_FILES,
};
use hostobs_kernel::fs::{Fs, MemFs, RealFs};
use hostobs_kernel::quality::QualityCode;
use hostobs_kernel::Bounds;

fn fixtures() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/cgroupfs")
}

fn read(name: &str) -> Vec<u8> {
    let path = fixtures().join(name);
    RealFs::new()
        .read(&path, Bounds::POLICY.max_file_bytes)
        .unwrap_or_else(|error| panic!("{name}: {error}"))
}

#[test]
fn cpu_stat_reads_the_sampler_fixtures_counters() {
    let readings = parse_cpu_stat(&read("cpu.stat"), Bounds::POLICY).expect("cpu.stat");
    // The committed fixture's numbers, read back field by field.
    assert_eq!(readings.u64("cpu.usage_usec"), Some(10_000_000));
    assert_eq!(readings.u64("cpu.user_usec"), Some(7_000_000));
    assert_eq!(readings.u64("cpu.system_usec"), Some(3_000_000));
    assert_eq!(readings.u64("cpu.nr_periods"), Some(100));
    assert_eq!(readings.u64("cpu.nr_throttled"), Some(0));
    assert_eq!(readings.u64("cpu.throttled_usec"), Some(0));
    // user + system == usage is a property of the fixture, not of the reader.
    assert_eq!(
        readings.u64("cpu.user_usec").expect("user")
            + readings.u64("cpu.system_usec").expect("system"),
        readings.u64("cpu.usage_usec").expect("usage")
    );
}

#[test]
fn a_parse_failure_is_unreadable_and_never_a_zero() {
    // The sampler's collector skipped a line it could not parse, which made the metric
    // silently disappear; a zero appeared where it accumulated one. Here the metric keeps
    // its name and says the value could not be read.
    let text = b"usage_usec 10\nuser_usec not-a-number\nnr_periods 3\n";
    let readings = parse_cpu_stat(text, Bounds::POLICY).expect("cpu.stat");
    assert_eq!(readings.u64("cpu.usage_usec"), Some(10));
    assert_eq!(
        readings.quality("cpu.user_usec"),
        Some(QualityCode::UNREADABLE)
    );
    assert_eq!(readings.u64("cpu.user_usec"), None, "no invented zero");
    assert_eq!(readings.u64("cpu.nr_periods"), Some(3));

    // A file that is not `key value` at all is refused as a file.
    assert!(parse_cpu_stat(b"usage_usec 10\nbroken\n", Bounds::POLICY).is_err());
    assert!(parse_cpu_stat(b"", Bounds::POLICY).is_err());
    // A bound the file exceeds is a refusal, not a truncated reading.
    let bounds = Bounds::new(1024, 2, 16);
    assert!(parse_cpu_stat(&read("cpu.stat"), bounds).is_err());
}

#[test]
fn a_max_limit_is_a_state_and_never_a_number() {
    let limit = parse_limit("memory.max", &read("memory.max")).expect("memory.max");
    assert_eq!(limit.raw, b"max");
    assert_eq!(limit.value, None, "`max` is not -1, not 0 and not u64::MAX");
    assert_eq!(limit.quality, QualityCode::UNSUPPORTED);
    assert_eq!(limit.reading().u64(), None);
    assert_eq!(limit.reading().quality(), QualityCode::UNSUPPORTED);

    // A numeric limit is a number, and keeps the text the kernel wrote.
    let mut fs = MemFs::new();
    fs.directory("/cg", 1);
    fs.file("/cg/memory.max", "1073741824\n", 2);
    let limit = parse_limit("memory.max", b"1073741824\n").expect("limit");
    assert_eq!(limit.value, Some(1_073_741_824));
    assert_eq!(limit.quality, QualityCode::OK);
    assert_eq!(limit.reading().u64(), Some(1_073_741_824));

    // `cpu.max` is two values and its quota may be `max`.
    let cpu_max = parse_cpu_max(&read("cpu.max")).expect("cpu.max");
    assert_eq!(cpu_max.quota_usec, None, "the fixture has no quota");
    assert_eq!(cpu_max.period_usec, 100_000);
    let cpu_max = parse_cpu_max(b"50000 100000\n").expect("cpu.max");
    assert_eq!(cpu_max.quota_usec, Some(50_000));
    assert_eq!(cpu_max.period_usec, 100_000);
    // One field is not `cpu.max`.
    assert!(
        parse_cpu_max(b"max\n").is_err(),
        "a shape the file does not have is refused"
    );
}

#[test]
fn memory_stat_and_events_keep_every_key_the_files_write() {
    let stat = parse_memory_stat(&read("memory.stat"), Bounds::POLICY).expect("memory.stat");
    assert!(!stat.is_empty());
    // The fixture's own keys come back under their wire names with their own numbers.
    assert_eq!(stat.u64("mem.stat.anon"), Some(1_000_000_000));
    assert_eq!(stat.u64("mem.stat.file"), Some(2_000_000_000));
    assert_eq!(stat.u64("mem.stat.zswapped"), Some(400_000_000));
    assert_eq!(stat.len(), 11, "every one of the fixture's eleven keys");
    for entry in stat.entries() {
        assert!(entry.metric.starts_with("mem.stat."), "{}", entry.metric);
        assert_eq!(entry.reading.quality(), QualityCode::OK, "{}", entry.metric);
    }
    let events = parse_memory_events(
        "memev.",
        "memory.events",
        &read("memory.events"),
        Bounds::POLICY,
    )
    .expect("memory.events");
    assert_eq!(events.u64("memev.high"), Some(1));
    assert_eq!(
        events.u64("memev.oom"),
        Some(0),
        "zero events are measurements"
    );
    assert_eq!(events.u64("memev.oom_kill"), Some(0));
    // `.local` and the hierarchical file share keys but not prefixes: the prefix is the
    // caller's, so an OOM attributed to the deepest cgroup is not confused with a
    // hierarchical count.
    let local = parse_memory_events(
        "memevl.",
        "memory.events.local",
        &read("memory.events"),
        Bounds::POLICY,
    )
    .expect("events.local");
    assert_eq!(local.len(), events.len());
    for entry in local.entries() {
        assert!(entry.metric.starts_with("memevl."), "{}", entry.metric);
    }
}

#[test]
fn an_io_aggregate_over_bottom_devices_is_a_sum_and_an_unknown_device_is_a_state() {
    let text = read("io.stat");
    // A device whose topology is known to be bottom: its counters are the aggregate.
    let mut topology = FixedTopology::with_default(DeviceTopology::Bottom);
    topology.set("8:0", DeviceTopology::Bottom);
    let readings = parse_io_stat(&text, Bounds::POLICY, &topology).expect("io.stat");
    assert_eq!(readings.u64("io.rbytes"), Some(1_000_000));
    assert_eq!(readings.u64("io.wbytes"), Some(2_000_000));
    assert_eq!(readings.u64("io.rios"), Some(10));
    assert_eq!(readings.u64("io.wios"), Some(20));
    assert_eq!(readings.u64("io.dbytes"), Some(0), "zero is a measurement");
    assert_eq!(readings.u64("io.dios"), Some(0));

    // A stacked device is already counted below it, so it contributes nothing.
    let mut stacked = FixedTopology::with_default(DeviceTopology::Bottom);
    stacked.set("8:0", DeviceTopology::Stacked);
    let readings = parse_io_stat(&text, Bounds::POLICY, &stacked).expect("io.stat");
    assert_eq!(readings.u64("io.rbytes"), Some(0));

    // THE M1 FIX: an unknown topology produces no aggregate at all. The sampler's
    // `last.get(dev).copied().unwrap_or(true)` counted an unknown device as bottom, which
    // double-counted a stacked device's I/O into the cgroup total.
    assert_eq!(
        parse_io_stat(&text, Bounds::POLICY, &UnknownTopology),
        Err(hostobs_kernel::cgroup::KernelError::UnknownTopology)
    );
    assert_eq!(
        hostobs_kernel::cgroup::KernelError::UnknownTopology.quality(),
        QualityCode::UNKNOWN_TOPOLOGY,
        "the state is a code, not a zero"
    );

    // A stacked and a bottom device together: the bottom one's counters are the total.
    let text = b"8:0 rbytes=10 wbytes=20 rios=1 wios=2 dbytes=0 dios=0\n254:0 rbytes=1000 wbytes=2000 rios=5 wios=6 dbytes=0 dios=0\n";
    let mut mixed = FixedTopology::with_default(DeviceTopology::Bottom);
    mixed.set("8:0", DeviceTopology::Bottom);
    mixed.set("254:0", DeviceTopology::Stacked);
    let readings = parse_io_stat(text, Bounds::POLICY, &mixed).expect("io.stat");
    assert_eq!(readings.u64("io.rbytes"), Some(10));

    // One unknown device among bottom ones is still unknown: the total cannot be trusted.
    let mut one_unknown = FixedTopology::with_default(DeviceTopology::Bottom);
    one_unknown.set("254:0", DeviceTopology::Unknown);
    assert_eq!(
        parse_io_stat(text, Bounds::POLICY, &one_unknown),
        Err(hostobs_kernel::cgroup::KernelError::UnknownTopology),
        "a partial total is a wrong number that looks right"
    );
}

#[test]
fn an_unparsable_io_counter_makes_the_sum_unavailable_and_a_cost_gauge_is_separate() {
    let text = b"8:0 rbytes=10 wbytes=oops rios=1 wios=2 dbytes=0 dios=0 cost.usage=5 cost.wait=6 cost.indelay=7 cost.vrate=1000.5\n";
    let mut topology = FixedTopology::with_default(DeviceTopology::Bottom);
    topology.set("8:0", DeviceTopology::Bottom);
    let readings = parse_io_stat(text, Bounds::POLICY, &topology).expect("io.stat");
    assert_eq!(
        readings.quality("io.rbytes"),
        Some(QualityCode::UNREADABLE),
        "one bad field poisons the counters it feeds, not the file"
    );
    assert_eq!(readings.quality("io.wbytes"), Some(QualityCode::UNREADABLE));
    assert_eq!(readings.u64("io.rbytes"), None, "no partial total");
    // iocost lives on its own metrics and still reads.
    assert_eq!(readings.u64("io.cost.usage"), Some(5));
    assert_eq!(readings.u64("io.cost.wait"), Some(6));
    assert_eq!(readings.u64("io.cost.indelay"), Some(7));
    assert_eq!(readings.f64("io.cost.vrate"), Some(1000.5));

    // A cgroup with no iocost device reports the feature as unsupported, not as zero.
    let plain = b"8:0 rbytes=10\n";
    let readings = parse_io_stat(plain, Bounds::POLICY, &topology).expect("io.stat");
    assert_eq!(readings.u64("io.rbytes"), Some(10));
    assert_eq!(
        readings.quality("io.cost.usage"),
        Some(QualityCode::UNSUPPORTED)
    );
    assert_eq!(
        readings.quality("io.cost.vrate"),
        Some(QualityCode::UNSUPPORTED)
    );
}

#[test]
fn pressure_files_carry_gauges_and_counters_and_a_missing_full_row() {
    let readings = parse_pressure(PressureResource::Cpu, &read("cpu.pressure"), Bounds::POLICY)
        .expect("cpu.pressure");
    assert_eq!(readings.f64("psi.cpu.some.avg10"), Some(1.0));
    assert_eq!(readings.f64("psi.cpu.some.avg60"), Some(0.5));
    assert_eq!(readings.f64("psi.cpu.some.avg300"), Some(0.2));
    assert_eq!(
        readings.u64("psi.cpu.some.total"),
        Some(5000),
        "`total` is µs"
    );
    assert_eq!(
        readings.f64("psi.cpu.full.avg10"),
        Some(0.0),
        "zero is a value"
    );

    // A resource the kernel writes no `full` row for reports that absence as a code.
    let some_only = b"some avg10=1.00 avg60=0.00 avg300=0.00 total=5\n";
    let readings =
        parse_pressure(PressureResource::Io, some_only, Bounds::POLICY).expect("pressure");
    assert_eq!(readings.u64("psi.io.some.total"), Some(5));
    assert_eq!(
        readings.quality("psi.io.full.total"),
        Some(QualityCode::UNSUPPORTED)
    );
    // A row that is not `some` or `full` is refused.
    assert!(parse_pressure(
        PressureResource::Memory,
        b"partial avg10=1.0\n",
        Bounds::POLICY
    )
    .is_err());
    assert!(parse_pressure(PressureResource::Memory, b"", Bounds::POLICY).is_err());
}

#[test]
fn a_whole_cgroup_directory_is_read_file_by_file_in_one_committed_order() {
    let fs = RealFs::new();
    let topology = FixedTopology::with_default(DeviceTopology::Bottom);
    let first = read_cgroup(&fs, &fixtures(), Bounds::POLICY, &topology);
    let second = read_cgroup(&fs, &fixtures(), Bounds::POLICY, &topology);
    assert_eq!(first, second, "two passes over one tree agree exactly");

    // The order is `CGROUP_FILES`'s, not the directory listing's.
    let names: Vec<&str> = first
        .readings
        .iter()
        .map(|(name, _)| name.as_str())
        .collect();
    let mut expected: Vec<&str> = Vec::new();
    for (file, _) in CGROUP_FILES {
        if first.readings_of(file).is_some() {
            expected.push(file);
        }
    }
    assert_eq!(names, expected);

    // The fixture's numbers arrive under their wire names.
    let cpu = first.readings_of("cpu.stat").expect("cpu.stat");
    assert_eq!(cpu.u64("cpu.usage_usec"), Some(10_000_000));
    let max = first.readings_of("memory.max").expect("memory.max");
    assert_eq!(
        max.quality("limit.memory.max"),
        Some(QualityCode::UNSUPPORTED)
    );

    // A file the fixture does not have is a *failure with its reason*, not a silence:
    // which files a cgroup directory has depends on the controllers that are on.
    let mut fs = MemFs::new();
    fs.directory("/cg", 7);
    fs.file("/cg/cpu.stat", "usage_usec 5\n", 8);
    let partial = read_cgroup(&fs, Path::new("/cg"), Bounds::POLICY, &topology);
    assert_eq!(
        partial
            .readings_of("cpu.stat")
            .and_then(|r| r.u64("cpu.usage_usec")),
        Some(5)
    );
    for file in ["io.stat", "memory.stat", "pids.max"] {
        assert_eq!(
            partial.failure_of(file).map(|error| error.quality()),
            Some(QualityCode::ABSENT),
            "{file}: a controller that is off is an absence"
        );
    }
}

#[test]
fn a_single_number_file_refuses_its_shape() {
    assert_eq!(
        parse_single_u64("memory.current", &read("memory.current"))
            .expect("memory.current")
            .u64(),
        Some(4_096_000_000)
    );
    assert!(parse_single_u64("memory.current", b"").is_err());
    assert!(parse_single_u64("memory.current", b"12 34\n").is_err());
    assert!(
        parse_single_u64("memory.current", b"-1").is_err(),
        "a negative number is corrupt here, not a sentinel"
    );
}

#[test]
fn a_cgroup_path_is_assembled_without_ever_becoming_an_identity() {
    let root = Path::new("/sys/fs/cgroup");
    assert_eq!(cgroup_dir(root, ""), root, "the root itself");
    assert_eq!(
        cgroup_dir(root, "/system.slice/demo.service"),
        root.join("system.slice").join("demo.service")
    );
    // The same path twice is the same path, and that is all a path says: an identity comes
    // from `(device, inode)` (see `identity.rs` and the SP-2 tests).
    assert_eq!(
        cgroup_dir(root, "system.slice/demo.service"),
        cgroup_dir(root, "/system.slice//demo.service")
    );
}
