//! Tiered store: raw/rollup frames appended to segment files. tmpfs tiers are plain, the
//! disk tier is a sequence of independent zstd frames, each prefixed by its length.
//! Frame body: u64 ts_ms | u32 dt_ms | varint n_ents | per entity: varint id, varint n,
//! n x value. A value is varint (metric<<2 | kind): kind 0 zigzag-varint int, kind 1
//! 8-byte f64, kind 2 one state-code byte (see `util::stc`). Counter deltas of zero are
//! stored (a zero is a measurement); a metric absent from a frame was not measured.
use crate::cfg::{Config, Tier};
use crate::collect::{Event, Val};
use crate::dict::{self, Dict};
use crate::util::*;
use std::collections::{HashMap, VecDeque};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Sv {
    V(f64),
    S(u8),
}

fn put_varint(o: &mut Vec<u8>, mut v: u64) {
    while v >= 0x80 {
        o.push((v as u8) | 0x80);
        v >>= 7;
    }
    o.push(v as u8);
}
fn get_varint(b: &[u8], p: &mut usize) -> Option<u64> {
    let mut v = 0u64;
    let mut sh = 0;
    loop {
        let c = *b.get(*p)?;
        *p += 1;
        v |= ((c & 0x7f) as u64) << sh;
        if c < 0x80 {
            return Some(v);
        }
        sh += 7;
        if sh > 63 {
            return None;
        }
    }
}
fn zz(i: i64) -> u64 {
    ((i << 1) ^ (i >> 63)) as u64
}
fn unzz(u: u64) -> i64 {
    ((u >> 1) as i64) ^ -((u & 1) as i64)
}
fn put_val(o: &mut Vec<u8>, m: u32, v: Sv) {
    match v {
        Sv::S(c) => {
            put_varint(o, ((m as u64) << 2) | 2);
            o.push(c);
        }
        Sv::V(v) if v.fract() == 0.0 && v.abs() < 9.0e15 => {
            put_varint(o, (m as u64) << 2);
            put_varint(o, zz(v as i64));
        }
        Sv::V(v) => {
            put_varint(o, ((m as u64) << 2) | 1);
            o.extend_from_slice(&v.to_bits().to_le_bytes());
        }
    }
}
fn get_val(b: &[u8], p: &mut usize) -> Option<(u32, Sv)> {
    let h = get_varint(b, p)?;
    let m = (h >> 2) as u32;
    match h & 3 {
        0 => Some((m, Sv::V(unzz(get_varint(b, p)?) as f64))),
        1 => {
            let s = b.get(*p..*p + 8)?;
            *p += 8;
            Some((m, Sv::V(f64::from_bits(u64::from_le_bytes(s.try_into().ok()?)))))
        }
        2 => {
            let c = *b.get(*p)?;
            *p += 1;
            Some((m, Sv::S(c)))
        }
        _ => None,
    }
}
fn head(o: &mut Vec<u8>, ts: u64, dt: u32, nents: usize) {
    o.extend_from_slice(&ts.to_le_bytes());
    o.extend_from_slice(&dt.to_le_bytes());
    put_varint(o, nents as u64);
}

struct Acc {
    n: u32,
    ns: u32,
    last_st: u8,
    sum: f64,
    min: f64,
    max: f64,
    counter: bool,
    samples: Vec<f32>,
}

struct TierState {
    t: Tier,
    dir: String,
    disk: bool,
    raw: bool,
    per: u64,
    n: u64,
    dt_sum: u64,
    acc: HashMap<(u32, u32), Acc>,
    cur: Option<(u64, File)>,
    segs: VecDeque<(u64, u64)>, // (start_s, bytes)
    total: u64,
    last_write_s: u64,
}

#[derive(Clone, Debug)]
pub struct TierInfo {
    pub name: String,
    pub medium: String,
    pub segments: usize,
    pub bytes: u64,
    pub oldest_s: u64,
    pub newest_s: u64,
    pub keep_s: u64,
    pub fill: f64,
}

pub struct Store {
    tiers: Vec<TierState>,
    cold_dir: String,
    cap: u64,
    rid: HashMap<u32, [u32; 3]>,
    w_mets: usize,
    w_ents: usize,
    ev_file: File,
    ev_path: String,
    ev_size: u64,
    ev_max: u64,
    pub w_tmpfs: u64,
    pub w_cold: u64,
    pub w_other: u64,
    enabled: bool,
}

impl Store {
    pub fn open(cfg: &Config, loaded_mets: usize, loaded_ents: usize) -> std::io::Result<Store> {
        let s = &cfg.store;
        if let Some(p) = std::path::Path::new(&cfg.events.events_path).parent() {
            fs::create_dir_all(p)?;
        }
        let ev_file = OpenOptions::new().create(true).append(true).open(&cfg.events.events_path)?;
        let ev_size = ev_file.metadata().map(|m| m.len()).unwrap_or(0);
        let mut tiers = Vec::new();
        if s.enabled {
            fs::create_dir_all(&s.tmpfs_dir)?;
            fs::create_dir_all(&s.cold_dir)?;
            for t in &cfg.tier {
                let disk = t.medium == "disk";
                let dir = if disk { s.cold_dir.clone() } else { s.tmpfs_dir.clone() };
                let mut segs = Vec::new();
                let mut total = 0;
                if let Ok(rd) = fs::read_dir(&dir) {
                    for e in rd.flatten() {
                        let n = e.file_name().to_string_lossy().into_owned();
                        if let Some(r) = n.strip_prefix(&format!("{}-", t.name)).and_then(|r| r.strip_suffix(".seg")) {
                            if let Ok(st) = r.parse::<u64>() {
                                let sz = e.metadata().map(|m| m.len()).unwrap_or(0);
                                segs.push((st, sz));
                                total += sz;
                            }
                        }
                    }
                }
                segs.sort();
                tiers.push(TierState {
                    t: t.clone(),
                    dir,
                    disk,
                    raw: t.step_s * 1000 == cfg.sampler.tick_ms,
                    per: t.step_s * 1000 / cfg.sampler.tick_ms,
                    n: 0,
                    dt_sum: 0,
                    acc: HashMap::new(),
                    cur: None,
                    segs: segs.into(),
                    total,
                    last_write_s: 0,
                });
            }
        }
        Ok(Store {
            tiers,
            cold_dir: s.cold_dir.clone(),
            cap: s.cold_cap_bytes,
            rid: HashMap::new(),
            w_mets: loaded_mets,
            w_ents: loaded_ents,
            ev_file,
            ev_path: cfg.events.events_path.clone(),
            ev_size,
            ev_max: cfg.events.max_bytes,
            w_tmpfs: 0,
            w_cold: 0,
            w_other: 0,
            enabled: s.enabled,
        })
    }

    pub fn sizes(&self) -> (u64, u64) {
        let mut tm = 0;
        let mut cold = 0;
        for t in &self.tiers {
            if t.disk {
                cold += t.total
            } else {
                tm += t.total
            }
        }
        (tm, cold)
    }

    pub fn tier_info(&self) -> Vec<TierInfo> {
        self.tiers
            .iter()
            .map(|t| {
                let oldest = t.segs.front().map(|s| s.0).unwrap_or(0);
                let newest = t.last_write_s;
                let fill = if t.segs.is_empty() || t.t.keep_s == 0 {
                    0.0
                } else {
                    (newest.saturating_sub(oldest) as f64 / t.t.keep_s as f64).min(1.0)
                };
                TierInfo {
                    name: t.t.name.clone(),
                    medium: t.t.medium.clone(),
                    segments: t.segs.len(),
                    bytes: t.total,
                    oldest_s: oldest,
                    newest_s: newest,
                    keep_s: t.t.keep_s,
                    fill,
                }
            })
            .collect()
    }

    /// Persist dictionary changes: new names are appended; a freed or reused entity slot
    /// rewrites the entity file atomically (rare: only on container churn).
    pub fn sync_dict(&mut self, dict: &mut Dict) {
        if !self.enabled {
            return;
        }
        let mut add = String::new();
        for (n, c) in &dict.mets[self.w_mets..] {
            add.push_str(if *c { "C " } else { "G " });
            add.push_str(&n.replace('\n', " "));
            add.push('\n');
        }
        if !add.is_empty() {
            if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(format!("{}/mets.dict", self.cold_dir)) {
                let _ = f.write_all(add.as_bytes());
                self.w_other += add.len() as u64;
            }
            self.w_mets = dict.mets.len();
        }
        let path = format!("{}/ents.dict", self.cold_dir);
        if dict.ents_dirty {
            let mut all = String::new();
            for n in &dict.ents {
                all.push_str(&n.replace('\n', " "));
                all.push('\n');
            }
            let tmp = format!("{path}.tmp");
            if fs::write(&tmp, all.as_bytes()).is_ok() && fs::rename(&tmp, &path).is_ok() {
                self.w_other += all.len() as u64;
                self.w_ents = dict.ents.len();
                dict.ents_dirty = false;
            }
            return;
        }
        let mut add = String::new();
        for n in &dict.ents[self.w_ents..] {
            add.push_str(&n.replace('\n', " "));
            add.push('\n');
        }
        if !add.is_empty() {
            if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(&path) {
                let _ = f.write_all(add.as_bytes());
                self.w_other += add.len() as u64;
            }
            self.w_ents = dict.ents.len();
        }
    }

    pub fn append_events(&mut self, evs: &[Event]) {
        for e in evs {
            if self.ev_size > self.ev_max {
                let _ = fs::rename(&self.ev_path, format!("{}.1", self.ev_path));
                if let Ok(f) = OpenOptions::new().create(true).append(true).open(&self.ev_path) {
                    self.ev_file = f;
                    self.ev_size = 0;
                }
            }
            let _ = self.ev_file.write_all(e.json.as_bytes());
            let _ = self.ev_file.write_all(b"\n");
            self.ev_size += e.json.len() as u64 + 1;
            self.w_other += e.json.len() as u64 + 1;
        }
    }

    pub fn push(&mut self, frame: &[(u32, Vec<Val>)], ts_ms: u64, dt_ms: u64, dict: &mut Dict) {
        if !self.enabled {
            return;
        }
        let cap = self.cap;
        let mut body = Vec::with_capacity(64 * 1024);
        let sv = |x: &Val| if x.st != stc::VALUE { Sv::S(x.st) } else { Sv::V(x.v) };
        for ti in 0..self.tiers.len() {
            let Store { tiers, rid, w_tmpfs, w_cold, .. } = self;
            let t = &mut tiers[ti];
            if t.raw {
                body.clear();
                head(&mut body, ts_ms, dt_ms as u32, frame.len());
                for (id, vals) in frame {
                    put_varint(&mut body, *id as u64);
                    put_varint(&mut body, vals.len() as u64);
                    for x in vals {
                        put_val(&mut body, x.m, sv(x));
                    }
                }
                t.write(ts_ms, &body, cap, w_tmpfs, w_cold);
                continue;
            }
            for (id, vals) in frame {
                for x in vals {
                    let a = t.acc.entry((*id, x.m)).or_insert_with(|| Acc {
                        n: 0,
                        ns: 0,
                        last_st: 0,
                        sum: 0.0,
                        min: f64::MAX,
                        max: f64::MIN,
                        counter: dict.mets.get(x.m as usize).map(|m| m.1).unwrap_or(false),
                        samples: Vec::new(),
                    });
                    if x.st != stc::VALUE {
                        a.ns += 1;
                        a.last_st = x.st;
                        continue;
                    }
                    a.n += 1;
                    a.sum += x.v;
                    if !a.counter {
                        a.min = a.min.min(x.v);
                        a.max = a.max.max(x.v);
                        a.samples.push(x.v as f32);
                    }
                }
            }
            t.n += 1;
            t.dt_sum += dt_ms;
            if t.n < t.per {
                continue;
            }
            // flush the bucket
            let mut keys: Vec<((u32, u32), &mut Acc)> =
                t.acc.iter_mut().filter(|(_, a)| a.n > 0 || a.ns > 0).map(|(k, a)| (*k, a)).collect();
            keys.sort_unstable_by_key(|(k, _)| *k);
            let mut ents: Vec<(u32, Vec<(u32, Sv)>)> = Vec::new();
            for ((e, m), a) in keys {
                if ents.last().map(|l| l.0) != Some(e) {
                    ents.push((e, Vec::new()));
                }
                let out = &mut ents.last_mut().unwrap().1;
                if a.n == 0 {
                    out.push((m, Sv::S(a.last_st)));
                } else if a.counter {
                    out.push((m, Sv::V(a.sum)));
                } else {
                    let ids = *rid.entry(m).or_insert_with(|| {
                        let base = dict.mets[m as usize].0.clone();
                        [dict.met(&format!("{base}:max"), false), dict.met(&format!("{base}:min"), false), dict.met(&format!("{base}:p95"), false)]
                    });
                    a.samples.sort_unstable_by(|x, y| x.total_cmp(y));
                    let rank = ((a.samples.len() as f64) * 0.95).ceil() as usize;
                    let p95 = a.samples[rank.saturating_sub(1).min(a.samples.len() - 1)] as f64;
                    out.push((m, Sv::V(a.sum / a.n as f64)));
                    out.push((ids[0], Sv::V(a.max)));
                    out.push((ids[1], Sv::V(a.min)));
                    out.push((ids[2], Sv::V(p95)));
                }
            }
            body.clear();
            head(&mut body, ts_ms, t.dt_sum as u32, ents.len());
            for (id, vs) in &ents {
                put_varint(&mut body, *id as u64);
                put_varint(&mut body, vs.len() as u64);
                for &(m, v) in vs {
                    put_val(&mut body, m, v);
                }
            }
            t.acc.retain(|_, a| {
                let keep = a.n > 0 || a.ns > 0;
                a.n = 0;
                a.ns = 0;
                a.sum = 0.0;
                a.min = f64::MAX;
                a.max = f64::MIN;
                a.samples.clear();
                keep
            });
            t.n = 0;
            t.dt_sum = 0;
            t.write(ts_ms, &body, cap, w_tmpfs, w_cold);
        }
    }
}

impl TierState {
    fn write(&mut self, ts_ms: u64, body: &[u8], cap: u64, w_tmpfs: &mut u64, w_cold: &mut u64) {
        let comp;
        let payload: &[u8] = if self.disk {
            comp = ruzstd::encoding::compress_to_vec(body, ruzstd::encoding::CompressionLevel::Fastest);
            &comp
        } else {
            body
        };
        let mut frame = Vec::with_capacity(payload.len() + 4);
        frame.extend_from_slice(&(payload.len() as u32).to_le_bytes());
        frame.extend_from_slice(payload);
        let now_s = ts_ms / 1000;
        self.last_write_s = now_s;
        let seg = now_s / self.t.segment_s * self.t.segment_s;
        let mut rolled = false;
        if self.cur.as_ref().map(|c| c.0) != Some(seg) {
            let path = format!("{}/{}-{}.seg", self.dir, self.t.name, seg);
            match OpenOptions::new().create(true).append(true).open(&path) {
                Ok(f) => {
                    self.cur = Some((seg, f));
                    if self.segs.back().map(|s| s.0) != Some(seg) {
                        self.segs.push_back((seg, 0));
                    }
                    rolled = true;
                }
                Err(_) => return,
            }
        }
        if let Some((_, f)) = self.cur.as_mut() {
            if f.write_all(&frame).is_ok() {
                if let Some(l) = self.segs.back_mut() {
                    l.1 += frame.len() as u64;
                }
                self.total += frame.len() as u64;
                if self.disk {
                    *w_cold += frame.len() as u64;
                } else {
                    *w_tmpfs += frame.len() as u64;
                }
            }
        }
        if rolled || self.disk {
            self.prune(now_s, if self.disk { Some(cap) } else { None });
        }
    }

    /// Remove expired segments and, on the disk tier, the oldest data until the byte cap
    /// holds. The cap is strict: when only the open segment is left and it alone exceeds
    /// the cap, it is dropped too (and a new one starts at the next write).
    fn prune(&mut self, now_s: u64, cap: Option<u64>) {
        loop {
            let (s, sz) = match self.segs.front() {
                Some(&x) => x,
                None => break,
            };
            let is_cur = self.cur.as_ref().map(|c| c.0) == Some(s);
            let expired = s + self.t.segment_s + self.t.keep_s < now_s;
            let over = cap.map_or(false, |c| self.total > c);
            if !(expired || over) || (is_cur && !over) {
                break;
            }
            if is_cur {
                self.cur = None;
            }
            let _ = fs::remove_file(format!("{}/{}-{}.seg", self.dir, self.t.name, s));
            self.total = self.total.saturating_sub(sz);
            self.segs.pop_front();
        }
    }
}

// ---------------------------------------------------------------- query side

fn read_frames(path: &str, disk: bool) -> Vec<Vec<u8>> {
    let mut data = Vec::new();
    if let Ok(mut f) = File::open(path) {
        let _ = f.read_to_end(&mut data);
    }
    let mut out = Vec::new();
    let mut p = 0;
    while p + 4 <= data.len() {
        let l = u32::from_le_bytes(data[p..p + 4].try_into().unwrap()) as usize;
        p += 4;
        if p + l > data.len() {
            break; // partial tail (being written)
        }
        let s = &data[p..p + l];
        p += l;
        if disk {
            match ruzstd::decoding::StreamingDecoder::new(s) {
                Ok(mut d) => {
                    let mut b = Vec::new();
                    if d.read_to_end(&mut b).is_ok() {
                        out.push(b);
                    }
                }
                Err(_) => continue,
            }
        } else {
            out.push(s.to_vec());
        }
    }
    out
}

fn decode(b: &[u8], want_ent: u32, want: &[u32]) -> Option<(u64, u32, Vec<(u32, Sv)>)> {
    let ts = u64::from_le_bytes(b.get(0..8)?.try_into().ok()?);
    let dt = u32::from_le_bytes(b.get(8..12)?.try_into().ok()?);
    let mut p = 12;
    let n = get_varint(b, &mut p)?;
    let mut res = Vec::new();
    for _ in 0..n {
        let id = get_varint(b, &mut p)? as u32;
        let k = get_varint(b, &mut p)?;
        for _ in 0..k {
            let (m, v) = get_val(b, &mut p)?;
            if id == want_ent && want.contains(&m) {
                res.push((m, v));
            }
        }
    }
    Some((ts, dt, res))
}

fn push_sv(o: &mut String, v: Sv) {
    match v {
        Sv::V(x) => jnum(o, x),
        Sv::S(c) => {
            o.push('{');
            jstate(o, c);
            o.push('}');
        }
    }
}

pub struct Series {
    /// Fields of the response object, without the surrounding braces.
    pub body: String,
    pub truncated: bool,
    pub window_s: u64,
}

/// Query one metric of one entity from one tier. A point is `[ts_ms, ...fields]`; a
/// state point is `[ts_ms, {"s":..,"why":..}]` (counters: with `dt_ms` last). The answer
/// stops at `max_bytes` with `truncated` set (newest points are kept out, oldest first).
pub fn series(cfg: &Config, entity: &str, metric: &str, tier: &str, window_s: u64, max_bytes: usize) -> Result<Series, PErr> {
    if !cfg.store.enabled {
        return Err(perr("store_disabled", "store disabled"));
    }
    let t = cfg.tier.iter().find(|t| t.name == tier).ok_or_else(|| perr("unknown_tier", format!("unknown tier {tier}")))?;
    let window_s = window_s.min(t.keep_s + t.segment_s);
    let since_ms = wall_ms().saturating_sub(window_s * 1000);
    let (mets, ents) = dict::read_lists(&cfg.store.cold_dir);
    let eid = ents.iter().position(|e| e == entity).ok_or_else(|| perr("unknown_entity", format!("unknown entity {entity}")))? as u32;
    let mi = mets.iter().position(|(n, _)| n == metric).ok_or_else(|| perr("unknown_metric", format!("unknown metric {metric}")))?;
    let counter = mets[mi].1;
    let rollup = t.step_s * 1000 != cfg.sampler.tick_ms;
    let mut want = vec![mi as u32];
    let mut have_rollup = true;
    if rollup && !counter {
        for suf in [":max", ":min", ":p95"] {
            let n = format!("{metric}{suf}");
            match mets.iter().position(|(m, _)| *m == n) {
                Some(p) => want.push(p as u32),
                None => have_rollup = false,
            }
        }
    }
    let disk = t.medium == "disk";
    let dir = if disk { &cfg.store.cold_dir } else { &cfg.store.tmpfs_dir };
    let mut segs: Vec<u64> = Vec::new();
    if let Ok(rd) = fs::read_dir(dir) {
        for e in rd.flatten() {
            let n = e.file_name().to_string_lossy().into_owned();
            if let Some(r) = n.strip_prefix(&format!("{tier}-")).and_then(|r| r.strip_suffix(".seg")) {
                if let Ok(s) = r.parse::<u64>() {
                    if (s + t.segment_s) * 1000 >= since_ms {
                        segs.push(s);
                    }
                }
            }
        }
    }
    segs.sort();
    let fields: &[&str] = match (rollup, counter) {
        (false, false) => &["ts_ms", "v"],
        (false, true) => &["ts_ms", "delta", "dt_ms"],
        (true, false) => &["ts_ms", "avg", "max", "min", "p95"],
        (true, true) => &["ts_ms", "sum", "dt_ms"],
    };
    let mut o = String::new();
    o.push_str("\"entity\":");
    jstr(&mut o, entity);
    o.push_str(",\"metric\":");
    jstr(&mut o, metric);
    o.push_str(&format!(",\"tier\":\"{}\",\"step_s\":{},\"kind\":\"{}\",\"fields\":{:?},\"points\":[", tier, t.step_s, if counter { "counter" } else { "gauge" }, fields));
    let mut first = true;
    let mut truncated = false;
    'outer: for s in segs {
        for body in read_frames(&format!("{dir}/{tier}-{s}.seg"), disk) {
            let (ts, dt, vals) = match decode(&body, eid, &want) {
                Some(x) => x,
                None => continue,
            };
            if ts < since_ms {
                continue;
            }
            let get = |m: u32| vals.iter().find(|(k, _)| *k == m).map(|x| x.1);
            let main = match get(want[0]) {
                Some(v) => v,
                None => continue, // not measured in this frame: a gap, not a zero
            };
            let mut row = String::new();
            row.push('[');
            row.push_str(&ts.to_string());
            match main {
                Sv::S(_) => {
                    row.push(',');
                    push_sv(&mut row, main);
                }
                Sv::V(_) if counter => {
                    row.push(',');
                    push_sv(&mut row, main);
                    row.push_str(&format!(",{dt}"));
                }
                Sv::V(_) if rollup && !counter => {
                    for &m in &want {
                        row.push(',');
                        match get(m) {
                            Some(v) => push_sv(&mut row, v),
                            None => row.push_str("null"),
                        }
                    }
                    if !have_rollup {
                        // rollup names not yet in the dictionary: only avg is real
                    }
                }
                Sv::V(_) => {
                    row.push(',');
                    push_sv(&mut row, main);
                }
            }
            if let (true, Sv::S(_)) = (counter, main) {
                row.push_str(&format!(",{dt}"));
            }
            row.push(']');
            if o.len() + row.len() + 64 > max_bytes {
                truncated = true;
                break 'outer;
            }
            if !first {
                o.push(',');
            }
            first = false;
            o.push_str(&row);
        }
    }
    o.push(']');
    Ok(Series { body: o, truncated, window_s })
}

/// Events in the window (newest `limit` of them, oldest first), from the events file and
/// its rotated predecessor. Bounded by `max_bytes`.
pub fn events(cfg: &Config, entity: &str, window_s: u64, limit: usize, max_bytes: usize) -> (String, bool) {
    let since_ms = wall_ms().saturating_sub(window_s * 1000);
    let mut all: Vec<String> = Vec::new();
    for p in [format!("{}.1", cfg.events.events_path), cfg.events.events_path.clone()] {
        let text = fs::read_to_string(&p).unwrap_or_default();
        for l in text.lines() {
            let v: serde_json::Value = match serde_json::from_str(l) {
                Ok(v) => v,
                Err(_) => continue,
            };
            if v["ts_ms"].as_u64().unwrap_or(0) < since_ms || (!entity.is_empty() && v["entity"].as_str() != Some(entity)) {
                continue;
            }
            all.push(l.to_string());
        }
    }
    let mut truncated = false;
    if all.len() > limit {
        all.drain(..all.len() - limit);
        truncated = true;
    }
    let mut o = String::from("[");
    for (i, l) in all.iter().enumerate() {
        if o.len() + l.len() + 16 > max_bytes {
            truncated = true;
            break;
        }
        if i > 0 {
            o.push(',');
        }
        o.push_str(l);
    }
    o.push(']');
    (o, truncated)
}
