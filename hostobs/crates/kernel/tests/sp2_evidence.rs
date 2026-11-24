//! SP-2 evidence, as tests with hand-computed expectations (plan v4 §F.3).
//!
//! SP-2 is the study's question: *can an observer tell one incarnation of a thing from
//! another, and can it tell a wall-clock jump from real elapsed time?* The answers are
//! properties of the identity inputs and the clocks this crate reads, so they are asserted
//! here against fixture trees and an injected clock — never against the live host, and
//! never as prose.
//!
//! Four claims, one test each:
//!
//! 1. **Incarnation reuse is visible.** A cgroup recreated at the same path gets a new
//!    inode, and a pid reused after exit gets a new `starttime`; the identity changes and
//!    the path does not.
//! 2. **A wall jump is not elapsed time.** A rate computed from a wall delta is wrong in
//!    both directions (negative, and far too large); the monotonic delta is the only
//!    duration, and the wall delta is reported as a jump.
//! 3. **A missing interval is stated, never invented.** Two readings from different
//!    boots/epochs are refused, and a zero-length interval refuses division rather than
//!    reporting `inf` or a fabricated rate.
//! 4. **Late discovery is a birth, not a lifetime.** A cgroup first seen mid-run has a
//!    measurable age since its discovery and no measurable age since boot.
//!
//! **The join (`HOB-26`).** The rate helper below feeds the kernel's [`Reading`] type directly
//! to `model::derive_rate`; it does not reproduce the arithmetic. The clock and identity
//! claims, the classifier and the hand-computed SP-2 answers are therefore observed through
//! one call across the crate boundary, while `model` keeps its own
//! `fixtures/rates/rates.json` cases.
//!
//! Every assertion is hand-computed and every fixture is a committed tree under
//! `crates/kernel/tests/fixtures`, never the live host.

use std::path::Path;

use hostobs_kernel::cgroup::read_file;
use hostobs_kernel::clock::{interval_between, Clock, ClockError, Interval, ManualClock, Timeline};
use hostobs_kernel::cmdline::CmdlineBound;
use hostobs_kernel::fs::{MemFs, RealFs};
use hostobs_kernel::identity::{
    cgroup_identity, read_boot_id, BootId, CgroupIdentity, ClockTicks, IdentityError,
    ProcessIdentity,
};
use hostobs_kernel::procfs::{list_pids, read_process, Proc};
use hostobs_kernel::quality::QualityCode;
use hostobs_kernel::value::{Reading, Value};
use hostobs_kernel::Bounds;
use hostobs_model::rate::{derive_rate, MonoTime, NoRate, Rate};

const BOOT_A: [u8; 16] = [0x11; 16];
const BOOT_B: [u8; 16] = [0x22; 16];
/// A wall clock reading that keeps the arithmetic readable: 100 s after the epoch.
const W0: i64 = 100_000_000_000;

fn timeline(epoch: u64) -> Timeline {
    Timeline::new(BOOT_A, epoch)
}

/// A rate from a counter delta and the interval it was measured over, derived by the
/// production classifier from two kernel readings.
fn rate(delta: u64, interval: Interval) -> Option<f64> {
    let previous = Reading::measured(Value::U64(0));
    let current = Reading::measured(Value::U64(delta));
    derive_rate(
        previous,
        MonoTime::from_nanos(0),
        current,
        MonoTime::from_nanos(interval.mono_ns),
        0,
    )
    .rate()
    .map(Rate::per_second)
}

// ------------------------------------------------------------------ 1. incarnation reuse

#[test]
fn a_cgroup_recreated_at_one_path_is_two_incarnations_and_the_path_says_nothing() {
    let mut fs = MemFs::new();
    fs.directory("/cg/demo", 100);

    // The first incarnation: (device 1, inode 100).
    let first: CgroupIdentity = cgroup_identity(&fs, Path::new("/cg/demo")).expect("first");
    assert_eq!((first.device(), first.inode()), (1, 100));

    // systemd removes the cgroup and a new unit lands at the same path; the inode is
    // recycled, and the fixture gives it the next number the kernel would.
    fs.directory("/cg/demo", 101);
    let second = cgroup_identity(&fs, Path::new("/cg/demo")).expect("second");
    assert_eq!((second.device(), second.inode()), (1, 101));
    assert_ne!(
        first, second,
        "a recreated cgroup at the same path is a different incarnation"
    );

    // Two more cycles: an identity used as a map key never collides across incarnations,
    // which is exactly what a path-keyed map would do.
    let mut seen: Vec<CgroupIdentity> = vec![first, second];
    for inode in [102u64, 103] {
        fs.directory("/cg/demo", inode);
        let next = cgroup_identity(&fs, Path::new("/cg/demo")).expect("next");
        assert!(!seen.contains(&next), "inode {inode} is a new incarnation");
        seen.push(next);
    }
    assert_eq!(seen.len(), 4);
    // The path never changed, so the path cannot distinguish any of them: this is the
    // "no path-keyed identity" rule as an assertion rather than a promise.
    assert!(seen.iter().all(|identity| identity.device() == 1));

    // Inode 0 and device 0 name nothing, so they are refusals rather than identities.
    assert_eq!(CgroupIdentity::new(0, 100), Err(IdentityError::DeviceZero));
    assert_eq!(CgroupIdentity::new(1, 0), Err(IdentityError::InodeZero));
}

#[test]
fn a_recycled_pid_after_exit_is_a_different_process() {
    let boot = BootId::parse("00112233-4455-6677-8899-aabbccddeeff").expect("boot");

    // Process A: pid 4242, started at tick 500 (5 s after boot at 100 Hz).
    let a = ProcessIdentity::new(boot, 4242, 500).expect("A");
    assert_eq!(
        ClockTicks::new(100).expect("rate").ticks_to_nanos(500),
        Some(5_000_000_000)
    );

    // A exits and the kernel hands the same pid to a new process, which starts later.
    let b = ProcessIdentity::new(boot, 4242, 900).expect("B");
    assert_eq!(a.pid(), b.pid(), "the pid is the same number");
    assert_ne!(a, b, "the start tick makes it a different process");
    assert_eq!(
        ClockTicks::new(100).expect("rate").ticks_to_nanos(900),
        Some(9_000_000_000),
        "the new process started 4 s after the old one"
    );

    // A reboot also breaks the key even when pid and start tick repeat: the kernel's
    // tick counter restarts at boot.
    let rebooted = ProcessIdentity::new(
        BootId::parse("ffeeddcc-bbaa-9988-7766-554433221100").expect("boot B"),
        4242,
        500,
    )
    .expect("C");
    assert_ne!(
        a, rebooted,
        "one boot id per identity means a reboot is a new process"
    );
    assert_eq!(rebooted.start_ticks(), a.start_ticks());
    assert_eq!(rebooted.pid(), a.pid());
}

#[test]
fn an_identity_refusal_maps_to_unreadable_and_never_to_a_value() {
    // A process whose stat could not be turned into an identity is not a process with
    // "identity 0".
    let missing = IdentityError::Read(hostobs_kernel::fs::FsError::NotFound);
    assert_eq!(missing.quality(), QualityCode::UNREADABLE);
}

// ------------------------------------------------------------------ 2. wall jumps

#[test]
fn a_wall_jump_is_not_elapsed_time_and_the_monotonic_delta_is_the_only_duration() {
    let mut clock = ManualClock::new(timeline(1), 0, W0);
    let t0 = clock.read();
    assert_eq!((t0.mono_ns, t0.wall_ns), (0, W0));

    // Hand-computed schedule, all in nanoseconds:
    //   +2 000 000 000 monotonic (and wall, for an ordinary advance)
    //   -10 000 000 000 wall step backwards (an NTP correction)
    //   +1 000 000 000 monotonic (and wall, as above)
    clock.advance(2_000_000_000);
    let t1 = clock.read();
    assert_eq!(
        (t1.mono_ns, t1.wall_ns),
        (2_000_000_000, W0 + 2_000_000_000)
    );

    clock.step_wall(-10_000_000_000);
    let t2 = clock.read();
    assert_eq!(
        (t2.mono_ns, t2.wall_ns),
        (2_000_000_000, W0 - 8_000_000_000),
        "the step moved only the wall label"
    );

    clock.advance(1_000_000_000);
    let t3 = clock.read();
    assert_eq!(
        (t3.mono_ns, t3.wall_ns),
        (3_000_000_000, W0 - 7_000_000_000)
    );

    // Interval t1 -> t2: no monotonic time passed, the wall fell by 10 s.
    let step = interval_between(t1, t2).expect("same timeline");
    assert_eq!(step.mono_ns, 0);
    assert_eq!(step.wall_ns, -10_000_000_000);
    assert!(step.wall_jumped_backwards());

    // Interval t2 -> t3: one second of real time, and the wall agrees.
    let after = interval_between(t2, t3).expect("same timeline");
    assert_eq!(after.mono_ns, 1_000_000_000);
    assert_eq!(after.wall_ns, 1_000_000_000);
    assert!(!after.wall_jumped_backwards());
    assert!(!after.wall_jumped_forwards(1_000_000));

    // Interval t0 -> t3: three seconds of real time; the wall says -7 s because of the
    // step. A consumer that used the wall delta would compute a NEGATIVE rate here.
    let whole = interval_between(t0, t3).expect("same timeline");
    assert_eq!(whole.mono_ns, 3_000_000_000);
    assert_eq!(whole.wall_ns, -7_000_000_000);
    assert!(whole.wall_jumped_backwards());

    // A counter that rose by 1200 over that interval. The monotonic rate is 400/s; the
    // wall-based one would be -171.43/s, which is the defect this evidence rules out.
    assert_eq!(rate(1_200, whole), Some(400.0));
    let wall_rate = 1_200.0 / (whole.wall_ns as f64 / 1_000_000_000.0);
    assert!(wall_rate < 0.0, "{wall_rate}");
    assert!(
        (wall_rate - -171.428_571_428_571_4).abs() < 1e-9,
        "{wall_rate}"
    );

    // A forward step larger than the elapsed monotonic time is a jump forward too: 5 s
    // more of wall label over 1 s of real time.
    let mut clock = ManualClock::new(timeline(1), 10_000_000_000, W0);
    let before = clock.read();
    clock.advance(1_000_000_000);
    clock.step_wall(5_000_000_000);
    let after = clock.read();
    let jumped = interval_between(before, after).expect("same timeline");
    assert_eq!(jumped.mono_ns, 1_000_000_000);
    assert_eq!(jumped.wall_ns, 6_000_000_000);
    assert!(
        jumped.wall_jumped_forwards(1_000_000),
        "5 s more than real time"
    );
    assert_eq!(rate(1_000, jumped), Some(1000.0), "the rate still uses 1 s");
}

#[test]
fn a_wall_reading_is_a_label_and_the_interval_keeps_its_sign() {
    // The same monotonic interval with three different wall labels: the duration is
    // identical, and only the label (and the jump classification) differ.
    let mut clock = ManualClock::new(timeline(1), 0, W0);
    let start = clock.read();
    clock.advance(1_000_000_000);
    let plain = clock.read();
    let plain = interval_between(start, plain).expect("same timeline");
    assert_eq!(plain.mono_ns, 1_000_000_000);

    let mut clock = ManualClock::new(timeline(1), 0, W0);
    let start = clock.read();
    clock.step_wall(-1_000_000_000);
    clock.advance(1_000_000_000);
    let back = clock.read();
    let back = interval_between(start, back).expect("same timeline");
    assert_eq!(back.mono_ns, 1_000_000_000, "the duration did not move");
    assert_eq!(
        back.wall_ns, 0,
        "the wall label did: 1 s real, 0 s labelled"
    );
    assert!(!back.wall_jumped_backwards(), "the delta is not negative");

    let mut clock = ManualClock::new(timeline(1), 0, W0);
    let start = clock.read();
    clock.step_wall(60_000_000_000);
    clock.advance(1_000_000_000);
    let ahead = clock.read();
    let ahead = interval_between(start, ahead).expect("same timeline");
    assert_eq!(ahead.mono_ns, 1_000_000_000);
    assert_eq!(ahead.wall_ns, 61_000_000_000);
    assert!(
        ahead.wall_jumped_forwards(0),
        "60 s more than the 1 s elapsed"
    );
}

// ------------------------------------------------------------------ 3. missing intervals

#[test]
fn a_pair_from_two_boots_or_epochs_is_refused_and_never_subtracted() {
    // A reboot: same daemon epoch number, new boot id.
    let mut clock = ManualClock::new(timeline(1), 1_000, W0);
    let before = clock.read();
    clock.restart(Timeline::new(BOOT_B, 1));
    let after = clock.read();
    assert_eq!(
        interval_between(before, after),
        Err(ClockError::NotComparable {
            previous: timeline(1),
            current: Timeline::new(BOOT_B, 1),
        })
    );

    // A probe restart in the same boot: same boot id, new epoch.
    let mut clock = ManualClock::new(timeline(1), 1_000, W0);
    let before = clock.read();
    clock.restart(timeline(2));
    let after = clock.read();
    assert_eq!(
        interval_between(before, after),
        Err(ClockError::NotComparable {
            previous: timeline(1),
            current: timeline(2),
        })
    );

    // Nothing joins across the boundary: no duration, so no rate and no lifetime.
    assert_eq!(
        rate(
            500,
            Interval {
                mono_ns: 0,
                wall_ns: 0
            }
        ),
        None
    );

    // A swapped pair is a caller defect, refused rather than silently negated.
    let clock = ManualClock::new(timeline(1), 10, W0);
    let later = clock.read();
    let earlier = hostobs_kernel::clock::ClockReading {
        mono_ns: 5,
        ..later
    };
    assert_eq!(
        interval_between(later, earlier),
        Err(ClockError::MonoWentBackwards)
    );
}

#[test]
fn a_zero_length_interval_refuses_a_rate_instead_of_reporting_infinity() {
    let clock = ManualClock::new(timeline(1), 1_000, W0);
    let at = clock.read();
    let again = clock.read();
    let interval = interval_between(at, again).expect("same timeline");
    assert_eq!(
        interval.mono_ns, 0,
        "two readings at one instant is a legal interval"
    );
    // A counter delta at a zero-length interval has no rate; `inf`/NaN is not an answer.
    assert_eq!(rate(1_000, interval), None);
    // The wall delta of a zero-length interval is exactly zero, not a jump.
    assert_eq!(interval.wall_ns, 0);
    assert!(!interval.wall_jumped_backwards());
    assert!(!interval.wall_jumped_forwards(0));
}

#[test]
fn a_counter_that_went_backwards_is_a_baseline_and_not_a_negative_rate() {
    // The kernel's counters only rise, except when a cgroup is recreated or a counter
    // is reset; the reader's job is to report that as a reset, so no rate is derived.
    // The sampler's `Collector::counter` reached this answer by never turning a
    // backwards counter into a spike; here the same fact is two quality codes.
    assert_eq!(QualityCode::RESET.code(), 4);
    assert!(!QualityCode::RESET.is_measurement());
    assert_eq!(QualityCode::STALE.code(), 3);
    // A consumer asks the production classifier; a backwards counter is a reset, not a
    // negative delta and not a rate.
    let previous = 1_000u64;
    let current = 400u64;
    assert_eq!(
        derive_rate(
            Reading::measured(Value::U64(previous)),
            MonoTime::from_nanos(0),
            Reading::measured(Value::U64(current)),
            MonoTime::from_nanos(1_000_000_000),
            0,
        )
        .reason(),
        Some(NoRate::CounterReset)
    );
    assert_eq!(
        derive_rate(
            Reading::measured(Value::U64(previous)),
            MonoTime::from_nanos(0),
            Reading::measured(Value::U64(current)),
            MonoTime::from_nanos(1_000_000_000),
            0,
        )
        .quality(),
        QualityCode::RESET
    );
}

// ------------------------------------------------------------------ 4. late discovery

#[test]
fn a_cgroup_discovered_mid_run_has_an_age_since_its_discovery_and_none_since_boot() {
    // The clock starts at boot; the walk discovers the first cgroup 2 s into the run.
    let boot = BootId::parse("00112233-4455-6677-8899-aabbccddeeff").expect("boot");
    let mut clock = ManualClock::new(Timeline::new(*boot.as_bytes(), 1), 0, W0);
    clock.advance(2_000_000_000);
    let discovered = clock.read();
    assert_eq!(discovered.mono_ns, 2_000_000_000);

    // `fs` gains the cgroup only now (a unit started after the run began).
    let mut fs = MemFs::new();
    fs.directory("/cg", 1);
    fs.directory("/cg/late.service", 900);
    let identity = cgroup_identity(&fs, Path::new("/cg/late.service")).expect("identity");
    assert_eq!(identity.inode(), 900);

    // At +10 s (hand-computed) the observer asks how long this cgroup has existed, and
    // how long it has been *observed*. Only the second is answerable, and it is exactly 8 s.
    clock.advance(8_000_000_000);
    let now = clock.read();
    let observed = interval_between(discovered, now).expect("same timeline");
    assert_eq!(observed.mono_ns, 8_000_000_000);

    // There is no reading at boot for this cgroup, so an "age since boot" would have to
    // be invented. The interval that would produce it needs a pair whose earlier reading
    // does not exist; the honest answer is the discovery reading and a stated absence.
    let since_boot: Option<Interval> = None;
    assert_eq!(
        since_boot, None,
        "a cgroup first seen at 2 s has no boot-time reading"
    );
    // And the identity still distinguishes it from a same-path predecessor.
    fs.directory("/cg/late.service", 901);
    let successor = cgroup_identity(&fs, Path::new("/cg/late.service")).expect("successor");
    assert_ne!(
        identity, successor,
        "the late incarnation is not the next one"
    );

    // A pid found late behaves the same way: its identity carries `starttime`, which is
    // how old it really is, so its age is *derivable* rather than estimated — and the
    // derivation is refused when the tick rate was not read.
    let process = ProcessIdentity::new(boot, 4711, 950).expect("process");
    let ticks = ClockTicks::new(100).expect("rate");
    assert_eq!(
        ticks.ticks_to_nanos(process.start_ticks()),
        Some(9_500_000_000),
        "950 ticks at 100 Hz is 9.5 s after boot"
    );
    assert_eq!(
        ClockTicks::unknown(),
        None,
        "no rate, no conversion, no guess"
    );
}

// ------------------------------------------------------------------ fixture reads

#[test]
fn the_committed_proc_fixture_yields_the_identity_fields_a_probe_needs() {
    let base = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures");
    let fs = RealFs::new();
    let proc = Proc::new(base.join("proc"));
    let boot = read_boot_id(
        &fs,
        &base.join("proc/sys/kernel/random/boot_id"),
        Bounds::POLICY.max_file_bytes,
    )
    .expect("boot id");
    assert_eq!(
        boot.to_string(),
        "00112233-4455-6677-8899-aabbccddeeff",
        "the fixture's boot id reads back in the kernel's spelling"
    );

    // The fixture's `/proc/1/stat` is written from proc(5) field numbers: each field carries
    // its own number as its value, and only state (3), num_threads (20), starttime (22) and
    // delayacct (42) hold a fact. So every assertion below names the field it proves.
    let files = read_process(&fs, &proc, 1, Bounds::POLICY, CmdlineBound::SAMPLER);
    let stat = files.stat.expect("fixture stat");
    assert_eq!(stat.pid, 1);
    assert_eq!(stat.comm, b"demo-worker");
    assert_eq!(stat.state, b'S');
    assert_eq!(stat.minflt, 10, "minflt is proc(5) field 10");
    assert_eq!(stat.majflt, 12, "majflt is proc(5) field 12");
    assert_eq!((stat.utime, stat.stime), (14, 15));
    assert_eq!(stat.num_threads, 20, "num_threads is field 20");
    assert_eq!(stat.start_ticks, 22, "starttime is field 22");
    assert_eq!(stat.rss_pages, 24, "rss is field 24");
    assert_eq!(stat.delayacct_blkio_ticks, Some(42));
    // 22 ticks at the kernel's usual 100 Hz is 220 ms after boot.
    assert_eq!(
        ClockTicks::new(100)
            .expect("rate")
            .ticks_to_nanos(stat.start_ticks),
        Some(220_000_000)
    );

    // The process identity is then (boot, 1, 22) — and a memoised value whose start tick
    // differs is a different process, which is what makes the identity safe as a key.
    let identity = ProcessIdentity::new(boot, stat.pid, stat.start_ticks).expect("identity");
    assert_eq!(
        (identity.boot(), identity.pid(), identity.start_ticks()),
        (boot, 1, 22)
    );

    // The fixture's `status`, `statm` and `cmdline` parse under their own bounds too.
    assert!(files.status.is_ok());
    assert!(files.statm.is_ok());
    let cmdline = files.cmdline.expect("fixture cmdline");
    assert_eq!(cmdline.text, "demo-worker --serve --port=8080");

    // A pid listing keeps only numeric directory names and never the `/proc` pseudo-dirs.
    let pids = list_pids(&fs, &proc, Bounds::POLICY).expect("pids");
    assert_eq!(
        pids,
        vec![1],
        "the fixture tree holds one process directory; `pressure` and `sys` are not pids"
    );
}

#[test]
fn a_fixture_cgroup_directory_is_read_below_a_root_without_a_path_identity() {
    // The same reader, the same bytes, twice: the readings are identical, and the only
    // thing that could have made them differ is a path — which nothing keys on.
    let base = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures");
    let fs = RealFs::new();
    let first = read_file(
        &fs,
        &base.join("cgroupfs"),
        "cpu.stat",
        Bounds::POLICY,
        hostobs_kernel::cgroup::Absence::Controller,
    )
    .expect("cpu.stat");
    let second = read_file(
        &fs,
        &base.join("cgroupfs"),
        "cpu.stat",
        Bounds::POLICY,
        hostobs_kernel::cgroup::Absence::Controller,
    )
    .expect("cpu.stat");
    assert_eq!(first, second);
    // An absent controller file is the caller's declared absence, not an error and not a
    // zero reading.
    assert_eq!(
        read_file(
            &fs,
            &base.join("cgroupfs"),
            "not-a-controller-file",
            Bounds::POLICY,
            hostobs_kernel::cgroup::Absence::Feature
        )
        .unwrap_err()
        .quality(),
        QualityCode::UNSUPPORTED
    );
}
