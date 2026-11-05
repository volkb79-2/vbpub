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

#[test]
fn config_parsing_rejects_bad_input_and_honours_order() {
    assert!(parse_config("[[column]]\nid = \"cpu\"\npri = 1\n").is_err(), "name must be first");
    assert!(parse_config("[[column]]\nid = \"name\"\npri = 1\n[[column]]\nid = \"nope\"\npri = 1\n").is_err());
    let c = parse_config("[[column]]\nid=\"name\"\npri=1\n[[column]]\nid=\"io_w\"\npri=2\n[[column]]\nid=\"cpu\"\npri=1 # c\nwidth=7\n").unwrap();
    assert_eq!(c.iter().map(|x| x.id).collect::<Vec<_>>(), [cols::ColId::Name, cols::ColId::IoW, cols::ColId::Cpu]);
    assert_eq!(c[2].w, 7);
}
