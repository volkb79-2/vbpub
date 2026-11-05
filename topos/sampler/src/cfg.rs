//! Configuration: every key is required, nothing is defaulted in code.
use serde::Deserialize;

#[derive(Deserialize, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub sampler: Sampler,
    pub process: Process,
    pub events: Events,
    pub protocol: Protocol,
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
    /// 0 = run until killed.
    pub duration_s: u64,
    /// Benchmark summary written at exit; empty string = none.
    pub stats_path: String,
}

#[derive(Deserialize, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct Process {
    pub scan: bool,
    pub top_cpu: usize,
    pub top_io: usize,
    /// Hard cap of the enriched union (D state + top CPU + top IO).
    pub cap: usize,
    /// `io` and `schedstat` are read for all PIDs only every this many seconds; between
    /// sweeps only for candidates (previous enriched union, CPU time rose, D state, new).
    pub full_sweep_every_s: u64,
    pub cmdline_max: usize,
}

#[derive(Deserialize, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct Events {
    pub psi_cpu_some_avg10: f64,
    pub psi_memory_some_avg10: f64,
    pub psi_io_some_avg10: f64,
    pub events_path: String,
    /// The events file is rotated to `<path>.1` when it exceeds this size.
    pub max_bytes: u64,
}

#[derive(Deserialize, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct Protocol {
    pub max_request_bytes: usize,
    pub max_response_bytes: usize,
    pub max_clients: usize,
    pub client_idle_ms: u64,
    pub client_write_ms: u64,
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
        Self::parse(&text).map_err(|e| format!("{path}: {e}"))
    }

    pub fn parse(text: &str) -> Result<Config, String> {
        let c: Config = toml::from_str(text).map_err(|e| e.to_string())?;
        c.validate()?;
        Ok(c)
    }

    fn validate(&self) -> Result<(), String> {
        let c = self;
        if c.sampler.tick_ms == 0 || c.sampler.slow_every_s == 0 {
            return Err("tick_ms and slow_every_s must be > 0".into());
        }
        if c.process.cap == 0 || c.process.full_sweep_every_s == 0 {
            return Err("process.cap and process.full_sweep_every_s must be > 0".into());
        }
        let p = &c.protocol;
        if p.max_request_bytes < 64 || p.max_response_bytes < 4096 || p.max_clients == 0 {
            return Err("protocol limits too small (request >= 64, response >= 4096, clients >= 1)".into());
        }
        if p.client_idle_ms == 0 || p.client_write_ms == 0 {
            // a zero socket timeout is an error in std and would silently disable the protection
            return Err("protocol.client_idle_ms and protocol.client_write_ms must be > 0".into());
        }
        let mut disk = 0;
        let mut last_step = 0;
        for t in &c.tier {
            if t.medium != "tmpfs" && t.medium != "disk" {
                return Err(format!("tier {}: medium must be tmpfs or disk", t.name));
            }
            if t.medium == "disk" {
                disk += 1;
            }
            if (t.step_s * 1000) % c.sampler.tick_ms != 0 || t.step_s * 1000 < c.sampler.tick_ms {
                return Err(format!("tier {}: step_s must be a multiple of tick_ms", t.name));
            }
            if t.segment_s == 0 || t.keep_s == 0 {
                return Err(format!("tier {}: segment_s and keep_s must be > 0", t.name));
            }
            if t.step_s <= last_step {
                return Err(format!("tier {}: tiers must be listed by increasing step_s", t.name));
            }
            last_step = t.step_s;
        }
        if disk > 1 {
            return Err("at most one tier may use medium = disk (the byte cap applies to it)".into());
        }
        Ok(())
    }

    /// Seconds after which no stored segment can reference an entity id any more.
    pub fn retention_s(&self) -> u64 {
        self.tier.iter().map(|t| t.keep_s + t.segment_s).max().unwrap_or(0) + 60
    }
}
