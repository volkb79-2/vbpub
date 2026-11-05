//! Per-tick collection: cgroup tree, host files, process scan. No process spawns; only
//! open/read/close on /sys and /proc plus directory listings.
use crate::cfg::Config;
use crate::dict::Dict;
use crate::util::*;
use std::collections::HashMap;
use std::fs;
use std::time::Instant;

/// One sampled value. `raw == NONE` marks a gauge; otherwise `v` is the counter delta
/// over the tick and `raw` the raw counter reading.
#[derive(Clone, Copy)]
pub struct Val {
    pub m: u32,
    pub v: f64,
    pub raw: u64,
}

pub struct Event {
    pub ts_ms: u64,
    pub json: String,
}

pub fn mk_event(start: Instant, kind: &str, entity: &str, detail: &str) -> Event {
    let ts = wall_ms();
    let mut j = String::with_capacity(160);
    j.push_str(&format!("{{\"ts_ms\":{},\"mono_ms\":{},\"type\":", ts, start.elapsed().as_millis()));
    jstr(&mut j, kind);
    j.push_str(",\"entity\":");
    jstr(&mut j, entity);
    j.push_str(",\"detail\":");
    j.push_str(detail);
    j.push('}');
    Event { ts_ms: ts, json: j }
}

fn ent_name(rel: &str) -> String {
    if rel.is_empty() {
        "cg:/".to_string()
    } else {
        format!("cg:{rel}")
    }
}

// ---------------------------------------------------------------- names and ids

const K_CPUSTAT: usize = 0;
const K_MEMSTAT: usize = 1;
const K_MEMEV: usize = 2;
const K_MEMINFO: usize = 3;
const K_VMSTAT: usize = 4;
const PREFIX: [&str; 5] = ["cpu.", "mem.", "memev.", "meminfo.", "vm."];
const MEMINFO_KEYS: [&str; 17] = [
    "MemTotal", "MemFree", "MemAvailable", "Buffers", "Cached", "SwapTotal", "SwapFree", "Dirty",
    "Writeback", "AnonPages", "Mapped", "Shmem", "Slab", "SReclaimable", "SUnreclaim", "Zswap",
    "Zswapped",
];
const VM_KEYS: [&str; 14] = [
    "pgfault", "pgmajfault", "pswpin", "pswpout", "pgscan_direct", "pgscan_kswapd",
    "pgsteal_direct", "pgsteal_kswapd", "oom_kill", "zswpin", "zswpout", "zswpwb", "allocstall_normal",
    "pgpgin",
];

fn is_mem_counter(k: &str) -> bool {
    k.starts_with("pg") || k.starts_with("thp_") || k.starts_with("zswp") || k.starts_with("workingset_")
}

pub struct Names {
    pub dict: Dict,
    kc: [HashMap<Vec<u8>, (u32, bool)>; 5],
}

impl Names {
    fn key(&mut self, kind: usize, key: &[u8]) -> Option<(u32, bool)> {
        if let Some(&(id, c)) = self.kc[kind].get(key) {
            return if id == u32::MAX { None } else { Some((id, c)) };
        }
        let ks = String::from_utf8_lossy(key).into_owned();
        let counter = match kind {
            K_CPUSTAT | K_MEMEV => Some(true),
            K_MEMSTAT => Some(is_mem_counter(&ks)),
            K_MEMINFO => MEMINFO_KEYS.contains(&ks.as_str()).then_some(false),
            K_VMSTAT => VM_KEYS.contains(&ks.as_str()).then_some(true),
            _ => None,
        };
        match counter {
            Some(c) => {
                let id = self.dict.met(&format!("{}{}", PREFIX[kind], ks), c);
                self.kc[kind].insert(key.to_vec(), (id, c));
                Some((id, c))
            }
            None => {
                self.kc[kind].insert(key.to_vec(), (u32::MAX, false));
                None
            }
        }
    }
}

pub struct Ids {
    mem_current: u32,
    pids_current: u32,
    io: [u32; 6],
    io_vrate: u32,
    psi: [u32; 12], // res*4 + kind*2 + (0 avg10 | 1 total)
    lim: [u32; 10],
    cpu: [u32; 8],
    cpu_busy: u32,
    procs_running: u32,
    procs_blocked: u32,
    ctxt: u32,
    forks: u32,
    load: [u32; 3],
    dk: [u32; 8],
    dk_inflight: u32,
    dk_await: u32,
    dk_util: u32,
}

impl Ids {
    fn new(d: &mut Dict) -> Ids {
        let mut io = [0; 6];
        for (i, n) in ["rbytes", "wbytes", "rios", "wios", "dbytes", "dios"].iter().enumerate() {
            io[i] = d.met(&format!("io.{n}"), true);
        }
        let mut psi = [0; 12];
        for (r, rn) in ["cpu", "memory", "io"].iter().enumerate() {
            for (k, kn) in ["some", "full"].iter().enumerate() {
                psi[r * 4 + k * 2] = d.met(&format!("psi.{rn}.{kn}.avg10"), false);
                psi[r * 4 + k * 2 + 1] = d.met(&format!("psi.{rn}.{kn}.total"), true);
            }
        }
        let ln = [
            "limit.memory.min", "limit.memory.low", "limit.memory.high", "limit.memory.max",
            "limit.memory.swap.max", "limit.cpu.max.quota", "limit.cpu.weight", "limit.io.max.capped",
            "limit.io.weight", "limit.cpu.max.period",
        ];
        let mut lim = [0; 10];
        for i in 0..10 {
            lim[i] = d.met(ln[i], false);
        }
        let mut cpu = [0; 8];
        for (i, n) in ["user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal"].iter().enumerate() {
            cpu[i] = d.met(&format!("cpu.{n}"), true);
        }
        let mut dk = [0; 8];
        for (i, n) in ["reads", "rd_sectors", "rd_ms", "writes", "wr_sectors", "wr_ms", "io_ticks_ms", "time_in_queue_ms"]
            .iter()
            .enumerate()
        {
            dk[i] = d.met(&format!("disk.{n}"), true);
        }
        Ids {
            mem_current: d.met("mem.current", false),
            pids_current: d.met("pids.current", false),
            io,
            io_vrate: d.met("io.cost.vrate", false),
            psi,
            lim,
            cpu,
            cpu_busy: d.met("cpu.busy_pct", false),
            procs_running: d.met("host.procs_running", false),
            procs_blocked: d.met("host.procs_blocked", false),
            ctxt: d.met("host.ctxt", true),
            forks: d.met("host.forks", true),
            load: [d.met("host.load1", false), d.met("host.load5", false), d.met("host.load15", false)],
            dk,
            dk_inflight: d.met("disk.inflight", false),
            dk_await: d.met("disk.await_ms", false),
            dk_util: d.met("disk.util_pct", false),
        }
    }
}

// ---------------------------------------------------------------- emit helper

pub struct Em<'a> {
    out: &'a mut Vec<Val>,
    prev: &'a mut Vec<u64>,
    base_zero: bool,
    resets: &'a mut u64,
}

impl Em<'_> {
    fn gauge(&mut self, m: u32, v: f64) {
        self.out.push(Val { m, v, raw: NONE });
    }
    /// Counter with reset handling: a decrease means the counter restarted, so the new
    /// reading is the delta since the restart. The first observation has no delta, except
    /// for an entity born after sampling started (its counters began at zero).
    fn counter(&mut self, m: u32, cur: u64) {
        let i = m as usize;
        if self.prev.len() <= i {
            self.prev.resize(i + 1, NONE);
        }
        let mut p = self.prev[i];
        self.prev[i] = cur;
        if p == NONE {
            if self.base_zero {
                p = 0;
            } else {
                return;
            }
        }
        let d = if cur >= p {
            cur - p
        } else {
            *self.resets += 1;
            cur
        };
        self.out.push(Val { m, v: d as f64, raw: cur });
    }
}

pub struct Ent {
    pub id: u32,
    prev: Vec<u64>,
    vals: Vec<Val>,
    base_zero: bool,
    seen: u64,
}

impl Ent {
    fn new(id: u32, base_zero: bool) -> Ent {
        Ent { id, prev: Vec::new(), vals: Vec::new(), base_zero, seen: 0 }
    }
}

struct CgState {
    e: Ent,
    missing: u16,
    serial: u32,
    fresh: bool,
    limits: [Option<Vec<u8>>; 9],
    psi_above: [Option<bool>; 3],
}

const CG_FILES: [&str; 9] = [
    "cpu.stat", "memory.current", "memory.stat", "memory.events", "io.stat", "cpu.pressure",
    "memory.pressure", "io.pressure", "pids.current",
];
const SLOW_FILES: [&str; 9] = [
    "memory.min", "memory.low", "memory.high", "memory.max", "memory.swap.max", "cpu.max", "cpu.weight",
    "io.max", "io.weight",
];

fn split_sp(l: &[u8]) -> Option<(&[u8], &[u8])> {
    let p = l.iter().position(|&c| c == b' ')?;
    Some((&l[..p], &l[p + 1..]))
}
fn split_eq(l: &[u8]) -> Option<(&[u8], &[u8])> {
    let p = l.iter().position(|&c| c == b'=')?;
    Some((&l[..p], &l[p + 1..]))
}

/// Bottom-level device rule: a device with a non-empty `slaves` directory is a stacked
/// device (dm, md, ...) whose IO is already counted on the devices below it.
fn is_bottom(cache: &mut HashMap<Vec<u8>, bool>, sys_root: &str, dev: &[u8]) -> bool {
    if let Some(&b) = cache.get(dev) {
        return b;
    }
    let d = String::from_utf8_lossy(dev);
    match fs::read_dir(format!("{sys_root}/dev/block/{d}/slaves")) {
        Ok(mut rd) => {
            let b = rd.next().is_none();
            cache.insert(dev.to_vec(), b);
            b
        }
        // No slaves directory at all (or unknown device): count it, retry next tick.
        Err(_) => true,
    }
}

fn limit_vals(j: usize, t: &[u8]) -> ([(usize, f64); 2], usize) {
    let num = |x: &[u8]| -> Option<f64> {
        if x == b"max" {
            Some(-1.0)
        } else {
            parse_f64(x)
        }
    };
    let r = [(0usize, 0.0f64); 2];
    match j {
        0..=4 | 6 => match num(t) {
            Some(v) => ([(j, v), (0, 0.0)], 1),
            None => (r, 0),
        },
        5 => {
            let mut it = fields(t);
            let (a, b) = (it.next(), it.next());
            match (a.and_then(num), b.and_then(num)) {
                (Some(q), Some(p)) => ([(5, q), (9, p)], 2),
                _ => (r, 0),
            }
        }
        7 => {
            let mut capped = 0.0;
            for l in lines(t) {
                for kv in fields(l).skip(1) {
                    if let Some((_, v)) = split_eq(kv) {
                        if v != b"max" && !v.is_empty() {
                            capped = 1.0;
                        }
                    }
                }
            }
            ([(7, capped), (0, 0.0)], 1)
        }
        _ => {
            for l in lines(t) {
                let mut it = fields(l);
                if it.next() == Some(b"default") {
                    if let Some(v) = it.next().and_then(parse_f64) {
                        return ([(8, v), (0, 0.0)], 1);
                    }
                }
            }
            (r, 0)
        }
    }
}

// ---------------------------------------------------------------- processes

#[derive(Clone, Copy)]
struct PRec {
    pid: u32,
    start: u64,
    comm: [u8; 16],
    clen: u8,
    state: u8,
    cpu_d: u64,
    io_d: u64,
    rss: u64,
    thr: u32,
}
struct PS {
    cpu: u64,
    io: u64,
    seen: u64,
}

pub struct ProcOut {
    pub pid: u32,
    pub start: u64,
    pub comm: String,
    pub state: char,
    pub cgroup: String,
    pub cpu_pct: f64,
    pub io_bps: f64,
    pub rss_bytes: u64,
    pub threads: u32,
    pub why: &'static str,
}

#[derive(Default)]
pub struct PScan {
    map: HashMap<(u32, u64), PS>,
    recs: Vec<PRec>,
    pub out: Vec<ProcOut>,
    pub total: u32,
    pub dstate: u32,
    pub denied: u64,
    mark: Vec<u8>,
}

fn top(idx: &mut Vec<usize>, k: usize, key: impl Fn(usize) -> u64) {
    if idx.len() > k {
        idx.select_nth_unstable_by(k, |&a, &b| key(b).cmp(&key(a)));
        idx.truncate(k);
    }
    idx.sort_unstable_by(|&a, &b| key(b).cmp(&key(a)));
}

impl PScan {
    fn scan(&mut self, cfg: &Config, slurp: &mut Slurp, pbuf: &mut String, tick: u64, dt: f64) {
        use std::fmt::Write;
        let root = &cfg.proc_root_ref();
        self.recs.clear();
        self.denied = 0;
        let rd = match fs::read_dir(root) {
            Ok(r) => r,
            Err(_) => return,
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
                pid, start: 0, comm: [0; 16], clen: 0, state: b'?', cpu_d: 0, io_d: 0, rss: 0, thr: 0,
            };
            let cl = (cp - op - 1).min(16);
            rec.comm[..cl].copy_from_slice(&b[op + 1..op + 1 + cl]);
            rec.clen = cl as u8;
            let (mut ut, mut st) = (0u64, 0u64);
            for (i, t) in b[(cp + 2).min(b.len())..].split(|&c| c == b' ').enumerate() {
                match i {
                    0 => rec.state = t.first().copied().unwrap_or(b'?'),
                    11 => ut = parse_u64(t).unwrap_or(0),
                    12 => st = parse_u64(t).unwrap_or(0),
                    17 => rec.thr = parse_u64(t).unwrap_or(0) as u32,
                    19 => rec.start = parse_u64(t).unwrap_or(0),
                    21 => {
                        rec.rss = parse_u64(trim(t)).unwrap_or(0);
                        break;
                    }
                    _ => {}
                }
            }
            let cpu = ut + st;
            // io: read_bytes + write_bytes (needs ptrace access; counted when denied)
            pbuf.clear();
            let _ = write!(pbuf, "{root}/{pid}/io");
            let mut io = 0u64;
            match slurp.read(pbuf) {
                Ok(b) => {
                    for l in lines(b) {
                        if let Some((k, v)) = split_sp(l) {
                            if k == b"read_bytes:" || k == b"write_bytes:" {
                                io += parse_u64(trim(v)).unwrap_or(0);
                            }
                        }
                    }
                }
                Err(_) => self.denied += 1,
            }
            // schedstat: presence check only for the PoC cost model (run/wait ns)
            pbuf.clear();
            let _ = write!(pbuf, "{root}/{pid}/schedstat");
            let _ = slurp.read(pbuf);
            let key = (pid, rec.start);
            match self.map.get_mut(&key) {
                Some(p) => {
                    rec.cpu_d = cpu.saturating_sub(p.cpu);
                    rec.io_d = io.saturating_sub(p.io);
                    p.cpu = cpu;
                    p.io = io;
                    p.seen = tick;
                }
                None => {
                    self.map.insert(key, PS { cpu, io, seen: tick });
                }
            }
            self.recs.push(rec);
        }
        self.map.retain(|_, p| p.seen == tick);
        self.total = self.recs.len() as u32;
        self.dstate = self.recs.iter().filter(|r| r.state == b'D').count() as u32;
        // bounded enriched union: all D, then top CPU / top IO interleaved, hard cap
        let n = self.recs.len();
        let recs = &self.recs;
        let mut cpu_i: Vec<usize> = (0..n).filter(|&i| recs[i].cpu_d > 0).collect();
        top(&mut cpu_i, cfg.process.top_cpu, |i| recs[i].cpu_d);
        let mut io_i: Vec<usize> = (0..n).filter(|&i| recs[i].io_d > 0).collect();
        top(&mut io_i, cfg.process.top_io, |i| recs[i].io_d);
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
        for (i, why) in chosen {
            let r = recs[i];
            pbuf.clear();
            let _ = write!(pbuf, "{root}/{}/cgroup", r.pid);
            let cg = match slurp.read(pbuf) {
                Ok(b) => lines(b)
                    .find(|l| l.starts_with(b"0::"))
                    .map(|l| String::from_utf8_lossy(&l[3..]).into_owned())
                    .unwrap_or_default(),
                Err(_) => String::new(),
            };
            self.out.push(ProcOut {
                pid: r.pid,
                start: r.start,
                comm: String::from_utf8_lossy(&r.comm[..r.clen as usize]).into_owned(),
                state: r.state as char,
                cgroup: cg,
                cpu_pct: r.cpu_d as f64 / dt, // 10 ms ticks: ticks*10ms/(dt*1000ms)*100
                io_bps: r.io_d as f64 / dt,
                rss_bytes: r.rss * 4096,
                threads: r.thr,
                why,
            });
        }
    }
}

impl Config {
    fn proc_root_ref(&self) -> String {
        self.sampler.proc_root.clone()
    }
}

// ---------------------------------------------------------------- collector

pub struct Collector {
    cfg: Config,
    pub names: Names,
    ids: Ids,
    slurp: Slurp,
    cgs: HashMap<String, CgState>,
    hosts: HashMap<String, Ent>,
    stack: Vec<(String, String)>,
    pbuf: String,
    stacked: HashMap<Vec<u8>, bool>,
    next_serial: u32,
    start: Instant,
    pub frame: Vec<(u32, Vec<Val>)>,
    pub events: Vec<Event>,
    pub limit_updates: Vec<(u32, u32, f64)>,
    pub gone_ents: Vec<u32>,
    pub resets: u64,
    pub n_cgroups: usize,
    pub procs: PScan,
    pub t_cg: u128,
    pub t_host: u128,
    pub t_proc: u128,
}

impl Collector {
    pub fn new(cfg: Config, dict: Dict, start: Instant) -> Collector {
        let mut names = Names { dict, kc: std::array::from_fn(|_| HashMap::new()) };
        let ids = Ids::new(&mut names.dict);
        Collector {
            cfg,
            names,
            ids,
            slurp: Slurp::new(),
            cgs: HashMap::new(),
            hosts: HashMap::new(),
            stack: Vec::new(),
            pbuf: String::new(),
            stacked: HashMap::new(),
            next_serial: 0,
            start,
            frame: Vec::new(),
            events: Vec::new(),
            limit_updates: Vec::new(),
            gone_ents: Vec::new(),
            resets: 0,
            n_cgroups: 0,
            procs: PScan::default(),
            t_cg: 0,
            t_host: 0,
            t_proc: 0,
        }
    }

    pub fn sample(&mut self, tick: u64, dt_ms: u64) {
        let dt = dt_ms as f64 / 1000.0;
        self.frame.clear();
        self.events.clear();
        self.limit_updates.clear();
        self.gone_ents.clear();
        let slow_ticks = (self.cfg.sampler.slow_every_s * 1000 / self.cfg.sampler.tick_ms).max(1);
        if tick % slow_ticks == 0 {
            self.stacked.clear();
        }
        let t = Instant::now();
        self.cgroups(tick, dt, slow_ticks);
        self.t_cg = t.elapsed().as_micros();
        let t = Instant::now();
        self.host(tick, dt);
        self.t_host = t.elapsed().as_micros();
        let t = Instant::now();
        if self.cfg.process.scan {
            let Collector { cfg, slurp, pbuf, procs, .. } = self;
            procs.scan(cfg, slurp, pbuf, tick, dt);
        }
        self.t_proc = t.elapsed().as_micros();
    }

    fn cgroups(&mut self, tick: u64, dt: f64, slow_ticks: u64) {
        self.stack.clear();
        self.stack.push((self.cfg.sampler.cgroup_root.clone(), String::new()));
        let mut n = 0;
        while let Some((abs, rel)) = self.stack.pop() {
            if let Ok(rd) = fs::read_dir(&abs) {
                for e in rd.flatten() {
                    if e.file_type().map(|t| t.is_dir()).unwrap_or(false) {
                        let nm = e.file_name();
                        let nm = nm.to_string_lossy();
                        self.stack.push((format!("{abs}/{nm}"), format!("{rel}/{nm}")));
                    }
                }
            }
            self.read_cg(&abs, &rel, tick, dt, slow_ticks);
            n += 1;
        }
        let gone: Vec<String> = self.cgs.iter().filter(|(_, s)| s.e.seen != tick).map(|(k, _)| k.clone()).collect();
        for k in gone {
            if let Some(s) = self.cgs.remove(&k) {
                self.events.push(mk_event(self.start, "cgroup_vanished", &ent_name(&k), "{}"));
                self.gone_ents.push(s.e.id);
            }
        }
        self.n_cgroups = n;
    }

    fn read_cg(&mut self, abs: &str, rel: &str, tick: u64, dt: f64, slow_ticks: u64) {
        let _ = dt;
        let Collector { cfg, names, ids, slurp, cgs, pbuf, frame, events, limit_updates, resets, next_serial, start, stacked, .. } = self;
        if !cgs.contains_key(rel) {
            let id = names.dict.ent(&ent_name(rel));
            *next_serial += 1;
            cgs.insert(
                rel.to_string(),
                CgState {
                    e: Ent::new(id, tick > 1),
                    missing: 0,
                    serial: *next_serial,
                    fresh: true,
                    limits: Default::default(),
                    psi_above: [None; 3],
                },
            );
        }
        let st = cgs.get_mut(rel).unwrap();
        st.e.seen = tick;
        let slow = st.fresh || (tick + st.serial as u64) % slow_ticks == 0;
        st.fresh = false;
        if slow {
            st.missing = 0;
        }
        let thr = [cfg.events.psi_cpu_some_avg10, cfg.events.psi_memory_some_avg10, cfg.events.psi_io_some_avg10];
        let mut em = Em { out: &mut st.e.vals, prev: &mut st.e.prev, base_zero: st.e.base_zero, resets: &mut *resets };
        for i in 0..9 {
            if (st.missing >> i) & 1 == 1 {
                continue;
            }
            pbuf.clear();
            pbuf.push_str(abs);
            pbuf.push('/');
            pbuf.push_str(CG_FILES[i]);
            let b = match slurp.read(pbuf) {
                Ok(b) => b,
                Err(_) => {
                    st.missing |= 1 << i;
                    continue;
                }
            };
            match i {
                0 | 2 | 3 => {
                    let kind = [K_CPUSTAT, 0, K_MEMSTAT, K_MEMEV][i];
                    for l in lines(b) {
                        let (k, v) = match split_sp(l) {
                            Some(x) => x,
                            None => continue,
                        };
                        let (m, c) = match names.key(kind, k) {
                            Some(x) => x,
                            None => continue,
                        };
                        let x = match parse_u64(trim(v)) {
                            Some(x) => x,
                            None => continue,
                        };
                        if c {
                            em.counter(m, x);
                            if i == 3 && (k == b"oom_kill" || k == b"oom_group_kill") {
                                if let Some(lv) = em.out.last() {
                                    if lv.m == m && lv.v > 0.0 {
                                        let d = format!("{{\"counter\":\"{}\",\"delta\":{},\"total\":{}}}", String::from_utf8_lossy(k), lv.v, x);
                                        events.push(mk_event(*start, "oom_kill", &ent_name(rel), &d));
                                    }
                                }
                            }
                        } else {
                            em.gauge(m, x as f64);
                        }
                    }
                }
                1 => {
                    if let Some(x) = parse_u64(trim(b)) {
                        em.gauge(ids.mem_current, x as f64);
                    }
                }
                8 => {
                    if let Some(x) = parse_u64(trim(b)) {
                        em.gauge(ids.pids_current, x as f64);
                    }
                }
                4 => {
                    let mut tot = [0u64; 6];
                    let mut vrate: Option<f64> = None;
                    for l in lines(b) {
                        let mut it = fields(l);
                        let dev = match it.next() {
                            Some(d) => d,
                            None => continue,
                        };
                        let bottom = is_bottom(stacked, &cfg.sampler.sys_root, dev);
                        for kv in it {
                            if let Some((k, v)) = split_eq(kv) {
                                let slot = match k {
                                    b"rbytes" => 0,
                                    b"wbytes" => 1,
                                    b"rios" => 2,
                                    b"wios" => 3,
                                    b"dbytes" => 4,
                                    b"dios" => 5,
                                    b"cost.vrate" => {
                                        if let Some(x) = parse_f64(v) {
                                            vrate = Some(vrate.map_or(x, |o: f64| o.max(x)));
                                        }
                                        continue;
                                    }
                                    _ => continue,
                                };
                                if bottom {
                                    tot[slot] += parse_u64(v).unwrap_or(0);
                                }
                            }
                        }
                    }
                    for k in 0..6 {
                        em.counter(ids.io[k], tot[k]);
                    }
                    if let Some(x) = vrate {
                        em.gauge(ids.io_vrate, x);
                    }
                }
                _ => {
                    let r = i - 5;
                    for l in lines(b) {
                        let mut it = fields(l);
                        let kind = match it.next() {
                            Some(b"some") => 0,
                            Some(b"full") => 1,
                            _ => continue,
                        };
                        for kv in it {
                            match split_eq(kv) {
                                Some((b"avg10", v)) => {
                                    if let Some(x) = parse_f64(v) {
                                        em.gauge(ids.psi[r * 4 + kind * 2], x);
                                        if kind == 0 && thr[r] > 0.0 {
                                            let above = x >= thr[r];
                                            if let Some(prev) = st.psi_above[r] {
                                                if prev != above {
                                                    let d = format!(
                                                        "{{\"resource\":\"{}\",\"avg10\":{},\"threshold\":{},\"direction\":\"{}\"}}",
                                                        ["cpu", "memory", "io"][r], x, thr[r], if above { "up" } else { "down" }
                                                    );
                                                    events.push(mk_event(*start, "psi_cross", &ent_name(rel), &d));
                                                }
                                            }
                                            st.psi_above[r] = Some(above);
                                        }
                                    }
                                }
                                Some((b"total", v)) => {
                                    if let Some(x) = parse_u64(v) {
                                        em.counter(ids.psi[r * 4 + kind * 2 + 1], x);
                                    }
                                }
                                _ => {}
                            }
                        }
                    }
                }
            }
        }
        if slow {
            for j in 0..9 {
                pbuf.clear();
                pbuf.push_str(abs);
                pbuf.push('/');
                pbuf.push_str(SLOW_FILES[j]);
                let b = match slurp.read(pbuf) {
                    Ok(b) => trim(b),
                    Err(_) => continue,
                };
                let changed = match &st.limits[j] {
                    Some(old) => old.as_slice() != b,
                    None => false,
                };
                if changed {
                    let mut d = String::from("{\"file\":");
                    jstr(&mut d, SLOW_FILES[j]);
                    d.push_str(",\"old\":");
                    jstr(&mut d, &String::from_utf8_lossy(st.limits[j].as_ref().unwrap()));
                    d.push_str(",\"new\":");
                    jstr(&mut d, &String::from_utf8_lossy(b));
                    d.push('}');
                    events.push(mk_event(*start, "limit_changed", &ent_name(rel), &d));
                }
                if st.limits[j].as_deref() != Some(b) {
                    st.limits[j] = Some(b.to_vec());
                }
                let (vs, n) = limit_vals(j, b);
                for &(slot, v) in &vs[..n] {
                    em.gauge(ids.lim[slot], v);
                    limit_updates.push((st.e.id, ids.lim[slot], v));
                }
            }
        }
        if !st.e.vals.is_empty() {
            frame.push((st.e.id, std::mem::take(&mut st.e.vals)));
        }
    }

    fn hent<'a>(hosts: &'a mut HashMap<String, Ent>, dict: &mut Dict, name: &str, tick: u64) -> &'a mut Ent {
        if !hosts.contains_key(name) {
            hosts.insert(name.to_string(), Ent::new(dict.ent(name), false));
        }
        let e = hosts.get_mut(name).unwrap();
        e.seen = tick;
        e
    }

    fn host(&mut self, tick: u64, _dt: f64) {
        use std::fmt::Write;
        let dt_ms = self.cfg.sampler.tick_ms as f64; // util uses the nominal tick; see below
        let _ = dt_ms;
        let Collector { cfg, names, ids, slurp, hosts, pbuf, frame, resets, .. } = self;
        let root = cfg.sampler.proc_root.as_str();
        let mut name = String::new();
        // /proc/stat
        pbuf.clear();
        let _ = write!(pbuf, "{root}/stat");
        if let Ok(b) = slurp.read(pbuf) {
            for l in lines(b) {
                let mut it = fields(l);
                let k = match it.next() {
                    Some(k) => k,
                    None => continue,
                };
                if k.starts_with(b"cpu") {
                    name.clear();
                    name.push_str("cpu:");
                    if k == b"cpu" {
                        name.push_str("all");
                    } else {
                        name.push_str(&String::from_utf8_lossy(&k[3..]));
                    }
                    let e = Self::hent(hosts, &mut names.dict, &name, tick);
                    let bz = e.base_zero;
                    let mut em = Em { out: &mut e.vals, prev: &mut e.prev, base_zero: bz, resets: &mut *resets };
                    let s0 = em.out.len();
                    for j in 0..8 {
                        match it.next().and_then(parse_u64) {
                            Some(x) => em.counter(ids.cpu[j], x),
                            None => break,
                        }
                    }
                    if em.out.len() - s0 == 8 {
                        let v: Vec<f64> = em.out[s0..].iter().map(|x| x.v).collect();
                        let total: f64 = v.iter().sum();
                        let pct = if total > 0.0 { (total - v[3] - v[4]) / total * 100.0 } else { 0.0 };
                        em.gauge(ids.cpu_busy, pct);
                    }
                } else if k == b"ctxt" || k == b"processes" || k == b"procs_running" || k == b"procs_blocked" {
                    let x = match it.next().and_then(parse_u64) {
                        Some(x) => x,
                        None => continue,
                    };
                    let e = Self::hent(hosts, &mut names.dict, "host", tick);
                    let bz = e.base_zero;
                    let mut em = Em { out: &mut e.vals, prev: &mut e.prev, base_zero: bz, resets: &mut *resets };
                    match k {
                        b"ctxt" => em.counter(ids.ctxt, x),
                        b"processes" => em.counter(ids.forks, x),
                        b"procs_running" => em.gauge(ids.procs_running, x as f64),
                        _ => em.gauge(ids.procs_blocked, x as f64),
                    }
                }
            }
        }
        // meminfo, vmstat, loadavg -> entity "host"
        for (file, kind) in [("meminfo", K_MEMINFO), ("vmstat", K_VMSTAT)] {
            pbuf.clear();
            let _ = write!(pbuf, "{root}/{file}");
            if let Ok(b) = slurp.read(pbuf) {
                let e = Self::hent(hosts, &mut names.dict, "host", tick);
                let bz = e.base_zero;
                let mut em = Em { out: &mut e.vals, prev: &mut e.prev, base_zero: bz, resets: &mut *resets };
                for l in lines(b) {
                    let mut it = fields(l);
                    let (k, v) = match (it.next(), it.next().and_then(parse_u64)) {
                        (Some(k), Some(v)) => (k, v),
                        _ => continue,
                    };
                    if kind == K_MEMINFO {
                        let k = k.strip_suffix(b":").unwrap_or(k);
                        if let Some((m, _)) = names.key(kind, k) {
                            em.gauge(m, (v * 1024) as f64);
                        }
                    } else if let Some((m, _)) = names.key(kind, k) {
                        em.counter(m, v);
                    }
                }
            }
        }
        pbuf.clear();
        let _ = write!(pbuf, "{root}/loadavg");
        if let Ok(b) = slurp.read(pbuf) {
            let e = Self::hent(hosts, &mut names.dict, "host", tick);
            let bz = e.base_zero;
            let mut em = Em { out: &mut e.vals, prev: &mut e.prev, base_zero: bz, resets: &mut *resets };
            for (j, f) in fields(b).take(3).enumerate() {
                if let Some(x) = parse_f64(f) {
                    em.gauge(ids.load[j], x);
                }
            }
        }
        // diskstats
        pbuf.clear();
        let _ = write!(pbuf, "{root}/diskstats");
        if let Ok(b) = slurp.read(pbuf) {
            for l in lines(b) {
                let f: Vec<&[u8]> = fields(l).collect();
                if f.len() < 14 {
                    continue;
                }
                let dn = String::from_utf8_lossy(f[2]);
                if dn.starts_with("loop") || dn.starts_with("ram") {
                    continue;
                }
                let n = |i: usize| parse_u64(f[i]).unwrap_or(0);
                if n(3) == 0 && n(7) == 0 {
                    continue;
                }
                name.clear();
                name.push_str("dev:");
                name.push_str(&dn);
                let e = Self::hent(hosts, &mut names.dict, &name, tick);
                let bz = e.base_zero;
                let mut em = Em { out: &mut e.vals, prev: &mut e.prev, base_zero: bz, resets: &mut *resets };
                let s0 = em.out.len();
                for (j, &fi) in [3usize, 5, 6, 7, 9, 10, 12, 13].iter().enumerate() {
                    em.counter(ids.dk[j], n(fi));
                }
                em.gauge(ids.dk_inflight, n(11) as f64);
                if em.out.len() - s0 == 9 {
                    let v: Vec<f64> = em.out[s0..s0 + 8].iter().map(|x| x.v).collect();
                    let ops = v[0] + v[3];
                    let aw = if ops > 0.0 { (v[2] + v[5]) / ops } else { 0.0 };
                    em.gauge(ids.dk_await, aw);
                    em.gauge(ids.dk_util, v[6] / (_dt * 1000.0) * 100.0);
                }
            }
        }
        for e in hosts.values_mut() {
            if !e.vals.is_empty() {
                frame.push((e.id, std::mem::take(&mut e.vals)));
            }
        }
        hosts.retain(|_, e| e.seen == tick);
    }
}
