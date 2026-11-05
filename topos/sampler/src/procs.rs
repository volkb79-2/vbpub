//! Process scan. `stat` is read for every PID every tick (it carries state, CPU time,
//! RSS, major faults and block-IO delay ticks of the thread-group LEADER); `io`, `schedstat`
//! and the per-thread view are read only for candidates: PIDs in the previous enriched
//! union, PIDs whose CPU time rose, D-state PIDs and new PIDs, plus every PID on a full
//! sweep (a slower configured cadence, leader files only), so an IO-hot process that burns
//! no CPU is still discovered, at most `full_sweep_every_s` late.
//!
//! Threads (S-10): `state`, `delayacct_blkio_ticks` and `schedstat` belong to a single task,
//! not to the process. For candidates with more than one thread, `/proc/<pid>/task/*/stat`
//! and `schedstat` are read, so the D-state count, block-IO delay and run delay cover all
//! threads (at most `TASK_THREADS_MAX` per process, `TASK_READ_BUDGET` task reads per tick).
//! A process whose leader sleeps while a worker thread sits in D, and that is not a candidate
//! for another reason, is invisible to the cheap pass; `host.procs_blocked` (all tasks) minus
//! the D tasks counted here is published as `pids.d_unseen`, and while it is positive the
//! per-thread scan is widened to all multi-threaded processes, a budgeted slice per tick.
//! `cmdline` is read by a bounded worker pool (see `cmdline.rs`), the owning cgroup inline.
use crate::cfg::Config;
use crate::cmdline::{read_now, CmdWorkers};
use crate::util::*;
use std::collections::HashMap;
use std::fmt::Write;
use std::fs;

/// Threads read per process, and task-file reads per tick.
pub const TASK_THREADS_MAX: usize = 1024;
pub const TASK_READ_BUDGET: u32 = 4096;
/// A cmdline request unanswered for this long is shown as a timeout; retried after the second.
pub const CMD_TIMEOUT_MS: u64 = 2_000;
pub const CMD_RETRY_MS: u64 = 30_000;

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
    dtasks: u32,
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
    have_stat: bool,
    majflt: u64,
    rb: u64,
    wb: u64,
    wait_ns: u64,
    io_t: u64,
    have_io: bool,
    have_sched: bool,
    /// The baselines `blk` / `wait_ns` are sums over all threads (true) or the leader's (false).
    blk_all: bool,
    sched_all: bool,
    enriched: bool,
    cgroup: String,
    cmd: String,
    /// 0 not asked, 1 asked, 2 have it, 3 failed or timed out (`cmd_err`).
    cmd_state: u8,
    cmd_since: u64,
    cmd_err: u8,
    meta_tick: u64,
    seen: u64,
    blk: u64,
}

pub struct ProcOut {
    pub pid: u32,
    pub start: u64,
    pub comm: String,
    pub state: char,
    /// D-state tasks of this process (all scanned threads; the leader only when not scanned).
    pub d_tasks: u32,
    pub cgroup: String,
    pub cmdline: String,
    pub cmd_state: PV,
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
    workers: Option<CmdWorkers>,
    cursor: u32,
    /// Read command lines inline (tests, `--once`); production uses the worker pool.
    pub sync_cmdline: bool,
    pub cmd_timeouts: u64,
    pub cmd_pending: u32,
    pub out: Vec<ProcOut>,
    pub total: u32,
    /// D-state TASKS (threads included where they were scanned).
    pub dstate: u32,
    /// `host.procs_blocked` minus `dstate`, set by the host pass of the same tick.
    pub d_gap: u64,
    pub task_scans: u32,
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

/// State letter and `delayacct_blkio_ticks` of a `stat` line; None if it does not parse.
fn task_stat(b: &[u8]) -> Option<(u8, u64)> {
    let cp = b.iter().rposition(|&c| c == b')')?;
    let mut state = None;
    let mut blk = None;
    for (i, t) in b[(cp + 2).min(b.len())..].split(|&c| c == b' ').enumerate() {
        match i {
            0 => state = t.first().copied(),
            39 => {
                blk = parse_u64(trim(t));
                break;
            }
            _ => {}
        }
    }
    Some((state?, blk?))
}

struct TaskScan {
    dtasks: u32,
    blk: u64,
    wait_ns: u64,
    sched_ok: bool,
    reads: u32,
}

/// All threads of one process. None when the task directory can not be read.
fn scan_tasks(slurp: &mut Slurp, pbuf: &mut String, root: &str, pid: u32) -> Option<TaskScan> {
    pbuf.clear();
    let _ = write!(pbuf, "{root}/{pid}/task");
    let rd = fs::read_dir(&*pbuf).ok()?;
    let tids: Vec<u32> = rd.flatten().take(TASK_THREADS_MAX).filter_map(|e| parse_u64(e.file_name().as_encoded_bytes()).map(|t| t as u32)).collect();
    let mut ts = TaskScan { dtasks: 0, blk: 0, wait_ns: 0, sched_ok: true, reads: 0 };
    let mut any = false;
    for tid in tids {
        pbuf.clear();
        let _ = write!(pbuf, "{root}/{pid}/task/{tid}/stat");
        ts.reads += 1;
        if let Some((st, blk)) = slurp.read(pbuf).ok().and_then(task_stat) {
            any = true;
            if st == b'D' {
                ts.dtasks += 1;
            }
            ts.blk += blk;
        }
        pbuf.clear();
        let _ = write!(pbuf, "{root}/{pid}/task/{tid}/schedstat");
        ts.reads += 1;
        match slurp.read(pbuf).ok().and_then(|b| fields(b).nth(1).and_then(parse_u64)) {
            Some(w) => ts.wait_ns += w,
            None => ts.sched_ok = false,
        }
    }
    if any { Some(ts) } else { None }
}

/// Rate of a monotonic ns counter whose baseline may have a different scope.
fn sched_rate(p: &mut PS, wait: u64, scope: bool, el_s: f64) -> PV {
    let r = if !p.have_sched || p.sched_all != scope {
        PV::st(stc::UN_FIRST)
    } else if wait >= p.wait_ns {
        PV::val((wait - p.wait_ns) as f64 / 1e6 / el_s)
    } else {
        PV::st(stc::UN_RESET)
    };
    p.wait_ns = wait;
    p.have_sched = true;
    p.sched_all = scope;
    r
}

impl PScan {
    pub fn scan(&mut self, cfg: &Config, slurp: &mut Slurp, pbuf: &mut String, si: &ScanIn) {
        let root = cfg.sampler.proc_root.as_str();
        let dt_s = (si.dt_ms.max(1)) as f64 / 1000.0;
        let ck = si.clk_tck as f64;
        self.recs.clear();
        self.io_denied = 0;
        self.io_read = 0;
        self.task_scans = 0;
        self.swept = si.sweep;
        self.scanned = true;
        if !self.sync_cmdline && self.workers.is_none() {
            self.workers = Some(CmdWorkers::new());
        }
        let escalate = self.d_gap > 0;
        let mut budget = TASK_READ_BUDGET;
        let mut last_escalated = 0u32;
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
                dtasks: 0,
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
            // A field that does not parse is a parse error, never a zero.
            let mut bad = false;
            let mut pu = |t: &[u8]| -> u64 {
                parse_u64(trim(t)).unwrap_or_else(|| {
                    bad = true;
                    0
                })
            };
            // Fields after ")": index 0 = state (field 3). majflt = field 12, utime 14,
            // stime 15, num_threads 20, starttime 22, rss 24, delayacct_blkio_ticks 42.
            for (i, t) in b[(cp + 2).min(b.len())..].split(|&c| c == b' ').enumerate() {
                match i {
                    0 => rec.state = t.first().copied().unwrap_or(b'?'),
                    9 => majflt = pu(t),
                    11 => ut = pu(t),
                    12 => st = pu(t),
                    17 => rec.thr = pu(t) as u32,
                    19 => rec.start = pu(t),
                    21 => rec.rss = pu(t),
                    39 => {
                        blk = pu(t);
                        break;
                    }
                    _ => {}
                }
            }
            rec.dtasks = (rec.state == b'D') as u32;
            let key = (pid, rec.start);
            if bad {
                rec.cpu_pct = PV::st(stc::UN_PARSE);
                rec.majflt = PV::st(stc::UN_PARSE);
                rec.blk = PV::st(stc::UN_PARSE);
                self.map.entry(key).or_default().seen = si.tick;
                self.recs.push(rec);
                continue;
            }
            let cpu = ut + st;
            let known = self.map.get(&key);
            let (prev_cpu, prev_enriched) = known.map(|p| (if p.have_stat { Some(p.cpu) } else { None }, p.enriched)).unwrap_or((None, false));
            if let Some(pc) = prev_cpu {
                if cpu >= pc {
                    rec.cpu_d = cpu - pc;
                    rec.cpu_pct = PV::val(rec.cpu_d as f64 / ck / dt_s * 100.0);
                } else {
                    rec.cpu_pct = PV::st(stc::UN_RESET);
                }
            }
            let base_cand = known.is_none() || prev_enriched || rec.cpu_d > 0 || rec.state == b'D';
            let candidate = si.sweep || base_cand;
            // Per-thread view: for the real candidates, and for a budgeted slice of all
            // multi-threaded processes while D tasks are unaccounted for.
            let want_tasks = rec.thr > 1 && budget > 0 && (base_cand || (escalate && pid > self.cursor));
            let mut tasks: Option<TaskScan> = None;
            if want_tasks {
                tasks = scan_tasks(slurp, pbuf, root, pid);
                if let Some(t) = &tasks {
                    budget = budget.saturating_sub(t.reads);
                    self.task_scans += 1;
                    rec.dtasks = t.dtasks;
                    if !base_cand {
                        last_escalated = pid;
                    }
                }
            }
            let mut io: Option<Result<(u64, u64), u8>> = None;
            let mut sched: Option<u64> = None;
            if candidate {
                self.io_read += 1;
                pbuf.clear();
                let _ = write!(pbuf, "{root}/{pid}/io");
                io = Some(match slurp.read(pbuf) {
                    Ok(b) => {
                        let (mut r, mut w) = (0u64, 0u64);
                        let mut ok = true;
                        for l in lines(b) {
                            if let Some((k, v)) = l.iter().position(|&c| c == b' ').map(|p| (&l[..p], &l[p + 1..])) {
                                if k == b"read_bytes:" {
                                    match parse_u64(trim(v)) {
                                        Some(x) => r = x,
                                        None => ok = false,
                                    }
                                } else if k == b"write_bytes:" {
                                    match parse_u64(trim(v)) {
                                        Some(x) => w = x,
                                        None => ok = false,
                                    }
                                }
                            }
                        }
                        if ok { Ok((r, w)) } else { Err(stc::UN_PARSE) }
                    }
                    Err(e) => {
                        let c = err_state(&e, stc::UN_READ);
                        if c == stc::UN_PERM {
                            self.io_denied += 1;
                        }
                        Err(c)
                    }
                });
                match &tasks {
                    Some(t) if t.sched_ok => sched = Some(t.wait_ns),
                    _ => {
                        pbuf.clear();
                        let _ = write!(pbuf, "{root}/{pid}/schedstat");
                        if let Ok(b) = slurp.read(pbuf) {
                            sched = fields(b).nth(1).and_then(parse_u64);
                        }
                    }
                }
            }
            let sched_scope = matches!(&tasks, Some(t) if t.sched_ok);
            let (blk_cur, blk_scope) = match &tasks {
                Some(t) => (t.blk, true),
                None => (blk, false),
            };
            let p = self.map.entry(key).or_default();
            if p.have_stat {
                // majflt and blkio use the stat interval
                rec.majflt = if p.majflt <= majflt { PV::val((majflt - p.majflt) as f64 / dt_s) } else { PV::st(stc::UN_RESET) };
                rec.blk = match si.delayacct {
                    Some(true) if p.blk_all != blk_scope => PV::st(stc::UN_FIRST),
                    Some(true) if p.blk <= blk_cur => PV::val((blk_cur - p.blk) as f64 * 1000.0 / ck / dt_s),
                    Some(true) => PV::st(stc::UN_RESET),
                    Some(false) => PV::st(stc::NA_DELAYACCT),
                    None => PV::st(stc::UN_READ),
                };
            } else if si.delayacct == Some(false) {
                rec.blk = PV::st(stc::NA_DELAYACCT);
            }
            p.cpu = cpu;
            p.have_stat = true;
            p.majflt = majflt;
            p.blk = blk_cur;
            p.blk_all = blk_scope;
            p.seen = si.tick;
            let el_s = (si.now_ms.saturating_sub(p.io_t)).max(1) as f64 / 1000.0;
            match io {
                Some(Ok((r, w))) => {
                    if p.have_io && r >= p.rb && w >= p.wb {
                        rec.ior = PV::val((r - p.rb) as f64 / el_s);
                        rec.iow = PV::val((w - p.wb) as f64 / el_s);
                        rec.io_rank = rec.ior.v + rec.iow.v;
                    } else if p.have_io {
                        rec.ior = PV::st(stc::UN_RESET);
                        rec.iow = PV::st(stc::UN_RESET);
                    } else {
                        rec.ior = PV::st(stc::UN_FIRST);
                        rec.iow = PV::st(stc::UN_FIRST);
                    }
                    p.rb = r;
                    p.wb = w;
                    p.have_io = true;
                    rec.rundelay = match sched {
                        Some(wait) => sched_rate(p, wait, sched_scope, el_s),
                        None => PV::st(stc::UN_READ),
                    };
                    p.io_t = si.now_ms;
                }
                Some(Err(c)) => {
                    rec.ior = PV::st(c);
                    rec.iow = PV::st(c);
                    // schedstat is world-readable in practice but follows the same file rules
                    if let Some(wait) = sched {
                        rec.rundelay = sched_rate(p, wait, sched_scope, el_s);
                        p.io_t = si.now_ms;
                    }
                }
                None => {}
            }
            self.recs.push(rec);
        }
        if escalate && budget > 0 {
            self.cursor = 0; // reached the end of the pid list: start over
        } else if last_escalated != 0 {
            self.cursor = last_escalated;
        }
        if !escalate {
            self.cursor = 0;
        }
        let tick = si.tick;
        self.map.retain(|_, p| p.seen == tick);
        self.total = self.recs.len() as u32;
        self.dstate = self.recs.iter().map(|r| r.dtasks).sum();
        for p in self.map.values_mut() {
            p.enriched = false;
        }
        // Bounded enriched union: all with D tasks (cap), then top CPU and top IO interleaved.
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
            if recs[i].dtasks > 0 && chosen.len() < cap {
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
            if recs[i].dtasks > 0 && self.mark[i] == 0 {
                if d_reads >= cap * 4 {
                    self.d_unattributed += recs[i].dtasks;
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
                    Some(c) => *self.dmap.entry(c).or_insert(0) += r.dtasks,
                    None => self.d_unattributed += r.dtasks,
                }
            }
        }
        // Command lines that the workers answered since the last tick.
        if let Some(w) = &self.workers {
            for ((pid, start), res) in w.take_answers() {
                if let Some(p) = self.map.get_mut(&(pid, start)) {
                    match res {
                        Ok(s) => {
                            p.cmd = s;
                            p.cmd_state = 2;
                        }
                        Err(c) => {
                            if p.cmd_state != 2 {
                                p.cmd_state = 3;
                                p.cmd_err = c;
                                p.cmd_since = si.now_ms;
                            }
                        }
                    }
                }
            }
        }
        self.cmd_pending = 0;
        for (i, why) in chosen {
            let r = recs[i];
            let key = (r.pid, r.start);
            let p = self.map.get_mut(&key).unwrap();
            p.enriched = true;
            let refresh = p.meta_tick == 0 || si.sweep;
            if refresh {
                pbuf.clear();
                let _ = write!(pbuf, "{root}/{}/cgroup", r.pid);
                if let Some(c) = cgroup_of(slurp, pbuf) {
                    p.cgroup = c;
                }
            }
            let path = |pid: u32| format!("{root}/{pid}/cmdline");
            if self.sync_cmdline {
                if refresh {
                    match read_now(&path(r.pid), cfg.process.cmdline_max) {
                        Ok(s) => {
                            p.cmd = s;
                            p.cmd_state = 2;
                        }
                        Err(c) => {
                            p.cmd_state = 3;
                            p.cmd_err = c;
                        }
                    }
                }
            } else {
                let ask = match p.cmd_state {
                    0 => true,
                    1 => {
                        if si.now_ms.saturating_sub(p.cmd_since) > CMD_TIMEOUT_MS {
                            p.cmd_state = 3;
                            p.cmd_err = stc::UN_TIMEOUT;
                            p.cmd_since = si.now_ms;
                            self.cmd_timeouts += 1;
                        }
                        false
                    }
                    2 => si.sweep,
                    _ => si.now_ms.saturating_sub(p.cmd_since) > CMD_RETRY_MS,
                };
                if ask {
                    if let Some(w) = &self.workers {
                        if w.ask(r.pid, r.start, path(r.pid), cfg.process.cmdline_max) && p.cmd_state != 2 {
                            p.cmd_state = 1;
                            p.cmd_since = si.now_ms;
                        }
                    }
                }
            }
            if refresh {
                p.meta_tick = si.tick;
            }
            if r.dtasks > 0 {
                *self.dmap.entry(p.cgroup.clone()).or_insert(0) += r.dtasks;
            }
            let cmd_state = match p.cmd_state {
                2 => PV::val(0.0),
                3 => PV::st(p.cmd_err),
                _ => PV::st(stc::UN_NOT_ENRICHED),
            };
            if p.cmd_state != 2 {
                self.cmd_pending += 1;
            }
            self.out.push(ProcOut {
                pid: r.pid,
                start: r.start,
                comm: String::from_utf8_lossy(&r.comm[..r.clen as usize]).into_owned(),
                state: r.state as char,
                d_tasks: r.dtasks,
                cgroup: ent_of_rel(&p.cgroup),
                cmdline: p.cmd.clone(),
                cmd_state,
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
