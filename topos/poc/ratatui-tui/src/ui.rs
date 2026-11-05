//! Drawing: meters, info line, tree table, side pane, status line, footer, help.
use crate::app::{App, PaneMode, RowKind};
use crate::cols::{self, band, band_style, dim, fmt_bytes, fmt_count, fmt_pct, ColId, Noise};
use crate::data::{Cn, Ent, Snap};
use crate::demo;
use ratatui::layout::{Alignment, Constraint, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Clear, Paragraph, Row, Table, Wrap};
use ratatui::Frame;

const SEL_BG: Color = Color::Rgb(38, 52, 84);
const BAR_BG: Color = Color::Rgb(34, 36, 48);

fn sp(s: impl Into<String>, st: Style) -> Span<'static> {
    Span::styled(s.into(), st)
}
fn plain(s: impl Into<String>) -> Span<'static> {
    sp(s, Style::new().fg(Color::Gray))
}
fn head(s: &str) -> Span<'static> {
    sp(s.to_string(), Style::new().fg(Color::Cyan).add_modifier(Modifier::BOLD))
}
fn demo_sp(s: impl Into<String>) -> Span<'static> {
    sp(s, Style::new().fg(Color::Magenta))
}
fn trunc(s: &str, w: usize) -> String {
    if s.chars().count() <= w {
        s.to_string()
    } else if w == 0 {
        String::new()
    } else {
        let mut t: String = s.chars().take(w - 1).collect();
        t.push('\u{2026}');
        t
    }
}

pub fn draw(f: &mut Frame, a: &mut App) {
    let area = f.area();
    let mh = match area.height {
        44.. => 9,
        28.. => 8,
        18.. => 6,
        12.. => 4,
        _ => 0,
    };
    let [mr, info_r, body_r, status_r, foot_r] =
        Layout::vertical([Constraint::Length(mh), Constraint::Length(1), Constraint::Min(1), Constraint::Length(1), Constraint::Length(1)])
            .areas(area);
    draw_meters(f, a, mr);
    let pane_w = pane_width(a, body_r.width);
    let [table_r, pane_r] = Layout::horizontal([Constraint::Min(10), Constraint::Length(pane_w)]).areas(body_r);
    a.hit.pane_shown = pane_w > 0;
    draw_info(f, a, info_r, table_r);
    draw_table(f, a, table_r);
    if pane_w > 0 {
        draw_pane(f, a, pane_r);
    }
    draw_status(f, a, status_r);
    draw_footer(f, a, foot_r);
    if a.help {
        draw_help(f, area);
    }
}

fn pane_width(a: &App, w: u16) -> u16 {
    let want = match a.pane {
        PaneMode::Auto => w >= 140,
        PaneMode::On => true,
        PaneMode::Off => false,
    };
    if !want {
        return 0;
    }
    (if w >= 220 { 62 } else { 46 }).min(w / 2)
}

// ---------------------------------------------------------------- meters

fn stacked_bar(w: usize, segs: &[(f64, Color)], total: f64) -> Vec<Span<'static>> {
    let mut out = vec![];
    let (mut acc, mut prev) = (0.0, 0usize);
    for &(v, c) in segs {
        acc += v.max(0.0);
        let upto = ((acc / total.max(1e-9)).min(1.0) * w as f64).round() as usize;
        if upto > prev {
            out.push(sp("\u{2588}".repeat(upto - prev), Style::new().fg(c)));
            prev = upto;
        }
    }
    if prev < w {
        out.push(sp("\u{b7}".repeat(w - prev), dim()));
    }
    out
}

fn spark(vals: &[f64], w: usize, floor: f64) -> String {
    const B: [char; 8] = ['\u{2581}', '\u{2582}', '\u{2583}', '\u{2584}', '\u{2585}', '\u{2586}', '\u{2587}', '\u{2588}'];
    let max = vals.iter().cloned().fold(floor, f64::max);
    let skip = vals.len().saturating_sub(w);
    vals[skip..].iter().map(|v| B[((v / max) * 7.0).round().clamp(0.0, 7.0) as usize]).collect()
}

fn uptime(s: f64) -> String {
    let (d, h, m) = ((s / 86400.0) as u64, (s % 86400.0 / 3600.0) as u64, (s % 3600.0 / 60.0) as u64);
    if d > 0 { format!("{d}d{h}h") } else { format!("{h}h{m:02}m") }
}

fn opt(v: Option<f64>, f: impl Fn(f64) -> String) -> String {
    v.map_or("n/a".to_string(), f)
}

/// Last `n` values of a host metric, ending at the current frame (wraps with the replay loop).
fn trend(a: &App, key: &str, n: usize) -> Vec<f64> {
    let len = a.snaps.len();
    (0..n.min(len)).rev().map(|k| a.snaps[(a.fi + len - k % len) % len].h(key).unwrap_or(0.0)).collect()
}

type Pl = Vec<(u8, Line<'static>)>;

fn fit(mut v: Pl, n: usize) -> Vec<Line<'static>> {
    while v.len() > n {
        let worst = v.iter().map(|x| x.0).max().unwrap();
        let i = v.iter().rposition(|x| x.0 == worst).unwrap();
        v.remove(i);
    }
    v.into_iter().map(|x| x.1).collect()
}

fn meter_lines(a: &App, w_each: usize) -> (Pl, Pl, Pl) {
    let s = a.snap();
    let root_kids = &s.children[s.root];
    let sum = |k: &str| root_kids.iter().filter_map(|&i| s.ents[i].num(k)).sum::<f64>();
    let any = root_kids.iter().any(|&i| s.ents[i].num("cpu_pct").is_some());
    let cores = if any { Some(sum("cpu_pct") / 100.0) } else { None };

    // ---- CPU: total is real (sum of top-level cgroups); the per-CPU split is demo
    let mut ca: Pl = vec![];
    ca.push((
        1,
        Line::from(vec![
            head("CPU "),
            plain(format!("{} busy  ", opt(cores, |c| format!("{c:.1} cores")))),
            plain(format!("load {} {} {}  up {}", opt(s.h("host_load1"), |v| format!("{v:.2}")), opt(s.h("host_load5"), |v| format!("{v:.2}")), opt(s.h("host_load15"), |v| format!("{v:.2}")), opt(s.h("host_uptime_s"), uptime))),
        ]),
    ));
    ca.push((
        3,
        Line::from(vec![
            demo_sp(format!("per-CPU demo({}) ", demo::NCPU)),
            sp("us", Style::new().fg(Color::Green)),
            plain(" "),
            sp("sy", Style::new().fg(Color::Red)),
            plain(" "),
            sp("io", Style::new().fg(Color::Blue)),
            plain(" "),
            sp("st", Style::new().fg(Color::Magenta)),
        ]),
    ));
    let cpus = demo::per_cpu(cores.unwrap_or(0.0), a.fi);
    let bw = (w_each.saturating_sub(12) / 2).clamp(6, 22);
    for (r, pair) in cpus.chunks(2).enumerate() {
        let mut spans = vec![];
        for (k, c) in pair.iter().enumerate() {
            let tot: f64 = c.iter().sum();
            spans.push(plain(format!("{:>2}", r * 2 + k)));
            spans.push(sp("[", dim()));
            spans.extend(stacked_bar(bw, &[(c[0], Color::Green), (c[1], Color::Red), (c[2], Color::Blue), (c[3], Color::Magenta)], 1.0));
            spans.push(sp("]", dim()));
            spans.push(demo_sp(format!("{:>3.0}% ", tot * 100.0)));
        }
        ca.push((4 + r as u8 / 2, Line::from(spans)));
    }
    let d: u32 = a.blk[s.root];
    ca.push((
        2,
        Line::from(vec![
            demo_sp(format!("run {}  D-state {}  ", (cores.unwrap_or(0.0) + 0.5) as u32 + 1, d)),
            plain("(no D-state/us/sy in recording)"),
        ]),
    ));

    // ---- memory
    let mut cb: Pl = vec![];
    let total = s.h("host_mem_total").unwrap_or(0.0);
    let avail = s.h("host_mem_available").unwrap_or(0.0);
    let (anon, file, shmem) = (sum("anon"), sum("file"), sum("shmem"));
    let zpool = s.h("host_zswap_pool").unwrap_or(0.0);
    cb.push((
        1,
        Line::from(vec![head("MEM "), plain(format!("{} used of {}  avail {}", fmt_bytes((total - avail).max(0.0)), fmt_bytes(total), fmt_bytes(avail)))]),
    ));
    let mut bar = vec![sp("[", dim())];
    bar.extend(stacked_bar(
        w_each.saturating_sub(2).max(8),
        &[(anon, Color::Green), (file, Color::Blue), (shmem, Color::Yellow), (zpool, Color::Magenta)],
        total,
    ));
    bar.push(sp("]", dim()));
    cb.push((1, Line::from(bar)));
    let lg = |c, t: &str, v: f64| vec![sp("\u{25a0}", Style::new().fg(c)), plain(format!("{t} {} ", fmt_bytes(v)))];
    let mut legend = lg(Color::Green, "anon", anon);
    legend.extend(lg(Color::Blue, "file", file));
    legend.extend(lg(Color::Yellow, "shm", shmem));
    legend.extend(lg(Color::Magenta, "zswap", zpool));
    legend.push(plain(format!("\u{b7}free {}", fmt_bytes(avail))));
    cb.push((3, Line::from(legend)));
    let ratio = s.h("host_zswap_ratio");
    let stored = s.h("host_zswap_stored");
    cb.push((
        2,
        Line::from(vec![
            head("ZSWAP "),
            plain(format!(
                "{} \u{2190} {}  ratio {}  pool {} of {}%",
                fmt_bytes(zpool),
                opt(stored, fmt_bytes),
                opt(ratio, |r| format!("{r:.2}x")),
                if total > 0.0 { format!("{:.1}%", zpool / total * 100.0) } else { "n/a".into() },
                opt(s.h("host_zswap_max_pool_percent"), |v| format!("{v:.0}")),
            )),
        ]),
    ));
    let nz = |k: u64| 20.0 * demo::noise(k + a.fi as u64);
    cb.push((
        5,
        Line::from(vec![plain("  in/out/wb/rej "), demo_sp(format!("{:.0}/{:.0}/{:.0}/{:.0} /s demo", nz(1) * 6.0, nz(2) * 4.0, nz(3) * 0.3, nz(4) * 0.1))]),
    ));
    let (st, sf) = (s.h("host_swap_total"), s.h("host_swap_free"));
    let used = st.zip(sf).map(|(t, f)| t - f);
    cb.push((
        2,
        Line::from(vec![
            head("SWAP "),
            plain(format!(
                "{} of {} used ({})  cached {}",
                opt(used, fmt_bytes),
                opt(st, fmt_bytes),
                opt(used.zip(st).map(|(u, t)| u / t * 100.0), fmt_pct),
                opt(s.h("host_swapcached"), fmt_bytes)
            )),
        ]),
    ));

    // ---- pressure, io, net
    let mut cc: Pl = vec![];
    cc.push((2, Line::from(vec![head("PSI "), plain("avg10 some / full   trend (20 frames)")])));
    for (lbl, r, has_full) in [("cpu", "cpu", false), ("mem", "mem", true), ("io ", "io", true)] {
        let some = s.h(&format!("host_psi_{r}_some_avg10"));
        let full = if has_full { s.h(&format!("host_psi_{r}_full_avg10")) } else { None };
        let t = trend(a, &format!("host_psi_{r}_some_avg10"), 20);
        let st = band_style(band(some.unwrap_or(0.0), [2.0, 10.0, 30.0]));
        cc.push((
            2,
            Line::from(vec![
                plain(format!("{lbl} ")),
                sp(format!("{:>6}", opt(some, fmt_pct)), st),
                plain(" / "),
                sp(format!("{:<6}", opt(full, fmt_pct)), band_style(band(full.unwrap_or(0.0), [1.0, 5.0, 15.0]))),
                sp(spark(&t, 20, 5.0), st),
            ]),
        ));
    }
    for b in s.blocks.iter().take(3) {
        cc.push((
            3,
            Line::from(vec![
                head("IO "),
                plain(format!(
                    "{} r {} w {}  {}/{} iops  await n/a util n/a",
                    b.name,
                    opt(b.rbps, fmt_bytes),
                    opt(b.wbps, fmt_bytes),
                    opt(b.riops, fmt_count),
                    opt(b.wiops, fmt_count)
                )),
            ]),
        ));
    }
    cc.push((4, Line::from(vec![head("IO.COST "), plain("vrate n/a (not in this recording)")])));
    for n in s.nets.iter().take(2) {
        cc.push((3, Line::from(vec![head("NET "), plain(format!("{} rx {} tx {}", n.name, opt(n.rx, fmt_bytes), opt(n.tx, fmt_bytes)))])));
    }
    (ca, cb, cc)
}

fn draw_meters(f: &mut Frame, a: &App, r: Rect) {
    if r.height == 0 {
        return;
    }
    let n = r.height as usize;
    let cols = if r.width >= 150 { 3 } else if r.width >= 100 { 2 } else { 1 };
    let w_each = (r.width as usize / cols).saturating_sub(2);
    let (ca, cb, cc) = meter_lines(a, w_each);
    let sets: Vec<Pl> = match cols {
        3 => vec![ca, cb, cc],
        2 => vec![ca, cb.into_iter().chain(cc).collect()],
        _ => vec![ca.into_iter().chain(cb).chain(cc).map(|(p, l)| (p, l)).collect()],
    };
    let rects = Layout::horizontal(vec![Constraint::Ratio(1, cols as u32); cols]).spacing(2).split(r);
    for (set, rc) in sets.into_iter().zip(rects.iter()) {
        f.render_widget(Paragraph::new(fit(set, n)), *rc);
    }
}

// ---------------------------------------------------------------- tabs / table

fn draw_info(f: &mut Frame, a: &mut App, r: Rect, table_r: Rect) {
    let mut spans = vec![];
    let noise = match a.noise {
        Noise::All => "all",
        Noise::Middle => "middle",
        Noise::Hot => "hot",
    };
    spans.push(plain(format!(" noise:{noise} ({} hidden)", a.hidden)));
    spans.push(plain(format!("  sort:{}{}", cols::head_of(a.sort), if a.desc { "\u{25bc}" } else { "\u{25b2}" })));
    if !a.filter.is_empty() {
        spans.push(sp(format!("  filter:\"{}\"", a.filter), Style::new().fg(Color::Yellow)));
    }
    if a.flat {
        spans.push(demo_sp("  FLAT process ranking (demo)"));
    }
    let p = cols::plan(&a.cols, table_r.width);
    let right = format!("cols {}/{} ({} dropped)  rows {}", p.cols.len(), p.total, p.dropped, a.rows.len());
    let rw = right.chars().count() as u16;
    let lw = spans.iter().map(|s| s.content.chars().count() as u16).sum::<u16>();
    f.render_widget(Paragraph::new(Line::from(spans)), r);
    if r.width > lw + rw + 2 {
        f.render_widget(Paragraph::new(sp(right, dim())).alignment(Alignment::Right), r);
    }
}

fn pad(s: &str, w: usize, left: bool) -> String {
    let t = trunc(s, w);
    let n = t.chars().count();
    if left { format!("{t}{}", " ".repeat(w - n)) } else { format!("{}{t}", " ".repeat(w - n)) }
}

fn name_style(e: &Ent) -> Style {
    match e.kind.as_str() {
        "slice" | "root" => Style::new().fg(Color::White).add_modifier(Modifier::BOLD),
        "scope" => Style::new().fg(Color::LightBlue),
        "service" => Style::new().fg(Color::LightGreen),
        _ => Style::new().fg(Color::Gray),
    }
}

fn draw_table(f: &mut Frame, a: &mut App, r: Rect) {
    let plan = cols::plan(&a.cols, r.width);
    a.vis_rows = (r.height as usize).saturating_sub(1);
    a.ensure_visible();

    // header + hit rectangles
    let mut heads = vec![];
    let mut x = r.x;
    let hcells: Vec<Line> = plan
        .cols
        .iter()
        .map(|c| {
            let w = if c.id == ColId::Name { plan.name_w } else { c.w };
            heads.push((Rect::new(x, r.y, w, 1), c.id));
            x += w + 1;
            let mut t = c.head.to_string();
            if a.sort == c.id {
                t.push(if a.desc { '\u{25bc}' } else { '\u{25b2}' });
            }
            let left = matches!(c.id, ColId::Name | ColId::Origin | ColId::DriftList | ColId::Warn);
            let st = if a.sort == c.id {
                Style::new().fg(Color::Black).bg(Color::Yellow).add_modifier(Modifier::BOLD)
            } else if c.demo {
                Style::new().fg(Color::Magenta).add_modifier(Modifier::BOLD)
            } else {
                Style::new().fg(Color::Yellow).add_modifier(Modifier::BOLD)
            };
            Line::from(sp(pad(&t, w as usize, left), st))
        })
        .collect();
    a.hit.heads = heads;
    a.hit.body = Rect::new(r.x, r.y + 1, r.width, r.height.saturating_sub(1));

    let s = &a.snaps[a.fi];
    let end = (a.scroll + a.vis_rows).min(a.rows.len());
    let mut rows = vec![];
    for ri in a.scroll..end {
        let row = a.rows[ri];
        let mut cells: Vec<Line> = vec![];
        for c in &plan.cols {
            let w = if c.id == ColId::Name { plan.name_w } else { c.w } as usize;
            let line = match (row.kind, c.id) {
                (RowKind::Cg(i), ColId::Name) => {
                    let e = &s.ents[i];
                    let glyph = match (row.has_kids, row.open) {
                        (true, true) => "\u{25be} ",
                        (true, false) => "\u{25b8} ",
                        _ => "  ",
                    };
                    let ind = " ".repeat(row.depth as usize * 2);
                    let used = ind.chars().count() + 2;
                    let mark = if e.findings.iter().any(|x| x.severity != "info") { " !" } else { "" };
                    let nm = trunc(&e.name, w.saturating_sub(used + mark.len()));
                    let mut v = vec![plain(ind), sp(glyph, Style::new().fg(Color::Cyan)), sp(nm, name_style(e))];
                    if !mark.is_empty() {
                        v.push(sp(mark, Style::new().fg(Color::Yellow).add_modifier(Modifier::BOLD)));
                    }
                    Line::from(v)
                }
                (RowKind::Cg(i), id) => {
                    let (t, st) = cols::cell(id, s, &a.blk, i);
                    let left = matches!(id, ColId::Origin | ColId::DriftList | ColId::Warn);
                    Line::from(sp(pad(&t, w, left), st))
                }
                (RowKind::Proc(i, p) | RowKind::Flat(i, p), ColId::Name) => {
                    let pr = &a.procs[i][p];
                    let ind = " ".repeat(row.depth as usize * 2);
                    let owner = if matches!(row.kind, RowKind::Flat(..)) { format!("  \u{2039}{}\u{203a}", s.ents[i].name) } else { String::new() };
                    let t = format!("{} {} {} demo{}", pr.state, pr.comm, pr.pid, owner);
                    Line::from(vec![plain(ind), sp(trunc(&t, w.saturating_sub(row.depth as usize * 2)), demo_style_for(pr.state))])
                }
                (RowKind::Proc(i, p) | RowKind::Flat(i, p), ColId::Cpu) => {
                    Line::from(sp(pad(&fmt_pct(a.procs[i][p].cpu), w, false), demo_style_for(a.procs[i][p].state)))
                }
                (RowKind::Proc(i, p) | RowKind::Flat(i, p), ColId::Blk) => {
                    let st = a.procs[i][p].state;
                    Line::from(sp(pad(if st == 'D' { "D" } else { "" }, w, false), Style::new().fg(Color::Magenta).add_modifier(Modifier::BOLD)))
                }
                _ => Line::from(""),
            };
            cells.push(line);
        }
        let mut rw = Row::new(cells);
        if ri == a.cursor {
            rw = rw.style(Style::new().bg(SEL_BG));
        }
        rows.push(rw);
    }
    let widths: Vec<Constraint> = plan.cols.iter().map(|c| Constraint::Length(if c.id == ColId::Name { plan.name_w } else { c.w })).collect();
    let t = Table::new(rows, widths).header(Row::new(hcells)).column_spacing(1);
    f.render_widget(t, r);
    if a.rows.is_empty() {
        f.render_widget(Paragraph::new(sp("no rows match (noise level / filter)", dim())), Rect::new(r.x, r.y + 2, r.width, 1));
    }
}

fn demo_style_for(state: char) -> Style {
    if state == 'D' {
        Style::new().fg(Color::Magenta).add_modifier(Modifier::BOLD)
    } else {
        Style::new().fg(Color::Magenta)
    }
}

// ---------------------------------------------------------------- side pane

fn lim_val(name: &str, c: Cn) -> String {
    match c {
        Cn::Unset => "\u{b7} (unset)".into(),
        Cn::NA => "n/a".into(),
        Cn::Val(v) if name.ends_with("weight") => format!("{v:.0}"),
        Cn::Val(v) => fmt_bytes(v),
    }
}

fn kv(k: &str, v: impl Into<String>) -> Line<'static> {
    Line::from(vec![sp(format!("{k:<9}"), dim()), plain(v)])
}

fn pane_lines(a: &App, i: usize, snap: &Snap) -> Vec<Line<'static>> {
    let e = &snap.ents[i];
    let mut l: Vec<Line<'static>> = vec![];
    l.push(Line::from(sp(if e.key.is_empty() { "/ (host root)".to_string() } else { e.key.clone() }, Style::new().add_modifier(Modifier::BOLD))));
    l.push(kv("kind", format!("{}  tier {}  protected {}", e.kind, e.tier.as_deref().unwrap_or("\u{b7}"), if e.protected { "yes" } else { "no" })));
    if let Some(d) = &e.docker_name {
        l.push(kv("docker", format!("{d}  {}", e.image.as_deref().unwrap_or(""))));
    }
    if let Some(c) = &e.ciu_stack {
        l.push(kv("ciu", c.clone()));
    }
    if let Some(u) = &e.unit {
        l.push(kv("unit", u.clone()));
    }
    l.push(Line::from(""));
    l.push(Line::from(head("CONFIG CHAIN  live  <- set (origin)")));
    let order = ["cpu_weight", "io_weight", "mem_min", "mem_low", "mem_high", "mem_max"];
    for n in order {
        if let Some(lim) = e.limits.iter().find(|x| x.name == n) {
            let live = lim_val(n, lim.live.cn());
            let set = lim.rec_val.map_or(String::new(), |v| format!(" <- {}", lim_val(n, Cn::Val(v))));
            let org = lim.rec_origin.clone().unwrap_or_else(|| lim.origin.clone());
            let st = if lim.drift { Style::new().fg(Color::Yellow) } else { Style::new().fg(Color::Gray) };
            l.push(Line::from(vec![sp(format!("{n:<9} "), dim()), sp(format!("{live}{set}  ({org})"), st), if lim.drift { sp(" DRIFT", Style::new().fg(Color::Red).add_modifier(Modifier::BOLD)) } else { plain("") }]));
            if lim.drift {
                l.push(Line::from(sp(format!("  {}", lim.reason), Style::new().fg(Color::Yellow))));
            }
        }
    }
    l.push(Line::from(head("EFFECTIVE memory.min")));
    l.push(Line::from(plain(format!("{}", e.eff_reason))));
    if !e.eff_path.is_empty() {
        let chain: Vec<String> = e
            .eff_path
            .iter()
            .map(|(k, v)| format!("{}={}", k.rsplit('/').next().filter(|x| !x.is_empty()).unwrap_or("/"), v.map_or("n/a".into(), fmt_bytes)))
            .collect();
        l.push(Line::from(sp(format!("chain: {}", chain.join(" > ")), dim())));
    }
    for w in cols::warnings(e) {
        l.push(Line::from(sp(format!("! {w}"), Style::new().fg(Color::Yellow))));
    }
    for fi in &e.findings {
        l.push(Line::from(sp(format!("[{}] {}", fi.severity, fi.message), Style::new().fg(Color::Yellow))));
        if let Some(r) = &fi.remedy {
            l.push(Line::from(sp(format!("  fix: {r}"), dim())));
        }
    }
    l.push(Line::from(""));
    l.push(Line::from(head("USE")));
    let g = |k: &str, f: &dyn Fn(f64) -> String| match e.cn(k) {
        Cn::Val(v) => f(v),
        Cn::Unset => "\u{b7}".into(),
        Cn::NA => "n/a".into(),
    };
    l.push(kv("cpu", format!("{}  throttled {}  quota {}", g("cpu_pct", &fmt_pct), g("cpu_throttled_pct", &fmt_pct), cols::cell(ColId::CpuQuota, snap, &a.blk, i).0)));
    l.push(kv("memory", format!("ram {}  anon {}  file {}", g("ram", &fmt_bytes), g("anon", &fmt_bytes), g("file", &fmt_bytes))));
    l.push(kv("", format!("shmem {}  swap {}  zswap {}", g("shmem", &fmt_bytes), g("swap_disk", &fmt_bytes), g("z_pool", &fmt_bytes))));
    l.push(kv("headroom", format!("to high {}  to max {}", g("headroom_high_pct", &fmt_pct), g("headroom_max_pct", &fmt_pct))));
    l.push(kv("psi", format!("c {}/{}  m {}/{}  i {}/{}", g("psi_cpu_some_avg10", &fmt_pct), g("psi_cpu_full_avg10", &fmt_pct), g("psi_mem_some_avg10", &fmt_pct), g("psi_mem_full_avg10", &fmt_pct), g("psi_io_some_avg10", &fmt_pct), g("psi_io_full_avg10", &fmt_pct))));
    l.push(kv("io", format!("r {}  w {}  iops {}/{}", g("io_r_bps", &fmt_bytes), g("io_w_bps", &fmt_bytes), g("io_r_iops", &fmt_count), g("io_w_iops", &fmt_count))));
    l.push(kv("net", format!("rx {}  tx {}  ({})", g("net_rx_bps", &fmt_bytes), g("net_tx_bps", &fmt_bytes), e.net_label.as_deref().unwrap_or("n/a"))));
    l.push(kv("pids", format!("{} of {}", g("pids_current", &|v| format!("{v:.0}")), g("pids_max", &|v| format!("{v:.0}")))));
    l.push(kv("reclaim", format!("scan {}/s  steal {}/s  rf d/f {}/{}", g("pgscan_per_s", &fmt_count), g("pgsteal_per_s", &fmt_count), g("rf_d_per_s", &fmt_count), g("rf_f_per_s", &fmt_count))));
    l.push(kv("events", format!("high {}/s max {}/s oom {}/s", g("mem_events_high_per_s", &fmt_count), g("mem_events_max_per_s", &fmt_count), g("mem_events_oom_kill_per_s", &fmt_count))));
    let procs = &a.procs[i];
    if !procs.is_empty() {
        l.push(Line::from(""));
        l.push(Line::from(demo_sp(format!("PROCESSES (demo), D-state {}", a.blk[i]))));
        for p in procs.iter().take(6) {
            l.push(Line::from(demo_sp(format!("  {} {:<14} {:>6} {}", p.state, p.comm, p.pid, fmt_pct(p.cpu)))));
        }
    }
    l
}

fn draw_pane(f: &mut Frame, a: &App, r: Rect) {
    let Some(i) = a.selected_ent() else { return };
    let snap = a.snap();
    let title = format!(" {} ", trunc(&snap.ents[i].name, r.width.saturating_sub(4) as usize));
    let blk = Block::new().borders(Borders::LEFT).border_style(dim()).title(sp(title, Style::new().fg(Color::Cyan).add_modifier(Modifier::BOLD)));
    let p = Paragraph::new(pane_lines(a, i, snap)).block(blk).wrap(Wrap { trim: false });
    f.render_widget(p, r);
}

// ---------------------------------------------------------------- status / footer / help

fn draw_status(f: &mut Frame, a: &App, r: Rect) {
    let s = a.snap();
    let t = (s.ts % 86400.0) as u64;
    let state = if a.frozen { sp(" FROZEN ", Style::new().fg(Color::Black).bg(Color::Yellow).add_modifier(Modifier::BOLD)) } else { sp(" replay ", Style::new().fg(Color::Black).bg(Color::Green)) };
    let mut parts = vec![
        state,
        plain(format!(" {} frame {}/{} {:02}:{:02}:{:02}Z tick {}ms ", a.host_id, a.fi, a.snaps.len() - 1, t / 3600, t % 3600 / 60, t % 60, a.tick_ms)),
        sp(
            format!("| fps {:.1} cpu {:.1}% draw {:.2}ms rss {}M ", a.perf.fps, a.perf.cpu_pct, a.perf.draw_ms, crate::app::rss_kb() / 1024),
            Style::new().fg(Color::Cyan),
        ),
        plain("| "),
        demo_sp("~ magenta = synthetic demo data"),
    ];
    if !a.msg.is_empty() {
        parts.push(plain(format!("  {}", a.msg)));
    }
    f.render_widget(Paragraph::new(Line::from(parts)).style(Style::new().bg(BAR_BG)), r);
}

fn draw_footer(f: &mut Frame, a: &App, r: Rect) {
    if a.filter_edit {
        let l = Line::from(vec![sp("/", Style::new().fg(Color::Yellow)), plain(a.filter.clone()), sp("\u{2588}", Style::new().fg(Color::Yellow)), sp("   Enter apply   Esc clear", dim())]);
        f.render_widget(Paragraph::new(l), r);
        return;
    }
    let keys = [("q", "quit"), ("?", "help"), ("n", "noise"), ("/", "filter"), ("s/S", "sort"), ("Tab", "pane"), ("space", "freeze"), (",/.", "step"), ("\u{2190}\u{2192}", "fold"), ("g", "flat"), ("p", "procs"), ("e/c", "all")];
    let mut spans = vec![];
    let mut used = 0;
    for (k, d) in keys {
        let w = k.chars().count() + d.len() + 2;
        if used + w > r.width as usize {
            break;
        }
        used += w;
        spans.push(sp(k, Style::new().fg(Color::Black).bg(Color::Gray)));
        spans.push(plain(format!(" {d} ")));
    }
    f.render_widget(Paragraph::new(Line::from(spans)), r);
}

fn draw_help(f: &mut Frame, area: Rect) {
    let lines = [
        "topos ratatui PoC   (any key closes)",
        "",
        "  One view: usage and configuration share the row; columns come from the config file",
        "  n            row noise: all / middle (default) / hot only",
        "  / text       filter (name, path, image); Enter keeps, Esc clears",
        "  s  S         cycle sort column / reverse; click a header to sort",
        "  Tab          side pane (auto below 140 cols; Tab forces it on/off)",
        "  space        freeze; ,  . step back / forward one frame",
        "  arrows jk    move; Right/Enter expand, Left collapse / go to parent",
        "  e  c         expand all / collapse to top level",
        "  p  g         demo processes on/off; flat process ranking",
        "  mouse        wheel scrolls, click selects, click the arrow folds",
        "  q  Ctrl-C    quit",
        "",
        "Meaning of cells:  \u{b7} = not set (no limit)    n/a = not available",
        "  0 = real zero.  Colour bands: grey ok, yellow warn, orange high, red critical.",
        "Magenta / '~' / 'demo' = SYNTHETIC (per-CPU, us/sy, D state, processes,",
        "  zswap rates): the recording has no such data; only for judging layout.",
    ];
    let w = 82.min(area.width);
    let h = (lines.len() as u16 + 2).min(area.height);
    let r = Rect::new(area.x + (area.width - w) / 2, area.y + (area.height - h) / 2, w, h);
    f.render_widget(Clear, r);
    let text: Vec<Line> = lines.iter().map(|s| Line::from(plain(*s))).collect();
    f.render_widget(Paragraph::new(text).block(Block::bordered().border_style(Style::new().fg(Color::Cyan)).title(" help ")), r);
}
