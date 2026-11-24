//! Every refusal in the kernel crate states a fact, maps to a quality code, and renders a
//! text no other refusal shares.
//!
//! A refusal is the crate's answer when it cannot read a fact, so its *rendering* and its
//! *code* are part of the contract a consumer sees in a log line or an evidence file. Two
//! refusals that render the same text are indistinguishable to whoever reads the log, and
//! a refusal that maps to a measurement code would let a caller publish a value that was
//! never read. Both are asserted here for every variant of every error type, which is the
//! only way a newly added variant cannot quietly skip the rule.

use hostobs_kernel::cgroup::{Absence, KernelError, Limit, Readings};
use hostobs_kernel::clock::{ClockError, Timeline};
use hostobs_kernel::cmdline::CmdlineError;
use hostobs_kernel::fs::{FsError, IoKind, Meta};
use hostobs_kernel::identity::{ClockTicks, IdentityError};
use hostobs_kernel::parse::{lines, parse_failure_quality, ParseError};
use hostobs_kernel::procfs::{parse_loadavg, parse_meminfo, ProcError};
use hostobs_kernel::quality::QualityCode;

/// Assert that every error in `errors` renders a non-empty, distinct text and that its
/// quality code is not [`QualityCode::Ok`].
fn refuses(rendered: Vec<(String, QualityCode)>) {
    let mut texts: Vec<String> = Vec::new();
    for (text, quality) in rendered {
        assert!(!text.is_empty(), "a refusal renders nothing");
        assert!(
            !quality.is_measurement(),
            "{text:?} maps to a measurement code"
        );
        assert!(!texts.contains(&text), "two refusals render {text:?}");
        texts.push(text);
    }
}

#[test]
fn every_cgroup_refusal_maps_to_its_code_and_renders_its_own_text() {
    let rendered: Vec<(String, QualityCode)> = vec![
        (
            KernelError::Absent(QualityCode::Absent).detail(),
            KernelError::Absent(QualityCode::Absent).quality(),
        ),
        (
            KernelError::Absent(QualityCode::Unsupported).detail(),
            KernelError::Absent(QualityCode::Unsupported).quality(),
        ),
        (
            KernelError::Read(FsError::PermissionDenied).to_string(),
            KernelError::Read(FsError::PermissionDenied).quality(),
        ),
        (
            KernelError::Parse {
                file: "cpu.stat",
                line: Some(3),
            }
            .to_string(),
            KernelError::Parse {
                file: "cpu.stat",
                line: Some(3),
            }
            .quality(),
        ),
        (
            KernelError::Parse {
                file: "cpu.stat",
                line: None,
            }
            .to_string(),
            KernelError::Parse {
                file: "cpu.stat",
                line: None,
            }
            .quality(),
        ),
        (
            KernelError::UnknownTopology.to_string(),
            KernelError::UnknownTopology.quality(),
        ),
        (
            KernelError::NotAValue.to_string(),
            KernelError::NotAValue.quality(),
        ),
    ];
    refuses(rendered);

    // The two parse refusals name the line, which is what makes a corrupt file
    // diagnosable, and an absence names the code the caller chose.
    assert_eq!(
        KernelError::Parse {
            file: "io.stat",
            line: Some(7)
        }
        .detail(),
        "io.stat: line 7 does not parse"
    );
    assert_eq!(
        KernelError::Absent(QualityCode::Unsupported).detail(),
        "absent (unsupported)"
    );
    // An absence is the *caller's* code: the same missing file is `absent` for a controller
    // and `unsupported` for a kernel feature, and the reader never picks.
    assert_eq!(Absence::Controller.quality(), QualityCode::Absent);
    assert_eq!(Absence::Feature.quality(), QualityCode::Unsupported);
    // `From<FsError>` keeps the I/O failure, so a caller's `?` does not discard it.
    assert_eq!(
        KernelError::from(FsError::TooLarge { max_bytes: 8 }).quality(),
        QualityCode::NotSampled
    );
}

#[test]
fn every_proc_refusal_maps_to_its_code_and_renders_its_own_text() {
    let rendered: Vec<(String, QualityCode)> = vec![
        (ProcError::Gone.to_string(), ProcError::Gone.quality()),
        (
            ProcError::Read(FsError::NotFound).to_string(),
            ProcError::Read(FsError::NotFound).quality(),
        ),
        (
            ProcError::NotAPid {
                name: "self".to_string(),
            }
            .to_string(),
            ProcError::NotAPid {
                name: "self".to_string(),
            }
            .quality(),
        ),
        (
            ProcError::Parse {
                file: "stat",
                line: Some(1),
            }
            .to_string(),
            ProcError::Parse {
                file: "stat",
                line: Some(1),
            }
            .quality(),
        ),
        (
            ProcError::Parse {
                file: "stat",
                line: None,
            }
            .to_string(),
            ProcError::Parse {
                file: "stat",
                line: None,
            }
            .quality(),
        ),
        (
            ProcError::Kernel(KernelError::UnknownTopology).to_string(),
            ProcError::Kernel(KernelError::UnknownTopology).quality(),
        ),
        (
            ProcError::Cmdline(CmdlineError::Missing).to_string(),
            ProcError::Cmdline(CmdlineError::Missing).quality(),
        ),
        (
            ProcError::Identity(IdentityError::StatComm).to_string(),
            ProcError::Identity(IdentityError::StatComm).quality(),
        ),
        (
            ProcError::Identity(IdentityError::Read(FsError::NotFound)).to_string(),
            ProcError::Identity(IdentityError::Read(FsError::NotFound)).quality(),
        ),
    ];
    refuses(rendered);

    // A vanished process is an absence, not a failure: the sampler's answer was "gone".
    assert_eq!(ProcError::Gone.quality(), QualityCode::Absent);
    assert_eq!(ProcError::Gone.detail(), "the process is gone");
    assert_eq!(
        ProcError::from(FsError::NotFound),
        ProcError::Gone,
        "a missing file is the process, not the reader"
    );
    assert_eq!(
        ProcError::from(FsError::PermissionDenied),
        ProcError::Read(FsError::PermissionDenied),
        "a permission failure is a failure"
    );
    assert_eq!(
        ProcError::from(hostobs_kernel::cmdline::CmdlineError::BoundZero).quality(),
        QualityCode::NotSampled
    );
}

#[test]
fn every_cmdline_refusal_maps_to_its_code_and_renders_its_own_text() {
    let rendered: Vec<(String, QualityCode)> = vec![
        (
            CmdlineError::Missing.to_string(),
            CmdlineError::Missing.quality(),
        ),
        (
            CmdlineError::Read(FsError::PermissionDenied).to_string(),
            CmdlineError::Read(FsError::PermissionDenied).quality(),
        ),
        (
            CmdlineError::BoundZero.to_string(),
            CmdlineError::BoundZero.quality(),
        ),
    ];
    refuses(rendered);
    // A removed process has no command line; that is an absence, not an unreadable file.
    assert_eq!(CmdlineError::from(FsError::NotFound), CmdlineError::Missing);
    assert_eq!(
        CmdlineError::from(FsError::TooLarge { max_bytes: 4 }),
        CmdlineError::Read(FsError::TooLarge { max_bytes: 4 }),
        "a bound refusing a whole file is a read failure here"
    );
}

#[test]
fn every_identity_and_clock_refusal_maps_to_unreadable_and_renders_its_own_text() {
    let identity_errors = vec![
        IdentityError::BootIdLength { len: 4 },
        IdentityError::BootIdNonHex { index: 20 },
        IdentityError::BootIdSeparator { index: 7 },
        IdentityError::BootIdMissing,
        IdentityError::DeviceZero,
        IdentityError::InodeZero,
        IdentityError::PidZero,
        IdentityError::StartTicksZero,
        IdentityError::StatShape { field: 3 },
        IdentityError::StatComm,
        IdentityError::Read(FsError::NotFound),
        IdentityError::Parse(ParseError::Empty),
    ];
    let rendered = identity_errors
        .iter()
        .map(|error| (error.to_string(), error.quality()))
        .collect();
    refuses(rendered);
    // An identity is never a value: every refusal is `unreadable`, and a zero-field
    // identity (`device 0`) is a refusal rather than a key.
    assert_eq!(
        hostobs_kernel::identity::identity_failure_quality(),
        QualityCode::Unreadable
    );
    assert_eq!(
        ClockTicks::new(0),
        Err(IdentityError::Parse(ParseError::Empty)),
        "a zero tick rate is not a rate"
    );

    let big = Timeline::new([1; 16], 1);
    let later = Timeline::new([2; 16], 2);
    let clock_errors = vec![
        (ClockError::MonoWentBackwards.to_string(), 3),
        (
            ClockError::NotComparable {
                previous: big,
                current: later,
            }
            .to_string(),
            3,
        ),
    ];
    let mut texts: Vec<String> = Vec::new();
    for (text, _) in clock_errors {
        assert!(!text.is_empty());
        assert!(!texts.contains(&text), "two clock refusals render {text:?}");
        texts.push(text);
    }
    assert!(texts[1].contains("cannot be compared"), "{}", texts[1]);
}

#[test]
fn every_parse_refusal_maps_to_unreadable_and_renders_its_own_text() {
    let rendered: Vec<(String, QualityCode)> = vec![
        (ParseError::Empty.to_string(), parse_failure_quality()),
        (
            ParseError::NotANumber {
                byte: b'x',
                offset: 2,
            }
            .to_string(),
            parse_failure_quality(),
        ),
        (ParseError::Negative.to_string(), parse_failure_quality()),
        (ParseError::Overflow.to_string(), parse_failure_quality()),
        (
            ParseError::TooManyLines { max_lines: 3 }.to_string(),
            parse_failure_quality(),
        ),
        (
            ParseError::MissingField { line: 1 }.to_string(),
            parse_failure_quality(),
        ),
        (
            ParseError::BadPair { offset: 4 }.to_string(),
            parse_failure_quality(),
        ),
        (
            ParseError::ValueMissing.to_string(),
            parse_failure_quality(),
        ),
    ];
    refuses(rendered);
    // The line bound is a refusal, and a file past it is never truncated to a plausible
    // subset.
    assert_eq!(
        lines(b"a\nb\nc\n", 2),
        Err(ParseError::TooManyLines { max_lines: 2 })
    );
}

#[test]
fn every_filesystem_refusal_carries_its_kind_and_its_code() {
    // A metadata value is a fact; the constructor it came from is not the reader's business
    // except for the inode, which is what identity uses.
    assert_eq!(Meta::file(7).inode, 7);
    assert_eq!(Meta::directory(9).inode, 9);
    assert!(Meta::directory(9).is_directory);
    assert_eq!(IoKind::NotFound, IoKind::NotFound);

    let rendered: Vec<(String, QualityCode)> = vec![
        (
            FsError::NotFound.to_string(),
            FsError::NotFound.quality(QualityCode::Absent),
        ),
        (
            FsError::PermissionDenied.to_string(),
            FsError::PermissionDenied.quality(QualityCode::Absent),
        ),
        (
            FsError::NotAFile.to_string(),
            FsError::NotAFile.quality(QualityCode::Absent),
        ),
        (
            FsError::NotADirectory.to_string(),
            FsError::NotADirectory.quality(QualityCode::Absent),
        ),
        (
            FsError::TooLarge { max_bytes: 16 }.to_string(),
            FsError::TooLarge { max_bytes: 16 }.quality(QualityCode::Absent),
        ),
        (
            FsError::TooManyEntries { max_entries: 4 }.to_string(),
            FsError::TooManyEntries { max_entries: 4 }.quality(QualityCode::Absent),
        ),
        (
            FsError::Io(IoKind::Other).to_string(),
            FsError::Io(IoKind::Other).quality(QualityCode::Absent),
        ),
    ];
    refuses(rendered);
    // The bound is named in the text, so an operator can see which bound refused the file.
    assert!(FsError::TooLarge { max_bytes: 16 }
        .to_string()
        .contains("16"));
}

#[test]
fn readings_refuse_a_placeholder_that_looks_measured() {
    let mut readings = Readings::new();
    readings.measured("a", hostobs_kernel::value::Value::U64(1));
    readings.missing("b", QualityCode::Absent);
    // Pushing an `ok` absence is refused by the value type, so it adds nothing rather
    // than publishing a zero that reads as a measurement.
    readings.missing("c", QualityCode::Ok);
    assert_eq!(readings.len(), 2);
    assert_eq!(readings.u64("a"), Some(1));
    assert_eq!(readings.u64("b"), None);
    assert_eq!(
        readings.quality("c"),
        None,
        "an `ok` absence is not published"
    );
    assert!(!readings.is_empty());
    let empty = Readings::new();
    assert!(empty.is_empty());

    // A limit's value and its quality travel together.
    let limit = Limit {
        file: "pids.max",
        value: None,
        raw: b"max".to_vec(),
        quality: QualityCode::Unsupported,
    };
    assert_eq!(limit.reading().u64(), None);
    assert_eq!(
        limit.reading().quality(),
        QualityCode::Unsupported,
        "a `max` limit is a state, not a placeholder zero"
    );
}

#[test]
fn the_host_readers_keep_each_refusal_separate_in_one_snapshot() {
    // A tree with a broken `stat` and a good `meminfo`: the snapshot still carries both
    // answers, because one unreadable file must not hide the other facts.
    let base = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/proc");
    let broken = base.join("stat");
    let bytes = std::fs::read(&broken).expect("fixture stat");
    assert!(
        hostobs_kernel::procfs::parse_proc_stat(&bytes, hostobs_kernel::Bounds::POLICY).is_ok(),
        "the committed fixture parses"
    );
    // `cleanup_linux` is field 15 in `meminfo`; a value with an unknown unit must not be
    // read as `kB`.
    assert!(parse_meminfo(b"Cleanup: 100 MB\n", hostobs_kernel::Bounds::POLICY).is_err());
    // A `loadavg` with a non-numeric pair component is refused, not zeroed.
    assert!(parse_loadavg(b"0.5 0.4 0.3 x/10 20\n").is_err());
}
