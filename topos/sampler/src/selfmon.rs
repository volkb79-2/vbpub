//! The sampler watching itself: CPU, RSS, and the throttling of its own cgroup, so the
//! operator sees collection gaps instead of silently thin data.
use crate::cfg::Config;
use crate::procs::PV;
use crate::util::*;
use std::collections::VecDeque;

pub struct SelfMon {
    hist: VecDeque<(u64, u64)>, // (mono_ms, cpu_ticks), about the last 60 s
    first: Option<(u64, u64)>,
    slurp: Slurp,
    path: String,
}

pub struct SelfSample {
    pub cpu_ticks: Option<u64>,
    pub rss: Option<u64>,
    pub cpu_recent: PV,
    pub cpu_avg: PV,
    /// nr_throttled, throttled_usec, memory.events high, memory.events max.
    pub throttle: [PV; 4],
}

impl SelfMon {
    pub fn new() -> SelfMon {
        SelfMon { hist: VecDeque::new(), first: None, slurp: Slurp::new(), path: String::new() }
    }

    fn kv(&mut self, file: &str, keys: [&[u8]; 2]) -> [PV; 2] {
        let b = match self.slurp.read(file) {
            Ok(b) => b,
            Err(e) => return [PV::st(err_state(&e, stc::NA_CONTROLLER)); 2],
        };
        let mut out = [PV::st(stc::UN_PARSE); 2];
        for l in lines(b) {
            let mut it = fields(l);
            if let (Some(k), Some(v)) = (it.next(), it.next().and_then(parse_u64)) {
                for i in 0..2 {
                    if k == keys[i] {
                        out[i] = PV::val(v as f64);
                    }
                }
            }
        }
        out
    }

    pub fn sample(&mut self, cfg: &Config, mono_ms: u64, clk: u64, page: u64) -> SelfSample {
        let root = &cfg.sampler.proc_root;
        let mut cpu_ticks = None;
        if let Ok(b) = self.slurp.read(&format!("{root}/self/stat")) {
            if let Some(cp) = b.iter().rposition(|&c| c == b')') {
                let f: Vec<&[u8]> = b[(cp + 2).min(b.len())..].split(|&c| c == b' ').collect();
                if let (Some(u), Some(s)) = (f.get(11).and_then(|x| parse_u64(x)), f.get(12).and_then(|x| parse_u64(x))) {
                    cpu_ticks = Some(u + s);
                }
            }
        }
        let rss = self
            .slurp
            .read(&format!("{root}/self/statm"))
            .ok()
            .and_then(|b| fields(b).nth(1).and_then(parse_u64))
            .map(|p| p * page);
        let (mut recent, mut avg) = (PV::st(stc::UN_READ), PV::st(stc::UN_READ));
        if let Some(c) = cpu_ticks {
            self.first.get_or_insert((mono_ms, c));
            self.hist.push_back((mono_ms, c));
            while self.hist.len() > 1 && mono_ms.saturating_sub(self.hist[0].0) > 60_000 {
                self.hist.pop_front();
            }
            let pct = |a: (u64, u64)| {
                let dt = mono_ms.saturating_sub(a.0);
                if dt == 0 { PV::st(stc::UN_FIRST) } else { PV::val((c.saturating_sub(a.1)) as f64 * 1000.0 / clk as f64 / dt as f64 * 100.0) }
            };
            recent = pct(self.hist[0]);
            avg = pct(self.first.unwrap());
        }
        // own cgroup: path from /proc/self/cgroup
        if self.path.is_empty() {
            if let Ok(b) = self.slurp.read(&format!("{root}/self/cgroup")) {
                if let Some(l) = lines(b).find(|l| l.starts_with(b"0::")) {
                    let p = String::from_utf8_lossy(&l[3..]).into_owned();
                    self.path = if p == "/" { "-".to_string() } else { p };
                }
            }
        }
        let rel = if self.path == "-" { "" } else { self.path.as_str() };
        let throttle = if self.path.is_empty() {
            [PV::st(stc::UN_READ); 4]
        } else {
            let base = format!("{}{}", cfg.sampler.cgroup_root, rel);
            let a = self.kv(&format!("{base}/cpu.stat"), [b"nr_throttled", b"throttled_usec"]);
            let b = self.kv(&format!("{base}/memory.events"), [b"high", b"max"]);
            [a[0], a[1], b[0], b[1]]
        };
        SelfSample { cpu_ticks, rss, cpu_recent: recent, cpu_avg: avg, throttle }
    }
}

/// `hello.cgroup_view`: whether the sampler sees the whole host tree or only a
/// cgroup-namespaced slice of it (for example inside a container). Heuristic, with the
/// evidence reported: the sampler's own cgroup path is "/" (it sits at the root of its
/// namespace), or its cgroup namespace differs from the one of PID 1 when PID 1 is visible.
pub fn cgroup_view(cfg: &Config) -> String {
    let root = &cfg.sampler.proc_root;
    let mut o = String::from("{\"mode\":");
    let self_path = std::fs::read_to_string(format!("{root}/self/cgroup")).ok().and_then(|t| {
        t.lines().find(|l| l.starts_with("0::")).map(|l| l[3..].to_string())
    });
    let ns_self = std::fs::read_link(format!("{root}/self/ns/cgroup")).ok().map(|p| p.to_string_lossy().into_owned());
    let ns_init = std::fs::read_link(format!("{root}/1/ns/cgroup")).ok().map(|p| p.to_string_lossy().into_owned());
    let (mode, why) = match (&self_path, &ns_self, &ns_init) {
        (None, _, _) => ("unknown", "self_cgroup_unreadable"),
        (Some(p), _, _) if p == "/" => ("namespaced", "self_at_cgroup_root"),
        (_, Some(a), Some(b)) if a != b => ("namespaced", "cgroup_ns_differs_from_pid1"),
        _ => ("host", "full_tree_visible"),
    };
    jstr(&mut o, mode);
    o.push_str(",\"because\":");
    jstr(&mut o, why);
    o.push_str(",\"self_path\":");
    match &self_path {
        Some(p) => jstr(&mut o, p),
        None => o.push_str("null"),
    }
    o.push_str(",\"cgroup_root\":");
    jstr(&mut o, &cfg.sampler.cgroup_root);
    o.push('}');
    o
}
