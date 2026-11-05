//! Application state: tree rows, noise/filter/sort, cursor, input handling.
use crate::cols::{self, Col, ColId, Noise, UiCfg};
use crate::data::{Proc, Recording, Snap};
use crate::demo;
use crate::live;
use ratatui::crossterm::event::{KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use ratatui::layout::Rect;
use std::collections::HashSet;
use std::sync::mpsc::{Receiver, TryRecvError};
use std::time::{Duration, Instant};

/// Frames kept for trends and stepping in live mode.
const HISTORY: usize = 120;

/// The socket feed as the UI sees it. The socket itself belongs to the thread in `live.rs`.
pub struct LiveState {
    pub path: String,
    pub rx: Receiver<live::Msg>,
    pub hello: Option<live::Hello>,
    pub status: Option<live::Status>,
    pub epoch: Option<String>,
    pub epoch_changes: u32,
    pub last_ok: Option<Instant>,
    /// Why the feed is down (None while answers arrive).
    pub down: Option<String>,
    pub got_first: bool,
    pub started: Instant,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum RowKind {
    Cg(usize),
    Proc(usize, usize),
    Flat(usize, usize),
}

#[derive(Clone, Copy)]
pub struct Row {
    pub kind: RowKind,
    pub depth: u16,
    pub has_kids: bool,
    pub open: bool,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum PaneMode {
    Auto,
    On,
    Off,
}

/// Hit-test rectangles recorded by the last draw.
#[derive(Default)]
pub struct Hit {
    pub body: Rect,
    pub heads: Vec<(Rect, ColId)>,
    pub pane_shown: bool,
}

#[derive(Default)]
pub struct Perf {
    pub fps: f64,
    pub cpu_pct: f64,
    pub draw_ms: f64,
    count: u32,
    t0: Option<Instant>,
    cpu0: f64,
}

pub fn cpu_ms() -> f64 {
    let mut u: libc::rusage = unsafe { std::mem::zeroed() };
    unsafe { libc::getrusage(libc::RUSAGE_SELF, &mut u) };
    (u.ru_utime.tv_sec + u.ru_stime.tv_sec) as f64 * 1e3 + (u.ru_utime.tv_usec + u.ru_stime.tv_usec) as f64 / 1e3
}
pub fn rss_kb() -> i64 {
    let mut u: libc::rusage = unsafe { std::mem::zeroed() };
    unsafe { libc::getrusage(libc::RUSAGE_SELF, &mut u) };
    u.ru_maxrss as i64
}

impl Perf {
    pub fn after_draw(&mut self, ms: f64) {
        self.draw_ms = ms;
        let now = Instant::now();
        let t0 = *self.t0.get_or_insert(now);
        self.count += 1;
        let dt = now.duration_since(t0).as_secs_f64();
        if dt >= 1.0 {
            self.fps = self.count as f64 / dt;
            let c = cpu_ms();
            self.cpu_pct = (c - self.cpu0) / (dt * 10.0);
            self.cpu0 = c;
            self.count = 0;
            self.t0 = Some(now);
        } else if self.cpu0 == 0.0 {
            self.cpu0 = cpu_ms();
        }
    }
}

pub struct App {
    pub host_id: String,
    pub snaps: Vec<Snap>,
    pub fi: usize,
    pub tick_ms: u64,
    pub frozen: bool,
    pub cols: Vec<Col>,
    pub noise: Noise,
    pub sort: ColId,
    pub desc: bool,
    pub filter: String,
    pub filter_edit: bool,
    pub expanded: HashSet<String>,
    pub cursor: usize,
    pub cursor_key: String,
    pub scroll: usize,
    pub vis_rows: usize,
    pub pane: PaneMode,
    pub help: bool,
    pub procs_on: bool,
    pub flat: bool,
    pub rows: Vec<Row>,
    pub hidden: usize,
    pub blk: Vec<Option<u32>>,
    pub procs: Vec<Vec<Proc>>,
    pub hit: Hit,
    pub perf: Perf,
    pub msg: String,
    /// True only in recording mode: per-CPU, D-state, processes and zswap rates are invented.
    /// Never true with `--socket`.
    pub demo: bool,
    pub live: Option<LiveState>,
    pub ui: UiCfg,
}

impl App {
    pub fn new(rec: Recording, tick_ms: u64, cols: Vec<Col>) -> App {
        App::build(rec.host_id, rec.snaps, tick_ms, cols, UiCfg::default(), true, None)
    }

    /// Live mode: nothing is shown until the sampler answers, and nothing is ever invented.
    pub fn new_live(path: &str, tick_override: Option<u64>, cols: Vec<Col>, ui: UiCfg) -> App {
        let rx = live::spawn(path.to_string(), tick_override);
        App::from_feed(path, rx, tick_override.unwrap_or(1000), cols, ui)
    }

    /// Live mode on an existing feed (tests feed it from a fake server).
    pub fn from_feed(path: &str, rx: Receiver<live::Msg>, tick_ms: u64, cols: Vec<Col>, ui: UiCfg) -> App {
        let lv = LiveState { path: path.to_string(), rx, hello: None, status: None, epoch: None, epoch_changes: 0, last_ok: None, down: Some("connecting".into()), got_first: false, started: Instant::now() };
        App::build(path.to_string(), vec![Snap::empty()], tick_ms, cols, ui, false, Some(lv))
    }

    fn build(host_id: String, snaps: Vec<Snap>, tick_ms: u64, cols: Vec<Col>, ui: UiCfg, demo: bool, live: Option<LiveState>) -> App {
        let mut a = App {
            host_id,
            snaps,
            fi: 0,
            tick_ms,
            frozen: false,
            cols,
            noise: Noise::Middle,
            sort: ColId::Cpu,
            desc: true,
            filter: String::new(),
            filter_edit: false,
            expanded: HashSet::new(),
            cursor: 0,
            cursor_key: String::new(),
            scroll: 0,
            vis_rows: 20,
            pane: PaneMode::Auto,
            help: false,
            procs_on: true,
            flat: false,
            rows: vec![],
            hidden: 0,
            blk: vec![],
            procs: vec![],
            hit: Hit::default(),
            perf: Perf::default(),
            msg: String::new(),
            demo,
            live,
            ui,
        };
        a.fi = a.lo();
        a.expand_depth(1);
        a.recompute();
        a
    }

    pub fn snap(&self) -> &Snap {
        &self.snaps[self.fi]
    }

    /// Frame 0 of a recording has no rates yet, so the loop skips it when there is more than one frame.
    fn lo(&self) -> usize {
        if self.live.is_none() && self.snaps.len() > 1 { 1 } else { 0 }
    }

    pub fn advance(&mut self, d: isize) {
        if self.live.is_some() {
            // stepping through the kept history: no wrap-around
            self.fi = (self.fi as isize + d).clamp(0, self.snaps.len() as isize - 1) as usize;
        } else {
            let (lo, span) = (self.lo(), (self.snaps.len() - self.lo()) as isize);
            self.fi = lo + ((self.fi as isize - lo as isize + d).rem_euclid(span)) as usize;
        }
        self.recompute();
    }

    /// Seconds since the last sampler answer; None before the first one or in recording mode.
    pub fn age_s(&self) -> Option<f64> {
        self.live.as_ref().and_then(|l| l.last_ok).map(|t| t.elapsed().as_secs_f64())
    }

    /// No answer for three sampler ticks (at least 3 s), or the feed reported itself down.
    pub fn stale(&self) -> bool {
        let Some(l) = &self.live else { return false };
        let tick = l.hello.as_ref().map_or(self.tick_ms, |h| h.tick_ms);
        match l.last_ok {
            Some(t) => l.down.is_some() || t.elapsed() > Duration::from_millis((tick * 3).max(3000)),
            None => false,
        }
    }

    /// Take everything the feed thread has sent. True when the screen needs a redraw.
    pub fn pump(&mut self) -> bool {
        let Some(l) = self.live.as_mut() else { return false };
        let mut msgs = vec![];
        loop {
            match l.rx.try_recv() {
                Ok(m) => msgs.push(m),
                Err(TryRecvError::Empty) => break,
                Err(TryRecvError::Disconnected) => {
                    l.down = Some("the feed thread stopped".into());
                    break;
                }
            }
        }
        let changed = !msgs.is_empty();
        let mut new_snap = false;
        for m in msgs {
            let l = self.live.as_mut().unwrap();
            match m {
                live::Msg::Hello(h) => l.hello = Some(h),
                live::Msg::Status(s) => l.status = Some(s),
                live::Msg::Down(why) => l.down = Some(why),
                live::Msg::Snap { snap, epoch } => {
                    let restarted = l.epoch.as_ref().map_or(false, |e| *e != epoch);
                    if restarted {
                        self.msg = format!("sampler restarted (epoch {} -> {}); history cleared", l.epoch.clone().unwrap_or_default(), epoch);
                        l.epoch_changes += 1;
                        self.snaps.clear();
                    }
                    if !l.got_first {
                        self.snaps.clear();
                        l.got_first = true;
                    }
                    l.epoch = Some(epoch);
                    l.last_ok = Some(Instant::now());
                    l.down = None;
                    self.snaps.push(*snap);
                    if self.snaps.len() > HISTORY {
                        self.snaps.remove(0);
                        self.fi = self.fi.saturating_sub(1);
                    }
                    if !self.frozen || restarted || self.snaps.len() == 1 {
                        self.fi = self.snaps.len() - 1;
                    }
                    if self.snaps.len() == 1 {
                        // first frame of this sampler run: show the top-level slices
                        self.expand_depth(1);
                    }
                    new_snap = true;
                }
            }
        }
        if new_snap {
            self.recompute();
        }
        changed
    }

    fn expand_depth(&mut self, max: usize) {
        let s = &self.snaps[self.fi];
        fn go(s: &Snap, i: usize, d: usize, max: usize, out: &mut HashSet<String>) {
            if d > max {
                return;
            }
            out.insert(s.ents[i].key.clone());
            for &c in &s.children[i] {
                go(s, c, d + 1, max, out);
            }
        }
        let mut set = HashSet::new();
        go(s, s.root, 0, max, &mut set);
        self.expanded = set;
    }

    /// Demo processes and the aggregated demo D-state count per cgroup, then rows.
    pub fn recompute(&mut self) {
        let s = &self.snaps[self.fi];
        if let (false, Some(l)) = (self.demo, &s.live) {
            // live: processes and D-state counts come from the sampler
            self.procs = l.procs.clone();
            let mut blk: Vec<Option<u32>> = vec![None; s.ents.len()];
            fn agg(s: &Snap, i: usize, own: &[Option<u32>], out: &mut [Option<u32>]) -> Option<u32> {
                let mut sum = own[i];
                for &c in &s.children[i] {
                    if let Some(x) = agg(s, c, own, out) {
                        sum = Some(sum.unwrap_or(0) + x);
                    }
                }
                out[i] = sum;
                sum
            }
            agg(s, s.root, &l.dstate, &mut blk);
            if l.d_total.is_some() {
                blk[s.root] = l.d_total;
            }
            self.blk = blk;
        } else {
            self.procs = (0..s.ents.len()).map(|i| demo::procs_for(s, i, self.fi)).collect();
            let mut blk = vec![None; s.ents.len()];
            fn go(s: &Snap, i: usize, procs: &[Vec<Proc>], blk: &mut [Option<u32>]) -> u32 {
                let mut n = procs[i].iter().filter(|p| p.state == 'D').count() as u32;
                for &c in &s.children[i] {
                    n += go(s, c, procs, blk);
                }
                blk[i] = Some(n);
                n
            }
            go(s, s.root, &self.procs, &mut blk);
            self.blk = blk;
        }
        self.rebuild();
    }

    fn row_key(&self, r: &Row) -> String {
        let s = self.snap();
        match r.kind {
            RowKind::Cg(i) => s.ents[i].key.clone(),
            RowKind::Proc(i, p) | RowKind::Flat(i, p) => format!("{}#{}", s.ents[i].key, self.procs[i][p].pid),
        }
    }

    pub fn rebuild(&mut self) {
        let s = &self.snaps[self.fi];
        let filt = self.filter.to_lowercase();
        let n = s.ents.len();
        let own = |i: usize| -> bool {
            let e = &s.ents[i];
            let pn = match self.noise {
                Noise::All => true,
                Noise::Middle => cols::middle(e),
                Noise::Hot => cols::hot(e),
            };
            pn && (filt.is_empty() || e.search.contains(&filt))
        };
        let mut vis = vec![false; n];
        fn mark(s: &Snap, i: usize, own: &dyn Fn(usize) -> bool, vis: &mut [bool]) -> bool {
            let mut v = own(i);
            for &c in &s.children[i] {
                v |= mark(s, c, own, vis);
            }
            vis[i] = v;
            v
        }
        mark(s, s.root, &own, &mut vis);
        vis[s.root] = true;
        self.hidden = vis.iter().filter(|v| !**v).count();

        let mut rows = vec![];
        if self.flat {
            let mut all: Vec<(usize, usize)> =
                (0..n).filter(|&i| vis[i]).flat_map(|i| (0..self.procs[i].len()).map(move |p| (i, p))).collect();
            all.sort_by(|a, b| self.procs[b.0][b.1].cpu_f().total_cmp(&self.procs[a.0][a.1].cpu_f()));
            all.truncate(300);
            rows = all.into_iter().map(|(i, p)| Row { kind: RowKind::Flat(i, p), depth: 0, has_kids: false, open: false }).collect();
        } else {
            struct Cx<'a> {
                s: &'a Snap,
                vis: &'a [bool],
                a: &'a App,
                filtering: bool,
            }
            fn emit(cx: &Cx, i: usize, depth: u16, rows: &mut Vec<Row>) {
                let (s, a) = (cx.s, cx.a);
                let mut kids: Vec<usize> = s.children[i].iter().copied().filter(|&c| cx.vis[c]).collect();
                let has_procs = a.procs_on && !a.procs[i].is_empty();
                let open = cx.filtering || a.expanded.contains(&s.ents[i].key);
                rows.push(Row { kind: RowKind::Cg(i), depth, has_kids: !kids.is_empty() || has_procs, open });
                if open {
                    kids.sort_by(|&x, &y| {
                        let ord = if a.sort == ColId::Name {
                            s.ents[x].name.to_lowercase().cmp(&s.ents[y].name.to_lowercase())
                        } else {
                            let k = |j| cols::sortval(a.sort, s, &a.blk, j).unwrap_or(-1.0);
                            k(x).total_cmp(&k(y))
                        };
                        let ord = if a.desc { ord.reverse() } else { ord };
                        ord.then_with(|| s.ents[x].name.cmp(&s.ents[y].name))
                    });
                    for c in kids {
                        emit(cx, c, depth + 1, rows);
                    }
                }
                if has_procs {
                    // hot and blocked processes float under their owner, capped; open shows all
                    let cap = if open { usize::MAX } else { a.ui.proc_cap };
                    let mut shown = 0;
                    for (p, pr) in a.procs[i].iter().enumerate() {
                        if (open || pr.hot()) && shown < cap {
                            rows.push(Row { kind: RowKind::Proc(i, p), depth: depth + 1, has_kids: false, open: false });
                            shown += 1;
                        }
                    }
                }
            }
            let cx = Cx { s, vis: &vis, a: self, filtering: !filt.is_empty() };
            emit(&cx, s.root, 0, &mut rows);
        }
        self.rows = rows;
        // keep the cursor on the same row across rebuilds
        let keys: Vec<String> = self.rows.iter().map(|r| self.row_key(r)).collect();
        if let Some(p) = keys.iter().position(|k| *k == self.cursor_key) {
            self.cursor = p;
        } else {
            self.cursor = self.cursor.min(self.rows.len().saturating_sub(1));
            self.cursor_key = keys.get(self.cursor).cloned().unwrap_or_default();
        }
    }

    pub fn move_cursor(&mut self, d: isize) {
        if self.rows.is_empty() {
            return;
        }
        self.cursor = (self.cursor as isize + d).clamp(0, self.rows.len() as isize - 1) as usize;
        self.cursor_key = self.row_key(&self.rows[self.cursor].clone());
    }

    pub fn ensure_visible(&mut self) {
        let vis = self.vis_rows.max(1);
        if self.cursor < self.scroll {
            self.scroll = self.cursor;
        } else if self.cursor >= self.scroll + vis {
            self.scroll = self.cursor + 1 - vis;
        }
        self.scroll = self.scroll.min(self.rows.len().saturating_sub(vis));
    }

    /// The cgroup index the side pane should describe.
    pub fn selected_ent(&self) -> Option<usize> {
        self.rows.get(self.cursor).map(|r| match r.kind {
            RowKind::Cg(i) | RowKind::Proc(i, _) | RowKind::Flat(i, _) => i,
        })
    }

    fn toggle(&mut self, idx: usize) {
        if let Some(Row { kind: RowKind::Cg(i), has_kids: true, .. }) = self.rows.get(idx).copied() {
            let k = self.snaps[self.fi].ents[i].key.clone();
            if !self.expanded.remove(&k) {
                self.expanded.insert(k);
            }
            self.rebuild();
        }
    }

    fn set_sort(&mut self, id: ColId) {
        if self.sort == id {
            self.desc = !self.desc;
        } else {
            self.sort = id;
            self.desc = id != ColId::Name;
        }
        self.rebuild();
    }

    fn cycle_sort(&mut self) {
        let cs: Vec<ColId> = self.cols
            .iter()
            .map(|c| c.id)
            .filter(|&id| id == ColId::Name || cols::sortval(id, &self.snaps[self.fi], &self.blk, 0).is_some())
            .collect();
        let p = cs.iter().position(|&c| c == self.sort).map_or(0, |p| p + 1);
        self.sort = cs[p % cs.len()];
        self.desc = self.sort != ColId::Name;
        self.rebuild();
    }

    /// Returns true to quit.
    pub fn on_key(&mut self, k: KeyEvent) -> bool {
        use KeyCode::*;
        let ctrl = k.modifiers.contains(KeyModifiers::CONTROL);
        if ctrl && k.code == Char('c') {
            return true;
        }
        if self.filter_edit {
            match k.code {
                Enter => self.filter_edit = false,
                Esc => {
                    self.filter.clear();
                    self.filter_edit = false;
                }
                Backspace => {
                    self.filter.pop();
                }
                Char(c) if !ctrl => self.filter.push(c),
                _ => return false,
            }
            self.rebuild();
            return false;
        }
        if self.help {
            self.help = false;
            return false;
        }
        self.msg.clear();
        let page = self.vis_rows.max(2) as isize - 1;
        match k.code {
            Char('q') => return true,
            Esc => {
                if !self.filter.is_empty() {
                    self.filter.clear();
                    self.rebuild();
                }
            }
            Char('?') | F(1) => self.help = true,
            Char('n') => {
                self.noise = match self.noise {
                    Noise::All => Noise::Middle,
                    Noise::Middle => Noise::Hot,
                    Noise::Hot => Noise::All,
                };
                self.rebuild();
            }
            Char('/') => self.filter_edit = true,
            Tab => {
                self.pane = if self.hit.pane_shown { PaneMode::Off } else { PaneMode::On };
            }
            Char('s') => self.cycle_sort(),
            Char('S') => {
                self.desc = !self.desc;
                self.rebuild();
            }
            Char(' ') => self.frozen = !self.frozen,
            Char(',') => {
                self.frozen = true;
                self.advance(-1);
            }
            Char('.') => {
                self.frozen = true;
                self.advance(1);
            }
            Char('p') => {
                self.procs_on = !self.procs_on;
                self.rebuild();
            }
            Char('g') => {
                self.flat = !self.flat;
                self.scroll = 0;
                self.rebuild();
            }
            Char('e') => {
                self.expand_depth(99);
                self.rebuild();
            }
            Char('c') => {
                self.expand_depth(0);
                self.rebuild();
            }
            Up | Char('k') => self.move_cursor(-1),
            Down | Char('j') => self.move_cursor(1),
            PageUp => self.move_cursor(-page),
            PageDown => self.move_cursor(page),
            Home => self.move_cursor(isize::MIN / 2),
            End => self.move_cursor(isize::MAX / 2),
            Enter => self.toggle(self.cursor),
            Right | Char('l') => match self.rows.get(self.cursor).copied() {
                Some(r) if r.has_kids && !r.open => self.toggle(self.cursor),
                _ => self.move_cursor(1),
            },
            Left | Char('h') => match self.rows.get(self.cursor).copied() {
                Some(r) if r.open && r.has_kids => self.toggle(self.cursor),
                Some(r) => {
                    let up = (0..self.cursor).rev().find(|&j| self.rows[j].depth < r.depth);
                    if let Some(j) = up {
                        self.move_cursor(j as isize - self.cursor as isize);
                    }
                }
                None => {}
            },
            _ => return false,
        }
        false
    }

    /// Returns true when the screen needs a redraw.
    pub fn on_mouse(&mut self, m: MouseEvent) -> bool {
        let inside = |r: Rect| m.column >= r.x && m.column < r.x + r.width && m.row >= r.y && m.row < r.y + r.height;
        match m.kind {
            MouseEventKind::ScrollDown | MouseEventKind::ScrollUp => {
                let d: isize = if m.kind == MouseEventKind::ScrollDown { 3 } else { -3 };
                let max = self.rows.len().saturating_sub(self.vis_rows) as isize;
                self.scroll = (self.scroll as isize + d).clamp(0, max.max(0)) as usize;
                let (lo, hi) = (self.scroll, self.scroll + self.vis_rows.max(1) - 1);
                let c = self.cursor.clamp(lo, hi.min(self.rows.len().saturating_sub(1)));
                self.move_cursor(c as isize - self.cursor as isize);
                true
            }
            MouseEventKind::Down(MouseButton::Left) => {
                if let Some((_, id)) = self.hit.heads.iter().find(|(r, _)| inside(*r)) {
                    let id = *id;
                    self.set_sort(id);
                } else if inside(self.hit.body) {
                    let idx = self.scroll + (m.row - self.hit.body.y) as usize;
                    if idx < self.rows.len() {
                        self.move_cursor(idx as isize - self.cursor as isize);
                        let r = self.rows[idx];
                        let gx = self.hit.body.x + r.depth * 2;
                        if r.has_kids && m.column >= gx && m.column < gx + 2 {
                            self.toggle(idx);
                        }
                    }
                } else {
                    return false;
                }
                true
            }
            _ => false,
        }
    }
}
