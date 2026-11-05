//! Unix socket, JSON lines. Two requests: `current` and `series`.
//!   {"req":"current"}
//!   {"req":"series","entity":"cg:/system.slice","metric":"mem.current","tier":"hot","window_s":60}
//! Metric "@events" on `series` returns hard events in the window (entity optional).
use crate::cfg::Config;
use crate::collect::{ProcOut, Val};
use crate::store;
use crate::util::*;
use std::collections::{HashMap, VecDeque};
use std::io::{BufRead, BufReader, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixListener;
use std::sync::{Arc, Mutex};

#[derive(Default)]
pub struct Shared {
    pub tick: u64,
    pub ts_ms: u64,
    pub dt_ms: u64,
    pub work_us: u64,
    pub frame: Vec<(u32, Vec<Val>)>,
    pub mets: Vec<(String, bool)>,
    pub ents: Vec<String>,
    pub limits: HashMap<u32, HashMap<u32, f64>>,
    pub procs: Vec<ProcOut>,
    pub pids_total: u32,
    pub pids_d: u32,
    pub io_denied: u64,
    pub events: VecDeque<String>,
    pub n_cgroups: usize,
    pub resets: u64,
}

pub fn spawn(cfg: Config, sh: Arc<Mutex<Shared>>) -> std::io::Result<()> {
    let path = cfg.sampler.socket_path.clone();
    let _ = std::fs::remove_file(&path);
    let l = UnixListener::bind(&path)?;
    let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
    std::thread::Builder::new().name("serve".into()).stack_size(256 * 1024).spawn(move || {
        for s in l.incoming().flatten() {
            let mut w = match s.try_clone() {
                Ok(w) => w,
                Err(_) => continue,
            };
            for line in BufReader::new(s).lines().map_while(Result::ok) {
                let reply = handle(&cfg, &sh, &line).unwrap_or_else(|e| {
                    let mut o = String::from("{\"error\":");
                    jstr(&mut o, &e);
                    o.push('}');
                    o
                });
                if w.write_all(reply.as_bytes()).is_err() || w.write_all(b"\n").is_err() {
                    break;
                }
            }
        }
    })?;
    Ok(())
}

fn handle(cfg: &Config, sh: &Arc<Mutex<Shared>>, line: &str) -> Result<String, String> {
    let v: serde_json::Value = serde_json::from_str(line).map_err(|e| e.to_string())?;
    match v["req"].as_str() {
        Some("current") => Ok(current(sh)),
        Some("series") => store::series(
            cfg,
            v["entity"].as_str().unwrap_or(""),
            v["metric"].as_str().ok_or("metric required")?,
            v["tier"].as_str().ok_or("tier required")?,
            v["window_s"].as_u64().ok_or("window_s required")?,
        ),
        _ => Err("req must be current or series".into()),
    }
}

fn current(sh: &Arc<Mutex<Shared>>) -> String {
    let s = sh.lock().unwrap();
    let dt = (s.dt_ms.max(1)) as f64 / 1000.0;
    let mut o = String::with_capacity(256 * 1024);
    o.push_str(&format!(
        "{{\"req\":\"current\",\"tick\":{},\"ts_ms\":{},\"dt_ms\":{},\"work_us\":{},\"cgroups\":{},\"counter_resets\":{},\"entities\":{{",
        s.tick, s.ts_ms, s.dt_ms, s.work_us, s.n_cgroups, s.resets
    ));
    for (i, (id, vals)) in s.frame.iter().enumerate() {
        if i > 0 {
            o.push(',');
        }
        jstr(&mut o, &s.ents[*id as usize]);
        o.push_str(":{");
        let mut first = true;
        let mut put = |o: &mut String, m: u32, v: f64, raw: u64| {
            if !first {
                o.push(',');
            }
            first = false;
            jstr(o, &s.mets[m as usize].0);
            o.push(':');
            if raw == NONE {
                jnum(o, v);
            } else {
                o.push_str("{\"d\":");
                jnum(o, v);
                o.push_str(",\"rate\":");
                jnum(o, v / dt);
                o.push_str(&format!(",\"raw\":{raw}}}"));
            }
        };
        for x in vals {
            put(&mut o, x.m, x.v, x.raw);
        }
        if let Some(l) = s.limits.get(id) {
            for (&m, &v) in l {
                if !vals.iter().any(|x| x.m == m) {
                    put(&mut o, m, v, NONE);
                }
            }
        }
        o.push('}');
    }
    o.push_str(&format!(
        "}},\"pids\":{{\"total\":{},\"d_state\":{},\"io_denied\":{}}},\"procs\":[",
        s.pids_total, s.pids_d, s.io_denied
    ));
    for (i, p) in s.procs.iter().enumerate() {
        if i > 0 {
            o.push(',');
        }
        o.push_str(&format!("{{\"pid\":{},\"start\":{},\"comm\":", p.pid, p.start));
        jstr(&mut o, &p.comm);
        o.push_str(&format!(",\"state\":\"{}\",\"cgroup\":", p.state));
        jstr(&mut o, &p.cgroup);
        o.push_str(&format!(",\"cpu_pct\":{:.2},\"io_bps\":{:.0},\"rss_bytes\":{},\"threads\":{},\"why\":\"{}\"}}", p.cpu_pct, p.io_bps, p.rss_bytes, p.threads, p.why));
    }
    o.push_str("],\"events\":[");
    for (i, e) in s.events.iter().enumerate() {
        if i > 0 {
            o.push(',');
        }
        o.push_str(e);
    }
    o.push_str("]}");
    o
}
