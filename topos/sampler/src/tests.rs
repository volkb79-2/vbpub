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
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

static SEQ: AtomicUsize = AtomicUsize::new(0);

/// A private work area: `<tmp>/tsp-..-name/` is 0700 and owned by the test user, and the returned
/// root `<..>/w` lives inside it. The sampler refuses data directories whose parent is
/// group/world-writable (X-01), and `/tmp` itself is sticky and world-writable.
fn tmp(name: &str) -> PathBuf {
    use std::os::unix::fs::PermissionsExt;
    let base = std::env::temp_dir().join(format!("tsp-{}-{}-{}", std::process::id(), SEQ.fetch_add(1, Ordering::SeqCst), name));
    let _ = fs::remove_dir_all(&base);
    fs::create_dir_all(&base).unwrap();
    fs::set_permissions(&base, fs::Permissions::from_mode(0o700)).unwrap();
    let p = base.join("w");
    fs::create_dir(&p).unwrap();
    fs::set_permissions(&p, fs::Permissions::from_mode(0o755)).unwrap();
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
socket_mode = "0600"
duration_s = 0
stats_path = ""
[process]
scan = true
top_cpu = 20
top_io = 20
cap = 64
full_sweep_every_s = {sweep_s}
cmdline_max = 256
cmdline_backoff_ms = 30000
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
require_tmpfs = false
writer_queue_bytes = 8388608
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
    let mut col = Collector::new(cfg, Dict::default(), Instant::now());
    col.procs.sync_cmdline = true; // the helper pool has its own test
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
    let v = get(&col, "cg:/", "cpu.usage_usec").unwrap();
    assert_eq!((v.st, v.raw), (stc::UN_RESET, 40), "S-04: a counter that went backwards is a reset state, never a spike");
    assert_eq!(col.resets, before + 1);
    put(&root, "cg/cpu.stat", "usage_usec 100\nuser_usec 1\nsystem_usec 1\nnr_periods 1\nnr_throttled 0\nthrottled_usec 0\n");
    tick(&mut col, 5);
    assert_eq!(get(&col, "cg:/", "cpu.usage_usec").unwrap().v, 60.0, "the reading after the reset is the new baseline");
}

#[test]
fn unparsable_values_are_unavailable_never_zero_and_iowait_clamps() {
    let (root, mut col) = world("s04");
    put(&root, "cg/io.stat", "8:0 rbytes=100 wbytes=0 rios=1 wios=0 dbytes=0 dios=0\n");
    fs::create_dir_all(root.join("sys/dev/block/8:0/slaves")).unwrap();
    tick(&mut col, 1);
    put(&root, "cg/io.stat", "8:0 rbytes=12x wbytes=0 rios=1 wios=0 dbytes=0 dios=0\n");
    put(&root, "proc/diskstats", "   8       0 vda 100 0 2000 50 200 0 4000 80 0 60 130 0 0 0 0\n");
    tick(&mut col, 2);
    assert_eq!(get(&col, "cg:/", "io.rbytes").unwrap().st, stc::UN_PARSE, "a parse failure is a state, not a 0");
    put(&root, "cg/io.stat", "8:0 rbytes=160 wbytes=0 rios=1 wios=0 dbytes=0 dios=0\n");
    tick(&mut col, 3);
    assert_eq!(get(&col, "cg:/", "io.rbytes").unwrap().v, 60.0, "no spike after the bad read");
    put(&root, "proc/diskstats", "   8       0 vda 100 0 2000 50 200 0 4000 80 0 6x 130 0 0 0 0\n");
    tick(&mut col, 4);
    assert_eq!(get(&col, "dev:vda", "disk.io_ticks_ms").unwrap().st, stc::UN_PARSE);
    // iowait goes backwards (NO_HZ): clamped to 0 for that field, not a reset and not a spike
    put(&root, "proc/stat", "cpu  200 0 100 900 20 9 9 0 0 0\ncpu0 150 0 75 500 15 4 6 0 0 0\nctxt 3000\nprocesses 510\nprocs_running 3\nprocs_blocked 0\n");
    tick(&mut col, 5);
    let resets = col.resets;
    put(&root, "proc/stat", "cpu  300 0 150 1000 12 12 12 0 0 0\ncpu0 250 0 100 600 9 6 8 0 0 0\nctxt 3000\nprocesses 510\nprocs_running 3\nprocs_blocked 0\n");
    tick(&mut col, 6);
    let io = get(&col, "cpu:all", "cpu.iowait").unwrap();
    assert_eq!((io.st, io.v), (stc::VALUE, 0.0));
    assert_eq!(col.resets, resets, "not counted as a reset");
    assert!(get(&col, "cpu:all", "cpupct.io").unwrap().v.abs() < 1e-9);
    assert!(get(&col, "cpu:all", "cpu.busy_pct").unwrap().v <= 100.0);
}

#[test]
fn counter_resets_and_is_bottom_do_not_flip_on_a_failed_read() {
    let mut cache = std::collections::HashMap::new();
    let mut last = std::collections::HashMap::new();
    let root = tmp("bottom");
    fs::create_dir_all(root.join("dev/block/9:0/slaves/sda")).unwrap();
    let sys = root.display().to_string();
    assert!(!crate::collect::is_bottom_for_test(&mut cache, &mut last, &sys, b"9:0"), "stacked");
    fs::remove_dir_all(root.join("dev/block/9:0")).unwrap();
    cache.clear(); // next interval; the directory is gone (a failed read)
    assert!(!crate::collect::is_bottom_for_test(&mut cache, &mut last, &sys, b"9:0"), "the last good answer is kept");
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
    assert_eq!(get(&col, "cg:/", "io.cost.usage").unwrap().raw, 999 + 77, "iocost state is summed over every device that has it");
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
    let (mets, ents) = crate::dict::read_lists(&cfg.store.cold_dir);
    let d = crate::store::Dicts { mets: &mets, ents: &ents };
    let r = crate::store::series(cfg, &d, ent, met, tier, 60, 1 << 20).unwrap();
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
    // A whole hour back, so all 150 frames stay in one 3600 s segment at any time of day.
    let base = wall_ms() / 3_600_000 * 3_600_000 - 3_600_000;
    let mut peak = 0;
    for i in 0..150u64 {
        let vals: Vec<Val> = ms.iter().enumerate().map(|(k, m)| Val { m: *m, v: ((i * 7919 + k as u64 * 104729) % 100_003) as f64 * 1.37, raw: NONE, st: 0 }).collect();
        st.push(&[(e, vals)], base + i * 1000, 1000, &mut d);
        assert!(st.sizes().1 <= 1500, "cap exceeded at frame {i}: {}", st.sizes().1);
        peak = peak.max(st.sizes().1);
    }
    // Eviction can empty the only segment, so the size after the last frame may be 0.
    assert!(peak > 0, "nothing was ever stored");
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
        (r#"{"req":"series","entity":"cg:/no-such","metric":"x","tier":"hot","window_s":5}"#, "unknown_entity"),
        (r#"{"req":"series","entity":"cg:/","metric":"x","tier":"hot","window_s":5}"#, "unknown_metric"),
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
    let evs: Vec<String> = (0..5).map(|i| mk_event(Instant::now(), "oom_kill", if i < 3 { "cg:/a" } else { "cg:/b" }, "{}").json).collect();
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
    let path = ctx.cfg.sampler.socket_path.clone();
    crate::serve::spawn(ctx.clone(), crate::fsafe::Instance::acquire(&path).unwrap()).unwrap();
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
    assert!(Config::parse(&ok.replace("client_idle_ms = 5000", "client_idle_ms = 0")).is_err(), "S-15: a zero timeout would switch the protection off");
    assert!(Config::parse(&ok.replace("client_write_ms = 200", "client_write_ms = 0")).is_err());
    // X-03, X-05, X-06: the new keys are required and validated
    assert!(Config::parse(&ok.replace("require_tmpfs = false", "")).is_err(), "no defaults in code");
    assert!(Config::parse(&ok.replace("writer_queue_bytes = 8388608", "writer_queue_bytes = 10")).is_err());
    assert!(Config::parse(&ok.replace("cmdline_backoff_ms = 30000", "cmdline_backoff_ms = 0")).is_err());
}

// ================================================================ review fixes (S-01 .. S-22)

fn seg_files(dir: &Path, prefix: &str) -> Vec<PathBuf> {
    let mut v: Vec<PathBuf> = fs::read_dir(dir)
        .unwrap()
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.file_name().unwrap().to_string_lossy().starts_with(prefix) && p.extension().map_or(false, |x| x == "seg"))
        .collect();
    v.sort();
    v
}

fn one_val(e: u32, m: u32, v: f64) -> Vec<(u32, Vec<Val>)> {
    vec![(e, vec![Val { m, v, raw: NONE, st: 0 }])]
}

#[test]
fn s01_torn_tail_is_cut_at_restart_and_bad_frames_stop_the_reader_and_are_reported() {
    let root = tmp("s01");
    let cfg = cfg_for(&root);
    let mut d = Dict::default();
    let (e, m) = (d.ent("cg:/x", 0), d.met("g.metric", false));
    let base = wall_ms() / 1000 * 1000 - 20_000;
    let mut st = Store::open(&cfg, 0, 0).unwrap();
    for i in 0..5 {
        st.push(&one_val(e, m, i as f64), base + i * 1000, 1000, &d);
    }
    st.sync_dict(&mut d);
    drop(st);
    let hot = seg_files(&root.join("tm"), "hot-");
    assert!(!hot.is_empty());
    let last = hot.last().unwrap().clone();
    let good_len = fs::metadata(&last).unwrap().len();
    // a crash in the middle of a frame: magic, a length, then nothing
    let mut f = fs::OpenOptions::new().append(true).open(&last).unwrap();
    f.write_all(b"TSF1\xff\x00\x00\x00junk").unwrap();
    drop(f);
    let torn_before = crate::store::CTR.torn_open.load(Ordering::Relaxed);
    let mut st = Store::open(&cfg, d.mets.len(), d.ents.len()).unwrap();
    assert_eq!(fs::metadata(&last).unwrap().len(), good_len, "the torn tail was truncated to the last good frame");
    assert!(crate::store::CTR.torn_open.load(Ordering::Relaxed) > torn_before, "and counted");
    st.push(&one_val(e, m, 99.0), base + 5000, 1000, &d);
    let pts = query(&cfg, "cg:/x", "g.metric", "hot");
    let vals: Vec<f64> = pts["points"].as_array().unwrap().iter().map(|p| p[1].as_f64().unwrap()).collect();
    assert_eq!(vals, vec![0.0, 1.0, 2.0, 3.0, 4.0, 99.0], "frames written after the restart are readable");
    assert_eq!(pts["corrupt_frames"], 0);
    // a flipped bit in the payload of the last frame: the reader stops there and says so
    drop(st);
    let newest = seg_files(&root.join("tm"), "hot-").pop().unwrap();
    let mut bytes = fs::read(&newest).unwrap();
    let n = bytes.len();
    bytes[n - 3] ^= 0x40;
    fs::write(&newest, &bytes).unwrap();
    let pts = query(&cfg, "cg:/x", "g.metric", "hot");
    assert_eq!(pts["points"].as_array().unwrap().len(), 5, "everything before the bad frame is served");
    assert_eq!(pts["corrupt_frames"], 1, "the gap is reported");
}

#[test]
fn s01_a_failed_write_is_rolled_back_and_counted() {
    let root = tmp("s01w");
    let cfg = cfg_for(&root);
    let mut d = Dict::default();
    let (e, m) = (d.ent("cg:/x", 0), d.met("g.metric", false));
    let mut st = Store::open(&cfg, 0, 0).unwrap();
    // Aligned to the hot tier's 10 s segment: otherwise one wall-clock second in ten puts the
    // second push in a new segment, whose healthy file replaces the broken one.
    let base = wall_ms() / 10_000 * 10_000 - 20_000;
    st.push(&one_val(e, m, 1.0), base, 1000, &d);
    let errs = crate::store::CTR.write_errors.load(Ordering::Relaxed);
    // make the next write fail: /dev/full accepts the open and fails every write with ENOSPC
    st.break_current_segment_for_test("/dev/full");
    st.push(&one_val(e, m, 2.0), base + 1000, 1000, &d);
    assert!(crate::store::CTR.write_errors.load(Ordering::Relaxed) > errs, "write errors are counted");
}

#[test]
fn s02_heavy_requests_are_bounded_and_get_busy() {
    let (root, mut col) = world("s02");
    tick(&mut col, 1);
    let ctx = ctx_with(&root, &mut col, 4_000_000);
    let a = ctx.heavy_slot().unwrap();
    let b = ctx.heavy_slot().unwrap();
    for req in [r#"{"req":"series","entity":"cg:/","metric":"mem.current","tier":"hot","window_s":5}"#, r#"{"req":"events","window_s":5}"#] {
        let r = j(&handle(&ctx, req));
        assert_eq!((r["ok"].clone(), r["error"]["code"].clone()), (Value::Bool(false), Value::String("busy".into())));
    }
    assert_eq!(j(&handle(&ctx, r#"{"req":"current"}"#))["ok"], true, "the hot path is never gated");
    drop(a);
    drop(b);
    assert_ne!(j(&handle(&ctx, r#"{"req":"events","window_s":5}"#))["error"]["code"], "busy");
}

#[test]
fn s02_a_panic_while_serving_is_an_error_reply_not_a_dead_daemon() {
    let (root, mut col) = world("s02p");
    tick(&mut col, 1);
    let ctx = ctx_with(&root, &mut col, 4_000_000);
    let r = j(&handle(&ctx, r#"{"req":"__panic"}"#));
    assert_eq!(r["error"]["code"], "internal");
    assert_eq!(j(&handle(&ctx, r#"{"req":"status"}"#))["ok"], true, "the context is still usable (no poisoned lock)");
}

#[test]
fn s02_a_corrupt_compressed_frame_is_counted_not_fatal() {
    let root = tmp("s02c");
    let cfg = cfg_for(&root);
    let mut d = Dict::default();
    let (e, m) = (d.ent("cg:/x", 0), d.met("g.metric", false));
    let mut st = Store::open(&cfg, 0, 0).unwrap();
    let base = wall_ms() / 1000 * 1000 - 20_000;
    for i in 0..4 {
        st.push(&one_val(e, m, 1.0), base + i * 1000, 1000, &d);
    }
    st.sync_dict(&mut d);
    // a frame with a valid checksum whose zstd payload is garbage
    let cold = seg_files(&root.join("cold"), "cold-");
    let mut f = fs::OpenOptions::new().append(true).open(&cold[0]).unwrap();
    f.write_all(&crate::store::encode_frame(base + 1, b"definitely not zstd")).unwrap();
    drop(f);
    let pts = query(&cfg, "cg:/x", "g.metric", "cold");
    assert_eq!(pts["corrupt_frames"], 1);
}

#[test]
fn s03_the_writer_queue_never_blocks_drops_oldest_and_keeps_dictionary_and_events() {
    let sh = crate::writer::Shared::new(3, 1 << 30);
    for i in 0..6u64 {
        let mut dd = Dict::default();
        dd.met(&format!("m{i}"), false);
        let job = crate::writer::Job {
            ts_ms: 1000 + i,
            dt_ms: 1000,
            frame: Arc::new(Vec::new()),
            events: vec![format!("ev{i}")],
            delta: crate::dict::Delta { mets: vec![(format!("m{i}"), false)], ents: vec![] },
        };
        sh.submit(job); // nothing consumes: a stalled disk
    }
    assert_eq!(sh.queue_len(), 3);
    assert_eq!(sh.dropped_frames.load(Ordering::SeqCst), 3, "the dropped frames are counted");
    assert_eq!(sh.lag_ms(), 1005, "and the lag is reported (nothing written yet)");
    sh.close();
    let mut seen_mets = Vec::new();
    let mut seen_events = Vec::new();
    // drain through a real store: ids and events of dropped jobs arrive with the next job
    let root = tmp("s03q");
    let cfg = cfg_for(&root);
    let mut st = Store::open(&cfg, 0, 0).unwrap();
    let mut mirror = Dict::default();
    while let Some(job) = sh.next() {
        seen_mets.extend(job.delta.mets.iter().map(|x| x.0.clone()));
        seen_events.extend(job.events.clone());
        crate::writer::process(&mut st, &mut mirror, &job);
    }
    assert_eq!(seen_mets, vec!["m0", "m1", "m2", "m3", "m4", "m5"], "no dictionary id is lost with a dropped frame");
    assert_eq!(seen_events, vec!["ev0", "ev1", "ev2", "ev3", "ev4", "ev5"], "no event is lost either");
    assert_eq!(mirror.mets.len(), 6);
}

#[test]
fn s03_a_cmdline_read_that_blocks_does_not_stall_the_tick() {
    extern "C" {
        fn mkfifo(path: *const std::os::raw::c_char, mode: u32) -> i32;
    }
    let (root, mut col) = world("s03c");
    col.procs.sync_cmdline = false;
    proc_files(&root, 400, "stuck", "D", 0, 0, 0, "/system.slice/db.service");
    let fifo = root.join("proc/400/cmdline");
    fs::remove_file(&fifo).unwrap();
    let c = std::ffi::CString::new(fifo.to_str().unwrap()).unwrap();
    assert_eq!(unsafe { mkfifo(c.as_ptr(), 0o600) }, 0);
    // a hang here is the bug: the tick would block in open(2) on the fifo
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        tick(&mut col, 1);
        tick(&mut col, 2);
        tick(&mut col, 4);
        tx.send(col).unwrap();
    });
    let mut col = rx.recv_timeout(Duration::from_secs(60)).expect("sampling must not wait for cmdline");
    let p = col.procs.out.iter().find(|p| p.pid == 400).unwrap();
    assert_eq!(p.cmdline, "");
    assert_eq!(p.cmd_state.st, stc::UN_TIMEOUT, "unanswered for more than the deadline: shown as a timeout");
    assert_eq!(col.procs.cmd_timeouts, 1);
    // the stall ends: the answer arrives and replaces the unavailable state
    let mut w = fs::OpenOptions::new().write(true).open(&fifo).unwrap();
    w.write_all(b"stuck\0--late\0").unwrap();
    drop(w);
    let mut n = 5;
    loop {
        tick(&mut col, n);
        if col.procs.out.iter().any(|p| p.pid == 400 && p.cmdline == "stuck --late") {
            break;
        }
        assert!(n < 400, "the late answer never arrived");
        n += 1;
        std::thread::sleep(Duration::from_millis(25));
    }
    let p = col.procs.out.iter().find(|p| p.pid == 400).unwrap();
    assert_eq!(p.cmd_state.st, stc::VALUE);
}

#[test]
fn s05_one_oom_kill_is_one_event_on_the_owning_cgroup() {
    for with_local in [false, true] {
        let root = tmp("s05");
        mk_proc(&root);
        for (rel, v) in [("cg", 0), ("cg/a", 0), ("cg/a/b", 0)] {
            put(&root, &format!("{rel}/memory.events"), &format!("oom_kill {v}\n"));
            if with_local {
                put(&root, &format!("{rel}/memory.events.local"), "oom_kill 0\n");
            }
        }
        let mut col = Collector::new(cfg_for(&root), Dict::default(), Instant::now());
        tick(&mut col, 1);
        // a kill inside a/b: the hierarchical counter rises in b, a and the root
        for rel in ["cg", "cg/a", "cg/a/b"] {
            put(&root, &format!("{rel}/memory.events"), "oom_kill 1\n");
        }
        if with_local {
            put(&root, "cg/a/b/memory.events.local", "oom_kill 1\n");
        }
        tick(&mut col, 2);
        let ooms: Vec<Value> = col.events.iter().map(|e| j(&e.json)).filter(|v| v["type"] == "oom_kill").collect();
        assert_eq!(ooms.len(), 1, "with_local={with_local}: {ooms:?}");
        assert_eq!(ooms[0]["entity"], "cg:/a/b");
        assert_eq!(ooms[0]["detail"]["delta"], 1);
    }
}

#[test]
fn s06_throttle_is_read_from_the_slice_that_carries_the_limits() {
    let root = tmp("s06");
    mk_proc(&root);
    put(&root, "proc/self/cgroup", "0::/system.slice/topos.service\n");
    put(&root, "cg/system.slice/cpu.max", "25000 100000\n");
    put(&root, "cg/system.slice/cpu.stat", "usage_usec 1\nnr_throttled 7\nthrottled_usec 900\n");
    put(&root, "cg/system.slice/memory.high", "100000000\n");
    put(&root, "cg/system.slice/memory.events", "low 0\nhigh 3\nmax 1\n");
    put(&root, "cg/system.slice/topos.service/cpu.max", "max 100000\n");
    put(&root, "cg/system.slice/topos.service/cpu.stat", "usage_usec 1\nnr_throttled 0\nthrottled_usec 0\n");
    put(&root, "cg/system.slice/topos.service/memory.events", "low 0\nhigh 0\nmax 0\n");
    let mut mon = crate::selfmon::SelfMon::new();
    let s = mon.sample(&cfg_for(&root), 1000, 100, 4096);
    assert_eq!(s.throttle[0].v, 7.0, "the slice's nr_throttled, not the child's 0");
    assert_eq!(s.throttle[1].v, 900.0);
    assert_eq!(s.throttle[2].v, 3.0);
    assert_eq!(s.throttle[3].v, 1.0);
    assert_eq!(s.throttle_src, [Some("/system.slice".to_string()), Some("/system.slice".to_string())], "and it says which cgroup it read");
}

#[test]
fn s08_run_times_are_only_collected_when_a_summary_is_wanted() {
    let mut v = Vec::new();
    for i in 0..1000 {
        crate::record_work(&mut v, false, i);
    }
    assert!(v.is_empty(), "a 24/7 daemon without stats_path must not grow a vector");
    crate::record_work(&mut v, true, 5);
    assert_eq!(v, vec![5]);
}

#[test]
fn s09_dictionary_is_durable_before_frames_and_names_stay_one_line() {
    let root = tmp("s09");
    let cfg = cfg_for(&root);
    let mut owner = Dict::default();
    let (e, m) = (owner.ent("cg:/x", 0), owner.met("g.metric", false));
    let e2 = owner.ent("cg:/bad\nname", 0);
    assert_eq!(owner.ents[e2 as usize], "cg:/bad?name", "a newline can not be stored: replaced at insert time");
    assert_eq!(owner.ent("cg:/bad?name", 1), e2, "so two names can not collapse into one line after a reload");
    let mut st = Store::open(&cfg, 0, 0).unwrap();
    let mut mirror = Dict::default();
    let base = wall_ms() / 1000 * 1000 - 5000;
    let job = |d: &mut Dict, ts| crate::writer::Job { ts_ms: ts, dt_ms: 1000, frame: Arc::new(one_val(e, m, 3.0)), events: vec![], delta: d.take_delta() };
    // dictionary cannot be written: the frames that reference its ids must not be either
    fs::create_dir_all(root.join("cold/mets.dict")).unwrap();
    let skipped = crate::store::CTR.frames_skipped.load(Ordering::Relaxed);
    crate::writer::process(&mut st, &mut mirror, &job(&mut owner, base));
    assert!(crate::store::CTR.frames_skipped.load(Ordering::Relaxed) > skipped);
    assert!(seg_files(&root.join("tm"), "hot-").is_empty(), "no frame was written behind an undurable dictionary");
    // repaired: the next job carries a fresh delta and now both files and frames exist
    fs::remove_dir_all(root.join("cold/mets.dict")).unwrap();
    owner.met("second", true);
    let mut mirror2 = Dict::default();
    let mut st2 = Store::open(&cfg, 0, 0).unwrap();
    let mut full = crate::dict::Delta::default();
    full.mets = owner.mets.clone();
    full.ents = owner.ents.iter().enumerate().map(|(i, n)| (i as u32, n.clone())).collect();
    crate::writer::process(&mut st2, &mut mirror2, &crate::writer::Job { ts_ms: base + 1000, dt_ms: 1000, frame: Arc::new(one_val(e, m, 4.0)), events: vec![], delta: full });
    let (mets, ents) = crate::dict::read_lists(&format!("{}/cold", root.display()));
    assert_eq!(mets.len(), owner.mets.len());
    assert!(ents.contains(&"cg:/x".to_string()));
    assert!(!seg_files(&root.join("tm"), "hot-").is_empty());
    // a torn last dictionary line is ignored by readers and cut back by the next open
    fs::write(root.join("cold/mets.dict"), "G a\nC b\nG tor").unwrap();
    assert_eq!(crate::dict::read_lists(&format!("{}/cold", root.display())).0.len(), 2);
    let _st3 = Store::open(&cfg, 2, 0).unwrap();
    // the open also gives a dictionary written before the count header existed its header
    assert_eq!(fs::read_to_string(root.join("cold/mets.dict")).unwrap(), format!("{}G a\nC b\n", crate::dict::header(2)));
    assert_eq!(crate::dict::read_lists(&format!("{}/cold", root.display())).0.len(), 2);
}

#[test]
fn s10_threads_count_for_d_state_and_a_hidden_d_thread_is_reported_then_found() {
    let (root, mut col) = world("s10");
    proc_files(&root, 500, "game", "S", 0, 0, 0, "/system.slice/a.service");
    put(&root, "proc/500/stat", &pstat(500, "game", "S", 0, 0, 0, 3, 77, 10, 0));
    let task = |tid: u32, state: &str| {
        put(&root, &format!("proc/500/task/{tid}/stat"), &pstat(tid, "w", state, 0, 0, 0, 3, 77, 10, 0));
        put(&root, &format!("proc/500/task/{tid}/schedstat"), "0 1000000 1\n");
    };
    for t in [500, 501, 502] {
        task(t, "S");
    }
    let host_stat = |blocked: u32| format!("cpu  100 0 50 800 10 5 5 0 0 0\ncpu0 50 0 25 400 5 2 3 0 0 0\nctxt 1000\nprocesses 500\nprocs_running 2\nprocs_blocked {blocked}\n");
    put(&root, "proc/stat", &host_stat(0));
    tick(&mut col, 1);
    assert_eq!(col.procs.dstate, 0);
    // tick 2 is not a sweep and the process burns no CPU: a worker enters D unseen
    task(501, "D");
    put(&root, "proc/stat", &host_stat(1));
    tick(&mut col, 2);
    assert_eq!(col.procs.dstate, 0, "the cheap pass sees only the leader");
    assert_eq!(col.procs.d_gap, 1, "host.procs_blocked counts it: the gap is published");
    tick(&mut col, 3);
    assert_eq!(col.procs.dstate, 1, "while tasks are unaccounted for, multi-threaded processes are scanned in a budgeted slice");
    let p = col.procs.out.iter().find(|p| p.pid == 500).expect("the process with a D thread is in the union");
    assert_eq!((p.why, p.d_tasks, p.state), ("D", 1, 'S'));
    assert_eq!(col.procs.dmap.get("/system.slice/a.service"), Some(&1));
}

#[test]
fn s11_orphans_are_removed_and_the_cap_counts_the_whole_directory() {
    crate::store::clear_startup_notes();
    let root = tmp("s11");
    let cfg = cfg_for(&root);
    fs::create_dir_all(root.join("cold")).unwrap();
    fs::create_dir_all(root.join("tm")).unwrap();
    // an orphan segment of the sampler (frame magic) is removed ...
    put(&root, "cold/oldtier-123.seg", "TSF1 orphan frame bytes");
    put(&root, "tm/hot-999.seg", "wrong medium dir? no: a configured tier file stays");
    let _ = Store::open(&cfg, 0, 0).unwrap();
    assert!(!root.join("cold/oldtier-123.seg").exists());
    // ... a configured tier's file in the wrong directory is an orphan too
    put(&root, "cold/hot-5.seg", "TSF1x");
    let _ = Store::open(&cfg, 0, 0).unwrap();
    assert!(!root.join("cold/hot-5.seg").exists());
    // X-09: but a file that merely has a segment-like name is somebody else's: kept and reported
    put(&root, "cold/photo-7.seg", "not a topos segment");
    let _ = Store::open(&cfg, 0, 0).unwrap();
    assert!(root.join("cold/photo-7.seg").exists(), "foreign file left alone");
    assert!(crate::store::startup_notes().iter().any(|n| n.contains("photo-7.seg")), "and reported");
    // cap 1500 with 1400 bytes of dictionary: segments must leave room for it
    let tiers = "[[tier]]\nname = \"cold\"\nstep_s = 1\nkeep_s = 86400\nsegment_s = 3600\nmedium = \"disk\"\n";
    let root = tmp("s11b");
    let cfg = Config::parse(&cfg_text(&root, 4_000_000, tiers, 1500, 3)).unwrap();
    fs::create_dir_all(root.join("cold")).unwrap();
    put(&root, "cold/mets.dict", &"G x\n".repeat(350));
    let mut st = Store::open(&cfg, 350, 0).unwrap();
    let mut d = Dict::default();
    let (e, m) = (d.ent("cg:/x", 0), d.met("g", false));
    let base = wall_ms() - 100_000;
    for i in 0..40u64 {
        st.push(&one_val(e, m, i as f64 * 1.37), base + i * 1000, 1000, &d);
        assert!(st.sizes().1 + st.other_bytes() <= 1500, "frame {i}: {} + {}", st.sizes().1, st.other_bytes());
    }
}

#[test]
fn s12_a_cgroup_recreated_at_the_same_path_is_a_new_incarnation() {
    let (root, mut col) = world("s12");
    tick(&mut col, 1);
    // a new directory that exists at the same time as the old one has another inode number
    let fresh = root.join("cg/zz-fresh");
    fs::create_dir_all(&fresh).unwrap();
    let old = root.join("cg/besteffort.slice");
    fs::remove_dir_all(&old).unwrap();
    fs::rename(&fresh, &old).unwrap();
    tick(&mut col, 2);
    let n = col.events.iter().filter(|e| e.json.contains("cgroup_vanished") && e.json.contains("recreated") && e.json.contains("cg:/besteffort.slice")).count();
    assert_eq!(n, 1, "{:?}", col.events.iter().map(|e| e.json.clone()).collect::<Vec<_>>());
}

#[test]
fn s14_events_survive_torn_lines_bad_utf8_and_psi_has_hysteresis() {
    let (root, mut col) = world("s14");
    let ts = wall_ms();
    let good = format!("{{\"ts_ms\":{ts},\"mono_ms\":1,\"type\":\"oom_kill\",\"entity\":\"cg:/a\",\"detail\":{{}}}}\n");
    let mut bytes = good.clone().into_bytes();
    bytes.extend_from_slice(b"\xff\xfe\xfd not json\n");
    bytes.extend_from_slice(good.as_bytes());
    bytes.extend_from_slice(b"{\"ts_ms\":1,\"type\":\"torn");
    fs::write(root.join("ev.jsonl"), &bytes).unwrap();
    tick(&mut col, 1);
    let ctx = ctx_with(&root, &mut col, 4_000_000);
    let r = j(&handle(&ctx, r#"{"req":"events","window_s":60}"#));
    assert_eq!(r["events"].as_array().unwrap().len(), 2, "invalid UTF-8 and a torn tail do not hide the other events");
    let mut st = Store::open(&ctx.cfg, 0, 0).unwrap();
    st.append_events(&["{\"ts_ms\":1}".to_string()]);
    let text = fs::read(root.join("ev.jsonl")).unwrap();
    assert!(text.ends_with(b"torn\n{\"ts_ms\":1}\n"), "the torn line was ended, the new event did not merge with it");
    // PSI oscillating around the threshold: one `up`, no flapping
    let root2 = tmp("s14p");
    mk_proc(&root2);
    put(&root2, "cg/cpu.pressure", "some avg10=1.00 avg60=0.00 avg300=0.00 total=1\n");
    let mut col2 = Collector::new(cfg_for(&root2), Dict::default(), Instant::now());
    tick(&mut col2, 1);
    let mut crossings = 0;
    for (n, v) in [(2, 26.0), (3, 24.0), (4, 26.0), (5, 24.5), (6, 10.0)] {
        put(&root2, "cg/cpu.pressure", &format!("some avg10={v:.2} avg60=0.00 avg300=0.00 total=1\n"));
        tick(&mut col2, n);
        crossings += col2.events.iter().filter(|e| e.json.contains("psi_cross")).count();
    }
    assert_eq!(crossings, 2, "up at 26, down only below 80% of 25 (at 10)");
}

#[test]
fn s15_a_second_instance_is_refused_the_socket() {
    let (root, mut col) = world("s15");
    tick(&mut col, 1);
    let a = ctx_with(&root, &mut col, 4_000_000);
    let sock = a.cfg.sampler.socket_path.clone();
    crate::serve::spawn(a.clone(), crate::fsafe::Instance::acquire(&sock).unwrap()).unwrap();
    let e = crate::fsafe::Instance::acquire(&sock).unwrap_err();
    assert_eq!(e.code, "instance_running");
    let (mut s, mut r) = connect(&a.cfg.sampler.socket_path);
    assert_eq!(rpc(&mut s, &mut r, r#"{"req":"hello"}"#)["ok"], true, "the first instance keeps its socket");
    assert_eq!(fs::metadata(&a.cfg.sampler.socket_path).unwrap().permissions().mode() & 0o777, 0o600);
}

#[test]
fn s16_files_are_created_private() {
    let root = tmp("s16");
    let cfg = cfg_for(&root);
    let mut d = Dict::default();
    let (e, m) = (d.ent("cg:/x", 0), d.met("g", false));
    let mut st = Store::open(&cfg, 0, 0).unwrap();
    let base = wall_ms() / 1000 * 1000 - 3000;
    st.push(&one_val(e, m, 1.0), base, 1000, &d);
    st.sync_dict(&mut d);
    for p in seg_files(&root.join("tm"), "hot-").into_iter().chain(seg_files(&root.join("cold"), "cold-")).chain([root.join("cold/mets.dict"), root.join("cold/ents.dict"), root.join("ev.jsonl")]) {
        assert_eq!(fs::metadata(&p).unwrap().permissions().mode() & 0o777, 0o600, "{}", p.display());
    }
}

#[test]
fn s17_p95_of_large_gauges_is_exact_and_never_above_max() {
    let root = tmp("s17");
    let cfg = cfg_for(&root);
    let mut d = Dict::default();
    let (e, m) = (d.ent("cg:/x", 0), d.met("mem.big", false));
    let mut st = Store::open(&cfg, 0, 0).unwrap();
    let base = wall_ms() / 1000 * 1000 - 15_000;
    let big = (1u64 << 24) as f64;
    for i in 0..4u64 {
        st.push(&one_val(e, m, big + 1.0 + 2.0 * i as f64), base + i * 1000, 1000, &d);
    }
    st.sync_dict(&mut d);
    let warm = query(&cfg, "cg:/x", "mem.big", "warm");
    let w = &warm["points"].as_array().unwrap()[0];
    assert!(w[4].as_f64().unwrap() <= w[2].as_f64().unwrap(), "p95 {} <= max {}", w[4], w[2]);
    assert_eq!(w[2].as_f64().unwrap(), big + 3.0);
}

#[test]
fn s19_a_huge_window_does_not_wrap() {
    let (root, mut col) = world("s19");
    tick(&mut col, 1);
    let ctx = ctx_with(&root, &mut col, 4_000_000);
    let mut st = Store::open(&ctx.cfg, 0, 0).unwrap();
    st.append_events(&[mk_event(Instant::now(), "oom_kill", "cg:/a", "{}").json]);
    let r = j(&handle(&ctx, r#"{"req":"events","window_s":18446744073709551615}"#));
    assert_eq!(r["events"].as_array().unwrap().len(), 1);
}

#[test]
fn slurp_reads_until_eof_because_seq_files_answer_in_chunks() {
    extern "C" {
        fn mkfifo(path: *const std::os::raw::c_char, mode: u32) -> i32;
    }
    let root = tmp("slurp");
    let fifo = root.join("vmstat");
    let c = std::ffi::CString::new(fifo.to_str().unwrap()).unwrap();
    assert_eq!(unsafe { mkfifo(c.as_ptr(), 0o600) }, 0);
    let p = fifo.clone();
    let w = std::thread::spawn(move || {
        let mut f = fs::OpenOptions::new().write(true).open(&p).unwrap();
        f.write_all(b"pswpin 1\n").unwrap();
        f.flush().unwrap();
        std::thread::sleep(Duration::from_millis(300)); // a second chunk, like seq_file's next buffer
        f.write_all(b"zswpin 2\n").unwrap();
    });
    let mut s = Slurp::new();
    let got = s.read(fifo.to_str().unwrap()).unwrap().to_vec();
    w.join().unwrap();
    assert_eq!(String::from_utf8(got).unwrap(), "pswpin 1\nzswpin 2\n", "a short first read is not the end of the file");
}

#[test]
fn vmstat_keys_the_kernel_lacks_are_typed_states_not_missing() {
    let (root, mut col) = world("vmkeys");
    put(&root, "proc/vmstat", "pswpin 5\nzswpin 7\npgmajfault 10\n");
    tick(&mut col, 1);
    assert_eq!(get(&col, "host", "vm.zswpin").unwrap().raw, 7);
    for k in ["vm.zswpout", "vm.zswpwb", "vm.pgscan_direct", "vm.oom_kill", "vm.workingset_activate_file"] {
        assert_eq!(get(&col, "host", k).unwrap().st, stc::NA_FEATURE, "{k}");
    }
    fs::remove_file(root.join("proc/vmstat")).unwrap();
    tick(&mut col, 2);
    assert_eq!(get(&col, "host", "vm.zswpin").unwrap().st, stc::UN_READ, "an unreadable file is unavailable");
}

#[test]
fn view_additions_pids_max_swap_current_and_root_memory_from_meminfo() {
    let root = tmp("views");
    mk_proc(&root);
    put(&root, "proc/meminfo", "MemTotal: 1000 kB\nMemAvailable: 400 kB\nSwapTotal: 500 kB\nSwapFree: 300 kB\nMemFree: 100 kB\n");
    put(&root, "cg/a/memory.current", "4096\n");
    put(&root, "cg/a/memory.swap.current", "8192\n");
    put(&root, "cg/a/pids.max", "max\n");
    put(&root, "cg/b/pids.max", "128\n");
    let mut col = Collector::new(cfg_for(&root), Dict::default(), Instant::now());
    tick(&mut col, 1);
    assert_eq!(get(&col, "cg:/a", "mem.swap_current").unwrap().v, 8192.0);
    assert_eq!(sticky(&col, "cg:/a", "limit.pids.max").unwrap().v, -1.0);
    assert_eq!(sticky(&col, "cg:/b", "limit.pids.max").unwrap().v, 128.0);
    assert_eq!(get(&col, "cg:/", "mem.current").unwrap().v, 600.0 * 1024.0, "root RAM = MemTotal - MemAvailable");
    assert_eq!(get(&col, "cg:/", "mem.swap_current").unwrap().v, 200.0 * 1024.0);
}

#[test]
fn current_fields_projection_and_compact_form() {
    let (root, mut col) = world("proj");
    tick(&mut col, 1);
    put(&root, "cg/cpu.stat", "usage_usec 10000500\nuser_usec 1\nsystem_usec 1\nnr_periods 1\nnr_throttled 0\nthrottled_usec 0\n");
    tick(&mut col, 2);
    let ctx = ctx_with(&root, &mut col, 4_000_000);
    let full = handle(&ctx, r#"{"req":"current","procs":false}"#);
    let fp = handle(&ctx, r#"{"req":"current","procs":false,"fields":["mem.current","cpu.usage_usec","limit.*"]}"#);
    let compact = handle(&ctx, r#"{"req":"current","procs":false,"compact":true,"fields":["mem.current","cpu.usage_usec"]}"#);
    assert!(fp.len() < full.len() / 3 && compact.len() < fp.len(), "{} {} {}", full.len(), fp.len(), compact.len());
    let f = j(&fp);
    let r = f["entities"]["cg:/"].as_object().unwrap();
    assert!(r.keys().all(|k| k == "mem.current" || k == "cpu.usage_usec" || k.starts_with("limit.")), "{:?}", r.keys().collect::<Vec<_>>());
    assert!(r["cpu.usage_usec"]["raw"].is_number(), "projection alone keeps the full value objects");
    let c = j(&compact);
    let root_ent = &c["entities"]["cg:/"];
    assert_eq!(root_ent["mem.current"], 4_096_000_000.0);
    assert_eq!(root_ent["cpu.usage_usec"][0], 500.0, "counters are [delta, rate]");
    assert!(!compact.contains("\"raw\""));
    assert!(j(&full)["entities"]["cg:/"]["cpu.usage_usec"]["raw"].is_number(), "default behaviour is unchanged");
}

#[test]
fn s20_memory_stat_counters_are_classified_explicitly() {
    for k in ["swpin_zero", "swpout_zero", "numa_pages_migrated", "numa_pte_updates", "numa_hint_faults", "pgfault", "thp_fault_alloc"] {
        assert!(crate::collect::is_mem_counter_for_test(k), "{k}");
    }
    assert!(!crate::collect::is_mem_counter_for_test("anon"));
    assert!(!crate::collect::is_mem_counter_for_test("file_dirty"));
}

// ================================================================ S2-review fixes (X-01 .. X-15)

use crate::fsafe::{self, Instance};
use std::os::unix::fs::{symlink, MetadataExt};

extern "C" {
    fn mkfifo(path: *const std::os::raw::c_char, mode: u32) -> i32;
    fn chown(path: *const std::os::raw::c_char, uid: u32, gid: u32) -> i32;
}

fn cpath(p: &Path) -> std::ffi::CString {
    std::ffi::CString::new(p.to_str().unwrap()).unwrap()
}

/// Every file below the given directories: relative path -> bytes.
fn snapshot(dirs: &[&Path]) -> std::collections::BTreeMap<String, Vec<u8>> {
    let mut m = std::collections::BTreeMap::new();
    for d in dirs {
        if let Ok(rd) = fs::read_dir(d) {
            for e in rd.flatten() {
                if e.file_type().map(|t| t.is_file()).unwrap_or(false) {
                    m.insert(e.path().display().to_string(), fs::read(e.path()).unwrap());
                }
            }
        }
    }
    m
}

#[test]
fn x01_a_planted_symlink_in_a_data_directory_is_refused_and_its_target_is_never_touched() {
    for (dir, name) in [("tm", "hot-0.seg"), ("cold", "cold-0.seg"), ("cold", "mets.dict"), ("cold", "ents.dict"), ("cold", "ents.dict.tmp"), ("w", "ev.jsonl")] {
        let root = tmp("x01a");
        let cfg = cfg_for(&root);
        fs::create_dir_all(root.join("tm")).unwrap();
        fs::create_dir_all(root.join("cold")).unwrap();
        let victim = root.join("victim");
        fs::write(&victim, "precious bytes that must survive").unwrap();
        let at = if dir == "w" { root.clone() } else { root.join(dir) };
        symlink(&victim, at.join(name)).unwrap();
        let e = Store::open(&cfg, 0, 0).err().unwrap_or_else(|| panic!("{dir}/{name}: start must be refused"));
        assert_eq!(e.code, "symlink_in_data_dir", "{dir}/{name}: {}", e.msg);
        assert!(e.msg.contains(name), "the refusal names the link: {}", e.msg);
        assert_eq!(fs::read(&victim).unwrap(), b"precious bytes that must survive", "{dir}/{name}: target untouched");
    }
}

#[test]
fn x01_a_link_planted_while_running_is_not_followed_by_dictionary_or_segment_writes() {
    let root = tmp("x01r");
    let cfg = cfg_for(&root);
    let mut st = Store::open(&cfg, 0, 0).unwrap();
    let victim = root.join("victim");
    // a segment name that becomes a link before the writer rolls over to it
    let mut dseg = Dict::default();
    let (e, m) = (dseg.ent("cg:/x", 0), dseg.met("g", false));
    let a = wall_ms() / 1000 / 10 * 10 - 100; // aligned to the 10 s hot segment
    fs::write(&victim, "precious").unwrap();
    st.push(&one_val(e, m, 1.0), a * 1000, 1000, &dseg);
    symlink(&victim, root.join(format!("tm/hot-{}.seg", a + 10))).unwrap();
    let errs = crate::store::CTR.write_errors.load(Ordering::Relaxed);
    st.push(&one_val(e, m, 2.0), (a + 10) * 1000, 1000, &dseg);
    assert!(crate::store::CTR.write_errors.load(Ordering::Relaxed) > errs, "the open of the linked segment failed and was counted");
    assert_eq!(fs::read(&victim).unwrap(), b"precious", "a segment was not written through the link");
    fs::write(&victim, "precious").unwrap();
    // the dictionary append: mets.dict replaced by a link after start
    let mut d = Dict::default();
    d.met("m1", false);
    symlink(&victim, root.join("cold/mets.dict")).unwrap();
    let errs = crate::store::CTR.write_errors.load(Ordering::Relaxed);
    assert!(!st.sync_dict(&mut d), "the dictionary could not be made durable");
    assert!(crate::store::CTR.write_errors.load(Ordering::Relaxed) > errs);
    assert_eq!(fs::read(&victim).unwrap(), b"precious", "append did not go through the link");
    // the tmp file of the atomic rewrite: a link planted at ents.dict.tmp is replaced, not written through
    let mut d2 = Dict::default();
    d2.ent("cg:/a", 0);
    d2.gc(1_000_000, 10); // frees the slot -> a full rewrite
    d2.ent("cg:/b", 1_000_000);
    assert!(d2.ents_dirty);
    symlink(&victim, root.join("cold/ents.dict.tmp")).unwrap();
    assert!(st.sync_dict(&mut d2));
    assert_eq!(fs::read(&victim).unwrap(), b"precious", "rewrite did not go through the link");
    assert!(fs::symlink_metadata(root.join("cold/ents.dict")).unwrap().file_type().is_file());
    assert!(fs::symlink_metadata(root.join("cold/ents.dict.tmp")).is_err(), "the tmp name was renamed into place");
}

#[test]
fn x01_data_directories_must_be_real_private_directories_with_a_safe_parent() {
    use std::os::unix::fs::PermissionsExt;
    let root = tmp("x01d");
    let set = |p: &Path, m: u32| fs::set_permissions(p, fs::Permissions::from_mode(m)).unwrap();
    // a missing leaf is created private
    let ok = fsafe::open_checked(root.join("fresh").to_str().unwrap(), "t").unwrap();
    assert_eq!(fs::metadata(&ok.path).unwrap().mode() & 0o777, 0o700);
    // world- and group-writable leaves
    for (name, mode) in [("ww", 0o777), ("gw", 0o770), ("ow", 0o702)] {
        let p = root.join(name);
        fs::create_dir(&p).unwrap();
        set(&p, mode);
        assert_eq!(fsafe::open_checked(p.to_str().unwrap(), "t").unwrap_err().code, "dir_unsafe", "{name}");
    }
    // a symlink to a perfectly good directory is still refused
    fs::create_dir(root.join("real")).unwrap();
    set(&root.join("real"), 0o700);
    symlink(root.join("real"), root.join("link")).unwrap();
    assert_eq!(fsafe::open_checked(root.join("link").to_str().unwrap(), "t").unwrap_err().code, "dir_symlink");
    // a file is not a directory; a missing parent is not created
    fs::write(root.join("afile"), "x").unwrap();
    assert_eq!(fsafe::open_checked(root.join("afile").to_str().unwrap(), "t").unwrap_err().code, "dir_not_directory");
    assert_eq!(fsafe::open_checked(root.join("nope/inner").to_str().unwrap(), "t").unwrap_err().code, "dir_missing");
    assert!(!root.join("nope").exists());
    // the parent: group/world-writable (even when it is ours) is refused, and nothing is created in it
    fs::create_dir(root.join("openparent")).unwrap();
    set(&root.join("openparent"), 0o777);
    let e = fsafe::open_checked(root.join("openparent/inner").to_str().unwrap(), "t").unwrap_err();
    assert_eq!(e.code, "dir_parent_unsafe", "{}", e.msg);
    assert!(!root.join("openparent/inner").exists());
    // /tmp itself: root-owned but sticky and world-writable
    if fs::metadata("/tmp").map(|m| m.mode() & 0o1002 == 0o1002).unwrap_or(false) {
        let p = format!("/tmp/topos-x01-must-not-be-created-{}", std::process::id());
        assert_eq!(fsafe::open_checked(&p, "t").unwrap_err().code, "dir_parent_unsafe");
        assert!(!Path::new(&p).exists());
    }
    // a directory owned by somebody else
    if fsafe::euid() == 0 {
        let p = root.join("foreign");
        fs::create_dir(&p).unwrap();
        set(&p, 0o700);
        assert_eq!(unsafe { chown(cpath(&p).as_ptr(), 4242, 4242) }, 0);
        let e = fsafe::open_checked(p.to_str().unwrap(), "t").unwrap_err();
        assert_eq!(e.code, "dir_unsafe");
        assert!(e.msg.contains("4242"), "{}", e.msg);
        // ... and a foreign parent that is not root-owned
        let q = root.join("foreign/inner");
        let e = fsafe::open_checked(q.to_str().unwrap(), "t").unwrap_err();
        assert_eq!(e.code, "dir_parent_unsafe");
    }
}

#[test]
fn x01_files_are_opened_nofollow_and_only_when_regular() {
    let root = tmp("x01f");
    fs::write(root.join("target"), "t").unwrap();
    symlink(root.join("target"), root.join("ln")).unwrap();
    assert_eq!(unsafe { mkfifo(cpath(&root.join("ff")).as_ptr(), 0o600) }, 0);
    let d = fsafe::Dir::open_plain(root.to_str().unwrap()).unwrap();
    for flags in [fsafe::O_RDONLY, fsafe::O_WRONLY | fsafe::O_APPEND, fsafe::O_WRONLY | fsafe::O_CREAT] {
        assert_eq!(d.open_file("ln", flags, 0o600).unwrap_err().raw_os_error(), Some(40), "ELOOP: the link is not followed ({flags})");
    }
    assert_eq!(fs::read(root.join("target")).unwrap(), b"t");
    // a FIFO under a data name neither blocks the open nor is it accepted
    assert!(d.open_file("ff", fsafe::O_RDONLY, 0).is_err());
    assert!(d.open_file("ff", fsafe::O_WRONLY, 0).is_err());
    // names are single components
    assert!(d.open_file("../x", fsafe::O_RDONLY, 0).is_err());
    assert!(d.open_file("a/b", fsafe::O_RDONLY, 0).is_err());
    // unlink removes a link itself, never its target
    d.unlink("ln").unwrap();
    assert!(!root.join("ln").exists() && root.join("target").exists());
    // a symlinked directory is not opened either
    symlink(&root, std::env::temp_dir().join(format!("tsp-dirlink-{}", std::process::id()))).unwrap();
    let l = std::env::temp_dir().join(format!("tsp-dirlink-{}", std::process::id()));
    assert!(fsafe::Dir::open_plain(l.to_str().unwrap()).is_err());
    let _ = fs::remove_file(l);
}

#[test]
fn x02_a_second_instance_exits_having_changed_nothing() {
    let root = tmp("x02");
    let cfg = cfg_for(&root);
    let first = crate::store::start(&cfg).unwrap();
    let mut st = Store::open_with(&cfg, &first.dirs, 0, 0).unwrap();
    let mut d = Dict::default();
    let (e, m) = (d.ent("cg:/x", 0), d.met("g.metric", false));
    let base = wall_ms() / 1000 * 1000 - 20_000;
    for i in 0..5 {
        st.push(&one_val(e, m, i as f64), base + i * 1000, 1000, &d);
    }
    st.sync_dict(&mut d);
    st.append_events(&["{\"ts_ms\":1}".to_string()]);
    // the live writer is part-way through a frame: a repairing second instance would cut it
    let hot = seg_files(&root.join("tm"), "hot-").pop().unwrap();
    let mut f = fs::OpenOptions::new().append(true).open(&hot).unwrap();
    f.write_all(b"TSF1\xff\x00\x00\x00junk").unwrap();
    drop(f);
    // and a segment of a tier this (differently configured) instance would call an orphan
    put(&root, "tm/other-1.seg", "TSF1 not mine to delete");
    let dirs = [root.join("tm"), root.join("cold"), root.clone()];
    let refs: Vec<&Path> = dirs.iter().map(|p| p.as_path()).collect();
    let before = snapshot(&refs);
    let listing = |p: &Path| -> Vec<String> {
        let mut v: Vec<String> = fs::read_dir(p).unwrap().flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect();
        v.sort();
        v
    };
    let names = (listing(&root.join("tm")), listing(&root.join("cold")), listing(&root));
    let second = crate::store::start(&cfg).err().expect("the second instance must be refused");
    assert_eq!(second.code, "instance_running");
    assert_eq!(snapshot(&refs), before, "every file is byte-identical");
    assert_eq!((listing(&root.join("tm")), listing(&root.join("cold")), listing(&root)), names, "no file was created or deleted");
    // control: opening the store the way the old start-up order did really would have changed it
    let _ = Store::open(&cfg, d.mets.len(), d.ents.len()).unwrap();
    assert_ne!(snapshot(&refs), before, "(control) the repair path does modify the live files");
    drop(first);
    assert!(crate::store::start(&cfg).is_ok(), "the lock is released with the first instance");
}

#[test]
fn x03_tmpfs_is_checked_warned_about_and_can_be_required() {
    let proc_dir = fsafe::Dir::open_plain("/proc").unwrap();
    let e = fsafe::check_tmpfs(&proc_dir, true).unwrap_err();
    assert_eq!(e.code, "tmpfs_required");
    let c = fsafe::check_tmpfs(&proc_dir, false).unwrap();
    assert!(!c.is_tmpfs && c.detail.contains("NOT tmpfs"), "{}", c.detail);
    if let Ok(shm) = fsafe::Dir::open_plain("/dev/shm") {
        if shm.fs_magic() == Some(0x0102_1994) {
            assert!(fsafe::check_tmpfs(&shm, true).unwrap().is_tmpfs);
        }
    }
    // Dirs::open applies it to store.tmpfs_dir
    let root = tmp("x03");
    let text = cfg_text(&root, 4_000_000, TIERS3, 1 << 30, 3).replace("require_tmpfs = false", "require_tmpfs = true");
    let cfg = Config::parse(&text).unwrap();
    let here_is_tmpfs = fsafe::Dir::open_plain(root.to_str().unwrap()).unwrap().fs_magic() == Some(0x0102_1994);
    match crate::store::Dirs::open(&cfg) {
        Ok(_) => assert!(here_is_tmpfs, "a disk-backed tmpfs_dir must be refused when require_tmpfs is set"),
        Err(e) => {
            assert!(!here_is_tmpfs, "{}", e.msg);
            assert_eq!(e.code, "tmpfs_required", "{}", e.msg);
        }
    }
    // the warning reaches `status`
    let (root, mut col) = world("x03s");
    tick(&mut col, 1);
    let ctx = ctx_status(&root, &mut col, Status { store: crate::serve::StoreStatus { tmpfs_dir_is_tmpfs: Some(false), notes: vec!["WARNING store.tmpfs_dir: not tmpfs".into()], ..Default::default() }, ..Default::default() });
    let s = j(&handle(&ctx, r#"{"req":"status"}"#));
    assert_eq!(s["store"]["tmpfs_dir_is_tmpfs"], false);
    assert_eq!(s["store"]["notes"][0], "WARNING store.tmpfs_dir: not tmpfs");
    let ok = ctx_status(&root, &mut col, Status::default());
    assert!(j(&handle(&ok, r#"{"req":"status"}"#))["store"]["tmpfs_dir_is_tmpfs"].is_null());
}

fn ctx_status(root: &Path, col: &mut Collector, status: Status) -> Arc<Ctx> {
    let cfg = Config::parse(&cfg_text(root, 4_000_000, TIERS3, 1 << 30, 3)).unwrap();
    let ctx = Arc::new(Ctx::new(cfg, "{\"mode\":\"host\"}".to_string()));
    let mut sb = SnapBuilder::default();
    let snap = sb.build(col, 2, wall_ms(), 1000, 1234, status);
    ctx.publish(snap);
    ctx
}

#[test]
fn x05_the_writer_queue_is_bounded_in_bytes_too() {
    let big = |i: u64| crate::writer::Job {
        ts_ms: 1000 + i,
        dt_ms: 1000,
        frame: Arc::new(vec![(0u32, (0..1000).map(|m| Val { m, v: 1.0, raw: NONE, st: 0 }).collect::<Vec<_>>())]),
        events: vec![format!("ev{i}")],
        delta: crate::dict::Delta { mets: vec![(format!("m{i}"), false)], ents: vec![] },
    };
    let one = big(0).bytes();
    assert!(one > 24_000, "a 1000-value frame is about 24 KB: {one}");
    // 1000 jobs allowed by count, but only ~3 by bytes
    let sh = crate::writer::Shared::new(1000, one * 3 + one / 2);
    for i in 0..20 {
        sh.submit(big(i));
    }
    assert!(sh.queue_len() <= 3, "bounded by bytes, not by count: {}", sh.queue_len());
    assert!(sh.queue_bytes() <= one * 4, "{}", sh.queue_bytes());
    assert_eq!(sh.dropped_frames.load(Ordering::SeqCst), 20 - sh.queue_len(), "the drops are counted");
    assert!(sh.dropped_bytes.load(Ordering::SeqCst) >= 17 * one);
    sh.close();
    let (mut mets, mut events) = (Vec::new(), Vec::new());
    while let Some(job) = sh.next() {
        mets.extend(job.delta.mets.iter().map(|x| x.0.clone()));
        events.extend(job.events.clone());
    }
    assert_eq!(mets.len(), 20, "no dictionary id is lost with a dropped frame");
    assert_eq!(events.len(), 20, "no event is lost either");
    assert_eq!(sh.queue_bytes(), 0, "the byte count returns to zero when drained");
    // a single job above the cap is still written (the newest always stays)
    let sh = crate::writer::Shared::new(10, 1000);
    sh.submit(big(1));
    assert_eq!((sh.queue_len(), sh.dropped_frames.load(Ordering::SeqCst)), (1, 0));
}

#[test]
fn x06_one_stuck_pid_occupies_one_worker_and_is_not_asked_again() {
    let root = tmp("x06");
    let fifo = root.join("stuck-cmdline");
    assert_eq!(unsafe { mkfifo(cpath(&fifo).as_ptr(), 0o600) }, 0);
    put(&root, "ok-cmdline", "prog\0--flag\0");
    let ok = root.join("ok-cmdline").to_str().unwrap().to_string();
    let w = crate::cmdline::CmdWorkers::new();
    let f = fifo.to_str().unwrap().to_string();
    assert!(w.ask(7, 1, f.clone(), 256), "the first ask is queued; its read blocks in open(2)");
    for _ in 0..10 {
        assert!(!w.ask(7, 1, f.clone(), 256), "never a second read for a pid whose read is in flight");
    }
    assert_eq!(w.in_flight(), 1);
    // every other pid is still answered: the stuck one holds ONE worker, not all three
    for pid in 10..16 {
        assert!(w.ask(pid, 1, ok.clone(), 256));
    }
    let mut got = std::collections::HashMap::new();
    let t0 = Instant::now();
    while got.len() < 6 {
        for (k, v) in w.take_answers() {
            got.insert(k.0, v);
        }
        assert!(t0.elapsed() < Duration::from_secs(60), "other pids starved behind the stuck one: {:?}", got.keys());
        std::thread::sleep(Duration::from_millis(5));
    }
    assert!(got.values().all(|v| v.as_deref() == Ok("prog --flag")));
    // the stall ends: the answer arrives, and only then may the pid be asked again
    let mut wr = fs::OpenOptions::new().write(true).open(&fifo).unwrap();
    wr.write_all(b"late\0").unwrap();
    drop(wr);
    let t0 = Instant::now();
    loop {
        if w.take_answers().iter().any(|(k, v)| k.0 == 7 && v.as_deref() == Ok("late")) {
            break;
        }
        assert!(t0.elapsed() < Duration::from_secs(60), "the late answer never arrived");
        std::thread::sleep(Duration::from_millis(5));
    }
    assert_eq!(w.in_flight(), 0);
    assert!(w.ask(7, 1, ok, 256), "free again after the read returned");
}

#[test]
fn x07_secrets_in_command_lines_are_redacted_before_they_leave_the_sampler() {
    let r = |s: &str| crate::cmdline::render(s.as_bytes());
    assert_eq!(r("mysql\0--password\0hunter2\0--host\0db\0"), "mysql --password <redacted> --host db");
    assert_eq!(r("mysql\0--password=hunter2\0--host=db"), "mysql --password=<redacted> --host=db");
    assert_eq!(r("app\0--api-token\0abc\0--verbose"), "app --api-token <redacted> --verbose");
    assert_eq!(r("app\0--secret=s3\0--client_secret\0s4"), "app --secret=<redacted> --client_secret <redacted>");
    assert_eq!(r("app\0--password\0--verbose"), "app --password --verbose", "a following option is not a value");
    assert_eq!(r("env\0DB_PASSWORD=abc\0API_KEY=zzz\0AWS_SECRET_ACCESS_KEY=q\0LANG=C\0cmd"), "env DB_PASSWORD=<redacted> API_KEY=<redacted> AWS_SECRET_ACCESS_KEY=<redacted> LANG=C cmd");
    assert_eq!(r("docker\0run\0-e\0GH_TOKEN=abc\0--env=SESSION_TOKEN=x\0img"), "docker run -e GH_TOKEN=<redacted> --env=SESSION_TOKEN=<redacted> img");
    assert_eq!(r("curl\0https://user:pw@host/path?x=1"), "curl https://user:<redacted>@host/path?x=1");
    assert_eq!(r("psql\0postgres://app:p%40ss@db:5432/x"), "psql postgres://app:<redacted>@db:5432/x");
    // names that mention a secret but whose value is not one
    assert_eq!(r("app\0--password-file\0/etc/pw\0--no-password\0db"), "app --password-file /etc/pw --no-password db");
    assert_eq!(r("app\0--token-file=/run/t"), "app --token-file=/run/t");
    // ordinary command lines are untouched
    assert_eq!(r("/usr/bin/foo\0--bar\0baz=1\0-x\0--author=me\0"), "/usr/bin/foo --bar baz=1 -x --author=me");
    assert_eq!(r("sshd:\0root@pts/0"), "sshd: root@pts/0");
    // and it applies to what the reader returns (the path both the helper pool and --once use)
    let root = tmp("x07");
    put(&root, "cmdline", "app\0--password\0hunter2\0");
    assert_eq!(crate::cmdline::read_now(root.join("cmdline").to_str().unwrap(), 256).unwrap(), "app --password <redacted>");
    // a read cut at cmdline_max inside the value never reveals more than the cut
    assert_eq!(crate::cmdline::read_now(root.join("cmdline").to_str().unwrap(), 17).unwrap(), "app --password <redacted>");
}

#[test]
fn x08_the_socket_is_never_created_with_a_looser_mode() {
    let root = tmp("x08");
    let sock = root.join("p.sock").to_str().unwrap().to_string();
    // a link planted at the socket path is replaced, never written through
    fs::write(root.join("victim"), "precious").unwrap();
    symlink(root.join("victim"), &sock).unwrap();
    let inst = Instance::acquire(&sock).unwrap();
    let _l = fsafe::bind_private(&inst, 0o600).unwrap();
    let probe = fsafe::BIND_PROBE.lock().unwrap().iter().find(|(p, _)| *p == sock).map(|x| x.1);
    assert_eq!(probe, Some(0o600), "right after bind, before any chmod, the socket is already private (a plain bind gives 0777 & ~umask)");
    let md = fs::symlink_metadata(&sock).unwrap();
    assert_eq!(md.mode() & 0o777, 0o600);
    assert_eq!(md.uid(), fsafe::euid());
    assert_eq!(fs::read(root.join("victim")).unwrap(), b"precious");
}

#[test]
fn socket_mode_0660_is_applied_but_bind_stays_private() {
    let root = tmp("x08-660");
    let sock = root.join("g.sock").to_str().unwrap().to_string();
    let inst = Instance::acquire(&sock).unwrap();
    let _l = fsafe::bind_private(&inst, 0o660).unwrap();
    let probe = fsafe::BIND_PROBE.lock().unwrap().iter().find(|(p, _)| *p == sock).map(|x| x.1);
    assert_eq!(probe, Some(0o600), "during bind the socket is still 0600");
    let md = fs::symlink_metadata(&sock).unwrap();
    assert_eq!(md.mode() & 0o777, 0o660);
    assert_eq!(md.uid(), fsafe::euid());
}

#[test]
fn a_socket_that_fails_verification_is_unlinked() {
    let root = tmp("x08-unlink");
    let sock = root.join("v.sock").to_str().unwrap().to_string();
    let inst = Instance::acquire(&sock).unwrap();
    // 0o1660 can never verify: the verify compares `mode & 0o777` with the whole value
    assert!(fsafe::bind_private(&inst, 0o1660).is_err());
    assert!(fs::symlink_metadata(&sock).is_err(), "the failed socket was removed");
}

#[test]
fn socket_mode_config_default_and_validation() {
    let root = tmp("cfg-mode");
    let ok = cfg_text(&root, 4_000_000, TIERS3, 1 << 30, 3);
    let with = |v: &str| ok.replace("socket_mode = \"0600\"", &format!("socket_mode = \"{v}\""));
    let c = Config::parse(&ok).unwrap();
    assert_eq!(c.sampler.socket_mode_bits(), 0o600);
    let e = Config::parse(&ok.replace("socket_mode = \"0600\"\n", "")).unwrap_err();
    assert!(e.contains("socket_mode"), "a missing key is a config error, no default in code: {e}");
    assert_eq!(Config::parse(&with("0600")).unwrap().sampler.socket_mode_bits(), 0o600);
    assert_eq!(Config::parse(&with("0660")).unwrap().sampler.socket_mode_bits(), 0o660);
    for bad in ["0666", "0770", "660", ""] {
        let e = Config::parse(&with(bad)).unwrap_err();
        assert!(e.contains("socket_mode") && e.contains("0600") && e.contains("0660"), "{bad}: {e}");
    }
}

#[test]
fn x09_dictionary_files_carry_a_line_count_that_is_checked_at_load() {
    let root = tmp("x09");
    let cfg = cfg_for(&root);
    let cold = format!("{}/cold", root.display());
    let mut st = Store::open(&cfg, 0, 0).unwrap();
    let mut d = Dict::default();
    d.met("a", false);
    d.met("b", true);
    d.ent("cg:/x", 0);
    st.sync_dict(&mut d);
    let text = |f: &str| fs::read_to_string(root.join("cold").join(f)).unwrap();
    assert_eq!(text("mets.dict"), format!("{}G a\nC b\n", crate::dict::header(2)));
    assert_eq!(text("ents.dict"), format!("{}cg:/x\n", crate::dict::header(1)));
    // appends keep the count in step, in place
    d.met("c", false);
    d.ent("cg:/y", 0);
    st.sync_dict(&mut d);
    assert_eq!(text("mets.dict"), format!("{}G a\nC b\nG c\n", crate::dict::header(3)));
    assert_eq!(text("ents.dict"), format!("{}cg:/x\ncg:/y\n", crate::dict::header(2)));
    let (mets, ents) = crate::dict::read_lists_checked(&cold).unwrap();
    assert_eq!((mets.len(), ents.len()), (3, 2));
    // lines lost from the end of the file (header says 3): ids would map to the wrong names
    fs::write(root.join("cold/mets.dict"), format!("{}G a\nC b\n", crate::dict::header(3))).unwrap();
    assert_eq!(crate::dict::read_lists_checked(&cold).unwrap_err().code, "dict_short");
    assert_eq!(Dict::load_checked(&cold, 0).err().unwrap().code, "dict_short");
    // a header BEHIND the lines is a crash between the append and the count update: accepted
    fs::write(root.join("cold/mets.dict"), format!("{}G a\nC b\nG c\n", crate::dict::header(2))).unwrap();
    assert_eq!(crate::dict::read_lists_checked(&cold).unwrap().0.len(), 3);
    // a damaged header is an error, not a guess
    fs::write(root.join("cold/mets.dict"), "#TOPOS-DICT 1 n=0000000000000000x003\nG a\n").unwrap();
    assert_eq!(crate::dict::read_lists_checked(&cold).unwrap_err().code, "dict_header_corrupt");
    // a file from before the header existed still loads
    fs::write(root.join("cold/mets.dict"), "G a\nC b\n").unwrap();
    assert_eq!(crate::dict::read_lists_checked(&cold).unwrap().0.len(), 2);
}

#[test]
fn x10_slurp_refuses_files_without_end_and_gives_the_memory_back() {
    let mut s = crate::util::Slurp::with_cap(1 << 20);
    let e = s.read("/dev/zero").unwrap_err();
    assert_eq!(e.raw_os_error(), Some(27), "EFBIG, not an out-of-memory kill: {e}");
    assert!(s.buf_len() <= 1 << 20);
    // a big but legal file grows the buffer; the next read gives it back
    let root = tmp("x10");
    fs::write(root.join("big"), vec![b'1'; 3 << 20]).unwrap();
    fs::write(root.join("small"), "7\n").unwrap();
    let mut s = crate::util::Slurp::with_cap(8 << 20);
    assert_eq!(s.read(root.join("big").to_str().unwrap()).unwrap().len(), 3 << 20);
    assert!(s.buf_len() > 3 << 20);
    assert_eq!(s.read(root.join("small").to_str().unwrap()).unwrap(), b"7\n");
    assert!(s.buf_len() <= 256 * 1024, "{}", s.buf_len());
}

#[test]
fn x11_a_hung_sampling_thread_shows_in_status() {
    assert_eq!(crate::serve::stall_state(10_000, 9_500, 1000), (500, false));
    assert_eq!(crate::serve::stall_state(10_000, 6_000, 1000), (4000, true), "more than 3 tick periods since the last finished tick");
    assert_eq!(crate::serve::stall_state(10_000, 20_000, 1000), (0, false), "a clock step backwards never reports a stall");
    let (root, mut col) = world("x11");
    tick(&mut col, 1);
    let ctx = ctx_with(&root, &mut col, 4_000_000);
    ctx.set_beat_for_test(wall_ms() - 3_600_000);
    let s = j(&handle(&ctx, r#"{"req":"status"}"#));
    assert_eq!(s["sampling"]["stalled"], true);
    assert!(s["sampling"]["last_tick_age_ms"].as_u64().unwrap() >= 3_600_000);
    ctx.set_beat_for_test(wall_ms() + 3_600_000);
    assert_eq!(j(&handle(&ctx, r#"{"req":"status"}"#))["sampling"]["stalled"], false);
}

#[test]
fn x12_the_first_stop_signal_is_graceful_and_a_later_one_ends_the_process() {
    use crate::util::{stop_action, StopAction};
    assert_eq!(stop_action(0), StopAction::Graceful);
    assert_eq!(stop_action(1), StopAction::Force);
    assert_eq!(stop_action(7), StopAction::Force);
    // the real thing, in a child process that never looks at the stop flag (a stuck sampler)
    extern "C" {
        fn kill(pid: i32, sig: i32) -> i32;
    }
    if let Ok(dir) = std::env::var("TOPOS_X12_DIR") {
        crate::util::install_stop_handlers();
        fs::write(format!("{dir}/ready"), "1").unwrap();
        loop {
            if crate::util::STOP.load(Ordering::SeqCst) {
                let _ = fs::write(format!("{dir}/stopped"), "1");
            }
            std::thread::sleep(Duration::from_millis(10));
        }
    }
    let dir = tmp("x12");
    let mut child = std::process::Command::new(std::env::current_exe().unwrap())
        .args(["--exact", "tests::x12_the_first_stop_signal_is_graceful_and_a_later_one_ends_the_process", "--test-threads=1", "--nocapture"])
        .env("TOPOS_X12_DIR", &dir)
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .unwrap();
    let pid = child.id() as i32;
    let wait_for = |f: &str| {
        let t0 = Instant::now();
        while !dir.join(f).exists() {
            assert!(t0.elapsed() < Duration::from_secs(60), "child never wrote {f}");
            std::thread::sleep(Duration::from_millis(10));
        }
    };
    wait_for("ready");
    assert_eq!(unsafe { kill(pid, 15) }, 0);
    wait_for("stopped");
    std::thread::sleep(Duration::from_millis(100));
    assert!(child.try_wait().unwrap().is_none(), "the first signal only asks for a clean stop");
    assert_eq!(unsafe { kill(pid, 15) }, 0);
    // well inside HARD_STOP_S: the second signal itself ended it, with exit code 1
    let t0 = Instant::now();
    let status = loop {
        if let Some(s) = child.try_wait().unwrap() {
            break s;
        }
        if t0.elapsed() > Duration::from_secs(10) {
            let _ = child.kill();
            panic!("a second stop signal did not end the stuck process");
        }
        std::thread::sleep(Duration::from_millis(10));
    };
    assert_eq!(status.code(), Some(1));
}

#[test]
fn x13_rollups_say_how_many_ticks_of_a_bucket_held_a_value() {
    let root = tmp("x13");
    let cfg = cfg_for(&root);
    let mut st = Store::open(&cfg, 0, 0).unwrap();
    let mut d = Dict::default();
    let e = d.ent("cg:/x", 0);
    let (c, g) = (d.met("k.counter", true), d.met("k.gauge", false));
    let base = wall_ms() / 1000 * 1000 - 15_000;
    // warm tier = 2 ticks per bucket. bucket 1: both ticks valid; bucket 2: the second is a reset; bucket 3: all states
    let frames = [(5.0, 0u8), (5.0, 0), (5.0, 0), (0.0, stc::UN_RESET), (0.0, stc::UN_RESET), (0.0, stc::UN_RESET)];
    for (i, (v, s)) in frames.iter().enumerate() {
        let vals = vec![Val { m: c, v: *v, raw: 100, st: *s }, Val { m: g, v: *v, raw: NONE, st: *s }];
        st.push(&[(e, vals)], base + i as u64 * 1000, 1000, &d);
    }
    st.sync_dict(&mut d);
    for m in ["k.counter", "k.gauge"] {
        let w = query(&cfg, "cg:/x", m, "warm");
        assert_eq!(w["bucket_ticks"], 2, "{m}");
        assert_eq!(w["valid_ticks"], serde_json::json!([2, 1, 0]), "{m}: partial and all-state buckets are visible");
        assert_eq!(w["partial_points"], 2, "{m}");
        assert_eq!(w["points"].as_array().unwrap().len(), 3);
    }
    let w = query(&cfg, "cg:/x", "k.counter", "warm");
    assert_eq!(w["points"][0][1], 10.0, "a full bucket: sum of two ticks");
    assert_eq!(w["points"][1][1], 5.0, "a partial bucket: sum of ONE valid tick, over dt_ms of two");
    assert_eq!(w["points"][1][2], 2000);
    // the raw tier has no buckets and no such fields
    assert!(query(&cfg, "cg:/x", "k.gauge", "hot").get("valid_ticks").is_none());
    // the cold (zstd) tier carries it too
    assert_eq!(query(&cfg, "cg:/x", "k.gauge", "cold")["bucket_ticks"], 4);
}

#[test]
fn x14_compact_can_keep_not_applicable_states_and_fields_reports_omitted_entities() {
    let (root, mut col) = world("x14");
    tick(&mut col, 1);
    tick(&mut col, 2);
    let ctx = ctx_with(&root, &mut col, 4_000_000);
    // compact: not_applicable states are dropped by default, kept as typed objects with "na":true
    let plain = j(&handle(&ctx, r#"{"req":"current","procs":false,"compact":true}"#));
    let with_na = j(&handle(&ctx, r#"{"req":"current","procs":false,"compact":true,"na":true}"#));
    let count_na = |v: &Value| v["entities"].as_object().unwrap().values().flat_map(|e| e.as_object().unwrap().values()).filter(|x| x["s"] == "not_applicable").count();
    assert_eq!(count_na(&plain), 0);
    assert!(count_na(&with_na) > 0, "the expected absences are visible when asked for");
    // fields: entities with none of the requested metrics are counted, not silently gone
    let all = j(&handle(&ctx, r#"{"req":"current","procs":false}"#));
    let n_all = all["entities"].as_object().unwrap().len();
    let f = j(&handle(&ctx, r#"{"req":"current","procs":false,"fields":["meminfo.MemTotal"]}"#));
    let kept = f["entities"].as_object().unwrap().len();
    assert!(kept < n_all);
    assert_eq!(f["entities_omitted"].as_u64().unwrap() as usize, n_all - kept, "kept + omitted = all");
    assert!(all.get("entities_omitted").is_none(), "the default form is unchanged");
}

#[test]
fn x15_a_device_that_drops_out_of_io_stat_and_returns_is_a_reset_not_one_huge_delta() {
    let (root, mut col) = world("x15");
    fs::create_dir_all(root.join("sys/dev/block/253:0/slaves")).unwrap();
    fs::create_dir_all(root.join("sys/dev/block/254:0/slaves")).unwrap();
    let io = |a: u64, b: Option<u64>| {
        let mut s = format!("253:0 rbytes={a} wbytes=0 rios=1 wios=0 dbytes=0 dios=0\n");
        if let Some(b) = b {
            s.push_str(&format!("254:0 rbytes={b} wbytes=0 rios=1 wios=0 dbytes=0 dios=0\n"));
        }
        s
    };
    let rb = |col: &Collector| get(col, "cg:/", "io.rbytes").unwrap();
    put(&root, "cg/io.stat", &io(100, Some(1000)));
    tick(&mut col, 1);
    put(&root, "cg/io.stat", &io(110, Some(1010)));
    tick(&mut col, 2);
    assert_eq!((rb(&col).st, rb(&col).v), (stc::VALUE, 20.0));
    // the second device's blkg goes away: the sum loses its share
    put(&root, "cg/io.stat", &io(120, None));
    tick(&mut col, 3);
    assert_eq!(rb(&col).st, stc::UN_RESET);
    // ... and comes back with a large cumulative count: that is NOT 5 MB read in one tick
    put(&root, "cg/io.stat", &io(130, Some(5_000_000)));
    tick(&mut col, 4);
    assert_eq!(rb(&col).st, stc::UN_RESET, "the returning device must not turn its history into one delta");
    // afterwards deltas are normal again, measured from the new baseline
    put(&root, "cg/io.stat", &io(140, Some(5_000_010)));
    tick(&mut col, 5);
    assert_eq!((rb(&col).st, rb(&col).v), (stc::VALUE, 20.0));
    // a device seen for the first time began at zero: its first reading IS a delta
    fs::create_dir_all(root.join("sys/dev/block/255:0/slaves")).unwrap();
    put(&root, "cg/io.stat", &format!("{}255:0 rbytes=7 wbytes=0 rios=1 wios=0 dbytes=0 dios=0\n", io(150, Some(5_000_020))));
    tick(&mut col, 6);
    assert_eq!((rb(&col).st, rb(&col).v), (stc::VALUE, 27.0));
}
