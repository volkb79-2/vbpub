//! The strict configuration reader (design §3).
//!
//! `/etc/thrashguard/thrashguard.toml` plus `thrashguard.toml.d/*.toml` merged
//! **lexically**, the shape fleetd already reads (`fleetd/src/fleetd/facts.py`).
//! Every key is declared here, every value has one accepted form, and **an
//! unknown key is an error** — not a silent setting.
//!
//! ## Why a small TOML reader lives here
//!
//! thrashguard declares no third-party crate (design §5 and the crate README):
//! the Rust gate image caches only `hostobs`'s registry set. So this module
//! accepts exactly the TOML subset the design's config surface needs and refuses
//! everything else **with its line number** instead of guessing:
//!
//! - `key = value` at top level, where value is a string, a boolean, an integer,
//!   a decimal number, or a single-line array of strings;
//! - `[[static_unit]]` tables (with `name` and `cgroup` only);
//! - `#` comments and blank lines. Nothing else: no nested tables, no inline
//!   tables, no multi-line values, no dotted keys.
//!
//! A refused form is a loud refusal rather than a default, which is the whole
//! point: a config key this crate does not know cannot silently become a
//! behaviour.

use std::collections::BTreeMap;
use std::fmt;
use std::path::{Path, PathBuf};

use crate::ENFORCE_REFUSAL;

/// Default `/etc` configuration path (the daemon's `--config` default).
pub const DEFAULT_CONFIG: &str = "/etc/thrashguard/thrashguard.toml";
/// The directory whose `*.toml` files are merged after the main file.
pub const DROPIN_SUFFIX: &str = ".d";
/// Default `allowed_ancestors` (design §3).
pub const DEFAULT_ALLOWED_ANCESTORS: [&str; 3] = [
    "dev-gates.slice",
    "dev-background.slice",
    "dev-buildkitd.slice",
];
/// Default `protected` (design §3).
pub const DEFAULT_PROTECTED: [&str; 3] = ["dev-interactive.slice", "system.slice", "user.slice"];
/// Default state directory (design §3). `/run` is tmpfs, so this is deliberately
/// not durable; the journal rotates (§2).
pub const DEFAULT_STATE_DIR: &str = "/run/thrashguard";
/// The largest accepted configuration file.
pub const MAX_CONFIG_BYTES: usize = 64 * 1024;

/// `mode = off | report | enforce` (design §2, D6).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mode {
    /// Exit without serving: an operator's explicit "do nothing".
    Off,
    /// Journal what would be done and change nothing (the shipped default).
    Report,
    /// Act (P2; refused in this build).
    Enforce,
}

impl Mode {
    /// The spelling the wire and the journal use.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Off => "off",
            Self::Report => "report",
            Self::Enforce => "enforce",
        }
    }
}

/// `victim_rule` (design §2, D3).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum VictimRule {
    /// Most recently registered valid candidate (the default).
    Newest,
    /// Highest measured refault rate (P3).
    Refault,
    /// Smallest unit whose reclaim estimate covers the deficit (P3).
    SmallestEnough,
}

impl VictimRule {
    /// The spelling the configuration and the wire use.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Newest => "newest",
            Self::Refault => "refault",
            Self::SmallestEnough => "smallest-enough",
        }
    }

    /// The rule as configured, or `None` for an unknown spelling.
    pub fn from_name(name: &str) -> Option<Self> {
        match name {
            "newest" => Some(Self::Newest),
            "refault" => Some(Self::Refault),
            "smallest-enough" => Some(Self::SmallestEnough),
            _ => None,
        }
    }
}

/// One `[[static_unit]]` entry: a candidate supplied by `/etc`, for a host with
/// no registrant at all (design §3, §6d).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct StaticUnit {
    /// The unit name a consumer recognises.
    pub name: String,
    /// The cgroup path, relative to the cgroup root (`dev.slice/dev-buildkitd.slice/...`)
    /// or the scope name; resolved like a registration (subtask 3's `units` module).
    pub cgroup: String,
}

/// The validated configuration.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Config {
    /// `off | report | enforce`.
    pub mode: Mode,
    /// Tick period in seconds.
    pub tick_s: u64,
    /// Trigger threshold: host memory PSI `full avg10`, in hundredths of a point.
    pub trigger_avg10: u32,
    /// How long the trigger must hold, in seconds.
    pub hold_s: u64,
    /// Release when `full avg10 <= trigger_avg10 / release_avg10_factor`.
    pub release_avg10_factor: u32,
    /// How long the release condition must hold, in seconds.
    pub release_s: u64,
    /// The victim rule.
    pub victim_rule: VictimRule,
    /// At most this many units parked at once.
    pub max_parked: u32,
    /// A park older than this thaws (one policy key, shared with run-gate's
    /// stall-pause bound, design §4c).
    pub max_park_s: u64,
    /// At most `candidates - 1` parked, so something always progresses.
    pub never_park_last: bool,
    /// Grace before a just-thawed unit may be parked again, in seconds.
    pub thaw_grace_s: u64,
    /// The registrant's expected heartbeat period, in seconds (config side of
    /// the derived `ttl_s`).
    pub heartbeat_s: u64,
    /// How many missed heartbeats expire a registration: `ttl_s = missed x heartbeat_s`.
    pub missed_heartbeats: u32,
    /// `memory.reclaim` chunk size in bytes (P2).
    pub reclaim_chunk_bytes: u64,
    /// At most this many `memory.reclaim` passes per freeze (P2).
    pub reclaim_max_chunks: u32,
    /// Do nothing about pressure sooner than this after boot, in seconds.
    pub boot_grace_s: u64,
    /// The slices a resolved scope must live under.
    pub allowed_ancestors: Vec<String>,
    /// Ancestors never parked, whatever a client asks.
    pub protected: Vec<String>,
    /// Where `events.jsonl` lives.
    pub state_dir: PathBuf,
    /// Whether the registrant socket is expected to be served.
    pub registrant: bool,
    /// `/etc`-supplied candidates.
    pub static_units: Vec<StaticUnit>,
}

impl Config {
    /// A configuration with every design default and `mode = report`.
    pub fn defaults() -> Self {
        Self {
            mode: Mode::Report,
            tick_s: 5,
            trigger_avg10: 300,
            hold_s: 10,
            release_avg10_factor: 2,
            release_s: 60,
            victim_rule: VictimRule::Newest,
            max_parked: 2,
            max_park_s: 1800,
            never_park_last: true,
            thaw_grace_s: 30,
            heartbeat_s: 30,
            missed_heartbeats: 3,
            reclaim_chunk_bytes: 268_435_456,
            reclaim_max_chunks: 8,
            boot_grace_s: 60,
            allowed_ancestors: DEFAULT_ALLOWED_ANCESTORS.map(str::to_string).to_vec(),
            protected: DEFAULT_PROTECTED.map(str::to_string).to_vec(),
            state_dir: PathBuf::from(DEFAULT_STATE_DIR),
            registrant: true,
            static_units: Vec::new(),
        }
    }

    /// The derived `ttl_s`: `missed_heartbeats x heartbeat_s`, never a job bound
    /// (design §3, M2/D13).
    pub fn ttl_s(&self) -> u64 {
        u64::from(self.missed_heartbeats).saturating_mul(self.heartbeat_s)
    }

    /// The release threshold in hundredths: `trigger_avg10 / release_avg10_factor`.
    pub fn release_avg10(&self) -> u32 {
        self.trigger_avg10 / self.release_avg10_factor.max(1)
    }

    /// Refuse a configuration this build cannot honour.
    ///
    /// `mode = enforce` is valid vocabulary and a valid file, but P1 compiles no
    /// act path, so the refusal happens where a run would begin and carries one
    /// line naming the cut that will add it (design §5).
    pub fn check_for_run(&self) -> Result<(), ConfigError> {
        if self.mode == Mode::Enforce {
            return Err(ConfigError::Unsupported(ENFORCE_REFUSAL));
        }
        Ok(())
    }

    /// The default path of the drop-in directory for a main configuration path.
    pub fn dropin_dir(main: &Path) -> PathBuf {
        let mut name = main
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default();
        name.push_str(DROPIN_SUFFIX);
        main.with_file_name(name)
    }
}

/// Why a configuration could not be loaded.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ConfigError {
    /// The file could not be read.
    Read {
        /// The path.
        path: PathBuf,
        /// The underlying reason.
        detail: String,
    },
    /// A syntactic refusal, with its location.
    Syntax {
        /// The file (or `<defaults>`).
        source: String,
        /// 1-based line number.
        line: usize,
        /// What the reader refused.
        detail: String,
    },
    /// A semantic refusal: a key whose value is not one it accepts.
    Value {
        /// The file.
        source: String,
        /// 1-based line number.
        line: usize,
        /// The key.
        key: String,
        /// What it accepts.
        detail: String,
    },
    /// A key this crate does not know: refused rather than ignored.
    UnknownKey {
        /// The file.
        source: String,
        /// 1-based line number.
        line: usize,
        /// The key.
        key: String,
    },
    /// A dropped file that cannot be read, which must not be skipped silently.
    Dropin {
        /// The drop-in directory.
        path: PathBuf,
        /// The file name.
        file: String,
        /// The reason.
        detail: String,
    },
    /// `mode = enforce` in a build that compiles no act path.
    Unsupported(&'static str),
}

impl fmt::Display for ConfigError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Read { path, detail } => write!(f, "cannot read {path:?}: {detail}"),
            Self::Syntax {
                source,
                line,
                detail,
            } => write!(f, "{source}:{line}: {detail}"),
            Self::Value {
                source,
                line,
                key,
                detail,
            } => write!(f, "{source}:{line}: key {key}: {detail}"),
            Self::UnknownKey { source, line, key } => {
                write!(f, "{source}:{line}: unknown key {key:?}")
            }
            Self::Dropin { path, file, detail } => {
                write!(f, "drop-in {file} in {path:?}: {detail}")
            }
            Self::Unsupported(line) => f.write_str(line),
        }
    }
}

impl std::error::Error for ConfigError {}

#[derive(Clone, Debug, PartialEq)]
enum Raw {
    Str(String),
    Bool(bool),
    Int(i64),
    Dec(String),
    Array(Vec<String>),
}

#[derive(Clone, Debug)]
struct Entry {
    value: Raw,
    source: String,
    line: usize,
}

/// One parsed configuration file, before merging.
#[derive(Clone, Debug, Default)]
struct Layer {
    scalars: BTreeMap<String, Entry>,
    static_units: Vec<BTreeMap<String, Entry>>,
}

/// Read the main configuration and its lexical drop-ins.
///
/// Every `*.toml` file in `thrashguard.toml.d/` is merged in file-name byte
/// order after the main file. A missing drop-in directory is not an error; an
/// unreadable one is. Later layers win for a scalar key; a `[[static_unit]]`
/// array is taken as a whole from the last layer that declares one, because
/// merging half a table into another table is a guess about intent.
pub fn load(main: &Path) -> Result<Config, ConfigError> {
    let mut layers = vec![read_layer(main)?];
    let dropins = Config::dropin_dir(main);
    match std::fs::read_dir(&dropins) {
        Ok(entries) => {
            let mut names = Vec::new();
            for entry in entries {
                let entry = entry.map_err(|error| ConfigError::Dropin {
                    path: dropins.clone(),
                    file: "<directory>".to_string(),
                    detail: error.to_string(),
                })?;
                let name = entry.file_name().to_string_lossy().into_owned();
                if name.ends_with(".toml") && entry.path().is_file() {
                    names.push(name);
                }
            }
            names.sort();
            for name in names {
                let path = dropins.join(&name);
                let mut layer = read_layer(&path)?;
                // The drop-in's file name replaces the path in a refusal, so an
                // operator sees `/etc/thrashguard/thrashguard.toml.d/10-x.toml`.
                let label = format!("{}/{}", dropins.display(), name);
                for entry in layer.scalars.values_mut() {
                    entry.source = label.clone();
                }
                for table in &mut layer.static_units {
                    for entry in table.values_mut() {
                        entry.source = label.clone();
                    }
                }
                layers.push(layer);
            }
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => {
            return Err(ConfigError::Dropin {
                path: dropins,
                file: "<directory>".to_string(),
                detail: error.to_string(),
            })
        }
    }
    merge(&layers)
}

fn read_layer(path: &Path) -> Result<Layer, ConfigError> {
    let text = std::fs::read_to_string(path).map_err(|error| ConfigError::Read {
        path: path.to_path_buf(),
        detail: error.to_string(),
    })?;
    if text.len() > MAX_CONFIG_BYTES {
        return Err(ConfigError::Syntax {
            source: path.display().to_string(),
            line: 0,
            detail: format!("the file exceeds the {MAX_CONFIG_BYTES}-byte bound"),
        });
    }
    parse_layer(&text, &path.display().to_string())
}

/// Parse one layer's text. The `source` label appears in every refusal.
fn parse_layer(text: &str, source: &str) -> Result<Layer, ConfigError> {
    let mut layer = Layer::default();
    let mut table: Option<BTreeMap<String, Entry>> = None;
    for (index, raw_line) in text.lines().enumerate() {
        let line_no = index + 1;
        let line = strip_comment(raw_line);
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        if line.starts_with("[[") {
            if !line.ends_with("]]") {
                return Err(ConfigError::Syntax {
                    source: source.into(),
                    line: line_no,
                    detail: "unterminated table header".into(),
                });
            }
            let name = line[2..line.len() - 2].trim();
            if name != "static_unit" {
                return Err(ConfigError::Syntax {
                    source: source.into(),
                    line: line_no,
                    detail: format!("[[{name}]] is not a table this configuration declares (only [[static_unit]])"),
                });
            }
            if let Some(previous) = table.take() {
                layer.static_units.push(previous);
            }
            table = Some(BTreeMap::new());
            continue;
        }
        if line.starts_with('[') {
            return Err(ConfigError::Syntax {
                source: source.into(),
                line: line_no,
                detail: "nested [table] headers are not accepted; the configuration is one flat document plus [[static_unit]]".into(),
            });
        }
        let Some((key, value_text)) = line.split_once('=') else {
            return Err(ConfigError::Syntax {
                source: source.into(),
                line: line_no,
                detail: format!("expected `key = value`, found {line:?}"),
            });
        };
        let key = key.trim();
        if key.is_empty() || key.chars().any(|c| c.is_whitespace() || c == '"') {
            return Err(ConfigError::Syntax {
                source: source.into(),
                line: line_no,
                detail: format!("{key:?} is not a plain key"),
            });
        }
        let value = parse_value(value_text.trim()).map_err(|detail| ConfigError::Syntax {
            source: source.into(),
            line: line_no,
            detail: format!("key {key}: {detail}"),
        })?;
        let entry = Entry {
            value,
            source: source.into(),
            line: line_no,
        };
        let map = match &mut table {
            Some(map) => map,
            None => &mut layer.scalars,
        };
        if map.contains_key(key) {
            return Err(ConfigError::Syntax {
                source: source.into(),
                line: line_no,
                detail: format!("key {key:?} is declared twice in one file"),
            });
        }
        map.insert(key.to_string(), entry);
    }
    if let Some(previous) = table {
        layer.static_units.push(previous);
    }
    Ok(layer)
}

fn strip_comment(line: &str) -> &str {
    let mut in_string = false;
    let mut escaped = false;
    for (index, character) in line.char_indices() {
        match character {
            '\\' if in_string => escaped = !escaped,
            '"' if !escaped => {
                in_string = !in_string;
                escaped = false;
            }
            '#' if !in_string => return &line[..index],
            _ => escaped = false,
        }
    }
    line
}

fn parse_value(text: &str) -> Result<Raw, String> {
    if text.is_empty() {
        return Err("a value is required".to_string());
    }
    if text.starts_with('"') {
        let body = text
            .strip_prefix('"')
            .and_then(|rest| rest.strip_suffix('"'));
        let Some(body) = body else {
            return Err(format!("{text:?} is not a complete quoted string"));
        };
        if body.contains('"') {
            return Err(format!("{text:?} has an unescaped quote"));
        }
        let mut out = String::new();
        let mut chars = body.chars();
        while let Some(character) = chars.next() {
            match character {
                '\\' => match chars.next() {
                    Some('n') => out.push('\n'),
                    Some('t') => out.push('\t'),
                    Some('"') => out.push('"'),
                    Some('\\') => out.push('\\'),
                    Some(other) => return Err(format!("unknown escape \\{other}")),
                    None => return Err("a string ends with a lone backslash".to_string()),
                },
                other => out.push(other),
            }
        }
        return Ok(Raw::Str(out));
    }
    if let Some(body) = text
        .strip_prefix('[')
        .and_then(|rest| rest.strip_suffix(']'))
    {
        let mut items = Vec::new();
        for piece in body.split(',') {
            let piece = piece.trim();
            if piece.is_empty() {
                continue;
            }
            match parse_value(piece)? {
                Raw::Str(value) => items.push(value),
                _ => return Err(format!("array item {piece:?} is not a string")),
            }
        }
        return Ok(Raw::Array(items));
    }
    match text {
        "true" => return Ok(Raw::Bool(true)),
        "false" => return Ok(Raw::Bool(false)),
        _ => {}
    }
    if let Ok(value) = text.parse::<i64>() {
        return Ok(Raw::Int(value));
    }
    if text.chars().all(|c| c.is_ascii_digit() || c == '.') && text.contains('.') {
        return Ok(Raw::Dec(text.to_string()));
    }
    Err(format!(
        "{text:?} is not a string, boolean, integer, decimal or string array"
    ))
}

/// One accepted key at the top level, in the order the design lists them.
const TOP_KEYS: [&str; 20] = [
    "mode",
    "tick_s",
    "trigger_avg10",
    "hold_s",
    "release_avg10_factor",
    "release_s",
    "victim_rule",
    "max_parked",
    "max_park_s",
    "never_park_last",
    "thaw_grace_s",
    "heartbeat_s",
    "missed_heartbeats",
    "reclaim_chunk_bytes",
    "reclaim_max_chunks",
    "boot_grace_s",
    "allowed_ancestors",
    "protected",
    "state_dir",
    "registrant",
];

fn merge(layers: &[Layer]) -> Result<Config, ConfigError> {
    let mut merged: BTreeMap<String, Entry> = BTreeMap::new();
    let mut static_units: Vec<BTreeMap<String, Entry>> = Vec::new();
    for layer in layers {
        for (key, entry) in &layer.scalars {
            merged.insert(key.clone(), entry.clone());
        }
        if !layer.static_units.is_empty() {
            static_units = layer.static_units.clone();
        }
    }
    for (key, entry) in &merged {
        if !TOP_KEYS.contains(&key.as_str()) {
            return Err(ConfigError::UnknownKey {
                source: entry.source.clone(),
                line: entry.line,
                key: key.clone(),
            });
        }
    }
    let mut config = Config::defaults();
    for (key, entry) in &merged {
        apply(&mut config, key, &entry.value, entry)?;
    }
    config.static_units = parse_static_units(&static_units)?;
    Ok(config)
}

fn parse_static_units(tables: &[BTreeMap<String, Entry>]) -> Result<Vec<StaticUnit>, ConfigError> {
    let mut units = Vec::new();
    for table in tables {
        let mut name = None;
        let mut cgroup = None;
        for (key, entry) in table {
            match key.as_str() {
                "name" => match &entry.value {
                    Raw::Str(value) => {
                        if value.trim().is_empty() {
                            return Err(ConfigError::Value {
                                source: entry.source.clone(),
                                line: entry.line,
                                key: key.clone(),
                                detail: "must not be empty".into(),
                            });
                        }
                        name = Some(value.clone());
                    }
                    _ => return Err(not_a_string(entry, key)),
                },
                "cgroup" => match &entry.value {
                    Raw::Str(value) => {
                        if value.is_empty() || value.contains("..") || value.contains('\0') {
                            return Err(ConfigError::Value {
                                source: entry.source.clone(),
                                line: entry.line,
                                key: key.clone(),
                                detail: "must be a non-empty relative cgroup name with no `..`"
                                    .into(),
                            });
                        }
                        cgroup = Some(value.clone());
                    }
                    _ => return Err(not_a_string(entry, key)),
                },
                other => {
                    return Err(ConfigError::UnknownKey {
                        source: entry.source.clone(),
                        line: entry.line,
                        key: other.to_string(),
                    })
                }
            }
        }
        let (Some(name), Some(cgroup)) = (name, cgroup) else {
            let entry = table.values().next();
            let (source, line) = entry.map_or((String::from("<defaults>"), 0), |entry| {
                (entry.source.clone(), entry.line)
            });
            return Err(ConfigError::Value {
                source,
                line,
                key: "static_unit".into(),
                detail: "a [[static_unit]] needs both `name` and `cgroup`".into(),
            });
        };
        if units.iter().any(|unit: &StaticUnit| unit.name == name) {
            let entry = table.values().next().expect("a table with keys");
            return Err(ConfigError::Value {
                source: entry.source.clone(),
                line: entry.line,
                key: "name".into(),
                detail: format!("duplicate static unit name {name:?}"),
            });
        }
        units.push(StaticUnit { name, cgroup });
    }
    Ok(units)
}

fn not_a_string(entry: &Entry, key: &str) -> ConfigError {
    ConfigError::Value {
        source: entry.source.clone(),
        line: entry.line,
        key: key.to_string(),
        detail: "must be a quoted string".into(),
    }
}

fn apply(config: &mut Config, key: &str, value: &Raw, entry: &Entry) -> Result<(), ConfigError> {
    let bad = |detail: String| ConfigError::Value {
        source: entry.source.clone(),
        line: entry.line,
        key: key.to_string(),
        detail,
    };
    match key {
        "mode" => match as_str(value, entry, key)?.as_str() {
            "off" => config.mode = Mode::Off,
            "report" => config.mode = Mode::Report,
            "enforce" => config.mode = Mode::Enforce,
            other => {
                return Err(entry_value(
                    entry,
                    key,
                    format!("{other:?} is not one of: off, report, enforce"),
                ))
            }
        },
        "tick_s" => config.tick_s = bounded_u64(value, entry, key, 1, 3600)?,
        "trigger_avg10" => config.trigger_avg10 = decimal_hundredths(value, entry, key)?,
        "hold_s" => config.hold_s = bounded_u64(value, entry, key, 0, 86_400)?,
        "release_avg10_factor" => {
            let factor = bounded_u64(value, entry, key, 2, 100)?;
            config.release_avg10_factor = u32::try_from(factor).expect("bounded above");
        }
        "release_s" => config.release_s = bounded_u64(value, entry, key, 0, 86_400)?,
        "victim_rule" => {
            let name = as_str(value, entry, key)?;
            let Some(rule) = VictimRule::from_name(&name) else {
                return Err(entry_value(
                    entry,
                    key,
                    format!("{name:?} is not one of: newest, refault, smallest-enough"),
                ));
            };
            config.victim_rule = rule;
        }
        "max_parked" => {
            config.max_parked =
                u32::try_from(bounded_u64(value, entry, key, 0, 64)?).expect("bounded above")
        }
        "max_park_s" => config.max_park_s = bounded_u64(value, entry, key, 1, 86_400)?,
        "never_park_last" => config.never_park_last = as_bool(value, entry, key)?,
        "thaw_grace_s" => config.thaw_grace_s = bounded_u64(value, entry, key, 0, 3600)?,
        "heartbeat_s" => config.heartbeat_s = bounded_u64(value, entry, key, 1, 3600)?,
        "missed_heartbeats" => {
            config.missed_heartbeats =
                u32::try_from(bounded_u64(value, entry, key, 1, 100)?).expect("bounded above")
        }
        "reclaim_chunk_bytes" => {
            config.reclaim_chunk_bytes = bounded_u64(value, entry, key, 1, 1 << 34)?
        }
        "reclaim_max_chunks" => {
            config.reclaim_max_chunks =
                u32::try_from(bounded_u64(value, entry, key, 0, 64)?).expect("bounded above")
        }
        "boot_grace_s" => config.boot_grace_s = bounded_u64(value, entry, key, 0, 3600)?,
        "allowed_ancestors" | "protected" => {
            let names = as_array(value, entry, key)?;
            let which = if key == "allowed_ancestors" {
                "allowed_ancestors"
            } else {
                "protected"
            };
            for name in &names {
                if name.trim().is_empty() || name.contains('/') {
                    return Err(entry_value(
                        entry,
                        key,
                        format!("{name:?} is not a slice name"),
                    ));
                }
            }
            if names.is_empty() {
                return Err(entry_value(
                    entry,
                    key,
                    format!("{which} must name at least one slice"),
                ));
            }
            if which == "allowed_ancestors" {
                config.allowed_ancestors = names;
            } else {
                config.protected = names;
            }
        }
        "state_dir" => {
            let text = as_str(value, entry, key)?;
            if text.contains('\0') {
                return Err(entry_value(entry, key, "must contain no NUL".to_string()));
            }
            config.state_dir = PathBuf::from(text);
        }
        "registrant" => config.registrant = as_bool(value, entry, key)?,
        other => {
            return Err(ConfigError::UnknownKey {
                source: entry.source.clone(),
                line: entry.line,
                key: other.to_string(),
            })
        }
    }
    let _ = bad;
    Ok(())
}

fn entry_value(entry: &Entry, key: &str, detail: String) -> ConfigError {
    ConfigError::Value {
        source: entry.source.clone(),
        line: entry.line,
        key: key.to_string(),
        detail,
    }
}

fn as_str(value: &Raw, entry: &Entry, key: &str) -> Result<String, ConfigError> {
    match value {
        Raw::Str(text) => Ok(text.clone()),
        _ => Err(not_a_string(entry, key)),
    }
}

fn as_bool(value: &Raw, entry: &Entry, key: &str) -> Result<bool, ConfigError> {
    match value {
        Raw::Bool(flag) => Ok(*flag),
        _ => Err(entry_value(entry, key, "must be true or false".to_string())),
    }
}

fn as_array(value: &Raw, entry: &Entry, key: &str) -> Result<Vec<String>, ConfigError> {
    match value {
        Raw::Array(items) => Ok(items.clone()),
        _ => Err(entry_value(
            entry,
            key,
            "must be an array of quoted strings".to_string(),
        )),
    }
}

fn bounded_u64(
    value: &Raw,
    entry: &Entry,
    key: &str,
    min: u64,
    max: u64,
) -> Result<u64, ConfigError> {
    let Raw::Int(number) = value else {
        return Err(entry_value(entry, key, "must be an integer".to_string()));
    };
    let number = u64::try_from(*number)
        .map_err(|_| entry_value(entry, key, "must not be negative".to_string()))?;
    if !(min..=max).contains(&number) {
        return Err(entry_value(
            entry,
            key,
            format!("must be in {min}..={max}, got {number}"),
        ));
    }
    Ok(number)
}

/// A decimal like `3.00` as integer hundredths; reuse [`crate::psi`]'s rule.
fn decimal_hundredths(value: &Raw, entry: &Entry, key: &str) -> Result<u32, ConfigError> {
    let Raw::Dec(text) = value else {
        return Err(entry_value(
            entry,
            key,
            "must be a decimal number such as 3.00".to_string(),
        ));
    };
    let hundredths =
        crate::psi::parse_avg10_public(text).map_err(|detail| entry_value(entry, key, detail))?;
    if hundredths > 10_000 {
        return Err(entry_value(entry, key, format!("{text} is above 100.00")));
    }
    Ok(hundredths)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::TempTree;

    const DOCUMENTS: [(&str, &str); 3] = [
        ("README.md", include_str!("../README.md")),
        ("docs/CONSUMERS.md", include_str!("../docs/CONSUMERS.md")),
        (
            "docs/DESIGN-GUIDE.md",
            include_str!("../docs/DESIGN-GUIDE.md"),
        ),
    ];

    fn toml_blocks(markdown: &str) -> (Vec<String>, bool) {
        let mut blocks = Vec::new();
        let mut current: Option<String> = None;
        for line in markdown.lines() {
            if current.is_none() && line.trim_start().starts_with("```toml") {
                current = Some(String::new());
            } else if line.trim() == "```" {
                if let Some(block) = current.take() {
                    blocks.push(block);
                }
            } else if let Some(block) = current.as_mut() {
                block.push_str(line);
                block.push('\n');
            }
        }
        (blocks, current.is_some())
    }

    fn has_word(text: &str, word: &str) -> bool {
        text.split(|character: char| {
            !(character.is_ascii_alphanumeric() || matches!(character, '-' | '_'))
        })
        .any(|token| token == word)
    }

    fn markdown_targets(text: &str) -> Vec<&str> {
        let mut targets = Vec::new();
        let mut rest = text;
        while let Some(start) = rest.find("](") {
            let after = &rest[start + 2..];
            let Some(end) = after.find(')') else {
                break;
            };
            let target = after[..end]
                .split_whitespace()
                .next()
                .unwrap_or_default()
                .trim_matches(&['<', '>'][..]);
            targets.push(target);
            rest = &after[end + 1..];
        }
        targets
    }

    fn heading_slug(line: &str) -> Option<String> {
        let heading = line.trim_start_matches('#');
        if heading.len() == line.len() {
            return None;
        }
        let mut slug = String::new();
        let mut pending_dash = false;
        for character in heading.trim().chars() {
            if character.is_ascii_alphanumeric() || matches!(character, '-' | '_') {
                if pending_dash && !slug.is_empty() && !slug.ends_with('-') {
                    slug.push('-');
                }
                pending_dash = false;
                slug.push(character.to_ascii_lowercase());
            } else if character.is_whitespace() {
                pending_dash = true;
            }
        }
        Some(slug)
    }

    fn anchor_exists(text: &str, anchor: &str) -> bool {
        text.lines()
            .filter_map(heading_slug)
            .any(|slug| slug == anchor)
    }

    /// Every config example the three documents show must load with THIS loader.
    #[test]
    fn every_documented_example_loads() {
        let mut example_count = 0;
        let mut all_docs = String::new();
        for (path, markdown) in DOCUMENTS {
            all_docs.push_str(markdown);
            let (examples, unclosed) = toml_blocks(markdown);
            assert!(
                !unclosed,
                "{path}: an opening TOML fence has no closing fence"
            );
            for (index, text) in examples.iter().enumerate() {
                let label = format!("{path} TOML example {}", index + 1);
                let config = parse_layer(text, &label).and_then(|layer| merge(&[layer]));
                assert!(config.is_ok(), "{label}: {:?}", config.err());
                example_count += 1;
            }
        }
        assert!(
            example_count > 0,
            "the consumer docs must show a config example"
        );

        for (vocabulary, values) in [
            ("mode", &["off", "report", "enforce"][..]),
            ("victim_rule", &["newest", "refault", "smallest-enough"][..]),
            ("priority", &["freezable", "last-resort"][..]),
            (
                "refusal reason",
                &[
                    "none",
                    "several",
                    "protected",
                    "malformed",
                    "unreadable",
                    "duplicate",
                    "missing",
                    "wrong-socket",
                    "unknown-key",
                    "unknown-verb",
                    "version",
                    "too-large",
                    "too-many-sessions",
                    "value",
                ][..],
            ),
        ] {
            for value in values {
                assert!(
                    has_word(&all_docs, value),
                    "{vocabulary} value {value:?} is absent from the shipped docs"
                );
            }
        }

        let root = Path::new(env!("CARGO_MANIFEST_DIR"));
        for (source, markdown) in DOCUMENTS {
            for target in markdown_targets(markdown) {
                if target.is_empty() || target.contains("://") {
                    continue;
                }
                if let Some(anchor) = target.strip_prefix('#') {
                    assert!(
                        anchor_exists(markdown, anchor),
                        "{source}: link anchor #{anchor} is missing from this document"
                    );
                    continue;
                }
                let (path_part, anchor) = target.split_once('#').unwrap_or((target, ""));
                let path = root
                    .join(source)
                    .parent()
                    .expect("doc parent")
                    .join(path_part);
                assert!(path.exists(), "{source}: broken local link {target:?}");
                if !anchor.is_empty() {
                    assert!(
                        path.is_file(),
                        "{source}: anchor link target is not a file: {target:?}"
                    );
                    let linked = std::fs::read_to_string(&path)
                        .unwrap_or_else(|error| panic!("cannot read {}: {error}", path.display()));
                    assert!(
                        anchor_exists(&linked, anchor),
                        "{source}: link anchor #{anchor} is missing from {path_part}"
                    );
                }
            }
        }
    }

    #[test]
    fn consumer_static_path_and_install_units_match_the_shipped_tree() {
        const CONSUMERS: &str = include_str!("../docs/CONSUMERS.md");
        const DESIGN: &str = include_str!("../../docs/design/thrashguard/DESIGN.md");
        const STATIC_CGROUP: &str = "dev.slice/dev-buildkitd.slice";
        const UNIT_NAMES: [&str; 4] = [
            "thrashguard.service",
            "thrashguard-reader.socket",
            "thrashguard-registrant.socket",
            "thrashguard.slice",
        ];
        let root = Path::new(env!("CARGO_MANIFEST_DIR"));

        assert!(
            CONSUMERS.contains(&format!("cgroup = \"{STATIC_CGROUP}\"")),
            "the consumer's static cgroup example must include its parent slice"
        );
        assert!(
            DESIGN.contains(STATIC_CGROUP),
            "the consumer example must match the design's cgroup hierarchy"
        );

        let install_line = format!(
            "sudo install -m 0644 {} /etc/systemd/system/",
            UNIT_NAMES
                .iter()
                .map(|name| format!("deploy/systemd/{name}"))
                .collect::<Vec<_>>()
                .join(" ")
        );
        assert!(
            CONSUMERS.contains(&install_line),
            "the systemd install command must list every shipped unit"
        );
        for name in UNIT_NAMES {
            let unit = root.join("deploy/systemd").join(name);
            assert!(unit.is_file(), "the documented unit {name} must ship");
        }
        let service = std::fs::read_to_string(root.join("deploy/systemd/thrashguard.service"))
            .expect("the shipped service unit");
        assert!(service.contains("Requires=thrashguard-reader.socket"));
        assert!(service.contains("After=systemd-tmpfiles-setup.service thrashguard-reader.socket"));
        assert!(
            !service.contains("Requires=thrashguard-registrant.socket"),
            "the registrant dependency is opt-in in the documented drop-in"
        );
        for (name, role) in [
            ("thrashguard-reader.socket", "reader"),
            ("thrashguard-registrant.socket", "registrant"),
        ] {
            let socket = std::fs::read_to_string(root.join("deploy/systemd").join(name))
                .unwrap_or_else(|error| panic!("cannot read {name}: {error}"));
            assert!(
                socket.contains("Accept=no"),
                "{name} must pass a listener fd"
            );
            assert!(
                socket.contains("Service=thrashguard.service"),
                "{name} must activate the shipped service"
            );
            assert!(
                socket.contains(&format!("FileDescriptorName={role}")),
                "{name} must announce the expected role"
            );
        }
        assert!(
            root.join("deploy/tmpfiles.d/thrashguard.conf").is_file()
                && CONSUMERS.contains("deploy/tmpfiles.d/thrashguard.conf"),
            "the documented tmpfiles rules must ship"
        );
        for required in [
            "target/release/thrashguard /usr/local/bin/thrashguard",
            "thrashguard.toml /etc/thrashguard/thrashguard.toml",
            "groupadd --force --system thrashguard-registrant",
            "groupadd --force --system thrashguard-reader",
            "Requires=thrashguard-registrant.socket\nAfter=thrashguard-registrant.socket",
            "systemctl enable --now thrashguard-reader.socket thrashguard-registrant.socket thrashguard.service",
            "systemctl disable --now thrashguard-registrant.socket",
            "systemctl enable --now thrashguard-reader.socket thrashguard.service",
        ] {
            assert!(CONSUMERS.contains(required), "missing install step: {required}");
        }
    }

    #[test]
    fn closed_wire_reason_vocabulary_is_exact_in_consumer_docs() {
        const CONSUMERS: &str = include_str!("../docs/CONSUMERS.md");
        // Anchor this check to the one closed-list sentence; the expected set is
        // duplicated from socket::refuse plus daemon::too-many-sessions, so a code
        // vocabulary change must update this list as well as the consumer contract.
        let list = CONSUMERS
            .split("closed reason vocabulary ")
            .nth(1)
            .and_then(|tail| tail.split(';').next())
            .expect("the closed wire reason sentence");
        let documented = list.split('`').skip(1).step_by(2).collect::<Vec<_>>();
        assert_eq!(
            documented,
            [
                "unreadable",
                "missing",
                "wrong-socket",
                "unknown-key",
                "unknown-verb",
                "version",
                "too-large",
                "too-many-sessions",
                "value",
            ],
            "the named closed reason list must match the daemon's wire vocabulary"
        );
    }

    #[test]
    fn design_matches_assay_tim030_and_the_p1_emission_boundary() {
        const DESIGN: &str = include_str!("../../docs/design/thrashguard/DESIGN.md");
        const ASSAY_SPEC: &str = include_str!("../../assay/docs/SPEC.md");
        let normalized_spec = ASSAY_SPEC.split_whitespace().collect::<Vec<_>>().join(" ");
        let normalized_design = DESIGN.split_whitespace().collect::<Vec<_>>().join(" ");
        for phrase in [
            "effective CPU seconds (TIM-020) as raw, un-normalized CPU seconds",
            "ref_cpu_s = effective_cpu_s * single_core / baseline_score",
            "assay holds no score constant",
            "a record of another major counts as no record",
        ] {
            assert!(
                normalized_spec.contains(phrase),
                "TIM-030 no longer says {phrase:?}"
            );
        }
        for phrase in [
            "TIM-020 is planned and is not implemented in the current assay code",
            "record effective CPU seconds as raw, un-normalized seconds",
            "ref_cpu_s = effective_cpu_s * single_core / baseline_score",
            "assay holds no score constant",
            "A record for another major counts as no record",
            "P1 is observe-only and emits no hostobs events",
            "idempotent on `(event name, boot_id, seq)` and best-effort",
        ] {
            assert!(
                normalized_design.contains(phrase),
                "the design contract is missing {phrase:?}"
            );
        }
    }

    #[test]
    fn unknown_keys_and_bad_values_are_refused_with_a_line() {
        let cases = [
            "mode = \"sleep\"\n",                       // not in the closed set
            "mode = 1\n",                               // wrong kind
            "ttl_s = 90\n",                             // not a config key: derived instead
            "tick_s = 0\n",                             // below the accepted range
            "tick_s = 9999\n",                          // above it
            "trigger_avg10 = 3\n",                      // a decimal is required
            "allowed_ancestors = []\n",                 // at least one
            "allowed_ancestors = \"dev.slice\"\n",      // not an array
            "registrant = \"yes\"\n",                   // not a boolean
            "[[unit]]\nname = \"x\"\ncgroup = \"y\"\n", // unknown table
            "[limits]\nmax = 1\n",                      // nested table
            "mode = \"report\"\nmode = \"report\"\n",   // twice in one file
        ];
        for text in cases {
            // A refusal may come from the syntax layer or from the semantic layer; both are
            // refusals, and both name the file. What must NEVER happen is a silent accept.
            let error = match parse_layer(text, "case.toml") {
                Err(error) => error,
                Ok(layer) => merge(&[layer]).expect_err("a refusal"),
            };
            assert!(
                matches!(
                    error,
                    ConfigError::Value { .. }
                        | ConfigError::UnknownKey { .. }
                        | ConfigError::Syntax { .. }
                ),
                "{text:?} -> {error:?}"
            );
            assert!(
                error.to_string().starts_with("case.toml"),
                "{text:?} -> {error}"
            );
        }
        // The first of them names its own line, so an operator can find it.
        if let Err(ConfigError::Value { line, .. }) =
            merge(&[parse_layer("tick_s = 0", "case.toml").expect("syntax")])
        {
            assert_eq!(line, 1);
        } else {
            panic!("tick_s = 0 must be refused");
        }
    }

    #[test]
    fn duplicate_static_units_and_incomplete_tables_are_refused() {
        let text = "[[static_unit]]\nname = \"a\"\ncgroup = \"x\"\n[[static_unit]]\nname = \"a\"\ncgroup = \"y\"\n";
        let error = merge(&[parse_layer(text, "t").expect("syntax")]).expect_err("duplicate");
        assert!(error.to_string().contains("duplicate static unit name"));
        let incomplete = "[[static_unit]]\nname = \"a\"\n";
        let error =
            merge(&[parse_layer(incomplete, "t").expect("syntax")]).expect_err("incomplete");
        assert!(error.to_string().contains("both `name` and `cgroup`"));
        let unknown = "[[static_unit]]\nname = \"a\"\ncgroup = \"x\"\nscore = 3\n";
        let error = merge(&[parse_layer(unknown, "t").expect("syntax")]).expect_err("unknown key");
        assert!(matches!(error, ConfigError::UnknownKey { .. }), "{error:?}");
    }

    #[test]
    fn enforce_is_refused_at_the_run_boundary_with_one_line() {
        let config =
            merge(&[parse_layer("mode = \"enforce\"", "t").expect("syntax")]).expect("loads");
        let refusal = config
            .check_for_run()
            .expect_err("P1 cannot honour enforce");
        let text = refusal.to_string();
        assert!(
            text.starts_with("mode=enforce is not available in this build"),
            "{text}"
        );
        assert!(text.contains("no act path"), "{text}");
        assert_eq!(text.lines().count(), 1, "{text}");
        // It is valid vocabulary: parsing succeeds so `status` can report it.
        assert_eq!(config.mode, Mode::Enforce);
    }

    #[test]
    fn dropins_merge_lexically_and_later_files_win() {
        let tree = TempTree::new("config-dropin");
        let main = tree.write("thrashguard.toml", "mode = \"off\"\ntrigger_avg10 = 3.00\n");
        tree.write("thrashguard.toml.d/10-a.toml", "mode = \"report\"\n");
        tree.write("thrashguard.toml.d/20-b.toml", "tick_s = 9\n");
        tree.write("thrashguard.toml.d/README.txt", "not a config file\n");
        let config = load(&main).expect("loads with drop-ins");
        assert_eq!(config.mode, Mode::Report, "10-a wins over the main file");
        assert_eq!(config.tick_s, 9, "20-b is merged after 10-a");
        assert_eq!(config.trigger_avg10, 300);
    }

    #[test]
    fn an_unreadable_dropin_is_not_skipped_and_a_missing_dir_is_fine() {
        let tree = TempTree::new("config-baddropin");
        let main = tree.write("thrashguard.toml", "mode = \"report\"\n");
        assert!(load(&main).is_ok(), "no .d directory is not an error");
        tree.write("thrashguard.toml.d/10-bad.toml", "mode = \"nonsense\"\n");
        let error = load(&main).expect_err("a bad drop-in must fail the load");
        assert!(error.to_string().contains("10-bad.toml"), "{error}");
        let main = tree.write("thrashguard.toml", "mode = \"x\"\n");
        let error = load(&main).expect_err("a bad main file fails");
        assert!(matches!(error, ConfigError::Value { .. }), "{error:?}");
    }
}
