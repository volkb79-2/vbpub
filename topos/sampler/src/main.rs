mod cfg;
mod collect;
mod dict;
mod procs;
mod selfmon;
mod serve;
mod store;
#[cfg(test)]
mod tests;
mod util;

use std::sync::Arc;
use std::time::{Duration, Instant};

fn vm_status() -> (u64, u64, u64) {
    // VmHWM kB, VmRSS kB, write_bytes (block level) of this process, for the exit summary
    let status = std::fs::read_to_string("/proc/self/status").unwrap_or_default();
    let kv = |k: &str| status.lines().find(|l| l.starts_with(k)).and_then(|l| l.split_whitespace().nth(1)).and_then(|x| x.parse().ok()).unwrap_or(0u64);
    let io = std::fs::read_to_string("/proc/self/io").unwrap_or_default();
    let wb = io.lines().find(|l| l.starts_with("write_bytes:")).and_then(|l| l.split_whitespace().nth(1)).and_then(|x| x.parse().ok()).unwrap_or(0u64);
    (kv("VmHWM:"), kv("VmRSS:"), wb)
}

fn pct(v: &mut Vec<u64>, p: f64) -> u64 {
    if v.is_empty() {
        return 0;
    }
    v.sort_unstable();
    v[((v.len() as f64 * p).ceil() as usize).saturating_sub(1).min(v.len() - 1)]
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let once = args.iter().any(|a| a == "--once");
    let path = match args.iter().find(|a| !a.starts_with("--")) {
        Some(p) => p.clone(),
        None => {
            eprintln!("usage: topos-sampler [--once] <config.toml>");
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
    let use_store = cfg.store.enabled && !once;
    let dict = if use_store {
        let _ = std::fs::create_dir_all(&cfg.store.cold_dir);
        dict::Dict::load(&cfg.store.cold_dir, util::wall_ms() / 1000)
    } else {
        dict::Dict::default()
    };
    let (lm, le) = (dict.mets.len(), dict.ents.len());
    let start = Instant::now();
    let mut col = collect::Collector::new(cfg.clone(), dict, start);
    let mut store = if once {
        None
    } else {
        match store::Store::open(&cfg, lm, le) {
            Ok(s) => Some(s),
            Err(e) => {
                eprintln!("store error: {e}");
                std::process::exit(2);
            }
        }
    };
    let ctx = Arc::new(serve::Ctx::new(cfg.clone(), selfmon::cgroup_view(&cfg)));
    if !once {
        if let Err(e) = serve::spawn(ctx.clone()) {
            eprintln!("socket error: {e}");
            std::process::exit(2);
        }
    }
    let mut mon = selfmon::SelfMon::new();
    let mut sb = serve::SnapBuilder::default();
    let tick_d = Duration::from_millis(cfg.sampler.tick_ms);
    let mut next = Instant::now();
    let mut last: Option<Instant> = None;
    let mut tick = 0u64;
    let (mut works, mut tcg, mut thost, mut tproc, mut tstore) = (Vec::new(), 0u128, 0u128, 0u128, 0u128);
    let (mut maxcg, mut overruns, mut skipped, mut last_work_us) = (0usize, 0u64, 0u64, 0u64);
    loop {
        tick += 1;
        let t0 = Instant::now();
        let dt_ms = last.map(|l| t0.duration_since(l).as_millis() as u64).unwrap_or(cfg.sampler.tick_ms);
        last = Some(t0);
        let wall = util::wall_ms();
        let mono = start.elapsed().as_millis() as u64;
        let ss = mon.sample(&cfg, mono, col.clk_tck, col.page_size);
        col.selfin = collect::SelfIn { work_us: last_work_us, rss_bytes: ss.rss, cpu_ticks: ss.cpu_ticks, overruns, skipped };
        col.sample(tick, dt_ms, mono, wall / 1000);
        let t1 = Instant::now();
        if let Some(st) = store.as_mut() {
            st.push(&col.frame, wall, dt_ms, &mut col.names.dict);
            st.sync_dict(&mut col.names.dict);
            st.append_events(&col.events);
        }
        let ts = t1.elapsed().as_micros();
        let work_us = t0.elapsed().as_micros() as u64;
        if work_us > cfg.sampler.tick_ms * 1000 {
            overruns += 1;
        }
        last_work_us = work_us;
        let (tiers, tmpfs_b, cold_b) = match store.as_ref() {
            Some(s) => {
                let (a, b) = s.sizes();
                (s.tier_info(), a, b)
            }
            None => (Vec::new(), 0, 0),
        };
        let status = serve::Status {
            uptime_s: start.elapsed().as_secs(),
            ticks: tick,
            cpu_recent: Some(ss.cpu_recent),
            cpu_avg: Some(ss.cpu_avg),
            rss: ss.rss,
            last_tick_us: work_us,
            overruns,
            skipped,
            stage_us: [col.t_proc, col.t_cg, col.t_host, ts],
            throttle: Some(ss.throttle),
            tiers,
            cold_bytes: cold_b,
            cold_cap: cfg.store.cold_cap_bytes,
            tmpfs_bytes: tmpfs_b,
            ids_freed: col.gc_freed,
        };
        let maxn = col.n_cgroups;
        ctx.publish(sb.build(&mut col, tick, wall, dt_ms, work_us, status));
        works.push(work_us);
        tcg += col.t_cg;
        thost += col.t_host;
        tproc += col.t_proc;
        tstore += ts;
        maxcg = maxcg.max(maxn);
        if once && tick >= 2 {
            println!("{}", serve::handle(&ctx, "{\"req\":\"current\"}"));
            return;
        }
        if cfg.sampler.duration_s > 0 && start.elapsed().as_secs() >= cfg.sampler.duration_s {
            break;
        }
        next += tick_d;
        let now = Instant::now();
        if next > now {
            std::thread::sleep(next - now);
        } else {
            skipped += (now - next).as_millis() as u64 / cfg.sampler.tick_ms.max(1);
            next = now;
        }
    }
    if cfg.sampler.stats_path.is_empty() {
        return;
    }
    let wall_s = start.elapsed().as_secs_f64();
    let (hwm, rss, wbytes) = vm_status();
    let ck = col.clk_tck as f64;
    let cpu_ticks = mon.sample(&cfg, 0, col.clk_tck, col.page_size).cpu_ticks.unwrap_or(0) as f64;
    let (tm_files, cold_files) = store.as_ref().map(|s| s.sizes()).unwrap_or((0, 0));
    let n = works.len().max(1) as f64;
    let mut w2 = works.clone();
    let (p50, p95) = (pct(&mut w2, 0.5), pct(&mut w2, 0.95));
    let mx = *works.iter().max().unwrap_or(&0);
    let out = format!(
        concat!(
            "{{\"impl\":\"rust\",\"ticks\":{},\"wall_s\":{:.2},\"cpu_pct_of_core\":{:.3},\"cpu_ms_per_tick\":{:.3},",
            "\"work_ms_avg\":{:.3},\"work_ms_p50\":{:.3},\"work_ms_p95\":{:.3},\"work_ms_max\":{:.3},",
            "\"ms_cgroups\":{:.3},\"ms_host\":{:.3},\"ms_procs\":{:.3},\"ms_store\":{:.3},",
            "\"vm_hwm_kb\":{},\"vm_rss_kb\":{},\"write_bytes_disk\":{},\"tier_tmpfs_bytes\":{},\"tier_cold_bytes\":{},",
            "\"cgroups\":{},\"pids\":{},\"io_read_pids_last_tick\":{},\"overruns\":{},\"skipped_ticks\":{},\"counter_resets\":{}}}"
        ),
        tick, wall_s,
        cpu_ticks / ck / wall_s * 100.0,
        cpu_ticks * 1000.0 / ck / tick as f64,
        works.iter().sum::<u64>() as f64 / n / 1000.0, p50 as f64 / 1000.0, p95 as f64 / 1000.0, mx as f64 / 1000.0,
        tcg as f64 / n / 1000.0, thost as f64 / n / 1000.0, tproc as f64 / n / 1000.0, tstore as f64 / n / 1000.0,
        hwm, rss, wbytes, tm_files, cold_files,
        maxcg, col.procs.total, col.procs.io_read, overruns, skipped, col.resets
    );
    let _ = std::fs::write(&cfg.sampler.stats_path, out);
}
