//! Unit and protocol tests. No test depends on wall-clock speed or host load: ticks are
//! driven by hand with explicit time arguments; sockets use timeouts only to fail, never
//! to measure.
use crate::cfg::Config;
use crate::collect::{mk_event, Collector, Val};
use crate::dict::Dict;
use crate::serve::{handle, Ctx, SnapBuilder, Status};
use crate::store::Store;
use crate::util::*;
use serde_json::Value;
use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

static SEQ: AtomicUsize = AtomicUsize::new(0);

fn tmp(name: &str) -> PathBuf {
    let p = std::env::temp_dir().join(format!("tsp-{}-{}-{}", std::process::id(), SEQ.fetch_add(1, Ordering::SeqCst), name));
    let _ = fs::remove_dir_all(&p);
    fs::create_dir_all(&p).unwrap();
    p
}

fn put(root: &Path, rel: &str, content: &str) {
    let p = root.join(rel);
    fs::create_dir_all(p.parent().unwrap()).unwrap();
    fs::write(p, content).unwrap();
}

fn copy_dir(from: &Path, to: &Path) {
    fs::create_dir_all(to).unwrap();
    for e in fs::read_dir(from).unwrap().flatten() {
        let t = to.join(e.file_name());
        if e.file_type().unwrap().is_dir() {
            copy_dir(&e.path(), &t);
        } else {
            fs::copy(e.path(), t).unwrap();
        }
    }
}

fn fixture_cg() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../tests/fixtures/cgroupfs/gstammtisch")
}

fn cfg_text(root: &Path, resp_cap: usize, tiers: &str, cap: u64, sweep_s: u64) -> String {
    let r = root.display();
    format!(
        r#"
[sampler]
tick_ms = 1000
slow_every_s = 1
cgroup_root = "{r}/cg"
proc_root = "{r}/proc"
sys_root = "{r}/sys"
socket_path = "{r}/s.sock"
duration_s = 0
stats_path = ""
[process]
scan = true
top_cpu = 20
top_io = 20
cap = 64
full_sweep_every_s = {sweep_s}
cmdline_max = 256
[events]
psi_cpu_some_avg10 = 25.0
psi_memory_some_avg10 = 10.0
psi_io_some_avg10 = 25.0
events_path = "{r}/ev.jsonl"
max_bytes = 1000000
[protocol]
max_request_bytes = 512
max_response_bytes = {resp_cap}
max_clients = 4
client_idle_ms = 5000
client_write_ms = 200
[store]
enabled = true
tmpfs_dir = "{r}/tm"
cold_dir = "{r}/cold"
cold_cap_bytes = {cap}
{tiers}
"#
    )
}

const TIERS3: &str = r#"
[[tier]]
name = "hot"
step_s = 1
keep_s = 60
segment_s = 10
medium = "tmpfs"
[[tier]]
name = "warm"
step_s = 2
keep_s = 300
segment_s = 20
medium = "tmpfs"
[[tier]]
name = "cold"
step_s = 4
keep_s = 86400
segment_s = 3600
medium = "disk"
"#;

fn cfg_for(root: &Path) -> Config {
    Config::parse(&cfg_text(root, 4_000_000, TIERS3, 1 << 30, 3)).unwrap()
}

fn pstat(pid: u32, comm: &str, state: &str, majflt: u64, ut: u64, st: u64, thr: u64, start: u64, rss: u64, blk: u64) -> String {
    let mut f: Vec<String> = vec!["0".to_string(); 46];
    f[0] = state.to_string();
    f[9] = majflt.to_string();
    f[11] = ut.to_string();
    f[12] = st.to_string();
    f[17] = thr.to_string();
    f[19] = start.to_string();
    f[21] = rss.to_string();
    f[39] = blk.to_string();
    format!("{pid} ({comm}) {}\n", f.join(" "))
}

fn mk_proc(root: &Path) {
    put(root, "proc/stat", "cpu  100 0 50 800 10 5 5 0 0 0\ncpu0 50 0 25 400 5 2 3 0 0 0\nctxt 1000\nprocesses 500\nprocs_running 2\nprocs_blocked 1\n");
    put(root, "proc/meminfo", "MemTotal:       1000000 kB\nMemFree:         100000 kB\nMemAvailable:    500000 kB\nBuffers:          10000 kB\nCached:          300000 kB\nShmem:            50000 kB\nAnonPages:       400000 kB\nSReclaimable:     20000 kB\nSUnreclaim:       10000 kB\nKernelStack:       5000 kB\nPageTables:        8000 kB\nPercpu:            2000 kB\nZswap:             1000 kB\nZswapped:          3000 kB\n");
    put(root, "proc/vmstat", "pgmajfault 10\npgfault 100\noom_kill 0\nunrelated 5\n");
    put(root, "proc/loadavg", "0.50 0.40 0.30 1/100 999\n");
    put(root, "proc/diskstats", "   8       0 vda 100 0 2000 50 200 0 4000 80 0 60 130 0 0 0 0\n");
    put(root, "proc/pressure/cpu", "some avg10=1.50 avg60=1.00 avg300=0.50 total=1000\n");
    put(root, "proc/pressure/memory", "some avg10=0.50 avg60=1.00 avg300=0.50 total=10\nfull avg10=0.25 avg60=1.00 avg300=0.50 total=5\n");
    put(root, "proc/pressure/io", "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n");
    put(root, "proc/sys/kernel/task_delayacct", "0\n");
    put(root, "proc/self/stat", &pstat(1, "topos-sampler", "S", 0, 7, 3, 2, 5, 100, 0));
    put(root, "proc/self/statm", "1000 250 10 0 0 0 0\n");
    put(root, "proc/self/cgroup", "0::/system.slice/topos.service\n");
}

fn world(name: &str) -> (PathBuf, Collector) {
    let root = tmp(name);
    copy_dir(&fixture_cg(), &root.join("cg"));
    mk_proc(&root);
    let cfg = cfg_for(&root);
    let col = Collector::new(cfg, Dict::default(), Instant::now());
    (root, col)
}

fn id_of(col: &Collector, ent: &str, met: &str) -> Option<(u32, u32)> {
    let e = col.names.dict.ents.iter().position(|x| x == ent)? as u32;
    let m = col.names.dict.mets.iter().position(|x| x.0 == met)? as u32;
    Some((e, m))
}

fn get(col: &Collector, ent: &str, met: &str) -> Option<Val> {
    let (e, m) = id_of(col, ent, met)?;
    col.frame.iter().find(|(i, _)| *i == e)?.1.iter().find(|v| v.m == m).copied()
}

fn sticky(col: &Collector, ent: &str, met: &str) -> Option<Val> {
    let (e, m) = id_of(col, ent, met)?;
    col.sticky.get(&e)?.get(&m).copied()
}

fn tick(col: &mut Collector, n: u64) {
    col.sample(n, 1000, n * 1000, 1_000_000 + n);
}

// ---------------------------------------------------------------- rates and resets

#[test]
fn first_sample_zero_delta_and_reset_are_distinguishable() {
    let (root, mut col) = world("rates");
    tick(&mut col, 1);
    let v = get(&col, "cg:/", "cpu.usage_usec").unwrap();
    assert_eq!(v.st, stc::UN_FIRST);
    assert_eq!(v.raw, 10_000_000);
    assert_eq!(get(&col, "cg:/", "mem.current").unwrap().v, 4_096_000_000.0);
    tick(&mut col, 2);
    let v = get(&col, "cg:/", "cpu.usage_usec").unwrap();
    assert_eq!((v.st, v.v), (stc::VALUE, 0.0), "an unchanged counter is a real zero");
    assert!(get(&col, "cg:/", "no.such.metric").is_none(), "an unmeasured metric is absent, never zero");
    put(&root, "cg/cpu.stat", "usage_usec 10000500\nuser_usec 1\nsystem_usec 1\nnr_periods 1\nnr_throttled 0\nthrottled_usec 0\n");
    tick(&mut col, 3);
    assert_eq!(get(&col, "cg:/", "cpu.usage_usec").unwrap().v, 500.0);
    put(&root, "cg/cpu.stat", "usage_usec 40\nuser_usec 1\nsystem_usec 1\nnr_periods 1\nnr_throttled 0\nthrottled_usec 0\n");
    let before = col.resets;
    tick(&mut col, 4);
    assert_eq!(get(&col, "cg:/", "cpu.usage_usec").unwrap().v, 40.0, "after a restart the reading is the delta");
    assert_eq!(col.resets, before + 1);
}

#[test]
fn per_cpu_percentages_and_host_gauges() {
    let (root, mut col) = world("cpu");
    tick(&mut col, 1);
    put(&root, "proc/stat", "cpu  200 0 100 900 20 9 9 0 0 0\ncpu0 150 0 75 500 15 4 6 0 0 0\nctxt 3000\nprocesses 510\nprocs_running 3\nprocs_blocked 0\n");
    tick(&mut col, 2);
    let g = |m: &str| get(&col, "cpu:0", m).unwrap().v;
    let total = 100.0 + 50.0 + 100.0 + 10.0 + 2.0 + 3.0;
    assert!((g("cpupct.us") - 100.0 / total * 100.0).abs() < 1e-9);
    assert!((g("cpupct.io") - 10.0 / total * 100.0).abs() < 1e-9);
    assert!((g("cpupct.irq") - 2.0 / total * 100.0).abs() < 1e-9);
    let sum: f64 = ["us", "sy", "io", "st", "irq", "si", "id"].iter().map(|k| g(&format!("cpupct.{k}"))).sum();
    assert!((sum - 100.0).abs() < 1e-9);
    assert_eq!(get(&col, "host", "host.procs_running").unwrap().v, 3.0);
    assert_eq!(get(&col, "host", "host.procs_blocked").unwrap().v, 0.0);
    assert_eq!(get(&col, "host", "host.ctxt").unwrap().v, 2000.0);
}

#[test]
fn memory_stack_sums_to_total() {
    let (_r, mut col) = world("memstack");
    tick(&mut col, 1);
    let parts = ["anon", "file_cache", "shmem", "buffers", "slab_reclaimable", "slab_unreclaim", "x"];
    let _ = parts;
    let names = ["anon", "file_cache", "shmem", "buffers", "slab_reclaimable", "slab_unreclaimable", "kernel_stack", "pagetables", "percpu", "zswap_pool", "free", "other"];
    let sum: f64 = names.iter().map(|n| get(&col, "host", &format!("memstack.{n}")).unwrap().v).sum();
    assert_eq!(sum, 1_000_000.0 * 1024.0);
    assert_eq!(get(&col, "host", "memstack.file_cache").unwrap().v, (300_000.0 - 50_000.0) * 1024.0);
}

// ---------------------------------------------------------------- cgroup shapes

#[test]
fn stacked_devices_unknown_lines_and_io_cost() {
    let root = tmp("stacked");
    mk_proc(&root);
    put(&root, "cg/io.stat", "253:0 rbytes=100 wbytes=0 rios=1 wios=0 dbytes=0 dios=0 cost.usage=999\n254:0 rbytes=50 wbytes=7 rios=1 wios=0 dbytes=0 dios=0 cost.vrate=12.50 cost.usage=77 cost.wait=3 cost.indebt=0 cost.indelay=0\n(unknown) \n");
    fs::create_dir_all(root.join("sys/dev/block/253:0/slaves/vda")).unwrap();
    fs::create_dir_all(root.join("sys/dev/block/254:0/slaves")).unwrap();
    let mut col = Collector::new(cfg_for(&root), Dict::default(), Instant::now());
    tick(&mut col, 1);
    assert_eq!(get(&col, "cg:/", "io.rbytes").unwrap().raw, 50, "stacked dm device excluded");
    assert_eq!(get(&col, "cg:/", "io.cost.usage").unwrap().raw, 77);
    assert_eq!(get(&col, "cg:/", "io.cost.vrate").unwrap().v, 12.5);
    // the other cgroup files are absent: sticky not-applicable states, not zeros
    let s = sticky(&col, "cg:/", "mem.current").unwrap();
    assert_eq!(s.st, stc::NA_CONTROLLER);
}

#[test]
fn root_pressure_falls_back_to_proc_and_missing_full_is_typed() {
    let root = tmp("psi");
    mk_proc(&root);
    fs::create_dir_all(root.join("cg")).unwrap();
    let mut col = Collector::new(cfg_for(&root), Dict::default(), Instant::now());
    tick(&mut col, 1);
    assert_eq!(get(&col, "cg:/", "psi.cpu.some.avg10").unwrap().v, 1.5);
    assert_eq!(get(&col, "cg:/", "psi.cpu.some.total").unwrap().raw, 1000);
    assert_eq!(get(&col, "cg:/", "psi.cpu.full.avg10").unwrap().st, stc::NA_NO_FULL);
    assert_eq!(get(&col, "cg:/", "psi.memory.full.avg10").unwrap().v, 0.25);
}

#[test]
fn limits_are_values_or_typed_states() {
    let (root, mut col) = world("limits");
    put(&root, "cg/memory.swap.max", "0\n");
    put(&root, "cg/memory.zswap.max", "max\n");
    put(&root, "cg/io.max", "8:0 rbps=max wbps=1000 riops=max wiops=max\n");
    tick(&mut col, 1);
    assert_eq!(sticky(&col, "cg:/", "limit.memory.swap.max").unwrap().v, 0.0);
    assert_eq!(sticky(&col, "cg:/", "limit.memory.zswap.max").unwrap().v, -1.0, "max is -1");
    assert_eq!(sticky(&col, "cg:/", "limit.cpu.max.quota").unwrap().v, -1.0);
    assert_eq!(sticky(&col, "cg:/", "limit.cpu.max.period").unwrap().v, 100000.0);
    assert_eq!(sticky(&col, "cg:/", "limit.cpu.weight").unwrap().v, 100.0);
    assert_eq!(sticky(&col, "cg:/", "limit.io.weight").unwrap().v, 100.0);
    assert_eq!(sticky(&col, "cg:/", "limit.io.max.capped").unwrap().v, 1.0);
    // a cgroup without the swap controller file: not applicable, not zero
    let s = sticky(&col, "cg:/system.slice", "limit.memory.swap.max").unwrap();
    assert_eq!(s.st, stc::NA_CONTROLLER);
}

#[test]
fn hard_events() {
    let (root, mut col) = world("events");
    tick(&mut col, 1);
    put(&root, "cg/memory.events", "low 0\nhigh 1\nmax 0\noom 2\noom_kill 2\n");
    put(&root, "cg/memory.max", "1048576\n");
    put(&root, "cg/memory.pressure", "some avg10=20.00 avg60=0.20 avg300=0.30 total=1000\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n");
    tick(&mut col, 2);
    let kinds: Vec<String> = col.events.iter().map(|e| serde_json::from_str::<Value>(&e.json).unwrap()["type"].as_str().unwrap().to_string()).collect();
    for k in ["oom_kill", "limit_changed", "psi_cross"] {
        assert!(kinds.iter().any(|x| x == k), "missing {k} in {kinds:?}");
    }
    let oom = col.events.iter().find(|e| e.json.contains("\"oom_kill\",\"entity\":\"cg:/\"")).unwrap();
    assert!(oom.json.contains("\"delta\":2"));
    fs::remove_dir_all(root.join("cg/besteffort.slice")).unwrap();
    tick(&mut col, 3);
    let gone = col.events.iter().filter(|e| e.json.contains("cgroup_vanished")).count();
    assert_eq!(gone, 2, "the slice and its child");
}

// ---------------------------------------------------------------- hosts: zswap, ksm

#[test]
fn zswap_falls_back_to_meminfo_with_typed_states() {
    let (root, mut col) = world("zswap");
    tick(&mut col, 1);
    assert_eq!(get(&col, "host", "zswap.debugfs_ok").unwrap().st, stc::UN_DEBUGFS);
    assert_eq!(get(&col, "host", "zswap.reject_alloc_fail").unwrap().st, stc::UN_DEBUGFS);
    assert_eq!(get(&col, "host", "zswap.pool_bytes").unwrap().v, 1000.0 * 1024.0, "meminfo fallback");
    assert_eq!(get(&col, "host", "ksm.pages_shared").unwrap().st, stc::NA_FEATURE);
    // now with debugfs and ksm present
    put(&root, "sys/kernel/debug/zswap/stored_pages", "10\n");
    put(&root, "sys/kernel/debug/zswap/pool_total_size", "4096\n");
    put(&root, "sys/kernel/debug/zswap/reject_alloc_fail", "7\n");
    put(&root, "sys/kernel/debug/zswap/pool_limit_hit", "3\n");
    put(&root, "sys/kernel/mm/ksm/run", "1\n");
    put(&root, "sys/kernel/mm/ksm/pages_shared", "5\n");
    put(&root, "sys/kernel/mm/ksm/general_profit", "-3\n");
    put(&root, "sys/module/zswap/parameters/enabled", "Y\n");
    put(&root, "sys/module/zswap/parameters/max_pool_percent", "20\n");
    tick(&mut col, 2);
    assert_eq!(get(&col, "host", "zswap.debugfs_ok").unwrap().v, 1.0);
    assert_eq!(get(&col, "host", "zswap.pool_bytes").unwrap().v, 4096.0);
    assert_eq!(get(&col, "host", "zswap.stored_bytes").unwrap().v, 10.0 * col.page_size as f64);
    assert_eq!(get(&col, "host", "zswap.reject_alloc_fail").unwrap().raw, 7);
    assert_eq!(get(&col, "host", "zswap.reject_compress_poor").unwrap().st, stc::NA_FEATURE, "single missing file on a readable dir");
    assert_eq!(get(&col, "host", "ksm.pages_shared").unwrap().v, 5.0);
    assert_eq!(get(&col, "host", "ksm.general_profit").unwrap().v, -3.0);
    assert_eq!(get(&col, "host", "zswap.enabled").unwrap().v, 1.0);
    assert_eq!(get(&col, "host", "zswap.max_pool_percent").unwrap().v, 20.0);
}

#[test]
fn disk_await_is_not_applicable_without_io() {
    let (root, mut col) = world("disk");
    tick(&mut col, 1);
    tick(&mut col, 2);
    assert_eq!(get(&col, "dev:vda", "disk.await_ms").unwrap().st, stc::NA_IDLE);
    put(&root, "proc/diskstats", "   8       0 vda 110 0 2200 150 200 0 4000 80 0 160 230 0 0 0 0\n");
    tick(&mut col, 3);
    assert_eq!(get(&col, "dev:vda", "disk.await_ms").unwrap().v, 10.0);
    assert_eq!(get(&col, "dev:vda", "disk.r_await_ms").unwrap().v, 10.0);
    assert_eq!(get(&col, "dev:vda", "disk.w_await_ms").unwrap().st, stc::NA_IDLE);
    assert!((get(&col, "dev:vda", "disk.util_pct").unwrap().v - 10.0).abs() < 1e-9);
}

// ---------------------------------------------------------------- processes

fn proc_files(root: &Path, pid: u32, comm: &str, state: &str, ut: u64, wb: u64, blk: u64, cg: &str) {
    put(root, &format!("proc/{pid}/stat"), &pstat(pid, comm, state, 0, ut, 0, 1, 77, 10, blk));
    put(root, &format!("proc/{pid}/io"), &format!("rchar: 0\nread_bytes: 0\nwrite_bytes: {wb}\n"));
    put(root, &format!("proc/{pid}/schedstat"), "100 2000000 3\n");
    put(root, &format!("proc/{pid}/cgroup"), &format!("0::{cg}\n"));
    put(root, &format!("proc/{pid}/cmdline"), &format!("{comm}\0arg\0"));
}

#[test]
fn process_scan_reads_io_for_candidates_and_sweeps_for_the_rest() {
    let (root, mut col) = world("procs");
    proc_files(&root, 100, "busy", "R", 0, 0, 0, "/system.slice/a.service");
    proc_files(&root, 200, "iohot", "S", 0, 0, 0, "/system.slice/b.service");
    proc_files(&root, 300, "stuck", "D", 0, 0, 0, "/system.slice/db.service");
    tick(&mut col, 1);
    assert_eq!(col.procs.io_read, 3, "first tick is a full sweep");
    proc_files(&root, 100, "busy", "R", 50, 0, 0, "/system.slice/a.service");
    proc_files(&root, 200, "iohot", "S", 0, 3_000_000, 0, "/system.slice/b.service");
    proc_files(&root, 300, "stuck", "D", 0, 0, 0, "/system.slice/db.service");
    tick(&mut col, 2);
    assert_eq!(col.procs.io_read, 2, "idle sleeping pid 200 is not read between sweeps");
    assert!(!col.procs.out.iter().any(|p| p.pid == 200));
    let busy = col.procs.out.iter().find(|p| p.pid == 100).unwrap();
    assert_eq!(busy.why, "cpu");
    assert!((busy.cpu_pct.v - 50.0 * 100.0 / col.clk_tck as f64).abs() < 1e-9);
    proc_files(&root, 100, "busy", "R", 100, 0, 0, "/system.slice/a.service");
    proc_files(&root, 200, "iohot", "S", 0, 6_000_000, 0, "/system.slice/b.service");
    tick(&mut col, 3);
    assert_eq!(col.procs.io_read, 3, "tick 3 is the sweep (3 s cadence)");
    let hot = col.procs.out.iter().find(|p| p.pid == 200).expect("IO-hot idle process discovered by the sweep");
    assert_eq!(hot.why, "io");
    assert_eq!(hot.io_write_bps.v, 3_000_000.0, "rate uses the time since its last io read");
    assert_eq!(hot.cmdline, "iohot arg");
    let stuck = col.procs.out.iter().find(|p| p.pid == 300).unwrap();
    assert_eq!((stuck.why, stuck.state), ("D", 'D'));
    assert_eq!(stuck.blkio_delay_ms_ps.st, stc::NA_DELAYACCT);
    assert_eq!(col.procs.dmap.get("/system.slice/db.service"), Some(&1));
    assert_eq!(col.procs.dstate, 1);
}

#[test]
fn d_state_count_per_cgroup_and_blkio_delay_when_enabled() {
    let (root, mut col) = world("dstate");
    put(&root, "proc/sys/kernel/task_delayacct", "1\n");
    fs::create_dir_all(root.join("cg/db.service")).unwrap();
    proc_files(&root, 300, "stuck", "D", 0, 0, 0, "/db.service");
    tick(&mut col, 1);
    proc_files(&root, 300, "stuck", "D", 0, 0, 10, "/db.service");
    tick(&mut col, 2);
    assert_eq!(get(&col, "cg:/db.service", "procs.d_state").unwrap().v, 1.0);
    assert_eq!(get(&col, "cg:/", "procs.d_state").unwrap().v, 0.0, "a scanned zero is a value");
    let p = &col.procs.out[0];
    assert_eq!(p.blkio_delay_ms_ps.st, stc::VALUE);
    assert!((p.blkio_delay_ms_ps.v - 10.0 * 1000.0 / col.clk_tck as f64).abs() < 1e-9);
    assert_eq!(p.rss_bytes, 10 * col.page_size);
}

// ---------------------------------------------------------------- dictionary

#[test]
fn dictionary_reuses_ids_after_retention_and_survives_reload() {
    let mut d = Dict::default();
    let a = d.ent("cg:/a", 0);
    let b = d.ent("cg:/b", 0);
    d.touch(b, 900);
    assert_eq!(d.gc(1000, 100), 1, "a unseen for 1000 s, b seen 100 s ago");
    assert_eq!(d.ents[a as usize], "");
    assert_eq!(d.live_ents(), 1);
    let c = d.ent("cg:/c", 1000);
    assert_eq!(c, a, "freed id is reused");
    assert!(d.ents_dirty);
    // persisted by the store, then reloaded: ids and free slots survive
    let root = tmp("dict");
    let mut cfg = cfg_for(&root);
    cfg.store.enabled = true;
    let mut st = Store::open(&cfg, 0, 0).unwrap();
    let x = d.ent("cg:/x", 1000);
    st.sync_dict(&mut d);
    assert!(!d.ents_dirty);
    d.gc(100_000, 10);
    st.sync_dict(&mut d);
    let l = Dict::load(&format!("{}/cold", root.display()), 5);
    assert_eq!(l.ents.len(), d.ents.len());
    assert_eq!(l.live_ents(), d.live_ents());
    let _ = x;
}

// ---------------------------------------------------------------- store and tiers

fn push_frames(st: &mut Store, d: &mut Dict, base: u64, n: u64) -> (u32, u32, u32, u32) {
    let e = d.ent("cg:/x", 0);
    let (g, c, s) = (d.met("g.metric", false), d.met("c.metric", true), d.met("s.metric", false));
    for i in 0..n {
        let frame = vec![(
            e,
            vec![
                Val { m: g, v: (i + 1) as f64, raw: NONE, st: 0 },
                Val { m: c, v: if i % 2 == 0 { 2.0 } else { 0.0 }, raw: 100 + i, st: 0 },
                Val { m: s, v: 0.0, raw: NONE, st: stc::UN_DEBUGFS },
            ],
        )];
        st.push(&frame, base + i * 1000, 1000, d);
    }
    st.sync_dict(d);
    (e, g, c, s)
}

fn query(cfg: &Config, ent: &str, met: &str, tier: &str) -> Value {
    let r = crate::store::series(cfg, ent, met, tier, 60, 1 << 20).unwrap();
    serde_json::from_str(&format!("{{{}}}", r.body)).unwrap()
}

#[test]
fn tiers_roll_up_counters_to_sums_gauges_to_four_stats_and_keep_states() {
    let root = tmp("tiers");
    let cfg = cfg_for(&root);
    let mut st = Store::open(&cfg, 0, 0).unwrap();
    let mut d = Dict::default();
    let base = wall_ms() / 1000 * 1000 - 15_000;
    push_frames(&mut st, &mut d, base, 12);
    let hot = query(&cfg, "cg:/x", "g.metric", "hot");
    let pts = hot["points"].as_array().unwrap();
    assert_eq!(pts.len(), 12);
    assert_eq!(pts[0][1], 1.0);
    assert_eq!(pts[11][1], 12.0);
    let c = query(&cfg, "cg:/x", "c.metric", "hot");
    let deltas: Vec<f64> = c["points"].as_array().unwrap().iter().map(|p| p[1].as_f64().unwrap()).collect();
    assert_eq!(&deltas[..4], &[2.0, 0.0, 2.0, 0.0], "zero deltas are stored");
    let s = query(&cfg, "cg:/x", "s.metric", "hot");
    assert_eq!(s["points"][0][1]["s"], "unavailable");
    assert_eq!(s["points"][0][1]["why"], "debugfs_unreadable");
    let warm = query(&cfg, "cg:/x", "g.metric", "warm");
    assert_eq!(warm["fields"], serde_json::json!(["ts_ms", "avg", "max", "min", "p95"]));
    let w = &warm["points"].as_array().unwrap()[0];
    assert_eq!((w[1].as_f64(), w[2].as_f64(), w[3].as_f64(), w[4].as_f64()), (Some(1.5), Some(2.0), Some(1.0), Some(2.0)));
    assert_eq!(warm["points"].as_array().unwrap().len(), 6);
    let cold = query(&cfg, "cg:/x", "g.metric", "cold");
    assert_eq!(cold["points"].as_array().unwrap().len(), 3, "zstd round trip");
    let k = &cold["points"][0];
    assert_eq!((k[1].as_f64(), k[2].as_f64(), k[3].as_f64(), k[4].as_f64()), (Some(2.5), Some(4.0), Some(1.0), Some(4.0)));
    let cc = query(&cfg, "cg:/x", "c.metric", "cold");
    assert_eq!(cc["points"][0][1], 4.0);
    let cs = query(&cfg, "cg:/x", "s.metric", "cold");
    assert_eq!(cs["points"][0][1]["s"], "unavailable", "an all-state bucket stays a state");
}

#[test]
fn byte_cap_holds_even_for_one_oversized_segment() {
    let root = tmp("cap");
    let tiers = "[[tier]]\nname = \"cold\"\nstep_s = 1\nkeep_s = 86400\nsegment_s = 3600\nmedium = \"disk\"\n";
    let cfg = Config::parse(&cfg_text(&root, 4_000_000, tiers, 1500, 3)).unwrap();
    let mut st = Store::open(&cfg, 0, 0).unwrap();
    let mut d = Dict::default();
    let e = d.ent("cg:/x", 0);
    let ms: Vec<u32> = (0..40).map(|i| d.met(&format!("m{i}"), false)).collect();
    let base = wall_ms() - 200_000;
    for i in 0..150u64 {
        let vals: Vec<Val> = ms.iter().enumerate().map(|(k, m)| Val { m: *m, v: ((i * 7919 + k as u64 * 104729) % 100_003) as f64 * 1.37, raw: NONE, st: 0 }).collect();
        st.push(&[(e, vals)], base + i * 1000, 1000, &mut d);
        assert!(st.sizes().1 <= 1500, "cap exceeded at frame {i}: {}", st.sizes().1);
    }
    assert!(st.sizes().1 > 0);
}

// ---------------------------------------------------------------- protocol

fn ctx_with(root: &Path, col: &mut Collector, resp_cap: usize) -> Arc<Ctx> {
    let cfg = Config::parse(&cfg_text(root, resp_cap, TIERS3, 1 << 30, 3)).unwrap();
    let ctx = Arc::new(Ctx::new(cfg, "{\"mode\":\"host\"}".to_string()));
    let mut sb = SnapBuilder::default();
    let snap = sb.build(col, 2, wall_ms(), 1000, 1234, Status::default());
    ctx.publish(snap);
    ctx
}

fn j(s: &str) -> Value {
    serde_json::from_str(s).unwrap_or_else(|e| panic!("not JSON ({e}): {s}"))
}

#[test]
fn hello_current_status_and_typed_values() {
    let (root, mut col) = world("proto");
    tick(&mut col, 1);
    tick(&mut col, 2);
    let ctx = ctx_with(&root, &mut col, 4_000_000);
    let h = j(&handle(&ctx, r#"{"req":"hello","id":7}"#));
    assert_eq!(h["ok"], true);
    assert_eq!(h["id"], 7);
    assert_eq!(h["protocol"], 1);
    assert_eq!(h["schema"], 1);
    assert_eq!(h["epoch"], ctx.epoch.as_str());
    assert_eq!(h["cgroup_view"]["mode"], "host");
    assert_eq!(h["state_codes"].as_array().unwrap().len(), stc::MAX as usize + 1);
    assert_eq!(h["clk_tck_source"], "sysconf");
    let other = Ctx::new(ctx.cfg.clone(), "{}".into());
    assert_ne!(other.epoch, ctx.epoch, "a new instance has a new epoch");
    let c = j(&handle(&ctx, r#"{"req":"current"}"#));
    assert_eq!(c["truncated"], false);
    let root_ent = &c["entities"]["cg:/"];
    assert_eq!(root_ent["mem.current"]["s"], "value");
    assert_eq!(root_ent["cpu.usage_usec"]["v"], 0.0);
    assert!(root_ent["cpu.usage_usec"]["rate"].is_number());
    assert_eq!(root_ent["limit.memory.swap.max"]["s"], "not_applicable");
    assert!(root_ent["limit.memory.swap.max"]["why"].is_string());
    assert!(root_ent["limit.memory.swap.max"].get("v").is_none(), "no value on a state");
    let only = j(&handle(&ctx, r#"{"req":"current","prefix":"cg:/system.slice","procs":false}"#));
    assert!(only["entities"].as_object().unwrap().keys().all(|k| k.starts_with("cg:/system.slice")));
    let s = j(&handle(&ctx, r#"{"req":"status"}"#));
    for k in ["self", "timing", "throttle", "tiers", "disk", "tmpfs", "dict"] {
        assert!(s.get(k).is_some(), "status.{k}");
    }
    assert_eq!(s["self"]["cpu_pct_recent_60s"]["s"], "unavailable");
    assert_eq!(s["timing"]["tick_budget_us"], 1_000_000);
}

#[test]
fn malformed_and_unknown_requests_get_typed_errors() {
    let (root, mut col) = world("bad");
    tick(&mut col, 1);
    let ctx = ctx_with(&root, &mut col, 4_000_000);
    for (req, code) in [
        ("not json", "bad_json"),
        ("[]", "bad_request"),
        (r#"{"req":5}"#, "bad_request"),
        (r#"{"req":"frobnicate"}"#, "unknown_req"),
        (r#"{"req":"series"}"#, "bad_request"),
        (r#"{"req":"series","entity":"cg:/","metric":"x","tier":"hot"}"#, "bad_request"),
        (r#"{"req":"series","entity":"cg:/","metric":"x","tier":"nope","window_s":5}"#, "unknown_tier"),
        (r#"{"req":"series","entity":"cg:/","metric":"x","tier":"hot","window_s":5}"#, "unknown_entity"),
        (r#"{"req":"events"}"#, "bad_request"),
    ] {
        let r = j(&handle(&ctx, req));
        assert_eq!(r["ok"], false, "{req}");
        assert_eq!(r["error"]["code"], code, "{req}");
        assert_eq!(r["epoch"], ctx.epoch.as_str());
    }
}

#[test]
fn responses_are_bounded_and_say_truncated() {
    let (root, mut col) = world("cap");
    tick(&mut col, 1);
    tick(&mut col, 2);
    let ctx = ctx_with(&root, &mut col, 6000);
    let r = handle(&ctx, r#"{"req":"current"}"#);
    assert!(r.len() <= 6000, "{} bytes", r.len());
    let v = j(&r);
    assert_eq!(v["truncated"], true);
    assert_eq!(v["ok"], true);
}

#[test]
fn events_request_returns_window_entity_filter_and_limit() {
    let (root, mut col) = world("evreq");
    tick(&mut col, 1);
    let ctx = ctx_with(&root, &mut col, 4_000_000);
    let mut st = Store::open(&ctx.cfg, 0, 0).unwrap();
    let evs: Vec<_> = (0..5).map(|i| mk_event(Instant::now(), "oom_kill", if i < 3 { "cg:/a" } else { "cg:/b" }, "{}")).collect();
    st.append_events(&evs);
    let r = j(&handle(&ctx, r#"{"req":"events","window_s":60}"#));
    assert_eq!(r["events"].as_array().unwrap().len(), 5);
    let r = j(&handle(&ctx, r#"{"req":"events","window_s":60,"entity":"cg:/a"}"#));
    assert_eq!(r["events"].as_array().unwrap().len(), 3);
    let r = j(&handle(&ctx, r#"{"req":"events","window_s":60,"limit":2}"#));
    assert_eq!(r["events"].as_array().unwrap().len(), 2);
    assert_eq!(r["truncated"], true);
}

fn rpc(s: &mut UnixStream, r: &mut BufReader<UnixStream>, req: &str) -> Value {
    s.write_all(req.as_bytes()).unwrap();
    s.write_all(b"\n").unwrap();
    let mut l = String::new();
    r.read_line(&mut l).unwrap();
    j(&l)
}

fn connect(path: &str) -> (UnixStream, BufReader<UnixStream>) {
    let s = UnixStream::connect(path).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(20))).unwrap();
    let r = BufReader::new(s.try_clone().unwrap());
    (s, r)
}

#[test]
fn socket_rejects_oversized_requests_and_stalled_clients_do_not_block_publishing() {
    let (root, mut col) = world("sock");
    tick(&mut col, 1);
    tick(&mut col, 2);
    let ctx = ctx_with(&root, &mut col, 4_000_000);
    crate::serve::spawn(ctx.clone()).unwrap();
    let path = ctx.cfg.sampler.socket_path.clone();
    // an oversized request line (no newline within max_request_bytes) is refused
    {
        let (mut s, mut r) = connect(&path);
        s.write_all(&vec![b'x'; 2000]).unwrap();
        let mut l = String::new();
        r.read_line(&mut l).unwrap();
        assert_eq!(j(&l)["error"]["code"], "request_too_large");
    }
    // a client floods requests and never reads
    let (mut flood, _keep) = connect(&path);
    let t = std::thread::spawn(move || {
        for _ in 0..20000 {
            if flood.write_all(b"{\"req\":\"current\"}\n").is_err() {
                break;
            }
        }
    });
    // sampling-side publishing keeps going, and another client is served
    let mut sb = SnapBuilder::default();
    for n in 3..60 {
        tick(&mut col, n);
        ctx.publish(sb.build(&mut col, n, wall_ms(), 1000, 1, Status::default()));
    }
    let (mut s, mut r) = connect(&path);
    let h = rpc(&mut s, &mut r, r#"{"req":"hello"}"#);
    assert_eq!(h["ok"], true);
    let bad = rpc(&mut s, &mut r, "garbage");
    assert_eq!(bad["error"]["code"], "bad_json");
    let again = rpc(&mut s, &mut r, r#"{"req":"status"}"#);
    assert_eq!(again["ok"], true, "the connection survives a malformed request");
    t.join().unwrap();
}

#[test]
fn cgroup_view_reports_namespaced_when_self_is_at_root() {
    let (root, mut col) = world("view");
    let _ = &mut col;
    let cfg = cfg_for(&root);
    assert!(crate::selfmon::cgroup_view(&cfg).contains("\"mode\":\"host\""));
    put(&root, "proc/self/cgroup", "0::/\n");
    let v = j(&crate::selfmon::cgroup_view(&cfg));
    assert_eq!(v["mode"], "namespaced");
    assert_eq!(v["because"], "self_at_cgroup_root");
    fs::remove_file(root.join("proc/self/cgroup")).unwrap();
    assert_eq!(j(&crate::selfmon::cgroup_view(&cfg))["mode"], "unknown");
}

#[test]
fn config_validation_rejects_bad_shapes() {
    let root = tmp("cfg");
    let ok = cfg_text(&root, 4_000_000, TIERS3, 1 << 30, 3);
    assert!(Config::parse(&ok).is_ok());
    let two_disk = format!("{}\n[[tier]]\nname = \"c2\"\nstep_s = 8\nkeep_s = 10\nsegment_s = 10\nmedium = \"disk\"\n", ok);
    assert!(Config::parse(&two_disk).unwrap_err().contains("at most one"));
    assert!(Config::parse(&ok.replace("max_clients = 4", "max_clients = 0")).is_err());
    assert!(Config::parse(&ok.replace("cmdline_max = 256", "")).is_err(), "no defaults in code");
    assert!(Config::parse(&ok.replace("step_s = 2", "step_s = 1")).is_err(), "tiers must increase");
}
