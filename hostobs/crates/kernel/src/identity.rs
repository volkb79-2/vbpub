//! Identity inputs: boot id, `(mount identity, inode)` and process `starttime` ticks.
//!
//! Plan v4 §C.1 makes three identity facts into keys: a boot id, a cgroup key
//! `(mount id, cgroup inode)`, and a process key `(boot id, pid, starttime ticks)`. The
//! path is never a key. This module reads those *inputs* from the host through [`Fs`] and
//! parses them strictly; the mint/retire policy (`IncarnationRegistry`) lives in
//! `hostobs-model`, and the probe package is what puts the two together.
//!
//! # Contracts
//!
//! - **A path is a label, never an identity.** [`CgroupIdentity`] is built from metadata
//!   (a device and an inode), so a cgroup recreated at the same path gets a different
//!   identity and a renamed cgroup keeps its own. [`cgroup_identity`] therefore never
//!   looks at the path's text.
//! - **A bare pid is never an identity.** [`ProcessIdentity`] needs a boot id and a
//!   `starttime`, so a recycled pid is a different process. A `starttime` of 0 is refused:
//!   the kernel reports it from tick 1, so 0 means the field was not read.
//! - **A boot id has exactly one spelling set.** The kernel writes 32 hex digits; the
//!   `boot_id` command also accepts the 36-character dashed form. Anything else, including
//!   upper-case hex, is [`IdentityError`] rather than a best-effort normalisation.
//! - **A `starttime` is clock ticks, not seconds.** The conversion to nanoseconds needs
//!   `CLK_TCK`, which is a host fact this crate does not invent: [`ClockTicks`] carries it
//!   and the caller supplies it (a test supplies 100, the sampler's own fallback with its
//!   "reported, never silent" flag; see `HOB-18`).
//!
//! # Mount identity: `st_dev` here, `statx`'s mount id in the plan
//!
//! Plan v4 §C.1 says `(mount id, cgroup inode)`. `stx_mnt_id` needs a `statx` syscall,
//! which in Rust without `unsafe` is not reachable; [`crate::fs::Meta`] therefore carries
//! `st_dev`, the filesystem instance's own id. For the cgroupfs this crate reads, that
//! property is the one the rule exists for: a device id does not change while a cgroup
//! lives, and it distinguishes an inode number reused on another filesystem. The residual
//! difference — two separate mounts of *the same* filesystem instance share `st_dev` — is
//! recorded as `HOB-18` with the condition under which it matters (two cgroupfs mounts
//! visible at once) and the exit criteria (a statx wrapper confined to one small module
//! under the plan's `unsafe` allowlist, when a package actually needs it).

use core::fmt;
use std::path::Path;

use crate::fs::{Fs, FsError};
use crate::parse::{parse_u64, ParseError};
use crate::quality::QualityCode;

/// Every identity refusal.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum IdentityError {
    /// A boot id must be 32 hex digits or the kernel's 36-character dashed form.
    BootIdLength {
        /// Observed length in characters.
        len: usize,
    },
    /// A boot id character that is not a lower-case hex digit.
    BootIdNonHex {
        /// Character index.
        index: usize,
    },
    /// The dashed form has a separator outside the kernel's four positions.
    BootIdSeparator {
        /// Character index.
        index: usize,
    },
    /// The boot id file is empty, so nothing was read.
    BootIdMissing,
    /// A device (mount) id of 0 names no mount: the kernel never reports it.
    DeviceZero,
    /// Inode 0 names no file: the kernel never reports it.
    InodeZero,
    /// Pid 0 is not a process.
    PidZero,
    /// `starttime` 0 is not a reading: the kernel reports it from tick 1.
    StartTicksZero,
    /// A process stat line does not have the fields an identity needs.
    StatShape {
        /// The zero-based field index after the `comm` field that was missing.
        field: usize,
    },
    /// The command name is not delimited by parentheses in one stat line.
    StatComm,
    /// The file could not be read.
    Read(FsError),
    /// The text could not be parsed.
    Parse(ParseError),
}

impl fmt::Display for IdentityError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            IdentityError::BootIdLength { len } => {
                write!(f, "boot id has {len} characters, expected 32 or 36")
            }
            IdentityError::BootIdNonHex { index } => {
                write!(f, "boot id character {index} is not a lower-case hex digit")
            }
            IdentityError::BootIdSeparator { index } => {
                write!(f, "boot id separator {index} is misplaced")
            }
            IdentityError::BootIdMissing => f.write_str("the boot id file is empty"),
            IdentityError::DeviceZero => f.write_str("device id 0 names no mount"),
            IdentityError::InodeZero => f.write_str("inode 0 names no cgroup"),
            IdentityError::PidZero => f.write_str("pid 0 names no process"),
            IdentityError::StartTicksZero => {
                f.write_str("starttime 0 is not a reading the kernel produces")
            }
            IdentityError::StatShape { field } => {
                write!(
                    f,
                    "the process stat line is missing field {field} after comm"
                )
            }
            IdentityError::StatComm => f.write_str("the stat line has no (comm) delimiters"),
            IdentityError::Read(error) => write!(f, "{error}"),
            IdentityError::Parse(error) => write!(f, "{error}"),
        }
    }
}

impl core::error::Error for IdentityError {}

impl IdentityError {
    /// The quality code this refusal maps to.
    ///
    /// An identity that cannot be established never becomes a value: the caller either
    /// omits the entity (producing no reading at all) or reports the one field it could
    /// not read as unreadable. It is never `unknown-topology` (which is about a *value's*
    /// device tree) and never a zero.
    #[must_use]
    pub const fn quality(&self) -> QualityCode {
        QualityCode::UNREADABLE
    }
}

/// The quality code an identity refusal maps to.
///
/// An identity that cannot be established is never "unknown topology" and never a value:
/// the caller either skips the entity (generating no reading) or reports it as unreadable.
#[must_use]
pub const fn identity_failure_quality() -> QualityCode {
    QualityCode::UNREADABLE
}

/// A boot id (plan v4 §C.1), stored as the 16 bytes the wire header carries.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
pub struct BootId([u8; 16]);

impl BootId {
    /// Parse the kernel's spelling(s): 32 hex digits, or 36 characters with four dashes.
    ///
    /// # Errors
    ///
    /// [`IdentityError::BootIdLength`], [`IdentityError::BootIdNonHex`] and
    /// [`IdentityError::BootIdSeparator`].
    pub fn parse(text: &str) -> Result<BootId, IdentityError> {
        let trimmed = text.trim();
        if trimmed.is_empty() {
            return Err(IdentityError::BootIdMissing);
        }
        let characters: Vec<char> = trimmed.chars().collect();
        let digits: Vec<(usize, char)> = match characters.len() {
            32 => characters.iter().copied().enumerate().collect(),
            36 => characters
                .iter()
                .copied()
                .enumerate()
                .filter(|(_index, character)| *character != '-')
                .collect(),
            len => return Err(IdentityError::BootIdLength { len }),
        };
        if characters.len() == 36 {
            // The kernel's four separators sit after 8, 13, 18 and 23 characters. A dash
            // anywhere else is a malformed id, not a bare-form id with a stray character.
            if characters.iter().enumerate().any(|(index, character)| {
                if *character == '-' {
                    !matches!(index, 8 | 13 | 18 | 23)
                } else {
                    false
                }
            }) {
                let index = characters
                    .iter()
                    .position(|character| *character == '-')
                    .unwrap_or_default();
                return Err(IdentityError::BootIdSeparator { index });
            }
            if digits.len() != 32 {
                let len = digits.len();
                return Err(IdentityError::BootIdLength { len });
            }
        }
        let mut bytes = [0u8; 16];
        for (position, (index, character)) in digits.iter().enumerate() {
            let Some(value) = character.to_digit(16) else {
                return Err(IdentityError::BootIdNonHex { index: *index });
            };
            // Upper-case hex is not the kernel's spelling, and one id has one identity.
            if character.is_ascii_uppercase() {
                return Err(IdentityError::BootIdNonHex { index: *index });
            }
            let shift = u32::try_from(position % 2)
                .map(|odd| if odd == 1 { 4 } else { 0 })
                .unwrap_or(0);
            bytes[position / 2] |= u8::try_from(value).unwrap_or(0) << shift;
        }
        Ok(BootId(bytes))
    }

    /// The 16 bytes the wire header carries.
    #[must_use]
    pub const fn as_bytes(&self) -> &[u8; 16] {
        &self.0
    }

    /// A boot id from the wire bytes (16 bytes are a whole id by definition).
    #[must_use]
    pub const fn from_wire(bytes: [u8; 16]) -> BootId {
        BootId(bytes)
    }
}

impl fmt::Display for BootId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let digits = self.0;
        write!(
            f,
            "{:02x}{:02x}{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}",
            digits[0], digits[1], digits[2], digits[3], digits[4], digits[5], digits[6], digits[7],
            digits[8], digits[9], digits[10], digits[11], digits[12], digits[13], digits[14], digits[15]
        )
    }
}

/// The cgroup key input: a filesystem instance id and an inode, never a path.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
pub struct CgroupIdentity {
    device: u64,
    inode: u64,
}

impl CgroupIdentity {
    /// A `(device, inode)` identity; zero is refused for both parts.
    ///
    /// # Errors
    ///
    /// [`IdentityError::DeviceZero`] and [`IdentityError::InodeZero`].
    pub const fn new(device: u64, inode: u64) -> Result<CgroupIdentity, IdentityError> {
        if device == 0 {
            return Err(IdentityError::DeviceZero);
        }
        if inode == 0 {
            return Err(IdentityError::InodeZero);
        }
        Ok(CgroupIdentity { device, inode })
    }

    /// The filesystem instance id (`st_dev`; see the module docs on `statx`).
    #[must_use]
    pub const fn device(self) -> u64 {
        self.device
    }

    /// The inode.
    #[must_use]
    pub const fn inode(self) -> u64 {
        self.inode
    }
}

/// The identity of one directory (or file) path, read from metadata only.
///
/// # Errors
///
/// [`IdentityError::Read`] for an unreadable path, and the zero-field refusals.
pub fn cgroup_identity<F: Fs>(fs: &F, path: &Path) -> Result<CgroupIdentity, IdentityError> {
    let meta = fs.meta(path).map_err(IdentityError::Read)?;
    CgroupIdentity::new(meta.device, meta.inode)
}

/// A process key input: boot id, pid, `starttime` ticks (plan v4 §C.1).
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
pub struct ProcessIdentity {
    boot: BootId,
    pid: u32,
    start_ticks: u64,
}

impl ProcessIdentity {
    /// A process identity; pid 0 and `starttime` 0 are refused.
    ///
    /// # Errors
    ///
    /// [`IdentityError::PidZero`] and [`IdentityError::StartTicksZero`].
    pub const fn new(
        boot: BootId,
        pid: u32,
        start_ticks: u64,
    ) -> Result<ProcessIdentity, IdentityError> {
        if pid == 0 {
            return Err(IdentityError::PidZero);
        }
        if start_ticks == 0 {
            return Err(IdentityError::StartTicksZero);
        }
        Ok(ProcessIdentity {
            boot,
            pid,
            start_ticks,
        })
    }

    /// The boot the process was seen in.
    #[must_use]
    pub const fn boot(self) -> BootId {
        self.boot
    }

    /// The pid (not an identity on its own).
    #[must_use]
    pub const fn pid(self) -> u32 {
        self.pid
    }

    /// The `starttime` field, in clock ticks since boot.
    #[must_use]
    pub const fn start_ticks(self) -> u64 {
        self.start_ticks
    }
}

/// `CLK_TCK`: how many `starttime`/`utime` ticks the kernel counts per second.
///
/// The kernel's value is a host fact (`sysconf(_SC_CLK_TCK)`, normally 100); this crate
/// does not invent it. A caller that could not read it says so with [`ClockTicks::unknown`]
/// and any nanosecond conversion becomes a typed refusal rather than a guess.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ClockTicks(u64);

impl ClockTicks {
    /// A tick rate; zero is refused because it would divide by zero.
    ///
    /// # Errors
    ///
    /// [`IdentityError::Parse`] with [`ParseError::Empty`]: a zero tick rate is not a
    /// reading.
    pub const fn new(ticks_per_second: u64) -> Result<ClockTicks, IdentityError> {
        if ticks_per_second == 0 {
            return Err(IdentityError::Parse(ParseError::Empty));
        }
        Ok(ClockTicks(ticks_per_second))
    }

    /// A tick rate that could not be read: no conversion is possible.
    #[must_use]
    pub const fn unknown() -> Option<ClockTicks> {
        None
    }

    /// The rate itself.
    #[must_use]
    pub const fn per_second(self) -> u64 {
        self.0
    }

    /// Convert `ticks` to nanoseconds, or refuse when the multiplication overflows.
    #[must_use]
    pub fn ticks_to_nanos(self, ticks: u64) -> Option<u64> {
        ticks.checked_mul(1_000_000_000).map(|nanos| nanos / self.0)
    }
}

/// The fields of one `/proc/<pid>/stat` line an identity and a reader need.
///
/// The `comm` field may contain spaces and parentheses, so the line is split at its last
/// `)`: everything before it is `pid (comm`, and the fields after it are numbered from 3
/// (state) as the kernel's own field numbers do.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StatLine {
    /// The pid the line names (field 1).
    pub pid: u32,
    /// The command name between the parentheses, exactly as the kernel wrote it (it may be
    /// non-UTF-8, so it is kept as bytes).
    pub comm: Vec<u8>,
    /// Field 3: the state letter.
    pub state: u8,
    /// Field 12: minor faults.
    pub minflt: u64,
    /// Field 13: major faults since the process started.
    pub majflt: u64,
    /// Field 14: user time, clock ticks.
    pub utime: u64,
    /// Field 15: system time, clock ticks.
    pub stime: u64,
    /// Field 20: number of threads.
    pub num_threads: u64,
    /// Field 22: `starttime`, clock ticks since boot.
    pub start_ticks: u64,
    /// Field 24: resident set size, pages.
    pub rss_pages: u64,
    /// Field 42 (`delayacct_blkio_ticks`), when the kernel writes it.
    pub delayacct_blkio_ticks: Option<u64>,
}

/// Parse one `/proc/<pid>/stat` line.
///
/// # Errors
///
/// [`IdentityError::StatComm`] for a line without the `(comm)` delimiters,
/// [`IdentityError::StatShape`] for a line too short for the fields an identity needs,
/// [`IdentityError::PidZero`] for a pid that is not a process, and
/// [`IdentityError::Parse`] for a field that does not parse.
pub fn parse_stat(line: &[u8]) -> Result<StatLine, IdentityError> {
    let open = line
        .iter()
        .position(|byte| *byte == b'(')
        .ok_or(IdentityError::StatComm)?;
    let close = line
        .iter()
        .rposition(|byte| *byte == b')')
        .ok_or(IdentityError::StatComm)?;
    if close <= open {
        return Err(IdentityError::StatComm);
    }
    let pid = parse_u64(&line[..open]).map_err(IdentityError::Parse)?;
    let pid = u32::try_from(pid).map_err(|_| IdentityError::Parse(ParseError::Overflow))?;
    if pid == 0 {
        return Err(IdentityError::PidZero);
    }
    let comm = line
        .get(open + 1..close)
        .ok_or(IdentityError::StatComm)?
        .to_vec();
    // Fields after `)`: index 0 is field 3 (state). The kernel's own numbering starts at 1
    // (pid) and 2 (comm), so index `i` is field `i + 3`.
    let rest = line.get(close + 1..).ok_or(IdentityError::StatComm)?;
    let fields: Vec<&[u8]> = crate::parse::fields(rest);
    // Field 3 is the state letter (`R`, `S`, `D`, …), not a number: it is the one field
    // this reader refuses to parse as one.
    let state_letter = fields
        .first()
        .ok_or(IdentityError::StatShape { field: 3 })?
        .first()
        .copied()
        .ok_or(IdentityError::StatShape { field: 3 })?;
    let required = [
        (9usize, 12usize),
        (10usize, 13usize),
        (11usize, 14usize),
        (12usize, 15usize),
        (17usize, 20usize),
        (19usize, 22usize),
        (21usize, 24usize),
    ];
    let mut minflt = 0u64;
    let mut majflt = 0u64;
    let mut utime = 0u64;
    let mut stime = 0u64;
    let mut num_threads = 0u64;
    let mut start_ticks = 0u64;
    let mut rss_pages = 0u64;
    let mut delayacct = None;
    for (index, field_number) in required {
        let field = fields.get(index).ok_or(IdentityError::StatShape {
            field: field_number,
        })?;
        let parsed = parse_u64(field).map_err(IdentityError::Parse)?;
        match index {
            9 => minflt = parsed,
            10 => majflt = parsed,
            11 => utime = parsed,
            12 => stime = parsed,
            17 => num_threads = parsed,
            19 => start_ticks = parsed,
            21 => rss_pages = parsed,
            _ => {}
        }
    }
    // delayacct_blkio_ticks is field 42, i.e. index 39. It exists only when the kernel
    // builds with delay accounting; its absence is not an error, just no reading.
    if let Some(field) = fields.get(39) {
        delayacct = Some(parse_u64(field).map_err(IdentityError::Parse)?);
    }
    Ok(StatLine {
        pid,
        comm,
        state: state_letter,
        minflt,
        majflt,
        utime,
        stime,
        num_threads,
        start_ticks,
        rss_pages,
        delayacct_blkio_ticks: delayacct,
    })
}

/// Read the boot id (`/proc/sys/kernel/random/boot_id` by default).
///
/// # Errors
///
/// [`IdentityError::Read`] for an unreadable file and the [`BootId::parse`] refusals.
pub fn read_boot_id<F: Fs>(fs: &F, path: &Path, max_bytes: usize) -> Result<BootId, IdentityError> {
    let bytes = fs.read(path, max_bytes).map_err(IdentityError::Read)?;
    let text = core::str::from_utf8(&bytes).map_err(|_| IdentityError::Parse(ParseError::Empty))?;
    BootId::parse(text)
}

#[cfg(test)]
mod tests {
    use super::{
        cgroup_identity, parse_stat, read_boot_id, BootId, CgroupIdentity, ClockTicks,
        IdentityError, ProcessIdentity, StatLine,
    };
    use crate::fs::MemFs;
    use crate::parse::ParseError;
    use std::path::Path;

    const BOOT: &str = "00112233-4455-6677-8899-aabbccddeeff";
    const BARE: &str = "00112233445566778899aabbccddeeff";

    fn stat_line(pid: u32, comm: &str, start: u64, threads: u64, delayacct: bool) -> Vec<u8> {
        // The kernel's field order: 1 pid, 2 comm, 3 state, ... 12 minflt, 13 majflt,
        // 14 utime, 15 stime, 20 num_threads, 22 starttime, 24 rss, 42 delayacct.
        let mut fields: Vec<String> = vec!["0".to_string(); 44];
        fields[0] = "S".to_string();
        fields[9] = "7".to_string(); // minflt (field 12)
        fields[10] = "3".to_string(); // majflt (field 13)
        fields[11] = "5".to_string(); // utime
        fields[12] = "2".to_string(); // stime
        fields[17] = threads.to_string(); // num_threads
        fields[19] = start.to_string(); // starttime
        fields[21] = "100".to_string(); // rss pages
        if delayacct {
            fields[39] = "11".to_string();
        }
        let tail = if delayacct {
            fields.join(" ")
        } else {
            fields[..39].join(" ")
        };
        format!("{pid} ({comm}) {tail}\n").into_bytes()
    }

    #[test]
    fn a_boot_id_has_two_spellings_and_refuses_everything_else() {
        let dashed = BootId::parse(BOOT).expect("dashed form");
        let bare = BootId::parse(BARE).expect("bare form");
        assert_eq!(dashed, bare);
        assert_eq!(dashed.as_bytes()[0], 0x00);
        assert_eq!(dashed.as_bytes()[15], 0xff);
        assert_eq!(dashed.to_string(), BOOT);
        assert_eq!(BootId::from_wire(*dashed.as_bytes()), dashed);
        assert_eq!(
            BootId::parse(&format!("{BARE}\n")),
            Ok(bare),
            "the file ends with a newline"
        );

        assert_eq!(
            BootId::parse("0011"),
            Err(IdentityError::BootIdLength { len: 4 })
        );
        assert_eq!(BootId::parse(""), Err(IdentityError::BootIdMissing));
        assert_eq!(BootId::parse("   "), Err(IdentityError::BootIdMissing));
        assert_eq!(
            BootId::parse("00112233-4455-6677-8899-aabbccddeefg"),
            Err(IdentityError::BootIdNonHex { index: 35 })
        );
        assert_eq!(
            BootId::parse("00112233445566778899AABBCCDDEEFF"),
            Err(IdentityError::BootIdNonHex { index: 20 }),
            "one id has one spelling: upper case is refused"
        );
        assert_eq!(
            BootId::parse("0011223-34455-6677-8899-aabbccddeeff"),
            Err(IdentityError::BootIdSeparator { index: 7 })
        );
        assert_eq!(
            BootId::parse("001122334455-6677-8899-aabbccddeeff"),
            Err(IdentityError::BootIdLength { len: 35 })
        );
    }

    #[test]
    fn a_cgroup_identity_is_its_metadata_and_two_inodes_at_one_path_are_two_cgroups() {
        let mut fs = MemFs::new();
        fs.directory("/cg", 1);
        fs.file("/cg/cpu.stat", "usage_usec 1\n", 77);
        let first = cgroup_identity(&fs, Path::new("/cg")).expect("identity");
        assert_eq!(first.device(), 1);
        assert_eq!(first.inode(), 1);
        // The same path with another inode is another cgroup: that is the whole rule.
        fs.directory("/cg", 2);
        let second = cgroup_identity(&fs, Path::new("/cg")).expect("identity");
        assert_ne!(
            first, second,
            "a recreated cgroup at the same path is a different identity"
        );
        assert_eq!(
            cgroup_identity(&fs, Path::new("/gone")),
            Err(IdentityError::Read(crate::fs::FsError::NotFound))
        );
        assert_eq!(CgroupIdentity::new(0, 5), Err(IdentityError::DeviceZero));
        assert_eq!(CgroupIdentity::new(1, 0), Err(IdentityError::InodeZero));
    }

    #[test]
    fn a_process_identity_needs_a_boot_a_pid_and_a_start_tick() {
        let boot = BootId::parse(BOOT).expect("boot");
        let process = ProcessIdentity::new(boot, 1234, 90).expect("identity");
        assert_eq!(process.boot(), boot);
        assert_eq!(process.pid(), 1234);
        assert_eq!(process.start_ticks(), 90);
        assert_eq!(
            ProcessIdentity::new(boot, 0, 90),
            Err(IdentityError::PidZero)
        );
        assert_eq!(
            ProcessIdentity::new(boot, 1234, 0),
            Err(IdentityError::StartTicksZero),
            "a bare pid is not an identity, and a starttime of 0 was not read"
        );
        assert_ne!(
            process,
            ProcessIdentity::new(boot, 1234, 91).expect("same pid, new start")
        );
    }

    #[test]
    fn a_stat_line_is_read_at_its_kernel_field_numbers_including_a_parenthesis_in_comm() {
        let line = stat_line(4242, "worker (2)", 500, 3, true);
        let parsed: StatLine = parse_stat(&line).expect("stat");
        assert_eq!(parsed.pid, 4242);
        assert_eq!(parsed.comm, b"worker (2)", "spaces and parentheses survive");
        assert_eq!(parsed.state, b'S');
        assert_eq!(parsed.minflt, 7);
        assert_eq!(parsed.majflt, 3);
        assert_eq!(parsed.utime, 5);
        assert_eq!(parsed.stime, 2);
        assert_eq!(parsed.num_threads, 3);
        assert_eq!(parsed.start_ticks, 500);
        assert_eq!(parsed.rss_pages, 100);
        assert_eq!(parsed.delayacct_blkio_ticks, Some(11));

        // Without delay accounting the file simply has no field 42; that is not an error.
        let parsed = parse_stat(&stat_line(7, "plain", 9, 1, false)).expect("stat");
        assert_eq!(parsed.delayacct_blkio_ticks, None);
        assert_eq!(parsed.start_ticks, 9);
    }

    #[test]
    fn a_short_or_malformed_stat_line_is_a_typed_refusal() {
        assert_eq!(
            parse_stat(b"1 (a"),
            Err(IdentityError::StatComm),
            "no closing paren"
        );
        assert_eq!(parse_stat(b"1 a S"), Err(IdentityError::StatComm));
        assert_eq!(
            parse_stat(b"0 (a) S 1"),
            Err(IdentityError::PidZero),
            "pid 0 is not a process"
        );
        // Field 3 exists but field 12 does not: name the field the identity needed.
        assert_eq!(
            parse_stat(b"1 (a) S"),
            Err(IdentityError::StatShape { field: 12 }),
            "a line with only the state has no minflt field"
        );
        let short = b"1 (a) S 1 2 3 4 5 6 7 8 9";
        assert_eq!(
            parse_stat(short),
            Err(IdentityError::StatShape { field: 13 }),
            "ten fields after `)` reach field 12 and stop before field 13"
        );
        let bad = b"1 (a) S 1 2 3 4 5 6 7 8 9 x";
        assert_eq!(
            parse_stat(bad),
            Err(IdentityError::Parse(ParseError::NotANumber {
                byte: b'x',
                offset: 0
            }))
        );
        // A negative field is refused, never wrapped.
        let negative = b"1 (a) S 1 2 3 4 5 6 7 8 9 -1 0 0 0 0 0 0 0 0 0";
        assert_eq!(
            parse_stat(negative),
            Err(IdentityError::Parse(ParseError::Negative)),
            "a negative minflt is corrupt, not u64::MAX"
        );
    }

    #[test]
    fn clock_ticks_convert_or_refuse_and_never_invent_a_rate() {
        let ticks = ClockTicks::new(100).expect("the kernel's usual rate");
        assert_eq!(ticks.per_second(), 100);
        assert_eq!(ticks.ticks_to_nanos(100), Some(1_000_000_000));
        assert_eq!(ticks.ticks_to_nanos(0), Some(0));
        assert_eq!(
            ClockTicks::new(0),
            Err(IdentityError::Parse(ParseError::Empty)),
            "a zero tick rate would divide by zero"
        );
        assert_eq!(ClockTicks::unknown(), None);
        assert_eq!(
            ClockTicks::new(100).expect("rate").ticks_to_nanos(u64::MAX),
            None,
            "an overflowing conversion is refused, not wrapped"
        );
    }

    #[test]
    fn the_boot_id_is_read_through_the_root_abstraction() {
        let mut fs = MemFs::new();
        fs.directory("/proc/sys/kernel/random", 1);
        fs.file("/proc/sys/kernel/random/boot_id", format!("{BARE}\n"), 2);
        let boot =
            read_boot_id(&fs, Path::new("/proc/sys/kernel/random/boot_id"), 64).expect("boot id");
        assert_eq!(boot, BootId::parse(BOOT).expect("dashed"));
        assert_eq!(
            read_boot_id(&fs, Path::new("/proc/sys/kernel/random/boot_id"), 4),
            Err(IdentityError::Read(crate::fs::FsError::TooLarge {
                max_bytes: 4
            })),
            "a bound smaller than the file refuses it"
        );
        assert_eq!(
            read_boot_id(&fs, Path::new("/proc/nope"), 64),
            Err(IdentityError::Read(crate::fs::FsError::NotFound))
        );
    }

    #[test]
    fn every_identity_refusal_renders_its_fact_and_maps_to_unreadable() {
        let errors = [
            IdentityError::BootIdLength { len: 4 },
            IdentityError::BootIdNonHex { index: 3 },
            IdentityError::BootIdSeparator { index: 7 },
            IdentityError::BootIdMissing,
            IdentityError::DeviceZero,
            IdentityError::InodeZero,
            IdentityError::PidZero,
            IdentityError::StartTicksZero,
            IdentityError::StatShape { field: 12 },
            IdentityError::StatComm,
            IdentityError::Read(crate::fs::FsError::NotFound),
            IdentityError::Parse(ParseError::Empty),
        ];
        let mut texts = Vec::new();
        for error in errors {
            let text = error.to_string();
            assert!(!text.is_empty(), "{error:?}");
            assert!(
                !texts.contains(&text),
                "two refusals render the same text: {text}"
            );
            texts.push(text);
        }
        assert_eq!(
            super::identity_failure_quality(),
            super::QualityCode::UNREADABLE
        );
    }
}
