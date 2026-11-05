//! Column model: fixed position + priority per column, priority-based dropping;
//! threshold-banded cell formatting.
use crate::data::{Cn, Ent, Snap};
use ratatui::style::{Color, Modifier, Style};

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum ColId {
    Name, Cpu, Thr, RamCeil, Prot, Zsw, RfD, RfF, PsiM, PsiI, PsiC, PsiCf, PsiMf, PsiIf, IoR, IoW, IopsR, IopsW,
    Stall, Blk, NetRx, NetTx, Pids, Anon, File, Shmem, Swap, Pgscan, Pgsteal, IoCost, Origin, Drift,
    CpuWt, CpuQuota, MemMin, MemLow, MemHigh, MemMax, EffMin, IoWt, PidsMax, DriftList, Warn,
    EvHigh, EvMax, EvOom, SwapMax, Ratio, RfZ, IoMax, Maj,
}

/// Settings from the optional `[ui]` table of the same file (Textual keeps these in its own `[ui]` config).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct UiCfg {
    /// Side pane width in columns at any terminal width (capped to half the screen).
    pub pane_width: u16,
    /// The pane shows by itself from this terminal width.
    pub pane_auto_min_width: u16,
    /// Hot or blocked process rows shown under a collapsed cgroup.
    pub proc_cap: usize,
}

impl Default for UiCfg {
    fn default() -> UiCfg {
        UiCfg { pane_width: 46, pane_auto_min_width: 140, proc_cap: 3 }
    }
}

#[derive(Clone, Copy)]
pub struct Col {
    pub id: ColId,
    pub head: &'static str,
    pub w: u16,
    pub pri: u8,
    pub demo: bool,
}

/// How each column id renders: config-file name, default header and width. Order and priority are NOT here.
const REGISTRY: &[(&str, ColId, &str, u16, bool)] = &[
    ("name", ColId::Name, "NAME", 0, false), ("cpu", ColId::Cpu, "CPU", 5, false), ("thr", ColId::Thr, "THR", 4, false),
    ("ram_ceil", ColId::RamCeil, "RAM/CEIL", 17, false), ("prot", ColId::Prot, "PROT", 6, false), ("zsw", ColId::Zsw, "ZSW", 5, false),
    ("rf_d", ColId::RfD, "RF_D", 6, false), ("rf_f", ColId::RfF, "RF_F", 6, false),
    ("psi_m", ColId::PsiM, "PSI_M", 5, false), ("psi_i", ColId::PsiI, "PSI_I", 5, false), ("psi_c", ColId::PsiC, "PSI_C", 5, false),
    ("psi_mf", ColId::PsiMf, "PSI_MF", 6, false), ("psi_if", ColId::PsiIf, "PSI_IF", 6, false), ("psi_cf", ColId::PsiCf, "PSI_CF", 6, false),
    ("io_r", ColId::IoR, "IO_R", 6, false), ("io_w", ColId::IoW, "IO_W", 6, false),
    ("iops_r", ColId::IopsR, "IOPS_R", 6, false), ("iops_w", ColId::IopsW, "IOPS_W", 6, false),
    ("stall", ColId::Stall, "STALL", 5, false), ("blk", ColId::Blk, "BLK~", 4, true),
    ("net_rx", ColId::NetRx, "NET_RX", 6, false), ("net_tx", ColId::NetTx, "NET_TX", 6, false),
    ("pids", ColId::Pids, "PIDS", 5, false), ("anon", ColId::Anon, "ANON", 5, false), ("file", ColId::File, "FILE", 5, false),
    ("shmem", ColId::Shmem, "SHMEM", 5, false), ("swap", ColId::Swap, "SWAP", 5, false),
    ("pgscan", ColId::Pgscan, "PGSCAN", 6, false), ("pgsteal", ColId::Pgsteal, "PGSTEAL", 7, false),
    ("iocost", ColId::IoCost, "IOCOST", 6, false), ("origin", ColId::Origin, "ORIGIN", 8, false), ("drift", ColId::Drift, "DRIFT", 5, false),
    ("cpu_wt", ColId::CpuWt, "CPU.WT", 6, false), ("quota", ColId::CpuQuota, "QUOTA", 6, false),
    ("mem_min", ColId::MemMin, "MEM.MIN", 7, false), ("mem_low", ColId::MemLow, "MEM.LOW", 7, false),
    ("mem_high", ColId::MemHigh, "MEM.HIGH", 8, false), ("mem_max", ColId::MemMax, "MEM.MAX", 7, false),
    ("eff_min", ColId::EffMin, "EFF.MIN", 7, false), ("io_wt", ColId::IoWt, "IO.WT", 5, false),
    ("pids_max", ColId::PidsMax, "PIDS.MAX", 8, false), ("drift_list", ColId::DriftList, "DRIFTED", 22, false),
    ("warn", ColId::Warn, "INCONSISTENCY", 30, false),
    ("ev_high", ColId::EvHigh, "EV_HIGH", 7, false), ("ev_max", ColId::EvMax, "EV_MAX", 6, false), ("ev_oom", ColId::EvOom, "EV_OOM", 6, false),
    ("swap_max", ColId::SwapMax, "SWAP.MAX", 8, false), ("ratio", ColId::Ratio, "RATIO", 5, false), ("rf_z", ColId::RfZ, "RF_Z", 6, false),
    ("io_max", ColId::IoMax, "IO.MAX", 9, false), ("maj", ColId::Maj, "MAJ", 5, false),
];

pub fn column_ids() -> String {
    REGISTRY.iter().map(|r| r.0).collect::<Vec<_>>().join(", ")
}

pub fn head_of(id: ColId) -> &'static str {
    REGISTRY.iter().find(|r| r.1 == id).map_or("name", |r| r.2)
}

/// The shipped example; also the fallback when no config file is found.
pub const EXAMPLE: &str = include_str!("../columns.example.toml");

/// Parse the column config (a TOML subset: `[[column]]` tables with `id`, `pri`, and optional `width`/`head`).
/// File order is display order; `pri` 1 is kept longest.
pub fn parse_config(text: &str) -> Result<Vec<Col>, String> {
    parse_full(text).map(|x| x.0)
}

/// Columns plus the optional `[ui]` table (`pane_width`, `pane_auto_min_width`, `proc_cap`).
pub fn parse_full(text: &str) -> Result<(Vec<Col>, UiCfg), String> {
    let mut ui = UiCfg::default();
    let mut in_ui = false;
    let mut out: Vec<Col> = vec![];
    let mut cur: Option<(Option<String>, Option<u8>, Option<u16>, Option<String>)> = None;
    let mut flush = |cur: &mut Option<(Option<String>, Option<u8>, Option<u16>, Option<String>)>, out: &mut Vec<Col>| -> Result<(), String> {
        if let Some((id, pri, w, head)) = cur.take() {
            let id = id.ok_or("a [[column]] has no id")?;
            let r = REGISTRY.iter().find(|r| r.0 == id).ok_or(format!("unknown column id \"{id}\"; known: {}", column_ids()))?;
            if out.iter().any(|c| c.id == r.1) {
                return Err(format!("column \"{id}\" listed twice"));
            }
            let head: &'static str = head.map_or(r.2, |h| Box::leak(h.into_boxed_str()));
            out.push(Col { id: r.1, head, w: w.unwrap_or(r.3), pri: pri.ok_or(format!("column \"{id}\" has no pri"))?, demo: r.4 });
        }
        Ok(())
    };
    for (n, raw) in text.lines().enumerate() {
        let line = raw.split('#').next().unwrap_or("").trim();
        if line.is_empty() {
            continue;
        }
        if line == "[[column]]" {
            flush(&mut cur, &mut out)?;
            cur = Some((None, None, None, None));
            in_ui = false;
            continue;
        }
        if line == "[ui]" {
            flush(&mut cur, &mut out)?;
            in_ui = true;
            continue;
        }
        let (k, v) = line.split_once('=').ok_or(format!("line {}: expected key = value", n + 1))?;
        let (k, v) = (k.trim(), v.trim().trim_matches('"'));
        if in_ui {
            let num = |what: &str| v.parse::<u16>().map_err(|_| format!("line {}: bad number for {what}", n + 1));
            match k {
                "pane_width" => ui.pane_width = num(k)?,
                "pane_auto_min_width" => ui.pane_auto_min_width = num(k)?,
                "proc_cap" => ui.proc_cap = num(k)? as usize,
                _ => return Err(format!("line {}: unknown [ui] key {k}", n + 1)),
            }
            continue;
        }
        let c = cur.as_mut().ok_or(format!("line {}: key outside [[column]]", n + 1))?;
        let bad = |_| format!("line {}: bad number for {k}", n + 1);
        match k {
            "id" => c.0 = Some(v.to_string()),
            "pri" => c.1 = Some(v.parse().map_err(bad)?),
            "width" => c.2 = Some(v.parse().map_err(bad)?),
            "head" => c.3 = Some(v.to_string()),
            _ => return Err(format!("line {}: unknown key {k}", n + 1)),
        }
    }
    flush(&mut cur, &mut out)?;
    if out.first().map(|c| c.id) != Some(ColId::Name) {
        return Err("the first column must be \"name\"".into());
    }
    Ok((out, ui))
}

pub struct Plan {
    pub cols: Vec<Col>,
    pub name_w: u16,
    pub dropped: usize,
    pub total: usize,
}

pub const NAME_MIN: u16 = 30;
pub const NAME_MAX: u16 = 56;

/// Keep columns in priority order while they fit; the survivors keep their fixed order.
/// The first column that does not fit ends the plan, so lower-priority columns never
/// jump ahead of a higher-priority one. NAME is always kept and takes the leftover.
pub fn plan(all: &[Col], avail: u16) -> Plan {
    let mut order: Vec<usize> = (0..all.len()).collect();
    order.sort_by_key(|&i| (all[i].pri, i));
    let mut keep = vec![false; all.len()];
    let mut used = NAME_MIN.min(avail);
    for &i in &order {
        if all[i].id == ColId::Name {
            keep[i] = true;
        } else if used + 1 + all[i].w <= avail {
            used += 1 + all[i].w;
            keep[i] = true;
        } else {
            break;
        }
    }
    let name_w = (NAME_MIN.min(avail) + avail - used).min(NAME_MAX);
    let cols: Vec<Col> = all.iter().zip(keep).filter(|(_, k)| *k).map(|(c, _)| *c).collect();
    Plan { dropped: all.len() - cols.len(), total: all.len(), cols, name_w }
}

// ---------- formatting ----------

pub fn fmt_bytes(v: f64) -> String {
    let units = ["B", "K", "M", "G", "T", "P"];
    let (mut x, mut u) = (v.abs(), 0);
    while x >= 1024.0 && u < 5 {
        x /= 1024.0;
        u += 1;
    }
    if u == 0 || x >= 10.0 {
        format!("{:.0}{}", x, units[u])
    } else {
        format!("{:.1}{}", x, units[u])
    }
}

pub fn fmt_count(v: f64) -> String {
    let (x, suf) = if v >= 1e6 { (v / 1e6, "M") } else if v >= 1e3 { (v / 1e3, "k") } else { (v, "") };
    if x >= 10.0 || suf.is_empty() && v >= 10.0 {
        format!("{:.0}{}", x, suf)
    } else {
        format!("{:.1}{}", x, suf)
    }
}

pub fn fmt_pct(v: f64) -> String {
    if v >= 10.0 { format!("{:.0}%", v) } else { format!("{:.1}%", v) }
}

// ---------- threshold bands: 0 zero, 1 low, 2 warn, 3 high, 4 critical ----------

pub fn band(v: f64, t: [f64; 3]) -> u8 {
    if v <= 0.0 { 0 } else if v < t[0] { 1 } else if v < t[1] { 2 } else if v < t[2] { 3 } else { 4 }
}

pub fn dim() -> Style {
    Style::new().fg(Color::DarkGray)
}
pub fn na_style() -> Style {
    Style::new().fg(Color::DarkGray).add_modifier(Modifier::ITALIC)
}
pub fn demo_style(zero: bool) -> Style {
    Style::new().fg(if zero { Color::Rgb(110, 60, 110) } else { Color::Magenta })
}
pub fn band_style(b: u8) -> Style {
    match b {
        0 => dim(),
        1 => Style::new().fg(Color::Gray),
        2 => Style::new().fg(Color::Yellow),
        3 => Style::new().fg(Color::Indexed(208)),
        _ => Style::new().fg(Color::Red).add_modifier(Modifier::BOLD),
    }
}

pub type Cell = (String, Style);

fn unset() -> Cell {
    ("\u{b7}".into(), dim())
}
fn na() -> Cell {
    ("n/a".into(), na_style())
}

fn numcell(c: Cn, f: impl Fn(f64) -> (String, u8)) -> Cell {
    match c {
        Cn::Unset => unset(),
        Cn::NA => na(),
        Cn::Val(v) => {
            let (s, b) = f(v);
            (s, band_style(b))
        }
    }
}

fn bytes_cell(c: Cn, t: [f64; 3]) -> Cell {
    numcell(c, |v| (if v == 0.0 { "0".into() } else { fmt_bytes(v) }, band(v, t)))
}
fn count_cell(c: Cn, t: [f64; 3]) -> Cell {
    numcell(c, |v| (if v == 0.0 { "0".into() } else { fmt_count(v) }, band(v, t)))
}
fn pct_cell(c: Cn, t: [f64; 3]) -> Cell {
    numcell(c, |v| (if v == 0.0 { "0".into() } else { fmt_pct(v) }, band(v, t)))
}
/// Configured limit/protection: 0 or unlimited means "not set" and renders as a dim dot.
fn cfg_bytes(c: Cn, style: Style) -> Cell {
    match c {
        Cn::Val(v) if v > 0.0 => (fmt_bytes(v), style),
        Cn::NA => na(),
        _ => unset(),
    }
}

pub fn gauge(frac: f64, w: usize) -> String {
    const P: [char; 8] = [' ', '\u{258f}', '\u{258e}', '\u{258d}', '\u{258c}', '\u{258b}', '\u{258a}', '\u{2589}'];
    let eighths = (frac.clamp(0.0, 1.0) * (w * 8) as f64).round() as usize;
    let mut s = String::new();
    for i in 0..w {
        let e = eighths.saturating_sub(i * 8).min(8);
        s.push(if e >= 8 { '\u{2588}' } else { P[e] });
    }
    s
}

pub fn ceil_of(e: &Ent) -> Cn {
    match e.cn("mem_high") {
        Cn::Val(v) => Cn::Val(v),
        _ => e.cn("mem_max"),
    }
}

pub fn max_psi(e: &Ent, which: &str) -> Option<f64> {
    ["cpu", "mem", "io"].iter().filter_map(|r| e.num(&format!("psi_{r}_{which}_avg10"))).fold(None, |a, v| Some(a.map_or(v, |x: f64| x.max(v))))
}

fn origin_short(o: &str) -> &str {
    match o {
        "systemd_unit" => "unit",
        "systemd_runtime_dropin" => "rt-drop",
        "raw_write" => "RAW",
        "docker_default" => "docker",
        "unset" | "" => "\u{b7}",
        x => x,
    }
}

/// Inconsistencies between set values and effective values.
pub fn warnings(e: &Ent) -> Vec<String> {
    let mut w = vec![];
    let v = |k: &str| e.num(k).filter(|x| *x > 0.0);
    if let (Some(h), Some(m)) = (v("mem_high"), v("mem_max")) {
        if h > m {
            w.push("high>max".to_string());
        }
    }
    if let (Some(l), Some(h)) = (v("mem_low"), v("mem_high")) {
        if l > h {
            w.push("low>high".to_string());
        }
    }
    if let (Some(mn), Some(ef)) = (v("mem_min"), e.num("effective_memory_min")) {
        if ef < mn {
            w.push(format!("min clamped to {}", fmt_bytes(ef)));
        }
    }
    if e.drift {
        w.push("drift".into());
    }
    if e.mv("io_max_capped").v == Some(1.0) && e.n0("io_cap_saturation_pct") > 80.0 {
        w.push("io cap saturated".into());
    }
    let n = e.findings.len();
    if n > 0 {
        w.push(format!("{n} finding{}", if n > 1 { "s" } else { "" }));
    }
    w
}

pub fn cell(id: ColId, s: &Snap, blk: &[Option<u32>], i: usize) -> Cell {
    use ColId::*;
    let e = &s.ents[i];
    // the sampler does not report where a limit was set, so governance columns have no source live
    if s.live.is_some() && matches!(id, Origin | Drift | DriftList) {
        return na();
    }
    match id {
        SwapMax => cfg_bytes(e.cn("swap_max"), Style::new().fg(Color::Yellow)),
        Ratio => numcell(e.cn("ratio"), |v| (format!("{v:.1}x"), 1)),
        RfZ => count_cell(e.cn("rf_z_per_s"), [1.0, 50.0, 500.0]),
        Maj => count_cell(e.cn("maj_per_s"), [1.0, 50.0, 500.0]),
        IoMax => match e.cn("io_max_capped") {
            Cn::Val(c) if c == 1.0 => match e.num("io_cap_saturation_pct") {
                Some(sat) => (format!("cap {sat:.0}%"), band_style(band(sat, [50.0, 80.0, 95.0]))),
                None => ("cap".into(), Style::new().fg(Color::Yellow)),
            },
            Cn::Val(_) => unset(),
            _ => na(),
        },
        Name => (e.name.clone(), Style::new()),
        Cpu => numcell(e.cn("cpu_pct"), |v| (fmt_pct(v), band(v, [20.0, 80.0, 150.0]))),
        Thr => pct_cell(e.cn("cpu_throttled_pct"), [1.0, 10.0, 30.0]),
        RamCeil => match e.cn("ram") {
            Cn::Val(r) => {
                let (cs, frac) = match ceil_of(e) {
                    Cn::Val(c) if c > 0.0 => (fmt_bytes(c), Some(r / c)),
                    _ => ("\u{b7}".to_string(), None),
                };
                let g = frac.map_or(" \u{b7}   ".to_string(), |f| gauge(f, 5));
                let b = frac.map_or(1, |f| band(f * 100.0, [60.0, 85.0, 95.0]).max(1));
                (format!("{:>5}/{:<5} {}", fmt_bytes(r), cs, g), band_style(b))
            }
            _ => na(),
        },
        Prot => match (e.num("effective_memory_min"), e.num("mem_min"), e.num("mem_low")) {
            (_, Some(m), _) if m > 0.0 => (format!("m{}", fmt_bytes(m)), Style::new().fg(Color::Cyan)),
            (_, _, Some(l)) if l > 0.0 => (format!("l{}", fmt_bytes(l)), Style::new().fg(Color::Cyan)),
            (None, None, None) => na(),
            _ => unset(),
        },
        Zsw => bytes_cell(e.cn("z_pool"), [1e8, 1e9, 4e9]),
        RfD => count_cell(e.cn("rf_d_per_s"), [1.0, 50.0, 500.0]),
        RfF => count_cell(e.cn("rf_f_per_s"), [10.0, 500.0, 5000.0]),
        PsiM => pct_cell(e.cn("psi_mem_some_avg10"), [2.0, 10.0, 30.0]),
        PsiI => pct_cell(e.cn("psi_io_some_avg10"), [2.0, 10.0, 30.0]),
        PsiC => pct_cell(e.cn("psi_cpu_some_avg10"), [2.0, 10.0, 30.0]),
        IoR => bytes_cell(e.cn("io_r_bps"), [1e6, 2e7, 1e8]),
        IoW => bytes_cell(e.cn("io_w_bps"), [1e6, 2e7, 1e8]),
        IopsR => count_cell(e.cn("io_r_iops"), [100.0, 1000.0, 5000.0]),
        IopsW => count_cell(e.cn("io_w_iops"), [100.0, 1000.0, 5000.0]),
        Stall => match max_psi(e, "full") {
            Some(v) => pct_cell(Cn::Val(v), [1.0, 5.0, 15.0]),
            None => na(),
        },
        Blk => match blk.get(i).copied().flatten() {
            None => na(),
            Some(n) if s.live.is_some() => (n.to_string(), if n == 0 { dim() } else { Style::new().fg(Color::Indexed(208)).add_modifier(Modifier::BOLD) }),
            Some(n) => (if n == 0 { "0".into() } else { n.to_string() }, demo_style(n == 0)),
        },
        NetRx => bytes_cell(e.cn("net_rx_bps"), [1e5, 1e7, 1e8]),
        NetTx => bytes_cell(e.cn("net_tx_bps"), [1e5, 1e7, 1e8]),
        Pids => numcell(e.cn("pids_current"), |v| {
            let b = match e.num("pids_max") {
                Some(m) if m > 0.0 => band(v / m * 100.0, [50.0, 80.0, 95.0]).max(1),
                _ => 1,
            };
            (format!("{v:.0}"), if v == 0.0 { 0 } else { b })
        }),
        Anon => bytes_cell(e.cn("anon"), [1e9, 4e9, 16e9]),
        File => bytes_cell(e.cn("file"), [1e9, 4e9, 16e9]),
        Shmem => bytes_cell(e.cn("shmem"), [1e8, 1e9, 4e9]),
        Swap => bytes_cell(e.cn("swap_disk"), [1e6, 1e8, 1e9]),
        Pgscan => count_cell(e.cn("pgscan_per_s"), [100.0, 5000.0, 50000.0]),
        Pgsteal => count_cell(e.cn("pgsteal_per_s"), [100.0, 5000.0, 50000.0]),
        IoCost => na(),
        Origin => {
            let o = origin_short(&e.origin);
            let st = match e.origin.as_str() {
                "raw_write" => Style::new().fg(Color::Yellow),
                "unset" | "" => dim(),
                _ => Style::new().fg(Color::Gray),
            };
            (o.to_string(), st)
        }
        Drift => {
            if e.drift {
                (format!("\u{25b2}{}", e.drifted.len().max(1)), Style::new().fg(Color::Yellow).add_modifier(Modifier::BOLD))
            } else {
                unset()
            }
        }
        CpuWt => numcell(e.cn("cpu_weight"), |v| (format!("{v:.0}"), if v == 100.0 { 1 } else { 2 })),
        IoWt => numcell(e.cn("io_weight"), |v| (format!("{v:.0}"), if v == 100.0 { 1 } else { 2 })),
        CpuQuota => match (e.cn("cpu_quota_us"), e.num("cpu_period_us")) {
            (Cn::Val(q), Some(p)) if p > 0.0 => (format!("{:.0}%", q / p * 100.0), Style::new().fg(Color::Yellow)),
            (Cn::NA, _) => na(),
            _ => unset(),
        },
        MemMin => cfg_bytes(e.cn("mem_min"), Style::new().fg(Color::Cyan)),
        MemLow => cfg_bytes(e.cn("mem_low"), Style::new().fg(Color::Cyan)),
        MemHigh => cfg_bytes(e.cn("mem_high"), Style::new().fg(Color::Yellow)),
        MemMax => cfg_bytes(e.cn("mem_max"), Style::new().fg(Color::Yellow)),
        EffMin => {
            let clamped = matches!((e.num("mem_min"), e.num("effective_memory_min")), (Some(m), Some(f)) if f < m);
            cfg_bytes(e.cn("effective_memory_min"), if clamped { Style::new().fg(Color::Red) } else { Style::new().fg(Color::Cyan) })
        }
        PidsMax => match e.cn("pids_max") {
            Cn::Val(v) => (format!("{v:.0}"), Style::new().fg(Color::Yellow)),
            Cn::NA => na(),
            Cn::Unset => unset(),
        },
        DriftList => {
            if e.drift {
                (e.drifted.join(","), Style::new().fg(Color::Yellow))
            } else {
                unset()
            }
        }
        Warn => {
            let w = warnings(e);
            if w.is_empty() { unset() } else { (w.join("; "), Style::new().fg(Color::Yellow)) }
        }
        PsiCf => pct_cell(e.cn("psi_cpu_full_avg10"), [1.0, 5.0, 15.0]),
        PsiMf => pct_cell(e.cn("psi_mem_full_avg10"), [1.0, 5.0, 15.0]),
        PsiIf => pct_cell(e.cn("psi_io_full_avg10"), [1.0, 5.0, 15.0]),
        EvHigh => count_cell(e.cn("mem_events_high_per_s"), [1.0, 50.0, 500.0]),
        EvMax => count_cell(e.cn("mem_events_max_per_s"), [0.1, 1.0, 10.0]),
        EvOom => count_cell(e.cn("mem_events_oom_kill_per_s"), [0.001, 0.01, 0.1]),
    }
}

/// Sort key per column (None = not sortable).
pub fn sortval(id: ColId, s: &Snap, blk: &[Option<u32>], i: usize) -> Option<f64> {
    use ColId::*;
    let e = &s.ents[i];
    let n = |k: &str| e.num(k).unwrap_or(-1.0);
    Some(match id {
        SwapMax => n("swap_max"),
        Ratio => n("ratio"),
        RfZ => n("rf_z_per_s"),
        Maj => n("maj_per_s"),
        IoMax => n("io_cap_saturation_pct"),
        Cpu => n("cpu_pct"),
        Thr => n("cpu_throttled_pct"),
        RamCeil => n("ram"),
        Zsw => n("z_pool"),
        RfD => n("rf_d_per_s"),
        RfF => n("rf_f_per_s"),
        PsiM => n("psi_mem_some_avg10"),
        PsiI => n("psi_io_some_avg10"),
        PsiC => n("psi_cpu_some_avg10"),
        PsiCf => n("psi_cpu_full_avg10"),
        PsiMf => n("psi_mem_full_avg10"),
        PsiIf => n("psi_io_full_avg10"),
        IoR => n("io_r_bps"),
        IoW => n("io_w_bps"),
        IopsR => n("io_r_iops"),
        IopsW => n("io_w_iops"),
        Stall => max_psi(e, "full").unwrap_or(-1.0),
        Blk => blk.get(i).copied().flatten().map_or(-1.0, |b| b as f64),
        NetRx => n("net_rx_bps"),
        NetTx => n("net_tx_bps"),
        Pids => n("pids_current"),
        Anon => n("anon"),
        File => n("file"),
        Shmem => n("shmem"),
        Swap => n("swap_disk"),
        Pgscan => n("pgscan_per_s"),
        Pgsteal => n("pgsteal_per_s"),
        Drift | DriftList => e.drifted.len() as f64,
        Warn => warnings(e).len() as f64,
        MemMin => n("mem_min"),
        MemLow => n("mem_low"),
        MemHigh => n("mem_high"),
        MemMax => n("mem_max"),
        EffMin => n("effective_memory_min"),
        CpuWt => n("cpu_weight"),
        IoWt => n("io_weight"),
        EvHigh => n("mem_events_high_per_s"),
        EvMax => n("mem_events_max_per_s"),
        EvOom => n("mem_events_oom_kill_per_s"),
        _ => return None,
    })
}

// ---------- row noise ----------

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Noise {
    All,
    Middle,
    Hot,
}

pub fn hot(e: &Ent) -> bool {
    e.n0("cpu_pct") >= 5.0
        || e.n0("io_r_bps") + e.n0("io_w_bps") >= 1_048_576.0
        || max_psi(e, "some").unwrap_or(0.0) >= 5.0
        || e.n0("cpu_throttled_pct") > 0.5
        || e.n0("mem_events_oom_kill_per_s") > 0.0
}

pub fn has_limit(e: &Ent) -> bool {
    let set = |k: &str| e.num(k).map_or(false, |v| v > 0.0);
    set("mem_min") || set("mem_low") || set("mem_high") || set("mem_max") || set("cpu_quota_us") || set("pids_max")
        || e.num("cpu_weight").map_or(false, |v| v != 100.0)
        || e.num("io_weight").map_or(false, |v| v != 100.0)
}

pub fn middle(e: &Ent) -> bool {
    hot(e) || e.n0("pids_current") > 0.0 || has_limit(e) || e.drift || !e.findings.is_empty()
}
