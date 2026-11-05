//! SYNTHETIC data. Everything here is invented so the layout can be judged where the
//! recording has no data (per-CPU, us/sy/io/st split, D state, processes, zswap rates).
//! The UI marks all of it with `~` / `demo` and a magenta colour; it is never real.
use crate::data::Snap;

pub fn hash(s: &str) -> u64 {
    let mut h: u64 = 0xcbf29ce484222325;
    for b in s.bytes() {
        h ^= b as u64;
        h = h.wrapping_mul(0x100000001b3);
    }
    h
}

/// Deterministic noise in 0..1.
pub fn noise(seed: u64) -> f64 {
    let mut z = seed.wrapping_add(0x9E3779B97F4A7C15);
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58476D1CE4E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D049BB133111EB);
    ((z ^ (z >> 31)) >> 11) as f64 / (1u64 << 53) as f64
}

pub const NCPU: usize = 8;

/// us, sy, io, st as fractions of one CPU, scaled so the mean matches the real busy-cores.
pub fn per_cpu(cores_busy: f64, fi: usize) -> Vec<[f64; 4]> {
    let base = cores_busy / NCPU as f64;
    (0..NCPU)
        .map(|c| {
            let r = noise((c * 131 + fi * 7) as u64);
            let tot = (base * (0.3 + 1.4 * r)).clamp(0.0, 1.0);
            let sy = 0.12 + 0.15 * noise((c * 17 + fi) as u64);
            let io = 0.06 * noise((c * 29 + fi * 3) as u64);
            let st = 0.02;
            let us = 1.0 - sy - io - st;
            [tot * us, tot * sy, tot * io, tot * st]
        })
        .collect()
}

pub struct Proc {
    pub pid: u32,
    pub comm: &'static str,
    pub cpu: f64,
    pub state: char,
}

impl Proc {
    pub fn hot(&self) -> bool {
        self.cpu >= 3.0 || self.state == 'D'
    }
}

const COMMS: [&str; 12] = [
    "python3", "postgres", "java", "node", "dockerd", "containerd-shim", "rg", "cc1plus", "sshd", "rsync", "kworker/u16", "mysqld",
];

/// Invented processes for every cgroup that has directly attached tasks.
pub fn procs_for(s: &Snap, i: usize, fi: usize) -> Vec<Proc> {
    let e = &s.ents[i];
    let n = (e.n0("cgroup_procs") as usize).min(6);
    if n == 0 {
        return vec![];
    }
    let share = if s.children[i].is_empty() { 1.0 } else { 0.3 };
    let cpu = e.n0("cpu_pct") * share;
    let io = e.n0("psi_io_some_avg10");
    let h = hash(&e.key);
    let mut out = vec![];
    for k in 0..n {
        let w = 0.6 / (1.0 + k as f64 * 1.3);
        let jit = 0.85 + 0.3 * noise(h ^ (fi as u64 * 977 + k as u64));
        let c = cpu * w * jit;
        let pd = 0.05 + (io / 20.0).min(0.4);
        let state = if k < 2 && noise(h.wrapping_add((fi / 4) as u64 * 31 + k as u64)) < pd {
            'D'
        } else if c > 1.0 {
            'R'
        } else {
            'S'
        };
        out.push(Proc {
            pid: 1000 + ((h >> 8) % 60000) as u32 + k as u32 * 7,
            comm: COMMS[((h >> 3) as usize + k * 5) % COMMS.len()],
            cpu: c,
            state,
        });
    }
    out
}
