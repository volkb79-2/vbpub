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
/// (state) as the kernel's own field numbers do (proc(5), `Documentation/filesystems/`
/// `proc.rst`). Every field below is named by its **proc(5) field number**, and the index
/// into the split line is derived from it ([`STAT_FIELD_OFFSET`]) rather than written
/// twice: a hand-written `(index, field)` pair that disagreed with itself is exactly the
/// defect `HOB-23` fixed (minflt read from field 12 instead of field 10).
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StatLine {
    /// The pid the line names (field 1).
    pub pid: u32,
    /// The command name between the parentheses, exactly as the kernel wrote it (it may be
    /// non-UTF-8, so it is kept as bytes).
    pub comm: Vec<u8>,
    /// Field 3: the state letter.
    pub state: u8,
    /// Field 10 (`minflt`): minor faults this process has had since it started.
    ///
    /// Fields 11 (`cminflt`) and 13 (`cmajflt`) are the faults its waited-for children had;
    /// they are deliberately not published here, because they are not this process's faults
    /// and folding them into `minflt`/`majflt` would overstate them.
    pub minflt: u64,
    /// Field 12 (`majflt`): major faults this process has had since it started.
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

/// The offset between a proc(5) field number and an index into the fields after `)`.
///
/// `pid` is field 1 and `comm` is field 2 (and `comm` is consumed with its parentheses), so
/// the first field after `)` is the kernel's field 3 and index `i` is field `i + 3`.
/// Every field lookup in [`parse_stat`] goes through this one constant, so a field can be
/// named exactly once.
pub const STAT_FIELD_OFFSET: usize = 3;

/// The index into the fields after `)` of a kernel field number.
///
/// A field number below [`STAT_FIELD_OFFSET`] names one of the two fields that come *before*
/// `)`, so it has no index here; this crate only calls it with the field numbers it declares
/// (10, 12, 14, 15, 20, 22, 24, 42), all above the offset. `wrapping_sub` keeps the function
/// total and infallible for those inputs instead of introducing an unwrap.
#[must_use]
const fn field_index(field_number: usize) -> usize {
    field_number.wrapping_sub(STAT_FIELD_OFFSET)
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
    // (pid) and 2 (comm), so `field_index` derives an index from a field number and no
    // index is written by hand beside a field number anywhere in this reader.
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
    // The fields this reader needs, named by their proc(5) field numbers only. The index
    // into the split line is derived, never written beside the number: an index/field pair
    // written by hand is what let minflt (field 10) and majflt (field 12) be read from
    // fields 12 and 13 in the first place.
    let required = [10usize, 12, 14, 15, 20, 22, 24];
    let mut minflt = 0u64;
    let mut majflt = 0u64;
    let mut utime = 0u64;
    let mut stime = 0u64;
    let mut num_threads = 0u64;
    let mut start_ticks = 0u64;
    let mut rss_pages = 0u64;
    let mut delayacct = None;
    for field_number in required {
        let field = fields
            .get(field_index(field_number))
            .ok_or(IdentityError::StatShape {
                field: field_number,
            })?;
        let parsed = parse_u64(field).map_err(IdentityError::Parse)?;
        match field_number {
            // `minflt` (field 10) and `majflt` (field 12) are this process's own fault
            // counters; field 11 (`cminflt`) and field 13 (`cmajflt`) are its children's
            // and are deliberately not read.
            10 => minflt = parsed,
            12 => majflt = parsed,
            14 => utime = parsed,
            15 => stime = parsed,
            20 => num_threads = parsed,
            22 => start_ticks = parsed,
            24 => rss_pages = parsed,
            _ => {}
        }
    }
    // delayacct_blkio_ticks is field 42. It exists only when the kernel is built with
    // delay accounting; its absence is not an error, just no reading.
    if let Some(field) = fields.get(field_index(42)) {
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
        IdentityError, ProcessIdentity, StatLine, STAT_FIELD_OFFSET,
    };
    use crate::fs::MemFs;
    use crate::parse::ParseError;
    use std::path::Path;

    const BOOT: &str = "00112233-4455-6677-8899-aabbccddeeff";
    const BARE: &str = "00112233445566778899aabbccddeeff";

    /// A `/proc/<pid>/stat` line built from proc(5) field numbers.
    ///
    /// `value(field)` supplies field `field`'s text; the state (field 3) is always `S`, and
    /// the line ends at field `last`. The array is filled by **field number** and the index
    /// is derived once, so a helper cannot re-introduce the index/field disagreement that
    /// `HOB-23` fixed.
    fn stat_line_with(
        pid: u32,
        comm: &str,
        last: usize,
        value: impl Fn(usize) -> String,
    ) -> Vec<u8> {
        let tokens: Vec<String> = (STAT_FIELD_OFFSET..=last)
            .map(|field| {
                if field == STAT_FIELD_OFFSET {
                    "S".to_string()
                } else {
                    value(field)
                }
            })
            .collect();
        format!("{pid} ({comm}) {}\n", tokens.join(" ")).into_bytes()
    }

    /// A realistic stat line whose fields are distinct from their neighbours.
    ///
    /// Every field carries a value that differs from the value of the fields beside it, so
    /// an index that slips by one reads a different number and the assertion fails by name
    /// (the tautological helper of the M1b package carried `7` and `3` at the wrong
    /// indexes, and its own comments repeated the same wrong table).
    fn stat_line(pid: u32, comm: &str, start: u64, threads: u64, delayacct: bool) -> Vec<u8> {
        let last = if delayacct { 46 } else { 40 };
        stat_line_with(pid, comm, last, |field| match field {
            10 => "7".to_string(),      // minflt (field 10)
            11 | 13 => "0".to_string(), // cminflt, cmajflt: the children's faults
            12 => "3".to_string(),      // majflt (field 12)
            14 => "5".to_string(),      // utime
            15 => "2".to_string(),      // stime
            20 => threads.to_string(),  // num_threads
            22 => start.to_string(),    // starttime
            24 => "100".to_string(),    // rss
            42 => "11".to_string(),     // delayacct_blkio_ticks
            other => other.to_string(), // a harmless distinct value
        })
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
        let line = stat_line(4242, "worker (2)", 500, 4, true);
        let parsed: StatLine = parse_stat(&line).expect("stat");
        assert_eq!(parsed.pid, 4242);
        assert_eq!(parsed.comm, b"worker (2)", "spaces and parentheses survive");
        assert_eq!(parsed.state, b'S');
        // proc(5): 10 minflt, 11 cminflt, 12 majflt, 13 cmajflt. The two children's
        // counters are zero in this fixture, so reading either of them as this process's
        // faults does not produce 7 or 3.
        assert_eq!(parsed.minflt, 7, "field 10 is minflt");
        assert_eq!(parsed.majflt, 3, "field 12 is majflt");
        assert_eq!(parsed.utime, 5);
        assert_eq!(parsed.stime, 2);
        assert_eq!(parsed.num_threads, 4);
        assert_eq!(parsed.start_ticks, 500);
        assert_eq!(parsed.rss_pages, 100);
        assert_eq!(parsed.delayacct_blkio_ticks, Some(11));

        // Without delay accounting the file simply has no field 42; that is not an error.
        let parsed = parse_stat(&stat_line(7, "plain", 9, 1, false)).expect("stat");
        assert_eq!(parsed.delayacct_blkio_ticks, None);
        assert_eq!(parsed.start_ticks, 9);
    }

    #[test]
    fn every_stat_field_this_reader_reads_arrives_at_its_proc5_field_number() {
        // Field `n` carries the value `n`, so a reading can only equal its own field
        // number: every assertion below therefore says "this value came from field N" and
        // an index that slipped by even one reads a different number and fails by name.
        // This is the test K1 (`HOB-23`) asked for: the earlier helper put `7` and `3` at
        // the wrong indexes and asserted the same wrong table back to itself.
        let line = stat_line_with(4242, "by-number", 46, |field| field.to_string());
        let parsed = parse_stat(&line).expect("stat");
        assert_eq!(parsed.minflt, 10, "minflt is field 10, not 11 (cminflt)");
        assert_eq!(parsed.majflt, 12, "majflt is field 12, not 13 (cmajflt)");
        assert_eq!(parsed.utime, 14, "utime is field 14");
        assert_eq!(parsed.stime, 15, "stime is field 15");
        assert_eq!(parsed.num_threads, 20, "num_threads is field 20");
        assert_eq!(parsed.start_ticks, 22, "starttime is field 22");
        assert_eq!(parsed.rss_pages, 24, "rss is field 24");
        assert_eq!(
            parsed.delayacct_blkio_ticks,
            Some(42),
            "delayacct_blkio_ticks is field 42"
        );
        // The two fault counters are this process's own faults: field 11 and field 13 are
        // the faults its waited-for children had and must not be folded in (a value of 11
        // or 13 would prove one was).
        assert_eq!((parsed.minflt, parsed.majflt), (10, 12));
        assert_eq!(parsed.comm, b"by-number");
        assert_eq!(parsed.state, b'S');
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
        // Field 3 exists but field 10 does not: name the field the identity needed.
        assert_eq!(
            parse_stat(b"1 (a) S"),
            Err(IdentityError::StatShape { field: 10 }),
            "a line with only the state has no minflt field"
        );
        // Nine numbers after the state reach field 12 and stop before field 14.
        let short = b"1 (a) S 1 2 3 4 5 6 7 8 9";
        assert_eq!(
            parse_stat(short),
            Err(IdentityError::StatShape { field: 14 }),
            "the first field the line does not have is named"
        );
        let bad = stat_line_with(1, "a", 24, |field| {
            if field == 12 {
                "x".to_string()
            } else {
                field.to_string()
            }
        });
        assert_eq!(
            parse_stat(&bad),
            Err(IdentityError::Parse(ParseError::NotANumber {
                byte: b'x',
                offset: 0
            })),
            "field 12 (majflt) does not parse"
        );
        // A negative field is refused, never wrapped.
        let negative = stat_line_with(1, "a", 24, |field| {
            if field == 10 {
                "-1".to_string()
            } else {
                field.to_string()
            }
        });
        assert_eq!(
            parse_stat(&negative),
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
