//! topos ratatui PoC: a parallel implementation of the redesigned topos main screen.
mod app;
mod cols;
mod data;
mod demo;
mod ui;

use app::App;
use ratatui::backend::{CrosstermBackend, TestBackend};
use ratatui::crossterm::event::{self, DisableMouseCapture, EnableMouseCapture, Event, KeyEventKind};
use ratatui::crossterm::execute;
use ratatui::layout::Rect;
use ratatui::{Terminal, TerminalOptions, Viewport};
use std::io::Write;
use std::time::{Duration, Instant};

const USAGE: &str = "usage: topos-ratatui-poc <recording.jsonl> [--tick-ms 1000] [--config columns.toml]\n\
       topos-ratatui-poc <recording.jsonl> --bench N [--size WxH]\n\
       topos-ratatui-poc <recording.jsonl> --dump WxH [--frame N] [--noise all|middle|hot] [--select KEY] [--filter TEXT]";

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

fn load_cols(path: Option<String>) -> Vec<cols::Col> {
    let beside = std::env::current_exe().ok().and_then(|p| p.parent().map(|d| d.join("topos-columns.toml")));
    let (src, text) = match path {
        Some(p) => (p.clone(), std::fs::read_to_string(&p).unwrap_or_else(|e| { eprintln!("error: {p}: {e}"); std::process::exit(1) })),
        None => match beside.as_ref().and_then(|p| std::fs::read_to_string(p).ok().map(|t| (p.display().to_string(), t))) {
            Some(x) => x,
            None => ("(embedded columns.example.toml)".to_string(), cols::EXAMPLE.to_string()),
        },
    };
    cols::parse_config(&text).unwrap_or_else(|e| {
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
    let Some(path) = args.first().filter(|a| !a.starts_with("--")).cloned() else {
        eprintln!("{USAGE}");
        std::process::exit(2);
    };
    let tick_ms: u64 = opt("--tick-ms").and_then(|v| v.parse().ok()).unwrap_or(1000);
    let rec = data::load(&path).unwrap_or_else(|e| {
        eprintln!("error: {e}");
        std::process::exit(1);
    });
    let cols = load_cols(opt("--config"));
    if let Some(w) = opt("--plan") {
        // report which columns survive at a given table width
        let w: u16 = w.parse().unwrap_or(200);
        let p = cols::plan(&cols, w);
        let names: Vec<String> = p.cols.iter().map(|c| format!("{}[{}]", c.head, c.pri)).collect();
        println!("table width {w}: name_w={} {}/{} columns: {}", p.name_w, p.cols.len(), p.total, names.join(" "));
        return;
    }
    let mut a = App::new(rec, tick_ms, cols);

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

fn run(a: &mut App) -> std::io::Result<()> {
    let mut term = ratatui::init();
    execute!(std::io::stdout(), EnableMouseCapture)?;
    let tick = Duration::from_millis(a.tick_ms.max(10));
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
            let timeout = next.saturating_duration_since(Instant::now());
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
                if !a.frozen {
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
        a.advance(1);
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
