//! Interned entity and metric names. The canonical copy is persisted next to the cold
//! segments so ids stay stable across restarts.
//!
//! The sampling thread owns one `Dict`; the writer thread owns a mirror that is fed with
//! `Delta`s (S-03), so sampling never waits for the dictionary files. The writer persists
//! (and fsyncs) every delta before it writes a frame that can reference its ids (S-09).
//!
//! Entity ids are garbage collected: an entity not seen for longer than the retention
//! of the longest tier cannot be referenced by any stored segment, so its id is freed
//! and reused. A freed slot is an empty line in `ents.dict`. Metric names are bounded
//! by the kernel's key set and are never collected.
use crate::util::{perr, PErr};
use std::collections::HashMap;

/// Rollup companions of a gauge `m` (max, min, p95) have the ids `ROLLUP_BASE + 3*m + k`.
/// They are derived, never interned, so the sampling thread and the writer cannot disagree.
pub const ROLLUP_BASE: u32 = 1 << 24;

pub fn rollup_ids(m: u32) -> [u32; 3] {
    [ROLLUP_BASE + 3 * m, ROLLUP_BASE + 3 * m + 1, ROLLUP_BASE + 3 * m + 2]
}

/// Companion of metric `m` in rollup frames: how many of the bucket's ticks held a value (X-13).
/// Derived like the rollup ids, never interned; the range lies above every rollup id.
pub const COVER_BASE: u32 = 1 << 26;

pub fn cover_id(m: u32) -> u32 {
    COVER_BASE + m
}

/// Count header of a dictionary file (S-09): the first line, fixed width so it can be updated in
/// place after an append: `#TOPOS-DICT 1 n=<20 digits>\n`, n = number of lines after it.
pub const HEADER_PREFIX: &str = "#TOPOS-DICT 1 n=";
pub const HEADER_LEN: usize = HEADER_PREFIX.len() + 20 + 1;

pub fn header(n: usize) -> String {
    format!("{HEADER_PREFIX}{n:020}\n")
}

/// Names are stored one per line: a newline or carriage return can not be represented, so
/// it is replaced at insert time (in memory and on disk alike, so two names never collapse
/// into one dictionary line after a reload).
pub fn clean(name: &str) -> std::borrow::Cow<'_, str> {
    if name.contains(['\n', '\r']) {
        std::borrow::Cow::Owned(name.replace(['\n', '\r'], "?"))
    } else {
        std::borrow::Cow::Borrowed(name)
    }
}

/// Dictionary changes since the previous delta, in id order.
#[derive(Default, Debug, Clone)]
pub struct Delta {
    pub mets: Vec<(String, bool)>,
    /// (entity id, name); an empty name frees the slot.
    pub ents: Vec<(u32, String)>,
}

impl Delta {
    /// Put `older` (changes that happened before this delta) in front.
    pub fn prepend(&mut self, mut older: Delta) {
        older.mets.append(&mut self.mets);
        older.ents.append(&mut self.ents);
        *self = older;
    }
}

#[derive(Default)]
pub struct Dict {
    pub mets: Vec<(String, bool)>, // (name, is_counter)
    mmap: HashMap<String, u32>,
    pub ents: Vec<String>, // "" = free slot
    emap: HashMap<String, u32>,
    seen: Vec<u64>,
    free: Vec<u32>,
    /// Bumped on every change to `ents` or `mets` (the snapshot rebuilds its copy).
    pub version: u64,
    /// True when a slot was freed or reused: the persisted file needs a full rewrite.
    pub ents_dirty: bool,
    sent_mets: usize,
    log: Vec<(u32, String)>,
}

impl Dict {
    pub fn met(&mut self, name: &str, counter: bool) -> u32 {
        if let Some(&i) = self.mmap.get(name) {
            return i;
        }
        let name = clean(name).into_owned();
        if let Some(&i) = self.mmap.get(&name) {
            return i;
        }
        let i = self.mets.len() as u32;
        assert!(i < ROLLUP_BASE / 4, "metric id space exhausted");
        self.mets.push((name.clone(), counter));
        self.mmap.insert(name, i);
        self.version += 1;
        i
    }

    pub fn ent(&mut self, name: &str, now_s: u64) -> u32 {
        if let Some(&i) = self.emap.get(name) {
            self.seen[i as usize] = now_s;
            return i;
        }
        let name = clean(name).into_owned();
        if let Some(&i) = self.emap.get(&name) {
            self.seen[i as usize] = now_s;
            return i;
        }
        self.version += 1;
        let i = if let Some(i) = self.free.pop() {
            self.ents[i as usize] = name.clone();
            self.seen[i as usize] = now_s;
            self.ents_dirty = true;
            i
        } else {
            self.ents.push(name.clone());
            self.seen.push(now_s);
            (self.ents.len() - 1) as u32
        };
        self.log.push((i, name.clone()));
        self.emap.insert(name, i);
        i
    }

    pub fn touch(&mut self, id: u32, now_s: u64) {
        if let Some(s) = self.seen.get_mut(id as usize) {
            *s = now_s;
        }
    }

    /// Free entity ids not seen for `retention_s`. Returns how many were freed.
    pub fn gc(&mut self, now_s: u64, retention_s: u64) -> usize {
        let mut n = 0;
        for i in 0..self.ents.len() {
            if !self.ents[i].is_empty() && now_s.saturating_sub(self.seen[i]) > retention_s {
                self.emap.remove(&self.ents[i]);
                self.ents[i].clear();
                self.free.push(i as u32);
                self.log.push((i as u32, String::new()));
                n += 1;
            }
        }
        if n > 0 {
            self.ents_dirty = true;
            self.version += 1;
        }
        n
    }

    pub fn live_ents(&self) -> usize {
        self.ents.len() - self.free.len()
    }

    /// Everything that changed since the previous call.
    pub fn take_delta(&mut self) -> Delta {
        let d = Delta { mets: self.mets[self.sent_mets..].to_vec(), ents: std::mem::take(&mut self.log) };
        self.sent_mets = self.mets.len();
        self.ents_dirty = false;
        d
    }

    /// Mirror side: apply a delta produced by `take_delta` of the owning dictionary. Ids are
    /// positional, so the mirror reproduces the owner's tables exactly.
    pub fn apply(&mut self, d: &Delta) {
        for (n, c) in &d.mets {
            if self.mets.len() as u32 >= ROLLUP_BASE / 4 {
                break;
            }
            self.mmap.insert(n.clone(), self.mets.len() as u32);
            self.mets.push((n.clone(), *c));
        }
        for (id, name) in &d.ents {
            let i = *id as usize;
            if i < self.ents.len() {
                self.ents[i] = name.clone();
                self.ents_dirty = true;
            } else {
                while self.ents.len() < i {
                    self.ents.push(String::new());
                    self.ents_dirty = true;
                }
                self.ents.push(name.clone());
            }
        }
        self.sent_mets = self.mets.len();
        self.version += 1;
    }

    /// Load from `dir`, ignoring damage (tests). Every loaded entity counts as seen at `now_s`.
    #[cfg(test)]
    pub fn load(dir: &str, now_s: u64) -> Dict {
        Dict::from_lists(read_lists(dir), now_s)
    }

    /// Like `load`, but a damaged dictionary is an error (start-up refuses).
    pub fn load_checked(dir: &str, now_s: u64) -> Result<Dict, PErr> {
        Ok(Dict::from_lists(read_lists_checked(dir)?, now_s))
    }

    fn from_lists(lists: (Vec<(String, bool)>, Vec<String>), now_s: u64) -> Dict {
        let mut d = Dict::default();
        let (mets, ents) = lists;
        for (n, c) in mets {
            d.met(&n, c);
        }
        for n in ents {
            if n.is_empty() {
                let i = d.ents.len() as u32;
                d.ents.push(String::new());
                d.seen.push(now_s);
                d.free.push(i);
            } else {
                d.ent(&n, now_s);
            }
        }
        d.version = 0;
        d.ents_dirty = false;
        d.sent_mets = d.mets.len();
        d.log.clear();
        d
    }
}

/// Line-oriented dictionary reader. A last line without its newline is a torn append
/// (power loss, ENOSPC) and is ignored, so it can never merge with the next appended entry.
#[cfg(test)]
pub fn read_lists(dir: &str) -> (Vec<(String, bool)>, Vec<String>) {
    read_lists_checked(dir).unwrap_or_default()
}

/// The lines of one dictionary file (header stripped). A file whose header declares more lines
/// than it holds lost part of itself: reading it would hand out ids for the wrong names, so it
/// is an error, not a short list (S-09). A header BEHIND the lines is a crash between the
/// append and the count update and is accepted. A file without a header is a pre-header one.
fn read_dict_file(dir: &str, name: &str) -> Result<Vec<String>, PErr> {
    let b = match crate::fsafe::read_small(dir, name, 1 << 30) {
        Ok(b) => b,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(e) => return Err(perr("dict_io", format!("{dir}/{name}: {e}"))),
    };
    let t = String::from_utf8_lossy(&b);
    let t = &t[..t.rfind('\n').map(|p| p + 1).unwrap_or(0)];
    let (mut body, mut declared) = (t, None);
    if t.starts_with(HEADER_PREFIX) {
        let digits = t.get(HEADER_PREFIX.len()..HEADER_LEN - 1).filter(|_| t.len() >= HEADER_LEN && t.as_bytes()[HEADER_LEN - 1] == b'\n');
        match digits.and_then(|d| if d.bytes().all(|c| c.is_ascii_digit()) { d.parse::<usize>().ok() } else { None }) {
            Some(n) => {
                declared = Some(n);
                body = &t[HEADER_LEN..];
            }
            None => return Err(perr("dict_header_corrupt", format!("{dir}/{name}: the count header is damaged; move the dictionaries aside (history ids would be wrong)"))),
        }
    }
    let lines: Vec<String> = body.split('\n').map(|s| s.to_string()).collect();
    let lines: Vec<String> = lines[..lines.len().saturating_sub(1)].to_vec(); // body ends with a newline
    if let Some(n) = declared {
        if lines.len() < n {
            return Err(perr(
                "dict_short",
                format!("{dir}/{name} holds {} lines but its header says {n}: part of the dictionary was lost; move the dictionaries and segments aside, or restore them", lines.len()),
            ));
        }
    }
    Ok(lines)
}

/// Both dictionaries, or the reason one of them cannot be trusted.
pub fn read_lists_checked(dir: &str) -> Result<(Vec<(String, bool)>, Vec<String>), PErr> {
    let mets = read_dict_file(dir, "mets.dict")?
        .into_iter()
        .filter_map(|l| l.split_once(' ').map(|(k, n)| (n.to_string(), k == "C")))
        .collect();
    let ents = read_dict_file(dir, "ents.dict")?;
    Ok((mets, ents))
}
