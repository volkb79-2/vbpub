//! The journal: `state_dir/events.jsonl`, one object per line, rotated at 1 MiB
//! (design §2 D6, §3, §4).
//!
//! What it is: thrashguard's **own** crash record and its reader's source for the
//! `events` verb. What it is not: the consumer path. Consumers read `hostobs`
//! (§4, D15/§6c); P4 emits there. `/run` is tmpfs, so the file is rotated rather
//! than grown, in the psi-guard shape.
//!
//! ## P1's rows are decisions, and say so
//!
//! Nothing can be frozen in P1, so no interval exists: every row carries
//! `seconds: 0`, `mono_end_ns` equal to the decision instant, and an explicit
//! `simulated: true`. A reader in P1 therefore cannot mistake a would-be park
//! for a frozen interval — the alternative (writing `seconds: 0` with no marker)
//! is a row that reads as a real, instantly-thawed freeze.
//!
//! ## Two facts the file cannot tell you, and the ring can
//!
//! `seq` is monotonic per boot, never reused, and survives a rotation. The
//! in-memory ring keeps the most recent rows so `events {since_seq}` answers
//! without re-reading the file each request; when a client asks for a sequence
//! number older than the ring, the reply says `truncated: true` rather than
//! pretending the missing rows never existed. The file itself is the durable
//! record for a human, and it is never rewritten by a reader.

use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

use crate::json::Value;
use crate::EVENT_VERSION;

/// The journal's file name, inside `state_dir`.
pub const JOURNAL_FILE: &str = "events.jsonl";
/// The rotated name (one generation is kept, as psi-guard does).
pub const ROTATED_FILE: &str = "events.jsonl.1";
/// Rotate before the file is larger than this.
pub const ROTATE_BYTES: u64 = 1024 * 1024;
/// Maximum accepted size for one journal generation, including one final row.
pub const MAX_JOURNAL_FILE_BYTES: u64 = ROTATE_BYTES + crate::json::MAX_DOCUMENT_BYTES as u64;
/// Leave room for the `events` reply envelope when an individual row is paged.
const MAX_JOURNAL_TEXT_BYTES: usize = crate::json::MAX_DOCUMENT_BYTES - 1024;
const TRUNCATED_MARKER: &str = "...[truncated]";
const MAX_BOOT_ID_BYTES: usize = 128;
const MAX_SCOPE_FIELD_BYTES: usize = 256;
const MAX_DETAIL_BYTES: usize = 1024;
const MAX_SHADOWED_ROWS: usize = 8;
const MAX_SHADOWED_FIELD_BYTES: usize = 256;
/// How many recent rows the reader verb answers from memory.
pub const RECENT_ROWS: usize = 1024;
/// Where the host's boot id is read from in production.
pub const BOOT_ID_PATH: &str = "/proc/sys/kernel/random/boot_id";

/// One journal row, before it is stamped with `boot_id` and `seq`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Event {
    /// The row kind: `would-freeze`, `would-thaw`, `registered`, `expired`,
    /// `deregistered`, `unknown-pressure`, `registration-refused`.
    pub event: &'static str,
    /// The unit name the registrant supplied (`None` for a guard-level row).
    pub unit: Option<String>,
    /// The scope name, when a scope is known.
    pub cgroup: Option<String>,
    /// The scope's inode, so a recycled path is detectable.
    pub cgroup_id: Option<u64>,
    /// Monotonic seconds-nanoseconds of the interval start (the decision).
    pub mono_start_ns: u64,
    /// Monotonic nanoseconds of the interval end. P1: equal to the start.
    pub mono_end_ns: u64,
    /// The mode the decision was made in (`off|report|enforce`).
    pub mode: &'static str,
    /// One of `pressure|aged_out|guard_stop|deregistered|unreadable_psi|manual`
    /// where a thaw/death reason exists, or the row's own reason word.
    pub reason: &'static str,
    /// `true` when the row describes a decision rather than an effect (always
    /// `true` in P1, which compiles no act path).
    pub simulated: bool,
    /// Free text for a human: the refusal detail, the expired unit's silence.
    pub detail: Option<String>,
    /// Read-back reclaim bytes; `null` in P1 (no reclaim exists).
    pub reclaim_bytes: Option<u64>,
    /// Deeper matching scopes that lost to the selected shallow scope, when any.
    pub shadowed: Option<Vec<String>>,
}

impl Event {
    /// A decision row: `seconds: 0` and `simulated: true` are set from `kind`.
    pub fn decision(
        kind: &'static str,
        reason: &'static str,
        mode: &'static str,
        at_ns: u64,
    ) -> Self {
        Self {
            event: kind,
            unit: None,
            cgroup: None,
            cgroup_id: None,
            mono_start_ns: at_ns,
            mono_end_ns: at_ns,
            mode,
            reason,
            simulated: true,
            detail: None,
            reclaim_bytes: None,
            shadowed: None,
        }
    }

    /// Attach the unit and its scope.
    pub fn with_scope(
        mut self,
        unit: Option<&str>,
        scope: Option<&str>,
        cgroup_id: Option<u64>,
    ) -> Self {
        self.unit = unit.map(str::to_string);
        self.cgroup = scope.map(str::to_string);
        self.cgroup_id = cgroup_id;
        self
    }

    /// Attach a human-readable detail.
    pub fn with_detail(mut self, detail: &str) -> Self {
        self.detail = Some(detail.to_string());
        self
    }

    /// Attach the deeper matching scopes that were shadowed by this selection.
    pub fn with_shadowed(mut self, shadowed: &[String]) -> Self {
        if !shadowed.is_empty() {
            self.shadowed = Some(shadowed.to_vec());
        }
        self
    }

    /// The interval's length in seconds (0 in P1 by construction).
    pub fn seconds(&self) -> u64 {
        crate::clock::seconds_of_ns(self.mono_end_ns.saturating_sub(self.mono_start_ns))
    }

    /// The row as a journal object, with the two facts the journal stamps added.
    pub fn to_value(&self, boot_id: &str, seq: u64) -> Value {
        let optional = |value: Option<String>| value.map_or(Value::Null, Value::String);
        let mut fields = vec![
            ("v".to_string(), Value::String(EVENT_VERSION.to_string())),
            ("boot_id".to_string(), Value::String(boot_id.to_string())),
            ("seq".to_string(), Value::Integer(seq as i64)),
            ("event".to_string(), Value::String(self.event.to_string())),
            ("unit".to_string(), optional(self.unit.clone())),
            ("cgroup".to_string(), optional(self.cgroup.clone())),
            (
                "cgroup_id".to_string(),
                self.cgroup_id
                    .map_or(Value::Null, |id| Value::Integer(id as i64)),
            ),
            (
                "mono_start_ns".to_string(),
                Value::Integer(self.mono_start_ns as i64),
            ),
            (
                "mono_end_ns".to_string(),
                Value::Integer(self.mono_end_ns as i64),
            ),
            ("seconds".to_string(), Value::Integer(self.seconds() as i64)),
            ("mode".to_string(), Value::String(self.mode.to_string())),
            ("reason".to_string(), Value::String(self.reason.to_string())),
            ("simulated".to_string(), Value::Bool(self.simulated)),
            (
                "reclaim_bytes".to_string(),
                self.reclaim_bytes
                    .map_or(Value::Null, |b| Value::Integer(b as i64)),
            ),
            ("detail".to_string(), optional(self.detail.clone())),
        ];
        if let Some(shadowed) = &self.shadowed {
            fields.push((
                "shadowed".to_string(),
                Value::Array(shadowed.iter().cloned().map(Value::String).collect()),
            ));
        }
        Value::Object(fields)
    }
}

/// Read the host's boot id from `path`, trimmed. A missing file is `None`, never
/// an invented id: two intervals must not be joined across a reboot.
pub fn read_boot_id(path: &Path) -> Option<String> {
    let text = std::fs::read_to_string(path).ok()?;
    let trimmed = text.trim();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed.to_string())
    }
}

/// The append-only journal for one boot.
#[derive(Debug)]
pub struct Journal {
    dir: PathBuf,
    boot_id: String,
    seq: u64,
    rows: Vec<Value>,
    truncated_from: Option<u64>,
}

impl Journal {
    /// A journal in `dir`: the directory is created if it is missing (that is
    /// the only write this module performs besides the append itself).
    pub fn new(dir: impl Into<PathBuf>, boot_id: &str) -> io::Result<Self> {
        let dir = dir.into();
        std::fs::create_dir_all(&dir)?;
        let seq = existing_sequence(&dir, boot_id)?;
        Ok(Self {
            dir,
            boot_id: boot_id.to_string(),
            seq,
            rows: Vec::new(),
            truncated_from: (seq > 0).then_some(seq),
        })
    }

    /// The directory the journal lives in.
    pub fn dir(&self) -> &Path {
        &self.dir
    }

    /// The boot id every row of this journal carries.
    pub fn boot_id(&self) -> &str {
        &self.boot_id
    }

    /// The last sequence number handed out.
    pub fn seq(&self) -> u64 {
        self.seq
    }

    /// The journal file.
    pub fn file(&self) -> PathBuf {
        self.dir.join(JOURNAL_FILE)
    }

    /// The rotated file.
    pub fn rotated(&self) -> PathBuf {
        self.dir.join(ROTATED_FILE)
    }

    /// Append one row: stamp it, rotate if needed, write the line, keep it in the
    /// ring, and hand the stamped object back so a reply can report the `seq`.
    pub fn append(&mut self, event: &Event) -> io::Result<Value> {
        self.seq = self.seq.saturating_add(1);
        let mut value = event.to_value(&self.boot_id, self.seq);
        bound_journal_fields(&mut value);
        self.rotate_if_full()?;
        let mut file = std::fs::OpenOptions::new()
            .create(true)
            .read(true)
            .append(true)
            .open(self.file())?;
        let end = file.seek(SeekFrom::End(0))?;
        if end > 0 {
            file.seek(SeekFrom::End(-1))?;
            let mut last = [0];
            file.read_exact(&mut last)?;
            file.seek(SeekFrom::End(0))?;
            if last[0] != b'\n' {
                file.write_all(b"\n")?;
            }
        }
        let line = value.to_json();
        debug_assert!(line.len() + 1 <= MAX_JOURNAL_TEXT_BYTES);
        file.write_all(line.as_bytes())?;
        file.write_all(b"\n")?;
        file.flush()?;
        if self.rows.len() >= RECENT_ROWS {
            let dropped = self.rows.remove(0);
            if let Some(seq) = dropped.get("seq").and_then(Value::as_i64) {
                self.truncated_from = Some(seq as u64);
            }
        }
        self.rows.push(value.clone());
        Ok(value)
    }

    /// Rotate once the file reaches [`ROTATE_BYTES`]; one generation is kept.
    fn rotate_if_full(&self) -> io::Result<()> {
        let path = self.file();
        let size = match std::fs::metadata(&path) {
            Ok(metadata) => metadata.len(),
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
            Err(error) => return Err(error),
        };
        if size < ROTATE_BYTES {
            return Ok(());
        }
        let rotated = self.rotated();
        if rotated.exists() {
            std::fs::remove_file(&rotated)?;
        }
        std::fs::rename(&path, &rotated)
    }

    /// Rows above `since_seq` that this process still holds in memory.
    pub fn rows_since(&self, since_seq: u64) -> (&[Value], bool) {
        let truncated = self
            .truncated_from
            .map(|oldest| since_seq < oldest)
            .unwrap_or(false);
        let first = self
            .rows
            .iter()
            .position(|row| row.get("seq").and_then(Value::as_i64).unwrap_or(0) > since_seq as i64)
            .unwrap_or(self.rows.len());
        (&self.rows[first..], truncated)
    }

    /// The number of rows this process still holds (the reader's paging window).
    pub fn retained(&self) -> usize {
        self.rows.len()
    }
}

/// Recover the highest sequence for this boot from a bounded tail of each file.
/// Only complete, newline-terminated UTF-8 rows can recover a sequence.
fn existing_sequence(dir: &Path, boot_id: &str) -> io::Result<u64> {
    let mut highest = 0;
    for name in [JOURNAL_FILE, ROTATED_FILE] {
        let path = dir.join(name);
        let metadata = match std::fs::metadata(&path) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == io::ErrorKind::NotFound => continue,
            Err(error) => return Err(error),
        };
        let tail_bytes = crate::json::MAX_DOCUMENT_BYTES as u64;
        let start = metadata.len().saturating_sub(tail_bytes);
        let mut file = std::fs::File::open(&path)?;
        file.seek(SeekFrom::Start(start))?;
        let mut contents = Vec::with_capacity((metadata.len() - start) as usize);
        file.take(tail_bytes).read_to_end(&mut contents)?;
        let first_line = if start == 0 {
            0
        } else if let Some(newline) = contents.iter().position(|byte| *byte == b'\n') {
            newline + 1
        } else {
            continue;
        };
        for line in complete_lines(&contents[first_line..]) {
            let Ok(line) = std::str::from_utf8(line) else {
                continue;
            };
            if line.trim().is_empty() {
                continue;
            }
            let Ok(row) = Value::parse(line) else {
                continue;
            };
            if row.get("boot_id").and_then(Value::as_str) != Some(boot_id) {
                continue;
            }
            if let Some(seq) = row.get("seq").and_then(Value::as_i64) {
                if let Ok(seq) = u64::try_from(seq) {
                    highest = highest.max(seq);
                }
            }
        }
    }
    Ok(highest)
}

/// Iterate only newline-terminated rows, stripping a CR from CRLF input.
pub fn complete_lines(bytes: &[u8]) -> impl Iterator<Item = &[u8]> {
    bytes.split_inclusive(|byte| *byte == b'\n').filter_map(|line| {
        let line = line.strip_suffix(b"\n")?;
        Some(line.strip_suffix(b"\r").unwrap_or(line))
    })
}

/// Bound every peer-influenced string before it reaches the append-only file.
/// The per-field ceilings keep the worst-case JSON-escaped row well below the
/// document limit, and every shortened field carries a visible marker.
fn bound_journal_fields(value: &mut Value) {
    let Value::Object(fields) = value else {
        return;
    };
    for (name, field) in fields {
        let limit = match name.as_str() {
            "boot_id" => Some(MAX_BOOT_ID_BYTES),
            "unit" | "cgroup" => Some(MAX_SCOPE_FIELD_BYTES),
            "detail" => Some(MAX_DETAIL_BYTES),
            _ => None,
        };
        if let Some(limit) = limit {
            truncate_value_string(field, limit);
        }
        if name == "shadowed" {
            let Value::Array(items) = field else {
                continue;
            };
            if items.len() > MAX_SHADOWED_ROWS {
                items.truncate(MAX_SHADOWED_ROWS - 1);
                items.push(Value::String(TRUNCATED_MARKER.to_string()));
            }
            for item in items {
                truncate_value_string(item, MAX_SHADOWED_FIELD_BYTES);
            }
        }
    }
}

fn truncate_value_string(value: &mut Value, max_bytes: usize) {
    let Value::String(text) = value else {
        return;
    };
    if text.len() <= max_bytes {
        return;
    }
    let prefix_limit = max_bytes.saturating_sub(TRUNCATED_MARKER.len());
    let mut end = prefix_limit.min(text.len());
    while !text.is_char_boundary(end) {
        end -= 1;
    }
    text.truncate(end);
    text.push_str(TRUNCATED_MARKER);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::TempTree;

    const BOOT: &str = "b0a1-4f2c";

    fn journal(tree: &TempTree) -> Journal {
        Journal::new(tree.path().join("run"), BOOT).expect("journal")
    }

    #[test]
    fn a_row_carries_the_journal_stamp_and_the_p1_simulated_marker() {
        let tree = TempTree::new("events-row");
        let mut journal = journal(&tree);
        let event = Event::decision("would-freeze", "pressure", "report", 1_000_000_000)
            .with_scope(Some("rg-abc"), Some("docker-0123.scope"), Some(4242));
        let row = journal.append(&event).expect("append");
        assert_eq!(row.get("v").and_then(Value::as_str), Some(EVENT_VERSION));
        assert_eq!(row.get("boot_id").and_then(Value::as_str), Some(BOOT));
        assert_eq!(row.get("seq").and_then(Value::as_i64), Some(1));
        assert_eq!(
            row.get("event").and_then(Value::as_str),
            Some("would-freeze")
        );
        assert_eq!(
            row.get("seconds").and_then(Value::as_i64),
            Some(0),
            "P1 records no interval"
        );
        assert_eq!(
            row.get("simulated").and_then(Value::as_bool),
            Some(true),
            "a decision, not an effect"
        );
        assert_eq!(
            row.get("reclaim_bytes"),
            Some(&Value::Null),
            "nothing is reclaimed in P1"
        );
        let file = std::fs::read_to_string(journal.file()).expect("premature");
        let lines: Vec<&str> = file.lines().collect();
        assert_eq!(lines.len(), 1, "one object per line");
        assert_eq!(
            lines[0],
            row.to_json(),
            "the file holds exactly the replied bytes"
        );
    }

    #[test]
    fn append_truncates_peer_influenced_fields_with_a_visible_marker() {
        let tree = TempTree::new("events-row-bound");
        let boot = "boot".repeat(MAX_BOOT_ID_BYTES);
        let mut journal = Journal::new(tree.path().join("run"), &boot).expect("journal");
        let shadowed = (0..MAX_SHADOWED_ROWS + 2)
            .map(|index| format!("{}-{index}", "界".repeat(400)))
            .collect::<Vec<_>>();
        let event = Event::decision("registered", "registered", "report", 1)
            .with_scope(
                Some(&"x".repeat(MAX_SCOPE_FIELD_BYTES + 20)),
                Some(&"y".repeat(MAX_SCOPE_FIELD_BYTES + 20)),
                Some(1),
            )
            .with_detail(&"é".repeat(MAX_DETAIL_BYTES + 100))
            .with_shadowed(&shadowed);
        let row = journal.append(&event).expect("bounded append");
        let line = std::fs::read_to_string(journal.file()).expect("journal line");
        assert!(line.len() <= crate::json::MAX_DOCUMENT_BYTES);
        assert!(line.trim_end().len() < MAX_JOURNAL_TEXT_BYTES);
        for field in ["boot_id", "unit", "cgroup", "detail"] {
            assert!(
                row.get(field)
                    .and_then(Value::as_str)
                    .is_some_and(|text| text.ends_with(TRUNCATED_MARKER)),
                "{field} was shortened with a visible marker"
            );
        }
        let shadowed = row.get("shadowed").and_then(Value::as_array).expect("shadowed");
        assert_eq!(shadowed.len(), MAX_SHADOWED_ROWS);
        assert_eq!(shadowed.last().and_then(Value::as_str), Some(TRUNCATED_MARKER));
        assert_eq!(Value::parse(line.trim()).expect("valid row"), row);
    }

    #[test]
    fn recovery_scans_a_bounded_tail_instead_of_rejecting_a_large_generation() {
        let tree = TempTree::new("events-large-generation");
        let dir = tree.at("run");
        std::fs::create_dir_all(&dir).expect("state dir");
        let prior = Event::decision("would-freeze", "pressure", "report", 1)
            .to_value(BOOT, 77)
            .to_json();
        let mut contents = vec![b'x'; MAX_JOURNAL_FILE_BYTES as usize + 4096];
        contents.push(b'\n');
        contents.extend_from_slice(prior.as_bytes());
        contents.push(b'\n');
        std::fs::write(dir.join(JOURNAL_FILE), contents).expect("large generation");

        let mut journal = Journal::new(&dir, BOOT).expect("large file does not block restart");
        let next = journal
            .append(&Event::decision("would-thaw", "pressure", "report", 2))
            .expect("append after restart");
        assert_eq!(next.get("seq").and_then(Value::as_i64), Some(78));
    }

    #[test]
    fn a_torn_utf8_tail_is_skipped_and_append_starts_a_new_line() {
        let tree = TempTree::new("events-torn-utf8");
        let dir = tree.at("run");
        std::fs::create_dir_all(&dir).expect("state dir");
        let prior = Event::decision("would-freeze", "pressure", "report", 1)
            .to_value(BOOT, 4)
            .to_json();
        let mut contents = format!("{prior}\n{{\"boot_id\":\"").into_bytes();
        contents.extend_from_slice(&[0xf0, 0x9f]);
        std::fs::write(dir.join(JOURNAL_FILE), contents).expect("torn tail");

        let mut journal = Journal::new(&dir, BOOT).expect("invalid UTF-8 tail is ignored");
        let row = journal
            .append(&Event::decision("would-thaw", "pressure", "report", 2))
            .expect("append after torn tail");
        assert_eq!(row.get("seq").and_then(Value::as_i64), Some(5));
        let bytes = std::fs::read(journal.file()).expect("journal bytes");
        let complete: Vec<&[u8]> = complete_lines(&bytes).collect();
        assert_eq!(complete.len(), 3, "the torn fragment is isolated by a newline");
        assert_eq!(
            std::str::from_utf8(complete[2]).expect("new row is valid UTF-8"),
            row.to_json()
        );
        let restarted = Journal::new(&dir, BOOT).expect("second restart");
        assert_eq!(restarted.seq(), 5, "the complete row's sequence is recovered");
    }

    #[test]
    fn sequence_numbers_are_monotonic_and_survive_a_rotation() {
        let tree = TempTree::new("events-rotate");
        let mut journal = journal(&tree);
        for _ in 0..3 {
            journal
                .append(&Event::decision("would-thaw", "pressure", "report", 2))
                .expect("append");
        }
        assert_eq!(journal.seq(), 3);
        // A journal whose file is already at the bound rotates on the NEXT append,
        // so the current line is never the one split away from its file.
        std::fs::write(journal.file(), "x".repeat(ROTATE_BYTES as usize)).expect("fill");
        journal
            .append(&Event::decision(
                "unknown-pressure",
                "unreadable_psi",
                "report",
                3,
            ))
            .expect("append");
        assert_eq!(journal.seq(), 4, "seq keeps counting across a rotation");
        let rotated = std::fs::read_to_string(journal.rotated()).expect("rotated file");
        assert!(rotated.len() as u64 >= ROTATE_BYTES);
        let current = std::fs::read_to_string(journal.file()).expect("current file");
        assert_eq!(current.lines().count(), 1);
        assert!(current.contains("\"reason\":\"unreadable_psi\""));
    }

    #[test]
    fn a_restart_in_the_same_boot_continues_the_existing_sequence() {
        let tree = TempTree::new("events-restart");
        {
            let mut journal = journal(&tree);
            for at in 1..=2 {
                journal
                    .append(&Event::decision("would-freeze", "pressure", "report", at))
                    .expect("append before restart");
            }
        }

        let mut restarted = journal(&tree);
        let row = restarted
            .append(&Event::decision("would-thaw", "pressure", "report", 3))
            .expect("append after restart");
        assert_eq!(row.get("seq").and_then(Value::as_i64), Some(3));
    }

    #[test]
    fn sequence_recovery_reads_the_rotated_file_when_current_has_no_row_for_this_boot() {
        let tree = TempTree::new("events-restart-rotated");
        let dir = tree.at("run");
        std::fs::create_dir_all(&dir).expect("state dir");
        let prior = Event::decision("would-freeze", "pressure", "report", 1)
            .to_value(BOOT, 7)
            .to_json();
        std::fs::write(dir.join(ROTATED_FILE), format!("{prior}\n")).expect("rotated row");
        let other_boot = Event::decision("would-thaw", "pressure", "report", 2)
            .to_value("other-boot", 1)
            .to_json();
        std::fs::write(dir.join(JOURNAL_FILE), format!("{other_boot}\n")).expect("other boot row");

        let mut journal = Journal::new(&dir, BOOT).expect("journal");
        let row = journal
            .append(&Event::decision("would-thaw", "pressure", "report", 3))
            .expect("append");
        assert_eq!(row.get("seq").and_then(Value::as_i64), Some(8));
    }

    #[test]
    fn a_reader_pages_by_sequence_and_learns_when_the_window_is_short() {
        let tree = TempTree::new("events-page");
        let mut journal = journal(&tree);
        for _ in 0..2 {
            journal
                .append(&Event::decision("would-freeze", "pressure", "report", 5))
                .expect("append");
        }
        let (rows, truncated) = journal.rows_since(0);
        assert_eq!(rows.len(), 2);
        assert!(!truncated);
        let (rows, truncated) = journal.rows_since(2);
        assert_eq!(
            rows.len(),
            0,
            "asking for the last row returns what followed it"
        );
        assert!(!truncated);
        // Fill the ring past its window: the oldest rows drop and the reply says so
        // instead of reporting a silently short page.
        for _ in 0..RECENT_ROWS {
            journal
                .append(&Event::decision("would-freeze", "pressure", "report", 6))
                .expect("append");
        }
        let (rows, truncated) = journal.rows_since(1);
        assert_eq!(rows.len(), RECENT_ROWS);
        assert!(
            truncated,
            "a client behind the window is told it missed rows"
        );
        assert_eq!(journal.retained(), RECENT_ROWS);
    }

    #[test]
    fn restart_marks_the_pre_restart_prefix_as_truncated() {
        let tree = TempTree::new("events-restart-truncated");
        let mut first = journal(&tree);
        first
            .append(&Event::decision("would-freeze", "pressure", "report", 1))
            .expect("first row");
        drop(first);

        let restarted = journal(&tree);
        let (rows, truncated) = restarted.rows_since(0);
        assert!(rows.is_empty());
        assert!(truncated, "a new in-memory ring cannot claim the old prefix");
        let (_, truncated) = restarted.rows_since(restarted.seq());
        assert!(!truncated, "a caller already at the recovered sequence is current");
    }

    #[test]
    fn a_missing_boot_id_is_absent_and_never_invented() {
        let tree = TempTree::new("events-boot");
        let path = tree.write("boot_id", "  b0a1-4f2c  \n");
        assert_eq!(
            read_boot_id(&path).as_deref(),
            Some(BOOT),
            "the read is trimmed"
        );
        tree.write("empty", "\n");
        assert_eq!(
            read_boot_id(&tree.at("empty")),
            None,
            "an empty file is not an id"
        );
        assert_eq!(
            read_boot_id(&tree.at("absent")),
            None,
            "an absent file is not an id"
        );
    }
}
