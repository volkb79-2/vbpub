//! Unix socket, JSON lines. Wire contract: `docs/SAMPLER-PROTOCOL.md` (version PROTOCOL).
//! Requests: hello, current, series, events, status. Every response carries the sampler
//! epoch, so a client sees a restart; every response is bounded by
//! `protocol.max_response_bytes` and says `truncated` when it had to stop.
//!
//! Memory (S-02): `series` and `events` are heavy (they read stored history). At most
//! `MAX_HEAVY` run at once; the next gets a typed `busy` error. A heavy request streams its
//! segments one frame at a time. A panic while serving a request becomes an `internal` error
//! and never takes the daemon down.
use crate::cfg::Config;
use crate::collect::{Collector, Val};
use crate::procs::{ProcOut, PV};
use crate::store::{self, TierInfo};
use crate::util::*;
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::os::unix::net::{UnixListener, UnixStream};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

pub const PROTOCOL: u32 = 1;
pub const SCHEMA: u32 = 1;
/// Concurrent `series` / `events` requests; more get `busy`.
pub const MAX_HEAVY: usize = 2;

/// What the writer thread and the stores report (S-01, S-03, S-11).
#[derive(Default, Clone)]
pub struct StoreStatus {
    pub enabled: bool,
    pub queue_len: u64,
    pub dropped_frames: u64,
    pub lag_ms: u64,
    pub last_job_us: u64,
    pub write_errors: u64,
    pub last_error: String,
    pub torn_frames_seen: u64,
    pub corrupt_frame_reads: u64,
    pub frames_skipped: u64,
    pub cold_other_bytes: u64,
}

#[derive(Default, Clone)]
pub struct Status {
    pub uptime_s: u64,
    pub ticks: u64,
    pub cpu_recent: Option<PV>,
    pub cpu_avg: Option<PV>,
    pub rss: Option<u64>,
    pub last_tick_us: u64,
    pub overruns: u64,
    pub skipped: u64,
    /// procs, cgroups, host, store (microseconds, last tick; "store" is the writer thread's last job).
    pub stage_us: [u128; 4],
    /// nr_throttled, throttled_usec, memory.events high, memory.events max.
    pub throttle: Option<[PV; 4]>,
    /// The cgroups those were read from (cpu limit carrier, memory limit carrier).
    pub throttle_src: [Option<String>; 2],
    pub tiers: Vec<TierInfo>,
    pub cold_bytes: u64,
    pub cold_cap: u64,
    pub tmpfs_bytes: u64,
    pub ids_freed: u64,
    pub store: StoreStatus,
    pub cmd_pending: u32,
    pub cmd_timeouts: u64,
}

pub struct Snapshot {
    pub tick: u64,
    pub ts_ms: u64,
    pub dt_ms: u64,
    pub work_us: u64,
    pub frame: Arc<Vec<(u32, Vec<Val>)>>,
    pub mets: Arc<Vec<(String, bool)>>,
    pub ents: Arc<Vec<String>>,
    pub sticky: Arc<HashMap<u32, HashMap<u32, Val>>>,
    pub procs: Vec<ProcOut>,
    /// total, d_state, io_denied, io_read, full_sweep, d_unattributed, scanned, d_unseen, task_scans
    pub pids: [u64; 9],
    pub n_cgroups: usize,
    pub resets: u64,
    pub status: Status,
}

impl Default for Snapshot {
    fn default() -> Self {
        Snapshot {
            tick: 0,
            ts_ms: 0,
            dt_ms: 0,
            work_us: 0,
            frame: Arc::new(Vec::new()),
            mets: Arc::new(Vec::new()),
            ents: Arc::new(Vec::new()),
            sticky: Arc::new(HashMap::new()),
            procs: Vec::new(),
            pids: [0; 9],
            n_cgroups: 0,
            resets: 0,
            status: Status::default(),
        }
    }
}

/// Builds snapshots, sharing the (rarely changing) name tables between them.
#[derive(Default)]
pub struct SnapBuilder {
    ver: Option<u64>,
    mets: Arc<Vec<(String, bool)>>,
    ents: Arc<Vec<String>>,
}

impl SnapBuilder {
    pub fn build(&mut self, col: &mut Collector, tick: u64, ts_ms: u64, dt_ms: u64, work_us: u64, status: Status) -> Snapshot {
        if self.ver != Some(col.names.dict.version) {
            self.mets = Arc::new(col.names.dict.mets.clone());
            self.ents = Arc::new(col.names.dict.ents.clone());
            self.ver = Some(col.names.dict.version);
        }
        let p = &col.procs;
        let pids = [
            p.total as u64,
            p.dstate as u64,
            p.io_denied,
            p.io_read as u64,
            p.swept as u64,
            p.d_unattributed as u64,
            p.scanned as u64,
            p.d_gap,
            p.task_scans as u64,
        ];
        Snapshot {
            tick,
            ts_ms,
            dt_ms,
            work_us,
            frame: Arc::new(std::mem::take(&mut col.frame)),
            mets: self.mets.clone(),
            ents: self.ents.clone(),
            sticky: col.sticky.clone(),
            procs: std::mem::take(&mut col.procs.out),
            pids,
            n_cgroups: col.n_cgroups,
            resets: col.resets,
            status,
        }
    }
}

pub struct Ctx {
    pub cfg: Config,
    pub epoch: String,
    pub started_ms: u64,
    pub view_json: String,
    snap: Mutex<Arc<Snapshot>>,
    clients: AtomicUsize,
    heavy: AtomicUsize,
}

/// Releases a heavy-request slot when dropped.
pub struct HeavyGuard<'a>(&'a AtomicUsize);
impl Drop for HeavyGuard<'_> {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::SeqCst);
    }
}

impl Ctx {
    pub fn new(cfg: Config, view_json: String) -> Ctx {
        let started_ms = wall_ms();
        static SEQ: AtomicUsize = AtomicUsize::new(0);
        let epoch = format!("{:x}-{:x}-{:x}", started_ms, std::process::id(), SEQ.fetch_add(1, Ordering::SeqCst));
        Ctx { cfg, epoch, started_ms, view_json, snap: Mutex::new(Arc::new(Snapshot::default())), clients: AtomicUsize::new(0), heavy: AtomicUsize::new(0) }
    }
    /// The lock is held only for the pointer swap; readers serialize outside it.
    pub fn publish(&self, s: Snapshot) {
        *self.snap.lock().unwrap_or_else(|e| e.into_inner()) = Arc::new(s);
    }
    pub fn snap(&self) -> Arc<Snapshot> {
        self.snap.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }
    pub fn heavy_slot(&self) -> Result<HeavyGuard<'_>, PErr> {
        if self.heavy.fetch_add(1, Ordering::SeqCst) >= MAX_HEAVY {
            self.heavy.fetch_sub(1, Ordering::SeqCst);
            return Err(perr("busy", format!("{MAX_HEAVY} history requests are already running; retry shortly")));
        }
        Ok(HeavyGuard(&self.heavy))
    }
}

pub fn spawn(ctx: Arc<Ctx>) -> std::io::Result<()> {
    use std::os::unix::fs::DirBuilderExt;
    let path = ctx.cfg.sampler.socket_path.clone();
    if let Some(p) = std::path::Path::new(&path).parent() {
        let _ = std::fs::DirBuilder::new().recursive(true).mode(0o700).create(p);
    }
    // One sampler per socket: a second instance would steal the socket and share the store files.
    let lock = std::fs::OpenOptions::new().create(true).write(true).mode(0o600).open(format!("{path}.lock"))?;
    if !try_flock(&lock) {
        return Err(std::io::Error::new(std::io::ErrorKind::AddrInUse, format!("{path}.lock is held: another topos-sampler is running")));
    }
    let _ = std::fs::remove_file(&path);
    let l = UnixListener::bind(&path)?;
    // Root only: `current` carries full command lines. Group access is an operator decision
    // that needs command-line redaction first (docs/SAMPLER-PROTOCOL.md, Transport).
    let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
    std::thread::Builder::new().name("accept".into()).spawn(move || {
        let _lock = lock;
        for s in l.incoming() {
            let s = match s {
                Ok(s) => s,
                Err(_) => {
                    // EMFILE and friends persist: do not spin
                    std::thread::sleep(Duration::from_millis(100));
                    continue;
                }
            };
            if ctx.clients.fetch_add(1, Ordering::SeqCst) >= ctx.cfg.protocol.max_clients {
                let mut s = s;
                let _ = s.set_write_timeout(Some(Duration::from_millis(ctx.cfg.protocol.client_write_ms)));
                let _ = s.write_all(err_json(&ctx, None, &perr("too_many_clients", "client limit reached")).as_bytes());
                let _ = s.write_all(b"\n");
                ctx.clients.fetch_sub(1, Ordering::SeqCst);
                continue;
            }
            let c = ctx.clone();
            let r = std::thread::Builder::new().name("client".into()).stack_size(512 * 1024).spawn(move || {
                conn(s, &c);
                c.clients.fetch_sub(1, Ordering::SeqCst);
            });
            if r.is_err() {
                ctx.clients.fetch_sub(1, Ordering::SeqCst);
            }
        }
    })?;
    Ok(())
}

/// Write everything, giving up after `total` however slowly the peer drains.
fn write_deadline(w: &mut UnixStream, data: &[u8], total: Duration) -> bool {
    let t0 = Instant::now();
    let mut off = 0;
    while off < data.len() {
        if t0.elapsed() > total {
            return false;
        }
        match w.write(&data[off..]) {
            Ok(0) => return false,
            Ok(n) => off += n,
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => {}
            Err(_) => return false,
        }
    }
    true
}

fn conn(s: UnixStream, ctx: &Ctx) {
    let p = &ctx.cfg.protocol;
    let _ = s.set_read_timeout(Some(Duration::from_millis(p.client_idle_ms)));
    let _ = s.set_write_timeout(Some(Duration::from_millis(p.client_write_ms)));
    let total = Duration::from_millis(p.client_write_ms.saturating_mul(4));
    let mut w = match s.try_clone() {
        Ok(w) => w,
        Err(_) => return,
    };
    let mut r = BufReader::new(s);
    loop {
        let mut buf = Vec::new();
        let n = match (&mut r).take(p.max_request_bytes as u64 + 1).read_until(b'\n', &mut buf) {
            Ok(n) => n,
            Err(_) => return, // idle timeout or reset
        };
        if n == 0 {
            return;
        }
        if buf.last() != Some(&b'\n') {
            if buf.len() > p.max_request_bytes {
                let mut e = err_json(ctx, None, &perr("request_too_large", format!("request over {} bytes", p.max_request_bytes)));
                e.push('\n');
                let _ = write_deadline(&mut w, e.as_bytes(), total);
            }
            return;
        }
        buf.pop();
        let mut reply = handle(ctx, &String::from_utf8_lossy(&buf));
        reply.push('\n');
        // A client that does not read blocks only its own thread, and only until the deadline.
        if !write_deadline(&mut w, reply.as_bytes(), total) {
            return;
        }
    }
}

fn err_json(ctx: &Ctx, id: Option<&str>, e: &PErr) -> String {
    let mut o = String::from("{\"ok\":false,");
    if let Some(i) = id {
        o.push_str(&format!("\"id\":{i},"));
    }
    o.push_str(&format!("\"protocol\":{PROTOCOL},\"epoch\":\"{}\",\"error\":{{\"code\":\"{}\",\"message\":", ctx.epoch, e.code));
    jstr(&mut o, &e.msg);
    o.push_str("}}");
    o
}

/// Handle one request line; always returns exactly one JSON line (a panic is an `internal` error).
pub fn handle(ctx: &Ctx, line: &str) -> String {
    match catch_unwind(AssertUnwindSafe(|| handle_inner(ctx, line))) {
        Ok(s) => s,
        Err(_) => err_json(ctx, None, &perr("internal", "internal error while serving the request")),
    }
}

fn handle_inner(ctx: &Ctx, line: &str) -> String {
    let v: serde_json::Value = match serde_json::from_str(line) {
        Ok(v) => v,
        Err(e) => return err_json(ctx, None, &perr("bad_json", e.to_string())),
    };
    let id = match &v["id"] {
        serde_json::Value::String(_) | serde_json::Value::Number(_) => Some(v["id"].to_string()),
        _ => None,
    };
    let req = match v["req"].as_str() {
        Some(r) => r,
        None => return err_json(ctx, id.as_deref(), &perr("bad_request", "req (string) required")),
    };
    let mut o = String::with_capacity(4096);
    o.push_str("{\"ok\":true,\"req\":\"");
    o.push_str(req);
    o.push_str("\",");
    if let Some(i) = &id {
        o.push_str(&format!("\"id\":{i},"));
    }
    o.push_str(&format!("\"protocol\":{PROTOCOL},\"epoch\":\"{}\",", ctx.epoch));
    // The body is written straight into the reply (no second copy of a multi-megabyte answer).
    let truncated = match req {
        "hello" => Ok(hello(ctx, &mut o)),
        "current" => Ok(current(ctx, v["prefix"].as_str().unwrap_or(""), v["procs"].as_bool().unwrap_or(true), &Proj::from_req(&v), &mut o)),
        "status" => Ok(status(ctx, &mut o)),
        "series" => series_req(ctx, &v, &mut o),
        "events" => events_req(ctx, &v, &mut o),
        #[cfg(test)]
        "__panic" => panic!("test: a request handler panics"),
        _ => Err(perr("unknown_req", format!("unknown req {req}"))),
    };
    match truncated {
        Ok(t) => {
            o.push_str(&format!(",\"truncated\":{t}}}"));
            o
        }
        Err(e) => err_json(ctx, id.as_deref(), &e),
    }
}

fn hello(ctx: &Ctx, o: &mut String) -> bool {
    let c = &ctx.cfg;
    let (ck, cks) = clk_tck();
    let (pg, pgs) = page_size();
    o.push_str(&format!(
        "\"schema\":{SCHEMA},\"version\":\"{}\",\"pid\":{},\"started_ms\":{},\"tick_ms\":{},\"clk_tck\":{ck},\"clk_tck_source\":\"{}\",\"page_size\":{pg},\"page_size_source\":\"{}\",",
        env!("CARGO_PKG_VERSION"),
        std::process::id(),
        ctx.started_ms,
        c.sampler.tick_ms,
        if cks { "sysconf" } else { "fallback" },
        if pgs { "sysconf" } else { "fallback" }
    ));
    o.push_str("\"cgroup_view\":");
    o.push_str(&ctx.view_json);
    o.push_str(&format!(
        ",\"limits\":{{\"max_request_bytes\":{},\"max_response_bytes\":{},\"max_clients\":{},\"max_heavy_requests\":{}}},",
        c.protocol.max_request_bytes, c.protocol.max_response_bytes, c.protocol.max_clients, MAX_HEAVY
    ));
    o.push_str(&format!(
        "\"process\":{{\"scan\":{},\"top_cpu\":{},\"top_io\":{},\"cap\":{},\"full_sweep_every_s\":{}}},\"tiers\":[",
        c.process.scan, c.process.top_cpu, c.process.top_io, c.process.cap, c.process.full_sweep_every_s
    ));
    for (i, t) in c.tier.iter().enumerate() {
        if i > 0 {
            o.push(',');
        }
        o.push_str(&format!("{{\"name\":\"{}\",\"step_s\":{},\"keep_s\":{},\"segment_s\":{},\"medium\":\"{}\"}}", t.name, t.step_s, t.keep_s, t.segment_s, t.medium));
    }
    o.push_str("],\"requests\":[\"hello\",\"current\",\"series\",\"events\",\"status\"],\"state_codes\":[");
    for code in 0..=stc::MAX {
        if code > 0 {
            o.push(',');
        }
        let (s, w) = state_name(code);
        o.push_str(&format!("{{\"code\":{code},\"state\":\"{s}\",\"why\":\"{w}\"}}"));
    }
    o.push(']');
    false
}

fn jpv(o: &mut String, p: &PV) {
    o.push('{');
    jstate(o, p.st);
    if p.st == stc::VALUE {
        o.push_str(",\"v\":");
        jnum(o, p.v);
    }
    o.push('}');
}

fn jopt(o: &mut String, p: &Option<PV>) {
    match p {
        Some(p) => jpv(o, p),
        None => jpv(o, &PV::st(stc::UN_READ)),
    }
}

fn jopt_str(o: &mut String, s: &Option<String>) {
    match s {
        Some(s) => jstr(o, s),
        None => o.push_str("null"),
    }
}

fn status(ctx: &Ctx, o: &mut String) -> bool {
    let s = ctx.snap();
    let st = &s.status;
    o.push_str(&format!("\"uptime_s\":{},\"ticks\":{},\"tick_ms\":{},\"self\":{{\"cpu_pct_recent_60s\":", st.uptime_s, st.ticks, ctx.cfg.sampler.tick_ms));
    jopt(o, &st.cpu_recent);
    o.push_str(",\"cpu_pct_since_start\":");
    jopt(o, &st.cpu_avg);
    o.push_str(",\"rss_bytes\":");
    jopt(o, &st.rss.map(|r| PV::val(r as f64)));
    o.push_str(&format!(
        "}},\"timing\":{{\"last_tick_us\":{},\"tick_budget_us\":{},\"tick_overruns\":{},\"skipped_ticks\":{},\"last_stage_us\":{{\"procs\":{},\"cgroups\":{},\"host\":{},\"store\":{}}}}},",
        st.last_tick_us, ctx.cfg.sampler.tick_ms * 1000, st.overruns, st.skipped, st.stage_us[0], st.stage_us[1], st.stage_us[2], st.stage_us[3]
    ));
    o.push_str("\"throttle\":{");
    let names = ["nr_throttled", "throttled_usec", "memory_high_events", "memory_max_events"];
    for (i, n) in names.iter().enumerate() {
        if i > 0 {
            o.push(',');
        }
        o.push_str(&format!("\"{n}\":"));
        match &st.throttle {
            Some(t) => jpv(o, &t[i]),
            None => jpv(o, &PV::st(stc::UN_READ)),
        }
    }
    o.push_str(",\"cpu_cgroup\":");
    jopt_str(o, &st.throttle_src[0]);
    o.push_str(",\"memory_cgroup\":");
    jopt_str(o, &st.throttle_src[1]);
    o.push_str("},\"tiers\":[");
    for (i, t) in st.tiers.iter().enumerate() {
        if i > 0 {
            o.push(',');
        }
        o.push_str(&format!(
            "{{\"name\":\"{}\",\"medium\":\"{}\",\"segments\":{},\"bytes\":{},\"oldest_s\":{},\"newest_s\":{},\"keep_s\":{},\"fill\":{:.4}}}",
            t.name, t.medium, t.segments, t.bytes, t.oldest_s, t.newest_s, t.keep_s, t.fill
        ));
    }
    o.push_str(&format!(
        "],\"disk\":{{\"cold_bytes\":{},\"cold_cap_bytes\":{},\"cold_other_bytes\":{}}},\"tmpfs\":{{\"bytes\":{}}},\"dict\":{{\"metrics\":{},\"entity_slots\":{},\"ids_freed_total\":{}}},\"counter_resets\":{},",
        st.cold_bytes, st.cold_cap, st.store.cold_other_bytes, st.tmpfs_bytes, s.mets.len(), s.ents.len(), st.ids_freed, s.resets
    ));
    let w = &st.store;
    o.push_str(&format!(
        "\"store\":{{\"enabled\":{},\"queue_len\":{},\"dropped_frames\":{},\"writer_lag_ms\":{},\"last_job_us\":{},\"write_errors\":{},\"frames_skipped\":{},\"torn_frames_seen\":{},\"corrupt_frame_reads\":{},\"last_error\":",
        w.enabled, w.queue_len, w.dropped_frames, w.lag_ms, w.last_job_us, w.write_errors, w.frames_skipped, w.torn_frames_seen, w.corrupt_frame_reads
    ));
    jstr(o, &w.last_error);
    o.push_str(&format!("}},\"cmdline\":{{\"pending\":{},\"timeouts\":{}}}", st.cmd_pending, st.cmd_timeouts));
    false
}

/// Optional shaping of `current` (all additive; the default is the full form).
/// `fields`: metric names, an entry ending in `*` is a prefix. `compact`: no `raw`; gauges are
/// bare numbers, counters `[delta, rate_per_s]`, `unavailable` states stay typed objects, and
/// `not_applicable` states (expected absences) are left out.
#[derive(Default)]
pub struct Proj {
    names: Vec<String>,
    any: bool,
    compact: bool,
}

impl Proj {
    pub fn from_req(v: &serde_json::Value) -> Proj {
        let names: Vec<String> = v["fields"].as_array().map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect()).unwrap_or_default();
        Proj { any: v["fields"].is_array(), names, compact: v["compact"].as_bool().unwrap_or(false) }
    }
    fn keep(&self, name: &str) -> bool {
        !self.any || self.names.iter().any(|n| n == name || (n.ends_with('*') && name.starts_with(&n[..n.len() - 1])))
    }
}

/// Writes `"name":value` (with the separating comma) unless filtered out; true if written.
fn put_metric(o: &mut String, s: &Snapshot, dt: f64, v: &Val, pj: &Proj, first: &mut bool) -> bool {
    let name = &s.mets[v.m as usize].0;
    if !pj.keep(name) {
        return false;
    }
    if pj.compact {
        if v.st != stc::VALUE && v.st <= stc::NA_NO_FULL {
            return false;
        }
        if !*first {
            o.push(',');
        }
        *first = false;
        jstr(o, name);
        o.push(':');
        if v.st != stc::VALUE {
            o.push('{');
            jstate(o, v.st);
            o.push('}');
        } else if v.raw != NONE {
            o.push('[');
            jnum(o, v.v);
            o.push(',');
            jnum(o, v.v / dt);
            o.push(']');
        } else {
            jnum(o, v.v);
        }
        return true;
    }
    if !*first {
        o.push(',');
    }
    *first = false;
    put_metric_full(o, s, dt, v);
    true
}

fn put_metric_full(o: &mut String, s: &Snapshot, dt: f64, v: &Val) {
    jstr(o, &s.mets[v.m as usize].0);
    o.push_str(":{");
    jstate(o, v.st);
    if v.st == stc::VALUE {
        o.push_str(",\"v\":");
        jnum(o, v.v);
        if v.raw != NONE {
            o.push_str(",\"rate\":");
            jnum(o, v.v / dt);
        }
    }
    if v.raw != NONE {
        o.push_str(&format!(",\"raw\":{}", v.raw));
    }
    o.push('}');
}

fn current(ctx: &Ctx, prefix: &str, with_procs: bool, pj: &Proj, out: &mut String) -> bool {
    let s = ctx.snap();
    let cap = ctx.cfg.protocol.max_response_bytes.saturating_sub(1024);
    let base = out.len();
    let dt = (s.dt_ms.max(1)) as f64 / 1000.0;
    out.push_str(&format!(
        "\"tick\":{},\"ts_ms\":{},\"dt_ms\":{},\"work_us\":{},\"cgroups\":{},\"counter_resets\":{},\"pids\":{{\"total\":{},\"d_state\":{},\"io_denied\":{},\"io_read\":{},\"full_sweep\":{},\"d_unattributed\":{},\"scanned\":{},\"d_unseen\":{},\"task_scans\":{}}},\"entities\":{{",
        s.tick, s.ts_ms, s.dt_ms, s.work_us, s.n_cgroups, s.resets, s.pids[0], s.pids[1], s.pids[2], s.pids[3], s.pids[4] != 0, s.pids[5], s.pids[6] != 0, s.pids[7], s.pids[8]
    ));
    let mut truncated = false;
    let mut first = true;
    // Entities with values this tick, then entities that only have sticky states (every
    // file unreadable): a cgroup in the tree is always listed, never silently dropped.
    let in_frame: std::collections::HashSet<u32> = s.frame.iter().map(|(i, _)| *i).collect();
    let mut sticky_only: Vec<u32> = s.sticky.keys().filter(|k| !in_frame.contains(k)).copied().collect();
    sticky_only.sort_unstable();
    let rows: Vec<(u32, &[Val])> = s.frame.iter().map(|(i, v)| (*i, v.as_slice())).chain(sticky_only.iter().map(|i| (*i, &[][..]))).collect();
    let mut e = String::new();
    for (id, vals) in rows.iter() {
        let name = match s.ents.get(*id as usize) {
            Some(n) => n,
            None => continue,
        };
        if !name.starts_with(prefix) {
            continue;
        }
        e.clear();
        jstr(&mut e, name);
        e.push_str(":{");
        let mut f2 = true;
        for x in vals.iter() {
            put_metric(&mut e, &s, dt, x, pj, &mut f2);
        }
        if let Some(l) = s.sticky.get(id) {
            for (m, x) in l {
                if !vals.iter().any(|v| v.m == *m) {
                    put_metric(&mut e, &s, dt, x, pj, &mut f2);
                }
            }
        }
        if pj.any && f2 {
            continue; // nothing of what was asked for on this entity
        }
        e.push('}');
        if out.len() - base + e.len() + 1 > cap {
            truncated = true;
            break;
        }
        if !first {
            out.push(',');
        }
        first = false;
        out.push_str(&e);
    }
    out.push_str("},\"procs\":[");
    if with_procs {
        let mut fp = true;
        for p in &s.procs {
            e.clear();
            e.push_str(&format!("{{\"pid\":{},\"start\":{},\"comm\":", p.pid, p.start));
            jstr(&mut e, &p.comm);
            e.push_str(&format!(",\"state\":\"{}\",\"d_tasks\":{},\"why\":\"{}\",\"cgroup\":", p.state, p.d_tasks, p.why));
            jstr(&mut e, &p.cgroup);
            e.push_str(",\"cmdline\":");
            jstr(&mut e, &p.cmdline);
            // the command line is read by a bounded helper: say when it is not there (yet)
            e.push_str(",\"cmdline_state\":");
            jpv(&mut e, &p.cmd_state);
            e.push_str(&format!(",\"rss_bytes\":{},\"threads\":{}", p.rss_bytes, p.threads));
            for (k, v) in [
                ("cpu_pct", &p.cpu_pct),
                ("majflt_per_s", &p.majflt_ps),
                ("io_read_bps", &p.io_read_bps),
                ("io_write_bps", &p.io_write_bps),
                ("run_delay_ms_per_s", &p.run_delay_ms_ps),
                ("blkio_delay_ms_per_s", &p.blkio_delay_ms_ps),
            ] {
                e.push_str(&format!(",\"{k}\":"));
                jpv(&mut e, v);
            }
            e.push('}');
            if out.len() - base + e.len() + 1 > cap {
                truncated = true;
                break;
            }
            if !fp {
                out.push(',');
            }
            fp = false;
            out.push_str(&e);
        }
    }
    out.push(']');
    truncated
}

fn series_req(ctx: &Ctx, v: &serde_json::Value, out: &mut String) -> Result<bool, PErr> {
    let metric = v["metric"].as_str().ok_or_else(|| perr("bad_request", "metric (string) required"))?;
    let tier = v["tier"].as_str().ok_or_else(|| perr("bad_request", "tier (string) required"))?;
    let entity = v["entity"].as_str().ok_or_else(|| perr("bad_request", "entity (string) required"))?;
    let window = v["window_s"].as_u64().ok_or_else(|| perr("bad_request", "window_s (unsigned integer) required"))?;
    let _slot = ctx.heavy_slot()?;
    let cap = ctx.cfg.protocol.max_response_bytes.saturating_sub(1024);
    let s = ctx.snap();
    let d = store::Dicts { mets: s.mets.as_slice(), ents: s.ents.as_slice() };
    let r = store::series(&ctx.cfg, &d, entity, metric, tier, window, cap)?;
    out.push_str(&r.body);
    out.push_str(&format!(",\"window_s\":{}", r.window_s));
    Ok(r.truncated)
}

fn events_req(ctx: &Ctx, v: &serde_json::Value, out: &mut String) -> Result<bool, PErr> {
    let window = v["window_s"].as_u64().ok_or_else(|| perr("bad_request", "window_s (unsigned integer) required"))?;
    let limit = v["limit"].as_u64().unwrap_or(1000).min(100_000) as usize;
    let _slot = ctx.heavy_slot()?;
    let cap = ctx.cfg.protocol.max_response_bytes.saturating_sub(1024);
    let (arr, truncated) = store::events(&ctx.cfg, v["entity"].as_str().unwrap_or(""), window, limit, cap);
    out.push_str("\"events\":");
    out.push_str(&arr);
    Ok(truncated)
}
