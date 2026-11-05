//! Per-tick collection: process scan, cgroup tree, host files. No process spawns; only
//! open/read/close on /sys and /proc plus directory listings. Every value that cannot be
//! a plain number carries a state (see `util::stc`); nothing is defaulted to zero.
use crate::cfg::Config;
use crate::dict::Dict;
use crate::procs::{ent_of_rel, PScan, ScanIn};
use crate::util::*;
use std::collections::HashMap;
use std::fs;
use std::sync::Arc;
use std::time::Instant;

/// One sampled value. `st != VALUE` means `v` is meaningless. `raw == NONE` marks a gauge
/// (or a state); otherwise `v` is the counter delta over the tick and `raw` the reading.
#[derive(Clone, Copy, Debug)]
pub struct Val {
    pub m: u32,
    pub v: f64,
    pub raw: u64,
    pub st: u8,
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

// ---------------------------------------------------------------- names and ids

const K_CPUSTAT: usize = 0;
const K_MEMSTAT: usize = 1;
const K_MEMEV: usize = 2;
const K_MEMINFO: usize = 3;
const K_VMSTAT: usize = 4;
const PREFIX: [&str; 5] = ["cpu.", "mem.", "memev.", "meminfo.", "vm."];
const MEMINFO_KEYS: [&str; 24] = [
    "MemTotal", "MemFree", "MemAvailable", "Buffers", "Cached", "SwapCached", "Active", "Inactive",
    "Unevictable", "SwapTotal", "SwapFree", "Dirty", "Writeback", "AnonPages", "Mapped", "Shmem",
    "Slab", "SReclaimable", "SUnreclaim", "KernelStack", "PageTables", "SecPageTables", "Percpu", "Zswap",
];
const MEMINFO_EXTRA: [&str; 1] = ["Zswapped"];
const VM_KEYS: [&str; 20] = [
    "pgfault", "pgmajfault", "pswpin", "pswpout", "pgscan_direct", "pgscan_kswapd", "pgscan_khugepaged",
    "pgsteal_direct", "pgsteal_kswapd", "pgsteal_khugepaged", "oom_kill", "zswpin", "zswpout", "zswpwb",
    "allocstall_normal", "pgpgin", "pgpgout", "workingset_refault_anon", "workingset_refault_file",
    "workingset_activate_file",
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
            K_MEMINFO => (MEMINFO_KEYS.contains(&ks.as_str()) || MEMINFO_EXTRA.contains(&ks.as_str())).then_some(false),
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

const CG_FILES: [&str; 9] = [
    "cpu.stat", "memory.current", "memory.stat", "memory.events", "io.stat", "cpu.pressure",
    "memory.pressure", "io.pressure", "pids.current",
];
/// One representative metric per cgroup file, used to publish a sticky state when the file is absent.
const CG_REP: [(&str, bool); 9] = [
    ("cpu.usage_usec", true), ("mem.current", false), ("mem.anon", false), ("memev.oom_kill", true),
    ("io.rbytes", true), ("psi.cpu.some.avg10", false), ("psi.memory.some.avg10", false),
    ("psi.io.some.avg10", false), ("pids.current", false),
];
const SLOW_FILES: [&str; 10] = [
    "memory.min", "memory.low", "memory.high", "memory.max", "memory.swap.max", "memory.zswap.max", "cpu.max",
    "cpu.weight", "io.max", "io.weight",
];
/// Metric slots (index into LIM_NAMES) fed by each SLOW_FILES entry.
const LIM_SLOT: [&[usize]; 10] = [&[0], &[1], &[2], &[3], &[4], &[5], &[6, 10], &[7], &[8], &[9]];
const LIM_NAMES: [&str; 11] = [
    "limit.memory.min", "limit.memory.low", "limit.memory.high", "limit.memory.max", "limit.memory.swap.max",
    "limit.memory.zswap.max", "limit.cpu.max.quota", "limit.cpu.weight", "limit.io.max.capped", "limit.io.weight",
    "limit.cpu.max.period",
];
const ZSWAP_GAUGES: [&str; 2] = ["stored_pages", "pool_total_size"];
const ZSWAP_COUNTERS: [&str; 8] = [
    "written_back_pages", "pool_limit_hit", "decompress_fail", "reject_alloc_fail", "reject_compress_fail",
    "reject_compress_poor", "reject_kmemcache_fail", "reject_reclaim_fail",
];
const KSM_GAUGES: [&str; 7] = ["run", "pages_shared", "pages_sharing", "pages_unshared", "pages_volatile", "general_profit", "ksm_zero_pages"];
const KSM_COUNTERS: [&str; 2] = ["full_scans", "pages_scanned"];
const MEMSTACK: [&str; 11] = [
    "anon", "file_cache", "shmem", "buffers", "slab_reclaimable", "slab_unreclaimable", "kernel_stack",
    "pagetables", "percpu", "zswap_pool", "free",
];

pub struct Ids {
    mem_current: u32,
    pids_current: u32,
    io: [u32; 6],
    io_cost: [u32; 3],
    io_vrate: u32,
    psi: [u32; 12], // res*4 + kind*2 + (0 avg10 | 1 total)
    lim: [u32; 11],
    cpu: [u32; 8],
    cpu_pct: [u32; 7],
    cpu_busy: u32,
    procs_running: u32,
    procs_blocked: u32,
    ctxt: u32,
    forks: u32,
    load: [u32; 3],
    dk: [u32; 8],
    dk_inflight: u32,
    dk_await: [u32; 3],
    dk_util: u32,
    dk_aqu: u32,
    d_state: u32,
    d_unattr: u32,
    memstack: [u32; 12],
    zs_g: [u32; 2],
    zs_c: [u32; 8],
    zs_debugfs: u32,
    zs_pool: u32,
    zs_stored: u32,
    zs_enabled: u32,
    zs_maxpct: u32,
    ksm_g: [u32; 7],
    ksm_c: [u32; 2],
    selfm: [u32; 6],
}

impl Ids {
    fn new(d: &mut Dict) -> Ids {
        let mut io = [0; 6];
        for (i, n) in ["rbytes", "wbytes", "rios", "wios", "dbytes", "dios"].iter().enumerate() {
            io[i] = d.met(&format!("io.{n}"), true);
        }
        let mut io_cost = [0; 3];
        for (i, n) in ["usage", "wait", "indelay"].iter().enumerate() {
            io_cost[i] = d.met(&format!("io.cost.{n}"), true);
        }
        let mut psi = [0; 12];
        for (r, rn) in ["cpu", "memory", "io"].iter().enumerate() {
            for (k, kn) in ["some", "full"].iter().enumerate() {
                psi[r * 4 + k * 2] = d.met(&format!("psi.{rn}.{kn}.avg10"), false);
                psi[r * 4 + k * 2 + 1] = d.met(&format!("psi.{rn}.{kn}.total"), true);
            }
        }
        let mut lim = [0; 11];
        for i in 0..11 {
            lim[i] = d.met(LIM_NAMES[i], false);
        }
        let mut cpu = [0; 8];
        for (i, n) in ["user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal"].iter().enumerate() {
            cpu[i] = d.met(&format!("cpu.{n}"), true);
        }
        let mut cpu_pct = [0; 7];
        for (i, n) in ["us", "sy", "io", "st", "irq", "si", "id"].iter().enumerate() {
            cpu_pct[i] = d.met(&format!("cpupct.{n}"), false);
        }
        let mut dk = [0; 8];
        for (i, n) in ["reads", "rd_sectors", "rd_ms", "writes", "wr_sectors", "wr_ms", "io_ticks_ms", "time_in_queue_ms"]
            .iter()
            .enumerate()
        {
            dk[i] = d.met(&format!("disk.{n}"), true);
        }
        let mut memstack = [0; 12];
        for (i, n) in MEMSTACK.iter().enumerate() {
            memstack[i] = d.met(&format!("memstack.{n}"), false);
        }
        memstack[11] = d.met("memstack.other", false);
        let mut zs_g = [0; 2];
        for (i, n) in ZSWAP_GAUGES.iter().enumerate() {
            zs_g[i] = d.met(&format!("zswap.{n}"), false);
        }
        let mut zs_c = [0; 8];
        for (i, n) in ZSWAP_COUNTERS.iter().enumerate() {
            zs_c[i] = d.met(&format!("zswap.{n}"), true);
        }
        let mut ksm_g = [0; 7];
        for (i, n) in KSM_GAUGES.iter().enumerate() {
            ksm_g[i] = d.met(&format!("ksm.{n}"), false);
        }
        let mut ksm_c = [0; 2];
        for (i, n) in KSM_COUNTERS.iter().enumerate() {
            ksm_c[i] = d.met(&format!("ksm.{n}"), true);
        }
        let mut selfm = [0; 6];
        for (i, (n, c)) in [("work_us", false), ("dt_ms", false), ("rss_bytes", false), ("cpu_ticks", true), ("overruns", true), ("skipped_ticks", true)]
            .iter()
            .enumerate()
        {
            selfm[i] = d.met(&format!("sampler.{n}"), *c);
        }
        Ids {
            mem_current: d.met("mem.current", false),
            pids_current: d.met("pids.current", false),
            io,
            io_cost,
            io_vrate: d.met("io.cost.vrate", false),
            psi,
            lim,
            cpu,
            cpu_pct,
            cpu_busy: d.met("cpu.busy_pct", false),
            procs_running: d.met("host.procs_running", false),
            procs_blocked: d.met("host.procs_blocked", false),
            ctxt: d.met("host.ctxt", true),
            forks: d.met("host.forks", true),
            load: [d.met("host.load1", false), d.met("host.load5", false), d.met("host.load15", false)],
            dk,
            dk_inflight: d.met("disk.inflight", false),
            dk_await: [d.met("disk.await_ms", false), d.met("disk.r_await_ms", false), d.met("disk.w_await_ms", false)],
            dk_util: d.met("disk.util_pct", false),
            dk_aqu: d.met("disk.aqu_sz", false),
            d_state: d.met("procs.d_state", false),
            d_unattr: d.met("procs.d_state_unattributed", false),
            memstack,
            zs_g,
            zs_c,
            zs_debugfs: d.met("zswap.debugfs_ok", false),
            zs_pool: d.met("zswap.pool_bytes", false),
            zs_stored: d.met("zswap.stored_bytes", false),
            zs_enabled: d.met("zswap.enabled", false),
            zs_maxpct: d.met("zswap.max_pool_percent", false),
            ksm_g,
            ksm_c,
            selfm,
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
        self.out.push(Val { m, v, raw: NONE, st: stc::VALUE });
    }
    fn state(&mut self, m: u32, st: u8) {
        self.out.push(Val { m, v: 0.0, raw: NONE, st });
    }
    /// Counter with reset handling: a decrease means the counter restarted, so the new
    /// reading is the delta since the restart. The first observation of an entity that
    /// existed at start has no delta (state `first_sample`, with the raw reading); an
    /// entity born after sampling started began at zero, so its first reading is its delta.
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
                self.out.push(Val { m, v: 0.0, raw: cur, st: stc::UN_FIRST });
                return;
            }
        }
        let d = if cur >= p {
            cur - p
        } else {
            *self.resets += 1;
            cur
        };
        self.out.push(Val { m, v: d as f64, raw: cur, st: stc::VALUE });
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
    fail: [u8; 9],
    serial: u32,
    fresh: bool,
    limits: [Option<Vec<u8>>; 10],
    psi_above: [Option<bool>; 3],
}

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

/// Parse a limit file into (slot, value) pairs. `max` is -1 (documented sentinel).
pub fn limit_vals(j: usize, t: &[u8]) -> ([(usize, f64); 2], usize) {
    let num = |x: &[u8]| -> Option<f64> {
        if x == b"max" {
            Some(-1.0)
        } else {
            parse_f64(x)
        }
    };
    let r = [(0usize, 0.0f64); 2];
    match j {
        0..=5 | 7 => match num(t) {
            Some(v) => ([(j, v), (0, 0.0)], 1),
            None => (r, 0),
        },
        6 => {
            let mut it = fields(t);
            let (a, b) = (it.next(), it.next());
            match (a.and_then(num), b.and_then(num)) {
                (Some(q), Some(p)) => ([(6, q), (10, p)], 2),
                _ => (r, 0),
            }
        }
        8 => {
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
            ([(8, capped), (0, 0.0)], 1)
        }
        _ => {
            for l in lines(t) {
                let mut it = fields(l);
                if it.next() == Some(b"default") {
                    if let Some(v) = it.next().and_then(parse_f64) {
                        return ([(9, v), (0, 0.0)], 1);
                    }
                }
            }
            (r, 0)
        }
    }
}

// ---------------------------------------------------------------- collector

/// What the main loop knows about the sampler itself, published as entity `sampler`.
#[derive(Default, Clone, Copy)]
pub struct SelfIn {
    pub work_us: u64,
    pub rss_bytes: Option<u64>,
    pub cpu_ticks: Option<u64>,
    pub overruns: u64,
    pub skipped: u64,
}

pub struct Collector {
    pub cfg: Config,
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
    pub clk_tck: u64,
    pub page_size: u64,
    delayacct: Option<bool>,
    pub frame: Vec<(u32, Vec<Val>)>,
    pub events: Vec<Event>,
    /// Sticky values (limits, absent-file states) shown in `current` between slow ticks.
    pub sticky: Arc<HashMap<u32, HashMap<u32, Val>>>,
    limit_updates: Vec<(u32, Val)>,
    gone_ents: Vec<u32>,
    pub resets: u64,
    pub n_cgroups: usize,
    pub procs: PScan,
    pub selfin: SelfIn,
    pub gc_freed: u64,
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
            clk_tck: clk_tck().0,
            page_size: page_size().0,
            delayacct: None,
            frame: Vec::new(),
            events: Vec::new(),
            sticky: Arc::new(HashMap::new()),
            limit_updates: Vec::new(),
            gone_ents: Vec::new(),
            resets: 0,
            n_cgroups: 0,
            procs: PScan::default(),
            selfin: SelfIn::default(),
            gc_freed: 0,
            t_cg: 0,
            t_host: 0,
            t_proc: 0,
        }
    }

    /// One sampling pass. `dt_ms` is the measured time since the previous pass (monotonic),
    /// `mono_ms` a monotonic clock, `wall_s` wall seconds (entity last-seen bookkeeping).
    pub fn sample(&mut self, tick: u64, dt_ms: u64, mono_ms: u64, wall_s: u64) {
        let dt = dt_ms as f64 / 1000.0;
        self.frame.clear();
        self.events.clear();
        self.limit_updates.clear();
        self.gone_ents.clear();
        let slow_ticks = (self.cfg.sampler.slow_every_s * 1000 / self.cfg.sampler.tick_ms).max(1);
        if tick % slow_ticks == 0 {
            self.stacked.clear();
        }
        if tick == 1 || tick % slow_ticks == 0 {
            self.delayacct = fs::read_to_string(format!("{}/sys/kernel/task_delayacct", self.cfg.sampler.proc_root))
                .ok()
                .map(|s| s.trim() == "1");
        }
        let t = Instant::now();
        if self.cfg.process.scan {
            let sweep_ticks = (self.cfg.process.full_sweep_every_s * 1000 / self.cfg.sampler.tick_ms).max(1);
            let si = ScanIn {
                tick,
                dt_ms,
                now_ms: mono_ms,
                sweep: tick == 1 || tick % sweep_ticks == 0,
                delayacct: self.delayacct,
                clk_tck: self.clk_tck,
                page_size: self.page_size,
            };
            let Collector { cfg, slurp, pbuf, procs, .. } = self;
            procs.scan(cfg, slurp, pbuf, &si);
        }
        self.t_proc = t.elapsed().as_micros();
        let t = Instant::now();
        self.cgroups(tick, wall_s, slow_ticks);
        self.t_cg = t.elapsed().as_micros();
        let t = Instant::now();
        self.host(tick, dt);
        self.t_host = t.elapsed().as_micros();
        // Apply sticky changes.
        if !self.limit_updates.is_empty() || !self.gone_ents.is_empty() {
            let s = Arc::make_mut(&mut self.sticky);
            for (e, v) in self.limit_updates.drain(..) {
                s.entry(e).or_default().insert(v.m, v);
            }
            for e in &self.gone_ents {
                s.remove(e);
            }
        }
        // Id garbage collection once a minute worth of ticks (cheap scan of the entity table).
        if tick % slow_ticks == 0 {
            let freed = self.names.dict.gc(wall_s, self.cfg.retention_s());
            self.gc_freed += freed as u64;
        }
    }

    fn cgroups(&mut self, tick: u64, wall_s: u64, slow_ticks: u64) {
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
            self.read_cg(&abs, &rel, tick, wall_s, slow_ticks);
            n += 1;
        }
        let gone: Vec<String> = self.cgs.iter().filter(|(_, s)| s.e.seen != tick).map(|(k, _)| k.clone()).collect();
        for k in gone {
            if let Some(s) = self.cgs.remove(&k) {
                self.events.push(mk_event(self.start, "cgroup_vanished", &ent_of_rel(&k), "{}"));
                self.gone_ents.push(s.e.id);
            }
        }
        self.n_cgroups = n;
    }

    fn read_cg(&mut self, abs: &str, rel: &str, tick: u64, wall_s: u64, slow_ticks: u64) {
        let Collector { cfg, names, ids, slurp, cgs, pbuf, frame, events, limit_updates, resets, next_serial, start, stacked, procs, .. } = self;
        if !cgs.contains_key(rel) {
            let id = names.dict.ent(&ent_of_rel(rel), wall_s);
            *next_serial += 1;
            cgs.insert(
                rel.to_string(),
                CgState {
                    e: Ent::new(id, tick > 1),
                    missing: 0,
                    fail: [0; 9],
                    serial: *next_serial,
                    fresh: true,
                    limits: Default::default(),
                    psi_above: [None; 3],
                },
            );
        }
        let st = cgs.get_mut(rel).unwrap();
        st.e.seen = tick;
        names.dict.touch(st.e.id, wall_s);
        let ent_name = ent_of_rel(rel);
        let slow = st.fresh || (tick + st.serial as u64) % slow_ticks == 0;
        st.fresh = false;
        if slow {
            st.missing = 0;
        }
        let thr = [cfg.events.psi_cpu_some_avg10, cfg.events.psi_memory_some_avg10, cfg.events.psi_io_some_avg10];
        let eid = st.e.id;
        let mut em = Em { out: &mut st.e.vals, prev: &mut st.e.prev, base_zero: st.e.base_zero, resets: &mut *resets };
        for i in 0..9 {
            if (st.missing >> i) & 1 == 1 {
                continue;
            }
            pbuf.clear();
            // The root cgroup has no pressure files: use the system-wide /proc/pressure.
            if rel.is_empty() && (5..8).contains(&i) && !std::path::Path::new(&format!("{abs}/{}", CG_FILES[i])).exists() {
                pbuf.push_str(&format!("{}/pressure/{}", cfg.sampler.proc_root, ["cpu", "memory", "io"][i - 5]));
            } else {
                pbuf.push_str(abs);
                pbuf.push('/');
                pbuf.push_str(CG_FILES[i]);
            }
            let b = match slurp.read(pbuf) {
                Ok(b) => b,
                Err(e) => {
                    st.missing |= 1 << i;
                    st.fail[i] = err_state(&e, stc::NA_CONTROLLER);
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
                                    if lv.m == m && lv.st == stc::VALUE && lv.v > 0.0 {
                                        let d = format!("{{\"counter\":\"{}\",\"delta\":{},\"total\":{}}}", String::from_utf8_lossy(k), lv.v, x);
                                        events.push(mk_event(*start, "oom_kill", &ent_name, &d));
                                    }
                                }
                            }
                        } else {
                            em.gauge(m, x as f64);
                        }
                    }
                }
                1 => match parse_u64(trim(b)) {
                    Some(x) => em.gauge(ids.mem_current, x as f64),
                    None => em.state(ids.mem_current, stc::UN_PARSE),
                },
                8 => match parse_u64(trim(b)) {
                    Some(x) => em.gauge(ids.pids_current, x as f64),
                    None => em.state(ids.pids_current, stc::UN_PARSE),
                },
                4 => {
                    let mut tot = [0u64; 6];
                    let mut cost = [0u64; 3];
                    let mut have_cost = false;
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
                                    b"cost.usage" | b"cost.wait" | b"cost.indelay" => {
                                        // iocost state exists only on devices where it is enabled, so
                                        // there is no double counting: sum over every device (Python
                                        // collector semantics, `io.stat:cost_usage`).
                                        have_cost = true;
                                        let ci = match k { b"cost.usage" => 0, b"cost.wait" => 1, _ => 2 };
                                        cost[ci] += parse_u64(v).unwrap_or(0);
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
                    if have_cost {
                        for k in 0..3 {
                            em.counter(ids.io_cost[k], cost[k]);
                        }
                    } else {
                        for k in 0..3 {
                            em.state(ids.io_cost[k], stc::NA_FEATURE);
                        }
                    }
                    if let Some(x) = vrate {
                        em.gauge(ids.io_vrate, x);
                    } else if rel.is_empty() {
                        em.state(ids.io_vrate, stc::NA_FEATURE);
                    }
                }
                _ => {
                    let r = i - 5;
                    let mut have_full = false;
                    for l in lines(b) {
                        let mut it = fields(l);
                        let kind = match it.next() {
                            Some(b"some") => 0,
                            Some(b"full") => {
                                have_full = true;
                                1
                            }
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
                                                    events.push(mk_event(*start, "psi_cross", &ent_name, &d));
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
                    if !have_full {
                        em.state(ids.psi[r * 4 + 2], stc::NA_NO_FULL);
                        em.state(ids.psi[r * 4 + 3], stc::NA_NO_FULL);
                    }
                }
            }
        }
        if procs.scanned {
            em.gauge(ids.d_state, procs.dmap.get(rel).copied().unwrap_or(0) as f64);
        }
        if rel.is_empty() && procs.scanned {
            em.gauge(ids.d_unattr, procs.d_unattributed as f64);
        }
        if slow {
            // Files that are absent right now: a sticky state, so `current` says why.
            for i in 0..9 {
                if (st.missing >> i) & 1 == 1 {
                    let (n, c) = CG_REP[i];
                    let m = names.dict.met(n, c);
                    limit_updates.push((eid, Val { m, v: 0.0, raw: NONE, st: st.fail[i] }));
                }
            }
            for j in 0..10 {
                pbuf.clear();
                pbuf.push_str(abs);
                pbuf.push('/');
                pbuf.push_str(SLOW_FILES[j]);
                let b = match slurp.read(pbuf) {
                    Ok(b) => trim(b),
                    Err(e) => {
                        let code = err_state(&e, stc::NA_CONTROLLER);
                        for &s in LIM_SLOT[j] {
                            limit_updates.push((eid, Val { m: ids.lim[s], v: 0.0, raw: NONE, st: code }));
                        }
                        continue;
                    }
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
                    events.push(mk_event(*start, "limit_changed", &ent_name, &d));
                }
                if st.limits[j].as_deref() != Some(b) {
                    st.limits[j] = Some(b.to_vec());
                }
                let (vs, n) = limit_vals(j, b);
                if n == 0 {
                    limit_updates.push((eid, Val { m: ids.lim[LIM_SLOT[j][0]], v: 0.0, raw: NONE, st: stc::UN_PARSE }));
                }
                for &(slot, v) in &vs[..n] {
                    em.gauge(ids.lim[slot], v);
                    limit_updates.push((eid, Val { m: ids.lim[slot], v, raw: NONE, st: stc::VALUE }));
                }
            }
        }
        if !st.e.vals.is_empty() {
            frame.push((st.e.id, std::mem::take(&mut st.e.vals)));
        }
    }

    fn hent<'a>(hosts: &'a mut HashMap<String, Ent>, dict: &mut Dict, name: &str, tick: u64, wall_s: u64) -> &'a mut Ent {
        if !hosts.contains_key(name) {
            hosts.insert(name.to_string(), Ent::new(dict.ent(name, wall_s), false));
        }
        let e = hosts.get_mut(name).unwrap();
        dict.touch(e.id, wall_s);
        e.seen = tick;
        e
    }

    fn host(&mut self, tick: u64, dt: f64) {
        use std::fmt::Write;
        let wall_s = wall_ms() / 1000;
        let Collector { cfg, names, ids, slurp, hosts, pbuf, frame, resets, selfin, page_size, .. } = self;
        let root = cfg.sampler.proc_root.as_str();
        let sys = cfg.sampler.sys_root.as_str();
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
                    let e = Self::hent(hosts, &mut names.dict, &name, tick, wall_s);
                    let bz = e.base_zero;
                    let mut em = Em { out: &mut e.vals, prev: &mut e.prev, base_zero: bz, resets: &mut *resets };
                    let s0 = em.out.len();
                    for j in 0..8 {
                        match it.next().and_then(parse_u64) {
                            Some(x) => em.counter(ids.cpu[j], x),
                            None => break,
                        }
                    }
                    if em.out.len() - s0 == 8 && em.out[s0..].iter().all(|x| x.st == stc::VALUE) {
                        let v: Vec<f64> = em.out[s0..].iter().map(|x| x.v).collect();
                        let total: f64 = v.iter().sum();
                        if total > 0.0 {
                            let p = |x: f64| x / total * 100.0;
                            // us = user+nice, sy = system, io = iowait, st = steal, irq, si, id
                            let pct = [p(v[0] + v[1]), p(v[2]), p(v[4]), p(v[7]), p(v[5]), p(v[6]), p(v[3])];
                            for (i, x) in pct.iter().enumerate() {
                                em.gauge(ids.cpu_pct[i], *x);
                            }
                            em.gauge(ids.cpu_busy, (total - v[3] - v[4]) / total * 100.0);
                        } else {
                            for i in 0..7 {
                                em.state(ids.cpu_pct[i], stc::NA_IDLE);
                            }
                            em.state(ids.cpu_busy, stc::NA_IDLE);
                        }
                    }
                } else if k == b"ctxt" || k == b"processes" || k == b"procs_running" || k == b"procs_blocked" {
                    let x = match it.next().and_then(parse_u64) {
                        Some(x) => x,
                        None => continue,
                    };
                    let e = Self::hent(hosts, &mut names.dict, "host", tick, wall_s);
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
        // meminfo -> host, plus the stacked breakdown and the zswap fallback inputs
        let mut mi: HashMap<&'static str, u64> = HashMap::new();
        pbuf.clear();
        let _ = write!(pbuf, "{root}/meminfo");
        if let Ok(b) = slurp.read(pbuf) {
            let e = Self::hent(hosts, &mut names.dict, "host", tick, wall_s);
            let bz = e.base_zero;
            let mut em = Em { out: &mut e.vals, prev: &mut e.prev, base_zero: bz, resets: &mut *resets };
            for l in lines(b) {
                let mut it = fields(l);
                let (k, v) = match (it.next(), it.next().and_then(parse_u64)) {
                    (Some(k), Some(v)) => (k, v),
                    _ => continue,
                };
                let k = k.strip_suffix(b":").unwrap_or(k);
                if let Some((m, _)) = names.key(K_MEMINFO, k) {
                    em.gauge(m, (v * 1024) as f64);
                    let ks = std::str::from_utf8(k).unwrap_or("");
                    if let Some(s) = MEMINFO_KEYS.iter().chain(MEMINFO_EXTRA.iter()).find(|s| **s == ks) {
                        mi.insert(*s, v * 1024);
                    }
                }
            }
            if let Some(&total) = mi.get("MemTotal") {
                let g = |k: &str| mi.get(k).copied().unwrap_or(0);
                let parts = [
                    g("AnonPages"),
                    g("Cached").saturating_sub(g("Shmem")),
                    g("Shmem"),
                    g("Buffers"),
                    g("SReclaimable"),
                    g("SUnreclaim"),
                    g("KernelStack"),
                    g("PageTables") + g("SecPageTables"),
                    g("Percpu"),
                    g("Zswap"),
                    g("MemFree"),
                ];
                for (i, p) in parts.iter().enumerate() {
                    em.gauge(ids.memstack[i], *p as f64);
                }
                em.gauge(ids.memstack[11], total.saturating_sub(parts.iter().sum()) as f64);
            } else {
                for i in 0..12 {
                    em.state(ids.memstack[i], stc::UN_READ);
                }
            }
        }
        // vmstat counters
        pbuf.clear();
        let _ = write!(pbuf, "{root}/vmstat");
        if let Ok(b) = slurp.read(pbuf) {
            let e = Self::hent(hosts, &mut names.dict, "host", tick, wall_s);
            let bz = e.base_zero;
            let mut em = Em { out: &mut e.vals, prev: &mut e.prev, base_zero: bz, resets: &mut *resets };
            for l in lines(b) {
                let mut it = fields(l);
                if let (Some(k), Some(v)) = (it.next(), it.next().and_then(parse_u64)) {
                    if let Some((m, _)) = names.key(K_VMSTAT, k) {
                        em.counter(m, v);
                    }
                }
            }
        }
        pbuf.clear();
        let _ = write!(pbuf, "{root}/loadavg");
        if let Ok(b) = slurp.read(pbuf) {
            let e = Self::hent(hosts, &mut names.dict, "host", tick, wall_s);
            let bz = e.base_zero;
            let mut em = Em { out: &mut e.vals, prev: &mut e.prev, base_zero: bz, resets: &mut *resets };
            for (j, f) in fields(b).take(3).enumerate() {
                if let Some(x) = parse_f64(f) {
                    em.gauge(ids.load[j], x);
                }
            }
        }
        // zswap: debugfs counters with a typed fallback to meminfo
        {
            let e = Self::hent(hosts, &mut names.dict, "host", tick, wall_s);
            let bz = e.base_zero;
            let mut em = Em { out: &mut e.vals, prev: &mut e.prev, base_zero: bz, resets: &mut *resets };
            let dir = format!("{sys}/kernel/debug/zswap");
            let mut dfail: Option<u8> = None;
            let mut pool_dbg = None;
            let mut stored_dbg = None;
            for (i, k) in ZSWAP_GAUGES.iter().chain(ZSWAP_COUNTERS.iter()).enumerate() {
                pbuf.clear();
                let _ = write!(pbuf, "{dir}/{k}");
                let id = if i < 2 { ids.zs_g[i] } else { ids.zs_c[i - 2] };
                match slurp.read(pbuf) {
                    Ok(b) => match parse_u64(trim(b)) {
                        Some(x) => {
                            if i < 2 {
                                em.gauge(id, x as f64);
                                if i == 0 { stored_dbg = Some(x * *page_size) } else { pool_dbg = Some(x) }
                            } else {
                                em.counter(id, x);
                            }
                        }
                        None => em.state(id, stc::UN_PARSE),
                    },
                    Err(err) => {
                        // A missing single file on a readable directory is an older kernel;
                        // a missing or denied directory is "debugfs unreadable".
                        let dir_ok = fs::read_dir(&dir).is_ok();
                        let code = if dir_ok { err_state(&err, stc::NA_FEATURE) } else { stc::UN_DEBUGFS };
                        if !dir_ok {
                            dfail = Some(code);
                        }
                        em.state(id, code);
                    }
                }
            }
            match dfail {
                None => em.gauge(ids.zs_debugfs, 1.0),
                Some(c) => em.state(ids.zs_debugfs, c),
            }
            // Pool and stored bytes: debugfs when readable, else meminfo (Zswap, Zswapped).
            match (pool_dbg, mi.get("Zswap")) {
                (Some(p), _) => em.gauge(ids.zs_pool, p as f64),
                (None, Some(&p)) => em.gauge(ids.zs_pool, p as f64),
                (None, None) => em.state(ids.zs_pool, stc::NA_FEATURE),
            }
            match (stored_dbg, mi.get("Zswapped")) {
                (Some(s), _) => em.gauge(ids.zs_stored, s as f64),
                (None, Some(&s)) => em.gauge(ids.zs_stored, s as f64),
                (None, None) => em.state(ids.zs_stored, stc::NA_FEATURE),
            }
            for (id, f) in [(ids.zs_enabled, "enabled"), (ids.zs_maxpct, "max_pool_percent")] {
                pbuf.clear();
                let _ = write!(pbuf, "{sys}/module/zswap/parameters/{f}");
                match slurp.read(pbuf) {
                    Ok(b) => {
                        let t = trim(b);
                        match (t, parse_f64(t)) {
                            (b"Y", _) => em.gauge(id, 1.0),
                            (b"N", _) => em.gauge(id, 0.0),
                            (_, Some(x)) => em.gauge(id, x),
                            _ => em.state(id, stc::UN_PARSE),
                        }
                    }
                    Err(err) => em.state(id, err_state(&err, stc::NA_FEATURE)),
                }
            }
            // KSM
            let kdir = format!("{sys}/kernel/mm/ksm");
            for (i, k) in KSM_GAUGES.iter().chain(KSM_COUNTERS.iter()).enumerate() {
                pbuf.clear();
                let _ = write!(pbuf, "{kdir}/{k}");
                let id = if i < 7 { ids.ksm_g[i] } else { ids.ksm_c[i - 7] };
                match slurp.read(pbuf) {
                    Ok(b) => {
                        let t = trim(b);
                        if i < 7 {
                            match parse_f64(t) {
                                Some(x) => em.gauge(id, x),
                                None => em.state(id, stc::UN_PARSE),
                            }
                        } else {
                            match parse_u64(t) {
                                Some(x) => em.counter(id, x),
                                None => em.state(id, stc::UN_PARSE),
                            }
                        }
                    }
                    Err(err) => em.state(id, err_state(&err, stc::NA_FEATURE)),
                }
            }
        }
        // the sampler itself
        {
            let e = Self::hent(hosts, &mut names.dict, "sampler", tick, wall_s);
            let bz = e.base_zero;
            let mut em = Em { out: &mut e.vals, prev: &mut e.prev, base_zero: bz, resets: &mut *resets };
            em.gauge(ids.selfm[0], selfin.work_us as f64);
            em.gauge(ids.selfm[1], dt * 1000.0);
            match selfin.rss_bytes {
                Some(r) => em.gauge(ids.selfm[2], r as f64),
                None => em.state(ids.selfm[2], stc::UN_READ),
            }
            match selfin.cpu_ticks {
                Some(c) => em.counter(ids.selfm[3], c),
                None => em.state(ids.selfm[3], stc::UN_READ),
            }
            em.counter(ids.selfm[4], selfin.overruns);
            em.counter(ids.selfm[5], selfin.skipped);
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
                let e = Self::hent(hosts, &mut names.dict, &name, tick, wall_s);
                let bz = e.base_zero;
                let mut em = Em { out: &mut e.vals, prev: &mut e.prev, base_zero: bz, resets: &mut *resets };
                let s0 = em.out.len();
                for (j, &fi) in [3usize, 5, 6, 7, 9, 10, 12, 13].iter().enumerate() {
                    em.counter(ids.dk[j], n(fi));
                }
                em.gauge(ids.dk_inflight, n(11) as f64);
                if em.out[s0..s0 + 8].iter().all(|x| x.st == stc::VALUE) {
                    let v: Vec<f64> = em.out[s0..s0 + 8].iter().map(|x| x.v).collect();
                    let (rd, wr) = (v[0], v[3]);
                    if rd + wr > 0.0 {
                        em.gauge(ids.dk_await[0], (v[2] + v[5]) / (rd + wr));
                    } else {
                        em.state(ids.dk_await[0], stc::NA_IDLE);
                    }
                    if rd > 0.0 { em.gauge(ids.dk_await[1], v[2] / rd) } else { em.state(ids.dk_await[1], stc::NA_IDLE) }
                    if wr > 0.0 { em.gauge(ids.dk_await[2], v[5] / wr) } else { em.state(ids.dk_await[2], stc::NA_IDLE) }
                    em.gauge(ids.dk_util, (v[6] / (dt * 1000.0) * 100.0).min(100.0));
                    em.gauge(ids.dk_aqu, v[7] / (dt * 1000.0));
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
