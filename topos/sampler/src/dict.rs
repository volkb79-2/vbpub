//! Interned entity and metric names. The canonical copy is persisted next to the cold
//! segments so ids stay stable across restarts; a mirror sits in the tmpfs directory.
//!
//! Entity ids are garbage collected: an entity not seen for longer than the retention
//! of the longest tier cannot be referenced by any stored segment, so its id is freed
//! and reused. A freed slot is an empty line in `ents.dict`. Metric names are bounded
//! by the kernel's key set and are never collected.
use std::collections::HashMap;

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
}

impl Dict {
    pub fn met(&mut self, name: &str, counter: bool) -> u32 {
        if let Some(&i) = self.mmap.get(name) {
            return i;
        }
        let i = self.mets.len() as u32;
        self.mets.push((name.to_string(), counter));
        self.mmap.insert(name.to_string(), i);
        self.version += 1;
        i
    }

    pub fn ent(&mut self, name: &str, now_s: u64) -> u32 {
        if let Some(&i) = self.emap.get(name) {
            self.seen[i as usize] = now_s;
            return i;
        }
        self.version += 1;
        let i = if let Some(i) = self.free.pop() {
            self.ents[i as usize] = name.to_string();
            self.seen[i as usize] = now_s;
            self.ents_dirty = true;
            i
        } else {
            self.ents.push(name.to_string());
            self.seen.push(now_s);
            (self.ents.len() - 1) as u32
        };
        self.emap.insert(name.to_string(), i);
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
        d
    }
}

/// Line-oriented dictionary reader (also used by the query side, no shared state needed).
pub fn read_lists(dir: &str) -> (Vec<(String, bool)>, Vec<String>) {
    let mut mets = Vec::new();
    let mut ents = Vec::new();
    if let Ok(t) = std::fs::read_to_string(format!("{dir}/mets.dict")) {
        for l in t.lines() {
            if let Some((k, n)) = l.split_once(' ') {
                mets.push((n.to_string(), k == "C"));
            }
        }
    }
    if let Ok(t) = std::fs::read_to_string(format!("{dir}/ents.dict")) {
        ents = t.split('\n').map(|s| s.to_string()).collect();
        if ents.last().map(|s| s.is_empty()).unwrap_or(false) {
            ents.pop(); // trailing newline
        }
    }
    (mets, ents)
}
