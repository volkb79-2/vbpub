//! Live feed: SAMPLER-PROTOCOL v1 over a Unix socket, spoken on a background thread.
//!
//! The UI never touches the socket. The thread connects, sends `hello`, then `current` (with
//! processes) once per sampler tick and `status` every few seconds, and turns each answer into a
//! [`Snap`] with the same metric keys the recording reader produces. Any failure (no socket, stalled
//! sampler, closed connection, bad JSON, protocol error) becomes a `Down` message and a reconnect
//! after a growing pause; the UI keeps showing the last data with a stale marker.
//!
//! Value states: `value` is a number; `not_applicable` ("does not exist here") is a dim `·`;
//! `unavailable` and any metric missing from the answer are `n/a`. Nothing becomes a zero.
use crate::data::*;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::sync::mpsc::{channel, Receiver, Sender};
use std::time::{Duration, Instant};

#[derive(Clone, Debug, Default)]
pub struct Hello {
    pub version: String,
    pub pid: u64,
    pub tick_ms: u64,
    pub epoch: String,
    pub view_mode: String,
}

#[derive(Clone, Debug, Default)]
pub struct Status {
    pub cpu_pct: Option<f64>,
    pub rss: Option<f64>,
    pub uptime_s: Option<f64>,
    pub overruns: Option<f64>,
    pub skipped: Option<f64>,
}

pub enum Msg {
    Hello(Hello),
    /// `bytes` and `parse_ms`: size of the `current` answer and the time to parse and convert it.
    Snap { snap: Box<Snap>, epoch: String, bytes: usize, parse_ms: f64 },
    Status(Status),
    Down(String),
}

// ------------------------------------------------------------ value objects

const NOTAPP: Mv = Mv { v: None, src: Src::Unlimited };

fn state(x: &Value, val: Option<f64>) -> Mv {
    match x["s"].as_str() {
        Some("value") => val.map_or(NA, |v| Mv { v: Some(v), src: Src::Exact }),
        Some("not_applicable") => NOTAPP,
        _ => NA,
    }
}
/// A gauge or other plain value.
fn g(m: &Value, k: &str) -> Mv {
    m.get(k).map_or(NA, |x| state(x, x["v"].as_f64()))
}
/// A counter as a per-second rate.
fn r(m: &Value, k: &str) -> Mv {
    m.get(k).map_or(NA, |x| state(x, x["rate"].as_f64()))
}
fn sc(m: Mv, f: f64) -> Mv {
    Mv { v: m.v.map(|v| v * f), src: m.src }
}
/// A limit: -1 is "max" (unlimited), shown as a dim dot.
fn lim(m: &Value, k: &str) -> Mv {
    let x = g(m, k);
    if x.v == Some(-1.0) { NOTAPP } else { x }
}
fn vcn(x: &Value) -> Cn {
    state(x, x["v"].as_f64()).cn()
}
fn ratio(num: Mv, den: Mv) -> Mv {
    match (num.v, den.v) {
        (Some(n), Some(d)) if d > 0.0 => Mv { v: Some(n / d), src: Src::Derived },
        (Some(_), Some(_)) => NOTAPP,
        _ => if num.v.is_none() { num } else { den },
    }
}

fn kind_of(key: &str) -> String {
    if key.is_empty() {
        return "root".into();
    }
    key.rsplit('/').next().and_then(|s| s.rsplit_once('.')).map_or("", |(_, ext)| ext).to_string()
}

fn cg_metrics(m: &Value) -> HashMap<String, Mv> {
    let mut o: HashMap<String, Mv> = HashMap::new();
    let mut put = |k: &str, v: Mv| {
        o.insert(k.to_string(), v);
    };
    put("cpu_pct", sc(r(m, "cpu.usage_usec"), 1e-4));
    put("cpu_throttled_pct", sc(r(m, "cpu.throttled_usec"), 1e-4));
    put("ram", g(m, "mem.current"));
    put("anon", g(m, "mem.anon"));
    put("file", g(m, "mem.file"));
    put("shmem", g(m, "mem.shmem"));
    put("z_pool", g(m, "mem.zswap"));
    put("ratio", ratio(g(m, "mem.zswapped"), g(m, "mem.zswap")));
    // refaults: anon refaults split into zswap hits (rf_z) and device reads (rf_d), file refaults (rf_f)
    let (wra, zin) = (r(m, "mem.workingset_refault_anon"), r(m, "mem.zswpin"));
    match (wra.v, zin.v) {
        (Some(a), Some(z)) => {
            put("rf_d_per_s", Mv { v: Some((a - z).max(0.0)), src: Src::Derived });
            put("rf_z_per_s", zin);
        }
        (_, None) if m.get("mem.zswpin").is_none() => {
            put("rf_d_per_s", wra);
            put("rf_z_per_s", NA);
        }
        _ => {
            put("rf_d_per_s", NA);
            put("rf_z_per_s", zin);
        }
    }
    put("rf_f_per_s", r(m, "mem.workingset_refault_file"));
    put("maj_per_s", r(m, "mem.pgmajfault"));
    put("pgscan_per_s", r(m, "mem.pgscan"));
    put("pgsteal_per_s", r(m, "mem.pgsteal"));
    put("mem_events_high_per_s", r(m, "memev.high"));
    put("mem_events_max_per_s", r(m, "memev.max"));
    put("mem_events_oom_kill_per_s", r(m, "memev.oom_kill"));
    for (n, l) in [("cpu", "cpu"), ("memory", "mem"), ("io", "io")] {
        for w in ["some", "full"] {
            put(&format!("psi_{l}_{w}_avg10"), g(m, &format!("psi.{n}.{w}.avg10")));
        }
    }
    put("io_r_bps", r(m, "io.rbytes"));
    put("io_w_bps", r(m, "io.wbytes"));
    put("io_r_iops", r(m, "io.rios"));
    put("io_w_iops", r(m, "io.wios"));
    put("pids_current", g(m, "pids.current"));
    put("mem_min", lim(m, "limit.memory.min"));
    put("mem_low", lim(m, "limit.memory.low"));
    put("mem_high", lim(m, "limit.memory.high"));
    put("mem_max", lim(m, "limit.memory.max"));
    put("swap_max", lim(m, "limit.memory.swap.max"));
    put("cpu_weight", g(m, "limit.cpu.weight"));
    put("io_weight", g(m, "limit.io.weight"));
    put("cpu_quota_us", lim(m, "limit.cpu.max.quota"));
    put("cpu_period_us", g(m, "limit.cpu.max.period"));
    put("io_max_capped", g(m, "limit.io.max.capped"));
    // headroom to the ceilings
    let ram = o.get("ram").copied().unwrap_or(NA);
    for (k, lk) in [("headroom_high_pct", "mem_high"), ("headroom_max_pct", "mem_max")] {
        let l = o.get(lk).copied().unwrap_or(NA);
        let v = match (l.cn(), ram.v) {
            (Cn::Val(h), Some(u)) if h > 0.0 => Mv { v: Some((h - u) / h * 100.0), src: Src::Derived },
            (Cn::Unset, _) => NOTAPP,
            _ => NA,
        };
        o.insert(k.to_string(), v);
    }
    o
}

/// Effective memory.min: the smallest value on the chain below the root (the root has no memory.min).
fn effective_min(ents: &mut [Ent], children: &[Vec<usize>], i: usize, root: usize, chain: &mut Vec<(String, Option<f64>)>) {
    let mut pushed = false;
    if i == root {
        ents[i].m.insert("effective_memory_min".into(), NOTAPP);
        ents[i].eff_reason = "the root cgroup has no memory.min".into();
    } else {
        chain.push((ents[i].key.clone(), ents[i].num("mem_min")));
        pushed = true;
        let own = ents[i].num("mem_min");
        let (mv, reason, by) = if chain.iter().any(|c| c.1.is_none()) {
            (NA, "an ancestor's memory.min is unavailable".to_string(), None)
        } else {
            let (mk, mv) = chain.iter().map(|c| (c.0.clone(), c.1.unwrap_or(0.0))).fold(None, |a: Option<(String, f64)>, c| match a {
                Some(x) if x.1 <= c.1 => Some(x),
                _ => Some(c),
            }).unwrap();
            let clamped = own.map_or(false, |o| mv < o);
            (
                Mv { v: Some(mv), src: Src::Derived },
                if clamped { "clamped by the smallest memory.min on the ancestor chain".to_string() } else { "memory.min as set".to_string() },
                if clamped { Some(mk) } else { None },
            )
        };
        ents[i].m.insert("effective_memory_min".into(), mv);
        ents[i].eff_reason = reason;
        ents[i].eff_clamped_by = by;
        ents[i].eff_path = chain.clone();
    }
    for &c in &children[i] {
        effective_min(ents, children, c, root, chain);
    }
    if pushed {
        chain.pop();
    }
}

fn is_partition(name: &str, all: &[&str]) -> bool {
    all.iter().any(|p| {
        name.len() > p.len() && name.starts_with(p) && {
            let rest = name[p.len()..].trim_start_matches('p');
            !rest.is_empty() && rest.chars().all(|c| c.is_ascii_digit())
        }
    })
}

/// Turn a `current` answer into a snapshot.
pub fn to_snap(v: &Value) -> Result<Snap, String> {
    let ev = v["entities"].as_object().ok_or("current has no entities")?;
    let null = Value::Null;
    let mut ents: Vec<Ent> = vec![];
    let mut parents: Vec<Option<String>> = vec![];
    for (name, m) in ev {
        let Some(path) = name.strip_prefix("cg:") else { continue };
        let key = path.trim_start_matches('/').to_string();
        let parent = if key.is_empty() { None } else { Some(key.rfind('/').map_or(String::new(), |p| key[..p].to_string())) };
        let mut e = Ent::bare(&key);
        e.kind = kind_of(&key);
        e.m = cg_metrics(m);
        for n in ["cpu_weight", "io_weight", "mem_min", "mem_low", "mem_high", "mem_max"] {
            e.limits.push(Limit { name: n.into(), live: e.m[n], origin: String::new(), rec_origin: None, rec_val: None, drift: false, reason: String::new() });
        }
        ents.push(e);
        parents.push(parent);
    }
    if !ents.iter().any(|e| e.key.is_empty()) {
        ents.insert(0, Ent::bare(""));
        parents.insert(0, None);
    }
    let keys: Vec<String> = ents.iter().map(|e| e.key.clone()).collect();
    let (children, root) = build_tree(&keys, &parents);
    effective_min(&mut ents, &children, root, root, &mut vec![]);
    let index: HashMap<&str, usize> = keys.iter().enumerate().map(|(i, k)| (k.as_str(), i)).collect();

    let hm = ev.get("host").unwrap_or(&null);
    let rootm = ev.get("cg:/").unwrap_or(&null);
    let allm = ev.get("cpu:all").unwrap_or(&null);
    let mut host: HashMap<String, Mv> = HashMap::new();
    let mut hput = |k: &str, m: Mv| {
        host.insert(k.to_string(), m);
    };
    for (k, s) in [("host_load1", "host.load1"), ("host_load5", "host.load5"), ("host_load15", "host.load15"), ("host_procs_running", "host.procs_running"), ("host_procs_blocked", "host.procs_blocked"),
        ("host_mem_total", "meminfo.MemTotal"), ("host_mem_available", "meminfo.MemAvailable"), ("host_swap_total", "meminfo.SwapTotal"), ("host_swap_free", "meminfo.SwapFree"),
        ("host_swapcached", "meminfo.SwapCached"), ("host_zswap_pool", "zswap.pool_bytes"), ("host_zswap_stored", "zswap.stored_bytes"), ("host_zswap_max_pool_percent", "zswap.max_pool_percent")] {
        hput(k, g(hm, s));
    }
    for (k, s) in [("host_anon", "memstack.anon"), ("host_file", "memstack.file_cache"), ("host_shmem", "memstack.shmem")] {
        hput(k, g(hm, s));
    }
    hput("host_zswap_ratio",ratio(g(hm, "zswap.stored_bytes"), g(hm, "zswap.pool_bytes")));
    hput("host_cpu_busy_pct", g(allm, "cpu.busy_pct"));
    hput("host_iocost_vrate", g(rootm, "io.cost.vrate"));
    for (n, l) in [("cpu", "cpu"), ("memory", "mem"), ("io", "io")] {
        for w in ["some", "full"] {
            hput(&format!("host_psi_{l}_{w}_avg10"), g(rootm, &format!("psi.{n}.{w}.avg10")));
        }
    }

    // zswap rates (pages per second): in, out, written back, rejected
    let rej_keys = ["reject_alloc_fail", "reject_compress_fail", "reject_compress_poor", "reject_kmemcache_fail", "reject_reclaim_fail"];
    let rej = {
        let all: Vec<Mv> = rej_keys.iter().map(|k| r(hm, &format!("zswap.{k}"))).collect();
        match all.iter().find(|m| m.v.is_none()) {
            Some(bad) => *bad,
            None => Mv { v: Some(all.iter().filter_map(|m| m.v).sum()), src: Src::Derived },
        }
    };
    let zrates = [r(hm, "vm.zswpin").cn(), r(hm, "vm.zswpout").cn(), r(hm, "zswap.written_back_pages").cn(), rej.cn()];

    // per-CPU
    let mut cpun: Vec<(u32, &Value)> = ev.iter().filter_map(|(k, m)| k.strip_prefix("cpu:").and_then(|n| n.parse::<u32>().ok()).map(|n| (n, m))).collect();
    cpun.sort_by_key(|c| c.0);
    let cpus: Vec<[Cn; 4]> = cpun.iter().map(|(_, m)| ["cpupct.us", "cpupct.sy", "cpupct.io", "cpupct.st"].map(|k| g(m, k).cn())).collect();

    // block devices: skip partitions of a listed disk, show the busiest three
    let devs: Vec<&str> = ev.keys().filter_map(|k| k.strip_prefix("dev:")).collect();
    let mut blocks: Vec<BlockDev> = devs
        .iter()
        .filter(|d| !is_partition(d, &devs))
        .map(|d| {
            let m = &ev[&format!("dev:{d}")];
            let o = |mv: Mv| mv.v;
            BlockDev {
                name: d.to_string(),
                rbps: o(sc(r(m, "disk.rd_sectors"), 512.0)),
                wbps: o(sc(r(m, "disk.wr_sectors"), 512.0)),
                riops: o(r(m, "disk.reads")),
                wiops: o(r(m, "disk.writes")),
                await_ms: g(m, "disk.await_ms").cn(),
                util_pct: g(m, "disk.util_pct").cn(),
            }
        })
        .collect();
    let act = |b: &BlockDev| b.rbps.unwrap_or(0.0) + b.wbps.unwrap_or(0.0);
    blocks.sort_by(|a, b| act(b).total_cmp(&act(a)).then_with(|| a.name.cmp(&b.name)));

    // processes, joined to their cgroup
    let mut procs: Vec<Vec<Proc>> = vec![vec![]; ents.len()];
    for p in v["procs"].as_array().map(|a| a.as_slice()).unwrap_or(&[]) {
        let key = p["cgroup"].as_str().and_then(|c| c.strip_prefix("cg:")).map_or("", |c| c.trim_start_matches('/'));
        let i = index.get(key).copied().unwrap_or(root);
        procs[i].push(Proc {
            pid: p["pid"].as_u64().unwrap_or(0) as u32,
            comm: p["comm"].as_str().unwrap_or("?").to_string(),
            state: p["state"].as_str().and_then(|s| s.chars().next()).unwrap_or('?'),
            cpu: vcn(&p["cpu_pct"]),
            rss: p["rss_bytes"].as_f64().map_or(Cn::NA, Cn::Val),
            io_r: vcn(&p["io_read_bps"]),
            io_w: vcn(&p["io_write_bps"]),
            maj: vcn(&p["majflt_per_s"]),
            cmdline: p["cmdline"].as_str().unwrap_or("").to_string(),
            demo: false,
        });
    }
    for list in procs.iter_mut() {
        list.sort_by(|a, b| ((b.state == 'D') as u8).cmp(&((a.state == 'D') as u8)).then(b.cpu_f().total_cmp(&a.cpu_f())).then(a.pid.cmp(&b.pid)));
    }
    let dstate: Vec<Option<u32>> = ents
        .iter()
        .map(|e| {
            let m = ev.get(&format!("cg:/{}", e.key)).unwrap_or(&null);
            match g(m, "procs.d_state").cn() {
                Cn::Val(n) => Some(n as u32),
                _ => None,
            }
        })
        .collect();
    let d_total = v["pids"]["d_state"].as_u64().map(|n| n as u32);
    Ok(Snap {
        ts: v["ts_ms"].as_f64().unwrap_or(0.0) / 1000.0,
        host,
        blocks,
        nets: vec![],
        ents,
        children,
        root,
        live: Some(LiveSnap { cpus, procs, dstate, d_total, zrates, tick: v["tick"].as_u64().unwrap_or(0), truncated: v["truncated"].as_bool().unwrap_or(false) }),
    })
}

// ------------------------------------------------------------ the thread

pub fn spawn(path: String, tick_override: Option<u64>) -> Receiver<Msg> {
    let (tx, rx) = channel();
    std::thread::spawn(move || run(path, tick_override, tx));
    rx
}

enum Exit {
    Gone,
    Fail(String),
}

fn run(path: String, tick_override: Option<u64>, tx: Sender<Msg>) {
    let mut pause = Duration::from_millis(500);
    loop {
        match session(&path, tick_override, &tx, &mut pause) {
            Exit::Gone => return,
            Exit::Fail(e) => {
                if tx.send(Msg::Down(e)).is_err() {
                    return;
                }
            }
        }
        std::thread::sleep(pause);
        pause = (pause * 2).min(Duration::from_secs(5));
    }
}

/// Returns the answer, its size in bytes and the time spent parsing it.
fn call(rd: &mut BufReader<UnixStream>, req: &Value) -> Result<(Value, usize, Duration), String> {
    let mut line = req.to_string();
    line.push('\n');
    rd.get_mut().write_all(line.as_bytes()).map_err(|e| format!("write: {e}"))?;
    let mut buf = String::new();
    let n = rd.read_line(&mut buf).map_err(|e| match e.kind() {
        std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut => "no answer from the sampler (timeout)".to_string(),
        _ => format!("read: {e}"),
    })?;
    if n == 0 {
        return Err("the sampler closed the connection".into());
    }
    let tp = Instant::now();
    let v: Value = serde_json::from_str(&buf).map_err(|e| format!("bad JSON from the sampler: {e}"))?;
    let parse = tp.elapsed();
    if v["ok"] != true {
        return Err(format!("sampler error {}: {}", v["error"]["code"].as_str().unwrap_or("?"), v["error"]["message"].as_str().unwrap_or("")));
    }
    if v["protocol"].as_u64() != Some(1) {
        return Err(format!("unsupported protocol {}", v["protocol"]));
    }
    Ok((v, n, parse))
}

fn session(path: &str, tick_override: Option<u64>, tx: &Sender<Msg>, pause: &mut Duration) -> Exit {
    let fail = |e: String| Exit::Fail(e);
    let s = match UnixStream::connect(path) {
        Ok(s) => s,
        Err(e) => return fail(format!("connect {path}: {e}")),
    };
    let _ = s.set_read_timeout(Some(Duration::from_secs(5)));
    let _ = s.set_write_timeout(Some(Duration::from_secs(2)));
    let mut rd = BufReader::new(s);
    let (h, _, _) = match call(&mut rd, &json!({"req": "hello", "id": 1})) {
        Ok(x) => x,
        Err(e) => return fail(e),
    };
    let hello = Hello {
        version: h["version"].as_str().unwrap_or("?").to_string(),
        pid: h["pid"].as_u64().unwrap_or(0),
        tick_ms: h["tick_ms"].as_u64().unwrap_or(1000),
        epoch: h["epoch"].as_str().unwrap_or("").to_string(),
        view_mode: h["cgroup_view"]["mode"].as_str().unwrap_or("unknown").to_string(),
    };
    let tick = tick_override.unwrap_or(hello.tick_ms).max(50);
    if tx.send(Msg::Hello(hello)).is_err() {
        return Exit::Gone;
    }
    let _ = rd.get_ref().set_read_timeout(Some(Duration::from_millis((tick * 3).max(5000))));
    let mut last_tick: Option<u64> = None;
    let mut next_status = Instant::now();
    loop {
        let t0 = Instant::now();
        let (v, bytes, parse) = match call(&mut rd, &json!({"req": "current", "procs": true, "id": 2})) {
            Ok(x) => x,
            Err(e) => return fail(e),
        };
        let tk = v["tick"].as_u64().unwrap_or(0);
        if Some(tk) != last_tick {
            last_tick = Some(tk);
            let tc = Instant::now();
            let snap = match to_snap(&v) {
                Ok(s) => s,
                Err(e) => return fail(e),
            };
            let parse_ms = (parse + tc.elapsed()).as_secs_f64() * 1e3;
            *pause = Duration::from_millis(500);
            let epoch = v["epoch"].as_str().unwrap_or("").to_string();
            if tx.send(Msg::Snap { snap: Box::new(snap), epoch: epoch.clone(), bytes, parse_ms }).is_err() {
                return Exit::Gone;
            }
            if Instant::now() >= next_status {
                next_status = Instant::now() + Duration::from_secs(5);
                if let Ok((s, _, _)) = call(&mut rd, &json!({"req": "status", "id": 3})) {
                    let val = |x: &Value| match x["s"].as_str() {
                        Some("value") => x["v"].as_f64(),
                        _ => None,
                    };
                    let st = Status {
                        cpu_pct: val(&s["self"]["cpu_pct_recent_60s"]),
                        rss: val(&s["self"]["rss_bytes"]),
                        uptime_s: s["uptime_s"].as_f64(),
                        overruns: s["timing"]["tick_overruns"].as_f64(),
                        skipped: s["timing"]["skipped_ticks"].as_f64(),
                    };
                    if tx.send(Msg::Status(st)).is_err() {
                        return Exit::Gone;
                    }
                }
            }
            let used = t0.elapsed();
            // the next request is one tick after this one started: same phase as the sampler, no duplicate answers
            std::thread::sleep(Duration::from_millis(tick).saturating_sub(used));
        } else {
            std::thread::sleep(Duration::from_millis((tick / 10).clamp(20, 200)));
        }
    }
}
