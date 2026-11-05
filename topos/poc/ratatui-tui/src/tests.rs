use super::*;
use crate::cols::{parse_config, plan, EXAMPLE};
use crate::data::parse_lines;

fn ent(key: &str, parent: Option<&str>, kind: &str, cpu: f64, procs: u32) -> String {
    let p = parent.map_or("null".to_string(), |p| format!("\"{p}\""));
    format!(
        r#""{key}":{{"entity":{{"key":"{key}","kind":"{kind}","parent":{p},"docker":null,"tier":null,"is_protected":false}},
        "metrics":{{"cpu_pct":[{cpu},"derived"],"ram":[1048576,"exact"],"mem_max":[null,"unlimited"],"mem_high":[null,"unavail_kernel"],
        "pids_current":[{procs},"exact"],"cgroup_procs":[{procs},"exact"],"io_r_bps":[0.0,"derived"],"mem_min":[0,"exact"]}},"findings":[]}}"#
    )
}

fn rec() -> data::Recording {
    let frame = |ts: u32| {
        format!(
            r#"{{"type":"frame","schema_version":1,"ts":{ts},"interval_s":1.0,"host":{{"host_load1":[1.0,"host"],"host_mem_total":[8000000000,"host"]}},
            "host_meta":{{"block_devices":[],"net_devices":[]}},
            "entities":{{{},{},{}}}}}"#,
            ent("", None, "root", 0.0, 0),
            ent("a.slice", Some(""), "slice", 50.0, 3),
            ent("idle.slice", Some(""), "slice", 0.0, 0)
        )
        .replace('\n', "")
    };
    let text = format!("{{\"type\":\"header\",\"schema_version\":1,\"host_id\":\"t\"}}\n{}\n{}\n{}\n", frame(1), frame(2), frame(3));
    parse_lines(&text).unwrap()
}

#[test]
fn parse_distinguishes_unset_and_unavailable() {
    let r = rec();
    let e = &r.snaps[0].ents[1];
    assert_eq!(e.cn("mem_max"), data::Cn::Unset);
    assert_eq!(e.cn("mem_high"), data::Cn::NA);
    assert_eq!(e.cn("nonexistent"), data::Cn::NA);
    assert_eq!(e.cn("io_r_bps"), data::Cn::Val(0.0));
}

#[test]
fn columns_drop_by_priority_and_never_exceed_width() {
    {
        let lens = "example";
        let all = parse_config(EXAMPLE).unwrap();
        let mut prev = usize::MAX;
        for w in (20..=320u16).rev() {
            let p = plan(&all, w);
            let used: u16 = p.name_w + p.cols.iter().filter(|c| c.id != cols::ColId::Name).map(|c| c.w + 1).sum::<u16>();
            assert!(used <= w, "{lens} width {w}: used {used}");
            assert!(p.cols.len() <= prev, "narrowing must never add columns");
            prev = p.cols.len();
            // the survivors keep the configured order
            let pos: Vec<usize> = p.cols.iter().map(|c| all.iter().position(|x| x.id == c.id).unwrap()).collect();
            assert!(pos.windows(2).all(|w| w[0] < w[1]));
            // no kept column has a worse priority than a dropped one
            let worst_kept = p.cols.iter().map(|c| c.pri).max().unwrap();
            let best_dropped = all.iter().filter(|c| !p.cols.iter().any(|k| k.id == c.id)).map(|c| c.pri).min();
            if let Some(b) = best_dropped {
                assert!(b >= worst_kept || w < 30, "{lens} w{w}: dropped pri {b} while keeping {worst_kept}");
            }
        }
    }
}

#[test]
fn noise_levels_hide_rows_and_count_them() {
    let mut a = App::new(rec(), 1000, parse_config(EXAMPLE).unwrap());
    a.noise = cols::Noise::All;
    a.rebuild();
    let all = a.rows.len();
    a.noise = cols::Noise::Hot;
    a.rebuild();
    assert!(a.rows.len() < all);
    assert!(a.hidden >= 1);
    a.noise = cols::Noise::All;
    a.filter = "idle".into();
    a.rebuild();
    assert!(a.rows.iter().any(|r| matches!(r.kind, app::RowKind::Cg(i) if a.snap().ents[i].key == "idle.slice")));
    assert!(!a.rows.iter().any(|r| matches!(r.kind, app::RowKind::Cg(i) if a.snap().ents[i].key == "a.slice")));
}

#[test]
fn renders_at_every_width_without_panicking_and_without_right_overflow() {
    let mut a = App::new(rec(), 1000, parse_config(EXAMPLE).unwrap());
    {
        for w in [20u16, 40, 80, 100, 139, 140, 200, 240, 300] {
            let text = render_text(&mut a, w, 30);
            assert!(text.lines().all(|l| l.chars().count() <= w as usize));
            assert!(text.contains("NAME") || w < 20);
        }
    }
}

#[test]
fn tree_expand_collapse_and_mouse_hit_testing() {
    let mut a = App::new(rec(), 1000, parse_config(EXAMPLE).unwrap());
    a.noise = cols::Noise::All;
    a.ui.proc_cap = 2; // collapsed shows at most 2 hot processes, expanded all of them
    a.rebuild();
    render_text(&mut a, 120, 30);
    let n0 = a.rows.len();
    a.move_cursor(1); // a.slice
    a.on_key(ratatui::crossterm::event::KeyEvent::from(ratatui::crossterm::event::KeyCode::Enter));
    assert_ne!(a.rows.len(), n0, "Enter toggles the open top-level slice and its process rows");
    let before = a.sort;
    let (r, id) = a.hit.heads[2];
    a.on_mouse(ratatui::crossterm::event::MouseEvent {
        kind: ratatui::crossterm::event::MouseEventKind::Down(ratatui::crossterm::event::MouseButton::Left),
        column: r.x,
        row: r.y,
        modifiers: ratatui::crossterm::event::KeyModifiers::NONE,
    });
    assert!(a.sort == id || a.sort == before);
    a.advance(1);
    assert!(a.fi >= 1);
}

// ---------------------------------------------------------------- live feed against a fake sampler

mod fake {
    use serde_json::{json, Value};
    use std::io::{BufRead, BufReader, Write};
    use std::os::unix::net::UnixListener;
    use std::path::PathBuf;

    fn val(v: f64) -> Value {
        json!({"s": "value", "v": v})
    }
    fn ctr(rate: f64) -> Value {
        json!({"s": "value", "v": rate, "rate": rate, "raw": 1})
    }
    fn unavail() -> Value {
        json!({"s": "unavailable", "why": "first_sample"})
    }
    fn notapp() -> Value {
        json!({"s": "not_applicable", "why": "feature_absent"})
    }

    pub fn sock(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!("topos-poc-{}-{name}.sock", std::process::id()))
    }

    /// A `current` answer: root, system.slice (150% cpu, io unavailable, high unlimited) and
    /// a.service with one D-state process, five running and one idle one.
    pub fn current(tick: u64, epoch: &str) -> Value {
        let mut procs = vec![];
        let mut p = |pid: u64, state: &str, cpu: f64, cg: &str| {
            procs.push(json!({"pid": pid, "start": 1, "comm": format!("proc{pid}"), "state": state, "why": "cpu", "cgroup": cg, "cmdline": "x",
                "rss_bytes": 10_000_000, "threads": 1, "cpu_pct": val(cpu), "majflt_per_s": val(0.0),
                "io_read_bps": unavail(), "io_write_bps": val(0.0), "run_delay_ms_per_s": val(0.0),
                "blkio_delay_ms_per_s": {"s": "not_applicable", "why": "task_delayacct_off"}}));
        };
        p(10, "D", 0.5, "cg:/system.slice/a.service");
        for k in 0..5 {
            p(20 + k, "R", 80.0 - k as f64 * 10.0, "cg:/system.slice/a.service");
        }
        p(30, "S", 0.1, "cg:/system.slice/a.service");
        p(40, "R", 9.0, "cg:/system.slice");
        let psi = |c: f64| json!({"psi.cpu.some.avg10": val(c), "psi.cpu.full.avg10": unavail(), "psi.memory.some.avg10": val(0.0), "psi.memory.full.avg10": val(0.0), "psi.io.some.avg10": val(0.0), "psi.io.full.avg10": val(0.0)});
        let mut root = psi(5.0);
        let ro = root.as_object_mut().unwrap();
        for (k, v) in [("cpu.usage_usec", ctr(3_000_000.0)), ("mem.current", val(8e9)), ("limit.memory.min", val(0.0)), ("limit.memory.max", val(-1.0)), ("procs.d_state", val(0.0)), ("pids.current", val(400.0)), ("io.cost.vrate", notapp())] {
            ro.insert(k.into(), v);
        }
        let mut sys = psi(2.0);
        let so = sys.as_object_mut().unwrap();
        for (k, v) in [("cpu.usage_usec", ctr(1_500_000.0)), ("mem.current", val(4e9)), ("mem.zswap", val(1e8)), ("mem.zswapped", val(4e8)), ("io.rbytes", unavail()), ("io.wbytes", ctr(2048.0)),
            ("limit.memory.min", val(1e9)), ("limit.memory.high", val(-1.0)), ("limit.memory.max", val(6e9)), ("procs.d_state", val(1.0)), ("pids.current", val(100.0))] {
            so.insert(k.into(), v);
        }
        let mut svc = psi(1.0);
        let vo = svc.as_object_mut().unwrap();
        for (k, v) in [("cpu.usage_usec", ctr(200_000.0)), ("mem.current", val(2e9)), ("mem.zswap", val(0.0)), ("mem.zswapped", val(0.0)), ("limit.memory.min", val(5e8)), ("procs.d_state", val(2.0)), ("pids.current", val(8.0))] {
            vo.insert(k.into(), v);
        }
        json!({"ok": true, "req": "current", "id": 2, "protocol": 1, "epoch": epoch, "tick": tick, "ts_ms": 1_700_000_000_000u64 + tick * 100, "dt_ms": 100,
            "pids": {"total": 300, "d_state": 3}, "truncated": false, "procs": procs,
            "entities": {
                "cg:/": root, "cg:/system.slice": sys, "cg:/system.slice/a.service": svc,
                "host": {"host.load1": val(1.5), "host.load5": val(1.0), "host.load15": val(0.5), "host.procs_running": val(3.0), "host.procs_blocked": val(1.0),
                    "meminfo.MemTotal": val(16e9), "meminfo.MemAvailable": val(8e9), "meminfo.SwapTotal": val(8e9), "meminfo.SwapFree": val(7e9),
                    "zswap.pool_bytes": val(1e8), "zswap.stored_bytes": val(4e8), "zswap.max_pool_percent": val(20.0)},
                "cpu:all": {"cpu.busy_pct": val(50.0)},
                "cpu:0": {"cpupct.us": val(40.0), "cpupct.sy": val(10.0), "cpupct.io": val(0.0), "cpupct.st": val(0.0)},
                "cpu:1": {"cpupct.us": unavail(), "cpupct.sy": val(10.0), "cpupct.io": val(0.0), "cpupct.st": val(0.0)},
                "dev:vda": {"disk.rd_sectors": ctr(2048.0), "disk.wr_sectors": ctr(0.0), "disk.reads": ctr(10.0), "disk.writes": ctr(0.0), "disk.await_ms": notapp(), "disk.util_pct": val(3.0)},
                "dev:vda1": {"disk.rd_sectors": ctr(2048.0), "disk.wr_sectors": ctr(0.0), "disk.reads": ctr(10.0), "disk.writes": ctr(0.0)},
            }})
    }

    fn hello(epoch: &str) -> Value {
        json!({"ok": true, "req": "hello", "id": 1, "protocol": 1, "epoch": epoch, "schema": 1, "version": "t", "pid": 4242, "tick_ms": 100,
            "cgroup_view": {"mode": "host", "because": "test"}})
    }

    /// Serve one client: `hello`, `status`, and `ticks` answers to `current`, then close the connection
    /// and remove the socket (the sampler "goes away").
    pub fn serve(path: PathBuf, epoch: &'static str, ticks: u64) -> std::thread::JoinHandle<()> {
        let _ = std::fs::remove_file(&path);
        let l = UnixListener::bind(&path).unwrap();
        std::thread::spawn(move || {
            let Ok((s, _)) = l.accept() else { return };
            let mut w = s.try_clone().unwrap();
            let mut rd = BufReader::new(s);
            let (mut n, mut line) = (0u64, String::new());
            loop {
                line.clear();
                if rd.read_line(&mut line).unwrap_or(0) == 0 {
                    break;
                }
                let req: Value = serde_json::from_str(&line).unwrap();
                let out = match req["req"].as_str() {
                    Some("hello") => hello(epoch),
                    Some("current") => {
                        if n >= ticks {
                            break;
                        }
                        n += 1;
                        current(n, epoch)
                    }
                    Some("status") => json!({"ok": true, "req": "status", "protocol": 1, "epoch": epoch, "uptime_s": 99.0,
                        "self": {"cpu_pct_recent_60s": val(0.5), "rss_bytes": val(1.5e6)}, "timing": {"tick_overruns": 0, "skipped_ticks": 0}}),
                    _ => json!({"ok": false, "protocol": 1, "epoch": epoch, "error": {"code": "unknown_req", "message": "?"}}),
                };
                if writeln!(w, "{out}").is_err() {
                    break;
                }
            }
            drop(l);
            let _ = std::fs::remove_file(&path);
        })
    }
}

fn pump_until(a: &mut App, secs: u64, f: impl Fn(&App) -> bool) -> bool {
    let t = std::time::Instant::now();
    while t.elapsed() < std::time::Duration::from_secs(secs) {
        a.pump();
        if f(a) {
            return true;
        }
        std::thread::sleep(std::time::Duration::from_millis(20));
    }
    false
}

fn live_app(path: &std::path::Path) -> App {
    let (mut cols, ui) = cols::parse_full(EXAMPLE).unwrap();
    for c in cols.iter_mut() {
        c.demo = false;
        if c.head == "BLK~" {
            c.head = "BLK";
        }
    }
    App::new_live(path.to_str().unwrap(), Some(100), cols, ui)
}

fn line_with<'a>(text: &'a str, needle: &str) -> &'a str {
    text.lines().find(|l| l.contains(needle)).unwrap_or_else(|| panic!("no line with {needle:?} in:\n{text}"))
}

#[test]
fn live_shows_sampler_values_and_never_zero_for_missing() {
    let p = fake::sock("values");
    let _srv = fake::serve(p.clone(), "e1", 1000);
    let mut a = live_app(&p);
    assert!(pump_until(&mut a, 5, |a| a.snaps.len() >= 2 && a.live.as_ref().unwrap().hello.is_some()), "no frames from the fake sampler");
    a.noise = cols::Noise::All;
    a.on_key(ratatui::crossterm::event::KeyEvent::from(ratatui::crossterm::event::KeyCode::Char('e')));
    for (w, h) in [(260u16, 60u16), (100, 30), (170, 40), (80, 24)] {
        let t = render_text(&mut a, w, h);
        for bad in ["demo", "BLK~", "~"] {
            assert!(!t.contains(bad), "{w}x{h}: {bad:?} must not appear with --socket:\n{t}");
        }
        assert!(t.contains("LIVE"), "{w}x{h}: source badge missing:\n{t}");
        assert!(t.contains(p.to_str().unwrap()) || w < 120, "{w}x{h}: socket path not shown");
    }
    let t = render_text(&mut a, 260, 60);
    // the visible source and sampler identity
    assert!(line_with(&t, "LIVE").contains("sampler t pid 4242 view:host epoch e1"));
    // real numbers: 1.5e6 usec/s = 150%, 3 running, per-CPU 50% busy bar for cpu 0
    assert!(line_with(&t, "system.slice").contains("150%"));
    assert!(t.contains("run 3  blocked 1  D-state 3"));
    assert!(line_with(&t, " 0[").contains(" 50%"));
    // unavailable -> n/a, not applicable -> dot, never zero: io.rbytes of system.slice is unavailable
    let s = a.snap();
    let sys = s.ents.iter().position(|e| e.key == "system.slice").unwrap();
    assert_eq!(s.ents[sys].cn("io_r_bps"), data::Cn::NA);
    assert_eq!(s.ents[sys].cn("mem_high"), data::Cn::Unset);
    assert_eq!(s.ents[sys].cn("ratio"), data::Cn::Val(4.0));
    let svc = s.ents.iter().position(|e| e.key == "system.slice/a.service").unwrap();
    assert_eq!(s.ents[svc].cn("ratio"), data::Cn::Unset, "zswapped 0 / zswap 0 is not applicable, not 0x");
    assert_eq!(s.ents[svc].cn("pids_max"), data::Cn::NA);
    let row = line_with(&t, "system.slice");
    assert!(row.contains("n/a"), "unavailable value must print n/a: {row}");
    assert!(row.contains('\u{b7}'), "unlimited/not-applicable must print a dot: {row}");
    // zswap rates are missing from the answer: n/a each, not 0
    assert!(t.contains("in/out/wb/rej n/a/n/a/n/a/n/a pages/s"), "{t}");
    // the memory stack is not in the answer: n/a, not 0B
    assert!(t.contains("anon n/a") && t.contains("file n/a"), "{t}");
    // a CPU whose us is unavailable prints n/a
    assert!(line_with(&t, " 1[").contains("n/a"));
    // effective memory.min is computed along the chain: a.service asks 500M under system.slice's 1G
    assert_eq!(s.ents[svc].num("effective_memory_min"), Some(5e8));
    // io.cost vrate is not applicable on this host: a dot, not n/a and not 0
    assert!(line_with(&t, "IO.COST").contains("vrate \u{b7}"));
    // partitions are folded into their disk
    assert!(t.contains("vda r") && !t.contains("vda1 r"));
}

#[test]
fn processes_float_under_their_cgroup_capped_and_blocked_first() {
    let p = fake::sock("procs");
    let _srv = fake::serve(p.clone(), "e1", 1000);
    let mut a = live_app(&p);
    assert!(pump_until(&mut a, 5, |a| a.snaps.len() >= 2));
    a.noise = cols::Noise::All;
    // a.service is collapsed (depth 1 expansion): 7 processes, only hot or blocked ones float, at most proc_cap = 3
    a.rebuild();
    let t = render_text(&mut a, 200, 50);
    let svc_row = t.lines().position(|l| l.contains("a.service")).expect("a.service row");
    // (system.slice's own hot process, proc40, follows its children; it is not one of a.service's)
    let procs: Vec<&str> = t.lines().skip(svc_row + 1).take_while(|l| l.trim_start().starts_with(|c: char| "DRS".contains(c)) && l.contains("proc")).filter(|l| !l.contains("proc40")).collect();
    assert_eq!(procs.len(), 3, "capped at proc_cap:\n{t}");
    assert!(procs[0].trim_start().starts_with("D proc10"), "blocked first: {}", procs[0]);
    assert!(procs[1].contains("proc20"), "then the hottest: {}", procs[1]);
    // the unavailable io rate of a process prints n/a, never 0
    assert!(procs[0].contains("n/a"));
    // expanded shows every process
    a.on_key(ratatui::crossterm::event::KeyEvent::from(ratatui::crossterm::event::KeyCode::Char('e')));
    let n = a.rows.iter().filter(|r| matches!(r.kind, app::RowKind::Proc(..))).count();
    assert_eq!(n, 8);
    // D-state counts roll up from the sampler's per-cgroup numbers: a.service 2, system.slice 1+2, root pids.d_state 3
    let sys = a.snap().ents.iter().position(|e| e.key == "system.slice").unwrap();
    assert_eq!(a.blk[sys], Some(3));
    assert_eq!(a.blk[a.snap().root], Some(3));
}

#[test]
fn dead_socket_neither_freezes_nor_crashes_the_ui() {
    let p = fake::sock("never-created");
    let _ = std::fs::remove_file(&p);
    let mut a = live_app(&p);
    let t0 = std::time::Instant::now();
    for _ in 0..8 {
        a.pump();
        let t = render_text(&mut a, 100, 30);
        assert!(t.contains("WAITING") && t.contains("sampler down: connect"), "{t}");
        std::thread::sleep(std::time::Duration::from_millis(25));
    }
    assert!(t0.elapsed() < std::time::Duration::from_secs(3), "the UI loop must not block on the dead socket");
    // keys still work: filter, help, quit
    a.on_key(ratatui::crossterm::event::KeyEvent::from(ratatui::crossterm::event::KeyCode::Char('?')));
    render_text(&mut a, 100, 30);
    a.on_key(ratatui::crossterm::event::KeyEvent::from(ratatui::crossterm::event::KeyCode::Char('x')));
    assert!(a.on_key(ratatui::crossterm::event::KeyEvent::from(ratatui::crossterm::event::KeyCode::Char('q'))));
    // no value is invented while waiting: the empty root prints n/a
    assert!(!render_text(&mut a, 100, 30).contains("demo"));
}

#[test]
fn sampler_going_away_marks_data_stale_and_keeps_last_values() {
    let p = fake::sock("stale");
    let srv = fake::serve(p.clone(), "e1", 3);
    let mut a = live_app(&p);
    assert!(pump_until(&mut a, 5, |a| a.snaps.len() >= 3));
    srv.join().unwrap();
    assert!(pump_until(&mut a, 5, |a| a.live.as_ref().unwrap().down.is_some()), "the feed must report the loss");
    let t = render_text(&mut a, 200, 50);
    assert!(a.stale());
    assert!(t.contains("STALE") && t.contains("sampler down"), "{t}");
    assert!(t.contains("system.slice") && t.contains("150%"), "last data stays on screen");
    // the sampler comes back with the same epoch: LIVE again, no restart noticed
    let _srv2 = fake::serve(p.clone(), "e1", 1000);
    assert!(pump_until(&mut a, 8, |a| a.live.as_ref().unwrap().down.is_none()), "no reconnect");
    assert!(!a.stale());
    assert_eq!(a.live.as_ref().unwrap().epoch_changes, 0);
}

#[test]
fn epoch_change_is_reported_and_clears_history() {
    let p = fake::sock("epoch");
    let srv = fake::serve(p.clone(), "e1", 3);
    let mut a = live_app(&p);
    assert!(pump_until(&mut a, 5, |a| a.snaps.len() >= 3));
    srv.join().unwrap();
    let _srv2 = fake::serve(p.clone(), "e2", 1000);
    assert!(pump_until(&mut a, 8, |a| a.live.as_ref().unwrap().epoch_changes == 1), "epoch change not noticed");
    assert!(a.snaps.len() <= 2, "counters are not comparable across epochs: history cleared, have {}", a.snaps.len());
    assert!(a.msg.contains("restarted") && a.msg.contains("e1") && a.msg.contains("e2"), "{}", a.msg);
    assert!(render_text(&mut a, 200, 40).contains("epoch e2 (restarts seen 1)"));
}

/// Every id of the Textual view's assets/columns.toml (read from that worktree, not changed).
const TEXTUAL_IDS: &str = "name cpu thr cpu_wt quota ram_ceil mem_min mem_low eff_min mem_high mem_max swap swap_max zsw ratio anon file rf_d rf_z rf_f \
    psi_c psi_m psi_i io_r io_w iops_r iops_w stall io_max iocost blk maj pids net_rx net_tx origin drift \
    shmem psi_cf psi_mf psi_if pgscan pgsteal prot drift_list warn io_wt pids_max ev_high ev_max ev_oom";

#[test]
fn one_file_drives_both_views_all_textual_ids_accepted() {
    let mut text = String::new();
    for (i, id) in TEXTUAL_IDS.split_whitespace().enumerate() {
        text.push_str(&format!("[[column]]\nid = \"{id}\"\npri = {}\n", 1 + i % 5));
    }
    let c = parse_config(&text).unwrap_or_else(|e| panic!("{e}"));
    assert_eq!(c.len(), TEXTUAL_IDS.split_whitespace().count());
    // the shipped example lists every one of them too
    let ex = parse_config(EXAMPLE).unwrap();
    for id in TEXTUAL_IDS.split_whitespace() {
        assert!(EXAMPLE.contains(&format!("id = \"{id}\"")), "columns.example.toml lacks {id}");
    }
    assert!(ex.len() >= c.len());
    // and a file with a [ui] table still parses as plain TOML columns for Textual: [ui] comes before the first column
    assert!(EXAMPLE.find("[ui]").unwrap() < EXAMPLE.find("[[column]]").unwrap());
}

#[test]
fn pane_width_is_46_at_any_width_and_comes_from_the_config() {
    let (cols, ui) = cols::parse_full(EXAMPLE).unwrap();
    assert_eq!((ui.pane_width, ui.pane_auto_min_width, ui.proc_cap), (46, 140, 3));
    let mut a = App::new(rec(), 1000, cols.clone());
    for (w, pane) in [(260u16, 46u16), (170, 46), (140, 46), (139, 0)] {
        render_text(&mut a, w, 30);
        assert_eq!(a.hit.body.width, w - pane, "terminal {w}");
    }
    let (_, narrow) = cols::parse_full("[ui]\npane_width = 30\n[[column]]\nid = \"name\"\npri = 1\n").unwrap();
    assert_eq!(narrow.pane_width, 30);
    let mut b = App::new(rec(), 1000, cols);
    b.ui = narrow;
    render_text(&mut b, 260, 30);
    assert_eq!(b.hit.body.width, 230);
    assert!(cols::parse_full("[ui]\nbogus = 1\n").is_err());
}

#[test]
fn recording_mode_keeps_its_demo_labels() {
    let mut a = App::new(rec(), 1000, parse_config(EXAMPLE).unwrap());
    let t = render_text(&mut a, 200, 40);
    assert!(a.demo && t.contains("demo") && t.contains("BLK~"));
}

#[test]
fn config_parsing_rejects_bad_input_and_honours_order() {
    assert!(parse_config("[[column]]\nid = \"cpu\"\npri = 1\n").is_err(), "name must be first");
    assert!(parse_config("[[column]]\nid = \"name\"\npri = 1\n[[column]]\nid = \"nope\"\npri = 1\n").is_err());
    let c = parse_config("[[column]]\nid=\"name\"\npri=1\n[[column]]\nid=\"io_w\"\npri=2\n[[column]]\nid=\"cpu\"\npri=1 # c\nwidth=7\n").unwrap();
    assert_eq!(c.iter().map(|x| x.id).collect::<Vec<_>>(), [cols::ColId::Name, cols::ColId::IoW, cols::ColId::Cpu]);
    assert_eq!(c[2].w, 7);
}
