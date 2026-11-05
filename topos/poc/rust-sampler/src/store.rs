//! Tiered store: raw/rollup frames appended to segment files. tmpfs tiers are plain, the
//! disk tier is a sequence of independent zstd frames, each prefixed by its length.
//! Frame body: u64 ts_ms | u32 dt_ms | varint n_ents | per entity: varint id, varint n,
//! n x (varint (metric<<1 | is_float), zigzag-varint int | 8 byte f64).
use crate::cfg::{Config, Tier};
use crate::collect::{Event, Val};
use crate::dict::{self, Dict};
use crate::util::*;
use std::collections::{HashMap, VecDeque};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};

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
fn put_val(o: &mut Vec<u8>, m: u32, v: f64) {
    if v.fract() == 0.0 && v.abs() < 9.0e15 {
        put_varint(o, (m as u64) << 1);
        put_varint(o, zz(v as i64));
    } else {
        put_varint(o, ((m as u64) << 1) | 1);
        o.extend_from_slice(&v.to_bits().to_le_bytes());
    }
}
fn get_val(b: &[u8], p: &mut usize) -> Option<(u32, f64)> {
    let h = get_varint(b, p)?;
    let m = (h >> 1) as u32;
    if h & 1 == 0 {
        Some((m, unzz(get_varint(b, p)?) as f64))
    } else {
        let s = b.get(*p..*p + 8)?;
        *p += 8;
        Some((m, f64::from_bits(u64::from_le_bytes(s.try_into().ok()?))))
    }
}
fn head(o: &mut Vec<u8>, ts: u64, dt: u32, nents: usize) {
    o.extend_from_slice(&ts.to_le_bytes());
    o.extend_from_slice(&dt.to_le_bytes());
    put_varint(o, nents as u64);
}

struct Acc {
    n: u32,
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
}

pub struct Store {
    tiers: Vec<TierState>,
    cold_dir: String,
    cap: u64,
    rid: HashMap<u32, [u32; 3]>,
    w_mets: usize,
    w_ents: usize,
    ev_file: File,
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

    /// Persist dictionary names added since the last call (rare: only new names).
    pub fn sync_dict(&mut self, dict: &Dict) {
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
        let mut add = String::new();
        for n in &dict.ents[self.w_ents..] {
            add.push_str(&n.replace('\n', " "));
            add.push('\n');
        }
        if !add.is_empty() {
            if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(format!("{}/ents.dict", self.cold_dir)) {
                let _ = f.write_all(add.as_bytes());
                self.w_other += add.len() as u64;
            }
            self.w_ents = dict.ents.len();
        }
    }

    pub fn append_events(&mut self, evs: &[Event]) {
        for e in evs {
            let _ = self.ev_file.write_all(e.json.as_bytes());
            let _ = self.ev_file.write_all(b"\n");
            self.w_other += e.json.len() as u64 + 1;
        }
    }

    pub fn push(&mut self, frame: &[(u32, Vec<Val>)], ts_ms: u64, dt_ms: u64, dict: &mut Dict) {
        if !self.enabled {
            return;
        }
        let cap = self.cap;
        let mut body = Vec::with_capacity(64 * 1024);
        for ti in 0..self.tiers.len() {
            let Store { tiers, rid, w_tmpfs, w_cold, .. } = self;
            let t = &mut tiers[ti];
            if t.raw {
                body.clear();
                let nents = frame.iter().filter(|(_, v)| v.iter().any(|x| x.raw == NONE || x.v != 0.0)).count();
                head(&mut body, ts_ms, dt_ms as u32, nents);
                for (id, vals) in frame {
                    let n = vals.iter().filter(|x| x.raw == NONE || x.v != 0.0).count();
                    if n == 0 {
                        continue;
                    }
                    put_varint(&mut body, *id as u64);
                    put_varint(&mut body, n as u64);
                    for x in vals {
                        if x.raw == NONE || x.v != 0.0 {
                            put_val(&mut body, x.m, x.v);
                        }
                    }
                }
                t.write(ts_ms, &body, cap, w_tmpfs, w_cold);
                continue;
            }
            for (id, vals) in frame {
                for x in vals {
                    let a = t.acc.entry((*id, x.m)).or_insert_with(|| Acc {
                        n: 0, sum: 0.0, min: f64::MAX, max: f64::MIN, counter: x.raw != NONE, samples: Vec::new(),
                    });
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
            let mut keys: Vec<((u32, u32), &mut Acc)> = t.acc.iter_mut().filter(|(_, a)| a.n > 0).map(|(k, a)| (*k, a)).collect();
            keys.sort_unstable_by_key(|(k, _)| *k);
            let mut ents: Vec<(u32, Vec<(u32, f64)>)> = Vec::new();
            for ((e, m), a) in keys {
                if ents.last().map(|l| l.0) != Some(*&e) {
                    ents.push((e, Vec::new()));
                }
                let out = &mut ents.last_mut().unwrap().1;
                if a.counter {
                    if a.sum != 0.0 {
                        out.push((m, a.sum));
                    }
                } else {
                    let ids = *rid.entry(m).or_insert_with(|| {
                        let base = dict.mets[m as usize].0.clone();
                        [dict.met(&format!("{base}:max"), false), dict.met(&format!("{base}:min"), false), dict.met(&format!("{base}:p95"), false)]
                    });
                    a.samples.sort_unstable_by(|x, y| x.total_cmp(y));
                    let rank = ((a.samples.len() as f64) * 0.95).ceil() as usize;
                    let p95 = a.samples[rank.saturating_sub(1).min(a.samples.len() - 1)] as f64;
                    out.push((m, a.sum / a.n as f64));
                    out.push((ids[0], a.max));
                    out.push((ids[1], a.min));
                    out.push((ids[2], p95));
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
                let keep = a.n > 0;
                a.n = 0;
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

    fn prune(&mut self, now_s: u64, cap: Option<u64>) {
        let cur = self.cur.as_ref().map(|c| c.0);
        while let Some(&(s, sz)) = self.segs.front() {
            if Some(s) == cur {
                break;
            }
            let expired = s + self.t.segment_s + self.t.keep_s < now_s;
            let over = cap.map_or(false, |c| self.total > c);
            if !(expired || over) {
                break;
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

fn decode(b: &[u8], want_ent: u32, want: &[u32]) -> Option<(u64, u32, Vec<(u32, f64)>)> {
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

pub fn series(cfg: &Config, entity: &str, metric: &str, tier: &str, window_s: u64) -> Result<String, String> {
    if !cfg.store.enabled {
        return Err("store disabled".into());
    }
    let t = cfg.tier.iter().find(|t| t.name == tier).ok_or("unknown tier")?;
    let now_ms = wall_ms();
    let since_ms = now_ms.saturating_sub(window_s * 1000);
    let mut o = String::new();
    o.push_str("{\"req\":\"series\",\"entity\":");
    jstr(&mut o, entity);
    o.push_str(",\"metric\":");
    jstr(&mut o, metric);
    o.push_str(",\"tier\":");
    jstr(&mut o, tier);
    if metric == "@events" {
        o.push_str(",\"events\":[");
        let text = fs::read_to_string(&cfg.events.events_path).unwrap_or_default();
        let mut first = true;
        for l in text.lines() {
            let v: serde_json::Value = match serde_json::from_str(l) {
                Ok(v) => v,
                Err(_) => continue,
            };
            if v["ts_ms"].as_u64().unwrap_or(0) < since_ms || (!entity.is_empty() && v["entity"].as_str() != Some(entity)) {
                continue;
            }
            if !first {
                o.push(',');
            }
            first = false;
            o.push_str(l);
        }
        o.push_str("]}");
        return Ok(o);
    }
    let (mets, ents) = dict::read_lists(&cfg.store.cold_dir);
    let eid = ents.iter().position(|e| e == entity).ok_or("unknown entity")? as u32;
    let mi = mets.iter().position(|(n, _)| n == metric).ok_or("unknown metric")?;
    let counter = mets[mi].1;
    let rollup = t.step_s * 1000 != cfg.sampler.tick_ms;
    let mut want = vec![mi as u32];
    if rollup && !counter {
        for suf in [":max", ":min", ":p95"] {
            let n = format!("{metric}{suf}");
            want.push(mets.iter().position(|(m, _)| *m == n).ok_or("rollup metric missing")? as u32);
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
    o.push_str(&format!(",\"kind\":\"{}\",\"fields\":{:?},\"points\":[", if counter { "counter" } else { "gauge" }, fields));
    let mut first = true;
    for s in segs {
        for body in read_frames(&format!("{dir}/{tier}-{s}.seg"), disk) {
            let (ts, dt, vals) = match decode(&body, eid, &want) {
                Some(x) => x,
                None => continue,
            };
            if ts < since_ms {
                continue;
            }
            let get = |m: u32| vals.iter().find(|(k, _)| *k == m).map(|x| x.1);
            let row: Vec<f64> = if counter {
                vec![ts as f64, get(want[0]).unwrap_or(0.0), dt as f64]
            } else {
                let mut r = vec![ts as f64];
                for &m in &want {
                    match get(m) {
                        Some(v) => r.push(v),
                        None => {
                            r.clear();
                            break;
                        }
                    }
                }
                r
            };
            if row.is_empty() {
                continue;
            }
            if !first {
                o.push(',');
            }
            first = false;
            o.push('[');
            for (i, v) in row.iter().enumerate() {
                if i > 0 {
                    o.push(',');
                }
                jnum(&mut o, *v);
            }
            o.push(']');
        }
    }
    o.push_str("]}");
    Ok(o)
}
