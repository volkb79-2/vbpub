//! Identity: boot, daemon epoch, cgroup incarnation and process key (plan v4 §C.1).
//!
//! # Contracts
//!
//! - **A cgroup is identified by its incarnation id (`inc`), never by its path.** An
//!   incarnation is minted once per `(mount_id, inode)` pair and lives until the kernel
//!   cgroup behind it is gone. The path, the parent and the first/last observation are
//!   *labels*; a cgroup that is renamed keeps its incarnation and only its label changes,
//!   and a new inode appearing at the same path is a new incarnation.
//! - **Missing on one scan is not deletion.** [`IncarnationRegistry::end_round`] retires
//!   an incarnation only after two consecutive scans without it, so a cgroup lost by a
//!   slow or partly failed scan is not reported as dead and re-minted with a new id on
//!   the next tick.
//! - **A retired key that appears again is minted again.** Inode numbers are recycled by
//!   the kernel; identity across a gap in observation cannot be trusted, so the second
//!   appearance is a new `inc` (`first_seen` starts again). This is the same rule the
//!   sampler applies to a path whose inode changed (`topos/sampler/src/collect.rs:688`),
//!   expressed on the inode itself.
//! - **A process is `(boot, pid, start_ticks)`, never a pid.** A pid alone names a
//!   different process after a reboot or a pid reuse.
//! - **Labels expire with their incarnation**: a label can only be set on an active
//!   incarnation, so a late enrichment reply can never relabel a replacement cgroup.
//! - **The live registry is bounded.** At most [`DEFAULT_ACTIVE_MAX`] incarnations are held
//!   by default, and a registry can be built with a different configured maximum. Attempting
//!   to mint a new key at the bound is [`IdentityError::RegistryFull`]; a live entry is never
//!   evicted and an already-live key can still be refreshed (plan v4 §A.4).
//!
//! Incarnation ids start at 1; 0 is [`Incarnation::UNASSIGNED`] and is never minted, so
//! an all-zero identity field is detectable. Ids are never reused.

use core::fmt;
use std::collections::{BTreeMap, HashMap};

/// Largest accepted label or enrichment value in bytes (plan v4 §B.3: bounded UTF-8 ≤ 256 B).
pub const MAX_LABEL_LEN: usize = 256;

/// The default maximum number of live incarnations (plan v4 §A.4 `dict_active_max`).
///
/// The store's active dictionary re-uses this default.
pub const DEFAULT_ACTIVE_MAX: usize = 65_536;

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
    /// The dashed kernel form has a separator at the wrong position.
    BootIdSeparator {
        /// Character index.
        index: usize,
    },
    /// A `(mount id, inode)` key needs a non-zero mount id.
    MountIdZero,
    /// A zero inode names no cgroup.
    InodeZero,
    /// Pid 0 is not a process.
    PidZero,
    /// A label exceeds [`MAX_LABEL_LEN`] bytes.
    LabelTooLong {
        /// Observed byte length.
        len: usize,
    },
    /// A label holds a control character, which no fixture or dictionary line can carry.
    LabelControl {
        /// Byte index of the offending character.
        index: usize,
    },
    /// A label of a kind cannot be parsed from a stored code.
    UnknownLabelKind {
        /// The stored code.
        code: u8,
    },
    /// A label of a source cannot be parsed from a stored code.
    UnknownLabelSource {
        /// The stored code.
        code: u8,
    },
    /// Incarnation 0 is never minted and cannot be looked up.
    UnassignedIncarnation,
    /// The incarnation is not (or no longer) active.
    UnknownIncarnation(Incarnation),
    /// Minting a new key would exceed the registry's live-entry bound.
    RegistryFull {
        /// The configured maximum number of live entries.
        max: usize,
    },
    /// The id space of an `u64` incarnation counter is exhausted.
    IncarnationSpaceExhausted,
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
            IdentityError::MountIdZero => f.write_str("mount id 0 names no mount"),
            IdentityError::InodeZero => f.write_str("inode 0 names no cgroup"),
            IdentityError::PidZero => f.write_str("pid 0 names no process"),
            IdentityError::LabelTooLong { len } => {
                write!(f, "label is {len} bytes, the bound is {MAX_LABEL_LEN}")
            }
            IdentityError::LabelControl { index } => {
                write!(f, "label has a control character at byte {index}")
            }
            IdentityError::UnknownLabelKind { code } => write!(f, "unknown label kind code {code}"),
            IdentityError::UnknownLabelSource { code } => {
                write!(f, "unknown label source code {code}")
            }
            IdentityError::UnassignedIncarnation => f.write_str("incarnation 0 is unassigned"),
            IdentityError::UnknownIncarnation(id) => write!(f, "incarnation {id} is not active"),
            IdentityError::RegistryFull { max } => {
                write!(f, "incarnation registry is full at {max} live entries")
            }
            IdentityError::IncarnationSpaceExhausted => {
                f.write_str("incarnation id space exhausted")
            }
        }
    }
}

impl core::error::Error for IdentityError {}

/// A kernel boot id (`/proc/sys/kernel/random/boot_id`), 16 bytes.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
pub struct BootId([u8; 16]);

impl BootId {
    /// Parse the content of `/proc/sys/kernel/random/boot_id` (or the same id in the
    /// kernel's canonical lower-case form, with or without the four dashes).
    ///
    /// # Errors
    ///
    /// [`IdentityError::BootIdLength`], [`IdentityError::BootIdNonHex`] or
    /// [`IdentityError::BootIdSeparator`]: the id is fixed-width hex and nothing else.
    pub fn parse(text: &str) -> Result<BootId, IdentityError> {
        let text = text.trim();
        let separators: Option<&[usize]> = match text.len() {
            32 => None,
            36 => Some(&[8, 13, 18, 23]),
            len => return Err(IdentityError::BootIdLength { len }),
        };
        let mut out = [0u8; 16];
        let mut nibbles = 0usize;
        for (index, byte) in text.bytes().enumerate() {
            if let Some(positions) = separators {
                if positions.contains(&index) {
                    if byte != b'-' {
                        return Err(IdentityError::BootIdSeparator { index });
                    }
                    continue;
                }
                if byte == b'-' {
                    return Err(IdentityError::BootIdSeparator { index });
                }
            }
            let value = hex_value(byte).ok_or(IdentityError::BootIdNonHex { index })?;
            let slot = nibbles / 2;
            if nibbles.is_multiple_of(2) {
                out[slot] = value << 4;
            } else {
                out[slot] |= value;
            }
            nibbles += 1;
        }
        debug_assert_eq!(
            nibbles, 32,
            "the length check above guarantees 32 hex digits"
        );
        Ok(BootId(out))
    }

    /// The 16 bytes exactly as they travel in the frame and segment headers.
    #[must_use]
    pub const fn as_bytes(&self) -> &[u8; 16] {
        &self.0
    }

    /// Build from stored bytes; the wire type is opaque to the codec.
    #[must_use]
    pub const fn from_wire(bytes: [u8; 16]) -> BootId {
        BootId(bytes)
    }
}

fn hex_value(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        _ => None,
    }
}

impl fmt::Display for BootId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let mut rendered = [0u8; 36];
        let mut digit = 0;
        for (position, slot) in rendered.iter_mut().enumerate() {
            if matches!(position, 8 | 13 | 18 | 23) {
                *slot = b'-';
            } else {
                let byte = self.0[digit / 2];
                let nibble = if digit % 2 == 0 {
                    byte >> 4
                } else {
                    byte & 0x0f
                };
                *slot = if nibble < 10 {
                    b'0' + nibble
                } else {
                    b'a' + nibble - 10
                };
                digit += 1;
            }
        }
        f.write_str(&String::from_utf8_lossy(&rendered))
    }
}

/// The probe's epoch: a new value on every probe start (plan v4 §C.1).
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
pub struct Epoch(u64);

impl Epoch {
    /// The epoch the probe was started with.
    #[must_use]
    pub const fn new(value: u64) -> Epoch {
        Epoch(value)
    }

    /// The stored number.
    #[must_use]
    pub const fn value(self) -> u64 {
        self.0
    }
}

/// A daemon-minted cgroup incarnation id (plan v4 §C.1).
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
pub struct Incarnation(u64);

impl Incarnation {
    /// The id that is never minted and names no cgroup.
    pub const UNASSIGNED: Incarnation = Incarnation(0);

    /// Build from a stored number.
    #[must_use]
    pub const fn from_raw(value: u64) -> Incarnation {
        Incarnation(value)
    }

    /// The stored number.
    #[must_use]
    pub const fn raw(self) -> u64 {
        self.0
    }

    /// True for [`Incarnation::UNASSIGNED`].
    #[must_use]
    pub const fn is_unassigned(self) -> bool {
        self.0 == 0
    }
}

impl fmt::Display for Incarnation {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "inc:{}", self.0)
    }
}

/// The kernel identity of a cgroup: `(mount id, inode)`. A path is not part of it.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
pub struct CgroupKey {
    mount_id: u64,
    inode: u64,
}

impl CgroupKey {
    /// The key of a cgroup directory as read from `stat(2)` on cgroupfs.
    ///
    /// # Errors
    ///
    /// [`IdentityError::MountIdZero`] or [`IdentityError::InodeZero`]: a zero field is
    /// a failed read, not an identity.
    pub const fn new(mount_id: u64, inode: u64) -> Result<CgroupKey, IdentityError> {
        if mount_id == 0 {
            return Err(IdentityError::MountIdZero);
        }
        if inode == 0 {
            return Err(IdentityError::InodeZero);
        }
        Ok(CgroupKey { mount_id, inode })
    }

    /// The mount id of the cgroup hierarchy the directory lives on.
    #[must_use]
    pub const fn mount_id(self) -> u64 {
        self.mount_id
    }

    /// The inode of the cgroup directory.
    #[must_use]
    pub const fn inode(self) -> u64 {
        self.inode
    }
}

/// A process identity: `(boot, pid, start_ticks)` (plan v4 §C.1).
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
pub struct ProcessKey {
    boot: BootId,
    pid: u32,
    start_ticks: u64,
}

impl ProcessKey {
    /// The identity of a task as read from `/proc/<pid>/stat`.
    ///
    /// # Errors
    ///
    /// [`IdentityError::PidZero`]: pid 0 is the kernel's idle task list, never a process.
    pub const fn new(
        boot: BootId,
        pid: u32,
        start_ticks: u64,
    ) -> Result<ProcessKey, IdentityError> {
        if pid == 0 {
            return Err(IdentityError::PidZero);
        }
        Ok(ProcessKey {
            boot,
            pid,
            start_ticks,
        })
    }

    /// The boot this process was seen in.
    #[must_use]
    pub const fn boot(self) -> BootId {
        self.boot
    }

    /// The pid at the time of the observation.
    #[must_use]
    pub const fn pid(self) -> u32 {
        self.pid
    }

    /// The process start time in clock ticks since boot (field 22 of `/proc/<pid>/stat`).
    #[must_use]
    pub const fn start_ticks(self) -> u64 {
        self.start_ticks
    }
}

/// The closed set of enrichment label kinds (plan v4 §C.1).
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
pub enum LabelKind {
    /// A docker/containerd container name.
    DockerName,
    /// A container image reference.
    DockerImage,
    /// A systemd unit name.
    SystemdUnit,
    /// Where a cgroup came from (placement origin).
    Origin,
}

impl LabelKind {
    /// The stable stored code (append-only, as the quality codes).
    #[must_use]
    pub const fn code(self) -> u8 {
        match self {
            LabelKind::DockerName => 1,
            LabelKind::DockerImage => 2,
            LabelKind::SystemdUnit => 3,
            LabelKind::Origin => 4,
        }
    }

    /// Parse a stored code.
    ///
    /// # Errors
    ///
    /// [`IdentityError::UnknownLabelKind`]: the code is outside the table.
    pub const fn from_code(code: u8) -> Result<LabelKind, IdentityError> {
        match code {
            1 => Ok(LabelKind::DockerName),
            2 => Ok(LabelKind::DockerImage),
            3 => Ok(LabelKind::SystemdUnit),
            4 => Ok(LabelKind::Origin),
            other => Err(IdentityError::UnknownLabelKind { code: other }),
        }
    }

    /// The code's stable name.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            LabelKind::DockerName => "docker-name",
            LabelKind::DockerImage => "docker-image",
            LabelKind::SystemdUnit => "systemd-unit",
            LabelKind::Origin => "origin",
        }
    }
}

/// Which source observed a label (plan v4 §C.1: labels carry a source).
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash)]
pub enum LabelSource {
    /// Parsed from docker's own configuration.
    Docker,
    /// Read from systemd's unit state.
    Systemd,
    /// Stated by an operator or a placement record.
    Operator,
}

impl LabelSource {
    /// The stable stored code.
    #[must_use]
    pub const fn code(self) -> u8 {
        match self {
            LabelSource::Docker => 1,
            LabelSource::Systemd => 2,
            LabelSource::Operator => 3,
        }
    }

    /// Parse a stored code.
    ///
    /// # Errors
    ///
    /// [`IdentityError::UnknownLabelSource`]: the code is outside the table.
    pub const fn from_code(code: u8) -> Result<LabelSource, IdentityError> {
        match code {
            1 => Ok(LabelSource::Docker),
            2 => Ok(LabelSource::Systemd),
            3 => Ok(LabelSource::Operator),
            other => Err(IdentityError::UnknownLabelSource { code: other }),
        }
    }

    /// The code's stable name.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            LabelSource::Docker => "docker",
            LabelSource::Systemd => "systemd",
            LabelSource::Operator => "operator",
        }
    }
}

/// One bounded enrichment label bound to an incarnation.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Label {
    kind: LabelKind,
    value: String,
    source: LabelSource,
    observed_mono_ns: u64,
}

impl Label {
    /// A label value observed by `source` at `observed_mono_ns` (CLOCK_MONOTONIC).
    ///
    /// # Errors
    ///
    /// [`IdentityError::LabelTooLong`] or [`IdentityError::LabelControl`]: the value is
    /// bounded text, not arbitrary bytes.
    pub fn new(
        kind: LabelKind,
        value: &str,
        source: LabelSource,
        observed_mono_ns: u64,
    ) -> Result<Label, IdentityError> {
        validate_text(value)?;
        Ok(Label {
            kind,
            value: value.to_string(),
            source,
            observed_mono_ns,
        })
    }

    /// The label kind.
    #[must_use]
    pub const fn kind(&self) -> LabelKind {
        self.kind
    }

    /// The label value.
    #[must_use]
    pub fn value(&self) -> &str {
        &self.value
    }

    /// The source that observed it.
    #[must_use]
    pub const fn source(&self) -> LabelSource {
        self.source
    }

    /// When it was observed, on the monotonic clock.
    #[must_use]
    pub const fn observed_mono_ns(&self) -> u64 {
        self.observed_mono_ns
    }
}

fn validate_text(value: &str) -> Result<(), IdentityError> {
    if value.len() > MAX_LABEL_LEN {
        return Err(IdentityError::LabelTooLong { len: value.len() });
    }
    for (index, byte) in value.bytes().enumerate() {
        if byte < 0x20 || byte == 0x7f {
            return Err(IdentityError::LabelControl { index });
        }
    }
    Ok(())
}

/// A read-only view of one active incarnation.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct IncarnationSnapshot<'a> {
    /// The incarnation id.
    pub id: Incarnation,
    /// The kernel key it was minted for.
    pub key: CgroupKey,
    /// The path label of the last observation, if one was supplied.
    pub label: Option<&'a str>,
    /// The scan round of the first observation.
    pub first_round: u64,
    /// The scan round of the last observation.
    pub last_round: u64,
    /// Labels currently bound to this incarnation.
    pub labels: &'a [Label],
}

#[derive(Debug)]
struct Entry {
    id: Incarnation,
    key: CgroupKey,
    label: Option<String>,
    first_round: u64,
    last_round: u64,
    absent_rounds: u8,
    labels: Vec<Label>,
}

/// The incarnation registry: mints ids per `(mount id, inode)` and retires them after
/// two consecutive scans without the cgroup (plan v4 §C.1).
///
/// Live entries are keyed by incarnation id, with a compact reverse index from the kernel
/// [`CgroupKey`] to the id. Retirement removes both records, so neither storage nor
/// [`Self::end_round`] grows with ids that were minted and retired earlier. Point lookups
/// are O(log live), [`Self::active`] is O(1), and a round is
/// O(live + retired · log live), plus one O(`max_active`) reverse-index rebuild after a
/// round that retires (about 3 MiB at the 65,536 default); nothing is proportional to ids
/// ever minted.
///
/// The registry is bounded by a configurable maximum, [`DEFAULT_ACTIVE_MAX`] by default
/// (plan v4 §A.4's `dict_active_max`). At the bound, observing an already-live key still
/// refreshes it, while observing a new key returns [`IdentityError::RegistryFull`]. The
/// registry never evicts a live entry to make room and never mints past the bound.
#[derive(Debug)]
pub struct IncarnationRegistry {
    next: u64,
    round: u64,
    /// The last round whose absence bookkeeping ran; 0 while round 0 is open.
    ended_round: u64,
    max_active: usize,
    /// Live entries only, keyed by id so iteration and retirement order are stable.
    entries: BTreeMap<Incarnation, Entry>,
    by_key: HashMap<CgroupKey, Incarnation>,
    retired: u64,
}

impl Default for IncarnationRegistry {
    fn default() -> IncarnationRegistry {
        IncarnationRegistry::with_max(DEFAULT_ACTIVE_MAX)
    }
}

impl IncarnationRegistry {
    /// An empty registry; the first incarnation minted is 1.
    #[must_use]
    pub fn new() -> IncarnationRegistry {
        IncarnationRegistry::default()
    }

    /// An empty registry bounded to at most `max_active` live entries.
    ///
    /// A maximum of zero is a valid bound that refuses every mint; it never panics.
    #[must_use]
    pub fn with_max(max_active: usize) -> IncarnationRegistry {
        IncarnationRegistry {
            next: 0,
            round: 0,
            ended_round: 0,
            max_active,
            entries: BTreeMap::new(),
            by_key: HashMap::new(),
            retired: 0,
        }
    }

    /// The configured maximum number of live entries.
    #[must_use]
    pub const fn max_active(&self) -> usize {
        self.max_active
    }

    /// Start a new scan round. Observations belong to the round that is open.
    pub fn begin_round(&mut self) {
        self.round = self.round.saturating_add(1);
    }

    /// True when the open round's absences have already been counted.
    #[must_use]
    pub const fn round_is_ended(&self) -> bool {
        self.round == self.ended_round
    }

    /// The currently open scan round (0 before the first [`Self::begin_round`]).
    #[must_use]
    pub const fn round(&self) -> u64 {
        self.round
    }

    /// The incarnation of `key`, minting one on first observation and refreshing the
    /// observation round of an existing one.
    ///
    /// A `label` is the current path (or another display label); it replaces the stored
    /// label so a renamed cgroup keeps its incarnation with the new name.
    ///
    /// # Errors
    ///
    /// [`IdentityError::LabelTooLong`], [`IdentityError::LabelControl`] or
    /// [`IdentityError::RegistryFull`] when a new key would exceed the live bound, or
    /// [`IdentityError::IncarnationSpaceExhausted`].
    ///
    /// A refused observation (any error) mints nothing and consumes no id.
    pub fn observe(
        &mut self,
        key: CgroupKey,
        label: Option<&str>,
    ) -> Result<Incarnation, IdentityError> {
        if let Some(label) = label {
            validate_text(label)?;
        }
        if let Some(id) = self.by_key.get(&key) {
            let id = *id;
            if let Some(entry) = self.entries.get_mut(&id) {
                entry.last_round = self.round;
                entry.absent_rounds = 0;
                if let Some(label) = label {
                    entry.label = Some(label.to_string());
                }
                return Ok(id);
            }
            // A key indexed to an entry that is not live is an internal inconsistency;
            // drop the index rather than returning a retired incarnation.
            self.by_key.remove(&key);
        }
        if self.entries.len() >= self.max_active {
            return Err(IdentityError::RegistryFull {
                max: self.max_active,
            });
        }
        let next = self
            .next
            .checked_add(1)
            .ok_or(IdentityError::IncarnationSpaceExhausted)?;
        self.next = next;
        let id = Incarnation::from_raw(next);
        let entry = Entry {
            id,
            key,
            label: label.map(str::to_string),
            first_round: self.round,
            last_round: self.round,
            absent_rounds: 0,
            labels: Vec::new(),
        };
        self.entries.insert(id, entry);
        self.by_key.insert(key, id);
        Ok(id)
    }

    /// Close the open scan round: an incarnation not observed in it gets one more absent
    /// round, and twice absent is retired. Returns the incarnations retired by this call.
    ///
    /// Closing the same round twice is a no-op: a second call would otherwise count one
    /// absent scan twice and retire a cgroup that was missing for only one scan.
    ///
    /// The pass walks the live entries only (the `BTreeMap` is ordered by id, so the
    /// returned ids remain in mint order); retired entries are removed from both indexes.
    /// It is O(live + retired · log live) per call, plus one O(`max_active`) reverse-index
    /// allocation and rebuild after a round that retires (about 3 MiB at the 65,536
    /// default); nothing is proportional to ids ever minted.
    pub fn end_round(&mut self) -> Vec<Incarnation> {
        if self.round_is_ended() {
            return Vec::new();
        }
        self.ended_round = self.round;
        let mut retired = Vec::new();
        for entry in self.entries.values_mut() {
            if entry.last_round == self.round {
                continue;
            }
            entry.absent_rounds = entry.absent_rounds.saturating_add(1);
            if entry.absent_rounds >= 2 {
                // The key has to be read before the entry is removed, or the reverse
                // index would keep pointing at an incarnation that no longer exists.
                retired.push((entry.id, entry.key));
            }
        }
        for (id, key) in &retired {
            self.entries.remove(id);
            self.by_key.remove(key);
        }
        if !retired.is_empty() {
            // The long-churn capacity assertion requires a tombstone-free index whose
            // capacity is fixed at the configured bound; this costs O(max_active).
            let mut compact = HashMap::new();
            if compact.try_reserve(self.max_active).is_err()
                && compact.try_reserve(self.entries.len()).is_err()
            {
                self.by_key = compact;
            } else {
                compact.extend(self.entries.values().map(|entry| (entry.key, entry.id)));
                self.by_key = compact;
            }
        }
        self.retired = self.retired.saturating_add(retired.len() as u64);
        retired.into_iter().map(|(id, _)| id).collect()
    }

    /// The active incarnation of `key`, if it is currently observed.
    #[must_use]
    pub fn incarnation_of(&self, key: &CgroupKey) -> Option<Incarnation> {
        self.by_key.get(key).copied()
    }

    /// A view of an active incarnation.
    #[must_use]
    pub fn snapshot(&self, id: Incarnation) -> Option<IncarnationSnapshot<'_>> {
        let entry = self.entry(id)?;
        Some(IncarnationSnapshot {
            id: entry.id,
            key: entry.key,
            label: entry.label.as_deref(),
            first_round: entry.first_round,
            last_round: entry.last_round,
            labels: &entry.labels,
        })
    }

    /// True while the incarnation is active (not retired).
    #[must_use]
    pub fn is_active(&self, id: Incarnation) -> bool {
        self.entry(id).is_some()
    }

    fn entry(&self, id: Incarnation) -> Option<&Entry> {
        if id.is_unassigned() {
            return None;
        }
        self.entries.get(&id)
    }

    /// Bind a label to an active incarnation, replacing a label of the same kind.
    ///
    /// # Errors
    ///
    /// [`IdentityError::UnassignedIncarnation`] or [`IdentityError::UnknownIncarnation`]:
    /// a label is never attached to a retired incarnation (it would relabel a
    /// replacement cgroup using stale enrichment).
    pub fn set_label(&mut self, id: Incarnation, label: Label) -> Result<(), IdentityError> {
        if id.is_unassigned() {
            return Err(IdentityError::UnassignedIncarnation);
        }
        let entry = self
            .entries
            .get_mut(&id)
            .ok_or(IdentityError::UnknownIncarnation(id))?;
        match entry
            .labels
            .iter_mut()
            .find(|existing| existing.kind == label.kind)
        {
            Some(existing) => *existing = label,
            None => entry.labels.push(label),
        }
        Ok(())
    }

    /// The labels of an active incarnation.
    #[must_use]
    pub fn labels(&self, id: Incarnation) -> Option<&[Label]> {
        self.entry(id).map(|entry| entry.labels.as_slice())
    }

    /// The number of active incarnations.
    #[must_use]
    pub fn active(&self) -> usize {
        self.entries.len()
    }

    /// The number of incarnations retired so far.
    #[must_use]
    pub const fn retired(&self) -> u64 {
        self.retired
    }

    /// The ids of the active incarnations, in mint order.
    #[must_use]
    pub fn active_ids(&self) -> Vec<Incarnation> {
        self.entries.keys().copied().collect()
    }

    /// The lengths and reverse-index capacity of the collections that grow with live
    /// incarnations.
    #[cfg(test)]
    fn storage_len(&self) -> (usize, usize, usize) {
        (
            self.entries.len(),
            self.by_key.len(),
            self.by_key.capacity(),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::{
        validate_text, BootId, CgroupKey, Epoch, IdentityError, Incarnation, IncarnationRegistry,
        Label, LabelKind, LabelSource, ProcessKey, DEFAULT_ACTIVE_MAX,
    };
    use std::collections::VecDeque;

    const BOOT: &str = "00112233-4455-6677-8899-aabbccddeeff";

    fn key(inode: u64) -> CgroupKey {
        CgroupKey::new(41, inode).expect("test key")
    }

    #[test]
    fn boot_id_parses_both_kernel_spellings_and_refuses_the_rest() {
        let dashed = BootId::parse(BOOT).expect("dashed form");
        let bare = BootId::parse("00112233445566778899aabbccddeeff").expect("bare form");
        assert_eq!(dashed, bare);
        assert_eq!(dashed.as_bytes()[0], 0x00);
        assert_eq!(dashed.as_bytes()[15], 0xff);
        assert_eq!(dashed.to_string(), BOOT);
        assert_eq!(dashed, BootId::from_wire(*dashed.as_bytes()));

        assert_eq!(
            BootId::parse(&format!("{BOOT}\n")),
            Ok(dashed),
            "the file ends with a newline"
        );
        assert_eq!(
            BootId::parse(" 00112233445566778899aabbccddeeff\t"),
            Ok(bare)
        );
        assert_eq!(
            BootId::parse("0011"),
            Err(IdentityError::BootIdLength { len: 4 })
        );
        assert_eq!(
            BootId::parse(""),
            Err(IdentityError::BootIdLength { len: 0 })
        );
        assert_eq!(
            BootId::parse("00112233-4455-6677-8899-aabbccddeefg"),
            Err(IdentityError::BootIdNonHex { index: 35 })
        );
        assert_eq!(
            BootId::parse("A0112233-4455-6677-8899-aabbccddeeff"),
            Err(IdentityError::BootIdNonHex { index: 0 }),
            "hex digits are lower case; one id has one spelling"
        );
        assert_eq!(
            BootId::parse("0011223-34455-6677-8899-aabbccddeeff"),
            Err(IdentityError::BootIdSeparator { index: 7 })
        );
        assert_eq!(
            BootId::parse("001122334-455-6677-8899-aabbccddeeff"),
            Err(IdentityError::BootIdSeparator { index: 8 }),
            "a dash at the wrong position is a misplaced separator"
        );
        assert_eq!(
            BootId::parse("001122334455-6677-8899-aabbccddeeff"),
            Err(IdentityError::BootIdLength { len: 35 }),
            "a dashed id is 36 characters, not 35"
        );
        assert_eq!(
            BootId::parse("00_12233-4455-6677-8899-aabbccddeeff"),
            Err(IdentityError::BootIdNonHex { index: 2 }),
            "a non-hex character inside the dashed form is a non-hex error"
        );
    }

    #[test]
    fn keys_refuse_zero_fields_and_keep_their_parts() {
        assert_eq!(CgroupKey::new(0, 5), Err(IdentityError::MountIdZero));
        assert_eq!(CgroupKey::new(41, 0), Err(IdentityError::InodeZero));
        let parsed = key(7);
        assert_eq!(parsed.mount_id(), 41);
        assert_eq!(parsed.inode(), 7);
        assert_ne!(parsed, key(8), "the inode is the identity");
        assert_ne!(parsed, CgroupKey::new(42, 7).expect("other mount"));

        let boot = BootId::parse(BOOT).expect("boot");
        assert_eq!(ProcessKey::new(boot, 0, 100), Err(IdentityError::PidZero));
        let process = ProcessKey::new(boot, 1234, 100).expect("process");
        assert_eq!(process.boot(), boot);
        assert_eq!(process.pid(), 1234);
        assert_eq!(process.start_ticks(), 100);
        assert_ne!(
            process,
            ProcessKey::new(boot, 1234, 101).expect("same pid, new start"),
            "a bare pid is not an identity"
        );
    }

    #[test]
    fn epoch_and_incarnation_are_plain_values_with_an_unassigned_zero() {
        assert_eq!(Epoch::new(9).value(), 9);
        assert_eq!(Incarnation::UNASSIGNED.raw(), 0);
        assert!(Incarnation::UNASSIGNED.is_unassigned());
        assert_eq!(Incarnation::from_raw(3).to_string(), "inc:3");
        assert!(!Incarnation::from_raw(1).is_unassigned());
    }

    #[test]
    fn label_kind_and_source_codes_round_trip_and_refuse_the_rest() {
        for kind in [
            LabelKind::DockerName,
            LabelKind::DockerImage,
            LabelKind::SystemdUnit,
            LabelKind::Origin,
        ] {
            assert_eq!(LabelKind::from_code(kind.code()), Ok(kind));
            let name = kind.name();
            assert!(
                name.bytes()
                    .all(|byte| byte.is_ascii_lowercase() || byte == b'-'),
                "names are lower-case kebab case: {name}"
            );
        }
        for code in 0u8..=u8::MAX {
            if !(1..=4).contains(&code) {
                assert_eq!(
                    LabelKind::from_code(code),
                    Err(IdentityError::UnknownLabelKind { code })
                );
            }
        }
        for source in [
            LabelSource::Docker,
            LabelSource::Systemd,
            LabelSource::Operator,
        ] {
            assert_eq!(LabelSource::from_code(source.code()), Ok(source));
        }
        for code in 0u8..=u8::MAX {
            if !(1..=3).contains(&code) {
                assert_eq!(
                    LabelSource::from_code(code),
                    Err(IdentityError::UnknownLabelSource { code })
                );
            }
        }
    }

    #[test]
    fn labels_are_bounded_utf8_without_control_characters() {
        assert!(validate_text("cg:/system.slice").is_ok());
        assert!(validate_text(&"a".repeat(256)).is_ok());
        assert_eq!(
            validate_text(&"a".repeat(257)),
            Err(IdentityError::LabelTooLong { len: 257 })
        );
        assert_eq!(
            validate_text("two\nlines"),
            Err(IdentityError::LabelControl { index: 3 })
        );
        assert_eq!(
            validate_text("nul\0byte"),
            Err(IdentityError::LabelControl { index: 3 })
        );
        assert_eq!(
            validate_text("del\u{7f}byte"),
            Err(IdentityError::LabelControl { index: 3 })
        );
        let label = Label::new(
            LabelKind::SystemdUnit,
            "docker.service",
            LabelSource::Systemd,
            11,
        )
        .expect("valid label");
        assert_eq!(label.kind(), LabelKind::SystemdUnit);
        assert_eq!(label.value(), "docker.service");
        assert_eq!(label.source(), LabelSource::Systemd);
        assert_eq!(label.observed_mono_ns(), 11);
    }

    #[test]
    fn an_unobserved_key_is_minted_once_and_ids_start_at_one() {
        let mut registry = IncarnationRegistry::new();
        registry.begin_round();
        let first = registry
            .observe(key(1), Some("/system.slice"))
            .expect("mint");
        assert_eq!(first.raw(), 1, "id 0 is never minted");
        assert_eq!(registry.observe(key(1), Some("/system.slice")), Ok(first));
        assert_eq!(registry.incarnation_of(&key(1)), Some(first));
        registry.end_round();
        assert_eq!(registry.active(), 1);
        assert_eq!(registry.round(), 1);
        assert_eq!(registry.active_ids(), vec![first]);
    }

    #[test]
    fn a_new_inode_at_the_same_path_is_a_new_incarnation_and_a_rename_is_not() {
        let mut registry = IncarnationRegistry::new();
        registry.begin_round();
        let first = registry
            .observe(key(11), Some("/docker/abc"))
            .expect("mint");
        registry.end_round();

        registry.begin_round();
        let renamed = registry
            .observe(key(11), Some("/docker/xyz"))
            .expect("same inode");
        assert_eq!(renamed, first, "a rename keeps the incarnation");
        assert_eq!(
            registry
                .snapshot(first)
                .and_then(|snapshot| snapshot.label.map(str::to_string)),
            Some("/docker/xyz".to_string()),
            "the path is only a label"
        );

        let replacement = registry
            .observe(key(12), Some("/docker/abc"))
            .expect("new inode");
        assert_ne!(
            replacement, first,
            "the same path with a new inode is another cgroup"
        );
        assert_eq!(registry.active(), 2);
        assert_eq!(
            registry
                .snapshot(replacement)
                .map(|snapshot| snapshot.first_round),
            Some(2)
        );
    }

    #[test]
    fn one_absent_scan_is_not_deletion_and_two_are() {
        let mut registry = IncarnationRegistry::new();
        registry.begin_round();
        let kept = registry.observe(key(1), None).expect("mint");
        let lost = registry.observe(key(2), None).expect("mint");
        registry.end_round();
        assert!(
            registry.end_round().is_empty(),
            "no round opened, nothing retired"
        );

        registry.begin_round(); // round 2: both keys observed
        registry.observe(key(1), None).expect("observed");
        registry.observe(key(2), None).expect("observed");
        registry.end_round();

        registry.begin_round(); // round 3: neither observed
        let retired = registry.end_round();
        assert!(retired.is_empty(), "round 3 is the first absent scan");
        assert!(registry.is_active(kept));
        registry.begin_round(); // round 4: still neither
        let retired = registry.end_round();
        assert_eq!(
            retired.len(),
            2,
            "two consecutive absent scans retire an incarnation"
        );
        assert!(!registry.is_active(kept));
        assert!(!registry.is_active(lost));
        assert_eq!(registry.retired(), 2);
        assert_eq!(registry.active(), 0);
        assert_eq!(registry.incarnation_of(&key(1)), None);

        registry.begin_round(); // round 5: key 1 observed again
        registry.observe(key(1), None).expect("observed");
        registry.end_round();
        assert!(
            !registry.is_active(kept),
            "a retired incarnation comes back with a new id"
        );

        // Reappearance after retirement mints a fresh id.
        registry.begin_round();
        registry.observe(key(2), None).expect("mint");
        let reminted = registry.incarnation_of(&key(2)).expect("active");
        assert_ne!(
            reminted, lost,
            "a retired key that reappears gets a new incarnation"
        );
    }

    #[test]
    fn a_cgroup_missing_one_scan_survives_it() {
        let mut registry = IncarnationRegistry::new();
        registry.begin_round();
        let id = registry.observe(key(1), None).expect("mint");
        registry.end_round();

        registry.begin_round(); // scan without the cgroup (partial read, churn)
        assert!(
            registry.end_round().is_empty(),
            "one absent scan is not deletion"
        );
        assert!(registry.is_active(id));

        registry.begin_round();
        assert_eq!(
            registry.observe(key(1), None),
            Ok(id),
            "the same id comes back"
        );
        assert!(registry.end_round().is_empty());
        assert_eq!(registry.retired(), 0);
    }

    #[test]
    fn retired_ids_are_never_reused() {
        let mut registry = IncarnationRegistry::new();
        registry.begin_round();
        let a = registry.observe(key(1), None).expect("mint");
        registry.end_round();
        registry.begin_round();
        registry.end_round();
        registry.begin_round();
        assert_eq!(registry.end_round(), vec![a]);
        registry.begin_round();
        let b = registry.observe(key(2), None).expect("mint");
        assert_eq!(b.raw(), 2, "ids increase, a retired id is never reused");
        registry.end_round();
        assert!(registry.is_active(b));
        assert!(!registry.is_active(a));
        assert!(registry.snapshot(a).is_none());
    }

    #[test]
    fn a_huge_registry_bound_does_not_panic_when_retiring() {
        let mut registry = IncarnationRegistry::with_max(usize::MAX);
        registry.begin_round();
        let id = registry.observe(key(1), None).expect("mint");
        registry.end_round();
        registry.begin_round();
        registry.end_round();
        registry.begin_round();
        assert_eq!(registry.end_round(), vec![id]);
    }

    #[test]
    fn the_registry_bound_refuses_new_keys_and_reuses_freed_capacity() {
        let mut registry = IncarnationRegistry::with_max(2);
        assert_eq!(registry.max_active(), 2);
        registry.begin_round();
        let first = registry.observe(key(1), Some("/first")).expect("mint");
        let second = registry.observe(key(2), Some("/second")).expect("mint");

        assert_eq!(
            registry.observe(key(3), Some("/refused")),
            Err(IdentityError::RegistryFull { max: 2 }),
            "a new key is refused at the bound"
        );
        assert_eq!(registry.incarnation_of(&key(3)), None);
        assert_eq!(registry.active(), 2, "the refusal evicts nothing");
        assert!(registry.is_active(first) && registry.is_active(second));

        assert_eq!(
            registry.observe(key(1), Some("/renamed")),
            Ok(first),
            "re-observing a live key still succeeds at the bound"
        );
        assert_eq!(
            registry.snapshot(first).and_then(|view| view.label),
            Some("/renamed")
        );
        registry.end_round();

        registry.begin_round();
        registry.observe(key(1), None).expect("keep first");
        registry.end_round();
        assert!(
            registry.is_active(second),
            "one absent round does not retire the second key"
        );

        registry.begin_round();
        registry.observe(key(1), None).expect("keep first");
        assert_eq!(
            registry.observe(key(3), None),
            Err(IdentityError::RegistryFull { max: 2 }),
            "retirement has not run yet, so the bound is still full"
        );
        assert_eq!(registry.incarnation_of(&key(3)), None);
        assert_eq!(
            registry.end_round(),
            vec![second],
            "the second key is retired after two absent rounds"
        );

        registry.begin_round();
        registry.observe(key(1), None).expect("keep first");
        let third = registry
            .observe(key(3), Some("/third"))
            .expect("retirement freed one slot");
        assert_eq!(
            third.raw(),
            second.raw() + 1,
            "the refused mint did not consume an id"
        );
        assert_ne!(third, second, "freed capacity mints a fresh id");
        assert_eq!(registry.active(), 2);

        let mut zero = IncarnationRegistry::with_max(0);
        zero.begin_round();
        assert_eq!(
            zero.observe(key(4), None),
            Err(IdentityError::RegistryFull { max: 0 }),
            "a zero bound refuses instead of panicking"
        );
        assert_eq!(zero.incarnation_of(&key(4)), None);
        assert_eq!(zero.active(), 0);

        assert_eq!(
            IncarnationRegistry::new().max_active(),
            DEFAULT_ACTIVE_MAX,
            "the model owns the one default bound"
        );
    }

    #[test]
    fn bounded_registry_stays_live_sized_through_extended_churn() {
        // Scan rounds are the fake clock. Each round creates ten new cgroups and does not
        // observe the ten from two rounds ago, so those ten retire at this round's end.
        const ROUNDS: u64 = 100_000;
        const GROUPS_PER_ROUND: u64 = 10;
        const MAX_ACTIVE: usize = 30;
        const STORAGE_BOUND: usize = 2 * MAX_ACTIVE;

        let mut registry = IncarnationRegistry::with_max(MAX_ACTIVE);
        let mut due: VecDeque<Vec<Incarnation>> = VecDeque::new();
        let mut previous = Incarnation::UNASSIGNED;
        let mut capacity_after_round_3 = None;
        for round in 0..ROUNDS {
            registry.begin_round();
            let mut minted = Vec::new();
            for slot in 0..GROUPS_PER_ROUND {
                let inode = round * GROUPS_PER_ROUND + slot + 1;
                let id = registry
                    .observe(key(inode), None)
                    .expect("ten new cgroups fit the bound");
                assert!(
                    id.raw() > previous.raw(),
                    "incarnation ids stay monotonically increasing"
                );
                previous = id;
                minted.push(id);
            }
            let (entries, reverse, capacity) = registry.storage_len();
            assert_eq!(entries, reverse, "both live indexes retire together");
            assert!(entries <= MAX_ACTIVE);
            assert!(entries + reverse <= STORAGE_BOUND);
            if let Some(bound) = capacity_after_round_3 {
                assert!(
                    capacity <= bound,
                    "capacity grows after round 3: round={round}, capacity={capacity}, bound={bound}"
                );
            }

            due.push_back(minted);
            let retired = registry.end_round();
            if due.len() > 2 {
                assert_eq!(
                    retired,
                    due.pop_front().expect("a two-round-old batch is due"),
                    "end_round returns exactly the cgroups that became two-rounds absent"
                );
            } else {
                assert!(retired.is_empty(), "the first two rounds retire nothing");
            }
            let (entries, reverse, capacity) = registry.storage_len();
            assert_eq!(entries, reverse, "retirement removes both index records");
            assert!(entries <= MAX_ACTIVE);
            assert!(entries + reverse <= STORAGE_BOUND);
            if round == 3 {
                capacity_after_round_3 = Some(capacity);
            } else if let Some(bound) = capacity_after_round_3 {
                assert!(
                    capacity <= bound,
                    "reverse-index capacity grows after round 3: round={round}, capacity={capacity}, bound={bound}"
                );
            }
        }
        assert_eq!(
            registry.active(),
            20,
            "only the current and immediately preceding ten-cgroup batches remain"
        );
    }

    #[test]
    fn labels_bind_to_active_incarnations_only_and_replace_by_kind() {
        let mut registry = IncarnationRegistry::new();
        registry.begin_round();
        let id = registry.observe(key(1), Some("/docker/abc")).expect("mint");
        registry.end_round();

        registry.begin_round();
        registry.observe(key(1), None).expect("observed");
        registry
            .set_label(
                id,
                Label::new(LabelKind::DockerName, "web-1", LabelSource::Docker, 5).expect("label"),
            )
            .expect("active");
        registry
            .set_label(
                id,
                Label::new(LabelKind::DockerName, "web-2", LabelSource::Docker, 7).expect("label"),
            )
            .expect("replace by kind");
        registry
            .set_label(
                id,
                Label::new(LabelKind::DockerImage, "alpine", LabelSource::Docker, 7)
                    .expect("label"),
            )
            .expect("other kind");
        let labels = registry.labels(id).expect("active").to_vec();
        assert_eq!(labels.len(), 2);
        assert_eq!(labels[0].value(), "web-2");
        assert_eq!(labels[0].observed_mono_ns(), 7);
        assert_eq!(labels[1].kind(), LabelKind::DockerImage);
        registry.end_round();

        // A healthy scan keeps the labels.
        registry.begin_round();
        registry.observe(key(1), None).expect("observed");
        registry.end_round();
        assert_eq!(registry.retired(), 0);
        assert_eq!(registry.labels(id).map(<[Label]>::len), Some(2));

        // Retire it, then a late enrichment can not relabel anything.
        registry.begin_round();
        assert!(
            registry.end_round().is_empty(),
            "one absent scan is not deletion"
        );
        assert!(registry.is_active(id));
        assert_eq!(
            registry.end_round(),
            Vec::new(),
            "closing an ended round counts nothing"
        );
        assert!(
            registry.is_active(id),
            "the repeated close did not add a second absence"
        );

        registry.begin_round();
        assert_eq!(
            registry.end_round(),
            vec![id],
            "two absent scans retired it"
        );
        assert_eq!(
            registry.set_label(
                id,
                Label::new(LabelKind::DockerName, "stale", LabelSource::Docker, 9).expect("label"),
            ),
            Err(IdentityError::UnknownIncarnation(id))
        );
        assert_eq!(
            registry.labels(id),
            None,
            "labels expire with the incarnation"
        );
        assert_eq!(
            registry.set_label(
                Incarnation::UNASSIGNED,
                Label::new(LabelKind::Origin, "x", LabelSource::Operator, 1).expect("label"),
            ),
            Err(IdentityError::UnassignedIncarnation)
        );
    }

    #[test]
    fn observe_refuses_unbounded_labels_and_the_registry_keeps_its_counters() {
        let mut registry = IncarnationRegistry::new();
        registry.begin_round();
        assert_eq!(
            registry.observe(key(1), Some(&"x".repeat(257))),
            Err(IdentityError::LabelTooLong { len: 257 })
        );
        assert_eq!(
            registry.observe(key(1), Some("bad\nlabel")),
            Err(IdentityError::LabelControl { index: 3 })
        );
        assert_eq!(registry.active(), 0, "a refused observation mints nothing");
        registry.end_round();
        assert_eq!(registry.round(), 1);
    }

    #[test]
    fn every_identity_refusal_renders_its_fact() {
        let cases: Vec<(IdentityError, &str)> = vec![
            (
                IdentityError::BootIdLength { len: 3 },
                "boot id has 3 characters",
            ),
            (
                IdentityError::BootIdNonHex { index: 7 },
                "character 7 is not a lower-case hex digit",
            ),
            (
                IdentityError::BootIdSeparator { index: 9 },
                "separator 9 is misplaced",
            ),
            (IdentityError::MountIdZero, "mount id 0 names no mount"),
            (IdentityError::InodeZero, "inode 0 names no cgroup"),
            (IdentityError::PidZero, "pid 0 names no process"),
            (
                IdentityError::LabelTooLong { len: 300 },
                "label is 300 bytes",
            ),
            (
                IdentityError::LabelControl { index: 2 },
                "control character at byte 2",
            ),
            (
                IdentityError::UnknownLabelKind { code: 9 },
                "unknown label kind code 9",
            ),
            (
                IdentityError::UnknownLabelSource { code: 9 },
                "unknown label source code 9",
            ),
            (
                IdentityError::UnassignedIncarnation,
                "incarnation 0 is unassigned",
            ),
            (
                IdentityError::UnknownIncarnation(Incarnation::from_raw(5)),
                "incarnation inc:5 is not active",
            ),
            (
                IdentityError::RegistryFull { max: 2 },
                "incarnation registry is full at 2 live entries",
            ),
            (
                IdentityError::IncarnationSpaceExhausted,
                "incarnation id space exhausted",
            ),
        ];
        let mut rendered = Vec::new();
        for (error, expected) in cases {
            let text = error.to_string();
            assert!(
                text.contains(expected),
                "{error:?} rendered {text:?}, missing {expected:?}"
            );
            assert!(
                !rendered.contains(&text),
                "two refusals render the same text: {text}"
            );
            rendered.push(text);
        }
        // Every label source names itself, and every kind names itself in kebab case.
        for source in [
            LabelSource::Docker,
            LabelSource::Systemd,
            LabelSource::Operator,
        ] {
            assert!(
                source.name().bytes().all(|byte| byte.is_ascii_lowercase()),
                "{}",
                source.name()
            );
        }
        assert_eq!(LabelSource::Systemd.name(), "systemd");
        assert_eq!(LabelKind::DockerImage.name(), "docker-image");
        assert_eq!(LabelKind::Origin.name(), "origin");
    }

    #[test]
    fn an_unassigned_incarnation_has_no_entry() {
        let mut registry = IncarnationRegistry::new();
        assert!(registry.snapshot(Incarnation::UNASSIGNED).is_none());
        assert!(!registry.is_active(Incarnation::UNASSIGNED));
        assert_eq!(registry.labels(Incarnation::UNASSIGNED), None);
        assert_eq!(
            registry.set_label(
                Incarnation::UNASSIGNED,
                Label::new(LabelKind::Origin, "x", LabelSource::Operator, 1).expect("label"),
            ),
            Err(IdentityError::UnassignedIncarnation)
        );
    }

    #[test]
    fn an_index_pointing_at_a_retired_entry_is_repaired_by_minting_a_fresh_incarnation() {
        // The reverse index and the live entry map are one invariant. This test breaks it
        // on purpose: a key left indexed to a retired entry must not resurrect that id.
        let key = CgroupKey::new(31, 1).expect("key");
        let mut registry = IncarnationRegistry::new();
        registry.begin_round();
        let first = registry.observe(key, Some("/gone")).expect("observe");
        registry.end_round();
        for _ in 0..2 {
            registry.begin_round();
            registry.end_round();
        }
        assert!(
            !registry.is_active(first),
            "twice absent retires the incarnation"
        );
        assert!(
            registry.incarnation_of(&key).is_none(),
            "retirement drops the index"
        );
        registry.by_key.insert(key, first);
        registry.begin_round();
        let fresh = registry.observe(key, Some("/back")).expect("observe");
        assert_ne!(fresh, first, "a repaired index mints a new incarnation");
        assert!(registry.is_active(fresh));
        assert_eq!(
            registry.snapshot(fresh).expect("snapshot").label,
            Some("/back")
        );
        assert_eq!(registry.incarnation_of(&key), Some(fresh));
    }

    #[test]
    fn error_text_names_the_refusal() {
        assert_eq!(
            IdentityError::BootIdLength { len: 3 }.to_string(),
            "boot id has 3 characters, expected 32 or 36"
        );
        assert_eq!(
            IdentityError::InodeZero.to_string(),
            "inode 0 names no cgroup"
        );
        assert_eq!(
            IdentityError::LabelTooLong { len: 300 }.to_string(),
            "label is 300 bytes, the bound is 256"
        );
        assert_eq!(
            IdentityError::UnknownIncarnation(Incarnation::from_raw(4)).to_string(),
            "incarnation inc:4 is not active"
        );
    }
}
