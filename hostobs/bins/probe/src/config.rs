//! The probe's strict, dependency-free configuration and start-up argument rules.
//!
//! The accepted file is deliberately a small subset of TOML. Every file this parser
//! accepts is valid TOML, but it does not accept aliases, dotted keys, inline tables,
//! escapes or any value outside the shape in `docs/PROBE-PROCESS.md` section (g).
//!
//! Protocol-wide keys have exactly one spelling: `[limits] max_rows`. A per-level form
//! such as `[limits.observe]` is an unknown section, so it cannot silently configure a
//! level while the probe-wide value keeps its default (P-06 backlog B3).

use std::collections::{BTreeSet, HashMap, HashSet};
use std::fmt;
use std::fs::File;
use std::io::Read;
use std::os::unix::net::UnixListener;
use std::path::{Component, Path, PathBuf};

use hostobs_probe::limits::Level;
use hostobs_probe::server::ServerLimits;

/// Largest accepted configuration file.
pub(crate) const MAX_CONFIG_BYTES: usize = 64 * 1024;

const KEY_LEVELS: &str = "probe.levels";
const KEY_TICK_MS: &str = "probe.tick_ms";
const KEY_CGROUP_ROOT: &str = "probe.cgroup_root";
const KEY_MAX_ROWS: &str = "limits.max_rows";

/// A parsed probe configuration.
#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct Config {
    /// The levels whose listeners must be present.
    pub(crate) levels: Vec<Level>,
    /// The sampler period and subscription interval unit.
    pub(crate) tick_ms: u32,
    /// The cgroup tree the sampler walks.
    pub(crate) cgroup_root: PathBuf,
    /// The largest row count a selection may resolve to.
    pub(crate) max_rows: u32,
}

impl Config {
    /// The serving bounds with the two configured probe-wide keys applied.
    pub(crate) fn server_limits(&self) -> ServerLimits {
        let mut limits = ServerLimits::new();
        limits.probe.tick_ms = self.tick_ms;
        limits.probe.max_rows = self.max_rows;
        limits
    }
}

/// One command-line action, before phase-B wiring.
#[derive(Clone, Debug, Eq, PartialEq)]
#[cfg_attr(not(test), allow(dead_code))]
pub(crate) enum Action {
    /// Print the package version and exit successfully.
    Version,
    /// Read this explicit configuration path and serve.
    Run {
        /// The only accepted configuration location for this invocation.
        config: PathBuf,
    },
}

/// A one-line command-line refusal.
#[derive(Clone, Debug, Eq, PartialEq)]
#[cfg_attr(not(test), allow(dead_code))]
pub(crate) struct ArgumentError(String);

impl fmt::Display for ArgumentError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// Parse `hostobs-probe` arguments, excluding argv[0].
///
/// The only successful forms are exactly `--version`, or exactly one
/// `--config <path>`.
#[cfg_attr(not(test), allow(dead_code))]
pub(crate) fn parse_args(args: &[String]) -> Result<Action, ArgumentError> {
    if args.len() == 1 && args[0] == "--version" {
        return Ok(Action::Version);
    }
    if args.is_empty() {
        return Err(ArgumentError(String::from(
            "usage: hostobs-probe --config <path> | --version",
        )));
    }

    let mut config: Option<PathBuf> = None;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--config" => {
                if config.is_some() {
                    return Err(ArgumentError(String::from(
                        "--config may be given exactly once",
                    )));
                }
                let Some(value) = args.get(index + 1) else {
                    return Err(ArgumentError(String::from("--config requires a path")));
                };
                if value.is_empty() || value.starts_with("--") {
                    return Err(ArgumentError(String::from("--config requires a path")));
                }
                config = Some(PathBuf::from(value));
                index += 2;
            }
            unknown => {
                return Err(ArgumentError(format!(
                    "unexpected argument {unknown:?}; expected --config <path> or --version"
                )));
            }
        }
    }
    config
        .map(|config| Action::Run { config })
        .ok_or_else(|| ArgumentError(String::from("--config is required")))
}

/// A one-line configuration refusal naming its line and key.
#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct ConfigError {
    line: usize,
    key: String,
    message: String,
}

impl ConfigError {
    fn at(line: usize, key: impl Into<String>, message: impl Into<String>) -> ConfigError {
        ConfigError {
            line,
            key: key.into(),
            message: message.into(),
        }
    }

    fn file(message: impl Into<String>) -> ConfigError {
        ConfigError::at(0, "<file>", message)
    }
}

impl fmt::Display for ConfigError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "line {} key {}: {}", self.line, self.key, self.message)
    }
}

impl std::error::Error for ConfigError {}

/// Read a configuration file and enforce its byte and UTF-8 bounds.
pub(crate) fn read(path: &Path) -> Result<Config, ConfigError> {
    let file = File::open(path)
        .map_err(|error| ConfigError::file(format!("cannot open {}: {error}", path.display())))?;
    let bound = u64::try_from(MAX_CONFIG_BYTES.saturating_add(1)).unwrap_or(u64::MAX);
    let mut bytes = Vec::new();
    file.take(bound)
        .read_to_end(&mut bytes)
        .map_err(|error| ConfigError::file(format!("cannot read configuration: {error}")))?;
    decode(&bytes)
}

fn decode(bytes: &[u8]) -> Result<Config, ConfigError> {
    if bytes.len() > MAX_CONFIG_BYTES {
        return Err(ConfigError::file(format!(
            "the file exceeds the {MAX_CONFIG_BYTES}-byte bound"
        )));
    }
    let text =
        std::str::from_utf8(bytes).map_err(|_| ConfigError::file("the file is not UTF-8"))?;
    parse(text)
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
enum Section {
    Probe,
    Limits,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
enum Key {
    Levels,
    TickMs,
    CgroupRoot,
    MaxRows,
}

#[derive(Debug, Eq, PartialEq)]
enum Value {
    String(String),
    Integer(u64),
    Array(Vec<String>),
}

#[derive(Default)]
struct Slots {
    levels: Option<(usize, Vec<String>)>,
    tick_ms: Option<(usize, u64)>,
    cgroup_root: Option<(usize, String)>,
    max_rows: Option<(usize, u64)>,
    keys: BTreeSet<Key>,
    sections: BTreeSet<Section>,
}

fn parse(text: &str) -> Result<Config, ConfigError> {
    let mut slots = Slots::default();
    let mut section: Option<Section> = None;

    for (offset, raw) in text.lines().enumerate() {
        let line = offset.saturating_add(1);
        let before_comment = strip_comment(raw);
        if raw
            .chars()
            .any(|character| character.is_control() && character != '\t')
        {
            let key = before_comment
                .split_once('=')
                .map_or("<line>", |(key, _value)| trim_toml(key));
            return Err(ConfigError::at(
                line,
                key,
                "contains a forbidden control character",
            ));
        }
        let trimmed = trim_toml(before_comment);
        if trimmed.is_empty() {
            continue;
        }
        if trimmed.starts_with('[') {
            let Some(name) = trimmed
                .strip_prefix('[')
                .and_then(|rest| rest.strip_suffix(']'))
            else {
                return Err(ConfigError::at(line, trimmed, "malformed section header"));
            };
            let parsed = match name {
                "probe" => Section::Probe,
                "limits" => Section::Limits,
                _ => {
                    return Err(ConfigError::at(
                        line,
                        name,
                        "unknown section; only [probe] and [limits] are accepted",
                    ));
                }
            };
            if !slots.sections.insert(parsed) {
                return Err(ConfigError::at(
                    line,
                    format!("[{name}]"),
                    "duplicate section",
                ));
            }
            section = Some(parsed);
            continue;
        }

        let Some((raw_key, raw_value)) = trimmed.split_once('=') else {
            return Err(ConfigError::at(
                line,
                trimmed,
                "expected a section header or key = value",
            ));
        };
        let key_text = trim_toml(raw_key);
        let value_text = trim_toml(raw_value);
        if key_text.is_empty() || value_text.is_empty() {
            return Err(ConfigError::at(
                line,
                key_text,
                "a key and a value are both required",
            ));
        }
        let Some(section) = section else {
            return Err(ConfigError::at(
                line,
                key_text,
                "key appears outside a section",
            ));
        };
        let (key, expected_section) = match key_text {
            "levels" => (Key::Levels, Section::Probe),
            "tick_ms" => (Key::TickMs, Section::Probe),
            "cgroup_root" => (Key::CgroupRoot, Section::Probe),
            "max_rows" => (Key::MaxRows, Section::Limits),
            _ => {
                return Err(ConfigError::at(
                    line,
                    key_text,
                    "unknown key or unsupported dotted/per-level form",
                ));
            }
        };
        if section != expected_section {
            return Err(ConfigError::at(
                line,
                key_text,
                "key appears outside its section",
            ));
        }
        if !slots.keys.insert(key) {
            return Err(ConfigError::at(line, key_text, "duplicate key"));
        }
        let value = parse_value(value_text, line, key_text)?;
        match (key, value) {
            (Key::Levels, Value::Array(levels)) => slots.levels = Some((line, levels)),
            (Key::TickMs, Value::Integer(value)) => slots.tick_ms = Some((line, value)),
            (Key::CgroupRoot, Value::String(value)) => {
                slots.cgroup_root = Some((line, value));
            }
            (Key::MaxRows, Value::Integer(value)) => slots.max_rows = Some((line, value)),
            (key, _) => {
                return Err(ConfigError::at(
                    line,
                    key_text,
                    format!("{} has the wrong value type", key_name(key)),
                ));
            }
        }
    }

    let (levels_line, levels) = slots
        .levels
        .ok_or_else(|| ConfigError::at(0, KEY_LEVELS, "required key is missing"))?;
    let (tick_line, tick_ms) = slots
        .tick_ms
        .ok_or_else(|| ConfigError::at(0, KEY_TICK_MS, "required key is missing"))?;
    let (root_line, cgroup_root) = slots
        .cgroup_root
        .ok_or_else(|| ConfigError::at(0, KEY_CGROUP_ROOT, "required key is missing"))?;
    let (rows_line, max_rows) = slots
        .max_rows
        .ok_or_else(|| ConfigError::at(0, KEY_MAX_ROWS, "required key is missing"))?;

    let levels = configured_levels(levels_line, &levels)?;
    let tick_ms = u32::try_from(tick_ms)
        .ok()
        .filter(|value| (100..=60_000).contains(value))
        .ok_or_else(|| ConfigError::at(tick_line, KEY_TICK_MS, "must be in 100..=60000"))?;
    let path = Path::new(&cgroup_root);
    if !path.is_absolute()
        || path
            .components()
            .any(|component| component == Component::ParentDir)
    {
        return Err(ConfigError::at(
            root_line,
            KEY_CGROUP_ROOT,
            "must be absolute and contain no .. component",
        ));
    }
    let max_rows = u32::try_from(max_rows)
        .ok()
        .filter(|value| (1..=4_096).contains(value))
        .ok_or_else(|| ConfigError::at(rows_line, KEY_MAX_ROWS, "must be in 1..=4096"))?;

    Ok(Config {
        levels,
        tick_ms,
        cgroup_root: PathBuf::from(cgroup_root),
        max_rows,
    })
}

fn configured_levels(line: usize, names: &[String]) -> Result<Vec<Level>, ConfigError> {
    if names.is_empty() {
        return Err(ConfigError::at(
            line,
            KEY_LEVELS,
            "must contain at least one level",
        ));
    }
    let mut seen = HashSet::new();
    let mut levels = Vec::with_capacity(names.len());
    for name in names {
        let level = match Level::from_name(name) {
            Some(Level::Observe) => Level::Observe,
            Some(Level::Operator) => Level::Operator,
            Some(Level::Observer) => Level::Observer,
            Some(Level::Control) | None => {
                return Err(ConfigError::at(
                    line,
                    KEY_LEVELS,
                    format!("{name:?} is not a probe-served level"),
                ));
            }
        };
        if !seen.insert(level) {
            return Err(ConfigError::at(
                line,
                KEY_LEVELS,
                format!("duplicate level {name:?}"),
            ));
        }
        levels.push(level);
    }
    Ok(levels)
}

fn key_name(key: Key) -> &'static str {
    match key {
        Key::Levels => KEY_LEVELS,
        Key::TickMs => KEY_TICK_MS,
        Key::CgroupRoot => KEY_CGROUP_ROOT,
        Key::MaxRows => KEY_MAX_ROWS,
    }
}

fn strip_comment(line: &str) -> &str {
    let mut quoted = false;
    for (offset, byte) in line.bytes().enumerate() {
        match byte {
            b'"' => quoted = !quoted,
            b'#' if !quoted => return &line[..offset],
            _ => {}
        }
    }
    line
}

fn trim_toml(text: &str) -> &str {
    text.trim_matches(|character| matches!(character, ' ' | '\t'))
}

fn is_toml_space(character: char) -> bool {
    matches!(character, ' ' | '\t')
}

fn parse_value(text: &str, line: usize, key: &str) -> Result<Value, ConfigError> {
    if text.starts_with('[') {
        return parse_array(text, line, key).map(Value::Array);
    }
    if text.starts_with('"') {
        return parse_basic_string(text, line, key).map(Value::String);
    }
    if text.bytes().all(|byte| byte.is_ascii_digit()) && !text.is_empty() {
        if text.len() > 1 && text.starts_with('0') {
            return Err(ConfigError::at(
                line,
                key,
                "integers may not have a leading zero",
            ));
        }
        return text
            .parse()
            .map(Value::Integer)
            .map_err(|_| ConfigError::at(line, key, "integer does not fit"));
    }
    Err(ConfigError::at(
        line,
        key,
        "value must be a basic string, an unsigned integer, or a one-line string array",
    ))
}

fn parse_basic_string(text: &str, line: usize, key: &str) -> Result<String, ConfigError> {
    let Some(inner) = text
        .strip_prefix('"')
        .and_then(|rest| rest.strip_suffix('"'))
    else {
        return Err(ConfigError::at(
            line,
            key,
            "unterminated basic string or trailing characters after it",
        ));
    };
    if inner.contains('\\') || inner.contains('"') {
        return Err(ConfigError::at(
            line,
            key,
            "basic string contains a backslash or unescaped quote",
        ));
    }
    if inner.chars().any(char::is_control) {
        return Err(ConfigError::at(
            line,
            key,
            "basic string contains a control character",
        ));
    }
    Ok(inner.to_string())
}

fn parse_array(text: &str, line: usize, key: &str) -> Result<Vec<String>, ConfigError> {
    let Some(inner) = text
        .strip_prefix('[')
        .and_then(|rest| rest.strip_suffix(']'))
    else {
        return Err(ConfigError::at(
            line,
            key,
            "array must be one line and end with ]",
        ));
    };
    let characters: Vec<char> = inner.chars().collect();
    let mut index = 0;
    while index < characters.len() && is_toml_space(characters[index]) {
        index += 1;
    }
    if index == characters.len() {
        return Ok(Vec::new());
    }

    let mut values = Vec::new();
    loop {
        while index < characters.len() && is_toml_space(characters[index]) {
            index += 1;
        }
        if index == characters.len() || characters[index] != '"' {
            return Err(ConfigError::at(
                line,
                key,
                "array elements must be basic strings",
            ));
        }
        let start = index;
        index += 1;
        while index < characters.len() && characters[index] != '"' {
            if characters[index] == '\\' || characters[index].is_control() {
                return Err(ConfigError::at(
                    line,
                    key,
                    "array string contains a backslash or control character",
                ));
            }
            index += 1;
        }
        if index == characters.len() {
            return Err(ConfigError::at(line, key, "array string is unterminated"));
        }
        let quoted: String = characters[start..=index].iter().collect();
        values.push(parse_basic_string(&quoted, line, key)?);
        index += 1;
        while index < characters.len() && is_toml_space(characters[index]) {
            index += 1;
        }
        if index == characters.len() {
            break;
        }
        if characters[index] != ',' {
            return Err(ConfigError::at(
                line,
                key,
                "array elements must be separated by commas",
            ));
        }
        index += 1;
        if index == characters.len() {
            return Err(ConfigError::at(line, key, "array has a trailing comma"));
        }
    }
    Ok(values)
}

/// A one-line listener mapping refusal.
#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct LevelMapError(String);

impl fmt::Display for LevelMapError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// Map each adopted descriptor name to the one configured level of that name.
pub(crate) fn map_levels(
    listeners: Vec<(String, UnixListener)>,
    configured: &[Level],
) -> Result<Vec<(Level, UnixListener)>, LevelMapError> {
    let configured_set: HashSet<Level> = configured.iter().copied().collect();
    if configured_set.len() != configured.len() {
        return Err(LevelMapError(String::from(
            "the configured level list repeats a level",
        )));
    }

    let mut by_level = HashMap::new();
    for (name, listener) in listeners {
        let level = match Level::from_name(&name) {
            Some(level @ (Level::Observe | Level::Operator | Level::Observer)) => level,
            Some(Level::Control) | None => {
                return Err(LevelMapError(format!(
                    "adopted listener name {name:?} is not observe, operator or observer"
                )));
            }
        };
        if !configured_set.contains(&level) {
            return Err(LevelMapError(format!(
                "adopted listener {} is not a configured level",
                level.name()
            )));
        }
        if by_level.insert(level, listener).is_some() {
            return Err(LevelMapError(format!(
                "adopted listener name {} is repeated",
                level.name()
            )));
        }
    }

    let mut mapped = Vec::with_capacity(configured.len());
    for level in configured.iter().copied() {
        let Some(listener) = by_level.remove(&level) else {
            return Err(LevelMapError(format!(
                "configured level {} has no adopted listener",
                level.name()
            )));
        };
        mapped.push((level, listener));
    }
    Ok(mapped)
}

#[cfg(test)]
mod tests {
    use std::os::linux::net::SocketAddrExt;
    use std::os::unix::net::{SocketAddr, UnixListener};
    use std::sync::atomic::{AtomicU32, Ordering};

    use super::{
        decode, map_levels, parse, parse_args, read, Action, Config, Level, LevelMapError, Path,
        ServerLimits, MAX_CONFIG_BYTES,
    };

    const EXAMPLE: &str = r#"[probe]
levels = ["observe", "operator"]   # served levels
tick_ms = 1000
cgroup_root = "/sys/fs/cgroup"     # absolute, no ..

[limits]
max_rows = 1024
"#;

    static NEXT_NAME: AtomicU32 = AtomicU32::new(0);

    fn listener() -> UnixListener {
        let serial = NEXT_NAME.fetch_add(1, Ordering::Relaxed);
        let name = format!("hostobs-config-{}-{serial}", std::process::id());
        let address = SocketAddr::from_abstract_name(name.as_bytes()).expect("abstract socket");
        UnixListener::bind_addr(&address).expect("abstract listener")
    }

    fn one_line(error: impl std::fmt::Display) -> String {
        let text = error.to_string();
        assert!(!text.contains('\n'), "refusal must be one line: {text:?}");
        text
    }

    fn parse_refusal(text: &str, line: &str, key: &str) {
        let error = parse(text).expect_err("configuration must be refused");
        let text = one_line(error);
        assert!(text.contains(line), "missing line {line:?}: {text}");
        assert!(text.contains(key), "missing key {key:?}: {text}");
    }

    #[test]
    fn the_documented_example_parses_and_sets_only_the_two_probe_wide_keys() {
        let config = parse(EXAMPLE).expect("example configuration");
        assert_eq!(
            config,
            Config {
                levels: vec![Level::Observe, Level::Operator],
                tick_ms: 1_000,
                cgroup_root: "/sys/fs/cgroup".into(),
                max_rows: 1_024,
            }
        );
        let limits = config.server_limits();
        let defaults = ServerLimits::new();
        assert_eq!(limits.probe.tick_ms, 1_000);
        assert_eq!(limits.probe.max_rows, 1_024);
        assert_eq!(limits.probe.max_block_bytes, defaults.probe.max_block_bytes);
        assert_eq!(
            limits.probe.client_queue_bytes,
            defaults.probe.client_queue_bytes
        );
        assert_eq!(
            limits.probe.query_queue_bytes,
            defaults.probe.query_queue_bytes
        );
        assert_eq!(limits.observe, defaults.observe);
        assert_eq!(limits.operator, defaults.operator);
        assert_eq!(limits.observer, defaults.observer);
    }

    #[test]
    fn comments_are_outside_strings_and_hashes_inside_strings_survive() {
        let text = r#"[probe]
levels = ["observe#1"] # comment to end
tick_ms = 100
cgroup_root = "/tmp/#not-a-comment"
[limits]
max_rows = 1
"#;
        assert!(
            parse(text).is_err(),
            "observe#1 is still not a served level"
        );

        let text = r#"[probe]
levels = ["observe"] # allowed
tick_ms = 100
cgroup_root = "/tmp/#not-a-comment"
[limits]
max_rows = 1
"#;
        let config = parse(text).expect("hash inside a string is data");
        assert_eq!(config.cgroup_root, Path::new("/tmp/#not-a-comment"));
    }

    #[test]
    fn unknown_sections_keys_and_per_level_probe_wide_forms_are_refused() {
        for (text, key) in [
            (
                "[probe]\nlevels=[\"observe\"]\ntick_ms=100\ncgroup_root=\"/\"\n[limits]\nmax_rows=1\n[limits.observe]\nmax_rows=2\n",
                "limits.observe",
            ),
            (
                "[probe]\nlevels=[\"observe\"]\ntick_ms=100\ncgroup_root=\"/\"\nlimts.max_rows=1\n[limits]\nmax_rows=1\n",
                "limts.max_rows",
            ),
            (
                "[probe]\nlevels=[\"observe\"]\ntick_ms=100\ncgroup_root=\"/\"\n[limits]\nmax_rows={value=1}\n",
                "max_rows",
            ),
        ] {
            parse_refusal(text, "line", key);
        }
    }

    #[test]
    fn keys_outside_their_section_and_duplicates_are_refused() {
        parse_refusal(
            "[probe]\nlevels=[\"observe\"]\ntick_ms=100\ncgroup_root=\"/\"\nmax_rows=1\n[limits]\nmax_rows=1\n",
            "line 5",
            "max_rows",
        );
        parse_refusal(
            "[probe]\nlevels=[\"observe\"]\ntick_ms=100\ncgroup_root=\"/\"\ntick_ms=101\n[limits]\nmax_rows=1\n",
            "line 5",
            "tick_ms",
        );
        parse_refusal(
            "[probe]\nlevels=[\"observe\"]\ntick_ms=100\ncgroup_root=\"/\"\n[probe]\n[limits]\nmax_rows=1\n",
            "line 5",
            "[probe]",
        );
    }

    #[test]
    fn every_required_key_is_checked_when_absent() {
        for (key, text) in [
            (
                "probe.levels",
                "[probe]\ntick_ms=100\ncgroup_root=\"/\"\n[limits]\nmax_rows=1\n",
            ),
            (
                "probe.tick_ms",
                "[probe]\nlevels=[\"observe\"]\ncgroup_root=\"/\"\n[limits]\nmax_rows=1\n",
            ),
            (
                "probe.cgroup_root",
                "[probe]\nlevels=[\"observe\"]\ntick_ms=100\n[limits]\nmax_rows=1\n",
            ),
            (
                "limits.max_rows",
                "[probe]\nlevels=[\"observe\"]\ntick_ms=100\ncgroup_root=\"/\"\n[limits]\n",
            ),
        ] {
            parse_refusal(text, "line 0", key);
        }
    }

    #[test]
    fn levels_are_a_nonempty_duplicate_free_subset_of_the_served_levels() {
        for levels in [
            "[]",
            "[\"control\"]",
            "[\"observe\", \"observe\"]",
            "[\"admin\"]",
            "[\"observe\" \"operator\"]",
            "[\"observe\",]",
        ] {
            let text = format!(
                "[probe]\nlevels={levels}\ntick_ms=100\ncgroup_root=\"/\"\n\
                 [limits]\nmax_rows=1\n"
            );
            parse_refusal(&text, "line 2", "levels");
        }
    }

    #[test]
    fn integer_grammar_and_ranges_are_strict() {
        for tick in ["99", "60001", "+100", "1_000", "0100"] {
            let text = format!(
                "[probe]\nlevels=[\"observe\"]\ntick_ms={tick}\ncgroup_root=\"/\"\n\
                 [limits]\nmax_rows=1\n"
            );
            parse_refusal(&text, "line 3", "tick_ms");
        }
        for rows in ["0", "4097", "-1", "1_024", "01"] {
            let text = format!(
                "[probe]\nlevels=[\"observe\"]\ntick_ms=100\ncgroup_root=\"/\"\n\
                 [limits]\nmax_rows={rows}\n"
            );
            parse_refusal(&text, "line 6", "max_rows");
        }
    }

    #[test]
    fn strings_reject_escapes_controls_and_trailing_text() {
        for root in [
            r#""relative""#,
            r#""""#,
            r#""/tmp/../etc""#,
            r#""/tmp/a\b""#,
            "\"/tmp/control\u{0001}\"",
            r#""/tmp/a" trailing"#,
        ] {
            let text = format!(
                "[probe]\nlevels=[\"observe\"]\ntick_ms=100\ncgroup_root={root}\n\
                 [limits]\nmax_rows=1\n"
            );
            parse_refusal(&text, "line 4", "cgroup_root");
        }
    }

    #[test]
    fn the_byte_bound_and_utf8_are_enforced_before_parsing() {
        let too_large = vec![b'#'; MAX_CONFIG_BYTES + 1];
        assert!(one_line(decode(&too_large).expect_err("oversized file")).contains("65536"));
        let mut at_bound = EXAMPLE.as_bytes().to_vec();
        at_bound.resize(MAX_CONFIG_BYTES, b'#');
        assert!(decode(&at_bound).is_ok(), "a file of comments at the bound");
        assert!(one_line(decode(&[0xff]).expect_err("invalid UTF-8")).contains("UTF-8"));
    }

    #[test]
    fn read_uses_the_same_parser_and_bounds() {
        let error = read(Path::new("/dev/null")).expect_err("an empty file has no required keys");
        one_line(error);
    }

    #[test]
    fn command_line_accepts_only_version_or_one_config() {
        assert_eq!(
            parse_args(&["--version".to_string()]).expect("version"),
            Action::Version
        );
        assert_eq!(
            parse_args(&["--config".to_string(), "/tmp/probe.toml".to_string()]).expect("config"),
            Action::Run {
                config: "/tmp/probe.toml".into()
            }
        );
        for args in [
            vec![],
            vec!["--version", "extra"],
            vec!["--config"],
            vec!["--config", ""],
            vec!["--config", "--version"],
            vec!["--config", "a", "--config", "b"],
            vec!["other"],
        ] {
            let args: Vec<String> = args.into_iter().map(String::from).collect();
            let error = parse_args(&args).expect_err("arguments refused");
            one_line(error);
        }
    }

    #[test]
    fn names_map_to_configured_levels_in_configuration_order() {
        let configured = [Level::Operator, Level::Observe];
        let mapped = map_levels(
            vec![
                ("observe".to_string(), listener()),
                ("operator".to_string(), listener()),
            ],
            &configured,
        )
        .expect("both levels");
        assert_eq!(
            mapped.iter().map(|(level, _)| *level).collect::<Vec<_>>(),
            configured
        );
    }

    #[test]
    fn name_to_level_mapping_refuses_all_four_mismatches() {
        for name in ["control", "hostobs-probe.socket"] {
            assert!(matches!(
                map_levels(vec![(name.to_string(), listener())], &[Level::Observe]),
                Err(LevelMapError(message)) if message.contains(name)
            ));
        }

        assert!(matches!(
            map_levels(
                vec![("operator".to_string(), listener())],
                &[Level::Observe]
            ),
            Err(LevelMapError(message)) if message.contains("operator")
                && message.contains("not a configured")
        ));
        assert!(matches!(
            map_levels(vec![], &[Level::Observe]),
            Err(LevelMapError(message)) if message.contains("observe")
                && message.contains("no adopted listener")
        ));
        assert!(matches!(
            map_levels(
                vec![
                    ("observe".to_string(), listener()),
                    ("observe".to_string(), listener()),
                ],
                &[Level::Observe]
            ),
            Err(LevelMapError(message)) if message.contains("observe")
                && message.contains("repeated")
        ));
    }
}
