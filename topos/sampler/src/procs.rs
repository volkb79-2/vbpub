//! Process scan. `stat` is read for every PID every tick (it carries state, CPU time,
//! RSS, major faults and block-IO delay ticks); `io`, `schedstat` are read only for
//! candidates: PIDs in the previous enriched union, PIDs whose CPU time rose, D-state
//! PIDs and new PIDs, plus every PID on a full sweep (a slower configured cadence), so
//! an IO-hot process that burns no CPU is still discovered, at most
//! `full_sweep_every_s` late. `cmdline` and the owning cgroup are read only for the
//! enriched union (and for D-state PIDs, cgroup only, for per-cgroup D counts).
use crate::cfg::Config;
use crate::util::*;
use std::collections::HashMap;
use std::fmt::Write;
use std::fs;

/// A value with a state (see `stc`).
#[derive(Clone, Copy, Debug)]
pub struct PV {
    pub v: f64,
    pub st: u8,
}
impl PV {
    pub fn val(v: f64) -> PV {
        PV { v, st: stc::VALUE }
    }
    pub fn st(st: u8) -> PV {
        PV { v: 0.0, st }
    }
}

#[derive(Clone, Copy)]
struct PRec {
    pid: u32,
    start: u64,
    comm: [u8; 16],
    clen: u8,
    state: u8,
    cpu_d: u64,
    rss: u64,
    thr: u32,
    cpu_pct: PV,
    majflt: PV,
    blk: PV,
    ior: PV,
    iow: PV,
    rundelay: PV,
    io_rank: f64,
}

#[derive(Default)]
struct PS {
    cpu: u64,
    majflt: u64,
    rb: u64,
    wb: u64,
    wait_ns: u64,
    io_t: u64,
    have_io: bool,
    have_sched: bool,
    enriched: bool,
    cgroup: String,
    cmd: String,
    meta_tick: u64,
    seen: u64,
    blk: u64,
}

pub struct ProcOut {
    pub pid: u32,
    pub start: u64,
    pub comm: String,
    pub state: char,
    pub cgroup: String,
    pub cmdline: String,
    pub cpu_pct: PV,
    pub rss_bytes: u64,
    pub threads: u32,
    pub majflt_ps: PV,
    pub io_read_bps: PV,
    pub io_write_bps: PV,
    pub run_delay_ms_ps: PV,
    pub blkio_delay_ms_ps: PV,
    pub why: &'static str,
}

#[derive(Default)]
pub struct PScan {
    map: HashMap<(u32, u64), PS>,
    recs: Vec<PRec>,
    mark: Vec<u8>,
    pub out: Vec<ProcOut>,
    pub total: u32,
    pub dstate: u32,
    pub io_denied: u64,
    pub io_read: u32,
    pub swept: bool,
    /// D-state task count per owning cgroup (key = path relative to the cgroup root).
    pub dmap: HashMap<String, u32>,
    pub d_unattributed: u32,
    pub scanned: bool,
}

fn top(idx: &mut Vec<usize>, k: usize, key: impl Fn(usize) -> f64) {
    let cmp = |a: &usize, b: &usize| key(*b).total_cmp(&key(*a));
    if idx.len() > k {
        idx.select_nth_unstable_by(k, cmp);
        idx.truncate(k);
    }
    idx.sort_unstable_by(cmp);
}

fn cgroup_of(slurp: &mut Slurp, path: &str) -> Option<String> {
    let b = slurp.read(path).ok()?;
    let l = lines(b).find(|l| l.starts_with(b"0::"))?;
    let s = String::from_utf8_lossy(&l[3..]).into_owned();
    Some(if s == "/" { String::new() } else { s })
}

pub fn ent_of_rel(rel: &str) -> String {
    if rel.is_empty() { "cg:/".to_string() } else { format!("cg:{rel}") }
}

pub struct ScanIn {
    pub tick: u64,
    pub dt_ms: u64,
    pub now_ms: u64,
    pub sweep: bool,
    pub delayacct: Option<bool>,
    pub clk_tck: u64,
    pub page_size: u64,
}

impl PScan {
    pub fn scan(&mut self, cfg: &Config, slurp: &mut Slurp, pbuf: &mut String, si: &ScanIn) {
        let root = cfg.sampler.proc_root.as_str();
        let dt_s = (si.dt_ms.max(1)) as f64 / 1000.0;
        let ck = si.clk_tck as f64;
        self.recs.clear();
        self.io_denied = 0;
        self.io_read = 0;
        self.swept = si.sweep;
        self.scanned = true;
        let rd = match fs::read_dir(root) {
            Ok(r) => r,
            Err(_) => {
                self.scanned = false;
                self.total = 0;
                return;
            }
        };
        for e in rd.flatten() {
            let name = e.file_name();
            let pid = match parse_u64(name.as_encoded_bytes()) {
                Some(p) => p as u32,
                None => continue,
            };
            pbuf.clear();
            let _ = write!(pbuf, "{root}/{pid}/stat");
            let b = match slurp.read(pbuf) {
                Ok(b) => b,
                Err(_) => continue,
            };
            let (op, cp) = match (b.iter().position(|&c| c == b'('), b.iter().rposition(|&c| c == b')')) {
                (Some(o), Some(c)) if c > o => (o, c),
                _ => continue,
            };
            let mut rec = PRec {
                pid,
                start: 0,
                comm: [0; 16],
                clen: 0,
                state: b'?',
                cpu_d: 0,
                rss: 0,
                thr: 0,
                cpu_pct: PV::st(stc::UN_FIRST),
                majflt: PV::st(stc::UN_FIRST),
                blk: PV::st(stc::UN_FIRST),
                ior: PV::st(stc::UN_NOT_ENRICHED),
                iow: PV::st(stc::UN_NOT_ENRICHED),
                rundelay: PV::st(stc::UN_NOT_ENRICHED),
                io_rank: 0.0,
            };
            let cl = (cp - op - 1).min(16);
            rec.comm[..cl].copy_from_slice(&b[op + 1..op + 1 + cl]);
            rec.clen = cl as u8;
            let (mut ut, mut st, mut majflt, mut blk) = (0u64, 0u64, 0u64, 0u64);
            // Fields after ")": index 0 = state (field 3). majflt = field 12, utime 14,
            // stime 15, num_threads 20, starttime 22, rss 24, delayacct_blkio_ticks 42.
            for (i, t) in b[(cp + 2).min(b.len())..].split(|&c| c == b' ').enumerate() {
                match i {
                    0 => rec.state = t.first().copied().unwrap_or(b'?'),
                    9 => majflt = parse_u64(t).unwrap_or(0),
                    11 => ut = parse_u64(t).unwrap_or(0),
                    12 => st = parse_u64(t).unwrap_or(0),
                    17 => rec.thr = parse_u64(t).unwrap_or(0) as u32,
                    19 => rec.start = parse_u64(t).unwrap_or(0),
                    21 => rec.rss = parse_u64(trim(t)).unwrap_or(0),
                    39 => {
                        blk = parse_u64(trim(t)).unwrap_or(0);
                        break;
                    }
                    _ => {}
                }
            }
            let cpu = ut + st;
            let key = (pid, rec.start);
            let known = self.map.get(&key);
            let (prev_cpu, prev_enriched) = known.map(|p| (Some(p.cpu), p.enriched)).unwrap_or((None, false));
            if let Some(pc) = prev_cpu {
                rec.cpu_d = cpu.saturating_sub(pc);
                rec.cpu_pct = PV::val(rec.cpu_d as f64 / ck / dt_s * 100.0);
            }
            let candidate = si.sweep || known.is_none() || prev_enriched || rec.cpu_d > 0 || rec.state == b'D';
            let mut io: Option<Result<(u64, u64), u8>> = None;
            let mut sched: Option<u64> = None;
            if candidate {
                self.io_read += 1;
                pbuf.clear();
                let _ = write!(pbuf, "{root}/{pid}/io");
                io = Some(match slurp.read(pbuf) {
                    Ok(b) => {
                        let (mut r, mut w) = (0u64, 0u64);
                        for l in lines(b) {
                            if let Some((k, v)) = l.iter().position(|&c| c == b' ').map(|p| (&l[..p], &l[p + 1..])) {
                                if k == b"read_bytes:" {
                                    r = parse_u64(trim(v)).unwrap_or(0);
                                } else if k == b"write_bytes:" {
                                    w = parse_u64(trim(v)).unwrap_or(0);
                                }
                            }
                        }
                        Ok((r, w))
                    }
                    Err(e) => {
                        let c = err_state(&e, stc::UN_READ);
                        if c == stc::UN_PERM {
                            self.io_denied += 1;
                        }
                        Err(c)
                    }
                });
                pbuf.clear();
                let _ = write!(pbuf, "{root}/{pid}/schedstat");
                if let Ok(b) = slurp.read(pbuf) {
                    sched = fields(b).nth(1).and_then(parse_u64);
                }
            }
            if !self.map.contains_key(&key) {
                self.map.insert(key, PS::default());
            }
            let p = self.map.get_mut(&key).unwrap();
            if p.seen != 0 || prev_cpu.is_some() {
                // majflt and blkio use the stat interval
                if p.majflt <= majflt {
                    rec.majflt = PV::val((majflt - p.majflt) as f64 / dt_s);
                }
                rec.blk = match si.delayacct {
                    Some(true) if p.blk <= blk => PV::val((blk - p.blk) as f64 * 1000.0 / ck / dt_s),
                    Some(false) => PV::st(stc::NA_DELAYACCT),
                    _ => PV::st(stc::UN_READ),
                };
            } else if si.delayacct == Some(false) {
                rec.blk = PV::st(stc::NA_DELAYACCT);
            }
            p.cpu = cpu;
            p.majflt = majflt;
            p.blk = blk;
            p.seen = si.tick;
            let el_s = (si.now_ms.saturating_sub(p.io_t)).max(1) as f64 / 1000.0;
            match io {
                Some(Ok((r, w))) => {
                    if p.have_io && r >= p.rb && w >= p.wb {
                        rec.ior = PV::val((r - p.rb) as f64 / el_s);
                        rec.iow = PV::val((w - p.wb) as f64 / el_s);
                        rec.io_rank = rec.ior.v + rec.iow.v;
                    } else {
                        rec.ior = PV::st(stc::UN_FIRST);
                        rec.iow = PV::st(stc::UN_FIRST);
                    }
                    p.rb = r;
                    p.wb = w;
                    p.have_io = true;
                    if let Some(wait) = sched {
                        if p.have_sched && wait >= p.wait_ns {
                            rec.rundelay = PV::val((wait - p.wait_ns) as f64 / 1e6 / el_s);
                        } else {
                            rec.rundelay = PV::st(stc::UN_FIRST);
                        }
                        p.wait_ns = wait;
                        p.have_sched = true;
                    } else {
                        rec.rundelay = PV::st(stc::UN_READ);
                    }
                    p.io_t = si.now_ms;
                }
                Some(Err(c)) => {
                    rec.ior = PV::st(c);
                    rec.iow = PV::st(c);
                    // schedstat is world-readable in practice but follows the same file rules
                    if let Some(wait) = sched {
                        if p.have_sched && wait >= p.wait_ns {
                            rec.rundelay = PV::val((wait - p.wait_ns) as f64 / 1e6 / el_s);
                        }
                        p.wait_ns = wait;
                        p.have_sched = true;
                        p.io_t = si.now_ms;
                    }
                }
                None => {}
            }
            self.recs.push(rec);
        }
        let tick = si.tick;
        self.map.retain(|_, p| p.seen == tick);
        self.total = self.recs.len() as u32;
        self.dstate = self.recs.iter().filter(|r| r.state == b'D').count() as u32;
        for p in self.map.values_mut() {
            p.enriched = false;
        }
        // Bounded enriched union: all D (cap), then top CPU and top IO interleaved.
        let n = self.recs.len();
        let recs = &self.recs;
        let mut cpu_i: Vec<usize> = (0..n).filter(|&i| recs[i].cpu_d > 0).collect();
        top(&mut cpu_i, cfg.process.top_cpu, |i| recs[i].cpu_d as f64);
        let mut io_i: Vec<usize> = (0..n).filter(|&i| recs[i].io_rank > 0.0).collect();
        top(&mut io_i, cfg.process.top_io, |i| recs[i].io_rank);
        self.mark.clear();
        self.mark.resize(n, 0);
        let mut chosen: Vec<(usize, &'static str)> = Vec::new();
        let cap = cfg.process.cap;
        for i in 0..n {
            if recs[i].state == b'D' && chosen.len() < cap {
                self.mark[i] = 1;
                chosen.push((i, "D"));
            }
        }
        for k in 0..cpu_i.len().max(io_i.len()) {
            for (list, why) in [(&cpu_i, "cpu"), (&io_i, "io")] {
                if let Some(&i) = list.get(k) {
                    if self.mark[i] == 0 && chosen.len() < cap {
                        self.mark[i] = 1;
                        chosen.push((i, why));
                    }
                }
            }
        }
        self.out.clear();
        self.dmap.clear();
        self.d_unattributed = 0;
        let mut d_reads = 0usize;
        // D-state tasks beyond the cap still get their cgroup attributed, bounded by 4x cap.
        for i in 0..n {
            if recs[i].state == b'D' && self.mark[i] == 0 {
                if d_reads >= cap * 4 {
                    self.d_unattributed += 1;
                    continue;
                }
                d_reads += 1;
                let r = recs[i];
                let key = (r.pid, r.start);
                let cached = self.map.get(&key).filter(|p| p.meta_tick != 0).map(|p| p.cgroup.clone());
                let rel = match cached {
                    Some(c) => Some(c),
                    None => {
                        pbuf.clear();
                        let _ = write!(pbuf, "{root}/{}/cgroup", r.pid);
                        cgroup_of(slurp, pbuf)
                    }
                };
                match rel {
                    Some(c) => *self.dmap.entry(c).or_insert(0) += 1,
                    None => self.d_unattributed += 1,
                }
            }
        }
        for (i, why) in chosen {
            let r = recs[i];
            let key = (r.pid, r.start);
            let p = self.map.get_mut(&key).unwrap();
            p.enriched = true;
            if p.meta_tick == 0 || si.sweep {
                pbuf.clear();
                let _ = write!(pbuf, "{root}/{}/cgroup", r.pid);
                if let Some(c) = cgroup_of(slurp, pbuf) {
                    p.cgroup = c;
                }
                pbuf.clear();
                let _ = write!(pbuf, "{root}/{}/cmdline", r.pid);
                if let Ok(b) = slurp.read(pbuf) {
                    let take = &b[..b.len().min(cfg.process.cmdline_max)];
                    let s: Vec<u8> = take.iter().map(|&c| if c == 0 { b' ' } else { c }).collect();
                    p.cmd = String::from_utf8_lossy(&s).trim_end().to_string();
                }
                p.meta_tick = si.tick;
            }
            if r.state == b'D' {
                *self.dmap.entry(p.cgroup.clone()).or_insert(0) += 1;
            }
            self.out.push(ProcOut {
                pid: r.pid,
                start: r.start,
                comm: String::from_utf8_lossy(&r.comm[..r.clen as usize]).into_owned(),
                state: r.state as char,
                cgroup: ent_of_rel(&p.cgroup),
                cmdline: p.cmd.clone(),
                cpu_pct: r.cpu_pct,
                rss_bytes: r.rss * si.page_size,
                threads: r.thr,
                majflt_ps: r.majflt,
                io_read_bps: r.ior,
                io_write_bps: r.iow,
                run_delay_ms_ps: r.rundelay,
                blkio_delay_ms_ps: r.blk,
                why,
            });
        }
    }
}
