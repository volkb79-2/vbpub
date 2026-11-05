//! topos ratatui PoC: a parallel implementation of the redesigned topos main screen.
mod app;
mod cols;
mod data;
mod demo;
mod live;
mod ui;

use app::App;
use ratatui::backend::{CrosstermBackend, TestBackend};
use ratatui::crossterm::event::{self, DisableMouseCapture, EnableMouseCapture, Event, KeyEventKind};
use ratatui::crossterm::execute;
use ratatui::layout::Rect;
use ratatui::{Terminal, TerminalOptions, Viewport};
use std::io::Write;
use std::time::{Duration, Instant};

const USAGE: &str = "usage: topos-ratatui-poc --socket /run/topos/sampler.sock [--config columns.toml]     (live, from topos-sampler)\n\
       topos-ratatui-poc <recording.jsonl> [--tick-ms 1000] [--config columns.toml]                   (replay, with demo data)\n\
       topos-ratatui-poc (<recording.jsonl> | --socket PATH) --bench N [--size WxH]\n\
       topos-ratatui-poc (<recording.jsonl> | --socket PATH) --dump WxH [--frame N] [--noise all|middle|hot] [--select KEY] [--filter TEXT]\n\
       topos-ratatui-poc --plan TABLE_WIDTH | --plan-term TERMINAL_WIDTH   [--config columns.toml]";

static BYTES: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
struct Count;
impl Write for Count {
    fn write(&mut self, b: &[u8]) -> std::io::Result<usize> {
        BYTES.fetch_add(b.len() as u64, std::sync::atomic::Ordering::Relaxed);
        Ok(b.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

fn load_cols(path: Option<String>) -> (Vec<cols::Col>, cols::UiCfg) {
    let beside = std::env::current_exe().ok().and_then(|p| p.parent().map(|d| d.join("topos-columns.toml")));
    let (src, text) = match path {
        Some(p) => (p.clone(), std::fs::read_to_string(&p).unwrap_or_else(|e| { eprintln!("error: {p}: {e}"); std::process::exit(1) })),
        None => match beside.as_ref().and_then(|p| std::fs::read_to_string(p).ok().map(|t| (p.display().to_string(), t))) {
            Some(x) => x,
            None => ("(embedded columns.example.toml)".to_string(), cols::EXAMPLE.to_string()),
        },
    };
    cols::parse_full(&text).unwrap_or_else(|e| {
        eprintln!("error: column config {src}: {e}");
        std::process::exit(1)
    })
}

fn size_of(s: &str) -> (u16, u16) {
    let (w, h) = s.split_once('x').unwrap_or(("170", "48"));
    (w.parse().unwrap_or(170), h.parse().unwrap_or(48))
}

fn render_text(a: &mut App, w: u16, h: u16) -> String {
    let mut t = Terminal::new(TestBackend::new(w, h)).unwrap();
    // two passes: the first learns the viewport height for scrolling
    for _ in 0..2 {
        t.draw(|f| ui::draw(f, a)).unwrap();
    }
    let b = t.backend().buffer();
    (0..h).map(|y| (0..w).map(|x| b[(x, y)].symbol().to_string()).collect::<String>().trim_end().to_string()).collect::<Vec<_>>().join("\n")
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let opt = |k: &str| args.iter().position(|a| a == k).and_then(|i| args.get(i + 1)).cloned();
    let socket = opt("--socket");
    let (cols, ui_cfg) = load_cols(opt("--config"));
    if let Some(w) = opt("--plan") {
        // report which columns survive at a given table width
        let w: u16 = w.parse().unwrap_or(200);
        let p = cols::plan(&cols, w);
        let names: Vec<String> = p.cols.iter().map(|c| format!("{}[{}]", c.head, c.pri)).collect();
        println!("table width {w}: name_w={} {}/{} columns: {}", p.name_w, p.cols.len(), p.total, names.join(" "));
        return;
    }
    if let Some(w) = opt("--plan-term") {
        // the same for a whole terminal: the side pane takes its configured width first
        let w: u16 = w.parse().unwrap_or(200);
        let pane = if w >= ui_cfg.pane_auto_min_width { ui_cfg.pane_width.min(w / 2) } else { 0 };
        let p = cols::plan(&cols, w - pane);
        let names: Vec<String> = p.cols.iter().map(|c| c.head.to_string()).collect();
        println!("terminal {w} (pane {pane}, table {}): {}/{} columns: {}", w - pane, p.cols.len(), p.total, names.join(" "));
        return;
    }
    let tick_ms: u64 = opt("--tick-ms").and_then(|v| v.parse().ok()).unwrap_or(1000);
    let mut a = if let Some(sock) = &socket {
        // live: no recording and no demo data is reachable from here
        let mut cols = cols;
        for c in cols.iter_mut() {
            c.demo = false;
            if c.head == "BLK~" {
                c.head = "BLK";
            }
        }
        App::new_live(sock, opt("--tick-ms").and_then(|v| v.parse().ok()), cols, ui_cfg)
    } else {
        let Some(path) = args.first().filter(|a| !a.starts_with("--")).cloned() else {
            eprintln!("{USAGE}");
            std::process::exit(2);
        };
        let rec = data::load(&path).unwrap_or_else(|e| {
            eprintln!("error: {e}");
            std::process::exit(1);
        });
        let mut a = App::new(rec, tick_ms, cols);
        a.ui = ui_cfg;
        a
    };
    if a.live.is_some() && (opt("--bench").is_some() || opt("--dump").is_some()) {
        // one-shot modes need real data first: wait for two sampler ticks
        if !wait_snaps(&mut a, 2, Duration::from_secs(15)) {
            eprintln!("error: no data from the sampler at {} within 15 s", socket.as_deref().unwrap_or(""));
            std::process::exit(1);
        }
    }

    if let Some(n) = opt("--bench") {
        bench(&mut a, n.parse().unwrap_or(1000), opt("--size").as_deref().map_or((170, 48), size_of));
        return;
    }
    if let Some(sz) = opt("--dump") {
        if let Some(n) = opt("--noise") {
            a.noise = match n.as_str() { "all" => cols::Noise::All, "hot" => cols::Noise::Hot, _ => cols::Noise::Middle };
        }
        if let Some(f) = opt("--frame").and_then(|v| v.parse::<usize>().ok()) {
            a.fi = f.min(a.snaps.len() - 1);
            a.recompute();
        }
        if let Some(t) = opt("--filter") {
            a.filter = t;
        }
        a.rebuild();
        if let Some(k) = opt("--select") {
            if let Some(p) = a.rows.iter().position(|r| matches!(r.kind, app::RowKind::Cg(i) if a.snap().ents[i].key.contains(&k))) {
                a.move_cursor(p as isize);
            }
        }
        let (w, h) = size_of(&sz);
        println!("{}", render_text(&mut a, w, h));
        return;
    }
    if let Err(e) = run(&mut a) {
        eprintln!("error: {e}");
        std::process::exit(1);
    }
}

/// Pump the live feed until `n` frames have arrived (or the time is up).
fn wait_snaps(a: &mut App, n: usize, limit: Duration) -> bool {
    let t0 = Instant::now();
    while t0.elapsed() < limit {
        a.pump();
        if a.live.as_ref().map_or(false, |l| l.got_first) && a.snaps.len() >= n {
            return true;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    false
}

fn run(a: &mut App) -> std::io::Result<()> {
    let mut term = ratatui::init();
    execute!(std::io::stdout(), EnableMouseCapture)?;
    // live mode repaints once a second (data age, uptime) and whenever the feed delivers
    let tick = Duration::from_millis(if a.live.is_some() { 1000 } else { a.tick_ms.max(10) });
    let mut next = Instant::now() + tick;
    let mut dirty = true;
    let res = (|| -> std::io::Result<()> {
        loop {
            if dirty {
                let t0 = Instant::now();
                term.draw(|f| ui::draw(f, a))?;
                a.perf.after_draw(t0.elapsed().as_secs_f64() * 1e3);
                dirty = false;
            }
            if a.live.is_some() {
                dirty |= a.pump();
            }
            let mut timeout = next.saturating_duration_since(Instant::now());
            if a.live.is_some() {
                timeout = timeout.min(Duration::from_millis(50));
            }
            if event::poll(timeout)? {
                loop {
                    match event::read()? {
                        Event::Key(k) if k.kind != KeyEventKind::Release => {
                            if a.on_key(k) {
                                return Ok(());
                            }
                            dirty = true;
                        }
                        Event::Mouse(m) => dirty |= a.on_mouse(m),
                        Event::Resize(..) => dirty = true,
                        _ => {}
                    }
                    if !event::poll(Duration::ZERO)? {
                        break;
                    }
                }
            }
            if Instant::now() >= next {
                next += tick;
                if a.live.is_some() {
                    // new data already repainted; the clock only matters while the age counter runs
                    dirty |= a.stale() || a.age_s().is_none();
                } else if !a.frozen {
                    a.advance(1);
                    dirty = true;
                }
            }
        }
    })();
    let _ = execute!(std::io::stdout(), DisableMouseCapture);
    ratatui::restore();
    res
}

fn bench(a: &mut App, n: usize, (w, h): (u16, u16)) {
    let mut term = Terminal::with_options(CrosstermBackend::new(Count), TerminalOptions { viewport: Viewport::Fixed(Rect::new(0, 0, w, h)) }).unwrap();
    for _ in 0..2 {
        term.draw(|f| ui::draw(f, a)).unwrap();
    }
    let (c0, t0) = (app::cpu_ms(), Instant::now());
    for _ in 0..n {
        if a.live.is_some() {
            a.recompute(); // rebuild the rows as a new live frame would
        } else {
            a.advance(1);
        }
        term.draw(|f| ui::draw(f, a)).unwrap();
    }
    let (cpu, wall) = (app::cpu_ms() - c0, t0.elapsed().as_secs_f64() * 1e3);
    println!(
        "ratatui-poc: frames={n} size={w}x{h} rows={} cpu_ms_per_frame={:.3} wall_ms_per_frame={:.3} rss_kb={} bytes_out={}",
        a.rows.len(),
        cpu / n as f64,
        wall / n as f64,
        app::rss_kb(),
        BYTES.load(std::sync::atomic::Ordering::Relaxed)
    );
}

#[cfg(test)]
mod tests;
