//! Recording reader: JSONL, one header line, then one Frame per line (topos/record/reader.py).
use serde_json::Value;
use std::collections::HashMap;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Src {
    Exact,
    Derived,
    Unlimited,
    Unavail,
}

pub fn src_of(s: &str) -> Src {
    match s {
        "unlimited" => Src::Unlimited,
        "unavail_perm" | "unavail_kernel" => Src::Unavail,
        "derived" | "bpf" => Src::Derived,
        _ => Src::Exact,
    }
}

#[derive(Clone, Copy, Debug)]
pub struct Mv {
    pub v: Option<f64>,
    pub src: Src,
}
pub const NA: Mv = Mv { v: None, src: Src::Unavail };

/// A cell value: a number, deliberately unset (no limit), or unavailable (not zero!).
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Cn {
    Val(f64),
    Unset,
    NA,
}

impl Mv {
    pub fn cn(&self) -> Cn {
        match (self.src, self.v) {
            (Src::Unlimited, _) => Cn::Unset,
            (_, Some(v)) => Cn::Val(v),
            _ => Cn::NA,
        }
    }
}

fn parse_mv(x: &Value) -> Option<Mv> {
    let a = x.as_array()?;
    Some(Mv { v: a.first()?.as_f64(), src: src_of(a.get(1)?.as_str()?) })
}

pub struct Limit {
    pub name: String,
    pub live: Mv,
    pub origin: String,
    pub rec_origin: Option<String>,
    pub rec_val: Option<f64>,
    pub drift: bool,
    pub reason: String,
}

pub struct Finding {
    pub severity: String,
    pub message: String,
    pub remedy: Option<String>,
}

pub struct Ent {
    pub key: String,
    pub kind: String,
    pub name: String,
    pub docker_name: Option<String>,
    pub image: Option<String>,
    pub ciu_stack: Option<String>,
    pub tier: Option<String>,
    pub protected: bool,
    pub m: HashMap<String, Mv>,
    pub limits: Vec<Limit>,
    pub origin: String,
    pub drift: bool,
    pub drifted: Vec<String>,
    pub unit: Option<String>,
    pub eff_reason: String,
    pub eff_clamped_by: Option<String>,
    pub eff_path: Vec<(String, Option<f64>)>,
    pub findings: Vec<Finding>,
    pub net_label: Option<String>,
    pub search: String,
}

impl Ent {
    pub fn mv(&self, k: &str) -> Mv {
        self.m.get(k).copied().unwrap_or(NA)
    }
    pub fn cn(&self, k: &str) -> Cn {
        self.mv(k).cn()
    }
    pub fn num(&self, k: &str) -> Option<f64> {
        match self.cn(k) {
            Cn::Val(v) => Some(v),
            _ => None,
        }
    }
    pub fn n0(&self, k: &str) -> f64 {
        self.num(k).unwrap_or(0.0)
    }
}

pub struct BlockDev {
    pub name: String,
    pub rbps: Option<f64>,
    pub wbps: Option<f64>,
    pub riops: Option<f64>,
    pub wiops: Option<f64>,
}
pub struct NetDev {
    pub name: String,
    pub rx: Option<f64>,
    pub tx: Option<f64>,
}

pub struct Snap {
    pub ts: f64,
    pub host: HashMap<String, Mv>,
    pub blocks: Vec<BlockDev>,
    pub nets: Vec<NetDev>,
    pub ents: Vec<Ent>,
    pub children: Vec<Vec<usize>>,
    pub root: usize,
}

impl Snap {
    pub fn h(&self, k: &str) -> Option<f64> {
        self.host.get(k).and_then(|m| if m.src == Src::Unavail { None } else { m.v })
    }
}

fn s(v: &Value) -> Option<String> {
    v.as_str().map(|x| x.to_string())
}

fn disp_name(key: &str, docker: &Option<String>) -> String {
    if key.is_empty() {
        return "/ (host)".into();
    }
    if let Some(d) = docker {
        return d.clone();
    }
    let seg = key.rsplit('/').next().unwrap_or(key);
    if seg.starts_with("docker-") && seg.len() > 24 {
        return format!("{}\u{2026}.scope", &seg[..19]);
    }
    seg.to_string()
}

fn parse_ent(v: &Value) -> Option<Ent> {
    let en = &v["entity"];
    let key = en["key"].as_str()?.to_string();
    let docker_name = s(&en["docker"]["name"]);
    let mut m = HashMap::new();
    for (k, x) in v["metrics"].as_object()? {
        if let Some(mv) = parse_mv(x) {
            m.insert(k.clone(), mv);
        }
    }
    let gov = &v["governance"];
    let mut limits = vec![];
    if let Some(o) = gov["limits"].as_object() {
        for (name, l) in o {
            limits.push(Limit {
                name: name.clone(),
                live: Mv {
                    v: l["live_value"].as_f64(),
                    src: src_of(l["live_src"].as_str().unwrap_or("exact")),
                },
                origin: s(&l["origin"]).unwrap_or_default(),
                rec_origin: s(&l["recorded_origin"]),
                rec_val: l["recorded_value"].as_f64(),
                drift: l["drift"].as_bool().unwrap_or(false),
                reason: s(&l["reason"]).unwrap_or_default(),
            });
        }
    }
    let sm = &gov["summary"];
    let eff = &gov["effective_memory_min"];
    let findings = v["findings"]
        .as_array()
        .map(|a| {
            a.iter()
                .map(|f| Finding {
                    severity: s(&f["severity"]).unwrap_or_default(),
                    message: s(&f["message"]).unwrap_or_default(),
                    remedy: s(&f["remedy"]),
                })
                .collect()
        })
        .unwrap_or_default();
    let name = disp_name(&key, &docker_name);
    let search = format!("{} {} {}", key, name, s(&en["docker"]["image"]).unwrap_or_default()).to_lowercase();
    Some(Ent {
        name,
        docker_name,
        image: s(&en["docker"]["image"]),
        ciu_stack: s(&en["ciu"]["stack"]),
        tier: s(&en["tier"]),
        protected: en["is_protected"].as_bool().unwrap_or(false),
        kind: s(&en["kind"]).unwrap_or_default(),
        m,
        limits,
        origin: s(&sm["origin"]).unwrap_or_else(|| "unset".into()),
        drift: sm["drift"].as_bool().unwrap_or(false),
        drifted: sm["drifted_limits"]
            .as_array()
            .map(|a| a.iter().filter_map(s).collect())
            .unwrap_or_default(),
        unit: s(&sm["unit"]),
        eff_reason: s(&eff["reason"]).unwrap_or_default(),
        eff_clamped_by: s(&eff["clamped_by"]["key"]),
        eff_path: eff["path"]
            .as_array()
            .map(|a| a.iter().map(|p| (s(&p["key"]).unwrap_or_default(), p["value"].as_f64())).collect())
            .unwrap_or_default(),
        findings,
        net_label: s(&v["network"]["source_label"]),
        search,
        key,
    })
}

pub fn parse_snap(v: &Value) -> Result<Snap, String> {
    let mut host = HashMap::new();
    for (k, x) in v["host"].as_object().ok_or("frame has no host")? {
        if let Some(mv) = parse_mv(x) {
            host.insert(k.clone(), mv);
        }
    }
    let f = |x: &Value, k: &str| x[k].as_f64();
    let blocks = v["host_meta"]["block_devices"]
        .as_array()
        .map(|a| {
            a.iter()
                .map(|d| BlockDev {
                    name: s(&d["name"]).unwrap_or_default(),
                    rbps: f(d, "read_bps"),
                    wbps: f(d, "write_bps"),
                    riops: f(d, "read_iops"),
                    wiops: f(d, "write_iops"),
                })
                .collect()
        })
        .unwrap_or_default();
    let nets = v["host_meta"]["net_devices"]
        .as_array()
        .map(|a| a.iter().map(|d| NetDev { name: s(&d["name"]).unwrap_or_default(), rx: f(d, "rx_bps"), tx: f(d, "tx_bps") }).collect())
        .unwrap_or_default();
    let mut ents = vec![];
    let mut parents = vec![];
    for (_, e) in v["entities"].as_object().ok_or("frame has no entities")? {
        let p = s(&e["entity"]["parent"]);
        if let Some(x) = parse_ent(e) {
            parents.push(p);
            ents.push(x);
        }
    }
    let index: HashMap<&str, usize> = ents.iter().enumerate().map(|(i, e)| (e.key.as_str(), i)).collect();
    let root = *index.get("").unwrap_or(&0);
    let mut children = vec![vec![]; ents.len()];
    for (i, p) in parents.iter().enumerate() {
        if i == root {
            continue;
        }
        let pi = p.as_deref().and_then(|k| index.get(k).copied()).unwrap_or(root);
        children[pi].push(i);
    }
    Ok(Snap { ts: v["ts"].as_f64().unwrap_or(0.0), host, blocks, nets, ents, children, root })
}

pub struct Recording {
    pub host_id: String,
    pub snaps: Vec<Snap>,
}

pub fn parse_lines(text: &str) -> Result<Recording, String> {
    let mut host_id = String::from("?");
    let mut snaps = vec![];
    for (n, line) in text.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let v: Value = serde_json::from_str(line).map_err(|e| format!("line {}: {e}", n + 1))?;
        if v["type"] == "header" {
            host_id = s(&v["host_id"]).unwrap_or(host_id);
            continue;
        }
        snaps.push(parse_snap(&v).map_err(|e| format!("line {}: {e}", n + 1))?);
    }
    if snaps.is_empty() {
        return Err("recording has no frames".into());
    }
    Ok(Recording { host_id, snaps })
}

pub fn load(path: &str) -> Result<Recording, String> {
    let bytes = std::fs::read(path).map_err(|e| format!("{path}: {e}"))?;
    if bytes.starts_with(&[0x28, 0xb5, 0x2f, 0xfd]) {
        return Err("zstd recordings are not supported by this PoC; decompress first".into());
    }
    parse_lines(&String::from_utf8_lossy(&bytes))
}
