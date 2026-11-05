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
use std::collections::HashMap;

/// Rollup companions of a gauge `m` (max, min, p95) have the ids `ROLLUP_BASE + 3*m + k`.
/// They are derived, never interned, so the sampling thread and the writer cannot disagree.
pub const ROLLUP_BASE: u32 = 1 << 24;

pub fn rollup_ids(m: u32) -> [u32; 3] {
    [ROLLUP_BASE + 3 * m, ROLLUP_BASE + 3 * m + 1, ROLLUP_BASE + 3 * m + 2]
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

    /// Load from `dir`. Every loaded entity counts as seen at `now_s`.
    pub fn load(dir: &str, now_s: u64) -> Dict {
        let mut d = Dict::default();
        let (mets, ents) = read_lists(dir);
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
pub fn read_lists(dir: &str) -> (Vec<(String, bool)>, Vec<String>) {
    let complete = |t: &str| -> usize { t.rfind('\n').map(|p| p + 1).unwrap_or(0) };
    let mut mets = Vec::new();
    let mut ents = Vec::new();
    if let Ok(t) = std::fs::read_to_string(format!("{dir}/mets.dict")) {
        let t = &t[..complete(&t)];
        for l in t.lines() {
            if let Some((k, n)) = l.split_once(' ') {
                mets.push((n.to_string(), k == "C"));
            }
        }
    }
    if let Ok(t) = std::fs::read_to_string(format!("{dir}/ents.dict")) {
        let t = &t[..complete(&t)];
        ents = t.split('\n').map(|s| s.to_string()).collect();
        if ents.last().map(|s| s.is_empty()).unwrap_or(false) {
            ents.pop(); // trailing newline
        }
    }
    (mets, ents)
}
