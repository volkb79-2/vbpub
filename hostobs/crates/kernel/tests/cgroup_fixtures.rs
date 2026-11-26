//! The cgroupfs readers against the committed fixture tree the sampler proved its
//! collectors on (`crates/kernel/tests/fixtures/cgroupfs`, copied from
//! `topos/tests/fixtures/cgroupfs` in the sampler worktree). Standalone parser files live
//! under `cgroupfs-files/`, outside the recursively walked cgroup tree.
//!
//! The tree is the sampler's own fixture, so these tests assert the *same facts* the
//! sampler asserted on the same bytes, and they add the behaviours plan v4 requires to
//! differ: an unknown topology is a state (never a bottom device), a `max` limit is not a
//! number, and an unparsable field is unreadable rather than zero.

use std::path::{Path, PathBuf};

use hostobs_kernel::cgroup::{
    cgroup_dir, parse_cgroup_events, parse_cpu_max, parse_cpu_stat, parse_io_stat, parse_limit,
    parse_memory_events, parse_memory_stat, parse_pressure, parse_single_u64, read_cgroup,
    DeviceTopology, FixedTopology, KernelError, PressureResource, UnknownTopology, CGROUP_FILES,
};
use hostobs_kernel::fs::{Fs, MemFs, RealFs};
use hostobs_kernel::quality::QualityCode;
use hostobs_kernel::Bounds;

fn fixtures() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/cgroupfs")
}

fn state_fixtures() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/cgroupfs-files/state")
}

fn read(name: &str) -> Vec<u8> {
    let path = fixtures().join(name);
    RealFs::new()
        .read(&path, Bounds::POLICY.max_file_bytes)
        .unwrap_or_else(|error| panic!("{name}: {error}"))
}

fn read_state(name: &str) -> Vec<u8> {
    let path = state_fixtures().join(name);
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
        Some(QualityCode::Unreadable)
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
    assert_eq!(limit.quality, QualityCode::Unsupported);
    assert_eq!(limit.reading().u64(), None);
    assert_eq!(limit.reading().quality(), QualityCode::Unsupported);

    // A numeric limit is a number, and keeps the text the kernel wrote.
    let mut fs = MemFs::new();
    fs.directory("/cg", 1);
    fs.file("/cg/memory.max", "1073741824\n", 2);
    let limit = parse_limit("memory.max", b"1073741824\n").expect("limit");
    assert_eq!(limit.value, Some(1_073_741_824));
    assert_eq!(limit.quality, QualityCode::Ok);
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
fn cpu_max_fixture_quota_period_absence_and_parse_failure_are_distinct() {
    let topology = FixedTopology::with_default(DeviceTopology::Bottom);
    let unlimited = read_cgroup(&RealFs::new(), &fixtures(), Bounds::POLICY, &topology);
    let max = unlimited.readings_of("cpu.max").expect("cpu.max fixture");
    assert_eq!(max.quality("limit.cpu.max.quota"), Some(QualityCode::Unsupported));
    assert_eq!(max.u64("limit.cpu.max.quota"), None, "max is not a measurement");
    assert_eq!(max.u64("limit.cpu.max.period"), Some(100_000));

    let numeric_path = Path::new("system.slice")
        .join(format!("docker-{}.scope", "a".repeat(64)))
        .join("cpu.max");
    let numeric_bytes = read(numeric_path.to_str().expect("fixture path"));
    let numeric = parse_cpu_max(&numeric_bytes).expect("numeric cpu.max fixture");
    assert_eq!(numeric.quota_usec, Some(200_000));
    assert_eq!(numeric.period_usec, 100_000);

    let mut fs = MemFs::new();
    fs.directory("/cg", 1);
    let missing = read_cgroup(&fs, Path::new("/cg"), Bounds::POLICY, &topology);
    assert_eq!(
        missing.failure_of("cpu.max"),
        Some(&KernelError::Absent(QualityCode::Absent))
    );
    assert!(missing.readings_of("cpu.max").is_none());

    let malformed = parse_cpu_max(b"200000 not-a-period\n").expect_err("bad period");
    assert_eq!(malformed.quality(), QualityCode::Unreadable);
    assert!(matches!(
        malformed,
        KernelError::Parse {
            file: "cpu.max",
            line: None
        }
    ));
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
        assert_eq!(entry.reading.quality(), QualityCode::Ok, "{}", entry.metric);
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
fn freezer_state_parsers_read_zero_one_and_frozen_but_empty() {
    let thawed = parse_single_u64("cgroup.freeze", &read_state("cgroup.freeze.zero"))
        .expect("cgroup.freeze zero");
    assert_eq!(thawed.u64(), Some(0));
    let frozen = parse_single_u64("cgroup.freeze", &read_state("cgroup.freeze.one"))
        .expect("cgroup.freeze one");
    assert_eq!(frozen.u64(), Some(1));

    let events = parse_cgroup_events(&read_state("cgroup.events.frozen-empty"), Bounds::POLICY)
        .expect("cgroup.events frozen but empty");
    assert_eq!(events.u64("cgevents.frozen"), Some(1));
    assert_eq!(events.u64("cgevents.populated"), Some(0));
}

#[test]
fn an_unparseable_cgroup_event_only_marks_its_metric_unreadable() {
    let events = parse_cgroup_events(&read_state("cgroup.events.unparseable"), Bounds::POLICY)
        .expect("valid keyed lines remain readable");
    assert_eq!(
        events.quality("cgevents.frozen"),
        Some(QualityCode::Unreadable)
    );
    assert_eq!(events.u64("cgevents.frozen"), None, "no invented zero");
    assert_eq!(events.u64("cgevents.populated"), Some(0));
}

#[test]
fn a_cgroup_event_above_one_is_unreadable_and_an_empty_file_is_refused() {
    let events = parse_cgroup_events(b"populated 2\nfrozen 1\n", Bounds::POLICY)
        .expect("keyed lines remain readable");
    assert_eq!(
        events.quality("cgevents.populated"),
        Some(QualityCode::Unreadable)
    );
    assert_eq!(events.u64("cgevents.populated"), None);
    assert_eq!(events.u64("cgevents.frozen"), Some(1));

    assert!(parse_cgroup_events(b"", Bounds::POLICY).is_err());
}

#[test]
fn a_duplicate_cgroup_event_is_unreadable_without_poisoning_other_keys() {
    let events = parse_cgroup_events(b"populated 0\nfrozen 0\nfrozen 1\n", Bounds::POLICY)
        .expect("keyed lines remain readable");

    assert_eq!(
        events.quality("cgevents.frozen"),
        Some(QualityCode::Unreadable)
    );
    assert_eq!(events.u64("cgevents.frozen"), None, "no chosen duplicate");
    assert_eq!(events.quality("cgevents.populated"), Some(QualityCode::Ok));
    assert_eq!(events.u64("cgevents.populated"), Some(0));
}

#[test]
fn memory_event_duplicates_keep_the_existing_first_entry_behavior() {
    let events = parse_memory_events(
        "memev.",
        "memory.events",
        b"high 2\nhigh 3\n",
        Bounds::POLICY,
    )
    .expect("keyed lines remain readable");

    assert_eq!(events.len(), 2, "the generic parser keeps both entries");
    assert_eq!(events.u64("memev.high"), Some(2), "lookup remains first-wins");
}

#[test]
fn a_missing_freezer_file_is_unsupported_and_not_a_measured_zero() {
    let mut fs = MemFs::new();
    fs.directory("/cg", 1);
    let topology = FixedTopology::with_default(DeviceTopology::Bottom);
    let snapshot = read_cgroup(&fs, Path::new("/cg"), Bounds::POLICY, &topology);

    let failure = snapshot
        .failure_of("cgroup.freeze")
        .expect("missing freezer feature is recorded");
    assert_eq!(failure, &KernelError::Absent(QualityCode::Unsupported));
    assert_eq!(failure.quality(), QualityCode::Unsupported);
    assert!(
        snapshot.readings_of("cgroup.freeze").is_none(),
        "an absent file cannot produce a measured zero"
    );
    assert_eq!(
        snapshot
            .failure_of("cgroup.events")
            .map(KernelError::quality),
        Some(QualityCode::Unsupported),
        "cgroup.events is a kernel feature too"
    );
}

#[test]
fn cgroup_freeze_rejects_values_outside_zero_and_one() {
    let mut fs = MemFs::new();
    fs.directory("/cg", 1);
    fs.file("/cg/cgroup.freeze", "2\n", 2);
    let topology = FixedTopology::with_default(DeviceTopology::Bottom);
    let snapshot = read_cgroup(&fs, Path::new("/cg"), Bounds::POLICY, &topology);

    assert_eq!(
        snapshot
            .failure_of("cgroup.freeze")
            .map(KernelError::quality),
        Some(QualityCode::Unreadable),
        "the kernel's boolean state is only zero or one"
    );
    assert!(snapshot.readings_of("cgroup.freeze").is_none());
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
        QualityCode::UnknownTopology,
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
        Some(QualityCode::Unreadable),
        "one bad field poisons the counters it feeds, not the file"
    );
    assert_eq!(readings.quality("io.wbytes"), Some(QualityCode::Unreadable));
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
        Some(QualityCode::Unsupported)
    );
    assert_eq!(
        readings.quality("io.cost.vrate"),
        Some(QualityCode::Unsupported)
    );
}

#[test]
fn an_overflowing_io_sum_is_unreadable_instead_of_clamped() {
    // A sum that does not fit the type is a corrupt file, not a saturated reading.
    // `saturating_add` would report `u64::MAX` as a measured total: a number the kernel
    // never wrote, which is precisely the invented value this crate refuses. The counters
    // (`checked_add` + `bad`) and the iocost sums use the same rule.
    let mut topology = FixedTopology::with_default(DeviceTopology::Bottom);
    topology.set("8:0", DeviceTopology::Bottom);
    topology.set("8:16", DeviceTopology::Bottom);

    let counters = b"8:0 rbytes=18446744073709551615\n8:16 rbytes=1\n";
    let readings = parse_io_stat(counters, Bounds::POLICY, &topology).expect("io.stat");
    assert_eq!(
        readings.quality("io.rbytes"),
        Some(QualityCode::Unreadable),
        "an overflowing total is refused, not clamped at u64::MAX"
    );
    assert_eq!(readings.u64("io.rbytes"), None);
    // The whole counter set is refused together, which is the module's stated rule: a
    // partial total is a wrong number that looks right, and `io.rbytes`/`io.wbytes` are
    // read as one aggregate. So nobody can read `wbytes` as a total while `rbytes` failed.
    assert_eq!(readings.quality("io.wbytes"), Some(QualityCode::Unreadable));

    let cost = b"8:0 cost.usage=18446744073709551615\n8:16 cost.usage=1 cost.wait=2\n";
    let readings = parse_io_stat(cost, Bounds::POLICY, &topology).expect("io.stat");
    assert_eq!(
        readings.quality("io.cost.usage"),
        Some(QualityCode::Unreadable),
        "the iocost sum refuses an overflow too (checked_add, not saturating_add)"
    );
    assert_eq!(readings.u64("io.cost.usage"), None);
    assert_eq!(
        readings.quality("io.cost.wait"),
        Some(QualityCode::Unreadable),
        "one bad iocost field makes the iocost sums unavailable as a set"
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
        Some(QualityCode::Unsupported)
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
        Some(QualityCode::Unsupported)
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
            Some(QualityCode::Absent),
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
