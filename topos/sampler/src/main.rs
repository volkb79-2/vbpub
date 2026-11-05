mod cfg;
mod cmdline;
mod collect;
mod dict;
mod procs;
mod selfmon;
mod serve;
mod store;
#[cfg(test)]
mod tests;
mod util;
mod writer;

use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{Duration, Instant};

/// Jobs the writer may be behind before the oldest frame is dropped (and counted).
const WRITER_QUEUE: usize = 30;

fn vm_status() -> (u64, u64, u64) {
    // VmHWM kB, VmRSS kB, write_bytes (block level) of this process, for the exit summary
    let status = std::fs::read_to_string("/proc/self/status").unwrap_or_default();
    let kv = |k: &str| status.lines().find(|l| l.starts_with(k)).and_then(|l| l.split_whitespace().nth(1)).and_then(|x| x.parse().ok()).unwrap_or(0u64);
    let io = std::fs::read_to_string("/proc/self/io").unwrap_or_default();
    let wb = io.lines().find(|l| l.starts_with("write_bytes:")).and_then(|l| l.split_whitespace().nth(1)).and_then(|x| x.parse().ok()).unwrap_or(0u64);
    (kv("VmHWM:"), kv("VmRSS:"), wb)
}

/// Per-tick work times feed only the exit summary: a 24/7 daemon without `stats_path`
/// must not grow a vector forever (S-08).
fn record_work(works: &mut Vec<u64>, want_stats: bool, work_us: u64) {
    if want_stats {
        works.push(work_us);
    }
}

fn pct(v: &mut Vec<u64>, p: f64) -> u64 {
    if v.is_empty() {
        return 0;
    }
    v.sort_unstable();
    v[((v.len() as f64 * p).ceil() as usize).saturating_sub(1).min(v.len() - 1)]
}

/// Writer-side numbers for `status.store`.
fn store_status(sh: Option<&writer::Shared>) -> (serve::StoreStatus, writer::WInfo) {
    use store::CTR;
    let mut s = serve::StoreStatus {
        write_errors: CTR.write_errors.load(Ordering::Relaxed),
        torn_frames_seen: CTR.torn_open.load(Ordering::Relaxed),
        corrupt_frame_reads: CTR.corrupt_reads.load(Ordering::Relaxed),
        frames_skipped: CTR.frames_skipped.load(Ordering::Relaxed),
        last_error: store::last_error(),
        ..Default::default()
    };
    let mut info = writer::WInfo::default();
    if let Some(sh) = sh {
        info = sh.info();
        s.enabled = true;
        s.queue_len = sh.queue_len();
        s.dropped_frames = sh.dropped_frames.load(Ordering::SeqCst);
        s.lag_ms = sh.lag_ms();
        s.last_job_us = info.last_job_us;
        s.cold_other_bytes = info.cold_other_bytes;
    }
    (s, info)
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.iter().any(|a| a == "--version") {
        println!("topos-sampler {}", env!("CARGO_PKG_VERSION"));
        return;
    }
    let once = args.iter().any(|a| a == "--once");
    let path = match args.iter().find(|a| !a.starts_with("--")) {
        Some(p) => p.clone(),
        None => {
            eprintln!("usage: topos-sampler [--once|--version] <config.toml>");
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
    util::install_stop_handlers();
    let use_store = cfg.store.enabled && !once;
    let dict = if use_store {
        let _ = std::fs::create_dir_all(&cfg.store.cold_dir);
        dict::Dict::load(&cfg.store.cold_dir, util::wall_ms() / 1000)
    } else {
        dict::Dict::default()
    };
    let (lm, le) = (dict.mets.len(), dict.ents.len());
    let start = Instant::now();
    // The writer thread owns the store and a mirror of the dictionary; sampling never does disk IO.
    let mut writer_thread = None;
    let mut shared: Option<Arc<writer::Shared>> = None;
    if !once {
        let store = match store::Store::open(&cfg, lm, le) {
            Ok(s) => s,
            Err(e) => {
                eprintln!("store error: {e}");
                std::process::exit(2);
            }
        };
        if cfg.store.enabled {
            let mirror = dict::Dict::load(&cfg.store.cold_dir, 0);
            let sh = writer::Shared::new(WRITER_QUEUE);
            match writer::spawn(sh.clone(), store, mirror) {
                Ok(h) => writer_thread = Some(h),
                Err(e) => {
                    eprintln!("writer thread: {e}");
                    std::process::exit(2);
                }
            }
            shared = Some(sh);
        }
    }
    let mut col = collect::Collector::new(cfg.clone(), dict, start);
    col.procs.sync_cmdline = once; // --once is two ticks: no time for the helper pool
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
    let want_stats = !cfg.sampler.stats_path.is_empty();
    let (mut works, mut tcg, mut thost, mut tproc, mut tsub) = (Vec::new(), 0u128, 0u128, 0u128, 0u128);
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
        let work_us = t0.elapsed().as_micros() as u64;
        if work_us > cfg.sampler.tick_ms * 1000 {
            overruns += 1;
        }
        last_work_us = work_us;
        let (sstat, winfo) = store_status(shared.as_deref());
        let status = serve::Status {
            uptime_s: start.elapsed().as_secs(),
            ticks: tick,
            cpu_recent: Some(ss.cpu_recent),
            cpu_avg: Some(ss.cpu_avg),
            rss: ss.rss,
            last_tick_us: work_us,
            overruns,
            skipped,
            stage_us: [col.t_proc, col.t_cg, col.t_host, winfo.last_job_us as u128],
            throttle: Some(ss.throttle),
            throttle_src: ss.throttle_src,
            tiers: winfo.tiers.clone(),
            cold_bytes: winfo.cold_bytes,
            cold_cap: cfg.store.cold_cap_bytes,
            tmpfs_bytes: winfo.tmpfs_bytes,
            ids_freed: col.gc_freed,
            store: sstat,
            cmd_pending: col.procs.cmd_pending,
            cmd_timeouts: col.procs.cmd_timeouts,
        };
        let maxn = col.n_cgroups;
        // Hand the tick to the writer (never blocks), then publish it to the clients.
        let delta = col.names.dict.take_delta();
        let events: Vec<String> = col.events.drain(..).map(|e| e.json).collect();
        let snap = sb.build(&mut col, tick, wall, dt_ms, work_us, status);
        if let Some(sh) = &shared {
            sh.submit(writer::Job { ts_ms: wall, dt_ms, frame: snap.frame.clone(), events, delta });
        }
        ctx.publish(snap);
        let tsubmit = t1.elapsed().as_micros();
        record_work(&mut works, want_stats, work_us);
        tcg += col.t_cg;
        thost += col.t_host;
        tproc += col.t_proc;
        tsub += tsubmit;
        maxcg = maxcg.max(maxn);
        if once && tick >= 2 {
            println!("{}", serve::handle(&ctx, "{\"req\":\"current\"}"));
            return;
        }
        if util::STOP.load(Ordering::SeqCst) || (cfg.sampler.duration_s > 0 && start.elapsed().as_secs() >= cfg.sampler.duration_s) {
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
        if util::STOP.load(Ordering::SeqCst) {
            break;
        }
    }
    // Drain the writer, so the last ticks are on disk before the process exits.
    if let Some(sh) = &shared {
        sh.close();
    }
    if let Some(h) = writer_thread {
        let _ = h.join();
    }
    if !want_stats {
        return;
    }
    let wall_s = start.elapsed().as_secs_f64();
    let (hwm, rss, wbytes) = vm_status();
    let ck = col.clk_tck as f64;
    let cpu_ticks = mon.sample(&cfg, 0, col.clk_tck, col.page_size).cpu_ticks.unwrap_or(0) as f64;
    let (tm_files, cold_files) = shared.as_ref().map(|s| { let i = s.info(); (i.tmpfs_bytes, i.cold_bytes) }).unwrap_or((0, 0));
    let n = works.len().max(1) as f64;
    let mut w2 = works.clone();
    let (p50, p95) = (pct(&mut w2, 0.5), pct(&mut w2, 0.95));
    let mx = *works.iter().max().unwrap_or(&0);
    let dropped = shared.as_ref().map(|s| s.dropped_frames.load(Ordering::SeqCst)).unwrap_or(0);
    let out = format!(
        concat!(
            "{{\"impl\":\"rust\",\"ticks\":{},\"wall_s\":{:.2},\"cpu_pct_of_core\":{:.3},\"cpu_ms_per_tick\":{:.3},",
            "\"work_ms_avg\":{:.3},\"work_ms_p50\":{:.3},\"work_ms_p95\":{:.3},\"work_ms_max\":{:.3},",
            "\"ms_cgroups\":{:.3},\"ms_host\":{:.3},\"ms_procs\":{:.3},\"ms_store\":{:.3},",
            "\"vm_hwm_kb\":{},\"vm_rss_kb\":{},\"write_bytes_disk\":{},\"tier_tmpfs_bytes\":{},\"tier_cold_bytes\":{},",
            "\"cgroups\":{},\"pids\":{},\"io_read_pids_last_tick\":{},\"overruns\":{},\"skipped_ticks\":{},\"counter_resets\":{},",
            "\"dropped_frames\":{},\"write_errors\":{}}}"
        ),
        tick, wall_s,
        cpu_ticks / ck / wall_s * 100.0,
        cpu_ticks * 1000.0 / ck / tick as f64,
        works.iter().sum::<u64>() as f64 / n / 1000.0, p50 as f64 / 1000.0, p95 as f64 / 1000.0, mx as f64 / 1000.0,
        tcg as f64 / n / 1000.0, thost as f64 / n / 1000.0, tproc as f64 / n / 1000.0, tsub as f64 / n / 1000.0,
        hwm, rss, wbytes, tm_files, cold_files,
        maxcg, col.procs.total, col.procs.io_read, overruns, skipped, col.resets,
        dropped, store::CTR.write_errors.load(Ordering::Relaxed)
    );
    let _ = std::fs::write(&cfg.sampler.stats_path, out);
}
