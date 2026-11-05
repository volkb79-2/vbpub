//! Configuration: every key is required, nothing is defaulted in code.
use serde::Deserialize;

#[derive(Deserialize, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub sampler: Sampler,
    pub process: Process,
    pub events: Events,
    pub store: StoreCfg,
    pub tier: Vec<Tier>,
}

#[derive(Deserialize, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct Sampler {
    pub tick_ms: u64,
    pub slow_every_s: u64,
    pub cgroup_root: String,
    pub proc_root: String,
    pub sys_root: String,
    pub socket_path: String,
    pub duration_s: u64,
    pub stats_path: String,
}

#[derive(Deserialize, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct Process {
    pub scan: bool,
    pub top_cpu: usize,
    pub top_io: usize,
    pub cap: usize,
}

#[derive(Deserialize, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct Events {
    pub psi_cpu_some_avg10: f64,
    pub psi_memory_some_avg10: f64,
    pub psi_io_some_avg10: f64,
    pub events_path: String,
}

#[derive(Deserialize, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct StoreCfg {
    pub enabled: bool,
    pub tmpfs_dir: String,
    pub cold_dir: String,
    pub cold_cap_bytes: u64,
}

#[derive(Deserialize, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct Tier {
    pub name: String,
    pub step_s: u64,
    pub keep_s: u64,
    pub segment_s: u64,
    pub medium: String,
}

impl Config {
    pub fn load(path: &str) -> Result<Config, String> {
        let text = std::fs::read_to_string(path).map_err(|e| format!("{path}: {e}"))?;
        let c: Config = toml::from_str(&text).map_err(|e| format!("{path}: {e}"))?;
        if c.sampler.tick_ms == 0 || c.sampler.slow_every_s == 0 {
            return Err("tick_ms and slow_every_s must be > 0".into());
        }
        for t in &c.tier {
            if t.medium != "tmpfs" && t.medium != "disk" {
                return Err(format!("tier {}: medium must be tmpfs or disk", t.name));
            }
            if (t.step_s * 1000) % c.sampler.tick_ms != 0 || t.step_s * 1000 < c.sampler.tick_ms {
                return Err(format!("tier {}: step_s must be a multiple of tick_ms", t.name));
            }
            if t.segment_s == 0 || t.keep_s == 0 {
                return Err(format!("tier {}: segment_s and keep_s must be > 0", t.name));
            }
        }
        Ok(c)
    }
}
