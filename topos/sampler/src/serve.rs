//! Unix socket, JSON lines. Wire contract: `docs/SAMPLER-PROTOCOL.md` (version PROTOCOL).
//! Requests: hello, current, series, events, status. Every response carries the sampler
//! epoch, so a client sees a restart; every response is bounded by
//! `protocol.max_response_bytes` and says `truncated` when it had to stop.
use crate::cfg::Config;
use crate::collect::{Collector, Val};
use crate::procs::{ProcOut, PV};
use crate::store::{self, TierInfo};
use crate::util::*;
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::{UnixListener, UnixStream};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

pub const PROTOCOL: u32 = 1;
pub const SCHEMA: u32 = 1;

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
    /// procs, cgroups, host, store (microseconds, last tick).
    pub stage_us: [u128; 4],
    /// nr_throttled, throttled_usec, memory.events high, memory.events max.
    pub throttle: Option<[PV; 4]>,
    pub tiers: Vec<TierInfo>,
    pub cold_bytes: u64,
    pub cold_cap: u64,
    pub tmpfs_bytes: u64,
    pub ids_freed: u64,
}

pub struct Snapshot {
    pub tick: u64,
    pub ts_ms: u64,
    pub dt_ms: u64,
    pub work_us: u64,
    pub frame: Vec<(u32, Vec<Val>)>,
    pub mets: Arc<Vec<(String, bool)>>,
    pub ents: Arc<Vec<String>>,
    pub sticky: Arc<HashMap<u32, HashMap<u32, Val>>>,
    pub procs: Vec<ProcOut>,
    pub pids: [u64; 7], // total, d_state, io_denied, io_read, full_sweep, d_unattributed, scanned
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
            frame: Vec::new(),
            mets: Arc::new(Vec::new()),
            ents: Arc::new(Vec::new()),
            sticky: Arc::new(HashMap::new()),
            procs: Vec::new(),
            pids: [0; 7],
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
        let pids = [p.total as u64, p.dstate as u64, p.io_denied, p.io_read as u64, p.swept as u64, p.d_unattributed as u64, p.scanned as u64];
        Snapshot {
            tick,
            ts_ms,
            dt_ms,
            work_us,
            frame: std::mem::take(&mut col.frame),
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
}

impl Ctx {
    pub fn new(cfg: Config, view_json: String) -> Ctx {
        let started_ms = wall_ms();
        static SEQ: AtomicUsize = AtomicUsize::new(0);
        let epoch = format!("{:x}-{:x}-{:x}", started_ms, std::process::id(), SEQ.fetch_add(1, Ordering::SeqCst));
        Ctx { cfg, epoch, started_ms, view_json, snap: Mutex::new(Arc::new(Snapshot::default())), clients: AtomicUsize::new(0) }
    }
    /// The lock is held only for the pointer swap; readers serialize outside it.
    pub fn publish(&self, s: Snapshot) {
        *self.snap.lock().unwrap() = Arc::new(s);
    }
    pub fn snap(&self) -> Arc<Snapshot> {
        self.snap.lock().unwrap().clone()
    }
}

pub fn spawn(ctx: Arc<Ctx>) -> std::io::Result<()> {
    let path = ctx.cfg.sampler.socket_path.clone();
    if let Some(p) = std::path::Path::new(&path).parent() {
        let _ = std::fs::create_dir_all(p);
    }
    let _ = std::fs::remove_file(&path);
    let l = UnixListener::bind(&path)?;
    let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
    std::thread::Builder::new().name("accept".into()).spawn(move || {
        for s in l.incoming().flatten() {
            if ctx.clients.fetch_add(1, Ordering::SeqCst) >= ctx.cfg.protocol.max_clients {
                let mut s = s;
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

fn conn(s: UnixStream, ctx: &Ctx) {
    let p = &ctx.cfg.protocol;
    let _ = s.set_read_timeout(Some(Duration::from_millis(p.client_idle_ms)));
    let _ = s.set_write_timeout(Some(Duration::from_millis(p.client_write_ms)));
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
                let _ = w.write_all(err_json(ctx, None, &perr("request_too_large", format!("request over {} bytes", p.max_request_bytes))).as_bytes());
                let _ = w.write_all(b"\n");
            }
            return;
        }
        buf.pop();
        let reply = handle(ctx, &String::from_utf8_lossy(&buf));
        // A client that does not read blocks only its own thread, and only until the write timeout.
        if w.write_all(reply.as_bytes()).is_err() || w.write_all(b"\n").is_err() {
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

/// Handle one request line; always returns exactly one JSON line.
pub fn handle(ctx: &Ctx, line: &str) -> String {
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
    let body = match req {
        "hello" => Ok(hello(ctx)),
        "current" => Ok(current(ctx, v["prefix"].as_str().unwrap_or(""), v["procs"].as_bool().unwrap_or(true))),
        "status" => Ok(status(ctx)),
        "series" => series_req(ctx, &v),
        "events" => events_req(ctx, &v),
        _ => Err(perr("unknown_req", format!("unknown req {req}"))),
    };
    match body {
        Ok((b, truncated)) => {
            let mut o = String::with_capacity(b.len() + 128);
            o.push_str("{\"ok\":true,\"req\":\"");
            o.push_str(req);
            o.push_str("\",");
            if let Some(i) = &id {
                o.push_str(&format!("\"id\":{i},"));
            }
            o.push_str(&format!("\"protocol\":{PROTOCOL},\"epoch\":\"{}\",", ctx.epoch));
            o.push_str(&b);
            o.push_str(&format!(",\"truncated\":{truncated}}}"));
            o
        }
        Err(e) => err_json(ctx, id.as_deref(), &e),
    }
}

fn hello(ctx: &Ctx) -> (String, bool) {
    let c = &ctx.cfg;
    let (ck, cks) = clk_tck();
    let (pg, pgs) = page_size();
    let mut o = String::new();
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
        ",\"limits\":{{\"max_request_bytes\":{},\"max_response_bytes\":{},\"max_clients\":{}}},",
        c.protocol.max_request_bytes, c.protocol.max_response_bytes, c.protocol.max_clients
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
    (o, false)
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

fn status(ctx: &Ctx) -> (String, bool) {
    let s = ctx.snap();
    let st = &s.status;
    let mut o = String::new();
    o.push_str(&format!("\"uptime_s\":{},\"ticks\":{},\"tick_ms\":{},\"self\":{{\"cpu_pct_recent_60s\":", st.uptime_s, st.ticks, ctx.cfg.sampler.tick_ms));
    jopt(&mut o, &st.cpu_recent);
    o.push_str(",\"cpu_pct_since_start\":");
    jopt(&mut o, &st.cpu_avg);
    o.push_str(",\"rss_bytes\":");
    jopt(&mut o, &st.rss.map(|r| PV::val(r as f64)));
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
            Some(t) => jpv(&mut o, &t[i]),
            None => jpv(&mut o, &PV::st(stc::UN_READ)),
        }
    }
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
        "],\"disk\":{{\"cold_bytes\":{},\"cold_cap_bytes\":{}}},\"tmpfs\":{{\"bytes\":{}}},\"dict\":{{\"metrics\":{},\"entity_slots\":{},\"ids_freed_total\":{}}},\"counter_resets\":{}",
        st.cold_bytes, st.cold_cap, st.tmpfs_bytes, s.mets.len(), s.ents.len(), st.ids_freed, s.resets
    ));
    (o, false)
}

fn put_metric(o: &mut String, s: &Snapshot, dt: f64, v: &Val) {
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

fn current(ctx: &Ctx, prefix: &str, with_procs: bool) -> (String, bool) {
    let s = ctx.snap();
    let cap = ctx.cfg.protocol.max_response_bytes.saturating_sub(1024);
    let dt = (s.dt_ms.max(1)) as f64 / 1000.0;
    let mut o = String::with_capacity(64 * 1024);
    o.push_str(&format!(
        "\"tick\":{},\"ts_ms\":{},\"dt_ms\":{},\"work_us\":{},\"cgroups\":{},\"counter_resets\":{},\"pids\":{{\"total\":{},\"d_state\":{},\"io_denied\":{},\"io_read\":{},\"full_sweep\":{},\"d_unattributed\":{},\"scanned\":{}}},\"entities\":{{",
        s.tick, s.ts_ms, s.dt_ms, s.work_us, s.n_cgroups, s.resets, s.pids[0], s.pids[1], s.pids[2], s.pids[3], s.pids[4] != 0, s.pids[5], s.pids[6] != 0
    ));
    let mut truncated = false;
    let mut first = true;
    for (id, vals) in s.frame.iter() {
        let name = &s.ents[*id as usize];
        if !name.starts_with(prefix) {
            continue;
        }
        let mut e = String::new();
        jstr(&mut e, name);
        e.push_str(":{");
        let mut f2 = true;
        for x in vals {
            if !f2 {
                e.push(',');
            }
            f2 = false;
            put_metric(&mut e, &s, dt, x);
        }
        if let Some(l) = s.sticky.get(id) {
            for (m, x) in l {
                if !vals.iter().any(|v| v.m == *m) {
                    if !f2 {
                        e.push(',');
                    }
                    f2 = false;
                    put_metric(&mut e, &s, dt, x);
                }
            }
        }
        e.push('}');
        if o.len() + e.len() + 1 > cap {
            truncated = true;
            break;
        }
        if !first {
            o.push(',');
        }
        first = false;
        o.push_str(&e);
    }
    o.push_str("},\"procs\":[");
    if with_procs {
        let mut fp = true;
        for p in &s.procs {
            let mut e = String::new();
            e.push_str(&format!("{{\"pid\":{},\"start\":{},\"comm\":", p.pid, p.start));
            jstr(&mut e, &p.comm);
            e.push_str(&format!(",\"state\":\"{}\",\"why\":\"{}\",\"cgroup\":", p.state, p.why));
            jstr(&mut e, &p.cgroup);
            e.push_str(",\"cmdline\":");
            jstr(&mut e, &p.cmdline);
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
            if o.len() + e.len() + 1 > cap {
                truncated = true;
                break;
            }
            if !fp {
                o.push(',');
            }
            fp = false;
            o.push_str(&e);
        }
    }
    o.push(']');
    (o, truncated)
}

fn series_req(ctx: &Ctx, v: &serde_json::Value) -> Result<(String, bool), PErr> {
    let metric = v["metric"].as_str().ok_or_else(|| perr("bad_request", "metric (string) required"))?;
    let tier = v["tier"].as_str().ok_or_else(|| perr("bad_request", "tier (string) required"))?;
    let entity = v["entity"].as_str().ok_or_else(|| perr("bad_request", "entity (string) required"))?;
    let window = v["window_s"].as_u64().ok_or_else(|| perr("bad_request", "window_s (unsigned integer) required"))?;
    let cap = ctx.cfg.protocol.max_response_bytes.saturating_sub(1024);
    let r = store::series(&ctx.cfg, entity, metric, tier, window, cap)?;
    Ok((format!("{},\"window_s\":{}", r.body, r.window_s), r.truncated))
}

fn events_req(ctx: &Ctx, v: &serde_json::Value) -> Result<(String, bool), PErr> {
    let window = v["window_s"].as_u64().ok_or_else(|| perr("bad_request", "window_s (unsigned integer) required"))?;
    let limit = v["limit"].as_u64().unwrap_or(1000).min(100_000) as usize;
    let cap = ctx.cfg.protocol.max_response_bytes.saturating_sub(1024);
    let (arr, truncated) = store::events(&ctx.cfg, v["entity"].as_str().unwrap_or(""), window, limit, cap);
    Ok((format!("\"events\":{arr}"), truncated))
}
