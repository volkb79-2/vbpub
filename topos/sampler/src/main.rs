mod cfg;
mod collect;
mod dict;
mod serve;
mod store;
mod util;

use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

fn self_stats() -> (u64, u64, u64, u64, u64, u64, u64) {
    // utime, stime (ticks), VmHWM kB, VmRSS kB, wchar, write_bytes, syscw
    let st = std::fs::read_to_string("/proc/self/stat").unwrap_or_default();
    let rest = st.rsplit_once(')').map(|x| x.1).unwrap_or("");
    let f: Vec<&str> = rest.split_whitespace().collect();
    let g = |i: usize| f.get(i).and_then(|x| x.parse::<u64>().ok()).unwrap_or(0);
    let (ut, stt) = (g(11), g(12));
    let status = std::fs::read_to_string("/proc/self/status").unwrap_or_default();
    let kv = |k: &str| status.lines().find(|l| l.starts_with(k)).and_then(|l| l.split_whitespace().nth(1)).and_then(|x| x.parse().ok()).unwrap_or(0u64);
    let io = std::fs::read_to_string("/proc/self/io").unwrap_or_default();
    let iv = |k: &str| io.lines().find(|l| l.starts_with(k)).and_then(|l| l.split_whitespace().nth(1)).and_then(|x| x.parse().ok()).unwrap_or(0u64);
    (ut, stt, kv("VmHWM:"), kv("VmRSS:"), iv("wchar:"), iv("write_bytes:"), iv("syscw:"))
}

fn dir_bytes(d: &str) -> u64 {
    std::fs::read_dir(d)
        .map(|rd| rd.flatten().filter_map(|e| e.metadata().ok()).filter(|m| m.is_file()).map(|m| m.len()).sum())
        .unwrap_or(0)
}

fn pct(v: &mut Vec<u64>, p: f64) -> u64 {
    if v.is_empty() {
        return 0;
    }
    v.sort_unstable();
    v[((v.len() as f64 * p).ceil() as usize).saturating_sub(1).min(v.len() - 1)]
}

fn main() {
    let path = match std::env::args().nth(1) {
        Some(p) => p,
        None => {
            eprintln!("usage: topos-sampler-poc <config.toml>");
            std::process::exit(2);
        }
    };
    let cfg = match cfg::Config::load(&path) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("config error: {e}");
            std::process::exit(2);
        }
    };
    if let Some(p) = std::path::Path::new(&cfg.sampler.socket_path).parent() {
        let _ = std::fs::create_dir_all(p);
    }
    let dict = if cfg.store.enabled {
        let _ = std::fs::create_dir_all(&cfg.store.cold_dir);
        dict::Dict::load(&cfg.store.cold_dir)
    } else {
        dict::Dict::default()
    };
    let (lm, le) = (dict.mets.len(), dict.ents.len());
    let start = Instant::now();
    let mut col = collect::Collector::new(cfg.clone(), dict, start);
    let mut store = match store::Store::open(&cfg, lm, le) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("store error: {e}");
            std::process::exit(2);
        }
    };
    let shared = Arc::new(Mutex::new(serve::Shared::default()));
    if let Err(e) = serve::spawn(cfg.clone(), shared.clone()) {
        eprintln!("socket error: {e}");
        std::process::exit(2);
    }
    let tick_d = Duration::from_millis(cfg.sampler.tick_ms);
    let mut next = Instant::now();
    let mut last: Option<Instant> = None;
    let mut tick = 0u64;
    let (mut works, mut tcg, mut thost, mut tproc, mut tstore) = (Vec::new(), 0u128, 0u128, 0u128, 0u128);
    let mut maxcg = 0usize;
    loop {
        tick += 1;
        let t0 = Instant::now();
        let dt_ms = last.map(|l| t0.duration_since(l).as_millis() as u64).unwrap_or(cfg.sampler.tick_ms);
        last = Some(t0);
        let wall = util::wall_ms();
        col.sample(tick, dt_ms);
        let t1 = Instant::now();
        store.push(&col.frame, wall, dt_ms, &mut col.names.dict);
        store.sync_dict(&col.names.dict);
        store.append_events(&col.events);
        let ts = t1.elapsed().as_micros();
        {
            let mut sh = shared.lock().unwrap();
            std::mem::swap(&mut sh.frame, &mut col.frame);
            std::mem::swap(&mut sh.procs, &mut col.procs.out);
            while sh.mets.len() < col.names.dict.mets.len() {
                let m = col.names.dict.mets[sh.mets.len()].clone();
                sh.mets.push(m);
            }
            while sh.ents.len() < col.names.dict.ents.len() {
                let e = col.names.dict.ents[sh.ents.len()].clone();
                sh.ents.push(e);
            }
            for &(e, m, v) in &col.limit_updates {
                sh.limits.entry(e).or_default().insert(m, v);
            }
            for e in &col.gone_ents {
                sh.limits.remove(e);
            }
            for e in &col.events {
                sh.events.push_back(e.json.clone());
                if sh.events.len() > 200 {
                    sh.events.pop_front();
                }
            }
            sh.tick = tick;
            sh.ts_ms = wall;
            sh.dt_ms = dt_ms;
            sh.work_us = t0.elapsed().as_micros() as u64;
            sh.n_cgroups = col.n_cgroups;
            sh.resets = col.resets;
            sh.pids_total = col.procs.total;
            sh.pids_d = col.procs.dstate;
            sh.io_denied = col.procs.denied;
        }
        let w = t0.elapsed().as_micros() as u64;
        works.push(w);
        tcg += col.t_cg;
        thost += col.t_host;
        tproc += col.t_proc;
        tstore += ts;
        maxcg = maxcg.max(col.n_cgroups);
        if cfg.sampler.duration_s > 0 && start.elapsed().as_secs() >= cfg.sampler.duration_s {
            break;
        }
        next += tick_d;
        let now = Instant::now();
        if next > now {
            std::thread::sleep(next - now);
        } else {
            next = now;
        }
    }
    let wall_s = start.elapsed().as_secs_f64();
    let (ut, st, hwm, rss, wchar, wbytes, syscw) = self_stats();
    let (tm_files, cold_files) = store.sizes();
    let n = works.len().max(1) as f64;
    let mut w2 = works.clone();
    let (p50, p95) = (pct(&mut w2, 0.5), pct(&mut w2, 0.95));
    let mx = *works.iter().max().unwrap_or(&0);
    let out = format!(
        concat!(
            "{{\"impl\":\"rust\",\"ticks\":{},\"wall_s\":{:.2},\"cpu_ticks_user\":{},\"cpu_ticks_sys\":{},",
            "\"cpu_pct_of_core\":{:.3},\"cpu_ms_per_tick\":{:.3},\"work_ms_avg\":{:.3},\"work_ms_p50\":{:.3},",
            "\"work_ms_p95\":{:.3},\"work_ms_max\":{:.3},\"ms_cgroups\":{:.3},\"ms_host\":{:.3},\"ms_procs\":{:.3},",
            "\"ms_store\":{:.3},\"vm_hwm_kb\":{},\"vm_rss_kb\":{},\"wchar\":{},\"write_bytes_disk\":{},\"syscw\":{},",
            "\"bytes_written_tmpfs\":{},\"bytes_written_cold\":{},\"bytes_written_other_disk\":{},",
            "\"tmpfs_dir_bytes\":{},\"cold_dir_bytes\":{},\"tier_tmpfs_bytes\":{},\"tier_cold_bytes\":{},",
            "\"cgroups\":{},\"pids\":{},\"counter_resets\":{}}}"
        ),
        tick, wall_s, ut, st,
        (ut + st) as f64 / 100.0 / wall_s * 100.0,
        (ut + st) as f64 * 10.0 / tick as f64,
        works.iter().sum::<u64>() as f64 / n / 1000.0, p50 as f64 / 1000.0, p95 as f64 / 1000.0, mx as f64 / 1000.0,
        tcg as f64 / n / 1000.0, thost as f64 / n / 1000.0, tproc as f64 / n / 1000.0, tstore as f64 / n / 1000.0,
        hwm, rss, wchar, wbytes, syscw,
        store.w_tmpfs, store.w_cold, store.w_other,
        dir_bytes(&cfg.store.tmpfs_dir), dir_bytes(&cfg.store.cold_dir), tm_files, cold_files,
        maxcg, col.procs.total, col.resets
    );
    let _ = std::fs::write(&cfg.sampler.stats_path, out);
}
