//! Interned entity and metric names. The canonical copy is persisted next to the cold
//! segments so ids stay stable across restarts; a mirror sits in the tmpfs directory.
use std::collections::HashMap;

#[derive(Default)]
pub struct Dict {
    pub mets: Vec<(String, bool)>, // (name, is_counter)
    mmap: HashMap<String, u32>,
    pub ents: Vec<String>,
    emap: HashMap<String, u32>,
}

impl Dict {
    pub fn met(&mut self, name: &str, counter: bool) -> u32 {
        if let Some(&i) = self.mmap.get(name) {
            return i;
        }
        let i = self.mets.len() as u32;
        self.mets.push((name.to_string(), counter));
        self.mmap.insert(name.to_string(), i);
        i
    }

    pub fn ent(&mut self, name: &str) -> u32 {
        if let Some(&i) = self.emap.get(name) {
            return i;
        }
        let i = self.ents.len() as u32;
        self.ents.push(name.to_string());
        self.emap.insert(name.to_string(), i);
        i
    }

    pub fn load(dir: &str) -> Dict {
        let mut d = Dict::default();
        if let Ok(t) = std::fs::read_to_string(format!("{dir}/mets.dict")) {
            for l in t.lines() {
                if let Some((k, n)) = l.split_once(' ') {
                    d.met(n, k == "C");
                }
            }
        }
        if let Ok(t) = std::fs::read_to_string(format!("{dir}/ents.dict")) {
            for l in t.lines() {
                d.ent(l);
            }
        }
        d
    }
}

/// Line-oriented dictionary reader for the query thread (no shared state needed).
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
        ents = t.lines().map(|s| s.to_string()).collect();
    }
    (mets, ents)
}
